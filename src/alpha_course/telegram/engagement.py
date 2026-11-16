"""Seven-day learning journey and notification settings (opt-in, no spam)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, time
from html import escape
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from alpha_course.application.assistant import Assistant
from alpha_course.application.engagement import (
    JOURNEY_DAYS,
    Journey,
    JourneyDayNotReached,
    NotificationSettings,
)
from alpha_course.application.reminders import ReminderError
from alpha_course.application.schedule_ai import parse_when, resolve_timezone
from alpha_course.telegram.menu import ROUTE, SETTINGS, labels, leaves_flow
from alpha_course.telegram.profiles import ProfileStore
from alpha_course.telegram.reminders import timezone_label

_LOG = logging.getLogger(__name__)
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
_HHMM = re.compile(r"(?<!\d)(\d{1,2})[:. ](\d{2})(?!\d)")
_HOUR = re.compile(r"(?:в|к)\s*(\d{1,2})(?!\d)")
LIMITS = (3, 7, 14)
STALE = "Кнопка устарела. Открой раздел заново."


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _rows(buttons: list[InlineKeyboardButton], width: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


def _clock(value: time) -> str:
    return value.strftime("%H:%M")


def parse_clock(text: str) -> time | None:
    """«09:00», «в 9», «18.30» → time; None when no clock time is written."""
    text = text.lower()
    if match := _HHMM.search(text):
        hour, minute = int(match.group(1)), int(match.group(2))
    elif match := _HOUR.search(text) or re.fullmatch(r"\s*(\d{1,2})\s*", text):
        hour, minute = int(match.group(1)), 0
        if "вечер" in text and hour < 12:
            hour += 12
    else:
        return None
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


async def offer_weekly_summary(message: Message, owner: int, engagement) -> None:
    """Offer once; showing the question never opts the user in."""
    if engagement is None:
        return
    settings = await engagement.get_settings(owner)
    if settings.weekly_summary_asked:
        return
    zone = settings.timezone or "Europe/Moscow (по Москве)"
    await message.answer(
        "Хочешь получать итоги недели? По воскресеньям в 19:00 "
        f"({zone}) отмечу твою активность в боте и отметки об обучении. "
        "Похвалю за фактическую работу. Можно отключить в /settings.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Да, получать итоги", "ns:weekly:on")],
                [_button("Нет, спасибо", "ns:weekly:off")],
            ]
        ),
    )
    await engagement.save_settings(owner, weekly_summary_asked=True)


def settings_html(settings: NotificationSettings) -> str:
    quiet = (
        f"с {_clock(settings.quiet_start)} до {_clock(settings.quiet_end)}"
        if settings.quiet_enabled
        else "выключены"
    )
    city = (
        timezone_label(settings.timezone)
        if settings.timezone
        else "не выбран; итоги — в 19:00 по Москве"
    )
    return (
        "⚙️ <b>Настройки</b>\n"
        f"📅 Итоги недели: <b>{'включены' if settings.weekly_summary else 'выключены'}</b>"
        " — воскресенье, 19:00\n"
        f"🔔 Все уведомления: <b>{'включены' if settings.enabled else 'выключены'}</b>\n"
        f"🌙 Тихие часы: <b>{quiet}</b>\n"
        f"📊 Не больше <b>{settings.weekly_limit}</b> сообщений в неделю\n"
        f"📍 Город: <b>{escape(city)}</b>\n\n"
        "<i>Напоминание, попавшее на тихие часы или сверх лимита, придёт позже, "
        "а не пропадёт. Любое можно отложить кнопкой «Отложить». "
        "Итоги недели приходят только по воскресеньям; при ограничениях выпуск пропускается.</i>"
    )


def settings_markup(settings: NotificationSettings) -> InlineKeyboardMarkup:
    limits = [
        _button(
            ("✅ " if settings.weekly_limit == value else "") + f"{value} в неделю", f"ns:l:{value}"
        )
        for value in LIMITS
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    "🔕 Выключить всё" if settings.enabled else "🔔 Включить уведомления",
                    "ns:e",
                )
            ],
            [
                _button(
                    "🌙 Отключить тихие часы"
                    if settings.quiet_enabled
                    else "🌙 Включить тихие часы",
                    "ns:q",
                )
            ],
            [
                _button(
                    "Отключить итоги недели"
                    if settings.weekly_summary
                    else "Включить итоги недели",
                    "ns:weekly:off" if settings.weekly_summary else "ns:weekly:on",
                )
            ],
            limits,
            [_button("📍 Изменить город", "ns:tz")],
            [_button("‹ К напоминаниям", "r:list")],
        ]
    )


def journey_html(journey: Journey, now: datetime) -> str:
    lines = [f"📅 <b>План на {JOURNEY_DAYS} дней</b>", f"🎯 {escape(journey.goal)}\n"]
    for step in journey.steps:
        mark = "✅" if step.done else ("👉" if step.due_utc and step.due_utc <= now else "▫️")
        lines.append(
            f"{mark} <b>День {step.day}.</b> {escape(step.task)}\n"
            f'    <a href="{escape(step.url, quote=True)}">{escape(step.title)}</a>'
        )
    lines.append(
        f"\nПройдено: <b>{journey.done_days} из {JOURNEY_DAYS}</b>. "
        f"Шаг приходит каждый день в {_clock(journey.local_time)}.\n"
        "<i>Отметка — это твоё нажатие, а не проверка знаний.</i>"
    )
    return "\n".join(lines)


def journey_markup(journey: Journey, now: datetime) -> InlineKeyboardMarkup:
    marks = [
        _button(f"✅ День {step.day}", f"jn:d:{step.day}")
        for step in journey.steps
        if not step.done and step.due_utc and step.due_utc <= now
    ]
    rows = _rows(marks, 3)
    rows.append([_button("⚙️ Уведомления", "ns:show"), _button("⏹ Остановить", "jn:stop")])
    rows.append([_button("‹ Все напоминания", "r:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_engagement_router(
    engagement,
    assistant: Assistant,
    profiles: ProfileStore,
    *,
    events=None,
    model=None,
) -> Router:
    router = Router(name="engagement")
    router.message.filter(
        F.chat.type == "private",
        lambda message: message.from_user and message.from_user.id == message.chat.id,
    )
    router.callback_query.filter(
        F.message.chat.type == "private", lambda query: query.from_user.id == query.message.chat.id
    )
    knowledge = assistant.knowledge

    async def log(owner: int, kind: str, **payload) -> None:
        if events is not None:
            try:
                await events.log(owner, kind, **payload)
            except Exception:
                _LOG.warning("event_log_failed")

    async def waiting(message: Message, state: FSMContext) -> bool:
        return bool((await state.get_data()).get("jn_wait")) and bool(message.text)

    not_now = _button("✖️ Не сейчас", "jn:x")

    # ------------------------------------------------------------------ settings
    async def show_settings(message: Message, owner: int, *, edit: bool = False) -> None:
        settings = await engagement.get_settings(owner)
        reply = message.edit_text if edit else message.answer
        await reply(
            settings_html(settings), parse_mode="HTML", reply_markup=settings_markup(settings)
        )

    async def reset_flow(state: FSMContext) -> None:
        await state.update_data(
            ai_help=False,
            last_ai_answer=None,
            draft=None,
            profile_field=None,
            jn_wait=None,
            jn_draft=None,
            jn_goal=None,
        )

    @router.message(F.text.in_(labels(SETTINGS)))
    @router.message(Command("settings"))
    async def settings_command(message: Message, state: FSMContext) -> None:
        await reset_flow(state)
        await show_settings(message, message.from_user.id)

    # ------------------------------------------------------------------ journey
    async def ask_next(message: Message, state: FSMContext, owner: int) -> None:
        """Collect what the journey needs, one question at a time: goal → city → time."""
        profile = await profiles.load(owner)
        settings = await engagement.get_settings(owner)
        data = await state.get_data()
        goal = (data.get("jn_goal") or profile.goal or "").strip()
        if not goal:
            await state.update_data(jn_wait="goal")
            await message.answer(
                "📅 <b>План на 7 дней</b> — по одному короткому шагу в день.\n\n"
                "🎯 <b>Какая у тебя цель?</b>\nНапиши своими словами, например: «найти первых "
                "клиентов для кофейни» или «разобраться с налогами ИП».",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[not_now]]),
            )
            return
        await state.update_data(jn_goal=goal)
        if not settings.timezone:
            await state.update_data(jn_wait="tz")
            await message.answer(
                "📍 <b>В каком ты городе?</b>\nНапиши город — так шаги придут в удобное время.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[not_now]]),
            )
            return
        await state.update_data(jn_wait="time")
        await message.answer(
            "🕒 <b>Во сколько присылать шаг дня?</b>\n"
            "Выбери или напиши время, например «09:00» или «в 8 вечера».",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _button(value, f"jn:t:{value.replace(':', '')}")
                        for value in ("09:00", "13:00", "19:00")
                    ],
                    [not_now],
                ]
            ),
        )

    async def build_preview(message: Message, state: FSMContext, owner: int, at: time) -> None:
        profile = await profiles.load(owner)
        settings = await engagement.get_settings(owner)
        goal = (await state.get_data()).get("jn_goal") or profile.goal or "основы бизнеса"
        wait = await message.answer("⏳ Подбираю шаги под твою цель…")
        found = await asyncio.to_thread(knowledge.search, goal, profile=profile, candidates=14)
        route = [
            item
            for item, _ in found.materials
            if not item.is_paid and item.kind not in ("glossary", "podcast")
        ][:6]
        steps = await assistant.journey_steps(goal, profile, route)
        if len(steps) < JOURNEY_DAYS:
            await wait.edit_text(
                "Не нашёл достаточно бесплатных материалов под эту цель. "
                "Сформулируй цель точнее — подберу маршрут."
            )
            await state.update_data(jn_wait="goal")
            return
        await state.update_data(
            jn_wait=None,
            jn_draft={"time": _clock(at), "steps": steps, "goal": goal},
        )
        lines = "\n".join(
            f"<b>День {index}.</b> {escape(step['task'])}\n"
            f'    <a href="{escape(step["url"], quote=True)}">{escape(step["title"])}</a>'
            for index, step in enumerate(steps, 1)
        )
        quiet = (
            f"тихие часы {_clock(settings.quiet_start)}–{_clock(settings.quiet_end)}"
            if settings.quiet_enabled
            else "тихие часы выключены"
        )
        await wait.edit_text(
            f"📅 <b>План на 7 дней</b>\n🎯 {escape(goal)}\n\n{lines}\n\n"
            f"🔔 Присылаю по шагу в день в <b>{_clock(at)}</b> "
            f"({escape(timezone_label(settings.timezone or ''))}), начиная с завтра. "
            f"Уведомления: {quiet}, не больше {settings.weekly_limit} в неделю. "
            "Остановить можно в любой момент в «🧭 Мой маршрут».",
            parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("✅ Запустить на 7 дней", "jn:go")],
                    [_button("🕒 Изменить время", "jn:time"), _button("✖️ Не надо", "jn:x")],
                ]
            ),
        )

    async def show_journey(message: Message, owner: int, *, edit: bool = False) -> None:
        journey = await engagement.get_journey(owner)
        reply = message.edit_text if edit else message.answer
        if journey is None:
            await reply(
                "📅 Плана на 7 дней пока нет. Соберу его под твою цель: "
                "по одному короткому шагу в день.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [_button("📅 Составить план", "jn:new")],
                    ]
                ),
            )
            return
        now = datetime.now(UTC)
        await reply(
            journey_html(journey, now),
            parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
            reply_markup=journey_markup(journey, now),
        )

    @router.message(F.text.in_(labels(ROUTE)))
    @router.message(Command("route", "plan"))
    async def route_command(message: Message, state: FSMContext) -> None:
        await reset_flow(state)
        owner = message.from_user.id
        if await engagement.get_journey(owner) is None:
            await ask_next(message, state, owner)
        else:
            await show_journey(message, owner)

    @router.message(F.text, waiting)
    async def wait_text(message: Message, state: FSMContext) -> None:
        owner = message.from_user.id
        if leaves_flow(message.text):
            # Menu buttons and commands close the open question instead of becoming its answer.
            await state.update_data(jn_wait=None)
            raise SkipHandler
        text = message.text.strip()[:200]
        step = (await state.get_data()).get("jn_wait")
        if step == "goal":
            if len(text) < 4 or text.endswith("?") and len(text) < 8:
                await message.answer("Опиши цель чуть подробнее, например «найти клиентов».")
                return
            from alpha_course.application.privacy import mask_personal

            goal = mask_personal(text, limit=120)
            profile = await profiles.load(owner)
            await profiles.save(owner, profile.merged(goal=goal))
            await state.update_data(jn_goal=goal)
            await ask_next(message, state, owner)
        elif step in ("tz", "tz_only"):
            zone = await resolve_timezone(text, model)
            if not zone:
                await message.answer(
                    "Не узнал город 🤔 Напиши ближайший крупный город, например «Омск»."
                )
                return
            await engagement.save_settings(owner, timezone=zone)
            if step == "tz_only":
                await state.update_data(jn_wait=None)
                await show_settings(message, owner)
                return
            await ask_next(message, state, owner)
        elif step == "time":
            at = parse_clock(text)
            if at is None:
                settings = await engagement.get_settings(owner)
                when = await parse_when(
                    text, now=datetime.now(UTC), timezone=settings.timezone, model=model
                )
                at = when.local_time if when else None
            if at is None:
                await message.answer(
                    "Не понял время 🤔 Напиши, например, «09:00» или «в 8 вечера»."
                )
                return
            await build_preview(message, state, owner, at)

    # ------------------------------------------------------------------ callbacks
    @router.callback_query(F.data.startswith(("jn:", "ns:", "pl:")))
    async def callback(query: CallbackQuery, state: FSMContext) -> None:
        message = query.message
        owner = query.from_user.id
        prefix, _, rest = (query.data or "").partition(":")
        await query.answer()
        if prefix == "pl":
            await reset_flow(state)
            if await engagement.get_journey(owner) is None:
                await ask_next(message, state, owner)
            else:
                await show_journey(message, owner, edit=True)
            return
        if prefix == "ns":
            await handle_settings(message, state, owner, rest)
            return
        if rest in ("new", "goal"):
            # «goal» keeps a goal prepared by another screen (the idea check in «Тренажёры»).
            changes = {"jn_draft": None, "draft": None, "ai_help": False}
            if rest == "new":
                changes["jn_goal"] = None
            await state.update_data(**changes)
            await ask_next(message, state, owner)
        elif rest == "time":
            await state.update_data(jn_wait="time")
            await message.answer(
                "🕒 Напиши новое время, например «10:30».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[not_now]]),
            )
        elif rest.startswith("t:") and rest[2:].isdigit() and len(rest) == 6:
            await build_preview(message, state, owner, time(int(rest[2:4]), int(rest[4:6])))
        elif rest == "x":
            await state.update_data(jn_wait=None, jn_draft=None)
            await message.edit_text(
                "Хорошо, без рассылки. План на 7 дней можно собрать в любой момент.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[_button("🧭 Мой маршрут", "jn:show")]]
                ),
            )
        elif rest == "go":
            await start_journey(message, state, owner)
        elif rest == "show":
            await show_journey(message, owner, edit=True)
        elif rest.startswith("d:") and rest[2:].isdigit():
            journey = await engagement.get_journey(owner)
            if journey is None:
                await message.answer(STALE)
                return
            try:
                await engagement.mark_done(owner, journey.id, int(rest[2:]))
            except JourneyDayNotReached:
                await message.answer("Этот день ещё не наступил — отметишь, когда придёт шаг.")
                return
            except ReminderError:
                await message.answer(STALE)
                return
            step = next((step for step in journey.steps if step.day == int(rest[2:])), None)
            if step is not None and not step.done:
                material = knowledge.materials.get(step.material_id)
                await log(owner, "journey_step_done", journey_id=str(journey.id), day=step.day)
                await log(
                    owner,
                    "learning_marked_done",
                    material_id=step.material_id,
                    material_kind=material.kind if material else "unknown",
                )
            await show_journey(message, owner, edit=True)
        elif rest == "stop":
            journey = await engagement.get_journey(owner)
            if journey is not None:
                await engagement.stop_journey(owner, journey.id)
                await log(owner, "journey_stopped", done=journey.done_days)
            await message.edit_text(
                "⏹ План на 7 дней остановлен — новых шагов не будет.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [_button("🔔 Все напоминания", "r:list")],
                    ]
                ),
            )
        else:
            await message.answer(STALE)

    async def start_journey(message: Message, state: FSMContext, owner: int) -> None:
        draft = (await state.get_data()).get("jn_draft")
        settings = await engagement.get_settings(owner)
        if not isinstance(draft, dict) or not settings.timezone:
            await message.answer(STALE)
            return
        hour, minute = map(int, draft["time"].split(":"))
        try:
            ZoneInfo(settings.timezone)
            journey = await engagement.create_journey(
                owner,
                uuid4(),
                draft["goal"],
                settings.timezone,
                time(hour, minute),
                draft["steps"],
            )
        except ReminderError as error:
            await message.answer(str(error))
            return
        await state.update_data(jn_draft=None)
        await log(owner, "journey_started")
        await message.edit_text(
            f"🚀 <b>План запущен!</b> Первый шаг придёт завтра в {draft['time']}.\n"
            "Шаги и отметки — в плане, остановить его можно в «🧭 Мой маршрут».",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("📅 Открыть план", "jn:show")],
                    [_button("🔔 Все напоминания", "r:list")],
                ]
            ),
        )
        del journey

    async def handle_settings(message: Message, state: FSMContext, owner: int, rest: str) -> None:
        settings = await engagement.get_settings(owner)
        if rest in ("weekly:on", "weekly:off"):
            changes = {"weekly_summary": rest == "weekly:on", "weekly_summary_asked": True}
            if rest == "weekly:on" and not settings.timezone:
                changes["timezone"] = "Europe/Moscow"
            await engagement.save_settings(owner, **changes)
        elif rest == "e":
            await engagement.save_settings(owner, enabled=not settings.enabled)
        elif rest == "q":
            await engagement.save_settings(owner, quiet_enabled=not settings.quiet_enabled)
        elif rest.startswith("l:") and rest[2:].isdigit() and int(rest[2:]) in LIMITS:
            await engagement.save_settings(owner, weekly_limit=int(rest[2:]))
        elif rest == "tz":
            await state.update_data(jn_wait="tz_only")
            await message.answer("📍 Напиши город — например «Казань» или «Владивосток».")
            return
        elif rest != "show":
            await message.answer(STALE)
            return
        await show_settings(message, owner, edit=True)

    return router


__all__ = ["create_engagement_router", "parse_clock", "settings_html"]
