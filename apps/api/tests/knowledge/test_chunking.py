"""Chunker goldens (plan WP10.1).

No database, no model: chunking is a pure function of a string, which is what
lets these be goldens at all. They are the cheapest tests in the build and they
guard the place where retrieval quality is mostly decided — before any model is
involved.

Each test names the failure it prevents rather than the behaviour it describes,
because "chunks by heading" is true of an implementation that also cuts every
code block into pieces.
"""

from __future__ import annotations

from dataagent.knowledge.chunking import (
    MIN_TOKENS,
    TARGET_TOKENS,
    Chunk,
    chunk_document,
    split_sections,
)
from dataagent.llm.base import estimate_tokens

POLICY = """\
# Revenue policy

This document defines how revenue is counted across the group.

## Exclusions

Net revenue excludes cancelled and refunded orders.

## Timing

Revenue is recognised on the order date, not the payment date.
"""


def test_a_heading_starts_a_new_passage() -> None:
    chunks = chunk_document(POLICY)

    assert [chunk.headings for chunk in chunks] == [
        ("Revenue policy",),
        ("Revenue policy", "Exclusions"),
        ("Revenue policy", "Timing"),
    ]


def test_the_heading_trail_is_a_stack_and_not_the_nearest_heading() -> None:
    """Provenance has to say *where in the document* a passage sits. "Exclusions"
    on its own could be anything's exclusions."""
    sections = split_sections(POLICY)

    assert sections[1].headings == ("Revenue policy", "Exclusions")


def test_a_deeper_heading_pops_back_out_again() -> None:
    """Without this the trail only ever grows, and by the end of a long document
    every chunk claims to sit under every heading in it."""
    document = "# One\n\n## Deep\n\nx\n\n# Two\n\ny\n"

    sections = split_sections(document)

    assert sections[-1].headings == ("Two",)


def test_text_before_the_first_heading_is_kept() -> None:
    """Plenty of real documents open with their most important paragraph."""
    sections = split_sections("An opening claim.\n\n# Later\n\nmore\n")

    assert sections[0].headings == ()
    assert "An opening claim." in sections[0].text


def test_a_hash_inside_a_code_fence_is_not_a_heading() -> None:
    """A `#` in a shell block is a comment. Treating it as a section cuts a
    script into pieces under invented headings — and a document full of SQL is
    exactly what an analytics customer uploads."""
    document = "# Setup\n\n```sh\n# install everything\nmake up\n```\n\nAfterwards, run it.\n"

    sections = split_sections(document)

    assert len(sections) == 1
    assert "# install everything" in sections[0].text


def test_the_headings_travel_beside_the_text_rather_than_inside_it() -> None:
    """Prepending the trail would inflate every embedding with the same words and
    make provenance impossible to render separately."""
    chunks = chunk_document(POLICY)
    exclusions = next(c for c in chunks if c.headings == ("Revenue policy", "Exclusions"))

    assert exclusions.text.startswith("Net revenue excludes")
    assert "Revenue policy" not in exclusions.text


def test_a_long_section_is_split_and_never_mid_paragraph() -> None:
    """A paragraph is the smallest unit whose meaning survives being moved.
    Cutting inside one gives two chunks that are each slightly wrong rather than
    one that is right.

    Asserted as *every emitted paragraph appears verbatim in the source*, which
    is the actual property. Checking that chunks end in a full stop would pass
    for an implementation that cut after any sentence, mid-paragraph included.
    """
    paragraphs = [
        f"{n}. " + "Cancelled orders are excluded from every revenue figure. " * 12
        for n in range(12)
    ]
    document = "# Policy\n\n" + "\n\n".join(paragraphs)
    source = {paragraph.strip() for paragraph in paragraphs}

    chunks = chunk_document(document)

    assert len(chunks) > 1
    emitted = {part.strip() for chunk in chunks for part in chunk.text.split("\n\n")}
    assert emitted <= source, "a paragraph was cut in half"
    assert emitted == source, "a paragraph was lost"


def test_a_continuation_repeats_the_previous_paragraph() -> None:
    """The overlap. A claim that straddles a boundary has to be retrievable from
    either side, or the one question it answers finds half of it."""
    paragraphs = [f"Paragraph {n}. " + ("filler words here. " * 40) for n in range(10)]
    document = "# Policy\n\n" + "\n\n".join(paragraphs)

    chunks = chunk_document(document)

    assert len(chunks) > 1
    tail_of_first = chunks[0].text.split("\n\n")[-1]
    assert tail_of_first in chunks[1].text


def test_one_enormous_paragraph_is_left_whole_rather_than_mangled() -> None:
    """Splitting it needs a sentence tokenizer, and one that is wrong about
    abbreviations cuts mid-sentence. An oversized chunk is a cost; a mangled one
    is a wrong answer."""
    document = "# Policy\n\n" + ("one long unbroken sentence without any breaks " * 400)

    chunks = chunk_document(document)

    assert len(chunks) == 1
    assert chunks[0].token_estimate > TARGET_TOKENS


def test_a_short_section_stays_its_own_chunk() -> None:
    """Merging across a heading would produce a chunk whose `headings` is a lie —
    claiming one provenance while holding text from two."""
    document = "# A\n\nshort.\n\n# B\n\nalso short.\n"

    chunks = chunk_document(document)

    assert [chunk.headings for chunk in chunks] == [("A",), ("B",)]


def test_a_straggler_at_the_end_of_a_section_is_folded_back_in() -> None:
    """Within a section only, and only the tail: a two-line remainder is not
    worth a row, a vector and a retrieval slot of its own."""
    body = "\n\n".join(f"Paragraph {n}. " + ("filler " * 60) for n in range(8))
    document = "# Policy\n\n" + body + "\n\ntiny."

    chunks = chunk_document(document)

    assert all(chunk.token_estimate >= MIN_TOKENS or len(chunks) == 1 for chunk in chunks), (
        "a straggler survived on its own"
    )
    assert "tiny." in chunks[-1].text


def test_seq_is_gap_free_across_the_whole_document() -> None:
    """Revision 0016's unique constraint expects it, and it is what makes
    re-indexing a delete-and-rewrite rather than an append."""
    chunks = chunk_document(POLICY)

    assert [chunk.seq for chunk in chunks] == list(range(len(chunks)))


def test_the_same_document_chunks_the_same_way_every_time() -> None:
    """The property that lets these be goldens, and the one that makes a
    re-index produce the same rows rather than a shuffled set."""
    assert chunk_document(POLICY) == chunk_document(POLICY)


def test_an_empty_document_produces_nothing_rather_than_one_empty_chunk() -> None:
    """An empty chunk is a row, a vector and a retrieval slot holding nothing."""
    assert chunk_document("") == []
    assert chunk_document("   \n\n  \n") == []


def test_a_chunk_estimates_its_own_size_with_the_shared_estimator() -> None:
    """The same function the prompt budget uses, so "700 tokens" means one thing
    in this codebase rather than two."""
    chunk = Chunk(seq=0, text="Net revenue excludes cancelled orders.")

    assert chunk.token_estimate == estimate_tokens(chunk.text)
