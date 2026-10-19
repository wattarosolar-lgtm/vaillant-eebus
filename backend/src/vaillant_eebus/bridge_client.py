"""gRPC connection to the EEBUS bridge.

This module is the only place in the backend that talks to the bridge.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import grpc
from eebus.v1.common_pb2 import Empty
from eebus.v1.device_service_pb2 import ServerInfo
from eebus.v1.device_service_pb2_grpc import DeviceServiceStub

_LOGGER = logging.getLogger(__name__)

DEFAULT_ADDRESS = "127.0.0.1:50051"
RPC_TIMEOUT = 5.0  # seconds

# Codes that mean "bridge not (yet) reachable". Anything else is a real error
# and is raised instead of being retried forever.
_RETRYABLE = frozenset({grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED})


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
            ],
        )
        self._device = DeviceServiceStub(self._channel)

    @property
    def address(self) -> str:
        return self._address

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
                if err.code() not in _RETRYABLE:
                    raise
                _LOGGER.info(
                    "Bridge at %s not reachable (%s), retrying in %.1f s", self._address, err.code().name, delay
                )
            await asyncio.sleep(delay)
        raise AssertionError("unreachable: Backoff.delays() never ends")
