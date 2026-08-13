"""The only SQL this product runs before the DAL exists.

Almost every statement here is a constant, and the values that vary are bound
parameters — schema names, passed to the driver and never interpolated — so
there is no path from a request to the text of a query.

The exception is the sampling pair at the bottom of the file (WP4.2), which
interpolates *identifiers*, because no dialect can bind one: `SELECT * FROM $1`
is not a thing. Those two functions carry their own rules, written out where
they are defined. They are the only place in this module where a statement is
assembled, and keeping them here rather than in the profiler is deliberate: the
quoting is then subject to the same review as everything else that may declare
SQL runnable.

That is why this module holds a ``PolicyGrant``: it is a validator in the sense
that matters, by construction rather than by inspection. The DAL's real
validator joins it in Phase 5 (architecture Part 7.5), and these templates keep
running exactly as they do now.

Metadata comes from ``pg_catalog`` and from ``sys.*`` rather than from
``information_schema``: those views are faster, they carry comments and primary
keys without extra joins, and they do not hide objects behind privilege filters
in ways that vary by version.

**Both dialects live in this one module, and that is the point.** The grant is
held by a module name, so a second introspection module would mean a third entry
on ``SANCTIONED_VALIDATORS`` — widening the list of things allowed to declare SQL
runnable in order to add a *file*. One module keeps that list at two names, and
keeps "every statement this product runs before Phase 5" a single thing to read.

The two halves differ in one visible way. Postgres filters by schema in SQL with
a bound array; T-SQL has no array parameter, and building an ``IN`` list would
mean interpolating names into a statement, which nothing here will do. So the
T-SQL metadata templates take no parameters at all and the SQL Server connector
filters in Python. Slightly more data crosses the wire, and no *name* is ever
put into a metadata statement.
"""

from __future__ import annotations

from collections.abc import Sequence

from dataagent.connectors.base import PolicyGrant, ValidatedQuery

__all__ = [
    "READONLY_PROBE_TABLE",
    "pg_columns",
    "pg_foreign_keys",
    "pg_readonly_evidence",
    "pg_row_estimates",
    "pg_sample",
    "pg_schemas",
    "pg_tables",
    "pg_tls_status",
    "pg_write_probe_sql",
    "quote_pg",
    "quote_tsql",
    "tsql_columns",
    "tsql_foreign_keys",
    "tsql_readonly_evidence",
    "tsql_row_estimates",
    "tsql_sample",
    "tsql_schemas",
    "tsql_tables",
    "tsql_tls_status",
    "tsql_write_probe_sql",
]

_GRANT = PolicyGrant(__name__)

POSTGRES = "postgres"

#: sqlglot's name for the SQL Server dialect, and the one Phase 5 will transpile
#: to. "mssql" is the *engine* in `data_sources`; "tsql" is the language.
TSQL = "tsql"

#: Written out rather than built from a constant: every statement in this module
#: is a literal a reader can check against the engine's documentation, and an
#: f-string in a SQL context is the habit this codebase refuses even when the
#: interpolated value is its own.
_PG_SCHEMAS = """
SELECT n.nspname AS schema_name
FROM pg_namespace n
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND n.nspname NOT LIKE 'pg\\_temp%'
  AND n.nspname NOT LIKE 'pg\\_toast%'
  AND has_schema_privilege(n.nspname, 'USAGE')
ORDER BY n.nspname
"""

_PG_TABLES = """
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

_PG_COLUMNS = """
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

_PG_FOREIGN_KEYS = """
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
_PG_READONLY_EVIDENCE = """
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

#: Whether *this* connection is encrypted, according to the server rather than
#: according to the driver we configured (B-013). Restricted to our own backend,
#: which every role may see regardless of privileges.
_PG_TLS_STATUS = """
SELECT s.ssl AS encrypted,
       s.version AS tls_version,
       s.cipher AS cipher
FROM pg_stat_ssl s
WHERE s.pid = pg_backend_pid()
"""

#: A fixed name, so the probe below needs no interpolation of any kind. It is
#: created inside a transaction that is always rolled back, and in practice it
#: is never created at all — that is the point of running it.
READONLY_PROBE_TABLE = "dataagent_readonly_probe"

_PG_WRITE_PROBE = f"CREATE TABLE {READONLY_PROBE_TABLE} (probe integer)"


# ---------------------------------------------------------------------------
# SQL Server (WP3.3)
#
# No parameters anywhere: see the module docstring. Every filter that Postgres
# expresses with a bound array is done by the connector afterwards.
# ---------------------------------------------------------------------------

