"""Discover a Quick Tunnel through cloudflared's internal metrics API."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout

logger = logging.getLogger(__name__)
HOSTNAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com", re.ASCII)


async def discover_tunnel_url(session: ClientSession, endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    async with session.get(f"{endpoint}/ready") as response:
        response.raise_for_status()
    async with session.get(f"{endpoint}/quicktunnel") as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)
    hostname = payload.get("hostname") if isinstance(payload, dict) else None
    if not isinstance(hostname, str) or not HOSTNAME.fullmatch(hostname):
        raise ValueError("invalid_quick_tunnel_hostname")
    return f"https://{hostname}"


async def watch_tunnel(
    endpoint: str, publish: Callable[[str], Awaitable[None]], *, interval: float = 5
) -> None:
    """Retry startup failures and republish after a tunnel container restart."""
    published = None
    waiting = False
    async with ClientSession(timeout=ClientTimeout(total=5)) as session:
        while True:
            try:
                url = await discover_tunnel_url(session, endpoint)
                if url != published:
                    await publish(url)
                    published = url
                    logger.info("webapp_public_url=%s", url)
                waiting = False
            except Exception as error:
                # URLs and driver messages can contain credentials; log only the type.
                if not waiting:
                    logger.warning("webapp_tunnel_waiting error=%s", type(error).__name__)
                waiting = True
            await asyncio.sleep(interval)
