"""Outdoor temperature forecast per hour.

From a Home Assistant weather entity (service weather.get_forecasts) or
straight from Open-Meteo for the coordinates in the settings - the only
internet connection of the app, made only when the user chooses it. A
forecast is fetched at most once an hour; after a failure the last forecast
is kept for a few hours.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeIs

import aiohttp

from vaillant_eebus.ha_client import HomeAssistantClient, HomeAssistantUnavailable
from vaillant_eebus.hems.config import WeatherSource
from vaillant_eebus.settings import SettingsStore

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REFRESH = timedelta(hours=1)
RETRY = timedelta(minutes=10)
# A forecast older than this is not used any more.
KEEP = timedelta(hours=6)
REQUEST_TIMEOUT = 15.0


@dataclass(frozen=True, slots=True)
class TemperatureForecast:
    # Start of the hour (UTC) -> °C.
    hours: dict[datetime, float]
    source: WeatherSource
    fetched_at: datetime

    def at(self, moment: datetime) -> float | None:
        return self.hours.get(moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0))


class WeatherForecasts:
    def __init__(
        self,
        settings: SettingsStore,
        client: HomeAssistantClient | None,
        *,
        open_meteo_url: str = OPEN_METEO_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._client = client
        self._open_meteo_url = open_meteo_url
        self._clock = clock
        self._forecast: TemperatureForecast | None = None
        # The settings the forecast belongs to; a change fetches a new one at once.
        self._key: tuple[Any, ...] | None = None
        self._next_fetch: datetime | None = None
        # German reason why there is no current forecast.
        self.problem: str | None = None

    @property
    def current(self) -> TemperatureForecast | None:
        """The last forecast fetched, without fetching."""
        return self._forecast

    async def forecast(self) -> TemperatureForecast | None:
        hems = self._settings.current.hems
        key = (hems.weather_source, hems.weather_entity, hems.latitude, hems.longitude)
        now = self._clock()
        if key != self._key:
            self._key, self._forecast, self._next_fetch = key, None, None
        if self._next_fetch is None or now >= self._next_fetch:
            try:
                hours = await self._fetch()
            except ForecastUnavailable as err:
                _LOGGER.warning("Weather forecast unavailable: %s", err.reason)
                self.problem = err.reason
                self._next_fetch = now + RETRY
            else:
                self._forecast = TemperatureForecast(hours=hours, source=hems.weather_source, fetched_at=now)
                self.problem = None
                self._next_fetch = now + REFRESH
        if self._forecast is not None and now - self._forecast.fetched_at > KEEP:
            self._forecast = None
        return self._forecast

    async def _fetch(self) -> dict[datetime, float]:
        hems = self._settings.current.hems
        if hems.weather_source is WeatherSource.OPEN_METEO:
            if hems.latitude is None or hems.longitude is None:
                raise ForecastUnavailable("Für Open-Meteo fehlt der Standort in den Einstellungen.")
            return await self._open_meteo(hems.latitude, hems.longitude)
        if self._client is None:
            raise ForecastUnavailable("Ohne Home Assistant gibt es keine Wetter-Entität.")
        if hems.weather_entity is None:
            raise ForecastUnavailable("Es ist keine Wetter-Entität gewählt.")
        return await self._home_assistant(self._client, hems.weather_entity)

    async def _home_assistant(self, client: HomeAssistantClient, entity_id: str) -> dict[datetime, float]:
        try:
            state = await client.state(entity_id)
            items = await client.hourly_forecast(entity_id)
        except HomeAssistantUnavailable as err:
            raise ForecastUnavailable(f"Die Wetter-Entität {entity_id} liefert keine Stundenprognose.") from err
        fahrenheit = state is not None and state.attributes.get("temperature_unit") == "°F"
        hours: dict[datetime, float] = {}
        for item in items:
            moment, value = _time(item.get("datetime")), item.get("temperature")
            if moment is None or not _is_number(value):
                continue
            celsius = (float(value) - 32) * 5 / 9 if fahrenheit else float(value)
            hours[moment.replace(minute=0, second=0, microsecond=0)] = round(celsius, 1)
        if not hours:
            raise ForecastUnavailable(f"Die Wetter-Entität {entity_id} liefert keine Temperaturen.")
        return hours

    async def _open_meteo(self, latitude: float, longitude: float) -> dict[datetime, float]:
        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "hourly": "temperature_2m",
            "forecast_days": "3",
            "timezone": "GMT",
        }
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    self._open_meteo_url, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                ) as response,
            ):
                if response.status >= 400:
                    raise ForecastUnavailable(f"Open-Meteo antwortet mit HTTP {response.status}.")
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ForecastUnavailable("Open-Meteo ist nicht erreichbar.") from err
        hourly = data.get("hourly") if isinstance(data, dict) else None
        times = hourly.get("time") if isinstance(hourly, dict) else None
        values = hourly.get("temperature_2m") if isinstance(hourly, dict) else None
        if not isinstance(times, list) or not isinstance(values, list):
            raise ForecastUnavailable("Open-Meteo hat keine Stundenwerte geliefert.")
        hours: dict[datetime, float] = {}
        for text, value in zip(times, values, strict=False):
            moment = _time(f"{text}+00:00") if isinstance(text, str) else None
            if moment is not None and _is_number(value):
                hours[moment] = round(float(value), 1)
        if not hours:
            raise ForecastUnavailable("Open-Meteo hat keine Temperaturen geliefert.")
        return hours


class ForecastUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def _is_number(value: Any) -> TypeIs[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
