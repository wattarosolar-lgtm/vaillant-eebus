"""The heat pump's power over the last 24 hours, for the chart on the overview.

The values come from Home Assistant's history of the power sensor that this
app exports (spec 8.3). The backend turns the recorded changes into
five-minute means; the UI only draws them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

STEP = timedelta(minutes=5)
# 24 hours of five-minute buckets.
BUCKETS = 288
# A bucket with values for less than this share of its time is a gap.
MIN_COVERAGE = 0.5


class HistoryGap(StrEnum):
    """Why there is no history to show."""

    # The app runs outside Home Assistant (development).
    NO_HOME_ASSISTANT = "no_home_assistant"
    # The export group "Leistung und Energie" is off, so Home Assistant records no power.
    NOT_EXPORTED = "not_exported"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class PowerHistory:
    # Start of the first bucket; each value is the mean of step_seconds from there on.
    start: datetime | None
    step_seconds: int
    # Mean power in W per bucket, oldest first; None where nothing was recorded.
    watts: list[float | None]
    # Over the buckets with values, for the summary next to the chart.
    mean_watts: float | None = None
    peak_watts: float | None = None
    # Set when there are no values at all.
    gap: HistoryGap | None = None

    @classmethod
    def missing(cls, gap: HistoryGap) -> PowerHistory:
        return cls(start=None, step_seconds=int(STEP.total_seconds()), watts=[], gap=gap)


def first_bucket(end: datetime, *, step: timedelta = STEP, count: int = BUCKETS) -> datetime:
    """Start of the oldest bucket; the newest one contains end and is still running."""
    step_s = step.total_seconds()
    last = datetime.fromtimestamp(math.floor(end.timestamp() / step_s) * step_s, UTC)
    return last - (count - 1) * step


def bucket_means(
    changes: Sequence[tuple[datetime, str]],
    end: datetime,
    *,
    step: timedelta = STEP,
    count: int = BUCKETS,
) -> PowerHistory:
    """Time-weighted means of a sensor's states, which hold until the next change.

    States that are not numbers (unavailable, unknown) and the time before the
    first known state count as missing.
    """
    start = first_bucket(end, step=step, count=count)
    step_s = step.total_seconds()
    known = [0.0] * count
    integral = [0.0] * count
    ordered = sorted(changes, key=lambda change: change[0])
    for index, (since, state) in enumerate(ordered):
        value = _number(state)
        until = ordered[index + 1][0] if index + 1 < len(ordered) else end
        segment_start = max(since, start)
        segment_end = min(until, end)
        if value is None or segment_end <= segment_start:
            continue
        bucket = int((segment_start - start).total_seconds() // step_s)
        while bucket < count:
            bucket_start = start + bucket * step
            overlap = (min(segment_end, bucket_start + step) - max(segment_start, bucket_start)).total_seconds()
            if overlap <= 0:
                break
            known[bucket] += overlap
            integral[bucket] += overlap * value
            bucket += 1
    watts: list[float | None] = []
    for bucket in range(count):
        bucket_start = start + bucket * step
        duration = (min(bucket_start + step, end) - bucket_start).total_seconds()
        covered = duration > 0 and known[bucket] >= MIN_COVERAGE * duration
        watts.append(round(integral[bucket] / known[bucket], 1) if covered else None)
    values = [value for value in watts if value is not None]
    return PowerHistory(
        start=start,
        step_seconds=int(step_s),
        watts=watts,
        mean_watts=round(sum(values) / len(values), 1) if values else None,
        peak_watts=max(values) if values else None,
    )


def _number(state: str) -> float | None:
    try:
        value = float(state)
    except ValueError:
        return None
    return value if math.isfinite(value) else None
