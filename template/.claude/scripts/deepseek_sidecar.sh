#!/usr/bin/env bash
# deepseek_sidecar.sh — run headless Claude Code against DeepSeek's Anthropic-compatible
# endpoint, as an off-subscription delegate.
#
# The credential is read into a local variable and passed ONLY as a per-command env
# prefix, so it lives in the child `claude` process and nowhere else. The parent
# session's claude.ai subscription auth is never read, exported, or overridden.
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
#   -A  authorize a `gated` model past the BAND gate (pro is gated). The balance
#       floor still applies. The "I typed this deliberately" flag.
#   -e  effort level        low|medium|high|xhigh (default: unset — provider default)
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
#       (Monitor-able live) while -R parses the final result event
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
#       CLAUDE_CODE_SIDECAR_SHAPE; the hook inlines .claude/guards/<shape>.md
#       at the strict tier on the child's SessionStart.
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
#
# Availability: `deepseek_sidecar.sh --check` prints one line and exits (0 = can
# dispatch now). SessionStart reports it as `sidecar:` in <session-context>, so
# availability is a known fact before any routing decision — never something to
# investigate mid-session.
#
# Exit codes: 0 ok · 2 bad usage / unresolvable model / unusable registry
#             3 credential missing · 4 claude CLI missing
#             5 band gate refusal (pass -A to override) · 6 balance floor unmet
#               or the balance probe itself failed (-A does NOT override 6)

set -uo pipefail

ENV_FILE="${HOME}/.env.ai-worker.cmd"
BASE_URL="https://api.deepseek.com/anthropic"

MODEL="flash"          # resolved through the registry below; alias or full id both fine
AUTHORIZED=0           # -A: bypass the band gate for a `gated` model
EFFORT=""
TOOLS="Read,Glob,Grep"
MAX_TURNS=""   # empty = NO turn cap (user directive 2026-08-03: never cap turns; -T wall-clock is the runaway guard). Pass -n N to cap explicitly.
FORMAT="json"
WORKDIR="$PWD"
PROMPT_FILE=""
TIMEOUT=""   # empty = NO wall-clock kill (user directive 2026-08-03: a timeout only ever WAKES the orchestrator to check for a hang via -P heartbeat staleness; it never halts a legitimate long run). Pass -T N to opt in to a hard kill.
RECORD=""
DISALLOWED="Task,Agent"
PROGRESS=""
SCHEMA_FILE=""
PERSIST=0
RESUME=""
PERM_MODE="auto"   # user directive 2026-08-05. Headless auto-DENIES out-of-grant tools (see -p in
                   # the header), which hard-fails a write delegate whose Bash command falls outside
                   # the project allowlist. Pass -p explicitly to narrow it.
LEDGER="__default__"
LABEL=""
SHAPE=""
ADD_DIRS=()
DISCLOSURE="full"
CONTEXT_FILES=()

# --check: zero-argument availability probe. Prints ONE line, exits, dispatches
# nothing. Exit 0 = the sidecar can dispatch on this workstation right now;
# nonzero = it cannot, and the line names the missing precondition.
# This is the SINGLE definition of sidecar availability. hooks/session_context_loader.py
# calls it at SessionStart so the answer is already in context — an agent weighing a
# sidecar dispatch must never have to investigate credentials or transport.
# Intercepted before getopts, which would reject `--check` as bad usage.
if [ "${1:-}" = "--check" ]; then
  command -v claude >/dev/null 2>&1 || { echo "UNAVAILABLE (claude CLI not on PATH)"; exit 4; }
  [ -f "$ENV_FILE" ] || { echo "UNAVAILABLE (credential file missing: $ENV_FILE)"; exit 3; }
  _check_key="$(grep -iE '^[[:space:]]*set[[:space:]]+DEEPSEEK_API_KEY=' "$ENV_FILE" \
    | head -1 | sed -E 's/^[[:space:]]*set[[:space:]]+DEEPSEEK_API_KEY=//' \
    | tr -d '\r' | sed -E 's/^"(.*)"$/\1/')"
  case "$_check_key" in
    ""|"<redacted>"|"your-key-here"|"sk-xxx"*)
      echo "UNAVAILABLE (DEEPSEEK_API_KEY not populated in $ENV_FILE)"; exit 3 ;;
  esac
  # The registry is a hard dependency of every dispatch (model resolution, prices,
  # gates), so an unusable one means UNAVAILABLE — not a surprise at dispatch time.
  _check_reg="$(python3 "$(dirname "${BASH_SOURCE[0]}")/../tools/model_registry.py" \
    sidecar-fields "$MODEL" 2>&1)" || {
    echo "UNAVAILABLE (model registry unusable: ${_check_reg%%$'\n'*})"; exit 2; }
  echo "OK (model=${_check_reg%%|*} endpoint=$BASE_URL key=***${_check_key: -4})"
  exit 0
