"""What the control learns of each room besides the valve (1.4.3, plan-1.4.3.md R2 and R3).

R2, the demand by the outside temperature: the valve opening that holds a room
at its setpoint, at -10 / -5 / 0 / 5 / 10 / 15 °C outside. The outside
temperature is the mean of the last 3 hours (the house is slow); between two
stages the demand is interpolated. Each stage starts from the history (21
days of calm hours: the room within 0.3 K of its setpoint for at least an
hour, the valve's mean in that hour, sorted by the outside temperature, newer
hours weighing more with a half-life of 14 days; at least 5 hours per stage).
An empty stage takes the line through the learned ones (two at least), else
today's median of the room model, else 30 %. In "Steuern", where the cascade
really sets the thermostat, the demand follows the room: every half hour the
room was off by at least 0.2 K the same way, 2 % more or less, shared by the
two neighbouring stages by their weight - not with the valve at its stop, no
heat, a window, a new setpoint, heating up or as a donor. Within 0 to 90 %.
After midnight each stage moves 20 % toward the median of its calm hours of
the last 7 days (with at least 5), so following and history stay together.

R3, the gain per room (% opening per K too cold): starts from the room model -
the opening that makes up 1 K in about 2 hours with the flow now, within 20 to
40 %/K (the valve gives little more heat in its middle) - or at 30 %/K. Once a
day after midnight, after a day of at least 12 hours in "Steuern": an
overshoot above +0.5 K or two swings make it 0.8 times ("zu nervös"); else a
stretch of 2 hours at least 0.3 K off that hardly fell makes it 1.2 times
("zu träge"); both or neither leave it. Within 10 to 60 %/K. The trend's share
is the gain times a third of an hour.

Pure data and functions; the room control feeds and keeps them.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from vaillant_eebus.hems.room_model import MAX_STEP, segments
from vaillant_eebus.rooms.quality import QualityDay

type Series = Sequence[tuple[datetime, float]]

STAGES = (-10.0, -5.0, 0.0, 5.0, 10.0, 15.0)
OUTDOOR_OVER = timedelta(hours=3)
HISTORY = timedelta(days=21)
RECENT = timedelta(days=7)
HALF_LIFE = timedelta(days=14)
CALM_K = 0.3
HOUR = timedelta(hours=1)
# A calm hour needs this many samples (every 5 minutes) with a valve value.
HOUR_SAMPLES = 10
STAGE_HOURS = 5
DEMAND_DEFAULT = 30.0
DEMAND_MAX = 90.0
TRACK_K = 0.2
TRACK_FOR = timedelta(minutes=30)
TRACK_STEP = 2.0
VALVE_STOP = 90.0
VALVE_CLOSED = 5.0
DAILY_SHARE = 0.2
# A change of a stage at least this large is named in the daily line.
CHANGE_SHOWN = 1.0

GAIN_DEFAULT = 30.0
GAIN_START = (20.0, 40.0)
GAIN_RANGE = (10.0, 60.0)
GAIN_DOWN = 0.8
GAIN_UP = 1.2
# The start: the opening that makes up 1 K in this time.
CATCH_UP_H = 2.0
CONTROL_FOR = timedelta(hours=12)
NERVOUS_OVERSHOOT_K = 0.5
NERVOUS_SWINGS = 2
SLUGGISH_MIN = 120.0
TREND_H = 1 / 3
TIMES = "\u00d7"
MINUS = "\u2212"


# --- R2: demand ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DemandStage:
    outdoor_c: float
    value: float
    # Calm hours of the history it was learned from (fewer than STAGE_HOURS: a start value).
    hours: int
    # Following the room changed it since.
    tracked: bool = False

    @property
    def learned(self) -> bool:
        return self.hours >= STAGE_HOURS or self.tracked


@dataclass(frozen=True, slots=True)
class DemandCurve:
    stages: tuple[DemandStage, ...]
    # Where the start values came from ("Verlauf", "Raummodell", "Startwert"), for the page.
    basis: str = "Startwert"

    def weights(self, outdoor_c: float) -> list[tuple[int, float]]:
        """The stages around this outside temperature and their weight (summing to 1)."""
        if outdoor_c <= STAGES[0]:
            return [(0, 1.0)]
        if outdoor_c >= STAGES[-1]:
            return [(len(STAGES) - 1, 1.0)]
        for index in range(len(STAGES) - 1):
            low, high = STAGES[index], STAGES[index + 1]
            if low <= outdoor_c <= high:
                upper = (outdoor_c - low) / (high - low)
                return [(index, 1.0 - upper), (index + 1, upper)]
        return [(0, 1.0)]

    def value(self, outdoor_c: float) -> float:
        return round(sum(self.stages[index].value * weight for index, weight in self.weights(outdoor_c)), 1)

    def follow(self, outdoor_c: float, sign: int) -> DemandCurve:
        """2 % more (sign +1) or less, shared by the neighbouring stages by their weight."""
        stages = list(self.stages)
        for index, weight in self.weights(outdoor_c):
            if weight <= 0:
                continue
            stage = stages[index]
            value = _bounded(stage.value + sign * TRACK_STEP * weight)
            stages[index] = replace(stage, value=round(value, 2), tracked=True)
        return replace(self, stages=tuple(stages))

    def toward(self, medians: dict[int, float], share: float = DAILY_SHARE) -> DemandCurve:
        """Each stage with a median of the recent history moves this share toward it."""
        stages = list(self.stages)
        for index, median in medians.items():
            stage = stages[index]
            stages[index] = replace(stage, value=round(_bounded(stage.value + share * (median - stage.value)), 2))
        return replace(self, stages=tuple(stages))

    def values(self) -> list[float]:
        return [stage.value for stage in self.stages]


@dataclass(frozen=True, slots=True)
class CalmHour:
    at: datetime
    valve: float
    outdoor_c: float


def calm_hours(temperature: Series, setpoint: Series, valve: Series, outdoor: Series) -> list[CalmHour]:
    """Whole hours in which the room stayed within 0.3 K of its setpoint, with the valve and outside means."""
    times = [moment for moment, _value in temperature]
    temps = [value for _moment, value in temperature]
    targets = list(map(_lookup(setpoint), times))
    openings = list(map(_lookup(valve), times))
    outside = list(map(_lookup(outdoor), times))

    def near(index: int) -> bool:
        target = targets[index]
        return target is not None and abs(temps[index] - target) < CALM_K

    hours = []
    for run in segments(times, near):
        start = 0
        while start < len(run):
            first = times[run[start]]
            chunk = [index for index in run[start:] if times[index] - first < HOUR]
            if times[chunk[-1]] - first < HOUR - timedelta(minutes=10):
                break
            valves = [value for index in chunk if (value := openings[index]) is not None]
            outs = [value for index in chunk if (value := outside[index]) is not None]
            if len(valves) >= HOUR_SAMPLES and outs:
                hours.append(CalmHour(times[chunk[-1]], statistics.fmean(valves), statistics.fmean(outs)))
            start += len(chunk)
    return hours


def stage_of(outdoor_c: float) -> int:
    """The nearest stage."""
    return min(range(len(STAGES)), key=lambda index: abs(STAGES[index] - outdoor_c))


def demand_from_history(hours: Sequence[CalmHour], now: datetime, fallback: float | None) -> DemandCurve:
    """The start values of the stages (R2)."""
    by_stage: dict[int, list[CalmHour]] = {}
    for hour in hours:
        by_stage.setdefault(stage_of(hour.outdoor_c), []).append(hour)
    learned: dict[int, float] = {}
    for index, items in by_stage.items():
        if len(items) < STAGE_HOURS:
            continue
        weights = [0.5 ** (max(0.0, (now - item.at).total_seconds()) / HALF_LIFE.total_seconds()) for item in items]
        learned[index] = sum(weight * item.valve for weight, item in zip(weights, items, strict=True)) / sum(weights)
    line = _line([(STAGES[index], value) for index, value in learned.items()]) if len(learned) >= 2 else None
    if learned:
        basis = "Verlauf"
    elif fallback is not None:
        basis = "Raummodell"
    else:
        basis = "Startwert"
    stages = []
    for index, outdoor in enumerate(STAGES):
        count = len(by_stage.get(index, []))
        if index in learned:
            value = learned[index]
        elif line is not None:
            value = line[0] * outdoor + line[1]
        elif fallback is not None:
            value = fallback
        elif learned:
            value = statistics.fmean(learned.values())
        else:
            value = DEMAND_DEFAULT
        stages.append(DemandStage(outdoor, round(_bounded(value), 1), count))
    return DemandCurve(tuple(stages), basis)


def recent_medians(hours: Sequence[CalmHour], now: datetime) -> dict[int, float]:
    """The median valve of each stage's calm hours of the last 7 days (with at least 5)."""
    by_stage: dict[int, list[float]] = {}
    for hour in hours:
        if now - hour.at <= RECENT:
            by_stage.setdefault(stage_of(hour.outdoor_c), []).append(hour.valve)
    return {index: statistics.median(values) for index, values in by_stage.items() if len(values) >= STAGE_HOURS}


