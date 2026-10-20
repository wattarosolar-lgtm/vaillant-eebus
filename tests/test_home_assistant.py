from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Coroutine
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from tests.fake_home_assistant import TOKEN, FakeHomeAssistant
from tests.support import eventually
from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.bridge_client import Backoff
from vaillant_eebus.energy import EnergyMeter, EnergyToday
from vaillant_eebus.ha_client import EntityState, HomeAssistantClient
from vaillant_eebus.ha_export import HomeAssistantExport
from vaillant_eebus.ha_import import HeatToday, HomeAssistantImport, ValueStatus, ZoneAverage
from vaillant_eebus.home_assistant import HomeAssistant, performance_today
from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import ExportGroup, Room, SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import WriteGate

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
ROOMS = (
    Room(entity_id="sensor.wohnzimmer", name="Wohnzimmer", zone_id="5.1.1"),
    Room(entity_id="sensor.kueche", name="Küche", zone_id="5.1.1"),
    Room(entity_id="sensor.bad", name="Bad", zone_id="6.1.1"),
    Room(entity_id="sensor.keller", name="Keller"),
)


@contextlib.asynccontextmanager
async def running(work: Coroutine[Any, Any, None]) -> AsyncIterator[None]:
    task = asyncio.create_task(work)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def entity(entity_id: str, value: str, unit: str | None = "°C", *, age: timedelta = timedelta(0)) -> EntityState:
    return EntityState(
        entity_id=entity_id,
        state=value,
        attributes={"unit_of_measurement": unit} if unit else {},
        last_updated=NOW - age,
        last_reported=NOW - age,
    )


@pytest.fixture
async def ha_client(fake_ha: FakeHomeAssistant, log_store: LogStore) -> AsyncIterator[HomeAssistantClient]:
    async with HomeAssistantClient(
        fake_ha.url, TOKEN, log=log_store, backoff=Backoff(initial=0.05, maximum=0.2), refresh_interval=60
    ) as client:
        yield client


@pytest.fixture
async def connected_state(pairing: PairingService, fake_bridge: FakeBridge, state: StateService) -> StateService:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)
    await state.refresh(GATEWAY_SKI)
    return state


# --- import ------------------------------------------------------------------------


async def test_a_zone_shows_the_average_of_its_valid_rooms(settings_store: SettingsStore) -> None:
    settings_store.update(lambda settings: replace(settings, rooms=ROOMS))
    importer = HomeAssistantImport(settings_store, available=True, clock=lambda: NOW)
    importer.ha_connected()
    importer.ha_state("sensor.wohnzimmer", entity("sensor.wohnzimmer", "21.0"))
    importer.ha_state("sensor.kueche", entity("sensor.kueche", "71.6", "°F"))
    importer.ha_state("sensor.bad", entity("sensor.bad", "unavailable"))
    importer.ha_state("sensor.keller", entity("sensor.keller", "15.5", age=timedelta(minutes=31)))

    snapshot = importer.snapshot()

    assert [(room.name, room.celsius, room.status) for room in snapshot.rooms] == [
        ("Wohnzimmer", 21.0, ValueStatus.OK),
        ("Küche", 22.0, ValueStatus.OK),
        ("Bad", None, ValueStatus.UNAVAILABLE),
        ("Keller", 15.5, ValueStatus.STALE),
    ]
    assert snapshot.zones == [
        ZoneAverage(zone_id="5.1.1", celsius=21.5, rooms=2, of=2),
        ZoneAverage(zone_id="6.1.1", celsius=None, rooms=0, of=1),
    ]


async def test_stale_rooms_and_unknown_units_are_left_out(settings_store: SettingsStore) -> None:
    settings_store.update(lambda settings: replace(settings, rooms=ROOMS[:2]))
    importer = HomeAssistantImport(settings_store, available=True, clock=lambda: NOW)
    importer.ha_state("sensor.wohnzimmer", entity("sensor.wohnzimmer", "20.0", age=timedelta(minutes=45)))
    importer.ha_state("sensor.kueche", entity("sensor.kueche", "293", "K"))

    assert importer.zone_average("5.1.1") == ZoneAverage(zone_id="5.1.1", celsius=None, rooms=0, of=2)


HEAT = ("sensor.waerme_heizung", "sensor.waerme_warmwasser")


