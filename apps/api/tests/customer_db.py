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


#: A customer database that declares **nothing** — no primary keys, no foreign
#: keys — which is the shape most of them arrive in and the one that made the
#: product refuse every real question (B-145). Modelled on `miseq`, including
#: both of the traps that dataset supplied.
UNDECLARED_SCHEMA = """
CREATE TABLE dim_outlet (
    outlet_key  integer NOT NULL,
    outlet_name text NOT NULL
);
INSERT INTO dim_outlet (outlet_key, outlet_name) VALUES
    (1, 'Harbour'), (2, 'Northgate'), (3, 'Riccarton'), (4, 'Papanui'), (5, 'Cuba Street');

-- The join the product needs: every one of these outlet_key values exists in
-- dim_outlet, and the column repeats, which is what makes it a many-to-one.
CREATE TABLE fact_sale (
    sale_id    integer NOT NULL,
    outlet_key integer NOT NULL,
    amount     numeric(8, 2) NOT NULL
);
INSERT INTO fact_sale (sale_id, outlet_key, amount)
SELECT g, 1 + (g % 5), (g * 7 % 90) + 10 FROM generate_series(1, 40) AS g;

-- **The first trap, and the one the live run fell into.** outlet_key holds 1..5,
-- and 1..5 is inside 1..9 — so containment against transfer_id is true and means
-- nothing. It must lose to dim_outlet, which its five values account for
-- entirely, rather than being resolved by what the columns are called.
CREATE TABLE fact_transfer (
    transfer_id integer NOT NULL,
    moved_qty   integer NOT NULL
);
INSERT INTO fact_transfer (transfer_id, moved_qty)
SELECT g, g * 2 FROM generate_series(1, 9) AS g;

-- **The second trap, which is the owner's test.** Same column name on both
-- sides, and not one value in common.
CREATE TABLE dim_item (
    item_key  text NOT NULL,
    item_name text NOT NULL
);
INSERT INTO dim_item (item_key, item_name) VALUES
    ('I1', 'Flat White'), ('I2', 'Long Black'), ('I3', 'Mocha'),
    ('I4', 'Chai'), ('I5', 'Scone'), ('I6', 'Muffin');

CREATE TABLE map_item_key (
    item_key text NOT NULL,
    source   text NOT NULL
);
INSERT INTO map_item_key (item_key, source) VALUES
    ('WB-1', 'a'), ('WB-2', 'a'), ('WB-3', 'b'), ('WB-4', 'b'),
    ('WB-1', 'c'), ('WB-2', 'c'), ('WB-3', 'd'), ('WB-4', 'd');
"""


async def build_undeclared(url: URL) -> CustomerDatabase:
    """`build`, against a database that declares no keys at all."""
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for statement in filter(None, (part.strip() for part in UNDECLARED_SCHEMA.split(";"))):
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
