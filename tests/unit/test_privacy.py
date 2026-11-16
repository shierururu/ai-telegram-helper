"""Personal details must be masked before text goes to an external model (D-02)."""

import pytest

from alpha_course.application.privacy import contains_personal, mask_personal


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("пиши на ivan.petrov@example.com", "ivan.petrov"),
        ("Иван Петров просит договор", "Петров"),
        ("Магазин «Секретный бренд» продаёт кофе", "Секретный бренд"),
        ("звоните +7 916 123-45-67 завтра", "916"),
        ("мой телефон 8(916)1234567", "1234567"),
        ("ИНН 7707083893 у ИП", "7707083893"),
        ("номер 1234567890123 в договоре", "1234567890123"),
        ("меня зовут Иван Петров, у меня кафе", "Иван"),
        ("директор Сергей Петрович сказал", "Петрович"),
        ("мы на ул. Ленина 5", "Ленина"),
        ("работаю как ООО «Ромашка» давно", "Ромашка"),
        ("смотри https://example.com/private?id=1", "example.com"),
        ("я тут @some_user пишу", "some_user"),
    ],
)
def test_personal_details_are_removed(text: str, leaked: str) -> None:
    masked = mask_personal(text)
    assert leaked not in masked
    assert contains_personal(text)


def test_placeholders_are_neutral() -> None:
    masked = mask_personal("почта a@b.ru, телефон +7 916 123-45-67")
    assert "[email]" in masked
    assert "[номер]" in masked


@pytest.mark.parametrize(
    "text",
    [
        "Как работает УСН 6% для ИП?",
        "Когда платить НДС 22% в 2026 год",
        "Нужен ли ИП для продажи на маркетплейсе",
        "налог 15% с дохода минус расходы",
    ],
)
def test_business_terms_survive(text: str) -> None:
    assert mask_personal(text) == text
    assert not contains_personal(text)


def test_whitespace_and_control_characters_are_normalized() -> None:
    assert mask_personal("а\x00   б\t\tв") == "а б в"


def test_output_is_length_limited() -> None:
    assert len(mask_personal("слово " * 1000)) == 1200
    assert len(mask_personal("слово " * 1000, limit=50)) == 50


def test_street_name_before_comma_is_masked() -> None:
    assert "Ленина" not in mask_personal("мы на ул. Ленина, д. 5")
