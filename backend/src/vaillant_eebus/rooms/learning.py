"""What the app learns about each thermostat's valve (1.4.2, plan-1.4.2-testlauf-lernt.md A and B).

The map ("Kennfeld"): the valve position after it settled, by the offset of
the setpoint to the temperature at the thermostat (0.5 K grid), kept apart for
a rising and a falling offset (hysteresis) and by how warm the water at the
radiator is (flow below 30, 30 to 35, above 35 °C). Every point was taken with
warm water arriving. Newer points weigh more (half-life 14 days).

Besides the map: each change of the setpoint gives a step response (after how
many minutes the valve moved, how fast, when it settled); calm hours show
whether the valve keeps wandering at an unchanged offset (a PI controller),
follows the offset (P) or knows only open and shut; and the smallest change of
the setpoint that moved the valve at all.

Points come from test runs (with the probe pair) and from everyday operation
(CalmWatch: setpoint and offset calm for 30 minutes with warm water). The
control may go to "Steuern" once the map knows the valve closed and fully open.

Pure data and step functions; the room control feeds them and keeps them.
"""

from __future__ import annotations

import statistics
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

# A valve at most this open is closed ("zu"), at least FULL_OPEN fully open ("ganz offen").
CLOSED = 5.0
FULL_OPEN = 85.0
# Fully open also when the valve is at least PLATEAU_FROM open and 1 K more brings less than PLATEAU_GAIN.
PLATEAU_FROM = 50.0
PLATEAU_GAIN = 5.0
PLATEAU_K = 1.0
GRID_K = 0.5
HALF_LIFE = timedelta(days=14)
# Kept: the newest points per cell, and in all; step responses and drifts.
CELL_KEEP = 10
POINTS_KEEP = 600
RESPONSES_KEEP = 30
DRIFTS_KEEP = 30
# Everyday points: setpoint, offset and warm water calm this long; the valve as the median of the last minutes.
CALM_FOR = timedelta(minutes=30)
MEDIAN_OVER = timedelta(minutes=10)
# A step response: the valve moved this much; it settled when it stayed within this for STEADY_FOR; followed
# for RESPONSE_MAX at most.
MOVE_POINTS = 5.0
STEADY_FOR = timedelta(minutes=10)
RESPONSE_MAX = timedelta(minutes=60)
# A step of the setpoint smaller than this is no step (the thermostats' resolution is 0.5 K).
STEP_FROM_K = 0.49
# Drift: the valve's change over an hour at a calm setpoint and offset; at least PI_DRIFT %/h toward the
# offset's sign is a PI controller.
DRIFT_OVER = timedelta(minutes=60)
PI_DRIFT = 10.0
KIND_MIN = 3
ON_OFF_SHARE = 0.9
ON_OFF_MIN = 20


class Supply(StrEnum):
    """How warm the water at the radiator is (warm water arrives with every point)."""

    COOL = "cool"
    MILD = "mild"
    WARM = "warm"
    UNKNOWN = "unknown"


SUPPLY_NAMES = {
    Supply.COOL: "Vorlauf unter 30 °C",
    Supply.MILD: "Vorlauf 30 bis 35 °C",
    Supply.WARM: "Vorlauf über 35 °C",
    Supply.UNKNOWN: "Vorlauf unbekannt",
}
# Neighbours first when the current class has no points.
_NEAREST = {
    Supply.COOL: (Supply.COOL, Supply.MILD, Supply.WARM, Supply.UNKNOWN),
    Supply.MILD: (Supply.MILD, Supply.COOL, Supply.WARM, Supply.UNKNOWN),
    Supply.WARM: (Supply.WARM, Supply.MILD, Supply.COOL, Supply.UNKNOWN),
    Supply.UNKNOWN: (Supply.UNKNOWN, Supply.MILD, Supply.WARM, Supply.COOL),
}


def supply_class(flow_c: float | None) -> Supply:
    if flow_c is None:
        return Supply.UNKNOWN
    if flow_c < 30.0:
        return Supply.COOL
    if flow_c <= 35.0:
        return Supply.MILD
    return Supply.WARM


class Source(StrEnum):
    TEST = "test"
    DAILY = "daily"


class Kind(StrEnum):
    UNKNOWN = "unknown"
    ON_OFF = "on_off"
    P = "p"
    PI = "pi"


KIND_NAMES = {
    Kind.UNKNOWN: "noch unbekannt",
    Kind.ON_OFF: "auf/zu",
    Kind.P: "proportional (P)",
    Kind.PI: "PI (läuft nach)",
}