#: The schemas SQL Server creates for its own fixed database roles. They are
#: never a customer's data, and listing them would put twelve empty names in
#: front of every user of the catalog browser.
_TSQL_SCHEMAS = """
SELECT s.name AS schema_name
FROM sys.schemas s
WHERE s.name NOT IN (
        'sys', 'INFORMATION_SCHEMA', 'guest', 'db_owner', 'db_accessadmin',
        'db_securityadmin', 'db_ddladmin', 'db_backupoperator', 'db_datareader',
        'db_datawriter', 'db_denydatareader', 'db_denydatawriter')
  AND HAS_PERMS_BY_NAME(QUOTENAME(s.name), 'SCHEMA', 'SELECT') = 1
ORDER BY s.name
"""

#: MS_Description is the convention SSMS writes table and column comments under,
#: and the only thing resembling COMMENT ON in this engine.
_TSQL_TABLES = """
SELECT SCHEMA_NAME(o.schema_id) AS schema_name,
       o.name AS table_name,
       CASE WHEN o.type = 'V' THEN 'view' ELSE 'table' END AS kind,
       CAST(ep.value AS nvarchar(4000)) AS comment
FROM sys.objects o
LEFT JOIN sys.extended_properties ep
       ON ep.major_id = o.object_id
      AND ep.minor_id = 0
      AND ep.class = 1
      AND ep.name = 'MS_Description'
WHERE o.type IN ('U', 'V')
  AND HAS_PERMS_BY_NAME(
          QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name),
          'OBJECT', 'SELECT') = 1
ORDER BY schema_name, table_name
"""

#: The type is assembled to read like a declaration — `varchar(50)`, not
#: `varchar` with a length hidden in another column — because that string is what
#: Phase 4's catalog shows and what Phase 5 reasons about. nvarchar and nchar
#: store max_length in bytes, hence the halving; -1 means (max).
_TSQL_COLUMNS = """
SELECT SCHEMA_NAME(o.schema_id) AS schema_name,
       o.name AS table_name,
       c.name AS column_name,
       CASE
           WHEN t.name IN ('varchar', 'char', 'varbinary', 'binary')
               THEN t.name + '(' + CASE WHEN c.max_length = -1 THEN 'max'
                                        ELSE CAST(c.max_length AS varchar(11)) END + ')'
           WHEN t.name IN ('nvarchar', 'nchar')
               THEN t.name + '(' + CASE WHEN c.max_length = -1 THEN 'max'
                                        ELSE CAST(c.max_length / 2 AS varchar(11)) END + ')'
           WHEN t.name IN ('decimal', 'numeric')
               THEN t.name + '(' + CAST(c.precision AS varchar(11))
                           + ', ' + CAST(c.scale AS varchar(11)) + ')'
           ELSE t.name
       END AS data_type,
       c.is_nullable,
       c.column_id AS ordinal,
       CASE WHEN pk.column_id IS NULL THEN 0 ELSE 1 END AS is_primary_key,
       CAST(ep.value AS nvarchar(4000)) AS comment
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id
JOIN sys.types t ON t.user_type_id = c.user_type_id
LEFT JOIN (
    SELECT ic.object_id, ic.column_id
    FROM sys.index_columns ic
    JOIN sys.indexes i ON i.object_id = ic.object_id AND i.index_id = ic.index_id
    WHERE i.is_primary_key = 1
) pk ON pk.object_id = c.object_id AND pk.column_id = c.column_id
LEFT JOIN sys.extended_properties ep
       ON ep.major_id = c.object_id
      AND ep.minor_id = c.column_id
      AND ep.class = 1
      AND ep.name = 'MS_Description'
WHERE o.type IN ('U', 'V')
  AND HAS_PERMS_BY_NAME(
          QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name),
          'OBJECT', 'SELECT') = 1
ORDER BY schema_name, table_name, ordinal
"""

#: One row per key *column*, in key order. Postgres aggregates into arrays with
#: array_agg; T-SQL's equivalents are version-dependent and the connector has to
#: group the rows anyway, so this returns the rows and lets it.
_TSQL_FOREIGN_KEYS = """
SELECT fk.name AS constraint_name,
       SCHEMA_NAME(pt.schema_id) AS from_schema,
       pt.name AS from_table,
       pc.name AS from_column,
       SCHEMA_NAME(rt.schema_id) AS to_schema,
       rt.name AS to_table,
       rc.name AS to_column,
       fkc.constraint_column_id AS key_ordinal
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.tables pt ON pt.object_id = fk.parent_object_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id
                   AND pc.column_id = fkc.parent_column_id
JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id
                   AND rc.column_id = fkc.referenced_column_id
ORDER BY from_schema, from_table, constraint_name, key_ordinal
"""

