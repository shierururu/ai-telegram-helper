"""Product analytics for measuring return to the platform (T2-09).

Events carry a salted hash of the Telegram identifier, never the raw identifier,
and only short generic payload values.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from alpha_course.application.weekly_summary import WeeklyProgress, weekly_progress
from alpha_course.infrastructure.models import Event

logger = logging.getLogger(__name__)

SALT = "alfa-course-events-v1"

SUMMARY_SQL = text(
    """
    SELECT kind, count(*) AS events, count(DISTINCT user_key) AS users
    FROM app.events WHERE created_at >= :since AND user_key NOT LIKE 'synthetic:%' GROUP BY kind
    """
)
RETURNING_SQL = text(
    """
    SELECT count(*) FROM (
        SELECT user_key FROM app.events
        WHERE created_at >= :since AND user_key NOT LIKE 'synthetic:%'
        GROUP BY user_key
        HAVING count(DISTINCT (created_at AT TIME ZONE 'UTC')::date) >= 2
    ) AS returning_users
    """
)
USERS_SQL = text(
    "SELECT count(DISTINCT user_key) FROM app.events "
    "WHERE created_at >= :since AND user_key NOT LIKE 'synthetic:%'"
)


def user_key(owner: int) -> str:
    return hashlib.sha256(f"{SALT}:{owner}".encode()).hexdigest()


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


class EventStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def log(
        self, owner: int, kind: str, *, material_id: str | None = None, **payload: Any
    ) -> None:
        """Record one event. Never raises: analytics must not break the user flow."""
        try:
            async with self.engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO app.events (id, user_key, kind, material_id, payload) "
                        "VALUES (gen_random_uuid(), :user_key, :kind, :material_id, "
                        "CAST(:payload AS jsonb))"
                    ),
                    {
                        "user_key": user_key(owner),
                        "kind": kind,
                        "material_id": material_id,
                        "payload": _json(payload),
                    },
                )
        except Exception:
            logger.warning("event_log_failed kind=%s", kind)

    async def weekly_progress(
        self, owner: int, timezone: str, now: datetime | None = None
    ) -> WeeklyProgress:
        moment = now or datetime.now(UTC)
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(Event).where(
                        Event.user_key == user_key(owner), Event.created_at <= moment
                    )
                )
            ).all()
        return weekly_progress(rows, timezone, moment)

    async def summary(self, days: int = 30) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(SUMMARY_SQL, {"since": since})).all()
            users = (await connection.execute(USERS_SQL, {"since": since})).scalar_one()
            returning = (await connection.execute(RETURNING_SQL, {"since": since})).scalar_one()
        counts = {row.kind: row.events for row in rows}
        unique = {row.kind: row.users for row in rows}

        def count(kind: str) -> int:
            return counts.get(kind, 0)

        return {
            "days": days,
            "counts": counts,
            "users": users,
            "returning_users": returning,
            "click_through": rate(count("material_click"), count("answer_shown")),
            "reminder_done_rate": rate(count("reminder_done"), count("reminder_delivered")),
            "reminder_open_rate": rate(count("reminder_open"), count("reminder_delivered")),
            "users_by_kind": unique,
        }


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
