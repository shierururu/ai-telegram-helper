"""«Слово дня»: Wordle with business terms from the free glossary.

One puzzle per Moscow calendar day for everybody. The server never sends the answer before the
game is over; progress and streaks live on the client, the server only scores guesses.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiohttp import web

from alpha_course.application.knowledge import KnowledgeBase, Material
from alpha_course.webapp.api import Api, bad, json_response, read_body, text_field

MAX_TRIES = 6
LENGTHS = (5, 6, 7)
EPOCH = date(2026, 11, 11)
_WORD = re.compile(r"[а-я]+")


def normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е")


def eligible(knowledge: KnowledgeBase) -> list[Material]:
    """Free single-word glossary terms, in a fixed pseudo-random order."""
    items = [
        item
        for item in knowledge.materials.values()
        if item.kind == "glossary"
        and not item.is_paid
        and item.summary
        and len(normalize(item.title)) in LENGTHS
        and _WORD.fullmatch(normalize(item.title))
    ]
    items.sort(key=lambda item: hashlib.sha256(item.id.encode()).hexdigest())
    return items


def today_moscow() -> date:
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def puzzle_for(knowledge: KnowledgeBase, day: date) -> Material | None:
    pool = eligible(knowledge)
    if not pool:
        return None
    return pool[(day - EPOCH).days % len(pool)]


def score(guess: str, answer: str) -> list[str]:
    """Standard Wordle marks with correct handling of repeated letters."""
    result = ["absent"] * len(answer)
    left: dict[str, int] = {}
    for index, letter in enumerate(answer):
        if guess[index] == letter:
            result[index] = "correct"
        else:
            left[letter] = left.get(letter, 0) + 1
    for index, letter in enumerate(guess):
        if result[index] == "correct":
            continue
        if left.get(letter, 0) > 0:
            result[index] = "present"
            left[letter] -= 1
    return result


def _first_sentence(text: str, limit: int) -> str:
    text = " ".join(text.split())
    cut = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(cut) > limit:
        cut = cut[: limit - 1].rsplit(" ", 1)[0] + "…"
    return cut


def hint_for(material: Material) -> str:
    """Definition with the term itself masked."""
    answer = normalize(material.title)
    stem = answer[: max(4, len(answer) - 2)]
    sentence = _first_sentence(material.summary, 170)
    return re.sub(rf"\b{re.escape(stem)}\w*", "_____", sentence, flags=re.IGNORECASE | re.UNICODE)


class WordleApi:
    def __init__(self, api: Api) -> None:
        self.api = api

    def _puzzle(self, day: date | None = None) -> tuple[date, Material]:
        day = day or today_moscow()
        material = puzzle_for(self.api.s.knowledge, day)
        if material is None:
            raise bad("Слово дня пока недоступно.", "wordle_unavailable")
        return day, material

    async def today(self, request: web.Request) -> web.Response:
        self.api.owner(request)
        day, material = self._puzzle()
        return json_response(
            {
                "date": day.isoformat(),
                "number": (day - EPOCH).days + 1,
                "length": len(normalize(material.title)),
                "max_tries": MAX_TRIES,
                "hint": hint_for(material),
            }
        )

    async def guess(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        body = await read_body(request)
        word = normalize(text_field(body, "word", limit=20))
        attempt = body.get("attempt")
        if type(attempt) is not int or not 1 <= attempt <= MAX_TRIES:
            raise bad("Неверный номер попытки.")
        day, material = self._puzzle()
        answer = normalize(material.title)
        if len(word) != len(answer) or not _WORD.fullmatch(word):
            raise bad(f"Нужно слово из {len(answer)} русских букв.", "invalid_word")
        marks = score(word, answer)
        solved = word == answer
        finished = solved or attempt >= MAX_TRIES
        payload = {"result": marks, "solved": solved, "finished": finished}
        if finished:
            payload.update(
                answer=material.title.strip().upper(),
                definition=_first_sentence(material.summary, 300),
                material={"id": material.id, "title": material.title, "url": material.url},
            )
            await self.api.log(
                owner, "wordle_won" if solved else "wordle_lost", attempts=attempt, day=str(day)
            )
        return json_response(payload)


def register(app: web.Application, api: Api) -> None:
    wordle = WordleApi(api)
    app.router.add_get("/api/wordle", wordle.today)
    app.router.add_post("/api/wordle/guess", wordle.guess)