async def test_the_heat_of_the_day_adds_up_its_sensors_in_kwh(settings_store: SettingsStore) -> None:
    settings_store.update(lambda settings: replace(settings, heat_energy_entities=HEAT))
    importer = HomeAssistantImport(settings_store, available=True, clock=lambda: NOW)
    importer.ha_state(HEAT[0], entity(HEAT[0], "12345", "Wh"))
    importer.ha_state(HEAT[1], entity(HEAT[1], "2.5", "kWh", age=timedelta(minutes=20)))

    heat = importer.snapshot().heat_today

    assert heat is not None
    assert (heat.kwh, heat.status, heat.reported_at) == (14.845, ValueStatus.OK, NOW - timedelta(minutes=20))


async def test_a_missing_heat_sensor_leaves_no_partial_sum(settings_store: SettingsStore) -> None:
    settings_store.update(lambda settings: replace(settings, heat_energy_entities=HEAT))
    importer = HomeAssistantImport(settings_store, available=True, clock=lambda: NOW)
    importer.ha_state(HEAT[0], entity(HEAT[0], "12.3", "kWh"))
    importer.ha_state(HEAT[1], entity(HEAT[1], "unavailable", "kWh"))

    heat = importer.snapshot().heat_today

    assert heat is not None
    assert (heat.kwh, heat.status) == (None, ValueStatus.UNAVAILABLE)


async def test_an_old_heat_sensor_marks_the_sum_stale(settings_store: SettingsStore) -> None:
    settings_store.update(lambda settings: replace(settings, heat_energy_entities=HEAT))
    importer = HomeAssistantImport(settings_store, available=True, clock=lambda: NOW)
    importer.ha_state(HEAT[0], entity(HEAT[0], "12.3", "kWh"))
    importer.ha_state(HEAT[1], entity(HEAT[1], "2.0", "kWh", age=timedelta(hours=4)))

    heat = importer.snapshot().heat_today

    assert heat is not None
    assert (heat.kwh, heat.status) == (14.3, ValueStatus.STALE)


async def test_losing_home_assistant_drops_its_values(settings_store: SettingsStore) -> None:
    settings_store.update(lambda settings: replace(settings, rooms=ROOMS[:1]))
    importer = HomeAssistantImport(settings_store, available=True, clock=lambda: NOW)
    importer.ha_connected()
    importer.ha_state("sensor.wohnzimmer", entity("sensor.wohnzimmer", "21.0"))

    importer.ha_disconnected()

    snapshot = importer.snapshot()
    assert not snapshot.connected
    assert snapshot.rooms[0].status is ValueStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("heat", "electricity", "expected"),
    [
        (HeatToday(["sensor.w"], 12.0, ValueStatus.OK, NOW), EnergyToday(kwh=3.0, incomplete=False), 4.0),
        # A day with gaps or an outdated heat value would give a wrong ratio.
        (HeatToday(["sensor.w"], 12.0, ValueStatus.OK, NOW), EnergyToday(kwh=3.0, incomplete=True), None),
        (HeatToday(["sensor.w"], 12.0, ValueStatus.STALE, NOW), EnergyToday(kwh=3.0, incomplete=False), None),
        # Too little electrical energy yet, e.g. early in the morning.
        (HeatToday(["sensor.w"], 1.0, ValueStatus.OK, NOW), EnergyToday(kwh=0.2, incomplete=False), None),
        (None, EnergyToday(kwh=3.0, incomplete=False), None),
    ],
)
def test_the_performance_of_the_day_needs_reliable_values(
    heat: HeatToday | None, electricity: EnergyToday, expected: float | None
) -> None:
    assert performance_today(heat, electricity) == expected


# --- client ------------------------------------------------------------------------


