#!/usr/bin/env bash
# anthropic_sidecar.sh — run headless Claude Code against ANTHROPIC on one Anthropic PLAN, as a child
# of any session. Two callers need it: a foreign session (codex, deepseek, opencode) reaching claude-*,
# and an Anthropic session paying with ANOTHER Anthropic plan than its own (the session's own plan
# runs native through Workflow/Agent). Workflow/Agent never cross transports or accounts, so a
# sidecar is the only route either way.
#
# WHICH PLAN PAYS is the launcher path that was invoked: this script is the `anthropic` plan's
# launcher, and each further plan's launcher (anthropic_secondary_sidecar.sh) sources this file.
# The registry maps the invoked path back to its plan (`model_registry.py plan-for-launcher`); the
# caller's CLAUDE_CONFIG_DIR and an inherited SC_PLAN never choose it. The plan's `configDir` is its
# login: the child runs with CLAUDE_CONFIG_DIR set to it, or unset for `~/.claude` (with the variable
# set, Claude Code reads `.claude.json` from inside the dir instead of `~/.claude.json`). Before a
# launch on a non-default login, sync_plan_config.py brings that login's user config in step with
# `~/.claude`, and a failed sync fails the launch.
#
# A plan may instead be authenticated by a KEY, declared as `credentialVar` plus `credentialFile` in
# the registry (the Console-credit plans, anthropic-console and anthropic-console-secondary). Such a
# plan has NO `.credentials.json` to find -- its configDir is a dedicated, login-free directory,
# created on demand, that exists only so the child's CLAUDE_CONFIG_DIR cannot name a host login --
# and the credential is read out of the named file the way deepseek_sidecar.sh reads DEEPSEEK_API_KEY.
# `plan-for-launcher` prints both fields (empty for a login plan), so the registry stays the one place
# either is declared and this script never hardcodes a variable name or a file path.
#
# THIS LAUNCHER DELIBERATELY INVERTS THE SCRUB'S ORIGINAL INTENT — read this before editing.
# sc_scrub_env() was written to stop a child inheriting the host's subscription OAuth, because a
# child pointed at a THIRD-PARTY endpoint that authenticates with host OAuth sends the wrong
# credential and gets a 401 whose text blames the key you did supply
# (gotcha_claude_code_entrypoint_leaks_host_auth_to_child). Here the plan's Anthropic credential
# is exactly what the child SHOULD use. For a LOGIN plan that still works with the scrub in place,
# and the scrub is still run, because the credential is a FILE on disk
# (`<configDir>/.credentials.json`) — an env scrub cannot touch it. For a KEY plan the credential is
# the env variable ANTHROPIC_API_KEY, so the scrub's `-u ANTHROPIC_API_KEY` is dropped for that plan
# and the key is EXPORTED inside the subshell that runs the child. It is never passed as an `env
# NAME=value` argument: those are `env`'s own argv, and argv is readable by every process on the box.
# Either way the child gets a clean environment AND the right auth, and no ANTHROPIC_BASE_URL /
# ANTHROPIC_AUTH_TOKEN is set: unset is what makes the CLI talk to Anthropic directly.
#
# The FLAG SURFACE, validation, gate ladder, entrypoint scrub and run-record live in
# lib/sidecar_common.sh, shared with every other provider's launcher. This file holds only what
# is Anthropic-specific: the plan's credential, no endpoint override, a free structural --check and
# an opt-in --check --deep that spends one turn to test live authentication.
#
# CACHE TTL: the child's prompt cache is written at 5 minutes (CLAUDE_CODE_PROMPT_CACHE_TTL=5m), not the
# subscription default of 1 hour. Only this launcher sets it: codex-proxy, deepseek and opencode
# children talk to a non-Anthropic backend whose cache the variable does not govern.
# The cache-lifetime memory note holds the replay and the reversal test.
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
#   -e  effort level        the rungs `model_registry.py effort-values <model>` prints. A rung the row's
#       registry `effort.avoid` lists exits 2 outside a comparison: only `sidecar_fanout.py --compare` or
#       a benchmark campaign sets SC_COMPARISON (sc_validate_effort); reference/model_ladder_evidence.md owns the rule.
#
# Every other flag behaves exactly as documented in lib/sidecar_common.sh and
# reference/sidecar_dispatch.md — one recipe, every transport.
#
# COST: this spends the invoked PLAN's quota. From a session on that same plan it is the wrong
# shape — use Workflow/Agent, which need no child process. From a session on another plan it moves
# the job onto this plan's allowance, the route to take when the ladder's selection line chose this plan.
#
# Exit codes: the shared table in reference/sidecar_runs.md.

