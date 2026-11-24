"""F3 (1.5.0): the night setback - window, off or minimum power, the steps, waiting for a defrost, the way back,
and recognizing the heat pump's minimum power."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from vaillant_eebus.hems.forecast import WeatherHour
from vaillant_eebus.hems.night import (
    MinimumPhase,
    NightMode,
    ReduceInputs,
    RoomNow,
    at_minimum,
    hot_water,
    humid,
    minimum_by_band,
    minimum_phases,
    next_start,
    off_end,
    off_start,
    plan_window,
    reduce_step,
    window_at,
)

LOCAL = timezone(timedelta(hours=1), "CET")
STEP = timedelta(minutes=5)


def local(hour: int, minute: int = 0, day: int = 25) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=LOCAL)


def test_the_window_is_5_30_to_10() -> None:
    bounds = window_at(local(7).astimezone(UTC), time(5, 30), time(10, 0), LOCAL)

    assert bounds == (local(5, 30), local(10))
    assert window_at(local(5, 29), time(5, 30), time(10, 0), LOCAL) is None
    assert window_at(local(10), time(5, 30), time(10, 0), LOCAL) is None
    # A window over midnight.
    assert window_at(local(1), time(22, 0), time(6, 0), LOCAL) == (local(22, day=24), local(6))


def forecast(celsius: float) -> dict[datetime, WeatherHour]:
    hours = [local(4) + timedelta(hours=index) for index in range(8)]
    return {hour.astimezone(UTC): WeatherHour(start=hour.astimezone(UTC), temperature_c=celsius) for hour in hours}


def test_above_3_degrees_off_below_minimum_power() -> None:
    bounds = (local(5, 30), local(10))

    mild = plan_window(bounds, forecast(4.2), frozenset())
    cold = plan_window(bounds, forecast(-1.5), frozenset())
    frosty = plan_window(bounds, forecast(3.5), frozenset({local(7).astimezone(UTC)}))

    assert (mild.mode, mild.mean_c) == (NightMode.OFF, 4.2)
    assert mild.reason() == "Nachtabsenkung: Abschalten (Prognose im Mittel 4,2 °C)"
    assert cold.mode is NightMode.REDUCE
    # Between 3 and 4 °C the frost protection may run the heat pump anyway: minimum power instead of off.
    assert frosty.mode is NightMode.REDUCE and frosty.frost
    # Exactly 3 °C is not above.
    assert plan_window(bounds, forecast(3.0), frozenset()).mode is NightMode.REDUCE


# --- minimum power -----------------------------------------------------------------------------------------


def step_inputs(**changes: object) -> ReduceInputs:
    base = ReduceInputs(
        now=local(6, 30),
        shift_k=-1.0,
        stepped_at=local(6, 5),
        band_k=2.0,
        stopped=False,
        at_minimum=False,
        busy=None,
        zone1_gap_k=3.0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_down_by_half_a_kelvin_every_20_minutes() -> None:
    down = reduce_step(step_inputs())
    early = reduce_step(step_inputs(stepped_at=local(6, 20)))

    assert (down.shift_k, down.changed) == (-1.5, True)
    assert not early.changed and early.note == "nächster Schritt nach 20 min"


def test_at_the_minimum_one_step_back_and_hold() -> None:
    reached = reduce_step(step_inputs(shift_k=-1.5, at_minimum=True))
    later = reduce_step(step_inputs(shift_k=-1.0, held=reached.held, stepped_at=local(5, 0)))

    assert (reached.shift_k, reached.changed) == (-1.0, True)
    assert reached.held is not None and reached.held.startswith("Minimum erreicht bei")
    assert not later.changed


def test_a_stop_brings_one_kelvin_up_at_once() -> None:
    stopped = reduce_step(step_inputs(shift_k=-2.0, stopped=True, stepped_at=local(6, 25)))

    assert (stopped.shift_k, stopped.changed) == (-1.0, True)
    assert stopped.held is not None and "stand" in stopped.held


@pytest.mark.parametrize(
    ("changes", "held", "note"),
    [
        ({"shift_k": -2.0}, "Band ausgeschöpft, Minimum nicht erreicht", None),
        ({"zone1_gap_k": 0.4}, "Zone 1 bestimmt den Vorlauf", None),
        ({"busy": "Abtauung"}, None, "Abtauung"),
        ({"zone1_gap_k": None, "zone1_unknown": True}, None, "Vorlauf-Soll der Zone 1 unbekannt"),
    ],
)
def test_what_keeps_it_from_going_lower(changes: dict[str, object], held: str | None, note: str | None) -> None:
    step = reduce_step(step_inputs(**changes))

    assert not step.changed
    assert (step.held, step.note) == (held, note)


def test_the_next_night_starts_where_this_one_held() -> None:
    assert next_start(-1.0, None, 2.0, 1.0) == 1.0
    # Stopped at -2 K: next time 0.5 K above.
    assert next_start(None, -2.0, 2.0, 1.0) == 1.5
    assert next_start(None, None, 2.0, 1.5) == 1.5
    assert next_start(-2.5, None, 2.0, 1.0) == 2.0


def warm_day_run(start: datetime) -> tuple[list[tuple[datetime, float]], ...]:
    """A run on a mild day (field data 13.-23.11.): 540 W, flow setpoint 31 °C, the buffer from 27.2 °C up to
    33 °C (2 K above), then the heat pump stops."""
    times = [start + STEP * index for index in range(10)]
    power = [(moment, 540.0) for moment in times] + [(times[-1] + STEP, 0.0)]
    buffer = [(moment, 27.2 + 0.65 * index) for index, moment in enumerate(times)]
    target = [(moment, 31.0) for moment in times]
    outdoor = [(moment, 9.0) for moment in times]
    return power, buffer, target, outdoor


def test_the_buffer_above_a_steady_setpoint_is_the_minimum() -> None:
    # The user's sign (24.11.2026): the buffer rises although the setpoint stays - the heat pump at its minimum
    # overheats to reach its minimum run time.
    power, buffer, target, outdoor = warm_day_run(local(12))

    phases = minimum_phases(power=power, buffer=buffer, target=target, outdoor=outdoor, dhw=[], defrosts=[])

    # 27.2 + 0.65 * 7 = 31.75 >= 31 + 0.5 from the 8th sample on (and 10 min running, 20 min steady setpoint).
    assert [phase.at for phase in phases] == [local(12, 35), local(12, 40), local(12, 45)]
    assert {phase.power_w for phase in phases} == {540.0}


def test_rising_below_the_setpoint_is_no_minimum() -> None:
    # After a start, a defrost or a hot water charge the buffer rises too - with high power and below the setpoint.
    power, buffer, target, outdoor = warm_day_run(local(12))
    low = [(moment, value - 3.0) for moment, value in buffer]
    times = [moment for moment, _ in target]
    moving = [(moment, 31.0 + 0.2 * index) for index, moment in enumerate(times)]

    assert minimum_phases(power=power, buffer=low, target=target, outdoor=outdoor, dhw=[], defrosts=[]) == []
    assert minimum_phases(power=power, buffer=buffer, target=moving, outdoor=outdoor, dhw=[], defrosts=[]) == []
    defrost = minimum_phases(
        power=power, buffer=buffer, target=target, outdoor=outdoor, dhw=[], defrosts=[local(12, 30)]
    )
    assert [phase.at for phase in defrost] == []


def test_a_hot_water_charge_is_left_out() -> None:
    power, buffer, target, outdoor = warm_day_run(local(12))
    dhw = [(local(12, 20), 48.0), (local(12, 25), 48.8), (local(12, 30), 50.0)]

    assert hot_water(dhw, local(12, 35))
    assert not hot_water(dhw, local(13, 30))
    assert minimum_phases(power=power, buffer=buffer, target=target, outdoor=outdoor, dhw=dhw, defrosts=[]) == []


def test_the_minimum_per_outdoor_band_needs_three_phases() -> None:
    phases = [MinimumPhase(local(12, index), watts, 8.0) for index, watts in enumerate((530.0, 545.0, 560.0))]
    phases.append(MinimumPhase(local(13), 610.0, 1.0))

    bands = minimum_by_band(phases)

    assert [(band.band, band.power_w, band.phases) for band in bands] == [
        ("unter 0 °C", None, 0),
        ("0 bis 3 °C", None, 1),
        ("3 bis 7 °C", None, 0),
        ("ab 7 °C", 545.0, 3),
    ]


def test_at_minimum_needs_the_pump_running() -> None:
    power, buffer, target, _outdoor = warm_day_run(local(12))
    standing = {moment: 0.0 for moment, _ in power}

    assert at_minimum(dict(power), dict(buffer), dict(target), [], [], local(12, 40))
    assert not at_minimum(standing, dict(buffer), dict(target), [], [], local(12, 40))


# --- switching off ------------------------------------------------------------------------------------------


def test_off_at_once_without_icing_else_right_after_a_defrost() -> None:
    clear = off_start(local(5, 30), icing=False, last_defrost_end=None, zone=LOCAL)
    fresh = off_start(local(5, 30), icing=True, last_defrost_end=local(5, 20), zone=LOCAL)
    waiting = off_start(local(5, 30), icing=True, last_defrost_end=local(4, 40), zone=LOCAL)
    late = off_start(local(7), icing=True, last_defrost_end=None, zone=LOCAL)

    assert clear.off and fresh.off
    assert not waiting.off and waiting.waiting == "Aus direkt nach der nächsten Abtauung (spätestens 07:00 Uhr)"
    # No defrost until 7:00: minimum power instead.
    assert late.give_up and not late.off


def test_back_on_at_the_allowed_loss_humidity_or_frost() -> None:
    # Allowed loss 0.5 K, back 0.1 K before it: at 21.6 °C for a setpoint of 22 °C.
    warm = RoomNow("Franz", 21.7, 22.0, 0.5, 45.0)
    cold = replace(warm, temperature_c=21.6)
    wet = replace(warm, humidity=66.0)
    sabine = RoomNow("Sabine", 21.15, 22.0, 1.0, 40.0)

    assert off_end([warm], frost_active=False) is None
    assert off_end([cold], frost_active=False) == "Franz hat den erlaubten Verlust erreicht (21,6 °C)"
    assert off_end([wet], frost_active=False) == "Feuchteschutz: Franz 66 %"
    assert off_end([warm], frost_active=True) == "Frostschutz der Anlage beginnt"
    # Sabine may lose 1 K.
    assert off_end([sabine], frost_active=False) is None
    assert humid([warm, wet]) == "Feuchteschutz: Franz"
