"""The rule planner: 48 hours ahead, per zone and hour, with the reason for every shift.

Rules, no optimisation (decision point 4). Each hour gets a setpoint shift per
zone in steps within the zone's band, and a setpoint per room:

- warm day: a day whose forecast mean is above the heating limit is not heated;
- ahead (point 4): colder or sunnier weather in the zone's delay -> shift early;
- defrost (point 5): less load in risky hours (at most 3 in a row), stored
  before in the floor heating, within the allowed loss;
- night setback (point 7): in the night window, only when reheating pays off
  (no frost, no defrost risk while reheating), up early enough, not in humid rooms;
- block operation (point 9): marked in mild hours, carried out minute by minute;
- flow (point 2): the offset from the valves, carried on to every hour;
- night (F3, 1.5.0, hems/night.py): in the night window the zones are switched
  off (mean of the forecast above 3 °C) or go down to the heat pump's minimum
  power; for the hour now the live state of the night control counts;
- the Vaillant frost protection comes first: no block operation in its hours.

The plan itself sends nothing: the executor (1.5.0) carries out the parts of the
shift whose function is on "Steuern" (Part).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from enum import StrEnum

from vaillant_eebus.hems.config import HemsSettings, ZoneConfig, ZoneKind
from vaillant_eebus.hems.defrost import LEVEL_NAMES, RiskHour, RiskLevel
from vaillant_eebus.hems.forecast import WeatherHour
from vaillant_eebus.hems.house_model import DemandHour
from vaillant_eebus.hems.night import START_K, ZONE1_K, NightMode, Window, plan_window, window_at
from vaillant_eebus.room_data import RoomDetails

HOURS = 48
# Ahead (point 4): outdoor change within the zone's delay that shifts the zone.
COLDER_K = (2.0, 4.0)
MILDER_K = 3.0
SUN_W = 400.0
# Defrost: at most this many hours in a row with less load; storing before.
DEFROST_HOURS = 3
STORE_HOURS = 2
WARM_DAY_MIN_HOURS = 12


@dataclass(frozen=True, slots=True)
class RoomInput:
    entity_id: str
    name: str
    zone_id: str | None
    details: RoomDetails
    # Current relative humidity, %.
    humidity: float | None = None


@dataclass(frozen=True, slots=True)
class ZoneInput:
    zone_id: str
    label: str
    config: ZoneConfig
    # The zone's setpoint now (time program); None when unknown.
    base_c: float | None = None
    # The live flow offset of point 2 (K) and why.
    flow_offset_k: float = 0.0
    flow_reason: str | None = None
    # Expected flow = a + b * outdoor (learned from the circuit's flow setpoint).
    flow_line: tuple[float, float] | None = None
    # The night control now (F3): active in the window, the zone's shift (K, <= 0) and whether it is switched off;
    # why the zone has no setback (humid room, test run); the shift a radiator zone starts with (learned, K > 0).
    night_active: bool = False
    night_shift_k: float = 0.0
    night_off: bool = False
    night_block: str | None = None
    night_start_k: float = START_K


class Part(StrEnum):
    """Where a zone's shift comes from; the executor sends only the parts of functions in "Steuern"."""

    # Point 2: the flow after the valves.
    FLOW = "flow"
    # Point 4: cold, sun or warmth ahead, by the zone's lag.
    AHEAD = "ahead"
    # Point 4: a warm day is not heated.
    WARM = "warm"
    # Point 5: less load in defrost risk hours, store heat before them (1.6.0).
    DEFROST = "defrost"
    STORE = "store"
    # Point 7: the night setback (F3, after 1.4.2).
    NIGHT = "night"


@dataclass(frozen=True, slots=True)
class ZoneHour:
    zone_id: str
    shift_k: float
    target_c: float | None
    flow_c: float | None
    block: bool
    night: bool
    # The shift's parts before the band and step, K.
    parts: dict[Part, float] = field(default_factory=dict)
    # F3: the zone is switched off in this hour (for the hour now: right now).
    off: bool = False


@dataclass(frozen=True, slots=True)
class RoomHour:
    entity_id: str
    setpoint_c: float


@dataclass(frozen=True, slots=True)
class PlanHour:
    start: datetime
    outdoor_c: float | None
    humidity: float | None
    defrost: RiskLevel | None
    heat_w: float | None
    electricity_w: float | None
    frost: bool
    zones: list[ZoneHour]
    rooms: list[RoomHour]
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Plan:
    created_at: datetime
    hours: list[PlanHour]


@dataclass(frozen=True, slots=True)
class PlanInputs:
    now: datetime
    zone: tzinfo
    settings: HemsSettings
    weather: Sequence[WeatherHour]
    risks: Mapping[datetime, RiskHour]
    frost: frozenset[datetime]
    demand: Mapping[datetime, DemandHour]
    zones: Sequence[ZoneInput]
    rooms: Sequence[RoomInput]


