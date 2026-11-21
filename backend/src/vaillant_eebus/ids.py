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


def is_own_entity(entity_id: str) -> bool:
    return OWN_ENTITY.match(entity_id) is not None


def ieee_address(entity_id: str | None) -> str | None:
    """The Zigbee device an entity belongs to (Zigbee2MQTT naming), e.g. "0x54ef441001881d95"."""
    match = _IEEE.match(entity_id) if entity_id else None
    return match.group(1) if match else None