def demand_changes(before: Sequence[float], curve: DemandCurve) -> list[str]:
    """The stages that changed noticeably since before, German ("bei 0 °C 34 → 38 %")."""
    texts = []
    for stage, old in zip(curve.stages, before, strict=False):
        if abs(stage.value - old) >= CHANGE_SHOWN:
            texts.append(f"bei {_degrees(stage.outdoor_c)} {old:.0f} → {stage.value:.0f} %")
    return texts


@dataclass
class DemandFollow:
    """Whether the room has been off the same way for half an hour (R2, following)."""

    since: datetime | None = None
    sign: int = 0

    def update(self, now: datetime, deviation: float | None, *, blocked: bool) -> int:
        """deviation = setpoint - room. Returns +1 / -1 when the demand is to rise / fall now, else 0."""
        if blocked or deviation is None or abs(deviation) < TRACK_K:
            self.since, self.sign = None, 0
            return 0
        sign = 1 if deviation > 0 else -1
        if sign != self.sign or self.since is None:
            self.since, self.sign = now, sign
            return 0
        if now - self.since < TRACK_FOR:
            return 0
        self.since = now
        return sign


def valve_at_stop(valve: float | None, sign: int) -> bool:
    """More (sign +1) or less opening does not help: the valve is at that stop already."""
    if valve is None:
        return False
    return (sign > 0 and valve >= VALVE_STOP) or (sign < 0 and valve <= VALVE_CLOSED)


