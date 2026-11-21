"""The write gate of the radiator thermostats (1.4.0): what it lets through, how it spares the valves,
and the structure tests that keep it the only way to the thermostats. All values invented."""

from __future__ import annotations

import ast
import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.fake_home_assistant import (
    MV_HOT_WATER,
    MV_OUTDOOR,
    MV_POWER,
    MV_ZONE_1,
    MV_ZONE_2,
    OFFICE,
    OFFICE_HUMIDITY,
    TOKEN,
    TRV_BATH,
    TRV_BATH_TEMPERATURE,
    TRV_BATH_VALVE,
    TRV_OFFICE,
    TRV_OFFICE_VALVE,
    FakeHomeAssistant,
)
from tests.support import eventually
from vaillant_eebus import ha_client as ha_client_module
from vaillant_eebus import hems as hems_package
from vaillant_eebus import rooms as rooms_package
from vaillant_eebus.bridge_client import Backoff
from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import ROOM_SERVICES, HomeAssistantClient
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.myvaillant import MyVaillantState
from vaillant_eebus.room_data import RoomControlMode, RoomControlSettings, RoomDetails, RoomStage
from vaillant_eebus.settings import Connection, MyVaillantEntities, OperatingMode, Room, SettingsStore
from vaillant_eebus.write_gate import (
    Held,
    RoomGate,
    RoomReason,
    SetThermostatSetpoint,
    WriteNotExecuted,
    WriteOutcomeUnknown,
    WriteRejected,
)


