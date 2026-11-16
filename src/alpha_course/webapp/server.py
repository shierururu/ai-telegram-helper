"""aiohttp application of the Mini App: auth, rate limit, JSON errors, API routes and static."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

from alpha_course.application.engagement import JourneyDayNotReached
from alpha_course.application.reminders import (
    AmbiguousTime,
    NotFound,
    ReminderError,
    StaleReminder,
)
from alpha_course.webapp import games, myths, tools, wordle
from alpha_course.webapp.api import USER_ID, Api, ApiError, Services, json_response
from alpha_course.webapp.auth import AuthError, dev_user, validate_init_data

_LOG = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
RATE_LIMIT = 30
RATE_WINDOW_SECONDS = 60.0
LIMITED_PATHS = frozenset(
    {
        "/api/ask",
        "/api/ask/free",
        "/api/simplify",
        "/api/journey/preview",
        "/api/reminders/parse",
        "/api/negotiation/turn",
        "/api/negotiation/finish",
        "/api/idea/check",
        "/api/finmodel/extract",
        "/api/finmodel/pdf",
    }
)
CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' https://telegram.org",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'self' https://web.telegram.org https://webk.telegram.org "
        "https://webz.telegram.org",
        "base-uri 'none'",
        "object-src 'none'",
    )
)
PLACEHOLDER = (
    '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Бизнес-навигатор</title>'
    "<p>Мини-апп собирается.</p></html>"
)


class RateLimiter:
    def __init__(
        self,
        limit: int = RATE_LIMIT,
        window: float = RATE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[int, deque[float]] = {}

    def allow(self, user: int) -> bool:
        now = self.clock()
        queue = self.hits.setdefault(user, deque())
        while queue and now - queue[0] >= self.window:
            queue.popleft()
        if len(queue) >= self.limit:
            return False
        queue.append(now)
        if len(self.hits) > 5000:  # forget idle users
            for key in [key for key, value in self.hits.items() if not value]:
                del self.hits[key]
        return True


def to_api_error(error: ReminderError) -> ApiError:
    """Domain errors carry user-safe Russian text; map them to statuses and codes."""
    message = str(error)
    if isinstance(error, NotFound):
        return ApiError(404, "not_found", message)
    if isinstance(error, StaleReminder):
        return ApiError(409, "stale", message)
    if isinstance(error, JourneyDayNotReached):
        return ApiError(
            409, "day_not_reached", "Этот день ещё не наступил — отметишь, когда придёт шаг."
        )
    if isinstance(error, AmbiguousTime):
        options = [
            {"fold": index, "label": option.strftime("UTC%z")}
            for index, option in enumerate(error.options)
        ]
        return ApiError(409, "ambiguous_time", message, options=options)
    if "активных" in message:
        return ApiError(409, "limit", message)
    if "активный маршрут" in message:
        return ApiError(409, "conflict", message)
    if "в будущем" in message:
        return ApiError(400, "past_time", message)
    return ApiError(400, "invalid", message)


def error_response(error: ApiError) -> web.Response:
    return json_response(
        {"error": error.code, "message": error.message, **error.extra}, status=error.status
    )


HTTP_ERRORS = {
    404: ("not_found", "Не нашёл такой страницы."),
    405: ("method_not_allowed", "Так сюда обращаться нельзя."),
    413: ("too_large", "Запрос слишком большой."),
}


@web.middleware
async def errors_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as error:
        return error_response(error)
    except ReminderError as error:
        return error_response(to_api_error(error))
    except web.HTTPException as error:
        if request.path.startswith("/api/") and error.status >= 400:
            code, message = HTTP_ERRORS.get(
                error.status, ("error", "Не получилось обработать запрос.")
            )
            return json_response({"error": code, "message": message}, status=error.status)
        raise
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Neither exception strings nor tracebacks are safe diagnostics (they may hold user text).
        _LOG.warning("webapp_request_failed path=%s error=%s", request.path, type(error).__name__)
        return json_response(
            {"error": "internal", "message": "Что-то пошло не так. Попробуй ещё раз 🙏"},
            status=500,
        )


def make_auth_middleware(bot_token: str | None, dev: bool):
    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)
        try:
            if "X-Dev-User" in request.headers:
                user = dev_user(request.headers, request.remote, enabled=dev)
            else:
                user = validate_init_data(
                    request.headers.get("X-Telegram-Init-Data", ""), bot_token or ""
                )
        except AuthError:
            raise ApiError(
                401, "unauthorized", "Открой мини-апп из Telegram, чтобы войти."
            ) from None
        request[USER_ID] = user
        return await handler(request)

    return auth_middleware


def make_rate_middleware(limiter: RateLimiter):
    @web.middleware
    async def rate_middleware(request: web.Request, handler):
        if request.method == "POST" and request.path in LIMITED_PATHS:
            if not limiter.allow(request[USER_ID]):
                raise ApiError(
                    429,
                    "rate_limited",
                    "Слишком много запросов. Подожди минуту и повтори.",
                )
        return await handler(request)

    return rate_middleware


async def add_headers(request: web.Request, response: web.StreamResponse) -> None:
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.path != "/healthz":
        response.headers["Cache-Control"] = "no-cache"


def ensure_static(static_dir: Path) -> None:
    """A placeholder keeps `/` alive until the real front end is in place."""
    index = static_dir / "index.html"
    if not index.exists():
        static_dir.mkdir(parents=True, exist_ok=True)
        index.write_text(PLACEHOLDER, encoding="utf-8")


def create_app(
    services: Services,
    *,
    bot_token: str | None,
    dev: bool = False,
    static_dir: Path | None = None,
    limiter: RateLimiter | None = None,
) -> web.Application:
    root = (static_dir or STATIC_DIR).resolve()
    limiter = limiter or RateLimiter()
    app = web.Application(
        middlewares=[
            errors_middleware,
            make_auth_middleware(bot_token, dev),
            make_rate_middleware(limiter),
        ],
        client_max_size=64 * 1024,
    )
    if bot_token:
        app[tools.BOT_TOKEN] = bot_token
    api = Api(services)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def index(_: web.Request) -> web.StreamResponse:
        page = root / "index.html"
        if not page.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(page)

    async def static_file(request: web.Request) -> web.StreamResponse:
        target = (root / request.match_info["tail"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(target)

    get, post = app.router.add_get, app.router.add_post
    get("/healthz", health)
    get("/api/bootstrap", api.bootstrap)
    post("/api/ask", api.ask)
    post("/api/ask/free", api.ask_free)
    post("/api/simplify", api.simplify)
    post("/api/feedback", api.feedback)
    post("/api/click", api.click)
    get("/api/profile", api.get_profile)
    app.router.add_put("/api/profile", api.put_profile)
    post("/api/profile/describe", api.describe_profile)
    app.router.add_delete("/api/profile", api.clear_profile)
    post("/api/onboarding/done", api.onboarding_done)
    post("/api/route", api.route)
    post("/api/route/done", api.route_done)
    post("/api/journey/preview", api.journey_preview)
    post("/api/journey", api.journey_start)
    get("/api/journey", api.journey_get)
    post("/api/journey/done", api.journey_done)
    app.router.add_delete("/api/journey", api.journey_stop)
    get("/api/taxes", api.taxes)
    post("/api/taxes/remind", api.taxes_remind)
    get("/api/reminders", api.reminders_list)
    post("/api/reminders/parse", api.reminders_parse)
    post("/api/reminders/timezone", api.reminders_timezone)
    post("/api/reminders", api.reminders_create)
    app.router.add_patch("/api/reminders/{id}", api.reminders_patch)
    post("/api/reminders/{id}/snooze", api.reminders_snooze)
    app.router.add_delete("/api/reminders/{id}", api.reminders_disable)
    get("/api/settings", api.settings_get)
    app.router.add_put("/api/settings", api.settings_put)
    get("/api/materials/search", api.search_materials)
    wordle.register(app, api)
    myths.register(app, api)
    games.register(app, api)
    tools.register(app, api)
    get("/", index)
    get("/static/{tail:.*}", static_file)
    get("/{tail:.*}", static_file)  # the front end may also reference its files from the root
    app.on_response_prepare.append(add_headers)
    return app


async def start_webapp(app: web.Application, *, port: int, host: str = "0.0.0.0") -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
    except BaseException:
        await runner.cleanup()
        raise
    _LOG.info("webapp_started port=%s", port)
    return runner


async def run_webapp(
    services: Services,
    *,
    bot_token: str | None,
    dev: bool,
    port: int,
    host: str = "0.0.0.0",
    static_dir: Path | None = None,
) -> web.AppRunner:
    """Create the app and start serving; the caller owns `runner.cleanup()`."""
    ensure_static(static_dir or STATIC_DIR)
    app = create_app(services, bot_token=bot_token, dev=dev, static_dir=static_dir)
    return await start_webapp(app, port=port, host=host)
