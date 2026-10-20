from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vaillant_eebus.history import BUCKETS, bucket_means, first_bucket

# Mid-bucket: the newest bucket has run for 2.5 of its 5 minutes.
END = datetime(2026, 10, 20, 12, 2, 30, tzinfo=UTC)
START = datetime(2026, 10, 19, 12, 5, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def at(minutes: float) -> datetime:
    """Minutes after the start of the first bucket."""
    return START + minutes * MINUTE


def test_the_buckets_cover_24_hours_up_to_now() -> None:
    assert first_bucket(END) == START
    assert first_bucket(datetime(2026, 10, 20, 12, 0, tzinfo=UTC)) == START
    assert START + (BUCKETS - 1) * 5 * MINUTE <= END < START + BUCKETS * 5 * MINUTE


def test_a_state_holds_until_the_next_change() -> None:
    history = bucket_means([(START - timedelta(hours=3), "500"), (at(7.5), "1500")], END)

    assert history.start == START
    assert history.step_seconds == 300
    assert len(history.watts) == BUCKETS
    # The state from before the window, then half of bucket 1 at 500 W and half at 1500 W.
    assert history.watts[:3] == [500.0, 1000.0, 1500.0]
    assert history.watts[-1] == 1500.0
    assert history.peak_watts == 1500.0
    assert history.mean_watts is not None and 1490 < history.mean_watts < 1500
    assert history.gap is None


def test_the_running_bucket_counts_its_elapsed_time_only() -> None:
    history = bucket_means([(START, "0"), (END - MINUTE, "3000")], END)

    # 1.5 minutes at 0 W, 1 minute at 3000 W.
    assert history.watts[-1] == 1200.0


def test_unavailable_time_is_a_gap_and_not_a_zero() -> None:
    history = bucket_means(
        [(START, "800"), (at(6), "unavailable"), (at(16), "800"), (at(19), "unknown"), (at(21), "1200")],
        END,
    )

    # Bucket 1: 1 minute of values, too little. Bucket 2: none. Bucket 3: 3 of 5 minutes.
    assert history.watts[:5] == [800.0, None, None, 800.0, 1200.0]


def test_nothing_recorded_yet_gives_only_gaps() -> None:
    nothing = bucket_means([], END)
    assert nothing.watts == [None] * BUCKETS
    assert (nothing.mean_watts, nothing.peak_watts) == (None, None)
    assert bucket_means([(END - MINUTE, "650")], END).watts[-2:] == [None, None]


def test_values_that_are_not_numbers_are_ignored() -> None:
    history = bucket_means([(START, "nan"), (at(5), "inf"), (at(10), "432.25")], END)

    assert history.watts[:3] == [None, None, 432.2]
