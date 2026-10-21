"""The energy management end to end: Home Assistant inputs, weather, the plan, the API and the sensors."""

from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from aiohttp import web

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from tests.fake_home_assistant import TOKEN, FakeHomeAssistant
from tests.support import eventually
from vaillant_eebus import ha_client as ha_client_module
from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.bridge_client import Backoff, BridgeClient
from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import HomeAssistantClient
from vaillant_eebus.hems.config import HemsSettings, PlannerMode, Tariff, TariffKind, WeatherSource
from vaillant_eebus.hems.days import DayRecorder
from vaillant_eebus.hems.planner import Action, Block, Plan
from vaillant_eebus.hems.service import HemsService
from vaillant_eebus.hems.weather import WeatherForecasts
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import ExportGroup, OperatingMode, SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import WriteGate

BERLIN = timezone(timedelta(hours=2), "CEST")
# Wednesday 21.10.2026, 07:00 local.
NOW = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def ha_client(fake_ha: FakeHomeAssistant, log_store: LogStore) -> AsyncIterator[HomeAssistantClient]:
    async with HomeAssistantClient(
        fake_ha.url, TOKEN, log=log_store, backoff=Backoff(initial=0.05, maximum=0.2)
    ) as client:
        yield client


@pytest.fixture
async def connected(pairing: PairingService, fake_bridge: FakeBridge, state: StateService) -> StateService:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)
    await state.refresh(GATEWAY_SKI)
    return state


def forecast_hours(start: datetime, celsius: float, hours: int = 72) -> list[dict[str, Any]]:
    return [
        {"datetime": (start + timedelta(hours=hour)).isoformat(), "temperature": celsius, "condition": "cloudy"}
        for hour in range(hours)
    ]


def sunny_forecast(day: str) -> dict[str, float]:
    """PV power over a day: 3 kW from 11:00 to 15:00 local."""
    return {
        f"{day}T10:00:00+02:00": 0.0,
        f"{day}T11:00:00+02:00": 3000.0,
        f"{day}T15:00:00+02:00": 3000.0,
        f"{day}T16:00:00+02:00": 0.0,
    }


def configure(fake_ha: FakeHomeAssistant, settings: SettingsStore, **changes: Any) -> None:
    """Home Assistant with PV forecast, house power and weather, and settings that use them."""
    fake_ha.set("sensor.pv_today", "8.5", unit="kWh", attributes={"watts": sunny_forecast("2026-10-21")})
    fake_ha.set("sensor.pv_tomorrow", "9.1", unit="kWh", attributes={"watts": sunny_forecast("2026-10-22")})
    fake_ha.set("sensor.house", "600", unit="W", reported=NOW - timedelta(days=8))
    fake_ha.history["sensor.house"] = [(NOW - timedelta(days=8), "600")]
    fake_ha.set("weather.home", "cloudy", attributes={"temperature_unit": "°C"})
    fake_ha.forecasts["weather.home"] = forecast_hours(NOW.replace(minute=0), 6.0)
    hems = HemsSettings(
        mode=PlannerMode.PREVIEW,
        tariff=Tariff(kind=TariffKind.FIXED, price_ct=30.0, feed_in_ct=8.0),
        pv_forecast_today="sensor.pv_today",
        pv_forecast_tomorrow="sensor.pv_tomorrow",
        house_power="sensor.house",
        weather_source=WeatherSource.HOME_ASSISTANT,
        weather_entity="weather.home",
    )
    settings.update(lambda current: replace(current, hems=replace(hems, **changes)))


def service(
    settings: SettingsStore,
    state: StateService,
    gate: WriteGate,
    energy: EnergyMeter,
    home_assistant: HomeAssistant,
    log: LogStore,
    data_dir: Path,
    clock: Clock,
    *,
    open_meteo_url: str = "http://127.0.0.1:9/never",
) -> HemsService:
    return HemsService(
        settings,
        state,
        gate,
        energy,
        home_assistant,
        log,
        data_dir,
        zone=BERLIN,
        clock=clock,
        open_meteo_url=open_meteo_url,
    )


@pytest.fixture
def ha_with_client(
    settings_store: SettingsStore,
    connected: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
    ha_client: HomeAssistantClient,
) -> HomeAssistant:
    return HomeAssistant(settings_store, connected, energy_meter, log_store, ha_client)


