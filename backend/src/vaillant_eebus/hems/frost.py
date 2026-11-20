"""The Vaillant controller's own frost protection, recognized from the outdoor temperature.

sensoCOMFORT VRC 720 (manual, checked 20.11.2026): when it is colder than 4 °C
outside for more than 4 hours, the controller switches the heat pump on and
holds every room at 5 °C at least - also in the mode "Aus" and in the eco
setback. The app never fights it: switching zone 1 off and long pauses help
little then, and heat in such a pause is no change by hand.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from vaillant_eebus.hems.forecast import WeatherHour

THRESHOLD_C = 4.0
DELAY = timedelta(hours=4)


@dataclass(frozen=True, slots=True)
class FrostState:
    active: bool
    # Since when it is continuously colder than the threshold; None when it is not.
    below_since: datetime | None


def frost_state(outdoor: Sequence[tuple[datetime, float]], now: datetime) -> FrostState:
    """From the outdoor values (a value holds until the next one; NaN is a gap)."""
    since = None
    for moment, celsius in reversed(outdoor):
        if math.isnan(celsius) or celsius >= THRESHOLD_C:
            break
        since = moment
    if since is None or (outdoor and outdoor[-1][1] >= THRESHOLD_C):
        return FrostState(active=False, below_since=None)
    return FrostState(active=now - since > DELAY, below_since=since)


def frost_hours(hours: Sequence[WeatherHour], state: FrostState) -> set[datetime]:
    """Hour starts in which the frost protection will be active, by the forecast."""
    active = set()
    since = state.below_since
    for hour in hours:
        if hour.temperature_c is None:
            continue
        if hour.temperature_c < THRESHOLD_C:
            since = since or hour.start
            if hour.start + timedelta(hours=1) - since > DELAY:
                active.add(hour.start)
        else:
            since = None
    return active
