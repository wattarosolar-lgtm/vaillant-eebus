"""The only place that writes to the heat pump and to the radiator thermostats.

Every change - from the UI, the API and the energy management's planner -
goes through WriteGate.submit(). The gate checks the command against what the
device reports (zone, writable flag, limits, step, offered modes). Then:

- preview (the planner's stage "Vorschau"): nothing is sent, in either mode;
  the protocol says what the planner would send.
- demo mode: nothing is sent. Setpoints, modes and boost are answered with
  what would be sent; power limits and compressor control are blocked.
- live mode: at most one command per control every few seconds, values that
  are already set are skipped, and every sent value is followed until the
  device reports it or a timeout passes - also when the bridge's answer got
  lost and it is unknown whether the value arrived.

A caller states the mode it showed the user; a command decided in demo is
never sent because the mode changed in between. Every attempt is in the
protocol before it is sent.

Two ways lead to the heat pump: the EEBUS bridge, or with the connection
myVAILLANT a Link that calls the integration's services in Home Assistant
(myvaillant.py). No other module calls the bridge client's write methods or a
link's send_command(); tests check that.

RoomGate (1.4.0) is the one way to the radiator thermostats of the rooms: it
alone calls Home Assistant's room service (climate.set_temperature) and spares
the valves (dead band, interval, commands a day) whatever the room control
decides.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import tempfile
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import grpc

from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.ha_client import EntityState, HomeAssistantClient, ServiceCallFailed
from vaillant_eebus.hems.sources import HemsSources
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.pairing import NotPairedError, PairingService
from vaillant_eebus.room_data import RoomControlMode, RoomDetails, effective_stage
from vaillant_eebus.settings import Connection, OperatingMode, Room, Settings, SettingsStore
from vaillant_eebus.state import MODE_NAMES, HeatPumpState, HotWater, Setpoint, StateHub, Zone

_LOGGER = logging.getLogger(__name__)

# Spec 9: at most one write per control within this many seconds.
RATE_LIMIT_S = 5.0
# A sent value counts as not confirmed when the device does not report it in time.
CONFIRM_TIMEOUT_S = 30.0
# Through the Vaillant cloud a value takes longer to come back.
LINK_CONFIRM_TIMEOUT_S = 90.0
# After a value sent over myVAILLANT was confirmed, the integration's refresh may still bring
# the cloud's old value for a moment; for this long that is no change at the device.
LINK_SETTLE_S = 20.0
# Hours a temporary override (quick veto) may last.
OVERRIDE_HOURS = (1.0, 24.0)
# What ending an override sets: the zone follows its time program again.
TIME_PROGRAM = "time_program"

# Errors after which the value certainly did not reach the device: the bridge
# refused the request before sending it, or the device answered with a
# rejection. Any other error (timeout, internal error, lost connection) leaves
# open whether the value arrived.
_NOT_EXECUTED = frozenset(
    {
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.FAILED_PRECONDITION,
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.OUT_OF_RANGE,
        grpc.StatusCode.UNIMPLEMENTED,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNAUTHENTICATED,
    }
)

_COMPRESSOR_ACTIONS = {"schedule": "einplanen", "pause": "pausieren", "resume": "fortsetzen", "abort": "abbrechen"}

type JsonScalar = str | bool | int | float | None


class Source(StrEnum):
    """Who gives a command; the protocol names the planner."""

    UI = "ui"
    PLANNER = "planner"


class Control(StrEnum):
    ZONE_SETPOINT = "zone_setpoint"
    ZONE_MODE = "zone_mode"
    DHW_SETPOINT = "dhw_setpoint"
    DHW_MODE = "dhw_mode"
    DHW_BOOST = "dhw_boost"
    CONSUMPTION_LIMIT = "consumption_limit"
    FAILSAFE_LIMIT = "failsafe_limit"
    COMPRESSOR = "compressor"


# Controls demo mode blocks instead of previewing (spec 9).
_BLOCKED_IN_DEMO = frozenset({Control.CONSUMPTION_LIMIT, Control.FAILSAFE_LIMIT, Control.COMPRESSOR})


@dataclass(frozen=True, slots=True)
class SetZoneSetpoint:
    zone_id: str
    celsius: float


@dataclass(frozen=True, slots=True)
class SetZoneOverride:
    """myVAILLANT, zone in auto: a temporary setpoint (quick veto) for some hours."""

    zone_id: str
    celsius: float
    hours: float


@dataclass(frozen=True, slots=True)
class EndZoneOverride:
    """myVAILLANT: end a zone's temporary override; the zone follows its time program again."""

    zone_id: str


@dataclass(frozen=True, slots=True)
class SetZoneMode:
    zone_id: str
    mode: str


@dataclass(frozen=True, slots=True)
class SetHotWaterSetpoint:
    celsius: float


@dataclass(frozen=True, slots=True)
class SetHotWaterMode:
    mode: str


@dataclass(frozen=True, slots=True)
class SetHotWaterBoost:
    active: bool


@dataclass(frozen=True, slots=True)
class SetConsumptionLimit:
    watts: float
    duration_s: int
    active: bool


@dataclass(frozen=True, slots=True)
class SetFailsafeLimit:
    watts: float
    duration_s: int


@dataclass(frozen=True, slots=True)
class ControlCompressor:
    # schedule, pause, resume or abort
    action: str
    # Only for schedule; None starts at once.
    start_time: datetime | None = None


type Command = (
    SetZoneSetpoint
    | SetZoneOverride
    | EndZoneOverride
    | SetZoneMode
    | SetHotWaterSetpoint
    | SetHotWaterMode
    | SetHotWaterBoost
    | SetConsumptionLimit
    | SetFailsafeLimit
    | ControlCompressor
)


@dataclass(frozen=True, slots=True)
class WriteResult:
    sent: bool
    mode: OperatingMode
    # German, e.g. "Würde senden: Zone 2: Solltemperatur 22,5 °C".
    message: str
    # What would have been sent; demo mode only.
    would_send: dict[str, JsonScalar] | None = None


@dataclass(frozen=True, slots=True)
class PendingWrite:
    """A sent value the device has not reported yet ("wird gesendet …")."""

    control: Control
    zone_id: str | None
    value: float | str | bool
    # German description, e.g. "Zone 2: Solltemperatur 22,5 °C".
    message: str
    since: datetime


class WriteOutcomeUnknown(Exception):
    """The command was sent, but whether it reached the device is unknown.

    The gate keeps following the value like any sent value.
    """

    def __init__(self, code: grpc.StatusCode | str) -> None:
        self.code = code
        # e.g. "INTERNAL" (bridge) or "HTTP 500" (Home Assistant).
        self.code_name = code if isinstance(code, str) else code.name
        super().__init__(self.code_name)


