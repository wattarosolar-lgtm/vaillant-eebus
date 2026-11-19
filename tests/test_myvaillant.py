"""The connection myVAILLANT: reading and controlling through the integration's entities in Home Assistant."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import itertools
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from tests.fake_home_assistant import (
    MV_HOT_WATER,
    MV_OUTDOOR,
    MV_POWER,
    MV_ZONE_1,
    MV_ZONE_2,
    TOKEN,
    FakeHomeAssistant,
)
from tests.support import eventually
from vaillant_eebus import ha_client as ha_client_module
from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.bridge_client import Backoff, BridgeClient
from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import DEVICE_SERVICES, EntityState, HomeAssistantClient
from vaillant_eebus.hems.config import PlannerMode
from vaillant_eebus.hems.executor import Executor
from vaillant_eebus.hems.planner import Action, Block, Plan
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogStore
from vaillant_eebus.myvaillant import MyVaillantLink, MyVaillantState, discover, zone_label
from vaillant_eebus.pairing import ConnectionState, PairingService
from vaillant_eebus.settings import Connection, MyVaillantEntities, OperatingMode, SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import (
    EndZoneOverride,
    SetConsumptionLimit,
    SetHotWaterBoost,
    SetHotWaterMode,
    SetHotWaterSetpoint,
    SetZoneMode,
    SetZoneOverride,
    SetZoneSetpoint,
    WriteGate,
    WriteNotExecuted,
    WriteOutcomeUnknown,
    WriteRejected,
)

CHOSEN = MyVaillantEntities(
    zones=(MV_ZONE_1, MV_ZONE_2),
    hot_water=MV_HOT_WATER,
    outdoor_temperature=MV_OUTDOOR,
    power=MV_POWER,
)


@pytest.fixture
def chosen(settings_store: SettingsStore) -> SettingsStore:
    settings_store.update(lambda settings: replace(settings, connection=Connection.MYVAILLANT, myvaillant=CHOSEN))
    return settings_store


@pytest.fixture
async def ha(fake_ha: FakeHomeAssistant, log_store: LogStore) -> AsyncIterator[HomeAssistantClient]:
    fake_ha.add_myvaillant()
    async with HomeAssistantClient(
        fake_ha.url, TOKEN, log=log_store, backoff=Backoff(initial=0.05, maximum=0.2), refresh_interval=60
    ) as client:
        yield client


@pytest.fixture
async def device(chosen: SettingsStore, ha: HomeAssistantClient) -> AsyncIterator[MyVaillantState]:
    state = MyVaillantState(chosen, available=True)
    ha.add_listener(state)
    ha.watch(state.watched())
    task = asyncio.create_task(ha.run())
    await eventually(lambda: state.connection_state() is ConnectionState.CONNECTED)
    yield state
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def make_gate(
    bridge_client: BridgeClient,
    pairing: PairingService,
    device: MyVaillantState,
    settings: SettingsStore,
    log: LogStore,
    ha: HomeAssistantClient,
    *,
    live: bool,
) -> WriteGate:
    # Every call of the clock is 10 s later: the limit per control never gets in the way.
    ticks = itertools.count(step=10.0)
    gate = WriteGate(
        bridge_client,
        pairing,
        device,
        settings,
        log,
        link=MyVaillantLink(ha, device, settings),
        clock=lambda: next(ticks),
    )
    if live:
        gate.set_mode(OperatingMode.LIVE, confirm=True)
    return gate


@pytest.fixture
def live(
    bridge_client: BridgeClient,
    pairing: PairingService,
    device: MyVaillantState,
    chosen: SettingsStore,
    log_store: LogStore,
    ha: HomeAssistantClient,
) -> WriteGate:
    return make_gate(bridge_client, pairing, device, chosen, log_store, ha, live=True)


def device_calls(fake_ha: FakeHomeAssistant) -> list[tuple[str, dict[str, object]]]:
    return [
        (service, body)
        for service, body, _query in fake_ha.service_calls
        if tuple(service.split(".", 1)) in DEVICE_SERVICES
    ]


async def messages(log: LogStore) -> list[str]:
    return [entry.message for entry in (await log.page()).entries]


async def wait_logged(log: LogStore, text: str) -> None:
    # Polls on purpose: the protocol has no event to await.
    async with asyncio.timeout(5):
        while text not in await messages(log):  # noqa: ASYNC110
            await asyncio.sleep(0.01)


# --- finding and reading the entities -------------------------------------------------


def _entity(entity_id: str, state: str = "on", **attributes: object) -> EntityState:
    return EntityState(entity_id=entity_id, state=state, attributes=attributes, last_reported=None)


def test_discover_suggests_the_integrations_entities() -> None:
    states = [
        _entity(MV_ZONE_2, "heat_cool", time_program_heating={}),
        _entity(MV_ZONE_1, "auto", quick_veto_end_date_time=None),
        _entity("climate.wohnzimmer_thermostat", "heat"),
        _entity(MV_HOT_WATER, "Manual", operation_list=["Manual", "Cylinder Boost"]),
        _entity(MV_OUTDOOR, "4.5", unit_of_measurement="°C"),
        _entity("sensor.wohnzimmer_temperatur", "21.0", unit_of_measurement="°C"),
        _entity(MV_POWER, "850", unit_of_measurement="W"),
    ]

    assert discover(states) == CHOSEN


def test_zone_names_are_shortened() -> None:
    assert zone_label(_entity(MV_ZONE_1, friendly_name="Zuhause Zone 1 (Circuit 0) Climate"), 1) == "Zone 1"
    assert zone_label(_entity(MV_ZONE_1, friendly_name="Wohnen"), 1) == "Wohnen"
    assert zone_label(None, 2) == "Zone 2"


async def test_the_state_comes_from_the_chosen_entities(device: MyVaillantState) -> None:
    heat_pump = device.current
    assert heat_pump is not None and heat_pump.available and heat_pump.connected
    first, second = heat_pump.zones
    assert (first.id, first.label, first.mode, first.available_modes) == (
        MV_ZONE_1,
        "Zone 1",
        "auto",
        ["off", "on", "auto"],
    )
    assert first.setpoint is not None
    assert (first.setpoint.value, first.setpoint.min, first.setpoint.max, first.setpoint.step) == (21.5, 7, 30, 0.5)
    assert first.room_temperature is not None and first.room_temperature.value == 21.0
    assert first.room_temperature.source == "myvaillant"
    assert (second.label, second.mode, second.setpoint and second.setpoint.value) == ("Zone 2", "on", 23.0)
    water = heat_pump.hot_water
    assert water is not None and water.setpoint is not None
    assert (water.mode, water.setpoint.value, water.setpoint.step, water.boost, water.boost_writable) == (
        "auto",
        50,
        1.0,
        "inactive",
        True,
    )
    assert {name: reading.value for name, reading in heat_pump.measurements.items()} == {
        "outdoor_temperature": 4.5,
        "power_consumption": 850.0,
        "dhw_temperature": 47.5,
    }
    assert heat_pump.compressor_active is True
    assert heat_pump.captured_at is not None


async def test_a_quick_veto_shows_as_an_override_until_its_end(
    device: MyVaillantState, fake_ha: FakeHomeAssistant
) -> None:
    end = datetime(2026, 11, 19, 15, 30, tzinfo=UTC)
    fake_ha.change(MV_ZONE_1, preset_mode="boost", temperature=23.0, quick_veto_end_date_time=end.isoformat())

    await eventually(lambda: device.current is not None and device.current.zones[0].override)
    zone = device.current.zones[0] if device.current else None
    assert zone is not None and zone.override_until == end and zone.setpoint and zone.setpoint.value == 23.0


async def test_the_one_time_charge_keeps_the_hot_water_mode(
    device: MyVaillantState, fake_ha: FakeHomeAssistant
) -> None:
    fake_ha.change(MV_HOT_WATER, "Cylinder Boost")

    await eventually(
        lambda: (
            device.current is not None
            and device.current.hot_water is not None
            and device.current.hot_water.boost == "running"
        )
    )
    assert device.current is not None and device.current.hot_water is not None
    assert device.current.hot_water.mode == "auto"


async def test_power_in_kilowatts_counts_in_watts(device: MyVaillantState, fake_ha: FakeHomeAssistant) -> None:
    fake_ha.set(MV_POWER, "1.2", unit="kW")

    await eventually(
        lambda: (
            device.current is not None
            and device.current.measurements.get("power_consumption") is not None
            and device.current.measurements["power_consumption"].value == 1200
        )
    )


async def test_an_unavailable_zone_means_not_connected(device: MyVaillantState, fake_ha: FakeHomeAssistant) -> None:
    fake_ha.set(MV_ZONE_1, "unavailable")

    await eventually(lambda: device.connection_state() is ConnectionState.DISCONNECTED)
    assert device.current is not None and device.current.zones[0].setpoint is None


def test_without_chosen_entities_there_is_no_heat_pump(settings_store: SettingsStore) -> None:
    device = MyVaillantState(settings_store, available=True)

    assert device.current is None
    assert device.connection_state() is ConnectionState.NOT_CONFIGURED


def test_outside_home_assistant_the_heat_pump_is_unavailable(chosen: SettingsStore) -> None:
    device = MyVaillantState(chosen, available=False)

    assert device.current is not None and not device.current.available
    assert device.connection_state() is ConnectionState.HA_UNAVAILABLE


# --- writing through the services ------------------------------------------------------


async def test_demo_mode_calls_no_service(
    bridge_client: BridgeClient,
    pairing: PairingService,
    device: MyVaillantState,
    chosen: SettingsStore,
    log_store: LogStore,
    ha: HomeAssistantClient,
    fake_ha: FakeHomeAssistant,
) -> None:
    gate = make_gate(bridge_client, pairing, device, chosen, log_store, ha, live=False)

    for command in (
        SetZoneSetpoint(zone_id=MV_ZONE_2, celsius=22.0),
        SetZoneSetpoint(zone_id=MV_ZONE_1, celsius=23.0),
        SetZoneMode(zone_id=MV_ZONE_2, mode="auto"),
        SetZoneOverride(zone_id=MV_ZONE_1, celsius=22.0, hours=2.0),
        SetHotWaterSetpoint(celsius=55),
        SetHotWaterMode(mode="on"),
        SetHotWaterBoost(active=True),
    ):
        result = await gate.submit(command)
        assert not result.sent and result.message.startswith("Würde senden")

    assert device_calls(fake_ha) == []


async def test_a_manual_zone_gets_its_setpoint(live: WriteGate, fake_ha: FakeHomeAssistant) -> None:
    result = await live.submit(SetZoneSetpoint(zone_id=MV_ZONE_2, celsius=22.0))

    assert result.sent
    assert device_calls(fake_ha) == [("climate.set_temperature", {"entity_id": MV_ZONE_2, "temperature": 22.0})]


async def test_a_setpoint_in_auto_starts_a_temporary_override(
    live: WriteGate, device: MyVaillantState, fake_ha: FakeHomeAssistant, log_store: LogStore
) -> None:
    await live.submit(SetZoneSetpoint(zone_id=MV_ZONE_1, celsius=23.0))

    assert device_calls(fake_ha) == [("climate.set_temperature", {"entity_id": MV_ZONE_1, "temperature": 23.0})]
    await eventually(lambda: device.current is not None and device.current.zones[0].override)
    assert "Gesendet: Zone 1: Solltemperatur 23,0 °C als zeitweise Übersteuerung" in await messages(log_store)


async def test_a_switched_off_zone_takes_no_setpoint(
    live: WriteGate, device: MyVaillantState, fake_ha: FakeHomeAssistant
) -> None:
    fake_ha.change(MV_ZONE_2, "off")
    await eventually(lambda: device.current is not None and device.current.zones[1].mode == "off")

    with pytest.raises(WriteRejected) as rejected:
        await live.submit(SetZoneSetpoint(zone_id=MV_ZONE_2, celsius=22.0))

    assert rejected.value.status == 409
    assert device_calls(fake_ha) == []


async def test_modes_and_hot_water_use_the_entities_own_names(
    live: WriteGate, device: MyVaillantState, fake_ha: FakeHomeAssistant
) -> None:
    def water_shows(operation: str) -> bool:
        return fake_ha.states[MV_HOT_WATER]["state"] == operation and (
            device.current is not None
            and device.current.hot_water is not None
            and device.current.hot_water.boost == ("running" if operation == "Cylinder Boost" else "inactive")
        )

    await live.submit(SetZoneMode(zone_id=MV_ZONE_1, mode="on"))
    await live.submit(SetHotWaterSetpoint(celsius=55))
    await live.submit(SetHotWaterMode(mode="on"))
    await eventually(lambda: water_shows("Manual"))
    await live.submit(SetHotWaterBoost(active=True))
    await eventually(lambda: water_shows("Cylinder Boost"))
    await live.submit(SetHotWaterBoost(active=False))

    assert device_calls(fake_ha) == [
        ("climate.set_hvac_mode", {"entity_id": MV_ZONE_1, "hvac_mode": "heat_cool"}),
        ("water_heater.set_temperature", {"entity_id": MV_HOT_WATER, "temperature": 55}),
        ("water_heater.set_operation_mode", {"entity_id": MV_HOT_WATER, "operation_mode": "Manual"}),
        ("water_heater.set_operation_mode", {"entity_id": MV_HOT_WATER, "operation_mode": "Cylinder Boost"}),
        # Choosing the mode again ends the charge.
        ("water_heater.set_operation_mode", {"entity_id": MV_HOT_WATER, "operation_mode": "Manual"}),
    ]


async def test_the_planners_override_lasts_its_hours_and_can_be_ended(
    live: WriteGate, device: MyVaillantState, fake_ha: FakeHomeAssistant
) -> None:
    await live.submit(SetZoneOverride(zone_id=MV_ZONE_1, celsius=22.5, hours=2.0))
    await eventually(lambda: device.current is not None and device.current.zones[0].override)
    await live.submit(EndZoneOverride(zone_id=MV_ZONE_1))

    assert device_calls(fake_ha) == [
        ("mypyllant.set_quick_veto", {"entity_id": MV_ZONE_1, "temperature": 22.5, "duration_hours": 2.0}),
        ("mypyllant.cancel_quick_veto", {"entity_id": MV_ZONE_1}),
    ]
    await eventually(lambda: device.current is not None and not device.current.zones[0].override)


async def test_an_override_needs_a_zone_in_auto(live: WriteGate, fake_ha: FakeHomeAssistant) -> None:
    with pytest.raises(WriteRejected) as rejected:
        await live.submit(SetZoneOverride(zone_id=MV_ZONE_2, celsius=24.0, hours=2.0))

    assert rejected.value.status == 409
    assert device_calls(fake_ha) == []


async def test_eebus_has_no_override(
    gate: WriteGate, pairing: PairingService, fake_bridge: FakeBridge, state: StateService
) -> None:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)
    await state.refresh(GATEWAY_SKI)

    with pytest.raises(WriteRejected) as rejected:
        await gate.submit(SetZoneOverride(zone_id="5.1.1", celsius=22.0, hours=2.0))

    assert "nur mit der Anbindung myVAILLANT" in rejected.value.message
    assert fake_bridge.device.write_calls == []


async def test_power_limits_and_the_compressor_need_eebus(live: WriteGate, fake_ha: FakeHomeAssistant) -> None:
    with pytest.raises(WriteRejected) as rejected:
        await live.submit(SetConsumptionLimit(watts=2000, duration_s=3600, active=True))

    assert rejected.value.status == 409 and "EEBUS" in rejected.value.message
    assert device_calls(fake_ha) == []


async def test_a_zone_that_is_not_chosen_is_refused(live: WriteGate, fake_ha: FakeHomeAssistant) -> None:
    with pytest.raises(WriteRejected) as rejected:
        await live.submit(SetZoneSetpoint(zone_id="climate.nachbar", celsius=21.0))

    assert rejected.value.status == 404
    assert device_calls(fake_ha) == []


async def test_a_refused_call_did_not_arrive(live: WriteGate, fake_ha: FakeHomeAssistant, log_store: LogStore) -> None:
    fake_ha.service_status["climate.set_temperature"] = 400

    with pytest.raises(WriteNotExecuted):
        await live.submit(SetZoneSetpoint(zone_id=MV_ZONE_2, celsius=22.0))

    assert live.pending == []
    assert "Nicht ausgeführt: Zone 2: Solltemperatur 22,0 °C (HTTP 400)" in await messages(log_store)


async def test_an_error_of_the_cloud_leaves_the_outcome_open(
    live: WriteGate, fake_ha: FakeHomeAssistant, log_store: LogStore
) -> None:
    fake_ha.service_status["climate.set_temperature"] = 500

    with pytest.raises(WriteOutcomeUnknown) as unknown:
        await live.submit(SetZoneSetpoint(zone_id=MV_ZONE_2, celsius=22.0))

    assert unknown.value.code_name == "HTTP 500"
    assert [write.value for write in live.pending] == [22.0]
    assert any(message.startswith("Ergebnis unbekannt: Zone 2") for message in await messages(log_store))


async def test_the_entity_confirms_a_sent_value(live: WriteGate, log_store: LogStore) -> None:
    follower = asyncio.create_task(live.run())
    try:
        await live.submit(SetZoneSetpoint(zone_id=MV_ZONE_2, celsius=22.5))
        await eventually(lambda: live.pending == [])
    finally:
        follower.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follower

    assert "Bestätigt vom Gerät: Zone 2: Solltemperatur 22,5 °C" in await messages(log_store)


async def test_the_time_program_moving_on_is_no_change_at_the_device(
    live: WriteGate, device: MyVaillantState, fake_ha: FakeHomeAssistant, log_store: LogStore
) -> None:
    changed = "Am Gerät geändert: Zone 2: Solltemperatur 22,0 °C (vorher Solltemperatur 23,0 °C)"
    follower = asyncio.create_task(live.run())
    try:
        # The gate learns the values first; it reports changes from then on.
        await asyncio.sleep(0.05)
        device.republish()
        await asyncio.sleep(0.05)
        fake_ha.change(MV_ZONE_1, temperature=19.0)
        fake_ha.change(MV_ZONE_2, temperature=22.0)
        await wait_logged(log_store, changed)
    finally:
        follower.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follower

    assert not any("Zone 1: Solltemperatur 19" in message for message in await messages(log_store))


# --- the client may call only the device services --------------------------------------


async def test_other_services_and_entities_are_refused(ha: HomeAssistantClient) -> None:
    with pytest.raises(ValueError, match="not a service"):
        await ha.call_device_service("light", "turn_on", "light.kueche", {})
    with pytest.raises(ValueError, match="may not target"):
        await ha.call_device_service("climate", "set_temperature", "water_heater.speicher", {"temperature": 50})


def test_the_device_services_are_exactly_these() -> None:
    assert set(DEVICE_SERVICES) == {
        ("climate", "set_temperature"),
        ("climate", "set_hvac_mode"),
        ("water_heater", "set_temperature"),
        ("water_heater", "set_operation_mode"),
        ("mypyllant", "set_quick_veto"),
        ("mypyllant", "cancel_quick_veto"),
    }


def test_only_the_write_gate_controls_the_heat_pump_through_home_assistant() -> None:
    package = Path(ha_client_module.__file__).parent
    callers: dict[str, set[str]] = {}
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        used = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in ("call_device_service", "send_command")
        }
        if used:
            callers[source.name] = used

    assert callers == {"myvaillant.py": {"call_device_service"}, "write_gate.py": {"send_command"}}


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def restarts() -> list[int]:
    return []


@pytest.fixture
async def http(
    bridge_client: BridgeClient,
    pairing: PairingService,
    device: MyVaillantState,
    chosen: SettingsStore,
    log_store: LogStore,
    ha: HomeAssistantClient,
    tmp_path: Path,
    restarts: list[int],
) -> AsyncIterator[httpx.AsyncClient]:
    gate = make_gate(bridge_client, pairing, device, chosen, log_store, ha, live=False)
    energy = EnergyMeter(device, tmp_path)
    home_assistant = HomeAssistant(chosen, device, energy, log_store, ha)
    app = create_app(
        pairing,
        device,
        gate,
        log_store,
        energy,
        home_assistant,
        static_dir=None,
        trusted_clients=frozenset({INGRESS_CLIENT}),
        restart=lambda: restarts.append(1),
    )
    transport = httpx.ASGITransport(app=app, client=(INGRESS_CLIENT, 51234))
    async with httpx.AsyncClient(transport=transport, base_url="http://ingress") as client:
        yield client


async def test_the_state_says_where_the_values_come_from(http: httpx.AsyncClient) -> None:
    state = (await http.get("/api/state")).json()

    assert (state["link"], state["connection"]) == ("myvaillant", "connected")
    assert [zone["id"] for zone in state["heat_pump"]["zones"]] == [MV_ZONE_1, MV_ZONE_2]


async def test_the_zone_of_an_entity_takes_a_setpoint_over_the_api(
    http: httpx.AsyncClient, fake_ha: FakeHomeAssistant
) -> None:
    response = await http.post(f"/api/zones/{MV_ZONE_2}/setpoint", json={"value": 22.0, "operating_mode": "demo"})

    assert response.status_code == 200 and response.json()["sent"] is False
    assert device_calls(fake_ha) == []


async def test_pairing_and_dump_need_eebus(http: httpx.AsyncClient) -> None:
    for response in (
        await http.get("/api/pairing"),
        await http.post("/api/pairing/register", json={"ski": GATEWAY_SKI}),
        await http.get("/api/diagnostics/dump"),
    ):
        assert response.status_code == 409


async def test_discover_and_connection_over_the_api(http: httpx.AsyncClient) -> None:
    found = (await http.get("/api/myvaillant/discover")).json()
    status = (await http.get("/api/connection")).json()

    assert found == {
        "zones": [MV_ZONE_1, MV_ZONE_2],
        "hot_water": MV_HOT_WATER,
        "outdoor_temperature": MV_OUTDOOR,
        "power": MV_POWER,
    }
    assert (status["connection"], status["active"], status["state"], status["restarting"]) == (
        "myvaillant",
        "myvaillant",
        "connected",
        False,
    )


async def test_switching_to_eebus_restarts_in_demo_mode(
    http: httpx.AsyncClient, chosen: SettingsStore, restarts: list[int], log_store: LogStore
) -> None:
    await http.post("/api/mode", json={"mode": "live", "confirm": True})

    response = await http.put("/api/connection", json={"connection": "eebus", "myvaillant": {"zones": [MV_ZONE_1]}})

    assert response.json()["restarting"] is True
    assert restarts == [1]
    assert chosen.current.connection is Connection.EEBUS
    assert chosen.current.operating_mode is OperatingMode.DEMO
    assert chosen.current.myvaillant == MyVaillantEntities(zones=(MV_ZONE_1,))
    logged = await messages(log_store)
    assert "Anbindung: EEBUS." in logged and "Demo-Betrieb eingeschaltet: Änderungen werden nicht gesendet." in logged


async def test_other_entities_apply_without_a_restart(
    http: httpx.AsyncClient, chosen: SettingsStore, restarts: list[int], device: MyVaillantState
) -> None:
    response = await http.put(
        "/api/connection", json={"connection": "myvaillant", "myvaillant": {"zones": [MV_ZONE_2]}}
    )

    assert response.status_code == 200 and response.json()["restarting"] is False
    assert restarts == []
    assert device.current is not None and [zone.id for zone in device.current.zones] == [MV_ZONE_2]


async def test_myvaillant_needs_a_zone_or_the_hot_water(http: httpx.AsyncClient) -> None:
    response = await http.put("/api/connection", json={"connection": "myvaillant", "myvaillant": {}})

    assert response.status_code == 422


async def test_switching_to_myvaillant_outside_home_assistant_is_refused(
    client_eebus: httpx.AsyncClient,
) -> None:
    response = await client_eebus.put(
        "/api/connection", json={"connection": "myvaillant", "myvaillant": {"zones": [MV_ZONE_1]}}
    )

    assert response.status_code == 409


@pytest.fixture
async def client_eebus(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log_store: LogStore,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        pairing,
        state,
        gate,
        log_store,
        energy_meter,
        home_assistant,
        static_dir=None,
        trusted_clients=frozenset({INGRESS_CLIENT}),
    )
    transport = httpx.ASGITransport(app=app, client=(INGRESS_CLIENT, 51234))
    async with httpx.AsyncClient(transport=transport, base_url="http://ingress") as client:
        yield client


async def test_choosing_eebus_in_the_wizard_needs_no_restart(
    client_eebus: httpx.AsyncClient, settings_store: SettingsStore
) -> None:
    response = await client_eebus.put("/api/connection", json={"connection": "eebus"})

    assert response.json()["restarting"] is False
    assert settings_store.current.connection is Connection.EEBUS


# --- the planner in auto: temporary overrides ----------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def control_mode(settings: SettingsStore, mode: PlannerMode) -> None:
    settings.update(lambda current: replace(current, hems=replace(current.hems, mode=mode)))


def raise_plan(clock: Clock, zone: str = MV_ZONE_1, hours: float = 2.0) -> Plan:
    block = Block(
        control=zone,
        action=Action.RAISE,
        start=clock.now,
        end=clock.now + timedelta(hours=hours),
        shift_k=1.0,
        target_c=None,
        reason="PV-Überschuss 2,0 kW erwartet",
    )
    return Plan(created_at=clock.now, slots=[], blocks=[block])


@pytest.fixture
def planner(
    live: WriteGate, device: MyVaillantState, chosen: SettingsStore, log_store: LogStore, tmp_path: Path, clock: Clock
) -> Executor:
    control_mode(chosen, PlannerMode.CONTROL)
    return Executor(live, device, chosen, log_store, tmp_path, clock=clock)


def overridden(device: MyVaillantState) -> bool:
    return device.current is not None and device.current.zones[0].override


async def test_the_planner_overrides_a_zone_in_auto_for_the_block(
    planner: Executor, device: MyVaillantState, fake_ha: FakeHomeAssistant, clock: Clock
) -> None:
    await planner.step(raise_plan(clock))

    assert device_calls(fake_ha) == [
        ("mypyllant.set_quick_veto", {"entity_id": MV_ZONE_1, "temperature": 22.5, "duration_hours": 2.0})
    ]
    assert planner.shifted() == {MV_ZONE_1: 22.5}
    assert planner.bases()[MV_ZONE_1] == 21.5


async def test_the_override_ends_by_itself_with_the_block(
    planner: Executor, device: MyVaillantState, fake_ha: FakeHomeAssistant, clock: Clock
) -> None:
    plan = raise_plan(clock)
    await planner.step(plan)
    await eventually(lambda: overridden(device))

    await planner.step(plan)
    clock.now += timedelta(hours=2)
    await planner.step(plan)

    assert [service for service, _body in device_calls(fake_ha)] == ["mypyllant.set_quick_veto"]


async def test_a_block_that_ends_early_ends_the_override(
    planner: Executor, device: MyVaillantState, fake_ha: FakeHomeAssistant, clock: Clock
) -> None:
    await planner.step(raise_plan(clock))
    await eventually(lambda: overridden(device))

    clock.now += timedelta(minutes=30)
    await planner.step(None)

    assert device_calls(fake_ha)[-1] == ("mypyllant.cancel_quick_veto", {"entity_id": MV_ZONE_1})
    assert planner.shifted() == {}
    await eventually(lambda: not overridden(device))
    await planner.step(None)
    assert planner.bases()[MV_ZONE_1] == 21.5


async def test_release_ends_the_override(
    planner: Executor, device: MyVaillantState, fake_ha: FakeHomeAssistant, clock: Clock, log_store: LogStore
) -> None:
    await planner.step(raise_plan(clock))
    await eventually(lambda: overridden(device))

    await planner.release("Die App wird beendet")

    assert device_calls(fake_ha)[-1] == ("mypyllant.cancel_quick_veto", {"entity_id": MV_ZONE_1})
    assert "Gesendet: Zone 1: Übersteuerung beenden (Planer)" in await messages(log_store)


async def test_an_override_set_by_hand_pauses_the_zone(
    planner: Executor, device: MyVaillantState, fake_ha: FakeHomeAssistant, clock: Clock, log_store: LogStore
) -> None:
    await planner.step(None)
    fake_ha.change(MV_ZONE_1, preset_mode="boost", temperature=24.0)
    await eventually(lambda: overridden(device))

    await planner.step(raise_plan(clock))

    assert device_calls(fake_ha) == []
    assert "Planer: Zone 1 von Hand übersteuert; der Planer wartet bis zum Blockende." in await messages(log_store)


async def test_ending_the_planners_override_by_hand_pauses_the_zone(
    planner: Executor, device: MyVaillantState, fake_ha: FakeHomeAssistant, clock: Clock, log_store: LogStore
) -> None:
    plan = raise_plan(clock)
    await planner.step(plan)
    await eventually(lambda: overridden(device))
    await planner.step(plan)

    fake_ha.change(MV_ZONE_1, preset_mode="none", temperature=21.5, quick_veto_end_date_time=None)
    await eventually(lambda: not overridden(device))
    clock.now += timedelta(minutes=40)
    await planner.step(plan)

    assert [service for service, _body in device_calls(fake_ha)] == ["mypyllant.set_quick_veto"]
    assert "Planer: Zone 1 Übersteuerung von Hand beendet; der Planer wartet bis zum Blockende." in await messages(
        log_store
    )


async def test_preview_names_the_override_and_sends_nothing(
    planner: Executor, chosen: SettingsStore, fake_ha: FakeHomeAssistant, clock: Clock, log_store: LogStore
) -> None:
    control_mode(chosen, PlannerMode.PREVIEW)

    await planner.step(raise_plan(clock))

    assert device_calls(fake_ha) == []
    assert "Planer würde senden: Zone 1: Übersteuerung 22,5 °C für 2 h" in await messages(log_store)


async def test_the_planners_override_survives_a_restart(
    planner: Executor,
    live: WriteGate,
    device: MyVaillantState,
    chosen: SettingsStore,
    log_store: LogStore,
    fake_ha: FakeHomeAssistant,
    tmp_path: Path,
    clock: Clock,
) -> None:
    plan = raise_plan(clock)
    await planner.step(plan)
    await eventually(lambda: overridden(device))

    again = Executor(live, device, chosen, log_store, tmp_path, clock=clock)
    clock.now += timedelta(minutes=10)
    await again.step(plan)
    await again.release("Die App wird beendet")

    assert [service for service, _body in device_calls(fake_ha)] == [
        "mypyllant.set_quick_veto",
        "mypyllant.cancel_quick_veto",
    ]


async def test_a_zone_in_on_is_shifted_as_with_eebus(
    planner: Executor, fake_ha: FakeHomeAssistant, clock: Clock
) -> None:
    await planner.step(raise_plan(clock, zone=MV_ZONE_2))

    assert device_calls(fake_ha) == [("climate.set_temperature", {"entity_id": MV_ZONE_2, "temperature": 24.0})]
