"""JSON shapes of the Mini App API (see docs/webapp-api.md)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from alpha_course.application.assistant import Recommendation, Reply
from alpha_course.application.engagement import Journey, NotificationSettings
from alpha_course.application.knowledge import Material
from alpha_course.application.profile import FIELD_LABELS, BusinessProfile
from alpha_course.domain.reminders import ReminderFrequency
from alpha_course.telegram.reminders import MONTHS_GENITIVE, format_due, timezone_label

PROFILE_FIELDS = tuple(FIELD_LABELS)
WEEKDAY_PHRASES = (
    "каждый понедельник",
    "каждый вторник",
    "каждую среду",
    "каждый четверг",
    "каждую пятницу",
    "каждую субботу",
    "каждое воскресенье",
)


def iso_utc(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def clock_text(value) -> str:
    return value.strftime("%H:%M")


def material_json(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "title": material.title,
        "url": material.url,
        "kind": material.kind,
        "kind_label": material.kind_label,
        "icon": material.icon,
        "access": material.access,
        "is_paid": material.is_paid,
        "price": None if material.access == "subscription" else material.price,
        "duration": material.duration,
        "lessons": material.lessons,
        "purchase_url": material.purchase_url,
    }


def recommendation_json(item: Recommendation) -> dict[str, Any]:
    return {"material": material_json(item.material), "why": item.why}


def reply_json(reply: Reply) -> dict[str, Any]:
    clarification = reply.clarification
    return {
        "kind": reply.kind,
        "status": reply.status,
        "summary": reply.summary,
        "steps": list(reply.steps),
        "personal": reply.personal,
        "sources": [material_json(item) for item in reply.sources],
        "recommendations": [recommendation_json(item) for item in reply.recommendations],
        "followups": list(reply.followups),
        "clarification": {
            "question": clarification.question,
            "options": list(clarification.options),
        }
        if clarification
        else None,
        "profile_updates": dict(reply.profile_updates),
        "excerpt": reply.excerpt,
        "reminder_event": reply.summary if reply.kind == "reminder" and reply.summary else None,
    }


def profile_json(profile: BusinessProfile) -> dict[str, Any]:
    return {
        "fields": {name: getattr(profile, name) for name in PROFILE_FIELDS},
        "labels": profile.filled(),
    }


def settings_json(settings: NotificationSettings) -> dict[str, Any]:
    return {
        "timezone": settings.timezone,
        "quiet_enabled": settings.quiet_enabled,
        "quiet_start": clock_text(settings.quiet_start),
        "quiet_end": clock_text(settings.quiet_end),
        "weekly_limit": settings.weekly_limit,
        "enabled": settings.enabled,
        "weekly_summary": settings.weekly_summary,
        "weekly_summary_asked": settings.weekly_summary_asked,
    }


def timing_text(record) -> str:
    """«каждый понедельник в 09:00», «15 марта в 10:00 · Москва» for one reminder."""
    frequency = ReminderFrequency(record.frequency)
    due = record.next_due_utc
    if frequency == ReminderFrequency.ONCE:
        if due is None:
            return f"Ожидает отправки · {timezone_label(record.timezone)}"
        return format_due(due, record.timezone)
    clock = clock_text(record.local_time)
    if frequency == ReminderFrequency.DAILY:
        return f"каждый день в {clock}"
    if frequency == ReminderFrequency.WEEKLY and record.weekday is not None:
        return f"{WEEKDAY_PHRASES[record.weekday]} в {clock}"
    if frequency == ReminderFrequency.MONTHLY and record.month_day:
        return f"каждый месяц, {record.month_day} числа в {clock}"
    if frequency == ReminderFrequency.YEARLY and record.month_day and due is not None:
        month = MONTHS_GENITIVE[due.astimezone(ZoneInfo(record.timezone)).month - 1]
        return f"каждый год, {record.month_day} {month} в {clock}"
    return f"{frequency.value} в {clock}"


def reminder_json(record) -> dict[str, Any]:
    zone = ZoneInfo(record.timezone)
    due = record.next_due_utc
    local_at = (
        due.astimezone(zone).replace(tzinfo=None).isoformat(timespec="minutes") if due else None
    )
    link_url = getattr(record, "link_url", None)
    return {
        "id": record.id.hex,
        "version": record.version,
        "event": record.event_text,
        "frequency": str(ReminderFrequency(record.frequency).value),
        "timing": timing_text(record),
        "next_due": iso_utc(due),
        "due_label": format_due(due, record.timezone) if due else None,
        "local_at": local_at,
        "timezone": record.timezone,
        "city": timezone_label(record.timezone),
        "link": {"url": link_url, "title": getattr(record, "link_title", None) or link_url}
        if link_url
        else None,
    }


def journey_json(journey: Journey, now: datetime) -> dict[str, Any]:
    reached = [
        step.day for step in journey.steps if step.due_utc is not None and step.due_utc <= now
    ]
    latest = max(reached, default=None)
    steps = []
    for step in journey.steps:
        if step.done:
            state = "done"
        elif step.day not in reached:
            state = "locked"
        elif step.day == latest:
            state = "today"
        else:
            state = "missed"
        steps.append(
            {
                "day": step.day,
                "task": step.task,
                "title": step.title,
                "url": step.url,
                "material_id": step.material_id,
                "done": step.done,
                "due": iso_utc(step.due_utc),
                "state": state,
            }
        )
    return {
        "id": str(journey.id),
        "goal": journey.goal,
        "timezone": journey.timezone,
        "time": clock_text(journey.local_time),
        "status": journey.status,
        "done_days": journey.done_days,
        "steps": steps,
    }
