"""Turning a document into passages worth retrieving (architecture 5.5).

Chunking is where retrieval quality is mostly decided, and it is decided before
any model is involved — which makes it the cheapest place in the system to be
careful and the easiest to get quietly wrong. A chunk that is too big buries its
own answer among three others; one that is too small says *"excluding cancelled
orders"* with nothing to say what it is about.

Four decisions, each of which shows up in a golden test.

**Headings are the primary boundary, length is the secondary one.** A document's
author already decided what belongs together and wrote a heading over it. Cutting
purely by length ignores that and splits mid-argument; cutting *only* by heading
leaves a 4,000-token section that is one chunk. So sections are split by heading
first, and a long section is then split by length within itself.

**The heading trail travels beside the text, not inside it.** `headings` is
`["Revenue policy", "Exclusions"]`, kept as its own column (revision 0016), so
provenance can be shown without every chunk repeating its own context — and so a
future heading-weighted rank has something to weigh. Prepending it to the text
would inflate every embedding with the same words.

**Overlap is measured in paragraphs, never in characters.** A character window
cuts mid-word and mid-number, and an embedding of half a number is worse than
useless. So a long section's chunks carry the last whole paragraph of the one
before, which is what keeps a sentence that straddles a boundary findable from
either side.

**A short section stays short.** Merging small sections across a heading would
produce a chunk whose `headings` is a lie — it would claim one provenance while
holding text from two. A three-word section is a three-word chunk, and retrieval
ranking is the right place to deal with that, not ingest.

Everything here is a pure function of its input: the same document chunks the
same way on every machine and in a year's time, which is what lets the goldens be
goldens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dataagent.llm.base import estimate_tokens

__all__ = ["Chunk", "chunk_document", "split_sections"]

#: The target size, in estimated tokens. Architecture 5.5 says 500 to 800; this is
#: the upper end of it, because a chunk is split only when it *exceeds* the
#: target, so most chunks land comfortably below.
TARGET_TOKENS = 700

#: A chunk shorter than this is merged into the next one **within the same
#: section**. Never across a heading — that would make `headings` a lie. Its job
#: is the stray one-line paragraph, not the short section.
MIN_TOKENS = 40

#: How much of the previous chunk a continuation repeats. One paragraph, so a
#: sentence that straddles a boundary is findable from either side, and no more,
#: because overlap is duplicated text in every vector and every prompt.
OVERLAP_PARAGRAPHS = 1

#: Markdown ATX headings. Setext (`===` underlines) is deliberately not handled:
#: it is rare in the documents people upload, and a half-supported syntax that
#: silently produces one giant chunk is worse than one that is not supported.
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

#: A blank line, however much whitespace it carries — which is what separates
#: paragraphs in every format this ingests.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

#: Fenced code, which must not be split by the heading scanner: a `#` inside a
#: shell block is a comment, not a section, and treating it as one would cut a
#: script into pieces under invented headings.
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True, slots=True)
class Section:
    """A run of text under one heading trail."""

    headings: tuple[str, ...]
    text: str


@dataclass(frozen=True, slots=True)
class Chunk:
    """One passage, as it will be stored and retrieved."""

    seq: int
    text: str
    headings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.text)


def split_sections(document: str) -> list[Section]:
    """Break a document at its headings, keeping the trail to each.

    The trail is a *stack*: `## Exclusions` under `# Revenue policy` yields
    `("Revenue policy", "Exclusions")`, and a later `# Appendix` pops back to
    depth one. That is what makes the heading list usable as provenance — it
    names where in the document a passage sits, not merely the nearest bold line
    above it.

    Text before the first heading is a section with an empty trail rather than
    being dropped, because plenty of real documents open with their most
    important paragraph.
    """
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(body).strip()
        body.clear()
        if text:
            sections.append(Section(headings=tuple(title for _, title in stack), text=text))

    for line in document.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            body.append(line)
            continue
        flush()
        depth, title = len(match.group(1)), match.group(2).strip()
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if title:
            stack.append((depth, title))

    flush()
    return sections


def chunk_document(document: str, *, target_tokens: int = TARGET_TOKENS) -> list[Chunk]:
    """The whole pipeline: sections, then length, then a gap-free ``seq``.

    ``seq`` is assigned last and across the whole document, so it is 0-based and
    gap-free however the sections fell — which is what revision 0016's unique
    constraint expects and what makes re-indexing a delete-and-rewrite rather
    than an append.
    """
    chunks: list[Chunk] = []
    for section in split_sections(document):
        for text in _split_by_length(section.text, target_tokens):
            chunks.append(Chunk(seq=len(chunks), text=text, headings=section.headings))
    return chunks


def _split_by_length(text: str, target_tokens: int) -> list[str]:
    """One section's text, cut on paragraph boundaries near the target size.

    Never mid-paragraph, which is the whole point: a paragraph is the smallest
    unit whose meaning survives being moved, and cutting inside one produces two
    chunks that are each slightly wrong rather than one that is right.

    A single paragraph longer than the target is **left whole**. Splitting it
    would need a sentence tokenizer, and one that is wrong about abbreviations
    cuts mid-sentence — which is the failure this function exists to avoid. An
    oversized chunk is a cost; a mangled one is a wrong answer.
    """
    if estimate_tokens(text) <= target_tokens:
        return [text]

    paragraphs = [part.strip() for part in _PARAGRAPH_BREAK.split(text) if part.strip()]
    out: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        cost = estimate_tokens(paragraph)
        if current and size + cost > target_tokens:
            out.append("\n\n".join(current))
            # The overlap: the tail of what was just emitted opens the next
            # chunk, so a claim spanning the boundary is retrievable from
            # either side.
            current = current[-OVERLAP_PARAGRAPHS:]
            size = sum(estimate_tokens(part) for part in current)
        current.append(paragraph)
        size += cost
    if current:
        out.append("\n\n".join(current))

    return _merge_stragglers(out)


def _merge_stragglers(chunks: list[str]) -> list[str]:
    """Fold a too-small trailing chunk back into its predecessor.

    Only within a section, and only the tail: a two-line remainder is not worth a
    row, a vector and a retrieval slot of its own. Anything earlier is left
    alone, because a small chunk in the middle of a section is usually a short
    paragraph that means something on its own.
    """
    if len(chunks) > 1 and estimate_tokens(chunks[-1]) < MIN_TOKENS:
        tail = chunks.pop()
        chunks[-1] = f"{chunks[-1]}\n\n{tail}"
    return chunks
