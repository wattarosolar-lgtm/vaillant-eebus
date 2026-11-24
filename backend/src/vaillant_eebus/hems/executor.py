"""The zone executor (1.5.0, plan-1.5.0-zonen.md F0/F1): carries the plan out at the heat pump.

The planner (rules.py) decides a shift per zone and hour; this module turns it
into commands through the write gate, the only way to the heat pump:

- quick veto (F0): the zone in auto gets program value + shift until the next
  planned change (1-4 h), renewed 10 min before its end, changed at most every
  min_interval_min, ended at once when the shift is back to 0. Only the parts of
  functions on "Steuern" are sent; the parts on "Vorschau" are logged as "würde".
- floor heating off/auto (F1): off after the heat pump stood still a minute
  (not in frost protection, not with a cold room, not at the daily limit, not
  within 5 min of the last auto), auto at once when it runs.
- outside changes: someone else changing a zone's setpoint, mode or quick veto
  (myVAILLANT, the controller, the app's own UI) gets the zone a rest until the
  next planned change, at most 3 h. Two outside off/auto switches of the floor
  zone in step with the heat pump on one day stop F1: the user's automation
  probably still runs.
- fallback: stage off/preview, demo, stopping the app -> only its own quick
  vetos are ended and only the zones it switched off go back to auto. A quick
  veto ends by itself (at most 4 h); a zone left off is caught by the Home
  Assistant automation of the life sign (automation()).

Nothing is sent in demo mode, without the connection myVAILLANT, with the zone
or its program value unknown, or while the heat pump is not reachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import tempfile
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from vaillant_eebus.cloud_budget import BudgetStatus
from vaillant_eebus.ha_export import PREFIX, Sensor
from vaillant_eebus.hems.config import Function, HemsSettings, PlannerMode
from vaillant_eebus.hems.forecast import hour_start
from vaillant_eebus.hems.rules import Part, Plan, PlanHour, ZoneHour, shift_of
from vaillant_eebus.hems.service import HemsService
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.settings import Connection, OperatingMode, SettingsStore
from vaillant_eebus.state import HeatPumpState, StateHub, Zone
from vaillant_eebus.write_gate import (
    Command,
    Control,
    EndZoneOverride,
    SetZoneMode,
    SetZoneOverride,
    SetZoneSetpoint,
    Source,
    WriteGate,
    WriteNotExecuted,
    WriteOutcomeUnknown,
    WriteRejected,
)

_LOGGER = logging.getLogger(__name__)

STATE_FILE = "zone_control.json"
LIFESIGN_ENTITY = f"sensor.{PREFIX}zones_lifesign"
# A pass at least this often, also without news (renewals, end of a rest, the life sign).
PASS_EVERY = 60.0
# Quick veto: how long, and how long before its end it is renewed.
OVERRIDE_MIN_H = 1
OVERRIDE_MAX_H = 4
RENEW_BEFORE = timedelta(minutes=10)
# An outside change gets the zone a rest until the next planned change, at most this long.
REST_MAX = timedelta(hours=3)
# Ending a quick veto this much before its end is someone else's doing, not its expiry.
EXPIRY_SLACK = timedelta(minutes=2)
# F1: off at the earliest this long after the zone went back to auto.
ZONE1_OFF_GAP = timedelta(minutes=5)
# F1: outside off/auto switches in step with the heat pump per day that stop it.
AUTOMATION_SWITCHES = 2
# After a refused or failed command the same zone waits this long.
RETRY_AFTER = timedelta(minutes=5)
# Stopping: at most this long for ending the quick vetos and setting zones back to auto.
RELEASE_TIMEOUT_S = 15.0
ACTIONS_KEPT = 30

# Which function's stage decides about a part of the shift. Defrost and store
# (block operation, 1.6.0) and the night (F3) are not carried out by this map.
PART_FUNCTION = {Part.FLOW: Function.FLOW, Part.AHEAD: Function.FLOW, Part.WARM: Function.FLOW}

MODE_NAMES = {"off": "Aus", "auto": "Auto", "on": "Ein"}
NBSP = "\u00a0"
MINUS = "\u2212"


@dataclass(frozen=True, slots=True)
class Action:
    at: datetime
    text: str
    # True: sent to the heat pump; False: would have been sent (preview).
    sent: bool


@dataclass(frozen=True, slots=True)
class ZoneState:
    zone_id: str
    label: str
    # The executor's own quick veto: value, end, shift.
    override_c: float | None
    override_until: datetime | None
    shift_k: float
    # The executor switched the zone off (F1).
    off: bool
    rest_until: datetime | None
    rest_reason: str | None
    # Why nothing is sent right now, if anything keeps it from it.
    waiting: str | None
    # What preview would have set now.
    preview_shift_k: float | None


@dataclass(frozen=True, slots=True)
class ControlStatus:
    # Sends at all: live mode, myVAILLANT, some function on "Steuern".
    live: bool
    problems: list[str]
    budget: BudgetStatus | None
    zones: list[ZoneState]
    zone1_halted: bool
    automation_switches_today: int
    actions: list[Action]
    lifesign_entity: str
    lifesign_at: datetime | None


@dataclass
class _Simulated:
    """What preview would have set (nothing is sent)."""

    override_c: float | None = None
    override_until: datetime | None = None
    shift_k: float = 0.0
    changed_at: datetime | None = None


@dataclass
class _Zone:
    override_c: float | None = None
    override_until: datetime | None = None
    shift_k: float = 0.0
    changed_at: datetime | None = None
    off: bool = False
    # The mode the executor sent last and when the zone last went to auto.
    mode_sent: str | None = None
    auto_at: datetime | None = None
    rest_until: datetime | None = None
    rest_reason: str | None = None
    retry_at: datetime | None = None
    waiting: str | None = None
    # The time program's value (the setpoint in auto without a quick veto).
    program_c: float | None = None
    # The last state seen: mode, quick veto running, its value.
    seen: tuple[str | None, bool, float | None] | None = None
    preview: _Simulated = field(default_factory=_Simulated)
    # F1 in preview: what it would have set last.
    preview_mode: str | None = None


class ZoneExecutor:
    def __init__(
        self,
        hems: HemsService,
        gate: WriteGate,
        state: StateHub,
        settings: SettingsStore,
        log: LogStore,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._hems = hems
        self._gate = gate
        self._state = state
        self._settings = settings
        self._log = log
        self._path = data_dir / STATE_FILE
        self._zone = zone
        self._clock = clock
        self._zones: dict[str, _Zone] = {}
        self._actions: list[Action] = []
        self._problems: list[str] = []
        self._zone1_halted = False
        self._automation: tuple[date, int] | None = None
        self._alive_at: datetime | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._load()
        hems.on_update(self.wake)
        hems.set_control_status(self.status)
        gate.on_sent(self._sent)

    def wake(self) -> None:
        self._wake.set()

    # --- loop ----------------------------------------------------------------------------

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.step()
                except Exception:
                    # A fault must not stop the app; the life sign then stops.
                    _LOGGER.exception("The zone executor failed")
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(PASS_EVERY):
                        await self._wake.wait()
                self._wake.clear()
        finally:
            self._save()
            await _despite_cancellation(self.release("Die App stoppt"))

    async def step(self) -> None:
        """One pass: watch the zones for outside changes, then decide and send."""
        async with self._lock:
            now = self._clock()
            heat_pump = self._state.current
            self._problems = []
            if heat_pump is None or not heat_pump.available or not heat_pump.connected:
                self._problems.append("Die Wärmepumpe ist nicht erreichbar; der Planer sendet nichts.")
                self._alive_at = now
                return
            hems = self._settings.current.hems
            if self._wants_control(hems) and not self._can_send():
                if self._gate.connection is not Connection.MYVAILLANT:
                    self._problems.append(
                        "„Steuern“ der Zonen braucht die Anbindung myVAILLANT; es bleibt bei Vorschau."
                    )
                else:
                    self._problems.append("Im Demo-Betrieb sendet der Planer nichts; er zeigt, was er senden würde.")
            for zone in heat_pump.zones:
                self._observe(self._zones.setdefault(zone.id, _Zone()), zone, heat_pump, now)
            plan = self._hems.plan
            for zone in heat_pump.zones:
                record = self._zones[zone.id]
                record.waiting = None
                await self._quick_veto(zone, record, hems, plan, now)
                await self._preview_quick_veto(zone, record, hems, plan, now)
            await self._zone1(heat_pump, hems, now)
            self._alive_at = now
            self._save()

    async def release(self, why: str) -> None:
        """Give the zones back: end the own quick vetos, own off -> auto. At most RELEASE_TIMEOUT_S."""
        owned = [(zone_id, record) for zone_id, record in self._zones.items() if record.override_c or record.off]
        if not owned or self._gate.mode is not OperatingMode.LIVE:
            return
        try:
            async with asyncio.timeout(RELEASE_TIMEOUT_S):
                for zone_id, record in owned:
                    if record.override_c is not None:
                        await self._send(
                            zone_id, record, EndZoneOverride(zone_id=zone_id), f"{why}: Quick-Veto beendet"
                        )
                    if record.off:
                        await self._send(zone_id, record, SetZoneMode(zone_id=zone_id, mode="auto"), f"{why}: Auto")
        except TimeoutError:
            _LOGGER.warning("Giving the zones back took too long")
        self._save()

    # --- watching ---------------------------------------------------------------------------

    def _observe(self, record: _Zone, zone: Zone, heat_pump: HeatPumpState, now: datetime) -> None:
        value = zone.setpoint.value if zone.override and zone.setpoint is not None else None
        seen = (zone.mode, zone.override, value)
        before, record.seen = record.seen, seen
        settling = self._gate.settling(Control.ZONE_SETPOINT, zone.id) or self._gate.settling(
            Control.ZONE_MODE, zone.id
        )
        program = zone.mode == "auto" and not zone.override and not zone.setback and zone.setpoint is not None
        if program and not settling and record.override_c is None:
            # Not while a value of the executor settles: the integration may show it before the quick veto.
            assert zone.setpoint is not None
            record.program_c = zone.setpoint.value
        if zone.mode == "auto" and (before is None or before[0] != "auto"):
            record.auto_at = now
        if before is None:
            # The first state after a start: what was owned and is gone is no longer owned.
            if record.override_c is not None and not zone.override:
                record.override_c, record.override_until, record.shift_k = None, None, 0.0
            if record.off and zone.mode != "off":
                record.off = False
            return
        # A mode the executor (or the UI) sent and the zone now shows is its own, also after the settling.
        own_mode = zone.mode == record.mode_sent
        if not settling and own_mode:
            record.mode_sent = None
        if settling or seen == before:
            return
        mode, override, _ = before
        outside: str | None = None
        if zone.override and not override and record.override_c is None:
            outside = f"Quick-Veto {_celsius(value)} von außen"
        elif zone.override and override and value != before[2] and not _same(value, record.override_c):
            outside = f"Quick-Veto auf {_celsius(value)} von außen"
        elif not zone.override and override and record.override_c is not None:
            # Switched off (F1) also ends the quick veto: not someone else's doing.
            ended_early = (
                zone.mode == "auto" and record.override_until is not None and now < record.override_until - EXPIRY_SLACK
            )
            record.override_c, record.override_until, record.shift_k = None, None, 0.0
            if ended_early:
                outside = "Quick-Veto von außen beendet"
        if zone.mode != mode and not own_mode:
            if record.off and zone.mode != "off":
                record.off = False
            outside = self._outside_mode(zone, mode, heat_pump, now) or outside
        if outside is not None:
            self._rest(zone, record, outside, now)

    def _outside_mode(self, zone: Zone, before: str | None, heat_pump: HeatPumpState, now: datetime) -> str | None:
        """An outside mode change: a hand change, or the user's automation switching the floor zone."""
        text = f"Betriebsart {MODE_NAMES.get(str(before), before)} → {MODE_NAMES.get(str(zone.mode), zone.mode)}"
        hems = self._settings.current.hems
        floor = zone.id == self._hems.floor_zone()
        switch = {before, zone.mode} == {"off", "auto"}
        if floor and switch and hems.effective(Function.ZONE1) is not PlannerMode.CONTROL:
            # The user's automation still switches the floor zone, as it should until F1 steers.
            return None
        if floor and switch and self._in_step(zone.mode, heat_pump):
            today = now.astimezone(self._zone).date()
            count = self._automation[1] + 1 if self._automation and self._automation[0] == today else 1
            self._automation = (today, count)
            if count >= AUTOMATION_SWITCHES and not self._zone1_halted:
                self._zone1_halted = True
                self._log.add(
                    LogCategory.HEMS,
                    f"{zone.label} wurde heute {count}-mal im Takt der Wärmepumpe von außen geschaltet. Läuft die "
                    "Automation noch? „Zone 1 Aus/Auto“ hält an, bis die Stufe neu gewählt wird.",
                    problem=True,
                )
        return text

    def _in_step(self, mode: str | None, heat_pump: HeatPumpState) -> bool:
        power = heat_pump.measurements.get("power_consumption")
        if power is None:
            return False
        below = power.value < self._settings.current.hems.zone1_threshold_w
        return (mode == "off") == below

    def _rest(self, zone: Zone, record: _Zone, why: str, now: datetime) -> None:
        until = now + REST_MAX
        upcoming = self._next_change(zone.id, self._parts(self._settings.current.hems, preview=True), now)
        if upcoming is not None:
            until = min(until, upcoming)
        record.rest_until, record.rest_reason = until, why
        local = until.astimezone(self._zone)
        self._log.add(
            LogCategory.HEMS, f"{zone.label}: {why}. Der Planer lässt die Zone bis {local:%H:%M} Uhr in Ruhe."
        )

    def _sent(self, command: Command, source: Source) -> None:
        """A command of the UI to a zone is a hand change (W6 too)."""
        if source is not Source.UI or not isinstance(
            command, SetZoneSetpoint | SetZoneOverride | EndZoneOverride | SetZoneMode
        ):
            return
        heat_pump = self._state.current
        zone = next((item for item in heat_pump.zones if item.id == command.zone_id), None) if heat_pump else None
        record = self._zones.setdefault(command.zone_id, _Zone())
        if isinstance(command, EndZoneOverride):
            record.override_c, record.override_until, record.shift_k = None, None, 0.0
        if isinstance(command, SetZoneMode):
            record.off = False
            record.mode_sent = command.mode
        if zone is not None:
            self._rest(zone, record, "in der App von Hand geändert", self._clock())

    # --- quick veto (F0) ---------------------------------------------------------------------

    async def _quick_veto(
        self, zone: Zone, record: _Zone, hems: HemsSettings, plan: Plan | None, now: datetime
    ) -> None:
        parts = self._parts(hems, preview=False)
        if not parts and record.override_c is None:
            return
        config = hems.zone(zone.id)
        hour = _zone_hour(plan, zone.id, now)
        target = shift_of(hour.parts, config, hems.step_k, parts) if hour is not None and parts else 0.0
        if target == 0.0:
            if record.override_c is not None and not zone.override:
                record.override_c, record.override_until, record.shift_k = None, None, 0.0
            elif record.override_c is not None and self._can_send() and not self._retrying(record, now):
                await self._send(zone.id, record, EndZoneOverride(zone_id=zone.id), "keine Verschiebung mehr")
            return
        if (why := self._blocked(zone, record, now)) is not None:
            record.waiting = why
            return
        assert record.program_c is not None
        celsius = _within(round(record.program_c + target, 1), zone)
        reason = _reason(plan, now)
        if record.override_c is not None and record.shift_k == target:
            if record.override_until is not None and record.override_until - now <= RENEW_BEFORE:
                hours = self._hours(zone.id, parts, now)
                command = SetZoneOverride(zone_id=zone.id, celsius=celsius, hours=hours)
                if await self._send(zone.id, record, command, f"Quick-Veto erneuert{reason}"):
                    record.override_until = now + timedelta(hours=hours)
            return
        interval = timedelta(minutes=hems.min_interval_min)
        # Up goes out at once (F2: a room lacks heat); down waits for the interval.
        if target < record.shift_k and record.changed_at is not None and now - record.changed_at < interval:
            record.waiting = (
                f"Abstand: nächste Änderung ab {(record.changed_at + interval).astimezone(self._zone):%H:%M} Uhr"
            )
            return
        budget = self._gate.budget
        if budget is not None and not budget.allows(fallback=False):
            record.waiting = "Tagesgrenze der Befehle an myVAILLANT erreicht"
            return
        hours = self._hours(zone.id, parts, now)
        command = SetZoneOverride(zone_id=zone.id, celsius=celsius, hours=hours)
        if await self._send(zone.id, record, command, f"{_signed(target)} K{reason}"):
            record.override_c, record.override_until = celsius, now + timedelta(hours=hours)
            record.shift_k, record.changed_at = target, now

    async def _preview_quick_veto(
        self, zone: Zone, record: _Zone, hems: HemsSettings, plan: Plan | None, now: datetime
    ) -> None:
        """What the parts on "Vorschau" would send: logged, counted, never sent."""
        parts = self._parts(hems, preview=True)
        sim = record.preview
        if parts == self._parts(hems, preview=False):
            record.preview = _Simulated()
            return
        config = hems.zone(zone.id)
        hour = _zone_hour(plan, zone.id, now)
        target = shift_of(hour.parts, config, hems.step_k, parts) if hour is not None and parts else 0.0
        if sim.override_until is not None and now >= sim.override_until:
            record.preview = sim = _Simulated(changed_at=sim.changed_at)
        if target == 0.0:
            if sim.override_c is not None:
                await self._would(zone.id, EndZoneOverride(zone_id=zone.id))
                record.preview = _Simulated(changed_at=now)
            return
        if (
            zone.mode != "auto"
            or zone.setback
            or zone.override
            or record.program_c is None
            or self._resting(record, now)
        ):
            return
        celsius = _within(round(record.program_c + target, 1), zone)
        if sim.override_c is not None and sim.shift_k == target:
            if sim.override_until is not None and sim.override_until - now <= RENEW_BEFORE:
                hours = self._hours(zone.id, parts, now)
                if await self._would(zone.id, SetZoneOverride(zone_id=zone.id, celsius=celsius, hours=hours)):
                    sim.override_until = now + timedelta(hours=hours)
            return
        if (
            target < sim.shift_k
            and sim.changed_at is not None
            and now - sim.changed_at < timedelta(minutes=hems.min_interval_min)
        ):
            return
        hours = self._hours(zone.id, parts, now)
        if await self._would(zone.id, SetZoneOverride(zone_id=zone.id, celsius=celsius, hours=hours)):
            record.preview = _Simulated(celsius, now + timedelta(hours=hours), target, now)

    def _blocked(self, zone: Zone, record: _Zone, now: datetime) -> str | None:
        if not self._can_send():
            return "sendet nicht (Demo oder ohne myVAILLANT)"
        if self._resting(record, now):
            assert record.rest_until is not None
            return f"in Ruhe bis {record.rest_until.astimezone(self._zone):%H:%M} Uhr ({record.rest_reason})"
        if zone.mode != "auto":
            return f"Quick-Veto nur im Auto-Betrieb (jetzt {MODE_NAMES.get(str(zone.mode), zone.mode)})"
        if zone.setback or record.program_c is None:
            return "Programmwert unbekannt (Absenkung im Zeitprogramm?): Zeitprogramm auf durchgehend Komfort stellen"
        if zone.override and record.override_c is None:
            return "eine fremde Übersteuerung läuft"
        if record.retry_at is not None and now < record.retry_at:
            return f"nach einem Fehler: neuer Versuch ab {record.retry_at.astimezone(self._zone):%H:%M} Uhr"
        return None

    @staticmethod
    def _retrying(record: _Zone, now: datetime) -> bool:
        return record.retry_at is not None and now < record.retry_at

    def _resting(self, record: _Zone, now: datetime) -> bool:
        if record.rest_until is not None and now >= record.rest_until:
            record.rest_until, record.rest_reason = None, None
        return record.rest_until is not None

    def _hours(self, zone_id: str, parts: frozenset[Part], now: datetime) -> int:
        """Until the next planned change, in whole hours, 1 to 4."""
        upcoming = self._next_change(zone_id, parts, now)
        if upcoming is None:
            return OVERRIDE_MAX_H
        hours = math.ceil((upcoming - now).total_seconds() / 3600)
        return max(OVERRIDE_MIN_H, min(OVERRIDE_MAX_H, hours))

    def _next_change(self, zone_id: str, parts: frozenset[Part], now: datetime) -> datetime | None:
        plan = self._hems.plan
        if plan is None:
            return None
        config = self._settings.current.hems.zone(zone_id)
        step = self._settings.current.hems.step_k
        current: float | None = None
        for hour in plan.hours:
            entry = next((item for item in hour.zones if item.zone_id == zone_id), None)
            if entry is None or hour.start + timedelta(hours=1) <= now:
                continue
            shift = shift_of(entry.parts, config, step, parts)
            if current is None:
                current = shift
            elif shift != current:
                return hour.start
        return None

    # --- floor heating off/auto (F1) ------------------------------------------------------------

    async def _zone1(self, heat_pump: HeatPumpState, hems: HemsSettings, now: datetime) -> None:
        zone_id = self._hems.floor_zone()
        zone = next((item for item in heat_pump.zones if item.id == zone_id), None)
        if zone is None:
            return
        record = self._zones.setdefault(zone.id, _Zone())
        stage = hems.effective(Function.ZONE1)
        control = stage is PlannerMode.CONTROL and self._can_send() and not self._zone1_halted
        would = self._hems.zone1_would()
        if not control:
            if record.off and self._can_send() and not self._retrying(record, now):
                await self._send(zone.id, record, SetZoneMode(zone_id=zone.id, mode="auto"), "F1 steuert nicht mehr")
            if stage is not PlannerMode.OFF and would is not None and would != record.preview_mode:
                if record.preview_mode is not None and self._gate.budget is not None:
                    self._gate.budget.count_would()
                record.preview_mode = would
            return
        record.preview_mode = None
        if self._resting(record, now) or would is None or self._gate.settling(Control.ZONE_MODE, zone.id):
            return
        if zone.mode not in ("off", "auto"):
            mode = MODE_NAMES.get(str(zone.mode), zone.mode)
            record.waiting = f"{zone.label} steht auf {mode}; F1 schaltet nur zwischen Aus und Auto"
            return
        if would == "auto" and zone.mode == "off":
            if await self._send(zone.id, record, SetZoneMode(zone_id=zone.id, mode="auto"), "Wärmepumpe läuft"):
                record.off, record.auto_at = False, now
        elif would == "off" and zone.mode == "auto" and not self._hems.frost_active:
            if record.auto_at is not None and now - record.auto_at < ZONE1_OFF_GAP:
                return
            budget = self._gate.budget
            if budget is not None and not budget.allows(fallback=False):
                record.waiting = "Tagesgrenze der Befehle an myVAILLANT erreicht"
                return
            if self._retrying(record, now):
                return
            if await self._send(zone.id, record, SetZoneMode(zone_id=zone.id, mode="off"), "Wärmepumpe steht"):
                record.off = True

    # --- sending -------------------------------------------------------------------------------

    async def _send(self, zone_id: str, record: _Zone, command: Command, why: str) -> bool:
        """Through the write gate; True when it went out (also with an unknown outcome)."""
        now = self._clock()
        try:
            result = await self._gate.submit(command, expected_mode=OperatingMode.LIVE, source=Source.PLANNER)
        except WriteOutcomeUnknown:
            sent = True
        except (WriteRejected, WriteNotExecuted) as err:
            record.retry_at = now + RETRY_AFTER
            _LOGGER.info("Planner command not sent: %s", err)
            return False
        else:
            sent = result.sent
            if not sent:
                record.retry_at = now + RETRY_AFTER
        if sent:
            record.retry_at = None
            if isinstance(command, SetZoneMode):
                record.mode_sent = command.mode
                record.off = command.mode == "off"
            if isinstance(command, EndZoneOverride):
                record.override_c, record.override_until, record.shift_k = None, None, 0.0
                record.changed_at = now
            self._remember(Action(now, f"{_describe(self._state.current, command)} ({why})", sent=True))
        return sent

    async def _would(self, zone_id: str, command: Command) -> bool:
        try:
            await self._gate.submit(command, source=Source.PLANNER, preview=True)
        except (WriteRejected, WriteOutcomeUnknown, WriteNotExecuted):
            return False
        if self._gate.budget is not None:
            self._gate.budget.count_would()
        self._remember(Action(self._clock(), _describe(self._state.current, command), sent=False))
        return True

    def _remember(self, action: Action) -> None:
        self._actions = [*self._actions, action][-ACTIONS_KEPT:]

    def _can_send(self) -> bool:
        return self._gate.mode is OperatingMode.LIVE and self._gate.connection is Connection.MYVAILLANT

    def _wants_control(self, hems: HemsSettings) -> bool:
        return any(hems.effective(function) is PlannerMode.CONTROL for function in Function)

    def _parts(self, hems: HemsSettings, *, preview: bool) -> frozenset[Part]:
        """The parts of the shift carried out: on "Steuern" (preview False) or on "Vorschau" and up."""
        if not preview and not self._can_send():
            return frozenset()
        wanted = (PlannerMode.PREVIEW, PlannerMode.CONTROL) if preview else (PlannerMode.CONTROL,)
        return frozenset(part for part, function in PART_FUNCTION.items() if hems.effective(function) in wanted)

    # --- status, life sign, automation ------------------------------------------------------------

    def status(self) -> ControlStatus:
        heat_pump = self._state.current
        labels = {zone.id: zone.label for zone in heat_pump.zones} if heat_pump is not None else {}
        budget = self._gate.budget
        today = self._clock().astimezone(self._zone).date()
        zones = [
            ZoneState(
                zone_id=zone_id,
                label=labels.get(zone_id, zone_id),
                override_c=record.override_c,
                override_until=record.override_until,
                shift_k=record.shift_k,
                off=record.off,
                rest_until=record.rest_until,
                rest_reason=record.rest_reason,
                waiting=record.waiting,
                preview_shift_k=record.preview.shift_k if record.preview.override_c is not None else None,
            )
            for zone_id, record in self._zones.items()
            if zone_id in labels
        ]
        return ControlStatus(
            live=self._can_send() and self._wants_control(self._settings.current.hems),
            problems=list(self._problems),
            budget=budget.status() if budget is not None else None,
            zones=zones,
            zone1_halted=self._zone1_halted,
            automation_switches_today=self._automation[1] if self._automation and self._automation[0] == today else 0,
            actions=list(reversed(self._actions)),
            lifesign_entity=LIFESIGN_ENTITY,
            lifesign_at=self._alive_at,
        )

    def resume_zone1(self) -> None:
        """The user chose the stage of F1 again: it no longer stops for the automation."""
        if self._zone1_halted:
            self._zone1_halted = False
            self._automation = None
            self._save()

    def sensors(self) -> list[Sensor]:
        """The life sign for the zones' emergency automation; reported whatever the export groups."""
        alive = self._alive_at
        hems = self._settings.current.hems
        budget = self._gate.budget
        return [
            Sensor(
                entity_id=LIFESIGN_ENTITY,
                state=alive.replace(second=0, microsecond=0).isoformat() if alive is not None else "unavailable",
                attributes={
                    "friendly_name": "Zonensteuerung Lebenszeichen",
                    "device_class": "timestamp",
                    "icon": "mdi:heart-pulse",
                    "stufe": hems.mode.value,
                    # The zones the app switched off, for the emergency automation.
                    "ausgeschaltet": sorted(zone_id for zone_id, record in self._zones.items() if record.off),
                    "quick_veto": sorted(
                        zone_id for zone_id, record in self._zones.items() if record.override_c is not None
                    ),
                    "befehle_heute": budget.status().sent if budget is not None else None,
                },
                always=True,
            )
        ]

    def automation(self) -> str:
        """The Home Assistant automation: zones the app switched off back to auto when it falls silent."""
        floor = self._hems.floor_zone()
        condition = (
            "{% set t = as_datetime(states('" + LIFESIGN_ENTITY + "'), none) %}\n"
            "          {{ t is none or (now() - t).total_seconds() > 900 }}"
        )
        lines = [
            'alias: "Vaillant EEBUS: Zonen auf Auto, wenn die App schweigt"',
            "description: >-",
            "  Erzeugt von der App Vaillant EEBUS. Kommt 15 min kein Lebenszeichen der Zonensteuerung, gehen die",
            "  Zonen, die die App ausgeschaltet hat, zurück auf Auto. Nach einem Neustart von Home Assistant",
            "  bekommt die App erst 15 min Zeit.",
            "mode: single",
            "triggers:",
            "  - trigger: template",
            "    value_template: >",
            "      " + condition.replace("\n          ", "\n      "),
            "  - trigger: homeassistant",
            "    event: start",
            "conditions:",
            "  - condition: template",
            "    value_template: >",
            "      " + condition.replace("\n          ", "\n      "),
            "actions:",
            "  - if:",
            "      - condition: template",
            "        value_template: \"{{ trigger.platform == 'homeassistant' }}\"",
            "    then:",
            '      - delay: "00:15:00"',
            "      - condition: template",
            "        value_template: >",
            "          " + condition,
            "  # Die Zonen, die die App ausgeschaltet hat (aus dem Lebenszeichen).",
            "  - variables:",
            "      aus: \"{{ state_attr('" + LIFESIGN_ENTITY + "', 'ausgeschaltet') }}\"",
            "  - if:",
            "      - condition: template",
            '        value_template: "{{ aus is not none }}"',
            "    then:",
            "      - repeat:",
            '          for_each: "{{ aus }}"',
            "          sequence:",
            "            - action: climate.set_hvac_mode",
            "              target:",
            '                entity_id: "{{ repeat.item }}"',
            "              data:",
            "                hvac_mode: auto",
            "    # Ohne Lebenszeichen (Home Assistant neu gestartet, die App fehlt): die Zone mit Fußbodenheizung.",
            "    else:",
        ]
        if floor is not None:
            lines += [
                "      - action: climate.set_hvac_mode",
                "        target:",
                f"          entity_id: {floor}",
                "        data:",
                "          hvac_mode: auto",
            ]
        else:
            lines += ["      # Keine Zone mit Fußbodenheizung eingestellt.", "      - stop: Keine Zone"]
        return "\n".join(lines) + "\n"

    # --- saving ------------------------------------------------------------------------------------

    def _save(self) -> None:
        payload = {
            "zones": {
                zone_id: {
                    "override_c": record.override_c,
                    "override_until": _iso(record.override_until),
                    "shift_k": record.shift_k,
                    "changed_at": _iso(record.changed_at),
                    "off": record.off,
                    "rest_until": _iso(record.rest_until),
                    "rest_reason": record.rest_reason,
                    "program_c": record.program_c,
                }
                for zone_id, record in self._zones.items()
            },
            "zone1_halted": self._zone1_halted,
            "automation": [self._automation[0].isoformat(), self._automation[1]] if self._automation else None,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=STATE_FILE, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(payload, file)
                os.replace(tmp, self._path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as err:
            _LOGGER.warning("Saving the zone control failed: %s", err)

    def _load(self) -> None:
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            _LOGGER.warning("Reading the zone control failed: %s", err)
            return
        if not isinstance(raw, dict):
            return
        try:
            for zone_id, item in (raw.get("zones") or {}).items():
                self._zones[str(zone_id)] = _Zone(
                    override_c=_float(item.get("override_c")),
                    override_until=_time(item.get("override_until")),
                    shift_k=float(item.get("shift_k") or 0.0),
                    changed_at=_time(item.get("changed_at")),
                    off=bool(item.get("off")),
                    rest_until=_time(item.get("rest_until")),
                    rest_reason=item.get("rest_reason") if isinstance(item.get("rest_reason"), str) else None,
                    program_c=_float(item.get("program_c")),
                )
            self._zone1_halted = bool(raw.get("zone1_halted"))
            automation = raw.get("automation")
            if isinstance(automation, list) and len(automation) == 2:
                self._automation = (date.fromisoformat(automation[0]), int(automation[1]))
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning("The zone control file is invalid, ignored: %s", err)
            self._zones = {}


# --- helpers ---------------------------------------------------------------------------------


def _zone_hour(plan: Plan | None, zone_id: str, now: datetime) -> ZoneHour | None:
    hour = _plan_hour(plan, now)
    if hour is None:
        return None
    return next((item for item in hour.zones if item.zone_id == zone_id), None)


def _plan_hour(plan: Plan | None, now: datetime) -> PlanHour | None:
    if plan is None:
        return None
    current = hour_start(now)
    return next((hour for hour in plan.hours if hour.start == current), None)


def _reason(plan: Plan | None, now: datetime) -> str:
    hour = _plan_hour(plan, now)
    return f": {hour.reasons[0]}" if hour is not None and hour.reasons else ""


def _within(celsius: float, zone: Zone) -> float:
    setpoint = zone.setpoint
    if setpoint is None:
        return celsius
    return max(setpoint.min, min(setpoint.max, celsius))


def _same(value: float | None, other: float | None) -> bool:
    return value is not None and other is not None and abs(value - other) < 0.05


def _describe(heat_pump: HeatPumpState | None, command: Command) -> str:
    zone_id = getattr(command, "zone_id", "")
    label = next((zone.label for zone in heat_pump.zones if zone.id == zone_id), zone_id) if heat_pump else zone_id
    match command:
        case SetZoneOverride(celsius=celsius, hours=hours):
            return f"{label}: Quick-Veto {_celsius(celsius)} für {hours:g} h"
        case EndZoneOverride():
            return f"{label}: Quick-Veto beenden"
        case SetZoneMode(mode=mode):
            return f"{label}: Betriebsart {MODE_NAMES.get(mode, mode)}"
        case _:
            return label


def _celsius(value: float | None) -> str:
    return f"{value:.1f}{NBSP}°C".replace(".", ",") if value is not None else "?"


def _signed(value: float) -> str:
    text = f"{abs(value):g}".replace(".", ",")
    return f"+{text}" if value > 0 else f"{MINUS}{text}"


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


async def _despite_cancellation(work: Coroutine[Any, Any, None]) -> None:
    """Run work to its end even if the waiting task is cancelled again meanwhile (stopping the app)."""
    task = asyncio.create_task(work)
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
