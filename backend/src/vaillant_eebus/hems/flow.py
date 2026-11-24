"""Zone 2's flow after the valves (F2, plan-1.5.0-zonen.md "F2 Vorlauf Zone 2 im Detail").

The lever is the radiator zone's setpoint (quick veto, executor.py): with the
controller's room influence off it shifts the heating curve in parallel, 0.5 K
setpoint is about 0.7 K flow (curve 0.35). Pure functions on the five-minute
samples:

- bottleneck(): the room with the widest mean valve over 7 days, counted only
  while the heat pump delivers heat and outside windows (+30 min), setpoint
  changes (30 min), heating up after a raise (until 0.2 K close, at most 3 h),
  test runs and sweeps; below 24 such hours the snapshot of the last hour.
- decide(): up fast, down slowly. A room undersupplied (and redistributing
  exhausted) brings a shift below 0 back to 0 at once, else +0.5 K per interval
  up to the band. Down by 0.5 K only with heat from the heat pump, no room
  undersupplied or wide open, the bottleneck's valve between 10 and 70 % (it
  draws heat and has room), 2 h after the last change and 6 h after a jump back,
  zone 2's flow setpoint staying 1 K above zone 1's and the floor room not cold.
- curve_advice(): from 7 days of the flow part, a steeper/flatter curve or a
  parallel shift (the app never writes the curve).
"""

from __future__ import annotations

import itertools
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from vaillant_eebus.hems.room_model import FlowHint

type Series = Sequence[tuple[datetime, float]]

BOTTLENECK_DAYS = 7
BOTTLENECK_MIN_HOURS = 24.0
SAMPLE = timedelta(minutes=5)
# The heat pump delivers heat: it runs and the flow (or buffer) is this far above the room (as rooms/supply.py).
RUNNING_W = 50.0
FROM_PUMP_K = 8.0
# Phases that say nothing about the flow.
WINDOW_AFTER = timedelta(minutes=30)
SETPOINT_QUIET = timedelta(minutes=30)
REHEAT_NEAR_K = 0.2
REHEAT_MAX = timedelta(hours=3)
RAISE_K = 0.3
# Supply of a room in a sample: as room_model.supply (valve and how far below the setpoint).
UNDER_VALVE = 90.0
UNDER_K = 0.3
# The decision.
LOWER_FROM = 10.0
LOWER_BELOW = 70.0
WIDE_OPEN = 90.0
LOWER_EVERY = timedelta(hours=2)
AFTER_JUMP = timedelta(hours=6)
ZONE1_MARGIN_K = 1.0
# Flow per K of setpoint when the zone's flow line is not learned yet (curve about 0.35-0.4).
DEFAULT_FLOW_PER_K = 1.4
# The heating curve advice.
ADVICE_DAYS = 7
ADVICE_MIN_DAY_HOURS = 6.0
ADVICE_MIN_DAYS = 5
ADVICE_FROM_K = 0.5
STEEP_FROM_K = 0.5
STEEP_SPREAD_C = 4.0
MINUS = "\u2212"


@dataclass(frozen=True, slots=True)
class RoomSamples:
    """One room's samples (five minutes apart, same moments as the zone's)."""

    room_id: str
    name: str
    temperature: Series
    valve: Series
    setpoint: Series
    # 1 while its window was open / a test run or sweep had its thermostat.
    window: Series = ()
    testing: Series = ()
    # Measured at the thermostat (bath): its valve counts, it is never "undersupplied" (sensor at the radiator).
    at_thermostat: bool = False


@dataclass(frozen=True, slots=True)
class Bottleneck:
    room_id: str
    name: str
    # Mean valve opening in the counted samples, %, and the share of them undersupplied.
    mean_valve: float
    under_share: float
    # Hours counted; long_term False: the snapshot of the last hour (too few hours yet).
    hours: float
    long_term: bool


@dataclass(frozen=True, slots=True)
class FlowInputs:
    now: datetime
    offset_k: float
    changed_at: datetime | None
    jumped_at: datetime | None
    step_k: float
    down_k: float
    up_k: float
    min_interval: timedelta
    # The heat pump delivers heat to the zone now.
    heat_pump: bool
    # Rooms undersupplied now (not those measured at the thermostat).
    under: Sequence[str]
    # The room control's word on raising: None without rooms it leads, else (may raise, why not).
    may_raise: tuple[bool, str | None] | None
    # Rooms in a normal phase whose valve was at least WIDE_OPEN % over the last hour.
    wide_open: Sequence[str]
    bottleneck: str | None
    # The bottleneck's mean valve over the last hour, None when it is not in a normal phase now.
    bottleneck_valve: float | None
    # Zone 2's flow setpoint minus zone 1's after a step down, K; None when zone 1 is not known (no floor zone).
    zone1_gap_k: float | None
    zone1_unknown: bool = False
    floor_room_cold: bool = False
    # Something else holds the flow (e.g. heating up after the night).
    hold: str | None = None


