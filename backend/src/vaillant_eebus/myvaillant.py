"""The connection myVAILLANT: the heat pump through the integration's entities in Home Assistant.

The myVAILLANT integration (signalkraft/mypyllant-component) talks to the
Vaillant cloud. MyVaillantState builds the app's HeatPumpState from the
entities the user chose - a climate entity per zone, the water heater, the
outdoor temperature and optionally a power sensor - so overview, protocol,
export, energy meter and planner work as with EEBUS. MyVaillantLink carries
the write gate's commands out as service calls on exactly those entities;
only the write gate calls it.

What the integration offers (source of v0.9.19):

- climate: hvac_mode "off", "heat_cool" (Vaillant's manual mode, the app's
  "on") and "auto" (time program); preset "boost" is a quick veto, the
  attribute quick_veto_end_date_time says until when. set_temperature sets the
  manual setpoint in heat_cool and otherwise starts a quick veto with the
  integration's default duration; mypyllant.set_quick_veto takes the duration
  in hours (at least 1), mypyllant.cancel_quick_veto ends it.
- water_heater: operations "Manual", "Time Controlled", "Off" and "Cylinder
  Boost" (the one-time charge, which hides the mode while it runs); the
  setpoint is cut to whole degrees.
- The values are as old as the integration's last poll of the cloud (every
  30 min by default) and are read again a few seconds after every service call.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from vaillant_eebus.ha_client import EntityState, HomeAssistantClient, HomeAssistantListener
from vaillant_eebus.pairing import ConnectionState
from vaillant_eebus.settings import MYVAILLANT_ZONES_MAX, Connection, MyVaillantEntities, SettingsStore
from vaillant_eebus.state import (
    COMPRESSOR_ACTIVE_ABOVE_W,
    UNAVAILABLE,
    HeatPumpState,
    HotWater,
    Reading,
    Setpoint,
    StateHub,
    Zone,
)
from vaillant_eebus.write_gate import (
    Command,
    EndZoneOverride,
    SetHotWaterBoost,
    SetHotWaterMode,
    SetHotWaterSetpoint,
    SetZoneMode,
    SetZoneOverride,
    SetZoneSetpoint,
    WriteRejected,
)

_LOGGER = logging.getLogger(__name__)

SOURCE = "myvaillant"
# Entity states without a value.
NO_VALUE = frozenset({"unavailable", "unknown", ""})
# The operation modes in the order the UI shows them.
MODE_ORDER = ("off", "on", "auto")
# hvac_mode of a zone's climate entity -> the app's operation mode.
_ZONE_MODES = {"off": "off", "heat_cool": "on", "heat": "on", "auto": "auto"}
# Vaillant's names of the hot water operations (also of the VRC700) -> the app's operation mode.
_DHW_MODES = {"MANUAL": "on", "DAY": "on", "TIME_CONTROLLED": "auto", "AUTO": "auto", "OFF": "off"}
_BOOST = "CYLINDER_BOOST"
_DHW_FALLBACK = {"on": "Manual", "auto": "Time Controlled", "off": "Off"}
# Zone setpoints Vaillant accepts (a quick veto takes 0 to 30 °C).
ZONE_LIMITS = (5.0, 30.0)
DHW_LIMITS = (35.0, 70.0)
# The state is handed on at least this often, so the energy meter counts on.
REPUBLISH_EVERY = 60.0

_OUTDOOR_NAMES = ("outdoor_temperature", "aussentemperatur", "auszentemperatur")
_POWER_NAMES = ("current_power", "aktuelle_leistung")


# --- finding the integration's entities ----------------------------------------


def is_zone(entity: EntityState) -> bool:
    """A zone's climate entity of the myVAILLANT integration (it reports a quick veto and a time program)."""
    return entity.entity_id.startswith("climate.") and (
        "quick_veto_end_date_time" in entity.attributes or "time_program_heating" in entity.attributes
    )


def is_water_heater(entity: EntityState) -> bool:
    operations = entity.attributes.get("operation_list")
    return entity.entity_id.startswith("water_heater.") and (
        "time_program_dhw" in entity.attributes
        or (isinstance(operations, list) and any(_name(operation) == _BOOST for operation in operations))
    )


def _is_outdoor(entity: EntityState) -> bool:
    return (
        entity.entity_id.startswith("sensor.")
        and entity.attributes.get("unit_of_measurement") == "°C"
        and any(name in entity.entity_id for name in _OUTDOOR_NAMES)
    )


def _is_power(entity: EntityState) -> bool:
    return (
        entity.entity_id.startswith("sensor.")
        and entity.attributes.get("unit_of_measurement") in ("W", "kW")
        and any(name in entity.entity_id for name in _POWER_NAMES)
    )


