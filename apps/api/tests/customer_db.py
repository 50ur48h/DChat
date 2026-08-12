"""A stand-in for a customer's database, shared by the tests that need one.

Deliberately *not* the pizza fixture: connector tests must not depend on
``make seed`` having been run, or they would run only on a developer's machine
with a compose stack up. This builds a small database with a foreign key,
comments, a view and a genuinely read-only login, on the same PostgreSQL server
the rest of the suite already needs.

It lives beside ``conftest.py`` rather than inside it so that more than one test
module can import the type without importing a conftest.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.connectors.postgres import PostgresConnector

#: Cluster-wide, so it is created idempotently and never dropped — another test
#: database on the same server may still be using it.
READER_ROLE = "dataagent_connector_reader"
READER_LOGIN = "connector-test-only"

#: This server is on the test machine and serves no certificate, so it is exactly
#: the case the policy calls local (B-013). Tests that care about the mode itself
#: pass their own.
LOCAL_TLS_MODE = "prefer"

SCHEMA = """
CREATE TABLE regions (
    id   integer PRIMARY KEY,
    name text NOT NULL
);
COMMENT ON TABLE regions IS 'Sales regions.';

CREATE TABLE shops (
    id        integer PRIMARY KEY,
    region_id integer NOT NULL REFERENCES regions (id),
    name      text NOT NULL,
    opened_on date
);
COMMENT ON COLUMN shops.name IS 'Trading name.';

CREATE VIEW busy_shops AS SELECT id, name FROM shops;

-- Nothing references this table and nothing it references, on purpose. The pizza
-- fixture has the same hole between orders and menu_items, and Phase 8's honest
-- refusal depends on the catalog recording an absence faithfully rather than
-- inventing a plausible join. Asserted here because this fixture runs in CI on
-- every commit, where the seeded pizza database does not.
CREATE TABLE products (
    id       integer PRIMARY KEY,
    name     text NOT NULL,
    price    numeric(6, 2) NOT NULL
);
COMMENT ON TABLE products IS 'Deliberately unrelated to shops.';

INSERT INTO products (id, name, price) VALUES (1, 'Margherita', 12.50), (2, 'Cola', 3.00);

-- Real-shaped personal data, which the pizza fixture also has in
-- customers.email. The profiler must find it, mask it on the way into the
-- catalog, and default its policy to `mask` before anyone has looked.
-- `contact` is named to be unhelpful on purpose: it must be caught by the shape
-- of its values, not by its name.
CREATE TABLE people (
    id      integer PRIMARY KEY,
    email   text NOT NULL,
    contact text,
    city    text NOT NULL
);
COMMENT ON COLUMN people.email IS 'Contact email address (personal data).';

INSERT INTO people (id, email, contact, city) VALUES
    (1, 'ada@example.com',    'ada.lovelace@example.org',  'Wellington'),
    (2, 'grace@example.com',  'grace.hopper@example.org',  'Auckland'),
    (3, 'linus@example.com',  'linus.torvalds@example.org','Wellington'),
    (4, 'edsger@example.com', NULL,                        'Christchurch');

INSERT INTO regions (id, name) VALUES (1, 'North'), (2, 'South');
INSERT INTO shops (id, region_id, name, opened_on) VALUES
    (1, 1, 'Harbour', '2020-01-01'),
    (2, 1, 'Northgate', '2021-02-02'),
    (3, 2, 'Riccarton', '2022-03-03'),
    (4, 2, 'Papanui', '2023-04-04'),
    (5, 1, 'Cuba Street', '2024-05-05');
"""

GRANTS = f"""
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {READER_ROLE};
GRANT USAGE ON SCHEMA public TO {READER_ROLE};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER_ROLE};
"""


@dataclass(frozen=True)
class CustomerDatabase:
    """Two ways in: the owner, who may write, and a login that may only read."""

    url: URL
    reader_username: str
    reader_password: str

    @property
    def host(self) -> str:
        return self.url.host or "localhost"

    @property
    def port(self) -> int:
        return self.url.port or 5432

    @property
    def database(self) -> str:
        return self.url.database or "postgres"

    def reader(self, tls_mode: str = LOCAL_TLS_MODE) -> PostgresConnector:
        return PostgresConnector(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.reader_username,
            password=self.reader_password,
            tls_mode=tls_mode,
        )

    def owner(self, tls_mode: str = LOCAL_TLS_MODE) -> PostgresConnector:
        return PostgresConnector(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.url.username or "postgres",
            password=self.url.password or "",
            tls_mode=tls_mode,
        )


async def build(url: URL) -> CustomerDatabase:
    """Populate an empty database and give it a read-only login."""
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for statement in filter(None, (part.strip() for part in SCHEMA.split(";"))):
                await connection.execute(text(statement))

            await connection.execute(
                text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READER_ROLE}')
                    THEN CREATE ROLE {READER_ROLE} NOLOGIN;
                    END IF;
                END
                $$;
                """)
            )
            await connection.execute(
                text(f"ALTER ROLE {READER_ROLE} WITH LOGIN PASSWORD '{READER_LOGIN}'")
            )
            await connection.execute(
                text(f'GRANT CONNECT ON DATABASE "{url.database}" TO {READER_ROLE}')
            )
            for statement in filter(None, (part.strip() for part in GRANTS.split(";"))):
                await connection.execute(text(statement))
    finally:
        await engine.dispose()

    return CustomerDatabase(url=url, reader_username=READER_ROLE, reader_password=READER_LOGIN)
