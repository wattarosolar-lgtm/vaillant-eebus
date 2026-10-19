"""User settings, persisted as JSON in the app's /data directory."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


@dataclass(frozen=True, slots=True)
class Settings:
    # SKI of the gateway the user paired, normalized to upper case.
    paired_ski: str | None = None


class SettingsStore:
    """Loads and atomically saves Settings in <data_dir>/settings.json."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / SETTINGS_FILE

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Settings:
        """Return the saved settings, or defaults if there are none.

        A corrupt file is kept as settings.json.corrupt and replaced by defaults,
        so the app still starts; the user then has to pair again.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        try:
            data = json.loads(raw)
            paired_ski = data.get("paired_ski")
            if paired_ski is not None and not isinstance(paired_ski, str):
                raise ValueError("paired_ski is not a string")
            return Settings(paired_ski=paired_ski)
        except (ValueError, AttributeError) as err:
            corrupt = self._path.with_name(SETTINGS_FILE + ".corrupt")
            os.replace(self._path, corrupt)
            _LOGGER.error("Settings file %s is unreadable (%s); moved to %s, using defaults", self._path, err, corrupt)
            return Settings()

    def save(self, settings: Settings) -> None:
        """Write settings atomically: temporary file in the same directory, then rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=SETTINGS_FILE, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
