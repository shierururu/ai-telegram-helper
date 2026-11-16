"""Telegram version of the Mini App tool «🧮 Финмодель».

Numbers come only from application.finmodel.
"""

from __future__ import annotations

import json
import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from alpha_course.application.assistant import Assistant
from alpha_course.application.finmodel import (
    MAX_VALUE,
    PROFILE_REGIMES,
    REGIMES,
    FinmodelError,
    calculate,
    parse_inputs,
)
from alpha_course.application.privacy import mask_personal
from alpha_course.telegram.menu import CANCEL, FINMODEL, MENU, leaves_flow
from alpha_course.telegram.profiles import ProfileStore
from alpha_course.webapp.pdf import PdfUnavailable, render_pdf, report_html
from alpha_course.webapp.tools import EXTRACT

_LOG = logging.getLogger(__name__)
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
STALE = "Кнопка устарела. Открой раздел заново."
CANCEL_TEXTS = frozenset({CANCEL, "Отмена"})
SHORT_REGIMES = {
    "npd": "НПД",
    "usn_income": "УСН «Доходы»",
    "usn_income_expense": "УСН «Д−Р»",
}
VERDICTS = {
    "ok": ("🟢", "Модель сходится"),
    "tight": ("🟡", "Запас прочности небольшой"),
    "loss": ("🔴", "Работа в минус"),
}
# key, label, required, hint
FM_FIELDS = (
    ("price", "Цена одной продажи", True, "Средний чек или цена единицы. Например: 300"),
    (
        "unit_cost",
        "Переменные затраты на продажу",
        False,
        "Себестоимость, закупка, доставка на одну продажу. Например: 100",
    ),
    ("units", "Продаж в месяц", True, "Сколько продаж планируешь. Например: 500"),
    ("fixed", "Постоянные расходы в месяц", False, "Аренда, реклама, связь. Например: 40 000"),
    ("payroll", "Зарплаты в месяц", False, "До налогов. Например: 120 000"),
    ("investment", "Стартовые вложения", False, "Разовые траты на запуск. Например: 500к"),
)
FM_KEYS = tuple(item[0] for item in FM_FIELDS)
FM_BY_KEY = {item[0]: item for item in FM_FIELDS}
FM_LABELS = {key: label for key, label, *_ in FM_FIELDS}
FM_DATA = ("fm_in", "fm_queue", "fm_guided", "fm_regime", "fm_client", "fm_last")

_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)(млн|миллион\w*|тыс\w*|м|m|к|k)?")
_MULTIPLIERS = {"млн": 1_000_000, "миллион": 1_000_000, "тыс": 1000, "к": 1000, "k": 1000}


class Fm(StatesGroup):
    describe = State()
    step = State()


def parse_money(text: str) -> float | None:
    """«50 000», «50к», «1,2 млн», «50 тыс», «300 ₽» → number; None when it is not a number."""
    cleaned = re.sub(r"[\s  ]+", "", text.lower())
    cleaned = re.sub(r"(₽|руб\w*|р\.?)$", "", cleaned)
    match = _NUMBER.fullmatch(cleaned)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    suffix = match.group(2)
    if suffix:
        for prefix, factor in _MULTIPLIERS.items():
            if suffix.startswith(prefix) or (prefix == "млн" and suffix in ("м", "m")):
                value *= factor
                break
    if value > MAX_VALUE:
        return None
    return value


def money(value: float) -> str:
    amount = int(round(value))
    sign = "−" if amount < 0 else ""
    return sign + f"{abs(amount):,}".replace(",", " ") + " ₽"


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