class WriteNotExecuted(Exception):
    """The command certainly did not reach the heat pump (a link's error; the
    bridge's errors stay gRPC errors)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Link(Protocol):
    """A way to the heat pump other than the EEBUS bridge (myVAILLANT)."""

    @property
    def connection(self) -> Connection: ...

    def check(self, command: Command, heat_pump: HeatPumpState) -> None:
        """Raise WriteRejected if this way cannot carry the command."""

    async def send_command(self, command: Command) -> None:
        """Send the command; raises ServiceCallFailed."""


class WriteRejected(Exception):
    """The gate refused a command; nothing was sent."""

    def __init__(self, status: int, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class _Observed:
    # Comparable form of the device's value, matching _target() of a command.
    value: Hashable
    # German, e.g. "Zone 2: Solltemperatur 23,0 °C".
    text: str
    # A zone in auto without an override: its setpoint follows the time program.
    program: bool = False


@dataclass(slots=True)
class _Pending:
    write: PendingWrite
    target: Hashable
    sent_at: float
    # The device's value before the command; myVAILLANT may fall back to it for a moment.
    before: Hashable | None = None
    # myVAILLANT: the service call has not returned yet.
    in_flight: bool = False
    # myVAILLANT: when the device reported the value; kept LINK_SETTLE_S longer.
    confirmed_at: float | None = None


type _Key = tuple[Control, str | None]


class WriteGate:
    def __init__(
        self,
        bridge: BridgeClient,
        pairing: PairingService,
        state: StateHub,
        settings: SettingsStore,
        log: LogStore,
        *,
        link: Link | None = None,
        rate_limit_s: float = RATE_LIMIT_S,
        confirm_timeout_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Without link the gate writes over EEBUS through the bridge."""
        self._bridge = bridge
        self._pairing = pairing
        self._link = link
        self._state = state
        self._settings = settings
        self._log = log
        self._rate_limit_s = rate_limit_s
        if confirm_timeout_s is None:
            confirm_timeout_s = CONFIRM_TIMEOUT_S if link is None else LINK_CONFIRM_TIMEOUT_S
        self._confirm_timeout_s = confirm_timeout_s
        self._clock = clock
        self._locks: dict[_Key, asyncio.Lock] = {}
        self._last_sent: dict[_Key, float] = {}
        self._pending: dict[_Key, _Pending] = {}
        # Sent values that timed out while the device was unreachable; resolved
        # by the first state after the connection is back.
        self._unresolved: dict[_Key, _Pending] = {}
        self._last_values: dict[_Key, _Observed] = {}
        self._device_reachable = False

    @property
    def mode(self) -> OperatingMode:
        return self._settings.current.operating_mode

    @property
    def connection(self) -> Connection:
        """The way this gate writes; fixed while the app runs."""
        return Connection.EEBUS if self._link is None else self._link.connection

    @property
    def pending(self) -> list[PendingWrite]:
        # A confirmed value counts again while the cloud shows its old one for a moment.
        # Ending an override shows as "end_override".
        return [
            pending.write
            for key, pending in self._pending.items()
            if pending.confirmed_at is None or not self._shows(key, pending.target)
        ]

    def _shows(self, key: _Key, target: Hashable) -> bool:
        observed = self._last_values.get(key)
        return observed is not None and _reached(target, observed)

    def settling(self, control: Control, zone_id: str | None = None) -> bool:
        """A value sent over myVAILLANT is not confirmed yet.

        The integration shows a sent value at once and may then fall back to the
        cloud's old value for a moment, so until the value is confirmed (or not
        confirmed in time) the device's value says nothing about outside changes.
        """
        return self._link is not None and (control, zone_id) in self._pending

    def set_mode(self, mode: OperatingMode, *, confirm: bool) -> OperatingMode:
        """Switch between demo and live; live needs the user's explicit confirmation."""
        if mode is OperatingMode.LIVE and not confirm:
            raise WriteRejected(400, "Der Live-Betrieb muss ausdrücklich bestätigt werden.")
        if mode is self.mode:
            return mode
        self._settings.update(lambda settings: replace(settings, operating_mode=mode))
        if mode is OperatingMode.LIVE:
            _LOGGER.warning("Live mode on: changes are sent to the heat pump")
            self._log.add(LogCategory.MODE, "Live-Betrieb eingeschaltet: Änderungen werden an die Wärmepumpe gesendet.")
        else:
            _LOGGER.info("Demo mode on: nothing is sent to the heat pump")
            self._log.add(LogCategory.MODE, "Demo-Betrieb eingeschaltet: Änderungen werden nicht gesendet.")
        self._state.republish()
        return mode

    async def submit(
        self,
        command: Command,
        *,
        expected_mode: OperatingMode | None = None,
        source: Source = Source.UI,
        preview: bool = False,
    ) -> WriteResult:
        """Check the command and, in live mode, send it to the heat pump.

        expected_mode is the mode the caller showed the user when the change was
        made; if the mode changed since, nothing is sent. preview checks the
        command and sends nothing, in either mode. Raises WriteRejected when the
        command is not allowed, NotPairedError without a paired gateway,
        WriteOutcomeUnknown when it is unknown whether a sent value arrived, and
        the bridge's gRPC error when the value certainly did not arrive.
        """
        key = _key(command)
        async with self._locks.setdefault(key, asyncio.Lock()):
            ski = self._pairing.paired_ski
            if self._link is None and ski is None:
                raise NotPairedError
            heat_pump = self._state.current
            description = _describe(command, heat_pump, self.connection)
            if source is Source.PLANNER and not preview:
                description += " (Planer)"
            try:
                if expected_mode is not None and expected_mode is not self.mode:
                    now_named = "Live" if self.mode is OperatingMode.LIVE else "Demo"
                    raise WriteRejected(
                        409,
                        f"Der Betrieb wurde inzwischen auf {now_named} umgestellt; die Änderung wurde nicht "
                        "gesendet. Bitte neu einstellen.",
                    )
                if heat_pump is None or not heat_pump.available or not heat_pump.connected:
                    raise WriteRejected(409, "Die Wärmepumpe ist gerade nicht verbunden.")
                _validate(command, heat_pump, self.connection)
                if self._link is not None:
                    self._link.check(command, heat_pump)
            except WriteRejected as rejected:
                if preview:
                    self._log.add(
                        LogCategory.HEMS, f"Planer abgelehnt: {description}. {rejected.message}", problem=True
                    )
                else:
                    self._log.add(LogCategory.WRITE, f"Abgelehnt: {description}. {rejected.message}", problem=True)
                raise
            if preview:
                message = f"Planer würde senden: {description}"
                self._log.add(LogCategory.HEMS, message)
                return WriteResult(sent=False, mode=self.mode, message=message, would_send=_payload(command))
            if self.mode is OperatingMode.DEMO:
                return self._demo(command, description)
            return await self._send(command, key, ski, heat_pump, description)

    async def run(self) -> None:
        """Confirm sent values from the device's state and record outside changes."""
        _LOGGER.info("Operating mode: %s", self.mode.value)
        self._log.add(
            LogCategory.MODE,
            "App gestartet im Live-Betrieb." if self.mode is OperatingMode.LIVE else "App gestartet im Demo-Betrieb.",
        )
        async with self._state.listen() as updates:
            while True:
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(self._seconds_to_next_expiry()):
                        self._observe(await updates.get())
                self._expire()

    # --- internals -------------------------------------------------------------

    def _demo(self, command: Command, description: str) -> WriteResult:
        if _key(command)[0] in _BLOCKED_IN_DEMO:
            message = f"Im Demo-Betrieb gesperrt: {description}"
            self._log.add(LogCategory.WRITE, message)
            return WriteResult(sent=False, mode=OperatingMode.DEMO, message=message)
        message = f"Würde senden: {description}"
        self._log.add(LogCategory.WRITE, message)
        return WriteResult(sent=False, mode=OperatingMode.DEMO, message=message, would_send=_payload(command))

    async def _send(
        self, command: Command, key: _Key, ski: str | None, heat_pump: HeatPumpState, description: str
    ) -> WriteResult:
        target = _target(command)
        current = _device_values(heat_pump).get(key)
        pending = self._pending.get(key)
        # An override also sets how long it lasts, which the device's value does not show.
        overrides = isinstance(command, SetZoneOverride | EndZoneOverride)
        in_place = (current is not None and current.value == target) or (
            pending is not None and pending.target == target
        )
        if target is not None and not overrides and in_place:
            message = f"Nicht gesendet, bereits eingestellt: {description}"
            self._log.add(LogCategory.WRITE, message)
            return WriteResult(sent=False, mode=OperatingMode.LIVE, message=message)

        now = self._clock()
        last = self._last_sent.get(key)
        if last is not None and now - last < self._rate_limit_s:
            wait = self._rate_limit_s - (now - last)
            message = (
                f"Bitte noch {math.ceil(wait)} s warten; je Einstellung wird höchstens alle "
                f"{self._rate_limit_s:.0f} s gesendet."
            )
            self._log.add(LogCategory.WRITE, f"Abgelehnt: {description}. Zu schnell nacheinander.", problem=True)
            raise WriteRejected(429, message, retry_after=wait)
        self._last_sent[key] = now
        before = current.value if current is not None else None

        # In the database before the bridge sees the command, so even a crash
        # of the app in the middle of a write leaves a trace.
        await self._log.record(LogCategory.WRITE, f"Wird gesendet: {description}")
        if self._link is not None:
            # The myVAILLANT integration shows the new value while the call still
            # runs: known beforehand, it is not mistaken for an outside change.
            self._pending[key] = _Pending(
                write=_pending_write(command, key, description),
                target=target,
                sent_at=now,
                before=before,
                in_flight=True,
            )
            self._state.republish()
        try:
            if self._link is not None:
                await self._link.send_command(command)
            else:
                assert ski is not None
                await _call_bridge(self._bridge, ski, command)
        except ServiceCallFailed as err:
            if err.executed is False:
                self._drop_in_flight(key)
                _LOGGER.warning("Write not executed (%s): %s", err, description)
                self._log.add(LogCategory.WRITE, f"Nicht ausgeführt: {description} ({err.code})", problem=True)
                raise WriteNotExecuted(err.code) from err
            _LOGGER.warning("Write outcome unknown (%s): %s", err, description)
            self._log.add(
                LogCategory.WRITE,
                f"Ergebnis unbekannt: {description} ({err.code}); die App prüft, ob die Wärmepumpe den Wert meldet.",
                problem=True,
            )
            self._follow(command, key, target, description, now, before=before)
            raise WriteOutcomeUnknown(err.code) from err
        except grpc.aio.AioRpcError as err:
            code = err.code()
            if code in _NOT_EXECUTED:
                _LOGGER.warning("Write not executed (%s): %s", code.name, description)
                self._log.add(
                    LogCategory.WRITE, f"Nicht ausgeführt: {description} (Fehlercode {code.name})", problem=True
                )
                raise
            _LOGGER.warning("Write outcome unknown (%s): %s", code.name, description)
            self._log.add(
                LogCategory.WRITE,
                f"Ergebnis unbekannt: {description} (Fehlercode {code.name}); die App prüft, ob das Gerät den "
                "Wert meldet.",
                problem=True,
            )
            self._follow(command, key, target, description, now)
            raise WriteOutcomeUnknown(code) from err
        except BaseException:
            # Refused before sending, or the app stops: nothing left to follow.
            self._drop_in_flight(key)
            raise
        _LOGGER.info("Sent: %s", description)
        message = f"Gesendet: {description}"
        self._log.add(LogCategory.WRITE, message)
        self._follow(command, key, target, description, now, before=before)
        return WriteResult(sent=True, mode=OperatingMode.LIVE, message=message)

    def _follow(
        self,
        command: Command,
        key: _Key,
        target: Hashable | None,
        description: str,
        sent_at: float,
        *,
        before: Hashable | None = None,
    ) -> None:
        """Watch for the device to report a sent value."""
        self._unresolved.pop(key, None)
        if target is not None:
            self._pending[key] = _Pending(
                write=_pending_write(command, key, description),
                target=target,
                sent_at=sent_at,
                before=before,
            )
            self._state.republish()
        else:
            self._drop_in_flight(key)
        self._state.refresh_now()

    def _drop_in_flight(self, key: _Key) -> None:
        pending = self._pending.get(key)
        if pending is not None and pending.in_flight:
            del self._pending[key]
            self._state.republish()

    def _observe(self, heat_pump: HeatPumpState | None) -> None:
        self._device_reachable = heat_pump is not None and heat_pump.available and heat_pump.connected
        if heat_pump is None or not self._device_reachable:
            return
        changed = False
        for key, observed in _device_values(heat_pump).items():
            pending = self._pending.get(key)
            if pending is not None and pending.in_flight:
                # myVAILLANT shows the value being sent at once and may fall back to the
                # cloud's old value; what counts is the state once the call returned.
                self._last_values[key] = observed
                continue
            unresolved = self._unresolved.pop(key, None)
            previous = self._last_values.get(key)
            if pending is not None and _reached(pending.target, observed):
                if pending.confirmed_at is None:
                    changed = True
                    text = f"{observed.text} (Zeitprogramm)" if pending.target == TIME_PROGRAM else observed.text
                    self._log.add(LogCategory.WRITE, f"Bestätigt vom Gerät: {text}")
                    if self._link is not None:
                        # The cloud may still send its old value for a moment; see _observe below.
                        pending.confirmed_at = self._clock()
                    else:
                        del self._pending[key]
                elif previous is None or not _reached(pending.target, previous):
                    # Back after the cloud's old value: no longer shown as pending.
                    changed = True
            elif unresolved is not None:
                if _reached(unresolved.target, observed):
                    self._log.add(LogCategory.WRITE, f"Nachträglich bestätigt vom Gerät: {observed.text}")
                else:
                    self._log.add(
                        LogCategory.WRITE,
                        f"Nicht übernommen: {unresolved.write.message}; das Gerät meldet {_value_part(observed)}",
                        problem=True,
                    )
            elif previous is not None and previous.value != observed.value:
                if self.connection is Connection.MYVAILLANT and previous.program and observed.program:
                    # The time program moved on; nobody changed anything.
                    self._last_values[key] = observed
                    continue
                if self._link is not None and pending is not None and observed.value == pending.before:
                    # The cloud still reports the value from before the command (also just after it
                    # was confirmed: the integration's refresh may arrive after the call's answer).
                    self._last_values[key] = observed
                    # A confirmed value shows as pending again meanwhile.
                    changed = changed or pending.confirmed_at is not None
                    continue
                if pending is not None and pending.confirmed_at is not None:
                    # A real change after the confirmation ends the wait for the cloud.
                    del self._pending[key]
                self._log.add(
                    LogCategory.DEVICE, f"Am Gerät geändert: {observed.text} (vorher {_value_part(previous)})"
                )
            self._last_values[key] = observed
        if changed:
            self._state.republish()

    def _deadline(self, pending: _Pending) -> float | None:
        """When a followed value stops being followed; None while its call still runs."""
        if pending.in_flight:
            return None
        if pending.confirmed_at is not None:
            return pending.confirmed_at + LINK_SETTLE_S
        return pending.sent_at + self._confirm_timeout_s

    def _seconds_to_next_expiry(self) -> float | None:
        deadlines = [
            deadline for pending in self._pending.values() if (deadline := self._deadline(pending)) is not None
        ]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - self._clock())

    def _expire(self) -> None:
        now = self._clock()
        settled = [
            key
            for key, pending in self._pending.items()
            if pending.confirmed_at is not None and now >= pending.confirmed_at + LINK_SETTLE_S
        ]
        for key in settled:
            del self._pending[key]
        expired = [
            key
            for key, pending in self._pending.items()
            if not pending.in_flight
            and pending.confirmed_at is None
            and now - pending.sent_at >= self._confirm_timeout_s
        ]
        for key in expired:
            pending = self._pending.pop(key)
            timed_out = f"Nicht bestätigt nach {self._confirm_timeout_s:.0f} s: {pending.write.message}"
            if not self._device_reachable:
                # The last known value is from before the connection broke.
                self._unresolved[key] = pending
                self._log.add(
                    LogCategory.WRITE,
                    f"{timed_out}; die Verbindung zum Gerät ist unterbrochen, das Ergebnis ist unbekannt.",
                    problem=True,
                )
                continue
            reported = self._last_values.get(key)
            still = f"; das Gerät meldet weiterhin {_value_part(reported)}" if reported else ""
            self._log.add(LogCategory.WRITE, f"{timed_out}{still}", problem=True)
        if expired:
            self._state.republish()


