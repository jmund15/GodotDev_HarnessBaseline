#!/usr/bin/env bash
# Proof for sc_gate_balance in lib/sidecar_common.sh: a transient probe failure retries before it
# refuses. Measured 2026-09-15: one 15 s curl timeout voided benchmark cell ARM-T7-FLASH-r1 at $0,
# while the endpoint answered in 0.5 s a minute later.
#   1. one empty answer, then a balance -> passes on the second try;
#   2. no answer on any try -> exit 6 after exactly 3 tries;
#   3. a low balance is an answer, not a failure -> exit 6 after 1 try;
#   4. a healthy first answer -> passes after 1 try.
# curl and sleep are planted as functions; every case must produce output.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
fail=0
ran=0
ok()  { ran=$((ran + 1)); echo "OK   $1"; }
bad() { ran=$((ran + 1)); echo "FAIL $1"; fail=1; }
expect() { if [ "$2" = "$3" ]; then ok "$1 -> $3"; else bad "$1: expected '$2', got '$3'"; fi; }

probe() {  # $1 = space-separated answers per try ("-" = empty); prints "rc=<n> tries=<n>"
  local answers="$1" calls rc
  calls="$(mktemp)"
  ( SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1
    SC_AUTH_TIER=gated SC_BALANCE_URL="https://balance.invalid" SC_CREDENTIAL=k SC_MIN_BALANCE=1
    SC_ALIAS=flash SC_MODEL=deepseek-flash SC_BAND=Hot SC_BAND_P=2 SC_MIN_BAND="On pace" SC_FRESH_RATE=0.3
    read -r -a SEQ <<< "$answers"
    curl() { local n; n=$(wc -l < "$calls"); echo x >> "$calls"
             local a="${SEQ[$n]:--}"; [ "$a" = "-" ] || printf '%s' "$a"; }
    sleep() { :; }
    sc_gate_balance >/dev/null 2>&1 )
  rc=$?
  printf 'rc=%s tries=%s' "$rc" "$(wc -l < "$calls" | tr -d ' ')"
  rm -f "$calls"
}
GOOD='{"balance_infos":[{"total_balance":"9.57"}]}'
LOW='{"balance_infos":[{"total_balance":"0.20"}]}'

expect "empty then balance -> pass on try 2" "rc=0 tries=2" "$(probe "- $GOOD")"
expect "never answers -> exit 6 after 3 tries" "rc=6 tries=3" "$(probe "- - - -")"
expect "low balance -> exit 6, no retry" "rc=6 tries=1" "$(probe "$LOW")"
expect "healthy first answer -> pass, 1 try" "rc=0 tries=1" "$(probe "$GOOD")"

[ "$ran" -eq 4 ] || { echo "FAIL expected 4 cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_balance_retry" || echo "FAIL test_sidecar_balance_retry"
exit "$fail"
