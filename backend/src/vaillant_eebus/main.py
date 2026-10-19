"""Backend entry point (`python -m vaillant_eebus`), started by the s6 service `backend`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from vaillant_eebus.api import INGRESS_CLIENT, create_app
from vaillant_eebus.bridge_client import DEFAULT_ADDRESS, BridgeClient
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import SettingsStore

_LOGGER = logging.getLogger("vaillant_eebus")

_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


@dataclass(frozen=True, slots=True)
class BackendConfig:
    bridge_address: str = DEFAULT_ADDRESS
    data_dir: Path = Path("/data")
    # With host networking the Supervisor reaches ingress on the host's address
    # in the hassio network.
    http_host: str = "172.30.32.1"
    http_port: int = 8714
    static_dir: Path | None = None
    trusted_clients: frozenset[str] = frozenset({INGRESS_CLIENT})
    app_version: str = "dev"
    log_level: str = "info"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BackendConfig:
        defaults = cls()
        static_dir = env.get("STATIC_DIR")
        trusted = env.get("TRUSTED_CLIENTS")
        return cls(
            bridge_address=env.get("BRIDGE_ADDRESS", defaults.bridge_address),
            data_dir=Path(env.get("DATA_DIR", str(defaults.data_dir))),
            http_host=env.get("HTTP_HOST", defaults.http_host),
            http_port=int(env.get("HTTP_PORT", str(defaults.http_port))),
            static_dir=Path(static_dir) if static_dir else None,
            trusted_clients=(
                frozenset(client.strip() for client in trusted.split(",") if client.strip())
                if trusted
                else defaults.trusted_clients
            ),
            app_version=env.get("APP_VERSION", defaults.app_version),
            log_level=env.get("LOG_LEVEL", defaults.log_level),
        )


def configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=_LOG_LEVELS.get(level_name.lower(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def run_backend(config: BackendConfig, bridge: BridgeClient) -> None:
    """Serve the ingress UI and keep the bridge pairing alive until stopped."""
    pairing = PairingService(bridge, SettingsStore(config.data_dir), config.app_version)
    app = create_app(pairing, static_dir=config.static_dir, trusted_clients=config.trusted_clients)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.http_host,
            port=config.http_port,
            log_config=None,
            access_log=False,
            lifespan="off",
        )
    )
    async with asyncio.TaskGroup() as tasks:
        pairing_task = tasks.create_task(pairing.run())
        # Returns when uvicorn handles SIGTERM; it re-raises the signal afterwards.
        await server.serve()
        pairing_task.cancel()


async def _main(config: BackendConfig) -> None:
    main_task = asyncio.current_task()
    assert main_task is not None
    # s6 stops the service with SIGTERM. add_signal_handler is unavailable on
    # Windows dev machines, where Ctrl+C still stops the backend.
    with contextlib.suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, main_task.cancel)
    try:
        async with BridgeClient(config.bridge_address) as bridge:
            await run_backend(config, bridge)
    except asyncio.CancelledError:
        pass
    _LOGGER.info("Backend stopped")


def run() -> None:
    config = BackendConfig.from_env(os.environ)
    configure_logging(config.log_level)
    _LOGGER.info("Starting backend %s, UI on %s:%d", config.app_version, config.http_host, config.http_port)
    asyncio.run(_main(config))
