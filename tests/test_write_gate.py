from __future__ import annotations

import ast
import asyncio
import contextlib
from collections.abc import AsyncIterator, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import grpc
import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from tests.support import EVENTUALLY_TIMEOUT, eventually
from vaillant_eebus import write_gate
from vaillant_eebus.bridge_client import WRITE_METHODS, BridgeClient
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.pairing import NotPairedError, PairingService
from vaillant_eebus.settings import OperatingMode, SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import (
    Command,
    Control,
    ControlCompressor,
    SetConsumptionLimit,
    SetFailsafeLimit,
    SetHotWaterBoost,
    SetHotWaterMode,
    SetHotWaterSetpoint,
    SetZoneMode,
    SetZoneSetpoint,
    WriteGate,
    WriteRejected,
)

EVERY_COMMAND: list[Command] = [
    SetZoneSetpoint(zone_id="6.1.1", celsius=22.5),
    SetZoneMode(zone_id="5.1.1", mode="on"),
    SetHotWaterSetpoint(celsius=54),
    SetHotWaterMode(mode="off"),
    SetHotWaterBoost(active=True),
    SetConsumptionLimit(watts=4200, duration_s=3600, active=True),
    SetFailsafeLimit(watts=4200, duration_s=7200),
    ControlCompressor(action="schedule"),
]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def connected(pairing: PairingService, fake_bridge: FakeBridge, state: StateService) -> StateService:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)
    await state.refresh(GATEWAY_SKI)
    return state


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def live_gate(
    bridge_client: BridgeClient,
    pairing: PairingService,
    connected: StateService,
    settings_store: SettingsStore,
    log_store: LogStore,
    clock: FakeClock,
) -> WriteGate:
    gate = WriteGate(bridge_client, pairing, connected, settings_store, log_store, clock=clock)
    gate.set_mode(OperatingMode.LIVE, confirm=True)
    return gate


@contextlib.asynccontextmanager
async def running(work: Coroutine[Any, Any, None]) -> AsyncIterator[None]:
    task = asyncio.create_task(work)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def messages(log: LogStore, category: LogCategory | None = None) -> list[str]:
    """Protocol messages, oldest first."""
    return [entry.message for entry in reversed((await log.page(category=category, limit=200)).entries)]


async def logged(log: LogStore, category: LogCategory, count: int) -> list[str]:
    """Wait until the protocol holds count messages of the category."""
    async with asyncio.timeout(EVENTUALLY_TIMEOUT):
        while len(entries := await messages(log, category)) < count:  # noqa: ASYNC110 - polls the database
            await asyncio.sleep(0.01)
    return entries


# --- demo and live ---------------------------------------------------------------


@pytest.mark.usefixtures("connected")
async def test_demo_mode_sends_nothing_to_the_heat_pump(
    gate: WriteGate, fake_bridge: FakeBridge, log_store: LogStore
) -> None:
    assert gate.mode is OperatingMode.DEMO

    results = [await gate.submit(command) for command in EVERY_COMMAND]

    assert fake_bridge.device.write_calls == []
    assert all(not result.sent and result.mode is OperatingMode.DEMO for result in results)
    assert results[0].would_send == {"control": "zone_setpoint", "zone_id": "6.1.1", "value": 22.5}
    assert results[0].message == "Würde senden: Zone 2: Solltemperatur 22,5 °C"
    # Power limits and compressor control are blocked, not previewed.
    assert [result.would_send for result in results[5:]] == [None, None, None]
    assert all(result.message.startswith("Im Demo-Betrieb gesperrt: ") for result in results[5:])
    assert await messages(log_store, LogCategory.WRITE) == [result.message for result in results]


async def test_live_mode_needs_an_explicit_confirmation(gate: WriteGate, settings_store: SettingsStore) -> None:
    with pytest.raises(WriteRejected) as rejected:
        gate.set_mode(OperatingMode.LIVE, confirm=False)

    assert rejected.value.status == 400
    assert gate.mode is OperatingMode.DEMO
    assert settings_store.load().operating_mode is OperatingMode.DEMO


async def test_mode_changes_are_saved_and_logged(
    gate: WriteGate, settings_store: SettingsStore, log_store: LogStore
) -> None:
    gate.set_mode(OperatingMode.LIVE, confirm=True)
    saved = settings_store.load().operating_mode
    gate.set_mode(OperatingMode.DEMO, confirm=False)

    assert saved is OperatingMode.LIVE
    assert settings_store.load().operating_mode is OperatingMode.DEMO
    assert await messages(log_store, LogCategory.MODE) == [
        "Live-Betrieb eingeschaltet: Änderungen werden an die Wärmepumpe gesendet.",
        "Demo-Betrieb eingeschaltet: Änderungen werden nicht gesendet.",
    ]


async def test_the_mode_is_logged_at_start(gate: WriteGate, log_store: LogStore) -> None:
    async with running(gate.run()):
        entries = await logged(log_store, LogCategory.MODE, 1)

    assert entries == ["App gestartet im Demo-Betrieb."]


