from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import httpx
import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from vaillant_eebus.api import INGRESS_CLIENT, ApiState, create_app, state_events
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.log_store import LogStore
from vaillant_eebus.pairing import ConnectionState, PairingService
from vaillant_eebus.settings import OperatingMode, SettingsStore
from vaillant_eebus.state import HeatPumpState, StateService
from vaillant_eebus.write_gate import WriteGate


def _client(
    pairing: PairingService,
    state: StateService,
    gate: WriteGate,
    log: LogStore,
    *,
    client_ip: str = INGRESS_CLIENT,
    static_dir: Path | None = None,
) -> httpx.AsyncClient:
    app = create_app(pairing, state, gate, log, static_dir=static_dir, trusted_clients=frozenset({INGRESS_CLIENT}))
    transport = httpx.ASGITransport(app=app, client=(client_ip, 51234))
    return httpx.AsyncClient(transport=transport, base_url="http://ingress")


@pytest.fixture
async def client(
    pairing: PairingService, state: StateService, gate: WriteGate, log_store: LogStore
) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(pairing, state, gate, log_store) as http:
        yield http


async def test_requests_from_outside_ingress_are_rejected(
    pairing: PairingService, state: StateService, gate: WriteGate, log_store: LogStore
) -> None:
    async with _client(pairing, state, gate, log_store, client_ip="192.168.178.23") as http:
        response = await http.get("/api/pairing")

    assert response.status_code == 403


