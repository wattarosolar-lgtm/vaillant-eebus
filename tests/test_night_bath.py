"""The night of 22/23 November 2026 in the bathroom (fehlerliste-abnahme-1.4.0.md, finding 2), played again.

As it was (1.4.1): 0.5 K steps, 12 steps from 23:44 to 5:00, the Comet at most 88 % open, never closed: the
room could not go to "Steuern". The real reports come from Home Assistant's history (times in UTC as it gave
them; local time is UTC + 1 h): the Comet's setpoint, its temperature and its valve (pi_heating_demand), and the
probe pair on the radiator's pipes. The steps are the ones the app stored that night.

With 1.4.2 the same night: the probes and the Comet's temperature as they were, the Comet's valve from a model
made of what it did that night (it answers a new setpoint within a minute; rising as measured, falling as
measured down to +1 K, below that assumed to close at -1 K). The run steps by the valve and is through before 5.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from vaillant_eebus.rooms.curve import Step
from vaillant_eebus.rooms.learning import StepWatch, ValveMap, points_from_steps
from vaillant_eebus.rooms.testrun import (
    PROBE_VALID,
    TestInputs,
    TestRun,
    TestState,
    advance,
    probe_reading,
)
from vaillant_eebus.write_gate import TEST_COMMANDS_MAX, ThermostatLimits

COMET = ThermostatLimits(8.0, 28.0, 0.5)
LOCAL = timedelta(hours=1)

# Flow ("Temp Sonde 2", sensor.0xa4c138061c82ffff_temperature) and return ("Temp Sonde 1"), UTC.
FLOW = """21:57:43 22.5|22:09:25 22.3|22:23:11 22.1|22:26:17 22.3|22:27:02 22.5|22:27:32 22.7|22:28:02 22.9|
22:28:37 23.1|22:29:22 23.3|22:31:42 23.5|22:36:53 23.3|22:40:23 23.1|22:41:04 23.3|22:41:19 23.6|22:41:29 23.8|
22:41:34 24|22:41:44 24.3|22:41:49 24.5|22:41:59 24.8|22:42:04 25|22:42:14 25.3|22:42:19 25.5|22:42:29 25.8|
22:42:39 26.1|22:42:49 26.3|22:42:59 26.6|22:43:09 26.8|22:43:19 27.1|22:43:29 27.3|22:43:39 27.5|22:43:49 27.7|
22:43:59 27.9|22:44:09 28.1|22:44:24 28.3|22:44:39 28.5|22:44:54 28.7|22:45:09 28.9|22:45:24 29.1|22:45:44 29.3|
22:46:14 29.5|22:46:39 29.7|22:47:09 29.9|22:47:49 30.1|22:48:44 30.3|22:50:54 30.5|22:56:20 30.7|22:58:15 30.9|
22:59:40 31.1|23:02:01 31.3|23:13:57 31.5|23:34:35 31.7|23:41:40 31.5|23:42:31 31.3|23:43:16 31.1|23:44:06 30.9|
23:45:16 30.7|23:46:01 30.5|23:46:56 30.3|23:47:46 30.1|23:48:41 29.9|23:49:56 29.7|23:50:56 29.5|23:51:52 29.3|
23:52:57 29.1|23:53:57 28.9|23:55:22 28.7|23:56:27 28.5|23:57:32 28.3|23:58:42 28.1|23:59:52 27.9|00:01:28 27.7|
00:02:38 27.5|00:03:48 27.3|00:05:08 27.1|00:06:33 26.9|00:08:23 26.7|00:09:54 26.5|00:11:24 26.3|00:13:04 26.1|
00:14:39 25.9|00:16:44 25.7|00:17:14 25.9|00:17:29 26.1|00:17:39 26.3|00:17:54 26.6|00:18:04 26.8|00:18:14 27.1|
00:18:24 27.4|00:18:34 27.6|00:18:39 27.8|00:18:49 28.1|00:18:59 28.3|00:19:09 28.6|00:19:19 28.9|00:19:30 29.1|
00:19:40 29.3|00:19:50 29.5|00:20:00 29.8|00:20:15 30|00:20:25 30.2|00:20:40 30.4|00:20:55 30.6|00:21:10 30.8|
00:21:35 31|00:22:00 31.2|00:22:35 31.4|00:23:15 31.6|00:24:50 31.8|00:28:15 31.6|00:29:36 31.4|00:30:16 31.2|
00:30:36 31|00:30:56 30.8|00:31:11 30.6|00:31:31 30.4|00:31:51 30.2|00:32:06 30|00:32:21 29.8|00:32:27 29.6|
00:32:32 29.3|00:32:37 29.1|00:32:46 28.9|00:32:52 28.7|00:32:57 28.5|00:33:11 28.8|00:33:17 29.2|00:33:22 29.4|
00:33:46 29.6|00:33:52 29.8|00:35:06 30|00:35:41 30.2|00:36:16 30.4|00:36:46 30.6|00:37:16 30.8|00:38:22 31|
00:39:07 31.2|00:39:57 31.4|00:41:02 31.6|00:42:07 31.8|00:43:27 32|00:44:22 32.2|00:45:32 32.4|00:46:52 32.6|
00:54:33 32.4|00:57:54 32.2|01:01:19 32|01:31:08 31.8|01:42:34 32|02:13:08 32.2|02:15:08 32.4|02:16:58 32.6|
02:25:04 32.4|02:29:15 32.2|02:36:11 32|02:49:02 32.2|02:58:28 32.4|03:01:04 32.6|03:10:15 32.4|03:11:55 32.2|
03:13:55 32|03:16:10 31.8|03:18:01 31.6|03:22:21 31.4|03:30:27 31.2|03:30:52 31|03:31:17 30.8|03:31:32 30.6|
03:31:47 30.4|03:32:12 30.2|03:32:27 30|03:32:47 29.8|03:33:07 29.6|03:33:22 29.4|03:33:52 29.2|03:34:07 29|
03:34:28 28.8|03:34:48 28.6|03:35:03 28.4|03:35:23 28.2|03:35:38 28|03:35:53 27.8|03:36:08 27.6|03:36:28 27.4|
03:36:48 27.2|03:37:03 27|03:37:18 26.8|03:37:33 26.6|03:37:48 26.4|03:38:03 26.2|03:38:18 26|03:38:33 25.8|
03:38:43 25.6|03:38:58 25.4|03:39:18 25.2|03:40:13 25.4|03:40:33 25.6|03:40:48 25.8|03:41:08 26|03:41:23 26.2|
03:41:38 26.4|03:41:53 26.6|03:42:08 26.8|03:42:33 27|03:42:48 27.2|03:43:03 27.4|03:43:18 27.6|03:43:33 27.8|
03:43:58 28|03:44:18 28.2|03:44:33 28.4|03:44:53 28.6|03:45:13 28.8|03:45:44 29|03:46:04 29.2|03:46:29 29.4|
03:47:04 29.6|03:47:29 29.8|03:48:09 30|03:48:44 30.2|03:49:19 30.4|03:49:54 30.6|03:50:34 30.8|03:51:39 31|
03:52:39 31.2|03:53:39 31.4|03:54:55 31.6|03:56:00 31.8|03:57:15 32|03:58:00 32.2|03:58:45 32.4|03:59:40 32.6|
04:00:55 32.8"""
RETURN = """21:48:08 22.1|22:06:00 21.9|22:30:38 21.7|22:44:20 21.5|22:58:57 21.7|23:03:02 21.9|23:04:57 22.1|
23:06:12 22.3|23:07:43 22.5|23:08:48 22.7|23:09:48 22.9|23:10:48 23.1|23:11:43 23.3|23:13:03 23.5|23:14:03 23.7|
23:15:03 23.9|23:16:09 24.1|23:17:14 24.3|23:18:49 24.5|23:20:19 24.7|23:21:54 24.9|23:24:04 25.1|23:26:25 25.3|
23:32:25 25.5|23:45:52 25.3|23:48:07 25.1|23:50:28 24.9|23:55:28 24.5|23:57:38 24.3|00:00:09 24.1|00:02:39 23.9|
00:06:34 23.7|00:09:15 23.5|00:12:20 23.3|00:16:05 23.1|00:17:36 22.9|00:20:51 22.7|00:27:07 22.9|00:32:12 23.1|
00:34:22 23.3|00:34:32 23.6|00:34:37 23.8|00:34:42 24.6|00:34:47 25.8|00:34:52 26.4|00:34:57 26.6|00:35:02 26.9|
00:35:07 27.1|00:35:17 27.3|00:35:22 27.6|00:35:27 27.9|00:35:33 28.1|00:35:53 28.3|00:36:48 28.5|01:44:57 29.8|
02:19:14 30|02:20:25 30.2|02:21:35 30.4|02:23:10 30.6|02:31:06 30.4|02:41:37 30.2|02:53:34 30.4|03:03:40 30.6|
03:09:00 30.8|03:13:31 30.6|03:16:11 30.4|03:19:42 30.2|03:22:37 30|03:26:52 29.8|03:31:48 29.6|03:32:43 29.4|
03:33:48 29.2|03:34:28 29|03:34:58 28.8|03:35:28 28.6|03:35:54 28.4|03:36:19 28.2|03:36:39 28|03:36:59 27.8|
03:37:14 27.6|03:37:34 27.4|03:37:54 27.2|03:38:09 27|03:38:24 26.8|03:38:39 26.6|03:38:54 26.4|03:39:14 26.2|
03:39:29 26|03:39:44 25.8|03:40:49 26|03:41:14 26.2|03:41:44 26.4|03:42:29 26.6|03:43:49 26.8|03:45:40 27|
03:46:55 27.2|03:47:55 27.4|03:48:45 27.6|03:49:30 27.8|03:50:20 28|03:51:00 28.2|03:51:40 28.4|03:52:20 28.6|
03:53:10 28.8|03:54:16 29|03:55:16 29.2|03:56:16 29.4|03:57:16 29.6|03:58:31 29.8|04:00:11 30"""
# The Comet: its setpoint and temperature (climate entity), and its valve; UTC.
SETPOINT = """20:30:00 23|23:24:30 23.5|23:44:31 24|00:23:28 24.5|00:45:32 25|01:05:29 25.5|01:25:29 26|
01:54:29 26.5|02:14:32 27|02:35:33 26|02:55:32 25|03:15:33 24|03:45:31 23.5|04:00:32 23"""
TEMPERATURE = "20:30:00 22.5|21:55:32 23|00:23:29 22.5|02:14:32 22|02:35:33 22.5"
VALVE = """20:30:00 50|21:55:32 38|23:24:31 16|23:44:46 28|00:23:44 45|00:45:48 56|01:05:45 69|01:25:44 82|
02:26:27 88|02:55:47 76|03:15:49 64|04:16:37 56"""
# The steps the app stored that night (api/rooms, curve.steps): offset to the thermostat and valve.
STORED = [
    (23.0, 23.0, 0.0, 38.0, True, 40.0, 31.5, 25.1),
    (23.5, 23.0, 0.5, 16.0, True, 20.0, 30.9, 25.5),
    (24.0, 23.0, 1.0, 28.0, True, 39.0, 31.6, 22.7),
    (24.5, 22.5, 2.0, 45.0, True, 22.0, 32.2, 28.5),
    (25.0, 22.5, 2.5, 56.0, True, 20.0, 32.0, 28.5),
    (25.5, 22.5, 3.0, 69.0, True, 20.0, 32.0, 28.5),
    (26.0, 22.5, 3.5, 82.0, True, 29.0, 32.0, 29.8),
    (26.5, 22.5, 4.0, 82.0, True, 20.0, 32.2, 29.8),
    (27.0, 22.0, 5.0, 88.0, True, 21.0, 32.2, 30.4),
    (26.0, 22.5, 3.5, 88.0, False, 20.0, 32.2, 30.4),
    (25.0, 22.5, 2.5, 76.0, False, 20.0, 32.0, 30.6),
    (24.0, 22.5, 1.5, 64.0, False, 30.0, 28.8, 26.8),
]
# The Comet's valve by offset: rising as it reported a minute after each step; falling as reported down to +1 K
# (04:45 local: 56 %), below that assumed to close at -1 K.
RISING = [(-0.5, 0.0), (0.0, 5.0), (0.5, 16.0), (1.0, 28.0), (2.0, 45.0), (2.5, 56.0), (3.0, 69.0), (3.5, 82.0),
          (4.0, 82.0), (5.0, 88.0), (6.0, 89.0)]  # fmt: skip
FALLING = [(-1.0, 0.0), (-0.5, 12.0), (0.0, 25.0), (0.5, 40.0), (1.0, 56.0), (1.5, 64.0), (2.5, 76.0), (3.5, 88.0),
           (6.0, 89.0)]  # fmt: skip


def series(text: str) -> list[tuple[datetime, float]]:
    """Reports in UTC from the evening of 22 November to the morning of 23 November."""
    items = []
    for item in text.replace("\n", "").split("|"):
        clock, value = item.split()
        hour, minute, second = (int(part) for part in clock.split(":"))
        day = 22 if hour >= 12 else 23
        items.append((datetime(2026, 11, day, hour, minute, second, tzinfo=UTC), float(value)))
    return items


def last(items: Sequence[tuple[datetime, float]], moment: datetime) -> tuple[datetime, float] | None:
    seen = [item for item in items if item[0] <= moment]
    return seen[-1] if seen else None


def between(table: Sequence[tuple[float, float]], offset: float) -> float:
    if offset <= table[0][0]:
        return table[0][1]
    for (low, low_valve), (high, high_valve) in itertools.pairwise(table):
        if offset <= high:
            return low_valve + (high_valve - low_valve) * (offset - low) / (high - low)
    return table[-1][1]


@dataclass
class Comet:
    """The bathroom's thermostat as it behaved that night: a new setpoint moves the valve within a minute."""

    setpoint: float = 23.0
    valve: float = 38.0
    rising: bool = True
    due: tuple[datetime, float] | None = None

    def command(self, moment: datetime, celsius: float, trv_c: float) -> None:
        self.rising = celsius > self.setpoint
        self.setpoint = celsius
        table = RISING if self.rising else FALLING
        self.due = (moment + timedelta(minutes=1), round(between(table, celsius - trv_c)))

    def at(self, moment: datetime) -> float:
        if self.due is not None and moment >= self.due[0]:
            self.valve, self.due = self.due[1], None
        return self.valve