async def test_rooms_follow_home_assistant_live(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    settings_store.update(lambda settings: replace(settings, rooms=ROOMS[:2]))
    fake_ha.set("sensor.wohnzimmer", "21.0", unit="°C")
    fake_ha.set("sensor.kueche", "22.0", unit="°C")
    home_assistant = HomeAssistant(settings_store, state, energy_meter, log_store, ha_client)

    async with running(home_assistant.run()):
        await eventually(lambda: home_assistant.importer.zone_average("5.1.1") == ZoneAverage("5.1.1", 21.5, 2, 2))
        fake_ha.set("sensor.kueche", "23.0", unit="°C")
        await eventually(lambda: home_assistant.importer.zone_average("5.1.1") == ZoneAverage("5.1.1", 22.0, 2, 2))

    assert home_assistant.snapshot().connected


async def test_a_new_room_is_followed_without_reconnecting(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    fake_ha.set("sensor.bad", "20.5", unit="°C")
    home_assistant = HomeAssistant(settings_store, state, energy_meter, log_store, ha_client)

    async with running(home_assistant.run()):
        await eventually(lambda: ha_client.connected)
        home_assistant.change(rooms=ROOMS[2:3], heat_energy_entities=(), export=frozenset())
        await eventually(lambda: home_assistant.importer.zone_average("6.1.1") == ZoneAverage("6.1.1", 20.5, 1, 1))
        fake_ha.set("sensor.bad", "21.5", unit="°C")
        await eventually(lambda: home_assistant.importer.zone_average("6.1.1") == ZoneAverage("6.1.1", 21.5, 1, 1))

    assert fake_ha.connections == 1


async def test_older_home_assistant_versions_are_followed_too(
    ha_client: HomeAssistantClient,
    fake_ha: FakeHomeAssistant,
    settings_store: SettingsStore,
    state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    # Before HA 2024.10 the trigger type was named "platform".
    fake_ha.trigger_key = "platform"
    settings_store.update(lambda settings: replace(settings, rooms=ROOMS[2:3]))
    fake_ha.set("sensor.bad", "20.5", unit="°C")
    home_assistant = HomeAssistant(settings_store, state, energy_meter, log_store, ha_client)

    async with running(home_assistant.run()):
        await eventually(lambda: ha_client.connected)
        fake_ha.set("sensor.bad", "19.0", unit="°C")
        await eventually(lambda: home_assistant.importer.zone_average("6.1.1") == ZoneAverage("6.1.1", 19.0, 1, 1))


async def test_a_refused_token_leaves_the_app_running_without_values(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    fake_ha.refuse_auth = True
    home_assistant = HomeAssistant(settings_store, state, energy_meter, log_store, ha_client)

    async with running(home_assistant.run()):
        await asyncio.sleep(0.3)

    assert not ha_client.connected
    assert not home_assistant.snapshot().connected
    assert fake_ha.writes == []


async def test_only_the_apps_own_sensors_can_be_written(
    fake_ha: FakeHomeAssistant, ha_client: HomeAssistantClient
) -> None:
    for foreign in ("sensor.wohnzimmer", "light.kueche", "sensor.vaillant_eebus", "climate.vaillant_eebus_zone_1"):
        with pytest.raises(ValueError, match="not one of this app's sensors"):
            await ha_client.set_state(foreign, "1", {})
        with pytest.raises(ValueError, match="not one of this app's sensors"):
            await ha_client.remove_state(foreign)

    assert fake_ha.writes == []


# --- export ------------------------------------------------------------------------


async def test_the_heat_pump_is_reported_as_sensors(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    connected_state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    settings_store.update(lambda settings: replace(settings, rooms=ROOMS[:1]))
    fake_ha.set("sensor.wohnzimmer", "21.0", unit="°C")
    home_assistant = HomeAssistant(settings_store, connected_state, energy_meter, log_store, ha_client)

    expected = {
        "binary_sensor.vaillant_eebus_connected",
        "sensor.vaillant_eebus_outdoor_temperature",
        "sensor.vaillant_eebus_zone_1_room_temperature",
        "sensor.vaillant_eebus_zone_1_mode",
        "sensor.vaillant_eebus_zone_2_setpoint",
        "sensor.vaillant_eebus_power",
    }
    async with running(home_assistant.run()):
        await eventually(lambda: expected <= fake_ha.own_states().keys())

    own = fake_ha.own_states()
    assert own["sensor.vaillant_eebus_zone_1_room_temperature"]["state"] == "21.0"
    assert own["binary_sensor.vaillant_eebus_connected"]["state"] == "on"
    assert own["sensor.vaillant_eebus_outdoor_temperature"]["state"] == "13.5"
    assert own["sensor.vaillant_eebus_outdoor_temperature"]["attributes"] == {
        "friendly_name": "Außentemperatur",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "icon": "mdi:thermometer",
    }
    assert own["sensor.vaillant_eebus_zone_2_setpoint"]["state"] == "23.0"
    assert own["sensor.vaillant_eebus_zone_1_mode"]["state"] == "Auto"
    assert own["sensor.vaillant_eebus_power"]["state"] == "532"
    # Power limits and compressor control are not exported by default.
    assert "sensor.vaillant_eebus_power_limit" not in own
    # Nothing but the app's own entities was written.
    assert all(
        entity_id.startswith(("sensor.vaillant_eebus_", "binary_sensor.vaillant_eebus_"))
        for _method, entity_id, _body in fake_ha.writes
    )


async def test_sensors_come_back_after_a_restart_of_home_assistant(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    connected_state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    home_assistant = HomeAssistant(settings_store, connected_state, energy_meter, log_store, ha_client)

    async with running(home_assistant.run()):
        await eventually(lambda: "binary_sensor.vaillant_eebus_connected" in fake_ha.own_states())
        await fake_ha.restart()
        assert fake_ha.own_states() == {}
        await eventually(lambda: "binary_sensor.vaillant_eebus_connected" in fake_ha.own_states())

    assert fake_ha.connections == 2
    log = [entry.message for entry in (await log_store.page(category=LogCategory.CONNECTION)).entries]
    assert "Verbindung zu Home Assistant verloren; die App versucht es weiter." in log


async def test_sensors_are_reported_again_when_home_assistant_has_started(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    connected_state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    home_assistant = HomeAssistant(settings_store, connected_state, energy_meter, log_store, ha_client)

    async with running(home_assistant.run()):
        await eventually(lambda: "binary_sensor.vaillant_eebus_connected" in fake_ha.own_states())
        for entity_id in list(fake_ha.own_states()):
            del fake_ha.states[entity_id]
        await fake_ha.started()
        await eventually(lambda: "binary_sensor.vaillant_eebus_connected" in fake_ha.own_states())

    assert fake_ha.connections == 1


async def test_a_group_no_longer_exported_is_removed(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    connected_state: StateService,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    home_assistant = HomeAssistant(settings_store, connected_state, energy_meter, log_store, ha_client)

    status = {
        "binary_sensor.vaillant_eebus_connected",
        "binary_sensor.vaillant_eebus_compressor_running",
        "binary_sensor.vaillant_eebus_heartbeat_ok",
        "sensor.vaillant_eebus_operating_state",
    }
    async with running(home_assistant.run()):
        await eventually(lambda: "sensor.vaillant_eebus_zone_1_setpoint" in fake_ha.own_states())
        home_assistant.change(rooms=(), heat_energy_entities=(), export=frozenset({ExportGroup.STATUS}))
        # The other groups' sensors are removed one after another.
        await eventually(lambda: fake_ha.own_states().keys() == status)

    assert home_assistant.exported() == sorted(status)
    assert ("DELETE", "sensor.vaillant_eebus_zone_1_setpoint", None) in fake_ha.writes


async def test_a_changed_value_is_reported_at_most_every_ten_seconds(
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
    settings_store: SettingsStore,
    connected_state: StateService,
    fake_bridge: FakeBridge,
    energy_meter: EnergyMeter,
    log_store: LogStore,
) -> None:
    now = [1000.0]
    home_assistant = HomeAssistant(settings_store, connected_state, energy_meter, log_store, ha_client)
    export = HomeAssistantExport(
        ha_client, connected_state, energy_meter, home_assistant.importer, settings_store, clock=lambda: now[0]
    )
    outdoor = "sensor.vaillant_eebus_outdoor_temperature"

    async with running(ha_client.run()):
        await eventually(lambda: ha_client.connected)
        # One reporting round at a time, under the test's clock.
        await export._report()
        fake_bridge.device.measurements["outdoor_temperature"] = (14.0, "degC")
        await connected_state.refresh(GATEWAY_SKI)
        now[0] += 5
        await export._report()
        too_early = fake_ha.own_states()[outdoor]["state"]
        now[0] += 5
        await export._report()

    assert too_early == "13.5"
    assert fake_ha.own_states()[outdoor]["state"] == "14.0"


# --- API ---------------------------------------------------------------------------


def _api(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log: LogStore,
    energy: EnergyMeter,
    home_assistant: HomeAssistant,
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
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(INGRESS_CLIENT, 51234)), base_url="http://x"
    )


async def test_settings_are_saved_and_logged(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log_store: LogStore,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
    settings_store: SettingsStore,
) -> None:
    body = {
        "rooms": [
            {"entity_id": "sensor.wohnzimmer", "name": " Wohnzimmer ", "zone_id": "5.1.1"},
            {"entity_id": "sensor.keller", "name": "Keller", "zone_id": None},
        ],
        "heat_energy_entities": ["sensor.waerme_heizung", "sensor.waerme_warmwasser"],
        "export": ["status", "zones"],
    }
    async with _api(pairing, state, gate, log_store, energy_meter, home_assistant) as http:
        saved = (await http.put("/api/settings", json=body)).json()
        loaded = (await http.get("/api/settings")).json()

    assert saved == loaded
    assert loaded["rooms"][0] == {"entity_id": "sensor.wohnzimmer", "name": "Wohnzimmer", "zone_id": "5.1.1"}
    assert loaded["export"] == ["status", "zones"]
    assert loaded["home_assistant_available"] is False
    assert settings_store.load().heat_energy_entities == ("sensor.waerme_heizung", "sensor.waerme_warmwasser")
    log = [entry.message for entry in (await log_store.page(category=LogCategory.SETTINGS)).entries]
    assert "Räume geändert: Wohnzimmer (5.1.1), Keller (keine Zone)" in log
    assert "Export nach Home Assistant: Status, Zonen" in log


@pytest.mark.parametrize(
    "rooms",
    [
        [{"entity_id": "sensor.a", "name": "A"}, {"entity_id": "sensor.a", "name": "B"}],
        [{"entity_id": "Sensor A", "name": "A"}],
        [{"entity_id": "sensor.a", "name": ""}],
        [{"entity_id": "sensor.a", "name": "A", "zone_id": "zone 1"}],
    ],
)
async def test_invalid_settings_are_rejected(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log_store: LogStore,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
    settings_store: SettingsStore,
    rooms: list[dict[str, Any]],
) -> None:
    async with _api(pairing, state, gate, log_store, energy_meter, home_assistant) as http:
        response = await http.put("/api/settings", json={"rooms": rooms, "export": []})

    assert response.status_code == 422
    assert settings_store.current.rooms == ()


async def test_entities_to_choose_are_filtered_by_unit_and_text(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log_store: LogStore,
    energy_meter: EnergyMeter,
    settings_store: SettingsStore,
    fake_ha: FakeHomeAssistant,
    ha_client: HomeAssistantClient,
) -> None:
    fake_ha.set("sensor.wohnzimmer", "21.0", unit="°C", name="Wohnzimmer Temperatur")
    fake_ha.set("sensor.bad", "70.1", unit="°F", name="Bad Temperatur")
    fake_ha.set("sensor.bad_feuchte", "55", unit="%", name="Bad Feuchte")
    fake_ha.set("sensor.waerme_heute", "12.3", unit="kWh", name="Wärme heute")
    fake_ha.set("light.bad", "on", name="Bad Licht")
    fake_ha.set("sensor.vaillant_eebus_outdoor_temperature", "13.5", unit="°C")
    home_assistant = HomeAssistant(settings_store, state, energy_meter, log_store, ha_client)

    async with _api(pairing, state, gate, log_store, energy_meter, home_assistant) as http:
        temperatures = (await http.get("/api/ha/entities", params={"kind": "temperature"})).json()
        bathroom = (await http.get("/api/ha/entities", params={"kind": "temperature", "q": "bad"})).json()
        energy = (await http.get("/api/ha/entities", params={"kind": "energy"})).json()

    assert [choice["entity_id"] for choice in temperatures] == ["sensor.bad", "sensor.wohnzimmer"]
    assert [choice["entity_id"] for choice in bathroom] == ["sensor.bad"]
    assert energy == [{"entity_id": "sensor.waerme_heute", "name": "Wärme heute", "state": "12.3", "unit": "kWh"}]


async def test_choosing_entities_outside_home_assistant_explains_why(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log_store: LogStore,
    energy_meter: EnergyMeter,
    home_assistant: HomeAssistant,
) -> None:
    async with _api(pairing, state, gate, log_store, energy_meter, home_assistant) as http:
        response = await http.get("/api/ha/entities", params={"kind": "temperature"})

    assert response.status_code == 503
    assert "Home Assistant ist gerade nicht erreichbar" in response.json()["detail"]
