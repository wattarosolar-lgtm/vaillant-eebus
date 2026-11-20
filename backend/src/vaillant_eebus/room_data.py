"""What the user enters per room: the static balancing, thermostat, comfort and schedule.

Kept in settings.json with each room (key "details"). The values of the static
hydraulic balancing are start values for learning; the app never sends any of
them anywhere. Every value is checked on its own: an invalid one falls back to
its default and is logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Any

from vaillant_eebus.hems.config import in_window, is_number, on_step, parse_time
from vaillant_eebus.ids import CLIMATE_ENTITY, SENSOR_ENTITY

_LOGGER = logging.getLogger(__name__)

ROOM_HEAT_LOAD_W = (0.0, 10000.0)
DESIGN_C = (10.0, 30.0)
RATED_W = (0.0, 20000.0)
FLOW_LPH = (0.0, 2000.0)
# Room setpoints the app plans with; the thermostats' own limits come on top (1.5.0).
ROOM_SETPOINT_C = (12.0, 26.0)
COMFORT_K = (0.0, 2.0)
ROOM_LOSS_K = (0.0, 3.0)
EMERGENCY_C = (5.0, 26.0)
SURFACES_MAX = 4
SCHEDULE_MAX = 4
PRESETTING_MAX = 20
DEFAULT_SETPOINT_C = 20.0


class SurfaceKind(StrEnum):
    RADIATOR = "radiator"
    FLOOR = "floor"


class RatedBasis(StrEnum):
    """The temperatures the rated output refers to (flow/return in °C, room 20 °C)."""

    T75_65 = "75/65"
    T55_45 = "55/45"
    T50_40 = "50/40"


@dataclass(frozen=True, slots=True)
class HeatingSurface:
    kind: SurfaceKind = SurfaceKind.RADIATOR
    rated_w: float | None = None
    rated_basis: RatedBasis = RatedBasis.T75_65
    # Design flow of the static balancing.
    flow_lph: float | None = None
    # The valve's presetting as written in the report, e.g. "2" or "voll offen".
    presetting: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleWindow:
    """Every day from start to end (local; an end before the start crosses midnight) this setpoint."""

    start: time
    end: time
    setpoint_c: float


@dataclass(frozen=True, slots=True)
class RoomDetails:
    # Static balancing: the room's heat load and design temperature.
    heat_load_w: float | None = None
    design_c: float | None = None
    surfaces: tuple[HeatingSurface, ...] = ()
    # The radiator thermostat (Zigbee2MQTT climate entity), its valve position in % and the room's humidity.
    trv_entity: str | None = None
    valve_entity: str | None = None
    humidity_entity: str | None = None
    # The room's setpoint outside the schedule's windows.
    setpoint_c: float = DEFAULT_SETPOINT_C
    schedule: tuple[ScheduleWindow, ...] = ()
    # Comfort band around the setpoint.
    comfort_down_k: float = 0.5
    comfort_up_k: float = 0.5
    # Allowed temperature loss at night and to avoid defrosts; None: the energy management's default.
    night_loss_k: float | None = None
    defrost_loss_k: float | None = None
    # The room may be throttled to send more water to a cold room (1.5.0).
    throttle: bool = True
    # The thermostat's setpoint when the app fails (template of the emergency automation, 1.5.0).
    emergency_c: float | None = None

    def setpoint_at(self, moment: time) -> float:
        """The room's setpoint at this local time of day."""
        for window in self.schedule:
            if in_window(moment, window.start, window.end):
                return window.setpoint_c
        return self.setpoint_c

    def heat_loss_w_per_k(self, design_outdoor_c: float | None) -> float | None:
        """Start value of the room's heat loss from the static balancing (W/K)."""
        if self.heat_load_w is None or design_outdoor_c is None:
            return None
        design = self.design_c if self.design_c is not None else DEFAULT_SETPOINT_C
        spread = design - design_outdoor_c
        return self.heat_load_w / spread if spread > 1 else None


