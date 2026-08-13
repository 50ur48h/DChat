"""Architecture Part 7.5, transcribed as a map from property to proof.

This file is the Phase 5 gate's evidence. Arch 7.5 states what the DAL
guarantees in prose; the accept criterion for milestone M5 is that **every row
of it has a named passing test**. The table below is that mapping, written as
data so it can be checked rather than believed:

* every test named here must exist — a renamed or deleted proof fails the gate
  rather than quietly stopping being run;
* every property must name at least one test, so a row cannot be added to the
  architecture and left unproven.

Read it alongside `docs/architecture.md` §7.5 and §7.1. If the two disagree,
one of them is wrong and neither should be edited to match the other without
saying why.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DAL_TESTS = Path(__file__).parent

#: property (architecture 7.5 / 7.1) → the tests that prove it.
#:
#: Names are bare function names, resolved across the DAL suite. Where the proof
#: is generated rather than enumerated, the property test is named; where it is
#: the corpus, the corpus runner is named and the corpus file says which cases.
PROPERTY_TABLE: dict[str, tuple[str, ...]] = {
    # -- 7.5 layer 1: the AST allowlist ------------------------------------
    "exactly one statement": (
        "test_a_second_statement_is_refused",
        "test_a_trailing_semicolon_is_not_a_second_statement",
        "test_a_comment_cannot_hide_a_second_statement",
        "test_every_corpus_case_is_refused_and_never_sent",
    ),
    "statement type is SELECT or EXPLAIN of one": (
        "test_a_statement_that_is_not_a_select_is_refused",
        "test_a_union_is_a_select",
        "test_explain_of_a_select_is_allowed",
        "test_explain_analyze_is_refused",
        "test_explain_cannot_explain_an_explain",
    ),
    "no DDL, DML or transaction control anywhere in the tree": (
        "test_a_write_inside_a_cte_is_refused",
        "test_a_write_inside_a_subquery_is_refused",
        "test_select_into_is_a_write",
        "test_a_locking_read_is_refused",
        "test_session_settings_cannot_be_changed",
    ),
    "syntax the parser cannot understand is refused, not passed through": (
        "test_syntax_the_parser_does_not_understand_is_refused",
        "test_gibberish_is_refused_without_quoting_it_back",
        "test_an_unterminated_string_is_a_parse_error",
    ),
    "no system schemas": (
        "test_the_engines_own_dictionary_is_not_readable",
        "test_a_system_schema_in_a_subquery_is_refused",
        "test_a_cte_cannot_shadow_a_qualified_system_table",
    ),
    "no reach into another database on the same server": (
        "test_another_database_on_the_same_server_is_refused",
    ),
    "no engine escape-hatch functions": (
        "test_an_escape_hatch_function_is_refused",
        "test_a_function_nobody_can_vouch_for_is_refused",
        "test_a_table_function_is_refused",
    ),
    "ordinary SQL still works (strict, not unusable)": (
        "test_ordinary_functions_still_work",
        "test_a_qualified_column_is_fine",
        "test_ordinary_nesting_is_unaffected",
        "test_a_table_resolves_however_it_is_cased",
    ),
    # -- 7.5 layer 2: catalog grounding ------------------------------------
    "unknown identifiers fail closed, and the refusal names them": (
        "test_an_unknown_table_is_refused_by_name",
        "test_an_unknown_column_is_refused_by_name",
        "test_a_qualified_unknown_column_names_its_table",
        "test_an_unknown_table_in_a_join_is_refused",
        "test_anything_that_validates_touched_only_catalogued_columns",
    ),
    "lookalike and re-cased identifiers do not bypass grounding": (
        "test_a_denied_column_stays_denied_however_it_is_cased",
        "test_a_lookalike_column_is_refused_by_name",
        "test_a_lookalike_table_is_refused_by_name",
    ),
    "ambiguity is refused rather than guessed": (
        "test_a_name_in_two_schemas_must_be_qualified",
        "test_an_ambiguous_column_says_so",
    ),
    # -- 7.1 step 3 / 7.6: column policy -----------------------------------
    "a denied column is refused wherever it appears in the AST": (
        "test_a_denied_column_is_refused_wherever_it_appears",
        "test_an_alias_does_not_launder_a_denied_column",
        "test_a_cte_does_not_launder_a_denied_column",
        "test_a_denied_column_stays_denied_in_a_predicate",
        "test_union_smuggling_is_still_caught",
    ),
    "SELECT * is expanded against the catalog before column rules apply": (
        "test_a_star_is_expanded_against_the_catalog",
        "test_a_star_over_a_denied_column_is_refused",
    ),
    "a column is judged by the policy of the table it actually came from": (
        "test_the_same_alias_in_two_scopes_is_judged_separately",
        "test_a_cte_name_is_not_looked_up_in_the_catalog",
    ),
    "masked columns are masked in results": (
        "test_a_masked_column_is_masked_in_the_result",
        "test_an_expression_over_a_masked_column_is_masked",
        "test_max_over_a_masked_column_is_masked",
        "test_two_columns_of_the_same_name_are_masked_by_position",
        "test_a_masked_column_is_masked_before_the_caller_sees_it",
    ),
    "aggregates over masked columns remain usable": (
        "test_a_count_over_a_masked_column_is_a_number",
        "test_a_masked_column_in_a_predicate_is_allowed",
    ),
    # -- 7.1 steps 5-7: bounded execution and the record -------------------
    "every execution is bounded by rows and by time": (
        "test_the_row_cap_is_written_into_the_sql_and_into_the_fetch",
        "test_a_caller_may_not_ask_for_more_than_policy_allows",
        "test_a_smaller_limit_in_the_query_is_kept",
        "test_the_deadline_comes_from_policy",
    ),
    "a refused statement never reaches the database": (
        "test_a_refused_statement_never_reaches_the_connector",
        "test_every_corpus_case_is_refused_and_never_sent",
    ),
    "a statement too complex to check is refused rather than crashing": (
        "test_a_statement_too_deep_to_check_is_refused",
        "test_a_statement_longer_than_the_limit_is_refused",
        "test_a_recursion_failure_below_still_reaches_the_caller_as_a_refusal",
    ),
    "every attempt is recorded, including refusals": (
        "test_a_successful_query_is_recorded_with_what_it_read",
        "test_a_refusal_is_recorded_with_its_code",
        "test_a_failure_is_recorded_with_its_sanitized_reason",
        "test_every_outcome_writes_exactly_one_record",
        "test_the_audit_trail_names_what_was_refused",
    ),
    "nothing unmasked is persisted": (
        "test_the_stored_sample_is_the_masked_one",
        "test_nothing_unmasked_reaches_the_platform_database",
        "test_the_full_result_goes_to_the_store_and_the_row_points_at_it",
    ),
    # -- 7.4: what a refusal may say ---------------------------------------
    "refusals are structured and safe to show the model": (
        "test_a_violation_carries_a_code_and_a_safe_message",
        "test_no_refusal_leaks_anything_but_identifiers",
        "test_a_violation_never_chains_the_parser_error",
        "test_each_code_has_a_statement_that_produces_it",
    ),
    # -- 5.1: the type gate ------------------------------------------------
    "only the sanctioned validator can declare SQL runnable": (
        "test_the_sanctioned_list_is_exactly_what_it_should_be",
        "test_only_sanctioned_modules_build_queries",
        "test_the_result_is_a_validated_query_from_this_module",
    ),
}


def _defined_test_names() -> set[str]:
    """Every test function defined in the DAL suite and in the query-gate suite.

    Parsed rather than imported: importing pulls in fixtures and a database, and
    this check is about names on disk.
    """
    roots = [DAL_TESTS, DAL_TESTS.parent / "connectors", DAL_TESTS.parent / "db"]
    names: set[str] = set()
    for root in roots:
        for path in root.glob("test_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(
                    node, ast.FunctionDef | ast.AsyncFunctionDef
                ) and node.name.startswith("test_"):
                    names.add(node.name)
    return names


@pytest.mark.parametrize(
    ("prop", "tests"), PROPERTY_TABLE.items(), ids=lambda value: str(value)[:60]
)
def test_every_property_names_tests_that_exist(prop: str, tests: tuple[str, ...]) -> None:
    """The gate's own guard: a proof that has been renamed or deleted fails here
    rather than quietly ceasing to run."""
    defined = _defined_test_names()
    missing = [name for name in tests if name not in defined]

    assert not missing, (
        f"{prop!r} names tests that do not exist: {missing}. Either the test was "
        "renamed — update this row — or the proof is gone, in which case the "
        "property is no longer proven and the gate should not pass."
    )


def test_every_property_has_at_least_one_proof() -> None:
    empty = [prop for prop, tests in PROPERTY_TABLE.items() if not tests]

    assert not empty, f"properties with no test: {empty}"


def test_the_table_covers_the_pipeline_end_to_end() -> None:
    """A coarse completeness check: the eight numbered rules of the validator's
    pipeline, plus execution and recording, all appear. It cannot prove the
    table is complete — only a reader against 7.5 can — but it catches a whole
    section going missing."""
    joined = " ".join(PROPERTY_TABLE).lower()

    for required in (
        "one statement",
        "select",
        "transaction control",
        "system schema",
        "function",
        "unknown identifiers",
        "denied column",
        "select *",
        "masked",
        "bounded",
        "recorded",
    ):
        assert required in joined, f"the property table says nothing about {required!r}"
