"""Telegram finmodel tool through a real Dispatcher with the main router behind."""

import json

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from telegram_fakes import (
    FakeAssistant,
    FakeEvents,
    FakeProfiles,
    FakeReminders,
    FakeSession,
    Harness,
    assert_all_html_valid,
)

from alpha_course.application.profile import BusinessProfile
from alpha_course.telegram import tools
from alpha_course.telegram.menu import CANCEL, FINMODEL, MENU, TAXES
from alpha_course.telegram.router import create_router
from alpha_course.telegram.tools import create_tools_router, parse_money
from alpha_course.webapp.pdf import PdfUnavailable

pytestmark = pytest.mark.asyncio


class FakeModel:
    def __init__(self, reply: str | None = None, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.calls: list[tuple] = []

    async def complete(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs))
        if self.fail:
            raise RuntimeError("boom")
        return self.reply


class ToolsHarness(Harness):
    def __init__(self, model=None) -> None:
        self.assistant = FakeAssistant()
        self.profiles = FakeProfiles()
        self.reminders = FakeReminders()
        self.events = FakeEvents()
        self.session = FakeSession()
        self.bot = Bot(token="123:test", session=self.session)
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(
            create_tools_router(self.assistant, self.profiles, self.events, model)
        )
        self.dispatcher.include_router(
            create_router(self.assistant, self.profiles, self.reminders, events=self.events)
        )
        self._update_id = 0

    async def fsm_state(self, user: int = 1):
        key = StorageKey(bot_id=self.bot.id, chat_id=user, user_id=user)
        return await self.dispatcher.storage.get_state(key)

    def documents(self):
        return [call for call in self.calls if call.name == "SendDocument"]


@pytest.fixture
def pdfs(monkeypatch):
    async def fake(markup):
        return b"%PDF-fake"

    monkeypatch.setattr(tools, "render_pdf", fake)


@pytest.fixture
async def h():
    harness = ToolsHarness()
    yield harness
    await harness.aclose()


def plain(text: str) -> str:
    return text.replace(" ", " ")


async def run_guided(h, answers, regime="usn_income"):
    calls = await h.send(FINMODEL)
    await h.click("fm:g", source=calls[-1])
    for answer in answers:
        if answer is None:
            await h.click("fm:s")
        else:
            await h.send(answer)
    return await h.click(f"fm:r:{regime}")


# ------------------------------------------------------------------ parse_money
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("50 000", 50000),
        ("50к", 50000),
        ("1,2 млн", 1_200_000),
        ("50 тыс", 50000),
        ("300 ₽", 300),
        ("1.5", 1.5),
        ("2 млн руб", 2_000_000),
        ("0", 0),
    ],
)
async def test_parse_money_accepts(text, expected):
    assert parse_money(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "много", "-5", "12abc", "1e5", "99999999999999"])
async def test_parse_money_rejects(text):
    assert parse_money(text) is None


# ------------------------------------------------------------------ finmodel
async def test_guided_flow_gives_expected_profit_and_buttons(h):
    calls = await run_guided(h, ["300", "100", "500", "40 000", None, None])
    result = h.last
    assert "51 000 ₽" in plain(result.text)
    assert "🟢" in result.text
    assert_all_html_valid(calls)
    labels = [button.text for button in result.buttons]
    assert labels == ["📄 Скачать PDF", "🔁 Изменить цифры", "📊 Сравнить режимы"]
    assert "finmodel_calculated" in h.events.kinds


async def test_regime_hint_from_profile_is_first_button(h):
    h.profiles.data[1] = BusinessProfile(tax_regime="УСН «Доходы»")
    calls = await h.send(FINMODEL)
    await h.click("fm:g", source=calls[-1])
    for answer in ("300", "100", "500"):
        await h.send(answer)
    await h.click("fm:s")
    await h.click("fm:s")
    calls = await h.click("fm:s")
    assert calls[-1].buttons[0].text.startswith("Использовать:")


async def test_npd_asks_client_type(h):
    calls = await run_guided(h, ["300", None, "500", None, None, None], regime="npd")
    assert "Кто твои клиенты" in calls[-1].text
    calls = await h.click("fm:c:company")
    assert "6%" in calls[-1].text


async def test_describe_in_words_asks_only_missing_required():
    model = FakeModel(json.dumps({"price": 250, "unit_cost": 80, "units": None, "fixed": 45000}))
    h = ToolsHarness(model)
    try:
        calls = await h.send(FINMODEL)
        await h.click("fm:w", source=calls[-1])
        calls = await h.send("Продаю кофе по 250, звонить на +7 999 123-45-67")
        assert "Продаж в месяц" in h.last.text
        assert "250" in plain(h.texts()[-2].text)
        assert "999" not in model.calls[0][1]
        await h.send("600")
        assert "Налоговый режим" in h.last.text
        calls = await h.click("fm:r:usn_income")
        assert "Прибыль в месяц" in calls[-1].text
        assert "finmodel_extracted" in h.events.kinds
    finally:
        await h.aclose()


