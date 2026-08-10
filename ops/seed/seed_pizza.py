# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.2", "python-dotenv>=1.0"]
# ///
"""Build the pizza-chain demo dataset (plan WP0.4, architecture Part 9.2).

Run it with ``make seed``. It is a standalone uv script rather than part of
``apps/api`` because it is a fixture generator, not product code — the API must
never depend on it.

Three properties are load-bearing for later phases and are asserted at the end
of every run, so drift fails here rather than in a Phase 8 demo:

* **Reproducible.** One fixed RNG seed and one fixed end date, so reseeding
  yields byte-identical data. Nothing reads the wall clock.
* **Large enough to be honest.** More than 50 000 orders, so query plans,
  timeouts and profiling budgets meet a realistic table.
* **A real decline to find.** Revenue in the last eight weeks is ~12% below the
  eight before it, caused entirely by delivery orders at one store. Phase 8's
  research loop has to locate that, and Phase 9's evals depend on the number.

Every run also writes ``ops/seed/truths.json``: the dataset's ground truth, which
the Phase 9 eval harness reads instead of hardcoding expected answers. That file
is committed, and ``--check`` regenerates it and fails if the two disagree, so
the fixture and the numbers the evals trust can never drift apart silently.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final, NamedTuple

import psycopg
from dotenv import load_dotenv
from psycopg import sql

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SCHEMA_FILE: Final = Path(__file__).with_name("pizza_schema.sql")
TRUTHS_FILE: Final = Path(__file__).with_name("truths.json")

# --------------------------------------------------------------------------
# Fixed generation parameters. Changing any of these changes the dataset, and
# therefore the expected answers in ops/evals — treat them as a contract.
# --------------------------------------------------------------------------
RNG_SEED: Final = 20260810
START_DATE: Final = date(2025, 2, 1)
END_DATE: Final = date(2026, 7, 31)
DECLINE_WEEKS: Final = 8

N_CUSTOMERS: Final = 8_000
LOYAL_CUSTOMERS: Final = 800  # the ~10% who order most often
ORDERS_PER_DAY: Final = 118.0
GROWTH_OVER_PERIOD: Final = 0.10  # gentle upward trend across the 18 months
# Kept small on purpose: a strong annual cycle would put several points of
# "decline" into every store at once and blur the signal Phase 8 must find.
SEASONAL_AMPLITUDE: Final = 0.02

# The store and channel whose collapse causes the decline.
DECLINE_STORE_ID: Final = 3
DECLINE_CHANNEL: Final = "delivery"
DECLINE_DROP_PROBABILITY: Final = 0.53

# Guard rails checked before anything is written.
MIN_ORDERS: Final = 50_000
DECLINE_TARGET_RANGE: Final = (-0.15, -0.09)
# The rest of the business must stay roughly flat, or the decline is not
# "concentrated" and Phase 8's expected finding would be wrong.
UNAFFECTED_TOLERANCE: Final = 0.04

WEEKDAY_FACTOR: Final = (0.85, 0.82, 0.90, 1.00, 1.35, 1.45, 1.15)  # Mon…Sun
CHANNEL_MEAN_VALUE: Final = {"delivery": 34.0, "pickup": 27.5, "dine_in": 41.0}
STATUS_WEIGHTS: Final = (("completed", 0.930), ("cancelled", 0.045), ("refunded", 0.025))
PAYMENT_WEIGHTS: Final = (("card", 0.62), ("wallet", 0.22), ("cash", 0.16))

STORES: Final = (
    (1, "Harbourview", "Wellington", "New Zealand", date(2019, 3, 12)),
    (2, "Cuba Street", "Wellington", "New Zealand", date(2020, 7, 1)),
    (3, "Northgate", "Auckland", "New Zealand", date(2018, 5, 20)),
    (4, "Ponsonby Road", "Auckland", "New Zealand", date(2021, 2, 8)),
    (5, "Riccarton", "Christchurch", "New Zealand", date(2022, 9, 15)),
    (6, "Papanui", "Christchurch", "New Zealand", date(2024, 1, 22)),
)
# Northgate is the flagship: biggest store, heaviest delivery mix. Its delivery
# business is deliberately ~22% of group revenue, because that is what lets a
# believable partial collapse (rather than a total shutdown) move the group
# number by the ~12% the Phase 8 scenario is built around.
STORE_WEIGHTS: Final = (0.13, 0.11, 0.34, 0.17, 0.14, 0.11)
CHANNEL_MIX_DEFAULT: Final = (("delivery", 0.42), ("pickup", 0.33), ("dine_in", 0.25))
CHANNEL_MIX_FLAGSHIP: Final = (("delivery", 0.62), ("pickup", 0.23), ("dine_in", 0.15))

FIRST_NAMES: Final = (
    "Aroha",
    "Ben",
    "Chloe",
    "Daniel",
    "Emma",
    "Finn",
    "Grace",
    "Hemi",
    "Isla",
    "Jack",
    "Kiri",
    "Liam",
    "Maia",
    "Noah",
    "Olivia",
    "Piper",
    "Quinn",
    "Ruby",
    "Sam",
    "Tane",
    "Ursula",
    "Vikram",
    "Willow",
    "Xanthe",
    "Yusuf",
    "Zara",
)
LAST_NAMES: Final = (
    "Anderson",
    "Brown",
    "Chen",
    "Davies",
    "Edwards",
    "Fletcher",
    "Green",
    "Harris",
    "Ihaka",
    "Jones",
    "Kaur",
    "Lawson",
    "Martin",
    "Ngata",
    "OConnor",
    "Patel",
    "Quinn",
    "Roberts",
    "Singh",
    "Taylor",
    "Ualesi",
    "Vaughan",
    "Walker",
    "Young",
)
CITIES: Final = ("Wellington", "Auckland", "Christchurch", "Hamilton", "Dunedin")

MENU_ITEMS: Final = (
    ("Margherita", "pizza", 16.50),
    ("Pepperoni", "pizza", 19.00),
    ("Hawaiian", "pizza", 18.50),
    ("Meat Lovers", "pizza", 23.00),
    ("Vegetarian Supreme", "pizza", 20.50),
    ("Four Cheese", "pizza", 21.00),
    ("BBQ Chicken", "pizza", 22.00),
    ("Spicy Salami", "pizza", 21.50),
    ("Mushroom Truffle", "pizza", 24.50),
    ("Prawn and Chorizo", "pizza", 25.00),
    ("Marinara", "pizza", 15.00),
    ("Capricciosa", "pizza", 22.50),
    ("Quattro Stagioni", "pizza", 23.50),
    ("Pumpkin and Feta", "pizza", 20.00),
    ("Lamb and Rosemary", "pizza", 24.00),
    ("Smoked Salmon", "pizza", 26.00),
    ("Buffalo Chicken", "pizza", 22.00),
    ("Pesto Verde", "pizza", 21.00),
    ("Diavola", "pizza", 22.50),
    ("Calzone Classico", "pizza", 20.50),
    ("Garlic Bread", "side", 7.50),
    ("Cheesy Garlic Bread", "side", 9.00),
    ("Potato Wedges", "side", 9.50),
    ("Buffalo Wings", "side", 13.00),
    ("Greek Salad", "side", 12.50),
    ("Caesar Salad", "side", 13.50),
    ("Onion Rings", "side", 8.50),
    ("Mozzarella Sticks", "side", 11.00),
    ("Coleslaw", "side", 6.00),
    ("Seasoned Fries", "side", 8.00),
    ("Tiramisu", "dessert", 11.00),
    ("Chocolate Lava Cake", "dessert", 12.00),
    ("Gelato Trio", "dessert", 9.50),
    ("Cannoli", "dessert", 10.00),
    ("Affogato", "dessert", 8.50),
    ("Cheesecake Slice", "dessert", 10.50),
    ("Cola 1.5L", "drink", 5.50),
    ("Lemonade 1.5L", "drink", 5.50),
    ("Sparkling Water", "drink", 4.00),
    ("Orange Juice", "drink", 5.00),
    ("Craft Lager", "drink", 9.00),
    ("House Red 750ml", "drink", 28.00),
)

STAFF_ROLES: Final = (("manager", 2), ("chef", 4), ("server", 3), ("driver", 3))


class Order(NamedTuple):
    id: int
    order_date: date
    store_id: int
    customer_id: int
    channel: str
    total_amount: Decimal
    status: str


def money(value: float) -> Decimal:
    """Round to cents the way an accountant would, not the way floats do."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def weighted_choice(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    roll = rng.random() * sum(weight for _, weight in options)
    cumulative = 0.0
    for name, weight in options:
        cumulative += weight
        if roll < cumulative:
            return name
    return options[-1][0]


