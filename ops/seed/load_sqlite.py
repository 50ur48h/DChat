# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.2", "python-dotenv>=1.0"]
# ///
"""Load a customer's SQLite file into a Postgres demo database, verbatim.

    make seed.fnb SQLITE=.SampleData/<their-database>.sqlite

Unlike ``seed_pizza.py`` this script invents nothing. It is a *translator*: the
tables, columns, primary keys, foreign keys, indexes and row counts that come out
of Postgres are the ones that went into SQLite, and the script fails rather than
silently dropping anything it cannot carry across. That is the whole point — the
pizza fixture is a schema this project designed, so it flatters every part of the
product that has to read a schema. A schema written by someone else does not.

**What it changes, and why each change is defensible**

* ``INTEGER`` becomes ``bigint``, ``REAL`` becomes ``double precision``,
  everything else becomes ``text``. Straight affinity mapping.
* A ``text`` column becomes ``date`` **only** when it has at least one value and
  *every* non-null value is a bare ISO ``YYYY-MM-DD``. SQLite has no date type,
  so a date in a SQLite file is *always* text; leaving it text in Postgres would
  be testing an artifact of the interchange format rather than anything about the
  customer's data. The rule is data-driven on purpose — this dataset has columns
  named ``coverage_start`` holding ``'opening'`` and ``weighing_time`` holding
  ``'7.30 pm'``, and a name-based rule would have mangled both.
  ``--no-dates`` turns the promotion off entirely.
* Views are **not** translated. SQLite's dialect is close to Postgres but not
  equal to it, and a half-working automatic port would put wrong numbers in front
  of people. Write the Postgres DDL by hand as ``<database>.views.sql`` beside
  the ``.sqlite`` file and this script picks it up; without one the views are
  skipped and it says so loudly.

Everything else — including the tables that are empty, the ``bridge_``, ``map_``
and ``meta_`` tables, and the columns whose type is a lie — is carried across
untouched, because those are exactly the things worth finding out about.

The database is rebuilt from nothing on each run, so this is idempotent and
re-runnable. It never touches ``seed-pizza-pg``.

**Nothing customer-specific belongs in this file, or beside it in git.** This
repository is public. The script is generic; the database, its data dictionary
and its hand-written view DDL all live together under ``.SampleData/``, which is
gitignored as a directory — a ported view definition carries the customer's own
literal strings, which is not obvious until you read one.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import psycopg
from dotenv import load_dotenv
from psycopg import sql

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

#: A bare ISO date, as a SQLite GLOB. GLOB has character classes and LIKE does
#: not, and the pattern is anchored by being the whole string.
ISO_DATE_GLOB: Final = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"

#: Rows per COPY batch. Large enough that 112k rows is a handful of round trips.
BATCH: Final = 5_000


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    sqlite_type: str
    pg_type: str
    not_null: bool


@dataclass(frozen=True, slots=True)
class ForeignKey:
    columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    indexes: tuple[tuple[str, tuple[str, ...], bool], ...]


def affinity(declared: str) -> str:
    """SQLite's own type-affinity rules, reduced to the three types we emit."""
    upper = declared.upper()
    if "INT" in upper:
        return "bigint"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB")):
        return "double precision"
    return "text"


def looks_like_dates(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """True when every non-null value is a bare ISO date, and there is one.

    "And there is one" matters: eight tables in this dataset are empty, and an
    empty column tells you nothing about its type. Promoting it on the strength
    of its *name* is how ``coverage_start`` — which holds ``'opening'`` — would
    have become a date column that refused to load.
    """
    quoted = f'"{table}"."{column}"'
    total, iso = cursor.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN {quoted} GLOB ? THEN 1 ELSE 0 END) "  # noqa: S608
        f'FROM "{table}" WHERE {quoted} IS NOT NULL',
        (ISO_DATE_GLOB,),
    ).fetchone()
    return bool(total) and total == iso


