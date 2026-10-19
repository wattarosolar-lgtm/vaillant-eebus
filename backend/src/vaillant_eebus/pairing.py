"""Pairing with the heat pump's EEBUS gateway and the diagnostic dump.

The bridge forgets registered SKIs when it restarts. The backend therefore
stores the paired gateway in the settings and registers it again after every
bridge start, and whenever the bridge reports the device as unknown.

All apps with host networking share the host's ports. Before trusting a bridge
the backend checks that it is this app's own bridge: it must report the EEBUS
serial that this app generated in /data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC
from enum import StrEnum
from pathlib import Path

import grpc
from eebus.v1.device_service_pb2 import (
    DEVICE_EVENT_CONNECTED,
    DEVICE_EVENT_DISCONNECTED,
    DEVICE_EVENT_TRUST_REMOVED,
    DEVICE_READINESS_EXHAUSTED,
    DEVICE_READINESS_UNTRUSTED,
    DeviceEvent,
    DiscoveredDevice,
    PairedDevice,
    ServerInfo,
)

from vaillant_eebus.bridge_client import RETRYABLE_CODES, BridgeClient
from vaillant_eebus.settings import SettingsStore

_LOGGER = logging.getLogger(__name__)

_SKI_PATTERN = re.compile(r"[0-9A-F]{40}")
_SKI_SEPARATORS = re.compile(r"[\s:-]")


class ConnectionState(StrEnum):
    NOT_PAIRED = "not_paired"
    BRIDGE_UNAVAILABLE = "bridge_unavailable"
    # This app's bridge is down and something else holds the EEBUS port,
    # usually another EEBUS app.
    SHIP_PORT_IN_USE = "ship_port_in_use"
    # A bridge answers on this app's gRPC address, but it is not this app's.
    FOREIGN_BRIDGE = "foreign_bridge"
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    # The gateway withdrew its trust, e.g. in the myVAILLANT app.
    TRUST_REMOVED = "trust_removed"
    # The bridge gave up recovering the connection.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Gateway:
    ski: str
    brand: str = ""
    model: str = ""
    serial: str = ""
    device_type: str = ""
    host: str = ""
    software_revision: str = ""
    hardware_revision: str = ""


@dataclass(frozen=True, slots=True)
class BridgeInfo:
    reachable: bool
    ski: str = ""
    build: str = ""


@dataclass(frozen=True, slots=True)
class PairingStatus:
    bridge: BridgeInfo
    state: ConnectionState
    paired: Gateway | None
    discovered: list[Gateway]


@dataclass(frozen=True, slots=True)
class DiagnosticDump:
    filename: str
    content: bytes


class NotPairedError(Exception):
    """No gateway is paired yet."""


class ForeignBridgeError(Exception):
    """The bridge on this app's gRPC address is not this app's own bridge."""


def normalize_ski(ski: str) -> str:
    """Upper-case hex without separators, as the bridge stores SKIs."""
    return _SKI_SEPARATORS.sub("", ski).upper()


def short_ski(ski: str) -> str:
    """Redacted form for logs, matching the bridge's own log format."""
    return "…" + ski[-6:]