def build_customers(rng: random.Random) -> list[tuple[int, str, str, str, str, date]]:
    """Customers with realistic-shaped but unmistakably fake contact details.

    Addresses use the RFC 2606 reserved domain and phone numbers the reserved
    555-01xx range, so nothing here can reach a real person — this repository is
    public and the rows land in a database we hand to a classifier.
    """
    customers: list[tuple[int, str, str, str, str, date]] = []
    span_days = (END_DATE - START_DATE).days

    for customer_id in range(1, N_CUSTOMERS + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        email = f"{first.lower()}.{last.lower()}{customer_id}@example.com"
        phone = f"+64-555-01{customer_id % 100:02d}"
        signed_up = (
            START_DATE
            - timedelta(days=rng.randint(0, 400))
            + timedelta(days=rng.randint(0, span_days) if rng.random() < 0.55 else 0)
        )
        customers.append(
            (customer_id, f"{first} {last}", email, phone, rng.choice(CITIES), signed_up)
        )

    return customers


def build_staff(rng: random.Random) -> list[tuple[int, int, str, str, date]]:
    staff: list[tuple[int, int, str, str, date]] = []
    staff_id = 1

    for store_id, _, _, _, opened_on in STORES:
        for role, count in STAFF_ROLES:
            for _ in range(count):
                hired = opened_on + timedelta(days=rng.randint(0, 1_400))
                name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                staff.append((staff_id, store_id, name, role, min(hired, END_DATE)))
                staff_id += 1

    return staff


def pick_store(rng: random.Random) -> int:
    roll = rng.random()
    cumulative = 0.0
    for index, weight in enumerate(STORE_WEIGHTS):
        cumulative += weight
        if roll < cumulative:
            return STORES[index][0]
    return STORES[-1][0]


def daily_order_count(rng: random.Random, day: date, day_index: int, total_days: int) -> int:
    trend = 1.0 + GROWTH_OVER_PERIOD * (day_index / total_days)
    seasonal = 1.0 + SEASONAL_AMPLITUDE * math.sin(2 * math.pi * day.timetuple().tm_yday / 365.0)
    noise = rng.gauss(1.0, 0.07)
    expected = ORDERS_PER_DAY * WEEKDAY_FACTOR[day.weekday()] * trend * seasonal * noise
    return max(0, round(expected))


def build_orders(rng: random.Random) -> list[Order]:
    orders: list[Order] = []
    order_id = 1
    total_days = (END_DATE - START_DATE).days
    decline_starts = END_DATE - timedelta(days=DECLINE_WEEKS * 7 - 1)

    for offset in range(total_days + 1):
        day = START_DATE + timedelta(days=offset)
        in_decline = day >= decline_starts

        for _ in range(daily_order_count(rng, day, offset, total_days)):
            store_id = pick_store(rng)
            mix = CHANNEL_MIX_FLAGSHIP if store_id == DECLINE_STORE_ID else CHANNEL_MIX_DEFAULT
            channel = weighted_choice(rng, mix)

            # The whole decline lives in this branch: during the last eight
            # weeks most delivery orders at one store simply never happen.
            if (
                in_decline
                and store_id == DECLINE_STORE_ID
                and channel == DECLINE_CHANNEL
                and rng.random() < DECLINE_DROP_PROBABILITY
            ):
                continue

            customer_id = (
                rng.randint(1, LOYAL_CUSTOMERS)
                if rng.random() < 0.35
                else rng.randint(1, N_CUSTOMERS)
            )
            amount = CHANNEL_MEAN_VALUE[channel] * math.exp(rng.gauss(0.0, 0.35))
            orders.append(
                Order(
                    id=order_id,
                    order_date=day,
                    store_id=store_id,
                    customer_id=customer_id,
                    channel=channel,
                    total_amount=money(max(6.0, amount)),
                    status=weighted_choice(rng, STATUS_WEIGHTS),
                )
            )
            order_id += 1

    return orders


def build_payments(
    rng: random.Random, orders: list[Order]
) -> list[tuple[int, int, str, Decimal, datetime]]:
    """One payment per order that was actually paid for. Cancelled orders have none."""
    payments: list[tuple[int, int, str, Decimal, datetime]] = []
    payment_id = 1

    for order in orders:
        if order.status == "cancelled":
            continue
        paid_at = datetime.combine(
            order.order_date,
            time(hour=rng.randint(11, 21), minute=rng.randint(0, 59), second=rng.randint(0, 59)),
            tzinfo=UTC,
        )
        method = weighted_choice(rng, PAYMENT_WEIGHTS)
        payments.append((payment_id, order.id, method, order.total_amount, paid_at))
        payment_id += 1

    return payments


def revenue_between(
    orders: list[Order], start: date, end: date, *, segment: str = "all"
) -> Decimal:
    """Revenue as the business defines it: cancelled and refunded orders do not count.

    ``segment`` narrows to the store/channel carrying the injected decline
    (``"affected"``) or to everything else (``"rest"``), which is how the
    concentration of the decline gets verified rather than assumed.
    """

    def in_segment(order: Order) -> bool:
        affected = order.store_id == DECLINE_STORE_ID and order.channel == DECLINE_CHANNEL
        if segment == "affected":
            return affected
        if segment == "rest":
            return not affected
        return True

    return sum(
        (
            order.total_amount
            for order in orders
            if start <= order.order_date <= end
            and order.status == "completed"
            and in_segment(order)
        ),
        Decimal("0"),
    )


def pct_change(recent: Decimal, previous: Decimal) -> float:
    return float(recent / previous) - 1.0 if previous else 0.0


def cash(value: Decimal) -> float:
    """A money amount as a JSON number, rounded once so it cannot drift."""
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def ratio(value: float) -> float:
    return round(value, 6)


class Bucket:
    """Running order count and revenue for one slice of the dataset."""

    __slots__ = ("orders", "revenue")

    def __init__(self) -> None:
        self.orders = 0
        self.revenue = Decimal("0")

    def add(self, amount: Decimal) -> None:
        self.orders += 1
        self.revenue += amount

    def render(self, label: str, key: object) -> dict[str, object]:
        return {
            label: key,
            "orders": self.orders,
            "revenue": cash(self.revenue),
            "average_order_value": cash(self.revenue / self.orders) if self.orders else 0.0,
        }


def build_truths(
    orders: list[Order],
    customers: list[tuple[int, str, str, str, str, date]],
    staff: list[tuple[int, int, str, str, date]],
    menu_size: int,
) -> dict[str, object]:
    """The ground truth of this dataset, for the Phase 9 eval harness to read.

    Evals must load their expected answers from ``truths.json`` rather than
    hardcoding numbers: the fixture and the expectations then cannot drift apart,
    because both come from the same generator run. Everything here uses the same
    definition of revenue the semantic layer will use - completed orders only.
    """
    completed = [order for order in orders if order.status == "completed"]
    store_names = {store_id: name for store_id, name, *_ in STORES}
    weekday_names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

    decline_start = END_DATE - timedelta(days=DECLINE_WEEKS * 7 - 1)
    previous_start = decline_start - timedelta(days=DECLINE_WEEKS * 7)
    previous_end = decline_start - timedelta(days=1)

    by_store: defaultdict[int, Bucket] = defaultdict(Bucket)
    by_channel: defaultdict[str, Bucket] = defaultdict(Bucket)
    by_store_channel: defaultdict[tuple[int, str], Bucket] = defaultdict(Bucket)
    by_month: defaultdict[str, Bucket] = defaultdict(Bucket)
    by_weekday: defaultdict[int, Bucket] = defaultdict(Bucket)
    by_week: defaultdict[date, Bucket] = defaultdict(Bucket)

    for order in completed:
        week_start = order.order_date - timedelta(days=order.order_date.weekday())
        by_store[order.store_id].add(order.total_amount)
        by_channel[order.channel].add(order.total_amount)
        by_store_channel[order.store_id, order.channel].add(order.total_amount)
        by_month[order.order_date.strftime("%Y-%m")].add(order.total_amount)
        by_weekday[order.order_date.weekday()].add(order.total_amount)
        by_week[week_start].add(order.total_amount)

    status_counts = dict.fromkeys((status for status, _ in STATUS_WEIGHTS), 0)
    orders_per_customer: defaultdict[int, int] = defaultdict(int)
    for order in orders:
        status_counts[order.status] += 1
        orders_per_customer[order.customer_id] += 1

    repeat_customers = sum(1 for count in orders_per_customer.values() if count > 1)

    staff_per_store: defaultdict[int, int] = defaultdict(int)
    for _, store_id, *_ in staff:
        staff_per_store[store_id] += 1

    july_2026 = [o for o in orders if o.order_date.strftime("%Y-%m") == "2026-07"]
    march_window = [o for o in completed if date(2026, 3, 1) <= o.order_date <= date(2026, 3, 15)]

    def segment_change(segment: str) -> float:
        return ratio(
            pct_change(
                revenue_between(orders, decline_start, END_DATE, segment=segment),
                revenue_between(orders, previous_start, previous_end, segment=segment),
            )
        )

    return {
        "$comment": (
            "Ground truth for the pizza demo dataset, written by "
            "ops/seed/seed_pizza.py. Regenerate with `make seed` or `make truths`; "
            "`make check.truths` fails if this file and the generator disagree. "
            "Phase 9 evals must read expected answers from here, never hardcode them."
        ),
        "definitions": {
            "revenue": "SUM(orders.total_amount) WHERE status = 'completed'",
            "average_order_value": "revenue / completed order count",
        },
        "generator": {
            "script": "ops/seed/seed_pizza.py",
            "rng_seed": RNG_SEED,
            "window_start": START_DATE.isoformat(),
            "window_end": END_DATE.isoformat(),
        },
        "row_counts": {
            "stores": len(STORES),
            "customers": len(customers),
            "staff": len(staff),
            "menu_items": menu_size,
            "orders": len(orders),
            "payments": sum(1 for order in orders if order.status != "cancelled"),
        },
        "orders": {
            "total": len(orders),
            "completed": len(completed),
            "by_status": status_counts,
            "cancelled_rate": ratio(status_counts["cancelled"] / len(orders)),
            "in_july_2026": len(july_2026),
            "busiest_weekday": weekday_names[
                max(by_weekday, key=lambda day: by_weekday[day].orders)
            ],
        },
        "revenue": {
            "total": cash(sum((o.total_amount for o in completed), Decimal("0"))),
            "by_store": [
                by_store[key].render("store_id", key) | {"store_name": store_names[key]}
                for key in sorted(by_store)
            ],
            "by_channel": [by_channel[key].render("channel", key) for key in sorted(by_channel)],
            "by_store_channel": [
                by_store_channel[key].render("store_channel", list(key))
                for key in sorted(by_store_channel)
            ],
            "by_month": [by_month[key].render("month", key) for key in sorted(by_month)],
            "top_store_by_revenue": max(by_store, key=lambda key: by_store[key].revenue),
            "slowest_week_starting": min(
                by_week, key=lambda week: by_week[week].revenue
            ).isoformat(),
        },
        "decline": {
            "weeks": DECLINE_WEEKS,
            "recent_window": [decline_start.isoformat(), END_DATE.isoformat()],
            "previous_window": [previous_start.isoformat(), previous_end.isoformat()],
            "store_id": DECLINE_STORE_ID,
            "store_name": store_names[DECLINE_STORE_ID],
            "channel": DECLINE_CHANNEL,
            "overall_pct": segment_change("all"),
            "affected_pct": segment_change("affected"),
            "unaffected_pct": segment_change("rest"),
        },
        "customers": {
            "total": len(customers),
            "with_at_least_one_order": len(orders_per_customer),
            "repeat_customers": repeat_customers,
            "repeat_rate": ratio(repeat_customers / len(orders_per_customer)),
        },
        "staff_by_store": [
            {"store_id": key, "store_name": store_names[key], "staff": staff_per_store[key]}
            for key in sorted(staff_per_store)
        ],
        "date_range_example": {
            "$comment": "Golden eval #18 asks for a bounded range; this is the answer.",
            "from": "2026-03-01",
            "to": "2026-03-15",
            "completed_orders": len(march_window),
            "revenue": cash(sum((o.total_amount for o in march_window), Decimal("0"))),
        },
        "unanswerable": {
            "$comment": (
                "There is no order_items table and no join path from orders to "
                "menu_items, so these must produce an honest refusal, not a number."
            ),
            "examples": [
                "Which menu items sell best?",
                "What is the revenue per pizza type?",
            ],
        },
    }


def render_truths(truths: dict[str, object]) -> str:
    """Stable JSON: sorted keys, fixed indent, LF endings — so a diff means a real change."""
    return json.dumps(truths, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def connection_string() -> str:
    load_dotenv(REPO_ROOT / ".env")

    password = os.environ.get("SEED_PIZZA_PASSWORD")
    if not password:
        sys.exit(
            "SEED_PIZZA_PASSWORD is not set.\n"
            "Copy .env.example to .env (`make env`) and start the stack with `make up`."
        )

    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("SEED_PIZZA_HOST", "localhost"),
        port=os.environ.get("SEED_PIZZA_PORT", "6543"),
        dbname=os.environ.get("SEED_PIZZA_DB", "pizza"),
        user=os.environ.get("SEED_PIZZA_USER", "pizza"),
        password=password,
    )


def copy_rows(
    cursor: psycopg.Cursor[tuple[object, ...]],
    table: str,
    columns: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> None:
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )
    with cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--truths-only",
        action="store_true",
        help="write ops/seed/truths.json and skip the database entirely",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate the truths and fail if the committed file disagrees (used by CI)",
    )
    args = parser.parse_args(argv)

    # S311: a seeded, non-cryptographic PRNG is the requirement here, not a
    # weakness — reproducibility is the whole point of this fixture.
    rng = random.Random(RNG_SEED)  # noqa: S311

    print(f"Generating {START_DATE} .. {END_DATE} with seed {RNG_SEED}")
    customers = build_customers(rng)
    staff = build_staff(rng)
    menu = [
        (index, name, category, money(price), True)
        for index, (name, category, price) in enumerate(MENU_ITEMS, start=1)
    ]
    orders = build_orders(rng)
    payments = build_payments(rng, orders)

    decline_start = END_DATE - timedelta(days=DECLINE_WEEKS * 7 - 1)
    previous_start = decline_start - timedelta(days=DECLINE_WEEKS * 7)
    previous_end = decline_start - timedelta(days=1)

    def change(segment: str) -> float:
        return pct_change(
            revenue_between(orders, decline_start, END_DATE, segment=segment),
            revenue_between(orders, previous_start, previous_end, segment=segment),
        )

    delta = change("all")
    delta_affected = change("affected")
    delta_rest = change("rest")

    print(
        f"  stores {len(STORES):>7,}\n"
        f"  customers {len(customers):>5,}\n"
        f"  staff {len(staff):>9,}\n"
        f"  menu_items {len(menu):>4,}\n"
        f"  orders {len(orders):>8,}\n"
        f"  payments {len(payments):>6,}"
    )
    print(
        f"Revenue, last {DECLINE_WEEKS} weeks vs the {DECLINE_WEEKS} before:\n"
        f"  overall                       {delta:+.1%}\n"
        f"  store {DECLINE_STORE_ID} / {DECLINE_CHANNEL:<9}            {delta_affected:+.1%}\n"
        f"  everything else               {delta_rest:+.1%}"
    )

    # Self-checks run before anything is written, so a dataset that lost one of
    # the properties later phases rely on never reaches the database at all.
    if len(orders) <= MIN_ORDERS:
        sys.exit(f"FAIL: only {len(orders):,} orders, expected more than {MIN_ORDERS:,}")
    low, high = DECLINE_TARGET_RANGE
    if not low <= delta <= high:
        sys.exit(f"FAIL: decline {delta:+.1%} outside the expected band {low:+.0%}..{high:+.0%}")
    if abs(delta_rest) > UNAFFECTED_TOLERANCE:
        sys.exit(
            f"FAIL: the rest of the business moved {delta_rest:+.1%}, so the decline is not "
            f"concentrated in store {DECLINE_STORE_ID} / {DECLINE_CHANNEL}"
        )

    truths = render_truths(build_truths(orders, customers, staff, len(menu)))

    if args.check:
        committed = TRUTHS_FILE.read_text(encoding="utf-8") if TRUTHS_FILE.exists() else ""
        if committed != truths:
            sys.exit(
                f"FAIL: {TRUTHS_FILE.relative_to(REPO_ROOT)} is out of date.\n"
                "The fixture and the numbers the evals trust have drifted apart. "
                "Run `make truths` and commit the result."
            )
        print(f"{TRUTHS_FILE.relative_to(REPO_ROOT)} matches the generator.")
        return 0

    # newline="" keeps the LF endings render_truths produced; on Windows the
    # default would rewrite them to CRLF and every CI diff check would fail.
    with TRUTHS_FILE.open("w", encoding="utf-8", newline="") as handle:
        handle.write(truths)
    print(f"Wrote {TRUTHS_FILE.relative_to(REPO_ROOT)}")

    if args.truths_only:
        return 0

    with psycopg.connect(connection_string()) as connection, connection.cursor() as cursor:
        cursor.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        copy_rows(cursor, "stores", ("id", "name", "city", "country", "opened_on"), list(STORES))
        copy_rows(
            cursor,
            "customers",
            ("id", "full_name", "email", "phone", "city", "signed_up_on"),
            customers,
        )
        copy_rows(cursor, "staff", ("id", "store_id", "full_name", "role", "hired_on"), staff)
        copy_rows(cursor, "menu_items", ("id", "name", "category", "price", "is_active"), menu)
        copy_rows(
            cursor,
            "orders",
            ("id", "order_date", "store_id", "customer_id", "channel", "total_amount", "status"),
            [tuple(order) for order in orders],
        )
        copy_rows(cursor, "payments", ("id", "order_id", "method", "amount", "paid_at"), payments)
        connection.commit()

    print("Seed complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
