"""Consultation pipeline with a scripted model and a tiny synthetic knowledge base."""

import asyncio
import json
import time

import pytest

from alpha_course.application import assistant as module
from alpha_course.application.assistant import Assistant
from alpha_course.application.knowledge import Chunk, KnowledgeBase, Material
from alpha_course.application.profile import BusinessProfile
from alpha_course.infrastructure.yandex import YandexError

MATERIALS = [
    Material(
        id="tax",
        kind="article",
        title="Налоги для ИП",
        summary="Как платить налоги",
        url="https://example.test/tax",
        access="free",
    ),
    Material(
        id="tpl",
        kind="document",
        title="Шаблон договора по налогам",
        summary="Готовый шаблон",
        url="https://example.test/tpl",
        access="free",
    ),
    Material(
        id="paid",
        kind="course",
        title="Курс по налогам для бизнеса",
        summary="Разберётесь в налогах системно",
        url="https://example.test/paid",
        access="paid",
        url_observed=True,
        price="5 000 ₽",
    ),
]
CHUNKS = [
    Chunk("tax-1", "tax", "Сроки", "Налоги ИП платят раз в квартал до 28 числа."),
    Chunk("tpl-1", "tpl", "Образец", "Образец договора по налогам и сборам."),
]


class FakeModel:
    def __init__(self, understand: dict | None = None, compose=None) -> None:
        self.understand = understand or {"intent": "question", "search_query": "налоги ИП"}
        self.compose = compose if compose is not None else []
        self.calls: list[tuple[str, str, dict]] = []

    async def complete(
        self,
        instruction,
        payload,
        *,
        model=None,
        json_mode=False,
        temperature=0,
        max_tokens=700,
    ) -> str:
        self.calls.append((instruction, payload, {"model": model, "json_mode": json_mode}))
        if instruction == module.UNDERSTAND:
            return json.dumps(self.understand, ensure_ascii=False)
        assert instruction == module.COMPOSE
        item = self.compose.pop(0) if isinstance(self.compose, list) else self.compose
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)

    async def embed(self, text: str, *, query: bool = False) -> list[float]:
        raise AssertionError("semantic search is not ready in these tests")

    def of(self, instruction: str) -> list[tuple[str, str, dict]]:
        return [call for call in self.calls if call[0] == instruction]


def good_compose(**changes) -> dict:
    data = {
        "status": "answered",
        "summary": CHUNKS[0].text,
        "steps": [],
        "personal": "",
        "evidence": [{"source": 1, "quote": CHUNKS[0].text}],
        "sources": [1],
        "materials": [{"id": "tax", "why": "Про сроки"}],
        "followups": ["Какие ставки?"],
    }
    data.update(changes)
    return data


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase(MATERIALS, CHUNKS)


@pytest.mark.parametrize("text", ["Привет!", "здравствуйте", "Спасибо", "спс!"])
async def test_greeting_and_thanks_do_not_call_the_model(kb, text) -> None:
    model = FakeModel()
    reply = await Assistant(kb, model).reply(text)
    assert reply.kind in ("greeting", "thanks")
    assert model.calls == []


async def test_clarify_path_returns_options(kb) -> None:
    model = FakeModel(
        {
            "intent": "question",
            "clarify": True,
            "clarify_question": "Что именно с налогами?",
            "options": ["Сроки", "Ставки", ""],
            "search_query": "налоги",
        }
    )
    reply = await Assistant(kb, model).reply("что с налогами?")
    assert reply.kind == "clarify"
    assert reply.clarification.question == "Что именно с налогами?"
    assert reply.clarification.options == ["Сроки", "Ставки"]
    assert model.of(module.COMPOSE) == []


async def test_clarify_needs_at_least_two_options(kb) -> None:
    model = FakeModel(
        {"intent": "question", "clarify": True, "options": ["Сроки"], "search_query": "налоги"},
        compose=[good_compose()],
    )
    reply = await Assistant(kb, model).reply("налоги")
    assert reply.kind == "answer"


async def test_allow_clarify_false_goes_straight_to_answer(kb) -> None:
    model = FakeModel(
        {
            "intent": "question",
            "clarify": True,
            "options": ["Сроки", "Ставки"],
            "search_query": "налоги ИП",
        },
        compose=[good_compose()],
    )
    reply = await Assistant(kb, model).reply("что с налогами?", allow_clarify=False)
    assert reply.kind == "answer"
    assert len(model.of(module.COMPOSE)) == 1


async def test_offtopic_route_skips_retrieval(kb) -> None:
    model = FakeModel({"intent": "offtopic"})
    reply = await Assistant(kb, model).reply("какая сегодня погода")
    assert reply.kind == "offtopic"
    assert model.of(module.COMPOSE) == []


async def test_reminder_route_carries_event(kb) -> None:
    model = FakeModel({"intent": "reminder", "reminder_event": "сдать отчет по УСН"})
    reply = await Assistant(kb, model).reply("напомни про отчет завтра")
    assert reply.kind == "reminder"
    assert reply.summary == "сдать отчет по УСН"


async def test_unknown_intent_falls_back_to_question(kb) -> None:
    model = FakeModel({"intent": "nonsense", "search_query": "налоги ИП"}, compose=[good_compose()])
    assert (await Assistant(kb, model).reply("налоги ИП")).kind == "answer"