def grid(offset_k: float) -> float:
    """The offset on the map's 0.5 K grid."""
    return round(round(offset_k / GRID_K) * GRID_K, 2)


@dataclass(frozen=True, slots=True)
class MapPoint:
    at: datetime
    # Setpoint minus temperature at the thermostat, on the grid.
    offset_k: float
    rising: bool
    supply: Supply
    valve: float
    source: Source


@dataclass(frozen=True, slots=True)
class Response:
    """How the valve answered a change of the setpoint."""

    at: datetime
    delta_k: float
    valve_before: float
    valve_after: float
    # Minutes until the valve moved (None: it did not), until it settled, and its mean speed until then.
    reaction_min: float | None
    settle_min: float | None
    speed: float | None
    # The app sent the change (else by hand or the thermostat's own schedule).
    own: bool


@dataclass(frozen=True, slots=True)
class Drift:
    """The valve's change per hour at a calm setpoint and offset."""

    at: datetime
    offset_k: float
    per_h: float


@dataclass(frozen=True, slots=True)
class Cell:
    offset_k: float
    rising: bool
    supply: Supply
    valve: float
    count: int


@dataclass(frozen=True, slots=True)
class ValveMap:
    points: tuple[MapPoint, ...] = ()
    responses: tuple[Response, ...] = ()
    drifts: tuple[Drift, ...] = ()

    # --- learning -------------------------------------------------------------------------------

    def add_points(self, points: Iterable[MapPoint]) -> ValveMap:
        merged = [*self.points, *points]
        kept: list[MapPoint] = []
        per_cell: dict[tuple[float, bool, Supply], int] = {}
        # Newest first: each cell keeps its CELL_KEEP newest points.
        for point in sorted(merged, key=lambda item: item.at, reverse=True):
            key = (point.offset_k, point.rising, point.supply)
            if per_cell.get(key, 0) >= CELL_KEEP:
                continue
            per_cell[key] = per_cell.get(key, 0) + 1
            kept.append(point)
        return replace(self, points=tuple(sorted(kept[:POINTS_KEEP], key=lambda item: item.at)))

    def add_response(self, response: Response) -> ValveMap:
        return replace(self, responses=(*self.responses, response)[-RESPONSES_KEEP:])

    def add_drift(self, drift: Drift) -> ValveMap:
        return replace(self, drifts=(*self.drifts, drift)[-DRIFTS_KEEP:])

    # --- what it knows ------------------------------------------------------------------------------

    def cells(self, now: datetime) -> list[Cell]:
        groups: dict[tuple[float, bool, Supply], list[MapPoint]] = {}
        for point in self.points:
            groups.setdefault((point.offset_k, point.rising, point.supply), []).append(point)
        return [
            Cell(offset, rising, supply, _weighted(points, now), len(points))
            for (offset, rising, supply), points in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1]))
        ]

    def merged(self, now: datetime) -> list[Cell]:
        """The cells of all supply classes together, by offset and direction (for the page)."""
        groups: dict[tuple[float, bool], list[MapPoint]] = {}
        for point in self.points:
            groups.setdefault((point.offset_k, point.rising), []).append(point)
        return [
            Cell(offset, rising, Supply.UNKNOWN, _weighted(points, now), len(points))
            for (offset, rising), points in sorted(groups.items())
        ]

    def closed_k(self, now: datetime) -> float | None:
        """The highest offset at which the valve was closed; None while unknown."""
        closed = [cell.offset_k for cell in self.cells(now) if cell.valve <= CLOSED]
        return max(closed) if closed else None

    def full(self, now: datetime, *, rising: bool | None = None) -> tuple[float, float] | None:
        """The smallest offset at which the valve was fully open, with its opening; None while unknown.

        Fully open is at least 85 %, or a rising plateau: at least 50 % open and 1 K more brought less
        than 5 points (the Comet stays below 90 %). rising limits it to one direction.
        """
        cells = [cell for cell in self.cells(now) if rising is None or cell.rising is rising]
        found = [(cell.offset_k, cell.valve) for cell in cells if cell.valve >= FULL_OPEN]
        up = sorted((cell for cell in cells if cell.rising), key=lambda cell: cell.offset_k)
        for supply in {cell.supply for cell in up}:
            line = _line([cell for cell in up if cell.supply is supply])
            for offset, valve in line:
                if valve < PLATEAU_FROM:
                    continue
                higher = [other for other_offset, other in line if other_offset >= offset + PLATEAU_K - 1e-6]
                if higher and max(higher) - valve < PLATEAU_GAIN:
                    found.append((offset, max(valve, *higher)))
                    break
        if not found:
            return None
        offset = min(item[0] for item in found)
        return offset, max(valve for item_offset, valve in found if item_offset == offset)

    def full_k(self, now: datetime) -> float | None:
        full = self.full(now)
        return full[0] if full is not None else None

    def missing(self, now: datetime) -> list[str]:
        """What the map still lacks before the room may go to "Steuern" (German)."""
        missing = []
        if self.closed_k(now) is None:
            missing.append("„zu“ bei warmem Wasser")
        if self.full(now) is None:
            missing.append("„ganz offen“ bei warmem Wasser")
        return missing

    def ready(self, now: datetime) -> bool:
        return not self.missing(now)

    def opening_k(self, now: datetime) -> float | None:
        """The smallest rising offset at which the valve was open."""
        opened = [cell.offset_k for cell in self.cells(now) if cell.rising and cell.valve > CLOSED]
        return min(opened) if opened else None

    def levels(self, now: datetime) -> list[float]:
        """Openings the thermostat really reached, rounded to 10 %."""
        return sorted({round(cell.valve / 10) * 10.0 for cell in self.cells(now)})

    def nearest_level(self, opening: float, now: datetime) -> float | None:
        levels = self.levels(now)
        return min(levels, key=lambda level: abs(level - opening)) if levels else None

    def offset_for(self, opening: float, *, rising: bool, supply: Supply, now: datetime) -> float | None:
        """The smallest offset that reached this opening in this state (direction, warm water); the nearest
        state that knows it when this one does not. For a closed valve the highest offset still closed."""
        cells = self.cells(now)
        for wanted_rising in (rising, not rising):
            for wanted_supply in _NEAREST[supply]:
                candidates = [cell for cell in cells if cell.rising is wanted_rising and cell.supply is wanted_supply]
                if opening <= CLOSED:
                    closed = [cell.offset_k for cell in candidates if cell.valve <= CLOSED]
                    if closed:
                        return max(closed)
                    continue
                reached = [cell.offset_k for cell in candidates if cell.valve >= opening - 1e-6]
                if reached:
                    return min(reached)
        return None

    def kind(self) -> Kind:
        """auf/zu, P or PI from what the valve did; unknown until there is enough."""
        valves = [point.valve for point in self.points]
        if len(valves) >= ON_OFF_MIN:
            extreme = sum(1 for valve in valves if valve <= CLOSED or valve >= 95.0)
            if extreme / len(valves) >= ON_OFF_SHARE and any(valve >= 95.0 for valve in valves):
                return Kind.ON_OFF
        # Drifts toward the offset's sign (an integral winds up) speak for PI.
        toward = [_toward(drift) for drift in self.drifts if abs(drift.offset_k) >= GRID_K - 1e-6]
        if len(toward) >= KIND_MIN:
            return Kind.PI if statistics.median(toward) >= PI_DRIFT else Kind.P
        return Kind.UNKNOWN

    def pi_per_h_per_k(self) -> float | None:
        """How fast a PI thermostat's valve wanders per K of offset (%/h per K)."""
        rates = [_toward(drift) / abs(drift.offset_k) for drift in self.drifts if abs(drift.offset_k) >= GRID_K]
        return round(statistics.median(rates), 1) if len(rates) >= KIND_MIN else None

    def reaction_min(self) -> float | None:
        return _median(response.reaction_min for response in self.responses)

    def settle_min(self) -> float | None:
        return _median(response.settle_min for response in self.responses)

    def speed(self) -> float | None:
        return _median(response.speed for response in self.responses)

    def smallest_step_k(self) -> float | None:
        """The smallest change of the setpoint that moved the valve (not at its stop already)."""
        moved = [
            abs(response.delta_k)
            for response in self.responses
            if abs(response.valve_after - response.valve_before) >= MOVE_POINTS
            and not (response.delta_k > 0 and response.valve_before >= 95.0)
            and not (response.delta_k < 0 and response.valve_before <= CLOSED)
        ]
        return min(moved) if moved else None

    def counts(self) -> dict[Source, int]:
        return {source: sum(1 for point in self.points if point.source is source) for source in Source}