# --- per-command logic ---------------------------------------------------------


def _key(command: Command) -> _Key:
    match command:
        case SetZoneSetpoint(zone_id=zone_id) | SetZoneOverride(zone_id=zone_id) | EndZoneOverride(zone_id=zone_id):
            return Control.ZONE_SETPOINT, zone_id
        case SetZoneMode(zone_id=zone_id):
            return Control.ZONE_MODE, zone_id
        case SetHotWaterSetpoint():
            return Control.DHW_SETPOINT, None
        case SetHotWaterMode():
            return Control.DHW_MODE, None
        case SetHotWaterBoost():
            return Control.DHW_BOOST, None
        case SetConsumptionLimit():
            return Control.CONSUMPTION_LIMIT, None
        case SetFailsafeLimit():
            return Control.FAILSAFE_LIMIT, None
        case ControlCompressor():
            return Control.COMPRESSOR, None


def _target(command: Command) -> Hashable | None:
    """The command's value in the comparable form of _device_values(); None if the
    device reports nothing to confirm it with."""
    match command:
        case SetZoneSetpoint(celsius=celsius) | SetZoneOverride(celsius=celsius) | SetHotWaterSetpoint(celsius=celsius):
            return round(celsius, 3)
        case EndZoneOverride():
            # The zone returns to its time program, whose value the app does not know beforehand.
            return TIME_PROGRAM
        case SetZoneMode(mode=mode) | SetHotWaterMode(mode=mode):
            return mode
        case SetHotWaterBoost(active=active):
            return active
        case SetConsumptionLimit(watts=watts, active=active):
            return round(watts, 3), active
        case SetFailsafeLimit(watts=watts, duration_s=duration_s):
            return round(watts, 3), duration_s
        case ControlCompressor():
            return None


def _reached(target: Hashable, observed: _Observed) -> bool:
    """The device reports the value a command set: equal, or for an ended override the time program."""
    return observed.program if target == TIME_PROGRAM else observed.value == target


