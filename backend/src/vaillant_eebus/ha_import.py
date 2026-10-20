"""Values the app takes from Home Assistant (spec 7.3).

The user chooses temperature sensors for rooms and assigns them to zones; the
average of a zone's valid rooms stands in for the gateway's room temperature,
which is unreliable on the real installation. Optionally a sensor with the
heat produced today. A value counts only while it is a number in a known unit,
not unavailable and reported within the last 30 minutes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from vaillant_eebus.ha_client import EntityState, HomeAssistantListener
from vaillant_eebus.settings import SettingsStore

# Spec 7.3: older room values are marked and left out of the average.
STALE_AFTER = timedelta(minutes=30)
# Energy sensors of cloud integrations report less often than room sensors.
HEAT_STALE_AFTER = timedelta(hours=3)
# Seconds between two notifications of a change, so busy sensors do not flood the UI.
NOTIFY_EVERY = 2.0

_TO_CELSIUS: dict[str, Callable[[float], float]] = {
    "°C": lambda value: value,
    "°F": lambda value: (value - 32) * 5 / 9,
}
_TO_KWH: dict[str, float] = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}


class ValueStatus(StrEnum):
    OK = "ok"
    # Reported, but longer ago than allowed.
    STALE = "stale"
    # Unknown entity, unavailable, not a number or an unknown unit.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RoomReading:
    entity_id: str
    name: str
    zone_id: str | None
    celsius: float | None
    status: ValueStatus
    reported_at: datetime | None


@dataclass(frozen=True, slots=True)
class ZoneAverage:
    zone_id: str
    # None when no room of the zone has a valid value.
    celsius: float | None
    # Rooms in the average and rooms assigned to the zone.
    rooms: int
    of: int


@dataclass(frozen=True, slots=True)
class HeatToday:
    """The heat of the day: the sum of the chosen sensors, e.g. heating and hot water."""

    entity_ids: list[str]
    # None unless every sensor has a value: a partial sum would understate the day.
    kwh: float | None
    # The worst status among the sensors.
    status: ValueStatus
    # The oldest report among the sensors.
    reported_at: datetime | None


@dataclass(frozen=True, slots=True)
class HomeAssistantState:
    # False outside Home Assistant (no Supervisor token), e.g. in development.
    available: bool
    connected: bool
    rooms: list[RoomReading]
    zones: list[ZoneAverage]
    heat_today: HeatToday | None


class HomeAssistantImport(HomeAssistantListener):
    def __init__(
        self,
        settings: SettingsStore,
        *,
        available: bool,
        on_change: Callable[[], None] = lambda: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._available = available
        self._on_change = on_change
        self._clock = clock
        self._states: dict[str, EntityState] = {}
        self._connected = False
        self._last_notified = 0.0
        self._notify_pending = False

    def watched(self) -> frozenset[str]:
        """The entities whose states the app needs."""
        settings = self._settings.current
        return frozenset({room.entity_id for room in settings.rooms} | set(settings.heat_energy_entities))

    def snapshot(self) -> HomeAssistantState:
        now = self._clock()
        settings = self._settings.current
        rooms = [self._room(room.entity_id, room.name, room.zone_id, now) for room in settings.rooms]
        zone_ids = sorted({room.zone_id for room in settings.rooms if room.zone_id is not None})
        zones = []
        for zone_id in zone_ids:
            assigned = [room for room in rooms if room.zone_id == zone_id]
            valid = [room.celsius for room in assigned if room.status is ValueStatus.OK and room.celsius is not None]
            zones.append(
                ZoneAverage(
                    zone_id=zone_id,
                    celsius=round(sum(valid) / len(valid), 2) if valid else None,
                    rooms=len(valid),
                    of=len(assigned),
                )
            )
        heat = self._heat(settings.heat_energy_entities, now) if settings.heat_energy_entities else None
        return HomeAssistantState(
            available=self._available, connected=self._connected, rooms=rooms, zones=zones, heat_today=heat
        )

    def zone_average(self, zone_id: str) -> ZoneAverage | None:
        return next((zone for zone in self.snapshot().zones if zone.zone_id == zone_id), None)

    # --- HomeAssistantListener ----------------------------------------------------

    def ha_connected(self) -> None:
        self._connected = True
        self._notify()

    def ha_disconnected(self) -> None:
        # Without the connection nothing tells whether the values still hold.
        self._connected = False
        self._states.clear()
        self._notify()

    def ha_state(self, entity_id: str, state: EntityState | None) -> None:
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
            self._notify()

    # --- internals -------------------------------------------------------------

    def _room(self, entity_id: str, name: str, zone_id: str | None, now: datetime) -> RoomReading:
        state = self._states.get(entity_id)
        value = _number(state)
        convert = _TO_CELSIUS.get(str(state.attributes.get("unit_of_measurement"))) if state else None
        celsius = round(convert(value), 2) if convert is not None and value is not None else None
        reported_at = state.last_reported if state else None
        return RoomReading(
            entity_id=entity_id,
            name=name,
            zone_id=zone_id,
            celsius=celsius,
            status=_status(celsius, reported_at, now, STALE_AFTER),
            reported_at=reported_at,
        )

    def _heat(self, entity_ids: tuple[str, ...], now: datetime) -> HeatToday:
        parts: list[float | None] = []
        statuses: list[ValueStatus] = []
        reports: list[datetime] = []
        for entity_id in entity_ids:
            state = self._states.get(entity_id)
            value = _number(state)
            factor = _TO_KWH.get(str(state.attributes.get("unit_of_measurement"))) if state else None
            kwh = value * factor if factor is not None and value is not None else None
            reported_at = state.last_reported if state else None
            parts.append(kwh)
            statuses.append(_status(kwh, reported_at, now, HEAT_STALE_AFTER))
            if reported_at is not None:
                reports.append(reported_at)
        complete = [part for part in parts if part is not None]
        return HeatToday(
            entity_ids=list(entity_ids),
            kwh=round(sum(complete), 3) if len(complete) == len(parts) else None,
            status=max(statuses, key=_SEVERITY.__getitem__),
            reported_at=min(reports) if len(reports) == len(parts) else None,
        )

    def _notify(self) -> None:
        """Tell the app about a change, at most every few seconds."""
        if self._notify_pending:
            return
        wait = self._last_notified + NOTIFY_EVERY - time.monotonic()
        if wait <= 0:
            self._last_notified = time.monotonic()
            self._on_change()
            return
        self._notify_pending = True

        def later() -> None:
            self._notify_pending = False
            self._last_notified = time.monotonic()
            self._on_change()

        asyncio.get_running_loop().call_later(wait, later)


_SEVERITY = {ValueStatus.OK: 0, ValueStatus.STALE: 1, ValueStatus.UNAVAILABLE: 2}


def _number(state: EntityState | None) -> float | None:
    if state is None:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def _status(value: float | None, reported_at: datetime | None, now: datetime, stale_after: timedelta) -> ValueStatus:
    if value is None:
        return ValueStatus.UNAVAILABLE
    if reported_at is None or now - reported_at > stale_after:
        return ValueStatus.STALE
    return ValueStatus.OK
