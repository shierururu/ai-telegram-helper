"""Paid course offers: selection by question and profile, public-only explanation, D-04 links.

Synthetic catalog in the real corpus shape (tests/unit/telegram_fakes.tiny_knowledge plus
course cards like the organizer export). An optional check over the prepared corpus runs
only when data/derived/corpus.json exists locally; it is never committed (D-01).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from alpha_course.application import assistant as assistant_module
from alpha_course.application.assistant import Assistant, Recommendation, Reply
from alpha_course.application.experiments import offer_pitch
from alpha_course.application.knowledge import Chunk, KnowledgeBase, Material
from alpha_course.application.offers import (
    FIT_GENERAL,
    FIT_PROFILE,
    FIT_QUESTION,
    NOTE,
    choose_course,
)
from alpha_course.application.profile import BusinessProfile
from tests.unit.telegram_fakes import Harness, assert_all_html_valid, material, tiny_knowledge

SECRET = "СЕКРЕТНЫЙ УРОК 3: пошаговая схема"

USN = material(
    "usn",
    kind="course",
    title="УСН на маркетплейсах: особенности налогового учёта",
    summary=(
        "Узнайте, как вести учёт продаж на маркетплейсах и не допускать ошибок при расчёте налогов"
    ),
    url="https://kurs.example.test/courses/usn-marketplace/?utm_source=x",
    access="paid",
    price="3990 ₽",
    topics=("Финансы",),
    lessons=7,
    url_observed=True,
    body_allowed=False,
)
TEAM = material(
    "team",
    kind="course",
    title="Первые сотрудники: найм и адаптация",
    summary="Как нанять первых сотрудников и выстроить работу команды",
    url="https://kurs.example.test/courses/team/",
    access="subscription",
    purchase_url="https://kurs.example.test/subscriptions/plus/",
    topics=("Управление",),
    url_observed=True,
    body_allowed=False,
)
CAFE = material(
    "cafe",
    kind="course",
    title="Продвижение заведения",
    summary="Маркетинг для кафе и ресторанов: как привлечь гостей",
    url="https://kurs.example.test/courses/cafe/",
    access="paid",
    price="4900 ₽",
    topics=("Маркетинг",),
    industries=("HoReCa",),
    outcomes=("Составите план продвижения на сезон",),
    url_observed=True,
    body_allowed=False,
)


def catalog(*extra: Material) -> KnowledgeBase:
    """tiny_knowledge() plus course cards; paid lesson text is offered to the index on purpose."""
    base = tiny_knowledge()
    items = [
        replace(item, url_observed=True) if item.id == "paid1" else item
        for item in base.materials.values()
    ]
    chunks = [*base.chunks, Chunk("secret", "usn", "Урок 3", SECRET)]
    return KnowledgeBase([*items, USN, TEAM, CAFE, *extra], chunks)


@pytest.fixture
def kb() -> KnowledgeBase:
    return catalog()


PROFILES = {
    "empty": BusinessProfile(),
    "marketplace_usn": BusinessProfile(
        stage="работает до года",
        industry="онлайн-торговля и маркетплейсы",
        legal_form="ИП",
        tax_regime="УСН «Доходы»",
        level="novice",
    ),
    "cafe_team": BusinessProfile(
        stage="развитие", industry="общепит", legal_form="ООО", employees=True
    ),
    "self_employed": BusinessProfile(stage="идея", legal_form="самозанятый", tax_regime="НПД"),
    "services_team": BusinessProfile(industry="услуги", employees=True, goal="нанять сотрудников"),
}


# ------------------------------------------------------------------ selection
def test_question_topic_wins_and_is_labelled_as_related(kb):
    offer = choose_course(kb, "как вести учёт продаж на маркетплейсе", PROFILES["empty"])
    assert offer.material.id == "usn" and offer.fit == FIT_QUESTION
    assert offer.reason == "Связан с темой твоего вопроса."


@pytest.mark.parametrize(
    ("name", "expected", "fact"),
    [
        ("marketplace_usn", "usn", "УСН «Доходы»"),
        ("cafe_team", "cafe", "сфера «общепит»"),
        ("services_team", "team", "есть сотрудники"),
    ],
)
def test_profile_decides_when_question_is_neutral(kb, name, expected, fact):
    offer = choose_course(kb, "с чего начать", PROFILES[name])
    assert offer.material.id == expected
    assert offer.fit == FIT_PROFILE
    assert fact in offer.matched and fact in offer.reason


def test_industry_hint_selects_the_sector_course():
    kb = KnowledgeBase([USN, CAFE], [])
    offer = choose_course(kb, "с чего начать", BusinessProfile(industry="общепит"))
    assert offer.material.id == "cafe" and "сфера «общепит»" in offer.matched


def test_profile_changes_the_choice_for_the_same_question(kb):
    question = "как увеличить выручку"
    picks = {name: choose_course(kb, question, p).material.id for name, p in PROFILES.items()}
    assert picks["marketplace_usn"] == "usn"
    assert picks["cafe_team"] == "cafe"
    assert picks["services_team"] == "team"


def test_no_match_falls_back_honestly_and_ai_surface_gets_none(kb):
    offer = choose_course(kb, "погода и отпуск", BusinessProfile())
    assert offer is not None and offer.fit == FIT_GENERAL and not offer.matched
    assert "прямо не отвечает" in offer.why
    assert "Подобран" not in offer.why and "Связан" not in offer.why
    assert choose_course(kb, "погода и отпуск", require_relevance=True) is None


def test_selection_is_deterministic(kb):
    runs = {
        choose_course(kb, "с чего начать", PROFILES["self_employed"]).material.id for _ in range(5)
    }
    assert len(runs) == 1


def test_excluded_courses_are_a_preference_not_a_ban(kb):
    first = choose_course(kb, "учёт на маркетплейсе")
    second = choose_course(kb, "учёт на маркетплейсе", exclude=frozenset({first.material.id}))
    assert second.material.id != first.material.id
    every = frozenset(m.id for m in kb.materials.values())
    assert choose_course(kb, "учёт", exclude=every) is not None


def test_empty_catalog_never_invents_a_course():
    assert choose_course(KnowledgeBase([], []), "налоги", PROFILES["marketplace_usn"]) is None


# ------------------------------------------------------------------ D-04: links
@pytest.mark.parametrize(
    "changes",
    [
        {"url": "javascript:alert(1)"},
        {"url": "/courses/relative"},
        {"url": "https://user:pw@kurs.example.test/x"},
        {"url_observed": False},
        {"purchase_url": "javascript:alert(1)"},
        {"kind": "article"},
    ],
)
def test_ineligible_cards_are_never_offered(changes):
    bad = replace(USN, **changes)
    kb = KnowledgeBase([bad], [])
    assert choose_course(kb, "учёт на маркетплейсе") is None


def test_offered_links_are_exactly_catalog_links(kb):
    catalog_links = {m.url for m in kb.materials.values()} | {
        m.purchase_url for m in kb.materials.values() if m.purchase_url
    }
    for profile in PROFILES.values():
        for question in ("налоги", "найм", "реклама кафе", "что угодно"):
            offer = choose_course(kb, question, profile)
            assert offer.material.url in catalog_links
            assert offer.material.url == kb.materials[offer.material.id].url
            assert "http" not in offer.why  # the explanation never carries a link


# ------------------------------------------------------------------ D-04: explanation
def test_explanation_is_public_card_only(kb):
    offer = choose_course(kb, "реклама кафе", PROFILES["cafe_team"])
    assert offer.material.id == "cafe"
    assert offer.why.startswith("О курсе: Маркетинг для кафе и ресторанов")
    assert "Результат по карточке: Составите план продвижения на сезон" in offer.why
    assert offer.why.endswith(NOTE)
    assert len(offer.why) < 420
    usn = choose_course(kb, "налоги на маркетплейсе")
    assert SECRET not in usn.why
    # The closed lesson never entered the index either.
    assert all(chunk.material_id != "usn" for chunk in kb.chunks)


def test_card_without_summary_still_says_what_it_is_about():
    bare = replace(USN, summary="", outcomes=())
    offer = choose_course(KnowledgeBase([bare], []), "налоги")
    assert offer.about == "Тема курса: Финансы"


def test_pitch_states_only_real_matches(kb):
    offer = choose_course(kb, "с чего начать", PROFILES["marketplace_usn"])
    pitch = offer_pitch(offer.material, PROFILES["marketplace_usn"], offer)
    assert pitch["fit"] == offer.reason
    assert "сфера «онлайн-торговля и маркетплейсы»" in pitch["fit"]
    assert SECRET not in json.dumps(pitch, ensure_ascii=False)


# ------------------------------------------------------------------ consultation
class ScriptedModel:
    """Answers COMPOSE with a model that tries to smuggle paid content and a fake link."""

    def __init__(self, materials: list[dict]) -> None:
        self.materials = materials
        self.calls: list[tuple[str, str]] = []

    async def complete(self, instruction, payload, **kwargs) -> str:
        self.calls.append((instruction, payload))
        if instruction == assistant_module.UNDERSTAND:
            return json.dumps({"intent": "question", "search_query": "налоги ИП"})
        text = "Налоги ИП режимы УСН сроки уплаты налогов начать бизнес идея запуск"
        return json.dumps(
            {
                "status": "answered",
                "summary": text,
                "steps": [],
                "evidence": [{"source": 1, "quote": text}],
                "sources": [1],
                "materials": self.materials,
                "followups": [],
            },
            ensure_ascii=False,
        )

    async def embed(self, text, *, query=False):  # pragma: no cover - not semantic-ready
        raise AssertionError


@pytest.mark.parametrize("name", list(PROFILES))
@pytest.mark.parametrize("with_model", [False, True])
async def test_every_answer_carries_a_personal_paid_course(kb, name, with_model):
    profile = PROFILES[name]
    model = (
        ScriptedModel(
            [
                {"id": "free1", "why": "Про сроки"},
                {"id": "usn", "why": SECRET + " https://invented.test/buy"},
            ]
        )
        if with_model
        else None
    )
    reply = await Assistant(kb, model).answer("налоги ИП", "какие налоги платит ИП", profile)
    paid = [rec for rec in reply.recommendations if rec.material.is_paid]
    assert paid, "a paid course must be offered"
    assert len(reply.recommendations) <= 3
    offer = reply.course_offer
    assert offer is not None and any(rec.material.id == offer.material.id for rec in paid)
    expected = choose_course(kb, "налоги ИП какие налоги платит ИП", profile)
    assert offer.material.id == expected.material.id
    for rec in paid:
        assert rec.why.endswith(NOTE)
        assert SECRET not in rec.why and "invented" not in rec.why
    if model is not None:
        sent = json.dumps([payload for _, payload in model.calls], ensure_ascii=False)
        assert SECRET not in sent
        assert "kurs.example.test" not in sent  # links are never sent to the model


async def test_no_evidence_answer_still_offers_course_without_claiming_an_answer(kb):
    reply = await Assistant(kb).answer("погода", "какая завтра погода", BusinessProfile())
    assert reply.status == "no_evidence"
    offer = reply.course_offer
    assert offer is not None and offer.fit == FIT_GENERAL
    assert any(rec.material.id == offer.material.id for rec in reply.recommendations)
    assert all(material.access == "free" for material in reply.sources)


async def test_learning_route_has_one_consistent_paid_course(kb):
    for profile in PROFILES.values():
        _, steps = await Assistant(kb).learning_plan(profile)
        paid = [step for step in steps if step.material.is_paid]
        assert paid and len(steps) <= 5
        expected = choose_course(kb, profile.goal or "", profile)
        assert expected.material.id in {step.material.id for step in paid}
        assert all(step.why.endswith(NOTE) and SECRET not in step.why for step in paid)


async def test_seven_day_journey_stays_free(kb):
    route = list(kb.materials.values())
    days = await Assistant(kb).journey_steps("налоги", PROFILES["marketplace_usn"], route)
    assert days and all(not kb.materials[day["material_id"]].is_paid for day in days)


# ------------------------------------------------------------------ AI assistant
class GeneralModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, instruction, payload, **kwargs) -> str:
        self.calls.append(payload)
        if instruction == assistant_module.GENERAL_HELP_SCOPE:
            return '{"scope":"business"}'
        return json.dumps({"kind": "answer", "title": "Налоги", "text": "Сравни режимы."})


async def test_ai_help_offers_only_a_related_course_chosen_locally(kb):
    model = GeneralModel()
    reply = await Assistant(kb, model).general_help(
        "как вести учёт продаж на маркетплейсе", profile=PROFILES["empty"]
    )
    assert reply.course_offer and reply.course_offer.material.id == "usn"
    assert reply.course_offer.relevant
    sent = " ".join(model.calls)
    assert USN.title not in sent and "kurs.example.test" not in sent

    unrelated = await Assistant(kb, GeneralModel()).general_help(
        "придумай слоган", profile=BusinessProfile()
    )
    assert unrelated.course_offer is None
    by_profile = await Assistant(kb, GeneralModel()).general_help(
        "придумай слоган", profile=PROFILES["marketplace_usn"]
    )
    assert by_profile.course_offer and by_profile.course_offer.fit == FIT_PROFILE
    document = await Assistant(kb, GeneralModel()).general_help(
        "учёт на маркетплейсе в PDF", profile=PROFILES["marketplace_usn"], document=True
    )
    assert document.course_offer is None  # the PDF is the user's own document


@pytest.mark.parametrize("variant", ["A", "B"])
async def test_ai_help_message_shows_course_through_counted_button(variant):
    from alpha_course.telegram.router import AI_HELP

    h = Harness(variant=variant)
    try:
        # The router keeps the knowledge object it was built with: add the card to it.
        h.assistant.knowledge.materials["usn"] = USN
        offer = choose_course(catalog(), "учёт на маркетплейсе")
        h.assistant.general_reply = Reply(
            kind="ai_help", summary="Сравни режимы.", course_offer=offer
        )
        await h.send(AI_HELP)
        calls = await h.send("Как вести учёт на маркетплейсе?")
        answer = [c for c in calls if c.name == "SendMessage"][-1]
        assert "Сравни режимы." in answer.text
        assert USN.title in answer.text
        assert "kurs.example.test" not in answer.text
        assert answer.find("Сохранить в PDF") is not None
        button = answer.find("Посмотреть курс") or answer.find("Подробнее о курсе")
        assert button.callback_data.startswith("po:") and button.url is None
        assert ("Следующий шаг для тебя" in answer.text) is (variant == "B")
        assert h.experiments.exposed == [(1, "usn", "bot")]
        assert_all_html_valid(calls)
        page = [
            c for c in await h.click(button.callback_data, source=answer) if c.name == "SendMessage"
        ][-1]
        assert page.find("Открыть страницу курса").url == USN.url
    finally:
        await h.aclose()


async def test_ai_help_without_offer_has_no_course_block():
    from alpha_course.telegram.router import AI_HELP

    h = Harness(variant="A")
    try:
        h.assistant.general_reply = Reply(kind="ai_help", summary="Сравни режимы.")
        await h.send(AI_HELP)
        calls = await h.send("придумай слоган")
        answer = [c for c in calls if c.name == "SendMessage"][-1]
        assert "💎" not in answer.text and answer.find("курс") is None
        assert h.experiments.exposed == []
    finally:
        await h.aclose()


def test_finalize_rewrites_every_paid_row_even_a_second_course(kb):
    offer = choose_course(kb, "налоги", PROFILES["marketplace_usn"])
    other = kb.materials["team"]
    reply = Reply(
        kind="answer",
        recommendations=[
            Recommendation(other, SECRET),
            Recommendation(kb.materials["free1"], "Про налоги"),
        ],
    )
    result = Assistant._finalize(reply, offer, PROFILES["marketplace_usn"], topic="налоги")
    assert [rec.material.id for rec in result.recommendations] == ["team", "free1", "usn"]
    assert all(SECRET not in rec.why for rec in result.recommendations)
    assert result.recommendations[1].why == "Про налоги"


# ------------------------------------------------------------------ optional real corpus
def _corpus_path() -> Path:
    if os.environ.get("ALFA_CORPUS_PATH"):
        return Path(os.environ["ALFA_CORPUS_PATH"])
    # The checkout root, or an enclosing checkout when tests run from a git worktree.
    for parent in Path(__file__).resolve().parents[2:]:
        candidate = parent / "data" / "derived" / "corpus.json"
        if candidate.exists():
            return candidate
    return Path("data/derived/corpus.json")


CORPUS = _corpus_path()


@pytest.fixture(scope="module")
def real_kb() -> KnowledgeBase:
    if not CORPUS.exists():
        pytest.skip("prepared corpus is not available locally (D-01: never committed)")
    return KnowledgeBase.from_path(CORPUS)


REAL_QUESTIONS = (
    "как платить налоги на УСН",
    "как продавать на маркетплейсах",
    "как нанять первого сотрудника",
    "где найти первых клиентов для кофейни",
    "как открыть ИП",
)


async def test_real_corpus_every_profile_and_question_gets_a_safe_offer(real_kb):
    eligible = [m for m in real_kb.materials.values() if m.recommendable_paid_course]
    assert eligible, "the prepared corpus has at least one eligible paid course"
    paid_ids = {m.id for m in real_kb.materials.values() if m.is_paid}
    assert all(chunk.material_id not in paid_ids for chunk in real_kb.chunks)
    assistant = Assistant(real_kb)
    for profile in PROFILES.values():
        for question in REAL_QUESTIONS:
            reply = await assistant.answer(question, question, profile)
            offer = reply.course_offer
            assert offer is not None and offer.material.recommendable_paid_course
            assert any(rec.material.id == offer.material.id for rec in reply.recommendations)
            assert offer.material.url == real_kb.materials[offer.material.id].url
            assert all(material.access == "free" for material in reply.sources)
            rec = next(r for r in reply.recommendations if r.material.id == offer.material.id)
            assert rec.why.endswith(NOTE) and "http" not in rec.why
        _, steps = await assistant.learning_plan(profile)
        assert any(step.material.is_paid for step in steps)
    marketplace = choose_course(real_kb, "как продавать", PROFILES["marketplace_usn"])
    assert marketplace.fit != FIT_GENERAL
