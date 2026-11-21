"""Does warm water reach the radiators? (punkt1-trv-regelung.md 3, "Wärmeangebot Zone 2")

Only with usable heat may the room control raise a correction, count a room as
undersupplied or redistribute. Until the zones are controlled (1.5.0) the app
knows no planned pauses; it reads the state from measurements alone:

- heat from the heat pump: it runs (above 50 W) and the flow (or, without a
  flow sensor, the buffer) is at least 8 K above the rooms;
- heat from the buffer: the heat pump stands, the buffer is at least 5 K above
  the rooms and falls by 2 K/h or more (the circuits draw from it), with the
  time left until the heat pump usually starts again;
- no usable heat: otherwise (e.g. a buffer that hardly falls: nothing flows);
- unclear: power or temperatures older than 15 minutes.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

type Series = Sequence[tuple[datetime, float]]

RUNNING_W = 50.0
FRESH = timedelta(minutes=15)
FROM_PUMP_K = 8.0
FROM_BUFFER_K = 5.0
FALLING_K_PER_H = 2.0
RATE_OVER = timedelta(minutes=30)
# Buffer temperature at which the heat pump started again in the field data (13.-20.11.2026).
DEFAULT_RESTART_C = 28.0
START_W = 100.0
PAUSE_BEFORE_START = timedelta(minutes=20)
DEFAULT_ROOM_C = 20.0


class HeatSupply(StrEnum):
    HEAT_PUMP = "heat_pump"
    BUFFER = "buffer"
    NONE = "none"
    UNKNOWN = "unknown"


SUPPLY_NAMES = {
    HeatSupply.HEAT_PUMP: "Wärme von der Wärmepumpe",
    HeatSupply.BUFFER: "Wärme aus dem Puffer",
    HeatSupply.NONE: "keine nutzbare Wärme",
    HeatSupply.UNKNOWN: "unklar",
}


@dataclass(frozen=True, slots=True)
class SupplyNow:
    supply: HeatSupply
    # With heat from the buffer: minutes until it is used up (the heat pump usually starts then).
    remaining_min: float | None = None
    buffer_c: float | None = None
    # How fast the buffer falls, K/h (positive = falling).
    falling_k_per_h: float | None = None

    @property
    def usable(self) -> bool:
        return self.supply in (HeatSupply.HEAT_PUMP, HeatSupply.BUFFER)


def latest(series: Series, now: datetime) -> float | None:
    """The newest value, if it is fresh."""
    if not series or now - series[-1][0] > FRESH:
        return None
    return series[-1][1]


def falling_rate(series: Series, now: datetime) -> float | None:
    """How fast the values fell over the last half hour, K/h (positive = falling)."""
    recent = [point for point in series if now - point[0] <= RATE_OVER]
    if len(recent) < 2 or now - recent[-1][0] > FRESH:
        return None
    hours = (recent[-1][0] - recent[0][0]).total_seconds() / 3600
    if hours < 0.15:
        return None
    return (recent[0][1] - recent[-1][1]) / hours


def heat_supply(
    *,
    now: datetime,
    power: Series,
    buffer: Series,
    flow: Series = (),
    room_c: float | None,
    restart_c: float | None = None,
) -> SupplyNow:
    watts = latest(power, now)
    if watts is None:
        return SupplyNow(HeatSupply.UNKNOWN)
    room = room_c if room_c is not None else DEFAULT_ROOM_C
    buffer_c = latest(buffer, now)
    flow_c = latest(flow, now)
    if watts >= RUNNING_W:
        water = flow_c if flow_c is not None else buffer_c
        if water is None or water >= room + FROM_PUMP_K:
            return SupplyNow(HeatSupply.HEAT_PUMP, buffer_c=buffer_c)
        return SupplyNow(HeatSupply.NONE, buffer_c=buffer_c)
    if buffer_c is None:
        return SupplyNow(HeatSupply.UNKNOWN)
    rate = falling_rate(buffer, now)
    if rate is None:
        return SupplyNow(HeatSupply.UNKNOWN, buffer_c=buffer_c)
    if buffer_c >= room + FROM_BUFFER_K and rate >= FALLING_K_PER_H:
        floor = max(restart_c if restart_c is not None else DEFAULT_RESTART_C, room + FROM_BUFFER_K)
        remaining = max(0.0, (buffer_c - floor) / rate * 60)
        return SupplyNow(HeatSupply.BUFFER, remaining_min=round(remaining), buffer_c=buffer_c, falling_k_per_h=rate)
    return SupplyNow(HeatSupply.NONE, buffer_c=buffer_c, falling_k_per_h=rate)


def restart_temperature(power: Series, buffer: Series) -> float | None:
    """The buffer temperature at which the heat pump started after a pause (median)."""
    values = []
    times = [point[0] for point in buffer]
    below_since: datetime | None = None
    for moment, watts in power:
        if watts < START_W:
            below_since = below_since or moment
            continue
        started = below_since is not None and moment - below_since >= PAUSE_BEFORE_START
        below_since = None
        if not started:
            continue
        nearest = min(range(len(times)), key=lambda i: abs(times[i] - moment), default=None)
        if nearest is not None and abs(times[nearest] - moment) <= timedelta(minutes=10):
            values.append(buffer[nearest][1])
    return round(statistics.median(values), 1) if len(values) >= 3 else None
