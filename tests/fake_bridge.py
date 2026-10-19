"""Fake EEBUS bridge for development and tests without a heat pump.

A gRPC server built from the same proto files as the real bridge. So far it
only serves DeviceService.GetServerInfo; every other RPC answers UNIMPLEMENTED
(behaviour of the generated base class). It grows with each phase.

Standalone, e.g. to run the backend against it:
    PYTHONPATH=backend/gen python -m tests.fake_bridge --port 50051
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

import grpc
from eebus.v1.common_pb2 import Empty
from eebus.v1.device_service_pb2 import ServerInfo
from eebus.v1.device_service_pb2_grpc import DeviceServiceServicer, add_DeviceServiceServicer_to_server

FAKE_SKI = "4a1f9c2e7b3d8065f1a2c4e6b8d0f2a4c6e8b0d2"
FAKE_BUILD_VERSION = "fake"


class FakeDeviceService(DeviceServiceServicer):
    def __init__(self) -> None:
        self.server_info = ServerInfo(
            api_major=1,
            api_minor=0,
            bridge_build_version=FAKE_BUILD_VERSION,
            local_ski=FAKE_SKI,
        )
        # When set, GetServerInfo fails with this status code.
        self.fail_with: grpc.StatusCode | None = None
        self.server_info_calls = 0

    async def GetServerInfo(self, request: Empty, context: grpc.aio.ServicerContext[Empty, ServerInfo]) -> ServerInfo:
        self.server_info_calls += 1
        if self.fail_with is not None:
            await context.abort(self.fail_with, "fake bridge failure")
        return self.server_info


@dataclass
class FakeBridge:
    server: grpc.aio.Server
    address: str
    device: FakeDeviceService

    async def stop(self) -> None:
        await self.server.stop(grace=None)


async def start_fake_bridge(port: int = 0) -> FakeBridge:
    """Start the fake bridge on 127.0.0.1; port 0 picks a free port."""
    server = grpc.aio.server()
    device = FakeDeviceService()  # type: ignore[abstract]  # unimplemented RPCs answer UNIMPLEMENTED
    add_DeviceServiceServicer_to_server(device, server)
    bound_port = server.add_insecure_port(f"127.0.0.1:{port}")
    await server.start()
    return FakeBridge(server=server, address=f"127.0.0.1:{bound_port}", device=device)


async def _serve(port: int) -> None:
    bridge = await start_fake_bridge(port)
    print(f"Fake bridge listening on {bridge.address}")
    await bridge.server.wait_for_termination()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake EEBUS bridge (gRPC) for development without a heat pump")
    parser.add_argument("--port", type=int, default=50051)
    asyncio.run(_serve(parser.parse_args().port))


if __name__ == "__main__":
    main()
