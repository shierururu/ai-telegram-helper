"""Verify local calendar behavior across real timezone transitions."""

from datetime import UTC, datetime

import pytest

from alpha_course.application.reminders import AmbiguousTime, ReminderError, Schedule, next_due
from alpha_course.domain.reminders import ReminderFrequency as Frequency


def test_first_occurrence_in_gap_requires_another_time():
    with pytest.raises(ReminderError, match="местного времени"):
        Schedule("test", datetime(2026, 3, 29, 2, 30), "Europe/Berlin", Frequency.DAILY).first_due()


def test_first_ambiguous_occurrence_requires_explicit_fold():
    local = datetime(2026, 10, 25, 2, 30)
    with pytest.raises(AmbiguousTime) as error:
        Schedule("test", local, "Europe/Berlin", Frequency.WEEKLY).first_due()
    assert [option.fold for option in error.value.options] == [0, 1]
    assert Schedule("test", local, "Europe/Berlin", Frequency.WEEKLY, 1).first_due() == datetime(
        2026, 10, 25, 1, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("local", "frequency", "expected"),
    [
        (datetime(2026, 3, 28, 2, 30), Frequency.DAILY, datetime(2026, 3, 30, 0, 30, tzinfo=UTC)),
        (
            datetime(2026, 10, 18, 2, 30),
            Frequency.WEEKLY,
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        ),
        (datetime(2026, 10, 18, 2, 30), Frequency.ONCE, None),
    ],
)
def test_repeats_use_calendar_with_gap_skip_and_first_fold(local, frequency, expected):
    schedule = Schedule("test", local, "Europe/Berlin", frequency)
    due = schedule.first_due()
    assert next_due(schedule, due, due) == expected


def _walk(schedule, count):
    due = schedule.first_due()
    result = [due]
    for _ in range(count):
        due = next_due(schedule, due, due)
        result.append(due)
    return result


def test_monthly_clamps_short_months_and_returns_to_anchor_day():
    schedule = Schedule("test", datetime(2026, 1, 31, 9, 0), "Etc/UTC", Frequency.MONTHLY)
    due = schedule.first_due()
    days = []
    for _ in range(4):
        due = next_due(schedule, due, due)
        days.append((due.month, due.day))
    assert days == [(2, 28), (3, 31), (4, 30), (5, 31)]


def test_monthly_without_anchor_would_drift_but_anchor_day_prevents_it():
    # The worker rebuilds the schedule from the previous due date (Feb 28).
    schedule = Schedule(
        "test", datetime(2026, 2, 28, 9, 0), "Etc/UTC", Frequency.MONTHLY, anchor_day=31
    )
    due = datetime(2026, 2, 28, 9, tzinfo=UTC)
    assert next_due(schedule, due, due) == datetime(2026, 3, 31, 9, tzinfo=UTC)


def test_monthly_skips_to_future_after_long_downtime():
    schedule = Schedule("test", datetime(2026, 1, 15, 9, 0), "Etc/UTC", Frequency.MONTHLY)
    due = schedule.first_due()
    after = datetime(2027, 6, 20, tzinfo=UTC)
    assert next_due(schedule, due, after) == datetime(2027, 7, 15, 9, tzinfo=UTC)


def test_yearly_leap_day_clamps_to_feb_28_and_returns():
    schedule = Schedule("test", datetime(2024, 2, 29, 9, 0), "Etc/UTC", Frequency.YEARLY)
    dates = [(d.year, d.month, d.day) for d in _walk(schedule, 4)]
    assert dates == [
        (2024, 2, 29),
        (2025, 2, 28),
        (2026, 2, 28),
        (2027, 2, 28),
        (2028, 2, 29),
    ]


def test_monthly_keeps_local_time_across_dst_change():
    schedule = Schedule("test", datetime(2026, 3, 10, 9, 0), "Europe/Berlin", Frequency.MONTHLY)
    due = schedule.first_due()
    assert due == datetime(2026, 3, 10, 8, tzinfo=UTC)
    assert next_due(schedule, due, due) == datetime(2026, 4, 10, 7, tzinfo=UTC)
