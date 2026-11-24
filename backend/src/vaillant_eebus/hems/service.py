"""The energy management as one service for main and api (1.3.0: observe, learn, plan).

It watches the heat pump's power and outdoor temperature (starts, runs,
pauses, defrosts, frost protection), samples the rooms every five minutes
(room model, windows, supply), reads Home Assistant's long-term statistics
(house model), fetches the 48-hour forecast and plans every quarter hour by
rules. It never calls the write gate itself: the zone executor (executor.py,
1.5.0) carries out the plan and the floor heating's off/auto, and is woken
after every plan and every new state. The room control (package rooms, 1.4.0)
runs on its samples - it is called right after them - and tells it when a
radiator zone really needs a higher flow.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import logging
import os
import statistics
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from vaillant_eebus.ha_client import EntityState, HomeAssistantUnavailable
from vaillant_eebus.ha_export import PREFIX, Sensor
from vaillant_eebus.ha_import import room_celsius
from vaillant_eebus.hems.config import (
    AVAILABLE_MODES,
    FUNCTION_MODES,
    Function,
    HemsSettings,
    PlannerMode,
    WeatherSource,
    ZoneConfig,
    ZoneKind,
    hems_to_json,
)
from vaillant_eebus.hems.cycles import CycleStats, CycleTracker
from vaillant_eebus.hems.defrost import LEVEL_NAMES, BandRate, RiskLevel, learn_rates, risk
from vaillant_eebus.hems.flow import (
    BOTTLENECK_DAYS,
    Bottleneck,
    CurveAdvice,
    FlowInputs,
    RoomSamples,
    curve_advice,
    decide,
    flow_per_k,
    heat_from_pump,
    hour_valve,
    snapshot,
)
from vaillant_eebus.hems.flow import bottleneck as find_bottleneck
from vaillant_eebus.hems.forecast import OPEN_METEO_URL, ForecastCheck, ForecastService, WeatherHour, hour_start
from vaillant_eebus.hems.frost import FrostState, frost_hours, frost_state
from vaillant_eebus.hems.house_model import (
    DEFAULT_INSIDE_C,
    HouseModel,
    demand,
    find_lag,
    fit_cop,
    fit_house,
    start_model,
    zone_shares,
)
from vaillant_eebus.hems.night import (
    MODE_NAMES as NIGHT_MODE_NAMES,
)
from vaillant_eebus.hems.night import (
    START_K,
    STEP_K,
    ZONE1_K,
    BandMinimum,
    NightMode,
    ReduceInputs,
    RoomNow,
    at_minimum,
    hot_water,
    humid,
    minimum_by_band,
    minimum_phases,
    next_start,
    off_end,
    off_start,
    plan_window,
    reduce_step,
    window_at,
)
from vaillant_eebus.hems.room_model import (
    FlowHint,
    RoomModel,
    Supply,
    SupplyState,
    cooling_table,
    learn,
    minutes_until,
    supply,
)
from vaillant_eebus.hems.rules import (
    Plan,
    PlanHour,
    PlanInputs,
    RoomHour,
    RoomInput,
    ZoneHour,
    ZoneInput,
    make_plan,
)
from vaillant_eebus.hems.samples import SampleStore, room_series
from vaillant_eebus.hems.sources import HemsSources
from vaillant_eebus.hems.statistics import StatisticsHistory, daily_values, hourly_changes
from vaillant_eebus.hems.windows import Outside, Signal, WindowDetector, WindowEvent, humidity_threshold
from vaillant_eebus.home_assistant import POWER_SENSOR, HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.room_data import RoomControlMode, at_least, details_to_json, effective_stage
from vaillant_eebus.settings import Connection, ExportGroup, OperatingMode, Room, SettingsStore
from vaillant_eebus.state import HeatPumpState, StateHub

_LOGGER = logging.getLogger(__name__)

PLAN_EVERY = timedelta(minutes=15)
SAMPLE_EVERY = 300.0
# Bursts of new settings are planned once.
PLAN_SETTLE = 2.0
STATISTICS_EVERY = timedelta(hours=6)
ROOM_MODEL_EVERY = timedelta(hours=1)
DETECT_EVERY = timedelta(seconds=60)
# A window contact that reports nothing usable this long gives way to the rules (1.4.3).
CONTACT_LOST_AFTER = timedelta(minutes=10)
# After a start: the power and outdoor history read from Home Assistant for the counting.
BACKFILL = timedelta(days=10)
# Zone 1 logic ("würde"): off only after the power stayed below the threshold this long.
ZONE1_OFF_AFTER = timedelta(seconds=60)
# F2 (1.5.0): the bottleneck over 7 days is computed hourly; the decision looks at the last 4 hours of samples.
BOTTLENECK_EVERY = timedelta(hours=1)
# F3: after the night window F2 holds while the rooms heat up again, at most this long.
REHEAT_HOLD = timedelta(hours=3)
# F3: the minimum power steps down only with a power reported within this.
POWER_STALE = timedelta(minutes=10)
FLOW_RECENT = timedelta(hours=4)
FLOW_ROOM_VALUES = ("temperature", "valve", "setpoint", "window", "testing")
ZONE1_CHECK_AFTER = timedelta(minutes=2)
HISTORY_FILE = "plan_history.json"
STATE_FILE = "hems_state.json"
LEGACY_FILE = "hems.json"
LOOK_BACK = timedelta(hours=24)
NEXT_CHANGES = 5
EXPORT_CHANGES = 20

_MODE_LOG = {
    PlannerMode.OFF: "Energiemanagement ausgeschaltet.",
    PlannerMode.PREVIEW: "Energiemanagement auf Vorschau: Es plant und zeigt, was es tun würde, und sendet nichts.",
    PlannerMode.CONTROL: "Energiemanagement auf Steuern.",
}
# The minus sign of the UI texts.
MINUS = "\u2212"
MODE_NAMES = {PlannerMode.OFF: "Aus", PlannerMode.PREVIEW: "Vorschau", PlannerMode.CONTROL: "Steuern"}
SUPPLY_NAMES = {
    Supply.UNKNOWN: "unbekannt",
    Supply.SUPPLIED: "versorgt",
    Supply.UNDER: "unterversorgt",
    Supply.OVER: "überversorgt",
}


class ModeNotAvailable(Exception):
    """The stage comes with a later version."""


FUNCTION_NAMES = {
    Function.ZONE1: "Zone 1 Aus/Auto",
    Function.FLOW: "Vorlauf und Vorausschau",
    Function.NIGHT: "Nachtabsenkung",
}


@dataclass(frozen=True, slots=True)
class FunctionStatus:
    function: Function
    name: str
    mode: PlannerMode
    # The stage it works in: the smaller of its own and the energy management's.
    effective: PlannerMode
    available_modes: list[PlannerMode]


@dataclass(frozen=True, slots=True)
class ForecastStatus:
    source: WeatherSource | None
    fetched_at: datetime | None
    calibrated: bool
    check: ForecastCheck | None
    now: WeatherHour | None


@dataclass(frozen=True, slots=True)
class DayDemand:
    day: date
    heat_kwh: float
    electricity_kwh: float | None
    outdoor_c: float | None


@dataclass(frozen=True, slots=True)
class FlowStatus:
    zone_id: str
    offset_k: float
    hint: FlowHint
    reason: str | None
    bottleneck: str | None
    max_valve: float | None
    under: int
    # F2 (1.5.0): the bottleneck's mean valve and share undersupplied over 7 days (long_term False: the snapshot
    # of the last half hour), why the flow holds now, the heating curve advice.
    bottleneck_mean: float | None = None
    bottleneck_under_share: float | None = None
    bottleneck_long_term: bool = False
    holding: str | None = None
    curve_advice: str | None = None
    # "Vorlauf und Vorausschau" works on "Steuern" (else the shift is "würde").
    controlled: bool = False


@dataclass(frozen=True, slots=True)
class NightStatus:
    zone_id: str
    label: str
    # In the night window now (and not blocked).
    active: bool
    mode: NightMode | None
    mean_c: float | None
    shift_k: float
    off: bool
    off_since: datetime | None
    # What it does or waits for, German.
    text: str
    # The shift a radiator zone starts with next time, K.
    start_k: float
    # "Nachtabsenkung" works on "Steuern" (else "würde").
    controlled: bool


@dataclass(frozen=True, slots=True)
class Zone1Status:
    zone_id: str
    # What the app would do now: "off" or "auto"; None without power.
    would: str | None
    actual: str | None
    reason: str
    switches_today: int
    # Of the switches checked today, how often the zone did the same (the user's automation still runs).
    checked_today: int
    agreed_today: int


@dataclass(frozen=True, slots=True)
class Change:
    at: datetime
    text: str
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class HemsStatus:
    mode: PlannerMode
    available_modes: list[PlannerMode]
    app_mode: OperatingMode
    settings: dict[str, Any]
    problems: list[str]
    labels: dict[str, str]
    forecast: ForecastStatus
    house: HouseModel | None
    statistics_days: int
    demand: list[DayDemand]
    cycles: CycleStats
    defrost_rates: list[BandRate]
    frost: FrostState
    zone1: Zone1Status | None
    flow: list[FlowStatus]
    next_changes: list[Change]
    plan_created_at: datetime | None
    functions: list[FunctionStatus]
    # The zone executor's state (1.5.0); None without one.
    control: Any = None
    # F3 (1.5.0): the night setback per zone and the heat pump's minimum power per outdoor band.
    night: list[NightStatus] = field(default_factory=list)
    minimum: list[BandMinimum] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HourRecord:
    """One past hour: what the plan said when the hour began, and what happened."""

    start: datetime
    planned: PlanHour | None
    outdoor_c: float | None
    setpoints: dict[str, float]
    starts: int
    defrosts: int
    electricity_kwh: float | None


@dataclass(frozen=True, slots=True)
class PlanView:
    created_at: datetime | None
    hours: list[PlanHour]
    history: list[HourRecord]
    labels: dict[str, str]
    rooms: dict[str, str]


@dataclass(frozen=True, slots=True)
class RoomLearned:
    tau_h: float | None
    gains_k_per_h: float | None
    heating_rate: float | None
    startup_min: float | None
    demand_valve: float | None
    correction_k: float | None
    overshoot_k: float | None
    # Value name -> learned (True) or start value / unknown (False).
    learned: dict[str, bool]
    learning: bool


@dataclass(frozen=True, slots=True)
class RoomStatus:
    entity_id: str
    name: str
    zone_id: str | None
    zone_label: str | None
    measured_at_thermostat: bool
    details: dict[str, Any]
    temperature_c: float | None
    humidity: float | None
    valve: float | None
    trv_setpoint_c: float | None
    setpoint_c: float
    comfort_low_c: float
    comfort_high_c: float
    # Start value of the heat loss from the static balancing, W/K.
    heat_loss_start_w_per_k: float | None
    model: RoomLearned | None
    cooling_now_k_per_h: float | None
    cooling_table: dict[str, float]
    minutes_to_lower: float | None
    supply: Supply
    window_open: bool
    window_since: datetime | None
    # 1.4.3: what decides about the window ("contact" or "rules"), the humidity fall that shows it now (g/m³;
    # None: the humidity does not count) and the signal of the last opening or closing (German).
    window_source: str = "rules"
    window_threshold_g: float | None = None
    window_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ZoneRoomsStatus:
    zone_id: str
    label: str
    # The room that ends a pause first, and after how many minutes.
    limiting_room: str | None
    limiting_minutes: float | None
    flow: FlowStatus | None


@dataclass(frozen=True, slots=True)
class RoomsView:
    rooms: list[RoomStatus]
    zones: list[ZoneRoomsStatus]


@dataclass
class _Contact:
    """A room's window contact (binary_sensor, on = open); it decides while it reports."""

    open: bool | None = None
    since: datetime | None = None
    lost_since: datetime | None = None

    def usable(self, now: datetime) -> bool:
        return self.open is not None and (self.lost_since is None or now - self.lost_since < CONTACT_LOST_AFTER)


