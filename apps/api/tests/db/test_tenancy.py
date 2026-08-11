"""The tenancy layer: refusing without a context, and scoping when it has one."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import URL, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dataagent.db.models import AuditLog, Invitation, Organization, User
from dataagent.tenancy import session as session_module
from dataagent.tenancy.base_repo import OrgScopedRepository
from dataagent.tenancy.context import (
    MissingTenantContextError,
    TenantContext,
    current_context,
    current_context_or_none,
    tenant_context,
)
from dataagent.tenancy.session import current_org_setting, org_session, tenant_session

# ---------------------------------------------------------------------------
# Context — no database needed
# ---------------------------------------------------------------------------


def test_current_context_raises_when_nothing_is_set() -> None:
    with pytest.raises(MissingTenantContextError):
        current_context()


def test_tenant_context_scopes_and_restores() -> None:
    context = TenantContext(org_id=uuid.uuid4())

    assert current_context_or_none() is None
    with tenant_context(context):
        assert current_context() is context
    assert current_context_or_none() is None


async def test_tenant_session_refuses_without_a_context() -> None:
    """The refusal the module exists for — and it happens before any connection."""
    with pytest.raises(MissingTenantContextError):
        async with tenant_session():
            pytest.fail("a session was handed out with no organization")


# ---------------------------------------------------------------------------
# Repository — the SQL-level half of defence in depth
# ---------------------------------------------------------------------------


def test_repository_narrows_every_select_by_org() -> None:
    org_id = uuid.uuid4()
    repository = OrgScopedRepository(session=None, org_id=org_id, model=Invitation)  # type: ignore[arg-type]

    compiled = str(repository.select().compile(compile_kwargs={"literal_binds": True}))

    assert "invitations.org_id" in compiled
    # Postgres renders a UUID literal without dashes.
    assert org_id.hex in compiled


def test_repository_narrows_a_statement_it_did_not_build() -> None:
    org_id = uuid.uuid4()
    repository = OrgScopedRepository(session=None, org_id=org_id, model=Invitation)  # type: ignore[arg-type]

    compiled = str(
        repository.scoped(select(Invitation).where(Invitation.email == "x@example.com")).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "invitations.org_id" in compiled
    assert "invitations.email" in compiled


def test_repository_uses_the_declared_tenant_key_for_organizations() -> None:
    """`organizations` is scoped by `id`; passing the default would be a silent bug."""
    org_id = uuid.uuid4()
    repository = OrgScopedRepository(
        session=None,  # type: ignore[arg-type]
        org_id=org_id,
        model=Organization,
        tenant_key="id",
    )

    compiled = str(repository.select().compile(compile_kwargs={"literal_binds": True}))

    assert "organizations.id" in compiled


def test_repository_refuses_a_model_with_no_tenant_column() -> None:
    """`users` is global. Scoping it would be meaningless, so it fails loudly."""
    with pytest.raises(AttributeError, match="has no 'org_id' column"):
        OrgScopedRepository(session=None, org_id=uuid.uuid4(), model=User)  # type: ignore[arg-type]


def test_repository_add_stamps_the_org_id_it_was_built_with() -> None:
    org_id = uuid.uuid4()
    repository = OrgScopedRepository(session=_FakeSession(), org_id=org_id, model=AuditLog)  # type: ignore[arg-type]

    entry = AuditLog(org_id=uuid.uuid4(), action="whatever")
    repository.add(entry)

    assert entry.org_id == org_id, "a caller-supplied org_id survived into the session"


class _FakeSession:
    def add(self, _entity: object) -> None:
        return None


# ---------------------------------------------------------------------------
# Session — against a real database, as the application role
# ---------------------------------------------------------------------------


@pytest.fixture
def app_session_factory(
    app_database: URL, monkeypatch: pytest.MonkeyPatch
) -> async_sessionmaker[AsyncSession]:
    """Point the tenancy session at the test database, still as dataagent_app."""
    engine = create_async_engine(app_database)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(session_module, "_session_factory", lambda: factory)
    return factory


@pytest.mark.usefixtures("app_session_factory")
async def test_org_session_sets_the_database_side_org(app_database: URL) -> None:
    org_id = uuid.uuid4()

    async with org_session(org_id) as session:
        assert await current_org_setting(session) == str(org_id)


@pytest.mark.usefixtures("app_session_factory")
async def test_the_org_setting_does_not_survive_the_transaction(app_database: URL) -> None:
    """SET LOCAL is transaction-scoped, so a pooled connection carries nothing over.

    Without this, a connection returned to the pool could hand the next borrower
    someone else's organization — a cross-tenant leak with no bad code anywhere.
    """
    first = uuid.uuid4()
    async with org_session(first) as session:
        assert await current_org_setting(session) == str(first)

    second = uuid.uuid4()
    async with org_session(second) as session:
        assert await current_org_setting(session) == str(second)


@pytest.mark.usefixtures("app_session_factory")
async def test_tenant_session_uses_the_context_organization() -> None:
    context = TenantContext(org_id=uuid.uuid4())

    with tenant_context(context):
        async with tenant_session() as session:
            assert await current_org_setting(session) == str(context.org_id)


@pytest.mark.usefixtures("app_session_factory")
async def test_a_repository_reads_only_its_own_organization(migrated_database: URL) -> None:
    """End to end: two orgs in the database, one org visible through the repository."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    owner = create_async_engine(migrated_database)
    try:
        for org_id, name in ((org_a, "A"), (org_b, "B")):
            async with owner.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
                )
                await connection.execute(
                    text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
                    {"id": org_id, "name": name},
                )
                await connection.execute(
                    text(
                        "INSERT INTO invitations (org_id, email, role, token_hash, expires_at) "
                        "VALUES (:org, :email, 'reader', :hash, now() + interval '7 days')"
                    ),
                    {"org": org_id, "email": f"{name}@example.com", "hash": uuid.uuid4().hex},
                )
    finally:
        await owner.dispose()

    async with org_session(org_a) as session:
        repository = OrgScopedRepository(session=session, org_id=org_a, model=Invitation)
        found = await repository.list_all()

    assert [invitation.email for invitation in found] == ["A@example.com"]