async def test_answered_reply_is_grounded_and_validated(kb) -> None:
    model = FakeModel(compose=[good_compose(sources=[1, 99, "x"])])
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    assert reply.kind == "answer"
    assert reply.status == "answered"
    assert [item.id for item in reply.sources] == ["tax"]
    assert reply.steps == []
    assert reply.followups == ["Какие ставки?"]
    compose = model.of(module.COMPOSE)[0]
    assert compose[2]["json_mode"] is True
    payload = json.loads(compose[1])
    assert payload["fragments"][0]["n"] == 1
    assert "квартал" in payload["fragments"][0]["text"]


async def test_no_valid_source_downgrades_status(kb) -> None:
    model = FakeModel(compose=[good_compose(sources=[42])])
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    assert reply.status == "no_evidence"
    assert reply.sources == []
    assert reply.summary == "В базе знаний не нашёл ответа на этот вопрос."


async def test_no_valid_source_and_no_fragments_means_no_evidence(kb) -> None:
    model = FakeModel(
        {"intent": "question", "search_query": "квантовая хромодинамика"},
        compose=[good_compose(sources=[1], materials=[])],
    )
    reply = await Assistant(kb, model).reply("квантовая хромодинамика")
    assert reply.status == "no_evidence"
    assert reply.steps == []
    assert reply.personal == ""


async def test_recommendations_are_limited_to_candidates(kb) -> None:
    rows = [
        {"id": "invented", "why": "x"},
        {"id": "tpl", "why": "Образец"},
        {"id": "tpl", "why": "d"},
    ]
    model = FakeModel(compose=[good_compose(materials=rows)])
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    assert [rec.material.id for rec in reply.recommendations] == ["tpl", "paid"]
    assert reply.recommendations[0].why == "Образец"


async def test_paid_material_is_only_a_card(kb) -> None:
    model = FakeModel(compose=[good_compose(materials=[{"id": "paid", "why": "Системно"}])])
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    assert [rec.material.id for rec in reply.recommendations] == ["paid"]
    assert reply.recommendations[0].material.is_paid
    payload = json.loads(model.of(module.COMPOSE)[0][1])
    paid = next(item for item in payload["materials"] if item["id"] == "paid")
    assert paid["access"] == "платно"
    # Only public metadata is sent: no chunk text from paid material exists, no url or price.
    assert set(paid) == {"id", "type", "title", "description", "access", "for"}


async def test_summary_is_stripped_of_markdown_and_urls(kb) -> None:
    model = FakeModel(
        compose=[good_compose(summary="**Платите** налоги https://evil.test/x вовремя")]
    )
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    assert reply.status == "local"
    assert "evil.test" not in reply.excerpt


async def test_empty_summary_falls_back_to_local(kb) -> None:
    model = FakeModel(compose=[good_compose(summary="")])
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    assert reply.status == "local"


async def test_invalid_json_twice_falls_back_to_local_excerpt(kb) -> None:
    model = FakeModel(compose=["not json", "{broken", "oops"])
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    # One retry at most: retries multiplied the waiting time.
    assert len(model.of(module.COMPOSE)) == 2
    assert reply.status == "unavailable"
    assert reply.excerpt == ""
    assert reply.sources == []


async def test_compose_retry_recovers_after_one_failure(kb) -> None:
    model = FakeModel(compose=[RuntimeError("boom"), good_compose()])
    reply = await Assistant(kb, model).reply("когда платить налоги ИП")
    assert reply.status == "answered"
    assert len(model.of(module.COMPOSE)) == 2


async def test_json_inside_code_fence_is_accepted(kb) -> None:
    fenced = "```json\n" + json.dumps(good_compose(), ensure_ascii=False) + "\n```"
    reply = await Assistant(kb, FakeModel(compose=[fenced])).reply("налоги ИП")
    assert reply.status == "answered"


async def test_without_model_answers_locally(kb) -> None:
    reply = await Assistant(kb, None).reply("когда платить налоги ИП")
    assert reply.kind == "answer"
    assert reply.status == "local"
    assert reply.sources[0].id == "tax"


async def test_without_model_and_without_match_is_honest(kb) -> None:
    reply = await Assistant(kb, None).reply("квантовая хромодинамика")
    assert reply.status == "no_evidence"
    assert reply.sources == []


async def test_without_model_reminder_words_are_detected(kb) -> None:
    reply = await Assistant(kb, None).reply("напомни оплатить налог")
    assert reply.kind == "reminder"


async def test_profile_updates_need_a_statement_about_the_user(kb) -> None:
    understanding = {
        "intent": "question",
        "search_query": "налоги ИП",
        "profile": {"industry": "общепит", "stage": "запуск"},
    }
    about_topic = await Assistant(kb, FakeModel(understanding, [good_compose()])).reply(
        "как платить налоги в общепите"
    )
    assert about_topic.profile_updates == {}
    about_self = await Assistant(kb, FakeModel(understanding, [good_compose()])).reply(
        "у меня кафе, как платить налоги"
    )
    assert about_self.profile_updates == {"industry": "общепит", "stage": "запуск"}


