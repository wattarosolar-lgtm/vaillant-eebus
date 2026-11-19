"""Carries the plan out through the write gate.

Each control (hot water, every zone) has a base value: the setpoint the user
wants normally. The planner shifts around it and always returns to it - at
the end of a block, when the planner is switched off or to preview, before
the app goes to demo mode and when the app stops. A setpoint the planner did
not send (changed in the app, in myVAILLANT or at the device, also while the
app was stopped) becomes the new base value; the planner leaves that control
alone until its running block ends.

Each control changes at most every 30 minutes; returning to the base value
is never held back. Base values and the values the planner set survive a
restart in /data/hems.json.

With the connection myVAILLANT a zone in auto follows its time program; there
the planner starts a temporary override (quick veto) at the program's value
plus the block's shift, lasting as long as the block, so it ends by itself even
when the app is gone. It ends the override early when the block ends early
(new plan, off, preview, demo, stop). An override it did not start, or one
ended by someone else, pauses the zone until the block ends.

Only zone setpoints and overrides, the hot water setpoint and the one-time
charge are ever submitted (a test checks it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

import grpc

from vaillant_eebus.hems.config import PlannerMode
from vaillant_eebus.hems.planner import DHW, Action, Plan, block_at
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.pairing import NotPairedError
from vaillant_eebus.settings import Connection, OperatingMode, SettingsStore
from vaillant_eebus.state import HeatPumpState, Setpoint, StateHub
from vaillant_eebus.write_gate import (
    Command,
    EndZoneOverride,
    SetHotWaterBoost,
    SetHotWaterSetpoint,
    SetZoneOverride,
    SetZoneSetpoint,
    Source,
    WriteGate,
    WriteNotExecuted,
    WriteOutcomeUnknown,
    WriteRejected,
    WriteResult,
)

_LOGGER = logging.getLogger(__name__)

STATE_FILE = "hems.json"
REST = timedelta(minutes=30)
# A return to the base value waits at most this long for the gate's limit per control.
URGENT_WAIT_S = 6.0
# Days on which the hot water was charged extra, kept for the planner.
KEEP_CHARGE_DAYS = 3
# An override of the planner that runs at most this much longer than wanted is left to end by
# itself; one that ends this close to its planned end ended by itself.
OVERRIDE_MARGIN = timedelta(minutes=5)
# A block lasting this much longer than the running override starts it anew.
OVERRIDE_EXTEND = timedelta(minutes=15)
# Overrides are given in quarter hours, at least one hour (the integration's minimum).
OVERRIDE_STEP_H = 0.25
OVERRIDE_MIN_H = 1.0
# A failed end of an override is tried again after this long (every call goes to the cloud).
END_RETRY = timedelta(minutes=5)


@dataclass(slots=True)
class _Control:
    # Saved: the user's normal setpoint and what the planner really set on the device.
    base: float | None = None
    applied: float | None = None
    # Saved, zones in auto with myVAILLANT: the time program's value, and the planner's running
    # override with its end. Kept apart from base and applied, which belong to the setpoint of "on".
    program: float | None = None
    override: float | None = None
    override_until: datetime | None = None
    # In memory: the last value the planner decided on (sent or only previewed), when, and
    # what the device reported last.
    decided: float | None = None
    decided_at: datetime | None = None
    previewed: float | None = None
    previewed_at: datetime | None = None
    observed: float | None = None
    # Zones in auto with myVAILLANT: the last value and whether an override ran.
    observed_auto: tuple[float, bool] | None = None
    observed_mode: str | None = None
    # The zone is in auto and steered with overrides.
    auto: bool = False
    # A manual change pauses the control until the block it fell into ends.
    paused_until: datetime | None = None
    # Start of the hot water block whose one-time charge was given, and of the one it was
    # only previewed for (switching to control in that block still gives it).
    boosted_block: datetime | None = None
    previewed_block: datetime | None = None


@dataclass(frozen=True, slots=True)
class _Device:
    """One control as the device reports it."""

    label: str
    setpoint: Setpoint
    # Zones: the operation mode; the planner acts in "on" (and in "auto" with myVAILLANT).
    mode: str | None
    # myVAILLANT: an override (quick veto) runs.
    override: bool = False


@dataclass(slots=True)
class _Saved:
    controls: dict[str, _Control] = field(default_factory=dict)
    charged: set[date] = field(default_factory=set)


class Executor:
    def __init__(
        self,
        gate: WriteGate,
        state: StateHub,
        settings: SettingsStore,
        log: LogStore,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._gate = gate
        self._state = state
        self._settings = settings
        self._log = log
        self._path = data_dir / STATE_FILE
        self._zone = zone
        self._clock = clock
        # Zones in auto get overrides; only the myVAILLANT integration offers them.
        self._overrides = gate.connection is Connection.MYVAILLANT
        saved = self._load()
        self._controls = saved.controls
        self._charged = saved.charged
        self._previewed_charges: set[date] = set()

    def bases(self) -> dict[str, float]:
        """The value each control returns to: for a zone in auto its time program's value."""
        bases = {}
        for key, control in self._controls.items():
            base = control.program if control.auto else control.base
            if base is not None:
                bases[key] = base
        return bases

    def shifted(self) -> dict[str, float]:
        """Controls the planner really changed and has not returned to the base value yet."""
        shifted = {
            key: control.applied
            for key, control in self._controls.items()
            if control.applied is not None and control.applied != control.base
        }
        shifted.update(
            {key: control.override for key, control in self._controls.items() if control.override is not None}
        )
        return shifted

    def charged_days(self, *, preview: bool) -> frozenset[date]:
        """Days on which the hot water was charged extra (in preview: also as a preview)."""
        return frozenset(self._charged | self._previewed_charges) if preview else frozenset(self._charged)

    async def step(self, plan: Plan | None) -> None:
        """Bring every control to what the plan wants now."""
        mode = self._settings.current.hems.mode
        heat_pump = self._state.current
        if heat_pump is None or not heat_pump.available or not heat_pump.connected:
            return
        now = self._clock()
        devices = _devices(heat_pump)
        self._observe(devices, mode, plan, now)
        for key, device in devices.items():
            control = self._controls[key]
            if control.auto or control.override is not None:
                await self._step_override(key, device, mode, plan, now)
                continue
            if control.base is None:
                continue
            if mode is PlannerMode.CONTROL:
                target = self._wanted(key, device, control.base, plan, now)
                if target is not None:
                    await self._apply(key, device, target, now)
                    await self._boost(key, heat_pump, plan, now, preview=False)
                continue
            if key in self.shifted():
                await self._apply(key, device, control.base, now, urgent=True)
            if mode is PlannerMode.PREVIEW:
                target = self._wanted(key, device, control.base, plan, now)
                if target is not None:
                    await self._preview(key, device, target, now)
                    await self._boost(key, heat_pump, plan, now, preview=True)

    async def release(self, reason: str) -> None:
        """Return every control the planner changed to its base value at once."""
        heat_pump = self._state.current
        if heat_pump is None or not self.shifted():
            return
        now = self._clock()
        devices = _devices(heat_pump)
        self._log.add(LogCategory.HEMS, f"Planer setzt die Grundwerte zurück: {reason}.")
        returns = []
        for key, control in self._controls.items():
            device = devices.get(key)
            if device is None:
                continue
            if control.override is not None and self._override_runs(control, now):
                returns.append(self._end_override(key, now))
            shifted = control.applied is not None and control.base is not None and control.applied != control.base
            # A zone's setpoint works only in "on" (myVAILLANT: in auto it would start an override).
            if shifted and control.base is not None and (key == DHW or device.mode == "on" or not self._overrides):
                returns.append(self._apply(key, device, control.base, now, urgent=True))
        # All at the same time: when the app stops, there are only a few seconds.
        for outcome in await asyncio.gather(*returns, return_exceptions=True):
            if isinstance(outcome, Exception):
                _LOGGER.error("Returning to a base value failed", exc_info=outcome)

    # --- internals -------------------------------------------------------------

    def _observe(self, devices: dict[str, _Device], mode: PlannerMode, plan: Plan | None, now: datetime) -> None:
        """Follow the device's setpoints; one the planner did not set becomes the new base value."""
        changed = False
        for key, device in devices.items():
            value = device.setpoint.value
            control = self._controls.setdefault(key, _Control())
            previous_mode, control.observed_mode = control.observed_mode, device.mode
            if previous_mode is not None and previous_mode != device.mode:
                # A decision for the other mode does not hold any more.
                control.decided = None
            control.auto = self._overrides and key != DHW and device.mode == "auto"
            if control.auto or control.override is not None:
                changed |= self._observe_override(key, device, mode, plan, now)
                continue
            previous, control.observed = control.observed, value
            if control.base is None:
                control.base = value
                changed = True
                continue
            if previous is None:
                # First reading since the start: was the setpoint changed while the app was away?
                if control.applied is None and value != control.base:
                    self._follow(key, device, value, mode, plan, now)
                    changed = True
                elif control.applied is not None and value not in (control.applied, control.base):
                    self._manual(key, device, value, plan, now)
                    changed = True
                elif control.applied is not None and value == control.base:
                    control.applied = None
                    changed = True
                continue
            if value == previous:
                continue
            # Only a value the planner really sent is its own; one it merely decided on
            # (in demo mode, or rejected) and someone set by hand is a manual change.
            if control.applied is None:
                if value != control.base:
                    self._follow(key, device, value, mode, plan, now)
                    changed = True
            elif value != control.applied:
                self._manual(key, device, value, plan, now)
                changed = True
        if changed:
            self._save()

    @staticmethod
    def _override_runs(control: _Control, now: datetime) -> bool:
        """The planner's override still runs noticeably longer."""
        return (
            control.override is not None
            and control.override_until is not None
            and now < control.override_until - OVERRIDE_MARGIN
        )

    def _observe_override(self, key: str, device: _Device, mode: PlannerMode, plan: Plan | None, now: datetime) -> bool:
        """Follow a zone in auto: its value without an override is the time program's; an
        override is the planner's only while it runs with exactly the value the planner sent."""
        control = self._controls[key]
        value, active = device.setpoint.value, device.override
        previous = control.observed_auto
        if previous == (value, active):
            return False
        control.observed_auto = (value, active)
        if not control.auto:
            # The zone left auto; Vaillant ends an override with that.
            control.override, control.override_until = None, None
            return True
        own = control.override is not None
        if not active:
            if own:
                early = self._override_runs(control, now)
                control.override, control.override_until = None, None
                if early and previous is not None:
                    self._overridden(key, device, mode, plan, now, "Übersteuerung von Hand beendet")
            control.program = value
            return True
        if own and value == control.override:
            return False
        if own:
            # Someone set another temperature on the planner's override: it is theirs now.
            control.override, control.override_until = None, None
        self._overridden(key, device, mode, plan, now, "von Hand übersteuert")
        return True

    def _overridden(
        self, key: str, device: _Device, mode: PlannerMode, plan: Plan | None, now: datetime, what: str
    ) -> None:
        """Someone else steers the zone in auto: the planner waits until the block ends."""
        control = self._controls[key]
        block = block_at(plan.blocks, key, now) if plan is not None else None
        control.paused_until = block.end if block is not None else None
        control.decided = None
        if mode is PlannerMode.CONTROL:
            wait = "; der Planer wartet bis zum Blockende" if block is not None else ""
            self._log.add(LogCategory.HEMS, f"Planer: {device.label} {what}{wait}.")

    async def _step_override(
        self, key: str, device: _Device, mode: PlannerMode, plan: Plan | None, now: datetime
    ) -> None:
        """Start, extend or end the planner's override of a zone in auto."""
        control = self._controls[key]
        if mode is not PlannerMode.CONTROL or not control.auto:
            if self._override_runs(control, now):
                await self._end_override(key, now)
            if mode is PlannerMode.PREVIEW and control.auto:
                wanted = self._wanted_override(key, device, plan, now)
                if wanted is not None:
                    await self._preview_override(key, wanted, now)
            return
        if control.paused_until is not None:
            if now < control.paused_until:
                return
            control.paused_until = None
        wanted = self._wanted_override(key, device, plan, now)
        if wanted is None:
            if self._override_runs(control, now):
                await self._end_override(key, now)
            return
        celsius, end = wanted
        if (
            control.override == celsius
            and control.override_until is not None
            and end - control.override_until < OVERRIDE_EXTEND
        ):
            return
        if control.decided_at is not None and now - control.decided_at < REST:
            return
        hours = _override_hours(now, end)
        try:
            result = await self._gate.submit(
                SetZoneOverride(zone_id=key, celsius=celsius, hours=hours), source=Source.PLANNER
            )
        except WriteRejected as rejected:
            if rejected.status != 429:
                control.decided, control.decided_at = celsius, now
            return
        except WriteOutcomeUnknown:
            control.override, control.override_until = celsius, now + timedelta(hours=hours)
        except (WriteNotExecuted, grpc.aio.AioRpcError):
            control.decided_at = now
            return
        except NotPairedError:
            return
        else:
            if result.mode is OperatingMode.LIVE:
                control.override, control.override_until = celsius, now + timedelta(hours=hours)
        control.decided, control.decided_at = celsius, now
        self._save()

    def _wanted_override(
        self, key: str, device: _Device, plan: Plan | None, now: datetime
    ) -> tuple[float, datetime] | None:
        """The override the plan wants now (temperature, end); None for none."""
        control = self._controls[key]
        block = block_at(plan.blocks, key, now) if plan is not None else None
        if block is None or control.program is None:
            return None
        return _fit(control.program + (block.shift_k or 0.0), device.setpoint), block.end

    async def _end_override(self, key: str, now: datetime) -> None:
        """End the planner's override; the zone follows its time program again."""
        control = self._controls[key]
        if control.decided is None and control.decided_at is not None and now - control.decided_at < END_RETRY:
            return
        try:
            result = await self._submit(EndZoneOverride(zone_id=key), urgent=True)
        except WriteRejected as rejected:
            if rejected.status == 429:
                return
            # Nothing to end any more (e.g. the zone left auto).
            control.override, control.override_until = None, None
        except WriteOutcomeUnknown:
            # If it did not arrive, the override ends by itself at its end.
            control.override, control.override_until = None, None
        except (WriteNotExecuted, grpc.aio.AioRpcError):
            control.decided, control.decided_at = None, now
            return
        except NotPairedError:
            return
        else:
            if result.mode is OperatingMode.LIVE:
                control.override, control.override_until = None, None
        control.decided, control.decided_at = None, now
        self._save()

    async def _preview_override(self, key: str, wanted: tuple[float, datetime], now: datetime) -> None:
        control = self._controls[key]
        celsius, end = wanted
        if celsius == control.previewed:
            return
        if control.previewed_at is not None and now - control.previewed_at < REST:
            return
        with contextlib.suppress(WriteRejected, NotPairedError):
            await self._gate.submit(
                SetZoneOverride(zone_id=key, celsius=celsius, hours=_override_hours(now, end)),
                source=Source.PLANNER,
                preview=True,
            )
        control.previewed, control.previewed_at = celsius, now

    def _follow(
        self, key: str, device: _Device, value: float, mode: PlannerMode, plan: Plan | None, now: datetime
    ) -> None:
        """A setpoint the planner did not set while nothing of its own is on the device."""
        if mode is PlannerMode.CONTROL:
            self._manual(key, device, value, plan, now)
        else:
            # The planner is not steering: the base value follows the device quietly.
            self._controls[key].base = value

    def _manual(self, key: str, device: _Device, value: float, plan: Plan | None, now: datetime) -> None:
        control = self._controls[key]
        control.base = value
        control.applied = None
        control.decided = value
        block = block_at(plan.blocks, key, now) if plan is not None else None
        control.paused_until = block.end if block is not None else None
        self._log.add(
            LogCategory.HEMS,
            f"Planer: neuer Grundwert {device.label}: {_celsius(value, device.setpoint)} (von Hand geändert).",
        )

    def _wanted(self, key: str, device: _Device, base: float, plan: Plan | None, now: datetime) -> float | None:
        """The setpoint the plan wants now; None when the planner must not touch the control."""
        control = self._controls[key]
        if control.paused_until is not None:
            if now < control.paused_until:
                return None
            control.paused_until = None
        if key != DHW and device.mode != "on":
            return None
        block = block_at(plan.blocks, key, now) if plan is not None else None
        if block is None:
            return base
        if block.action is Action.CHARGE and block.target_c is not None:
            return _fit(max(base, block.target_c), device.setpoint)
        return _fit(base + (block.shift_k or 0.0), device.setpoint)

    async def _submit(self, command: Command, *, urgent: bool) -> WriteResult:
        """Submit as the planner; a return to the base value waits out the gate's few seconds once."""
        try:
            return await self._gate.submit(command, source=Source.PLANNER)
        except WriteRejected as rejected:
            if not urgent or rejected.retry_after is None or rejected.retry_after > URGENT_WAIT_S:
                raise
            await asyncio.sleep(rejected.retry_after)
        return await self._gate.submit(command, source=Source.PLANNER)

    async def _apply(self, key: str, device: _Device, target: float, now: datetime, *, urgent: bool = False) -> None:
        """Send target unless it is in place; urgent (back to the base value) skips the rest."""
        control = self._controls[key]
        done = target == control.decided
        # A return to the base value that did not take (e.g. in demo mode) is tried again.
        if done and not (urgent and control.applied not in (None, target)):
            return
        if target == device.setpoint.value and control.applied in (None, target):
            control.decided = target
            return
        rested = control.decided_at is None or now - control.decided_at >= REST
        if not rested and (done or not urgent):
            return
        try:
            result = await self._submit(_setpoint(key, target), urgent=urgent)
        except WriteRejected as rejected:
            if rejected.status != 429:
                control.decided, control.decided_at = target, now
            return
        except WriteOutcomeUnknown:
            control.applied = target
        except grpc.aio.AioRpcError:
            # Certainly not arrived; the next attempt follows after the rest.
            control.decided_at = now
            return
        except NotPairedError:
            return
        else:
            if result.mode is OperatingMode.LIVE:
                control.applied = target
        control.decided, control.decided_at = target, now
        if control.applied == control.base:
            control.applied = None
        self._save()

    async def _preview(self, key: str, device: _Device, target: float, now: datetime) -> None:
        control = self._controls[key]
        if target == control.previewed:
            return
        if control.previewed is None and target == device.setpoint.value:
            control.previewed = target
            return
        if control.previewed_at is not None and now - control.previewed_at < REST:
            return
        with contextlib.suppress(WriteRejected, NotPairedError):
            await self._gate.submit(_setpoint(key, target), source=Source.PLANNER, preview=True)
        control.previewed, control.previewed_at = target, now

    async def _boost(
        self, key: str, heat_pump: HeatPumpState, plan: Plan | None, now: datetime, *, preview: bool
    ) -> None:
        """Start the one-time charge once per hot water block, after the setpoint went up."""
        if key != DHW or plan is None:
            return
        block = block_at(plan.blocks, DHW, now)
        control = self._controls[DHW]
        given = control.previewed_block if preview else control.boosted_block
        if block is None or block.action is not Action.CHARGE or given == block.start:
            return
        raised = control.previewed if preview else control.decided
        if raised is None or control.base is None or raised <= control.base:
            return
        water = heat_pump.hot_water
        if water is not None and water.boost in ("active", "running"):
            self._mark_boost(control, block.start, preview=preview)
            return
        try:
            await self._gate.submit(SetHotWaterBoost(active=True), source=Source.PLANNER, preview=preview)
        except WriteRejected as rejected:
            if rejected.status == 429:
                return
        except (WriteOutcomeUnknown, grpc.aio.AioRpcError, NotPairedError):
            pass
        self._mark_boost(control, block.start, preview=preview)
        day = block.start.astimezone(self._zone).date()
        if preview:
            self._previewed_charges.add(day)
        else:
            self._charged.add(day)
            self._save()

    @staticmethod
    def _mark_boost(control: _Control, block_start: datetime, *, preview: bool) -> None:
        if preview:
            control.previewed_block = block_start
        else:
            control.boosted_block = block_start

    def _load(self) -> _Saved:
        if not self._path.exists():
            return _Saved()
        try:
            content = json.loads(self._path.read_text(encoding="utf-8"))
            controls = {
                str(key): _Control(
                    base=_float(entry.get("base")),
                    applied=_float(entry.get("applied")),
                    program=_float(entry.get("program")),
                    override=_float(entry.get("override")),
                    override_until=_moment(entry.get("override_until")),
                )
                for key, entry in content["controls"].items()
            }
            charged = {date.fromisoformat(day) for day in content.get("charged", [])}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            corrupt = self._path.with_suffix(".json.corrupt")
            _LOGGER.warning(
                "Unreadable %s, moved to %s; base values are read from the device again", self._path, corrupt
            )
            os.replace(self._path, corrupt)
            return _Saved()
        return _Saved(controls=controls, charged=charged)

    def _save(self) -> None:
        for old in sorted(self._charged)[:-KEEP_CHARGE_DAYS]:
            self._charged.discard(old)
        content: dict[str, Any] = {
            "version": 1,
            "controls": {
                key: {
                    "base": control.base,
                    "applied": control.applied,
                    "program": control.program,
                    "override": control.override,
                    "override_until": control.override_until.isoformat() if control.override_until else None,
                }
                for key, control in sorted(self._controls.items())
            },
            "charged": [day.isoformat() for day in sorted(self._charged)],
        }
        temporary = self._path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(content, indent=1), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            _LOGGER.exception("Could not save %s", self._path)


