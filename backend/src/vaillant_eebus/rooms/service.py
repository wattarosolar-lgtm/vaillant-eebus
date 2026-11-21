"""The room control as one service for main and api (1.4.0, punkt1-trv-regelung.md).

Every five minutes, right after the energy management took its samples (one
common beat), it works out each room's thermostat setpoint and hands it to
the write gate: as a preview ("würde") or - with the room in "Steuern", live
- as a command. Between the rooms of a radiator zone it redistributes (a cold
room gets water from throttled, well supplied rooms) before it asks for a
higher flow; that request stays a hint until the zones are controlled
(1.5.0). Every minute it reports its life sign to Home Assistant, follows a
test run and counts the valves' movements. When it stops - the app ends,
the room control or a room leaves "Steuern", demo mode - every thermostat it
has set gets its emergency value.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import statistics
import tempfile
from collections import deque
from collections.abc import Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from vaillant_eebus.ha_client import EntityState, HomeAssistantUnavailable
from vaillant_eebus.ha_export import PREFIX, Sensor
from vaillant_eebus.ha_import import room_celsius
from vaillant_eebus.hems.config import ZoneKind
from vaillant_eebus.hems.room_model import FlowHint, Supply
from vaillant_eebus.hems.samples import room_series
from vaillant_eebus.hems.service import HemsService
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.room_data import ProbePair, RoomControlMode, RoomStage, at_least, effective_stage
from vaillant_eebus.rooms.controller import MODE_NAMES, Correction, Decision, RoomInputs, RoomMode, decide
from vaillant_eebus.rooms.curve import Step, ValveCurve, curve_from_json, curve_to_json
from vaillant_eebus.rooms.supply import SUPPLY_NAMES, HeatSupply, SupplyNow, heat_supply, restart_temperature
from vaillant_eebus.rooms.testrun import (
    STATE_NAMES,
    Action,
    TestInputs,
    TestRun,
    TestState,
    abort,
    advance,
    describe,
    run_from_json,
    run_to_json,
)
from vaillant_eebus.rooms.thermostats import MODEL_NAMES, Thermostat, TrvModel, blocking, read, side_entities
from vaillant_eebus.settings import OperatingMode, Room, Settings, SettingsStore
from vaillant_eebus.state import StateHub
from vaillant_eebus.write_gate import (
    ROOM_REASON_NAMES,
    Held,
    RoomGate,
    RoomReason,
    SetThermostatSetpoint,
    WriteNotExecuted,
    WriteOutcomeUnknown,
    WriteRejected,
)

_LOGGER = logging.getLogger(__name__)

LIFESIGN_ENTITY = f"sensor.{PREFIX}raeume_lebenszeichen"
STATE_FILE = "rooms.json"
TICK = 60.0
DISCOVER_EVERY = timedelta(minutes=15)
RECOMMEND_EVERY = timedelta(hours=1)
RELEASE_TIMEOUT = 15.0
# Warm water must arrive this long before a room counts as undersupplied.
HEAT_FOR = timedelta(minutes=30)
REDISTRIBUTE_CHECK = timedelta(minutes=45)
DONOR_MAX = timedelta(hours=2)
DONOR_REST = timedelta(hours=1)
HELPS_FAILURES = 3
MOVE_POINTS = 5.0
# After a window closed, the way back from its value is exempt from the interval this long.
WINDOW_RETURN = timedelta(minutes=30)
# In a room measured at its thermostat a window is not believed this long after the app's own command or
# after the valve closed: the sensor at the radiator then falls fast by itself.
WINDOW_QUIET = timedelta(minutes=30)
VALVE_OPEN = 30.0
VALVE_SHUT = 5.0
# The life sign stops when the control's beat (every 5 min) has not run for this long.
HEALTHY_FOR = timedelta(minutes=12)
# A rejected command is tried again with the same value after this long.
RETRY_REJECTED = timedelta(minutes=30)
PROBE_FRESH = timedelta(minutes=15)
PROBE_KEEP = 60
TREND_OVER = timedelta(minutes=30)
RECOMMEND_DAYS = 7
RECOMMEND_MIN_SAMPLES = 3 * 288
MODE_TEXTS = {
    RoomControlMode.OFF: "Raumregelung ausgeschaltet.",
    RoomControlMode.OBSERVE: "Raumregelung auf Beobachten: Sie lernt und zeigt Fenster und Versorgung als „würde“.",
    RoomControlMode.PREVIEW: "Raumregelung auf Vorschau: Sie rechnet die Sollwerte und sendet nichts.",
    RoomControlMode.CONTROL: "Raumregelung auf Steuern: Räume in „Steuern“ bekommen ihre Sollwerte.",
}
STAGE_NAMES = {
    RoomControlMode.OFF: "Aus",
    RoomControlMode.OBSERVE: "Beobachten",
    RoomControlMode.PREVIEW: "Vorschau",
    RoomControlMode.CONTROL: "Steuern",
}


@dataclass(frozen=True, slots=True)
class ProbeStatus:
    flow_entity: str | None
    return_entity: str | None
    room_id: str | None
    room_name: str | None
    since: datetime | None
    flow_c: float | None
    return_c: float | None
    flow_age_min: float | None
    return_age_min: float | None
    # How often the sensors report (median minutes between reports) and their smallest change seen.
    report_minutes: float | None
    resolution_k: float | None


@dataclass(frozen=True, slots=True)
class TestStatus:
    # Not a test case (pytest collects classes named Test...).
    __test__ = False

    state: TestState
    state_name: str
    message: str
    armed_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    phase: str | None
    setpoint_c: float | None
    commands: int
    steps: int
    nights: int


@dataclass(frozen=True, slots=True)
class CurveStatus:
    learned: bool
    runs: int
    complete_runs: int
    last_at: datetime | None
    last_ending: str | None
    opening_k: float | None
    full_k: float | None
    closing_k: float | None
    reaction_min: float | None
    levels: list[float]
    steps: list[Step]


@dataclass(frozen=True, slots=True)
class RoomControlRoom:
    room_id: str
    name: str
    zone_id: str | None
    stage: RoomStage
    effective: RoomControlMode
    trv_entity: str | None
    model: TrvModel | None
    model_name: str | None
    at_thermostat: bool
    trv_setpoint_c: float | None
    trv_c: float | None
    valve: float | None
    target_c: float | None
    setpoint_c: float | None
    mode: RoomMode | None
    mode_name: str | None
    reason_name: str | None
    note: str
    correction_k: float
    offset_k: float
    commands_today: int
    commands_limit: int
    test_commands_today: int
    preview_commands_today: int
    movements_today: int
    pending_c: float | None
    last_sent: datetime | None
    paused_until: datetime | None
    warnings: list[str]
    side: dict[str, str]
    test: TestStatus | None
    curve: CurveStatus
    recommendation: str | None
    donor: bool
    cold: bool
    can_control: bool
    control_blocked: str | None


@dataclass(frozen=True, slots=True)
class ZoneControl:
    zone_id: str
    label: str
    supply: HeatSupply
    supply_name: str
    remaining_min: float | None
    buffer_c: float | None
    # "keins nötig", "läuft", "hat gereicht", "ausgeschöpft"
    redistribution: str
    cold_room: str | None
    donors: list[str]
    flow_hint: FlowHint | None
    flow_text: str | None


@dataclass(frozen=True, slots=True)
class RoomControlStatus:
    mode: RoomControlMode
    app_mode: OperatingMode
    lifesign_entity: str
    lifesign_at: datetime | None
    test_start: str
    test_latest_start: str
    test_end: str
    probe: ProbeStatus
    rooms: list[RoomControlRoom]
    zones: list[ZoneControl]
    problems: list[str]


@dataclass
class _RoomState:
    correction: Correction = field(default_factory=Correction)
    decision: Decision | None = None
    window_open: bool = False
    window_closed_at: datetime | None = None
    setpoint_c: float | None = None
    setpoint_changed_at: datetime | None = None
    rest_until: datetime | None = None
    helps_ok: int = 0
    helps_failed: int = 0
    recommendation: str | None = None
    # K/h over the last half hour, from the last beat.
    trend_k_per_h: float | None = None
    # A window shown by the sensor at the radiator was not believed (logged once).
    window_ignored: bool = False
    # The last command that was rejected or failed: (value, reason, when), not tried again for a while.
    refused: tuple[float, RoomReason, datetime] | None = None


@dataclass
class _Movements:
    day: date | None = None
    count: int = 0
    last: float | None = None
    # The value a minute ago and when the valve last closed (from more than 30 % to 5 % or less).
    previous: float | None = None
    closed_at: datetime | None = None


@dataclass
class _Redistribution:
    cold: str
    stage: RoomControlMode
    started_at: datetime
    donors: set[str]
    outcome: str = "läuft"
    counted: bool = False


@dataclass
class _Zone:
    supply: SupplyNow = field(default_factory=lambda: SupplyNow(HeatSupply.UNKNOWN))
    heat_since: datetime | None = None
    redistribution: _Redistribution | None = None
    # What the last redistribution came to, shown until the next one.
    outcome: str = "keins nötig"


class RoomControl:
    def __init__(
        self,
        settings: SettingsStore,
        state: StateHub,
        hems: HemsService,
        gate: RoomGate,
        home_assistant: HomeAssistant,
        log: LogStore,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._state = state
        self._hems = hems
        self._gate = gate
        self._home_assistant = home_assistant
        self._log = log
        self._path = data_dir / STATE_FILE
        self._zone = zone
        self._clock = clock
        self._rooms: dict[str, _RoomState] = {}
        self._curves: dict[str, ValveCurve] = {}
        self._movements: dict[str, _Movements] = {}
        self._zones: dict[str, _Zone] = {}
        self._thermostats: dict[str, Thermostat] = {}
        self._side: dict[str, dict[str, str]] = {}
        self._side_states: dict[str, EntityState] = {}
        self._discovered_at: datetime | None = None
        self._recommended_at: datetime | None = None
        self._restart_c: float | None = None
        self._test: TestRun | None = None
        # A test run that was interrupted by a restart: its thermostat goes back to this value.
        self._restore: tuple[str, float] | None = None
        self._alive_at: datetime | None = None
        # The last beat of the control that ran through, and the start as grace for the first one.
        self._ticked_at: datetime | None = None
        self._started_at = clock()
        self._stale_logged = False
        self._probe_reports: dict[str, deque[tuple[datetime, float]]] = {}
        self._problems: list[str] = []
        self._lock = asyncio.Lock()
        self._load()
        hems.on_sampled(self.tick)
        hems.set_flow_advisor(self.flow_advice)
        hems.sources.on_change(self._source_changed)
        if home_assistant.exporter is not None:
            home_assistant.exporter.add_source(self.sensors, always=True)

    # --- loops ---------------------------------------------------------------------------

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.minute()
                except Exception:
                    # A fault here must not stop the rest of the app; the life sign then stops.
                    _LOGGER.exception("Room control failed")
                await asyncio.sleep(TICK)
        finally:
            self._save()
            await _despite_cancellation(self._release_on_stop())

    async def minute(self) -> None:
        """Every minute: life sign, thermostats' other entities, movements and the test run."""
        now = self._clock()
        async with self._lock:
            await self._discover(now)
            self._refresh_thermostats(now)
            self._count_movements(now)
            await self._restore_after_restart()
            await self._advance_test(now)
        if self._healthy(now):
            self._alive_at = now
            self._stale_logged = False
        elif not self._stale_logged:
            self._stale_logged = True
            self._log.add(
                LogCategory.ROOMS,
                "Die Raumregelung rechnet seit über 12 Minuten nicht mehr; das Lebenszeichen bleibt stehen, damit die "
                "Notfall-Automation greifen kann.",
                problem=True,
            )

    async def tick(self, now: datetime) -> None:
        """Every five minutes, right after the energy management's samples."""
        async with self._lock:
            settings = self._settings.current
            mode = settings.room_control.mode
            if mode is RoomControlMode.OFF:
                self._refresh_thermostats(now)
                self._ticked_at = now
                return
            await self._update_zones(now, settings)
            self._redistribute(now, settings)
            # Fresh right before deciding: the awaits above may have brought new states.
            self._refresh_thermostats(now)
            for room in settings.rooms:
                if room.details.trv_entity is None:
                    continue
                stage = effective_stage(mode, room.details.stage)
                if not at_least(stage, RoomControlMode.PREVIEW):
                    self._rooms.setdefault(room.entity_id, _RoomState()).decision = None
                    continue
                if self._testing(room):
                    continue
                await self._control(room, stage, now, settings)
            if self._recommended_at is None or now - self._recommended_at >= RECOMMEND_EVERY:
                self._recommended_at = now
                await self._recommend(now, settings)
            self._save()
            self._ticked_at = now

    # --- for the API -----------------------------------------------------------------------

    async def set_mode(self, mode: RoomControlMode, *, confirm: bool) -> None:
        settings = self._settings.current
        before = settings.room_control.mode
        if mode is before:
            return
        if mode is RoomControlMode.CONTROL:
            if settings.operating_mode is not OperatingMode.LIVE:
                raise WriteRejected(409, "„Steuern“ geht nur im Live-Betrieb.")
            if not confirm:
                raise WriteRejected(400, "„Steuern“ muss ausdrücklich bestätigt werden.")
        async with self._lock:
            if before is RoomControlMode.CONTROL:
                await self.release(f"Raumregelung auf {STAGE_NAMES[mode]}")
            if mode is RoomControlMode.OFF and self._test is not None and self._test.active:
                await self._end_test(abort(self._test, self._clock(), "Raumregelung ausgeschaltet"))
            self._settings.update(
                lambda current: replace(current, room_control=replace(current.room_control, mode=mode))
            )
        self._log.add(LogCategory.ROOMS, MODE_TEXTS[mode])

    async def set_stage(self, room_id: str, stage: RoomStage, *, confirm: bool) -> None:
        settings = self._settings.current
        room = self._room(room_id)
        before = room.details.stage
        if stage is before:
            return
        if stage is RoomStage.CONTROL:
            problem = self._control_blocked(room, settings)
            if problem is not None:
                raise WriteRejected(409, problem)
            if not confirm:
                raise WriteRejected(400, "„Steuern“ muss ausdrücklich bestätigt werden.")
        async with self._lock:
            if before is RoomStage.CONTROL:
                await self.release(f"{room.name} auf {STAGE_NAMES[RoomControlMode(stage.value)]}", {room.entity_id})
            # Only the stage, on the room as it is now (the editor may have saved meanwhile).
            self._settings.update(
                lambda current: replace(
                    current,
                    rooms=tuple(
                        replace(item, details=replace(item.details, stage=stage)) if item.entity_id == room_id else item
                        for item in current.rooms
                    ),
                )
            )
        self._log.add(LogCategory.ROOMS, f"{room.name}: Raumregelung auf {STAGE_NAMES[RoomControlMode(stage.value)]}.")

    async def set_probe(self, probe: ProbePair) -> None:
        settings = self._settings.current
        if probe.flow_entity is not None and probe.flow_entity == probe.return_entity:
            raise WriteRejected(422, "Vorlauf und Rücklauf brauchen zwei verschiedene Fühler.")
        if probe.room_id is not None:
            self._room(probe.room_id)
        before = settings.room_control.probe
        moved = probe.room_id != before.room_id
        since = self._clock() if moved and probe.room_id is not None else (before.since if not moved else None)
        probe = replace(probe, since=since)
        async with self._lock:
            run = self._test
            if run is not None and run.active and (moved or probe.flow_entity is None) and run.room_id != probe.room_id:
                await self._end_test(abort(run, self._clock(), "Das Fühlerpaar hängt jetzt woanders"))
            self._settings.update(
                lambda current: replace(current, room_control=replace(current.room_control, probe=probe))
            )
        self._home_assistant.watch()
        where = self._room(probe.room_id).name if probe.room_id else "nicht an einem Heizkörper"
        self._log.add(
            LogCategory.SETTINGS,
            f"Fühlerpaar: Vorlauf {probe.flow_entity or 'keiner'}, Rücklauf {probe.return_entity or 'keiner'}, "
            f"hängt {where}.",
        )

    async def arm_test(self, room_id: str, *, confirm: bool) -> None:
        settings = self._settings.current
        room = self._room(room_id)
        trv = room.details.trv_entity
        if trv is None:
            raise WriteRejected(409, f"{room.name}: Es ist kein Thermostat gewählt.")
        if settings.operating_mode is not OperatingMode.LIVE:
            raise WriteRejected(409, "Ein Testlauf stellt das Thermostat und geht nur im Live-Betrieb.")
        if settings.room_control.mode is RoomControlMode.OFF:
            raise WriteRejected(409, "Die Raumregelung ist aus.")
        probe = settings.room_control.probe
        if probe.flow_entity is None or probe.return_entity is None:
            raise WriteRejected(409, "Für den Testlauf bitte zuerst das Fühlerpaar (Vorlauf und Rücklauf) einrichten.")
        if probe.room_id != room_id:
            raise WriteRejected(409, f"Das Fühlerpaar hängt nicht am Heizkörper von {room.name}.")
        if self._test is not None and self._test.active:
            raise WriteRejected(409, "Es ist schon ein Testlauf vorgemerkt; es geht nur einer zur Zeit.")
        if not confirm:
            raise WriteRejected(400, "Der Testlauf muss ausdrücklich bestätigt werden.")
        async with self._lock:
            self._test = TestRun(room_id=room_id, trv=trv, armed_at=self._clock())
            self._test.message = "Wartet auf die Nacht"
            self._save()
        start = settings.room_control.test_start.strftime("%H:%M")
        self._log.add(LogCategory.ROOMS, f"Testlauf {room.name} vorgemerkt: startet ab {start} Uhr.")

    async def cancel_test(self, room_id: str) -> None:
        async with self._lock:
            run = self._test
            if run is None or not run.active or run.room_id != room_id:
                raise WriteRejected(409, "Für diesen Raum ist kein Testlauf vorgemerkt.")
            await self._end_test(abort(run, self._clock(), "vom Nutzer abgebrochen"))

    async def before_demo(self) -> None:
        """The app goes to demo mode: hand the thermostats back while still live."""
        async with self._lock:
            if self._test is not None and self._test.active:
                await self._end_test(abort(self._test, self._clock(), "Demo-Betrieb"), restore=False)
            await self.release("Demo-Betrieb")

    async def before_rooms_change(self, rooms: Iterable[tuple[str, str | None]], reason: str) -> None:
        """Before the rooms are saved: rooms that go away or lose their thermostat hand it back first.

        rooms are the (room, thermostat) pairs as they will be.
        """
        keep = set(rooms)
        touched = set(self._gate.touched())
        affected = {
            room.entity_id
            for room in self._settings.current.rooms
            if room.details.trv_entity in touched and (room.entity_id, room.details.trv_entity) not in keep
        }
        if affected:
            async with self._lock:
                await self.release(reason, affected)

    def flow_advice(self, zone_id: str, hint: FlowHint) -> tuple[FlowHint, str | None] | None:
        """The flow hint of a radiator zone with rooms in preview or control: higher only when redistributing
        is exhausted and the heat pump runs."""
        settings = self._settings.current
        mode = settings.room_control.mode
        members = [
            room
            for room in settings.rooms
            if room.zone_id == zone_id
            and room.details.trv_entity is not None
            and at_least(effective_stage(mode, room.details.stage), RoomControlMode.PREVIEW)
        ]
        if not members or hint is not FlowHint.HIGHER:
            return None
        zone = self._zones.get(zone_id) or _Zone()
        if zone.supply.supply is HeatSupply.BUFFER:
            return FlowHint.HOLD, "Raum braucht Wärme (aus dem Puffer); Vorlauf höher erst, wenn die Wärmepumpe läuft"
        if zone.supply.supply is not HeatSupply.HEAT_PUMP:
            return FlowHint.HOLD, None
        red = zone.redistribution
        if red is not None and red.outcome == "ausgeschöpft":
            return FlowHint.HIGHER, f"Vorlauf höher: Umverteilen ausgeschöpft ({self._name(red.cold)})"
        for room in members:
            state = self._rooms.get(room.entity_id)
            if state is not None and _no_help(state) and self._hems.supply_of(room.entity_id).supply is Supply.UNDER:
                return FlowHint.HIGHER, f"Vorlauf höher: Umverteilen hilft {room.name} nicht"
        return FlowHint.HOLD, "erst umverteilen, dann Vorlauf"

    def sensors(self) -> list[Sensor]:
        """The life sign for the emergency automation; reported whatever the export groups."""
        settings = self._settings.current
        mode = settings.room_control.mode
        controlled = [
            room.name
            for room in settings.rooms
            if room.details.trv_entity and effective_stage(mode, room.details.stage) is RoomControlMode.CONTROL
        ]
        alive = self._alive_at
        return [
            Sensor(
                entity_id=LIFESIGN_ENTITY,
                state=alive.replace(second=0, microsecond=0).isoformat() if alive is not None else "unavailable",
                attributes={
                    "friendly_name": "Raumregelung Lebenszeichen",
                    "device_class": "timestamp",
                    "icon": "mdi:heart-pulse",
                    "stufe": STAGE_NAMES[mode],
                    "gesteuert": controlled,
                    # The thermostats the app has set, for the emergency automation.
                    "notfallwerte": self._emergency_values(),
                },
                always=True,
            )
        ]

    def _emergency_values(self) -> list[dict[str, Any]]:
        settings = self._settings.current
        values = []
        for kept in self._gate.touched_thermostats():
            room = next((item for item in settings.rooms if item.details.trv_entity == kept.trv), None)
            value = room.details.fallback_c() if room is not None else kept.fallback_c
            if value is None:
                continue
            limits = self._gate.known_limits(kept.trv)
            values.append({"entity_id": kept.trv, "temperature": limits.round(value) if limits else value})
        return values

    def automation(self) -> str:
        """The emergency automation for Home Assistant (template of punkt1-trv-regelung.md 7)."""
        rooms = [room for room in self._settings.current.rooms if room.details.trv_entity]
        condition = (
            "{% set t = as_datetime(states('" + LIFESIGN_ENTITY + "'), none) %}\n"
            "          {{ t is none or (now() - t).total_seconds() > 900 }}"
        )
        lines = [
            'alias: "Vaillant EEBUS: Thermostate auf Notfallwerte, wenn die App schweigt"',
            "description: >-",
            "  Erzeugt von der App Vaillant EEBUS. Kommt 15 min kein Lebenszeichen der Raumregelung, bekommt",
            "  jedes Thermostat seinen Notfallwert. Nach einem Neustart von Home Assistant bekommt die App",
            "  erst 15 min Zeit.",
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
            "  # Die Thermostate, die die App gerade gestellt hat, mit Notfallwerten (aus dem Lebenszeichen).",
            "  - variables:",
            "      gestellt: \"{{ state_attr('" + LIFESIGN_ENTITY + "', 'notfallwerte') }}\"",
            "  - if:",
            "      - condition: template",
            '        value_template: "{{ gestellt is not none }}"',
            "    then:",
            "      - repeat:",
            '          for_each: "{{ gestellt }}"',
            "          sequence:",
            "            - action: climate.set_temperature",
            "              target:",
            '                entity_id: "{{ repeat.item.entity_id }}"',
            "              data:",
            '                temperature: "{{ repeat.item.temperature }}"',
            "    # Ohne Lebenszeichen (Home Assistant neu gestartet, die App fehlt): alle Thermostate der Räume.",
            "    else:",
        ]
        for room in rooms:
            trv = room.details.trv_entity
            assert trv is not None
            value = room.details.fallback_c()
            limits = self._gate.known_limits(trv)
            if limits is not None:
                value = limits.round(value)
            source = "Notfallwert" if room.details.emergency_c is not None else "kein Notfallwert, Raum-Soll"
            lines += [
                "      - action: climate.set_temperature",
                "        target:",
                f"          entity_id: {trv}   # {_one_line(room.name)} ({source})",
                "        data:",
                f"          temperature: {value:g}",
            ]
        if not rooms:
            lines += ["      # Noch kein Raum hat ein Thermostat.", "      - stop: Kein Thermostat"]
        return "\n".join(lines) + "\n"

    def status(self) -> RoomControlStatus:
        settings = self._settings.current
        now = self._clock()
        self._refresh_thermostats(now)
        mode = settings.room_control.mode
        labels = self._labels()
        rooms = [self._room_status(room, settings, now) for room in settings.rooms]
        zones = []
        for zone_config in settings.hems.zones:
            if zone_config.kind is not ZoneKind.RADIATOR:
                continue
            zone = self._zones.get(zone_config.zone_id) or _Zone()
            red = zone.redistribution
            flow = self._hems.flow_of(zone_config.zone_id)
            hint, text = flow if flow is not None else (None, None)
            zones.append(
                ZoneControl(
                    zone_id=zone_config.zone_id,
                    label=labels.get(zone_config.zone_id, zone_config.zone_id),
                    supply=zone.supply.supply,
                    supply_name=SUPPLY_NAMES[zone.supply.supply],
                    remaining_min=zone.supply.remaining_min,
                    buffer_c=zone.supply.buffer_c,
                    redistribution=red.outcome if red is not None else zone.outcome,
                    cold_room=self._name(red.cold) if red is not None else None,
                    donors=sorted(self._name(donor) for donor in red.donors) if red is not None else [],
                    flow_hint=hint,
                    flow_text=text,
                )
            )
        return RoomControlStatus(
            mode=mode,
            app_mode=settings.operating_mode,
            lifesign_entity=LIFESIGN_ENTITY,
            lifesign_at=self._alive_at,
            test_start=settings.room_control.test_start.strftime("%H:%M"),
            test_latest_start=settings.room_control.test_latest_start.strftime("%H:%M"),
            test_end=settings.room_control.test_end.strftime("%H:%M"),
            probe=self._probe_status(settings, now),
            rooms=rooms,
            zones=zones,
            problems=list(self._problems),
        )

    async def release(self, reason: str, room_ids: set[str] | None = None) -> bool:
        """Every thermostat the app has set (of these rooms) gets its emergency value.

        Also a thermostat whose room is gone, with the emergency value the write gate kept.
        Returns False when one did not surely go out.
        """
        settings = self._settings.current
        done = []
        jobs = []
        for kept in self._gate.touched_thermostats():
            room = next((item for item in settings.rooms if item.details.trv_entity == kept.trv), None)
            room_id = room.entity_id if room is not None else kept.room_id
            if room_ids is not None and room_id not in room_ids:
                continue
            value = room.details.fallback_c() if room is not None else kept.fallback_c
            name = room.name if room is not None else (kept.name or kept.trv)
            if value is None:
                self._log.add(LogCategory.ROOMS, f"Thermostat {name}: kein Notfallwert bekannt.", problem=True)
                continue
            limits = self._gate.known_limits(kept.trv)
            if limits is not None:
                value = limits.round(value)
            command = SetThermostatSetpoint(
                room_id=room_id or "",
                celsius=value,
                reason=RoomReason.FALLBACK,
                trv=None if room is not None else kept.trv,
            )
            jobs.append(self._gate.submit(command))
            done.append(f"{name} {_celsius(value)}")
        if not jobs:
            return True
        results = await asyncio.gather(*jobs, return_exceptions=True)
        failed = [
            text
            for text, result in zip(done, results, strict=True)
            if isinstance(result, BaseException) or (not result.sent and result.held is not Held.SAME)
        ]
        self._log.add(
            LogCategory.ROOMS,
            f"Raumregelung gibt die Thermostate zurück ({reason}): {', '.join(done)}."
            + (f" Nicht sicher angekommen: {', '.join(failed)}." if failed else ""),
            problem=bool(failed),
        )
        return not failed

    # --- the rooms ---------------------------------------------------------------------------

    async def _control(self, room: Room, stage: RoomControlMode, now: datetime, settings: Settings) -> None:
        details = room.details
        trv = details.trv_entity
        assert trv is not None
        state = self._rooms.setdefault(room.entity_id, _RoomState())
        thermostat = self._thermostats.get(trv)
        local = now.astimezone(self._zone)
        room_c = room_celsius(self._hems.sources.get(room.entity_id))
        window = self._hems.window_open(room.entity_id)
        if window and room.measured_at_thermostat and self._just_changed(trv, stage, now, settings):
            if not state.window_ignored:
                self._log.add(
                    LogCategory.ROOMS,
                    f"Fenster im Raum {room.name} nicht beachtet: Kurz nach einem eigenen Befehl oder nach dem "
                    "Schließen des Ventils fällt der Fühler am Heizkörper von selbst.",
                )
            state.window_ignored = True
            window = False
        elif not window:
            state.window_ignored = False
        if state.window_open and not window:
            state.window_closed_at = now
            self._log.add(LogCategory.ROOMS, f"Fenster wieder zu: {room.name}.")
        if window and not state.window_open:
            verb = (
                "Thermostat auf den kleinsten Wert"
                if stage is RoomControlMode.CONTROL
                else "würde das Thermostat schließen"
            )
            self._log.add(LogCategory.ROOMS, f"Fenster offen erkannt: {room.name}, {verb}.")
        state.window_open = window
        plain = details.setpoint_at(local.time())
        if state.setpoint_c is not None and abs(state.setpoint_c - plain) >= 0.01:
            state.setpoint_changed_at = now
        state.setpoint_c = plain
        model = self._hems.room_model(room.entity_id)
        zone = self._zones.get(room.zone_id or "")
        heat = zone is not None and zone.supply.usable
        flow_c = self._flow_now(room.zone_id, settings)
        red = zone.redistribution if zone is not None else None
        donor = red is not None and room.entity_id in red.donors and red.stage is stage
        state.trend_k_per_h = await self._trend(room.entity_id, now)
        inputs = RoomInputs(
            now=now,
            local=local,
            details=details,
            at_thermostat=room.measured_at_thermostat,
            limits=self._gate.limits(trv),
            room_c=room_c,
            trend_k_per_h=state.trend_k_per_h,
            valve=self._hems.sources.number(details.valve_entity),
            trv_setpoint_c=thermostat.setpoint_c if thermostat else None,
            trv_c=thermostat.temperature_c if thermostat else None,
            window_open=window,
            window_closed_at=state.window_closed_at,
            heat=heat,
            donor=donor,
            curve=self._curves.get(trv),
            demand_valve=model.demand_valve.value if model is not None and model.demand_valve.learned else None,
            heating_rate=model.heating_rate.value if model is not None and model.heating_rate.learned else None,
            startup_min=model.startup_min.value if model is not None and model.startup_min.learned else None,
            flow_c=flow_c,
            expected_c=self._gate.expected(trv) if stage is RoomControlMode.CONTROL else None,
            sending=self._gate.sending(trv),
            setpoint_changed_at=state.setpoint_changed_at,
        )
        before = state.decision
        decision, state.correction = decide(inputs, state.correction)
        if decision.mode is RoomMode.SAFETY and (before is None or before.mode is not RoomMode.SAFETY):
            would = "" if stage is RoomControlMode.CONTROL else " (Vorschau)"
            self._log.add(
                LogCategory.ROOMS,
                f"{room.name} zu warm{would}: {_celsius(room_c or 0.0)} bei Soll {_celsius(decision.setpoint_c)}; "
                f"Thermostat auf {_celsius(decision.target_c or 0.0)}, Korrektur jetzt "
                f"{_kelvin(state.correction.value_k)}.",
            )
        limits = inputs.limits
        if (
            decision.reason in (RoomReason.CONTROL, RoomReason.PREHEAT)
            and state.window_closed_at is not None
            and now - state.window_closed_at < WINDOW_RETURN
            and limits is not None
            and inputs.trv_setpoint_c is not None
            and abs(inputs.trv_setpoint_c - limits.min_c) < 0.01
        ):
            # Back from the window's value at once (punkt1-trv-regelung.md 3a: window open and closed are exempt).
            decision = replace(decision, reason=RoomReason.WINDOW)
        state.decision = decision
        if decision.target_c is None or decision.reason is None:
            return
        if stage is RoomControlMode.CONTROL and thermostat is not None and blocking(thermostat):
            if thermostat.available and trv in self._gate.touched():
                # It must not stay on a window's or a large correction's value while the app cannot set it.
                self._log.add(
                    LogCategory.ROOMS,
                    f"Thermostat {room.name}: {blocking(thermostat)[0]} Die App gibt es auf den Notfallwert zurück.",
                    problem=True,
                )
                await self.release(f"Warnung bei {room.name}", {room.entity_id})
            return
        refused = state.refused
        if (
            refused is not None
            and abs(refused[0] - decision.target_c) < 0.01
            and refused[1] is decision.reason
            and now - refused[2] < RETRY_REJECTED
        ):
            return
        command = SetThermostatSetpoint(room_id=room.entity_id, celsius=decision.target_c, reason=decision.reason)
        try:
            result = await self._gate.submit(command, preview=stage is not RoomControlMode.CONTROL)
        except (WriteRejected, WriteNotExecuted) as err:
            state.refused = (decision.target_c, decision.reason, now)
            _LOGGER.info("Thermostat %s: %s", room.name, err)
            return
        except WriteOutcomeUnknown:
            return
        state.refused = None
        if result.sent and decision.manual:
            self._log.add(LogCategory.ROOMS, f"Handänderung am Thermostat {room.name} zurückgesetzt.")
        if result.held is Held.LIMIT:
            state.refused = (decision.target_c, decision.reason, now)

    async def _update_zones(self, now: datetime, settings: Settings) -> None:
        names = ["power", "buffer"]
        radiator = [zone for zone in settings.hems.zones if zone.kind is ZoneKind.RADIATOR]
        names += [f"flow:{zone.zone_id}" for zone in radiator]
        series = await self._hems.samples.series_async(names, now - timedelta(hours=1))
        power = list(series["power"])
        # The newest power reading of the heat pump, if fresher than the sample.
        heat_pump = self._state.current
        reading = heat_pump.measurements.get("power_consumption") if heat_pump is not None else None
        if reading is not None and heat_pump is not None and heat_pump.available and heat_pump.connected:
            power.append((now, reading.value))
        for zone_config in radiator:
            members = [room for room in settings.rooms if room.zone_id == zone_config.zone_id]
            temperatures = [
                value
                for room in members
                if not room.measured_at_thermostat
                and (value := room_celsius(self._hems.sources.get(room.entity_id))) is not None
            ]
            zone = self._zones.setdefault(zone_config.zone_id, _Zone())
            zone.supply = heat_supply(
                now=now,
                power=power,
                buffer=series["buffer"],
                flow=series[f"flow:{zone_config.zone_id}"],
                room_c=statistics.fmean(temperatures) if temperatures else None,
                restart_c=self._restart_c,
            )
            if zone.supply.usable:
                zone.heat_since = zone.heat_since or now
            else:
                zone.heat_since = None

    def _redistribute(self, now: datetime, settings: Settings) -> None:
        mode = settings.room_control.mode
        local = now.astimezone(self._zone).time()
        for zone_config in settings.hems.zones:
            if zone_config.kind is not ZoneKind.RADIATOR:
                continue
            zone = self._zones.setdefault(zone_config.zone_id, _Zone())
            members = {
                room.entity_id: room
                for room in settings.rooms
                if room.zone_id == zone_config.zone_id
                and room.details.trv_entity is not None
                and at_least(effective_stage(mode, room.details.stage), RoomControlMode.PREVIEW)
            }

            def stage_of(room: Room) -> RoomControlMode:
                return effective_stage(mode, room.details.stage)

            def temperature(room: Room) -> float | None:
                return room_celsius(self._hems.sources.get(room.entity_id))

            red = zone.redistribution
            heat_ok = zone.heat_since is not None and now - zone.heat_since >= HEAT_FOR
            if red is not None and red.cold not in members:
                zone.redistribution = red = None
            if red is None:
                if not heat_ok:
                    continue
                cold = [
                    room
                    for room in members.values()
                    if self._hems.supply_of(room.entity_id).supply is Supply.UNDER
                    and not self._hems.window_open(room.entity_id)
                    and temperature(room) is not None
                    and not _no_help(self._rooms.setdefault(room.entity_id, _RoomState()))
                    and not self._testing(room)
                ]
                if not cold:
                    continue
                coldest = min(cold, key=lambda room: (temperature(room) or 0.0) - room.details.setpoint_at(local))
                stage = stage_of(coldest)
                donors = {
                    room.entity_id
                    for room in members.values()
                    if room is not coldest
                    and stage_of(room) is stage
                    and room.details.throttle
                    and (value := temperature(room)) is not None
                    and value >= room.details.setpoint_at(local)
                    and self._hems.supply_of(room.entity_id).supply is not Supply.UNDER
                    and not self._hems.window_open(room.entity_id)
                    and not self._testing(room)
                    and _rested(self._rooms.setdefault(room.entity_id, _RoomState()), now)
                }
                red = _Redistribution(cold=coldest.entity_id, stage=stage, started_at=now, donors=donors)
                would = "" if stage is RoomControlMode.CONTROL else " (Vorschau)"
                if donors:
                    names = ", ".join(sorted(self._name(donor) for donor in donors))
                    self._log.add(
                        LogCategory.ROOMS,
                        f"{coldest.name} unterversorgt: Umverteilen{would}, gedrosselt werden {names}.",
                    )
                else:
                    red.outcome = "ausgeschöpft"
                    self._log.add(
                        LogCategory.ROOMS,
                        f"{coldest.name} unterversorgt, aber kein Raum kann abgeben{would}: Vorlauf höher wäre nötig "
                        "(die App hebt ihn erst mit dem Zonen-Release an).",
                    )
                zone.redistribution = red
                continue
            cold_room = members[red.cold]
            cold_state = self._rooms.setdefault(red.cold, _RoomState())
            cold_c = temperature(cold_room)
            target = cold_room.details.setpoint_at(local)
            recovered = self._hems.supply_of(red.cold).supply is not Supply.UNDER or (
                cold_c is not None and cold_c >= target - 0.1
            )
            for donor in sorted(red.donors):
                room = members.get(donor)
                value = temperature(room) if room is not None else None
                lower = room.details.setpoint_at(local) - room.details.comfort_down_k if room is not None else None
                if (
                    room is None
                    or now - red.started_at >= DONOR_MAX
                    or (value is not None and lower is not None and value <= lower + 0.05)
                    or self._hems.window_open(donor)
                ):
                    red.donors.discard(donor)
                    self._rooms.setdefault(donor, _RoomState()).rest_until = now + DONOR_REST
            if recovered or now - red.started_at >= DONOR_MAX:
                if not red.counted:
                    if recovered:
                        cold_state.helps_ok += 1
                    else:
                        cold_state.helps_failed += 1
                for donor in red.donors:
                    self._rooms.setdefault(donor, _RoomState()).rest_until = now + DONOR_REST
                zone.outcome = (
                    red.outcome if red.outcome != "läuft" else ("hat gereicht" if recovered else "ausgeschöpft")
                )
                text = "wieder versorgt" if recovered else "nach 2 h nicht versorgt"
                self._log.add(LogCategory.ROOMS, f"Umverteilen für {cold_room.name} beendet: {text}.")
                zone.redistribution = None
                continue
            if red.outcome == "läuft" and (now - red.started_at >= REDISTRIBUTE_CHECK or not red.donors):
                trend = cold_state.trend_k_per_h
                reaches = cold_c is not None and trend is not None and trend > 0 and trend >= target - cold_c
                if reaches:
                    red.outcome = "hat gereicht"
                    red.counted = True
                    cold_state.helps_ok += 1
                else:
                    red.outcome = "ausgeschöpft"
                    red.counted = True
                    cold_state.helps_failed += 1
                    self._log.add(
                        LogCategory.ROOMS,
                        f"Umverteilen reicht nicht für {cold_room.name}: Vorlauf höher wäre nötig (die App hebt "
                        "ihn erst mit dem Zonen-Release an; bis dahin von Hand in myVAILLANT möglich).",
                    )

    # --- test run --------------------------------------------------------------------------

    async def _advance_test(self, now: datetime) -> None:
        run = self._test
        if run is None or not run.active:
            return
        settings = self._settings.current
        room = next((item for item in settings.rooms if item.entity_id == run.room_id), None)
        if room is None or room.details.trv_entity != run.trv:
            await self._end_test(abort(run, now, "Raum oder Thermostat geändert"))
            return
        inputs = self._test_inputs(room, run.trv, now, settings)
        was = run.state
        action = advance(
            run,
            inputs,
            settings.room_control.test_start,
            settings.room_control.test_latest_start,
            settings.room_control.test_end,
        )
        if was is TestState.ARMED and run.state is TestState.RUNNING:
            self._log.add(LogCategory.ROOMS, f"Testlauf {room.name} gestartet.")
        if action.kind == "send" and action.celsius is not None:
            command = SetThermostatSetpoint(room_id=room.entity_id, celsius=action.celsius, reason=RoomReason.TEST)
            try:
                result = await self._gate.submit(command)
            except (WriteRejected, WriteNotExecuted, WriteOutcomeUnknown) as err:
                await self._end_test(abort(run, now, f"Befehl nicht ausgeführt ({err})"))
                return
            if not result.sent and result.held is not Held.SAME:
                await self._end_test(abort(run, now, f"Befehl nicht gesendet ({result.message})"))
                return
        elif action.kind == "end":
            await self._end_test(action, run=run)
            return
        self._save()

    async def _end_test(self, action: Action, *, run: TestRun | None = None, restore: bool = True) -> None:
        run = run or self._test
        if run is None:
            return
        room = next((item for item in self._settings.current.rooms if item.entity_id == run.room_id), None)
        name = room.name if room is not None else run.room_id
        if restore and action.celsius is not None and room is not None:
            await self._restore_thermostat(room, action.celsius, f"Testlauf {name} beendet")
        result = run.result()
        if result is not None:
            self._curves[run.trv] = self._curves.get(run.trv, ValveCurve()).add(result)
            summary = describe(result.steps)
            state = "fertig" if result.complete else "unvollständig"
            self._log.add(LogCategory.ROOMS, f"Testlauf {name} {state} ({run.message}): {summary}.")
        elif run.state is TestState.FAILED:
            self._log.add(LogCategory.ROOMS, f"Testlauf {name}: {run.message}", problem=True)
        elif run.state is TestState.ABORTED:
            self._log.add(LogCategory.ROOMS, f"Testlauf {name} abgebrochen: {run.message}.")
        self._save()

    def _test_inputs(self, room: Room, trv: str, now: datetime, settings: Settings) -> TestInputs:
        sources = self._hems.sources
        probe = settings.room_control.probe
        flow = _fresh(sources.get(probe.flow_entity), now)
        back = _fresh(sources.get(probe.return_entity), now)
        thermostat = self._thermostats.get(trv)
        may_write = (
            settings.operating_mode is OperatingMode.LIVE
            and settings.room_control.mode is not RoomControlMode.OFF
            and thermostat is not None
            and thermostat.available
            and not blocking(thermostat)
        )
        surface = room.details.surfaces[0] if room.details.surfaces else None
        return TestInputs(
            now=now,
            local=now.astimezone(self._zone),
            setpoint_c=room.details.setpoint_at(now.astimezone(self._zone).time()),
            room_c=room_celsius(sources.get(room.entity_id)),
            trv_c=thermostat.temperature_c if thermostat else None,
            trv_setpoint_c=thermostat.setpoint_c if thermostat else None,
            valve=sources.number(room.details.valve_entity),
            flow_c=flow,
            return_c=back,
            window_open=self._hems.window_open(room.entity_id),
            limits=self._gate.limits(trv),
            probe_here=probe.room_id == room.entity_id and flow is not None and back is not None,
            may_write=may_write,
            rated_w=surface.rated_w if surface is not None else None,
            rated_basis=surface.rated_basis.value if surface is not None else "75/65",
        )

    async def _restore_after_restart(self) -> None:
        if self._restore is None:
            return
        room_id, celsius = self._restore
        room = next((item for item in self._settings.current.rooms if item.entity_id == room_id), None)
        if room is None or room.details.trv_entity is None:
            # The room is gone: its thermostat gets the emergency value the write gate kept.
            if await self.release("Testlauf nach einem Neustart", {room_id}):
                self._restore = None
            return
        if self._gate.limits(room.details.trv_entity) is None:
            # Not known yet after the start; tried again next minute.
            return
        if await self._restore_thermostat(room, celsius, "Testlauf nach einem Neustart"):
            self._restore = None

    async def _restore_thermostat(self, room: Room, celsius: float, reason: str) -> bool:
        """The way back after a test run; when it does not go out, the fallback. True when one went out."""
        command = SetThermostatSetpoint(room_id=room.entity_id, celsius=celsius, reason=RoomReason.RESTORE)
        try:
            result = await self._gate.submit(command)
        except (WriteRejected, WriteNotExecuted, WriteOutcomeUnknown) as err:
            problem = str(err) or type(err).__name__
        else:
            if result.sent or result.held is Held.SAME:
                return True
            problem = result.message
        self._log.add(
            LogCategory.ROOMS,
            f"Rückweg nach dem Testlauf {room.name} nicht gesendet ({problem}); Rückfall auf den Notfallwert.",
            problem=True,
        )
        return await self.release(reason, {room.entity_id})

    # --- helpers ------------------------------------------------------------------------------

    def _room_status(self, room: Room, settings: Settings, now: datetime) -> RoomControlRoom:
        details = room.details
        trv = details.trv_entity
        state = self._rooms.get(room.entity_id) or _RoomState()
        thermostat = self._thermostats.get(trv) if trv else None
        decision = state.decision
        commands = self._gate.commands(trv) if trv else None
        preview = self._gate.commands(trv, preview=True) if trv else None
        movements = self._movements.get(trv or "")
        today = now.astimezone(self._zone).date()
        run = self._test if self._test is not None and self._test.room_id == room.entity_id else None
        curve = self._curves.get(trv or "", ValveCurve())
        zone = self._zones.get(room.zone_id or "")
        red = zone.redistribution if zone is not None else None
        blocked = self._control_blocked(room, settings)
        return RoomControlRoom(
            room_id=room.entity_id,
            name=room.name,
            zone_id=room.zone_id,
            stage=details.stage,
            effective=effective_stage(settings.room_control.mode, details.stage),
            trv_entity=trv,
            model=thermostat.model if thermostat else None,
            model_name=MODEL_NAMES[thermostat.model] if thermostat else None,
            at_thermostat=room.measured_at_thermostat,
            trv_setpoint_c=thermostat.setpoint_c if thermostat else None,
            trv_c=thermostat.temperature_c if thermostat else None,
            valve=self._hems.sources.number(details.valve_entity),
            target_c=decision.target_c if decision else None,
            setpoint_c=decision.setpoint_c if decision else None,
            mode=decision.mode if decision else None,
            mode_name=MODE_NAMES[decision.mode] if decision else None,
            reason_name=ROOM_REASON_NAMES[decision.reason] if decision and decision.reason else None,
            note=decision.note if decision else "",
            correction_k=state.correction.value_k,
            offset_k=details.offset_k,
            commands_today=commands.today if commands else 0,
            commands_limit=details.max_commands,
            test_commands_today=commands.test_today if commands else 0,
            preview_commands_today=preview.today if preview else 0,
            movements_today=movements.count if movements is not None and movements.day == today else 0,
            pending_c=commands.pending_c if commands else None,
            last_sent=commands.last_sent if commands else None,
            paused_until=commands.paused_until if commands else None,
            warnings=list(thermostat.warnings) if thermostat else [],
            side=dict(thermostat.side) if thermostat else {},
            test=_test_status(run) if run is not None else None,
            curve=_curve_status(curve),
            recommendation=state.recommendation,
            donor=red is not None and room.entity_id in red.donors,
            cold=red is not None and red.cold == room.entity_id,
            can_control=blocked is None,
            control_blocked=blocked,
        )

    def _control_blocked(self, room: Room, settings: Settings) -> str | None:
        """Why the room may not go to "Steuern"; None when it may."""
        trv = room.details.trv_entity
        if trv is None:
            return "Es ist kein Thermostat gewählt."
        if settings.operating_mode is not OperatingMode.LIVE:
            return "„Steuern“ geht nur im Live-Betrieb."
        if not self._curves.get(trv, ValveCurve()).learned:
            return "Erst ist ein vollständiger Testlauf nötig."
        thermostat = self._thermostats.get(trv)
        if thermostat is not None and blocking(thermostat):
            return blocking(thermostat)[0]
        return None

    async def _discover(self, now: datetime) -> None:
        client = self._home_assistant.client
        if client is None or not self._hems.sources.connected:
            return
        if self._discovered_at is not None and now - self._discovered_at < DISCOVER_EVERY:
            return
        self._discovered_at = now
        try:
            states = await client.states()
        except HomeAssistantUnavailable as err:
            _LOGGER.warning("Reading the thermostats' entities failed: %s", err)
            return
        by_id = {state.entity_id: state for state in states}
        side: dict[str, dict[str, str]] = {}
        for room in self._settings.current.rooms:
            trv = room.details.trv_entity
            if trv is not None:
                side[trv] = side_entities(trv, states)
        self._side = side
        self._side_states = {entity: by_id[entity] for names in side.values() for entity in names.values()}

    def _refresh_thermostats(self, now: datetime) -> None:
        sources = self._hems.sources
        thermostats = {}
        for room in self._settings.current.rooms:
            trv = room.details.trv_entity
            if trv is None:
                continue
            thermostats[trv] = read(
                trv,
                sources.get(trv),
                self._side.get(trv, {}),
                self._side_states,
                valve=sources.get(room.details.valve_entity),
                now=now,
            )
        self._thermostats = thermostats

    def _count_movements(self, now: datetime) -> None:
        today = now.astimezone(self._zone).date()
        for room in self._settings.current.rooms:
            trv = room.details.trv_entity
            value = self._hems.sources.number(room.details.valve_entity)
            if trv is None or value is None:
                continue
            moves = self._movements.setdefault(trv, _Movements())
            if moves.day != today:
                moves.day, moves.count = today, 0
            if moves.previous is not None and moves.previous > VALVE_OPEN and value <= VALVE_SHUT:
                moves.closed_at = now
            moves.previous = value
            if moves.last is None:
                moves.last = value
            elif abs(value - moves.last) >= MOVE_POINTS:
                moves.count += 1
                moves.last = value

    async def _trend(self, room_id: str, now: datetime) -> float | None:
        series = await self._hems.samples.series_async([room_series(room_id, "temperature")], now - TREND_OVER)
        return _slope(series[room_series(room_id, "temperature")])

    async def _recommend(self, now: datetime, settings: Settings) -> None:
        history = await self._hems.samples.series_async(["power", "buffer"], now - timedelta(days=RECOMMEND_DAYS))
        self._restart_c = restart_temperature(history["power"], history["buffer"])
        names = []
        for room in settings.rooms:
            if room.details.valve_entity:
                names += [room_series(room.entity_id, value) for value in ("temperature", "valve", "setpoint")]
        if not names:
            return
        series = await self._hems.samples.series_async(names, now - timedelta(days=RECOMMEND_DAYS))
        for room in settings.rooms:
            if not room.details.valve_entity:
                continue
            state = self._rooms.setdefault(room.entity_id, _RoomState())
            state.recommendation = _recommendation(
                series[room_series(room.entity_id, "temperature")],
                series[room_series(room.entity_id, "valve")],
                series[room_series(room.entity_id, "setpoint")],
                no_help=_no_help(state),
            )

    def _probe_status(self, settings: Settings, now: datetime) -> ProbeStatus:
        probe = settings.room_control.probe
        sources = self._hems.sources

        def age(entity: str | None) -> float | None:
            state = sources.get(entity)
            if state is None or state.last_reported is None:
                return None
            return round((now - state.last_reported).total_seconds() / 60, 1)

        reports = [
            report
            for entity in (probe.flow_entity, probe.return_entity)
            if entity
            for report in [list(self._probe_reports.get(entity, ()))]
        ]
        intervals = [
            (later[0] - earlier[0]).total_seconds() / 60
            for items in reports
            for earlier, later in itertools.pairwise(items)
        ]
        changes = [
            abs(later[1] - earlier[1])
            for items in reports
            for earlier, later in itertools.pairwise(items)
            if abs(later[1] - earlier[1]) > 1e-6
        ]
        return ProbeStatus(
            flow_entity=probe.flow_entity,
            return_entity=probe.return_entity,
            room_id=probe.room_id,
            room_name=self._name(probe.room_id) if probe.room_id else None,
            since=probe.since,
            flow_c=sources.number(probe.flow_entity),
            return_c=sources.number(probe.return_entity),
            flow_age_min=age(probe.flow_entity),
            return_age_min=age(probe.return_entity),
            report_minutes=round(statistics.median(intervals), 1) if intervals else None,
            resolution_k=round(min(changes), 3) if changes else None,
        )

    def _source_changed(self, entity_id: str, state: EntityState | None) -> None:
        probe = self._settings.current.room_control.probe
        if entity_id not in (probe.flow_entity, probe.return_entity) or state is None:
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        reports = self._probe_reports.setdefault(entity_id, deque(maxlen=PROBE_KEEP))
        reports.append((state.last_reported or self._clock(), value))

    def _flow_now(self, zone_id: str | None, settings: Settings) -> float | None:
        if zone_id is None:
            return None
        config = settings.hems.zone(zone_id)
        return self._hems.sources.number(config.flow_entity)

    def _labels(self) -> dict[str, str]:
        heat_pump = self._state.current
        return {zone.id: zone.label for zone in heat_pump.zones} if heat_pump is not None else {}

    def _room(self, room_id: str) -> Room:
        room = next((item for item in self._settings.current.rooms if item.entity_id == room_id), None)
        if room is None:
            raise WriteRejected(404, "Diesen Raum gibt es nicht (mehr). Räume legen Sie in den Einstellungen an.")
        return room

    def _name(self, room_id: str | None) -> str:
        room = next((item for item in self._settings.current.rooms if item.entity_id == room_id), None)
        return room.name if room is not None else str(room_id)

    def _healthy(self, now: datetime) -> bool:
        """The control's beat runs (or nothing depends on it); else the life sign stops."""
        if self._settings.current.room_control.mode is RoomControlMode.OFF and not self._gate.touched():
            return True
        reference = self._ticked_at or self._started_at
        return now - reference <= HEALTHY_FOR

    def _just_changed(self, trv: str, stage: RoomControlMode, now: datetime, settings: Settings) -> bool:
        """The app set the thermostat or its valve closed within the last half hour."""
        preview = stage is not RoomControlMode.CONTROL or settings.operating_mode is not OperatingMode.LIVE
        sent = self._gate.commands(trv, preview=preview).last_sent
        closed = self._movements.get(trv, _Movements()).closed_at
        return any(moment is not None and now - moment < WINDOW_QUIET for moment in (sent, closed))

    def _testing(self, room: Room) -> bool:
        run = self._test
        return run is not None and run.state is TestState.RUNNING and run.room_id == room.entity_id

    async def _release_on_stop(self) -> None:
        if self._settings.current.operating_mode is not OperatingMode.LIVE or not self._gate.touched():
            return
        with contextlib.suppress(Exception):
            async with asyncio.timeout(RELEASE_TIMEOUT):
                await self.release("Die App wird beendet")

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            _LOGGER.warning("Room control state %s unreadable (%s); starting fresh", self._path, err)
            return
        if not isinstance(raw, dict):
            return
        for room_id, item in (raw.get("rooms") or {}).items():
            if not isinstance(item, dict):
                continue
            correction = item.get("correction_k")
            self._rooms[room_id] = _RoomState(
                correction=Correction(value_k=float(correction) if isinstance(correction, int | float) else 0.0),
                helps_ok=int(item.get("helps_ok", 0)),
                helps_failed=int(item.get("helps_failed", 0)),
            )
        for trv, item in (raw.get("curves") or {}).items():
            self._curves[trv] = curve_from_json(item)
        run = run_from_json(raw.get("test"))
        if run is not None and run.state is TestState.RUNNING:
            # Interrupted by a restart: the thermostat goes back to its setpoint from before.
            if run.before_c is not None:
                self._restore = (run.room_id, run.before_c)
            run.state = TestState.ABORTED
            run.message = "App neu gestartet"
            run.ended_at = self._clock()
            self._log.add(LogCategory.ROOMS, "Testlauf abgebrochen: Die App wurde neu gestartet.")
        self._test = run

    def _save(self) -> None:
        payload = {
            "rooms": {
                room_id: {
                    "correction_k": state.correction.value_k,
                    "helps_ok": state.helps_ok,
                    "helps_failed": state.helps_failed,
                }
                for room_id, state in self._rooms.items()
            },
            "curves": {trv: curve_to_json(curve) for trv, curve in self._curves.items()},
            "test": run_to_json(self._test) if self._test is not None else None,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=STATE_FILE, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file)
            os.replace(tmp, self._path)
        except OSError as err:
            _LOGGER.warning("Saving the room control state failed: %s", err)


