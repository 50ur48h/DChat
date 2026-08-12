"""The only SQL this product runs before the DAL exists.

Every statement here is a constant. The only values that vary are bound
parameters — schema names, passed to the driver as parameters and never
interpolated — so there is no path from a request to the text of a query.

That is why this module holds a ``PolicyGrant``: it is a validator in the sense
that matters, by construction rather than by inspection. The DAL's real
validator joins it in Phase 5 (architecture Part 7.5), and these templates keep
running exactly as they do now.

Metadata comes from ``pg_catalog`` rather than ``information_schema``: the views
are faster, they carry comments and primary keys without extra joins, and they
do not hide objects behind privilege filters in ways that vary by version.
"""

from __future__ import annotations

from collections.abc import Sequence

from dataagent.connectors.base import PolicyGrant, ValidatedQuery

__all__ = [
    "READONLY_PROBE_TABLE",
    "columns",
    "foreign_keys",
    "readonly_evidence",
    "schemas",
    "tables",
    "write_probe_sql",
]

_GRANT = PolicyGrant(__name__)

POSTGRES = "postgres"

#: Written out rather than built from a constant: every statement in this module
#: is a literal a reader can check against the engine's documentation, and an
#: f-string in a SQL context is the habit this codebase refuses even when the
#: interpolated value is its own.
_SCHEMAS = """
SELECT n.nspname AS schema_name
FROM pg_namespace n
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND n.nspname NOT LIKE 'pg\\_temp%'
  AND n.nspname NOT LIKE 'pg\\_toast%'
  AND has_schema_privilege(n.nspname, 'USAGE')
ORDER BY n.nspname
"""

_TABLES = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       CASE WHEN c.relkind IN ('v', 'm') THEN 'view' ELSE 'table' END AS kind,
       obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND n.nspname = ANY($1::text[])
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY n.nspname, c.relname
"""

_COLUMNS = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS is_nullable,
       a.attnum AS ordinal,
       COALESCE(pk.is_primary_key, false) AS is_primary_key,
       col_description(c.oid, a.attnum) AS comment
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN LATERAL (
    SELECT true AS is_primary_key
    FROM pg_index i
    WHERE i.indrelid = c.oid AND i.indisprimary AND a.attnum = ANY(i.indkey)
) pk ON true
WHERE a.attnum > 0
  AND NOT a.attisdropped
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND n.nspname = ANY($1::text[])
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY n.nspname, c.relname, a.attnum
"""

_FOREIGN_KEYS = """
SELECT con.conname AS constraint_name,
       fn.nspname AS from_schema,
       fc.relname AS from_table,
       (SELECT array_agg(att.attname ORDER BY u.ord)
          FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
          JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = u.attnum) AS from_columns,
       tn.nspname AS to_schema,
       tc.relname AS to_table,
       (SELECT array_agg(att.attname ORDER BY u.ord)
          FROM unnest(con.confkey) WITH ORDINALITY AS u(attnum, ord)
          JOIN pg_attribute att
            ON att.attrelid = con.confrelid AND att.attnum = u.attnum) AS to_columns
FROM pg_constraint con
JOIN pg_class fc ON fc.oid = con.conrelid
JOIN pg_namespace fn ON fn.oid = fc.relnamespace
JOIN pg_class tc ON tc.oid = con.confrelid
JOIN pg_namespace tn ON tn.oid = tc.relnamespace
WHERE con.contype = 'f'
  AND fn.nspname = ANY($1::text[])
ORDER BY fn.nspname, fc.relname, con.conname
"""

#: One row of privilege facts about the credentials we were given. Nothing here
#: writes, and nothing here trusts our own session settings: it asks the
#: database what this *role* is allowed to do (architecture Part 7.5).
_READONLY_EVIDENCE = """
SELECT current_user AS role_name,
       version() AS server_version,
       COALESCE((SELECT r.rolsuper FROM pg_roles r WHERE r.rolname = current_user), false)
           AS is_superuser,
       COALESCE((SELECT r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user), false)
           AS can_bypass_rls,
       COALESCE((SELECT pg_has_role(current_user, r.oid, 'USAGE')
                   FROM pg_roles r WHERE r.rolname = 'pg_write_all_data'), false)
           AS writes_everything,
       has_database_privilege(current_database(), 'CREATE') AS can_create_in_database,
       (SELECT count(*) FROM pg_namespace n
         WHERE n.nspname = ANY($1::text[])
           AND has_schema_privilege(n.nspname, 'CREATE')) AS writable_schemas,
       (SELECT count(*) FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY($1::text[])
           AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND (has_table_privilege(c.oid, 'INSERT')
             OR has_table_privilege(c.oid, 'UPDATE')
             OR has_table_privilege(c.oid, 'DELETE')
             OR has_table_privilege(c.oid, 'TRUNCATE'))) AS writable_tables
"""

#: A fixed name, so the probe below needs no interpolation of any kind. It is
#: created inside a transaction that is always rolled back, and in practice it
#: is never created at all — that is the point of running it.
READONLY_PROBE_TABLE = "dataagent_readonly_probe"

_WRITE_PROBE = f"CREATE TABLE {READONLY_PROBE_TABLE} (probe integer)"


def _query(sql: str, parameters: Sequence[object] = ()) -> ValidatedQuery:
    return ValidatedQuery(_GRANT, sql=sql.strip(), dialect=POSTGRES, parameters=parameters)


def schemas() -> ValidatedQuery:
    """Every schema this role may look inside, minus the engine's own."""
    return _query(_SCHEMAS)


def tables(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_TABLES, [list(in_schemas)])


def columns(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_COLUMNS, [list(in_schemas)])


def foreign_keys(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_FOREIGN_KEYS, [list(in_schemas)])


def readonly_evidence(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_READONLY_EVIDENCE, [list(in_schemas)])


def write_probe_sql() -> str:
    """The one statement in this codebase that *tries* to write, and must fail.

    Returned as text rather than as a ``ValidatedQuery`` on purpose: it is not a
    query, it is an experiment, and the connector runs it through a separate path
    that always rolls back. Giving it the validated type would make the type mean
    two different things.
    """
    return _WRITE_PROBE
