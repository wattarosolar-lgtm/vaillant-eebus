"""The zone executor (1.5.0, F0/F1) against the fake myVAILLANT integration, and the cloud budget."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from tests.fake_home_assistant import MV_HOT_WATER, MV_OUTDOOR, MV_POWER, MV_ZONE_1, MV_ZONE_2, TOKEN, FakeHomeAssistant
from tests.support import eventually
from vaillant_eebus.bridge_client import Backoff, BridgeClient
from vaillant_eebus.cloud_budget import BudgetLevel, CloudBudget
from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import HomeAssistantClient
from vaillant_eebus.hems.config import HemsSettings, PlannerMode, ZoneConfig, ZoneKind
from vaillant_eebus.hems.executor import LIFESIGN_ENTITY, ZoneExecutor
from vaillant_eebus.hems.forecast import hour_start
from vaillant_eebus.hems.rules import Part, Plan, PlanHour, ZoneHour
from vaillant_eebus.hems.service import HemsService
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.myvaillant import MyVaillantLink, MyVaillantState
from vaillant_eebus.pairing import ConnectionState, PairingService
from vaillant_eebus.settings import Connection, MyVaillantEntities, OperatingMode, SettingsStore
from vaillant_eebus.state import Zone
from vaillant_eebus.write_gate import SetHotWaterMode, SetZoneMode, SetZoneOverride, Source, WriteGate, WriteRejected

ZONE_COMMANDS = ("mypyllant.set_quick_veto", "mypyllant.cancel_quick_veto", "climate.set_hvac_mode")


class Clock:
    def __init__(self) -> None:
        # A quarter past the hour: durations up to the next planned change are not whole hours.
        self.now = datetime.now(UTC).replace(minute=15, second=0, microsecond=0)

    def __call__(self) -> datetime:
        return self.now


class FakeHems:
    """What the executor needs from the energy management: the plan and F1's decision."""

    def __init__(self) -> None:
        self.plan: Plan | None = None
        self.would: str | None = None
        self.frost_active = False

    def on_update(self, listener: Callable[[], None]) -> None:
        pass

    def set_control_status(self, status: Callable[[], Any]) -> None:
        pass

    def floor_zone(self) -> str | None:
        return MV_ZONE_1

    def zone1_would(self) -> str | None:
        return self.would


@dataclass
class Rig:
    fake: FakeHomeAssistant
    settings: SettingsStore
    device: MyVaillantState
    gate: WriteGate
    budget: CloudBudget
    hems: FakeHems
    executor: ZoneExecutor
    log: LogStore
    clock: Clock
    monotonic: list[float]

    def plan(self, shift: Callable[[int], float], zone_id: str = MV_ZONE_2) -> None:
        start = hour_start(self.clock.now)
        self.hems.plan = Plan(
            created_at=self.clock.now,
            hours=[
                PlanHour(
                    start=start + timedelta(hours=index),
                    outdoor_c=None,
                    humidity=None,
                    defrost=None,
                    heat_w=None,
                    electricity_w=None,
                    frost=False,
                    zones=[
                        ZoneHour(
                            zone_id=zone_id,
                            shift_k=shift(index),
                            target_c=None,
                            flow_c=None,
                            block=False,
                            night=False,
                            parts={Part.FLOW: shift(index)} if shift(index) else {},
                        )
                    ],
                    rooms=[],
                    reasons=["Vorlauf tiefer: alle Räume versorgt"],
                )
                for index in range(12)
            ],
        )

    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [(service, body) for service, body, _ in self.fake.service_calls if service in ZONE_COMMANDS]

    def zone(self, zone_id: str) -> Zone:
        current = self.device.current
        assert current is not None
        return next(zone for zone in current.zones if zone.id == zone_id)

    def later(self, minutes: float) -> None:
        self.clock.now += timedelta(minutes=minutes)

    async def settle(self) -> None:
        """Let the gate's follow of the sent values end (confirmed plus its settling time)."""
        self.monotonic[0] += 120.0
        # Any new state makes the gate check its deadlines.
        self.fake.change(MV_OUTDOOR, str(4.5 + self.monotonic[0] / 1000))
        await eventually(lambda: not self.gate.pending and not self._settling())

    def _settling(self) -> bool:
        from vaillant_eebus.write_gate import Control

        return any(
            self.gate.settling(control, zone_id)
            for control in (Control.ZONE_SETPOINT, Control.ZONE_MODE)
            for zone_id in (MV_ZONE_1, MV_ZONE_2)
        )

    def settings_hems(self, **changes: Any) -> None:
        self.settings.update(lambda current: replace(current, hems=replace(current.hems, **changes)))


