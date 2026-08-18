#!/usr/bin/env bash
#
# BACKLOG.md is the only record of why something was *not* done. Every deferred
# decision this project has made is one row, and a row that disappears takes its
# reason with it -- which is worse than losing a feature, because afterwards
# nobody can tell that anything is missing.
#
# It went wrong exactly once and nothing noticed. Filing B-080 prepended its row
# to B-076's and dropped the newline between them. Every character of B-076
# survived; it simply no longer began a line, so `grep '^| B-076'` found nothing
# and a later edit to B-076's own text landed inside what read as B-080's cell.
# An id audit by hand at the end of a session caught it. This is that audit.
#
# B-019 built the same guard for STATUS.md after a docs PR gutted it, and the
# argument transfers unchanged: a file only a careful human reads is one that
# eventually nobody reads carefully.
#
# Three questions, in order:
#
#   1. Does every row still look like a row? It begins a line, it has the seven
#      columns the header declares, and its Prio and Status come from the
#      vocabulary §1.5 names. These hold with no history at all.
#   2. Are the ids whole? Unique, and contiguous from B-001 with no gaps --
#      §1.5 is append-only, never renumbered, never deleted, so a gap is a row
#      that has gone missing, and a row that lost its newline shows up here too.
#   3. Did this branch take an id away? Against the base branch's copy: no id
#      that existed there may be absent here.
#
# Growth is always fine. Rewording is fine. Losing a row is not.
#
# Usage:
#   bash scripts/check_backlog.sh              # baseline from git (CI, and locally)
#   bash scripts/check_backlog.sh --selftest   # prove the guard still fails when it should
#
# Overrides, all used by --selftest and none needed in normal use:
#   BACKLOG_FILE           path to check          (default docs/plan/BACKLOG.md)
#   BACKLOG_BASELINE_FILE  baseline as a file     (default: read BACKLOG_BASE_REF from git)
#   BACKLOG_BASE_REF       baseline as a git ref  (default: origin/main, then HEAD^)

set -euo pipefail

BACKLOG_FILE=${BACKLOG_FILE:-docs/plan/BACKLOG.md}
BACKLOG_BASE_REF=${BACKLOG_BASE_REF:-}
BACKLOG_BASELINE_FILE=${BACKLOG_BASELINE_FILE:-}

# The columns the table declares, verbatim. Adding one is a deliberate change to
# this line and to every row -- which is the point: rows are counted against
# whatever this header says, so the two can never drift apart quietly.
readonly EXPECTED_HEADER='| ID | Date | Found during | Title & detail | Suggested phase | Prio | Status |'

# Plan §2.3. `Status` is matched on its opening word only; the rest of the cell
# is free prose, and usually the PR number and what shipped.
#
# Two of these are wider than §2.3's original four. `in progress` and `accepted`
# were already in use -- B-059 was half-built when this guard was written, and
# the owner *accepted* B-053 rather than dropping it -- and the vocabulary a
# guard enforces has to be the one the project actually uses, or the first thing
# anyone does with the guard is switch it off. Declaring the two states changes
# no row's meaning; rewriting two rows to fit the shorter list would have. Both
# are now named in §1.5, §2.3 and the file's own header.
readonly PRIO_VOCABULARY='P1|P2|P3'
readonly STATUS_VOCABULARY='open|planned|in progress|done|dropped|accepted'

# What a row's opening cells look like: `| B-042 | 2026-08-14 |`. Distinctive
# enough that finding one anywhere but the start of a line means a row has lost
# its newline and is now sitting inside its neighbour.
readonly ID_CELL='\| B-[0-9]{3} \| [0-9]{4}-[0-9]{2}-[0-9]{2} \|'

# A floor for the case where no baseline can be resolved -- a shallow clone, or
# the very first commit. The file holds 81 rows today. This catches a truncated
# tail, which is the one gutting that leaves the surviving ids contiguous and so
# slips past every other check here. It is not a target; it moves up by hand.
MIN_ROWS=50

fail() {
  printf 'check_backlog: %s\n' "$1" >&2
  failures=$((failures + 1))
}

# GFM reads `\|` inside a cell as a literal pipe rather than a column boundary,
# and so must we -- B-013 documents `disable\|require\|verify-ca\|verify-full`,
# and B-081 quotes a row of this very table. Stripping the escapes before
# counting is also what lets a row quote another row's id cell without the guard
# mistaking the quotation for a second row.
strip_escaped_pipes() {
  sed 's/\\|//g' "$1"
}

