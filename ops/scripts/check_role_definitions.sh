#!/usr/bin/env sh
#
# Every role definition id in `roles.bicep` names a role that exists (B-130).
#
# **Why this exists.** `keyVaultSecretsOfficer` was written as
# `b86a8fe4-44ce-4948-aff7-8adaef4a4c62`. The real id is
# `b86a8fe4-44ce-4948-aee5-eccb2c155cd7` — the first three groups happen to match
# and the last two were invented. The deployment failed with
# `RoleDefinitionDoesNotExist`, three minutes in, after the resource group had
# already been touched.
#
# **A GUID is the one kind of constant that review cannot check.** Every other
# wrong value in this repository has been catchable by reading: a misspelled
# setting name reads wrong, a bad DSN reads wrong, `${${$name}}` reads wrong once
# you know the idiom. A role id that is wrong in its second half reads exactly
# like a role id that is right, to a reviewer and to `bicep build` alike — and
# `what-if` reported no problem, because it does not resolve role definitions
# either. The only thing that can tell the difference is Azure.
#
# So this asks Azure, before the deployment starts rather than partway through
# it. It costs one API call per distinct id and turns a failed deployment into a
# refused one, which is the difference between a resource group in an
# intermediate state and a pipeline that never began.
#
# **Scope, stated because it is narrower than the name suggests.** It reads
# `roles.bicep` only. Every GUID literal in that file is a role definition id by
# construction, which is what makes the extraction safe; a GUID literal elsewhere
# in `infra/` is probably not one, and checking those would produce failures
# about things that are not roles. A role assigned from another module would not
# be covered — if that ever happens, widen this deliberately rather than by
# globbing.
#
# POSIX sh, for the reason the other ops scripts are: see db_setup.sh.

set -eu

ROLES_FILE="${ROLES_FILE:-infra/modules/roles.bicep}"

[ -f "$ROLES_FILE" ] || {
  echo "check_role_definitions: $ROLES_FILE does not exist — is the path right?" >&2
  exit 1
}

# Quoted GUID literals, deduplicated. `grep -o` rather than a line match: the ids
# live in `var` assignments today and there is no reason for this to care.
ids=$(grep -oE "'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'" "$ROLES_FILE" |
  tr -d "'" | sort -u)

if [ -z "$ids" ]; then
  # Not "nothing to check": a file that yields no ids means the extraction has
  # stopped matching the file, and a guard that silently checks nothing is worse
  # than no guard. This repository has shipped that defect twice.
  echo "check_role_definitions: found no role definition ids in $ROLES_FILE." >&2
  echo "Either the file no longer assigns roles, or this pattern has stopped" >&2
  echo "matching it. Both need a person." >&2
  exit 1
fi

failures=0
for id in $ids; do
  name=$(az role definition list --query "[?name=='$id'].roleName" -o tsv --only-show-errors 2>/dev/null | head -1)
  if [ -n "$name" ]; then
    printf '  ok    %s  %s\n' "$id" "$name"
  else
    printf '  FAIL  %s  no such role definition in this subscription\n' "$id" >&2
    failures=$((failures + 1))
  fi
done

if [ "$failures" -gt 0 ]; then
  echo >&2
  echo "check_role_definitions: $failures id(s) name no role that exists." >&2
  echo "Look the role up rather than recalling it:" >&2
  echo "  az role definition list --name 'Key Vault Secrets Officer' --query '[].name' -o tsv" >&2
  echo "Deploying with one of these fails partway through with" >&2
  echo "RoleDefinitionDoesNotExist, after the resource group has been touched." >&2
  exit 1
fi

echo "check_role_definitions: every role id in $ROLES_FILE names a real role."