def discover(states: Iterable[EntityState]) -> MyVaillantEntities:
    """The myVAILLANT entities found in Home Assistant, as a suggestion for the user."""
    ordered = sorted(states, key=lambda entity: entity.entity_id)
    return MyVaillantEntities(
        zones=tuple(entity.entity_id for entity in ordered if is_zone(entity))[:MYVAILLANT_ZONES_MAX],
        hot_water=next((entity.entity_id for entity in ordered if is_water_heater(entity)), None),
        outdoor_temperature=next((entity.entity_id for entity in ordered if _is_outdoor(entity)), None),
        power=next((entity.entity_id for entity in ordered if _is_power(entity)), None),
    )


def zone_label(state: EntityState | None, position: int) -> str:
    """A short zone name from the entity's name, e.g. "Zuhause Zone 1 (Circuit 0) Climate" -> "Zone 1"."""
    name = str(state.attributes.get("friendly_name") or "") if state is not None else ""
    name = re.sub(r"\s*\(Circuit \d+\)", "", name)
    name = re.sub(r"\s+Climate$", "", name, flags=re.IGNORECASE).strip()
    zone = re.search(r"\bZone\b.*", name)
    if zone is not None and zone.start() > 0:
        name = zone.group(0)
    return name or f"Zone {position}"


# --- reading ------------------------------------------------------------------