set -uo pipefail

SC_TRANSPORT="anthropic"   # the route, until the plan resolves below; the lib reads it at source time
# shellcheck source=lib/sidecar_common.sh
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
. "$SC_LAUNCHER_DIR/lib/sidecar_common.sh"
sc_reexec_snapshot "$@"   # run from a snapshot copy; see lib

# The invoked launcher path names the plan. A snapshot's own path names nothing, so the lib carries
# the invoked path through the re-exec as SC_INVOKED_LAUNCHER, honored only in this launcher's own
# snapshot; a live run (no snapshot) reads $0.
AC_LAUNCHER="${SC_INVOKED_LAUNCHER:-$(cd "$(dirname "$0")" && pwd)/$(basename "$0")}"
_ac_plan_line="$(python3 "$SC_REGISTRY_CLI" plan-for-launcher "$AC_LAUNCHER" 2>&1)" || {
  sc_die 2 "[sidecar] no registry plan names the launcher $AC_LAUNCHER${_ac_plan_line:+ (${_ac_plan_line%%$'\n'*})}; set its transport's \`launcher\` in .claude/reference/external_models.json"
}
IFS=$'\t' read -r SC_PLAN AC_CONFIG_DIR AC_DEFAULT_LOGIN AC_CRED_VAR AC_CRED_FILE <<< "$_ac_plan_line"
SC_TRANSPORT="$SC_PLAN"   # markers, capacity gate, ledger and record all key on the paying plan
AC_CRED="$AC_CONFIG_DIR/.credentials.json"
# The calling session's plan, read before this process drops CLAUDE_CONFIG_DIR.
SC_SEAT_PLAN="$(python3 "$SC_ROOT/hooks/_session_transport.py" --plan 2>/dev/null)"
# This process resolves tools and MCP servers from the default login for every plan: the child gets
# its servers through an explicit --strict-mcp-config, so a delegate has the same servers on either
# account even when the second login registered none. Only the child runs on the plan's login.
unset CLAUDE_CONFIG_DIR SC_MCP_CONFIG_DIR
if [ "$AC_DEFAULT_LOGIN" = 1 ]; then
  AC_CHILD_CONFIG_DIR=""
else
  AC_CHILD_CONFIG_DIR="$AC_CONFIG_DIR"
fi
# A key plan's configDir is not a login: nothing creates it by logging in, and both the child and
# sync_plan_config.py need it there. Only a launch and a deep probe create it — the structural
# --check, which SessionStart runs for every launcher, changes nothing on disk.
ac_ensure_config_dir() {
  [ -n "$AC_CRED_VAR" ] && mkdir -p "$AC_CONFIG_DIR"
  return 0
}

# The key a Console plan authenticates with, read out of the registry-named file the way
# deepseek_sidecar.sh reads DEEPSEEK_API_KEY: the CMD-format env file the ai-worker MCP server uses,
# CR-stripped and unquoted. The VALUE is never printed, logged or put on a command line.
ac_read_key() {
  grep -iE "^[[:space:]]*set[[:space:]]+${AC_CRED_VAR}=" "$AC_CRED_FILE" \
    | head -1 | sed -E "s/^[[:space:]]*set[[:space:]]+${AC_CRED_VAR}=//" \
    | tr -d '\r' | sed -E 's/^"(.*)"$/\1/'
}

# Sets AC_KEY, or dies 3 naming the VARIABLE (never a value) with the same placeholder set
# deepseek_sidecar.sh refuses. Both the --check branch and the launch path call it.
ac_require_key() {
  [ -f "$AC_CRED_FILE" ] || sc_die 3 "credential file missing: $AC_CRED_FILE"
  AC_KEY="$(ac_read_key)"
  case "$AC_KEY" in
    ""|"<redacted>"|"your-key-here"|"sk-xxx"*)
      sc_die 3 "$AC_CRED_VAR not populated in $AC_CRED_FILE" ;;
  esac
}

SC_MODEL="sonnet"   # resolved through the registry; alias or full id both fine

