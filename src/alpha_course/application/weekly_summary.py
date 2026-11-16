"""Local, evidence-based weekly praise. Learning is explicitly self-reported."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def next_summary(now: datetime, timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    day = local.date() + timedelta(days=(6 - local.weekday()) % 7)
    due = datetime.combine(day, time(19), zone)
    if due <= local:
        due += timedelta(days=7)
    return due.astimezone(UTC)


def same_sunday(due: datetime, now: datetime, timezone: str) -> bool:
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    return local.weekday() == 6 and local.date() == due.astimezone(zone).date()


def _streak(days: set[date], monday: date) -> int:
    """Best consecutive-day run ending this week; history may cross Monday."""
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        if day >= monday:
            best = max(best, run)
        previous = day
    return best


@dataclass(frozen=True)
class WeeklyProgress:
    active_days: int = 0
    questions: int = 0
    materials: int = 0
    courses: int = 0
    tasks: int = 0
    active_streak: int = 0
    learning_streak: int = 0

    def format_text(self) -> str:
        if not any((self.active_days, self.questions, self.materials, self.tasks)):
            return (
                "Итоги недели 🌱\n\nНа этой неделе пока нет отмеченных достижений. "
                "Небольшой шаг в удобном темпе — уже хорошее начало. "
                "Когда захочешь, выбери материал или задай вопрос."
            )
        lines = ["Ты молодец! 🌟", f"На этой неделе активных дней в боте: {self.active_days}."]
        if self.questions:
            lines.append(f"Задано вопросов: {self.questions}.")
        if self.materials:
            lines.append(
                f"Ты отметил изученными материалов: {self.materials}, "
                f"из них курсов: {self.courses}. Это твои самоотметки."
            )
        if self.tasks:
            lines.append(f"Подтверждённых выполненных шагов и задач: {self.tasks}.")
        if self.active_streak > 1:
            lines.append(
                f"Лучшая серия активности, завершившаяся на этой неделе: {self.active_streak} дн."
            )
        if self.learning_streak > 1:
            lines.append(f"Серия дней с отметками об изучении: {self.learning_streak} дн.")
        lines.append("Продолжай в удобном темпе — каждый небольшой шаг имеет значение.")
        return "\n\n".join(lines)


def weekly_progress(rows: list[Any], timezone: str, now: datetime) -> WeeklyProgress:
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    monday = today - timedelta(days=today.weekday())
    active: set[date] = set()
    learned: dict[str, Any] = {}
    tasks: set[tuple[str, str]] = set()
    questions = 0
    for row in sorted(rows, key=lambda item: (item.created_at, str(item.id))):
        if row.created_at > now:
            continue
        day = row.created_at.astimezone(zone).date()
        kind = row.kind
        payload = row.payload or {}
        if kind in {
            "user_active",
            "question_asked",
            "learning_marked_done",
            "learning_marked_undone",
            "journey_step_done",
            "reminder_done",
        }:
            active.add(day)
        if day >= monday and kind == "question_asked":
            questions += 1
        if row.material_id and kind == "learning_marked_done":
            # Repeated callbacks do not invent additional completions or learning days.
            learned.setdefault(row.material_id, row)
        elif row.material_id and kind == "learning_marked_undone":
            learned.pop(row.material_id, None)
        if day >= monday and kind in {"journey_step_done", "reminder_done"}:
            identity = payload.get("reminder_id")
            if identity is None and kind == "journey_step_done":
                if payload.get("journey_id") is not None and payload.get("day") is not None:
                    identity = f"{payload['journey_id']}:{payload['day']}"
            if identity is not None:
                tasks.add((kind, str(identity)))
    learning_days = {row.created_at.astimezone(zone).date() for row in learned.values()}
    week_materials = [
        row for row in learned.values() if row.created_at.astimezone(zone).date() >= monday
    ]
    return WeeklyProgress(
        active_days=len({day for day in active if monday <= day <= today}),
        questions=questions,
        materials=len(week_materials),
        courses=sum(row.payload.get("material_kind") == "course" for row in week_materials),
        tasks=len(tasks),
        active_streak=_streak(active, monday),
        learning_streak=_streak(learning_days, monday),
    )
