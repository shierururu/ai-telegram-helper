"""«Тренажёры» in the chat: negotiation simulator and business idea check.

The game logic is shared with the Mini App (`application.games`); this module only drives it
through Telegram. Progress lives in FSM data: `ng_*` for a negotiation, `id_*` for an idea
check. Every text handler that depends on a flow state first lets menu buttons and commands
fall through to the main router.
"""

from __future__ import annotations

import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from alpha_course.application.games import (
    MAX_MESSAGE,
    MAX_TURNS,
    SCENARIO_BY_ID,
    SCENARIOS,
    START_MOOD,
    GameCoach,
    GameUnavailable,
    NotBusiness,
    clamp,
    sanitize_history,
)
from alpha_course.application.privacy import mask_personal
from alpha_course.telegram.menu import ACTIVE_MENU, CANCEL, GAMES, MENU, leaves_flow
from alpha_course.telegram.render import material_line

_LOG = logging.getLogger(__name__)
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
STALE = "Кнопка устарела. Открой «🎯 Тренажёры» заново."
_RUSSIAN_WORD = re.compile(r"[а-я]+")
OUTCOMES = {
    "deal": "🤝 Договорились!",
    "no_deal": "🤷 Договориться не вышло",
    "walkout": "🚪 Собеседник ушёл",
}
VERDICTS = {
    "go": ("🟢", "Можно пробовать"),
    "test": ("🟡", "Нужна проверка"),
    "rethink": ("🔴", "Стоит пересмотреть"),
}
STATUS_ICONS = {"ok": "✅", "unknown": "⚠️", "risk": "❌"}
NEEDS = (("documents", "📄 Документы"), ("taxes", "🧾 Налоги"), ("permits", "🛂 Разрешения"))
MAX_ANSWERS = 6
MESSAGE_LIMIT = 3800

NG_KEYS = ("ng_sc", "ng_hist", "ng_mood", "ng_tip", "ng_pending", "ng_busy")
ID_KEYS = ("id_idea", "id_answers", "id_qs", "id_qi", "id_score")


class GameStates(StatesGroup):
    negotiation = State()
    idea_text = State()
    idea_answer = State()


