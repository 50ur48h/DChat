"""Registering data sources, and keeping their credentials out of this database.

The rule this module exists to enforce (architecture Part 7.3): a username and a
password reach the ``SecretsProvider`` and nothing else. What lands in
``data_sources`` is where the database is, what it is called, and a ``secret_ref``
that is worthless without the store it points at.

Two orderings here are deliberate, and both come from asking what a half-finished
operation leaves behind:

* **Create** writes the secret first and the row second, deleting the secret again
  if the row fails. An orphaned secret is invisible and harmless; an orphaned row
  is a data source that exists in the UI and can never connect.
* **Delete** removes the row first and the secret second. The reverse would leave
  a row whose credentials are gone — the same broken state, arrived at from the
  other side.

Reads of ``data_sources`` go through ``org_session``, so row-level security is in
force even if a filter is ever forgotten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from dataagent.connectors.probe import Reachability, check_reachable
from dataagent.db.models import DataSource
from dataagent.orgs.service import ConflictError, audit
from dataagent.secrets.base import SecretsProvider
from dataagent.secrets.factory import get_secrets_provider
from dataagent.tenancy.session import org_session

__all__ = [
    "DataSourceView",
    "NotFoundError",
    "create_data_source",
    "credentials_ref",
    "delete_data_source",
    "get_data_source",
    "list_data_sources",
    "test_data_source",
    "update_data_source",
]

STATUS_REGISTERED = "registered"

#: How much of the username a response may carry (architecture Part 7.3).
USERNAME_HINT_CHARS = 4


class NotFoundError(Exception):
    """No such data source in this organization.

    Also what a caller sees for a data source that exists in *another*
    organization: row-level security means this process cannot tell the
    difference, which is the right answer either way.
    """


def credentials_ref(org_id: uuid.UUID, data_source_id: uuid.UUID) -> str:
    """The reference under which one data source's credentials are stored.

    Derived from identifiers rather than stored as a random name, so a row and
    its secret cannot drift apart, and so an orphaned secret is identifiable.
    """
    return f"ds/{org_id}/{data_source_id}/credentials"


@dataclass(frozen=True, slots=True)
class DataSourceView:
    """What the rest of the application may know about a data source.

    There is no ``password`` field and no ``username`` field, only the last four
    characters of the username (architecture Part 7.3) — enough to answer "which
    account is this connecting as?" without reading the credential back.
    """

    id: uuid.UUID
    name: str
    engine: str
    host: str
    port: int
    database: str
    host_display: str
    status: str
    secret_ref: str
    username_last4: str
    created_by: uuid.UUID | None
    created_at: datetime


# ---------------------------------------------------------------------------
# The non-secret half of a connection, as stored in data_sources.settings
# ---------------------------------------------------------------------------


def _display(host: str, port: int, database: str) -> str:
    """The human-readable address. Safe to log: it has never held a credential."""
    return f"{host}:{port}/{database}"


def _connection_settings(
    host: str, port: int, database: str, username_last4: str
) -> dict[str, object]:
    return {
        "host": host,
        "port": port,
        "database": database,
        # Not the username: the last of it, so a screen can identify the account
        # without this table becoming half a credential.
        "username_last4": username_last4,
    }


def _hint(username: str) -> str:
    return username[-USERNAME_HINT_CHARS:]


def _field(settings: dict[str, object], field: str) -> str:
    value = settings.get(field)
    if value is None:
        raise ValueError(f"data_sources.settings is missing {field!r}")
    return str(value)


def _port(settings: dict[str, object]) -> int:
    value = settings.get("port")
    if not isinstance(value, int):
        raise ValueError("data_sources.settings has no numeric 'port'")
    return value


def _view(row: DataSource) -> DataSourceView:
    settings = row.settings
    return DataSourceView(
        id=row.id,
        name=row.name,
        engine=row.engine,
        host=_field(settings, "host"),
        port=_port(settings),
        database=_field(settings, "database"),
        host_display=row.host_display,
        status=row.status,
        secret_ref=row.secret_ref,
        username_last4=_field(settings, "username_last4"),
        created_by=row.created_by,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


async def create_data_source(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    name: str,
    engine: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    provider: SecretsProvider | None = None,
) -> DataSourceView:
    """Store the credentials, then record where the database is."""
    store = provider if provider is not None else get_secrets_provider()

    data_source_id = uuid.uuid4()
    secret_ref = credentials_ref(org_id, data_source_id)
    host_display = _display(host, port, database)

    await store.put(secret_ref, {"username": username, "password": password})

    try:
        async with org_session(org_id) as session:
            row = DataSource(
                id=data_source_id,
                org_id=org_id,
                name=name,
                engine=engine,
                host_display=host_display,
                status=STATUS_REGISTERED,
                settings=_connection_settings(host, port, database, _hint(username)),
                secret_ref=secret_ref,
                created_by=actor_user_id,
            )
            session.add(row)
            # Flushed here so the view carries the database's own created_at and
            # status rather than this process's guess at them.
            await session.flush()
            audit(
                session,
                org_id=org_id,
                actor_user_id=actor_user_id,
                action="datasource.created",
                object_type="data_source",
                object_id=str(data_source_id),
                # host_display and the engine, never the credential. Audit rows
                # are read by admins and shipped to log aggregators.
                details={"name": name, "engine": engine, "host_display": host_display},
            )
            return _view(row)
    except IntegrityError as error:
        await store.delete(secret_ref)
        raise ConflictError(f"A data source named {name!r} already exists here") from error
    except Exception:
        # Any other failure would leave a credential nobody can reach, belonging
        # to a row that was never written.
        await store.delete(secret_ref)
        raise


async def list_data_sources(org_id: uuid.UUID) -> list[DataSourceView]:
    async with org_session(org_id) as session:
        rows = await session.execute(select(DataSource).order_by(DataSource.name))
        return [_view(row) for row in rows.scalars().all()]


async def get_data_source(org_id: uuid.UUID, data_source_id: uuid.UUID) -> DataSourceView:
    async with org_session(org_id) as session:
        row = (
            (await session.execute(select(DataSource).where(DataSource.id == data_source_id)))
            .scalars()
            .one_or_none()
        )
        if row is None:
            raise NotFoundError("No such data source in this organization")
        return _view(row)


async def update_data_source(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    data_source_id: uuid.UUID,
    name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    provider: SecretsProvider | None = None,
) -> DataSourceView:
    """Rename, re-address, or rotate credentials.

    Credential work happens inside the transaction that records it: if the secret
    store refuses, the row does not move either, and the caller is told about one
    failure instead of finding an inconsistency later.
    """
    store = provider if provider is not None else get_secrets_provider()

    async with org_session(org_id) as session:
        row = (
            (await session.execute(select(DataSource).where(DataSource.id == data_source_id)))
            .scalars()
            .one_or_none()
        )
        if row is None:
            raise NotFoundError("No such data source in this organization")

        settings = dict(row.settings)
        changed: list[str] = []

        if name is not None and name != row.name:
            row.name = name
            changed.append("name")

        next_host = host if host is not None else _field(settings, "host")
        next_port = port if port is not None else _port(settings)
        next_database = database if database is not None else _field(settings, "database")
        if row.host_display != _display(next_host, next_port, next_database):
            changed.append("address")

        hint = _field(settings, "username_last4")
        if username is not None or password is not None:
            existing = await store.get(row.secret_ref)
            next_username = username if username is not None else existing.get("username", "")
            await store.put(
                row.secret_ref,
                {
                    "username": next_username,
                    "password": password if password is not None else existing.get("password", ""),
                },
            )
            hint = _hint(next_username)
            changed.append("credentials")

        row.settings = _connection_settings(next_host, next_port, next_database, hint)
        row.host_display = _display(next_host, next_port, next_database)

        if changed:
            audit(
                session,
                org_id=org_id,
                actor_user_id=actor_user_id,
                action="datasource.updated",
                object_type="data_source",
                object_id=str(data_source_id),
                # Which fields moved, never what they moved to. "credentials"
                # records that a rotation happened, and nothing about it.
                details={"fields": changed, "host_display": row.host_display},
            )

        return _view(row)


async def delete_data_source(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    data_source_id: uuid.UUID,
    provider: SecretsProvider | None = None,
) -> None:
    store = provider if provider is not None else get_secrets_provider()

    async with org_session(org_id) as session:
        row = (
            (await session.execute(select(DataSource).where(DataSource.id == data_source_id)))
            .scalars()
            .one_or_none()
        )
        if row is None:
            raise NotFoundError("No such data source in this organization")

        secret_ref, name = row.secret_ref, row.name
        await session.delete(row)
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="datasource.deleted",
            object_type="data_source",
            object_id=str(data_source_id),
            details={"name": name},
        )

    # After the commit. A secret deleted for a row that then failed to delete
    # would leave a data source nobody can repair.
    await store.delete(secret_ref)


async def test_data_source(
    *, org_id: uuid.UUID, actor_user_id: uuid.UUID, data_source_id: uuid.UUID
) -> Reachability:
    """Check that the recorded address answers, and record that we asked.

    The probe runs between two transactions rather than inside one: holding a
    database transaction open across a network call to somebody else's machine is
    how a slow third party becomes a lock queue on your own.
    """
    view = await get_data_source(org_id, data_source_id)
    result = await check_reachable(host=view.host, port=view.port)

    async with org_session(org_id) as session:
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="datasource.tested",
            object_type="data_source",
            object_id=str(data_source_id),
            details={"reachable": result.reachable, "host_display": view.host_display},
        )

    return result