def _no_help(state: _RoomState) -> bool:
    """Redistributing did not help this room several times (and never did)."""
    return state.helps_failed >= HELPS_FAILURES and state.helps_ok == 0


def _rested(state: _RoomState, now: datetime) -> bool:
    return state.rest_until is None or now >= state.rest_until


def _fresh(state: EntityState | None, now: datetime) -> float | None:
    if state is None or state.last_reported is None or now - state.last_reported > PROBE_FRESH:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _slope(series: Sequence[tuple[datetime, float]]) -> float | None:
    """K/h by least squares; None with fewer than three points."""
    if len(series) < 3:
        return None
    start = series[0][0]
    xs = [(moment - start).total_seconds() / 3600 for moment, _value in series]
    ys = [value for _moment, value in series]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return round(sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx, 3)


def _recommendation(
    temperature: Sequence[tuple[datetime, float]],
    valve: Sequence[tuple[datetime, float]],
    setpoint: Sequence[tuple[datetime, float]],
    *,
    no_help: bool,
) -> str | None:
    """A hint about the valve's presetting from the last days (only a hint; the user sets it by hand)."""
    if no_help:
        return "Umverteilen hilft hier nicht: die Voreinstellung des Ventils größer prüfen."
    valves = dict(valve)
    setpoints = dict(setpoint)
    pairs = [
        (value, valves[moment], setpoints[moment])
        for moment, value in temperature
        if moment in valves and moment in setpoints
    ]
    if len(pairs) < RECOMMEND_MIN_SAMPLES:
        return None
    under = sum(1 for room, opening, target in pairs if opening >= 90 and room <= target - 0.3) / len(pairs)
    over = sum(1 for room, opening, target in pairs if opening <= 30 and room >= target) / len(pairs)
    if under >= 0.2:
        return "Oft unterversorgt bei offenem Ventil: die Voreinstellung größer prüfen (oder den Vorlauf höher)."
    if over >= 0.7:
        return "Meist überversorgt (Ventil fast zu): die Voreinstellung kann kleiner werden."
    return None