class Clock:
    def __init__(self) -> None:
        # Midday, local = UTC in these tests.
        self.now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class Rig:
    fake: FakeHomeAssistant
    settings: SettingsStore
    home: HomeAssistant
    log: LogStore
    clock: Clock
    data_dir: Path
    client: HomeAssistantClient
    gate: RoomGate

    def new_gate(self) -> RoomGate:
        return RoomGate(self.client, self.settings, self.home.hems_sources, self.log, self.data_dir, clock=self.clock)

    def calls(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        return [
            body
            for service, body, _query in self.fake.service_calls
            if service == "climate.set_temperature"
            and body["entity_id"] in self.fake.trvs
            and (entity_id is None or body["entity_id"] == entity_id)
        ]


def rooms(**office: Any) -> tuple[Room, ...]:
    return (
        Room(
            entity_id=OFFICE,
            name="Büro",
            zone_id=MV_ZONE_2,
            details=RoomDetails(
                trv_entity=TRV_OFFICE,
                valve_entity=TRV_OFFICE_VALVE,
                humidity_entity=OFFICE_HUMIDITY,
                setpoint_c=21.0,
                emergency_c=20.0,
                stage=RoomStage.CONTROL,
                **office,
            ),
        ),
        Room(
            entity_id=TRV_BATH_TEMPERATURE,
            name="Bad",
            zone_id=MV_ZONE_2,
            details=RoomDetails(trv_entity=TRV_BATH, valve_entity=TRV_BATH_VALVE, setpoint_c=22.0, emergency_c=22.0),
        ),
    )


def configure(settings: SettingsStore, *, mode: OperatingMode = OperatingMode.LIVE, **office: Any) -> None:
    settings.update(
        lambda current: replace(
            current,
            connection=Connection.MYVAILLANT,
            operating_mode=mode,
            myvaillant=MyVaillantEntities(
                zones=(MV_ZONE_1, MV_ZONE_2), hot_water=MV_HOT_WATER, outdoor_temperature=MV_OUTDOOR, power=MV_POWER
            ),
            rooms=rooms(**office),
            room_control=RoomControlSettings(mode=RoomControlMode.CONTROL),
        )
    )


@pytest.fixture
async def rig(
    fake_ha: FakeHomeAssistant, settings_store: SettingsStore, log_store: LogStore, tmp_path: Path
) -> AsyncIterator[Rig]:
    fake_ha.add_myvaillant()
    fake_ha.add_thermostats()
    configure(settings_store)
    clock = Clock()
    async with HomeAssistantClient(
        fake_ha.url, TOKEN, log=log_store, backoff=Backoff(initial=0.05, maximum=0.2), refresh_interval=60
    ) as client:
        device = MyVaillantState(settings_store, available=True)
        home = HomeAssistant(settings_store, device, EnergyMeter(device, tmp_path), log_store, client)
        gate = RoomGate(client, settings_store, home.hems_sources, log_store, tmp_path, clock=clock)
        task = asyncio.create_task(client.run())
        await eventually(lambda: home.hems_sources.get(TRV_OFFICE) is not None)
        await eventually(lambda: home.hems_sources.get(TRV_BATH) is not None)
        yield Rig(fake_ha, settings_store, home, log_store, clock, tmp_path, client, gate)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def office(celsius: float, reason: RoomReason = RoomReason.CONTROL) -> SetThermostatSetpoint:
    return SetThermostatSetpoint(room_id=OFFICE, celsius=celsius, reason=reason)


async def messages(log: LogStore, category: LogCategory | None = None, *, problems: bool = False) -> list[str]:
    return [entry.message for entry in (await log.page(category=category, problems=problems)).entries]


# --- what goes out ---------------------------------------------------------------------------------


async def test_a_room_in_control_gets_only_its_setpoint_and_it_is_confirmed(rig: Rig) -> None:
    result = await rig.gate.submit(office(22.0))

    assert result.sent
    assert rig.calls() == [{"entity_id": TRV_OFFICE, "temperature": 22.0}]
    await eventually(lambda: not rig.gate.sending(TRV_OFFICE))
    assert rig.gate.expected(TRV_OFFICE) == 22.0
    texts = await messages(rig.log, LogCategory.WRITE)
    assert any(text.startswith("Wird gesendet: Thermostat Büro: Soll 22,0 °C (Regelung)") for text in texts)
    assert "Bestätigt vom Thermostat: Büro 22,0 °C" in texts
    assert rig.gate.touched() == [TRV_OFFICE]


async def test_demo_mode_sends_nothing(rig: Rig) -> None:
    configure(rig.settings, mode=OperatingMode.DEMO)

    result = await rig.gate.submit(office(22.0))

    assert not result.sent
    assert rig.calls() == []
    assert any(text.startswith("Würde senden: Thermostat Büro") for text in await messages(rig.log, LogCategory.WRITE))
    assert rig.gate.touched() == []


async def test_a_preview_sends_nothing_even_live(rig: Rig) -> None:
    result = await rig.gate.submit(office(22.0), preview=True)

    assert not result.sent
    assert rig.calls() == []
    assert any(text.startswith("Vorschau, würde senden") for text in await messages(rig.log, LogCategory.ROOMS))
    assert rig.gate.commands(TRV_OFFICE, preview=True).today == 1
    assert rig.gate.commands(TRV_OFFICE).today == 0


@pytest.mark.parametrize(
    ("mode", "stage"),
    [(RoomControlMode.PREVIEW, RoomStage.CONTROL), (RoomControlMode.CONTROL, RoomStage.PREVIEW)],
)
async def test_only_a_room_in_control_with_the_switch_on_control_is_sent_to(
    rig: Rig, mode: RoomControlMode, stage: RoomStage
) -> None:
    rig.settings.update(
        lambda current: replace(
            current,
            room_control=replace(current.room_control, mode=mode),
            rooms=tuple(
                replace(room, details=replace(room.details, stage=stage)) if room.entity_id == OFFICE else room
                for room in current.rooms
            ),
        )
    )

    with pytest.raises(WriteRejected) as rejected:
        await rig.gate.submit(office(22.0))

    assert rejected.value.status == 409
    assert rig.calls() == []


async def test_a_test_run_needs_the_room_control_on_but_not_the_room_in_control(rig: Rig) -> None:
    bath = SetThermostatSetpoint(room_id=TRV_BATH_TEMPERATURE, celsius=24.0, reason=RoomReason.TEST)
    assert (await rig.gate.submit(bath)).sent

    rig.settings.update(
        lambda current: replace(current, room_control=replace(current.room_control, mode=RoomControlMode.OFF))
    )
    rig.clock.advance(minutes=1)
    with pytest.raises(WriteRejected):
        await rig.gate.submit(replace(bath, celsius=24.5))


async def test_the_fallback_goes_only_to_thermostats_the_app_has_set(rig: Rig) -> None:
    fallback = office(20.0, RoomReason.FALLBACK)
    with pytest.raises(WriteRejected):
        await rig.gate.submit(fallback)

    await rig.gate.submit(office(22.0))
    rig.clock.advance(seconds=10)
    assert (await rig.gate.submit(fallback)).sent
    assert rig.gate.touched() == []
    # Only the emergency value or the room's setpoint.
    await rig.gate.submit(office(22.0, RoomReason.SAFETY))
    rig.clock.advance(seconds=10)
    with pytest.raises(WriteRejected):
        await rig.gate.submit(office(23.0, RoomReason.FALLBACK))


async def test_values_must_fit_the_thermostat_and_the_room(rig: Rig) -> None:
    for celsius in (31.0, 22.3):
        with pytest.raises(WriteRejected) as rejected:
            await rig.gate.submit(office(celsius))
        assert rejected.value.status == 422
    # More than the limit of the correction (5 K) from the room's setpoint of 21 °C.
    with pytest.raises(WriteRejected):
        await rig.gate.submit(office(26.5))
    # The window's smallest value is fine.
    assert (await rig.gate.submit(office(5.0, RoomReason.WINDOW))).sent
    assert rig.calls() == [{"entity_id": TRV_OFFICE, "temperature": 5.0}]


async def test_a_myvaillant_zone_is_never_a_room_thermostat(rig: Rig) -> None:
    rig.settings.update(
        lambda current: replace(
            current,
            rooms=tuple(
                replace(room, details=replace(room.details, trv_entity=MV_ZONE_2)) if room.entity_id == OFFICE else room
                for room in current.rooms
            ),
        )
    )

    with pytest.raises(WriteRejected) as rejected:
        await rig.gate.submit(office(22.0))

    assert "myVAILLANT" in rejected.value.message
    assert rig.fake.service_calls == []


async def test_an_unknown_room_is_refused(rig: Rig) -> None:
    with pytest.raises(WriteRejected) as rejected:
        await rig.gate.submit(SetThermostatSetpoint(room_id="sensor.keller", celsius=20.0, reason=RoomReason.CONTROL))
    assert rejected.value.status == 404


# --- sparing the valves ---------------------------------------------------------------------------


async def test_the_same_value_and_the_dead_band_send_nothing(rig: Rig) -> None:
    configure(rig.settings, deadband_k=1.0)

    same = await rig.gate.submit(office(21.0))
    small = await rig.gate.submit(office(21.5))

    assert same.held is Held.SAME
    assert small.held is Held.DEADBAND
    assert rig.calls() == []
    # A window is no small change of the calculation.
    assert (await rig.gate.submit(office(21.5, RoomReason.WINDOW))).sent


async def test_the_interval_holds_back_the_next_command(rig: Rig) -> None:
    assert (await rig.gate.submit(office(22.0))).sent
    rig.clock.advance(minutes=10)

    held = await rig.gate.submit(office(22.5))

    assert held.held is Held.INTERVAL
    assert held.retry_at == datetime(2026, 11, 23, 12, 15, tzinfo=UTC)
    # A manual change is set back at once.
    assert (await rig.gate.submit(office(21.5, RoomReason.MANUAL))).sent
    rig.clock.advance(minutes=15)
    assert (await rig.gate.submit(office(22.5))).sent


async def test_the_commands_of_a_day_are_limited(rig: Rig) -> None:
    configure(rig.settings, max_commands=4, min_interval_min=5)
    for index in range(4):
        assert (await rig.gate.submit(office(21.5 + 0.5 * index))).sent
        rig.clock.advance(minutes=6)

    held = await rig.gate.submit(office(20.0))
    again = await rig.gate.submit(office(20.0))

    assert held.held is Held.LIMIT and again.held is Held.LIMIT
    problems = await messages(rig.log, LogCategory.ROOMS, problems=True)
    assert sum("Höchstzahl von 4 Befehlen" in text for text in problems) == 1
    # Window, safety and fallback still go out.
    assert (await rig.gate.submit(office(5.0, RoomReason.WINDOW))).sent
    rig.clock.advance(minutes=6)
    assert (await rig.gate.submit(office(20.5, RoomReason.SAFETY))).sent
    # The next day counts again.
    rig.clock.advance(hours=12)
    assert (await rig.gate.submit(office(22.0))).sent
    assert rig.gate.commands(TRV_OFFICE).today == 1


async def test_a_test_run_has_its_own_budget(rig: Rig) -> None:
    configure(rig.settings, max_commands=4)
    for index in range(16):
        rig.clock.advance(seconds=6)
        command = office(10.0 + 0.5 * index, RoomReason.TEST)
        assert (await rig.gate.submit(command)).sent, index
    rig.clock.advance(seconds=6)

    assert (await rig.gate.submit(office(20.0, RoomReason.TEST))).held is Held.LIMIT
    assert rig.gate.commands(TRV_OFFICE).today == 0
    rig.clock.advance(minutes=15)
    assert (await rig.gate.submit(office(22.0))).sent


async def test_the_counting_survives_a_restart(rig: Rig) -> None:
    await rig.gate.submit(office(22.0))
    rig.clock.advance(minutes=1)

    gate = rig.new_gate()

    assert gate.commands(TRV_OFFICE).today == 1
    assert gate.touched() == [TRV_OFFICE]
    assert (await gate.submit(office(22.5))).held is Held.INTERVAL


# --- confirmation and errors ----------------------------------------------------------------------


async def test_a_thermostat_that_does_not_take_the_setpoint_is_paused(rig: Rig) -> None:
    rig.fake.trv_silent.add(TRV_OFFICE)
    await rig.gate.submit(office(22.0))
    rig.clock.advance(minutes=5)
    rig.gate.expire()
    assert rig.gate.expected(TRV_OFFICE) is None
    rig.clock.advance(minutes=10)
    await rig.gate.submit(office(22.0))
    rig.clock.advance(minutes=5)
    rig.gate.expire()

    problems = await messages(rig.log, problems=True)
    assert sum(text.startswith("Nicht bestätigt nach 5 min: Thermostat Büro") for text in problems) == 2
    assert any("übernimmt die Sollwerte nicht" in text for text in problems)
    rig.clock.advance(minutes=20)
    assert (await rig.gate.submit(office(22.5))).held is Held.PAUSED
    assert (await rig.gate.submit(office(5.0, RoomReason.WINDOW))).sent


async def test_a_refused_call_does_not_count(rig: Rig) -> None:
    rig.fake.service_status["climate.set_temperature"] = 400

    with pytest.raises(WriteNotExecuted):
        await rig.gate.submit(office(22.0))

    assert rig.gate.commands(TRV_OFFICE).today == 0
    assert not rig.gate.sending(TRV_OFFICE)


async def test_an_unknown_outcome_is_followed(rig: Rig) -> None:
    rig.fake.service_status["climate.set_temperature"] = 500

    with pytest.raises(WriteOutcomeUnknown):
        await rig.gate.submit(office(22.0))

    assert rig.gate.commands(TRV_OFFICE).today == 1
    assert rig.gate.sending(TRV_OFFICE)


async def test_the_fallback_works_when_the_thermostat_state_is_gone(rig: Rig) -> None:
    # The thermostat does not answer, so no new state comes in meanwhile.
    rig.fake.trv_silent.add(TRV_OFFICE)
    await rig.gate.submit(office(22.0))
    rig.home.hems_sources.ha_disconnected()
    rig.clock.advance(seconds=10)

    assert (await rig.gate.submit(office(20.0, RoomReason.FALLBACK))).sent
    with pytest.raises(WriteRejected):
        await rig.gate.submit(office(5.0, RoomReason.WINDOW))


# --- structure ------------------------------------------------------------------------------------


def _attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None} | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }


