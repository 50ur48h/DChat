"""The only way feature code reaches the platform database.

Every session opened here connects as ``dataagent_app`` — no superuser, no
BYPASSRLS, owner of nothing. ``org_session`` additionally runs inside one
transaction that begins by setting ``app.org_id``, which is what the row-level
security policies from revision 0002 read; the scoping is not a parameter callers
may omit, it is the constructor.

**There is one unscoped variant, and its limits are the reason it is safe.**
``app_session`` sets no organization, for the two tables that have none:
``users``, whose rows exist before any membership does, and ``security_events``,
which by **D-008** deliberately sits outside ``TENANT_TABLES``. It is *not* a way
to see across tenants — RLS is still in force and no ``app.org_id`` is set, so a
caller that reached a tenant table through it would see **nothing**. That is the
whole difference from ``system_session``, where the same mistake sees everything
(**B-123**).

``SET LOCAL`` is transaction-scoped, so the setting is applied *inside* the
transaction it protects and disappears with it. A connection returned to the pool
therefore carries no organization, and a later borrower cannot inherit one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from dataagent.config import Settings, get_settings
from dataagent.db.engine import build_engine
from dataagent.tenancy.context import current_context

ORG_SETTING = "app.org_id"

# set_config(name, value, is_local) is the parameterisable form of SET LOCAL:
# `SET LOCAL app.org_id = :org` cannot take a bind parameter, and building that
# statement by string interpolation is exactly the habit this codebase refuses.
_SET_ORG = text("SELECT set_config(:name, :value, true)")


def build_app_engine(settings: Settings | None = None) -> AsyncEngine:
    """An engine connected as ``dataagent_app`` — no superuser, no BYPASSRLS."""
    resolved = settings if settings is not None else get_settings()
    return build_engine(url=resolved.require_app_database_url())


@lru_cache(maxsize=1)
def get_app_engine() -> AsyncEngine:
    return build_app_engine()


@lru_cache(maxsize=1)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_app_engine(), expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def org_session(org_id: uuid.UUID) -> AsyncGenerator[AsyncSession]:
    """A session scoped to one organization for the life of one transaction.

    Commits on clean exit, rolls back on an exception. Callers should not commit
    inside the block: the organization is set with ``SET LOCAL``, so a commit
    would end the transaction that carries it.
    """
    factory = _session_factory()
    async with factory() as session, session.begin():
        await session.execute(_SET_ORG, {"name": ORG_SETTING, "value": str(org_id)})
        yield session


@asynccontextmanager
async def app_session() -> AsyncGenerator[AsyncSession]:
    """The application role, with no organization set.

    For the tables that are not tenant-scoped and carry no RLS policy — `users`
    and `security_events` — and for the audited `SECURITY DEFINER` lookups from
    revision 0028, which answer the questions that must be asked *before* an
    organization is known.

    Uses the same factory as `org_session`, deliberately: one engine, one
    connection pool, and the only difference between them is scoping. That also
    means every test that redirects `_session_factory` at a test database
    redirects this too.

    **No `session.begin()`, unlike `org_session`, and the asymmetry is not an
    oversight.** `org_session` opens the transaction because `SET LOCAL
    app.org_id` only lives inside one, so the scope and the transaction are the
    same object. Nothing is set here, so there is nothing to hold open — and the
    callers moved here from `system_session` commit for themselves, which they
    could not do inside a `begin()` block.
    """
    factory = _session_factory()
    async with factory() as session:
        yield session


@asynccontextmanager
async def tenant_session() -> AsyncGenerator[AsyncSession]:
    """A session for the organization of the current request.

    Raises ``MissingTenantContextError`` when there is no request context, which is
    the refusal the whole module exists for.
    """
    async with org_session(current_context().org_id) as session:
        yield session


async def current_org_setting(session: AsyncSession) -> str | None:
    """Read back the organization the database believes this session is scoped to.

    Useful in tests and in assertions; ``missing_ok`` so an unscoped session
    answers ``None`` rather than raising.
    """
    result = await session.execute(
        text("SELECT current_setting(:name, true)"), {"name": ORG_SETTING}
    )
    return result.scalar_one_or_none()
