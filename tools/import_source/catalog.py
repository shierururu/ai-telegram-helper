"""Build a closed local corpus using verified access and exact supplied URLs.

Commercial classification and permission to expose lesson bodies are independent.
Unknown article access and unobserved URLs fail closed; no routes are generated.
"""

import json
import re
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from . import ARCHIVE_SHA256, SourceAuditError, read_registered_archive, run_pg_restore
from .blocks import params_link, params_text

SCHEMA_VERSION = 3
POLICY_VERSION = 4
SITE = "https://kurs.alfabank.ru"
SUBSCRIPTION_URL = f"{SITE}/subscriptions/subscription/"
ROUTES = {
    "article": "articles",
    "course": "courses",
    "glossary": "vocabulary",
    "document": "documents",
    "calculator": "calculators",
    "webinar": "webinars",
    "podcast": "podcasts",
}
TABLES = (
    "Statuses",
    "Articles",
    "ArticleEmbedItems",
    "ArticleSubjects",
    "Courses",
    "CourseTypes",
    "CourseInfoBindings",
    "CourseInfoTypes",
    "CourseLessonBindings",
    "CourseSubscriptions",
    "CourseTariffBindings",
    "CourseUserBindings",
    "Lessons",
    "LessonEmbedItems",
    "Glossary",
    "GlossaryEmbedItems",
    "Documents",
    "DocumentEmbedItems",
    "DocumentTypes",
    "DocumentBindings",
    "Calculators",
    "UserRatings",
    "Webinars",
    "Podcasts",
    "ContentSettings",
    "ContentSettingsTopics",
    "ContentSettingsTags",
    "ContentSettingsIndustries",
    "Topics",
    "Tags",
    "Industries",
    "RecommendedItems",
    "Subscription",
    "SubscriptionTariffs",
)
PUBLISHED = "Опубликовано"
PROMO_INDUSTRIES = {
    "актуальное",
    "альфа-банк",
    "альфа-курс",
    "alfa students",
    "for you",
    "new",
    "популярное",
    "татмолодежь.бизнес",
}
LEVELS = {"без опыта": "novice", "с опытом": "experienced"}
COPY = re.compile(rb'^COPY public\."(\w+)" \(([^\r\n]+)\) FROM stdin;$')
ESCAPE = re.compile(rb"\\([0-7]{1,3}|x[0-9a-fA-F]{1,2}|.)")
TRANSLATIONS = {b"b": b"\b", b"f": b"\f", b"n": b"\n", b"r": b"\r", b"t": b"\t", b"v": b"\v"}
CHUNK_LIMIT = 1100


def _decode(value: bytes) -> str | None:
    if value == rb"\N":
        return None

    def unescape(match: re.Match[bytes]) -> bytes:
        code = match[1]
        if code[0] in b"01234567":
            return bytes([int(code, 8)])
        if len(code) > 1 and code.startswith(b"x"):
            return bytes([int(code[1:], 16)])
        return TRANSLATIONS.get(code, code)

    return ESCAPE.sub(unescape, value).decode("utf-8")


def read_tables(output: bytes) -> dict[str, list[dict[str, str | None]]]:
    tables: dict[str, list[dict[str, str | None]]] = {}
    table = None
    columns: list[str] = []
    for raw in output.split(b"\n"):
        line = raw.removesuffix(b"\r")
        if table is not None:
            if line == rb"\.":
                table = None
                continue
            values = line.split(b"\t")
            if len(values) != len(columns):
                raise SourceAuditError(f"Unexpected COPY row width for {table}.")
            tables[table].append(dict(zip(columns, map(_decode, values), strict=True)))
        elif line.startswith(b"COPY "):
            header = COPY.fullmatch(line)
            if header is None:
                raise SourceAuditError("Unexpected COPY header.")
            table = header[1].decode()
            columns = header[2].decode().replace('"', "").split(", ")
            tables[table] = []
    missing = set(TABLES) - tables.keys()
    if missing:
        raise SourceAuditError("Selected tables are missing: " + ", ".join(sorted(missing)))
    return tables


