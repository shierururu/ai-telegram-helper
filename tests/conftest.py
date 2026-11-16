"""Shared pytest configuration."""

import asyncio
import sys
import warnings

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    # psycopg's async mode cannot run on the Windows default Proactor loop.
    if sys.platform == "win32":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()
