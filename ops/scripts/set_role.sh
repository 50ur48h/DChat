#!/bin/sh
# Set one person's role in one organization, directly in the platform database.
#
#   sh ops/scripts/set_role.sh <org_id> <email> <admin|contributor|reader> "<why>"
#
# An operator escape hatch, not part of the product. Roles change through the
# API, which audits every one and enforces "the last Admin cannot demote
# themselves". This exists for the case the API cannot help with: nobody who can
# sign in holds Admin — an identity provider problem, not an authorization one.
#
# It writes its own audit row with a NULL actor, because "someone edited the
# database" is the honest description and attributing it to a user would be a
# more comfortable lie. Row-level security still applies to the owner role, so
# the transaction sets app.org_id like every other writer does.
set -eu

[ $# -ge 3 ] || {
	echo "usage: $0 <org_id> <email> <admin|contributor|reader> [reason]" >&2
	exit 2
}

org=$1
email=$2
role=$3
reason=${4:-changed directly in the database by an operator}

case "$role" in
admin | contributor | reader) ;;
*)
	echo "role must be admin, contributor or reader" >&2
	exit 2
	;;
esac

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"

db_user=$(sed -n 's/^PLATFORM_DB_USER=//p' .env | head -n 1)
db_name=$(sed -n 's/^PLATFORM_DB_NAME=//p' .env | head -n 1)

docker compose --env-file .env -f ops/docker-compose.yml exec -T platform-pg \
	psql -U "${db_user:-dataagent}" -d "${db_name:-dataagent}" -v ON_ERROR_STOP=1 \
	-v org="$org" -v email="$email" -v role="$role" -v reason="$reason" <<'SQL'
BEGIN;
SELECT set_config('app.org_id', :'org', true);

UPDATE org_memberships m
   SET role = :'role'
  FROM users u
 WHERE u.id = m.user_id
   AND m.org_id = :'org'::uuid
   AND u.email = :'email';

INSERT INTO audit_log (org_id, actor_user_id, action, object_type, object_id, details)
SELECT :'org'::uuid, NULL, 'member.role_changed', 'membership', u.id::text,
       jsonb_build_object('role', :'role',
                          'by', 'operator, directly in the database',
                          'reason', :'reason')
  FROM users u
  JOIN org_memberships m ON m.user_id = u.id AND m.org_id = :'org'::uuid
 WHERE u.email = :'email';

SELECT u.email, m.role
  FROM org_memberships m JOIN users u ON u.id = m.user_id
 WHERE m.org_id = :'org'::uuid
 ORDER BY u.email;
COMMIT;
SQL