async def follow(home_assistant: HomeAssistant, ha_client: HomeAssistantClient) -> asyncio.Task[None]:
    """Start the client and wait until the planner's entities are read."""
    home_assistant.watch()
    task = asyncio.create_task(ha_client.run())
    await eventually(lambda: home_assistant.entity_state("sensor.pv_tomorrow") is not None)
    return task


# --- the plan from Home Assistant's data ---------------------------------------------


async def test_a_sunny_forecast_plans_hot_water_and_preheating(
    fake_ha: FakeHomeAssistant,
    settings_store: SettingsStore,
    connected: StateService,
    gate: WriteGate,
    energy_meter: EnergyMeter,
    ha_with_client: HomeAssistant,
    ha_client: HomeAssistantClient,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    configure(fake_ha, settings_store)
    hems = service(settings_store, connected, gate, energy_meter, ha_with_client, log_store, tmp_path, clock)
    task = await follow(ha_with_client, ha_client)
    try:
        await hems.plan_now()
    finally:
        task.cancel()

    status = hems.status()
    assert status.problems == []
    assert status.plan is not None
    controls = {(block.control, block.action.value) for block in status.plan.blocks}
    # Zone 2 runs in "on"; Zone 1 in auto is left alone.
    assert controls == {("dhw", "charge"), ("6.1.1", "raise")}
    assert status.plan.notes["5.1.1"] == "Der Planer verschiebt Zonen nur in der Betriebsart Ein."
    assert status.next_action is not None
    assert status.next_action.text == "Zone 2 +1 K"
    log = [entry.message for entry in (await log_store.page(category=LogCategory.HEMS)).entries]
    assert log[0].startswith(
        "Plan: 10:30 bis 15:30 Zone 2 +1 K (PV-Überschuss 2,2 kW erwartet); "
        "11:00 bis 12:30 Warmwasser auf 60 °C und Einmalladung (PV-Überschuss 2,4 kW erwartet);"
    )
    assert fake_ha.service_calls == [
        ("weather.get_forecasts", {"entity_id": "weather.home", "type": "hourly"}, {"return_response": ""})
    ]


async def test_missing_inputs_are_named(
    settings_store: SettingsStore,
    connected: StateService,
    gate: WriteGate,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    settings_store.update(
        lambda current: replace(
            current, hems=HemsSettings(mode=PlannerMode.PREVIEW, tariff=Tariff(kind=TariffKind.DYNAMIC))
        )
    )
    hems = service(settings_store, connected, gate, energy_meter, home_assistant, log_store, tmp_path, clock)

    await hems.plan_now()

    assert hems.status().problems == [
        "Ohne Home Assistant fehlen Preise und Prognosen.",
        "Es ist kein Preis-Sensor gewählt.",
        "Es ist keine PV-Prognose gewählt.",
        "Es ist kein Hausverbrauch gewählt.",
        "Ohne Home Assistant gibt es keine Wetter-Entität.",
    ]


async def test_switched_off_there_is_no_plan(
    settings_store: SettingsStore,
    connected: StateService,
    gate: WriteGate,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    hems = service(settings_store, connected, gate, energy_meter, home_assistant, log_store, tmp_path, clock)

    await hems.plan_now()

    assert hems.status().plan is None
    assert hems.status().mode is PlannerMode.OFF


# --- weather ----------------------------------------------------------------------------


async def test_a_forecast_in_fahrenheit_is_converted(
    fake_ha: FakeHomeAssistant, settings_store: SettingsStore, ha_client: HomeAssistantClient, clock: Clock
) -> None:
    fake_ha.set("weather.us", "sunny", attributes={"temperature_unit": "°F"})
    fake_ha.forecasts["weather.us"] = forecast_hours(NOW, 50.0, hours=2)
    settings_store.update(lambda current: replace(current, hems=HemsSettings(weather_entity="weather.us")))

    forecast = await WeatherForecasts(settings_store, ha_client, clock=clock).forecast()

    assert forecast is not None
    assert forecast.at(NOW + timedelta(minutes=45)) == 10.0


async def test_open_meteo_is_asked_with_the_entered_location_at_most_hourly(
    settings_store: SettingsStore, clock: Clock
) -> None:
    requests: list[dict[str, str]] = []

    async def answer(request: web.Request) -> web.Response:
        requests.append(dict(request.query))
        return web.json_response(
            {"hourly": {"time": ["2026-10-21T05:00", "2026-10-21T06:00"], "temperature_2m": [4.2, None]}}
        )

    app = web.Application()
    app.router.add_get("/v1/forecast", answer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        settings_store.update(
            lambda current: replace(
                current,
                hems=HemsSettings(weather_source=WeatherSource.OPEN_METEO, latitude=52.52, longitude=13.405),
            )
        )
        weather = WeatherForecasts(
            settings_store, None, open_meteo_url=f"http://127.0.0.1:{port}/v1/forecast", clock=clock
        )

        first = await weather.forecast()
        clock.now += timedelta(minutes=30)
        await weather.forecast()
    finally:
        await runner.cleanup()

    assert first is not None
    assert first.hours == {NOW: 4.2}
    assert requests == [
        {
            "latitude": "52.5200",
            "longitude": "13.4050",
            "hourly": "temperature_2m",
            "forecast_days": "3",
            "timezone": "GMT",
        }
    ]


async def test_without_a_location_open_meteo_is_not_asked(settings_store: SettingsStore, clock: Clock) -> None:
    settings_store.update(lambda current: replace(current, hems=HemsSettings(weather_source=WeatherSource.OPEN_METEO)))
    weather = WeatherForecasts(settings_store, None, clock=clock)

    assert await weather.forecast() is None
    assert weather.problem == "Für Open-Meteo fehlt der Standort in den Einstellungen."


def test_weather_get_forecasts_is_the_only_service_the_app_calls() -> None:
    package = Path(ha_client_module.__file__).parent
    services: set[str] = set()
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        services |= {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "/api/services" in node.value
        }

    assert services == {"/api/services/weather/get_forecasts"}


# --- daily values -------------------------------------------------------------------------


def test_the_demand_model_needs_ten_complete_heating_days(
    connected: StateService, settings_store: SettingsStore, tmp_path: Path, clock: Clock
) -> None:
    days: dict[str, Any] = {}
    energy: dict[str, Any] = {}
    for offset in range(1, 13):
        day = (NOW - timedelta(days=offset)).date().isoformat()
        outdoor = 10.0 - offset
        days[day] = {"outdoor_sum": outdoor * 86400, "outdoor_s": 86400, "indoor_sum": 21.0 * 86400, "indoor_s": 86400}
        # 0.5 kWh per kelvin plus 2 kWh for hot water.
        energy[day] = {"wh": (0.5 * (21.0 - outdoor) + 2.0) * 1000, "incomplete": offset == 12}
    (tmp_path / "hems_days.json").write_text(json.dumps({"version": 1, "days": days}), encoding="utf-8")
    (tmp_path / "energy.json").write_text(json.dumps({"version": 1, "days": energy, "last": None}), encoding="utf-8")
    meter = EnergyMeter(connected, tmp_path, clock=clock)
    importer = HomeAssistant(settings_store, connected, meter, LogStore(tmp_path / "log"), None).importer

    recorder = DayRecorder(connected, importer, meter, tmp_path, clock=clock)
    model = recorder.model(15.0)

    assert model is not None
    assert model.days == 11
    assert model.kwh_per_k == pytest.approx(0.5)
    assert model.base_kwh == pytest.approx(2.0)
    assert model.predict(21.0, 1.0) == pytest.approx(12.0)


# --- API and sensors -----------------------------------------------------------------------


def http_client(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log: LogStore,
    energy: EnergyMeter,
    home_assistant: HomeAssistant,
    hems: HemsService,
) -> httpx.AsyncClient:
    app = create_app(
        pairing,
        state,
        gate,
        log,
        energy,
        home_assistant,
        static_dir=None,
        trusted_clients=frozenset({INGRESS_CLIENT}),
        hems=hems,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(INGRESS_CLIENT, 1)), base_url="http://x")


async def test_the_settings_and_the_stage_are_changed_over_the_api(
    pairing: PairingService,
    connected: StateService,
    gate: WriteGate,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
    log_store: LogStore,
    settings_store: SettingsStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    hems = service(settings_store, connected, gate, energy_meter, home_assistant, log_store, tmp_path, clock)
    body = {
        "tariff": {"kind": "fixed", "price_ct": 31.2, "feed_in_ct": 7.9},
        "weather_source": "open_meteo",
        "latitude": 52.52,
        "longitude": 13.405,
        "zones": [{"zone_id": "6.1.1", "up_k": 1.5, "down_k": 0.5}],
        "dhw_max_c": 60,
        "heating_limit_c": 15,
    }
    async with http_client(pairing, connected, gate, log_store, energy_meter, home_assistant, hems) as http:
        saved = await http.put("/api/hems/settings", json=body)
        bad_step = await http.put(
            "/api/hems/settings", json={**body, "zones": [{"zone_id": "6.1.1", "up_k": 0.7, "down_k": 0}]}
        )
        too_hot = await http.put("/api/hems/settings", json={**body, "dhw_max_c": 80})
        stage = await http.post("/api/hems/mode", json={"mode": "preview"})
        status = await http.get("/api/hems")

    assert saved.status_code == 200
    assert saved.json()["settings"]["tariff"]["price_ct"] == 31.2
    assert saved.json()["settings"]["zones"] == [{"zone_id": "6.1.1", "up_k": 1.5, "down_k": 0.5}]
    assert bad_step.status_code == 422
    assert bad_step.json()["detail"] == "Die Verschiebung der Zonen geht nur in Schritten von 0,5 K."
    assert too_hot.status_code == 422
    assert stage.json()["mode"] == "preview"
    assert status.json()["plan"] is not None
    assert settings_store.current.hems.mode is PlannerMode.PREVIEW
    log = [entry.message for entry in (await log_store.page()).entries]
    assert "Planer auf Vorschau: Er plant und zeigt, was er senden würde, sendet aber nichts." in log
    assert "Einstellungen des Energiemanagements geändert." in log


async def test_switching_to_demo_first_returns_to_the_base_values(
    pairing: PairingService,
    connected: StateService,
    bridge_client: BridgeClient,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
    log_store: LogStore,
    settings_store: SettingsStore,
    fake_bridge: FakeBridge,
    tmp_path: Path,
    clock: Clock,
) -> None:
    gate = WriteGate(bridge_client, pairing, connected, settings_store, log_store, clock=lambda: clock.now.timestamp())
    gate.set_mode(OperatingMode.LIVE, confirm=True)
    settings_store.update(lambda current: replace(current, hems=HemsSettings(mode=PlannerMode.CONTROL)))
    hems = service(settings_store, connected, gate, energy_meter, home_assistant, log_store, tmp_path, clock)
    raised = Block("6.1.1", Action.RAISE, NOW, NOW + timedelta(hours=1), 1.0, None, "Test")
    await hems.executor.step(Plan(created_at=NOW, slots=[], blocks=[raised]))
    await connected.refresh(GATEWAY_SKI)

    clock.now += timedelta(seconds=10)
    async with http_client(pairing, connected, gate, log_store, energy_meter, home_assistant, hems) as http:
        response = await http.post("/api/mode", json={"mode": "demo"})

    assert response.json() == {"mode": "demo"}
    values = [getattr(request, "value_celsius", None) for _name, request in fake_bridge.device.write_calls]
    assert values == [24.0, 23.0]
    assert gate.mode is OperatingMode.DEMO


async def test_the_planner_is_reported_as_sensors(
    fake_ha: FakeHomeAssistant,
    settings_store: SettingsStore,
    connected: StateService,
    gate: WriteGate,
    energy_meter: EnergyMeter,
    ha_with_client: HomeAssistant,
    ha_client: HomeAssistantClient,
    log_store: LogStore,
    tmp_path: Path,
    clock: Clock,
) -> None:
    configure(fake_ha, settings_store)
    hems = service(settings_store, connected, gate, energy_meter, ha_with_client, log_store, tmp_path, clock)
    task = await follow(ha_with_client, ha_client)
    try:
        await hems.plan_now()
        sensors = {sensor.entity_id: sensor for sensor in hems.sensors()}
    finally:
        task.cancel()

    assert ExportGroup.HEMS in settings_store.current.export
    assert sensors["sensor.vaillant_eebus_hems_status"].state == "Vorschau"
    assert sensors["sensor.vaillant_eebus_hems_next_action"].state == "Zone 2 +1 K"
    assert sensors["sensor.vaillant_eebus_hems_plan"].state == "4"
    assert sensors["sensor.vaillant_eebus_hems_demand_tomorrow"].state == "unavailable"
