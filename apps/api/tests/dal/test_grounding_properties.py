"""Generated identifiers never bypass grounding (plan §6 WP5.3).

The corpus holds the attacks somebody thought of. These hold the ones nobody
did: hypothesis generates identifier spellings — case mixtures, quoting, padding,
lookalike characters — and asserts the same two properties over all of them.

**Property 1.** A denied column stays denied however it is spelled, as long as
the spelling is one the engine would still resolve to that column.

**Property 2.** Anything the catalog does not contain is refused, and refused by
*name* — never quietly matched to something that looks similar.

Both are one-directional on purpose: the assertion is never "this exact code" for
generated input, because a generated string can break more than one rule at once
and which one fires first is the pipeline's business. The assertion is that the
statement does not run.
"""

from __future__ import annotations

from catalog_fixture import build_source
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dataagent.dal.errors import PolicyViolation, ViolationCode
from dataagent.dal.policy import SourcePolicy
from dataagent.dal.validator import ColumnRef, validate

# Hypothesis runs each example against a fresh source; building the catalog is
# cheap but not free, and the deadline is about the validator rather than about
# the fixture.
PROFILE = settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


def _cased(word: str) -> st.SearchStrategy[str]:
    """Every case mixture of a word: `tax_id`, `TAX_ID`, `tAx_Id`, …"""
    return st.tuples(
        *[st.sampled_from([character.lower(), character.upper()]) for character in word]
    ).map("".join)


#: Characters that read as ASCII letters and are not. Each one makes an
#: identifier that is *different*, and must be treated as different.
# ruff's RUF001 is right that these look like ASCII and are not. That is the
# subject of the test, so it is suppressed here and nowhere else — a homoglyph
# anywhere but in this table is exactly the accident the rule exists to catch.
HOMOGLYPHS = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "i": "і"}  # noqa: RUF001


def _homoglyphed(word: str) -> st.SearchStrategy[str]:
    def swap(index: int) -> str:
        character = word[index]
        return word[:index] + HOMOGLYPHS.get(character, character) + word[index + 1 :]

    swappable = [index for index, character in enumerate(word) if character in HOMOGLYPHS]
    if not swappable:  # pragma: no cover - every word here has one
        return st.just(word)
    return st.sampled_from(swappable).map(swap)


@given(spelling=_cased("tax_id"))
@PROFILE
def test_a_denied_column_stays_denied_however_it_is_cased(spelling: str) -> None:
    """Unquoted identifiers fold, so every one of these is the same column.

    This is the property a deny list keyed on exact strings gets wrong, and it
    is one line of catalog code away from being wrong here too.
    """
    source = build_source("pg")

    try:
        validate(f"SELECT {spelling} FROM customers", source=source, max_rows=100)
    except PolicyViolation as violation:
        assert violation.code is ViolationCode.DENIED_COLUMN
        return
    raise AssertionError(f"{spelling!r} was allowed; it is public.customers.tax_id")


@given(spelling=_cased("customers"))
@PROFILE
def test_a_table_resolves_however_it_is_cased(spelling: str) -> None:
    """The other side of the same coin: folding must not make a real table
    unreachable, or the strictness has become a bug rather than a control."""
    source = build_source("pg")

    result = validate(f"SELECT id FROM {spelling}", source=source, max_rows=100)

    assert str(result.tables[0]) == "public.customers"


@given(spelling=_homoglyphed("email"))
@PROFILE
def test_a_lookalike_column_is_refused_by_name(spelling: str) -> None:
    """A Cyrillic ye makes a different identifier, and the refusal says so
    rather than resolving it to the column it resembles."""
    source = build_source("pg")

    try:
        validate(f"SELECT {spelling} FROM customers", source=source, max_rows=100)
    except PolicyViolation as violation:
        assert violation.code in {
            ViolationCode.UNKNOWN_COLUMN,
            ViolationCode.PARSE_ERROR,
        }
        return
    raise AssertionError(f"{spelling!r} resolved to something; it is not in the catalog")


@given(spelling=_homoglyphed("customers"))
@PROFILE
def test_a_lookalike_table_is_refused_by_name(spelling: str) -> None:
    source = build_source("pg")

    try:
        validate(f"SELECT id FROM {spelling}", source=source, max_rows=100)
    except PolicyViolation as violation:
        assert violation.code in {ViolationCode.UNKNOWN_TABLE, ViolationCode.PARSE_ERROR}
        return
    raise AssertionError(f"{spelling!r} resolved to a table; it is not in the catalog")


@given(
    name=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), max_codepoint=0x24F),
        min_size=1,
        max_size=24,
    )
)
@PROFILE
def test_anything_that_validates_touched_only_catalogued_columns(name: str) -> None:
    """The blunt property, over anything that can be spelled: a statement either
    resolves to catalogued objects or does not run. There is no third outcome.

    Stated over the *result* rather than over the input, which is a correction
    hypothesis made: `SELECT 0 FROM customers` is a literal and validates while
    resolving no column at all, so "the input must be a known column" was never
    the property. "Nothing uncatalogued comes out" is.
    """
    source = build_source("pg")
    catalogued = {
        str(ColumnRef(table.schema_name, table.table_name, column.name))
        for table in source.catalog.tables
        for column in table.columns
    }

    try:
        result = validate(f"SELECT {name} FROM customers", source=source, max_rows=100)
    except PolicyViolation:
        return

    assert {str(column) for column in result.columns} <= catalogued
    assert {str(table) for table in result.tables} == {"public.customers"}


@given(spelling=_cased("tax_id"))
@PROFILE
def test_a_denied_column_stays_denied_in_a_predicate(spelling: str) -> None:
    """Casing plus position: the two dimensions that a rule applied only to the
    select list, or only to exact names, would each get wrong."""
    source = build_source("pg")

    try:
        validate(f"SELECT id FROM customers WHERE {spelling} = '1'", source=source, max_rows=100)
    except PolicyViolation as violation:
        assert violation.code is ViolationCode.DENIED_COLUMN
        return
    raise AssertionError(f"{spelling!r} was allowed in a predicate")


@given(cased=_cased("tax_id"), homoglyphed=_homoglyphed("email"))
@PROFILE
def test_the_generators_produce_what_they_claim(cased: str, homoglyphed: str) -> None:
    """A guard on the guards: a strategy that silently produced one value would
    make every property above pass while testing nothing."""
    assert cased.lower() == "tax_id"
    assert homoglyphed != "email"
    assert homoglyphed.lower() != "email"


def test_both_dialects_agree_about_a_denied_column(either: SourcePolicy) -> None:
    """Not generated, but the same property across engines: the fixture's
    parametrisation is what makes every rule two rules."""
    try:
        validate("SELECT TAX_ID FROM customers", source=either, max_rows=100)
    except PolicyViolation as violation:
        assert violation.code is ViolationCode.DENIED_COLUMN
        return
    raise AssertionError("a denied column was allowed")
