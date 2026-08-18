"""Asking the organization's own documents what a term means (arch 4.6, 5.5).

**The division of labour is the whole point, and it is stated to the model
rather than hoped for.** Architecture 5.5 puts it in one line: *RAG answers "what
does this term mean here", the database answers "what is the value"*. So this
tool's description says so, and every result repeats it — because a model that
reads "net revenue excludes cancelled orders" in a document and then reports a
number from that document has answered a data question from prose, which is the
one failure this tool makes possible that did not exist before.

**Retrieved text is framed as reference material, in the envelope itself.** Plan
WP10.1 asks for architecture 7.4's wording in the tool result, and that is here
rather than only in the layered prompt for a specific reason: a tool result
arrives *after* the system turn, closer to the model's attention than anything
L4 said, and a customer's document is exactly the untrusted text 7.4's threat
model is about. A document that says "ignore your instructions" arrives wrapped
in a sentence saying it is a record.

**The framing is the cheap half and it says so.** The expensive half is that
nothing retrieved here can widen what the run may do: no tool is granted, no
budget raised, no SQL exempted from the DAL. A fully hijacked model still
commands only read-only, org-scoped, catalog-verified, budgeted tools (7.4). If
that were not already true, no amount of wrapping would make this safe.

**Provenance travels with every passage.** A passage that cannot say which
document and which heading it came from is text of unknown origin arriving inside
a prompt — and 5.5's claim that retrieved material is safe to *show* rests
entirely on being able to name where it came from.

**The search is hybrid from here, and it says when it was not** (**B-073**).
The embedder comes off `ToolContext`, so the query embedding is charged to this
run and checked against its ceiling like any other spend. When there is no
embedder, or the ceiling refuses one, the lexical arm still answers and the
result *says* only the wording was searched — because "nothing is written down
about that" and "the half of the search that would have found it did not run"
are different answers, and only one of them should stop a model looking.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.context import KnowledgeFrame
from dataagent.agent.tools.base import Tool, ToolContext
from dataagent.knowledge.retrieve import DEFAULT_LIMIT, search_knowledge

__all__ = ["SEARCH_KNOWLEDGE", "KnowledgeFrame"]

#: Architecture 7.4's wording, in the envelope — **defined in `agent/context.py`**
#: beside `REFERENCE_FRAME` and `HISTORY_FRAME` since B-075, because those three
#: places frame untrusted text for the same model and a reader comparing them is
#: checking a safety property. Re-exported here under the name its callers
#: already use, and because a tool's envelope should be readable from the tool.

#: How many passages a call may ask for. Bounded because these go into a prompt
#: and 4.4's budget is spent on every iteration of every run.
MAX_LIMIT = 10


class SearchKnowledgeIn(BaseModel):
    """``extra="forbid"`` and no optional-without-default fields, so a provider
    that constrains decoding natively enforces this rather than suggesting it
    (B-033)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "The term or policy you want defined, in ordinary words — "
            "'how do we count net revenue', 'what counts as an active customer'."
        ),
    )
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


#: What the model is told when the corpus genuinely has nothing. Deliberately
#: firm: the failure this tool makes possible is a model inventing a definition
#: from thin air and reporting it as the organization's own.
NOTHING_FOUND = (
    "No document in this organization mentions that. Nothing has been written "
    "down about it, so do not infer a definition — say what you could not find."
)


class PassageOut(BaseModel):
    """One extract, and enough to attribute it."""

    text: str
    #: How this passage names itself in an answer: document, heading trail,
    #: position. An answer that leans on a document should be able to say which.
    source: str
    document: str
    headings: list[str]
    #: Which arm of the hybrid search found it: `vector`, `lexical`, or `both`.
    #: In the model's envelope as well as the trace because a passage both arms
    #: agreed on is the strongest signal this retrieval can offer.
    found_by: str = "both"


class SearchKnowledgeOut(BaseModel):
    #: First field on purpose. It is the first thing the model reads in the
    #: envelope, before any retrieved text.
    framing: str = KnowledgeFrame
    passages: list[PassageOut]
    note: str = ""


async def _search_knowledge(context: ToolContext, params: BaseModel) -> BaseModel:
    # The same narrowing the catalog tools use: validated at the registry gate
    # already, re-validated here so a direct caller cannot skip it.
    args = (
        params
        if isinstance(params, SearchKnowledgeIn)
        else SearchKnowledgeIn.model_validate(params)
    )

    # Hybrid when the run has an embedder, and charged to the run either way
    # (**B-073**): `run_id` is what puts the query embedding under D-019's
    # ceiling, and `actor_user_id` is what puts it on the right person's line of
    # the ledger.
    found = await search_knowledge(
        org_id=context.org_id,
        query=args.query,
        limit=args.limit,
        embedder=context.embedder,
        run_id=context.run_id,
        actor_user_id=context.actor_user_id,
        settings=context.settings,
    )

    if not found.passages:
        # A degraded search that found nothing says *both* things, in that
        # order. "Nothing is written down" is an invitation to stop looking, and
        # it is only honest when the whole search actually ran.
        note = NOTHING_FOUND if found.degraded is None else f"{found.degraded} {NOTHING_FOUND}"
        return SearchKnowledgeOut(passages=[], note=note)

    return SearchKnowledgeOut(
        passages=[
            PassageOut(
                text=passage.text,
                source=passage.citation,
                document=passage.document_title,
                headings=list(passage.headings),
                found_by=passage.found_by,
            )
            for passage in found.passages
        ],
        note=found.degraded or "",
    )


SEARCH_KNOWLEDGE = Tool(
    name="search_knowledge",
    description=(
        "Search this organization's uploaded documents for what a term means "
        "here — a metric definition, a policy, an exclusion. Use it when a "
        "question depends on how the business defines something, then query the "
        "database for the values. It returns written guidance, never data."
    ),
    params=SearchKnowledgeIn,
    handler=_search_knowledge,
)
