"""Electricity price per quarter hour for the three kinds of tariff, in EUR/kWh."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, tzinfo
from typing import Any

from vaillant_eebus.ha_client import EntityState
from vaillant_eebus.hems.config import Tariff, TariffKind

# epex_spot names the price per kWh in EUR; older versions also give ct/kWh or EUR/MWh.
_PRICE_KEYS = {"price_per_kwh": 1.0, "price_ct_per_kwh": 0.01, "price_eur_per_mwh": 0.001}


def import_prices(
    tariff: Tariff, price_state: EntityState | None, slots: Sequence[datetime], zone: tzinfo
) -> list[float | None]:
    """The price of electricity from the grid per slot; None where it is unknown."""
    match tariff.kind:
        case TariffKind.FIXED:
            return [_eur(tariff.price_ct)] * len(slots)
        case TariffKind.FIXED_14A:
            return [_window_price(tariff, slot, zone) for slot in slots]
        case TariffKind.DYNAMIC:
            intervals = spot_intervals(price_state)
            return [_spot_price(tariff, intervals, slot) for slot in slots]


def feed_in_price(tariff: Tariff) -> float | None:
    return _eur(tariff.feed_in_ct)


def spot_intervals(state: EntityState | None) -> list[tuple[datetime, datetime, float]]:
    """(start, end, EUR/kWh) from the attribute "data" of an epex_spot price sensor, oldest first."""
    if state is None:
        return []
    data = state.attributes.get("data")
    if not isinstance(data, list):
        return []
    intervals = []
    for item in data:
        interval = _interval(item)
        if interval is not None:
            intervals.append(interval)
    return sorted(intervals)


def _interval(item: Any) -> tuple[datetime, datetime, float] | None:
    if not isinstance(item, dict):
        return None
    start, end = _time(item.get("start_time")), _time(item.get("end_time"))
    if start is None or end is None or end <= start:
        return None
    for key, factor in _PRICE_KEYS.items():
        value = item.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
            return start, end, value * factor
    return None


def _spot_price(tariff: Tariff, intervals: list[tuple[datetime, datetime, float]], slot: datetime) -> float | None:
    for start, end, price in intervals:
        if start <= slot < end:
            return (price + tariff.surcharge_ct / 100) * (1 + tariff.vat_percent / 100)
    return None


def _window_price(tariff: Tariff, slot: datetime, zone: tzinfo) -> float | None:
    local = slot.astimezone(zone).time()
    if any(window.contains(local) for window in tariff.high_windows):
        return _eur(tariff.high_price_ct)
    if any(window.contains(local) for window in tariff.low_windows):
        return _eur(tariff.low_price_ct)
    return _eur(tariff.price_ct)


def _eur(cents: float | None) -> float | None:
    return None if cents is None else cents / 100


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None