def result_html(data: dict, inputs: dict) -> str:
    emoji, verdict = VERDICTS.get(data["verdict"], VERDICTS["ok"])
    rate = f"{data['tax_rate']:g}%"
    expenses = data["variable"] + data["fixed_total"]
    lines = [
        f"{emoji} <b>{verdict}</b>",
        f"Прибыль в месяц: <b>{money(data['profit'])}</b>",
        "",
        f"💰 Выручка: <b>{money(data['revenue'])}</b>",
        f"🧱 Расходы: <b>{money(expenses)}</b>",
        f"🧾 Налоги ({escape(SHORT_REGIMES[data['regime']])}, {rate}): <b>{money(data['tax'])}</b>",
        f"📈 Маржа с продажи: <b>{money(data['unit_margin'])}</b> ({data['margin_pct']:g}%)",
        "",
    ]
    even = data["break_even_units"]
    if even is None:
        lines.append("⚖️ Точка безубыточности недостижима: цена не покрывает затраты.")
    elif even == 0:
        lines.append("⚖️ Безубыточность: уже с первой продажи.")
    else:
        lines.append(
            f"⚖️ Безубыточность: <b>{even} шт.</b> в месяц (≈ {money(data['break_even_revenue'])})"
        )
    if data["safety_pct"] is not None:
        lines.append(f"🛡 Запас прочности: <b>{data['safety_pct']:g}%</b>")
    if data["payback_months"] is not None:
        lines.append(f"⏱ Окупаемость вложений: <b>{data['payback_months']:g} мес.</b>")
    lines += ["", "📊 <b>Сценарии</b>"]
    for item in data["scenarios"]:
        mark = "🟢" if item["profit"] >= 0 else "🔴"
        lines.append(f"{mark} {escape(item['label'])}: <b>{money(item['profit'])}</b>")
    best = next((item for item in data["regimes"] if item.get("best")), None)
    if best is not None:
        lines.append("")
        if best["current"]:
            lines.append(f"💡 Из доступных режимов твой выгоднее всего: {escape(best['label'])}.")
        else:
            lines.append(
                f"💡 Выгоднее: <b>{escape(best['label'])}</b>, прибыль {money(best['profit'])}."
            )
    if not inputs.get("fixed") and not inputs.get("payroll"):
        lines += ["", "ℹ️ Постоянные расходы и зарплаты не указаны — с ними прибыль будет ниже."]
    for warning in data["warnings"]:
        lines.append(f"⚠️ {escape(warning)}")
    return "\n".join(lines)


def compare_html(data: dict) -> str:
    lines = [
        "📊 <b>Сравнение режимов</b>",
        "<i>Ставки по умолчанию, в твоём регионе могут отличаться.</i>",
        "",
    ]
    for item in data["regimes"]:
        label = escape(item["label"])
        if not item["available"]:
            lines.append(
                f"🚫 <b>{label}</b> — недоступен при зарплатах или доходе выше 2,4 млн ₽ в год"
            )
            continue
        tags = []
        if item["current"]:
            tags.append("сейчас")
        if item.get("best"):
            tags.append("выгоднее всего")
        suffix = f" ({', '.join(tags)})" if tags else ""
        lines.append(
            f"{'⭐' if item.get('best') else '▫️'} <b>{label}</b>{suffix}\n"
            f"    налог {money(item['tax'])}, прибыль <b>{money(item['profit'])}</b>"
        )
    return "\n".join(lines)


