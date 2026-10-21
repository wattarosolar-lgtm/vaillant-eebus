"""The user's energy management settings, kept in settings.json under "hems".

Every value is checked on its own: an invalid one falls back to its default
and is logged, the rest of the settings stays.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Any, TypeIs

from vaillant_eebus.ids import ENTITY_ID, ZONE_ID

_LOGGER = logging.getLogger(__name__)

WINDOWS_MAX = 4

# Allowed ranges of the numbers, inclusive.
PRICE_CT = (0.0, 200.0)
FEED_IN_CT = (0.0, 100.0)
VAT_PERCENT = (0.0, 50.0)
BATTERY_KWH = (0.0, 200.0)
BATTERY_KW = (0.0, 50.0)
PERCENT = (0.0, 100.0)
LATITUDE = (-90.0, 90.0)
LONGITUDE = (-180.0, 180.0)
SHIFT_K = (0.0, 3.0)
SHIFT_STEP_K = 0.5
DHW_MAX_C = (35.0, 70.0)
HEATING_LIMIT_C = (0.0, 25.0)


class PlannerMode(StrEnum):
    OFF = "off"
    # Plans and shows what it would send; sends nothing, also in live mode.
    PREVIEW = "preview"
    # Carries the plan out through the write gate (which still blocks in demo mode).
    CONTROL = "control"


class TariffKind(StrEnum):
    FIXED = "fixed"
    # Spot market price from a Home Assistant sensor (epex_spot).
    DYNAMIC = "dynamic"
    # Fixed price with time-variable grid fees (§14a EnWG, module 3): three prices by time window.
    FIXED_14A = "fixed_14a"


class WeatherSource(StrEnum):
    HOME_ASSISTANT = "home_assistant"
    OPEN_METEO = "open_meteo"


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Local time of day; end before start crosses midnight."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start <= self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end


@dataclass(frozen=True, slots=True)
class Tariff:
    kind: TariffKind = TariffKind.FIXED
    # ct/kWh. Fixed: the price; §14a: the price in standard time (ST).
    price_ct: float | None = None
    # Dynamic: sensor with the prices of today and tomorrow (epex_spot "Total Price").
    price_entity: str | None = None
    # Dynamic: added to the sensor's price, for a sensor with the bare market price.
    surcharge_ct: float = 0.0
    vat_percent: float = 0.0
    # §14a: prices in high (HT) and low (NT) time, and their windows.
    high_price_ct: float | None = None
    low_price_ct: float | None = None
    high_windows: tuple[TimeWindow, ...] = ()
    low_windows: tuple[TimeWindow, ...] = ()
    feed_in_ct: float | None = None


@dataclass(frozen=True, slots=True)
class ZoneLimits:
    zone_id: str
    up_k: float = 1.0
    down_k: float = 1.0


DEFAULT_ZONE_UP_K = 1.0
DEFAULT_ZONE_DOWN_K = 1.0


@dataclass(frozen=True, slots=True)
class HemsSettings:
    mode: PlannerMode = PlannerMode.OFF
    tariff: Tariff = Tariff()
    # open_meteo_solar_forecast: energy_production_today / _tomorrow with the attribute "watts".
    pv_forecast_today: str | None = None
    pv_forecast_tomorrow: str | None = None
    # Power of the whole house in W or kW, including the heat pump.
    house_power: str | None = None
    battery_soc: str | None = None
    battery_capacity_kwh: float | None = None
    battery_charge_kw: float | None = None
    battery_min_soc: float = 10.0
    weather_source: WeatherSource = WeatherSource.HOME_ASSISTANT
    weather_entity: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    # Zones not listed use the default limits.
    zones: tuple[ZoneLimits, ...] = ()
    # Hot water only goes up, to at most this setpoint.
    dhw_max_c: float = 60.0
    # Above this expected outdoor temperature the zones are not shifted.
    heating_limit_c: float = 15.0

    def zone_limits(self, zone_id: str) -> ZoneLimits:
        return next(
            (limits for limits in self.zones if limits.zone_id == zone_id),
            ZoneLimits(zone_id=zone_id, up_k=DEFAULT_ZONE_UP_K, down_k=DEFAULT_ZONE_DOWN_K),
        )

    def entities(self) -> frozenset[str]:
        """The Home Assistant entities the planner reads."""
        chosen = [
            self.tariff.price_entity if self.tariff.kind is TariffKind.DYNAMIC else None,
            self.pv_forecast_today,
            self.pv_forecast_tomorrow,
            self.house_power,
            self.battery_soc,
        ]
        return frozenset(entity for entity in chosen if entity)


def hems_from_json(raw: Any) -> HemsSettings:
    if raw is None:
        return HemsSettings()
    if not isinstance(raw, dict):
        _LOGGER.warning("Settings: hems is not an object, using the defaults")
        return HemsSettings()
    defaults = HemsSettings()
    return HemsSettings(
        mode=_enum(raw, "mode", PlannerMode, defaults.mode),
        tariff=_tariff(raw.get("tariff")),
        pv_forecast_today=_entity(raw, "pv_forecast_today"),
        pv_forecast_tomorrow=_entity(raw, "pv_forecast_tomorrow"),
        house_power=_entity(raw, "house_power"),
        battery_soc=_entity(raw, "battery_soc"),
        battery_capacity_kwh=_number(raw, "battery_capacity_kwh", BATTERY_KWH),
        battery_charge_kw=_number(raw, "battery_charge_kw", BATTERY_KW),
        battery_min_soc=_value(raw, "battery_min_soc", PERCENT, defaults.battery_min_soc),
        weather_source=_enum(raw, "weather_source", WeatherSource, defaults.weather_source),
        weather_entity=_entity(raw, "weather_entity"),
        latitude=_number(raw, "latitude", LATITUDE),
        longitude=_number(raw, "longitude", LONGITUDE),
        zones=_zones(raw.get("zones")),
        dhw_max_c=_value(raw, "dhw_max_c", DHW_MAX_C, defaults.dhw_max_c),
        heating_limit_c=_value(raw, "heating_limit_c", HEATING_LIMIT_C, defaults.heating_limit_c),
    )


def hems_to_json(settings: HemsSettings) -> dict[str, Any]:
    tariff = settings.tariff
    return {
        "mode": settings.mode.value,
        "tariff": {
            "kind": tariff.kind.value,
            "price_ct": tariff.price_ct,
            "price_entity": tariff.price_entity,
            "surcharge_ct": tariff.surcharge_ct,
            "vat_percent": tariff.vat_percent,
            "high_price_ct": tariff.high_price_ct,
            "low_price_ct": tariff.low_price_ct,
            "high_windows": [_window_to_json(window) for window in tariff.high_windows],
            "low_windows": [_window_to_json(window) for window in tariff.low_windows],
            "feed_in_ct": tariff.feed_in_ct,
        },
        "pv_forecast_today": settings.pv_forecast_today,
        "pv_forecast_tomorrow": settings.pv_forecast_tomorrow,
        "house_power": settings.house_power,
        "battery_soc": settings.battery_soc,
        "battery_capacity_kwh": settings.battery_capacity_kwh,
        "battery_charge_kw": settings.battery_charge_kw,
        "battery_min_soc": settings.battery_min_soc,
        "weather_source": settings.weather_source.value,
        "weather_entity": settings.weather_entity,
        "latitude": settings.latitude,
        "longitude": settings.longitude,
        "zones": [{"zone_id": zone.zone_id, "up_k": zone.up_k, "down_k": zone.down_k} for zone in settings.zones],
        "dhw_max_c": settings.dhw_max_c,
        "heating_limit_c": settings.heating_limit_c,
    }


def valid_shift(value: float) -> bool:
    steps = value / SHIFT_STEP_K
    return SHIFT_K[0] <= value <= SHIFT_K[1] and abs(steps - round(steps)) < 1e-9


def parse_time(text: str) -> time | None:
    """ "HH:MM" -> time; None if it is not a time of day."""
    match = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", text)
    return time(int(match.group(1)), int(match.group(2))) if match else None


def _tariff(raw: Any) -> Tariff:
    if raw is None:
        return Tariff()
    if not isinstance(raw, dict):
        _LOGGER.warning("Settings: hems.tariff is not an object, using the defaults")
        return Tariff()
    defaults = Tariff()
    return Tariff(
        kind=_enum(raw, "kind", TariffKind, defaults.kind),
        price_ct=_number(raw, "price_ct", PRICE_CT),
        price_entity=_entity(raw, "price_entity"),
        surcharge_ct=_value(raw, "surcharge_ct", PRICE_CT, 0.0),
        vat_percent=_value(raw, "vat_percent", VAT_PERCENT, 0.0),
        high_price_ct=_number(raw, "high_price_ct", PRICE_CT),
        low_price_ct=_number(raw, "low_price_ct", PRICE_CT),
        high_windows=_windows(raw.get("high_windows")),
        low_windows=_windows(raw.get("low_windows")),
        feed_in_ct=_number(raw, "feed_in_ct", FEED_IN_CT),
    )


def _windows(raw: Any) -> tuple[TimeWindow, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: time windows %r are not a list, ignored", raw)
        return ()
    windows: list[TimeWindow] = []
    for item in raw[:WINDOWS_MAX]:
        start = end = None
        if isinstance(item, dict) and isinstance(item.get("start"), str) and isinstance(item.get("end"), str):
            start, end = parse_time(item["start"]), parse_time(item["end"])
        if start is None or end is None or start == end:
            _LOGGER.warning("Settings: invalid time window %r ignored", item)
            continue
        windows.append(TimeWindow(start=start, end=end))
    return tuple(windows)


def _window_to_json(window: TimeWindow) -> dict[str, str]:
    return {"start": window.start.strftime("%H:%M"), "end": window.end.strftime("%H:%M")}


def _zones(raw: Any) -> tuple[ZoneLimits, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _LOGGER.warning("Settings: hems.zones is not a list, ignored")
        return ()
    zones: list[ZoneLimits] = []
    for item in raw:
        zone = None
        if isinstance(item, dict):
            zone_id, up_k, down_k = item.get("zone_id"), item.get("up_k"), item.get("down_k")
            if (
                isinstance(zone_id, str)
                and ZONE_ID.match(zone_id)
                and _is_number(up_k)
                and _is_number(down_k)
                and valid_shift(float(up_k))
                and valid_shift(float(down_k))
            ):
                zone = ZoneLimits(zone_id=zone_id, up_k=float(up_k), down_k=float(down_k))
        if zone is None:
            _LOGGER.warning("Settings: invalid zone limits %r ignored", item)
        elif all(other.zone_id != zone.zone_id for other in zones):
            zones.append(zone)
    return tuple(zones)


def _enum[E: StrEnum](raw: dict[str, Any], key: str, kind: type[E], default: E) -> E:
    value = raw.get(key)
    if value is None:
        return default
    try:
        return kind(value)
    except ValueError:
        _LOGGER.warning("Settings: unknown hems.%s %r, using %s", key, value, default.value)
        return default


def _entity(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, str) and ENTITY_ID.match(value):
        return value
    _LOGGER.warning("Settings: invalid entity %r for hems.%s ignored", value, key)
    return None


def _number(raw: dict[str, Any], key: str, allowed: tuple[float, float]) -> float | None:
    """An optional number; None when it is missing or invalid."""
    value = raw.get(key)
    if value is None:
        return None
    if _is_number(value) and allowed[0] <= float(value) <= allowed[1]:
        return float(value)
    _LOGGER.warning("Settings: hems.%s %r is out of range, ignored", key, value)
    return None


def _value(raw: dict[str, Any], key: str, allowed: tuple[float, float], default: float) -> float:
    """A number with a default for when it is missing or invalid."""
    value = _number(raw, key, allowed)
    return default if value is None else value


def _is_number(value: Any) -> TypeIs[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
