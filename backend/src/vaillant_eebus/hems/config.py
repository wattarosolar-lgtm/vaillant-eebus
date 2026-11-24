"""The user's energy management settings, kept in settings.json under "hems".

Every value is checked on its own: an invalid one falls back to its default
and is logged, the rest of the settings stays. Keys of the planner of 1.1/1.2
(tariff, PV forecast, battery) are no longer read.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, replace
from datetime import time
from enum import StrEnum
from typing import Any, TypeIs

from vaillant_eebus.ids import ENTITY_ID, SENSOR_ENTITY, ZONE_ID

_LOGGER = logging.getLogger(__name__)

# Allowed ranges of the numbers, inclusive.
LATITUDE = (-90.0, 90.0)
LONGITUDE = (-180.0, 180.0)
DESIGN_OUTDOOR_C = (-30.0, 10.0)
HEAT_LOAD_W = (100.0, 50000.0)
FLOW_C = (20.0, 80.0)
BAND_K = (0.0, 3.0)
STEP_K = (0.5, 1.0)
INTERVAL_MIN = (15, 120)
LOSS_K = (0.0, 3.0)
TARGET_STARTS = (1, 48)
HEATING_LIMIT_C = (0.0, 25.0)
BLOCK_ABOVE_C = (-10.0, 20.0)
CLOUD_COMMANDS = (10, 1000)
ZONE1_THRESHOLD_W = (5.0, 500.0)
LAG_H = (0.0, 12.0)
ZONES_MAX = 10


class PlannerMode(StrEnum):
    OFF = "off"
    # Plans and shows what it would do; sends nothing, also in live mode.
    PREVIEW = "preview"
    # Carries the plan out through the write gate (1.5.0, hems/executor.py).
    CONTROL = "control"


# Stages of the energy management as a whole.
AVAILABLE_MODES = frozenset({PlannerMode.OFF, PlannerMode.PREVIEW, PlannerMode.CONTROL})
_ORDER = (PlannerMode.OFF, PlannerMode.PREVIEW, PlannerMode.CONTROL)


class Function(StrEnum):
    """The functions of the zone control (plan 1.5.0), each with its own stage."""

    # F1: floor heating zone off while the heat pump stands still, auto when it runs.
    ZONE1 = "zone1"
    # F2: the zone setpoint after the valves and the forecast (quick veto).
    FLOW = "flow"


# Stages each function offers in this version.
FUNCTION_MODES: dict[Function, frozenset[PlannerMode]] = {
    Function.ZONE1: AVAILABLE_MODES,
    Function.FLOW: AVAILABLE_MODES,
}


def lower(first: PlannerMode, second: PlannerMode) -> PlannerMode:
    """The smaller of two stages: the energy management's and a function's."""
    return min(first, second, key=_ORDER.index)


class WeatherSource(StrEnum):
    # Open-Meteo with DWD's ICON models; the Home Assistant weather entity is the fallback.
    OPEN_METEO = "open_meteo"
    HOME_ASSISTANT = "home_assistant"


class ZoneKind(StrEnum):
    # Floor heating with a mixer: slow, stores heat (the user's zone 1).
    FLOOR = "floor"
    # Radiators, often with thermostats (the user's zone 2).
    RADIATOR = "radiator"


@dataclass(frozen=True, slots=True)
class ZoneConfig:
    zone_id: str
    kind: ZoneKind | None = None
    # The circuit's flow temperature and the controller's flow setpoint (myVAILLANT circuit sensors).
    flow_entity: str | None = None
    flow_setpoint_entity: str | None = None
    # The controller's heating curve (read only, F2's advice; 1.5.0), e.g. sensor.zuhause_circuit_1_heating_curve.
    heating_curve_entity: str | None = None
    # Design values of the static balancing.
    design_flow_c: float | None = None
    design_return_c: float | None = None
    # How far the planner may shift the zone's setpoint (quick veto from 1.4.0).
    band_down_k: float | None = None
    band_up_k: float | None = None

    @property
    def down_k(self) -> float:
        if self.band_down_k is not None:
            return self.band_down_k
        # Radiator rooms may cool down more than a floor heating zone (1.4.0 start values).
        return 2.0 if self.kind is ZoneKind.RADIATOR else 1.0

    @property
    def up_k(self) -> float:
        if self.band_up_k is not None:
            return self.band_up_k
        return 2.0 if self.kind is not None else 1.0

    @property
    def lag_h(self) -> float:
        """Start value of the delay between a change outside and its effect inside."""
        return 3.0 if self.kind is ZoneKind.FLOOR else 1.0


@dataclass(frozen=True, slots=True)
class HemsSettings:
    mode: PlannerMode = PlannerMode.OFF
    weather_source: WeatherSource = WeatherSource.OPEN_METEO
    weather_entity: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    # Heat of the day for heating and for hot water (myVAILLANT energy sensors), for learning.
    heat_heating_entity: str | None = None
    heat_dhw_entity: str | None = None
    # Temperature of the buffer tank, shown and recorded.
    buffer_entity: str | None = None
    # The house from the static balancing: design outdoor temperature and heat load.
    design_outdoor_c: float | None = None
    heat_load_w: float | None = None
    zones: tuple[ZoneConfig, ...] = ()
    # Quick veto limits, used from 1.4.0: step and the least time between two changes.
    step_k: float = 0.5
    min_interval_min: int = 30
    # Night setback: local window and the default allowed temperature loss of a room.
    night_start: time = time(0, 0)
    night_end: time = time(10, 0)
    night_loss_k: float = 0.5
    # Loss allowed when it avoids defrosts (pausing in risk hours).
    defrost_loss_k: float = 1.0
    # Goal for the compressor's starts per day.
    target_starts: int = 10
    # Days with a mean outdoor temperature above this are not heated.
    heating_limit_c: float = 15.0
    # Block operation (long runs, long pauses) from this outdoor temperature up.
    block_above_c: float = 5.0
    # Commands to the Vaillant cloud per day (from 1.4.0): warning at 80 %, then only fallback.
    cloud_commands_max: int = 100
    # The user's automation: floor heating off below this power of the heat pump, on above.
    zone1_threshold_w: float = 30.0
    # Stages of the functions; what counts is the smaller of these and mode.
    zone1_mode: PlannerMode = PlannerMode.PREVIEW
    flow_mode: PlannerMode = PlannerMode.PREVIEW

    def zone(self, zone_id: str) -> ZoneConfig:
        return next((zone for zone in self.zones if zone.zone_id == zone_id), ZoneConfig(zone_id=zone_id))

    def entities(self) -> frozenset[str]:
        """The Home Assistant entities the energy management reads besides the rooms."""
        chosen = [self.heat_heating_entity, self.heat_dhw_entity, self.buffer_entity]
        for zone in self.zones:
            chosen += [zone.flow_entity, zone.flow_setpoint_entity, zone.heating_curve_entity]
        return frozenset(entity for entity in chosen if entity)

    def function_mode(self, function: Function) -> PlannerMode:
        return self.zone1_mode if function is Function.ZONE1 else self.flow_mode

    def effective(self, function: Function) -> PlannerMode:
        """The stage a function works in: the smaller of its own and the energy management's."""
        return lower(self.mode, self.function_mode(function))

    def with_function(self, function: Function, mode: PlannerMode) -> HemsSettings:
        if function is Function.ZONE1:
            return replace(self, zone1_mode=mode)
        return replace(self, flow_mode=mode)


