#!/usr/bin/env bash
# deepseek_sidecar.sh — run headless Claude Code against DeepSeek's Anthropic-compatible
# endpoint, as an off-subscription delegate.
#
# The credential is read into a local variable and passed ONLY as a per-command env
# prefix, so it lives in the child `claude` process and nowhere else. The parent
# session's claude.ai subscription auth is never read, exported, or overridden.
#
# The FLAG SURFACE, validation, gate ladder, entrypoint scrub and run-record live in
# lib/sidecar_common.sh, shared with every other provider's launcher. This file holds
# only what is DeepSeek-specific: the credential file, the endpoint, and how the child
# is spawned. Flags are documented here because this is the launcher a caller reads.
#
# Usage:
#   deepseek_sidecar.sh [-m MODEL] [-t TOOLS] [-n MAX_TURNS] [-o FORMAT] [-d DIR] -- "PROMPT"
#   deepseek_sidecar.sh -f prompt.txt
#
#   -m  model ALIAS or id   (pro | flash | deepseek-v4-pro | deepseek-v4-flash;
#       default: flash). Resolved through .claude/reference/external_models.json,
#       the SSOT for ids, prices, limits and gates. An unresolvable name exits 2
#       listing the legal set. A missing/unreadable/invalid registry ALSO exits 2:
#       this is the spending consumer, so it fails closed rather than dispatching
#       at a tier nobody chose.
#
# ON `[claude-code:unrecognized_model]` (SETTLED 2026-08-19, supersedes the KNOWN
# BREAKAGE note that stood here): the warning is emitted on stderr while the request
# COMPLETES normally. Measured through a proxy on the same CLI: every run printed it and
# still returned real prose with a populated modelUsage. It is client-side vocabulary
# validation of a non-Anthropic id, it is non-fatal, and it does not indicate a rejected
# dispatch. The old note could not separate it from a $0.56 balance in the same window;
# they were unrelated. Do NOT "fix" it by guessing another id — the registry is the SSOT.
#
#   -A  authorize a `gated` model past the BAND gate (pro is gated), and past a
#       plan-quota provider's own band ceiling. The dollar balance floor still
#       applies. The "I typed this deliberately" flag.
#   -U  dispatch a model the registry marks UNAVAILABLE. Separate from -A on purpose:
#       -A trades one currency for another at an agent's discretion, while availability
#       is the user's standing decision about what is in the roster at all.
#   -e  effort level        low|medium|high|xhigh|max (default: unset — provider default)
#   -t  --allowedTools CSV  (default: Read,Glob,Grep — read-only)
#   -n  max turns           (default: UNCAPPED — never cap benchmark/delegate turns,
#       a cap discards completed work; -T wall-clock is the runaway guard)
#   -o  output format: json|text|stream-json  (default: json)
#   -d  working directory   (default: cwd)
#   -f  read prompt from file instead of argv
#   -T  timeout seconds     (default: UNSET - no kill; use -P + orchestrator staleness watch for hangs)
#   -x  --disallowedTools CSV (default: "Task,Agent" — headless sessions can
#       otherwise spawn built-in subagents (Explore etc.) OUTSIDE the -t grant;
#       the spawn is requested under the client's default registry model name,
#       so the compat layer serves it as an UNPROVABLE model and the run-record
#       shows a second servedModel. Measured 2026-08-03: a T2 arm's Explore
#       subagent surfaced as claude-opus-5[1m]. Pass -x "" to permit spawning.)
#   -R  run-record path: write one JSON provenance record per invocation
#       (servedModel from modelUsage canonicalModel, effort, harnessBase/
#       harnessSession from the workdir's git, raw token counts, recomputed
#       costUSD). Exits 2 up front if the path is unwritable.
#   -P  progress file: forces stream-json, tees each event line to the file
#       (Monitor-able live) while -R parses the final result event.
#       TEES -- stdout carries the identical stream, so with -P send stdout to
#       /dev/null; redirecting it to a second path stores every byte twice
#       (measured: 64 MB of byte-identical twins in the model-effort-v1.0 archive).
#   -S  JSON schema FILE passed to --json-schema (structured output);
#       record gains schemaValid when combined with -R
#   -s  persist the session (drops --no-session-persistence) so -r can
#       continue it later
#   -r  session id to --resume (iterative turns between completions)
#   -p  --permission-mode passthrough. DEFAULTS TO `auto` — headless auto-DENIES
#       out-of-grant tools, which silently cripples write-capable delegates, so
#       `auto` is the standard and a narrower mode is the opt-in.
#       Valid: auto|acceptEdits|dontAsk|manual|plan|bypassPermissions
#       (`default` is accepted as the old name for `manual`). Use acceptEdits for
#       a read-only arm you want edit-capable but nothing more.
#   -a  extra --add-dir path (repeatable). Grants the child access to a path
#       OUTSIDE -d, e.g. the Obsidian vault. The narrow alternative to
#       -p bypassPermissions: grant the one path instead of disarming the
#       whole permission system. VERIFY the read actually lands — a grant
#       that silently misses turns a check into a false empty.
#   -G  delegate guard shape: any|survey|review|author (default: unset ->
#       hooks/session_model_rails.py falls back to `any`). Exported as
#       CLAUDE_CODE_SIDECAR_SHAPE; the hook inlines .claude/guards/any.md PLUS
#       .claude/guards/<shape>.md on the child's SessionStart, assembled by
#       tools/guard_text.py. any.md carries the rules binding every delegate, so
#       it is concatenated rather than left as a pointer the child may not follow.
#       Tier is strict unless CLAUDE_CODE_SIDECAR_TIER says otherwise.
#   -D  disclosure tier       bare|pointer|full (default: full). `bare`: child
#       runs from an empty scratch run-cwd — vendor prompt + CLI only; user-level
#       config is intentionally KEPT (isolating CLAUDE_CONFIG_DIR would drop
#       marketplace MCP tools, and the dial is a token-disclosure lever, not a
#       tool-surface lever). `pointer`: bare + append .claude/auto-memory/MEMORY.md
#       (the compact gotcha index; deep reads stay demand-driven via the child's
#       Read against --add-dir grants). `full`: today's exact behavior — child
#       runs in-repo, hooks/rails/rules all live. Unknown value exits 2.
#       In bare/pointer tiers no project hooks fire, so the -G guard is appended
#       by the sidecar itself (same content, different delivery); `full` keeps
#       hook delivery — no duplication.
#   -C  context file (repeatable): append any additional file to the child's
#       system prompt via --append-system-prompt-file (domain rules, guard file,
#       spec excerpt). Missing file exits 2. This is the per-dispatch composition
#       layer on top of -D.
#   -L  spend-ledger JSONL: appends the run-record as one line (default:
#       ~/.claude/deepseek_spend.jsonl when -R is set; -L "" disables)
#   -l  label: attribution tag stored on the run-record (e.g. "review:config-dup").
#       /orchestration_metrics reads the spend ledger as a second source and
#       reports/rates sidecar runs BY THIS LABEL — an unlabeled delegate run is
#       skipped by default (only --sidecar-all spend audits see it). Always pass
#       -l for real delegated work.
#
# EFFORT MAPPING (2026-08-03): DeepSeek's own surfaces disagree on valid
# levels (HF: low/high/max; vllm/tech-report: None/high/max with high a
# no-op). Thinking blocks DO flow and `low` measurably suppresses them, but
# rung identity is NOT verifiable — treat -e as a requested-string
# coordinate. Benchmarked: requested-max scored best with 0 fabrications;
# requested-high fabricated catastrophically. Prefer max for real work.
#
# NOTE ON -e: whether DeepSeek's compat layer honors the effort parameter is an
# EMPIRICAL question, not a guarantee. Claude Code will send it regardless. Verify
# a pin actually changed behavior before trusting an effort-pinned benchmark arm.
#
# NOTE ON -m (measured 2026-08-12, supersedes the old "unrecognized names route
# to V4 Flash" claim — that was never true and is now demonstrably false):
#   * Unknown VENDOR-SHAPED ids HARD-ERROR. `deepseek-v4-pro-0813` is rejected with
#     "The supported API model names are deepseek-v4-pro or deepseek-v4-flash".
#     There is no silent fallback to correct for.
#   * BARE ROLE NAMES HARD-ERROR: opus, sonnet, haiku, fable are all rejected.
#     This is why hooks/model_pin_translate.py STRIPS the Agent tool's model pin
#     rather than passing it through — an un-stripped bare role makes agent()
#     return null, .filter(Boolean) swallows it, and the fan-out reports
#     "0 findings" while looking clean.
#   * FULL `claude-*` ids alias BY TIER: claude-opus-* -> V4 Pro;
#     claude-sonnet-* / claude-haiku-* / claude-fable-* -> V4 Flash.
# Still check `modelUsage`/`canonicalModel` in the JSON — it is now how you confirm
# a tier alias landed where you intended, rather than how you catch a fallback.
# A PROXIED transport inverts this: there, every bare role name RESOLVES to some
# model, so a mis-pin cannot announce itself and identity must come from the
# proxy's own capture. See codex_proxy_sidecar.sh.
#
# Availability: `deepseek_sidecar.sh --check` prints one line and exits (0 = can
# dispatch now). SessionStart reports it as `sidecar:` in <session-context>, so
# availability is a known fact before any routing decision — never something to
# investigate mid-session.
#
# Exit codes: see lib/sidecar_common.sh (the shared contract). 8 (provider quota
# band) cannot fire here — DeepSeek is dollar-billed, so it has no plan quota.

