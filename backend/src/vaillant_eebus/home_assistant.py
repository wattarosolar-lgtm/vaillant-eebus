"""Everything the app does with Home Assistant, behind one object for main and api.

Outside Home Assistant (no Supervisor token) there is no client: the import
reports itself unavailable and nothing is exported.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from vaillant_eebus.energy import EnergyMeter, EnergyToday
from vaillant_eebus.ha_client import HomeAssistantClient, HomeAssistantUnavailable
from vaillant_eebus.ha_export import PREFIX, HomeAssistantExport
from vaillant_eebus.ha_import import HeatToday, HomeAssistantImport, HomeAssistantState, ValueStatus
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.settings import ExportGroup, Room, Settings, SettingsStore
from vaillant_eebus.state import StateService

# Below this much electrical energy a day's ratio says nothing yet (early morning).
PERFORMANCE_MIN_KWH = 0.5

# Units that make an entity a candidate for a room or for the heat of the day.
_KIND_UNITS = {"temperature": frozenset({"°C", "°F"}), "energy": frozenset({"Wh", "kWh", "MWh"})}
MAX_CHOICES = 200

_EXPORT_NAMES = {
    ExportGroup.STATUS: "Status",
    ExportGroup.ZONES: "Zonen",
    ExportGroup.HOT_WATER: "Warmwasser",
    ExportGroup.TEMPERATURES: "Temperaturen",
    ExportGroup.ENERGY: "Leistung und Energie",
    ExportGroup.POWER_LIMITS: "Leistungsdeckel",
    ExportGroup.COMPRESSOR: "Verdichter-Flexibilität",
}


@dataclass(frozen=True, slots=True)
class EntityChoice:
    entity_id: str
    name: str
    state: str
    unit: str | None


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
    ) -> None:
        self._settings = settings
        self._state = state
        self._log = log
        self._client = client
        self.importer = HomeAssistantImport(settings, available=client is not None, on_change=state.republish)
        self.exporter = (
            HomeAssistantExport(client, state, energy, self.importer, settings) if client is not None else None
        )
        if client is not None:
            client.add_listener(self.importer)
            if self.exporter is not None:
                client.add_listener(self.exporter)
            client.watch(self.importer.watched())

    @property
    def available(self) -> bool:
        return self._client is not None

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
        """Sensors to choose from: temperatures for rooms, energy for the heat of the day."""
        if self._client is None:
            raise HomeAssistantUnavailable("no Supervisor token")
        units = _KIND_UNITS[kind]
        needle = query.strip().lower()
        choices = []
        for entity in await self._client.states():
            unit = entity.attributes.get("unit_of_measurement")
            if not entity.entity_id.startswith("sensor.") or entity.entity_id.startswith(f"sensor.{PREFIX}"):
                continue
            if unit not in units:
                continue
            name = str(entity.attributes.get("friendly_name") or entity.entity_id)
            if needle and needle not in entity.entity_id.lower() and needle not in name.lower():
                continue
            choices.append(EntityChoice(entity_id=entity.entity_id, name=name, state=entity.state, unit=str(unit)))
        choices.sort(key=lambda choice: (choice.name.lower(), choice.entity_id))
        return choices[:MAX_CHOICES]

    def change(
        self, rooms: tuple[Room, ...], heat_energy_entity: str | None, export: frozenset[ExportGroup]
    ) -> Settings:
        """Save the user's Home Assistant choices and follow them at once."""
        before = self._settings.current
        after = self._settings.update(
            lambda settings: replace(settings, rooms=rooms, heat_energy_entity=heat_energy_entity, export=export)
        )
        if after.rooms != before.rooms:
            self._log.add(LogCategory.SETTINGS, f"Räume geändert: {self._describe_rooms(after.rooms)}")
        if after.heat_energy_entity != before.heat_energy_entity:
            self._log.add(LogCategory.SETTINGS, f"Wärmemenge heute: {after.heat_energy_entity or 'keine'}")
        if after.export != before.export:
            groups = [name for group, name in _EXPORT_NAMES.items() if group in after.export]
            self._log.add(LogCategory.SETTINGS, f"Export nach Home Assistant: {', '.join(groups) or 'nichts'}")
        if self._client is not None:
            self._client.watch(self.importer.watched())
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
