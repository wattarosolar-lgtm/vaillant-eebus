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
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.settings import SettingsStore

_LOGGER = logging.getLogger(__name__)

_SKI_PATTERN = re.compile(r"[0-9A-F]{40}")
_SKI_SEPARATORS = re.compile(r"[\s:-]")

# Seconds between summaries while the gateway keeps refusing connections.
CONNECT_SUMMARY_INTERVAL = 300.0


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
    # mDNS instance name and SHIP id, shown when a device announces no brand/model.
    name: str = ""
    ship_id: str = ""
    # SHIP device categories, e.g. 4 = HVAC.
    categories: list[int] = field(default_factory=list)


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
        log: LogStore | None = None,
    ) -> None:
        """bridge_serial_file holds the EEBUS serial this app's bridge announces;
        None skips the identity check (development against the fake bridge).
        ship_port is checked for another listener while the bridge is down.
        log receives pairing and connection events for the protocol page."""
        self._bridge = bridge
        self._store = store
        self._app_version = app_version
        self._bridge_serial_file = bridge_serial_file
        self._ship_port = ship_port
        self._log = log
        self._connection_log = ConnectionLog(log=log)

    @property
    def paired_ski(self) -> str | None:
        return self._store.current.paired_ski

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
            # A (re)started bridge connects from scratch.
            self._connection_log.reset()
            try:
                async for event in self._bridge.device_events():
                    self._connection_log.record(event)
                _LOGGER.warning("Bridge ended the device event stream, reconnecting")
            except grpc.aio.AioRpcError as err:
                if err.code() not in RETRYABLE_CODES:
                    raise
                _LOGGER.warning("Lost the bridge's device event stream (%s), reconnecting", err.code().name)
            await asyncio.sleep(self._bridge.backoff.initial)

    async def status(self) -> PairingStatus:
        ski = self.paired_ski
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
        paired = await self._paired_gateway(ski, discovered)
        return PairingStatus(
            bridge=bridge,
            state=await self._connection_state(ski),
            paired=paired,
            # Gateways often announce no brand/model over mDNS; the paired one
            # is completed from its device classification.
            discovered=[_with_details_of(gateway, paired) if gateway.ski == ski else gateway for gateway in discovered],
        )

    async def pair(self, ski: str) -> PairingStatus:
        """Pair the gateway with this SKI, replacing a previously paired one."""
        ski = normalize_ski(ski)
        if not _SKI_PATTERN.fullmatch(ski):
            raise ValueError("SKI must be 40 hexadecimal characters")
        await self._require_own_bridge()
        previous = self.paired_ski
        if previous is not None and previous != ski:
            try:
                await self._bridge.unregister_ski(previous)
            except grpc.aio.AioRpcError as err:
                if err.code() is not grpc.StatusCode.NOT_FOUND:
                    raise
        await self._bridge.register_ski(ski)
        self._store.update(lambda settings: replace(settings, paired_ski=ski))
        _LOGGER.info("Paired gateway %s", short_ski(ski))
        if self._log is not None:
            self._log.add(LogCategory.CONNECTION, f"Gateway {short_ski(ski)} gekoppelt.")
        return await self.status()

    async def diagnostic_dump(self) -> DiagnosticDump:
        """Read-only structure dump of the paired gateway, wrapped with app metadata."""
        ski = self.paired_ski
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
        ski = self.paired_ski
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
        name=device.name,
        ship_id=device.ship_id,
        categories=list(device.categories),
    )


