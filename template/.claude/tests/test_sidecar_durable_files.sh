#!/usr/bin/env bash
# Verify that the exit-file publication follows the output-file publication.
set -uo pipefail
LIB="$(cd "$(dirname "$0")/.." && pwd)/scripts/lib/sidecar_common.sh"
WORK="$(mktemp -d)" || exit 2
trap 'rm -rf "$WORK"' EXIT
export SIDECAR_LEDGER_PATH="$WORK/ledger.jsonl"
source "$LIB"
SC_RECORD="$WORK/run.record.json"
OUTPUT='{"answer":1}'
: > "$WORK/seen"
mv() {
  if [ "$2" = "${SC_RECORD}.exit" ]; then
    [ -f "${SC_RECORD}.out" ] && printf 'ready\n' > "$WORK/seen" || printf 'missing\n' > "$WORK/seen"
    sleep 0.1
  fi
  command mv "$@"
}
sc_write_durable_files 0
if [ "$(<"$WORK/seen")" != ready ]; then
  printf 'FAIL: .exit published before .out\n'
  exit 1
fi
[ "$(<"${SC_RECORD}.out")" = "$OUTPUT" ] && [ "$(<"${SC_RECORD}.exit")" = 0 ] || {
  printf 'FAIL: durable result differs\n'; exit 1;
}
SC_RECORD="$WORK/failure.record.json"
mv() {
  [ "$2" = "${SC_RECORD}.out" ] && return 1
  command mv "$@"
}
sc_write_durable_files 1 2>/dev/null
if [ -e "${SC_RECORD}.exit" ]; then
  printf 'FAIL: .exit published despite failed .out\n'
  exit 1
fi
# A gate refusal publishes .exit. sc_parse_flags registers the durable-file writer once -R is
# parsed; the gate ladder exit()s before sc_write_record ever runs, so only that registration can
# publish the verdict, and the watch classifier reads .exit to tell `finished` from `died`.
# The seat's own environment may carry SIDECAR_DETACHED (a sidecar child runs proofs too), which
# would skip that registration: both cases below declare the state they test. The failure case's
# `mv` override stays in scope for the rest of the file, so drop it first — it would fail the .out
# write under a new record name and return before .exit is ever reached.
unset -f mv
SC_RECORD="$WORK/gate.record.json"
( unset SIDECAR_DETACHED SC_SNAPSHOT_OF
  SIDECAR_EXHAUSTED_DIR="$WORK/none" sc_parse_flags -R "$SC_RECORD" -m fixture-model
  SC_TRANSPORT=fixture SC_TRANSPORT_STATE=unavailable SC_ALIAS=fixture SC_UNSUSPEND=0
  SC_TRANSPORT_REASON="fixture suspension"
  sc_gate_availability ) >/dev/null 2>&1
if [ ! -f "${SC_RECORD}.exit" ] || [ "$(<"${SC_RECORD}.exit")" != 7 ]; then
  printf 'FAIL: a gate refusal published no .exit, or not the gate exit code\n'
  exit 1
fi

# A detached job registers NO second durable writer: the -X wrapper owns .out/.exit there, so a
# second writer would race it. Neither the launcher's .pid nor an .exit may appear.
SC_RECORD="$WORK/detached.record.json"
( SIDECAR_DETACHED=1 SIDECAR_EXHAUSTED_DIR="$WORK/none" \
  sc_parse_flags -R "$SC_RECORD" -m fixture-model
  exit 5 ) >/dev/null 2>&1
if [ -e "${SC_RECORD}.pid" ] || [ -e "${SC_RECORD}.exit" ]; then
  printf 'FAIL: a detached job registered a durable writer\n'
  exit 1
fi
printf 'test_sidecar_durable_files: 4 passed, 0 failed\n'