# sc_scrub_env unsets every ANTHROPIC* variable the caller carried, ANTHROPIC_API_KEY included.
# That is what a LOGIN plan wants (AC_BLANK_KEY re-blanks the variable after `env -u`, so a stray
# parent value cannot win the auth race) and exactly wrong for a KEY plan, whose credential IS that
# variable: the unset would delete it on the way in. Drop only that one pair.
ac_unscrub_key() {
  local -a out=(); local i=0
  while [ $i -lt ${#SC_SCRUB[@]} ]; do
    if [ "${SC_SCRUB[$i]}" = "-u" ] && [ "${SC_SCRUB[$((i + 1))]:-}" = "ANTHROPIC_API_KEY" ]; then
      i=$((i + 2)); continue
    fi
    out+=("${SC_SCRUB[$i]}"); i=$((i + 1))
  done
  SC_SCRUB=(${out[@]+"${out[@]}"})
}

ac_scrub() {
  sc_scrub_env
  AC_BLANK_KEY=()
  if [ -n "${AC_CRED_VAR:-}" ]; then ac_unscrub_key; else AC_BLANK_KEY=(ANTHROPIC_API_KEY=); fi
  return 0
}

# The key rides the CHILD'S ENVIRONMENT, never `env`'s argv: `env NAME=value` puts the value on the
# command line, where `ps` and any sibling process can read it. Called inside the subshell that runs
# the child, so the launcher's own environment never carries it either.
ac_export_key() {
  [ -n "${AC_CRED_VAR:-}" ] || return 0
  export ANTHROPIC_API_KEY="$AC_KEY"
}

ac_resolve_window() {
  local config
  config="$(SIDECAR_MAX_OUTPUT_TOKENS="${SC_MAX_OUTPUT:-}" python3 "$SC_REGISTRY_CLI" anthropic-window "$1" ${SC_LONG_CONTEXT:+--max} ${SC_SHAPE:+--shape "$SC_SHAPE"})" || sc_die 2 "cannot resolve Anthropic client window for $1"
  IFS='|' read -r AC_CLIENT_MODEL AC_AUTO_COMPACT_WINDOW AC_MAX_CONTEXT_TOKENS SC_CONTEXT_TOKENS SC_CONTEXT_WINDOW_SOURCE <<< "$(
    printf '%s' "$config" | python3 -c 'import json,sys; c=json.load(sys.stdin); print("|".join(str(c[k]) if c[k] is not None else "" for k in ("model","autoCompactWindow","maxContextTokens","contextWindow","contextWindowSource")))')"
}

# A real one-turn dispatch for --check --deep only. The ordinary structural check runs at
# SessionStart and cannot claim to have tested authentication.
ac_probe_child() {
  local bin; bin="$(sc_claude_bin)" || return 4
  SC_MAX_OUTPUT="$(sc_max_output)"
  ac_resolve_window "$1"
  ac_scrub
  # Run in the config dir, outside the repo: from the repo root the child loads every project hook,
  # whose SessionStart launcher health checks dwarf a one-turn probe.
  ( ac_ensure_config_dir; ac_export_key; cd "$AC_CONFIG_DIR" && env ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ${AC_BLANK_KEY[@]+"${AC_BLANK_KEY[@]}"} \
    ${AC_CHILD_CONFIG_DIR:+CLAUDE_CONFIG_DIR="$AC_CHILD_CONFIG_DIR"} \
    ${AC_AUTO_COMPACT_WINDOW:+CLAUDE_CODE_AUTO_COMPACT_WINDOW="$AC_AUTO_COMPACT_WINDOW"} \
    ${AC_MAX_CONTEXT_TOKENS:+CLAUDE_CODE_MAX_CONTEXT_TOKENS="$AC_MAX_CONTEXT_TOKENS"} \
    ${SC_MAX_OUTPUT:+CLAUDE_CODE_MAX_OUTPUT_TOKENS="$SC_MAX_OUTPUT"} \
    "$bin" -p "Reply with the single word OK." \
      --model "$AC_CLIENT_MODEL" \
      --output-format stream-json --verbose \
      --allowedTools "" \
      --disallowedTools "Task,Agent" \
      --no-session-persistence </dev/null 2>/dev/null )
}

if [ "${1:-}" = "--check" ]; then
  sc_check_model_override "$@"
  sc_claude_bin >/dev/null || { echo "UNAVAILABLE (claude CLI not found; set CLAUDE_BIN)"; exit 4; }

  _check_reg="$(python3 "$SC_REGISTRY_CLI" sidecar-fields "$SC_MODEL" --plan "$SC_PLAN" 2>&1)" || {
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

  if [ -n "$AC_CRED_VAR" ]; then
    # Named, never valued: this line is published into the session's context at SessionStart.
    AC_CRED_DESC="key:$AC_CRED_VAR"
    [ -f "$AC_CRED_FILE" ] || {
      echo "UNAVAILABLE ($AC_CRED_VAR: credential file missing: $AC_CRED_FILE)"; exit 3; }
    AC_KEY="$(ac_read_key)"
    case "$AC_KEY" in
      ""|"<redacted>"|"your-key-here"|"sk-xxx"*)
        echo "UNAVAILABLE ($AC_CRED_VAR not populated in $AC_CRED_FILE)"; exit 3 ;;
    esac
  else
    AC_CRED_DESC="$AC_CRED"
    if [ ! -f "$AC_CRED" ]; then
      echo "UNAVAILABLE (no $SC_PLAN credential at $AC_CRED — log in once with \`claude\` under that config dir)"
      exit 3
    fi
  fi

  # `--check` is called for EVERY registered launcher at SessionStart, so the default must stay
  # structural and free. `--check --deep` adds the real one-turn dispatch: file-exists is not
  # proof of auth (a revoked credential is a file like any other), but paying a live child on
  # every session start to learn that is the wrong trade. Deep is for workstation_setup and for
  # diagnosing a transport that just failed.
  sc_check_gates
  case " $* " in
    *" --deep "*)
      _check_out="$(ac_probe_child "${_check_reg%%|*}")"; _check_rc=$?
      if printf '%s' "${_check_out:-}" | grep -q '"is_error" *: *true\|"api_error_status" *: *4'; then
        echo "UNAVAILABLE (credential present at $AC_CRED_DESC but the probe dispatch failed — re-login)"
        exit 3
      fi
      [ -n "${_check_out:-}" ] || {
        echo "UNAVAILABLE (probe dispatch returned nothing; claude CLI or auth is broken)"; exit 3; }
      # A Console plan's deep probe SPENT its credit, so its one turn is priced through the same
      # writer an ordinary launch uses, into the one machine-global ledger (D5). A login plan's
      # probe stays what it was: it spends a plan that already records its own sessions.
      # The record that writer also produces is not this run's to keep, so it goes to a scratch
      # path and is removed.
      if [ -n "$AC_CRED_VAR" ]; then
        _check_probe_dir="$(mktemp -d 2>/dev/null)" \
          && { _check_saved_record="$SC_RECORD"; SC_RECORD="$_check_probe_dir/probe.record.json"
               SC_LABEL="${SC_LABEL:-console-credit-probe}"
               sc_write_record "$_check_out" "$_check_rc"
               SC_RECORD="$_check_saved_record"; rm -rf "$_check_probe_dir"; }
      fi
      _check_report="$(printf '%s' "$_check_out" | python3 -c 'import json,sys
