"""Everything the app does with Home Assistant, behind one object for main and api.

Outside Home Assistant (no Supervisor token) there is no client: the import
reports itself unavailable and nothing is exported.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from vaillant_eebus.energy import EnergyMeter, EnergyToday
from vaillant_eebus.ha_client import EntityState, HomeAssistantClient, HomeAssistantUnavailable
from vaillant_eebus.ha_export import PREFIX, HomeAssistantExport
from vaillant_eebus.ha_import import HeatToday, HomeAssistantImport, HomeAssistantState, ValueStatus
from vaillant_eebus.hems.sources import HemsSources
from vaillant_eebus.history import HistoryGap, PowerHistory, bucket_means, first_bucket
from vaillant_eebus.ids import is_own_entity
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.myvaillant import MyVaillantState, discover, is_zone, zone_label
from vaillant_eebus.room_data import RoomDetails
from vaillant_eebus.settings import Connection, ExportGroup, MyVaillantEntities, Room, Settings, SettingsStore
from vaillant_eebus.state import StateHub

_LOGGER = logging.getLogger(__name__)

# Below this much electrical energy a day's ratio says nothing yet (early morning).
PERFORMANCE_MIN_KWH = 0.5
# The exported power sensor whose history feeds the chart (group "Leistung und Energie").
POWER_SENSOR = f"sensor.{PREFIX}power"
# The history is read from Home Assistant at most this often; several open pages share it.
HISTORY_CACHE = timedelta(seconds=60)

# Units that make a sensor a candidate for a room, the heat of the day, a power or a valve position.
_KIND_UNITS = {
    "temperature": frozenset({"°C", "°F"}),
    "energy": frozenset({"Wh", "kWh", "MWh"}),
    "power": frozenset({"W", "kW"}),
    "percent": frozenset({"%"}),
}
# Entity domains offered as they are: the weather forecast and the myVAILLANT zones and hot water.
_KIND_DOMAINS = {"weather": "weather.", "climate": "climate.", "water_heater": "water_heater."}
# A room's temperature: a temperature sensor or a radiator thermostat; a thermostat; the humidity.
_KIND_SPECIAL = ("room", "thermostat", "humidity", "window")
ENTITY_KINDS = (*_KIND_UNITS, *_KIND_DOMAINS, *_KIND_SPECIAL)
# Window contacts (1.4.3): device classes, and names for contacts without one.
_WINDOW_CLASSES = frozenset({"window", "door", "opening", "garage_door"})
_WINDOW_NAMES = ("window", "fenster", "contact", "kontakt")
MAX_CHOICES = 200

_EXPORT_NAMES = {
    ExportGroup.STATUS: "Status",
    ExportGroup.ZONES: "Zonen",
    ExportGroup.HOT_WATER: "Warmwasser",
    ExportGroup.TEMPERATURES: "Temperaturen",
    ExportGroup.ENERGY: "Leistung und Energie",
    ExportGroup.POWER_LIMITS: "Leistungsdeckel",
    ExportGroup.COMPRESSOR: "Verdichter-Flexibilität",
    ExportGroup.HEMS: "Energiemanagement",
}


@dataclass(frozen=True, slots=True)
class EntityChoice:
    entity_id: str
    name: str
    state: str
    unit: str | None


def _is_thermostat(entity: EntityState) -> bool:
    """A climate entity that is not a myVAILLANT zone, e.g. a radiator thermostat."""
    return entity.entity_id.startswith("climate.") and not is_zone(entity)


def _is_humidity(entity: EntityState) -> bool:
    return entity.attributes.get("unit_of_measurement") == "%" and (
        entity.attributes.get("device_class") == "humidity"
        or any(name in entity.entity_id for name in ("humidity", "feuchte"))
    )


def _is_window(entity: EntityState) -> bool:
    """A window or door contact: a binary_sensor of such a device class, or named so."""
    if not entity.entity_id.startswith("binary_sensor.") or is_own_entity(entity.entity_id):
        return False
    if entity.attributes.get("device_class") in _WINDOW_CLASSES:
        return True
    return entity.attributes.get("device_class") is None and any(name in entity.entity_id for name in _WINDOW_NAMES)


def _is_kind(entity: EntityState, kind: str) -> bool:
    if kind == "window":
        return _is_window(entity)
    if kind in _KIND_DOMAINS:
        return entity.entity_id.startswith(_KIND_DOMAINS[kind])
    if kind in ("room", "thermostat") and _is_thermostat(entity):
        return True
    if not entity.entity_id.startswith("sensor.") or entity.entity_id.startswith(f"sensor.{PREFIX}"):
        return False
    if kind == "room":
        return entity.attributes.get("unit_of_measurement") in _KIND_UNITS["temperature"]
    if kind == "humidity":
        return _is_humidity(entity)
    if kind == "percent":
        return entity.attributes.get("unit_of_measurement") == "%" and not _is_humidity(entity)
    if kind == "thermostat":
        return False
    return entity.attributes.get("unit_of_measurement") in _KIND_UNITS[kind]


def _unless_own(entity_id: str | None) -> str | None:
    return None if entity_id is not None and is_own_entity(entity_id) else entity_id


def performance_today(heat: HeatToday | None, electricity: EnergyToday | None) -> float | None:
    """Heat divided by electrical energy of the day (Arbeitszahl).

    Only from a current heat value and a complete day of electrical energy;
    otherwise the ratio would mislead.
    """
    if heat is None or heat.status is not ValueStatus.OK or heat.kwh is None:
        return None
    if electricity is None or electricity.incomplete or electricity.kwh < PERFORMANCE_MIN_KWH:
        return None
    return round(heat.kwh / electricity.kwh, 2)


class HomeAssistant:
    def __init__(
        self,
        settings: SettingsStore,
        state: StateHub,
        energy: EnergyMeter,
        log: LogStore,
        client: HomeAssistantClient | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._state = state
        self._log = log
        self._client = client
        self._clock = clock
        self._history: tuple[datetime, PowerHistory] | None = None
        self.importer = HomeAssistantImport(settings, available=client is not None, on_change=state.republish)
        self.hems_sources = HemsSources(settings)
        self.exporter = (
            HomeAssistantExport(client, state, energy, self.importer, settings) if client is not None else None
        )
        # With the connection myVAILLANT the heat pump's state comes from Home Assistant, too.
        self.device = state if isinstance(state, MyVaillantState) else None
        if client is not None:
            client.add_listener(self.importer)
            client.add_listener(self.hems_sources)
            if self.device is not None:
                client.add_listener(self.device)
            if self.exporter is not None:
                client.add_listener(self.exporter)
        self._drop_own_sources()
        self.watch()

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> HomeAssistantClient | None:
        return self._client

    def watch(self) -> None:
        """Follow the entities of the current settings: rooms, heat, the energy management's inputs and
        the myVAILLANT entities."""
        if self._client is not None:
            device = self.device.watched() if self.device is not None else frozenset()
            self._client.watch(self.importer.watched() | self.hems_sources.watched() | device)

    def entity_state(self, entity_id: str | None) -> EntityState | None:
        return self.hems_sources.get(entity_id)

    async def history(self, entity_id: str, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
        """Recorded states of any entity, read a day at a time to keep the answers small."""
        if self._client is None:
            raise HomeAssistantUnavailable("no Supervisor token")
        changes: list[tuple[datetime, str]] = []
        moment = start
        while moment < end:
            until = min(end, moment + timedelta(days=1))
            changes += await self._client.history(entity_id, moment, until)
            moment = until
        return sorted(set(changes))

    @property
    def settings(self) -> Settings:
        return self._settings.current

    def snapshot(self) -> HomeAssistantState:
        return self.importer.snapshot()

    def exported(self) -> list[str]:
        return self.exporter.exported() if self.exporter is not None else []

    async def run(self) -> None:
        if self._client is None or self.exporter is None:
            return
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._client.run())
            tasks.create_task(self.exporter.run())

    async def entities(self, kind: str, query: str) -> list[EntityChoice]:
        """Entities to choose from: rooms, energy for the heat of the day, the myVAILLANT entities and
        the energy management's inputs (thermostats, humidity, valves, weather)."""
        if self._client is None:
            raise HomeAssistantUnavailable("no Supervisor token")
        needle = query.strip().lower()
        choices = []
        for entity in await self._client.states():
            if not _is_kind(entity, kind):
                continue
            unit = entity.attributes.get("unit_of_measurement")
            name = str(entity.attributes.get("friendly_name") or entity.entity_id)
            if needle and needle not in entity.entity_id.lower() and needle not in name.lower():
                continue
            choices.append(
                EntityChoice(
                    entity_id=entity.entity_id,
                    name=name,
                    state=entity.state,
                    unit=str(unit) if unit is not None else None,
                )
            )
        choices.sort(key=lambda choice: (choice.name.lower(), choice.entity_id))
        return choices[:MAX_CHOICES]

    async def discover_myvaillant(self) -> MyVaillantEntities:
        """The myVAILLANT integration's entities found in Home Assistant, as a suggestion."""
        if self._client is None:
            raise HomeAssistantUnavailable("no Supervisor token")
        return discover(await self._client.states())

    def change_myvaillant(self, entities: MyVaillantEntities) -> Settings:
        """Save the chosen myVAILLANT entities and follow them at once."""
        before = self._settings.current
        after = self._settings.update(lambda settings: replace(settings, myvaillant=entities))
        if after.myvaillant != before.myvaillant:
            chosen = after.myvaillant
            parts = [
                f"Zonen {', '.join(chosen.zones) or 'keine'}",
                f"Warmwasser {chosen.hot_water or 'keins'}",
                f"Außentemperatur {chosen.outdoor_temperature or 'keine'}",
                f"Leistung {chosen.power or 'keine'}",
            ]
            self._log.add(LogCategory.SETTINGS, f"myVAILLANT-Entitäten: {'; '.join(parts)}.")
        if self.device is not None:
            self.device.settings_changed()
        self.watch()
        return after

    async def myvaillant_zone_labels(self, zones: Sequence[str]) -> dict[str, str]:
        """The names the app gives the chosen myVAILLANT zones, e.g. "Zone 1"."""
        if self._client is None:
            return {}
        try:
            states = {state.entity_id: state for state in await self._client.states()}
        except HomeAssistantUnavailable as err:
            _LOGGER.warning("Reading the myVAILLANT zones' names failed: %s", err)
            return {}
        return {zone: zone_label(states.get(zone), position) for position, zone in enumerate(zones, start=1)}

    def switch_rooms(self, old_labels: Mapping[str, str], new_labels: Mapping[str, str]) -> None:
        """Before the connection changes: each room takes its zone for the new connection.

        The zones have other IDs with EEBUS and myVAILLANT. A room keeps its zone for
        the old connection and gets back the one it had with the new connection; a room
        assigned only with the old one gets the zone of the same name (e.g. "Zone 1").
        """
        by_label: dict[str, list[str]] = {}
        for zone_id, label in new_labels.items():
            by_label.setdefault(label, []).append(zone_id)

        def switched(room: Room) -> Room:
            zone_id = room.other_zone_id
            if zone_id is None and room.zone_id is not None:
                same = by_label.get(old_labels.get(room.zone_id, ""), [])
                zone_id = same[0] if len(same) == 1 else None
            return replace(room, zone_id=zone_id, other_zone_id=room.zone_id)

        after = self._settings.update(
            lambda settings: replace(settings, rooms=tuple(switched(room) for room in settings.rooms))
        )
        if after.rooms:
            described = ", ".join(
                f"{room.name} ({new_labels.get(room.zone_id, room.zone_id) if room.zone_id else 'keine Zone'})"
                for room in after.rooms
            )
            self._log.add(LogCategory.SETTINGS, f"Räume für die neue Anbindung: {described}")

    def _drop_own_sources(self) -> None:
        """Forget one of the app's own sensors chosen as a myVAILLANT source.

        Version 1.2.0 suggested sensor.vaillant_eebus_outdoor_temperature: the app
        then read back what it reported itself, and the value never changed.
        """
        chosen = self._settings.current.myvaillant
        own = [entity for entity in (chosen.outdoor_temperature, chosen.power) if entity and is_own_entity(entity)]
        if not own:
            return
        self._settings.update(
            lambda settings: replace(
                settings,
                myvaillant=replace(
                    settings.myvaillant,
                    outdoor_temperature=_unless_own(settings.myvaillant.outdoor_temperature),
                    power=_unless_own(settings.myvaillant.power),
                ),
            )
        )
        _LOGGER.warning("Dropped the app's own sensors as myVAILLANT sources: %s", ", ".join(own))
        self._log.add(
            LogCategory.SETTINGS,
            f"myVAILLANT: {', '.join(own)} entfernt. Das ist ein eigener Sensor der App, sein Wert käme nur von "
            "ihr selbst zurück. Bitte unter Einstellungen → Anbindung den Sensor der myVAILLANT-Integration wählen.",
            problem=True,
        )
        if self.device is not None:
            self.device.settings_changed()

    def change_connection(self, connection: Connection) -> Settings:
        """Save the chosen connection; a new one takes effect when the app has restarted."""
        before = self._settings.current.connection
        after = self._settings.update(lambda settings: replace(settings, connection=connection))
        if before is not connection:
            names = {Connection.EEBUS: "EEBUS", Connection.MYVAILLANT: "myVAILLANT"}
            self._log.add(LogCategory.SETTINGS, f"Anbindung: {names[connection]}.")
        return after

    async def location(self) -> tuple[float, float] | None:
        """Home Assistant's home location, to fill in the coordinates for Open-Meteo."""
        if self._client is None:
            return None
        return await self._client.location()

    async def power_history(self) -> PowerHistory:
        """The last 24 hours of power as five-minute means, from Home Assistant's history."""
        if self._client is None:
            return PowerHistory.missing(HistoryGap.NO_HOME_ASSISTANT)
        if ExportGroup.ENERGY not in self._settings.current.export:
            return PowerHistory.missing(HistoryGap.NOT_EXPORTED)
        now = self._clock()
        if self._history is not None and now - self._history[0] < HISTORY_CACHE:
            return self._history[1]
        try:
            changes = await self._client.history(POWER_SENSOR, first_bucket(now), now)
        except HomeAssistantUnavailable as err:
            _LOGGER.warning("Reading the power history failed: %s", err)
            return PowerHistory.missing(HistoryGap.UNREACHABLE)
        history = bucket_means(changes, now)
        self._history = (now, history)
        return history

    def change(
        self, rooms: tuple[Room, ...], heat_energy_entities: tuple[str, ...], export: frozenset[ExportGroup]
    ) -> Settings:
        """Save the user's Home Assistant choices and follow them at once."""
        before = self._settings.current
        # The UI edits the zones of the current connection; each room keeps its zone for the other one
        # and what the user entered for it on the page "Räume".
        known = {room.entity_id: room for room in before.rooms}
        rooms = tuple(
            replace(room, other_zone_id=known[room.entity_id].other_zone_id, details=known[room.entity_id].details)
            if room.entity_id in known
            else room
            for room in rooms
        )
        after = self._settings.update(
            lambda settings: replace(settings, rooms=rooms, heat_energy_entities=heat_energy_entities, export=export)
        )
        if after.rooms != before.rooms:
            self._log.add(LogCategory.SETTINGS, f"Räume geändert: {self._describe_rooms(after.rooms)}")
        if after.heat_energy_entities != before.heat_energy_entities:
            sensors = " + ".join(after.heat_energy_entities) or "keine"
            self._log.add(LogCategory.SETTINGS, f"Wärme heute: {sensors}")
        if after.export != before.export:
            groups = [name for group, name in _EXPORT_NAMES.items() if group in after.export]
            self._log.add(LogCategory.SETTINGS, f"Export nach Home Assistant: {', '.join(groups) or 'nichts'}")
        self.watch()
        if self.exporter is not None:
            self.exporter.refresh()
        self._state.republish()
        return after

    def change_room(self, entity_id: str, details: RoomDetails) -> Room:
        """Save what the user entered for one room (page "Räume"); KeyError for an unknown room."""
        before = self._settings.current
        room = next((room for room in before.rooms if room.entity_id == entity_id), None)
        if room is None:
            raise KeyError(entity_id)
        if room.details == details:
            return room
        changed = replace(room, details=details)
        self._settings.update(
            lambda settings: replace(
                settings, rooms=tuple(changed if item.entity_id == entity_id else item for item in settings.rooms)
            )
        )
        self._log.add(LogCategory.SETTINGS, f"Raumdaten geändert: {room.name}.")
        self.watch()
        return changed

    def _describe_rooms(self, rooms: tuple[Room, ...]) -> str:
        heat_pump = self._state.current
        labels = {zone.id: zone.label for zone in heat_pump.zones} if heat_pump is not None else {}
        if not rooms:
            return "keine"
        return ", ".join(
            f"{room.name} ({labels.get(room.zone_id, room.zone_id) if room.zone_id else 'keine Zone'})"
            for room in rooms
        )
