"""Fake EEBUS bridge for development and tests without a heat pump.

A gRPC server built from the same proto files as the real bridge. It serves the
RPCs the backend uses: server info, gateway discovery, pairing, device status,
device events, snapshot, structure dump and the writes of the HVAC, DHW, LPC
and OHPCF services. Writes are checked like the real bridge does, recorded in
FakeDeviceService.write_calls and applied to the served device state. Every
other RPC answers UNIMPLEMENTED (behaviour of the generated base class).

Standalone, e.g. to click through the setup wizard without hardware (the fake
gateway "accepts" the pairing a few seconds after it was registered):
    PYTHONPATH=backend/gen python -m tests.fake_bridge --port 50471
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import grpc
from eebus.v1.common_pb2 import DeviceRequest, Empty, LoadLimit, MeasurementEntry
from eebus.v1.device_service_pb2 import (
    DEVICE_EVENT_CONNECTED,
    DEVICE_EVENT_DISCONNECTED,
    DEVICE_EVENT_TRUST_REMOVED,
    DEVICE_READINESS_DISCONNECTED,
    DEVICE_READINESS_READY,
    RESYNC_REASON_INITIAL_STATE_REQUIRED,
    SNAPSHOT_VALUE_STATE_AVAILABLE,
    DeviceEvent,
    DeviceEventType,
    DeviceReadinessState,
    DeviceSnapshot,
    DeviceStateEvent,
    DeviceStatus,
    DeviceStructureDump,
    DiscoveredDevice,
    ListDevicesResponse,
    ListPairedDevicesResponse,
    PairedDevice,
    RegisterSKIRequest,
    ResyncRequired,
    ServerInfo,
)
from eebus.v1.device_service_pb2_grpc import DeviceServiceServicer, add_DeviceServiceServicer_to_server
from eebus.v1.dhw_service_pb2 import (
    DHW_BOOST_STATUS_ACTIVE,
    DHW_BOOST_STATUS_INACTIVE,
    DHWBoostStatus,
    DHWSetpoint,
    DHWSystemFunctionState,
    SetDHWBoostRequest,
    SetDHWOperationModeRequest,
    SetDHWSetpointRequest,
)
from eebus.v1.dhw_service_pb2_grpc import DHWServiceServicer, add_DHWServiceServicer_to_server
from eebus.v1.hvac_service_pb2 import (
    RoomHeatingSetpoint,
    RoomHeatingState,
    RoomHeatingSystemFunction,
    RoomHeatingZone,
    SetRoomHeatingModeRequest,
    SetRoomHeatingTemperatureRequest,
)
from eebus.v1.hvac_service_pb2_grpc import HVACServiceServicer, add_HVACServiceServicer_to_server
from eebus.v1.lpc_service_pb2 import (
    FailsafeLimit,
    HeartbeatStatus,
    WriteFailsafeLimitRequest,
    WriteLoadLimitRequest,
)
from eebus.v1.lpc_service_pb2_grpc import LPCServiceServicer, add_LPCServiceServicer_to_server
from eebus.v1.monitoring_service_pb2 import DeviceDiagnosticsData
from eebus.v1.ohpcf_service_pb2 import (
    COMPRESSOR_STATE_AVAILABLE,
    OHPCF_ACTION_SCHEDULE,
    CompressorFlexibility,
    ControlCompressorRequest,
)
from eebus.v1.ohpcf_service_pb2_grpc import OHPCFServiceServicer, add_OHPCFServiceServicer_to_server
from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

FAKE_SKI = "4a1f9c2e7b3d8065f1a2c4e6b8d0f2a4c6e8b0d2"
FAKE_BUILD_VERSION = "fake"
# EEBUS serial the fake bridge reports in ServerInfo.device_serial.
FAKE_SERIAL = "a1b2c3d4e5f60718"
GATEWAY_SKI = "6C2B9E41D7A0F3851E2C4B6D8F0A1C3E5B7D9F02"

FAKE_GATEWAY = DiscoveredDevice(
    ski=GATEWAY_SKI,
    brand="Vaillant",
    model="VR940f",
    serial="21223300202609620938000000N0",
    device_type="HeatGenerationSystem",
    host="192.168.178.40",
)

# Shape of the bridge's DeviceStructure JSON: two heating zones, only zone 1
# with a room temperature sensor.
FAKE_STRUCTURE: dict[str, Any] = {
    "ski": "…7D9F02",
    "device_type": "HeatGenerationSystem",
    "captured_at": "2026-10-19T08:00:00Z",
    "entities": [
        {
            "address": "2:1",
            "type": "HVACRoom",
            "features": [
                {
                    "address": "2:1:3",
                    "type": "Setpoint",
                    "role": "server",
                    "functions": [
                        {"name": "setpointListData", "operations": "r,w", "read_status": "read", "data": {}},
                    ],
                },
                {
                    "address": "2:1:4",
                    "type": "Measurement",
                    "role": "server",
                    "functions": [{"name": "measurementListData", "operations": "r", "read_status": "read"}],
                },
            ],
        },
        {
            "address": "2:2",
            "type": "HVACRoom",
            "features": [
                {
                    "address": "2:2:3",
                    "type": "Setpoint",
                    "role": "server",
                    "functions": [
                        {"name": "setpointListData", "operations": "r,w", "read_status": "read", "data": {}},
                    ],
                },
            ],
        },
    ],
    "use_cases": [
        {
            "actor": "HVACRoom",
            "name": "configurationOfRoomHeatingTemperature",
            "version": "1.0.0",
            "available": True,
            "scenarios": [1, 2],
        },
    ],
}


# Values from a real dump (aroTHERM split plus): no room temperature over EEBUS,
# no flow/return/compressor temperatures and no energy counters.
FAKE_MEASUREMENTS: dict[str, tuple[float, str]] = {
    "power_consumption": (532.0, "W"),
    "compressor_power": (527.0, "W"),
    "dhw_temperature": (48.0, "degC"),
    "outdoor_temperature": (13.5, "degC"),
}
FAKE_CAPTURED_AT = "2026-10-19T09:51:46Z"


def _timestamp(value: str = FAKE_CAPTURED_AT) -> Timestamp:
    timestamp = Timestamp()
    timestamp.FromJsonString(value)
    return timestamp


def _normalize(ski: str) -> str:
    return ski.replace(" ", "").upper()


@dataclass
class FakeZone:
    """A heating zone as the real gateway reports it: 5-30 °C in 0.5 steps, no eco."""

    label: str
    setpoint: float
    mode: str = "auto"
    available_modes: list[str] = field(default_factory=lambda: ["auto", "on", "off"])
    setpoint_writable: bool = True
    mode_writable: bool = True
    minimum: float = 5
    maximum: float = 30
    step: float = 0.5

    def state(self, temperature: float | None) -> RoomHeatingState:
        room = RoomHeatingState(
            setpoint=RoomHeatingSetpoint(
                value_celsius=self.setpoint,
                min_celsius=self.minimum,
                max_celsius=self.maximum,
                step_celsius=self.step,
                writable=self.setpoint_writable,
            ),
            system_function=RoomHeatingSystemFunction(
                operation_mode=self.mode, available_modes=self.available_modes, mode_writable=self.mode_writable
            ),
            zone_label=self.label,
        )
        if temperature is not None:
            room.current_temperature_celsius = temperature
        return room


def _on_step(value: float, minimum: float, maximum: float, step: float) -> bool:
    steps = (value - minimum) / step
    return minimum <= value <= maximum and abs(steps - round(steps)) < 1e-6


class FakeDeviceService(DeviceServiceServicer):
    def __init__(self, auto_connect_after: float | None = None) -> None:
        self.server_info = ServerInfo(
            api_major=1,
            api_minor=0,
            bridge_build_version=FAKE_BUILD_VERSION,
            local_ski=FAKE_SKI,
            device_serial=FAKE_SERIAL,
        )
        # When set, GetServerInfo fails with this status code.
        self.fail_with: grpc.StatusCode | None = None
        self.server_info_calls = 0
        self.discovered: list[DiscoveredDevice] = [FAKE_GATEWAY]
        # What the bridge learns from a connected device's classification,
        # independent of what the device announces over mDNS.
        self.device_details: dict[str, DiscoveredDevice] = {GATEWAY_SKI: FAKE_GATEWAY}
        self.registered: set[str] = set()
        self.connected: set[str] = set()
        # Readiness reported by GetDeviceStatus, overriding the connected flag.
        self.readiness: dict[str, DeviceReadinessState.ValueType] = {}
        self.register_calls: list[str] = []
        self.unregister_calls: list[str] = []
        self.structure_json = json.dumps(FAKE_STRUCTURE)
        # Standalone mode: the gateway accepts a registration after this delay.
        self.auto_connect_after = auto_connect_after
        self._subscribers: list[asyncio.Queue[DeviceEvent | None]] = []
        # Device state served by GetDeviceSnapshot; tests may change it.
        self.measurements: dict[str, tuple[float, str]] = dict(FAKE_MEASUREMENTS)
        self.room_temperature: float | None = None
        # Writable values, as the real installation reports them; only Zone 2 is
        # in "on" (the field runs both in auto), so its setpoint takes effect.
        self.zones: dict[str, FakeZone] = {"5.1.1": FakeZone("Zone 1", 21), "6.1.1": FakeZone("Zone 2", 23, mode="on")}
        self.dhw_setpoint = 55.0
        self.dhw_mode = "auto"
        self.dhw_boost: DHWBoostStatus.ValueType = DHW_BOOST_STATUS_INACTIVE
        self.consumption_limit = LoadLimit(value_watts=0, duration_seconds=0, is_active=False, is_changeable=True)
        self.failsafe_limit = FailsafeLimit(value_watts=1_000_000, duration_minimum_seconds=7200)
        self.compressor = CompressorFlexibility(available=True, state=COMPRESSOR_STATE_AVAILABLE)
        # Every write RPC that reached the fake bridge: (RPC name, request).
        self.write_calls: list[tuple[str, Message]] = []
        # When set, write RPCs fail with this status code before the device sees them.
        self.fail_writes_with: grpc.StatusCode | None = None
        # When set, the device applies a write but its answer is lost: the RPC
        # fails with this status code although the value arrived.
        self.answer_lost_with: grpc.StatusCode | None = None
        # Awaited when a write reaches the fake bridge, before it is checked.
        self.before_write: Callable[[], Awaitable[None]] | None = None
        # False: the device accepts writes but keeps reporting the old values.
        self.apply_writes = True
        self.snapshot_calls = 0
        self._state_subscribers: list[asyncio.Queue[DeviceStateEvent | None]] = []

    # --- helpers for tests -------------------------------------------------

    def connect(self, ski: str) -> None:
        self.connected.add(_normalize(ski))
        self.publish(ski, DEVICE_EVENT_CONNECTED)
        # The consolidated state stream carries connection changes as well.
        self.push_state_event(ski)

    def disconnect(self, ski: str) -> None:
        self.connected.discard(_normalize(ski))
        self.publish(ski, DEVICE_EVENT_DISCONNECTED)
        self.push_state_event(ski)

    def publish(self, ski: str, event_type: DeviceEventType.ValueType) -> None:
        event = DeviceEvent(ski=_normalize(ski), event_type=event_type)
        for queue in self._subscribers:
            queue.put_nowait(event)

    def restart(self) -> None:
        """Simulate a bridge restart: registrations are lost and event streams end."""
        self.registered.clear()
        self.connected.clear()
        for queue in self._subscribers:
            queue.put_nowait(None)
        for state_queue in self._state_subscribers:
            state_queue.put_nowait(None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def push_state_event(self, ski: str) -> None:
        """Announce a change of the device state, as the bridge does for new readings."""
        event = DeviceStateEvent(ski=_normalize(ski), event_time=_timestamp())
        for queue in self._state_subscribers:
            queue.put_nowait(event)

    @property
    def state_subscriber_count(self) -> int:
        return len(self._state_subscribers)

    def snapshot(self, ski: str) -> DeviceSnapshot:
        connected = ski in self.connected
        snapshot = DeviceSnapshot(
            ski=ski,
            captured_at=_timestamp(),
            local_ski=FAKE_SKI,
            connection=DeviceStatus(
                connected=connected,
                readiness=DEVICE_READINESS_READY if connected else DEVICE_READINESS_DISCONNECTED,
            ),
            connection_state=SNAPSHOT_VALUE_STATE_AVAILABLE,
        )
        if not connected:
            return snapshot
        snapshot.measurements.extend(
            MeasurementEntry(type=kind, value=value, unit=unit, timestamp=_timestamp())
            for kind, (value, unit) in self.measurements.items()
        )
        snapshot.measurements_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.heartbeat.CopyFrom(HeartbeatStatus(running=True, within_duration=True))
        snapshot.heartbeat_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.device_diagnostics.CopyFrom(DeviceDiagnosticsData(operating_state="normalOperation"))
        snapshot.device_diagnostics_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.dhw_setpoint.CopyFrom(
            DHWSetpoint(value_celsius=self.dhw_setpoint, min_celsius=35, max_celsius=70, step_celsius=1, writable=True)
        )
        snapshot.dhw_setpoint_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.dhw_system_function.CopyFrom(
            DHWSystemFunctionState(
                boost_status=self.dhw_boost,
                boost_writable=True,
                operation_mode=self.dhw_mode,
                available_modes=["auto", "on", "off"],
                mode_writable=True,
            )
        )
        snapshot.dhw_system_function_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        # Two zones like the Vaillant gateway; only Zone 1 may report a room temperature.
        zones = [
            RoomHeatingZone(zone_id=zone_id, state=zone.state(self.room_temperature if zone_id == "5.1.1" else None))
            for zone_id, zone in self.zones.items()
        ]
        snapshot.room_heating_zones.extend(zones)
        snapshot.room_heating.CopyFrom(zones[0].state)
        snapshot.room_heating_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.consumption_limit.CopyFrom(self.consumption_limit)
        snapshot.consumption_limit_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.failsafe_limit.CopyFrom(self.failsafe_limit)
        snapshot.failsafe_limit_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.compressor_flexibility.CopyFrom(self.compressor)
        snapshot.compressor_flexibility_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        return snapshot

    async def accept_write(
        self, name: str, request: Message, ski: str, context: grpc.aio.ServicerContext[Any, Any]
    ) -> bool:
        """Record a write and check it like the bridge; False: the device keeps its values."""
        self.write_calls.append((name, request))
        if self.before_write is not None:
            await self.before_write()
        if self.fail_writes_with is not None:
            await context.abort(self.fail_writes_with, "fake bridge write failure")
        normalized = _normalize(ski)
        if normalized not in self.registered:
            await context.abort(grpc.StatusCode.NOT_FOUND, "device not found for specified ski")
        if normalized not in self.connected:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "device not connected")
        return self.apply_writes

    async def answer(self, context: grpc.aio.ServicerContext[Any, Any]) -> Empty:
        """The bridge's answer to a write, unless it is lost on the way."""
        if self.answer_lost_with is not None:
            await context.abort(self.answer_lost_with, "fake bridge lost the device's answer")
        return Empty()

    async def zone(self, request: Any, context: grpc.aio.ServicerContext[Any, Any]) -> FakeZone:
        """The zone a room heating write goes to; never guesses between zones."""
        if not request.HasField("zone_id"):
            if len(self.zones) != 1:
                await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "zone_id is required with several zones")
            return next(iter(self.zones.values()))
        zone = self.zones.get(request.zone_id)
        if zone is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"unknown heating zone {request.zone_id!r}")
        assert zone is not None
        return zone

    # --- RPCs ----------------------------------------------------------------

    async def GetServerInfo(self, request: Empty, context: grpc.aio.ServicerContext[Empty, ServerInfo]) -> ServerInfo:
        self.server_info_calls += 1
        if self.fail_with is not None:
            await context.abort(self.fail_with, "fake bridge failure")
        return self.server_info

    async def ListDiscoveredDevices(
        self, request: Empty, context: grpc.aio.ServicerContext[Empty, ListDevicesResponse]
    ) -> ListDevicesResponse:
        return ListDevicesResponse(devices=self.discovered)

    async def RegisterRemoteSKI(
        self, request: RegisterSKIRequest, context: grpc.aio.ServicerContext[RegisterSKIRequest, Empty]
    ) -> Empty:
        ski = _normalize(request.ski)
        self.register_calls.append(ski)
        self.registered.add(ski)
        if self.auto_connect_after is not None and ski not in self.connected:
            asyncio.get_running_loop().call_later(self.auto_connect_after, self.connect, ski)
        return Empty()

    async def UnregisterRemoteSKI(
        self, request: RegisterSKIRequest, context: grpc.aio.ServicerContext[RegisterSKIRequest, Empty]
    ) -> Empty:
        ski = _normalize(request.ski)
        self.unregister_calls.append(ski)
        self.registered.discard(ski)
        self.connected.discard(ski)
        self.publish(ski, DEVICE_EVENT_TRUST_REMOVED)
        return Empty()

    async def ListPairedDevices(
        self, request: Empty, context: grpc.aio.ServicerContext[Empty, ListPairedDevicesResponse]
    ) -> ListPairedDevicesResponse:
        devices = [
            PairedDevice(
                ski=device.ski,
                brand=device.brand,
                model=device.model,
                serial=device.serial,
                device_type=device.device_type,
                software_revision="0357.40.31",
                hardware_revision="0201",
            )
            for ski, device in self.device_details.items()
            if ski in self.connected
        ]
        return ListPairedDevicesResponse(devices=devices)

    async def GetDeviceStatus(
        self, request: DeviceRequest, context: grpc.aio.ServicerContext[DeviceRequest, DeviceStatus]
    ) -> DeviceStatus:
        ski = _normalize(request.ski)
        if ski not in self.registered:
            await context.abort(grpc.StatusCode.NOT_FOUND, "device not found for specified ski")
        connected = ski in self.connected
        default = DEVICE_READINESS_READY if connected else DEVICE_READINESS_DISCONNECTED
        return DeviceStatus(connected=connected, readiness=self.readiness.get(ski, default))

    async def SubscribeDeviceEvents(
        self, request: Empty, context: grpc.aio.ServicerContext[Empty, DeviceEvent]
    ) -> AsyncIterator[DeviceEvent]:
        queue: asyncio.Queue[DeviceEvent | None] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._subscribers.remove(queue)

    async def GetDeviceSnapshot(
        self, request: DeviceRequest, context: grpc.aio.ServicerContext[DeviceRequest, DeviceSnapshot]
    ) -> DeviceSnapshot:
        ski = _normalize(request.ski)
        if ski not in self.registered:
            await context.abort(grpc.StatusCode.NOT_FOUND, "device not found for specified ski")
        self.snapshot_calls += 1
        return self.snapshot(ski)

    async def SubscribeDeviceState(
        self, request: DeviceRequest, context: grpc.aio.ServicerContext[DeviceRequest, DeviceStateEvent]
    ) -> AsyncIterator[DeviceStateEvent]:
        queue: asyncio.Queue[DeviceStateEvent | None] = asyncio.Queue()
        self._state_subscribers.append(queue)
        try:
            # Like the bridge: the first envelope asks the client to read a snapshot.
            yield DeviceStateEvent(
                ski=_normalize(request.ski),
                resync_required=ResyncRequired(reason=RESYNC_REASON_INITIAL_STATE_REQUIRED),
            )
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._state_subscribers.remove(queue)

    async def GetDeviceStructureDump(
        self, request: DeviceRequest, context: grpc.aio.ServicerContext[DeviceRequest, DeviceStructureDump]
    ) -> DeviceStructureDump:
        ski = _normalize(request.ski)
        if ski not in self.registered:
            await context.abort(grpc.StatusCode.NOT_FOUND, "device not found for specified ski")
        if ski not in self.connected:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "device not connected")
        return DeviceStructureDump(
            redacted_ski="…" + ski[-6:], captured_at=_timestamp("2026-10-19T08:00:00Z"), json=self.structure_json
        )