@pytest.fixture
async def rig(
    fake_ha: FakeHomeAssistant,
    settings_store: SettingsStore,
    log_store: LogStore,
    bridge_client: BridgeClient,
    pairing: PairingService,
    tmp_path: Path,
) -> AsyncIterator[Rig]:
    clock = Clock()
    fake_ha.add_myvaillant()
    # Zone 2 in auto, its time program at 21 °C.
    fake_ha.programs[MV_ZONE_2] = 21.0
    fake_ha.change(MV_ZONE_2, "auto", temperature=21.0)
    settings_store.update(
        lambda current: replace(
            current,
            connection=Connection.MYVAILLANT,
            operating_mode=OperatingMode.LIVE,
            myvaillant=MyVaillantEntities(
                zones=(MV_ZONE_1, MV_ZONE_2), hot_water=MV_HOT_WATER, outdoor_temperature=MV_OUTDOOR, power=MV_POWER
            ),
            hems=HemsSettings(
                mode=PlannerMode.CONTROL,
                zones=(ZoneConfig(MV_ZONE_1, kind=ZoneKind.FLOOR), ZoneConfig(MV_ZONE_2, kind=ZoneKind.RADIATOR)),
                # F2 is offered up to "Vorschau" in this version; the tests drive the quick veto through it.
                flow_mode=PlannerMode.CONTROL,
                zone1_mode=PlannerMode.CONTROL,
            ),
        )
    )
    monotonic = [1000.0]
    async with HomeAssistantClient(
        fake_ha.url, TOKEN, log=log_store, backoff=Backoff(initial=0.05, maximum=0.2), refresh_interval=60
    ) as client:
        device = MyVaillantState(settings_store, available=True)
        energy = EnergyMeter(device, tmp_path)
        HomeAssistant(settings_store, device, energy, log_store, client)
        budget = CloudBudget(tmp_path, settings_store, log_store, clock=clock)
        gate = WriteGate(
            bridge_client,
            pairing,
            device,
            settings_store,
            log_store,
            link=MyVaillantLink(client, device, settings_store),
            budget=budget,
            rate_limit_s=0,
            clock=lambda: monotonic[0],
        )
        hems = FakeHems()
        executor = ZoneExecutor(cast(HemsService, hems), gate, device, settings_store, log_store, tmp_path, clock=clock)
        tasks = [asyncio.create_task(client.run()), asyncio.create_task(gate.run())]
        await eventually(lambda: device.connection_state() is ConnectionState.CONNECTED)
        yield Rig(fake_ha, settings_store, device, gate, budget, hems, executor, log_store, clock, monotonic)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def messages(log: LogStore, category: LogCategory | None = None) -> list[str]:
    return [entry.message for entry in (await log.page(category=category)).entries]


# --- quick veto (F0) ------------------------------------------------------------------------------


