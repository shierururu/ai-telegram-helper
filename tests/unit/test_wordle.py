from datetime import date

import pytest

from alpha_course.application.knowledge import KnowledgeBase, Material
from alpha_course.webapp import wordle


@pytest.mark.parametrize(
    ("guess", "answer", "expected"),
    [
        ("налог", "налог", ["correct"] * 5),
        ("наког", "налог", ["correct", "correct", "absent", "correct", "correct"]),
        ("аааан", "банан", ["absent", "correct", "absent", "correct", "correct"]),
        ("нанба", "банан", ["present", "correct", "correct", "present", "present"]),
    ],
)
def test_score_handles_repeats(guess, answer, expected):
    assert wordle.score(guess, answer) == expected


def test_normalize_folds_yo():
    assert wordle.normalize(" Ёлка ") == "елка"


def test_hint_masks_the_term():
    class M:
        title = "Аванс"
        summary = "Аванс — часть платежа, внесённая заранее. Остальное платят позже."

    hint = wordle.hint_for(M)
    assert "Аванс" not in hint and "_____" in hint


@pytest.fixture
def glossary_knowledge():
    titles = (
        "аванс",
        "налог",
        "доход",
        "расход",
        "актив",
        "пассив",
        "кредит",
        "дебет",
        "баланс",
        "аренда",
    )
    return KnowledgeBase(
        [
            Material(
                id=f"synthetic-glossary-{index}",
                kind="glossary",
                title=title,
                summary=f"Тестовое определение термина {title}.",
                url=f"https://example.test/glossary/{index}",
                access="free",
            )
            for index, title in enumerate(titles)
        ],
        [],
    )


def test_puzzle_is_stable_per_day_and_varies(glossary_knowledge):
    knowledge = glossary_knowledge
    first = wordle.puzzle_for(knowledge, date(2026, 11, 12))
    assert first is wordle.puzzle_for(knowledge, date(2026, 11, 12))
    days = {wordle.puzzle_for(knowledge, date(2026, 11, 11 + i)).id for i in range(10)}
    assert len(days) == 10