set -uo pipefail

ENV_FILE="${HOME}/.env.ai-worker.cmd"
BASE_URL="https://api.deepseek.com/anthropic"

SC_TRANSPORT="deepseek"
# shellcheck source=lib/sidecar_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/sidecar_common.sh"

SC_MODEL="flash"   # resolved through the registry; alias or full id both fine

# Reads the credential out of the CMD-format env file the ai-worker MCP server uses.
# Strips CR (the file has Windows line endings) and any quotes.
ds_read_key() {
  grep -iE '^[[:space:]]*set[[:space:]]+DEEPSEEK_API_KEY=' "$ENV_FILE" \
    | head -1 | sed -E 's/^[[:space:]]*set[[:space:]]+DEEPSEEK_API_KEY=//' \
    | tr -d '\r' | sed -E 's/^"(.*)"$/\1/'
}

# --check: zero-argument availability probe. Prints ONE line, exits, dispatches
# nothing. Exit 0 = the sidecar can dispatch on this workstation right now;
# nonzero = it cannot, and the line names the missing precondition.
# This is the SINGLE definition of sidecar availability. hooks/session_context_loader.py
# calls it at SessionStart so the answer is already in context — an agent weighing a
# sidecar dispatch must never have to investigate credentials or transport.
# Intercepted before getopts, which would reject `--check` as bad usage.
if [ "${1:-}" = "--check" ]; then
  sc_claude_bin >/dev/null || { echo "UNAVAILABLE (claude CLI not found; set CLAUDE_BIN)"; exit 4; }
  [ -f "$ENV_FILE" ] || { echo "UNAVAILABLE (credential file missing: $ENV_FILE)"; exit 3; }
  _check_key="$(ds_read_key)"
  case "$_check_key" in
    ""|"<redacted>"|"your-key-here"|"sk-xxx"*)
      echo "UNAVAILABLE (DEEPSEEK_API_KEY not populated in $ENV_FILE)"; exit 3 ;;
  esac
  # The registry is a hard dependency of every dispatch (model resolution, prices,
  # gates), so an unusable one means UNAVAILABLE — not a surprise at dispatch time.
  _check_reg="$(python3 "$SC_REGISTRY_CLI" sidecar-fields "$SC_MODEL" 2>&1)" || {
    echo "UNAVAILABLE (model registry unusable: ${_check_reg%%$'\n'*})"; exit 2; }
  # SessionStart publishes this line as `sidecar:` in <session-context>, so an OK here becomes a
  # standing fact that the sidecar is a live option. Read from the same registry field the dispatch
  # gate reads, so the two cannot disagree.
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
  echo "OK (model=${_check_reg%%|*} endpoint=$BASE_URL key=***${_check_key: -4})"
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

