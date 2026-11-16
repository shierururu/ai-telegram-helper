"""Telegram HTML rendering of answers, materials, plans and profiles."""

from telegram_fakes import assert_valid_html, html_errors, material

from alpha_course.application.assistant import Recommendation, Reply
from alpha_course.application.profile import BusinessProfile
from alpha_course.telegram.render import (
    answer_html,
    material_line,
    plan_html,
    profile_html,
    profile_note,
)

FREE = material("f", title="Налоги для ИП", url="https://example.test/f")
COURSE = material("c", kind="course", title="Курс клиентов", lessons=7)
PAID = material("p", kind="course", title="Платный курс", access="paid", price="4900 ₽")
SUBSCRIPTION = material(
    "s", kind="course", title="Курс+ модуль", access="subscription", price="999 ₽"
)


def test_free_material_line_has_link_and_free_badge():
    line = material_line(FREE)
    assert '<a href="https://example.test/f">Налоги для ИП</a>' in line
    assert "бесплатно" in line
    assert "5 мин" in line
    assert "💎" not in line
    assert_valid_html(line)


def test_free_course_shows_lessons():
    assert "7 уроков" in material_line(COURSE)


def test_paid_material_shows_price_not_free():
    line = material_line(PAID, "поможет с финансами")
    assert "💎" in line
    assert "4900 ₽" in line
    assert "бесплатно" not in line
    assert "поможет с финансами" in line
    assert_valid_html(line)


def test_subscription_without_price_uses_badge_label():
    assert "Подписка Курс+" in material_line(SUBSCRIPTION)
    assert "999" not in material_line(SUBSCRIPTION)


def test_numbered_line_and_escaping_of_title_url_and_why():
    nasty = material("n", title="A <b> & B", url='https://example.test/?a=1&b="2"')
    line = material_line(nasty, "why <x> & y", numbered=3)
    assert line.startswith("3. ")
    assert "&lt;b&gt; &amp; B" in line
    assert "&amp;b=&quot;2&quot;" in line
    assert "why &lt;x&gt; &amp; y" in line
    assert_valid_html(line)


def test_answered_reply_has_all_sections():
    reply = Reply(
        kind="answer",
        summary="Выбери УСН.",
        steps=["Посчитай доходы", "Подай уведомление"],
        personal="Для кофейни подойдёт.",
        sources=[FREE],
        recommendations=[Recommendation(FREE, "по теме"), Recommendation(PAID, "глубже")],
    )
    html = answer_html(reply, profile_note="📝 <i>Запомнил</i>")
    assert "<b>Коротко</b>\nВыбери УСН." in html
    assert "1. Посчитай доходы\n2. Подай уведомление" in html
    assert "<b>Для тебя</b>" in html
    assert "Изучить подробнее" in html
    assert "<i>Источник: «Налоги для ИП»</i>" in html
    assert html.endswith("📝 <i>Запомнил</i>")
    assert_valid_html(html)


def test_partial_status_uses_summary_header_and_sources():
    html = answer_html(
        Reply(kind="answer", status="partial", summary="Часть ответа", sources=[FREE])
    )
    assert "Коротко" in html and "Источник" in html


def test_no_evidence_is_honest_and_hides_sources():
    reply = Reply(
        kind="answer",
        status="no_evidence",
        summary="В базе нет сроков по ЕНС.",
        sources=[FREE],
        recommendations=[Recommendation(FREE)],
    )
    html = answer_html(reply)
    assert "Уточни вопрос: точного ответа в базе нет" in html
    assert "Близкие материалы" in html
    assert "Изучить подробнее" not in html
    assert "Источник" not in html
    assert_valid_html(html)


def test_local_status_with_excerpt_and_source():
    reply = Reply(kind="answer", status="local", excerpt="Текст <из> базы", sources=[FREE])
    html = answer_html(reply)
    assert "Нашёл в базе" in html
    assert "Текст &lt;из&gt; базы" in html
    assert "Из материала «Налоги для ИП»" in html
    assert_valid_html(html)


def test_local_status_without_excerpt_says_nothing_found():
    html = answer_html(Reply(kind="answer", status="local"))
    assert "не нашёл ответа" in html


def test_llm_text_is_escaped_everywhere():
    reply = Reply(
        kind="answer",
        summary="<script>&",
        steps=["a < b & c"],
        personal="<i>x",
        sources=[material("z", title="T<&>")],
        recommendations=[Recommendation(material("y", title="<img>"), "<why>")],
    )
    html = answer_html(reply)
    assert "<script>" not in html and "<img>" not in html
    assert "&lt;script&gt;&amp;" in html
    assert html_errors(html) == []


def test_profile_note_lists_filled_fields_and_escapes():
    note = profile_note({"stage": "запуск", "industry": "общепит"})
    assert "запуск" in note and "общепит" in note
    assert_valid_html(note)
    assert profile_note({}) == ""


def test_profile_note_formats_employees_flag():
    assert "есть" in profile_note({"employees": True})


def test_plan_html_numbers_steps():
    html = plan_html(
        "Вступление <1>", [Recommendation(FREE, "первый"), Recommendation(COURSE, "второй")]
    )
    assert "Твой маршрут" in html
    assert "Вступление &lt;1&gt;" in html
    assert "1. " in html and "2. " in html
    assert_valid_html(html)


def test_profile_html_empty_shows_hint_and_placeholders():
    html = profile_html(BusinessProfile())
    assert html.count("не указано") == 7
    assert "Профиль необязателен" in html
    assert_valid_html(html)


def test_profile_html_filled_shows_values_status_without_hint():
    profile = BusinessProfile(stage="запуск", industry="услуги", legal_form="ИП", employees=False)
    html = profile_html(profile, "✅ Сохранено")
    assert "Этап: <b>запуск</b>" in html
    assert "Сотрудники: <b>нет</b>" in html
    assert "✅ Сохранено" in html
    assert "Профиль необязателен" not in html
    assert_valid_html(html)


def test_paid_onboarding_card_has_public_description_without_model_why():
    from dataclasses import replace

    card = replace(PAID, summary="Краткая публичная польза курса")
    rendered = material_line(card)
    assert card.summary in rendered
    assert "Программа и условия — на странице курса" in rendered
    assert_valid_html(rendered)
