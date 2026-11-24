"""Open windows by humidity, temperature and the thermostat's sensor (1.4.3, plan-1.4.3.md Teil B).

Winter air outside is dry, so airing lowers the absolute humidity (g of water
per m³) together with the temperature; the relative humidity alone would rise
while the air cools. The room sensors (e.g. Aqara WSDCGQ11LM) report only on a
change of about 0.5 K or 6 % relative humidity, else about hourly, so every
report is compared with the last one, however old (up to 90 minutes).

Opening, the first that holds:
- humidity: the absolute humidity fell by at least S_g, at least 1 g/m³ per
  hour over the time since the last report, and the temperature did not rise.
  S_g = 0.2 x (inside - outside humidity), 0.5 to 1.0 g/m³; below 1.5 g/m³ of
  difference (mild and damp) the humidity does not count;
- temperature: -0.5 K against a report at most 15 minutes old, or -0.8 K at
  most 30 minutes, while the room is at least 8 K warmer than outside;
- thermostat: its own sensor (the W600 report about every two minutes while
  airing) fell by 1 K against its highest value of the last 8 minutes and the
  room sensor did not rise meanwhile; a room measured at its thermostat needs
  1.5 K. Not within 30 minutes after a command of the app or the valve closing
  (the sensor at the radiator falls by itself then).

Closing, at least 3 minutes after opening: humidity +0.3 g/m³, room +0.2 K or
thermostat +0.5 K above its lowest value while open; at the latest after 30
minutes. A window contact, where the room has one, is handled by the energy
management and takes precedence over these rules.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Opening by humidity.
REFERENCE_MAX = timedelta(minutes=90)
HUMIDITY_SHARE = 0.2
HUMIDITY_MIN_G = 0.5
HUMIDITY_MAX_G = 1.0
HUMIDITY_DIFF_MIN_G = 1.5
HUMIDITY_RATE_G_PER_H = 1.0
# Without an outdoor temperature the humidity counts with this threshold.
HUMIDITY_DEFAULT_G = 0.7
# The outdoor humidity when the forecast has none.
OUTDOOR_RELATIVE_DEFAULT = 85.0
# A temperature that rose by more than this is a rise.
RISE_TOLERANCE_K = 0.05
# Opening by temperature: (fall, against a report at most this old); only this much warmer than outside.
TEMPERATURE_RULES = ((0.5, timedelta(minutes=15)), (0.8, timedelta(minutes=30)))
TEMPERATURE_SPREAD_K = 8.0
# Opening by the thermostat's sensor.
TRV_DROP_K = 1.0
TRV_ALONE_DROP_K = 1.5
TRV_WITHIN = timedelta(minutes=8)
# Closing.
CLOSE_RISE_G = 0.3
CLOSE_RISE_K = 0.2
CLOSE_TRV_K = 0.5
CLOSE_EARLIEST = timedelta(minutes=3)
CLOSE_AFTER = timedelta(minutes=30)
# Temperature and humidity of one report arrive as two state changes a moment apart: one report.
SAME_REPORT = timedelta(seconds=10)
KEEP = timedelta(minutes=90)
MINUS = "\u2212"


def absolute_humidity(celsius: float, relative: float) -> float:
    """Grams of water per m³ of air (Magnus formula)."""
    saturation = 6.112 * math.exp(17.67 * celsius / (celsius + 243.5))
    return saturation * relative * 2.1674 / (273.15 + celsius)


@dataclass(frozen=True, slots=True)
class Outside:
    """The weather the thresholds follow: outdoor temperature and relative humidity (forecast)."""

    celsius: float | None = None
    relative: float | None = None

    @property
    def grams(self) -> float | None:
        if self.celsius is None:
            return None
        relative = self.relative if self.relative is not None and 0 < self.relative <= 100 else None
        return absolute_humidity(self.celsius, relative if relative is not None else OUTDOOR_RELATIVE_DEFAULT)


def humidity_threshold(inside_g: float, outside: Outside) -> float | None:
    """S_g: the fall of humidity that shows an open window; None when the humidity does not count."""
    outside_g = outside.grams
    if outside_g is None:
        return HUMIDITY_DEFAULT_G
    difference = inside_g - outside_g
    if difference < HUMIDITY_DIFF_MIN_G:
        return None
    return round(min(HUMIDITY_MAX_G, max(HUMIDITY_MIN_G, HUMIDITY_SHARE * difference)), 2)


class Signal(StrEnum):
    HUMIDITY = "humidity"
    TEMPERATURE = "temperature"
    THERMOSTAT = "thermostat"
    CONTACT = "contact"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class WindowEvent:
    opened: bool
    at: datetime
    signal: Signal
    # The change that showed it (opening: a fall, positive; closing: a rise) and over how many minutes.
    change_k: float | None = None
    change_g: float | None = None
    minutes: float | None = None

    def describe(self) -> str:
        """The signal in German, e.g. "Feuchte (minus) 1,3 g/m³ in 34 min"."""
        sign = MINUS if self.opened else "+"
        over = f" in {self.minutes:.0f} min" if self.minutes is not None else ""
        match self.signal:
            case Signal.CONTACT:
                return "Fensterkontakt"
            case Signal.TIMEOUT:
                return f"nach {CLOSE_AFTER.total_seconds() / 60:.0f} min angenommen"
            case Signal.HUMIDITY:
                return f"Feuchte {sign}{_de(self.change_g, 1)} g/m³{over}"
            case Signal.TEMPERATURE:
                return f"Raum {sign}{_de(self.change_k, 1)} K{over}"
            case Signal.THERMOSTAT:
                return f"Thermostat {sign}{_de(self.change_k, 1)} K{over}"


@dataclass(frozen=True, slots=True)
class _Report:
    at: datetime
    celsius: float
    grams: float | None
    # Against the report before: the temperature rose.
    rose: bool = False


class WindowDetector:
    """One room: feed every report of the room sensor and every new value of the thermostat's sensor.

    alone: the room is measured at its thermostat (no room sensor); only the thermostat rule applies.
    """

    def __init__(self, *, alone: bool = False) -> None:
        self.alone = alone
        self._reports: deque[_Report] = deque()
        self._trv: deque[tuple[datetime, float]] = deque()
        self.open_since: datetime | None = None
        self.last_event: WindowEvent | None = None
        self._lowest_k: float | None = None
        self._lowest_g: float | None = None
        self._lowest_trv: float | None = None

    @property
    def open(self) -> bool:
        return self.open_since is not None

    @property
    def last_grams(self) -> float | None:
        """The room's absolute humidity at its last report."""
        return self._reports[-1].grams if self._reports else None

    def feed(
        self, moment: datetime, celsius: float, relative: float | None, outside: Outside | None = None
    ) -> WindowEvent | None:
        """A report of the room sensor (temperature, relative humidity)."""
        grams = absolute_humidity(celsius, relative) if relative is not None and 0 < relative <= 100 else None
        if self._reports and moment - self._reports[-1].at <= SAME_REPORT:
            # The other half of the same report: it replaces the first half.
            self._reports.pop()
        reference = self._reports[-1] if self._reports else None
        rose = reference is not None and celsius - reference.celsius > RISE_TOLERANCE_K
        report = _Report(moment, celsius, grams, rose)
        self._reports.append(report)
        while len(self._reports) > 2 and moment - self._reports[0].at > KEEP:
            self._reports.popleft()
        if self.open_since is not None:
            return self._while_open(moment, celsius=celsius, grams=grams)
        if self.alone or reference is None:
            return None
        event = self._opening(report, reference, outside or Outside())
        if event is not None:
            self._open(event)
        return event

    def feed_trv(self, moment: datetime, celsius: float, *, guarded: bool = False) -> WindowEvent | None:
        """A new value of the thermostat's own sensor; guarded: shortly after a command or the valve closing."""
        if self._trv and abs(self._trv[-1][1] - celsius) < 0.01:
            return None
        self._trv.append((moment, celsius))
        # Keep each value while it was valid within the last KEEP (it holds until the next one).
        while len(self._trv) > 1 and moment - self._trv[1][0] > KEEP:
            self._trv.popleft()
        if self.open_since is not None:
            return self._while_open(moment, trv=celsius)
        if guarded:
            return None
        highest = self._trv_highest(moment)
        drop = highest - celsius if highest is not None else 0.0
        if drop < (TRV_ALONE_DROP_K if self.alone else TRV_DROP_K) - 1e-6 or self._room_rose(moment):
            return None
        event = WindowEvent(True, moment, Signal.THERMOSTAT, change_k=round(drop, 2))
        self._open(event)
        return event

    def tick(self, moment: datetime) -> WindowEvent | None:
        """Close an open window after CLOSE_AFTER even without a new value."""
        if self.open_since is None or moment - self.open_since < CLOSE_AFTER:
            return None
        return self._close(WindowEvent(False, moment, Signal.TIMEOUT))

    # --- helpers --------------------------------------------------------------------------------------

    def _opening(self, report: _Report, reference: _Report, outside: Outside) -> WindowEvent | None:
        age = report.at - reference.at
        minutes = round(age.total_seconds() / 60, 1)
        if report.grams is not None and reference.grams is not None and age <= REFERENCE_MAX:
            threshold = humidity_threshold(reference.grams, outside)
            drop_g = reference.grams - report.grams
            hours = max(age, timedelta(minutes=1)).total_seconds() / 3600
            if (
                threshold is not None
                and drop_g >= threshold - 1e-6
                and drop_g / hours >= HUMIDITY_RATE_G_PER_H
                and not report.rose
            ):
                return WindowEvent(
                    True,
                    report.at,
                    Signal.HUMIDITY,
                    change_k=round(reference.celsius - report.celsius, 2),
                    change_g=round(drop_g, 2),
                    minutes=minutes,
                )
        if outside.celsius is not None and report.celsius - outside.celsius < TEMPERATURE_SPREAD_K:
            return None
        earlier = list(self._reports)[:-1]
        for limit, within in TEMPERATURE_RULES:
            span = [item for item in earlier if report.at - item.at <= within]
            if not span:
                continue
            warmest = max(span, key=lambda item: item.celsius)
            drop = warmest.celsius - report.celsius
            if drop >= limit - 1e-6:
                return WindowEvent(
                    True,
                    report.at,
                    Signal.TEMPERATURE,
                    change_k=round(drop, 2),
                    minutes=round((report.at - warmest.at).total_seconds() / 60, 1),
                )
        return None

    def _trv_highest(self, moment: datetime) -> float | None:
        """The highest earlier value of the thermostat's sensor that was valid within TRV_WITHIN."""
        values = list(self._trv)
        valid = [value for index, (_, value) in enumerate(values[:-1]) if moment - values[index + 1][0] <= TRV_WITHIN]
        return max(valid) if valid else None

    def _room_rose(self, moment: datetime) -> bool:
        return any(report.rose for report in self._reports if moment - report.at <= TRV_WITHIN)

    def _open(self, event: WindowEvent) -> None:
        self.open_since = event.at
        self.last_event = event
        self._lowest_k = self._reports[-1].celsius if self._reports else None
        self._lowest_g = self._reports[-1].grams if self._reports else None
        self._lowest_trv = self._trv[-1][1] if self._trv else None

    def _while_open(
        self, moment: datetime, *, celsius: float | None = None, grams: float | None = None, trv: float | None = None
    ) -> WindowEvent | None:
        assert self.open_since is not None
        rise: WindowEvent | None = None
        early = moment - self.open_since < CLOSE_EARLIEST
        if grams is not None:
            if self._lowest_g is not None and grams - self._lowest_g >= CLOSE_RISE_G - 1e-6 and not early:
                rise = WindowEvent(False, moment, Signal.HUMIDITY, change_g=round(grams - self._lowest_g, 2))
            self._lowest_g = grams if self._lowest_g is None else min(self._lowest_g, grams)
        if celsius is not None:
            warmer = self._lowest_k is not None and celsius - self._lowest_k >= CLOSE_RISE_K - 1e-6
            if rise is None and warmer and not early and self._lowest_k is not None:
                rise = WindowEvent(False, moment, Signal.TEMPERATURE, change_k=round(celsius - self._lowest_k, 2))
            self._lowest_k = celsius if self._lowest_k is None else min(self._lowest_k, celsius)
        if trv is not None:
            if self._lowest_trv is not None and trv - self._lowest_trv >= CLOSE_TRV_K - 1e-6 and not early:
                rise = WindowEvent(False, moment, Signal.THERMOSTAT, change_k=round(trv - self._lowest_trv, 2))
            self._lowest_trv = trv if self._lowest_trv is None else min(self._lowest_trv, trv)
        if rise is None and moment - self.open_since >= CLOSE_AFTER:
            rise = WindowEvent(False, moment, Signal.TIMEOUT)
        if rise is None:
            return None
        return self._close(rise)

    def _close(self, event: WindowEvent) -> WindowEvent:
        self.open_since = None
        self.last_event = event
        self._lowest_k = self._lowest_g = self._lowest_trv = None
        # The next fall is measured from the last report.
        while len(self._reports) > 1:
            self._reports.popleft()
        while len(self._trv) > 1:
            self._trv.popleft()
        return event


def _de(value: float | None, digits: int) -> str:
    if value is None:
        return "?"
    return f"{value:.{digits}f}".replace(".", ",")