async def test_describe_without_model_falls_back_to_steps(h):
    calls = await h.send(FINMODEL)
    await h.click("fm:w", source=calls[-1])
    await h.send("кофейня")
    assert "по шагам" in h.texts()[-2].text
    assert "Цена одной продажи" in h.last.text


async def test_bad_number_is_rejected_politely(h):
    calls = await h.send(FINMODEL)
    await h.click("fm:g", source=calls[-1])
    calls = await h.send("много")
    assert "Не понял число" in calls[-1].text


async def test_pdf_button_sends_document(h, pdfs):
    await run_guided(h, ["300", "100", "500", "40000", None, None])
    calls = await h.click("fm:pdf")
    assert len(h.documents()) == 1
    assert "pdf_sent" in h.events.kinds
    assert calls[-1].name == "SendDocument"


async def test_pdf_unavailable_is_friendly(h, monkeypatch):
    async def broken(markup):
        raise PdfUnavailable

    monkeypatch.setattr(tools, "render_pdf", broken)
    await run_guided(h, ["300", "100", "500", None, None, None])
    await h.click("fm:pdf")
    assert not h.documents()
    assert "PDF сейчас недоступен" in h.last.text


async def test_edit_numbers_keeps_previous_values_and_recomputes(h):
    await run_guided(h, ["300", "100", "500", "40000", None, None])
    calls = await h.click("fm:edit")
    assert "Оставить: 300" in " ".join(b.text for b in calls[-1].buttons).replace(" ", " ")
    await h.click("fm:s")  # price kept
    await h.click("fm:s")  # unit cost kept
    await h.send("1000")  # units
    for _ in range(3):
        calls = await h.click("fm:s")
    assert "Прибыль в месяц" in calls[-1].text
    assert "finmodel_calculated" in h.events.kinds
    assert h.events.kinds.count("finmodel_calculated") == 2


async def test_compare_regimes_and_back(h):
    await run_guided(h, ["300", "100", "500", "40000", None, None])
    calls = await h.click("fm:cmp")
    assert "Сравнение режимов" in calls[-1].text
    assert_all_html_valid(calls)
    calls = await h.click("fm:res")
    assert "Прибыль в месяц" in calls[-1].text


async def test_stale_callbacks_are_friendly(h):
    for data in ("fm:pdf", "fm:cmp", "fm:edit", "fm:s", "fm:r:npd", "fm:zzz"):
        calls = await h.click(data)
        assert "Кнопка устарела" in calls[-1].text, data


# ------------------------------------------------------------------ cancel & menu escape
async def test_cancel_text_exits_flow(h):
    calls = await h.send(FINMODEL)
    await h.click("fm:g", source=calls[-1])
    calls = await h.send(CANCEL)
    assert "отменил" in calls[-1].text
    assert await h.fsm_state() is None


async def test_menu_button_falls_through_to_main_router(h):
    calls = await h.send(FINMODEL)
    await h.click("fm:g", source=calls[-1])
    calls = await h.send(TAXES)
    assert await h.fsm_state() is None
    assert calls
    assert all("Не понял число" not in (c.text or "") for c in calls)


async def test_command_falls_through_in_finmodel_flow(h):
    calls = await h.send(FINMODEL)
    await h.click("fm:g", source=calls[-1])
    assert await h.fsm_state() is not None
    calls = await h.send("/start")
    assert await h.fsm_state() is None
    assert "Бизнес-навигатор" in calls[0].text


async def test_menu_buttons_reset_other_flows(h):
    await h.send(FINMODEL)
    data = await h.state_data()
    assert data["ai_help"] is False
    assert data["draft"] is None


async def test_documents_section_is_removed(h):
    labels = [button.text for row in MENU.keyboard for button in row]
    assert not any("Документы" in label for label in labels)
    calls = await h.send("/docs")
    assert all("Выбери документ" not in (c.text or "") for c in calls)
    assert not hasattr(tools, "Dc")


async def test_tools_submenu_opens_finmodel_and_legacy_entry_still_works(h):
    from alpha_course.telegram.menu import TOOLS

    menu = (await h.send(TOOLS))[-1]
    opened = await h.click("fm:show", source=menu)
    assert "Финмодель" in opened[-1].text and "fm:g" in opened[-1].callbacks()
    assert "Финмодель" in (await h.send(FINMODEL))[-1].text
