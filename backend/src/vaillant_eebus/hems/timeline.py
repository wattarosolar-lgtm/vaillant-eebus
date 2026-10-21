"""The planner's time grid: quarter hours from now until the end of tomorrow.

Slots are kept in UTC; days, price windows and the daily means follow Home
Assistant's local time, so the change to winter time (a 25-hour day) needs no
special case.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta, tzinfo

SLOT = timedelta(minutes=15)
SLOTS_PER_HOUR = 4


def slot_start(moment: datetime) -> datetime:
    """Start of the quarter hour that contains moment, in UTC."""
    step = SLOT.total_seconds()
    return datetime.fromtimestamp(math.floor(moment.timestamp() / step) * step, UTC)


def horizon(now: datetime, zone: tzinfo) -> list[datetime]:
    """Slot starts from the current quarter hour until local midnight after tomorrow."""
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    end = local_midnight(tomorrow + timedelta(days=1), zone)
    slots = []
    moment = slot_start(now)
    while moment < end:
        slots.append(moment)
        moment += SLOT
    return slots


def local_midnight(day: date, zone: tzinfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)


def local_day(moment: datetime, zone: tzinfo) -> date:
    return moment.astimezone(zone).date()
