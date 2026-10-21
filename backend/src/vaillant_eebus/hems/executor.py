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

Only zone setpoints, the hot water setpoint and the one-time charge are ever
submitted (a test checks it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
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
from vaillant_eebus.settings import OperatingMode, SettingsStore
from vaillant_eebus.state import HeatPumpState, Setpoint, StateService
from vaillant_eebus.write_gate import (
    Command,
    SetHotWaterBoost,
    SetHotWaterSetpoint,
    SetZoneSetpoint,
    Source,
    WriteGate,
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


@dataclass(slots=True)
class _Control:
    # Saved: the user's normal setpoint, and what the planner really set on the device.
    base: float | None = None
    applied: float | None = None
    # In memory: the last value the planner decided on (sent or only previewed), when, and
    # what the device reported last.
    decided: float | None = None
    decided_at: datetime | None = None
    previewed: float | None = None
    previewed_at: datetime | None = None
    observed: float | None = None
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
    # Zones: the operation mode; the planner only acts in "on".
    mode: str | None


@dataclass(slots=True)
class _Saved:
    controls: dict[str, _Control] = field(default_factory=dict)
    charged: set[date] = field(default_factory=set)


class Executor:
    def __init__(
        self,
        gate: WriteGate,
        state: StateService,
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
        saved = self._load()
        self._controls = saved.controls
        self._charged = saved.charged
        self._previewed_charges: set[date] = set()

    def bases(self) -> dict[str, float]:
        return {key: control.base for key, control in self._controls.items() if control.base is not None}

    def shifted(self) -> dict[str, float]:
        """Controls the planner really changed and has not returned to the base value yet."""
        return {
            key: control.applied
            for key, control in self._controls.items()
            if control.applied is not None and control.applied != control.base
        }

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
        bases = {key: self._controls[key].base for key in self.shifted()}
        returns = [
            self._apply(key, devices[key], base, now, urgent=True)
            for key, base in bases.items()
            if key in devices and base is not None
        ]
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
                str(key): _Control(base=_float(entry.get("base")), applied=_float(entry.get("applied")))
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
                key: {"base": control.base, "applied": control.applied}
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
            devices[zone.id] = _Device(label=zone.label, setpoint=zone.setpoint, mode=zone.mode)
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


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
