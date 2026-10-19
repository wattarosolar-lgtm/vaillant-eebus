from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.fake_bridge import FakeBridge, start_fake_bridge
from tests.support import FAST_BACKOFF
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import SettingsStore


@pytest.fixture
async def fake_bridge() -> AsyncIterator[FakeBridge]:
    bridge = await start_fake_bridge()
    yield bridge
    await bridge.stop()


@pytest.fixture
async def bridge_client(fake_bridge: FakeBridge) -> AsyncIterator[BridgeClient]:
    async with BridgeClient(fake_bridge.address, FAST_BACKOFF) as client:
        yield client


@pytest.fixture
def settings_store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path)


@pytest.fixture
def pairing(bridge_client: BridgeClient, settings_store: SettingsStore) -> PairingService:
    return PairingService(bridge_client, settings_store, app_version="0.2.0-test")
