#!/usr/bin/env bash
# Proof that disclosure follows the kind in lib/sidecar_common.sh (guards/tool_profiles.json `kinds`):
#   1. scout is bare, lens is pointer, reviewer/author/judge are full: the parser leaves the row's tier;
#   2. -D refuses on every kind ("disclosure follows the kind: pass -G <kind>"), the row's own tier included;
#   3. the one exception is -G arm, whose row has no disclosure: the caller's -D is required and kept.
# Each case sources the lib in a fresh subshell so one case's flags cannot leak into the next.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
fail=0
ran=0

tier_for() {  # flags -> the disclosure tier the parser leaves, or the refusal text
  ( # shellcheck source=../scripts/lib/sidecar_common.sh
    . "$LIB" >/dev/null 2>&1
    sc_parse_flags "$@" >/dev/null 2>&1
    printf '%s' "$SC_DISCLOSURE" )
}

check() {  # $1 label, $2 expected, $3 actual
  ran=$((ran + 1))
  if [ "$3" = "$2" ]; then echo "OK   $1 -> $3"; else echo "FAIL $1: expected $2, got '${3}'"; fail=1; fi
}

refuses() {  # $1 label, rest: flags -> the parser exits 2 naming the kind
  local label="$1" out; shift
  out="$( ( . "$LIB" >/dev/null 2>&1; sc_parse_flags "$@" ) 2>&1; echo "rc=$?" )"
  ran=$((ran + 1))
  case "$out" in
    *"disclosure follows the kind: pass -G <kind>"*"rc=2") echo "OK   $label" ;;
    *) echo "FAIL $label: got '$out'"; fail=1 ;;
  esac
}

check "scout is bare"            bare    "$(tier_for -G scout)"
check "lens is pointer"          pointer "$(tier_for -G lens)"
check "reviewer is full"         full    "$(tier_for -G reviewer)"
check "author is full"           full    "$(tier_for -G author)"
check "judge is full"            full    "$(tier_for -G judge)"
check "bench-score is pointer"   pointer "$(tier_for -G bench-score)"
check "bench-review is bare"     bare    "$(tier_for -G bench-review)"
check "arm keeps the caller's -D" pointer "$(tier_for -G arm -D pointer)"
refuses "scout -D full refuses"       -G scout -D full
refuses "lens -D bare refuses"        -G lens -D bare
refuses "reviewer -D full refuses"    -G reviewer -D full
refuses "author -D pointer refuses"   -G author -D pointer
refuses "judge -D bare refuses"       -G judge -D bare

[ "$ran" -eq 13 ] || { echo "FAIL expected 13 cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_disclosure_default" || echo "FAIL test_sidecar_disclosure_default"
exit "$fail"
