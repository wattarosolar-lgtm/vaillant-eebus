"""Fake EEBUS bridge for development and tests without a heat pump.

A gRPC server built from the same proto files as the real bridge. It serves the
DeviceService RPCs the backend uses so far: server info, gateway discovery,
pairing, device status, device events and the structure dump. Every other RPC
answers UNIMPLEMENTED (behaviour of the generated base class). It grows with
each phase.

Standalone, e.g. to click through the setup wizard without hardware (the fake
gateway "accepts" the pairing a few seconds after it was registered):
    PYTHONPATH=backend/gen python -m tests.fake_bridge --port 50471
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import grpc
from eebus.v1.common_pb2 import DeviceRequest, Empty, MeasurementEntry
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
from eebus.v1.dhw_service_pb2 import DHW_BOOST_STATUS_INACTIVE, DHWSetpoint, DHWSystemFunctionState
from eebus.v1.hvac_service_pb2 import RoomHeatingSetpoint, RoomHeatingState, RoomHeatingSystemFunction
from eebus.v1.lpc_service_pb2 import HeartbeatStatus
from eebus.v1.monitoring_service_pb2 import DeviceDiagnosticsData
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
        self.snapshot_calls = 0
        self._state_subscribers: list[asyncio.Queue[DeviceStateEvent | None]] = []

    # --- helpers for tests -------------------------------------------------

    def connect(self, ski: str) -> None:
        self.connected.add(_normalize(ski))
        self.publish(ski, DEVICE_EVENT_CONNECTED)

    def disconnect(self, ski: str) -> None:
        self.connected.discard(_normalize(ski))
        self.publish(ski, DEVICE_EVENT_DISCONNECTED)

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
            DHWSetpoint(value_celsius=55, min_celsius=35, max_celsius=70, step_celsius=1, writable=True)
        )
        snapshot.dhw_setpoint_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        snapshot.dhw_system_function.CopyFrom(
            DHWSystemFunctionState(
                boost_status=DHW_BOOST_STATUS_INACTIVE,
                boost_writable=True,
                operation_mode="auto",
                available_modes=["auto", "on", "off"],
                mode_writable=True,
            )
        )
        snapshot.dhw_system_function_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        room = RoomHeatingState(
            setpoint=RoomHeatingSetpoint(
                value_celsius=21, min_celsius=5, max_celsius=30, step_celsius=0.5, writable=True
            ),
            system_function=RoomHeatingSystemFunction(
                operation_mode="auto", available_modes=["auto", "on", "off"], mode_writable=True
            ),
            zone_label="Zone 1",
        )
        if self.room_temperature is not None:
            room.current_temperature_celsius = self.room_temperature
        snapshot.room_heating.CopyFrom(room)
        snapshot.room_heating_state = SNAPSHOT_VALUE_STATE_AVAILABLE
        return snapshot

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
