from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from tests.fake_bridge import FAKE_SKI, FakeBridge
from tests.support import FAST_BACKOFF, eventually
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.main import run_backend


async def test_run_backend_logs_full_bridge_ski_and_keeps_running(
    fake_bridge: FakeBridge, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="vaillant_eebus")
    async with BridgeClient(fake_bridge.address, FAST_BACKOFF) as client:
        backend = asyncio.create_task(run_backend(client))
        try:
            await eventually(lambda: f"local SKI {FAKE_SKI}" in caplog.text)
            await asyncio.sleep(0.1)
            assert not backend.done()
        finally:
            backend.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await backend