#: The same question the Postgres template asks, in this engine's vocabulary:
#: what may this *login* do, according to the server rather than to us.
#:
#: The product version is assembled rather than taken from @@VERSION, which also
#: names the host operating system and build — detail that would end up in an
#: API response for no benefit.
#:
#: COALESCE around every membership test: IS_ROLEMEMBER and IS_SRVROLEMEMBER
#: return NULL for a role that does not exist or a principal they cannot resolve,
#: and NULL read as "not a member" is exactly the wrong way round.
_TSQL_READONLY_EVIDENCE = """
SELECT USER_NAME() AS role_name,
       CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
       COALESCE(IS_SRVROLEMEMBER('sysadmin'), 0) AS is_sysadmin,
       COALESCE(IS_ROLEMEMBER('db_owner'), 0) AS is_db_owner,
       COALESCE(IS_ROLEMEMBER('db_datawriter'), 0) AS is_db_datawriter,
       COALESCE(IS_ROLEMEMBER('db_ddladmin'), 0) AS is_ddladmin,
       COALESCE(HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE'), 0)
           AS can_create_in_database,
       (SELECT COUNT(*)
          FROM sys.objects o
         WHERE o.type = 'U'
           AND (HAS_PERMS_BY_NAME(
                    QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name),
                    'OBJECT', 'INSERT') = 1
             OR HAS_PERMS_BY_NAME(
                    QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name),
                    'OBJECT', 'UPDATE') = 1
             OR HAS_PERMS_BY_NAME(
                    QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name),
                    'OBJECT', 'DELETE') = 1
             OR HAS_PERMS_BY_NAME(
                    QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name),
                    'OBJECT', 'ALTER') = 1)) AS writable_tables
"""

#: Unlike pg_stat_ssl, this view needs VIEW SERVER STATE — a permission a
#: genuinely read-only login will not have. The connector treats an error here as
#: "the server would not say" and falls back to what the driver was told to
#: demand, which it labels as such.
_TSQL_TLS_STATUS = """
SELECT c.encrypt_option
FROM sys.dm_exec_connections c
WHERE c.session_id = @@SPID
"""

_TSQL_WRITE_PROBE = f"CREATE TABLE {READONLY_PROBE_TABLE} (probe int)"


def _query(sql: str, parameters: Sequence[object] = ()) -> ValidatedQuery:
    return ValidatedQuery(_GRANT, sql=sql.strip(), dialect=POSTGRES, parameters=parameters)


def _tsql(sql: str, parameters: Sequence[object] = ()) -> ValidatedQuery:
    return ValidatedQuery(_GRANT, sql=sql.strip(), dialect=TSQL, parameters=parameters)


def pg_schemas() -> ValidatedQuery:
    """Every schema this role may look inside, minus the engine's own."""
    return _query(_PG_SCHEMAS)


def pg_tables(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_PG_TABLES, [list(in_schemas)])


def pg_columns(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_PG_COLUMNS, [list(in_schemas)])


def pg_foreign_keys(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_PG_FOREIGN_KEYS, [list(in_schemas)])


def pg_readonly_evidence(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_PG_READONLY_EVIDENCE, [list(in_schemas)])


def pg_tls_status() -> ValidatedQuery:
    """Ask the server whether it is talking to us in the clear."""
    return _query(_PG_TLS_STATUS)


def pg_write_probe_sql() -> str:
    """The one statement in this codebase that *tries* to write, and must fail.

    Returned as text rather than as a ``ValidatedQuery`` on purpose: it is not a
    query, it is an experiment, and the connector runs it through a separate path
    that always rolls back. Giving it the validated type would make the type mean
    two different things.
    """
    return _PG_WRITE_PROBE


def tsql_schemas() -> ValidatedQuery:
    """Every schema this login may look inside, minus the engine's own."""
    return _tsql(_TSQL_SCHEMAS)


def tsql_tables() -> ValidatedQuery:
    """Every table and view. Filtering by schema happens in the connector."""
    return _tsql(_TSQL_TABLES)


def tsql_columns() -> ValidatedQuery:
    return _tsql(_TSQL_COLUMNS)


def tsql_foreign_keys() -> ValidatedQuery:
    """One row per key column; the connector groups them into keys."""
    return _tsql(_TSQL_FOREIGN_KEYS)