fi

while getopts "m:e:t:n:o:d:f:T:R:x:P:S:sr:p:L:l:a:G:AD:C:" opt; do
  case "$opt" in
    m) MODEL="$OPTARG" ;;
    A) AUTHORIZED=1 ;;
    e) EFFORT="$OPTARG" ;;
    t) TOOLS="$OPTARG" ;;
    n) MAX_TURNS="$OPTARG" ;;
    o) FORMAT="$OPTARG" ;;
    d) WORKDIR="$OPTARG" ;;
    f) PROMPT_FILE="$OPTARG" ;;
    T) TIMEOUT="$OPTARG" ;;
    R) RECORD="$OPTARG" ;;
    x) DISALLOWED="$OPTARG" ;;
    P) PROGRESS="$OPTARG"; FORMAT="stream-json" ;;
    S) SCHEMA_FILE="$OPTARG" ;;
    s) PERSIST=1 ;;
    r) RESUME="$OPTARG"; PERSIST=1 ;;
    p) PERM_MODE="$OPTARG" ;;
    L) LEDGER="$OPTARG" ;;
    l) LABEL="$OPTARG" ;;
    a) ADD_DIRS+=(--add-dir "$OPTARG") ;;
    G) SHAPE="$OPTARG" ;;
    D) DISCLOSURE="$OPTARG" ;;
    C) CONTEXT_FILES+=("$OPTARG") ;;
    *) echo "bad usage; see header" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
[ "${1:-}" = "--" ] && shift

if [ -n "$PROMPT_FILE" ]; then
  [ -f "$PROMPT_FILE" ] || { echo "prompt file not found: $PROMPT_FILE" >&2; exit 2; }
  PROMPT="$(cat "$PROMPT_FILE")"
else
  PROMPT="${*:-}"
fi
[ -n "$PROMPT" ] || { echo "empty prompt" >&2; exit 2; }

command -v claude >/dev/null 2>&1 || { echo "claude CLI not on PATH" >&2; exit 4; }

# ---------------------------------------------------------------- model resolution
# One registry call yields everything the dispatch path needs. Python startup
# dominates on Windows, so five `resolve --field` calls would cost most of a
# second for what is one dict lookup.
REGISTRY_CLI="$(dirname "${BASH_SOURCE[0]}")/../tools/model_registry.py"
BUDGET_HOOK="$(dirname "${BASH_SOURCE[0]}")/../hooks/budget_posture.py"

if ! MODEL_FIELDS="$(python3 "$REGISTRY_CLI" sidecar-fields "$MODEL" 2>&1)"; then
  {
    echo "[sidecar] cannot resolve -m '$MODEL':"
    echo "  $MODEL_FIELDS"
    echo "  registry: $REGISTRY_CLI"
    echo "  This is the SPENDING consumer, so it fails closed: no fallback to a"
    echo "  hardcoded id, because dispatching at a tier nobody chose costs money."
  } >&2
  exit 2
fi
IFS='|' read -r MODEL MODEL_ALIAS AUTH_TIER MIN_BAND MIN_BALANCE FRESH_RATE BALANCE_URL \
  <<< "$MODEL_FIELDS"

# Fail BEFORE dispatch on an unwritable record path — an arm that runs and then
# discards its provenance is unscoreable under MANIFEST seal discipline.
if [ -n "$RECORD" ]; then
  if ! : >> "$RECORD" 2>/dev/null; then
    echo "run-record path not writable: $RECORD" >&2; exit 2
  fi
  case "$FORMAT" in
    json|stream-json) ;;
    *) echo "-R requires -o json or stream-json (record is parsed from the result payload)" >&2; exit 2 ;;
  esac
