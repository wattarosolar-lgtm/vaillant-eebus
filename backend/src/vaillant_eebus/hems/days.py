"""Daily values for learning the heat pump's demand, and a simple model of it.

Per local day the app keeps the mean outdoor temperature (from EEBUS) and the
mean indoor temperature (the zone averages from Home Assistant, otherwise the
zones' setpoints), sampled every minute, in /data/hems_days.json. The heat
pump's electricity per day comes from the energy meter. From at least ten
complete heating days a straight line is fitted:

    electricity per day = a * (indoor - outdoor) + b

b covers hot water and losses that do not depend on the weather. In 1.1.0 the
model is only shown as an estimate for tomorrow; the rules do not use it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_import import HomeAssistantImport
from vaillant_eebus.hems.timeline import local_midnight
from vaillant_eebus.state import StateService

_LOGGER = logging.getLogger(__name__)

FILE_NAME = "hems_days.json"
KEEP_DAYS = 400
SAMPLE_EVERY = timedelta(minutes=1)
SAVE_EVERY = timedelta(minutes=5)
# A day counts for the model with temperatures for at least this share of it.
MIN_COVERAGE = 0.9
MIN_DAYS = 10


@dataclass(slots=True)
class _Day:
    # Sums of temperature * seconds and the seconds covered.
    outdoor_sum: float = 0.0
    outdoor_s: float = 0.0
    indoor_sum: float = 0.0
    indoor_s: float = 0.0


@dataclass(frozen=True, slots=True)
class DayValues:
    day: date
    outdoor_c: float | None
    indoor_c: float | None
    # Share of the day with temperatures, 0 to 1.
    coverage: float
    kwh: float | None
    # The energy meter missed part of the day.
    incomplete: bool


@dataclass(frozen=True, slots=True)
class DemandModel:
    kwh_per_k: float
    base_kwh: float
    days: int
    # Standard deviation of the days around the line, kWh.
    spread_kwh: float

    def predict(self, indoor_c: float, outdoor_c: float) -> float:
        return max(0.0, self.kwh_per_k * (indoor_c - outdoor_c) + self.base_kwh)


class DayRecorder:
    def __init__(
        self,
        state: StateService,
        importer: HomeAssistantImport,
        energy: EnergyMeter,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._state = state
        self._importer = importer
        self._energy = energy
        self._path = data_dir / FILE_NAME
        self._zone = zone
        self._clock = clock
        self._days: dict[date, _Day] = {}
        self._saved_at: datetime | None = None
        self._load()

    async def run(self) -> None:
        while True:
            try:
                self.sample()
            except Exception:
                # A fault here must not stop the rest of the app.
                _LOGGER.exception("Recording the daily values failed")
            await asyncio.sleep(SAMPLE_EVERY.total_seconds())

    def sample(self) -> None:
        """Count the current temperatures for the last minute."""
        now = self._clock()
        heat_pump = self._state.current
        if heat_pump is None or not heat_pump.available or not heat_pump.connected:
            return
        seconds = SAMPLE_EVERY.total_seconds()
        day = self._days.setdefault(now.astimezone(self._zone).date(), _Day())
        outdoor = heat_pump.measurements.get("outdoor_temperature")
        if outdoor is not None:
            day.outdoor_sum += outdoor.value * seconds
            day.outdoor_s += seconds
        indoor = self.indoor()
        if indoor is not None:
            day.indoor_sum += indoor * seconds
            day.indoor_s += seconds
        if self._saved_at is None or now - self._saved_at >= SAVE_EVERY:
            self._save()

    def indoor(self) -> float | None:
        """The indoor temperature now: the zone averages from Home Assistant, otherwise the setpoints."""
        averages = [zone.celsius for zone in self._importer.snapshot().zones if zone.celsius is not None]
        if averages:
            return statistics.fmean(averages)
        heat_pump = self._state.current
        setpoints = [zone.setpoint.value for zone in heat_pump.zones if zone.setpoint] if heat_pump else []
        return statistics.fmean(setpoints) if setpoints else None

    def values(self) -> list[DayValues]:
        """Every finished day, oldest first."""
        energy = self._energy.past_days()
        today = self._clock().astimezone(self._zone).date()
        result = []
        for day, entry in sorted(self._days.items()):
            if day >= today:
                continue
            length = (
                local_midnight(day + timedelta(days=1), self._zone) - local_midnight(day, self._zone)
            ).total_seconds()
            used = energy.get(day)
            result.append(
                DayValues(
                    day=day,
                    outdoor_c=round(entry.outdoor_sum / entry.outdoor_s, 2) if entry.outdoor_s else None,
                    indoor_c=round(entry.indoor_sum / entry.indoor_s, 2) if entry.indoor_s else None,
                    coverage=round(min(entry.outdoor_s, entry.indoor_s) / length, 3),
                    kwh=used.kwh if used is not None else None,
                    incomplete=used.incomplete if used is not None else True,
                )
            )
        return result

    def model(self, heating_limit_c: float) -> DemandModel | None:
        """The fitted line from complete heating days; None with fewer than MIN_DAYS."""
        points = [
            (value.indoor_c - value.outdoor_c, value.kwh)
            for value in self.values()
            if value.kwh is not None
            and not value.incomplete
            and value.coverage >= MIN_COVERAGE
            and value.indoor_c is not None
            and value.outdoor_c is not None
            and value.outdoor_c < heating_limit_c
        ]
        if len(points) < MIN_DAYS:
            return None
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        if statistics.pvariance(xs) == 0:
            return None
        slope, intercept = statistics.linear_regression(xs, ys)
        residuals = [y - (slope * x + intercept) for x, y in points]
        return DemandModel(
            kwh_per_k=round(slope, 3),
            base_kwh=round(intercept, 2),
            days=len(points),
            spread_kwh=round(statistics.pstdev(residuals), 2),
        )

    def close(self) -> None:
        self._save()

    def _save(self) -> None:
        for old in sorted(self._days)[:-KEEP_DAYS]:
            del self._days[old]
        content: dict[str, Any] = {
            "version": 1,
            "days": {
                day.isoformat(): {
                    "outdoor_sum": round(entry.outdoor_sum, 1),
                    "outdoor_s": entry.outdoor_s,
                    "indoor_sum": round(entry.indoor_sum, 1),
                    "indoor_s": entry.indoor_s,
                }
                for day, entry in sorted(self._days.items())
            },
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
            self._days = {
                date.fromisoformat(day): _Day(
                    outdoor_sum=float(entry["outdoor_sum"]),
                    outdoor_s=float(entry["outdoor_s"]),
                    indoor_sum=float(entry["indoor_sum"]),
                    indoor_s=float(entry["indoor_s"]),
                )
                for day, entry in content["days"].items()
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            corrupt = self._path.with_suffix(".json.corrupt")
            _LOGGER.warning("Unreadable %s, moved to %s; learning starts again", self._path, corrupt.name)
            os.replace(self._path, corrupt)
            self._days = {}
