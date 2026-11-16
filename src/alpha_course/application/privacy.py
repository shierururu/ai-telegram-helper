"""Mask personal details before any text leaves the bot (CASE restriction 07, D-02).

Instead of refusing to consult when a message contains something personal, the
bot replaces likely personal details with neutral placeholders. Only the masked
question, generic profile categories and public knowledge-base fragments are
sent to the external model.
"""

import re
import unicodedata

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[email]"),
    (re.compile(r"(?:https?://|www\.)\S+", re.I), "[ссылка]"),
    (re.compile(r"@[A-Za-z0-9_]{4,}"), "[аккаунт]"),
    # Card numbers, phones, INN, OGRN, SNILS, passport and account numbers.
    (re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)"), "[номер]"),
    (re.compile(r"\b\d{6,}\b"), "[номер]"),
    (
        re.compile(
            r"\b(?:паспорт\w*|снилс|инн|огрн\w*|кпп|бик|р/с|расч[её]тн\w+\s+сч[её]т\w*)"
            r"\s*[:№#]?\s*\S+",
            re.I,
        ),
        "[реквизит]",
    ),
    (
        re.compile(
            r"\b(?:ул\.|улиц[аеуы]|просп\.|проспект\w*|пер\.|переул\w*|шоссе|наб\.|"
            r"набережн\w*|бульвар\w*|д\.|дом|кв\.|квартир\w*|офис)\s*[\w.-]*\s*,?\s*"
            r"(?:д\.|дом)?\s*\d+[\w/-]*"
            r"(?:\s*,?\s*(?:д\.|дом|кв\.|квартир\w*|корп\.|стр\.)\s*\d+[\w/-]*)*",
            re.I,
        ),
        "[адрес]",
    ),
    (
        re.compile(
            r"\b(?:меня зовут|я\s+—|моё имя|мое имя|зовут)\s+[А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+){0,2}"
        ),
        "[имя]",
    ),
    (re.compile(r"\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+)?\b"), "[имя]"),
    (re.compile(r"[«\"][^»\"\n]{1,80}[»\"]"), "[название]"),
    (re.compile(r"\b[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)\b"), "[имя]"),
    (re.compile(r"\b(?:ООО|ОАО|ЗАО|ПАО|АО|ИП)\s+[«\"][^»\"]{1,60}[»\"]"), "[компания]"),
    (re.compile(r"\bИП\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?)?"), "ИП [имя]"),
)


def mask_personal(text: str, *, limit: int = 1200) -> str:
    """Return a cleaned, length-limited copy with personal details replaced."""
    cleaned = "".join(
        char if not unicodedata.category(char).startswith("C") or char == "\n" else " "
        for char in unicodedata.normalize("NFKC", text)
    )
    for pattern, placeholder in _RULES:
        cleaned = pattern.sub(placeholder, cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned[:limit]


def contains_personal(text: str) -> bool:
    return (
        mask_personal(text, limit=10_000)
        != re.sub(r"[ \t]+", " ", unicodedata.normalize("NFKC", text)).strip()[:10_000]
    )
