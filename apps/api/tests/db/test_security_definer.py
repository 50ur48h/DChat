"""Every SECURITY DEFINER function in the database is one somebody declared.

**An allowlist that nothing checks is a comment.** Revision 0028 creates five
functions that run as the owner, which is how the application role reaches rows
RLS would otherwise hide from it — the authorization bootstrap, and the two
cross-organization reads. That is a deliberate, enumerable bypass, and it is only
enumerable for as long as something counts.

So this file is the counting. It asks the database what exists and fails on
anything not named below, in the same idiom `TENANT_TABLES` uses: the list is the
declaration, the test is what makes the declaration true.

**What each test is actually guarding against** is worth saying, because a
SECURITY DEFINER function is the sharpest object in this schema:

* A sixth function added without review — the list catches it.
* A function whose `search_path` is not pinned. This is the textbook PostgreSQL
  privilege escalation: the caller prepends a schema they control, an unqualified
  name inside the body resolves to *their* object, and it executes as the owner.
* A function executable by `PUBLIC`, which is the default the moment it is
  created. Granting to the application role without revoking from PUBLIC leaves
  it open to every role on the server.
* A function that writes. All five answer questions; none changes anything, and
  both token flows do their writes inside `org_session` where RLS applies.
"""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent.scheduler import ORPHANABLE

pytestmark = pytest.mark.asyncio

APP_ROLE = "dataagent_app"

#: **The declaration.** Every SECURITY DEFINER function this product is allowed to
#: have, and one line each saying why it cannot be an ordinary query. Adding a
#: function means adding a line here, in the same PR, with a reason a reviewer can
#: weigh — which is the whole mechanism.
SECURITY_DEFINER_FUNCTIONS: dict[str, str] = {
    "auth_membership_role": (
        "Reads org_memberships before the organization is known. This is the "
        "authorization bootstrap: app.org_id cannot be set until the caller's "
        "membership has been established."
    ),
    "auth_memberships_for_user": (
        "Which organizations a person belongs to spans every organization by "
        "definition, so no single app.org_id can answer it."
    ),
    "auth_invitation_by_token": (
        "The token is all the caller has; the organization is what the lookup "
        "answers. Every write in that flow is org-scoped."
    ),
    "auth_recovery_grant_by_token": (
        "Same shape as an invitation, for the way back in when no Admin can sign in (B-017)."
    ),
    "ops_orphaned_runs": (
        "Runs left hanging by a restart, across every organization. Each is then "
        "transitioned inside its own org's session."
    ),
}


async def _rows(dsn: URL, sql: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as connection:
            return [tuple(r) for r in (await connection.execute(text(sql))).all()]
    finally:
        await engine.dispose()


async def _definer_functions(dsn: URL) -> dict[str, tuple[object, ...]]:
    rows = await _rows(
        dsn,
        """
        SELECT p.proname,
               p.prosecdef,
               p.provolatile,
               p.proconfig,
               p.oid::bigint
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prosecdef
        """,
    )
    return {str(r[0]): r for r in rows}


async def test_every_security_definer_function_is_declared(app_database: URL) -> None:
    """The counting. An undeclared function is a bypass nobody reviewed."""
    found = await _definer_functions(app_database)

    undeclared = sorted(set(found) - set(SECURITY_DEFINER_FUNCTIONS))
    assert not undeclared, (
        f"SECURITY DEFINER function(s) {undeclared} exist and are not declared in "
        "SECURITY_DEFINER_FUNCTIONS. Each one runs as the owner and can read past "
        "every RLS policy; add it to the list with the reason it cannot be an "
        "ordinary query, or drop it."
    )


async def test_every_declared_function_exists(app_database: URL) -> None:
    """The other direction. A stale declaration excuses a function nobody has."""
    found = await _definer_functions(app_database)

    missing = sorted(set(SECURITY_DEFINER_FUNCTIONS) - set(found))
    assert not missing, f"declared but absent from the database: {missing}"


async def test_every_definer_function_pins_its_search_path(app_database: URL) -> None:
    """The escalation this whole class of function is famous for.

    Without a pinned `search_path` the caller prepends a schema of their own, an
    unqualified name in the body resolves to their object, and it runs as the
    owner.
    """
    for name, row in (await _definer_functions(app_database)).items():
        raw_config: object = row[3]
        entries = cast("list[object]", raw_config) if isinstance(raw_config, list) else []
        config: list[str] = [str(c) for c in entries]
        pinned = [c for c in config if c.startswith("search_path=")]
        assert pinned, f"{name} is SECURITY DEFINER and does not pin search_path"
        assert "pg_catalog" in str(pinned[0]), (
            f"{name} pins search_path without pg_catalog first, so a caller can "
            f"shadow a built-in operator: {pinned[0]}"
        )


async def test_no_definer_function_is_executable_by_public(app_database: URL) -> None:
    """`PUBLIC` gets EXECUTE by default the moment a function is created.

    Granting to the application role without revoking leaves it open to every
    role on the server, which on a shared cluster includes roles this product has
    never heard of.
    """
    for name, row in (await _definer_functions(app_database)).items():
        # By oid, not by a rendered signature: `pg_get_function_identity_arguments`
        # returns names as well as types (`p_user_id uuid`), which is not a
        # signature `has_function_privilege` will parse.
        granted = await _rows(
            app_database,
            f"SELECT has_function_privilege('public', {row[4]}::oid, 'EXECUTE')",
        )
        assert granted[0][0] is False, f"{name} is executable by PUBLIC"


async def test_the_application_role_can_execute_each_one(app_database: URL) -> None:
    """The other half: revoking from PUBLIC must not have revoked the point."""
    for name, row in (await _definer_functions(app_database)).items():
        granted = await _rows(
            app_database,
            f"SELECT has_function_privilege('{APP_ROLE}', {row[4]}::oid, 'EXECUTE')",
        )
        assert granted[0][0] is True, f"{APP_ROLE} cannot execute {name}"


async def test_no_definer_function_can_write(app_database: URL) -> None:
    """All five answer questions. A writing function that runs as the owner is a
    different and much larger thing to review, and none of these needs to be one:
    both token flows do their writes inside `org_session`, under RLS."""
    for name, row in (await _definer_functions(app_database)).items():
        # `provolatile` is Postgres's internal `"char"`, which asyncpg hands back
        # as bytes — so a bare `in {"s", "i"}` compares b's' to 's' and fails on a
        # function that is perfectly STABLE.
        raw = row[2]
        volatility = raw.decode() if isinstance(raw, bytes) else str(raw)
        assert volatility in {"s", "i"}, (
            f"{name} is VOLATILE, so it may write. Every declared function is a "
            "lookup; make it STABLE or justify the write in the declaration."
        )


async def test_the_orphan_statuses_match_the_scheduler(app_database: URL) -> None:
    """`ops_orphaned_runs` hard-codes the statuses rather than taking them as a
    parameter, so that a caller cannot ask it for *every* run in *every*
    organization. The cost of that is two lists, and this is the check that keeps
    them equal — the same arrangement `TENANT_TABLES` has with revision 0002."""
    body = await _rows(
        app_database,
        "SELECT prosrc FROM pg_proc WHERE proname = 'ops_orphaned_runs'",
    )
    source = str(body[0][0])

    for status in ORPHANABLE:
        assert f"'{status}'" in source, (
            f"scheduler.ORPHANABLE contains {status!r} and ops_orphaned_runs does "
            "not look for it, so runs in that state would never be swept"
        )
