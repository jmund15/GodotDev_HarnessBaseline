#!/usr/bin/env bash
# Proof for the sidecar long-context flag (lib/sidecar_common.sh):
#   1. -M sets SC_LONG_CONTEXT; without it the flag stays unset;
#   2. sc_declared_context_window declares the registry DEFAULT window minus the output reserve, and
#      under -M the registry MAX window minus the same reserve;
#   3. a model the registry does not know declares nothing (the child keeps its own default).
# Expected windows are read from the registry CLI: the unit under test is the flag -> --max choice and
# the reserve arithmetic, not the registry's numbers. Every case must produce output.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
REG="$HERE/../tools/model_registry.py"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
fail=0
ran=0

check() {  # $1 label, $2 expected, $3 actual
  ran=$((ran + 1))
  if [ -n "$2" ] && [ "$3" = "$2" ]; then echo "OK   $1 -> $3"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}

flag_for() {
  ( . "$LIB" >/dev/null 2>&1; sc_parse_flags "$@" >/dev/null 2>&1; printf '%s' "${SC_LONG_CONTEXT:-unset}" )
}

window_for() {  # $1 model, $2 "long" or ""
  ( . "$LIB" >/dev/null 2>&1
    SC_REGISTRY_CLI="$REG"; SC_MODEL="$1"; SC_OUTPUT_RESERVE=16000
    if [ -n "$2" ]; then SC_LONG_CONTEXT=1; else unset SC_LONG_CONTEXT; fi
    sc_declared_context_window 2>/dev/null )
}

DEF="$(python3 "$REG" context-window luna 2>/dev/null)"
MAX="$(python3 "$REG" context-window luna --max 2>/dev/null)"
[ -n "$DEF" ] && [ -n "$MAX" ] && [ "$MAX" -gt "$DEF" ] || { echo "FAIL: registry has no distinct luna default/max ($DEF/$MAX)"; exit 1; }

check "-M sets long context"                 1     "$(flag_for -m luna -M)"
check "no -M leaves it unset"                unset "$(flag_for -m luna)"
check "default window minus reserve"         "$((DEF - 16000))" "$(window_for luna "" "")"
check "-M declares max window minus reserve" "$((MAX - 16000))" "$(window_for luna long "")"
refused_on() {  # $1 transport, $2 model (default x): exit code of sc_validate_common under -M
  ( SC_TRANSPORT="$1"; . "$LIB" >/dev/null 2>&1; SC_REGISTRY_CLI="$REG"; sc_parse_flags -m "${2:-x}" -M -G scout >/dev/null 2>&1
    ( sc_validate_common >/dev/null 2>&1 ); echo "rc=$?" ) 2>/dev/null | tail -1
}
check "-M refused on a non-codex transport"  "rc=2" "$(refused_on deepseek)"
check "-M accepted on codex"                  "rc=0" "$(refused_on codex)"
# Off codex, -M opts a row with a long-context price step past it, and is refused where it changes nothing.
check "-M accepted on a stepped anthropic row"   "rc=0" "$(refused_on anthropic haiku)"
check "-M refused on an unstepped anthropic row" "rc=2" "$(refused_on anthropic sonnet)"
out="$(window_for no-such-model "" "")"
ran=$((ran + 1))
if [ -z "$out" ]; then echo "OK   unknown model declares nothing"; else echo "FAIL unknown model declared '$out'"; fail=1; fi

override_for() {
  ( unset SIDECAR_COMPACT_AT_TOKENS
    SC_TRANSPORT=anthropic; . "$LIB" >/dev/null 2>&1
    sc_parse_flags -K "$1" >/dev/null 2>&1 || exit $?
    printf '%s' "${SIDECAR_COMPACT_AT_TOKENS:-unset}" )
}
check "-K sets the compact point" 967000 "$(override_for 967000)"
check "-K accepts a 5-digit compact point" 67000 "$(override_for 67000)"
for bad in 9999 10000000 abc 1.5 -1 '' 999999999999999999999999; do
  override_for "$bad" >/dev/null; rc=$?
  check "-K refuses '$bad'" 2 "$rc"
done
for transport in anthropic anthropic-secondary codex deepseek opencode opencode-cli; do
  ( SC_TRANSPORT="$transport"; . "$LIB" >/dev/null 2>&1
    export SIDECAR_COMPACT_AT_TOKENS=967000
    sc_parse_flags -m haiku -G scout >/dev/null 2>&1
    sc_validate_common >/dev/null 2>&1 ); rc=$?
  case "$transport" in anthropic*) expected=0 ;; *) expected=2 ;; esac
  check "env override contract on $transport" "$expected" "$rc"
done
( SC_TRANSPORT=anthropic; . "$LIB" >/dev/null 2>&1
  export SIDECAR_COMPACT_AT_TOKENS=bad
  sc_parse_flags -m haiku55 >/dev/null 2>&1 ); rc=$?
check "env override gets the same validation" 2 "$rc"
( SC_TRANSPORT=anthropic; . "$LIB" >/dev/null 2>&1
  unset SIDECAR_COMPACT_AT_TOKENS
  sc_parse_flags -K >/dev/null 2>&1 ); rc=$?
check "-K needs a value" 2 "$rc"

[ "$ran" -gt 0 ] || { echo "FAIL: no case ran"; exit 1; }
echo "$ran cases"
exit $fail
