-- Pizza chain demo database for SQL Server (plan WP3.3, architecture Part 9.2).
--
-- The same fixture as the PostgreSQL one, in this engine's dialect, and for a
-- different job. `seed-pizza-pg` is the dataset the agent and the evals reason
-- about; this one exists so that a *second* dialect is exercised end to end —
-- introspection from sys.*, T-SQL types, the read-only verification, and the
-- error paths. So it keeps every structural property and none of the
-- statistical ones:
--
--   * still NO order_items table, so item-level questions stay unanswerable;
--   * customers.email and customers.phone are still real-shaped personal data;
--   * a foreign key graph worth walking: orders → stores, orders → customers,
--     staff → stores, payments → orders;
--   * table and column descriptions, which on this engine means extended
--     properties rather than COMMENT ON;
--   * a second schema, so "list the tables in these schemas" is a filter with
--     something to exclude rather than a formality.
--
-- The data is generated arithmetically from a row number, never from RAND() or
-- GETDATE(), so re-running this produces exactly the same rows. There is no
-- truths.json for it: ops/seed/truths.json describes the PostgreSQL dataset, and
-- a second set of ground truths nobody asks questions of would only go stale.
--
-- Run it with `make seed.mssql`, which passes $(ReadonlyPassword).

SET NOCOUNT ON;
GO

IF DB_ID('pizza') IS NULL
    CREATE DATABASE pizza;
GO

USE pizza;
GO

-- ---------------------------------------------------------------------------
-- Schema. Dropped in dependency order; reseeding rebuilds everything.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS analytics.busy_stores;
DROP TABLE IF EXISTS dbo.payments;
DROP TABLE IF EXISTS dbo.orders;
DROP TABLE IF EXISTS dbo.staff;
DROP TABLE IF EXISTS dbo.menu_items;
DROP TABLE IF EXISTS dbo.customers;
DROP TABLE IF EXISTS dbo.stores;
GO

CREATE TABLE dbo.stores (
    id        int          NOT NULL CONSTRAINT pk_stores PRIMARY KEY,
    name      nvarchar(100) NOT NULL CONSTRAINT uq_stores_name UNIQUE,
    city      nvarchar(100) NOT NULL,
    country   nvarchar(100) NOT NULL,
    opened_on date          NOT NULL
);
GO

CREATE TABLE dbo.customers (
    id           int           NOT NULL CONSTRAINT pk_customers PRIMARY KEY,
    full_name    nvarchar(200) NOT NULL,
    email        nvarchar(320) NOT NULL CONSTRAINT uq_customers_email UNIQUE,
    phone        nvarchar(40)  NULL,
    city         nvarchar(100) NOT NULL,
    signed_up_on date          NOT NULL
);
GO

CREATE TABLE dbo.staff (
    id        int           NOT NULL CONSTRAINT pk_staff PRIMARY KEY,
    store_id  int           NOT NULL CONSTRAINT fk_staff_store REFERENCES dbo.stores (id),
    full_name nvarchar(200) NOT NULL,
    role      varchar(20)   NOT NULL
        CONSTRAINT ck_staff_role CHECK (role IN ('manager', 'chef', 'driver', 'server')),
    hired_on  date          NOT NULL
);
GO

CREATE TABLE dbo.menu_items (
    id        int           NOT NULL CONSTRAINT pk_menu_items PRIMARY KEY,
    name      nvarchar(120) NOT NULL CONSTRAINT uq_menu_items_name UNIQUE,
    category  varchar(20)   NOT NULL
        CONSTRAINT ck_menu_items_category CHECK (category IN ('pizza', 'side', 'dessert', 'drink')),
    price     decimal(6, 2) NOT NULL CONSTRAINT ck_menu_items_price CHECK (price > 0),
    is_active bit           NOT NULL CONSTRAINT df_menu_items_active DEFAULT 1
);
GO

CREATE TABLE dbo.orders (
    id           bigint        NOT NULL CONSTRAINT pk_orders PRIMARY KEY,
    order_date   date          NOT NULL,
    store_id     int           NOT NULL CONSTRAINT fk_orders_store REFERENCES dbo.stores (id),
    customer_id  int           NOT NULL CONSTRAINT fk_orders_customer REFERENCES dbo.customers (id),
    channel      varchar(20)   NOT NULL
        CONSTRAINT ck_orders_channel CHECK (channel IN ('delivery', 'pickup', 'dine_in')),
    total_amount decimal(8, 2) NOT NULL CONSTRAINT ck_orders_total CHECK (total_amount >= 0),
    status       varchar(20)   NOT NULL
        CONSTRAINT ck_orders_status CHECK (status IN ('completed', 'cancelled', 'refunded'))
);
GO

