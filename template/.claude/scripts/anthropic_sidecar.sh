#!/usr/bin/env bash
# anthropic_sidecar.sh — run headless Claude Code against ANTHROPIC itself, as a child of any
# session. The mirror of the other launchers: those exist so an Anthropic session can reach a
# foreign model; this one exists so a foreign session (codex, deepseek, opencode) can reach
# claude-*. Workflow/Agent never cross transports, so a sidecar is the only route either way.
#
# THIS LAUNCHER DELIBERATELY INVERTS THE SCRUB'S ORIGINAL INTENT — read this before editing.
# sc_scrub_env() was written to stop a child inheriting the host's subscription OAuth, because a
# child pointed at a THIRD-PARTY endpoint that authenticates with host OAuth sends the wrong
# credential and gets a 401 whose text blames the key you did supply
# (gotcha_claude_code_entrypoint_leaks_host_auth_to_child). Here the host's Anthropic credential
# is exactly what the child SHOULD use. That still works with the scrub in place, and the scrub
# is still run, because the credential is a FILE on disk (the registry row's `credentialPath`,
# ~/.claude/.credentials.json) — an env scrub cannot touch it. So the child gets a clean
# environment AND the right auth, and no ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN is set:
# unset is what makes the CLI talk to Anthropic directly.
#
# The FLAG SURFACE, validation, gate ladder, entrypoint scrub and run-record live in
# lib/sidecar_common.sh, shared with every other provider's launcher. This file holds only what
# is Anthropic-specific: no endpoint override, no credential var, and a --check that spawns a
# real child rather than trusting that a file exists.
#
# Usage:
#   anthropic_sidecar.sh [-m MODEL] [-e EFFORT] [-D bare|pointer|full] [-G survey|review|author|any]
#                        [-t TOOLS] [-d DIR] [-R RECORD] [-l LABEL] -f prompt.md
#   anthropic_sidecar.sh --check [--deep]      structural by default; --deep dispatches one
#                                             real turn to prove the credential actually works
#
#   -m  model ALIAS or id (opus | sonnet | haiku | fable | claude-*; default: sonnet).
#       Resolved through .claude/reference/external_models.json. The registry states which
#       claude-* ids are DISPATCHABLE; reference/model_ladder_evidence.md (the transport's
#       roleSource) still owns which one to reach for and at what effort.
#   -e  effort level        low|medium|high|xhigh  (`max` is banned on Anthropic pins per
#       CLAUDE.md §Model Delegation — that ban is an Anthropic cost finding and DOES apply here,
#       unlike on deepseek where output is ~500x cheaper.)
#
# Every other flag behaves exactly as documented in lib/sidecar_common.sh and
# reference/sidecar_dispatch.md — one recipe, every transport.
#
# COST: this spends the SAME plan quota the calling session spends when it is an Anthropic
# session. It is not a way to get cheaper tokens; it is a way for a NON-Anthropic session to
# reach an Anthropic model. Dispatching it from an Anthropic session is usually the wrong shape —
# use Workflow/Agent, which need no child process.
#
# Exit codes: the shared table in reference/sidecar_dispatch.md.

set -uo pipefail

SC_TRANSPORT="anthropic"
# shellcheck source=lib/sidecar_common.sh
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
. "$SC_LAUNCHER_DIR/lib/sidecar_common.sh"
sc_reexec_snapshot "$@"   # run from a snapshot copy; see lib

SC_MODEL="sonnet"   # resolved through the registry; alias or full id both fine

# The registry row names the credential; this script never hardcodes the path, so moving the
# credential is a registry edit rather than a hunt through launchers.
ac_credential_path() {
  REGCLI_V="$SC_REGISTRY_CLI" python3 - <<'PY' 2>/dev/null
import importlib.util, os, sys
try:
    spec = importlib.util.spec_from_file_location("mr", os.environ["REGCLI_V"])
    mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
    cfg = mr.transport_meta("anthropic") or {}
    print(os.path.expanduser(cfg.get("credentialPath") or ""))
except Exception:
    print("")
PY
}

