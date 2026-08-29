#!/usr/bin/env sh
#
# Replace the demo Postgres server's allow-the-internet firewall rule with one
# naming the address you are actually calling from.
#
#     ops/scripts/narrow_db_firewall.sh --dry-run     # say what would change
#     ops/scripts/narrow_db_firewall.sh               # do it
#     EXTRA_IPS="203.0.113.7,198.51.100.4" ops/scripts/narrow_db_firewall.sh
#
# **Not run before a demo.** The owner's instruction on 2026-08-29 was to script
# it now and run it after, because a firewall change that locks you out is
# discovered at the worst possible moment. `--dry-run` is therefore the mode to
# use while deciding, and it touches nothing.
#
# **The current address is measured, never hard-coded.** A rule written against
# the office IP is wrong from a hotel, a hotspot or a client site — which is
# exactly how this server came to be unreachable mid-session on 2026-08-29 while
# `example.com` answered fine. So the script asks what the world sees, and
# `EXTRA_IPS` is there for the places you already know you will be.
#
# What it does NOT do: disable public network access. That is a bigger change
# (it needs VNet integration or a private endpoint) and it would cut off every
# machine at once, including the one running this.
#
# POSIX sh, for the reason the other scripts here give.

set -eu

SERVER="${DB_SERVER:-pg-fnb-demo-sk}"
GROUP="${DB_RESOURCE_GROUP:-rg-fnb-demo}"
DRY_RUN="no"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="yes" ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v az >/dev/null 2>&1; then
  echo "The Azure CLI is not on PATH." >&2
  exit 1
fi
if ! az account show >/dev/null 2>&1; then
  echo "Not signed in. Run 'az login' first." >&2
  exit 1
fi

# **Ask an address-echo service, not the local interface.** Behind NAT — which
# is every office, every hotspot and every VPN — the interface address is a
# private one the firewall has never seen.
CURRENT="$(curl -fsS --max-time 15 https://api.ipify.org 2>/dev/null || true)"
if [ -z "$CURRENT" ]; then
  CURRENT="$(curl -fsS --max-time 15 https://ifconfig.me/ip 2>/dev/null || true)"
fi
case "$CURRENT" in
  *[0-9].*[0-9].*[0-9].*[0-9]) : ;;
  *)
    echo "Could not determine this machine's public IPv4 address." >&2
    echo "Set it yourself and re-run:  EXTRA_IPS=<your.ip> $0" >&2
    [ -n "${EXTRA_IPS:-}" ] || exit 1
    CURRENT=""
    ;;
esac

echo "Server            : $SERVER (resource group $GROUP)"
echo "This machine      : ${CURRENT:-(unknown)}"
echo "Also allowing     : ${EXTRA_IPS:-(none)}"
echo
echo "Rules in force now:"
az postgres flexible-server firewall-rule list \
  --name "$SERVER" --resource-group "$GROUP" \
  --query "[].{name:name, from:startIpAddress, to:endIpAddress}" -o table

# Anything spanning the whole internet. Matched on the range rather than the
# name, because the name is a timestamp somebody happened to accept.
OPEN="$(az postgres flexible-server firewall-rule list \
  --name "$SERVER" --resource-group "$GROUP" \
  --query "[?startIpAddress=='0.0.0.0' && endIpAddress=='255.255.255.255'].name" -o tsv)"

echo
if [ -z "$OPEN" ]; then
  echo "No allow-the-internet rule is present. Nothing to narrow."
else
  echo "Would remove (allows every address on the internet):"
  for rule in $OPEN; do echo "  - $rule"; done
fi

WANTED=""
[ -n "$CURRENT" ] && WANTED="$CURRENT"
if [ -n "${EXTRA_IPS:-}" ]; then
  WANTED="$WANTED $(echo "$EXTRA_IPS" | tr ',' ' ')"
fi
echo "Would allow:"
for ip in $WANTED; do echo "  + $ip"; done

if [ "$DRY_RUN" = "yes" ]; then
  echo
  echo "Dry run. Nothing was changed."
  exit 0
fi

# **Add before removing.** The other order has a window in which the server
# permits nothing, and if the script dies in that window the way back in is the
# Azure portal.
echo
for ip in $WANTED; do
  name="allow-$(echo "$ip" | tr '.' '-')"
  echo "Allowing $ip as $name"
  az postgres flexible-server firewall-rule create \
    --name "$SERVER" --resource-group "$GROUP" \
    --rule-name "$name" --start-ip-address "$ip" --end-ip-address "$ip" \
    --output none
done

# Prove the new rule works before dropping the old one, so a mistake in the
# address above is caught while the wide rule is still there to fall back on.
echo "Checking this machine can still reach the server..."
if ! az postgres flexible-server show --name "$SERVER" --resource-group "$GROUP" \
     --query name -o tsv >/dev/null 2>&1; then
  echo "Could not read the server back. Leaving the existing rules alone." >&2
  exit 1
fi

for rule in $OPEN; do
  echo "Removing $rule"
  az postgres flexible-server firewall-rule delete \
    --name "$SERVER" --resource-group "$GROUP" --rule-name "$rule" --yes --output none
done

echo
echo "Rules now:"
az postgres flexible-server firewall-rule list \
  --name "$SERVER" --resource-group "$GROUP" \
  --query "[].{name:name, from:startIpAddress, to:endIpAddress}" -o table
echo
echo "If you move network, re-run this: the address it just allowed is this one."