class _Html(HTMLParser):
    """HTML → readable text that keeps headings, paragraphs and list items."""

    SKIP = {"script", "style", "iframe", "svg", "noscript", "template"}
    BLOCK = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "blockquote", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n\n## ")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in self.BLOCK | {"h1", "h2", "h3", "h4", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).replace("﻿", "").replace("\xa0", " ")
        lines = []
        for line in raw.split("\n"):
            line = " ".join(line.split())
            if line in ("##", "•"):
                continue
            lines.append(line)
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text


def html_text(value: str | None) -> str:
    parser = _Html()
    parser.feed(value or "")
    parser.close()
    return parser.text()


def plain(value: str | None) -> str:
    return " ".join(html_text(value).replace("## ", "").split())


def split_sections(text: str, title: str) -> list[tuple[str, str]]:
    """Split by headings, then by paragraphs into chunks of about CHUNK_LIMIT chars."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for paragraph in re.split(r"\n\s*\n|\n(?=## )", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if paragraph.startswith("## "):
            heading, _, rest = paragraph[3:].partition("\n")
            sections.append((heading.strip(), [rest.strip()] if rest.strip() else []))
        else:
            sections[-1][1].append(paragraph)
    chunks = []
    for heading, paragraphs in sections:
        buffer = ""
        for paragraph in paragraphs:
            for piece in _hard_split(paragraph):
                if buffer and len(buffer) + len(piece) > CHUNK_LIMIT:
                    chunks.append((heading, buffer))
                    buffer = ""
                buffer = f"{buffer}\n{piece}".strip()
        if buffer:
            chunks.append((heading, buffer))
    return [(heading or title, body) for heading, body in chunks if len(body) >= 40]


def _hard_split(paragraph: str) -> list[str]:
    if len(paragraph) <= CHUNK_LIMIT:
        return [paragraph]
    pieces, buffer = [], ""
    for sentence in re.split(r"(?<=[.!?…])\s+", paragraph):
        if buffer and len(buffer) + len(sentence) > CHUNK_LIMIT:
            pieces.append(buffer)
            buffer = ""
        buffer = f"{buffer} {sentence}".strip()
    if buffer:
        pieces.append(buffer)
    return pieces


def _clean_label(value: str | None) -> str:
    text = " ".join((value or "").replace("е", "е").split())
    # Fix Latin homoglyphs that appear in a few catalog labels.
    text = text.translate(str.maketrans("eoaрcx", "еоарсх")) if re.search("[а-я]", text) else text
    return re.sub(r"[^\w\s,.:()+-]", "", text).strip()


def _ordered(rows, key="Order"):
    return sorted(rows, key=lambda row: (int(row.get(key) or 0), row.get("Id") or ""))


def make_catalog(tables: dict) -> dict:
    statuses = {row["Id"]: row["Title"] for row in tables["Statuses"]}
    course_types = {row["Id"]: row["Title"] for row in tables["CourseTypes"]}
    counts: Counter = Counter()

    # Keep exact observed values; matching uses path only, output never reconstructs it.
    observed: dict[tuple[str, str], set[str]] = defaultdict(set)
    subscription_links: set[str] = set()
    for name in (
        "ArticleEmbedItems",
        "GlossaryEmbedItems",
        "DocumentEmbedItems",
        "LessonEmbedItems",
    ):
        for row in tables[name]:
            parser = _Html()
            parser.feed(row.get("HtmlContent") or "")
            candidates = parser.links
            candidate = params_link(row.get("Type"), row.get("Params"))
            if candidate:
                candidates.append(candidate)
            for url in candidates:
                if any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in url):
                    continue
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    continue
                if parsed.scheme != "https" or parsed.netloc != "kurs.alfabank.ru":
                    continue
                # Tracking query parameters are preserved; credentials/tokens are not admitted.
                if re.search(
                    r"(?:token|email|user_?id|password|session|auth)=", parsed.query, re.I
                ):
                    continue
                parts = parsed.path.rstrip("/").split("/")
                if (
                    len(parts) == 3
                    and parts[1] in ROUTES.values()
                    and re.fullmatch(r"[A-Za-z0-9_-]+", parts[2])
                ):
                    observed[parts[1], parts[2]].add(url)
                if parsed.path == "/subscriptions/subscription/":
                    subscription_links.add(url)
    subscription_url = sorted(subscription_links)[0] if subscription_links else None

    def url_for(kind: str, slug: str | None) -> tuple[str | None, bool]:
        candidates = observed.get((ROUTES[kind], slug), set())
        # Ambiguous supplied addresses require review, rather than arbitrary canonicalization.
        return (next(iter(candidates)), True) if len(candidates) == 1 else (None, False)

    # Taxonomy via ContentSettings.
    labels = {
        "topic": {row["Id"]: _clean_label(row["Title"]) for row in tables["Topics"]},
        "tag": {row["Id"]: _clean_label(row["Title"]) for row in tables["Tags"]},
        "industry": {row["Id"]: _clean_label(row["Title"]) for row in tables["Industries"]},
    }
    settings_entity = {
        row["Id"]: row["EntityId"] for row in tables["ContentSettings"] if row["IsDeleted"] == "f"
    }
    taxonomy: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for table, field, kind in (
        ("ContentSettingsTopics", "TopicId", "topic"),
        ("ContentSettingsTags", "TagId", "tag"),
        ("ContentSettingsIndustries", "IndustryId", "industry"),
    ):
        for row in _ordered(tables[table]):
            entity = settings_entity.get(row["ContentSettingsId"])
            label = labels[kind].get(row[field])
            if entity and label and label not in taxonomy[entity][kind]:
                taxonomy[entity][kind].append(label)

    materials: dict[str, dict] = {}
    chunks: list[dict] = []

    def add_material(kind, source, title, summary, *, access="free", extra=None):
        url, url_observed = url_for(kind, source.get("Url"))
        title = plain(title)
        if not title or not url:
            counts[f"{kind}:skipped_without_title_or_url"] += 1
            return None
        tax = taxonomy.get(source["Id"], {})
        industries = [
            label for label in tax.get("industry", []) if label.lower() not in PROMO_INDUSTRIES
        ]
        level = next(
            (LEVELS[label.lower()] for label in industries if label.lower() in LEVELS), None
        )
        industries = [label for label in industries if label.lower() not in LEVELS]
        key = f"{kind}:{source['Id']}"
        summary = plain(summary)
        if len(summary) > 320:
            summary = summary[:317].rsplit(" ", 1)[0] + "…"
        materials[key] = {
            "id": key,
            "kind": kind,
            "title": title,
            "summary": summary,
            "url": url,
            "url_observed": url_observed,
            "access": access,
            "body_allowed": access == "free",
            "topics": tax.get("topic", []),
            "tags": tax.get("tag", []),
            "industries": industries,
            "level": level,
            "popularity": 0,
            "related": [],
            **(extra or {}),
        }
        counts[f"{kind}:admitted"] += 1
        if url_observed:
            counts[f"{kind}:url_observed"] += 1
        return key

    def add_chunks(key: str, title: str, text: str) -> None:
        ordinal = sum(1 for chunk in chunks if chunk["material_id"] == key)
        for heading, body in split_sections(text, title):
            chunks.append(
                {"id": f"{key}:{ordinal}", "material_id": key, "heading": heading, "text": body}
            )
            ordinal += 1

    def blocks(table: str, parent: str) -> dict[str, list[str]]:
        grouped: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        for row in tables[table]:
            content = [row["HtmlContent"]] if row["Type"] == "0" and row.get("HtmlContent") else []
            content.extend(params_text(row["Type"], row.get("Params")))
            if content:
                grouped[row[parent]].append(
                    (int(row["Order"] or 0), row["Id"], "\n\n".join(content))
                )
        result = {}
        for owner, items in grouped.items():
            seen, texts = set(), []
            for _, _, html in sorted(items):
                text = html_text(html)
                if text and text not in seen:
                    seen.add(text)
                    texts.append(text)
            result[owner] = texts
        return result

    # Articles.
    subjects = {row["Id"]: _clean_label(row["Title"]) for row in tables["ArticleSubjects"]}
    article_blocks = blocks("ArticleEmbedItems", "ArticleId")
    for row in tables["Articles"]:
        if not (
            statuses.get(row["StatusId"]) == PUBLISHED
            and row["IsDeleted"] == "f"
            and row["IsClosed"] == "f"
            and course_types.get(row.get("TypeId")) in ("Public", "Открытый")
            and row["IsLesson"] == "f"
        ):
            counts["article:excluded_visibility"] += 1
            continue
        key = add_material(
            "article",
            row,
            row["Title"],
            row["CardDescription"] or row["Description"],
            extra={"duration": plain(row["Duration"]) or None},
        )
        if key:
            subject = subjects.get(row["SubjectId"])
            if subject and subject not in materials[key]["topics"] and subject != "Не про бизнес":
                materials[key]["topics"].append(subject)
            add_chunks(key, materials[key]["title"], "\n\n".join(article_blocks.get(row["Id"], [])))

    # Glossary.
    glossary_blocks = blocks("GlossaryEmbedItems", "GlossaryId")
    for row in tables["Glossary"]:
        if statuses.get(row["StatusId"]) != PUBLISHED or row["IsDeleted"] != "f":
            continue
        body = "\n\n".join(glossary_blocks.get(row["Id"], []))
        summary = row["Description"] or body.split("\n", 1)[0]
        key = add_material("glossary", row, row["Title"], summary)
        if key:
            add_chunks(
                key,
                materials[key]["title"],
                "\n\n".join(filter(None, [plain(row["Description"]), body])),
            )

    # Document templates.
    doc_types = {row["Id"]: _clean_label(row["Title"]) for row in tables["DocumentTypes"]}
    doc_blocks = blocks("DocumentEmbedItems", "DocumentId")
    for row in tables["Documents"]:
        if statuses.get(row["StatusId"]) != PUBLISHED or row["IsDeleted"] != "f":
            continue
        key = add_material(
            "document",
            row,
            row["Title"],
            row["Description"],
            extra={"file_type": (row["Extension"] or "").lower() or None},
        )
        if key:
            doc_type = doc_types.get(row["TypeId"])
            if doc_type and doc_type not in materials[key]["topics"]:
                materials[key]["topics"].append(doc_type)
            text = "\n\n".join([html_text(row["Description"]), *doc_blocks.get(row["Id"], [])])
            add_chunks(key, materials[key]["title"], f"Шаблон документа. {text}")

    # Calculators.
    ratings = Counter(row["EntityId"] for row in tables["UserRatings"] if row["IsDeleted"] == "f")
    for row in tables["Calculators"]:
        if row["IsDeleted"] != "f":
            continue
        key = add_material("calculator", row, row["Title"], row["Description"])
        if key:
            materials[key]["popularity"] = ratings[row["Id"]]
            add_chunks(
                key, materials[key]["title"], f"Онлайн-калькулятор. {plain(row['Description'])}"
            )

    # Webinars and podcasts (descriptions only).
    for kind, table in (("webinar", "Webinars"), ("podcast", "Podcasts")):
        for row in tables[table]:
            if (
                statuses.get(row["StatusId"]) != PUBLISHED
                or row["IsDeleted"] != "f"
                or row.get("IsClosed") == "t"
            ):
                continue
            if kind == "podcast" and course_types.get(row.get("TypeId")) not in (
                "Public",
                "Открытый",
            ):
                continue
            description = " ".join(
                filter(None, [plain(row["Description"]), plain(row.get("DescriptionExt"))])
            )
            key = add_material(kind, row, row["Title"], row["Description"])
            if key:
                add_chunks(key, materials[key]["title"], description)

    # Courses.
    subscription_courses = {row["CourseId"] for row in tables["CourseSubscriptions"]}
    tariff_courses = {row["CourseId"] for row in tables["CourseTariffBindings"]}
    learners = Counter(row["CourseId"] for row in tables["CourseUserBindings"])
    info_types = {row["Id"]: row["Title"] for row in tables["CourseInfoTypes"]}
    info = defaultdict(lambda: defaultdict(list))
    for row in _ordered(tables["CourseInfoBindings"]):
        kind_title = info_types.get(row["CourseInfoTypeId"], "")
        if kind_title in ("Кому подойдет", "Что вы получите от обучения", "Как проходит обучение"):
            text = " — ".join(filter(None, [plain(row["Title"]), plain(row.get("Description"))]))
            if text:
                info[row["CourseId"]][kind_title].append(text)
    lessons = {row["Id"]: row for row in tables["Lessons"]}
    lesson_blocks = blocks("LessonEmbedItems", "LessonId")
    course_lessons = defaultdict(list)
    for row in _ordered(tables["CourseLessonBindings"]):
        if row["IsDeleted"] == "f" and row["IsClosed"] == "f":
            course_lessons[row["CourseId"]].append(row["LessonId"])

    for row in tables["Courses"]:
        if statuses.get(row["StatusId"]) != PUBLISHED or row["IsDeleted"] != "f":
            counts["course:excluded_status"] += 1
            continue
        type_title = course_types.get(row["TypeId"], "")
        cost = int(row["Cost"]) if row["Cost"] else 0
        body_safe = (
            type_title in ("Открытый", "Public")
            and row["Id"] not in subscription_courses
            and row["Id"] not in tariff_courses
            and cost == 0
        )
        if row["Id"] in subscription_courses or type_title in ("По подписке", "Subscription"):
            access = "subscription"  # USER: Курс+ precedes NULL cost.
        elif row["Cost"] is None:
            access = "free"  # Organizer rule outside subscriptions.
        elif type_title in ("Платный", "Paid") or cost > 0 or row["Id"] in tariff_courses:
            access = "paid"
        elif body_safe:
            access = "free"
        else:
            counts["course:excluded_unknown_access"] += 1
            continue
        _, url_observed = url_for("course", row["Url"])
        if row["IsClosed"] == "t" and not url_observed:
            counts["course:excluded_closed"] += 1
            continue
        details = info.get(row["Id"], {})
        extra = {
            "lessons": int(row["LessonQuantity"] or 0) or None,
            "duration": plain(row["Duration"]) or None,
            "audience": list(
                dict.fromkeys(
                    filter(None, [plain(row.get("Target")), *details.get("Кому подойдет", [])])
                )
            )[:3],
            "body_allowed": access == "free" and body_safe and row["IsClosed"] != "t",
            "outcomes": details.get("Что вы получите от обучения", [])[:4],
        }
        if access == "paid" and cost:
            extra["price"] = f"{cost} ₽"
        if access == "subscription":
            extra["purchase_url"] = subscription_url
        key = add_material(
            "course", row, row["Title"], row["Description"], access=access, extra=extra
        )
        if not key:
            continue
        materials[key]["popularity"] = learners[row["Id"]]
        if access != "free" or not body_safe or row["IsClosed"] == "t":
            continue  # Paid and closed courses: card only, no lesson text.
        overview = "\n".join(
            filter(
                None,
                [
                    plain(row["Description"]),
                    plain(row["DescriptionAdditional"]),
                    "Кому подойдёт: " + "; ".join(extra["audience"]) if extra["audience"] else "",
                    "Что получите: " + "; ".join(extra["outcomes"]) if extra["outcomes"] else "",
                ],
            )
        )
        add_chunks(key, materials[key]["title"], f"Бесплатный курс. {overview}")
        for lesson_id in course_lessons.get(row["Id"], []):
            lesson = lessons.get(lesson_id)
            if not lesson or lesson["IsDeleted"] == "t" or lesson["IsGroup"] == "t":
                continue
            text = "\n\n".join(lesson_blocks.get(lesson_id, []))
            if len(text) >= 200:
                add_chunks(key, f"{materials[key]['title']} — {plain(lesson['Title'])}", text)
                counts["course:lesson_text"] += 1

    # Popularity proxies and "what next" links.
    # Source kind is absent: ambiguous source IDs cannot be resolved safely.
    entities = defaultdict(list)
    for material_id in materials:
        entities[material_id.split(":", 1)[1]].append(material_id)
    entity_key = {identifier: keys[0] for identifier, keys in entities.items() if len(keys) == 1}
    entity_kinds = {
        "c8f3a1d4-7e2b-4c9f-a618-5d3e9b0f2a47": "course",
        "142c23e2-bf71-4a73-b12e-6b1b5e4d47cc": "podcast",
        "8ede4179-0faf-407d-a4fc-1d94f9a63089": "article",
    }
    related = defaultdict(list)
    for row in _ordered(tables["RecommendedItems"]):
        source = entity_key.get(row["Id"])
        target_kind = entity_kinds.get(row.get("ItemTypeId"))
        target = f"{target_kind}:{row['ItemId']}" if target_kind else None
        if target not in materials:
            target = None
        if source and target and source != target and target not in related[source]:
            related[source].append(target)
    for row in _ordered(tables["DocumentBindings"]):
        source = f"document:{row['DocumentId']}"
        target = f"document:{row['LinkedDocumentId']}"
        if source not in materials or target not in materials:
            continue
        if source and target and source != target and target not in related[source]:
            related[source].append(target)
    for key, material in materials.items():
        material["related"] = related.get(key, [])[:6]

    with_text = {chunk["material_id"] for chunk in chunks}
    report = {
        "supported_content_fields": [
            "Type0.HtmlContent",
            "Type0.Params.text",
            "Type2.Params.title",
            "Type2.Params.description",
            "Type4.Params.title",
            "Type4.Params.items[str]",
        ],
        "params_text_policy": "reviewed whitelist; SEARCH_INDEX_FIELDS not supplied",
        "learner_count_meaning": "course registrations, not purchases or completion",
        "materials": len(materials),
        "chunks": len(chunks),
        "by_kind": dict(Counter(item["kind"] for item in materials.values())),
        "by_access": dict(Counter(item["access"] for item in materials.values())),
        "materials_with_text": len(with_text),
        "decisions": dict(sorted(counts.items())),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "source_sha256": ARCHIVE_SHA256,
        "site": SITE,
        "subscription_url": subscription_url,
        "materials": sorted(materials.values(), key=lambda item: item["id"]),
        "chunks": chunks,
        "report": report,
    }


def prepare_catalog(archive: Path, destination: Path, pg_restore: str) -> dict:
    snapshot = read_registered_archive(archive)
    output = run_pg_restore(
        pg_restore,
        snapshot,
        "--data-only",
        "--schema=public",
        "--file=-",
        "--no-owner",
        "--no-privileges",
        *(f"--table={table}" for table in TABLES),
    )
    try:
        corpus = make_catalog(read_tables(output))
    except (UnicodeError, ValueError, KeyError) as error:
        raise SourceAuditError("The selected source contains unexpected data.") from error
    temporary = None
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=destination.parent, encoding="utf-8", mode="w", delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(corpus, file, ensure_ascii=False, separators=(",", ":"))
            file.write("\n")
        temporary.chmod(0o600)
        temporary.replace(destination)
    except OSError as error:
        raise SourceAuditError("Could not write the derived corpus.") from error
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return corpus["report"]
