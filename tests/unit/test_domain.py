from datetime import UTC, datetime, time, timedelta

import pytest

from alpha_course.domain import (
    AccessClass,
    AccessPolicy,
    Answer,
    AnswerStatus,
    EvidenceRef,
    Occurrence,
    Profile,
    Reminder,
    ReminderFrequency,
)


def test_permissions_fail_closed_and_paid_body_is_forbidden() -> None:
    policy = AccessPolicy()
    assert policy.access_class == AccessClass.UNKNOWN
    assert not policy.factual_allowed
    assert not policy.external_allowed
    with pytest.raises(ValueError, match="free"):
        AccessPolicy(AccessClass.PAID, factual_allowed=True)
    with pytest.raises(ValueError, match="factual"):
        AccessPolicy(AccessClass.FREE, external_allowed=True)


def test_answer_requires_evidence_and_clarification_requires_question() -> None:
    with pytest.raises(ValueError, match="evidence"):
        Answer(AnswerStatus.ANSWERED, "Unsupported answer")
    with pytest.raises(ValueError, match="question"):
        Answer(AnswerStatus.CLARIFY, "Need more details")
    answer = Answer(AnswerStatus.ANSWERED, "Supported", (EvidenceRef("c", "m", 1),))
    assert answer.evidence[0].material_id == "m"


def test_incomplete_profile_is_valid() -> None:
    assert Profile("synthetic-user").sector is None
    with pytest.raises(ValueError, match="IANA"):
        Profile("synthetic-user", timezone="not/a-zone")


def test_occurrence_requires_utc_and_positive_version() -> None:
    with pytest.raises(ValueError, match="UTC"):
        Occurrence("r", 1, datetime(2026, 11, 9))
    with pytest.raises(ValueError, match="version"):
        Occurrence("r", 0, datetime(2026, 11, 9, tzinfo=UTC))
    due = datetime(2026, 11, 9, tzinfo=UTC)
    assert Occurrence("r", 1, due).key == ("r", 1, due)


def test_reminder_has_confirmed_future_time_and_iana_zone() -> None:
    confirmed = datetime(2026, 11, 9, tzinfo=UTC)
    reminder = Reminder(
        "r",
        "u",
        "Synthetic event",
        ReminderFrequency.DAILY,
        time(10),
        "Europe/Moscow",
        confirmed + timedelta(days=1),
        confirmed,
    )
    assert reminder.timezone == "Europe/Moscow"
    assert "Synthetic event" not in repr(reminder)
    with pytest.raises(ValueError, match="future"):
        Reminder(
            "r",
            "u",
            "Synthetic event",
            ReminderFrequency.ONCE,
            time(10),
            "Europe/Moscow",
            confirmed,
            confirmed,
        )


def test_paid_preview_can_be_recommended_without_body_permissions() -> None:
    preview = AccessPolicy(AccessClass.PAID, can_recommend=True)
    assert preview.can_recommend
    assert not preview.factual_allowed
    assert not preview.external_allowed
    with pytest.raises(ValueError, match="free"):
        AccessPolicy(AccessClass.PAID, factual_allowed=True, can_recommend=True)
    with pytest.raises(ValueError, match="previews"):
        AccessPolicy(AccessClass.UNKNOWN, can_recommend=True)


@pytest.mark.parametrize(
    "url",
    [
        "article-slug",
        "javascript:alert(1)",
        "https://user:password@example.org/a",
        "https://example.org/a\n",
        "https:///missing-host",
    ],
)
def test_material_rejects_unsafe_or_relative_urls(url: str) -> None:
    from alpha_course.domain import Material

    with pytest.raises(ValueError, match="absolute HTTP"):
        Material("m", "article", "1", "Title", AccessPolicy(), "synthetic", verified_url=url)
    material = Material(
        "m",
        "article",
        "1",
        "Title",
        AccessPolicy(),
        "synthetic",
        verified_url="https://example.org/provided-article",
    )
    assert material.verified_url == "https://example.org/provided-article"


def test_weekly_schedule_requires_weekday_and_other_frequencies_reject_it() -> None:
    confirmed = datetime(2026, 11, 9, tzinfo=UTC)
    fields = dict(
        id="r",
        owner_id="u",
        event_text="Synthetic",
        local_time=time(10),
        timezone="Europe/Moscow",
        next_due_utc=confirmed + timedelta(days=1),
        confirmed_at_utc=confirmed,
    )
    for weekday in (None, -1, 7):
        with pytest.raises(ValueError, match="weekday"):
            Reminder(**fields, frequency=ReminderFrequency.WEEKLY, weekday=weekday)
    assert Reminder(**fields, frequency=ReminderFrequency.WEEKLY, weekday=0).weekday == 0
    with pytest.raises(ValueError, match="Weekday"):
        Reminder(**fields, frequency=ReminderFrequency.DAILY, weekday=0)
    assert Profile("u", depth="short").depth == "short"


def test_monthly_and_yearly_require_a_month_day():
    confirmed = datetime(2026, 1, 1, tzinfo=UTC)
    fields = dict(
        id="r",
        owner_id="u",
        event_text="Synthetic",
        local_time=time(10),
        timezone="Europe/Moscow",
        next_due_utc=confirmed + timedelta(days=1),
        confirmed_at_utc=confirmed,
    )
    for frequency in (ReminderFrequency.MONTHLY, ReminderFrequency.YEARLY):
        for month_day in (None, 0, 32):
            with pytest.raises(ValueError, match="day"):
                Reminder(**fields, frequency=frequency, month_day=month_day)
        assert Reminder(**fields, frequency=frequency, month_day=31).month_day == 31
    with pytest.raises(ValueError, match="Month day"):
        Reminder(**fields, frequency=ReminderFrequency.DAILY, month_day=1)
