#!/usr/bin/env bash
# codex_proxy_sidecar.sh — THE codex-transport launcher (sole since 2026-08-20): run the REAL
# `claude` binary against a local proxy that translates Anthropic Messages traffic to the
# Codex backend, billed against the ChatGPT plan.
#
# The child is Claude Code, in this repo, so the full `.claude` harness applies — CLAUDE.md,
# skills, memory, hooks, rules, and the `-D` disclosure dial — with a GPT model behind the
# endpoint. Every dispatch shape routes here; how much context the child loads is the -D dial,
# not a different launcher. Pick -D and -G together as a SIDECAR AGENT TYPE — the recipe table
# is reference/sidecar_dispatch.md (scout=bare·survey, lens=pointer·survey, reviewer=full·review,
# author=full·author, judge=full·any). The former `codex exec` launcher stub was retired, then
# deleted (2026-09-14); git history at the pre-retirement tag point (fix(sidecar) 26393ee81)
# still has its header and implementation.
#
# Proxy: selected by `CCP_BIN` when set, otherwise the installed default. `ccp_probe.py continuation`
# enables WebSocket continuation only for v0.1.36+; older builds stay on full-history turns.
#
# EVERY DISPATCH STARTS ITS OWN PROXY on a fresh port and kills it on exit. That is not
# defensive hygiene, it is forced by where the knobs live: `CCP_CODEX_MODEL`,
# `CCP_CODEX_EFFORT` and `CCP_TRAFFIC_LOG` are read from the PROXY SERVER's environment at
# startup, not from the client's (measured 2026-08-20 — setting them on the `claude` child
# produced no capture at all, and no model force). A shared proxy therefore carries whichever
# pins its starter happened to set, so reusing one would silently serve a different model or
# effort than this dispatch asked for, and would attest nothing. One proxy per dispatch is
# what makes the pin and the attestation true.
#
# MODEL AND EFFORT ARE FORCED SERVER-SIDE, not through the child's own flags:
#   CCP_CODEX_MODEL   the child's `--model` reaches an alias surface that accepts `opus`,
#                     `sonnet`, `haiku`, `fable` and full `claude-*` ids as aliases onto GPT
#                     models. Every name resolves to SOMETHING, inverting DeepSeek's
#                     hard-error, so a mis-pinned arm cannot announce itself. Forcing at the
#                     proxy removes the alias surface from the decision entirely.
#   CCP_CODEX_EFFORT  vocabulary is identical to this harness's own `-e`, so there is no
#                     translation table. Passed instead of the child's `--effort` because it
#                     lands in the upstream request body, where it is verifiable per-run.
#
# Attestation is scoped to the CLI result's session ID and this launch time. Proxy captures
# record serialized upstream requests, not model self-reports or proof of generation acceptance.
# CCP_TRAFFIC_LOG stays on; captures contain prompts under
# ~/.local/state/claude-code-proxy/traffic and must be deleted periodically.
#
# DELEGATE RAILS. `-G <shape>` (any|survey|review|author) is honored here through the normal
# hook path, because the child IS Claude Code: the shape is exported as
# CLAUDE_CODE_SIDECAR_SHAPE and hooks/session_model_rails.py inlines .claude/guards/any.md plus
# the shape file on the child's SessionStart. Omitting -G yields `any`. Under `-D bare` or
# `-D pointer` no project hook fires, so lib/sidecar_common.sh appends the same assembled text
# itself. That is a delivery difference, not a content one — both routes call
# tools/guard_text.py.
#
# Exit codes: see lib/sidecar_common.sh. Exit 8 (provider quota-band ceiling) is live here.

set -uo pipefail

SC_TRANSPORT="codex"
# shellcheck source=lib/sidecar_common.sh
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
. "$SC_LAUNCHER_DIR/lib/sidecar_common.sh"
sc_reexec_snapshot "$@"   # run from a snapshot copy; see lib

SC_MODEL="luna"
CCP_AUTH="$HOME/.config/claude-code-proxy/codex/auth.json"
CCP_PROBE="$SC_ROOT/scripts/lib/ccp_probe.py"
CCP_LOG="${CCP_LOG:-$HOME/.local/state/claude-code-proxy/sidecar-serve.log}"
CCP_PORT=""

