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

Since 1.4.2 it also learns each thermostat's valve map every minute
(learning.py: everyday points, step responses, drifts; test runs add their
steps), asks the Aqara W600 for their valve position (reads.py, through the
write gate) and tells the page "Räume" when something changed (changes()).
A room may go to "Steuern" once its map knows the valve closed and fully open.
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
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from vaillant_eebus.ha_client import EntityState, HomeAssistantUnavailable
from vaillant_eebus.ha_export import PREFIX, Sensor
from vaillant_eebus.ha_import import room_celsius
from vaillant_eebus.hems.config import ZoneKind, in_window
from vaillant_eebus.hems.room_model import FlowHint, Supply
from vaillant_eebus.hems.samples import room_series
from vaillant_eebus.hems.service import SAMPLE_EVERY, HemsService
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.ids import MQTT_BASE_TOPIC
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.room_data import ProbePair, RoomControlMode, RoomStage, at_least, effective_stage
from vaillant_eebus.rooms.controller import MODE_NAMES, Correction, Decision, RoomInputs, RoomMode, decide
from vaillant_eebus.rooms.curve import Step, ValveCurve, curve_from_json, curve_to_json
from vaillant_eebus.rooms.learning import (
    KIND_NAMES,
    CalmWatch,
    Kind,
    Source,
    StepWatch,
    ValveMap,
    map_from_json,
    map_to_json,
    points_from_steps,
    supply_class,
)
from vaillant_eebus.rooms.learning import SUPPLY_NAMES as WATER_NAMES
from vaillant_eebus.rooms.reads import ReadPlan
from vaillant_eebus.rooms.supply import SUPPLY_NAMES, HeatSupply, SupplyNow, heat_supply, restart_temperature
from vaillant_eebus.rooms.testrun import (
    GOES_ON,
    PROBE_VALID,
    STATE_NAMES,
    Action,
    SwapWatch,
    TestInputs,
    TestRun,
    TestState,
    abort,
    advance,
    describe,
    night_of,
    probe_reading,
    run_from_json,
    run_to_json,
    swapped_text,
    waiting_for,
)
from vaillant_eebus.rooms.thermostats import (
    MODEL_NAMES,
    Thermostat,
    TrvModel,
    blocking,
    read,
    seen_at,
    side_entities,
)
from vaillant_eebus.settings import OperatingMode, Room, Settings, SettingsStore
from vaillant_eebus.state import StateHub
from vaillant_eebus.write_gate import (
    ROOM_REASON_NAMES,
    Held,
    ReadValvePosition,
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
PROBE_KEEP = 60
TREND_OVER = timedelta(minutes=30)
RECOMMEND_DAYS = 7
RECOMMEND_MIN_SAMPLES = 3 * 288
# A valve position counts as current this long: a W600's since its last read (or report), another thermostat's
# since its last report (the Comet reports with its messages, at least hourly).
READ_FRESH = timedelta(minutes=10)
REPORT_FRESH = timedelta(minutes=70)
# Seconds between the reads of several W600 in one minute.
READ_STAGGER = 10.0
# Reads kept to match an answer (last_seen) to, and how much earlier than the read the answer may seem.
READS_KEPT = 6
ANSWER_SLACK = timedelta(seconds=5)
# A command of the app and the setpoint change it caused are this close.
OWN_CHANGE = timedelta(minutes=2)
VALVE_TREND_OVER = timedelta(minutes=30)
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
    # The longest silence between two reports of either sensor since the app started, and the smallest change.
    longest_gap_min: float | None
    resolution_k: float | None
    # A reading counts this long after its report.
    valid_hours: float
    # The return was warmer than the flow with warm water at the open valve (SwapWatch).
    swapped: bool


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
    """The test runs of the thermostat: how many, and the steps of the last one."""

    runs: int
    complete_runs: int
    last_at: datetime | None
    last_ending: str | None
    steps: list[Step]


@dataclass(frozen=True, slots=True)
class MapCell:
    offset_k: float
    rising: bool
    valve: float
    count: int


@dataclass(frozen=True, slots=True)
class MapStatus:
    """What the app learned of the thermostat's valve (learning.py)."""

    # "zu" and "ganz offen" are known: the room may go to "Steuern".
    ready: bool
    missing: list[str]
    closed_k: float | None
    full_k: float | None
    full_valve: float | None
    opening_k: float | None
    kind: Kind
    kind_name: str
    pi_per_h_per_k: float | None
    reaction_min: float | None
    speed: float | None
    settle_min: float | None
    smallest_step_k: float | None
    points_test: int
    points_daily: int
    responses: int
    last_point_at: datetime | None
    cells: list[MapCell]
    # How warm the water was at the points (names).
    water: list[str]


@dataclass(frozen=True, slots=True)
class ReadInfo:
    """The reads of a W600's valve position (reads.py, write gate)."""

    topic: str | None
    # The device name found in Home Assistant (the room's own one may replace it).
    found_name: str | None
    last_read: datetime | None
    today: int
    limit: int
    problem: str | None


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
    valve_map: MapStatus
    # The W600's reads; None for thermostats that report their valve themselves.
    read: ReadInfo | None
    # When the thermostat last made itself heard, whether by last_seen, and when its valve position was confirmed.
    life_sign_at: datetime | None
    last_seen: bool
    valve_at: datetime | None
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
    mqtt_base_topic: str
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
        read_stagger: float = READ_STAGGER,
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
        self._read_stagger = read_stagger
        self._rooms: dict[str, _RoomState] = {}
        self._curves: dict[str, ValveCurve] = {}
        self._maps: dict[str, ValveMap] = {}
        self._calm: dict[str, CalmWatch] = {}
        self._step_watch: dict[str, StepWatch] = {}
        self._plans: dict[str, ReadPlan] = {}
        # The thermostats' device names in Home Assistant (= Zigbee2MQTT's), and the setpoints they reported.
        self._device_names: dict[str, str] = {}
        self._trv_setpoints: dict[str, float] = {}
        # The last reads that went out, per W600.
        self._reads_sent: dict[str, deque[datetime]] = {}
        self._dirty = False
        self._listeners: set[asyncio.Queue[None]] = set()
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
        self._swap = SwapWatch()
        # The reasons an armed run waited for in its start window, logged once per night: (night, reasons).
        self._waits: tuple[date, set[str]] | None = None
        self._problems: list[str] = []
        self._lock = asyncio.Lock()
        self._load()
        hems.on_sampled(self.tick)
        hems.set_flow_advisor(self.flow_advice)
        hems.sources.on_change(self._source_changed)
        hems.sources.follow(self._followed)
        if home_assistant.exporter is not None:
            home_assistant.exporter.add_source(self.sensors, always=True)

    # --- loops ---------------------------------------------------------------------------

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                started = loop.time()
                try:
                    await self.minute()
                except Exception:
                    # A fault here must not stop the rest of the app; the life sign then stops.
                    _LOGGER.exception("Room control failed")
                # The reads of a minute take a few seconds; the beat stays a minute.
                await asyncio.sleep(max(1.0, TICK - (loop.time() - started)))
        finally:
            self._save()
            await _despite_cancellation(self._release_on_stop())

    async def minute(self) -> None:
        """Every minute: life sign, thermostats' other entities, movements, the test run, learning and the
        W600's reads."""
        now = self._clock()
        async with self._lock:
            await self._discover(now)
            self._refresh_thermostats(now)
            self._count_movements(now)
            self._watch_probe(now)
            await self._restore_after_restart()
            await self._advance_test(now)
            self._learn(now)
            if self._dirty:
                self._dirty = False
                self._save()
        await self._read_positions(self._due_reads(now))
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
        self._notify()

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
            # A new pair (or the same one swapped) is judged afresh.
            self._swap = SwapWatch()
        self._home_assistant.watch()
        where = self._room(probe.room_id).name if probe.room_id else "nicht an einem Heizkörper"
        self._log.add(
            LogCategory.SETTINGS,
            f"Fühlerpaar: Vorlauf {probe.flow_entity or 'keiner'}, Rücklauf {probe.return_entity or 'keiner'}, "
            f"hängt {where}.",
        )

    async def set_base_topic(self, topic: str) -> None:
        """Zigbee2MQTT's base topic for the reads of the W600's valve position."""
        if not MQTT_BASE_TOPIC.match(topic):
            raise WriteRejected(
                422, "Das Basis-Topic besteht aus Buchstaben, Ziffern, „_“ und „-“, Teile getrennt durch „/“."
            )
        if topic == self._settings.current.room_control.mqtt_base_topic:
            return
        self._settings.update(
            lambda current: replace(current, room_control=replace(current.room_control, mqtt_base_topic=topic))
        )
        self._log.add(LogCategory.SETTINGS, f"Basis-Topic von Zigbee2MQTT: {topic}.")
        self._notify()

    @contextlib.asynccontextmanager
    async def changes(self) -> AsyncIterator[asyncio.Queue[None]]:
        """A queue that holds a mark while something on the page "Räume" changed (thermostat, valve, room
        sensor, probe, a decision or a command)."""
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._listeners.add(queue)
        try:
            yield queue
        finally:
            self._listeners.discard(queue)

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
            now = self._clock()
            run = TestRun(room_id=room_id, trv=trv, armed_at=now)
            run.message = "Wartet auf die Nacht"
            known = self._maps.get(trv, ValveMap())
            full = known.full(now, rising=True) or known.full(now)
            if full is not None and known.closed_k(now) is None:
                # "Fully open" is known already: the night goes downward only, to find "closed".
                run.down_only, run.full_offset_k = True, full[0]
                run.message = "Wartet auf die Nacht; „ganz offen“ ist bekannt, der Lauf geht nur abwärts"
            self._test = run
            self._save()
        start = settings.room_control.test_start.strftime("%H:%M")
        only = " (nur abwärts, „ganz offen“ ist schon bekannt)" if run.down_only else ""
        self._log.add(LogCategory.ROOMS, f"Testlauf {room.name} vorgemerkt{only}: startet ab {start} Uhr.")

    async def cancel_test(self, room_id: str) -> None:
        async with self._lock:
            run = self._test
            if run is None or not run.active or run.room_id != room_id:
                raise WriteRejected(409, "Für diesen Raum ist kein Testlauf vorgemerkt.")
            reason = "vom Nutzer abgebrochen"
            waited = waiting_for(run)
            if waited is not None:
                reason += f"; wartete zuletzt: {waited}"
            await self._end_test(abort(run, self._clock(), reason))

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
            mqtt_base_topic=settings.room_control.mqtt_base_topic,
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
        valve_map = self._maps.get(trv, ValveMap())
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
            valve_map=valve_map,
            supply=supply_class(self._radiator_flow(room, settings, now)),
            kind=valve_map.kind(),
            settle_min=valve_map.settle_min(),
            valve_trend_per_h=await self._valve_trend(room, now),
            changed_at=self._gate.commands(trv, preview=stage is not RoomControlMode.CONTROL).last_sent,
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
        counted = run.counted_night
        measured = len(run.steps) if run.state is TestState.RUNNING else 0
        action = advance(
            run,
            inputs,
            settings.room_control.test_start,
            settings.room_control.test_latest_start,
            settings.room_control.test_end,
        )
        if was is TestState.ARMED and run.state is TestState.RUNNING:
            only = " (nur abwärts)" if run.down_only else ""
            self._log.add(LogCategory.ROOMS, f"Testlauf {room.name} gestartet{only}.")
        if run.state is TestState.RUNNING or was is TestState.RUNNING:
            # The steps that settled go into the valve map at once.
            new = run.steps[measured:]
            if new:
                self._maps[run.trv] = self._maps.get(run.trv, ValveMap()).add_points(points_from_steps(new, now))
                self._dirty = True
                self._notify()
        self._log_waiting(run, room, inputs.local, counted, settings)
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
            self._notify()
        elif action.kind == "end":
            await self._end_test(action, run=run)
            return
        self._save()

    def _log_waiting(self, run: TestRun, room: Room, local: datetime, counted: date | None, settings: Settings) -> None:
        """Each new reason an armed run waits for, once a night; a line when its start window passed without a start."""
        control = settings.room_control
        night = night_of(local, control.test_start)
        waited = waiting_for(run)
        if run.state is TestState.ARMED and run.counted_night != counted and run.counted_night is not None:
            # The start window of that night has just passed.
            if self._waits is not None and self._waits[0] == run.counted_night and self._waits[1] and waited:
                self._log.add(LogCategory.ROOMS, f"Testlauf {room.name} heute Nacht nicht gestartet: zuletzt {waited}.")
            return
        # The reason is set only in the start window; outside it the message still holds the last night's.
        if waited is None or not in_window(local.time(), control.test_start, control.test_latest_start):
            return
        if self._waits is None or self._waits[0] != night:
            self._waits = (night, set())
        if waited not in self._waits[1]:
            self._waits[1].add(waited)
            self._log.add(LogCategory.ROOMS, f"Testlauf {room.name} wartet: {waited}.")

    def _watch_probe(self, now: datetime) -> None:
        """Every minute: do flow and return of the pair look swapped?"""
        probe = self._settings.current.room_control.probe
        room = next((item for item in self._settings.current.rooms if item.entity_id == probe.room_id), None)
        if room is None:
            self._swap = SwapWatch()
            return
        sources = self._hems.sources
        flow = self._probe_value(probe.flow_entity, "Vorlauf", now)[0]
        back = self._probe_value(probe.return_entity, "Rücklauf", now)[0]
        valve = sources.number(room.details.valve_entity)
        if not self._swap.update(now, flow, back, valve, room_celsius(sources.get(room.entity_id))):
            return
        if self._swap.swapped and flow is not None and back is not None:
            self._log.add(LogCategory.ROOMS, f"Fühlerpaar: {swapped_text(flow, back)}", problem=True)
        else:
            self._log.add(LogCategory.ROOMS, "Fühlerpaar: Der Vorlauf ist wieder wärmer als der Rücklauf.")

    def _probe_value(self, entity: str | None, which: str, now: datetime) -> tuple[float | None, str | None]:
        if entity is None:
            return None, f"Es ist kein {which}-Fühler gewählt."
        state = self._hems.sources.get(entity)
        return probe_reading(
            state.state if state is not None else None, state.last_reported if state is not None else None, now, which
        )

    async def _end_test(self, action: Action, *, run: TestRun | None = None, restore: bool = True) -> None:
        run = run or self._test
        if run is None:
            return
        room = next((item for item in self._settings.current.rooms if item.entity_id == run.room_id), None)
        name = room.name if room is not None else run.room_id
        if restore and action.celsius is not None and room is not None:
            await self._restore_thermostat(room, action.celsius, f"Testlauf {name} beendet")
        result, run.outcome = run.outcome, None
        if result is not None:
            self._curves[run.trv] = self._curves.get(run.trv, ValveCurve()).add(result)
            summary = describe(result.steps)
            state = "fertig" if result.complete else "unvollständig"
            goes_on = f" {GOES_ON}." if run.state is TestState.ARMED else ""
            self._log.add(LogCategory.ROOMS, f"Testlauf {name} {state} ({result.ending}): {summary}.{goes_on}")
            known = self._maps.get(run.trv, ValveMap())
            missing = known.missing(self._clock())
            if not missing:
                self._log.add(LogCategory.ROOMS, f"{name}: „zu“ und „ganz offen“ sind bekannt; „Steuern“ ist möglich.")
        elif run.state is TestState.FAILED:
            self._log.add(LogCategory.ROOMS, f"Testlauf {name}: {run.message}", problem=True)
        elif run.state is TestState.ABORTED:
            self._log.add(LogCategory.ROOMS, f"Testlauf {name} abgebrochen: {run.message}.")
        self._save()
        self._notify()

    def _test_inputs(self, room: Room, trv: str, now: datetime, settings: Settings) -> TestInputs:
        sources = self._hems.sources
        probe = settings.room_control.probe
        flow, flow_problem = self._probe_value(probe.flow_entity, "Vorlauf", now)
        back, back_problem = self._probe_value(probe.return_entity, "Rücklauf", now)
        if probe.room_id != room.entity_id:
            probe_problem: str | None = f"Das Fühlerpaar hängt nicht am Heizkörper von {room.name}."
        elif flow_problem is not None or back_problem is not None:
            probe_problem = flow_problem or back_problem
        elif self._swap.swapped and flow is not None and back is not None:
            probe_problem = swapped_text(flow, back)
        else:
            probe_problem = None
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
            probe_problem=probe_problem,
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
        valve_map = self._maps.get(trv or "", ValveMap())
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
            valve_map=_map_status(valve_map, now),
            read=self._read_info(room, thermostat),
            life_sign_at=thermostat.life_sign if thermostat else None,
            last_seen=thermostat.has_last_seen if thermostat else False,
            valve_at=self._valve_confirmed(room, thermostat) if thermostat else None,
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
        missing = self._maps.get(trv, ValveMap()).missing(self._clock())
        if missing:
            return f"Die App kennt noch nicht: {' und '.join(missing)} (aus einem Testlauf oder dem Alltag)."
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
        followed = self._followed()
        self._side = side
        self._side_states = {entity: by_id[entity] for names in side.values() for entity in names.values()}
        if self._followed() != followed:
            # Follow the new last_seen entities live.
            self._home_assistant.watch()
        for trv, names in side.items():
            if "position" not in names and "sensor" not in names:
                continue
            try:
                name = await client.device_name(trv)
            except (HomeAssistantUnavailable, ValueError) as err:
                _LOGGER.info("The device name of %s is unknown: %s", trv, err)
                continue
            if name is not None:
                self._device_names[trv] = name

    def _refresh_thermostats(self, now: datetime) -> None:
        sources = self._hems.sources
        thermostats = {}
        for room in self._settings.current.rooms:
            trv = room.details.trv_entity
            if trv is None:
                continue
            side = self._side.get(trv, {})
            seen = side.get("last_seen")
            thermostats[trv] = read(
                trv,
                sources.get(trv),
                side,
                self._side_states,
                valve=sources.get(room.details.valve_entity),
                now=now,
                last_seen=(sources.get(seen) or self._side_states.get(seen)) if seen else None,
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
        gaps = [
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
            longest_gap_min=round(max(gaps), 1) if gaps else None,
            resolution_k=round(min(changes), 3) if changes else None,
            valid_hours=PROBE_VALID.total_seconds() / 3600,
            swapped=self._swap.swapped,
        )

    def _source_changed(self, entity_id: str, state: EntityState | None) -> None:
        settings = self._settings.current
        rooms = [
            room
            for room in settings.rooms
            if entity_id
            in (room.entity_id, room.details.trv_entity, room.details.valve_entity, room.details.humidity_entity)
        ]
        probe = settings.room_control.probe
        if rooms or entity_id in self._followed() or entity_id in (probe.flow_entity, probe.return_entity):
            self._notify()
        for room in rooms:
            if entity_id != room.details.trv_entity or state is None:
                continue
            reported = state.attributes.get("temperature")
            if not isinstance(reported, int | float) or isinstance(reported, bool):
                continue
            before = self._trv_setpoints.get(entity_id)
            self._trv_setpoints[entity_id] = float(reported)
            if before is not None and abs(before - float(reported)) >= 0.01:
                # A new setpoint (the app's, by hand or a schedule): read the valve 1 and 3 minutes later.
                self._plans.setdefault(entity_id, ReadPlan()).after_change(self._clock())
        if entity_id not in (probe.flow_entity, probe.return_entity) or state is None:
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        reports = self._probe_reports.setdefault(entity_id, deque(maxlen=PROBE_KEEP))
        reported = state.last_reported or self._clock()
        if reports and reports[-1][0] == reported:
            # The same report again (read again after a reconnect), not a new one.
            return
        reports.append((reported, value))

    # --- learning and reads (1.4.2) ---------------------------------------------------------------

    def _learn(self, now: datetime) -> None:
        """Every minute: everyday points, drifts and step responses of each thermostat with a valve."""
        settings = self._settings.current
        if settings.room_control.mode is RoomControlMode.OFF:
            return
        for room in settings.rooms:
            trv = room.details.trv_entity
            thermostat = self._thermostats.get(trv or "")
            if trv is None or thermostat is None or room.details.valve_entity is None:
                continue
            valve = self._hems.sources.number(room.details.valve_entity)
            fresh = self._valve_fresh(room, thermostat, now)
            zone = self._zones.get(room.zone_id or "")
            warm = zone is not None and zone.supply.usable
            quiet = thermostat.available and not self._hems.window_open(room.entity_id) and not self._testing(room)
            point, drift = self._calm.setdefault(trv, CalmWatch()).update(
                now,
                setpoint=thermostat.setpoint_c,
                trv_c=thermostat.temperature_c,
                valve=valve,
                fresh=fresh,
                warm=warm,
                supply=supply_class(self._radiator_flow(room, settings, now)),
                quiet=quiet,
            )
            response = self._step_watch.setdefault(trv, StepWatch()).update(
                now, setpoint=thermostat.setpoint_c, valve=valve, fresh=fresh, own=self._own_change(trv, now)
            )
            if point is None and drift is None and response is None:
                continue
            valve_map = self._maps.get(trv, ValveMap())
            was_ready = valve_map.ready(now)
            if point is not None:
                valve_map = valve_map.add_points([point])
            if drift is not None:
                valve_map = valve_map.add_drift(drift)
            if response is not None:
                valve_map = valve_map.add_response(response)
            self._maps[trv] = valve_map
            self._dirty = True
            if not was_ready and valve_map.ready(now):
                self._log.add(
                    LogCategory.ROOMS,
                    f"{room.name}: Die App kennt jetzt „zu“ und „ganz offen“ (aus dem Alltag); „Steuern“ ist möglich.",
                )
            self._notify()

    def _due_reads(self, now: datetime) -> list[tuple[Room, str]]:
        """The W600 whose valve position is to be read now (reads.py)."""
        settings = self._settings.current
        if settings.room_control.mode is RoomControlMode.OFF or not self._hems.sources.connected:
            return []
        next_beat = self._ticked_at + timedelta(seconds=SAMPLE_EVERY) if self._ticked_at is not None else None
        due = []
        for room in settings.rooms:
            device = self._device_of(room)
            trv = room.details.trv_entity
            if trv is None or device is None:
                continue
            run = self._test
            test_step = (
                run.step_started
                if run is not None and run.state is TestState.RUNNING and run.room_id == room.entity_id
                else None
            )
            if self._plans.setdefault(trv, ReadPlan()).due(now, next_beat=next_beat, test_step_at=test_step):
                due.append((room, device))
        return due

    async def _read_positions(self, due: Sequence[tuple[Room, str]]) -> None:
        for index, (room, device) in enumerate(due):
            if index and self._read_stagger > 0:
                await asyncio.sleep(self._read_stagger)
            trv = room.details.trv_entity
            assert trv is not None
            self._plans.setdefault(trv, ReadPlan()).done(self._clock())
            try:
                result = await self._gate.read_position(ReadValvePosition(room_id=room.entity_id, device=device))
            except WriteRejected as err:
                _LOGGER.info("Reading the valve of %s refused: %s", room.name, err)
                continue
            if result.sent:
                sent = self._reads_sent.setdefault(trv, deque(maxlen=READS_KEPT))
                sent.append(self._clock())

    def _device_of(self, room: Room) -> str | None:
        """The W600's name in Zigbee2MQTT; None for a thermostat that is not read."""
        trv = room.details.trv_entity
        thermostat = self._thermostats.get(trv or "")
        if trv is None or thermostat is None or thermostat.model is not TrvModel.AQARA:
            return None
        return room.details.zigbee_name or self._device_names.get(trv)

    def _read_info(self, room: Room, thermostat: Thermostat | None) -> ReadInfo | None:
        trv = room.details.trv_entity
        if trv is None or thermostat is None or thermostat.model is not TrvModel.AQARA:
            return None
        device = self._device_of(room)
        status = self._gate.read_status(trv)
        base = self._settings.current.room_control.mqtt_base_topic
        return ReadInfo(
            topic=f"{base}/{device}/get" if device else None,
            found_name=self._device_names.get(trv),
            last_read=status.last_read,
            today=status.today,
            limit=status.limit,
            problem=status.problem,
        )

    def _valve_confirmed(self, room: Room, thermostat: Thermostat) -> datetime | None:
        """When the valve position the app has was last confirmed by the thermostat.

        A report of a change always is. For a W600 also a read: once last_seen shows its answer, or
        - without last_seen - as soon as Home Assistant took it (an unchanged answer is not recorded).
        For others their last message (last_seen).
        """
        valve = self._hems.sources.get(room.details.valve_entity)
        if valve is None or self._hems.sources.number(room.details.valve_entity) is None:
            return None
        moments = [valve.last_reported]
        seen_entity = thermostat.side.get("last_seen")
        seen = (
            seen_at(self._hems.sources.get(seen_entity) or self._side_states.get(seen_entity or ""))
            if (seen_entity)
            else None
        )
        if thermostat.model is TrvModel.AQARA:
            if seen_entity is None:
                # Without last_seen an answer that left the position unchanged does not show: a read Home
                # Assistant took counts.
                moments.append(self._gate.last_read(thermostat.entity_id))
            elif seen is not None:
                # The newest read that last_seen shows an answer to.
                answered = [
                    sent for sent in self._reads_sent.get(thermostat.entity_id, ()) if seen >= sent - ANSWER_SLACK
                ]
                moments.append(max(answered, default=None))
        else:
            moments.append(seen)
        known = [moment for moment in moments if moment is not None]
        return max(known) if known else None

    def _valve_fresh(self, room: Room, thermostat: Thermostat, now: datetime) -> bool:
        confirmed = self._valve_confirmed(room, thermostat)
        limit = READ_FRESH if thermostat.model is TrvModel.AQARA else REPORT_FRESH
        return confirmed is not None and now - confirmed <= limit

    def _own_change(self, trv: str, now: datetime) -> bool:
        sent = self._gate.commands(trv).last_sent
        return sent is not None and now - sent <= OWN_CHANGE

    def _radiator_flow(self, room: Room, settings: Settings, now: datetime) -> float | None:
        """How warm the water at the radiator is: the probe pair's flow when it hangs there, else the zone's flow,
        else the buffer."""
        probe = settings.room_control.probe
        if probe.room_id == room.entity_id:
            flow, problem = self._probe_value(probe.flow_entity, "Vorlauf", now)
            if problem is None and not self._swap.swapped:
                return flow
        zone_flow = self._flow_now(room.zone_id, settings)
        if zone_flow is not None:
            return zone_flow
        return self._hems.sources.number(settings.hems.buffer_entity)

    async def _valve_trend(self, room: Room, now: datetime) -> float | None:
        name = room_series(room.entity_id, "valve")
        series = await self._hems.samples.series_async([name], now - VALVE_TREND_OVER)
        return _slope(series[name])

    def _followed(self) -> set[str]:
        """The thermostats' last_seen entities, followed live."""
        return {names["last_seen"] for names in self._side.values() if "last_seen" in names}

    def _notify(self) -> None:
        for queue in self._listeners:
            if queue.empty():
                queue.put_nowait(None)

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
        for trv, item in (raw.get("maps") or {}).items():
            self._maps[trv] = map_from_json(item)
        for trv, curve in self._curves.items():
            if trv not in self._maps:
                # From 1.4.1: the steps of the test runs so far are the map's first points.
                self._maps[trv] = ValveMap().add_points(
                    point for result in curve.results for point in points_from_steps(result.steps, result.ended_at)
                )
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
            "maps": {trv: map_to_json(valve_map) for trv, valve_map in self._maps.items()},
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
    return CurveStatus(
        runs=len(curve.results),
        complete_runs=sum(1 for result in curve.results if result.complete),
        last_at=last.ended_at if last else None,
        last_ending=last.ending if last else None,
        steps=list(last.steps) if last else [],
    )


def _map_status(valve_map: ValveMap, now: datetime) -> MapStatus:
    full = valve_map.full(now)
    counts = valve_map.counts()
    kind = valve_map.kind()
    return MapStatus(
        ready=valve_map.ready(now),
        missing=valve_map.missing(now),
        closed_k=valve_map.closed_k(now),
        full_k=full[0] if full is not None else None,
        full_valve=full[1] if full is not None else None,
        opening_k=valve_map.opening_k(now),
        kind=kind,
        kind_name=KIND_NAMES[kind],
        pi_per_h_per_k=valve_map.pi_per_h_per_k() if kind is Kind.PI else None,
        reaction_min=valve_map.reaction_min(),
        speed=valve_map.speed(),
        settle_min=valve_map.settle_min(),
        smallest_step_k=valve_map.smallest_step_k(),
        points_test=counts[Source.TEST],
        points_daily=counts[Source.DAILY],
        responses=len(valve_map.responses),
        last_point_at=valve_map.points[-1].at if valve_map.points else None,
        cells=[MapCell(cell.offset_k, cell.rising, cell.valve, cell.count) for cell in valve_map.merged(now)],
        water=sorted({WATER_NAMES[point.supply] for point in valve_map.points}),
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