rows=[json.loads(l) for l in sys.stdin if l.strip()]
usage=next((r.get("modelUsage", {}) for r in reversed(rows) if r.get("type")=="result"), {})
model=next(iter(usage), next((r.get("model") for r in rows if r.get("subtype")=="init"), "unknown"))
window=(usage.get(model) or {}).get("contextWindow")
print("servedModel=%s reportedWindow=%s" % (model, window if window else "unknown"))')"
      echo "OK (model=${_check_reg%%|*} $_check_report endpoint=anthropic-direct cred=$AC_CRED_DESC probe=dispatched)"
      ;;
    *)
      echo "OK (model=${_check_reg%%|*} endpoint=anthropic-direct cred=$AC_CRED_DESC probe=skipped; --deep to dispatch one)"
      ;;
  esac
  exit 0
fi

sc_parse_flags "$@"
shift "$SC_SHIFT"
[ "${1:-}" = "--" ] && shift
sc_read_prompt "$@"

# A Console plan's rows always land in the ONE default ledger, whatever -L or SIDECAR_LEDGER_PATH
# said (D5): the credit probe measures spend by offsetting that file against the registry's grant, so
# a row written anywhere else would understate the credit without saying so. A login plan keeps the
# ordinary ledger contract. The launcher that resolves this plan unsets SIDECAR_LEDGER_PATH before
# lib/sidecar_common.sh reads it, so SC_LEDGER_DEFAULT is the HOME path here.
[ -n "$AC_CRED_VAR" ] && SC_LEDGER="__default__"

SC_CLAUDE="$(sc_claude_bin)" || sc_die 4 "claude CLI not found (set CLAUDE_BIN)"

