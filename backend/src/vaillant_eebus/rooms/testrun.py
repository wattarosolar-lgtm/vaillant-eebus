"""The test run of one thermostat (punkt1-trv-regelung.md 3b; steps chosen by the valve since 1.4.2).

Armed by hand for the radiator the probe pair (flow and return pipe sensors)
is fixed to; it runs at night: it starts from 22:00 as soon as the room is
between 0.5 K below and 0.2 K above its setpoint, no window is open and warm
water reaches the radiator, at the latest at 1:00 - else it tries the next
night, three nights at most. Then:

1. setpoint = temperature at the thermostat (starting point);
2. up until the valve is fully open (at least 85 %, or at least 50 % and 1 K
   more brought less than 5 points), at most to the thermostat's limit;
3. down until the valve is closed (at most 5 %), at most to 2 K below the
   temperature at the thermostat;
4. back to the setpoint from before the run, at the latest at 5:00.

The steps follow the valve (plan-1.4.2-testlauf-lernt.md C, decided with the
user 23.11.2026): the first of each direction is 1 K; after a step that moved
the valve less than 10 % per K the next is 2 K, then 3 K (the largest); after
one that moved it more than 40 points 0.5 K; else the step that brings the
valve to about the next mark (25 / 50 / 75 / 85 % up, 50 / 25 / 5 % down). The
run plans with the time until 5:00: when the steps left do not reach every
mark, it aims at a later one. A run cut off at 5:00 (or by its budget) after
the valve was fully open goes on the next night downward only, starting at the
offset where it was fully open; so does a run armed for a thermostat whose
valve map already knows "fully open" but not "closed".

A step ends when valve and return temperature stood still for 10 minutes,
after 20 to 40 minutes with warm water. It stops at once - back to the setpoint
from before - when the room gets 1 K warmer than its setpoint, a window opens,
the valve position or the warm water is missing for 30 minutes. At most 16
commands a night; the way back is not counted (write gate: RESTORE). Complete
is a run that had the valve fully open and closed again. A night counts as a
try only if its start window began after the run was armed.

A reading of the probe pair counts up to two hours after its last report
(1.4.1): many sensors report only when the value changes, so a quiet pipe is
silent for an hour. A pair whose return is warmer than its flow while warm
water reaches the open valve looks swapped; no run starts then
(fehlerliste-abnahme-1.4.0.md, finding 1).

Since 1.4.3 (plan-1.4.3.md A) a run begins with a sweep (sweep.py, about half
an hour without the probe pair) unless the thermostat had a complete one in
the last 7 days. With a valve map that knows "closed" and "fully open" the run
then holds only four positions with the probe pair - closed, about 30 %, about
60 % and fully open, from the map - 20 to 40 minutes each as before, for the
spread and the heat. Without such a map it steps up and down as before.

advance() is a pure step function: the room control feeds it every minute and
sends what it asks for through the write gate.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from vaillant_eebus.hems.config import in_window
from vaillant_eebus.rooms.curve import Step, TestResult
from vaillant_eebus.rooms.learning import (
    CLOSED,
    FULL_OPEN,
    GRID_K,
    PLATEAU_FROM,
    PLATEAU_GAIN,
    PLATEAU_K,
    Supply,
    ValveMap,
)
from vaillant_eebus.write_gate import TEST_COMMANDS_MAX, ThermostatLimits

STEP_MIN = timedelta(minutes=20)
STEP_MAX = timedelta(minutes=40)
STEADY_FOR = timedelta(minutes=10)
STEADY_VALVE = 5.0
STEADY_RETURN_K = 0.3
MISSING_FOR = timedelta(minutes=30)
ROOM_OVER_K = 1.0
START_BELOW_K = 0.5
START_ABOVE_K = 0.2
MAX_NIGHTS = 3
# Steps (1.4.2): the first of a direction, the smallest, the large ones; a step that moved the valve less than
# WEAK_GAIN per K makes the next one large, one that moved it more than STRONG_CHANGE points makes it small.
FIRST_STEP_K = 1.0
SMALL_STEP_K = 0.5
LARGE_STEP_K = 2.0
LARGEST_STEP_K = 3.0
WEAK_GAIN = 10.0
STRONG_CHANGE = 40.0
UP_MARKS = (25.0, 50.0, 75.0, FULL_OPEN)
DOWN_MARKS = (50.0, 25.0, CLOSED)
# A mark this close counts as reached.
MARK_MARGIN = 2.0
# How long a step takes until the run has measured its own.
STEP_MINUTES = 30.0
DOWN_BELOW_K = 2.0
# The positions held with the probe pair after a sweep (besides closed and fully open).
HOLD_LEVELS = (30.0, 60.0)
# Warm water at the radiator: its flow at least this much above the room.
HEAT_K = 5.0
# A reading of the probe pair counts this long after its last report. Sensors that report only on a change
# (the Sonoff probes: by 0.2 K) stay silent for 45-90 minutes while the pipe's temperature stands still, and
# Home Assistant does not show a report of an unchanged value (last_reported = last_changed).
PROBE_VALID = timedelta(hours=2)
# Flow and return look swapped when the return is this much warmer than the flow for SWAP_FOR, while the
# valve is at least SWAP_VALVE open and warm water reaches the radiator; the look goes away after the same
# time the other way round.
SWAP_K = 1.0
SWAP_FOR = timedelta(minutes=15)
SWAP_VALVE = 20.0
WAITING = "Wartet: "
# A run ended this way after the valve was fully open goes on the next night, downward only.
NIGHT_OVER = "Ende des Nachtfensters"
BUDGET_USED = "Befehle des Testlaufs verbraucht"
_GOES_ON = frozenset({NIGHT_OVER, BUDGET_USED})
GOES_ON = "Geht in der nächsten Nacht weiter, nur abwärts"
# Rated output's temperature difference to the room (flow/return mean - 20 °C) by basis.
RATED_DELTA_K = {"75/65": 50.0, "55/45": 30.0, "50/40": 25.0}
RADIATOR_EXPONENT = 1.3
MINUS = "\u2212"


class TestState(StrEnum):
    # Not a test case (pytest collects classes named Test...).
    __test__ = False

    ARMED = "armed"
    RUNNING = "running"
    DONE = "done"
    ABORTED = "aborted"
    FAILED = "failed"


class Phase(StrEnum):
    # The sweep at the start (1.4.3); the room control runs it.
    SWEEP = "sweep"
    BASELINE = "baseline"
    UP = "up"
    DOWN = "down"
    # Holding the positions from the valve map (1.4.3).
    HOLD = "hold"


STATE_NAMES = {
    TestState.ARMED: "vorgemerkt",
    TestState.RUNNING: "läuft",
    TestState.DONE: "fertig",
    TestState.ABORTED: "abgebrochen",
    TestState.FAILED: "nicht möglich",
}


@dataclass(frozen=True, slots=True)
class TestInputs:
    # Not a test case (pytest collects classes named Test...).
    __test__ = False

    now: datetime
    local: datetime
    setpoint_c: float
    room_c: float | None
    trv_c: float | None
    trv_setpoint_c: float | None
    valve: float | None
    flow_c: float | None
    return_c: float | None
    window_open: bool
    limits: ThermostatLimits | None
    # Why the probe pair cannot measure this radiator now (elsewhere, a sensor silent or missing, swapped);
    # None when it can.
    probe_problem: str | None
    # Live mode, room control on, nothing that keeps the app from setting the thermostat.
    may_write: bool
    # Rated output of the radiator and its basis, for the heat estimate.
    rated_w: float | None = None
    rated_basis: str = "75/65"
    # The thermostat had no complete sweep in the last 7 days: the run begins with one (1.4.3).
    sweep_needed: bool = False
    # Offsets to hold with the probe pair, from the valve map (hold_positions); empty: step up and down.
    positions: tuple[float, ...] = ()
    # The setpoint from before an earlier run that was not handed back cleanly (write gate); the run takes it as
    # its own setpoint before, so run and gate agree on the way back (1.4.3, finding 4 of the acceptance 1.4.0).
    restore_c: float | None = None


@dataclass(frozen=True, slots=True)
class Action:
    # "send": set the thermostat to celsius; "end": the night's run ended (back to celsius if given); "sweep":
    # the room control runs a sweep as the first part; "wait".
    kind: str
    celsius: float | None = None
    note: str = ""


@dataclass
class TestRun:
    # Not a test case (pytest collects classes named Test...).
    __test__ = False

    room_id: str
    trv: str
    armed_at: datetime
    state: TestState = TestState.ARMED
    nights: int = 0
    counted_night: date | None = None
    # Why it has not started yet, or why it ended.
    message: str = ""
    # Downward only, starting at this offset to the temperature at the thermostat (the valve was fully open
    # there before).
    down_only: bool = False
    full_offset_k: float | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    phase: Phase | None = None
    setpoint_c: float | None = None
    before_c: float | None = None
    commands: int = 0
    step_started: datetime | None = None
    heated: float = 0.0
    last_tick: datetime | None = None
    no_heat_since: datetime | None = None
    no_valve_since: datetime | None = None
    # The valve when the step's command went out, and the size of the last step of this direction.
    step_valve: float | None = None
    phase_size: float | None = None
    moved_at: datetime | None = None
    readings: list[tuple[datetime, float, float | None]] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    reached_full: bool = False
    reached_closed: bool = False
    # The way down reached 2 K below the temperature at the thermostat.
    reached_bottom: bool = False
    # What the night measured, kept when the run goes on the next night.
    outcome: TestResult | None = None
    # The offsets held with the probe pair after a sweep, and which one is held now.
    positions: list[float] = field(default_factory=list)
    position_index: int = 0

    @property
    def active(self) -> bool:
        return self.state in (TestState.ARMED, TestState.RUNNING)

    def result(self) -> TestResult | None:
        if self.started_at is None or self.ended_at is None:
            return None
        complete = self.reached_full and self.reached_closed
        return TestResult(
            started_at=self.started_at,
            ended_at=self.ended_at,
            complete=complete,
            ending=self.message,
            steps=tuple(self.steps),
        )


def advance(run: TestRun, inputs: TestInputs, start: time, latest_start: time, end: time) -> Action:
    if run.state is TestState.ARMED:
        return _armed(run, inputs, start, latest_start, end)
    if run.state is TestState.RUNNING:
        return _running(run, inputs, start, end)
    return Action("wait")


def abort(run: TestRun, now: datetime, reason: str) -> Action:
    """Stop the run from outside (user, app, mode); back to the setpoint from before if it had started."""
    was_running = run.state is TestState.RUNNING
    run.state = TestState.ABORTED
    run.message = reason
    run.ended_at = now
    run.outcome = run.result() if was_running else None
    return Action("end", celsius=run.before_c if was_running else None, note=reason)


def start_problem(run: TestRun, inputs: TestInputs) -> str | None:
    """Why the run cannot start now; None when it can."""
    if not inputs.may_write:
        return "Die App darf das Thermostat gerade nicht stellen (Live-Betrieb, Raumregelung an, Warnungen)."
    if inputs.probe_problem is not None:
        return inputs.probe_problem
    if inputs.limits is None or inputs.trv_c is None or inputs.trv_setpoint_c is None:
        return "Das Thermostat meldet seine Werte nicht."
    if inputs.valve is None:
        return "Die Ventilstellung fehlt."
    if inputs.room_c is None:
        return "Die Raumtemperatur fehlt."
    if not inputs.setpoint_c - START_BELOW_K <= inputs.room_c <= inputs.setpoint_c + START_ABOVE_K:
        return "Der Raum ist nicht nahe genug an seinem Soll (0,5 K darunter bis 0,2 K darüber)."
    if inputs.window_open:
        return "Ein Fenster ist offen."
    if not _heat(inputs):
        return "Am Heizkörper kommt kein warmes Wasser an."
    return None


def _armed(run: TestRun, inputs: TestInputs, start: time, latest_start: time, end: time) -> Action:
    moment = inputs.local.time()
    night = night_of(inputs.local, start)
    if in_window(moment, start, latest_start):
        problem = start_problem(run, inputs)
        if problem is not None:
            run.message = f"{WAITING}{problem}"
            return Action("wait")
        run.state = TestState.RUNNING
        run.started_at = inputs.now
        run.ended_at = None
        run.before_c = inputs.restore_c if inputs.restore_c is not None else inputs.trv_setpoint_c
        run.commands = 0
        run.steps = []
        run.outcome = None
        run.reached_closed = run.reached_bottom = False
        run.reached_full = run.down_only
        run.phase_size = None
        run.positions = []
        if inputs.sweep_needed:
            run.phase = Phase.SWEEP
            run.step_started = inputs.now
            run.message = "Teil 1: schneller Durchlauf"
            return Action("sweep")
        return _begin(run, inputs)
    if in_window(moment, latest_start, end) and run.counted_night != night:
        run.counted_night = night
        night_began = datetime.combine(night, start, tzinfo=inputs.local.tzinfo)
        if run.armed_at > night_began:
            # Armed after this night's start window had begun: it does not count as a try.
            return Action("wait")
        run.nights += 1
        if run.nights >= MAX_NIGHTS:
            waited = waiting_for(run)
            reason = f"{waited}." if waited else "Die Bedingungen waren nie erfüllt."
            run.state = TestState.FAILED
            run.ended_at = inputs.now
            run.message = f"In {MAX_NIGHTS} Nächten nicht gestartet: {reason}"
            return Action("end", note=run.message)
    return Action("wait")


def _begin(run: TestRun, inputs: TestInputs) -> Action:
    """The part with the probe pair: the positions from the map, else up and down as before."""
    assert inputs.limits is not None and inputs.trv_c is not None
    if inputs.positions:
        run.phase = Phase.HOLD
        run.positions = list(inputs.positions)
        run.position_index = 0
        run.reached_full = False
        run.message = _hold_text(run)
        return _set(run, inputs, inputs.limits.round(inputs.trv_c + run.positions[0]))
    run.phase = Phase.BASELINE
    if run.down_only and run.full_offset_k is not None:
        run.message = "Ausgangslage ganz offen, dann nur abwärts"
        return _set(run, inputs, inputs.limits.round(inputs.trv_c + run.full_offset_k))
    run.message = "Ausgangslage"
    return _set(run, inputs, inputs.limits.round(inputs.trv_c))


def after_sweep(run: TestRun, inputs: TestInputs) -> Action:
    """The sweep at the start ended (done or cut short by its budget): the part with the probe pair."""
    if run.state is not TestState.RUNNING or run.phase is not Phase.SWEEP:
        return Action("wait")
    if inputs.limits is None or inputs.trv_c is None:
        return _finish(run, inputs, "Das Thermostat meldet seine Werte nicht")
    return _begin(run, inputs)


def hold_positions(valve_map: ValveMap, now: datetime) -> tuple[float, ...]:
    """The offsets to hold with the probe pair: closed (0.5 K below the highest closed one), about 30 % and 60 %
    and fully open, rising, from the map; empty while it does not know closed and fully open."""
    if not valve_map.ready(now):
        return ()
    closed = valve_map.offset_for(0.0, rising=True, supply=Supply.UNKNOWN, now=now)
    full = valve_map.full(now, rising=True) or valve_map.full(now)
    if closed is None or full is None:
        return ()
    offsets = {round(closed - GRID_K, 2), round(full[0], 2)}
    for level in HOLD_LEVELS:
        offset = valve_map.offset_for(level, rising=True, supply=Supply.UNKNOWN, now=now)
        if offset is not None and closed < offset < full[0]:
            offsets.add(round(offset, 2))
    return tuple(sorted(offsets))


def _hold_text(run: TestRun) -> str:
    offset = run.positions[run.position_index]
    count = len(run.positions)
    return f"Stellung {run.position_index + 1} von {count} ({_signed(offset)} K)"


def _running(run: TestRun, inputs: TestInputs, start: time, end: time) -> Action:
    if not in_window(inputs.local.time(), start, end):
        return _finish(run, inputs, NIGHT_OVER)
    if not inputs.may_write:
        return _finish(run, inputs, "Die App darf das Thermostat nicht mehr stellen")
    if inputs.room_c is not None and inputs.room_c > inputs.setpoint_c + ROOM_OVER_K:
        return _finish(run, inputs, "Raum über Soll + 1 K")
    if inputs.window_open:
        return _finish(run, inputs, "Fenster offen")
    if run.phase is Phase.SWEEP:
        # The room control runs the sweep; the run waits for its end.
        return Action("wait")
    if inputs.valve is None:
        run.no_valve_since = run.no_valve_since or inputs.now
        if inputs.now - run.no_valve_since >= MISSING_FOR:
            return _finish(run, inputs, "Ventilstellung fehlt seit 30 min")
    else:
        run.no_valve_since = None
    minutes = 0.0 if run.last_tick is None else min(2.0, (inputs.now - run.last_tick).total_seconds() / 60)
    run.last_tick = inputs.now
    if _heat(inputs):
        run.no_heat_since = None
        run.heated += minutes
        if inputs.valve is not None:
            run.readings.append((inputs.now, inputs.valve, inputs.return_c))
    else:
        run.no_heat_since = run.no_heat_since or inputs.now
        if inputs.now - run.no_heat_since >= MISSING_FOR:
            return _finish(run, inputs, "Kein warmes Wasser seit 30 min")
    if (
        run.moved_at is None
        and inputs.valve is not None
        and run.step_valve is not None
        and abs(inputs.valve - run.step_valve) >= STEADY_VALVE
    ):
        run.moved_at = inputs.now
    if not _settled(run, inputs.now):
        return Action("wait")
    return _next(run, inputs, end)


def _settled(run: TestRun, now: datetime) -> bool:
    if run.heated >= STEP_MAX.total_seconds() / 60:
        return True
    if run.heated < STEP_MIN.total_seconds() / 60:
        return False
    recent = [reading for reading in run.readings if now - reading[0] <= STEADY_FOR]
    if len(recent) < 3 or now - run.readings[0][0] < STEADY_FOR:
        return False
    valves = [reading[1] for reading in recent]
    returns = [reading[2] for reading in recent if reading[2] is not None]
    return max(valves) - min(valves) < STEADY_VALVE and (not returns or max(returns) - min(returns) < STEADY_RETURN_K)


def _next(run: TestRun, inputs: TestInputs, end: time) -> Action:
    assert run.setpoint_c is not None and run.phase is not None and inputs.limits is not None
    rising = run.phase is not Phase.DOWN and not (run.phase is Phase.HOLD and run.position_index == 0)
    run.steps.append(_point(run, inputs, rising=rising))
    match run.phase:
        case Phase.HOLD:
            return _hold_next(run, inputs)
        case Phase.BASELINE if run.down_only:
            # The start at "fully open" (known from before): from here downward.
            return _turn_down(run, inputs, end)
        case Phase.BASELINE:
            run.phase = Phase.UP
            run.phase_size = None
            return _up(run, inputs, end)
        case Phase.UP:
            if _fully_open(run, inputs.valve):
                run.reached_full = True
                run.full_offset_k = run.steps[-1].offset_k
                return _turn_down(run, inputs, end)
            return _up(run, inputs, end)
        case Phase.DOWN:
            return _down(run, inputs, end)
    return Action("wait")


def _hold_next(run: TestRun, inputs: TestInputs) -> Action:
    assert inputs.limits is not None
    valve = inputs.valve
    last = run.position_index == len(run.positions) - 1
    if valve is not None and run.position_index == 0 and valve <= CLOSED:
        run.reached_closed = True
    if valve is not None and (valve >= FULL_OPEN or (last and valve >= PLATEAU_FROM)):
        run.reached_full = True
    run.position_index += 1
    if run.position_index >= len(run.positions):
        return _finish(run, inputs, "Fertig", done=True)
    if TEST_COMMANDS_MAX - run.commands <= 0:
        return _finish(run, inputs, BUDGET_USED)
    if inputs.trv_c is None:
        return Action("wait")
    run.message = _hold_text(run)
    return _set(run, inputs, inputs.limits.round(inputs.trv_c + run.positions[run.position_index]))


def _fully_open(run: TestRun, valve: float | None) -> bool:
    """At least 85 %, or at least 50 % and the last 1 K brought less than 5 points (the Comet stops at 88 %)."""
    if valve is None:
        return False
    if valve >= FULL_OPEN:
        return True
    current = run.steps[-1]
    for earlier in run.steps[:-1]:
        if (
            earlier.rising
            and earlier.valve is not None
            and earlier.valve >= PLATEAU_FROM
            and current.setpoint_c - earlier.setpoint_c >= PLATEAU_K - 1e-6
            and valve - earlier.valve < PLATEAU_GAIN
        ):
            return True
    return False


def _up(run: TestRun, inputs: TestInputs, end: time) -> Action:
    assert run.setpoint_c is not None and inputs.limits is not None
    if TEST_COMMANDS_MAX - run.commands <= 0:
        return _finish(run, inputs, BUDGET_USED)
    target = inputs.limits.round(run.setpoint_c + _step(run, inputs, end, up=True))
    if target <= run.setpoint_c + 1e-6:
        # At the thermostat's upper limit: the valve opens no further by the setpoint.
        return _turn_down(run, inputs, end)
    return _set(run, inputs, target)


def _turn_down(run: TestRun, inputs: TestInputs, end: time) -> Action:
    run.phase = Phase.DOWN
    run.phase_size = None
    return _down(run, inputs, end)


def _down(run: TestRun, inputs: TestInputs, end: time) -> Action:
    assert run.setpoint_c is not None and inputs.limits is not None
    if inputs.valve is not None and inputs.valve <= CLOSED and run.steps and not run.steps[-1].rising:
        run.reached_closed = True
        return _finish(run, inputs, "Fertig", done=True)
    reference = inputs.trv_c if inputs.trv_c is not None else run.setpoint_c
    bottom = inputs.limits.round(reference - DOWN_BELOW_K)
    if run.setpoint_c <= bottom + 1e-6:
        run.reached_bottom = True
        return _finish(run, inputs, "Fertig (2 K unter dem Thermostat, Ventil nicht ganz zu)", done=True)
    # The way back is free of the budget (write gate: RESTORE).
    if TEST_COMMANDS_MAX - run.commands <= 0:
        return _finish(run, inputs, BUDGET_USED)
    target = max(bottom, inputs.limits.round(run.setpoint_c - _step(run, inputs, end, up=False)))
    return _set(run, inputs, target)


def _step(run: TestRun, inputs: TestInputs, end: time, *, up: bool) -> float:
    """The size of the next step (plan-1.4.2-testlauf-lernt.md C)."""
    valve = inputs.valve
    last = run.phase_size
    # How far the last step moved the valve the way it should go (a valve that moved the other way - a value
    # from before that was not current - moved it by nothing).
    moved = None
    if valve is not None and run.step_valve is not None:
        moved = max(0.0, valve - run.step_valve if up else run.step_valve - valve)
    gain = moved / last if moved is not None and last else None
    if valve is None:
        marks: list[float] = []
    elif up:
        marks = [mark for mark in UP_MARKS if mark > valve + MARK_MARGIN]
    else:
        marks = [mark for mark in DOWN_MARKS if mark < valve - MARK_MARGIN]
    needed = max(1, len(marks)) + (len(DOWN_MARKS) if up else 0)
    left = steps_left(run, inputs, end)
    short = left < needed
    target = marks[min(max(0, needed - left), len(marks) - 1)] if marks else (FULL_OPEN if up else CLOSED)
    toward = abs(target - valve) / gain if gain is not None and gain >= WEAK_GAIN and valve is not None else None
    if last is None:
        size = LARGE_STEP_K if short else FIRST_STEP_K
    elif moved is not None and moved > STRONG_CHANGE:
        size = toward if short and toward is not None else SMALL_STEP_K
    elif gain is None or gain < WEAK_GAIN:
        size = LARGE_STEP_K if last < LARGE_STEP_K - 1e-6 else LARGEST_STEP_K
    else:
        assert toward is not None
        size = toward
    size = max(SMALL_STEP_K, min(LARGEST_STEP_K, round(size / SMALL_STEP_K) * SMALL_STEP_K))
    run.phase_size = size
    return size


def steps_left(run: TestRun, inputs: TestInputs, end: time) -> int:
    """How many steps still fit into the night, by the run's own step durations."""
    finish = datetime.combine(inputs.local.date(), end, tzinfo=inputs.local.tzinfo)
    if finish <= inputs.local:
        finish += timedelta(days=1)
    durations = [step.minutes for step in run.steps]
    minutes = max(STEP_MIN.total_seconds() / 60, statistics.median(durations)) if durations else STEP_MINUTES
    return math.floor((finish - inputs.local).total_seconds() / 60 / minutes)