def make_plan(inputs: PlanInputs) -> Plan:
    settings = inputs.settings
    zone = inputs.zone
    hours = [hour for hour in inputs.weather if hour.start >= _hour(inputs.now)][:HOURS]
    weather = {hour.start: hour for hour in inputs.weather}
    warm_days = _warm_days(inputs.weather, settings.heating_limit_c, zone)
    reduced = _defrost_hours(hours, inputs.risks, inputs.frost)
    plan_hours = []
    for hour in hours:
        local = hour.start.astimezone(zone)
        risk = inputs.risks.get(hour.start)
        frost = hour.start in inputs.frost
        warm = local.date() in warm_days
        reasons: list[str] = []
        if warm:
            reasons.append(f"Warmer Tag (im Mittel {_number(warm_days[local.date()])} °C): nicht heizen")
        if frost:
            reasons.append("Frostschutz der Anlage aktiv: kein Blockbetrieb, Zone mit Fußbodenheizung bleibt an")
        block = (
            not warm and not frost and hour.temperature_c is not None and hour.temperature_c >= settings.block_above_c
        )
        if block:
            reasons.append("Blockbetrieb (mild): lange Läufe, Zone 2 in den Pausen etwas tiefer")
        night = _night(hour, inputs, weather)
        if night.reason is not None:
            reasons.append(night.reason)
        reasons.extend(night.blocked)
        zone_hours = []
        for item in inputs.zones:
            config = item.config
            parts: dict[Part, float] = {}
            if warm:
                parts[Part.WARM] = -config.down_k
            else:
                ahead = _ahead(hour, item, weather, settings, zone, reasons)
                if ahead:
                    parts[Part.AHEAD] = ahead
                if hour.start in reduced and settings.defrost_loss_k >= settings.step_k:
                    parts[Part.DEFROST] = -settings.step_k
                    _add(reasons, f"Abtaurisiko {LEVEL_NAMES[risk.level] if risk else ''}: weniger Last")
                elif _stores(hour.start, reduced) and config.kind is not ZoneKind.RADIATOR:
                    parts[Part.STORE] = settings.step_k
                    _add(reasons, "Vorher Wärme speichern (Abtaurisiko danach)")
                active_night = night.zones.get(item.zone_id, 0.0)
                if active_night:
                    parts[Part.NIGHT] = active_night
                if item.flow_offset_k:
                    parts[Part.FLOW] = item.flow_offset_k
                    if item.flow_reason:
                        _add(reasons, item.flow_reason)
            shift = shift_of(parts, config, settings.step_k)
            target = round(item.base_c + shift, 1) if item.base_c is not None else None
            flow = None
            if item.flow_line is not None and hour.temperature_c is not None:
                a, b = item.flow_line
                flow = round(a + b * hour.temperature_c + shift * (1 + abs(b)), 1)
            zone_hours.append(
                ZoneHour(
                    zone_id=item.zone_id,
                    shift_k=shift,
                    target_c=target,
                    flow_c=flow,
                    block=block,
                    night=bool(night.zones.get(item.zone_id)) or item.zone_id in night.off,
                    parts=parts,
                    off=item.zone_id in night.off,
                )
            )
        # The rooms keep their own setpoints at night (F3 lowers the zones; open valves keep the pump running).
        rooms = [
            RoomHour(entity_id=room.entity_id, setpoint_c=round(room.details.setpoint_at(local.time()), 1))
            for room in inputs.rooms
        ]
        demand = inputs.demand.get(hour.start)
        plan_hours.append(
            PlanHour(
                start=hour.start,
                outdoor_c=hour.temperature_c,
                humidity=hour.humidity,
                defrost=risk.level if risk is not None else None,
                heat_w=demand.heat_w if demand is not None else None,
                electricity_w=demand.electricity_w if demand is not None else None,
                frost=frost,
                zones=zone_hours,
                rooms=rooms,
                reasons=reasons,
            )
        )
    return Plan(created_at=inputs.now, hours=plan_hours)


@dataclass(frozen=True, slots=True)
class _Night:
    # Zone ID -> shift (K, < 0) in this hour; zones switched off.
    zones: Mapping[str, float]
    off: frozenset[str]
    reason: str | None
    blocked: list[str] = field(default_factory=list)


def _night(hour: WeatherHour, inputs: PlanInputs, weather: Mapping[datetime, WeatherHour]) -> _Night:
    """F3 in this hour: while the window runs the night control's live state (it waits for a defrost, holds at the
    minimum, came back early ...), for later windows what they will do (shown). The hour now outside the window
    (5:00-5:30 for a start at 5:30) has nothing yet: the executor sends from the live state only."""
    settings = inputs.settings
    bounds = window_at(hour.start, settings.night_start, settings.night_end, inputs.zone) or window_at(
        hour.start + timedelta(minutes=59), settings.night_start, settings.night_end, inputs.zone
    )
    if bounds is None:
        return _Night({}, frozenset(), None)
    window = plan_window(bounds, weather, inputs.frost)
    live = bounds[0] <= inputs.now < bounds[1]
    this_hour = hour.start <= inputs.now < hour.start + timedelta(hours=1)
    zones: dict[str, float] = {}
    off: set[str] = set()
    blocked = []
    for item in inputs.zones:
        if item.config.kind is None:
            continue
        if item.night_block is not None:
            blocked.append(f"{item.label} ohne Nachtabsenkung: {item.night_block}")
        elif live:
            if item.night_active and item.night_off:
                off.add(item.zone_id)
            elif item.night_active and item.night_shift_k:
                zones[item.zone_id] = item.night_shift_k
        elif not this_hour:
            _planned(item, window, zones, off)
    return _Night(zones, frozenset(off), window.reason() if zones or off else None, blocked)