fi
if [ -n "$SCHEMA_FILE" ] && [ ! -f "$SCHEMA_FILE" ]; then
  echo "schema file not found: $SCHEMA_FILE" >&2; exit 2
fi
if [ -n "$PERM_MODE" ]; then
  case "$PERM_MODE" in
    auto|acceptEdits|dontAsk|manual|plan|bypassPermissions|default) ;;
    *) echo "invalid permission mode '$PERM_MODE' (auto|acceptEdits|dontAsk|manual|plan|bypassPermissions)" >&2; exit 2 ;;
  esac
fi
if [ -n "$SHAPE" ]; then
  case "$SHAPE" in
    any|survey|review|author) ;;
    *) echo "invalid guard shape '$SHAPE' (any|survey|review|author)" >&2; exit 2 ;;
  esac
fi
if [ -n "$DISCLOSURE" ]; then
  case "$DISCLOSURE" in
    bare|pointer|full) ;;
    *) echo "invalid disclosure tier '$DISCLOSURE' (bare|pointer|full)" >&2; exit 2 ;;
  esac
fi
for _cf in "${CONTEXT_FILES[@]}"; do
  [ -f "$_cf" ] || { echo "context file not found: $_cf" >&2; exit 2; }
done

# Parse `set DEEPSEEK_API_KEY=...` out of the CMD-format env file the ai-worker
# MCP server uses. Strip CR (the file has Windows line endings) and any quotes.
[ -f "$ENV_FILE" ] || { echo "credential file missing: $ENV_FILE" >&2; exit 3; }
DEEPSEEK_KEY="$(grep -iE '^[[:space:]]*set[[:space:]]+DEEPSEEK_API_KEY=' "$ENV_FILE" \
  | head -1 | sed -E 's/^[[:space:]]*set[[:space:]]+DEEPSEEK_API_KEY=//' \
  | tr -d '\r' | sed -E 's/^"(.*)"$/\1/')"

case "$DEEPSEEK_KEY" in
  ""|"<redacted>"|"your-key-here"|"sk-xxx"*)
    echo "DEEPSEEK_API_KEY not populated in $ENV_FILE" >&2; exit 3 ;;
esac

# -------------------------------------------------------------- preflight gate
# Runs BEFORE any dispatch. Two independent checks with different scopes:
#
#   BAND    every model, both tiers. The band read is local and free, so gating it
#           behind authTier would leave flash's minBand authored, validated and
#           never read - a dead config value that reads as a live prohibition.
#           Flash's `On pace` floor encodes live doctrine: sidecar spend is
#           forbidden in Surplus, where unused plan quota simply expires.
#   BALANCE `gated` models only (one HTTP round-trip; the common path pays nothing).
#
# Band NAMES are ordered by BURN RATE, not by headroom: Surplus is the LOW-pressure
# band ("plan quota going unused - spend it"), Ahead/Hot are HIGH-pressure ones
# ("quota tight - paid transport is now the cheaper currency"). Every message below
# prints the pressure NUMBER with the name, because the name alone reads backwards.
BAND_OUT="$(python3 "$BUDGET_HOOK" --band --pressure 2>/dev/null)"; BAND_RC=$?
BAND="${BAND_OUT%%$'\t'*}"
BAND_P="${BAND_OUT##*$'\t'}"
[ "$BAND_P" = "$BAND" ] && BAND_P="?"

if [ "$AUTHORIZED" -eq 1 ]; then
  echo "[sidecar] -A: band gate bypassed for $MODEL_ALIAS ($MODEL); band=$BAND pressure=$BAND_P, floor=$MIN_BAND" >&2
elif [ "$BAND_RC" -ne 0 ]; then
  # `unknown` is NOT a band. Treated as not-satisfied, and said so explicitly:
  # "unreadable" and "too low" are different problems with different fixes.
  {
    echo "[sidecar] REFUSING $MODEL_ALIAS ($MODEL): budget band is UNREADABLE (not low)."
    echo "  $BUDGET_HOOK --band exited $BAND_RC; no cc-cachestat state file was findable."
    echo "  That file is written by ~/.claude/statusline.py every turn - if it is absent,"
    echo "  the gate has no input and cannot certify the floor of $MIN_BAND."
    echo "  Override deliberately with -A, or restore the statusline."
  } >&2
  exit 5