async def test_a_shift_becomes_a_quick_veto_until_the_next_planned_change(rig: Rig) -> None:
    # -1 K now and in the next hour, then nothing: the change comes in 1 h 45 min -> 2 h.
    rig.plan(lambda index: -1.0 if index < 2 else 0.0)

    await rig.executor.step()

    assert rig.calls() == [
        ("mypyllant.set_quick_veto", {"entity_id": MV_ZONE_2, "temperature": 20.0, "duration_hours": 2})
    ]
    await eventually(lambda: rig.zone(MV_ZONE_2).override)
    state = next(zone for zone in rig.executor.status().zones if zone.zone_id == MV_ZONE_2)
    assert (state.override_c, state.shift_k) == (20.0, -1.0)
    assert rig.budget.status().sent == 1

    # Back to 0: the own quick veto ends at once, without waiting for the interval.
    await rig.settle()
    rig.plan(lambda _index: 0.0)
    rig.later(5)
    await rig.executor.step()

    assert rig.calls()[-1] == ("mypyllant.cancel_quick_veto", {"entity_id": MV_ZONE_2})
    await eventually(lambda: not rig.zone(MV_ZONE_2).override)
    await rig.settle()
    await rig.executor.step()
    # No outside change: the zone gets no rest, nothing more is sent.
    assert len(rig.calls()) == 2
    assert not any("in Ruhe" in message for message in await messages(rig.log, LogCategory.HEMS))


async def test_changes_wait_for_the_interval_and_a_quick_veto_is_renewed_before_its_end(rig: Rig) -> None:
    rig.plan(lambda _index: -1.0)
    await rig.executor.step()
    await eventually(lambda: rig.zone(MV_ZONE_2).override)
    await rig.settle()

    # A deeper shift 10 min later waits for the 30 min interval.
    rig.plan(lambda _index: -1.5)
    rig.later(10)
    await rig.executor.step()
    assert len(rig.calls()) == 1
    waiting = next(zone for zone in rig.executor.status().zones if zone.zone_id == MV_ZONE_2).waiting
    assert waiting is not None and waiting.startswith("Abstand")

    rig.later(25)
    await rig.executor.step()
    assert rig.calls()[-1][1]["temperature"] == 19.5
    await eventually(lambda: rig.zone(MV_ZONE_2).setpoint is not None and rig.zone(MV_ZONE_2).setpoint.value == 19.5)  # type: ignore[union-attr]
    await rig.settle()

    # No change in the plan: 4 h, renewed 10 min before the end with the same value.
    rig.later(4 * 60 - 12)
    await rig.executor.step()
    assert len(rig.calls()) == 2
    rig.later(5)
    await rig.executor.step()
    assert rig.calls()[-1] == (
        "mypyllant.set_quick_veto",
        {"entity_id": MV_ZONE_2, "temperature": 19.5, "duration_hours": 4},
    )


async def test_an_outside_quick_veto_gets_the_zone_a_rest(rig: Rig) -> None:
    rig.plan(lambda index: 0.0 if index < 2 else -1.0)
    await rig.executor.step()

    # Someone sets a quick veto in myVAILLANT.
    rig.fake.change(MV_ZONE_2, preset_mode="boost", temperature=23.0, quick_veto_end_date_time=None)
    await eventually(lambda: rig.zone(MV_ZONE_2).override)
    rig.plan(lambda _index: -1.0)
    await rig.executor.step()

    assert rig.calls() == []
    state = next(zone for zone in rig.executor.status().zones if zone.zone_id == MV_ZONE_2)
    assert state.rest_reason is not None and "von außen" in state.rest_reason
    assert state.rest_until is not None and state.rest_until - rig.clock.now <= timedelta(hours=3)
    assert any("in Ruhe" in message for message in await messages(rig.log, LogCategory.HEMS))


async def test_a_change_in_the_app_is_a_hand_change(rig: Rig) -> None:
    rig.plan(lambda _index: -1.0)

    await rig.gate.submit(SetZoneOverride(zone_id=MV_ZONE_2, celsius=22.0, hours=3), source=Source.UI)
    await eventually(lambda: rig.zone(MV_ZONE_2).override)
    await rig.executor.step()

    assert len(rig.calls()) == 1
    state = next(zone for zone in rig.executor.status().zones if zone.zone_id == MV_ZONE_2)
    assert state.rest_reason == "in der App von Hand geändert"
    # Counted too: every command to the cloud.
    assert rig.budget.status().sent == 1


