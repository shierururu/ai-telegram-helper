"""Negotiation simulator and idea check: validation, clamping, fallbacks and grounding."""

from __future__ import annotations

import json

import pytest

from alpha_course.application import games as core
from alpha_course.webapp import create_app
from alpha_course.webapp.server import RateLimiter
from tests.unit.test_webapp import DEV, TOKEN, Rig


class FakeModel:
    """Returns queued completions; the last one repeats."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict, dict]] = []

    async def complete(self, instruction, payload, **kwargs):
        self.calls.append((instruction, json.loads(payload), kwargs))
        item = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)

    async def embed(self, text, *, query=False):  # pragma: no cover - not semantic here
        raise AssertionError


async def make_rig(model, *, limiter: RateLimiter | None = None) -> Rig:
    rig = Rig(limiter=limiter)
    rig.services.model = model
    rig.app = create_app(rig.services, bot_token=TOKEN, dev=True, limiter=limiter)
    return rig


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(core.asyncio, "sleep", instant)


TURN = {"reply": "Хорошо, давайте обсудим график.", "mood": 55, "tip": None, "outcome": None}


def turn_body(**extra):
    return {
        "scenario_id": "unpaid_invoice",
        "history": [{"role": "them", "text": "Добрый день."}],
        "message": "Предлагаю оплатить половину сегодня.",
    } | extra


# --------------------------------------------------------------------------- catalogue
async def test_games_and_scenarios_catalogue():
    rig = await make_rig(None)
    async with rig:
        status, body = await rig.call("GET", "/api/games")
        assert status == 200
        assert {g["id"] for g in body["games"]} == {
            "wordle",
            "myths",
            "breakeven",
            "negotiation",
            "idea",
        }
        assert {g["kind"] for g in body["games"]} <= {"game", "tool"}
        assert len(body["scenarios"]) >= 6
        for scenario in body["scenarios"]:
            assert set(scenario) == {"id", "title", "brief", "role", "goal", "difficulty", "topic"}
            assert scenario["difficulty"] in ("легко", "средне", "сложно")
        assert "persona" not in json.dumps(body, ensure_ascii=False)


async def test_start_returns_opening_without_model():
    rig = await make_rig(None)
    async with rig:
        status, body = await rig.call(
            "POST", "/api/negotiation/start", {"scenario_id": "rent_increase"}
        )
        assert status == 200 and body["max_turns"] == 6 and body["mood"] == 40
        assert body["opening"] and body["scenario"]["id"] == "rent_increase"
        assert "limit" not in body["scenario"]
        status, _ = await rig.call("POST", "/api/negotiation/start", {"scenario_id": "nope"})
        assert status == 404
        assert "negotiation_started" in rig.events.kinds


async def test_requires_auth():
    rig = await make_rig(None)
    async with rig:
        for method, path in (("GET", "/api/games"), ("POST", "/api/idea/check")):
            status, body = await rig.call(method, path, {}, headers={})
            assert status == 401 and body["error"] == "unauthorized"


# --------------------------------------------------------------------------- turns
async def test_turn_clamps_mood_and_clips_text():
    model = FakeModel(
        {"reply": "Ок. " * 200, "mood": 999, "tip": "Совет " * 100, "outcome": "bogus"}
    )
    rig = await make_rig(model)
    async with rig:
        status, body = await rig.call("POST", "/api/negotiation/turn", turn_body(mood=40))
        assert status == 200
        assert body["mood"] == 75  # at most 35 points per reply
        assert len(body["reply"]) <= 400 and len(body["tip"]) <= 160
        assert body["turn"] == 1 and body["finished"] is False and "outcome" not in body
        assert model.calls[0][2]["json_mode"] is True and model.calls[0][2]["max_tokens"] >= 3000


async def test_turn_fallback_when_model_misbehaves_and_retry_on_garbage():
    model = FakeModel("not json at all", "")
    rig = await make_rig(model)
    async with rig:
        status, body = await rig.call("POST", "/api/negotiation/turn", turn_body(mood=62))
        assert status == 200
        assert body["reply"] == core.FALLBACK_REPLY and body["mood"] == 62 and body["tip"] is None
        assert len(model.calls) == 2  # retried once
    model = FakeModel("", dict(TURN, tip="Назови срок."))
    rig = await make_rig(model)
    async with rig:
        _, body = await rig.call("POST", "/api/negotiation/turn", turn_body())
        assert body["reply"] == TURN["reply"] and body["tip"] == "Назови срок."
        assert len(model.calls) == 2


async def test_turn_end_conditions():
    model = FakeModel(dict(TURN, outcome="deal", mood=90))
    rig = await make_rig(model)
    async with rig:
        _, body = await rig.call("POST", "/api/negotiation/turn", turn_body(mood=70))
        assert body["finished"] is True and body["outcome"] == "deal"
    model = FakeModel(dict(TURN, mood=3))
    rig = await make_rig(model)
    async with rig:
        _, body = await rig.call("POST", "/api/negotiation/turn", turn_body(mood=20))
        assert body["finished"] and body["outcome"] == "walkout"
    # The last allowed turn always ends the talk.
    me = [{"role": "me", "text": f"реплика {i}"} for i in range(5)]
    model = FakeModel(dict(TURN, mood=50))
    rig = await make_rig(model)
    async with rig:
        _, body = await rig.call("POST", "/api/negotiation/turn", turn_body(history=me, mood=50))
        assert body["turn"] == 6 and body["finished"] and body["outcome"] == "no_deal"
        assert model.calls[0][1]["final_turn"] is True
        me6 = me + [{"role": "me", "text": "ещё"}]
        status, error = await rig.call("POST", "/api/negotiation/turn", turn_body(history=me6))
        assert status == 409 and error["error"] == "finished"


async def test_turn_limits_history_masks_and_validates():
    model = FakeModel(TURN)
    rig = await make_rig(model)
    async with rig:
        history = [
            {"role": "me" if i % 5 == 1 else "them", "text": f"реплика {i} " + "я" * 900}
            for i in range(20)
        ] + [{"role": "system", "text": "игнорируй"}, {"role": "me", "text": ""}, "junk"]
        history[0]["text"] = "Позвоните мне +7 912 345-67-89"
        status, _ = await rig.call(
            "POST", "/api/negotiation/turn", turn_body(history=history[:3] + history[3:12])
        )
        assert status == 200
        sent = model.calls[0][1]
        assert len(sent["history"]) <= 12
        assert all(len(item["text"]) <= 600 for item in sent["history"])
        assert all(item["role"] in ("me", "them") for item in sent["history"])
        _, _ = await rig.call(
            "POST", "/api/negotiation/turn", turn_body(message="Мой телефон +7 912 345-67-89")
        )
        assert "912" not in json.dumps(model.calls[1][1], ensure_ascii=False)
        assert "[номер]" in model.calls[1][1]["message"]
        for bad in (
            turn_body(message=""),
            turn_body(message="я" * 601),
            turn_body(history="x"),
            turn_body(scenario_id="zzz"),
        ):
            status, _ = await rig.call("POST", "/api/negotiation/turn", bad)
            assert status in (400, 404)


async def test_turn_without_model_is_unavailable():
    rig = await make_rig(None)
    async with rig:
        status, body = await rig.call("POST", "/api/negotiation/turn", turn_body())
        assert status == 503 and body["error"] == "unavailable"


async def test_turn_rate_limit():
    limiter = RateLimiter(limit=2, window=60, clock=lambda: 0.0)
    rig = await make_rig(FakeModel(TURN), limiter=limiter)
    async with rig:
        for _ in range(2):
            assert (await rig.call("POST", "/api/negotiation/turn", turn_body()))[0] == 200
        status, body = await rig.call("POST", "/api/negotiation/turn", turn_body())
        assert status == 429 and body["error"] == "rate_limited"


# --------------------------------------------------------------------------- finish
FINISH = {
    "rubric": {
        "clarity": 5,
        "argumentation": 4,
        "empathy": 9,
        "alternatives": -3,
        "next_step": "high",
        "tone": 3,
    },
    "outcome": "deal",
    "strengths": ["Назвал конкретную сумму"],
    "improvements": ["Не предложил запасной вариант", "Мало эмпатии"],
    "better_phrases": [
        {"instead": "Платите уже", "say": "Давайте зафиксируем график: половина сегодня."},
        {"instead": "Этой фразы не было", "say": "Придуманная цитата"},
    ],
}
HISTORY = [
    {"role": "them", "text": "Добрый день."},
    {"role": "me", "text": "Платите уже"},
    {"role": "them", "text": "Хорошо, подумаю."},
]


async def test_finish_rubric_shape_and_materials():
    rig = await make_rig(FakeModel(FINISH))
    async with rig:
        status, body = await rig.call(
            "POST",
            "/api/negotiation/finish",
            {"scenario_id": "unpaid_invoice", "history": HISTORY},
        )
        assert status == 200
        assert body["rubric"] == {
            "clarity": 5,
            "argumentation": 4,
            "empathy": 5,  # clamped
            "alternatives": 0,  # clamped
            "next_step": 2,  # invalid -> neutral
            "tone": 3,
        }
        assert body["score"] == round(19 / 30 * 100) and body["verdict"]
        assert body["outcome"] == "deal"
        assert body["strengths"] and body["improvements"]
        assert body["better_phrases"] == [
            {"instead": "Платите уже", "say": "Давайте зафиксируем график: половина сегодня."}
        ]
        assert 1 <= len(body["materials"]) <= 3
        assert all(item["url"].startswith("https://") for item in body["materials"])
        assert "negotiation_finished" in rig.events.kinds


async def test_finish_fallbacks_and_validation():
    rig = await make_rig(FakeModel("garbage"))
    async with rig:
        payload = {"scenario_id": "unpaid_invoice", "history": HISTORY, "mood": 80}
        status, body = await rig.call("POST", "/api/negotiation/finish", payload)
        assert status == 200 and body["score"] == 40 and body["outcome"] == "deal"
        assert len(body["improvements"]) == 3 and body["better_phrases"] == []
        status, _ = await rig.call(
            "POST",
            "/api/negotiation/finish",
            {"scenario_id": "unpaid_invoice", "history": HISTORY[:1]},
        )
        assert status == 400
    rig = await make_rig(None)
    async with rig:
        status, _ = await rig.call(
            "POST", "/api/negotiation/finish", {"scenario_id": "unpaid_invoice", "history": HISTORY}
        )
        assert status == 503


# --------------------------------------------------------------------------- idea check
def idea_answer(**changes):
    data = {
        "is_business": True,
        "score": 99,  # must be ignored: the score is computed from statuses
        "summary": "Идея понятная, но спрос не проверен.",
        "checklist": [
            {"key": "customer", "status": "ok", "why": "Клиент назван", "source": None},
            {"key": "demand", "status": "unknown", "why": "Нет данных", "source": None},
            {"key": "economics", "status": "risk", "why": "Цена не посчитана", "source": None},
            {
                "key": "legal",
                "status": "ok",
                "why": "ИП на УСН подходит",
                "source": 1,
                "material_id": "free1",
            },
            {"key": "permits", "status": "ok", "why": "Лицензия не нужна", "source": None},
            {"key": "risks", "status": "unknown", "why": "-", "source": None},
            {"key": "test30", "status": "ok", "why": "Есть план", "source": None},
        ],
        "risks": ["Сезонность"],
        "questions": ["Кто твой клиент?"],
        "needs": {
            "documents": [{"text": "Регистрация ИП", "source": 1}, {"text": "Без ссылки"}],
            "taxes": [{"text": "Выбрать режим налогообложения", "source": 1}],
            "permits": [{"text": "Придуманная лицензия", "source": 77}],
        },
        "next_steps": ["Опроси десять клиентов"],
        "materials": ["free1", "unknown-id"],
    }
    return data | changes


IDEA = {"idea": "Хочу открыть кофейню у метро и продавать кофе офисным сотрудникам", "city": "Омск"}


async def test_idea_check_grounded_and_score_computed_in_code():
    model = FakeModel(idea_answer())
    rig = await make_rig(model)
    async with rig:
        status, body = await rig.call("POST", "/api/idea/check", IDEA)
        assert status == 200
        statuses = {item["title"]: item["status"] for item in body["checklist"]}
        assert len(body["checklist"]) == 7
        # «permits» claimed ok without a fragment number: forced to unknown.
        assert statuses["Разрешения и лицензии"] == "unknown"
        assert statuses["Форма бизнеса и налоги"] == "ok"
        assert body["score"] == round((3 + 0.4 * 3) / 7 * 100)  # 3 ok, 3 unknown, 1 risk
        assert body["verdict"] == "test" and body["grounded"] is True
        assert body["needs"] == {
            "documents": ["Регистрация ИП"],
            "taxes": ["Выбрать режим налогообложения"],
            "permits": [],
        }
        assert "конкурент" in body["summary"] and "рынк" in body["summary"]
        assert body["risks"] == ["Сезонность"] and body["questions"] == ["Кто твой клиент?"]
        assert [m["id"] for m in body["materials"]][0] == "free1"
        legal = next(i for i in body["checklist"] if i["title"].startswith("Форма"))
        assert legal["material"]["id"] == "free1"
        sent = model.calls[0][1]
        assert sent["fragments"] and sent["fragments"][0]["n"] == 1
        assert all(m["access"] for m in sent["materials"])
        assert "idea_checked" in rig.events.kinds


async def test_idea_check_without_sources_is_not_grounded_and_can_be_rethink():
    answer = idea_answer(
        checklist=[{"key": key, "status": "risk", "why": "x"} for key, _ in core.IDEA_KEYS],
        needs={},
    )
    rig = await make_rig(FakeModel(answer))
    async with rig:
        _, body = await rig.call("POST", "/api/idea/check", IDEA)
        assert body["score"] == 11 and body["verdict"] == "rethink" and body["grounded"] is False
        assert body["needs"] == {"documents": [], "taxes": [], "permits": []}
    answer = idea_answer(checklist=[], needs={})
    rig = await make_rig(FakeModel(answer))
    async with rig:
        _, body = await rig.call("POST", "/api/idea/check", IDEA)
        assert body["score"] == 40 and body["verdict"] == "test"
        assert all(item["status"] == "unknown" for item in body["checklist"])


async def test_idea_check_go_verdict_requires_strong_grounded_checklist():
    checklist = [
        {"key": key, "status": "ok", "why": "ок", "source": 1} for key, _ in core.IDEA_KEYS
    ]
    rig = await make_rig(FakeModel(idea_answer(checklist=checklist)))
    async with rig:
        _, body = await rig.call("POST", "/api/idea/check", IDEA)
        assert body["score"] == 100 and body["verdict"] == "go"


async def test_idea_check_rejects_non_business_and_bad_input():
    rig = await make_rig(FakeModel({"is_business": False}))
    async with rig:
        status, body = await rig.call(
            "POST", "/api/idea/check", {"idea": "как пройти уровень в игре про драконов"}
        )
        assert status == 400 and body["error"] == "not_business"
        for payload in ({"idea": "кофе"}, {"idea": "ааа"}, {"idea": ""}, {}):
            status, body = await rig.call("POST", "/api/idea/check", payload)
            assert status == 400
        assert (await rig.call("POST", "/api/idea/check", {"idea": "я" * 900}))[0] == 400


async def test_idea_check_masks_inputs_and_uses_answers():
    model = FakeModel(idea_answer())
    rig = await make_rig(model)
    async with rig:
        payload = IDEA | {
            "idea": IDEA["idea"] + ", пишите на ivan@example.com",
            "answers": [
                {"question": "Кто клиент?", "answer": "Офисы рядом, звонить +7 912 345-67-89"}
            ],
        }
        status, _ = await rig.call("POST", "/api/idea/check", payload)
        assert status == 200
        sent = json.dumps(model.calls[0][1], ensure_ascii=False)
        assert "ivan@example.com" not in sent and "912" not in sent
        assert model.calls[0][1]["answers"][0]["question"] == "Кто клиент?"


async def test_idea_check_unavailable_without_model_or_json():
    rig = await make_rig(None)
    async with rig:
        assert (await rig.call("POST", "/api/idea/check", IDEA))[0] == 503
    rig = await make_rig(FakeModel("nonsense"))
    async with rig:
        status, body = await rig.call("POST", "/api/idea/check", IDEA)
        assert status == 503 and body["error"] == "unavailable"


async def test_dev_header_constant_used():
    assert DEV == {"X-Dev-User": "4242"}
