"""The setpoint for one room's thermostat, every five minutes (punkt1-trv-regelung.md 3-6).

Order of precedence: window open (smallest value), room too warm (room setpoint
- 0.5 K at once), throttled as a donor (lower comfort limit), else the normal
setpoint: the room's setpoint from its schedule (earlier while preheating)
plus

- for a room with its own sensor: the correction. With a learned valve curve
  (test run) the app works out the opening the room needs and the setpoint
  that brings it ("Kaskade"); without one, or when that does not work, a
  simple correction in steps of 0.5 K after 0.2 K deviation for 30 minutes;
- for a room measured at its thermostat (the bathroom): a fixed offset.

The correction does not rise while the valve is at least 90 % open (water or
flow is missing, not setpoint) or without usable heat, and does not fall
while the valve is at most 5 % open. A setpoint that differs from what the app
set last is a manual change and is set back. Pure functions: the room control
keeps the state and sends through the write gate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from vaillant_eebus.room_data import RoomDetails
from vaillant_eebus.rooms.curve import ValveCurve
from vaillant_eebus.write_gate import RoomReason, ThermostatLimits

DEVIATION_K = 0.2
DEVIATION_FOR = timedelta(minutes=30)
STEP_K = 0.5
OPEN_VALVE = 90.0
CLOSED_VALVE = 5.0
AFTER_WINDOW = timedelta(minutes=30)
AFTER_SETPOINT_CHANGE = timedelta(minutes=30)
SAFETY_ABOVE_K = 0.5
SAFETY_BELOW_K = 0.5
PREHEAT_MAX = timedelta(hours=3)
PREHEAT_DEFAULT = timedelta(hours=1)
# Cascade: opening that holds the setpoint until learned, and how much more per K too cold and per K/h falling.
DEMAND_DEFAULT = 30.0
GAIN_PER_K = 30.0
GAIN_PER_K_PER_H = 10.0
# The cascade changes the thermostat only beyond the dead band plus this, against flapping.
CASCADE_HYSTERESIS_K = 0.25


class RoomMode(StrEnum):
    NORMAL = "normal"
    PREHEAT = "preheat"
    WINDOW = "window"
    SAFETY = "safety"
    DONOR = "donor"
    HOLD = "hold"


MODE_NAMES = {
    RoomMode.NORMAL: "normal",
    RoomMode.PREHEAT: "vorheizen",
    RoomMode.WINDOW: "Fenster offen",
    RoomMode.SAFETY: "zu warm",
    RoomMode.DONOR: "gedrosselt (Umverteilen)",
    RoomMode.HOLD: "hält",
}


@dataclass(frozen=True, slots=True)
class Correction:
    """The learned correction of a room with its own sensor (thermostat setpoint - room setpoint)."""

    value_k: float = 0.0
    # Since when the room deviates by DEVIATION_K or more, and in which direction (+1 too cold).
    since: datetime | None = None
    direction: int = 0
    changed_at: datetime | None = None
    # The room is too warm (safety rule): on entering it the correction went down once.
    too_warm: bool = False


@dataclass(frozen=True, slots=True)
class RoomInputs:
    now: datetime
    # Local time of the room's schedule.
    local: datetime
    details: RoomDetails
    at_thermostat: bool
    limits: ThermostatLimits | None
    room_c: float | None
    # K/h over the last half hour; positive = rising.
    trend_k_per_h: float | None
    valve: float | None
    trv_setpoint_c: float | None
    trv_c: float | None
    window_open: bool
    window_closed_at: datetime | None
    # Warm water reaches the radiators (heat pump or buffer).
    heat: bool
    donor: bool = False
    curve: ValveCurve | None = None
    demand_valve: float | None = None
    # For preheating: K/h per K of flow above the room, the startup in minutes and the flow now.
    heating_rate: float | None = None
    startup_min: float | None = None
    flow_c: float | None = None
    # What the app set last on the thermostat, and whether a command is still on its way.
    expected_c: float | None = None
    sending: bool = False
    # When the room's setpoint last changed (schedule, user).
    setpoint_changed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    # None: leave the thermostat as it is.
    target_c: float | None
    reason: RoomReason | None
    mode: RoomMode
    # The room's setpoint in effect (with preheating).
    setpoint_c: float
    note: str = ""
    manual: bool = False


def setpoint_now(details: RoomDetails, inputs: RoomInputs) -> tuple[float, bool]:
    """The room's setpoint now, and whether it is the next window's because of preheating."""
    current = details.setpoint_at(inputs.local.time())
    upcoming = _upcoming(details, inputs.local)
    if upcoming is None:
        return current, False
    start, value = upcoming
    if value <= current:
        return current, False
    if start - inputs.local <= _preheat_lead(inputs, value - current):
        return value, True
    return current, False


def decide(inputs: RoomInputs, correction: Correction) -> tuple[Decision, Correction]:
    details = inputs.details
    base, preheating = setpoint_now(details, inputs)
    limits = inputs.limits
    if limits is None:
        return Decision(None, None, RoomMode.HOLD, base, "Das Thermostat meldet keinen Bereich."), correction
    if inputs.window_open:
        return Decision(limits.min_c, RoomReason.WINDOW, RoomMode.WINDOW, base, "Fenster offen"), correction
    plain = details.setpoint_at(inputs.local.time())
    if (
        not inputs.at_thermostat
        and inputs.room_c is not None
        and inputs.room_c > plain + details.comfort_up_k + SAFETY_ABOVE_K
    ):
        target = _clamp(limits, details, plain - SAFETY_BELOW_K)
        if not correction.too_warm:
            # Too warm even with the correction: it goes down at once, so the room does not swing back.
            lowered = max(-details.correction_limit_k, correction.value_k - STEP_K)
            correction = Correction(value_k=lowered, changed_at=inputs.now, too_warm=True)
        return Decision(target, RoomReason.SAFETY, RoomMode.SAFETY, plain, "Raum zu warm"), correction
    if correction.too_warm:
        correction = replace(correction, too_warm=False)
    if inputs.at_thermostat:
        offset = details.offset_k
        note = f"fester Ausgleich {offset:+.1f} K".replace(".", ",")
    else:
        correction = _update(inputs, correction, base)
        offset = correction.value_k
        note = "Korrektur"
    if inputs.donor:
        lower = base - details.comfort_down_k
        target = _clamp(limits, details, lower + offset)
        return _manual(
            inputs, Decision(target, RoomReason.REDISTRIBUTE, RoomMode.DONOR, base, "gedrosselt")
        ), correction
    reason = RoomReason.PREHEAT if preheating else RoomReason.CONTROL
    mode = RoomMode.PREHEAT if preheating else RoomMode.NORMAL
    if not inputs.at_thermostat and inputs.room_c is None:
        note = "Raumsensor fehlt: Korrektur eingefroren"
    cascade = None if inputs.at_thermostat or inputs.room_c is None else _cascade(inputs, base)
    if cascade is not None:
        target, note = cascade
    else:
        target = _clamp(limits, details, base + offset)
    return _manual(inputs, Decision(target, reason, mode, base, note)), correction


def _manual(inputs: RoomInputs, decision: Decision) -> Decision:
    """A setpoint the app did not set is set back, exempt from the interval."""
    current = inputs.trv_setpoint_c
    expected = inputs.expected_c
    if inputs.sending or current is None or expected is None or abs(current - expected) < 0.01:
        return decision
    return replace(
        decision,
        reason=RoomReason.MANUAL,
        manual=True,
        note=f"Handänderung auf {current:.1f} °C zurückgesetzt".replace(".", ","),
    )


def _update(inputs: RoomInputs, correction: Correction, base: float) -> Correction:
    """The simple correction: 0.5 K after 0.2 K deviation for 30 minutes, with the valve rules."""
    details = inputs.details
    now = inputs.now
    if inputs.room_c is None:
        return replace(correction, since=None, direction=0)
    paused = (inputs.window_closed_at is not None and now - inputs.window_closed_at < AFTER_WINDOW) or (
        inputs.setpoint_changed_at is not None and now - inputs.setpoint_changed_at < AFTER_SETPOINT_CHANGE
    )
    if paused or inputs.donor:
        return replace(correction, since=None, direction=0)
    deviation = base - inputs.room_c
    if abs(deviation) < DEVIATION_K:
        return replace(correction, since=None, direction=0)
    direction = 1 if deviation > 0 else -1
    if correction.direction != direction or correction.since is None:
        return replace(correction, since=now, direction=direction)
    if now - correction.since < DEVIATION_FOR:
        return correction
    if direction > 0 and ((inputs.valve is not None and inputs.valve >= OPEN_VALVE) or not inputs.heat):
        return correction
    if direction < 0 and inputs.valve is not None and inputs.valve <= CLOSED_VALVE:
        return correction
    value = max(-details.correction_limit_k, min(details.correction_limit_k, correction.value_k + direction * STEP_K))
    return Correction(value_k=value, since=now, direction=direction, changed_at=now)


def _cascade(inputs: RoomInputs, base: float) -> tuple[float, str] | None:
    """The opening the room needs and the setpoint that brings it, from the learned valve curve."""
    curve, limits = inputs.curve, inputs.limits
    if curve is None or not curve.learned or limits is None or inputs.trv_c is None or inputs.valve is None:
        return None
    assert inputs.room_c is not None
    deviation = base - inputs.room_c
    trend = inputs.trend_k_per_h or 0.0
    demand = inputs.demand_valve if inputs.demand_valve is not None else DEMAND_DEFAULT
    wanted = max(0.0, min(100.0, demand + GAIN_PER_K * deviation - GAIN_PER_K_PER_H * trend))
    if inputs.valve >= OPEN_VALVE and wanted > inputs.valve:
        # More setpoint does not help an open valve: water or flow is missing.
        wanted = inputs.valve
    if not inputs.heat and wanted > inputs.valve:
        wanted = inputs.valve
    level = curve.nearest_level(wanted)
    offset = curve.offset_for(level) if level is not None else None
    if offset is None:
        return None
    raw = inputs.trv_c + offset
    # Against what the app set, not a manual change it is about to set back.
    current = inputs.expected_c if inputs.expected_c is not None else inputs.trv_setpoint_c
    if current is not None and abs(raw - current) < inputs.details.deadband_k + CASCADE_HYSTERESIS_K:
        raw = current
    note = f"braucht etwa {wanted:.0f} % Ventil (Stufe {level:.0f} %)"
    return _clamp(limits, inputs.details, raw), note


def _clamp(limits: ThermostatLimits, details: RoomDetails, celsius: float) -> float:
    low, high = details.setpoint_range()
    bounded = max(low - details.correction_limit_k, min(high + details.correction_limit_k, celsius))
    rounded = limits.round(bounded)
    # Rounding may step over the room's limit; then one step back inside.
    if rounded > high + details.correction_limit_k + 1e-6:
        rounded = round(rounded - limits.step, 2)
    if rounded < low - details.correction_limit_k - 1e-6:
        rounded = round(rounded + limits.step, 2)
    return rounded


def _upcoming(details: RoomDetails, local: datetime) -> tuple[datetime, float] | None:
    """The next start of a schedule window within the preheating time, with its setpoint."""
    best: tuple[datetime, float] | None = None
    for window in details.schedule:
        start = local.replace(hour=window.start.hour, minute=window.start.minute, second=0, microsecond=0)
        if start <= local:
            start += timedelta(days=1)
        if start - local > PREHEAT_MAX:
            continue
        value = details.setpoint_at(window.start)
        if best is None or start < best[0]:
            best = (start, value)
    return best


def _preheat_lead(inputs: RoomInputs, rise_k: float) -> timedelta:
    """How long before a window the room starts heating: learned speed, at most 3 h, else 1 h."""
    if (
        inputs.heating_rate is None
        or inputs.heating_rate <= 0
        or inputs.flow_c is None
        or inputs.room_c is None
        or inputs.startup_min is None
    ):
        return PREHEAT_DEFAULT
    speed = inputs.heating_rate * max(inputs.flow_c - inputs.room_c, 1.0)
    lead = timedelta(hours=rise_k / speed) + timedelta(minutes=inputs.startup_min)
    return min(PREHEAT_MAX, lead)
