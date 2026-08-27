"""Every event the database will store has a word a person can read.

**This is the check that was missing.** `knowledge_consulted` was added to
`EVENT_TYPES` by revision 0019 and never given a sentence in the trace, so for
three weeks it rendered to real users as the raw string `knowledge_consulted` —
a machine name reaching a person, which `trace.tsx` opens by saying it must not.
Nothing was broken, nothing failed, and no test had an opinion.

It is checked from here, in Python, reading the TypeScript, for one reason: the
list that must not grow silently lives in `db/models.py`, and a web test could
only assert against a copy of it. Ugly in exactly the way `TENANT_TABLES` and
`OUTCOME_STATES` are ugly, and for the same reason — a list kept in two languages
needs something that counts.
"""

from __future__ import annotations

import re
from pathlib import Path

from dataagent.db.models import EVENT_TYPES

TRACE = Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "screens" / "trace.tsx"

#: The record's entries sit at exactly one indent level, and the record itself
#: ends at a `};` in the first column. Anchoring on both is what stops a nested
#: object *inside* a builder — `said`, in `critic_verdict` — being read as an
#: event type and failing the second assertion for no reason.
END_OF_RECORD = "\n};"
ENTRY = re.compile(r"^  ([a-z_]+):", re.M)


def step_words() -> set[str]:
    """The keys of `STEP_SENTENCES`, read out of the component that renders them.

    **The name changed and the assertion did not.** WP13.16 replaced the flat
    `STEP_WORDS` labels with `STEP_SENTENCES`, a record of builders that turn an
    event's payload into a sentence — so the values are functions now, and the
    keys are the one thing this file was ever about.
    """
    source = TRACE.read_text(encoding="utf-8")
    block = source.split("const STEP_SENTENCES", 1)[1].split(END_OF_RECORD, 1)[0]
    return set(ENTRY.findall(block))


def test_every_event_type_has_a_word_for_a_person() -> None:
    """A type the database accepts but the screen cannot name is a leak.

    The trace falls back to the raw type rather than hiding an event it does not
    recognise, which is the right call — a trace that silently omits a step is
    worse than an ugly one. But the fallback is for an event from a *newer*
    server than the browser, not for one this repository added and forgot.
    """
    assert TRACE.exists(), f"{TRACE} not found — did the component move?"

    missing = sorted(set(EVENT_TYPES) - step_words())

    assert missing == [], (
        "these event types will render as raw machine names: "
        f"{missing}. Add a sentence to STEP_SENTENCES in {TRACE.name}."
    )


def test_the_vocabulary_does_not_name_events_that_cannot_happen() -> None:
    """The other direction, which is a smaller problem and still a wrong record.

    A word for an event the database would reject is dead code that reads as
    documentation of a feature — someone will believe it exists.
    """
    unknown = sorted(step_words() - set(EVENT_TYPES))

    assert unknown == [], (
        f"STEP_SENTENCES names events the schema does not allow: {unknown}. "
        "Either the CHECK constraint lost a type or the vocabulary invented one."
    )
