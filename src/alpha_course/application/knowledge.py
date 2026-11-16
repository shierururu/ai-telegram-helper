"""Knowledge base: corpus v2 loading and hybrid retrieval.

Lexical BM25 over text fragments (with Russian stemming) is always available and
works offline. When an embedding provider is configured, material-level vectors
(title + summary + taxonomy) add semantic recall, so informal questions like
«как поднять бабла» still find revenue materials. Vectors are cached on disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import numpy as np
import snowballstemmer

from alpha_course.application.privacy import mask_personal
from alpha_course.application.profile import BusinessProfile

logger = logging.getLogger(__name__)

KIND_LABELS = {
    "article": "статья",
    "course": "курс",
    "glossary": "термин",
    "document": "шаблон документа",
    "calculator": "калькулятор",
    "webinar": "вебинар",
    "podcast": "подкаст",
}
KIND_ICONS = {
    "article": "📄",
    "course": "🎓",
    "glossary": "📖",
    "document": "📝",
    "calculator": "🧮",
    "webinar": "🎥",
    "podcast": "🎧",
}
INDUSTRY_HINTS = {
    "розница": ("розница", "ретейл"),
    "общепит": ("horeca", "общепит"),
    "услуги": ("услуги",),
    "онлайн-торговля и маркетплейсы": ("ecom", "маркетплейсы", "marketplace"),
    "производство": ("производство",),
    "строительство": ("строительство и недвижимость", "строительство"),
    "логистика": ("транспорт и логистика", "логистика"),
    "оптовая торговля": ("оптовая торговля", "b2b"),
}
_WORD = re.compile(r"[a-zа-я0-9]+", re.I)
_STOP = set(
    """
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне
    было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до
    вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя
    их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого
    какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда
    можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая
    много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им
    более всегда конечно всю между это мой свой своего нужно хочу подскажи расскажи пожалуйста
    как-то какие какое каких который которая которые также очень просто
    """.split()
)
_stemmer = snowballstemmer.stemmer("russian")


_STEMS: dict[str, str] = {}


def _stem(word: str) -> str:
    stem = _STEMS.get(word)
    if stem is None:
        stem = _stemmer.stemWord(word) if len(word) > 3 else word
        if len(_STEMS) < 500_000:
            _STEMS[word] = stem
    return stem


def tokens(text: str) -> list[str]:
    stems = (
        _stem(word) for word in _WORD.findall(text.lower().replace("ё", "е")) if word not in _STOP
    )
    return [stem for stem in stems if len(stem) > 1 or stem.isdigit()]


def _safe_url(url: str | None) -> bool:
    try:
        parsed = urlsplit(url or "")
        return bool(
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not re.search(r"[\s<>\"']", url or "")
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class Material:
    id: str
    kind: str
    title: str
    summary: str
    url: str
    access: str
    topics: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    level: str | None = None
    popularity: int = 0
    related: tuple[str, ...] = ()
    duration: str | None = None
    lessons: int | None = None
    price: str | None = None
    purchase_url: str | None = None
    audience: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    url_observed: bool = False
    body_allowed: bool = True

    @property
    def is_paid(self) -> bool:
        return self.access in ("paid", "subscription")

    @property
    def has_safe_url(self) -> bool:
        return _safe_url(self.url)

    @property
    def recommendable_paid_course(self) -> bool:
        """Only an observed source link can enable a paid course recommendation.

        A purchase link, when the catalog has one, is opened by the paid-offer button too,
        so it must be a well-formed absolute link as well (D-04: never repaired or built).
        """
        return (
            self.kind == "course"
            and self.is_paid
            and self.url_observed
            and self.has_safe_url
            and (not self.purchase_url or _safe_url(self.purchase_url))
        )

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, "материал")

    @property
    def icon(self) -> str:
        return KIND_ICONS.get(self.kind, "📄")

    def card_text(self) -> str:
        """Public metadata only; safe for the external model and for paid items."""
        parts = [self.title]
        if self.summary:
            parts.append(self.summary)
        if self.topics or self.tags:
            parts.append("Темы: " + ", ".join((*self.topics, *self.tags)[:6]))
        return ". ".join(parts)


@dataclass(frozen=True)
class Chunk:
    id: str
    material_id: str
    heading: str
    text: str


@dataclass
class SearchResult:
    chunks: list[tuple[Chunk, float]] = field(default_factory=list)
    materials: list[tuple[Material, float]] = field(default_factory=list)


class Embedder(Protocol):
    async def embed(self, text: str, *, query: bool = False) -> list[float]: ...


class KnowledgeBase:
    def __init__(
        self, materials: list[Material], chunks: list[Chunk], *, subscription_url: str | None = None
    ) -> None:
        self.materials = {item.id: item for item in materials}
        # Closed bodies never become evidence, including manually assembled/corrupt corpora.
        chunks = [
            chunk
            for chunk in chunks
            if chunk.material_id in self.materials
            and self.materials[chunk.material_id].access == "free"
            and self.materials[chunk.material_id].body_allowed
            and self.materials[chunk.material_id].kind in KIND_LABELS
        ]
        self.chunks = chunks
        self.subscription_url = subscription_url
        self.by_material: dict[str, list[Chunk]] = defaultdict(list)
        for chunk in chunks:
            self.by_material[chunk.material_id].append(chunk)
        # BM25 over fragments; the title and heading are indexed with extra weight.
        self._docs: list[Counter] = []
        lengths = []
        df: Counter = Counter()
        for chunk in chunks:
            material = self.materials[chunk.material_id]
            words = tokens(chunk.text) + 2 * tokens(material.title) + tokens(chunk.heading)
            counts = Counter(words)
            self._docs.append(counts)
            lengths.append(len(words))
            df.update(counts.keys())
        self._avg = (sum(lengths) / len(lengths)) if lengths else 1.0
        self._lengths = lengths
        total = max(len(chunks), 1)
        self._idf = {term: math.log(1 + (total - n + 0.5) / (n + 0.5)) for term, n in df.items()}
        self._postings: dict[str, list[int]] = defaultdict(list)
        for index, counts in enumerate(self._docs):
            for term in counts:
                self._postings[term].append(index)
        # Material-level lexical index (cards), used when a material has no text.
        self._card_terms = {
            item.id: Counter(tokens(item.card_text()) + 2 * tokens(item.title))
            for item in materials
        }
        self._vectors: np.ndarray | None = None
        self._vector_ids: list[str] = []
        self.signature = hashlib.sha256(
            json.dumps(
                {
                    "policy_version": 4,
                    "cards": sorted(
                        (item.id, item.card_text(), item.access, item.kind, item.body_allowed)
                        for item in materials
                    ),
                    "chunks": sorted((chunk.id, chunk.material_id, chunk.text) for chunk in chunks),
                },
                ensure_ascii=False,
            ).encode()
        ).hexdigest()[:16]

    # ---------- loading ----------
    @classmethod
    def from_path(cls, path: Path | str) -> KnowledgeBase:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != 3 or payload.get("policy_version") != 4:
            raise ValueError(
                "Unsupported corpus schema; rebuild with `python -m tools.import_source --prepare`"
            )
        materials = []
        for row in payload["materials"]:
            materials.append(
                Material(
                    id=row["id"],
                    kind=row["kind"],
                    title=row["title"],
                    summary=row.get("summary") or "",
                    url=row["url"],
                    access=row["access"],
                    topics=tuple(row.get("topics") or ()),
                    tags=tuple(row.get("tags") or ()),
                    industries=tuple(row.get("industries") or ()),
                    level=row.get("level"),
                    popularity=int(row.get("popularity") or 0),
                    related=tuple(row.get("related") or ()),
                    duration=row.get("duration"),
                    lessons=row.get("lessons"),
                    price=row.get("price"),
                    purchase_url=row.get("purchase_url"),
                    audience=tuple(row.get("audience") or ()),
                    outcomes=tuple(row.get("outcomes") or ()),
                    url_observed=bool(row.get("url_observed")),
                    body_allowed=row.get("body_allowed") is True,
                )
            )
        chunks = [
            Chunk(row["id"], row["material_id"], row.get("heading") or "", row["text"])
            for row in payload["chunks"]
        ]
        return cls(materials, chunks, subscription_url=payload.get("subscription_url"))

    # ---------- embeddings ----------
    @property
    def semantic_ready(self) -> bool:
        return self._vectors is not None

    def load_vectors(self, directory: Path) -> bool:
        path = directory / f"materials-{self.signature}.npz"
        try:
            data = np.load(path, allow_pickle=False)
            ids = [str(item) for item in data["ids"]]
            vectors = data["vectors"].astype(np.float32)
        except OSError, KeyError, ValueError:
            return False
        if set(ids) != set(self.materials):
            return False
        self._vector_ids, self._vectors = ids, vectors
        return True

    async def build_vectors(self, embedder: Embedder, directory: Path) -> None:
        """Embed every material card (public metadata only) and cache the matrix."""
        if self.load_vectors(directory):
            logger.info("knowledge_vectors_loaded count=%s", len(self._vector_ids))
            return
        partial_path = directory / f"materials-{self.signature}.partial.json"
        done: dict[str, list[float]] = {}
        try:
            done = json.loads(partial_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            done = {}
        pending = [item for item in self.materials.values() if item.id not in done]
        logger.info("knowledge_vectors_build pending=%s cached=%s", len(pending), len(done))

        async def one(item: Material) -> None:
            text = item.card_text()
            if item.access == "free" and item.id in self.by_material:
                text += ". " + self.by_material[item.id][0].text[:600]
            done[item.id] = await embedder.embed(mask_personal(text, limit=6000))

        for start in range(0, len(pending), 40):
            batch = pending[start : start + 40]
            results = await asyncio.gather(*(one(item) for item in batch), return_exceptions=True)
            failures = sum(isinstance(result, Exception) for result in results)
            if failures:
                logger.warning("knowledge_vectors_batch_failures=%s", failures)
            try:
                directory.mkdir(parents=True, exist_ok=True)
                partial_path.write_text(json.dumps(done), encoding="utf-8")
            except OSError:
                logger.warning("knowledge_vectors_partial_save_failed")
        if set(done) != set(self.materials):
            logger.warning("knowledge_vectors_incomplete have=%s", len(done))
            return
        ids = sorted(done)
        matrix = np.array([done[item] for item in ids], dtype=np.float32)
        matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
        try:
            np.savez(
                directory / f"materials-{self.signature}.npz", ids=np.array(ids), vectors=matrix
            )
            partial_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("knowledge_vectors_save_failed")
        self._vector_ids, self._vectors = ids, matrix
        logger.info("knowledge_vectors_ready count=%s", len(ids))

    def semantic_scores(self, query_vector: list[float] | None) -> dict[str, float]:
        if self._vectors is None or not query_vector:
            return {}
        vector = np.asarray(query_vector, dtype=np.float32)
        vector /= max(float(np.linalg.norm(vector)), 1e-9)
        scores = self._vectors @ vector
        return {self._vector_ids[i]: float(scores[i]) for i in np.argsort(-scores)[:60]}

    # ---------- search ----------
    def bm25(self, query: str, limit: int = 80) -> list[tuple[int, float]]:
        terms = set(tokens(query))
        if not terms:
            return []
        scores: dict[int, float] = defaultdict(float)
        k1, b = 1.4, 0.7
        for term in terms:
            idf = self._idf.get(term)
            if not idf:
                continue
            for index in self._postings[term]:
                tf = self._docs[index][term]
                norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * self._lengths[index] / self._avg))
                scores[index] += idf * norm
        # Reward fragments that cover more distinct query terms.
        for index in scores:
            covered = sum(1 for term in terms if term in self._docs[index])
            scores[index] *= 0.6 + 0.4 * covered / len(terms)
        return sorted(scores.items(), key=lambda row: -row[1])[:limit]

    def search(
        self,
        query: str,
        *,
        query_vector: list[float] | None = None,
        profile: BusinessProfile | None = None,
        fragments: int = 8,
        candidates: int = 10,
    ) -> SearchResult:
        lexical = self.bm25(query)
        semantic = self.semantic_scores(query_vector)
        top_lexical = lexical[0][1] if lexical else 1.0
        material_score: dict[str, float] = defaultdict(float)
        chunk_scores: dict[int, float] = {}
        for index, score in lexical:
            chunk = self.chunks[index]
            relevance = score / top_lexical
            chunk_scores[index] = relevance
            material_score[chunk.material_id] = max(material_score[chunk.material_id], relevance)
        # Semantic similarity is relative; 0.45 is roughly "unrelated" for this model.
        for material_id, similarity in semantic.items():
            boost = max(0.0, (similarity - 0.45) / 0.3)
            material_score[material_id] = material_score[material_id] * 0.7 + boost * 0.9
        # Cards without text (paid courses) are matched by their public metadata.
        query_terms = set(tokens(query))
        if query_terms:
            for material_id, counts in self._card_terms.items():
                covered = sum(1 for term in query_terms if term in counts)
                if covered:
                    card = covered / len(query_terms)
                    material_score[material_id] = max(material_score[material_id], card * 0.8)
        for material_id in list(material_score):
            material_score[material_id] *= self._profile_factor(
                self.materials[material_id], profile
            )

        # Fragments: best lexical chunks plus the best chunk of strong semantic materials.
        ranked_chunks: dict[int, float] = {}
        for index, relevance in chunk_scores.items():
            chunk = self.chunks[index]
            ranked_chunks[index] = 0.65 * relevance + 0.35 * material_score[chunk.material_id]
        chunk_index = {chunk.id: i for i, chunk in enumerate(self.chunks)}
        for material_id, similarity in list(semantic.items())[:12]:
            if similarity < 0.55:
                continue
            own = self.by_material.get(material_id)
            if not own:
                continue
            best = max(
                own[:20],
                key=lambda chunk: sum(
                    1 for term in query_terms if term in self._docs[chunk_index[chunk.id]]
                ),
            )
            index = chunk_index[best.id]
            ranked_chunks[index] = max(
                ranked_chunks.get(index, 0.0), material_score[material_id] * 0.8
            )
        per_material: Counter = Counter()
        selected = []
        for index, score in sorted(ranked_chunks.items(), key=lambda row: -row[1]):
            chunk = self.chunks[index]
            if per_material[chunk.material_id] >= 2:
                continue
            per_material[chunk.material_id] += 1
            selected.append((chunk, score))
            if len(selected) >= fragments:
                break
        materials = sorted(
            (
                (self.materials[key], score)
                for key, score in material_score.items()
                if score > 0.05
                and (
                    not self.materials[key].is_paid or self.materials[key].recommendable_paid_course
                )
            ),
            key=lambda row: (-row[1], -row[0].popularity, row[0].id),
        )
        return SearchResult(selected, self._diverse(materials, candidates))

    @staticmethod
    def _profile_factor(material: Material, profile: BusinessProfile | None) -> float:
        if profile is None:
            return 1.0
        factor = 1.0
        if profile.industry:
            hints = INDUSTRY_HINTS.get(profile.industry, (profile.industry.lower(),))
            labels = " ".join(material.industries + material.topics).lower()
            if any(hint in labels for hint in hints):
                factor += 0.15
        if profile.level and material.level:
            factor += 0.1 if profile.level == material.level else -0.05
        if profile.stage in ("идея", "запуск") and any(
            topic in ("Старт бизнеса", "Первые шаги", "Регистрация бизнеса")
            for topic in material.topics
        ):
            factor += 0.1
        return factor

    @staticmethod
    def _diverse(rows: list[tuple[Material, float]], limit: int) -> list[tuple[Material, float]]:
        result, kinds = [], Counter()
        for material, score in rows:
            if kinds[material.kind] >= 4:
                continue
            kinds[material.kind] += 1
            result.append((material, score))
            if len(result) >= limit:
                break
        return result

    def glossary_for(self, text: str, limit: int = 4) -> list[Material]:
        """Free glossary entries whose term occurs in the text (stem match), shortest first."""
        if not hasattr(self, "_glossary_index"):
            self._glossary_index = [
                (tuple(tokens(item.title)), item)
                for item in self.materials.values()
                if item.kind == "glossary" and not item.is_paid and item.summary
            ]
        words = tokens(text)
        found: list[Material] = []
        for key, item in self._glossary_index:
            if not key or len(" ".join(key)) < 4:
                continue
            width = len(key)
            if any(tuple(words[i : i + width]) == key for i in range(len(words) - width + 1)):
                found.append(item)
        found.sort(key=lambda item: len(item.title))
        return found[:limit]

    def paid_course(
        self,
        query: str,
        profile: BusinessProfile | None = None,
        exclude: frozenset[str] = frozenset(),
    ) -> Material | None:
        """Stable full-catalog selection using public cards and confirmed profile only."""
        from alpha_course.application.offers import choose_course

        offer = choose_course(self, query, profile, exclude)
        return offer.material if offer else None

    def starter_pack(
        self, profile: BusinessProfile, limit: int = 5, exclude: frozenset[str] = frozenset()
    ) -> list[Material]:
        """Cold start: popular, level-appropriate materials for the user's stage and sphere."""
        query = " ".join(
            filter(
                None,
                [
                    {
                        "идея": "с чего начать бизнес идея проверка ниши бизнес-план",
                        "запуск": "открыть бизнес регистрация ИП первые клиенты",
                        "работает до года": "первые клиенты продажи учет налоги",
                        "развитие": "развитие бизнеса масштабирование команда маркетинг",
                    }.get(profile.stage or "", "первые шаги в бизнесе"),
                    profile.industry if profile.industry not in (None, "другое") else "",
                    profile.goal or "",
                ],
            )
        )
        result = self.search(query, profile=profile, candidates=60 if exclude else 30)
        picked: list[Material] = []
        kinds: Counter = Counter()
        for material, _ in sorted(
            result.materials,
            key=lambda row: -(row[1] + min(row[0].popularity, 2000) / 4000),
        ):
            if material.id in exclude or material.kind == "glossary" or kinds[material.kind] >= 2:
                continue
            kinds[material.kind] += 1
            picked.append(material)
            if len(picked) >= limit:
                break
        # Same query as Assistant.learning_plan, so the route never holds two different picks.
        paid = self.paid_course(profile.goal or "", profile, exclude)
        if paid and not any(item.id == paid.id for item in picked):
            picked = [*picked[: limit - 1], paid] if limit > 0 else []
        return picked
