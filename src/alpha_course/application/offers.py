"""Paid course offers: which course to suggest and an honest public explanation.

T2-05, D-04 and the USER decision of 13 November 2026: every consultation answer and
learning route offers at least one eligible paid course when the catalog has one.

* Eligibility comes from ``Material.recommendable_paid_course`` — a course card with an
  access class paid/subscription and a link observed in the organizer catalog. URLs
  are never built or guessed.
* Selection is deterministic over the whole eligible catalog: question terms and the
  confirmed profile (goal, industry, legal form, tax regime, employees, stage, level).
* The explanation uses public card metadata only (title, annotation, topics, public
  outcomes) and the profile fields that really matched. It never contains lesson text,
  program details or model-generated claims.
* When nothing matches, the best eligible course is still offered for consultation and
  the route, but labelled honestly: it is not presented as an answer to the question.
  Surfaces that require relevance (the generic AI assistant) get no offer in that case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from alpha_course.application.knowledge import INDUSTRY_HINTS, KnowledgeBase, Material, tokens
from alpha_course.application.profile import LEVEL_LABELS, BusinessProfile

FIT_QUESTION = "question"
FIT_PROFILE = "profile"
FIT_GENERAL = "general"

NOTE = "Программа и условия — на странице курса."

# Synonyms that let a profile value meet the wording of a public card.
TAX_TERMS = {
    "УСН": "усн упрощенка упрощенная",
    "АУСН": "аусн автоматизированная упрощенная",
    "НПД": "нпд самозанятый самозанятых",
    "ПСН": "псн патент патентная",
    "ОСНО": "осно ндс",
}
LEGAL_TERMS = {
    "ИП": "ип индивидуальный предприниматель",
    "ООО": "ооо общество юрлицо",
    "самозанятый": "самозанятый самозанятых нпд",
}
STAGE_TERMS = {
    "идея": "идея бизнес-план ниша",
    "запуск": "открыть открытие регистрация запуск",
    "работает до года": "первые клиенты",
    "развитие": "масштабирование рост развитие",
}
EMPLOYEE_TERMS = "сотрудник сотрудники персонал найм нанять команда зарплата кадровый"
# Words that say nothing about a course topic: they must not make a course «on topic».
_NOISE = set(
    tokens(
        "бизнес бизнеса предприниматель предпринимателям курс курсы узнать узнайте научиться "
        "малый малого вопрос нужно делать сделать правильно лучше какой хочу"
    )
)


@dataclass(frozen=True)
class CourseOffer:
    material: Material
    fit: str  # question | profile | general
    matched: tuple[str, ...] = ()  # profile facts that really occur on the public card

    @property
    def relevant(self) -> bool:
        return self.fit != FIT_GENERAL

    @property
    def about(self) -> str:
        return describe(self.material)

    @property
    def reason(self) -> str:
        facts = ", ".join(self.matched)
        if self.fit == FIT_QUESTION:
            if facts:
                return f"Связан с твоим вопросом и профилем ({facts})."
            return "Связан с темой твоего вопроса."
        if self.fit == FIT_PROFILE:
            return f"Подобран по профилю: {facts}."
        return (
            "На этот вопрос курс прямо не отвечает: более близкого платного курса в каталоге "
            "нет, предлагаю как возможный следующий шаг."
        )

    @property
    def why(self) -> str:
        about = self.about
        return " ".join(part for part in (about + "." if about else "", self.reason, NOTE) if part)


def describe(material: Material) -> str:
    """What the course is about, from public card metadata only (D-04)."""
    summary = _clip(_plain(material.summary), 160).rstrip(".")
    outcome = _clip(_plain(next(iter(material.outcomes), "")), 100).rstrip(".")
    if summary:
        text = f"О курсе: {summary}"
    elif material.topics or material.tags:
        text = "Тема курса: " + ", ".join((*material.topics, *material.tags)[:3])
    else:
        text = f"Курс «{_clip(_plain(material.title), 80)}»"
    if outcome and outcome.lower() not in text.lower():
        text += f". Результат по карточке: {outcome}"
    return text


def evaluate(
    material: Material, query: str = "", profile: BusinessProfile | None = None
) -> tuple[float, CourseOffer]:
    """Score one card against the question and the confirmed profile."""
    profile = profile or BusinessProfile()
    head = set(tokens(" ".join((material.title, *material.topics, *material.tags))))
    card = head | set(
        tokens(
            " ".join(
                (
                    material.summary,
                    *material.industries,
                    *material.audience,
                    *material.outcomes,
                )
            )
        )
    )
    asked = set(tokens(query)) - _NOISE
    on_head = asked & head
    on_card = asked & card
    on_topic = bool(on_head) or len(on_card) >= 2

    strong: list[str] = []
    weak: list[str] = []
    if profile.goal and (set(tokens(profile.goal)) - _NOISE) & card:
        strong.append(f"цель «{_clip(_plain(profile.goal), 40)}»")
    if profile.industry and profile.industry != "другое":
        words = " ".join((profile.industry, *INDUSTRY_HINTS.get(profile.industry, ())))
        labels = " ".join((*material.industries, *material.topics, *material.tags)).lower()
        hinted = any(hint in labels for hint in INDUSTRY_HINTS.get(profile.industry, ()))
        if hinted or (set(tokens(words)) - _NOISE) & card:
            strong.append(f"сфера «{profile.industry}»")
    if profile.legal_form in LEGAL_TERMS and set(tokens(LEGAL_TERMS[profile.legal_form])) & card:
        strong.append(profile.legal_form)
    regime = (profile.tax_regime or "").split(" ")[0]
    if regime in TAX_TERMS and set(tokens(TAX_TERMS[regime])) & card:
        strong.append(profile.tax_regime or regime)
    if profile.employees and set(tokens(EMPLOYEE_TERMS)) & card:
        strong.append("есть сотрудники")
    if profile.stage in STAGE_TERMS and set(tokens(STAGE_TERMS[profile.stage])) & card:
        weak.append(f"этап «{profile.stage}»")
    if profile.level and material.level == profile.level:
        weak.append(f"уровень «{LEVEL_LABELS.get(profile.level, profile.level)}»")

    if on_topic:
        fit = FIT_QUESTION
    elif strong:
        fit = FIT_PROFILE
    else:
        fit = FIT_GENERAL
    matched = tuple(strong + weak) if fit != FIT_GENERAL else ()
    score = 3 * len(on_head) + len(on_card) + 2 * len(strong) + 0.5 * len(weak)
    score += KnowledgeBase._profile_factor(material, profile) - 1
    return score, CourseOffer(material, fit, matched)


def choose_course(
    knowledge: KnowledgeBase,
    query: str = "",
    profile: BusinessProfile | None = None,
    exclude: frozenset[str] = frozenset(),
    *,
    require_relevance: bool = False,
) -> CourseOffer | None:
    """The best eligible paid course for this question and profile, or None.

    ``exclude`` (courses already shown in a route) is a preference: when every eligible
    course is excluded, the best one is offered again rather than none.
    """
    pool = [item for item in knowledge.materials.values() if item.recommendable_paid_course]
    fresh = [item for item in pool if item.id not in exclude] or pool
    if not fresh:
        return None
    ranked = sorted(
        (evaluate(item, query, profile) for item in fresh),
        key=lambda row: (-row[0], -row[1].material.popularity, row[1].material.id),
    )
    offer = ranked[0][1]
    if require_relevance and not offer.relevant:
        return None
    return offer


def _plain(text: str) -> str:
    text = re.sub(r"[*_`#<>]+", "", text or "")
    text = re.sub(r"(?:https?://|www\.)\S+", "", text)
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