def outdoor_mean(outdoor: Series, now: datetime) -> float | None:
    values = [value for moment, value in outdoor if now - moment <= OUTDOOR_OVER]
    return round(statistics.fmean(values), 1) if values else None


# --- R3: gain -----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gain:
    value: float
    start: float
    # Where the start came from, and the last change with its reason (German).
    start_reason: str
    changed_at: datetime | None = None
    reason: str = ""

    @property
    def trend(self) -> float:
        """% opening per K/h falling."""
        return round(self.value * TREND_H, 1)


def start_gain(heating_rate: float | None, flow_c: float | None, room_c: float | None) -> Gain:
    """The opening that makes up 1 K in about 2 hours, from the room model; 30 %/K without one."""
    if heating_rate is None or heating_rate <= 0 or flow_c is None or room_c is None:
        return Gain(GAIN_DEFAULT, GAIN_DEFAULT, "Startwert (ohne gelernte Aufheizrate)")
    # Fully open the room heats this fast now (K/h); 1 K in 2 h needs this share of it.
    full = heating_rate * max(1.0, flow_c - room_c)
    needed = 100.0 * (1.0 / CATCH_UP_H) / full
    value = round(min(GAIN_START[1], max(GAIN_START[0], needed)), 1)
    return Gain(value, value, "aus dem Raummodell (Aufheizrate)")


