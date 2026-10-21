"""The energy management as one service for main and api.

Plans every quarter hour and whenever prices or the PV forecast change,
carries the plan out at least every 30 seconds and after every new device
state, records the daily values, and reports its status to the UI and to
Home Assistant. When the app stops, it returns every control it changed to
the base value.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import HomeAssistantUnavailable
from vaillant_eebus.ha_export import PREFIX, Sensor
from vaillant_eebus.hems.config import HemsSettings, PlannerMode, TariffKind, hems_to_json
from vaillant_eebus.hems.days import MIN_COVERAGE, MIN_DAYS, DayRecorder
from vaillant_eebus.hems.executor import Executor
from vaillant_eebus.hems.planner import DHW, Action, Block, DeviceView, Plan, PlanSlot, ZoneView, make_plan
from vaillant_eebus.hems.supply import BASE_DAYS, base_load, base_profile, number, pv_forecast, surplus
from vaillant_eebus.hems.tariff import feed_in_price, import_prices
from vaillant_eebus.hems.timeline import SLOT, horizon, local_day, local_midnight, slot_start
from vaillant_eebus.hems.weather import OPEN_METEO_URL, WeatherForecasts
from vaillant_eebus.home_assistant import POWER_SENSOR, HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.settings import ExportGroup, OperatingMode, SettingsStore
from vaillant_eebus.state import HeatPumpState, StateService
from vaillant_eebus.write_gate import WriteGate

_LOGGER = logging.getLogger(__name__)

EXECUTE_EVERY = 30.0
# Bursts of new prices and forecasts are planned once.
PLAN_SETTLE = 2.0
PROFILE_EVERY = timedelta(hours=6)
# Time to return to the base values when the app stops: the gate's 5 s per control plus
# up to 10 s for the device's answer. The Supervisor waits 30 s (config.yaml timeout).
RELEASE_TIMEOUT = 15.0
PLAN_LOG_BLOCKS = 6

_MODE_LOG = {
    PlannerMode.OFF: "Planer ausgeschaltet.",
    PlannerMode.PREVIEW: "Planer auf Vorschau: Er plant und zeigt, was er senden würde, sendet aber nichts.",
    PlannerMode.CONTROL: "Planer auf Steuern: Er setzt den Plan über die Schreibsperre um.",
}
_MODE_NAMES = {PlannerMode.OFF: "Aus", PlannerMode.PREVIEW: "Vorschau", PlannerMode.CONTROL: "Steuern"}


@dataclass(frozen=True, slots=True)
class NextAction:
    at: datetime
    # German, e.g. "Warmwasser auf 60 °C und Einmalladung".
    text: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class Demand:
    """Estimated electricity of the heat pump tomorrow (model from the daily values)."""

    kwh: float
    spread_kwh: float
    days: int
    outdoor_c: float


@dataclass(frozen=True, slots=True)
class HemsStatus:
    mode: PlannerMode
    app_mode: OperatingMode
    settings: dict[str, Any]
    plan: Plan | None
    # German: missing or outdated inputs.
    problems: list[str]
    next_action: NextAction | None
    # Control ("dhw" or zone ID) -> label, base value and the value the planner set.
    labels: dict[str, str]
    bases: dict[str, float]
    shifted: dict[str, float]
    feed_in: float | None
    demand_tomorrow: Demand | None
    # Complete heating days learned so far, and how many the model needs.
    learned_days: int
    needed_days: int


class HemsService:
    def __init__(
        self,
        settings: SettingsStore,
        state: StateService,
        gate: WriteGate,
        energy: EnergyMeter,
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
        self._gate = gate
        self._home_assistant = home_assistant
        self._log = log
        self._zone = zone
        self._clock = clock
        self.weather = WeatherForecasts(settings, home_assistant.client, open_meteo_url=open_meteo_url, clock=clock)
        self.executor = Executor(gate, state, settings, log, data_dir, zone=zone, clock=clock)
        self.days = DayRecorder(state, home_assistant.importer, energy, data_dir, zone=zone, clock=clock)
        self._plan: Plan | None = None
        self._problems: list[str] = []
        self._profile: tuple[datetime, str, dict[tuple[int, int], float]] | None = None
        self._wake = asyncio.Event()
        # One change at a time: carrying out the plan and returning to the base values.
        self._lock = asyncio.Lock()
        home_assistant.hems_entities.on_forecast_change(self.wake)
        if home_assistant.exporter is not None:
            home_assistant.exporter.add_source(self.sensors)

    @property
    def plan(self) -> Plan | None:
        return self._plan

    def wake(self) -> None:
        """Plan again soon, e.g. after new prices."""
        self._wake.set()

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._planning())
                tasks.create_task(self._executing())
                tasks.create_task(self.days.run())
        finally:
            self.days.close()
            await _despite_cancellation(self._release_on_stop())

    async def _release_on_stop(self) -> None:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(RELEASE_TIMEOUT):
                await self.release("Die App wird beendet")

    async def release(self, reason: str) -> None:
        """Return every control the planner changed to its base value."""
        async with self._lock:
            await self.executor.release(reason)

    async def set_mode(self, mode: PlannerMode) -> PlannerMode:
        before = self._settings.current.hems.mode
        if mode is before:
            return mode
        if before is PlannerMode.CONTROL:
            await self.release(f"Planer auf {_MODE_NAMES[mode]}")
        self._settings.update(lambda settings: replace(settings, hems=replace(settings.hems, mode=mode)))
        self._log.add(LogCategory.HEMS, _MODE_LOG[mode])
        self.wake()
        return mode

    def change_settings(self, hems: HemsSettings) -> HemsSettings:
        """Save the user's settings (the mode stays) and plan again at once."""
        before = self._settings.current.hems
        hems = replace(hems, mode=before.mode)
        if hems == before:
            return hems
        self._settings.update(lambda settings: replace(settings, hems=hems))
        self._home_assistant.watch()
        self._log.add(LogCategory.SETTINGS, "Einstellungen des Energiemanagements geändert.")
        self.wake()
        return hems

    def status(self) -> HemsStatus:
        settings = self._settings.current
        heat_pump = self._state.current
        return HemsStatus(
            mode=settings.hems.mode,
            app_mode=settings.operating_mode,
            settings=hems_to_json(settings.hems),
            plan=self._plan,
            problems=list(self._problems),
            next_action=self._next_action(),
            labels=_labels(heat_pump),
            bases=self.executor.bases(),
            shifted=self.executor.shifted(),
            feed_in=feed_in_price(settings.hems.tariff),
            demand_tomorrow=self._demand(),
            learned_days=self._learned_days(settings.hems.heating_limit_c),
            needed_days=MIN_DAYS,
        )

    def sensors(self) -> list[Sensor]:
        """The planner's sensors for Home Assistant (export group "Energiemanagement")."""
        status = self.status()
        labels = status.labels
        upcoming = [block for block in status.plan.blocks if block.end > self._clock()] if status.plan else []
        demand = status.demand_tomorrow
        return [
            Sensor(
                entity_id=f"sensor.{PREFIX}hems_status",
                state=_MODE_NAMES[status.mode],
                attributes={
                    "friendly_name": "Energiemanagement Status",
                    "icon": "mdi:home-lightning-bolt-outline",
                    "fehlende_daten": status.problems,
                },
            ),
            Sensor(
                entity_id=f"sensor.{PREFIX}hems_next_action",
                state=status.next_action.text[:250] if status.next_action else "keine",
                attributes={
                    "friendly_name": "Energiemanagement nächste Aktion",
                    "icon": "mdi:clock-outline",
                    "zeit": status.next_action.at.isoformat() if status.next_action else None,
                    "grund": status.next_action.reason if status.next_action else None,
                },
            ),
            Sensor(
                entity_id=f"sensor.{PREFIX}hems_plan",
                state=str(len(upcoming)),
                attributes={
                    "friendly_name": "Energiemanagement geplante Blöcke",
                    "icon": "mdi:calendar-clock",
                    "blöcke": [_block_json(block, labels) for block in upcoming],
                },
            ),
            Sensor(
                entity_id=f"sensor.{PREFIX}hems_demand_tomorrow",
                state="unavailable" if demand is None else f"{demand.kwh:.1f}",
                attributes={
                    "friendly_name": "Wärmepumpe Strombedarf morgen (geschätzt)",
                    "unit_of_measurement": "kWh",
                    "icon": "mdi:chart-bell-curve",
                    "streuung_kwh": demand.spread_kwh if demand else None,
                    "gelernte_tage": demand.days if demand else None,
                },
            ),
        ]

    async def plan_now(self) -> None:
        # Without the lock: reading Home Assistant and the weather may take seconds, and
        # returning to the base values must never wait for it.
        await self._make_plan()

    # --- internals -------------------------------------------------------------

    async def _planning(self) -> None:
        while True:
            try:
                await self.plan_now()
            except Exception:
                _LOGGER.exception("Planning failed")
            now = self._clock()
            wait = (slot_start(now) + SLOT - now).total_seconds() + 1
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(wait):
                    await self._wake.wait()
                await asyncio.sleep(PLAN_SETTLE)
            self._wake.clear()

    async def _executing(self) -> None:
        async with self._state.listen() as updates:
            while True:
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(EXECUTE_EVERY):
                        await updates.get()
                try:
                    async with self._lock:
                        await self.executor.step(self._plan)
                except Exception:
                    # A fault here must not stop the rest of the app.
                    _LOGGER.exception("Carrying out the plan failed")

    async def _make_plan(self) -> None:
        settings = self._settings.current
        hems = settings.hems
        if hems.mode is PlannerMode.OFF:
            self._plan, self._problems = None, []
            return
        heat_pump = self._state.current
        if heat_pump is None or not heat_pump.available or not heat_pump.connected:
            self._problems = ["Die Wärmepumpe ist nicht verbunden; der Plan wartet."]
            return
        now = self._clock()
        slots = horizon(now, self._zone)
        problems: list[str] = []
        if self._home_assistant.client is None:
            problems.append("Ohne Home Assistant fehlen Preise und Prognosen.")
        elif not self._home_assistant.hems_entities.connected:
            problems.append("Home Assistant ist nicht erreichbar.")

        price_state = self._home_assistant.entity_state(hems.tariff.price_entity)
        prices = import_prices(hems.tariff, price_state, slots, self._zone)
        problems += _price_problems(hems, prices)

        pv_states = [
            self._home_assistant.entity_state(entity) for entity in (hems.pv_forecast_today, hems.pv_forecast_tomorrow)
        ]
        pv = pv_forecast(pv_states, slots, self._zone)
        if hems.pv_forecast_today is None and hems.pv_forecast_tomorrow is None:
            problems.append("Es ist keine PV-Prognose gewählt.")
        elif all(value is None for value in pv):
            problems.append("Die PV-Prognose liefert keine Werte.")

        base = base_load(await self._base_profile(now, problems), slots, self._zone)
        soc = number(self._home_assistant.entity_state(hems.battery_soc))
        if hems.battery_soc is not None and not hems.battery_capacity_kwh:
            problems.append("Die Akku-Kapazität fehlt in den Einstellungen.")
        elif hems.battery_soc is not None and not hems.battery_charge_kw:
            problems.append("Die Ladeleistung des Akkus fehlt in den Einstellungen.")
        elif hems.battery_soc is not None and soc is None:
            problems.append("Der Akku-Ladestand ist gerade nicht verfügbar.")
        supply = surplus(pv, base, soc, hems)

        forecast = await self.weather.forecast()
        if self.weather.problem is not None:
            problems.append(self.weather.problem)

        plan_slots = [
            PlanSlot(
                start=slot,
                price=None if price is None else round(price, 4),
                pv_w=entry.pv_w,
                base_w=entry.base_w,
                surplus_w=entry.surplus_w,
                battery_soc=entry.battery_soc,
                outdoor_c=forecast.at(slot) if forecast is not None else None,
            )
            for slot, price, entry in zip(slots, prices, supply, strict=True)
        ]
        previous = self._plan
        keep = [block for block in previous.blocks if block.covers(now)] if previous is not None else []
        dhw_base = self.executor.bases().get(DHW)
        if dhw_base is None and heat_pump.hot_water is not None and heat_pump.hot_water.setpoint is not None:
            dhw_base = heat_pump.hot_water.setpoint.value
        plan = make_plan(
            now,
            plan_slots,
            hems,
            _device_view(heat_pump),
            zone=self._zone,
            dhw_base=dhw_base,
            dhw_done=self.executor.charged_days(preview=hems.mode is PlannerMode.PREVIEW),
            keep=keep,
        )
        self._log_changes(previous, plan, _labels(heat_pump), now)
        self._plan, self._problems = plan, problems

    async def _base_profile(self, now: datetime, problems: list[str]) -> dict[tuple[int, int], float]:
        hems = self._settings.current.hems
        if hems.house_power is None:
            problems.append("Es ist kein Hausverbrauch gewählt.")
            return {}
        cached = self._profile
        if cached is not None and cached[1] == hems.house_power and now - cached[0] < PROFILE_EVERY:
            profile = cached[2]
        else:
            start = now - timedelta(days=BASE_DAYS) - SLOT
            try:
                house = await self._home_assistant.history(hems.house_power, start, now)
                pump = (
                    await self._home_assistant.history(POWER_SENSOR, start, now)
                    if ExportGroup.ENERGY in self._settings.current.export
                    else []
                )
            except HomeAssistantUnavailable as err:
                _LOGGER.warning("Reading the house's power history failed: %s", err)
                return cached[2] if cached is not None and cached[1] == hems.house_power else {}
            state = self._home_assistant.entity_state(hems.house_power)
            unit = state.attributes.get("unit_of_measurement") if state is not None else None
            if unit not in ("W", "kW"):
                problems.append("Der Hausverbrauch braucht die Einheit W oder kW.")
                return {}
            profile = base_profile(house, str(unit), pump, now, self._zone)
            self._profile = (now, hems.house_power, profile)
        if not profile:
            problems.append("Für den Hausverbrauch gibt es noch keinen Verlauf in Home Assistant.")
        return profile

    def _log_changes(self, previous: Plan | None, plan: Plan, labels: dict[str, str], now: datetime) -> None:
        """One protocol line when the upcoming blocks change."""

        def upcoming(source: Plan | None) -> list[tuple[str, Action, datetime, datetime, float | None, float | None]]:
            if source is None:
                return []
            return [
                (block.control, block.action, block.start, block.end, block.shift_k, block.target_c)
                for block in source.blocks
                if block.end > now
            ]

        if upcoming(previous) == upcoming(plan):
            return
        blocks = [block for block in plan.blocks if block.end > now]
        if not blocks:
            self._log.add(LogCategory.HEMS, "Plan: keine Verschiebung geplant.")
            return
        parts = [self._describe(block, labels) for block in blocks[:PLAN_LOG_BLOCKS]]
        more = f" und {len(blocks) - PLAN_LOG_BLOCKS} weitere" if len(blocks) > PLAN_LOG_BLOCKS else ""
        self._log.add(LogCategory.HEMS, f"Plan: {'; '.join(parts)}{more}.")

    def _describe(self, block: Block, labels: dict[str, str]) -> str:
        start = block.start.astimezone(self._zone)
        end = block.end.astimezone(self._zone)
        day = "morgen " if start.date() > self._clock().astimezone(self._zone).date() else ""
        return f"{day}{start:%H:%M} bis {end:%H:%M} {_action_text(block, labels)} ({block.reason})"

    def _next_action(self) -> NextAction | None:
        plan = self._plan
        if plan is None:
            return None
        now = self._clock()
        labels = _labels(self._state.current)
        candidates: list[NextAction] = []
        for block in plan.blocks:
            if block.start > now:
                candidates.append(NextAction(at=block.start, text=_action_text(block, labels), reason=block.reason))
            elif block.end > now:
                label = labels.get(block.control, block.control)
                candidates.append(NextAction(at=block.end, text=f"{label} zurück auf den Grundwert", reason=None))
        return min(candidates, key=lambda action: action.at, default=None)

    def _demand(self) -> Demand | None:
        hems = self._settings.current.hems
        model = self.days.model(hems.heating_limit_c)
        forecast = self.weather.current
        indoor = self.days.indoor()
        if model is None or forecast is None or indoor is None:
            return None
        tomorrow = local_day(self._clock(), self._zone) + timedelta(days=1)
        start, end = local_midnight(tomorrow, self._zone), local_midnight(tomorrow + timedelta(days=1), self._zone)
        temperatures = [value for hour, value in forecast.hours.items() if start <= hour < end]
        if len(temperatures) < 20:
            return None
        outdoor = statistics.fmean(temperatures)
        return Demand(
            kwh=round(model.predict(indoor, outdoor), 1),
            spread_kwh=model.spread_kwh,
            days=model.days,
            outdoor_c=round(outdoor, 1),
        )

    def _learned_days(self, heating_limit_c: float) -> int:
        return sum(
            1
            for value in self.days.values()
            if value.kwh is not None
            and not value.incomplete
            and value.outdoor_c is not None
            and value.outdoor_c < heating_limit_c
            and value.coverage >= MIN_COVERAGE
        )


