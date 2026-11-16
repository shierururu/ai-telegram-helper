"""Telegram «Тренажёры»: negotiation simulator and idea check through a real Dispatcher."""

from __future__ import annotations

import json
from datetime import date

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey

from alpha_course.application import games as core
from alpha_course.application.knowledge import KnowledgeBase, Material
from alpha_course.telegram.games import create_games_router
from alpha_course.telegram.menu import ACTIVE_MENU, CANCEL, GAMES, START_HERE
from alpha_course.telegram.router import create_router
from tests.unit.telegram_fakes import (
    FakeAssistant,
    FakeEvents,
    FakeProfiles,
    FakeReminders,
    FakeSession,
    Harness,
    assert_all_html_valid,
    tiny_knowledge,
)

DAY = date(2026, 11, 12)
TITLES = ("аванс", "налог", "доход", "расход", "актив", "пассив", "кредит", "дебет", "баланс")


def games_knowledge() -> KnowledgeBase:
    base = tiny_knowledge()
    glossary = [
        Material(
            id=f"gl-{index}",
            kind="glossary",
            title=title,
            summary=f"Тестовое определение термина {title}. Вторая фраза не нужна.",
            url=f"https://example.test/glossary/{index}",
            access="free",
        )
        for index, title in enumerate(TITLES)
    ]
    return KnowledgeBase([*base.materials.values(), *glossary], base.chunks)