@dataclass
class _Zone1:
    below_since: datetime | None = None
    would: str | None = None
    day: date | None = None
    switches: int = 0
    checked: int = 0
    agreed: int = 0
    check_at: datetime | None = None


def _night_text(state: _NightZone) -> str:
    if not state.active:
        return "außerhalb des Nachtfensters"
    if state.blocked is not None:
        return f"keine Nachtabsenkung: {state.blocked}"
    if state.off:
        return f"aus seit {state.off_since:%H:%M} Uhr" if state.off_since else "aus"
    parts = [NIGHT_MODE_NAMES[state.mode] if state.mode else "Nacht"]
    if state.waiting:
        parts.append(state.waiting)
    if state.off_done:
        parts.append(f"kein Abschalten: {state.off_done}")
    if state.shift_k:
        parts.append(f"{_signed(state.shift_k)} K")
    if state.held:
        parts.append(state.held)
    elif state.note:
        parts.append(state.note)
    return " · ".join(parts)


def _room_samples(room: Room, series: Mapping[str, list[tuple[datetime, float]]]) -> RoomSamples:
    def named(value: str) -> list[tuple[datetime, float]]:
        return series.get(room_series(room.entity_id, value), [])

    return RoomSamples(
        room_id=room.entity_id,
        name=room.name,
        temperature=named("temperature"),
        valve=named("valve"),
        setpoint=named("setpoint"),
        window=named("window"),
        testing=named("testing"),
        at_thermostat=room.measured_at_thermostat,
    )


@dataclass
class _Flow:
    offset_k: float = 0.0
    changed_at: datetime | None = None
    reason: str | None = None
    # The last hint from the valves (not saved), and the room control's word on it.
    hint: FlowHint = FlowHint.UNKNOWN
    advice: str | None = None
    # F2 (1.5.0): when a shift below 0 jumped back, the bottleneck (computed hourly), why it holds now, the
    # heating curve advice of the day.
    jumped_at: datetime | None = None
    bottleneck: Bottleneck | None = None
    bottleneck_at: datetime | None = None
    holding: str | None = None
    curve: CurveAdvice | None = None
    curve_day: date | None = None
    curve_text: str | None = None


@dataclass
class _NightZone:
    """F3's live state of one zone (hems/night.py)."""

    # The window's local date and what it does (from the forecast at its start).
    day: date | None = None
    mode: NightMode | None = None
    mean_c: float | None = None
    active: bool = False
    shift_k: float = 0.0
    stepped_at: datetime | None = None
    # Holding for the rest of the window (minimum reached, band used up ...) and at which shift.
    held: str | None = None
    held_shift_k: float | None = None
    stopped_at_k: float | None = None
    note: str | None = None
    off: bool = False
    off_since: datetime | None = None
    # Switching off given up or ended early: minimum power for the rest of the window.
    off_done: str | None = None
    waiting: str | None = None
    blocked: str | None = None
    ended_at: datetime | None = None
    # The shift a radiator zone starts with (K > 0), learned from the last windows.
    start_k: float = START_K


@dataclass
class _Models:
    house: HouseModel | None = None
    rooms: dict[str, RoomModel] = field(default_factory=dict)
    flow_lines: dict[str, tuple[float, float]] = field(default_factory=dict)
    radiation_wh: dict[date, float] = field(default_factory=dict)


