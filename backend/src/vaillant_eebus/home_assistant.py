"""Everything the app does with Home Assistant, behind one object for main and api.

Outside Home Assistant (no Supervisor token) there is no client: the import
reports itself unavailable and nothing is exported.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from vaillant_eebus.energy import EnergyMeter, EnergyToday
from vaillant_eebus.ha_client import EntityState, HomeAssistantClient, HomeAssistantUnavailable
from vaillant_eebus.ha_export import PREFIX, HomeAssistantExport
from vaillant_eebus.ha_import import HeatToday, HomeAssistantImport, HomeAssistantState, ValueStatus
from vaillant_eebus.hems.inputs import HemsEntities
from vaillant_eebus.history import HistoryGap, PowerHistory, bucket_means, first_bucket
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.settings import ExportGroup, Room, Settings, SettingsStore
from vaillant_eebus.state import StateService

_LOGGER = logging.getLogger(__name__)

# Below this much electrical energy a day's ratio says nothing yet (early morning).
PERFORMANCE_MIN_KWH = 0.5
# The exported power sensor whose history feeds the chart (group "Leistung und Energie").
POWER_SENSOR = f"sensor.{PREFIX}power"
# The history is read from Home Assistant at most this often; several open pages share it.
HISTORY_CACHE = timedelta(seconds=60)

# Units that make a sensor a candidate for a room, the heat of the day, the house's power or the battery.
_KIND_UNITS = {
    "temperature": frozenset({"°C", "°F"}),
    "energy": frozenset({"Wh", "kWh", "MWh"}),
    "power": frozenset({"W", "kW"}),
    "battery": frozenset({"%"}),
}
# Kinds recognized by an attribute: epex_spot prices and open_meteo_solar_forecast forecasts.
_KIND_ATTRIBUTES = {"price": ("data", list), "pv_forecast": ("watts", dict)}
ENTITY_KINDS = (*_KIND_UNITS, *_KIND_ATTRIBUTES, "weather")
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


def _is_kind(entity: EntityState, kind: str) -> bool:
    if kind == "weather":
        return entity.entity_id.startswith("weather.")
    if not entity.entity_id.startswith("sensor.") or entity.entity_id.startswith(f"sensor.{PREFIX}"):
        return False
    if kind in _KIND_ATTRIBUTES:
        attribute, kind_type = _KIND_ATTRIBUTES[kind]
        return isinstance(entity.attributes.get(attribute), kind_type)
    return entity.attributes.get("unit_of_measurement") in _KIND_UNITS[kind]


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
        state: StateService,
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
        self.hems_entities = HemsEntities(settings)
        self.exporter = (
            HomeAssistantExport(client, state, energy, self.importer, settings) if client is not None else None
        )
        if client is not None:
            client.add_listener(self.importer)
            client.add_listener(self.hems_entities)
            if self.exporter is not None:
                client.add_listener(self.exporter)
        self.watch()

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> HomeAssistantClient | None:
        return self._client

    def watch(self) -> None:
        """Follow the entities of the current settings: rooms, heat and the planner's inputs."""
        if self._client is not None:
            self._client.watch(self.importer.watched() | self.hems_entities.watched())

    def entity_state(self, entity_id: str | None) -> EntityState | None:
        return self.hems_entities.get(entity_id)

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
        """Entities to choose from: temperatures for rooms, energy for the heat of the day, and the
        planner's inputs (power, battery, prices, PV forecast, weather)."""
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

    def _describe_rooms(self, rooms: tuple[Room, ...]) -> str:
        heat_pump = self._state.current
        labels = {zone.id: zone.label for zone in heat_pump.zones} if heat_pump is not None else {}
        if not rooms:
            return "keine"
        return ", ".join(
            f"{room.name} ({labels.get(room.zone_id, room.zone_id) if room.zone_id else 'keine Zone'})"
            for room in rooms
        )
