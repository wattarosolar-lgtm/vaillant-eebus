"""The sweep ("schneller Durchlauf") of one thermostat (1.4.3, plan-1.4.3.md A).

It learns in about half an hour what the valve does at which setpoint, without
the probe pair and without warm water: every two minutes a new setpoint, then
the valve position (the Comet reports it about 15 seconds after a setpoint,
the W600 is read a minute after it).

1. The setpoint from before is kept; the setpoint goes to the temperature at
   the thermostat (the starting point).
2. Up by 0.5 K a step - by 1 K after a step that moved the valve less than 10
   points - until the valve is fully open (at least 85 %, or a plateau: at
   least 50 % and 1 K more brought less than 5 points), or the thermostat's
   upper limit, or 6 K above the starting point.
3. Down the same way until the valve is closed (at most 5 %) or 2 K below the
   temperature at the thermostat.
4. Back to the setpoint from before (write gate: RESTORE, not counted).

Without a fresh valve position two minutes after a step it waits up to four
minutes, then it stops ("Ventilstellung fehlt"). At most 20 commands; with
them used up it goes back with what it has. It stops at once - back to the
setpoint from before - when a window opens, the room gets 1 K warmer than its
setpoint, the thermostat cannot be set or its setpoint was changed by hand.

Every step is a point of the valve map (source "Durchlauf": what the valve did
at once, rising or falling, with the warm water as it was) and a step
response. Such points only fill gaps: a cell with points of a test run or of
everyday operation does not count them (learning.py). The sweep is also the
first part of a night's test run, unless one is younger than 7 days.

advance() is a pure step function: the room control feeds it every minute and
sends what it asks for through the write gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from vaillant_eebus.rooms.learning import (
    CLOSED,
    FULL_OPEN,
    MOVE_POINTS,
    PLATEAU_FROM,
    PLATEAU_GAIN,
    PLATEAU_K,
    MapPoint,
    Response,
    Source,
    Supply,
    grid,
)
from vaillant_eebus.write_gate import SWEEP_COMMANDS_MAX, ThermostatLimits

STEP_EVERY = timedelta(minutes=2)
VALVE_WAIT = timedelta(minutes=4)
SMALL_STEP_K = 0.5
WEAK_STEP_K = 1.0
WEAK_MOVE = 10.0
UP_MAX_K = 6.0
DOWN_BELOW_K = 2.0
ROOM_OVER_K = 1.0
# A setpoint other than the sweep's this long after its command is a change by hand.
MANUAL_AFTER = timedelta(minutes=1)
# A complete sweep younger than this spares the night's test run its own.
FRESH_FOR = timedelta(days=7)
RESULTS_KEEP = 3
BUDGET_USED = "Befehle des Durchlaufs verbraucht"
MINUS = "\u2212"


class SweepState(StrEnum):
    RUNNING = "running"
    DONE = "done"
    ABORTED = "aborted"


class SweepPhase(StrEnum):
    START = "start"
    UP = "up"
    DOWN = "down"


STATE_NAMES = {SweepState.RUNNING: "läuft", SweepState.DONE: "fertig", SweepState.ABORTED: "abgebrochen"}
PHASE_NAMES = {SweepPhase.START: "Startpunkt", SweepPhase.UP: "aufwärts", SweepPhase.DOWN: "abwärts"}


@dataclass(frozen=True, slots=True)
class SweepInputs:
    now: datetime
    # The room's setpoint now, the room and the thermostat.
    setpoint_c: float
    room_c: float | None
    trv_c: float | None
    trv_setpoint_c: float | None
    valve: float | None
    # When the thermostat last confirmed its valve position.
    valve_at: datetime | None
    window_open: bool
    limits: ThermostatLimits | None
    # Live, room control on, the thermostat reachable and without a blocking warning.
    may_write: bool
    # A command to the thermostat is not confirmed yet.
    sending: bool
    supply: Supply = Supply.UNKNOWN


@dataclass(frozen=True, slots=True)
class SweepPoint:
    at: datetime
    setpoint_c: float
    trv_c: float
    offset_k: float
    valve: float
    rising: bool
    # Minutes after the setpoint until the valve had moved; None: it did not move.
    reaction_min: float | None = None


@dataclass(frozen=True, slots=True)
class SweepResult:
    started_at: datetime
    ended_at: datetime
    complete: bool
    ending: str
    points: tuple[SweepPoint, ...]


@dataclass(frozen=True, slots=True)
class SweepAction:
    # "send": set the thermostat to celsius; "end": the sweep ended (back to celsius if given); "wait".
    kind: str
    celsius: float | None = None


@dataclass
class Sweep:
    room_id: str
    trv: str
    started_at: datetime
    # The first part of a night's test run: the run goes on from where the sweep ends.
    part_of_test: bool = False
    state: SweepState = SweepState.RUNNING
    phase: SweepPhase = SweepPhase.START
    message: str = "Startpunkt"
    before_c: float | None = None
    start_c: float | None = None
    setpoint_c: float | None = None
    commands: int = 0
    step_at: datetime | None = None
    step_valve: float | None = None
    moved_at: datetime | None = None
    last_size: float | None = None
    points: list[SweepPoint] = field(default_factory=list)
    reached_full: bool = False
    reached_closed: bool = False
    ended_at: datetime | None = None
    # The points and responses not taken into the valve map yet.
    new_points: list[MapPoint] = field(default_factory=list)
    new_responses: list[Response] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.state is SweepState.RUNNING

    def result(self) -> SweepResult | None:
        if self.ended_at is None:
            return None
        return SweepResult(
            started_at=self.started_at,
            ended_at=self.ended_at,
            complete=self.reached_full and self.reached_closed,
            ending=self.message,
            points=tuple(self.points),
        )


def start_problem(inputs: SweepInputs) -> str | None:
    """Why a sweep cannot start now; None when it can."""
    if not inputs.may_write:
        return "Die App darf das Thermostat gerade nicht stellen (Live-Betrieb, Raumregelung an, Warnungen)."
    if inputs.limits is None or inputs.trv_c is None or inputs.trv_setpoint_c is None:
        return "Das Thermostat meldet seine Werte nicht."
    if inputs.window_open:
        return "Ein Fenster ist offen."
    if inputs.room_c is not None and inputs.room_c > inputs.setpoint_c + ROOM_OVER_K:
        return "Der Raum ist mehr als 1 K wärmer als sein Soll."
    return None


def begin(sweep: Sweep, inputs: SweepInputs, *, before_c: float | None = None) -> SweepAction:
    """The first step: the setpoint from before is kept, the setpoint goes to the temperature at the thermostat."""
    assert inputs.limits is not None and inputs.trv_c is not None
    sweep.before_c = before_c if before_c is not None else inputs.trv_setpoint_c
    sweep.start_c = inputs.limits.round(inputs.trv_c)
    sweep.phase = SweepPhase.START
    sweep.message = "Startpunkt: Soll auf die Temperatur am Thermostat"
    return _set(sweep, inputs, sweep.start_c)


def advance(sweep: Sweep, inputs: SweepInputs) -> SweepAction:
    if sweep.state is not SweepState.RUNNING:
        return SweepAction("wait")
    problem = _stop_reason(sweep, inputs)
    if problem is not None:
        return _finish(sweep, inputs, problem)
    assert sweep.step_at is not None and sweep.setpoint_c is not None
    elapsed = inputs.now - sweep.step_at
    fresh = inputs.valve is not None and inputs.valve_at is not None and inputs.valve_at > sweep.step_at
    if (
        fresh
        and sweep.moved_at is None
        and sweep.step_valve is not None
        and inputs.valve is not None
        and abs(inputs.valve - sweep.step_valve) >= MOVE_POINTS
    ):
        sweep.moved_at = inputs.valve_at
    if elapsed < STEP_EVERY - timedelta(seconds=5):
        return SweepAction("wait")
    if not fresh:
        if elapsed < VALVE_WAIT:
            return SweepAction("wait")
        return _finish(sweep, inputs, "Ventilstellung fehlt")
    assert inputs.valve is not None and inputs.trv_c is not None
    _record(sweep, inputs)
    return _next(sweep, inputs)


def abort(sweep: Sweep, now: datetime, reason: str) -> SweepAction:
    """Stop the sweep from outside (user, app, mode); back to the setpoint from before."""
    sweep.state = SweepState.ABORTED
    sweep.message = reason
    sweep.ended_at = now
    return SweepAction("end", celsius=sweep.before_c)


def _stop_reason(sweep: Sweep, inputs: SweepInputs) -> str | None:
    if inputs.window_open:
        return "Fenster offen"
    if inputs.room_c is not None and inputs.room_c > inputs.setpoint_c + ROOM_OVER_K:
        return "Raum über Soll + 1 K"
    if not inputs.may_write or inputs.limits is None or inputs.trv_c is None:
        return "Das Thermostat ist nicht erreichbar oder darf nicht gestellt werden"
    if (
        not inputs.sending
        and sweep.step_at is not None
        and sweep.setpoint_c is not None
        and inputs.trv_setpoint_c is not None
        and abs(inputs.trv_setpoint_c - sweep.setpoint_c) >= 0.01
        and inputs.now - sweep.step_at >= MANUAL_AFTER
    ):
        return "Sollwert am Thermostat von Hand geändert (oder nicht übernommen)"
    return None


def _record(sweep: Sweep, inputs: SweepInputs) -> None:
    assert sweep.setpoint_c is not None and sweep.step_at is not None
    assert inputs.valve is not None and inputs.trv_c is not None
    if sweep.phase is SweepPhase.START:
        rising = sweep.before_c is None or sweep.setpoint_c >= sweep.before_c
    else:
        rising = sweep.phase is SweepPhase.UP
    reaction = (sweep.moved_at - sweep.step_at).total_seconds() / 60 if sweep.moved_at is not None else None
    offset = round(sweep.setpoint_c - inputs.trv_c, 2)
    point = SweepPoint(
        at=inputs.now,
        setpoint_c=sweep.setpoint_c,
        trv_c=inputs.trv_c,
        offset_k=offset,
        valve=inputs.valve,
        rising=rising,
        reaction_min=round(reaction, 1) if reaction is not None else None,
    )
    previous = sweep.points[-1] if sweep.points else None
    sweep.points.append(point)
    sweep.new_points.append(MapPoint(inputs.now, grid(offset), rising, inputs.supply, inputs.valve, Source.SWEEP))
    if previous is not None and sweep.step_valve is not None:
        sweep.new_responses.append(
            Response(
                at=sweep.step_at,
                delta_k=round(sweep.setpoint_c - previous.setpoint_c, 2),
                valve_before=sweep.step_valve,
                valve_after=inputs.valve,
                reaction_min=point.reaction_min,
                settle_min=None,
                speed=None,
                own=True,
            )
        )


def _next(sweep: Sweep, inputs: SweepInputs) -> SweepAction:
    assert inputs.valve is not None and inputs.limits is not None and sweep.setpoint_c is not None
    if sweep.phase is SweepPhase.START:
        sweep.phase = SweepPhase.UP
        sweep.last_size = None
    if sweep.phase is SweepPhase.UP:
        if _fully_open(sweep, inputs.valve):
            sweep.reached_full = True
            return _turn_down(sweep, inputs)
        assert sweep.start_c is not None
        if sweep.setpoint_c >= sweep.start_c + UP_MAX_K - 1e-6:
            return _turn_down(sweep, inputs)
        if sweep.commands >= SWEEP_COMMANDS_MAX:
            return _finish(sweep, inputs, BUDGET_USED)
        target = inputs.limits.round(min(inputs.limits.max_c, sweep.setpoint_c + _size(sweep, inputs.valve)))
        if target <= sweep.setpoint_c + 1e-6:
            # At the thermostat's upper limit: the valve opens no further by the setpoint.
            return _turn_down(sweep, inputs)
        sweep.message = f"aufwärts, Soll {_number(target)} °C"
        return _set(sweep, inputs, target)
    return _down(sweep, inputs)


def _turn_down(sweep: Sweep, inputs: SweepInputs) -> SweepAction:
    sweep.phase = SweepPhase.DOWN
    sweep.last_size = None
    return _down(sweep, inputs)


def _down(sweep: Sweep, inputs: SweepInputs) -> SweepAction:
    assert inputs.valve is not None and inputs.limits is not None and sweep.setpoint_c is not None
    assert inputs.trv_c is not None
    last = sweep.points[-1] if sweep.points else None
    if inputs.valve <= CLOSED and last is not None and not last.rising:
        sweep.reached_closed = True
        return _finish(sweep, inputs, "Fertig", done=True)
    bottom = inputs.limits.round(max(inputs.limits.min_c, inputs.trv_c - DOWN_BELOW_K))
    if sweep.setpoint_c <= bottom + 1e-6:
        return _finish(sweep, inputs, "Fertig (2 K unter dem Thermostat, Ventil nicht ganz zu)", done=True)
    if sweep.commands >= SWEEP_COMMANDS_MAX:
        return _finish(sweep, inputs, BUDGET_USED)
    target = max(bottom, inputs.limits.round(sweep.setpoint_c - _size(sweep, inputs.valve)))
    sweep.message = f"abwärts, Soll {_number(target)} °C"
    return _set(sweep, inputs, target)


def _size(sweep: Sweep, valve: float) -> float:
    """0.5 K; 1 K after a step that moved the valve less than 10 points."""
    moved = abs(valve - sweep.step_valve) if sweep.step_valve is not None else None
    size = WEAK_STEP_K if sweep.last_size is not None and moved is not None and moved < WEAK_MOVE else SMALL_STEP_K
    sweep.last_size = size
    return size


def _fully_open(sweep: Sweep, valve: float) -> bool:
    if valve >= FULL_OPEN:
        return True
    current = sweep.points[-1]
    return any(
        earlier.rising
        and earlier.valve >= PLATEAU_FROM
        and current.setpoint_c - earlier.setpoint_c >= PLATEAU_K - 1e-6
        and valve - earlier.valve < PLATEAU_GAIN
        for earlier in sweep.points[:-1]
    )


def _set(sweep: Sweep, inputs: SweepInputs, celsius: float) -> SweepAction:
    sweep.setpoint_c = celsius
    sweep.step_at = inputs.now
    sweep.step_valve = inputs.valve
    sweep.moved_at = None
    if inputs.trv_setpoint_c is not None and abs(inputs.trv_setpoint_c - celsius) < 0.01:
        return SweepAction("wait")
    sweep.commands += 1
    return SweepAction("send", celsius=celsius)


def _finish(sweep: Sweep, inputs: SweepInputs, reason: str, *, done: bool = False) -> SweepAction:
    sweep.state = SweepState.DONE if done else SweepState.ABORTED
    sweep.message = reason
    sweep.ended_at = inputs.now
    return SweepAction("end", celsius=sweep.before_c)


def fresh(results: list[SweepResult], now: datetime) -> bool:
    """A complete sweep younger than 7 days."""
    return any(result.complete and now - result.ended_at < FRESH_FOR for result in results)


def describe(result: SweepResult) -> str:
    """Short German summary for the protocol."""
    points = result.points
    full = next((point.offset_k for point in points if point.rising and point.valve >= FULL_OPEN), None)
    opened = next((point.offset_k for point in points if point.rising and point.valve > CLOSED), None)
    closed = next((point.offset_k for point in points if not point.rising and point.valve <= CLOSED), None)
    parts = [f"{len(points)} Punkte"]
    if opened is not None:
        parts.append(f"offen ab {_signed(opened)} K")
    if full is not None:
        parts.append(f"ganz offen ab {_signed(full)} K")
    if closed is not None:
        parts.append(f"zu ab {_signed(closed)} K")
    reactions = sorted(point.reaction_min for point in points if point.reaction_min is not None)
    if reactions:
        parts.append(f"Reaktion nach etwa {_number(reactions[len(reactions) // 2])} min")
    return ", ".join(parts)


def _number(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _signed(value: float) -> str:
    return f"{value:+.1f}".replace(".", ",").replace("-", MINUS)


# --- persistence -----------------------------------------------------------------------------------------


def result_to_json(result: SweepResult) -> dict[str, Any]:
    return {
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat(),
        "complete": result.complete,
        "ending": result.ending,
        "points": [
            [
                point.at.isoformat(),
                point.setpoint_c,
                point.trv_c,
                point.offset_k,
                point.valve,
                point.rising,
                point.reaction_min,
            ]
            for point in result.points
        ],
    }


def result_from_json(raw: Any) -> SweepResult | None:
    if not isinstance(raw, dict):
        return None
    try:
        points = []
        for at, setpoint, trv_c, offset, valve, rising, reaction in raw.get("points") or []:
            points.append(
                SweepPoint(
                    at=_aware(at),
                    setpoint_c=float(setpoint),
                    trv_c=float(trv_c),
                    offset_k=float(offset),
                    valve=float(valve),
                    rising=bool(rising),
                    reaction_min=float(reaction) if isinstance(reaction, int | float) else None,
                )
            )
        return SweepResult(
            started_at=_aware(raw["started_at"]),
            ended_at=_aware(raw["ended_at"]),
            complete=bool(raw.get("complete", False)),
            ending=str(raw.get("ending", "")),
            points=tuple(points),
        )
    except (KeyError, TypeError, ValueError):
        return None


def running_to_json(sweep: Sweep) -> dict[str, Any]:
    """What a restart needs of a running sweep: its thermostat goes back to the setpoint from before."""
    return {"room_id": sweep.room_id, "trv": sweep.trv, "before_c": sweep.before_c, "part_of_test": sweep.part_of_test}


def _aware(value: Any) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError("naive time")
    return moment
