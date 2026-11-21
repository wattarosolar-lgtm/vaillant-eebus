"""How a thermostat's valve answers its setpoint, learned in test runs (punkt1-trv-regelung.md 3b).

A test run moves the setpoint in steps; each step that settled is a point:
the offset of the setpoint to the temperature at the thermostat and the valve
opening, rising and falling, with the pipe temperatures of the probe pair.
The W600 regulates with a PI controller, so the points describe how the
thermostat behaves with warm water, not a fixed line; the app uses only the
openings the thermostat really reached.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

FULL_OPEN = 90.0
OPEN_FROM = 5.0
# Results kept per thermostat.
KEEP_RESULTS = 5


@dataclass(frozen=True, slots=True)
class Step:
    setpoint_c: float
    # Temperature at the thermostat and in the room when the step settled.
    trv_c: float | None
    room_c: float | None
    # Setpoint minus temperature at the thermostat.
    offset_k: float | None
    valve: float | None
    rising: bool
    # Minutes of the step until it settled and until the valve first moved.
    minutes: float
    moved_after_min: float | None
    flow_c: float | None = None
    return_c: float | None = None
    # Heat output of the radiator, estimated from the pipe temperatures and its rated output.
    heat_w: float | None = None


@dataclass(frozen=True, slots=True)
class TestResult:
    # Not a test case (pytest collects classes named Test...).
    __test__ = False

    started_at: datetime
    ended_at: datetime
    # The valve was fully open and closed again: the run went through.
    complete: bool
    # Why it ended, German.
    ending: str
    steps: tuple[Step, ...]

    @property
    def opening_k(self) -> float | None:
        """Offset at which the valve first opened (rising)."""
        return _first(self.steps, rising=True, test=lambda valve: valve > OPEN_FROM)

    @property
    def full_k(self) -> float | None:
        return _first(self.steps, rising=True, test=lambda valve: valve >= FULL_OPEN)

    @property
    def closing_k(self) -> float | None:
        """Offset at which the valve was closed again (falling)."""
        return _first(self.steps, rising=False, test=lambda valve: valve <= OPEN_FROM)

    @property
    def reaction_min(self) -> float | None:
        moved = [step.moved_after_min for step in self.steps if step.moved_after_min is not None]
        return round(statistics.median(moved), 1) if moved else None


@dataclass(frozen=True, slots=True)
class ValveCurve:
    results: tuple[TestResult, ...] = ()

    @property
    def learned(self) -> bool:
        """At least one complete test run: required before the room may be controlled."""
        return any(result.complete for result in self.results)

    @property
    def last(self) -> TestResult | None:
        return self.results[-1] if self.results else None

    def add(self, result: TestResult) -> ValveCurve:
        return ValveCurve(results=(*self.results, result)[-KEEP_RESULTS:])

    def rising(self) -> list[tuple[float, float]]:
        """(offset, opening) of the rising steps of the complete runs, sorted by offset."""
        points = [
            (step.offset_k, step.valve)
            for result in self.results
            if result.complete
            for step in result.steps
            if step.rising and step.offset_k is not None and step.valve is not None
        ]
        return sorted(points)

    def levels(self) -> list[float]:
        """Openings the thermostat really reached, rounded to 10 %."""
        return sorted({round(valve / 10) * 10.0 for _offset, valve in self.rising()})

    def offset_for(self, opening: float) -> float | None:
        """The smallest offset at which the thermostat opened at least this far (median over the runs)."""
        if opening <= OPEN_FROM:
            closing = [result.closing_k for result in self.results if result.complete and result.closing_k is not None]
            return statistics.median(closing) if closing else None
        per_run = []
        for result in self.results:
            if not result.complete:
                continue
            reached = [
                step.offset_k
                for step in result.steps
                if step.rising and step.offset_k is not None and step.valve is not None and step.valve >= opening
            ]
            if reached:
                per_run.append(min(reached))
        return round(statistics.median(per_run), 2) if per_run else None

    def nearest_level(self, opening: float) -> float | None:
        levels = self.levels()
        return min(levels, key=lambda level: abs(level - opening)) if levels else None


def curve_to_json(curve: ValveCurve) -> list[dict[str, Any]]:
    return [
        {
            "started_at": result.started_at.isoformat(),
            "ended_at": result.ended_at.isoformat(),
            "complete": result.complete,
            "ending": result.ending,
            "steps": [
                {
                    "setpoint_c": step.setpoint_c,
                    "trv_c": step.trv_c,
                    "room_c": step.room_c,
                    "offset_k": step.offset_k,
                    "valve": step.valve,
                    "rising": step.rising,
                    "minutes": step.minutes,
                    "moved_after_min": step.moved_after_min,
                    "flow_c": step.flow_c,
                    "return_c": step.return_c,
                    "heat_w": step.heat_w,
                }
                for step in result.steps
            ],
        }
        for result in curve.results
    ]


def curve_from_json(raw: Any) -> ValveCurve:
    results = []
    for item in raw if isinstance(raw, list) else []:
        try:
            results.append(
                TestResult(
                    started_at=datetime.fromisoformat(item["started_at"]),
                    ended_at=datetime.fromisoformat(item["ended_at"]),
                    complete=bool(item["complete"]),
                    ending=str(item.get("ending", "")),
                    steps=tuple(
                        Step(
                            setpoint_c=float(step["setpoint_c"]),
                            trv_c=_optional(step.get("trv_c")),
                            room_c=_optional(step.get("room_c")),
                            offset_k=_optional(step.get("offset_k")),
                            valve=_optional(step.get("valve")),
                            rising=bool(step["rising"]),
                            minutes=float(step["minutes"]),
                            moved_after_min=_optional(step.get("moved_after_min")),
                            flow_c=_optional(step.get("flow_c")),
                            return_c=_optional(step.get("return_c")),
                            heat_w=_optional(step.get("heat_w")),
                        )
                        for step in item.get("steps", [])
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return ValveCurve(results=tuple(results[-KEEP_RESULTS:]))


def _first(steps: Sequence[Step], *, rising: bool, test: Any) -> float | None:
    for step in steps:
        if step.rising == rising and step.valve is not None and step.offset_k is not None and test(step.valve):
            return step.offset_k
    return None


def _optional(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
