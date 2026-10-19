from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.fake_bridge import FakeBridge, start_fake_bridge


@pytest.fixture
async def fake_bridge() -> AsyncIterator[FakeBridge]:
    bridge = await start_fake_bridge()
    yield bridge
    await bridge.stop()
