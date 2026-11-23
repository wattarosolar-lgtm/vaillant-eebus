"""What the app knows about a radiator thermostat from Home Assistant; only read.

Zigbee2MQTT names all entities of a device after its IEEE address, e.g.
climate.0x54ef441001881d95 and select.0x54ef441001881d95_sensor. The app finds
the other entities of a thermostat by that name and only checks them: the
Aqara W600 must regulate with its own sensor ("internal"), run in "heat"
without a preset, with its own window detection off; the Comet must be in
trv_mode 2 (automatic). The child lock should be on (punkt1-trv-regelung.md 6).
None of these is ever written.

Zigbee2MQTT can report when it last heard of a device ("last_seen", 1.4.2): with
sensor.<address>_last_seen enabled, the app takes it as the thermostat's life
sign. Home Assistant does not record a value reported unchanged, so without it
the life sign is the newest change of the thermostat or its valve.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from vaillant_eebus.ha_client import EntityState
from vaillant_eebus.ids import ieee_address

NO_VALUE = frozenset({"unavailable", "unknown", ""})
# Names after the IEEE address the app reads (Zigbee2MQTT exposes, punkt1-trv-regelung.md).
SIDE_NAMES = frozenset(
    {
        "sensor",
        "window_detection",
        "child_lock",
        "valve_alarm",
        "battery",
        "trv_mode",
        "calibrated",
        "position",
        "pi_heating_demand",
        "window_open",
        "last_seen",
    }
)
LOW_BATTERY = 20.0
# A thermostat silent this long is shown as uncertain.
SILENT = timedelta(hours=12)
_ON = frozenset({"on", "true", "1"})
_OFF = frozenset({"off", "false", "0"})


class TrvModel(StrEnum):
    AQARA = "aqara"
    COMET = "comet"
    OTHER = "other"


MODEL_NAMES = {TrvModel.AQARA: "Aqara W600", TrvModel.COMET: "Eurotronic Comet", TrvModel.OTHER: "Thermostat"}


@dataclass(frozen=True, slots=True)
class Thermostat:
    entity_id: str
    model: TrvModel
    available: bool
    setpoint_c: float | None
    # The temperature at the thermostat (its own sensor at the radiator).
    temperature_c: float | None
    hvac_mode: str | None
    preset: str | None
    # Other entities of the same device by their name, e.g. "child_lock".
    side: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    # When Zigbee2MQTT last heard of it (last_seen), else the newest change of it or its valve.
    life_sign: datetime | None = None
    # The last_seen entity exists (Zigbee2MQTT reports it).
    has_last_seen: bool = False


def side_entities(trv: str, states: Iterable[EntityState]) -> dict[str, str]:
    """The thermostat's other Zigbee2MQTT entities: name -> entity ID."""
    device = ieee_address(trv)
    if device is None:
        return {}
    found: dict[str, str] = {}
    for state in states:
        if state.entity_id == trv or ieee_address(state.entity_id) != device:
            continue
        name = state.entity_id.split(".", 1)[1].removeprefix(f"{device}_")
        if name in SIDE_NAMES and name not in found:
            found[name] = state.entity_id
    return found


def model_of(side: Mapping[str, str]) -> TrvModel:
    if "trv_mode" in side or "pi_heating_demand" in side:
        return TrvModel.COMET
    if "sensor" in side or "position" in side:
        return TrvModel.AQARA
    return TrvModel.OTHER