async def test_live_setpoint_goes_to_the_chosen_zone_only(live_gate: WriteGate, fake_bridge: FakeBridge) -> None:
    result = await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22.5))

    assert result.sent and result.message == "Gesendet: Zone 2: Solltemperatur 22,5 °C"
    ((name, request),) = fake_bridge.device.write_calls
    assert name == "SetRoomHeatingTemperature"
    assert (request.zone_id, request.value_celsius) == ("6.1.1", 22.5)  # type: ignore[attr-defined]
    assert (fake_bridge.device.zones["5.1.1"].setpoint, fake_bridge.device.zones["6.1.1"].setpoint) == (21, 22.5)


async def test_every_control_reaches_the_bridge_in_live_mode(live_gate: WriteGate, fake_bridge: FakeBridge) -> None:
    for command in EVERY_COMMAND:
        assert (await live_gate.submit(command)).sent

    assert [name for name, _request in fake_bridge.device.write_calls] == [
        "SetRoomHeatingTemperature",
        "SetRoomHeatingMode",
        "SetDHWSetpoint",
        "SetDHWOperationMode",
        "SetDHWBoost",
        "WriteConsumptionLimit",
        "WriteFailsafeLimit",
        "ControlCompressorFlexibility",
    ]
    limit = fake_bridge.device.consumption_limit
    assert (limit.value_watts, limit.duration_seconds, limit.is_active) == (4200, 3600, True)
    assert fake_bridge.device.failsafe_limit.duration_minimum_seconds == 7200


# --- confirmation ------------------------------------------------------------------


async def test_a_sent_value_is_pending_until_the_device_reports_it(
    live_gate: WriteGate, connected: StateService, log_store: LogStore
) -> None:
    async with running(live_gate.run()):
        await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22.5))
        pending = live_gate.pending
        await connected.refresh(GATEWAY_SKI)
        await eventually(lambda: not live_gate.pending)

    assert [(write.control, write.zone_id, write.value) for write in pending] == [
        (Control.ZONE_SETPOINT, "6.1.1", 22.5)
    ]
    assert (await messages(log_store, LogCategory.WRITE))[-1] == "Bestätigt vom Gerät: Zone 2: Solltemperatur 22,5 °C"


async def test_a_value_the_device_does_not_report_is_not_confirmed(
    live_gate: WriteGate, connected: StateService, fake_bridge: FakeBridge, log_store: LogStore, clock: FakeClock
) -> None:
    fake_bridge.device.apply_writes = False
    async with running(live_gate.run()):
        await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22.5))
        await connected.refresh(GATEWAY_SKI)
        assert live_gate.pending
        clock.now += 30
        connected.republish()
        await eventually(lambda: not live_gate.pending)

    assert (await messages(log_store, LogCategory.WRITE))[-1] == (
        "Nicht bestätigt nach 30 s: Zone 2: Solltemperatur 22,5 °C; das Gerät meldet weiterhin Solltemperatur 23,0 °C"
    )


async def test_changes_made_outside_the_app_are_logged(
    gate: WriteGate, connected: StateService, fake_bridge: FakeBridge, log_store: LogStore
) -> None:
    async with running(gate.run()):
        await logged(log_store, LogCategory.MODE, 1)
        # The first state the gate sees is the starting point.
        connected.republish()
        fake_bridge.device.zones["6.1.1"].setpoint = 21.5
        fake_bridge.device.dhw_mode = "off"
        await connected.refresh(GATEWAY_SKI)
        entries = await logged(log_store, LogCategory.DEVICE, 2)

    assert entries == [
        "Am Gerät geändert: Zone 2: Solltemperatur 21,5 °C (vorher Solltemperatur 23,0 °C)",
        "Am Gerät geändert: Warmwasser: Betriebsart Aus (vorher Betriebsart Auto)",
    ]


# --- protection rules ---------------------------------------------------------------


@pytest.mark.usefixtures("connected")
@pytest.mark.parametrize(
    ("command", "status", "reason"),
    [
        (SetZoneSetpoint(zone_id="6.1.1", celsius=30.5), 422, "zwischen 5,0 °C und 30,0 °C"),
        (SetZoneSetpoint(zone_id="6.1.1", celsius=22.3), 422, "in Schritten von 0,5 °C"),
        (SetZoneSetpoint(zone_id="9.9.9", celsius=21), 404, "Heizzone 9.9.9 ist dem Gerät nicht bekannt"),
        (SetZoneMode(zone_id="5.1.1", mode="eco"), 422, "„eco“ bietet das Gerät nicht an"),
        (SetHotWaterSetpoint(celsius=71), 422, "zwischen 35 °C und 70 °C"),
    ],
)
async def test_invalid_commands_are_rejected_in_both_modes(
    gate: WriteGate, fake_bridge: FakeBridge, log_store: LogStore, command: Command, status: int, reason: str
) -> None:
    for mode in (OperatingMode.DEMO, OperatingMode.LIVE):
        gate.set_mode(mode, confirm=True)
        with pytest.raises(WriteRejected) as rejected:
            await gate.submit(command)
        assert rejected.value.status == status
        assert reason in rejected.value.message

    assert fake_bridge.device.write_calls == []
    assert (await messages(log_store, LogCategory.WRITE))[-1].startswith("Abgelehnt: ")


