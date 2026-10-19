from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import pytest
from eebus.v1.device_service_pb2 import DEVICE_READINESS_UNTRUSTED

from tests.fake_bridge import FAKE_SKI, GATEWAY_SKI, FakeBridge
from tests.support import FAST_BACKOFF, eventually, free_port
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.pairing import ConnectionState, NotPairedError, PairingService
from vaillant_eebus.settings import Settings, SettingsStore

OTHER_SKI = "0123456789ABCDEF0123456789ABCDEF01234567"


async def test_status_without_pairing_lists_discovered_gateways(pairing: PairingService) -> None:
    status = await pairing.status()

    assert status.state is ConnectionState.NOT_PAIRED
    assert status.bridge.reachable
    assert status.bridge.ski == FAKE_SKI
    assert status.paired is None
    assert [(g.ski, g.model, g.host) for g in status.discovered] == [(GATEWAY_SKI, "VR940f", "192.168.178.40")]


async def test_pair_registers_and_saves_the_gateway(
    pairing: PairingService, fake_bridge: FakeBridge, settings_store: SettingsStore
) -> None:
    status = await pairing.pair(GATEWAY_SKI.lower())

    assert fake_bridge.device.register_calls == [GATEWAY_SKI]
    assert settings_store.load() == Settings(paired_ski=GATEWAY_SKI)
    assert status.state is ConnectionState.DISCONNECTED
    assert status.paired is not None
    assert status.paired.model == "VR940f"


async def test_status_after_the_gateway_accepted_shows_paired_details(
    pairing: PairingService, fake_bridge: FakeBridge
) -> None:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)

    status = await pairing.status()

    assert status.state is ConnectionState.CONNECTED
    assert status.paired is not None
    assert (status.paired.software_revision, status.paired.host) == ("0357.40.31", "192.168.178.40")


async def test_pairing_another_gateway_unregisters_the_previous_one(
    pairing: PairingService, fake_bridge: FakeBridge
) -> None:
    await pairing.pair(GATEWAY_SKI)
    await pairing.pair(OTHER_SKI)

    assert fake_bridge.device.unregister_calls == [GATEWAY_SKI]
    assert fake_bridge.device.registered == {OTHER_SKI}
    assert pairing.paired_ski == OTHER_SKI


async def test_pair_rejects_malformed_ski(pairing: PairingService, fake_bridge: FakeBridge) -> None:
    with pytest.raises(ValueError, match="40 hexadecimal"):
        await pairing.pair("not-a-ski")

    assert fake_bridge.device.register_calls == []


async def test_status_reports_trust_removed(pairing: PairingService, fake_bridge: FakeBridge) -> None:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.readiness[GATEWAY_SKI] = DEVICE_READINESS_UNTRUSTED

    assert (await pairing.status()).state is ConnectionState.TRUST_REMOVED


async def test_status_re_registers_when_the_bridge_lost_the_gateway(
    pairing: PairingService, fake_bridge: FakeBridge
) -> None:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.restart()

    status = await pairing.status()

    assert status.state is ConnectionState.DISCONNECTED
    assert fake_bridge.device.register_calls == [GATEWAY_SKI, GATEWAY_SKI]
    assert GATEWAY_SKI in fake_bridge.device.registered


async def test_status_without_bridge_keeps_the_paired_gateway(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.save(Settings(paired_ski=GATEWAY_SKI))
    async with BridgeClient(f"127.0.0.1:{free_port()}", FAST_BACKOFF) as unreachable:
        status = await PairingService(unreachable, store, "test").status()

    assert status.state is ConnectionState.BRIDGE_UNAVAILABLE
    assert not status.bridge.reachable
    assert status.paired is not None and status.paired.ski == GATEWAY_SKI


async def test_run_registers_the_stored_gateway_again_after_a_bridge_restart(
    bridge_client: BridgeClient,
    fake_bridge: FakeBridge,
    settings_store: SettingsStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="vaillant_eebus")
    settings_store.save(Settings(paired_ski=GATEWAY_SKI))
    runner = asyncio.create_task(PairingService(bridge_client, settings_store, "test").run())
    try:
        await eventually(lambda: fake_bridge.device.subscriber_count == 1)
        assert fake_bridge.device.register_calls == [GATEWAY_SKI]
        assert f"local SKI {FAKE_SKI}" in caplog.text

        fake_bridge.device.restart()

        await eventually(lambda: fake_bridge.device.register_calls == [GATEWAY_SKI, GATEWAY_SKI])
        await eventually(lambda: fake_bridge.device.subscriber_count == 1)
        assert GATEWAY_SKI in fake_bridge.device.registered
    finally:
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


async def test_run_logs_gateway_connection_events(
    pairing: PairingService, fake_bridge: FakeBridge, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="vaillant_eebus")
    runner = asyncio.create_task(pairing.run())
    try:
        await eventually(lambda: fake_bridge.device.subscriber_count == 1)
        fake_bridge.device.connect(GATEWAY_SKI)
        await eventually(lambda: "Gateway …7D9F02 connected" in caplog.text)
    finally:
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


async def test_diagnostic_dump_requires_pairing(pairing: PairingService) -> None:
    with pytest.raises(NotPairedError):
        await pairing.diagnostic_dump()


async def test_diagnostic_dump_wraps_the_bridge_structure(pairing: PairingService, fake_bridge: FakeBridge) -> None:
    await pairing.pair(GATEWAY_SKI)
    fake_bridge.device.connect(GATEWAY_SKI)

    dump = await pairing.diagnostic_dump()

    assert dump.filename == "vaillant-eebus-diagnose-20261019-080000.json"
    document = json.loads(dump.content)
    assert document["app_version"] == "0.2.0-test"
    assert document["bridge_build"] == "fake"
    assert document["gateway"]["model"] == "VR940f"
    assert document["gateway"]["ski"] == "…7D9F02"
    assert [entity["address"] for entity in document["device"]["entities"]] == ["2:1", "2:2"]