def tsql_readonly_evidence() -> ValidatedQuery:
    return _tsql(_TSQL_READONLY_EVIDENCE)


def tsql_tls_status() -> ValidatedQuery:
    """Ask the server whether this session is encrypted. Often refused."""
    return _tsql(_TSQL_TLS_STATUS)


def tsql_write_probe_sql() -> str:
    """The T-SQL half of the experiment above, run and rolled back the same way."""
    return _TSQL_WRITE_PROBE


# ---------------------------------------------------------------------------
# Sampling, for the profiler (WP4.2)
#
# The one place in this module where a statement is not a constant, and the
# reason is unavoidable: an identifier cannot be a bound parameter in any SQL
# dialect. `SELECT * FROM $1` is not a thing.
#
# So the rules are tighter here than anywhere else, and they are three:
#
#   1. The identifiers come from the *engine's own catalog*, read by the
#      templates above. They are not user input and never have been — the only
#      thing a person chooses is which data source to profile.
#   2. Every one is quoted by the function below, which doubles the closing
#      quote character. That is the complete escaping rule for a quoted
#      identifier in both dialects, and `test_a_hostile_identifier_is_quoted`
#      holds it to that with names built to break out.
#   3. Row limits stay bound parameters, because they can be.
#
# The alternative — reading pg_stats and DBCC SHOW_STATISTICS instead of
# sampling — needs no identifiers at all, and was rejected: those are whatever
# ANALYZE last left behind, they need privileges a read-only login does not
# have on SQL Server, and architecture Part 5.2 asks for a sample.
# ---------------------------------------------------------------------------


def quote_pg(identifier: str) -> str:
    """A PostgreSQL quoted identifier. Doubling `"` is the whole rule."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def quote_tsql(identifier: str) -> str:
    """A T-SQL delimited identifier. Brackets, and `]` doubled."""
    escaped = identifier.replace("]", "]]")
    return f"[{escaped}]"


def pg_sample(schema: str, table: str, columns: Sequence[str], limit: int) -> ValidatedQuery:
    """Up to ``limit`` rows of the named columns.

    No ORDER BY: ordering a sample means sorting the table, which is the one
    thing a bounded profile must not do to somebody's production database. The
    consequence — that this is the *first* n rows rather than a random n — is
    recorded honestly by the profiler rather than dressed up.
    """
    projection = ", ".join(quote_pg(column) for column in columns)
    relation = f"{quote_pg(schema)}.{quote_pg(table)}"
    return ValidatedQuery(
        _GRANT,
        sql=f"SELECT {projection} FROM {relation} LIMIT $1",  # noqa: S608 - quoted above
        dialect=POSTGRES,
        parameters=[limit],
    )


def tsql_sample(schema: str, table: str, columns: Sequence[str], limit: int) -> ValidatedQuery:
    """The same sample, in the dialect that spells LIMIT as TOP."""
    projection = ", ".join(quote_tsql(column) for column in columns)
    relation = f"{quote_tsql(schema)}.{quote_tsql(table)}"
    return ValidatedQuery(
        _GRANT,
        sql=f"SELECT TOP (?) {projection} FROM {relation}",  # noqa: S608 - quoted above
        dialect=TSQL,
        parameters=[limit],
    )


#: Row counts as the engine already estimates them — no scan, and no identifier
#: interpolation, because these ask about every table at once and the profiler
#: looks up the one it wants.
#:
#: PostgreSQL writes ``reltuples = -1`` for a table that has never been analysed,
#: meaning *unknown*. Clamping that to zero would turn "we do not know" into "it
#: is empty", which a table card would then state as fact, so it stays NULL.
_PG_ROW_ESTIMATES = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS row_estimate
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'm')
  AND n.nspname = ANY($1::text[])
"""

#: sys.partitions is visible to a login that may read the table, unlike
#: dm_db_partition_stats, which needs VIEW DATABASE STATE.
_TSQL_ROW_ESTIMATES = """
SELECT SCHEMA_NAME(o.schema_id) AS schema_name,
       o.name AS table_name,
       SUM(p.rows) AS row_estimate
FROM sys.objects o
JOIN sys.partitions p ON p.object_id = o.object_id AND p.index_id IN (0, 1)
WHERE o.type = 'U'
GROUP BY SCHEMA_NAME(o.schema_id), o.name
"""


def pg_row_estimates(in_schemas: Sequence[str]) -> ValidatedQuery:
    return _query(_PG_ROW_ESTIMATES, [list(in_schemas)])


def tsql_row_estimates() -> ValidatedQuery:
    return _tsql(_TSQL_ROW_ESTIMATES)
