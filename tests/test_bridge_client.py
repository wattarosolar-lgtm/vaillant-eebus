from __future__ import annotations

import asyncio

import grpc
import pytest

from tests.fake_bridge import FAKE_SKI, FakeBridge, start_fake_bridge
from tests.support import FAST_BACKOFF, eventually, free_port
from vaillant_eebus.bridge_client import Backoff, BridgeClient


def test_backoff_doubles_up_to_maximum() -> None:
    delays = Backoff(initial=1, maximum=30).delays()
    assert [next(delays) for _ in range(7)] == [1, 2, 4, 8, 16, 30, 30]


async def test_get_server_info_returns_bridge_identity(fake_bridge: FakeBridge) -> None:
    async with BridgeClient(fake_bridge.address, FAST_BACKOFF) as client:
        info = await client.get_server_info()

    assert info.local_ski == FAKE_SKI


async def test_wait_until_ready_waits_for_bridge_to_start() -> None:
    port = free_port()
    async with BridgeClient(f"127.0.0.1:{port}", FAST_BACKOFF) as client:
        waiting = asyncio.create_task(client.wait_until_ready())
        await asyncio.sleep(0.3)
        assert not waiting.done()

        bridge = await start_fake_bridge(port)
        try:
            info = await asyncio.wait_for(waiting, timeout=10)
        finally:
            await bridge.stop()

    assert info.local_ski == FAKE_SKI


async def test_wait_until_ready_retries_while_bridge_is_unavailable(fake_bridge: FakeBridge) -> None:
    fake_bridge.device.fail_with = grpc.StatusCode.UNAVAILABLE
    async with BridgeClient(fake_bridge.address, FAST_BACKOFF) as client:
        waiting = asyncio.create_task(client.wait_until_ready())
        await eventually(lambda: fake_bridge.device.server_info_calls >= 2)
        fake_bridge.device.fail_with = None
        info = await asyncio.wait_for(waiting, timeout=5)

    assert info.local_ski == FAKE_SKI


async def test_wait_until_ready_raises_other_errors_without_retry(fake_bridge: FakeBridge) -> None:
    fake_bridge.device.fail_with = grpc.StatusCode.UNIMPLEMENTED
    async with BridgeClient(fake_bridge.address, FAST_BACKOFF) as client:
        with pytest.raises(grpc.aio.AioRpcError) as raised:
            await client.wait_until_ready()

    assert raised.value.code() is grpc.StatusCode.UNIMPLEMENTED
    assert fake_bridge.device.server_info_calls == 1
