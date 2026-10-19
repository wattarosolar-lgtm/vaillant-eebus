from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import httpx
import pytest

from tests.fake_bridge import FAKE_SKI, FakeBridge
from tests.support import FAST_BACKOFF, eventually, free_port
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.main import BackendConfig, run_backend


def test_config_from_environment() -> None:
    config = BackendConfig.from_env(
        {
            "BRIDGE_ADDRESS": "127.0.0.1:50052",
            "DATA_DIR": "/tmp/data",
            "HTTP_HOST": "127.0.0.1",
            "HTTP_PORT": "9000",
            "STATIC_DIR": "/opt/frontend",
            "TRUSTED_CLIENTS": "127.0.0.1, 172.30.32.2",
            "APP_VERSION": "0.2.0",
            "LOG_LEVEL": "debug",
            "BRIDGE_SERIAL_FILE": "/data/eebus/serial",
            "SHIP_PORT": "4712",
        }
    )

    assert config == BackendConfig(
        bridge_address="127.0.0.1:50052",
        data_dir=Path("/tmp/data"),
        http_host="127.0.0.1",
        http_port=9000,
        static_dir=Path("/opt/frontend"),
        trusted_clients=frozenset({"127.0.0.1", "172.30.32.2"}),
        app_version="0.2.0",
        log_level="debug",
        bridge_serial_file=Path("/data/eebus/serial"),
        ship_port=4712,
    )


def test_config_defaults_bind_ingress_only() -> None:
    config = BackendConfig.from_env({})

    assert (config.http_host, config.http_port) == ("172.30.32.1", 8714)
    assert config.trusted_clients == frozenset({"172.30.32.2"})
    assert config.static_dir is None
    assert config.bridge_serial_file is None
    assert config.ship_port is None


async def test_run_backend_serves_the_api_and_logs_the_bridge_ski(
    fake_bridge: FakeBridge, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="vaillant_eebus")
    port = free_port()
    config = BackendConfig(
        data_dir=tmp_path,
        http_host="127.0.0.1",
        http_port=port,
        trusted_clients=frozenset({"127.0.0.1"}),
    )
    async with BridgeClient(fake_bridge.address, FAST_BACKOFF) as bridge:
        backend = asyncio.create_task(run_backend(config, bridge))
        try:
            await eventually(lambda: f"local SKI {FAKE_SKI}" in caplog.text)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
                response = await http.get("/api/pairing")
            assert response.status_code == 200
            assert response.json()["bridge"]["ski"] == FAKE_SKI
        finally:
            backend.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await backend
