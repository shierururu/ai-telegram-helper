"""Process logs omit network-library messages and exception diagnostics."""

import copy
import logging
import re
import sys


def _message(record: logging.LogRecord) -> str:
    try:
        return record.getMessage()
    except Exception:
        return ""


NETWORK_LOGGERS = ("aiohttp", "urllib3", "httpx", "httpcore", "aiogram")


class SafeStreamHandler(logging.StreamHandler):
    """Logging failures must never dump records or active exception tracebacks."""

    _alpha_course_handler = True

    def handleError(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write("logging_failed code=output_unavailable\n")
        except Exception:
            pass


class SafeFormatter(logging.Formatter):
    """Replace third-party network diagnostics before formatting any message or traceback."""

    def format(self, record: logging.LogRecord) -> str:
        if any(
            record.name == name or record.name.startswith(name + ".") for name in NETWORK_LOGGERS
        ):
            kind = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else ""
            if not kind:
                # aiogram reports errors as "Failed to fetch updates - <ErrorType>: ...".
                match = re.search(
                    r"\b(Telegram\w+Error|Client\w+Error|\w+Timeout\w*)\b", _message(record)
                )
                kind = match[1] if match else ""
            record = copy.copy(record)
            # Only the error type is logged: messages and URLs may contain the bot token.
            record.msg = f"network_library_event type={kind}" if kind else "network_library_event"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return super().format(record)


def setup_logging(level: str = "INFO") -> None:
    """Configure the process handler once and update its level on repeated startup calls."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = next(
        (item for item in root.handlers if getattr(item, "_alpha_course_handler", False)), None
    )
    if handler is not None and (
        handler.stream is not sys.stderr or getattr(handler.stream, "closed", False)
    ):
        root.removeHandler(handler)
        # Handler.close does not flush its stream, unlike setStream.
        handler.close()
        handler = None
    if handler is None:
        handler = SafeStreamHandler()
        root.addHandler(handler)
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    for name in NETWORK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