# The proxy ships as a plain exe extracted from a GitHub release — there is no brew on this
# machine and no npm shim, so the install path is the primary lookup and PATH the fallback.
ccp_bin() {
  if [ -n "${CCP_BIN:-}" ]; then printf '%s' "$CCP_BIN"; return 0; fi
  local installed="$HOME/AppData/Local/claude-code-proxy/claude-code-proxy.exe"
  [ -x "$installed" ] && { printf '%s' "$installed"; return 0; }
  command -v claude-code-proxy >/dev/null 2>&1 && { printf '%s' "$(command -v claude-code-proxy)"; return 0; }
  return 1
}

# Start this dispatch's own proxy, carrying its pins. Bounded wait, never an indefinite hang:
# a proxy that cannot bind fails the same way forever, and waiting longer only delays the
# report.
ccp_start() {
  local ccp
  ccp="$(ccp_bin)" || { echo "claude-code-proxy not found (set CCP_BIN)" >&2; return 4; }
  CCP_PORT="$(python3 "$CCP_PROBE" freeport)"
  mkdir -p "$(dirname "$CCP_LOG")"
  # WebSocket continuation and native compaction are enabled only on tested builds.
  local continuation server_compaction probe_rc
  continuation="$(python3 "$CCP_PROBE" continuation "$ccp")" || {
    probe_rc=$?
    echo "[proxy-sidecar] continuation capability probe failed for $ccp (exit $probe_rc)." >&2
    return 4
  }
  [ "$continuation" = 1 ] || { continuation=0; echo "[proxy-sidecar] $ccp predates 0.1.36: continuation OFF, one WebSocket per turn (set CCP_BIN to a 0.1.36+ build)" >&2; }
  server_compaction="$(python3 "$CCP_PROBE" server-compaction "$ccp")" || {
    probe_rc=$?
    echo "[proxy-sidecar] server-compaction capability probe failed for $ccp (exit $probe_rc)." >&2
    return 4
  }
  [ "$server_compaction" = 1 ] || server_compaction=0
  echo "[proxy-sidecar] starting claude-code-proxy on :$CCP_PORT (model=$SC_MODEL effort=${SC_EFFORT:-unset} continuation=$continuation server_compaction=$server_compaction, log: $CCP_LOG)" >&2
  # The pins go in the SERVER's env; see the header. CCP_TRAFFIC_LOG is unconditional because
  # the capture proves the model and effort encoded in the upstream request, not acceptance.
  env CCP_CODEX_MODEL="$SC_MODEL" \
      ${SC_EFFORT:+CCP_CODEX_EFFORT="$SC_EFFORT"} \
      CCP_CODEX_PREVIOUS_RESPONSE_ID="$continuation" \
      CCP_CODEX_SERVER_COMPACTION="$server_compaction" \
      CCP_TRAFFIC_LOG=1 \
      nohup "$ccp" serve --no-monitor --port "$CCP_PORT" >>"$CCP_LOG" 2>&1 &
  CCP_PID=$!
  sc_on_exit 'kill "$CCP_PID" 2>/dev/null || :'
  python3 "$CCP_PROBE" wait "$CCP_PORT" 25 || {
    { echo "[proxy-sidecar] proxy did not become healthy on :$CCP_PORT within 25s."
      echo "  Last lines of $CCP_LOG:"
      tail -n 15 "$CCP_LOG" 2>/dev/null | sed 's/^/    /'
    } >&2
    return 4
  }
  [ "${SIDECAR_NO_SHIM:-0}" = 1 ] && return 0
  shim_start
}

