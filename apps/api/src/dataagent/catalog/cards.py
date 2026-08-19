"""The description of a table that an agent is given instead of the schema.

Architecture Part 5.2 is blunt about why this exists: **the full catalog never
enters a prompt**. A two-thousand-table database is workable only if the agent
retrieves a handful of relevant table cards and asks for more on demand, so a
card has to carry, in a few hundred words, everything that decides whether this
table is the right one — and nothing that would be a liability in a prompt.

Three rules follow from that, and they are the whole design.

**Written for a reader, not serialised for a parser.** Prose, because it is
consumed by a language model and indexed by a text search. `websearch_to_tsquery`
on "revenue" has to find the table whose description mentions revenue, which is
a property of sentences and not of JSON.

**Only what is already in the catalog.** A card is built from rows this
application already holds — names, types, keys, relationships, and the *masked*
profile. It never opens a connector, which means it can never reach past a
masking decision to fetch a nicer example. The values it shows were masked on
the way in (D-013), so the card is safe by construction rather than by care.

**Honest about what it does not know.** A table that has not been profiled says
so rather than implying its columns are uninteresting, and a masked column is
described as masked rather than silently omitted — an agent that cannot see a
column's values still needs to know the column is there.

The third rule is the one **B-092** was filed for, and it is stricter than it
sounds. A card must not present a **guess as a survey**. The profile is the first
`max_rows` rows of the table, not a random sample, and it counts how often each
value appeared — and until B-092 the card threw the counts away and printed
`examples: DO, PI, UC, CN, GR`. A code on 0.01% of rows then read exactly like
one on 78%, which is how a live run answered a purchasing question from a filter
matching **7 rows in 51,356** with nothing in the prompt to suggest that was odd
(**B-060**). So values carry their share, and the line says the list came from
the head of the table rather than from a survey of it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from dataagent.catalog.policies import effective_policy
from dataagent.config import Settings
from dataagent.db.models import (
    CatalogColumn,
    CatalogRelationship,
    CatalogSnapshot,
    CatalogTable,
    ColumnPolicy,
)
from dataagent.knowledge.embeddings import Embedder, embed_texts
from dataagent.llm.base import LLMError
from dataagent.tenancy.session import org_session

__all__ = [
    "MEASURES_HEADING",
    "CardColumn",
    "CardInput",
    "ValueCount",
    "build_card",
    "embed_cards",
    "offers_measures",
    "refresh_cards",
]

logger = logging.getLogger(__name__)

SNAPSHOT_ACTIVE = "active"

#: Values shown per column. A card is read in full by something that charges by
#: the token, so this is deliberately small.
MAX_EXAMPLES = 5

#: Columns listed one per line before the card summarises the rest. A hundred-
#: column table would otherwise produce a card nobody can use.
MAX_LISTED_COLUMNS = 40

#: How a card announces that this table has something to add up. Written by
#: `build_card` and read back by `offers_measures`, and the two are next to each
#: other on purpose: a reader elsewhere in the codebase matching this string
#: would be parsing prose, while a function here is reading its own output.
MEASURES_HEADING = "Numbers to aggregate:"


@dataclass(frozen=True, slots=True)
class ValueCount:
    """One value the profile saw, and how many of the sampled rows held it.

    The count travels with the value all the way to the card (**B-092**). It was
    measured, stored and then dropped one line before it would have been useful,
    and what it buys is the difference between *these are the values* and *this
    one is almost all of them*.
    """

    value: str
    count: int


@dataclass(frozen=True, slots=True)
class CardColumn:
    name: str
    data_type: str
    nullable: bool
    is_pk: bool
    description: str | None = None
    semantic_role: str | None = None
    sensitivity: str = "none"
    policy: str = "allow"
    null_frac: float | None = None
    distinct_est: int | None = None
    min_val: str | None = None
    max_val: str | None = None
    #: Already masked where it had to be — see the module docstring.
    top_values: Sequence[ValueCount] = ()
    #: How many rows the profile actually read, so a share can be stated as a
    #: share *of something* and the reader can see how thin the evidence is.
    sample_rows: int | None = None


@dataclass(frozen=True, slots=True)
class CardInput:
    schema_name: str
    table_name: str
    kind: str
    description: str | None
    row_estimate: int | None
    profiled: bool
    columns: Sequence[CardColumn]
    #: (from_columns, to_table, to_columns) for keys leaving this table.
    outgoing: Sequence[tuple[tuple[str, ...], str, tuple[str, ...]]] = ()
    #: (from_table, from_columns, to_columns) for keys arriving at it.
    incoming: Sequence[tuple[str, tuple[str, ...], tuple[str, ...]]] = ()


def offers_measures(card_text: str) -> bool:
    """Whether this card describes a table with figures to aggregate.

    The cheapest honest test for *"could this table have answered the question"*
    (**B-093**): a table with no measure is a dimension, and an answer that did
    not read it was not choosing between sources. Reads the sentence
    `build_card` writes a few lines above, so the question and the answer stay
    in one file and a test can hold them together.
    """
    return MEASURES_HEADING in card_text


def _qualified(schema: str, table: str) -> str:
    return f"{schema}.{table}"


def _column_line(column: CardColumn) -> str:
    """One column, in the order a person reads: what it is, then what it holds."""
    parts = [f"- {column.name} ({column.data_type}"]
    if column.is_pk:
        parts.append(", primary key")
    if not column.nullable:
        parts.append(", required")
    parts.append(")")
    line = "".join(parts)

    if column.semantic_role and column.semantic_role != "other":
        line += f" [{column.semantic_role}]"
    if column.description:
        line += f" — {column.description}"

    facts: list[str] = []
    if column.null_frac is not None and column.null_frac > 0.01:
        facts.append(f"{column.null_frac:.0%} empty")
    if column.distinct_est is not None and column.distinct_est > 0:
        facts.append(_distinct_fact(column))
    if column.min_val is not None and column.max_val is not None:
        facts.append(f"range {column.min_val} to {column.max_val}")
    if column.top_values:
        facts.append(_values_fact(column))

    # Said plainly, because an agent that does not know a column is masked will
    # write a query whose results are useless and not understand why.
    if column.policy == "deny":
        facts.append("values are not available to queries")
    elif column.policy == "mask":
        facts.append("values are masked")

    if facts:
        line += f" ({'; '.join(facts)})"
    return line


def _count_of(raw: object) -> int:
    """A stored count as an integer, and 0 for anything that is not one.

    The column is JSONB, so its shape is a convention rather than a type. A row
    written before the profile carried counts reads as 0 and simply has no share
    to state — better than a card that cannot be built.
    """
    return raw if isinstance(raw, int) else 0


def _share(count: int, total: int) -> str:
    """A count as a share of the rows read, in the fewest characters that stay true.

    Whole percents above 1, one decimal below it, and `<0.1%` under that — a code
    on six rows in five thousand should not round to `0%` and read as absent.
    """
    percent = 100.0 * count / total
    if percent >= 1:
        return f"{percent:.0f}%"
    if percent >= 0.1:
        return f"{percent:.1f}%"
    return "<0.1%"


def _distinct_fact(column: CardColumn) -> str:
    """How many values the profile saw, and how far it looked.

    Naming the sample size is the honest half of `distinct in sample`: five
    distinct values found in five thousand rows of a fifty-thousand-row table is
    a much weaker claim than five found in five thousand rows of five thousand,
    and the card used to state both the same way.
    """
    if column.sample_rows:
        return f"{column.distinct_est} distinct in the first {column.sample_rows:,} rows"
    return f"{column.distinct_est} distinct in sample"


def _values_fact(column: CardColumn) -> str:
    """The commonest values, each with its share (**B-092**).

    **Ordered and quantified, and said to be neither exhaustive nor random.**
    The profile reads the head of the table rather than a random sample — 5.2's
    deliberate choice, since ordering would sort somebody's warehouse — so a
    value that lives at the far end can be missing entirely. A model choosing a
    filter from this list is entitled to know both things: which value is
    typical, and that the list may not be the whole vocabulary.
    """
    shown = column.top_values[:MAX_EXAMPLES]
    total = column.sample_rows or sum(item.count for item in shown)
    if not total:
        return "examples: " + ", ".join(item.value for item in shown)
    described = ", ".join(f"{item.value} {_share(item.count, total)}" for item in shown)
    more = "" if len(column.top_values) <= MAX_EXAMPLES else ", and rarer ones"
    where = f"the table's first {total:,}" if column.sample_rows else "the rows read"
    return f"commonest in the rows read ({where}): {described}{more}"


def _relationship_lines(card: CardInput) -> list[str]:
    lines: list[str] = []
    for columns, to_table, to_columns in card.outgoing:
        lines.append(f"- {', '.join(columns)} references {to_table}({', '.join(to_columns)})")
    for from_table, from_columns, to_columns in card.incoming:
        lines.append(
            f"- {from_table}({', '.join(from_columns)}) references this table"
            f"({', '.join(to_columns)})"
        )
    return lines


def build_card(card: CardInput) -> str:
    """The card, as text. Deterministic: the same catalog always renders the same
    words, so a re-profile that changed nothing changes no card either.

    **The bare table name comes first, and that is load-bearing** (B-039).
    PostgreSQL's English parser reads ``public.shops`` as a single *host* token,
    so a card that named its table only in qualified form could not be found by
    searching for ``shops`` — ``to_tsvector('english','public.shops')`` is
    ``'public.shops'`` while ``websearch_to_tsquery('english','shops')`` is
    ``'shop'``, and the two never meet. Six of the thirteen cards in the demo
    catalogs were unfindable by their own name; the ones that worked did so only
    because the word happened to appear again in their prose.

    The bare form needs no help beyond being bare: ``menu_items`` on its own
    tokenises to ``'menu'`` and ``'item'``, because the parser splits on the
    underscore once there is no host to mistake it for. Putting it first also
    ranks it higher — ``ts_rank_cd`` weighs proximity, and the name is the most
    on-topic thing a card says about itself.
    """
    name = _qualified(card.schema_name, card.table_name)
    opening = f"{card.table_name} ({name}) is a {card.kind}"
    if card.row_estimate is not None:
        opening += f" with about {card.row_estimate:,} rows"
    opening += "."

    lines = [opening]
    if card.description:
        lines.append(card.description)

    measures = [c.name for c in card.columns if c.semantic_role == "measure"]
    times = [c.name for c in card.columns if c.semantic_role == "time"]
    dimensions = [c.name for c in card.columns if c.semantic_role == "dimension"]
    summary: list[str] = []
    if measures:
        summary.append(f"{MEASURES_HEADING} {', '.join(measures)}.")
    if times:
        summary.append(f"Dates to filter or group by: {', '.join(times)}.")
    if dimensions:
        summary.append(f"Categories to group by: {', '.join(dimensions)}.")
    if summary:
        lines.append(" ".join(summary))

    sensitive = [c.name for c in card.columns if c.sensitivity != "none"]
    if sensitive:
        lines.append(
            f"Personal or otherwise sensitive: {', '.join(sensitive)}. "
            "Their values are masked unless an administrator has allowed them."
        )

    lines.append("")
    lines.append(f"Columns ({len(card.columns)}):")
    lines.extend(_column_line(column) for column in card.columns[:MAX_LISTED_COLUMNS])
    if len(card.columns) > MAX_LISTED_COLUMNS:
        remaining = len(card.columns) - MAX_LISTED_COLUMNS
        lines.append(f"- …and {remaining} more column(s); ask for this table's detail to see them.")

    relationships = _relationship_lines(card)
    if relationships:
        lines.append("")
        lines.append("Related tables:")
        lines.extend(relationships)
    else:
        # The absence of a join is a fact an agent must be able to read, not an
        # empty section it can fill in with a guess. Phase 8's honest refusal
        # depends on exactly this sentence being here.
        lines.append("")
        lines.append(
            "No foreign keys connect this table to any other, so it cannot be "
            "joined to them on declared keys."
        )

    if not card.profiled:
        lines.append("")
        lines.append(
            "This table has not been profiled, so nothing here describes its "
            "values — only its structure."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Writing cards for a whole snapshot
#
# Kept in this module rather than in discovery or the profiler because both of
# them call it and neither owns it: a card is out of date the moment either the
# structure or the profile moves, and having one writer means it cannot be
# refreshed by one path and forgotten by the other.
# ---------------------------------------------------------------------------


async def refresh_cards(org_id: uuid.UUID, data_source_id: uuid.UUID) -> int:
    """Rebuild every card in the active snapshot. Returns how many were written.

    Reads only the platform database — no connector, no customer database — so
    this cannot reach past a masking decision to fetch a better example.
    """
    async with org_session(org_id) as session:
        snapshot = (
            (
                await session.execute(
                    select(CatalogSnapshot).where(
                        CatalogSnapshot.data_source_id == data_source_id,
                        CatalogSnapshot.status == SNAPSHOT_ACTIVE,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if snapshot is None:
            return 0

        tables = (
            (
                await session.execute(
                    select(CatalogTable).where(CatalogTable.snapshot_id == snapshot.id)
                )
            )
            .scalars()
            .all()
        )
        columns = (
            (
                await session.execute(
                    select(CatalogColumn)
                    .join(CatalogTable, CatalogTable.id == CatalogColumn.table_id)
                    .where(CatalogTable.snapshot_id == snapshot.id)
                    .order_by(CatalogColumn.ordinal)
                )
            )
            .scalars()
            .all()
        )
        edges = (
            (
                await session.execute(
                    select(CatalogRelationship).where(
                        CatalogRelationship.snapshot_id == snapshot.id
                    )
                )
            )
            .scalars()
            .all()
        )
        decisions = {
            (policy.schema_name, policy.table_name, policy.column_name): policy.policy
            for policy in (
                (
                    await session.execute(
                        select(ColumnPolicy).where(ColumnPolicy.data_source_id == data_source_id)
                    )
                )
                .scalars()
                .all()
            )
        }

        by_table: dict[uuid.UUID, list[CatalogColumn]] = {}
        for column in columns:
            by_table.setdefault(column.table_id, []).append(column)

        written = 0
        profiled = snapshot.profile_status != "none"
        for table in tables:
            card = CardInput(
                schema_name=table.schema_name,
                table_name=table.table_name,
                kind=table.kind,
                description=table.description,
                row_estimate=table.row_estimate,
                profiled=profiled,
                columns=[
                    CardColumn(
                        name=column.name,
                        data_type=column.data_type,
                        nullable=column.nullable,
                        is_pk=column.is_pk,
                        description=column.description,
                        semantic_role=column.semantic_role,
                        sensitivity=column.sensitivity,
                        policy=effective_policy(
                            stored=decisions.get(
                                (table.schema_name, table.table_name, column.name)
                            ),
                            sensitivity=column.sensitivity,
                        ),
                        null_frac=column.null_frac,
                        distinct_est=column.distinct_est,
                        min_val=column.min_val,
                        max_val=column.max_val,
                        # The counts come too, because dropping them here is
                        # exactly the defect B-092 records: they were measured,
                        # stored, and thrown away one line before use.
                        top_values=[
                            ValueCount(
                                value=str(entry.get("value", "")),
                                count=_count_of(entry.get("count")),
                            )
                            for entry in (column.top_values or [])
                        ],
                        sample_rows=column.sample_rows,
                    )
                    for column in by_table.get(table.id, [])
                ],
                outgoing=[
                    (tuple(edge.from_columns), edge.to_table, tuple(edge.to_columns))
                    for edge in edges
                    if edge.from_schema == table.schema_name and edge.from_table == table.table_name
                ],
                incoming=[
                    (edge.from_table, tuple(edge.from_columns), tuple(edge.to_columns))
                    for edge in edges
                    if edge.to_schema == table.schema_name and edge.to_table == table.table_name
                ],
            )
            text = build_card(card)
            if text != table.card_text:
                # Re-queued only when the words actually changed. A refresh that
                # finds nothing different must not invalidate a vector it would
                # then pay to compute again — D-012's "a refresh that finds no
                # change writes nothing" applied to the half that costs money.
                table.card_text = text
                table.embedding = None
                table.flags = {**table.flags, "embedding": "queued"}
            written += 1

        return written


async def embed_cards(
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    *,
    embedder: Embedder | None,
    settings: Settings | None = None,
) -> int:
    """Give every card in the active snapshot a vector. Returns how many it wrote.

    **Idempotent, and that is what makes it a backfill** (**B-018**). It visits
    exactly the cards with text and no vector — revision 0018's partial index is
    that query — so running it twice costs one provider call the first time and
    none the second. A catalog refresh calls it; so can anything else, which is
    how a deployment that configured a key after its first crawl catches up.

    **No embedder is not a failure.** The cards keep their text, stay lexically
    searchable, and stay `queued`; the deployment simply has no way to embed them
    yet. Raising here would make an optional capability a hard dependency of
    catalog refresh, which is the one thing a new organization cannot skip.

    **A provider failure is not a failure either**, for the same reason ingest
    treats one that way: the cards were already written by `refresh_cards`, and
    losing them to a rate limit would trade working search for tidy bookkeeping.
    The rows stay `queued` and the next call finishes the job.
    """
    if embedder is None:
        return 0

    async with org_session(org_id) as session:
        pending = (
            (
                await session.execute(
                    select(CatalogTable)
                    .join(CatalogSnapshot, CatalogSnapshot.id == CatalogTable.snapshot_id)
                    .where(
                        CatalogSnapshot.data_source_id == data_source_id,
                        CatalogSnapshot.status == SNAPSHOT_ACTIVE,
                        CatalogTable.card_text.is_not(None),
                        CatalogTable.embedding.is_(None),
                    )
                    .order_by(CatalogTable.schema_name, CatalogTable.table_name)
                )
            )
            .scalars()
            .all()
        )
        if not pending:
            return 0

        try:
            vectors = await embed_texts(
                org_id=org_id,
                texts=[table.card_text or "" for table in pending],
                embedder=embedder,
                settings=settings,
            )
        except LLMError:
            # Logged rather than raised: the caller is a catalog refresh, and a
            # refresh that fails because a provider was busy would leave an
            # organization with no catalog at all rather than one whose search
            # is temporarily lexical.
            logger.warning(
                "could not embed %d card(s) for data source %s; they stay lexically "
                "searchable and queued",
                len(pending),
                data_source_id,
            )
            return 0

        # Paired by position, which is safe only because `embed_texts` guarantees
        # one vector per input in input order — asserted there, not assumed here.
        for table, vector in zip(pending, vectors, strict=True):
            table.embedding = list(vector)
            table.flags = {**table.flags, "embedding": "done"}
        return len(pending)
