"""The room control (1.4.0): controller, test run, heat supply, thermostats and the service end to end
with the fake Home Assistant. All values invented."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.fake_home_assistant import (
    COMET,
    MV_HOT_WATER,
    MV_OUTDOOR,
    MV_POWER,
    MV_ZONE_1,
    MV_ZONE_2,
    OFFICE,
    OFFICE_HUMIDITY,
    PROBE_FLOW,
    PROBE_RETURN,
    TOKEN,
    TRV_BATH,
    TRV_BATH_TEMPERATURE,
    TRV_BATH_VALVE,
    TRV_OFFICE,
    TRV_OFFICE_VALVE,
    FakeHomeAssistant,
)
from tests.support import eventually
from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.bridge_client import Backoff, BridgeClient
from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import EntityState, HomeAssistantClient
from vaillant_eebus.hems.config import HemsSettings, PlannerMode, ZoneConfig, ZoneKind
from vaillant_eebus.hems.room_model import FlowHint
from vaillant_eebus.hems.service import HemsService
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.myvaillant import MyVaillantLink, MyVaillantState
from vaillant_eebus.pairing import ConnectionState, PairingService
from vaillant_eebus.room_data import (
    ProbePair,
    RoomControlMode,
    RoomControlSettings,
    RoomDetails,
    RoomStage,
    ScheduleWindow,
    effective_stage,
)
from vaillant_eebus.rooms.controller import Correction, RoomInputs, RoomMode, decide
from vaillant_eebus.rooms.curve import Step, TestResult, ValveCurve, curve_from_json, curve_to_json
from vaillant_eebus.rooms.learning import MapPoint, Source, Supply, ValveMap, points_from_steps
from vaillant_eebus.rooms.service import LIFESIGN_ENTITY, RoomControl, _Zone
from vaillant_eebus.rooms.supply import HeatSupply, SupplyNow, heat_supply, restart_temperature
from vaillant_eebus.rooms.testrun import Phase, TestInputs, TestRun, TestState, advance
from vaillant_eebus.rooms.thermostats import TrvModel, blocking, read, side_entities
from vaillant_eebus.settings import Connection, MyVaillantEntities, OperatingMode, Room, SettingsStore
from vaillant_eebus.write_gate import RoomGate, RoomReason, SetThermostatSetpoint, ThermostatLimits, WriteGate

BUFFER = "sensor.buffer_temperature"
T0 = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
AQARA_LIMITS = ThermostatLimits(5.0, 30.0, 0.5)


# --- stages ------------------------------------------------------------------------------------------


def test_the_switch_caps_each_room() -> None:
    assert effective_stage(RoomControlMode.CONTROL, RoomStage.PREVIEW) is RoomControlMode.PREVIEW
    assert effective_stage(RoomControlMode.PREVIEW, RoomStage.CONTROL) is RoomControlMode.PREVIEW
    assert effective_stage(RoomControlMode.OFF, RoomStage.CONTROL) is RoomControlMode.OFF
    assert effective_stage(RoomControlMode.CONTROL, RoomStage.CONTROL) is RoomControlMode.CONTROL


def test_a_sensor_of_the_thermostat_counts_as_measured_at_the_thermostat() -> None:
    bath = Room(TRV_BATH_TEMPERATURE, "Bad", details=RoomDetails(trv_entity=TRV_BATH))
    office = Room(OFFICE, "Büro", details=RoomDetails(trv_entity=TRV_OFFICE))

    assert bath.measured_at_thermostat
    assert not office.measured_at_thermostat


# --- thermostats --------------------------------------------------------------------------------------


def state(entity_id: str, value: str, **attributes: Any) -> EntityState:
    return EntityState(entity_id, value, attributes, T0)


def test_the_other_entities_of_a_thermostat_are_found_by_its_address() -> None:
    states = [
        state("select.0x00158d0000000a01_sensor", "internal"),
        state("lock.0x00158d0000000a01_child_lock", "locked"),
        state("sensor.0x00158d0000000a01_position", "30"),
        state("sensor.0x00158d0000000a01_linkquality", "120"),
        state("select.0x00158d0000000b02_trv_mode", "2"),
    ]

    side = side_entities(TRV_OFFICE, states)

    assert side == {
        "sensor": "select.0x00158d0000000a01_sensor",
        "child_lock": "lock.0x00158d0000000a01_child_lock",
        "position": "sensor.0x00158d0000000a01_position",
    }
    assert read(TRV_OFFICE, None, side, {}).model is TrvModel.AQARA
    assert read(TRV_BATH, None, side_entities(TRV_BATH, states), {}).model is TrvModel.COMET


def test_wrong_thermostat_settings_are_warned_about_and_never_written() -> None:
    side_states = {
        "select.0x00158d0000000a01_sensor": state("select.0x00158d0000000a01_sensor", "external"),
        "switch.0x00158d0000000a01_window_detection": state("switch.0x00158d0000000a01_window_detection", "on"),
        "lock.0x00158d0000000a01_child_lock": state("lock.0x00158d0000000a01_child_lock", "unlocked"),
        "sensor.0x00158d0000000a01_battery": state("sensor.0x00158d0000000a01_battery", "12"),
    }
    side = {
        "sensor": "select.0x00158d0000000a01_sensor",
        "window_detection": "switch.0x00158d0000000a01_window_detection",
        "child_lock": "lock.0x00158d0000000a01_child_lock",
        "battery": "sensor.0x00158d0000000a01_battery",
    }
    trv = state(TRV_OFFICE, "auto", min_temp=5, max_temp=30, temperature=21, current_temperature=21.5)

    thermostat = read(TRV_OFFICE, trv, side, side_states)

    assert len(thermostat.warnings) == 5
    assert [warning.split(" ")[0] for warning in blocking(thermostat)] == ["Fühler"]
    assert thermostat.setpoint_c == 21.0 and thermostat.temperature_c == 21.5


def test_a_comet_outside_trv_mode_2_may_not_be_controlled() -> None:
    side = {"trv_mode": "select.0x00158d0000000b02_trv_mode"}
    side_states = {"select.0x00158d0000000b02_trv_mode": state("select.0x00158d0000000b02_trv_mode", "1")}
    trv = state(TRV_BATH, "heat", min_temp=8, max_temp=28, temperature=22)

    assert blocking(read(TRV_BATH, trv, side, side_states))
    assert blocking(read(TRV_BATH, state(TRV_BATH, "unavailable"), side, side_states))


# --- heat supply ---------------------------------------------------------------------------------------


def series(values: list[float], *, every: int = 5, end: datetime = T0) -> list[tuple[datetime, float]]:
    return [(end - timedelta(minutes=every * (len(values) - 1 - index)), value) for index, value in enumerate(values)]


def test_heat_comes_from_the_heat_pump_or_the_buffer() -> None:
    running = heat_supply(now=T0, power=series([600]), buffer=series([34.0]), room_c=21.0)
    falling = heat_supply(
        now=T0, power=series([20]), buffer=series([33.0, 32.6, 32.2, 31.8, 31.4, 31.0, 30.6]), room_c=21.0
    )
    standing = heat_supply(now=T0, power=series([20]), buffer=series([32.0] * 7), room_c=21.0)
    stale = heat_supply(now=T0 + timedelta(minutes=30), power=series([600]), buffer=series([34.0]), room_c=21.0)

    assert running.supply is HeatSupply.HEAT_PUMP
    assert falling.supply is HeatSupply.BUFFER
    # 4.8 K/h down to 28 °C, the usual restart: 2.6 K left -> about 33 minutes.
    assert falling.remaining_min == 33
    assert standing.supply is HeatSupply.NONE
    assert stale.supply is HeatSupply.UNKNOWN


def test_the_restart_temperature_is_learned_from_the_starts() -> None:
    power: list[tuple[datetime, float]] = []
    buffer: list[tuple[datetime, float]] = []
    for cycle in range(4):
        start = T0 + timedelta(hours=2 * cycle)
        power += [(start + timedelta(minutes=5 * step), 10.0) for step in range(6)]
        power += [(start + timedelta(minutes=30), 650.0)]
        buffer += [(start + timedelta(minutes=30), 28.0 + 0.2 * cycle)]

    assert restart_temperature(power, buffer) == 28.3


# --- valve curve and test run ------------------------------------------------------------------------


def step(offset: float, valve: float, *, rising: bool = True) -> Step:
    return Step(21.5 + offset, 21.5, 21.0, offset, valve, rising, 20.0, 5.0)


def complete_curve() -> ValveCurve:
    result = TestResult(
        started_at=T0,
        ended_at=T0 + timedelta(hours=4),
        complete=True,
        ending="Fertig",
        steps=(
            step(0.0, 0.0),
            step(1.0, 25.0),
            step(1.5, 50.0),
            step(2.0, 75.0),
            step(2.5, 100.0),
            step(1.0, 40.0, rising=False),
            step(-1.0, 0.0, rising=False),
        ),
    )
    return ValveCurve((result,))


def known_map() -> ValveMap:
    """The valve map a complete test run leaves (its steps as points)."""
    result = complete_curve().results[0]
    return ValveMap().add_points(points_from_steps(result.steps, result.ended_at))


def test_the_steps_of_a_test_run_make_a_valve_map_that_gives_the_offset_for_an_opening() -> None:
    curve = complete_curve()
    valve_map = known_map()
    now = T0 + timedelta(hours=5)

    assert curve_from_json(curve_to_json(curve)) == curve
    assert valve_map.ready(now) and valve_map.missing(now) == []
    assert valve_map.levels(now) == [0.0, 20.0, 40.0, 50.0, 80.0, 100.0]
    assert valve_map.offset_for(50.0, rising=True, supply=Supply.UNKNOWN, now=now) == 1.5
    # Falling, 40 % was reached at +1 K already (hysteresis).
    assert valve_map.offset_for(40.0, rising=False, supply=Supply.UNKNOWN, now=now) == 1.0
    # Closed: the highest offset still closed - falling the way down closed it at -1 K, rising it opened above 0 K.
    assert valve_map.offset_for(0.0, rising=False, supply=Supply.UNKNOWN, now=now) == -1.0
    assert valve_map.offset_for(0.0, rising=True, supply=Supply.UNKNOWN, now=now) == 0.0
    assert valve_map.full(now) == (2.5, 100.0)


def night_inputs(moment: datetime, run: TestRun, **changes: Any) -> TestInputs:
    setpoint = run.setpoint_c if run.setpoint_c is not None else 21.0
    # The thermostat opens above +0.5 K and is fully open at +2.5 K.
    valve = max(0.0, min(100.0, (setpoint - 21.5 - 0.5) / 2.0 * 100))
    values: dict[str, Any] = {
        "now": moment,
        "local": moment,
        "setpoint_c": 21.0,
        "room_c": 21.0,
        "trv_c": 21.5,
        "trv_setpoint_c": setpoint,
        "valve": valve,
        "flow_c": 40.0,
        "return_c": 30.0 + valve / 20,
        "window_open": False,
        "limits": AQARA_LIMITS,
        "probe_problem": None,
        "may_write": True,
        "rated_w": 1600.0,
    }
    values.update(changes)
    return TestInputs(**values)


def play(run: TestRun, start: datetime, minutes: int, *, wobble: bool = False, **changes: Any) -> list[float]:
    sent = []
    for minute in range(minutes):
        moment = start + timedelta(minutes=minute)
        if wobble:
            # The return never settles: every step takes its longest time.
            changes["return_c"] = 30.0 + minute % 2
        action = advance(run, night_inputs(moment, run, **changes), time(22, 0), time(1, 0), time(5, 0))
        if action.kind == "send" and action.celsius is not None:
            sent.append(action.celsius)
        if not run.active:
            break
    return sent


def test_a_test_run_steps_up_to_open_and_down_to_closed_at_night() -> None:
    run = TestRun(room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0)

    sent = play(run, datetime(2026, 11, 23, 21, 30, tzinfo=UTC), 8 * 60)

    assert run.state is TestState.DONE
    assert run.started_at == datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    # 1 K first (25 %: 25 %/K), then 1 K to the 50 % mark (75 %: 50 points, too much), so 0.5 K (100 %, fully
    # open); down 1 K first (50 %: 50 points), 0.5 K (25 %), 0.5 K to the 5 % mark (closed).
    assert sent == [21.5, 22.5, 23.5, 24.0, 23.0, 22.5, 22.0]
    result = run.result()
    assert result is not None and result.complete
    assert result.opening_k == 1.0 and result.full_k == 2.5 and result.closing_k == 0.5
    assert run.ended_at is not None and run.ended_at.hour < 2
    assert len(sent) + 1 <= 16
    heat = [item.heat_w for item in result.steps]
    assert all(value is not None and value > 0 for value in heat)


def test_a_test_run_waits_for_the_room_and_gives_up_after_three_nights() -> None:
    run = TestRun(room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0)
    for night in range(3):
        play(run, datetime(2026, 11, 23, 21, 0, tzinfo=UTC) + timedelta(days=night), 6 * 60, room_c=22.0)

    assert run.state is TestState.FAILED
    assert "nahe genug" in run.message


def test_a_test_run_stops_when_the_room_gets_too_warm() -> None:
    run = TestRun(room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0)
    start = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    play(run, start, 60)
    assert run.started_at is not None and run.before_c == 21.0

    action = advance(
        run, night_inputs(start + timedelta(minutes=61), run, room_c=22.1), time(22, 0), time(1, 0), time(5, 0)
    )

    assert action.kind == "end" and action.celsius == 21.0
    assert run.state is TestState.ABORTED and run.message == "Raum über Soll + 1 K"


def test_a_test_run_ends_at_five_with_what_it_measured() -> None:
    run = TestRun(room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0)

    # Every step takes its 40 minutes: from 0:50 the night is too short for open and closed.
    play(run, datetime(2026, 11, 24, 0, 50, tzinfo=UTC), 6 * 60, wobble=True, valve=10.0)

    assert run.state is TestState.ABORTED and run.message == "Ende des Nachtfensters"
    assert run.ended_at == datetime(2026, 11, 24, 5, 0, tzinfo=UTC)
    result = run.outcome
    assert result is not None and not result.complete and len(result.steps) >= 5


def test_a_test_run_stops_without_warm_water() -> None:
    run = TestRun(room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0)
    start = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    play(run, start, 10)

    play(run, start + timedelta(minutes=10), 40, flow_c=24.0)

    assert run.state is TestState.ABORTED and run.message == "Kein warmes Wasser seit 30 min"


# --- controller -----------------------------------------------------------------------------------


def room_inputs(**changes: Any) -> RoomInputs:
    values: dict[str, Any] = {
        "now": T0,
        "local": T0,
        "details": RoomDetails(setpoint_c=21.0, trv_entity=TRV_OFFICE),
        "at_thermostat": False,
        "limits": AQARA_LIMITS,
        "room_c": 21.0,
        "trend_k_per_h": 0.0,
        "valve": 40.0,
        "trv_setpoint_c": 21.0,
        "trv_c": 21.5,
        "window_open": False,
        "window_closed_at": None,
        "heat": True,
    }
    values.update(changes)
    return RoomInputs(**values)


def test_the_window_closes_the_thermostat_and_comes_first() -> None:
    decision, _ = decide(room_inputs(window_open=True, room_c=23.0), Correction())

    assert (decision.target_c, decision.reason, decision.mode) == (5.0, RoomReason.WINDOW, RoomMode.WINDOW)


def test_a_too_warm_room_gets_a_low_setpoint_at_once() -> None:
    decision, _ = decide(room_inputs(room_c=22.1), Correction())

    assert (decision.target_c, decision.reason) == (20.5, RoomReason.SAFETY)


def test_the_bathroom_gets_its_setpoint_plus_the_fixed_offset() -> None:
    details = RoomDetails(setpoint_c=22.0, offset_k=1.0, trv_entity=TRV_BATH)

    decision, _ = decide(room_inputs(details=details, at_thermostat=True, room_c=26.0), Correction())

    # At the radiator it is warm while heating: no safety rule there.
    assert (decision.target_c, decision.reason) == (23.0, RoomReason.CONTROL)


def test_the_correction_rises_after_half_an_hour_too_cold() -> None:
    correction = Correction()
    for minutes in (0, 15, 30):
        decision, correction = decide(room_inputs(now=T0 + timedelta(minutes=minutes), room_c=20.7), correction)

    assert correction.value_k == 0.5
    assert decision.target_c == 21.5


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"room_c": 20.7, "valve": 95.0}, 0.0),
        ({"room_c": 20.7, "heat": False}, 0.0),
        ({"room_c": 21.3, "valve": 3.0}, 0.0),
        ({"room_c": 21.3, "valve": 40.0}, -0.5),
    ],
)
def test_the_valve_rules_hold_the_correction(changes: dict[str, Any], expected: float) -> None:
    correction = Correction()
    for minutes in (0, 30):
        _, correction = decide(room_inputs(now=T0 + timedelta(minutes=minutes), **changes), correction)

    assert correction.value_k == expected


def test_a_manual_change_is_set_back() -> None:
    decision, _ = decide(room_inputs(trv_setpoint_c=25.0, expected_c=21.0), Correction())

    assert (decision.target_c, decision.reason, decision.manual) == (21.0, RoomReason.MANUAL, True)


def test_a_donor_is_throttled_to_its_lower_comfort_limit() -> None:
    decision, _ = decide(room_inputs(donor=True), Correction())

    assert (decision.target_c, decision.reason) == (20.5, RoomReason.REDISTRIBUTE)


def test_preheating_starts_an_hour_before_a_warmer_window() -> None:
    details = RoomDetails(
        setpoint_c=19.0,
        trv_entity=TRV_OFFICE,
        schedule=(ScheduleWindow(time(13, 0), time(18, 0), 21.0),),
    )

    early, _ = decide(room_inputs(details=details, local=T0 - timedelta(minutes=1), room_c=19.0), Correction())
    now, _ = decide(room_inputs(details=details, local=T0 + timedelta(minutes=1), room_c=19.0), Correction())

    assert early.target_c == 19.0
    assert (now.target_c, now.reason, now.mode) == (21.0, RoomReason.PREHEAT, RoomMode.PREHEAT)


def test_the_cascade_picks_the_setpoint_for_the_opening_the_room_needs() -> None:
    decision, _ = decide(
        room_inputs(valve_map=known_map(), room_c=20.5, valve=20.0, trv_setpoint_c=21.0, demand_valve=30.0),
        Correction(),
    )

    # 30 % + 0.5 K too cold * 30 %/K = 45 % -> the 50 % step at +1.5 K above the thermostat's 21.5 °C.
    assert decision.target_c == 23.0
    assert "45 %" in decision.note


# --- the service end to end ----------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@dataclass
class World:
    fake: FakeHomeAssistant
    settings: SettingsStore
    home: HomeAssistant
    hems: HemsService
    gate: RoomGate
    control: RoomControl
    log: LogStore
    clock: Clock
    http: httpx.AsyncClient

    def calls(self, entity_id: str | None = None) -> list[float]:
        return [
            body["temperature"]
            for service, body, _query in self.fake.service_calls
            if service == "climate.set_temperature"
            and body["entity_id"] in self.fake.trvs
            and (entity_id is None or body["entity_id"] == entity_id)
        ]

    def stage(self, room_id: str, stage: RoomStage, **details: Any) -> None:
        self.settings.update(
            lambda current: replace(
                current,
                rooms=tuple(
                    replace(room, details=replace(room.details, stage=stage, **details))
                    if room.entity_id == room_id
                    else room
                    for room in current.rooms
                ),
            )
        )


def world_settings(settings: SettingsStore) -> None:
    settings.update(
        lambda current: replace(
            current,
            connection=Connection.MYVAILLANT,
            operating_mode=OperatingMode.LIVE,
            myvaillant=MyVaillantEntities(
                zones=(MV_ZONE_1, MV_ZONE_2), hot_water=MV_HOT_WATER, outdoor_temperature=MV_OUTDOOR, power=MV_POWER
            ),
            rooms=(
                Room(
                    OFFICE,
                    "Büro",
                    zone_id=MV_ZONE_2,
                    details=RoomDetails(
                        trv_entity=TRV_OFFICE,
                        valve_entity=TRV_OFFICE_VALVE,
                        humidity_entity=OFFICE_HUMIDITY,
                        setpoint_c=21.0,
                        emergency_c=20.0,
                    ),
                ),
                Room(
                    TRV_BATH_TEMPERATURE,
                    "Bad",
                    zone_id=MV_ZONE_2,
                    details=RoomDetails(
                        trv_entity=TRV_BATH, valve_entity=TRV_BATH_VALVE, setpoint_c=22.0, emergency_c=22.0
                    ),
                ),
            ),
            export=frozenset(),
            hems=HemsSettings(
                mode=PlannerMode.PREVIEW,
                buffer_entity=BUFFER,
                zones=(ZoneConfig(MV_ZONE_1, kind=ZoneKind.FLOOR), ZoneConfig(MV_ZONE_2, kind=ZoneKind.RADIATOR)),
            ),
            room_control=RoomControlSettings(mode=RoomControlMode.CONTROL),
        )
    )


@pytest.fixture
async def world(
    fake_ha: FakeHomeAssistant,
    settings_store: SettingsStore,
    log_store: LogStore,
    bridge_client: BridgeClient,
    pairing: PairingService,
    tmp_path: Path,
) -> AsyncIterator[World]:
    fake_ha.add_myvaillant()
    fake_ha.add_thermostats()
    fake_ha.set(BUFFER, "34.0", unit="°C", name="Puffer")
    world_settings(settings_store)
    clock = Clock()
    async with HomeAssistantClient(
        fake_ha.url, TOKEN, log=log_store, backoff=Backoff(initial=0.05, maximum=0.2), refresh_interval=60
    ) as client:
        device = MyVaillantState(settings_store, available=True)
        home = HomeAssistant(settings_store, device, EnergyMeter(device, tmp_path), log_store, client)
        hems = HemsService(
            settings_store, device, home, log_store, tmp_path, clock=clock, open_meteo_url=f"{fake_ha.url}/v1/dwd-icon"
        )
        room_gate = RoomGate(client, settings_store, home.hems_sources, log_store, tmp_path, clock=clock)
        control = RoomControl(
            settings_store, device, hems, room_gate, home, log_store, tmp_path, clock=clock, read_stagger=0
        )
        gate = WriteGate(
            bridge_client,
            pairing,
            device,
            settings_store,
            log_store,
            link=MyVaillantLink(client, device, settings_store),
        )
        task = asyncio.create_task(client.run())
        await eventually(lambda: device.connection_state() is ConnectionState.CONNECTED)
        await eventually(lambda: home.hems_sources.get(TRV_BATH) is not None)
        await eventually(lambda: home.hems_sources.get(BUFFER) is not None)
        api = create_app(
            pairing,
            device,
            gate,
            log_store,
            EnergyMeter(device, tmp_path),
            home,
            static_dir=None,
            trusted_clients=frozenset({INGRESS_CLIENT}),
            hems=hems,
            rooms=control,
        )
        transport = httpx.ASGITransport(app=api, client=(INGRESS_CLIENT, 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as http:
            yield World(fake_ha, settings_store, home, hems, room_gate, control, log_store, clock, http)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        hems.samples.close()


async def beat(world: World, minutes: float = 5) -> None:
    """Five minutes later: the energy management samples, the room control follows."""
    world.clock.now += timedelta(minutes=minutes)
    await world.control.minute()
    await world.hems.sample()


async def messages(log: LogStore, category: LogCategory | None = None) -> list[str]:
    return [entry.message for entry in (await log.page(category=category)).entries]


def learned(world: World, trv: str) -> None:
    # As if a test run had been done (it takes a night).
    world.control._maps[trv] = known_map()


CONTACT_OFFICE = "binary_sensor.buero_fenster"


async def test_the_bathroom_in_control_gets_its_setpoint_plus_offset(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)

    await beat(world)

    assert world.calls(TRV_BATH) == [23.0]
    assert world.calls(TRV_OFFICE) == []
    await eventually(lambda: world.home.hems_sources.attribute(TRV_BATH, "temperature") == 23.0)
    await beat(world)
    assert world.calls(TRV_BATH) == [23.0]


async def test_a_room_in_preview_only_logs_what_it_would_send(world: World) -> None:
    world.stage(OFFICE, RoomStage.PREVIEW, offset_k=0.0)
    world.fake.set(OFFICE, "22.3", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 22.3)

    await beat(world)

    assert world.calls() == []
    texts = await messages(world.log, LogCategory.ROOMS)
    assert any(text.startswith("Vorschau, würde senden: Thermostat Büro: Soll 20,5 °C (Sicherheit") for text in texts)


async def test_control_needs_a_complete_test_run_and_a_confirmation(world: World) -> None:
    refused = await world.http.put(
        f"/api/rooms/{TRV_BATH_TEMPERATURE}/stage", json={"stage": "control", "confirm": True}
    )
    assert refused.status_code == 409
    assert "Testlauf" in refused.json()["detail"]

    learned(world, TRV_BATH)
    unconfirmed = await world.http.put(f"/api/rooms/{TRV_BATH_TEMPERATURE}/stage", json={"stage": "control"})
    confirmed = await world.http.put(
        f"/api/rooms/{TRV_BATH_TEMPERATURE}/stage", json={"stage": "control", "confirm": True}
    )

    assert unconfirmed.status_code == 400
    assert confirmed.status_code == 200
    bath = next(room for room in confirmed.json()["control"]["rooms"] if room["name"] == "Bad")
    assert bath["stage"] == "control" and bath["effective"] == "control"
    assert bath["valve_map"]["ready"] and bath["valve_map"]["missing"] == []


async def test_demo_mode_hands_the_thermostats_back_first(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    await beat(world)

    response = await world.http.post("/api/mode", json={"mode": "demo"})

    assert response.status_code == 200
    assert world.calls(TRV_BATH) == [23.0, 22.0]
    assert world.gate.touched() == []
    assert any("gibt die Thermostate zurück (Demo-Betrieb): Bad 22,0 °C" in text for text in await messages(world.log))


async def test_leaving_control_hands_the_room_back(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    await beat(world)
    world.clock.now += timedelta(seconds=10)

    response = await world.http.put(f"/api/rooms/{TRV_BATH_TEMPERATURE}/stage", json={"stage": "preview"})

    assert response.status_code == 200
    assert world.calls(TRV_BATH) == [23.0, 22.0]


async def test_stopping_the_app_hands_the_thermostats_back(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    await beat(world)
    world.clock.now += timedelta(seconds=10)
    task = asyncio.create_task(world.control.run())
    await asyncio.sleep(0.05)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert world.calls(TRV_BATH) == [23.0, 22.0]


async def test_a_manual_change_at_the_thermostat_is_set_back(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL)
    await beat(world)
    assert world.calls(TRV_BATH) == []
    await world.gate.submit(SetThermostatSetpoint(TRV_BATH_TEMPERATURE, 22.5, RoomReason.CONTROL))
    await eventually(lambda: not world.gate.sending(TRV_BATH))
    world.clock.now += timedelta(minutes=1)
    world.fake.change(TRV_BATH, temperature=28.0)
    await eventually(lambda: world.home.hems_sources.attribute(TRV_BATH, "temperature") == 28.0)

    await beat(world)

    assert world.calls(TRV_BATH)[-1] == 22.0
    assert any("Handänderung am Thermostat Bad zurückgesetzt" in text for text in await messages(world.log))


async def test_an_open_window_sets_the_thermostat_to_its_lowest_value(world: World) -> None:
    learned(world, TRV_OFFICE)
    world.stage(OFFICE, RoomStage.CONTROL)
    world.fake.set(OFFICE, "21.1", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 21.1)
    await beat(world)
    world.clock.now += timedelta(minutes=4)
    world.fake.set(OFFICE, "20.4", unit="°C")
    await eventually(lambda: world.hems.window_open(OFFICE))

    await beat(world, 1)

    assert world.calls(TRV_OFFICE)[-1] == 5.0
    assert any(text.startswith("Fenster offen erkannt: Büro") for text in await messages(world.log, LogCategory.ROOMS))


async def test_after_the_window_the_thermostat_goes_back_at_once(world: World) -> None:
    learned(world, TRV_OFFICE)
    world.stage(OFFICE, RoomStage.CONTROL)
    world.fake.set(OFFICE, "21.1", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 21.1)
    await beat(world)
    world.clock.now += timedelta(minutes=4)
    world.fake.set(OFFICE, "20.4", unit="°C")
    await eventually(lambda: world.hems.window_open(OFFICE))
    await beat(world, 1)
    await eventually(lambda: world.home.hems_sources.attribute(TRV_OFFICE, "temperature") == 5.0)
    world.clock.now += timedelta(minutes=3)
    world.fake.set(OFFICE, "20.7", unit="°C")
    await eventually(lambda: not world.hems.window_open(OFFICE))

    await beat(world)

    # 8 minutes after the window's command, well within the interval of 15 minutes: the setpoint the
    # learned valve curve gives for the 0.3 K too cold room (50 % at +1.5 K above the thermostat's 21.5 °C).
    assert world.calls(TRV_OFFICE)[-2:] == [5.0, 23.0]


async def test_the_life_sign_is_reported_whatever_the_export(world: World) -> None:
    assert world.control.sensors()[0].state == "unavailable"

    await world.control.minute()

    sensor = world.control.sensors()[0]
    assert sensor.entity_id == LIFESIGN_ENTITY
    assert sensor.state == T0.isoformat()
    assert world.home.exported() == [LIFESIGN_ENTITY]


async def test_the_emergency_automation_lists_every_thermostat(world: World) -> None:
    response = await world.http.get("/api/rooms/automation")

    yaml = response.json()["yaml"]
    assert LIFESIGN_ENTITY in yaml
    assert f"entity_id: {TRV_OFFICE}   # Büro (Notfallwert)" in yaml
    assert "temperature: 20\n" in yaml and "temperature: 22\n" in yaml
    assert "total_seconds() > 900" in yaml


async def test_control_for_all_rooms_needs_live_mode_and_a_confirmation(world: World) -> None:
    world.settings.update(
        lambda current: replace(current, room_control=replace(current.room_control, mode=RoomControlMode.PREVIEW))
    )

    unconfirmed = await world.http.put("/api/rooms/control", json={"mode": "control"})
    confirmed = await world.http.put("/api/rooms/control", json={"mode": "control", "confirm": True})

    assert unconfirmed.status_code == 400
    assert confirmed.status_code == 200 and confirmed.json()["control"]["mode"] == "control"


async def test_a_test_run_is_armed_for_the_radiator_with_the_probe_pair(world: World) -> None:
    away = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "arm", "confirm": True})
    assert away.status_code == 409

    probe = await world.http.put(
        "/api/rooms/probe", json={"flow_entity": PROBE_FLOW, "return_entity": PROBE_RETURN, "room_id": OFFICE}
    )
    assert probe.status_code == 200
    assert world.settings.current.room_control.probe == ProbePair(PROBE_FLOW, PROBE_RETURN, OFFICE, T0)
    armed = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "arm", "confirm": True})
    second = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "arm", "confirm": True})
    cancelled = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "cancel"})

    assert armed.status_code == 200
    office = next(room for room in armed.json()["control"]["rooms"] if room["name"] == "Büro")
    assert office["test"]["state"] == "armed"
    assert second.status_code == 409
    assert cancelled.status_code == 200
    assert world.calls() == []


async def test_the_room_editor_keeps_the_stage_and_refuses_a_myvaillant_zone(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL)
    body = {"trv_entity": TRV_BATH, "valve_entity": TRV_BATH_VALVE, "setpoint_c": 22.0, "offset_k": 1.5}

    saved = await world.http.put(f"/api/rooms/{TRV_BATH_TEMPERATURE}", json=body)
    zone = await world.http.put(f"/api/rooms/{OFFICE}", json={"trv_entity": MV_ZONE_2, "setpoint_c": 21.0})

    assert saved.status_code == 200
    bath = next(room for room in world.settings.current.rooms if room.name == "Bad")
    assert bath.details.stage is RoomStage.CONTROL and bath.details.offset_k == 1.5
    assert zone.status_code == 422


async def test_the_flow_waits_for_redistribution_while_rooms_preview(world: World) -> None:
    world.stage(OFFICE, RoomStage.PREVIEW)
    await beat(world)

    advice = world.control.flow_advice(MV_ZONE_2, FlowHint.HIGHER)

    assert advice is not None and advice[0] is FlowHint.HOLD
    assert world.control.flow_advice(MV_ZONE_2, FlowHint.LOWER) is None


# --- findings of the review of 1.4.0 ----------------------------------------------------------------


def test_too_warm_lowers_the_correction_once() -> None:
    correction = Correction(value_k=1.0)

    _, correction = decide(room_inputs(room_c=22.1), correction)
    _, still = decide(room_inputs(room_c=22.2, now=T0 + timedelta(minutes=5)), correction)
    _, after = decide(room_inputs(room_c=21.0, now=T0 + timedelta(minutes=10)), still)

    # Finding 11: on entering "zu warm" the correction goes down at once, but only once.
    assert (correction.value_k, correction.too_warm) == (0.5, True)
    assert (still.value_k, still.too_warm) == (0.5, True)
    assert (after.value_k, after.too_warm) == (0.5, False)


def test_the_cascade_sets_a_manual_change_back_to_its_own_value() -> None:
    decision, _ = decide(
        room_inputs(valve_map=known_map(), room_c=21.0, valve=20.0, trv_setpoint_c=23.0, expected_c=21.0),
        Correction(),
    )

    # Finding 10: 30 % -> the 20 % step (falling, from 20 % open: reached at +1 K above the thermostat's 21.5 °C).
    # Compared with the hand's 23 °C the hysteresis would keep 23 °C; compared with what the app set (21 °C) it
    # goes to 22.5 °C.
    assert (decision.target_c, decision.reason) == (22.5, RoomReason.MANUAL)


def test_a_running_heat_pump_without_water_temperatures_is_unclear() -> None:
    supply = heat_supply(now=T0, power=series([600]), buffer=[], room_c=21.0)

    assert supply.supply is HeatSupply.UNKNOWN


def test_a_test_run_is_complete_only_when_the_valve_closed_again() -> None:
    run = TestRun(room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0, state=TestState.DONE, reached_full=True)
    run.started_at, run.ended_at = T0, T0 + timedelta(hours=4)
    run.steps = [step(0.0, 0.0), step(2.5, 100.0), step(1.5, 60.0, rising=False)]
    result = run.result()
    assert result is not None and not result.complete

    run.steps.append(step(-1.0, 0.0, rising=False))
    run.reached_closed = True
    result = run.result()
    assert result is not None and result.complete


def test_a_night_whose_start_was_before_arming_does_not_count() -> None:
    run = TestRun(room_id=OFFICE, trv=TRV_OFFICE, armed_at=datetime(2026, 11, 24, 1, 30, tzinfo=UTC))

    play(run, datetime(2026, 11, 24, 1, 30, tzinfo=UTC), 5 * 60, room_c=22.0)
    assert (run.state, run.nights) == (TestState.ARMED, 0)

    play(run, datetime(2026, 11, 24, 21, 0, tzinfo=UTC), 6 * 60, room_c=22.0)
    assert run.nights == 1


def office_test(celsius: float, reason: RoomReason = RoomReason.TEST) -> SetThermostatSetpoint:
    return SetThermostatSetpoint(OFFICE, celsius, reason)


async def test_a_way_back_that_does_not_go_out_falls_back(world: World) -> None:
    await world.gate.submit(office_test(24.0))
    await eventually(lambda: world.home.hems_sources.attribute(TRV_OFFICE, "temperature") == 24.0)
    # A run whose way back is not allowed (not the value from before, the emergency value or the room's setpoint).
    world.control._test = TestRun(
        room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0, state=TestState.RUNNING, before_c=23.5, started_at=T0
    )

    await world.control.cancel_test(OFFICE)

    # Finding 2: the fallback follows.
    assert world.calls(TRV_OFFICE) == [24.0, 20.0]
    assert any("Rückweg nach dem Testlauf Büro nicht gesendet" in text for text in await messages(world.log))
    assert world.gate.touched() == []


async def test_a_test_run_cut_by_a_restart_goes_back(world: World) -> None:
    await world.gate.submit(office_test(24.0))
    await eventually(lambda: world.home.hems_sources.attribute(TRV_OFFICE, "temperature") == 24.0)
    world.control._test = TestRun(
        room_id=OFFICE, trv=TRV_OFFICE, armed_at=T0, state=TestState.RUNNING, before_c=21.0, started_at=T0
    )
    world.control._save()

    restarted = RoomControl(
        world.settings,
        world.control._state,
        world.hems,
        world.gate,
        world.home,
        world.log,
        world.control._path.parent,
        clock=world.clock,
    )
    await restarted.minute()

    assert world.calls(TRV_OFFICE) == [24.0, 21.0]
    # The office stays in "Beobachten": the app lets go after the way back.
    assert world.gate.touched() == []


async def test_the_life_sign_stands_still_without_the_control_beat(world: World) -> None:
    await world.control.minute()
    assert world.control.sensors()[0].state == T0.isoformat()

    world.clock.now = T0 + timedelta(minutes=13)
    await world.control.minute()

    # Finding 3: the minute loop runs, but the control has not computed for 13 minutes.
    assert world.control.sensors()[0].state == T0.isoformat()
    assert any("rechnet seit über 12 Minuten nicht mehr" in text for text in await messages(world.log))
    await world.hems.sample()
    await world.control.minute()
    assert world.control.sensors()[0].state == world.clock.now.isoformat()


async def test_a_blocking_warning_hands_a_set_thermostat_back(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    await beat(world)
    world.fake.set(f"select.{COMET}_trv_mode", "1", name="Thermostat Bad trv_mode")

    # After 15 minutes the app reads the thermostat's other entities again.
    await beat(world, 16)

    assert world.calls(TRV_BATH) == [23.0, 22.0]
    assert any("Die App gibt es auf den Notfallwert zurück" in text for text in await messages(world.log))


async def test_removing_a_room_hands_its_thermostat_back_first(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    await beat(world)

    response = await world.http.put(
        "/api/settings",
        json={"rooms": [{"entity_id": OFFICE, "name": "Büro", "zone_id": MV_ZONE_2}], "export": []},
    )

    assert response.status_code == 200
    # Finding 4.
    assert world.calls(TRV_BATH) == [23.0, 22.0]
    assert [room.name for room in world.settings.current.rooms] == ["Büro"]


async def test_a_thermostat_whose_room_is_gone_gets_its_value_when_the_app_stops(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    await beat(world)
    world.settings.update(
        lambda current: replace(
            current, rooms=tuple(room for room in current.rooms if room.entity_id != TRV_BATH_TEMPERATURE)
        )
    )
    task = asyncio.create_task(world.control.run())
    await asyncio.sleep(0.05)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert world.calls(TRV_BATH) == [23.0, 22.0]


async def test_changing_a_rooms_thermostat_hands_the_old_one_back(world: World) -> None:
    await world.gate.submit(SetThermostatSetpoint(TRV_BATH_TEMPERATURE, 24.0, RoomReason.TEST))

    response = await world.http.put(
        f"/api/rooms/{TRV_BATH_TEMPERATURE}",
        json={"trv_entity": None, "valve_entity": TRV_BATH_VALVE, "setpoint_c": 22.0, "emergency_c": 22.0},
    )

    assert response.status_code == 200
    assert world.calls(TRV_BATH) == [24.0, 22.0]
    assert world.gate.touched() == []


async def test_the_life_sign_lists_what_the_emergency_automation_sets(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    await beat(world)
    world.settings.update(
        lambda current: replace(
            current,
            rooms=tuple(
                replace(room, name="Büro\nOben") if room.entity_id == OFFICE else room for room in current.rooms
            ),
        )
    )

    sensor = world.control.sensors()[0]
    yaml = (await world.http.get("/api/rooms/automation")).json()["yaml"]

    # Finding 5b: only the thermostats the app has set; the fixed list only without the life sign.
    assert sensor.attributes["notfallwerte"] == [{"entity_id": TRV_BATH, "temperature": 22.0}]
    assert f"state_attr('{LIFESIGN_ENTITY}', 'notfallwerte')" in yaml
    assert 'for_each: "{{ gestellt }}"' in yaml
    assert "    else:\n      - action: climate.set_temperature" in yaml
    # H5: a name over two lines stays one comment line.
    assert f"entity_id: {TRV_OFFICE}   # Büro Oben (Notfallwert)\n" in yaml


async def test_a_window_in_the_bathroom_right_after_a_command_is_not_believed(world: World) -> None:
    learned(world, TRV_BATH)
    world.stage(TRV_BATH_TEMPERATURE, RoomStage.CONTROL, offset_k=1.0)
    world.fake.set(TRV_BATH_TEMPERATURE, "22.6", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(TRV_BATH_TEMPERATURE) == 22.6)
    await beat(world)
    world.clock.now += timedelta(minutes=3)
    # Finding 6: the sensor at the radiator falls after the app's own command (1.4.3: not even seen as a window).
    world.fake.set(TRV_BATH_TEMPERATURE, "20.6", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(TRV_BATH_TEMPERATURE) == 20.6)

    await beat(world, 1)

    assert not world.hems.window_open(TRV_BATH_TEMPERATURE)
    assert world.calls(TRV_BATH) == [23.0]


async def test_the_bathroom_sees_a_window_by_a_fast_fall_at_its_thermostat(world: World) -> None:
    world.fake.set(TRV_BATH_TEMPERATURE, "22.6", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(TRV_BATH_TEMPERATURE) == 22.6)
    world.clock.now += timedelta(minutes=2)
    world.fake.set(TRV_BATH_TEMPERATURE, "21.6", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(TRV_BATH_TEMPERATURE) == 21.6)
    assert not world.hems.window_open(TRV_BATH_TEMPERATURE)
    world.clock.now += timedelta(minutes=2)

    world.fake.set(TRV_BATH_TEMPERATURE, "21.1", unit="°C")

    await eventually(lambda: world.hems.window_open(TRV_BATH_TEMPERATURE))


async def test_the_room_control_closes_the_thermostat_as_soon_as_the_window_is_seen(world: World) -> None:
    learned(world, TRV_OFFICE)
    world.stage(OFFICE, RoomStage.CONTROL)
    world.fake.set(OFFICE, "21.1", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 21.1)
    await beat(world)
    world.clock.now += timedelta(minutes=4)

    world.fake.set(OFFICE, "20.4", unit="°C")

    # No beat: the window decides at once (1.4.3).
    await eventually(lambda: world.calls(TRV_OFFICE)[-1:] == [5.0])
    log = await messages(world.log, LogCategory.ROOMS)
    assert any(text.startswith("Fenster offen erkannt: Büro (Raum \u22120,7 K") for text in log)


async def test_a_window_contact_decides_at_once_and_alone(world: World) -> None:
    learned(world, TRV_OFFICE)
    world.stage(OFFICE, RoomStage.CONTROL, window_entity=CONTACT_OFFICE)
    # Saving the room in the app follows the new entity too.
    world.home.watch()
    world.fake.set(OFFICE, "21.1", unit="°C")
    world.fake.set(CONTACT_OFFICE, "off", attributes={"device_class": "window"})
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 21.1)
    await eventually(lambda: world.home.hems_sources.get(CONTACT_OFFICE) is not None)
    await beat(world)
    # The rules would see a window; with a contact only the contact counts.
    world.clock.now += timedelta(minutes=4)
    world.fake.set(OFFICE, "20.4", unit="°C")
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 20.4)
    assert not world.hems.window_open(OFFICE)

    world.fake.set(CONTACT_OFFICE, "on", attributes={"device_class": "window"})

    await eventually(lambda: world.calls(TRV_OFFICE)[-1:] == [5.0])
    log = await messages(world.log, LogCategory.ROOMS)
    assert any(text.startswith("Fenster offen erkannt: Büro (Fensterkontakt)") for text in log)
    await eventually(lambda: world.home.hems_sources.attribute(TRV_OFFICE, "temperature") == 5.0)
    world.clock.now += timedelta(minutes=3)
    world.fake.set(CONTACT_OFFICE, "off", attributes={"device_class": "window"})
    await eventually(lambda: not world.hems.window_open(OFFICE))
    await eventually(lambda: world.calls(TRV_OFFICE)[-1] != 5.0)


async def test_a_silent_window_contact_gives_way_to_the_rules_after_ten_minutes(world: World) -> None:
    world.stage(OFFICE, RoomStage.OBSERVE, window_entity=CONTACT_OFFICE)
    # Saving the room in the app follows the new entity too.
    world.home.watch()
    world.fake.set(CONTACT_OFFICE, "on", attributes={"device_class": "window"})
    await eventually(lambda: world.hems.window_open(OFFICE))

    world.fake.set(CONTACT_OFFICE, "unavailable", attributes={"device_class": "window"})
    await eventually(lambda: world.home.hems_sources.get(CONTACT_OFFICE).state == "unavailable")  # type: ignore[union-attr]
    assert world.hems.window_open(OFFICE)
    world.clock.now += timedelta(minutes=11)

    assert not world.hems.window_open(OFFICE)
    room = next(item for item in world.hems.rooms_view().rooms if item.entity_id == OFFICE)
    assert room.window_source == "rules"


async def test_a_room_thermostat_cannot_become_a_myvaillant_zone(world: World) -> None:
    response = await world.http.put(
        "/api/connection",
        json={
            "connection": "myvaillant",
            "myvaillant": {
                "zones": [MV_ZONE_1, TRV_OFFICE],
                "hot_water": MV_HOT_WATER,
                "outdoor_temperature": MV_OUTDOOR,
                "power": MV_POWER,
            },
        },
    )

    assert response.status_code == 422
    assert world.settings.current.myvaillant.zones == (MV_ZONE_1, MV_ZONE_2)


# --- the probe pair after the field acceptance of 1.4.0 (1.4.1) -----------------------------------------------


async def hang_probe_at_the_office(world: World) -> None:
    response = await world.http.put(
        "/api/rooms/probe", json={"flow_entity": PROBE_FLOW, "return_entity": PROBE_RETURN, "room_id": OFFICE}
    )
    assert response.status_code == 200


async def probe_values(world: World, flow: str, back: str, *, flow_age: float = 0, back_age: float = 0) -> None:
    """New readings of the pair, reported flow_age and back_age minutes ago."""
    now = world.clock.now
    world.fake.set(PROBE_FLOW, flow, unit="°C", name="Fühler A", reported=now - timedelta(minutes=flow_age))
    world.fake.set(PROBE_RETURN, back, unit="°C", name="Fühler B", reported=now - timedelta(minutes=back_age))
    await eventually(lambda: world.home.hems_sources.number(PROBE_FLOW) == float(flow))
    await eventually(lambda: world.home.hems_sources.number(PROBE_RETURN) == float(back))


def room_named(world: World, name: str) -> Room:
    return next(room for room in world.settings.current.rooms if room.name == name)


async def test_a_quiet_probe_counts_for_two_hours_and_a_silent_one_is_named(world: World) -> None:
    await hang_probe_at_the_office(world)
    # Sensors that report only on a change: the flow 90 minutes ago, the return 130 minutes ago.
    await probe_values(world, "40.2", "30.2", flow_age=90, back_age=130)
    now, settings = world.clock.now, world.settings.current

    office = world.control._test_inputs(room_named(world, "Büro"), TRV_OFFICE, now, settings)
    bath = world.control._test_inputs(room_named(world, "Bad"), TRV_BATH, now, settings)
    probe = (await world.http.get("/api/rooms")).json()["control"]["probe"]

    assert (office.flow_c, office.return_c) == (40.2, None)
    assert office.probe_problem == "Der Rücklauf-Fühler meldet seit über 2 h nichts."
    assert bath.probe_problem == "Das Fühlerpaar hängt nicht am Heizkörper von Bad."
    assert (probe["flow_age_min"], probe["return_age_min"], probe["valid_hours"]) == (90.0, 130.0, 2.0)
    assert probe["swapped"] is False and "longest_gap_min" in probe and "report_minutes" not in probe


async def test_swapped_probes_are_reported_and_swapping_them_clears_it(world: World) -> None:
    await hang_probe_at_the_office(world)
    # Warm water at the office's open valve (30 %), but the return is the warmer pipe.
    await probe_values(world, "26.4", "30.3")

    for _ in range(16):
        world.clock.now += timedelta(minutes=1)
        await world.control.minute()

    probe = (await world.http.get("/api/rooms")).json()["control"]["probe"]
    assert probe["swapped"] is True
    texts = await messages(world.log, LogCategory.ROOMS)
    assert any("Der Vorlauf ist kälter als der Rücklauf (26,4 / 30,3 °C)" in text for text in texts)
    office = world.control._test_inputs(room_named(world, "Büro"), TRV_OFFICE, world.clock.now, world.settings.current)
    assert office.probe_problem is not None and "vertauscht" in office.probe_problem

    swapped = await world.http.put(
        "/api/rooms/probe", json={"flow_entity": PROBE_RETURN, "return_entity": PROBE_FLOW, "room_id": OFFICE}
    )

    assert swapped.status_code == 200 and swapped.json()["control"]["probe"]["swapped"] is False
    # Still the same radiator: hanging there since the first choice.
    assert world.settings.current.room_control.probe == ProbePair(PROBE_RETURN, PROBE_FLOW, OFFICE, T0)
    office = world.control._test_inputs(room_named(world, "Büro"), TRV_OFFICE, world.clock.now, world.settings.current)
    assert office.probe_problem is None


async def test_what_an_armed_run_waits_for_is_logged_once_a_night(world: World) -> None:
    await hang_probe_at_the_office(world)
    armed = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "arm", "confirm": True})
    assert armed.status_code == 200
    world.clock.now = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    # The office is 2 K too warm to start, and the return sensor has been silent for three hours.
    world.fake.set(OFFICE, "23.0", unit="°C", name="Büro Temperatur")
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 23.0)
    await probe_values(world, "40.2", "30.2", back_age=180)
    for _ in range(3):
        await world.control.minute()
        world.clock.now += timedelta(minutes=1)
    await probe_values(world, "40.4", "30.4")
    for _ in range(3):
        await world.control.minute()
        world.clock.now += timedelta(minutes=1)
    world.clock.now = datetime(2026, 11, 24, 1, 0, tzinfo=UTC)
    await world.control.minute()
    world.clock.now += timedelta(minutes=1)
    await world.control.minute()
    await world.control.cancel_test(OFFICE)

    lines = [text for text in reversed(await messages(world.log, LogCategory.ROOMS)) if text.startswith("Testlauf")]
    band = "Der Raum ist nicht nahe genug an seinem Soll (0,5 K darunter bis 0,2 K darüber)"
    assert lines == [
        "Testlauf Büro vorgemerkt: startet ab 22:00 Uhr.",
        "Testlauf Büro wartet: Der Rücklauf-Fühler meldet seit über 2 h nichts.",
        f"Testlauf Büro wartet: {band}.",
        f"Testlauf Büro heute Nacht nicht gestartet: zuletzt {band}.",
        f"Testlauf Büro abgebrochen: vom Nutzer abgebrochen; wartete zuletzt: {band}.",
    ]


# --- 1.4.2: reads of the W600, learning, the live page ------------------------------------------------------

READ_PAYLOAD = '{"running_state": ""}'


def reads(world: World) -> list[dict[str, Any]]:
    return [body for service, body, _query in world.fake.service_calls if service == "mqtt.publish"]


async def room_status(world: World, name: str) -> dict[str, Any]:
    page = (await world.http.get("/api/rooms")).json()["control"]
    room: dict[str, Any] = next(room for room in page["rooms"] if room["name"] == name)
    return room


async def test_the_w600_is_read_through_the_write_gate_and_the_comet_is_not(world: World) -> None:
    world.fake.positions["Thermostat Büro"] = 55.0

    await world.control.minute()

    assert reads(world) == [
        {"topic": "zigbee2mqtt/Thermostat Büro/get", "payload": READ_PAYLOAD, "qos": 0, "retain": False}
    ]
    await eventually(lambda: world.home.hems_sources.number(TRV_OFFICE_VALVE) == 55.0)
    office, bath = await room_status(world, "Büro"), await room_status(world, "Bad")
    assert office["read"]["topic"] == "zigbee2mqtt/Thermostat Büro/get"
    assert office["read"]["found_name"] == "Thermostat Büro" and office["read"]["today"] == 1
    assert bath["read"] is None


async def test_reads_go_out_in_demo_mode_too_but_not_with_the_room_control_off(world: World) -> None:
    world.settings.update(lambda current: replace(current, operating_mode=OperatingMode.DEMO))
    await world.control.minute()
    assert len(reads(world)) == 1

    world.settings.update(
        lambda current: replace(current, room_control=replace(current.room_control, mode=RoomControlMode.OFF))
    )
    world.clock.now += timedelta(minutes=10)
    await world.control.minute()

    assert len(reads(world)) == 1


async def test_the_regular_read_comes_shortly_before_the_beat(world: World) -> None:
    await beat(world, 0)
    first = len(reads(world))
    times: list[int] = []
    for minute in range(1, 11):
        world.clock.now += timedelta(minutes=1)
        await world.control.minute()
        if len(reads(world)) > first + len(times):
            times.append(minute)
        if minute % 5 == 0:
            await world.hems.sample()

    # The beat (samples, then the room control) at 0, 5 and 10 minutes. After the read at the start the next waits
    # 4 minutes (1 minute before the beat at 5), then 2 minutes before the beat at 10.
    assert times == [4, 8]


async def test_a_new_setpoint_is_followed_by_reads_one_and_three_minutes_later(world: World) -> None:
    await world.control.minute()
    world.fake.change(TRV_OFFICE, temperature=22.5)
    await eventually(lambda: world.home.hems_sources.attribute(TRV_OFFICE, "temperature") == 22.5)

    for _ in range(4):
        world.clock.now += timedelta(minutes=1)
        await world.control.minute()

    # The first read at the start, then 1 and 3 minutes after the change by hand (the next regular one later).
    assert len(reads(world)) == 3


def seen_state(world: World, entity_id: str) -> str | None:
    state = world.home.hems_sources.get(entity_id)
    return state.state if state is not None else None


async def test_last_seen_shows_whether_the_w600_answered_a_read(world: World) -> None:
    world.fake.clock = world.clock
    long_ago = T0 - timedelta(hours=13)
    world.fake.set(TRV_OFFICE_VALVE, "30", unit="%", name="Thermostat Büro Ventil", reported=long_ago)
    seen = world.fake.add_last_seen(at=long_ago)

    await world.control.minute()
    await eventually(lambda: seen_state(world, seen) == T0.isoformat())
    office = await room_status(world, "Büro")

    assert office["last_seen"] and office["side"]["last_seen"] == seen
    # The position was unchanged (Home Assistant shows nothing new), last_seen shows the answer.
    assert datetime.fromisoformat(office["valve_at"]) == T0

    # It stops answering: the reads go out, the position counts from its last answer.
    del world.fake.z2m_devices["Thermostat Büro"]
    world.clock.now += timedelta(minutes=5)
    await world.control.minute()
    office = await room_status(world, "Büro")
    assert datetime.fromisoformat(office["read"]["last_read"]) == T0 + timedelta(minutes=5)
    assert datetime.fromisoformat(office["valve_at"]) == T0


def test_a_silent_thermostat_is_named_by_its_life_sign_not_by_an_old_valve() -> None:
    old = T0 - timedelta(hours=13)
    trv = EntityState(TRV_OFFICE, "heat", {"min_temp": 5, "max_temp": 30, "temperature": 21}, old)
    valve = EntityState(TRV_OFFICE_VALVE, "30", {}, old)
    seen_entity = TRV_OFFICE.replace("climate.", "sensor.") + "_last_seen"
    side = {"last_seen": seen_entity}
    silent = "Das Thermostat hat sich seit über 12 h nicht gemeldet."

    assert silent in read(TRV_OFFICE, trv, {}, {}, valve=valve, now=T0).warnings
    seen = EntityState(seen_entity, (T0 - timedelta(minutes=3)).isoformat(), {}, old)
    heard = read(TRV_OFFICE, trv, side, {}, valve=valve, now=T0, last_seen=seen)
    assert silent not in heard.warnings and heard.life_sign == T0 - timedelta(minutes=3) and heard.has_last_seen
    assert not any("Ventilstellung" in warning for warning in heard.warnings)


async def test_the_page_rooms_hears_of_a_change_at_once(world: World) -> None:
    async with world.control.changes() as changes:
        world.fake.set(OFFICE, "21.4", unit="°C", name="Büro Temperatur")
        async with asyncio.timeout(2):
            await changes.get()
        await beat(world)
        async with asyncio.timeout(2):
            await changes.get()


async def test_everyday_points_free_control_without_a_test_run(world: World) -> None:
    closed = MapPoint(T0, -0.5, False, Supply.MILD, 0.0, Source.DAILY)
    world.control._maps[TRV_BATH] = ValveMap().add_points([closed])

    refused = await world.http.put(
        f"/api/rooms/{TRV_BATH_TEMPERATURE}/stage", json={"stage": "control", "confirm": True}
    )
    world.control._maps[TRV_BATH] = world.control._maps[TRV_BATH].add_points(
        [MapPoint(T0, 2.0, True, Supply.MILD, 92.0, Source.DAILY)]
    )
    allowed = await world.http.put(
        f"/api/rooms/{TRV_BATH_TEMPERATURE}/stage", json={"stage": "control", "confirm": True}
    )

    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Die App kennt noch nicht: „ganz offen“ (aus einem schnellen Durchlauf, einem Testlauf oder dem Alltag)."
    )
    assert allowed.status_code == 200
    bath = next(room for room in allowed.json()["control"]["rooms"] if room["name"] == "Bad")
    assert bath["valve_map"]["ready"] and bath["valve_map"]["points_daily"] == 2
    assert (bath["valve_map"]["closed_k"], bath["valve_map"]["full_k"]) == (-0.5, 2.0)


async def test_a_calm_half_hour_in_everyday_operation_is_a_point_of_the_bathrooms_map(world: World) -> None:
    world.control._zones[MV_ZONE_2] = _Zone(supply=SupplyNow(HeatSupply.HEAT_PUMP))

    for _ in range(31):
        await world.control.minute()
        world.clock.now += timedelta(minutes=1)

    # The Comet: setpoint 22 °C, 22.5 °C at the thermostat, 40 % open; warm water from the buffer at 34 °C.
    points = world.control._maps[TRV_BATH].points
    assert [(item.offset_k, item.valve, item.supply, item.source) for item in points] == [
        (-0.5, 40.0, Supply.MILD, Source.DAILY)
    ]


async def test_a_test_run_for_a_valve_known_fully_open_goes_down_only(world: World) -> None:
    world.control._maps[TRV_OFFICE] = ValveMap().add_points([MapPoint(T0, 2.5, True, Supply.MILD, 90.0, Source.TEST)])
    await hang_probe_at_the_office(world)

    armed = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "arm", "confirm": True})

    assert armed.status_code == 200
    run = world.control._test
    assert run is not None and run.down_only and run.full_offset_k == 2.5
    assert (
        "Testlauf Büro vorgemerkt (nur abwärts, „ganz offen“ ist schon bekannt): startet ab 22:00 Uhr."
        in await messages(world.log, LogCategory.ROOMS)
    )


async def test_the_test_runs_of_1_4_1_become_the_first_points_of_the_map(world: World, tmp_path: Path) -> None:
    world.control._curves[TRV_BATH] = complete_curve()
    world.control._save()
    saved = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    del saved["maps"]
    (tmp_path / "rooms.json").write_text(json.dumps(saved), encoding="utf-8")

    again = RoomControl(world.settings, world.control._state, world.hems, world.gate, world.home, world.log, tmp_path)

    assert again._maps[TRV_BATH].ready(T0 + timedelta(hours=5))
    assert again._maps[TRV_BATH].counts()[Source.TEST] == 7


async def test_the_base_topic_and_a_rooms_own_device_name_go_into_the_topic(world: World) -> None:
    bad = await world.http.put("/api/rooms/mqtt", json={"base_topic": "z2m/#"})
    good = await world.http.put("/api/rooms/mqtt", json={"base_topic": "z2m"})
    details = {"trv_entity": TRV_OFFICE, "valve_entity": TRV_OFFICE_VALVE, "setpoint_c": 21.0}
    writes = await world.http.put(f"/api/rooms/{OFFICE}", json={**details, "zigbee_name": "Heizung/set"})
    named = await world.http.put(f"/api/rooms/{OFFICE}", json={**details, "zigbee_name": "Heizung Büro"})

    assert bad.status_code == 422 and good.status_code == 200
    assert good.json()["control"]["mqtt_base_topic"] == "z2m"
    assert writes.status_code == 422 and "get" in writes.json()["detail"]
    assert named.status_code == 200
    await world.control.minute()
    assert reads(world)[-1]["topic"] == "z2m/Heizung Büro/get"
    office = await room_status(world, "Büro")
    assert office["read"]["topic"] == "z2m/Heizung Büro/get" and office["read"]["found_name"] == "Thermostat Büro"


# --- 1.4.3: sweeps, quality, demand and gain -------------------------------------------------------------


def office_valve(world: World) -> float:
    """The W600 in the office: opens 40 % per K above -0.5 K to the 21.5 °C at the thermostat."""
    setpoint = float(world.fake.states[TRV_OFFICE]["attributes"]["temperature"])
    return max(0.0, min(100.0, (setpoint - 21.5 + 0.5) * 40))


def _valve_is(world: World, wanted: float) -> bool:
    return world.home.hems_sources.number(TRV_OFFICE_VALVE) == wanted


async def sweep_minutes(world: World, minutes: int, *, until: Any = None) -> None:
    """Minute by minute; the W600 answers each read with the position for its setpoint now."""
    for _ in range(minutes):
        world.fake.positions["Thermostat Büro"] = office_valve(world)
        before = len(reads(world))
        world.clock.now += timedelta(minutes=1)
        await world.control.minute()
        if len(reads(world)) > before:
            await eventually(partial(_valve_is, world, world.fake.positions["Thermostat Büro"]))
        if until is not None and until():
            return


async def test_a_sweep_by_the_button_learns_the_map_and_frees_control(world: World) -> None:
    await world.control.minute()
    refused = await world.http.post(f"/api/rooms/{OFFICE}/sweep", json={"action": "start"})
    started = await world.http.post(f"/api/rooms/{OFFICE}/sweep", json={"action": "start", "confirm": True})

    assert refused.status_code == 400
    assert started.status_code == 200
    office = next(room for room in started.json()["control"]["rooms"] if room["name"] == "Büro")
    assert office["sweep"]["state"] == "running" and office["sweep_blocked"] == "Der Durchlauf läuft."
    await sweep_minutes(world, 30, until=lambda: world.control._sweep is None)

    assert world.calls(TRV_OFFICE) == [21.5, 22.0, 22.5, 23.0, 23.5, 23.0, 22.5, 22.0, 21.5, 21.0]
    texts = await messages(world.log, LogCategory.ROOMS)
    assert "Schneller Durchlauf Büro gestartet." in texts
    assert any(text.startswith("Schneller Durchlauf Büro fertig (Fertig): 10 Punkte") for text in texts)
    assert (
        "Büro: Die App kennt jetzt „zu“ und „ganz offen“ (aus dem schnellen Durchlauf); „Steuern“ ist möglich." in texts
    )
    office = await room_status(world, "Büro")
    assert office["can_control"] and office["valve_map"]["points_sweep"] == 10
    assert office["sweep"]["state_name"] == "fertig" and len(office["sweep"]["points"]) == 10
    # Its own budget: not among the day's commands.
    commands = world.gate.commands(TRV_OFFICE)
    assert (commands.today, commands.sweep_today) == (0, 10)
    assert world.control._testing(world.settings.current.rooms[0]) is False


async def test_a_sweep_stops_and_goes_back_when_the_window_opens(world: World) -> None:
    world.stage(OFFICE, RoomStage.OBSERVE, window_entity=CONTACT_OFFICE)
    world.home.watch()
    world.fake.set(CONTACT_OFFICE, "off", attributes={"device_class": "window"})
    await eventually(lambda: world.home.hems_sources.get(CONTACT_OFFICE) is not None)
    await world.control.minute()
    await world.http.post(f"/api/rooms/{OFFICE}/sweep", json={"action": "start", "confirm": True})
    await sweep_minutes(world, 5)

    world.fake.set(CONTACT_OFFICE, "on", attributes={"device_class": "window"})
    await eventually(lambda: world.hems.window_open(OFFICE))
    await sweep_minutes(world, 1)

    assert world.control._sweep is None
    assert world.calls(TRV_OFFICE)[-1] == 21.0
    texts = await messages(world.log, LogCategory.ROOMS)
    assert any(text.startswith("Schneller Durchlauf Büro abgebrochen (Fenster offen)") for text in texts)


async def test_sweeps_for_all_rooms_run_one_after_the_other_and_stop_together(world: World) -> None:
    await world.control.minute()
    response = await world.http.post("/api/rooms/sweep", json={"action": "start", "confirm": True})

    assert response.status_code == 200
    assert response.json()["control"]["sweep_queue"] == ["Bad"]

    def bath_runs() -> bool:
        sweep = world.control._sweep
        return sweep is not None and sweep.trv == TRV_BATH

    await sweep_minutes(world, 30, until=bath_runs)

    sweep = world.control._sweep
    assert sweep is not None and sweep.room_id == TRV_BATH_TEMPERATURE
    # The Comet starts at the 22.5 °C at its sensor.
    assert world.calls(TRV_BATH) == [22.5]
    await eventually(lambda: world.home.hems_sources.attribute(TRV_BATH, "temperature") == 22.5)
    cancelled = await world.http.post("/api/rooms/sweep", json={"action": "cancel"})

    assert cancelled.status_code == 200
    assert world.control._sweep is None and world.control._sweep_queue == []
    assert world.calls(TRV_BATH) == [22.5, 22.0]
    texts = await messages(world.log, LogCategory.ROOMS)
    assert "Schneller Durchlauf für alle Räume nacheinander: Büro, Bad." in texts
    assert any(text.startswith("Schneller Durchlauf Bad abgebrochen (vom Nutzer abgebrochen)") for text in texts)


async def test_a_sweep_needs_live_mode_and_no_test_run_going_on(world: World) -> None:
    world.settings.update(lambda current: replace(current, operating_mode=OperatingMode.DEMO))
    demo = await world.http.post(f"/api/rooms/{OFFICE}/sweep", json={"action": "start", "confirm": True})
    world.settings.update(lambda current: replace(current, operating_mode=OperatingMode.LIVE))
    world.control._test = TestRun(room_id=TRV_BATH_TEMPERATURE, trv=TRV_BATH, armed_at=T0, state=TestState.RUNNING)
    testing = await world.http.post(f"/api/rooms/{OFFICE}/sweep", json={"action": "start", "confirm": True})

    assert demo.status_code == 409 and "Live-Betrieb" in demo.json()["detail"]
    assert testing.status_code == 409 and testing.json()["detail"] == "Es läuft gerade ein Testlauf."
    assert world.calls() == []


async def test_the_quality_of_each_day_is_logged_shortly_after_midnight(world: World) -> None:
    world.stage(OFFICE, RoomStage.PREVIEW)
    world.fake.set(OFFICE, "20.2", unit="°C", name="Büro Temperatur")
    await eventually(lambda: world.home.hems_sources.number(OFFICE) == 20.2)

    await beat(world)
    await beat(world)
    office = await room_status(world, "Büro")
    quality = office["regulation"]["quality"]
    assert len(quality) == 1 and quality[0]["today"] and quality[0]["hours"] == 0.2
    assert quality[0]["mean_k"] == 0.8 and quality[0]["cold_pct"] == 100

    world.clock.now = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
    await beat(world)

    texts = await messages(world.log, LogCategory.ROOMS)
    line = next(text for text in texts if text.startswith("Regelgüte Büro am 23.11.:"))
    assert "im Mittel 0,8 K zu kalt" in line and "(Vorschau: würde senden)" in line
    office = await room_status(world, "Büro")
    assert [day["today"] for day in office["regulation"]["quality"]] == [True, False]


async def test_the_demand_and_the_gain_start_and_can_be_reset(world: World) -> None:
    world.stage(OFFICE, RoomStage.PREVIEW)
    await beat(world)
    office = await room_status(world, "Büro")
    regulation = office["regulation"]
    assert regulation["applies"] and regulation["gain"] == 30.0 and regulation["trend"] == 10.0
    assert [stage["outdoor_c"] for stage in regulation["stages"]] == [-10.0, -5.0, 0.0, 5.0, 10.0, 15.0]
    assert [stage["value"] for stage in regulation["stages"]] == [30.0] * 6
    assert (await room_status(world, "Bad"))["regulation"]["applies"] is False

    state = world.control._rooms[OFFICE]
    assert state.gain is not None
    state.gain = replace(state.gain, value=45.0, reason="zu träge")
    reset = await world.http.post(f"/api/rooms/{OFFICE}/regulation/reset")
    bath = await world.http.post(f"/api/rooms/{TRV_BATH_TEMPERATURE}/regulation/reset")

    assert reset.status_code == 200 and bath.status_code == 409
    office = next(room for room in reset.json()["control"]["rooms"] if room["name"] == "Büro")
    assert office["regulation"]["gain"] == 30.0 and office["regulation"]["gain_reason"] is None
    assert "Büro: Gelerntes der Regelung zurückgesetzt (Bedarf aus dem Verlauf, Verstärkung 30 %/K)." in (
        await messages(world.log, LogCategory.ROOMS)
    )


async def test_quality_demand_gain_and_sweeps_survive_a_restart(world: World, tmp_path: Path) -> None:
    world.stage(OFFICE, RoomStage.PREVIEW)
    await beat(world)
    await world.control.minute()
    await world.http.post(f"/api/rooms/{OFFICE}/sweep", json={"action": "start", "confirm": True})
    await sweep_minutes(world, 30, until=lambda: world.control._sweep is None)
    world.control._save()

    again = RoomControl(world.settings, world.control._state, world.hems, world.gate, world.home, world.log, tmp_path)

    state = again._rooms[OFFICE]
    assert state.quality.today is not None and state.demand is not None and state.gain is not None
    assert len(again._sweeps[TRV_OFFICE][0].points) == 10


async def test_a_night_test_run_begins_with_a_sweep_and_then_holds_four_positions(world: World) -> None:
    await hang_probe_at_the_office(world)
    armed = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "arm", "confirm": True})
    assert armed.status_code == 200
    world.clock.now = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    await probe_values(world, "40.0", "30.0")

    await sweep_minutes(world, 1)
    run = world.control._test
    sweep = world.control._sweep
    assert run is not None and (run.state, run.phase) == (TestState.RUNNING, Phase.SWEEP)
    assert sweep is not None and sweep.part_of_test

    await sweep_minutes(world, 30, until=lambda: world.control._sweep is None)

    assert run.phase is Phase.HOLD and run.positions == [-1.0, 0.5, 1.0, 2.0]
    # The sweep's commands, then the first position (closed) as a test run's command.
    assert world.calls(TRV_OFFICE) == [21.5, 22.0, 22.5, 23.0, 23.5, 23.0, 22.5, 22.0, 21.5, 21.0, 20.5]
    commands = world.gate.commands(TRV_OFFICE)
    assert (commands.sweep_today, commands.test_today) == (10, 1)
    texts = await messages(world.log, LogCategory.ROOMS)
    assert "Testlauf Büro: Teil 1, schneller Durchlauf ohne Fühlerpaar." in texts
    assert "Testlauf Büro: Teil 2 mit Fühlerpaar, Stellung 1 von 4 (\u22121,0 K)." in texts

    cancelled = await world.http.post(f"/api/rooms/{OFFICE}/test", json={"action": "cancel"})
    assert cancelled.status_code == 200
    assert world.calls(TRV_OFFICE)[-1] == 21.0