def _set(run: TestRun, inputs: TestInputs, celsius: float) -> Action:
    run.setpoint_c = celsius
    run.step_started = inputs.now
    run.heated = 0.0
    run.last_tick = inputs.now
    run.readings = []
    run.step_valve = inputs.valve
    run.moved_at = None
    if inputs.trv_setpoint_c is not None and abs(inputs.trv_setpoint_c - celsius) < 0.01:
        return Action("wait", note="Sollwert steht schon")
    run.commands += 1
    return Action("send", celsius=celsius)


def _finish(run: TestRun, inputs: TestInputs, reason: str, *, done: bool = False) -> Action:
    run.state = TestState.DONE if done else TestState.ABORTED
    run.message = reason
    run.ended_at = inputs.now
    run.outcome = run.result()
    action = Action("end", celsius=run.before_c, note=reason)
    if reason in _GOES_ON and run.reached_full and not run.reached_closed and run.full_offset_k is not None:
        # The night was too short after the valve was fully open: downward only, the next night.
        run.state = TestState.ARMED
        run.down_only = True
        run.armed_at = inputs.now
        run.nights = 0
        run.counted_night = None
        run.message = f"{GOES_ON} (ab {_signed(run.full_offset_k)} K)."
    return action


def _point(run: TestRun, inputs: TestInputs, *, rising: bool) -> Step:
    assert run.setpoint_c is not None and run.step_started is not None
    offset = round(run.setpoint_c - inputs.trv_c, 2) if inputs.trv_c is not None else None
    moved = (run.moved_at - run.step_started).total_seconds() / 60 if run.moved_at is not None else None
    return Step(
        setpoint_c=run.setpoint_c,
        trv_c=inputs.trv_c,
        room_c=inputs.room_c,
        offset_k=offset,
        valve=inputs.valve,
        rising=rising,
        minutes=round((inputs.now - run.step_started).total_seconds() / 60, 1),
        moved_after_min=round(moved, 1) if moved is not None else None,
        flow_c=inputs.flow_c,
        return_c=inputs.return_c,
        heat_w=heat_output(inputs),
    )


