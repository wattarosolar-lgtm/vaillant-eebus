from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from tests.fake_bridge import FakeBridge, start_fake_bridge
from tests.support import FAST_BACKOFF
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.log_store import LogStore
from vaillant_eebus.pairing import PairingService
from vaillant_eebus.settings import SettingsStore
from vaillant_eebus.state import StateService
from vaillant_eebus.write_gate import WriteGate


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
def log_store(tmp_path: Path) -> Iterator[LogStore]:
    store = LogStore(tmp_path)
    yield store
    store.close()


@pytest.fixture
def pairing(bridge_client: BridgeClient, settings_store: SettingsStore, log_store: LogStore) -> PairingService:
    return PairingService(bridge_client, settings_store, app_version="0.2.0-test", log=log_store)


@pytest.fixture
def state(bridge_client: BridgeClient, pairing: PairingService) -> StateService:
    return StateService(bridge_client, pairing, refresh_interval=60.0, debounce=0.05)


@pytest.fixture
def gate(
    bridge_client: BridgeClient,
    pairing: PairingService,
    state: StateService,
    settings_store: SettingsStore,
    log_store: LogStore,
) -> WriteGate:
    return WriteGate(bridge_client, pairing, state, settings_store, log_store)
