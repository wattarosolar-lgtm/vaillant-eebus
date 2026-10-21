"""Electrical energy of the heat pump per day, summed up from its power.

The real installation reports no energy counters over EEBUS, only the heat
pump's total electrical power. The meter adds up power x time between two
readings (a reading comes with every device event, at least once a minute).
Time without readings - app stopped, device unreachable - is never estimated
beyond a short gap; the day is marked incomplete instead. Days start at
midnight in Home Assistant's time zone and survive restarts in
/data/energy.json.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vaillant_eebus.state import HeatPumpState, StateService

_LOGGER = logging.getLogger(__name__)

FILE_NAME = "energy.json"
# Readings further apart than this are not bridged: the time in between counts as missing.
MAX_GAP = timedelta(minutes=3)
SAVE_EVERY = timedelta(minutes=1)
KEEP_DAYS = 400


@dataclass(frozen=True, slots=True)
class EnergyToday:
    kwh: float
    # Part of the day is not counted: the app was not running, the device was
    # unreachable or reported no power.
    incomplete: bool


@dataclass(slots=True)
class _Day:
    wh: float = 0.0
    incomplete: bool = False


@dataclass(frozen=True, slots=True)
class _Reading:
    at: datetime
    watts: float


def local_zone(name: str | None) -> tzinfo:
    """Home Assistant's time zone (the Supervisor passes it as TZ); UTC if unknown."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            _LOGGER.warning("Unknown time zone %r, energy days start at midnight UTC", name)
    else:
        _LOGGER.warning("No time zone set, energy days start at midnight UTC")
    return UTC


class EnergyMeter:
    def __init__(
        self,
        state: StateService,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._state = state
        self._path = data_dir / FILE_NAME
        self._zone = zone
        self._clock = clock
        self._days: dict[date, _Day] = {}
        # The last power reading; carried over a restart through the file.
        self._last: _Reading | None = None
        # Whether the last reading still holds (device reachable, power reported).
        self._live = False
        self._saved_at: datetime | None = None
        self._load()

    def today(self) -> EnergyToday | None:
        """Energy since midnight, including the running interval; None before the first reading."""
        now = self._clock()
        today = self._date(now)
        day = self._days.get(today)
        last = self._last
        running = 0.0
        if self._live and last is not None and timedelta(0) <= now - last.at <= MAX_GAP:
            start = max(last.at, self._midnight(today))
            running = last.watts * (now - start).total_seconds() / 3600
        if day is None and not running:
            return None
        wh = (day.wh if day else 0.0) + running
        return EnergyToday(kwh=round(wh / 1000, 3), incomplete=day.incomplete if day else False)

    def past_days(self) -> dict[date, EnergyToday]:
        """Energy of every finished day that is kept, oldest first."""
        today = self._date(self._clock())
        return {
            day: EnergyToday(kwh=round(entry.wh / 1000, 3), incomplete=entry.incomplete)
            for day, entry in sorted(self._days.items())
            if day < today
        }

    async def run(self) -> None:
        async with self._state.listen() as updates:
            while True:
                self.observe(await updates.get())

    def observe(self, heat_pump: HeatPumpState | None) -> None:
        reading = None
        if heat_pump is not None and heat_pump.available and heat_pump.connected:
            reading = heat_pump.measurements.get("power_consumption")
        if reading is None:
            self._live = False
            return
        self._add(_Reading(at=self._clock(), watts=reading.value))
        self._save_if_due()

    def close(self) -> None:
        self._save()

    # --- internals -------------------------------------------------------------

    def _add(self, reading: _Reading) -> None:
        last = self._last
        if last is not None and timedelta(0) <= reading.at - last.at <= MAX_GAP:
            self._count(last.watts, last.at, reading.at)
        else:
            # First reading, a gap or a clock jump: the time in between is missing.
            start = reading.at if last is None or last.at > reading.at else last.at
            for day in self._dates(start, reading.at):
                self._day(day).incomplete = True
        self._last = reading
        self._live = True

    def _count(self, watts: float, start: datetime, end: datetime) -> None:
        """Add watts over [start, end], split at midnight."""
        while start < end:
            day = self._date(start)
            stop = min(end, self._midnight(day + timedelta(days=1)))
            self._day(day).wh += watts * (stop - start).total_seconds() / 3600
            start = stop

    def _day(self, day: date) -> _Day:
        return self._days.setdefault(day, _Day())

    def _date(self, moment: datetime) -> date:
        return moment.astimezone(self._zone).date()

    def _midnight(self, day: date) -> datetime:
        return datetime.combine(day, time(0), tzinfo=self._zone)

    def _dates(self, start: datetime, end: datetime) -> list[date]:
        first, last = self._date(start), self._date(end)
        return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]

    def _save_if_due(self) -> None:
        now = self._clock()
        if self._saved_at is None or now - self._saved_at >= SAVE_EVERY:
            self._save()

    def _save(self) -> None:
        for old in sorted(self._days)[:-KEEP_DAYS]:
            del self._days[old]
        content: dict[str, Any] = {
            "version": 1,
            "days": {
                day.isoformat(): {"wh": round(entry.wh, 3), "incomplete": entry.incomplete}
                for day, entry in sorted(self._days.items())
            },
            "last": None if self._last is None else {"at": self._last.at.isoformat(), "watts": self._last.watts},
        }
        temporary = self._path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(content, indent=1), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            _LOGGER.exception("Could not save %s", self._path)
            return
        self._saved_at = self._clock()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            content = json.loads(self._path.read_text(encoding="utf-8"))
            days = {
                date.fromisoformat(day): _Day(wh=float(entry["wh"]), incomplete=bool(entry["incomplete"]))
                for day, entry in content["days"].items()
            }
            last = content["last"]
            reading = (
                None
                if last is None
                else _Reading(at=datetime.fromisoformat(last["at"]).astimezone(UTC), watts=float(last["watts"]))
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            corrupt = self._path.with_suffix(".json.corrupt")
            _LOGGER.warning("Unreadable %s, moved to %s; counting starts again", self._path, corrupt.name)
            os.replace(self._path, corrupt)
            return
        self._days = days
        self._last = reading
