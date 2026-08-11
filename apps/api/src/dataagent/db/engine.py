"""Async engine and session factory.

Deliberately minimal. WP1.2 adds ``tenancy/session.py`` on top, which is the
*only* thing feature code may use: it refuses to hand out a session without an
org context and issues ``SET LOCAL app.org_id`` on every transaction. This module
exists so migrations and that tenancy layer share one engine configuration.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dataagent.config import Settings, get_settings


def build_engine(settings: Settings | None = None, *, url: str | None = None) -> AsyncEngine:
    """An engine for the owner/migration role, or for an explicit DSN.

    The request path does not use this: it goes through
    ``tenancy.session``, which connects as ``dataagent_app``.
    """
    resolved = settings if settings is not None else get_settings()
    return create_async_engine(
        url if url is not None else resolved.require_database_url(),
        # We are a polite guest on every database, including our own: small pools
        # keep a scale-to-zero container from monopolising a B-series server.
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return build_engine()


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def system_session() -> AsyncGenerator[AsyncSession]:
    """A session with **no** org scoping.

    For migrations, bootstrap and admin jobs only. Every use is a deliberate step
    outside tenant isolation, so callers must be able to justify it in review —
    feature code uses the tenancy session from WP1.2 instead.
    """
    factory = build_session_factory(get_engine())
    async with factory() as session:
        yield session
