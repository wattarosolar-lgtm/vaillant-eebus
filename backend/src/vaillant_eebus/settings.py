"""User settings, persisted as JSON in the app's /data directory."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from vaillant_eebus.hems.config import HemsSettings, hems_from_json, hems_to_json
from vaillant_eebus.ids import CLIMATE_ENTITY, ENTITY_ID, SENSOR_ENTITY, WATER_HEATER_ENTITY, ZONE_ID
from vaillant_eebus.room_data import RoomDetails, details_from_json, details_to_json

_LOGGER = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


class OperatingMode(StrEnum):
    # Reads only; changes are previewed and logged but never sent.
    DEMO = "demo"
    # Reads and writes to the heat pump.
    LIVE = "live"


class Connection(StrEnum):
    """How the app reaches the heat pump."""

    # Through the myVAILLANT integration's entities in Home Assistant (Vaillant cloud).
    MYVAILLANT = "myvaillant"
    # Locally over EEBUS with this app's bridge and a paired gateway.
    EEBUS = "eebus"


class ExportGroup(StrEnum):
    """Groups of values the app reports to Home Assistant as sensors (spec 10)."""

    STATUS = "status"
    ZONES = "zones"
    HOT_WATER = "hot_water"
    TEMPERATURES = "temperatures"
    ENERGY = "energy"
    POWER_LIMITS = "power_limits"
    COMPRESSOR = "compressor"
    # The energy management's status and plan.
    HEMS = "hems"


# Spec 7.1: everything the overview shows; power limits and compressor control are off.
DEFAULT_EXPORT = frozenset(
    {
        ExportGroup.STATUS,
        ExportGroup.ZONES,
        ExportGroup.HOT_WATER,
        ExportGroup.TEMPERATURES,
        ExportGroup.ENERGY,
        ExportGroup.HEMS,
    }
)

ROOM_NAME_MAX = 60
MYVAILLANT_ZONES_MAX = 10
# Heat sensors that are added up, e.g. heating and hot water of the myVAILLANT integration.
HEAT_SENSORS_MAX = 4


@dataclass(frozen=True, slots=True)
class Room:
    """A room: the Home Assistant entity with its temperature and what the user entered for it.

    The entity is a temperature sensor, or - for a room without one - the radiator
    thermostat's climate entity (its current_temperature, measured at the radiator).
    """

    entity_id: str
    # Shown in the UI, e.g. "Wohnzimmer".
    name: str
    # The zone whose average includes this room; None shows the room without a zone.
    zone_id: str | None = None
    # The room's zone with the other connection (EEBUS or myVAILLANT), whose zones have
    # other IDs; it comes back when the user switches back.
    other_zone_id: str | None = None
    details: RoomDetails = field(default_factory=RoomDetails)

    @property
    def measured_at_thermostat(self) -> bool:
        """The room's temperature comes from its thermostat, at the radiator."""
        return CLIMATE_ENTITY.match(self.entity_id) is not None


@dataclass(frozen=True, slots=True)
class MyVaillantEntities:
    """The myVAILLANT integration's entities the app reads and controls."""

    # Climate entities, one per heating zone; their IDs are the zones' IDs.
    zones: tuple[str, ...] = ()
    hot_water: str | None = None
    outdoor_temperature: str | None = None
    # Electrical power of the heat pump in W or kW; any power sensor, optional.
    power: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.zones) or self.hot_water is not None

    def all(self) -> frozenset[str]:
        return frozenset(
            entity
            for entity in (*self.zones, self.hot_water, self.outdoor_temperature, self.power)
            if entity is not None
        )


@dataclass(frozen=True, slots=True)
class Settings:
    # None until the user chose in the setup wizard; settings of older versions mean EEBUS.
    connection: Connection | None = None
    myvaillant: MyVaillantEntities = MyVaillantEntities()
    # SKI of the gateway the user paired, normalized to upper case.
    paired_ski: str | None = None
    # A new installation starts in demo; the app keeps the last chosen mode.
    operating_mode: OperatingMode = OperatingMode.DEMO
    rooms: tuple[Room, ...] = ()
    # Home Assistant sensors with the heat produced today (each starts at 0 every day); their sum counts.
    heat_energy_entities: tuple[str, ...] = ()
    export: frozenset[ExportGroup] = DEFAULT_EXPORT
    hems: HemsSettings = field(default_factory=HemsSettings)