else
  python3 "$REGISTRY_CLI" band-satisfies "$BAND" "$MIN_BAND"; SAT_RC=$?
  if [ "$SAT_RC" -ne 0 ]; then
    {
      if [ "$SAT_RC" -eq 2 ]; then
        echo "[sidecar] REFUSING $MODEL_ALIAS ($MODEL): band name '$BAND' is not in budget_posture.BANDS."
      else
        echo "[sidecar] REFUSING $MODEL_ALIAS ($MODEL): band $BAND (7d pressure $BAND_P) is below the floor $MIN_BAND."
        echo "  Bands rank by BURN RATE: Surplus < On pace < Ahead < Hot. A low band means"
        echo "  plan quota is going unused - spend that first; it expires."
      fi
      echo "  Override: re-run with -A. Policy home: .claude/skills/orchestration/SKILL.md section 5."
    } >&2
    exit 5
  fi
fi

if [ "$AUTH_TIER" = "gated" ] && [ -n "$BALANCE_URL" ]; then
  # Applies ALWAYS, -A included: -A authorizes intent, it cannot conjure funds.
  BAL_RAW="$(curl -s --max-time 15 "$BALANCE_URL" -H "Authorization: Bearer $DEEPSEEK_KEY" 2>/dev/null)"
  BAL_NOW="$(BAL_V="$BAL_RAW" python3 -c '
import json, os, sys
try:
    infos = json.loads(os.environ["BAL_V"]).get("balance_infos") or []
    # total_balance is a STRING in this API ("6.74"), not a number.
    print(max(float(i["total_balance"]) for i in infos))
except Exception:
    sys.exit(1)
' 2>/dev/null)" || BAL_NOW=""
  if [ -z "$BAL_NOW" ]; then
    {
      echo "[sidecar] REFUSING $MODEL_ALIAS ($MODEL): balance probe FAILED (not: balance low)."
      echo "  $BALANCE_URL returned nothing parsable. Failing loud before spending, because"
      echo "  an unverified balance on a gated model is the case this gate exists for."
    } >&2
    exit 6
  fi
  if ! awk -v b="$BAL_NOW" -v f="$MIN_BALANCE" 'BEGIN{exit !(b+0 >= f+0)}'; then
    echo "[sidecar] REFUSING $MODEL_ALIAS ($MODEL): balance \$$BAL_NOW is below the \$$MIN_BALANCE floor. -A does not override this." >&2
    exit 6
  fi
  echo "[sidecar] $MODEL_ALIAS preflight OK: band=$BAND (pressure $BAND_P, floor $MIN_BAND), balance=\$$BAL_NOW (floor \$$MIN_BALANCE), fresh-token rate \$$FRESH_RATE/1M" >&2
fi

# -------------------------------------------------------- disclosure tier
# Constructive disclosure: the child runs isolated and the tier appends exactly
# what it names. `full` = today's behavior (RUN_CWD == WORKDIR, no appends);
# `bare`/`pointer` = scratch run-cwd; the repo grant (--add-dir "$WORKDIR")
# is unchanged, so the child keeps Read access to the repo either way.
# Appended paths must be ABSOLUTE: the child resolves them against its own cwd
# (RUN_CWD), not this script's — a relative dirname-of-BASH_SOURCE breaks in
# the isolated tiers exactly where these appends are the only context.
_SDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_CWD="$WORKDIR"
APPEND_ARGS=()
case "$DISCLOSURE" in
  bare|pointer)
    RUN_CWD="$(mktemp -d)"
    # The child (a Windows process) may still hold RUN_CWD when EXIT fires;
    # a busy-dir rm must not fail the script.
    trap 'rm -rf "$RUN_CWD" 2>/dev/null || :' EXIT
    if [ -n "$SHAPE" ]; then
      _guard="$_SDIR/guards/$SHAPE.md"
      [ -f "$_guard" ] || { echo "guard file not found: $_guard" >&2; exit 2; }
      APPEND_ARGS+=(--append-system-prompt-file "$_guard")
    fi
    ;;
