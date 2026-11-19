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
