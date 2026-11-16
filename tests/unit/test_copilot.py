# ruff: noqa: E501
"""Assistant capabilities text, personalization and the web parser."""

import pytest

from alpha_course.application.copilot import (
    SKILLS,
    capabilities_text,
    is_capabilities_question,
    personalization_note,
    profile_context,
)
from alpha_course.application.profile import BusinessProfile
from alpha_course.infrastructure.yandex import YandexError, _parse_web


@pytest.mark.parametrize(
    "text", ["какие у тебя функции?", "что ты умеешь", "Что ты можешь?", "функции", "помощь"]
)
def test_capabilities_question(text):
    assert is_capabilities_question(text)


@pytest.mark.parametrize("text", ["Как открыть ИП?", "что такое УСН", "помоги составить бизнес-план"])
def test_regular_question_is_not_capabilities(text):
    assert not is_capabilities_question(text)


def test_capabilities_text_lists_many_functions_and_fits_telegram():
    total = sum(len(items) for _, items in SKILLS)
    assert total >= 30
    text = capabilities_text()
    assert len(text) <= 4096 and "Поиск в интернете" in text and "PDF" in text


def test_personalization_reads_the_whole_profile():
    profile = BusinessProfile(
        stage="запускаю", industry="услуги", legal_form="ИП", tax_regime="УСН",
        employees=False, goal="первые клиенты", level="novice",
    )
    context = profile_context(profile)
    assert set(context) == {"stage", "industry", "goal", "legal_form", "tax_regime", "employees", "level"}
    assert context["employees"] == "без сотрудников"
    assert "УСН" in personalization_note(profile)
    assert "Профиль" in personalization_note(BusinessProfile())


def test_parse_web_collects_text_and_unique_http_citations():
    data = {
        "output": [
            {"type": "reasoning"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Ответ",
                        "annotations": [
                            {"type": "url_citation", "url": "https://a.test/1", "title": "A"},
                            {"type": "url_citation", "url": "https://a.test/1", "title": "dup"},
                            {"type": "url_citation", "url": "javascript:alert(1)", "title": "x"},
                        ],
                    }
                ],
            },
        ]
    }
    text, links = _parse_web(data)
    assert text == "Ответ" and links == [{"title": "A", "url": "https://a.test/1"}]


def test_parse_web_without_message_is_an_error():
    with pytest.raises(YandexError):
        _parse_web({"output": [{"type": "reasoning"}]})
