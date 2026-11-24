"""F2 (1.5.0): zone 2's flow after the valves - bottleneck, decision, heating curve advice, presetting at max."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from vaillant_eebus.hems.flow import (
    CurveAdvice,
    FlowInputs,
    RoomSamples,
    bottleneck,
    curve_advice,
    decide,
    flow_per_k,
    heat_from_pump,
    hour_valve,
    normal_moments,
    snapshot,
)
from vaillant_eebus.hems.room_model import FlowHint
from vaillant_eebus.room_data import HeatingSurface, RoomDetails, details_from_json, details_to_json
from vaillant_eebus.rooms.service import _recommendation

T0 = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)


def moments(hours: float, start: datetime = T0) -> list[datetime]:
    return [start + STEP * index for index in range(int(hours * 12))]


def steady(times: list[datetime], value: float) -> list[tuple[datetime, float]]:
    return [(moment, value) for moment in times]


def room(name: str, times: list[datetime], valve: float, **changes: object) -> RoomSamples:
    base = RoomSamples(
        room_id=f"sensor.{name.lower()}",
        name=name,
        temperature=steady(times, 21.8),
        valve=steady(times, valve),
        setpoint=steady(times, 22.0),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def inputs(**changes: object) -> FlowInputs:
    base = FlowInputs(
        now=T0 + timedelta(hours=10),
        offset_k=0.0,
        changed_at=T0,
        jumped_at=None,
        step_k=0.5,
        down_k=2.0,
        up_k=2.0,
        min_interval=timedelta(minutes=30),
        heat_pump=True,
        under=[],
        may_raise=None,
        wide_open=[],
        bottleneck="Franz",
        bottleneck_valve=62.0,
        zone1_gap_k=3.0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


# --- the decision ------------------------------------------------------------------------------------


def test_an_undersupplied_room_brings_a_shift_below_zero_back_at_once() -> None:
    # Finding of the acceptance 1.3.0: 4 steps over 2 h back from -2 K, and "Vorlauf höher" next to -1.5 K.
    decision = decide(inputs(offset_k=-1.5, changed_at=T0 + timedelta(hours=9, minutes=50), under=["Franz"]))

    assert (decision.offset_k, decision.hint, decision.changed, decision.jumped) == (0.0, FlowHint.HIGHER, True, True)
    assert decision.reason == "Vorlauf wieder höher: Franz unterversorgt (von \u22121,5 K)"


def test_above_zero_it_rises_a_step_per_interval_up_to_the_band() -> None:
    now = T0 + timedelta(hours=10)
    first = decide(inputs(under=["Franz"], changed_at=now - timedelta(minutes=40)))
    waiting = decide(inputs(offset_k=0.5, under=["Franz"], changed_at=now - timedelta(minutes=10)))
    top = decide(inputs(offset_k=2.0, under=["Franz"], changed_at=now - timedelta(hours=2)))

    assert (first.offset_k, first.reason) == (0.5, "Vorlauf höher: Franz unterversorgt")
    assert not waiting.changed and waiting.offset_k == 0.5
    assert not top.changed and top.reason == "Franz unterversorgt, Band oben erreicht"


def test_first_redistribute_and_never_up_without_heat_from_the_heat_pump() -> None:
    redistribute = decide(inputs(offset_k=-1.0, under=["Franz"], may_raise=(False, "erst umverteilen, dann Vorlauf")))
    no_heat = decide(inputs(offset_k=-1.0, under=["Franz"], heat_pump=False))
    exhausted = decide(
        inputs(offset_k=-1.0, under=["Franz"], may_raise=(True, "Vorlauf höher: Umverteilen ausgeschöpft (Franz)"))
    )

    assert not redistribute.changed and redistribute.reason == "erst umverteilen, dann Vorlauf"
    assert not no_heat.changed
    assert exhausted.offset_k == 0.0 and exhausted.reason == "Vorlauf höher: Umverteilen ausgeschöpft (Franz)"


def test_down_slowly_when_the_bottleneck_draws_heat_and_has_room() -> None:
    decision = decide(inputs(offset_k=-0.5, changed_at=T0 + timedelta(hours=7)))

    assert (decision.offset_k, decision.hint) == (-1.0, FlowHint.LOWER)
    assert decision.reason == "Vorlauf tiefer: Engpass Franz bei 62 %"


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"changed_at": T0 + timedelta(hours=8, minutes=30)}, "tiefer höchstens alle 2 h"),
        ({"jumped_at": T0 + timedelta(hours=5)}, "nach dem Sprung zurück 6 h nicht tiefer"),
        ({"bottleneck_valve": 78.0}, "Engpass Franz bei 78 % (Ziel 70 bis 90 %)"),
        # Valves shut at night or by guests' warmth say nothing about the flow (W1, acceptance 1.3.0).
        ({"bottleneck_valve": 4.0}, "Engpass Franz zieht kaum Wärme (4 %)"),
        ({"bottleneck_valve": None}, "Engpassraum unbekannt oder gerade nicht aussagekräftig"),
        ({"wide_open": ["Büro"]}, "Ventil weit offen: Büro"),
        ({"heat_pump": False}, "die Wärmepumpe liefert gerade keine Wärme"),
        ({"zone1_gap_k": 0.6}, "Zone 1 bestimmt dann den Vorlauf (Vorlauf-Soll Zone 2 nicht mehr 1 K darüber)"),
        ({"zone1_gap_k": None, "zone1_unknown": True}, "Vorlauf-Soll der Zone 1 unbekannt"),
        ({"floor_room_cold": True}, "die Zone mit Fußbodenheizung ist unter ihrem Komfortband"),
        ({"offset_k": -2.0}, "Band unten erreicht"),
        ({"hold": "Aufwärmen nach der Nacht"}, "Aufwärmen nach der Nacht"),
    ],
)
def test_what_keeps_it_from_going_down(changes: dict[str, object], why: str) -> None:
    decision = decide(inputs(**changes))

    assert not decision.changed
    assert decision.reason == why


def test_heat_from_the_heat_pump_needs_power_and_warm_water() -> None:
    assert heat_from_pump(900.0, 33.4, 21.5)
    assert not heat_from_pump(30.0, 33.4, 21.5)
    assert not heat_from_pump(900.0, 27.0, 21.5)
    assert not heat_from_pump(None, 33.4, 21.5)


def test_one_k_of_setpoint_moves_the_flow_by_the_curves_slope_plus_one() -> None:
    assert flow_per_k((40.0, -0.4)) == pytest.approx(1.4)
    assert flow_per_k(None) == 1.4


# --- phases and the bottleneck --------------------------------------------------------------------------


def test_windows_setpoint_changes_heating_up_and_test_runs_are_left_out() -> None:
    times = moments(10)
    window = [
        (moment, 1.0 if T0 + timedelta(hours=1) <= moment < T0 + timedelta(hours=1, minutes=20) else 0.0)
        for moment in times
    ]
    testing = [
        (moment, 1.0 if T0 + timedelta(hours=8) <= moment < T0 + timedelta(hours=9) else 0.0) for moment in times
    ]
    # The schedule raises 21 -> 22 °C at 4:00; the room is there at 5:00.
    setpoint = [(moment, 22.0 if moment >= T0 + timedelta(hours=4) else 21.0) for moment in times]
    temperature = [
        (
            moment,
            21.0
            if moment < T0 + timedelta(hours=4)
            else min(22.0, 21.0 + (moment - T0 - timedelta(hours=4)).total_seconds() / 3600),
        )
        for moment in times
    ]
    sample = room("Franz", times, 50.0, window=window, testing=testing, setpoint=setpoint, temperature=temperature)

    normal = normal_moments(sample, times[-1], T0)

    # Window 1:00-1:20 and 30 min after it.
    assert T0 + timedelta(hours=1, minutes=45) not in normal
    assert T0 + timedelta(hours=1, minutes=55) in normal
    # The raise at 4:00 and heating up until 0.2 K close (4:50).
    assert T0 + timedelta(hours=4, minutes=40) not in normal
    assert T0 + timedelta(hours=4, minutes=50) in normal
    assert T0 + timedelta(hours=8, minutes=30) not in normal
    assert T0 + timedelta(hours=3) in normal


def test_the_bottleneck_is_the_widest_valve_over_days_while_the_heat_pump_delivers() -> None:
    times = moments(48)
    # The heat pump delivers only in the first 30 hours; in the rest Büro's valve is wide open (no heat: not counted).
    power = [(moment, 900.0 if moment < T0 + timedelta(hours=30) else 0.0) for moment in times]
    supply = steady(times, 33.0)
    franz = room("Franz", times, 85.0)
    office = replace(
        room("Büro", times, 60.0),
        valve=[(moment, 60.0 if moment < T0 + timedelta(hours=30) else 100.0) for moment in times],
    )
    bath = room("Bad", times, 95.0, at_thermostat=True, temperature=steady(times, 21.0))

    found = bottleneck([franz, office], power, supply, times[-1])
    with_bath = bottleneck([franz, office, bath], power, supply, times[-1])

    assert found is not None
    assert (found.name, found.mean_valve, found.long_term, found.hours) == ("Franz", 85.0, True, 30.0)
    # The bath (measured at its radiator) counts with its valve, never as undersupplied.
    assert with_bath is not None and with_bath.name == "Bad" and with_bath.under_share == 0.0


def test_too_few_hours_give_the_snapshot() -> None:
    times = moments(10)
    power, supply = steady(times, 900.0), steady(times, 33.0)
    rooms = [room("Franz", times, 85.0), room("Büro", times, 95.0)]

    assert bottleneck(rooms, power, supply, times[-1]) is None
    shot = snapshot(rooms, times[-1])
    assert shot is not None and (shot.name, shot.long_term) == ("Büro", False)


def test_the_hours_valve_counts_only_in_a_normal_phase() -> None:
    times = moments(4)
    calm = room("Franz", times, 62.0)
    window = room("Franz", times, 62.0, window=[(moment, 1.0 if moment >= times[-3] else 0.0) for moment in times])

    assert hour_valve(calm, times[-1]) == 62.0
    assert hour_valve(window, times[-1]) is None


# --- heating curve advice --------------------------------------------------------------------------------


def advice(
    offset: float | Callable[[float], float], *, days: int = 7, curve: list[tuple[datetime, float]] | None = None
) -> CurveAdvice | None:
    times = moments(days * 24, T0 - timedelta(days=days))
    outdoor = [(moment, -4.0 if moment.hour < 12 else 6.0) for moment in times]
    offsets = [
        (moment, offset(out) if callable(offset) else offset) for moment, (_, out) in zip(times, outdoor, strict=True)
    ]
    return curve_advice(
        offsets=offsets,
        power=steady(times, 900.0),
        supply=steady(times, 34.0),
        rooms_c=steady(times, 21.5),
        outdoor=outdoor,
        flow_setpoint=[(moment, 36.5 if out < 0 else 32.0) for moment, out in outdoor],
        zone_setpoint=steady(times, 22.0),
        curve=curve if curve is not None else steady(times, 0.35),
        flow_per_k=1.4,
        label="Zone 2",
        now=T0,
        zone=UTC,
    )


def test_a_constant_shift_advises_the_programs_temperature() -> None:
    result = advice(1.0)

    assert result is not None
    assert result.kind == "parallel"
    assert result.text == "Wunschtemperatur im Zeitprogramm von Zone 2 um 1 K höher"


def test_a_shift_only_in_the_cold_advises_a_steeper_curve() -> None:
    # +2 K at -4 °C, 0 K at +6 °C: the flow is short in the cold, the curve too flat.
    result = advice(lambda out: 2.0 if out < 0 else 0.0)

    assert result is not None
    assert result.kind == "steeper"
    # Flow 36.5 °C at 22 °C setpoint (lift 14.5 K) plus 2 x 1.4 K: 0.35 x 17.3 / 14.5 = 0.42.
    assert result.curve_new == pytest.approx(0.42, abs=0.01)
    assert result.text == "Heizkurve Zone 2 steiler: etwa 0,35 \u2192 0,42"


def test_no_advice_without_enough_days_a_small_shift_or_right_after_a_curve_change() -> None:
    assert advice(0.25) is None
    assert advice(1.0, days=4) is None
    # The curve was changed two days ago: the days before do not count.
    times = moments(7 * 24, T0 - timedelta(days=7))
    changed = [(moment, 0.40 if moment < T0 - timedelta(days=2) else 0.35) for moment in times]
    assert advice(1.0, curve=changed) is None


# --- presetting at its maximum ---------------------------------------------------------------------------


def test_with_the_presetting_at_max_the_hint_names_the_shut_offs() -> None:
    times = moments(4 * 24)
    temperature = steady(times, 21.2)
    valve = steady(times, 100.0)
    setpoint = steady(times, 22.0)

    usual = _recommendation(temperature, valve, setpoint, no_help=False)
    at_max = _recommendation(temperature, valve, setpoint, no_help=False, at_max=True)

    assert usual is not None and "Voreinstellung größer" in usual
    assert at_max is not None and "Absperrungen am Heizkörper" in at_max and "Heizfläche zu klein" in at_max


def test_the_presetting_at_max_is_kept_with_the_room() -> None:
    details = RoomDetails(surfaces=(HeatingSurface(presetting="6", presetting_max=True),))

    again = details_from_json(details_to_json(details))

    assert again.surfaces[0].presetting_max
    assert not details_from_json({"surfaces": [{"presetting": "3"}]}).surfaces[0].presetting_max