def points_from_steps(steps: Iterable[Any], at: datetime) -> list[MapPoint]:
    """Map points from the settled steps of a test run (curve.Step): offset, valve, flow at the radiator."""
    points = []
    for step in steps:
        if step.offset_k is None or step.valve is None:
            continue
        points.append(
            MapPoint(
                at=at,
                offset_k=grid(step.offset_k),
                rising=step.rising,
                supply=supply_class(step.flow_c),
                valve=float(step.valve),
                source=Source.TEST,
            )
        )
    return points


# --- learning in everyday operation --------------------------------------------------------------------


@dataclass
class CalmWatch:
    """Everyday points of one thermostat: fed every minute, gives a point when setpoint, offset and warm
    water stood still for half an hour, and a drift for each calm hour."""

    setpoint: float | None = None
    setpoint_since: datetime | None = None
    cell: float | None = None
    cell_since: datetime | None = None
    rising: bool = True
    warm_since: datetime | None = None
    valves: deque[tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=120))
    last_point: datetime | None = None
    last_drift: datetime | None = None

    def update(
        self,
        now: datetime,
        *,
        setpoint: float | None,
        trv_c: float | None,
        valve: float | None,
        fresh: bool,
        warm: bool,
        supply: Supply,
        quiet: bool = True,
    ) -> tuple[MapPoint | None, Drift | None]:
        """quiet is False while something else disturbs the valve (window, test run, unavailable)."""
        if setpoint is None or trv_c is None or not quiet:
            self._restart(now)
            self.setpoint = setpoint
            return None, None
        if self.setpoint is None or abs(setpoint - self.setpoint) >= 0.01:
            if self.setpoint is not None:
                self.rising = setpoint > self.setpoint
            self.setpoint, self.setpoint_since = setpoint, now
            self.last_point = self.last_drift = None
        cell = grid(setpoint - trv_c)
        if self.cell is None or abs(cell - self.cell) >= 0.01:
            if self.cell is not None and self.setpoint_since != now:
                self.rising = cell > self.cell
            self.cell, self.cell_since = cell, now
            self.last_point = self.last_drift = None
        self.warm_since = (self.warm_since or now) if warm else None
        if valve is not None and fresh:
            self.valves.append((now, valve))
        while self.valves and now - self.valves[0][0] > DRIFT_OVER + MEDIAN_OVER:
            self.valves.popleft()
        calm_since = max(
            moment if moment is not None else now for moment in (self.setpoint_since, self.cell_since, self.warm_since)
        )
        if self.warm_since is None or now - calm_since < CALM_FOR:
            return None, None
        recent = [value for moment, value in self.valves if now - moment <= MEDIAN_OVER]
        if len(recent) < 2:
            return None, None
        valve_now = statistics.median(recent)
        point = None
        if self.last_point is None or now - self.last_point >= CALM_FOR:
            self.last_point = now
            point = MapPoint(now, cell, self.rising, supply, round(valve_now, 1), Source.DAILY)
        drift = None
        if now - calm_since >= DRIFT_OVER and (self.last_drift is None or now - self.last_drift >= DRIFT_OVER):
            earlier = [
                (moment, value)
                for moment, value in self.valves
                if moment >= calm_since and DRIFT_OVER - MEDIAN_OVER <= now - moment <= DRIFT_OVER + MEDIAN_OVER
            ]
            if earlier:
                moment, before = earlier[0]
                hours = (now - moment).total_seconds() / 3600
                stuck = (before <= CLOSED and valve_now <= CLOSED) or (before >= 95.0 and valve_now >= 95.0)
                if hours > 0 and not stuck:
                    self.last_drift = now
                    drift = Drift(now, cell, round((valve_now - before) / hours, 1))
        return point, drift

    def _restart(self, now: datetime) -> None:
        self.setpoint_since = self.cell_since = now
        self.last_point = self.last_drift = None