def read_schema(cursor: sqlite3.Cursor, *, promote_dates: bool) -> list[Table]:
    names = [
        row[0]
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    tables: list[Table] = []
    for name in names:
        columns: list[Column] = []
        primary: list[tuple[int, str]] = []
        for _, column, declared, not_null, _, pk in cursor.execute(
            f'PRAGMA table_info("{name}")'
        ).fetchall():
            pg_type = affinity(declared)
            if pg_type == "text" and promote_dates and looks_like_dates(cursor, name, column):
                pg_type = "date"
            columns.append(
                Column(
                    name=column,
                    sqlite_type=declared,
                    pg_type=pg_type,
                    not_null=bool(not_null),
                )
            )
            if pk:
                primary.append((pk, column))

        keys: list[ForeignKey] = []
        grouped: dict[int, list[tuple[str, str]]] = {}
        for row in cursor.execute(f'PRAGMA foreign_key_list("{name}")').fetchall():
            grouped.setdefault(row[0], []).append((row[3], row[4]))
        targets = {row[0]: row[2] for row in cursor.execute(f'PRAGMA foreign_key_list("{name}")')}
        for identifier, pairs in grouped.items():
            keys.append(
                ForeignKey(
                    columns=tuple(pair[0] for pair in pairs),
                    to_table=targets[identifier],
                    to_columns=tuple(pair[1] for pair in pairs),
                )
            )

        indexes: list[tuple[str, tuple[str, ...], bool]] = []
        for _, index_name, unique, origin, _ in cursor.execute(
            f'PRAGMA index_list("{name}")'
        ).fetchall():
            if origin != "c":
                # 'pk' and 'u' indexes are created by the constraints themselves.
                continue
            index_columns = tuple(
                row[2] for row in cursor.execute(f'PRAGMA index_info("{index_name}")')
            )
            indexes.append((index_name, index_columns, bool(unique)))

        tables.append(
            Table(
                name=name,
                columns=tuple(columns),
                primary_key=tuple(column for _, column in sorted(primary)),
                foreign_keys=tuple(keys),
                indexes=tuple(indexes),
            )
        )
    return tables


def coerce(value: object, pg_type: str) -> object:
    """SQLite is loosely typed; a Postgres column is not."""
    if value is None:
        return None
    if pg_type == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if pg_type == "bigint" and not isinstance(value, int):
        return int(value)
    if pg_type == "double precision" and not isinstance(value, float):
        return float(value)
    return value


def create_table(cursor: psycopg.Cursor[tuple[object, ...]], table: Table) -> None:
    pieces = [
        sql.SQL("{} {}").format(sql.Identifier(column.name), sql.SQL(column.pg_type))
        for column in table.columns
    ]
    if table.primary_key:
        pieces.append(
            sql.SQL("PRIMARY KEY ({})").format(
                sql.SQL(", ").join(sql.Identifier(name) for name in table.primary_key)
            )
        )
    cursor.execute(
        sql.SQL("CREATE TABLE {} ({})").format(
            sql.Identifier(table.name), sql.SQL(", ").join(pieces)
        )
    )


def copy_rows(
    source: sqlite3.Cursor,
    cursor: psycopg.Cursor[tuple[object, ...]],
    table: Table,
) -> int:
    columns = tuple(column.name for column in table.columns)
    types = tuple(column.pg_type for column in table.columns)
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table.name),
        sql.SQL(", ").join(sql.Identifier(name) for name in columns),
    )
    written = 0
    # Quoted for SQLite by SQLite's rules, not by psycopg's: these are two
    # different databases and only one of them is being written to.
    projection = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
    reader = source.execute(f'SELECT {projection} FROM "{table.name}"')  # noqa: S608
    with cursor.copy(statement) as copy:
        while batch := reader.fetchmany(BATCH):
            for row in batch:
                copy.write_row(
                    tuple(coerce(value, kind) for value, kind in zip(row, types, strict=True))
                )
                written += 1
    return written


