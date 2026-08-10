-- Pizza chain demo database — schema v0 (plan WP0.4, architecture Part 9.2).
--
-- This schema is a fixture with two deliberate properties. Do not "fix" either.
--
--   1. There is NO order_items table. ORDERS and MENU_ITEMS exist, but nothing
--      links them, so item-level sales are genuinely unanswerable. Phase 8's
--      capability check must detect this and refuse honestly instead of
--      inventing a join. See implementation-plan §6 WP8.2.
--   2. customers.email and customers.phone are real-shaped PII. Phase 4's
--      classifier must find them and default their column policy to `mask`.
--
-- Reseeding drops and rebuilds everything, so the dataset is reproducible.

DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS staff CASCADE;
DROP TABLE IF EXISTS menu_items CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS stores CASCADE;

CREATE TABLE stores (
    id        integer PRIMARY KEY,
    name      text NOT NULL UNIQUE,
    city      text NOT NULL,
    country   text NOT NULL,
    opened_on date NOT NULL
);

COMMENT ON TABLE stores IS 'Physical restaurant locations.';

CREATE TABLE customers (
    id           integer PRIMARY KEY,
    full_name    text NOT NULL,
    email        text NOT NULL UNIQUE,
    phone        text,
    city         text NOT NULL,
    signed_up_on date NOT NULL
);

COMMENT ON TABLE customers IS 'Loyalty-programme members. Contains personal data.';
COMMENT ON COLUMN customers.email IS 'Contact email address (personal data).';
COMMENT ON COLUMN customers.phone IS 'Contact phone number (personal data).';

CREATE TABLE staff (
    id        integer PRIMARY KEY,
    store_id  integer NOT NULL REFERENCES stores (id),
    full_name text NOT NULL,
    role      text NOT NULL CHECK (role IN ('manager', 'chef', 'driver', 'server')),
    hired_on  date NOT NULL
);

CREATE TABLE menu_items (
    id        integer PRIMARY KEY,
    name      text NOT NULL UNIQUE,
    category  text NOT NULL CHECK (category IN ('pizza', 'side', 'dessert', 'drink')),
    price     numeric(6, 2) NOT NULL CHECK (price > 0),
    is_active boolean NOT NULL DEFAULT true
);

COMMENT ON TABLE menu_items IS
    'The menu. Intentionally NOT linked to orders: this database records order '
    'totals only, never line items, so per-item sales cannot be derived.';

CREATE TABLE orders (
    id           bigint PRIMARY KEY,
    order_date   date NOT NULL,
    store_id     integer NOT NULL REFERENCES stores (id),
    customer_id  integer NOT NULL REFERENCES customers (id),
    channel      text NOT NULL CHECK (channel IN ('delivery', 'pickup', 'dine_in')),
    total_amount numeric(8, 2) NOT NULL CHECK (total_amount >= 0),
    status       text NOT NULL CHECK (status IN ('completed', 'cancelled', 'refunded'))
);

COMMENT ON TABLE orders IS 'One row per order. Order value only — no line items.';
COMMENT ON COLUMN orders.total_amount IS
    'Order value in local currency. Revenue excludes cancelled and refunded orders.';

CREATE TABLE payments (
    id       bigint PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders (id),
    method   text NOT NULL CHECK (method IN ('card', 'cash', 'wallet')),
    amount   numeric(8, 2) NOT NULL,
    paid_at  timestamptz NOT NULL
);

CREATE INDEX orders_order_date_idx ON orders (order_date);
CREATE INDEX orders_store_id_order_date_idx ON orders (store_id, order_date);
CREATE INDEX orders_customer_id_idx ON orders (customer_id);
CREATE INDEX payments_order_id_idx ON payments (order_id);
CREATE INDEX staff_store_id_idx ON staff (store_id);
