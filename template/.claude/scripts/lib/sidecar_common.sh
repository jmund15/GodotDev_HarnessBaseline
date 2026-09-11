#!/usr/bin/env bash
# sidecar_common.sh — the shared half of every sidecar launcher.
#
# Sourced, never executed. A launcher supplies what is PROVIDER-specific (credential
# discovery, endpoint, how the child is actually spawned) and calls into here for
# everything that must be identical across providers: the flag surface, validation,
# the gate ladder, the entrypoint scrub, and the run-record.
#
# The flag surface is uniform BY CONSTRUCTION rather than by convention. A launcher that
# re-implemented getopts would drift one flag at a time, and the drift is invisible until
# a dispatch silently ignores an option the caller passed.
#
# Contract for a launcher:
#   SC_TRANSPORT     required, before sourcing — registry transport name
#   SC_ROOT          set here — absolute path to .claude/
#   sc_parse_flags "$@" ; shift "$SC_SHIFT"    then read the SC_* variables
#   sc_resolve_model                            fills SC_MODEL/SC_ALIAS/gates from the registry
#   sc_gate_availability / sc_gate_band / sc_gate_provider_band / sc_gate_balance
#   sc_build_disclosure                         fills SC_RUN_CWD and SC_APPEND_ARGS
#   sc_scrub_env                                fills SC_SCRUB for a `claude` child
#   sc_write_record <raw-output> <exit-code>
#
# EXIT CODES are a shared contract, keyed on by several consumers — never renumber:
#   0 ok · 2 bad usage/unresolvable model/unusable registry · 3 credential missing
#   4 child CLI missing · 5 band gate refusal (-A overrides) · 6 balance floor unmet
#   (-A does NOT override) · 7 model UNAVAILABLE in the registry (-U overrides)
#   8 provider quota-band ceiling exceeded (-A overrides)
#
# 8 is NOT 7. "This provider is burning its allowance too fast" and "this transport is
# not in the roster" have different fixes and different overrides; one code for both
# would send a reader to the wrong one.

