"""A catalog to validate against, without a database anywhere near it.

The validator reads a ``Catalog`` and a ``Caps`` and nothing else — no session,
no connector, no credential — which is what makes the highest test density in
the repository affordable: every rule in architecture 7.5 can be asserted in
milliseconds, on both dialects, against a fixture that is a few dictionaries.

The shape mirrors the pizza demo database, so a case that fails here can be
pasted into a real session and reproduced.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from dataagent.catalog.browse import Catalog, CatalogColumnView, CatalogTableView
from dataagent.catalog.discovery import SnapshotView
from dataagent.connectors.base import ExecLimits
from dataagent.connectors.factory import caps_for
from dataagent.dal.errors import PolicyViolation
from dataagent.dal.policy import SourcePolicy
from dataagent.dal.validator import validate

#: schema -> table -> (column, type, policy). `customers.email` is masked and
#: `customers.tax_id` denied, so both halves of a column policy have something
#: to be asserted against in every clause position.
_TABLES: dict[str, dict[str, list[tuple[str, str, str]]]] = {
    "public": {
        "orders": [
            ("id", "integer", "allow"),
            ("customer_id", "integer", "allow"),
            ("ordered_at", "timestamp", "allow"),
            ("total", "numeric", "allow"),
        ],
        "customers": [
            ("id", "integer", "allow"),
            ("full_name", "text", "mask"),
            ("email", "text", "mask"),
            ("tax_id", "text", "deny"),
            ("city", "text", "allow"),
        ],
        "menu_items": [
            ("id", "integer", "allow"),
            ("name", "text", "allow"),
            ("price", "numeric", "allow"),
        ],
        "staff": [
            ("id", "integer", "allow"),
            ("full_name", "text", "mask"),
        ],
    },
    # A second schema, so "the same table name in two places" is a real case
    # rather than a hypothetical one. Only `staff` is duplicated: if every name
    # were, every test would be an ambiguity test and the interesting ones would
    # be hidden among them.
    "archive": {
        "orders_2025": [
            ("id", "integer", "allow"),
            ("total", "numeric", "allow"),
        ],
        "staff": [
            ("id", "integer", "allow"),
            ("full_name", "text", "mask"),
        ],
    },
}


def build_catalog() -> Catalog:
    tables: list[CatalogTableView] = []
    for schema_name, schema_tables in _TABLES.items():
        for table_name, columns in schema_tables.items():
            tables.append(
                CatalogTableView(
                    schema_name=schema_name,
                    table_name=table_name,
                    kind="table",
                    description=None,
                    columns=tuple(
                        CatalogColumnView(
                            id=uuid.uuid4(),
                            name=name,
                            ordinal=ordinal,
                            data_type=data_type,
                            nullable=True,
                            is_pk=name == "id",
                            description=None,
                            policy=policy,
                            policy_decided=policy != "allow",
                        )
                        for ordinal, (name, data_type, policy) in enumerate(columns, start=1)
                    ),
                )
            )
    return Catalog(
        snapshot=SnapshotView(
            id=uuid.uuid4(),
            data_source_id=uuid.uuid4(),
            version=1,
            status="active",
            captured_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            object_count=len(tables),
            error=None,
        ),
        tables=tuple(tables),
        relationships=(),
    )


def build_source(engine: str, *, max_rows: int = 1000) -> SourcePolicy:
    return SourcePolicy(
        org_id=uuid.uuid4(),
        data_source_id=uuid.uuid4(),
        engine=engine,
        caps=caps_for(engine),
        catalog=build_catalog(),
        limits=ExecLimits(max_rows=max_rows, timeout_seconds=30.0),
    )


def refuse(sql: str, source: SourcePolicy) -> PolicyViolation:
    """Assert that a statement is refused, and hand back the refusal.

    Every negative case in this suite goes through here, so "it was rejected"
    can never be satisfied by an unrelated exception: only a PolicyViolation
    counts, and the caller then asserts which one.
    """
    with pytest.raises(PolicyViolation) as caught:
        validate(sql, source=source)
    return caught.value
