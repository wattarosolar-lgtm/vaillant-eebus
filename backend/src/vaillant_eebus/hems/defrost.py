"""Defrost risk per hour from the forecast, learned from the defrosts the app saw.

Frost forms on the evaporator mainly between about -3 and +5 °C with humid
air. The start values per outdoor band come from the field data of November
2026 (defrosts per running hour); every counted defrost and running hour moves
them towards what the user's heat pump really does. Humidity scales the rate:
dry air carries little water, fog and drizzle a lot.

Since 1.5.0 (K2) a run's hours count in the band of the outdoor temperature of
each hour, and each hour counts with the humidity factor of its weather: an hour
in fog weighs 1.5, a dry one 0.5. The learned rate is so per hour of normal
humidity, and the forecast's factor is applied once - not on top of a rate
that was learned in humid weather already.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vaillant_eebus.hems.cycles import Defrost, Run
from vaillant_eebus.hems.forecast import WeatherHour

# Lower bounds of the outdoor bands, °C; the first is open downwards.
BANDS = (-100.0, 0.0, 2.0, 4.0, 7.0)
BAND_NAMES = ("unter 0 °C", "0 bis 2 °C", "2 bis 4 °C", "4 bis 7 °C", "ab 7 °C")
# Defrosts per running hour (field data 09.-19.11.2026).
START_RATES = (0.8, 0.3, 0.1, 0.02, 0.0)
# Running hours the start value counts as.
PRIOR_HOURS = 10.0
DRY_BELOW = 70.0
HUMID_FROM = 85.0
DRY_FACTOR = 0.5
HUMID_FACTOR = 1.5


class RiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


LEVEL_NAMES = {RiskLevel.NONE: "keins", RiskLevel.LOW: "gering", RiskLevel.MEDIUM: "mittel", RiskLevel.HIGH: "hoch"}


@dataclass(frozen=True, slots=True)
class BandRate:
    band: str
    # Defrosts per running hour at normal humidity: what the app uses (start value moved by what it saw).
    rate: float
    defrosts: int
    # Running hours in the band as they ran, and weighted by their humidity (what the rate is learned on).
    run_hours: float
    weighted_hours: float
    learned: bool


@dataclass(frozen=True, slots=True)
class RiskHour:
    start: datetime
    # Expected defrosts per running hour.
    per_run_hour: float
    level: RiskLevel


def band_index(outdoor_c: float) -> int:
    return bisect.bisect_right(BANDS, outdoor_c) - 1


def learn_rates(runs: Sequence[Run], defrosts: Sequence[Defrost], now: datetime) -> list[BandRate]:
    hours = [0.0] * len(BANDS)
    weighted = [0.0] * len(BANDS)
    counts = [0] * len(BANDS)
    for run in runs:
        for celsius, humidity, minutes in run.hours_by_weather(now):
            if celsius is None:
                continue
            index = band_index(celsius)
            hours[index] += minutes / 60
            weighted[index] += minutes / 60 * humidity_factor(humidity)
    for item in defrosts:
        if item.outdoor_c is not None:
            counts[band_index(item.outdoor_c)] += 1
    return [
        BandRate(
            band=name,
            rate=round((counts[index] + PRIOR_HOURS * START_RATES[index]) / (weighted[index] + PRIOR_HOURS), 3),
            defrosts=counts[index],
            run_hours=round(hours[index], 1),
            weighted_hours=round(weighted[index], 1),
            learned=hours[index] >= PRIOR_HOURS,
        )
        for index, name in enumerate(BAND_NAMES)
    ]


def humidity_factor(humidity: float | None) -> float:
    if humidity is None:
        return 1.0
    if humidity < DRY_BELOW:
        return DRY_FACTOR
    if humidity >= HUMID_FROM:
        return HUMID_FACTOR
    return 1.0


def level(per_run_hour: float) -> RiskLevel:
    if per_run_hour < 0.05:
        return RiskLevel.NONE
    if per_run_hour < 0.2:
        return RiskLevel.LOW
    if per_run_hour < 0.5:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def risk(hours: Sequence[WeatherHour], rates: Sequence[BandRate]) -> list[RiskHour]:
    result = []
    for hour in hours:
        if hour.temperature_c is None:
            continue
        expected = rates[band_index(hour.temperature_c)].rate * humidity_factor(hour.humidity)
        result.append(RiskHour(start=hour.start, per_run_hour=round(expected, 3), level=level(expected)))
    return result