esac
if [ "$DISCLOSURE" = "pointer" ]; then
  _mem="$_SDIR/auto-memory/MEMORY.md"
  [ -f "$_mem" ] || { echo "pointer disclosure requires $_mem" >&2; exit 2; }
  APPEND_ARGS+=(--append-system-prompt-file "$_mem")
fi
for _cf in "${CONTEXT_FILES[@]}"; do
  APPEND_ARGS+=(--append-system-prompt-file "$(cd "$(dirname "$_cf")" && pwd)/$(basename "$_cf")")
done

EFFORT_ARGS=()
if [ -n "$EFFORT" ]; then
  case "$EFFORT" in
    # `max` is permitted here. The CLAUDE.md ban on `max` is an Anthropic
    # cost finding; it has never been measured on DeepSeek, whose output is
    # ~500x cheaper, so the ban does not carry over.
    low|medium|high|xhigh|max) EFFORT_ARGS=(--effort "$EFFORT") ;;
    *) echo "invalid effort '$EFFORT' (low|medium|high|xhigh|max)" >&2; exit 2 ;;
  esac
fi

# ANTHROPIC_AUTH_TOKEN replaces subscription auth FOR THIS CHILD ONLY.
# ANTHROPIC_API_KEY is blanked so a stray parent value cannot win the auth race.
# The subshell cd makes -d the child's WORKING DIRECTORY, not merely an added
# dir: the harness the arm loads (.claude/, CLAUDE.md, hooks) is resolved from
# cwd, so a benchmark arm must run FROM its arm root or it silently loads the
# live repo's harness instead of the tagged one.
VERBOSE_ARGS=()
[ "$FORMAT" = "stream-json" ] && VERBOSE_ARGS=(--verbose)  # CLI requires it with --print

EXTRA_ARGS=()
[ "$PERSIST" -eq 1 ] || EXTRA_ARGS+=(--no-session-persistence)
[ -n "$RESUME" ] && EXTRA_ARGS+=(--resume "$RESUME")
[ -n "$PERM_MODE" ] && EXTRA_ARGS+=(--permission-mode "$PERM_MODE")
[ -n "$SCHEMA_FILE" ] && EXTRA_ARGS+=(--json-schema "$(cat "$SCHEMA_FILE")")

# PARENT-ENV SCRUB (measured 2026-08-04). A `claude` child inherits the parent
# session's CLAUDE_*/ANTHROPIC_* vars. `CLAUDE_CODE_ENTRYPOINT=claude-desktop`
# (exported by every Bash tool call inside a Claude Desktop session) makes the
# child authenticate through the HOST's subscription OAuth and ignore
# ANTHROPIC_AUTH_TOKEN entirely — the DeepSeek key is discarded, the host's
# rotating OAuth token goes to DeepSeek, and the endpoint answers 401 naming a
# "key" that matches nothing in the env file (bisected to this single var; the
# tail differs on every run because the token rotates). Launching from a
# terminal CLI never showed it, since that entrypoint is `cli`.
#
# Scrub is DYNAMIC, not a named blocklist: any future host var is caught too.
# Order matters — `env -u X X=v` unsets then re-sets, so the explicit
# assignments below still win.
SCRUB=()
while IFS='=' read -r _name _; do
  case "$_name" in CLAUDE*|ANTHROPIC*) SCRUB+=(-u "$_name") ;; esac
done < <(env)

