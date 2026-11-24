"""The Home Assistant states the energy management reads besides the heat pump.

Rooms (temperature, humidity, the thermostat and its valve), the zones' flow
temperatures, the buffer and the heat sensors for learning. Only read; the
values are as fresh as Home Assistant's last report.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from vaillant_eebus.ha_client import EntityState, HomeAssistantListener
from vaillant_eebus.settings import SettingsStore

NO_VALUE = frozenset({"unavailable", "unknown", ""})
_TO_KWH = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}
_TO_W = {"W": 1.0, "kW": 1000.0}


class HemsSources(HomeAssistantListener):
    def __init__(self, settings: SettingsStore) -> None:
        self._settings = settings
        self._states: dict[str, EntityState] = {}
        self._listeners: list[Callable[[str, EntityState | None], None]] = []
        self._on_connected: list[Callable[[], None]] = []
        # Further entities to follow, e.g. the thermostats' last_seen found by the room control.
        self._extra: list[Callable[[], Iterable[str]]] = []
        self.connected = False

    def watched(self) -> frozenset[str]:
        settings = self._settings.current
        rooms: set[str] = set()
        for room in settings.rooms:
            rooms.add(room.entity_id)
            details = room.details
            rooms |= {
                entity
                for entity in (details.trv_entity, details.valve_entity, details.humidity_entity, details.window_entity)
                if entity
            }
        probe = settings.room_control.probe
        pair = {entity for entity in (probe.flow_entity, probe.return_entity) if entity}
        extra = {entity for provider in self._extra for entity in provider()}
        return frozenset(rooms) | settings.hems.entities() | pair | extra

    def follow(self, provider: Callable[[], Iterable[str]]) -> None:
        """Follow these entities too (read at each change of the watched set)."""
        self._extra.append(provider)

    def on_change(self, listener: Callable[[str, EntityState | None], None]) -> None:
        self._listeners.append(listener)

    def on_connected(self, listener: Callable[[], None]) -> None:
        """Called when Home Assistant is connected (again) and the watched states are read."""
        self._on_connected.append(listener)

    def get(self, entity_id: str | None) -> EntityState | None:
        return self._states.get(entity_id) if entity_id is not None else None

    def number(self, entity_id: str | None) -> float | None:
        """The entity's state as a number; None if it has none."""
        state = self.get(entity_id)
        if state is None or state.state in NO_VALUE:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    def attribute(self, entity_id: str | None, name: str) -> float | None:
        state = self.get(entity_id)
        if state is None or state.state in NO_VALUE:
            return None
        value = state.attributes.get(name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value) if math.isfinite(value) else None

    def kwh_factor(self, entity_id: str | None) -> float | None:
        state = self.get(entity_id)
        return _TO_KWH.get(str(state.attributes.get("unit_of_measurement"))) if state is not None else None

    def watt_factor(self, entity_id: str | None) -> float | None:
        state = self.get(entity_id)
        return _TO_W.get(str(state.attributes.get("unit_of_measurement"))) if state is not None else None

    # --- HomeAssistantListener ---------------------------------------------------

    def ha_connected(self) -> None:
        self.connected = True
        for listener in self._on_connected:
            listener()

    def ha_disconnected(self) -> None:
        self.connected = False
        self._states.clear()

    def ha_state(self, entity_id: str, state: EntityState | None) -> None:
        if entity_id not in self.watched():
            return
        previous = self._states.get(entity_id)
        if state is None:
            self._states.pop(entity_id, None)
        else:
            self._states[entity_id] = state
        if (
            previous is None
            or state is None
            or (previous.state, previous.attributes) != (state.state, state.attributes)
        ):
            for listener in self._listeners:
                listener(entity_id, state)