CREATE TABLE dbo.payments (
    id       bigint         NOT NULL CONSTRAINT pk_payments PRIMARY KEY,
    order_id bigint         NOT NULL CONSTRAINT fk_payments_order REFERENCES dbo.orders (id),
    method   varchar(20)    NOT NULL
        CONSTRAINT ck_payments_method CHECK (method IN ('card', 'cash', 'wallet')),
    amount   decimal(8, 2)  NOT NULL,
    paid_at  datetime2(0)   NOT NULL
);
GO

CREATE INDEX orders_order_date_idx ON dbo.orders (order_date);
CREATE INDEX orders_store_id_order_date_idx ON dbo.orders (store_id, order_date);
CREATE INDEX orders_customer_id_idx ON dbo.orders (customer_id);
CREATE INDEX payments_order_id_idx ON dbo.payments (order_id);
CREATE INDEX staff_store_id_idx ON dbo.staff (store_id);
GO

IF SCHEMA_ID('analytics') IS NULL
    EXEC ('CREATE SCHEMA analytics');
GO

CREATE VIEW analytics.busy_stores AS
SELECT s.id, s.name, COUNT(o.id) AS order_count
FROM dbo.stores s
LEFT JOIN dbo.orders o ON o.store_id = s.id
GROUP BY s.id, s.name;
GO

-- ---------------------------------------------------------------------------
-- Descriptions. MS_Description is the convention SSMS reads and writes, and
-- what the connector's introspection looks for.
-- ---------------------------------------------------------------------------
EXEC sys.sp_addextendedproperty @name = N'MS_Description',
     @value = N'Physical restaurant locations.',
     @level0type = N'SCHEMA', @level0name = N'dbo',
     @level1type = N'TABLE',  @level1name = N'stores';

EXEC sys.sp_addextendedproperty @name = N'MS_Description',
     @value = N'Loyalty-programme members. Contains personal data.',
     @level0type = N'SCHEMA', @level0name = N'dbo',
     @level1type = N'TABLE',  @level1name = N'customers';

EXEC sys.sp_addextendedproperty @name = N'MS_Description',
     @value = N'Contact email address (personal data).',
     @level0type = N'SCHEMA', @level0name = N'dbo',
     @level1type = N'TABLE',  @level1name = N'customers',
     @level2type = N'COLUMN', @level2name = N'email';

EXEC sys.sp_addextendedproperty @name = N'MS_Description',
     @value = N'Contact phone number (personal data).',
     @level0type = N'SCHEMA', @level0name = N'dbo',
     @level1type = N'TABLE',  @level1name = N'customers',
     @level2type = N'COLUMN', @level2name = N'phone';

EXEC sys.sp_addextendedproperty @name = N'MS_Description',
     @value = N'The menu. Intentionally NOT linked to orders: this database records order totals only, never line items, so per-item sales cannot be derived.',
     @level0type = N'SCHEMA', @level0name = N'dbo',
     @level1type = N'TABLE',  @level1name = N'menu_items';

EXEC sys.sp_addextendedproperty @name = N'MS_Description',
     @value = N'One row per order. Order value only — no line items.',
     @level0type = N'SCHEMA', @level0name = N'dbo',
     @level1type = N'TABLE',  @level1name = N'orders';

EXEC sys.sp_addextendedproperty @name = N'MS_Description',
     @value = N'Order value in local currency. Revenue excludes cancelled and refunded orders.',
     @level0type = N'SCHEMA', @level0name = N'dbo',
     @level1type = N'TABLE',  @level1name = N'orders',
     @level2type = N'COLUMN', @level2name = N'total_amount';
GO

-- ---------------------------------------------------------------------------
-- Data. A tally table of cross-joined constants, then arithmetic on the row
-- number — no RAND(), no GETDATE(), so two runs produce identical rows.
-- ---------------------------------------------------------------------------
DECLARE @start date = '2025-02-01';
DECLARE @days int = 546;           -- through 2026-07-31, the PostgreSQL window

INSERT INTO dbo.stores (id, name, city, country, opened_on)
VALUES (1, N'Harbour',     N'Wellington',   N'New Zealand', '2019-04-01'),
       (2, N'Northgate',   N'Auckland',     N'New Zealand', '2020-08-15'),
       (3, N'Riccarton',   N'Christchurch', N'New Zealand', '2021-02-02'),
       (4, N'Cuba Street', N'Wellington',   N'New Zealand', '2022-06-30'),
       (5, N'Papanui',     N'Christchurch', N'New Zealand', '2023-11-11');

WITH e1(n) AS (
    SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
    UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
),
e2(n) AS (SELECT 1 FROM e1 a CROSS JOIN e1 b),
e4(n) AS (SELECT 1 FROM e2 a CROSS JOIN e2 b),
tally(n) AS (SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) FROM e4 a CROSS JOIN e2 b)
INSERT INTO dbo.customers (id, full_name, email, phone, city, signed_up_on)
SELECT n,
       CONCAT(N'Customer ', n),
       CONCAT(N'customer', n, N'@example.com'),
       CASE WHEN n % 7 = 0 THEN NULL ELSE CONCAT(N'+64 21 ', 100000 + (n * 37) % 899999) END,
       CHOOSE(1 + (n * 11) % 3, N'Wellington', N'Auckland', N'Christchurch'),
       DATEADD(day, -((n * 29) % 1200), @start)
