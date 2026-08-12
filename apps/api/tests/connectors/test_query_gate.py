"""The gate that stands between the agent and arbitrary SQL.

Architecture Part 5.1 wants "run unvalidated SQL" to be impossible rather than
discouraged. Python cannot give that literally, so the property is built from
three checks that *can* hold, and this file is where each one is asserted:

1. ``execute`` accepts only a ``ValidatedQuery`` — a signature, checked by
   pyright at every call site.
2. A ``ValidatedQuery`` needs a grant, and a grant needs a name from a short
   list — a runtime refusal.
3. Nothing in ``src`` builds either one except the two modules that may — a scan
   of the source tree, so a fourth caller cannot appear quietly.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

from dataagent.connectors import introspection
from dataagent.connectors.base import (
    SANCTIONED_VALIDATORS,
    ExecLimits,
    PolicyGrant,
    ValidatedQuery,
)
from dataagent.connectors.postgres import PostgresConnector

SRC = Path(__file__).resolve().parents[2] / "src" / "dataagent"

#: The only files in src/ allowed to name these constructors: the module that
#: defines them, and the module that holds the grant.
CONSTRUCTION_SITES = {
    SRC / "connectors" / "base.py",
    SRC / "connectors" / "introspection.py",
}


def test_the_sanctioned_list_is_exactly_what_it_should_be() -> None:
    """Widening this is a reviewed change, not an import somebody added."""
    assert {
        "dataagent.connectors.introspection",
        "dataagent.dal.validator",
    } == SANCTIONED_VALIDATORS


def test_an_unsanctioned_module_cannot_hold_a_grant() -> None:
    with pytest.raises(PermissionError, match="may not declare SQL validated"):
        PolicyGrant("dataagent.agent.tools")


def test_a_query_cannot_be_built_without_a_grant() -> None:
    with pytest.raises(TypeError, match="PolicyGrant"):
        ValidatedQuery("not-a-grant", sql="SELECT 1", dialect="postgres")  # pyright: ignore[reportArgumentType]


def test_a_validated_query_records_who_approved_it() -> None:
    query = introspection.schemas()

    assert query.origin == "dataagent.connectors.introspection"
    assert query.dialect == "postgres"
    assert query.sql.startswith("SELECT")


def test_its_repr_does_not_carry_the_sql_or_the_parameters() -> None:
    """A repr ends up in tracebacks and log lines; parameters can be data."""
    query = introspection.tables(["public", "secret_schema"])

    rendered = repr(query)

    assert "secret_schema" not in rendered
    assert "SELECT" not in rendered
    assert query.sql_hash in rendered


def test_execute_accepts_only_a_validated_query() -> None:
    """The signature is the first layer, so assert the signature."""
    hints = typing.get_type_hints(PostgresConnector.execute)

    assert hints["query"] is ValidatedQuery
    assert hints["limits"] is ExecLimits


def test_only_sanctioned_modules_build_queries() -> None:
    """A scan, because the runtime check can be satisfied by naming a lie.

    Anything in ``src`` that constructs a grant or a query must be one of the two
    modules that are allowed to. Phase 5 adds ``dal/validator.py`` here, in the
    same PR that adds it to SANCTIONED_VALIDATORS.
    """
    pattern = re.compile(r"\b(PolicyGrant|ValidatedQuery)\s*\(")
    offenders: list[str] = []

    for path in SRC.rglob("*.py"):
        if path in CONSTRUCTION_SITES:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(SRC)))

    assert offenders == [], (
        f"these modules construct validated SQL and are not allowed to: {offenders}. "
        "Only the SQL policy module may (architecture Part 5.1, 7.5)."
    )


def test_the_scan_would_notice_a_new_construction_site(tmp_path: Path) -> None:
    """A guard that has never failed is a guard nobody has tested."""
    rogue = tmp_path / "tool.py"
    rogue.write_text(
        "query = ValidatedQuery(PolicyGrant('dataagent.agent'), sql='DROP TABLE t', "
        "dialect='postgres')",
        encoding="utf-8",
    )

    pattern = re.compile(r"\b(PolicyGrant|ValidatedQuery)\s*\(")

    assert pattern.search(rogue.read_text(encoding="utf-8")) is not None


def test_introspection_never_interpolates_its_parameters() -> None:
    """Schema names arrive as bound parameters, not as text in the statement."""
    query = introspection.columns(["public", "sales"])

    assert "public" not in query.sql
    assert query.parameters == ([["public", "sales"]][0],)
    assert "$1" in query.sql