class FakeHVACService(HVACServiceServicer):
    def __init__(self, device: FakeDeviceService) -> None:
        self._device = device

    async def SetRoomHeatingTemperature(
        self, request: SetRoomHeatingTemperatureRequest, context: grpc.aio.ServicerContext[Any, Empty]
    ) -> Empty:
        apply = await self._device.accept_write("SetRoomHeatingTemperature", request, request.ski, context)
        zone = await self._device.zone(request, context)
        if not zone.setpoint_writable or not _on_step(request.value_celsius, zone.minimum, zone.maximum, zone.step):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "setpoint outside the zone's limits or step")
        if apply:
            zone.setpoint = request.value_celsius
            self._device.push_state_event(request.ski)
        return await self._device.answer(context)

    async def SetRoomHeatingMode(
        self, request: SetRoomHeatingModeRequest, context: grpc.aio.ServicerContext[Any, Empty]
    ) -> Empty:
        apply = await self._device.accept_write("SetRoomHeatingMode", request, request.ski, context)
        zone = await self._device.zone(request, context)
        if not zone.mode_writable or request.mode not in zone.available_modes:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"mode {request.mode!r} not offered")
        if apply:
            zone.mode = request.mode
            self._device.push_state_event(request.ski)
        return await self._device.answer(context)


