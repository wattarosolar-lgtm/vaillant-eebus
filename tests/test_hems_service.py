"""The energy management end to end with the fake Home Assistant: statistics, forecast, plan, API, sensors."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.fake_home_assistant import MV_HOT_WATER, MV_OUTDOOR, MV_POWER, MV_ZONE_1, MV_ZONE_2, TOKEN, FakeHomeAssistant
from tests.support import eventually
from vaillant_eebus import ha_client as ha_client_module
from vaillant_eebus import hems as hems_package
from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.bridge_client import Backoff, BridgeClient
from vaillant_eebus.energy import EnergyMeter
from vaillant_eebus.ha_client import HomeAssistantClient
from vaillant_eebus.hems.config import HemsSettings, PlannerMode, WeatherSource, ZoneConfig, ZoneKind
from vaillant_eebus.hems.rules import Part
from vaillant_eebus.hems.service import HemsService
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.myvaillant import MyVaillantLink, MyVaillantState
from vaillant_eebus.pairing import ConnectionState, PairingService
from vaillant_eebus.room_data import RoomDetails
from vaillant_eebus.settings import Connection, ExportGroup, MyVaillantEntities, Room, SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import WriteGate

HEAT = "sensor.heat_heating"
HEAT_DHW = "sensor.heat_dhw"
ROOM_A = "sensor.room_a_temperature"
ROOM_A_HUMIDITY = "sensor.room_a_humidity"
ROOM_A_VALVE = "sensor.trv_room_a_position"
ROOM_A_TRV = "climate.trv_room_a"
BATH_TRV = "climate.trv_bath"
FLOW_2 = "sensor.circuit_1_flow_temperature"


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


@dataclass
class App:
    fake: FakeHomeAssistant
    settings: SettingsStore
    device: MyVaillantState
    home: HomeAssistant
    hems: HemsService
    log: LogStore
    clock: Clock
    http: httpx.AsyncClient


def statistics_rows(now: datetime, days: int, value: Any) -> list[dict[str, Any]]:
    start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return [
        {"start": start + timedelta(hours=hour), **value(start + timedelta(hours=hour))} for hour in range(days * 24)
    ]


def open_meteo_hours(now: datetime, celsius: float = 4.0) -> dict[str, list[Any]]:
    start = int((now - timedelta(days=7)).replace(minute=0, second=0, microsecond=0).timestamp())
    hours = 10 * 24
    return {
        "time": [start + 3600 * hour for hour in range(hours)],
        "temperature_2m": [celsius] * hours,
        "relative_humidity_2m": [85.0] * hours,
        "dew_point_2m": [1.5] * hours,
        "wind_speed_10m": [3.0] * hours,
        "shortwave_radiation": [0.0] * hours,
        "cloud_cover": [90.0] * hours,
        "precipitation": [0.0] * hours,
        "snowfall": [0.0] * hours,
    }


def prepare(fake: FakeHomeAssistant, now: datetime) -> None:
    """Rooms, thermostats, statistics of 20 days and Open-Meteo, all invented."""
    fake.add_myvaillant()
    fake.set(ROOM_A, "21.0", unit="°C", name="Raum A Temperatur")
    fake.set(ROOM_A_HUMIDITY, "48", unit="%", name="Raum A Feuchte", attributes={"device_class": "humidity"})
    fake.set(ROOM_A_VALVE, "35", unit="%", name="Thermostat Raum A Ventil")
    fake.set(
        ROOM_A_TRV, "heat", name="Thermostat Raum A", attributes={"temperature": 21.0, "current_temperature": 21.4}
    )
    fake.set(BATH_TRV, "heat", name="Thermostat Bad", attributes={"temperature": 22.0, "current_temperature": 22.6})
    fake.set(HEAT, "8.5", unit="kWh", name="Wärme Heizen")
    fake.set(HEAT_DHW, "1.5", unit="kWh", name="Wärme Warmwasser")
    fake.set(FLOW_2, "33.0", unit="°C", name="Vorlauf Kreis 1")

    def outdoor_at(hour: datetime) -> float:
        # A cold and a mild week, so the heat loss can be learned.
        return 2.0 + (hour.day % 7) * 1.5

    fake.statistics[MV_OUTDOOR] = statistics_rows(now, 20, lambda hour: {"mean": outdoor_at(hour)})
    fake.statistics[ROOM_A] = statistics_rows(now, 20, lambda _hour: {"mean": 21.0})
    # 160 W/K, no gains: kWh per hour = 0.16 * (21 - outside).
    fake.statistics[HEAT] = statistics_rows(now, 20, lambda hour: {"change": 0.16 * (21.0 - outdoor_at(hour))})
    fake.statistics[HEAT_DHW] = statistics_rows(now, 20, lambda hour: {"change": 0.08})
    fake.statistics[MV_POWER] = statistics_rows(
        now, 20, lambda hour: {"mean": 160 * (21.0 - outdoor_at(hour)) / (2.5 + 0.1 * outdoor_at(hour))}
    )
    fake.statistics[FLOW_2] = statistics_rows(now, 20, lambda hour: {"mean": 37.0 - 0.5 * outdoor_at(hour)})
    fake.open_meteo = open_meteo_hours(now)


def configure(settings: SettingsStore, **changes: Any) -> None:
    hems = HemsSettings(
        mode=PlannerMode.PREVIEW,
        weather_source=WeatherSource.OPEN_METEO,
        latitude=52.5234,
        longitude=13.4049,
        heat_heating_entity=HEAT,
        heat_dhw_entity=HEAT_DHW,
        zones=(
            ZoneConfig(MV_ZONE_1, kind=ZoneKind.FLOOR),
            ZoneConfig(MV_ZONE_2, kind=ZoneKind.RADIATOR, flow_entity=FLOW_2),
        ),
    )
    rooms = (
        Room(
            entity_id=ROOM_A,
            name="Raum A",
            zone_id=MV_ZONE_2,
            details=RoomDetails(
                heat_load_w=600,
                trv_entity=ROOM_A_TRV,
                valve_entity=ROOM_A_VALVE,
                humidity_entity=ROOM_A_HUMIDITY,
                setpoint_c=21.0,
                night_loss_k=1.0,
            ),
        ),
        Room(entity_id=BATH_TRV, name="Bad", zone_id=MV_ZONE_2),
    )
    settings.update(
        lambda current: replace(
            current,
            connection=Connection.MYVAILLANT,
            myvaillant=MyVaillantEntities(
                zones=(MV_ZONE_1, MV_ZONE_2), hot_water=MV_HOT_WATER, outdoor_temperature=MV_OUTDOOR, power=MV_POWER
            ),
            rooms=rooms,
            hems=replace(hems, **changes),
        )
    )


@pytest.fixture
async def app(
    fake_ha: FakeHomeAssistant,
    settings_store: SettingsStore,
    log_store: LogStore,
    bridge_client: BridgeClient,
    pairing: PairingService,
    tmp_path: Path,
) -> AsyncIterator[App]:
    clock = Clock()
    prepare(fake_ha, clock.now)
    configure(settings_store)
    async with HomeAssistantClient(
        fake_ha.url, TOKEN, log=log_store, backoff=Backoff(initial=0.05, maximum=0.2), refresh_interval=60
    ) as client:
        device = MyVaillantState(settings_store, available=True)
        energy = EnergyMeter(device, tmp_path)
        home = HomeAssistant(settings_store, device, energy, log_store, client)
        hems = HemsService(
            settings_store,
            device,
            home,
            log_store,
            tmp_path,
            clock=clock,
            open_meteo_url=f"{fake_ha.url}/v1/dwd-icon",
        )
        gate = WriteGate(
            bridge_client,
            pairing,
            device,
            settings_store,
            log_store,
            link=MyVaillantLink(client, device, settings_store),
        )
        task = asyncio.create_task(client.run())
        await eventually(lambda: device.connection_state() is ConnectionState.CONNECTED)
        await eventually(lambda: home.hems_sources.get(ROOM_A_VALVE) is not None)
        api = create_app(
            pairing,
            device,
            gate,
            log_store,
            energy,
            home,
            static_dir=None,
            trusted_clients=frozenset({INGRESS_CLIENT}),
            hems=hems,
        )
        transport = httpx.ASGITransport(app=api, client=(INGRESS_CLIENT, 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as http:
            yield App(fake_ha, settings_store, device, home, hems, log_store, clock, http)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        hems.samples.close()


async def messages(log: LogStore, category: LogCategory | None = None) -> list[str]:
    return [entry.message for entry in (await log.page(category=category)).entries]


# --- planning from Home Assistant's data ------------------------------------------------------


async def test_the_preview_plans_from_open_meteo_and_learns_from_the_statistics(app: App) -> None:
    await app.hems.plan_now()

    status = app.hems.status()
    assert status.problems == []
    assert app.hems.plan is not None
    assert len(app.hems.plan.hours) == 48
    # Coordinates rounded to about 1 km; the past days calibrate the forecast.
    request = app.fake.open_meteo_requests[0]
    assert (request["latitude"], request["longitude"]) == ("52.52", "13.40")
    assert request["past_days"] == "7"
    assert status.forecast.source is WeatherSource.OPEN_METEO
    house = status.house
    assert house is not None and house.learned
    assert house.loss_w_per_k == pytest.approx(160.0, rel=0.05)
    assert house.cop is not None
    assert status.statistics_days >= 19
    # Read a month at a time, only reading.
    assert {tuple(ids) for ids, _start, _end in app.fake.statistics_requests} >= {
        tuple(sorted([HEAT, HEAT_DHW, MV_OUTDOOR, MV_POWER, ROOM_A, FLOW_2]))
    }
    assert status.demand and status.demand[0].heat_kwh > 0
    log = await messages(app.log, LogCategory.HEMS)
    assert any(message.startswith("Plan (Vorschau):") for message in log)


async def test_open_meteo_is_asked_at_most_hourly(app: App) -> None:
    await app.hems.plan_now()
    app.clock.now += timedelta(minutes=30)
    await app.hems.plan_now()
    app.clock.now += timedelta(minutes=31)
    await app.hems.plan_now()

    assert len(app.fake.open_meteo_requests) == 2


async def test_the_weather_entity_steps_in_when_open_meteo_fails(app: App) -> None:
    app.fake.open_meteo = None
    app.fake.set("weather.home", "cloudy", attributes={"temperature_unit": "°C", "wind_speed_unit": "km/h"})
    app.fake.forecasts["weather.home"] = [
        {"datetime": (app.clock.now + timedelta(hours=hour)).isoformat(), "temperature": 3.0} for hour in range(60)
    ]
    configure(app.settings, weather_entity="weather.home")

    await app.hems.plan_now()

    status = app.hems.status()
    assert status.forecast.source is WeatherSource.HOME_ASSISTANT
    assert "Open-Meteo antwortet gerade nicht. Die Prognose kommt aus Home Assistant." in status.problems
    assert app.hems.plan is not None


async def test_switched_off_there_is_no_plan_but_counting_goes_on(app: App) -> None:
    configure(app.settings, mode=PlannerMode.OFF)

    await app.hems.plan_now()

    assert app.hems.plan is None
    assert app.hems.changes(5) == []


# --- observing ---------------------------------------------------------------------------------


async def test_a_defrost_and_the_frost_protection_are_logged_in_preview(app: App) -> None:
    start = app.clock.now
    # Colder than 4 °C for five hours already.
    app.hems.cycles.feed_outdoor(start - timedelta(hours=5), 2.0)
    app.fake.change(MV_OUTDOOR, "2.0")
    profile = [(0, 700.0), (10, 720.0), (20, 450.0), (25, 1800.0), (40, 900.0), (60, 0.0)]
    for minute, watts in profile:
        app.clock.now = start + timedelta(minutes=minute)
        app.fake.change(MV_POWER, f"{watts:g}")
        await eventually(reported(app, watts))
        app.hems.observe(app.device.current)
    # The defrost shows once the recovery is seen; detection runs at most once a minute.
    app.clock.now += timedelta(minutes=2)
    app.hems.observe(app.device.current)

    stats = app.hems.cycles.stats()
    assert stats.today.defrosts == 1
    log = await messages(app.log, LogCategory.HEMS)
    assert any(message.startswith("Abtauung erkannt:") for message in log)
    assert any(message.startswith("Frostschutz der Wärmepumpe aktiv") for message in log)
    assert app.hems.status().frost.active


def reported(app: App, watts: float) -> Callable[[], bool]:
    return lambda: power(app) == watts and outdoor(app) == 2.0


def power(app: App) -> float | None:
    current = app.device.current
    reading = current.measurements.get("power_consumption") if current is not None else None
    return reading.value if reading is not None else None


def outdoor(app: App) -> float | None:
    current = app.device.current
    reading = current.measurements.get("outdoor_temperature") if current is not None else None
    return reading.value if reading is not None else None


async def test_the_zone_1_logic_is_shown_as_would(app: App) -> None:
    app.hems.observe(app.device.current)
    app.fake.change(MV_POWER, "12")
    await eventually(
        lambda: app.device.current is not None and app.device.current.measurements["power_consumption"].value == 12
    )
    app.hems.observe(app.device.current)
    app.clock.now += timedelta(minutes=2)
    app.hems.observe(app.device.current)

    zone1 = app.hems.status().zone1
    assert zone1 is not None
    assert zone1.zone_id == MV_ZONE_1
    assert zone1.would == "off"
    assert zone1.switches_today == 1


async def test_an_open_window_is_logged_as_would(app: App) -> None:
    app.fake.change(ROOM_A, "21.0")
    app.fake.change(ROOM_A_HUMIDITY, "50")
    await eventually(lambda: app.home.hems_sources.number(ROOM_A_HUMIDITY) == 50)
    app.hems._source_changed(ROOM_A, app.home.hems_sources.get(ROOM_A))
    app.clock.now += timedelta(minutes=5)
    app.fake.set(ROOM_A_HUMIDITY, "45", unit="%", attributes={"device_class": "humidity"})
    app.fake.set(ROOM_A, "20.5", unit="°C")
    await eventually(lambda: app.home.hems_sources.number(ROOM_A) == 20.5)

    await eventually(lambda: any(room.window_open for room in app.hems.rooms_view().rooms))
    log = await messages(app.log, LogCategory.ROOMS)
    assert any(message.startswith("Fenster offen erkannt (würde): Raum A") for message in log)


async def test_samples_feed_the_room_status(app: App) -> None:
    await app.hems.sample()
    await app.hems.plan_now()

    view = app.hems.rooms_view()
    room = next(item for item in view.rooms if item.entity_id == ROOM_A)
    bath = next(item for item in view.rooms if item.entity_id == BATH_TRV)
    assert room.temperature_c == 21.0
    assert room.valve == 35.0
    assert room.trv_setpoint_c == 21.0
    assert room.humidity == 48.0
    assert room.model is not None and room.model.learning
    assert room.minutes_to_lower is not None
    assert bath.measured_at_thermostat
    assert bath.temperature_c == 22.6
    assert app.hems.samples.series(["outdoor"], app.clock.now - timedelta(minutes=1))["outdoor"]


FLOW_SP_1 = "sensor.circuit_0_flow_setpoint"
FLOW_SP_2 = "sensor.circuit_1_flow_setpoint"


def history(app: App, hours: float, *, valve: float, room_c: float = 21.0, until: datetime | None = None) -> None:
    """Five-minute samples as the app writes them, the heat pump delivering heat all along."""
    end = until or app.clock.now
    moment = end - timedelta(hours=hours)
    while moment < end:
        app.hems.samples.add(
            moment,
            {
                "power": 900.0,
                f"flow:{MV_ZONE_2}": 33.0,
                "room:" + ROOM_A + ":temperature": room_c,
                "room:" + ROOM_A + ":valve": valve,
                "room:" + ROOM_A + ":setpoint": 21.0,
                "room:" + ROOM_A + ":window": 0.0,
                "room:" + ROOM_A + ":testing": 0.0,
            },
        )
        moment += timedelta(minutes=5)


async def test_the_flow_goes_down_slowly_after_the_bottleneck_and_back_at_once(app: App) -> None:
    # F2 (1.5.0): 26 hours of Raum A drawing heat at 50 % make it the bottleneck; zone 2's flow setpoint is 5 K
    # above zone 1's. One step down, then back to 0 at once when the room lacks heat.
    app.fake.set(FLOW_SP_1, "31.2", unit="°C")
    app.fake.set(FLOW_SP_2, "36.4", unit="°C")
    configure(
        app.settings,
        zones=(
            ZoneConfig(MV_ZONE_1, kind=ZoneKind.FLOOR, flow_setpoint_entity=FLOW_SP_1),
            ZoneConfig(MV_ZONE_2, kind=ZoneKind.RADIATOR, flow_entity=FLOW_2, flow_setpoint_entity=FLOW_SP_2),
        ),
    )
    app.home.watch()
    app.fake.change(MV_POWER, "900")
    app.fake.change(ROOM_A_VALVE, "50")
    await eventually(lambda: app.home.hems_sources.number(FLOW_SP_2) == 36.4 and power(app) == 900)
    history(app, 26, valve=50.0)

    await app.hems.sample()

    flow = app.hems.status().flow[0]
    assert (flow.offset_k, flow.bottleneck, flow.bottleneck_long_term, flow.bottleneck_mean) == (
        -0.5,
        "Raum A",
        True,
        50.0,
    )
    log = await messages(app.log, LogCategory.HEMS)
    assert "Zone 2 würde den Vorlauf ändern: \u22120,5 K (Vorlauf tiefer: Engpass Raum A bei 50 %)." in log
    # The shift is a part of the plan; the executor sends it on "Steuern".
    await app.hems.plan_now()
    assert app.hems.plan is not None and app.hems.plan.hours[0].zones[1].parts.get(Part.FLOW) == -0.5

    # 20 min later: nothing more (down at most every 2 h).
    app.clock.now += timedelta(minutes=20)
    await app.hems.sample()
    assert app.hems.status().flow[0].holding == "tiefer höchstens alle 2 h"

    # Raum A lacks heat: valve wide open for 40 min and 0.5 K below its setpoint.
    app.clock.now += timedelta(minutes=40)
    history(app, 0.75, valve=95.0, room_c=20.5)
    app.fake.change(ROOM_A_VALVE, "95")
    app.fake.change(ROOM_A, "20.5")
    await eventually(lambda: app.home.hems_sources.number(ROOM_A) == 20.5)
    await app.hems.sample()

    assert app.hems.status().flow[0].offset_k == 0.0
    log = await messages(app.log, LogCategory.HEMS)
    assert any("Vorlauf wieder höher: Raum A unterversorgt (von \u22120,5 K)" in message for message in log)


# --- API ------------------------------------------------------------------------------------


async def test_the_settings_are_checked_and_saved_over_the_api(app: App) -> None:
    body = {
        "weather_source": "open_meteo",
        "latitude": 50.0,
        "longitude": 8.0,
        "heat_heating_entity": HEAT,
        "zones": [{"zone_id": MV_ZONE_2, "kind": "radiator", "band_down_k": 1.5, "band_up_k": 2}],
        "night_start": "23:00",
        "night_end": "06:00",
        "night_loss_k": 0.7,
        "target_starts": 8,
    }
    saved = await app.http.put("/api/hems/settings", json=body)
    bad_band = await app.http.put(
        "/api/hems/settings", json={**body, "zones": [{"zone_id": MV_ZONE_2, "band_down_k": 0.7}]}
    )
    bad_window = await app.http.put("/api/hems/settings", json={**body, "night_end": "23:00"})
    no_entity = await app.http.put("/api/hems/settings", json={**body, "weather_source": "home_assistant"})
    not_a_sensor = await app.http.put("/api/hems/settings", json={**body, "buffer_entity": "climate.buffer"})

    assert saved.status_code == 200
    assert saved.json()["settings"]["night_start"] == "23:00"
    assert app.settings.current.hems.zones[0].band_down_k == 1.5
    assert app.settings.current.hems.mode is PlannerMode.PREVIEW
    assert bad_band.status_code == 422
    assert bad_band.json()["detail"] == "Das Band einer Zone geht nur in Schritten von 0,5 K."
    assert bad_window.status_code == 422
    assert no_entity.status_code == 422
    assert not_a_sensor.status_code == 422
    assert "Einstellungen des Energiemanagements geändert." in await messages(app.log)


async def test_the_flow_can_be_controlled_since_1_5_0(app: App) -> None:
    control = await app.http.post("/api/hems/mode", json={"mode": "control"})
    flow = await app.http.post("/api/hems/function", json={"function": "flow", "mode": "control"})
    off = await app.http.post("/api/hems/mode", json={"mode": "off"})

    assert control.status_code == 200
    assert flow.status_code == 200
    functions = {item["function"]: item for item in flow.json()["functions"]}
    assert (functions["flow"]["mode"], functions["flow"]["effective"]) == ("control", "control")
    assert functions["flow"]["available_modes"] == ["off", "preview", "control"]
    assert off.json()["mode"] == "off"
    assert off.json()["available_modes"] == ["off", "preview", "control"]
    assert "Energiemanagement ausgeschaltet." in await messages(app.log)


async def test_the_plan_and_its_csv_come_over_the_api(app: App) -> None:
    await app.hems.plan_now()

    plan = await app.http.get("/api/hems/plan")
    export = await app.http.get("/api/hems/plan/export")

    assert plan.status_code == 200
    body = plan.json()
    assert len(body["hours"]) == 48
    assert body["labels"] == {MV_ZONE_1: "Zone 1", MV_ZONE_2: "Zone 2"}
    assert body["rooms"] == {ROOM_A: "Raum A", BATH_TRV: "Bad"}
    assert len(body["history"]) == 24
    text = export.content.decode("utf-8")
    assert text.startswith("﻿Zeit;Art;Zone 1 Verschiebung K;")
    assert "Raum A Soll °C" in text.splitlines()[0]
    assert text.count("\r\n") == 49
    assert export.headers["content-disposition"].startswith('attachment; filename="vaillant-eebus-plan-')


async def test_room_details_are_saved_and_kept_when_the_room_list_is_saved(app: App) -> None:
    details = {
        "heat_load_w": 450,
        "design_c": 20,
        "surfaces": [{"kind": "radiator", "rated_w": 1600, "rated_basis": "75/65", "flow_lph": 15, "presetting": "3"}],
        "trv_entity": ROOM_A_TRV,
        "valve_entity": ROOM_A_VALVE,
        "humidity_entity": ROOM_A_HUMIDITY,
        "setpoint_c": 20.5,
        "schedule": [{"start": "06:00", "end": "22:00", "setpoint_c": 21}],
        "comfort_down_k": 0.5,
        "comfort_up_k": 0.5,
        "night_loss_k": 1.0,
        "throttle": True,
        "emergency_c": 20,
    }
    saved = await app.http.put(f"/api/rooms/{ROOM_A}", json=details)
    bad_step = await app.http.put(f"/api/rooms/{ROOM_A}", json={**details, "setpoint_c": 20.3})
    unknown = await app.http.put("/api/rooms/sensor.nowhere", json=details)
    rooms = await app.http.put(
        "/api/settings",
        json={
            "rooms": [
                {"entity_id": ROOM_A, "name": "Raum A neu", "zone_id": MV_ZONE_2},
                {"entity_id": BATH_TRV, "name": "Bad", "zone_id": MV_ZONE_2},
            ],
            "heat_energy_entities": [],
            "export": ["status"],
        },
    )

    assert saved.status_code == 200
    assert next(room for room in saved.json()["rooms"] if room["entity_id"] == ROOM_A)["details"]["setpoint_c"] == 20.5
    assert bad_step.status_code == 422
    assert unknown.status_code == 404
    assert rooms.status_code == 200
    room = app.settings.current.rooms[0]
    assert room.name == "Raum A neu"
    assert room.details.heat_load_w == 450
    assert room.details.schedule[0].setpoint_c == 21.0
    assert "Raumdaten geändert: Raum A." in await messages(app.log)


async def test_rooms_without_a_sensor_measure_at_the_thermostat_and_stay_out_of_the_average(app: App) -> None:
    snapshot = app.home.snapshot()

    bath = next(room for room in snapshot.rooms if room.entity_id == BATH_TRV)
    zone = next(zone for zone in snapshot.zones if zone.zone_id == MV_ZONE_2)
    assert bath.celsius == 22.6
    assert (zone.celsius, zone.rooms, zone.of) == (21.0, 1, 1)


async def test_the_entity_choice_knows_rooms_thermostats_humidity_and_valves(app: App) -> None:
    rooms = await app.http.get("/api/ha/entities", params={"kind": "room"})
    thermostats = await app.http.get("/api/ha/entities", params={"kind": "thermostat"})
    humidity = await app.http.get("/api/ha/entities", params={"kind": "humidity"})
    valves = await app.http.get("/api/ha/entities", params={"kind": "percent"})

    room_ids = {item["entity_id"] for item in rooms.json()}
    assert {ROOM_A, ROOM_A_TRV, BATH_TRV} <= room_ids
    # The myVAILLANT zones are no room thermostats.
    assert MV_ZONE_1 not in room_ids
    assert {item["entity_id"] for item in thermostats.json()} == {ROOM_A_TRV, BATH_TRV}
    assert [item["entity_id"] for item in humidity.json()] == [ROOM_A_HUMIDITY]
    assert [item["entity_id"] for item in valves.json()] == [ROOM_A_VALVE]


async def test_the_entity_choice_offers_window_contacts(app: App) -> None:
    app.fake.set("binary_sensor.buero_fenster", "off", attributes={"device_class": "window"})
    app.fake.set("binary_sensor.flur_bewegung", "off", attributes={"device_class": "motion"})
    app.fake.set("binary_sensor.kueche_kontakt", "off")

    contacts = await app.http.get("/api/ha/entities", params={"kind": "window"})

    assert {item["entity_id"] for item in contacts.json()} == {
        "binary_sensor.buero_fenster",
        "binary_sensor.kueche_kontakt",
    }


async def test_the_entity_choice_offers_heating_curves(app: App) -> None:
    # F2 (1.5.0): the controller's heating curve, read for the advice; myVAILLANT names it "... heating_curve".
    app.fake.set("sensor.zuhause_circuit_1_heating_curve", "0.35")
    app.fake.set("sensor.steilheit", "0.25")
    app.fake.set("sensor.leistung_faktor", "12")
    app.fake.set("sensor.vorlauf", "33.0", unit="°C")

    curves = await app.http.get("/api/ha/entities", params={"kind": "curve"})

    found = {item["entity_id"] for item in curves.json()}
    assert {"sensor.zuhause_circuit_1_heating_curve", "sensor.steilheit"} <= found
    assert not found & {"sensor.leistung_faktor", "sensor.vorlauf"}


# --- sensors ----------------------------------------------------------------------------------


async def test_the_energy_management_is_reported_as_sensors(app: App) -> None:
    await app.hems.plan_now()

    sensors = {sensor.entity_id: sensor for sensor in app.hems.sensors()}

    assert sensors["sensor.vaillant_eebus_hems_status"].state == "Vorschau"
    assert sensors["sensor.vaillant_eebus_starts_today"].state == "0"
    assert sensors["sensor.vaillant_eebus_defrosts_today"].state == "0"
    assert sensors["binary_sensor.vaillant_eebus_frost_protection"].state == "off"
    plan = sensors["sensor.vaillant_eebus_hems_plan"]
    assert plan.state == str(len(plan.attributes["änderungen"]))
    assert ExportGroup.HEMS in app.settings.current.export


# --- safety ------------------------------------------------------------------------------------


def test_only_the_executor_reaches_the_write_gate() -> None:
    package = Path(hems_package.__file__).parent
    executor = (package / "executor.py").read_text(encoding="utf-8")
    # The executor sends as the planner only.
    assert executor.count("self._gate.submit(") == executor.count("source=Source.PLANNER") == 2
    for source in package.rglob("*.py"):
        if source.name == "executor.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert not any("write_gate" in name or "bridge_client" in name for name in imported), source.name
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not names & {"submit", "send_command", "call_device_service", "set_state"}, source.name


def test_the_app_calls_only_the_weather_forecast_and_the_device_services() -> None:
    package = Path(ha_client_module.__file__).parent
    services: dict[str, set[str]] = {}
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        found = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "/api/services" in node.value
        }
        if found:
            services[source.name] = found

    # "/api/services/" is the start of call_device_service()'s path, limited to DEVICE_SERVICES.
    assert services == {"ha_client.py": {"/api/services/weather/get_forecasts", "/api/services/"}}


def test_the_websocket_only_reads() -> None:
    tree = ast.parse(Path(ha_client_module.__file__).read_text(encoding="utf-8"))
    types = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "type" and isinstance(value, ast.Constant):
                    types.add(value.value)

    # "hourly" is the forecast type of weather.get_forecasts (REST), not a WebSocket command.
    assert types == {
        "auth",
        "subscribe_events",
        "unsubscribe_events",
        "subscribe_trigger",
        "recorder/statistics_during_period",
        "hourly",
    }


async def test_a_shift_left_by_the_old_planner_is_named_once(
    settings_store: SettingsStore,
    log_store: LogStore,
    tmp_path: Path,
    home_assistant: HomeAssistant,
    state: StateService,
) -> None:
    saved = {
        "version": 1,
        "controls": {"6.1.1": {"base": 23.0, "applied": 24.0}, "dhw": {"base": 50.0, "applied": 50.0}},
    }
    (tmp_path / "hems.json").write_text(json.dumps(saved), encoding="utf-8")

    hems = HemsService(settings_store, state, home_assistant, log_store, tmp_path)
    hems.samples.close()

    log = await messages(log_store)
    assert any("noch eine Verschiebung offen (6.1.1)" in message for message in log)
    assert not (tmp_path / "hems.json").exists()
    assert (tmp_path / "hems.json.old").exists()


async def test_the_look_back_survives_a_restart(app: App, tmp_path: Path) -> None:
    await app.hems.plan_now()
    app.clock.now += timedelta(hours=1)

    again = HemsService(app.settings, app.device, app.home, app.log, tmp_path, clock=app.clock)
    try:
        history = again.plan_view().history
    finally:
        again.samples.close()

    assert history[-1].planned is not None
    assert history[-1].planned.zones