# A real one-turn dispatch, used by --check. File-exists is NOT proof of auth: a stale or
# revoked credential is a file like any other, and an availability probe that cannot tell them
# apart reports OK for a transport that fails on its first real dispatch.
ac_probe_child() {
  local bin; bin="$(sc_claude_bin)" || return 4
  sc_scrub_env
  env ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ANTHROPIC_API_KEY= \
    "$bin" -p "Reply with the single word OK." \
      --model "$1" \
      --output-format json \
      --allowedTools "" \
      --disallowedTools "Task,Agent" \
      --no-session-persistence 2>/dev/null
}

if [ "${1:-}" = "--check" ]; then
  sc_check_model_override "$@"
  sc_claude_bin >/dev/null || { echo "UNAVAILABLE (claude CLI not found; set CLAUDE_BIN)"; exit 4; }

  _check_reg="$(python3 "$SC_REGISTRY_CLI" sidecar-fields "$SC_MODEL" 2>&1)" || {
    echo "UNAVAILABLE (model registry unusable: ${_check_reg%%$'\n'*})"; exit 2; }

  _check_state="$(python3 "$SC_REGISTRY_CLI" transport-status "$SC_TRANSPORT" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("state", "available"))
except Exception:
    print("available")' 2>/dev/null)"
  if [ "$_check_state" = "unavailable" ]; then
    echo "UNAVAILABLE (excluded from the roster; see model_registry.py available)"
    exit 7
  fi

  _check_cred="$(ac_credential_path)"
  if [ -n "$_check_cred" ] && [ ! -f "$_check_cred" ]; then
    echo "UNAVAILABLE (no Anthropic credential at $_check_cred — run \`claude\` once to log in)"
    exit 3
  fi

  # `--check` is called for EVERY registered launcher at SessionStart, so the default must stay
  # structural and free. `--check --deep` adds the real one-turn dispatch: file-exists is not
  # proof of auth (a revoked credential is a file like any other), but paying a live child on
  # every session start to learn that is the wrong trade. Deep is for workstation_setup and for
  # diagnosing a transport that just failed.
  sc_check_gates
  case " $* " in
    *" --deep "*)
      _check_out="$(ac_probe_child "${_check_reg%%|*}")"
      if printf '%s' "${_check_out:-}" | grep -q '"is_error" *: *true\|"api_error_status" *: *4'; then
        echo "UNAVAILABLE (credential present at $_check_cred but the probe dispatch failed — re-login)"
        exit 3
      fi
      [ -n "${_check_out:-}" ] || {
        echo "UNAVAILABLE (probe dispatch returned nothing; claude CLI or auth is broken)"; exit 3; }
      echo "OK (model=${_check_reg%%|*} endpoint=anthropic-direct cred=$_check_cred probe=dispatched)"
      ;;
    *)
      echo "OK (model=${_check_reg%%|*} endpoint=anthropic-direct cred=$_check_cred probe=skipped; --deep to dispatch one)"
      ;;
  esac
  exit 0
fi

sc_parse_flags "$@"
shift "$SC_SHIFT"
[ "${1:-}" = "--" ] && shift
sc_read_prompt "$@"

SC_CLAUDE="$(sc_claude_bin)" || { echo "claude CLI not found (set CLAUDE_BIN)" >&2; exit 4; }

sc_resolve_model
sc_gate_availability
sc_validate_common

CRED_PATH="$(ac_credential_path)"
if [ -n "$CRED_PATH" ] && [ ! -f "$CRED_PATH" ]; then
  echo "no Anthropic credential at $CRED_PATH — run \`claude\` once to log in" >&2
  exit 3
fi

sc_gate_band
sc_gate_capacity        # live Anthropic OAuth usage, normalized by provider_capacity.py
sc_gate_price_window    # no-op without a registry pricingSchedule; present so the ladder is uniform
sc_build_disclosure
sc_validate_effort

case "$SC_EFFORT" in
  max) if [ "${SC_ALLOW_MAX:-}" = "1" ]; then
         echo "[sidecar] SC_ALLOW_MAX=1: effort max on an Anthropic pin ($SC_MODEL) — disclosed ceiling experiment" >&2
       else
         echo "effort 'max' is banned on Anthropic pins (CLAUDE.md §Model Delegation); use xhigh, or SC_ALLOW_MAX=1 for a disclosed ceiling experiment" >&2
         exit 2
       fi ;;
esac

SC_CONTEXT_TOKENS="$(python3 "$SC_REGISTRY_CLI" context-window "$SC_MODEL" 2>/dev/null)"