class FakeDHWService(DHWServiceServicer):
    def __init__(self, device: FakeDeviceService) -> None:
        self._device = device

    async def SetDHWSetpoint(
        self, request: SetDHWSetpointRequest, context: grpc.aio.ServicerContext[Any, Empty]
    ) -> Empty:
        apply = await self._device.accept_write("SetDHWSetpoint", request, request.ski, context)
        if not _on_step(request.value_celsius, 35, 70, 1):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "DHW setpoint outside the limits or step")
        if apply:
            self._device.dhw_setpoint = request.value_celsius
            self._device.push_state_event(request.ski)
        return await self._device.answer(context)

    async def SetDHWOperationMode(
        self, request: SetDHWOperationModeRequest, context: grpc.aio.ServicerContext[Any, Empty]
    ) -> Empty:
        apply = await self._device.accept_write("SetDHWOperationMode", request, request.ski, context)
        if request.mode not in ("auto", "on", "off"):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"mode {request.mode!r} not offered")
        if apply:
            self._device.dhw_mode = request.mode
            self._device.push_state_event(request.ski)
        return await self._device.answer(context)

    async def SetDHWBoost(self, request: SetDHWBoostRequest, context: grpc.aio.ServicerContext[Any, Empty]) -> Empty:
        if await self._device.accept_write("SetDHWBoost", request, request.ski, context):
            self._device.dhw_boost = DHW_BOOST_STATUS_ACTIVE if request.active else DHW_BOOST_STATUS_INACTIVE
            self._device.push_state_event(request.ski)
        return await self._device.answer(context)


