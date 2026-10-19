"""gRPC connection to the EEBUS bridge.

This module is the only place in the backend that talks to the bridge. Its
write methods (WRITE_METHODS) are called by the write gate alone.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Self

import grpc
from eebus.v1.common_pb2 import DeviceRequest, Empty
from eebus.v1.device_service_pb2 import (
    DeviceEvent,
    DeviceSnapshot,
    DeviceStateEvent,
    DeviceStatus,
    DeviceStructureDump,
    DiscoveredDevice,
    PairedDevice,
    RegisterSKIRequest,
    ServerInfo,
)
from eebus.v1.device_service_pb2_grpc import DeviceServiceStub
from eebus.v1.dhw_service_pb2 import SetDHWBoostRequest, SetDHWOperationModeRequest, SetDHWSetpointRequest
from eebus.v1.dhw_service_pb2_grpc import DHWServiceStub
from eebus.v1.hvac_service_pb2 import SetRoomHeatingModeRequest, SetRoomHeatingTemperatureRequest
from eebus.v1.hvac_service_pb2_grpc import HVACServiceStub
from eebus.v1.lpc_service_pb2 import WriteFailsafeLimitRequest, WriteLoadLimitRequest
from eebus.v1.lpc_service_pb2_grpc import LPCServiceStub
from eebus.v1.ohpcf_service_pb2 import (
    OHPCF_ACTION_ABORT,
    OHPCF_ACTION_PAUSE,
    OHPCF_ACTION_RESUME,
    OHPCF_ACTION_SCHEDULE,
    ControlCompressorRequest,
    OHPCFAction,
)
from eebus.v1.ohpcf_service_pb2_grpc import OHPCFServiceStub
from google.protobuf.timestamp_pb2 import Timestamp

_LOGGER = logging.getLogger(__name__)

# Not the upstream default 50051, so another EEBUS bridge on the same host does not collide.
DEFAULT_ADDRESS = "127.0.0.1:50471"
RPC_TIMEOUT = 5.0  # seconds
# The bridge waits up to 10 s for the device's answers before it builds the dump.
DUMP_TIMEOUT = 30.0  # seconds
# The structure dump of a large installation can exceed gRPC's 4 MB default.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
# The bridge waits up to 10 s for the device to accept a write; giving up
# earlier would leave unknown whether the value arrived.
WRITE_TIMEOUT = 15.0  # seconds

# Methods that change the heat pump. Only the write gate may call them.
WRITE_METHODS = frozenset(
    {
        "set_zone_setpoint",
        "set_zone_mode",
        "set_dhw_setpoint",
        "set_dhw_mode",
        "set_dhw_boost",
        "write_consumption_limit",
        "write_failsafe_limit",
        "control_compressor",
    }
)

_COMPRESSOR_ACTIONS: dict[str, OHPCFAction.ValueType] = {
    "schedule": OHPCF_ACTION_SCHEDULE,
    "pause": OHPCF_ACTION_PAUSE,
    "resume": OHPCF_ACTION_RESUME,
    "abort": OHPCF_ACTION_ABORT,
}

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
        self._hvac = HVACServiceStub(self._channel)
        self._dhw = DHWServiceStub(self._channel)
        self._lpc = LPCServiceStub(self._channel)
        self._ohpcf = OHPCFServiceStub(self._channel)

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

    async def get_device_snapshot(self, ski: str) -> DeviceSnapshot:
        """Complete current state of the device as the bridge caches it.

        A snapshot also counts as a live monitoring read for the bridge's
        watchdog, which otherwise reconnects the device after a few minutes.
        """
        return await self._device.GetDeviceSnapshot(DeviceRequest(ski=ski), timeout=RPC_TIMEOUT)

    async def device_state_events(self, ski: str) -> AsyncIterator[DeviceStateEvent]:
        """Yield the device's state change events until the stream ends or fails."""
        call = self._device.SubscribeDeviceState(DeviceRequest(ski=ski))
        try:
            async for event in call:
                yield event
        finally:
            call.cancel()

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

    # --- writes: called by the write gate only ---------------------------------

    async def set_zone_setpoint(self, ski: str, zone_id: str, celsius: float) -> None:
        request = SetRoomHeatingTemperatureRequest(ski=ski, value_celsius=celsius, zone_id=zone_id)
        await self._hvac.SetRoomHeatingTemperature(request, timeout=WRITE_TIMEOUT)

    async def set_zone_mode(self, ski: str, zone_id: str, mode: str) -> None:
        request = SetRoomHeatingModeRequest(ski=ski, mode=mode, zone_id=zone_id)
        await self._hvac.SetRoomHeatingMode(request, timeout=WRITE_TIMEOUT)

    async def set_dhw_setpoint(self, ski: str, celsius: float) -> None:
        await self._dhw.SetDHWSetpoint(SetDHWSetpointRequest(ski=ski, value_celsius=celsius), timeout=WRITE_TIMEOUT)

    async def set_dhw_mode(self, ski: str, mode: str) -> None:
        await self._dhw.SetDHWOperationMode(SetDHWOperationModeRequest(ski=ski, mode=mode), timeout=WRITE_TIMEOUT)

    async def set_dhw_boost(self, ski: str, active: bool) -> None:
        await self._dhw.SetDHWBoost(SetDHWBoostRequest(ski=ski, active=active), timeout=WRITE_TIMEOUT)

    async def write_consumption_limit(self, ski: str, watts: float, duration_s: int, active: bool) -> None:
        request = WriteLoadLimitRequest(ski=ski, value_watts=watts, duration_seconds=duration_s, is_active=active)
        await self._lpc.WriteConsumptionLimit(request, timeout=WRITE_TIMEOUT)

    async def write_failsafe_limit(self, ski: str, watts: float, duration_s: int) -> None:
        request = WriteFailsafeLimitRequest(ski=ski, value_watts=watts, duration_minimum_seconds=duration_s)
        await self._lpc.WriteFailsafeLimit(request, timeout=WRITE_TIMEOUT)

    async def control_compressor(self, ski: str, action: str, start_time: datetime | None = None) -> None:
        """action: schedule, pause, resume or abort; start_time only for schedule (None: now)."""
        request = ControlCompressorRequest(ski=ski, action=_COMPRESSOR_ACTIONS[action])
        if start_time is not None:
            timestamp = Timestamp()
            timestamp.FromDatetime(start_time)
            request.start_time.CopyFrom(timestamp)
        await self._ohpcf.ControlCompressorFlexibility(request, timeout=WRITE_TIMEOUT)
