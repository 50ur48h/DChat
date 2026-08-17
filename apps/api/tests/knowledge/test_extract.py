"""Bytes to text (WP10.1a, DECISIONS D-030).

No fixtures on disk and no database: extraction is a pure function of bytes and
a declared type. The PDF cases use bytes that are *not* a valid PDF on purpose —
the interesting behaviour is what a malformed one does, because a third-party
parser on hostile input is the only part of this package that can raise
something nobody anticipated.
"""

from __future__ import annotations

import pytest

from dataagent.knowledge.extract import SUPPORTED_MIME, ExtractionError, extract_text

POLICY = "# Revenue policy\n\nCancelled orders are excluded from revenue.\n"


def test_markdown_and_plain_text_come_back_whole() -> None:
    for mime in ("text/markdown", "text/plain"):
        assert extract_text(POLICY.encode(), mime=mime) == POLICY


def test_a_byte_order_mark_is_not_left_in_the_text() -> None:
    """A BOM at the head of a chunk becomes a stray character in an embedding and
    in anything rendered from it."""
    text = extract_text(POLICY.encode("utf-8-sig"), mime="text/plain")

    assert not text.startswith("﻿")
    assert text.startswith("# Revenue policy")


def test_undecodable_bytes_mangle_rather_than_lose_the_document() -> None:
    """Mangled text is visible to whoever reads the chunk; raising on one odd
    byte loses everything around it."""
    payload = POLICY.encode() + b"\xff\xfe invalid tail"

    text = extract_text(payload, mime="text/plain")

    assert "Cancelled orders are excluded" in text


def test_the_extension_is_trusted_when_the_browser_guesses_octet_stream() -> None:
    """Browsers send `application/octet-stream` for a `.md` more often than not,
    and refusing on the declared type alone rejects files that are fine."""
    text = extract_text(POLICY.encode(), mime="application/octet-stream", filename="policy.md")

    assert "Revenue policy" in text


def test_an_unsupported_type_says_what_is_supported() -> None:
    with pytest.raises(ExtractionError) as caught:
        extract_text(b"PK\x03\x04 zip bytes", mime="application/zip", filename="policy.zip")

    message = str(caught.value)
    assert "Markdown" in message and "PDF" in message


def test_an_empty_file_is_a_failure_naming_ocr_rather_than_a_silent_success() -> None:
    """**The failure this function exists for.** A scanned PDF has no text layer,
    so extraction "succeeds" with an empty string — and a document with zero
    chunks looks exactly like a successful upload of nothing."""
    with pytest.raises(ExtractionError) as caught:
        extract_text(b"   \n\n\t ", mime="text/plain")

    assert "OCR" in str(caught.value)


def test_a_file_too_short_to_be_useful_is_refused_too() -> None:
    """Not zero-length: a PDF's text layer often yields a few characters of page
    furniture even when there is nothing readable on the page."""
    with pytest.raises(ExtractionError):
        extract_text(b"hello", mime="text/plain")


def test_a_malformed_pdf_raises_our_error_and_not_the_parser_s() -> None:
    """pypdf raises a wide and undocumented range on bad input, and a traceback
    from a parser is not something to show a person who uploaded a document.
    The original stays in `__cause__` for whoever is debugging."""
    with pytest.raises(ExtractionError) as caught:
        extract_text(b"%PDF-1.7\nnot really a pdf at all", mime="application/pdf")

    assert "PDF" in str(caught.value)
    assert caught.value.__cause__ is not None, "the original was not kept for debugging"


def test_the_supported_types_are_the_three_the_decision_names() -> None:
    assert set(SUPPORTED_MIME) == {"text/markdown", "text/plain", "application/pdf"}