async def test_preview_and_demo_send_nothing(rig: Rig) -> None:
    rig.settings_hems(flow_mode=PlannerMode.PREVIEW, zone1_mode=PlannerMode.PREVIEW)
    rig.plan(lambda _index: -1.0)
    rig.hems.would = "off"

    await rig.executor.step()

    assert rig.calls() == []
    assert any("Planer würde senden" in message for message in await messages(rig.log, LogCategory.HEMS))
    assert rig.budget.status().would == 1

    rig.settings_hems(flow_mode=PlannerMode.CONTROL)
    rig.settings.update(lambda current: replace(current, operating_mode=OperatingMode.DEMO))
    rig.later(60)
    await rig.executor.step()
    assert rig.calls() == []
    assert any("Demo" in problem for problem in rig.executor.status().problems)


# --- floor heating off/auto (F1) ---------------------------------------------------------------------


async def test_zone1_goes_off_while_the_heat_pump_stands_and_back_to_auto_at_once(rig: Rig) -> None:
    rig.hems.would = "off"
    # Within 5 min of the zone going to auto (the start) it stays on.
    await rig.executor.step()
    assert rig.calls() == []

    rig.later(6)
    await rig.executor.step()
    assert rig.calls() == [("climate.set_hvac_mode", {"entity_id": MV_ZONE_1, "hvac_mode": "off"})]
    await eventually(lambda: rig.zone(MV_ZONE_1).mode == "off")
    await rig.settle()
    assert [zone.off for zone in rig.executor.status().zones if zone.zone_id == MV_ZONE_1] == [True]
    assert rig.executor.sensors()[0].attributes["ausgeschaltet"] == [MV_ZONE_1]

    rig.hems.would = "auto"
    rig.later(1)
    await rig.executor.step()
    assert rig.calls()[-1] == ("climate.set_hvac_mode", {"entity_id": MV_ZONE_1, "hvac_mode": "auto"})
    await eventually(lambda: rig.zone(MV_ZONE_1).mode == "auto")
    await rig.settle()

    # Off again only 5 min after that auto.
    rig.hems.would = "off"
    rig.later(2)
    await rig.executor.step()
    assert len(rig.calls()) == 2
    # Never off in frost protection.
    rig.hems.frost_active = True
    rig.later(10)
    await rig.executor.step()
    assert len(rig.calls()) == 2
    assert not any("in Ruhe" in message for message in await messages(rig.log, LogCategory.HEMS))


async def test_the_users_automation_still_switching_stops_f1(rig: Rig) -> None:
    rig.hems.would = "auto"
    await rig.executor.step()

    # The heat pump stands (power below 30 W) and the automation switches zone 1 off, twice.
    rig.fake.set(MV_POWER, "5", unit="W", name="Power")
    for mode in ("off", "auto", "off"):
        rig.fake.change(MV_ZONE_1, mode)
        await eventually(lambda mode=mode: rig.zone(MV_ZONE_1).mode == mode)  # type: ignore[misc]
        if mode == "auto":
            rig.fake.set(MV_POWER, "600", unit="W", name="Power")
            await eventually(
                lambda: (
                    rig.device.current is not None and rig.device.current.measurements["power_consumption"].value == 600
                )
            )
        await rig.executor.step()
        if mode == "auto":
            rig.fake.set(MV_POWER, "5", unit="W", name="Power")
            await eventually(
                lambda: (
                    rig.device.current is not None and rig.device.current.measurements["power_consumption"].value == 5
                )
            )

    status = rig.executor.status()
    assert status.zone1_halted
    assert status.automation_switches_today >= 2
    assert any("Läuft die Automation noch?" in message for message in await messages(rig.log, LogCategory.HEMS))
    rig.executor.resume_zone1()
    assert not rig.executor.status().zone1_halted


# --- fallback --------------------------------------------------------------------------------------