def heat_output(inputs: TestInputs) -> float | None:
    """The radiator's heat output from its rated output and the pipe temperatures (exponent 1.3)."""
    if inputs.rated_w is None or inputs.flow_c is None or inputs.return_c is None or inputs.room_c is None:
        return None
    excess = (inputs.flow_c + inputs.return_c) / 2 - inputs.room_c
    rated = RATED_DELTA_K.get(inputs.rated_basis, 50.0)
    if excess <= 0:
        return 0.0
    return float(round(inputs.rated_w * (excess / rated) ** RADIATOR_EXPONENT))


def _heat(inputs: TestInputs) -> bool:
    return inputs.flow_c is not None and inputs.room_c is not None and inputs.flow_c >= inputs.room_c + HEAT_K


def night_of(local: datetime, start: time) -> date:
    """The night a moment belongs to: the date of its evening."""
    return local.date() if local.time() >= start else (local - timedelta(days=1)).date()


def waiting_for(run: TestRun) -> str | None:
    """What an armed run waited for last (without the closing full stop); None if it has not waited."""
    if run.state is not TestState.ARMED or not run.message.startswith(WAITING):
        return None
    return run.message.removeprefix(WAITING).rstrip(".")


def probe_reading(
    state: str | None, reported: datetime | None, now: datetime, which: str, *, valid_for: timedelta = PROBE_VALID
) -> tuple[float | None, str | None]:
    """A probe's value if it counts, else why not ("Vorlauf" or "Rücklauf" as which)."""
    try:
        value = float(state) if state is not None else None
    except ValueError:
        value = None
    if value is None or not math.isfinite(value):
        return None, f"Der {which}-Fühler ist nicht verfügbar."
    if reported is None or now - reported > valid_for:
        hours = valid_for.total_seconds() / 3600
        return None, f"Der {which}-Fühler meldet seit über {hours:g} h nichts."
    return value, None