async def test_settings_the_device_does_not_allow_are_rejected(
    gate: WriteGate, connected: StateService, fake_bridge: FakeBridge
) -> None:
    fake_bridge.device.zones["6.1.1"].setpoint_writable = False
    fake_bridge.device.zones["6.1.1"].mode_writable = False
    await connected.refresh(GATEWAY_SKI)

    with pytest.raises(WriteRejected) as setpoint:
        await gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22))
    with pytest.raises(WriteRejected) as mode:
        await gate.submit(SetZoneMode(zone_id="6.1.1", mode="on"))

    assert (setpoint.value.status, mode.value.status) == (409, 409)
    assert "nicht änderbar" in setpoint.value.message


async def test_nothing_is_sent_while_the_gateway_is_disconnected(
    live_gate: WriteGate, connected: StateService, fake_bridge: FakeBridge
) -> None:
    fake_bridge.device.disconnect(GATEWAY_SKI)
    await connected.refresh(GATEWAY_SKI)

    with pytest.raises(WriteRejected) as rejected:
        await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22))

    assert rejected.value.status == 409
    assert fake_bridge.device.write_calls == []


async def test_nothing_is_sent_without_a_paired_gateway(gate: WriteGate) -> None:
    with pytest.raises(NotPairedError):
        await gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22))


async def test_one_write_per_control_within_five_seconds(
    live_gate: WriteGate, fake_bridge: FakeBridge, clock: FakeClock
) -> None:
    await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22.5))

    with pytest.raises(WriteRejected) as rejected:
        await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22))
    other_zone = await live_gate.submit(SetZoneSetpoint(zone_id="5.1.1", celsius=20))
    clock.now += 5
    later = await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22))

    assert (rejected.value.status, rejected.value.retry_after) == (429, 5)
    assert other_zone.sent and later.sent
    assert len(fake_bridge.device.write_calls) == 3


async def test_values_already_set_are_not_sent(live_gate: WriteGate, fake_bridge: FakeBridge, clock: FakeClock) -> None:
    unchanged = await live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=23))
    await live_gate.submit(SetHotWaterMode(mode="off"))
    clock.now += 10
    # Sent but not reported yet: the same value is not sent twice.
    pending = await live_gate.submit(SetHotWaterMode(mode="off"))

    assert not unchanged.sent and unchanged.message.startswith("Nicht gesendet, bereits eingestellt: ")
    assert not pending.sent
    assert [name for name, _request in fake_bridge.device.write_calls] == ["SetDHWOperationMode"]


async def test_simultaneous_requests_for_one_control_send_once(live_gate: WriteGate, fake_bridge: FakeBridge) -> None:
    results = await asyncio.gather(
        live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22.5)),
        live_gate.submit(SetZoneSetpoint(zone_id="6.1.1", celsius=22)),
        return_exceptions=True,
    )

    assert len(fake_bridge.device.write_calls) == 1
    assert sum(isinstance(result, WriteRejected) and result.status == 429 for result in results) == 1


@pytest.mark.parametrize(
    ("code", "logged"),
    [
        (grpc.StatusCode.UNAVAILABLE, "Fehler beim Senden: Warmwasser: Solltemperatur 54 °C (Fehlercode UNAVAILABLE)"),
        (
            grpc.StatusCode.DEADLINE_EXCEEDED,
            "Ergebnis unbekannt (keine Antwort des Geräts): Warmwasser: Solltemperatur 54 °C",
        ),
    ],
)
async def test_failed_writes_are_logged_and_reported(
    live_gate: WriteGate, fake_bridge: FakeBridge, log_store: LogStore, code: grpc.StatusCode, logged: str
) -> None:
    fake_bridge.device.fail_writes_with = code

    with pytest.raises(grpc.aio.AioRpcError) as failed:
        await live_gate.submit(SetHotWaterSetpoint(celsius=54))

    assert failed.value.code() is code
    assert live_gate.pending == []
    assert (await messages(log_store, LogCategory.WRITE))[-1] == logged


@pytest.mark.usefixtures("connected")
async def test_a_start_time_belongs_to_scheduling_only(gate: WriteGate) -> None:
    with pytest.raises(WriteRejected) as rejected:
        await gate.submit(ControlCompressor(action="pause", start_time=datetime(2026, 10, 20, 12, tzinfo=UTC)))

    assert rejected.value.status == 422


def test_only_the_write_gate_calls_the_bridges_write_methods() -> None:
    package = Path(write_gate.__file__).parent
    callers: dict[str, set[str]] = {}
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in WRITE_METHODS}
        if used:
            callers[source.name] = used

    assert callers == {"write_gate.py": set(WRITE_METHODS)}
