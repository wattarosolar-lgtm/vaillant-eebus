"""The night setback (F3, plan-1.5.0-zonen.md "F3 vereinfacht", decided by the user 24.11.2026).

Only in the night window (default 5:30-10:00, the hours of most defrosts and the
worst Arbeitszahl). The mean of the forecast over the window decides:

- above 3 °C (and no frost protection expected in it): switch the zones off
  ("Aus"). With icing possible only right after a defrost, so the heat pump does
  not stand iced; no defrost until 7:00 -> minimum power instead. Back to auto
  at the window's end, earlier when a room reaches its allowed loss (minus
  0.1 K), a room gets humid (65 %) or the frost protection starts.
- else: down to the heat pump's minimum power without it switching off. Zone 2
  (the zone that sets the flow) starts at its learned shift (-1 K) and goes down
  by 0.5 K every 20 min until the buffer rises above the flow setpoint (the heat
  pump cannot modulate lower and overheats to reach its minimum run time), then
  one step back; if it stops anyway, 1 K up. Zone 1 -1 K while the frost
  protection keeps it on (F1 does not switch it then).

Up again at the window's end, without heating ahead. The minimum power itself is
recognized from the samples: phases in which the buffer rises above the flow
setpoint while that stays the same (warm days show them at every run).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum

from vaillant_eebus.hems.forecast import WeatherHour

type Series = Sequence[tuple[datetime, float]]

OFF_ABOVE_C = 3.0
# Minimum power: start, step, rhythm (the flow setpoint and the heat pump need that long to settle).
START_K = 1.0
STEP_K = 0.5
STEP_EVERY = timedelta(minutes=20)
STOP_RAISE_K = 1.0
# The heat pump at its minimum: the buffer rises (by RISING_K in 10 min) and is this far above the flow setpoint.
OVERSHOOT_K = 0.5
RISING_K = 0.2
TARGET_STEADY_K = 0.3
TARGET_STEADY_FOR = timedelta(minutes=20)
RUNNING_FOR = timedelta(minutes=10)
RUNNING_W = 100.0
STOPPED_FOR = timedelta(minutes=2)
DEFROST_AROUND = timedelta(minutes=15)
DHW_RISE_K = 0.5
DHW_AFTER = timedelta(minutes=20)
# Zone 1 lower by this while the frost protection keeps it on.
ZONE1_K = 1.0
ZONE1_MARGIN_K = 1.0
# Switching off: right after a defrost (its end within this), at the latest until WAIT_UNTIL.
DEFROST_FRESH = timedelta(minutes=15)
WAIT_UNTIL = time(7, 0)
# Back on: a room this close to its allowed loss, or this humid.
LOSS_MARGIN_K = 0.1
HUMID_FROM = 65.0
# Outdoor bands of the minimum power (lower bounds; the first is open downwards).
MIN_BANDS = (-100.0, 0.0, 3.0, 7.0)
MIN_BAND_NAMES = ("unter 0 °C", "0 bis 3 °C", "3 bis 7 °C", "ab 7 °C")
MIN_PHASES = 3
MINUS = "\u2212"


class NightMode(StrEnum):
    # Switch the zones off.
    OFF = "off"
    # Down to the minimum power.
    REDUCE = "reduce"


MODE_NAMES = {NightMode.OFF: "Abschalten", NightMode.REDUCE: "Minimalleistung"}


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    mode: NightMode
    mean_c: float | None
    frost: bool

    @property
    def day(self) -> date:
        return self.start.date()

    def reason(self) -> str:
        mean = f"Prognose im Mittel {_number(self.mean_c)} °C" if self.mean_c is not None else "ohne Prognose"
        if self.mode is NightMode.OFF:
            return f"Nachtabsenkung: Abschalten ({mean})"
        frost = ", Frostschutz" if self.frost else ""
        return f"Nachtabsenkung auf Minimalleistung ({mean}{frost})"


def window_at(moment: datetime, start: time, end: time, zone: tzinfo) -> tuple[datetime, datetime] | None:
    """The night window containing moment (local start and end as aware datetimes), or None."""
    local = moment.astimezone(zone)
    for day in (local.date() - timedelta(days=1), local.date()):
        begin = datetime.combine(day, start, tzinfo=zone)
        finish = datetime.combine(day if end > start else day + timedelta(days=1), end, tzinfo=zone)
        if begin <= local < finish:
            return begin, finish
    return None


def plan_window(
    bounds: tuple[datetime, datetime], weather: Mapping[datetime, WeatherHour], frost: frozenset[datetime]
) -> Window:
    """Off or minimum power for the window, from the forecast's mean over it and the frost protection."""
    start, end = bounds
    temps = []
    frosty = False
    for moment, hour in weather.items():
        # Every hour that overlaps the window (5:00-6:00 for a start at 5:30).
        if moment < end and moment + timedelta(hours=1) > start:
            if hour.temperature_c is not None:
                temps.append(hour.temperature_c)
            frosty = frosty or moment in frost
    mean = round(statistics.fmean(temps), 1) if temps else None
    off = mean is not None and mean > OFF_ABOVE_C and not frosty
    return Window(start, end, NightMode.OFF if off else NightMode.REDUCE, mean, frosty)


