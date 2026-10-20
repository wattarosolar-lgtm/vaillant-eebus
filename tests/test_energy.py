from __future__ import annotations

import json
import zoneinfo
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from vaillant_eebus.energy import FILE_NAME, EnergyMeter, EnergyToday, local_zone
from vaillant_eebus.state import UNAVAILABLE, HeatPumpState, Reading, StateService

# A fixed offset keeps the tests independent of the time zone database, which
# Windows lacks; the image uses Home Assistant's zone from TZ.
BERLIN = timezone(timedelta(hours=2), "CEST")


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def power(watts: float) -> HeatPumpState:
    return HeatPumpState(
        available=True,
        connected=True,
        heartbeat_ok=True,
        operating_state="normalOperation",
        compressor_active=watts > 100,
        measurements={"power_consumption": Reading(value=watts, unit="W", updated_at=None)},
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 20, 10, 0, tzinfo=BERLIN))


def meter(state: StateService, data_dir: Path, clock: FakeClock) -> EnergyMeter:
    return EnergyMeter(state, data_dir, zone=BERLIN, clock=clock)


def test_power_is_summed_up_over_time(state: StateService, tmp_path: Path, clock: FakeClock) -> None:
    energy = meter(state, tmp_path, clock)
    assert energy.today() is None

    energy.observe(power(1200))
    clock.advance(minutes=1)
    energy.observe(power(600))
    clock.advance(minutes=1)
    energy.observe(power(600))

    # 1200 W for a minute and 600 W for a minute = 30 Wh. The app started
    # during the day, so the time before is missing.
    assert energy.today() == EnergyToday(kwh=0.03, incomplete=True)


def test_the_running_interval_counts_until_now(state: StateService, tmp_path: Path, clock: FakeClock) -> None:
    energy = meter(state, tmp_path, clock)
    energy.observe(power(3000))
    clock.advance(seconds=30)

    assert energy.today() == EnergyToday(kwh=0.025, incomplete=True)


def test_a_gap_is_not_bridged_and_marks_the_day(state: StateService, tmp_path: Path) -> None:
    # Counting since the day before, so the new day starts complete.
    clock = FakeClock(datetime(2026, 10, 20, 23, 59, tzinfo=BERLIN))
    energy = meter(state, tmp_path, clock)
    energy.observe(power(1000))
    clock.advance(minutes=2)
    energy.observe(power(1000))
    assert energy.today() == EnergyToday(kwh=0.017, incomplete=False)

    energy.observe(UNAVAILABLE)
    clock.advance(minutes=10)
    assert energy.today() == EnergyToday(kwh=0.017, incomplete=False)
    energy.observe(power(1000))

    assert energy.today() == EnergyToday(kwh=0.017, incomplete=True)


def test_days_start_at_local_midnight(state: StateService, tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 10, 20, 23, 59, tzinfo=BERLIN))
    energy = meter(state, tmp_path, clock)
    energy.observe(power(600))
    clock.advance(minutes=2)
    energy.observe(power(600))

    # 600 W for the first minute of the new day; nothing before midnight was missed today.
    assert energy.today() == EnergyToday(kwh=0.01, incomplete=False)
    energy.close()
    saved = json.loads((tmp_path / FILE_NAME).read_text(encoding="utf-8"))
    assert saved["days"]["2026-10-20"] == {"wh": 10.0, "incomplete": True}


def test_the_count_survives_a_quick_restart(state: StateService, tmp_path: Path, clock: FakeClock) -> None:
    first = meter(state, tmp_path, clock)
    first.observe(power(1200))
    clock.advance(minutes=1)
    first.observe(power(1200))
    first.close()

    clock.advance(minutes=1)
    second = meter(state, tmp_path, clock)
    second.observe(power(1200))

    # Two minutes at 1200 W, the restart included.
    assert second.today() == EnergyToday(kwh=0.04, incomplete=True)
    saved = json.loads((tmp_path / FILE_NAME).read_text(encoding="utf-8"))
    assert saved["days"]["2026-10-20"]["incomplete"] is True


def test_a_long_stop_is_missing_from_the_day(state: StateService, tmp_path: Path, clock: FakeClock) -> None:
    first = meter(state, tmp_path, clock)
    first.observe(power(1200))
    first.close()

    clock.advance(hours=1)
    second = meter(state, tmp_path, clock)
    second.observe(power(1200))
    clock.advance(minutes=1)
    second.observe(power(1200))

    assert second.today() == EnergyToday(kwh=0.02, incomplete=True)


def test_an_unreadable_file_is_set_aside(state: StateService, tmp_path: Path, clock: FakeClock) -> None:
    (tmp_path / FILE_NAME).write_text("{not json", encoding="utf-8")

    energy = meter(state, tmp_path, clock)

    assert energy.today() is None
    assert (tmp_path / "energy.json.corrupt").exists()


def test_old_days_are_dropped(state: StateService, tmp_path: Path, clock: FakeClock) -> None:
    days = {
        f"{datetime(2025, 1, 1, tzinfo=UTC).date() + timedelta(days=offset)}": {"wh": 1, "incomplete": False}
        for offset in range(450)
    }
    (tmp_path / FILE_NAME).write_text(json.dumps({"version": 1, "days": days, "last": None}), encoding="utf-8")

    energy = meter(state, tmp_path, clock)
    energy.observe(power(100))

    saved = json.loads((tmp_path / FILE_NAME).read_text(encoding="utf-8"))
    assert len(saved["days"]) == 400
    assert "2026-10-20" in saved["days"]


@pytest.mark.skipif(
    "Europe/Berlin" not in zoneinfo.available_timezones(), reason="no time zone database on this machine"
)
def test_the_time_zone_comes_from_its_name() -> None:
    assert local_zone("Europe/Berlin") == zoneinfo.ZoneInfo("Europe/Berlin")


def test_an_unknown_time_zone_falls_back_to_utc() -> None:
    assert local_zone("Nowhere/Town") is UTC
    assert local_zone(None) is UTC