def read(
    trv: str,
    state: EntityState | None,
    side: Mapping[str, str],
    side_states: Mapping[str, EntityState],
    *,
    valve: EntityState | None = None,
    now: datetime | None = None,
    last_seen: EntityState | None = None,
) -> Thermostat:
    """The thermostat as Home Assistant shows it, with what the room control needs checked.

    last_seen is the state of its last_seen entity, read live (the side states are read every 15 minutes).
    """
    model = model_of(side)
    seen = seen_at(last_seen)
    life_sign = max(
        (moment for moment in (seen, *(item.last_reported for item in (state, valve) if item)) if moment),
        default=None,
    )
    if state is None or state.state in NO_VALUE:
        return Thermostat(
            entity_id=trv,
            model=model,
            available=False,
            setpoint_c=None,
            temperature_c=None,
            hvac_mode=None,
            preset=None,
            side=dict(side),
            warnings=("Das Thermostat ist nicht erreichbar.",),
            life_sign=life_sign,
            has_last_seen="last_seen" in side,
        )
    attributes = state.attributes
    preset = attributes.get("preset_mode")
    warnings: list[str] = []
    if _number(attributes.get("min_temp")) is None or _number(attributes.get("max_temp")) is None:
        warnings.append("Das Thermostat meldet keinen Bereich (min_temp/max_temp); die App stellt es nicht.")
    if model is TrvModel.AQARA:
        if state.state != "heat":
            warnings.append(f"Betrieb ist „{state.state}“; die Raumregelung braucht „heat“.")
        if isinstance(preset, str) and preset.lower() not in ("none", "manual", ""):
            warnings.append(f"Preset „{preset}“ ist aktiv; bitte „none“ wählen.")
        sensor = _state(side_states, side, "sensor")
        if sensor is not None and sensor != "internal":
            warnings.append(f"Fühler steht auf „{sensor}“; die App regelt nur mit dem eigenen Fühler (intern).")
    if model is TrvModel.COMET:
        mode = _state(side_states, side, "trv_mode")
        if mode is not None and "2" not in mode:
            warnings.append(f"trv_mode ist „{mode}“; die Raumregelung braucht 2 (Automatik).")
    detection = _state(side_states, side, "window_detection")
    if detection is not None and detection.lower() in _ON:
        warnings.append(
            "Die eigene Fenstererkennung des Thermostats ist an; bitte ausschalten (die App erkennt Fenster)."
        )
    lock = _state(side_states, side, "child_lock")
    if lock is not None and (lock.lower() in _OFF or lock.lower() in ("unlocked", "unlock")):
        warnings.append("Die Kindersicherung ist aus.")
    alarm = _state(side_states, side, "valve_alarm")
    if alarm is not None and alarm.lower() in _ON:
        warnings.append("Das Thermostat meldet einen Ventilalarm.")
    battery = _number(_state(side_states, side, "battery"))
    if battery is not None and battery < LOW_BATTERY:
        warnings.append(f"Batterie schwach ({battery:.0f} %).")
    if now is not None and life_sign is not None and now - life_sign > SILENT:
        warnings.append("Das Thermostat hat sich seit über 12 h nicht gemeldet.")
    return Thermostat(
        entity_id=trv,
        model=model,
        available=True,
        setpoint_c=_number(attributes.get("temperature")),
        temperature_c=_number(attributes.get("current_temperature")),
        hvac_mode=state.state,
        preset=preset if isinstance(preset, str) else None,
        side=dict(side),
        warnings=tuple(warnings),
        life_sign=life_sign,
        has_last_seen="last_seen" in side,
    )


def seen_at(state: EntityState | None) -> datetime | None:
    """The time in a last_seen entity (ISO 8601 with offset); None without one."""
    if state is None or state.state in NO_VALUE:
        return None
    try:
        moment = datetime.fromisoformat(state.state)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def blocking(thermostat: Thermostat) -> list[str]:
    """Warnings that keep the app from controlling the thermostat (the rest only shows)."""
    return [
        warning
        for warning in thermostat.warnings
        if warning.startswith(("Das Thermostat ist nicht", "Das Thermostat meldet keinen", "Fühler steht", "trv_mode"))
    ]


def _state(states: Mapping[str, EntityState], side: Mapping[str, str], name: str) -> str | None:
    entity = side.get(name)
    state = states.get(entity) if entity is not None else None
    if state is None or state.state in NO_VALUE:
        return None
    return state.state


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None
