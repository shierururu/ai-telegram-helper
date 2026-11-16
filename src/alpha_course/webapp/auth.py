"""Telegram Mini App authorization: initData HMAC check and the local dev header."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 24 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60
LOCAL_ADDRESSES = frozenset({"127.0.0.1", "::1"})


class AuthError(Exception):
    """initData is missing, forged or expired; never carries the input values."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: float | None = None,
    max_age: int = MAX_AGE_SECONDS,
) -> int:
    """Return the Telegram user id from a valid `Telegram.WebApp.initData` string."""
    if not init_data or not bot_token or len(init_data) > 8192:
        raise AuthError("missing")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    fields = dict(pairs)
    received = fields.pop("hash", None)
    if not received or len(pairs) != len(fields) + 1:
        raise AuthError("hash")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise AuthError("signature")
    try:
        issued = int(fields["auth_date"])
    except KeyError, ValueError:
        raise AuthError("auth_date") from None
    moment = time.time() if now is None else now
    if moment - issued > max_age or issued - moment > MAX_FUTURE_SKEW_SECONDS:
        raise AuthError("expired")
    try:
        user = json.loads(fields["user"])
        user_id = user["id"]
    except KeyError, ValueError, TypeError:
        raise AuthError("user") from None
    if type(user_id) is not int or user_id <= 0:
        raise AuthError("user")
    return user_id


def dev_user(headers: Mapping[str, str], remote: str | None, *, enabled: bool) -> int:
    """`X-Dev-User` replaces initData, but only in dev mode and for direct loopback requests."""
    if not enabled or remote not in LOCAL_ADDRESSES:
        raise AuthError("dev_disabled")
    # A reverse proxy or tunnel running on this machine would also look like loopback.
    if any(name in headers for name in ("X-Forwarded-For", "Forwarded", "X-Real-IP")):
        raise AuthError("proxied")
    raw = headers.get("X-Dev-User", "")
    if not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2**53:
        raise AuthError("dev_user")
    return int(raw)
