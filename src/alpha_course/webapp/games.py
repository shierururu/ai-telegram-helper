"""Games and trainers: negotiation simulator and business idea check (docs/webapp-api.md)."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from alpha_course.application.games import (
    MAX_MESSAGE,
    MAX_TURNS,
    SCENARIO_BY_ID,
    SCENARIOS,
    START_MOOD,
    GameCoach,
    GameUnavailable,
    NotBusiness,
    clamp,
    sanitize_history,
)
from alpha_course.application.privacy import mask_personal
from alpha_course.webapp.api import Api, ApiError, bad, json_response, read_body, text_field
from alpha_course.webapp.serializers import material_json

GAMES = (
    {
        "id": "wordle",
        "title": "Слово дня",
        "tagline": "Угадай бизнес-термин за 6 попыток",
        "kind": "game",
    },
    {
        "id": "myths",
        "title": "Миф или правда",
        "tagline": "5 утверждений о бизнесе в день: проверь, что ты знаешь",
        "kind": "game",
    },
    {
        "id": "breakeven",
        "title": "Точка безубыточности",
        "tagline": "Угадай, сколько продаж нужно, чтобы выйти в ноль",
        "kind": "game",
    },
    {
        "id": "negotiation",
        "title": "Симулятор переговоров",
        "tagline": "Потренируйся договариваться с клиентом, поставщиком или арендодателем",
        "kind": "game",
    },
    {
        "id": "idea",
        "title": "Проверка идеи",
        "tagline": "Чек-лист по твоей бизнес-идее с опорой на базу знаний",
        "kind": "tool",
    },
)


def unavailable() -> ApiError:
    return ApiError(
        503, "unavailable", "ИИ-помощник сейчас недоступен. Попробуй ещё раз через минуту."
    )


def scenario_of(body: dict):
    scenario = SCENARIO_BY_ID.get(text_field(body, "scenario_id", limit=60))
    if scenario is None:
        raise ApiError(404, "not_found", "Такого сценария нет.")
    return scenario


class GamesApi:
    def __init__(self, api: Api) -> None:
        self.api = api
        services = api.s
        model = getattr(services.assistant, "model", None) or services.model
        self.coach = GameCoach(
            services.knowledge,
            model,
            fast_model=getattr(services.assistant, "fast_model", "deepseek-v4.1-flash"),
            answer_model=getattr(services.assistant, "answer_model", "deepseek-v4.1-flash"),
        )

    async def games(self, request: web.Request) -> web.Response:
        return json_response(
            {"games": list(GAMES), "scenarios": [item.public() for item in SCENARIOS]}
        )

    async def start(self, request: web.Request) -> web.Response:
        body = await read_body(request)
        scenario = scenario_of(body)
        await self.api.log(self.api.owner(request), "negotiation_started", scenario=scenario.id)
        return json_response(
            {
                "scenario": scenario.public(),
                "opening": scenario.opening,
                "max_turns": MAX_TURNS,
                "mood": START_MOOD,
            }
        )

    @staticmethod
    def _history(body: dict) -> list[dict[str, str]]:
        if "history" in body and not isinstance(body["history"], list):
            raise bad("Поле «history» должно быть списком.")
        return sanitize_history(body.get("history"))

    @staticmethod
    def _mood(body: dict) -> int:
        return clamp(body.get("mood"), 0, 100, START_MOOD)

    async def turn(self, request: web.Request) -> web.Response:
        body = await read_body(request)
        scenario = scenario_of(body)
        message = text_field(body, "message", limit=MAX_MESSAGE)
        history = self._history(body)
        if sum(1 for item in history if item["role"] == "me") >= MAX_TURNS:
            raise ApiError(409, "finished", "Переговоры уже закончились. Посмотри разбор.")
        try:
            result = await self.coach.negotiation_turn(
                scenario, history, message, mood=self._mood(body)
            )
        except GameUnavailable:
            raise unavailable() from None
        payload: dict[str, Any] = {
            "reply": result.reply,
            "mood": result.mood,
            "tip": result.tip,
            "turn": result.turn,
            "finished": result.finished,
        }
        if result.outcome:
            payload["outcome"] = result.outcome
        return json_response(payload)

    async def finish(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        body = await read_body(request)
        scenario = scenario_of(body)
        history = self._history(body)
        if not any(item["role"] == "me" for item in history):
            raise bad("Сначала поговори с собеседником хотя бы одну реплику.")
        if self.coach.model is None:
            raise unavailable()
        result = await self.coach.negotiation_finish(scenario, history, mood=self._mood(body))
        await self.api.log(
            owner,
            "negotiation_finished",
            scenario=scenario.id,
            score=result.score,
            outcome=result.outcome,
        )
        return json_response(
            {
                "score": result.score,
                "verdict": result.verdict,
                "rubric": result.rubric,
                "strengths": result.strengths,
                "improvements": result.improvements,
                "better_phrases": result.better_phrases,
                "materials": [material_json(item) for item in result.materials],
                "outcome": result.outcome,
            }
        )

    async def idea(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        body = await read_body(request)
        idea = text_field(body, "idea", limit=800)
        if len(idea) < 8 or len(idea.split()) < 2:
            raise ApiError(400, "not_business", "Опиши идею хотя бы одним-двумя предложениями.")
        city = text_field(body, "city", limit=80, required=False)
        answers = []
        raw = body.get("answers")
        for row in raw[:6] if isinstance(raw, list) else []:
            if isinstance(row, dict) and isinstance(row.get("question"), str):
                answer = row.get("answer")
                if isinstance(answer, str) and answer.strip():
                    answers.append(
                        {
                            "question": mask_personal(row["question"], limit=200),
                            "answer": mask_personal(answer, limit=300),
                        }
                    )
        profile = await self.api.s.profiles.load(owner)
        try:
            result = await self.coach.check_idea(idea, city=city, answers=answers, profile=profile)
        except NotBusiness:
            raise ApiError(
                400,
                "not_business",
                "Это не похоже на бизнес-идею. Расскажи, что ты хочешь продавать и кому.",
            ) from None
        except GameUnavailable:
            raise unavailable() from None
        await self.api.log(owner, "idea_checked", verdict=result.verdict, score=result.score)
        return json_response(
            {
                "verdict": result.verdict,
                "score": result.score,
                "summary": result.summary,
                "checklist": [
                    {
                        "title": item.title,
                        "status": item.status,
                        "why": item.why,
                        "material": material_json(item.material) if item.material else None,
                    }
                    for item in result.checklist
                ],
                "risks": result.risks,
                "questions": result.questions,
                "needs": result.needs,
                "next_steps": result.next_steps,
                "materials": [material_json(item) for item in result.materials],
                "grounded": result.grounded,
            }
        )


def register(app: web.Application, api: Api) -> None:
    games = GamesApi(api)
    app.router.add_get("/api/games", games.games)
    app.router.add_post("/api/negotiation/start", games.start)
    app.router.add_post("/api/negotiation/turn", games.turn)
    app.router.add_post("/api/negotiation/finish", games.finish)
    app.router.add_post("/api/idea/check", games.idea)
