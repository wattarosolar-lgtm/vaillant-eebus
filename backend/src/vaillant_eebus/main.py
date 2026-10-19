"""Backend entry point (`python -m vaillant_eebus`), started by the s6 service `backend`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

from vaillant_eebus.bridge_client import DEFAULT_ADDRESS, BridgeClient

_LOGGER = logging.getLogger("vaillant_eebus")

_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=_LOG_LEVELS.get(level_name.lower(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def run_backend(bridge: BridgeClient) -> None:
    """Wait for the bridge, report its identity, then keep running until cancelled."""
    info = await bridge.wait_until_ready()
    _LOGGER.info(
        "Bridge reachable at %s: build %s, API %d.%d, local SKI %s",
        bridge.address,
        info.bridge_build_version or "unknown",
        info.api_major,
        info.api_minor,
        info.local_ski,
    )
    await asyncio.Event().wait()


async def _main(address: str) -> None:
    main_task = asyncio.current_task()
    assert main_task is not None
    # s6 stops the service with SIGTERM. add_signal_handler is unavailable on
    # Windows dev machines, where Ctrl+C still stops the backend.
    with contextlib.suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, main_task.cancel)
    try:
        async with BridgeClient(address) as bridge:
            await run_backend(bridge)
    except asyncio.CancelledError:
        _LOGGER.info("Backend stopped")


def run() -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "info"))
    asyncio.run(_main(os.environ.get("BRIDGE_ADDRESS", DEFAULT_ADDRESS)))
