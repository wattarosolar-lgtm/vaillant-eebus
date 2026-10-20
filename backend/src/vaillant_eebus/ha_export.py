"""The heat pump's values as sensors in Home Assistant (spec 10).

The app sets the states of its own entities over the REST API
(sensor.vaillant_eebus_*, binary_sensor.vaillant_eebus_*). Such states have no
fixed identity in Home Assistant and are gone after a restart of Home
Assistant, so the app reports all of them again after connecting and when Home
Assistant has started. A changed value is sent at most every 10 seconds per
sensor, energy at most every minute. Values the heat pump has never delivered
get no sensor; a value that disappears is reported as unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import HomeAssistantClient, HomeAssistantListener, HomeAssistantUnavailable
from vaillant_eebus.ha_import import HomeAssistantImport
from vaillant_eebus.settings import ExportGroup, SettingsStore
from vaillant_eebus.state import HeatPumpState, Reading, StateService

_LOGGER = logging.getLogger(__name__)

PREFIX = "vaillant_eebus_"
# Spec 10: at most one report per sensor in this many seconds; energy at most once a minute.
MIN_INTERVAL = 10.0
ENERGY_MIN_INTERVAL = 60.0
UNAVAILABLE = "unavailable"

_MODE_NAMES = {"off": "Aus", "auto": "Auto", "on": "Ein", "eco": "Eco"}
_OPERATING_STATES = {
    "normalOperation": "Normalbetrieb",
    "standby": "Bereitschaft",
    "failure": "Störung",
    "serviceNeeded": "Wartung nötig",
    "overrideDetected": "Übersteuert",
    "inAlarm": "Alarm",
    "notReachable": "Nicht erreichbar",
    "finished": "Beendet",
    "temporarilyNotReady": "Vorübergehend nicht bereit",
    "off": "Aus",
}
_COMPRESSOR_STATES = {
    "available": "angeboten",
    "scheduled": "geplant",
    "running": "läuft",
    "paused": "pausiert",
    "completed": "fertig",
    "stopped": "gestoppt",
}

# Measurements exported when the device delivers them: key, entity suffix, name, kind.
_TEMPERATURES = [
    ("outdoor_temperature", "outdoor_temperature", "Außentemperatur"),
    ("flow_temperature", "flow_temperature", "Vorlauftemperatur"),
    ("return_temperature", "return_temperature", "Rücklauftemperatur"),
    ("compressor_temperature", "compressor_temperature", "Verdichtertemperatur"),
]
_POWERS = [
    ("power_consumption", "power", "Leistung"),
    ("compressor_power", "compressor_power", "Verdichterleistung"),
]
_ENERGIES = [
    ("energy_consumed", "energy_consumed", "Energie gesamt"),
    ("energy_produced", "energy_produced", "Erzeugte Energie"),
    ("energy_consumed_heating", "energy_consumed_heating", "Energie Heizung"),
    ("energy_consumed_dhw", "energy_consumed_dhw", "Energie Warmwasser"),
]


@dataclass(frozen=True, slots=True)
class Sensor:
    entity_id: str
    state: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    # Energy counters are reported less often.
    energy: bool = False
    group: ExportGroup = ExportGroup.STATUS


class HomeAssistantExport(HomeAssistantListener):
    def __init__(
        self,
        client: HomeAssistantClient,
        state: StateService,
        energy: EnergyMeter,
        ha_import: HomeAssistantImport,
        settings: SettingsStore,
        *,
        tick: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._state = state
        self._energy = energy
        self._import = ha_import
        self._settings = settings
        self._tick = tick
        self._clock = clock
        # What Home Assistant has: entity -> (state, attributes) and when it was sent.
        self._sent: dict[str, tuple[str, Mapping[str, Any]]] = {}
        self._sent_at: dict[str, float] = {}
        # Every entity reported since the app started, for removing it when it is no longer exported.
        self._reported: set[str] = set()
        # Sensors created so far; they stay (as unavailable) when their value goes away.
        self._known: dict[str, Sensor] = {}
        self._wake = asyncio.Event()

    def exported(self) -> list[str]:
        """Entity IDs this app currently reports, for the settings page."""
        return sorted(sensor.entity_id for sensor in self._current())

    def refresh(self) -> None:
        """Report soon, e.g. after the export choice changed."""
        self._wake.set()

    async def run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._tick):
                    await self._wake.wait()
            self._wake.clear()
            if self._client.connected:
                try:
                    await self._report()
                except HomeAssistantUnavailable as err:
                    _LOGGER.warning("Reporting sensors to Home Assistant failed: %s", err)

    # --- HomeAssistantListener ----------------------------------------------------

    def ha_connected(self) -> None:
        # Home Assistant may have restarted and lost the states: report everything again.
        self._forget()

    def ha_started(self) -> None:
        self._forget()

    def ha_disconnected(self) -> None:
        self._forget()

    # --- internals -------------------------------------------------------------

    def _forget(self) -> None:
        self._sent.clear()
        self._sent_at.clear()
        self._wake.set()

    async def _report(self) -> None:
        now = self._clock()
        current = {sensor.entity_id: sensor for sensor in self._current()}
        for entity_id in sorted(self._reported - current.keys()):
            await self._client.remove_state(entity_id)
            self._reported.discard(entity_id)
            self._sent.pop(entity_id, None)
            self._sent_at.pop(entity_id, None)
        for sensor in current.values():
            value = (sensor.state, dict(sensor.attributes))
            if self._sent.get(sensor.entity_id) == value:
                continue
            interval = ENERGY_MIN_INTERVAL if sensor.energy else MIN_INTERVAL
            last = self._sent_at.get(sensor.entity_id)
            if last is not None and now - last < interval:
                continue
            await self._client.set_state(sensor.entity_id, sensor.state, sensor.attributes)
            self._sent[sensor.entity_id] = value
            self._sent_at[sensor.entity_id] = now
            self._reported.add(sensor.entity_id)

    def _current(self) -> list[Sensor]:
        """Every sensor to report now: the known ones, unavailable if their value is gone."""
        groups = self._settings.current.export
        fresh = {sensor.entity_id: sensor for sensor in self._sensors(groups)}
        for entity_id, sensor in fresh.items():
            self._known[entity_id] = sensor
        return [
            fresh.get(entity_id) or replace(sensor, state=UNAVAILABLE)
            for entity_id, sensor in self._known.items()
            if sensor.group in groups
        ]

    def _sensors(self, groups: frozenset[ExportGroup]) -> list[Sensor]:
        heat_pump = self._state.current
        reachable = heat_pump is not None and heat_pump.available and heat_pump.connected
        sensors: list[Sensor] = []
        if ExportGroup.STATUS in groups:
            sensors.append(_binary("connected", "Wärmepumpe verbunden", reachable, device_class="connectivity"))
        if heat_pump is None or not reachable:
            return sensors
        temperatures = [
            _measurement(heat_pump.measurements.get(key), suffix, name, "°C", "temperature", "mdi:thermometer")
            for key, suffix, name in _TEMPERATURES
            if key in heat_pump.measurements
        ]
        flexibility = heat_pump.compressor_flexibility
        compressor = (
            []
            if flexibility is None
            else [
                _text(
                    "compressor_flexibility",
                    "Verdichter-Flexibilität",
                    _COMPRESSOR_STATES.get(flexibility.state or "", flexibility.state),
                    "mdi:heat-pump-outline",
                )
            ]
        )
        by_group = {
            ExportGroup.STATUS: _status(heat_pump),
            ExportGroup.ZONES: self._zones(heat_pump),
            ExportGroup.HOT_WATER: _hot_water(heat_pump),
            ExportGroup.TEMPERATURES: temperatures,
            ExportGroup.ENERGY: self._energy_sensors(heat_pump),
            ExportGroup.POWER_LIMITS: _power_limits(heat_pump),
            ExportGroup.COMPRESSOR: compressor,
        }
        for group, members in by_group.items():
            if group in groups:
                sensors += [replace(sensor, group=group) for sensor in members]
        return sensors

    def _zones(self, heat_pump: HeatPumpState) -> list[Sensor]:
        sensors = []
        for zone in heat_pump.zones:
            slug = _slug(zone.label) or _slug(zone.id)
            average = self._import.zone_average(zone.id)
            if average is not None:
                sensors.append(
                    _number(
                        f"{slug}_room_temperature",
                        f"{zone.label} Raumtemperatur",
                        average.celsius,
                        "°C",
                        "temperature",
                        "mdi:home-thermometer",
                        extra={"Quelle": f"Mittelwert aus Home Assistant ({average.rooms} von {average.of} Räumen)"},
                    )
                )
            if zone.setpoint is not None:
                sensors.append(
                    _number(
                        f"{slug}_setpoint",
                        f"{zone.label} Solltemperatur",
                        zone.setpoint.value,
                        "°C",
                        "temperature",
                        "mdi:thermostat",
                    )
                )
            if zone.mode is not None:
                sensors.append(
                    _text(f"{slug}_mode", f"{zone.label} Betriebsart", _MODE_NAMES.get(zone.mode, zone.mode), "mdi:cog")
                )
        return sensors

    def _energy_sensors(self, heat_pump: HeatPumpState) -> list[Sensor]:
        sensors = [
            _measurement(heat_pump.measurements.get(key), suffix, name, "W", "power", "mdi:flash")
            for key, suffix, name in _POWERS
            if key in heat_pump.measurements
        ]
        today = self._energy.today()
        if today is not None:
            sensors.append(
                Sensor(
                    entity_id=f"sensor.{PREFIX}energy_today",
                    state=f"{today.kwh:.3f}",
                    attributes={
                        "friendly_name": "Wärmepumpe Energie heute (berechnet)",
                        "unit_of_measurement": "kWh",
                        "device_class": "energy",
                        "state_class": "total_increasing",
                        "icon": "mdi:lightning-bolt",
                        "lückenhaft": today.incomplete,
                    },
                    energy=True,
                )
            )
        for key, suffix, name in _ENERGIES:
            reading = heat_pump.measurements.get(key)
            if reading is not None:
                sensor = _measurement(reading, suffix, name, "kWh", "energy", "mdi:lightning-bolt")
                attributes = {**sensor.attributes, "state_class": "total_increasing"}
                sensors.append(replace(sensor, attributes=attributes, energy=True))
        return sensors


def _status(heat_pump: HeatPumpState) -> list[Sensor]:
    sensors = [
        _binary("compressor_running", "Verdichter läuft", heat_pump.compressor_active, device_class="running"),
        _binary("heartbeat_ok", "Wärmepumpe Heartbeat OK", heat_pump.heartbeat_ok),
    ]
    if heat_pump.operating_state is not None:
        sensors.append(
            _text(
                "operating_state",
                "Wärmepumpe Betriebszustand",
                _OPERATING_STATES.get(heat_pump.operating_state, heat_pump.operating_state),
                "mdi:information-outline",
            )
        )
    return sensors


def _hot_water(heat_pump: HeatPumpState) -> list[Sensor]:
    water = heat_pump.hot_water
    if water is None:
        return []
    sensors = []
    if water.temperature is not None:
        sensors.append(
            _measurement(
                water.temperature, "dhw_temperature", "Warmwasser", "°C", "temperature", "mdi:water-thermometer"
            )
        )
    if water.setpoint is not None:
        sensors.append(
            _number(
                "dhw_setpoint", "Warmwasser Solltemperatur", water.setpoint.value, "°C", "temperature", "mdi:thermostat"
            )
        )
    if water.mode is not None:
        sensors.append(_text("dhw_mode", "Warmwasser Betriebsart", _MODE_NAMES.get(water.mode, water.mode), "mdi:cog"))
    if water.boost is not None:
        sensors.append(
            _binary(
                "dhw_boost", "Warmwasser Einmalladung", water.boost in ("active", "running"), device_class="running"
            )
        )
    return sensors


def _power_limits(heat_pump: HeatPumpState) -> list[Sensor]:
    sensors = []
    limit = heat_pump.consumption_limit
    if limit is not None:
        sensors.append(_number("power_limit", "Leistungsdeckel", limit.value_w, "W", "power", "mdi:speedometer"))
        sensors.append(_binary("power_limit_active", "Leistungsdeckel aktiv", limit.active))
    failsafe = heat_pump.failsafe_limit
    if failsafe is not None:
        sensors.append(
            _number("failsafe_limit", "Failsafe-Grenze", failsafe.value_w, "W", "power", "mdi:shield-outline")
        )
        sensors.append(
            _number(
                "failsafe_duration", "Failsafe-Dauer", failsafe.duration_s / 60, "min", "duration", "mdi:timer-outline"
            )
        )
    return sensors


def _measurement(reading: Reading | None, suffix: str, name: str, unit: str, device_class: str, icon: str) -> Sensor:
    return _number(suffix, name, reading.value if reading is not None else None, unit, device_class, icon)


def _number(
    suffix: str,
    name: str,
    value: float | None,
    unit: str,
    device_class: str,
    icon: str,
    *,
    extra: Mapping[str, Any] | None = None,
) -> Sensor:
    return Sensor(
        entity_id=f"sensor.{PREFIX}{suffix}",
        state=UNAVAILABLE if value is None else _format(value, unit),
        attributes={
            "friendly_name": name,
            "unit_of_measurement": unit,
            "device_class": device_class,
            "state_class": "measurement",
            "icon": icon,
            **(extra or {}),
        },
    )


def _text(suffix: str, name: str, value: str | None, icon: str) -> Sensor:
    return Sensor(
        entity_id=f"sensor.{PREFIX}{suffix}",
        state=value if value else UNAVAILABLE,
        attributes={"friendly_name": name, "icon": icon},
    )


def _binary(suffix: str, name: str, value: bool | None, *, device_class: str | None = None) -> Sensor:
    attributes: dict[str, Any] = {"friendly_name": name}
    if device_class is not None:
        attributes["device_class"] = device_class
    return Sensor(
        entity_id=f"binary_sensor.{PREFIX}{suffix}",
        state=UNAVAILABLE if value is None else ("on" if value else "off"),
        attributes=attributes,
    )


def _format(value: float, unit: str) -> str:
    if unit == "W":
        return f"{value:.0f}"
    if unit == "kWh":
        return f"{value:.3f}"
    return f"{value:.1f}"


def _slug(text: str) -> str:
    """Lower-case ASCII with underscores, e.g. "Zone 1" -> "zone_1", "Küche" -> "kueche"."""
    for umlaut, ascii_text in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.lower().replace(umlaut, ascii_text)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")
