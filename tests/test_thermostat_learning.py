"""What the app learns of a thermostat (1.4.2, plan-1.4.2-testlauf-lernt.md): the valve map, everyday points,
step responses, the controller's kind, when it reads a W600, and how the test run chooses its steps. Values
invented unless said otherwise."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from vaillant_eebus.ha_client import position_read_topic
from vaillant_eebus.ids import device_name_problem
from vaillant_eebus.rooms.learning import (
    CalmWatch,
    Drift,
    Kind,
    MapPoint,
    Response,
    Source,
    StepWatch,
    Supply,
    ValveMap,
    map_from_json,
    map_to_json,
    supply_class,
)
from vaillant_eebus.rooms.reads import ReadPlan
from vaillant_eebus.rooms.testrun import TestInputs, TestRun, TestState, advance, run_from_json, run_to_json
from vaillant_eebus.write_gate import ThermostatLimits

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
LIMITS = ThermostatLimits(5.0, 30.0, 0.5)


def point(
    offset: float, valve: float, *, rising: bool = True, days: float = 0, supply: Supply = Supply.MILD
) -> MapPoint:
    return MapPoint(T0 - timedelta(days=days), offset, rising, supply, valve, Source.DAILY)


# --- the map -----------------------------------------------------------------------------------------------


def test_newer_points_weigh_more_and_each_cell_keeps_its_newest() -> None:
    valve_map = ValveMap().add_points([point(1.0, 20.0, days=28), point(1.0, 60.0)])

    # 28 days = two half-lives: weights 1/4 and 1 -> (5 + 60) / 1.25.
    assert valve_map.cells(T0)[0].valve == 52.0
    many = ValveMap().add_points(point(1.0, float(index), days=index) for index in range(15))
    assert len(many.points) == 10 and min(item.valve for item in many.points) == 0.0


def test_closed_and_fully_open_are_known_from_everyday_points() -> None:
    valve_map = ValveMap().add_points([point(-0.5, 0.0, rising=False), point(0.5, 30.0), point(1.5, 70.0)])

    assert valve_map.closed_k(T0) == -0.5
    assert valve_map.full(T0) is None
    assert valve_map.missing(T0) == ["„ganz offen“"]

    valve_map = valve_map.add_points([point(2.5, 96.0)])
    assert valve_map.full(T0) == (2.5, 96.0) and valve_map.ready(T0)


def test_a_valve_that_stops_rising_below_85_percent_is_fully_open_at_its_plateau() -> None:
    # As the Comet: 82 % at +3.5 K, 84 % at +4.5 K.
    valve_map = ValveMap().add_points([point(2.5, 56.0), point(3.5, 82.0), point(4.5, 84.0)])
    assert valve_map.full(T0) == (3.5, 84.0)

    # 30 % does not count, even if 1 K more brings nothing (the thermostat is below its opening point).
    low = ValveMap().add_points([point(0.5, 30.0), point(1.5, 32.0)])
    assert low.full(T0) is None


def test_the_offset_for_an_opening_follows_the_direction_and_how_warm_the_water_is() -> None:
    valve_map = ValveMap().add_points(
        [
            point(1.0, 50.0, supply=Supply.WARM),
            point(2.0, 50.0, supply=Supply.COOL),
            point(1.5, 50.0, rising=False, supply=Supply.MILD),
        ]
    )

    assert valve_map.offset_for(50.0, rising=True, supply=Supply.WARM, now=T0) == 1.0
    assert valve_map.offset_for(50.0, rising=True, supply=Supply.COOL, now=T0) == 2.0
    # No rising point with mild water: the neighbour (cool before warm), then the other direction.
    assert valve_map.offset_for(50.0, rising=True, supply=Supply.MILD, now=T0) == 2.0
    assert valve_map.offset_for(50.0, rising=False, supply=Supply.WARM, now=T0) == 1.5
    assert (
        supply_class(29.9) is Supply.COOL and supply_class(35.0) is Supply.MILD and supply_class(None) is Supply.UNKNOWN
    )


def test_the_kind_of_controller_shows_in_what_the_valve_does() -> None:
    wanders = ValveMap(drifts=tuple(Drift(T0, 1.0, 15.0) for _ in range(3)))
    follows = ValveMap(drifts=tuple(Drift(T0, 1.0, 2.0) for _ in range(3)))
    on_off = ValveMap().add_points(point(offset / 2, 100.0 if offset > 0 else 0.0) for offset in range(-10, 10))

    assert wanders.kind() is Kind.PI and wanders.pi_per_h_per_k() == 15.0
    assert follows.kind() is Kind.P
    assert on_off.kind() is Kind.ON_OFF
    assert ValveMap().kind() is Kind.UNKNOWN


def test_the_smallest_step_ignores_a_valve_at_its_stop() -> None:
    def response(delta: float, before: float, after: float) -> Response:
        return Response(T0, delta, before, after, 1.0, 5.0, 4.0, own=True)

    valve_map = ValveMap(responses=(response(0.5, 100.0, 100.0), response(0.5, 30.0, 32.0), response(1.0, 30.0, 60.0)))

    assert valve_map.smallest_step_k() == 1.0
    assert valve_map.reaction_min() == 1.0 and valve_map.settle_min() == 5.0


def test_the_map_survives_a_restart() -> None:
    valve_map = ValveMap(
        points=(point(1.0, 50.0),),
        responses=(Response(T0, 1.0, 10.0, 60.0, 1.0, 6.0, 8.3, own=False),),
        drifts=(Drift(T0, 1.0, 12.0),),
    )

    assert map_from_json(map_to_json(valve_map)) == valve_map
    assert map_from_json({"points": [["kaputt"]], "responses": [{}], "drifts": [1]}) == ValveMap()


# --- everyday points and step responses -------------------------------------------------------------------


def calm(watch: CalmWatch, minutes: int, *, start: datetime = T0, **values: Any) -> list[Any]:
    found = []
    for minute in range(minutes):
        settings: dict[str, Any] = {
            "setpoint": 22.0,
            "trv_c": 21.0,
            "valve": 40.0,
            "fresh": True,
            "warm": True,
            "supply": Supply.MILD,
        }
        settings.update(values)
        point, drift = watch.update(start + timedelta(minutes=minute), **settings)
        found += [item for item in (point, drift) if item is not None]
    return found


def test_a_calm_half_hour_with_warm_water_is_a_point_of_the_map() -> None:
    watch = CalmWatch()

    found = calm(watch, 31)

    assert len(found) == 1
    assert found[0] == MapPoint(T0 + timedelta(minutes=30), 1.0, True, Supply.MILD, 40.0, Source.DAILY)


@pytest.mark.parametrize(
    "values",
    [{"warm": False}, {"fresh": False}, {"valve": None}, {"trv_c": None}],
    ids=["no warm water", "valve not current", "no valve", "no temperature"],
)
def test_no_point_without_warm_water_or_a_current_valve(values: dict[str, Any]) -> None:
    assert calm(CalmWatch(), 45, **values) == []


def test_a_new_setpoint_starts_the_half_hour_again_and_sets_the_direction() -> None:
    watch = CalmWatch()
    calm(watch, 20)

    found = calm(watch, 31, setpoint=21.5, start=T0 + timedelta(minutes=20))

    assert len(found) == 1 and found[0].offset_k == 0.5 and not found[0].rising
    assert found[0].at == T0 + timedelta(minutes=50)


def test_a_valve_that_keeps_wandering_at_a_calm_offset_gives_drifts() -> None:
    watch = CalmWatch()
    found = []
    for minute in range(70):
        _point, drift = watch.update(
            T0 + timedelta(minutes=minute),
            setpoint=22.0,
            trv_c=21.0,
            valve=20.0 + minute * 0.25,
            fresh=True,
            warm=True,
            supply=Supply.MILD,
        )
        found += [drift] if drift is not None else []

    # 15 points an hour at +1 K (the median of the last 10 minutes lags a little): a PI controller.
    assert len(found) == 1 and found[0].offset_k == 1.0 and 13.0 <= found[0].per_h <= 16.0


def test_a_step_response_shows_reaction_speed_and_settling() -> None:
    watch = StepWatch()
    watch.update(T0, setpoint=21.0, valve=10.0, fresh=True)
    valves = {1: 10.0, 2: 30.0, 3: 50.0, 4: 60.0}
    response = None
    for minute in range(1, 20):
        response = (
            watch.update(
                T0 + timedelta(minutes=minute), setpoint=22.0, valve=valves.get(minute, 62.0), fresh=True, own=True
            )
            or response
        )

    assert response is not None
    assert (response.delta_k, response.valve_before, response.valve_after) == (1.0, 10.0, 62.0)
    # Moved after 1 minute (seen at the 2nd), settled at the 4th, 52 points in 3 minutes.
    assert response.reaction_min == 1.0 and response.settle_min == 3.0 and response.speed == 17.3


def test_a_step_that_does_not_move_the_valve_is_closed_after_an_hour() -> None:
    watch = StepWatch()
    watch.update(T0, setpoint=21.0, valve=0.0, fresh=True)
    responses = [
        watch.update(T0 + timedelta(minutes=minute), setpoint=21.5, valve=0.0, fresh=True) for minute in range(1, 63)
    ]

    found = [item for item in responses if item is not None]
    assert len(found) == 1 and found[0].reaction_min is None and found[0].delta_k == 0.5


# --- reads of a W600 ----------------------------------------------------------------------------------------


def test_a_w600_is_read_every_five_minutes_shortly_before_the_beat() -> None:
    plan = ReadPlan()
    beat = T0 + timedelta(minutes=5)
    due = []
    for minute in range(10):
        moment = T0 + timedelta(minutes=minute, seconds=20)
        if plan.due(moment, next_beat=beat if minute < 5 else beat + timedelta(minutes=5), test_step_at=None):
            plan.done(moment)
            due.append(minute)

    # 2:40 and 1:40 before the beats at 5 and 10 minutes: the first minute at most 2.5 minutes before.
    assert due == [3, 8]


def test_a_w600_is_read_one_and_three_minutes_after_a_new_setpoint() -> None:
    plan = ReadPlan(last=T0)
    plan.after_change(T0)
    far = T0 + timedelta(hours=1)

    due = []
    for minute in range(1, 5):
        if plan.due(T0 + timedelta(minutes=minute), next_beat=far, test_step_at=None):
            plan.done(T0 + timedelta(minutes=minute))
            due.append(minute)

    assert due == [1, 3]
    assert not plan.due(T0 + timedelta(minutes=4), next_beat=far, test_step_at=None)


def test_in_a_test_run_a_w600_is_read_every_minute_then_every_two() -> None:
    plan = ReadPlan()
    due = []
    for minute in range(16):
        moment = T0 + timedelta(minutes=minute)
        if plan.due(moment, next_beat=None, test_step_at=T0):
            plan.done(moment)
            due.append(minute)

    # Ten reads in the first ten minutes, then every two minutes.
    assert due == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 15]


# --- device names and the topic ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("Thermostat Büro", True),
        ("Etage 1/Bad", True),
        ("0x54ef441001881d95", True),
        ("Büro/set", False),
        ("a/GET/b", False),
        ("bridge", False),
        ("Bad #1", False),
        ("Bad+", False),
        (" Bad", False),
        ("Bad//Heizung", False),
        ("", False),
        ("x" * 101, False),
    ],
)
def test_only_safe_device_names_make_a_topic(name: str, ok: bool) -> None:
    assert (device_name_problem(name) is None) is ok
    if ok:
        assert position_read_topic("zigbee2mqtt", name) == f"zigbee2mqtt/{name}/get"
    else:
        with pytest.raises(ValueError):
            position_read_topic("zigbee2mqtt", name)


@pytest.mark.parametrize("base", ["zigbee2mqtt/#", "", "z2m/", "a b", "+/x"])
def test_the_base_topic_has_no_wildcards(base: str) -> None:
    with pytest.raises(ValueError):
        position_read_topic(base, "Bad")


# --- steps of the test run ----------------------------------------------------------------------------------


class Valve:
    """A thermostat whose valve answers a new setpoint at once: rising and falling by the offset."""

    def __init__(self, rising: Any, falling: Any) -> None:
        self.rising_curve, self.falling_curve = rising, falling
        self.rising = True
        self.setpoint = 21.0

    def __call__(self, offset: float) -> float:
        curve = self.rising_curve if self.rising else self.falling_curve
        return float(max(0.0, min(100.0, curve(offset))))

    def set(self, celsius: float) -> None:
        self.rising = celsius > self.setpoint
        self.setpoint = celsius


def night(run: TestRun, valve: Valve, start: datetime, minutes: int, *, wobble: bool = False) -> list[float]:
    sent = []
    for minute in range(minutes):
        moment = start + timedelta(minutes=minute)
        opening = valve(valve.setpoint - 21.5)
        inputs = TestInputs(
            now=moment,
            local=moment,
            setpoint_c=21.0,
            room_c=21.0,
            trv_c=21.5,
            trv_setpoint_c=valve.setpoint,
            valve=opening,
            flow_c=40.0,
            return_c=30.0 + (minute % 2 if wobble else opening / 20),
            window_open=False,
            limits=LIMITS,
            probe_problem=None,
            may_write=True,
        )
        action = advance(run, inputs, time(22, 0), time(1, 0), time(5, 0))
        if action.kind == "send" and action.celsius is not None:
            valve.set(action.celsius)
            sent.append(action.celsius)
        if not run.active or (action.kind == "end" and run.state is TestState.ARMED):
            break
    return sent


def test_a_valve_that_hardly_moves_gets_steps_of_2_then_3_k() -> None:
    # Opens only from +5 K, by 10 % per K after that.
    valve = Valve(lambda offset: (offset - 5.0) * 10, lambda offset: (offset - 5.0) * 10)
    run = TestRun(room_id="sensor.r", trv="climate.r", armed_at=T0)

    sent = night(run, valve, datetime(2026, 11, 24, 21, 59, tzinfo=UTC), 120)

    # Ausgangslage 21.5, +1 K, +2 K, +3 K, +3 K.
    assert sent[:5] == [21.5, 22.5, 24.5, 27.5, 30.0]


def test_a_valve_that_jumps_gets_half_kelvin_steps() -> None:
    valve = Valve(lambda offset: offset * 60.0, lambda offset: offset * 60.0)
    run = TestRun(room_id="sensor.r", trv="climate.r", armed_at=T0)

    sent = night(run, valve, datetime(2026, 11, 24, 21, 59, tzinfo=UTC), 120)

    # +1 K opened it by 60 points: then 0.5 K steps (90 % - fully open).
    assert sent[:3] == [21.5, 22.5, 23.0]


def test_the_way_down_goes_at_most_2_k_below_the_thermostat() -> None:
    # Fully open at +2.5 K, never below 10 % (a thermostat that keeps a little flow).
    valve = Valve(lambda offset: max(10.0, 20.0 + offset * 35), lambda offset: max(10.0, 20.0 + offset * 35))
    run = TestRun(room_id="sensor.r", trv="climate.r", armed_at=T0)

    sent = night(run, valve, datetime(2026, 11, 24, 21, 59, tzinfo=UTC), 7 * 60)

    assert run.state is TestState.DONE and min(sent) == 19.5
    assert run.outcome is not None and not run.outcome.complete and run.reached_full and not run.reached_closed


def test_a_night_cut_after_fully_open_goes_on_downward_only() -> None:
    # Opens from +0.5 K, fully at +2.5 K; on the way down it closes only at -1.5 K; every step takes 40 minutes.
    valve = Valve(lambda offset: (offset - 0.5) * 50, lambda offset: (offset + 1.5) * 25)
    run = TestRun(room_id="sensor.r", trv="climate.r", armed_at=T0)

    night(run, valve, datetime(2026, 11, 25, 0, 59, tzinfo=UTC), 5 * 60, wobble=True)

    assert run.state is TestState.ARMED and run.down_only and run.full_offset_k is not None
    assert run.outcome is not None and not run.outcome.complete and run.outcome.ending == "Ende des Nachtfensters"
    assert run.message.startswith("Geht in der nächsten Nacht weiter, nur abwärts")
    full = run.full_offset_k
    assert run_from_json(run_to_json(run)) is not None and run_from_json(run_to_json(run)).full_offset_k == full  # type: ignore[union-attr]

    # The next night: first to "fully open", then only down until closed.
    valve.setpoint = 21.0
    sent = night(run, valve, datetime(2026, 11, 25, 21, 59, tzinfo=UTC), 7 * 60)

    assert sent[0] == 21.5 + full
    assert all(later < earlier for earlier, later in itertools.pairwise(sent))
    assert run.outcome is not None and run.outcome.complete
    assert run.state is TestState.DONE
