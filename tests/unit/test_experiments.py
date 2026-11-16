"""A/B logic: stable assignment, honest statistics and paid-offer copy (D-04)."""

import math

import pytest
from telegram_fakes import material

from alpha_course.application.experiments import (
    PAID_OFFER,
    Experiment,
    analyse,
    assign,
    difference_interval,
    offer_pitch,
    p_value,
    report_lines,
    sample_size,
    wilson,
)
from alpha_course.application.profile import BusinessProfile
from alpha_course.infrastructure.events import user_key


def test_assignment_is_stable_and_shared_by_bot_and_miniapp():
    key = user_key(123456)
    assert {assign(PAID_OFFER, key) for _ in range(5)} == {assign(PAID_OFFER, user_key(123456))}


def test_assignment_is_balanced():
    variants = [assign(PAID_OFFER, user_key(owner)) for owner in range(20000)]
    share = variants.count("B") / len(variants)
    assert 0.48 < share < 0.52


def test_assignment_follows_weights_and_is_independent_per_experiment():
    skewed = Experiment("other", ("A", "B"), (90, 10), "x", "y")
    variants = [assign(skewed, user_key(owner)) for owner in range(20000)]
    assert 0.08 < variants.count("B") / len(variants) < 0.12
    same = sum(
        assign(PAID_OFFER, user_key(o))
        == assign(Experiment("v2", ("A", "B"), (1, 1), "", ""), user_key(o))
        for o in range(4000)
    )
    assert 0.45 < same / 4000 < 0.55


def test_invalid_weights_are_rejected():
    with pytest.raises(ValueError):
        Experiment("bad", ("A", "B"), (100,), "", "")


def test_wilson_interval_known_values():
    lower, upper = wilson(10, 100)
    assert lower == pytest.approx(0.0552, abs=1e-3)
    assert upper == pytest.approx(0.1744, abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def test_difference_interval_and_p_value():
    diff, lower, upper = difference_interval(50, 1000, 80, 1000)
    assert diff == pytest.approx(0.03)
    assert lower > 0 and upper > lower
    assert p_value(50, 1000, 80, 1000) == pytest.approx(0.0060, abs=0.0015)
    assert p_value(5, 100, 5, 100) == pytest.approx(1.0)
    assert p_value(0, 10, 0, 10) == 1.0


def test_sample_size_for_two_points_from_five_percent():
    assert sample_size(0.05, 0.02) == pytest.approx(2210, abs=15)
    assert sample_size(0.05, 0.05) < sample_size(0.05, 0.02)


def test_small_samples_never_claim_a_winner():
    result = analyse({"A": {"users": 40, "clickers": 1}, "B": {"users": 40, "clickers": 10}})
    comparison = result["comparisons"]["B"]
    assert comparison["verdict"] == "insufficient"
    assert comparison["diff"] == pytest.approx(0.225)
    text = "\n".join(report_lines(result))
    assert "данных пока мало" in text
    assert "оплату" in text  # the report says purchases are not observed


def test_large_samples_give_a_verdict():
    better = analyse({"A": {"users": 5000, "clickers": 250}, "B": {"users": 5000, "clickers": 400}})
    assert better["comparisons"]["B"]["verdict"] == "better"
    same = analyse({"A": {"users": 5000, "clickers": 250}, "B": {"users": 5000, "clickers": 255}})
    assert same["comparisons"]["B"]["verdict"] == "no_difference"
    worse = analyse({"A": {"users": 5000, "clickers": 400}, "B": {"users": 5000, "clickers": 250}})
    assert worse["comparisons"]["B"]["verdict"] == "worse"


def test_conversion_is_per_user_not_per_show():
    result = analyse({"A": {"users": 10, "clickers": 1, "shows": 100, "clicks": 9}})
    variant = result["variants"]["A"]
    assert variant["conversion"] == pytest.approx(0.1)
    assert variant["ctr"] == pytest.approx(0.09)
    assert result["variants"]["B"]["conversion"] is None


def test_empty_report_is_readable():
    lines = report_lines(analyse({}), synthetic=True)
    assert "СИНТЕТИКА" in lines[0]
    assert all(isinstance(line, str) for line in lines)
    assert not any(str(math.nan) in line for line in lines)


def test_pitch_uses_public_card_and_profile_only():
    course = material(
        "paid",
        kind="course",
        access="paid",
        price="9 900 ₽",
        summary="Курс про налоги для малого бизнеса. " * 20,
        body_allowed=False,
    )
    pitch = offer_pitch(course, BusinessProfile(legal_form="ИП", tax_regime="УСН"))
    assert pitch["price"] == "9 900 ₽"
    assert len(pitch["benefit"]) <= 221 and pitch["benefit"].endswith("…")
    # The card mentions neither ИП nor УСН: the pitch must not claim a profile match.
    assert "ИП" not in pitch["fit"] and "прямо не отвечает" in pitch["fit"]
    usn = material("usn", kind="course", access="paid", summary="Учёт для ИП на УСН")
    fit = offer_pitch(usn, BusinessProfile(legal_form="ИП", tax_regime="УСН «Доходы»"))["fit"]
    assert fit == "Подобран по профилю: ИП, УСН «Доходы»."
    assert "странице курса" in pitch["note"]
    empty = offer_pitch(material("s", access="subscription", summary="", price="999 ₽"), None)
    assert empty["price"] == "Подписка Курс+"
    assert "999" not in str(empty)
    assert empty["benefit"] == "" and "Профиль" in empty["fit"]


def test_synthetic_simulation_is_labelled_and_balanced():
    from alpha_course.entrypoints.ab_demo import NAME, simulate

    rows = simulate(200, {"A": 0.05, "B": 0.1}, seed=1)
    assert all(row["key"].startswith("synthetic:") for row in rows)
    assigned = [row for row in rows if row["kind"] == "experiment_assigned"]
    variants = [assign(PAID_OFFER, row["key"]) for row in assigned]
    assert variants.count("A") == variants.count("B") == 200
    assert all(NAME in row["payload"] and '"synthetic": true' in row["payload"] for row in rows)
