"""Hybrid retrieval over a tiny synthetic knowledge base."""

import json
from pathlib import Path

import pytest

from alpha_course.application.knowledge import Chunk, KnowledgeBase, Material, tokens
from alpha_course.application.profile import BusinessProfile


def _material(identifier: str, title: str, **kwargs) -> Material:
    values = {
        "kind": "article",
        "summary": "",
        "url": f"https://example.test/{identifier}",
        "access": "free",
        "url_observed": True,
    }
    values.update(kwargs)
    return Material(id=identifier, title=title, **values)


MATERIALS = [
    _material("tax", "Налоги для ИП", topics=("Налоги",), popularity=50),
    _material("ads", "Реклама в соцсетях", topics=("Маркетинг",), popularity=10),
    _material(
        "cafe",
        "Учет в кафе",
        topics=("Учет",),
        industries=("HoReCa",),
        popularity=5,
    ),
    _material(
        "shop",
        "Учет в магазине",
        topics=("Учет",),
        industries=("Ритейл",),
        popularity=5,
    ),
    _material(
        "paid",
        "Курс по финансовой модели",
        kind="course",
        access="paid",
        summary="Научитесь строить финансовую модель бизнеса",
        popularity=100,
    ),
    _material("term", "Что такое амортизация", kind="glossary", topics=("Налоги",), popularity=900),
]
CHUNKS = [
    Chunk("tax-1", "tax", "Сроки", "Налоги платят по расписанию, налогов бывает много."),
    Chunk("tax-2", "tax", "Ставки", "Ставка налога зависит от режима."),
    Chunk("tax-3", "tax", "Отчеты", "Отчетность по налогам сдают раз в квартал."),
    Chunk("ads-1", "ads", "Таргет", "Настройте рекламу в соцсетях на узкую аудиторию."),
    Chunk("cafe-1", "cafe", "Учет", "Ведите учет продаж и склада."),
    Chunk("shop-1", "shop", "Учет", "Ведите учет продаж и склада."),
    Chunk("term-1", "term", "Определение", "Амортизация налоги основные средства."),
]


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase(MATERIALS, CHUNKS)


