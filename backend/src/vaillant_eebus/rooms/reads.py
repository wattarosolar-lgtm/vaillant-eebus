"""When the app asks an Aqara W600 for its valve position (1.4.2, plan-1.4.2-testlauf-lernt.md).

The W600 reports its valve position (attribute 0x0360) only now and then,
sometimes not for a day; a read through Zigbee2MQTT returns the real one at
once. The room control reads each W600

- every five minutes from "Beobachten" on, one to two and a half minutes before
  the control's beat, so it decides with a fresh value;
- one and three minutes after each change of the setpoint (its own, by hand or
  by a schedule), for the step response;
- in a test run every minute for ten minutes after each step, then every two
  minutes.

The write gate spaces and caps the reads per thermostat on its own. Pure: the
room control keeps one ReadPlan per thermostat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

EVERY = timedelta(minutes=5)
# The regular read waits this long after the last one, and goes out at most this long before the beat.
REGULAR_GAP = timedelta(minutes=4)
BEFORE_BEAT = timedelta(seconds=150)
AFTER_CHANGE = (timedelta(minutes=1), timedelta(minutes=3))
TEST_FAST_FOR = timedelta(minutes=10)
TEST_FAST = timedelta(minutes=1)
TEST_SLOW = timedelta(minutes=2)
# The minute loop is not exact: a read is due a few seconds early.
SLACK = timedelta(seconds=5)


@dataclass
class ReadPlan:
    last: datetime | None = None
    extra: list[datetime] = field(default_factory=list)

    def after_change(self, now: datetime) -> None:
        """The setpoint changed: reads one and three minutes later."""
        self.extra = sorted({*self.extra, *(now + delay for delay in AFTER_CHANGE)})

    def due(self, now: datetime, *, next_beat: datetime | None, test_step_at: datetime | None) -> bool:
        if any(moment <= now + SLACK for moment in self.extra):
            return True
        if test_step_at is not None:
            every = TEST_FAST if now - test_step_at < TEST_FAST_FOR else TEST_SLOW
            return self.last is None or now - self.last >= every - SLACK
        if self.last is not None and now - self.last < REGULAR_GAP:
            return False
        if next_beat is None or now > next_beat + TEST_FAST:
            # No beat to go by (it did not run yet or stands still): every five minutes.
            return self.last is None or now - self.last >= EVERY - SLACK
        return next_beat - now <= BEFORE_BEAT

    def done(self, now: datetime) -> None:
        self.last = now
        self.extra = [moment for moment in self.extra if moment > now + SLACK]