def details_from_json(raw: Any) -> RoomDetails:
    if raw is None:
        return RoomDetails()
    if not isinstance(raw, dict):
        _LOGGER.warning("Settings: room details are not an object, ignored")
        return RoomDetails()
    defaults = RoomDetails()
    throttle = raw.get("throttle")
    return RoomDetails(
        heat_load_w=_number(raw, "heat_load_w", ROOM_HEAT_LOAD_W),
        design_c=_number(raw, "design_c", DESIGN_C, step=0.5),
        surfaces=_surfaces(raw.get("surfaces")),
        trv_entity=_entity(raw, "trv_entity", climate=True),
        valve_entity=_entity(raw, "valve_entity"),
        humidity_entity=_entity(raw, "humidity_entity"),
        setpoint_c=_value(raw, "setpoint_c", ROOM_SETPOINT_C, defaults.setpoint_c, step=0.5),
        schedule=_schedule(raw.get("schedule")),
        comfort_down_k=_value(raw, "comfort_down_k", COMFORT_K, defaults.comfort_down_k, step=0.1),
        comfort_up_k=_value(raw, "comfort_up_k", COMFORT_K, defaults.comfort_up_k, step=0.1),
        night_loss_k=_number(raw, "night_loss_k", ROOM_LOSS_K, step=0.1),
        defrost_loss_k=_number(raw, "defrost_loss_k", ROOM_LOSS_K, step=0.1),
        throttle=throttle if isinstance(throttle, bool) else defaults.throttle,
        emergency_c=_number(raw, "emergency_c", EMERGENCY_C, step=0.5),
    )


def details_to_json(details: RoomDetails) -> dict[str, Any]:
    return {
        "heat_load_w": details.heat_load_w,
        "design_c": details.design_c,
        "surfaces": [
            {
                "kind": surface.kind.value,
                "rated_w": surface.rated_w,
                "rated_basis": surface.rated_basis.value,
                "flow_lph": surface.flow_lph,
                "presetting": surface.presetting,
            }
            for surface in details.surfaces
        ],
        "trv_entity": details.trv_entity,
        "valve_entity": details.valve_entity,
        "humidity_entity": details.humidity_entity,
        "setpoint_c": details.setpoint_c,
        "schedule": [
            {
                "start": window.start.strftime("%H:%M"),
                "end": window.end.strftime("%H:%M"),
                "setpoint_c": window.setpoint_c,
            }
            for window in details.schedule
        ],
        "comfort_down_k": details.comfort_down_k,
        "comfort_up_k": details.comfort_up_k,
        "night_loss_k": details.night_loss_k,
        "defrost_loss_k": details.defrost_loss_k,
        "throttle": details.throttle,
        "emergency_c": details.emergency_c,
    }


def _surfaces(raw: Any) -> tuple[HeatingSurface, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: heating surfaces are not a list, ignored")
        return ()
    surfaces = []
    for item in raw[:SURFACES_MAX]:
        if not isinstance(item, dict):
            _LOGGER.warning("Settings: invalid heating surface %r ignored", item)
            continue
        kind = item.get("kind")
        basis = item.get("rated_basis")
        presetting = item.get("presetting")
        surfaces.append(
            HeatingSurface(
                kind=SurfaceKind(kind) if kind in {member.value for member in SurfaceKind} else SurfaceKind.RADIATOR,
                rated_w=_number(item, "rated_w", RATED_W),
                rated_basis=(
                    RatedBasis(basis) if basis in {member.value for member in RatedBasis} else RatedBasis.T75_65
                ),
                flow_lph=_number(item, "flow_lph", FLOW_LPH),
                presetting=presetting.strip()[:PRESETTING_MAX] if isinstance(presetting, str) else "",
            )
        )
    return tuple(surfaces)


def _schedule(raw: Any) -> tuple[ScheduleWindow, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: room schedule is not a list, ignored")
        return ()
    windows = []
    for item in raw[:SCHEDULE_MAX]:
        start = end = None
        setpoint = None
        if isinstance(item, dict):
            start = parse_time(item["start"]) if isinstance(item.get("start"), str) else None
            end = parse_time(item["end"]) if isinstance(item.get("end"), str) else None
            setpoint = _number(item, "setpoint_c", ROOM_SETPOINT_C, step=0.5)
        if start is None or end is None or start == end or setpoint is None:
            _LOGGER.warning("Settings: invalid schedule window %r ignored", item)
            continue
        windows.append(ScheduleWindow(start=start, end=end, setpoint_c=setpoint))
    return tuple(windows)


def _entity(raw: dict[str, Any], key: str, *, climate: bool = False) -> str | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    pattern = CLIMATE_ENTITY if climate else SENSOR_ENTITY
    if isinstance(value, str) and pattern.match(value):
        return value
    _LOGGER.warning("Settings: invalid entity %r for room %s ignored", value, key)
    return None


def _number(raw: dict[str, Any], key: str, allowed: tuple[float, float], *, step: float | None = None) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if is_number(value) and allowed[0] <= float(value) <= allowed[1] and (step is None or on_step(float(value), step)):
        return float(value)
    _LOGGER.warning("Settings: room %s %r is out of range, ignored", key, value)
    return None


def _value(
    raw: dict[str, Any], key: str, allowed: tuple[float, float], default: float, *, step: float | None = None
) -> float:
    value = _number(raw, key, allowed, step=step)
    return default if value is None else value
