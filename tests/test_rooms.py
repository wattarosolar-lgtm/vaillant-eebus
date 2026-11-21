"""The room control (1.4.0): controller, test run, heat supply, thermostats and the service end to end
with the fake Home Assistant. All values invented."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.fake_home_assistant import (
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
from vaillant_eebus.rooms.service import LIFESIGN_ENTITY, RoomControl
from vaillant_eebus.rooms.supply import HeatSupply, heat_supply, restart_temperature
from vaillant_eebus.rooms.testrun import TestInputs, TestRun, TestState, advance
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


def test_the_curve_gives_the_offset_for_an_opening() -> None:
    curve = complete_curve()

    assert curve.learned
    assert curve.levels() == [0.0, 20.0, 50.0, 80.0, 100.0]
    assert curve.offset_for(50.0) == 1.5
    assert curve.offset_for(0.0) == -1.0
    assert curve_from_json(curve_to_json(curve)) == curve
    partial = ValveCurve((replace(curve.results[0], complete=False),))
    assert not partial.learned


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
        "probe_here": True,
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
    assert sent == [21.5, 22.5, 23.0, 23.5, 24.0, 23.5, 23.0, 22.5, 22.0, 21.5, 21.0, 20.5]
    result = run.result()
    assert result is not None and result.complete
    assert result.opening_k == 1.0 and result.full_k == 2.5
    assert run.ended_at is not None and run.ended_at.hour < 5
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

    play(run, datetime(2026, 11, 24, 0, 50, tzinfo=UTC), 6 * 60, wobble=True)

    assert run.state is TestState.ABORTED and run.message == "Ende des Nachtfensters"
    assert run.ended_at == datetime(2026, 11, 24, 5, 0, tzinfo=UTC)
    result = run.result()
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
        room_inputs(curve=complete_curve(), room_c=20.5, valve=20.0, trv_setpoint_c=21.0, demand_valve=30.0),
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
        control = RoomControl(settings_store, device, hems, room_gate, home, log_store, tmp_path, clock=clock)
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
    world.control._curves[trv] = complete_curve()


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
    assert bath["curve"]["learned"]


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