async def test_profile_updates_accept_only_allowed_values(kb) -> None:
    understanding = {
        "intent": "question",
        "search_query": "налоги ИП",
        "profile": {
            "industry": "космос",
            "legal_form": "ИП",
            "tax_regime": "не знаю",
            "level": "novice",
            "employees": "yes",
            "goal": "расти",
        },
    }
    reply = await Assistant(kb, FakeModel(understanding, [good_compose()])).reply(
        "я ИП, как платить налоги"
    )
    assert reply.profile_updates == {"legal_form": "ИП", "level": "novice"}


async def test_known_profile_values_are_not_reported_again(kb) -> None:
    understanding = {
        "intent": "question",
        "search_query": "налоги ИП",
        "profile": {"legal_form": "ИП"},
    }
    reply = await Assistant(kb, FakeModel(understanding, [good_compose()])).reply(
        "я ИП, как платить налоги", profile=BusinessProfile(legal_form="ИП")
    )
    assert reply.profile_updates == {}


async def test_goal_is_taken_only_for_profile_info(kb) -> None:
    profile_part = {"goal": "открыть вторую точку", "industry": "общепит"}
    info = await Assistant(
        kb, FakeModel({"intent": "profile_info", "profile": profile_part})
    ).reply("у меня кафе, хочу открыть вторую точку")
    assert info.kind == "profile"
    assert info.profile_updates == {"goal": "открыть вторую точку", "industry": "общепит"}

    question = await Assistant(
        kb,
        FakeModel(
            {"intent": "question", "search_query": "налоги ИП", "profile": profile_part},
            [good_compose()],
        ),
    ).reply("у меня кафе, как платить налоги")
    assert question.kind == "answer"
    assert "goal" not in question.profile_updates


async def test_new_profile_facts_personalize_the_composed_answer(kb) -> None:
    model = FakeModel(
        {"intent": "question", "search_query": "налоги ИП", "profile": {"tax_regime": "ПСН"}},
        [good_compose()],
    )
    await Assistant(kb, model).reply("я на патенте, когда платить налоги")
    payload = json.loads(model.of(module.COMPOSE)[0][1])
    assert payload["profile"] == {"Налоги": "ПСН"}


async def test_external_payloads_contain_masked_text_only(kb) -> None:
    model = FakeModel(compose=[good_compose()])
    await Assistant(kb, model).reply(
        "меня зовут Иван Петров, мой телефон +7 916 123-45-67, когда платить налоги ИП?",
        history=[{"role": "user", "text": "пишите на ivan@example.com"}],
    )
    sent = "\n".join(call[1] for call in model.calls)
    assert model.of(module.UNDERSTAND) and model.of(module.COMPOSE)
    for secret in ("916", "123-45-67", "Иван", "Петров", "ivan@example.com"):
        assert secret not in sent
    assert "[номер]" in sent


async def test_progress_callback_gets_distinct_material_count(kb) -> None:
    seen: list[int] = []

    async def progress(count: int) -> None:
        seen.append(count)

    model = FakeModel(compose=[good_compose()])
    await Assistant(kb, model).reply("налоги по договору", progress=progress)
    assert seen == [2]


async def test_progress_failure_does_not_break_answer(kb) -> None:
    async def progress(count: int) -> None:
        raise RuntimeError("telegram unavailable")

    reply = await Assistant(kb, FakeModel(compose=[good_compose()])).reply(
        "налоги ИП", progress=progress
    )
    assert reply.status == "answered"


async def test_understanding_failure_still_answers(kb) -> None:
    class Broken(FakeModel):
        async def complete(self, instruction, payload, **kwargs):
            if instruction == module.UNDERSTAND:
                raise RuntimeError("down")
            return await super().complete(instruction, payload, **kwargs)

    reply = await Assistant(kb, Broken(compose=[good_compose()])).reply("налоги ИП")
    assert reply.kind == "answer"


async def test_unsafe_request_is_refused_without_retrieval(kb, monkeypatch) -> None:
    searched = []
    monkeypatch.setattr(kb, "search", lambda *a, **k: searched.append(a))
    model = FakeModel({"intent": "unsafe", "search_query": "уйти от налогов"})
    reply = await Assistant(kb, model).reply("как незаконно уйти от налогов")
    assert reply.kind == "offtopic"
    assert reply.status == "unsafe"
    assert reply.summary == module.REFUSAL
    assert not reply.recommendations
    assert model.of(module.COMPOSE) == [] and searched == []


