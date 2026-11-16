"""Exercise the transport against a local HTTP endpoint with synthetic data."""

from collections.abc import AsyncIterator

import pytest
from aiohttp import web

from alpha_course.infrastructure import yandex
from alpha_course.infrastructure.yandex import YandexAI, YandexDeepSeek, YandexError

FOLDER = "synthetic-folder"
MODEL = f"gpt://{FOLDER}/deepseek-v4.1-flash/latest"
KEY = "synthetic-api-key"
PAYLOAD = '{"topic":"synthetic"}'


@pytest.fixture
async def provider(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict]:
    state = {
        "discoveries": 0,
        "requests": [],
        "embeddings": [],
        "status": 200,
        "statuses": [],
        "embed_statuses": [],
        "content": " synthetic reply ",
    }

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(yandex.asyncio, "sleep", no_sleep)

    async def models(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        state["discoveries"] += 1
        return web.json_response(
            {"data": [{"id": "other-model"}, {"id": f"gpt://{FOLDER}/yandexgpt/latest"}]}
        )

    async def complete(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        state["requests"].append(await request.json())
        status = state["statuses"].pop(0) if state["statuses"] else state["status"]
        if status != 200:
            return web.Response(status=status, text=f"{KEY} {PAYLOAD}")
        return web.json_response({"choices": [{"message": {"content": state["content"]}}]})

    async def embeddings(request: web.Request) -> web.Response:
        state["embeddings"].append(await request.json())
        status = state["embed_statuses"].pop(0) if state["embed_statuses"] else 200
        if status != 200:
            return web.Response(status=status)
        return web.json_response({"data": [{"embedding": [1, 0.5, 0]}]})

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", complete)
    app.router.add_post("/v1/embeddings", embeddings)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        monkeypatch.setattr(yandex, "_API_URL", f"http://127.0.0.1:{port}/v1")
        yield state
    finally:
        await runner.cleanup()


def test_legacy_alias_points_to_new_client() -> None:
    assert YandexDeepSeek is YandexAI


async def test_discovers_folder_and_caches_it(provider: dict) -> None:
    client = YandexAI(KEY)
    try:
        assert await client.complete("Synthetic instruction", PAYLOAD) == "synthetic reply"
        assert await client.complete("Synthetic instruction", PAYLOAD) == "synthetic reply"
    finally:
        await client.close()
    assert provider["discoveries"] == 1
    assert len(provider["requests"]) == 2
    assert provider["requests"][0]["model"] == MODEL
    assert provider["requests"][0]["messages"] == [
        {"role": "system", "content": "Synthetic instruction"},
        {"role": "user", "content": PAYLOAD},
    ]
    assert "response_format" not in provider["requests"][0]


async def test_explicit_folder_skips_discovery_and_model_override(provider: dict) -> None:
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        await client.complete("Synthetic instruction", PAYLOAD, model="yandexgpt")
    finally:
        await client.close()
    assert provider["discoveries"] == 0
    assert provider["requests"][0]["model"] == f"gpt://{FOLDER}/yandexgpt/latest"


async def test_json_mode_sets_response_format(provider: dict) -> None:
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        await client.complete("i", PAYLOAD, json_mode=True, temperature=0.3, max_tokens=50)
    finally:
        await client.close()
    request = provider["requests"][0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["temperature"] == 0.3
    assert request["max_tokens"] == 50


async def test_reasoning_is_disabled_by_default_and_configurable(provider: dict) -> None:
    for effort, expected in (("none", "none"), ("low", "low"), ("default", None)):
        client = YandexAI(KEY, folder_id=FOLDER, reasoning_effort=effort)
        try:
            await client.complete("i", PAYLOAD)
        finally:
            await client.close()
        assert provider["requests"][-1].get("reasoning_effort") == expected
    assert YandexAI(KEY)._reasoning == "none"
    with pytest.raises(ValueError):
        YandexAI(KEY, reasoning_effort="max")


async def test_usage_is_logged_without_content(provider: dict, caplog) -> None:
    caplog.set_level("INFO", logger="alpha_course.infrastructure.yandex")
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        await client.complete("Synthetic instruction", PAYLOAD, max_tokens=50)
    finally:
        await client.close()
    line = next(record.getMessage() for record in caplog.records if "llm_call" in record.message)
    assert "max_tokens=50" in line
    assert "synthetic" not in line.lower().replace("deepseek", "")


@pytest.mark.parametrize("status", [429, 503])
async def test_transient_errors_are_retried_then_succeed(provider: dict, status: int) -> None:
    provider["statuses"] = [status, status]
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        assert await client.complete("i", PAYLOAD) == "synthetic reply"
    finally:
        await client.close()
    assert len(provider["requests"]) == 3


async def test_persistent_rate_limit_gives_safe_error(provider: dict) -> None:
    provider["status"] = 429
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        with pytest.raises(YandexError, match=r"^Yandex completion rate-limited$") as error:
            await client.complete("i", PAYLOAD)
    finally:
        await client.close()
    assert len(provider["requests"]) == 3
    assert KEY not in str(error.value)
    assert PAYLOAD not in str(error.value)


async def test_client_error_is_not_retried_and_is_safe(provider: dict) -> None:
    provider["status"] = 400
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        with pytest.raises(YandexError, match=r"^Yandex completion failed \(HTTP 400\)$"):
            await client.complete("i", PAYLOAD)
    finally:
        await client.close()
    assert len(provider["requests"]) == 1


async def test_empty_completion_is_rejected(provider: dict) -> None:
    provider["content"] = " "
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        with pytest.raises(YandexError, match="empty completion"):
            await client.complete("i", PAYLOAD)
    finally:
        await client.close()


async def test_missing_key_fails_without_network() -> None:
    with pytest.raises(YandexError, match="Missing Yandex API key"):
        await YandexAI("  ").complete("i", PAYLOAD)
    with pytest.raises(YandexError, match="Missing Yandex API key"):
        await YandexAI("").embed("text")


async def test_embed_uses_doc_and_query_models(provider: dict) -> None:
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        assert await client.embed("synthetic text") == [1.0, 0.5, 0.0]
        await client.embed("synthetic question", query=True)
    finally:
        await client.close()
    doc, query = provider["embeddings"]
    assert doc == {
        "model": f"emb://{FOLDER}/text-embeddings-v2-doc/latest",
        "input": "synthetic text",
    }
    assert query["model"] == f"emb://{FOLDER}/text-embeddings-v2-query/latest"
    assert isinstance(query["input"], str)


async def test_embed_retries_rate_limit(provider: dict) -> None:
    provider["embed_statuses"] = [429, 429]
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        assert await client.embed("synthetic text")
    finally:
        await client.close()
    assert len(provider["embeddings"]) == 3


async def test_embed_gives_up_after_attempts(provider: dict) -> None:
    provider["embed_statuses"] = [429] * 10
    client = YandexAI(KEY, folder_id=FOLDER)
    try:
        with pytest.raises(YandexError, match="embedding unavailable"):
            await client.embed("synthetic text", attempts=2)
    finally:
        await client.close()
    assert len(provider["embeddings"]) == 2