sc_resolve_model
sc_gate_availability
sc_validate_common

# Credential BEFORE the band gate: a missing key is a configuration fault, and reporting a band
# refusal for a machine that could never have dispatched sends the reader to the wrong fix.
if [ -n "$AC_CRED_VAR" ]; then
  ac_require_key
elif [ ! -f "$AC_CRED" ]; then
  sc_die 3 "no $SC_PLAN credential at $AC_CRED — log in once with \`claude\` under that config dir"
fi
if [ -n "$AC_CHILD_CONFIG_DIR" ]; then
  ac_ensure_config_dir
  python3 "$SC_ROOT/scripts/sync_plan_config.py" --plan "$SC_PLAN" --files-only >&2 || {
    sc_die 2 "[sidecar] REFUSING: $SC_PLAN's user config could not be synced with ~/.claude (sync_plan_config.py output above); a child on a stale config would not behave like one on the primary login."
  }
fi

sc_gate_band
sc_gate_capacity        # live Anthropic OAuth usage for this plan, normalized by provider_capacity.py
sc_gate_price_window    # no-op without a registry pricingSchedule; present so the ladder is uniform
sc_build_disclosure
sc_validate_effort

SC_MAX_OUTPUT="$(sc_max_output)"
ac_resolve_window "$SC_MODEL"

EFFORT_ARGS=()
[ -n "$SC_EFFORT" ] && [ "$SC_EFFORT" != none ] && EFFORT_ARGS=(--effort "$SC_EFFORT")

VERBOSE_ARGS=()
[ "$SC_FORMAT" = "stream-json" ] && VERBOSE_ARGS=(--verbose)

EXTRA_ARGS=()
[ "$SC_PERSIST" -eq 1 ] || EXTRA_ARGS+=(--no-session-persistence)
[ -n "$SC_RESUME" ] && EXTRA_ARGS+=(--resume "$SC_RESUME")
[ -n "$SC_PERM_MODE" ] && EXTRA_ARGS+=(--permission-mode "$SC_PERM_MODE")
[ -n "$SC_SCHEMA_FILE" ] && EXTRA_ARGS+=(--json-schema "$(cat "$SC_SCHEMA_FILE")")
SC_SETTINGS_JSON="$(sc_settings_with_bench_guard "${SC_SETTINGS_JSON:-}")"
[ -n "$SC_SETTINGS_JSON" ] && EXTRA_ARGS+=(--settings "$SC_SETTINGS_JSON")

ac_scrub

# NO ANTHROPIC_BASE_URL and NO ANTHROPIC_AUTH_TOKEN, on purpose: unset is what routes the child
# to Anthropic. ANTHROPIC_API_KEY is blanked so a stray parent value cannot win the auth race and
# bill an API account instead of the subscription — or, on a key plan, is EXPORTED inside the
# subshell below so it never becomes one of `env`'s arguments. CLAUDE_CONFIG_DIR is set after the
# scrub for a non-default login, so the child authenticates as the paying plan. CLAUDE_CODE_TRANSPORT
# tells the CHILD which plan it is running on, so its own rails and guard resolve correctly
# (hooks/_session_transport.py).
run_claude() {
  cd "$SC_RUN_CWD" && ( ac_export_key; env \
    ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    CLAUDE_CODE_PROMPT_CACHE_TTL=5m \
    ${AC_BLANK_KEY[@]+"${AC_BLANK_KEY[@]}"} \
    ${AC_CHILD_CONFIG_DIR:+CLAUDE_CONFIG_DIR="$AC_CHILD_CONFIG_DIR"} \
    ANTHROPIC_SMALL_FAST_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SUBAGENT_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" \
    CLAUDE_CODE_TRANSPORT="$SC_TRANSPORT" \
    ${AC_AUTO_COMPACT_WINDOW:+CLAUDE_CODE_AUTO_COMPACT_WINDOW="$AC_AUTO_COMPACT_WINDOW"} \
    ${AC_MAX_CONTEXT_TOKENS:+CLAUDE_CODE_MAX_CONTEXT_TOKENS="$AC_MAX_CONTEXT_TOKENS"} \
    ${SC_MAX_OUTPUT:+CLAUDE_CODE_MAX_OUTPUT_TOKENS="$SC_MAX_OUTPUT"} \
    ${SC_SHAPE:+CLAUDE_CODE_SIDECAR_SHAPE="$SC_SHAPE"} \
    ${SC_TIMEOUT:+timeout} ${SC_TIMEOUT:+"$SC_TIMEOUT"} \
    "$SC_CLAUDE" -p \
      --model "$AC_CLIENT_MODEL" \
      ${EFFORT_ARGS[@]+"${EFFORT_ARGS[@]}"} \
      --output-format "$SC_FORMAT" \
      ${VERBOSE_ARGS[@]+"${VERBOSE_ARGS[@]}"} \
      --allowedTools "${SC_ALLOWED_TOOLS:-$SC_TOOLS}" \
      --tools "$(sc_builtin_tools "${SC_RESOLVED_BUILTINS:-$SC_TOOLS}")" \
      --strict-mcp-config --mcp-config "$(sc_mcp_config "${SC_RESOLVED_TOOL_SPEC:-$SC_TOOLS}" "${SC_MCP_CONFIG_DIR:-}")" \
      ${SC_DISALLOWED:+--disallowedTools "$SC_DISALLOWED"} \
      ${SC_APPEND_ARGS[@]+"${SC_APPEND_ARGS[@]}"} \
      ${SC_MAX_TURNS:+--max-turns "$SC_MAX_TURNS"} \
      ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
      ${SC_RESUME_SID:+--resume} ${SC_RESUME_SID:+"$SC_RESUME_SID"} \
      --add-dir "$SC_WORKDIR" \
      ${SC_ADD_DIRS[@]+"${SC_ADD_DIRS[@]}"} \
      <<< "$SC_PROMPT" )
  # Prompt rides STDIN, never argv: Windows caps a command line near 32 KB, and a larger brief
  # died at spawn with `Argument list too long` (exit 126), as codex_proxy_sidecar.sh records.
}