def daily_gain(gain: Gain, day: QualityDay, now: datetime) -> tuple[Gain, str | None]:
    """The gain after a day in "Steuern" (R3); with the protocol's reason when it changed."""
    if day.control_minutes < CONTROL_FOR.total_seconds() / 60:
        return gain, None
    nervous = []
    if day.overshoots and (day.overshoot_max_k or 0.0) > NERVOUS_OVERSHOOT_K:
        excess = f"{day.overshoot_max_k:.1f}".replace(".", ",")
        nervous.append(f"{day.overshoots} Überschwinger bis {excess} K")
    if day.swings >= NERVOUS_SWINGS:
        nervous.append(f"{day.swings}{TIMES} hin und her")
    sluggish = day.sluggish_min >= SLUGGISH_MIN
    if bool(nervous) == sluggish:
        return gain, None
    if nervous:
        factor, why = GAIN_DOWN, "zu nervös: " + ", ".join(nervous)
    else:
        hours = f"{day.sluggish_min / 60:.1f}".replace(".", ",")
        factor, why = GAIN_UP, f"zu träge: {hours} h lang mindestens 0,3 K daneben"
    value = round(min(GAIN_RANGE[1], max(GAIN_RANGE[0], gain.value * factor)), 1)
    if abs(value - gain.value) < 0.05:
        return gain, None
    return replace(gain, value=value, changed_at=now, reason=why), why


# --- persistence -----------------------------------------------------------------------------------------


def curve_to_json(curve: DemandCurve) -> dict[str, Any]:
    return {
        "basis": curve.basis,
        "stages": [[stage.outdoor_c, stage.value, stage.hours, stage.tracked] for stage in curve.stages],
    }


def curve_from_json(raw: Any) -> DemandCurve | None:
    if not isinstance(raw, dict):
        return None
    stages = []
    try:
        for (outdoor, value, hours, tracked), expected in zip(raw.get("stages") or [], STAGES, strict=True):
            if abs(float(outdoor) - expected) > 1e-6:
                return None
            stages.append(DemandStage(expected, _bounded(float(value)), int(hours), bool(tracked)))
    except (TypeError, ValueError):
        return None
    return DemandCurve(tuple(stages), str(raw.get("basis") or "Startwert"))


def gain_to_json(gain: Gain) -> dict[str, Any]:
    return {
        "value": gain.value,
        "start": gain.start,
        "start_reason": gain.start_reason,
        "changed_at": gain.changed_at.isoformat() if gain.changed_at else None,
        "reason": gain.reason,
    }


def gain_from_json(raw: Any) -> Gain | None:
    if not isinstance(raw, dict):
        return None
    try:
        value = float(raw["value"])
        start = float(raw["start"])
        changed = raw.get("changed_at")
        changed_at = datetime.fromisoformat(changed) if isinstance(changed, str) else None
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(value) and math.isfinite(start)):
        return None
    value = min(GAIN_RANGE[1], max(GAIN_RANGE[0], value))
    return Gain(value, start, str(raw.get("start_reason") or ""), changed_at, str(raw.get("reason") or ""))


# --- helpers ---------------------------------------------------------------------------------------------


def _bounded(value: float) -> float:
    return min(DEMAND_MAX, max(0.0, value))


def _line(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    return slope, mean_y - slope * mean_x


def _lookup(series: Series) -> Callable[[datetime], float | None]:
    """The sample at a moment or the last one before it within 12 minutes (as the room model)."""
    times = [moment for moment, _value in series]

    def value(moment: datetime) -> float | None:
        index = bisect.bisect_right(times, moment) - 1
        if index < 0 or moment - times[index] > MAX_STEP:
            return None
        return series[index][1]

    return value


def _degrees(value: float) -> str:
    return f"{value:g} °C".replace("-", MINUS)
