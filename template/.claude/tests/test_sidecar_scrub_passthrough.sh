#!/usr/bin/env bash
# Proof that sc_scrub_env (lib/sidecar_common.sh) passes the print-mode background-wait ceiling
# THROUGH its CLAUDE*/ANTHROPIC* scrub:
#   1. a caller-set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS survives (re-set after its own -u);
#   2. unset + Workflow in SC_TOOLS -> defaulted to 0 (a Workflow child must not die at 600 s);
#   3. unset + no Workflow -> not present (the scrub adds nothing it was not asked for);
#   4. the scrub itself still unsets a host CLAUDE_* var (the auth-isolation contract holds).
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
# shellcheck source=../scripts/lib/sidecar_common.sh
. "$LIB"
fail=0
joined() { local IFS=' '; printf '%s' "${SC_SCRUB[*]:-}"; }

# --- case 1: caller value survives, and is re-set AFTER its own unset
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 SC_TOOLS="Read,Glob"
sc_scrub_env; s="$(joined)"
if [[ "$s" == *"-u CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"*"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0"* ]]; then
  echo "OK   passthrough: caller ceiling 0 re-set after its unset"
else echo "FAIL passthrough: $s"; fail=1; fi

# --- case 2: unset + Workflow allowed -> defaulted to 0
unset CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS; export SC_TOOLS="Workflow,Read,Glob,Grep"
sc_scrub_env; s="$(joined)"
if [[ "$s" == *"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0"* ]]; then
  echo "OK   default: Workflow in SC_TOOLS lifts the ceiling"
else echo "FAIL default: $s"; fail=1; fi

# --- case 3: unset + no Workflow -> absent
export SC_TOOLS="Read,Glob"
sc_scrub_env; s="$(joined)"
if [[ "$s" != *"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="* ]]; then
  echo "OK   absent: no Workflow, no ceiling override"
else echo "FAIL absent: $s"; fail=1; fi

# --- case 4: the scrub still unsets a host var
export CLAUDE_CODE_ENTRYPOINT=claude-desktop
sc_scrub_env; s="$(joined)"
if [[ "$s" == *"-u CLAUDE_CODE_ENTRYPOINT"* ]]; then
  echo "OK   scrub: host CLAUDE_CODE_ENTRYPOINT still unset"
else echo "FAIL scrub: $s"; fail=1; fi

[ "$fail" = 0 ] && echo "PASS test_sidecar_scrub_passthrough" || echo "FAIL test_sidecar_scrub_passthrough"
exit "$fail"