# The text-only shim sits between the child and the proxy (scripts/lib/text_only_shim.py). Claude Code's
# auto-compaction asks for the summary with "Respond with TEXT ONLY" and still attaches every tool; a model that
# answers with a tool call kills the run ("summarization produced empty response" — measured 2026-09-09,
# gpt-5.6-luna at effort low, 3 of 3). The shim forwards that request with no tools and re-issues an empty
# answer once, for any model. SIDECAR_NO_SHIM=1 bypasses it (diagnosis only).
shim_start() {
  SHIM_PORT="$(python3 "$CCP_PROBE" freeport)"
  nohup python3 "$SC_ROOT/scripts/lib/text_only_shim.py" --listen "$SHIM_PORT" --upstream "$CCP_PORT" --log "$CCP_LOG.shim" >>"$CCP_LOG" 2>&1 &
  SHIM_PID=$!
  sc_on_exit 'kill "$SHIM_PID" 2>/dev/null || :'
  python3 "$CCP_PROBE" wait "$SHIM_PORT" 15 || { echo "[proxy-sidecar] text-only shim did not come up on :$SHIM_PORT (see $CCP_LOG)" >&2; return 4; }
  echo "[proxy-sidecar] text-only shim on :$SHIM_PORT -> proxy :$CCP_PORT (compaction summaries reach the model with no tools; log $CCP_LOG.shim)" >&2
  CCP_CLIENT_PORT="$SHIM_PORT"
}

if [ "${1:-}" = "--check" ]; then
  sc_check_model_override "$@"
  ccp_bin >/dev/null || { echo "UNAVAILABLE (claude-code-proxy not installed; set CCP_BIN)"; exit 4; }
  sc_claude_bin >/dev/null || { echo "UNAVAILABLE (claude CLI not found; set CLAUDE_BIN)"; exit 4; }
  # The proxy authenticates SEPARATELY from the `codex` CLI: its own browser login writes
  # here, and ~/.codex/auth.json does not satisfy it (v0.1.35 has no --reuse-codex).
  [ -f "$CCP_AUTH" ] || {
    echo "UNAVAILABLE (no $CCP_AUTH; run: claude-code-proxy codex auth login)"; exit 3; }
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
  sc_check_gates
  echo "OK (model=${_check_reg%%|*} plan-quota band=${SC_PROVIDER_BAND:-unknown} proxy=per-dispatch)"
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

# Credential BEFORE the band gates, matching every other launcher: a missing login is a
# configuration fault, and a band refusal on a machine that could never have dispatched
# sends the reader to the wrong fix.
[ -f "$CCP_AUTH" ] || {
  echo "no $CCP_AUTH — run: claude-code-proxy codex auth login" >&2
  echo "  (this is the PROXY's own login; ~/.codex/auth.json is the codex CLI's and does not satisfy it)" >&2
  exit 3
}

sc_gate_band
sc_gate_capacity     # one live Codex quota read, shared with --check
sc_gate_price_window # no-op without a registry pricingSchedule; present so the ladder is uniform
sc_build_disclosure
sc_validate_effort

# Claude Code defaults an UNRECOGNIZED model to a 200,000-token window, and every GPT id is
# unrecognized to it (the `[claude-code:unrecognized_model]` warning is the same fact surfacing).
# For gpt-5.6-luna that under-declares by 72K: OpenAI's own catalog states 272,000. The child
# compacts against whatever it believes the window is, so leaving the default costs real context.
#
# The value comes from the registry, never a literal here, and never DeepSeek's 1000000 — that
# would over-declare past this model's ceiling and convert a managed client-side compaction into
# a hard upstream error mid-run. Parity between arms means each declares ITS OWN model's window,
# not the same integer.
# SIDECAR_CONTEXT_TOKENS_OVERRIDE forces a smaller declared window — the lever that makes a
# compaction happen on purpose (hooks/sidecar_recompact_reprompt.py proof). Never set it for real work.
SC_CONTEXT_TOKENS="${SIDECAR_CONTEXT_TOKENS_OVERRIDE:-$(python3 "$SC_REGISTRY_CLI" context-window "$SC_MODEL" 2>/dev/null)}"
# Reserve the child's output budget (32000, the maxOutputTokens a proxied child reports) out of the
# declared window. The provider enforces input + max_output <= its window, while the client's
# auto-compaction watches input alone against the declared figure: declared at the provider's own
# effective window, the child reaches the provider's ceiling BEFORE its compaction threshold and the
# request dies "Prompt is too long" with no compact_boundary for the re-prompt rail to act on
# (measured 2026-09-08, gpt-5.6-luna T1 curator: 243.5k input, 0 compactions, 258400 declared, 107 turns lost).
# The child's max output tokens (CLAUDE_CODE_MAX_OUTPUT_TOKENS) IS the output reserve: the provider enforces
# input + max_output <= its window, and the CLI auto-compacts at declared - max_output - ~3k (measured 2026-09-09:
# gpt-5.6-luna T1 08-20 258,400/225,872; T1 09-09 226,400/190,957; 90k probes at 32k vs 8k caps). Leaving the CLI's
# 32k default AND subtracting 32k here stacked two reserves and compacted gpt-5.6-luna at 70% of its real window. 16k is
# ample for one response (a 46 KB design doc is ~12k tokens); a child that must emit more sets
# SIDECAR_MAX_OUTPUT_TOKENS, and SIDECAR_OUTPUT_RESERVE still overrides the subtraction alone.
SC_MAX_OUTPUT="${SIDECAR_MAX_OUTPUT_TOKENS:-16000}"
SC_OUTPUT_RESERVE="${SIDECAR_OUTPUT_RESERVE:-$SC_MAX_OUTPUT}"
if [ -n "$SC_CONTEXT_TOKENS" ] && [ -z "${SIDECAR_CONTEXT_TOKENS_OVERRIDE:-}" ] && [ "$SC_CONTEXT_TOKENS" -gt $((SC_OUTPUT_RESERVE * 2)) ]; then
  SC_CONTEXT_TOKENS=$((SC_CONTEXT_TOKENS - SC_OUTPUT_RESERVE))