def test_only_the_write_gate_sets_a_thermostat() -> None:
    package = Path(ha_client_module.__file__).parent
    callers = {
        source.name: used for source in package.rglob("*.py") if (used := _attributes(source) & {"call_room_service"})
    }

    assert callers == {"write_gate.py": {"call_room_service"}}


def test_only_the_client_calls_a_service_directly() -> None:
    package = Path(ha_client_module.__file__).parent
    callers = {source.name for source in package.rglob("*.py") if "_call_service" in _attributes(source)}

    assert callers == {"ha_client.py"}


def test_the_room_service_is_only_the_setpoint() -> None:
    assert set(ROOM_SERVICES) == {("climate", "set_temperature")}
    fields = ha_client_module.ROOM_SERVICE_FIELDS
    assert fields == {"temperature"}


async def test_the_room_service_refuses_anything_else(fake_ha: FakeHomeAssistant) -> None:
    async with HomeAssistantClient(fake_ha.url, TOKEN) as client:
        for domain, service, entity, data in (
            ("climate", "set_hvac_mode", TRV_OFFICE, {"hvac_mode": "off"}),
            ("select", "select_option", "select.0x00158d0000000a01_sensor", {"option": "external"}),
            ("climate", "set_temperature", "sensor.office_temperature", {"temperature": 20}),
            ("climate", "set_temperature", TRV_OFFICE, {"temperature": 20, "hvac_mode": "heat"}),
            ("climate", "set_temperature", TRV_OFFICE, {"temperature": float("nan")}),
        ):
            with pytest.raises(ValueError):
                await client.call_room_service(domain, service, entity, data)
    assert fake_ha.service_calls == []


def test_the_room_control_reaches_home_assistant_only_through_the_write_gate() -> None:
    package = Path(rooms_package.__file__).parent
    forbidden = {"call_room_service", "call_device_service", "set_state", "remove_state", "send_command"}
    for source in package.rglob("*.py"):
        assert not _attributes(source) & forbidden, source.name
        assert not any("bridge_client" in name for name in _imports(source)), source.name


def test_the_energy_management_still_has_no_way_to_write() -> None:
    package = Path(hems_package.__file__).parent
    for source in package.rglob("*.py"):
        imported = _imports(source)
        assert not any(
            "write_gate" in name or "bridge_client" in name or name.startswith("vaillant_eebus.rooms")
            for name in imported
        ), source.name