def hems_from_json(raw: Any) -> HemsSettings:
    if raw is None:
        return HemsSettings()
    if not isinstance(raw, dict):
        _LOGGER.warning("Settings: hems is not an object, using the defaults")
        return HemsSettings()
    defaults = HemsSettings()
    mode = _enum(raw, "mode", PlannerMode, defaults.mode)
    if mode is PlannerMode.CONTROL and "zone1_mode" not in raw:
        # "Steuern" of a planner before 1.5.0 (1.1/1.2): the zone control starts in preview.
        _LOGGER.warning("Settings: planner stage control from an older version, using preview")
        mode = PlannerMode.PREVIEW
    weather_source = raw.get("weather_source")
    return HemsSettings(
        mode=mode,
        weather_source=(
            _enum(raw, "weather_source", WeatherSource, defaults.weather_source)
            if weather_source is not None
            else defaults.weather_source
        ),
        weather_entity=_entity(raw, "weather_entity"),
        latitude=_number(raw, "latitude", LATITUDE),
        longitude=_number(raw, "longitude", LONGITUDE),
        heat_heating_entity=_sensor(raw, "heat_heating_entity"),
        heat_dhw_entity=_sensor(raw, "heat_dhw_entity"),
        buffer_entity=_sensor(raw, "buffer_entity"),
        design_outdoor_c=_number(raw, "design_outdoor_c", DESIGN_OUTDOOR_C),
        heat_load_w=_number(raw, "heat_load_w", HEAT_LOAD_W),
        zones=_zones(raw.get("zones")),
        step_k=_value(raw, "step_k", STEP_K, defaults.step_k, step=0.5),
        min_interval_min=int(_value(raw, "min_interval_min", INTERVAL_MIN, defaults.min_interval_min, step=1)),
        night_start=_time(raw, "night_start", defaults.night_start),
        night_end=_time(raw, "night_end", defaults.night_end),
        night_loss_k=_value(raw, "night_loss_k", LOSS_K, defaults.night_loss_k, step=0.1),
        defrost_loss_k=_value(raw, "defrost_loss_k", LOSS_K, defaults.defrost_loss_k, step=0.1),
        target_starts=int(_value(raw, "target_starts", TARGET_STARTS, defaults.target_starts, step=1)),
        heating_limit_c=_value(raw, "heating_limit_c", HEATING_LIMIT_C, defaults.heating_limit_c),
        block_above_c=_value(raw, "block_above_c", BLOCK_ABOVE_C, defaults.block_above_c),
        cloud_commands_max=int(_value(raw, "cloud_commands_max", CLOUD_COMMANDS, defaults.cloud_commands_max, step=1)),
        zone1_threshold_w=_value(raw, "zone1_threshold_w", ZONE1_THRESHOLD_W, defaults.zone1_threshold_w),
        zone1_mode=_function_mode(raw, Function.ZONE1, defaults.zone1_mode),
        flow_mode=_function_mode(raw, Function.FLOW, defaults.flow_mode),
    )


