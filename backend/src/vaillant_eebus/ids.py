"""Formats of the IDs the settings hold."""

from __future__ import annotations

import re

# A Home Assistant entity ID, e.g. "sensor.wohnzimmer_temperatur".
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
# The bridge's zone ID, the SPINE address of the zone's room entity, e.g. "5.1.1".
ZONE_ID = re.compile(r"^[0-9]{1,5}(\.[0-9]{1,5}){0,7}$")
