from datetime import date

from alpha_course.application.tax_calendar import (
    missing_for_calendar,
    regime_explainer,
    tax_deadlines,
)

TODAY = date(2026, 11, 10)


def by_code(items):
    return {item.code: item for item in items}


def test_unknown_regime_returns_nothing():
    assert tax_deadlines("ИП", None, None, today=TODAY) == []
    assert tax_deadlines("ИП", "не знаю", True, today=TODAY) == []
    assert tax_deadlines("пока не зарегистрирован", "УСН «Доходы»", None, today=TODAY) == []


def test_missing_fields():
    assert missing_for_calendar(None, None) == ["legal_form", "tax_regime"]
    assert missing_for_calendar("ИП", "не знаю") == ["tax_regime"]
    assert missing_for_calendar(None, "НПД") == ["legal_form"]
    assert missing_for_calendar("ООО", "ОСНО") == []


def test_sorted_unique_and_in_horizon():
    items = tax_deadlines("ИП", "УСН «Доходы»", True, today=TODAY, horizon_days=200)
    dates = [item.due for item in items]
    assert dates == sorted(dates)
    assert len({item.code for item in items}) == len(items)
    assert all(TODAY <= due <= date(2027, 5, 28) for due in dates)


def test_npd_monthly_count_and_shape():
    items = tax_deadlines("самозанятый", "НПД", None, today=TODAY, horizon_days=365)
    assert 12 <= len(items) <= 13
    assert all(item.kind == "payment" for item in items)
    first = items[0]
    assert first.code == "npd_2026_10"
    assert first.due == date(2026, 11, 30)  # 28.11 - суббота, сдвиг на понедельник


def test_weekend_shift_and_holiday_shift():
    items = by_code(tax_deadlines("ИП", "УСН «Доходы»", None, today=TODAY))
    # 28 мая... нет; 28 июля 2027 - среда, 28 октября 2027 - четверг
    assert items["usn_advance_q2_2027"].due == date(2027, 7, 28)
    # 25 апреля 2027 - воскресенье -> понедельник 26
    assert items["usn_declaration_2027"].due == date(2027, 4, 26)
    # 28 апреля 2027 - среда
    assert items["usn_annual_tax_2027"].due == date(2027, 4, 28)
    # 28 декабря 2026 - понедельник
    assert items["ip_fixed_contrib_2026"].due == date(2026, 12, 28)


def test_holiday_carry_may_2026():
    items = tax_deadlines("ИП", "ОСНО", None, today=date(2026, 4, 1), horizon_days=60)
    assert any(item.due == date(2026, 4, 30) for item in items)
    # 9 мая 2026 - суббота, перенос на 11 мая: срок на 9-е сдвинулся бы на 12-е
    from alpha_course.application.tax_calendar import _shift

    assert _shift(date(2026, 5, 9)) == date(2026, 5, 12)
    assert _shift(date(2026, 3, 8)) == date(2026, 3, 10)
    assert _shift(date(2026, 12, 31)) == date(2027, 1, 11)  # праздники до 8 января + 9-10 выходные


def test_ip_vs_ooo_usn_annual_dates():
    ip = by_code(tax_deadlines("ИП", "УСН «Доходы минус расходы»", None, today=TODAY))
    ooo = by_code(tax_deadlines("ООО", "УСН «Доходы минус расходы»", None, today=TODAY))
    assert ip["usn_annual_tax_2027"].due == date(2027, 4, 28)
    assert ip["usn_declaration_2027"].due == date(2027, 4, 26)
    assert ooo["usn_annual_tax_2027"].due == date(2027, 3, 29)  # 28.03 - воскресенье
    assert ooo["usn_declaration_2027"].due == date(2027, 3, 25)
    assert "ip_fixed_contrib_2026" in ip
    assert "ip_fixed_contrib_2026" not in ooo


def test_usn_advances_and_notices():
    items = by_code(tax_deadlines("ИП", "УСН «Доходы»", None, today=TODAY))
    assert items["usn_advance_q1_2027"].due == date(2027, 4, 28)
    assert items["usn_notice_q1_2027"].due == date(2027, 4, 26)
    assert items["usn_advance_q1_2027"].kind == "payment"
    assert items["usn_notice_q1_2027"].kind == "notice"


def test_ip_one_percent_in_july():
    items = by_code(tax_deadlines("ИП", "УСН «Доходы»", None, today=TODAY))
    assert items["ip_contrib_1pct_2027"].due == date(2027, 7, 1)


def test_ausn_monthly_25th_no_contributions():
    items = tax_deadlines("ИП", "АУСН", None, today=TODAY)
    assert items[0].code == "aucn_2026_10"
    assert items[0].due == date(2026, 11, 25)
    assert all(not item.code.startswith("ip_") for item in items)


def test_psn_has_only_contributions():
    items = tax_deadlines("ИП", "ПСН", None, today=TODAY)
    assert {item.code for item in items} == {
        "ip_fixed_contrib_2026",
        "ip_contrib_1pct_2027",
    }


def test_osno_ip_and_ooo():
    ip = by_code(tax_deadlines("ИП", "ОСНО", None, today=TODAY))
    assert ip["osno_3ndfl_2027"].due == date(2027, 4, 30)
    assert ip["osno_ndfl_advance_q1_2027"].due == date(2027, 4, 28)
    ooo = by_code(tax_deadlines("ООО", "ОСНО", None, today=TODAY))
    assert ooo["vat_declaration_q4_2026"].due == date(2027, 1, 25)
    assert [ooo[f"vat_pay_q4_2026_{n}"].due for n in (1, 2, 3)] == [
        date(2027, 1, 28),
        date(2027, 3, 1),  # 28.02.2027 - воскресенье
        date(2027, 3, 29),
    ]


def test_employees_add_monthly_items():
    without = tax_deadlines("ООО", "УСН «Доходы»", False, today=TODAY)
    with_staff = tax_deadlines("ООО", "УСН «Доходы»", True, today=TODAY)
    extra = by_code(with_staff).keys() - by_code(without).keys()
    pays = [code for code in extra if code.startswith("emp_pay_")]
    assert len(pays) in (12, 13)
    assert any(code.startswith("efs1_") for code in extra)
    assert any(code.startswith("emp_notice_") for code in extra)
    items = by_code(with_staff)
    assert items["emp_6ndfl_q1_2027"].due == date(2027, 4, 26)
    assert items["emp_rsv_q4_2026"].due == date(2027, 1, 25)
    assert items["emp_6ndfl_q4_2026"].due == date(2027, 3, 1)


def test_npd_ignores_employees():
    a = tax_deadlines("самозанятый", "НПД", True, today=TODAY)
    b = tax_deadlines("самозанятый", "НПД", None, today=TODAY)
    assert a == b


def test_texts_are_present():
    for item in tax_deadlines("ООО", "ОСНО", True, today=TODAY):
        assert item.title and item.what and item.kind in {"payment", "report", "notice"}
    for regime in ("НПД", "УСН «Доходы»", "УСН «Доходы минус расходы»", "ПСН", "АУСН", "ОСНО"):
        assert regime_explainer(regime)
    assert "nalog.ru" in regime_explainer(None)