@dataclass(frozen=True, slots=True)
class FlowDecision:
    offset_k: float
    hint: FlowHint
    # Why it changed (protocol, plan), or why it holds (status).
    reason: str | None
    changed: bool
    jumped: bool = False


def heat_from_pump(power_w: float | None, supply_c: float | None, room_c: float | None) -> bool:
    return (
        power_w is not None
        and power_w > RUNNING_W
        and supply_c is not None
        and room_c is not None
        and supply_c >= room_c + FROM_PUMP_K
    )


def normal_moments(room: RoomSamples, now: datetime, since: datetime) -> set[datetime]:
    """Sample moments of the room outside windows, setpoint changes, heating up and test runs."""
    window = _by_time(room.window)
    testing = _by_time(room.testing)
    setpoints = sorted(room.setpoint)
    temperatures = _by_time(room.temperature)
    result: set[datetime] = set()
    last_window: datetime | None = None
    last_change: datetime | None = None
    reheat_since: datetime | None = None
    before: float | None = None
    for moment, setpoint in setpoints:
        if before is not None and abs(setpoint - before) >= 0.05:
            last_change = moment
            if setpoint - before >= RAISE_K:
                reheat_since = moment
        before = setpoint
        if window.get(moment, 0.0) >= 1.0:
            last_window = moment
        room_c = temperatures.get(moment)
        if reheat_since is not None and (
            moment - reheat_since > REHEAT_MAX or (room_c is not None and room_c >= setpoint - REHEAT_NEAR_K)
        ):
            reheat_since = None
        if moment < since or moment > now:
            continue
        if last_window is not None and moment - last_window <= WINDOW_AFTER:
            continue
        if last_change is not None and moment - last_change < SETPOINT_QUIET:
            continue
        if reheat_since is not None or testing.get(moment, 0.0) >= 1.0:
            continue
        result.add(moment)
    return result


def bottleneck(
    rooms: Sequence[RoomSamples], power: Series, supply: Series, now: datetime, *, days: int = BOTTLENECK_DAYS
) -> Bottleneck | None:
    """The room with the widest mean valve while the heat pump delivers heat, over the last days."""
    since = now - timedelta(days=days)
    watts, supply_c = _by_time(power), _by_time(supply)
    candidates = []
    for room in rooms:
        valves, temperatures, setpoints = _by_time(room.valve), _by_time(room.temperature), _by_time(room.setpoint)
        counted, under = [], 0
        for moment in normal_moments(room, now, since):
            valve, room_c = valves.get(moment), temperatures.get(moment)
            if valve is None or not heat_from_pump(watts.get(moment), supply_c.get(moment), room_c):
                continue
            counted.append(valve)
            setpoint = setpoints.get(moment)
            if (
                not room.at_thermostat
                and room_c is not None
                and setpoint is not None
                and valve >= UNDER_VALVE
                and room_c <= setpoint - UNDER_K
            ):
                under += 1
        hours = len(counted) * SAMPLE.total_seconds() / 3600
        if hours >= BOTTLENECK_MIN_HOURS:
            candidates.append(
                Bottleneck(
                    room_id=room.room_id,
                    name=room.name,
                    mean_valve=round(statistics.fmean(counted), 1),
                    under_share=round(under / len(counted), 3),
                    hours=round(hours, 1),
                    long_term=True,
                )
            )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.mean_valve, item.under_share))


def snapshot(rooms: Sequence[RoomSamples], now: datetime) -> Bottleneck | None:
    """The widest valve over the last 30 minutes (the hint before 1.5.0), while there are too few hours."""
    best: Bottleneck | None = None
    for room in rooms:
        recent = [value for moment, value in room.valve if now - moment <= timedelta(minutes=30)]
        if not recent:
            continue
        mean = round(statistics.fmean(recent), 1)
        if best is None or mean > best.mean_valve:
            best = Bottleneck(room.room_id, room.name, mean, 0.0, 0.0, long_term=False)
    return best


