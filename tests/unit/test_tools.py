"""Financial model arithmetic and API: numbers in code, personal data stays local."""

from __future__ import annotations

import json

import pytest

from alpha_course.application.finmodel import FinmodelError, _profit, calculate, parse_inputs
from tests.unit.test_webapp_games import FakeModel, make_rig


def base(**extra):
    return {"price": 300, "unit_cost": 100, "units": 500, "fixed": 40000, **extra}


# --------------------------------------------------------------------------- arithmetic
def test_usn_income_numbers():
    result = calculate(parse_inputs(base(regime="usn_income")))
    assert result.revenue == 150000 and result.variable == 50000
    assert result.tax == pytest.approx(9000)  # 6% of revenue, no payroll
    assert result.profit == pytest.approx(150000 - 50000 - 40000 - 9000)
    assert result.unit_margin == 200 and round(result.margin_pct, 1) == 66.7


def test_break_even_is_the_first_profitable_volume():
    inputs = parse_inputs(base(regime="usn_income"))
    result = calculate(inputs)
    even = result.break_even_units
    assert even is not None and even > 0
    assert _profit(inputs, even)[0] >= 0 > _profit(inputs, even - 1)[0]
    assert result.safety_pct is not None and result.safety_pct > 0


def test_usn_income_expense_uses_base_and_minimum_tax():
    result = calculate(parse_inputs(base(regime="usn_income_expense")))
    assert result.tax == pytest.approx((150000 - 50000 - 40000) * 0.15)
    loss = calculate(parse_inputs(base(regime="usn_income_expense", fixed=500000)))
    assert loss.tax == pytest.approx(loss.revenue * 0.01) and loss.profit < 0
    assert loss.verdict == "loss"
    assert any("минимальный налог" in item for item in loss.warnings)


def test_payroll_adds_contributions_and_reduces_usn_income_tax():
    result = calculate(parse_inputs(base(regime="usn_income", payroll=60000)))
    assert result.contributions == pytest.approx(18000)
    assert result.fixed_total == pytest.approx(40000 + 60000 + 18000)
    # contributions reduce the tax by at most half of it
    assert result.tax == pytest.approx(9000 - min(18000, 4500))


def test_npd_rules_and_warnings():
    person = calculate(parse_inputs(base(regime="npd", client="person")))
    company = calculate(parse_inputs(base(regime="npd", client="company")))
    assert person.tax == pytest.approx(150000 * 0.04) and company.tax == pytest.approx(9000)
    over = calculate(parse_inputs(base(regime="npd", units=1000)))
    assert any("2,4 млн" in item for item in over.warnings)
    staff = calculate(parse_inputs(base(regime="npd", payroll=1000)))
    assert any("сотрудников" in item for item in staff.warnings)
    npd = next(item for item in staff.regimes if item["regime"] == "npd")
    assert npd["available"] is False


def test_price_below_cost_has_no_break_even():
    result = calculate(parse_inputs({"price": 100, "unit_cost": 150, "units": 10}))
    assert result.break_even_units is None and result.profit < 0
    assert any("убыток" in item for item in result.warnings)


def test_payback_and_scenarios():
    result = calculate(parse_inputs(base(investment=300000)))
    assert result.payback_months == pytest.approx(300000 / result.profit)
    labels = [item["label"] for item in result.scenarios]
    assert any("−30%" in item for item in labels) and any("Цена +10%" in item for item in labels)
    profits = [item["profit"] for item in result.scenarios[:3]]
    assert profits[2] > profits[1] > profits[0]
    assert sum(1 for item in result.regimes if item.get("best")) == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"price": 0},
        {"price": "abc"},
        {"units": -1},
        {"regime": "x"},
        {"rate": 600},
        {"price": None},
    ],
)
def test_invalid_inputs_are_rejected(patch):
    with pytest.raises(FinmodelError):
        parse_inputs(base() | patch)


def test_numbers_accept_spaces_and_commas():
    assert parse_inputs(base(price="1 250,5")).price == 1250.5


# --------------------------------------------------------------------------- api
async def test_finmodel_api_calc_and_meta():
    rig = await make_rig(None)
    async with rig:
        status, body = await rig.call("POST", "/api/finmodel/calc", base(regime="usn_income"))
        assert status == 200 and body["profit"] == 51000 and body["regime_label"].startswith("УСН")
        status, body = await rig.call("POST", "/api/finmodel/calc", {"price": 0})
        assert status == 400 and body["error"] == "invalid_input"
        status, body = await rig.call("GET", "/api/finmodel/meta")
        values = {item["value"] for item in body["regimes"]}
        assert status == 200 and values == {"npd", "usn_income", "usn_income_expense"}


async def test_finmodel_extract_masks_text_and_keeps_numbers_only():
    model = FakeModel(
        {
            "price": 250,
            "unit_cost": 80,
            "units": 600,
            "fixed": 60000,
            "payroll": None,
            "investment": "x",
            "client": "person",
        }
    )
    rig = await make_rig(model)
    async with rig:
        status, body = await rig.call(
            "POST",
            "/api/finmodel/extract",
            {"text": "кофе по 250, себестоимость 80, телефон +7 999 123-45-67"},
        )
    assert status == 200
    assert body["fields"] == {
        "price": 250,
        "unit_cost": 80,
        "units": 600,
        "fixed": 60000,
        "client": "person",
    }
    assert "999" not in json.dumps(model.calls[0][1], ensure_ascii=False)


async def test_finmodel_extract_unavailable_without_model():
    rig = await make_rig(None)
    async with rig:
        status, _ = await rig.call("POST", "/api/finmodel/extract", {"text": "кофе"})
    assert status == 503


# --------------------------------------------------------------------------- tools hub
async def test_tools_list_has_no_documents_and_needs_auth():
    rig = await make_rig(None)
    async with rig:
        status, body = await rig.call("GET", "/api/tools")
        assert status == 200 and {item["id"] for item in body["tools"]} == {"finmodel"}
        status, _ = await rig.call("GET", "/api/documents")
        assert status == 404
        response = await rig.client.get("/api/tools")
        assert response.status == 401