# Credential BEFORE the band gate: a missing key is a configuration fault, and reporting a
# band refusal for a machine that could never have dispatched sends the reader to the wrong fix.
[ -f "$ENV_FILE" ] || { echo "credential file missing: $ENV_FILE" >&2; exit 3; }
SC_CREDENTIAL="$(ds_read_key)"
case "$SC_CREDENTIAL" in
  ""|"<redacted>"|"your-key-here"|"sk-xxx"*)
    echo "DEEPSEEK_API_KEY not populated in $ENV_FILE" >&2; exit 3 ;;
esac

sc_gate_band
sc_gate_provider_band   # no-op on a marginal-usd transport; present so the ladder is uniform
sc_gate_balance
sc_build_disclosure
sc_validate_effort

# The window is the registry's to state, not this script's. It previously hardcoded 1000000 —
# the same number the registry already carried for this model, so the two could drift with
# nothing to catch it. MAX_CONTEXT_TOKENS still overrides for a one-off.
SC_CONTEXT_TOKENS="$(python3 "$SC_REGISTRY_CLI" context-window "$SC_MODEL" 2>/dev/null)"

EFFORT_ARGS=()
# `max` is permitted here. The CLAUDE.md ban on `max` is an Anthropic cost finding; it has
# never been measured on DeepSeek, whose output is ~500x cheaper, so the ban does not carry over.
[ -n "$SC_EFFORT" ] && EFFORT_ARGS=(--effort "$SC_EFFORT")

