from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from tests.support import eventually
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.state import HeatPumpState, StateService, state_from_snapshot


@pytest.fixture
async def paired(pairing: PairingService, fake_bridge: FakeBridge) -> PairingService:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)
    return pairing


@contextlib.asynccontextmanager
async def running(service: StateService) -> AsyncIterator[None]:
    task = asyncio.create_task(service.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_snapshot_maps_to_state_without_inventing_values(fake_bridge: FakeBridge) -> None:
    fake_bridge.device.registered.add(GATEWAY_SKI)
    fake_bridge.device.connected.add(GATEWAY_SKI)

    state = state_from_snapshot(fake_bridge.device.snapshot(GATEWAY_SKI))

    assert state.available and state.connected
    assert state.heartbeat_ok is True
    assert state.operating_state == "normalOperation"
    assert state.compressor_active is True
    assert state.measurements["power_consumption"].value == 532
    assert state.measurements["outdoor_temperature"].value == 13.5
    # Not reported by the device: never filled with substitutes.
    assert "flow_temperature" not in state.measurements
    assert "energy_consumed" not in state.measurements
    [zone] = state.zones
    assert (zone.id, zone.label, zone.room_temperature) == ("1", "Zone 1", None)
    assert zone.setpoint is not None
    assert (zone.setpoint.value, zone.setpoint.min, zone.setpoint.max, zone.setpoint.step) == (21, 5, 30, 0.5)
    assert (zone.mode, zone.available_modes) == ("auto", ["auto", "on", "off"])
    assert state.hot_water is not None
    assert state.hot_water.temperature is not None and state.hot_water.temperature.value == 48
    assert state.hot_water.setpoint is not None and state.hot_water.setpoint.value == 55
    assert (state.hot_water.mode, state.hot_water.boost) == ("auto", "inactive")


def test_room_temperature_is_used_when_the_device_reports_it(fake_bridge: FakeBridge) -> None:
    fake_bridge.device.connected.add(GATEWAY_SKI)
    fake_bridge.device.room_temperature = 21.5

    [zone] = state_from_snapshot(fake_bridge.device.snapshot(GATEWAY_SKI)).zones

    assert zone.room_temperature is not None and zone.room_temperature.value == 21.5


def test_compressor_counts_as_off_at_low_power(fake_bridge: FakeBridge) -> None:
    fake_bridge.device.connected.add(GATEWAY_SKI)
    fake_bridge.device.measurements["compressor_power"] = (40.0, "W")

    assert state_from_snapshot(fake_bridge.device.snapshot(GATEWAY_SKI)).compressor_active is False


def test_disconnected_snapshot_has_no_values(fake_bridge: FakeBridge) -> None:
    state = state_from_snapshot(fake_bridge.device.snapshot(GATEWAY_SKI))

    assert state.available and not state.connected
    assert (state.measurements, state.zones, state.hot_water) == ({}, [], None)
    assert state.compressor_active is None


async def test_service_reads_the_snapshot_periodically(
    bridge_client: BridgeClient, paired: PairingService, fake_bridge: FakeBridge
) -> None:
    service = StateService(bridge_client, paired, refresh_interval=0.1, debounce=0.01)
    async with running(service):
        await eventually(lambda: fake_bridge.device.snapshot_calls >= 4)

    assert service.current is not None and service.current.connected


async def test_service_refreshes_after_a_state_event(
    state: StateService, paired: PairingService, fake_bridge: FakeBridge
) -> None:
    async with running(state):
        await eventually(lambda: fake_bridge.device.state_subscriber_count == 1)
        await eventually(lambda: state.current is not None)
        fake_bridge.device.measurements["outdoor_temperature"] = (9.0, "degC")
        fake_bridge.device.push_state_event(GATEWAY_SKI)

        await eventually(
            lambda: state.current is not None and state.current.measurements["outdoor_temperature"].value == 9.0
        )


async def test_listeners_receive_new_states(
    state: StateService, paired: PairingService, fake_bridge: FakeBridge
) -> None:
    async with state.listen() as updates, running(state):
        first = await asyncio.wait_for(updates.get(), timeout=5)
        assert isinstance(first, HeatPumpState) and first.connected

        fake_bridge.device.disconnect(GATEWAY_SKI)
        fake_bridge.device.push_state_event(GATEWAY_SKI)
        second = await asyncio.wait_for(updates.get(), timeout=5)

    assert isinstance(second, HeatPumpState) and not second.connected


async def test_service_reconnects_after_the_stream_ends(
    state: StateService, paired: PairingService, fake_bridge: FakeBridge
) -> None:
    async with running(state):
        await eventually(lambda: fake_bridge.device.state_subscriber_count == 1)
        fake_bridge.device.restart()
        await eventually(lambda: state.current is not None and not state.current.available)

        # What the pairing service does after a bridge restart.
        await paired.pair(GATEWAY_SKI)
        fake_bridge.device.connect(GATEWAY_SKI)

        await eventually(lambda: fake_bridge.device.state_subscriber_count == 1)
        await eventually(lambda: state.current is not None and state.current.connected)


async def test_service_has_no_state_without_pairing(state: StateService, fake_bridge: FakeBridge) -> None:
    async with running(state):
        await asyncio.sleep(0.2)

    assert state.current is None
    assert fake_bridge.device.snapshot_calls == 0