# ---------------------------------------------------------------------------- rendering
def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _chunks(sections: list[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Pack whole HTML sections into messages that stay below Telegram's size limit."""
    result: list[str] = []
    current = ""
    for section in sections:
        if current and len(current) + len(section) + 2 > limit:
            result.append(current)
            current = section
        else:
            current = f"{current}\n\n{section}" if current else section
    if current:
        result.append(current)
    return result


def hub_html() -> str:
    return (
        "🎯 <b>Тренажёры</b>\n"
        "Короткие практики, чтобы навыки оставались в голове.\n\n"
        "🤝 <b>Переговоры</b> — потренируйся договариваться с клиентом, "
        "поставщиком или арендодателем\n"
        "💡 <b>Проверка идеи</b> — чек-лист по твоей идее с опорой на базу знаний"
    )


def hub_markup() -> InlineKeyboardMarkup:
    return _markup(
        [_button("🤝 Переговоры", "gm:n")],
        [_button("💡 Проверка идеи", "gm:i")],
    )


EXIT_HINT = f"\n\n<i>Выйти — «{CANCEL}» или любой раздел меню.</i>"


def mood_bar(mood: int) -> str:
    filled = max(0, min(5, (mood + 10) // 20))
    return f"{'🟩' * filled}{'⬜' * (5 - filled)} {mood}/100"


def idea_sections(result, previous: int | None) -> list[str]:
    icon, label = VERDICTS.get(result.verdict, ("🟡", "Нужна проверка"))
    head = f"{icon} <b>{label}</b> · {result.score}/100"
    if previous is not None and previous != result.score:
        head += f"\n<i>Оценка обновлена: {previous} → {result.score}</i>"
    sections = [head, escape(result.summary)]
    if not result.grounded:
        sections.append(
            "ℹ️ <i>Ответ опирается на ограниченные данные базы знаний: "
            "часть выводов — предположения, перепроверь их.</i>"
        )
    lines = "\n".join(
        f"{STATUS_ICONS.get(item.status, '⚠️')} <b>{escape(item.title)}</b> — {escape(item.why)}"
        for item in result.checklist
    )
    sections.append(f"📋 <b>Чек-лист</b>\n{lines}")
    for key, title in NEEDS:
        items = result.needs.get(key) or []
        if items:
            sections.append(f"{title}\n" + "\n".join(f"• {escape(text)}" for text in items))
    if result.risks:
        sections.append(
            "⚠️ <b>Риски</b>\n" + "\n".join(f"• {escape(text)}" for text in result.risks)
        )
    if result.next_steps:
        steps = "\n".join(f"{i}. {escape(text)}" for i, text in enumerate(result.next_steps, 1))
        sections.append(f"🚀 <b>Следующие шаги</b>\n{steps}")
    if result.materials:
        shelf = "\n\n".join(material_line(item) for item in result.materials)
        sections.append(f"📚 <b>Что почитать</b>\n{shelf}")
    if result.questions:
        asked = "\n".join(f"{i}. {escape(text)}" for i, text in enumerate(result.questions, 1))
        sections.append(
            f"❓ <b>Что уточнить</b>\n{asked}\n<i>Нажми вопрос и ответь — оценка станет точнее.</i>"
        )
    return sections


# ---------------------------------------------------------------------------- router
def create_games_router(assistant, profiles, events=None, model=None) -> Router:
    router = Router(name="games")
    router.message.filter(
        F.chat.type == "private",
        lambda message: message.from_user and message.from_user.id == message.chat.id,
    )
    router.callback_query.filter(
        F.message.chat.type == "private", lambda query: query.from_user.id == query.message.chat.id
    )
    knowledge = assistant.knowledge
    coach = GameCoach(
        knowledge,
        getattr(assistant, "model", None) or model,
        fast_model=getattr(assistant, "fast_model", "deepseek-v4.1-flash"),
        answer_model=getattr(assistant, "answer_model", "deepseek-v4.1-flash"),
    )

    async def log(owner: int, kind: str, **payload) -> None:
        if events is not None:
            try:
                await events.log(owner, kind, **payload)
            except Exception:
                _LOG.warning("event_log_failed")

    async def typing(message: Message) -> None:
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception as error:
            _LOG.warning("games_typing_failed error=%s", type(error).__name__)

    async def say(message: Message, text: str, markup=None, *, reply_markup=None) -> Message:
        return await message.answer(
            text,
            parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
            reply_markup=markup or reply_markup,
        )

    async def deliver(wait: Message, message: Message, chunks: list[str], markup) -> None:
        """Replace the placeholder with the first part, send the rest, buttons on the last."""
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            if index == 0:
                await wait.edit_text(
                    chunk,
                    parse_mode="HTML",
                    link_preview_options=_NO_PREVIEW,
                    reply_markup=markup if last else None,
                )
            else:
                await say(message, chunk, markup if last else None)

    async def clear_flow(state: FSMContext) -> None:
        await state.set_state(None)
        await state.update_data(**dict.fromkeys((*NG_KEYS, *ID_KEYS)))

    async def reset_other_flows(state: FSMContext) -> None:
        await state.set_state(None)
        await state.update_data(ai_help=False, draft=None, profile_field=None, jn_wait=None)

    async def exits(message: Message, state: FSMContext) -> bool:
        """True when the message closed the game; menu buttons fall through to the main router."""
        text = (message.text or "").strip()
        words = text.split()
        if text in (CANCEL, "Отмена") or (words and words[0].split("@")[0] == "/cancel"):
            await clear_flow(state)
            await message.answer("Ок, вышел из тренажёра.", reply_markup=MENU)
            return True
        if leaves_flow(text):
            await clear_flow(state)
            raise SkipHandler
        return False

    # ------------------------------------------------------------------ hub
    @router.message(F.text == GAMES)
    @router.message(Command("games"))
    async def open_hub(message: Message, state: FSMContext) -> None:
        await clear_flow(state)
        await reset_other_flows(state)
        await say(message, hub_html(), hub_markup())

    # ------------------------------------------------------------------ Переговоры
    async def show_picker(message: Message, state: FSMContext) -> None:
        await clear_flow(state)
        await reset_other_flows(state)
        best = (await state.get_data()).get("ng_best") or {}
        cards = "\n\n".join(
            f"{index}. <b>{escape(item.title)}</b> · {escape(item.difficulty)}\n"
            f"<i>{escape(item.brief)}</i>"
            for index, item in enumerate(SCENARIOS, 1)
        )
        rows = [
            [
                _button(
                    item.title + (f" · {best[item.id]}/100" if item.id in best else ""),
                    f"gm:n:{item.id}",
                )
            ]
            for item in SCENARIOS
        ]
        rows.append([_button("⬅️ К тренажёрам", "gm:hub")])
        await say(
            message,
            "🤝 <b>Выбери ситуацию</b>\nИИ сыграет собеседника. "
            f"У тебя {MAX_TURNS} реплик, чтобы договориться.\n\n{cards}",
            _markup(*rows),
        )

    async def start_negotiation(message: Message, state: FSMContext, owner: int, sid: str) -> None:
        scenario = SCENARIO_BY_ID.get(sid)
        if scenario is None:
            await message.answer(STALE)
            return
        await clear_flow(state)
        await reset_other_flows(state)
        await state.set_state(GameStates.negotiation)
        await state.update_data(
            ng_sc=scenario.id,
            ng_hist=[{"role": "them", "text": scenario.opening}],
            ng_mood=START_MOOD,
            ng_tip=None,
            ng_pending=None,
            ng_busy=False,
        )
        await log(owner, "negotiation_started", scenario=scenario.id)
        await say(
            message,
            f"🤝 <b>{escape(scenario.title)}</b> · {escape(scenario.difficulty)}\n\n"
            f"<i>{escape(scenario.brief)}</i>\n"
            f"🎭 <b>Твоя роль:</b> {escape(scenario.role)}\n"
            f"🎯 <b>Цель:</b> {escape(scenario.goal)}\n\n"
            "Характер собеседника и его пределы не раскрыты — пойми их по ходу разговора.\n\n"
            f"🗣 <b>Собеседник:</b>\n«{escape(scenario.opening)}»\n\n"
            f"Настрой: {mood_bar(START_MOOD)}\n"
            f"Реплика 1 из {MAX_TURNS} — напиши, что ответишь." + EXIT_HINT,
            reply_markup=ACTIVE_MENU,
        )

    async def play_turn(message: Message, state: FSMContext, owner: int, masked: str) -> None:
        data = await state.get_data()
        scenario = SCENARIO_BY_ID.get(data.get("ng_sc") or "")
        if scenario is None:
            await message.answer(STALE)
            return
        if data.get("ng_busy"):
            await message.answer("Собеседник ещё отвечает — подожди секунду.")
            return
        await state.update_data(ng_busy=True)
        history = sanitize_history(data.get("ng_hist"))
        mood = clamp(data.get("ng_mood"), 0, 100, START_MOOD)
        try:
            await typing(message)
            result = await coach.negotiation_turn(scenario, history, masked, mood=mood)
        except GameUnavailable:
            await state.update_data(ng_busy=False, ng_pending=masked)
            await say(
                message,
                "Собеседник сейчас недоступен, попробуй чуть позже.",
                _markup([_button("🔁 Повторить", "gm:nr")]),
            )
            return
        except Exception as error:
            _LOG.warning("negotiation_turn_failed error=%s", type(error).__name__)
            await state.update_data(ng_busy=False, ng_pending=masked)
            await say(
                message,
                "Что-то пошло не так, реплика не потеряна. Попробуем ещё раз?",
                _markup([_button("🔁 Повторить", "gm:nr")]),
            )
            return
        history += [{"role": "me", "text": masked}, {"role": "them", "text": result.reply}]
        await state.update_data(
            ng_busy=False,
            ng_pending=None,
            ng_hist=history,
            ng_mood=result.mood,
            ng_tip=result.tip,
        )
        text = f"🗣 <b>Собеседник:</b>\n«{escape(result.reply)}»\n\nНастрой: {mood_bar(result.mood)}"
        if result.finished:
            await say(message, f"{text}\n\n{OUTCOMES.get(result.outcome or '', '')}")
            await finish_negotiation(message, state, owner)
            return
        text += f"\nРеплика {result.turn + 1} из {MAX_TURNS}"
        rows = []
        if result.tip:
            rows.append([_button("💡 Подсказка", "gm:nt")])
        rows.append([_button("🏁 Завершить сейчас", "gm:nf")])
        await say(message, text, _markup(*rows))

    async def finish_negotiation(message: Message, state: FSMContext, owner: int) -> None:
        data = await state.get_data()
        scenario = SCENARIO_BY_ID.get(data.get("ng_sc") or "")
        if scenario is None:
            await message.answer(STALE)
            return
        history = sanitize_history(data.get("ng_hist"))
        if not any(item["role"] == "me" for item in history):
            await message.answer("Сначала поговори с собеседником хотя бы одну реплику.")
            return
        if data.get("ng_busy"):
            await message.answer("Собеседник ещё отвечает — подожди секунду.")
            return
        mood = clamp(data.get("ng_mood"), 0, 100, START_MOOD)
        await state.update_data(ng_busy=True)
        wait = await message.answer("⏳ Разбираю переговоры…")
        await typing(message)
        try:
            result = await coach.negotiation_finish(scenario, history, mood=mood)
        except Exception as error:
            _LOG.warning("negotiation_finish_failed error=%s", type(error).__name__)
            await state.update_data(ng_busy=False)
            await wait.edit_text(
                "Не получилось разобрать переговоры. Попробуем ещё раз?",
                reply_markup=_markup([_button("🔁 Повторить", "gm:nf")]),
            )
            return
        best = dict(data.get("ng_best") or {})
        previous = best.get(scenario.id)
        record = previous is None or result.score > previous
        if record:
            best[scenario.id] = result.score
        await log(
            owner,
            "negotiation_finished",
            scenario=scenario.id,
            score=result.score,
            outcome=result.outcome,
        )
        sections = [
            f"🏁 <b>Итог: {escape(scenario.title)}</b>\n{OUTCOMES.get(result.outcome, '')}",
            f"<b>Оценка: {result.score}/100</b> — {escape(result.verdict)}"
            + (
                "\n🏆 Новый личный рекорд!"
                if record and previous is not None
                else (f"\nЛучший результат: {previous}/100" if previous is not None else "")
            ),
        ]
        if result.strengths:
            sections.append(
                "✅ <b>Что получилось</b>\n" + "\n".join(f"• {escape(t)}" for t in result.strengths)
            )
        if result.improvements:
            sections.append(
                "🔧 <b>Что улучшить</b>\n"
                + "\n".join(f"• {escape(t)}" for t in result.improvements)
            )
        if result.better_phrases:
            phrases = "\n\n".join(
                f"Было: «{escape(row['instead'])}»\nЛучше: «{escape(row['say'])}»"
                for row in result.better_phrases
            )
            sections.append(f"💬 <b>Лучше сказать так</b>\n{phrases}")
        if result.materials:
            shelf = "\n\n".join(material_line(item) for item in result.materials)
            sections.append(f"📚 <b>Что почитать</b>\n{shelf}")
        await clear_flow(state)
        await state.update_data(ng_best=best)
        await deliver(
            wait,
            message,
            _chunks(sections),
            _markup(
                [_button("🔁 Ещё раз", f"gm:n:{scenario.id}")],
                [_button("🤝 Другой сценарий", "gm:n"), _button("⬅️ К тренажёрам", "gm:hub")],
            ),
        )

    @router.message(GameStates.negotiation, F.text)
    async def negotiation_message(message: Message, state: FSMContext) -> None:
        if await exits(message, state):
            return
        text = message.text.strip()
        if len(text) > MAX_MESSAGE:
            await message.answer(
                f"Реплика слишком длинная: {len(text)} символов, максимум {MAX_MESSAGE}. "
                "Скажи короче — реплика не потрачена."
            )
            return
        if not any(char.isalpha() for char in text):
            await message.answer("Напиши реплику словами — реплика не потрачена.")
            return
        await play_turn(
            message, state, message.from_user.id, mask_personal(text, limit=MAX_MESSAGE)
        )

    # ------------------------------------------------------------------ Проверка идеи
    async def ask_idea(message: Message, state: FSMContext) -> None:
        await clear_flow(state)
        await reset_other_flows(state)
        await state.set_state(GameStates.idea_text)
        await say(
            message,
            "💡 <b>Проверка идеи</b>\nОпиши бизнес-идею одним-двумя предложениями: что ты хочешь "
            "продавать, кому и как.\n<i>Не пиши личные данные: имена, телефоны, адреса.</i>"
            + EXIT_HINT,
            reply_markup=ACTIVE_MENU,
        )

    @router.message(GameStates.idea_text, F.text)
    async def idea_message(message: Message, state: FSMContext) -> None:
        if await exits(message, state):
            return
        text = message.text.strip()
        if len(text) < 8 or len(text.split()) < 2:
            await message.answer("Опиши идею хотя бы одним-двумя предложениями.")
            return
        await state.update_data(
            id_idea=mask_personal(text, limit=800), id_answers=[], id_qs=None, id_score=None
        )
        await run_check(message, state, message.from_user.id)

    @router.message(GameStates.idea_answer, F.text)
    async def idea_answer_message(message: Message, state: FSMContext) -> None:
        if await exits(message, state):
            return
        data = await state.get_data()
        questions = data.get("id_qs") or []
        index = data.get("id_qi")
        if not data.get("id_idea") or not isinstance(index, int) or index >= len(questions):
            await state.set_state(None)
            await message.answer(STALE)
            return
        text = message.text.strip()
        if len(text) < 2:
            await message.answer("Ответь хотя бы парой слов.")
            return
        question = mask_personal(questions[index], limit=200)
        answers = [row for row in data.get("id_answers") or [] if row["question"] != question]
        answers.append({"question": question, "answer": mask_personal(text, limit=300)})
        await state.update_data(id_answers=answers[-MAX_ANSWERS:])
        await run_check(message, state, message.from_user.id)

    async def run_check(message: Message, state: FSMContext, owner: int) -> None:
        data = await state.get_data()
        idea = data.get("id_idea")
        if not idea:
            await message.answer(STALE)
            return
        answers = list(data.get("id_answers") or [])
        previous = data.get("id_score")
        wait = await message.answer("⏳ Проверяю по базе знаний…")
        await typing(message)
        try:
            profile = await profiles.load(owner)
            result = await coach.check_idea(idea, city="", answers=answers, profile=profile)
        except NotBusiness:
            await state.set_state(GameStates.idea_text)
            await state.update_data(id_answers=[])
            await wait.edit_text(
                "Это не похоже на бизнес-идею. Расскажи, что ты хочешь продавать и кому."
            )
            return
        except GameUnavailable:
            await state.set_state(None)
            await wait.edit_text(
                "Проверка идеи сейчас недоступна, попробуй чуть позже.",
                reply_markup=_markup([_button("🔁 Повторить", "gm:ir")]),
            )
            return
        except Exception as error:
            _LOG.warning("idea_check_failed error=%s", type(error).__name__)
            await state.set_state(None)
            await wait.edit_text(
                "Что-то пошло не так при проверке. Попробуем ещё раз?",
                reply_markup=_markup([_button("🔁 Повторить", "gm:ir")]),
            )
            return
        await log(owner, "idea_checked", verdict=result.verdict, score=result.score)
        await state.set_state(None)
        await state.update_data(id_qs=result.questions, id_score=result.score, id_qi=None)
        rows = [
            [_button(f"❓ {i}. {_clip(q, 48)}", f"gm:iq:{i - 1}")]
            for i, q in enumerate(result.questions, 1)
        ]
        rows.append([_button("📅 План на 7 дней под идею", "gm:ip")])
        rows.append([_button("🔁 Другая идея", "gm:i"), _button("⬅️ К тренажёрам", "gm:hub")])
        await deliver(wait, message, _chunks(idea_sections(result, previous)), _markup(*rows))

    # ------------------------------------------------------------------ callbacks
    @router.callback_query(F.data.startswith("gm:"))
    async def callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        message = query.message
        if not isinstance(message, Message):
            return
        owner = query.from_user.id
        parts = (query.data or "").split(":")
        action = parts[1] if len(parts) > 1 else ""
        data = await state.get_data()
        if action == "hub":
            await clear_flow(state)
            await reset_other_flows(state)
            await say(message, hub_html(), hub_markup())
        elif action == "n" and len(parts) == 2:
            await show_picker(message, state)
        elif action == "n" and len(parts) == 3:
            await start_negotiation(message, state, owner, parts[2])
        elif action == "nt":
            tip = data.get("ng_tip") if data.get("ng_sc") else None
            if not tip:
                await message.answer(STALE)
                return
            await say(message, f"💡 <b>Подсказка</b>\n{escape(tip)}")
        elif action == "nf":
            if not data.get("ng_sc"):
                await message.answer(STALE)
                return
            await finish_negotiation(message, state, owner)
        elif action == "nr":
            pending = data.get("ng_pending")
            if not data.get("ng_sc") or not pending:
                await message.answer(STALE)
                return
            await play_turn(message, state, owner, pending)
        elif action == "i":
            await ask_idea(message, state)
        elif action == "ir":
            if not data.get("id_idea"):
                await message.answer(STALE)
                return
            await run_check(message, state, owner)
        elif action == "iq" and len(parts) == 3 and parts[2].isdigit():
            questions = data.get("id_qs") or []
            index = int(parts[2])
            if not data.get("id_idea") or index >= len(questions):
                await message.answer(STALE)
                return
            await reset_other_flows(state)
            await state.set_state(GameStates.idea_answer)
            await state.update_data(id_qi=index)
            await say(
                message,
                f"❓ {escape(questions[index])}\nНапиши ответ одним-двумя предложениями.",
                reply_markup=ACTIVE_MENU,
            )
        elif action == "ip":
            idea = data.get("id_idea")
            if not idea:
                await message.answer(STALE)
                return
            goal = _clip("Запустить: " + idea, 80)
            await reset_other_flows(state)
            await state.update_data(jn_goal=goal, jn_draft=None)
            await say(
                message,
                "📅 <b>План на 7 дней под идею</b>\n"
                f"🎯 Цель: <i>{escape(goal)}</i>\n"
                "Соберу короткий маршрут из бесплатных материалов базы — по шагу в день. "
                "Время и город уточню дальше.",
                _markup(
                    [_button("📅 Собрать план", "jn:goal")],
                    [_button("⬅️ К тренажёрам", "gm:hub")],
                ),
            )
        else:
            await message.answer(STALE)

    return router


def _ordinal(value) -> int:
    """Ordinal of an ISO date string, or 0 when it is missing or broken."""
    from datetime import date

    try:
        return date.fromisoformat(str(value)).toordinal()
    except ValueError:
        return 0


__all__ = ["GameStates", "create_games_router"]
