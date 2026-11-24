"""How well each room is controlled, per room and local day (1.4.3, plan-1.4.3.md R1).

Fed every five minutes by the room control with what it has anyway, for each
room in "Vorschau" or "Steuern" (in "Vorschau" the commands are those it would
send). Per day:

- the deviation from the setpoint in effect (setpoint - room, so positive is
  too cold) as a signed mean and as a mean amount;
- the share of time below the lower and above the upper comfort limit;
- overshoots: the room rose from below its setpoint to more than 0.3 K above
  it (with the largest excess); swings: the deviation went from below -0.3 K
  to above +0.3 K or back;
- heating up after each rise of the setpoint (schedule, preheating): minutes
  until the room is within 0.2 K, at most 3 hours; these minutes count as
  heating up only, not as deviation;
- commands to the thermostat and valve movements; the valve's mean opening and
  its time at the stop (at least 90 %: water or flow is missing);
- the longest stretch of at least 2 hours at least 0.3 K off with the valve
  not at its stop and the deviation falling by less than 0.1 K/h (too sluggish,
  for the gain, R3), and the minutes evaluated in "Steuern".

Not counted: a window open and 30 minutes after, a test run or a sweep, the
thermostat unreachable, the room sensor missing. A room measured at its
thermostat (the bathroom) has no deviation, only commands and valve. Days are
kept 30 days. Pure data; the room control keeps it in rooms.json.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

KEEP_DAYS = 30
SHOWN_DAYS = 7
SWING_K = 0.3
OVERSHOOT_K = 0.3
HEATUP_NEAR_K = 0.2
HEATUP_MAX = timedelta(hours=3)
# A rise of the setpoint by at least this starts a heat-up.
HEATUP_RISE_K = 0.1
VALVE_STOP = 90.0
VALVE_CLOSED = 5.0
SLUGGISH_K = 0.3
SLUGGISH_FOR = timedelta(hours=2)
SLUGGISH_RATE = 0.1
TIMES = "\u00d7"


@dataclass
class QualityDay:
    day: date
    # Minutes with a deviation evaluated, and of those in "Steuern".
    minutes: float = 0.0
    control_minutes: float = 0.0
    # Sums over the evaluated minutes (K x min).
    signed_sum: float = 0.0
    abs_sum: float = 0.0
    cold_minutes: float = 0.0
    warm_minutes: float = 0.0
    overshoots: int = 0
    overshoot_max_k: float | None = None
    swings: int = 0
    heatups: list[float] = field(default_factory=list)
    heatups_missed: int = 0
    commands: int = 0
    movements: int = 0
    valve_minutes: float = 0.0
    valve_sum: float = 0.0
    stop_minutes: float = 0.0
    sluggish_min: float = 0.0
    at_thermostat: bool = False
    # The room was in "Steuern" at some time of the day (else the commands are the preview's).
    in_control: bool = False

    @property
    def mean_k(self) -> float | None:
        return round(self.signed_sum / self.minutes, 2) if self.minutes else None

    @property
    def mean_abs_k(self) -> float | None:
        return round(self.abs_sum / self.minutes, 2) if self.minutes else None

    @property
    def cold_share(self) -> float | None:
        return round(100 * self.cold_minutes / self.minutes) if self.minutes else None

    @property
    def warm_share(self) -> float | None:
        return round(100 * self.warm_minutes / self.minutes) if self.minutes else None

    @property
    def valve_mean(self) -> float | None:
        return round(self.valve_sum / self.valve_minutes) if self.valve_minutes else None

    @property
    def stop_share(self) -> float | None:
        return round(100 * self.stop_minutes / self.valve_minutes) if self.valve_minutes else None

    @property
    def heatup_median_min(self) -> float | None:
        return round(statistics.median(self.heatups)) if self.heatups else None

    @property
    def evaluated(self) -> bool:
        """Anything was counted this day."""
        return bool(self.minutes or self.valve_minutes or self.commands)


@dataclass(frozen=True, slots=True)
class QualitySample:
    now: datetime
    day: date
    minutes: float
    # The setpoint in effect (with preheating); None when unknown.
    setpoint_c: float | None
    room_c: float | None
    valve: float | None
    comfort_down_k: float
    comfort_up_k: float
    # Window open or just closed, test run or sweep, thermostat unreachable.
    excluded: bool
    at_thermostat: bool
    control: bool
    # Today's counts so far (commands sent or - in "Vorschau" - that would be sent, valve movements).
    commands: int
    movements: int


@dataclass
class QualityWatch:
    """The days of one room and what it follows between two samples."""

    days: list[QualityDay] = field(default_factory=list)
    today: QualityDay | None = None
    setpoint: float | None = None
    heatup_since: datetime | None = None
    # Below the setpoint since the last overshoot (an overshoot may come), and an overshoot going on.
    armed: bool = False
    overshooting: bool = False
    side: int = 0
    stretch: tuple[datetime, int, float] | None = None

    def roll(self, day: date) -> QualityDay | None:
        """A new day began: the day before is closed and returned (None when there was none to close)."""
        current = self.today
        if current is None or current.day == day:
            return None
        self.today = None
        if not current.evaluated:
            return None
        self.days = [*self.days, current][-KEEP_DAYS:]
        return current

    def update(self, sample: QualitySample) -> QualityDay | None:
        """Takes a sample; returns the day before when this sample is the first of a new day."""
        closed = self.roll(sample.day)
        if self.today is None:
            self.today = QualityDay(sample.day, at_thermostat=sample.at_thermostat)
        day = self.today
        day.at_thermostat = sample.at_thermostat
        day.in_control = day.in_control or sample.control
        day.commands = max(day.commands, sample.commands)
        day.movements = max(day.movements, sample.movements)
        if sample.excluded:
            self._interrupt()
            self.setpoint = sample.setpoint_c
            return closed
        if sample.valve is not None:
            day.valve_minutes += sample.minutes
            day.valve_sum += sample.valve * sample.minutes
            if sample.valve >= VALVE_STOP:
                day.stop_minutes += sample.minutes
        if sample.at_thermostat or sample.setpoint_c is None or sample.room_c is None:
            self._interrupt()
            self.setpoint = sample.setpoint_c
            return closed
        self._deviation(sample, day)
        return closed

    def _deviation(self, sample: QualitySample, day: QualityDay) -> None:
        assert sample.setpoint_c is not None and sample.room_c is not None
        setpoint, room, now = sample.setpoint_c, sample.room_c, sample.now
        deviation = setpoint - room
        if self.setpoint is not None and setpoint >= self.setpoint + HEATUP_RISE_K:
            # The setpoint rose (schedule, preheating): heating up, not deviation.
            self.heatup_since = now
            self.side = 0
            self.stretch = None
        elif self.setpoint is not None and setpoint < self.setpoint - HEATUP_RISE_K:
            self.heatup_since = None
        self.setpoint = setpoint
        if room < setpoint:
            self.armed = True
            self.overshooting = False
        elif room > setpoint + OVERSHOOT_K:
            if self.armed:
                day.overshoots += 1
                self.armed = False
                self.overshooting = True
            if self.overshooting:
                excess = round(room - setpoint, 2)
                day.overshoot_max_k = max(day.overshoot_max_k or excess, excess)
        if self.heatup_since is not None:
            if deviation <= HEATUP_NEAR_K:
                day.heatups.append(round((now - self.heatup_since).total_seconds() / 60))
                self.heatup_since = None
            elif now - self.heatup_since >= HEATUP_MAX:
                day.heatups_missed += 1
                self.heatup_since = None
            else:
                return
        minutes = sample.minutes
        day.minutes += minutes
        if sample.control:
            day.control_minutes += minutes
        day.signed_sum += deviation * minutes
        day.abs_sum += abs(deviation) * minutes
        if room < setpoint - sample.comfort_down_k:
            day.cold_minutes += minutes
        elif room > setpoint + sample.comfort_up_k:
            day.warm_minutes += minutes
        side = 1 if deviation > SWING_K else (-1 if deviation < -SWING_K else 0)
        if side and self.side and side != self.side:
            day.swings += 1
        if side:
            self.side = side
        self._sluggish(sample, day, deviation)

    def _sluggish(self, sample: QualitySample, day: QualityDay, deviation: float) -> None:
        sign = 1 if deviation >= SLUGGISH_K else (-1 if deviation <= -SLUGGISH_K else 0)
        valve = sample.valve
        at_stop = valve is not None and ((sign > 0 and valve >= VALVE_STOP) or (sign < 0 and valve <= VALVE_CLOSED))
        if not sign or at_stop:
            self.stretch = None
            return
        if self.stretch is None or self.stretch[1] != sign:
            self.stretch = (sample.now, sign, abs(deviation))
            return
        since, _sign, start = self.stretch
        length = sample.now - since
        hours = length.total_seconds() / 3600
        if length >= SLUGGISH_FOR and (start - abs(deviation)) / hours < SLUGGISH_RATE:
            day.sluggish_min = max(day.sluggish_min, round(length.total_seconds() / 60))

    def _interrupt(self) -> None:
        """A sample that does not count: nothing that follows the room carries over it."""
        self.armed = self.overshooting = False
        self.side = 0
        self.stretch = None

    def shown(self) -> list[QualityDay]:
        """Today and the last seven days, newest first."""
        days = [*self.days[-SHOWN_DAYS:]]
        if self.today is not None and self.today.evaluated:
            days.append(self.today)
        return list(reversed(days))

    def last(self) -> QualityDay | None:
        return self.days[-1] if self.days else None


def summary(day: QualityDay, name: str, *, preview: bool) -> str:
    """The protocol's line for a day (German)."""
    parts = []
    if day.minutes and not day.at_thermostat:
        mean = day.mean_k or 0.0
        direction = "zu kalt" if mean > 0.05 else ("zu warm" if mean < -0.05 else "am Soll")
        lead = f"im Mittel {_k(abs(mean))} {direction}" if direction != "am Soll" else "im Mittel am Soll"
        parts.append(f"{lead} (Betrag {_k(day.mean_abs_k or 0.0)})")
        parts.append(f"{day.cold_share} % unter, {day.warm_share} % über dem Komfortband")
        if day.overshoots:
            parts.append(f"{day.overshoots} Überschwinger bis {_k(day.overshoot_max_k or 0.0)} über Soll")
        else:
            parts.append("keine Überschwinger")
        parts.append(f"{day.swings}{TIMES} hin und her")
        if day.heatups or day.heatups_missed:
            text = f"Aufheizen {len(day.heatups)}{TIMES}"
            if day.heatups:
                text += f" (Median {day.heatup_median_min} min)"
            if day.heatups_missed:
                text += f", {day.heatups_missed}{TIMES} nicht in 3 h"
            parts.append(text)
    elif day.at_thermostat:
        parts.append("am Thermostat gemessen, nur Befehle und Ventil")
    else:
        parts.append("keine Raumtemperatur bewertet")
    parts.append(f"{day.commands} Befehle" + (" (Vorschau: würde senden)" if preview else ""))
    parts.append(f"{day.movements} Ventilbewegungen")
    if day.valve_mean is not None:
        parts.append(f"Ventil im Mittel {day.valve_mean} % ({day.stop_share} % am Anschlag)")
    return f"Regelgüte {name} am {day.day.strftime('%d.%m.')}: " + ", ".join(parts)


