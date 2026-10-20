#!/usr/bin/env bash
# Proof that sc_scrub_env (lib/sidecar_common.sh) passes the print-mode background-wait ceiling
# THROUGH its CLAUDE*/ANTHROPIC* scrub:
#   1. a caller-set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS survives (re-set after its own -u);
#   2. unset + Workflow in SC_TOOLS -> still absent (no sidecar child holds Workflow: tools/tool_profiles.py
#      refuses it, so no child has a background task to wait on);
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
export CLAUDE_PROOF_HOST_VAR=host  # a runner with no CLAUDE*/ANTHROPIC* host variable leaves the scrub empty
joined() { local IFS=' '; printf '%s' "${SC_SCRUB[*]:-}"; }

# --- case 1: caller value survives, and is re-set AFTER its own unset
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 SC_TOOLS="Read,Glob"
sc_scrub_env; s="$(joined)"
if [[ "$s" == *"-u CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"*"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0"* ]]; then
  echo "OK   passthrough: caller ceiling 0 re-set after its unset"
else echo "FAIL passthrough: $s"; fail=1; fi

# --- case 2: unset + Workflow named -> still absent (the resolver refuses Workflow upstream)
unset CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS; export SC_TOOLS="Workflow,Read,Glob,Grep"
sc_scrub_env; s="$(joined)"
if [[ -n "$s" && "$s" != *"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="* ]]; then
  echo "OK   no default: Workflow in SC_TOOLS adds no ceiling"
else echo "FAIL no default: $s"; fail=1; fi

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

# --- case 5: the parent-session tag rides through the scrub, so tools/reap.py can prove a detached
#     child is this session's after CLAUDE_CODE_SESSION_ID is unset and its wrapper has exited
export CLAUDE_CODE_SESSION_ID=parent-sid; SC_PARENT_SESSION_ID=parent-sid
sc_scrub_env; s="$(joined)"
if [[ "$s" == *"-u CLAUDE_CODE_SESSION_ID"*"SIDECAR_PARENT_SESSION_ID=parent-sid"* ]]; then
  echo "OK   parent tag: SIDECAR_PARENT_SESSION_ID set after the session id's unset"
else echo "FAIL parent tag: $s"; fail=1; fi

# --- case 6: no parent session -> no tag
SC_PARENT_SESSION_ID=""
sc_scrub_env; s="$(joined)"
if [[ "$s" != *"SIDECAR_PARENT_SESSION_ID="* ]]; then
  echo "OK   no parent: no tag"
else echo "FAIL no parent: $s"; fail=1; fi

# --- case 7: the scrub is every backend's; the 5-minute cache TTL is the Anthropic launcher's alone,
#     so a codex-proxy, deepseek or opencode child never carries it
export CLAUDE_CODE_PROMPT_CACHE_TTL=1h
sc_scrub_env; s="$(joined)"
if [[ "$s" == *"-u CLAUDE_CODE_PROMPT_CACHE_TTL"* && "$s" != *"CLAUDE_CODE_PROMPT_CACHE_TTL="* ]]; then
  echo "OK   non-Anthropic: the scrub drops a host cache TTL and sets none"
else echo "FAIL non-Anthropic ttl: $s"; fail=1; fi
for l in codex_proxy deepseek opencode; do
  if git -C "$HERE" grep -q PROMPT_CACHE_TTL -- "../scripts/${l}_sidecar.sh"; then
    echo "FAIL ${l}_sidecar.sh sets a cache TTL"; fail=1
  else echo "OK   ${l}_sidecar.sh leaves the cache TTL unset"; fi
done

[ "$fail" = 0 ] && echo "PASS test_sidecar_scrub_passthrough" || echo "FAIL test_sidecar_scrub_passthrough"
exit "$fail"