def _device_values(heat_pump: HeatPumpState) -> dict[_Key, _Observed]:
    """The device's current value of every control it reports."""
    values: dict[_Key, _Observed] = {}
    for zone in heat_pump.zones:
        if zone.setpoint is not None:
            values[Control.ZONE_SETPOINT, zone.id] = _Observed(
                round(zone.setpoint.value, 3),
                f"{zone.label}: Solltemperatur {_celsius(zone.setpoint.value, zone.setpoint)}",
                program=zone.mode == "auto" and not zone.override,
            )
        if zone.mode is not None:
            values[Control.ZONE_MODE, zone.id] = _Observed(
                zone.mode, f"{zone.label}: Betriebsart {_mode_name(zone.mode)}"
            )
    water = heat_pump.hot_water
    if water is not None:
        if water.setpoint is not None:
            values[Control.DHW_SETPOINT, None] = _Observed(
                round(water.setpoint.value, 3),
                f"Warmwasser: Solltemperatur {_celsius(water.setpoint.value, water.setpoint)}",
            )
        if water.mode is not None:
            values[Control.DHW_MODE, None] = _Observed(water.mode, f"Warmwasser: Betriebsart {_mode_name(water.mode)}")
        if water.boost is not None:
            active = water.boost in ("active", "running")
            values[Control.DHW_BOOST, None] = _Observed(
                active, f"Warmwasser: Einmalladung {'ein' if active else 'aus'}"
            )
    limit = heat_pump.consumption_limit
    if limit is not None:
        values[Control.CONSUMPTION_LIMIT, None] = _Observed(
            (round(limit.value_w, 3), limit.active),
            f"Leistungsdeckel {_watts(limit.value_w)}, {'aktiv' if limit.active else 'nicht aktiv'}",
        )
    failsafe = heat_pump.failsafe_limit
    if failsafe is not None:
        values[Control.FAILSAFE_LIMIT, None] = _Observed(
            (round(failsafe.value_w, 3), failsafe.duration_s),
            f"Failsafe-Grenze {_watts(failsafe.value_w)}, mindestens {_duration(failsafe.duration_s)}",
        )
    return values


def _validate(command: Command, heat_pump: HeatPumpState, connection: Connection) -> None:
    myvaillant = connection is Connection.MYVAILLANT
    match command:
        case SetZoneSetpoint(zone_id=zone_id, celsius=celsius):
            zone = _zone(heat_pump, zone_id)
            # In auto the zone follows its time program, which is not exposed
            # over EEBUS; the setpoint only takes effect in "on" (field test 0.5.1).
            # myVAILLANT starts a temporary override instead.
            if zone.mode == "auto" and not myvaillant:
                raise WriteRejected(
                    409,
                    f"Im Auto-Modus folgt {zone.label} dem Zeitprogramm; die Solltemperatur wirkt nur in der "
                    "Betriebsart Ein. Für einen Boost myVAILLANT oder Home Assistant nutzen.",
                )
            # myVAILLANT would start an override that heats the switched-off zone.
            if zone.mode == "off" and myvaillant:
                raise WriteRejected(
                    409, f"{zone.label} ist ausgeschaltet; die Solltemperatur wirkt erst in Ein oder Auto."
                )
            _check_setpoint(zone.setpoint, celsius, zone.label)
        case SetZoneOverride(zone_id=zone_id, celsius=celsius, hours=hours):
            if not myvaillant:
                raise WriteRejected(409, "Eine zeitweise Übersteuerung gibt es nur mit der Anbindung myVAILLANT.")
            zone = _zone(heat_pump, zone_id)
            if zone.mode != "auto":
                raise WriteRejected(409, f"{zone.label}: Eine zeitweise Übersteuerung gibt es nur im Auto-Betrieb.")
            if not OVERRIDE_HOURS[0] <= hours <= OVERRIDE_HOURS[1]:
                raise WriteRejected(422, "Eine Übersteuerung dauert 1 bis 24 Stunden.")
            _check_setpoint(zone.setpoint, celsius, zone.label)
        case EndZoneOverride(zone_id=zone_id):
            if not myvaillant:
                raise WriteRejected(409, "Eine zeitweise Übersteuerung gibt es nur mit der Anbindung myVAILLANT.")
            _zone(heat_pump, zone_id)
        case SetZoneMode(zone_id=zone_id, mode=mode):
            zone = _zone(heat_pump, zone_id)
            _check_mode(zone.mode_writable, zone.available_modes, mode, zone.label)
        case SetHotWaterSetpoint(celsius=celsius):
            _check_setpoint(_hot_water(heat_pump).setpoint, celsius, "Warmwasser")
        case SetHotWaterMode(mode=mode):
            water = _hot_water(heat_pump)
            _check_mode(water.mode_writable, water.available_modes, mode, "Warmwasser")
        case SetHotWaterBoost():
            water = _hot_water(heat_pump)
            if water.boost is None or not water.boost_writable:
                raise WriteRejected(409, "Die Einmalladung ist am Gerät nicht schaltbar.")
        case SetConsumptionLimit() | SetFailsafeLimit() | ControlCompressor() if myvaillant:
            raise WriteRejected(409, "Leistungsdeckel, Failsafe und Verdichter gibt es nur mit der Anbindung EEBUS.")
        case SetConsumptionLimit(watts=watts, duration_s=duration_s):
            limit = heat_pump.consumption_limit
            if limit is None:
                raise WriteRejected(409, "Das Gerät meldet keinen Leistungsdeckel.")
            if not limit.changeable:
                raise WriteRejected(409, "Der Leistungsdeckel ist am Gerät nicht änderbar.")
            _check_non_negative(watts, duration_s)
        case SetFailsafeLimit(watts=watts, duration_s=duration_s):
            if heat_pump.failsafe_limit is None:
                raise WriteRejected(409, "Das Gerät meldet keine Failsafe-Grenze.")
            _check_non_negative(watts, duration_s)
        case ControlCompressor(action=action, start_time=start_time):
            if heat_pump.compressor_flexibility is None:
                raise WriteRejected(409, "Das Gerät meldet keine Verdichter-Flexibilität.")
            if action not in _COMPRESSOR_ACTIONS:
                raise WriteRejected(422, f"Unbekannte Aktion „{action}“.")
            if start_time is not None and action != "schedule":
                raise WriteRejected(422, "Eine Startzeit gibt es nur beim Einplanen.")


def _find_zone(heat_pump: HeatPumpState | None, zone_id: str) -> Zone | None:
    if heat_pump is None:
        return None
    return next((zone for zone in heat_pump.zones if zone.id == zone_id), None)


def _zone(heat_pump: HeatPumpState, zone_id: str) -> Zone:
    zone = _find_zone(heat_pump, zone_id)
    if zone is None:
        raise WriteRejected(404, f"Die Heizzone {zone_id} ist dem Gerät nicht bekannt.")
    return zone


def _hot_water(heat_pump: HeatPumpState) -> HotWater:
    if heat_pump.hot_water is None:
        raise WriteRejected(409, "Das Gerät meldet kein Warmwasser.")
    return heat_pump.hot_water


def _check_setpoint(setpoint: Setpoint | None, celsius: float, label: str) -> None:
    if setpoint is None:
        raise WriteRejected(409, f"{label}: Das Gerät meldet keine Solltemperatur.")
    if not setpoint.writable:
        raise WriteRejected(409, f"{label}: Die Solltemperatur ist am Gerät nicht änderbar.")
    if setpoint.step <= 0:
        # Without the device's step the value cannot be checked: fail closed.
        raise WriteRejected(409, f"{label}: Das Gerät meldet keine Schrittweite der Solltemperatur.")
    if not setpoint.min <= celsius <= setpoint.max:
        raise WriteRejected(
            422,
            f"{label}: Die Solltemperatur muss zwischen {_celsius(setpoint.min, setpoint)} "
            f"und {_celsius(setpoint.max, setpoint)} liegen.",
        )
    steps = (celsius - setpoint.min) / setpoint.step
    if abs(steps - round(steps)) > 1e-6:
        raise WriteRejected(
            422,
            f"{label}: Die Solltemperatur ist nur in Schritten von {_number(setpoint.step, setpoint)} °C einstellbar.",
        )


def _check_mode(writable: bool, available: list[str], mode: str, label: str) -> None:
    if not writable:
        raise WriteRejected(409, f"{label}: Die Betriebsart ist am Gerät nicht änderbar.")
    if mode not in available:
        raise WriteRejected(422, f"{label}: Die Betriebsart „{mode}“ bietet das Gerät nicht an.")


def _check_non_negative(watts: float, duration_s: int) -> None:
    if watts < 0 or duration_s < 0:
        raise WriteRejected(422, "Leistung und Dauer dürfen nicht negativ sein.")


