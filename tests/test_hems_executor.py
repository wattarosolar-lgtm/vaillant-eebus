"""Carrying out the plan through the write gate, against the fake bridge."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from vaillant_eebus import hems
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.hems import executor as executor_module
from vaillant_eebus.hems.config import PlannerMode
from vaillant_eebus.hems.executor import Executor
from vaillant_eebus.hems.planner import DHW, Action, Block, Plan
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import OperatingMode, SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import WriteGate

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
# Zone 2 of the fake runs in "on" at 23 °C, like the real installation; Zone 1 in auto.
ZONE = "6.1.1"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.now.timestamp()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def connected(pairing: PairingService, fake_bridge: FakeBridge, state: StateService) -> StateService:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)
    await state.refresh(GATEWAY_SKI)
    return state


@pytest.fixture
def live_gate(
    bridge_client: BridgeClient,
    pairing: PairingService,
    connected: StateService,
    settings_store: SettingsStore,
    log_store: LogStore,
    clock: Clock,
) -> WriteGate:
    gate = WriteGate(bridge_client, pairing, connected, settings_store, log_store, clock=clock.monotonic)
    gate.set_mode(OperatingMode.LIVE, confirm=True)
    return gate


def make_executor(
    gate: WriteGate, state: StateService, settings: SettingsStore, log: LogStore, data_dir: Path, clock: Clock
) -> Executor:
    return Executor(gate, state, settings, log, data_dir, clock=clock)


@pytest.fixture
def executor(
    live_gate: WriteGate,
    connected: StateService,
    settings_store: SettingsStore,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> Executor:
    return make_executor(live_gate, connected, settings_store, log_store, tmp_path, clock)


def planner_mode(settings: SettingsStore, mode: PlannerMode) -> None:
    settings.update(lambda current: replace(current, hems=replace(current.hems, mode=mode)))


def block(
    control: str, action: Action, *, hours: float = 1.0, shift: float | None = None, target: float | None = None
) -> Block:
    return Block(
        control=control,
        action=action,
        start=NOW,
        end=NOW + timedelta(hours=hours),
        shift_k=shift,
        target_c=target,
        reason="Test",
    )


def plan(*blocks: Block) -> Plan:
    return Plan(created_at=NOW, slots=[], blocks=list(blocks))


async def step(executor: Executor, state: StateService, current: Plan | None) -> None:
    """One round as the service runs it: read the device, then carry the plan out."""
    await state.refresh(GATEWAY_SKI)
    await executor.step(current)
    await state.refresh(GATEWAY_SKI)


def sent(fake_bridge: FakeBridge) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []
    for name, request in fake_bridge.device.write_calls:
        value = getattr(request, "active", None) if name == "SetDHWBoost" else getattr(request, "value_celsius", None)
        calls.append((name, value))
    return calls


async def messages(log: LogStore, category: LogCategory | None = None) -> list[str]:
    return [entry.message for entry in reversed((await log.page(category=category, limit=200)).entries)]


async def test_control_sends_the_shift_and_returns_to_the_base_value(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    log_store: LogStore,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)
    current = plan(block(ZONE, Action.RAISE, shift=1.0))

    await step(executor, connected, current)
    clock.now += timedelta(hours=1)
    await step(executor, connected, current)

    assert sent(fake_bridge) == [("SetRoomHeatingTemperature", 24.0), ("SetRoomHeatingTemperature", 23.0)]
    assert executor.bases() == {"5.1.1": 21.0, ZONE: 23.0, DHW: 55.0}
    assert executor.shifted() == {}
    assert "Gesendet: Zone 2: Solltemperatur 24,0 °C (Planer)" in await messages(log_store, LogCategory.WRITE)


async def test_preview_sends_nothing_even_in_live_mode(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    log_store: LogStore,
) -> None:
    planner_mode(settings_store, PlannerMode.PREVIEW)

    await step(executor, connected, plan(block(ZONE, Action.RAISE, shift=1.0)))

    assert fake_bridge.device.write_calls == []
    assert await messages(log_store, LogCategory.HEMS) == ["Planer würde senden: Zone 2: Solltemperatur 24,0 °C"]


async def test_in_demo_mode_control_only_logs_what_it_would_send(
    bridge_client: BridgeClient,
    pairing: PairingService,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    gate = WriteGate(bridge_client, pairing, connected, settings_store, log_store, clock=clock.monotonic)
    executor = make_executor(gate, connected, settings_store, log_store, tmp_path, clock)
    planner_mode(settings_store, PlannerMode.CONTROL)
    current = plan(block(ZONE, Action.RAISE, shift=1.0))

    await step(executor, connected, current)
    await step(executor, connected, current)

    assert fake_bridge.device.write_calls == []
    assert (await messages(log_store, LogCategory.WRITE)).count(
        "Würde senden: Zone 2: Solltemperatur 24,0 °C (Planer)"
    ) == 1


async def test_a_control_changes_at_most_every_30_minutes(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)
    await step(executor, connected, plan(block(ZONE, Action.RAISE, shift=1.0)))
    lowered = Block(ZONE, Action.LOWER, NOW + timedelta(minutes=10), NOW + timedelta(hours=2), -1.0, None, "Test")

    clock.now += timedelta(minutes=10)
    await step(executor, connected, plan(lowered))
    early = sent(fake_bridge)
    clock.now += timedelta(minutes=20)
    await step(executor, connected, plan(lowered))

    assert early == [("SetRoomHeatingTemperature", 24.0)]
    assert sent(fake_bridge)[-1] == ("SetRoomHeatingTemperature", 22.0)


async def test_a_setpoint_changed_by_hand_becomes_the_new_base_value(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    log_store: LogStore,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)
    current = plan(block(ZONE, Action.RAISE, shift=1.0, hours=2))
    await step(executor, connected, current)

    # Changed in myVAILLANT while the block runs.
    fake_bridge.device.zones[ZONE].setpoint = 21.5
    clock.now += timedelta(hours=1)
    await step(executor, connected, current)
    clock.now += timedelta(hours=2)
    await step(executor, connected, current)

    assert sent(fake_bridge) == [("SetRoomHeatingTemperature", 24.0)]
    assert executor.bases()[ZONE] == 21.5
    assert "Planer: neuer Grundwert Zone 2: 21,5 °C (von Hand geändert)." in await messages(log_store, LogCategory.HEMS)


async def test_release_returns_every_shifted_control_at_once(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)
    await step(executor, connected, plan(block(ZONE, Action.RAISE, shift=1.0)))

    clock.now += timedelta(seconds=10)
    await executor.release("Test")

    assert sent(fake_bridge) == [("SetRoomHeatingTemperature", 24.0), ("SetRoomHeatingTemperature", 23.0)]


async def test_switching_off_returns_to_the_base_value(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)
    current = plan(block(ZONE, Action.RAISE, shift=1.0))
    await step(executor, connected, current)

    planner_mode(settings_store, PlannerMode.OFF)
    clock.now += timedelta(seconds=10)
    await step(executor, connected, current)

    assert sent(fake_bridge)[-1] == ("SetRoomHeatingTemperature", 23.0)


async def test_after_a_restart_a_shift_the_plan_no_longer_wants_is_returned(
    live_gate: WriteGate,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)
    first = make_executor(live_gate, connected, settings_store, log_store, tmp_path, clock)
    await step(first, connected, plan(block(ZONE, Action.RAISE, shift=1.0)))

    # The app crashed; the new one has no plan yet.
    second = make_executor(live_gate, connected, settings_store, log_store, tmp_path, clock)
    clock.now += timedelta(seconds=10)
    await step(second, connected, plan())

    assert sent(fake_bridge)[-1] == ("SetRoomHeatingTemperature", 23.0)
    assert second.bases()[ZONE] == 23.0


async def test_a_hot_water_block_raises_the_setpoint_then_starts_the_charge(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)
    current = plan(block(DHW, Action.CHARGE, target=60.0, hours=1.5))

    await step(executor, connected, current)
    await step(executor, connected, current)
    clock.now += timedelta(hours=1.5)
    await step(executor, connected, current)

    assert sent(fake_bridge) == [("SetDHWSetpoint", 60.0), ("SetDHWBoost", True), ("SetDHWSetpoint", 55.0)]
    assert executor.charged_days(preview=False) == {NOW.date()}


async def test_switching_from_preview_to_control_in_a_hot_water_block_still_starts_the_charge(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    clock: Clock,
) -> None:
    planner_mode(settings_store, PlannerMode.PREVIEW)
    current = plan(block(DHW, Action.CHARGE, target=60.0, hours=1.5))
    await step(executor, connected, current)

    clock.now += timedelta(minutes=5)
    planner_mode(settings_store, PlannerMode.CONTROL)
    await step(executor, connected, current)
    # Back and forth within the same block: the charge is not given twice.
    clock.now += timedelta(minutes=5)
    planner_mode(settings_store, PlannerMode.PREVIEW)
    await step(executor, connected, current)
    clock.now += timedelta(minutes=35)
    planner_mode(settings_store, PlannerMode.CONTROL)
    await step(executor, connected, current)

    assert sent(fake_bridge) == [
        ("SetDHWSetpoint", 60.0),
        ("SetDHWBoost", True),
        ("SetDHWSetpoint", 55.0),
        ("SetDHWSetpoint", 60.0),
    ]
    assert executor.charged_days(preview=False) == {NOW.date()}


@pytest.mark.parametrize("mode", list(PlannerMode))
async def test_a_setpoint_changed_while_the_app_was_stopped_becomes_the_new_base_value(
    mode: PlannerMode,
    live_gate: WriteGate,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    planner_mode(settings_store, mode)
    first = make_executor(live_gate, connected, settings_store, log_store, tmp_path, clock)
    await step(first, connected, plan())

    # Changed in myVAILLANT while the app was stopped.
    fake_bridge.device.zones[ZONE].setpoint = 21.5
    second = make_executor(live_gate, connected, settings_store, log_store, tmp_path, clock)
    clock.now += timedelta(minutes=10)
    await step(second, connected, plan())
    planner_mode(settings_store, PlannerMode.CONTROL)
    await step(second, connected, plan())

    assert fake_bridge.device.write_calls == []
    assert second.bases()[ZONE] == 21.5
    noticed = "Planer: neuer Grundwert Zone 2: 21,5 °C (von Hand geändert)." in await messages(log_store)
    assert noticed is (mode is PlannerMode.CONTROL)


async def test_a_value_set_by_hand_that_the_planner_only_would_have_sent_becomes_the_base_value(
    bridge_client: BridgeClient,
    pairing: PairingService,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    # Demo mode: the planner decides on 24 °C but never sends it.
    gate = WriteGate(bridge_client, pairing, connected, settings_store, log_store, clock=clock.monotonic)
    executor = make_executor(gate, connected, settings_store, log_store, tmp_path, clock)
    planner_mode(settings_store, PlannerMode.CONTROL)
    current = plan(block(ZONE, Action.RAISE, shift=1.0, hours=2))
    await step(executor, connected, current)

    fake_bridge.device.zones[ZONE].setpoint = 24.0
    clock.now += timedelta(minutes=10)
    await step(executor, connected, current)

    assert executor.bases()[ZONE] == 24.0
    assert "Planer: neuer Grundwert Zone 2: 24,0 °C (von Hand geändert)." in await messages(log_store, LogCategory.HEMS)


async def test_a_return_to_the_base_value_held_back_by_the_gate_is_tried_again(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor_module, "URGENT_WAIT_S", 0.0)
    planner_mode(settings_store, PlannerMode.CONTROL)
    await step(executor, connected, plan(block(ZONE, Action.RAISE, shift=1.0)))

    # Within the gate's 5 seconds per control: 429.
    await executor.release("Test")
    held_back = executor.shifted()
    clock.now += timedelta(seconds=10)
    await executor.release("Test")

    assert held_back == {ZONE: 24.0}
    assert sent(fake_bridge) == [("SetRoomHeatingTemperature", 24.0), ("SetRoomHeatingTemperature", 23.0)]
    assert executor.shifted() == {}


async def test_zones_outside_operation_mode_on_are_not_touched(
    executor: Executor,
    connected: StateService,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
) -> None:
    planner_mode(settings_store, PlannerMode.CONTROL)

    await step(executor, connected, plan(block("5.1.1", Action.RAISE, shift=1.0)))

    assert fake_bridge.device.write_calls == []


def test_the_planner_only_ever_gives_setpoints_and_the_one_time_charge() -> None:
    forbidden = {"SetZoneMode", "SetHotWaterMode", "SetConsumptionLimit", "SetFailsafeLimit", "ControlCompressor"}
    used: set[str] = set()
    for source in Path(hems.__file__).parent.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        used |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}

    assert used & forbidden == set()
    assert {"SetZoneSetpoint", "SetHotWaterSetpoint", "SetHotWaterBoost"} <= used