class SlowModel(FakeModel):
    """Hangs on the chosen step to exercise the deadline and step budgets."""

    def __init__(self, slow: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.slow = slow

    async def complete(self, instruction, payload, **kwargs):
        if instruction == self.slow:
            self.calls.append((instruction, payload, kwargs))
            await asyncio.sleep(3600)
        return await super().complete(instruction, payload, **kwargs)


async def test_slow_compose_returns_honest_timeout_within_deadline(kb) -> None:
    model = SlowModel(module.COMPOSE, compose=[good_compose()])
    started = time.monotonic()
    reply = await Assistant(kb, model, deadline_seconds=1.5).reply("когда платить налоги ИП")
    assert time.monotonic() - started < 3
    assert reply.kind == "answer"
    assert reply.status == "unavailable"
    assert reply.summary == module.TIMEOUT_TEXT
    # Found materials are still offered; nothing generated is shown.
    assert reply.recommendations and not reply.steps and not reply.sources
    assert len(model.of(module.COMPOSE)) == 1


async def test_slow_understanding_falls_back_to_the_masked_question(kb, monkeypatch) -> None:
    monkeypatch.setattr(module, "UNDERSTAND_BUDGET", 0.05)
    model = SlowModel(module.UNDERSTAND, compose=[good_compose()])
    reply = await Assistant(kb, model, deadline_seconds=5).reply("когда платить налоги ИП")
    assert reply.status == "answered"
    payload = json.loads(model.of(module.COMPOSE)[0][1])
    assert payload["question"] == "когда платить налоги ИП"


async def test_slow_first_understanding_is_hedged_by_one_duplicate(kb, monkeypatch) -> None:
    monkeypatch.setattr(module, "UNDERSTAND_HEDGE", 0.05)

    class FirstHangs(FakeModel):
        async def complete(self, instruction, payload, **kwargs):
            if instruction == module.UNDERSTAND and not self.of(module.UNDERSTAND):
                self.calls.append((instruction, payload, kwargs))
                await asyncio.sleep(3600)
            return await super().complete(instruction, payload, **kwargs)

    model = FirstHangs(
        {"intent": "question", "clarify": True, "options": ["Сроки", "Ставки"]},
    )
    started = time.monotonic()
    reply = await Assistant(kb, model).reply("что с налогами?")
    assert time.monotonic() - started < 2
    assert reply.kind == "clarify"
    first, duplicate = model.of(module.UNDERSTAND)
    assert first[1] == duplicate[1]


async def test_hanging_embedding_does_not_block_the_answer(kb, monkeypatch) -> None:
    monkeypatch.setattr(module, "EMBED_BUDGET", 0.05)
    monkeypatch.setattr(type(kb), "semantic_ready", property(lambda self: True))
    monkeypatch.setattr(kb, "semantic_scores", lambda vector: {})

    class HangingEmbed(FakeModel):
        async def embed(self, text, *, query=False):
            await asyncio.sleep(3600)

    started = time.monotonic()
    reply = await Assistant(kb, HangingEmbed(compose=[good_compose()])).reply("налоги ИП")
    assert reply.status == "answered"
    assert time.monotonic() - started < 2


async def test_quote_matches_the_normalized_text_the_model_saw() -> None:
    material = Material(
        id="ip",
        kind="article",
        title="Регистрация ИП",
        summary="s",
        url="https://e.test",
        access="free",
    )
    raw = "Подайте заявление по форме № Р21001 в налоговую инспекцию."
    sent = "Подайте заявление по форме No Р21001 в налоговую инспекцию."
    kb = KnowledgeBase([material], [Chunk("ip-1", "ip", "h", raw)])
    model = FakeModel(
        {"intent": "question", "search_query": "заявление Р21001"},
        compose=[good_compose(summary=sent, evidence=[{"source": 1, "quote": sent}])],
    )
    reply = await Assistant(kb, model).reply("заявление Р21001")
    assert reply.status == "answered"
    assert reply.summary == sent


async def test_masking_placeholder_is_never_accepted_as_a_quote() -> None:
    material = Material(
        id="m",
        kind="article",
        title="Маркировка",
        summary="s",
        url="https://e.test",
        access="free",
    )
    raw = "Товары маркируют в системе Честный Знак до начала продажи."
    kb = KnowledgeBase([material], [Chunk("m-1", "m", "h", raw)])
    quote = "Товары маркируют в системе [имя] до начала продажи."
    model = FakeModel(
        {"intent": "question", "search_query": "маркировка товаров"},
        compose=[good_compose(summary=quote, evidence=[{"source": 1, "quote": quote}])],
    )
    reply = await Assistant(kb, model).reply("маркировка товаров")
    assert reply.status != "answered"
    assert "[имя]" not in reply.summary


async def test_compose_context_is_compact() -> None:
    materials = [
        Material(
            id=f"m{i}",
            kind="article",
            title=f"Налоги {i}",
            summary="s",
            url="https://e.test",
            access="free",
        )
        for i in range(12)
    ]
    chunks = [Chunk(f"c{i}", f"m{i}", "h", "налоги ИП " + "слово " * 400) for i in range(12)]
    model = FakeModel(compose=[good_compose()])
    await Assistant(KnowledgeBase(materials, chunks), model).reply("налоги ИП")
    payload = json.loads(model.of(module.COMPOSE)[0][1])
    assert len(payload["fragments"]) <= module.COMPOSE_FRAGMENTS
    assert all(len(item["text"]) <= module.FRAGMENT_CHARS for item in payload["fragments"])
    assert len(payload["materials"]) <= module.COMPOSE_MATERIALS + 1


async def test_learning_plan_uses_only_known_materials(kb) -> None:
    class PlanModel(FakeModel):
        async def complete(self, instruction, payload, **kwargs):
            return json.dumps(
                {
                    "intro": "Маршрут",
                    "steps": [
                        {"id": "ghost", "why": "x"},
                        {"id": "tax", "why": "Начни тут"},
                        {"id": "tpl", "why": "Затем шаблон"},
                        {"id": "tax", "why": "повтор"},
                    ],
                }
            )

    intro, steps = await Assistant(kb, PlanModel()).learning_plan(
        BusinessProfile(stage="запуск", goal="налоги")
    )
    assert intro == "Маршрут"
    assert [step.material.id for step in steps] == ["tax", "tpl", "paid"]


@pytest.mark.asyncio
async def test_general_help_uses_only_masked_question_and_categories():
    class GeneralModel:
        calls = []

        async def complete(self, instruction, payload, **kwargs):
            self.calls.append((instruction, json.loads(payload), kwargs))
            if instruction == module.GENERAL_HELP_SCOPE:
                return '{"scope":"business"}'
            return json.dumps({"kind": "answer", "text": "Проверь спрос https://invented.test"})

    model = GeneralModel()

    class NoCorpus:
        def __getattribute__(self, name):
            # Only public catalog cards may be read, locally, to pick a paid course offer.
            if name == "materials":
                return {}
            raise AssertionError("AI help must not access corpus")

    assistant = Assistant(NoCorpus(), model)
    reply = await assistant.general_help(
        "Как найти клиентов? Пиши owner@example.test",
        profile=BusinessProfile(industry="услуги", goal="PRIVATE GOAL"),
    )
    assert reply.kind == "ai_help"
    assert not reply.sources and not reply.recommendations
    assert "https://" not in reply.summary
    assert len(model.calls) == 2
    scope_instruction, scope_payload, scope_kwargs = model.calls[0]
    assert scope_instruction == module.GENERAL_HELP_SCOPE
    assert set(scope_payload) == {"message"}
    assert "owner@example.test" not in scope_payload["message"]
    assert scope_kwargs["model"] == assistant.fast_model
    assert scope_kwargs["max_tokens"] == 200
    instruction, payload, kwargs = model.calls[1]
    assert scope_payload["message"] == payload["message"]
    assert instruction.startswith(module.COPILOT[:60])
    assert set(payload) == {"message", "profile", "history"}
    assert payload["profile"] == {"industry": "услуги", "goal": "PRIVATE GOAL"}
    assert "owner@example.test" not in payload["message"]
    assert kwargs["model"] == assistant.answer_model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        RuntimeError("private provider error"),
        "bad json",
        '{"scope":"offtopic"}',
        '{"scope":"unknown"}',
        '{"kind":"answer","text":"weather"}',
        '{"scope":"business","extra":true}',
        "null",
        "[]",
    ],
)
async def test_general_help_failure_and_offtopic(result):
    class GeneralModel:
        calls = 0

        async def complete(self, instruction, payload, **kwargs):
            self.calls += 1
            assert instruction == module.GENERAL_HELP_SCOPE
            assert set(json.loads(payload)) == {"message"}
            if isinstance(result, Exception):
                raise result
            return result

    model = GeneralModel()
    reply = await Assistant(object(), model).general_help("погода")
    assert model.calls == 1
    if isinstance(result, str) and "offtopic" in result:
        assert reply.kind == "offtopic"
    else:
        assert "недоступна" in reply.summary
        assert "private provider error" not in reply.summary
    assert not reply.sources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        RuntimeError("private provider error"),
        YandexError("Yandex returned an empty completion"),
        "bad json",
    ],
)
async def test_general_help_answer_failure_after_business_scope_is_unavailable(result):
    class GeneralModel:
        calls = 0

        async def complete(self, instruction, payload, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return '{"scope":"business"}'
            if isinstance(result, Exception):
                raise result
            return result

    model = GeneralModel()
    reply = await Assistant(object(), model).general_help("Как открыть бизнес?")
    assert model.calls == 2
    assert "недоступна" in reply.summary
    assert "private provider error" not in reply.summary
    assert not reply.sources and not reply.recommendations


@pytest.mark.asyncio
async def test_general_help_without_model_is_unavailable():
    reply = await Assistant(KnowledgeBase(MATERIALS, CHUNKS)).general_help("Как открыть бизнес?")
    assert reply.status == "no_evidence"
    assert "недоступна" in reply.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "brand", ["Альфа-Курс", "альфа курс", "Альфа‑Курса", "Alfa-Course", "Alpha Course"]
)
async def test_general_help_does_not_show_generated_brand_mentions(brand):
    class GeneralModel:
        async def complete(self, instruction, payload, **kwargs):
            if instruction == module.GENERAL_HELP_SCOPE:
                return '{"scope":"business"}'
            return json.dumps({"kind": "answer", "text": f"По базе {brand} ответ такой."})

    reply = await Assistant(object(), GeneralModel()).general_help("Как открыть бизнес?")
    assert reply.status == "no_evidence"
    assert "недоступна" in reply.summary
    assert brand not in reply.summary
    assert not reply.sources and not reply.recommendations


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["scope", "answer"])
@pytest.mark.parametrize(
    ("error", "reason", "expected"),
    [
        (
            YandexError("Missing Yandex API key"),
            "configuration",
            "Сообщи администратору",
        ),
        (
            YandexError("Yandex completion failed (HTTP 403)"),
            "configuration",
            "Сообщи администратору",
        ),
        (YandexError("Yandex request timed out"), "provider", "чуть позже"),
        (YandexError("Yandex completion rate-limited"), "provider", "чуть позже"),
        (
            YandexError("Invalid response from Yandex"),
            "invalid_response",
            "корректный ответ",
        ),
        (RuntimeError("PRIVATE QUESTION PROFILE KEY"), "provider", "чуть позже"),
    ],
)
async def test_general_help_reports_failure_category_and_safe_diagnostics(
    stage, error, reason, expected, caplog
):
    class FailingModel:
        calls = 0

        async def complete(self, instruction, payload, **kwargs):
            self.calls += 1
            if stage == "answer" and self.calls == 1:
                return '{"scope":"business"}'
            raise error

    model = FailingModel()
    reply = await Assistant(object(), model).general_help(
        "Как продать товар? PRIVATE QUESTION",
        profile=BusinessProfile(goal="PRIVATE PROFILE"),
    )
    assert expected in reply.summary
    assert "Проверь подключение" not in reply.summary
    assert model.calls == (1 if stage == "scope" else 2)
    assert f"stage={stage} reason={reason}" in caplog.text
    if isinstance(error, YandexError):
        assert str(error) in caplog.text
    for secret in (
        "PRIVATE QUESTION",
        "PRIVATE PROFILE",
        "PRIVATE QUESTION PROFILE KEY",
    ):
        assert secret not in caplog.text
        assert secret not in reply.summary
    assert not reply.sources and not reply.recommendations