class MyVaillantState(StateHub, HomeAssistantListener):
    """The heat pump's state from the chosen entities of the myVAILLANT integration."""

    def __init__(self, settings: SettingsStore, *, available: bool, republish_every: float = REPUBLISH_EVERY) -> None:
        """available is False outside Home Assistant (no Supervisor token)."""
        super().__init__()
        self._settings = settings
        self._available = available
        self._republish_every = republish_every
        self._entities: dict[str, EntityState] = {}
        self._ha_connected = False
        # The hot water's mode, kept while a one-time charge hides it.
        self._dhw_mode: str | None = None
        self._rebuild()

    @property
    def chosen(self) -> MyVaillantEntities:
        return self._settings.current.myvaillant

    def watched(self) -> frozenset[str]:
        return self.chosen.all()

    def entity(self, entity_id: str | None) -> EntityState | None:
        return self._entities.get(entity_id) if entity_id is not None else None

    def connection_state(self) -> ConnectionState:
        if not self.chosen.configured:
            return ConnectionState.NOT_CONFIGURED
        if not self._available or not self._ha_connected:
            return ConnectionState.HA_UNAVAILABLE
        heat_pump = self._state
        if heat_pump is None or not heat_pump.connected:
            return ConnectionState.DISCONNECTED
        return ConnectionState.CONNECTED

    def settings_changed(self) -> None:
        """The user chose other entities: forget the rest and show the new choice."""
        watched = self.watched()
        self._entities = {entity_id: state for entity_id, state in self._entities.items() if entity_id in watched}
        self._rebuild()

    async def run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._republish_every):
                    await self._dirty.wait()
            self._dirty.clear()
            self._rebuild()

    # --- HomeAssistantListener ---------------------------------------------------

    def ha_connected(self) -> None:
        self._ha_connected = True
        self._rebuild()

    def ha_disconnected(self) -> None:
        self._ha_connected = False
        self._rebuild()

    def ha_state(self, entity_id: str, state: EntityState | None) -> None:
        if entity_id not in self.watched():
            return
        if state is None:
            self._entities.pop(entity_id, None)
        else:
            self._entities[entity_id] = state
        self._rebuild()

    # --- internals -------------------------------------------------------------

    def _rebuild(self) -> None:
        self._publish(self._build())

    def _build(self) -> HeatPumpState | None:
        chosen = self.chosen
        if not chosen.configured:
            return None
        if not self._available or not self._ha_connected:
            return UNAVAILABLE
        main = [self._entities.get(entity_id) for entity_id in (*chosen.zones, chosen.hot_water) if entity_id]
        connected = all(state is not None and state.state not in NO_VALUE for state in main)
        reported = [state.last_reported for state in main if state is not None and state.last_reported is not None]
        measurements: dict[str, Reading] = {}
        outdoor = self._reading(chosen.outdoor_temperature)
        if outdoor is not None:
            measurements["outdoor_temperature"] = outdoor
        power = self._power(chosen.power)
        if power is not None:
            measurements["power_consumption"] = power
        hot_water = self._hot_water(chosen.hot_water) if chosen.hot_water is not None else None
        if hot_water is not None and hot_water.temperature is not None:
            measurements["dhw_temperature"] = hot_water.temperature
        return HeatPumpState(
            available=True,
            connected=connected,
            heartbeat_ok=None,
            operating_state=None,
            compressor_active=None if power is None else power.value > COMPRESSOR_ACTIVE_ABOVE_W,
            measurements=measurements,
            zones=[self._zone(entity_id, position) for position, entity_id in enumerate(chosen.zones, start=1)],
            hot_water=hot_water,
            captured_at=min(reported) if reported else None,
        )

    def _zone(self, entity_id: str, position: int) -> Zone:
        state = self._entities.get(entity_id)
        label = zone_label(state, position)
        if state is None or state.state in NO_VALUE:
            return Zone(
                id=entity_id,
                label=label,
                room_temperature=None,
                setpoint=None,
                mode=None,
                available_modes=[],
                mode_writable=False,
            )
        attributes = state.attributes
        mode = _ZONE_MODES.get(state.state)
        offered = {_ZONE_MODES.get(str(hvac)) for hvac in _list(attributes.get("hvac_modes"))}
        available = [name for name in MODE_ORDER if name in offered]
        target = _number(attributes.get("temperature"))
        if target is None:
            target = _number(attributes.get("target_temp_low"))
        setpoint = None
        if target is not None:
            setpoint = Setpoint(
                value=target,
                min=max(ZONE_LIMITS[0], _number(attributes.get("min_temp")) or ZONE_LIMITS[0]),
                max=min(ZONE_LIMITS[1], _number(attributes.get("max_temp")) or ZONE_LIMITS[1]),
                step=_number(attributes.get("target_temp_step")) or 0.5,
                writable=mode is not None,
            )
        current = _number(attributes.get("current_temperature"))
        override = attributes.get("preset_mode") == "boost"
        return Zone(
            id=entity_id,
            label=label,
            room_temperature=(
                Reading(value=current, unit="degC", updated_at=state.last_reported, source=SOURCE)
                if current is not None
                else None
            ),
            setpoint=setpoint,
            mode=mode,
            available_modes=available,
            mode_writable=mode is not None and bool(available),
            override=override,
            override_until=_time(attributes.get("quick_veto_end_date_time")) if override else None,
        )

    def _hot_water(self, entity_id: str) -> HotWater:
        state = self._entities.get(entity_id)
        if state is None or state.state in NO_VALUE:
            return HotWater(
                temperature=None,
                setpoint=None,
                mode=None,
                available_modes=[],
                mode_writable=False,
                boost=None,
                boost_writable=False,
            )
        attributes = state.attributes
        operation = _name(state.state)
        boosting = operation == _BOOST
        if not boosting:
            self._dhw_mode = _DHW_MODES.get(operation)
        operations = [_name(item) for item in _list(attributes.get("operation_list"))]
        offered = {_DHW_MODES.get(item) for item in operations}
        target = _number(attributes.get("temperature"))
        current = _number(attributes.get("current_temperature"))
        return HotWater(
            temperature=(
                Reading(value=current, unit="degC", updated_at=state.last_reported, source=SOURCE)
                if current is not None
                else None
            ),
            setpoint=(
                Setpoint(
                    value=target,
                    min=_number(attributes.get("min_temp")) or DHW_LIMITS[0],
                    max=_number(attributes.get("max_temp")) or DHW_LIMITS[1],
                    # The integration cuts the setpoint to whole degrees.
                    step=1.0,
                    writable=True,
                )
                if target is not None
                else None
            ),
            mode=self._dhw_mode,
            available_modes=[name for name in MODE_ORDER if name in offered],
            mode_writable=bool(offered - {None}),
            boost="running" if boosting else "inactive",
            boost_writable=_BOOST in operations,
        )

    def _reading(self, entity_id: str | None) -> Reading | None:
        state = self.entity(entity_id)
        value = _number(state.state) if state is not None else None
        if state is None or value is None:
            return None
        return Reading(value=value, unit="degC", updated_at=state.last_reported, source=SOURCE)

    def _power(self, entity_id: str | None) -> Reading | None:
        state = self.entity(entity_id)
        value = _number(state.state) if state is not None else None
        if state is None or value is None:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit not in ("W", "kW"):
            return None
        watts = value * 1000 if unit == "kW" else value
        return Reading(value=watts, unit="W", updated_at=state.last_reported, source=SOURCE)


# --- writing ------------------------------------------------------------------


