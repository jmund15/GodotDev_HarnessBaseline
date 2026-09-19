#!/usr/bin/env bash
# Proof for the per-transport permission-mode default in lib/sidecar_common.sh:
#   1. codex, opencode and deepseek default to bypassPermissions (owner decision 2026-09-14);
#   2. anthropic and an unset transport keep auto;
#   3. an explicit -p overrides the default in both directions;
#   4. an invalid -p value is still rejected by sc_validate_common.
# Each case sources the lib in a fresh subshell so one transport's default cannot leak into the next.
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
fail=0
ran=0

mode_for() {  # $1 transport ("" = unset), remaining args = flags
  local transport="$1"; shift
  ( if [ -n "$transport" ]; then SC_TRANSPORT="$transport"; else unset SC_TRANSPORT; fi
    # shellcheck source=../scripts/lib/sidecar_common.sh
    . "$LIB" >/dev/null 2>&1
    sc_parse_flags "$@" >/dev/null 2>&1
    printf '%s' "$SC_PERM_MODE" )
}

check() {  # $1 label, $2 expected, $3 actual
  ran=$((ran + 1))
  if [ "$3" = "$2" ]; then echo "OK   $1 -> $3"; else echo "FAIL $1: expected $2, got '${3}'"; fail=1; fi
}

check "codex default"            bypassPermissions "$(mode_for codex)"
check "opencode default"         bypassPermissions "$(mode_for opencode)"
check "deepseek default"         bypassPermissions "$(mode_for deepseek)"
check "anthropic default"        auto              "$(mode_for anthropic)"
check "unset transport default"  auto              "$(mode_for "")"
check "codex -p auto overrides"  auto              "$(mode_for codex -p auto)"
check "anthropic -p bypass"      bypassPermissions "$(mode_for anthropic -p bypassPermissions)"

ran=$((ran + 1))
out="$( ( SC_TRANSPORT=codex; . "$LIB" >/dev/null 2>&1; sc_parse_flags -p yolo >/dev/null 2>&1; SC_FORMAT=json; sc_validate_common ) 2>&1 )"
rc=$?
if [ "$rc" -ne 0 ] && [[ "$out" == *"invalid permission mode 'yolo'"* ]]; then
  echo "OK   invalid -p rejected (exit $rc)"
else echo "FAIL invalid -p: exit $rc, output: $out"; fail=1; fi

[ "$ran" -eq 8 ] || { echo "FAIL expected 8 cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_perm_default" || echo "FAIL test_sidecar_perm_default"
exit "$fail"
