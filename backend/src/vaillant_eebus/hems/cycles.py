"""Compressor starts, runs, pauses and defrosts from the heat pump's power.

The myVAILLANT integration reports the power about every minute, which shows
every start and stop and the defrost signature found in the field data
(November 2026): a run at 600-900 W drops for about 5 minutes (2-8) to a
plateau of roughly 370-520 W, then jumps for about 15 minutes to 1.1-2.3 kW
to heat the water up again. The heat pump decides on its own when it
defrosts; the app only counts, links each event with the weather and learns
from it.

Points are (time, W) as the state changed: a value holds until the next one.
A gap in the data (sensor unavailable, app stopped) is a point with NaN; runs
and pauses next to it count as starts but not for the durations.
"""

from __future__ import annotations

import bisect
import itertools
import json
import logging
import math
import os
import statistics
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# The compressor counts as running above this power (as the app's "Verdichter aktiv").
ON_W = 100.0
# Defrost signature.
RUN_MIN_W = 500.0
PLATEAU_W = (200.0, 600.0)
DROP_RATIO = 0.8
PLATEAU_MINUTES = (2.0, 15.0)
RECOVERY_W = 900.0
RECOVERY_WITHIN = timedelta(minutes=4)
RECOVERY_PEAK_WITHIN = timedelta(minutes=20)
PLATEAU_GAP = timedelta(minutes=15)
# Without a report for this long the live state (running or not) is unknown.
STALE = timedelta(minutes=10)
GAP = math.nan
KEEP_POINTS = timedelta(hours=48)
KEEP_EVENTS = timedelta(days=90)
EVENTS_FILE = "cycles.json"
# Outdoor bands for the statistics, °C (lower bounds; the first is open downwards).
BANDS = (-100.0, 0.0, 5.0, 10.0, 15.0)
BAND_NAMES = ("unter 0 °C", "0 bis 5 °C", "5 bis 10 °C", "10 bis 15 °C", "ab 15 °C")


type Points = Sequence[tuple[datetime, float]]
# A run's share of one clock hour: (outdoor °C, relative humidity %, minutes); None where unknown.
type Piece = tuple[float | None, float | None, float]


@dataclass(frozen=True, slots=True)
class Run:
    start: datetime
    # None while it runs.
    end: datetime | None
    energy_wh: float
    # Outdoor temperature at the start.
    outdoor_c: float | None = None
    # False when a gap in the data cuts the run or the pause before it.
    measured: bool = True
    # The run split at full hours with the weather of each hour (K2, 1.5.0); empty for runs stored before.
    pieces: tuple[Piece, ...] = ()

    def minutes(self, now: datetime) -> float:
        return ((self.end or now) - self.start).total_seconds() / 60

    def hours_by_weather(self, now: datetime) -> tuple[Piece, ...]:
        """The run's minutes with the outdoor temperature and humidity they ran at (else all at the start's)."""
        return self.pieces or ((self.outdoor_c, None, self.minutes(now)),)


@dataclass(frozen=True, slots=True)
class Defrost:
    at: datetime
    minutes: float
    peak_w: float
    outdoor_c: float | None = None
    humidity: float | None = None


@dataclass(frozen=True, slots=True)
class DayCycles:
    day: date
    starts: int
    run_minutes: float | None
    pause_minutes: float | None
    defrosts: int
    outdoor_c: float | None


@dataclass(frozen=True, slots=True)
class BandCycles:
    band: str
    starts: int
    run_minutes: float | None
    pause_minutes: float | None
    defrosts: int
    run_hours: float


@dataclass(frozen=True, slots=True)
class CycleStats:
    today: DayCycles
    days: list[DayCycles]
    bands: list[BandCycles]
    running: bool | None
    # Since when the compressor runs or stands still.
    since: datetime | None
    last_defrost: Defrost | None


def value_at(points: Points, moment: datetime) -> float | None:
    """The value that held at moment (step function); None in a gap."""
    index = bisect.bisect_right([point[0] for point in points], moment) - 1
    if index < 0 or math.isnan(points[index][1]):
        return None
    return points[index][1]


