"""What the user enters per room: the static balancing, thermostat, comfort and schedule.

Kept in settings.json with each room (key "details"). The values of the static
hydraulic balancing are start values for learning; the app never sends any of
them anywhere. Every value is checked on its own: an invalid one falls back to
its default and is logged.

Also here: the switch of the room control (1.4.0, key "room_control") with the
pair of flow and return sensors for the test runs, and each room's stage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Any

from vaillant_eebus.hems.config import in_window, is_number, on_step, parse_time
from vaillant_eebus.ids import CLIMATE_ENTITY, ENTITY_ID, SENSOR_ENTITY

_LOGGER = logging.getLogger(__name__)

ROOM_HEAT_LOAD_W = (0.0, 10000.0)
DESIGN_C = (10.0, 30.0)
RATED_W = (0.0, 20000.0)
FLOW_LPH = (0.0, 2000.0)
# Room setpoints the app plans with; the thermostats' own limits come on top.
ROOM_SETPOINT_C = (12.0, 26.0)
COMFORT_K = (0.0, 2.0)
ROOM_LOSS_K = (0.0, 3.0)
EMERGENCY_C = (5.0, 26.0)
SURFACES_MAX = 4
SCHEDULE_MAX = 4
PRESETTING_MAX = 20
DEFAULT_SETPOINT_C = 20.0
# Room control (punkt1-trv-regelung.md 3 and 3a): how far the thermostat's setpoint may differ from
# the room's, the fixed offset of a room measured at its thermostat, and how the valves are spared.
CORRECTION_LIMIT_K = (0.5, 10.0)
OFFSET_K = (-1.0, 3.0)
DEADBAND_K = (0.5, 3.0)
MIN_INTERVAL_MIN = (5, 240)
MAX_COMMANDS = (4, 96)


class RoomStage(StrEnum):
    """A room's stage in the room control; the switch for all rooms caps it."""

    # Reads and learns; window and supply only as "würde" (as 1.3.0).
    OBSERVE = "observe"
    # Computes the thermostat's setpoint and logs what it would send.
    PREVIEW = "preview"
    # Sends the setpoint to the thermostat (live mode, after a test run).
    CONTROL = "control"


class RoomControlMode(StrEnum):
    """The switch of the room control for all rooms."""

    OFF = "off"
    OBSERVE = "observe"
    PREVIEW = "preview"
    CONTROL = "control"


_ORDER = (RoomControlMode.OFF, RoomControlMode.OBSERVE, RoomControlMode.PREVIEW, RoomControlMode.CONTROL)


def effective_stage(mode: RoomControlMode, stage: RoomStage) -> RoomControlMode:
    """The lower of the switch for all rooms and the room's own stage."""
    return min(mode, RoomControlMode(stage.value), key=_ORDER.index)


def at_least(stage: RoomControlMode, floor: RoomControlMode) -> bool:
    return _ORDER.index(stage) >= _ORDER.index(floor)


@dataclass(frozen=True, slots=True)
class ProbePair:
    """The wandering pair of pipe sensors for the test runs (punkt1-trv-regelung.md 3c)."""

    flow_entity: str | None = None
    return_entity: str | None = None
    # The room whose radiator the pair is fixed to; None: somewhere else.
    room_id: str | None = None
    # Since when it is fixed there.
    since: datetime | None = None