def hour_valve(room: RoomSamples, now: datetime) -> float | None:
    """The mean valve over the last hour; None when the room's newest sample is not in a normal phase."""
    newest = max((moment for moment, _ in room.setpoint if moment <= now), default=None)
    if newest is None or now - newest > timedelta(minutes=10):
        return None
    if newest not in normal_moments(room, now, newest):
        return None
    recent = [value for moment, value in room.valve if timedelta(0) <= now - moment <= timedelta(hours=1)]
    return round(statistics.fmean(recent), 1) if recent else None


def decide(inputs: FlowInputs) -> FlowDecision:
    """Up fast, down slowly (see the module's description)."""
    offset = inputs.offset_k
    if inputs.hold is not None:
        return FlowDecision(offset, FlowHint.HOLD, inputs.hold, changed=False)
    if inputs.under:
        names = ", ".join(inputs.under)
        if not inputs.heat_pump:
            return FlowDecision(
                offset, FlowHint.HOLD, f"{names} unterversorgt, die Wärmepumpe liefert gerade nicht", changed=False
            )
        if inputs.may_raise is not None and not inputs.may_raise[0]:
            return FlowDecision(offset, FlowHint.HOLD, inputs.may_raise[1], changed=False)
        why = inputs.may_raise[1] if inputs.may_raise is not None and inputs.may_raise[1] else None
        if offset < 0:
            reason = f"Vorlauf wieder höher: {names} unterversorgt (von {_signed(offset)} K)"
            return FlowDecision(0.0, FlowHint.HIGHER, why or reason, changed=True, jumped=True)
        if offset >= inputs.up_k:
            return FlowDecision(offset, FlowHint.HIGHER, f"{names} unterversorgt, Band oben erreicht", changed=False)
        if inputs.changed_at is not None and inputs.now - inputs.changed_at < inputs.min_interval:
            return FlowDecision(
                offset, FlowHint.HIGHER, f"{names} unterversorgt, nächster Schritt nach dem Abstand", changed=False
            )
        new = round(min(inputs.up_k, offset + inputs.step_k), 2)
        return FlowDecision(new, FlowHint.HIGHER, why or f"Vorlauf höher: {names} unterversorgt", changed=True)
    why_not = _why_not_lower(inputs)
    if why_not is not None:
        return FlowDecision(offset, FlowHint.HOLD, why_not, changed=False)
    new = round(max(-inputs.down_k, offset - inputs.step_k), 2)
    valve = f"{inputs.bottleneck_valve:.0f}" if inputs.bottleneck_valve is not None else "?"
    return FlowDecision(new, FlowHint.LOWER, f"Vorlauf tiefer: Engpass {inputs.bottleneck} bei {valve} %", changed=True)


def _why_not_lower(inputs: FlowInputs) -> str | None:
    if inputs.offset_k <= -inputs.down_k:
        return "Band unten erreicht"
    if not inputs.heat_pump:
        return "die Wärmepumpe liefert gerade keine Wärme"
    if inputs.wide_open:
        return f"Ventil weit offen: {', '.join(inputs.wide_open)}"
    if inputs.bottleneck is None or inputs.bottleneck_valve is None:
        return "Engpassraum unbekannt oder gerade nicht aussagekräftig"
    valve = inputs.bottleneck_valve
    if valve < LOWER_FROM:
        return f"Engpass {inputs.bottleneck} zieht kaum Wärme ({valve:.0f} %)"
    if valve >= LOWER_BELOW:
        return f"Engpass {inputs.bottleneck} bei {valve:.0f} % (Ziel 70 bis 90 %)"
    if inputs.changed_at is not None and inputs.now - inputs.changed_at < LOWER_EVERY:
        return "tiefer höchstens alle 2 h"
    if inputs.jumped_at is not None and inputs.now - inputs.jumped_at < AFTER_JUMP:
        return "nach dem Sprung zurück 6 h nicht tiefer"
    if inputs.zone1_unknown:
        return "Vorlauf-Soll der Zone 1 unbekannt"
    if inputs.zone1_gap_k is not None and inputs.zone1_gap_k < ZONE1_MARGIN_K:
        return "Zone 1 bestimmt dann den Vorlauf (Vorlauf-Soll Zone 2 nicht mehr 1 K darüber)"
    if inputs.floor_room_cold:
        return "die Zone mit Fußbodenheizung ist unter ihrem Komfortband"
    return None


# --- heating curve advice -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurveAdvice:
    # "steeper", "flatter" or "parallel".
    kind: str
    text: str
    mean_k: float
    days: int
    curve_now: float | None
    curve_new: float | None


