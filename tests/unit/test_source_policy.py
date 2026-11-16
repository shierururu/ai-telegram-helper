import pytest

from alpha_course.domain.knowledge import AccessClass, AccessPolicy, classify_source_material

PUBLIC = {
    "article_type": "Public",
    "published": True,
    "is_public": True,
    "is_closed": False,
    "is_deleted": False,
}


def test_public_free_article_can_supply_local_facts() -> None:
    decision = classify_source_material("Articles", **PUBLIC, has_paid_access=False)
    assert decision.policy == AccessPolicy(AccessClass.FREE, factual_allowed=True)


@pytest.mark.parametrize(
    ("field", "value", "access"),
    [
        ("published", False, AccessClass.RESTRICTED),
        ("published", None, AccessClass.UNKNOWN),
        ("published", "published", AccessClass.UNKNOWN),
        ("is_closed", True, AccessClass.RESTRICTED),
        ("is_closed", 0, AccessClass.UNKNOWN),
        ("is_deleted", True, AccessClass.RESTRICTED),
        ("is_deleted", None, AccessClass.UNKNOWN),
        ("has_paid_access", None, AccessClass.UNKNOWN),
        ("has_paid_access", True, AccessClass.UNKNOWN),
    ],
)
def test_uncertain_or_restricted_article_cannot_supply_content(field, value, access) -> None:
    evidence = {**PUBLIC, "has_paid_access": False, field: value}
    decision = classify_source_material("Articles", **evidence, approved_preview=True)
    assert decision.policy == AccessPolicy(access)


@pytest.mark.parametrize("source_kind", ["Lessons", "Glossary", "Documents", "legacy"])
def test_unreviewed_source_kinds_are_excluded(source_kind) -> None:
    decision = classify_source_material(
        source_kind, **PUBLIC, has_paid_access=False, approved_preview=True
    )
    assert decision.policy == AccessPolicy()


@pytest.mark.parametrize("paid", [None, False, "false", 0])
def test_unconfirmed_or_conflicting_course_access_stays_unknown(paid) -> None:
    decision = classify_source_material("Courses", **PUBLIC, has_paid_access=paid)
    assert decision.policy == AccessPolicy()


@pytest.mark.parametrize(("approval", "can_recommend"), [(True, True), (False, False), (1, False)])
def test_verified_paid_course_only_allows_an_approved_preview(approval, can_recommend) -> None:
    decision = classify_source_material(
        "Courses", **PUBLIC, has_paid_access=True, approved_preview=approval
    )
    assert decision.policy == AccessPolicy(AccessClass.PAID, can_recommend=can_recommend)


def test_paid_signal_does_not_override_missing_publication_evidence() -> None:
    decision = classify_source_material("Courses", has_paid_access=True, approved_preview=True)
    assert decision.policy == AccessPolicy()


@pytest.mark.parametrize("editor_flag", [False, None, True])
def test_article_editor_flag_does_not_control_text(editor_flag):
    decision = classify_source_material(
        "Articles", **{**PUBLIC, "is_public": editor_flag}, has_paid_access=False
    )
    assert decision.policy.factual_allowed


@pytest.mark.parametrize("kind", [None, "Paid", "Subscription", "Private", "uuid"])
def test_article_access_requires_verified_public_type(kind):
    decision = classify_source_material(
        "Articles", **{**PUBLIC, "article_type": kind}, has_paid_access=False
    )
    assert not decision.policy.factual_allowed