def add_constraints(cursor: psycopg.Cursor[tuple[object, ...]], table: Table) -> int:
    """Foreign keys, after the rows, so load order cannot matter.

    Declared here and not left out: the join graph the capability check walks is
    built from exactly these, and a load that quietly dropped them would make the
    product look broken in a way the customer's database is not.
    """
    added = 0
    for index, key in enumerate(table.foreign_keys):
        cursor.execute(
            sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} FOREIGN KEY ({}) REFERENCES {} ({})").format(
                sql.Identifier(table.name),
                sql.Identifier(f"fk_{table.name}_{index}"),
                sql.SQL(", ").join(sql.Identifier(name) for name in key.columns),
                sql.Identifier(key.to_table),
                sql.SQL(", ").join(sql.Identifier(name) for name in key.to_columns),
            )
        )
        added += 1
    for name, columns, unique in table.indexes:
        cursor.execute(
            sql.SQL("CREATE {} INDEX {} ON {} ({})").format(
                sql.SQL("UNIQUE" if unique else ""),
                sql.Identifier(name),
                sql.Identifier(table.name),
                sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            )
        )
    return added


def grant_readonly_role(cursor: psycopg.Cursor[tuple[object, ...]], database: str) -> str:
    """The login the platform is actually registered with (B-006).

    Connect, and read, and nothing else — now or on the tables the next run of
    this script creates. Same shape as ``seed_pizza.grant_readonly_role``, and
    kept separate rather than shared because these are two fixture scripts, not
    a library.
    """
    role = os.environ.get("SEED_FNB_READONLY_USER", "fnb_readonly")
    password = os.environ.get("SEED_FNB_READONLY_PASSWORD")
    if not password:
        sys.exit(
            "SEED_FNB_READONLY_PASSWORD is not set.\n"
            "Copy the block from .env.example into .env and choose a value."
        )

    identifier = sql.Identifier(role)
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if cursor.fetchone() is None:
        cursor.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                identifier, sql.Literal(password)
            )
        )
    else:
        cursor.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                identifier, sql.Literal(password)
            )
        )

    cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(identifier))
    cursor.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(identifier))
    cursor.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(database), identifier)
    )
    # **And from PUBLIC, which the line above does not touch** (B-155). PostgreSQL
    # grants CONNECT and TEMP on every new database to PUBLIC, so revoking on the
    # *role* leaves every login on the server able to open a connection to every
    # database on it. No data crosses — `public` stays owner-only — but `pg_class`
    # is not privilege-filtered the way `information_schema` is, so a neighbouring
    # login can read the whole table and column list. Measured on the demo server
    # on 2026-08-27: a freshly created read-only login read 29 table names and 251
    # column names out of the customer database next door.
    cursor.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
    )
    cursor.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), identifier)
    )
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
    cursor.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(identifier))
    cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(
            identifier
        )
    )
    return role


def connect_postgres() -> psycopg.Connection[tuple[object, ...]]:
    password = os.environ.get("SEED_FNB_PASSWORD")
    if not password:
        sys.exit("SEED_FNB_PASSWORD is not set. Copy the block from .env.example into .env.")
    return psycopg.connect(
        host=os.environ.get("SEED_FNB_HOST", "localhost"),
        port=int(os.environ.get("SEED_FNB_PORT", "6544")),
        dbname=os.environ.get("SEED_FNB_DB", "fnb"),
        user=os.environ.get("SEED_FNB_USER", "fnb"),
        password=password,
    )