@pytest.mark.asyncio
async def test_general_help_invalid_response_does_not_suggest_configuration(caplog):
    class InvalidModel:
        async def complete(self, instruction, payload, **kwargs):
            return '{"scope":"unknown"}'

    reply = await Assistant(object(), InvalidModel()).general_help("Как открыть бизнес?")
    assert "корректный ответ" in reply.summary
    assert "reason=invalid_response" in caplog.text
    assert "подключение" not in reply.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second_result",
    [
        '{"scope":"business"}',
        '{"scope":"offtopic"}',
        '{"scope":"unknown"}',
        YandexError("Yandex returned an empty completion"),
    ],
)
async def test_general_help_retries_empty_scope_once_with_same_masked_payload(
    second_result, caplog
):
    class RetryModel:
        def __init__(self):
            self.calls = []

        async def complete(self, instruction, payload, **kwargs):
            self.calls.append((instruction, json.loads(payload), kwargs))
            if len(self.calls) == 1:
                raise YandexError("Yandex returned an empty completion")
            if len(self.calls) == 2:
                if isinstance(second_result, Exception):
                    raise second_result
                return second_result
            assert instruction.startswith(module.COPILOT[:60])
            return '{"kind":"answer","text":"Проверь спрос."}'

    class NoCorpus:
        def __getattribute__(self, name):
            # Only public catalog cards may be read, locally, to pick a paid course offer.
            if name == "materials":
                return {}
            raise AssertionError("AI help must not access corpus")

    model = RetryModel()
    reply = await Assistant(NoCorpus(), model).general_help(
        "Как найти клиентов? Пиши owner@example.test",
        profile=BusinessProfile(goal="PRIVATE PROFILE"),
    )
    first, retry = model.calls[:2]
    assert first[:2] == retry[:2]
    assert first[0] == module.GENERAL_HELP_SCOPE
    assert set(first[1]) == {"message"}
    assert "owner@example.test" not in first[1]["message"]
    assert "PRIVATE PROFILE" not in json.dumps(model.calls[:2])
    assert first[2]["max_tokens"] == 200
    assert retry[2]["max_tokens"] == 500
    assert first[2]["model"] == retry[2]["model"]
    assert first[2]["json_mode"] is retry[2]["json_mode"] is True
    if second_result == '{"scope":"business"}':
        assert len(model.calls) == 3
        assert reply.summary == "Проверь спрос."
    else:
        assert len(model.calls) == 2
        if second_result == '{"scope":"offtopic"}':
            assert reply.kind == "offtopic"
        else:
            assert "корректный ответ" in reply.summary
            assert "stage=scope reason=invalid_response" in caplog.text
    assert "owner@example.test" not in caplog.text
    assert "PRIVATE PROFILE" not in caplog.text
    assert not reply.sources and not reply.recommendations


