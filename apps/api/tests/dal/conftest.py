"""Fixtures for the DAL suite. The catalog they are built from is in
``catalog_fixture``, which the tests also import directly when a case needs to
name its engine rather than take both.
"""

from __future__ import annotations

import pytest
from catalog_fixture import build_source

from dataagent.dal.policy import SourcePolicy


@pytest.fixture
def pg() -> SourcePolicy:
    return build_source("pg")


@pytest.fixture
def mssql() -> SourcePolicy:
    return build_source("mssql")


@pytest.fixture(params=["pg", "mssql"], ids=["postgres", "sqlserver"])
def either(request: pytest.FixtureRequest) -> SourcePolicy:
    """Both dialects, for every rule that is not about one of them.

    A rule proven on PostgreSQL alone is a rule proven on half the product: the
    two engines parse, quote and spell things differently, and Phase 5 exists
    because "it worked on the one we tested" is not a security position.
    """
    engine: str = request.param
    return build_source(engine)
