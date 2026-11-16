"""Financial model: every number is computed here, the language model never counts money.

The model is deliberately simple and transparent: one product (average check), monthly figures,
three tax regimes. Rates are defaults; the real rate depends on the region.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

CONTRIBUTIONS = 0.30  # employer insurance contributions on payroll (simplified)
NPD_LIMIT_YEAR = 2_400_000
MIN_TAX = 0.01  # minimum tax on USN "income minus expenses"
MAX_VALUE = 10_000_000_000

REGIMES = {
    "npd": "НПД (самозанятый)",
    "usn_income": "УСН «Доходы»",
    "usn_income_expense": "УСН «Доходы минус расходы»",
}
DEFAULT_RATES = {"npd_person": 0.04, "npd_company": 0.06, "usn_income": 0.06, "usn_ie": 0.15}
PROFILE_REGIMES = {
    "НПД": "npd",
    "УСН «Доходы»": "usn_income",
    "УСН «Доходы минус расходы»": "usn_income_expense",
}


class FinmodelError(ValueError):
    pass


@dataclass(frozen=True)
class Inputs:
    price: float  # revenue per unit
    unit_cost: float  # variable cost per unit
    units: float  # planned units per month
    fixed: float = 0.0  # rent and other fixed costs per month
    payroll: float = 0.0  # gross payroll per month
    investment: float = 0.0  # one-off start-up costs
    regime: str = "usn_income"
    client: str = "person"  # for NPD: person (4%) or company (6%)
    rate: float | None = None  # override, 0..1


@dataclass
class Result:
    inputs: Inputs
    revenue: float
    variable: float
    fixed_total: float
    contributions: float
    tax: float
    tax_rate: float
    profit: float
    unit_margin: float
    margin_pct: float
    break_even_units: int | None
    break_even_revenue: float | None
    safety_pct: float | None  # how far planned volume is above break-even
    payback_months: float | None
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    regimes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verdict: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "regime": self.inputs.regime,
            "regime_label": REGIMES[self.inputs.regime],
            "revenue": rub(self.revenue),
            "variable": rub(self.variable),
            "fixed_total": rub(self.fixed_total),
            "contributions": rub(self.contributions),
            "tax": rub(self.tax),
            "tax_rate": round(self.tax_rate * 100, 2),
            "profit": rub(self.profit),
            "unit_margin": round(self.unit_margin, 2),
            "margin_pct": round(self.margin_pct, 1),
            "break_even_units": self.break_even_units,
            "break_even_revenue": None
            if self.break_even_revenue is None
            else rub(self.break_even_revenue),
            "safety_pct": None if self.safety_pct is None else round(self.safety_pct, 1),
            "payback_months": None
            if self.payback_months is None
            else round(self.payback_months, 1),
            "scenarios": self.scenarios,
            "regimes": self.regimes,
            "warnings": self.warnings,
            "verdict": self.verdict,
        }


def rub(value: float) -> int:
    return int(round(value))


def count_text(value: float) -> str:
    """A quantity the Russian way: «2 500 000», «12,5»; never «2.5e+06» or «12.5»."""
    rounded = round(value, 2)
    whole = int(rounded)
    if rounded == whole:
        return f"{whole:,}".replace(",", " ")
    return f"{rounded:,.2f}".rstrip("0").replace(",", " ").replace(".", ",")


def number(raw: Any, name: str, *, low: float = 0.0, required: bool = True) -> float:
    if raw is None or raw == "":
        if required:
            raise FinmodelError(f"Заполни поле «{name}».")
        return 0.0
    if isinstance(raw, bool):
        raise FinmodelError(f"Поле «{name}» должно быть числом.")
    if isinstance(raw, str):
        raw = raw.replace(" ", "").replace(",", ".").replace(" ", "")
    try:
        value = float(raw)
    except TypeError, ValueError:
        raise FinmodelError(f"Поле «{name}» должно быть числом.") from None
    if not math.isfinite(value) or value < low or value > MAX_VALUE:
        raise FinmodelError(f"Поле «{name}»: допустимы значения от {low:g} до {MAX_VALUE:,}.")
    return value


def parse_inputs(body: dict[str, Any]) -> Inputs:
    regime = body.get("regime") or "usn_income"
    if regime not in REGIMES:
        raise FinmodelError("Неизвестный налоговый режим.")
    client = body.get("client") or "person"
    if client not in ("person", "company"):
        raise FinmodelError("Клиент: «person» или «company».")
    rate = body.get("rate")
    parsed_rate = None
    if rate not in (None, ""):
        parsed_rate = number(rate, "ставка", low=0) / 100
        if parsed_rate > 0.5:
            raise FinmodelError("Ставка налога указана в процентах, например 6.")
    price = number(body.get("price"), "цена за единицу", low=0.01)
    return Inputs(
        price=price,
        unit_cost=number(body.get("unit_cost"), "переменные затраты на единицу", required=False),
        units=number(body.get("units"), "продаж в месяц"),
        fixed=number(body.get("fixed"), "постоянные расходы", required=False),
        payroll=number(body.get("payroll"), "зарплаты", required=False),
        investment=number(body.get("investment"), "стартовые вложения", required=False),
        regime=regime,
        client=client,
        rate=parsed_rate,
    )


def tax_rate(inputs: Inputs) -> float:
    if inputs.rate is not None:
        return inputs.rate
    if inputs.regime == "npd":
        return DEFAULT_RATES["npd_company" if inputs.client == "company" else "npd_person"]
    return DEFAULT_RATES["usn_income" if inputs.regime == "usn_income" else "usn_ie"]


def _profit(inputs: Inputs, units: float) -> tuple[float, float, float, float, float]:
    """Return (profit, revenue, tax, contributions, fixed_total) for the given volume."""
    revenue = inputs.price * units
    variable = inputs.unit_cost * units
    contributions = inputs.payroll * CONTRIBUTIONS
    fixed_total = inputs.fixed + inputs.payroll + contributions
    rate = tax_rate(inputs)
    if inputs.regime == "usn_income_expense":
        base = max(0.0, revenue - variable - fixed_total)
        tax = max(base * rate, revenue * MIN_TAX) if revenue > 0 else 0.0
    elif inputs.regime == "usn_income":
        gross = revenue * rate
        tax = max(0.0, gross - min(contributions, gross * 0.5))
    else:
        tax = revenue * rate
    return revenue - variable - fixed_total - tax, revenue, tax, contributions, fixed_total


def _break_even(inputs: Inputs) -> int | None:
    if inputs.price <= inputs.unit_cost:
        return None
    if _profit(inputs, 0)[0] >= 0:
        return 0
    high = 1.0
    while _profit(inputs, high)[0] < 0:
        high *= 2
        if high > 1e9:
            return None
    low = high / 2 if high > 1 else 0.0
    for _ in range(60):
        mid = (low + high) / 2
        if _profit(inputs, mid)[0] < 0:
            low = mid
        else:
            high = mid
    return math.ceil(high - 1e-9)


def _scenario(inputs: Inputs, label: str, *, units_k: float = 1.0, price_k: float = 1.0) -> dict:
    variant = Inputs(
        price=inputs.price * price_k,
        unit_cost=inputs.unit_cost,
        units=inputs.units * units_k,
        fixed=inputs.fixed,
        payroll=inputs.payroll,
        investment=inputs.investment,
        regime=inputs.regime,
        client=inputs.client,
        rate=inputs.rate,
    )
    profit, revenue, *_ = _profit(variant, variant.units)
    return {
        "label": label,
        "units": round(variant.units, 1),
        "revenue": rub(revenue),
        "profit": rub(profit),
    }


def calculate(inputs: Inputs) -> Result:
    profit, revenue, tax, contributions, fixed_total = _profit(inputs, inputs.units)
    variable = inputs.unit_cost * inputs.units
    unit_margin = inputs.price - inputs.unit_cost
    margin_pct = unit_margin / inputs.price * 100
    even = _break_even(inputs)
    even_revenue = None if even is None else even * inputs.price
    safety = None
    if even is not None and even > 0 and inputs.units > 0:
        safety = (inputs.units - even) / inputs.units * 100
    payback = inputs.investment / profit if profit > 0 and inputs.investment > 0 else None

    warnings: list[str] = []
    if unit_margin <= 0:
        warnings.append("Цена не выше переменных затрат: каждая продажа приносит убыток.")
    if even is not None and even > inputs.units > 0:
        warnings.append(
            f"Плановых продаж ({count_text(inputs.units)}) не хватает "
            f"до точки безубыточности ({count_text(even)})."
        )
    if inputs.regime == "npd":
        if inputs.payroll > 0:
            warnings.append("На НПД нельзя нанимать сотрудников по трудовым договорам.")
        if revenue * 12 > NPD_LIMIT_YEAR:
            warnings.append("Доход за год выше 2,4 млн ₽: лимит НПД превышен.")
    if inputs.regime == "usn_income_expense" and tax == revenue * MIN_TAX and revenue > 0:
        warnings.append("Действует минимальный налог 1% от выручки: расходы почти съедают базу.")

    regimes = []
    for key, label in REGIMES.items():
        variant = Inputs(
            price=inputs.price,
            unit_cost=inputs.unit_cost,
            units=inputs.units,
            fixed=inputs.fixed,
            payroll=inputs.payroll,
            investment=inputs.investment,
            regime=key,
            client=inputs.client,
            rate=inputs.rate if key == inputs.regime else None,
        )
        variant_profit, _, variant_tax, *_ = _profit(variant, variant.units)
        available = not (key == "npd" and (inputs.payroll > 0 or revenue * 12 > NPD_LIMIT_YEAR))
        regimes.append(
            {
                "regime": key,
                "label": label,
                "tax": rub(variant_tax),
                "profit": rub(variant_profit),
                "available": available,
                "current": key == inputs.regime,
            }
        )
    available_regimes = [item for item in regimes if item["available"]]
    if available_regimes:
        best = max(available_regimes, key=lambda item: item["profit"])
        for item in regimes:
            item["best"] = item is best

    scenarios = [
        _scenario(inputs, "Пессимистично: −30% продаж", units_k=0.7),
        _scenario(inputs, "По плану"),
        _scenario(inputs, "Оптимистично: +30% продаж", units_k=1.3),
        _scenario(inputs, "Цена −10%", price_k=0.9),
        _scenario(inputs, "Цена +10%", price_k=1.1),
    ]

    if profit < 0:
        verdict = "loss"
    elif even is not None and safety is not None and safety < 20:
        verdict = "tight"
    else:
        verdict = "ok"
    return Result(
        inputs=inputs,
        revenue=revenue,
        variable=variable,
        fixed_total=fixed_total,
        contributions=contributions,
        tax=tax,
        tax_rate=tax_rate(inputs),
        profit=profit,
        unit_margin=unit_margin,
        margin_pct=margin_pct,
        break_even_units=even,
        break_even_revenue=even_revenue,
        safety_pct=safety,
        payback_months=payback,
        scenarios=scenarios,
        regimes=regimes,
        warnings=warnings,
        verdict=verdict,
    )