@dataclass
class _Open:
    at: datetime
    delta_k: float
    before: float
    own: bool
    moved_at: datetime | None = None
    anchor: float | None = None
    anchor_at: datetime | None = None


@dataclass
class StepWatch:
    """The step response of one thermostat: fed every minute, gives a Response when the valve settled after
    a change of the setpoint (or did not move for an hour)."""

    setpoint: float | None = None
    last_valve: float | None = None
    open: _Open | None = None

    def update(
        self, now: datetime, *, setpoint: float | None, valve: float | None, fresh: bool, own: bool = False
    ) -> Response | None:
        result = None
        if setpoint is not None and self.setpoint is not None and abs(setpoint - self.setpoint) >= STEP_FROM_K:
            # A new step; one still open is superseded.
            self.open = None
            if self.last_valve is not None:
                self.open = _Open(at=now, delta_k=round(setpoint - self.setpoint, 2), before=self.last_valve, own=own)
        if setpoint is not None:
            self.setpoint = setpoint
        current = self.open
        if current is not None and valve is not None and fresh:
            if current.moved_at is None and abs(valve - current.before) >= MOVE_POINTS:
                current.moved_at = now
            if current.moved_at is not None:
                if current.anchor is None or abs(valve - current.anchor) >= MOVE_POINTS:
                    current.anchor, current.anchor_at = valve, now
                elif current.anchor_at is not None and now - current.anchor_at >= STEADY_FOR:
                    result = self._close(current, now, valve)
        if result is None and current is not None and now - current.at >= RESPONSE_MAX:
            after = valve if valve is not None else (self.last_valve if self.last_valve is not None else current.before)
            result = self._close(current, now, after)
        if valve is not None and fresh:
            self.last_valve = valve
        return result

    def _close(self, current: _Open, now: datetime, valve: float) -> Response:
        self.open = None
        reaction = _minutes(current.moved_at - current.at) if current.moved_at is not None else None
        settle = _minutes(current.anchor_at - current.at) if current.anchor_at is not None else None
        speed = None
        if settle is not None:
            speed = round(abs(valve - current.before) / max(1.0, settle), 1)
        return Response(
            at=current.at,
            delta_k=current.delta_k,
            valve_before=current.before,
            valve_after=valve,
            reaction_min=reaction,
            settle_min=settle,
            speed=speed,
            own=current.own,
        )