def _describe(command: Command, heat_pump: HeatPumpState | None, connection: Connection = Connection.EEBUS) -> str:
    """German description of the command, also for unknown zones and unknown state."""
    match command:
        case SetZoneSetpoint(zone_id=zone_id, celsius=celsius):
            zone = _find_zone(heat_pump, zone_id)
            label = zone.label if zone else f"Heizzone {zone_id}"
            text = f"{label}: Solltemperatur {_celsius(celsius, zone.setpoint if zone else None)}"
            if connection is Connection.MYVAILLANT and zone is not None and zone.mode == "auto":
                text += " als zeitweise Übersteuerung"
            return text
        case SetZoneOverride(zone_id=zone_id, celsius=celsius, hours=hours):
            zone = _find_zone(heat_pump, zone_id)
            label = zone.label if zone else f"Heizzone {zone_id}"
            return f"{label}: Übersteuerung {_celsius(celsius, zone.setpoint if zone else None)} für {_hours(hours)}"
        case EndZoneOverride(zone_id=zone_id):
            zone = _find_zone(heat_pump, zone_id)
            return f"{zone.label if zone else f'Heizzone {zone_id}'}: Übersteuerung beenden"
        case SetZoneMode(zone_id=zone_id, mode=mode):
            zone = _find_zone(heat_pump, zone_id)
            return f"{zone.label if zone else f'Heizzone {zone_id}'}: Betriebsart {_mode_name(mode)}"
        case SetHotWaterSetpoint(celsius=celsius):
            water = heat_pump.hot_water if heat_pump else None
            return f"Warmwasser: Solltemperatur {_celsius(celsius, water.setpoint if water else None)}"
        case SetHotWaterMode(mode=mode):
            return f"Warmwasser: Betriebsart {_mode_name(mode)}"
        case SetHotWaterBoost(active=active):
            return f"Warmwasser: Einmalladung {'ein' if active else 'aus'}"
        case SetConsumptionLimit(watts=watts, duration_s=duration_s, active=active):
            state = "aktiv" if active else "nicht aktiv"
            return f"Leistungsdeckel {_watts(watts)} für {_duration(duration_s)}, {state}"
        case SetFailsafeLimit(watts=watts, duration_s=duration_s):
            return f"Failsafe-Grenze {_watts(watts)}, mindestens {_duration(duration_s)}"
        case ControlCompressor(action=action, start_time=start_time):
            when = f" ab {start_time:%d.%m.%Y %H:%M} UTC" if start_time else ""
            return f"Verdichter-Flexibilität {_COMPRESSOR_ACTIONS.get(action, action)}{when}"


def _payload(command: Command) -> dict[str, JsonScalar]:
    control, zone_id = _key(command)
    payload: dict[str, JsonScalar] = {"control": control.value}
    if zone_id is not None:
        payload["zone_id"] = zone_id
    match command:
        case SetZoneSetpoint(celsius=celsius) | SetHotWaterSetpoint(celsius=celsius):
            payload["value"] = celsius
        case SetZoneOverride(celsius=celsius, hours=hours):
            payload.update(value=celsius, hours=hours)
        case EndZoneOverride():
            payload["action"] = "end_override"
        case SetZoneMode(mode=mode) | SetHotWaterMode(mode=mode):
            payload["mode"] = mode
        case SetHotWaterBoost(active=active):
            payload["active"] = active
        case SetConsumptionLimit(watts=watts, duration_s=duration_s, active=active):
            payload.update(value_watts=watts, duration_seconds=duration_s, active=active)
        case SetFailsafeLimit(watts=watts, duration_s=duration_s):
            payload.update(value_watts=watts, duration_minimum_seconds=duration_s)
        case ControlCompressor(action=action, start_time=start_time):
            payload.update(action=action, start_time=start_time.isoformat() if start_time else None)
    return payload


def _pending_write(command: Command, key: _Key, description: str) -> PendingWrite:
    return PendingWrite(
        control=key[0],
        zone_id=key[1],
        value=_primary_value(command),
        message=description,
        since=datetime.now(UTC),
    )


def _primary_value(command: Command) -> float | str | bool:
    match command:
        case SetZoneSetpoint(celsius=celsius) | SetZoneOverride(celsius=celsius) | SetHotWaterSetpoint(celsius=celsius):
            return celsius
        case EndZoneOverride():
            return "end_override"
        case SetZoneMode(mode=mode) | SetHotWaterMode(mode=mode):
            return mode
        case SetHotWaterBoost(active=active):
            return active
        case SetConsumptionLimit(watts=watts) | SetFailsafeLimit(watts=watts):
            return watts
        case ControlCompressor(action=action):
            return action


async def _call_bridge(bridge: BridgeClient, ski: str, command: Command) -> None:
    match command:
        case SetZoneSetpoint(zone_id=zone_id, celsius=celsius):
            await bridge.set_zone_setpoint(ski, zone_id, celsius)
        case SetZoneMode(zone_id=zone_id, mode=mode):
            await bridge.set_zone_mode(ski, zone_id, mode)
        case SetHotWaterSetpoint(celsius=celsius):
            await bridge.set_dhw_setpoint(ski, celsius)
        case SetHotWaterMode(mode=mode):
            await bridge.set_dhw_mode(ski, mode)
        case SetHotWaterBoost(active=active):
            await bridge.set_dhw_boost(ski, active)
        case SetConsumptionLimit(watts=watts, duration_s=duration_s, active=active):
            await bridge.write_consumption_limit(ski, watts, duration_s, active)
        case SetFailsafeLimit(watts=watts, duration_s=duration_s):
            await bridge.write_failsafe_limit(ski, watts, duration_s)
        case ControlCompressor(action=action, start_time=start_time):
            await bridge.control_compressor(ski, action, start_time)
        case SetZoneOverride() | EndZoneOverride():
            # _validate() lets these through only with the connection myVAILLANT.
            raise WriteRejected(409, "Eine zeitweise Übersteuerung gibt es nur mit der Anbindung myVAILLANT.")


# --- German formatting for the protocol ------------------------------------------


def _number(value: float, setpoint: Setpoint | None) -> str:
    digits = 1 if setpoint is None or 0 < setpoint.step < 1 else 0
    return f"{value:.{digits}f}".replace(".", ",")


def _celsius(value: float, setpoint: Setpoint | None) -> str:
    return f"{_number(value, setpoint)} °C"


def _hours(hours: float) -> str:
    """German, e.g. "2 h", "1 h 15 min"."""
    whole, minutes = divmod(round(hours * 60), 60)
    return f"{whole} h {minutes} min" if minutes else f"{whole} h"


def _watts(watts: float) -> str:
    return f"{round(watts):,} W".replace(",", ".")


def _duration(seconds: int) -> str:
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def _mode_name(mode: str) -> str:
    return MODE_NAMES.get(mode, mode)


def _value_part(observed: _Observed) -> str:
    """The value part of an observed text, e.g. "Solltemperatur 23,0 °C"."""
    return observed.text.split(": ", 1)[-1]


# --- radiator thermostats (room control, 1.4.0) --------------------------------------------------

# Technical limit between two commands to the same thermostat, whatever the reason.
ROOM_RATE_LIMIT_S = 5.0
# Zigbee2MQTT reports a new setpoint within seconds; a battery thermostat may sleep a few minutes.
ROOM_CONFIRM_TIMEOUT_S = 300.0
# After this many unconfirmed commands in a row the thermostat gets only window, safety and fallback.
ROOM_UNCONFIRMED_MAX = 2
ROOM_UNCONFIRMED_PAUSE = timedelta(hours=1)
# A test run's own budget per thermostat and night (punkt1-trv-regelung.md 3b); not part of the daily limit.
TEST_COMMANDS_MAX = 16
# A night runs from noon to noon (local), so a test run after midnight keeps its budget.
_NIGHT_SHIFT = timedelta(hours=12)
# Step of the thermostats when the entity does not report its own.
DEFAULT_TRV_STEP = 0.5
ROOM_GATE_FILE = "room_gate.json"
_NO_VALUE = frozenset({"unavailable", "unknown", ""})


class RoomReason(StrEnum):
    """Why the room control sets a thermostat; the protocol names it, the valve sparing depends on it."""

    CONTROL = "control"
    PREHEAT = "preheat"
    REDISTRIBUTE = "redistribute"
    WINDOW = "window"
    MANUAL = "manual"
    SAFETY = "safety"
    TEST = "test"
    # Back to the setpoint from before a test run.
    RESTORE = "restore"
    FALLBACK = "fallback"


ROOM_REASON_NAMES = {
    RoomReason.CONTROL: "Regelung",
    RoomReason.PREHEAT: "Vorheizen",
    RoomReason.REDISTRIBUTE: "Umverteilen",
    RoomReason.WINDOW: "Fenster",
    RoomReason.MANUAL: "Handänderung zurückgesetzt",
    RoomReason.SAFETY: "Sicherheit, Raum zu warm",
    RoomReason.TEST: "Testlauf",
    RoomReason.RESTORE: "Rückweg nach dem Testlauf",
    RoomReason.FALLBACK: "Rückfall auf den Notfallwert",
}
# Valve sparing (punkt1-trv-regelung.md 3a): these reasons need no dead band and no interval ...
_FREE_OF_SPARING = frozenset(
    {
        RoomReason.WINDOW,
        RoomReason.MANUAL,
        RoomReason.SAFETY,
        RoomReason.FALLBACK,
        RoomReason.TEST,
        RoomReason.RESTORE,
    }
)
# ... and these still go out when the day's commands are used up.
_OVER_THE_LIMIT = frozenset({RoomReason.WINDOW, RoomReason.SAFETY, RoomReason.FALLBACK, RoomReason.RESTORE})
# Handing a thermostat back always goes out: no pause, no technical limit, no budget.
_HANDING_BACK = frozenset({RoomReason.FALLBACK, RoomReason.RESTORE})


