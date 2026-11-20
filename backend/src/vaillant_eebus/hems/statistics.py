"""Home Assistant's long-term statistics as the energy management's memory.

Home Assistant's recorder keeps hourly means (temperatures, power) and changes
(energy counters) for years. The app reads them over the WebSocket (read only),
a month at a time, first for up to HISTORY_DAYS and later only the new hours,
and turns them into daily values for the house model.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

from vaillant_eebus.ha_client import HomeAssistantClient, HomeAssistantUnavailable, StatisticRow

_LOGGER = logging.getLogger(__name__)

HISTORY_DAYS = 400
CHUNK = timedelta(days=31)
# The statistics of the last hours are read again: Home Assistant compiles them a few minutes late.
OVERLAP = timedelta(hours=3)
# A day counts for learning with at least this many hours of the needed values.
MIN_HOURS = 20


@dataclass(frozen=True, slots=True)
class DayValues:
    """One local day from the hourly statistics."""

    day: date
    # Heat for heating and for hot water, kWh (sum of the hourly changes).
    heating_kwh: float | None
    dhw_kwh: float | None
    # Electrical energy of the heat pump from the mean power, kWh.
    electricity_kwh: float | None
    outdoor_c: float | None
    indoor_c: float | None
    # Hours with an outdoor and a heat value.
    hours: int


class StatisticsHistory:
    """Hourly statistics of the chosen entities, read once and then kept current."""

    def __init__(
        self,
        client: HomeAssistantClient | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        days: int = HISTORY_DAYS,
    ) -> None:
        self._client = client
        self._clock = clock
        self._days = days
        self._rows: dict[str, dict[datetime, StatisticRow]] = {}
        # Up to when each entity was read.
        self._read_until: dict[str, datetime] = {}
        self.problem: str | None = None

    def rows(self, entity_id: str | None) -> list[StatisticRow]:
        if entity_id is None:
            return []
        return [row for _, row in sorted(self._rows.get(entity_id, {}).items())]

    def means(self, entity_id: str | None) -> dict[datetime, float]:
        """Hour start -> mean, for temperatures and power."""
        return {row.start: row.mean for row in self.rows(entity_id) if row.mean is not None}

    def known(self, entity_id: str) -> bool:
        return bool(self._rows.get(entity_id))

    async def update(self, entity_ids: Iterable[str]) -> None:
        """Read what is new for these entities; the first time the whole history."""
        if self._client is None:
            self.problem = "Ohne Home Assistant gibt es keine Langzeitstatistik."
            return
        wanted = sorted(set(entity_ids))
        for entity_id in list(self._rows):
            if entity_id not in wanted:
                self._rows.pop(entity_id)
                self._read_until.pop(entity_id, None)
        now = self._clock()
        groups: dict[datetime, list[str]] = {}
        for entity_id in wanted:
            since = self._read_until.get(entity_id)
            start = since - OVERLAP if since is not None else now - timedelta(days=self._days)
            groups.setdefault(start, []).append(entity_id)
        try:
            for start, ids in groups.items():
                moment = start
                while moment < now:
                    until = min(now, moment + CHUNK)
                    answer = await self._client.statistics(ids, moment, until)
                    for entity_id, rows in answer.items():
                        stored = self._rows.setdefault(entity_id, {})
                        for row in rows:
                            stored[row.start] = row
                    moment = until
                for entity_id in ids:
                    self._read_until[entity_id] = now
        except HomeAssistantUnavailable as err:
            _LOGGER.warning("Reading the long-term statistics failed: %s", err)
            self.problem = "Die Langzeitstatistik von Home Assistant ist gerade nicht lesbar."
            return
        self.problem = None
        oldest = now - timedelta(days=self._days)
        for stored in self._rows.values():
            for start in [start for start in stored if start < oldest]:
                del stored[start]


def daily_values(
    *,
    heating_kwh: Mapping[datetime, float],
    dhw_kwh: Mapping[datetime, float],
    power_w: Mapping[datetime, float],
    outdoor_c: Mapping[datetime, float],
    indoor_c: Sequence[Mapping[datetime, float]],
    zone: tzinfo,
) -> list[DayValues]:
    """Local days from hourly values (heat in kWh per hour); only days with an outdoor value."""
    days: dict[date, list[datetime]] = {}
    for hour in outdoor_c:
        days.setdefault(hour.astimezone(zone).date(), []).append(hour)
    result = []
    for day, hours in sorted(days.items()):
        heated = [hour for hour in hours if hour in heating_kwh]
        hot_water = [hour for hour in hours if hour in dhw_kwh]
        powered = [power_w[hour] for hour in hours if hour in power_w]
        inside = [
            statistics.fmean(values) for hour in hours if (values := [room[hour] for room in indoor_c if hour in room])
        ]
        result.append(
            DayValues(
                day=day,
                heating_kwh=round(sum(heating_kwh[hour] for hour in heated), 3) if heated else None,
                dhw_kwh=round(sum(dhw_kwh[hour] for hour in hot_water), 3) if hot_water else None,
                # Missing hours are filled with the day's mean power.
                electricity_kwh=(
                    round(statistics.fmean(powered) * len(hours) / 1000, 3) if len(powered) >= MIN_HOURS else None
                ),
                outdoor_c=round(statistics.fmean(outdoor_c[hour] for hour in hours), 2),
                indoor_c=round(statistics.fmean(inside), 2) if inside else None,
                hours=len(heated),
            )
        )
    return result


def hourly_changes(rows: Sequence[StatisticRow], factor: float = 1.0) -> dict[datetime, float]:
    """Hour start -> increase of an energy counter, times factor (e.g. Wh -> kWh)."""
    return {row.start: row.change * factor for row in rows if row.change is not None and row.change >= 0}
