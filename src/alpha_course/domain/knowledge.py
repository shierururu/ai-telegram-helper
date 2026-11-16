"""Immutable knowledge contracts; permissions fail closed."""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class AccessClass(StrEnum):
    FREE = "free"
    PAID = "paid"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    CLARIFY = "clarify"
    NO_EVIDENCE = "no_evidence"
    OUT_OF_SCOPE = "out_of_scope"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AccessPolicy:
    access_class: AccessClass = AccessClass.UNKNOWN
    factual_allowed: bool = False
    external_allowed: bool = False
    can_recommend: bool = False

    def __post_init__(self) -> None:
        if self.access_class != AccessClass.FREE and self.factual_allowed:
            raise ValueError("Only approved free content may be used for factual answers")
        if self.external_allowed and not self.factual_allowed:
            raise ValueError("External permission requires factual permission")
        if self.can_recommend and self.access_class not in (AccessClass.FREE, AccessClass.PAID):
            raise ValueError("Only approved free or paid previews may be recommended")


SOURCE_POLICY_VERSION = 3


@dataclass(frozen=True)
class SourcePolicyDecision:
    policy: AccessPolicy
    reason_code: str


def classify_source_material(
    source_kind: str,
    *,
    published: bool | None = None,
    is_public: bool | None = None,
    is_closed: bool | None = None,
    is_deleted: bool | None = None,
    has_paid_access: bool | None = None,
    approved_preview: bool = False,
    article_type: str | None = None,
) -> SourcePolicyDecision:
    """Evaluate normalized source evidence before admitting content to the local corpus.

    The importer resolves publication and visibility for each supported source kind.
    An absent, invalid, or conflicting commercial signal must stay unknown.
    Preview approval requires reviewed public metadata and a verified source URL.
    """
    if source_kind not in ("Articles", "Courses"):
        return SourcePolicyDecision(AccessPolicy(), "unsupported_source_kind")

    if published is False or is_closed is True or is_deleted is True:
        return SourcePolicyDecision(AccessPolicy(AccessClass.RESTRICTED), "not_publicly_available")

    if not (published is True and is_closed is False and is_deleted is False):
        return SourcePolicyDecision(AccessPolicy(), "unverified_visibility")

    if source_kind == "Courses":
        if has_paid_access is True:
            return SourcePolicyDecision(
                AccessPolicy(AccessClass.PAID, can_recommend=approved_preview is True),
                "paid_course_preview_only",
            )
        return SourcePolicyDecision(AccessPolicy(), "unverified_course_access")

    if article_type != "Public" or has_paid_access is not False:
        return SourcePolicyDecision(AccessPolicy(), "unverified_article_access")

    return SourcePolicyDecision(
        AccessPolicy(
            AccessClass.FREE, factual_allowed=True, can_recommend=approved_preview is True
        ),
        "public_free_article",
    )


@dataclass(frozen=True)
class Material:
    id: str
    source_kind: str
    source_id: str
    title: str
    policy: AccessPolicy
    provenance: str
    revision: int = 1
    approved_summary: str | None = None
    verified_url: str | None = None

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("Material revision must be positive")
        if self.verified_url is not None and not self.provenance.strip():
            raise ValueError("Verified URLs require source provenance")
        if self.verified_url is not None:
            # Syntax validation does not prove membership in the approved source registry.
            value = self.verified_url
            try:
                parsed = urlsplit(value)
                valid = (
                    parsed.scheme in ("http", "https")
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and not any(
                        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value
                    )
                )
                parsed.port  # Validate port syntax without making a network request.
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Verified URL must be an absolute HTTP(S) URL without userinfo")


@dataclass(frozen=True)
class Chunk:
    id: str
    material_id: str
    material_revision: int
    ordinal: int
    text: str
    source_item_ids: tuple[str, ...]
    policy: AccessPolicy

    def __post_init__(self) -> None:
        if self.material_revision < 1 or self.ordinal < 0:
            raise ValueError("Invalid chunk revision or ordinal")
        if not self.policy.factual_allowed:
            raise ValueError("QA chunks require factual permission")


@dataclass(frozen=True)
class EvidenceRef:
    chunk_id: str
    material_id: str
    material_revision: int

    def __post_init__(self) -> None:
        if self.material_revision < 1:
            raise ValueError("Evidence revision must be positive")


@dataclass(frozen=True)
class Answer:
    status: AnswerStatus
    text: str
    evidence: tuple[EvidenceRef, ...] = ()
    recommendations: tuple[Material, ...] = ()
    clarification: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status == AnswerStatus.ANSWERED and not self.evidence:
            raise ValueError("An answered result requires evidence")
        if self.status == AnswerStatus.CLARIFY and not (
            self.clarification and self.clarification.strip()
        ):
            raise ValueError("A clarification result requires a question")