# --- minimum power -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReduceInputs:
    now: datetime
    shift_k: float
    stepped_at: datetime | None
    band_k: float
    # The heat pump stands (below RUNNING_W for STOPPED_FOR, not in a defrost).
    stopped: bool
    # It runs at its minimum and overheats (buffer above the steady flow setpoint).
    at_minimum: bool
    # A defrost or hot water around now: wait.
    busy: str | None
    # Zone 2's flow setpoint minus zone 1's after one more step; None without zone 1.
    zone1_gap_k: float | None
    zone1_unknown: bool = False
    # Why it holds for the rest of the window (minimum reached, band used up, ...).
    held: str | None = None


@dataclass(frozen=True, slots=True)
class ReduceStep:
    shift_k: float
    changed: bool
    # Set when it holds from now on for the rest of the window.
    held: str | None
    note: str | None


def reduce_step(inputs: ReduceInputs) -> ReduceStep:
    """One look at the minimum power (every 5 min; steps at most every 20 min)."""
    shift = inputs.shift_k
    if inputs.stopped:
        new = min(0.0, round(shift + STOP_RAISE_K, 2))
        return ReduceStep(new, new != shift, f"zu tief bei {_signed(shift)} K: die Wärmepumpe stand", "WP stand")
    if inputs.held is not None:
        return ReduceStep(shift, False, inputs.held, None)
    settled = inputs.stepped_at is None or inputs.now - inputs.stepped_at >= STEP_EVERY
    if inputs.at_minimum and settled:
        new = min(0.0, round(shift + STEP_K, 2))
        return ReduceStep(new, new != shift, f"Minimum erreicht bei {_signed(shift)} K", "Puffer über dem Soll")
    if not settled:
        return ReduceStep(shift, False, None, "nächster Schritt nach 20 min")
    if inputs.busy is not None:
        return ReduceStep(shift, False, None, inputs.busy)
    if shift - STEP_K < -inputs.band_k - 1e-9:
        return ReduceStep(shift, False, "Band ausgeschöpft, Minimum nicht erreicht", None)
    if inputs.zone1_unknown:
        return ReduceStep(shift, False, None, "Vorlauf-Soll der Zone 1 unbekannt")
    if inputs.zone1_gap_k is not None and inputs.zone1_gap_k < ZONE1_MARGIN_K:
        return ReduceStep(shift, False, "Zone 1 bestimmt den Vorlauf", None)
    return ReduceStep(round(shift - STEP_K, 2), True, None, None)


def next_start(held_shift_k: float | None, stopped_at_k: float | None, band_k: float, before: float) -> float:
    """The shift to start with next time (a magnitude): where it held, or 0.5 K above where the pump stopped."""
    if stopped_at_k is not None:
        value = -stopped_at_k - STEP_K
    elif held_shift_k is not None:
        value = -held_shift_k
    else:
        return before
    return max(STEP_K, min(band_k, round(value * 2) / 2))


@dataclass(frozen=True, slots=True)
class MinimumPhase:
    at: datetime
    power_w: float
    outdoor_c: float | None


def minimum_phases(
    *,
    power: Series,
    buffer: Series,
    target: Series,
    outdoor: Series,
    dhw: Series,
    defrosts: Sequence[datetime],
    since: datetime | None = None,
) -> list[MinimumPhase]:
    """Samples in which the heat pump ran at its minimum: the buffer above a steady flow setpoint."""
    watts, top, aim, outside = dict(power), dict(buffer), dict(target), dict(outdoor)
    hot = sorted(dhw)
    times = sorted(watts)
    result = []
    defrost_times = sorted(defrosts)
    for moment in times:
        if since is not None and moment < since:
            continue
        if at_minimum(watts, top, aim, hot, defrost_times, moment):
            recent = [value for at, value in watts.items() if timedelta(0) <= moment - at < timedelta(minutes=10)]
            result.append(MinimumPhase(moment, round(statistics.fmean(recent), 1), outside.get(moment)))
    return result