@dataclass(frozen=True, slots=True)
class RoomControlSettings:
    mode: RoomControlMode = RoomControlMode.OFF
    probe: ProbePair = ProbePair()
    # Test runs happen at night: they start from test_start to test_latest_start and end by test_end.
    test_start: time = time(22, 0)
    test_latest_start: time = time(1, 0)
    test_end: time = time(5, 0)


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
    # The room may be throttled to send more water to a cold room.
    throttle: bool = True
    # The thermostat's setpoint when the app fails (template of the emergency automation).
    emergency_c: float | None = None
    # Room control: the room's stage, the limit of the correction, the fixed offset of a room without
    # its own sensor, and how its thermostat's valve is spared (dead band, interval, commands a day).
    stage: RoomStage = RoomStage.OBSERVE
    correction_limit_k: float = 5.0
    offset_k: float = 0.0
    deadband_k: float = 0.5
    min_interval_min: int = 15
    max_commands: int = 24

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

    def setpoint_range(self) -> tuple[float, float]:
        """Lowest and highest setpoint of the room over the day."""
        values = [self.setpoint_c, *(window.setpoint_c for window in self.schedule)]
        return min(values), max(values)

    def fallback_c(self) -> float:
        """The thermostat's setpoint when the app lets go: the emergency value, else the room's setpoint."""
        return self.emergency_c if self.emergency_c is not None else self.setpoint_c


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
        stage=_stage(raw.get("stage")),
        correction_limit_k=_value(raw, "correction_limit_k", CORRECTION_LIMIT_K, defaults.correction_limit_k, step=0.5),
        offset_k=_value(raw, "offset_k", OFFSET_K, defaults.offset_k, step=0.5),
        deadband_k=_value(raw, "deadband_k", DEADBAND_K, defaults.deadband_k, step=0.5),
        min_interval_min=int(_value(raw, "min_interval_min", MIN_INTERVAL_MIN, defaults.min_interval_min, step=1)),
        max_commands=int(_value(raw, "max_commands", MAX_COMMANDS, defaults.max_commands, step=1)),
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
        "stage": details.stage.value,
        "correction_limit_k": details.correction_limit_k,
        "offset_k": details.offset_k,
        "deadband_k": details.deadband_k,
        "min_interval_min": details.min_interval_min,
        "max_commands": details.max_commands,
    }


def room_control_from_json(raw: Any, *, observe: bool) -> RoomControlSettings:
    """The switch and the probe pair; without saved settings (1.3.x) observe chooses the stage."""
    if raw is None:
        return RoomControlSettings(mode=RoomControlMode.OBSERVE if observe else RoomControlMode.OFF)
    if not isinstance(raw, dict):
        _LOGGER.warning("Settings: room_control is not an object, ignored")
        return RoomControlSettings()
    defaults = RoomControlSettings()
    mode = raw.get("mode")
    raw_probe = raw.get("probe")
    probe: dict[str, Any] = raw_probe if isinstance(raw_probe, dict) else {}
    since = probe.get("since")
    parsed_since = None
    if isinstance(since, str):
        try:
            parsed_since = datetime.fromisoformat(since)
        except ValueError:
            _LOGGER.warning("Settings: invalid probe time %r ignored", since)
    room_id = probe.get("room_id")
    return RoomControlSettings(
        mode=RoomControlMode(mode) if mode in {member.value for member in RoomControlMode} else defaults.mode,
        probe=ProbePair(
            flow_entity=_entity(probe, "flow_entity"),
            return_entity=_entity(probe, "return_entity"),
            room_id=room_id if isinstance(room_id, str) and ENTITY_ID.match(room_id) else None,
            since=parsed_since if parsed_since is not None and parsed_since.tzinfo is not None else None,
        ),
        test_start=_time(raw, "test_start", defaults.test_start),
        test_latest_start=_time(raw, "test_latest_start", defaults.test_latest_start),
        test_end=_time(raw, "test_end", defaults.test_end),
    )


def room_control_to_json(settings: RoomControlSettings) -> dict[str, Any]:
    probe = settings.probe
    return {
        "mode": settings.mode.value,
        "probe": {
            "flow_entity": probe.flow_entity,
            "return_entity": probe.return_entity,
            "room_id": probe.room_id,
            "since": probe.since.isoformat() if probe.since is not None else None,
        },
        "test_start": settings.test_start.strftime("%H:%M"),
        "test_latest_start": settings.test_latest_start.strftime("%H:%M"),
        "test_end": settings.test_end.strftime("%H:%M"),
    }


def _stage(raw: Any) -> RoomStage:
    if raw is None:
        return RoomStage.OBSERVE
    if raw in {member.value for member in RoomStage}:
        return RoomStage(raw)
    _LOGGER.warning("Settings: unknown room stage %r, using observe", raw)
    return RoomStage.OBSERVE


def _time(raw: dict[str, Any], key: str, default: time) -> time:
    value = raw.get(key)
    parsed = parse_time(value) if isinstance(value, str) else None
    if value is not None and parsed is None:
        _LOGGER.warning("Settings: invalid time %r for room_control.%s, using the default", value, key)
    return parsed or default


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
