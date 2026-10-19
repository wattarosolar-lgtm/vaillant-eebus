from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import SettingsStore
from vaillant_eebus.state import StateService


def _client(
    pairing: PairingService,
    state: StateService,
    *,
    client_ip: str = INGRESS_CLIENT,
    static_dir: Path | None = None,
) -> httpx.AsyncClient:
    app = create_app(pairing, state, static_dir=static_dir, trusted_clients=frozenset({INGRESS_CLIENT}))
    transport = httpx.ASGITransport(app=app, client=(client_ip, 51234))
    return httpx.AsyncClient(transport=transport, base_url="http://ingress")


@pytest.fixture
async def client(pairing: PairingService, state: StateService) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(pairing, state) as http:
        yield http


async def test_requests_from_outside_ingress_are_rejected(pairing: PairingService, state: StateService) -> None:
    async with _client(pairing, state, client_ip="192.168.178.23") as http:
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
    pairing: PairingService, state: StateService, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Wärmepumpe</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1);", encoding="utf-8")
    async with _client(pairing, state, static_dir=tmp_path) as http:
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
    bridge_client: BridgeClient, settings_store: SettingsStore, tmp_path: Path, fake_bridge: FakeBridge
) -> None:
    serial_file = tmp_path / "serial"
    serial_file.write_text("0000000000000000", encoding="utf-8")
    pairing = PairingService(bridge_client, settings_store, "test", bridge_serial_file=serial_file)
    state = StateService(bridge_client, pairing)
    async with _client(pairing, state) as http:
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

    assert before == {"connection": "not_paired", "heat_pump": None}
    assert after["connection"] == "connected"
    heat_pump = after["heat_pump"]
    assert heat_pump["measurements"]["power_consumption"]["value"] == 532
    assert heat_pump["zones"][0]["label"] == "Zone 1"
    assert heat_pump["zones"][0]["room_temperature"] is None
    assert heat_pump["hot_water"]["setpoint"]["max"] == 70