class MyVaillantLink:
    """Carries the write gate's commands out as service calls on the chosen entities."""

    def __init__(self, client: HomeAssistantClient | None, state: MyVaillantState, settings: SettingsStore) -> None:
        self._client = client
        self._state = state
        self._settings = settings

    @property
    def connection(self) -> Connection:
        return Connection.MYVAILLANT

    def check(self, command: Command, heat_pump: HeatPumpState) -> None:
        if self._client is None:
            raise WriteRejected(409, "Ohne Home Assistant erreicht die App myVAILLANT nicht.")
        chosen = self._settings.current.myvaillant
        match command:
            case (
                SetZoneSetpoint(zone_id=zone_id)
                | SetZoneOverride(zone_id=zone_id)
                | EndZoneOverride(zone_id=zone_id)
                | SetZoneMode(zone_id=zone_id)
            ):
                if zone_id not in chosen.zones:
                    raise WriteRejected(404, f"Die Heizzone {zone_id} ist nicht als myVAILLANT-Zone gewählt.")
            case SetHotWaterSetpoint() | SetHotWaterMode() | SetHotWaterBoost() if chosen.hot_water is None:
                raise WriteRejected(409, "Es ist kein Warmwasser von myVAILLANT gewählt.")
            case SetHotWaterBoost(active=False):
                water = heat_pump.hot_water
                if water is not None and water.boost == "running" and water.mode is None:
                    raise WriteRejected(
                        409,
                        "Die Einmalladung lässt sich gerade nicht beenden: Die Betriebsart des Warmwassers ist "
                        "unbekannt. Bitte in myVAILLANT beenden.",
                    )
            case _:
                pass

    async def send_command(self, command: Command) -> None:
        client = self._client
        if client is None:
            raise WriteRejected(409, "Ohne Home Assistant erreicht die App myVAILLANT nicht.")
        hot_water = self._settings.current.myvaillant.hot_water
        match command:
            case SetZoneSetpoint(zone_id=zone_id, celsius=celsius):
                await client.call_device_service("climate", "set_temperature", zone_id, {"temperature": celsius})
            case SetZoneOverride(zone_id=zone_id, celsius=celsius, hours=hours):
                await client.call_device_service(
                    "mypyllant", "set_quick_veto", zone_id, {"temperature": celsius, "duration_hours": hours}
                )
            case EndZoneOverride(zone_id=zone_id):
                await client.call_device_service("mypyllant", "cancel_quick_veto", zone_id, {})
            case SetZoneMode(zone_id=zone_id, mode=mode):
                await client.call_device_service(
                    "climate", "set_hvac_mode", zone_id, {"hvac_mode": self._hvac_mode(zone_id, mode)}
                )
            case SetHotWaterSetpoint(celsius=celsius) if hot_water is not None:
                await client.call_device_service(
                    "water_heater", "set_temperature", hot_water, {"temperature": round(celsius)}
                )
            case SetHotWaterMode(mode=mode) if hot_water is not None:
                await client.call_device_service(
                    "water_heater", "set_operation_mode", hot_water, {"operation_mode": self._operation(mode)}
                )
            case SetHotWaterBoost(active=active) if hot_water is not None:
                water = self._state.current.hot_water if self._state.current is not None else None
                if active:
                    operation = self._operation(_BOOST)
                else:
                    # Choosing the mode again ends the charge.
                    operation = self._operation(water.mode if water is not None and water.mode else "auto")
                await client.call_device_service(
                    "water_heater", "set_operation_mode", hot_water, {"operation_mode": operation}
                )
            case _:
                raise WriteRejected(409, "Diesen Befehl gibt es mit der Anbindung myVAILLANT nicht.")

    def _hvac_mode(self, zone_id: str, mode: str) -> str:
        """The climate entity's own name for the operation mode, e.g. "heat_cool" for "on"."""
        state = self._state.entity(zone_id)
        offered = [str(hvac) for hvac in _list(state.attributes.get("hvac_modes"))] if state is not None else []
        return next((hvac for hvac in offered if _ZONE_MODES.get(hvac) == mode), {"on": "heat_cool"}.get(mode, mode))

    def _operation(self, mode: str) -> str:
        """The water heater's own name of the operation, e.g. "Time Controlled" for "auto"."""
        state = self._state.entity(self._settings.current.myvaillant.hot_water)
        operations = [str(item) for item in _list(state.attributes.get("operation_list"))] if state else []
        for operation in operations:
            name = _name(operation)
            if (mode == _BOOST and name == _BOOST) or _DHW_MODES.get(name) == mode:
                return operation
        return "Cylinder Boost" if mode == _BOOST else _DHW_FALLBACK.get(mode, mode)


# --- helpers ------------------------------------------------------------------


def _name(value: Any) -> str:
    """Vaillant's enum name of a display value: "Time Controlled" -> "TIME_CONTROLLED"."""
    return str(value).strip().upper().replace(" ", "_")


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
