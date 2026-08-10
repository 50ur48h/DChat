"""Declarative base and shared column conventions.

Every tenant-scoped table carries ``org_id`` and, from revision 0002, a row-level
security policy keyed on it (architecture Part 6.3). The two layers are
independent on purpose: application code filters by ``org_id`` *and* the database
refuses to hand back anything else.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Predictable constraint names. Without them Alembic invents names that differ
# between environments, and a downgrade cannot find what it is meant to drop.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: The three fixed roles (architecture Part 6.2). Stored as text with a CHECK
#: constraint rather than a PostgreSQL ENUM: adding a value to an enum type is a
#: migration that cannot run inside a transaction, and these are product concepts
#: that will change more readily than the schema should.
ROLES: tuple[str, ...] = ("admin", "contributor", "reader")

# Server-side defaults, so a row written by a migration or by psql is exactly as
# valid as one written through the ORM.
UuidPk = Annotated[
    uuid.UUID,
    mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
]
OrgId = Annotated[uuid.UUID, mapped_column(UUID(as_uuid=True), nullable=False)]
UuidRef = Annotated[uuid.UUID, mapped_column(UUID(as_uuid=True))]
CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False),
]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012
        dict[str, object]: JSONB,
        datetime: DateTime(timezone=True),
        uuid.UUID: UUID(as_uuid=True),
    }
