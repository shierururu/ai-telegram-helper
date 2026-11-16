"""Synthetic A/B data for demonstrating the report; never mixed with real users.

    python -m alpha_course.entrypoints.ab_demo            # 2 x 2500 synthetic users
    python -m alpha_course.entrypoints.ab_demo --users 300 --b-rate 0.12
    python -m alpha_course.entrypoints.ab_demo --clear    # remove synthetic rows (DB owner role)

Rows use the experiment name «paid_offer_v1_synthetic» and user keys «synthetic:N»,
so /stats labels them as synthetic and /export skips them. The rates are inputs of
the simulation, not measurements (D-09, P-06).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from alpha_course.application.experiments import PAID_OFFER, assign, report_lines
from alpha_course.entrypoints.common import execute
from alpha_course.infrastructure.config import Settings
from alpha_course.infrastructure.database import create_database_engine
from alpha_course.infrastructure.events import EventStore
from alpha_course.infrastructure.experiments import Experiments

logger = logging.getLogger(__name__)
NAME = PAID_OFFER.name + "_synthetic"
_INSERT = text(
    "INSERT INTO app.events (id, created_at, user_key, kind, material_id, payload) "
    "VALUES (gen_random_uuid(), :at, :key, :kind, :material, CAST(:payload AS jsonb))"
)
_CLEAR = text("DELETE FROM app.events WHERE user_key LIKE 'synthetic:%'")
_EXISTS = text("SELECT 1 FROM app.events WHERE user_key LIKE 'synthetic:%' LIMIT 1")


def simulate(users: int, rates: dict[str, float], seed: int = 7) -> list[dict]:
    """Events for `users` synthetic people per variant with the given click-through rates."""
    rng = random.Random(seed)
    now = datetime.now(UTC)
    rows: list[dict] = []
    index = 0
    per_variant = dict.fromkeys(PAID_OFFER.variants, 0)
    while min(per_variant.values()) < users:
        index += 1
        key = f"synthetic:{index}"
        variant = assign(PAID_OFFER, key)
        if per_variant[variant] >= users:
            continue
        per_variant[variant] += 1
        t0 = now - timedelta(days=rng.uniform(1, 28), minutes=rng.uniform(0, 600))

        def add(kind: str, at: datetime, **payload: object) -> None:
            rows.append(
                {
                    "at": at,
                    "key": key,
                    "kind": kind,
                    "material": "synthetic-course",
                    "payload": json.dumps(
                        {"experiment": NAME, "variant": variant, "synthetic": True} | payload
                    ),
                }
            )

        add("experiment_assigned", t0)
        for show in range(rng.randint(1, 4)):
            add("paid_offer_shown", t0 + timedelta(hours=show * rng.uniform(2, 40)))
        if rng.random() < rates[variant]:
            add("paid_offer_click", t0 + timedelta(minutes=rng.uniform(1, 2000)))
        if rng.random() < 0.35:
            add("question_asked", t0 + timedelta(days=rng.uniform(1.05, 6)))
    return rows


async def run(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_database_engine(settings)
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        async with engine.begin() as connection:
            if args.clear:
                # Needs DELETE: the bot role may only insert events (run as the DB owner).
                await connection.execute(_CLEAR)
                print("Синтетические события удалены.")
                return
            if (await connection.execute(_EXISTS)).first() is not None:
                print("Синтетика уже есть: показываю отчёт. Пересоздать — сначала --clear.")
            else:
                rows = simulate(args.users, {"A": args.a_rate, "B": args.b_rate}, args.seed)
                await connection.execute(_INSERT, rows)
        report = await Experiments(EventStore(engine)).report(NAME)
        print("\n".join(report_lines(report, synthetic=True)))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--users", type=int, default=2500, help="пользователей на вариант")
    parser.add_argument("--a-rate", type=float, default=0.05, help="доля переходов в A")
    parser.add_argument("--b-rate", type=float, default=0.075, help="доля переходов в B")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--clear", action="store_true", help="только удалить синтетику")
    args = parser.parse_args()
    return execute(lambda settings: run(settings, args))


if __name__ == "__main__":
    raise SystemExit(main())
