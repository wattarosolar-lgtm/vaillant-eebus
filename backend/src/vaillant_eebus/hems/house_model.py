"""The house model: how much heat the house needs, learned from real heat values.

Heat for heating per day = H * (inside - outside) * 24 h - internal gains
- sun, with H (heat loss in W/K) and the gains learned by a straight line over
past heating days, and the sun's share learned from the days with known
radiation. The performance factor (Arbeitszahl) comes from real heat and
electricity per day, never from the model itself. Until enough days are known
the heat load of the static balancing gives a start value. Hourly demand for
48 hours follows from the forecast; per zone it is split by the rooms' heat
loads (estimated).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from vaillant_eebus.hems.statistics import DayValues

MIN_DAYS = 10
MIN_WEATHER_DAYS = 14
# Days with less heat say little about the heat loss (heating nearly off).
MIN_HEAT_KWH = 1.0
# The inside temperature used when no room reports one.
DEFAULT_INSIDE_C = 21.0
COP_LIMITS = (1.5, 7.0)
# The line holds only this far beyond the outdoor temperatures it was learned from; further out it stays flat (K1).
COP_EXTEND_K = 2.0
MAX_LAG_H = 8
# Hours summed for finding the delay; shorter windows drown in the heat pump's cycling.
LAG_WINDOW_H = 6


@dataclass(frozen=True, slots=True)
class CopModel:
    """Arbeitszahl = a + b * outside temperature, from real heat and electricity per day.

    Learned mostly on mild days, the line is not drawn far into the frost: beyond COP_EXTEND_K outside the learned
    days' outdoor temperatures (low_c ... high_c) it keeps its value at that edge.
    """

    a: float
    b: float
    days: int
    low_c: float | None = None
    high_c: float | None = None

    def at(self, outdoor_c: float) -> float:
        if self.low_c is not None:
            outdoor_c = max(self.low_c - COP_EXTEND_K, outdoor_c)
        if self.high_c is not None:
            outdoor_c = min(self.high_c + COP_EXTEND_K, outdoor_c)
        return max(COP_LIMITS[0], min(COP_LIMITS[1], self.a + self.b * outdoor_c))


@dataclass(frozen=True, slots=True)
class HouseModel:
    # Heat loss of the house, W/K.
    loss_w_per_k: float
    # Internal gains (people, devices), W; lower the demand.
    gains_w: float
    # Heat the sun brings per W/m² of global radiation (W per W/m², an effective window area).
    sun_m2: float | None
    days: int
    # Share of the daily heat the line explains (R²) and the scatter of a single day, kWh.
    r2: float | None
    spread_kwh: float | None
    # False: start value from the static balancing, nothing learned yet.
    learned: bool
    lag_h: float | None = None
    cop: CopModel | None = None

    def heat_w(self, inside_c: float, outdoor_c: float, radiation_w: float | None = None) -> float:
        """Heat the house needs in this hour, W (never below 0)."""
        demand = self.loss_w_per_k * (inside_c - outdoor_c) - self.gains_w
        if self.sun_m2 is not None and radiation_w is not None:
            demand -= self.sun_m2 * radiation_w
        return max(0.0, demand)


@dataclass(frozen=True, slots=True)
class DemandHour:
    start: datetime
    heat_w: float
    electricity_w: float | None
    # Zone ID -> heat, W (split by the rooms' heat loads, estimated).
    zones: Mapping[str, float]


def start_model(heat_load_w: float | None, design_outdoor_c: float | None) -> HouseModel | None:
    """The heat load of the static balancing as a first guess (usually too high)."""
    if heat_load_w is None or design_outdoor_c is None or design_outdoor_c >= 15:
        return None
    return HouseModel(
        loss_w_per_k=round(heat_load_w / (20.0 - design_outdoor_c), 1),
        gains_w=0.0,
        sun_m2=None,
        days=0,
        r2=None,
        spread_kwh=None,
        learned=False,
    )


def fit_house(
    days: Sequence[DayValues],
    *,
    heating_limit_c: float,
    radiation_wh: Mapping[date, float] | None = None,
) -> HouseModel | None:
    """Learn H and the gains from heating days; None with fewer than MIN_DAYS."""
    usable = [
        day
        for day in days
        if day.heating_kwh is not None
        and day.heating_kwh >= MIN_HEAT_KWH
        and day.outdoor_c is not None
        and day.outdoor_c < heating_limit_c
        and day.hours >= 20
    ]
    if len(usable) < MIN_DAYS:
        return None
    xs = [((day.indoor_c or DEFAULT_INSIDE_C) - (day.outdoor_c or 0.0)) * 24 for day in usable]
    ys = [(day.heating_kwh or 0.0) * 1000 for day in usable]
    line = _line(xs, ys)
    if line is None or line[0] <= 0:
        return None
    slope, intercept = line
    sun_m2 = None
    sunny = [index for index, day in enumerate(usable) if radiation_wh and day.day in radiation_wh]
    if radiation_wh and len(sunny) >= MIN_WEATHER_DAYS:
        # With the radiation known: heat, the difference and the sun together, on those days.
        joint = _plane(
            [xs[index] for index in sunny],
            [radiation_wh[usable[index].day] for index in sunny],
            [ys[index] for index in sunny],
        )
        # The sun only lowers the demand.
        if joint is not None and joint[0] > 0 and joint[1] < 0:
            slope, sun, intercept = joint
            sun_m2 = round(-sun, 2)
    residuals = [
        y - (slope * x + intercept) + (sun_m2 or 0.0) * radiation_wh.get(day.day, 0.0)
        if radiation_wh is not None
        else y - (slope * x + intercept)
        for day, x, y in zip(usable, xs, ys, strict=True)
    ]
    mean_y = statistics.fmean(ys)
    total = sum((y - mean_y) ** 2 for y in ys)
    explained = 1 - sum(r * r for r in residuals) / total if total > 0 else None
    return HouseModel(
        loss_w_per_k=round(slope, 1),
        gains_w=round(-intercept / 24, 1),
        sun_m2=sun_m2,
        days=len(usable),
        r2=round(explained, 3) if explained is not None else None,
        spread_kwh=round(statistics.pstdev(residuals) / 1000, 2) if len(residuals) > 1 else None,
        learned=True,
    )


def fit_cop(days: Sequence[DayValues]) -> CopModel | None:
    """Arbeitszahl against the outside temperature from days with real heat and electricity of the same hours."""
    pairs = []
    for day in days:
        heat, electricity = day.cop_heat_kwh, day.cop_electricity_kwh
        if heat is None or electricity is None or electricity < 2 or day.outdoor_c is None or heat <= 0:
            continue
        ratio = heat / electricity
        if COP_LIMITS[0] <= ratio <= COP_LIMITS[1] + 1:
            pairs.append((day.outdoor_c, ratio))
    if len(pairs) < MIN_DAYS:
        return None
    line = _line([p[0] for p in pairs], [p[1] for p in pairs])
    if line is None:
        return None
    outdoor = [p[0] for p in pairs]
    return CopModel(
        a=round(line[1], 3),
        b=round(line[0], 4),
        days=len(pairs),
        low_c=round(min(outdoor), 1),
        high_c=round(max(outdoor), 1),
    )


def find_lag(heat_kwh: Mapping[datetime, float], difference_k: Mapping[datetime, float]) -> float | None:
    """The delay (h) between inside-outside difference and heat that explains the heat best.

    Compares sums over LAG_WINDOW_H hours; needs a week of hours with both values.
    """
    hours = sorted(set(heat_kwh) & set(difference_k))
    if len(hours) < 7 * 24:
        return None
    best: tuple[float, int] | None = None
    for lag in range(MAX_LAG_H + 1):
        xs, ys = [], []
        for index in range(0, len(hours) - LAG_WINDOW_H, LAG_WINDOW_H):
            window = hours[index : index + LAG_WINDOW_H]
            if window[-1] - window[0] != timedelta(hours=LAG_WINDOW_H - 1):
                continue
            earlier = [hour - timedelta(hours=lag) for hour in window]
            if not all(hour in difference_k for hour in earlier):
                continue
            xs.append(sum(difference_k[hour] for hour in earlier))
            ys.append(sum(heat_kwh[hour] for hour in window))
        if len(xs) < 20:
            continue
        r = _correlation(xs, ys)
        if r is not None and (best is None or r > best[0] + 0.005):
            best = (r, lag)
    return float(best[1]) if best is not None and best[0] > 0.3 else None


def demand(
    model: HouseModel,
    hours: Sequence[tuple[datetime, float | None, float | None]],
    inside_c: float,
    zone_shares: Mapping[str, float],
) -> list[DemandHour]:
    """Heat and electricity per hour from (start, outdoor, radiation); None outdoor gives no entry."""
    lag = timedelta(hours=model.lag_h or 0)
    outdoor = {start: value for start, value, _ in hours if value is not None}
    result = []
    for start, value, radiation in hours:
        if value is None:
            continue
        # The house answers the weather of a little while ago.
        earlier = outdoor.get(start - lag, value)
        heat = model.heat_w(inside_c, earlier, radiation)
        result.append(
            DemandHour(
                start=start,
                heat_w=round(heat),
                electricity_w=round(heat / model.cop.at(value)) if model.cop is not None else None,
                zones={zone: round(heat * share) for zone, share in zone_shares.items()},
            )
        )
    return result


def zone_shares(loads: Mapping[str, float], zones: Sequence[str]) -> dict[str, float]:
    """Each zone's share of the heat: by the heat loads of its rooms, else equal."""
    total = sum(loads.get(zone, 0.0) for zone in zones)
    if total > 0 and all(loads.get(zone, 0.0) > 0 for zone in zones):
        return {zone: loads[zone] / total for zone in zones}
    return {zone: 1 / len(zones) for zone in zones} if zones else {}


def _line(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float] | None:
    """Least squares y = slope * x + intercept."""
    if len(xs) < 2:
        return None
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    return slope, mean_y - slope * mean_x


def _plane(xs: Sequence[float], rs: Sequence[float], ys: Sequence[float]) -> tuple[float, float, float] | None:
    """Least squares y = a * x + b * r + c; None when x and r do not vary independently."""
    mean_x, mean_r, mean_y = statistics.fmean(xs), statistics.fmean(rs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    srr = sum((r - mean_r) ** 2 for r in rs)
    sxr = sum((x - mean_x) * (r - mean_r) for x, r in zip(xs, rs, strict=True))
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    sry = sum((r - mean_r) * (y - mean_y) for r, y in zip(rs, ys, strict=True))
    determinant = sxx * srr - sxr * sxr
    if determinant <= 1e-9 * max(sxx * srr, 1.0):
        return None
    a = (sxy * srr - sry * sxr) / determinant
    b = (sry * sxx - sxy * sxr) / determinant
    return a, b, mean_y - a * mean_x - b * mean_r


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None
