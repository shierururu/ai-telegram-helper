"""Pure rules for quiet hours, the rolling weekly limit and journey fallbacks."""

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from alpha_course.application.engagement import (
    NotificationSettings,
    fallback_steps,
    next_slot_after_limit,
    shift_for_quiet_hours,
)
from alpha_course.application.reminders import ReminderError

START, END = time(22), time(8)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_outside_quiet_window_is_unchanged():
    due = utc(2026, 1, 5, 12)
    assert shift_for_quiet_hours(due, "Etc/UTC", START, END) == due
    assert shift_for_quiet_hours(utc(2026, 1, 5, 8), "Etc/UTC", START, END) == utc(2026, 1, 5, 8)


def test_window_crossing_midnight_moves_to_next_end():
    assert shift_for_quiet_hours(utc(2026, 1, 5, 23, 30), "Etc/UTC", START, END) == utc(
        2026, 1, 6, 8
    )
    assert shift_for_quiet_hours(utc(2026, 1, 6, 3), "Etc/UTC", START, END) == utc(2026, 1, 6, 8)
    # 23:00 in Moscow (UTC+3) is 20:00 UTC; the answer is 08:00 Moscow = 05:00 UTC.
    assert shift_for_quiet_hours(utc(2026, 1, 5, 20), "Europe/Moscow", START, END) == utc(
        2026, 1, 6, 5
    )


def test_same_day_window():
    assert shift_for_quiet_hours(utc(2026, 1, 5, 13, 30), "Etc/UTC", time(13), time(15)) == utc(
        2026, 1, 5, 15
    )


def test_dst_uses_the_offset_of_the_end_instant():
    # New York leaves DST on 2026-11-01 (UTC-4 -> UTC-5). 23:00 EDT on 31 Oct is 03:00 UTC.
    due = utc(2026, 11, 1, 3)
    assert shift_for_quiet_hours(due, "America/New_York", START, END) == utc(2026, 11, 1, 13)
    # Spring forward on 2026-03-08: 23:00 EST on 7 March, 08:00 is already EDT (12:00 UTC).
    due = utc(2026, 3, 8, 4)
    assert shift_for_quiet_hours(due, "America/New_York", START, END) == utc(2026, 3, 8, 12)


def test_limit_window():
    due = utc(2026, 1, 20, 12)
    sent = [due - timedelta(days=1, hours=i) for i in range(3)]
    assert next_slot_after_limit(sent, 4, due, due) is None
    assert next_slot_after_limit(sent, 0, due, due) is None
    oldest = min(sent)
    assert next_slot_after_limit(sent, 3, due, due) == oldest + timedelta(days=7)
    assert next_slot_after_limit(sent, 2, due, due) == sorted(sent)[1] + timedelta(days=7)
    old = [due - timedelta(days=7, minutes=1)] * 5
    assert next_slot_after_limit(old, 1, due, due) is None


def test_settings_validation():
    assert NotificationSettings().weekly_limit == 7
    for bad in (
        {"weekly_limit": 51},
        {"weekly_limit": -1},
        {"timezone": "Mars/Base"},
        {"quiet_start": time(8), "quiet_end": time(8)},
    ):
        with pytest.raises(ReminderError):
            NotificationSettings(**bad)


def test_fallback_steps_cycle_without_inventing_links():
    assert fallback_steps("goal", []) == []
    cards = [
        SimpleNamespace(id="a", title="A", url="https://example.test/a"),
        SimpleNamespace(id="b", title="B", url="https://example.test/b"),
    ]
    steps = fallback_steps("goal", cards)
    assert len(steps) == 7
    assert [s["material_id"] for s in steps[:4]] == ["a", "b", "a", "b"]
    assert {s["url"] for s in steps} == {"https://example.test/a", "https://example.test/b"}
    assert len({s["task"] for s in steps}) == 7