def hems_to_json(settings: HemsSettings) -> dict[str, Any]:
    return {
        "mode": settings.mode.value,
        "weather_source": settings.weather_source.value,
        "weather_entity": settings.weather_entity,
        "latitude": settings.latitude,
        "longitude": settings.longitude,
        "heat_heating_entity": settings.heat_heating_entity,
        "heat_dhw_entity": settings.heat_dhw_entity,
        "buffer_entity": settings.buffer_entity,
        "design_outdoor_c": settings.design_outdoor_c,
        "heat_load_w": settings.heat_load_w,
        "zones": [zone_to_json(zone) for zone in settings.zones],
        "step_k": settings.step_k,
        "min_interval_min": settings.min_interval_min,
        "night_start": settings.night_start.strftime("%H:%M"),
        "night_end": settings.night_end.strftime("%H:%M"),
        "night_loss_k": settings.night_loss_k,
        "defrost_loss_k": settings.defrost_loss_k,
        "target_starts": settings.target_starts,
        "heating_limit_c": settings.heating_limit_c,
        "block_above_c": settings.block_above_c,
        "cloud_commands_max": settings.cloud_commands_max,
        "zone1_threshold_w": settings.zone1_threshold_w,
        "zone1_mode": settings.zone1_mode.value,
        "flow_mode": settings.flow_mode.value,
    }


def zone_to_json(zone: ZoneConfig) -> dict[str, Any]:
    return {
        "zone_id": zone.zone_id,
        "kind": zone.kind.value if zone.kind is not None else None,
        "flow_entity": zone.flow_entity,
        "flow_setpoint_entity": zone.flow_setpoint_entity,
        "heating_curve_entity": zone.heating_curve_entity,
        "design_flow_c": zone.design_flow_c,
        "design_return_c": zone.design_return_c,
        "band_down_k": zone.band_down_k,
        "band_up_k": zone.band_up_k,
    }