FROM tally
WHERE n <= 300;

WITH e1(n) AS (
    SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
    UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
),
tally(n) AS (SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) FROM e1 a CROSS JOIN e1 b)
INSERT INTO dbo.menu_items (id, name, category, price, is_active)
SELECT n,
       CONCAT(N'Item ', n),
       CHOOSE(1 + (n * 13) % 4, 'pizza', 'side', 'dessert', 'drink'),
       CAST(6.50 + ((n * 17) % 220) / 10.0 AS decimal(6, 2)),
       CASE WHEN n % 9 = 0 THEN 0 ELSE 1 END
FROM tally
WHERE n <= 25;

WITH e1(n) AS (
    SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
    UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
),
tally(n) AS (SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) FROM e1 a CROSS JOIN e1 b)
INSERT INTO dbo.staff (id, store_id, full_name, role, hired_on)
SELECT n,
       1 + (n * 7) % 5,
       CONCAT(N'Staff ', n),
       CHOOSE(1 + (n * 19) % 4, 'manager', 'chef', 'driver', 'server'),
       DATEADD(day, (n * 23) % @days, @start)
FROM tally
WHERE n <= 40;

WITH e1(n) AS (
    SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
    UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1 UNION ALL SELECT 1
),
e2(n) AS (SELECT 1 FROM e1 a CROSS JOIN e1 b),
e4(n) AS (SELECT 1 FROM e2 a CROSS JOIN e2 b),
tally(n) AS (SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) FROM e4 a CROSS JOIN e2 b)
INSERT INTO dbo.orders (id, order_date, store_id, customer_id, channel, total_amount, status)
SELECT CAST(n AS bigint),
       DATEADD(day, (n * 13) % @days, @start),
       1 + (n * 3) % 5,
       1 + (n * 31) % 300,
       CHOOSE(1 + (n * 5) % 3, 'delivery', 'pickup', 'dine_in'),
       CAST(12.00 + ((n * 41) % 6800) / 100.0 AS decimal(8, 2)),
       CASE WHEN n % 53 = 0 THEN 'cancelled'
            WHEN n % 97 = 0 THEN 'refunded'
            ELSE 'completed' END
FROM tally
WHERE n <= 20000;

INSERT INTO dbo.payments (id, order_id, method, amount, paid_at)
SELECT o.id,
       o.id,
       CHOOSE(1 + (CAST(o.id AS int) * 7) % 3, 'card', 'cash', 'wallet'),
       o.total_amount,
       DATEADD(minute, (CAST(o.id AS int) * 17) % 1440, CAST(o.order_date AS datetime2(0)))
FROM dbo.orders o
WHERE o.status <> 'cancelled';
GO

-- ---------------------------------------------------------------------------
-- The login a data source should actually be registered with.
--
-- db_datareader and nothing else: it may SELECT every table and may create
-- nothing, which is what makes `readonly_verified` mean something when the demo
-- is registered. sa exists to build this fixture and should never be handed to
-- the platform.
--
-- CHECK_POLICY is off because this is a local container with a documented
-- throwaway password, and the Windows password policy is not a thing a Linux
-- container should be arguing with.
-- ---------------------------------------------------------------------------
USE master;
GO

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'pizza_readonly')
    CREATE LOGIN pizza_readonly WITH PASSWORD = '$(ReadonlyPassword)',
        CHECK_POLICY = OFF, DEFAULT_DATABASE = pizza;
ELSE
    ALTER LOGIN pizza_readonly WITH PASSWORD = '$(ReadonlyPassword)';
GO

USE pizza;
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'pizza_readonly')
    CREATE USER pizza_readonly FOR LOGIN pizza_readonly;
GO

ALTER ROLE db_datareader ADD MEMBER pizza_readonly;
GO

-- Belt and braces, and a statement of intent that survives someone adding a
-- role membership by hand: whatever else is granted, these are refused.
DENY INSERT, UPDATE, DELETE, ALTER, CREATE TABLE TO pizza_readonly;
GO

SELECT CONCAT('stores=', (SELECT COUNT(*) FROM dbo.stores),
              ' customers=', (SELECT COUNT(*) FROM dbo.customers),
              ' menu_items=', (SELECT COUNT(*) FROM dbo.menu_items),
              ' staff=', (SELECT COUNT(*) FROM dbo.staff),
              ' orders=', (SELECT COUNT(*) FROM dbo.orders),
              ' payments=', (SELECT COUNT(*) FROM dbo.payments)) AS seeded;
GO