class HemsService:
    def __init__(
        self,
        settings: SettingsStore,
        state: StateHub,
        home_assistant: HomeAssistant,
        log: LogStore,
        data_dir: Path,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        open_meteo_url: str = OPEN_METEO_URL,
    ) -> None:
        self._settings = settings
        self._state = state
        self._home_assistant = home_assistant
        self._log = log
        self._data_dir = data_dir
        self._zone = zone
        self._clock = clock
        self.sources: HemsSources = home_assistant.hems_sources
        self.forecast = ForecastService(
            settings, home_assistant.client, data_dir, zone=zone, open_meteo_url=open_meteo_url, clock=clock
        )
        self.history = StatisticsHistory(home_assistant.client, clock=clock)
        self.samples = SampleStore(data_dir)
        self.cycles = CycleTracker(data_dir, zone=zone, clock=clock, humidity_at=self._humidity_at)
        self._models = _Models()
        self._plan: Plan | None = None
        self._problems: list[str] = []
        self._statistics_at: datetime | None = None
        self._rooms_at: datetime | None = None
        self._detected_at: datetime | None = None
        self._backfilled = False
        self._frost = FrostState(active=False, below_since=None)
        self._windows: dict[str, WindowDetector] = {}
        self._contacts: dict[str, _Contact] = {}
        self._window_listeners: list[Callable[[str], None]] = []
        self._window_guard: Callable[[str], bool] | None = None
        self._testing_guard: Callable[[str], bool] | None = None
        self._night_guard: Callable[[str], str | None] | None = None
        self._nights: dict[str, _NightZone] = {}
        self._minimum: list[BandMinimum] = []
        self._supply: dict[str, SupplyState] = {}
        self._zone1 = _Zone1()
        self._flows: dict[str, _Flow] = {}
        self._planned: dict[datetime, PlanHour] = {}
        self._logged_changes: list[tuple[datetime, str]] | None = None
        self._wake = asyncio.Event()
        self._sampled: list[Callable[[datetime], Awaitable[None]]] = []
        self._flow_advisor: Callable[[str, FlowHint], tuple[FlowHint, str | None] | None] | None = None
        self._updated: list[Callable[[], None]] = []
        self._control_status: Callable[[], Any] | None = None
        self._load_state()
        self._load_history()
        self._check_legacy()
        self.sources.on_change(self._source_changed)
        # Statistics, history and rooms come from Home Assistant: plan again once it is there.
        self.sources.on_connected(self.wake)
        if home_assistant.exporter is not None:
            home_assistant.exporter.add_source(self.sensors)

    @property
    def plan(self) -> Plan | None:
        return self._plan

    @property
    def mode(self) -> PlannerMode:
        return self._settings.current.hems.mode

    def wake(self) -> None:
        """Plan again soon, e.g. after new settings."""
        self._wake.set()

    def on_sampled(self, listener: Callable[[datetime], Awaitable[None]]) -> None:
        """Called every five minutes right after the samples and the rooms' supply (the room control)."""
        self._sampled.append(listener)

    def on_update(self, listener: Callable[[], None]) -> None:
        """Called after every plan and every new state of the heat pump (the zone executor)."""
        self._updated.append(listener)

    def set_control_status(self, status: Callable[[], Any]) -> None:
        self._control_status = status

    @property
    def frost_active(self) -> bool:
        return self._frost.active

    def floor_zone(self) -> str | None:
        """The zone with floor heating: the one F1 switches off while the heat pump stands still."""
        return self._floor_zone()

    def zone1_would(self) -> str | None:
        """What F1 would do now: "off", "auto", or None without power or zone."""
        return self._zone1.would if self._floor_zone() is not None else None

    def set_flow_advisor(self, advisor: Callable[[str, FlowHint], tuple[FlowHint, str | None] | None]) -> None:
        """The room control's word on a radiator zone's flow hint: first redistribute, then the flow."""
        self._flow_advisor = advisor

    def on_window(self, listener: Callable[[str], None]) -> None:
        """Called with the room's ID as soon as its window opens or closes (the room control reacts at once)."""
        self._window_listeners.append(listener)

    def set_window_guard(self, guard: Callable[[str], bool]) -> None:
        """The room control's word whether the thermostat's own sensor is to be ignored for a room now (shortly
        after a command or the valve closing it falls by itself)."""
        self._window_guard = guard

    def set_night_guard(self, guard: Callable[[str], str | None]) -> None:
        """The room control's word why a zone gets no night setback now (a test run armed or running in it)."""
        self._night_guard = guard

    def set_testing_guard(self, guard: Callable[[str], bool]) -> None:
        """The room control's word whether a test run or sweep has a room's thermostat now (F2 leaves it out)."""
        self._testing_guard = guard

    def window_open(self, room_id: str) -> bool:
        contact = self._contact_of(room_id)
        if contact is not None:
            return bool(contact.open)
        window = self._windows.get(room_id)
        return window is not None and window.open

    def window_reason(self, room_id: str) -> str | None:
        """The signal of the room's last window opening or closing, German."""
        if self._contact_of(room_id) is not None:
            return "Fensterkontakt"
        window = self._windows.get(room_id)
        return window.last_event.describe() if window is not None and window.last_event is not None else None

    def supply_of(self, room_id: str) -> SupplyState:
        return self._supply.get(room_id, SupplyState(Supply.UNKNOWN, None))

    def room_model(self, room_id: str) -> RoomModel | None:
        return self._models.rooms.get(room_id)

    def flow_of(self, zone_id: str) -> tuple[FlowHint, str | None] | None:
        """The zone's flow hint now and its reason."""
        flow = self._flows.get(zone_id)
        return (flow.hint, flow.advice or flow.reason) if flow is not None else None

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._planning())
                tasks.create_task(self._observing())
                tasks.create_task(self._sampling())
        finally:
            self.cycles.save()
            self._save_state()
            self.samples.close()

    async def set_mode(self, mode: PlannerMode) -> PlannerMode:
        if mode not in AVAILABLE_MODES:
            raise ModeNotAvailable(mode)
        before = self.mode
        if mode is before:
            return mode
        self._settings.update(lambda settings: replace(settings, hems=replace(settings.hems, mode=mode)))
        self._log.add(LogCategory.HEMS, _MODE_LOG[mode])
        self.wake()
        self._notify()
        return mode

    async def set_function_mode(self, function: Function, mode: PlannerMode) -> PlannerMode:
        if mode not in FUNCTION_MODES[function]:
            raise ModeNotAvailable(mode)
        if mode is self._settings.current.hems.function_mode(function):
            return mode
        self._settings.update(lambda settings: replace(settings, hems=settings.hems.with_function(function, mode)))
        self._log.add(LogCategory.HEMS, f"{FUNCTION_NAMES[function]}: Stufe {MODE_NAMES[mode]}.")
        self._notify()
        return mode

    def change_settings(self, hems: HemsSettings) -> HemsSettings:
        """Save the user's settings (the stage stays) and plan again at once."""
        before = self._settings.current.hems
        hems = replace(
            hems,
            mode=before.mode,
            zone1_mode=before.zone1_mode,
            flow_mode=before.flow_mode,
            night_mode=before.night_mode,
        )
        if hems == before:
            return hems
        self._settings.update(lambda settings: replace(settings, hems=hems))
        self._home_assistant.watch()
        self._log.add(LogCategory.SETTINGS, "Einstellungen des Energiemanagements geändert.")
        # New sources: read the statistics and learn again.
        self._statistics_at = self._rooms_at = None
        self.wake()
        return hems

    async def plan_now(self) -> None:
        try:
            await self._make_plan()
        except Exception:
            _LOGGER.exception("Planning failed")
        self._notify()

    def _notify(self) -> None:
        for listener in self._updated:
            try:
                listener()
            except Exception:
                _LOGGER.exception("A listener of the energy management failed")

    # --- status ------------------------------------------------------------------

    def status(self) -> HemsStatus:
        settings = self._settings.current
        now = self._clock()
        forecast = self.forecast.current
        measured = self._measured_outdoor()
        house = self._models.house or start_model(settings.hems.heat_load_w, settings.hems.design_outdoor_c)
        return HemsStatus(
            mode=settings.hems.mode,
            available_modes=sorted(AVAILABLE_MODES, key=list(PlannerMode).index),
            app_mode=settings.operating_mode,
            settings=hems_to_json(settings.hems),
            problems=list(self._problems),
            labels=self._labels(),
            forecast=ForecastStatus(
                source=forecast.source if forecast else None,
                fetched_at=forecast.fetched_at if forecast else None,
                calibrated=bool(forecast and forecast.bias),
                check=self.forecast.check(measured),
                now=forecast.at(now) if forecast else None,
            ),
            house=house,
            statistics_days=self._statistics_days(),
            demand=self._day_demand(),
            cycles=self.cycles.stats(),
            defrost_rates=learn_rates(self.cycles.runs, self.cycles.defrosts, now),
            frost=self._frost,
            zone1=self._zone1_status(),
            flow=self._flow_status(),
            next_changes=self.changes(NEXT_CHANGES),
            plan_created_at=self._plan.created_at if self._plan else None,
            functions=[
                FunctionStatus(
                    function=function,
                    name=FUNCTION_NAMES[function],
                    mode=settings.hems.function_mode(function),
                    effective=settings.hems.effective(function),
                    available_modes=sorted(FUNCTION_MODES[function], key=list(PlannerMode).index),
                )
                for function in Function
            ],
            control=self._control_status() if self._control_status is not None else None,
            night=self._night_status(),
            minimum=list(self._minimum),
        )

    def plan_view(self) -> PlanView:
        now = self._clock()
        plan = self._plan
        return PlanView(
            created_at=plan.created_at if plan else None,
            hours=list(plan.hours) if plan else [],
            history=self._look_back(now),
            labels=self._labels(),
            rooms={room.entity_id: room.name for room in self._settings.current.rooms},
        )

    def changes(self, limit: int) -> list[Change]:
        """The plan's coming changes, as text."""
        plan = self._plan
        if plan is None:
            return []
        labels = self._labels()
        names = {room.entity_id: room.name for room in self._settings.current.rooms}
        result: list[Change] = []
        previous: PlanHour | None = None
        for hour in plan.hours:
            parts = []
            for entry in hour.zones:
                before = (
                    next((item for item in previous.zones if item.zone_id == entry.zone_id), None) if previous else None
                )
                if previous is None or before is None or before.shift_k != entry.shift_k:
                    if previous is None and entry.shift_k == 0:
                        continue
                    target = f" ({_number(entry.target_c)}\u00a0°C)" if entry.target_c is not None else ""
                    parts.append(f"{labels.get(entry.zone_id, entry.zone_id)} {_signed(entry.shift_k)}\u00a0K{target}")
            for room_hour in hour.rooms:
                before_room = (
                    next((item for item in previous.rooms if item.entity_id == room_hour.entity_id), None)
                    if previous
                    else None
                )
                if before_room is not None and before_room.setpoint_c != room_hour.setpoint_c:
                    name = names.get(room_hour.entity_id, room_hour.entity_id)
                    parts.append(f"{name} {_number(room_hour.setpoint_c)}\u00a0°C")
            if parts:
                result.append(Change(at=hour.start, text=", ".join(parts), reasons=list(hour.reasons)))
            previous = hour
            if len(result) >= limit:
                break
        return result

    def rooms_view(self) -> RoomsView:
        now = self._clock()
        settings = self._settings.current
        labels = self._labels()
        forecast = self.forecast.current
        outdoor_now = self._outdoor_now()

        def outdoor_at(moment: datetime) -> float | None:
            hour = forecast.at(moment) if forecast is not None else None
            if hour is not None and hour.temperature_c is not None:
                return hour.temperature_c
            return outdoor_now

        rooms = []
        for room in settings.rooms:
            details = room.details
            temperature = room_celsius(self.sources.get(room.entity_id))
            setpoint = details.setpoint_at(now.astimezone(self._zone).time())
            model = self._models.rooms.get(room.entity_id)
            lower = setpoint - details.comfort_down_k
            window = self._windows.get(room.entity_id)
            contact = self._contact_of(room.entity_id)
            grams = window.last_grams if window is not None else None
            rooms.append(
                RoomStatus(
                    entity_id=room.entity_id,
                    name=room.name,
                    zone_id=room.zone_id,
                    zone_label=labels.get(room.zone_id) if room.zone_id else None,
                    measured_at_thermostat=room.measured_at_thermostat,
                    details=details_to_json(details),
                    temperature_c=temperature,
                    humidity=self.sources.number(details.humidity_entity),
                    valve=self.sources.number(details.valve_entity),
                    trv_setpoint_c=self.sources.attribute(details.trv_entity, "temperature"),
                    setpoint_c=setpoint,
                    comfort_low_c=round(lower, 1),
                    comfort_high_c=round(setpoint + details.comfort_up_k, 1),
                    heat_loss_start_w_per_k=_round(details.heat_loss_w_per_k(settings.hems.design_outdoor_c), 1),
                    model=_learned(model) if model is not None else None,
                    cooling_now_k_per_h=(
                        round(model.cooling_k_per_h(temperature, outdoor_now), 3)
                        if model is not None and temperature is not None and outdoor_now is not None
                        else None
                    ),
                    cooling_table=(
                        {f"{key:g}": value for key, value in cooling_table(model, setpoint).items()}
                        if model is not None
                        else {}
                    ),
                    minutes_to_lower=(
                        _round(minutes_until(model, temperature, lower, outdoor_at, now), 0)
                        if model is not None and temperature is not None
                        else None
                    ),
                    supply=self._supply.get(room.entity_id, SupplyState(Supply.UNKNOWN, None)).supply,
                    window_open=self.window_open(room.entity_id),
                    window_since=self._window_since(room.entity_id),
                    window_source="contact" if contact is not None else "rules",
                    window_threshold_g=(
                        humidity_threshold(grams, self._outside(now))
                        if grams is not None and contact is None and not room.measured_at_thermostat
                        else None
                    ),
                    window_reason=self.window_reason(room.entity_id),
                )
            )
        zones = []
        flows = {item.zone_id: item for item in self._flow_status()}
        for zone_id, label in labels.items():
            members = [item for item in rooms if item.zone_id == zone_id and item.minutes_to_lower is not None]
            limiting = min(members, key=lambda item: item.minutes_to_lower or 0.0, default=None)
            zones.append(
                ZoneRoomsStatus(
                    zone_id=zone_id,
                    label=label,
                    limiting_room=limiting.name if limiting else None,
                    limiting_minutes=limiting.minutes_to_lower if limiting else None,
                    flow=flows.get(zone_id),
                )
            )
        return RoomsView(rooms=rooms, zones=zones)

    def sensors(self) -> list[Sensor]:
        """The energy management's sensors for Home Assistant (export group "Energiemanagement")."""
        mode = self.mode
        stats = self.cycles.stats()
        changes = self.changes(EXPORT_CHANGES) if mode is not PlannerMode.OFF else []
        upcoming = changes[0] if changes else None
        return [
            Sensor(
                entity_id=f"sensor.{PREFIX}hems_status",
                state=MODE_NAMES[mode],
                attributes={
                    "friendly_name": "Energiemanagement Status",
                    "icon": "mdi:home-lightning-bolt-outline",
                    "fehlende_daten": list(self._problems),
                },
            ),
            Sensor(
                entity_id=f"sensor.{PREFIX}hems_next_change",
                state=upcoming.text[:250] if upcoming else "keine",
                attributes={
                    "friendly_name": "Energiemanagement nächste Änderung",
                    "icon": "mdi:clock-outline",
                    "zeit": upcoming.at.isoformat() if upcoming else None,
                    "grund": "; ".join(upcoming.reasons) if upcoming else None,
                },
            ),
            Sensor(
                entity_id=f"sensor.{PREFIX}hems_plan",
                state=str(len(changes)),
                attributes={
                    "friendly_name": "Energiemanagement geplante Änderungen (48 h)",
                    "icon": "mdi:calendar-clock",
                    "änderungen": [
                        {"zeit": change.at.isoformat(), "änderung": change.text, "grund": "; ".join(change.reasons)}
                        for change in changes
                    ],
                },
            ),
            Sensor(
                entity_id=f"sensor.{PREFIX}starts_today",
                state=str(stats.today.starts),
                attributes={
                    "friendly_name": "Wärmepumpe Starts heute",
                    "icon": "mdi:restart",
                    "state_class": "measurement",
                    "laufzeit_median_min": stats.today.run_minutes,
                    "pause_median_min": stats.today.pause_minutes,
                },
            ),
            Sensor(
                entity_id=f"sensor.{PREFIX}defrosts_today",
                state=str(stats.today.defrosts),
                attributes={
                    "friendly_name": "Wärmepumpe Abtauungen heute",
                    "icon": "mdi:snowflake-melt",
                    "state_class": "measurement",
                },
            ),
            Sensor(
                entity_id=f"binary_sensor.{PREFIX}frost_protection",
                state="on" if self._frost.active else "off",
                attributes={
                    "friendly_name": "Frostschutz der Wärmepumpe aktiv",
                    "icon": "mdi:snowflake-alert",
                    "unter_4_grad_seit": self._frost.below_since.isoformat() if self._frost.below_since else None,
                },
            ),
        ]

    # --- loops -------------------------------------------------------------------

    async def _planning(self) -> None:
        while True:
            await self.plan_now()
            now = self._clock()
            wait = (_quarter(now) + PLAN_EVERY - now).total_seconds() + 1
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(wait):
                    await self._wake.wait()
                await asyncio.sleep(PLAN_SETTLE)
            self._wake.clear()

    async def _observing(self) -> None:
        async with self._state.listen() as updates:
            while True:
                heat_pump = await updates.get()
                try:
                    self.observe(heat_pump)
                except Exception:
                    # A fault here must not stop the rest of the app.
                    _LOGGER.exception("Observing the heat pump failed")
                self._notify()

    async def _sampling(self) -> None:
        while True:
            try:
                await self.sample()
            except Exception:
                _LOGGER.exception("Sampling failed")
            await asyncio.sleep(SAMPLE_EVERY)

    # --- observing ---------------------------------------------------------------

    def observe(self, heat_pump: HeatPumpState | None) -> None:
        """A new state of the heat pump: power and outdoor temperature for the counting."""
        now = self._clock()
        reachable = heat_pump is not None and heat_pump.available and heat_pump.connected
        power = heat_pump.measurements.get("power_consumption") if reachable and heat_pump else None
        outdoor = heat_pump.measurements.get("outdoor_temperature") if reachable and heat_pump else None
        self.cycles.feed_power(now, power.value if power is not None else None)
        if outdoor is not None:
            self.cycles.feed_outdoor(now, outdoor.value)
        frost = frost_state(self.cycles.outdoor, now)
        if frost.active != self._frost.active and self.mode is not PlannerMode.OFF:
            self._log.add(
                LogCategory.HEMS,
                "Frostschutz der Wärmepumpe aktiv: länger als 4 h unter 4 °C. Die Anlage heizt jetzt auch in "
                "„Aus“ und in der Absenkung."
                if frost.active
                else "Frostschutz der Wärmepumpe beendet: wieder über 4 °C.",
            )
        self._frost = frost
        self._zone1_logic(heat_pump if reachable else None, power.value if power is not None else None, now)
        if self._detected_at is None or now - self._detected_at >= DETECT_EVERY:
            self._detected_at = now
            found = self.cycles.update()
            for item in found:
                if self.mode is not PlannerMode.OFF:
                    local = item.at.astimezone(self._zone)
                    outside = f", außen {_number(item.outdoor_c)} °C" if item.outdoor_c is not None else ""
                    self._log.add(
                        LogCategory.HEMS,
                        f"Abtauung erkannt: {local:%H:%M} Uhr, {_number(item.minutes)} min{outside}.",
                    )
            if found and any(state.waiting for state in self._nights.values()):
                # Right after a defrost the zones may go off (F3).
                self._night_after_defrost(now)

    def _zone1_logic(self, heat_pump: HeatPumpState | None, watts: float | None, now: datetime) -> None:
        """The user's automation as "würde": floor heating off while the heat pump stands still."""
        hems = self._settings.current.hems
        state = self._zone1
        today = now.astimezone(self._zone).date()
        if state.day != today:
            state.day, state.switches, state.checked, state.agreed = today, 0, 0, 0
        zone_id = self._floor_zone()
        if zone_id is None or heat_pump is None or watts is None:
            state.below_since = None
            return
        actual = next((zone.mode for zone in heat_pump.zones if zone.id == zone_id), None)
        if state.check_at is not None and now >= state.check_at:
            state.check_at = None
            state.checked += 1
            if (actual == "off") == (state.would == "off"):
                state.agreed += 1
        if watts < hems.zone1_threshold_w:
            state.below_since = state.below_since or now
        else:
            state.below_since = None
        would = "auto"
        if state.below_since is not None and now - state.below_since >= ZONE1_OFF_AFTER:
            would = "off" if not self._frost.active and not self._floor_room_cold(zone_id) else "auto"
        if would != state.would:
            if state.would is not None:
                state.switches += 1
                state.check_at = now + ZONE1_CHECK_AFTER
            state.would = would

    def _floor_room_cold(self, zone_id: str) -> bool:
        now = self._clock().astimezone(self._zone).time()
        for room in self._settings.current.rooms:
            if room.zone_id != zone_id:
                continue
            temperature = room_celsius(self.sources.get(room.entity_id))
            lower = room.details.setpoint_at(now) - room.details.comfort_down_k
            if temperature is not None and temperature < lower:
                return True
        return False

    # --- sampling ----------------------------------------------------------------

    async def sample(self) -> None:
        """Every five minutes: store the samples, check windows, supply and the flow ("würde")."""
        now = self._clock()
        settings = self._settings.current
        heat_pump = self._state.current
        values: dict[str, float] = {}
        if heat_pump is not None and heat_pump.available and heat_pump.connected:
            for key, series in (
                ("outdoor_temperature", "outdoor"),
                ("power_consumption", "power"),
                # The cylinder: a charge says nothing about the minimum power (F3).
                ("dhw_temperature", "dhw"),
            ):
                reading = heat_pump.measurements.get(key)
                if reading is not None:
                    values[series] = reading.value
            for zone in heat_pump.zones:
                if zone.setpoint is not None:
                    values[f"zone_setpoint:{zone.id}"] = zone.setpoint.value
                if zone.mode is not None:
                    # A zone switched off gives the room model clean cooling phases (K5).
                    values[f"zone_off:{zone.id}"] = 1.0 if zone.mode == "off" else 0.0
        for zone_config in settings.hems.zones:
            for entity, series in (
                (zone_config.flow_entity, f"flow:{zone_config.zone_id}"),
                (zone_config.flow_setpoint_entity, f"flow_setpoint:{zone_config.zone_id}"),
            ):
                value = self.sources.number(entity)
                if value is not None:
                    values[series] = value
        buffer = self.sources.number(settings.hems.buffer_entity)
        if buffer is not None:
            values["buffer"] = buffer
        local = now.astimezone(self._zone).time()
        for room in settings.rooms:
            details = room.details
            temperature = room_celsius(self.sources.get(room.entity_id))
            if temperature is not None:
                values[room_series(room.entity_id, "temperature")] = temperature
            values[room_series(room.entity_id, "setpoint")] = details.setpoint_at(local)
            for name, value in (
                ("humidity", self.sources.number(details.humidity_entity)),
                ("valve", self.sources.number(details.valve_entity)),
                ("trv_setpoint", self.sources.attribute(details.trv_entity, "temperature")),
                ("trv_temperature", self.sources.attribute(details.trv_entity, "current_temperature")),
            ):
                if value is not None:
                    values[room_series(room.entity_id, name)] = value
            window = self._windows.get(room.entity_id)
            if window is not None:
                self._window_event(room, window.tick(now), notify=self._contact_of(room.entity_id) is None)
            # Phases that say nothing about the flow (F2's bottleneck leaves them out).
            values[room_series(room.entity_id, "window")] = 1.0 if self.window_open(room.entity_id) else 0.0
            testing = self._testing_guard is not None and self._testing_guard(room.entity_id)
            values[room_series(room.entity_id, "testing")] = 1.0 if testing else 0.0
        for zone_config in settings.hems.zones:
            if zone_config.kind is ZoneKind.RADIATOR:
                flow = self._flows.get(zone_config.zone_id)
                values[f"flow_offset:{zone_config.zone_id}"] = flow.offset_k if flow is not None else 0.0
            curve = self.sources.number(zone_config.heating_curve_entity)
            if curve is not None:
                values[f"curve:{zone_config.zone_id}"] = curve
        if values:
            await self.samples.add_async(now, values)
        await self._update_supply(now)
        for listener in self._sampled:
            try:
                await listener(now)
            except Exception:
                _LOGGER.exception("The room control failed after the samples")
        try:
            await self._update_flow(now)
        except Exception:
            _LOGGER.exception("The flow of the radiator zone failed")
        try:
            await self._update_night(now)
        except Exception:
            _LOGGER.exception("The night setback failed")

    async def _update_supply(self, now: datetime) -> dict[str, list[tuple[datetime, float]]]:
        settings = self._settings.current
        rooms = [room for room in settings.rooms if room.details.valve_entity]
        if not rooms:
            self._supply = {}
            return {}
        names = [room_series(room.entity_id, value) for room in rooms for value in ("temperature", "valve")]
        series = await self.samples.series_async(names, now - timedelta(hours=1))
        local = now.astimezone(self._zone).time()
        supplies = {}
        for room in rooms:
            state = supply(
                series[room_series(room.entity_id, "temperature")],
                series[room_series(room.entity_id, "valve")],
                room.details.setpoint_at(local),
                now,
            )
            before = self._supply.get(room.entity_id)
            if (
                self.mode is not PlannerMode.OFF
                and self._observed(room)
                and state.supply in (Supply.UNDER, Supply.OVER)
                and (before is None or before.supply != state.supply)
            ):
                self._log.add(
                    LogCategory.ROOMS,
                    f"{room.name} wäre {SUPPLY_NAMES[state.supply]} (Ventil im Mittel {_number(state.valve or 0)} %).",
                )
            supplies[room.entity_id] = state
        self._supply = supplies
        return series

    async def _update_flow(self, now: datetime) -> None:
        """F2: zone 2's flow after the valves - bottleneck, up fast, down slowly (hems/flow.py); the executor sends
        the shift when "Vorlauf und Vorausschau" is on "Steuern"."""
        settings = self._settings.current
        hems = settings.hems
        floor = next((zone for zone in hems.zones if zone.kind is ZoneKind.FLOOR), None)
        for zone_config in hems.zones:
            if zone_config.kind is not ZoneKind.RADIATOR:
                continue
            members = [
                room for room in settings.rooms if room.zone_id == zone_config.zone_id and room.details.valve_entity
            ]
            if not members:
                continue
            zone_id = zone_config.zone_id
            flow = self._flows.setdefault(zone_id, _Flow())
            supply_name = f"flow:{zone_id}" if zone_config.flow_entity else "buffer"
            names = [
                "power",
                supply_name,
                *[room_series(room.entity_id, value) for room in members for value in FLOW_ROOM_VALUES],
            ]
            if flow.bottleneck_at is None or now - flow.bottleneck_at >= BOTTLENECK_EVERY:
                week = await self.samples.series_async(names, now - timedelta(days=BOTTLENECK_DAYS))
                flow.bottleneck = find_bottleneck(
                    [_room_samples(room, week) for room in members], week["power"], week[supply_name], now
                )
                flow.bottleneck_at = now
            recent = await self.samples.series_async(names, now - FLOW_RECENT)
            rooms = {room.entity_id: _room_samples(room, recent) for room in members}
            current = flow.bottleneck or snapshot(list(rooms.values()), now)
            temperatures = [value for room in members if (value := room_celsius(self.sources.get(room.entity_id)))]
            heat_pump = heat_from_pump(
                self._power_now(),
                self.sources.number(zone_config.flow_entity or hems.buffer_entity),
                statistics.fmean(temperatures) if temperatures else None,
            )
            under = [
                room.name
                for room in members
                if not room.measured_at_thermostat and self.supply_of(room.entity_id).supply is Supply.UNDER
            ]
            may_raise: tuple[bool, str | None] | None = None
            flow.advice = None
            if under and self._flow_advisor is not None:
                advice = self._flow_advisor(zone_id, FlowHint.HIGHER)
                if advice is not None:
                    hint, text = advice
                    may_raise = (hint is FlowHint.HIGHER, text or "erst umverteilen, dann Vorlauf")
                    flow.advice = text
            wide = [
                room.name
                for room in members
                if (valve := hour_valve(rooms[room.entity_id], now)) is not None and valve >= 90
            ]
            gap, unknown = self._zone1_gap(zone_config, floor, hems.step_k)
            decision = decide(
                FlowInputs(
                    now=now,
                    offset_k=flow.offset_k,
                    changed_at=flow.changed_at,
                    jumped_at=flow.jumped_at,
                    step_k=hems.step_k,
                    down_k=zone_config.down_k,
                    up_k=zone_config.up_k,
                    min_interval=timedelta(minutes=hems.min_interval_min),
                    heat_pump=heat_pump,
                    under=under,
                    may_raise=may_raise,
                    wide_open=wide,
                    bottleneck=current.name if current is not None else None,
                    bottleneck_valve=hour_valve(rooms[current.room_id], now) if current is not None else None,
                    zone1_gap_k=gap,
                    zone1_unknown=unknown,
                    floor_room_cold=floor is not None and self._floor_room_cold(floor.zone_id),
                    hold=self._flow_hold(zone_id, now),
                )
            )
            flow.hint = decision.hint
            flow.holding = None if decision.changed else decision.reason
            if decision.changed and decision.offset_k != flow.offset_k:
                flow.offset_k, flow.changed_at, flow.reason = decision.offset_k, now, decision.reason
                if decision.jumped:
                    flow.jumped_at = now
                if self.mode is not PlannerMode.OFF:
                    label = self._labels().get(zone_id, zone_id)
                    would = " würde" if hems.effective(Function.FLOW) is not PlannerMode.CONTROL else ""
                    self._log.add(
                        LogCategory.HEMS,
                        f"{label}{would} den Vorlauf ändern: {_signed(flow.offset_k)} K ({decision.reason}).",
                    )
                self._save_state()
                # The executor sends what the plan says: plan again at once (up goes out without waiting).
                self.wake()
            await self._update_curve_advice(zone_config, members, flow, now)

    def _flow_hold(self, zone_id: str, now: datetime) -> str | None:
        """Something else holds the radiator zone's flow now: the night setback, and heating up after it until the
        rooms are back (0.2 K) or 3 h are over (F3)."""
        state = self._nights.get(zone_id)
        if state is None:
            return None
        if state.active and state.blocked is None:
            return "Nachtabsenkung läuft"
        if state.ended_at is not None and now - state.ended_at < REHEAT_HOLD:
            local = now.astimezone(self._zone).time()
            for room in self._settings.current.rooms:
                temperature = room_celsius(self.sources.get(room.entity_id))
                if (
                    room.zone_id == zone_id
                    and temperature is not None
                    and temperature < room.details.setpoint_at(local) - 0.2
                ):
                    return "Aufwärmen nach der Nacht"
        return None

    async def _update_night(self, now: datetime) -> None:
        """F3 (hems/night.py): in the night window switch the zones off or go down to the minimum power."""
        settings = self._settings.current
        hems = settings.hems
        stage = hems.effective(Function.NIGHT)
        bounds = window_at(now, hems.night_start, hems.night_end, self._zone)
        floor = next((zone for zone in hems.zones if zone.kind is ZoneKind.FLOOR), None)
        changed = False
        for zone_config in hems.zones:
            if zone_config.kind is None:
                continue
            state = self._nights.setdefault(zone_config.zone_id, _NightZone())
            before = (state.active, state.shift_k, state.off, state.blocked)
            if stage is PlannerMode.OFF or bounds is None:
                if state.active or state.off:
                    self._end_night(zone_config, state, now)
            else:
                await self._night_zone(zone_config, floor, state, bounds, now)
            # A new window replaces the zone's state object.
            state = self._nights[zone_config.zone_id]
            changed = changed or before != (state.active, state.shift_k, state.off, state.blocked)
        if changed:
            self._save_state()
            # The executor sends what the plan says for the hour now: plan again at once.
            self.wake()

    async def _night_zone(
        self,
        zone: ZoneConfig,
        floor: ZoneConfig | None,
        state: _NightZone,
        bounds: tuple[datetime, datetime],
        now: datetime,
    ) -> None:
        settings = self._settings.current
        label = self._labels().get(zone.zone_id, zone.zone_id)
        day = bounds[0].date()
        if state.day != day:
            forecast = self.forecast.current
            weather = {hour.start: hour for hour in forecast.hours} if forecast is not None else {}
            frost = frozenset(frost_hours(list(weather.values()), self._frost))
            window = plan_window(bounds, weather, frost)
            start_k = state.start_k
            self._nights[zone.zone_id] = state = _NightZone(
                day=day, mode=window.mode, mean_c=window.mean_c, active=True, start_k=start_k
            )
            if zone.kind is ZoneKind.RADIATOR:
                state.shift_k, state.stepped_at = -min(start_k, zone.down_k), now
            self._night_log(
                f"{label}: {window.reason()}, {bounds[0]:%H:%M} bis {bounds[1]:%H:%M} Uhr"
                + (f", Start {_signed(state.shift_k)} K" if state.shift_k else "")
                + "."
            )
        local = now.astimezone(self._zone).time()
        rooms = [
            RoomNow(
                name=room.name,
                temperature_c=room_celsius(self.sources.get(room.entity_id)),
                setpoint_c=room.details.setpoint_at(local),
                loss_k=room.details.night_loss_k
                if room.details.night_loss_k is not None
                else settings.hems.night_loss_k,
                humidity=self.sources.number(room.details.humidity_entity),
            )
            for room in settings.rooms
            if room.zone_id == zone.zone_id
        ]
        guard = self._night_guard(zone.zone_id) if self._night_guard is not None else None
        blocked = humid(rooms) or guard
        if blocked != state.blocked and blocked is not None:
            self._night_log(f"{label} ohne Nachtabsenkung: {blocked}.")
        state.blocked = blocked
        if blocked is not None:
            state.off, state.shift_k = False, 0.0
            return
        if state.mode is NightMode.OFF and state.off_done is None:
            self._night_off(label, state, rooms, now)
        if state.off:
            state.shift_k = 0.0
            return
        if zone.kind is ZoneKind.FLOOR:
            # Zone 1 lower only while the frost protection keeps it on (then F1 does not switch it).
            state.shift_k = -min(ZONE1_K, zone.down_k) if self._frost.active else 0.0
            return
        await self._night_reduce(zone, floor, state, label, now)

    def _night_off(self, label: str, state: _NightZone, rooms: list[RoomNow], now: datetime) -> None:
        if state.off:
            why = off_end(rooms, self._frost.active)
            if why is not None:
                state.off, state.off_done = False, why
                self._night_log(f"{label} wieder auf Auto ({why}); bis zum Ende Minimalleistung.")
            return
        if self._frost.active:
            state.off_done, state.waiting = "Frostschutz der Anlage aktiv", None
            self._night_log(f"{label}: kein Abschalten, der Frostschutz ist aktiv; Minimalleistung.")
            return
        start = off_start(now, icing=self._icing(now), last_defrost_end=self._last_defrost_end(), zone=self._zone)
        if start.off:
            state.off, state.off_since, state.waiting = True, now, None
            after = " direkt nach der Abtauung" if self._icing(now) else ""
            self._night_log(f"{label} aus{after}.")
        elif start.give_up:
            state.off_done, state.waiting = "keine Abtauung bis 7:00 Uhr", None
            self._night_log(f"{label}: keine Abtauung bis 7:00 Uhr, kein Abschalten; Minimalleistung.")
        else:
            state.waiting = start.waiting

    def _night_after_defrost(self, now: datetime) -> None:
        """A defrost just ended: zones waiting for it go off now."""
        for zone_id, state in self._nights.items():
            if state.waiting is None or state.off or state.off_done is not None:
                continue
            start = off_start(now, icing=True, last_defrost_end=self._last_defrost_end(), zone=self._zone)
            if start.off:
                state.off, state.off_since, state.waiting, state.shift_k = True, now, None, 0.0
                self._night_log(f"{self._labels().get(zone_id, zone_id)} aus direkt nach der Abtauung.")
                self._save_state()
                self.wake()

    async def _night_reduce(
        self, zone: ZoneConfig, floor: ZoneConfig | None, state: _NightZone, label: str, now: datetime
    ) -> None:
        """Zone 2 down to the minimum power: step by step until the buffer rises above the flow setpoint."""
        hems = self._settings.current.hems
        targets = [entity for item in hems.zones if (entity := item.flow_setpoint_entity)]
        names = ["power", "buffer", "dhw", *[f"flow_setpoint:{item.zone_id}" for item in hems.zones]]
        recent = await self.samples.series_async(names, now - timedelta(minutes=45))
        target: dict[datetime, float] = {}
        for item in hems.zones:
            for moment, value in recent.get(f"flow_setpoint:{item.zone_id}", []):
                target[moment] = max(value, target.get(moment, value))
        defrosts = [item.at for item in self.cycles.defrosts]
        newest = max((moment for moment, _ in recent["power"]), default=None)
        minimum = (
            newest is not None
            and bool(targets)
            and hems.buffer_entity is not None
            and at_minimum(dict(recent["power"]), dict(recent["buffer"]), target, recent["dhw"], defrosts, newest)
        )
        stats = self.cycles.stats()
        # Only a stop after the last step counts (a pause that ran already says nothing about the step).
        stopped = (
            stats.running is False
            and stats.since is not None
            and now - stats.since >= timedelta(minutes=2)
            and (state.stepped_at is None or stats.since >= state.stepped_at)
        )
        busy = None
        seen = self.cycles.last_seen
        if seen is None or now - seen > POWER_STALE:
            # Without a fresh power a stop would go unseen: no further step down.
            busy = "Leistung der Wärmepumpe meldet sich nicht"
        elif any(abs(now - at) <= timedelta(minutes=15) for at in defrosts):
            busy = "Abtauung"
        elif hot_water(recent["dhw"], now):
            busy = "Warmwasser"
        gap, unknown = self._zone1_gap(zone, floor, STEP_K) if floor is not None else (None, False)
        shift = state.shift_k
        step = reduce_step(
            ReduceInputs(
                now=now,
                shift_k=shift,
                stepped_at=state.stepped_at,
                band_k=zone.down_k,
                stopped=stopped,
                at_minimum=minimum,
                busy=busy,
                zone1_gap_k=gap,
                zone1_unknown=unknown,
                held=state.held,
            )
        )
        state.note = step.note
        if stopped:
            state.stopped_at_k = shift
        if step.changed:
            state.shift_k, state.stepped_at = step.shift_k, now
            self._night_log(f"{label} Nacht: {_signed(step.shift_k)} K" + (f" ({step.note})." if step.note else "."))
        if step.held is not None and state.held is None:
            state.held, state.held_shift_k = step.held, state.shift_k
            self._night_log(f"{label} Nacht hält: {step.held}.")

    def _end_night(self, zone: ZoneConfig, state: _NightZone, now: datetime) -> None:
        """The window ended: learn where to start next time; the zone goes back to its program."""
        label = self._labels().get(zone.zone_id, zone.zone_id)
        if zone.kind is ZoneKind.RADIATOR and state.mode is not None:
            state.start_k = next_start(state.held_shift_k, state.stopped_at_k, zone.down_k, state.start_k)
        if state.active and state.blocked is None:
            self._night_log(f"{label}: Nachtabsenkung zu Ende, zurück auf das Zeitprogramm.")
        state.active, state.shift_k, state.off, state.waiting, state.ended_at = False, 0.0, False, None, now
        state.held = state.held_shift_k = state.stopped_at_k = None

    def _night_log(self, text: str) -> None:
        if self.mode is PlannerMode.OFF:
            return
        stage = self._settings.current.hems.effective(Function.NIGHT)
        prefix = "Nachtabsenkung" if stage is PlannerMode.CONTROL else "Nachtabsenkung (würde)"
        self._log.add(LogCategory.HEMS, f"{prefix}: {text}")

    def _icing(self, now: datetime) -> bool:
        """The evaporator may ice now: the defrost risk of this hour at least "gering" (unknown counts as yes)."""
        forecast = self.forecast.current
        hour = forecast.at(now) if forecast is not None else None
        if hour is None or hour.temperature_c is None:
            return True
        rates = learn_rates(self.cycles.runs, self.cycles.defrosts, now)
        return risk([hour], rates)[0].level is not RiskLevel.NONE

    def _last_defrost_end(self) -> datetime | None:
        if not self.cycles.defrosts:
            return None
        last = self.cycles.defrosts[-1]
        return last.at + timedelta(minutes=last.minutes)

    def _night_status(self) -> list[NightStatus]:
        settings = self._settings.current
        labels = self._labels()
        controlled = settings.hems.effective(Function.NIGHT) is PlannerMode.CONTROL
        result = []
        for zone in settings.hems.zones:
            state = self._nights.get(zone.zone_id)
            if zone.kind is None or state is None:
                continue
            result.append(
                NightStatus(
                    zone_id=zone.zone_id,
                    label=labels.get(zone.zone_id, zone.zone_id),
                    active=state.active and state.blocked is None,
                    mode=state.mode if state.active else None,
                    mean_c=state.mean_c if state.active else None,
                    shift_k=state.shift_k,
                    off=state.off,
                    off_since=state.off_since if state.off else None,
                    text=_night_text(state),
                    start_k=state.start_k,
                    controlled=controlled,
                )
            )
        return result

    def _power_now(self) -> float | None:
        heat_pump = self._state.current
        reading = heat_pump.measurements.get("power_consumption") if heat_pump is not None else None
        return reading.value if reading is not None else None

    def _zone1_gap(self, zone: ZoneConfig, floor: ZoneConfig | None, step_k: float) -> tuple[float | None, bool]:
        """Zone 2's flow setpoint minus zone 1's after one step down, and whether zone 1's is unknown."""
        if floor is None:
            return None, False
        radiators = self.sources.number(zone.flow_setpoint_entity)
        floor_c = self.sources.number(floor.flow_setpoint_entity)
        if radiators is None or floor_c is None:
            return None, True
        per_k = flow_per_k(self._models.flow_lines.get(zone.zone_id))
        return round(radiators - step_k * per_k - floor_c, 2), False

    async def _update_curve_advice(self, zone: ZoneConfig, members: list[Room], flow: _Flow, now: datetime) -> None:
        """Once a day (and after a start): what 7 days of the flow part say about the heating curve."""
        today = now.astimezone(self._zone).date()
        if flow.curve_day == today:
            return
        flow.curve_day = today
        zone_id = zone.zone_id
        supply_name = f"flow:{zone_id}" if zone.flow_entity else "buffer"
        temperature_names = [room_series(room.entity_id, "temperature") for room in members]
        names = [
            f"flow_offset:{zone_id}",
            "power",
            supply_name,
            "outdoor",
            f"flow_setpoint:{zone_id}",
            f"zone_setpoint:{zone_id}",
            f"curve:{zone_id}",
            *temperature_names,
        ]
        series = await self.samples.series_async(names, now - timedelta(days=BOTTLENECK_DAYS))
        by_time: dict[datetime, list[float]] = {}
        for name in temperature_names:
            for moment, value in series[name]:
                by_time.setdefault(moment, []).append(value)
        label = self._labels().get(zone_id, "Zone 2")
        flow.curve = curve_advice(
            offsets=series[f"flow_offset:{zone_id}"],
            power=series["power"],
            supply=series[supply_name],
            rooms_c=sorted((moment, statistics.fmean(values)) for moment, values in by_time.items()),
            outdoor=series["outdoor"],
            flow_setpoint=series[f"flow_setpoint:{zone_id}"],
            zone_setpoint=series[f"zone_setpoint:{zone_id}"],
            curve=series[f"curve:{zone_id}"],
            flow_per_k=flow_per_k(self._models.flow_lines.get(zone_id)),
            label=label,
            now=now,
            zone=self._zone,
        )
        text = flow.curve.text if flow.curve is not None else None
        if text is not None and text != flow.curve_text and self.mode is not PlannerMode.OFF:
            assert flow.curve is not None
            self._log.add(
                LogCategory.HEMS,
                f"Empfehlung: {text} (Vorlauf-Verschiebung der letzten {flow.curve.days} Tage im Mittel "
                f"{_signed(flow.curve.mean_k)} K). Die App ändert die Heizkurve nicht.",
            )
        if text != flow.curve_text:
            flow.curve_text = text
            self._save_state()

    def _source_changed(self, entity_id: str, state: EntityState | None) -> None:
        """A room's temperature, humidity, thermostat or window contact changed: look for an open window."""
        now = self._clock()
        for room in self._settings.current.rooms:
            details = room.details
            if entity_id == details.window_entity:
                self._contact_changed(room, state, now)
                continue
            if entity_id not in (room.entity_id, details.humidity_entity, details.trv_entity) or state is None:
                continue
            detector = self._windows.get(room.entity_id)
            if detector is None or detector.alone is not room.measured_at_thermostat:
                detector = self._windows[room.entity_id] = WindowDetector(alone=room.measured_at_thermostat)
            notify = self._contact_of(room.entity_id) is None
            guarded = self._window_guard is not None and self._window_guard(room.entity_id)
            if room.measured_at_thermostat:
                # The room's temperature is the thermostat's sensor: only the thermostat rule.
                if entity_id == room.entity_id:
                    value = room_celsius(state)
                    if value is not None:
                        self._window_event(room, detector.feed_trv(now, value, guarded=guarded), notify=notify)
                continue
            if entity_id in (room.entity_id, details.humidity_entity):
                temperature = room_celsius(self.sources.get(room.entity_id))
                if temperature is not None:
                    humidity = self.sources.number(details.humidity_entity)
                    event = detector.feed(now, temperature, humidity, self._outside(now))
                    self._window_event(room, event, notify=notify)
            if entity_id == details.trv_entity:
                value = self.sources.attribute(entity_id, "current_temperature")
                if value is not None:
                    self._window_event(room, detector.feed_trv(now, value, guarded=guarded), notify=notify)

    def _contact_changed(self, room: Room, state: EntityState | None, now: datetime) -> None:
        contact = self._contacts.setdefault(room.entity_id, _Contact())
        before = self.window_open(room.entity_id)
        value = state.state if state is not None else ""
        if value in ("on", "off"):
            opened = value == "on"
            if contact.open is not opened:
                contact.since = now
            contact.open, contact.lost_since = opened, None
        elif contact.lost_since is None:
            contact.lost_since = now
        after = self.window_open(room.entity_id)
        if after is not before:
            self._window_event(room, WindowEvent(after, now, Signal.CONTACT))

    def _contact_of(self, room_id: str) -> _Contact | None:
        """The room's window contact while it decides (configured and reporting)."""
        room = next((item for item in self._settings.current.rooms if item.entity_id == room_id), None)
        if room is None or room.details.window_entity is None:
            return None
        contact = self._contacts.get(room_id)
        return contact if contact is not None and contact.usable(self._clock()) else None

    def _window_since(self, room_id: str) -> datetime | None:
        contact = self._contact_of(room_id)
        if contact is not None:
            return contact.since if contact.open else None
        window = self._windows.get(room_id)
        return window.open_since if window is not None else None

    def _outside(self, now: datetime) -> Outside:
        forecast = self.forecast.current
        hour = forecast.at(now) if forecast is not None else None
        celsius = self._outdoor_now()
        if celsius is None and hour is not None:
            celsius = hour.temperature_c
        return Outside(celsius=celsius, relative=hour.humidity if hour is not None else None)

    def _observed(self, room: Room) -> bool:
        """The room is not in the room control's preview or control, which log on their own."""
        stage = effective_stage(self._settings.current.room_control.mode, room.details.stage)
        return not at_least(stage, RoomControlMode.PREVIEW) or room.details.trv_entity is None

    def _window_event(self, room: Room, event: WindowEvent | None, *, notify: bool = True) -> None:
        """notify: the event decides about the window (no contact decides instead)."""
        if event is None or not notify:
            return
        for listener in self._window_listeners:
            try:
                listener(room.entity_id)
            except Exception:
                _LOGGER.exception("A window listener failed")
        if self.mode is PlannerMode.OFF or not self._observed(room):
            return
        if event.opened:
            self._log.add(LogCategory.ROOMS, f"Fenster offen erkannt (würde): {room.name}, {event.describe()}.")
        else:
            self._log.add(LogCategory.ROOMS, f"Fenster wieder zu (würde): {room.name}, {event.describe()}.")

    # --- planning ----------------------------------------------------------------

    async def _make_plan(self) -> None:
        settings = self._settings.current
        hems = settings.hems
        now = self._clock()
        problems: list[str] = []
        if self._home_assistant.client is None:
            problems.append("Ohne Home Assistant fehlen Prognose, Statistik und Räume.")
        elif not self.sources.connected:
            problems.append("Home Assistant ist nicht erreichbar.")
        if not self._backfilled and self._home_assistant.client is not None and self.sources.connected:
            await self._backfill(now)
        due = self._statistics_at is None or now - self._statistics_at >= STATISTICS_EVERY
        if due and self.sources.connected:
            await self._learn_house(now)
        if self.history.problem:
            problems.append(self.history.problem)
        if self._rooms_at is None or now - self._rooms_at >= ROOM_MODEL_EVERY:
            await self._learn_rooms(now)
        measured = self._measured_outdoor()
        forecast = await self.forecast.refresh(measured)
        if self.forecast.problem is not None:
            problems.append(self.forecast.problem)
        if hems.mode is PlannerMode.OFF:
            self._plan, self._problems = None, problems
            return
        if forecast is None:
            self._problems = problems
            return
        heat_pump = self._state.current
        if heat_pump is None or not heat_pump.available or not heat_pump.connected:
            problems.append("Die Wärmepumpe ist nicht verbunden; die Zonen fehlen im Plan.")
        weather = forecast.ahead(now - timedelta(hours=1), timedelta(hours=52))
        rates = learn_rates(self.cycles.runs, self.cycles.defrosts, now)
        risks = {item.start: item for item in risk(weather, rates)}
        frost = frozenset(frost_hours(weather, self._frost))
        zones = self._zone_inputs(heat_pump)
        rooms = [
            RoomInput(
                entity_id=room.entity_id,
                name=room.name,
                zone_id=room.zone_id,
                details=room.details,
                humidity=self.sources.number(room.details.humidity_entity),
            )
            for room in settings.rooms
        ]
        demand_hours = self._demand_hours(weather, [item.zone_id for item in zones])
        plan = make_plan(
            PlanInputs(
                now=now,
                zone=self._zone,
                settings=hems,
                weather=weather,
                risks=risks,
                frost=frost,
                demand={item.start: item for item in demand_hours},
                zones=zones,
                rooms=rooms,
            )
        )
        self._plan, self._problems = plan, problems
        current = hour_start(now)
        first = next((hour for hour in plan.hours if hour.start == current), None)
        if first is not None and current not in self._planned:
            self._planned[current] = first
            self._planned = {start: hour for start, hour in self._planned.items() if now - start <= LOOK_BACK}
            self._save_history()
        if zones:
            # Without the zones (heat pump not connected yet) the plan is incomplete: not logged.
            self._log_changes(now)

    def _zone_inputs(self, heat_pump: HeatPumpState | None) -> list[ZoneInput]:
        hems = self._settings.current.hems
        result = []
        for zone in heat_pump.zones if heat_pump is not None else []:
            config = hems.zone(zone.id)
            flow = self._flows.get(zone.id)
            night = self._nights.get(zone.id)
            result.append(
                ZoneInput(
                    zone_id=zone.id,
                    label=zone.label,
                    config=config,
                    base_c=zone.setpoint.value if zone.setpoint is not None and not zone.override else None,
                    flow_offset_k=flow.offset_k if flow is not None else 0.0,
                    flow_reason=flow.reason if flow is not None and flow.offset_k else None,
                    flow_line=self._models.flow_lines.get(zone.id),
                    night_active=night is not None and night.active,
                    night_shift_k=night.shift_k if night is not None else 0.0,
                    night_off=night is not None and night.off,
                    night_block=night.blocked if night is not None else None,
                    night_start_k=night.start_k if night is not None else START_K,
                )
            )
        return result

    def _demand_hours(self, weather: list[WeatherHour], zone_ids: list[str]) -> list[Any]:
        settings = self._settings.current
        house = self._models.house or start_model(settings.hems.heat_load_w, settings.hems.design_outdoor_c)
        if house is None:
            return []
        loads: dict[str, float] = {}
        for room in settings.rooms:
            if room.zone_id is not None and room.details.heat_load_w:
                loads[room.zone_id] = loads.get(room.zone_id, 0.0) + room.details.heat_load_w
        inside = self._inside_now()
        return demand(
            house,
            [(hour.start, hour.temperature_c, hour.radiation_w) for hour in weather],
            inside if inside is not None else DEFAULT_INSIDE_C,
            zone_shares(loads, zone_ids),
        )

    def _log_changes(self, now: datetime) -> None:
        """One protocol line when the coming changes of the next 12 hours change."""
        if self.mode is PlannerMode.OFF:
            return
        changes = [change for change in self.changes(EXPORT_CHANGES) if change.at - now <= timedelta(hours=12)]
        key = [(change.at, change.text) for change in changes]
        if key == self._logged_changes:
            return
        self._logged_changes = key
        if not changes:
            self._log.add(LogCategory.HEMS, f"Plan ({MODE_NAMES[self.mode]}): in den nächsten 12 h keine Änderung.")
            return
        parts = []
        for change in changes[:4]:
            local = change.at.astimezone(self._zone)
            reason = f" ({change.reasons[0]})" if change.reasons else ""
            parts.append(f"{local:%H} Uhr {change.text}{reason}")
        more = f" und {len(changes) - 4} weitere" if len(changes) > 4 else ""
        self._log.add(LogCategory.HEMS, f"Plan ({MODE_NAMES[self.mode]}): {'; '.join(parts)}{more}.")

    # --- learning ----------------------------------------------------------------

    async def _backfill(self, now: datetime) -> None:
        """Starts and defrosts of the last days from Home Assistant's history."""
        self._backfilled = True
        power_entity, outdoor_entity = self._power_entity(), self._outdoor_entity()
        if power_entity is None:
            return
        start = now - BACKFILL
        try:
            power = await self._home_assistant.history(power_entity, start, now)
            outdoor = await self._home_assistant.history(outdoor_entity, start, now) if outdoor_entity else []
        except HomeAssistantUnavailable as err:
            _LOGGER.warning("Reading the power history failed: %s", err)
            self._backfilled = False
            return
        factor = self.sources.watt_factor(power_entity) or 1.0
        self.cycles.import_history(
            [(moment, value * factor if value is not None else float("nan")) for moment, value in _numbers(power)],
            [(moment, value) for moment, value in _numbers(outdoor) if value is not None],
        )
        self.cycles.save()

    async def _learn_house(self, now: datetime) -> None:
        settings = self._settings.current
        hems = settings.hems
        outdoor_entity = self._outdoor_entity()
        power_entity = self._power_entity()
        rooms = [room.entity_id for room in settings.rooms if not room.measured_at_thermostat]
        flow_entities = [entity for zone in hems.zones if (entity := zone.flow_setpoint_entity or zone.flow_entity)]
        entities = [
            entity
            for entity in (hems.heat_heating_entity, hems.heat_dhw_entity, outdoor_entity, power_entity)
            if entity is not None
        ]
        await self.history.update([*entities, *rooms, *flow_entities])
        self._statistics_at = now
        if self.history.problem is not None:
            return
        outdoor = self.history.means(outdoor_entity)
        heating = hourly_changes(
            self.history.rows(hems.heat_heating_entity), self.sources.kwh_factor(hems.heat_heating_entity) or 1.0
        )
        dhw = hourly_changes(
            self.history.rows(hems.heat_dhw_entity), self.sources.kwh_factor(hems.heat_dhw_entity) or 1.0
        )
        watt_factor = self.sources.watt_factor(power_entity) or 1.0
        power = {hour: value * watt_factor for hour, value in self.history.means(power_entity).items()}
        indoor = [self.history.means(room) for room in rooms]
        days = daily_values(
            heating_kwh=heating, dhw_kwh=dhw, power_w=power, outdoor_c=outdoor, indoor_c=indoor, zone=self._zone
        )
        house = fit_house(days, heating_limit_c=hems.heating_limit_c, radiation_wh=self._models.radiation_wh)
        if house is not None:
            difference = {
                hour: statistics.fmean(values) - outdoor[hour]
                for hour in outdoor
                if (values := [room[hour] for room in indoor if hour in room])
            }
            house = replace(house, lag_h=find_lag(heating, difference), cop=fit_cop(days))
        self._models.house = house
        lines = {}
        for zone in hems.zones:
            entity = zone.flow_setpoint_entity or zone.flow_entity
            line = _flow_line(self.history.means(entity), outdoor, hems.heating_limit_c)
            if line is not None:
                lines[zone.zone_id] = line
        self._models.flow_lines = lines

    async def _learn_rooms(self, now: datetime) -> None:
        settings = self._settings.current
        self._rooms_at = now
        names = ["outdoor", "power"]
        for room in settings.rooms:
            names += [
                room_series(room.entity_id, value) for value in ("temperature", "valve", "trv_setpoint", "setpoint")
            ]
        names += [f"flow:{zone.zone_id}" for zone in settings.hems.zones]
        names += [f"zone_off:{zone_id}" for zone_id in {room.zone_id for room in settings.rooms if room.zone_id}]
        names += ["buffer", "dhw", *[f"flow_setpoint:{zone.zone_id}" for zone in settings.hems.zones]]
        series = await self.samples.series_async(names, now - timedelta(days=21))
        self._minimum = self._learn_minimum(series)
        models = {}
        for room in settings.rooms:
            kind = settings.hems.zone(room.zone_id).kind if room.zone_id else None
            models[room.entity_id] = learn(
                temperature=series[room_series(room.entity_id, "temperature")],
                outdoor=series["outdoor"],
                power=series["power"],
                valve=series[room_series(room.entity_id, "valve")],
                flow=series.get(f"flow:{room.zone_id}", []),
                trv_setpoint=series[room_series(room.entity_id, "trv_setpoint")],
                setpoint=series[room_series(room.entity_id, "setpoint")],
                zone_off=series.get(f"zone_off:{room.zone_id}", []),
                kind=kind.value if kind is not None else "radiator",
            )
        self._models.rooms = models

    def _learn_minimum(self, series: Mapping[str, list[tuple[datetime, float]]]) -> list[BandMinimum]:
        """The heat pump's minimum power per outdoor band: the buffer above a steady flow setpoint (F3)."""
        target: dict[datetime, float] = {}
        for zone in self._settings.current.hems.zones:
            for moment, value in series.get(f"flow_setpoint:{zone.zone_id}", []):
                target[moment] = max(value, target.get(moment, value))
        phases = minimum_phases(
            power=series.get("power", []),
            buffer=series.get("buffer", []),
            target=sorted(target.items()),
            outdoor=series.get("outdoor", []),
            dhw=series.get("dhw", []),
            defrosts=[item.at for item in self.cycles.defrosts],
        )
        return minimum_by_band(phases)

    # --- helpers -----------------------------------------------------------------

    def _labels(self) -> dict[str, str]:
        heat_pump = self._state.current
        return {zone.id: zone.label for zone in heat_pump.zones} if heat_pump is not None else {}

    def _floor_zone(self) -> str | None:
        return next(
            (zone.zone_id for zone in self._settings.current.hems.zones if zone.kind is ZoneKind.FLOOR),
            None,
        )

    def _power_entity(self) -> str | None:
        settings = self._settings.current
        if settings.connection is Connection.MYVAILLANT:
            return settings.myvaillant.power
        return POWER_SENSOR if ExportGroup.ENERGY in settings.export else None

    def _outdoor_entity(self) -> str | None:
        settings = self._settings.current
        if settings.connection is Connection.MYVAILLANT:
            return settings.myvaillant.outdoor_temperature
        return f"sensor.{PREFIX}outdoor_temperature" if ExportGroup.TEMPERATURES in settings.export else None

    def _measured_outdoor(self) -> dict[datetime, float]:
        return self.history.means(self._outdoor_entity())

    def _outdoor_now(self) -> float | None:
        heat_pump = self._state.current
        reading = heat_pump.measurements.get("outdoor_temperature") if heat_pump is not None else None
        return reading.value if reading is not None else None

    def _inside_now(self) -> float | None:
        values = [
            temperature
            for room in self._settings.current.rooms
            if not room.measured_at_thermostat
            and (temperature := room_celsius(self.sources.get(room.entity_id))) is not None
        ]
        return statistics.fmean(values) if values else None

    def _humidity_at(self, moment: datetime) -> float | None:
        forecast = self.forecast.current
        hour = forecast.at(moment) if forecast is not None else None
        return hour.humidity if hour is not None else None

    def _statistics_days(self) -> int:
        rows = self.history.rows(self._outdoor_entity())
        return len({row.start.astimezone(self._zone).date() for row in rows})

    def _day_demand(self) -> list[DayDemand]:
        plan = self._plan
        if plan is None:
            return []
        by_day: dict[date, list[PlanHour]] = {}
        for hour in plan.hours:
            by_day.setdefault(hour.start.astimezone(self._zone).date(), []).append(hour)
        result = []
        for day, hours in sorted(by_day.items()):
            heat = [hour.heat_w for hour in hours if hour.heat_w is not None]
            if not heat:
                continue
            electricity = [hour.electricity_w for hour in hours if hour.electricity_w is not None]
            temps = [hour.outdoor_c for hour in hours if hour.outdoor_c is not None]
            result.append(
                DayDemand(
                    day=day,
                    heat_kwh=round(sum(heat) / 1000, 1),
                    electricity_kwh=round(sum(electricity) / 1000, 1) if len(electricity) == len(heat) else None,
                    outdoor_c=round(statistics.fmean(temps), 1) if temps else None,
                )
            )
        return result

    def _zone1_status(self) -> Zone1Status | None:
        zone_id = self._floor_zone()
        if zone_id is None:
            return None
        heat_pump = self._state.current
        actual = next((zone.mode for zone in heat_pump.zones if zone.id == zone_id), None) if heat_pump else None
        state = self._zone1
        if state.would is None:
            reason = "Ohne Leistung der Wärmepumpe keine Aussage."
        elif self._frost.active:
            reason = "Frostschutz aktiv: Zone bleibt an."
        elif state.would == "off":
            reason = "Wärmepumpe steht: Fußbodenheizung aus, nur Zone 2 nimmt Wärme aus dem Puffer."
        elif state.below_since is not None and self._floor_room_cold(zone_id):
            reason = "Ein Raum der Zone ist unter dem Komfortband: Zone bleibt an."
        else:
            reason = "Wärmepumpe läuft: Fußbodenheizung nimmt Wärme auf."
        return Zone1Status(
            zone_id=zone_id,
            would=state.would,
            actual=actual,
            reason=reason,
            switches_today=state.switches,
            checked_today=state.checked,
            agreed_today=state.agreed,
        )

    def _flow_status(self) -> list[FlowStatus]:
        settings = self._settings.current
        result = []
        for zone in settings.hems.zones:
            if zone.kind is not ZoneKind.RADIATOR:
                continue
            members = [room for room in settings.rooms if room.zone_id == zone.zone_id and room.details.valve_entity]
            states = {room.name: self._supply.get(room.entity_id) for room in members}
            known = {name: state for name, state in states.items() if state is not None and state.valve is not None}
            flow = self._flows.get(zone.zone_id, _Flow())
            long_term = flow.bottleneck
            bottleneck = (
                long_term.name
                if long_term is not None
                else max(known, key=lambda name: known[name].valve or 0.0, default=None)
            )
            now_valve = known[bottleneck].valve if bottleneck in known else None
            result.append(
                FlowStatus(
                    zone_id=zone.zone_id,
                    offset_k=flow.offset_k,
                    hint=flow.hint,
                    reason=flow.reason,
                    bottleneck=bottleneck,
                    max_valve=now_valve,
                    under=sum(1 for state in states.values() if state is not None and state.supply is Supply.UNDER),
                    bottleneck_mean=long_term.mean_valve if long_term is not None else None,
                    bottleneck_under_share=long_term.under_share if long_term is not None else None,
                    bottleneck_long_term=long_term is not None,
                    holding=flow.holding,
                    curve_advice=flow.curve_text,
                    controlled=settings.hems.effective(Function.FLOW) is PlannerMode.CONTROL,
                )
            )
        return result

    def _look_back(self, now: datetime) -> list[HourRecord]:
        stats_runs = self.cycles.runs
        defrosts = self.cycles.defrosts
        records = []
        start = hour_start(now) - LOOK_BACK
        samples = self.samples.series(
            ["outdoor", "power", *[f"zone_setpoint:{zone}" for zone in self._labels()]], start
        )
        moment = start
        while moment < hour_start(now):
            end = moment + timedelta(hours=1)
            outdoor = [value for at, value in samples["outdoor"] if moment <= at < end]
            power = [value for at, value in samples["power"] if moment <= at < end]
            setpoints = {}
            for zone in self._labels():
                values = [value for at, value in samples[f"zone_setpoint:{zone}"] if moment <= at < end]
                if values:
                    setpoints[zone] = round(statistics.fmean(values), 1)
            records.append(
                HourRecord(
                    start=moment,
                    planned=self._planned.get(moment),
                    outdoor_c=round(statistics.fmean(outdoor), 1) if outdoor else None,
                    setpoints=setpoints,
                    starts=sum(1 for run in stats_runs if moment <= run.start < end),
                    defrosts=sum(1 for item in defrosts if moment <= item.at < end),
                    electricity_kwh=round(statistics.fmean(power) / 1000, 2) if len(power) >= 6 else None,
                )
            )
            moment = end
        return records

    # --- persistence -------------------------------------------------------------

    def _load_state(self) -> None:
        path = self._data_dir / STATE_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for zone_id, entry in raw.get("flows", {}).items():
                self._flows[zone_id] = _Flow(
                    offset_k=float(entry["offset_k"]),
                    changed_at=datetime.fromisoformat(entry["changed_at"]) if entry.get("changed_at") else None,
                    reason=entry.get("reason"),
                    jumped_at=datetime.fromisoformat(entry["jumped_at"]) if entry.get("jumped_at") else None,
                    curve_text=entry.get("curve_text") if isinstance(entry.get("curve_text"), str) else None,
                )
            for zone_id, entry in raw.get("nights", {}).items():
                self._nights[zone_id] = _NightZone(
                    day=date.fromisoformat(entry["day"]) if entry.get("day") else None,
                    mode=NightMode(entry["mode"]) if entry.get("mode") else None,
                    mean_c=entry.get("mean_c"),
                    active=bool(entry.get("active")),
                    shift_k=float(entry.get("shift_k") or 0.0),
                    held=entry.get("held"),
                    held_shift_k=entry.get("held_shift_k"),
                    off=bool(entry.get("off")),
                    off_since=datetime.fromisoformat(entry["off_since"]) if entry.get("off_since") else None,
                    off_done=entry.get("off_done"),
                    ended_at=datetime.fromisoformat(entry["ended_at"]) if entry.get("ended_at") else None,
                    start_k=float(entry.get("start_k") or START_K),
                )
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
            _LOGGER.warning("%s unreadable (%s), starting anew", path, err)

    def _save_state(self) -> None:
        payload = {
            "flows": {
                zone_id: {
                    "offset_k": flow.offset_k,
                    "changed_at": flow.changed_at.isoformat() if flow.changed_at else None,
                    "reason": flow.reason,
                    "jumped_at": flow.jumped_at.isoformat() if flow.jumped_at else None,
                    "curve_text": flow.curve_text,
                }
                for zone_id, flow in self._flows.items()
            },
            "nights": {
                zone_id: {
                    "day": state.day.isoformat() if state.day else None,
                    "mode": state.mode.value if state.mode else None,
                    "mean_c": state.mean_c,
                    "active": state.active,
                    "shift_k": state.shift_k,
                    "held": state.held,
                    "held_shift_k": state.held_shift_k,
                    "off": state.off,
                    "off_since": state.off_since.isoformat() if state.off_since else None,
                    "off_done": state.off_done,
                    "ended_at": state.ended_at.isoformat() if state.ended_at else None,
                    "start_k": state.start_k,
                }
                for zone_id, state in self._nights.items()
            },
        }
        path = self._data_dir / STATE_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as err:
            _LOGGER.warning("Saving %s failed: %s", path, err)

    def _save_history(self) -> None:
        """What was planned for the last hours, for the look-back after a restart."""
        payload = [_hour_json(hour) for _, hour in sorted(self._planned.items())]
        path = self._data_dir / HISTORY_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as err:
            _LOGGER.warning("Saving %s failed: %s", path, err)

    def _load_history(self) -> None:
        path = self._data_dir / HISTORY_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            hours = [_hour_from_json(item) for item in raw]
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
            _LOGGER.warning("%s unreadable (%s), starting anew", path, err)
            return
        now = self._clock()
        self._planned = {hour.start: hour for hour in hours if now - hour.start <= LOOK_BACK}

    def _check_legacy(self) -> None:
        """The planner of 1.1/1.2 kept its state in hems.json; a shift it left is named once."""
        path = self._data_dir / LEGACY_FILE
        if not path.exists():
            return
        shifted = []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for control, entry in (raw.get("controls") or {}).items():
                applied, base, override = entry.get("applied"), entry.get("base"), entry.get("override")
                if (applied is not None and applied != base) or override is not None:
                    shifted.append(str(control))
        except (OSError, ValueError, AttributeError, TypeError) as err:
            _LOGGER.warning("Old planner state %s unreadable: %s", path, err)
        if shifted:
            self._log.add(
                LogCategory.HEMS,
                "Der Planer der Version 1.1/1.2 hatte beim Beenden noch eine Verschiebung offen "
                f"({', '.join(sorted(shifted))}). Bitte die Solltemperatur prüfen; ein Quick-Veto endet von selbst. "
                "Version 1.3.0 sendet nichts.",
                problem=True,
            )
        with contextlib.suppress(OSError):
            os.replace(path, path.with_name(LEGACY_FILE + ".old"))