SC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# A launcher runs for up to an hour, and bash reads a script INCREMENTALLY: editing the launcher file
# mid-run corrupts the live launcher at its next read (measured 2026-09-08: an edit to
# codex_proxy_sidecar.sh killed a running luna curator's resume loop with "unexpected EOF", losing a
# resumable session). Every launcher re-execs itself from a snapshot copy before doing any work;
# SC_LAUNCHER_DIR keeps lib resolution on the real tree. This lib is sourced whole, so editing it is safe.
sc_reexec_snapshot() {
  [ -n "${SC_SNAPSHOT_OF:-}" ] && return 0
  local launcher="${BASH_SOURCE[1]}" snapdir snap
  snapdir="${TEMP:-${TMPDIR:-/tmp}}/sidecar-snapshots"
  mkdir -p "$snapdir" 2>/dev/null || return 0
  # Age alone is not evidence a snapshot is dead — a >24h run is still reading its own copy, and
  # the owning pid is in the filename. Sweep only the ones whose process is gone.
  local _old _p
  for _old in "$snapdir"/*.sh; do
    [ -e "$_old" ] || continue
    [ -n "$(find "$_old" -mmin +1440 2>/dev/null)" ] || continue
    _p="${_old%.sh}"; _p="${_p##*.}"
    kill -0 "$_p" 2>/dev/null && continue
    rm -f "$_old" 2>/dev/null
  done
  snap="$snapdir/$(basename "$launcher").$$.sh"
  cp "$launcher" "$snap" 2>/dev/null || return 0   # cannot snapshot -> run live, as before
  SC_SNAPSHOT_OF="$launcher" SC_LAUNCHER_DIR="$SC_LAUNCHER_DIR" exec bash "$snap" "$@"
}
SC_REGISTRY_CLI="$SC_ROOT/tools/model_registry.py"
SC_BUDGET_HOOK="$SC_ROOT/hooks/budget_posture.py"

# ------------------------------------------------------------------------ defaults
# Every launcher starts from the same values. A provider that needs a different default
# overrides the variable AFTER sourcing and BEFORE sc_parse_flags, so the flag still wins.
SC_MODEL="${SC_MODEL:-}"
SC_AUTHORIZED=0
SC_UNSUSPEND=0
SC_EFFORT=""
SC_TOOLS="Read,Glob,Grep"
SC_MAX_TURNS=""   # empty = NO turn cap (user directive 2026-08-03: a cap discards completed
                  # work; -T wall-clock is the runaway guard). Pass -n N to cap explicitly.
SC_FORMAT="json"
SC_WORKDIR="$PWD"
SC_PROMPT_FILE=""
SC_TIMEOUT=""     # empty = NO wall-clock kill. A timeout only ever WAKES the orchestrator to
                  # check for a hang via -P heartbeat staleness; it never halts a long run.
SC_RECORD=""
SC_DISALLOWED="Task,Agent,Workflow"
SC_PROGRESS=""
SC_SCHEMA_FILE=""
# PERSIST BY DEFAULT. This was 0, with `-s` opting in, and the opt-in is the wrong shape: losing a
# transcript costs a whole paid run, while writing one costs disk. A default nobody can forget beats
# a flag every caller must remember -- measured twice on 2026-08-20, once when a T8 cell died at
# turn 122 and once when a t2-luna-medium cell died at turn 42 (WebSocket os error 10054), both
# unrecoverable BY CONFIGURATION. The first was "fixed" by adding `-s` to one dispatcher; the
# sibling dispatcher still lacked it three hours later, which is what a per-caller fix buys you.
# `-s` remains accepted and is now a no-op, so every existing caller keeps working unchanged.
# `-N` opts out for a caller that genuinely wants no transcript on disk.
SC_PERSIST=1
SC_RESUME=""
SC_PERM_MODE="auto"   # -t pre-approves; it does not grant. Under auto, read-shaped MCP tools
                      # (ai-worker, semantic-search, godot, LSP) run off-list with no denial
                      # (measured 2026-09-03). Writes need the list: a write delegate whose
                      # Bash/Edit falls outside it hard-fails.
SC_LEDGER="__default__"
SC_LABEL=""
SC_SHAPE=""
SC_ADD_DIRS=()
SC_DISCLOSURE="full"
SC_CONTEXT_FILES=()
SC_PROMPT=""
SC_SHIFT=0

# One spend ledger across providers. A per-provider file would need a second reader in
# /orchestration_metrics, and the one that got forgotten would drop its runs silently;
# every record carries requestedModel and costBasis, so the rows stay separable.
SC_LEDGER_DEFAULT="$HOME/.claude/deepseek_spend.jsonl"

# ------------------------------------------------------------------- flag parsing
sc_parse_flags() {
  local opt
  OPTIND=1
  while getopts "m:e:t:n:o:d:f:T:R:x:P:S:sNr:p:L:l:a:G:AUD:C:Z:" opt; do
    case "$opt" in
      m) SC_MODEL="$OPTARG" ;;
      A) SC_AUTHORIZED=1 ;;
      U) SC_UNSUSPEND=1 ;;
      e) SC_EFFORT="$OPTARG" ;;
      t) SC_TOOLS="$OPTARG" ;;
      n) SC_MAX_TURNS="$OPTARG"
         # A cap does not bound cost, it DISCARDS work: the run stops mid-tool-use with every
         # token already spent and no deliverable. -T is the runaway guard. Warn at dispatch
         # because the failure otherwise surfaces only as a puzzling exit 1 much later.
         echo "[sidecar] WARNING: -n $OPTARG caps turns. Hitting it discards a paid, unfinished run (stopReason=tool_use, exit 1). Prefer -T for runaway protection." >&2 ;;
      o) SC_FORMAT="$OPTARG" ;;
      d) SC_WORKDIR="$OPTARG" ;;
      f) SC_PROMPT_FILE="$OPTARG" ;;
      T) SC_TIMEOUT="$OPTARG" ;;
      R) SC_RECORD="$OPTARG" ;;
      x) SC_DISALLOWED="$OPTARG" ;;
      P) SC_PROGRESS="$OPTARG"; SC_FORMAT="stream-json" ;;
      S) SC_SCHEMA_FILE="$OPTARG" ;;
      s) SC_PERSIST=1 ;;   # retained for compatibility; persistence is now the default
      N) SC_PERSIST=0 ;;   # explicit opt-OUT of session persistence
      r) SC_RESUME="$OPTARG"; SC_PERSIST=1 ;;
      p) SC_PERM_MODE="$OPTARG" ;;
      L) SC_LEDGER="$OPTARG" ;;
      l) SC_LABEL="$OPTARG" ;;
      a) SC_ADD_DIRS+=(--add-dir "$OPTARG") ;;
      G) SC_SHAPE="$OPTARG" ;;
      D) SC_DISCLOSURE="$OPTARG" ;;
      C) SC_CONTEXT_FILES+=("$OPTARG") ;;
      Z) SC_STALL_SEC="$OPTARG" ;;
      *) echo "bad usage; see header" >&2; exit 2 ;;
    esac
  done
  SC_SHIFT=$((OPTIND - 1))
}

sc_read_prompt() {
  if [ -n "$SC_PROMPT_FILE" ]; then
    [ -f "$SC_PROMPT_FILE" ] || { echo "prompt file not found: $SC_PROMPT_FILE" >&2; exit 2; }
    SC_PROMPT="$(cat "$SC_PROMPT_FILE")"
  else
    SC_PROMPT="${*:-}"
  fi
  [ -n "$SC_PROMPT" ] || { echo "empty prompt" >&2; exit 2; }
}

# ------------------------------------------------------------------ shared validation
# Ordered to fail on the cheapest, most-likely-wrong input first, and to fail BEFORE any
# dispatch: an arm that runs and then discards its provenance is unscoreable.
sc_validate_common() {
  # -o is a FORMAT, not a file path — the misuse every new caller makes. The payload goes to
  # stdout; capturing it is the caller's redirect. Validate the value before anything spends.
  case "$SC_FORMAT" in
    text|json|stream-json) ;;
    *) echo "-o is the OUTPUT FORMAT (text|json|stream-json; default json), not a file path. The result payload goes to stdout — redirect it to capture (… -o json > result.json). Got: -o $SC_FORMAT" >&2; exit 2 ;;
  esac
  if [ -n "$SC_RECORD" ]; then
    if ! : >> "$SC_RECORD" 2>/dev/null; then
      echo "run-record path not writable: $SC_RECORD" >&2; exit 2
    fi
    case "$SC_FORMAT" in
      json|stream-json) ;;
      *) echo "-R requires -o json or stream-json (record is parsed from the result payload)" >&2; exit 2 ;;
    esac
  fi
  if [ -n "$SC_SCHEMA_FILE" ] && [ ! -f "$SC_SCHEMA_FILE" ]; then
    echo "schema file not found: $SC_SCHEMA_FILE" >&2; exit 2
  fi
  if [ -n "$SC_PERM_MODE" ]; then
    case "$SC_PERM_MODE" in
      auto|acceptEdits|dontAsk|manual|plan|bypassPermissions|default) ;;
      *) echo "invalid permission mode '$SC_PERM_MODE' (auto|acceptEdits|dontAsk|manual|plan|bypassPermissions)" >&2; exit 2 ;;
    esac
  fi
  if [ -n "$SC_SHAPE" ]; then
    case "$SC_SHAPE" in
      any|survey|review|author) ;;
      *) echo "invalid guard shape '$SC_SHAPE' (any|survey|review|author)" >&2; exit 2 ;;
    esac
  fi
  if [ -n "$SC_DISCLOSURE" ]; then
    case "$SC_DISCLOSURE" in
      bare|pointer|full) ;;
      *) echo "invalid disclosure tier '$SC_DISCLOSURE' (bare|pointer|full)" >&2; exit 2 ;;
    esac
  fi
  local _cf
  for _cf in ${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"}; do
    [ -f "$_cf" ] || { echo "context file not found: $_cf" >&2; exit 2; }
  done
}

sc_validate_effort() {
  [ -n "$SC_EFFORT" ] || return 0
  case "$SC_EFFORT" in
    low|medium|high|xhigh|max) ;;
    *) echo "invalid effort '$SC_EFFORT' (low|medium|high|xhigh|max)" >&2; exit 2 ;;
  esac
}

# --------------------------------------------------------------- model resolution
# One registry call yields everything the dispatch path needs. Python startup dominates
# on Windows, so several `resolve --field` calls would cost most of a second for what is
# one dict lookup.
sc_resolve_model() {
  local fields
  if ! fields="$(python3 "$SC_REGISTRY_CLI" sidecar-fields "$SC_MODEL" 2>&1)"; then
    {
      echo "[sidecar] cannot resolve -m '$SC_MODEL':"
      echo "  $fields"
      echo "  registry: $SC_REGISTRY_CLI"
      echo "  This is the SPENDING consumer, so it fails closed: no fallback to a"
      echo "  hardcoded id, because dispatching at a tier nobody chose costs money."
    } >&2
    exit 2
  fi
  IFS='|' read -r SC_MODEL SC_ALIAS SC_AUTH_TIER SC_MIN_BAND SC_MIN_BALANCE SC_FRESH_RATE \
    SC_BALANCE_URL SC_TRANSPORT_STATE SC_TRANSPORT_REASON SC_COST_MODEL SC_MAX_PROVIDER_BAND \
    SC_API_MODE \
    <<< "$fields"
}

# ------------------------------------------------------- gate 1: transport suspension
# Checked FIRST, ahead of every other gate, because it outranks them: a suspended transport
# must not be dispatched at any band or any balance. The band gate answers "is this transport
# the cheaper currency right now"; this answers "is it available at all", and a NO here makes
# the band question moot.
#
# -A does NOT override it. -A authorizes spending past a BAND floor, which is a judgement
# about relative currency cost that an agent may legitimately make. A suspension is a standing
# decision by the user about a provider, so overriding it takes its own flag (-U).
sc_gate_availability() {
  [ "$SC_TRANSPORT_STATE" = "unavailable" ] || return 0
  if [ "$SC_UNSUSPEND" -eq 1 ]; then
    echo "[sidecar] -U: availability gate overridden for $SC_ALIAS ($SC_MODEL)." >&2
    return 0
  fi
  # States the exclusion and its reason. It does NOT name a replacement: which model to use
  # instead depends on the task's breadth, its complexity and the budget, none of which this
  # script knows, and a hardcoded redirect would make that call on the dispatcher's behalf.
  {
    echo "[sidecar] $SC_ALIAS ($SC_MODEL) is UNAVAILABLE: ${SC_TRANSPORT_REASON:-no reason recorded}"
    echo "  Not a band or balance refusal (5 and 6). Re-select from the available roster:"
    echo "    python3 .claude/tools/model_registry.py available"
    echo "  Re-enable: model_registry.py set-status $SC_TRANSPORT available   ·   override once: -U"
  } >&2
  exit 7
}

# ------------------------------------------------- benchmark-arm escape guard (settings merge)
# Every launcher passes its child settings through this. When run_grid.sh exports BENCH_ARM=1 the
# child gets hooks/bench_arm_escape_guard.py on PreToolUse (vault/holdout reads denied outside
# BENCH_ARM_INPUT_DIR); otherwise the settings pass through unchanged. One --settings flag per
# child: the CLI keeps only the last one, so fragments are merged here, never appended.
sc_settings_with_bench_guard() { # <settings-json-or-empty> -> merged json on stdout ("" if nothing to pass)
  python3 - "$1" "$SC_ROOT/hooks/bench_arm_escape_guard.py" "${BENCH_ARM:-}" <<'PY'
import json, sys
base = json.loads(sys.argv[1]) if sys.argv[1].strip() else {}
if sys.argv[3] == "1":
    hooks = base.setdefault("hooks", {})
    hooks.setdefault("PreToolUse", []).append({
        "matcher": "Read|Glob|Grep|Bash|Edit|Write|LS|MultiEdit|NotebookEdit",
        "hooks": [{"type": "command", "command": 'python3 "%s"' % sys.argv[2], "timeout": 10}],
    })
print(json.dumps(base) if base else "")
PY
}

# --------------------------------------------------------------- gate 2: our own band
# Band NAMES are ordered by BURN RATE, not by headroom: Surplus is the LOW-pressure band
# ("plan quota going unused - spend it"), Ahead/Hot are HIGH-pressure ones. Every message
# prints the pressure NUMBER with the name, because the name alone reads backwards.
sc_gate_band() {
  local out rc sat_rc
  out="$(python3 "$SC_BUDGET_HOOK" --band --pressure 2>/dev/null)"; rc=$?
  SC_BAND="${out%%$'\t'*}"
  SC_BAND_P="${out##*$'\t'}"
  [ "$SC_BAND_P" = "$SC_BAND" ] && SC_BAND_P="?"

  if [ "$SC_AUTHORIZED" -eq 1 ]; then
    echo "[sidecar] -A: band gate bypassed for $SC_ALIAS ($SC_MODEL); band=$SC_BAND pressure=$SC_BAND_P, floor=$SC_MIN_BAND" >&2
    return 0
  fi
  if [ "$rc" -ne 0 ]; then
    # WHY it is unreadable decides the fix, so ask rather than asserting a cause. Some
    # entrypoints send no rate_limits at all: the writer is healthy and "restore the
    # statusline" would be the wrong instruction.
    local why
    why="$(python3 "$SC_BUDGET_HOOK" --why 2>/dev/null)"
    # A floor the LOWEST band already satisfies is vacuous: no reading could fail it, so an
    # unreadable band refuses nothing. ($0 tiers and plan-quota rows at the Surplus floor.)
    if python3 "$SC_REGISTRY_CLI" band-satisfies Surplus "$SC_MIN_BAND" >/dev/null 2>&1; then
      echo "[sidecar] band UNREADABLE (${why:-no cc-cachestat state file}); floor $SC_MIN_BAND is the lowest band, so the gate passes." >&2
      SC_BAND="unknown"; SC_BAND_P="?"
      return 0
    fi
    # `unknown` is NOT a band. Treated as not-satisfied, and said so explicitly:
    # "unreadable" and "too low" are different problems with different fixes.
    {
      echo "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): budget band is UNREADABLE (not low)."
      echo "  $SC_BUDGET_HOOK --band exited $rc."
      if [ -n "$why" ]; then
        echo "  Cause: $why."
        echo "  The gate has no input and cannot certify the floor of $SC_MIN_BAND. This will"
        echo "  not clear on its own - dispatch deliberately with -A."
      else
        echo "  No cc-cachestat state file was findable. That file is written by"
        echo "  ~/.claude/statusline.py every turn - if it is absent, the gate has no input"
        echo "  and cannot certify the floor of $SC_MIN_BAND."
        echo "  Override deliberately with -A, or restore the statusline."
      fi
    } >&2
    exit 5
  fi
  python3 "$SC_REGISTRY_CLI" band-satisfies "$SC_BAND" "$SC_MIN_BAND"; sat_rc=$?
  [ "$sat_rc" -eq 0 ] && return 0
  {
    if [ "$sat_rc" -eq 2 ]; then
      echo "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): band name '$SC_BAND' is not in quota_bands.BANDS."
    else
      echo "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): band $SC_BAND (7d pressure $SC_BAND_P) is below the floor $SC_MIN_BAND."
      echo "  Bands rank by BURN RATE: Surplus < On pace < Ahead < Hot. A low band means"
      echo "  plan quota is going unused - spend that first; it expires."
    fi
    echo "  Override: re-run with -A. Policy home: .claude/skills/orchestration/SKILL.md section 5."
  } >&2
  exit 5
}

# ------------------------------------------------- gate 3: the PROVIDER's own quota band
# Only a plan-quota transport has one. This is a CEILING, the inverse of the floor above:
# the refusal case is the provider running HOT, i.e. burning its own allowance faster than
# the window can carry. A floor here would permit dispatch precisely when the allowance is
# most exhausted.
#
# `unknown` does NOT refuse. The probe reports band-or-unknown, and an unreadable provider
# band is an advisory failure, not evidence of exhaustion; refusing on it would make every
# probe hiccup look like a spent quota. The band FLOOR above fails closed because its input
# is local and always available; this one depends on a network round-trip.
sc_gate_provider_band() {
  [ "$SC_COST_MODEL" = "plan-quota" ] || return 0
  [ -n "$SC_MAX_PROVIDER_BAND" ] || return 0

  local probe reading band
  probe="$SC_ROOT/scripts/${SC_TRANSPORT}_quota_probe.py"
  [ -f "$probe" ] || return 0
  reading="$(python3 "$probe" 2>/dev/null)" || {
    echo "[sidecar] $SC_TRANSPORT quota probe failed; provider-band gate SKIPPED (not a refusal)." >&2
    return 0
  }
  band="$(READING="$reading" python3 -c 'import json,os;print((json.loads(os.environ["READING"]).get("band") or ""))' 2>/dev/null)"
  if [ -z "$band" ]; then
    echo "[sidecar] $SC_TRANSPORT quota band is UNKNOWN (window not computable); gate SKIPPED." >&2
    return 0
  fi
  SC_PROVIDER_BAND="$band"
  local ceil_rc
  python3 "$SC_REGISTRY_CLI" band-within-ceiling "$band" "$SC_MAX_PROVIDER_BAND"; ceil_rc=$?
  [ "$ceil_rc" -eq 0 ] && return 0
  if [ "$ceil_rc" -ne 1 ]; then
    # 2 = a band name the registry does not know. That is a config fault, not evidence of
    # exhaustion, and this gate does not fail closed on advisory input.
    echo "[sidecar] provider-band comparison failed (rc=$ceil_rc); gate SKIPPED (not a refusal)." >&2
    return 0
  fi
  if [ "$SC_AUTHORIZED" -eq 1 ]; then
    echo "[sidecar] -A: provider-band ceiling bypassed for $SC_ALIAS; $SC_TRANSPORT band=$band, ceiling=$SC_MAX_PROVIDER_BAND" >&2
    return 0
  fi
  {
    echo "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): $SC_TRANSPORT's OWN quota band is $band, above the $SC_MAX_PROVIDER_BAND ceiling."
    echo "  This is the PROVIDER's allowance, not this session's (that gate passed): the account"
    echo "  is burning its plan quota faster than the window can carry."
    echo "  Read it: python3 .claude/scripts/${SC_TRANSPORT}_quota_probe.py"
    echo "  Override: re-run with -A."
  } >&2
  exit 8
}

# ---------------------------------------------------------------- gate 4: dollar balance
# `gated` models only, and only where the registry knows a balance endpoint — so it costs a
# plan-quota transport nothing. Applies ALWAYS, -A included: -A authorizes intent, it cannot
# conjure funds.
sc_gate_balance() {
  [ "$SC_AUTH_TIER" = "gated" ] || return 0
  [ -n "$SC_BALANCE_URL" ] || return 0
  local raw now
  raw="$(curl -s --max-time 15 "$SC_BALANCE_URL" -H "Authorization: Bearer $SC_CREDENTIAL" 2>/dev/null)"
  now="$(BAL_V="$raw" python3 -c '
import json, os, sys
try:
    infos = json.loads(os.environ["BAL_V"]).get("balance_infos") or []
    # total_balance is a STRING in this API ("6.74"), not a number.
    print(max(float(i["total_balance"]) for i in infos))
except Exception:
    sys.exit(1)
' 2>/dev/null)" || now=""
  if [ -z "$now" ]; then
    {
      echo "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): balance probe FAILED (not: balance low)."
      echo "  $SC_BALANCE_URL returned nothing parsable. Failing loud before spending, because"
      echo "  an unverified balance on a gated model is the case this gate exists for."
    } >&2
    exit 6
  fi
  if ! awk -v b="$now" -v f="$SC_MIN_BALANCE" 'BEGIN{exit !(b+0 >= f+0)}'; then
    echo "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): balance \$$now is below the \$$SC_MIN_BALANCE floor. -A does not override this." >&2
    exit 6
  fi
  echo "[sidecar] $SC_ALIAS preflight OK: band=$SC_BAND (pressure $SC_BAND_P, floor $SC_MIN_BAND), balance=\$$now (floor \$$SC_MIN_BALANCE), fresh-token rate \$$SC_FRESH_RATE/1M" >&2
}

# ----------------------------------------------------------------- cleanup registry
# One EXIT trap, many cleanups. A bare `trap ... EXIT` REPLACES whatever is installed, with no
# error and no warning, so a launcher that installed its own would silently disable the
# disclosure tier's scratch-dir removal -- leaking a directory per run with nothing pointing at
# the cause. Every cleanup registers here instead.
SC_CLEANUP=()
sc_run_cleanups() {
  local i
  # Reverse order: a later registration may depend on an earlier one's resource, so it unwinds
  # first.
  for (( i=${#SC_CLEANUP[@]}-1; i>=0; i-- )); do eval "${SC_CLEANUP[$i]}"; done
}
# ---------------------------------------------------------------- resume (universal)
# Re-invoke a child whose run ended on a rescuable ending, keeping the turns already paid for.
# RESUME IS NOT RETRY: --resume continues the same session; a fresh dispatch re-buys it. That is
# what keeps this inside the rule against auto-retrying a billed call rather than in breach of it.
# Policy and the two endings: reference/sidecar_dispatch.md §Resume. Classifier:
# tools/sidecar_resume_check.py (deterministic faults are rejected there, never resumed here).
#
# The launcher owns flag assembly, so it passes its own run function in; this owns the policy.
# Usage, after `rc=$?` and BEFORE the final `printf '%s\n' "$OUTPUT"`:
#     sc_resume_loop run_claude "$SC_PROGRESS"
# Reads and rewrites OUTPUT and rc in the caller's scope; exports SC_RESUME_SID for run_claude.
# ------------------------------------------------------------------ stall watchdog
# Stalls kill paid calls, never duration: a run that emits nothing for SC_STALL_SEC seconds is
# dead (an upstream that never answers, a classifier that never returns, a child wedged on a
# socket), and without this it sits until a human notices — measured 2026-09-08: three shells
# over an hour, one over two days, every one found by the owner, not the harness. The watch is
# on the -P progress stream (stream-json only: `-o json` emits nothing until the end, so there is
# nothing to watch and the watchdog stays off with a notice). -Z <sec> / SIDECAR_STALL_SEC
# override; 0 disables. A stall is recorded as a synthetic result event (terminal_reason
# "stall"), exit code 9, and is NOT auto-resumed: the record names the session id for a
# deliberate -r.
SC_STALL_SEC="${SIDECAR_STALL_SEC:-900}"
SC_STALLED=0

sc_kill_tree() {
  # $1 = the bash pid of the watched subshell; taskkill /T needs its Windows pid.
  local w
  w="$(ps -W 2>/dev/null | awk -v p="$1" '$1==p{print $4}')"
  [ -n "$w" ] && MSYS_NO_PATHCONV=1 taskkill /PID "$w" /T /F >/dev/null 2>&1
  kill "$1" 2>/dev/null
  return 0
}

# sc_run_watched <run_fn> [<progress-file>] -> sets OUTPUT and rc, appends to the progress file.
sc_run_watched() {
  local run_fn="$1" progress="${2:-}" tmp w now last idle line
  case "${SC_STALL_SEC:-0}" in
    ''|*[!0-9]*) echo "[sidecar] -Z/SIDECAR_STALL_SEC='${SC_STALL_SEC:-}' is not a whole number of seconds; refusing rather than running unwatched" >&2; exit 2 ;;
  esac
  if [ "${SC_STALL_SEC:-0}" -le 0 ] || [ -z "$progress" ] || [ "$SC_FORMAT" != "stream-json" ]; then
    [ "${SC_STALL_SEC:-0}" -gt 0 ] && [ -z "$progress" ] && \
      echo "[sidecar] no -P progress stream: the stall watchdog is OFF for this run (nothing to watch)" >&2
    if [ -n "$progress" ]; then OUTPUT="$("$run_fn" | tee -a "$progress")"; else OUTPUT="$("$run_fn")"; fi
    rc=$?
    return 0
  fi
  tmp="$(mktemp)"
  local started; started=$(date +%s)
  ( "$run_fn" | tee -a "$progress" > "$tmp" ) &
  w=$!
  while kill -0 "$w" 2>/dev/null; do
    sleep 10
    now=$(date +%s); last=$(stat -c %Y "$progress" 2>/dev/null || echo "$now")
    # A pre-existing -P file carries the PREVIOUS run's mtime: silence is measured from this run.
    [ "$last" -lt "$started" ] && last="$started"
    idle=$((now - last))
    if [ "$idle" -ge "$SC_STALL_SEC" ]; then
      echo "[sidecar] STALL: no output for ${idle}s (limit ${SC_STALL_SEC}s, -Z/SIDECAR_STALL_SEC) — killing the child tree" >&2
      sc_kill_tree "$w"
      SC_STALLED=1
      break
    fi
  done
  wait "$w" 2>/dev/null; rc=$?
  OUTPUT="$(cat "$tmp")"; rm -f "$tmp"
  if [ "$SC_STALLED" = 1 ]; then
    line="$(printf '{"type":"result","subtype":"stall","is_error":true,"terminal_reason":"stall","num_turns":0,"result":"STALLED: the sidecar watchdog killed the child after %ss without output (limit %ss). Resume deliberately with -r <session_id> if the work is worth keeping."}' "$idle" "$SC_STALL_SEC")"
    OUTPUT="${OUTPUT}"$'\n'"${line}"
    printf '%s\n' "$line" >> "$progress"
    rc=9
  fi
  return 0
}

sc_resume_loop() {
  local run_fn="$1" progress="${2:-}" det n=0 max="${SC_COMPACT_RESUMES:-3}" _r sid reason
  det="$SC_ROOT/tools/sidecar_resume_check.py"
  # stream-json only: `-o json` emits no compact_boundary and no per-event stream, so neither
  # ending is detectable. Say so once rather than failing silently.
  if [ "$SC_FORMAT" != "stream-json" ]; then
    echo "[sidecar] -o $SC_FORMAT: resume is unavailable (needs stream-json; -P sets it). A run that compacts or hits a transient upstream fault will end there and the record will store that ending as the deliverable." >&2
    return 0
  fi
  [ -f "$det" ] || { echo "[sidecar] resume classifier missing: $det — resume disabled this run" >&2; return 0; }

  [ "${SC_STALLED:-0}" = 1 ] && return 0
  while [ "$n" -lt "$max" ]; do
    _r="$(printf '%s\n' "$OUTPUT" | python3 "$det" 2>/dev/null)"
    [ -n "$_r" ] || return 0
    sid="${_r%%$'\t'*}"; reason="${_r#*$'\t'}"
    n=$((n + 1))
    # A caller-supplied -r already pushed its own --resume; drop it so the detected sid is the only one.
    if [ -n "${SC_RESUME:-}" ]; then
      local _keep=() _a
      for _a in ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}; do
        [ "$_a" = '--resume' ] || [ "$_a" = "$SC_RESUME" ] || _keep+=("$_a")
      done
      EXTRA_ARGS=(${_keep[@]+"${_keep[@]}"}); SC_RESUME=''
    fi
    echo "[sidecar] run ended on a $reason ending; resuming session $sid with the brief ($n/$max)" >&2
    SC_RESUME_SID="$sid"; export SC_RESUME_SID
    sc_run_watched "$run_fn" "$progress"
    [ "${SC_STALLED:-0}" = 1 ] && return 0
  done

  # Budget spent and STILL rescuable. Without this line a run that burned its resumes records
  # exactly like one that finished, which is the failure this whole path exists to prevent.
  _r="$(printf '%s\n' "$OUTPUT" | python3 "$det" 2>/dev/null)"
  if [ -n "$_r" ]; then
    reason="${_r#*$'\t'}"
    echo "[sidecar] RESUME BUDGET EXHAUSTED after $n resumes: the run STILL ended on a $reason ending, so the result below is that ending, NOT the deliverable. Raise SC_COMPACT_RESUMES, or shrink the read footprint (-D pointer, bounded Reads)." >&2
  fi
  return 0
}

sc_on_exit() {
  SC_CLEANUP+=("$1")
  trap sc_run_cleanups EXIT
}

# ------------------------------------------------------------------- disclosure tier
# Constructive disclosure: the child runs isolated and the tier appends exactly what it
# names. `full` = in-repo, no DISCLOSURE appends; `bare`/`pointer` = scratch run-cwd, with the
# repo grant unchanged so the child keeps Read access either way. One append rides EVERY tier
# and every model: the autonomy rail below. It is transport, not disclosure — a sidecar child
# has no human on the other end, and a `-D full` child in a sealed arm root reads that root's
# frozen hooks, so no guard file in this repo can reach it.
# Appended paths must be ABSOLUTE: the child resolves them against its own cwd, not this
# script's — a relative path breaks in the isolated tiers exactly where these appends are
# the only context.
sc_build_disclosure() {
  SC_RUN_CWD="$SC_WORKDIR"
  SC_APPEND_ARGS=()
  # Autonomy rail (user ruling 2026-09-04): three Luna benchmark cells ended on "which should I
  # lock, A or B?" / "please choose: fix, commit or abort" after 47–99 turns and were scored as
  # delivered-nothing. Any model that halts on a question gets the same text; it names the
  # consequence rather than forbidding the behaviour.
  local auto_tmp auto_arg
  auto_tmp="$(mktemp)"
  cat > "$auto_tmp" <<'SC_AUTONOMY'
[sidecar] You run UNATTENDED. No human reads anything you write until you exit, so a question in your final message is answered by nobody and the run is recorded as having delivered nothing. Never end on a question or a request for direction. When a choice is yours to make, make it, state it in the deliverable as an assumption, and finish. When a gate, a failing suite or a missing input blocks a step, record the blocker in the deliverable and still return everything the brief asked for.
SC_AUTONOMY
  sc_on_exit "rm -f \"$auto_tmp\" 2>/dev/null || :"
  auto_arg="$auto_tmp"
  command -v cygpath >/dev/null 2>&1 && auto_arg="$(cygpath -m "$auto_tmp")"
  SC_APPEND_ARGS+=(--append-system-prompt-file "$auto_arg")
  case "$SC_DISCLOSURE" in
    bare|pointer)
      SC_RUN_CWD="$(mktemp -d)"
      # The child (a Windows process) may still hold SC_RUN_CWD when EXIT fires;
      # a busy-dir rm must not fail the script.
      sc_on_exit 'rm -rf "$SC_RUN_CWD" 2>/dev/null || :'
      if [ -n "$SC_SHAPE" ]; then
        # Assemble exactly what the SessionStart hook would inline — any.md plus the shape
        # file, one tier — rather than appending the raw shape file. The raw file ships BOTH
        # tiers and omits any.md entirely, and these isolated tiers are precisely where no
        # hook fires to correct it. Strict matches the hook's own default.
        local guard_tmp guard_arg
        guard_tmp="$(mktemp)"
        python3 "$SC_ROOT/tools/guard_text.py" "$SC_SHAPE" strict > "$guard_tmp" || {
          rm -f "$guard_tmp" 2>/dev/null || :
          echo "could not assemble guards for shape '$SC_SHAPE' — refusing to dispatch unguarded" >&2
          exit 2
        }
        sc_on_exit "rm -f \"$guard_tmp\" 2>/dev/null || :"
        # mktemp hands back an MSYS path (/tmp/...). /tmp is Git Bash's own mount and does not
        # exist for the native child, which would read the flag as a missing file. The -C branch
        # below never hit this because cd/pwd yields /c/Users/..., which maps trivially.
        guard_arg="$guard_tmp"
        command -v cygpath >/dev/null 2>&1 && guard_arg="$(cygpath -m "$guard_tmp")"
        SC_APPEND_ARGS+=(--append-system-prompt-file "$guard_arg")
      fi
      ;;
  esac
  if [ "$SC_DISCLOSURE" = "pointer" ]; then
    local mem="$SC_ROOT/auto-memory/MEMORY.md"
    [ -f "$mem" ] || { echo "pointer disclosure requires $mem" >&2; exit 2; }
    SC_APPEND_ARGS+=(--append-system-prompt-file "$mem")
  fi
  local cf
  for cf in ${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"}; do
    SC_APPEND_ARGS+=(--append-system-prompt-file "$(cd "$(dirname "$cf")" && pwd)/$(basename "$cf")")
  done
}

# ------------------------------------------------------------------ claude binary
# `claude` lives in ~/.local/bin on this machine, which is on the Git Bash profile PATH but NOT
# on the Windows PATH that a NON-LOGIN shell inherits. So a launcher invoked interactively finds
# it and the same launcher invoked BY A PROGRAM -- the void-check preflight, a benchmark
# harness, any subprocess that spawns `bash script.sh` -- exits 4 claiming the CLI is not
# installed. Resolve explicitly, so the launcher behaves the same whoever starts it.
#
# PATH order first, so a user who has placed their own `claude` ahead of these still wins.
sc_claude_bin() {
  if [ -n "${CLAUDE_BIN:-}" ]; then printf '%s' "$CLAUDE_BIN"; return 0; fi
  local c
  for c in claude claude.cmd claude.exe; do
    if command -v "$c" >/dev/null 2>&1; then printf '%s' "$(command -v "$c")"; return 0; fi
  done
  for c in "$HOME/.local/bin/claude" "$HOME/AppData/Roaming/npm/claude.cmd"; do
    [ -x "$c" ] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

# ------------------------------------------------------------- --check model override
# `--check` is intercepted BEFORE getopts (it must answer with no prompt and no dispatch), so a
# `-m ALIAS` on a --check call never reaches flag parsing. Without this scan, `--check -m terra`
# silently probes the launcher's DEFAULT model and reports OK for a row it never touched -- the
# availability answer would be about luna while the caller asked about terra.
sc_check_model_override() {
  local prev="" a
  for a in "$@"; do
    [ "$a" = "-A" ] && SC_AUTHORIZED=1
    [ "$a" = "-U" ] && SC_UNSUSPEND=1
    if [ "$prev" = "-m" ]; then SC_MODEL="$a"; fi
    prev="$a"
  done
  return 0
}

# `--check` runs the SAME refusal gates a dispatch runs (availability 7, band floor 5, provider
# ceiling 8), so a refusal arrives synchronously — hooks/sidecar_dispatch_context.py runs this for
# every launch and denies the Bash call on a non-zero exit. Measured 2026-09-08: a backgrounded
# dispatch refused at exit 8 surfaced only when its task "completed" minutes later.
sc_check_gates() {
  sc_resolve_model
  sc_gate_availability
  sc_gate_balance
  [ -n "${SC_MIN_BAND:-}" ] && sc_gate_band
  sc_gate_provider_band
  return 0
}

# --------------------------------------------------------------- parent-env scrub
# PARENT-ENV SCRUB (measured 2026-08-04). A `claude` child inherits the parent session's
# CLAUDE_*/ANTHROPIC_* vars. `CLAUDE_CODE_ENTRYPOINT=claude-desktop` (exported by every Bash
# tool call inside a Claude Desktop session) makes the child authenticate through the HOST's
# subscription OAuth and ignore ANTHROPIC_AUTH_TOKEN entirely — the provider key is discarded,
# the host's rotating OAuth token goes to the provider, and the endpoint answers 401 naming a
# "key" that matches nothing in the env file. Launching from a terminal CLI never showed it,
# since that entrypoint is `cli`.
#
# This is what makes a proxy launcher's auth-isolation claim TRUE, not the placeholder token:
# without the scrub, real Anthropic credentials enter the request path regardless of what
# ANTHROPIC_AUTH_TOKEN says.
#
# Scrub is DYNAMIC, not a named blocklist: any future host var is caught too. Order matters —
# `env -u X X=v` unsets then re-sets, so a launcher's explicit assignments still win.
sc_scrub_env() {
  SC_SCRUB=()
  local name _
  while IFS='=' read -r name _; do
    case "$name" in CLAUDE*|ANTHROPIC*) SC_SCRUB+=(-u "$name") ;; esac
  done < <(env)
  # PASS-THROUGH after the unsets (`env -u X X=v` re-sets): the print-mode background-wait
  # ceiling. `claude -p` kills a child whose Workflow/background task is still running at 600 s and
  # exits 0 with no deliverable; a caller that sets the ceiling means it, and a child allowed the
  # Workflow tool needs it lifted (0 = wait; the -Z stall watchdog still bounds silence).
  local ceiling="${CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS:-}"
  if [ -z "$ceiling" ]; then
    case ",${SC_TOOLS:-}," in *,Workflow,*) ceiling=0 ;; esac
  fi
  [ -n "$ceiling" ] && SC_SCRUB+=(CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="$ceiling")
  return 0
}

