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
from eebus.v1.common_pb2 import DeviceRequest, Empty
from eebus.v1.device_service_pb2 import (
    DEVICE_EVENT_CONNECTED,
    DEVICE_EVENT_DISCONNECTED,
    DEVICE_EVENT_TRUST_REMOVED,
    DEVICE_READINESS_DISCONNECTED,
    DEVICE_READINESS_READY,
    DeviceEvent,
    DeviceEventType,
    DeviceReadinessState,
    DeviceStatus,
    DeviceStructureDump,
    DiscoveredDevice,
    ListDevicesResponse,
    ListPairedDevicesResponse,
    PairedDevice,
    RegisterSKIRequest,
    ServerInfo,
)
from eebus.v1.device_service_pb2_grpc import DeviceServiceServicer, add_DeviceServiceServicer_to_server
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

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

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
            for device in self.discovered
            if _normalize(device.ski) in self.connected
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

    async def GetDeviceStructureDump(
        self, request: DeviceRequest, context: grpc.aio.ServicerContext[DeviceRequest, DeviceStructureDump]
    ) -> DeviceStructureDump:
        ski = _normalize(request.ski)
        if ski not in self.registered:
            await context.abort(grpc.StatusCode.NOT_FOUND, "device not found for specified ski")
        if ski not in self.connected:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "device not connected")
        captured_at = Timestamp()
        captured_at.FromJsonString("2026-10-19T08:00:00Z")
        return DeviceStructureDump(redacted_ski="…" + ski[-6:], captured_at=captured_at, json=self.structure_json)


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