class FakeModel:
    """Queued completions; the last one repeats."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    async def complete(self, instruction, payload, **kwargs):
        self.calls.append((instruction, json.loads(payload)))
        item = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)

    async def embed(self, text, *, query=False):  # pragma: no cover
        raise AssertionError


class GamesHarness(Harness):
    def __init__(self, model=None) -> None:
        self.assistant = FakeAssistant(games_knowledge())
        self.profiles = FakeProfiles()
        self.reminders = FakeReminders()
        self.events = FakeEvents()
        self.session = FakeSession()
        self.bot = Bot(token="123:test", session=self.session)
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(
            create_games_router(self.assistant, self.profiles, self.events, model)
        )
        self.dispatcher.include_router(
            create_router(self.assistant, self.profiles, self.reminders, events=self.events)
        )
        self._update_id = 0

    async def fsm_state(self, user: int = 1):
        key = StorageKey(bot_id=self.bot.id, chat_id=user, user_id=user)
        return await self.dispatcher.storage.get_state(key)

    def all_text(self, calls) -> str:
        return "\n".join(call.text for call in calls if call.text)


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(core.asyncio, "sleep", instant)


@pytest.fixture
async def rig():
    harness = GamesHarness()
    yield harness
    await harness.aclose()


def last(calls):
    return [call for call in calls if call.text is not None][-1]


# --------------------------------------------------------------------------- hub
async def test_hub_has_two_trainers_and_resets_other_flows(rig):
    key_user = 1
    await rig.send("/start")
    state_key = StorageKey(bot_id=rig.bot.id, chat_id=key_user, user_id=key_user)
    await rig.dispatcher.storage.update_data(state_key, {"ai_help": True, "profile_field": "city"})
    calls = await rig.send("/games")
    assert "Слово дня" not in last(calls).text and "Тренажёры" in last(calls).text
    assert [b.text for b in last(calls).buttons] == ["🤝 Переговоры", "💡 Проверка идеи"]
    data = await rig.state_data()
    assert data["ai_help"] is False and data["profile_field"] is None
    assert_all_html_valid(calls)


async def test_cancel_inside_trainer_and_slash_cancel(rig):
    await rig.send(GAMES)
    await rig.click("gm:i")
    calls = await rig.send(CANCEL)
    assert last(calls).text == "Ок, вышел из тренажёра." and await rig.fsm_state() is None
    await rig.send(GAMES)
    await rig.click("gm:i")
    calls = await rig.send("/cancel")
    assert last(calls).text == "Ок, вышел из тренажёра." and await rig.fsm_state() is None


# --------------------------------------------------------------------------- Переговоры
TURN = {"reply": "Хорошо, давайте обсудим график.", "mood": 55, "tip": None, "outcome": None}
TIP_TURN = {"reply": "Не уверен.", "mood": 50, "tip": "Назови срок.", "outcome": None}
FINISH = {
    "rubric": {
        "clarity": 4,
        "argumentation": 4,
        "empathy": 3,
        "alternatives": 3,
        "next_step": 4,
        "tone": 4,
    },
    "outcome": "deal",
    "strengths": ["Назвал срок"],
    "improvements": ["Добавь альтернативу"],
    "better_phrases": [
        {"instead": "Предлагаю оплатить половину", "say": "Предлагаю 50% до пятницы"}
    ],
}


async def start_negotiation(rig: GamesHarness, scenario="unpaid_invoice"):
    await rig.send(GAMES)
    picker = await rig.click("gm:n")
    assert len(last(picker).buttons) == len(core.SCENARIOS) + 1
    assert all(item.title in last(picker).text for item in core.SCENARIOS)
    return await rig.click(f"gm:n:{scenario}")


async def test_negotiation_full_flow_scores_and_keeps_best():
    model = FakeModel(TURN, FINISH)
    rig = GamesHarness(model)
    try:
        calls = await start_negotiation(rig)
        card = rig.all_text(calls)
        scenario = core.SCENARIO_BY_ID["unpaid_invoice"]
        assert scenario.role in card and scenario.goal in card and scenario.opening in card
        assert "Реплика 1 из 6" in card and "40/100" in card
        assert await rig.fsm_state() == "GameStates:negotiation"
        assert last(calls).reply_markup == ACTIVE_MENU

        calls = await rig.send("Предлагаю оплатить половину сегодня, звоните +7 999 123-45-67.")
        turn = last(calls)
        assert "Хорошо, давайте обсудим график." in turn.text
        assert "Настрой: 🟩🟩🟩⬜⬜ 55/100" in turn.text and "Реплика 2 из 6" in turn.text
        assert turn.find("Подсказка") is None and turn.find("Завершить") is not None
        sent = model.calls[0][1]
        assert "999" not in sent["message"] and sent["turn"] == 1

        calls = await rig.click("gm:nf")
        text = rig.all_text(calls)
        assert "Оценка: 73/100" in text
        assert "Назвал срок" in text and "Добавь альтернативу" in text
        assert "Предлагаю 50% до пятницы" in text and "Было:" in text
        assert "Что почитать" in text
        assert (await rig.state_data())["ng_best"] == {"unpaid_invoice": 73}
        assert await rig.fsm_state() is None
        assert {"negotiation_started", "negotiation_finished"} <= set(rig.events.kinds)
        assert_all_html_valid(calls)

        await rig.click("gm:n")
        picker = await rig.click("gm:n")
        assert "73/100" in last(picker).find("Клиент не платит").text
    finally:
        await rig.aclose()


async def test_negotiation_tip_button_and_forced_end_after_max_turns():
    model = FakeModel(TIP_TURN, TURN, TURN, TURN, TURN, TURN, FINISH)
    rig = GamesHarness(model)
    try:
        await start_negotiation(rig, "discount_30")
        calls = await rig.send("Давайте обсудим цену")
        tip_data = last(calls).data_for("Подсказка")
        tip = await rig.click(tip_data)
        assert "Назови срок." in last(tip).text
        for _ in range(4):
            await rig.send("Предлагаю скидку десять процентов")
        calls = await rig.send("Фиксируем десять процентов?")
        text = rig.all_text(calls)
        assert "Оценка:" in text and "Реплика 7" not in text
        assert await rig.fsm_state() is None
    finally:
        await rig.aclose()


async def test_negotiation_rejects_overlong_and_wordless_messages():
    model = FakeModel(TURN)
    rig = GamesHarness(model)
    try:
        await start_negotiation(rig)
        calls = await rig.send("а" * (core.MAX_MESSAGE + 1))
        assert "слишком длинная" in last(calls).text
        calls = await rig.send("12345 !!!")
        assert "словами" in last(calls).text
        assert model.calls == []
        assert await rig.fsm_state() == "GameStates:negotiation"
    finally:
        await rig.aclose()


async def test_negotiation_model_unavailable_offers_retry():
    rig = GamesHarness(None)
    try:
        await start_negotiation(rig)
        calls = await rig.send("Здравствуйте, давайте обсудим график платежей")
        assert "Собеседник сейчас недоступен, попробуй чуть позже" in last(calls).text
        assert last(calls).data_for("Повторить") == "gm:nr"
        assert (await rig.state_data())["ng_busy"] is False
        assert await rig.fsm_state() == "GameStates:negotiation"
    finally:
        await rig.aclose()


async def test_negotiation_error_is_friendly_and_retry_resends_the_same_message():
    model = FakeModel(RuntimeError("boom"), TURN)
    rig = GamesHarness(model)
    try:
        await start_negotiation(rig)
        calls = await rig.send("Здравствуйте, давайте обсудим график платежей")
        text = rig.all_text(calls)
        assert "Traceback" not in text and "boom" not in text
    finally:
        await rig.aclose()


async def test_negotiation_finish_without_turns_and_menu_escape():
    model = FakeModel(TURN, FINISH)
    rig = GamesHarness(model)
    try:
        await start_negotiation(rig)
        calls = await rig.send("Расскажи про налоги")
        assert "Сначала" not in rig.all_text(calls)
        await rig.send(START_HERE)
        assert await rig.fsm_state() is None
        stale = await rig.click("gm:nf")
        assert "Кнопка устарела" in last(stale).text
    finally:
        await rig.aclose()


# --------------------------------------------------------------------------- Проверка идеи
def idea_json(**changes):
    keys = ("customer", "demand", "economics", "legal", "permits", "risks", "test30")
    data = {
        "is_business": True,
        "summary": "Идея понятная, но спрос не проверен.",
        "checklist": [
            {
                "key": key,
                "status": "ok" if key == "customer" else ("risk" if key == "risks" else "unknown"),
                "why": f"Пояснение {key}",
                "source": 1 if key == "legal" else None,
                "material_id": None,
            }
            for key in keys
        ],
        "risks": ["Мало клиентов"],
        "questions": ["Кто твой клиент?", "Сколько стоит чашка?"],
        "needs": {
            "documents": [{"text": "Регистрация ИП", "source": 1}],
            "taxes": [],
            "permits": [],
        },
        "next_steps": ["Опроси десять знакомых"],
        "materials": ["free1"],
    }
    data.update(changes)
    return data


async def ask_idea(rig: GamesHarness):
    await rig.send(GAMES)
    return await rig.click("gm:i")


async def test_idea_check_happy_path_and_clarification_rerun():
    model = FakeModel(idea_json(), idea_json(summary="Теперь лучше."))
    rig = GamesHarness(model)
    try:
        prompt = await ask_idea(rig)
        assert "Опиши бизнес-идею" in last(prompt).text
        assert await rig.fsm_state() == "GameStates:idea_text"

        calls = await rig.send("Хочу открыть кофейню у дома, телефон +7 999 123-45-67")
        assert any("Проверяю по базе знаний" in (c.text or "") for c in calls)
        text = rig.all_text(calls)
        assert "Идея понятная" in text and "Целевой клиент" in text
        assert "✅" in text and "⚠️" in text and "❌" in text
        assert "Регистрация ИП" in text and "Мало клиентов" in text
        assert "Опроси десять знакомых" in text
        assert "https://example.test/free1" in text
        assert "999" not in json.dumps(model.calls[0][1], ensure_ascii=False)
        assert model.calls[0][1]["answers"] == []
        final_msg = last(calls)
        labels = [b.text for b in final_msg.buttons]
        assert any("План на 7 дней" in label for label in labels)
        assert any("Другая идея" in label for label in labels)
        assert any("К тренажёрам" in label for label in labels)
        assert "idea_checked" in rig.events.kinds
        assert await rig.fsm_state() is None
        assert_all_html_valid(calls)

        ask = await rig.click("gm:iq:1")
        assert "Сколько стоит чашка?" in last(ask).text
        assert await rig.fsm_state() == "GameStates:idea_answer"
        calls = await rig.send("Двести рублей за чашку")
        payload = model.calls[1][1]
        assert payload["answers"] == [
            {"question": "Сколько стоит чашка?", "answer": "Двести рублей за чашку"}
        ]
        assert "Теперь лучше." in rig.all_text(calls)

        plan = await rig.click("gm:ip")
        assert "План на 7 дней" in last(plan).text
        assert "Запустить: Хочу открыть кофейню" in last(plan).text
        assert last(plan).data_for("Собрать план") == "jn:goal"
        assert (await rig.state_data())["jn_goal"].startswith("Запустить: Хочу открыть кофейню")
        assert_all_html_valid(plan)
    finally:
        await rig.aclose()


async def test_idea_not_business_and_too_short():
    model = FakeModel(idea_json(is_business=False))
    rig = GamesHarness(model)
    try:
        await ask_idea(rig)
        calls = await rig.send("кот")
        assert "хотя бы одним-двумя предложениями" in last(calls).text and model.calls == []
        calls = await rig.send("Просто болтаю про погоду сегодня")
        assert "не похоже на бизнес-идею" in rig.all_text(calls)
        assert await rig.fsm_state() == "GameStates:idea_text"
    finally:
        await rig.aclose()


async def test_idea_unavailable_offers_retry_and_menu_escape():
    rig = GamesHarness(None)
    try:
        await ask_idea(rig)
        calls = await rig.send("Хочу открыть кофейню у дома")
        assert "недоступна" in rig.all_text(calls)
        assert last(calls).data_for("Повторить") == "gm:ir"
        await ask_idea(rig)
        await rig.send(GAMES)
        assert await rig.fsm_state() is None
    finally:
        await rig.aclose()


async def test_idea_menu_button_falls_through_to_main_router():
    rig = GamesHarness(FakeModel(idea_json()))
    try:
        await ask_idea(rig)
        calls = await rig.send(START_HERE)
        assert await rig.fsm_state() is None
        assert calls and "предложениями" not in rig.all_text(calls)
    finally:
        await rig.aclose()


# --------------------------------------------------------------------------- stale buttons
@pytest.mark.parametrize(
    "data", ["gm:nt", "gm:nf", "gm:nr", "gm:ir", "gm:iq:0", "gm:ip", "gm:zz", "gm:n:nope"]
)
async def test_stale_callbacks_are_tolerated(rig, data):
    calls = await rig.click(data)
    assert "Кнопка устарела. Открой «🎯 Тренажёры» заново." in rig.all_text(calls)


async def test_foreign_callbacks_pass_through(rig):
    calls = await rig.click("zz:unknown")
    assert "Кнопка устарела. Открой «🎯 Тренажёры» заново." not in rig.all_text(calls)


async def test_tools_submenu_opens_games_and_legacy_entry_still_works(rig):
    from alpha_course.telegram.menu import TOOLS

    menu = (await rig.send(TOOLS))[-1]
    opened = await rig.click("gm:hub", source=menu)
    assert "Тренажёры" in last(opened).text
    assert {"gm:n", "gm:i"} == set(last(opened).callbacks())
    assert "Тренажёры" in last(await rig.send(GAMES)).text