@dataclass(frozen=True, slots=True)
class SetThermostatSetpoint:
    """The setpoint of a room's radiator thermostat: the one chosen for the room in the settings.

    trv names the thermostat only for the fallback of a thermostat whose room is gone
    (deleted, or its sensor or thermostat changed); the gate then uses what it kept of the room.
    """

    room_id: str
    celsius: float
    reason: RoomReason
    trv: str | None = None


class Held(StrEnum):
    """Why a valid command was not sent (valve sparing); the room control tries again later."""

    SAME = "same"
    DEADBAND = "deadband"
    INTERVAL = "interval"
    LIMIT = "limit"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class RoomWriteResult:
    sent: bool
    message: str
    # Why nothing was sent although the command is allowed; None when it was sent (or would be).
    held: Held | None = None
    # The earliest time the next command may go out (interval).
    retry_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThermostatLimits:
    min_c: float
    max_c: float
    step: float

    def round(self, celsius: float) -> float:
        """To the thermostat's step (counted from its minimum) and into its range."""
        steps = round((celsius - self.min_c) / self.step)
        return round(min(self.max_c, max(self.min_c, self.min_c + steps * self.step)), 2)

    def on_step(self, celsius: float) -> bool:
        steps = (celsius - self.min_c) / self.step
        return abs(steps - round(steps)) <= 1e-6


@dataclass(frozen=True, slots=True)
class ThermostatCommands:
    """What the gate counts for one thermostat, for the page "Räume"."""

    today: int
    limit: int
    # Commands of test runs this night.
    test_today: int
    last_sent: datetime | None
    # The value the app set last (confirmed or still on its way); None when unknown.
    expected_c: float | None
    pending_c: float | None
    unconfirmed: int
    paused_until: datetime | None


@dataclass(frozen=True, slots=True)
class TouchedThermostat:
    """A thermostat the app has set, with what the gate kept of its room for the fallback."""

    trv: str
    room_id: str | None
    name: str | None
    fallback_c: float | None


@dataclass
class _Trv:
    day: date | None = None
    count: int = 0
    test_night: date | None = None
    test_count: int = 0
    last_sent: datetime | None = None
    expected: float | None = None
    # The app has set this thermostat since it took it over; the fallback may only go to these.
    touched: bool = False
    unconfirmed: int = 0
    paused_until: datetime | None = None
    pending: float | None = None
    pending_since: datetime | None = None
    pending_text: str = ""
    limit_logged: date | None = None
    # The setpoint before the first command of a test run: the way back may go there.
    restore_c: float | None = None
    # The room the thermostat belonged to when the app last set it, for a fallback without the room.
    room_id: str | None = None
    name: str | None = None
    fallback_c: float | None = None