def test_the_stored_night_knows_fully_open_but_not_closed() -> None:
    end = datetime(2026, 11, 23, 4, 0, tzinfo=UTC)
    steps = [
        Step(setpoint, trv, trv, offset, valve, rising, minutes, None, flow, back)
        for setpoint, trv, offset, valve, rising, minutes, flow, back in STORED
    ]
    valve_map = ValveMap().add_points(points_from_steps(steps, end))

    # 88 % counts as fully open since 1.4.2 (at least 85 %); the valve never closed.
    assert valve_map.full(end, rising=True) == (5.0, 88.0)
    assert valve_map.closed_k(end) is None
    assert valve_map.missing(end) == ["„zu“"]


def test_the_real_night_shows_the_comets_answer_to_a_step() -> None:
    setpoints, valves = series(SETPOINT), series(VALVE)
    watch = StepWatch()
    valve_map = ValveMap()
    moment = datetime(2026, 11, 22, 21, 0, tzinfo=UTC)
    while moment <= datetime(2026, 11, 23, 4, 30, tzinfo=UTC):
        setpoint, valve = last(setpoints, moment), last(valves, moment)
        assert setpoint is not None and valve is not None
        # The Comet reports its valve with its messages, at least hourly: such a value counts for 70 minutes.
        fresh = moment - valve[0] <= timedelta(minutes=70)
        response = watch.update(moment, setpoint=setpoint[1], valve=valve[1], fresh=fresh, own=True)
        if response is not None:
            valve_map = valve_map.add_response(response)
        moment += timedelta(minutes=1)

    # It answers within the minute (seen in the same minute as the new setpoint); 0.5 K moves it.
    reaction = valve_map.reaction_min()
    assert reaction is not None and reaction <= 1.0
    assert valve_map.smallest_step_k() == 0.5
    assert len(valve_map.responses) >= 8