def curve_advice(
    *,
    offsets: Series,
    power: Series,
    supply: Series,
    rooms_c: Series,
    outdoor: Series,
    flow_setpoint: Series,
    zone_setpoint: Series,
    curve: Series,
    flow_per_k: float,
    label: str,
    now: datetime,
    zone: tzinfo,
) -> CurveAdvice | None:
    """What the flow part of the last 7 days says about the heating curve; None without enough or with ±0 K.

    Only hours with heat from the heat pump count, only days with at least 6 such hours, at least 5 days; after a
    change of the curve the days before are left out.
    """
    since = now - timedelta(days=ADVICE_DAYS)
    changed = _last_change(curve)
    if changed is not None and changed > since:
        since = changed
    watts, supply_c, room_c = _by_time(power), _by_time(supply), _by_time(rooms_c)
    outside, flow_sp, zone_sp = _by_time(outdoor), _by_time(flow_setpoint), _by_time(zone_setpoint)
    counted: list[tuple[float, float, float | None, float | None]] = []
    per_day: dict[date, int] = {}
    for moment, offset in offsets:
        if moment < since or moment > now:
            continue
        if not heat_from_pump(watts.get(moment), supply_c.get(moment), room_c.get(moment)):
            continue
        out = outside.get(moment)
        if out is None:
            continue
        counted.append((offset, out, flow_sp.get(moment), zone_sp.get(moment)))
        day = moment.astimezone(zone).date()
        per_day[day] = per_day.get(day, 0) + 1
    hours_per_sample = SAMPLE.total_seconds() / 3600
    days = [day for day, count in per_day.items() if count * hours_per_sample >= ADVICE_MIN_DAY_HOURS]
    if len(days) < ADVICE_MIN_DAYS:
        return None
    used = counted
    mean = statistics.fmean(item[0] for item in used)
    if abs(mean) < ADVICE_FROM_K:
        return None
    curve_now = curve[-1][1] if curve else None
    ordered = sorted(used, key=lambda item: item[1])
    half = len(ordered) // 2
    cold, mild = ordered[:half], ordered[half:]
    if cold and mild:
        cold_k = statistics.fmean(item[0] for item in cold)
        mild_k = statistics.fmean(item[0] for item in mild)
        spread = statistics.median(item[1] for item in mild) - statistics.median(item[1] for item in cold)
        flows = [(item[2], item[3]) for item in cold if item[2] is not None and item[3] is not None]
        if abs(cold_k - mild_k) >= STEEP_FROM_K and spread >= STEEP_SPREAD_C and flows:
            flow_cold = statistics.fmean(value[0] for value in flows if value[0] is not None)
            room_set = statistics.fmean(value[1] for value in flows if value[1] is not None)
            lift = flow_cold - room_set
            if lift > 1:
                ratio = (lift + (cold_k - mild_k) * flow_per_k) / lift
                kind = "steeper" if ratio > 1 else "flatter"
                word = "steiler" if ratio > 1 else "flacher"
                if curve_now is not None:
                    curve_new = round(curve_now * ratio, 2)
                    text = f"Heizkurve {label} {word}: etwa {_comma(curve_now)} → {_comma(curve_new)}"
                else:
                    curve_new = None
                    text = f"Heizkurve {label} etwa {abs(ratio - 1) * 100:.0f} % {word}"
                return CurveAdvice(kind, text, round(mean, 2), len(days), curve_now, curve_new)
    shift = round(mean / 0.5) * 0.5
    word = "höher" if shift > 0 else "tiefer"
    text = f"Wunschtemperatur im Zeitprogramm von {label} um {_comma(abs(shift))} K {word}"
    return CurveAdvice("parallel", text, round(mean, 2), len(days), curve_now, curve_now)


def flow_per_k(line: tuple[float, float] | None) -> float:
    """Flow change per K of zone setpoint: 1 + the flow line's slope against outside (a parallel shift)."""
    return 1 + abs(line[1]) if line is not None else DEFAULT_FLOW_PER_K


def _last_change(series: Series) -> datetime | None:
    last: datetime | None = None
    for (_, before), (moment, value) in itertools.pairwise(series):
        if abs(value - before) > 1e-6:
            last = moment
    return last


def _by_time(series: Series) -> Mapping[datetime, float]:
    return dict(series)


def _signed(value: float) -> str:
    text = _comma(abs(value))
    return f"+{text}" if value > 0 else f"{MINUS}{text}" if value < 0 else "0"


def _comma(value: float) -> str:
    return f"{value:g}".replace(".", ",")
