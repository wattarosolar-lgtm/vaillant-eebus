from __future__ import annotations

import ipaddress
import os
from pathlib import Path

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from tests.fake_bridge import start_fake_bridge
from tests.support import FAST_BACKOFF
from vaillant_eebus.bridge_auth import (
    CERT_FILE,
    KEY_FILE,
    SERVER_NAME,
    TOKEN_FILE,
    BridgeCredentials,
    ensure,
    main,
)
from vaillant_eebus.bridge_client import BridgeClient
from vaillant_eebus.pairing import ConnectionState, PairingService
from vaillant_eebus.settings import SettingsStore


def test_the_credentials_are_created_once(tmp_path: Path) -> None:
    directory = tmp_path / "eebus" / "grpc"

    created = ensure(directory)
    first = {name: (directory / name).read_bytes() for name in (CERT_FILE, KEY_FILE, TOKEN_FILE)}
    again = ensure(directory)

    assert (created, again) == (True, False)
    assert {name: (directory / name).read_bytes() for name in first} == first
    assert len(first[TOKEN_FILE]) >= 40
    assert sorted(path.name for path in (tmp_path / "eebus").iterdir()) == ["grpc"]
    if os.name == "posix":
        assert {(directory / name).stat().st_mode & 0o777 for name in first} == {0o600}
        assert directory.stat().st_mode & 0o777 == 0o700


def test_incomplete_credentials_are_replaced_together(tmp_path: Path) -> None:
    directory = tmp_path / "grpc"
    ensure(directory)
    old_certificate = (directory / CERT_FILE).read_bytes()
    (directory / TOKEN_FILE).unlink()
    (tmp_path / "grpc.tmp-leftover").mkdir()

    assert ensure(directory) is True
    assert (directory / TOKEN_FILE).is_file()
    assert (directory / CERT_FILE).read_bytes() != old_certificate
    assert not (tmp_path / "grpc.tmp-leftover").exists()


def test_the_certificate_names_the_bridge_and_matches_its_key(tmp_path: Path) -> None:
    main([str(tmp_path / "grpc")])

    certificate = x509.load_pem_x509_certificate((tmp_path / "grpc" / CERT_FILE).read_bytes())
    key = serialization.load_pem_private_key((tmp_path / "grpc" / KEY_FILE).read_bytes(), password=None)
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value

    assert names.get_values_for_type(x509.DNSName) == [SERVER_NAME]
    assert names.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    assert key.public_key() == certificate.public_key()
    assert certificate.not_valid_after_utc.year > 2100


async def test_the_backend_talks_to_the_bridge_with_tls_and_token(tmp_path: Path) -> None:
    ensure(tmp_path / "grpc")
    credentials = BridgeCredentials.load(tmp_path / "grpc")
    bridge = await start_fake_bridge(credentials_dir=tmp_path / "grpc")
    try:
        async with BridgeClient(bridge.address, FAST_BACKOFF, credentials=credentials) as client:
            info = await client.get_server_info()
            discovered = await client.list_discovered()
        wrong_token = BridgeCredentials(certificate=credentials.certificate, token="geraten")
        async with BridgeClient(bridge.address, FAST_BACKOFF, credentials=wrong_token) as client:
            with pytest.raises(grpc.aio.AioRpcError) as unary:
                await client.get_server_info()
            with pytest.raises(grpc.aio.AioRpcError) as stream:
                async for _event in client.device_events():
                    pass
    finally:
        await bridge.stop()

    assert info.local_ski
    assert discovered
    assert unary.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert stream.value.code() is grpc.StatusCode.UNAUTHENTICATED


async def test_a_bridge_without_this_apps_certificate_counts_as_foreign(
    tmp_path: Path, settings_store: SettingsStore
) -> None:
    ensure(tmp_path / "ours")
    ensure(tmp_path / "theirs")
    credentials = BridgeCredentials.load(tmp_path / "ours")
    plaintext = await start_fake_bridge()
    other_certificate = await start_fake_bridge(credentials_dir=tmp_path / "theirs")
    states = []
    try:
        for bridge in (plaintext, other_certificate):
            async with BridgeClient(bridge.address, FAST_BACKOFF, credentials=credentials) as client:
                status = await PairingService(client, settings_store, app_version="1.0.0-test").status()
                states.append((status.state, status.bridge.ski))
    finally:
        await plaintext.stop()
        await other_certificate.stop()

    # Never the other bridge's SKI: the user would trust it in myVAILLANT.
    assert states == [(ConnectionState.FOREIGN_BRIDGE, ""), (ConnectionState.FOREIGN_BRIDGE, "")]


async def test_a_missing_bridge_is_unavailable_not_foreign(tmp_path: Path, settings_store: SettingsStore) -> None:
    ensure(tmp_path / "grpc")
    bridge = await start_fake_bridge(credentials_dir=tmp_path / "grpc")
    address = bridge.address
    await bridge.stop()

    async with BridgeClient(address, FAST_BACKOFF, credentials=BridgeCredentials.load(tmp_path / "grpc")) as client:
        status = await PairingService(client, settings_store, app_version="1.0.0-test").status()

    assert status.state is ConnectionState.BRIDGE_UNAVAILABLE