@pytest.mark.asyncio
async def test_general_help_does_not_retry_non_provider_empty_completion():
    class OtherModel:
        calls = 0

        async def complete(self, instruction, payload, **kwargs):
            self.calls += 1
            raise RuntimeError("Yandex returned an empty completion")

    model = OtherModel()
    reply = await Assistant(object(), model).general_help("Как открыть бизнес?")
    assert model.calls == 1
    assert "чуть позже" in reply.summary


async def test_pdf_report_scope_uses_original_question_and_longer_compose_without_corpus():
    import json

    class NoCorpus:
        def __getattribute__(self, name):
            raise AssertionError("General report must not access corpus")

    class ReportModel:
        def __init__(self):
            self.calls = []

        async def complete(self, instruction, payload, **kwargs):
            self.calls.append((instruction, json.loads(payload), kwargs))
            if len(self.calls) == 1:
                return '{"scope":"business"}'
            return json.dumps({"kind": "answer", "text": "План действий. " * 300})

    model = ReportModel()
    assistant = Assistant(NoCorpus(), model)
    question = "Подготовь PDF с планом запуска кофейни"
    result = await assistant.general_help(question, document=True)
    assert model.calls[0][1] == {"message": question}
    assert len(result.summary) > 2200
    assert model.calls[1][2]["max_tokens"] == 4000
    assert "PDF создаёт приложение" in model.calls[1][0]


