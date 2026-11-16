"""Run the consultation pipeline on a battery of test questions and write a report.

Usage: python -m tools.eval_questions [--limit N] [--out artifacts/private/eval.md]
Requires the prepared corpus (ALFA_CORPUS_PATH) and ALFA_YANDEX_API_KEY in .env.
Test profiles are synthetic (D-03, D-09).
"""

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

from alpha_course.application.assistant import Assistant
from alpha_course.application.knowledge import KnowledgeBase
from alpha_course.application.profile import BusinessProfile
from alpha_course.infrastructure.config import load_settings
from alpha_course.infrastructure.yandex import YandexAI
from alpha_course.telegram.render import answer_html

NOVICE = BusinessProfile(stage="идея", industry="общепит", level="novice")
SHOP = BusinessProfile(
    stage="развитие", industry="розница", legal_form="ИП", tax_regime="УСН «Доходы»", employees=True
)
SOLO = BusinessProfile(
    stage="работает до года", industry="IT и digital", legal_form="самозанятый", tax_regime="НПД"
)
EMPTY = BusinessProfile()

QUESTIONS: list[tuple[str, BusinessProfile, list[dict]]] = [
    # Persona 1: beginner
    ("с чего начать бизнес?", EMPTY, []),
    ("хочу открыть кофейню, что нужно сделать", NOVICE, []),
    ("как зарегистрировать ИП", NOVICE, []),
    ("что такое франшиза", EMPTY, []),
    ("какой налоговый режим выбрать для начинающего", NOVICE, []),
    ("ип или ооо что лучше", EMPTY, []),
    ("как составить бизнес план", NOVICE, []),
    ("где взять деньги на открытие бизнеса", NOVICE, []),
    ("что такое эквайринг", EMPTY, []),
    ("много деняк", EMPTY, []),
    # Persona 2: small business owner, point questions
    ("когда платить налог на усн", SHOP, []),
    ("как рассчитать точку безубыточности", SHOP, []),
    ("нужен шаблон договора поставки", SHOP, []),
    ("как уволить сотрудника по собственному желанию", SHOP, []),
    ("как нанять первого сотрудника", SHOP, []),
    ("что такое 115-ФЗ и почему блокируют счет", SHOP, []),
    ("как выйти на маркетплейсы", SHOP, []),
    ("как поднять продажи в магазине", SHOP, []),
    ("какие отчеты сдает ип с сотрудниками", SHOP, []),
    ("как считать зарплату с ндфл", SHOP, []),
    ("оптимизировать расходы", SHOP, []),
    (
        "на доставку",
        SHOP,
        [
            {"role": "user", "text": "оптимизировать расходы"},
            {"role": "assistant", "text": "Какие расходы ты хочешь сократить?"},
        ],
    ),
    ("а для ооо?", SHOP, [{"role": "user", "text": "когда сдавать декларацию по усн ип"}]),
    # Persona 3: solo founder
    ("как самозанятому стать ип", SOLO, []),
    ("какой лимит дохода у самозанятого", SOLO, []),
    ("как продвигать IT-услуги в интернете", SOLO, []),
    ("хочу разобраться в маркетинге системно", SOLO, []),
    ("как вести переговоры с клиентом", SOLO, []),
    ("как использовать нейросети в бизнесе", SOLO, []),
    ("как найти инвестора", SOLO, []),
    # Ambiguous / out of base / offtopic / informal
    ("что с налогами?", EMPTY, []),
    ("помоги с бизнесом", EMPTY, []),
    ("как пройти уровень в brawl stars", EMPTY, []),
    ("рецепт борща", EMPTY, []),
    ("как получить лицензию на добычу золота на марсе", EMPTY, []),
    ("сколько стоит патент для ип в москве на 2026", EMPTY, []),
    ("клиенты не платят, чо делать", SHOP, []),
    ("меня зовут Иван Петров, мой телефон +7 916 123-45-67, как открыть ип", EMPTY, []),
    ("напомни завтра в 10 оплатить аренду", EMPTY, []),
    ("составь мне план обучения", NOVICE, []),
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="")
    parser.add_argument("--answer-model", default="deepseek-v4.1-flash")
    parser.add_argument("--out", type=Path, default=Path("artifacts/private/eval.md"))
    args = parser.parse_args()
    settings = load_settings()
    knowledge = KnowledgeBase.from_path(settings.corpus_path)
    model = YandexAI(
        settings.yandex_api_key.get_secret_value(), folder_id=settings.yandex_folder_id
    )
    knowledge.load_vectors(settings.index_dir)
    assistant = Assistant(knowledge, model, answer_model=args.answer_model)
    items = QUESTIONS[: args.limit] if args.limit else QUESTIONS
    if args.only:
        items = [item for item in items if re.search(args.only, item[0], re.I)]
    lines = [f"# Прогон вопросов ({len(items)})\n"]

    async def run(question, profile, history):
        started = time.monotonic()
        reply = await assistant.reply(question, profile=profile, history=history)
        return question, profile, reply, time.monotonic() - started

    sem = asyncio.Semaphore(4)

    async def guarded(item):
        async with sem:
            return await run(*item)

    results = await asyncio.gather(*(guarded(item) for item in items))
    for question, profile, reply, elapsed in results:
        lines.append(
            f"## {question}\n_profile: {profile.context() or '—'} · {elapsed:.1f}s · kind={reply.kind} status={reply.status}_\n"
        )
        if reply.kind == "clarify" and reply.clarification:
            lines.append(
                f"CLARIFY: {reply.clarification.question} → {reply.clarification.options}\n"
            )
        elif reply.kind == "answer":
            lines.append(re.sub(r"<[^>]+>", "", answer_html(reply)) + "\n")
            if reply.followups:
                lines.append(f"followups: {reply.followups}\n")
        else:
            lines.append(f"(route → {reply.kind}) {reply.summary}\n")
        if reply.profile_updates:
            lines.append(f"profile_updates: {reply.profile_updates}\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    times = [r[3] for r in results]
    print(
        f"done {len(results)} answers, avg {sum(times) / len(times):.1f}s, max {max(times):.1f}s → {args.out}"
    )
    await model.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault(
        "ALFA_DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused"
    )
    asyncio.run(main())
