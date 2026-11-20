"""Five-minute samples of rooms and system for the room model (SQLite, 21 days).

Series are named "room:<entity>:<value>" (temperature, humidity, valve,
thermostat setpoint, room setpoint) and "outdoor", "power", "flow:<zone>",
"flow_setpoint:<zone>", "buffer". The database lives next to the protocol in
/data (hems.db); a damaged one is set aside and started anew.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

DB_FILE = "hems.db"
KEEP = timedelta(days=21)
STEP = timedelta(minutes=5)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS samples (t INTEGER NOT NULL, series TEXT NOT NULL, value REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS samples_series ON samples (series, t)",
)


class SampleStore:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / DB_FILE
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._db = self._open()
            except sqlite3.DatabaseError as err:
                corrupt = self._path.with_name(DB_FILE + ".corrupt")
                _LOGGER.error("Sample database %s is damaged (%s); moved to %s", self._path, err, corrupt)
                os.replace(self._path, corrupt)
                self._db = self._open()
        return self._db

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        for statement in _SCHEMA:
            db.execute(statement)
        db.commit()
        return db

    def add(self, moment: datetime, values: Mapping[str, float]) -> None:
        stamp = int(moment.timestamp())
        with self._lock:
            db = self._connection()
            db.executemany(
                "INSERT INTO samples (t, series, value) VALUES (?, ?, ?)",
                [(stamp, series, value) for series, value in values.items()],
            )
            db.execute("DELETE FROM samples WHERE t < ?", (int((moment - KEEP).timestamp()),))
            db.commit()

    def series(self, names: Iterable[str], since: datetime) -> dict[str, list[tuple[datetime, float]]]:
        wanted = sorted(set(names))
        result: dict[str, list[tuple[datetime, float]]] = {name: [] for name in wanted}
        if not wanted:
            return result
        marks = ",".join("?" * len(wanted))
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"SELECT series, t, value FROM samples WHERE series IN ({marks}) AND t >= ? ORDER BY t",
                    [*wanted, int(since.timestamp())],
                )
                .fetchall()
            )
        for series, stamp, value in rows:
            result[series].append((datetime.fromtimestamp(stamp, UTC), float(value)))
        return result

    async def add_async(self, moment: datetime, values: Mapping[str, float]) -> None:
        await asyncio.to_thread(self.add, moment, values)

    async def series_async(self, names: Iterable[str], since: datetime) -> dict[str, list[tuple[datetime, float]]]:
        names = list(names)
        return await asyncio.to_thread(self.series, names, since)

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None


def room_series(entity_id: str, value: str) -> str:
    return f"room:{entity_id}:{value}"
