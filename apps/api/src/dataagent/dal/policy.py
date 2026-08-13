"""Everything the validator needs to judge one query, loaded once (arch 7.1).

Validating a statement needs three things that all belong to a data source: the
**catalog** it must resolve identifiers against, the **column policies** that say
what may be read, and the **capabilities** of the engine it will run on. Fetching
those per query would mean several round trips to the platform database before a
single row of anyone's data is read, on a path an agent walks in a loop.

So they are loaded together and cached for a few seconds. Three things about
that cache are deliberate:

* **Keys are org-scoped**, per architecture Part 6.4 — there is no cache entry
  that is not stamped with the organization it belongs to, so there is no shape
  of bug in which one tenant's lookup finds another's catalog.
* **The TTL is short, and it is a leak window.** An Admin who denies a column
  expects that to be true immediately; a cache that holds the old answer for a
  minute is a minute in which a denied column is still queryable. So the setter
  invalidates this cache directly (``catalog.routes``), and the TTL is the
  backstop for anything that changes without telling us — not the mechanism.
* **It caches the *policy*, never a credential and never a row.** Everything in
  ``SourcePolicy`` is metadata an Admin could read on a screen.

``Caps`` comes from the connector module for the engine rather than from a live
connection: the DAL must be able to judge a query without opening a socket to a
customer's database, and what LIMIT is spelled on SQL Server does not depend on
whether that server is currently up.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from dataagent.catalog.browse import Catalog, active_catalog
from dataagent.config import Settings, get_settings
from dataagent.connectors.base import Caps, ExecLimits
from dataagent.connectors.factory import caps_for
from dataagent.datasources.service import get_data_source

__all__ = [
    "POLICY_TTL_SECONDS",
    "SourcePolicy",
    "invalidate_all",
    "invalidate_source",
    "source_policy",
]

#: How long a loaded context may be reused. Seconds rather than minutes: this is
#: the window in which a revoked column policy is still honoured, and the only
#: thing it is here to spare is a burst of identical reads inside one agent run.
POLICY_TTL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """One data source, as the validator sees it.

    Immutable, and safe to hold: no credential, no connection, no rows.
    """

    org_id: uuid.UUID
    data_source_id: uuid.UUID
    engine: str
    #: Per-engine truth — dialect, quoting, how a deadline is imposed. Nothing in
    #: ``dal`` branches on an engine name; it asks this.
    caps: Caps
    #: The active snapshot, with each column's effective policy already resolved
    #: (allow | mask | deny — D-013).
    catalog: Catalog
    #: The ceiling every execution runs under. A caller may ask for less and
    #: never for more, which is what makes this a policy rather than a default.
    limits: ExecLimits

    @property
    def dialect(self) -> str:
        return self.caps.dialect

    def limits_for(self, max_rows: int | None) -> ExecLimits:
        """Bounds for one execution: the caller's wish, clamped to the ceiling.

        A caller asking for more rows than policy allows is not an error — it is
        an agent guessing at a number — so it is quietly held to the ceiling
        rather than refused. Asking for fewer is honoured exactly.
        """
        if max_rows is None or max_rows >= self.limits.max_rows:
            return self.limits
        return ExecLimits(max_rows=max(max_rows, 1), timeout_seconds=self.limits.timeout_seconds)


_CACHE: dict[tuple[uuid.UUID, uuid.UUID], tuple[float, SourcePolicy]] = {}


async def source_policy(
    org_id: uuid.UUID, data_source_id: uuid.UUID, *, settings: Settings | None = None
) -> SourcePolicy:
    """Load — or reuse — everything needed to validate against one data source.

    Raises ``NotFoundError`` when the source does not belong to this
    organization, and ``NoCatalogError`` when it has never been discovered. Both
    are the caller's to translate: the DAL does not invent an empty catalog,
    because "no tables exist" and "we have not looked" must not answer the same.
    """
    key = (org_id, data_source_id)
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < POLICY_TTL_SECONDS:
        return cached[1]

    # Both reads are org-scoped and go through the tenant session, so a source
    # belonging to another organization is not found rather than found and
    # rejected.
    resolved = settings if settings is not None else get_settings()
    source = await get_data_source(org_id, data_source_id)
    policy = SourcePolicy(
        org_id=org_id,
        data_source_id=data_source_id,
        engine=source.engine,
        caps=caps_for(source.engine),
        catalog=await active_catalog(org_id, data_source_id),
        limits=ExecLimits(
            max_rows=resolved.dal_max_rows, timeout_seconds=resolved.dal_timeout_seconds
        ),
    )
    _CACHE[key] = (now, policy)
    return policy


def invalidate_source(org_id: uuid.UUID, data_source_id: uuid.UUID) -> None:
    """Forget one source's context — called when a policy or catalog changes.

    Cheap, and the reason the TTL is allowed to exist at all: a masking decision
    an Admin has just made takes effect on the next query, not in half a minute.
    """
    _CACHE.pop((org_id, data_source_id), None)


def invalidate_all() -> None:
    """Empty the cache. For tests, and for a process that has just migrated."""
    _CACHE.clear()