fi
[ -n "$SC_CONTEXT_TOKENS" ] &&   echo "[proxy-sidecar] declaring context window $SC_CONTEXT_TOKENS (registry effective window minus the ${SC_OUTPUT_RESERVE}-token output reserve; max output ${SC_MAX_OUTPUT}; auto-compaction expected near $((SC_CONTEXT_TOKENS - SC_MAX_OUTPUT - 3000)))" >&2
# Window-relative harness cost (measured 2026-09-03 on gpt-5.6-luna: -D bare 22k, pointer 25k, full 37k tokens at turn 1).
# Under 400k a survey/review child at -D full spends ~14% of its window before the brief; the shape table
# (reference/sidecar_dispatch.md) pins lenses at pointer. Advisory only — an author/verdict child may need full.
if [ -n "$SC_CONTEXT_TOKENS" ] && [ "$SC_CONTEXT_TOKENS" -lt 400000 ] && [ "${SC_DISCLOSURE:-}" = "full" ] \
   && { [ "${SC_SHAPE:-}" = "survey" ] || [ "${SC_SHAPE:-}" = "review" ]; }; then
  echo "[proxy-sidecar] -D full on a ${SC_CONTEXT_TOKENS}-token window costs ~37k (~$((3700000 / SC_CONTEXT_TOKENS))%) before the brief; lenses take -D pointer (25k) — keep full only if this child must APPLY doctrine" >&2
fi

# AFTER sc_build_disclosure so the port is never claimed for a run a validation gate rejects,
# and so the proxy's cleanup registers behind the scratch-dir cleanup rather than replacing it.
ccp_start || exit 4

VERBOSE_ARGS=()
[ "$SC_FORMAT" = "stream-json" ] && VERBOSE_ARGS=(--verbose)

EXTRA_ARGS=()
[ "$SC_PERSIST" -eq 1 ] || EXTRA_ARGS+=(--no-session-persistence)
[ -n "$SC_RESUME" ] && EXTRA_ARGS+=(--resume "$SC_RESUME")
[ -n "$SC_PERM_MODE" ] && EXTRA_ARGS+=(--permission-mode "$SC_PERM_MODE")
[ -n "$SC_SCHEMA_FILE" ] && EXTRA_ARGS+=(--json-schema "$(cat "$SC_SCHEMA_FILE")")
# Register the re-prompt hook from the LAUNCHER (a worktree older than the hook has no
# registration of its own); --settings merges with the cwd's settings, and the hook no-ops
# without the env var. The path must be absolute and native (`C:/...`, never `/c/...` or
# relative): the hook opens it from the CHILD's cwd. Rationale: reference/sidecar_dispatch.md §Compaction.
SC_PROMPT_FILE_ABS=""
if [ -n "$SC_PROMPT_FILE" ]; then
  SC_PROMPT_FILE_ABS="$(cd "$(dirname "$SC_PROMPT_FILE")" && pwd)/$(basename "$SC_PROMPT_FILE")"
  command -v cygpath >/dev/null 2>&1 && SC_PROMPT_FILE_ABS="$(cygpath -m "$SC_PROMPT_FILE_ABS")"
  SC_SETTINGS_JSON="$(python3 -c 'import json,sys; print(json.dumps({"hooks":{"SessionStart":[{"matcher":"compact","hooks":[{"type":"command","command":"python3 \"%s\"" % sys.argv[1],"timeout":15}]}]}}))' "$SC_ROOT/hooks/sidecar_recompact_reprompt.py")"
