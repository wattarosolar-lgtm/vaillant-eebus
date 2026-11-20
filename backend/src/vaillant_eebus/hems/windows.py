"""Open windows by the fall of temperature and absolute humidity (as "würde" in 1.3.0).

There are no window contacts. Winter air outside is dry, so airing lowers the
absolute humidity (g of water per m³) together with the temperature; the
relative humidity alone would rise while the air cools. Rules of
punkt1-trv-regelung.md section 5 (start values):

- open: temperature -0.3 K and absolute humidity -0.5 g/m³ within 10 min, or
  without humidity -0.5 K within 10 min or -0.8 K within 20 min;
- closed: temperature 0.2 K above its lowest value, or the humidity rises
  again, at the latest after 30 min.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

BOTH_DROP_K = 0.3
HUMIDITY_DROP_G = 0.5
BOTH_WITHIN = timedelta(minutes=10)
ALONE_DROP_K = (0.5, 0.8)
ALONE_WITHIN = (timedelta(minutes=10), timedelta(minutes=20))
CLOSE_RISE_K = 0.2
CLOSE_RISE_G = 0.2
CLOSE_AFTER = timedelta(minutes=30)
KEEP = timedelta(minutes=20)


def absolute_humidity(celsius: float, relative: float) -> float:
    """Grams of water per m³ of air (Magnus formula)."""
    saturation = 6.112 * math.exp(17.67 * celsius / (celsius + 243.5))
    return saturation * relative * 2.1674 / (273.15 + celsius)


@dataclass(frozen=True, slots=True)
class WindowEvent:
    opened: bool
    at: datetime
    # Fall of temperature (K) and absolute humidity (g/m³) that showed it; None for humidity without a value.
    drop_k: float
    drop_g: float | None


class WindowDetector:
    """One room: feed every new temperature (and humidity) as it comes."""

    def __init__(self) -> None:
        self._readings: deque[tuple[datetime, float, float | None]] = deque()
        self.open_since: datetime | None = None
        self._lowest_k: float | None = None
        self._lowest_g: float | None = None

    @property
    def open(self) -> bool:
        return self.open_since is not None

    def feed(self, moment: datetime, celsius: float, relative: float | None) -> WindowEvent | None:
        grams = absolute_humidity(celsius, relative) if relative is not None and 0 < relative <= 100 else None
        if self.open_since is not None:
            return self._while_open(moment, celsius, grams)
        self._readings.append((moment, celsius, grams))
        while self._readings and moment - self._readings[0][0] > KEEP:
            self._readings.popleft()
        event = self._opening(moment, celsius, grams)
        if event is not None:
            self.open_since = moment
            self._lowest_k, self._lowest_g = celsius, grams
            self._readings.clear()
        return event

    def tick(self, moment: datetime) -> WindowEvent | None:
        """Close an open window after CLOSE_AFTER even without a new value."""
        if self.open_since is None or moment - self.open_since < CLOSE_AFTER:
            return None
        self.open_since = None
        self._lowest_k = self._lowest_g = None
        self._readings.clear()
        return WindowEvent(opened=False, at=moment, drop_k=0.0, drop_g=None)

    def _opening(self, moment: datetime, celsius: float, grams: float | None) -> WindowEvent | None:
        recent = [reading for reading in self._readings if moment - reading[0] <= BOTH_WITHIN]
        drop_k = max(reading[1] for reading in recent) - celsius
        if grams is not None:
            earlier = [reading[2] for reading in recent if reading[2] is not None]
            drop_g = max(earlier) - grams if earlier else None
            if drop_g is not None and drop_k >= BOTH_DROP_K and drop_g >= HUMIDITY_DROP_G:
                return WindowEvent(opened=True, at=moment, drop_k=round(drop_k, 2), drop_g=round(drop_g, 2))
        for limit, within in zip(ALONE_DROP_K, ALONE_WITHIN, strict=True):
            span = [reading[1] for reading in self._readings if moment - reading[0] <= within]
            drop = max(span) - celsius
            if drop >= limit:
                return WindowEvent(opened=True, at=moment, drop_k=round(drop, 2), drop_g=None)
        return None

    def _while_open(self, moment: datetime, celsius: float, grams: float | None) -> WindowEvent | None:
        assert self.open_since is not None and self._lowest_k is not None
        self._lowest_k = min(self._lowest_k, celsius)
        if grams is not None:
            self._lowest_g = grams if self._lowest_g is None else min(self._lowest_g, grams)
        closed = (
            celsius - self._lowest_k >= CLOSE_RISE_K
            or (grams is not None and self._lowest_g is not None and grams - self._lowest_g >= CLOSE_RISE_G)
            or moment - self.open_since >= CLOSE_AFTER
        )
        if not closed:
            return None
        self.open_since = None
        self._lowest_k = self._lowest_g = None
        self._readings.clear()
        self._readings.append((moment, celsius, grams))
        return WindowEvent(opened=False, at=moment, drop_k=0.0, drop_g=None)
