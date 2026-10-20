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
from vaillant_eebus.energy import EnergyMeter, local_zone
from vaillant_eebus.ha_client import SUPERVISOR_CORE_URL, HomeAssistantClient
from vaillant_eebus.home_assistant import HomeAssistant
from vaillant_eebus.log_store import LogStore
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import WriteGate

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
    # EEBUS serial file written by the bridge's s6 service; None skips the
    # bridge identity check (development against the fake bridge).
    bridge_serial_file: Path | None = None
    # SHIP port of this app's bridge, probed for another listener while the bridge is down.
    ship_port: int | None = None
    # Home Assistant's time zone; the Supervisor passes it to every app as TZ.
    time_zone: str | None = None
    # Home Assistant Core; the Supervisor passes the token as SUPERVISOR_TOKEN
    # (config.yaml homeassistant_api). Without a token the app runs without HA.
    ha_url: str = SUPERVISOR_CORE_URL
    ha_token: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BackendConfig:
        defaults = cls()
        static_dir = env.get("STATIC_DIR")
        trusted = env.get("TRUSTED_CLIENTS")
        serial_file = env.get("BRIDGE_SERIAL_FILE")
        return cls(
            bridge_address=env.get("BRIDGE_ADDRESS", defaults.bridge_address),
            data_dir=Path(env.get("DATA_DIR", str(defaults.data_dir))),
            http_host=env.get("HTTP_HOST", defaults.http_host),
            http_port=_port(env, "HTTP_PORT") or defaults.http_port,
            static_dir=Path(static_dir) if static_dir else None,
            trusted_clients=(
                frozenset(client.strip() for client in trusted.split(",") if client.strip())
                if trusted
                else defaults.trusted_clients
            ),
            app_version=env.get("APP_VERSION", defaults.app_version),
            log_level=env.get("LOG_LEVEL", defaults.log_level),
            bridge_serial_file=Path(serial_file) if serial_file else None,
            ship_port=_port(env, "SHIP_PORT"),
            time_zone=env.get("TZ") or None,
            ha_url=env.get("HA_URL", defaults.ha_url),
            ha_token=env.get("SUPERVISOR_TOKEN") or None,
        )


def _port(env: Mapping[str, str], name: str) -> int | None:
    """The port in env[name], None if unset; a clear error for anything else."""
    value = env.get(name)
    if not value:
        return None
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise ValueError(f"{name} must be a port number between 1 and 65535, got {value!r}")
    return int(value)


def configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=_LOG_LEVELS.get(level_name.lower(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def run_backend(config: BackendConfig, bridge: BridgeClient) -> None:
    """Serve the ingress UI and keep the bridge pairing alive until stopped."""
    settings = SettingsStore(config.data_dir)
    log = LogStore(config.data_dir)
    pairing = PairingService(
        bridge,
        settings,
        config.app_version,
        bridge_serial_file=config.bridge_serial_file,
        ship_port=config.ship_port,
        log=log,
    )
    state = StateService(bridge, pairing)
    gate = WriteGate(bridge, pairing, state, settings, log)
    energy = EnergyMeter(state, config.data_dir, zone=local_zone(config.time_zone))
    ha_client = HomeAssistantClient(config.ha_url, config.ha_token, log=log) if config.ha_token else None
    if ha_client is None:
        _LOGGER.info("No Supervisor token: running without Home Assistant")
    home_assistant = HomeAssistant(settings, state, energy, log, ha_client)
    app = create_app(
        pairing,
        state,
        gate,
        log,
        energy,
        home_assistant,
        static_dir=config.static_dir,
        trusted_clients=config.trusted_clients,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.http_host,
            port=config.http_port,
            log_config=None,
            access_log=False,
            lifespan="off",
            # Open event streams would otherwise hold up the shutdown forever.
            timeout_graceful_shutdown=3,
        )
    )
    try:
        async with contextlib.AsyncExitStack() as stack, asyncio.TaskGroup() as tasks:
            if ha_client is not None:
                await stack.enter_async_context(ha_client)
            background = [
                tasks.create_task(pairing.run()),
                tasks.create_task(state.run()),
                tasks.create_task(gate.run()),
                tasks.create_task(energy.run()),
                tasks.create_task(home_assistant.run()),
            ]
            # Returns when uvicorn handles SIGTERM; it re-raises the signal afterwards.
            await server.serve()
            for task in background:
                task.cancel()
    finally:
        energy.close()
        log.close()


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