class SettingsStore:
    """Loads and atomically saves Settings in <data_dir>/settings.json.

    The store is the single owner of the current settings: every part of the
    backend changes them through update(), so no part overwrites another
    part's change with an outdated copy.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / SETTINGS_FILE
        self._current: Settings | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def current(self) -> Settings:
        """The settings in effect, read from the file on first use."""
        if self._current is None:
            self._current = self.load()
        return self._current

    def update(self, change: Callable[[Settings], Settings]) -> Settings:
        """Apply change to the current settings and save the result."""
        settings = change(self.current)
        self.save(settings)
        return settings

    def load(self) -> Settings:
        """Return the saved settings, or defaults if there are none.

        A corrupt file is kept as settings.json.corrupt and replaced by defaults,
        so the app still starts in demo mode; the user then has to pair again.
        An invalid Home Assistant choice only drops itself, not the pairing.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        try:
            data = json.loads(raw)
            paired_ski = data.get("paired_ski")
            if paired_ski is not None and not isinstance(paired_ski, str):
                raise ValueError("paired_ski is not a string")
            mode = data.get("operating_mode", OperatingMode.DEMO)
            if not isinstance(mode, str):
                raise ValueError("operating_mode is not a string")
            return Settings(
                connection=_connection(data),
                myvaillant=myvaillant_from_json(data.get("myvaillant")),
                paired_ski=paired_ski,
                operating_mode=OperatingMode(mode),
                rooms=_rooms(data.get("rooms", [])),
                heat_energy_entities=_heat_entities(data),
                export=_export(data.get("export")),
                hems=hems_from_json(data.get("hems")),
            )
        except (ValueError, AttributeError) as err:
            corrupt = self._path.with_name(SETTINGS_FILE + ".corrupt")
            os.replace(self._path, corrupt)
            _LOGGER.error("Settings file %s is unreadable (%s); moved to %s, using defaults", self._path, err, corrupt)
            return Settings()

    def save(self, settings: Settings) -> None:
        """Write settings atomically: temporary file in the same directory, then rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_to_json(settings), indent=2, ensure_ascii=False) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=SETTINGS_FILE, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        self._current = settings


def valid_room(room: Room) -> bool:
    return (
        ENTITY_ID.match(room.entity_id) is not None
        and 0 < len(room.name.strip()) <= ROOM_NAME_MAX
        and all(zone is None or ZONE_ID.match(zone) is not None for zone in (room.zone_id, room.other_zone_id))
    )


def valid_myvaillant(entities: MyVaillantEntities) -> bool:
    return (
        len(entities.zones) <= MYVAILLANT_ZONES_MAX
        and len(set(entities.zones)) == len(entities.zones)
        and all(CLIMATE_ENTITY.match(zone) for zone in entities.zones)
        and (entities.hot_water is None or WATER_HEATER_ENTITY.match(entities.hot_water) is not None)
        and all(
            entity is None or SENSOR_ENTITY.match(entity) is not None
            for entity in (entities.outdoor_temperature, entities.power)
        )
    )


def _connection(data: dict[str, Any]) -> Connection | None:
    raw = data.get("connection", Connection.EEBUS.value)
    if raw is None:
        return None
    if raw not in {connection.value for connection in Connection}:
        _LOGGER.warning("Settings: unknown connection %r, using EEBUS", raw)
        return Connection.EEBUS
    return Connection(raw)


def myvaillant_from_json(raw: Any) -> MyVaillantEntities:
    """The saved myVAILLANT entities; invalid entries are dropped one by one."""
    if raw is None:
        return MyVaillantEntities()
    if not isinstance(raw, dict):
        _LOGGER.warning("Settings: myvaillant is not an object, ignored")
        return MyVaillantEntities()
    zones: list[str] = []
    raw_zones = raw.get("zones", [])
    for zone in raw_zones if isinstance(raw_zones, list) else []:
        if isinstance(zone, str) and CLIMATE_ENTITY.match(zone) and zone not in zones:
            zones.append(zone)
        else:
            _LOGGER.warning("Settings: invalid myVAILLANT zone %r ignored", zone)

    def single(key: str, pattern: re.Pattern[str]) -> str | None:
        value = raw.get(key)
        if value is None or (isinstance(value, str) and pattern.match(value)):
            return value
        _LOGGER.warning("Settings: invalid myVAILLANT %s %r ignored", key, value)
        return None

    return MyVaillantEntities(
        zones=tuple(zones[:MYVAILLANT_ZONES_MAX]),
        hot_water=single("hot_water", WATER_HEATER_ENTITY),
        outdoor_temperature=single("outdoor_temperature", SENSOR_ENTITY),
        power=single("power", SENSOR_ENTITY),
    )


def myvaillant_to_json(entities: MyVaillantEntities) -> dict[str, Any]:
    return {
        "zones": list(entities.zones),
        "hot_water": entities.hot_water,
        "outdoor_temperature": entities.outdoor_temperature,
        "power": entities.power,
    }


def _rooms(raw: Any) -> tuple[Room, ...]:
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: rooms is not a list, ignored")
        return ()
    rooms: list[Room] = []
    for item in raw:
        room = None
        if isinstance(item, dict):
            entity_id, name = item.get("entity_id"), item.get("name")
            zone_id, other_zone_id = item.get("zone_id"), item.get("other_zone_id")
            if (
                isinstance(entity_id, str)
                and isinstance(name, str)
                and all(zone is None or isinstance(zone, str) for zone in (zone_id, other_zone_id))
            ):
                room = Room(
                    entity_id=entity_id,
                    name=name,
                    zone_id=zone_id,
                    other_zone_id=other_zone_id,
                    details=details_from_json(item.get("details")),
                )
        if room is None or not valid_room(room):
            _LOGGER.warning("Settings: invalid room %r ignored", item)
            continue
        if all(other.entity_id != room.entity_id for other in rooms):
            rooms.append(room)
    return tuple(rooms)


def _heat_entities(data: dict[str, Any]) -> tuple[str, ...]:
    # 0.6.0 kept a single sensor as heat_energy_entity.
    raw = data.get("heat_energy_entities", [data["heat_energy_entity"]] if data.get("heat_energy_entity") else [])
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: heat_energy_entities is not a list, ignored")
        return ()
    entities: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not ENTITY_ID.match(item):
            _LOGGER.warning("Settings: invalid heat sensor %r ignored", item)
        elif item not in entities and len(entities) < HEAT_SENSORS_MAX:
            entities.append(item)
    return tuple(entities)


def _export(raw: Any) -> frozenset[ExportGroup]:
    if raw is None:
        return DEFAULT_EXPORT
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: export is not a list, using the default")
        return DEFAULT_EXPORT
    known = {group.value for group in ExportGroup}
    groups = frozenset(ExportGroup(item) for item in raw if item in known)
    if len(groups) != len(raw):
        _LOGGER.warning("Settings: unknown export groups in %r ignored", raw)
    return groups


def _to_json(settings: Settings) -> dict[str, Any]:
    return {
        "connection": settings.connection.value if settings.connection is not None else None,
        "myvaillant": myvaillant_to_json(settings.myvaillant),
        "paired_ski": settings.paired_ski,
        "operating_mode": settings.operating_mode.value,
        "rooms": [
            {
                "entity_id": room.entity_id,
                "name": room.name,
                "zone_id": room.zone_id,
                "other_zone_id": room.other_zone_id,
                "details": details_to_json(room.details),
            }
            for room in settings.rooms
        ],
        "heat_energy_entities": list(settings.heat_energy_entities),
        "export": sorted(group.value for group in settings.export),
        "hems": hems_to_json(settings.hems),
    }