def _hour_json(hour: PlanHour) -> dict[str, Any]:
    return {
        "start": hour.start.isoformat(),
        "outdoor_c": hour.outdoor_c,
        "humidity": hour.humidity,
        "defrost": hour.defrost.value if hour.defrost is not None else None,
        "heat_w": hour.heat_w,
        "electricity_w": hour.electricity_w,
        "frost": hour.frost,
        "zones": [
            [entry.zone_id, entry.shift_k, entry.target_c, entry.flow_c, entry.block, entry.night]
            for entry in hour.zones
        ],
        "rooms": [[entry.entity_id, entry.setpoint_c] for entry in hour.rooms],
        "reasons": list(hour.reasons),
    }


def _hour_from_json(raw: dict[str, Any]) -> PlanHour:
    return PlanHour(
        start=datetime.fromisoformat(raw["start"]),
        outdoor_c=raw.get("outdoor_c"),
        humidity=raw.get("humidity"),
        defrost=RiskLevel(raw["defrost"]) if raw.get("defrost") else None,
        heat_w=raw.get("heat_w"),
        electricity_w=raw.get("electricity_w"),
        frost=bool(raw.get("frost")),
        zones=[
            ZoneHour(
                zone_id=str(item[0]),
                shift_k=float(item[1]),
                target_c=item[2],
                flow_c=item[3],
                block=bool(item[4]),
                night=bool(item[5]),
            )
            for item in raw.get("zones", [])
        ],
        rooms=[RoomHour(entity_id=str(item[0]), setpoint_c=float(item[1])) for item in raw.get("rooms", [])],
        reasons=[str(reason) for reason in raw.get("reasons", [])],
    )


