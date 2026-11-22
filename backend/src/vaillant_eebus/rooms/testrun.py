"""The test run of one thermostat (punkt1-trv-regelung.md 3b, decided with the user 21.11.2026).

Armed by hand for the radiator the probe pair (flow and return pipe sensors)
is fixed to; it runs at night: it starts from 22:00 as soon as the room is
between 0.5 K below and 0.2 K above its setpoint, no window is open and warm
water reaches the radiator, at the latest at 1:00 - else it tries the next
night, three nights at most. Then:

1. setpoint = temperature at the thermostat (starting point);
2. up in steps of 0.5 K (1 K while the valve has not moved yet) until the valve
   is at least 90 % open, at most to the thermostat's limit;
3. down in steps of 0.5 K until 1 K below the temperature at the thermostat;
4. back to the setpoint from before the run, at the latest at 5:00.

A step ends when valve and return temperature stood still for 10 minutes,
after 20 to 40 minutes with warm water. It stops at once - back to the setpoint
from before - when the room gets 1 K warmer than its setpoint, a window opens,
the valve position or the warm water is missing for 30 minutes. At most 16
commands a night; the way back is not counted (write gate: RESTORE). Complete
is a run that opened the valve fully and closed it again. A night counts as a
try only if its start window began after the run was armed.

A reading of the probe pair counts up to two hours after its last report
(1.4.1): many sensors report only when the value changes, so a quiet pipe is
silent for an hour. A pair whose return is warmer than its flow while warm
water reaches the open valve looks swapped; no run starts then
(fehlerliste-abnahme-1.4.0.md, finding 1).

advance() is a pure step function: the room control feeds it every minute and
sends what it asks for through the write gate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from vaillant_eebus.hems.config import in_window
from vaillant_eebus.rooms.curve import FULL_OPEN, OPEN_FROM, Step, TestResult
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
STEP_K = 0.5
JUMP_K = 1.0
DOWN_BELOW_K = 1.0
# Warm water at the radiator: its flow at least this much above the room.
HEAT_K = 5.0
UP_COMMANDS_MAX = 8
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
    BASELINE = "baseline"
    UP = "up"
    DOWN = "down"


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


@dataclass(frozen=True, slots=True)
class Action:
    # "send": set the thermostat to celsius; "end": the run ended (back to celsius if given); "wait".
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
    step_valve: float | None = None
    moved_at: datetime | None = None
    readings: list[tuple[datetime, float, float | None]] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    reached_full: bool = False
    # The way down reached 1 K below the temperature at the thermostat.
    reached_bottom: bool = False

    @property
    def active(self) -> bool:
        return self.state in (TestState.ARMED, TestState.RUNNING)

    def result(self) -> TestResult | None:
        if self.started_at is None or self.ended_at is None:
            return None
        # Complete: up to fully open and down to closed (a run cut off at the end of the night or by its
        # budget before the valve closed is not).
        closed = any(not step.rising and step.valve is not None and step.valve <= OPEN_FROM for step in self.steps)
        complete = self.state is TestState.DONE and self.reached_full and (self.reached_bottom or closed)
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
        assert inputs.limits is not None and inputs.trv_c is not None
        run.state = TestState.RUNNING
        run.started_at = inputs.now
        run.phase = Phase.BASELINE
        run.before_c = inputs.trv_setpoint_c
        run.message = "Ausgangslage"
        return _set(run, inputs, inputs.limits.round(inputs.trv_c))
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


def _running(run: TestRun, inputs: TestInputs, start: time, end: time) -> Action:
    if not in_window(inputs.local.time(), start, end):
        return _finish(run, inputs.now, "Ende des Nachtfensters")
    if not inputs.may_write:
        return _finish(run, inputs.now, "Die App darf das Thermostat nicht mehr stellen")
    if inputs.room_c is not None and inputs.room_c > inputs.setpoint_c + ROOM_OVER_K:
        return _finish(run, inputs.now, "Raum über Soll + 1 K")
    if inputs.window_open:
        return _finish(run, inputs.now, "Fenster offen")
    if inputs.valve is None:
        run.no_valve_since = run.no_valve_since or inputs.now
        if inputs.now - run.no_valve_since >= MISSING_FOR:
            return _finish(run, inputs.now, "Ventilstellung fehlt seit 30 min")
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
            return _finish(run, inputs.now, "Kein warmes Wasser seit 30 min")
    if (
        run.moved_at is None
        and inputs.valve is not None
        and run.step_valve is not None
        and abs(inputs.valve - run.step_valve) >= STEADY_VALVE
    ):
        run.moved_at = inputs.now
    if not _settled(run, inputs.now):
        return Action("wait")
    return _next(run, inputs)


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


def _next(run: TestRun, inputs: TestInputs) -> Action:
    assert run.setpoint_c is not None and run.phase is not None and inputs.limits is not None
    limits = inputs.limits
    valve = inputs.valve
    rising = run.phase is not Phase.DOWN
    run.steps.append(_point(run, inputs, rising=rising))
    if valve is not None and valve >= FULL_OPEN:
        run.reached_full = True
    match run.phase:
        case Phase.BASELINE:
            run.phase = Phase.UP
            jump = JUMP_K if valve is None or valve <= OPEN_FROM else STEP_K
            return _set(run, inputs, limits.round(run.setpoint_c + jump))
        case Phase.UP:
            up_commands = sum(1 for step in run.steps if step.rising)
            if run.reached_full or run.setpoint_c >= limits.max_c - 1e-6 or up_commands > UP_COMMANDS_MAX:
                run.phase = Phase.DOWN
                return _down(run, inputs)
            moved = any(step.valve is not None and step.valve > OPEN_FROM for step in run.steps)
            return _set(run, inputs, limits.round(run.setpoint_c + (STEP_K if moved else JUMP_K)))
        case Phase.DOWN:
            return _down(run, inputs)
    return Action("wait")


def _down(run: TestRun, inputs: TestInputs) -> Action:
    assert run.setpoint_c is not None and inputs.limits is not None
    reference = inputs.trv_c if inputs.trv_c is not None else run.setpoint_c
    bottom = inputs.limits.round(reference - DOWN_BELOW_K)
    if run.setpoint_c <= bottom + 1e-6:
        run.reached_bottom = True
        return _finish(run, inputs.now, "Fertig", done=True)
    # The way back is free of the budget (write gate: RESTORE).
    left = TEST_COMMANDS_MAX - run.commands
    if left <= 0:
        return _finish(run, inputs.now, "Befehle des Testlaufs verbraucht", done=True)
    needed = math.ceil((run.setpoint_c - bottom) / STEP_K - 1e-6)
    step = STEP_K if needed <= left else JUMP_K
    return _set(run, inputs, max(bottom, inputs.limits.round(run.setpoint_c - step)))


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


def _finish(run: TestRun, now: datetime, reason: str, *, done: bool = False) -> Action:
    run.state = TestState.DONE if done else TestState.ABORTED
    run.message = reason
    run.ended_at = now
    return Action("end", celsius=run.before_c, note=reason)


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
    }


def run_from_json(raw: Any) -> TestRun | None:
    if not isinstance(raw, dict):
        return None
    try:
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
        )
    except (KeyError, TypeError, ValueError):
        return None
    return run


def describe(steps: Sequence[Step]) -> str:
    """Short German summary, e.g. for the protocol."""
    full = next((step.offset_k for step in steps if step.rising and (step.valve or 0) >= FULL_OPEN), None)
    opened = next((step.offset_k for step in steps if step.rising and (step.valve or 0) > OPEN_FROM), None)
    parts = [f"{len(steps)} Stufen"]
    if opened is not None:
        parts.append(f"öffnet ab {_signed(opened)} K")
    if full is not None:
        parts.append(f"ganz offen ab {_signed(full)} K")
    return ", ".join(parts)


def _signed(value: float) -> str:
    return f"{value:+.1f}".replace(".", ",").replace("-", MINUS)
