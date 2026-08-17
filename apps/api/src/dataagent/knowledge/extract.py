"""Bytes in, text out (architecture 5.5, plan WP10.1).

Three formats for V1 — markdown, plain text, and a PDF's text layer — and the
seam is one function so a fourth is a branch rather than a redesign.

**A PDF's *text layer*, and the distinction is not pedantic.** A scanned
document is a series of images and has no text; extracting it yields an empty
string, which would sail through ingest and produce a document with zero chunks
that looks exactly like a successful upload of nothing. So an extraction that
finds nothing says so, loudly, and the document is marked `failed` with a reason
naming OCR — because "your file produced no text" is a sentence a person can act
on and a silent empty document is not.

**Decoding is strict about what it will guess.** UTF-8, then UTF-8 with a BOM,
then Latin-1 as a last resort — which cannot fail and will mangle rather than
raise. Mangled text is the right failure here: it is visible to whoever reads
the chunk, whereas a raised error on one odd byte loses a whole document.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SUPPORTED_MIME", "ExtractionError", "extract_text"]


class ExtractionError(Exception):
    """A document's text could not be read out of its bytes.

    Carries a message meant for the person who uploaded it, because that is
    where it ends up: `knowledge_documents.failure_reason`, rendered on the
    documents page.
    """


@dataclass(frozen=True, slots=True)
class Format:
    """One accepted upload, and the extensions people actually use for it."""

    mime: str
    extensions: tuple[str, ...]


MARKDOWN = Format("text/markdown", (".md", ".markdown"))
PLAIN = Format("text/plain", (".txt", ".text"))
PDF = Format("application/pdf", (".pdf",))

SUPPORTED_MIME: tuple[str, ...] = (MARKDOWN.mime, PLAIN.mime, PDF.mime)

#: Below this, a "successful" extraction has almost certainly found nothing —
#: a scanned PDF, an empty file, a format we misread. Not zero, because a PDF's
#: text layer often yields a few stray characters of page furniture even when
#: there is no readable content.
MIN_USEFUL_CHARACTERS = 20


def extract_text(payload: bytes, *, mime: str, filename: str | None = None) -> str:
    """The document's text, or ``ExtractionError`` saying why there is none."""
    resolved = _resolve(mime, filename)
    text = _from_pdf(payload) if resolved is PDF else _decode(payload)

    if len(text.strip()) < MIN_USEFUL_CHARACTERS:
        raise ExtractionError(
            "No readable text was found in this file. If it is a scanned PDF, "
            "the pages are images and need OCR before they can be indexed."
        )
    return text


def _resolve(mime: str, filename: str | None) -> Format:
    """Trust the extension over the browser's guess, and refuse the unknown.

    Browsers send `application/octet-stream` for a `.md` more often than not, so
    a check on the declared type alone rejects files that are perfectly fine.
    The extension is the better signal and neither is authoritative — what
    actually protects anything is that this only ever produces *text*, and that
    the text is framed as reference material wherever it is used (7.4).
    """
    for candidate in (MARKDOWN, PLAIN, PDF):
        if mime == candidate.mime:
            return candidate
    lowered = (filename or "").lower()
    for candidate in (MARKDOWN, PLAIN, PDF):
        if any(lowered.endswith(extension) for extension in candidate.extensions):
            return candidate
    raise ExtractionError(
        f"{mime or 'that file type'} cannot be indexed yet. "
        "Markdown, plain text and PDFs with a text layer can be."
    )


def _decode(payload: bytes) -> str:
    # `utf-8-sig` **first**, and the order is the whole point. Plain `utf-8`
    # decodes BOM-prefixed bytes quite happily — the BOM simply becomes U+FEFF —
    # so trying it first means the fallback never runs and every such file gets
    # an invisible character at the head of its first chunk, in the text, in the
    # embedding, and in anything rendered from either. `utf-8-sig` strips a BOM
    # when there is one and is identical to `utf-8` when there is not.
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Cannot fail, and will mangle rather than raise. That is the right failure:
    # mangled text is visible to whoever reads the chunk, while raising on one
    # odd byte loses the whole document.
    return payload.decode("latin-1", errors="replace")


def _from_pdf(payload: bytes) -> str:
    """The text layer, page by page, with pages separated by a blank line.

    A blank line rather than a form feed, because the chunker splits on blank
    lines: a page boundary is a paragraph boundary as far as retrieval is
    concerned, and encoding it any other way would let one chunk span two pages
    with nothing marking the seam.
    """
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(payload))
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as error:
        raise ExtractionError(f"This PDF could not be read: {error}") from error
    except Exception as error:
        # pypdf raises a wide and undocumented range on malformed files, and a
        # traceback from a parser is not something to show a person who uploaded
        # a document. The message is ours; the original stays in `__cause__`.
        raise ExtractionError("This PDF could not be read.") from error

    return "\n\n".join(page.strip() for page in pages if page.strip())
