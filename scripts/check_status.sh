#!/usr/bin/env bash
#
# STATUS.md is the single source of truth for where the build is (plan §1.1), and
# it is edited by every PR -- including PRs about something else entirely. That is
# how it once lost most of its phase checklist to a docs edit that looked tidy in
# review: nothing failed, because nothing was watching.
#
# This is the thing that watches. Two questions, in order:
#
#   1. Does STATUS still have its shape? Header fields, one heading per phase
#      0-12, a GATE line in every phase. These hold with no history at all.
#   2. Did this branch take anything away? Against the base branch's copy:
#      the file may not lose a fifth of its lines, and no item that was already
#      signed off -- a `[x]` WP, backlog item, or GATE -- may come back as
#      anything else, or vanish.
#
# Growth is always fine. Rewording is fine. Losing finished work is not.
#
# Usage:
#   bash scripts/check_status.sh              # baseline from git (CI, and locally)
#   bash scripts/check_status.sh --selftest   # prove the guard still fails when it should
#
# Overrides, both used by --selftest and neither needed in normal use:
#   STATUS_FILE           path to check          (default docs/plan/STATUS.md)
#   STATUS_BASELINE_FILE  baseline as a file     (default: read STATUS_BASE_REF from git)
#   STATUS_BASE_REF       baseline as a git ref  (default: origin/main, then HEAD^)

set -euo pipefail

STATUS_FILE=${STATUS_FILE:-docs/plan/STATUS.md}
STATUS_BASE_REF=${STATUS_BASE_REF:-}
STATUS_BASELINE_FILE=${STATUS_BASELINE_FILE:-}

# The phases are fixed by the plan: Phase N = Milestone MN, 0 through 12. A
# fourteenth would be a change to the plan, and to this line.
LAST_PHASE=12

# A floor for the case where no baseline can be resolved -- a shallow clone, or
# the very first commit. The file holds ~55 tracked items today, so this is a
# long way below anything healthy: it catches a gutting, not a diet. It is not a
# target, and it moves up only by hand.
MIN_ITEMS=30

# How much of the file may disappear in one PR before we stop believing it was
# deliberate. Phrased as the fraction that must REMAIN.
MIN_REMAINING_PERCENT=80

# Emits "key<TAB>state" for every tracked checkbox line, where the key is stable
# across rewordings: a WP number, a backlog ID, or GATE plus its phase. Lines
# with no such key (prose bullets) are not tracked and not reported.
#
# **The key is what the item IS, not what it mentions**, which is why every
# pattern is anchored. Unanchored, `- [x] B-098 … Small, WP11.2's polish pass`
# was keyed **WP11.2**, because the WP branch is tried first and matched in the
# middle of the sentence — so B-098's own state went unwatched from the day it
# was written, and WP11.2 acquired a second, contradictory entry. Leading `*` and
# `~` are stripped first, so bold and struck-through items key the same as plain
# ones.
readonly TRACKED_ITEMS_AWK='
  { sub(/\r$/, "") }
  /^## Phase [0-9]+/ { phase = $3; next }
  /^- \[.\]/ {
    state = substr($0, 4, 1)
    rest  = substr($0, 7)
    gsub(/^[*~ ]+/, "", rest)
    key   = ""
    if (rest ~ /^GATE:/)                       key = "GATE(phase " phase ")"
    else if (match(rest, /^WP[0-9]+\.[0-9]+[a-z]?/)) key = substr(rest, RSTART, RLENGTH)
    else if (match(rest, /^B-[0-9]+/))         key = substr(rest, RSTART, RLENGTH)
    if (key != "") print key "\t" state
  }
'

fail() {
  printf 'check_status: %s\n' "$1" >&2
  failures=$((failures + 1))
}

