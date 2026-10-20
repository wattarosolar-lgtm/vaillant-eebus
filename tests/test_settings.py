from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from vaillant_eebus.settings import ExportGroup, OperatingMode, Room, Settings, SettingsStore


def test_load_without_file_returns_defaults(tmp_path: Path) -> None:
    assert SettingsStore(tmp_path).load() == Settings()


def test_a_new_installation_starts_in_demo_mode(tmp_path: Path) -> None:
    assert SettingsStore(tmp_path).current.operating_mode is OperatingMode.DEMO


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.save(Settings(paired_ski="6C2B9E41D7A0F3851E2C4B6D8F0A1C3E5B7D9F02"))

    assert store.load() == Settings(paired_ski="6C2B9E41D7A0F3851E2C4B6D8F0A1C3E5B7D9F02")
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "paired_ski": "6C2B9E41D7A0F3851E2C4B6D8F0A1C3E5B7D9F02",
        "operating_mode": "demo",
        "rooms": [],
        "heat_energy_entities": [],
        "export": ["energy", "hot_water", "status", "temperatures", "zones"],
    }


def test_home_assistant_choices_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    settings = Settings(
        paired_ski="A" * 40,
        rooms=(
            Room(entity_id="sensor.wohnzimmer_temperatur", name="Wohnzimmer", zone_id="5.1.1"),
            Room(entity_id="sensor.keller_temperatur", name="Keller"),
        ),
        heat_energy_entities=("sensor.waerme_heizung", "sensor.waerme_warmwasser"),
        export=frozenset({ExportGroup.STATUS, ExportGroup.POWER_LIMITS}),
    )
    store.save(settings)

    assert SettingsStore(tmp_path).load() == settings


def test_the_single_heat_sensor_of_0_6_0_is_taken_over(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text(json.dumps({"paired_ski": "A" * 40, "heat_energy_entity": "sensor.waerme"}), encoding="utf-8")

    assert store.load().heat_energy_entities == ("sensor.waerme",)


def test_invalid_home_assistant_choices_keep_the_pairing(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text(
        json.dumps(
            {
                "paired_ski": "A" * 40,
                "operating_mode": "live",
                "rooms": [
                    {"entity_id": "sensor.ok", "name": "Bad", "zone_id": "6.1.1"},
                    {"entity_id": "Not An Entity", "name": "Flur"},
                    {"entity_id": "sensor.ok", "name": "Doppelt"},
                    {"entity_id": "sensor.leer", "name": " "},
                    "kein Raum",
                ],
                "heat_energy_entities": ["sensor.ok", 42, "Kein Sensor", "sensor.ok"],
                "export": ["status", "unbekannt"],
            }
        ),
        encoding="utf-8",
    )

    settings = store.load()

    assert (settings.paired_ski, settings.operating_mode) == ("A" * 40, OperatingMode.LIVE)
    assert settings.rooms == (Room(entity_id="sensor.ok", name="Bad", zone_id="6.1.1"),)
    assert settings.heat_energy_entities == ("sensor.ok",)
    assert settings.export == frozenset({ExportGroup.STATUS})
    assert not store.path.with_name("settings.json.corrupt").exists()


def test_settings_of_an_older_version_load_in_demo_mode(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text(json.dumps({"paired_ski": "A" * 40}), encoding="utf-8")

    assert store.load() == Settings(paired_ski="A" * 40, operating_mode=OperatingMode.DEMO)


def test_the_chosen_mode_survives_a_restart(tmp_path: Path) -> None:
    SettingsStore(tmp_path).update(lambda settings: replace(settings, operating_mode=OperatingMode.LIVE))

    assert SettingsStore(tmp_path).current.operating_mode is OperatingMode.LIVE


def test_update_keeps_the_changes_of_other_parts(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.update(lambda settings: replace(settings, paired_ski="A" * 40))
    store.update(lambda settings: replace(settings, operating_mode=OperatingMode.LIVE))

    assert store.load() == Settings(paired_ski="A" * 40, operating_mode=OperatingMode.LIVE)


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


def test_unknown_mode_counts_as_corrupt_and_falls_back_to_demo(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text(json.dumps({"paired_ski": "A" * 40, "operating_mode": "turbo"}), encoding="utf-8")

    assert store.current.operating_mode is OperatingMode.DEMO
    assert (tmp_path / "settings.json.corrupt").exists()


def test_wrong_value_type_counts_as_corrupt(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.path.write_text(json.dumps({"paired_ski": 42}), encoding="utf-8")

    assert store.load() == Settings()
    assert (tmp_path / "settings.json.corrupt").exists()
