"""Private Telegram dialogue through a real Dispatcher and a recording Bot session."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from telegram_fakes import Harness, assert_all_html_valid, material

from alpha_course.application.assistant import Clarification, Recommendation, Reply
from alpha_course.application.profile import (
    INDUSTRIES,
    STAGES,
    TAX_REGIMES,
    BusinessProfile,
)
from alpha_course.domain.reminders import ReminderFrequency
from alpha_course.telegram.reminders import Draft
from alpha_course.telegram.router import ACTIVE_MENU, MENU

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def h():
    harness = Harness()
    yield harness
    await harness.aclose()


def kb(h: Harness, ident: str):
    return h.assistant.knowledge.materials[ident]


def full_answer(h: Harness) -> Reply:
    free, paid = kb(h, "free1"), kb(h, "paid1")
    return Reply(
        kind="answer",
        summary="<script>&",
        steps=["Шаг <1>", "Шаг & 2"],
        personal="Тебе <подойдёт>",
        sources=[free],
        recommendations=[Recommendation(free, "по теме"), Recommendation(paid, "глубже")],
        followups=["А для ООО?", "Какие сроки?"],
    )


async def ask(h: Harness, text="как выбрать режим", reply: Reply | None = None):
    h.assistant.next_reply = reply or full_answer(h)
    calls = await h.send(text)
    edits = [c for c in calls if c.name == "EditMessageText"]
    return calls, (edits[-1] if edits else None)


# ------------------------------------------------------------------ 1. start & onboarding
async def test_new_user_gets_welcome_and_stage_step(h):
    calls = await h.send("/start")
    texts = h.texts("SendMessage")
    assert "Привет! Я «Бизнес-навигатор»" in texts[0].text
    assert texts[0].parse_mode == "HTML"
    assert texts[0].reply_markup == MENU
    step = texts[1]
    assert "1/5" in step.text
    for index in range(len(STAGES)):
        assert f"ob:stage:{index}" in step.callbacks()
    assert "ob:skip:stage" in step.callbacks()
    assert "start" in h.events.kinds
    assert_all_html_valid(calls)


async def test_onboarding_flow_saves_profile_and_shows_starter_pack(h):
    await h.send("/start")
    step = h.texts("SendMessage")[1]
    calls = await h.click("ob:stage:0", source=step)
    assert h.profiles.data[1].stage == STAGES[0]
    industry = calls[-1]
    assert industry.name == "EditMessageText" and "2/5" in industry.text
    assert f"ob:ind:{len(INDUSTRIES) - 1}" in industry.callbacks()

    calls = await h.click("ob:ind:2", source=industry)
    assert h.profiles.data[1].industry == INDUSTRIES[2]
    calls = await h.click("ob:goal:0", source=calls[-1])
    assert h.profiles.data[1].goal == "найти клиентов"
    calls = await h.click("ob:tax:1", source=calls[-1])
    calls = await h.click("ob:emp:1", source=calls[-1])
    assert h.profiles.data[1].employees is True
    assert 1 in h.profiles.onboarded
    done = calls[-1]
    assert done.name == "EditMessageText" and done.parse_mode == "HTML"
    assert "Диагностика готова" in done.text
    assert re.search(r'<a href="https://example\.test/[^"]+">', done.text)
    assert "jn:show" in done.callbacks()
    assert "onboarding_done" in h.events.kinds
    assert_all_html_valid(calls)


async def test_onboarding_skip_still_finishes(h):
    await h.send("/start")
    step = h.texts("SendMessage")[1]
    calls = await h.click("ob:skip:stage", source=step)
    assert "2/5" in calls[-1].text
    for step_name in ("industry", "goal", "tax", "emp"):
        calls = await h.click(f"ob:skip:{step_name}", source=calls[-1])
    assert 1 in h.profiles.onboarded
    assert "Диагностика готова" in calls[-1].text
    assert h.profiles.data.get(1, BusinessProfile()).is_empty()


async def test_returning_user_is_not_onboarded_again(h):
    h.profiles.onboarded.add(1)
    await h.send("/start")
    texts = h.texts("SendMessage")
    assert len(texts) == 2
    assert texts[0].reply_markup == MENU
    assert "С возвращением" in texts[1].text
    assert texts[1].callbacks() == ["jn:show", "menu:reminders"]
    assert not any(c.text and "1/5" in c.text for c in texts)


async def test_invalid_onboarding_index_is_stale(h):
    calls = await h.click("ob:stage:99")
    assert "устарела" in calls[-1].text
    assert 1 not in h.profiles.data


# ------------------------------------------------------------------ 2. grounded answer
async def test_question_shows_placeholder_then_valid_html_answer(h):
    calls, final = await ask(h)
    names = [c.name for c in calls]
    assert names.index("SendMessage") < names.index("EditMessageText")
    assert "Ищу в базе" in h.texts("SendMessage")[0].text
    assert h.assistant.reply_calls[0]["message"] == "как выбрать режим"
    assert h.assistant.reply_calls[0]["allow_clarify"] is True

    assert final.parse_mode == "HTML"
    assert "<b>Коротко</b>" in final.text
    assert "&lt;script&gt;&amp;" in final.text
    assert "1. Шаг &lt;1&gt;" in final.text
    assert '<a href="https://example.test/free1">' in final.text
    # Paid courses open only through the counted button, never an inline link.
    assert "https://example.test/paid1" not in final.text
    assert "💎 <b>" in final.text
    assert_all_html_valid(calls)


async def test_answer_buttons_followups_feedback_learn_and_paid_url(h):
    _, final = await ask(h)
    callbacks = final.callbacks()
    assert sum(c.startswith("q:") for c in callbacks) == 2
    assert "fb:up:x" in callbacks and "fb:down:x" in callbacks
    assert any(c.startswith("learn:") for c in callbacks)
    assert final.find("Напомнить изучить") is not None
    assert final.find("А для ООО?").text.startswith("❓")
    paid = final.find("Подробнее о курсе")
    assert paid.text == "💎 Подробнее о курсе"
    assert paid.url is None and paid.callback_data.startswith("po:")
    assert all(len(c.encode()) <= 64 for c in callbacks)
    calls = await h.click(paid.callback_data, source=final)
    page = [c for c in calls if c.name == "SendMessage"][-1]
    assert page.find("Открыть страницу курса").url == "https://example.test/paid1"
    assert "paid_offer_click" in h.events.kinds
    assert_all_html_valid(calls)


async def test_subscription_material_opens_purchase_url(h):
    sub = material(
        "sub", access="subscription", kind="course", purchase_url="https://example.test/plus"
    )
    h.assistant.knowledge.materials["sub"] = sub
    reply = Reply(kind="answer", summary="x", recommendations=[Recommendation(sub)])
    _, final = await ask(h, reply=reply)
    assert final.find("Напомнить изучить") is None
    calls = await h.click(final.data_for("Подробнее о курсе"), source=final)
    page = [c for c in calls if c.name == "SendMessage"][-1]
    assert page.find("Открыть страницу курса").url == "https://example.test/plus"


async def test_stale_paid_offer_button_is_reported(h):
    calls = await h.click("po:missing")
    assert "устарела" in calls[-1].text
    assert "paid_offer_click" not in h.events.kinds


# ------------------------------------------------------------------ A/B paid offer
@pytest.mark.parametrize("variant", ["A", "B"])
@pytest.mark.parametrize("status", ["answered", "unavailable"])
async def test_paid_offer_variant_controls_the_pitch(variant, status):
    h = Harness(variant=variant)
    try:
        h.profiles.data[1] = BusinessProfile(legal_form="ИП", tax_regime="УСН")
        reply = full_answer(h)
        reply.status = status
        other_paid = material("paid2", title="Другой платный курс", access="paid", kind="course")
        reply.recommendations.append(Recommendation(other_paid))
        original_recommendations = list(reply.recommendations)
        calls, final = await ask(h, reply=reply)
        assert final.text.count(kb(h, "paid1").title) == 1
        assert other_paid.title in final.text
        assert '<a href="https://example.test/free1">' in final.text
        assert final.find("Напомнить изучить") is not None
        assert reply.recommendations == original_recommendations
        shown = [p for _, kind, p in h.events.logged if kind == "answer_shown"]
        assert shown == [{"status": status, "materials": ["free1", "paid1", "paid2"]}]
        if status == "unavailable":
            assert "Не удалось собрать ответ" in final.text
        assert h.experiments.exposed == [(1, "paid1", "bot")]
        personal = "Следующий шаг для тебя" in final.text
        assert personal is (variant == "B")
        if variant == "B":
            # «Курс по финансам» mentions neither ИП nor УСН: no false personalization claim.
            assert "Подобран под твой профиль" not in final.text
            assert "прямо не отвечает" in final.text
            assert final.find("Посмотреть курс").callback_data.startswith("po:")
        else:
            assert final.find("Подробнее о курсе") is not None
        assert_all_html_valid(calls)
        await h.click(
            (final.find("Посмотреть курс") or final.find("Подробнее о курсе")).callback_data,
            source=final,
        )
        clicks = [p for _, kind, p in h.events.logged if kind == "paid_offer_click"]
        assert clicks == [{"material_id": "paid1", "variant": variant}]
    finally:
        await h.aclose()


async def test_paid_only_variant_b_has_no_empty_materials_section():
    h = Harness(variant="B")
    try:
        paid = kb(h, "paid1")
        reply = Reply(kind="answer", recommendations=[Recommendation(paid)])
        _, final = await ask(h, reply=reply)
        assert final.text.count(paid.title) == 1
        assert "Изучить подробнее" not in final.text
        assert "Близкие материалы" not in final.text
        assert final.find("Посмотреть курс") is not None
        assert reply.recommendations == [Recommendation(paid)]
    finally:
        await h.aclose()


async def test_answer_without_paid_course_is_not_an_exposure():
    h = Harness(variant="B")
    try:
        free = kb(h, "free1")
        await ask(
            h, reply=Reply(kind="answer", summary="x", recommendations=[Recommendation(free)])
        )
        assert h.experiments.exposed == []
    finally:
        await h.aclose()


async def test_rating_survey_when_due_and_score_is_logged():
    h = Harness(variant="A")
    try:
        h.experiments.survey = True
        await ask(h)
        survey = h.texts("SendMessage")[-1]
        assert "Оцени от 1 до 5" in survey.text
        assert survey.callbacks() == [f"sv:{n}" for n in range(1, 6)]
        calls = await h.click("sv:4", source=survey)
        assert "4 из 5" in calls[-1].text
        scores = [p for _, kind, p in h.events.logged if kind == "survey_score"]
        assert scores == [{"score": 4}] and "survey_asked" in h.events.kinds
    finally:
        await h.aclose()


async def test_no_survey_when_not_due():
    h = Harness(variant="A")
    try:
        await ask(h)
        assert "survey_asked" not in h.events.kinds
        assert not any("Оцени от 1 до 5" in c.text for c in h.texts("SendMessage"))
    finally:
        await h.aclose()


async def test_admin_stats_include_ab_report_and_export():
    h = Harness(admin_ids=frozenset({1}), variant="A")
    try:
        await h.send("/stats")
        text = h.texts("SendMessage")[-1].text
        assert "A/B «paid_offer_v1»" in text
        assert "данных пока мало" in text and "СИНТЕТИКА" not in text
        assert "Средняя оценка: 4.5 из 5" in text
        calls = await h.send("/export")
        document = [c for c in calls if c.name == "SendDocument"]
        assert document and document[0].method.document.filename.endswith(".csv")
    finally:
        await h.aclose()


async def test_export_is_admin_only():
    h = Harness(variant="A")
    try:
        calls = await h.send("/export")
        assert not [c for c in calls if c.name == "SendDocument"]
        assert "только администраторам" in h.texts("SendMessage")[-1].text
    finally:
        await h.aclose()


async def test_answer_without_materials_offers_ai_fallback_and_feedback(h):
    _, final = await ask(h, reply=Reply(kind="answer", status="no_evidence", summary="Нет в базе"))
    assert "Уточни вопрос: точного ответа в базе нет" in final.text
    assert set(final.callbacks()) == {"ai:x", "fb:up:x", "fb:down:x", "jn:show"}
    assert {"question_asked", "answer_shown"} <= set(h.events.kinds)


async def test_ai_fallback_button_answers_labelled_without_base(h):
    _, final = await ask(
        h,
        "как назвать кофейню",
        Reply(kind="answer", status="no_evidence", summary="Нет в базе"),
    )
    calls = await h.click("ai:x", source=final)
    assert h.assistant.general_calls == ["как назвать кофейню"]
    text = " ".join(c.text or "" for c in calls if c.name in ("EditMessageText", "SendMessage"))
    assert "Ответ ИИ" in text and "Не из базы знаний" in text and "Общий совет." in text
    assert "ai_fallback_used" in h.events.kinds


async def test_profile_updates_from_reply_are_saved_and_noted(h):
    reply = Reply(kind="answer", summary="ok", profile_updates={"industry": "общепит"})
    _, final = await ask(h, reply=reply)
    assert h.profiles.data[1].industry == "общепит"
    assert "Запомнил о бизнесе" in final.text
    assert "profile_updated" in h.events.kinds


async def test_history_and_profile_are_passed_to_next_question(h):
    h.profiles.data[1] = BusinessProfile(stage="запуск")
    await ask(h, "первый вопрос")
    await ask(h, "второй вопрос")
    second = h.assistant.reply_calls[1]
    assert second["profile"].stage == "запуск"
    assert [item["role"] for item in second["history"]] == ["user", "assistant"]


async def test_assistant_failure_gets_safe_message(h):
    async def boom(*args, **kwargs):
        raise RuntimeError("secret detail")

    h.assistant.reply = boom
    calls = await h.send("вопрос")
    assert "Что-то пошло не так" in calls[-1].text
    assert "secret" not in "".join(c.text or "" for c in calls)


async def test_overlong_message_is_rejected_without_calling_assistant(h):
    calls = await h.send("а" * 1501)
    assert "Слишком длинное" in calls[-1].text
    assert h.assistant.reply_calls == []


async def test_unknown_slash_command_shows_hint(h):
    calls = await h.send("/whatever")
    assert "Напиши вопрос" in calls[-1].text
    assert h.assistant.reply_calls == []


async def test_non_private_chats_are_ignored(h):
    from aiogram.types import Update

    update = Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": -5, "type": "group"},
                "from": {"id": 1, "is_bot": False, "first_name": "T"},
                "text": "/start",
            },
        }
    )
    await h.dispatcher.feed_update(h.bot, update)
    assert h.calls == []


# ------------------------------------------------------------------ 3. clarification
async def test_clarify_reply_offers_options_and_click_asks_with_option(h):
    clarify = Reply(
        kind="clarify",
        clarification=Clarification("Что именно с налогами?", ["Выбрать режим", "Сроки <оплаты>"]),
    )
    calls, final = await ask(h, "что с налогами?", reply=clarify)
    assert "Что именно с налогами?" in final.text
    labels = [b.text for b in final.buttons]
    assert labels == ["Выбрать режим", "Сроки <оплаты>"]
    assert "clarification_shown" in h.events.kinds
    assert_all_html_valid(calls)

    h.assistant.next_reply = Reply(kind="answer", summary="Ответ")
    calls = await h.click(final.data_for("Выбрать режим"), source=final)
    last = h.assistant.reply_calls[-1]
    assert last["message"] == "что с налогами? — Выбрать режим"
    assert last["allow_clarify"] is False
    assert "followup_clicked" in h.events.kinds
    assert "Коротко" in [c for c in calls if c.name == "EditMessageText"][-1].text


async def test_followup_button_asks_followup_text(h):
    _, final = await ask(h)
    h.assistant.next_reply = Reply(kind="answer", summary="Про ООО")
    calls = await h.click(final.data_for("А для ООО?"), source=final)
    assert h.assistant.reply_calls[-1]["message"] == "А для ООО?"
    assert h.assistant.reply_calls[-1]["allow_clarify"] is False
    assert "А для ООО?" in h.texts("SendMessage")[-1].text
    assert "Про ООО" in calls[-1].text


async def test_unknown_followup_key_is_stale(h):
    calls = await h.click("q:deadbeef00")
    assert "устарела" in calls[-1].text
    assert h.assistant.reply_calls == []


# ------------------------------------------------------------------ 4. feedback
async def test_thumbs_down_removes_feedback_and_sends_hint(h):
    _, final = await ask(h)
    calls = await h.click("fb:down:x", source=final)
    edit = next(c for c in calls if c.name == "EditMessageReplyMarkup")
    remaining = edit.callbacks()
    assert not any(c.startswith("fb:") for c in remaining)
    assert any(c.startswith("learn:") for c in remaining)
    assert "Что поправить" in calls[-1].text
    assert any(c.startswith("sm:") for c in calls[-1].callbacks())
    assert "feedback_down" in h.events.kinds


async def test_thumbs_up_removes_feedback_and_thanks(h):
    _, final = await ask(h)
    calls = await h.click("fb:up:x", source=final)
    edit = next(c for c in calls if c.name == "EditMessageReplyMarkup")
    assert not any(c.startswith("fb:") for c in edit.callbacks())
    assert "feedback_up" in h.events.kinds
    assert not h.texts("SendMessage")[-1].text.startswith("Спасибо, что сказал")


# ------------------------------------------------------------------ 5. learn reminder
async def test_learn_button_starts_reminder_draft_with_link(h):
    _, final = await ask(h)
    calls = await h.click(final.data_for("Напомнить изучить"), source=final)
    sent = [c for c in calls if c.name == "SendMessage"]
    assert "Настраиваю напоминание" in sent[0].text
    assert sent[0].reply_markup == ACTIVE_MENU
    picker = sent[-1]
    assert "часового пояса" in picker.text or "город" in picker.text
    assert not any(c.startswith("r:z:") for c in picker.callbacks())
    draft = (await h.state_data())["draft"]
    assert isinstance(draft, Draft)
    assert draft.event == "📚 Изучить: Налоги для ИП: режимы и сроки"
    assert draft.link_url == "https://example.test/free1"
    assert draft.link_title == "Налоги для ИП: режимы и сроки"


async def test_learn_with_unknown_key_is_stale(h):
    calls = await h.click("learn:nope")
    assert "устарела" in calls[-1].text
    assert "draft" not in await h.state_data() or (await h.state_data())["draft"] is None


async def test_learn_while_draft_active_asks_to_finish_first(h):
    _, final = await ask(h)
    await h.click("r:new")
    before = (await h.state_data())["draft"]
    calls = await h.click(final.data_for("Напомнить изучить"), source=final)
    assert "Сначала закончи" in calls[-1].text
    assert (await h.state_data())["draft"] is before


# ------------------------------------------------------------------ 6. profile
async def test_profile_menu_lists_fields_and_edit_buttons(h):
    h.profiles.data[1] = BusinessProfile(stage="запуск")
    calls = await h.send("👤 Профиль")
    text = calls[-1]
    assert "Профиль бизнеса" in text.text and "<b>запуск</b>" in text.text
    for expected in ("pf:describe", "pf:edit:tax_regime", "pf:edit:stage", "pf:clear"):
        assert expected in text.callbacks()
    assert_all_html_valid(calls)


async def test_profile_legacy_button_text_works(h):
    calls = await h.send("Профиль")
    assert "Профиль бизнеса" in calls[-1].text


async def test_profile_edit_choice_saves_value(h):
    shown = (await h.send("👤 Профиль"))[-1]
    edit = (await h.click("pf:edit:tax_regime", source=shown))[-1]
    assert f"pf:set:tax_regime:{len(TAX_REGIMES) - 1}" in edit.callbacks()
    assert "pf:unset:tax_regime" in edit.callbacks()
    calls = await h.click("pf:set:tax_regime:1", source=edit)
    assert h.profiles.data[1].tax_regime == "УСН «Доходы»"
    assert "✅ Сохранено" in calls[-1].text
    assert "УСН «Доходы»" in calls[-1].text


async def test_profile_employees_and_level_conversion(h):
    await h.click("pf:set:employees:0")
    await h.click("pf:set:level:1")
    profile = h.profiles.data[1]
    assert profile.employees is True and profile.level == "experienced"


async def test_profile_invalid_choice_is_stale_and_unset_clears_field(h):
    calls = await h.click("pf:set:stage:99")
    assert "устарела" in calls[-1].text
    h.profiles.data[1] = BusinessProfile(stage="запуск", industry="услуги")
    await h.click("pf:unset:stage")
    assert h.profiles.data[1].stage is None and h.profiles.data[1].industry == "услуги"


async def test_profile_clear_asks_confirmation_then_clears(h):
    h.profiles.data[1] = BusinessProfile(stage="запуск")
    ask_calls = await h.click("pf:clear")
    assert "pf:confirm" in ask_calls[-1].callbacks() and h.profiles.cleared == []
    calls = await h.click("pf:confirm")
    assert h.profiles.cleared == [1]
    assert "Сведения удалены" in calls[-1].text


async def test_profile_describe_uses_extract_profile_and_saves(h):
    calls = await h.click("pf:describe")
    assert calls[-1].reply_markup == ACTIVE_MENU
    h.assistant.extracted = {"legal_form": "ИП", "tax_regime": "УСН «Доходы»", "employees": True}
    calls = await h.send("Открываю кофейню, ИП на УСН 6%, двое сотрудников")
    assert h.assistant.extract_calls == ["Открываю кофейню, ИП на УСН 6%, двое сотрудников"]
    assert h.assistant.reply_calls == []
    assert h.profiles.data[1] == BusinessProfile(
        legal_form="ИП", tax_regime="УСН «Доходы»", employees=True
    )
    assert "Запомнил о бизнесе" in calls[-1].text and "Профиль бизнеса" in calls[-1].text
    assert (await h.state_data())["profile_field"] is None
    assert_all_html_valid(calls)


async def test_profile_describe_with_nothing_extracted_explains(h):
    await h.click("pf:describe")
    calls = await h.send("что-то неясное")
    assert "Не получилось выделить" in calls[-1].text
    assert h.profiles.data.get(1, BusinessProfile()).is_empty()


# ------------------------------------------------------------------ 7. taxes
async def test_taxes_empty_profile_asks_legal_form(h):
    calls = await h.send("🧾 Налоги")
    assert "Какая у тебя форма бизнеса" in calls[-1].text
    assert {"tx:lf:0", "tx:lf:1", "tx:lf:2", "tx:lf:3"} <= set(calls[-1].callbacks())


async def test_tax_wizard_to_calendar_and_reminders(h):
    asked = (await h.send("🧾 Налоговый календарь"))[-1]
    regime = (await h.click("tx:lf:1", source=asked))[-1]
    assert h.profiles.data[1].legal_form == "ИП"
    assert "налоговый режим" in regime.text
    employees = (await h.click("tx:rg:1", source=regime))[-1]
    assert h.profiles.data[1].tax_regime == "УСН «Доходы»"
    assert "сотрудники" in employees.text
    calls = await h.click("tx:emp:0", source=employees)
    assert h.profiles.data[1].employees is False
    calendar = calls[-1]
    assert calendar.name == "EditMessageText" and "Налоговый календарь" in calendar.text
    assert re.search(r"<b>\d\d\.\d\d</b> — ", calendar.text)
    assert "Сроки справочные" in calendar.text
    assert calendar.find("Напоминать за 3 дня до сроков") is not None
    assert "tx:reset" in calendar.callbacks()
    assert "tax_calendar_shown" in h.events.kinds
    assert_all_html_valid(calls)

    calls = await h.click("tx:remind", source=calendar)
    created = h.reminders.create_calls
    assert 1 <= len(created) <= 6
    owner, _, schedule, _ = created[0]
    assert owner == 1 and schedule.frequency == ReminderFrequency.ONCE
    assert schedule.timezone == "Europe/Moscow"
    assert schedule.first_due() > datetime.now(UTC) - timedelta(minutes=1)
    assert f"Поставил {len(created)} напоминаний" in calls[-1].text
    assert "tax_reminders_created" in h.events.kinds
    assert_all_html_valid(calls)

    again = await h.click("tx:remind", source=calendar)
    assert len(h.reminders.create_calls) == len(created)
    assert "уже стоят" in again[-1].text


async def test_self_employed_gets_npd_automatically(h):
    calls = await h.click("tx:lf:0")
    profile = h.profiles.data[1]
    assert profile.legal_form == "самозанятый" and profile.tax_regime == "НПД"
    assert "Налоговый календарь" in calls[-1].text
    assert "сотрудники" not in calls[-1].text


async def test_unknown_regime_hands_over_to_consultation(h):
    h.profiles.data[1] = BusinessProfile(legal_form="ИП")
    await h.click(f"tx:rg:{TAX_REGIMES.index('не знаю')}")
    call = h.assistant.reply_calls[-1]
    assert "налоговый режим" in call["message"] and call["allow_clarify"] is False
    assert h.profiles.data[1].tax_regime is None


async def test_tax_reset_clears_tax_fields(h):
    h.profiles.data[1] = BusinessProfile(
        legal_form="ИП", tax_regime="ПСН", employees=True, stage="запуск"
    )
    calls = await h.click("tx:reset")
    assert h.profiles.data[1] == BusinessProfile(stage="запуск")
    assert "форма бизнеса" in calls[-1].text


# ------------------------------------------------------------------ 8. reminders list
async def test_reminders_empty_state_and_new_button(h):
    calls = await h.send("🔔 Напоминания")
    assert "Напоминаний пока нет" in calls[-1].text
    assert calls[-1].callbacks() == ["r:new", "mv:show", "ns:show"]
    started = await h.click("r:new")
    assert "О чём напомнить" in started[-1].text
    assert started[-1].reply_markup == ACTIVE_MENU
    assert isinstance((await h.state_data())["draft"], Draft)
    again = await h.click("r:new")
    assert "Сначала закончи" in next(c.text for c in again if c.text)


async def test_reminders_list_escapes_event_and_offers_manage_buttons(h):
    schedule = _schedule("Сдать <отчёт> & оплатить")
    record = await h.reminders.create(1, None, schedule)
    calls = await h.send("🔔 Напоминания")
    text = calls[-1]
    assert "Твои напоминания" in text.text
    assert "Сдать &lt;отчёт&gt; &amp; оплатить" in text.text
    assert f"r:l:{record.id.hex}:1" in text.callbacks()
    assert "r:new" in text.callbacks()
    assert_all_html_valid(calls)

    card = await h.click(f"r:l:{record.id.hex}:1", source=text)
    assert any(c.startswith("r:e:") for c in card[-1].callbacks())
    assert f"r:d:{record.id.hex}:1" in card[-1].callbacks()
    assert_all_html_valid(card)

    after = await h.click(f"r:d:{record.id.hex}:1", source=card[-1])
    assert h.reminders.disabled == [(1, record.id, 1)]
    assert "Напоминаний пока нет" in after[-1].text
    assert "reminder_disabled" in h.events.kinds


async def test_stale_reminder_key_is_rejected(h):
    calls = await h.click("r:l:" + "0" * 32 + ":1")
    assert "устарела" in calls[-1].text
    calls = await h.click("r:z:" + "0" * 32 + ":3")
    assert "устарела" in calls[-1].text


async def test_unknown_callback_prefix_is_stale(h):
    calls = await h.click("zzz:1")
    assert "устарела" in calls[-1].text


# ------------------------------------------------------------------ 9. reply kinds
async def test_greeting_thanks_offtopic_replies(h):
    _, greeting = await ask(h, "привет", Reply(kind="greeting"))
    assert "Привет" in greeting.text
    _, thanks = await ask(h, "спасибо", Reply(kind="thanks"))
    assert "Рад помочь" in thanks.text
    _, off = await ask(h, "погода", Reply(kind="offtopic"))
    assert "помогаю только с вопросами бизнеса" in off.text
    assert "offtopic" in h.events.kinds


async def test_unsafe_request_shows_refusal_without_ai_fallback(h):
    reply = Reply(kind="offtopic", status="unsafe", summary="С этим не помогу: это незаконно.")
    _, final = await ask(h, "как уйти от налогов незаконно", reply)
    assert "С этим не помогу" in final.text
    assert "Ответить без базы" not in str(final.reply_markup)


async def test_profile_info_reply_acknowledges_with_note(h):
    reply = Reply(kind="profile", profile_updates={"stage": "запуск"})
    _, final = await ask(h, "у меня идея", reply)
    assert "Запомнил о бизнесе" in final.text
    assert h.profiles.data[1].stage == "запуск"


async def test_tax_kind_routes_to_tax_screen(h):
    calls, _ = await ask(h, "покажи мои сроки", Reply(kind="tax"))
    assert any(c.name == "DeleteMessage" for c in calls)
    assert "Какая у тебя форма бизнеса" in calls[-1].text


@pytest.mark.parametrize("profile", [BusinessProfile(), BusinessProfile(stage="запуск")])
async def test_plan_kind_offers_only_seven_day_route(h, profile):
    h.profiles.data[1] = profile
    calls, _ = await _ask_plan(h)
    assert h.assistant.plan_calls == []
    assert "7 дней" in calls[-1].text
    assert calls[-1].callbacks() == ["jn:show"]
    assert_all_html_valid(calls)


async def test_reminder_kind_starts_draft_from_summary(h):
    calls, _ = await ask(h, "напоминалка", Reply(kind="reminder", summary="оплатить аренду"))
    assert any(c.name == "DeleteMessage" for c in calls)
    assert (await h.state_data())["draft"].event == "оплатить аренду"


async def _ask_plan(h):
    h.assistant.next_reply = Reply(kind="plan")
    calls = await h.send("составь план обучения")
    return calls, None


# ------------------------------------------------------------------ 10. natural-language reminder
async def test_natural_language_reminder_skips_assistant(h):
    calls = await h.send("напомни завтра в 10:00 оплатить аренду")
    assert h.assistant.reply_calls == []
    sent = [c for c in calls if c.name == "SendMessage"]
    assert "Настраиваю напоминание" in sent[0].text
    assert not any(c.startswith("r:z:") for c in sent[-1].callbacks())
    draft = (await h.state_data())["draft"]
    assert "оплатить аренду" in draft.event
    assert draft.entered_time.hour == 10 and draft.pending_day


async def test_reminder_draft_text_while_active_is_not_a_question(h):
    await h.click("r:new")
    calls = await h.send("Что делать?")
    assert "Сейчас настраиваем напоминание" in calls[-1].text
    assert h.assistant.reply_calls == []


async def test_cancel_drops_draft(h):
    await h.click("r:new")
    calls = await h.send("✖️ Отмена")
    assert calls[-1].reply_markup == MENU
    assert (await h.state_data())["draft"] is None


# ------------------------------------------------------------------ 11. reminder actions
async def test_reminder_done_by_owner_edits_message_and_logs(h):
    record = await h.reminders.create(1, None, _schedule("Позвонить"))
    calls = await h.click(f"rd:done:{record.id.hex}")
    edit = next(c for c in calls if c.name == "EditMessageText")
    assert "Отмечено" in edit.text
    assert "reminder_done" in h.events.kinds


async def test_reminder_done_by_other_user_has_no_side_effects(h):
    record = await h.reminders.create(1, None, _schedule("Позвонить"))
    calls = await h.click(f"rd:done:{record.id.hex}", user=2)
    assert not [c for c in calls if c.name in ("EditMessageText", "SendMessage")]
    assert "reminder_done" not in h.events.kinds


# ------------------------------------------------------------------ misc
async def test_stats_is_admin_only():
    admin = Harness(admin_ids=frozenset({1}))
    try:
        calls = await admin.send("/stats", user=1)
        assert "Статистика за 30 дней" in calls[-1].text and "users" in calls[-1].text
        denied = await admin.send("/stats", user=2)
        assert "только администраторам" in denied[-1].text
    finally:
        await admin.aclose()


async def test_event_log_failure_does_not_break_answer(h):
    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    h.events.log = broken
    _, final = await ask(h)
    assert "Коротко" in final.text


async def test_works_without_event_store():
    bare = Harness(with_events=False)
    try:
        bare.assistant.next_reply = Reply(kind="answer", summary="ok")
        calls = await bare.send("вопрос")
        assert "Коротко" in calls[-1].text
    finally:
        await bare.aclose()


async def test_help_and_ask_buttons(h):
    assert "Спрашивай" in (await h.send("/help"))[-1].text
    assert "Напиши вопрос своими словами" in (await h.send("💬 Задать вопрос"))[-1].text


def _schedule(event: str):
    from alpha_course.application.reminders import Schedule

    local = (datetime.now(UTC) + timedelta(days=3)).replace(
        tzinfo=None, minute=0, second=0, microsecond=0
    )
    return Schedule(event, local, "Europe/Moscow", ReminderFrequency.ONCE)


async def test_ai_help_stays_active_until_explicit_switch_to_rag(h):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    h.assistant.general_help = AsyncMock(
        return_value=Reply(kind="ai_help", summary="Проверь <спрос>")
    )
    assert any(button.text == AI_HELP for row in MENU.keyboard for button in row)
    invitation = await h.send(AI_HELP)
    assert "Альфа" not in invitation[-1].text
    assert (await h.state_data())["ai_help"] is True
    calls = await h.send("Как оценить спрос?")
    assert h.assistant.general_help.await_count == 1
    assert not h.assistant.reply_calls
    assert not any(call.name == "EditMessageText" for call in calls)
    messages = [call for call in calls if call.name == "SendMessage"]
    assert len(messages) == 2
    assert messages[0].text == "🤖 ИИ думает…"
    answer = messages[1]
    assert has_pdf_button(answer.reply_markup)
    assert answer.text.startswith("🤖 <b>Ответ ИИ</b>\n\n")
    assert all("Альфа" not in (getattr(call, "text", "") or "") for call in calls)
    assert "&lt;спрос&gt;" in answer.text
    assert (await h.state_data())["ai_help"] is True
    second = await h.send("Как проверить результат?")
    assert h.assistant.general_help.await_count == 2
    assert has_pdf_button([c for c in second if c.name == "SendMessage"][-1].reply_markup)
    assert not h.assistant.reply_calls
    await h.send("💬 Задать вопрос")
    assert (await h.state_data())["ai_help"] is False
    await ask(h)
    assert len(h.assistant.reply_calls) == 1
    assert_all_html_valid(calls)


@pytest.mark.parametrize(
    "action",
    [
        "✖️ Отмена",
        "💬 Задать вопрос",
        "👤 Профиль",
        "🔔 Напоминания",
        "🧭 Мой маршрут",
        "🧾 Налоговый календарь",
        "🧰 Инструменты",
        "⚙️ Настройки",
        "/help",
        "/unknown",
    ],
)
async def test_ai_help_does_not_capture_other_menu_actions(he, action):
    h = he
    from alpha_course.telegram.router import AI_HELP

    await h.send(AI_HELP)
    await h.send(action)
    assert (await h.state_data())["ai_help"] is False


async def test_ai_help_offtopic_explicitly_rejects_unrelated_question(h):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    h.assistant.general_help = AsyncMock(
        side_effect=[Reply(kind="ai_help", summary="Проверь спрос"), Reply(kind="offtopic")]
    )
    await h.send(AI_HELP)
    await h.send("Как оценить спрос?")
    calls = await h.send("Какая погода?")
    messages = [c for c in calls if c.name == "SendMessage"]
    assert messages[0].text == "🤖 ИИ думает…"
    assert "Этот вопрос не связан с бизнесом или предпринимательством" in messages[-1].text
    assert "Альфа-Курса" not in messages[-1].text
    assert messages[-1].reply_markup == ACTIVE_MENU
    assert h.assistant.general_help.await_count == 2
    assert (await h.state_data())["ai_help"] is True
    assert not h.assistant.reply_calls


@pytest.mark.parametrize("kind", ["ai_help", "offtopic"])
async def test_ai_help_delivers_even_when_typing_fails(h, monkeypatch, kind):
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendChatAction

    from alpha_course.telegram.router import AI_HELP

    h.assistant.general_help = AsyncMock(return_value=Reply(kind=kind, summary="Проверь спрос"))
    await h.send(AI_HELP)
    original = h.session.make_request

    async def failing_typing(bot, method, timeout=None):
        if isinstance(method, SendChatAction):
            raise TelegramBadRequest(method=method, message="chat action failed")
        return await original(bot, method, timeout=timeout)

    monkeypatch.setattr(h.session, "make_request", failing_typing)
    calls = await h.send("Как оценить спрос?" if kind == "ai_help" else "Какая погода?")
    messages = [call for call in calls if call.name == "SendMessage"]
    assert len(messages) == 2
    assert messages[0].text == "🤖 ИИ думает…"
    expected = "Проверь спрос" if kind == "ai_help" else "Этот вопрос не связан с бизнесом"
    assert expected in messages[-1].text
    assert messages[-1].reply_markup == ACTIVE_MENU or has_pdf_button(messages[-1].reply_markup)
    assert not any(call.name == "EditMessageText" for call in calls)
    assert (await h.state_data())["ai_help"] is True
    h.assistant.general_help.assert_awaited_once()
    assert not h.assistant.reply_calls


async def test_ai_help_known_corpus_topic_only_calls_general_help(h, monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from alpha_course.telegram.router import AI_HELP

    h.assistant.general_help = AsyncMock(
        return_value=Reply(kind="ai_help", summary="Сравни режимы")
    )
    monkeypatch.setattr(
        h.assistant.knowledge, "search", Mock(side_effect=AssertionError("corpus access"))
    )
    await h.send(AI_HELP)
    calls = await h.send("Как выбрать налоговый режим для ИП?")
    h.assistant.general_help.assert_awaited_once()
    assert not h.assistant.reply_calls
    assert not any(call.name == "EditMessageText" for call in calls)
    answer = [c for c in calls if c.name == "SendMessage"][-1]
    assert "Сравни режимы" in answer.text
    assert "Альфа" not in answer.text


async def test_callback_exits_ai_help(h):
    from alpha_course.telegram.router import AI_HELP

    await h.send(AI_HELP)
    await h.click("unknown:action")
    assert (await h.state_data())["ai_help"] is False


@pytest.mark.parametrize("kind", ["ai_help", "offtopic"])
async def test_ai_help_progress_precedes_model_and_is_deleted_after_final(h, kind):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    await h.send(AI_HELP)
    mark = h.mark()

    async def answer(*args, **kwargs):
        messages = [c for c in h.since(mark) if c.name == "SendMessage"]
        assert [c.text for c in messages] == ["🤖 ИИ думает…"]
        assert not any(c.name == "DeleteMessage" for c in h.since(mark))
        return Reply(kind=kind, summary="Проверь спрос")

    h.assistant.general_help = AsyncMock(side_effect=answer)
    calls = await h.send("Как оценить спрос?")
    assert [c.name for c in calls] == [
        "SendMessage",
        "SendChatAction",
        "SendMessage",
        "DeleteMessage",
    ]
    assert calls[-1].method.message_id == 102
    assert (await h.state_data())["ai_help"] is True


@pytest.mark.parametrize("kind", ["ai_help", "offtopic"])
@pytest.mark.parametrize("failure", ["send", "delete"])
async def test_ai_help_progress_failure_does_not_prevent_final(h, monkeypatch, kind, failure):
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import DeleteMessage, SendMessage

    from alpha_course.telegram.router import AI_HELP

    await h.send(AI_HELP)
    h.assistant.general_help = AsyncMock(return_value=Reply(kind=kind, summary="Проверь спрос"))
    original = h.session.make_request

    async def failing_progress(bot, method, timeout=None):
        if (
            failure == "send" and isinstance(method, SendMessage) and method.text == "🤖 ИИ думает…"
        ) or (failure == "delete" and isinstance(method, DeleteMessage)):
            raise TelegramBadRequest(method=method, message="progress operation failed")
        return await original(bot, method, timeout=timeout)

    monkeypatch.setattr(h.session, "make_request", failing_progress)
    calls = await h.send("Как оценить спрос?")
    messages = [c for c in calls if c.name == "SendMessage"]
    assert len(messages) == (1 if failure == "send" else 2)
    expected = "Проверь спрос" if kind == "ai_help" else "Этот вопрос не связан с бизнесом"
    assert expected in messages[-1].text
    assert messages[-1].reply_markup == ACTIVE_MENU or has_pdf_button(messages[-1].reply_markup)
    assert not any(c.name == "EditMessageText" for c in calls)
    assert all("Альфа" not in (c.text or "") for c in calls)
    assert (await h.state_data())["ai_help"] is True
    h.assistant.general_help.assert_awaited_once()


def has_pdf_button(markup) -> bool:
    rows = getattr(markup, "inline_keyboard", None) or []
    return any(button.callback_data == "ai:pdf" for row in rows for button in row)


async def test_business_pdf_sends_document_without_corpus_access(h, monkeypatch):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    renderer = AsyncMock(return_value=b"%PDF-1.4 synthetic")
    monkeypatch.setattr("alpha_course.telegram.router.render_report", renderer)
    h.assistant.general_help = AsyncMock(
        return_value=Reply(kind="ai_help", summary="План запуска: изучить спрос.")
    )
    await h.send(AI_HELP)
    question = "Подготовь PDF с планом запуска кофейни"
    calls = await h.send(question)
    assert any(call.name == "SendMessage" and call.text == "🤖 Готовлю PDF…" for call in calls)
    assert any(call.name == "DeleteMessage" for call in calls)
    assert not any(call.name == "EditMessageText" for call in calls)
    documents = [call.method for call in calls if call.name == "SendDocument"]
    assert len(documents) == 1
    assert documents[0].document.filename == "ai_result.pdf"
    assert documents[0].document.data.startswith(b"%PDF")
    assert h.assistant.general_help.call_args.args == (question,)
    assert h.assistant.general_help.call_args.kwargs["document"] is True
    assert renderer.call_args.args[1][-1] == "План запуска: изучить спрос."
    assert not h.assistant.reply_calls
    assert (await h.state_data())["ai_help"] is True


async def test_offtopic_pdf_classifies_original_request_and_sends_no_document(h, monkeypatch):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    renderer = AsyncMock()
    monkeypatch.setattr("alpha_course.telegram.router.render_report", renderer)
    h.assistant.general_help = AsyncMock(return_value=Reply(kind="offtopic"))
    await h.send(AI_HELP)
    question = "Сделай PDF про прохождение компьютерной игры"
    calls = await h.send(question)
    assert h.assistant.general_help.call_args.args == (question,)
    assert not any(call.name == "SendDocument" for call in calls)
    renderer.assert_not_called()


async def test_pdf_export_reuses_last_successful_ai_answer(h, monkeypatch):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    renderer = AsyncMock(return_value=b"%PDF synthetic")
    monkeypatch.setattr("alpha_course.telegram.router.render_report", renderer)
    h.assistant.general_help = AsyncMock(
        return_value=Reply(kind="ai_help", summary="Проверь спрос и расходы.")
    )
    await h.send(AI_HELP)
    calls = await h.send("Как запустить кофейню?")
    assert not any(call.name == "SendDocument" for call in calls)
    calls = await h.send("Пришли это в PDF")
    assert any(call.name == "SendDocument" for call in calls)
    assert renderer.call_args.args[1][-1] == "Проверь спрос и расходы."
    assert h.assistant.general_help.await_count == 1
    assert not h.assistant.reply_calls


@pytest.mark.parametrize(
    "reply",
    [
        Reply(kind="offtopic"),
        Reply(kind="ai_help", status="no_evidence", summary="Недоступно"),
    ],
)
async def test_pdf_export_cannot_use_rejected_or_failed_answer(h, monkeypatch, reply):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    renderer = AsyncMock()
    monkeypatch.setattr("alpha_course.telegram.router.render_report", renderer)
    h.assistant.general_help = AsyncMock(return_value=reply)
    await h.send(AI_HELP)
    await h.send("Вопрос")
    calls = await h.send("Пришли это в PDF")
    assert not any(call.name == "SendDocument" for call in calls)
    assert h.assistant.general_help.await_count == 1
    renderer.assert_not_called()


async def test_ai_help_capabilities_question_is_answered_locally(h):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    h.assistant.general_help = AsyncMock()
    await h.send(AI_HELP)
    calls = await h.send("какие у тебя функции?")
    text = [c for c in calls if c.name == "SendMessage"][-1].text
    assert "Поиск в интернете" in text and "PDF" in text
    h.assistant.general_help.assert_not_awaited()


async def test_ai_help_shows_sources_note_and_keeps_history(h):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    h.assistant.general_help = AsyncMock(
        return_value=Reply(
            kind="ai_help",
            summary="Нашёл конкурентов.",
            title="Конкуренты",
            links=[{"title": "Источник <1>", "url": "https://example.org/a?x=1&y=2"}],
        )
    )
    await h.send(AI_HELP)
    calls = await h.send("Найди конкурентов")
    answer = [c for c in calls if c.name == "SendMessage"][-1]
    assert "<b>Конкуренты</b>" in answer.text
    assert 'href="https://example.org/a?x=1&amp;y=2"' in answer.text
    assert "Источник &lt;1&gt;" in answer.text and has_pdf_button(answer.reply_markup)
    await h.send("А подешевле?")
    kwargs = h.assistant.general_help.call_args.kwargs
    assert kwargs["history"] == [{"q": "Найди конкурентов", "a": "Нашёл конкурентов."}]


async def test_ai_pdf_button_sends_document_with_sources(h, monkeypatch):
    from unittest.mock import AsyncMock

    from alpha_course.telegram.router import AI_HELP

    renderer = AsyncMock(return_value=b"%PDF synthetic")
    monkeypatch.setattr("alpha_course.telegram.router.render_report", renderer)
    links = [{"title": "Источник", "url": "https://example.org/a"}]
    h.assistant.general_help = AsyncMock(
        return_value=Reply(kind="ai_help", summary="Текст.", title="Тема", links=links)
    )
    await h.send(AI_HELP)
    await h.send("Найди конкурентов")
    calls = await h.click("ai:pdf")
    assert any(c.name == "SendDocument" for c in calls)
    assert renderer.call_args.args[:2] == ("Тема", ["Текст."])
    assert renderer.call_args.kwargs["links"] == links


# ------------------------------------------------------------------ menu and the learning route
async def test_main_menu_puts_ask_first_and_the_route_right_below():
    from alpha_course.telegram.menu import ASK, DOCS, ROUTE

    assert [button.text for button in MENU.keyboard[0]] == [ASK]
    assert ROUTE in [button.text for button in MENU.keyboard[1]]
    assert all(len(row) <= 2 for row in ACTIVE_MENU.keyboard)
    labels = [button.text for row in MENU.keyboard for button in row]
    assert DOCS not in labels and len(set(labels)) == len(labels)


@pytest.mark.parametrize("entry", ["🧭 Мой маршрут", "🧭 С чего начать", "/plan", "/route"])
async def test_route_entry_points_show_seven_day_route(he, entry):
    calls = await he.send(entry)
    assert any("7 дней" in (call.text or "") for call in calls)
    assert he.assistant.plan_calls == []


async def test_answer_and_greeting_offer_the_route(h):
    _, final = await ask(h)
    assert final.data_for("Мой маршрут") == "jn:show"
    _, greeting = await ask(h, "привет", Reply(kind="greeting"))
    assert greeting.callbacks() == ["jn:show"]


@pytest.mark.parametrize("callback", ["pl:show", "pl:new", "pl:d:0", "pl:w:0", "pl:weekly:free1"])
async def test_legacy_plan_buttons_open_seven_day_route(he, callback):
    calls = await he.click(callback)
    assert any("7 дней" in (call.text or "") for call in calls)
    assert he.assistant.plan_calls == []
    assert not (await he.state_data()).get("draft")


# ------------------------------------------------------------------ route reminders in the list
@pytest.fixture
async def he():
    harness = Harness(with_engagement=True)
    yield harness
    await harness.aclose()


async def _confirm_tomorrow_at_nine(h):
    draft = (await h.state_data())["draft"]
    await h.click(f"r:q:{draft.key}:1")
    draft = (await h.state_data())["draft"]
    await h.click(f"r:s:{draft.key}:9")
    draft = (await h.state_data())["draft"]
    return await h.click(f"r:c:{draft.key}")


async def test_existing_weekly_learning_reminder_remains_manageable(he):
    record = await he.reminders.create(1, None, _schedule("📚 Шаг 1 маршрута: материал"))
    listing = (await he.send("🔔 Напоминания"))[-1]
    assert "Шаг 1 маршрута" in listing.text
    card = await he.click(f"r:l:{record.id.hex}:1", source=listing)
    after = await he.click(f"r:d:{record.id.hex}:1", source=card[-1])
    assert "Напоминание отключено" in after[-1].text


async def test_seven_day_route_is_not_in_reminders(he):
    from telegram_fakes import synthetic_journey

    he.engagement.journey = synthetic_journey()
    listing = (await he.send("🔔 Напоминания"))[-1]
    assert "Напоминаний пока нет" in listing.text
    assert "7 дней" not in listing.text and "Найти первых клиентов" not in listing.text
    assert not any(c.startswith(("jn:", "pl:")) for c in listing.callbacks())
    assert "ns:show" in listing.callbacks()
    assert_all_html_valid([listing])


async def test_open_plan_question_does_not_swallow_menu_buttons(he):
    await he.set_state_data(jn_wait="goal")
    calls = await he.send("🔔 Напоминания")
    assert "Напоминаний пока нет" in calls[-1].text
    assert (await he.state_data())["jn_wait"] is None
    assert he.profiles.data.get(1, BusinessProfile()).goal is None


async def test_reminder_draft_can_be_cancelled_and_city_changed(h):
    await h.set_state_data(reminder_timezone="Europe/Moscow")
    await h.send("напомни завтра в 10:00 оплатить аренду")
    card = h.last
    assert card.find("Подтвердить") and card.find("Другой город") and card.find("Отмена")
    calls = await h.click(card.data_for("Другой город"), source=card)
    assert "В каком ты городе" in calls[-1].text
    draft = (await h.state_data())["draft"]
    calls = [c for c in await h.click(f"r:x:{draft.key}") if c.text]
    assert "не создано" in calls[0].text and (await h.state_data())["draft"] is None
    assert calls[-1].reply_markup == MENU


async def test_journey_step_card_points_to_the_plan():
    from types import SimpleNamespace
    from uuid import uuid4

    from alpha_course.telegram.reminder_actions import management_card

    record = SimpleNamespace(
        id=uuid4(), kind="journey", status="active", event_text="📅 День 1 из 7", version=1
    )
    text, markup = management_card(record)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "план" in text and callbacks == ["jn:show", "jn:stop", "r:list"]


# ------------------------------------------------------------------ on-demand progress
async def test_progress_command_shows_weekly_numbers(h):
    from alpha_course.application.weekly_summary import WeeklyProgress

    async def weekly_progress(owner, timezone, now=None):
        assert timezone == "Europe/Moscow"
        return WeeklyProgress(active_days=3, questions=5, active_streak=3)

    h.events.weekly_progress = weekly_progress
    calls = await h.send("/progress")
    text = calls[-1].text
    assert text.startswith("🏆") and "активных дней в боте: 3" in text
    assert "Задано вопросов: 5" in text
    assert "mv:show" not in calls[-1].callbacks() and "jn:show" in calls[-1].callbacks()
    assert "progress_viewed" in h.events.kinds
    assert_all_html_valid(calls)


async def test_progress_button_from_reminders_and_missing_journal(h):
    calls = await h.click("mv:show")
    assert "недоступны" in calls[-1].text


async def test_tools_menu_groups_tools_and_clears_flows(h):
    from alpha_course.telegram.menu import FINMODEL, GAMES, TAXES, TOOLS

    await h.set_state_data(
        ai_help=True, draft=Draft(event="черновик"), profile_field="goal", jn_wait="goal"
    )
    calls = await h.send(TOOLS)
    assert calls[-1].callbacks() == ["fm:show", "tx:show", "gm:hub"]
    assert all(label in str(calls[-1].reply_markup) for label in (FINMODEL, GAMES, TAXES))
    data = await h.state_data()
    assert not any(data.get(key) for key in ("ai_help", "draft", "profile_field", "jn_wait"))


async def test_tools_submenu_opens_tax_calendar(h):
    from alpha_course.telegram.menu import TOOLS

    menu = (await h.send(TOOLS))[-1]
    calls = await h.click("tx:show", source=menu)
    assert "Налоговый календарь" in calls[-1].text
    assert any(callback.startswith("tx:lf:") for callback in calls[-1].callbacks())
