"""Protocol of the app's events and write attempts, shown on the protocol page.

Entries live in SQLite (<data_dir>/app.db). All database work runs in order on
one worker thread: add() returns at once, so the event loop never waits for
the disk, and page() sees every entry added before it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

DB_FILE = "app.db"
# The oldest entries are deleted beyond this count.
MAX_ENTRIES = 10_000
PAGE_SIZE = 50

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS log ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " time TEXT NOT NULL,"
    " category TEXT NOT NULL,"
    " message TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS log_category ON log (category, id)",
)


class LogCategory(StrEnum):
    # Operating mode at start and every change.
    MODE = "mode"
    # Every write attempt and its outcome.
    WRITE = "write"
    # Gateway connected, lost or refusing.
    CONNECTION = "connection"
    # Setpoints, operation modes and boost changed outside this app.
    DEVICE = "device"
    # Rooms, Home Assistant sensors and the export choice.
    SETTINGS = "settings"


@dataclass(frozen=True, slots=True)
class LogEntry:
    id: int
    time: datetime
    category: LogCategory
    # German, shown as is in the UI.
    message: str


@dataclass(frozen=True, slots=True)
class LogPage:
    # Newest first.
    entries: list[LogEntry]
    # True if older entries exist; ask again with before = the last entry's id.
    more: bool


class LogStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        max_entries: int = MAX_ENTRIES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = data_dir / DB_FILE
        self._max_entries = max_entries
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="log-store")
        # Opened and used only on the worker thread.
        self._db: sqlite3.Connection | None = None

    def add(self, category: LogCategory, message: str) -> None:
        """Record an entry; returns without waiting for the database."""
        time = self._clock()
        try:
            self._executor.submit(self._insert, time, category, message)
        except RuntimeError:
            # Shutting down: the entry would not be written any more.
            _LOGGER.debug("Protocol closed, dropped entry: %s", message)

    async def record(self, category: LogCategory, message: str) -> None:
        """Record an entry and wait until it is in the database, e.g. right before
        an action whose trace must survive a crash of the app."""
        time = self._clock()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._insert, time, category, message)

    async def page(
        self, *, category: LogCategory | None = None, before: int | None = None, limit: int = PAGE_SIZE
    ) -> LogPage:
        """Entries older than the entry `before` (all if None), newest first."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._select, category, before, limit)

    def close(self) -> None:
        """Write pending entries, then close the database."""
        try:
            self._executor.submit(self._close)
        except RuntimeError:
            return
        self._executor.shutdown(wait=True)

    # --- worker thread -------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            try:
                self._db = self._open()
            except sqlite3.DatabaseError as err:
                # Like a corrupt settings file: keep it aside and start a new protocol.
                corrupt = self._path.with_name(DB_FILE + ".corrupt")
                os.replace(self._path, corrupt)
                for suffix in ("-wal", "-shm"):
                    self._path.with_name(DB_FILE + suffix).unlink(missing_ok=True)
                _LOGGER.error(
                    "Protocol %s is unreadable (%s); moved to %s, starting a new one", self._path, err, corrupt
                )
                self._db = self._open()
        return self._db

    def _open(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path, autocommit=True)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            for statement in _SCHEMA:
                db.execute(statement)
        except sqlite3.Error:
            db.close()
            raise
        return db

    def _insert(self, time: datetime, category: LogCategory, message: str) -> None:
        try:
            db = self._connection()
            cursor = db.execute(
                "INSERT INTO log (time, category, message) VALUES (?, ?, ?)",
                (time.isoformat(), category.value, message),
            )
            newest = cursor.lastrowid
            if newest is not None and newest > self._max_entries:
                db.execute("DELETE FROM log WHERE id <= ?", (newest - self._max_entries,))
        except sqlite3.Error:
            _LOGGER.exception("Could not write to the protocol: %s", message)

    def _select(self, category: LogCategory | None, before: int | None, limit: int) -> LogPage:
        rows = (
            self._connection()
            .execute(
                "SELECT id, time, category, message FROM log"
                " WHERE (?1 IS NULL OR category = ?1) AND (?2 IS NULL OR id < ?2)"
                " ORDER BY id DESC LIMIT ?3",
                (category.value if category else None, before, limit + 1),
            )
            .fetchall()
        )
        entries = [
            LogEntry(id=row[0], time=datetime.fromisoformat(row[1]), category=LogCategory(row[2]), message=row[3])
            for row in rows[:limit]
        ]
        return LogPage(entries=entries, more=len(rows) > limit)

    def _close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