def _devices(heat_pump: HeatPumpState) -> dict[str, _Device]:
    devices: dict[str, _Device] = {}
    for zone in heat_pump.zones:
        if zone.setpoint is not None:
            devices[zone.id] = _Device(label=zone.label, setpoint=zone.setpoint, mode=zone.mode, override=zone.override)
    water = heat_pump.hot_water
    if water is not None and water.setpoint is not None:
        devices[DHW] = _Device(label="Warmwasser", setpoint=water.setpoint, mode=water.mode)
    return devices


def _setpoint(key: str, celsius: float) -> Command:
    if key == DHW:
        return SetHotWaterSetpoint(celsius=celsius)
    return SetZoneSetpoint(zone_id=key, celsius=celsius)


def _fit(value: float, setpoint: Setpoint) -> float:
    """The nearest value the device accepts: on its step, within its limits."""
    if setpoint.step <= 0:
        return setpoint.value
    steps = round((value - setpoint.min) / setpoint.step)
    fitted = setpoint.min + steps * setpoint.step
    return round(min(setpoint.max, max(setpoint.min, fitted)), 3)


def _celsius(value: float, setpoint: Setpoint) -> str:
    digits = 1 if 0 < setpoint.step < 1 else 0
    return f"{value:.{digits}f} °C".replace(".", ",")


def _override_hours(now: datetime, end: datetime) -> float:
    """The override's length up to end, in quarter hours, at least an hour."""
    hours = (end - now).total_seconds() / 3600
    return max(OVERRIDE_MIN_H, math.ceil(hours / OVERRIDE_STEP_H - 1e-9) * OVERRIDE_STEP_H)


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