class RoomGate:
    """The only place that writes to radiator thermostats (punkt1-trv-regelung.md 8).

    One service, climate.set_temperature with only the temperature, on the
    thermostat a room has in the settings - never on a myVAILLANT zone. A room
    must be in "Steuern" (switch and room), a test run needs the room control
    on, the fallback and the way back after a test run only go to thermostats
    the app has set. The value must fit the thermostat's range and step and the
    room's limits; dead band, interval and the commands a day spare the valves
    here, not only in the room control. A value still on its way counts as the
    thermostat's setpoint. Preview and demo mode send nothing; their counting is
    simulated separately, so the preview shows what control would send.
    """

    def __init__(
        self,
        client: HomeAssistantClient | None,
        settings: SettingsStore,
        sources: HemsSources,
        log: LogStore,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        confirm_timeout_s: float = ROOM_CONFIRM_TIMEOUT_S,
        rate_limit_s: float = ROOM_RATE_LIMIT_S,
    ) -> None:
        self._client = client
        self._settings = settings
        self._sources = sources
        self._log = log
        self._path = data_dir / ROOM_GATE_FILE
        self._zone = zone
        self._clock = clock
        self._confirm_timeout = timedelta(seconds=confirm_timeout_s)
        self._rate_limit = timedelta(seconds=rate_limit_s)
        self._live: dict[str, _Trv] = {}
        self._preview: dict[str, _Trv] = {}
        # Last known range of each thermostat, for the fallback when its state is gone (stopping).
        self._limits: dict[str, ThermostatLimits] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._load()
        sources.on_change(self._changed)

    # --- for the room control and the page -----------------------------------------------

    def limits(self, trv: str) -> ThermostatLimits | None:
        """The thermostat's range and step as Home Assistant reports them now."""
        state = self._sources.get(trv)
        if state is None or state.state in _NO_VALUE:
            return None
        low, high = _finite(state.attributes.get("min_temp")), _finite(state.attributes.get("max_temp"))
        if low is None or high is None or low >= high:
            return None
        step = _finite(state.attributes.get("target_temp_step"))
        limits = ThermostatLimits(low, high, step if step is not None and step > 0 else DEFAULT_TRV_STEP)
        self._limits[trv] = limits
        return limits

    def known_limits(self, trv: str) -> ThermostatLimits | None:
        """The range now, else the last one known (for the fallback while stopping)."""
        return self.limits(trv) or self._limits.get(trv)

    def expected(self, trv: str) -> float | None:
        """The setpoint the app set last on this thermostat (live)."""
        state = self._live.get(trv)
        return state.expected if state is not None else None

    def sending(self, trv: str) -> bool:
        """A command to this thermostat is not confirmed yet."""
        state = self._live.get(trv)
        return state is not None and state.pending is not None

    def touched(self) -> list[str]:
        """Thermostats the app has set and not handed back yet."""
        return sorted(trv for trv, state in self._live.items() if state.touched)

    def touched_thermostats(self) -> list[TouchedThermostat]:
        """The thermostats the app has set, with what the gate kept of their rooms."""
        return [
            TouchedThermostat(trv=trv, room_id=state.room_id, name=state.name, fallback_c=state.fallback_c)
            for trv, state in sorted(self._live.items())
            if state.touched
        ]

    def commands(self, trv: str, *, preview: bool = False) -> ThermostatCommands:
        state = (self._preview if preview else self._live).get(trv) or _Trv()
        self._new_day(state, self._clock())
        room = self._room_of(trv)
        return ThermostatCommands(
            today=state.count,
            limit=room.details.max_commands if room is not None else 0,
            test_today=state.test_count,
            last_sent=state.last_sent,
            expected_c=state.expected,
            pending_c=state.pending,
            unconfirmed=state.unconfirmed,
            paused_until=state.paused_until,
        )

    async def submit(self, command: SetThermostatSetpoint, *, preview: bool = False) -> RoomWriteResult:
        """Check the command and, in live mode with the room in "Steuern", send it to the thermostat.

        preview checks it and logs what control would send. Raises WriteRejected when
        the command is not allowed, WriteNotExecuted when Home Assistant refused it and
        WriteOutcomeUnknown when it is unknown whether it arrived.
        """
        trv = self._thermostat_of(command, self._settings.current)
        async with self._locks.setdefault(trv, asyncio.Lock()):
            # Read inside the lock: the mode or the room may have changed while waiting for it.
            settings = self._settings.current
            room = self._room_for(command, trv, settings)
            kept = self._live.get(trv)
            name = room.name if room is not None else (kept.name if kept is not None and kept.name else trv)
            description = (
                f"Thermostat {name}: Soll {_number(command.celsius, None)} °C ({ROOM_REASON_NAMES[command.reason]})"
            )
            live = not preview and settings.operating_mode is OperatingMode.LIVE
            try:
                handing_back = command.reason in _HANDING_BACK
                if room is not None:
                    self._check_room(room, trv, command, settings, preview=preview)
                    limits = self._value_limits(trv, handing_back=handing_back)
                    _check_room_value(room, command, limits, restore_c=kept.restore_c if kept is not None else None)
                else:
                    limits = self._value_limits(trv, handing_back=handing_back)
                    self._check_orphan(trv, command, settings, limits, preview=preview)
            except WriteRejected as rejected:
                category = LogCategory.ROOMS if preview else LogCategory.WRITE
                self._log.add(category, f"Abgelehnt: {description}. {rejected.message}", problem=True)
                raise
            now = self._clock()
            state = (self._live if live else self._preview).setdefault(trv, _Trv())
            self._new_day(state, now)
            reported = self._sources.attribute(trv, "temperature")
            if live:
                # A value still on its way is what the thermostat will have.
                current = state.pending if state.pending is not None else reported
            else:
                current = state.expected if state.expected is not None else reported
            details = room.details if room is not None else RoomDetails()
            held = self._held(name, details, trv, state, command, current, now)
            if held is not None:
                if live and held.held is Held.SAME and command.reason in _HANDING_BACK:
                    self._handed_back(state, command, room, settings)
                    self._save()
                return held
            if not live:
                text = "Vorschau, würde senden" if preview else "Würde senden"
                message = f"{text}: {description}"
                self._log.add(LogCategory.ROOMS if preview else LogCategory.WRITE, message)
                self._count(state, command, now)
                state.expected = command.celsius
                return RoomWriteResult(sent=False, message=message)
            if self._client is None:
                raise WriteRejected(409, "Ohne Home Assistant kann die App kein Thermostat stellen.")
            return await self._send(trv, state, command, description, now, room, settings, before=reported)

    async def run(self) -> None:
        """Follow sent setpoints: not reported in time means not confirmed."""
        while True:
            await asyncio.sleep(5.0)
            self.expire()

    def expire(self) -> None:
        now = self._clock()
        changed = False
        for trv, state in self._live.items():
            since = state.pending_since
            if state.pending is None or since is None or now - since < self._confirm_timeout:
                continue
            reported = self._sources.attribute(trv, "temperature")
            still = f"; das Thermostat meldet {_number(reported, None)} °C" if reported is not None else ""
            minutes = self._confirm_timeout.total_seconds() / 60
            self._log.add(
                LogCategory.WRITE, f"Nicht bestätigt nach {minutes:.0f} min: {state.pending_text}{still}", problem=True
            )
            state.pending = state.pending_since = None
            state.expected = None
            state.unconfirmed += 1
            if state.unconfirmed >= ROOM_UNCONFIRMED_MAX:
                state.paused_until = now + ROOM_UNCONFIRMED_PAUSE
                self._log.add(
                    LogCategory.ROOMS,
                    f"Thermostat {state.name or trv} übernimmt die Sollwerte nicht "
                    f"({state.unconfirmed}-mal nicht bestätigt). Die App sendet ihm eine Stunde lang nur Fenster, "
                    "Sicherheit, Rückweg und Rückfall. Bitte Batterie und Funkverbindung prüfen.",
                    problem=True,
                )
            changed = True
        if changed:
            self._save()

    # --- internals ----------------------------------------------------------------------

    def _thermostat_of(self, command: SetThermostatSetpoint, settings: Settings) -> str:
        if command.trv is not None:
            if command.reason is not RoomReason.FALLBACK:
                raise WriteRejected(422, "Ein Thermostat ohne Raum bekommt nur den Rückfall.")
            return command.trv
        room = next((item for item in settings.rooms if item.entity_id == command.room_id), None)
        if room is None:
            raise WriteRejected(404, "Diesen Raum gibt es nicht (mehr).")
        if room.details.trv_entity is None:
            raise WriteRejected(409, f"{room.name}: Es ist kein Thermostat gewählt.")
        return room.details.trv_entity

    def _room_for(self, command: SetThermostatSetpoint, trv: str, settings: Settings) -> Room | None:
        """The room of the command's thermostat now; None only for the fallback of a thermostat without room."""
        if command.trv is not None:
            return next((room for room in settings.rooms if room.details.trv_entity == trv), None)
        room = next((item for item in settings.rooms if item.entity_id == command.room_id), None)
        if room is None or room.details.trv_entity != trv:
            raise WriteRejected(409, "Raum oder Thermostat wurden gerade geändert; nichts gesendet.")
        return room

    def _check_room(
        self, room: Room, trv: str, command: SetThermostatSetpoint, settings: Settings, *, preview: bool
    ) -> None:
        self._check_not_a_zone(trv, settings, room.name)
        if any(other.details.trv_entity == trv for other in settings.rooms if other.entity_id != room.entity_id):
            raise WriteRejected(409, f"{room.name}: Das Thermostat ist auch einem anderen Raum zugeordnet.")
        if preview:
            return
        mode = settings.room_control.mode
        match command.reason:
            case RoomReason.TEST:
                if mode is RoomControlMode.OFF:
                    raise WriteRejected(409, "Die Raumregelung ist aus; ein Testlauf geht erst, wenn sie läuft.")
            case RoomReason.FALLBACK | RoomReason.RESTORE:
                state = self._live.get(trv)
                if settings.operating_mode is OperatingMode.LIVE and (state is None or not state.touched):
                    raise WriteRejected(409, f"{room.name}: Die App hat dieses Thermostat nicht gestellt.")
            case _:
                if effective_stage(mode, room.details.stage) is not RoomControlMode.CONTROL:
                    raise WriteRejected(409, f"{room.name}: Der Raum steht nicht auf „Steuern“.")

    def _check_orphan(
        self, trv: str, command: SetThermostatSetpoint, settings: Settings, limits: ThermostatLimits, *, preview: bool
    ) -> None:
        """The fallback of a thermostat whose room is gone: only to the emergency value kept for it."""
        state = self._live.get(trv)
        if state is None or state.fallback_c is None or (not preview and not state.touched):
            raise WriteRejected(409, "Die App hat dieses Thermostat nicht gestellt.")
        self._check_not_a_zone(trv, settings, state.name or trv)
        if abs(command.celsius - limits.round(state.fallback_c)) >= 0.01:
            raise WriteRejected(422, "Der Rückfall geht nur auf den Notfallwert.")

    def _check_not_a_zone(self, trv: str, settings: Settings, name: str) -> None:
        """Never a zone of myVAILLANT: neither a chosen one nor one that looks like it (fail closed)."""
        entity = self._sources.get(trv)
        looks_like_zone = entity is not None and (
            "quick_veto_end_date_time" in entity.attributes or "time_program_heating" in entity.attributes
        )
        if trv in settings.myvaillant.zones or looks_like_zone:
            raise WriteRejected(409, f"{name}: Das Thermostat ist eine Zone von myVAILLANT, kein Heizkörper.")

    def _value_limits(self, trv: str, *, handing_back: bool) -> ThermostatLimits:
        limits = self.limits(trv)
        if limits is None and handing_back:
            limits = self._limits.get(trv)
        if limits is None:
            state = self._sources.get(trv)
            if state is None or state.state in _NO_VALUE:
                raise WriteRejected(409, "Das Thermostat ist gerade nicht erreichbar.")
            raise WriteRejected(409, "Das Thermostat meldet keinen Bereich (min_temp/max_temp).")
        return limits

    def _held(
        self,
        name: str,
        details: RoomDetails,
        trv: str,
        state: _Trv,
        command: SetThermostatSetpoint,
        current: float | None,
        now: datetime,
    ) -> RoomWriteResult | None:
        reason = command.reason
        if state.paused_until is not None and now < state.paused_until and reason not in _OVER_THE_LIMIT:
            return RoomWriteResult(sent=False, message="Thermostat pausiert", held=Held.PAUSED)
        if state.pending is not None and abs(state.pending - command.celsius) < 0.01:
            return RoomWriteResult(sent=False, message="Wird schon gesendet", held=Held.SAME)
        if current is not None and abs(current - command.celsius) < 0.01:
            return RoomWriteResult(sent=False, message="Bereits eingestellt", held=Held.SAME)
        if (
            reason not in _FREE_OF_SPARING
            and current is not None
            and abs(current - command.celsius) < details.deadband_k - 1e-6
        ):
            return RoomWriteResult(sent=False, message="Innerhalb der Totzone", held=Held.DEADBAND)
        if state.last_sent is not None and reason not in _HANDING_BACK:
            # Handing back goes out even right after another command: the app may be stopping.
            wait = self._rate_limit
            if reason not in _FREE_OF_SPARING:
                wait = max(wait, timedelta(minutes=details.min_interval_min))
            if now - state.last_sent < wait:
                return RoomWriteResult(
                    sent=False, message="Mindestabstand", held=Held.INTERVAL, retry_at=state.last_sent + wait
                )
        if reason is RoomReason.TEST:
            if state.test_count >= TEST_COMMANDS_MAX:
                return RoomWriteResult(sent=False, message="Testlauf-Budget verbraucht", held=Held.LIMIT)
            return None
        if state.count >= details.max_commands and reason not in _OVER_THE_LIMIT:
            if state.limit_logged != state.day and state is self._live.get(trv):
                state.limit_logged = state.day
                self._log.add(
                    LogCategory.ROOMS,
                    f"Thermostat {name}: Höchstzahl von {details.max_commands} Befehlen heute erreicht; bis "
                    "Mitternacht gehen nur noch Fenster, Sicherheit, Rückweg und Rückfall hinaus.",
                    problem=True,
                )
            return RoomWriteResult(sent=False, message="Höchstzahl erreicht", held=Held.LIMIT)
        return None

    async def _send(
        self,
        trv: str,
        state: _Trv,
        command: SetThermostatSetpoint,
        description: str,
        now: datetime,
        room: Room | None,
        settings: Settings,
        *,
        before: float | None,
    ) -> RoomWriteResult:
        assert self._client is not None
        # In the database before Home Assistant sees the command.
        await self._log.record(LogCategory.WRITE, f"Wird gesendet: {description}")
        undo = (state.last_sent, state.count, state.test_count, state.restore_c)
        if command.reason is RoomReason.TEST and state.restore_c is None:
            # The first command of a test run: the way back goes to the setpoint from before.
            state.restore_c = before if before is not None else state.expected
        self._count(state, command, now)
        if room is not None:
            state.room_id, state.name = room.entity_id, room.name
            state.fallback_c = room.details.fallback_c()
        state.pending, state.pending_since, state.pending_text = command.celsius, now, description
        self._save()
        try:
            await self._client.call_room_service("climate", "set_temperature", trv, {"temperature": command.celsius})
        except ServiceCallFailed as err:
            if err.executed is False:
                # Certainly not at the thermostat: as if it had not been tried.
                state.pending = state.pending_since = None
                state.last_sent, state.count, state.test_count, state.restore_c = undo
                self._save()
                self._log.add(LogCategory.WRITE, f"Nicht ausgeführt: {description} ({err.code})", problem=True)
                raise WriteNotExecuted(err.code) from err
            state.expected = command.celsius
            # It may have arrived: the app keeps the thermostat until it hands it back for sure.
            state.touched = True
            self._save()
            self._log.add(
                LogCategory.WRITE,
                f"Ergebnis unbekannt: {description} ({err.code}); die App prüft, ob das Thermostat den Wert meldet.",
                problem=True,
            )
            raise WriteOutcomeUnknown(err.code) from err
        except ValueError:
            # Refused before anything was sent.
            state.pending = state.pending_since = None
            state.last_sent, state.count, state.test_count, state.restore_c = undo
            self._save()
            raise
        except BaseException:
            # The app stops in the middle of the call: the value may have arrived.
            state.pending = state.pending_since = None
            if command.reason not in _HANDING_BACK:
                state.touched = True
            self._save()
            raise
        state.expected = command.celsius
        if command.reason in _HANDING_BACK:
            self._handed_back(state, command, room, settings)
        else:
            state.touched = True
        self._save()
        self._log.add(LogCategory.WRITE, f"Gesendet: {description}")
        return RoomWriteResult(sent=True, message=f"Gesendet: {description}")

    def _handed_back(self, state: _Trv, command: SetThermostatSetpoint, room: Room | None, settings: Settings) -> None:
        """After the fallback or the way back: the app lets go, unless the room stays in "Steuern"."""
        state.restore_c = None
        controlled = (
            command.reason is RoomReason.RESTORE
            and room is not None
            and effective_stage(settings.room_control.mode, room.details.stage) is RoomControlMode.CONTROL
        )
        state.touched = controlled

    def _count(self, state: _Trv, command: SetThermostatSetpoint, now: datetime) -> None:
        state.last_sent = now
        if command.reason is RoomReason.TEST:
            state.test_count += 1
        elif command.reason is not RoomReason.RESTORE:
            state.count += 1

    def _new_day(self, state: _Trv, now: datetime) -> None:
        local = now.astimezone(self._zone)
        today = local.date()
        if state.day != today:
            state.day, state.count = today, 0
        night = (local - _NIGHT_SHIFT).date()
        if state.test_night != night:
            state.test_night, state.test_count = night, 0

    def _room_of(self, trv: str) -> Room | None:
        return next((room for room in self._settings.current.rooms if room.details.trv_entity == trv), None)

    def _changed(self, entity_id: str, entity: EntityState | None) -> None:
        state = self._live.get(entity_id)
        if state is None or state.pending is None or entity is None:
            return
        reported = _finite(entity.attributes.get("temperature"))
        if reported is None or abs(reported - state.pending) >= 0.01:
            return
        self._log.add(
            LogCategory.WRITE,
            f"Bestätigt vom Thermostat: {state.name or entity_id} {_number(reported, None)} °C",
        )
        state.expected = state.pending
        state.pending = state.pending_since = None
        state.unconfirmed = 0
        state.paused_until = None
        self._save()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            _LOGGER.warning("Room gate state %s unreadable (%s); counting starts again", self._path, err)
            return
        items = raw.get("thermostats") if isinstance(raw, dict) else None
        for trv, item in (items or {}).items():
            if not isinstance(item, dict):
                continue
            try:
                self._live[trv] = _Trv(
                    day=date.fromisoformat(item["day"]) if item.get("day") else None,
                    count=int(item.get("count", 0)),
                    test_night=date.fromisoformat(item["test_night"]) if item.get("test_night") else None,
                    test_count=int(item.get("test_count", 0)),
                    last_sent=_moment(item.get("last_sent")),
                    expected=_finite(item.get("expected")),
                    touched=bool(item.get("touched", False)),
                    unconfirmed=int(item.get("unconfirmed", 0)),
                    paused_until=_moment(item.get("paused_until")),
                    restore_c=_finite(item.get("restore_c")),
                    room_id=item["room_id"] if isinstance(item.get("room_id"), str) else None,
                    name=item["name"] if isinstance(item.get("name"), str) else None,
                    fallback_c=_finite(item.get("fallback_c")),
                )
            except (TypeError, ValueError):
                _LOGGER.warning("Room gate state of %s unreadable, ignored", trv)

    def _save(self) -> None:
        payload = {
            "thermostats": {
                trv: {
                    "day": state.day.isoformat() if state.day else None,
                    "count": state.count,
                    "test_night": state.test_night.isoformat() if state.test_night else None,
                    "test_count": state.test_count,
                    "last_sent": state.last_sent.isoformat() if state.last_sent else None,
                    "expected": state.expected,
                    "touched": state.touched,
                    "unconfirmed": state.unconfirmed,
                    "paused_until": state.paused_until.isoformat() if state.paused_until else None,
                    "restore_c": state.restore_c,
                    "room_id": state.room_id,
                    "name": state.name,
                    "fallback_c": state.fallback_c,
                }
                for trv, state in self._live.items()
            }
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=ROOM_GATE_FILE, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file)
            os.replace(tmp, self._path)
        except OSError as err:
            _LOGGER.warning("Saving the room gate state failed: %s", err)


