"""A/B assignment log, admin metrics and the in-dialog rating survey.

Everything is stored in the existing app.events table under the salted user key:
no new table, no raw Telegram identifiers, no message texts.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from alpha_course.application.experiments import CONTROL, PAID_OFFER, Experiment, analyse, assign
from alpha_course.infrastructure.events import EventStore, user_key

logger = logging.getLogger(__name__)

SURVEY_EVERY_DAYS = 14
SURVEY_AFTER_ANSWERS = 3

_ASSIGNED_SQL = text(
    "SELECT 1 FROM app.events WHERE user_key = :key AND kind = 'experiment_assigned' "
    "AND payload->>'experiment' = :name LIMIT 1"
)

# Per-variant counts for one experiment; clicks and returns count only after t0.
REPORT_SQL = text(
    """
    WITH u AS (
        SELECT user_key,
               min(created_at) FILTER (WHERE kind = 'experiment_assigned'
                   AND payload->>'experiment' = :name) AS t0,
               min(payload->>'variant') FILTER (WHERE kind = 'experiment_assigned'
                   AND payload->>'experiment' = :name) AS variant,
               count(*) FILTER (WHERE kind = 'paid_offer_shown'
                   AND payload->>'experiment' = :name) AS shows,
               array_agg(created_at) FILTER (WHERE kind = 'paid_offer_click') AS click_times,
               max(created_at) AS last_at
        FROM app.events
        WHERE user_key IN (
            SELECT user_key FROM app.events
            WHERE kind = 'experiment_assigned' AND payload->>'experiment' = :name
        )
        GROUP BY user_key
    ),
    p AS (
        SELECT variant, shows,
               (SELECT count(*) FROM unnest(click_times) AS at WHERE at >= t0) AS clicks,
               (last_at AT TIME ZONE 'UTC')::date > (t0 AT TIME ZONE 'UTC')::date AS returned
        FROM u WHERE t0 IS NOT NULL
    )
    SELECT variant, count(*) AS users, coalesce(sum(shows), 0) AS shows,
           count(*) FILTER (WHERE clicks > 0) AS clickers, coalesce(sum(clicks), 0) AS clicks,
           count(*) FILTER (WHERE returned) AS returned
    FROM p GROUP BY variant
    """
)

# One row per (pseudonymous) user: what a business owner asks about engagement.
METRICS_SQL = text(
    """
    SELECT user_key,
           min(created_at) AS first_seen,
           max(created_at) AS last_seen,
           count(DISTINCT (created_at AT TIME ZONE 'UTC')::date) AS active_days,
           count(*) FILTER (WHERE kind = 'question_asked') AS questions,
           count(*) FILTER (WHERE kind = 'answer_shown') AS answers,
           count(*) FILTER (WHERE kind = 'plan_shown') AS plans,
           count(*) FILTER (WHERE kind = 'plan_step_toggled' AND payload->>'done' = 'true')
               AS plan_steps_done,
           count(*) FILTER (WHERE kind = 'journey_step_done') AS journey_steps_done,
           count(*) FILTER (WHERE kind = 'reminder_done') AS reminders_done,
           count(*) FILTER (WHERE kind = 'material_click') AS materials_opened,
           count(*) FILTER (WHERE kind = 'paid_offer_shown') AS paid_offers_shown,
           count(*) FILTER (WHERE kind = 'paid_offer_click') AS paid_offer_clicks,
           count(*) FILTER (WHERE kind = 'feedback_up') AS likes,
           count(*) FILTER (WHERE kind = 'feedback_down') AS dislikes,
           round(avg((payload->>'score')::numeric) FILTER (WHERE kind = 'survey_score'), 2)
               AS rating,
           max(payload->>'variant') FILTER (
               WHERE kind = 'experiment_assigned' AND payload->>'experiment' = :name
           ) AS ab_variant
    FROM app.events
    WHERE created_at >= :since AND user_key NOT LIKE 'synthetic:%'
    GROUP BY user_key
    ORDER BY last_seen DESC
    LIMIT 20000
    """
)

OVERVIEW_SQL = text(
    """
    SELECT count(DISTINCT user_key) AS users,
           count(DISTINCT user_key) FILTER (WHERE created_at >= :day) AS active_1d,
           count(DISTINCT user_key) FILTER (WHERE created_at >= :week) AS active_7d,
           round(avg((payload->>'score')::numeric) FILTER (WHERE kind = 'survey_score'), 2)
               AS rating,
           count(*) FILTER (WHERE kind = 'survey_score') AS ratings
    FROM app.events
    WHERE created_at >= :since AND user_key NOT LIKE 'synthetic:%'
    """
)

_SURVEY_SQL = text(
    """
    SELECT count(*) FILTER (WHERE kind = 'answer_shown') AS answers,
           count(*) FILTER (WHERE kind = 'survey_asked' AND created_at >= :since) AS asked
    FROM app.events WHERE user_key = :key AND kind IN ('answer_shown', 'survey_asked')
    """
)

METRIC_COLUMNS = (
    "user",
    "first_seen",
    "last_seen",
    "active_days",
    "questions",
    "answers",
    "plans",
    "plan_steps_done",
    "journey_steps_done",
    "reminders_done",
    "materials_opened",
    "paid_offers_shown",
    "paid_offer_clicks",
    "likes",
    "dislikes",
    "rating",
    "ab_variant",
)


class Experiments:
    """Exposure and click logging plus reports; failures never break the user flow."""

    def __init__(
        self, events: EventStore, *, enabled: bool = True, experiment: Experiment = PAID_OFFER
    ) -> None:
        self.events = events
        self.enabled = enabled
        self.experiment = experiment
        self._assigned: set[str] = set()

    def variant(self, owner: int) -> str:
        if not self.enabled:
            return CONTROL
        return assign(self.experiment, user_key(owner))

    async def expose(self, owner: int, material_id: str, surface: str) -> str:
        """The user is about to see a paid offer: fix the assignment (t0) and log the show."""
        variant = self.variant(owner)
        if not self.enabled:
            return variant
        key = user_key(owner)
        name = self.experiment.name
        if key not in self._assigned:
            try:
                async with self.events.engine.connect() as connection:
                    known = (
                        await connection.execute(_ASSIGNED_SQL, {"key": key, "name": name})
                    ).first()
                if known is None:
                    await self.events.log(
                        owner, "experiment_assigned", experiment=name, variant=variant
                    )
                self._assigned.add(key)
            except Exception:
                logger.warning("experiment_assign_failed")
        await self.events.log(
            owner,
            "paid_offer_shown",
            material_id=material_id,
            experiment=name,
            variant=variant,
            surface=surface,
        )
        return variant

    async def click(self, owner: int, material_id: str, surface: str) -> None:
        await self.events.log(
            owner,
            "paid_offer_click",
            material_id=material_id,
            experiment=self.experiment.name,
            variant=self.variant(owner),
            surface=surface,
        )

    async def report(self, name: str | None = None) -> dict[str, Any]:
        async with self.events.engine.connect() as connection:
            rows = (
                await connection.execute(REPORT_SQL, {"name": name or self.experiment.name})
            ).all()
        counts = {
            row.variant: {
                "users": row.users,
                "shows": row.shows,
                "clickers": row.clickers,
                "clicks": row.clicks,
                "returned": row.returned,
            }
            for row in rows
        }
        return analyse(counts, self.experiment) | {"name": name or self.experiment.name}

    async def overview(self, days: int = 30) -> dict[str, Any]:
        now = datetime.now(UTC)
        params = {
            "since": now - timedelta(days=days),
            "day": now - timedelta(days=1),
            "week": now - timedelta(days=7),
        }
        async with self.events.engine.connect() as connection:
            row = (await connection.execute(OVERVIEW_SQL, params)).one()
        return {
            "users": row.users,
            "active_1d": row.active_1d,
            "active_7d": row.active_7d,
            "rating": float(row.rating) if row.rating is not None else None,
            "ratings": row.ratings,
        }

    async def metrics_csv(self, days: int = 90) -> bytes:
        """Per-user engagement table for the admin; user keys are shortened hashes."""
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.events.engine.connect() as connection:
            rows = (
                await connection.execute(
                    METRICS_SQL, {"since": since, "name": self.experiment.name}
                )
            ).all()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(METRIC_COLUMNS)
        for row in rows:
            values = list(row)
            values[0] = str(values[0])[:12]
            values[1] = values[1].isoformat(timespec="minutes") if values[1] else ""
            values[2] = values[2].isoformat(timespec="minutes") if values[2] else ""
            writer.writerow(["" if value is None else value for value in values])
        return ("﻿" + buffer.getvalue()).encode()

    async def survey_due(self, owner: int) -> bool:
        """Ask for a 1–5 rating at most once per two weeks, after a few real answers."""
        try:
            since = datetime.now(UTC) - timedelta(days=SURVEY_EVERY_DAYS)
            async with self.events.engine.connect() as connection:
                row = (
                    await connection.execute(_SURVEY_SQL, {"key": user_key(owner), "since": since})
                ).one()
        except Exception:
            logger.warning("survey_check_failed")
            return False
        return row.answers >= SURVEY_AFTER_ANSWERS and row.asked == 0