EFFORT_ARGS=()
[ -n "$SC_EFFORT" ] && EFFORT_ARGS=(--effort "$SC_EFFORT")

VERBOSE_ARGS=()
[ "$SC_FORMAT" = "stream-json" ] && VERBOSE_ARGS=(--verbose)

EXTRA_ARGS=()
[ "$SC_PERSIST" -eq 1 ] || EXTRA_ARGS+=(--no-session-persistence)
[ -n "$SC_RESUME" ] && EXTRA_ARGS+=(--resume "$SC_RESUME")
[ -n "$SC_PERM_MODE" ] && EXTRA_ARGS+=(--permission-mode "$SC_PERM_MODE")
[ -n "$SC_SCHEMA_FILE" ] && EXTRA_ARGS+=(--json-schema "$(cat "$SC_SCHEMA_FILE")")
SC_SETTINGS_JSON="$(sc_settings_with_bench_guard "")"
[ -n "$SC_SETTINGS_JSON" ] && EXTRA_ARGS+=(--settings "$SC_SETTINGS_JSON")

sc_scrub_env

# NO ANTHROPIC_BASE_URL and NO ANTHROPIC_AUTH_TOKEN, on purpose: unset is what routes the child
# to Anthropic. ANTHROPIC_API_KEY is blanked so a stray parent value cannot win the auth race and
# bill an API account instead of the subscription. CLAUDE_CODE_TRANSPORT tells the CHILD what it
# is running on, so its own rails and guard resolve correctly (hooks/_session_transport.py).
run_claude() {
  cd "$SC_RUN_CWD" && env \
    ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ANTHROPIC_API_KEY= \
    ANTHROPIC_SMALL_FAST_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SUBAGENT_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" \
    CLAUDE_CODE_TRANSPORT="$SC_TRANSPORT" \
    ${SC_CONTEXT_TOKENS:+CLAUDE_CODE_MAX_CONTEXT_TOKENS="$SC_CONTEXT_TOKENS"} \
    ${SC_SHAPE:+CLAUDE_CODE_SIDECAR_SHAPE="$SC_SHAPE"} \
    ${SC_TIMEOUT:+timeout} ${SC_TIMEOUT:+"$SC_TIMEOUT"} \
    "$SC_CLAUDE" -p "$SC_PROMPT" \
      --model "$SC_MODEL" \
      ${EFFORT_ARGS[@]+"${EFFORT_ARGS[@]}"} \
      --output-format "$SC_FORMAT" \
      ${VERBOSE_ARGS[@]+"${VERBOSE_ARGS[@]}"} \
      --allowedTools "$SC_TOOLS" \
      ${SC_DISALLOWED:+--disallowedTools "$SC_DISALLOWED"} \
      ${SC_APPEND_ARGS[@]+"${SC_APPEND_ARGS[@]}"} \
      ${SC_MAX_TURNS:+--max-turns "$SC_MAX_TURNS"} \
      ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
      ${SC_RESUME_SID:+--resume} ${SC_RESUME_SID:+"$SC_RESUME_SID"} \
      --add-dir "$SC_WORKDIR" \
      ${SC_ADD_DIRS[@]+"${SC_ADD_DIRS[@]}"}
}

[ -n "$SC_PROGRESS" ] && : > "$SC_PROGRESS"
sc_run_watched run_claude "$SC_PROGRESS"   # stall watchdog on the -P stream; sets OUTPUT and rc
sc_resume_loop run_claude "$SC_PROGRESS"
printf '%s\n' "$OUTPUT"
[ $rc -eq 124 ] && echo "sidecar timed out after ${SC_TIMEOUT}s" >&2

sc_write_record "$OUTPUT" "$rc"

# A 401 on THIS transport means the host login itself is stale — there is no third-party key to
# suspect, which is the opposite of the deepseek case and worth saying so nobody hunts one.
if [ "$rc" -ne 0 ] && printf '%s' "${OUTPUT:-}" | grep -q '"api_error_status" *: *401'; then
  cat >&2 <<DIAG
[sidecar] 401 from Anthropic.
  This transport uses the HOST's own login (${CRED_PATH:-~/.claude/.credentials.json}); no
  third-party key is involved. Run \`claude\` interactively once to refresh the session, then
  re-run \`anthropic_sidecar.sh --check\`.
DIAG
fi

exit $rc
