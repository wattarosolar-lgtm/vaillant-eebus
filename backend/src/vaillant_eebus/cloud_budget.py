"""Commands to the Vaillant cloud per day (1.5.0, plan F0.6).

The myVAILLANT integration reaches the heat pump through Vaillant's cloud, which
allows only so many calls. The write gate counts every command it sends over
myVAILLANT - from the UI, the planner and the floor heating's off/auto - per
local day. From 80 % of the daily limit the app warns; at the limit the planner
sends only what returns the zones to their own control (end its quick veto,
zone back to auto). The user's own changes are counted, never refused.

In preview the planner counts what it would have sent, so the preview week
shows how many commands the zone control needs.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import Any

from vaillant_eebus.log_store import LogCategory, LogStore
from vaillant_eebus.settings import SettingsStore

_LOGGER = logging.getLogger(__name__)

BUDGET_FILE = "cloud_budget.json"
# Share of the daily limit from which the app warns.
WARN_SHARE = 0.8


class BudgetLevel(StrEnum):
    OK = "ok"
    # 80 % of the limit reached: the app warns.
    WARNING = "warning"
    # The limit reached: the planner sends only what returns control.
    FULL = "full"


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    day: date
    # Commands sent to myVAILLANT today.
    sent: int
    # Commands the planner would have sent today (preview).
    would: int
    limit: int
    warn_from: int
    level: BudgetLevel


class CloudBudget:
    def __init__(
        self,
        data_dir: Path,
        settings: SettingsStore,
        log: LogStore,
        *,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = data_dir / BUDGET_FILE
        self._settings = settings
        self._log = log
        self._zone = zone
        self._clock = clock
        self._day: date | None = None
        self._sent = 0
        self._would = 0
        # The level already reported today, so each warning comes once a day.
        self._reported = BudgetLevel.OK
        self._load()

    @property
    def limit(self) -> int:
        return self._settings.current.hems.cloud_commands_max

    @property
    def level(self) -> BudgetLevel:
        self._roll()
        return self._level(self._sent)

    def allows(self, *, fallback: bool) -> bool:
        """Whether the planner may send now; what returns control always may."""
        return fallback or self.level is not BudgetLevel.FULL

    def count(self) -> None:
        """A command goes to myVAILLANT now."""
        self._roll()
        self._sent += 1
        self._save()
        level = self._level(self._sent)
        if level is self._reported or level is BudgetLevel.OK:
            return
        self._reported = level
        if level is BudgetLevel.WARNING:
            self._log.add(
                LogCategory.HEMS,
                f"Befehle an myVAILLANT heute: {self._sent} von {self.limit}. Ab {self.limit} sendet der Planer heute "
                "nur noch, was die Zonen zurückgibt (Quick-Veto beenden, Zone auf Auto).",
                problem=True,
            )
        else:
            self._log.add(
                LogCategory.HEMS,
                f"Tagesgrenze für Befehle an myVAILLANT erreicht ({self._sent} von {self.limit}). Der Planer sendet "
                "heute nur noch, was die Zonen zurückgibt; Änderungen von Hand gehen weiter.",
                problem=True,
            )

    def count_would(self) -> None:
        """The planner would send a command now (preview)."""
        self._roll()
        self._would += 1
        self._save()

    def status(self) -> BudgetStatus:
        self._roll()
        day = self._day
        assert day is not None
        return BudgetStatus(
            day=day,
            sent=self._sent,
            would=self._would,
            limit=self.limit,
            warn_from=self._warn_from(),
            level=self._level(self._sent),
        )

    # --- internals -------------------------------------------------------------------------

    def _warn_from(self) -> int:
        return math.ceil(self.limit * WARN_SHARE)

    def _level(self, sent: int) -> BudgetLevel:
        if sent >= self.limit:
            return BudgetLevel.FULL
        if sent >= self._warn_from():
            return BudgetLevel.WARNING
        return BudgetLevel.OK

    def _roll(self) -> None:
        today = self._clock().astimezone(self._zone).date()
        if self._day != today:
            self._day, self._sent, self._would, self._reported = today, 0, 0, BudgetLevel.OK

    def _load(self) -> None:
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            _LOGGER.warning("Reading the cloud budget failed: %s", err)
            return
        if not isinstance(raw, dict):
            return
        try:
            day = date.fromisoformat(raw["day"])
            sent, would = int(raw["sent"]), int(raw.get("would", 0))
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning("The cloud budget file is invalid; counting starts at 0")
            return
        if day == self._clock().astimezone(self._zone).date():
            self._day, self._sent, self._would = day, max(0, sent), max(0, would)
            self._reported = self._level(self._sent)

    def _save(self) -> None:
        payload = {"day": self._day.isoformat() if self._day else None, "sent": self._sent, "would": self._would}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=BUDGET_FILE, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(payload, file)
                os.replace(tmp, self._path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as err:
            _LOGGER.warning("Saving the cloud budget failed: %s", err)
