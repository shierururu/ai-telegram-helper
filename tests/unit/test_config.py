"""Synthetic secrets exercise safe process diagnostics."""

import pytest
from pydantic import ValidationError

from alpha_course.infrastructure.config import ConfigurationError, Settings, load_settings

DSN = "postgresql+psycopg://synthetic:secret-password@localhost/runtime"
TOKEN = "synthetic-bot-token"


def test_checks_need_database_but_no_bot_token() -> None:
    settings = Settings(_env_file=None, database_url=DSN)
    assert settings.database_url.get_secret_value() == DSN
    with pytest.raises(ConfigurationError, match="telegram_bot_token"):
        settings.require_bot_token()


def test_settings_repr_hides_secrets() -> None:
    settings = Settings(_env_file=None, database_url=DSN, telegram_bot_token=TOKEN)
    assert "secret-password" not in repr(settings)
    assert TOKEN not in repr(settings)
    assert settings.require_bot_token() == TOKEN


def test_invalid_dialect_hides_input_in_validation_message() -> None:
    value = "sqlite:///secret-password"
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=value)
    assert value not in str(caught.value)
    assert "secret-password" not in str(caught.value)


def test_load_error_reports_only_field_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFA_DATABASE_URL", "invalid-secret-password")
    monkeypatch.setenv("ALFA_TELEGRAM_BOT_TOKEN", TOKEN)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "database_url" in str(caught.value)
    assert "secret-password" not in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_worker_interval_must_be_finite_and_positive(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DSN, worker_poll_interval_seconds=value)


def test_network_diagnostics_are_redacted_in_actual_handler_output() -> None:
    import io
    import logging

    from aiohttp import ClientResponseError, RequestInfo
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    from alpha_course.infrastructure.logging import SafeFormatter

    token = "synthetic-private-token"
    url = URL(f"https://api.telegram.org/bot123:{token}/getUpdates")
    request = RequestInfo(url, "POST", CIMultiDictProxy(CIMultiDict()), url)
    error = ClientResponseError(request, (), status=502, message="synthetic failure")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeFormatter("%(levelname)s %(name)s %(message)s"))
    for name in (
        "aiogram.dispatcher",
        "aiohttp.client",
        "urllib3.connectionpool",
        "httpx",
        "httpcore",
    ):
        record = logging.LogRecord(
            name,
            logging.ERROR,
            __file__,
            1,
            "Failed %s",
            (error,),
            (type(error), error, None),
        )
        record.stack_info = f"stack contains {token}"
        record.exc_text = f"cached traceback contains {token}"
        handler.handle(record)
        # Formatting must not mutate records consumed by other handlers.
        assert record.args == (error,)
    rendered = output.getvalue()
    assert rendered.count("network_library_event") == 5
    assert "ERROR aiogram.dispatcher" in rendered
    assert token not in rendered
    assert str(url) not in rendered
    assert "Traceback" not in rendered
    assert "cached traceback" not in rendered


def test_logging_setup_reuses_owned_handler_and_updates_level() -> None:
    import logging

    from alpha_course.infrastructure.logging import setup_logging

    root = logging.getLogger()
    old_level = root.level
    before = list(root.handlers)
    try:
        setup_logging("INFO")
        setup_logging("DEBUG")
        owned = [item for item in root.handlers if getattr(item, "_alpha_course_handler", False)]
        assert len(owned) == 1
        assert root.level == logging.DEBUG
    finally:
        for item in root.handlers[:]:
            if item not in before:
                root.removeHandler(item)
                item.close()
        root.setLevel(old_level)


def test_logging_replaces_closed_stream_and_never_dumps_active_exception(monkeypatch) -> None:
    import io
    import logging
    import sys

    from alpha_course.infrastructure.logging import SafeStreamHandler, setup_logging

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    closed_output = io.StringIO()
    replacement = io.StringIO()
    try:
        monkeypatch.setattr(sys, "stderr", closed_output)
        setup_logging()
        original = next(
            item for item in root.handlers if getattr(item, "_alpha_course_handler", False)
        )
        closed_output.close()
        monkeypatch.setattr(sys, "stderr", replacement)
        try:
            raise RuntimeError("synthetic-private-exception")
        except RuntimeError:
            setup_logging()
            current = next(
                item for item in root.handlers if getattr(item, "_alpha_course_handler", False)
            )
            assert current is not original
            current.handle(
                logging.LogRecord(
                    "alpha_course.test",
                    logging.ERROR,
                    __file__,
                    1,
                    "startup_failed code=runtime_error",
                    (),
                    None,
                )
            )
            # Even a handler whose stream fails after setup cannot invoke logging's raw diagnostic.
            broken = SafeStreamHandler(closed_output)
            broken.handle(
                logging.LogRecord(
                    "alpha_course.test",
                    logging.ERROR,
                    __file__,
                    1,
                    "synthetic-private-record",
                    (),
                    None,
                )
            )
        rendered = replacement.getvalue()
        assert "startup_failed code=runtime_error" in rendered
        assert "logging_failed code=output_unavailable" in rendered
        assert "synthetic-private" not in rendered
        assert "Traceback" not in rendered
        assert all(item in root.handlers for item in previous_handlers if item is not original)
    finally:
        for item in root.handlers[:]:
            if getattr(item, "_alpha_course_handler", False):
                root.removeHandler(item)
                item.close()
        for item in previous_handlers:
            if item not in root.handlers:
                root.addHandler(item)
        root.setLevel(previous_level)


def test_webapp_settings_defaults_and_https_only(monkeypatch):
    from alpha_course.infrastructure.config import Settings

    base = {"ALFA_DATABASE_URL": "postgresql+psycopg://u:p@localhost/db"}
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.webapp_url is None and settings.webapp_port == 8080 and not settings.webapp_dev
    assert Settings(_env_file=None, webapp_url="").webapp_url is None
    assert Settings(_env_file=None, webapp_url="https://a.example.test").webapp_url
    with pytest.raises(ValueError):
        Settings(_env_file=None, webapp_url="http://a.example.test")
