#!/bin/sh
# Apply ops/seed/seed_pizza_mssql.sql to the compose SQL Server (`make seed.mssql`).
#
# A script rather than a Makefile recipe because recipes here are single lines
# (see the note at the top of the Makefile), and because this needs to fail
# clearly when the container is not running — which is the common case, since
# SQL Server is started on demand.
#
# Neither password reaches the host process list: the SA password is already in
# the container's environment (compose put it there), and the read-only one is
# handed to `docker compose exec -e`, which passes it through the daemon.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"

COMPOSE="docker compose --env-file .env -f ops/docker-compose.yml"

if ! $COMPOSE --profile mssql ps --status running --services 2>/dev/null | grep -qx mssql; then
	echo "The mssql container is not running. Start it with: make up.mssql" >&2
	exit 1
fi

# Read from .env rather than the environment: this script is run through make,
# which does not export the file's contents.
readonly_password=$(sed -n 's/^MSSQL_PIZZA_READONLY_PASSWORD=//p' .env | head -n 1)
if [ -z "$readonly_password" ]; then
	echo "MSSQL_PIZZA_READONLY_PASSWORD is not set in .env (see .env.example)." >&2
	exit 1
fi

printf 'Seeding the SQL Server pizza database...\n'

# Both passwords are expanded by the shell *inside* the container, from
# variables compose and `-e` put there — neither is ever an argument here.
#
# -b: exit non-zero on the first SQL error, so a broken seed fails the target
#     rather than printing an error and reporting success.
# -C: trust the server's self-signed certificate. This is the container talking
#     to itself over the compose network; how the *platform* connects to it is
#     decided by the TLS policy (B-013), not here.
#
# The command begins with `exec` for a Windows reason: Git Bash rewrites any
# argument that starts with a slash into a `C:\...` path, so a command string
# beginning with /opt/... arrives at the container as nonsense. Starting it with
# a word leaves it alone.
$COMPOSE exec -T -e RO_PASSWORD="$readonly_password" mssql \
	sh -c 'exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -b -v ReadonlyPassword="$RO_PASSWORD"' \
	<ops/seed/seed_pizza_mssql.sql

printf '\nRegister it as a data source with the pizza_readonly login, never with sa.\n'