# --- 1. Shape, with no history needed ---------------------------------------
check_structure() {
  local file=$1

  for field in 'Current position:' 'Merge policy:' 'Blocked on user:' 'Last updated:'; do
    grep -qF "$field" "$file" || fail "the header lost its '$field' line"
  done

  local phase
  for ((phase = 0; phase <= LAST_PHASE; phase++)); do
    local seen
    seen=$(grep -cE "^## Phase $phase( |—|-)" "$file" || true)
    case "$seen" in
      1) ;;
      0) fail "no heading for Phase $phase — the phase checklist is incomplete" ;;
      *) fail "Phase $phase has $seen headings; expected exactly one" ;;
    esac
  done

  # Every phase ends in a gate the user signs off. A phase section with no GATE
  # line has lost its exit criterion, which is the part that stops a phase from
  # being declared done by whoever is tired.
  local phases_with_gate
  phases_with_gate=$(awk "$TRACKED_ITEMS_AWK" "$file" | grep -c '^GATE(phase ' || true)
  if ((phases_with_gate < LAST_PHASE + 1)); then
    fail "only $phases_with_gate of $((LAST_PHASE + 1)) phases carry a GATE line"
  fi

  local items
  items=$(awk "$TRACKED_ITEMS_AWK" "$file" | wc -l | tr -d ' ')
  if ((items < MIN_ITEMS)); then
    fail "only $items tracked items left; below the floor of $MIN_ITEMS"
  fi

  # **One key, one state.** An item may legitimately appear twice — the same WP
  # listed under two phases — and that is fine while both say the same thing.
  # Two entries that *disagree* are not: the baseline comparison keeps the last
  # one it reads, so the file and its baseline can resolve the same key
  # differently and the guard reports a regression nobody made. That is not a
  # hypothetical; it cost an afternoon, and the message it printed —
  # "WP11.2 went from [x] to [ ]" — was true of nothing in the diff.
  local conflicted
  conflicted=$(awk "$TRACKED_ITEMS_AWK" "$file" | sort -u | cut -f1 | uniq -d)
  if [[ -n $conflicted ]]; then
    local key
    while read -r key; do
      fail "$key is tracked more than once with different states — one item, one box"
    done <<<"$conflicted"
  fi
}

# --- 2. What this branch took away ------------------------------------------
check_against_baseline() {
  local file=$1 baseline=$2

  local now before
  now=$(wc -l <"$file" | tr -d ' ')
  before=$(wc -l <"$baseline" | tr -d ' ')
  if ((before > 0 && now * 100 < before * MIN_REMAINING_PERCENT)); then
    fail "$file went from $before lines to $now — more than $((100 - MIN_REMAINING_PERCENT))% of it is gone"
  fi

  declare -A after_state=()
  local key state
  while IFS=$'\t' read -r key state; do
    after_state["$key"]=$state
  done < <(awk "$TRACKED_ITEMS_AWK" "$file")

  # Only signed-off items are protected. An unchecked box may be reworded,
  # renumbered or dropped -- that is planning. A checked one is a claim someone
  # already reviewed and merged.
  while IFS=$'\t' read -r key state; do
    [[ $state == "x" ]] || continue
    if [[ -z ${after_state[$key]+set} ]]; then
      fail "$key was [x] on the baseline and is no longer in the file"
    elif [[ ${after_state[$key]} != "x" ]]; then
      fail "$key went from [x] to [${after_state[$key]}]"
    fi
  done < <(awk "$TRACKED_ITEMS_AWK" "$baseline" | sort)
}

resolve_baseline() {
  local dest=$1

  if [[ -n $STATUS_BASELINE_FILE ]]; then
    cp "$STATUS_BASELINE_FILE" "$dest"
    printf 'baseline: %s\n' "$STATUS_BASELINE_FILE"
    return 0
  fi

  local ref
  for ref in ${STATUS_BASE_REF:-origin/main HEAD^}; do
    # A ref that resolves to this very commit compares the file with itself and
    # would report a clean bill of health it never actually checked.
    if git rev-parse --verify --quiet "$ref^{commit}" >/dev/null &&
      [[ $(git rev-parse "$ref^{commit}") != $(git rev-parse HEAD) ]] &&
      git show "$ref:$STATUS_FILE" >"$dest" 2>/dev/null; then
      printf 'baseline: %s\n' "$ref"
      return 0
    fi
  done

  return 1
}

check_file() {
  failures=0

  if [[ ! -f $STATUS_FILE ]]; then
    printf 'check_status: %s is missing\n' "$STATUS_FILE" >&2
    return 1
  fi

  check_structure "$STATUS_FILE"

  local baseline
  baseline=$(mktemp)
  # shellcheck disable=SC2064  # $baseline is expanded now on purpose.
  trap "rm -f '$baseline'" RETURN
  if resolve_baseline "$baseline"; then
    check_against_baseline "$STATUS_FILE" "$baseline"
  else
    printf 'check_status: no baseline to compare against; structure only\n'
  fi

  if ((failures > 0)); then
    printf '\n%s must keep its phase checklist and its signed-off work.\n' "$STATUS_FILE" >&2
    printf 'Plan §1.1: it is the single source of truth for position.\n' >&2
    return 1
  fi

  printf 'check_status: %s intact\n' "$STATUS_FILE"
  return 0
}