class FakeLPCService(LPCServiceServicer):
    def __init__(self, device: FakeDeviceService) -> None:
        self._device = device

    async def WriteConsumptionLimit(
        self, request: WriteLoadLimitRequest, context: grpc.aio.ServicerContext[Any, Empty]
    ) -> Empty:
        if await self._device.accept_write("WriteConsumptionLimit", request, request.ski, context):
            self._device.consumption_limit = LoadLimit(
                value_watts=request.value_watts,
                duration_seconds=request.duration_seconds,
                is_active=request.is_active,
                is_changeable=True,
            )
            self._device.push_state_event(request.ski)
        return await self._device.answer(context)

    async def WriteFailsafeLimit(
        self, request: WriteFailsafeLimitRequest, context: grpc.aio.ServicerContext[Any, Empty]
    ) -> Empty:
        if await self._device.accept_write("WriteFailsafeLimit", request, request.ski, context):
            self._device.failsafe_limit = FailsafeLimit(
                value_watts=request.value_watts, duration_minimum_seconds=request.duration_minimum_seconds
            )
            self._device.push_state_event(request.ski)
        return await self._device.answer(context)


class FakeOHPCFService(OHPCFServiceServicer):
    def __init__(self, device: FakeDeviceService) -> None:
        self._device = device

    async def ControlCompressorFlexibility(
        self, request: ControlCompressorRequest, context: grpc.aio.ServicerContext[Any, Empty]
    ) -> Empty:
        await self._device.accept_write("ControlCompressorFlexibility", request, request.ski, context)
        if request.action == OHPCF_ACTION_SCHEDULE and not self._device.compressor.available:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "no optional consumption available")
        return await self._device.answer(context)