def _test_status(run: TestRun) -> TestStatus:
    return TestStatus(
        state=run.state,
        state_name=STATE_NAMES[run.state],
        message=run.message,
        armed_at=run.armed_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        phase=run.phase.value if run.phase is not None else None,
        setpoint_c=run.setpoint_c,
        commands=run.commands,
        steps=len(run.steps),
        nights=run.nights,
    )


def _curve_status(curve: ValveCurve) -> CurveStatus:
    last = curve.last
    complete = [result for result in curve.results if result.complete]
    reference = complete[-1] if complete else last
    return CurveStatus(
        learned=curve.learned,
        runs=len(curve.results),
        complete_runs=len(complete),
        last_at=last.ended_at if last else None,
        last_ending=last.ending if last else None,
        opening_k=reference.opening_k if reference else None,
        full_k=reference.full_k if reference else None,
        closing_k=reference.closing_k if reference else None,
        reaction_min=reference.reaction_min if reference else None,
        levels=curve.levels(),
        steps=list(reference.steps) if reference else [],
    )


def _celsius(value: float) -> str:
    return f"{value:.1f} °C".replace(".", ",")


def _kelvin(value: float) -> str:
    return f"{value:+.1f} K".replace(".", ",").replace("-", "\u2212")


def _one_line(text: str) -> str:
    """A room's name as a YAML comment: one line, nothing that ends it."""
    return " ".join(text.split())


async def _despite_cancellation(work: Coroutine[Any, Any, None]) -> None:
    """Run work to its end even if the waiting task is cancelled again meanwhile.

    Stopping the app cancels the services more than once (uvicorn passes SIGTERM on
    after its own shutdown); work must limit its own duration.
    """
    task = asyncio.create_task(work)
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
