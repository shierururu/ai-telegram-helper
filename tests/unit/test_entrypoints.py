"""Process boundaries must keep secrets private and release resources."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from alpha_course.application.knowledge import KnowledgeBase
from alpha_course.entrypoints import bot, common
from alpha_course.infrastructure.config import ConfigurationError
from alpha_course.infrastructure.lifecycle import idle_until_stopped


@pytest.mark.asyncio
async def test_database_engine_disposed_after_failure(monkeypatch):
    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(common, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(common, "check_database", AsyncMock(side_effect=RuntimeError("secret")))
    with pytest.raises(RuntimeError):
        await common.verify_database(object())
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [ConfigurationError("private-token"), RuntimeError("private-dsn")]
)
def test_safe_failure_does_not_print_exception(monkeypatch, caplog, capsys, error):
    def fail():
        raise error

    monkeypatch.setattr(common, "load_settings", fail)
    assert common.execute(AsyncMock()) == 2
    output = caplog.text + str(capsys.readouterr())
    assert "private-token" not in output
    assert "private-dsn" not in output
    assert "startup_failed code=" in output


@pytest.mark.asyncio
async def test_bot_check_needs_no_token_or_network(monkeypatch):
    settings = SimpleNamespace(require_bot_token=MagicMock(side_effect=AssertionError()))
    check = AsyncMock()
    monkeypatch.setattr(bot, "technical_check", check)
    monkeypatch.setattr(bot, "Bot", MagicMock(side_effect=AssertionError()))
    await bot.run(settings, check=True)
    check.assert_awaited_once_with(settings, "bot")
    settings.require_bot_token.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, SecretStr("test-api-key")])
async def test_bot_closes_session_when_polling_fails(monkeypatch, key):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.delete_my_commands = AsyncMock()
    client.set_my_commands = AsyncMock()
    engine = SimpleNamespace(dispose=AsyncMock())
    dispatcher = SimpleNamespace(
        message=SimpleNamespace(middleware=MagicMock()),
        callback_query=SimpleNamespace(middleware=MagicMock()),
        include_router=MagicMock(), start_polling=AsyncMock(side_effect=RuntimeError())
    )
    monkeypatch.setattr(bot, "Bot", lambda **_: client)
    monkeypatch.setattr(bot, "Dispatcher", lambda **_: dispatcher)
    monkeypatch.setattr(bot, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(bot, "check_database", AsyncMock())
    adapter = MagicMock()
    adapter.return_value.close = AsyncMock()
    monkeypatch.setattr(bot, "YandexAI", adapter)
    knowledge = KnowledgeBase([], [])
    from_path = MagicMock(return_value=knowledge)
    monkeypatch.setattr(bot.KnowledgeBase, "from_path", from_path)
    vectors = MagicMock(return_value=True)
    monkeypatch.setattr(knowledge, "load_vectors", vectors)
    with pytest.raises(RuntimeError):
        await bot.run(
            SimpleNamespace(
                require_bot_token=lambda: "test-only",
                corpus_path=Path("synthetic-corpus.json"),
                index_dir=Path("synthetic-index"),
                yandex_api_key=key,
                yandex_folder_id="test-folder",
                yandex_timeout_seconds=5,
                yandex_reasoning_effort="none",
                answer_deadline_seconds=20,
                admin_set=frozenset,
                webapp_url=None,
                webapp_dev=False,
                webapp_port=8080,
            )
        )
    from_path.assert_called_once_with(Path("synthetic-corpus.json"))
    if key is None:
        adapter.assert_not_called()
        vectors.assert_not_called()
    else:
        adapter.assert_called_once_with(
            "test-api-key", folder_id="test-folder", timeout_seconds=5, reasoning_effort="none"
        )
        adapter.return_value.close.assert_awaited_once()
        vectors.assert_called_once_with(Path("synthetic-index"))
    dispatcher.message.middleware.assert_called_once()
    dispatcher.callback_query.middleware.assert_called_once()
    assert dispatcher.include_router.call_count == 6
    client.set_my_commands.assert_awaited_once()
    assert [call.kwargs["scope"].type for call in client.delete_my_commands.await_args_list] == [
        "default"
    ]
    assert client.set_my_commands.await_args.kwargs["scope"].type == "all_private_chats"
    client.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_idle_worker_stops_promptly():
    stop = asyncio.Event()
    task = asyncio.create_task(idle_until_stopped(stop, 3600))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_bot_rejects_missing_corpus_without_leaking_path_or_token(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "create_database_engine", MagicMock(side_effect=AssertionError()))
    settings = SimpleNamespace(
        require_bot_token=lambda: "private-token", corpus_path=tmp_path / "absent.json"
    )
    with pytest.raises(ConfigurationError) as error:
        await bot.run(settings)
    assert "private-token" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_bot_starts_webapp_and_sets_menu_button(monkeypatch, automatic):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.delete_my_commands = AsyncMock()
    client.set_my_commands = AsyncMock()
    client.set_chat_menu_button = AsyncMock(
        side_effect=None if automatic else RuntimeError("telegram is down")
    )
    published, cancelled = asyncio.Event(), asyncio.Event()

    async def watch(endpoint, publish):
        assert automatic  # A configured public URL must take precedence over discovery.
        assert endpoint == "http://tunnel:20241"
        try:
            await publish("https://app.example.test")
            published.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def poll(*args, **kwargs):
        if automatic:
            await asyncio.wait_for(published.wait(), timeout=1)

    monkeypatch.setattr(bot, "watch_tunnel", watch)
    engine = SimpleNamespace(dispose=AsyncMock())
    dispatcher = SimpleNamespace(
        message=SimpleNamespace(middleware=MagicMock()),
        callback_query=SimpleNamespace(middleware=MagicMock()),
        include_router=MagicMock(), start_polling=AsyncMock(side_effect=poll))
    runner = SimpleNamespace(cleanup=AsyncMock())
    start = AsyncMock(return_value=runner)
    monkeypatch.setattr(bot, "Bot", lambda **_: client)
    monkeypatch.setattr(bot, "Dispatcher", lambda **_: dispatcher)
    monkeypatch.setattr(bot, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(bot, "check_database", AsyncMock())
    monkeypatch.setattr(bot, "run_webapp", start)
    monkeypatch.setattr(
        bot.KnowledgeBase, "from_path", MagicMock(return_value=KnowledgeBase([], []))
    )
    await bot.run(
        SimpleNamespace(
            require_bot_token=lambda: "test-only",
            corpus_path=Path("synthetic-corpus.json"),
            index_dir=Path("synthetic-index"),
            yandex_api_key=None,
            admin_set=frozenset,
            webapp_url=None if automatic else "https://app.example.test",
            webapp_tunnel_url="http://tunnel:20241",
            webapp_dev=False,
            webapp_port=8081,
        )
    )
    assert start.await_args.kwargs["port"] == 8081 and start.await_args.kwargs["dev"] is False
    menu = client.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert menu.web_app.url == "https://app.example.test" and menu.text == "Навигатор"
    assert cancelled.is_set() is automatic
    runner.cleanup.assert_awaited_once()
    engine.dispose.assert_awaited_once()
