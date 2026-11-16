"""Signal-controlled idle lifecycle; this is not a reminder scheduler."""

import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def stop_on_signals(stop: asyncio.Event) -> Iterator[None]:
    loop = asyncio.get_running_loop()
    installed = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed.append(sig)
        except NotImplementedError:
            pass
    try:
        yield
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


async def idle_until_stopped(stop: asyncio.Event, interval: float) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
