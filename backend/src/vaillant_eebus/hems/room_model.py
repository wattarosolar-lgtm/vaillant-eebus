"""The room model: how each room cools, heats and is supplied (punkt1-trv-regelung.md 2, 4, 9, 9a).

Without heat a room's temperature changes by -(room - outside) / tau + gains
(K/h). From the five-minute samples of the last 21 days the app learns tau
(time constant), the gains, how fast the room heats per K of flow above the
room, how long after a start of the heat pump the room stops falling, the
valve opening that holds the setpoint, the thermostat's offset to the room
sensor and the overshoot. Until enough phases are seen it shows "lernt noch"
and uses start values. Supply per room and the hint for the flow temperature
are shown as "würde" in 1.3.0; nothing is sent.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

type Series = Sequence[tuple[datetime, float]]

# Phases needed before a value counts as learned.
MIN_PHASES = 5
# Start values until learned (assumptions, shown as such). The time constant: rooms of this house cool by
# about 0.08-0.15 K/h (acceptance 1.3.0), 30 h made it 0.9 K/h (K5, 1.5.0).
START_TAU_H = 150.0
START_STARTUP_MIN = {"floor": 120.0, "radiator": 30.0}
TAU_RANGE_H = (5.0, 500.0)
# Sample spacing is 5 min; a step longer than this is a gap.
MAX_STEP = timedelta(minutes=12)
NO_HEAT_VALVE = 5.0
OPEN_VALVE = 80.0
MIN_COOLING = timedelta(hours=1)
MIN_HEATING = timedelta(minutes=30)
CALM_K = 0.3
CALM_FOR = timedelta(hours=1)
PAUSE_BEFORE_START = timedelta(minutes=20)
STARTUP_MAX = timedelta(hours=3)
OVERSHOOT_WITHIN = timedelta(minutes=90)
OUTDOOR_TABLE = (-10.0, -5.0, 0.0, 5.0, 10.0, 15.0)
SIMULATE_FOR = timedelta(hours=24)
# Supply rules (punkt1-trv-regelung.md 4 and 9).
UNDER_VALVE = 90.0
UNDER_K = 0.3
UNDER_FOR = timedelta(minutes=30)
OVER_VALVE = 30.0
LOWER_FLOW_VALVE = 60.0
LOWER_FLOW_FOR = timedelta(hours=1)
ON_W = 100.0


class Supply(StrEnum):
    UNKNOWN = "unknown"
    SUPPLIED = "supplied"
    UNDER = "under"
    OVER = "over"


class FlowHint(StrEnum):
    UNKNOWN = "unknown"
    HOLD = "hold"
    HIGHER = "higher"
    LOWER = "lower"


@dataclass(frozen=True, slots=True)
class Learned:
    value: float | None
    # Phases the value was learned from; below MIN_PHASES it is a start value (or unknown).
    phases: int

    @property
    def learned(self) -> bool:
        return self.value is not None and self.phases >= MIN_PHASES


@dataclass(frozen=True, slots=True)
class RoomModel:
    tau_h: Learned
    gains_k_per_h: Learned
    # K/h per K of flow above the room, with the valve open.
    heating_rate: Learned
    startup_min: Learned
    demand_valve: Learned
    # Thermostat setpoint minus room temperature in calm times.
    correction_k: Learned
    overshoot_k: Learned

    @property
    def learning(self) -> bool:
        return not (self.tau_h.learned and self.startup_min.learned)

    def cooling_k_per_h(self, room_c: float, outdoor_c: float) -> float:
        tau = self.tau_h.value if self.tau_h.learned and self.tau_h.value else START_TAU_H
        gains = self.gains_k_per_h.value if self.gains_k_per_h.learned and self.gains_k_per_h.value else 0.0
        return -(room_c - outdoor_c) / tau + gains


@dataclass(frozen=True, slots=True)
class SupplyState:
    supply: Supply
    # Mean valve opening over the last 30 min, %.
    valve: float | None


def segments(times: Sequence[datetime], keep: Callable[[int], bool]) -> list[list[int]]:
    """Runs of consecutive sample indices where keep(index) holds, split at gaps."""
    runs: list[list[int]] = []
    current: list[int] = []
    for index in range(len(times)):
        if keep(index) and (not current or times[index] - times[current[-1]] <= MAX_STEP):
            current.append(index)
            continue
        if len(current) > 1:
            runs.append(current)
        current = [index] if keep(index) else []
    if len(current) > 1:
        runs.append(current)
    return runs


def at(series: Series, moment: datetime) -> float | None:
    """The sample of series at moment or the last one before (within MAX_STEP)."""
    times = [point[0] for point in series]
    index = bisect.bisect_right(times, moment) - 1
    if index < 0 or moment - series[index][0] > MAX_STEP:
        return None
    return series[index][1]


def learn(
    *,
    temperature: Series,
    outdoor: Series,
    power: Series,
    valve: Series = (),
    flow: Series = (),
    trv_setpoint: Series = (),
    setpoint: Series = (),
    zone_off: Series = (),
    kind: str = "radiator",
) -> RoomModel:
    """zone_off: 1 while the room's zone is switched off (no heat at all, whatever the valve; K5)."""
    times = [point[0] for point in temperature]
    temps = [point[1] for point in temperature]
    out = [at(outdoor, moment) for moment in times]
    pw = [at(power, moment) for moment in times]
    vv = [at(valve, moment) for moment in times] if valve else [None] * len(times)
    fl = [at(flow, moment) for moment in times] if flow else [None] * len(times)
    sp = [at(setpoint, moment) for moment in times] if setpoint else [None] * len(times)
    trv = [at(trv_setpoint, moment) for moment in times] if trv_setpoint else [None] * len(times)
    off = [at(zone_off, moment) for moment in times] if zone_off else [None] * len(times)

    # Cooling: the zone switched off, the valve (nearly) closed, or without valve data the heat pump standing still.
    def no_heat(index: int) -> bool:
        if out[index] is None:
            return False
        if off[index] == 1.0:
            return True
        opening = vv[index]
        if opening is not None:
            return opening <= NO_HEAT_VALVE
        watts = pw[index]
        return watts is not None and watts < ON_W

    points = []
    for run in segments(times, no_heat):
        if times[run[-1]] - times[run[0]] < MIN_COOLING:
            continue
        hours = (times[run[-1]] - times[run[0]]).total_seconds() / 3600
        rate = (temps[run[-1]] - temps[run[0]]) / hours
        spread = statistics.fmean(temps[i] - (out[i] or 0.0) for i in run)
        points.append((spread, rate))
    tau = gains = None
    if len(points) >= 2:
        line = _line([p[0] for p in points], [p[1] for p in points])
        if line is not None and line[0] < 0:
            candidate = -1 / line[0]
            if TAU_RANGE_H[0] <= candidate <= TAU_RANGE_H[1]:
                tau, gains = round(candidate, 1), round(line[1], 3)

    # Heating: valve open with a known flow temperature.
    def heating(index: int) -> bool:
        opening = vv[index]
        return opening is not None and opening >= OPEN_VALVE and fl[index] is not None

    rates = []
    for run in segments(times, heating):
        if times[run[-1]] - times[run[0]] < MIN_HEATING:
            continue
        hours = (times[run[-1]] - times[run[0]]).total_seconds() / 3600
        excess = statistics.fmean((fl[i] or 0.0) - temps[i] for i in run)
        if excess > 2:
            rates.append((temps[run[-1]] - temps[run[0]]) / hours / excess)

    startups = _startups(times, temps, power)
    calm = _calm(times, temps, sp)
    demand = [value for i in calm if (value := vv[i]) is not None]
    corrections = [value - temps[i] for i in calm if (value := trv[i]) is not None]
    overshoots = _overshoots(times, temps, vv, sp)
    start_startup = START_STARTUP_MIN.get(kind, START_STARTUP_MIN["radiator"])
    return RoomModel(
        tau_h=Learned(tau if tau is not None else START_TAU_H, len(points) if tau is not None else 0),
        gains_k_per_h=Learned(gains, len(points) if gains is not None else 0),
        heating_rate=Learned(round(statistics.median(rates), 4) if rates else None, len(rates)),
        startup_min=Learned(round(statistics.median(startups)) if startups else start_startup, len(startups)),
        demand_valve=Learned(round(statistics.median(demand), 1) if demand else None, len(calm) // 12),
        correction_k=Learned(round(statistics.median(corrections), 2) if corrections else None, len(calm) // 12),
        overshoot_k=Learned(round(statistics.median(overshoots), 2) if overshoots else None, len(overshoots)),
    )


def minutes_until(
    model: RoomModel, room_c: float, limit_c: float, outdoor: Callable[[datetime], float | None], now: datetime
) -> float | None:
    """Minutes until the room falls to limit_c without heat, with the forecast outside; None: not within 24 h."""
    if room_c <= limit_c:
        return 0.0
    step = timedelta(minutes=5)
    temperature = room_c
    moment = now
    while moment - now < SIMULATE_FOR:
        outside = outdoor(moment)
        if outside is None:
            return None
        temperature += model.cooling_k_per_h(temperature, outside) * step.total_seconds() / 3600
        moment += step
        if temperature <= limit_c:
            return (moment - now).total_seconds() / 60
    return None


def cooling_table(model: RoomModel, setpoint_c: float) -> dict[float, float]:
    """K/h at the setpoint for outside temperatures from -10 to +15 °C."""
    return {outside: round(model.cooling_k_per_h(setpoint_c, outside), 3) for outside in OUTDOOR_TABLE}


def supply(temperature: Series, valve: Series, setpoint_c: float | None, now: datetime) -> SupplyState:
    """versorgt / unterversorgt / überversorgt from the last hour (rules of punkt1 section 4)."""
    recent_valve = [value for moment, value in valve if now - moment <= UNDER_FOR]
    if not recent_valve or not temperature or setpoint_c is None or now - temperature[-1][0] > MAX_STEP:
        return SupplyState(Supply.UNKNOWN, round(statistics.fmean(recent_valve), 1) if recent_valve else None)
    room = temperature[-1][1]
    mean_valve = round(statistics.fmean(recent_valve), 1)
    if min(recent_valve) >= UNDER_VALVE and room <= setpoint_c - UNDER_K:
        return SupplyState(Supply.UNDER, mean_valve)
    hour_valve = [value for moment, value in valve if now - moment <= timedelta(hours=1)]
    if room >= setpoint_c and statistics.fmean(hour_valve) <= OVER_VALVE:
        return SupplyState(Supply.OVER, mean_valve)
    return SupplyState(Supply.SUPPLIED, mean_valve)


def flow_hint(states: Sequence[SupplyState], valves_last_hour: Sequence[Series], now: datetime) -> FlowHint:
    """Zone 2: higher when a room lacks heat, lower when all are supplied and no valve is wide open."""
    known = [state for state in states if state.supply is not Supply.UNKNOWN]
    if not known:
        return FlowHint.UNKNOWN
    if any(state.supply is Supply.UNDER for state in known):
        return FlowHint.HIGHER
    widest = [
        max(values)
        for series in valves_last_hour
        if (values := [value for moment, value in series if now - moment <= LOWER_FLOW_FOR])
    ]
    if widest and max(widest) < LOWER_FLOW_VALVE:
        return FlowHint.LOWER
    return FlowHint.HOLD


def _startups(times: Sequence[datetime], temps: Sequence[float], power: Series) -> list[float]:
    """Minutes from a start of the heat pump after a pause until the room stops falling."""
    starts = []
    for index in range(1, len(power)):
        moment, watts = power[index]
        before_time, before = power[index - 1]
        if watts >= ON_W and before < ON_W and moment - before_time >= PAUSE_BEFORE_START:
            starts.append(moment)
    result = []
    for start in starts:
        first = bisect.bisect_left(times, start)
        window = [i for i in range(first, len(times)) if times[i] - start <= STARTUP_MAX]
        if len(window) < 4:
            continue
        lowest = min(window, key=lambda i: temps[i])
        # The room must have been falling, and must rise afterwards: else nothing to measure.
        if lowest == window[-1] or temps[window[-1]] - temps[lowest] < 0.1:
            continue
        result.append((times[lowest] - start).total_seconds() / 60)
    return result


def _calm(times: Sequence[datetime], temps: Sequence[float], setpoints: Sequence[float | None]) -> list[int]:
    """Samples in calm hours: within CALM_K of the setpoint for at least CALM_FOR."""

    def near(index: int) -> bool:
        target = setpoints[index]
        return target is not None and abs(temps[index] - target) < CALM_K

    result = []
    for run in segments(times, near):
        if times[run[-1]] - times[run[0]] >= CALM_FOR:
            result += run
    return result


def _overshoots(
    times: Sequence[datetime], temps: Sequence[float], valves: Sequence[float | None], setpoints: Sequence[float | None]
) -> list[float]:
    """Highest temperature above the setpoint after the valve closed following a heating phase."""
    result = []
    for index in range(1, len(times)):
        before, now = valves[index - 1], valves[index]
        if before is None or now is None or not (before >= 50 and now <= NO_HEAT_VALVE):
            continue
        target = setpoints[index]
        if target is None:
            continue
        window = [temps[i] for i in range(index, len(times)) if times[i] - times[index] <= OVERSHOOT_WITHIN]
        excess = max(window) - target
        if excess > 0:
            result.append(excess)
    return result


def _line(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float] | None:
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0 or not math.isfinite(sxx):
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    return slope, mean_y - slope * mean_x