class WebModel:
    """Scope -> router answer that asks for web -> search result with citations."""

    def __init__(self, web_fails=False):
        self.calls = []
        self.web_calls = []
        self.web_fails = web_fails

    async def complete(self, instruction, payload, **kwargs):
        self.calls.append((instruction, json.loads(payload)))
        if instruction == module.GENERAL_HELP_SCOPE:
            return '{"scope":"business"}'
        if "Поиск в интернете сейчас недоступен" in instruction:
            return json.dumps({"kind": "answer", "text": "Данные не проверены. Общий совет."})
        return json.dumps(
            {
                "kind": "answer",
                "title": "Конкуренты",
                "needs_web": True,
                "search_query": "кофейни Казань конкуренты owner@example.test",
                "text": "",
            }
        )

    async def web_answer(self, instruction, payload, **kwargs):
        self.web_calls.append((instruction, json.loads(payload)))
        if self.web_fails:
            raise YandexError("Yandex request failed")
        return "Нашёл трёх конкурентов https://leak.test/x", [
            {"title": "Источник", "url": "https://example.org/a"}
        ]


@pytest.mark.asyncio
async def test_general_help_searches_web_with_masked_query_and_profile():
    model = WebModel()
    profile = BusinessProfile(industry="услуги", tax_regime="УСН", goal="первые клиенты")
    reply = await Assistant(object(), model).general_help(
        "Найди конкурентов, пиши owner@example.test", profile=profile
    )
    assert reply.kind == "ai_help" and reply.title == "Конкуренты"
    assert reply.links == [{"title": "Источник", "url": "https://example.org/a"}]
    assert "https://" not in reply.summary  # links come only from citations
    task = model.web_calls[0][1]
    assert "owner@example.test" not in json.dumps(task, ensure_ascii=False)
    assert task["profile"]["tax_regime"] == "УСН"


@pytest.mark.asyncio
async def test_general_help_falls_back_when_search_fails():
    model = WebModel(web_fails=True)
    reply = await Assistant(object(), model).general_help("Найди конкурентов")
    assert reply.kind == "ai_help" and reply.summary.startswith("Данные не проверены")
    assert reply.links == []


@pytest.mark.asyncio
async def test_general_help_sends_masked_history_to_the_answer_only():
    model = WebModel()
    await Assistant(object(), model).general_help(
        "А для ООО?", history=[{"q": "Налоги? owner@example.test", "a": "УСН."}]
    )
    scope_payload = model.calls[0][1]
    answer_payload = model.calls[1][1]
    assert set(scope_payload) == {"message", "previous_question"}
    assert "owner@example.test" not in scope_payload["previous_question"]
    assert answer_payload["history"][0]["a"] == "УСН."
    assert "owner@example.test" not in json.dumps(answer_payload, ensure_ascii=False)


@pytest.mark.parametrize("status", ["partial", "no_evidence", "answered"])
async def test_missing_evidence_never_displays_invented_answer(kb, status):
    reply = await Assistant(
        kb,
        FakeModel(
            compose=[
                good_compose(
                    status=status,
                    summary="Invented tax deadline tomorrow",
                    sources=[99],
                    steps=["Send private dump"],
                    personal="Unsupported personal fact",
                )
            ]
        ),
    ).reply("налоги ИП")
    assert reply.status == "no_evidence"
    assert not reply.steps and not reply.personal and not reply.sources
    assert "Invented" not in reply.summary


async def test_unrelated_valid_quote_cannot_authorize_generated_claim(kb):
    reply = await Assistant(
        kb,
        FakeModel(
            compose=[
                good_compose(
                    summary="Ставка налога 99 процентов",
                    steps=["Переведи деньги"],
                )
            ]
        ),
    ).reply("налоги ИП")
    assert reply.status == "local"
    assert reply.excerpt == CHUNKS[0].text
    assert not reply.steps and not reply.personal


async def test_forged_quote_is_rejected(kb):
    reply = await Assistant(
        kb,
        FakeModel(
            compose=[
                good_compose(
                    evidence=[{"source": 1, "quote": "Ставка налога 99 процентов"}],
                )
            ]
        ),
    ).reply("налоги ИП")
    assert reply.status == "no_evidence"


async def test_personal_note_is_built_locally_not_generated(kb):
    reply = await Assistant(
        kb,
        FakeModel(
            compose=[
                good_compose(
                    personal="Unsupported private claim",
                )
            ]
        ),
    ).reply("налоги ИП", profile=BusinessProfile(industry="общепит"))
    assert reply.status == "answered"
    assert reply.personal == "Подбор учитывает контекст бизнеса: общепит."


@pytest.mark.parametrize(
    "changes",
    [
        {"steps": 1},
        {"materials": 1},
        {"followups": 1},
        {"sources": 1},
        {"sources": "1"},
        {"evidence": 1},
        {"evidence": {"source": 1, "quote": CHUNKS[0].text}},
    ],
)
async def test_malformed_evidence_fails_closed(kb, changes):
    reply = await Assistant(kb, FakeModel(compose=[good_compose(**changes)])).reply("налоги ИП")
    assert reply.status == "no_evidence"
    assert not reply.sources and not reply.steps and not reply.personal


