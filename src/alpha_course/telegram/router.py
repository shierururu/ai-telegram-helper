"""Private Telegram dialogue: consultation, onboarding, profile, learning plan, taxes, reminders."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from html import escape
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)

from alpha_course.application.assistant import Assistant, Recommendation, Reply
from alpha_course.application.copilot import (
    capabilities_text,
    is_capabilities_question,
    personalization_note,
)
from alpha_course.application.experiments import CONTROL, offer_pitch, report_lines
from alpha_course.application.knowledge import Material
from alpha_course.application.offers import CourseOffer
from alpha_course.application.pdf_reports import is_pdf_export, is_pdf_request
from alpha_course.application.profile import (
    INDUSTRIES,
    LEGAL_FORMS,
    STAGES,
    TAX_REGIMES,
    BusinessProfile,
)
from alpha_course.application.reminder_input import ReminderInputModel, parse_reminder_input
from alpha_course.application.reminders import ReminderError, Schedule
from alpha_course.application.schedule_ai import parse_when, resolve_timezone
from alpha_course.application.tax_calendar import (
    missing_for_calendar,
    regime_explainer,
    tax_deadlines,
)
from alpha_course.domain.reminders import ReminderFrequency
from alpha_course.infrastructure.pdf_reports import render_report
from alpha_course.infrastructure.reminders import ReminderStore
from alpha_course.telegram.menu import (
    ACTIVE_MENU,
    AI_HELP,
    ASK,
    CANCEL,
    FINMODEL,
    GAMES,
    LEGACY_BUTTONS,
    MENU,
    PROFILE,
    REMINDERS,
    TAXES,
    TOOLS,
    labels,
)
from alpha_course.telegram.profiles import ProfileStore
from alpha_course.telegram.reminders import (
    FREQUENCIES,
    Draft,
    apply_picker,
    format_due,
    get_draft,
    management_markup,
    reminder_timing,
    show_draft,
    timezone_label,
)
from alpha_course.telegram.render import (
    answer_html,
    material_line,
    offer_html,
    profile_html,
    profile_note,
)

WELCOME = (
    "👋 <b>Привет! Я «Бизнес-навигатор»</b> — помощник предпринимателя.\n"
    "Отвечаю на вопросы о бизнесе по материалам платформы и подсказываю, что изучить дальше.\n\n"
    "💬 <b>Спрашивай</b> своими словами — можно коротко\n"
    "🧭 <b>Мой маршрут</b> — один шаг в день на 7 дней под твой бизнес\n"
    "🧰 <b>Инструменты</b> — финмодель, налоговый календарь и тренажёры\n"
    "⚙️ <b>Настройки</b> — город и мотивирующие итоги недели\n"
    "🔔 <b>Напоминания</b> — об учёбе и важных датах, без спама\n"
)
ASK_HINT = (
    "💬 Напиши вопрос своими словами. Например:\n"
    "• <i>Как выбрать налоговый режим для ИП?</i>\n"
    "• <i>Где найти первых клиентов для кофейни?</i>\n"
    "• <i>Что такое эквайринг?</i>"
)
BUSY_DRAFT = "Сначала закончи текущее напоминание или нажми «✖️ Отмена»."
GOALS = (
    "найти клиентов",
    "разобраться с налогами",
    "запустить дело",
    "снизить расходы",
    "увеличить продажи",
    "собрать команду",
)
STALE = "Кнопка устарела. Открой раздел заново из меню."
LEVEL_CHOICES = (("novice", "🌱 Новичок"), ("experienced", "🚀 Есть опыт"))
PROFILE_CHOICES = {
    "stage": ("Этап бизнеса", STAGES),
    "industry": ("Сфера", INDUSTRIES),
    "legal_form": ("Форма бизнеса", LEGAL_FORMS),
    "tax_regime": ("Налоговый режим", TAX_REGIMES),
    "employees": ("Есть сотрудники?", ("да", "нет")),
    "level": ("Опыт в бизнесе", tuple(label for _, label in LEVEL_CHOICES)),
}
_LOG = logging.getLogger(__name__)
_OPERATION_NAMESPACE = UUID("2ee46bd2-67ec-4ac6-814a-b090ce04dc44")
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


_LOOSE_TIME = re.compile(r"(?<=\bв )(\d{1,2})[ .](\d{2})\b(?![.:]\d)")


def _normalize_time(text: str) -> str:
    """«завтра в 15 00» and «в 9.30» become «в 15:00» and «в 9:30»."""
    return _LOOSE_TIME.sub(r"\1:\2", text)


def _rows(buttons: list[InlineKeyboardButton], width: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[index : index + width] for index in range(0, len(buttons), width)]


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _choice_value(field: str, index: int) -> object:
    values = PROFILE_CHOICES[field][1]
    if field == "employees":
        return index == 0
    if field == "level":
        return LEVEL_CHOICES[index][0]
    return values[index]


async def _remember(state: FSMContext, role: str, text: str) -> None:
    history = (await state.get_data()).get("history", [])
    history = [*history, {"role": role, "text": text[:600]}][-6:]
    await state.update_data(history=history)


async def _store_keys(state: FSMContext, name: str, values: list[str]) -> list[str]:
    """Keep callback payloads server-side; buttons carry only short random keys."""
    data = (await state.get_data()).get(name, {})
    data = dict(list(data.items())[-30:])
    keys = []
    for value in values:
        key = uuid4().hex[:10]
        data[key] = value
        keys.append(key)
    await state.update_data(**{name: data})
    return keys


async def _lookup(state: FSMContext, name: str, key: str) -> object | None:
    return (await state.get_data()).get(name, {}).get(key)


def _split_text(text: str, size: int = 3800) -> list[str]:
    """Split a long HTML message on paragraph breaks so Telegram's limit is never hit."""
    parts: list[str] = []
    current = ""
    for block in text.split(chr(10) * 2):
        if current and len(current) + len(block) + 2 > size:
            parts.append(current)
            current = ""
        while len(block) > size:
            parts.append(block[:size])
            block = block[size:]
        current = current + chr(10) * 2 + block if current else block
    if current:
        parts.append(current)
    return parts