def detect_runs(points: Points, now: datetime) -> list[Run]:
    """Runs of the compressor: intervals with power at or above ON_W."""
    runs: list[Run] = []
    start: datetime | None = None
    energy = 0.0
    measured = True
    for index, (moment, watts) in enumerate(points):
        following = points[index + 1][0] if index + 1 < len(points) else now
        on = watts >= ON_W
        if on and start is None:
            start, energy = moment, 0.0
            # Only a run after a known pause has a measured start.
            measured = index > 0 and not math.isnan(points[index - 1][1])
        if on:
            energy += watts * (following - moment).total_seconds() / 3600
        elif start is not None:
            runs.append(
                Run(start=start, end=moment, energy_wh=round(energy, 1), measured=measured and not math.isnan(watts))
            )
            start = None
    if start is not None:
        runs.append(Run(start=start, end=None, energy_wh=round(energy, 1), measured=measured))
    return runs


def detect_defrosts(points: Points) -> list[Defrost]:
    """Defrosts by their signature (see the module's description)."""
    defrosts = []
    index = 1
    while index < len(points):
        moment, watts = points[index]
        before_time, before = points[index - 1]
        if (
            before >= RUN_MIN_W
            and PLATEAU_W[0] <= watts <= PLATEAU_W[1]
            and watts < DROP_RATIO * before
            and moment - before_time <= KEEP_POINTS
        ):
            end = index
            while (
                end < len(points)
                and PLATEAU_W[0] <= points[end][1] <= PLATEAU_W[1]
                and points[end][0] - points[end - 1][0] <= PLATEAU_GAP
            ):
                end += 1
            if end < len(points):
                recovered_at = points[end][0]
                minutes = (recovered_at - moment).total_seconds() / 60
                after = [value for time, value in points[end:] if time <= recovered_at + RECOVERY_WITHIN]
                if PLATEAU_MINUTES[0] <= minutes <= PLATEAU_MINUTES[1] and after and max(after) >= RECOVERY_W:
                    peak = max(value for time, value in points[end:] if time <= recovered_at + RECOVERY_PEAK_WITHIN)
                    defrosts.append(Defrost(at=moment, minutes=round(minutes, 1), peak_w=peak))
            index = max(end, index + 1)
        else:
            index += 1
    return defrosts


def band_index(outdoor_c: float) -> int:
    return bisect.bisect_right(BANDS, outdoor_c) - 1