fi
SC_SETTINGS_JSON="$(sc_settings_with_bench_guard "${SC_SETTINGS_JSON:-}")"
[ -n "$SC_SETTINGS_JSON" ] && EXTRA_ARGS+=(--settings "$SC_SETTINGS_JSON")

sc_scrub_env

# ANTHROPIC_AUTH_TOKEN is a literal placeholder: the proxy accepts any value and holds the
# real Codex credential itself. What actually keeps host Anthropic auth out of the request
# path is sc_scrub_env plus CLAUDE_CODE_ENTRYPOINT=cli — a Desktop-hosted parent exports
# CLAUDE_CODE_ENTRYPOINT=claude-desktop, which makes the child authenticate through the HOST's
# subscription OAuth and ignore this token entirely.
#
# CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 stops the child's background calls (conversation
# titles, telemetry) from crossing the proxy as extra upstream requests, which would both
# spend plan allowance and pollute the attestation capture with non-task traffic.
_attest_since="$(python3 -c 'import time; print(time.time())')"
run_claude() {
  cd "$SC_RUN_CWD" && env \
    ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ANTHROPIC_BASE_URL="http://127.0.0.1:${CCP_CLIENT_PORT:-$CCP_PORT}" \
    ANTHROPIC_AUTH_TOKEN=unused \
    ANTHROPIC_API_KEY= \
    ANTHROPIC_MODEL="$SC_MODEL" \
    ANTHROPIC_SMALL_FAST_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SUBAGENT_MODEL="$SC_MODEL" \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" \
    CLAUDE_CODE_TRANSPORT="$SC_TRANSPORT" \
    ${SC_CONTEXT_TOKENS:+CLAUDE_CODE_MAX_CONTEXT_TOKENS="$SC_CONTEXT_TOKENS"} \
    ${SC_MAX_OUTPUT:+CLAUDE_CODE_MAX_OUTPUT_TOKENS="$SC_MAX_OUTPUT"} \
    ${SC_SHAPE:+CLAUDE_CODE_SIDECAR_SHAPE="$SC_SHAPE"} \
    ${SC_PROMPT_FILE_ABS:+CLAUDE_CODE_SIDECAR_PROMPT_FILE="$SC_PROMPT_FILE_ABS"} \
    ${SC_TIMEOUT:+timeout} ${SC_TIMEOUT:+"$SC_TIMEOUT"} \
    "$SC_CLAUDE" -p \
      --model "$SC_MODEL" \
      --output-format "$SC_FORMAT" \
      ${VERBOSE_ARGS[@]+"${VERBOSE_ARGS[@]}"} \
      --allowedTools "$SC_TOOLS" \
      ${SC_DISALLOWED:+--disallowedTools "$SC_DISALLOWED"} \
      ${SC_APPEND_ARGS[@]+"${SC_APPEND_ARGS[@]}"} \
      ${SC_MAX_TURNS:+--max-turns "$SC_MAX_TURNS"} \
      ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
      ${SC_RESUME_SID:+--resume} ${SC_RESUME_SID:+"$SC_RESUME_SID"} \
      --add-dir "$SC_WORKDIR" \
      ${SC_ADD_DIRS[@]+"${SC_ADD_DIRS[@]}"} \
      <<< "$SC_PROMPT"
  # Prompt rides STDIN, never argv: Windows exec caps the argument list around 32KB, so a
  # -f file above a few KB used to die at spawn with `Argument list too long` (exit 126) —
  # the same class the retired codex-exec launcher died of
  # (gotcha_prompt_as_argv_transport_windows_limit). `claude -p` reads the prompt from
  # stdin when the positional is absent.
}

