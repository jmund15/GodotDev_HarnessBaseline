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
# author=full·author, judge=full·any). The former `codex exec` launcher (codex_sidecar.sh) is
# a retired refusing stub; its header records why.
#
# Proxy: raine/claude-code-proxy v0.1.35 (caixiaoshun/claudex was tested and rejected — its
# tool-schema conversion emits schemas the upstream refuses).
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
# ATTESTATION. A proxied child CANNOT report which model served it: Claude Code's system
# prompt asserts a Claude identity, and a run with the upstream forced to gpt-5.6-luna still
# reported the child's own pin (`gpt-5.4-mini`) in `modelUsage`. The proxy's own
# `004-*-upstream-request.json` capture is the only valid evidence, so CCP_TRAFFIC_LOG=1 is
# always on here and the record carries `attestedModel`/`attestationAgrees`. The captures
# accumulate under ~/.local/state/claude-code-proxy/traffic and are never pruned — delete that
# tree periodically; it holds full request bodies, prompts included.
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
. "$(dirname "${BASH_SOURCE[0]}")/lib/sidecar_common.sh"

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
  echo "[proxy-sidecar] starting claude-code-proxy on :$CCP_PORT (model=$SC_MODEL effort=${SC_EFFORT:-unset}, log: $CCP_LOG)" >&2
  # The pins go in the SERVER's env; see the header. CCP_TRAFFIC_LOG is unconditional because
  # the capture it writes is the only evidence of which model actually served the run.
  env CCP_CODEX_MODEL="$SC_MODEL" \
      ${SC_EFFORT:+CCP_CODEX_EFFORT="$SC_EFFORT"} \
      CCP_TRAFFIC_LOG=1 \
      nohup "$ccp" serve --no-monitor --port "$CCP_PORT" >>"$CCP_LOG" 2>&1 &
  CCP_PID=$!
  sc_on_exit 'kill "$CCP_PID" 2>/dev/null || :'
  python3 "$CCP_PROBE" wait "$CCP_PORT" 25 && return 0
  { echo "[proxy-sidecar] proxy did not become healthy on :$CCP_PORT within 25s."
    echo "  Last lines of $CCP_LOG:"
    tail -n 15 "$CCP_LOG" 2>/dev/null | sed 's/^/    /'
  } >&2
  return 4
}

if [ "${1:-}" = "--check" ]; then
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
  if ! _check_q="$(python3 "$SC_ROOT/scripts/codex_quota_probe.py" 2>&1)"; then
    echo "UNAVAILABLE (quota probe failed: ${_check_q%%$'\n'*})"; exit 3
  fi
  _band="$(READING="$_check_q" python3 -c 'import json,os;d=json.loads(os.environ["READING"]);print(d.get("band") or "unknown", d.get("planType") or "?")')"
  echo "OK (model=${_check_reg%%|*} plan-quota band=$_band proxy=per-dispatch)"
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
sc_gate_provider_band
sc_gate_balance      # no-op: a plan-quota transport declares no balance endpoint
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
SC_CONTEXT_TOKENS="$(python3 "$SC_REGISTRY_CLI" context-window "$SC_MODEL" 2>/dev/null)"
[ -n "$SC_CONTEXT_TOKENS" ] && \
  echo "[proxy-sidecar] declaring context window $SC_CONTEXT_TOKENS (registry; CLI default is 200000)" >&2

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
_attest_since="$(date +%s)"
run_claude() {
  cd "$SC_RUN_CWD" && env \
    ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ANTHROPIC_BASE_URL="http://127.0.0.1:$CCP_PORT" \
    ANTHROPIC_AUTH_TOKEN=unused \
    ANTHROPIC_API_KEY= \
    ANTHROPIC_MODEL="$SC_MODEL" \
    ANTHROPIC_SMALL_FAST_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SUBAGENT_MODEL="$SC_MODEL" \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" \
    ${SC_CONTEXT_TOKENS:+CLAUDE_CODE_MAX_CONTEXT_TOKENS="$SC_CONTEXT_TOKENS"} \
    ${SC_SHAPE:+CLAUDE_CODE_SIDECAR_SHAPE="$SC_SHAPE"} \
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
      --add-dir "$SC_WORKDIR" \
      ${SC_ADD_DIRS[@]+"${SC_ADD_DIRS[@]}"} \
      <<< "$SC_PROMPT"
  # Prompt rides STDIN, never argv: Windows exec caps the argument list around 32KB, so a
  # -f file above a few KB used to die at spawn with `Argument list too long` (exit 126) —
  # the same class the retired codex-exec launcher died of
  # (gotcha_prompt_as_argv_transport_windows_limit). `claude -p` reads the prompt from
  # stdin when the positional is absent.
}

if [ -n "$SC_PROGRESS" ]; then
  OUTPUT="$(run_claude | tee "$SC_PROGRESS")"
else
  OUTPUT="$(run_claude)"
fi
rc=$?
printf '%s\n' "$OUTPUT"
[ $rc -eq 124 ] && echo "proxy sidecar timed out after ${SC_TIMEOUT}s" >&2

# Read the proxy's server-side truth before writing the record, so the record carries it.
if _att="$(python3 "$CCP_PROBE" attest "$_attest_since" 2>/dev/null)"; then
  SC_ATTESTED_MODEL="${_att%%$'\t'*}"
  _att_effort="${_att#*$'\t'}"
  echo "[proxy-sidecar] upstream attestation: model=$SC_ATTESTED_MODEL effort=${_att_effort:-unset}" >&2
else
  echo "[proxy-sidecar] no upstream capture found; record carries no attestation." >&2
fi

sc_write_record "$OUTPUT" "$rc"

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