def _learned(model: RoomModel) -> RoomLearned:
    values = {
        "tau_h": model.tau_h,
        "gains_k_per_h": model.gains_k_per_h,
        "heating_rate": model.heating_rate,
        "startup_min": model.startup_min,
        "demand_valve": model.demand_valve,
        "correction_k": model.correction_k,
        "overshoot_k": model.overshoot_k,
    }
    return RoomLearned(
        tau_h=model.tau_h.value,
        gains_k_per_h=model.gains_k_per_h.value,
        heating_rate=model.heating_rate.value,
        startup_min=model.startup_min.value,
        demand_valve=model.demand_valve.value,
        correction_k=model.correction_k.value,
        overshoot_k=model.overshoot_k.value,
        learned={name: value.learned for name, value in values.items()},
        learning=model.learning,
    )


def _flow_line(
    flow: Mapping[datetime, float], outdoor: Mapping[datetime, float], heating_limit_c: float
) -> tuple[float, float] | None:
    """Flow (setpoint) = a + b * outdoor from hourly means while the circuit heats."""
    pairs = [
        (outdoor[hour], value)
        for hour, value in flow.items()
        if hour in outdoor and value > 20 and outdoor[hour] < heating_limit_c
    ]
    if len(pairs) < 48:
        return None
    xs, ys = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / sxx
    return round(mean_y - b * mean_x, 2), round(b, 3)


