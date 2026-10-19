from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from vaillant_eebus.log_store import LogCategory, LogStore


async def test_entries_come_back_newest_first(log_store: LogStore) -> None:
    log_store.add(LogCategory.MODE, "App gestartet im Demo-Betrieb.")
    log_store.add(LogCategory.WRITE, "Würde senden: Zone 2: Solltemperatur 22,5 °C")

    page = await log_store.page()

    assert [(entry.category, entry.message) for entry in page.entries] == [
        (LogCategory.WRITE, "Würde senden: Zone 2: Solltemperatur 22,5 °C"),
        (LogCategory.MODE, "App gestartet im Demo-Betrieb."),
    ]
    assert page.more is False
    assert page.entries[0].time.tzinfo is not None


async def test_pages_continue_before_the_last_entry(log_store: LogStore) -> None:
    for number in range(5):
        log_store.add(LogCategory.WRITE, f"Eintrag {number}")

    first = await log_store.page(limit=2)
    second = await log_store.page(before=first.entries[-1].id, limit=2)
    third = await log_store.page(before=second.entries[-1].id, limit=2)

    assert [entry.message for entry in first.entries] == ["Eintrag 4", "Eintrag 3"]
    assert [entry.message for entry in second.entries] == ["Eintrag 2", "Eintrag 1"]
    assert [entry.message for entry in third.entries] == ["Eintrag 0"]
    assert (first.more, second.more, third.more) == (True, True, False)


async def test_entries_can_be_filtered_by_category(log_store: LogStore) -> None:
    log_store.add(LogCategory.CONNECTION, "Gateway …7D9F02 verbunden.")
    log_store.add(LogCategory.WRITE, "Gesendet: Warmwasser: Solltemperatur 54 °C")

    page = await log_store.page(category=LogCategory.CONNECTION)

    assert [entry.message for entry in page.entries] == ["Gateway …7D9F02 verbunden."]


async def test_only_the_newest_entries_are_kept(tmp_path: Path) -> None:
    store = LogStore(tmp_path, max_entries=3)
    try:
        for number in range(5):
            store.add(LogCategory.WRITE, f"Eintrag {number}")

        page = await store.page()
    finally:
        store.close()

    assert [entry.message for entry in page.entries] == ["Eintrag 4", "Eintrag 3", "Eintrag 2"]


async def test_the_protocol_survives_a_restart(tmp_path: Path) -> None:
    moment = datetime(2026, 10, 19, 18, 34, 32, tzinfo=UTC)
    first = LogStore(tmp_path, clock=lambda: moment)
    first.add(LogCategory.MODE, "Live-Betrieb eingeschaltet.")
    first.close()

    second = LogStore(tmp_path)
    try:
        page = await second.page()
    finally:
        second.close()

    assert [(entry.time, entry.message) for entry in page.entries] == [(moment, "Live-Betrieb eingeschaltet.")]


async def test_a_corrupt_database_is_kept_aside(tmp_path: Path) -> None:
    (tmp_path / "app.db").write_bytes(b"not a database" * 100)
    store = LogStore(tmp_path)
    try:
        store.add(LogCategory.MODE, "App gestartet im Demo-Betrieb.")
        page = await store.page()
    finally:
        store.close()

    assert [entry.message for entry in page.entries] == ["App gestartet im Demo-Betrieb."]
    assert (tmp_path / "app.db.corrupt").read_bytes().startswith(b"not a database")


def test_entries_after_closing_are_dropped_without_error(tmp_path: Path) -> None:
    store = LogStore(tmp_path)
    store.close()

    store.add(LogCategory.MODE, "zu spät")