# ------------------------------------------------------------------------ run record
# `python3` may be native Windows Python, which cannot resolve an MSYS path — /tmp and /c/...
# open as FileNotFoundError and the record is silently lost. Hand it native paths where
# cygpath exists; elsewhere this is the identity.
sc_to_native() {
  [ -z "$1" ] && return 0
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

# sc_write_record <raw-output> <exit-code>
sc_write_record() {
  local output="$1" rc="$2"
  [ -n "$SC_RECORD" ] || return 0
  local hb hs dsha raw_tmp ledger_default
  hb="$(git -C "$SC_WORKDIR" describe --tags --exact-match HEAD 2>/dev/null || echo unknown)"
  hs="$(git -C "$SC_WORKDIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  # bench_overlay_doctrine (benchmark_campaign/lib.sh) drops this marker when it refreshes
  # $SC_WORKDIR/.claude from BENCH_DOCTRINE_REF; absent on a root whose .claude/ is still the
  # task tag's own frozen copy (BENCH_DOCTRINE_REF=none, or a non-benchmark dispatch entirely).
  dsha="$(cat "$SC_WORKDIR/.claude/.bench-doctrine-sha" 2>/dev/null || echo "")"
  # Payload goes via temp file: `python3 -` reads its PROGRAM from stdin, so a heredoc and a
  # data pipe cannot share the channel.
  raw_tmp="$(mktemp)"
  printf '%s' "$output" > "$raw_tmp"
  ledger_default="$SC_LEDGER_DEFAULT"

  RAW_V="$(sc_to_native "$raw_tmp")" EFFORT_V="$SC_EFFORT" MODEL_V="$SC_MODEL" RC_V="$rc" \
    HB_V="$hb" HS_V="$hs" DSHA_V="$dsha" REC_V="$(sc_to_native "$SC_RECORD")" \
    SCHEMA_V="$(sc_to_native "$SC_SCHEMA_FILE")" LEDGER_V="$SC_LEDGER" LABEL_V="$SC_LABEL" \
    DISCLOSURE_V="$SC_DISCLOSURE" LEDGER_DEFAULT_V="$ledger_default" \
    TRANSPORT_V="$SC_TRANSPORT" COSTMODEL_V="$SC_COST_MODEL" \
    ATTEST_V="${SC_ATTESTED_MODEL:-}" PARSER_V="${SC_RECORD_PARSER:-claude}" \
    REGCLI_V="$SC_REGISTRY_CLI" CTXWIN_V="${SC_CONTEXT_TOKENS:-}" python3 - <<'PYEOF'
import json, os, sys, time
raw = open(os.environ["RAW_V"], encoding="utf-8", errors="replace").read()
parser = os.environ.get("PARSER_V") or "claude"

if parser == "codex":
    # `codex exec --json` emits a different stream from Claude Code's: JSONL of
    # thread.started / turn.started / item.completed / turn.completed, with the totals on
    # the terminal turn.completed event. Measured 2026-08-20 against codex-cli 0.148.0.
    # thread_id is tracked separately, not on `data`: turn.completed REPLACES data wholesale,
    # so anything stashed there by an earlier event is lost when the terminal event arrives.
    data, items, err, thread_id = {}, [], None, None
    for line in raw.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue          # the CLI interleaves plain-text log lines on stdout
        if not isinstance(o, dict):
            continue
        t = o.get("type")
        if t == "turn.completed":
            data = o
        elif t == "item.completed":
            items.append(o.get("item") or {})
        elif t in ("turn.failed", "error"):
            err = o.get("error") or {"message": o.get("message")}
        elif t == "thread.started":
            thread_id = o.get("thread_id")
    usage = data.get("usage") or {}
    # input_tokens is the TOTAL; cached_input_tokens is the subset served from cache. Fresh is
    # the difference, so a cache hit is not double-counted as fresh (measured: 14241 total /
    # 6912 cached on one run, 14251/0 on the next with the same prompt shape).
    total_in = usage.get("input_tokens") or 0
    cache_read = usage.get("cached_input_tokens") or 0
    fresh = max(0, total_in - cache_read)
    # output_tokens ALREADY includes reasoning_output_tokens (measured: 39 total with 31
    # reasoning and a 4-token answer). Summing them would inflate the count.
    out_tok = usage.get("output_tokens") or 0
    # codex exec never echoes the served model, so there is no servedModel to read — the
    # only identity evidence is that the requested id was accepted (a wrong or unsupported
    # id 400s rather than falling back silently).
    served = []
    _msg = next((i.get("text") for i in reversed(items)
                 if i.get("type") == "agent_message" and i.get("text")), None)
    data = {
        "result": _msg,
        "num_turns": None,
        "duration_ms": None,
        "stop_reason": "error" if err else "completed",
        "api_error_status": None,
        "permission_denials": [],
        "thread_id": thread_id,
        "reasoningOutputTokens": usage.get("reasoning_output_tokens"),
        "error": err,
    }
else:
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
    # Token totals from modelUsage sums, NOT the top-level usage block — the latter covers the
    # main loop only and undercounts any session with nested loops (measured 2026-08-03: a
    # spawned-subagent session's usage block omitted the subagent's ~1M tokens).
    if mu:
        fresh = sum(v.get("inputTokens") or 0 for v in mu.values())
        cache_read = sum(v.get("cacheReadInputTokens") or 0 for v in mu.values())
        out_tok = sum(v.get("outputTokens") or 0 for v in mu.values())
    else:
        fresh = usage.get("input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        out_tok = usage.get("output_tokens") or 0
rate_limit_info = None
if parser != "codex":
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if (isinstance(event, dict) and event.get("type") == "rate_limit_event"
                and isinstance(event.get("rate_limit_info"), dict)):
            rate_limit_info = event["rate_limit_info"]
# costUSD is COMPUTED from raw token counts at the per-model rates in
# .claude/reference/external_models.json (the ONE price home - never re-author a rate here,
# in code or in a comment). NEVER read total_cost_usd: Claude Code prices non-Anthropic tokens
# on its Anthropic table, so that field is inflated by the rate ratio - ~39x on flash.
#
# Keyed on servedModel, falling back to requestedModel when modelUsage is absent.
sys.path.insert(0, os.path.join(os.path.dirname(os.environ["REGCLI_V"])))
import model_registry as _reg
_cost_model = (served[0] if len(served) == 1 else None) or os.environ["MODEL_V"]
if os.environ.get("COSTMODEL_V") == "plan-quota":
    # A plan-quota run has NO dollar cost, so the field is null rather than 0.0 or absent.
    # 0.0 is indistinguishable from a measured free call; absent makes a consumer guess.
    # /orchestration_metrics and the spend ledger branch on costModel, never on costUSD
    # being numeric.
    cost, cost_basis = None, "plan-quota (no marginal cost)"
else:
    try:
        cost = _reg.price_run(_cost_model, fresh, cache_read, out_tok)
        cost_basis = _cost_model
    except Exception as exc:
        # Never lose the record over pricing: keep the tokens, flag the gap.
        cost, cost_basis = None, f"UNPRICED ({exc})"
def _turn1_context(sid):
    import glob
    if not sid:
        return None
    for path in glob.glob(os.path.join(os.path.expanduser('~'), '.claude', 'projects', '*', sid + '.jsonl')):
        try:
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    row = json.loads(line)
                    if row.get('type') == 'assistant':
                        u = (row.get('message') or {}).get('usage') or {}
                        return sum(u.get(k) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
        except Exception:
            return None
    return None
turn1 = _turn1_context(data.get('session_id'))
ctxwin = int(os.environ.get('CTXWIN_V') or 0) or None
if turn1:
    pct = f' ({100.0 * turn1 / ctxwin:.0f}% of {ctxwin})' if ctxwin else ''
    print(f'[sidecar] turn-1 context {turn1} tokens{pct}', file=sys.stderr)
record = {
    "label": os.environ.get("LABEL_V") or None,
    "transport": os.environ.get("TRANSPORT_V") or None,
    "costModel": os.environ.get("COSTMODEL_V") or None,
    "servedModel": served[0] if len(served) == 1 else (served or None),
    "requestedModel": os.environ["MODEL_V"],
    "effort": os.environ["EFFORT_V"] or None,
    "disclosureTier": os.environ.get("DISCLOSURE_V") or None,
    "harnessBase": os.environ["HB_V"],
    "harnessSession": os.environ["HS_V"],
    # The .claude/ actually loaded, distinct from harnessBase/harnessSession (which are the
    # CODE tag) -- absent means the root's .claude/ is that tag's own frozen native copy.
    "doctrineSha": os.environ.get("DSHA_V") or None,
    "inputTokens": fresh,
    "cacheReadTokens": cache_read,
    "outputTokens": out_tok,
    "costUSD": round(cost, 6) if cost is not None else None,
    "costBasis": cost_basis,
    "numTurns": data.get("num_turns"),
    # Wall clock, then the share of it spent waiting on the MODEL. The gap is local tooling --
    # MCP servers, searches, builds -- and it dominates: measured 63-89% across arms, so
    # durationMs alone rates a fast model as slow whenever it routes work to a slow local tool.
    # Any speed comparison between arms uses apiDurationMs; durationMs answers "how long until
    # I get my answer", which is a different question.
    "durationMs": data.get("duration_ms"),
    "apiDurationMs": data.get("duration_api_ms"),
    "ttftMs": data.get("ttft_ms"),
    "stopReason": data.get("stop_reason") or data.get("subtype"),
    "apiErrorStatus": data.get("api_error_status"),
    "permissionDenials": data.get("permission_denials") or [],
    "exitCode": int(os.environ["RC_V"]),
    # Turn-1 context = system prompt + hooks + brief, read from the child's own transcript (its
    # first assistant usage). Measured per run so the -D cost is never a stale constant.
    "turn1ContextTokens": turn1,
    "contextWindow": ctxwin,
    "turn1ContextPct": (round(100.0 * turn1 / ctxwin, 1) if (turn1 and ctxwin) else None),
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
if rate_limit_info is not None:
    record["rateLimitInfo"] = rate_limit_info
if parser == "codex":
    # Reasoning tokens are a BREAKDOWN of outputTokens, never an addition — recorded so a
    # reader can see how much of the output was thinking without re-deriving it wrongly.
    record["reasoningOutputTokens"] = data.get("reasoningOutputTokens")
    record["threadId"] = data.get("thread_id")
    if data.get("error"):
        record["providerError"] = (data["error"] or {}).get("message")
# Under a translating proxy the child CANNOT report which model served it: Claude Code's
# system prompt asserts a Claude identity, and a forced upstream model still leaves the
# child's own pin in modelUsage. When the launcher captured the proxy's server-side truth,
# it wins over servedModel, and the disagreement is kept rather than smoothed away.
_attested = os.environ.get("ATTEST_V") or ""
if _attested:
    record["attestedModel"] = _attested
    record["attestationAgrees"] = (record["servedModel"] == _attested)
# DNF early-warning. MUST come from PER-REQUEST usage. `fresh`/`cache_read` above are session
# CUMULATIVE sums -- they are the cost basis -- so comparing them to a context window fires on
# any long run and means nothing. Measured 2026-08-20: a cell whose true peak request was 64,114
# reported a cumulative 414,662 and warned, against a window it never approached.
#
# Only `type: assistant` events carry a per-request usage block; the terminal `result` event
# carries the session total, which is what made the two look interchangeable. Without a
# stream (`-P`) there is no per-request data, so no warning is emitted rather than a wrong one.
peak_req = 0
for _line in raw.splitlines():
    try:
        _o = json.loads(_line)
    except Exception:
        continue
    if not isinstance(_o, dict) or _o.get("type") != "assistant":
        continue
    _u = (_o.get("message") or {}).get("usage") or {}
    peak_req = max(peak_req, (_u.get("input_tokens") or 0)
                   + (_u.get("cache_read_input_tokens") or 0)
                   + (_u.get("cache_creation_input_tokens") or 0))
if peak_req:
    record["peakRequestTokens"] = peak_req
    try:
        _window = (_reg.resolve(os.environ["MODEL_V"]).get("limits") or {}).get("contextTokens")
    except Exception:
        _window = None
    if _window and peak_req > 0.8 * int(_window):
        record["contextCeilingWarning"] = True
# schemaValid: with -S, the result text must parse as JSON. (Key-level conformance is the
# caller's check; this catches the prose-instead-of-JSON failure mode cheaply.)
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
    ledger = os.environ["LEDGER_DEFAULT_V"]
if ledger:
    try:
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # spend ledger is advisory; never fail the run over it
PYEOF
  rm -f "$raw_tmp"
}
