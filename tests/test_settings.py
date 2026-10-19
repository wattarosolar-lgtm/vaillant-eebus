from __future__ import annotations

import json
from pathlib import Path

from vaillant_eebus.settings import Settings, SettingsStore


def test_load_without_file_returns_defaults(tmp_path: Path) -> None:
    assert SettingsStore(tmp_path).load() == Settings()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.save(Settings(paired_ski="6C2B9E41D7A0F3851E2C4B6D8F0A1C3E5B7D9F02"))

    assert store.load() == Settings(paired_ski="6C2B9E41D7A0F3851E2C4B6D8F0A1C3E5B7D9F02")
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "paired_ski": "6C2B9E41D7A0F3851E2C4B6D8F0A1C3E5B7D9F02"
    }


def test_save_leaves_no_temporary_files(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.save(Settings(paired_ski="A" * 40))
    store.save(Settings(paired_ski="B" * 40))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


def test_corrupt_file_is_kept_aside_and_defaults_are_used(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text("{not json", encoding="utf-8")

    assert store.load() == Settings()
    assert not store.path.exists()
    assert (tmp_path / "settings.json.corrupt").read_text(encoding="utf-8") == "{not json"


def test_wrong_value_type_counts_as_corrupt(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text(json.dumps({"paired_ski": 42}), encoding="utf-8")

    assert store.load() == Settings()
    assert (tmp_path / "settings.json.corrupt").exists()
