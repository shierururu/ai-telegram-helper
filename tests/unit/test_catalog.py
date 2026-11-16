import pytest

from tools.import_source import SourceAuditError, catalog


def test_html_text_keeps_headings_and_lists():
    text = catalog.html_text(
        "<h2>Заголовок</h2><p>Первый&nbsp;абзац</p><ul><li>один</li><li>два</li></ul>"
        "<script>alert(1)</script>"
    )
    assert "## Заголовок" in text
    assert "Первый абзац" in text
    assert "• один" in text
    assert "• два" in text
    assert "alert" not in text
    assert catalog.html_text(None) == ""


def test_split_sections_limits_size_attaches_headings_and_drops_short():
    long_paragraph = " ".join(f"Предложение номер {number} про налоги." for number in range(120))
    intro = "Вступительный абзац достаточно длинный для сохранения."
    text = f"{intro}\n\n## Налоги\n{long_paragraph}\n\n## Пустое\nкоротко"
    chunks = catalog.split_sections(text, "Статья")
    assert chunks[0] == ("Статья", "Вступительный абзац достаточно длинный для сохранения.")
    tax = [body for heading, body in chunks if heading == "Налоги"]
    assert len(tax) > 1
    assert all(len(body) <= catalog.CHUNK_LIMIT for _, body in chunks)
    assert all(heading != "Пустое" for heading, _ in chunks)


BS = chr(92)


def _copy(name, columns, *rows):
    header = f'COPY public."{name}" ({", ".join(columns)}) FROM stdin;'
    return "\n".join([header, *rows, r"\.", ""])


def test_read_tables_decodes_escapes_and_nulls():
    parts = [_copy(name, ["Id", "Title"], "1\tname") for name in catalog.TABLES if name != "Tags"]
    escaped = "1	a" + BS + "tb" + BS + "nc" + BS + BS + "d"
    null_row = "2	" + BS + "N"
    parts.append(_copy("Tags", ["Id", "Title"], escaped, null_row))
    tables = catalog.read_tables("\n".join(parts).encode())
    assert tables["Tags"] == [
        {"Id": "1", "Title": "a\tb\nc" + BS + "d"},
        {"Id": "2", "Title": None},
    ]
    assert tables["Topics"] == [{"Id": "1", "Title": "name"}]


def test_read_tables_reports_missing_tables_and_bad_width():
    only_topics = _copy("Topics", ["Id", "Title"], "1\tx").encode()
    with pytest.raises(SourceAuditError, match="missing"):
        catalog.read_tables(only_topics)
    with pytest.raises(SourceAuditError, match="width"):
        catalog.read_tables(_copy("Topics", ["Id", "Title"], "1").encode())


SLUGS = {
    "articles": "free-article",
    "courses": "open-course",
    "vocabulary": "term",
    "documents": "template",
    "calculators": "calc",
    "webinars": "webinar",
    "podcasts": "podcast",
}


