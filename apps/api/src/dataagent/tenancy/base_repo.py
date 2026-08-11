"""Org-scoped repository base — the *first* of the two isolation layers.

Row-level security already makes a missing filter harmless, so why filter again?
Because defence in depth means neither layer is allowed to be the only one
(architecture Part 6.3). RLS is the net; this is not walking off the beam. The
duplication is the point, and the RLS proof suite exists precisely to show the
net still holds when this layer is deliberately bypassed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession


class OrgScopedRepository[ModelT]:
    """Reads and writes rows of one model, for one organization."""

    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        model: type[ModelT],
        *,
        tenant_key: str = "org_id",
    ) -> None:
        self._session = session
        self._org_id = org_id
        self._model = model
        self._tenant_key = tenant_key

        if not hasattr(model, tenant_key):
            raise AttributeError(
                f"{model.__name__} has no '{tenant_key}' column, so it cannot be "
                "scoped to an organization. Pass tenant_key= for tables whose "
                "tenant column is named differently (organizations uses 'id')."
            )

    @property
    def session(self) -> AsyncSession:
        return self._session

    @property
    def org_id(self) -> uuid.UUID:
        return self._org_id

    def _tenant_column(self) -> Any:
        return getattr(self._model, self._tenant_key)

    def select(self) -> Select[tuple[ModelT]]:
        """A SELECT already narrowed to this organization.

        Start every query from here rather than from a bare ``select(Model)``.
        """
        return select(self._model).where(self._tenant_column() == self._org_id)

    def scoped(self, statement: Select[tuple[ModelT]]) -> Select[tuple[ModelT]]:
        """Narrow a statement someone else built."""
        return statement.where(self._tenant_column() == self._org_id)

    async def list_all(self) -> Sequence[ModelT]:
        result = await self._session.execute(self.select())
        return result.scalars().all()

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        statement = self.select().where(getattr(self._model, "id") == entity_id)  # noqa: B009
        result = await self._session.execute(statement)
        return result.scalars().one_or_none()

    def add(self, entity: ModelT) -> ModelT:
        """Stage a new row, forcing its tenant column to this organization.

        Setting it here rather than trusting the caller means a wrong ``org_id``
        cannot arrive from a request body; the RLS ``WITH CHECK`` clause would
        reject it anyway, and this turns that rejection into an impossibility.
        """
        setattr(entity, self._tenant_key, self._org_id)
        self._session.add(entity)
        return entity