class FakeEmbedder:
    """Deterministic 2-d vectors: revenue-like cards point along x, others along y."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, text: str, *, query: bool = False) -> list[float]:
        self.calls += 1
        return [1.0, 0.0] if "реклам" in text.lower() else [0.0, 1.0]


def test_tokens_share_stem_across_inflections() -> None:
    assert len({tokens(word)[0] for word in ("налоги", "налогов", "налогами")}) == 1


def test_tokens_drop_stop_words_and_normalize_yo() -> None:
    assert tokens("как и что") == []
    assert tokens("счёт") == tokens("счет")


def test_bm25_ranks_relevant_fragment_first(kb: KnowledgeBase) -> None:
    ranked = kb.bm25("реклама в соцсетях")
    assert kb.chunks[ranked[0][0]].id == "ads-1"
    assert kb.bm25("и в на") == []


def test_search_limits_two_fragments_per_material(kb: KnowledgeBase) -> None:
    result = kb.search("налоги")
    per_material = [chunk.material_id for chunk, _ in result.chunks]
    assert per_material.count("tax") == 2


def test_search_finds_paid_card_by_title_without_chunks(kb: KnowledgeBase) -> None:
    result = kb.search("финансовая модель")
    assert result.chunks == []
    assert result.materials[0][0].id == "paid"
    assert result.materials[0][0].is_paid


def test_profile_industry_boosts_matching_material(kb: KnowledgeBase) -> None:
    plain = dict((m.id, s) for m, s in kb.search("учет продаж").materials)
    boosted = dict(
        (m.id, s)
        for m, s in kb.search("учет продаж", profile=BusinessProfile(industry="общепит")).materials
    )
    assert plain["cafe"] == pytest.approx(plain["shop"])
    assert boosted["cafe"] > boosted["shop"]
    cafe_rank = [
        m.id
        for m, _ in kb.search("учет продаж", profile=BusinessProfile(industry="общепит")).materials
    ]
    assert cafe_rank.index("cafe") < cafe_rank.index("shop")


def test_search_with_no_matches_is_empty(kb: KnowledgeBase) -> None:
    result = kb.search("квантовая хромодинамика")
    assert result.chunks == []
    assert result.materials == []


def test_semantic_scores_require_vectors(kb: KnowledgeBase) -> None:
    assert not kb.semantic_ready
    assert kb.semantic_scores([1.0, 0.0]) == {}


async def test_build_vectors_caches_and_reloads(kb: KnowledgeBase, tmp_path: Path) -> None:
    embedder = FakeEmbedder()
    await kb.build_vectors(embedder, tmp_path)
    assert kb.semantic_ready
    assert embedder.calls == len(MATERIALS)
    assert list(tmp_path.glob("materials-*.npz"))
    assert not list(tmp_path.glob("*.partial.json"))

    fresh = KnowledgeBase(MATERIALS, CHUNKS)
    assert not fresh.semantic_ready
    assert fresh.load_vectors(tmp_path) is True
    assert fresh.semantic_ready
    # A second build is served from the cache without calling the embedder.
    again = FakeEmbedder()
    await KnowledgeBase(MATERIALS, CHUNKS).build_vectors(again, tmp_path)
    assert again.calls == 0


def test_load_vectors_missing_or_other_corpus_returns_false(
    kb: KnowledgeBase, tmp_path: Path
) -> None:
    assert kb.load_vectors(tmp_path) is False
    assert kb.load_vectors(tmp_path / "absent") is False


async def test_semantic_scores_order_and_help_search(kb: KnowledgeBase, tmp_path: Path) -> None:
    await kb.build_vectors(FakeEmbedder(), tmp_path)
    scores = kb.semantic_scores([1.0, 0.0])
    assert max(scores, key=scores.get) == "ads"
    assert scores["ads"] > scores["tax"]
    # A query with no lexical overlap still surfaces the semantically close material.
    result = kb.search("продвижение бизнеса", query_vector=[1.0, 0.0])
    assert result.materials[0][0].id == "ads"


async def test_partial_embedding_failure_does_not_publish_vectors(tmp_path: Path) -> None:
    class Flaky(FakeEmbedder):
        async def embed(self, text: str, *, query: bool = False) -> list[float]:
            if "Налоги для ИП" in text:
                raise RuntimeError("synthetic outage")
            return await super().embed(text, query=query)

    kb = KnowledgeBase(MATERIALS, CHUNKS)
    await kb.build_vectors(Flaky(), tmp_path)
    assert not kb.semantic_ready
    assert not list(tmp_path.glob("materials-*.npz"))
    assert list(tmp_path.glob("*.partial.json"))


def test_starter_pack_excludes_glossary_and_respects_limit(kb: KnowledgeBase) -> None:
    pack = kb.starter_pack(BusinessProfile(stage="запуск", goal="налоги и учет"), limit=3)
    assert 0 < len(pack) <= 3
    assert all(item.kind != "glossary" for item in pack)
    assert len({item.id for item in pack}) == len(pack)


def _write_corpus(path: Path, schema: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": schema,
                "policy_version": 4,
                "subscription_url": "https://example.test/sub",
                "materials": [
                    {
                        "id": "m1",
                        "kind": "article",
                        "title": "Синтетическая статья",
                        "url": "https://example.test/m1",
                        "access": "free",
                        "body_allowed": True,
                        "topics": ["Налоги"],
                    }
                ],
                "chunks": [{"id": "c1", "material_id": "m1", "text": "Синтетический текст"}],
            }
        ),
        encoding="utf-8",
    )


def test_from_path_loads_current_schema(tmp_path: Path) -> None:
    _write_corpus(tmp_path / "corpus.json", 3)
    loaded = KnowledgeBase.from_path(tmp_path / "corpus.json")
    assert loaded.materials["m1"].topics == ("Налоги",)
    assert loaded.subscription_url == "https://example.test/sub"
    assert loaded.search("синтетический текст").chunks[0][0].id == "c1"


def test_from_path_rejects_other_schema(tmp_path: Path) -> None:
    _write_corpus(tmp_path / "corpus.json", 1)
    with pytest.raises(ValueError, match="Unsupported corpus schema"):
        KnowledgeBase.from_path(tmp_path / "corpus.json")


@pytest.mark.parametrize(
    "access,kind,body_allowed",
    [
        ("paid", "article", True),
        ("subscription", "course", True),
        ("unknown", "article", True),
        ("free", "unknown", True),
        ("free", "course", False),
    ],
)
def test_closed_or_unknown_body_never_enters_index(access, kind, body_allowed):
    item = _material("blocked", "Secret", access=access, kind=kind, body_allowed=body_allowed)
    kb = KnowledgeBase([item], [Chunk("secret", "blocked", "", "Private secret body")])
    assert kb.chunks == []
    assert kb.search("Private secret body").chunks == []


def test_current_schema_without_body_admission_is_closed(tmp_path):
    path = tmp_path / "corpus.json"
    _write_corpus(path, 3)
    payload = json.loads(path.read_text())
    payload["materials"][0].pop("body_allowed")
    path.write_text(json.dumps(payload))
    assert KnowledgeBase.from_path(path).chunks == []


def test_old_policy_is_rejected_even_with_current_schema(tmp_path):
    path = tmp_path / "corpus.json"
    _write_corpus(path, 3)
    payload = json.loads(path.read_text())
    payload["policy_version"] = 3
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Unsupported corpus schema"):
        KnowledgeBase.from_path(path)


async def test_embeddings_exclude_closed_bodies_and_mask_identifiers(tmp_path):
    allowed = _material("open", "Business", summary="Иван Петров продажи")
    blocked = _material("closed", "Course", kind="course", access="paid")
    kb = KnowledgeBase(
        [allowed, blocked],
        [
            Chunk("open-body", "open", "", "Продажи owner@example.test"),
            Chunk("closed-body", "closed", "", "FORBIDDEN CLOSED LESSON"),
        ],
    )
    sent = []

    class Embedder:
        async def embed(self, text, **kwargs):
            sent.append(text)
            return [1.0, 0.0]

    await kb.build_vectors(Embedder(), tmp_path)
    payload = " ".join(sent)
    assert "FORBIDDEN" not in payload
    assert "owner@example.test" not in payload
    assert "Иван Петров" not in payload


def test_vector_signature_changes_when_body_or_policy_admission_changes():
    item = _material("m", "Business")
    first = KnowledgeBase([item], [Chunk("c", "m", "", "Original safe body")])
    changed = KnowledgeBase([item], [Chunk("c", "m", "", "Changed safe body")])
    closed = KnowledgeBase([_material("m", "Business", body_allowed=False)], [])
    assert len({first.signature, changed.signature, closed.signature}) == 3


async def test_closed_and_unknown_bodies_never_enter_retrieval_or_embeddings(tmp_path):
    paid = _material("closed", "Налоги", access="subscription", kind="course")
    restricted = _material("restricted", "Налоги", access="unknown")
    kb = KnowledgeBase(
        [paid, restricted],
        [
            Chunk("secret", "closed", "", "PRIVATE PAID BODY"),
            Chunk("other", "restricted", "", "PRIVATE UNKNOWN BODY"),
            Chunk("orphan", "missing", "", "PRIVATE ORPHAN BODY"),
        ],
    )
    assert not kb.chunks and not kb.by_material
    assert not kb.search("PRIVATE").chunks

    class RecordingEmbedder:
        async def embed(self, text, **kwargs):
            assert "PRIVATE" not in text
            return [1.0, 0.0]

    await kb.build_vectors(RecordingEmbedder(), tmp_path)


def test_paid_course_searches_whole_catalog_and_uses_confirmed_profile():
    cafe = _material("cafe-course", "Учет", kind="course", access="paid", industries=("общепит",))
    shop = _material(
        "shop-course", "Учет", kind="course", access="subscription", industries=("розница",)
    )
    broken = _material(
        "broken", "Учет розница", kind="course", access="paid", url="javascript:alert(1)"
    )
    kb = KnowledgeBase([cafe, shop, broken], [])
    assert kb.paid_course("учет", BusinessProfile(industry="розница")) == shop
    assert kb.paid_course("учет", BusinessProfile(industry="общепит")) == cafe
    assert KnowledgeBase([broken], []).paid_course("учет") is None


def test_unobserved_paid_card_is_ineligible_even_with_valid_url():
    item = _material("unobserved", "Налоги", kind="course", access="paid", url_observed=False)
    kb = KnowledgeBase([item], [])
    assert item.has_safe_url
    assert not item.recommendable_paid_course
    assert not kb.search("налоги").materials
    assert kb.paid_course("налоги") is None
