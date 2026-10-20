"""The only place that writes to the heat pump.

Every change - from the UI, the API and later the HEMS - goes through
WriteGate.submit(). The gate checks the command against what the device
reports (zone, writable flag, limits, step, offered modes). Then:

- demo mode: nothing is sent. Setpoints, modes and boost are answered with
  what would be sent; power limits and compressor control are blocked.
- live mode: at most one command per control every few seconds, values that
  are already set are skipped, and every sent value is followed until the
  device reports it or a timeout passes - also when the bridge's answer got
  lost and it is unknown whether the value arrived.

A caller states the mode it showed the user; a command decided in demo is
never sent because the mode changed in between. Every attempt is in the
protocol before it is sent. No other module calls the bridge client's write
methods; a test checks that.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

import grpc

from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.pairing import NotPairedError, PairingService
from vaillant_eebus.settings import OperatingMode, SettingsStore
from vaillant_eebus.state import HeatPumpState, HotWater, Setpoint, StateService, Zone

_LOGGER = logging.getLogger(__name__)

# Spec 9: at most one write per control within this many seconds.
RATE_LIMIT_S = 5.0
# A sent value counts as not confirmed when the device does not report it in time.
CONFIRM_TIMEOUT_S = 30.0

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

_MODE_NAMES = {"off": "Aus", "auto": "Auto", "on": "Ein", "eco": "Eco"}
_COMPRESSOR_ACTIONS = {"schedule": "einplanen", "pause": "pausieren", "resume": "fortsetzen", "abort": "abbrechen"}

type JsonScalar = str | bool | int | float | None


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

    def __init__(self, code: grpc.StatusCode) -> None:
        super().__init__(code.name)
        self.code = code


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


@dataclass(slots=True)
class _Pending:
    write: PendingWrite
    target: Hashable
    sent_at: float


type _Key = tuple[Control, str | None]


class WriteGate:
    def __init__(
        self,
        bridge: BridgeClient,
        pairing: PairingService,
        state: StateService,
        settings: SettingsStore,
        log: LogStore,
        *,
        rate_limit_s: float = RATE_LIMIT_S,
        confirm_timeout_s: float = CONFIRM_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bridge = bridge
        self._pairing = pairing
        self._state = state
        self._settings = settings
        self._log = log
        self._rate_limit_s = rate_limit_s
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
    def pending(self) -> list[PendingWrite]:
        return [pending.write for pending in self._pending.values()]

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

    async def submit(self, command: Command, *, expected_mode: OperatingMode | None = None) -> WriteResult:
        """Check the command and, in live mode, send it to the heat pump.

        expected_mode is the mode the caller showed the user when the change was
        made; if the mode changed since, nothing is sent. Raises WriteRejected
        when the command is not allowed, NotPairedError without a paired gateway,
        WriteOutcomeUnknown when it is unknown whether a sent value arrived, and
        the bridge's gRPC error when the value certainly did not arrive.
        """
        key = _key(command)
        async with self._locks.setdefault(key, asyncio.Lock()):
            ski = self._pairing.paired_ski
            if ski is None:
                raise NotPairedError
            heat_pump = self._state.current
            description = _describe(command, heat_pump)
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
                _validate(command, heat_pump)
            except WriteRejected as rejected:
                self._log.add(LogCategory.WRITE, f"Abgelehnt: {description}. {rejected.message}", problem=True)
                raise
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
        self, command: Command, key: _Key, ski: str, heat_pump: HeatPumpState, description: str
    ) -> WriteResult:
        target = _target(command)
        current = _device_values(heat_pump).get(key)
        pending = self._pending.get(key)
        if target is not None and (
            (current is not None and current.value == target) or (pending is not None and pending.target == target)
        ):
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

        # In the database before the bridge sees the command, so even a crash
        # of the app in the middle of a write leaves a trace.
        await self._log.record(LogCategory.WRITE, f"Wird gesendet: {description}")
        try:
            await _call_bridge(self._bridge, ski, command)
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
        _LOGGER.info("Sent: %s", description)
        message = f"Gesendet: {description}"
        self._log.add(LogCategory.WRITE, message)
        self._follow(command, key, target, description, now)
        return WriteResult(sent=True, mode=OperatingMode.LIVE, message=message)

    def _follow(self, command: Command, key: _Key, target: Hashable | None, description: str, sent_at: float) -> None:
        """Watch for the device to report a sent value."""
        self._unresolved.pop(key, None)
        if target is not None:
            self._pending[key] = _Pending(
                write=PendingWrite(
                    control=key[0],
                    zone_id=key[1],
                    value=_primary_value(command),
                    message=description,
                    since=datetime.now(UTC),
                ),
                target=target,
                sent_at=sent_at,
            )
            self._state.republish()
        self._state.refresh_now()

    def _observe(self, heat_pump: HeatPumpState | None) -> None:
        self._device_reachable = heat_pump is not None and heat_pump.available and heat_pump.connected
        if heat_pump is None or not self._device_reachable:
            return
        changed = False
        for key, observed in _device_values(heat_pump).items():
            pending = self._pending.get(key)
            unresolved = self._unresolved.pop(key, None)
            previous = self._last_values.get(key)
            if pending is not None and observed.value == pending.target:
                del self._pending[key]
                changed = True
                self._log.add(LogCategory.WRITE, f"Bestätigt vom Gerät: {observed.text}")
            elif unresolved is not None:
                if observed.value == unresolved.target:
                    self._log.add(LogCategory.WRITE, f"Nachträglich bestätigt vom Gerät: {observed.text}")
                else:
                    self._log.add(
                        LogCategory.WRITE,
                        f"Nicht übernommen: {unresolved.write.message}; das Gerät meldet {_value_part(observed)}",
                        problem=True,
                    )
            elif previous is not None and previous.value != observed.value:
                self._log.add(
                    LogCategory.DEVICE, f"Am Gerät geändert: {observed.text} (vorher {_value_part(previous)})"
                )
            self._last_values[key] = observed
        if changed:
            self._state.republish()

    def _seconds_to_next_expiry(self) -> float | None:
        if not self._pending:
            return None
        oldest = min(pending.sent_at for pending in self._pending.values())
        return max(0.0, oldest + self._confirm_timeout_s - self._clock())

    def _expire(self) -> None:
        now = self._clock()
        expired = [key for key, pending in self._pending.items() if now - pending.sent_at >= self._confirm_timeout_s]
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
        case SetZoneSetpoint(zone_id=zone_id):
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
        case SetZoneSetpoint(celsius=celsius) | SetHotWaterSetpoint(celsius=celsius):
            return round(celsius, 3)
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


def _device_values(heat_pump: HeatPumpState) -> dict[_Key, _Observed]:
    """The device's current value of every control it reports."""
    values: dict[_Key, _Observed] = {}
    for zone in heat_pump.zones:
        if zone.setpoint is not None:
            values[Control.ZONE_SETPOINT, zone.id] = _Observed(
                round(zone.setpoint.value, 3),
                f"{zone.label}: Solltemperatur {_celsius(zone.setpoint.value, zone.setpoint)}",
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


def _validate(command: Command, heat_pump: HeatPumpState) -> None:
    match command:
        case SetZoneSetpoint(zone_id=zone_id, celsius=celsius):
            zone = _zone(heat_pump, zone_id)
            # In auto the zone follows its time program, which is not exposed
            # over EEBUS; the setpoint only takes effect in "on" (field test 0.5.1).
            if zone.mode == "auto":
                raise WriteRejected(
                    409,
                    f"Im Auto-Modus folgt {zone.label} dem Zeitprogramm; die Solltemperatur wirkt nur in der "
                    "Betriebsart Ein. Für einen Boost myVAILLANT oder Home Assistant nutzen.",
                )
            _check_setpoint(zone.setpoint, celsius, zone.label)
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


def _describe(command: Command, heat_pump: HeatPumpState | None) -> str:
    """German description of the command, also for unknown zones and unknown state."""
    match command:
        case SetZoneSetpoint(zone_id=zone_id, celsius=celsius):
            zone = _find_zone(heat_pump, zone_id)
            label = zone.label if zone else f"Heizzone {zone_id}"
            return f"{label}: Solltemperatur {_celsius(celsius, zone.setpoint if zone else None)}"
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


def _primary_value(command: Command) -> float | str | bool:
    match command:
        case SetZoneSetpoint(celsius=celsius) | SetHotWaterSetpoint(celsius=celsius):
            return celsius
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


# --- German formatting for the protocol ------------------------------------------


def _number(value: float, setpoint: Setpoint | None) -> str:
    digits = 1 if setpoint is None or 0 < setpoint.step < 1 else 0
    return f"{value:.{digits}f}".replace(".", ",")


def _celsius(value: float, setpoint: Setpoint | None) -> str:
    return f"{_number(value, setpoint)} °C"


def _watts(watts: float) -> str:
    return f"{round(watts):,} W".replace(",", ".")


def _duration(seconds: int) -> str:
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def _mode_name(mode: str) -> str:
    return _MODE_NAMES.get(mode, mode)


def _value_part(observed: _Observed) -> str:
    """The value part of an observed text, e.g. "Solltemperatur 23,0 °C"."""
    return observed.text.split(": ", 1)[-1]