def replay_with_1_4_2(run: TestRun, start: datetime, end: datetime) -> tuple[list[float], Comet]:
    """Every minute, as the room control does it; the Comet as modelled, everything else as it was."""
    flow, back, temperature = series(FLOW), series(RETURN), series(TEMPERATURE)
    comet = Comet()
    sent = []
    moment = start
    while moment <= end and run.active:
        readings = []
        for items, which in ((flow, "Vorlauf"), (back, "Rücklauf")):
            report = last(items, moment)
            value, reported = (None, None) if report is None else (str(report[1]), report[0])
            readings.append(probe_reading(value, reported, moment, which, valid_for=PROBE_VALID))
        (flow_c, flow_problem), (back_c, back_problem) = readings
        trv = last(temperature, moment)
        trv_c = trv[1] if trv is not None else None
        inputs = TestInputs(
            now=moment,
            local=moment + LOCAL,
            setpoint_c=23.0,
            room_c=trv_c,
            trv_c=trv_c,
            trv_setpoint_c=comet.setpoint,
            valve=comet.at(moment),
            flow_c=flow_c,
            return_c=back_c,
            window_open=False,
            limits=COMET,
            probe_problem=flow_problem or back_problem,
            may_write=True,
            rated_w=537.0,
        )
        action = advance(run, inputs, time(22, 0), time(1, 0), time(5, 0))
        if action.kind == "send" and action.celsius is not None and trv_c is not None:
            comet.command(moment, action.celsius, trv_c)
            sent.append(action.celsius)
        moment += timedelta(minutes=1)
    return sent, comet