[ -n "$SC_PROGRESS" ] && : > "$SC_PROGRESS"
sc_run_watched run_claude "$SC_PROGRESS"   # stall watchdog on the -P stream; sets OUTPUT and rc

# An auto-compaction ENDS a -p run: the child answers the summarize request and the harness closes
# the turn, so the SessionStart:compact re-prompt is never read. Resume the same session with the
# brief as the next user turn, up to SC_COMPACT_RESUMES times (stream-json runs only — json has no
# boundary event). Detector: tools/sidecar_compact_end.py.
sc_resume_loop run_claude "$SC_PROGRESS"
printf '%s\n' "$OUTPUT"
[ $rc -eq 124 ] && echo "proxy sidecar timed out after ${SC_TIMEOUT}s" >&2

# Read the proxy's server-side truth before writing the record, so the record carries it.
SC_ATTESTED_MODEL=''
SC_ATTESTED_EFFORT=''
if _att="$(printf '%s\n' "$OUTPUT" | python3 "$CCP_PROBE" attest "$_attest_since" 2>/dev/null)"; then
  SC_ATTESTED_MODEL="${_att%%$'\t'*}"
  SC_ATTESTED_EFFORT="${_att#*$'\t'}"
  echo "[proxy-sidecar] upstream attestation: model=$SC_ATTESTED_MODEL effort=${SC_ATTESTED_EFFORT:-unset}" >&2
else
  if [ "$SC_FORMAT" = text ]; then
    echo "[proxy-sidecar] upstream attestation needs -o json or -o stream-json; text output has no session ID." >&2
  else
    echo "[proxy-sidecar] no parseable session ID or valid in-window upstream capture; record carries no attestation." >&2
  fi
fi

# Write the record before adding attested effort, but do not publish its stale pre-attestation
# form to the ledger. record-effort rewrites the record atomically and publishes that final form.
_record_ledger="$SC_LEDGER"
[ "$_record_ledger" = "__default__" ] && _record_ledger="$SC_LEDGER_DEFAULT"
SC_LEDGER=''
sc_write_record "$OUTPUT" "$rc"
_record_rc=$?
SC_LEDGER="$_record_ledger"
if [ "$_record_rc" -eq 0 ] && [ -n "$SC_RECORD" ] && [ -f "$SC_RECORD" ]; then
  if [ -n "$_record_ledger" ]; then
    python3 "$CCP_PROBE" record-effort "$SC_RECORD" "$SC_ATTESTED_EFFORT" "$_record_ledger" ||
      echo "[proxy-sidecar] could not add attested effort to the run record; child was not re-run." >&2
  else
    python3 "$CCP_PROBE" record-effort "$SC_RECORD" "$SC_ATTESTED_EFFORT" ||
      echo "[proxy-sidecar] could not add attested effort to the run record; child was not re-run." >&2
  fi
elif [ "$_record_rc" -ne 0 ]; then
  echo "[proxy-sidecar] could not write the run record; child was not re-run." >&2
fi

# `[claude-code:unrecognized_model]` on stderr is EXPECTED and non-fatal: the CLI validates
# model names against its own Anthropic vocabulary client-side, and a GPT id is not in it.
# Runs complete normally with the warning present (measured across the Slice 0 E2E matrix).
if [ "$rc" -ne 0 ] && printf '%s' "${OUTPUT:-}" | grep -q '"api_error_status" *: *401'; then
  cat >&2 <<'DIAG'
[proxy-sidecar] 401 through the local proxy.
  The placeholder token is not the suspect — the proxy accepts any value. Either the
  proxy's own Codex credential expired (~10-day rotation; re-run
  `claude-code-proxy codex auth login`), or a CLAUDE_*/ANTHROPIC_* var leaked past
  sc_scrub_env and the child authenticated against the real Anthropic endpoint instead.
  Distinguish by checking whether ANTHROPIC_BASE_URL survived into the child.
DIAG
fi

exit $rc
