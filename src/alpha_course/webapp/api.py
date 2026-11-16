"""JSON handlers of the Mini App. All data flows through the same services as the bot."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from aiohttp import web

from alpha_course.application.assistant import Assistant
from alpha_course.application.copilot import personalization_note
from alpha_course.application.engagement import JOURNEY_DAYS
from alpha_course.application.experiments import CONTROL, offer_pitch
from alpha_course.application.privacy import mask_personal
from alpha_course.application.profile import (
    INDUSTRIES,
    LEGAL_FORMS,
    LEVELS,
    STAGES,
    TAX_REGIMES,
    BusinessProfile,
)
from alpha_course.application.reminder_input import (
    parse_reminder_input,
    resolve_reminder_day,
)
from alpha_course.application.reminders import ReminderError, Schedule, local_options
from alpha_course.application.schedule_ai import parse_when, resolve_timezone
from alpha_course.application.tax_calendar import (
    missing_for_calendar,
    regime_explainer,
    tax_deadlines,
)
from alpha_course.domain.reminders import ReminderFrequency, ReminderStatus, require_timezone
from alpha_course.telegram.engagement import LIMITS
from alpha_course.telegram.router import GOALS
from alpha_course.webapp.serializers import (
    PROFILE_FIELDS,
    journey_json,
    material_json,
    profile_json,
    reminder_json,
    reply_json,
    settings_json,
)

_LOG = logging.getLogger(__name__)
USER_ID = web.RequestKey("user_id", int)
MOSCOW = "Europe/Moscow"
_OPERATION_NAMESPACE = UUID("2ee46bd2-67ec-4ac6-814a-b090ce04dc44")  # shared with the bot
_LOOSE_TIME = re.compile(r"(?<=\bв )(\d{1,2})[ .](\d{2})\b(?![.:]\d)")
_CLOCK = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
_HEX32 = re.compile(r"[0-9a-f]{32}")
PROFILE_ENUMS = {
    "stage": STAGES,
    "industry": INDUSTRIES,
    "legal_form": LEGAL_FORMS,
    "tax_regime": TAX_REGIMES,
    "level": LEVELS,
}
MAX_TEXT = 1500


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def bad(message: str, code: str = "bad_request") -> ApiError:
    return ApiError(400, code, message)


@dataclass
class Services:
    assistant: Assistant
    profiles: Any  # ProfileStore
    reminders: Any  # ReminderStore
    engagement: Any  # EngagementStore
    events: Any = None  # EventStore | None
    model: Any = None  # ChatModel | None, used for city and time understanding
    experiments: Any = None  # Experiments | None: A/B for the paid-course offer
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    @property
    def knowledge(self):
        return self.assistant.knowledge


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(
        data, status=status, dumps=lambda value: json.dumps(value, ensure_ascii=False)
    )


async def read_body(request: web.Request) -> dict:
    raw = await request.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise bad("Не удалось прочитать запрос.") from None
    if not isinstance(data, dict):
        raise bad("Ожидался JSON-объект.")
    return data


def text_field(body: dict, name: str, *, limit: int = MAX_TEXT, required: bool = True) -> str:
    value = body.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise bad(f"Поле «{name}» должно быть строкой.")
    value = " ".join(value.split())
    if required and not value:
        raise bad(f"Поле «{name}» не должно быть пустым.")
    if len(value) > limit:
        raise bad(f"Поле «{name}» длиннее {limit} символов.")
    return value


def int_field(body: dict, name: str, *, required: bool = True) -> int | None:
    value = body.get(name)
    if value is None and not required:
        return None
    if type(value) is not int:
        raise bad(f"Поле «{name}» должно быть целым числом.")
    return value


def parse_clock_value(value: Any, name: str) -> time:
    match = _CLOCK.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise bad(f"Поле «{name}» — время в формате ЧЧ:ММ.")
    return time(int(match[1]), int(match[2]))


def parse_local(value: Any, name: str = "local_at") -> datetime:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is not None:
        raise bad(f"Поле «{name}» — местное время без пояса, например 2026-11-12T10:00.")
    return moment.replace(second=0, microsecond=0)


def check_timezone(value: Any) -> str:
    if not isinstance(value, str):
        raise bad("Укажи часовой пояс.", "bad_timezone")
    try:
        require_timezone(value)
    except ValueError:
        raise bad("Не узнал часовой пояс.", "bad_timezone") from None
    return value


def normalize_time(text: str) -> str:
    """«завтра в 15 00» and «в 9.30» become «в 15:00» and «в 9:30»."""
    return _LOOSE_TIME.sub(r"\1:\2", text)


def reminder_id(raw: str) -> UUID:
    if not _HEX32.fullmatch(raw):
        raise ApiError(404, "not_found", "Напоминание не найдено.")
    return UUID(hex=raw)


class Api:
    """Handlers; the authenticated Telegram user id is `request["user_id"]`."""

    def __init__(self, services: Services) -> None:
        self.s = services
        self._links: dict[str, Any] | None = None

    # ------------------------------------------------------------------ helpers
    def owner(self, request: web.Request) -> int:
        return request[USER_ID]

    async def log(self, owner: int, kind: str, **payload: Any) -> None:
        if self.s.events is not None:
            try:
                await self.s.events.log(owner, kind, **payload)
                if kind != "user_active":
                    await self.s.events.log(owner, "user_active")
            except Exception:
                _LOG.warning("event_log_failed")

    async def save_profile(self, owner: int, updates: dict) -> BusinessProfile:
        before = await self.s.profiles.load(owner)
        after = before.merged(**updates)
        if after != before:
            await self.s.profiles.save(owner, after)
            await self.log(owner, "profile_updated", fields=sorted(updates))
        return after

    def link_for(self, url: str):
        """Reminder links may only point to materials of the catalog (D-04)."""
        if self._links is None:
            links: dict[str, Any] = {}
            for item in self.s.knowledge.materials.values():
                links[item.url] = item
                if item.purchase_url:
                    links.setdefault(item.purchase_url, item)
            self._links = links
        return self._links.get(url)

    @staticmethod
    def taxes_missing(profile: BusinessProfile) -> list[str]:
        missing = missing_for_calendar(profile.legal_form, profile.tax_regime)
        if profile.legal_form in ("ИП", "ООО") and profile.employees is None:
            missing.append("employees")
        return missing

    # ------------------------------------------------------------------ bootstrap
    async def bootstrap(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        profile, onboarded, settings, journey, records = await asyncio.gather(
            self.s.profiles.load(owner),
            self.s.profiles.is_onboarded(owner),
            self.s.engagement.get_settings(owner),
            self.s.engagement.get_journey(owner),
            self.s.reminders.list(owner),
        )
        return json_response(
            {
                "profile": profile_json(profile),
                "onboarded": onboarded,
                "settings": settings_json(settings),
                "journey": journey_json(journey, self.s.clock()) if journey else None,
                "reminders": [reminder_json(item) for item in records],
                "options": {
                    "stages": list(STAGES),
                    "industries": list(INDUSTRIES),
                    "legal_forms": list(LEGAL_FORMS),
                    "tax_regimes": list(TAX_REGIMES),
                    "goals": list(GOALS),
                    "levels": [
                        {"value": "novice", "label": "Новичок"},
                        {"value": "experienced", "label": "Есть опыт"},
                    ],
                },
                "taxes_ready": not self.taxes_missing(profile),
                "brand": "Бизнес-навигатор",
            }
        )

    # ------------------------------------------------------------------ consultation
    async def ask(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        question = text_field(body, "question")
        history = []
        raw_history = body.get("history")
        for turn in raw_history[-6:] if isinstance(raw_history, list) else []:
            if isinstance(turn, dict) and turn.get("role") in ("user", "assistant"):
                value = turn.get("text")
                if isinstance(value, str) and value.strip():
                    history.append({"role": turn["role"], "text": value[:600]})
        allow_clarify = body.get("allow_clarify", True) is not False
        profile = await self.s.profiles.load(owner)
        await self.log(owner, "question_asked", length=len(question))
        reply = await self.s.assistant.reply(
            question, profile=profile, history=history, allow_clarify=allow_clarify
        )
        if reply.profile_updates:
            await self.save_profile(owner, reply.profile_updates)
        offer = None
        if reply.kind == "answer":
            await self.log(
                owner,
                "answer_shown",
                status=reply.status,
                materials=[item.material.id for item in reply.recommendations],
            )
            paid = next(
                (item.material for item in reply.recommendations if item.material.is_paid), None
            )
            if paid is not None and self.s.experiments is not None:
                variant = await self.s.experiments.expose(owner, paid.id, "miniapp")
                offer = {"variant": variant, "material": material_json(paid)}
                if variant != CONTROL:
                    offer["pitch"] = offer_pitch(paid, profile, reply.course_offer)
        elif reply.kind == "clarify":
            await self.log(owner, "clarification_shown")
        elif reply.kind == "offtopic":
            await self.log(owner, "offtopic")
        return json_response(reply_json(reply) | {"offer": offer})

    async def ask_free(self, request: web.Request) -> web.Response:
        """Explicit opt-in answer without the knowledge base; clearly labelled in the UI."""
        owner = self.owner(request)
        body = await read_body(request)
        question = text_field(body, "question")
        profile = await self.s.profiles.load(owner)
        await self.log(owner, "ai_fallback_used")
        reply = await self.s.assistant.general_help(question, profile=profile)
        if reply.kind == "offtopic":
            return json_response({"kind": "offtopic", "text": "", "available": True})
        failed = reply.status == "no_evidence"
        return json_response(
            {
                "kind": "free",
                "text": "" if failed else reply.summary,
                "available": not failed,
                "message": reply.summary if failed else "",
                "title": reply.title,
                "links": [] if failed else reply.links,
                "note": "" if failed else personalization_note(profile),
            }
        )

    async def simplify(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        question = text_field(body, "question", required=False)
        answer = text_field(body, "answer", limit=4000)
        text, terms = await self.s.assistant.simplify(question, answer[:2500])
        if text:
            await self.log(owner, "simplified", terms=len(terms))
        return json_response(
            {"text": text, "terms": [{"term": term, "meaning": meaning} for term, meaning in terms]}
        )

    async def feedback(self, request: web.Request) -> web.Response:
        body = await read_body(request)
        vote = body.get("vote")
        if vote not in ("up", "down"):
            raise bad("Оценка: «up» или «down».")
        await self.log(self.owner(request), f"feedback_{vote}")
        return json_response({"ok": True})

    async def click(self, request: web.Request) -> web.Response:
        body = await read_body(request)
        material_id = text_field(body, "material_id", limit=200)
        if material_id not in self.s.knowledge.materials:
            raise ApiError(404, "not_found", "Материал не найден.")
        owner = self.owner(request)
        await self.log(owner, "material_click", material_id=material_id)
        if self.s.knowledge.materials[material_id].is_paid and self.s.experiments is not None:
            await self.s.experiments.click(owner, material_id, "miniapp")
        return json_response({"ok": True})

    async def search_materials(self, request: web.Request) -> web.Response:
        query = " ".join(request.query.get("q", "").split())[:200]
        if len(query) < 2:
            return json_response({"items": []})
        found = await asyncio.to_thread(self.s.knowledge.search, query, candidates=8)
        return json_response({"items": [material_json(item) for item, _ in found.materials[:8]]})

    # ------------------------------------------------------------------ profile
    async def get_profile(self, request: web.Request) -> web.Response:
        return json_response(profile_json(await self.s.profiles.load(self.owner(request))))

    async def put_profile(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        fields = body.get("fields")
        if not isinstance(fields, dict):
            raise bad("Передай поля профиля в «fields».")
        changes: dict[str, Any] = {}
        for name, value in fields.items():
            if name not in PROFILE_FIELDS:
                raise bad(f"Неизвестное поле профиля «{name}».", "unknown_field")
            if value is None or value == "":
                changes[name] = None
            elif name in PROFILE_ENUMS:
                if value not in PROFILE_ENUMS[name]:
                    raise bad(f"Недопустимое значение поля «{name}».", "bad_value")
                changes[name] = value
            elif name == "employees":
                if type(value) is not bool:
                    raise bad("Поле «employees»: true или false.", "bad_value")
                changes[name] = value
            else:  # goal
                if not isinstance(value, str):
                    raise bad("Поле «goal» должно быть строкой.", "bad_value")
                changes[name] = mask_personal(" ".join(value.split()), limit=60) or None
        current = await self.s.profiles.load(owner)
        if (
            changes.get("legal_form") == "самозанятый"
            and "tax_regime" not in changes
            and current.tax_regime is None
        ):
            changes["tax_regime"] = "НПД"
        return json_response(profile_json(await self.save_profile(owner, changes)))

    async def describe_profile(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        text = text_field(body, "text")
        profile = await self.s.profiles.load(owner)
        updates = await self.s.assistant.extract_profile(text, profile)
        profile = await self.save_profile(owner, updates) if updates else profile
        return json_response({"updates": updates, "profile": profile_json(profile)})

    async def clear_profile(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        await self.s.profiles.clear(owner)
        await self.log(owner, "profile_cleared")
        return json_response(profile_json(BusinessProfile()))

    async def onboarding_done(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        await self.s.profiles.mark_onboarded(owner)
        profile = await self.s.profiles.load(owner)
        await self.log(owner, "onboarding_done", filled=len(profile.filled()))
        return json_response({"ok": True})

    # Compatibility for already-open clients; old progress is never mutated.
    async def route(self, request: web.Request) -> web.Response:
        raise ApiError(410, "route_obsolete", "Открой маршрут на 7 дней.")

    async def route_done(self, request: web.Request) -> web.Response:
        raise ApiError(410, "route_obsolete", "Отмечай дни в маршруте на 7 дней.")

    # ------------------------------------------------------------------ journey (7 days)
    async def journey_preview(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        at = parse_clock_value(body.get("time"), "time")
        profile = await self.s.profiles.load(owner)
        settings = await self.s.engagement.get_settings(owner)
        if not settings.timezone:
            raise ApiError(409, "need_timezone", "Сначала укажи свой город.")
        goal = text_field(body, "goal", limit=200, required=False) or (profile.goal or "")
        if not goal.strip():
            raise ApiError(409, "need_goal", "Сначала опиши свою цель.")
        goal = mask_personal(goal, limit=120)
        knowledge = self.s.knowledge
        found = await asyncio.to_thread(knowledge.search, goal, profile=profile, candidates=14)
        route = [
            item
            for item, _ in found.materials
            if not item.is_paid and item.kind not in ("glossary", "podcast")
        ][:6]
        steps = await self.s.assistant.journey_steps(goal, profile, route)
        if len(steps) < JOURNEY_DAYS:
            raise ApiError(
                409,
                "not_enough_materials",
                "Не нашёл достаточно бесплатных материалов под эту цель. Сформулируй цель точнее.",
            )
        return json_response(
            {
                "goal": goal,
                "time": at.strftime("%H:%M"),
                "timezone": settings.timezone,
                "steps": [
                    {
                        "day": index,
                        "task": step["task"],
                        "title": step["title"],
                        "url": step["url"],
                        "material_id": step["material_id"],
                    }
                    for index, step in enumerate(steps[:JOURNEY_DAYS], 1)
                ],
            }
        )

    async def journey_start(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        goal = mask_personal(text_field(body, "goal", limit=300), limit=300)
        at = parse_clock_value(body.get("time"), "time")
        raw_steps = body.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) != JOURNEY_DAYS:
            raise bad("В маршруте должно быть ровно семь шагов.")
        steps = []
        for raw in raw_steps:
            material = self.s.knowledge.materials.get(
                str(raw.get("material_id")) if isinstance(raw, dict) else ""
            )
            task = raw.get("task") if isinstance(raw, dict) else None
            if (
                material is None
                or material.is_paid
                or not isinstance(task, str)
                or not task.strip()
            ):
                raise bad("Шаг маршрута указывает на неизвестный или платный материал.")
            # Title and link always come from the catalog, never from the client.
            steps.append(
                {
                    "title": material.title,
                    "url": material.url,
                    "material_id": material.id,
                    "task": " ".join(task.split())[:90],
                }
            )
        settings = await self.s.engagement.get_settings(owner)
        if not settings.timezone:
            raise ApiError(409, "need_timezone", "Сначала укажи свой город.")
        operation = body.get("operation_id")
        try:
            operation_id = UUID(operation) if isinstance(operation, str) else uuid4()
        except ValueError:
            raise bad("operation_id должен быть UUID.") from None
        journey = await self.s.engagement.create_journey(
            owner, operation_id, goal, settings.timezone, at, steps
        )
        await self.log(owner, "journey_started")
        return json_response(journey_json(journey, self.s.clock()))

    async def journey_get(self, request: web.Request) -> web.Response:
        journey = await self.s.engagement.get_journey(self.owner(request))
        return json_response(journey_json(journey, self.s.clock()) if journey else None)

    async def journey_done(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        day = int_field(body, "day")
        journey = await self.s.engagement.get_journey(owner)
        if journey is None:
            raise ApiError(404, "not_found", "Активного маршрута нет.")
        updated = await self.s.engagement.mark_done(owner, journey.id, day)
        if updated is None:
            raise ApiError(404, "not_found", "Активного маршрута нет.")
        step = next((step for step in journey.steps if step.day == day), None)
        if step is not None and not step.done:
            material = self.s.knowledge.materials.get(step.material_id)
            await self.log(owner, "journey_step_done", journey_id=str(journey.id), day=day)
            await self.log(
                owner,
                "learning_marked_done",
                material_id=step.material_id,
                material_kind=material.kind if material else "unknown",
                journey_id=str(journey.id),
                day=day,
            )
        return json_response(journey_json(updated, self.s.clock()))

    async def journey_stop(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        journey = await self.s.engagement.get_journey(owner)
        if journey is not None:
            await self.s.engagement.stop_journey(owner, journey.id)
            await self.log(owner, "journey_stopped", done=journey.done_days)
        return json_response({"ok": True})

    # ------------------------------------------------------------------ taxes
    def _today(self):
        return self.s.clock().astimezone(ZoneInfo(MOSCOW)).date()

    async def taxes(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        profile = await self.s.profiles.load(owner)
        missing = self.taxes_missing(profile)
        if missing:
            return json_response({"ready": False, "missing": missing})
        deadlines = tax_deadlines(
            profile.legal_form,
            profile.tax_regime,
            profile.employees,
            today=self._today(),
            horizon_days=120,
        )
        await self.log(owner, "tax_calendar_shown", regime=profile.tax_regime)
        items = [
            {
                "date": item.due.isoformat(),
                "title": item.title,
                "kind": item.kind,
                "note": item.what,
                "shifted": item.shifted,
            }
            for item in deadlines[:6]
        ]
        related = await asyncio.to_thread(
            self.s.knowledge.search,
            f"налоги {profile.tax_regime} {profile.legal_form} сроки уплаты",
            candidates=6,
        )
        guides = [
            item
            for item, _ in related.materials
            if item.kind in ("article", "course", "calculator")
        ][:2]
        return json_response(
            {
                "ready": True,
                "profile_labels": profile.filled(),
                "explainer": regime_explainer(profile.tax_regime),
                "items": items,
                "next": items[0] if items else None,
                "guides": [material_json(item) for item in guides],
                "disclaimer": "Сроки справочные (НК РФ, 2026) с переносом выходных. "
                "Перед оплатой сверяйся с nalog.ru.",
            }
        )

    async def taxes_remind(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        profile = await self.s.profiles.load(owner)
        if self.taxes_missing(profile):
            raise ApiError(
                409, "profile_incomplete", "Сначала укажи форму бизнеса и налоговый режим."
            )
        deadlines = tax_deadlines(
            profile.legal_form,
            profile.tax_regime,
            profile.employees,
            today=self._today(),
            horizon_days=200,
        )
        settings = await self.s.engagement.get_settings(owner)
        timezone = settings.timezone or MOSCOW
        zone = ZoneInfo(timezone)
        now = self.s.clock()
        created = 0
        existing = {record.event_text for record in await self.s.reminders.list(owner)}
        for item in deadlines:
            if created >= 6:
                break
            local_at = datetime.combine(item.due - timedelta(days=3), time(10, 0))
            if local_at.replace(tzinfo=zone) <= now:
                local_at = datetime.combine(item.due, time(9, 0))
                if local_at.replace(tzinfo=zone) <= now:
                    continue
            event = f"🧾 {item.title} — срок {item.due:%d.%m.%Y}. {item.what}"
            if event in existing:
                continue
            schedule = Schedule(event, local_at, timezone, ReminderFrequency.ONCE)
            try:
                await self.s.reminders.create(
                    owner, uuid5(_OPERATION_NAMESPACE, f"{owner}:{item.code}"), schedule
                )
            except ReminderError as error:
                if "активных" in str(error):
                    break
                continue
            created += 1
        await self.log(owner, "tax_reminders_created", count=created)
        return json_response({"created": created})

    # ------------------------------------------------------------------ reminders
    async def reminders_list(self, request: web.Request) -> web.Response:
        records = await self.s.reminders.list(self.owner(request))
        return json_response({"reminders": [reminder_json(item) for item in records]})

    async def reminders_parse(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        text = text_field(body, "text", limit=1000)
        when_text = text_field(body, "when", limit=160, required=False)
        timezone = body.get("timezone")
        timezone = check_timezone(timezone) if timezone else None
        if timezone is None:
            timezone = (await self.s.engagement.get_settings(owner)).timezone
        now = self.s.clock()
        normalized = normalize_time(text)
        parsed = await parse_reminder_input(
            normalized, now=now, timezone=timezone, model=self.s.model
        )
        if parsed is None:
            parsed = await parse_reminder_input(
                f"напомни {normalized}", now=now, timezone=timezone, model=self.s.model
            )
        event = (parsed.event if parsed else text).strip()[:1000]
        zone = (parsed.timezone if parsed else None) or timezone
        day = parsed.day if parsed else None
        pending = parsed.pending_day if parsed else None
        local_time = parsed.local_time if parsed else None
        frequency = parsed.frequency if parsed else ReminderFrequency.ONCE
        if when_text:
            # The follow-up phrase holds only the time: it is the only part sent to the model.
            when = await parse_when(
                normalize_time(when_text), now=now, timezone=zone, model=self.s.model
            )
            if when is not None:
                zone = when.timezone or zone
                day = when.day or day
                pending = None if when.day else (when.pending_day or pending)
                local_time = when.local_time or local_time
                if when.frequency != ReminderFrequency.ONCE:
                    frequency = when.frequency
        if zone and not day:
            if local_time and not pending:
                pending = "daily"
            if pending:
                day = resolve_reminder_day(pending, now=now, timezone=zone, local_time=local_time)
        local_at = datetime.combine(day, local_time) if day and local_time else None
        if local_at is not None and zone and local_at.replace(tzinfo=ZoneInfo(zone)) <= now:
            local_at = None
        needs = []
        if not event:
            needs.append("event")
        if not zone:
            needs.append("timezone")
        # Without a city the date cannot be resolved yet; ask for it again after the city.
        if local_at is None and (zone or local_time is None):
            needs.append("time")
        return json_response(
            {
                "event": event,
                "local_at": local_at.isoformat(timespec="minutes") if local_at else None,
                "timezone": zone,
                "frequency": frequency.value,
                "link": None,
                "needs": needs,
            }
        )

    async def reminders_timezone(self, request: web.Request) -> web.Response:
        body = await read_body(request)
        city = text_field(body, "city", limit=80)
        zone = await resolve_timezone(city, self.s.model)
        if not zone:
            raise bad(
                "Не узнал город 🤔 Напиши ближайший крупный город, например «Самара» или «Омск».",
                "unknown_city",
            )
        return json_response({"timezone": zone})

    @staticmethod
    def _fold(body: dict) -> int | None:
        fold = body.get("fold")
        if fold is None:
            return None
        if fold not in (0, 1) or type(fold) is not int:
            raise bad("Смещение: 0 или 1.")
        return fold

    async def reminders_create(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        event = text_field(body, "event", limit=1000)
        local_at = parse_local(body.get("local_at"))
        timezone = check_timezone(body.get("timezone"))
        try:
            frequency = ReminderFrequency(body.get("frequency", "once"))
        except ValueError:
            raise bad("Выбери повторение: разово, каждый день, неделю, месяц или год.") from None
        link_url = link_title = None
        link = body.get("link")
        if link:
            url = link.get("url") if isinstance(link, dict) else None
            material = self.link_for(url) if isinstance(url, str) else None
            if material is None:
                raise bad("Ссылка должна вести на материал каталога.", "bad_link")
            link_url, link_title = url, material.title
        operation = body.get("operation_id")
        try:
            operation_id = UUID(operation) if isinstance(operation, str) else uuid4()
        except ValueError:
            raise bad("operation_id должен быть UUID.") from None
        schedule = Schedule(event, local_at, timezone, frequency, self._fold(body))
        record = await self.s.reminders.create(
            owner, operation_id, schedule, link_url=link_url, link_title=link_title
        )
        await self.log(
            owner, "reminder_created", frequency=frequency.value, learning=bool(link_url)
        )
        return json_response(reminder_json(record))

    async def _owned_reminder(self, owner: int, raw_id: str):
        record = await self.s.reminders.get(owner, reminder_id(raw_id))
        if (
            record is None
            or record.status != ReminderStatus.ACTIVE
            or getattr(record, "kind", "normal") == "journey"
        ):
            raise ApiError(404, "not_found", "Напоминание не найдено.")
        return record

    async def reminders_patch(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        record = await self._owned_reminder(owner, request.match_info["id"])
        body = await read_body(request)
        version = int_field(body, "version")
        try:
            frequency = ReminderFrequency(body.get("frequency") or record.frequency)
        except ValueError:
            raise bad("Выбери повторение: разово, каждый день, неделю, месяц или год.") from None
        timezone = check_timezone(body["timezone"]) if body.get("timezone") else record.timezone
        event = text_field(body, "event", limit=1000) if body.get("event") else record.event_text
        if record.next_due_utc is None:
            raise ApiError(409, "stale", "Напоминание уже изменилось. Открой список заново.")
        if "local_at" in body:
            local_at, fold, anchor = parse_local(body["local_at"]), self._fold(body), None
        else:
            # Keep the wall-clock time of the next delivery, as the bot does for «Частота».
            local = record.next_due_utc.astimezone(ZoneInfo(record.timezone))
            local_at = local.replace(tzinfo=None)
            ambiguous = len(local_options(local_at, ZoneInfo(timezone))) > 1
            fold = local.fold if ambiguous else None
            anchor = record.month_day
        schedule = Schedule(event, local_at, timezone, frequency, fold, anchor_day=anchor)
        updated = await self.s.reminders.update(owner, record.id, version, schedule)
        await self.log(owner, "reminder_updated", frequency=frequency.value)
        return json_response(reminder_json(updated))

    async def reminders_snooze(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        record = await self._owned_reminder(owner, request.match_info["id"])
        body = await read_body(request)
        version = int_field(body, "version")
        kind = body.get("kind")
        if kind not in ("h1", "d1"):
            raise bad("Отложить можно на час («h1») или до завтра («d1»).")
        if record.version != version:
            raise ApiError(409, "stale", "Напоминание уже изменилось. Открой список заново.")
        copy = await self.s.reminders.snooze(owner, record.id, kind, str(version))
        await self.log(owner, "reminder_snoozed", delay=kind)
        return json_response(reminder_json(copy))

    async def reminders_disable(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        record = await self._owned_reminder(owner, request.match_info["id"])
        raw = request.query.get("version")
        if raw is not None and not (raw.isascii() and raw.isdecimal()):
            raise bad("version должен быть числом.")
        await self.s.reminders.disable(
            owner, record.id, version=int(raw) if raw is not None else None
        )
        await self.log(owner, "reminder_disabled")
        return json_response({"ok": True})

    # ------------------------------------------------------------------ notification settings
    async def settings_get(self, request: web.Request) -> web.Response:
        return json_response(
            settings_json(await self.s.engagement.get_settings(self.owner(request)))
        )

    async def settings_put(self, request: web.Request) -> web.Response:
        owner = self.owner(request)
        body = await read_body(request)
        changes: dict[str, Any] = {}
        for name in ("enabled", "quiet_enabled", "weekly_summary", "weekly_summary_asked"):
            if name in body:
                if type(body[name]) is not bool:
                    raise bad(f"Поле «{name}»: true или false.")
                changes[name] = body[name]
        for name in ("quiet_start", "quiet_end"):
            if name in body:
                changes[name] = parse_clock_value(body[name], name)
        if "weekly_limit" in body:
            if body["weekly_limit"] not in LIMITS or type(body["weekly_limit"]) is not int:
                raise bad("Лимит в неделю: 3, 7 или 14.")
            changes["weekly_limit"] = body["weekly_limit"]
        if "city" in body:
            zone = await resolve_timezone(text_field(body, "city", limit=80), self.s.model)
            if not zone:
                raise bad(
                    "Не узнал город 🤔 Напиши ближайший крупный город, например «Омск».",
                    "unknown_city",
                )
            changes["timezone"] = zone
        if "weekly_summary" in changes:
            changes["weekly_summary_asked"] = True
            if changes["weekly_summary"] and "timezone" not in changes:
                current = await self.s.engagement.get_settings(owner)
                if not current.timezone:
                    changes["timezone"] = MOSCOW
        if changes:
            settings = await self.s.engagement.save_settings(owner, **changes)
            await self.log(owner, "user_active")
        else:
            settings = await self.s.engagement.get_settings(owner)
        return json_response(settings_json(settings))


__all__ = ["Api", "ApiError", "Services", "json_response"]