def split_run(
    run: Run, outdoor: Points, humidity_at: Callable[[datetime], float | None], now: datetime
) -> tuple[Piece, ...]:
    """The run cut at full hours; each piece with the outdoor value in its middle and that hour's humidity.

    A run that starts at +1 °C and runs into the frost counts its later hours there, not all at its start (K2).
    """
    end = run.end or now
    pieces: list[Piece] = []
    moment = run.start
    while moment < end:
        cut = min(end, moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        middle = moment + (cut - moment) / 2
        celsius = value_at(outdoor, middle) if outdoor else None
        pieces.append((celsius, humidity_at(middle), round((cut - moment).total_seconds() / 60, 2)))
        moment = cut
    return tuple(pieces)


def summarize(
    runs: Sequence[Run], defrosts: Sequence[Defrost], now: datetime, zone: tzinfo, *, days: int = 14
) -> tuple[list[DayCycles], list[BandCycles]]:
    """Per local day (newest first, today included) and per outdoor band."""
    pauses = _pauses(runs)
    today = now.astimezone(zone).date()
    per_day = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        in_day = [run for run in runs if run.start.astimezone(zone).date() == day]
        day_pauses = [minutes for start, minutes in pauses if start.astimezone(zone).date() == day]
        outdoor = [run.outdoor_c for run in in_day if run.outdoor_c is not None]
        per_day.append(
            DayCycles(
                day=day,
                starts=len(in_day),
                run_minutes=_median([run.minutes(now) for run in in_day if run.measured and run.end is not None]),
                pause_minutes=_median(day_pauses),
                defrosts=sum(1 for item in defrosts if item.at.astimezone(zone).date() == day),
                outdoor_c=round(statistics.fmean(outdoor), 1) if outdoor else None,
            )
        )
    bands = []
    for index, name in enumerate(BAND_NAMES):
        in_band = [run for run in runs if run.outdoor_c is not None and band_index(run.outdoor_c) == index]
        starts = {run.start for run in in_band}
        # Running hours by the outdoor temperature of each hour, not of the run's start (K2).
        band_minutes = sum(
            piece_minutes
            for run in runs
            for celsius, _humidity, piece_minutes in run.hours_by_weather(now)
            if celsius is not None and band_index(celsius) == index
        )
        bands.append(
            BandCycles(
                band=name,
                starts=len(in_band),
                run_minutes=_median([run.minutes(now) for run in in_band if run.measured and run.end is not None]),
                pause_minutes=_median([minutes for start, minutes in pauses if start in starts]),
                defrosts=sum(
                    1 for item in defrosts if item.outdoor_c is not None and band_index(item.outdoor_c) == index
                ),
                run_hours=round(band_minutes / 60, 1),
            )
        )
    return per_day, bands


def _pauses(runs: Sequence[Run]) -> list[tuple[datetime, float]]:
    """(start of the run after the pause, minutes of the pause) for measured pauses."""
    result = []
    for before, after in itertools.pairwise(runs):
        if before.end is not None and before.measured and after.measured:
            result.append((after.start, (after.start - before.end).total_seconds() / 60))
    return result


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


@dataclass
class CycleTracker:
    """Keeps the last two days of power and outdoor values and the events of 90 days."""

    data_dir: Path
    zone: tzinfo = UTC
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    # Humidity at a moment (from the weather), for the defrost statistics.
    humidity_at: Callable[[datetime], float | None] = field(default=lambda _moment: None)
    power: list[tuple[datetime, float]] = field(default_factory=list)
    outdoor: list[tuple[datetime, float]] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)
    defrosts: list[Defrost] = field(default_factory=list)
    # Last time the power was reported, also unchanged.
    last_seen: datetime | None = None
    _dirty: bool = False

    def __post_init__(self) -> None:
        self._load()

    @property
    def path(self) -> Path:
        return self.data_dir / EVENTS_FILE

    def feed_power(self, moment: datetime, watts: float | None) -> None:
        """A reported power; None when it is not available (a gap)."""
        _append(self.power, moment, GAP if watts is None else watts)
        if watts is not None:
            self.last_seen = moment if self.last_seen is None else max(self.last_seen, moment)

    def feed_outdoor(self, moment: datetime, celsius: float) -> None:
        _append(self.outdoor, moment, celsius)

    def import_history(self, power: Points, outdoor: Points) -> None:
        """Values read from Home Assistant's history after a start (NaN power: unavailable).

        Detects runs and defrosts over the whole history once; what it finds
        replaces the stored events of that time. The last two days are kept for
        the live counting.
        """
        if not power:
            return
        now = self.clock()
        start = power[0][0]
        outdoor = list(outdoor)
        runs = [
            replace(
                run,
                outdoor_c=value_at(outdoor, run.start),
                measured=run.measured and run.start > start,
                pieces=split_run(run, outdoor, self.humidity_at, now),
            )
            for run in detect_runs(power, now)
            if run.end is not None
        ]
        defrosts = [
            replace(item, outdoor_c=value_at(outdoor, item.at), humidity=self.humidity_at(item.at))
            for item in detect_defrosts(power)
        ]
        self.runs = sorted([run for run in self.runs if run.start < start] + runs, key=lambda run: run.start)
        self.defrosts = sorted([item for item in self.defrosts if item.at < start] + defrosts, key=lambda item: item.at)
        self.power = _merge(self.power, power)
        self.outdoor = _merge(self.outdoor, outdoor)
        self._dirty = True

    def update(self) -> list[Defrost]:
        """Detect again over the kept values; returns defrosts found for the first time."""
        now = self.clock()
        self.power = [point for point in self.power if point[0] >= now - KEEP_POINTS]
        self.outdoor = [point for point in self.outdoor if point[0] >= now - KEEP_POINTS]
        if not self.power:
            return []
        window = self.power[0][0]
        fresh_runs = [
            Run(
                start=run.start,
                end=run.end,
                energy_wh=run.energy_wh,
                outdoor_c=value_at(self.outdoor, run.start),
                # The first run in the window may have started before it.
                measured=run.measured and run.start > window,
                pieces=split_run(run, self.outdoor, self.humidity_at, now),
            )
            for run in detect_runs(self.power, now)
        ]
        known = {item.at for item in self.defrosts}
        fresh_defrosts = [
            Defrost(
                at=item.at,
                minutes=item.minutes,
                peak_w=item.peak_w,
                outdoor_c=value_at(self.outdoor, item.at),
                humidity=self.humidity_at(item.at),
            )
            for item in detect_defrosts(self.power)
        ]
        new = [item for item in fresh_defrosts if item.at not in known]
        oldest = now - KEEP_EVENTS
        # The window decides about everything that starts in it; older events stay as stored.
        older = [run for run in self.runs if oldest <= run.start < window]
        if fresh_runs and fresh_runs[0].start == window and older and (older[-1].end or now) >= window:
            # A run across the window's start: the stored one knows when it began.
            fresh_runs = fresh_runs[1:]
        runs = older + fresh_runs
        defrosts = [item for item in self.defrosts if item.at < window and item.at >= oldest] + fresh_defrosts
        if runs != self.runs or defrosts != self.defrosts:
            self.runs, self.defrosts = runs, defrosts
            self._dirty = True
        return new

    def stats(self) -> CycleStats:
        now = self.clock()
        per_day, bands = summarize(self.runs, self.defrosts, now, self.zone)
        last = self.power[-1] if self.power else None
        running = None
        if (
            last is not None
            and not math.isnan(last[1])
            and self.last_seen is not None
            and now - self.last_seen <= STALE
        ):
            running = last[1] >= ON_W
        since = None
        if running is not None:
            # The first point of the current state, after the last one of the other state.
            for moment, watts in reversed(self.power):
                if math.isnan(watts) or (watts >= ON_W) != running:
                    break
                since = moment
        return CycleStats(
            today=per_day[0],
            days=per_day,
            bands=bands,
            running=running,
            since=since,
            last_defrost=self.defrosts[-1] if self.defrosts else None,
        )

    def save(self) -> None:
        if not self._dirty:
            return
        payload = {
            "runs": [
                [
                    run.start.isoformat(),
                    run.end.isoformat() if run.end else None,
                    run.energy_wh,
                    run.outdoor_c,
                    run.measured,
                    [list(piece) for piece in run.pieces],
                ]
                for run in self.runs
                if run.end is not None
            ],
            "defrosts": [
                [item.at.isoformat(), item.minutes, item.peak_w, item.outdoor_c, item.humidity]
                for item in self.defrosts
            ],
        }
        try:
            _write_json(self.path, payload)
            self._dirty = False
        except OSError as err:
            _LOGGER.warning("Saving %s failed: %s", self.path, err)

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            _LOGGER.warning("%s unreadable (%s), starting anew", self.path, err)
            return
        try:
            self.runs = [
                Run(
                    start=datetime.fromisoformat(item[0]),
                    end=datetime.fromisoformat(item[1]) if item[1] else None,
                    energy_wh=float(item[2]),
                    outdoor_c=_optional(item[3]),
                    measured=bool(item[4]),
                    pieces=_pieces(item[5]) if len(item) > 5 else (),
                )
                for item in raw.get("runs", [])
            ]
            self.defrosts = [
                Defrost(
                    at=datetime.fromisoformat(item[0]),
                    minutes=float(item[1]),
                    peak_w=float(item[2]),
                    outdoor_c=_optional(item[3]),
                    humidity=_optional(item[4]),
                )
                for item in raw.get("defrosts", [])
            ]
        except (TypeError, ValueError, IndexError, AttributeError) as err:
            _LOGGER.warning("%s has an unknown format (%s), starting anew", self.path, err)
            self.runs, self.defrosts = [], []


def _append(points: list[tuple[datetime, float]], moment: datetime, value: float) -> None:
    if points and moment <= points[-1][0]:
        if moment == points[-1][0]:
            points[-1] = (moment, value)
        return
    last = points[-1][1] if points else None
    if last is not None and (last == value or (math.isnan(last) and math.isnan(value))):
        # Unchanged: the step function holds it already.
        return
    points.append((moment, value))


def _merge(kept: Points, read: Points) -> list[tuple[datetime, float]]:
    merged = {moment: value for moment, value in read}
    merged.update({moment: value for moment, value in kept})
    return sorted(merged.items())


def _optional(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _pieces(raw: Any) -> tuple[Piece, ...]:
    if not isinstance(raw, list):
        return ()
    pieces: list[Piece] = []
    for item in raw:
        if isinstance(item, list) and len(item) == 3 and (minutes := _optional(item[2])) is not None:
            pieces.append((_optional(item[0]), _optional(item[1]), minutes))
    return tuple(pieces)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
