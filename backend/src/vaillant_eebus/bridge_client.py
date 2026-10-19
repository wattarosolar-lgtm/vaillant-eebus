"""gRPC connection to the EEBUS bridge.

This module is the only place in the backend that talks to the bridge.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import grpc
from eebus.v1.common_pb2 import DeviceRequest, Empty
from eebus.v1.device_service_pb2 import (
    DeviceEvent,
    DeviceStatus,
    DeviceStructureDump,
    DiscoveredDevice,
    PairedDevice,
    RegisterSKIRequest,
    ServerInfo,
)
from eebus.v1.device_service_pb2_grpc import DeviceServiceStub

_LOGGER = logging.getLogger(__name__)

# Not the upstream default 50051, so another EEBUS bridge on the same host does not collide.
DEFAULT_ADDRESS = "127.0.0.1:50471"
RPC_TIMEOUT = 5.0  # seconds
# The bridge waits up to 10 s for the device's answers before it builds the dump.
DUMP_TIMEOUT = 30.0  # seconds
# The structure dump of a large installation can exceed gRPC's 4 MB default.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024

# Codes that mean "bridge not (yet) reachable". Anything else is a real error
# and is raised instead of being retried forever.
RETRYABLE_CODES = frozenset({grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED})


@dataclass(frozen=True, slots=True)
class Backoff:
    """Reconnect delays in seconds: start at initial, double per failure, cap at maximum."""

    initial: float = 1.0
    maximum: float = 30.0

    def delays(self) -> Iterator[float]:
        delay = self.initial
        while True:
            yield delay
            delay = min(delay * 2, self.maximum)


class BridgeClient:
    """Async client for the bridge's gRPC API on the loopback interface."""

    def __init__(self, address: str = DEFAULT_ADDRESS, backoff: Backoff | None = None) -> None:
        self._address = address
        self._backoff = backoff or Backoff()
        self._channel = grpc.aio.insecure_channel(
            address,
            options=[
                # Let the channel reconnect on the same schedule as our retries.
                ("grpc.initial_reconnect_backoff_ms", int(self._backoff.initial * 1000)),
                ("grpc.max_reconnect_backoff_ms", int(self._backoff.maximum * 1000)),
                ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ],
        )
        self._device = DeviceServiceStub(self._channel)

    @property
    def address(self) -> str:
        return self._address

    @property
    def backoff(self) -> Backoff:
        return self._backoff

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._channel.close()

    async def get_server_info(self) -> ServerInfo:
        return await self._device.GetServerInfo(Empty(), timeout=RPC_TIMEOUT)

    async def wait_until_ready(self) -> ServerInfo:
        """Return the bridge's server info as soon as its gRPC API answers.

        Retries with backoff while the bridge is unreachable (e.g. still starting
        or restarted by s6). Other gRPC errors are raised.
        """
        for delay in self._backoff.delays():
            try:
                return await self.get_server_info()
            except grpc.aio.AioRpcError as err:
                if err.code() not in RETRYABLE_CODES:
                    raise
                _LOGGER.info(
                    "Bridge at %s not reachable (%s), retrying in %.1f s", self._address, err.code().name, delay
                )
            await asyncio.sleep(delay)
        raise AssertionError("unreachable: Backoff.delays() never ends")

    async def list_discovered(self) -> list[DiscoveredDevice]:
        response = await self._device.ListDiscoveredDevices(Empty(), timeout=RPC_TIMEOUT)
        return list(response.devices)

    async def list_paired(self) -> list[PairedDevice]:
        response = await self._device.ListPairedDevices(Empty(), timeout=RPC_TIMEOUT)
        return list(response.devices)

    async def register_ski(self, ski: str) -> None:
        await self._device.RegisterRemoteSKI(RegisterSKIRequest(ski=ski), timeout=RPC_TIMEOUT)

    async def unregister_ski(self, ski: str) -> None:
        await self._device.UnregisterRemoteSKI(RegisterSKIRequest(ski=ski), timeout=RPC_TIMEOUT)

    async def get_device_status(self, ski: str) -> DeviceStatus:
        return await self._device.GetDeviceStatus(DeviceRequest(ski=ski), timeout=RPC_TIMEOUT)

    async def get_structure_dump(self, ski: str) -> DeviceStructureDump:
        return await self._device.GetDeviceStructureDump(DeviceRequest(ski=ski), timeout=DUMP_TIMEOUT)

    async def device_events(self) -> AsyncIterator[DeviceEvent]:
        """Yield connection events of all devices until the stream ends or fails."""
        call = self._device.SubscribeDeviceEvents(Empty())
        try:
            async for event in call:
                yield event
        finally:
            call.cancel()
