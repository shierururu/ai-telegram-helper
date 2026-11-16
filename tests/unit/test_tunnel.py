"""Automatic Mini App URL discovery and recovery without external services."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from alpha_course.infrastructure import tunnel


@pytest.mark.parametrize(
    ("hostname", "valid"),
    [
        ("sample-mini-app.trycloudflare.com", True),
        ("attacker.test", False),
        ("sample.trycloudflare.com/other", False),
        ("sample.trycloudflare.com@attacker.test", False),
        (None, False),
    ],
)
async def test_discovery_validates_hostname(hostname, valid):
    async def ready(_):
        return web.Response(text="ready")

    async def address(_):
        return web.json_response({"hostname": hostname})

    app = web.Application()
    app.router.add_get("/ready", ready)
    app.router.add_get("/quicktunnel", address)
    async with TestServer(app) as server, ClientSession() as session:
        endpoint = str(server.make_url("/"))
        if valid:
            assert await tunnel.discover_tunnel_url(session, endpoint) == f"https://{hostname}"
        else:
            with pytest.raises(ValueError):
                await tunnel.discover_tunnel_url(session, endpoint)


async def test_watcher_recovers_and_republishes_changed_url(monkeypatch, caplog):
    first = "https://first.trycloudflare.com"
    second = "https://second.trycloudflare.com"
    discover = AsyncMock(side_effect=[OSError("private-error"), first, first, first, second])
    monkeypatch.setattr(tunnel, "discover_tunnel_url", discover)
    publish = AsyncMock(side_effect=[RuntimeError("private-error"), None, None])
    task = asyncio.create_task(tunnel.watch_tunnel("http://tunnel:20241", publish, interval=0.01))
    try:
        async with asyncio.timeout(2):
            while publish.await_count < 3:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert [call.args[0] for call in publish.await_args_list] == [first, first, second]
    assert discover.await_count == 5
    assert "private-error" not in caplog.text