def _planned(item: ZoneInput, window: Window, zones: dict[str, float], off: set[str]) -> None:
    """What a later window will do with the zone (shown; the live control decides when it comes)."""
    if window.mode is NightMode.OFF:
        off.add(item.zone_id)
    elif item.config.kind is ZoneKind.RADIATOR:
        zones[item.zone_id] = -min(item.night_start_k, item.config.down_k)
    elif item.config.kind is ZoneKind.FLOOR and window.frost:
        zones[item.zone_id] = -min(ZONE1_K, item.config.down_k)


def _ahead(
    hour: WeatherHour,
    item: ZoneInput,
    weather: Mapping[datetime, WeatherHour],
    settings: HemsSettings,
    zone: tzinfo,
    reasons: list[str],
) -> float:
    now_c = hour.temperature_c
    if now_c is None or now_c >= settings.heating_limit_c:
        return 0.0
    later = weather.get(hour.start + timedelta(hours=round(item.config.lag_h)))
    if later is None or later.temperature_c is None:
        return 0.0
    change = later.temperature_c - now_c
    when = later.start.astimezone(zone)
    if change <= -COLDER_K[1]:
        _add(reasons, f"Vorausschauend: in {_hours(item.config.lag_h)} {_number(-change)} K kälter")
        return 2 * settings.step_k
    if change <= -COLDER_K[0]:
        _add(reasons, f"Vorausschauend: in {_hours(item.config.lag_h)} {_number(-change)} K kälter")
        return settings.step_k
    if change >= MILDER_K:
        _add(reasons, f"Vorausschauend: in {_hours(item.config.lag_h)} {_number(change)} K milder")
        return -settings.step_k
    if (later.radiation_w or 0.0) >= SUN_W and (hour.radiation_w or 0.0) < SUN_W / 2:
        _add(reasons, f"Vorausschauend: Sonne ab {when:%H} Uhr")
        return -settings.step_k
    return 0.0


def _defrost_hours(
    hours: Sequence[WeatherHour], risks: Mapping[datetime, RiskHour], frost: frozenset[datetime]
) -> set[datetime]:
    """Hours with less load: per block of risky hours the (at most 3) riskiest."""
    chosen: set[datetime] = set()
    block: list[RiskHour] = []
    for hour in [*hours, None]:
        risk = risks.get(hour.start) if hour is not None else None
        if risk is not None and risk.level in (RiskLevel.MEDIUM, RiskLevel.HIGH) and hour is not None:
            block.append(risk)
            continue
        if block:
            top = sorted(block, key=lambda item: item.per_run_hour, reverse=True)[:DEFROST_HOURS]
            chosen |= {item.start for item in top}
            block = []
    return chosen


def _stores(start: datetime, reduced: set[datetime]) -> bool:
    """One of the STORE_HOURS hours before a run of reduced hours."""
    if start in reduced:
        return False
    return any(start + timedelta(hours=ahead) in reduced for ahead in range(1, STORE_HOURS + 1))


def _warm_days(weather: Sequence[WeatherHour], limit_c: float, zone: tzinfo) -> dict[date, float]:
    by_day: dict[date, list[float]] = {}
    for hour in weather:
        if hour.temperature_c is not None:
            by_day.setdefault(hour.start.astimezone(zone).date(), []).append(hour.temperature_c)
    return {
        day: round(statistics.fmean(values), 1)
        for day, values in by_day.items()
        if len(values) >= WARM_DAY_MIN_HOURS and statistics.fmean(values) >= limit_c
    }


def shift_of(
    parts: Mapping[Part, float], config: ZoneConfig, step: float, only: frozenset[Part] | None = None
) -> float:
    """The zone's shift from its parts (all, or only some): summed, within the band, on the step."""
    total = sum(value for part, value in parts.items() if only is None or part in only)
    return _clamp(total, -config.down_k, config.up_k, step)


def _clamp(value: float, lowest: float, highest: float, step: float) -> float:
    steps = round(value / step)
    shift = steps * step
    return round(max(-abs(lowest), min(highest, shift)) + 0.0, 2)


def _add(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _hours(value: float) -> str:
    return "1 h" if value == 1 else f"{_number(value)} h"


def _number(value: float) -> str:
    """German, without a decimal for whole numbers: "3", "1,5"."""
    return f"{value:.0f}" if value == round(value) else f"{value:.1f}".replace(".", ",")
