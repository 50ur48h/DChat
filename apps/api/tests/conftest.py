"""Shared test fixtures.

Tests run against the ASGI app in-process — no network, no server, no ports.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dataagent.config import Settings
from dataagent.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Settings for tests, independent of the developer's environment."""
    return Settings(env="ci", build_env="dev", git_sha="testsha", log_level="WARNING")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings=settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
