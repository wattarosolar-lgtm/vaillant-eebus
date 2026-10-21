"""Expected PV surplus per quarter hour: what would go to the grid otherwise.

PV forecast minus the house's usual consumption without the heat pump. The
battery charges first (up to its charging power and capacity) and covers the
house when there is no sun, down to its discharge limit; only what is left
over counts as surplus the heat pump can use cheaply.
"""

from __future__ import annotations

import itertools
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from vaillant_eebus.ha_client import EntityState
from vaillant_eebus.hems.config import HemsSettings
from vaillant_eebus.hems.timeline import SLOT, local_day, slot_start
from vaillant_eebus.history import bucket_means

# Days of history the usual consumption is learned from.
BASE_DAYS = 7
SLOTS_PER_DAY = 96
_POWER_UNITS = {"W": 1.0, "kW": 1000.0}
HOURS_PER_SLOT = SLOT.total_seconds() / 3600


@dataclass(frozen=True, slots=True)
class SupplySlot:
    # Forecast PV power, usual consumption without the heat pump, and what would go to the grid, in W.
    pv_w: float | None
    base_w: float | None
    surplus_w: float | None
    # Expected state of charge at the end of the slot, in %.
    battery_soc: float | None


def pv_forecast(states: Sequence[EntityState | None], slots: Sequence[datetime], zone: tzinfo) -> list[float | None]:
    """Mean PV power per slot from the attribute "watts" (time -> W) of the forecast sensors.

    Between two points the power runs linearly. A day the forecast covers has
    no sun before its first and after its last point (0 W); other days are unknown.
    """
    points: list[tuple[datetime, float]] = []
    for state in states:
        watts = state.attributes.get("watts") if state is not None else None
        if not isinstance(watts, dict):
            continue
        for key, value in watts.items():
            moment = _time(key)
            if moment is not None and isinstance(value, int | float) and not isinstance(value, bool):
                points.append((moment, float(value)))
    points.sort()
    days = {local_day(moment, zone) for moment, _ in points}
    values: list[float | None] = []
    for slot in slots:
        if local_day(slot, zone) not in days:
            values.append(None)
            continue
        start, end = _interpolate(points, slot), _interpolate(points, slot + SLOT)
        values.append(round((start + end) / 2, 1))
    return values


def base_profile(
    house: Sequence[tuple[datetime, str]],
    house_unit: str | None,
    heat_pump: Sequence[tuple[datetime, str]],
    now: datetime,
    zone: tzinfo,
) -> dict[tuple[int, int], float]:
    """Usual consumption without the heat pump per local quarter of the day, (hour, minute) -> W.

    The median of the last days, so a single day with the oven or the sauna
    does not count. Quarters without data are missing.
    """
    factor = _POWER_UNITS.get(house_unit or "")
    if factor is None:
        return {}
    end = slot_start(now)
    count = BASE_DAYS * SLOTS_PER_DAY
    house_means = bucket_means(house, end, step=SLOT, count=count + 1)
    pump_means = bucket_means(heat_pump, end, step=SLOT, count=count + 1) if heat_pump else None
    if house_means.start is None:
        return {}
    by_quarter: dict[tuple[int, int], list[float]] = {}
    # The last bucket is the running quarter hour; it is left out.
    for index in range(count):
        value = house_means.watts[index]
        if value is None:
            continue
        pump = pump_means.watts[index] if pump_means is not None else 0.0
        if pump is None:
            continue
        moment = (house_means.start + index * SLOT).astimezone(zone)
        by_quarter.setdefault((moment.hour, moment.minute), []).append(max(0.0, value * factor - pump))
    return {quarter: round(statistics.median(values), 1) for quarter, values in by_quarter.items()}


def base_load(profile: dict[tuple[int, int], float], slots: Sequence[datetime], zone: tzinfo) -> list[float | None]:
    """The profile per slot; a quarter without data takes the mean of the profile."""
    if not profile:
        return [None] * len(slots)
    fallback = round(statistics.fmean(profile.values()), 1)
    values: list[float | None] = []
    for slot in slots:
        local = slot.astimezone(zone)
        values.append(profile.get((local.hour, local.minute), fallback))
    return values


def surplus(
    pv: Sequence[float | None],
    base: Sequence[float | None],
    battery_soc: float | None,
    settings: HemsSettings,
) -> list[SupplySlot]:
    """What would go to the grid per slot, with the battery charging first."""
    capacity_wh = (settings.battery_capacity_kwh or 0.0) * 1000
    with_battery = settings.battery_soc is not None and capacity_wh > 0
    if with_battery and battery_soc is None:
        # Without the state of charge it is unknown when the battery is full.
        return [
            SupplySlot(pv_w=pv_w, base_w=base_w, surplus_w=None, battery_soc=None)
            for pv_w, base_w in zip(pv, base, strict=True)
        ]
    limit_w = settings.battery_charge_kw * 1000 if settings.battery_charge_kw else float("inf")
    floor_wh = capacity_wh * settings.battery_min_soc / 100
    stored_wh = capacity_wh * (battery_soc or 0.0) / 100 if with_battery else 0.0
    result: list[SupplySlot] = []
    known = True
    for pv_w, base_w in zip(pv, base, strict=True):
        if pv_w is None or base_w is None:
            # The battery cannot be followed through a gap.
            known = False
            result.append(SupplySlot(pv_w=pv_w, base_w=base_w, surplus_w=None, battery_soc=None))
            continue
        net_w = pv_w - base_w
        soc = None
        if with_battery and known:
            if net_w > 0:
                charge_w = min(net_w, limit_w, (capacity_wh - stored_wh) / HOURS_PER_SLOT)
                stored_wh += charge_w * HOURS_PER_SLOT
                net_w -= charge_w
            else:
                discharge_w = min(-net_w, limit_w, max(0.0, stored_wh - floor_wh) / HOURS_PER_SLOT)
                stored_wh -= discharge_w * HOURS_PER_SLOT
            soc = round(stored_wh / capacity_wh * 100, 1)
        elif with_battery:
            result.append(SupplySlot(pv_w=pv_w, base_w=base_w, surplus_w=None, battery_soc=None))
            continue
        result.append(SupplySlot(pv_w=pv_w, base_w=base_w, surplus_w=round(max(0.0, net_w), 1), battery_soc=soc))
    return result


def number(state: EntityState | None) -> float | None:
    if state is None:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def _interpolate(points: Sequence[tuple[datetime, float]], moment: datetime) -> float:
    if not points or moment < points[0][0] or moment > points[-1][0]:
        return 0.0
    for (start, start_w), (end, end_w) in itertools.pairwise(points):
        if start <= moment <= end:
            span = (end - start).total_seconds()
            if span <= 0 or end - start > timedelta(hours=3):
                return start_w
            return start_w + (end_w - start_w) * (moment - start).total_seconds() / span
    return 0.0


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None