def _tables():
    tables = {name: [] for name in catalog.TABLES}
    links = " ".join(f'<a href="https://kurs.alfabank.ru/{r}/{s}/">x</a>' for r, s in SLUGS.items())
    links += ' <a href="https://kurs.alfabank.ru/subscriptions/subscription/">sub</a><a href="https://kurs.alfabank.ru/courses/paid-course/">paid</a>'
    body = "<h2>Как начать</h2><p>" + "Подробный текст про бизнес и первые шаги. " * 4 + "</p>"
    flags = {
        "IsDeleted": "f",
        "IsClosed": "f",
        "IsPublic": "t",
        "IsLesson": "f",
        "TypeId": "t-public",
    }
    tables["Statuses"] = [
        {"Id": "pub", "Title": "Опубликовано"},
        {"Id": "draft", "Title": "Черновик"},
    ]
    tables["Articles"] = [
        {
            **flags,
            "Id": "a1",
            "Title": "Свободная статья",
            "CardDescription": "Кратко о старте",
            "Description": None,
            "Duration": "5 мин",
            "Url": "free-article",
            "StatusId": "pub",
            "SubjectId": None,
        },
        {
            **flags,
            "Id": "a2",
            "Title": "Черновик статьи",
            "CardDescription": "Не опубликовано",
            "Description": None,
            "Duration": None,
            "Url": "draft-article",
            "StatusId": "draft",
            "SubjectId": None,
        },
    ]
    tables["ArticleEmbedItems"] = [
        {
            "Id": "e1",
            "Type": "0",
            "ArticleId": "a1",
            "Order": "1",
            "HtmlContent": body + links,
            "Params": None,
        },
        {
            "Id": "e2",
            "Type": "0",
            "ArticleId": "a2",
            "Order": "1",
            "HtmlContent": body,
            "Params": None,
        },
    ]
    tables["CourseTypes"] = [
        {"Id": "t-public", "Title": "Public"},
        {"Id": "t-open", "Title": "Открытый"},
        {"Id": "t-sub", "Title": "По подписке"},
    ]

    def course(identifier, title, type_id, url):
        return {
            "Id": identifier,
            "Title": title,
            "StatusId": "pub",
            "IsDeleted": "f",
            "IsClosed": "f",
            "TypeId": type_id,
            "Cost": None,
            "Url": url,
            "LessonQuantity": "1",
            "Duration": None,
            "Description": "Описание пользы курса для предпринимателя",
            "DescriptionAdditional": None,
        }

    tables["Courses"] = [
        course("c1", "Открытый курс", "t-open", "open-course"),
        course("c2", "Курс по подписке", "t-sub", "paid-course"),
    ]
    tables["CourseSubscriptions"] = [{"CourseId": "c2"}]
    tables["Lessons"] = [{"Id": "l1", "Title": "Урок 1", "IsDeleted": "f", "IsGroup": "f"}]
    binding = {"LessonId": "l1", "Order": "1", "IsDeleted": "f", "IsClosed": "f"}
    tables["CourseLessonBindings"] = [
        {**binding, "Id": "b1", "CourseId": "c1"},
        {**binding, "Id": "b2", "CourseId": "c2"},
    ]
    tables["LessonEmbedItems"] = [
        {
            "Id": "le1",
            "Type": "0",
            "LessonId": "l1",
            "Order": "1",
            "HtmlContent": "<p>" + "Текст урока про учёт доходов и расходов. " * 8 + "</p>",
            "Params": None,
        }
    ]
    tables["SubscriptionTariffs"] = [{"Cost": "990", "DiscountCost": None}]
    return tables


def test_make_catalog_applies_access_policy():
    corpus = catalog.make_catalog(_tables())
    materials = {item["id"]: item for item in corpus["materials"]}
    chunk_owners = {chunk["material_id"] for chunk in corpus["chunks"]}

    assert corpus["schema_version"] == catalog.SCHEMA_VERSION
    assert "article:a2" not in materials  # draft excluded

    article = materials["article:a1"]
    assert article["access"] == "free"
    assert article["url"] == "https://kurs.alfabank.ru/articles/free-article/"
    assert article["url_observed"] is True
    assert "article:a1" in chunk_owners

    paid = materials["course:c2"]
    assert paid["access"] == "subscription"
    assert paid["url"] == "https://kurs.alfabank.ru/courses/paid-course/"
    assert paid["url_observed"] is True
    assert paid["purchase_url"] == catalog.SUBSCRIPTION_URL
    assert "course:c2" not in chunk_owners
    assert corpus["subscription_url"] == catalog.SUBSCRIPTION_URL

    open_course = materials["course:c1"]
    assert open_course["access"] == "free"
    assert open_course["url_observed"] is True
    lesson_chunks = [c for c in corpus["chunks"] if c["material_id"] == "course:c1"]
    assert any("учёт доходов" in chunk["text"] for chunk in lesson_chunks)


def test_make_catalog_requires_observed_routes():
    tables = _tables()
    tables["ArticleEmbedItems"][0]["HtmlContent"] = "<p>без ссылок</p>"
    corpus = catalog.make_catalog(tables)
    assert corpus["materials"] == []
    assert corpus["chunks"] == []