def create_tools_router(
    assistant: Assistant,
    profiles: ProfileStore,
    events=None,
    model=None,
) -> Router:
    router = Router(name="tools")
    router.message.filter(
        F.chat.type == "private",
        lambda message: message.from_user and message.from_user.id == message.chat.id,
    )
    router.callback_query.filter(
        F.message.chat.type == "private", lambda query: query.from_user.id == query.message.chat.id
    )
    model = model if model is not None else getattr(assistant, "model", None)
    answer_model = getattr(assistant, "answer_model", "deepseek-v4.1-flash")

    async def log(owner: int, kind: str, **payload) -> None:
        if events is not None:
            try:
                await events.log(owner, kind, **payload)
            except Exception:
                _LOG.warning("event_log_failed")

    async def reset(state: FSMContext) -> None:
        await state.set_state(None)
        await state.update_data(**{key: None for key in FM_DATA})

    async def guard(message: Message, state: FSMContext) -> bool:
        """True when the message was a cancel (handled); raises SkipHandler for menu/commands."""
        text = (message.text or "").strip()
        if text in CANCEL_TEXTS or text.split("@")[0] == "/cancel":
            await reset(state)
            await message.answer("Ок, отменил.", reply_markup=MENU)
            return True
        if leaves_flow(text):
            await reset(state)
            raise SkipHandler
        return False

    async def say(message: Message, text: str, markup=None, *, edit: bool = False) -> None:
        reply = message.edit_text if edit else message.answer
        await reply(text, parse_mode="HTML", reply_markup=markup, link_preview_options=_NO_PREVIEW)

    # ================================================================== finmodel
    async def fm_intro(message: Message, state: FSMContext) -> None:
        await state.update_data(ai_help=False, draft=None, profile_field=None, jn_wait=None)
        await reset(state)
        await say(
            message,
            "🧮 <b>Финмодель</b>\n"
            "Посчитаю прибыль, налоги и точку безубыточности по твоим цифрам "
            "и покажу три сценария.\n"
            "Как удобнее ввести данные?",
            _markup(
                [_button("✍️ Опиши словами", "fm:w")],
                [_button("📋 По шагам", "fm:g")],
            ),
        )

    @router.message(F.text == FINMODEL)
    @router.message(Command("finmodel"))
    async def finmodel_menu(message: Message, state: FSMContext) -> None:
        await fm_intro(message, state)

    async def fm_ask(message: Message, state: FSMContext, *, edit: bool = False) -> None:
        data = await state.get_data()
        queue = data.get("fm_queue") or []
        if not queue:
            await fm_after_numbers(message, state, edit=edit)
            return
        key = queue[0]
        _, label, required, hint = FM_BY_KEY[key]
        step = f" · шаг {FM_KEYS.index(key) + 1} из {len(FM_KEYS)}" if data.get("fm_guided") else ""
        current = (data.get("fm_in") or {}).get(key)
        row = []
        if current is not None:
            row.append(_button(f"Оставить: {money(current)}", "fm:s"))
        elif not required:
            row.append(_button("Пропустить", "fm:s"))
        await state.set_state(Fm.step)
        await say(
            message,
            f"<b>{escape(label)}</b>{step}\n{escape(hint)}\n"
            "<i>Можно писать так: 50 000, 50к, 1,2 млн.</i>"
            + ("" if required else "\n<i>Необязательно.</i>"),
            _markup(row, [_button("✖️ Отмена", "fm:x")])
            if row
            else _markup([_button("✖️ Отмена", "fm:x")]),
            edit=edit,
        )

    async def fm_after_numbers(message: Message, state: FSMContext, *, edit: bool) -> None:
        data = await state.get_data()
        if data.get("fm_regime"):
            await fm_compute(message, state, edit=edit)
            return
        profile = await profiles.load(message.chat.id)
        hint = PROFILE_REGIMES.get(profile.tax_regime or "")
        rows = []
        if hint:
            rows.append([_button(f"Использовать: {SHORT_REGIMES[hint]}", f"fm:r:{hint}")])
        rows += [
            [_button(short, f"fm:r:{key}")] for key, short in SHORT_REGIMES.items() if key != hint
        ]
        await state.set_state(None)
        await say(
            message,
            "🧾 <b>Налоговый режим</b>\nПо какому режиму работаешь или планируешь работать?",
            _markup(*rows),
            edit=edit,
        )

    async def fm_compute(message: Message, state: FSMContext, *, edit: bool) -> None:
        data = await state.get_data()
        body = {
            **(data.get("fm_in") or {}),
            "regime": data.get("fm_regime") or "usn_income",
            "client": data.get("fm_client") or "person",
        }
        try:
            result = calculate(parse_inputs(body))
        except FinmodelError as error:
            await state.update_data(fm_queue=list(FM_KEYS), fm_guided=True)
            await message.answer(f"Не получилось посчитать: {escape(str(error))}")
            await fm_ask(message, state)
            return
        await state.set_state(None)
        await state.update_data(fm_last=body, fm_queue=[])
        await log(message.chat.id, "finmodel_calculated", regime=result.inputs.regime)
        await say(
            message,
            result_html(result.public(), body),
            _markup(
                [_button("📄 Скачать PDF", "fm:pdf")],
                [_button("🔁 Изменить цифры", "fm:edit"), _button("📊 Сравнить режимы", "fm:cmp")],
            ),
            edit=edit,
        )

    async def extract(text: str) -> dict | None:
        if model is None:
            return None
        raw = None
        for _ in range(2):
            try:
                raw = await model.complete(
                    EXTRACT,
                    json.dumps({"text": mask_personal(text, limit=1200)}, ensure_ascii=False),
                    model=answer_model,
                    json_mode=True,
                    temperature=0,
                    max_tokens=1500,
                )
                if raw and raw.strip():
                    break
            except Exception as error:
                _LOG.warning("finmodel_extract_failed error=%s", type(error).__name__)
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        fields: dict = {}
        for key in FM_KEYS:
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                fields[key] = value
        if data.get("client") in ("person", "company"):
            fields["client"] = data["client"]
        return fields

    @router.message(Fm.describe, F.text)
    async def fm_describe_text(message: Message, state: FSMContext) -> None:
        if await guard(message, state):
            return
        owner = message.from_user.id
        wait = await message.answer("⏳ Разбираю описание…")
        fields = await extract(message.text.strip()[:1200])
        if fields is None:
            await state.update_data(fm_in={}, fm_queue=list(FM_KEYS), fm_guided=True)
            await wait.edit_text("Не получилось разобрать описание 😕 Давай по шагам — это быстро.")
            await fm_ask(message, state)
            return
        client = fields.pop("client", None)
        recognised = {key: value for key, value in fields.items() if key in FM_KEYS}
        queue = [key for key in FM_KEYS if FM_BY_KEY[key][2] and key not in recognised]
        await state.update_data(fm_in=recognised, fm_queue=queue, fm_guided=False, fm_client=client)
        await log(owner, "finmodel_extracted", fields=len(recognised))
        if recognised:
            body = "\n".join(
                f"• {FM_LABELS[key]}: <b>{f'{v:g} шт.' if key == 'units' else money(v)}</b>"
                for key, v in recognised.items()
            )
            await wait.edit_text(f"✍️ <b>Вот что я понял</b>\n{body}", parse_mode="HTML")
        else:
            await wait.edit_text("В описании не нашёл чисел. Спрошу самое нужное.")
        await fm_ask(message, state)

    @router.message(Fm.step, F.text)
    async def fm_step_text(message: Message, state: FSMContext) -> None:
        if await guard(message, state):
            return
        data = await state.get_data()
        queue = list(data.get("fm_queue") or [])
        if not queue:
            await reset(state)
            raise SkipHandler
        key = queue[0]
        value = parse_money(message.text)
        if value is None:
            await message.answer("Не понял число 🤔 Напиши, например: 50 000, 50к или 1,2 млн.")
            return
        if key in ("price", "units") and value <= 0:
            await message.answer("Здесь нужно число больше нуля.")
            return
        values = {**(data.get("fm_in") or {}), key: value}
        await state.update_data(fm_in=values, fm_queue=queue[1:])
        await fm_ask(message, state)

    async def fm_callback(query: CallbackQuery, state: FSMContext, rest: str) -> None:
        message = query.message
        data = await state.get_data()
        numbers = data.get("fm_in") or {}
        if rest == "show":
            await fm_intro(message, state)
        elif rest == "w":
            await reset(state)
            await state.set_state(Fm.describe)
            await state.update_data(fm_in={})
            await say(
                message,
                "✍️ <b>Опиши бизнес своими словами</b>\n"
                "Например: «Продаю кофе по 250 ₽, себестоимость 80, около 600 чашек в месяц, "
                "аренда 45 тысяч». Личные данные писать не нужно.",
                _markup([_button("✖️ Отмена", "fm:x")]),
                edit=True,
            )
        elif rest == "g":
            await reset(state)
            await state.update_data(fm_in={}, fm_queue=list(FM_KEYS), fm_guided=True)
            await fm_ask(message, state, edit=True)
        elif rest == "x":
            await reset(state)
            await message.edit_text("✖️ Отменил. Финмодель можно открыть в меню снова.")
        elif rest == "s":
            queue = list(data.get("fm_queue") or [])
            if not queue or (queue[0] in ("price", "units") and queue[0] not in numbers):
                await message.answer(STALE)
                return
            await state.update_data(fm_queue=queue[1:])
            await fm_ask(message, state, edit=True)
        elif rest.startswith("r:") and rest[2:] in REGIMES and numbers.get("price"):
            regime = rest[2:]
            await state.update_data(fm_regime=regime)
            if regime == "npd" and not data.get("fm_client"):
                await say(
                    message,
                    "👥 <b>Кто твои клиенты?</b>\nОт этого зависит ставка НПД.",
                    _markup(
                        [
                            _button("Физлица, 4%", "fm:c:person"),
                            _button("Компании, 6%", "fm:c:company"),
                        ]
                    ),
                    edit=True,
                )
                return
            await fm_compute(message, state, edit=True)
        elif (
            rest in ("c:person", "c:company")
            and data.get("fm_regime") == "npd"
            and numbers.get("price")
        ):
            await state.update_data(fm_client=rest[2:])
            await fm_compute(message, state, edit=True)
        elif rest == "edit" and data.get("fm_last"):
            last = data["fm_last"]
            kept = {key: last[key] for key in FM_KEYS if last.get(key) is not None}
            await state.update_data(
                fm_in=kept,
                fm_queue=list(FM_KEYS),
                fm_guided=True,
                fm_regime=last.get("regime"),
                fm_client=last.get("client") if last.get("regime") == "npd" else None,
            )
            await fm_ask(message, state, edit=True)
        elif rest in ("cmp", "res") and data.get("fm_last"):
            try:
                result = calculate(parse_inputs(data["fm_last"]))
            except FinmodelError:
                await message.answer(STALE)
                return
            public = result.public()
            if rest == "res":
                await say(
                    message,
                    result_html(public, data["fm_last"]),
                    _markup(
                        [_button("📄 Скачать PDF", "fm:pdf")],
                        [
                            _button("🔁 Изменить цифры", "fm:edit"),
                            _button("📊 Сравнить режимы", "fm:cmp"),
                        ],
                    ),
                    edit=True,
                )
                return
            picks = [
                _button(f"Выбрать: {SHORT_REGIMES[item['regime']]}", f"fm:r:{item['regime']}")
                for item in public["regimes"]
                if item["available"] and not item["current"]
            ]
            await say(
                message,
                compare_html(public),
                _markup(*[[pick] for pick in picks], [_button("⬅️ К расчёту", "fm:res")]),
                edit=True,
            )
        elif rest == "pdf" and data.get("fm_last"):
            await fm_pdf(query, data["fm_last"])
        else:
            await message.answer(STALE)

    async def fm_pdf(query: CallbackQuery, body: dict) -> None:
        message = query.message
        try:
            result = calculate(parse_inputs(body))
        except FinmodelError:
            await message.answer(STALE)
            return
        try:
            data = await render_pdf(report_html(result))
        except PdfUnavailable:
            await message.answer("PDF сейчас недоступен 😕 Расчёт выше можно сохранить текстом.")
            return
        except Exception as error:
            _LOG.warning("finmodel_pdf_failed error=%s", type(error).__name__)
            await message.answer("Не получилось собрать PDF. Попробуй ещё раз чуть позже.")
            return
        try:
            await message.answer_document(
                BufferedInputFile(data, filename="Финмодель.pdf"),
                caption="Финансовая модель: отчёт по расчёту",
            )
        except Exception as error:
            _LOG.warning("finmodel_pdf_send_failed error=%s", type(error).__name__)
            await message.answer("Не получилось отправить файл. Попробуй ещё раз.")
            return
        await log(query.from_user.id, "pdf_sent", name="Финмодель")

    @router.callback_query(F.data.startswith("fm:"))
    async def fm_dispatch(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await fm_callback(query, state, (query.data or "")[3:])

    return router


__all__ = ["create_tools_router", "parse_money"]
