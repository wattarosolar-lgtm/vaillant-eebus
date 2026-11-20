"""Weather for the next 48 hours: Open-Meteo (DWD ICON), Home Assistant's weather as fallback.

Open-Meteo is asked at most once an hour with the coordinates rounded to two
decimals (about 1 km); its answer includes the past days, which calibrate the
forecast against the heat pump's own outdoor sensor (a bias per local hour of
day). The Home Assistant weather entity is read with weather.get_forecasts,
which changes nothing. Every forecast is also remembered a day ahead, so the
app can show how well it held (Prognosefehler).
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

import aiohttp

from vaillant_eebus.ha_client import HomeAssistantClient, HomeAssistantUnavailable
from vaillant_eebus.hems.config import WeatherSource
from vaillant_eebus.settings import SettingsStore

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/dwd-icon"
HOURLY = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "cloud_cover",
    "precipitation",
    "snowfall",
)
# Values Open-Meteo gives for the hour before the time stamp.
_PRECEDING_HOUR = frozenset({"shortwave_radiation", "precipitation", "snowfall"})
PAST_DAYS = 7
FORECAST_DAYS = 3
HORIZON = timedelta(hours=48)
FETCH_EVERY = timedelta(hours=1)
RETRY_AFTER = timedelta(minutes=10)
# An older forecast is used while a new one cannot be fetched, up to this age.
KEEP_FOR = timedelta(hours=6)
REQUEST_TIMEOUT = 20.0
# Calibration: needs this many days of pairs; the correction is limited.
CALIBRATION_MIN_DAYS = 3
CALIBRATION_MAX_K = 5.0
# Day-ahead check: the forecast remembered for an hour is the first one 18 to 30 hours ahead.
DAY_AHEAD = (timedelta(hours=18), timedelta(hours=30))
CHECK_DAYS = 14
CHECK_FILE = "forecast_check.json"
_WIND_TO_MS = {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 0.514444, "ft/s": 0.3048}


@dataclass(frozen=True, slots=True)
class WeatherHour:
    """The weather of one hour, starting at start (UTC)."""

    start: datetime
    temperature_c: float | None = None
    humidity: float | None = None
    dew_point_c: float | None = None
    wind_ms: float | None = None
    # Mean global radiation of the hour, W/m².
    radiation_w: float | None = None
    cloud_cover: float | None = None
    precipitation_mm: float | None = None
    snowfall_cm: float | None = None


@dataclass(frozen=True, slots=True)
class Forecast:
    source: WeatherSource
    fetched_at: datetime
    hours: tuple[WeatherHour, ...]
    # Kelvin added to the raw forecast per local hour of day (calibration); empty: not calibrated.
    bias: Mapping[int, float] = field(default_factory=dict)

    def at(self, moment: datetime) -> WeatherHour | None:
        start = hour_start(moment)
        return next((hour for hour in self.hours if hour.start == start), None)

    def ahead(self, now: datetime, horizon: timedelta = HORIZON) -> list[WeatherHour]:
        """The hours from the current one on, at most horizon."""
        first = hour_start(now)
        return [hour for hour in self.hours if first <= hour.start < first + horizon]

    def past(self, now: datetime) -> list[WeatherHour]:
        return [hour for hour in self.hours if hour.start < hour_start(now)]


@dataclass(frozen=True, slots=True)
class ForecastCheck:
    """How well the day-ahead temperature forecast held over the last days."""

    hours: int
    mean_abs_error_k: float
    bias_k: float


def hour_start(moment: datetime) -> datetime:
    return datetime.fromtimestamp(math.floor(moment.timestamp() / 3600) * 3600, UTC)


def parse_open_meteo(data: Any) -> list[WeatherHour]:
    """Open-Meteo's hourly answer (timeformat unixtime) -> hours."""
    hourly = data.get("hourly") if isinstance(data, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise ValueError("no hourly data")
    times = hourly["time"]
    columns = {name: hourly.get(name) for name in HOURLY}
    values: dict[datetime, dict[str, float]] = {}
    for index, stamp in enumerate(times):
        if not isinstance(stamp, int | float) or isinstance(stamp, bool):
            continue
        moment = datetime.fromtimestamp(stamp, UTC)
        for name, column in columns.items():
            if not isinstance(column, list) or index >= len(column):
                continue
            value = _finite(column[index])
            if value is None:
                continue
            # Sums and means of the hour before the time stamp belong to that hour.
            start = moment - timedelta(hours=1) if name in _PRECEDING_HOUR else moment
            values.setdefault(start, {})[name] = value
    return [
        WeatherHour(
            start=start,
            temperature_c=row.get("temperature_2m"),
            humidity=row.get("relative_humidity_2m"),
            dew_point_c=row.get("dew_point_2m"),
            wind_ms=row.get("wind_speed_10m"),
            radiation_w=row.get("shortwave_radiation"),
            cloud_cover=row.get("cloud_cover"),
            precipitation_mm=row.get("precipitation"),
            snowfall_cm=row.get("snowfall"),
        )
        for start, row in sorted(values.items())
    ]


def parse_weather_entity(items: Sequence[Mapping[str, Any]], attributes: Mapping[str, Any]) -> list[WeatherHour]:
    """An hourly forecast of weather.get_forecasts -> hours (°F and wind units converted)."""
    fahrenheit = attributes.get("temperature_unit") == "°F"
    wind_factor = _WIND_TO_MS.get(str(attributes.get("wind_speed_unit") or "km/h"), 1 / 3.6)
    hours: dict[datetime, WeatherHour] = {}

    def temperature(item: Mapping[str, Any], key: str) -> float | None:
        value = _finite(item.get(key))
        if value is None:
            return None
        return (value - 32) * 5 / 9 if fahrenheit else value

    for item in items:
        moment = _time(item.get("datetime"))
        if moment is None:
            continue
        wind = _finite(item.get("wind_speed"))
        start = hour_start(moment)
        hours[start] = WeatherHour(
            start=start,
            temperature_c=temperature(item, "temperature"),
            humidity=_finite(item.get("humidity")),
            dew_point_c=temperature(item, "dew_point"),
            wind_ms=wind * wind_factor if wind is not None else None,
            cloud_cover=_finite(item.get("cloud_coverage")),
            precipitation_mm=_finite(item.get("precipitation")),
        )
    return [hours[start] for start in sorted(hours)]


def calibration(past: Sequence[WeatherHour], measured: Mapping[datetime, float], zone: tzinfo) -> dict[int, float]:
    """Median of measured - forecast per local hour of day; empty with too few days."""
    differences: dict[int, list[float]] = {}
    days = set()
    for hour in past:
        actual = measured.get(hour.start)
        if hour.temperature_c is None or actual is None:
            continue
        local = hour.start.astimezone(zone)
        differences.setdefault(local.hour, []).append(actual - hour.temperature_c)
        days.add(local.date())
    if len(days) < CALIBRATION_MIN_DAYS:
        return {}
    return {
        hour: round(max(-CALIBRATION_MAX_K, min(CALIBRATION_MAX_K, statistics.median(values))), 2)
        for hour, values in differences.items()
        if len(values) >= CALIBRATION_MIN_DAYS
    }


def calibrated(hours: Sequence[WeatherHour], bias: Mapping[int, float], zone: tzinfo) -> tuple[WeatherHour, ...]:
    if not bias:
        return tuple(hours)
    result = []
    for hour in hours:
        shift = bias.get(hour.start.astimezone(zone).hour)
        if hour.temperature_c is None or shift is None:
            result.append(hour)
        else:
            result.append(replace(hour, temperature_c=round(hour.temperature_c + shift, 2)))
    return tuple(result)


class ForecastService:
    """Fetches the forecast at most hourly and keeps the last good one."""

    def __init__(
        self,
        settings: SettingsStore,
        client: HomeAssistantClient | None,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        open_meteo_url: str = OPEN_METEO_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._client = client
        self._zone = zone
        self._url = open_meteo_url
        self._clock = clock
        self._check_path = data_dir / CHECK_FILE
        self._raw: Forecast | None = None
        self._current: Forecast | None = None
        self._next_fetch: datetime | None = None
        self._key: tuple[Any, ...] | None = None
        self.problem: str | None = None
        self._day_ahead: dict[datetime, float] = self._load_check()

    @property
    def current(self) -> Forecast | None:
        return self._current

    async def refresh(self, measured: Mapping[datetime, float]) -> Forecast | None:
        """The forecast, fetched again when due; calibrated with the measured hourly outdoor means."""
        now = self._clock()
        hems = self._settings.current.hems
        key = (hems.weather_source, hems.weather_entity, _rounded(hems.latitude), _rounded(hems.longitude))
        if key != self._key:
            self._key, self._raw, self._next_fetch = key, None, None
        if self._next_fetch is None or now >= self._next_fetch:
            fetched = await self._fetch()
            if fetched is not None:
                self._raw = fetched
                self._next_fetch = now + FETCH_EVERY
            else:
                self._next_fetch = now + RETRY_AFTER
        raw = self._raw
        if raw is not None and now - raw.fetched_at > KEEP_FOR:
            raw = self._raw = None
        if raw is None:
            self._current = None
            return None
        bias = calibration(raw.past(now), measured, self._zone)
        self._current = replace(raw, hours=calibrated(raw.hours, bias, self._zone), bias=bias)
        self._remember(raw, now)
        return self._current

    def check(self, measured: Mapping[datetime, float]) -> ForecastCheck | None:
        """Mean error of the day-ahead forecast against the measured hourly means."""
        errors = [
            measured[hour] - predicted
            for hour, predicted in self._day_ahead.items()
            if hour in measured and hour < hour_start(self._clock())
        ]
        if len(errors) < 12:
            return None
        return ForecastCheck(
            hours=len(errors),
            mean_abs_error_k=round(statistics.fmean(abs(error) for error in errors), 2),
            bias_k=round(statistics.fmean(errors), 2),
        )

    # --- internals -------------------------------------------------------------

    async def _fetch(self) -> Forecast | None:
        hems = self._settings.current.hems
        problems = []
        if hems.weather_source is WeatherSource.OPEN_METEO:
            if hems.latitude is None or hems.longitude is None:
                problems.append("Für Open-Meteo fehlt der Standort in den Einstellungen.")
            else:
                try:
                    hours = await self._open_meteo(hems.latitude, hems.longitude)
                    self.problem = None
                    return Forecast(source=WeatherSource.OPEN_METEO, fetched_at=self._clock(), hours=tuple(hours))
                except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                    _LOGGER.warning("Open-Meteo failed: %r", err)
                    problems.append("Open-Meteo antwortet gerade nicht.")
        if hems.weather_entity is not None:
            try:
                hours = await self._weather_entity(hems.weather_entity)
                self.problem = " ".join([*problems, "Die Prognose kommt aus Home Assistant."]) if problems else None
                return Forecast(source=WeatherSource.HOME_ASSISTANT, fetched_at=self._clock(), hours=tuple(hours))
            except (HomeAssistantUnavailable, ValueError) as err:
                _LOGGER.warning("Weather forecast of %s failed: %s", hems.weather_entity, err)
                problems.append("Die Wetter-Entität in Home Assistant liefert keine Prognose.")
        elif hems.weather_source is WeatherSource.HOME_ASSISTANT:
            problems.append("Es ist keine Wetter-Entität gewählt.")
        self.problem = " ".join(problems) or "Es gibt keine Wetterprognose."
        return None

    async def _open_meteo(self, latitude: float, longitude: float) -> list[WeatherHour]:
        params = {
            "latitude": f"{latitude:.2f}",
            "longitude": f"{longitude:.2f}",
            "hourly": ",".join(HOURLY),
            "past_days": str(PAST_DAYS),
            "forecast_days": str(FORECAST_DAYS),
            "timeformat": "unixtime",
            "timezone": "GMT",
            "wind_speed_unit": "ms",
        }
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(self._url, params=params) as response,
        ):
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            data = await response.json(content_type=None)
        hours = parse_open_meteo(data)
        if not hours:
            raise ValueError("empty forecast")
        return hours

    async def _weather_entity(self, entity_id: str) -> list[WeatherHour]:
        if self._client is None:
            raise HomeAssistantUnavailable("no Supervisor token")
        state = await self._client.state(entity_id)
        items = await self._client.hourly_forecast(entity_id)
        hours = parse_weather_entity(items, state.attributes if state is not None else {})
        if not hours:
            raise ValueError("empty forecast")
        return hours

    def _remember(self, raw: Forecast, now: datetime) -> None:
        """Keep the first forecast 18-30 hours ahead per hour, for the day-ahead check."""
        changed = False
        for hour in raw.hours:
            ahead = hour.start - now
            known = hour.start in self._day_ahead
            if DAY_AHEAD[0] <= ahead <= DAY_AHEAD[1] and hour.temperature_c is not None and not known:
                self._day_ahead[hour.start] = hour.temperature_c
                changed = True
        oldest = now - timedelta(days=CHECK_DAYS)
        for start in [start for start in self._day_ahead if start < oldest]:
            del self._day_ahead[start]
            changed = True
        if changed:
            self._save_check()

    def _load_check(self) -> dict[datetime, float]:
        try:
            raw = json.loads(self._check_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as err:
            _LOGGER.warning("Forecast check %s unreadable (%s), starting anew", self._check_path, err)
            return {}
        result = {}
        for key, value in raw.items() if isinstance(raw, dict) else []:
            moment, number = _time(key), _finite(value)
            if moment is not None and number is not None:
                result[moment] = number
        return result

    def _save_check(self) -> None:
        payload = json.dumps({hour.isoformat(): value for hour, value in sorted(self._day_ahead.items())})
        try:
            self._check_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._check_path.parent, prefix=CHECK_FILE, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self._check_path)
        except OSError as err:
            _LOGGER.warning("Saving the forecast check failed: %s", err)


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