def run_views(cursor: psycopg.Cursor[tuple[object, ...]], path: Path) -> int:
    created = 0
    for piece in path.read_text(encoding="utf-8").split(";\n"):
        statement = piece.strip()
        # A chunk that is only comments and blank lines is not a statement.
        body = "\n".join(
            line for line in statement.splitlines() if line.strip() and not line.startswith("--")
        )
        if not body.strip():
            continue
        cursor.execute(statement)  # type: ignore[arg-type]
        created += statement.upper().count("CREATE VIEW")
    return created


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, type=Path, help="The SQLite file to load")
    parser.add_argument(
        "--views",
        type=Path,
        default=None,
        help="Hand-written Postgres DDL for the views. Defaults to <database>.views.sql "
        "beside the SQLite file, so the customer's SQL stays with the customer's data.",
    )
    parser.add_argument(
        "--no-dates",
        action="store_true",
        help="Keep ISO-date text columns as text instead of promoting them to date",
    )
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    if not args.sqlite.exists():
        sys.exit(f"No such file: {args.sqlite}")

    if args.views is None:
        beside = args.sqlite.with_suffix(".views.sql")
        args.views = beside if beside.exists() else None

    source = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    reader = source.cursor()
    tables = read_schema(reader, promote_dates=not args.no_dates)

    promoted = [
        f"{table.name}.{column.name}"
        for table in tables
        for column in table.columns
        if column.pg_type == "date"
    ]
    print(f"Read {len(tables)} table(s) from {args.sqlite.name}.")
    print(f"Promoted to date: {', '.join(promoted) if promoted else '(none)'}\n")

    database = os.environ.get("SEED_FNB_DB", "fnb")
    connection = connect_postgres()
    connection.autocommit = False
    keys = 0
    with connection, connection.cursor() as cursor:
        # A clean rebuild. `make seed.fnb` is run repeatedly and a half-migrated
        # schema is worse than no schema; the read-only role survives because it
        # is a cluster object, and its grants are reapplied below.
        cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
        cursor.execute("CREATE SCHEMA public")

        for table in tables:
            create_table(cursor, table)
        for table in tables:
            written = copy_rows(reader, cursor, table)
            # The identifier came out of this same SQLite file a moment ago.
            expected = reader.execute(
                f'SELECT COUNT(*) FROM "{table.name}"'  # noqa: S608
            ).fetchone()[0]
            if written != expected:
                sys.exit(f"{table.name}: copied {written} rows, SQLite has {expected}")
            print(f"  {table.name:28s} {written:>7,} rows")
        for table in tables:
            keys += add_constraints(cursor, table)

        views = 0
        if args.views is not None:
            views = run_views(cursor, args.views)
        role = grant_readonly_role(cursor, database)

    # ANALYZE, outside the transaction and before anything profiles this.
    # PostgreSQL writes `reltuples = -1` for a table it has never analysed, and
    # the catalog reads that as *unknown* and prints no row count at all — which
    # is the right call (unknown must not become "empty") but leaves a freshly
    # loaded 30-row table looking exactly like a freshly loaded empty one. Any
    # real load ends this way; autovacuum would get here on its own eventually,
    # and "eventually" is after the demo.
    with connect_postgres() as connection:
        connection.autocommit = True
        connection.execute("ANALYZE")
    print("\n  ANALYZE done — row estimates are available to the catalog")

    # Verify against the loaded database rather than against our own bookkeeping,
    # and include the views: a hand-ported view is the one thing here that could
    # be quietly wrong, so it is the one thing checked against the original.
    view_names = [
        row[0]
        for row in reader.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name"
        ).fetchall()
    ]
    with connect_postgres() as connection, connection.cursor() as cursor:
        for name in [table.name for table in tables] + (view_names if args.views else []):
            expected = reader.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # noqa: S608
            actual = cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(name))
            ).fetchone()[0]
            if actual != expected:
                sys.exit(f"{name}: Postgres has {actual} rows, SQLite has {expected}")
        checked = len(view_names) if args.views else 0
        print(f"  verified {len(tables)} table(s) and {checked} view(s)")

    print(f"\n{len(tables)} table(s), {keys} foreign key(s), {views} view(s).")
    if args.views is None:
        print("No --views file given, so the source's views were NOT created.")
    print(f"Read-only login ready: {role}\n")
    source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