# --- persistence -----------------------------------------------------------------------------------------


def map_to_json(valve_map: ValveMap) -> dict[str, Any]:
    return {
        "points": [
            [point.at.isoformat(), point.offset_k, point.rising, point.supply.value, point.valve, point.source.value]
            for point in valve_map.points
        ],
        "responses": [
            {
                "at": response.at.isoformat(),
                "delta_k": response.delta_k,
                "before": response.valve_before,
                "after": response.valve_after,
                "reaction_min": response.reaction_min,
                "settle_min": response.settle_min,
                "speed": response.speed,
                "own": response.own,
            }
            for response in valve_map.responses
        ],
        "drifts": [[drift.at.isoformat(), drift.offset_k, drift.per_h] for drift in valve_map.drifts],
    }


def map_from_json(raw: Any) -> ValveMap:
    if not isinstance(raw, dict):
        return ValveMap()
    points = []
    for item in raw.get("points") or []:
        try:
            at, offset, rising, supply, valve, source = item
            points.append(
                MapPoint(_aware(at), float(offset), bool(rising), Supply(supply), float(valve), Source(source))
            )
        except (TypeError, ValueError):
            continue
    responses = []
    for item in raw.get("responses") or []:
        try:
            responses.append(
                Response(
                    at=_aware(item["at"]),
                    delta_k=float(item["delta_k"]),
                    valve_before=float(item["before"]),
                    valve_after=float(item["after"]),
                    reaction_min=_optional(item.get("reaction_min")),
                    settle_min=_optional(item.get("settle_min")),
                    speed=_optional(item.get("speed")),
                    own=bool(item.get("own", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    drifts = []
    for item in raw.get("drifts") or []:
        try:
            at, offset, per_h = item
            drifts.append(Drift(_aware(at), float(offset), float(per_h)))
        except (TypeError, ValueError):
            continue
    return ValveMap(
        points=tuple(points[-POINTS_KEEP:]),
        responses=tuple(responses[-RESPONSES_KEEP:]),
        drifts=tuple(drifts[-DRIFTS_KEEP:]),
    )


# --- helpers ---------------------------------------------------------------------------------------------


def _weighted(points: Sequence[MapPoint], now: datetime) -> float:
    weights = [
        float(0.5 ** (max(0.0, (now - point.at).total_seconds()) / HALF_LIFE.total_seconds())) for point in points
    ]
    total = sum(weights)
    value = sum(weight * point.valve for weight, point in zip(weights, points, strict=True)) / total
    return round(value, 1)


def _line(cells: Sequence[Cell]) -> list[tuple[float, float]]:
    return sorted((cell.offset_k, cell.valve) for cell in cells)


def _toward(drift: Drift) -> float:
    """The drift in the direction the offset pushes (positive: the integral winds up)."""
    return drift.per_h if drift.offset_k > 0 else -drift.per_h


def _median(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return round(statistics.median(known), 1) if known else None


def _minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60, 1)


def _optional(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _aware(value: Any) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError("naive time")
    return moment
