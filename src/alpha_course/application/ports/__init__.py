"""Small ports for future use cases; no successful behavior is simulated."""

from datetime import datetime
from typing import Protocol

from alpha_course.domain import Answer, Chunk, Profile, Reminder


class Consultation(Protocol):
    async def answer(self, question: str, profile: Profile) -> Answer: ...


class KnowledgeSearch(Protocol):
    async def search(self, query: str, limit: int = 5) -> tuple[Chunk, ...]: ...


class ReminderStorage(Protocol):
    """Implementations must scope reads/writes by owner and compare versions atomically."""

    async def get(self, reminder_id: str, owner_id: str) -> Reminder | None: ...

    async def save(self, reminder: Reminder, expected_version: int | None) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime:
        """Return a timezone-aware UTC instant."""
        ...


class TelegramTransport(Protocol):
    async def send_message(self, chat_id: int, text: str) -> str:
        """Return Telegram message ID; errors must preserve delivery uncertainty."""
        ...