def _numbers(changes: list[tuple[datetime, str]]) -> list[tuple[datetime, float | None]]:
    result: list[tuple[datetime, float | None]] = []
    for moment, text in changes:
        try:
            value: float | None = float(text)
        except ValueError:
            value = None
        result.append((moment, value))
    return result


def _quarter(moment: datetime) -> datetime:
    return moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def _number(value: float | None) -> str:
    """German, without a decimal for whole numbers: "3", "1,5"."""
    if value is None:
        return "-"
    return f"{value:.0f}" if value == round(value) else f"{value:.1f}".replace(".", ",")


def _signed(value: float) -> str:
    if value == 0:
        return "±0"
    return f"{'+' if value > 0 else MINUS}{_number(abs(value))}"


def plan_csv(view: PlanView, zone: tzinfo) -> bytes:
    """The plan (and the look-back) as a CSV file like the protocol: semicolons, decimal commas, local time."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    zones = list(view.labels.items())
    rooms = list(view.rooms.items())
    header = ["Zeit", "Art"]
    for _, label in zones:
        header += [f"{label} Verschiebung K", f"{label} Soll °C", f"{label} Vorlauf °C"]
    header += [f"{name} Soll °C" for _, name in rooms]
    header += ["Außen °C", "Feuchte %", "Abtaurisiko", "Wärmebedarf W", "Strom W", "Frostschutz", "Grund"]
    writer.writerow(header)

    def row(hour: PlanHour, kind: str, start: datetime) -> list[str]:
        cells = [f"{start.astimezone(zone):%d.%m.%Y %H:%M}", kind]
        for zone_id, _ in zones:
            entry = next((item for item in hour.zones if item.zone_id == zone_id), None)
            cells += [
                _csv(entry.shift_k if entry else None),
                _csv(entry.target_c if entry else None),
                _csv(entry.flow_c if entry else None),
            ]
        for entity_id, _ in rooms:
            room = next((item for item in hour.rooms if item.entity_id == entity_id), None)
            cells.append(_csv(room.setpoint_c if room else None))
        cells += [
            _csv(hour.outdoor_c),
            _csv(hour.humidity),
            LEVEL_NAMES[hour.defrost] if hour.defrost is not None else "",
            _csv(hour.heat_w),
            _csv(hour.electricity_w),
            "ja" if hour.frost else "",
            "; ".join(hour.reasons),
        ]
        return cells

    for record in view.history:
        if record.planned is not None:
            writer.writerow(row(record.planned, "Rückblick", record.start))
    for hour in view.hours:
        writer.writerow(row(hour, "Plan", hour.start))
    return ("﻿" + out.getvalue()).encode("utf-8")


def _csv(value: float | None) -> str:
    return "" if value is None else f"{value:g}".replace(".", ",")