def _check_room_value(
    room: Room, command: SetThermostatSetpoint, limits: ThermostatLimits, *, restore_c: float | None
) -> None:
    celsius = command.celsius
    if not math.isfinite(celsius):
        raise WriteRejected(422, "Der Sollwert ist keine Zahl.")
    if not limits.min_c <= celsius <= limits.max_c:
        raise WriteRejected(
            422,
            f"{room.name}: Das Thermostat nimmt {_number(limits.min_c, None)} bis {_number(limits.max_c, None)} °C an.",
        )
    if not limits.on_step(celsius):
        raise WriteRejected(
            422, f"{room.name}: Das Thermostat nimmt nur Schritte von {_number(limits.step, None)} °C an."
        )
    details = room.details
    low, high = details.setpoint_range()
    normal = low - details.correction_limit_k - 1e-6 <= celsius <= high + details.correction_limit_k + 1e-6
    handing_back = {limits.round(value) for value in (details.fallback_c(), *details.setpoint_range())}
    match command.reason:
        case RoomReason.TEST:
            return
        case RoomReason.WINDOW if abs(celsius - limits.min_c) < 0.01 or normal:
            return
        case RoomReason.FALLBACK:
            if not any(abs(celsius - value) < 0.01 for value in handing_back):
                raise WriteRejected(422, f"{room.name}: Der Rückfall geht nur auf den Notfallwert oder das Raum-Soll.")
        case RoomReason.RESTORE:
            allowed = handing_back | ({limits.round(restore_c)} if restore_c is not None else set())
            if not any(abs(celsius - value) < 0.01 for value in allowed):
                raise WriteRejected(
                    422,
                    f"{room.name}: Der Rückweg geht nur auf den Sollwert vor dem Testlauf, den Notfallwert oder das "
                    "Raum-Soll.",
                )
        case _ if not normal:
            raise WriteRejected(
                422,
                f"{room.name}: Der Sollwert liegt weiter als {_number(details.correction_limit_k, None)} K vom "
                "Raum-Soll entfernt.",
            )


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else None
