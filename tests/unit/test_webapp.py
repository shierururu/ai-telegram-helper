"""Mini App backend: auth, rate limit, every endpoint's happy path and the main error paths."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer

from alpha_course.application.assistant import Clarification, Recommendation, Reply
from alpha_course.application.engagement import (
    Journey,
    JourneyDayNotReached,
    JourneyStep,
    NotificationSettings,
)
from alpha_course.application.offers import CourseOffer
from alpha_course.application.profile import BusinessProfile
from alpha_course.application.reminders import NotFound, ReminderError, StaleReminder
from alpha_course.domain.reminders import ReminderFrequency
from alpha_course.webapp import Services, create_app
from alpha_course.webapp.auth import AuthError, dev_user, validate_init_data
from alpha_course.webapp.server import RateLimiter
from tests.unit.telegram_fakes import FakeAssistant, FakeEvents, FakeExperiments, FakeProfiles

TOKEN = "123456:TEST-ONLY-TOKEN"
DEV = {"X-Dev-User": "4242"}


# --------------------------------------------------------------------------- fakes
def sign(user_id: int = 7, *, token: str = TOKEN, age: int = 0, tamper: bool = False) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "T"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["user"] = json.dumps({"id": user_id + 1, "first_name": "T"}, separators=(",", ":"))
    return urlencode(fields)


class WebAssistant(FakeAssistant):
    def __init__(self) -> None:
        super().__init__()
        self.simplified = ("Простыми словами.", [("ЕНП", "единый платёж")])
        self.steps: list[dict] | None = None

    async def simplify(self, question, answer):
        return self.simplified

    async def journey_steps(self, goal, profile, route):
        if self.steps is not None:
            return self.steps
        pool = [item for item in self.knowledge.materials.values() if not item.is_paid]
        return [
            {
                "title": pool[index % len(pool)].title,
                "url": pool[index % len(pool)].url,
                "material_id": pool[index % len(pool)].id,
                "task": f"Шаг {index + 1}",
            }
            for index in range(7)
        ]


class FakeEngagement:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.settings: dict[int, NotificationSettings] = {}
        self.journeys: dict[int, Journey] = {}

    async def get_settings(self, owner):
        return self.settings.get(owner, NotificationSettings())

    async def save_settings(self, owner, **changes):
        updated = replace(await self.get_settings(owner), **changes)
        NotificationSettings.__post_init__(updated)
        self.settings[owner] = updated
        return updated

    async def create_journey(self, owner, operation_id, goal, timezone, local_time, steps):
        if owner in self.journeys:
            raise ReminderError("У тебя уже есть активный маршрут на неделю.")
        start = self.clock() + timedelta(days=1)
        built = tuple(
            JourneyStep(
                day=index + 1,
                title=raw["title"],
                url=raw["url"],
                material_id=raw["material_id"],
                task=raw["task"],
                due_utc=start + timedelta(days=index),
            )
            for index, raw in enumerate(steps)
        )
        journey = Journey(uuid4(), owner, goal, timezone, local_time, "active", built)
        self.journeys[owner] = journey
        return journey

    async def get_journey(self, owner):
        return self.journeys.get(owner)

    async def mark_done(self, owner, journey_id, day):
        journey = self.journeys.get(owner)
        if journey is None or journey.id != journey_id:
            return None
        step = next((item for item in journey.steps if item.day == day), None)
        if step is None:
            raise ReminderError("В маршруте нет такого дня.")
        if step.due_utc > self.clock():
            raise JourneyDayNotReached("Этот день ещё не наступил.")
        steps = tuple(item.mark_done() if item.day == day else item for item in journey.steps)
        self.journeys[owner] = replace(journey, steps=steps)
        return self.journeys[owner]

    async def stop_journey(self, owner, journey_id):
        return self.journeys.pop(owner, None) is not None


class FakeReminderStore:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.records: list[SimpleNamespace] = []
        self.limit = 5

    def _build(self, owner, schedule, links, ident=None, version=1, kind="normal"):
        due = schedule.first_due()
        if due <= self.clock():
            raise ReminderError("Выбери дату и время в будущем.")
        return SimpleNamespace(
            id=ident or uuid4(),
            owner_id=owner,
            version=version,
            event_text=schedule.event_text,
            frequency=schedule.frequency,
            timezone=schedule.timezone,
            local_time=schedule.local_at.time(),
            weekday=schedule.local_at.weekday()
            if schedule.frequency == ReminderFrequency.WEEKLY
            else None,
            month_day=schedule.day_of_month
            if schedule.frequency in (ReminderFrequency.MONTHLY, ReminderFrequency.YEARLY)
            else None,
            next_due_utc=due,
            link_url=links.get("link_url"),
            link_title=links.get("link_title"),
            status="active",
            kind=kind,
            operation_id=None,
        )

    async def create(self, owner, operation_id, schedule, **links):
        prior = next(
            (r for r in self.records if r.owner_id == owner and r.operation_id == operation_id),
            None,
        )
        if prior:
            return prior
        active = [r for r in self.records if r.owner_id == owner and r.status == "active"]
        if len(active) >= self.limit:
            raise ReminderError("У тебя уже пять активных напоминаний. Отключи одно из них.")
        record = self._build(owner, schedule, links)
        record.operation_id = operation_id
        self.records.append(record)
        return record

    async def list(self, owner):
        return [
            r
            for r in self.records
            if r.owner_id == owner and r.status == "active" and r.kind != "journey"
        ]

    async def get(self, owner, ident):
        return next((r for r in self.records if r.id == ident and r.owner_id == owner), None)

    async def update(self, owner, ident, version, schedule, **links):
        record = await self.get(owner, ident)
        if record is None:
            raise NotFound("Напоминание не найдено.")
        if record.version != version:
            raise StaleReminder("Напоминание уже изменилось. Открой список заново.")
        fresh = self._build(owner, schedule, links, ident=ident, version=version + 1)
        fresh.link_url, fresh.link_title = record.link_url, record.link_title
        fresh.operation_id = record.operation_id
        self.records = [r for r in self.records if r.id != ident] + [fresh]
        return fresh

    async def disable(self, owner, ident, *, version=None):
        record = await self.get(owner, ident)
        if record is None:
            raise NotFound("Напоминание не найдено.")
        if version is not None and record.version != version:
            raise StaleReminder("Напоминание уже изменилось. Открой список заново.")
        record.status = "disabled"
        return record

    async def snooze(self, owner, ident, kind, token=""):
        record = await self.get(owner, ident)
        if record is None:
            raise NotFound("Напоминание не найдено.")
        due = self.clock() + (timedelta(hours=1) if kind == "h1" else timedelta(days=1))
        local = due.astimezone(__import__("zoneinfo").ZoneInfo(record.timezone))
        from alpha_course.application.reminders import Schedule

        schedule = Schedule(
            record.event_text,
            local.replace(tzinfo=None, second=0, microsecond=0),
            record.timezone,
            ReminderFrequency.ONCE,
        )
        copy = self._build(owner, schedule, {"link_url": record.link_url})
        self.records.append(copy)
        return copy


class Rig:
    def __init__(self, *, dev: bool = True, limiter: RateLimiter | None = None) -> None:
        self.now = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
        clock = lambda: self.now  # noqa: E731
        self.assistant = WebAssistant()
        self.profiles = FakeProfiles()
        self.events = FakeEvents()
        self.reminders = FakeReminderStore(clock)
        self.engagement = FakeEngagement(clock)
        self.model = None
        self.services = Services(
            assistant=self.assistant,
            profiles=self.profiles,
            reminders=self.reminders,
            engagement=self.engagement,
            events=self.events,
            model=self.model,
            clock=clock,
        )
        self.app = create_app(self.services, bot_token=TOKEN, dev=dev, limiter=limiter)
        self.client: TestClient | None = None

    async def __aenter__(self) -> Rig:
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.close()

    async def call(self, method, path, body=None, *, headers=DEV, **kwargs):
        response = await self.client.request(method, path, json=body, headers=headers, **kwargs)
        return response.status, await response.json()


@pytest.fixture
async def rig():
    async with Rig() as value:
        yield value


# --------------------------------------------------------------------------- auth
def test_init_data_valid_and_rejections():
    assert validate_init_data(sign(7), TOKEN) == 7
    with pytest.raises(AuthError):
        validate_init_data(sign(7, tamper=True), TOKEN)
    with pytest.raises(AuthError):
        validate_init_data(sign(7), "999:OTHER")
    with pytest.raises(AuthError):
        validate_init_data(sign(7, age=25 * 3600), TOKEN)
    with pytest.raises(AuthError):
        validate_init_data("", TOKEN)
    with pytest.raises(AuthError):
        validate_init_data("auth_date=1&user=%7B%7D", TOKEN)
    assert validate_init_data(sign(7, age=23 * 3600), TOKEN) == 7


def test_dev_header_needs_flag_loopback_and_no_proxy():
    assert dev_user({"X-Dev-User": "5"}, "127.0.0.1", enabled=True) == 5
    assert dev_user({"X-Dev-User": "5"}, "::1", enabled=True) == 5
    for headers, remote, enabled in [
        ({"X-Dev-User": "5"}, "10.0.0.8", True),
        ({"X-Dev-User": "5"}, "127.0.0.1", False),
        ({"X-Dev-User": "5", "X-Forwarded-For": "1.2.3.4"}, "127.0.0.1", True),
        ({"X-Dev-User": "abc"}, "127.0.0.1", True),
        ({}, "127.0.0.1", True),
    ]:
        with pytest.raises(AuthError):
            dev_user(headers, remote, enabled=enabled)


async def test_api_requires_auth(rig):
    status, body = await rig.call("GET", "/api/profile", headers={})
    assert status == 401 and body["error"] == "unauthorized" and body["message"]
    status, _ = await rig.call("GET", "/api/profile", headers={"X-Telegram-Init-Data": "x=1"})
    assert status == 401


async def test_init_data_header_identifies_user(rig):
    rig.profiles.data[7] = replace(rig.profiles.data.get(7) or __profile(), stage="идея")
    headers = {"X-Telegram-Init-Data": sign(7)}
    status, body = await rig.call("GET", "/api/profile", headers=headers)
    assert status == 200 and body["fields"]["stage"] == "идея"
    status, body = await rig.call(
        "GET", "/api/profile", headers={"X-Telegram-Init-Data": sign(7, tamper=True)}
    )
    assert status == 401


def __profile():
    from alpha_course.application.profile import BusinessProfile

    return BusinessProfile()


async def test_dev_header_rejected_when_dev_mode_off():
    async with Rig(dev=False) as rig:
        status, body = await rig.call("GET", "/api/profile")
        assert status == 401 and body["error"] == "unauthorized"


async def test_healthz_static_and_unknown_api():
    async with Rig() as rig:
        response = await rig.client.get("/healthz")
        assert response.status == 200 and await response.text() == "ok"
        index = await rig.client.get("/")
        assert index.status == 200
        assert index.headers["Cache-Control"] == "no-cache"
        assert "telegram.org" in index.headers["Content-Security-Policy"]
        assert (await rig.client.get("/static/../server.py")).status in (400, 404)
        status, body = await rig.call("GET", "/api/nope")
        assert status == 404 and body["error"] == "not_found"


# --------------------------------------------------------------------------- consultation
async def test_ask_returns_reply_shape_with_materials_and_saves_profile(rig):
    free = rig.assistant.knowledge.materials["free1"]
    paid = rig.assistant.knowledge.materials["paid1"]
    rig.assistant.next_reply = Reply(
        kind="answer",
        status="answered",
        summary="Выбери УСН.",
        steps=["Посчитай доход"],
        personal="Тебе подойдёт.",
        sources=[free],
        recommendations=[Recommendation(free, "для старта"), Recommendation(paid, "глубже")],
        followups=["А для ООО?"],
        profile_updates={"stage": "запуск", "employees": False},
    )
    status, body = await rig.call(
        "POST",
        "/api/ask",
        {"question": "как выбрать налоги", "history": [{"role": "user", "text": "привет"}]},
    )
    assert status == 200
    assert body["kind"] == "answer" and body["status"] == "answered"
    assert body["summary"] == "Выбери УСН." and body["steps"] == ["Посчитай доход"]
    assert body["sources"][0]["url"] == free.url
    recs = body["recommendations"]
    assert recs[0]["material"]["id"] == "free1" and recs[0]["why"] == "для старта"
    assert recs[1]["material"]["is_paid"] is True
    assert recs[1]["material"]["purchase_url"] == paid.purchase_url
    assert recs[1]["material"]["price"] == "4900 ₽"
    assert body["clarification"] is None and body["reminder_event"] is None
    assert body["profile_updates"] == {"stage": "запуск", "employees": False}
    saved = rig.profiles.data[4242]
    assert saved.stage == "запуск" and saved.employees is False
    assert [kind for kind in rig.events.kinds if kind != "user_active"] == [
        "question_asked",
        "profile_updated",
        "answer_shown",
    ]
    call = rig.assistant.reply_calls[0]
    assert call["history"] == [{"role": "user", "text": "привет"}] and call["allow_clarify"]


@pytest.mark.parametrize("variant", ["A", "B"])
async def test_ask_offers_paid_course_by_ab_variant(rig, variant):
    rig.services.experiments = FakeExperiments(rig.events, variant)
    free = rig.assistant.knowledge.materials["free1"]
    paid = rig.assistant.knowledge.materials["paid1"]
    rig.profiles.data[4242] = BusinessProfile(legal_form="ИП")
    rig.assistant.next_reply = Reply(
        kind="answer",
        summary="x",
        recommendations=[Recommendation(free), Recommendation(paid)],
        course_offer=CourseOffer(paid, "profile", ("ИП",)),
    )
    _, body = await rig.call("POST", "/api/ask", {"question": "налоги"})
    offer = body["offer"]
    assert offer["variant"] == variant and offer["material"]["id"] == "paid1"
    assert ("pitch" in offer) is (variant == "B")
    if variant == "B":
        # The pitch states the application's real match, not every filled profile field.
        assert offer["pitch"]["fit"] == "Подобран по профилю: ИП."
    assert rig.services.experiments.exposed == [(4242, "paid1", "miniapp")]
    await rig.call("POST", "/api/click", {"material_id": "free1"})
    await rig.call("POST", "/api/click", {"material_id": "paid1"})
    clicks = [p for _, kind, p in rig.events.logged if kind == "paid_offer_click"]
    assert clicks == [{"material_id": "paid1", "variant": variant}]


async def test_ask_without_experiments_has_no_offer(rig):
    rig.assistant.next_reply = Reply(kind="answer", summary="x")
    _, body = await rig.call("POST", "/api/ask", {"question": "налоги"})
    assert body["offer"] is None


async def test_ask_clarify_and_reminder_kinds(rig):
    rig.assistant.next_reply = Reply(
        kind="clarify", clarification=Clarification("Что именно?", ["А", "Б"])
    )
    _, body = await rig.call("POST", "/api/ask", {"question": "налоги", "allow_clarify": False})
    assert body["clarification"] == {"question": "Что именно?", "options": ["А", "Б"]}
    assert rig.assistant.reply_calls[-1]["allow_clarify"] is False
    rig.assistant.next_reply = Reply(kind="reminder", summary="оплатить аренду")
    _, body = await rig.call("POST", "/api/ask", {"question": "напомни про аренду"})
    assert body["reminder_event"] == "оплатить аренду"


@pytest.mark.parametrize(
    "payload", [{}, {"question": ""}, {"question": 5}, {"question": "я" * 1600}]
)
async def test_ask_validates_question(rig, payload):
    status, body = await rig.call("POST", "/api/ask", payload)
    assert status == 400 and body["error"] == "bad_request" and body["message"]


async def test_invalid_json_body(rig):
    response = await rig.client.post("/api/ask", data="{oops", headers=DEV)
    assert response.status == 400


async def test_rate_limit_per_user():
    ticks = iter(range(1000))
    limiter = RateLimiter(limit=3, window=60, clock=lambda: next(ticks) / 100)
    async with Rig(limiter=limiter) as rig:
        for _ in range(3):
            status, _ = await rig.call("POST", "/api/ask", {"question": "вопрос"})
            assert status == 200
        status, body = await rig.call("POST", "/api/ask", {"question": "вопрос"})
        assert status == 429 and body["error"] == "rate_limited"
        # Another user and unlimited endpoints are unaffected.
        status, _ = await rig.call(
            "POST", "/api/ask", {"question": "вопрос"}, headers={"X-Dev-User": "99"}
        )
        assert status == 200
        status, _ = await rig.call("GET", "/api/bootstrap")
        assert status == 200


async def test_simplify_feedback_click(rig):
    status, body = await rig.call(
        "POST", "/api/simplify", {"question": "q", "answer": "Длинный ответ " * 10}
    )
    assert status == 200
    assert body == {
        "text": "Простыми словами.",
        "terms": [{"term": "ЕНП", "meaning": "единый платёж"}],
    }
    assert (await rig.call("POST", "/api/feedback", {"vote": "up"}))[0] == 200
    assert (await rig.call("POST", "/api/feedback", {"vote": "meh"}))[0] == 400
    assert (await rig.call("POST", "/api/click", {"material_id": "free1"}))[0] == 200
    assert (await rig.call("POST", "/api/click", {"material_id": "nope"}))[0] == 404
    assert "feedback_up" in rig.events.kinds
    assert (4242, "material_click", {"material_id": "free1"}) in rig.events.logged


async def test_materials_search(rig):
    status, body = await rig.call("GET", "/api/materials/search?q=налоги ИП режимы")
    assert status == 200
    assert body["items"] and body["items"][0]["id"] == "free1" and body["items"][0]["url"]
    assert (await rig.call("GET", "/api/materials/search?q=а"))[1] == {"items": []}


# --------------------------------------------------------------------------- profile
async def test_profile_put_get_describe_clear_onboarding(rig):
    status, body = await rig.call(
        "PUT",
        "/api/profile",
        {
            "fields": {
                "stage": "запуск",
                "industry": "общепит",
                "legal_form": "ИП",
                "employees": True,
                "level": "novice",
                "goal": "найти клиентов",
            }
        },
    )
    assert status == 200
    assert body["fields"]["stage"] == "запуск" and body["fields"]["employees"] is True
    assert body["labels"]["stage"] == "запуск" and body["labels"]["level"] == "новичок"
    status, body = await rig.call("PUT", "/api/profile", {"fields": {"stage": None}})
    assert body["fields"]["stage"] is None and body["fields"]["industry"] == "общепит"

    for bad in (
        {"stage": "космос"},
        {"unknown": "x"},
        {"employees": "да"},
        {"level": "guru"},
        {"tax_regime": 5},
    ):
        status, body = await rig.call("PUT", "/api/profile", {"fields": bad})
        assert status == 400, bad
    assert (await rig.call("PUT", "/api/profile", {}))[0] == 400

    rig.assistant.extracted = {"tax_regime": "ПСН"}
    status, body = await rig.call("POST", "/api/profile/describe", {"text": "я на патенте"})
    assert body["updates"] == {"tax_regime": "ПСН"}
    assert body["profile"]["fields"]["tax_regime"] == "ПСН"

    assert (await rig.call("POST", "/api/onboarding/done", {}))[1] == {"ok": True}
    assert 4242 in rig.profiles.onboarded
    status, body = await rig.call("DELETE", "/api/profile")
    assert status == 200 and body["labels"] == {} and rig.profiles.cleared == [4242]


async def test_self_employed_gets_npd_by_default(rig):
    _, body = await rig.call("PUT", "/api/profile", {"fields": {"legal_form": "самозанятый"}})
    assert body["fields"]["tax_regime"] == "НПД"


async def test_bootstrap(rig):
    status, body = await rig.call("GET", "/api/bootstrap")
    assert status == 200
    assert body["brand"] == "Бизнес-навигатор" and body["onboarded"] is False
    assert body["profile"]["labels"] == {} and body["journey"] is None
    assert body["reminders"] == [] and body["taxes_ready"] is False
    assert body["settings"]["quiet_start"] == "22:00" and body["settings"]["weekly_limit"] == 7
    options = body["options"]
    assert "запуск" in options["stages"] and "ИП" in options["legal_forms"]
    assert options["goals"] and "ОСНО" in options["tax_regimes"]


# --------------------------------------------------------------------------- route
async def test_old_route_endpoints_are_obsolete_without_progress_mutation(rig):
    for path, body in [("/api/route", {}), ("/api/route/done", {"index": 0})]:
        status, response = await rig.call("POST", path, body)
        assert status == 410 and response["error"] == "route_obsolete"
    assert rig.assistant.plan_calls == []
    assert rig.engagement.journeys == {}


# --------------------------------------------------------------------------- journey
async def test_journey_preview_needs_timezone_and_goal(rig):
    status, body = await rig.call("POST", "/api/journey/preview", {"time": "09:00", "goal": "x"})
    assert status == 409 and body["error"] == "need_timezone"
    await rig.call("PUT", "/api/settings", {"city": "Омск"})
    status, body = await rig.call("POST", "/api/journey/preview", {"time": "09:00"})
    assert status == 409 and body["error"] == "need_goal"
    status, body = await rig.call("POST", "/api/journey/preview", {"time": "25:00", "goal": "x"})
    assert status == 400


async def test_journey_full_cycle(rig):
    await rig.call("PUT", "/api/settings", {"city": "Омск"})
    status, preview = await rig.call(
        "POST", "/api/journey/preview", {"goal": "найти клиентов", "time": "09:00"}
    )
    assert status == 200 and len(preview["steps"]) == 7
    assert preview["timezone"] == "Asia/Omsk" and preview["steps"][0]["day"] == 1
    assert preview["steps"][0]["url"].startswith("https://")
    # Steps from the client cannot smuggle links or paid materials.
    forged = [dict(step, url="https://evil.test/") for step in preview["steps"]]
    status, journey = await rig.call(
        "POST", "/api/journey", {"goal": "найти клиентов", "time": "09:00", "steps": forged}
    )
    assert status == 200
    assert all(step["url"].startswith("https://example.test/") for step in journey["steps"])
    assert (
        journey["status"] == "active" and journey["time"] == "09:00" and journey["done_days"] == 0
    )
    assert [step["state"] for step in journey["steps"]] == ["locked"] * 7
    status, body = await rig.call(
        "POST",
        "/api/journey",
        {"goal": "x", "time": "09:00", "steps": [{"material_id": "paid1", "task": "t"}] * 7},
    )
    assert status == 400
    assert (await rig.call("POST", "/api/journey", {"goal": "x", "time": "09:00", "steps": []}))[
        0
    ] == 400

    status, body = await rig.call("POST", "/api/journey/done", {"day": 1})
    assert status == 409 and body["error"] == "day_not_reached"

    rig.now += timedelta(days=2, hours=1)  # days 1 and 2 are reached
    status, journey = await rig.call("GET", "/api/journey")
    assert [step["state"] for step in journey["steps"][:3]] == ["missed", "today", "locked"]
    status, journey = await rig.call("POST", "/api/journey/done", {"day": 1})
    assert status == 200 and journey["done_days"] == 1 and journey["steps"][0]["state"] == "done"
    count = rig.events.kinds.count("learning_marked_done")
    marks = [payload for _, kind, payload in rig.events.logged if kind == "learning_marked_done"]
    assert marks[-1]["material_id"] == journey["steps"][0]["material_id"]
    assert marks[-1]["material_kind"] == "article"
    assert marks[-1]["journey_id"] == journey["id"] and marks[-1]["day"] == 1
    await rig.call("POST", "/api/journey/done", {"day": 1})
    assert rig.events.kinds.count("learning_marked_done") == count
    assert (await rig.call("POST", "/api/journey/done", {"day": 9}))[0] == 400

    assert (await rig.call("DELETE", "/api/journey"))[1] == {"ok": True}
    assert (await rig.call("GET", "/api/journey"))[1] is None
    assert (await rig.call("POST", "/api/journey/done", {"day": 1}))[0] == 404


async def test_journey_conflict_when_one_is_active(rig):
    await rig.call("PUT", "/api/settings", {"city": "Омск"})
    _, preview = await rig.call("POST", "/api/journey/preview", {"goal": "g", "time": "09:00"})
    body = {"goal": "g", "time": "09:00", "steps": preview["steps"]}
    assert (await rig.call("POST", "/api/journey", body))[0] == 200
    status, error = await rig.call("POST", "/api/journey", body)
    assert status == 409 and error["error"] == "conflict"


# --------------------------------------------------------------------------- taxes
async def test_taxes_not_ready_then_ready_and_reminders(rig):
    status, body = await rig.call("GET", "/api/taxes")
    assert body == {"ready": False, "missing": ["legal_form", "tax_regime"]}
    await rig.call("PUT", "/api/profile", {"fields": {"legal_form": "ИП", "tax_regime": "ПСН"}})
    _, body = await rig.call("GET", "/api/taxes")
    assert body["missing"] == ["employees"]
    await rig.call(
        "PUT",
        "/api/profile",
        {"fields": {"tax_regime": "УСН «Доходы»", "employees": False}},
    )
    status, body = await rig.call("GET", "/api/taxes")
    assert status == 200 and body["ready"] is True
    assert body["profile_labels"]["tax_regime"] == "УСН «Доходы»"
    assert 1 <= len(body["items"]) <= 6 and body["next"] == body["items"][0]
    first = body["items"][0]
    assert first["date"] >= "2026-11-10" and first["kind"] in ("payment", "report", "notice")
    assert isinstance(first["shifted"], bool) and first["note"] and body["explainer"]

    status, body = await rig.call("POST", "/api/taxes/remind", {})
    assert status == 200 and body["created"] >= 1
    created = body["created"]
    assert (await rig.call("POST", "/api/taxes/remind", {}))[1]["created"] == 0
    _, listed = await rig.call("GET", "/api/reminders")
    assert len(listed["reminders"]) == created
    assert listed["reminders"][0]["event"].startswith("🧾")


async def test_taxes_remind_needs_profile(rig):
    status, body = await rig.call("POST", "/api/taxes/remind", {})
    assert status == 409 and body["error"] == "profile_incomplete"


# --------------------------------------------------------------------------- reminders
def future(rig, **delta) -> str:
    when = (rig.now + timedelta(**(delta or {"days": 1}))).astimezone(UTC)
    return when.replace(tzinfo=None, minute=0, second=0, microsecond=0).isoformat(
        timespec="minutes"
    )


async def create_reminder(rig, **overrides):
    body = {
        "event": "Позвонить поставщику",
        "local_at": future(rig),
        "timezone": "Europe/Moscow",
        "frequency": "once",
        "operation_id": str(uuid4()),
    } | overrides
    return await rig.call("POST", "/api/reminders", body)


async def test_reminder_create_list_patch_snooze_disable(rig):
    status, created = await create_reminder(rig, frequency="weekly")
    assert status == 200
    assert created["event"] == "Позвонить поставщику" and created["version"] == 1
    assert created["frequency"] == "weekly" and created["timezone"] == "Europe/Moscow"
    assert created["timing"].startswith("кажд") and created["next_due"].endswith("+00:00")
    assert created["link"] is None and len(created["id"]) == 32

    _, listed = await rig.call("GET", "/api/reminders")
    assert [item["id"] for item in listed["reminders"]] == [created["id"]]

    status, changed = await rig.call(
        "PATCH",
        f"/api/reminders/{created['id']}",
        {"version": 1, "frequency": "daily"},
    )
    assert status == 200 and changed["frequency"] == "daily" and changed["version"] == 2
    assert changed["timing"].startswith("каждый день в ")
    status, stale = await rig.call(
        "PATCH", f"/api/reminders/{created['id']}", {"version": 1, "frequency": "monthly"}
    )
    assert status == 409 and stale["error"] == "stale"

    status, moved = await rig.call(
        "PATCH",
        f"/api/reminders/{created['id']}",
        {"version": 2, "local_at": future(rig, days=3), "timezone": "Asia/Omsk"},
    )
    assert status == 200 and moved["timezone"] == "Asia/Omsk"

    status, snoozed = await rig.call(
        "POST", f"/api/reminders/{created['id']}/snooze", {"version": 3, "kind": "h1"}
    )
    assert status == 200 and snoozed["frequency"] == "once" and snoozed["id"] != created["id"]
    assert (
        await rig.call(
            "POST", f"/api/reminders/{created['id']}/snooze", {"version": 3, "kind": "w1"}
        )
    )[0] == 400

    status, body = await rig.call("DELETE", f"/api/reminders/{created['id']}?version=3")
    assert status == 200 and body == {"ok": True}
    _, listed = await rig.call("GET", "/api/reminders")
    assert [item["id"] for item in listed["reminders"]] == [snoozed["id"]]
    assert (await rig.call("DELETE", f"/api/reminders/{created['id']}"))[0] == 404


async def test_reminder_create_is_idempotent_and_limited(rig):
    operation = str(uuid4())
    _, first = await create_reminder(rig, operation_id=operation)
    _, second = await create_reminder(rig, operation_id=operation)
    assert first["id"] == second["id"]
    for _ in range(4):
        assert (await create_reminder(rig))[0] == 200
    status, body = await create_reminder(rig)
    assert status == 409 and body["error"] == "limit"


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": ""},
        {"local_at": "завтра"},
        {"local_at": "2026-11-12T10:00:00+03:00"},
        {"timezone": "Mars/Base"},
        {"frequency": "hourly"},
        {"operation_id": "nope"},
        {"link": {"url": "https://evil.test/"}},
    ],
)
async def test_reminder_create_validation(rig, overrides):
    status, body = await create_reminder(rig, **overrides)
    assert status == 400 and body["message"]


async def test_reminder_in_the_past_is_rejected(rig):
    status, body = await create_reminder(rig, local_at="2026-11-09T10:00")
    assert status == 400 and body["error"] == "past_time"


async def test_reminder_link_must_be_catalog_material(rig):
    material = rig.assistant.knowledge.materials["free1"]
    status, body = await create_reminder(rig, link={"url": material.url, "title": "подмена"})
    assert status == 200
    assert body["link"] == {"url": material.url, "title": material.title}


async def test_foreign_and_unknown_reminders_are_404(rig):
    _, mine = await create_reminder(rig)
    other = {"X-Dev-User": "555"}
    path = f"/api/reminders/{mine['id']}"
    assert (await rig.call("PATCH", path, {"version": 1}, headers=other))[0] == 404
    assert (await rig.call("DELETE", path, headers=other))[0] == 404
    assert (await rig.call("POST", path + "/snooze", {"version": 1, "kind": "h1"}, headers=other))[
        0
    ] == 404
    missing = uuid4().hex
    assert (await rig.call("DELETE", f"/api/reminders/{missing}"))[0] == 404
    assert (await rig.call("DELETE", "/api/reminders/not-a-uuid"))[0] == 404
    _, listed = await rig.call("GET", "/api/reminders", headers=other)
    assert listed["reminders"] == []
    _, listed = await rig.call("GET", "/api/reminders")
    assert len(listed["reminders"]) == 1


async def test_journey_reminders_are_not_manageable(rig):
    await create_reminder(rig)
    hidden = rig.reminders.records[0]
    hidden.kind = "journey"
    assert (await rig.call("DELETE", f"/api/reminders/{hidden.id.hex}"))[0] == 404
    assert (await rig.call("GET", "/api/reminders"))[1] == {"reminders": []}


async def test_reminder_parse_and_timezone(rig):
    status, body = await rig.call(
        "POST",
        "/api/reminders/parse",
        {"text": "напомни завтра в 15:00 отправить счёт", "timezone": "Europe/Moscow"},
    )
    assert status == 200
    assert body["event"] == "отправить счёт" and body["needs"] == []
    assert body["local_at"] == "2026-11-11T15:00" and body["frequency"] == "once"
    assert body["timezone"] == "Europe/Moscow" and body["link"] is None

    _, body = await rig.call("POST", "/api/reminders/parse", {"text": "завтра в 15 00 позвонить"})
    assert body["event"] == "позвонить" and body["needs"] == ["timezone"]
    assert body["local_at"] is None

    _, body = await rig.call(
        "POST",
        "/api/reminders/parse",
        {"text": "каждый понедельник в 9 планёрка", "timezone": "Asia/Omsk"},
    )
    assert body["frequency"] == "weekly" and body["needs"] == []
    assert body["local_at"].endswith("T09:00")

    _, body = await rig.call(
        "POST", "/api/reminders/parse", {"text": "оплатить аренду", "timezone": "Asia/Omsk"}
    )
    assert body["event"] == "оплатить аренду" and body["needs"] == ["time"]

    _, body = await rig.call(
        "POST",
        "/api/reminders/parse",
        {"text": "оплатить аренду", "when": "завтра в 10:00", "timezone": "Asia/Omsk"},
    )
    assert body["needs"] == [] and body["local_at"] == "2026-11-11T10:00"

    status, body = await rig.call("POST", "/api/reminders/timezone", {"city": "Новосибирск"})
    assert body == {"timezone": "Asia/Novosibirsk"}
    status, body = await rig.call("POST", "/api/reminders/timezone", {"city": "Атлантида"})
    assert status == 400 and body["error"] == "unknown_city"
    assert (await rig.call("POST", "/api/reminders/parse", {"text": "x", "timezone": "Mars/Base"}))[
        0
    ] == 400


async def test_parse_falls_back_to_saved_city(rig):
    await rig.call("PUT", "/api/settings", {"city": "Омск"})
    _, body = await rig.call(
        "POST", "/api/reminders/parse", {"text": "напомни завтра в 18:00 позвонить"}
    )
    assert body["timezone"] == "Asia/Omsk" and body["needs"] == []


# --------------------------------------------------------------------------- settings
async def test_settings_get_put_and_city(rig):
    status, body = await rig.call("GET", "/api/settings")
    assert status == 200 and body["timezone"] is None and body["enabled"] is True
    status, body = await rig.call(
        "PUT",
        "/api/settings",
        {
            "enabled": False,
            "quiet_enabled": True,
            "quiet_start": "23:30",
            "quiet_end": "07:00",
            "weekly_limit": 14,
            "city": "Екатеринбург",
        },
    )
    assert status == 200
    assert body == {
        "timezone": "Asia/Yekaterinburg",
        "weekly_summary": False,
        "weekly_summary_asked": False,
        "quiet_enabled": True,
        "quiet_start": "23:30",
        "quiet_end": "07:00",
        "weekly_limit": 14,
        "enabled": False,
    }
    assert (await rig.call("GET", "/api/settings"))[1] == body
    for bad in (
        {"city": "Атлантида"},
        {"weekly_limit": 5},
        {"enabled": "yes"},
        {"quiet_start": "25:00"},
        {"quiet_start": "08:00", "quiet_end": "08:00"},
    ):
        status, error = await rig.call("PUT", "/api/settings", bad)
        assert status == 400 and error["message"], bad
    assert (await rig.call("PUT", "/api/settings", {}))[1] == body


async def test_ask_free_is_opt_in_and_labelled(rig):
    rig.assistant.general_reply = Reply(kind="ai_help", summary="Общий совет.")
    status, body = await rig.call("POST", "/api/ask/free", {"question": "как назвать кофейню"})
    assert status == 200
    assert body["kind"] == "free" and body["text"] == "Общий совет." and body["available"] is True
    assert body["links"] == [] and "Профиль" in body["note"]
    assert rig.assistant.general_calls == ["как назвать кофейню"]
    rig.assistant.general_reply = Reply(kind="offtopic")
    _, body = await rig.call("POST", "/api/ask/free", {"question": "рецепт борща"})
    assert body["kind"] == "offtopic" and body["text"] == ""
    rig.assistant.general_reply = Reply(kind="ai_help", status="no_evidence", summary="Недоступно")
    _, body = await rig.call("POST", "/api/ask/free", {"question": "вопрос"})
    assert body["available"] is False and body["message"] == "Недоступно"
    status, _ = await rig.call("POST", "/api/ask/free", {})
    assert status == 400


async def test_weekly_summary_explicit_opt_in_and_decline_persist(rig):
    _, boot = await rig.call("GET", "/api/bootstrap")
    assert boot["settings"]["weekly_summary"] is False
    assert boot["settings"]["weekly_summary_asked"] is False
    _, body = await rig.call("PUT", "/api/settings", {"weekly_summary_asked": True})
    assert body["weekly_summary"] is False
    _, body = await rig.call("PUT", "/api/settings", {"weekly_summary": True})
    assert body["weekly_summary"] is True and body["weekly_summary_asked"] is True
    assert body["timezone"] == "Europe/Moscow"
    _, body = await rig.call("PUT", "/api/settings", {"weekly_summary": False})
    assert body["weekly_summary"] is False and body["weekly_summary_asked"] is True
    _, boot = await rig.call("GET", "/api/bootstrap")
    assert boot["settings"]["weekly_summary"] is False
    assert boot["settings"]["weekly_summary_asked"] is True


@pytest.mark.parametrize("field", ["weekly_summary", "weekly_summary_asked"])
async def test_weekly_settings_require_bool(rig, field):
    status, _ = await rig.call("PUT", "/api/settings", {field: "true"})
    assert status == 400


def test_subscription_serializer_hides_stale_price():
    from alpha_course.webapp.serializers import material_json
    from tests.unit.telegram_fakes import material

    item = material("subscription", access="subscription", price="999 ₽")
    payload = material_json(item)
    assert payload["price"] is None
    assert payload["access"] == "subscription"