def create_router(
    assistant: Assistant,
    profiles: ProfileStore,
    reminders: ReminderStore,
    *,
    events=None,
    engagement=None,
    model: ReminderInputModel | None = None,
    admin_ids: frozenset[int] = frozenset(),
    experiments=None,
) -> Router:
    """Private assistant and ordinary reminder management."""
    router = Router(name="private_assistant")
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

    async def leave_flows(state: FSMContext, **extra) -> None:
        """A menu action closes the AI mode and an unfinished 7-day plan question."""
        await state.update_data(
            **dict(ai_help=False, last_ai_answer=None, jn_wait=None, draft=None, profile_field=None)
            | extra
        )

    async def current_menu(state: FSMContext) -> ReplyKeyboardMarkup:
        data = await state.get_data()
        return (
            ACTIVE_MENU
            if await get_draft(state) or data.get("profile_field") or data.get("ai_help")
            else MENU
        )

    # ------------------------------------------------------------------ start & onboarding
    @router.message(Command("start"))
    async def start(message: Message, state: FSMContext) -> None:
        owner = message.from_user.id
        await leave_flows(state, profile_field=None, history=[])
        await log(owner, "start")
        onboarded = await profiles.is_onboarded(owner)
        await message.answer(WELCOME, reply_markup=MENU, parse_mode="HTML")
        if onboarded:
            await message.answer(
                "С возвращением! 👋 Продолжим по маршруту или задашь вопрос?",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [_button("🧭 Мой маршрут", "jn:show")],
                        [_button("🔔 Напоминания", "menu:reminders")],
                    ]
                ),
            )
            from alpha_course.telegram.engagement import offer_weekly_summary

            await offer_weekly_summary(message, owner, engagement)
            return
        await ask_onboarding(message, "stage")

    async def ask_onboarding(message: Message, step: str, *, edit: bool = False) -> None:
        head = "Мини-диагностика: 5 вопросов — и советы будут под твой бизнес.\nМожно пропустить любой.\n\n"
        if step == "stage":
            text = head + "<b>1/5 · На каком ты этапе?</b>"
            buttons = [
                _button(label.capitalize(), f"ob:stage:{i}") for i, label in enumerate(STAGES)
            ]
        elif step == "industry":
            text = "<b>2/5 · В какой сфере бизнес?</b>"
            buttons = [
                _button(label.capitalize(), f"ob:ind:{i}") for i, label in enumerate(INDUSTRIES)
            ]
        elif step == "goal":
            text = "<b>3/5 · Какая главная задача сейчас?</b>"
            buttons = [_button(label.capitalize(), f"ob:goal:{i}") for i, label in enumerate(GOALS)]
        elif step == "tax":
            text = "<b>4/5 · Какой у тебя налоговый режим?</b>"
            buttons = [_button(label, f"ob:tax:{i}") for i, label in enumerate(TAX_REGIMES)]
        else:
            text = "<b>5/5 · Есть ли у тебя сотрудники?</b>"
            buttons = [_button("Да", "ob:emp:1"), _button("Нет", "ob:emp:0")]
        markup = InlineKeyboardMarkup(
            inline_keyboard=[*_rows(buttons, 2), [_button("Пропустить →", f"ob:skip:{step}")]]
        )
        if edit:
            await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=markup, parse_mode="HTML")

    async def finish_onboarding(message: Message, owner: int) -> None:
        await profiles.mark_onboarded(owner)
        profile = await profiles.load(owner)
        await log(owner, "onboarding_done", filled=len(profile.filled()))
        pack = knowledge.starter_pack(profile, limit=3)
        lines = "\n\n".join(material_line(item) for item in pack)
        filled = profile.filled()
        text = "✅ <b>Диагностика готова</b>\n"
        if filled:
            text += "Запомнил: " + escape(", ".join(filled.values())) + "\n\n"
        text += "Вот с чего стоит начать:\n\n" + lines if pack else "Можешь задавать вопросы."
        text += (
            "\n\n🧭 <b>Дальше — маршрут на 7 дней</b>: один шаг в день и отметки прогресса. "
            "Или просто задай вопрос."
        )
        await message.edit_text(
            text,
            parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("🧭 Открыть мой маршрут", "jn:show")],
                    [_button("🧾 Налоговый календарь", "tx:show")],
                ]
            ),
        )

        from alpha_course.telegram.engagement import offer_weekly_summary

        await offer_weekly_summary(message, owner, engagement)

    # ------------------------------------------------------------------ menu buttons
    @router.message(F.text == AI_HELP)
    async def ai_help_menu(message: Message, state: FSMContext) -> None:
        await state.update_data(
            ai_help=True,
            draft=None,
            profile_field=None,
            last_ai_answer=None,
            ai_history=[],
            jn_wait=None,
        )
        await message.answer(
            "🤖 <b>Помощь ИИ</b>\n"
            "Знаю твой профиль и помню разговор. Ищу в интернете (конкуренты, ставки, цены, "
            "новости), пишу тексты и планы, делаю PDF. Просто напиши задачу своими словами.\n\n"
            f"<i>«Что ты умеешь» — список функций. Выйти — «{CANCEL}» или любой раздел меню.</i>",
            parse_mode="HTML",
            reply_markup=ACTIVE_MENU,
        )

    @router.message(F.text.in_(labels(ASK)))
    async def ask_question(message: Message, state: FSMContext) -> None:
        await leave_flows(state, profile_field=None)
        await message.answer(ASK_HINT, reply_markup=MENU, parse_mode="HTML")

    @router.message(Command("cancel"))
    @router.message(F.text.in_(labels(CANCEL)))
    async def cancel(message: Message, state: FSMContext) -> None:
        await leave_flows(state, draft=None, profile_field=None, history=[])
        await message.answer("Ок, отменил. Можешь задать вопрос.", reply_markup=MENU)

    @router.message(Command("profile"))
    @router.message(F.text.in_(labels(PROFILE)))
    async def profile_menu(message: Message, state: FSMContext) -> None:
        await leave_flows(state, profile_field=None)
        await show_profile(message, message.from_user.id)

    @router.message(Command("reminders"))
    @router.message(F.text.in_(labels(REMINDERS)))
    async def reminders_menu(message: Message, state: FSMContext) -> None:
        await leave_flows(state)
        await show_reminders(message, message.from_user.id)

    @router.message(F.text == TOOLS)
    @router.message(Command("tools"))
    async def tools_menu(message: Message, state: FSMContext) -> None:
        await leave_flows(state)
        await state.set_state(None)
        await message.answer(
            "🧰 <b>Инструменты</b>\nВыбери, с чем поработать:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button(FINMODEL, "fm:show")],
                    [_button(TAXES, "tx:show")],
                    [_button(GAMES, "gm:hub")],
                ]
            ),
        )

    @router.message(Command("taxes"))
    @router.message(F.text.in_(labels(TAXES)))
    async def tax_menu(message: Message, state: FSMContext) -> None:
        await leave_flows(state)
        await show_taxes(message, message.from_user.id)

    async def show_progress(message: Message, owner: int) -> None:
        """On-demand «Мои успехи»: the same evidence-based numbers as the Sunday summary."""
        if events is None or not hasattr(events, "weekly_progress"):
            await message.answer("🏆 Успехи пока недоступны: нет журнала активности.")
            return
        timezone = "Europe/Moscow"
        if engagement is not None:
            try:
                timezone = (await engagement.get_settings(owner)).timezone or timezone
            except Exception:
                _LOG.warning("progress_settings_failed")
        try:
            progress = await events.weekly_progress(owner, timezone)
        except Exception:
            _LOG.warning("progress_failed")
            await message.answer("🏆 Не получилось посчитать успехи. Попробуй чуть позже.")
            return
        await log(owner, "progress_viewed")
        text = progress.format_text().replace("Итоги недели", "🏆 Мои успехи за неделю", 1)
        if not text.startswith("🏆"):
            text = "🏆 Мои успехи за неделю\n\n" + text
        text = escape(text)
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("🧭 Мой маршрут", "jn:show"), _button("⚙️ Итоги недели", "ns:show")]
                ]
            ),
        )

    @router.message(Command("progress"))
    async def progress_command(message: Message, state: FSMContext) -> None:
        await leave_flows(state)
        await show_progress(message, message.from_user.id)

    @router.message(Command("help"))
    async def help_command(message: Message, state: FSMContext) -> None:
        await leave_flows(state)
        await message.answer(
            WELCOME + "\n<b>Команды</b>: /route — маршрут на 7 дней, /reminders — напоминания, "
            "/profile — профиль, /progress — мои успехи, /cancel — отменить действие.\n\n"
            + ASK_HINT,
            reply_markup=MENU,
            parse_mode="HTML",
        )

    @router.message(Command("stats"))
    async def stats(message: Message, state: FSMContext) -> None:
        await state.update_data(ai_help=False, last_ai_answer=None)
        if message.from_user.id not in admin_ids or events is None:
            await message.answer("Команда доступна только администраторам.")
            return
        summary = await events.summary(30)
        lines = ["<b>Статистика за 30 дней</b>"]
        for key, value in summary.items():
            lines.append(f"{escape(str(key))}: <b>{escape(str(value))}</b>")
        if experiments is not None:
            try:
                overview = await experiments.overview(30)
                rating = overview["rating"]
                lines += [
                    "",
                    "<b>Активность</b>",
                    f"Пользователей: {overview['users']}, за сутки: {overview['active_1d']},"
                    f" за неделю: {overview['active_7d']}",
                    "Средняя оценка: "
                    + (f"{rating} из 5 ({overview['ratings']} оценок)" if rating else "пока нет"),
                ]
                for name, synthetic in (
                    (experiments.experiment.name, False),
                    (experiments.experiment.name + "_synthetic", True),
                ):
                    analysis = await experiments.report(name)
                    if synthetic and not any(
                        item["users"] for item in analysis["variants"].values()
                    ):
                        continue
                    text = report_lines(analysis, synthetic=synthetic)
                    lines += ["", f"<b>{escape(text[0])}</b>", *map(escape, text[1:])]
                if not experiments.enabled:
                    lines.append("A/B выключен (ALFA_AB_PAID_OFFER=false): все видят вариант A.")
                lines.append("\nТаблица по пользователям: /export")
            except Exception:
                _LOG.warning("ab_report_failed")
                lines.append("\nA/B-отчёт временно недоступен.")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @router.message(Command("export"))
    async def export(message: Message, state: FSMContext) -> None:
        await state.update_data(ai_help=False, last_ai_answer=None)
        if message.from_user.id not in admin_ids or experiments is None:
            await message.answer("Команда доступна только администраторам.")
            return
        data = await experiments.metrics_csv(90)
        await message.answer_document(
            BufferedInputFile(data, filename=f"metrics-{datetime.now(UTC):%Y%m%d}.csv"),
            caption="Метрики пользователей за 90 дней. Пользователи — обезличенные хеши.",
        )

    # ------------------------------------------------------------------ profile
    async def show_profile(
        message: Message, owner: int, *, edit: bool = False, status: str = ""
    ) -> None:
        profile = await profiles.load(owner)
        buttons = [
            _button(f"✏️ {title}", f"pf:edit:{field}")
            for field, (title, _) in PROFILE_CHOICES.items()
        ]
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("✨ Рассказать о бизнесе своими словами", "pf:describe")],
                *_rows(buttons, 2),
                [_button("🗑 Очистить", "pf:clear")],
            ]
        )
        text = profile_html(profile, status)
        if edit:
            await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=markup, parse_mode="HTML")

    async def save_profile(owner: int, updates: dict) -> BusinessProfile:
        before = await profiles.load(owner)
        after = before.merged(**updates)
        if after != before:
            await profiles.save(owner, after)
            await log(owner, "profile_updated", fields=sorted(updates))
        return after

    async def show_route_intro(message: Message, owner: int) -> None:
        await message.answer(
            "🧭 <b>Мой маршрут на 7 дней</b>\nОдин небольшой шаг в день под твою задачу. "
            "Открой маршрут, чтобы продолжить или создать новый.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_button("🧭 Открыть мой маршрут", "jn:show")]]
            ),
        )

    # ------------------------------------------------------------------ taxes
    async def show_taxes(message: Message, owner: int, *, edit: bool = False) -> None:
        profile = await profiles.load(owner)
        missing = missing_for_calendar(profile.legal_form, profile.tax_regime)
        reply = message.edit_text if edit else message.answer
        if "legal_form" in missing:
            buttons = [_button(label, f"tx:lf:{i}") for i, label in enumerate(LEGAL_FORMS)]
            await reply(
                "🧾 <b>Налоговый календарь</b>\nПокажу сроки платежей и отчётов под твой режим.\n\n"
                "<b>Какая у тебя форма бизнеса?</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=_rows(buttons, 2)),
                parse_mode="HTML",
            )
            return
        if "tax_regime" in missing:
            buttons = [_button(label, f"tx:rg:{i}") for i, label in enumerate(TAX_REGIMES)]
            await reply(
                "🧾 <b>Какой у тебя налоговый режим?</b>\nЕсли не знаешь — выбери «не знаю», расскажу, как выбрать.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=_rows(buttons, 2)),
                parse_mode="HTML",
            )
            return
        if profile.legal_form in ("ИП", "ООО") and profile.employees is None:
            await reply(
                "🧾 <b>Есть ли у тебя сотрудники?</b>\nОт этого зависят взносы и отчёты.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[_button("Да", "tx:emp:1"), _button("Нет", "tx:emp:0")]]
                ),
                parse_mode="HTML",
            )
            return
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        deadlines = tax_deadlines(
            profile.legal_form, profile.tax_regime, profile.employees, today=today, horizon_days=120
        )
        await log(owner, "tax_calendar_shown", regime=profile.tax_regime)
        explainer = regime_explainer(profile.tax_regime)
        head = (
            f"🧾 <b>Налоговый календарь</b> · {escape(profile.legal_form or '')}, "
            f"{escape(profile.tax_regime or '')}\n<i>{escape(explainer)}</i>"
        )
        related = knowledge.search(
            f"налоги {profile.tax_regime} {profile.legal_form} сроки уплаты", candidates=6
        )
        guides = [
            item
            for item, _ in related.materials
            if item.kind in ("article", "course", "calculator")
        ][:2]
        guide_text = (
            "\n\n📚 <b>Разобраться подробнее</b>\n"
            + "\n\n".join(material_line(item) for item in guides)
            if guides
            else ""
        )
        if not deadlines:
            await reply(
                head + "\n\nДля этого режима нет фиксированных дат, которые я могу посчитать без "
                "дополнительных данных." + guide_text,
                parse_mode="HTML",
                link_preview_options=_NO_PREVIEW,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[_button("✏️ Изменить режим", "tx:reset")]]
                ),
            )
            return
        shown = deadlines[:8]
        lines = []
        for item in shown:
            icon = {"payment": "💳", "report": "📄", "notice": "📨"}.get(item.kind, "•")
            lines.append(
                f"{icon} <b>{item.due:%d.%m}</b> — {escape(item.title)}\n    <i>{escape(item.what)}</i>"
            )
        text = (
            head
            + "\n\n<b>Ближайшие сроки</b>\n"
            + "\n".join(lines)
            + "\n\n<i>Сроки справочные (НК РФ, 2026) с переносом выходных. Перед оплатой сверяйся с nalog.ru.</i>"
            + guide_text
        )
        await reply(
            text,
            parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("🔔 Напоминать за 3 дня до сроков", "tx:remind")],
                    [_button("✏️ Изменить режим", "tx:reset")],
                ]
            ),
        )

    async def create_tax_reminders(message: Message, state: FSMContext, owner: int) -> None:
        profile = await profiles.load(owner)
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        deadlines = tax_deadlines(
            profile.legal_form, profile.tax_regime, profile.employees, today=today, horizon_days=200
        )
        timezone = (await state.get_data()).get("reminder_timezone") or "Europe/Moscow"
        zone = ZoneInfo(timezone)
        now = datetime.now(UTC)
        created = []
        existing = {record.event_text for record in await reminders.list(owner)}
        for item in deadlines:
            if len(created) >= 6:
                break
            remind_day = item.due - timedelta(days=3)
            local_at = datetime.combine(remind_day, time(10, 0))
            if local_at.replace(tzinfo=zone) <= now:
                local_at = datetime.combine(item.due, time(9, 0))
                if local_at.replace(tzinfo=zone) <= now:
                    continue
            event = f"🧾 {item.title} — срок {item.due:%d.%m.%Y}. {item.what}"
            if event in existing:
                continue
            schedule = Schedule(event, local_at, timezone, ReminderFrequency.ONCE)
            try:
                await reminders.create(
                    owner, uuid5(_OPERATION_NAMESPACE, f"{owner}:{item.code}"), schedule
                )
            except ReminderError:
                continue
            created.append(item)
        await log(owner, "tax_reminders_created", count=len(created))
        if created:
            lines = "\n".join(
                f"• {item.due - timedelta(days=3):%d.%m} — {escape(item.title)}" for item in created
            )
            text = (
                f"✅ Поставил {len(created)} напоминаний на 10:00 за 3 дня до срока "
                f"({escape(timezone_label(timezone))}):\n{lines}\n\n"
                "Изменить или отключить их можно в «🔔 Напоминания»."
            )
        else:
            text = "Напоминания по этим срокам уже стоят. Список — в «🔔 Напоминания»."
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_button("🔔 Открыть напоминания", "menu:reminders")]]
            ),
        )

    # ------------------------------------------------------------------ reminders list
    async def show_reminders(
        message: Message, owner: int, *, edit: bool = False, status: str = ""
    ) -> None:
        records = await reminders.list(owner)
        if records:
            lines = ["🔔 <b>Твои напоминания</b>"]
        else:
            lines = [
                "🔔 <b>Напоминаний пока нет</b>\n\nНапиши, например: <i>«напомни завтра в 10:00 "
                "оплатить аренду»</i> или <i>«напоминай каждый понедельник изучать курс»</i>."
            ]
        if status:
            lines.insert(0, status)
        buttons = []
        for index, record in enumerate(records, 1):
            event = " ".join(record.event_text.split())
            if len(event) > 80:
                event = event[:79] + "…"
            lines.append(f"{index}. {escape(event)}\n    <i>{escape(reminder_timing(record))}</i>")
            buttons.append(_button(f"⚙️ {index}", f"r:l:{record.id.hex}:{record.version}"))
        rows = _rows(buttons, 5)
        if records:
            lines.append("<i>Нажми ⚙️ с номером, чтобы изменить время, частоту или отключить.</i>")
        rows.append(
            [_button("➕ Создать напоминание", "r:new"), _button("🏆 Мои успехи", "mv:show")]
        )
        rows.append([_button("⚙️ Настройки", "ns:show")])
        reply = message.edit_text if edit else message.answer
        await reply(
            "\n\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
        )

    async def begin_reminder(message: Message, state: FSMContext, draft: Draft) -> None:
        data = await state.get_data()
        remembered = data.get("reminder_timezone")
        if not remembered and engagement is not None:
            try:
                remembered = (await engagement.get_settings(message.chat.id)).timezone
            except Exception as error:
                _LOG.warning("settings_lookup_failed error=%s", type(error).__name__)
        draft.suggested_timezone = remembered
        if not draft.timezone and remembered:
            # The city was confirmed before: do not ask again, it stays visible on the card.
            draft.timezone = remembered
            draft.resolve_input()
        await state.update_data(draft=draft, profile_field=None, jn_wait=None)
        if draft.event:
            await message.answer("🔔 Настраиваю напоминание", reply_markup=ACTIVE_MENU)
            await show_draft(message, draft)
        else:
            await message.answer(
                "🔔 <b>О чём напомнить?</b>\nМожно сразу целиком: "
                "<i>«завтра в 15:30 отправить счёт»</i>.",
                reply_markup=ACTIVE_MENU,
                parse_mode="HTML",
            )

    async def manage(
        message: Message, state: FSMContext, owner: int, action: str, key: str, version: str
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", key) or not re.fullmatch(r"[1-9][0-9]{0,9}", version):
            await message.answer(STALE)
            return
        target, expected = UUID(hex=key), int(version)
        if action == "d":
            await reminders.disable(owner, target, version=expected)
            await log(owner, "reminder_disabled")
            await show_reminders(message, owner, edit=True, status="🔕 Напоминание отключено.")
            return
        records = await reminders.list(owner)
        record = next(
            (item for item in records if item.id == target and item.version == expected), None
        )
        if record is None:
            await message.answer(STALE)
            return
        if action == "l":
            await message.edit_text(
                f"🔔 {escape(record.event_text)}\n\n<i>{escape(reminder_timing(record))}</i>",
                reply_markup=management_markup(record),
                parse_mode="HTML",
                link_preview_options=_NO_PREVIEW,
            )
            return
        if await get_draft(state):
            await message.answer(BUSY_DRAFT)
            return
        local_at = (
            record.next_due_utc.astimezone(ZoneInfo(record.timezone))
            if record.next_due_utc
            else None
        )
        draft = Draft(
            event=record.event_text,
            local_at=local_at.replace(tzinfo=None) if local_at else None,
            timezone=record.timezone,
            frequency=record.frequency,
            target=target,
            version=expected,
            fold=local_at.fold if local_at else None,
        )
        draft.link_url = getattr(record, "link_url", None)
        draft.link_title = getattr(record, "link_title", None)
        await begin_reminder(message, state, draft)

    async def advance(
        message: Message, state: FSMContext, owner: int, draft: Draft, action: str, args: list[str]
    ) -> None:
        if len(args) > 1:
            await message.answer(STALE)
            return
        value = args[0] if len(args) == 1 else None
        if action == "x" and not args:
            await state.update_data(draft=None)
            await message.edit_text(
                "✖️ Изменения не сохранены." if draft.target else "✖️ Напоминание не создано.",
                reply_markup=None,
            )
            await message.answer("Ок, отменил. Можешь задать вопрос.", reply_markup=MENU)
            return
        if action == "i" and not args:
            return
        if apply_picker(draft, action, value):
            if action == "z":
                await state.update_data(reminder_timezone=draft.timezone)
        elif action == "f" and value in FREQUENCIES and draft.local_at and draft.choose_frequency:
            draft.frequency = ReminderFrequency(value)
            draft.choose_frequency = False
        elif action == "v" and not args and draft.local_at:
            draft.choose_frequency = True
        elif action == "o" and value in {"0", "1"} and draft.local_at and draft.fold is None:
            draft.fold = int(value)
        elif action == "t" and not args and draft.timezone:
            draft.reset_time()
        elif action == "g" and not args and draft.timezone:
            # «Другой город»: ask the city again; the day and time are chosen after it.
            draft.timezone = ""
            draft.reset_time()
            await state.update_data(reminder_timezone=None)
        elif action == "w" and not args and draft.local_at:
            draft.awaiting_time = True
        elif action == "b" and not args and draft.selected_hour is not None and not draft.local_at:
            draft.selected_hour, draft.minute_page = None, 0
        elif action == "c" and not args and draft.local_at and draft.timezone:
            schedule = draft.schedule
            if schedule.first_due() <= datetime.now(UTC):
                draft.reset_time()
                await show_draft(message, draft, edit=True)
                return
            links = {"link_url": draft.link_url, "link_title": draft.link_title}
            if draft.target is None:
                record = await reminders.create(owner, draft.id, schedule, **links)
                result = "✅ Напоминание создано"
                await log(
                    owner,
                    "reminder_created",
                    frequency=str(draft.frequency),
                    learning=bool(draft.link_url),
                )
            else:
                record = await reminders.update(
                    owner, draft.target, draft.version, schedule, **links
                )
                result = "✅ Напоминание изменено"
                await log(owner, "reminder_updated", frequency=str(draft.frequency))
            await state.update_data(draft=None)
            await message.edit_text(
                f"{result}\n\n🔔 {escape(record.event_text)}\n<i>{escape(format_due(record.next_due_utc, record.timezone))}"
                f" · {escape(FREQUENCIES[ReminderFrequency(record.frequency)])}</i>",
                reply_markup=management_markup(record),
                parse_mode="HTML",
                link_preview_options=_NO_PREVIEW,
            )
            await message.answer(
                "Готово! Все напоминания — в «🔔 Напоминания». Чем ещё помочь?", reply_markup=MENU
            )
            return
        else:
            await message.answer(STALE)
            return
        draft.action_key = uuid4()
        await show_draft(message, draft, edit=True)

    # ------------------------------------------------------------------ callbacks

    @router.callback_query(F.data == "ai:pdf")
    async def ai_answer_pdf(query: CallbackQuery, state: FSMContext) -> None:
        message = query.message
        data = await state.get_data()
        previous = data.get("last_ai_answer")
        if not isinstance(message, Message) or not previous:
            await query.answer("Этот ответ уже не сохранён, задай вопрос заново", show_alert=True)
            return
        await query.answer("Готовлю PDF…")
        try:
            content = await render_report(
                data.get("last_ai_title") or "Отчёт",
                [previous],
                links=data.get("last_ai_links") or [],
            )
            await message.answer_document(
                BufferedInputFile(content, filename="ai_result.pdf"),
                caption="PDF готов. Можно продолжить диалог с ИИ.",
                reply_markup=ACTIVE_MENU,
            )
        except Exception as error:
            _LOG.warning("pdf_report_failed error=%s", type(error).__name__)
            await message.answer(
                "Не удалось подготовить PDF. Попробуй ещё раз.", reply_markup=ACTIVE_MENU
            )

    @router.callback_query()
    async def callback(query: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(ai_help=False, last_ai_answer=None)
        message = query.message
        if not isinstance(message, Message):
            await query.answer()
            return
        owner = query.from_user.id
        data = query.data or ""
        try:
            await route_callback(query, message, state, owner, data)
        except ReminderError as error:
            await query.answer()
            await message.answer(str(error), parse_mode=None)

    async def route_callback(
        query: CallbackQuery, message: Message, state: FSMContext, owner: int, data: str
    ) -> None:
        prefix, _, rest = data.partition(":")
        if prefix == "ob":
            await query.answer()
            step, _, value = rest.partition(":")
            order = ("stage", "industry", "goal", "tax", "emp")
            if step == "skip":
                index = order.index(value) if value in order else len(order) - 1
                if index + 1 < len(order):
                    await ask_onboarding(message, order[index + 1], edit=True)
                else:
                    await finish_onboarding(message, owner)
                return
            if step == "goal" and value.isdigit() and int(value) < len(GOALS):
                await save_profile(owner, {"goal": GOALS[int(value)]})
                await ask_onboarding(message, "tax", edit=True)
                return
            if step == "tax" and value.isdigit() and int(value) < len(TAX_REGIMES):
                if TAX_REGIMES[int(value)] != "не знаю":
                    await save_profile(owner, {"tax_regime": TAX_REGIMES[int(value)]})
                await ask_onboarding(message, "emp", edit=True)
                return
            if step == "emp" and value in ("0", "1"):
                await save_profile(owner, {"employees": value == "1"})
                await finish_onboarding(message, owner)
                return
            if step in ("stage", "pstage") and value.isdigit() and int(value) < len(STAGES):
                await save_profile(owner, {"stage": STAGES[int(value)]})
                if step == "pstage":
                    await message.delete()
                    await show_route_intro(message, owner)
                    return
                await ask_onboarding(message, "industry", edit=True)
                return
            if step == "ind" and value.isdigit() and int(value) < len(INDUSTRIES):
                await save_profile(owner, {"industry": INDUSTRIES[int(value)]})
                await ask_onboarding(message, "goal", edit=True)
                return
            await message.answer(STALE)
            return
        if prefix == "q":
            # Follow-up question or clarification option.
            question = await _lookup(state, "pending", rest)
            await query.answer()
            if not isinstance(question, str):
                await message.answer(STALE)
                return
            await log(owner, "followup_clicked")
            await consult(message, state, owner, question, allow_clarify=False, echo=True)
            return
        if prefix == "ai":
            await query.answer()
            question = (await state.get_data()).get("last_unanswered")
            if not isinstance(question, str) or not question:
                await message.answer(STALE)
                return
            await message.edit_reply_markup(
                reply_markup=_without_button(message.reply_markup, "ai:")
            )
            wait = await message.answer("🤖 ИИ думает…")
            await log(owner, "ai_fallback_used")
            reply = await assistant.general_help(question, profile=await profiles.load(owner))
            if reply.kind == "offtopic":
                text = "Это не похоже на вопрос о бизнесе, поэтому ИИ не отвечает."
                await wait.edit_text(text)
                return
            await wait.edit_text(
                "🤖 <b>Ответ ИИ</b>\n<i>Не из базы знаний: общий совет, проверь важные детали.</i>\n\n"
                + escape(reply.summary),
                parse_mode="HTML",
                link_preview_options=_NO_PREVIEW,
            )
            return
        if prefix == "sm":
            await query.answer()
            last = (await state.get_data()).get("last_answer")
            if not isinstance(last, dict):
                await message.answer(STALE)
                return
            wait = await message.answer("🧸 Перевожу на простой язык…")
            text, terms = await assistant.simplify(last["question"], last["answer"])
            if not text:
                await wait.edit_text(
                    "Не получилось упростить, не исказив смысл. Лучше уточни вопрос — отвечу проще."
                )
                return
            await log(owner, "simplified", terms=len(terms))
            body = f"🧸 <b>Проще</b>\n{escape(text)}"
            if terms:
                body += "\n\n📖 <b>Термины</b>\n" + "\n".join(
                    f"• <b>{escape(term)}</b> — {escape(meaning)}" for term, meaning in terms
                )
            await wait.edit_text(body, parse_mode="HTML")
            return
        if prefix == "mv" and rest == "show":
            await query.answer()
            await show_progress(message, owner)
            return
        if prefix == "po":
            target = await _lookup(state, "paid", rest)
            material = knowledge.materials.get(target) if isinstance(target, str) else None
            await query.answer()
            if material is None or not material.is_paid:
                await message.answer(STALE)
                return
            if experiments is not None:
                await experiments.click(owner, material.id, "bot")
            else:
                await log(owner, "paid_offer_click", material_id=material.id)
            url = (
                material.purchase_url
                if material.access == "subscription" and material.purchase_url
                else material.url
            )
            await message.answer(
                f"💎 <b>{escape(material.title)}</b>\n"
                "Программа, стоимость и условия — на странице курса.",
                parse_mode="HTML",
                link_preview_options=_NO_PREVIEW,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Открыть страницу курса", url=url)]]
                ),
            )
            return
        if prefix == "sv":
            if rest in ("1", "2", "3", "4", "5"):
                await log(owner, "survey_score", score=int(rest))
                await query.answer("Спасибо за оценку! 🙌")
                await message.edit_text(f"⭐ Твоя оценка: {rest} из 5. Спасибо!")
            else:
                await query.answer()
            return
        if prefix == "fb":
            vote, _, key = rest.partition(":")
            if vote == "clar":
                await query.answer()
                await message.edit_reply_markup(reply_markup=None)
                await message.answer(
                    "Напиши, что именно нужно: сфера, этап, что уже пробовал — и я отвечу точнее."
                )
                return
            await log(owner, "feedback_up" if vote == "up" else "feedback_down")
            if vote == "up":
                await query.answer("Спасибо! 🙌")
                await message.edit_reply_markup(
                    reply_markup=_without_feedback(message.reply_markup)
                )
            else:
                await query.answer()
                await message.edit_reply_markup(
                    reply_markup=_without_feedback(message.reply_markup)
                )
                await message.answer(
                    "Спасибо, что сказал 🙏 Что поправить?\n"
                    "Можно объяснить проще или уточнить вопрос — тогда отвечу точнее.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [_button("🧸 Объясни проще", "sm:x")],
                            [_button("✍️ Уточню вопрос", "fb:clar:x")],
                        ]
                    ),
                )
            return
        if prefix == "learn":
            target = await _lookup(state, "learn", rest)
            await query.answer()
            material = knowledge.materials.get(target) if isinstance(target, str) else None
            if material is None:
                await message.answer(STALE)
                return
            if await get_draft(state):
                await message.answer(BUSY_DRAFT)
                return
            draft = Draft(event=f"📚 Изучить: {material.title}"[:1000])
            draft.link_url, draft.link_title = material.url, material.title
            await begin_reminder(message, state, draft)
            return
        if prefix == "tx":
            await query.answer()
            if rest == "show":
                await leave_flows(state)
                await state.set_state(None)
                await show_taxes(message, owner)
                return
            action, _, value = rest.partition(":")
            if action == "lf" and value.isdigit() and int(value) < len(LEGAL_FORMS):
                form = LEGAL_FORMS[int(value)]
                updates = {"legal_form": form}
                if form == "самозанятый":
                    updates["tax_regime"] = "НПД"
                await save_profile(owner, updates)
            elif action == "rg" and value.isdigit() and int(value) < len(TAX_REGIMES):
                regime = TAX_REGIMES[int(value)]
                if regime == "не знаю":
                    await message.edit_text("Подскажу, как выбрать режим 👇", reply_markup=None)
                    await consult(
                        message,
                        state,
                        owner,
                        "Как выбрать налоговый режим для бизнеса?",
                        allow_clarify=False,
                    )
                    return
                await save_profile(owner, {"tax_regime": regime})
            elif action == "emp" and value in ("0", "1"):
                await save_profile(owner, {"employees": value == "1"})
            elif action == "reset":
                profile = await profiles.load(owner)
                await profiles.save(
                    owner, profile.merged(tax_regime=None, legal_form=None, employees=None)
                )
            elif action == "remind":
                await create_tax_reminders(message, state, owner)
                return
            await show_taxes(message, owner, edit=True)
            return
        if prefix == "pf":
            await query.answer()
            action, _, value = rest.partition(":")
            if action == "show":
                await show_profile(message, owner)
            elif action == "describe":
                await state.update_data(profile_field="describe")
                await message.answer(
                    "✨ Расскажи о бизнесе в паре предложений: чем занимаешься, на каком этапе, "
                    "ИП или ООО, какой налоговый режим, что хочешь улучшить.\n\n"
                    "<i>Например: «Открываю кофейню, ИП на УСН 6%, двое сотрудников, хочу больше клиентов»</i>",
                    parse_mode="HTML",
                    reply_markup=ACTIVE_MENU,
                )
            elif action == "edit" and value in PROFILE_CHOICES:
                title, options = PROFILE_CHOICES[value]
                buttons = [
                    _button(
                        str(option).capitalize() if value != "tax_regime" else str(option),
                        f"pf:set:{value}:{i}",
                    )
                    for i, option in enumerate(options)
                ]
                await message.edit_text(
                    f"<b>{escape(title)}</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            *_rows(buttons, 2),
                            [
                                _button("‹ Назад", "pf:back"),
                                _button("Не указывать", f"pf:unset:{value}"),
                            ],
                        ]
                    ),
                )
            elif action == "set":
                field, _, index = value.partition(":")
                if (
                    field in PROFILE_CHOICES
                    and index.isdigit()
                    and int(index) < len(PROFILE_CHOICES[field][1])
                ):
                    await save_profile(owner, {field: _choice_value(field, int(index))})
                    await show_profile(message, owner, edit=True, status="✅ Сохранено")
                else:
                    await message.answer(STALE)
            elif action == "unset" and value in PROFILE_CHOICES:
                profile = await profiles.load(owner)
                await profiles.save(owner, profile.merged(**{value: None}))
                await show_profile(message, owner, edit=True, status="✅ Сохранено")
            elif action == "back":
                await show_profile(message, owner, edit=True)
            elif action == "clear":
                await message.edit_text(
                    "🗑 Удалить все сведения о бизнесе?\nНапоминания и маршрут останутся.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [_button("🗑 Удалить", "pf:confirm"), _button("‹ Назад", "pf:back")]
                        ]
                    ),
                )
            elif action == "confirm":
                await profiles.clear(owner)
                await state.update_data(history=[])
                await show_profile(message, owner, edit=True, status="Сведения удалены.")
            return
        if data in {"menu:reminders", "r:list"}:
            await query.answer()
            await show_reminders(message, owner, edit=data == "r:list")
            return
        if prefix == "r":
            await query.answer()
            draft = await get_draft(state)
            if data == "r:new":
                if draft:
                    await message.answer(BUSY_DRAFT)
                    await show_draft(message, draft)
                    return
                await begin_reminder(message, state, Draft())
                return
            parts = data.split(":")
            if len(parts) < 3:
                await message.answer(STALE)
                return
            _, action, key, *args = parts
            if action in {"d", "e", "l"} and len(args) == 1:
                await manage(message, state, owner, action, key, args[0])
            elif draft and key == draft.key:
                await advance(message, state, owner, draft, action, args)
            else:
                await message.answer(STALE)
            return
        await query.answer()
        await message.answer(STALE)

    # ------------------------------------------------------------------ consultation
    async def consult(
        message: Message,
        state: FSMContext,
        owner: int,
        question: str,
        *,
        allow_clarify: bool = True,
        echo: bool = False,
    ) -> None:
        bot: Bot = message.bot
        await bot.send_chat_action(message.chat.id, "typing")
        placeholder = await message.answer(
            f"🔎 <i>{escape(question[:120])}</i>\nИщу в базе знаний…"
            if echo
            else "🔎 Ищу в базе знаний…",
            parse_mode="HTML",
        )
        profile = await profiles.load(owner)
        history = (await state.get_data()).get("history", [])
        await log(owner, "question_asked", length=len(question))

        async def progress(found: int) -> None:
            await bot.send_chat_action(message.chat.id, "typing")
            await placeholder.edit_text(
                f"📚 Нашёл подходящих материалов: {found}\n✍️ Формулирую ответ…"
                if found
                else "✍️ Формулирую ответ…"
            )

        reply = await assistant.reply(
            question,
            profile=profile,
            history=history,
            allow_clarify=allow_clarify,
            progress=progress,
        )
        note = ""
        if reply.profile_updates:
            await save_profile(owner, reply.profile_updates)
            note = profile_note(reply.profile_updates)
        await _remember(state, "user", question)
        await deliver(placeholder, message, state, owner, question, reply, note)

    async def paid_offer(
        owner: int,
        state: FSMContext,
        paid: Material,
        course: CourseOffer | None,
        rows: list[list[InlineKeyboardButton]],
    ) -> str:
        """A/B: the variant decides how the course is offered; returns variant B's block.

        The click goes through a callback so it can be counted, then the real catalog
        link is shown (no redirect). Which course and its explanation come from offers.
        """
        variant = await experiments.expose(owner, paid.id, "bot") if experiments else CONTROL
        (key,) = await _store_keys(state, "paid", [paid.id])
        if variant == CONTROL:
            rows.append([_button("💎 Подробнее о курсе", f"po:{key}")])
            return ""
        rows.insert(0, [_button("💎 Посмотреть курс", f"po:{key}")])
        return offer_html(offer_pitch(paid, await profiles.load(owner), course))

    async def deliver(
        placeholder: Message,
        message: Message,
        state: FSMContext,
        owner: int,
        question: str,
        reply: Reply,
        note: str,
    ) -> None:
        if reply.kind == "clarify" and reply.clarification:
            texts = [f"{question} — {option}" for option in reply.clarification.options]
            keys = await _store_keys(state, "pending", texts)
            buttons = [
                _button(option, f"q:{key}")
                for option, key in zip(reply.clarification.options, keys, strict=True)
            ]
            await log(owner, "clarification_shown")
            await _remember(state, "assistant", reply.clarification.question)
            await placeholder.edit_text(
                f"🤔 {escape(reply.clarification.question)}" + (f"\n\n{note}" if note else ""),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button] for button in buttons]),
            )
            return
        route = InlineKeyboardMarkup(inline_keyboard=[[_button("🧭 Мой маршрут", "jn:show")]])
        if reply.kind == "greeting":
            await placeholder.edit_text(
                "Привет! 👋 Задай вопрос о бизнесе — или начни с маршрута обучения.",
                reply_markup=route,
            )
            return
        if reply.kind == "thanks":
            await placeholder.edit_text("Рад помочь! Если появятся вопросы — пиши 🙂")
            return
        if reply.kind == "offtopic" and reply.status == "unsafe":
            await log(owner, "offtopic")
            await placeholder.edit_text(f"🚫 {escape(reply.summary)}")
            return
        if reply.kind == "offtopic":
            await log(owner, "offtopic")
            await placeholder.edit_text(
                "Я помогаю только с вопросами бизнеса и обучения по материалам платформы 🙂\n\n"
                "Например: налоги и отчётность, запуск дела, маркетинг, продажи, команда."
            )
            return
        if reply.kind == "profile":
            await placeholder.edit_text(
                (note or "📝 Учёл.") + "\n\nТеперь советы будут точнее. Задай вопрос или "
                "посмотри маршрут — он подстроится под новые сведения.",
                parse_mode="HTML",
                reply_markup=route,
            )
            return
        if reply.kind == "tax":
            await placeholder.delete()
            await show_taxes(message, owner)
            return
        if reply.kind == "plan":
            await placeholder.delete()
            await show_route_intro(message, owner)
            return
        if reply.kind == "reminder":
            await placeholder.delete()
            if await get_draft(state):
                await message.answer(BUSY_DRAFT)
                return
            await begin_reminder(message, state, Draft(event=reply.summary[:1000]))
            return
        # A grounded answer.
        await log(
            owner,
            "answer_shown",
            status=reply.status,
            materials=[rec.material.id for rec in reply.recommendations],
        )
        await _remember(state, "assistant", reply.summary or reply.excerpt)
        simplifiable = reply.status in ("answered", "partial") and bool(reply.summary)
        if simplifiable:
            await state.update_data(
                last_answer={
                    "question": question,
                    "answer": " ".join([reply.summary, *reply.steps])[:2500],
                }
            )
        rows: list[list[InlineKeyboardButton]] = []
        if reply.followups:
            keys = await _store_keys(state, "pending", reply.followups)
            rows.extend(
                [_button(f"❓ {text}", f"q:{key}")]
                for text, key in zip(reply.followups, keys, strict=True)
            )
        learnable = next(
            (rec.material for rec in reply.recommendations if not rec.material.is_paid), None
        )
        bottom = []
        if learnable is not None:
            (key,) = await _store_keys(state, "learn", [learnable.id])
            bottom.append(_button("🔔 Напомнить изучить", f"learn:{key}"))
        bottom += [_button("👍", "fb:up:x"), _button("👎", "fb:down:x")]
        rows.append(bottom)
        # The learning route is always one tap away from an answer.
        simpler = [_button("🧸 Объясни проще", "sm:x")] if simplifiable else []
        rows.append([*simpler, _button("🧭 Мой маршрут", "jn:show")])
        if reply.status == "no_evidence":
            # Explicit opt-in: the base has no answer, so offer a clearly labelled AI answer.
            await state.update_data(last_unanswered=question[:1500])
            rows.insert(0, [_button("🤖 Ответить без базы", "ai:x")])
        paid = next((rec.material for rec in reply.recommendations if rec.material.is_paid), None)
        offer = ""
        if paid is not None:
            offer = await paid_offer(owner, state, paid, reply.course_offer, rows)
        rendered_reply = reply
        if offer and paid is not None:
            rendered_reply = replace(
                reply,
                recommendations=[
                    rec for rec in reply.recommendations if rec.material.id != paid.id
                ],
            )
        body = answer_html(rendered_reply, profile_note=note)
        if offer:
            body = f"{body}\n\n{offer}"
        await placeholder.edit_text(
            body,
            parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        if experiments is not None and await experiments.survey_due(owner):
            await log(owner, "survey_asked")
            await message.answer(
                "⭐ Насколько бот полезен для твоего бизнеса? Оцени от 1 до 5 — "
                "это займёт секунду и поможет стать лучше.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[_button(str(score), f"sv:{score}") for score in range(1, 6)]]
                ),
            )

    # ------------------------------------------------------------------ free text
    @router.message(F.text)
    async def text_message(message: Message, state: FSMContext) -> None:
        owner = message.from_user.id
        text = message.text.strip()
        if text in LEGACY_BUTTONS:
            text = LEGACY_BUTTONS[text]
        if text.startswith("/"):
            await state.update_data(ai_help=False, last_ai_answer=None)
            await message.answer(ASK_HINT, reply_markup=MENU, parse_mode="HTML")
            return
        if (await state.get_data()).get("ai_help") and is_pdf_request(text):
            if len(text) > 6000:
                await message.answer(
                    "Опиши задачу для PDF до 6000 символов.", reply_markup=ACTIVE_MENU
                )
                return
            progress = None
            try:
                progress = await message.answer("🤖 Готовлю PDF…")
            except TelegramAPIError as error:
                _LOG.warning("pdf_progress_failed error=%s", type(error).__name__)
            try:
                if is_pdf_export(text):
                    previous = (await state.get_data()).get("last_ai_answer")
                    if not previous:
                        await message.answer(
                            "Пока нет ответа для PDF. Опиши бизнес-задачу и попроси PDF.",
                            reply_markup=ACTIVE_MENU,
                        )
                        return
                    reply = Reply(
                        kind="ai_help",
                        summary=previous,
                        links=(await state.get_data()).get("last_ai_links") or [],
                        title=(await state.get_data()).get("last_ai_title") or "",
                    )
                else:
                    reply = await assistant.general_help(
                        text,
                        profile=await profiles.load(owner),
                        document=True,
                        history=(await state.get_data()).get("ai_history") or [],
                    )
                if reply.kind == "offtopic" or reply.status == "no_evidence":
                    await state.update_data(last_ai_answer=None)
                    await message.answer(
                        reply.summary or "PDF доступен для задач бизнеса и предпринимательства.",
                        reply_markup=ACTIVE_MENU,
                    )
                    return
                await state.update_data(
                    last_ai_answer=reply.summary,
                    last_ai_links=reply.links,
                    last_ai_title=reply.title,
                )
                try:
                    content = await render_report(
                        reply.title or "Отчёт", [reply.summary], links=reply.links
                    )
                    await message.answer_document(
                        BufferedInputFile(content, filename="ai_result.pdf"),
                        caption="PDF готов. Можно продолжить диалог с ИИ.",
                        reply_markup=ACTIVE_MENU,
                    )
                except Exception as error:
                    _LOG.warning("pdf_report_failed error=%s", type(error).__name__)
                    await message.answer(
                        "Не удалось подготовить или отправить PDF. Попробуй ещё раз.",
                        reply_markup=ACTIVE_MENU,
                    )
            finally:
                if progress is not None:
                    try:
                        await progress.delete()
                    except TelegramAPIError as error:
                        _LOG.warning("pdf_progress_cleanup_failed error=%s", type(error).__name__)
            return
        if len(text) > 1500:
            await message.answer(
                "Слишком длинное сообщение. Сформулируй вопрос короче (до 1500 символов)."
            )
            return
        if (await state.get_data()).get("ai_help"):
            if is_capabilities_question(text):
                await message.answer(
                    capabilities_text(), parse_mode="HTML", reply_markup=ACTIVE_MENU
                )
                return
            progress = None
            try:
                progress = await message.answer("🤖 ИИ думает…")
            except TelegramAPIError as error:
                _LOG.warning("ai_help_progress_failed error=%s", type(error).__name__)
            try:
                await message.bot.send_chat_action(message.chat.id, "typing")
            except TelegramAPIError as error:
                _LOG.warning("ai_help_typing_failed error=%s", type(error).__name__)
            profile = await profiles.load(owner)
            history = (await state.get_data()).get("ai_history") or []
            reply = await assistant.general_help(text, profile=profile, history=history)
            ok = reply.kind == "ai_help" and reply.status != "no_evidence"
            await state.update_data(
                last_ai_answer=reply.summary if ok else None,
                last_ai_links=reply.links if ok else [],
                last_ai_title=reply.title if ok else "",
                ai_history=(
                    [*history, {"q": text[:300], "a": reply.summary[:500]}][-4:] if ok else history
                ),
            )
            if reply.kind == "offtopic":
                await message.answer(
                    "Этот вопрос не связан с бизнесом или предпринимательством — "
                    "помощь ИИ доступна только по этим темам.\n\n"
                    "Например: налоги и отчётность, запуск дела, маркетинг, продажи, команда.\n"
                    "Напиши «что ты умеешь», чтобы увидеть все функции.",
                    reply_markup=ACTIVE_MENU,
                )
            elif not ok:
                await message.answer(escape(reply.summary), reply_markup=ACTIVE_MENU)
            else:
                body = (
                    "🤖 <b>" + escape(reply.title or "Ответ ИИ", quote=False) + "</b>" + chr(10) * 2
                )
                body += escape(reply.summary, quote=False)
                if reply.links:
                    body += chr(10) * 2 + "🔎 <b>Источники из интернета:</b>"
                    for link in reply.links[:5]:
                        body += (
                            chr(10)
                            + '• <a href="'
                            + escape(link["url"], quote=True)
                            + '">'
                            + escape(link["title"][:70], quote=False)
                            + "</a>"
                        )
                    body += chr(10) + "<i>Проверяй важные цифры по источнику.</i>"
                body += (
                    chr(10) * 2
                    + "<i>"
                    + escape(personalization_note(profile), quote=False)
                    + "</i>"
                )
                buttons = [
                    [InlineKeyboardButton(text="📄 Сохранить в PDF", callback_data="ai:pdf")]
                ]
                course = reply.course_offer
                if course is not None:
                    # Chosen by the app from public cards, never by the model (D-04).
                    pitch = await paid_offer(owner, state, course.material, course, buttons)
                    body += chr(10) * 2 + (
                        pitch
                        or "💎 <b>Курс платформы по теме</b>\n"
                        + material_line(course.material, course.why, link=False)
                    )
                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                chunks = _split_text(body)
                for index, chunk in enumerate(chunks):
                    await message.answer(
                        chunk,
                        parse_mode="HTML",
                        link_preview_options=_NO_PREVIEW,
                        reply_markup=keyboard if index == len(chunks) - 1 else None,
                    )
            if progress is not None:
                try:
                    await progress.delete()
                except TelegramAPIError as error:
                    _LOG.warning("ai_help_progress_cleanup_failed error=%s", type(error).__name__)
            return
        draft = await get_draft(state)
        if draft:
            await reminder_text(message, state, draft, text)
            return
        field = (await state.get_data()).get("profile_field")
        if field == "describe":
            await state.update_data(profile_field=None)
            wait = await message.answer("⏳ Разбираю…")
            updates = await assistant.extract_profile(text, await profiles.load(owner))
            if not updates:
                await wait.edit_text(
                    "Не получилось выделить сведения. Попробуй указать сферу, этап, форму (ИП/ООО) или налоговый режим."
                )
                return
            await save_profile(owner, updates)
            await wait.delete()
            await message.answer("Готово!", reply_markup=MENU)
            await show_profile(message, owner, status=profile_note(updates))
            return
        parsed = await parse_reminder_input(
            _normalize_time(text), now=datetime.now(UTC), model=model
        )
        if parsed and parsed.event:
            draft = Draft(id=uuid5(_OPERATION_NAMESPACE, f"{owner}:{message.message_id}"))
            draft.apply_input(parsed)
            await begin_reminder(message, state, draft)
            return
        await consult(message, state, owner, text)

    async def reminder_text(message: Message, state: FSMContext, draft: Draft, text: str) -> None:
        if len(text) > 1000:
            await message.answer("Укажи событие длиной до 1000 символов.")
            return
        if not draft.event and text.endswith("?"):
            await message.answer(
                "Сейчас настраиваем напоминание. Напиши, о чём напомнить, или нажми «Отмена»."
            )
            return
        text = _normalize_time(text)
        now = datetime.now(UTC)
        if draft.event and not draft.timezone:
            # The city question is open: «Казань», «я из Омска», «по екатеринбургу».
            zone = await resolve_timezone(text, model)
            if not zone:
                await message.answer(
                    "Не узнал город 🤔 Напиши ближайший крупный город, например «Самара» или «Омск»."
                )
                return
            draft.timezone = zone
            await state.update_data(reminder_timezone=zone)
            draft.resolve_input()
            draft.action_key = uuid4()
        elif draft.event and (draft.awaiting_time or draft.local_at is None):
            when = await parse_when(text, now=now, timezone=draft.timezone or None, model=model)
            if when is None or not (when.local_time or when.day or when.pending_day):
                await message.answer(
                    "Не понял время 🤔 Напиши, например: «завтра в 18:00» или "
                    "«каждый понедельник в 9»."
                )
                return
            draft.awaiting_time = False
            if when.timezone and not draft.timezone:
                draft.timezone = when.timezone
            draft.selected_day = when.day or draft.selected_day
            draft.pending_day = None if when.day else when.pending_day or draft.pending_day
            draft.entered_time = when.local_time or draft.entered_time
            if when.frequency != ReminderFrequency.ONCE:
                draft.frequency = when.frequency
            if draft.local_at is not None:
                draft.local_at, draft.fold = None, None
            if draft.entered_time and not (draft.selected_day or draft.pending_day):
                draft.pending_day = "daily"
            draft.resolve_input()
            draft.action_key = uuid4()
        elif not draft.event:
            parsed = await parse_reminder_input(
                text, now=now, timezone=draft.timezone or None, model=model
            )
            if parsed is None:
                parsed = await parse_reminder_input(
                    f"напомни {text}", now=now, timezone=draft.timezone or None, model=model
                )
            if parsed:
                draft.apply_input(parsed)
            else:
                draft.event = text
            draft.action_key = uuid4()
        await show_draft(message, draft)

    @router.message()
    async def unsupported(message: Message, state: FSMContext) -> None:
        await message.answer(
            "Пока понимаю только текст. Напиши вопрос о бизнесе 🙂",
            reply_markup=await current_menu(state),
        )

    @router.errors()
    async def safe_error(event: ErrorEvent) -> bool:
        _LOG.warning("telegram_update_failed error=%s", type(event.exception).__name__)
        message = event.update.message
        if event.update.callback_query:
            message = event.update.callback_query.message
        if isinstance(message, Message) and message.chat.type == "private":
            try:
                await message.answer("Что-то пошло не так. Попробуй ещё раз 🙏")
            except Exception:
                _LOG.warning("telegram_error_reply_failed")
        return True

    return router


def _without_button(
    markup: InlineKeyboardMarkup | None, prefix: str
) -> InlineKeyboardMarkup | None:
    if markup is None:
        return None
    rows = []
    for row in markup.inline_keyboard:
        kept = [button for button in row if not (button.callback_data or "").startswith(prefix)]
        if kept:
            rows.append(kept)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _without_feedback(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    return _without_button(markup, "fb:")


__all__ = ["create_router", "MENU", "Recommendation"]