VERBOSE_ARGS=()
[ "$SC_FORMAT" = "stream-json" ] && VERBOSE_ARGS=(--verbose)  # CLI requires it with --print

EXTRA_ARGS=()
[ "$SC_PERSIST" -eq 1 ] || EXTRA_ARGS+=(--no-session-persistence)
[ -n "$SC_RESUME" ] && EXTRA_ARGS+=(--resume "$SC_RESUME")
[ -n "$SC_PERM_MODE" ] && EXTRA_ARGS+=(--permission-mode "$SC_PERM_MODE")
[ -n "$SC_SCHEMA_FILE" ] && EXTRA_ARGS+=(--json-schema "$(cat "$SC_SCHEMA_FILE")")

sc_scrub_env

# ANTHROPIC_AUTH_TOKEN replaces subscription auth FOR THIS CHILD ONLY.
# ANTHROPIC_API_KEY is blanked so a stray parent value cannot win the auth race.
# The subshell cd makes -d the child's WORKING DIRECTORY, not merely an added dir: the
# harness the arm loads (.claude/, CLAUDE.md, hooks) is resolved from cwd, so a benchmark
# arm must run FROM its arm root or it silently loads the live repo's harness.
#
# Subagent/background-model names are pinned to the REQUESTED model. Left unpinned, a
# spawned agent's Anthropic name reaches the compat layer, which aliases full `claude-*`
# ids BY TIER: claude-opus-* -> V4 Pro (the expensive one, billing-confirmed 2026-08-03).
# Pinning removes the tier lottery entirely. Belt to the -x suspenders.
run_claude() {
  cd "$SC_RUN_CWD" && env \
    ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ${DS_CONFIG_DIR:+CLAUDE_CONFIG_DIR="$DS_CONFIG_DIR"} \
    ANTHROPIC_BASE_URL="$BASE_URL" \
    ANTHROPIC_AUTH_TOKEN="$SC_CREDENTIAL" \
    ANTHROPIC_API_KEY= \
    ANTHROPIC_SMALL_FAST_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SUBAGENT_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" \
    CLAUDE_CODE_MAX_CONTEXT_TOKENS="${MAX_CONTEXT_TOKENS:-$SC_CONTEXT_TOKENS}" \
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
      --add-dir "$SC_WORKDIR" \
      ${SC_ADD_DIRS[@]+"${SC_ADD_DIRS[@]}"}
}

if [ -n "$SC_PROGRESS" ]; then
  # Tee event lines live so a Monitor can follow the run; capture for -R too.
  OUTPUT="$(run_claude | tee "$SC_PROGRESS")"
else
  OUTPUT="$(run_claude)"
fi
rc=$?
printf '%s\n' "$OUTPUT"
[ $rc -eq 124 ] && echo "sidecar timed out after ${SC_TIMEOUT}s" >&2

sc_write_record "$OUTPUT" "$rc"

# A 401 here almost never means a bad DEEPSEEK_API_KEY — the endpoint reports
# whatever credential actually arrived, and a host-auth leak sends a rotating
# OAuth token whose tail differs every run. Say so, so nobody re-diagnoses it.
if [ "$rc" -ne 0 ] && printf '%s' "${OUTPUT:-}" | grep -q '"api_error_status" *: *401'; then
  cat >&2 <<'DIAG'
[sidecar] 401 from the DeepSeek endpoint.
  Before suspecting the key: compare the key tail the error names against the
  tail in ~/.env.ai-worker.cmd. If they differ (or change between runs), the
  child authenticated with host subscription OAuth instead of the env token —
  a CLAUDE_*/ANTHROPIC_* var leaked past the scrub in sc_scrub_env().
  Verify the key independently:
    curl -s -o /dev/null -w '%{http_code}\n' https://api.deepseek.com/v1/models \
      -H "Authorization: Bearer <key>"
DIAG
fi

exit $rc
