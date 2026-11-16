"""The bot's main menu and the «Мини-апп» launcher, shared by every router."""

from __future__ import annotations

from collections.abc import Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

ASK = "💬 Задать вопрос"
ROUTE = "🧭 Мой маршрут"
REMINDERS = "🔔 Напоминания"
TAXES = "🧾 Налоговый календарь"
FINMODEL = "🧮 Финмодель"
GAMES = "🎯 Тренажёры"
TOOLS = "🧰 Инструменты"
SETTINGS = "⚙️ Настройки"
PROFILE = "👤 Профиль"
AI_HELP = "🤖 Помощь ИИ"
APP = "📱 Мини-апп"
CANCEL = "✖️ Отмена"
START_HERE = ROUTE  # former name of the learning-route button
DOCS = "📝 Документы"
# Labels from older keyboards that may still be on users' screens.
LEGACY_BUTTONS = {
    "💬 Спросить": ASK,
    "🧾 Налоги": TAXES,
    "Задать вопрос": ASK,
    "🧭 С чего начать": ROUTE,
    "🧭 Маршрут": ROUTE,
    "Профиль": PROFILE,
    "Напоминания": REMINDERS,
    "Отмена": CANCEL,
}
# «Задать вопрос» is the main action, so it takes a full row.
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=ASK)],
        [KeyboardButton(text=ROUTE), KeyboardButton(text=TOOLS)],
        [KeyboardButton(text=REMINDERS), KeyboardButton(text=PROFILE)],
        [KeyboardButton(text=SETTINGS)],
        [KeyboardButton(text=AI_HELP), KeyboardButton(text=APP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Спроси про бизнес, налоги, маркетинг…",
)
ACTIVE_MENU = ReplyKeyboardMarkup(
    keyboard=[*MENU.keyboard, [KeyboardButton(text=CANCEL)]],
    resize_keyboard=True,
    is_persistent=True,
)
# Texts that always leave whatever flow is open and go to the main handlers.
MENU_TEXTS = frozenset(
    {
        ASK,
        TOOLS,
        SETTINGS,
        ROUTE,
        TAXES,
        REMINDERS,
        PROFILE,
        CANCEL,
        AI_HELP,
        FINMODEL,
        GAMES,
        APP,
        *LEGACY_BUTTONS,
    }
)


def labels(button: str) -> frozenset[str]:
    """The current label of a menu button plus its older spellings."""
    return frozenset({button, *(old for old, new in LEGACY_BUTTONS.items() if new == button)})


def leaves_flow(text: str) -> bool:
    """True when the message is a menu button or a command, not an answer inside a flow."""
    text = text.strip()
    return text in MENU_TEXTS or text.startswith("/")


def create_menu_router(webapp_url: str | None | Callable[[], str | None]) -> Router:
    router = Router(name="menu")

    @router.message(F.text == APP)
    @router.message(Command("app"))
    async def open_app(message: Message, state: FSMContext) -> None:
        await state.update_data(ai_help=False, jn_wait=None)
        url = webapp_url() if callable(webapp_url) else webapp_url
        if not url:
            await message.answer(
                "📱 Мини-приложение пока не подключено. Всё главное есть здесь, в чате.",
                reply_markup=MENU,
            )
            return
        await message.answer(
            "📱 <b>Бизнес-навигатор</b> в приложении: консультация, маршрут, налоги, "
            "финмодель, тренажёры и «Слово дня» на одном экране.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📱 Открыть Навигатор", web_app=WebAppInfo(url=url)
                        )
                    ]
                ]
            ),
        )

    return router