async def test_get_pairing_returns_status(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/pairing")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "not_paired"
    assert body["bridge"]["reachable"] is True
    assert body["paired"] is None
    assert body["discovered"][0]["ski"] == GATEWAY_SKI


async def test_register_pairs_the_gateway(client: httpx.AsyncClient, fake_bridge: FakeBridge) -> None:
    response = await client.post("/api/pairing/register", json={"ski": GATEWAY_SKI})

    assert response.status_code == 200
    assert response.json()["state"] == "disconnected"
    assert response.json()["paired"]["ski"] == GATEWAY_SKI
    assert fake_bridge.device.registered == {GATEWAY_SKI}


async def test_register_rejects_malformed_ski(client: httpx.AsyncClient, fake_bridge: FakeBridge) -> None:
    response = await client.post("/api/pairing/register", json={"ski": "1234"})

    assert response.status_code == 422
    assert fake_bridge.device.register_calls == []


async def test_dump_without_pairing_is_a_conflict(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/diagnostics/dump")

    assert response.status_code == 409
    assert response.json()["detail"] == "Es ist noch kein Gateway gekoppelt."


async def test_dump_is_downloaded_as_json_file(client: httpx.AsyncClient, fake_bridge: FakeBridge) -> None:
    await client.post("/api/pairing/register", json={"ski": GATEWAY_SKI})
    fake_bridge.device.connect(GATEWAY_SKI)

    response = await client.get("/api/diagnostics/dump")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == (
        'attachment; filename="vaillant-eebus-diagnose-20261019-080000.json"'
    )
    assert response.json()["gateway"]["model"] == "VR940f"


async def test_dump_of_a_disconnected_gateway_is_unavailable(client: httpx.AsyncClient) -> None:
    await client.post("/api/pairing/register", json={"ski": GATEWAY_SKI})

    response = await client.get("/api/diagnostics/dump")

    assert response.status_code == 503
    assert "nicht erreichbar" in response.json()["detail"]


async def test_ui_is_served_from_the_static_directory(
    pairing: PairingService, state: StateService, gate: WriteGate, log_store: LogStore, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Wärmepumpe</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1);", encoding="utf-8")
    async with _client(pairing, state, gate, log_store, static_dir=tmp_path) as http:
        page = await http.get("/")
        asset = await http.get("/assets/index-abc123.js")

    assert page.status_code == 200
    assert "Wärmepumpe" in page.text
    # After an update the page must point to the new asset names at once.
    assert page.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]


async def test_bridge_errors_name_the_grpc_code(client: httpx.AsyncClient) -> None:
    await client.post("/api/pairing/register", json={"ski": GATEWAY_SKI})

    response = await client.get("/api/diagnostics/dump")

    assert response.json()["detail"].endswith("(Fehlercode UNAVAILABLE)")


async def test_pairing_through_a_foreign_bridge_is_refused(
    bridge_client: BridgeClient,
    settings_store: SettingsStore,
    log_store: LogStore,
    tmp_path: Path,
    fake_bridge: FakeBridge,
) -> None:
    serial_file = tmp_path / "serial"
    serial_file.write_text("0000000000000000", encoding="utf-8")
    pairing = PairingService(bridge_client, settings_store, "test", bridge_serial_file=serial_file)
    state = StateService(bridge_client, pairing)
    gate = WriteGate(bridge_client, pairing, state, settings_store, log_store)
    async with _client(pairing, state, gate, log_store) as http:
        status = (await http.get("/api/pairing")).json()
        response = await http.post("/api/pairing/register", json={"ski": GATEWAY_SKI})

    assert status["state"] == "foreign_bridge"
    assert response.status_code == 409
    assert "fremde EEBUS-Bridge" in response.json()["detail"]
    assert fake_bridge.device.register_calls == []


async def test_state_contains_connection_and_heat_pump(
    client: httpx.AsyncClient, state: StateService, fake_bridge: FakeBridge
) -> None:
    before = (await client.get("/api/state")).json()
    await client.post("/api/pairing/register", json={"ski": GATEWAY_SKI})
    fake_bridge.device.connect(GATEWAY_SKI)
    await state.refresh(GATEWAY_SKI)

    after = (await client.get("/api/state")).json()

    assert before == {"connection": "not_paired", "mode": "demo", "pending": [], "heat_pump": None}
    assert after["connection"] == "connected"
    heat_pump = after["heat_pump"]
    assert heat_pump["measurements"]["power_consumption"]["value"] == 532
    assert [(zone["id"], zone["label"]) for zone in heat_pump["zones"]] == [("5.1.1", "Zone 1"), ("6.1.1", "Zone 2")]
    assert heat_pump["zones"][0]["room_temperature"] is None
    assert heat_pump["zones"][1]["setpoint"]["value"] == 23
    assert heat_pump["hot_water"]["setpoint"]["max"] == 70


@pytest.fixture
async def connected_client(
    client: httpx.AsyncClient, state: StateService, fake_bridge: FakeBridge
) -> httpx.AsyncClient:
    await client.post("/api/pairing/register", json={"ski": GATEWAY_SKI})
    fake_bridge.device.connect(GATEWAY_SKI)
    await state.refresh(GATEWAY_SKI)
    return client


WRITE_REQUESTS = [
    ("/api/zones/6.1.1/setpoint", {"value": 22.5}),
    ("/api/zones/5.1.1/mode", {"mode": "on"}),
    ("/api/dhw/setpoint", {"value": 54}),
    ("/api/dhw/mode", {"mode": "off"}),
    ("/api/dhw/boost", {"active": True}),
    ("/api/lpc", {"value_watts": 4200, "duration_seconds": 3600, "active": True}),
    ("/api/lpc/failsafe", {"value_watts": 4200, "duration_minimum_seconds": 7200}),
    ("/api/ohpcf", {"action": "schedule"}),
]


async def test_demo_mode_answers_every_write_without_sending(
    connected_client: httpx.AsyncClient, fake_bridge: FakeBridge
) -> None:
    responses = [await connected_client.post(path, json=body) for path, body in WRITE_REQUESTS]

    assert [response.status_code for response in responses] == [200] * len(WRITE_REQUESTS)
    assert all(response.json()["sent"] is False for response in responses)
    assert responses[0].json() == {
        "sent": False,
        "mode": "demo",
        "message": "Würde senden: Zone 2: Solltemperatur 22,5 °C",
        "would_send": {"control": "zone_setpoint", "zone_id": "6.1.1", "value": 22.5},
    }
    assert fake_bridge.device.write_calls == []


async def test_live_mode_sends_after_confirmation(connected_client: httpx.AsyncClient, fake_bridge: FakeBridge) -> None:
    refused = await connected_client.post("/api/mode", json={"mode": "live"})
    confirmed = await connected_client.post("/api/mode", json={"mode": "live", "confirm": True})
    response = await connected_client.post("/api/zones/6.1.1/setpoint", json={"value": 22.5})
    state = (await connected_client.get("/api/state")).json()

    assert refused.status_code == 400
    assert confirmed.json() == {"mode": "live"}
    assert (await connected_client.get("/api/mode")).json() == {"mode": "live"}
    assert response.json() == {
        "sent": True,
        "mode": "live",
        "message": "Gesendet: Zone 2: Solltemperatur 22,5 °C",
        "would_send": None,
    }
    assert [name for name, _request in fake_bridge.device.write_calls] == ["SetRoomHeatingTemperature"]
    assert state["mode"] == "live"
    assert [(write["control"], write["zone_id"], write["value"]) for write in state["pending"]] == [
        ("zone_setpoint", "6.1.1", 22.5)
    ]


async def test_rejected_writes_explain_why(connected_client: httpx.AsyncClient) -> None:
    await connected_client.post("/api/mode", json={"mode": "live", "confirm": True})
    out_of_range = await connected_client.post("/api/zones/6.1.1/setpoint", json={"value": 35})
    unknown_zone = await connected_client.post("/api/zones/9.9.9/setpoint", json={"value": 21})
    await connected_client.post("/api/dhw/setpoint", json={"value": 54})
    too_soon = await connected_client.post("/api/dhw/setpoint", json={"value": 53})

    assert out_of_range.status_code == 422
    assert out_of_range.json()["detail"] == "Zone 2: Die Solltemperatur muss zwischen 5,0 °C und 30,0 °C liegen."
    assert unknown_zone.status_code == 404
    assert too_soon.status_code == 429
    assert too_soon.headers["retry-after"] == "5"


async def test_malformed_write_requests_are_refused(connected_client: httpx.AsyncClient) -> None:
    bad_zone = await connected_client.post("/api/zones/zone-1/setpoint", json={"value": 21})
    not_a_number = await connected_client.post("/api/dhw/setpoint", json={"value": "warm"})
    negative = await connected_client.post("/api/lpc", json={"value_watts": -1, "duration_seconds": 0, "active": True})

    assert (bad_zone.status_code, not_a_number.status_code, negative.status_code) == (422, 422, 422)


async def test_writes_without_a_paired_gateway_are_refused(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/dhw/boost", json={"active": True})

    assert response.status_code == 409
    assert response.json()["detail"] == "Es ist noch kein Gateway gekoppelt."


async def test_protocol_is_paged_and_filtered(connected_client: httpx.AsyncClient) -> None:
    await connected_client.post("/api/zones/6.1.1/setpoint", json={"value": 22.5})
    await connected_client.post("/api/dhw/mode", json={"mode": "off"})

    writes = (await connected_client.get("/api/log", params={"category": "write", "limit": 1})).json()
    older = (
        await connected_client.get("/api/log", params={"category": "write", "before": writes["entries"][0]["id"]})
    ).json()
    connection = (await connected_client.get("/api/log", params={"category": "connection"})).json()

    assert [entry["message"] for entry in writes["entries"]] == ["Würde senden: Warmwasser: Betriebsart Aus"]
    assert writes["more"] is True
    assert [entry["message"] for entry in older["entries"]] == ["Würde senden: Zone 2: Solltemperatur 22,5 °C"]
    assert [entry["message"] for entry in connection["entries"]] == ["Gateway …7D9F02 gekoppelt."]


class _OpenClient:
    async def is_disconnected(self) -> bool:
        return False


async def test_event_stream_survives_a_failing_bridge_call(state: StateService) -> None:
    calls = 0

    async def flaky_state(heat_pump: HeatPumpState | None) -> ApiState:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise grpc.aio.AioRpcError(grpc.StatusCode.INTERNAL, grpc.aio.Metadata(), grpc.aio.Metadata(), "broken")
        return ApiState(connection=ConnectionState.CONNECTED, mode=OperatingMode.DEMO, pending=[], heat_pump=heat_pump)

    events = state_events(_OpenClient(), state, flaky_state, keepalive=5)
    try:
        first = await anext(events)
        second = anext(events)
        state.republish()
        async with asyncio.timeout(2):
            message = await second
    finally:
        await events.aclose()

    assert first == ": bridge error\n\n"
    assert message.startswith("event: state\ndata: ")
    assert json.loads(message.split("data: ", 1)[1])["connection"] == "connected"
