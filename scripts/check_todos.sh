#!/usr/bin/env bash
#
# Every TODO in source must carry a backlog ID -- TODO(B-123) -- so deferred work
# is tracked in docs/plan/BACKLOG.md instead of rotting in a comment. FIXME, HACK
# and XXX are banned outright: file a backlog entry and write honest code.
# Plan sections 1.5 and 4.3.

set -euo pipefail

roots=()
for dir in apps ops; do
  [[ -d "$dir" ]] && roots+=("$dir")
done

if [[ ${#roots[@]} -eq 0 ]]; then
  echo "check_todos: no source directories to scan"
  exit 0
fi

flags=(
  -rnI
  --include=*.py --include=*.ts --include=*.tsx --include=*.mts --include=*.mjs --include=*.sql
  --exclude-dir=node_modules --exclude-dir=.next --exclude-dir=.venv
  --exclude-dir=__pycache__ --exclude-dir=.ruff_cache --exclude-dir=dist
)

# grep exits 0 with matches, 1 with none, and >1 on a real error. Collapsing all
# three to "no matches" would turn a broken guard into a silent pass, which is
# how a check like this stops protecting anything.
scan() {
  local output status
  set +e
  output=$(grep "$@")
  status=$?
  set -e
  if ((status > 1)); then
    echo "check_todos: grep failed with exit $status" >&2
    exit 2
  fi
  printf '%s' "$output"
}

# Deliberately POSIX ERE rather than a -P lookahead: PCRE mode is unavailable in
# some locales (notably Git Bash on Windows), and this must behave identically
# for a developer and for CI.
todos=$(scan "${flags[@]}" -E 'TODO' "${roots[@]}")
orphans=""
if [[ -n "$todos" ]]; then
  orphans=$(printf '%s\n' "$todos" | { grep -v -E 'TODO\(B-[0-9]+\)' || true; })
fi

banned=$(scan "${flags[@]}" -E '(FIXME|HACK|XXX)' "${roots[@]}")

status=0

if [[ -n "$orphans" ]]; then
  echo "Orphan TODOs found. Add a backlog ID, e.g. TODO(B-017): ..."
  echo "$orphans"
  echo
  status=1
fi

if [[ -n "$banned" ]]; then
  echo "Banned markers found (FIXME/HACK/XXX). Raise a BACKLOG.md entry instead."
  echo "$banned"
  echo
  status=1
fi

if [[ $status -eq 0 ]]; then
  echo "check_todos: clean"
fi

exit "$status"