@pytest.mark.parametrize("type_title", ["Paid", "Private", "Subscription", "Unknown"])
def test_non_public_article_type_never_supplies_text(type_title):
    tables = _tables()
    tables["CourseTypes"][0]["Title"] = type_title
    corpus = catalog.make_catalog(tables)
    assert not any(c["material_id"] == "article:a1" for c in corpus["chunks"])


def test_editor_flag_does_not_block_public_article():
    tables = _tables()
    tables["Articles"][0]["IsPublic"] = "f"
    corpus = catalog.make_catalog(tables)
    assert any(c["material_id"] == "article:a1" for c in corpus["chunks"])


def test_unknown_article_uuid_fails_closed():
    tables = _tables()
    tables["Articles"][0]["TypeId"] = "unmapped"
    assert not any(m["kind"] == "article" for m in catalog.make_catalog(tables)["materials"])


def test_exact_url_keeps_query_and_absent_trailing_slash():
    tables = _tables()
    original = "https://kurs.alfabank.ru/articles/free-article/"
    exact = "https://kurs.alfabank.ru/articles/free-article?utm_source=synthetic"
    tables["ArticleEmbedItems"][0]["HtmlContent"] = tables["ArticleEmbedItems"][0][
        "HtmlContent"
    ].replace(original, exact)
    corpus = catalog.make_catalog(tables)
    assert next(m for m in corpus["materials"] if m["id"] == "article:a1")["url"] == exact


def test_params_private_fields_are_never_searchable():
    tables = _tables()
    tables["ArticleEmbedItems"][0]["Params"] = (
        '{"private":"PRIVATE_SENTINEL", "image":{"text":"UNREVIEWED_SENTINEL"}}'
    )
    corpus = catalog.make_catalog(tables)
    assert all("SENTINEL" not in c["text"] for c in corpus["chunks"])


def test_manual_recommendations_keep_typed_links_without_popularity():
    tables = _tables()
    tables["RecommendedItems"] = [
        {
            "Id": "c1",
            "ItemId": "a1",
            "ItemTypeId": "8ede4179-0faf-407d-a4fc-1d94f9a63089",
            "Order": "1",
        }
    ]
    materials = {m["id"]: m for m in catalog.make_catalog(tables)["materials"]}
    assert materials["course:c1"]["related"] == ["article:a1"]
    assert materials["article:a1"]["popularity"] == 0
    tables["RecommendedItems"][0]["ItemTypeId"] = "unknown"
    materials = {m["id"]: m for m in catalog.make_catalog(tables)["materials"]}
    assert materials["course:c1"]["related"] == []


def test_reviewed_params_fields_only():
    from tools.import_source.blocks import params_text

    assert params_text("0", '{"text":"visible", "filePath":"private"}') == ["visible"]
    assert params_text("2", '{"title":"title", "description":"body", "private":"secret"}') == [
        "title",
        "body",
    ]
    assert params_text("4", '{"title":"list", "items":["first","second"]}') == [
        "list",
        "first",
        "second",
    ]
    assert params_text("4", '{"items":[{"private":"secret"}]}') == []
    assert params_text("99", '{"text":"unknown"}') == []


def test_subscription_never_uses_subscription_tariff_price():
    tables = _tables()
    tables["Courses"][1]["Cost"] = "100"
    tables["CourseSubscriptions"] = [{"CourseId": "c2", "SubscriptionId": "own"}]
    tables["SubscriptionTariffs"] = [
        {"SubscriptionId": "other", "Cost": "1", "DiscountCost": None},
        {"SubscriptionId": "own", "Cost": "900", "DiscountCost": "700"},
    ]
    materials = {m["id"]: m for m in catalog.make_catalog(tables)["materials"]}
    assert "price" not in materials["course:c2"]
    assert materials["course:c2"]["body_allowed"] is False


def test_null_cost_subscription_is_not_free_and_has_no_body():
    corpus = catalog.make_catalog(_tables())
    course = next(m for m in corpus["materials"] if m["id"] == "course:c2")
    assert course["access"] == "subscription"
    assert course["body_allowed"] is False
    assert not any(c["material_id"] == course["id"] for c in corpus["chunks"])
