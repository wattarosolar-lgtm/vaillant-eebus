"""Current state of the heat pump.

The bridge's device snapshot is the single source of truth. StateService
re-reads it after every state event of the bridge (bundled over a short delay)
and at least once per refresh interval, and hands every new state to its
listeners. Values the device does not report stay None ("nicht verfügbar"),
they are never filled with substitutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import grpc
from eebus.v1.device_service_pb2 import (
    SNAPSHOT_VALUE_STATE_AVAILABLE,
    DeviceSnapshot,
)
from eebus.v1.dhw_service_pb2 import (
    DHW_BOOST_STATUS_ACTIVE,
    DHW_BOOST_STATUS_FINISHED,
    DHW_BOOST_STATUS_INACTIVE,
    DHW_BOOST_STATUS_RUNNING,
)
from eebus.v1.hvac_service_pb2 import RoomHeatingState
from eebus.v1.ohpcf_service_pb2 import (
    COMPRESSOR_STATE_AVAILABLE,
    COMPRESSOR_STATE_COMPLETED,
    COMPRESSOR_STATE_PAUSED,
    COMPRESSOR_STATE_RUNNING,
    COMPRESSOR_STATE_SCHEDULED,
    COMPRESSOR_STATE_STOPPED,
)
from google.protobuf.timestamp_pb2 import Timestamp

from vaillant_eebus.bridge_client import RETRYABLE_CODES, BridgeClient
from vaillant_eebus.pairing import PairingService

_LOGGER = logging.getLogger(__name__)

# German names of the operation modes, as in the UI (protocol, sensors in Home Assistant).
MODE_NAMES = {"off": "Aus", "auto": "Auto", "on": "Ein", "eco": "Eco"}

# Measurement types of the bridge's catalog (canonical units: W, kWh, A, V, Hz, degC).
MEASUREMENT_TYPES = frozenset(
    {
        "power_consumption",
        "energy_consumed",
        "energy_produced",
        "energy_consumed_heating",
        "energy_consumed_dhw",
        "compressor_power",
        "power_l1",
        "power_l2",
        "power_l3",
        "current_l1",
        "current_l2",
        "current_l3",
        "voltage_l1",
        "voltage_l2",
        "voltage_l3",
        "frequency",
        "dhw_temperature",
        "room_temperature",
        "outdoor_temperature",
        "flow_temperature",
        "return_temperature",
        "compressor_temperature",
    }
)

# Spec 7.1: the compressor counts as running above this electrical power.
COMPRESSOR_ACTIVE_ABOVE_W = 100.0

_BOOST_STATES = {
    DHW_BOOST_STATUS_INACTIVE: "inactive",
    DHW_BOOST_STATUS_ACTIVE: "active",
    DHW_BOOST_STATUS_RUNNING: "running",
    DHW_BOOST_STATUS_FINISHED: "finished",
}

_COMPRESSOR_STATES = {
    COMPRESSOR_STATE_AVAILABLE: "available",
    COMPRESSOR_STATE_SCHEDULED: "scheduled",
    COMPRESSOR_STATE_RUNNING: "running",
    COMPRESSOR_STATE_PAUSED: "paused",
    COMPRESSOR_STATE_COMPLETED: "completed",
    COMPRESSOR_STATE_STOPPED: "stopped",
}


@dataclass(frozen=True, slots=True)
class Reading:
    value: float
    unit: str
    updated_at: datetime | None
    source: str = "eebus"


@dataclass(frozen=True, slots=True)
class Setpoint:
    value: float
    min: float
    max: float
    step: float
    writable: bool


@dataclass(frozen=True, slots=True)
class Zone:
    # The bridge's zone ID, the SPINE address of the zone's room entity, e.g. "5.1.1".
    id: str
    label: str
    room_temperature: Reading | None
    setpoint: Setpoint | None
    mode: str | None
    available_modes: list[str]
    mode_writable: bool


@dataclass(frozen=True, slots=True)
class HotWater:
    temperature: Reading | None
    setpoint: Setpoint | None
    mode: str | None
    available_modes: list[str]
    mode_writable: bool
    # inactive, active, running or finished; None if the device reports no boost.
    boost: str | None
    boost_writable: bool


@dataclass(frozen=True, slots=True)
class ConsumptionLimit:
    # Power limit of the LPC use case; only enforced while active.
    value_w: float
    duration_s: int
    active: bool
    changeable: bool


@dataclass(frozen=True, slots=True)
class FailsafeLimit:
    # Limit the device applies on its own when the app's heartbeat stops.
    value_w: float
    duration_s: int


@dataclass(frozen=True, slots=True)
class CompressorFlexibility:
    # Whether the compressor offers an optional consumption the app may schedule.
    available: bool
    # available, scheduled, running, paused, completed or stopped; None if unknown.
    state: str | None
    stoppable: bool
    pausable: bool


@dataclass(frozen=True, slots=True)
class HeatPumpState:
    # False when the last snapshot could not be read (bridge or device away).
    available: bool
    connected: bool
    heartbeat_ok: bool | None
    operating_state: str | None
    compressor_active: bool | None
    measurements: dict[str, Reading] = field(default_factory=dict)
    zones: list[Zone] = field(default_factory=list)
    hot_water: HotWater | None = None
    # Not shown in the UI; read for the API and the write gate.
    consumption_limit: ConsumptionLimit | None = None
    failsafe_limit: FailsafeLimit | None = None
    compressor_flexibility: CompressorFlexibility | None = None
    captured_at: datetime | None = None


def _time(timestamp: Timestamp) -> datetime | None:
    if timestamp.seconds == 0 and timestamp.nanos == 0:
        return None
    return timestamp.ToDatetime(tzinfo=UTC)


def _measurements(snapshot: DeviceSnapshot) -> dict[str, Reading]:
    readings: dict[str, Reading] = {}
    for entry in snapshot.measurements:
        if entry.type in MEASUREMENT_TYPES:
            readings[entry.type] = Reading(value=entry.value, unit=entry.unit, updated_at=_time(entry.timestamp))
    return readings


def _compressor_active(measurements: dict[str, Reading]) -> bool | None:
    power = measurements.get("compressor_power") or measurements.get("power_consumption")
    return None if power is None else power.value > COMPRESSOR_ACTIVE_ABOVE_W


def _zones(snapshot: DeviceSnapshot, captured_at: datetime | None) -> list[Zone]:
    if snapshot.room_heating_state != SNAPSHOT_VALUE_STATE_AVAILABLE:
        return []
    return [
        _zone(entry.zone_id, position, entry.state, captured_at)
        for position, entry in enumerate(snapshot.room_heating_zones, start=1)
    ]


def _zone(zone_id: str, position: int, room: RoomHeatingState, captured_at: datetime | None) -> Zone:
    temperature = (
        Reading(value=room.current_temperature_celsius, unit="degC", updated_at=captured_at)
        if room.HasField("current_temperature_celsius")
        else None
    )
    setpoint = (
        Setpoint(
            value=room.setpoint.value_celsius,
            min=room.setpoint.min_celsius,
            max=room.setpoint.max_celsius,
            step=room.setpoint.step_celsius,
            writable=room.setpoint.writable,
        )
        if room.HasField("setpoint")
        else None
    )
    function = room.system_function if room.HasField("system_function") else None
    return Zone(
        id=zone_id,
        # Zones without a name of their own are numbered in the bridge's order.
        # The bridge lists only zones it can read right now, so such a number is
        # stable only while every zone is readable (the real gateway names its zones).
        label=room.zone_label if room.HasField("zone_label") and room.zone_label else f"Zone {position}",
        room_temperature=temperature,
        setpoint=setpoint,
        mode=function.operation_mode or None if function else None,
        available_modes=list(function.available_modes) if function else [],
        mode_writable=function.mode_writable if function else False,
    )


def _hot_water(snapshot: DeviceSnapshot, measurements: dict[str, Reading]) -> HotWater | None:
    has_setpoint = snapshot.dhw_setpoint_state == SNAPSHOT_VALUE_STATE_AVAILABLE and snapshot.HasField("dhw_setpoint")
    has_function = snapshot.dhw_system_function_state == SNAPSHOT_VALUE_STATE_AVAILABLE and snapshot.HasField(
        "dhw_system_function"
    )
    temperature = measurements.get("dhw_temperature")
    if not (has_setpoint or has_function or temperature):
        return None
    setpoint = None
    if has_setpoint:
        dhw = snapshot.dhw_setpoint
        setpoint = Setpoint(
            value=dhw.value_celsius,
            min=dhw.min_celsius,
            max=dhw.max_celsius,
            step=dhw.step_celsius,
            writable=dhw.writable,
        )
    function = snapshot.dhw_system_function if has_function else None
    return HotWater(
        temperature=temperature,
        setpoint=setpoint,
        mode=function.operation_mode or None if function else None,
        available_modes=list(function.available_modes) if function else [],
        mode_writable=function.mode_writable if function else False,
        boost=_BOOST_STATES.get(function.boost_status) if function else None,
        boost_writable=function.boost_writable if function else False,
    )


def _consumption_limit(snapshot: DeviceSnapshot) -> ConsumptionLimit | None:
    if snapshot.consumption_limit_state != SNAPSHOT_VALUE_STATE_AVAILABLE or not snapshot.HasField("consumption_limit"):
        return None
    limit = snapshot.consumption_limit
    return ConsumptionLimit(
        value_w=limit.value_watts,
        duration_s=limit.duration_seconds,
        active=limit.is_active,
        changeable=limit.is_changeable,
    )


def _failsafe_limit(snapshot: DeviceSnapshot) -> FailsafeLimit | None:
    if snapshot.failsafe_limit_state != SNAPSHOT_VALUE_STATE_AVAILABLE or not snapshot.HasField("failsafe_limit"):
        return None
    limit = snapshot.failsafe_limit
    return FailsafeLimit(value_w=limit.value_watts, duration_s=limit.duration_minimum_seconds)


def _compressor_flexibility(snapshot: DeviceSnapshot) -> CompressorFlexibility | None:
    if snapshot.compressor_flexibility_state != SNAPSHOT_VALUE_STATE_AVAILABLE or not snapshot.HasField(
        "compressor_flexibility"
    ):
        return None
    flexibility = snapshot.compressor_flexibility
    return CompressorFlexibility(
        available=flexibility.available,
        state=_COMPRESSOR_STATES.get(flexibility.state),
        stoppable=flexibility.is_stoppable,
        pausable=flexibility.is_pausable,
    )


def state_from_snapshot(snapshot: DeviceSnapshot) -> HeatPumpState:
    """Map the bridge's snapshot to the app's state; absent values stay None."""
    captured_at = _time(snapshot.captured_at)
    measurements = _measurements(snapshot)
    heartbeat_ok = None
    if snapshot.heartbeat_state == SNAPSHOT_VALUE_STATE_AVAILABLE and snapshot.HasField("heartbeat"):
        heartbeat_ok = snapshot.heartbeat.running and snapshot.heartbeat.within_duration
    operating_state = None
    if snapshot.device_diagnostics_state == SNAPSHOT_VALUE_STATE_AVAILABLE and snapshot.HasField("device_diagnostics"):
        operating_state = snapshot.device_diagnostics.operating_state or None
    return HeatPumpState(
        available=True,
        connected=snapshot.connection.connected,
        heartbeat_ok=heartbeat_ok,
        operating_state=operating_state,
        compressor_active=_compressor_active(measurements),
        measurements=measurements,
        zones=_zones(snapshot, captured_at),
        hot_water=_hot_water(snapshot, measurements),
        consumption_limit=_consumption_limit(snapshot),
        failsafe_limit=_failsafe_limit(snapshot),
        compressor_flexibility=_compressor_flexibility(snapshot),
        captured_at=captured_at,
    )


UNAVAILABLE = HeatPumpState(
    available=False, connected=False, heartbeat_ok=None, operating_state=None, compressor_active=None
)


class StateService:
    """Keeps the heat pump state current and notifies listeners of every new state."""

    def __init__(
        self,
        bridge: BridgeClient,
        pairing: PairingService,
        *,
        refresh_interval: float = 60.0,
        debounce: float = 1.0,
    ) -> None:
        """refresh_interval also keeps the bridge's monitoring watchdog satisfied
        (it expects a live monitoring read within minutes after connecting)."""
        self._bridge = bridge
        self._pairing = pairing
        self._refresh_interval = refresh_interval
        self._debounce = debounce
        self._state: HeatPumpState | None = None
        self._listeners: set[Callable[[HeatPumpState | None], None]] = set()
        self._dirty = asyncio.Event()

    @property
    def current(self) -> HeatPumpState | None:
        """None until a gateway is paired and this app's own bridge answers."""
        return self._state

    @contextlib.asynccontextmanager
    async def listen(self) -> AsyncIterator[asyncio.Queue[HeatPumpState | None]]:
        """Queue that always holds the latest state not yet taken by the listener."""
        queue: asyncio.Queue[HeatPumpState | None] = asyncio.Queue(maxsize=1)

        def deliver(state: HeatPumpState | None) -> None:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

        self._listeners.add(deliver)
        try:
            yield queue
        finally:
            self._listeners.discard(deliver)

    def refresh_now(self) -> None:
        """Read the snapshot again soon, e.g. right after a write."""
        self._dirty.set()

    def republish(self) -> None:
        """Hand the current state to all listeners again, e.g. after the operating
        mode changed, so open event streams send the new overall state."""
        self._publish(self._state)

    async def run(self) -> None:
        backoff = self._bridge.backoff.delays()
        while True:
            ski = self._pairing.paired_ski
            if ski is None or not await self._pairing.own_bridge_ready():
                self._publish(None)
                await asyncio.sleep(self._bridge.backoff.maximum)
                continue
            try:
                await self._follow(ski)
            except grpc.aio.AioRpcError as err:
                if err.code() not in RETRYABLE_CODES:
                    raise
                _LOGGER.warning("Device state stream failed (%s), reconnecting", err.code().name)
                self._publish(UNAVAILABLE)
                await asyncio.sleep(next(backoff))
                continue
            backoff = self._bridge.backoff.delays()
            await asyncio.sleep(self._bridge.backoff.initial)

    async def refresh(self, ski: str) -> None:
        """Read the snapshot once and publish it; marks the state unavailable on failure."""
        try:
            snapshot = await self._bridge.get_device_snapshot(ski)
        except grpc.aio.AioRpcError as err:
            if err.code() not in RETRYABLE_CODES and err.code() is not grpc.StatusCode.NOT_FOUND:
                raise
            _LOGGER.info("Device snapshot unavailable (%s)", err.code().name)
            self._publish(UNAVAILABLE)
            return
        self._publish(state_from_snapshot(snapshot))

    async def _follow(self, ski: str) -> None:
        """Refresh on stream events and periodically; returns when the stream ends
        or the paired gateway changes, raises when the stream fails."""
        self._dirty.clear()
        await self.refresh(ski)
        stream = asyncio.create_task(self._consume_events(ski))
        poller = asyncio.create_task(self._poll(ski))
        try:
            done, _ = await asyncio.wait({stream, poller}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (stream, poller):
                task.cancel()
            await asyncio.gather(stream, poller, return_exceptions=True)
        for task in done:
            task.result()

    async def _consume_events(self, ski: str) -> None:
        async for _event in self._bridge.device_state_events(ski):
            self._dirty.set()
        _LOGGER.info("Bridge ended the device state stream, reconnecting")

    async def _poll(self, ski: str) -> None:
        while self._pairing.paired_ski == ski:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._refresh_interval):
                    await self._dirty.wait()
                # Bundle bursts of events into one snapshot read.
                await asyncio.sleep(self._debounce)
            self._dirty.clear()
            await self.refresh(ski)

    def _publish(self, state: HeatPumpState | None) -> None:
        self._state = state
        for deliver in list(self._listeners):
            deliver(state)
