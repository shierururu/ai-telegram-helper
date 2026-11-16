"""A/B experiments: stable assignment, the paid-offer variants and honest statistics.

Pure logic only. Storage lives in infrastructure.experiments; this module never
sees Telegram identifiers, only the salted user key used by the event log.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from alpha_course.application.knowledge import Material
from alpha_course.application.offers import CourseOffer, evaluate
from alpha_course.application.profile import BusinessProfile


@dataclass(frozen=True)
class Experiment:
    name: str
    variants: tuple[str, ...]
    weights: tuple[int, ...]
    primary: str
    description: str

    def __post_init__(self) -> None:
        if len(self.variants) != len(self.weights) or sum(self.weights) <= 0:
            raise ValueError("experiment_weights_invalid")


# The first experiment: how a relevant paid course is offered under an answer.
# A — the course stays a line in the material list plus a «Подробнее о курсе» button.
# B — an extra personal block «Следующий шаг для тебя» built from public card data.
PAID_OFFER = Experiment(
    name="paid_offer_v1",
    variants=("A", "B"),
    weights=(50, 50),
    primary="Доля пользователей, перешедших к платному курсу",
    description="A — курс в списке материалов; B — персональный блок «Следующий шаг для тебя»",
)
CONTROL = "A"

# Minimal detectable effect for the sample-size estimate: +2 p.p. absolute.
DEFAULT_MDE = 0.02
DEFAULT_BASELINE = 0.05


def assign(experiment: Experiment, user_key: str) -> str:
    """Deterministic variant: same user, same experiment -> same variant everywhere."""
    digest = hashlib.sha256(f"{experiment.name}:{user_key}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % sum(experiment.weights)
    for variant, weight in zip(experiment.variants, experiment.weights, strict=True):
        if bucket < weight:
            return variant
        bucket -= weight
    return experiment.variants[-1]  # pragma: no cover - unreachable with positive weights


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def offer_pitch(
    material: Material, profile: BusinessProfile | None, offer: CourseOffer | None = None
) -> dict[str, str]:
    """Variant B copy from public catalog metadata only (D-04): no lessons or program.

    ``fit`` states only what really matched (question topic or confirmed profile facts);
    a course offered without such a match says so instead of claiming personalization.
    """
    price = (
        "Подписка Курс+" if material.access == "subscription" else material.price or "платный курс"
    )
    benefit = _clip(material.summary, 220) if material.summary else ""
    if offer is None or offer.material.id != material.id:
        offer = evaluate(material, "", profile)[1]
    fit = offer.reason
    if not (profile or BusinessProfile()).filled():
        fit += " Заполни «👤 Профиль» — буду подбирать точнее."
    return {
        "title": material.title,
        "price": price,
        "benefit": benefit,
        "fit": fit,
        "note": "Программа и условия — на странице курса.",
    }


# ---------------------------------------------------------------- statistics
def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


def difference_interval(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float, float]:
    """Newcombe hybrid score interval for p2 - p1 (B minus A), 95%."""
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson(x1, n1)
    l2, u2 = wilson(x2, n2)
    diff = p2 - p1
    lower = diff - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2)
    upper = diff + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2)
    return diff, lower, upper


def p_value(x1: int, n1: int, x2: int, n2: int) -> float:
    """Two-sided pooled z-test for two proportions."""
    pooled = (x1 + x2) / (n1 + n2)
    variance = pooled * (1 - pooled) * (1 / n1 + 1 / n2)
    if variance <= 0:
        return 1.0
    z = (x2 / n2 - x1 / n1) / math.sqrt(variance)
    return math.erfc(abs(z) / math.sqrt(2))


def sample_size(baseline: float, mde: float = DEFAULT_MDE) -> int:
    """Users per variant for alpha 0.05 (two-sided) and power 0.8."""
    p1 = min(max(baseline, 0.001), 0.999)
    p2 = min(p1 + mde, 0.999)
    z_alpha, z_beta = 1.959964, 0.841621
    average = (p1 + p2) / 2
    numerator = (
        z_alpha * math.sqrt(2 * average * (1 - average))
        + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    return math.ceil(numerator / (p2 - p1) ** 2)


def analyse(rows: dict[str, dict[str, int]], experiment: Experiment = PAID_OFFER) -> dict[str, Any]:
    """Compare every variant with the control on the per-user primary metric (ITT)."""
    variants: dict[str, dict[str, Any]] = {}
    for name in experiment.variants:
        row = rows.get(name, {})
        users = int(row.get("users", 0))
        clickers = int(row.get("clickers", 0))
        returned = int(row.get("returned", 0))
        shows = int(row.get("shows", 0))
        clicks = int(row.get("clicks", 0))
        variants[name] = {
            "users": users,
            "shows": shows,
            "clicks": clicks,
            "clickers": clickers,
            "returned": returned,
            "conversion": clickers / users if users else None,
            "conversion_ci": wilson(clickers, users) if users else None,
            "ctr": clicks / shows if shows else None,
            "retention": returned / users if users else None,
        }
    control = variants[CONTROL]
    baseline = control["conversion"] or DEFAULT_BASELINE
    needed = sample_size(baseline)
    comparisons: dict[str, dict[str, Any]] = {}
    for name, data in variants.items():
        if name == CONTROL:
            continue
        result: dict[str, Any] = {"needed_per_variant": needed}
        if control["users"] and data["users"]:
            diff, lower, upper = difference_interval(
                control["clickers"], control["users"], data["clickers"], data["users"]
            )
            result.update(
                diff=diff,
                ci=(lower, upper),
                p_value=p_value(
                    control["clickers"], control["users"], data["clickers"], data["users"]
                ),
            )
        enough = min(control["users"], data["users"]) >= needed
        if not enough:
            verdict = "insufficient"
        elif result["ci"][0] > 0:
            verdict = "better"
        elif result["ci"][1] < 0:
            verdict = "worse"
        else:
            verdict = "no_difference"
        result["verdict"] = verdict
        comparisons[name] = result
    return {
        "experiment": experiment.name,
        "primary": experiment.primary,
        "description": experiment.description,
        "variants": variants,
        "comparisons": comparisons,
    }


VERDICTS = {
    "insufficient": "данных пока мало — выводы делать рано",
    "better": "B лучше A (интервал выше нуля)",
    "worse": "B хуже A (интервал ниже нуля)",
    "no_difference": "разница не доказана",
}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _p(value: float) -> str:
    return "p<0.001" if value < 0.001 else f"p={value:.3f}"


def report_lines(analysis: dict[str, Any], *, synthetic: bool = False) -> list[str]:
    """Plain-text lines for the admin report (the caller escapes HTML if needed)."""
    title = f"A/B «{analysis['experiment']}»"
    if synthetic:
        title += " — СИНТЕТИКА, не реальные пользователи"
    lines = [title, analysis["description"], f"Главная метрика: {analysis['primary']}"]
    for name, data in analysis["variants"].items():
        lower_upper = data["conversion_ci"]
        interval = (
            f" [{_pct(lower_upper[0])}–{_pct(lower_upper[1])}]" if lower_upper is not None else ""
        )
        lines.append(
            f"{name}: пользователей {data['users']}, перешли {data['clickers']}"
            f" = {_pct(data['conversion'])}{interval}; показов {data['shows']},"
            f" CTR {_pct(data['ctr'])}; вернулись {_pct(data['retention'])}"
        )
    for name, result in analysis["comparisons"].items():
        if "diff" in result:
            lower, upper = result["ci"]
            lines.append(
                f"{name}−A: {result['diff'] * 100:+.1f} п.п."
                f" (95% ДИ {lower * 100:+.1f}…{upper * 100:+.1f}), {_p(result['p_value'])}"
            )
        lines.append(
            f"Итог: {VERDICTS[result['verdict']]}."
            f" Нужно ≥{result['needed_per_variant']} пользователей на вариант"
            f" (эффект +{DEFAULT_MDE * 100:.0f} п.п.)."
        )
    lines.append("Покупки бот не видит: считаем переходы к странице курса, а не оплату.")
    return lines
