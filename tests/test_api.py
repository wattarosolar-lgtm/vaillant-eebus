from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from tests.fake_bridge import GATEWAY_SKI, FakeBridge
from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.pairing import PairingService


def _client(
    pairing: PairingService, *, client_ip: str = INGRESS_CLIENT, static_dir: Path | None = None
) -> httpx.AsyncClient:
    app = create_app(pairing, static_dir=static_dir, trusted_clients=frozenset({INGRESS_CLIENT}))
    transport = httpx.ASGITransport(app=app, client=(client_ip, 51234))
    return httpx.AsyncClient(transport=transport, base_url="http://ingress")


@pytest.fixture
async def client(pairing: PairingService) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(pairing) as http:
        yield http


async def test_requests_from_outside_ingress_are_rejected(pairing: PairingService) -> None:
    async with _client(pairing, client_ip="192.168.178.23") as http:
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


async def test_ui_is_served_from_the_static_directory(pairing: PairingService, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Wärmepumpe</title>", encoding="utf-8")
    async with _client(pairing, static_dir=tmp_path) as http:
        response = await http.get("/")

    assert response.status_code == 200
    assert "Wärmepumpe" in response.text