def at_minimum(
    power: Mapping[datetime, float],
    buffer: Mapping[datetime, float],
    target: Mapping[datetime, float],
    dhw: Series,
    defrosts: Sequence[datetime],
    moment: datetime,
) -> bool:
    """At this sample: running for 10 min, flow setpoint steady 20 min, the buffer rising (0.2 K in 10 min) and
    0.5 K above it, no defrost within 15 min and no hot water within 20 min. A buffer that only stays above the
    setpoint (sensor offset, slow mixing) is no minimum: the user's sign is the rise (24.11.2026)."""
    running = [value for at, value in power.items() if timedelta(0) <= moment - at <= RUNNING_FOR]
    if len(running) < 2 or min(running) < RUNNING_W:
        return False
    aims = [value for at, value in target.items() if timedelta(0) <= moment - at <= TARGET_STEADY_FOR]
    if len(aims) < 3 or max(aims) - min(aims) > TARGET_STEADY_K:
        return False
    now_top = buffer.get(moment)
    before = [value for at, value in buffer.items() if timedelta(minutes=9) <= moment - at <= timedelta(minutes=11)]
    if now_top is None or not before or now_top < aims[-1] + OVERSHOOT_K or now_top < before[0] + RISING_K:
        return False
    if any(abs(moment - at) <= DEFROST_AROUND for at in defrosts):
        return False
    return not hot_water(dhw, moment)


def hot_water(dhw: Series, moment: datetime) -> bool:
    """The cylinder warmed by DHW_RISE_K within 10 min in the last DHW_AFTER (a charge)."""
    recent = [(at, value) for at, value in dhw if timedelta(0) <= moment - at <= DHW_AFTER + timedelta(minutes=10)]
    for index, (at, value) in enumerate(recent):
        for later, other in recent[index + 1 :]:
            if later - at <= timedelta(minutes=10) and other - value >= DHW_RISE_K:
                return True
    return False


@dataclass(frozen=True, slots=True)
class BandMinimum:
    band: str
    # Median power of the phases, W; None below MIN_PHASES.
    power_w: float | None
    phases: int


def minimum_by_band(phases: Sequence[MinimumPhase]) -> list[BandMinimum]:
    result = []
    for index, name in enumerate(MIN_BAND_NAMES):
        low = MIN_BANDS[index]
        high = MIN_BANDS[index + 1] if index + 1 < len(MIN_BANDS) else 100.0
        values = [item.power_w for item in phases if item.outdoor_c is not None and low <= item.outdoor_c < high]
        median = round(statistics.median(values)) if len(values) >= MIN_PHASES else None
        result.append(BandMinimum(name, float(median) if median is not None else None, len(values)))
    return result


# --- switching off -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OffStart:
    off: bool
    # Waiting for a defrost (shown), or None.
    waiting: str | None
    # No defrost came in time: minimum power for the rest of the window.
    give_up: bool


def off_start(now: datetime, *, icing: bool, last_defrost_end: datetime | None, zone: tzinfo) -> OffStart:
    """Switch off now? Right away without icing; with icing only right after a defrost, at the latest until 7:00."""
    if not icing:
        return OffStart(True, None, False)
    if last_defrost_end is not None and timedelta(0) <= now - last_defrost_end <= DEFROST_FRESH:
        return OffStart(True, None, False)
    local = now.astimezone(zone)
    if local.time() >= WAIT_UNTIL:
        return OffStart(False, None, True)
    return OffStart(False, f"Aus direkt nach der nächsten Abtauung (spätestens {WAIT_UNTIL:%H:%M} Uhr)", False)


@dataclass(frozen=True, slots=True)
class RoomNow:
    name: str
    temperature_c: float | None
    setpoint_c: float
    loss_k: float
    humidity: float | None


def off_end(rooms: Sequence[RoomNow], frost_active: bool) -> str | None:
    """Why a zone switched off goes back to auto before the window's end, or None."""
    if frost_active:
        return "Frostschutz der Anlage beginnt"
    for room in rooms:
        if room.humidity is not None and room.humidity >= HUMID_FROM:
            return f"Feuchteschutz: {room.name} {room.humidity:.0f} %"
        if room.temperature_c is not None and room.temperature_c <= room.setpoint_c - room.loss_k + LOSS_MARGIN_K:
            return f"{room.name} hat den erlaubten Verlust erreicht ({_number(room.temperature_c)} °C)"
    return None


def humid(rooms: Sequence[RoomNow]) -> str | None:
    names = [room.name for room in rooms if room.humidity is not None and room.humidity >= HUMID_FROM]
    return f"Feuchteschutz: {', '.join(names)}" if names else None


def _number(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.0f}" if value == round(value) else f"{value:.1f}".replace(".", ",")


def _signed(value: float) -> str:
    text = _number(abs(value))
    return f"+{text}" if value > 0 else f"{MINUS}{text}" if value < 0 else "0"