def swapped_text(flow_c: float, return_c: float) -> str:
    return (
        f"Der Vorlauf ist kälter als der Rücklauf ({_number(flow_c)} / {_number(return_c)} °C): "
        "Fühler vertauscht oder liegt der Vorlauf-Fühler schlecht an?"
    )


@dataclass
class SwapWatch:
    """Whether flow and return of the probe pair look swapped (see SWAP_K)."""

    swapped: bool = False
    # Since when the readings speak for swapped, or against it; None without evidence.
    for_since: datetime | None = None
    against_since: datetime | None = None

    def update(
        self, now: datetime, flow_c: float | None, return_c: float | None, valve: float | None, room_c: float | None
    ) -> bool:
        """Takes the readings of a minute; True when swapped changed."""
        warm = (
            flow_c is not None and return_c is not None and (room_c is None or max(flow_c, return_c) >= room_c + HEAT_K)
        )
        if flow_c is None or return_c is None or valve is None or valve < SWAP_VALVE or not warm:
            # No evidence either way: a closed valve or cold pipes say nothing about the pair.
            self.for_since = self.against_since = None
            return False
        if return_c >= flow_c + SWAP_K:
            self.against_since = None
            self.for_since = self.for_since or now
            if not self.swapped and now - self.for_since >= SWAP_FOR:
                self.swapped = True
                return True
            return False
        self.for_since = None
        self.against_since = self.against_since or now
        if self.swapped and now - self.against_since >= SWAP_FOR:
            self.swapped = False
            return True
        return False