[ -n "$SC_PROGRESS" ] && : > "$SC_PROGRESS"
sc_run_watched run_claude "$SC_PROGRESS"   # stall watchdog on the -P stream; sets OUTPUT and rc
sc_resume_loop run_claude "$SC_PROGRESS"
printf '%s\n' "$OUTPUT"
[ $rc -eq 124 ] && echo "sidecar timed out after ${SC_TIMEOUT}s" >&2

sc_write_record "$OUTPUT" "$rc"

# R1: the endpoint can refuse a spent credit while the ledger's own arithmetic still shows dollars
# left, and a KEY plan's child result is the only surface that refusal appears on. The probe owns
# both the scan (CHILD_EXHAUSTED_TEXT) and the marker's cycle, so it gets the output as-is and
# decides; a clean run answers "no credit-exhausted error ... nothing marked" and writes nothing.
#
# KEY PLANS ONLY: a login plan's spend is its own account's and there is no credit to mark, so this
# block is unreachable for one and its argv is untouched. The probe's message goes to STDERR -- stdout
# is the child's stream, which every caller parses -- and a probe fault cannot change the exit status,
# because nothing here reassigns `rc` and the exit below is explicit.
if [ -n "$AC_CRED_VAR" ]; then
  PROVIDER_CAPACITY_TRANSPORT="$SC_PLAN" python3 "$SC_ROOT/scripts/console_credit_probe.py" \
    --child-result - <<<"${OUTPUT:-}" >&2
fi

# A 401 on this route means the paying plan's own credential is stale — a login for a login plan, a
# key in the registry-named file for a Console plan. There is no third-party key to suspect, which is
# the opposite of the deepseek case and worth saying so nobody hunts one.
if [ "$rc" -ne 0 ] && printf '%s' "${OUTPUT:-}" | grep -q '"api_error_status" *: *401'; then
  if [ -n "$AC_CRED_VAR" ]; then
    cat >&2 <<DIAG
[sidecar] 401 from Anthropic on plan $SC_PLAN.
  This launch authenticates with \$$AC_CRED_VAR from $AC_CRED_FILE; no login is involved. Check that
  the key in that file is still valid (Console → API keys), then re-run
  \`$(basename "$AC_LAUNCHER") --check\`. A revoked key is a 401, not an empty response.
DIAG
  else
    cat >&2 <<DIAG
[sidecar] 401 from Anthropic on plan $SC_PLAN.
  This launch uses that plan's own login ($AC_CRED); no third-party key is involved. Run
  \`claude\` interactively once${AC_CHILD_CONFIG_DIR:+ with CLAUDE_CONFIG_DIR=$AC_CHILD_CONFIG_DIR} to refresh the
  login, then re-run \`$(basename "$AC_LAUNCHER") --check\`.
DIAG
  fi
fi

exit $rc