class PairingService:
    def __init__(
        self,
        bridge: BridgeClient,
        store: SettingsStore,
        app_version: str,
        *,
        bridge_serial_file: Path | None = None,
        ship_port: int | None = None,
    ) -> None:
        """bridge_serial_file holds the EEBUS serial this app's bridge announces;
        None skips the identity check (development against the fake bridge).
        ship_port is checked for another listener while the bridge is down."""
        self._bridge = bridge
        self._store = store
        self._settings = store.load()
        self._app_version = app_version
        self._bridge_serial_file = bridge_serial_file
        self._ship_port = ship_port

    @property
    def paired_ski(self) -> str | None:
        return self._settings.paired_ski

    async def run(self) -> None:
        """Keep the paired gateway registered with the bridge, across bridge restarts."""
        while True:
            info = await self._bridge.wait_until_ready()
            _LOGGER.info(
                "Bridge reachable at %s: build %s, API %d.%d, local SKI %s",
                self._bridge.address,
                info.bridge_build_version or "unknown",
                info.api_major,
                info.api_minor,
                info.local_ski,
            )
            if not self._is_own_bridge(info):
                _LOGGER.error(
                    "The bridge at %s is not this app's bridge (serial %r). Another EEBUS app is probably running; "
                    "stop it and restart this app. The gateway is not registered with the foreign bridge.",
                    self._bridge.address,
                    info.device_serial,
                )
                await asyncio.sleep(self._bridge.backoff.maximum)
                continue
            await self._register_paired()
            try:
                async for event in self._bridge.device_events():
                    _log_device_event(event)
                _LOGGER.warning("Bridge ended the device event stream, reconnecting")
            except grpc.aio.AioRpcError as err:
                if err.code() not in RETRYABLE_CODES:
                    raise
                _LOGGER.warning("Lost the bridge's device event stream (%s), reconnecting", err.code().name)
            await asyncio.sleep(self._bridge.backoff.initial)

    async def status(self) -> PairingStatus:
        ski = self._settings.paired_ski
        try:
            info = await self._bridge.get_server_info()
        except grpc.aio.AioRpcError as err:
            if err.code() not in RETRYABLE_CODES:
                raise
            in_use = self._ship_port is not None and await _port_accepts_connections(self._ship_port)
            return PairingStatus(
                bridge=BridgeInfo(reachable=False),
                state=ConnectionState.SHIP_PORT_IN_USE if in_use else ConnectionState.BRIDGE_UNAVAILABLE,
                paired=Gateway(ski=ski) if ski else None,
                discovered=[],
            )
        if not self._is_own_bridge(info):
            # Never show the foreign bridge's SKI: the user would trust it in myVAILLANT.
            return PairingStatus(
                bridge=BridgeInfo(reachable=True),
                state=ConnectionState.FOREIGN_BRIDGE,
                paired=Gateway(ski=ski) if ski else None,
                discovered=[],
            )

        bridge = BridgeInfo(reachable=True, ski=info.local_ski, build=info.bridge_build_version)
        discovered = [_gateway_from_discovered(device) for device in await self._bridge.list_discovered()]
        if ski is None:
            return PairingStatus(bridge=bridge, state=ConnectionState.NOT_PAIRED, paired=None, discovered=discovered)
        return PairingStatus(
            bridge=bridge,
            state=await self._connection_state(ski),
            paired=await self._paired_gateway(ski, discovered),
            discovered=discovered,
        )

    async def pair(self, ski: str) -> PairingStatus:
        """Pair the gateway with this SKI, replacing a previously paired one."""
        ski = normalize_ski(ski)
        if not _SKI_PATTERN.fullmatch(ski):
            raise ValueError("SKI must be 40 hexadecimal characters")
        await self._require_own_bridge()
        previous = self._settings.paired_ski
        if previous is not None and previous != ski:
            try:
                await self._bridge.unregister_ski(previous)
            except grpc.aio.AioRpcError as err:
                if err.code() is not grpc.StatusCode.NOT_FOUND:
                    raise
        await self._bridge.register_ski(ski)
        self._settings = replace(self._settings, paired_ski=ski)
        self._store.save(self._settings)
        _LOGGER.info("Paired gateway %s", short_ski(ski))
        return await self.status()

    async def diagnostic_dump(self) -> DiagnosticDump:
        """Read-only structure dump of the paired gateway, wrapped with app metadata."""
        ski = self._settings.paired_ski
        if ski is None:
            raise NotPairedError
        info = await self._require_own_bridge()
        dump = await self._bridge.get_structure_dump(ski)
        gateway = await self._paired_gateway(ski, [])
        captured_at = dump.captured_at.ToDatetime(tzinfo=UTC)
        document = {
            "app_version": self._app_version,
            "bridge_build": info.bridge_build_version,
            "captured_at": captured_at.isoformat(),
            "gateway": {
                "ski": dump.redacted_ski,
                "brand": gateway.brand,
                "model": gateway.model,
                "serial": gateway.serial,
                "device_type": gateway.device_type,
                "software_revision": gateway.software_revision,
                "hardware_revision": gateway.hardware_revision,
            },
            "device": json.loads(dump.json),
        }
        return DiagnosticDump(
            filename=f"vaillant-eebus-diagnose-{captured_at:%Y%m%d-%H%M%S}.json",
            content=(json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )

    async def own_bridge_ready(self) -> bool:
        """True if this app's own bridge answers right now."""
        try:
            await self._require_own_bridge()
        except ForeignBridgeError:
            return False
        except grpc.aio.AioRpcError as err:
            if err.code() not in RETRYABLE_CODES:
                raise
            return False
        return True

    def _is_own_bridge(self, info: ServerInfo) -> bool:
        if self._bridge_serial_file is None:
            return True
        try:
            expected = self._bridge_serial_file.read_text(encoding="utf-8").strip()
        except OSError as err:
            _LOGGER.error("Cannot read this app's bridge serial from %s: %s", self._bridge_serial_file, err)
            return False
        return bool(expected) and info.device_serial == expected

    async def _require_own_bridge(self) -> ServerInfo:
        info = await self._bridge.get_server_info()
        if not self._is_own_bridge(info):
            raise ForeignBridgeError
        return info

    async def _register_paired(self) -> None:
        ski = self._settings.paired_ski
        if ski is None:
            return
        try:
            await self._bridge.register_ski(ski)
        except grpc.aio.AioRpcError as err:
            _LOGGER.warning("Registering paired gateway %s failed (%s)", short_ski(ski), err.code().name)
            return
        _LOGGER.info("Registered paired gateway %s with the bridge", short_ski(ski))

    async def _connection_state(self, ski: str) -> ConnectionState:
        try:
            status = await self._bridge.get_device_status(ski)
        except grpc.aio.AioRpcError as err:
            if err.code() is not grpc.StatusCode.NOT_FOUND:
                raise
            # The bridge restarted and lost the registration before run() renewed it.
            await self._register_paired()
            return ConnectionState.DISCONNECTED
        if status.connected:
            return ConnectionState.CONNECTED
        if status.readiness == DEVICE_READINESS_UNTRUSTED:
            return ConnectionState.TRUST_REMOVED
        if status.readiness == DEVICE_READINESS_EXHAUSTED:
            return ConnectionState.FAILED
        return ConnectionState.DISCONNECTED

    async def _paired_gateway(self, ski: str, discovered: list[Gateway]) -> Gateway:
        host = next((gateway.host for gateway in discovered if gateway.ski == ski), "")
        for device in await self._bridge.list_paired():
            if normalize_ski(device.ski) == ski:
                return _gateway_from_paired(device, host)
        return next((gateway for gateway in discovered if gateway.ski == ski), Gateway(ski=ski))


async def _port_accepts_connections(port: int) -> bool:
    """True if something listens on the loopback port."""
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=1.0)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


def _gateway_from_discovered(device: DiscoveredDevice) -> Gateway:
    return Gateway(
        ski=normalize_ski(device.ski),
        brand=device.brand,
        model=device.model,
        serial=device.serial,
        device_type=device.device_type,
        host=device.host,
    )


def _gateway_from_paired(device: PairedDevice, host: str) -> Gateway:
    return Gateway(
        ski=normalize_ski(device.ski),
        brand=device.brand,
        model=device.model,
        serial=device.serial,
        device_type=device.device_type,
        host=host,
        software_revision=device.software_revision,
        hardware_revision=device.hardware_revision,
    )


def _log_device_event(event: DeviceEvent) -> None:
    ski = short_ski(normalize_ski(event.ski))
    if event.event_type == DEVICE_EVENT_CONNECTED:
        _LOGGER.info("Gateway %s connected", ski)
    elif event.event_type == DEVICE_EVENT_DISCONNECTED:
        _LOGGER.warning("Gateway %s disconnected", ski)
    elif event.event_type == DEVICE_EVENT_TRUST_REMOVED:
        _LOGGER.warning("Gateway %s withdrew its trust (check the myVAILLANT app)", ski)
    else:
        _LOGGER.debug("Device event %d for %s", event.event_type, ski)