def test_the_same_night_with_steps_by_the_valve_is_through_before_five() -> None:
    run = TestRun(room_id="sensor.bad", trv="climate.bad", armed_at=datetime(2026, 11, 22, 21, 23, tzinfo=UTC))

    sent, comet = replay_with_1_4_2(
        run, datetime(2026, 11, 22, 21, 23, tzinfo=UTC), datetime(2026, 11, 23, 4, 30, tzinfo=UTC)
    )

    # It starts as it did (warm water at 23:44 local, seen in the next minute) and is through before 4 o'clock.
    assert run.started_at == datetime(2026, 11, 22, 22, 45, tzinfo=UTC)
    assert run.state is TestState.DONE and run.message == "Fertig", run.message
    result = run.outcome
    assert result is not None and result.complete
    assert run.ended_at is not None and run.ended_at + LOCAL < datetime(2026, 11, 23, 4, 0, tzinfo=UTC)
    assert run.reached_full and run.reached_closed and comet.valve <= 5.0
    # Fewer and larger steps than the 12 of 0.5 K: the first is 1 K.
    sizes = [abs(later - earlier) for earlier, later in itertools.pairwise([23.0, *sent])]
    assert sizes[0] == 1.0 and max(sizes) >= 1.0 and len(sent) <= 9 < TEST_COMMANDS_MAX
    valve_map = ValveMap().add_points(points_from_steps(result.steps, run.ended_at))
    assert valve_map.ready(run.ended_at)


def test_a_late_start_the_same_night_plans_larger_steps_and_is_through_too() -> None:
    # As if the warm water had come at 0:50 local: about four hours for the run.
    run = TestRun(room_id="sensor.bad", trv="climate.bad", armed_at=datetime(2026, 11, 22, 21, 23, tzinfo=UTC))

    sent, _comet = replay_with_1_4_2(
        run, datetime(2026, 11, 22, 23, 50, tzinfo=UTC), datetime(2026, 11, 23, 4, 30, tzinfo=UTC)
    )

    assert run.state is TestState.DONE and run.outcome is not None and run.outcome.complete
    sizes = [abs(later - earlier) for earlier, later in itertools.pairwise([23.0, *sent])]
    # Too little time for 1 K first: 2 K, then 3 K while the valve hardly moves.
    assert sizes[:2] == [2.0, 3.0] and len(sent) <= 8