def _with_details_of(gateway: Gateway, paired: Gateway) -> Gateway:
    """The discovered gateway, with announced values kept and gaps filled from the paired device."""
    return replace(
        gateway,
        brand=gateway.brand or paired.brand,
        model=gateway.model or paired.model,
        serial=gateway.serial or paired.serial,
        device_type=gateway.device_type or paired.device_type,
        software_revision=gateway.software_revision or paired.software_revision,
        hardware_revision=gateway.hardware_revision or paired.hardware_revision,
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


class ConnectionLog:
    """Logs the gateway's connection events without flooding the log.

    While no connection is established, every failed SHIP attempt arrives as a
    disconnect event; after a start the gateway often refuses attempts for
    minutes. The first failure is logged, the following ones only at debug
    level with a summary every few minutes. Losing an established connection
    is a warning.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        summary_interval: float = CONNECT_SUMMARY_INTERVAL,
        log: LogStore | None = None,
    ) -> None:
        """log receives the same events in German for the protocol page."""
        self._clock = clock
        self._summary_interval = summary_interval
        self._log = log
        self.reset()

    def reset(self) -> None:
        """Start counting again, e.g. after the bridge restarted."""
        self._connected = False
        self._failed_attempts = 0
        self._waiting_since = self._clock()
        self._last_summary = self._waiting_since

    def record(self, event: DeviceEvent) -> None:
        ski = short_ski(normalize_ski(event.ski))
        now = self._clock()
        if event.event_type == DEVICE_EVENT_CONNECTED:
            if self._failed_attempts:
                _LOGGER.info(
                    "Gateway %s connected after %s and %s",
                    ski,
                    _duration(now - self._waiting_since),
                    _failed_attempts(self._failed_attempts),
                )
                self._protocol(
                    f"Gateway {ski} verbunden nach {_german_duration(now - self._waiting_since)} "
                    f"und {_german_attempts(self._failed_attempts)}."
                )
            else:
                _LOGGER.info("Gateway %s connected", ski)
                self._protocol(f"Gateway {ski} verbunden.")
            self._connected = True
            self._failed_attempts = 0
        elif event.event_type == DEVICE_EVENT_DISCONNECTED:
            self._record_disconnect(ski, now)
        elif event.event_type == DEVICE_EVENT_TRUST_REMOVED:
            _LOGGER.warning("Gateway %s withdrew its trust (check the myVAILLANT app)", ski)
            self._protocol(
                f"Gateway {ski} hat das Vertrauen entzogen (bitte in der myVAILLANT-App prüfen).", problem=True
            )
        else:
            _LOGGER.debug("Device event %d for %s", event.event_type, ski)

    def _record_disconnect(self, ski: str, now: float) -> None:
        if self._connected:
            _LOGGER.warning("Gateway %s disconnected, reconnecting", ski)
            self._protocol(f"Verbindung zu Gateway {ski} getrennt; die App verbindet neu.", problem=True)
            self._connected = False
            self._waiting_since = now
            self._last_summary = now
            return
        self._failed_attempts += 1
        if self._failed_attempts == 1:
            _LOGGER.info(
                "Gateway %s refused the connection attempt; retrying (each attempt is logged at level debug)", ski
            )
            self._protocol(f"Gateway {ski} lehnt die Verbindung ab; die App versucht es weiter.", problem=True)
            return
        _LOGGER.debug("Connection attempt %d to gateway %s failed", self._failed_attempts, ski)
        if now - self._last_summary >= self._summary_interval:
            _LOGGER.info(
                "Gateway %s not connected yet: %s in %s, still trying",
                ski,
                _failed_attempts(self._failed_attempts),
                _duration(now - self._waiting_since),
            )
            self._protocol(
                f"Gateway {ski} noch nicht verbunden nach {_german_attempts(self._failed_attempts)} "
                f"in {_german_duration(now - self._waiting_since)}; die App versucht es weiter.",
                problem=True,
            )
            self._last_summary = now

    def _protocol(self, message: str, *, problem: bool = False) -> None:
        if self._log is not None:
            self._log.add(LogCategory.CONNECTION, message, problem=problem)


def _failed_attempts(count: int) -> str:
    return f"{count} failed attempt" if count == 1 else f"{count} failed attempts"


def _duration(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"


def _german_attempts(count: int) -> str:
    return "1 abgelehnten Versuch" if count == 1 else f"{count} abgelehnten Versuchen"


def _german_duration(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest:02d} s" if minutes else f"{rest} s"
