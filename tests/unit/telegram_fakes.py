"""In-memory fakes and a Telegram transport double for router tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import InlineKeyboardMarkup, Message, Update

from alpha_course.application.assistant import Recommendation, Reply
from alpha_course.application.knowledge import Chunk, KnowledgeBase, Material
from alpha_course.application.profile import BusinessProfile
from alpha_course.telegram.reminder_actions import create_reminder_actions_router
from alpha_course.telegram.router import create_router

ALLOWED_TAGS = {"b", "i", "a", "code", "pre", "u", "s"}


# --------------------------------------------------------------------------- knowledge
def material(ident="m1", **changes) -> Material:
    values = dict(
        id=ident,
        kind="article",
        title="Как выбрать налоговый режим",
        summary="Сравнение режимов налогообложения для малого бизнеса",
        url=f"https://example.test/{ident}",
        access="free",
        duration="5 мин",
    )
    values.update(changes)
    return Material(**values)


def tiny_knowledge() -> KnowledgeBase:
    materials = [
        material("free1", title="Налоги для ИП: режимы и сроки"),
        material("free2", kind="course", title="Первые клиенты для кофейни", lessons=5),
        material(
            "paid1",
            kind="course",
            title="Курс по финансам",
            access="paid",
            price="4900 ₽",
            url="https://example.test/paid1",
            purchase_url="https://example.test/buy-paid1",
        ),
    ]
    chunks = [
        Chunk(
            "c1",
            "free1",
            "Режимы",
            "Налоги ИП режимы УСН сроки уплаты налогов начать бизнес идея запуск",
        ),
        Chunk("c2", "free2", "Клиенты", "Первые клиенты кофейня маркетинг продажи"),
    ]
    return KnowledgeBase(materials, chunks)


# --------------------------------------------------------------------------- app fakes
class FakeAssistant:
    def __init__(self, knowledge: KnowledgeBase | None = None) -> None:
        self.knowledge = knowledge or tiny_knowledge()
        self.next_reply = Reply(kind="answer", summary="ok")
        self.reply_calls: list[dict] = []
        self.plan_calls: list[BusinessProfile] = []
        self.plan_result: tuple[str, list[Recommendation]] | None = None
        self.extract_calls: list[str] = []
        self.extracted: dict = {}
        self.general_calls: list[str] = []
        self.general_reply = Reply(kind="ai_help", summary="Общий совет.")

    async def reply(
        self, message, *, profile=None, history=None, allow_clarify=True, progress=None
    ):
        self.reply_calls.append(
            {
                "message": message,
                "profile": profile,
                "history": history,
                "allow_clarify": allow_clarify,
            }
        )
        return self.next_reply

    async def learning_plan(self, profile, exclude=frozenset()):
        self.plan_calls.append(profile)
        if self.plan_result is not None:
            return self.plan_result
        first = next(iter(self.knowledge.materials.values()))
        return "Маршрут.", [Recommendation(first, "с него проще начать")]

    async def extract_profile(self, text, profile):
        self.extract_calls.append(text)
        return dict(self.extracted)


class FakeProfiles:
    def __init__(self) -> None:
        self.data: dict[int, BusinessProfile] = {}
        self.onboarded: set[int] = set()
        self.cleared: list[int] = []

    async def load(self, owner):
        return self.data.get(owner, BusinessProfile())

    async def save(self, owner, profile):
        self.data[owner] = profile

    async def clear(self, owner):
        self.cleared.append(owner)
        self.data[owner] = BusinessProfile()

    async def is_onboarded(self, owner):
        return owner in self.onboarded

    async def mark_onboarded(self, owner):
        self.onboarded.add(owner)


class FakeReminders:
    def __init__(self) -> None:
        self.records: list[SimpleNamespace] = []
        self.create_calls: list[tuple] = []
        self.disabled: list[tuple] = []

    def _record(self, owner, schedule, links, ident=None, version=1):
        return SimpleNamespace(
            id=ident or uuid4(),
            owner_id=owner,
            version=version,
            event_text=schedule.event_text,
            frequency=schedule.frequency,
            timezone=schedule.timezone,
            next_due_utc=schedule.first_due(),
            link_url=links.get("link_url"),
            link_title=links.get("link_title"),
            status="active",
        )

    async def create(self, owner, operation_id, schedule, **links):
        self.create_calls.append((owner, operation_id, schedule, links))
        record = self._record(owner, schedule, links)
        self.records.append(record)
        return record

    async def update(self, owner, target, version, schedule, **links):
        record = self._record(owner, schedule, links, ident=target, version=version + 1)
        self.records = [item for item in self.records if item.id != target] + [record]
        return record

    async def list(self, owner):
        return [item for item in self.records if item.owner_id == owner]

    async def get(self, owner, ident):
        return next(
            (item for item in self.records if item.id == ident and item.owner_id == owner), None
        )

    async def disable(self, owner, target, *, version):
        self.disabled.append((owner, target, version))
        self.records = [item for item in self.records if item.id != target]

    async def snooze(self, owner, ident, action, token):  # pragma: no cover - unused here
        raise NotImplementedError


class FakeEngagement:
    """In-memory 7-day plan and notification settings."""

    def __init__(self) -> None:
        from alpha_course.application.engagement import NotificationSettings

        self.settings = NotificationSettings()
        self.journey = None
        self.stopped: list = []

    async def get_settings(self, owner):
        return self.settings

    async def save_settings(self, owner, **changes):
        from dataclasses import replace

        self.settings = replace(self.settings, **changes)
        return self.settings

    async def get_journey(self, owner):
        return self.journey if self.journey and self.journey.owner_id == owner else None

    async def stop_journey(self, owner, journey_id):
        self.stopped.append(journey_id)
        self.journey = None
        return True


def synthetic_journey(owner: int = 1, *, goal: str = "Найти первых клиентов", done: int = 1):
    """A running 7-day plan: the first `done` days are past and ticked, the rest are ahead."""
    from datetime import time, timedelta

    from alpha_course.application.engagement import Journey, JourneyStep

    now = datetime.now(UTC).replace(microsecond=0)
    steps = tuple(
        JourneyStep(
            day=day,
            title=f"Материал {day}",
            url=f"https://example.test/j{day}",
            material_id=None,
            task=f"Задание {day}",
            reminder_id=uuid4(),
            due_utc=now + timedelta(days=day - done - 0.5),
            done=day <= done,
        )
        for day in range(1, 8)
    )
    return Journey(
        id=uuid4(),
        owner_id=owner,
        goal=goal,
        timezone="Europe/Moscow",
        local_time=time(9, 0),
        status="active",
        steps=steps,
    )


class FakeEvents:
    def __init__(self) -> None:
        self.logged: list[tuple[int, str, dict]] = []

    async def log(self, owner, kind, **payload):
        self.logged.append((owner, kind, payload))

    @property
    def kinds(self) -> list[str]:
        return [kind for _, kind, _ in self.logged]

    async def summary(self, days=30):
        return {"users": 3, "answers": 7}


class FakeExperiments:
    """A/B double: a fixed variant, recorded exposures/clicks and a switchable survey."""

    def __init__(self, events: FakeEvents, variant: str = "A") -> None:
        from alpha_course.application.experiments import PAID_OFFER

        self.events = events
        self.forced = variant
        self.enabled = True
        self.experiment = PAID_OFFER
        self.survey = False
        self.exposed: list[tuple[int, str, str]] = []

    def variant(self, owner):
        return self.forced

    async def expose(self, owner, material_id, surface):
        self.exposed.append((owner, material_id, surface))
        await self.events.log(
            owner, "paid_offer_shown", material_id=material_id, variant=self.forced
        )
        return self.forced

    async def click(self, owner, material_id, surface):
        await self.events.log(
            owner, "paid_offer_click", material_id=material_id, variant=self.forced
        )

    async def survey_due(self, owner):
        return self.survey

    async def overview(self, days=30):
        return {"users": 10, "active_1d": 2, "active_7d": 5, "rating": 4.5, "ratings": 2}

    async def report(self, name=None):
        from alpha_course.application.experiments import analyse

        if name and name.endswith("_synthetic"):
            return analyse({})
        return analyse(
            {
                "A": {"users": 40, "clickers": 2, "shows": 60, "clicks": 2},
                "B": {"users": 38, "clickers": 4, "shows": 55, "clicks": 5},
            }
        )

    async def metrics_csv(self, days=90):
        return b"user,active_days\r\nabc,3\r\n"


# --------------------------------------------------------------------------- transport
@dataclass
class Call:
    name: str
    method: object

    @property
    def text(self) -> str | None:
        return getattr(self.method, "text", None)

    @property
    def parse_mode(self) -> str | None:
        mode = getattr(self.method, "parse_mode", None)
        return mode if isinstance(mode, str) else None

    @property
    def reply_markup(self):
        return getattr(self.method, "reply_markup", None)

    @property
    def buttons(self) -> list:
        markup = self.reply_markup
        if isinstance(markup, InlineKeyboardMarkup):
            return [button for row in markup.inline_keyboard for button in row]
        return []

    def callbacks(self) -> list[str]:
        return [button.callback_data for button in self.buttons if button.callback_data]

    def find(self, label: str):
        return next((button for button in self.buttons if label in button.text), None)

    def data_for(self, label: str) -> str:
        button = self.find(label)
        assert button is not None, f"no button {label!r} in {[b.text for b in self.buttons]}"
        return button.callback_data


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Call] = []
        self._next_id = 100

    async def close(self) -> None:  # pragma: no cover
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(Call(type(method).__name__, method))
        if isinstance(method, SendMessage | EditMessageText):
            if isinstance(method, SendMessage):
                self._next_id += 1
                message_id = self._next_id
            else:
                message_id = method.message_id
            return Message.model_validate(
                {
                    "message_id": message_id,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": method.chat_id, "type": "private"},
                    "from": {"id": 999, "is_bot": True, "first_name": "Bot"},
                    "text": method.text,
                },
                context={"bot": bot},
            )
        return True


class Harness:
    """Feeds updates through a real Dispatcher with the router under test."""

    def __init__(
        self,
        *,
        admin_ids=frozenset(),
        model=None,
        with_events=True,
        variant=None,
        with_engagement=False,
    ) -> None:
        self.assistant = FakeAssistant()
        self.profiles = FakeProfiles()
        self.reminders = FakeReminders()
        self.events = FakeEvents() if with_events else None
        self.engagement = FakeEngagement() if with_engagement else None
        self.experiments = (
            FakeExperiments(self.events, variant) if variant and self.events else None
        )
        self.session = FakeSession()
        self.bot = Bot(token="123:test", session=self.session)
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(create_reminder_actions_router(self.reminders, self.events))
        if self.engagement is not None:
            # Same order as the bot entrypoint: the 7-day plan router before the main one.
            from alpha_course.telegram.engagement import create_engagement_router

            self.dispatcher.include_router(
                create_engagement_router(
                    self.engagement, self.assistant, self.profiles, events=self.events
                )
            )
        self.dispatcher.include_router(
            create_router(
                self.assistant,
                self.profiles,
                self.reminders,
                events=self.events,
                model=model,
                admin_ids=admin_ids,
                experiments=self.experiments,
                engagement=self.engagement,
            )
        )
        self._update_id = 0

    def _key(self, user: int) -> StorageKey:
        return StorageKey(bot_id=self.bot.id, chat_id=user, user_id=user)

    async def state_data(self, user: int = 1) -> dict:
        return await self.dispatcher.storage.get_data(self._key(user))

    async def set_state_data(self, user: int = 1, **values) -> None:
        await self.dispatcher.storage.update_data(self._key(user), values)

    @property
    def calls(self) -> list[Call]:
        return self.session.calls

    def texts(self, *names: str) -> list[Call]:
        names = names or ("SendMessage", "EditMessageText")
        return [call for call in self.calls if call.name in names and call.text is not None]

    @property
    def last(self) -> Call:
        return self.texts()[-1]

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int) -> list[Call]:
        return self.calls[mark:]

    async def _feed(self, payload: dict) -> None:
        self._update_id += 1
        update = Update.model_validate({"update_id": self._update_id, **payload})
        await self.dispatcher.feed_update(self.bot, update)

    async def send(self, text: str, *, user: int = 1) -> list[Call]:
        mark = self.mark()
        await self._feed(
            {
                "message": {
                    "message_id": 1000 + self._update_id,
                    "date": 0,
                    "chat": {"id": user, "type": "private"},
                    "from": {"id": user, "is_bot": False, "first_name": "T"},
                    "text": text,
                }
            }
        )
        return self.since(mark)

    async def click(self, data: str, *, user: int = 1, source: Call | None = None) -> list[Call]:
        mark = self.mark()
        markup = (
            source.reply_markup
            if source and isinstance(source.reply_markup, InlineKeyboardMarkup)
            else None
        )
        message = {
            "message_id": 500,
            "date": 0,
            "chat": {"id": user, "type": "private"},
            "from": {"id": 999, "is_bot": True, "first_name": "Bot"},
            "text": "x",
        }
        if markup is not None:
            message["reply_markup"] = markup.model_dump(mode="json", exclude_none=True)
        await self._feed(
            {
                "callback_query": {
                    "id": str(self._update_id),
                    "chat_instance": "0",
                    "from": {"id": user, "is_bot": False, "first_name": "T"},
                    "message": message,
                    "data": data,
                }
            }
        )
        return self.since(mark)

    async def aclose(self) -> None:
        await self.bot.session.close()


# --------------------------------------------------------------------------- html validation
class _Validator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"tag <{tag}> not allowed")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unbalanced </{tag}>")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if "<" in data or ">" in data:
            self.errors.append(f"raw angle bracket in {data!r}")
        if "&" in data:
            self.errors.append(f"raw ampersand in {data!r}")

    def handle_entityref(self, name):
        if name not in {"lt", "gt", "amp", "quot"}:
            self.errors.append(f"entity &{name};")

    def handle_charref(self, name):
        self.errors.append(f"charref &#{name};")

    def handle_startendtag(self, tag, attrs):
        self.errors.append(f"self-closing <{tag}/>")


def html_errors(text: str) -> list[str]:
    parser = _Validator()
    parser.feed(text)
    parser.close()
    if parser.stack:
        parser.errors.append(f"unclosed {parser.stack}")
    return parser.errors


def assert_valid_html(text: str) -> None:
    errors = html_errors(text)
    assert not errors, f"{errors} in {text!r}"


def assert_all_html_valid(calls) -> None:
    for call in calls:
        if call.parse_mode == "HTML" and call.text is not None:
            assert_valid_html(call.text)


async def _fake_general_help(self, message, *, profile=None, document=False, history=None):
    self.general_calls.append(message)
    return self.general_reply


FakeAssistant.general_help = _fake_general_help
