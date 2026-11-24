"""The control learns along (1.4.3, plan-1.4.3.md): its quality per room and day (R1), the demand by the outside
temperature (R2), the gain per room (R3), the sweep (A) and the test run's positions after it. Pure functions;
values invented."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from vaillant_eebus.room_data import RoomDetails
from vaillant_eebus.rooms.controller import Correction, RoomInputs, decide
from vaillant_eebus.rooms.learning import MapPoint, Source, Supply, ValveMap
from vaillant_eebus.rooms.quality import (
    QualityDay,
    QualitySample,
    QualityWatch,
    summary,
    watch_from_json,
    watch_to_json,
)
from vaillant_eebus.rooms.regulation import (
    STAGES,
    CalmHour,
    DemandCurve,
    DemandFollow,
    Gain,
    calm_hours,
    curve_from_json,
    curve_to_json,
    daily_gain,
    demand_changes,
    demand_from_history,
    gain_from_json,
    gain_to_json,
    outdoor_mean,
    recent_medians,
    start_gain,
)
from vaillant_eebus.rooms.sweep import (
    Sweep,
    SweepInputs,
    SweepState,
    abort,
    advance,
    begin,
    describe,
    fresh,
    result_from_json,
    result_to_json,
    start_problem,
)
from vaillant_eebus.rooms.testrun import Phase, TestInputs, TestRun, TestState, after_sweep, hold_positions
from vaillant_eebus.rooms.testrun import advance as run_step
from vaillant_eebus.write_gate import SWEEP_COMMANDS_MAX, ThermostatLimits

T0 = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)
DAY = T0.date()
LIMITS = ThermostatLimits(5.0, 30.0, 0.5)
STEP = timedelta(minutes=5)


# --- R1: quality ---------------------------------------------------------------------------------------------


def sample(moment: datetime, room: float | None, **changes: Any) -> QualitySample:
    values: dict[str, Any] = {
        "now": moment,
        "day": moment.date(),
        "minutes": 5.0,
        "setpoint_c": 21.0,
        "room_c": room,
        "valve": 40.0,
        "comfort_down_k": 0.5,
        "comfort_up_k": 0.5,
        "excluded": False,
        "at_thermostat": False,
        "control": False,
        "commands": 0,
        "movements": 0,
    }
    values.update(changes)
    return QualitySample(**values)


def feed(watch: QualityWatch, start: datetime, rooms: Sequence[float | None], **changes: Any) -> datetime:
    moment = start
    for room in rooms:
        watch.update(sample(moment, room, **changes))
        moment += STEP
    return moment


def test_the_deviation_is_signed_and_the_time_outside_comfort_counted() -> None:
    watch = QualityWatch()

    # An hour: half at 20.4 (too cold, outside the band), half at 21.2.
    feed(watch, T0, [20.4] * 6 + [21.2] * 6, commands=3, movements=2)

    day = watch.today
    assert day is not None and day.minutes == 60.0
    assert day.mean_k == 0.2  # (0.6 - 0.2) / 2: too cold on average
    assert day.mean_abs_k == 0.4
    assert (day.cold_share, day.warm_share) == (50, 0)
    assert (day.commands, day.movements, day.valve_mean, day.stop_share) == (3, 2, 40, 0)


def test_overshoots_and_swings_count_from_0_3_k() -> None:
    watch = QualityWatch()

    # Below, up to 0.6 K over (one overshoot), back below by more than 0.3 K (a swing), over again (another).
    feed(watch, T0, [20.8, 21.2, 21.6, 21.4, 20.6, 21.5, 21.0])

    day = watch.today
    assert day is not None
    assert (day.overshoots, day.overshoot_max_k, day.swings) == (2, 0.6, 2)


def test_heating_up_after_a_rise_of_the_setpoint_counts_apart() -> None:
    watch = QualityWatch()
    moment = feed(watch, T0, [19.0, 19.0], setpoint_c=19.0)

    # The schedule goes to 21 °C: 30 minutes until the room is within 0.2 K; those minutes are no deviation.
    moment = feed(watch, moment, [19.2, 19.6, 20.0, 20.3, 20.5, 20.7, 20.8, 20.9, 21.0], setpoint_c=21.0)

    day = watch.today
    assert day is not None
    assert day.heatups == [30.0]
    # The two samples before and the three from the one that got there (20.8, 20.9, 21.0) count.
    assert day.minutes == 25.0
    assert day.overshoots == 0


def test_a_heat_up_that_takes_over_3_hours_is_missed() -> None:
    watch = QualityWatch()
    moment = feed(watch, T0, [19.0], setpoint_c=19.0)

    feed(watch, moment, [19.1] * 40, setpoint_c=21.0)

    day = watch.today
    assert day is not None and day.heatups == [] and day.heatups_missed == 1


def test_window_test_run_and_missing_sensor_are_not_counted() -> None:
    watch = QualityWatch()

    moment = feed(watch, T0, [19.0] * 3, excluded=True)
    moment = feed(watch, moment, [None] * 3)
    feed(watch, moment, [21.0] * 2)

    day = watch.today
    assert day is not None
    assert day.minutes == 10.0 and day.mean_k == 0.0
    # The valve counts while the room sensor is missing, not while excluded.
    assert day.valve_minutes == 25.0


def test_the_bathroom_has_only_commands_and_valve() -> None:
    watch = QualityWatch()

    feed(watch, T0, [23.0] * 4, at_thermostat=True, valve=95.0, commands=1)

    day = watch.today
    assert day is not None and day.minutes == 0.0 and day.stop_share == 100
    assert "am Thermostat gemessen" in summary(day, "Bad", preview=False)


def test_a_new_day_closes_the_old_one_and_30_days_are_kept() -> None:
    watch = QualityWatch()
    for offset in range(35):
        moment = T0 + timedelta(days=offset)
        watch.update(sample(moment, 21.4, control=True))

    assert len(watch.days) == 30
    assert watch.days[-1].day == (T0 + timedelta(days=33)).date()
    assert [day.day for day in watch.shown()][:2] == [
        (T0 + timedelta(days=34)).date(),
        (T0 + timedelta(days=33)).date(),
    ]
    assert len(watch.shown()) == 8
    again = watch_from_json(watch_to_json(watch))
    assert again.days == watch.days and again.today == watch.today


def test_the_daily_line_names_the_deviation_and_what_was_sent() -> None:
    day = QualityDay(date(2026, 11, 24), minutes=600.0, signed_sum=120.0, abs_sum=180.0, cold_minutes=60.0)
    day.overshoots, day.overshoot_max_k, day.swings = 1, 0.6, 0
    day.heatups, day.commands, day.movements = [40.0, 50.0], 5, 7
    day.valve_minutes, day.valve_sum = 600.0, 21000.0

    line = summary(day, "Büro", preview=True)

    assert line == (
        "Regelgüte Büro am 24.11.: im Mittel 0,2 K zu kalt (Betrag 0,3 K), 10 % unter, 0 % über dem Komfortband, "
        "1 Überschwinger bis 0,6 K über Soll, 0\u00d7 hin und her, Aufheizen 2\u00d7 (Median 45 min), "
        "5 Befehle (Vorschau: würde senden), 7 Ventilbewegungen, Ventil im Mittel 35 % (0 % am Anschlag)"
    )


def test_a_long_stretch_that_hardly_falls_is_sluggish() -> None:
    watch = QualityWatch()

    # 0.5 K too cold for 2.5 h, falling by 0.05 K/h; the valve is not at its stop.
    rooms = [20.5 + 0.05 * index / 12 for index in range(31)]
    feed(watch, T0, rooms, valve=60.0)

    day = watch.today
    assert day is not None and day.sluggish_min == 150.0

    # The same with the valve wide open is not a matter of the gain.
    stuck = QualityWatch()
    feed(stuck, T0, rooms, valve=95.0)
    assert stuck.today is not None and stuck.today.sluggish_min == 0.0


# --- R2: demand ---------------------------------------------------------------------------------------------


def hours_at(outdoor: float, valve: float, count: int, *, days: float = 1.0) -> list[CalmHour]:
    return [CalmHour(T0 - timedelta(days=days, hours=index), valve, outdoor) for index in range(count)]


def test_the_stages_start_from_calm_hours_and_empty_ones_from_the_line() -> None:
    hours = hours_at(0.0, 40.0, 6) + hours_at(10.0, 20.0, 5) + hours_at(-5.0, 99.0, 4)

    curve = demand_from_history(hours, T0, fallback=33.0)

    assert curve.basis == "Verlauf"
    assert [stage.value for stage in curve.stages] == [60.0, 50.0, 40.0, 30.0, 20.0, 10.0]
    assert [stage.learned for stage in curve.stages] == [False, False, True, False, True, False]
    # The 4 hours at -5 °C are too few: that stage is on the line.
    assert curve.stages[1].hours == 4


def test_newer_calm_hours_weigh_more() -> None:
    hours = hours_at(0.0, 20.0, 5, days=28) + hours_at(0.0, 60.0, 5, days=0)

    curve = demand_from_history(hours, T0, fallback=None)

    # 28 days are two half-lives: (0.25 * 20 + 60) / 1.25.
    assert curve.stages[2].value == 52.0


def test_without_history_the_room_model_or_30_percent() -> None:
    assert demand_from_history([], T0, fallback=37.0).values() == [37.0] * 6
    assert demand_from_history([], T0, fallback=37.0).basis == "Raummodell"
    assert demand_from_history([], T0, fallback=None).values() == [30.0] * 6


def flat(value: float = 30.0) -> DemandCurve:
    return demand_from_history([], T0, fallback=value)


def test_the_demand_is_interpolated_between_stages() -> None:
    curve = demand_from_history(hours_at(0.0, 40.0, 5) + hours_at(5.0, 30.0, 5), T0, fallback=None)

    assert curve.value(2.5) == 35.0
    assert curve.value(-20.0) == curve.stages[0].value
    assert curve.value(20.0) == curve.stages[-1].value


def test_following_shares_two_percent_by_the_weights_and_the_day_moves_20_percent() -> None:
    curve = flat().follow(1.0, +1)

    # 1 °C: 80 % of stage 0 °C, 20 % of stage 5 °C.
    assert curve.values() == [30.0, 30.0, 31.6, 30.4, 30.0, 30.0]
    assert curve.stages[2].tracked and curve.stages[2].learned

    toward = curve.toward({2: 41.6})
    assert toward.stages[2].value == 33.6
    assert demand_changes([30.0] * 6, toward) == ["bei 0 °C 30 → 34 %"]
    assert curve_from_json(curve_to_json(toward)) == toward


def test_the_demand_stays_within_0_and_90_percent() -> None:
    curve = flat(89.5)
    for _ in range(5):
        curve = curve.follow(-10.0, +1)
    assert curve.stages[0].value == 90.0
    assert flat(1.0).follow(15.0, -1).stages[-1].value == 0.0


def test_following_needs_half_an_hour_off_the_same_way() -> None:
    follow = DemandFollow()

    results = [follow.update(T0 + timedelta(minutes=minute), 0.3, blocked=False) for minute in range(0, 65, 5)]

    assert results == [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1]
    assert follow.update(T0 + timedelta(minutes=70), -0.3, blocked=False) == 0
    assert follow.update(T0 + timedelta(minutes=75), 0.1, blocked=False) == 0 and follow.since is None
    assert follow.update(T0 + timedelta(minutes=80), 0.5, blocked=True) == 0 and follow.since is None


def test_calm_hours_are_whole_hours_near_the_setpoint() -> None:
    times = [T0 + timedelta(minutes=5 * index) for index in range(36)]
    temperature = [(moment, 21.0 if index < 26 else 22.0) for index, moment in enumerate(times)]
    setpoint = [(moment, 21.0) for moment in times]
    valve = [(moment, 35.0) for moment in times]
    outdoor = [(moment, 2.0) for moment in times]

    hours = calm_hours(temperature, setpoint, valve, outdoor)

    # 26 calm samples (2 h 5 min): two whole hours.
    assert [(hour.valve, hour.outdoor_c) for hour in hours] == [(35.0, 2.0), (35.0, 2.0)]
    assert recent_medians(hours * 3, T0 + timedelta(hours=3)) == {2: 35.0}
    assert outdoor_mean(outdoor, times[-1]) == 2.0


# --- R3: gain --------------------------------------------------------------------------------------------------


def controlled_day(**changes: Any) -> QualityDay:
    day = QualityDay(DAY, minutes=900.0, control_minutes=900.0)
    for name, value in changes.items():
        setattr(day, name, value)
    return day


def test_the_gain_starts_from_the_room_model_within_20_and_40() -> None:
    # Fully open the room makes up 0.03 * 15 = 0.45 K/h; 0.5 K/h needs 111 % -> 40.
    assert start_gain(0.03, 36.0, 21.0).value == 40.0
    # 0.15 * 15 = 2.25 K/h -> 22 %/K.
    assert start_gain(0.15, 36.0, 21.0).value == 22.2
    assert start_gain(1.0, 36.0, 21.0).value == 20.0
    assert start_gain(None, 36.0, 21.0) == Gain(30.0, 30.0, "Startwert (ohne gelernte Aufheizrate)")


@pytest.mark.parametrize(
    ("changes", "value", "why"),
    [
        ({"overshoots": 2, "overshoot_max_k": 0.7}, 24.0, "zu nervös: 2 Überschwinger bis 0,7 K"),
        ({"swings": 3}, 24.0, "zu nervös: 3\u00d7 hin und her"),
        ({"sluggish_min": 150.0}, 36.0, "zu träge: 2,5 h lang mindestens 0,3 K daneben"),
        ({"swings": 2, "sluggish_min": 150.0}, 30.0, None),
        ({"overshoots": 1, "overshoot_max_k": 0.4}, 30.0, None),
        ({"swings": 3, "control_minutes": 600.0}, 30.0, None),
    ],
)
def test_the_gain_changes_once_a_day_after_12_hours_in_control(
    changes: dict[str, Any], value: float, why: str | None
) -> None:
    gain = Gain(30.0, 30.0, "Startwert")

    after, reason = daily_gain(gain, controlled_day(**changes), T0)

    assert (after.value, reason) == (value, why)
    if why is not None:
        assert after.changed_at == T0 and after.reason == why


def test_the_gain_stays_within_10_and_60_and_the_trend_is_a_third() -> None:
    low, _why = daily_gain(Gain(11.0, 30.0, ""), controlled_day(swings=4), T0)
    high, _why = daily_gain(Gain(55.0, 30.0, ""), controlled_day(sluggish_min=200.0), T0)

    assert (low.value, high.value) == (10.0, 60.0)
    assert Gain(30.0, 30.0, "").trend == 10.0
    assert gain_from_json(gain_to_json(high)) == high


def test_the_cascade_uses_the_rooms_gain_and_its_trend_share() -> None:
    valve_map = ValveMap().add_points(
        [
            MapPoint(T0, -0.5, False, Supply.MILD, 0.0, Source.TEST),
            MapPoint(T0, 0.5, True, Supply.MILD, 30.0, Source.TEST),
            MapPoint(T0, 1.0, True, Supply.MILD, 50.0, Source.TEST),
            MapPoint(T0, 1.5, True, Supply.MILD, 70.0, Source.TEST),
            MapPoint(T0, 2.0, True, Supply.MILD, 90.0, Source.TEST),
        ]
    )

    def wanted(gain: float | None, trend: float) -> str:
        inputs = RoomInputs(
            now=T0,
            local=T0.replace(hour=12),
            details=RoomDetails(trv_entity="climate.x", setpoint_c=21.0, deadband_k=0.0),
            at_thermostat=False,
            limits=LIMITS,
            room_c=20.5,
            trend_k_per_h=trend,
            valve=30.0,
            trv_setpoint_c=21.0,
            trv_c=21.0,
            window_open=False,
            window_closed_at=None,
            heat=True,
            valve_map=valve_map,
            supply=Supply.MILD,
            demand_valve=30.0,
            gain_per_k=gain,
        )
        decision, _correction = decide(inputs, Correction())
        return decision.note.split(" (")[0]

    # 30 + 40 * 0.5 = 50 %; with 0.6 K/h rising 40 / 3 * 0.6 = 8 less.
    assert wanted(40.0, 0.0) == "braucht etwa 50 % Ventil"
    assert wanted(40.0, 0.6) == "braucht etwa 42 % Ventil"
    assert wanted(None, 0.0) == "braucht etwa 45 % Ventil"


# --- A: the sweep ------------------------------------------------------------------------------------------


class Valve:
    """A thermostat that opens 40 % per K above -0.5 K: fully open at +2 K; it answers the next minute."""

    def __init__(self) -> None:
        self.setpoint = 21.0
        self.trv_c = 21.5

    def position(self) -> float:
        return max(0.0, min(100.0, (self.setpoint - self.trv_c + 0.5) * 40))


def sweep_inputs(moment: datetime, valve: Valve, *, valve_at: datetime | None, **changes: Any) -> SweepInputs:
    values: dict[str, Any] = {
        "now": moment,
        "setpoint_c": 21.0,
        "room_c": 21.0,
        "trv_c": valve.trv_c,
        "trv_setpoint_c": valve.setpoint,
        "valve": valve.position(),
        "valve_at": valve_at,
        "window_open": False,
        "limits": LIMITS,
        "may_write": True,
        "sending": False,
        "supply": Supply.MILD,
    }
    values.update(changes)
    return SweepInputs(**values)


def run_sweep(
    valve: Valve, minutes: int = 60, *, change: Callable[[int], dict[str, Any]] | None = None
) -> tuple[Sweep, list[float], float | None]:
    sweep = Sweep(room_id="sensor.buero", trv="climate.buero", started_at=T0)
    first = begin(sweep, sweep_inputs(T0, valve, valve_at=None))
    sent = []
    if first.kind == "send" and first.celsius is not None:
        valve.setpoint = first.celsius
        sent.append(first.celsius)
    restore = None
    for minute in range(1, minutes):
        moment = T0 + timedelta(minutes=minute)
        assert sweep.step_at is not None
        # The answer to the read a minute after the step.
        answered = sweep.step_at + timedelta(minutes=1, seconds=10)
        extra = change(minute) if change is not None else {}
        action = advance(sweep, sweep_inputs(moment, valve, valve_at=answered if answered <= moment else None, **extra))
        if action.kind == "send" and action.celsius is not None:
            valve.setpoint = action.celsius
            sent.append(action.celsius)
        if action.kind == "end":
            restore = action.celsius
            break
    return sweep, sent, restore


def test_a_sweep_goes_up_until_fully_open_and_down_until_closed_every_two_minutes() -> None:
    valve = Valve()

    sweep, sent, restore = run_sweep(valve)

    assert sent == [21.5, 22.0, 22.5, 23.0, 23.5, 23.0, 22.5, 22.0, 21.5, 21.0]
    assert sweep.state is SweepState.DONE and sweep.message == "Fertig"
    assert sweep.reached_full and sweep.reached_closed and restore == 21.0
    assert sweep.ended_at == T0 + timedelta(minutes=20)
    assert [(point.offset_k, point.valve, point.rising) for point in sweep.points][-2:] == [
        (0.0, 20.0, False),
        (-0.5, 0.0, False),
    ]
    assert len(sweep.new_points) == 10 and all(point.source is Source.SWEEP for point in sweep.new_points)
    result = sweep.result()
    assert result is not None and result.complete and result_from_json(result_to_json(result)) == result
    assert describe(result) == (
        "10 Punkte, offen ab +0,0 K, ganz offen ab +2,0 K, zu ab \u22120,5 K, Reaktion nach etwa 1,2 min"
    )
    assert fresh([result], T0 + timedelta(days=6)) and not fresh([result], T0 + timedelta(days=8))


def test_a_weak_step_makes_the_next_one_1_k() -> None:
    valve = Valve()
    # Opens only 5 % per K: every step after the first is 1 K.
    valve.position = lambda: max(0.0, min(100.0, (valve.setpoint - valve.trv_c + 0.5) * 5))  # type: ignore[method-assign]

    _sweep, sent, _restore = run_sweep(valve, minutes=8)

    assert sent[:4] == [21.5, 22.0, 23.0, 24.0]


def test_a_sweep_stops_at_6_k_and_at_its_budget() -> None:
    valve = Valve()
    # A valve that never opens fully: up to +6 K, then down.
    valve.position = lambda: 40.0 if valve.setpoint > 21.5 else 0.0  # type: ignore[method-assign]

    sweep, sent, _restore = run_sweep(valve, minutes=200)

    assert max(sent) == 27.5 and not sweep.reached_full
    assert sweep.commands <= SWEEP_COMMANDS_MAX


def test_without_a_fresh_valve_position_the_step_waits_up_to_4_minutes() -> None:
    valve = Valve()
    sweep = Sweep(room_id="sensor.buero", trv="climate.buero", started_at=T0)
    begin(sweep, sweep_inputs(T0, valve, valve_at=None))
    valve.setpoint = 21.5

    waits = [advance(sweep, sweep_inputs(T0 + timedelta(minutes=minute), valve, valve_at=None)) for minute in (2, 3)]
    ended = advance(sweep, sweep_inputs(T0 + timedelta(minutes=4), valve, valve_at=None))

    assert [action.kind for action in waits] == ["wait", "wait"]
    assert ended.kind == "end" and ended.celsius == 21.0 and sweep.message == "Ventilstellung fehlt"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"window_open": True}, "Fenster offen"),
        ({"room_c": 22.1}, "Raum über Soll + 1 K"),
        ({"may_write": False}, "Das Thermostat ist nicht erreichbar oder darf nicht gestellt werden"),
        ({"trv_setpoint_c": 25.0}, "Sollwert am Thermostat von Hand geändert (oder nicht übernommen)"),
    ],
)
def test_a_sweep_stops_and_goes_back(changes: dict[str, Any], reason: str) -> None:
    valve = Valve()

    sweep, _sent, restore = run_sweep(valve, change=lambda minute: changes if minute >= 5 else {})

    assert sweep.state is SweepState.ABORTED and sweep.message == reason and restore == 21.0


def test_a_sweep_does_not_start_with_a_window_or_a_warm_room() -> None:
    valve = Valve()

    assert start_problem(sweep_inputs(T0, valve, valve_at=None, window_open=True)) == "Ein Fenster ist offen."
    assert start_problem(sweep_inputs(T0, valve, valve_at=None, room_c=22.5)) is not None
    assert start_problem(sweep_inputs(T0, valve, valve_at=None)) is None
    sweep = Sweep(room_id="sensor.buero", trv="climate.buero", started_at=T0)
    begin(sweep, sweep_inputs(T0, valve, valve_at=None))
    assert abort(sweep, T0, "vom Nutzer abgebrochen").celsius == 21.0


def test_sweep_points_only_fill_gaps_in_the_map() -> None:
    settled = MapPoint(T0, 1.0, True, Supply.MILD, 30.0, Source.DAILY)
    quick = [
        MapPoint(T0, 1.0, True, Supply.MILD, 60.0, Source.SWEEP),
        MapPoint(T0, 2.0, True, Supply.MILD, 90.0, Source.SWEEP),
    ]

    valve_map = ValveMap().add_points([settled, *quick])

    assert [(cell.offset_k, cell.valve, cell.count) for cell in valve_map.cells(T0)] == [(1.0, 30.0, 1), (2.0, 90.0, 1)]
    assert valve_map.counts()[Source.SWEEP] == 2


# --- the test run after the sweep ----------------------------------------------------------------------------


def night(moment: datetime, run: TestRun, valve: Valve, **changes: Any) -> TestInputs:
    values: dict[str, Any] = {
        "now": moment,
        "local": moment,
        "setpoint_c": 21.0,
        "room_c": 21.0,
        "trv_c": valve.trv_c,
        "trv_setpoint_c": valve.setpoint,
        "valve": valve.position(),
        "flow_c": 40.0,
        "return_c": 32.0,
        "window_open": False,
        "limits": LIMITS,
        "probe_problem": None,
        "may_write": True,
    }
    values.update(changes)
    return TestInputs(**values)


def swept_map() -> ValveMap:
    valve = Valve()
    sweep, _sent, _restore = run_sweep(valve)
    return ValveMap().add_points(sweep.new_points)


def test_the_positions_after_a_sweep_are_closed_30_60_and_fully_open() -> None:
    valve_map = swept_map()

    # Rising: closed up to -0.5 K (one below as margin), 40 % at +0.5, 60 % at +1.0, fully open at +2.0.
    assert hold_positions(valve_map, T0) == (-1.0, 0.5, 1.0, 2.0)
    assert hold_positions(ValveMap(), T0) == ()


def test_a_test_run_begins_with_the_sweep_and_then_holds_the_positions() -> None:
    valve = Valve()
    run = TestRun(room_id="sensor.buero", trv="climate.buero", armed_at=T0 - timedelta(days=1))
    start = datetime(2026, 11, 24, 22, 0, tzinfo=UTC)

    first = run_step(run, night(start, run, valve, sweep_needed=True), time(22, 0), time(1, 0), time(5, 0))
    assert (first.kind, run.phase, run.before_c) == ("sweep", Phase.SWEEP, 21.0)
    # While the sweep runs the run only watches for its own reasons to stop.
    later = start + timedelta(minutes=10)
    assert run_step(run, night(later, run, valve), time(22, 0), time(1, 0), time(5, 0)).kind == "wait"

    after = start + timedelta(minutes=30)
    positions = hold_positions(swept_map(), after)
    action = after_sweep(run, night(after, run, valve, positions=positions))
    assert run.phase is Phase.HOLD and action.kind == "send" and action.celsius == 20.5
    assert run.message == "Stellung 1 von 4 (\u22121,0 K)"

    sent = [action.celsius]
    valve.setpoint = 20.5
    for minute in range(1, 200):
        moment = after + timedelta(minutes=minute)
        action = run_step(run, night(moment, run, valve), time(22, 0), time(1, 0), time(5, 0))
        if action.kind == "send" and action.celsius is not None:
            valve.setpoint = action.celsius
            sent.append(action.celsius)
        if action.kind == "end":
            break

    assert sent == [20.5, 22.0, 22.5, 23.5]
    assert run.state is TestState.DONE and run.reached_closed and run.reached_full
    assert [step.valve for step in run.steps] == [0.0, 40.0, 60.0, 100.0]
    assert run.commands == 4


def test_without_a_known_map_the_run_steps_as_before_after_the_sweep() -> None:
    valve = Valve()
    run = TestRun(room_id="sensor.buero", trv="climate.buero", armed_at=T0 - timedelta(days=1))
    start = datetime(2026, 11, 24, 22, 0, tzinfo=UTC)
    run_step(run, night(start, run, valve, sweep_needed=True), time(22, 0), time(1, 0), time(5, 0))

    action = after_sweep(run, night(start + timedelta(minutes=30), run, valve))

    assert run.phase is Phase.BASELINE and action.kind == "send" and action.celsius == 21.5


def test_the_stages_cover_minus_10_to_15() -> None:
    assert STAGES == (-10.0, -5.0, 0.0, 5.0, 10.0, 15.0)
    assert flat().weights(7.5) == [(3, 0.5), (4, 0.5)]
