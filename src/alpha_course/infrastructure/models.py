"""Runtime persistence models, deliberately independent of source exports."""

from datetime import datetime, time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("version >= 1", name="profile_version_positive"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    business_stage: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    task: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[str | None] = mapped_column(Text)
    legal_form: Mapped[str | None] = mapped_column(Text)
    tax_regime: Mapped[str | None] = mapped_column(Text)
    employees: Mapped[bool | None] = mapped_column(Boolean)
    onboarded: Mapped[bool] = mapped_column(Boolean, server_default="false")
    timezone: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class CorpusRevision(Base):
    __tablename__ = "corpus_revisions"
    __table_args__ = {"schema": "kb"}

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    source_name: Mapped[str] = mapped_column(Text)
    source_checksum: Mapped[str] = mapped_column(Text)
    transformation_version: Mapped[str] = mapped_column(Text)


class ReminderRecord(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("owner_id", "operation_id", name="reminder_operation_unique"),
        Index("reminder_due", "next_due_utc", postgresql_where=text("status = 'active'")),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    operation_id: Mapped[UUID]
    event_text: Mapped[str] = mapped_column(Text)
    frequency: Mapped[str] = mapped_column(Text)
    local_time: Mapped[time]
    timezone: Mapped[str] = mapped_column(Text)
    weekday: Mapped[int | None]
    month_day: Mapped[int | None]
    link_url: Mapped[str | None] = mapped_column(Text)
    link_title: Mapped[str | None] = mapped_column(Text)
    next_due_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    version: Mapped[int]
    rules_version: Mapped[int]
    confirmed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(Text, server_default="normal", default="normal")


class ReminderOccurrence(Base):
    __tablename__ = "reminder_occurrences"
    __table_args__ = (
        UniqueConstraint("reminder_id", "version", "due_utc", name="reminder_occurrence_unique"),
        Index("reminder_occurrence_queue", "status", "next_attempt_utc", "lease_until_utc"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    reminder_id: Mapped[UUID] = mapped_column(ForeignKey("app.reminders.id"))
    version: Mapped[int]
    due_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int]
    next_attempt_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token: Mapped[UUID | None]


class ReminderAttempt(Base):
    __tablename__ = "reminder_attempts"
    __table_args__ = {"schema": "app"}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    occurrence_id: Mapped[UUID] = mapped_column(ForeignKey("app.reminder_occurrences.id"))
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(BigInteger)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("event_kind_time", "kind", "created_at"),
        Index("event_user_time", "user_key", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    material_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class NotificationSettingsRecord(Base):
    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint("weekly_limit BETWEEN 0 AND 50", name="notification_weekly_limit_range"),
        {"schema": "app"},
    )

    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timezone: Mapped[str | None] = mapped_column(Text)
    quiet_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", default=True)
    quiet_start: Mapped[time] = mapped_column(server_default=text("'22:00'"), default=time(22))
    quiet_end: Mapped[time] = mapped_column(server_default=text("'08:00'"), default=time(8))
    weekly_limit: Mapped[int] = mapped_column(Integer, server_default="7", default=7)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", default=True)
    weekly_summary: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    weekly_summary_asked: Mapped[bool] = mapped_column(
        Boolean, server_default="false", default=False
    )


class JourneyRecord(Base):
    __tablename__ = "journeys"
    __table_args__ = (
        UniqueConstraint("owner_id", "operation_id", name="journey_operation_unique"),
        Index("journey_owner", "owner_id", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    operation_id: Mapped[UUID]
    goal: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    local_time: Mapped[time]
    status: Mapped[str] = mapped_column(Text)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