# --- The guard's own tests ---------------------------------------------------
# A check that cannot fail is decoration. These run in CI before the real one,
# so the day someone breaks the matching, the build says so instead of going
# quietly green on a gutted file.
selftest() {
  local passed=0 failed=0
  # Global, not local: the EXIT trap runs after this function has returned, and
  # under `set -u` a local would be unbound by then.
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' EXIT

  local good=$dir/good.md
  {
    printf '# STATUS\n\nCurrent position: x\nMerge policy: ASK\n'
    printf 'Blocked on user: nothing\nLast updated: today\n\n'
    local phase item
    for ((phase = 0; phase <= LAST_PHASE; phase++)); do
      printf '## Phase %d — something (M%d)\n' "$phase" "$phase"
      for item in 1 2 3; do
        printf -- '- [x] WP%d.%d Did a thing\n' "$phase" "$item"
      done
      printf -- '- [x] GATE: proven; user sign-off\n\n'
    done
  } >"$good"

  # name : expectation : the mutation applied to a copy of the good file
  run_case() {
    local name=$1 expect=$2 subject=$3 baseline=${4:-}
    local status=0
    STATUS_FILE=$subject STATUS_BASELINE_FILE=$baseline STATUS_BASE_REF= \
      bash "$0" >/dev/null 2>&1 || status=$?
    if [[ ($expect == pass && $status -eq 0) || ($expect == fail && $status -ne 0) ]]; then
      printf '  ok    %s\n' "$name"
      passed=$((passed + 1))
    else
      printf '  FAIL  %s (expected to %s, exit %d)\n' "$name" "$expect" "$status" >&2
      failed=$((failed + 1))
    fi
  }

  run_case 'a healthy file passes' pass "$good" "$good"

  local gutted=$dir/gutted.md
  head -n 8 "$good" >"$gutted"
  run_case 'a file cut down to its header is caught' fail "$gutted" "$good"

  local no_phase=$dir/no_phase.md
  grep -v '^## Phase 7' "$good" >"$no_phase"
  run_case 'a missing phase heading is caught' fail "$no_phase" "$good"

  local renamed=$dir/renamed.md
  sed 's/^## Phase 5 .*/## Some other heading/' "$good" >"$renamed"
  run_case 'a phase heading renamed away is caught' fail "$renamed" "$good"

  local unchecked=$dir/unchecked.md
  sed 's/^- \[x\] WP3\.2/- [ ] WP3.2/' "$good" >"$unchecked"
  run_case 'signed-off work turned back to todo is caught' fail "$unchecked" "$good"

  local dropped=$dir/dropped.md
  grep -v 'WP4\.1' "$good" >"$dropped"
  run_case 'a signed-off WP deleted outright is caught' fail "$dropped" "$good"

  local no_gate=$dir/no_gate.md
  awk '!/GATE:/ || ++seen != 2' "$good" >"$no_gate"
  run_case 'a phase that lost its GATE line is caught' fail "$no_gate" "$good"

  # The two cases the WP11.2 collision taught, on 2026-08-21.
  local same_key_agreeing=$dir/same_key_agreeing.md
  {
    cat "$good"
    printf -- '- [x] WP3.2 The same work, listed again under another phase\n'
  } >"$same_key_agreeing"
  run_case 'one key twice, both saying the same thing, passes' pass \
    "$same_key_agreeing" "$good"

  local same_key_conflicting=$dir/same_key_conflicting.md
  {
    cat "$good"
    printf -- '- [ ] WP3.2 The same key, disagreeing with itself\n'
  } >"$same_key_conflicting"
  run_case 'one key twice with different states is caught' fail \
    "$same_key_conflicting" "$good"

  local mentions_a_wp=$dir/mentions_a_wp.md
  sed 's/^- \[x\] WP6\.1 Did a thing/- [x] B-777 A backlog item that mentions WP6.1 in passing/' \
    "$good" >"$mentions_a_wp"
  # B-777 must be tracked as B-777. Keyed as WP6.1 it would both hide B-777 and
  # give WP6.1 a duplicate, which is exactly what happened to B-098.
  run_case 'an item that merely mentions a WP keys as itself' fail \
    "$mentions_a_wp" "$good"

  local grown=$dir/grown.md
  {
    cat "$good"
    printf -- '- [ ] WP12.9 Future work\n'
  } >"$grown"
  run_case 'growth and new todo items pass' pass "$grown" "$good"

  local reworded=$dir/reworded.md
  sed 's/Did a thing/Did a thing, described at greater length/' "$good" >"$reworded"
  run_case 'rewording a signed-off item passes' pass "$reworded" "$good"

  printf 'check_status --selftest: %d passed, %d failed\n' "$passed" "$failed"
  ((failed == 0))
}

if [[ ${1:-} == "--selftest" ]]; then
  selftest
else
  check_file
fi
