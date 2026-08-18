#!/bin/sh
# Microsoft's ODBC driver for SQL Server (plan WP3.3, architecture Part 3).
#
# pyodbc is a thin binding over unixODBC: without a driver it imports fine and
# fails at connect time with "Can't open lib ... file not found", which is a
# confusing way to learn that an image is incomplete. Both runtime targets in
# the Dockerfile run this script, so there is one description of what the driver
# install is rather than two that drift.
#
# The signing key is stored as a binary keyring and named by the repository entry
# that uses it. apt-key is deprecated, and dropping a key into trusted.gpg.d
# would trust Microsoft's key for *every* repository configured on the system.
set -eu

export DEBIAN_FRONTEND=noninteractive

# **A mirror that stops answering must not hang the build.** apt's defaults wait
# a long time and retry patiently, which is right for a laptop on hotel wifi and
# wrong for CI: on #71 an `apt-get update` against azure.archive.ubuntu.com sat
# for 32 minutes without failing. Three retries with a 15-second timeout turns a
# flaky mirror into a slow success and a dead one into a prompt failure.
APT_OPTIONS="-o Acquire::Retries=3 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15"

# shellcheck disable=SC2086 # word splitting is what passes these as flags
apt-get $APT_OPTIONS update
# shellcheck disable=SC2086
apt-get $APT_OPTIONS install --no-install-recommends -y ca-certificates curl gnupg

# --batch --yes --no-tty: on a CI runner there is no controlling terminal, and
# gpg fails with "cannot open /dev/tty" rather than reading from the pipe it was
# given. --yes so a re-run overwrites the keyring instead of prompting.
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc |
	gpg --batch --yes --no-tty --dearmor -o /usr/share/keyrings/microsoft-prod.gpg

# Microsoft publishes one repository per distribution release, so the path is
# looked up rather than guessed. An allow-list, not a fallback: a base image that
# quietly changed distribution should fail here, not install nothing and be
# discovered at the first connection.
#
# Both image targets are Debian 12; CI's runner is Ubuntu, and it runs this same
# script so that the driver install is exercised by every connector build rather
# than only when an image is rebuilt.
. /etc/os-release
case "${ID:-}/${VERSION_ID:-}" in
debian/12) repository="debian/12" ;;
ubuntu/24.04) repository="ubuntu/24.04" ;;
ubuntu/22.04) repository="ubuntu/22.04" ;;
*)
	echo "no Microsoft ODBC repository known for ${ID:-unknown} ${VERSION_ID:-unknown}" >&2
	exit 1
	;;
esac

echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/${repository}/prod ${VERSION_CODENAME} main" \
	>/etc/apt/sources.list.d/mssql-release.list

# shellcheck disable=SC2086
apt-get $APT_OPTIONS update
# ACCEPT_EULA is Microsoft's licence prompt for this driver; unattended installs
# have no other way to answer it.
# shellcheck disable=SC2086
ACCEPT_EULA=Y apt-get $APT_OPTIONS install --no-install-recommends -y msodbcsql18 unixodbc

# The tools that fetched the key are not wanted in a running image. Skipped on a
# CI runner, where removing curl from under the rest of the job would be rude.
if [ "${KEEP_BUILD_TOOLS:-0}" != "1" ]; then
	apt-get purge -y curl gnupg
	apt-get autoremove -y
fi
rm -rf /var/lib/apt/lists/*

# Fail the build rather than the first connection.
test -f /etc/odbcinst.ini
grep -q "ODBC Driver 18 for SQL Server" /etc/odbcinst.ini
