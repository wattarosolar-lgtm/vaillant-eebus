from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from vaillant_eebus.log_store import LogCategory, LogEntry, LogStore, csv_document

# A fixed offset: Windows has no time zone database (see test_energy.py).
BERLIN = timezone(timedelta(hours=2), "CEST")


def _ticking(start: datetime, step: timedelta) -> Iterator[datetime]:
    moment = start
    while True:
        yield moment
        moment += step


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


async def test_problems_are_marked_and_can_be_filtered(log_store: LogStore) -> None:
    log_store.add(LogCategory.WRITE, "Gesendet: Warmwasser: Solltemperatur 54 °C")
    log_store.add(LogCategory.WRITE, "Nicht bestätigt nach 30 s: Warmwasser: Solltemperatur 54 °C", problem=True)
    await log_store.record(LogCategory.CONNECTION, "Verbindung zu Gateway …7D9F02 getrennt.", problem=True)

    everything = await log_store.page()
    problems = await log_store.page(problems=True)
    write_problems = await log_store.page(category=LogCategory.WRITE, problems=True)

    assert [entry.problem for entry in everything.entries] == [True, True, False]
    assert [entry.message for entry in problems.entries] == [
        "Verbindung zu Gateway …7D9F02 getrennt.",
        "Nicht bestätigt nach 30 s: Warmwasser: Solltemperatur 54 °C",
    ]
    assert [entry.category for entry in write_problems.entries] == [LogCategory.WRITE]


async def test_only_newer_entries_after_an_id(log_store: LogStore) -> None:
    for number in range(4):
        log_store.add(LogCategory.WRITE, f"Eintrag {number}")
    newest = await log_store.page(limit=2)

    newer = await log_store.page(after=newest.entries[0].id)
    log_store.add(LogCategory.WRITE, "Eintrag 4")
    log_store.add(LogCategory.WRITE, "Eintrag 5")
    again = await log_store.page(after=newest.entries[0].id)

    assert newer.entries == []
    assert [entry.message for entry in again.entries] == ["Eintrag 5", "Eintrag 4"]
    assert again.more is False


async def test_entries_before_a_moment(tmp_path: Path) -> None:
    times = _ticking(datetime(2026, 10, 19, 21, 58, tzinfo=UTC), timedelta(minutes=1))
    store = LogStore(tmp_path, clock=lambda: next(times))
    try:
        for message in ("21:58 UTC", "21:59 UTC", "22:00 UTC", "22:01 UTC"):
            store.add(LogCategory.WRITE, message)
        # Local midnight in Berlin (CEST) is 22:00 UTC.
        page = await store.page(before_time=datetime(2026, 10, 20, 0, 0, tzinfo=BERLIN))
    finally:
        store.close()

    assert [entry.message for entry in page.entries] == ["21:59 UTC", "21:58 UTC"]


async def test_all_entries_come_oldest_first_for_the_download(tmp_path: Path) -> None:
    store = LogStore(tmp_path, max_entries=3)
    try:
        for number in range(5):
            store.add(LogCategory.WRITE, f"Eintrag {number}", problem=number % 2 == 1)
        everything = await store.entries()
        problems = await store.entries(problems=True)
    finally:
        store.close()

    assert [entry.message for entry in everything] == ["Eintrag 2", "Eintrag 3", "Eintrag 4"]
    assert [entry.message for entry in problems] == ["Eintrag 3"]


async def test_a_protocol_without_problem_column_is_upgraded(tmp_path: Path) -> None:
    # The layout of the protocol before 1.0.0.
    db = sqlite3.connect(tmp_path / "app.db")
    db.execute(
        "CREATE TABLE log (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, category TEXT NOT NULL,"
        " message TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO log (time, category, message) VALUES (?, ?, ?)",
        ("2026-10-19T18:34:32+00:00", "mode", "App gestartet im Demo-Betrieb."),
    )
    db.commit()
    db.close()

    store = LogStore(tmp_path)
    try:
        store.add(LogCategory.WRITE, "Nicht ausgeführt: Zone 2: Solltemperatur 22,5 °C", problem=True)
        page = await store.page()
    finally:
        store.close()

    assert [(entry.message, entry.problem) for entry in page.entries] == [
        ("Nicht ausgeführt: Zone 2: Solltemperatur 22,5 °C", True),
        ("App gestartet im Demo-Betrieb.", False),
    ]


def test_the_download_suits_german_spreadsheets() -> None:
    entries = [
        LogEntry(
            id=1,
            time=datetime(2026, 10, 20, 19, 40, 42, tzinfo=UTC),
            category=LogCategory.WRITE,
            message='Abgelehnt: Zone 2; "zu schnell"',
            problem=True,
        ),
        LogEntry(
            id=2,
            time=datetime(2026, 7, 5, 7, 3, 0, tzinfo=UTC),
            category=LogCategory.DEVICE,
            message="Am Gerät geändert:\nWarmwasser 55 °C",
        ),
    ]

    document = csv_document(entries, BERLIN)

    assert document.startswith("﻿".encode())
    assert document.decode("utf-8-sig").split("\r\n") == [
        "Zeit;Kategorie;Problem;Meldung",
        '20.10.2026 21:40:42;Schreiben;ja;"Abgelehnt: Zone 2; ""zu schnell"""',
        '05.07.2026 09:03:00;Gerät;;"Am Gerät geändert:\nWarmwasser 55 °C"',
        "",
    ]