@dataclass
class FakeBridge:
    server: grpc.aio.Server
    address: str
    device: FakeDeviceService

    async def stop(self) -> None:
        await self.server.stop(grace=None)


async def start_fake_bridge(port: int = 0, auto_connect_after: float | None = None) -> FakeBridge:
    """Start the fake bridge on 127.0.0.1; port 0 picks a free port."""
    server = grpc.aio.server()
    device = FakeDeviceService(auto_connect_after)  # type: ignore[abstract]  # unimplemented RPCs answer UNIMPLEMENTED
    add_DeviceServiceServicer_to_server(device, server)
    add_HVACServiceServicer_to_server(FakeHVACService(device), server)  # type: ignore[abstract]
    add_DHWServiceServicer_to_server(FakeDHWService(device), server)  # type: ignore[abstract]
    add_LPCServiceServicer_to_server(FakeLPCService(device), server)  # type: ignore[abstract]
    add_OHPCFServiceServicer_to_server(FakeOHPCFService(device), server)  # type: ignore[abstract]
    bound_port = server.add_insecure_port(f"127.0.0.1:{port}")
    await server.start()
    return FakeBridge(server=server, address=f"127.0.0.1:{bound_port}", device=device)


async def _serve(port: int, connect_after: float) -> None:
    bridge = await start_fake_bridge(port, auto_connect_after=connect_after)
    print(f"Fake bridge listening on {bridge.address}")
    await bridge.server.wait_for_termination()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake EEBUS bridge (gRPC) for development without a heat pump")
    parser.add_argument("--port", type=int, default=50471)
    parser.add_argument(
        "--connect-after", type=float, default=5.0, help="seconds until the fake gateway accepts a pairing"
    )
    args = parser.parse_args()
    asyncio.run(_serve(args.port, args.connect_after))


if __name__ == "__main__":
    main()