def _k(value: float) -> str:
    return f"{value:.1f} K".replace(".", ",")


# --- persistence -----------------------------------------------------------------------------------------


def day_to_json(day: QualityDay) -> dict[str, Any]:
    raw = asdict(day)
    raw["day"] = day.day.isoformat()
    return raw


def day_from_json(raw: Any) -> QualityDay | None:
    if not isinstance(raw, dict):
        return None
    try:
        day = QualityDay(date.fromisoformat(str(raw["day"])))
    except (KeyError, ValueError):
        return None
    for name, kind in (
        ("minutes", float),
        ("control_minutes", float),
        ("signed_sum", float),
        ("abs_sum", float),
        ("cold_minutes", float),
        ("warm_minutes", float),
        ("overshoots", int),
        ("swings", int),
        ("heatups_missed", int),
        ("commands", int),
        ("movements", int),
        ("valve_minutes", float),
        ("valve_sum", float),
        ("stop_minutes", float),
        ("sluggish_min", float),
    ):
        value = raw.get(name)
        if isinstance(value, int | float) and not isinstance(value, bool):
            setattr(day, name, kind(value))
    excess = raw.get("overshoot_max_k")
    day.overshoot_max_k = float(excess) if isinstance(excess, int | float) and not isinstance(excess, bool) else None
    day.heatups = [float(item) for item in raw.get("heatups") or [] if isinstance(item, int | float)]
    day.at_thermostat = bool(raw.get("at_thermostat", False))
    day.in_control = bool(raw.get("in_control", False))
    return day


def watch_to_json(watch: QualityWatch) -> dict[str, Any]:
    return {
        "days": [day_to_json(day) for day in watch.days],
        "today": day_to_json(watch.today) if watch.today is not None else None,
    }


def watch_from_json(raw: Any) -> QualityWatch:
    if not isinstance(raw, dict):
        return QualityWatch()
    days = [day for item in raw.get("days") or [] if (day := day_from_json(item)) is not None]
    return QualityWatch(days=days[-KEEP_DAYS:], today=day_from_json(raw.get("today")))
