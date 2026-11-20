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
- the Vaillant frost protection comes first: no block operation in its hours.

In 1.3.0 the plan is only shown (Vorschau); nothing is sent.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from vaillant_eebus.hems.config import HemsSettings, ZoneConfig, ZoneKind, in_window
from vaillant_eebus.hems.defrost import LEVEL_NAMES, RiskHour, RiskLevel
from vaillant_eebus.hems.forecast import WeatherHour
from vaillant_eebus.hems.house_model import DemandHour
from vaillant_eebus.room_data import RoomDetails

HOURS = 48
# Ahead (point 4): outdoor change within the zone's delay that shifts the zone.
COLDER_K = (2.0, 4.0)
MILDER_K = 3.0
SUN_W = 400.0
# Defrost: at most this many hours in a row with less load; storing before.
DEFROST_HOURS = 3
STORE_HOURS = 2
# Night setback: not when it is colder than this in the window.
NIGHT_MIN_C = -5.0
REHEAT_HOURS = 3
HUMID_FROM = 65.0
# Start values of how fast a zone heats up again, K/h.
REHEAT_RATE = {ZoneKind.FLOOR: 0.25, ZoneKind.RADIATOR: 1.0}
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


@dataclass(frozen=True, slots=True)
class ZoneHour:
    zone_id: str
    shift_k: float
    target_c: float | None
    flow_c: float | None
    block: bool
    night: bool


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
        zone_hours = []
        for item in inputs.zones:
            config = item.config
            shift = 0.0
            if warm:
                shift = -config.down_k
            else:
                shift += _ahead(hour, item, weather, settings, zone, reasons)
                if hour.start in reduced and settings.defrost_loss_k >= settings.step_k:
                    shift -= settings.step_k
                    _add(reasons, f"Abtaurisiko {LEVEL_NAMES[risk.level] if risk else ''}: weniger Last")
                elif _stores(hour.start, reduced) and config.kind is not ZoneKind.RADIATOR:
                    shift += settings.step_k
                    _add(reasons, "Vorher Wärme speichern (Abtaurisiko danach)")
                active_night = night.zones.get(item.zone_id, 0.0)
                if active_night:
                    shift -= active_night
                if item.flow_offset_k:
                    shift += item.flow_offset_k
                    if item.flow_reason:
                        _add(reasons, item.flow_reason)
            shift = _clamp(shift, -config.down_k, config.up_k, settings.step_k)
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
                    night=bool(night.zones.get(item.zone_id)),
                )
            )
        rooms = [
            RoomHour(
                entity_id=room.entity_id,
                setpoint_c=round(room.details.setpoint_at(local.time()) - night.rooms.get(room.entity_id, 0.0), 1),
            )
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
    # Zone ID / room entity -> K lowered in this hour.
    zones: Mapping[str, float]
    rooms: Mapping[str, float]
    reason: str | None


def _night(hour: WeatherHour, inputs: PlanInputs, weather: Mapping[datetime, WeatherHour]) -> _Night:
    settings = inputs.settings
    local = hour.start.astimezone(inputs.zone)
    if not in_window(local.time(), settings.night_start, settings.night_end):
        return _Night({}, {}, None)
    window = _window_hours(hour.start, inputs, weather)
    temps = [item.temperature_c for item in window if item.temperature_c is not None]
    end = window[-1].start + timedelta(hours=1) if window else hour.start + timedelta(hours=1)
    reheat = [item.start for item in window if end - item.start <= timedelta(hours=REHEAT_HOURS)]
    if any(start in inputs.frost for start in (item.start for item in window)):
        return _Night({}, {}, "Nachtabsenkung ausgesetzt: Frostschutz der Anlage")
    if temps and min(temps) < NIGHT_MIN_C:
        return _Night({}, {}, "Nachtabsenkung ausgesetzt: zu kalt, das Wiederaufheizen kostet mehr")
    risky = [
        start
        for start in reheat
        if (risk := inputs.risks.get(start)) is not None and risk.level in (RiskLevel.MEDIUM, RiskLevel.HIGH)
    ]
    if risky:
        return _Night({}, {}, "Nachtabsenkung ausgesetzt: Abtaurisiko beim Wiederaufheizen")
    zones: dict[str, float] = {}
    rooms: dict[str, float] = {}
    humid = []
    for item in inputs.zones:
        members = [room for room in inputs.rooms if room.zone_id == item.zone_id]
        allowed = []
        for room in members:
            loss = room.details.night_loss_k if room.details.night_loss_k is not None else settings.night_loss_k
            if room.humidity is not None and room.humidity >= HUMID_FROM:
                humid.append(room.name)
                continue
            allowed.append(loss)
            # Up again early enough: rooms follow their zone's reheating.
            rooms[room.entity_id] = loss
        if members and not allowed:
            continue
        loss = min(allowed) if allowed else settings.night_loss_k
        steps = math.floor(loss / settings.step_k + 1e-9)
        lowered = steps * settings.step_k
        rate = REHEAT_RATE.get(item.config.kind, 1.0) if item.config.kind else 1.0
        reheat_h = math.ceil(lowered / rate - 1e-9) if lowered > 0 else 0
        if lowered > 0 and hour.start + timedelta(hours=reheat_h) < end:
            zones[item.zone_id] = lowered
        else:
            # Reheating: the zone's rooms go back to their setpoint, too.
            for room in members:
                rooms.pop(room.entity_id, None)
    if not inputs.zones:
        rooms = {
            room.entity_id: room.details.night_loss_k
            if room.details.night_loss_k is not None
            else settings.night_loss_k
            for room in inputs.rooms
            if room.humidity is None or room.humidity < HUMID_FROM
        }
    reason = "Nachtabsenkung" if zones or rooms else "Sanft wieder aufheizen"
    if humid:
        reason += f"; Feuchteschutz: {', '.join(sorted(set(humid)))} ohne Absenkung"
    return _Night(zones, rooms, reason)


def _window_hours(start: datetime, inputs: PlanInputs, weather: Mapping[datetime, WeatherHour]) -> list[WeatherHour]:
    """The hours of the night window that contains start."""
    settings = inputs.settings
    zone = inputs.zone
    moment = start
    while in_window((moment - timedelta(hours=1)).astimezone(zone).time(), settings.night_start, settings.night_end):
        moment -= timedelta(hours=1)
        if start - moment > timedelta(hours=24):
            break
    result = []
    while in_window(moment.astimezone(zone).time(), settings.night_start, settings.night_end):
        result.append(weather.get(moment, WeatherHour(start=moment)))
        moment += timedelta(hours=1)
        if moment - start > timedelta(hours=24):
            break
    return result


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