def parse_time(text: str) -> time | None:
    """ "HH:MM" -> time; None if it is not a time of day."""
    match = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", text)
    return time(int(match.group(1)), int(match.group(2))) if match else None


def in_window(moment: time, start: time, end: time) -> bool:
    """Local time of day in [start, end); an end before the start crosses midnight."""
    if start == end:
        return False
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


def _zones(raw: Any) -> tuple[ZoneConfig, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: hems.zones is not a list, ignored")
        return ()
    zones: list[ZoneConfig] = []
    for item in raw[:ZONES_MAX]:
        if not isinstance(item, dict) or not isinstance(item.get("zone_id"), str) or not ZONE_ID.match(item["zone_id"]):
            _LOGGER.warning("Settings: invalid zone %r ignored", item)
            continue
        if any(zone.zone_id == item["zone_id"] for zone in zones):
            continue
        kind = item.get("kind")
        zones.append(
            ZoneConfig(
                zone_id=item["zone_id"],
                kind=ZoneKind(kind) if kind in {member.value for member in ZoneKind} else None,
                flow_entity=_sensor(item, "flow_entity"),
                flow_setpoint_entity=_sensor(item, "flow_setpoint_entity"),
                heating_curve_entity=_sensor(item, "heating_curve_entity"),
                design_flow_c=_number(item, "design_flow_c", FLOW_C),
                design_return_c=_number(item, "design_return_c", FLOW_C),
                band_down_k=_number(item, "band_down_k", BAND_K, step=0.5),
                band_up_k=_number(item, "band_up_k", BAND_K, step=0.5),
            )
        )
    return tuple(zones)


def _function_mode(raw: dict[str, Any], function: Function, default: PlannerMode) -> PlannerMode:
    mode = _enum(raw, f"{function.value}_mode", PlannerMode, default)
    if mode not in FUNCTION_MODES[function]:
        _LOGGER.warning("Settings: stage %s of %s is not available in this version, using preview", mode, function)
        return PlannerMode.PREVIEW
    return mode


def _enum[E: StrEnum](raw: dict[str, Any], key: str, kind: type[E], default: E) -> E:
    value = raw.get(key)
    if value is None:
        return default
    try:
        return kind(value)
    except ValueError:
        _LOGGER.warning("Settings: unknown hems.%s %r, using %s", key, value, default.value)
        return default


def _entity(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, str) and ENTITY_ID.match(value):
        return value
    _LOGGER.warning("Settings: invalid entity %r for hems.%s ignored", value, key)
    return None


def _sensor(raw: dict[str, Any], key: str) -> str | None:
    value = _entity(raw, key)
    if value is not None and not SENSOR_ENTITY.match(value):
        _LOGGER.warning("Settings: %r for hems.%s is not a sensor, ignored", value, key)
        return None
    return value


def _time(raw: dict[str, Any], key: str, default: time) -> time:
    value = raw.get(key)
    if value is None:
        return default
    parsed = parse_time(value) if isinstance(value, str) else None
    if parsed is None:
        _LOGGER.warning("Settings: invalid time %r for hems.%s, using the default", value, key)
        return default
    return parsed


def on_step(value: float, step: float) -> bool:
    steps = value / step
    return abs(steps - round(steps)) < 1e-6


def _number(raw: dict[str, Any], key: str, allowed: tuple[float, float], *, step: float | None = None) -> float | None:
    """An optional number; None when it is missing or invalid."""
    value = raw.get(key)
    if value is None:
        return None
    if is_number(value) and allowed[0] <= float(value) <= allowed[1] and (step is None or on_step(float(value), step)):
        return float(value)
    _LOGGER.warning("Settings: hems.%s %r is out of range, ignored", key, value)
    return None


def _value(
    raw: dict[str, Any], key: str, allowed: tuple[float, float], default: float, *, step: float | None = None
) -> float:
    """A number with a default for when it is missing or invalid."""
    value = _number(raw, key, allowed, step=step)
    return default if value is None else value


def is_number(value: Any) -> TypeIs[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
