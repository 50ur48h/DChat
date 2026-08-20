"""The org suite's shared harness: a real app, over real HTTP, on a real database.

Here rather than inside one test module because two suites need it — the M2
signup flow, and **B-017**'s recovery grants. A fixture that lives in a test file
can only be shared by importing it, which makes the fixture look unused to a
linter and reaches past a private name to do it. `conftest.py` is where pytest
looks, so both suites simply ask for `api` and get one.

`audit_rows` is public for the same reason: two suites read the audit log to
prove that what happened was recorded, and a leading underscore on something
another module is meant to call is a lie about its intended reach.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, Row, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.config import Settings
from dataagent.db import engine as engine_module
from dataagent.main import create_app
from dataagent.tenancy import session as session_module


class _SubjectAsToken(TokenValidator):
    """The bearer token *is* the subject, so these tests are about the flow."""

    def __init__(self) -> None:
        #: Subjects whose token carries no email claim — the Entra default until
        #: an administrator adds the optional claim (B-009).
        self.without_email: set[str] = set()

    async def validate(self, token: str) -> Principal:
        if not token or token == "invalid":  # noqa: S105
            raise TokenError("bad_signature", "nope")
        if token in self.without_email:
            return Principal(subject=token, email=None, name=None)
        return Principal(subject=token, email=f"{token}@example.com", name=token.title())


class Api:
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def call(
        self, method: str, path: str, who: str | None = None, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {who}"} if who else {}
        async with AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        ) as client:
            response = await client.request(method, path, headers=headers, json=body)
        payload = None if response.status_code == 204 else response.json()
        return response.status_code, payload


@pytest.fixture
async def api(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Api]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    factory = async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False)

    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(session_module, "_session_factory", lambda: factory)

    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken()

    try:
        yield Api(app)
    finally:
        await owner.dispose()
        await app_engine.dispose()


def subject_validator(api: Api) -> _SubjectAsToken:
    validator = api.app.state.token_validator
    assert isinstance(validator, _SubjectAsToken)
    return validator


async def audit_rows(url: URL, org_id: uuid.UUID) -> Sequence[Row[Any]]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            rows = await connection.execute(
                text("SELECT action, object_type, details FROM audit_log ORDER BY id")
            )
            return rows.fetchall()
    finally:
        await engine.dispose()