async def test_leaving_control_gives_back_only_what_the_executor_set(rig: Rig) -> None:
    rig.plan(lambda _index: -1.0)
    rig.hems.would = "off"
    await rig.executor.step()
    rig.later(6)
    await rig.executor.step()
    await eventually(lambda: rig.zone(MV_ZONE_2).override and rig.zone(MV_ZONE_1).mode == "off")
    await rig.settle()

    rig.settings_hems(mode=PlannerMode.PREVIEW)
    rig.later(1)
    await rig.executor.step()

    assert rig.calls()[-2:] == [
        ("mypyllant.cancel_quick_veto", {"entity_id": MV_ZONE_2}),
        ("climate.set_hvac_mode", {"entity_id": MV_ZONE_1, "hvac_mode": "auto"}),
    ] or rig.calls()[-2:] == [
        ("climate.set_hvac_mode", {"entity_id": MV_ZONE_1, "hvac_mode": "auto"}),
        ("mypyllant.cancel_quick_veto", {"entity_id": MV_ZONE_2}),
    ]


async def test_stopping_releases_and_the_automation_template_names_the_floor_zone(rig: Rig) -> None:
    rig.hems.would = "off"
    await rig.executor.step()
    rig.later(6)
    await rig.executor.step()
    await eventually(lambda: rig.zone(MV_ZONE_1).mode == "off")
    await rig.settle()

    await rig.executor.release("Die App stoppt")

    assert rig.calls()[-1] == ("climate.set_hvac_mode", {"entity_id": MV_ZONE_1, "hvac_mode": "auto"})
    automation = rig.executor.automation()
    assert LIFESIGN_ENTITY in automation
    assert f"entity_id: {MV_ZONE_1}" in automation
    assert "climate.set_hvac_mode" in automation


async def test_at_the_daily_limit_only_the_way_back_goes_out(rig: Rig) -> None:
    rig.settings_hems(cloud_commands_max=10)
    for _ in range(10):
        rig.budget.count()
    rig.plan(lambda _index: -1.0)
    rig.hems.would = "off"
    rig.later(6)

    await rig.executor.step()

    assert rig.calls() == []
    assert rig.budget.level is BudgetLevel.FULL
    # The gate itself: the planner's way back goes out, a new quick veto or another command not.
    with pytest.raises(WriteRejected) as full:
        await rig.gate.submit(SetZoneOverride(zone_id=MV_ZONE_2, celsius=20.0, hours=2), source=Source.PLANNER)
    assert full.value.status == 429
    with pytest.raises(WriteRejected) as foreign:
        await rig.gate.submit(SetHotWaterMode(mode="off"), source=Source.PLANNER)
    assert foreign.value.status == 403
    sent = await rig.gate.submit(SetZoneMode(zone_id=MV_ZONE_1, mode="auto"), source=Source.PLANNER)
    assert sent.message.startswith("Nicht gesendet, bereits eingestellt") or sent.sent


# --- the cloud budget ------------------------------------------------------------------------------


async def test_the_budget_warns_once_stops_at_the_limit_and_starts_again_the_next_day(
    tmp_path: Path, settings_store: SettingsStore, log_store: LogStore
) -> None:
    clock = Clock()
    settings_store.update(lambda current: replace(current, hems=replace(current.hems, cloud_commands_max=10)))
    budget = CloudBudget(tmp_path, settings_store, log_store, clock=clock)

    for _ in range(8):
        budget.count()
    assert budget.status().level is BudgetLevel.WARNING
    assert budget.allows(fallback=False)
    budget.count()
    budget.count()
    assert budget.status().level is BudgetLevel.FULL
    assert not budget.allows(fallback=False)
    assert budget.allows(fallback=True)
    found = [message for message in await messages(log_store, LogCategory.HEMS) if "myVAILLANT" in message]
    assert len(found) == 2

    # Kept over a restart on the same day.
    again = CloudBudget(tmp_path, settings_store, log_store, clock=clock)
    assert again.status().sent == 10
    clock.now += timedelta(days=1)
    assert again.status().sent == 0
    assert again.level is BudgetLevel.OK
