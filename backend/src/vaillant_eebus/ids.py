"""Formats of the IDs the settings hold."""

from __future__ import annotations

import re

# A Home Assistant entity ID, e.g. "sensor.wohnzimmer_temperatur".
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
# A zone's ID: with EEBUS the SPINE address of the zone's room entity, e.g. "5.1.1";
# with myVAILLANT the zone's climate entity, e.g. "climate.zuhause_zone_1_climate".
ZONE_ID = re.compile(r"^([0-9]{1,5}(\.[0-9]{1,5}){0,7}|climate\.[a-z0-9_]+)$")
# Entities of the myVAILLANT integration the app reads and controls, by kind.
CLIMATE_ENTITY = re.compile(r"^climate\.[a-z0-9_]+$")
WATER_HEATER_ENTITY = re.compile(r"^water_heater\.[a-z0-9_]+$")
SENSOR_ENTITY = re.compile(r"^sensor\.[a-z0-9_]+$")
# The sensors this app reports to Home Assistant itself; never a source of its own values.
OWN_ENTITY = re.compile(r"^(sensor|binary_sensor)\.vaillant_eebus_[a-z0-9_]+$")


# Zigbee2MQTT names a device's entities after its IEEE address, e.g. "climate.0x54ef441001881d95"
# and "sensor.0x54ef441001881d95_position".
_IEEE = re.compile(r"^[a-z_]+\.(0x[0-9a-f]{16})(?:_[a-z0-9_]+)?$")


# Zigbee2MQTT's MQTT base topic: plain segments, no wildcards.
MQTT_BASE_TOPIC = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+){0,3}$")
DEFAULT_MQTT_BASE_TOPIC = "zigbee2mqtt"
DEVICE_NAME_MAX = 100
# Zigbee2MQTT splits "<base>/<name>/(get|set)[/<attribute>]" at the first "get" or "set" segment and ignores
# names starting with "bridge" (lib/extension/publish.ts): such a name could turn a read into a write.
_COMMAND_SEGMENTS = frozenset({"get", "set"})


def device_name_problem(name: str) -> str | None:
    """Why this Zigbee2MQTT device name may not be used in a topic; None when it may (German)."""
    if not name or len(name) > DEVICE_NAME_MAX:
        return f"Der Gerätename braucht 1 bis {DEVICE_NAME_MAX} Zeichen."
    if name != name.strip():
        return "Der Gerätename darf nicht mit einem Leerzeichen beginnen oder enden."
    if any(ord(char) < 32 or char in "+#\u007f" for char in name):
        return "Der Gerätename darf weder Steuerzeichen noch „+“ oder „#“ enthalten."
    segments = name.split("/")
    if any(not segment.strip() for segment in segments):
        return "Der Gerätename darf keine leeren Teile zwischen „/“ haben."
    if any(segment.strip().lower() in _COMMAND_SEGMENTS for segment in segments):
        return "Der Gerätename darf keinen Teil „get“ oder „set“ haben."
    if name.lower().startswith("bridge"):
        return "Der Gerätename darf nicht mit „bridge“ beginnen."
    return None


def is_own_entity(entity_id: str) -> bool:
    return OWN_ENTITY.match(entity_id) is not None


def ieee_address(entity_id: str | None) -> str | None:
    """The Zigbee device an entity belongs to (Zigbee2MQTT naming), e.g. "0x54ef441001881d95"."""
    match = _IEEE.match(entity_id) if entity_id else None
    return match.group(1) if match else None