async def _despite_cancellation(work: Coroutine[Any, Any, None]) -> None:
    """Run work to its end even if the waiting task is cancelled again meanwhile.

    Stopping the app cancels the services more than once (uvicorn passes SIGTERM on
    after its own shutdown); work must limit its own duration.
    """
    task = asyncio.create_task(work)
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)


def _price_problems(hems: HemsSettings, prices: list[float | None]) -> list[str]:
    tariff = hems.tariff
    match tariff.kind:
        case TariffKind.FIXED:
            return ["Der Strompreis fehlt in den Einstellungen."] if tariff.price_ct is None else []
        case TariffKind.FIXED_14A:
            missing = None in (tariff.price_ct, tariff.high_price_ct, tariff.low_price_ct)
            return ["Für §14a Modul 3 fehlen Preise in den Einstellungen."] if missing else []
        case TariffKind.DYNAMIC:
            if tariff.price_entity is None:
                return ["Es ist kein Preis-Sensor gewählt."]
            if all(price is None for price in prices):
                return ["Der Preis-Sensor liefert keine Preise."]
            return []


def _device_view(heat_pump: HeatPumpState) -> DeviceView:
    water = heat_pump.hot_water
    return DeviceView(
        zones=[
            ZoneView(
                id=zone.id,
                label=zone.label,
                mode=zone.mode,
                writable=zone.setpoint is not None and zone.setpoint.writable,
            )
            for zone in heat_pump.zones
        ],
        dhw_mode=water.mode if water is not None else None,
        dhw_writable=water is not None and water.setpoint is not None and water.setpoint.writable,
        boost_writable=water is not None and water.boost_writable,
    )


def _labels(heat_pump: HeatPumpState | None) -> dict[str, str]:
    labels = {DHW: "Warmwasser"}
    if heat_pump is not None:
        labels.update({zone.id: zone.label for zone in heat_pump.zones})
    return labels


def _action_text(block: Block, labels: dict[str, str]) -> str:
    label = labels.get(block.control, block.control)
    if block.action is Action.CHARGE:
        return f"{label} auf {_number(block.target_c or 0)} °C und Einmalladung"
    shift = block.shift_k or 0.0
    sign = "+" if shift > 0 else "-"
    return f"{label} {sign}{_number(abs(shift))} K"


def _block_json(block: Block, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "steuerung": labels.get(block.control, block.control),
        "aktion": _action_text(block, labels),
        "beginn": block.start.isoformat(),
        "ende": block.end.isoformat(),
        "grund": block.reason,
    }


def _number(value: float) -> str:
    """German, without a decimal for whole numbers: "60", "1,5"."""
    return f"{value:.0f}" if value == round(value) else f"{value:.1f}".replace(".", ",")