# Emits "id<TAB>columns<TAB>prio<TAB>status" for every line that starts a row.
# Prio and Status are indexed from the right, so a row with the wrong column
# count still reports the vocabulary it used rather than reporting nonsense.
#
# ID_CELL is spelled out again here rather than interpolated, and spelled
# awkwardly. A `\|` inside an awk *string* is an escape that collapses to a bare
# `|`, which turns the pattern into an alternation matching every line in the
# file -- so the pipes are bracket expressions. The date is written out digit by
# digit because `{4}` is an interval expression, and CI's awk is mawk.
readonly ROWS_AWK='
  function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
  BEGIN { FS = "|" }
  { sub(/\r$/, "") }
  /^[|] B-[0-9][0-9][0-9] [|] [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [|]/ {
    print trim($2) "\t" (NF - 2) "\t" trim($(NF - 2)) "\t" trim($(NF - 1))
  }
'

# --- 1. Every row still looks like a row ------------------------------------
check_rows() {
  local file=$1 columns=$2

  local id cols prio status
  while IFS=$'\t' read -r id cols prio status; do
    ((cols == columns)) ||
      fail "$id has $cols columns; the header declares $columns — an unescaped '|' inside a cell must be written '\\|'"

    # `**P1**` is emphasis, not a different priority.
    local plain_prio=${prio//\*/} plain_status=${status//\*/}

    [[ $plain_prio =~ ^($PRIO_VOCABULARY)$ ]] ||
      fail "$id has Prio '$prio'; §1.5 names $PRIO_VOCABULARY"

    [[ $plain_status =~ ^($STATUS_VOCABULARY)( .*)?$ ]] ||
      fail "$id has Status '${plain_status:0:40}'; §1.5 names $STATUS_VOCABULARY"
  done < <(awk "$ROWS_AWK" "$file")
}

# --- 2. The ids are whole ---------------------------------------------------
check_ids() {
  local file=$1

  # The defect this guard exists for: an id cell that is not at the start of a
  # line is a row swallowed by the one above it.
  # `|| true` on both: grep exits 1 when it matches nothing, and under
  # `pipefail` a file with no rows left at all would kill the script right here
  # -- exiting non-zero without printing the one thing worth printing.
  local at_start anywhere
  at_start=$(grep -cE "^$ID_CELL" "$file" || true)
  anywhere=$({ grep -oE "$ID_CELL" "$file" || true; } | wc -l | tr -d ' ')
  if ((anywhere > at_start)); then
    local stray
    stray=$(grep -oE ".$ID_CELL" "$file" | grep -oE 'B-[0-9]{3}' | tr '\n' ' ')
    fail "${stray% } does not begin a line — the newline before it was lost and its row is now inside another row"
  fi

  local -a ids=()
  local id
  while IFS=$'\t' read -r id _; do
    ids+=("$id")
  done < <(awk "$ROWS_AWK" "$file")

  local rows=${#ids[@]}
  if ((rows < MIN_ROWS)); then
    fail "only $rows rows left; below the floor of $MIN_ROWS"
  fi
  ((rows > 0)) || return 0

  local duplicates
  duplicates=$(printf '%s\n' "${ids[@]}" | sort | uniq -d | tr '\n' ' ')
  [[ -z $duplicates ]] || fail "duplicate ids: ${duplicates% }"

  # Append-only, never renumbered, never deleted (§1.5) — so the ids present are
  # exactly B-001 through the highest, and any gap is a row that went missing.
  # Order is deliberately not checked: restoring B-076 by hand put it back at
  # the end of the file, which is where an append lands and costs nothing.
  local highest
  highest=$(printf '%s\n' "${ids[@]}" | sort -u | tail -n 1)
  highest=$((10#${highest#B-}))

  local -A present=()
  for id in "${ids[@]}"; do present["$id"]=1; done

  local n missing=""
  for ((n = 1; n <= highest; n++)); do
    id=$(printf 'B-%03d' "$n")
    [[ -n ${present[$id]+set} ]] || missing+="$id "
  done
  [[ -z $missing ]] ||
    fail "the ids run to $(printf 'B-%03d' "$highest") but ${missing% } is not among them — §1.5 leaves no gaps"
}

# --- 3. Shape that needs no history -----------------------------------------
check_structure() {
  local file=$1

  grep -qF -- "$EXPECTED_HEADER" "$file" ||
    fail "the table header is no longer the seven columns §2.3 declares"

  check_ids "$file"

  local columns
  columns=$(awk -v h="$EXPECTED_HEADER" 'BEGIN { print gsub(/[|]/, "|", h) - 1 }')
  check_rows "$file" "$columns"
}

# --- 4. What this branch took away ------------------------------------------
check_against_baseline() {
  local file=$1 baseline=$2

  local -A present=()
  local id
  while IFS=$'\t' read -r id _; do
    present["$id"]=1
  done < <(awk "$ROWS_AWK" "$file")

  # Every id, not only the finished ones. STATUS protects what is signed off,
  # because an unchecked box there is still planning. A backlog row is the
  # opposite: an open one is the only note anybody kept, and it is the likeliest
  # to be lost, because nothing downstream refers to it yet.
  local gone=""
  while IFS=$'\t' read -r id _; do
    [[ -n ${present[$id]+set} ]] || gone+="$id "
  done < <(awk "$ROWS_AWK" "$baseline")

  [[ -z $gone ]] ||
    fail "${gone% } was on the baseline and is gone here — a row is never deleted (§1.5), its status becomes done or dropped"
}

resolve_baseline() {
  local dest=$1

  if [[ -n $BACKLOG_BASELINE_FILE ]]; then
    strip_escaped_pipes "$BACKLOG_BASELINE_FILE" >"$dest"
    printf 'baseline: %s\n' "$BACKLOG_BASELINE_FILE"
    return 0
  fi

  local ref
  for ref in ${BACKLOG_BASE_REF:-origin/main HEAD^}; do
    # A ref that resolves to this very commit compares the file with itself and
    # would report a clean bill of health it never actually checked.
    if git rev-parse --verify --quiet "$ref^{commit}" >/dev/null &&
      [[ $(git rev-parse "$ref^{commit}") != $(git rev-parse HEAD) ]] &&
      git show "$ref:$BACKLOG_FILE" >"$dest" 2>/dev/null; then
      printf 'baseline: %s\n' "$ref"
      return 0
    fi
  done

  return 1
}

check_file() {
  failures=0

  if [[ ! -f $BACKLOG_FILE ]]; then
    printf 'check_backlog: %s is missing\n' "$BACKLOG_FILE" >&2
    return 1
  fi

  local subject baseline
  subject=$(mktemp)
  baseline=$(mktemp)
  # shellcheck disable=SC2064  # both paths are expanded now on purpose.
  trap "rm -f '$subject' '$baseline'" RETURN

  strip_escaped_pipes "$BACKLOG_FILE" >"$subject"
  check_structure "$subject"

  if resolve_baseline "$baseline"; then
    check_against_baseline "$subject" "$baseline"
  else
    printf 'check_backlog: no baseline to compare against; structure only\n'
  fi

  if ((failures > 0)); then
    printf '\n%s is append-only and is the only record of why work was deferred.\n' "$BACKLOG_FILE" >&2
    printf 'Plan §1.5: ids are never renumbered and rows are never deleted.\n' >&2
    return 1
  fi

  printf 'check_backlog: %s intact\n' "$BACKLOG_FILE"
  return 0
}

# --- The guard's own tests ---------------------------------------------------
# A check that cannot fail is decoration. These run in CI before the real one,
# so the day someone breaks the matching, the build says so instead of going
# quietly green on a file that has lost a row.
selftest() {
  local passed=0 failed=0
  # Global, not local: the EXIT trap runs after this function has returned, and
  # under `set -u` a local would be unbound by then.
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' EXIT

  # Wide enough to clear MIN_ROWS, so that a case fails for the reason it is
  # named after rather than for being a small file.
  local rows=60

  write_backlog() {
    local file=$1 last=${2:-$rows}
    {
      printf '# BACKLOG — deferred work (append-only)\n\n'
      printf '%s\n' "$EXPECTED_HEADER"
      printf '|----|------|--------------|----------------|-----------------|------|--------|\n'
      local n
      for ((n = 1; n <= last; n++)); do
        printf '| B-%03d | 2026-08-%02d | P1.1 | **A thing.** Deferred because of another thing. | P7 | P2 | open |\n' \
          "$n" "$((n % 28 + 1))"
      done
    } >"$file"
  }

  local good=$dir/good.md
  write_backlog "$good"

  # name : expectation : the file to check : the baseline to check it against
  run_case() {
    local name=$1 expect=$2 subject=$3 baseline=${4:-}
    local status=0 said_why=yes
    BACKLOG_FILE=$subject BACKLOG_BASELINE_FILE=$baseline BACKLOG_BASE_REF= \
      bash "$0" >/dev/null 2>"$dir/stderr" || status=$?
    # A failure has to name itself. Exiting non-zero in silence is the same
    # defect as reporting success without checking anything, wearing the other
    # face -- and it is how a grep that matched nothing once ended a whole run.
    if [[ $expect == fail ]] && ! grep -q '^check_backlog: ' "$dir/stderr"; then
      said_why=no
    fi
    if [[ $said_why == yes ]] &&
      [[ ($expect == pass && $status -eq 0) || ($expect == fail && $status -ne 0) ]]; then
      printf '  ok    %s\n' "$name"
      passed=$((passed + 1))
    else
      printf '  FAIL  %s (expected to %s, exit %d, said why: %s)\n' "$name" "$expect" "$status" "$said_why" >&2
      failed=$((failed + 1))
    fi
  }

  run_case 'a healthy file passes' pass "$good" "$good"

  # The defect this guard was written for, reproduced: the newline in front of
  # B-042's row is dropped, so it ends up inside B-041's. Every character of it
  # still exists, which is exactly why nothing else notices.
  local merged=$dir/merged.md
  awk 'NR == 1 { buf = $0; next }
       /^\| B-042 / { buf = buf $0; next }
       { print buf; buf = $0 }
       END { print buf }' "$good" >"$merged"
  run_case 'a row that lost its newline is caught' fail "$merged" "$good"

  local gap=$dir/gap.md
  grep -v '^| B-030 ' "$good" >"$gap"
  run_case 'a row deleted outright leaves a gap and is caught' fail "$gap" "$good"

  local duplicate=$dir/duplicate.md
  sed 's/^| B-031 /| B-030 /' "$good" >"$duplicate"
  run_case 'a duplicate id is caught' fail "$duplicate" "$good"

  local extra_column=$dir/extra_column.md
  sed 's/^\(| B-020 .*\)another thing\./\1another | thing./' "$good" >"$extra_column"
  run_case 'an unescaped pipe splitting a row is caught' fail "$extra_column" "$good"

  local escaped=$dir/escaped.md
  sed 's/^\(| B-020 .*\)another thing\./\1a quoted row: \\| B-076 \\| 2026-08-17 \\| and a pipe \\|./' \
    "$good" >"$escaped"
  run_case 'an escaped pipe, even quoting a whole row, passes' pass "$escaped" "$good"

  local short_row=$dir/short_row.md
  sed 's/^\(| B-021 .*\) | P7 | P2 | open |$/\1 | P2 | open |/' "$good" >"$short_row"
  run_case 'a row that lost a column is caught' fail "$short_row" "$good"

  local bad_prio=$dir/bad_prio.md
  sed 's/^\(| B-022 .*\)| P2 | open |$/\1| urgent | open |/' "$good" >"$bad_prio"
  run_case 'a Prio outside the vocabulary is caught' fail "$bad_prio" "$good"

  local bad_status=$dir/bad_status.md
  sed 's/^\(| B-023 .*\)| open |$/\1| nearly done |/' "$good" >"$bad_status"
  run_case 'a Status outside the vocabulary is caught' fail "$bad_status" "$good"

  local vocabulary=$dir/vocabulary.md
  sed -e 's/^\(| B-024 .*\)| open |$/\1| **done (#12)** — shipped |/' \
    -e 's/^\(| B-025 .*\)| P2 | open |$/\1| **P1** | in progress (WP10.2d) — half of it |/' \
    -e 's/^\(| B-026 .*\)| open |$/\1| dropped (superseded by B-025) |/' \
    -e 's/^\(| B-027 .*\)| open |$/\1| accepted (owner, 2026-08-16) — risk understood |/' \
    "$good" >"$vocabulary"
  run_case 'every declared status, and a bold Prio, pass' pass "$vocabulary" "$good"

  local gutted=$dir/gutted.md
  head -n 4 "$good" >"$gutted"
  run_case 'a file gutted to its header is caught, and says so' fail "$gutted" "$good"

  local no_header=$dir/no_header.md
  sed 's/^| ID | Date .*/| ID | Date | Title | Prio | Status |/' "$good" >"$no_header"
  run_case 'a header that lost columns is caught' fail "$no_header" "$good"

  # Renumbered so the ids stay contiguous and every structural check passes:
  # only the baseline knows B-060 ever existed. This is the case that proves the
  # comparison against the base branch is doing work of its own.
  local renumbered=$dir/renumbered.md
  write_backlog "$renumbered" $((rows - 1))
  run_case 'a row dropped and the rest renumbered is caught by the baseline' fail "$renumbered" "$good"

  local grown=$dir/grown.md
  write_backlog "$grown" $((rows + 3))
  run_case 'appending new rows passes' pass "$grown" "$good"

  local reworded=$dir/reworded.md
  sed 's/Deferred because of another thing\./Deferred, at considerably greater length, because of another thing./' \
    "$good" >"$reworded"
  run_case 'rewording a row passes' pass "$reworded" "$good"

  local closed=$dir/closed.md
  sed 's/^\(| B-040 .*\)| open |$/\1| done (#72) — shipped with the gate |/' "$good" >"$closed"
  run_case 'closing an open row passes' pass "$closed" "$good"

  printf 'check_backlog --selftest: %d passed, %d failed\n' "$passed" "$failed"
  ((failed == 0))
}

if [[ ${1:-} == "--selftest" ]]; then
  selftest
else
  check_file
fi