def _number(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def run_to_json(run: TestRun) -> dict[str, Any]:
    return {
        "room_id": run.room_id,
        "trv": run.trv,
        "armed_at": run.armed_at.isoformat(),
        "state": run.state.value,
        "nights": run.nights,
        "counted_night": run.counted_night.isoformat() if run.counted_night else None,
        "message": run.message,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "before_c": run.before_c,
        "down_only": run.down_only,
        "full_offset_k": run.full_offset_k,
    }


def run_from_json(raw: Any) -> TestRun | None:
    if not isinstance(raw, dict):
        return None
    try:
        full = raw.get("full_offset_k")
        run = TestRun(
            room_id=str(raw["room_id"]),
            trv=str(raw["trv"]),
            armed_at=datetime.fromisoformat(raw["armed_at"]),
            state=TestState(raw["state"]),
            nights=int(raw.get("nights", 0)),
            counted_night=date.fromisoformat(raw["counted_night"]) if raw.get("counted_night") else None,
            message=str(raw.get("message", "")),
            started_at=datetime.fromisoformat(raw["started_at"]) if raw.get("started_at") else None,
            before_c=float(raw["before_c"]) if isinstance(raw.get("before_c"), int | float) else None,
            down_only=bool(raw.get("down_only", False)),
            full_offset_k=float(full) if isinstance(full, int | float) and not isinstance(full, bool) else None,
        )
    except (KeyError, TypeError, ValueError):
        return None
    return run


def describe(steps: Sequence[Step]) -> str:
    """Short German summary, e.g. for the protocol."""
    full = next((step.offset_k for step in steps if (step.valve or 0) >= FULL_OPEN), None)
    opened = next((step.offset_k for step in steps if step.rising and (step.valve or 0) > CLOSED), None)
    closed = next((step.offset_k for step in steps if not step.rising and (step.valve or 100) <= CLOSED), None)
    parts = [f"{len(steps)} Stufen"]
    if opened is not None:
        parts.append(f"offen ab {_signed(opened)} K")
    if full is not None:
        parts.append(f"ganz offen ab {_signed(full)} K")
    if closed is not None:
        parts.append(f"zu ab {_signed(closed)} K")
    sizes = [abs(later.setpoint_c - earlier.setpoint_c) for earlier, later in itertools.pairwise(steps)]
    if sizes:
        parts.append(f"Schritte {_number(min(sizes))} bis {_number(max(sizes))} K")
    return ", ".join(parts)


def _signed(value: float) -> str:
    return f"{value:+.1f}".replace(".", ",").replace("-", MINUS)
