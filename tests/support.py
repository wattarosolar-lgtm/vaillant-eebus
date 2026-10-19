"""Shared helpers for the backend tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable

from vaillant_eebus.bridge_client import Backoff

# Short delays so retry tests finish quickly.
FAST_BACKOFF = Backoff(initial=0.05, maximum=0.2)
EVENTUALLY_TIMEOUT = 5.0  # seconds


async def eventually(condition: Callable[[], bool]) -> None:
    """Wait until condition() is true; fail the test after EVENTUALLY_TIMEOUT.

    Polls on purpose: the conditions are plain state (log text, call counters)
    with no event to await.
    """
    async with asyncio.timeout(EVENTUALLY_TIMEOUT):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port