# Subagent/background-model names are pinned to the REQUESTED model. Left
# unpinned, a spawned agent's Anthropic name reaches the compat layer, which
# aliases full `claude-*` ids BY TIER: claude-opus-* -> V4 Pro (the expensive
# one, billing-confirmed 2026-08-03), claude-sonnet-*/haiku-*/fable-* -> V4 Flash.
# Pinning removes the tier lottery entirely. Belt to the -x suspenders.
run_claude() {
  cd "$RUN_CWD" && env \
    ${SCRUB[@]+"${SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ${DS_CONFIG_DIR:+CLAUDE_CONFIG_DIR="$DS_CONFIG_DIR"} \
    ANTHROPIC_BASE_URL="$BASE_URL" \
    ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_KEY" \
    ANTHROPIC_API_KEY= \
    ANTHROPIC_SMALL_FAST_MODEL="$MODEL" \
    CLAUDE_CODE_SUBAGENT_MODEL="$MODEL" \
    CLAUDE_CODE_SIDECAR=deepseek \
    CLAUDE_CODE_MAX_CONTEXT_TOKENS="${MAX_CONTEXT_TOKENS:-1000000}" \
    ${SHAPE:+CLAUDE_CODE_SIDECAR_SHAPE="$SHAPE"} \
    ${TIMEOUT:+timeout} ${TIMEOUT:+"$TIMEOUT"} \
    claude -p "$PROMPT" \
      --model "$MODEL" \
      ${EFFORT_ARGS[@]+"${EFFORT_ARGS[@]}"} \
      --output-format "$FORMAT" \
      ${VERBOSE_ARGS[@]+"${VERBOSE_ARGS[@]}"} \
      --allowedTools "$TOOLS" \
      ${DISALLOWED:+--disallowedTools "$DISALLOWED"} \
      ${APPEND_ARGS[@]+"${APPEND_ARGS[@]}"} \
      ${MAX_TURNS:+--max-turns "$MAX_TURNS"} \
      ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
      --add-dir "$WORKDIR" \
      ${ADD_DIRS[@]+"${ADD_DIRS[@]}"}
}

if [ -n "$PROGRESS" ]; then
  # Tee event lines live so a Monitor can follow the run; capture for -R too.
  OUTPUT="$(run_claude | tee "$PROGRESS")"
else
  OUTPUT="$(run_claude)"
fi
rc=$?
printf '%s\n' "$OUTPUT"
[ $rc -eq 124 ] && echo "sidecar timed out after ${TIMEOUT}s" >&2

if [ -n "$RECORD" ]; then
  HARNESS_BASE="$(git -C "$WORKDIR" describe --tags --exact-match HEAD 2>/dev/null || echo unknown)"
  HARNESS_SESSION="$(git -C "$WORKDIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  # Payload goes via temp file: `python3 -` reads its PROGRAM from stdin, so a
  # heredoc and a data pipe cannot share the channel.
  RAW_TMP="$(mktemp)"
  printf '%s' "$OUTPUT" > "$RAW_TMP"
  # `python3` may be native Windows Python, which cannot resolve an MSYS path —
  # /tmp and /c/... open as FileNotFoundError and the record is silently lost.
  # Hand it native paths where cygpath exists; elsewhere this is the identity.
  to_native() {
    [ -z "$1" ] && return 0
    if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
  }
  RAW_V="$(to_native "$RAW_TMP")" EFFORT_V="$EFFORT" MODEL_V="$MODEL" RC_V="$rc" \
    HB_V="$HARNESS_BASE" HS_V="$HARNESS_SESSION" REC_V="$(to_native "$RECORD")" \
    SCHEMA_V="$(to_native "$SCHEMA_FILE")" LEDGER_V="$LEDGER" LABEL_V="$LABEL" \
    DISCLOSURE_V="$DISCLOSURE" \
    REGCLI_V="$REGISTRY_CLI" python3 - <<'PYEOF'
import json, os, sys, time
raw = open(os.environ["RAW_V"], encoding="utf-8", errors="replace").read()
try:
    data = json.loads(raw)
except Exception:
    # stream-json: one event per line; the record source is the result event
    data = {}
    for line in raw.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if isinstance(o, dict) and o.get("type") == "result":
            data = o
usage = data.get("usage") or {}
mu = {k: v for k, v in (data.get("modelUsage") or {}).items() if isinstance(v, dict)}
served = sorted({v.get("canonicalModel") for v in mu.values() if v.get("canonicalModel")})
# Token totals from modelUsage sums, NOT the top-level usage block — the
# latter covers the main loop only and undercounts any session with nested
# loops (measured 2026-08-03: a spawned-subagent session's usage block
# omitted the subagent's ~1M tokens that modelUsage carried).
if mu:
    fresh = sum(v.get("inputTokens") or 0 for v in mu.values())
    cache_read = sum(v.get("cacheReadInputTokens") or 0 for v in mu.values())
    out_tok = sum(v.get("outputTokens") or 0 for v in mu.values())
else:
    fresh = usage.get("input_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0
    out_tok = usage.get("output_tokens") or 0
# costUSD is COMPUTED from raw token counts at the per-model rates in
# .claude/reference/external_models.json (the ONE price home - never re-author a
# rate here, in code or in a comment). NEVER read total_cost_usd: Claude Code
# prices DeepSeek tokens on its Anthropic table, so that field is inflated by the
# Anthropic-to-DeepSeek rate ratio - ~39x on flash, proportionally smaller on pro.
#
# Keyed on servedModel, falling back to requestedModel when modelUsage is absent.
# Hardcoded flash rates here under-reported a real V4 Pro run by exactly 3.11x
# (measured 2026-08-12: recorded $0.007934, true cost $0.024652).
sys.path.insert(0, os.path.join(os.path.dirname(os.environ["REGCLI_V"])))
import model_registry as _reg
_cost_model = (served[0] if len(served) == 1 else None) or os.environ["MODEL_V"]
try:
    cost = _reg.price_run(_cost_model, fresh, cache_read, out_tok)
    cost_basis = _cost_model
except Exception as exc:
    # Never lose the record over pricing: keep the tokens, flag the gap.
    cost, cost_basis = None, f"UNPRICED ({exc})"
record = {
    "label": os.environ.get("LABEL_V") or None,
    "servedModel": served[0] if len(served) == 1 else (served or None),
    "requestedModel": os.environ["MODEL_V"],
    "effort": os.environ["EFFORT_V"] or None,
    "disclosureTier": os.environ.get("DISCLOSURE_V") or None,
    "harnessBase": os.environ["HB_V"],
    "harnessSession": os.environ["HS_V"],
    "inputTokens": fresh,
    "cacheReadTokens": cache_read,
    "outputTokens": out_tok,
    "costUSD": round(cost, 6) if cost is not None else None,
    "costBasis": cost_basis,
    "numTurns": data.get("num_turns"),
    "durationMs": data.get("duration_ms"),
    "stopReason": data.get("stop_reason") or data.get("subtype"),
    "apiErrorStatus": data.get("api_error_status"),
    "permissionDenials": data.get("permission_denials") or [],
    "exitCode": int(os.environ["RC_V"]),
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
# DNF early-warning: per-request context approaching the 200K window
if (fresh + cache_read) > 160_000:
    record["contextCeilingWarning"] = True
# schemaValid: with -S, the result text must parse as JSON. (Key-level
# conformance is the caller's check; this catches the prose-instead-of-JSON
# failure mode cheaply.)
if os.environ.get("SCHEMA_V"):
    try:
        json.loads(data.get("result") or "")
        record["schemaValid"] = True
    except Exception:
        record["schemaValid"] = False
with open(os.environ["REC_V"], "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2)
ledger = os.environ.get("LEDGER_V", "")
if ledger == "__default__":
    ledger = os.path.expanduser("~/.claude/deepseek_spend.jsonl")
if ledger:
    try:
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # spend ledger is advisory; never fail the run over it
PYEOF
  rm -f "$RAW_TMP"
fi

# A 401 here almost never means a bad DEEPSEEK_API_KEY — the endpoint reports
# whatever credential actually arrived, and a host-auth leak sends a rotating
# OAuth token whose tail differs every run. Say so, so nobody re-diagnoses it.
if [ "$rc" -ne 0 ] && printf '%s' "${OUTPUT:-}" | grep -q '"api_error_status" *: *401'; then
  cat >&2 <<'DIAG'
[sidecar] 401 from the DeepSeek endpoint.
  Before suspecting the key: compare the key tail the error names against the
  tail in ~/.env.ai-worker.cmd. If they differ (or change between runs), the
  child authenticated with host subscription OAuth instead of the env token —
  a CLAUDE_*/ANTHROPIC_* var leaked past the scrub in run_claude().
  Verify the key independently:
    curl -s -o /dev/null -w '%{http_code}\n' https://api.deepseek.com/v1/models \
      -H "Authorization: Bearer <key>"
DIAG
fi

exit $rc
