"""The probe pair of the test run (1.4.1, fehlerliste-abnahme-1.4.0.md finding 1): how long a reading counts,
swapped sensors, and the night of 21/22 November 2026 played again with the bathroom's real reports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta

from vaillant_eebus.rooms.testrun import (
    PROBE_VALID,
    SwapWatch,
    TestInputs,
    TestRun,
    TestState,
    advance,
    probe_reading,
    swapped_text,
)
from vaillant_eebus.write_gate import ThermostatLimits

NOW = datetime(2026, 11, 23, 23, 0, tzinfo=UTC)
COMET_LIMITS = ThermostatLimits(8.0, 28.0, 0.5)


def test_a_reading_counts_for_two_hours_after_its_report() -> None:
    assert probe_reading("30.5", NOW - timedelta(minutes=90), NOW, "Vorlauf") == (30.5, None)
    assert probe_reading("30.5", NOW - timedelta(minutes=130), NOW, "Rücklauf") == (
        None,
        "Der Rücklauf-Fühler meldet seit über 2 h nichts.",
    )
    assert probe_reading("unavailable", NOW, NOW, "Vorlauf") == (None, "Der Vorlauf-Fühler ist nicht verfügbar.")
    assert probe_reading(None, None, NOW, "Vorlauf") == (None, "Der Vorlauf-Fühler ist nicht verfügbar.")
    # The rule of 1.4.0 for comparison.
    old_rule = timedelta(minutes=15)
    assert probe_reading("30.5", NOW - timedelta(minutes=16), NOW, "Vorlauf", valid_for=old_rule)[0] is None


def test_swapped_sensors_are_seen_after_a_quarter_of_an_hour_with_warm_water() -> None:
    watch = SwapWatch()
    changes = [watch.update(NOW + timedelta(minutes=minute), 26.4, 30.3, 50.0, 22.5) for minute in range(16)]

    assert watch.swapped and changes.count(True) == 1 and changes[15]
    # A closed valve says nothing: the look stays.
    assert not watch.update(NOW + timedelta(minutes=20), 26.4, 30.3, 10.0, 22.5)
    assert watch.swapped
    # The right way round for a quarter of an hour, and it goes away.
    back = [watch.update(NOW + timedelta(minutes=30 + minute), 30.3, 26.4, 50.0, 22.5) for minute in range(16)]
    assert not watch.swapped and back.count(True) == 1


def test_cold_pipes_or_a_short_moment_do_not_count_as_swapped() -> None:
    watch = SwapWatch()
    for minute in range(30):
        # No warm water at the radiator (both pipes less than 5 K above the room).
        watch.update(NOW + timedelta(minutes=minute), 24.0, 26.0, 50.0, 22.5)
    assert not watch.swapped

    for minute in range(10):
        watch.update(NOW + timedelta(minutes=40 + minute), 26.4, 30.3, 50.0, 22.5)
    watch.update(NOW + timedelta(minutes=50), 30.3, 26.4, 50.0, 22.5)
    for minute in range(10):
        watch.update(NOW + timedelta(minutes=51 + minute), 26.4, 30.3, 50.0, 22.5)
    assert not watch.swapped


# --- the night of 21/22 November 2026 in the bathroom (local times, from Home Assistant's history) -----------

# "Temp Sonde 1", chosen as flow; it reports only on a change of 0.2 K. (21:30 is the state at the start of the
# history read, not a report.)
SONDE_1 = """21:30 26.0|21:44 26.2|21:44 26.4|21:44 26.6|21:45 26.8|21:45 27.0|21:45 27.2|21:46 27.4|21:46 27.6|
21:47 27.8|21:47 28.0|21:47 28.2|21:48 28.4|21:48 28.6|21:49 28.8|21:49 29.0|21:50 29.2|21:51 29.4|21:53 29.2|
21:53 29.0|21:54 28.8|21:54 28.6|21:55 28.4|21:56 28.2|21:58 28.0|21:59 27.8|22:02 27.4|22:05 27.2|22:07 27.0|
22:10 26.8|22:14 26.6|22:21 26.4|22:42 26.2|23:31 26.4|00:59 26.6|01:03 26.4|01:03 26.2|01:04 26.0|01:04 25.8|
01:05 25.6|01:06 25.4|01:08 25.6|01:10 25.4|01:14 25.2|01:25 25.4|01:30 25.6|01:33 25.8|01:36 26.0|01:40 26.2|
01:43 26.4"""
# "Temp Sonde 2", chosen as return; it drops first and fast when no warm water comes: the flow pipe.
SONDE_2 = """21:30 29.5|21:45 29.7|21:45 29.9|21:47 30.1|21:50 30.3|22:01 30.1|22:20 29.9|23:04 30.1|23:08 30.3|
23:38 30.5|00:38 30.4|00:53 30.2|00:54 30.0|00:54 29.8|00:55 29.6|00:55 29.4|00:56 29.2|00:56 29.0|00:57 28.8|
00:57 28.6|00:57 28.4|00:58 28.1|00:58 27.9|00:58 27.7|00:59 27.5|00:59 27.3|00:59 27.1|01:00 26.9|01:00 26.5|
01:01 26.3|01:01 26.1|01:01 25.9|01:02 25.7|01:02 25.5|01:02 25.3|01:03 25.1|01:03 24.9|01:04 24.7|01:05 24.9|
01:05 25.1|01:06 25.3|01:06 25.5|01:06 25.7|01:07 25.9|01:07 26.1|01:07 26.3|01:08 26.5|01:08 26.7|01:08 26.9|
01:09 27.1|01:09 27.3|01:10 27.5|01:10 27.7|01:10 27.9|01:11 28.1|01:11 28.3|01:12 28.5|01:13 28.7|01:13 28.9|
01:14 29.1|01:15 29.3|01:16 29.5|01:17 29.7|01:18 29.9|01:20 30.1|01:22 30.3|01:24 30.5|01:25 30.7|01:28 30.9|
01:34 30.7|01:38 30.5"""
# The Comet's own temperature: the bathroom's room temperature (steps of 0.5 K).
COMET = "21:30 20.0|22:19 20.5|22:34 21.0|22:46 21.5|23:04 22.0|23:41 22.5"
# Its valve (pi_heating_demand, %).
VALVE = "21:30 100|22:19 89|22:20 45|22:21 60|22:21 89|22:29 58|23:29 50|00:30 51"
ARMED = datetime(2026, 11, 21, 22, 23, tzinfo=UTC)


def reports(text: str) -> list[tuple[datetime, float]]:
    items = []
    for item in text.replace("\n", "").split("|"):
        clock, value = item.split()
        hour, minute = (int(part) for part in clock.split(":"))
        day = 21 if hour >= 12 else 22
        items.append((datetime(2026, 11, day, hour, minute, tzinfo=UTC), float(value)))
    return items


def last(series: Sequence[tuple[datetime, float]], moment: datetime) -> tuple[datetime, float] | None:
    seen = [item for item in series if item[0] <= moment]
    return seen[-1] if seen else None


def replay(flow: str, back: str, *, valid_for: timedelta = PROBE_VALID, until: time = time(1, 40)) -> TestRun:
    """Every minute from arming to `until`, as the room control does it; the thermostat stays at 23 °C as it did."""
    run = TestRun(room_id="sensor.bad", trv="climate.bad", armed_at=ARMED)
    watch = SwapWatch()
    flow_series, back_series = reports(flow), reports(back)
    room_series, valve_series = reports(COMET), reports(VALVE)
    moment = ARMED
    end = datetime.combine(datetime(2026, 11, 22).date(), until, tzinfo=UTC)
    while moment <= end and run.active:
        readings = []
        for series, which in ((flow_series, "Vorlauf"), (back_series, "Rücklauf")):
            report = last(series, moment)
            value, reported = (None, None) if report is None else (str(report[1]), report[0])
            readings.append(probe_reading(value, reported, moment, which, valid_for=valid_for))
        (flow_c, flow_problem), (back_c, back_problem) = readings
        room = last(room_series, moment)
        valve = last(valve_series, moment)
        room_c = room[1] if room else None
        watch.update(moment, flow_c, back_c, valve[1] if valve else None, room_c)
        problem = flow_problem or back_problem
        if problem is None and watch.swapped and flow_c is not None and back_c is not None:
            problem = swapped_text(flow_c, back_c)
        inputs = TestInputs(
            now=moment,
            local=moment,
            setpoint_c=23.0,
            room_c=room_c,
            trv_c=room_c,
            trv_setpoint_c=23.0,
            valve=valve[1] if valve else None,
            flow_c=flow_c,
            return_c=back_c,
            window_open=False,
            limits=COMET_LIMITS,
            probe_problem=problem,
            may_write=True,
            rated_w=537.0,
        )
        advance(run, inputs, time(22, 0), time(1, 0), time(5, 0))
        moment += timedelta(minutes=1)
    return run


def test_the_night_as_it_was_set_up_never_starts_and_says_why() -> None:
    run = replay(SONDE_1, SONDE_2)

    assert run.state is TestState.ARMED and run.started_at is None
    assert "Vorlauf ist kälter als der Rücklauf" in run.message


def test_the_night_with_the_sensors_swapped_starts_when_the_bathroom_reaches_its_band() -> None:
    run = replay(SONDE_2, SONDE_1)

    assert run.started_at == datetime(2026, 11, 21, 23, 41, tzinfo=UTC)
    # Through the quiet hour of the flow sensor (23:38 to 00:38) and the dip at 01:00 it keeps running.
    assert run.state is TestState.RUNNING, run.message


def test_with_the_rule_of_1_4_0_the_swapped_night_stops_after_half_an_hour_without_reports() -> None:
    run = replay(SONDE_2, SONDE_1, valid_for=timedelta(minutes=15))

    assert run.started_at == datetime(2026, 11, 21, 23, 41, tzinfo=UTC)
    assert run.state is TestState.ABORTED and run.message == "Kein warmes Wasser seit 30 min"
    # The flow sensor reported at 23:38; from 23:54 its reading no longer counted.
    assert run.ended_at == datetime(2026, 11, 22, 0, 24, tzinfo=UTC)