@pytest.mark.parametrize("mode", ["local", "model", "failure", "empty"])
async def test_final_course_is_guaranteed_even_when_model_omits_it(kb, mode, monkeypatch):
    async def no_sleep(*args):
        pass

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    model = (
        None
        if mode == "local"
        else FakeModel(
            compose=[
                good_compose(
                    summary="" if mode == "empty" else "Налоги платят.", materials=[{"id": "tax"}]
                )
            ]
            if mode != "failure"
            else ["broken"] * 3
        )
    )
    reply = await Assistant(kb, model).answer(
        "налоги", "только бесплатные налоги", BusinessProfile()
    )
    assert any(rec.material.id == "paid" for rec in reply.recommendations)
    assert any(rec.material.id == "tax" for rec in reply.recommendations)


async def test_paid_model_claim_is_replaced_with_public_card(kb):
    model = FakeModel(
        compose=[good_compose(materials=[{"id": "paid", "why": "SECRET LESSON DETAILS"}])]
    )
    reply = await Assistant(kb, model).answer("налоги", "налоги", BusinessProfile())
    rec = next(rec for rec in reply.recommendations if rec.material.is_paid)
    assert "SECRET" not in rec.why
    assert MATERIALS[2].summary in rec.why


async def test_paid_body_is_not_sent_or_shown_with_corrupt_input():
    kb = KnowledgeBase(MATERIALS, [Chunk("secret", "paid", "", "SECRET PAID LESSON")])
    model = FakeModel(compose=[good_compose()])
    reply = await Assistant(kb, model).answer("налоги", "налоги", BusinessProfile())
    assert "SECRET" not in json.dumps(model.calls)
    assert not reply.sources and not reply.excerpt
    assert reply.status == "no_evidence"


async def test_empty_catalog_does_not_invent_a_course():
    reply = await Assistant(KnowledgeBase([], [])).answer("налоги", "налоги", BusinessProfile())
    assert not reply.recommendations


def test_finalizer_preserves_cap_and_personalizes_from_confirmed_goal(kb):
    profile = BusinessProfile(goal="налоги")
    reply = module.Reply(kind="answer", recommendations=[module.Recommendation(MATERIALS[0])] * 3)
    result = Assistant._finalize(reply, MATERIALS[2], profile)
    assert len(result.recommendations) == 3
    assert result.recommendations[-1].material.id == "paid"
    assert "цель «налоги»" in result.recommendations[-1].why
    assert result.course_offer and result.course_offer.material.id == "paid"


async def test_learning_plan_refresh_keeps_course_when_all_courses_seen(kb):
    _, steps = await Assistant(kb).learning_plan(
        BusinessProfile(goal="налоги"), frozenset({"paid"})
    )
    assert any(step.material.is_paid for step in steps)
    assert len(steps) <= 5


async def test_seven_day_journey_remains_free(kb):
    days = await Assistant(kb).journey_steps("налоги", BusinessProfile(goal="налоги"), MATERIALS)
    assert len(days) == 7
    assert all(day["material_id"] != "paid" for day in days)


async def test_invalid_paid_url_is_never_a_candidate_or_card():
    from dataclasses import replace

    invalid = replace(MATERIALS[2], url="javascript:alert(1)")
    kb = KnowledgeBase([MATERIALS[0], invalid], CHUNKS[:1])
    model = FakeModel(compose=[good_compose(materials=[{"id": "paid"}])])
    reply = await Assistant(kb, model).answer("налоги", "налоги", BusinessProfile())
    assert all(not rec.material.is_paid for rec in reply.recommendations)
    assert "javascript" not in json.dumps(model.calls)
    assert all(not item.is_paid for item in kb.starter_pack(BusinessProfile(goal="налоги")))


async def test_unobserved_paid_link_never_enters_recommendations_or_payload():
    from dataclasses import replace

    unobserved = replace(MATERIALS[2], url_observed=False)
    kb = KnowledgeBase([MATERIALS[0], unobserved], CHUNKS[:1])
    model = FakeModel(compose=[good_compose(materials=[{"id": "paid"}])])
    reply = await Assistant(kb, model).answer("налоги", "налоги", BusinessProfile())
    assert all(not rec.material.is_paid for rec in reply.recommendations)
    payload = json.loads(model.of(module.COMPOSE)[0][1])
    assert all(item["id"] != "paid" for item in payload["materials"])
    assert not kb.paid_course("налоги")
    assert all(not item.is_paid for item in kb.starter_pack(BusinessProfile(goal="налоги")))


async def test_no_answer_keeps_only_materials_that_share_a_topic_word(kb):
    """«Налоги ИП» has related cards, «золото» has none: unrelated cards and buttons are dropped."""
    related = await Assistant(
        kb,
        FakeModel(
            {"intent": "question", "search_query": "налоги"},
            compose=[good_compose(status="no_evidence", sources=[], materials=[], followups=[])],
        ),
    ).reply("как заплатить налоги, если я зарубежом")
    assert related.status == "no_evidence"
    assert any("налог" in row.material.title.lower() for row in related.recommendations)

    unrelated = await Assistant(
        kb,
        FakeModel(
            {"intent": "question", "search_query": "добыча золота лицензия"},
            compose=[good_compose(status="no_evidence", sources=[], materials=[], followups=[])],
        ),
    ).reply("как получить лицензию на добычу золота")
    assert unrelated.status == "no_evidence"
    assert [row for row in unrelated.recommendations if not row.material.is_paid] == []
    assert unrelated.followups == list(module._GENERIC_FOLLOWUPS)
    assert not any(item.startswith(module._FALLBACK_PREFIX) for item in unrelated.followups)
