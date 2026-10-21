"""States of the Home Assistant entities the planner reads.

Prices, the PV forecast, the house's power and the battery's state of charge,
followed over the client's subscription like the rooms. New prices or a new
forecast wake the planner; the power values change every few seconds and are
only read when it plans.
"""

from __future__ import annotations

from collections.abc import Callable

from vaillant_eebus.ha_client import EntityState, HomeAssistantListener
from vaillant_eebus.hems.config import TariffKind
from vaillant_eebus.settings import SettingsStore


class HemsEntities(HomeAssistantListener):
    def __init__(self, settings: SettingsStore) -> None:
        self._settings = settings
        self._states: dict[str, EntityState] = {}
        self._connected = False
        self._listeners: list[Callable[[], None]] = []

    @property
    def connected(self) -> bool:
        return self._connected

    def watched(self) -> frozenset[str]:
        return self._settings.current.hems.entities()

    def get(self, entity_id: str | None) -> EntityState | None:
        return self._states.get(entity_id) if entity_id else None

    def on_forecast_change(self, listener: Callable[[], None]) -> None:
        """Call listener when prices or the PV forecast change."""
        self._listeners.append(listener)

    # --- HomeAssistantListener ----------------------------------------------------

    def ha_connected(self) -> None:
        self._connected = True
        self._notify()

    def ha_disconnected(self) -> None:
        self._connected = False
        self._states.clear()

    def ha_state(self, entity_id: str, state: EntityState | None) -> None:
        previous = self._states.get(entity_id)
        if state is None:
            self._states.pop(entity_id, None)
        else:
            self._states[entity_id] = state
        hems = self._settings.current.hems
        forecasts = {hems.pv_forecast_today, hems.pv_forecast_tomorrow}
        if hems.tariff.kind is TariffKind.DYNAMIC:
            forecasts.add(hems.tariff.price_entity)
        if entity_id in forecasts and (previous is None or state is None or previous.attributes != state.attributes):
            self._notify()

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()
