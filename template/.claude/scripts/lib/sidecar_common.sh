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
#   sc_gate_availability / sc_gate_band / sc_gate_capacity / sc_gate_price_window
#   sc_build_disclosure                         fills SC_RUN_CWD and SC_APPEND_ARGS
#   sc_scrub_env                                fills SC_SCRUB for a `claude` child
#   sc_write_record <raw-output> <exit-code>
#
# EXIT CODES are a shared contract, keyed on by several consumers — never renumber:
#   0 ok · 2 bad usage/unresolvable model/unusable registry/invalid launch-memory override ·
#   3 credential missing
#   4 child CLI missing · 5 band gate refusal (-A overrides) · 6 balance floor unmet
#   (-A does NOT override) · 7 model UNAVAILABLE in the registry (-U overrides)
#   8 provider capacity or dispatch band unreadable (sc_gate_capacity; no flag overrides it)
#   9 RETIRED — the stall watchdog used to kill a silent child. A silent run now writes
#   `<record>.stall` and keeps running. Do not reuse the code: consumers carry the old mapping.
#   10 pre-spawn dispatch floor, spent allowance or live exhausted marker; after spawn, a provider
#   usage limit stopped work (neither -A nor -U overrides)
#   11 peak pricing window refused on a gate.peakPolicy=refuse row (-W overrides, on the user's
#   word only; -A does not)
#   12 memory headroom below its floor: foreground refuses at once; a detached job waits,
#   then exits 12 if its opt-in -T / SIDECAR_TIMEOUT wait bound expires. No flag overrides it.
#   13 launch refused: a live launcher or seat child already runs this job (this -f prompt file under the
#   same -l label or -R record) or resumes this -r session (sc_check_duplicate_launch); nothing started.
#   Stop the old run first with the printed tools/reap.py command; no flag overrides it
#   14 RETIRED (never reuse the code).
#   15 re-pay refused: the spend ledger already holds a FINISHED row for this work key, or exists but cannot be read (tools/work_key.py:
#   the brief's bytes, the harness revision, the pin and the label, or the key the starting tool declared in
#   SIDECAR_WORK_KEY); nothing started. A deliberate repeat is different work: give it its own label. A
#   resume (-r) and a --check are never refused. No flag overrides it
#
# 8 is NOT 7. "This provider's capacity cannot be read" and "this transport is not in the
# roster" have different fixes and different overrides; one code for both would send a
# reader to the wrong one.

# `pwd -W` gives the C:/ spelling Windows Python can open even when MSYS_NO_PATHCONV=1 stops argument conversion.
SC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && { pwd -W 2>/dev/null || pwd; })"

# A launcher runs for up to an hour, and bash reads a script INCREMENTALLY: editing the launcher file
# mid-run corrupts the live launcher at its next read (measured 2026-09-08: an edit to
# codex_proxy_sidecar.sh killed a running luna curator's resume loop with "unexpected EOF", losing a
# resumable session). Every launcher re-execs itself from a snapshot copy before doing any work;
# SC_LAUNCHER_DIR keeps lib resolution on the real tree. This lib is sourced whole, so editing it is safe.
# sc_snapshot_sweep <dir> -- delete snapshot copies whose owning process is gone.
# Age alone is not evidence a snapshot is dead — a >24h run is still reading its own copy, and the
# owning pid is in the filename. Sweep only the ones whose process is gone: TWO batched `find`
# calls against the whole directory, never one per file. Measured 2026-09-15: 1021 accumulated
# snapshots had never been swept (Windows pid reuse makes `kill -0` false-positive "alive" for an
# arbitrary recycled number often enough that almost nothing got past the liveness check), and the
# old per-file `find "$_old" -mmin +1440` loop cost ~30s of pure subprocess-spawn overhead on
# EVERY single dispatch and --check, worse every day as the pile grew. A second, unconditional
# 10080-minute (7-day) ceiling — far beyond any real launcher run — bounds the pile even when
# `kill -0` keeps false-positiving: past that age, delete regardless of the liveness check.
sc_snapshot_sweep() {
  local snapdir="$1" _old _p _soft _hard
  _soft="$(find "$snapdir" -maxdepth 1 -type f -name '*.sh' -mmin +1440 2>/dev/null)"
  [ -n "$_soft" ] || return 0
  _hard="$(find "$snapdir" -maxdepth 1 -type f -name '*.sh' -mmin +10080 2>/dev/null)"
  while IFS= read -r _old; do
    [ -n "$_old" ] || continue
    case $'\n'"$_hard"$'\n' in
      *$'\n'"$_old"$'\n'*) rm -f "$_old" 2>/dev/null; continue ;;
    esac
    _p="${_old%.sh}"; _p="${_p##*.}"
    kill -0 "$_p" 2>/dev/null && continue
    rm -f "$_old" 2>/dev/null
  done <<< "$_soft"
}

sc_reexec_snapshot() {
  local launcher="${BASH_SOURCE[1]}" snapdir snap
  # A launcher invoked from inside another sidecar child inherits that child's machinery: its
  # SC_SNAPSHOT_OF and SIDECAR_DETACHED sit in the environment, and honoring them makes THIS launch
  # behave as if it were the outer job -- `-X` is never scanned and getopts rejects it, and
  # sc_write_record registers no durable writer because "the -X wrapper owns .out/.exit", so a
  # nested dispatch loses its record silently. Measured 2026-09-23 from a deepseek sidecar session:
  # every nested `-X` launch died `-X must be a standalone argument` (exit 2), 0 records written.
  # Only our OWN snapshot -- the copy under sidecar-snapshots/ that the detach and the re-exec both
  # run -- may honor them. SC_LAUNCHER_DIR is kept: the lib was just sourced through it, and the
  # re-exec below passes it to the snapshot so the snapshot resolves the real lib, not its own dir.
  case "$launcher" in
    *"/sidecar-snapshots/"*|*"\\sidecar-snapshots\\"*) ;;
    *) unset SC_SNAPSHOT_OF SIDECAR_DETACHED SC_INVOKED_LAUNCHER SC_DISPATCH_CHECKED ;;
  esac
  [ -n "${SC_SNAPSHOT_OF:-}" ] && return 0
  # The path the caller invoked, which a snapshot's own path no longer names: a launcher serving
  # several plans (anthropic_sidecar.sh) resolves its plan from it. It is carried like
  # SC_SNAPSHOT_OF, so only this launcher's own snapshot honors it.
  SC_INVOKED_LAUNCHER="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

  # -X: foreground->background detach (Design §1, sidecar-detach.md). Never for --check (must
  # answer synchronously with no spawn) and never re-entered once SIDECAR_DETACHED is set — the
  # detached job re-invokes the launcher WITHOUT -X, so it takes the ordinary branch below.
  if [ "${1:-}" != "--check" ] && [ -z "${SIDECAR_DETACHED:-}" ]; then
    sc_x_scan "$@"
    if [ "$SC_X_PRESENT" = 1 ]; then
      sc_detach_launch "$launcher" "$@"
      exit 0   # sc_detach_launch always exits itself; this is an unreachable safety net.
    fi
  fi

  snapdir="${TEMP:-${TMPDIR:-/tmp}}/sidecar-snapshots"
  mkdir -p "$snapdir" 2>/dev/null || return 0
  sc_snapshot_sweep "$snapdir"
  snap="$snapdir/$(basename "$launcher").$$.sh"
  cp "$launcher" "$snap" 2>/dev/null || return 0   # cannot snapshot -> run live, as before
  # ${SC_LAUNCHER_DIR:-}, not "$SC_LAUNCHER_DIR": a launcher that sources this lib without first
  # setting SC_LAUNCHER_DIR (opencode_sidecar.sh, until D1) would otherwise hit "unbound
  # variable" under set -u on the very first re-exec -- measured 2026-09-14. Every launcher that
  # DOES set it (anthropic/codex_proxy/deepseek) is unaffected either way.
  SC_SNAPSHOT_OF="$launcher" SC_INVOKED_LAUNCHER="$SC_INVOKED_LAUNCHER" SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-}" \
    exec bash "$snap" "$@"
}

# sc_x_takes_value <single letter> -> true if SC_OPTSTRING marks it as value-taking. A flag's own
# colon (if it has one) is always the character immediately after its own letter in the option
# string, so this substring check can never cross into a neighboring flag's colon.
sc_x_takes_value() {
  case "$SC_OPTSTRING" in *"${1}:"*) return 0 ;; *) return 1 ;; esac
}

# sc_x_scan "$@" -> sets SC_X_PRESENT (0/1) and SC_X_RECORD (the -R value, or empty). A "-X"
# token is only ever the TRIGGER when it is not itself the value a preceding value-taking option
# is about to consume (e.g. "-l -X" leaves -X as -l's label, not a detach request).
sc_x_scan() {
  SC_X_PRESENT=0
  SC_X_RECORD=""
  SC_X_PROMPT=""
  SC_X_RESUME=""
  SC_X_LABEL=""
  SC_X_WORKDIR="$(pwd)"
  local tok next_is_value=0 pending_letter="" letter
  for tok in "$@"; do
    if [ "$next_is_value" = 1 ]; then
      case "$pending_letter" in R) SC_X_RECORD="$tok" ;; f) SC_X_PROMPT="$tok" ;; r) SC_X_RESUME="$tok" ;; l) SC_X_LABEL="$tok" ;; d) SC_X_WORKDIR="$tok" ;; esac
      next_is_value=0
      continue
    fi
    if [ "$tok" = "-X" ]; then
      SC_X_PRESENT=1
      continue
    fi
    case "$tok" in
      -?)
        letter="${tok#-}"
        if sc_x_takes_value "$letter"; then
          next_is_value=1
          pending_letter="$letter"
        fi
        ;;
    esac
  done
}

# sc_detach_launch <launcher-path> <original-args...> -- called only when sc_x_scan found a
# standalone "-X". Owns every -X refusal and the whole claim/snapshot/spawn sequence; it always
# exits and never returns, matching sc_reexec_snapshot's own `exec` branch never returning.
sc_detach_launch() {
  local launcher="$1"; shift
  local record="$SC_X_RECORD"
  if [ -z "$record" ]; then
    sc_die 2 "-X needs -R"
  fi

  local out="${record}.out" err="${record}.err" exitf="${record}.exit" pidf="${record}.pid"
  local f
  # A missing directory makes the noclobber claim below fail exactly like a taken path; name it.
  if [ ! -d "$(dirname -- "$record")" ]; then
    sc_die 1 "record directory does not exist: $(dirname -- "$record") (a relative -R resolves against $(pwd)); create it or pass an absolute -R"
  fi
  for f in "$out" "$err" "$exitf" "$pidf"; do
    if [ -e "$f" ]; then
      sc_die 1 "record path already used; pass a new -R path ($f exists)"
    fi
  done

  # Once claimed below, the record path is this launch's: a refusal writes its .exit and finish row,
  # and a spawned job hands both to the detached run.
  SC_RECORD="$record"
  SC_LABEL="$SC_X_LABEL"
  sc_on_exit 'sc_write_durable_files "$rc"'
  sc_register_finish_row

  # Before the claim, so its .pid line names it: one id for every process of this launch.
  sc_ensure_launch_id

  # Claim the record path atomically: noclobber makes this fail if a concurrent -X launch won the
  # race between the existence check above and here, so exactly one of two simultaneous launches
  # on the same new -R path spawns.
  if ! ( set -o noclobber; sc_pid_line "$$" > "$pidf" ) 2>/dev/null; then
    SC_FINISH_HANDED_OFF=1   # the launch that won the claim owns this path's .exit and finish row
    sc_die 1 "record path already used; pass a new -R path ($pidf exists)"
  fi
  # After the claim, so a refusal writes only to a path this launch owns; before any spawn.
  sc_check_duplicate_launch "$SC_X_PROMPT" "$SC_X_RESUME" "$SC_X_LABEL" "$SC_X_RECORD"
  sc_dispatch_launch "$@"

  # Snapshot before any spawn -- the same "editing a running launcher corrupts it mid-read"
  # hazard sc_reexec_snapshot exists for applies here too, and a detached job can run for an hour.
  local snapdir claimcopy
  snapdir="${TEMP:-${TMPDIR:-/tmp}}/sidecar-snapshots"
  if ! mkdir -p "$snapdir" 2>/dev/null; then
    sc_die 1 "cannot snapshot; detach refused"
  fi
  claimcopy="$snapdir/$(basename "$launcher").claim-$$.sh"
  if ! cp "$launcher" "$claimcopy" 2>/dev/null; then
    sc_die 1 "cannot snapshot; detach refused"
  fi

  # Strip exactly the "-X" token (per the same scan rule: a token consumed as another flag's
  # value is never touched), so the detached re-invocation can never re-trigger this branch.
  local pass_args=() tok skip_next=0 letter
  for tok in "$@"; do
    if [ "$skip_next" = 1 ]; then pass_args+=("$tok"); skip_next=0; continue; fi
    if [ "$tok" = "-X" ]; then continue; fi
    case "$tok" in
      -?) letter="${tok#-}"; sc_x_takes_value "$letter" && skip_next=1 ;;
    esac
    pass_args+=("$tok")
  done

  ( trap '' HUP
    sc_pid_line "$BASHPID" >> "$pidf"
    renamed="$snapdir/$(basename "$launcher").$BASHPID.sh"
    if ! mv "$claimcopy" "$renamed" 2>>"$err"; then
      printf '%s\n' 1 > "${exitf}.tmp" && mv "${exitf}.tmp" "$exitf"
      echo "cannot rename detach snapshot claim" >> "$err"
      exit 0
    fi
    SIDECAR_DETACHED=1 SC_SNAPSHOT_OF="$launcher" SC_INVOKED_LAUNCHER="${SC_INVOKED_LAUNCHER:-}" \
      SC_DISPATCH_CHECKED="${SC_DISPATCH_CHECKED:-0}" SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-}" \
      bash "$renamed" "${pass_args[@]}" >"$out" 2>>"$err"
    jrc=$?
    [ -f "${record}.claims.json" ] || sc_check_completion "$record" "${SC_X_WORKDIR:-$(pwd)}"
    printf '%s\n' "$jrc" > "${exitf}.tmp" && mv "${exitf}.tmp" "$exitf"
  # The job owns no caller descriptor: a subshell that kept fd 1 or 2 held the caller's pipe
  # open until the run ended, so `$(...)` and a harness Bash call waited out the whole run.
  ) </dev/null >/dev/null 2>>"$err" &
  disown
  local child=$!
  SC_FINISH_HANDED_OFF=1   # A closed receipt pipe must not let the parent finish the live job.
  # The watcher reads the last pid= line; exiting before the job writes its own leaves the launcher's.
  local waited=0
  while [ "$(grep -c 'pid=' "$pidf" 2>/dev/null)" -lt 2 ] && [ "$waited" -lt 100 ]; do
    sleep 0.05; waited=$((waited + 1))
  done

  echo "DETACHED pid=$child"
  echo "OUT=$out"
  echo "ERR=$err"
  echo "EXIT=$exitf"
  echo "WAIT=Monitor until EXIT exists or the -P progress file goes stale; inspect the process and record before any kill"
  exit 0
}
SC_REGISTRY_CLI="$SC_ROOT/tools/model_registry.py"
SC_BUDGET_HOOK="$SC_ROOT/hooks/budget_posture.py"

# ------------------------------------------------------------------------ defaults
# Every launcher starts from the same values. A provider that needs a different default
# overrides the variable AFTER sourcing and BEFORE sc_parse_flags, so the flag still wins.
SC_MODEL="${SC_MODEL:-}"
# The paying plan and the calling session's plan, set after sourcing by a launcher that serves more
# than one plan of its route (anthropic_sidecar.sh); never inherited from the caller.
SC_PLAN=""
SC_SEAT_PLAN=""
SC_AUTHORIZED=0
SC_UNSUSPEND=0
SC_PEAK_AUTHORIZED=0   # -W: the user explicitly authorized a peak-window dispatch
SC_PRICE_WINDOW=""; SC_PRICE_MULT=""; SC_PRICE_CHANGES=""; SC_PRICE_AT_START=""; SC_PRICE_SUMMARY=""
SC_EFFORT=""
# Empty means the caller asserted no tool list, so the shape's row in guards/tool_profiles.json
# applies (sc_resolve_tools). `-t` is a DELTA when every entry carries a sign
# (`+Bash,-Grep,+mcp:godot`), the old absolute `--allowedTools` list when none does, and an arm's
# task row when the shape is `arm`. A caller that wants read-only explicitly writes `-t Read,Glob,Grep`.
SC_TOOLS=""
# -B: the `arm` shape's task built-ins. The arm row carries no built-ins of its own
# (guards/tool_profiles.json), so its task row supplies them -- normally as the unsigned `-t`. An
# arm that also wants a DELTA (`+mcp:ai-worker`) cannot put both grammars in one CSV, and the
# resolver refuses a task-base `-t` beside a structured delta, so the base gets its own flag.
# Empty means "not asserted". Without a value and without a `-t` task row, shape `arm` refuses.
SC_BASE_BUILTINS=""
SC_MAX_TURNS=""   # empty = NO turn cap (user directive 2026-08-03: a cap discards completed
                  # work; -T wall-clock is the runaway guard). Pass -n N to cap explicitly.
SC_FORMAT="json"
SC_FORMAT_SET=""
SC_CALLER_JSON=""   # 1: the caller asked for `-o json` on a run that streams internally; sc_resume_loop converts its OUTPUT back
SC_WORKDIR="$PWD"
SC_PROMPT_FILE=""
# `timeout` really does kill the child (exit 124, `[ $rc -eq 124 ]` in every launcher), so it is
# OPT-IN and default OFF: the owner's directive is that no bound ends a paid run by itself, and a
# run silent for SC_STALL_SEC now writes the `.stall` signal the session watches instead of dying.
# Pass -T <sec>, or SIDECAR_TIMEOUT=<sec>, only when the caller can name why it wants the kill.
SC_TIMEOUT="${SIDECAR_TIMEOUT-}"
SC_RECORD=""
SC_INVENTORY=""
SC_INVENTORY_STALE_SECONDS="${SIDECAR_INVENTORY_STALE_SECONDS:-604800}"
SC_INVENTORY_SWEEP_LIMIT="${SIDECAR_INVENTORY_SWEEP_LIMIT:-32}"
SC_PARENT_SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
SC_LAUNCH_ID="${SIDECAR_LAUNCH_ID:-}"
# A seat child carries its launch's id (sc_scrub_env) and marks it SIDECAR_SEAT_LAUNCH_ID, so a launch
# nested inside that seat takes its own id; an id a fan-out assigned to this launch differs and is kept.
if [ -n "$SC_LAUNCH_ID" ] && [ "$SC_LAUNCH_ID" = "${SIDECAR_SEAT_LAUNCH_ID:-}" ]; then SC_LAUNCH_ID=""; fi
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
SC_RESUMED_FROM=""
# -t pre-approves; it does not grant. Under auto, read-shaped MCP tools (ai-worker, semantic-search,
# godot, LSP) run off-list with no denial (measured 2026-09-03). Writes need the list: a write
# delegate whose Bash/Edit falls outside it hard-fails.
# Provider transports default to bypassPermissions, anthropic to auto; reference/sidecar_dispatch.md
# §Permission mode owns the reason and evidence. `-p` overrides either default.
case "${SC_TRANSPORT:-}" in
  codex|opencode|deepseek) SC_PERM_MODE="bypassPermissions" ;;
  *) SC_PERM_MODE="auto" ;;
esac
SC_LEDGER="__default__"
SC_LABEL=""
SC_SHAPE=""
SC_TIER=""   # -Q: the job's written tier; empty takes the -G shape row's tier
SC_PIN_BASIS=""
SC_ADD_DIRS=()
SC_DISCLOSURE=""
SC_CONTEXT_FILES=()
SC_PROMPT=""
SC_SHIFT=0
SC_DISPATCH_ARGS=()
SC_DISPATCH_PENDING=0

# One spend ledger across providers. A per-provider file would need a second reader in
# /orchestration_metrics, and the one that got forgotten would drop its runs silently;
# every record carries requestedModel and costBasis, so the rows stay separable.
# SIDECAR_LEDGER_PATH overrides this path here and in orchestration_metrics.py, so writer and
# delegation reader move together. Provider capacity is account-wide and uses a separate cache.
SC_LEDGER_DEFAULT="${SIDECAR_LEDGER_PATH:-$HOME/.claude/sidecar_ledger.jsonl}"

# One option string, defined once, so the -X pre-scan in sc_reexec_snapshot (which needs to know
# which single-letter flags consume the next token as their argument, e.g. "-l -X" is not a
# trigger) and sc_parse_flags's own getopts can never drift apart. Leading ':' is silent-error
# mode: an invalid or argument-missing option lands in OPTARG instead of bash printing its own
# message, which is what lets '?' distinguish a malformed "-X" cluster (OPTARG=X) from every
# other bad flag below.
SC_OPTSTRING=":m:e:t:n:o:d:f:T:R:x:P:S:sNr:p:L:l:a:G:B:AUWMD:C:Z:K:Y:Q:"

# The window the child declares (CLAUDE_CODE_MAX_CONTEXT_TOKENS): the registry's default window, or its
# max under -M, minus the output reserve (the provider enforces input + max_output <= its window).
# An unknown model prints nothing.
sc_declared_context_window() {
  local w
  w="$(python3 "$SC_REGISTRY_CLI" context-window "$SC_MODEL" ${SC_LONG_CONTEXT:+--max} 2>/dev/null)" || return 0
  case "$w" in ''|*[!0-9]*) return 0 ;; esac
  [ "$w" -gt $(( ${SC_OUTPUT_RESERVE:-0} * 2 )) ] && w=$(( w - ${SC_OUTPUT_RESERVE:-0} ))
  printf '%s' "$w"
}

# The child's output cap (CLAUDE_CODE_MAX_OUTPUT_TOKENS): SIDECAR_MAX_OUTPUT_TOKENS when it is a positive number, else
# the registry's `max-output` (the model's own maximum, bounded by a quarter of its window). $1, when given, is
# the window the launcher declares in place of the registry's, and bounds the registry cap at a quarter of it,
# as model_registry's OUTPUT_WINDOW_DIVISOR does: the cap is also the compaction reserve. The client's 32000
# default discards a paid reply that outgrows it, reasoning included. An unknown model prints nothing.
# codex_proxy_sidecar.sh is the exception and does not call this: its cap also sets its output reserve, and
# its 16000 default comes from a measured compaction point.
sc_max_output() {
  if [ -n "${SIDECAR_MAX_OUTPUT_TOKENS:-}" ]; then
    case "$SIDECAR_MAX_OUTPUT_TOKENS" in
      ''|*[!0-9]*|0|00*) echo "[sidecar] SIDECAR_MAX_OUTPUT_TOKENS is not a positive number; using the registry cap" >&2 ;;
      *) printf '%s' "$SIDECAR_MAX_OUTPUT_TOKENS"; return 0 ;;
    esac
  fi
  local o
  o="$(python3 "$SC_REGISTRY_CLI" max-output "$SC_MODEL" ${SC_LONG_CONTEXT:+--max} 2>/dev/null)" || return 0
  case "$o" in ''|*[!0-9]*) return 0 ;; esac
  case "${1:-}" in ''|*[!0-9]*) ;; *) [ "$o" -gt $(( $1 / 4 )) ] && o=$(( $1 / 4 )) ;; esac
  printf '%s' "$o"
}

# ------------------------------------------------------------------- flag parsing
# Register as soon as -R is consumed, so a later parse refusal still terminates the watched job.
# Detached wrappers own their durable files; there must not be a second writer in the child.
sc_register_durable_files() {
  [ -n "$SC_RECORD" ] && [ -z "${SIDECAR_DETACHED:-}" ] || return 0
  sc_ensure_launch_id
  if [ "${SC_DURABLE_REGISTERED:-0}" != 1 ]; then
    SC_DURABLE_REGISTERED=1
    sc_on_exit 'sc_write_durable_files "$rc"'
  fi
  sc_pid_line "$$" > "${SC_RECORD}.pid" 2>/dev/null || :
}

# A record without a progress stream would run with the stall watchdog and the result grace off; `-R` watches
# `<record>.stream`, the path sidecar_fanout passes. A caller that asked for `-o json` parses one result
# object on stdout, so the child still streams and sc_resume_loop converts its OUTPUT back to that object.
# `-o text` is left for its refusal, and no `-R` stays unwatched.
sc_settle_format() {
  [ -n "$SC_RECORD" ] && [ -z "$SC_PROGRESS" ] || return 0
  case "$SC_FORMAT" in
    json) [ -z "${SC_FORMAT_SET:-}" ] || SC_CALLER_JSON=1 ;;
    stream-json) ;;
    *) return 0 ;;
  esac
  SC_PROGRESS="${SC_RECORD}.stream"; SC_FORMAT="stream-json"
}

# The json output contract is the CLI's one result object (scripts/lib/cli_envelope.py reads every shape).
# A stream with no result event is left as written: it is the evidence, and no result is invented.
sc_json_contract() {
  [ "${SC_CALLER_JSON:-}" = 1 ] || return 0
  local one
  one="$(printf '%s\n' "$OUTPUT" | python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
import cli_envelope
row = cli_envelope.result_event(cli_envelope.events(sys.stdin.read()))
if isinstance(row, dict) and row.get("type") == "result":
    print(json.dumps(row))' "$(sc_to_native "$SC_ROOT/scripts/lib")" 2>/dev/null)"
  [ -z "$one" ] || OUTPUT="$one"
}

sc_parse_flags() {
  local opt
  sc_register_finish_row
  OPTIND=1
  while getopts "$SC_OPTSTRING" opt; do
    case "$opt" in
      m) SC_MODEL="$OPTARG" ;;
      A) SC_AUTHORIZED=1 ;;
      U) SC_UNSUSPEND=1 ;;
      W) SC_PEAK_AUTHORIZED=1 ;;
      M) SC_LONG_CONTEXT=1 ;;
      K) [ -n "$OPTARG" ] || sc_die 2 "-K needs a compact point in input tokens"
         export SIDECAR_COMPACT_AT_TOKENS="$OPTARG" ;;
      e) SC_EFFORT="$OPTARG" ;;
      t) SC_TOOLS="$OPTARG" ;;
      B) SC_BASE_BUILTINS="$OPTARG" ;;
      n) SC_MAX_TURNS="$OPTARG"
         # A cap does not bound cost, it DISCARDS work: the run stops mid-tool-use with every
         # token already spent and no deliverable. -T is the runaway guard. Warn at dispatch
         # because the failure otherwise surfaces only as a puzzling exit 1 much later.
         echo "[sidecar] WARNING: -n $OPTARG caps turns. Hitting it discards a paid, unfinished run (stopReason=tool_use, exit 1). Prefer -T for runaway protection." >&2 ;;
      o) SC_FORMAT="$OPTARG"; SC_FORMAT_SET=1 ;;
      d) SC_WORKDIR="$OPTARG" ;;
      f) SC_PROMPT_FILE="$OPTARG" ;;
      T) SC_TIMEOUT="$OPTARG" ;;
      R) SC_RECORD="$OPTARG"; sc_register_durable_files ;;
      x) SC_DISALLOWED="$OPTARG" ;;
      P) SC_PROGRESS="$OPTARG"; SC_FORMAT="stream-json" ;;
      S) SC_SCHEMA_FILE="$OPTARG" ;;
      s) SC_PERSIST=1 ;;   # retained for compatibility; persistence is now the default
      N) SC_PERSIST=0 ;;   # explicit opt-OUT of session persistence
      r) SC_RESUME="$OPTARG"; SC_RESUMED_FROM="$OPTARG"; SC_PERSIST=1 ;;   # SC_RESUME is consumed by the resume loop; the record keeps SC_RESUMED_FROM
      p) SC_PERM_MODE="$OPTARG" ;;
      L) SC_LEDGER="$OPTARG" ;;
      l) SC_LABEL="$OPTARG" ;;
      a) SC_ADD_DIRS+=(--add-dir "$OPTARG") ;;
      G) SC_SHAPE="$OPTARG" ;;
      Q) SC_TIER="$OPTARG" ;;
      Y) SC_PIN_BASIS="$OPTARG" ;;
      D) SC_DISCLOSURE="$OPTARG" ;;
      C) SC_CONTEXT_FILES+=("$OPTARG") ;;
      Z) SC_STALL_SEC="$OPTARG" ;;
      \?)
        # -X only ever reaches getopts glued into a cluster (e.g. "-AX") -- a standalone "-X" is
        # intercepted by sc_reexec_snapshot's scan before sc_parse_flags is ever called, and the
        # detached child never receives -X at all. So OPTARG=X here always means "not standalone".
        if [ "$OPTARG" = "X" ]; then
          sc_die 2 "-X must be a standalone argument"
        fi
        sc_die 2 "bad usage; see header" ;;
      :) sc_die 2 "bad usage; see header" ;;
    esac
  done
  SC_SHIFT=$((OPTIND - 1))
  sc_settle_format
  if [ -n "${SIDECAR_COMPACT_AT_TOKENS:-}" ]; then
    [[ "$SIDECAR_COMPACT_AT_TOKENS" =~ ^[1-9][0-9]{4,6}$ ]] ||
      sc_die 2 "-K / SIDECAR_COMPACT_AT_TOKENS needs an integer compact point in input tokens; the window it implies must be 100000..1000000"
    case "${SC_TRANSPORT:-}" in
      anthropic|anthropic-secondary) ;;
      *) sc_die 2 "-K / SIDECAR_COMPACT_AT_TOKENS is supported only by the Anthropic Claude-client launchers; ${SC_TRANSPORT:-unset} has no compact-point override contract" ;;
    esac
  fi
  # An omitted -D takes the lowest tier the shape needs (reference/sidecar_dispatch.md §Agent type).
  if [ -z "$SC_DISCLOSURE" ]; then
    if [ "$SC_SHAPE" = "survey" ]; then SC_DISCLOSURE="pointer"; else SC_DISCLOSURE="full"; fi
  fi
  # The daemon driver's drift guard (sc_settings_with_bench_guard) rewrites its Workflow args; two hooks' updatedInput
  # never merge, so this marker tells hooks/workflow_provider_guard.py to inject nothing into that call.
  if sc_drift_guard_active; then
    export HARNESS_WORKFLOW_ARGS_OWNER=drift_guard
  fi
  sc_ensure_launch_id
  # A detached job's -X wrapper already checked, and this job is the one it cleared.
  [ -n "${SIDECAR_DETACHED:-}" ] || sc_check_duplicate_launch "$SC_PROMPT_FILE" "$SC_RESUME" "$SC_LABEL" "$SC_RECORD"
  SC_DISPATCH_ARGS=("$@")
  SC_DISPATCH_PENDING=1
}

sc_dispatch_launch() {
  [ "${SC_DISPATCH_CHECKED:-0}" = 1 ] && return 0
  [ -n "${SC_INVOKED_LAUNCHER:-}" ] || return 0
  SC_DISPATCH_DEFAULT_MODEL="$SC_MODEL" python3 "$SC_ROOT/hooks/sidecar_dispatch_context.py" \
    --launcher "$(sc_to_native "$SC_INVOKED_LAUNCHER")" "$@" || { rc=2; exit 2; }
  SC_DISPATCH_CHECKED=1
}

# sc_pid_line <bash pid> -> one `<UTC stamp> [launch=<id> ][winpid=<Windows pid> ]pid=<pid>` line for
# <record>.pid; tools/watch_records.py reads the launch id to find a seat that outlived its launcher.
# A reader rejects a reused pid by creation time (a process created after the stamp did not write
# it); winpid lets a Windows reader check liveness without bash. `pid=` stays last because
# sidecar_fanout._detached_pid reads the value after the last `pid=`.
sc_pid_line() {
  local win=""
  [ -r "/proc/$1/winpid" ] && win="winpid=$(cat "/proc/$1/winpid") "
  printf '%s %s%spid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SC_LAUNCH_ID:+launch=$SC_LAUNCH_ID }" "$win" "$1"
}

sc_read_prompt() {
  if [ -n "$SC_PROMPT_FILE" ]; then
    [ -f "$SC_PROMPT_FILE" ] || sc_die 2 "prompt file not found: $SC_PROMPT_FILE"
    SC_PROMPT="$(cat "$SC_PROMPT_FILE")"
    # How a parent corrects a running author job (orchestration §4); same text as dispatch.js.
    # Absolute: the job's cwd may be a worktree, where a relative brief path names a missing file.
    local brief_abs
    brief_abs="$(sc_to_native "$(cd "$(dirname "$SC_PROMPT_FILE")" && pwd)/$(basename "$SC_PROMPT_FILE")")"
    [ "${SC_SHAPE:-}" = "author" ] && SC_PROMPT+=$'\n\n'"Amend: at each item and before returning, if $brief_abs.amend.md exists, read it; it overrides the brief. Name what you applied."
  else
    SC_PROMPT="${*:-}"
  fi
  [ -n "$SC_PROMPT" ] || sc_die 2 "empty prompt"
}

# ------------------------------------------------------------------ shared validation
# Ordered to fail on the cheapest, most-likely-wrong input first, and to fail BEFORE any
# dispatch: an arm that runs and then discards its provenance is unscoreable.
# The enumerated flags (-p, -G, -D), shared by a dispatch and its `--check`, so a check never passes a
# value the launch refuses.
sc_validate_enums() {
  if [ -n "$SC_PERM_MODE" ]; then
    case "$SC_PERM_MODE" in
      auto|acceptEdits|dontAsk|manual|plan|bypassPermissions|default) ;;
      *) sc_die 2 "invalid permission mode '$SC_PERM_MODE' (auto|acceptEdits|dontAsk|manual|plan|bypassPermissions)" ;;
    esac
  fi
  if [ -n "$SC_SHAPE" ]; then
    case "$SC_SHAPE" in
      any|survey|review|author|arm|bench-score) ;;
      *) sc_die 2 "invalid guard shape '$SC_SHAPE' (any|survey|review|author|arm|bench-score)" ;;
    esac
  fi
  if [ -n "$SC_DISCLOSURE" ]; then
    case "$SC_DISCLOSURE" in
      bare|pointer|full) ;;
      *) sc_die 2 "invalid disclosure tier '$SC_DISCLOSURE' (bare|pointer|full)" ;;
    esac
  fi
  if [ -n "${SC_TIER:-}" ]; then
    python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import model_registry; model_registry.canonical_tier(sys.argv[2])' \
      "$SC_ROOT/tools" "$SC_TIER" 2>/dev/null \
      || sc_die 2 "invalid tier '$SC_TIER' (a tier name from model_registry.py TIER_ORDER, or validation)"
  fi
  # -Y <pick>,<spec>: why this pin was chosen and whether the slice's test intent is clearly
  # defined (`orchestration` §5). An omitted -Y is valid -- the record then carries no pinBasis,
  # which the readouts count as unrecorded -- but a value outside the two enums is refused here,
  # before any spend, because a wrong reason recorded silently is worse than none.
  if [ -n "$SC_PIN_BASIS" ]; then
    case "$SC_PIN_BASIS" in
      cell,tight|cell,judged|room,tight|room,judged|owner,tight|owner,judged|default,tight|default,judged) ;;
      *) sc_die 2 "invalid pin basis '$SC_PIN_BASIS' (pick in cell|room|owner|default, spec in tight|judged; e.g. -Y cell,tight)" ;;
    esac
  fi
}

sc_validate_common() {
  # -M opts past a long-context price step: codex rows, and any row whose registry --max window differs
  # from its default (model_registry.effective_window). Elsewhere it would change nothing, so it is refused.
  if [ -n "${SC_LONG_CONTEXT:-}" ] && [ "${SC_TRANSPORT:-}" != "codex" ]; then
    local def max
    def="$(python3 "${SC_REGISTRY_CLI:-}" context-window "${SC_MODEL:-}" 2>/dev/null)"
    max="$(python3 "${SC_REGISTRY_CLI:-}" context-window "${SC_MODEL:-}" --max 2>/dev/null)"
    if [ -z "$max" ] || [ "$max" = "$def" ]; then
      sc_die 2 "-M (long context) changes nothing for ${SC_MODEL:-this model} on ${SC_TRANSPORT:-unset}: it has no long-context price step (model_registry.py context-window <model> --max). Drop -M."
    fi
  fi
  # -o is a FORMAT, not a file path — the misuse every new caller makes. The payload goes to
  # stdout; capturing it is the caller's redirect. Validate the value before anything spends.
  case "$SC_FORMAT" in
    text|json|stream-json) ;;
    *) sc_die 2 "-o is the OUTPUT FORMAT (text|json|stream-json; default json), not a file path. The result payload lands in <record>.out when -R is set; do not redirect it. Got: -o $SC_FORMAT" ;;
  esac
  if [ -n "$SC_RECORD" ]; then
    if ! : >> "$SC_RECORD" 2>/dev/null; then
      sc_die 2 "run-record path not writable: $SC_RECORD"
    fi
    case "$SC_FORMAT" in
      json|stream-json) ;;
      *) sc_die 2 "-R requires -o json or stream-json (record is parsed from the result payload)" ;;
    esac
  fi
  if [ -n "$SC_SCHEMA_FILE" ] && [ ! -f "$SC_SCHEMA_FILE" ]; then
    sc_die 2 "schema file not found: $SC_SCHEMA_FILE"
  fi
  sc_validate_enums
  local _cf
  for _cf in ${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"}; do
    [ -f "$_cf" ] || sc_die 2 "context file not found: $_cf"
  done
  sc_resolve_tools
}

# ----------------------------------------------------------------- tool profile resolution
# One resolved profile per job, before any model spend: the shape's row in
# guards/tool_profiles.json with `-t` applied, and every logical server name looked up in the
# active config. An unregistered server, an unknown shape, a removal the shape does not carry or a
# mixed signed/unsigned `-t` fails HERE with the resolver's own message: a strict config that
# quietly dropped a server would run the job on an inventory nobody chose.
#
# SC_RESOLVED_BUILTINS  -> `--tools` (the child's built-in availability list)
# SC_RESOLVED_TOOL_SPEC -> built-ins plus `mcp__<registered key>`, the form sc_mcp_config reads
# SC_TOOL_PROFILE       -> the record's toolProfile object (and the ledger row's)
sc_resolve_tools() {
  local err out packed
  err="$(mktemp)"
  sc_on_exit "rm -f \"$err\" 2>/dev/null || :"
  # `--flag=value`, not `--flag value`: a delta starts with `-` and argparse would read it as the
  # next option.
  local base_arg=()
  [ -n "${SC_BASE_BUILTINS:-}" ] && base_arg=(--base-builtins="${SC_BASE_BUILTINS}")
  # The profile must cover what the caller's brief tells the child to run: the -f file (or the
  # positional prompt, written to a file) and every -C file go to the resolver's --brief check.
  local brief_arg=() _bf
  if [ -n "${SC_PROMPT_FILE:-}" ]; then
    brief_arg+=(--brief="$SC_PROMPT_FILE")
  elif [ -n "${SC_PROMPT:-}" ]; then
    _bf="$(mktemp)"
    sc_on_exit "rm -f \"$_bf\" 2>/dev/null || :"
    printf '%s' "$SC_PROMPT" > "$_bf"
    brief_arg+=(--brief="$_bf")
  fi
  for _bf in ${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"}; do
    brief_arg+=(--context="$_bf")
  done
  out="$(python3 "$SC_ROOT/tools/tool_profiles.py" resolve --widen \
      --shape="${SC_SHAPE:-any}" --tier="${SC_TIER:-}" --tools="${SC_TOOLS:-}" \
      ${base_arg[@]+"${base_arg[@]}"} ${brief_arg[@]+"${brief_arg[@]}"} \
      --config-dir="${SC_MCP_CONFIG_DIR:-${CLAUDE_CONFIG_DIR:-}}" \
      --workdir="${SC_WORKDIR:-}" 2>"$err")" || {
    cat "$err" >&2
    sc_die 2 "[sidecar] refusing to dispatch: the tool profile is invalid or does not cover the brief (resolver text above)"
  }
  # Success still forwards the resolver's one-line "profile widened by +X" notice.
  [ -s "$err" ] && cat "$err" >&2
  rm -f "$err" 2>/dev/null || :
  packed="$(printf '%s' "$out" | SC_ROOT="$SC_ROOT" python3 -c 'import json, sys, os
sys.path.insert(0, os.path.join(os.environ["SC_ROOT"], "tools"))
import tool_profiles
p = json.load(sys.stdin)
print(",".join(p["builtins"]))
print(tool_profiles.tool_spec(p, list(p["mcpRegistrations"].values())))')" || {
    sc_die 2 "[sidecar] could not read the resolved tool profile"; }
  SC_TOOL_PROFILE="$out"
  SC_RESOLVED_BUILTINS="${packed%%$'\n'*}"
  SC_RESOLVED_TOOL_SPEC="${packed#*$'\n'}"
  # `--allowedTools` pre-approves and never bounds, but it must still name what this child may
  # use: a delta `-t` (+mcp:godot) is not a tool list, and passing it verbatim would pre-approve
  # a tool by that literal name. SC_TOOLS stays the caller's raw input for anything else.
  SC_ALLOWED_TOOLS="$SC_RESOLVED_TOOL_SPEC"
}

sc_validate_effort() {
  [ "$SC_EFFORT" = none ] && return 0
  [ -n "$SC_EFFORT" ] || return 0
  case "$SC_EFFORT" in
    low|medium|high|xhigh|max) ;;
    *) sc_die 2 "invalid effort '$SC_EFFORT' (low|medium|high|xhigh|max)" ;;
  esac
  # A transport that declares effortValues serves only those rungs; any other request is served
  # as a different rung, so the run record would name a coordinate that never ran.
  local declared legal
  # Fails CLOSED like sc_gate_price_window: an unreadable vocabulary is not "no restriction".
  if ! declared="$(python3 "$SC_REGISTRY_CLI" effort-values "$SC_MODEL" 2>&1)"; then
    sc_die 2 "[sidecar] cannot read effort values for $SC_MODEL: ${declared%%$'\n'*}"
  fi
  case "$declared" in
    ""|"[]"|*"\"$SC_EFFORT\""*) ;;
    *)
      legal="$(printf '%s' "$declared" | tr -d '[]" ' | tr ',' '|')"
      sc_die 2 "effort '$SC_EFFORT' is not served by $SC_TRANSPORT ($legal); the provider would run another rung under this label" ;;
  esac
  # A rung the route serves can still be one this row's evidence marks wasteful (`effort.avoid`).
  # Only a driver-declared comparison pins it (`sidecar_fanout.py --compare` or a benchmark campaign sets
  # SC_COMPARISON); free text never does. The lookup fails closed like the vocabulary's.
  local avoided avoid_err avoid_rc
  avoid_err="$(mktemp)"
  avoided="$(python3 "$SC_REGISTRY_CLI" effort-avoid "$SC_MODEL" "$SC_EFFORT" 2>"$avoid_err")"
  avoid_rc=$?
  if [ "$avoid_rc" -ne 0 ]; then
    avoided="$(head -n1 "$avoid_err")"
    rm -f "$avoid_err"
    sc_die 2 "[sidecar] cannot read effort.avoid for $SC_MODEL: $avoided"
  fi
  rm -f "$avoid_err"
  [ -n "$avoided" ] || return 0
  if [ -n "${SC_COMPARISON:-}" ]; then
    echo "[sidecar] effort '$SC_EFFORT' on $SC_MODEL is a rung its row avoids; pinned inside a comparison (${SC_COMPARISON})" >&2
    return 0
  fi
  sc_die 2 "effort '$SC_EFFORT' is in $SC_MODEL's registry effort.avoid: $avoided Pin the ladder's rung."
}

# --------------------------------------------------------------- model resolution
# One registry call yields everything the dispatch path needs. Python startup dominates
# on Windows, so several `resolve --field` calls would cost most of a second for what is
# one dict lookup.
sc_resolve_model() {
  local fields
  if ! fields="$(python3 "$SC_REGISTRY_CLI" sidecar-fields "$SC_MODEL" ${SC_PLAN:+--plan "$SC_PLAN"} 2>&1)"; then
    sc_die 2 \
      "[sidecar] cannot resolve -m '$SC_MODEL':" \
      "  $fields" \
      "  registry: $SC_REGISTRY_CLI" \
      "  This is the SPENDING consumer, so it fails closed: no fallback to a" \
      "  hardcoded id, because dispatching at a tier nobody chose costs money."
  fi
  # Column 11 is a RETIRED slot (the row's provider-band ceiling) and always reads empty. It stays
  # in this read, and in sidecar-fields, only so apiMode keeps landing in column 12: the opencode
  # launcher reads it positionally as `cut -d'|' -f12`, so dropping a column renumbers it silently.
  IFS='|' read -r SC_MODEL SC_ALIAS SC_AUTH_TIER SC_MIN_BAND SC_MIN_BALANCE SC_FRESH_RATE \
    SC_RETIRED_BALANCE_URL SC_TRANSPORT_STATE SC_TRANSPORT_REASON SC_COST_MODEL SC_MAX_PROVIDER_BAND \
    SC_API_MODE \
    <<< "$fields"
}

# ------------------------------------------------- gate 0: provider exhausted marker
# A usage-limit stop (sc_finish_usage_limit) writes <dir>/<transport>.json. The default dir sits under
# $HOME, not the repo, so every session, worktree and fan-out on this machine reads the same marker.
# Go included: no recorded Go error body scopes a limit to one model, so every limit marks the transport.
# sc_gate_availability runs this first, so every dispatch and every --check refuses with exit 10.
# Neither -A nor -U overrides it: -A authorizes spend past a band and -U lifts a registry suspension,
# and an exhausted allowance answers neither.
# A member key (`sc_exhausted_marker <member>`) names one pool member of the transport: its marker lives under
# members/<transport>/ and holds the member's consecutive-failure count. A member marker never reads or writes
# the transport-wide one, so one cooled member never refuses the rest of its transport.
sc_exhausted_marker() {
  local dir="${SIDECAR_EXHAUSTED_DIR:-$HOME/.claude/sidecar-exhausted}"
  if [ -n "${1:-}" ]; then
    printf '%s/members/%s/%s.json' "$dir" "$SC_TRANSPORT" "$1"
    return 0
  fi
  printf '%s/%s.json' "$dir" "$SC_TRANSPORT"
}

sc_gate_exhausted() {
  [ -n "${SC_TRANSPORT:-}" ] || return 0
  local marker msg grc
  marker="$(sc_exhausted_marker ${1:+"$1"})"
  [ -f "$marker" ] || return 0
  msg="$(MARKER_V="$(sc_to_native "$marker")" TRANSPORT_V="$SC_TRANSPORT" MEMBER_V="${1:-}" python3 - <<'PY'
import json, os, sys, time
path = os.environ["MARKER_V"]
try:
    with open(path, encoding="utf-8") as fh:
        marker = json.load(fh)
    expires = float(marker["expiresAt"])
except (OSError, ValueError, KeyError, TypeError):
    marker, expires = {}, os.path.getmtime(path) + 1800   # unreadable: fail closed for the default window
if expires <= time.time():
    sys.exit(0)
when = time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(expires))
transport, member = os.environ["TRANSPORT_V"], os.environ.get("MEMBER_V", "")
if member:
    basis = "the member cooldown"
elif marker.get("resetsAt"):
    basis = "the provider's reset time"
else:
    basis = "the 30-minute default; the stream named no reset time"
scope = "the %s member %s" % (transport, member) if member else "the %s provider" % transport
print("[sidecar] REFUSING: %s hit its usage limit. Its exhausted marker expires %s (%s)." % (scope, when, basis))
print("  Route this work to another provider: python3 .claude/tools/model_registry.py available")
print("  -A and -U do not override an exhausted provider. Owner override: delete %s" % path)
sys.exit(10)
PY
)"
  grc=$?
  [ "$grc" -eq 0 ] && return 0
  [ -n "$msg" ] || msg="[sidecar] REFUSING: exhausted marker $marker could not be evaluated (python exit $grc); route this work to another provider."
  sc_die 10 "$msg"
}

sc_mark_exhausted() {
  [ -n "${SC_TRANSPORT:-}" ] || return 0
  local marker
  marker="$(sc_exhausted_marker ${1:+"$1"})"
  mkdir -p "$(dirname "$marker")" 2>/dev/null || { echo "[sidecar] could not create $(dirname "$marker"); no exhausted marker written" >&2; return 0; }
  MARKER_V="$(sc_to_native "$marker")" TRANSPORT_V="$SC_TRANSPORT" RESET_V="${SC_USAGE_LIMIT_RESET:-}" \
    LABEL_V="${SC_LABEL:-}" SID_V="${SC_USAGE_LIMIT_SID:-}" MEMBER_V="${1:-}" HOOKS_V="$(sc_to_native "$SC_ROOT/hooks")" \
    python3 - <<'PY' || echo "[sidecar] could not write the exhausted marker $marker" >&2
import json, os, sys, tempfile, time
sys.path.insert(0, os.environ["HOOKS_V"])
from _hook_state import replace_path
path, now = os.environ["MARKER_V"], time.time()
prior = {}
try:
    with open(path, encoding="utf-8") as fh:
        prior = json.load(fh)
        if float(prior["expiresAt"]) > now:
            raise SystemExit(0)   # the first stop's live marker stands
except (OSError, ValueError, KeyError, TypeError):
    pass
reset = os.environ.get("RESET_V", "")
reset = int(reset) if reset.isdigit() and int(reset) > now else None
data = {"schemaVersion": 1, "transport": os.environ["TRANSPORT_V"], "writtenAt": int(now),
        "expiresAt": reset or int(now + 1800), "resetsAt": reset,
        "label": os.environ.get("LABEL_V") or None, "sessionId": os.environ.get("SID_V") or None}
member = os.environ.get("MEMBER_V", "")
if member:
    failures = prior.get("failures", 0) if isinstance(prior, dict) else 0
    data.update({"member": member, "failures": int(failures) + 1})
fd, temp_path = tempfile.mkstemp(prefix=".exhausted-", dir=os.path.dirname(path))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    replace_path(temp_path, path)
except Exception:
    try:
        os.unlink(temp_path)
    except OSError:
        pass
    raise
PY
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
# Work already finished and paid for is not paid for again: the spend ledger is asked for a finished row under
# this launch's work key (tools/work_key.py owns the key and the message). A --check starts nothing, so it is
# not asked (it never parses launch flags, which is what sets SC_DISPATCH_PENDING). A resume continues an
# unfinished call, so it is not refused either, but it still carries the key, so the row it finishes under is
# found by a later launch of the same work. The key is exported once for sc_write_record. Class: floor (it
# protects the owner's paid quota; rules/harness_tooling.md §Paid work stops on an observed fact of waste).
sc_gate_repay() {
  [ "${SC_DISPATCH_PENDING:-0}" = 1 ] || return 0
  local ledger="$SC_LEDGER" harness rc=0 prompt_arg=() text_file=""
  { [ -n "$ledger" ] && [ "$ledger" != "__default__" ]; } || ledger="$SC_LEDGER_DEFAULT"
  harness="$(sc_harness_revision)"
  if [ -n "${SC_PROMPT_FILE:-}" ]; then
    prompt_arg=(--prompt-file "$(sc_to_native "$SC_PROMPT_FILE")")
  else
    text_file="$(mktemp)"
    printf '%s' "${SC_PROMPT:-}" > "$text_file"
    prompt_arg=(--prompt-text-file "$(sc_to_native "$text_file")")
  fi
  SC_WORK_KEY="$(python3 "$SC_ROOT/tools/work_key.py" key --model "$SC_MODEL" --effort "${SC_EFFORT:-}" \
    --label "${SC_LABEL:-}" --harness "$harness" "${prompt_arg[@]}")" || rc=$?
  [ -z "$text_file" ] || rm -f "$text_file"
  { [ "$rc" -eq 0 ] && [ -n "$SC_WORK_KEY" ]; } || sc_die 2 "[sidecar] the work key could not be computed (exit $rc); nothing started"
  export SC_WORK_KEY
  [ -z "${SC_RESUME:-}${SC_RESUMED_FROM:-}${SC_RESUME_SID:-}" ] || return 0
  rc=0
  python3 "$SC_ROOT/tools/work_key.py" check --key "$SC_WORK_KEY" --ledger "$(sc_to_native "$ledger")" || rc=$?
  [ "$rc" -eq 0 ] && return 0
  [ "$rc" -eq 15 ] && sc_die 15 "[sidecar] REFUSED: the re-pay check found this work finished and paid, or could not read the spend ledger (line above); nothing started. Finished work repeats under its own label; an unreadable ledger is repaired first."
  sc_die 2 "[sidecar] the re-pay check failed (exit $rc); nothing started"
}

# The harness revision a launch runs under: the code HEAD sc_write_record records as harnessSession, plus the
# doctrineSha a benchmark overlay stamped when one is present.
# bench_overlay_doctrine (benchmark_campaign/lib.sh) drops the .bench-doctrine-sha marker when it refreshes
# $SC_WORKDIR/.claude from BENCH_DOCTRINE_REF; absent on a root whose .claude/ is still the task tag's own
# frozen copy (BENCH_DOCTRINE_REF=none, or a non-benchmark dispatch entirely).
sc_harness_head() { git -C "$SC_WORKDIR" rev-parse HEAD 2>/dev/null || echo unknown; }
sc_doctrine_sha() { cat "$SC_WORKDIR/.claude/.bench-doctrine-sha" 2>/dev/null || echo ""; }
sc_harness_revision() {
  local dsha; dsha="$(sc_doctrine_sha)"
  printf '%s%s' "$(sc_harness_head)" "${dsha:+/$dsha}"
}

sc_gate_availability() {
  # --check never parses launch flags, so its gate call cannot re-enter the dispatch preflight.
  [ "${SC_DISPATCH_PENDING:-0}" != 1 ] || sc_dispatch_launch "${SC_DISPATCH_ARGS[@]}"
  sc_gate_repay
  sc_gate_exhausted
  [ "$SC_TRANSPORT_STATE" = "unavailable" ] || return 0
  if [ "$SC_UNSUSPEND" -eq 1 ]; then
    echo "[sidecar] -U: availability gate overridden for $SC_ALIAS ($SC_MODEL)." >&2
    return 0
  fi
  # States the exclusion and its reason. It does NOT name a replacement: which model to use
  # instead depends on the task's breadth, its complexity and the budget, none of which this
  # script knows, and a hardcoded redirect would make that call on the dispatcher's behalf.
  sc_die 7 \
    "[sidecar] $SC_ALIAS ($SC_MODEL) is UNAVAILABLE: ${SC_TRANSPORT_REASON:-no reason recorded}" \
    "  Not a band or balance refusal (5 and 6). Re-select from the available roster:" \
    "    python3 .claude/tools/model_registry.py available" \
    "  Re-enable: model_registry.py set-status $SC_TRANSPORT available   ·   override once: -U"
}

# ------------------------------------------------- benchmark-arm escape guard (settings merge)
# Every launcher passes its child settings through this, and it is the ONE --settings value the
# child gets: the CLI keeps only the last flag, so every fragment is merged here.
# Project rows come from adaptation.json sidecar_hook_rows: matcher, command and optional when.
# when maps environment names to exact values (null means nonempty); commands remain self-gated.
# {root}, {shape}, {prompt}, {ruler_scope}, {workdir}, {main_root} and {spill_dir} expand to shell-quoted launcher values.
#
# The former T1 fragment — enabledPlugins.semantic-search@dreb=false for a non-arm child whose -t
# named no search tool — is gone. Every launcher now passes --strict-mcp-config with the resolved
# profile's own config, so a server the profile does not name is not loaded on ANY transport,
# plugin servers included. A settings plugin switch was the only mechanism available to the one
# launcher that passed no strict config; keeping it beside a strict config would be a second,
# silently disagreeing answer to "which servers does this child have".
# A bench-score child (the score daemon's driver) with a -f brief gets scoring/drift_guard.py on Workflow: the
# call's args are checked against that brief before the paid panel runs.
sc_drift_guard_active() { [ "${SC_SHAPE:-}" = "bench-score" ] && [ -n "${SC_PROMPT_FILE:-}" ]; }
# BENCH_RULER_SCOPE adds the succession ruler's staged-file Read allowlist.
sc_settings_with_bench_guard() { # <settings-json-or-empty> -> merged json on stdout ("" if nothing to pass)
  python3 - "$1" "$SC_ROOT" "${SC_SHAPE:-}" "${SC_PROMPT_FILE:-}" "${BENCH_ARM:-}" "${BENCH_RULER_SCOPE:-}" "${SC_WORKDIR:-}" "${SC_RECORD:-}" "${SC_PROGRESS:-}" <<'PY'
import json, os, shlex, sys
from pathlib import Path
base = json.loads(sys.argv[1]) if sys.argv[1].strip() else {}
root = Path(sys.argv[2]).resolve()
if sys.argv[4]:
    prompt = Path(sys.argv[4]).resolve().as_posix()
    command = 'python3 "%s" --prompt-file "%s"' % (
        (root / "hooks/sidecar_recompact_reprompt.py").as_posix(), prompt)
    for event, matcher in (("SessionStart", "compact"), ("PreCompact", None)):
        rows_ = base.setdefault("hooks", {}).setdefault(event, [])
        if not any(row.get("matcher") == matcher and any(
                "sidecar_recompact_reprompt.py" in hook.get("command", "")
                for hook in row.get("hooks", [])) for row in rows_):
            row = {"hooks": [{"type": "command", "command": command, "timeout": 15}]}
            rows_.append({"matcher": matcher, **row} if matcher else row)
try:
    project = json.loads((root / "skills/project_subsystems/adaptation.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    project = {}
rows = project.get("sidecar_hook_rows", []) if isinstance(project, dict) else []
# The launcher's own row: a child in a linked worktree may write only its workdir and spill dir.
builtin = {"matcher": "Write|Edit|NotebookEdit|Bash", "when": {"SC_MAIN_ROOT": None},
           "command": "python3 {root}/hooks/sidecar_workdir_guard.py {workdir} {main_root} {spill_dir}"}
rows = [builtin] + [row for row in (rows if isinstance(rows, list) else [])
                    if not (isinstance(row, dict) and "sidecar_workdir_guard.py" in str(row.get("command", "")))]
env = dict(os.environ, SC_SHAPE=sys.argv[3], SC_PROMPT_FILE=sys.argv[4],
           BENCH_ARM=sys.argv[5], BENCH_RULER_SCOPE=sys.argv[6])
sys.path.insert(0, (root / "hooks").as_posix())
from _checkout_root import linked_main_root
workdir = os.path.abspath(sys.argv[7]) if sys.argv[7] else ""
main_root = linked_main_root(workdir) if workdir else ""
main_root = Path(main_root).as_posix() if main_root else ""
spill = os.path.dirname(os.path.abspath(sys.argv[8] or sys.argv[9])) if (sys.argv[8] or sys.argv[9]) else ""
env["SC_MAIN_ROOT"] = main_root
values = {"root": root.as_posix(), "shape": sys.argv[3], "workdir": workdir.replace("\\", "/"),
          "main_root": main_root, "spill_dir": spill.replace("\\", "/"),
          "prompt": os.path.abspath(sys.argv[4]).replace("\\", "/") if sys.argv[4] else "",
          "ruler_scope": env.get("BENCH_RULER_SCOPE", "")}
for row in rows if isinstance(rows, list) else []:
    if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("matcher", "command")):
        continue
    when = row.get("when", {})
    if not isinstance(when, dict) or not all(bool(env.get(k)) if v is None else env.get(k, "") == v
                                           for k, v in when.items()):
        continue
    command = row["command"]
    for key, value in values.items():
        command = command.replace("{" + key + "}", shlex.quote(value))
    base.setdefault("hooks", {}).setdefault("PreToolUse", []).append({
        "matcher": row["matcher"],
        "hooks": [{"type": "command", "command": command, "timeout": 10}],
    })
print(json.dumps(base) if base else "")
PY
}

# --------------------------------------------------------------- gate 2: our own band
# Band NAMES are ordered by QUOTA RISK, not by utilization: Surplus is the LOW-risk band
# ("plan quota going unused - spend it"), Ahead/Hot are HIGH-risk ones, and a band the projected
# run-out raised sits above what its burn rate alone would read. Every message prints the pressure
# NUMBER of the window whose band it printed (`quota.routingWindow`), because the name alone reads
# backwards and a pace from any other window is a different quantity.
# The seat is the calling session's PLAN (hooks/_session_transport.py --plan): a session on a second
# Anthropic login is gated on that login's band. A launcher that changes CLAUDE_CONFIG_DIR for its
# child reads the seat first into SC_SEAT_PLAN. The read is `provider_capacity.py`'s: cache first, so
# the gates of one launch share a single usage-endpoint read per cache life.
sc_gate_band() {
  local runner="$SC_ROOT/tools/provider_capacity.py" seat raw rc parsed status sat_rc
  seat="${SC_SEAT_PLAN:-}"
  [ -n "$seat" ] || seat="$(python3 "$SC_ROOT/hooks/_session_transport.py" --plan 2>/dev/null)"
  seat="${seat:-unknown}"
  raw="$(python3 "$runner" "$seat" 2>/dev/null)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    parsed="$(CAP_V="$raw" python3 -c '
import json,os,sys
try:
 d=json.loads(os.environ["CAP_V"]); q=d.get("quota") or {}; wins=q.get("windows") or []
 w=next((x for x in wins if x.get("name")==q.get("routingWindow")), None) or next((x for x in wins if x.get("name")==q.get("bindingWindow")), {})
 print("%s|%s|%s" % (d.get("status") or "", q.get("routingBand") or "", w.get("pressure") if w.get("pressure") is not None else ""))
except Exception: sys.exit(1)
' 2>/dev/null)" || rc=2
  fi
  if [ "$rc" -eq 0 ]; then
    IFS='|' read -r status SC_BAND SC_BAND_P <<< "$parsed"
    if [ "$status" = "exhausted" ]; then
      SC_BAND="Hot"; SC_BAND_P="spent"
    elif [ "$status" = "dispatch-floor" ]; then
      SC_BAND="Hot"; SC_BAND_P="dispatch-floor"
    elif [ "$status" != "available" ] || [ -z "$SC_BAND" ]; then
      rc=2
    fi
  fi

  if [ "$SC_AUTHORIZED" -eq 1 ]; then
    echo "[sidecar] -A: host-band floor bypassed for $SC_ALIAS ($SC_MODEL); seat=$seat band=${SC_BAND:-unknown} floor=$SC_MIN_BAND" >&2
    return 0
  fi
  if [ "$rc" -ne 0 ]; then
    if python3 "$SC_REGISTRY_CLI" band-satisfies Surplus "$SC_MIN_BAND" >/dev/null 2>&1; then
      echo "[sidecar] host capacity unavailable for $seat; floor $SC_MIN_BAND is the lowest band, so no reading could fail it." >&2
      SC_BAND="unknown"; SC_BAND_P="?"
      return 0
    fi
    sc_die 5 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): live host capacity for $seat is unavailable; cannot certify floor $SC_MIN_BAND. Use -A only for deliberate off-quota spend."
  fi
  python3 "$SC_REGISTRY_CLI" band-satisfies "$SC_BAND" "$SC_MIN_BAND"; sat_rc=$?
  [ "$sat_rc" -eq 0 ] && return 0
  if [ "$sat_rc" -eq 2 ]; then
    sc_die 5 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): host band '$SC_BAND' for seat $seat is invalid." \
      "  Override deliberate off-quota spend with -A."
  fi
  sc_die 5 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): host band $SC_BAND (pressure ${SC_BAND_P:-?}) for seat $seat is below floor $SC_MIN_BAND." \
    "  Surplus < On pace < Ahead < Hot; spend expiring plan quota before marginal dollars." \
    "  Override deliberate off-quota spend with -A."
}

# --------------------------------------------------------- unified provider capacity gate
# Every transport delegates amount evidence to tools/provider_capacity.py. Expected provider
# outcomes are normalized JSON; a nonzero exit is a runner/schema fault and refuses before launch.
sc_gate_capacity() {
  local runner="$SC_ROOT/tools/provider_capacity.py" raw rc parsed status band amount source detail
  if [ ! -f "$runner" ]; then
    sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): provider capacity runner is missing."
  fi
  raw="$(PROVIDER_CAPACITY_TOKEN="${SC_CREDENTIAL:-}" python3 "$runner" "$SC_TRANSPORT" 2>/dev/null)"; rc=$?
  SC_CAPACITY_JSON="$raw"
  if [ "$rc" -ne 0 ]; then
    sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): provider capacity runner failed (exit $rc)."
  fi
  parsed="$(CAP_V="$raw" CAP_TOOLS_V="$(sc_to_native "$(dirname "$SC_REGISTRY_CLI")")" python3 -c '
import json,os,sys
try:
 d=json.loads(os.environ["CAP_V"])
 status=d["status"]; source=d["sourceKind"]
 quota=d.get("quota") or {}; balance=d.get("balance") or {}
 detail=""
 if status == "dispatch-floor":
  sys.path.insert(0, os.environ["CAP_TOOLS_V"])
  from provider_capacity import floor_refusal
  detail=floor_refusal(d)
 print("%s|%s|%s|%s|%s" % (status, quota.get("dispatchBand") or "", balance.get("amount") if balance.get("amount") is not None else "", source, detail))
except Exception:
 sys.exit(1)
' 2>/dev/null)" || {
    sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): provider capacity output is malformed."
  }
  IFS='|' read -r status band amount source detail <<< "$parsed"
  SC_PROVIDER_BAND="$band"
  case "$status" in
    dispatch-floor)
      sc_die 10 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): $detail" ;;
    exhausted)
      sc_die 10 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): $SC_TRANSPORT plan allowance is exhausted." ;;
    insufficient)
      sc_die 6 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): $SC_TRANSPORT balance is insufficient." ;;
    auth-error)
      sc_die 3 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): $SC_TRANSPORT authentication failed." ;;
    network-error|malformed)
      sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): $SC_TRANSPORT capacity is $status." ;;
    unsupported)
      if [ "$SC_COST_MODEL" = "plan-quota" ] || awk -v f="${SC_MIN_BALANCE:-0}" 'BEGIN{exit !(f+0 > 0)}'; then
        sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): required provider amount is unsupported."
      fi
      echo "[sidecar] $SC_ALIAS capacity: amount unsupported, no amount floor; launcher eligibility remains authoritative." >&2
      return 0 ;;
    available) ;;
    *)
      sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): unknown provider capacity status '$status'." ;;
  esac

  if [ "$SC_COST_MODEL" = "plan-quota" ]; then
    # ONE plan-quota rule for every window, whatever its vendor: refuse on exhaustion (exit 10,
    # above) or on a band this launcher cannot read -- never on a per-row ceiling. The retired
    # `gate.maxProviderBand` refused every codex dispatch at band Ahead while the native Anthropic
    # route kept taking work at the same pressure. Which plan-quota window takes a job is the
    # `orchestration` section 5b comparison between the windows, not a registry constant.
    [ -n "$band" ] || {
      sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): provider quota has no dispatch band."
    }
    return 0
  fi

  if awk -v f="${SC_MIN_BALANCE:-0}" 'BEGIN{exit !(f+0 > 0)}'; then
    [ -n "$amount" ] || {
      sc_die 8 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): provider capacity has no required balance."
    }
    if ! awk -v b="$amount" -v f="$SC_MIN_BALANCE" 'BEGIN{exit !(b+0 >= f+0)}'; then
      sc_die 6 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): balance \$$amount is below the \$$SC_MIN_BALANCE floor."
    fi
  fi
  return 0
}

# ---------------------------------------------------------------- gate 5: price window
# Every launcher calls this after sc_gate_capacity. The registry owns the schedule
# (transports.<t>.pricingSchedule) and the policy (gate.peakPolicy), so a future time-priced
# provider is a registry edit; on a transport with no schedule this is a no-op. It also records
# the dispatch START instant, which is the moment the run record prices at.
# Refuses a peak dispatch on a peakPolicy:refuse row unless -W. -A and -U do NOT bypass it:
# -W exists because peak pricing is the user's call, not an agent's (reference/sidecar_dispatch.md).
# SC_CHECK_MODE=1 (from sc_check_gates) prints the one-line --check answer instead.
sc_gate_price_window() {
  local info parsed hhmm policy out_rate
  if ! info="$(python3 "$SC_REGISTRY_CLI" price-window "$SC_MODEL" 2>&1)"; then
    sc_die 2 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): price-window lookup failed: ${info%%$'\n'*}"
  fi
  parsed="$(INFO_V="$info" python3 -c '
import json, os
d = json.loads(os.environ["INFO_V"])
c = d.get("changesAt") or ""
print("|".join([d["window"], str(d["multiplier"]), c, c[11:16], d.get("peakPolicy") or "allow",
                str((d.get("rates") or {}).get("outputPer1M", ""))]))' 2>/dev/null)" || {
    sc_die 2 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): price-window output unparsable"; }
  IFS='|' read -r SC_PRICE_WINDOW SC_PRICE_MULT SC_PRICE_CHANGES hhmm policy out_rate <<< "$parsed"
  SC_PRICE_AT_START="${SC_PRICE_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
  if [ "$SC_PRICE_WINDOW" = "flat" ]; then
    SC_PRICE_SUMMARY=""
    return 0
  fi
  SC_PRICE_SUMMARY="window=$SC_PRICE_WINDOW until $hhmm UTC, output \$$out_rate/1M"
  if [ "$SC_PRICE_WINDOW" = "peak" ] && [ "$policy" = "refuse" ] && [ "$SC_PEAK_AUTHORIZED" -ne 1 ]; then
    if [ "${SC_CHECK_MODE:-0}" = 1 ]; then
      echo "UNAVAILABLE (peak pricing window until $hhmm UTC; -W only on the user's word)"
      exit 11
    fi
    sc_die 11 \
      "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): peak pricing window until $hhmm UTC ($SC_PRICE_SUMMARY)." \
      "  The registry sets gate.peakPolicy=refuse for this model. Wait for off-peak, or re-run with -W" \
      "  ONLY if the user explicitly authorized peak pricing for this dispatch. -A does not override this."
  fi
  if [ "$SC_PRICE_WINDOW" = "peak" ]; then
    echo "[sidecar] $SC_ALIAS $SC_PRICE_SUMMARY (peak rates${SC_PEAK_AUTHORIZED:+; -W given})" >&2
  else
    echo "[sidecar] $SC_ALIAS $SC_PRICE_SUMMARY" >&2
  fi
  return 0
}

# ----------------------------------------------------------------- cleanup registry
# One EXIT trap, many cleanups. A bare `trap ... EXIT` REPLACES whatever is installed, with no
# error and no warning, so a launcher that installed its own would silently disable the
# disclosure tier's scratch-dir removal -- leaking a directory per run with nothing pointing at
# the cause. Every cleanup registers here instead.
SC_CLEANUP=()
sc_run_cleanups() {
  # `local rc=$?` captures the exit status BEFORE any other command runs — `local i` alone (the
  # next line) is itself a command and overwrites $? with its own (0), which is the standard trap
  # gotcha this guards against. A registered cleanup that needs the exiting status (durable-file
  # writing, below) reads $rc, never $?.
  local rc=$? i
  # Reverse order: a later registration may depend on an earlier one's resource, so it unwinds
  # first.
  for (( i=${#SC_CLEANUP[@]}-1; i>=0; i-- )); do eval "${SC_CLEANUP[$i]}"; done
}

# ------------------------------------------------------------- durable exit/output files (E1)
# Registered once -R is parsed (sc_parse_flags, below) and only outside a detached job — the -X
# wrapper (sc_detach_launch) already owns .out/.exit there, and registering a second writer would
# race it. This is what makes a killed harness task a solved problem instead of a caller-side
# retry: the launcher process itself, on EVERY exit path including a gate refusal that never
# reaches sc_write_record, writes the last word on the run before it exits. Design:
# .claude/plans/sidecar-detach.md §1.
sc_check_completion() {
  if [ -n "${SIDECAR_CHECK_OWNER:-}" ] && [ "$SIDECAR_CHECK_OWNER" = "${SC_LAUNCH_ID:-}" ]; then return 0; fi
  [ "${SC_CHECKED_RECORD:-}" != "$1" ] || return 0
  local check_args=(--record "$(sc_to_native "$1")" --root "$(sc_to_native "$2")")
  [ -z "${3:-}" ] || check_args+=(--capture "$(sc_to_native "$3")")
  python3 "$SC_ROOT/tools/sidecar_completion.py" "${check_args[@]}" >&2 || :
  SC_CHECKED_RECORD="$1"
}

sc_write_durable_files() {
  local rc="$1" exitf outf
  [ -n "$SC_RECORD" ] || return 0
  [ "$SC_FINISH_HANDED_OFF" = 1 ] && return 0
  exitf="${SC_RECORD}.exit"
  # Output must land first: publishing .exit promises that a successful .out write is readable.
  # Temp file + mv prevents partial results, and write failures keep the launcher exit status.
  if [ -n "${OUTPUT:-}" ]; then
    outf="${SC_RECORD}.out"
    if printf '%s' "$OUTPUT" > "${outf}.tmp" 2>/dev/null && mv "${outf}.tmp" "$outf" 2>/dev/null; then
      :
    else
      echo "[sidecar] could not write $outf" >&2
      rm -f "${outf}.tmp" 2>/dev/null
      return 0
    fi
  fi
  sc_check_completion "$SC_RECORD" "${SC_WORKDIR:-$(pwd)}"
  if printf '%s\n' "$rc" > "${exitf}.tmp" 2>/dev/null && mv "${exitf}.tmp" "$exitf" 2>/dev/null; then
    :
  else
    echo "[sidecar] could not write $exitf" >&2
    rm -f "${exitf}.tmp" 2>/dev/null
  fi
  return 0
}
# ---------------------------------------------------------------- resume (universal)
# Re-invoke a child whose run ended on a rescuable ending, keeping the turns already paid for.
# RESUME IS NOT RETRY: --resume continues the same session; a fresh dispatch re-buys it. That is
# what keeps this inside the rule against auto-retrying a billed call rather than in breach of it.
# Policy and the two endings: reference/sidecar_runs.md §Resume. Classifier:
# tools/sidecar_resume_check.py (deterministic faults are rejected there, never resumed here).
#
# The launcher owns flag assembly, so it passes its own run function in; this owns the policy.
# Usage, after `rc=$?` and BEFORE the final `printf '%s\n' "$OUTPUT"`:
#     sc_resume_loop run_claude "$SC_PROGRESS"
# Reads and rewrites OUTPUT and rc in the caller's scope; exports SC_RESUME_SID for run_claude.
# ------------------------------------------------------------------ stall watchdog
# The stall watchdog SIGNALS; it never ends a run. A paid run silent for SC_STALL_SEC seconds is
# suspected dead (an upstream that never answers, a classifier that never returns, a child wedged
# on a socket), so it writes `<record>.stall` (idle seconds and a UTC stamp) and keeps watching;
# the next work event deletes the marker, and the session decides — inspect the progress stream and
# the process, then re-arm the watcher or stop the job through tools/reap.py. The watch is on the
# -P progress stream (stream-json only: `-o json` emits nothing until the end, so there is nothing
# to watch and the watchdog stays off with a notice). Silence counts from the last WORK event:
# api_retry and other system lines are not progress (measured 2026-09-14: ten 429 retries kept six
# lanes "live"). -Z <sec> / SIDECAR_STALL_SEC override; 0 disables.
SC_STALL_SEC="${SIDECAR_STALL_SEC:-900}"
SC_WATCH_POLL_SEC="${SIDECAR_WATCH_POLL_SEC:-10}"
# Consecutive 429 api_retry lines, with no work between them, that end a run as a provider usage
# limit. 10 is the CLI's own max_retries (both 2026-09-14 streams), so a 429 the child could still
# clear inside its own retry budget is never cut short; a child retrying past it is stopped.
SC_USAGE_LIMIT_RETRIES="${SIDECAR_USAGE_LIMIT_RETRIES:-10}"
# Seconds a child may stay alive after its terminal result before the launcher ends its tree. claude -p
# keeps running after its result while a background task it started runs (a run_in_background call, or a
# foreground Bash call the CLI moved to the background at its timeout), and each task's end re-wakes it
# for another billed turn. A child with nothing pending exits within seconds of its result, so the grace
# only bounds the bookkeeping after it; the run keeps the first result's code (0 success, 1 error).
# watch_records.py and sidecar_fanout --status read a child alive well past this as lingering.
# SIDECAR_RESULT_GRACE_SEC overrides; 0 leaves the child to its own exit.
SC_RESULT_GRACE_SEC="${SIDECAR_RESULT_GRACE_SEC:-30}"
SC_USAGE_LIMITED=0
SC_USAGE_LIMIT_RESET=""
SC_USAGE_LIMIT_SID=""
SC_USAGE_MARKED=0

sc_kill_tree() {
  # $1 = the bash pid of the watched subshell; taskkill /T needs its Windows pid.
  local w
  w="$(ps -W 2>/dev/null | awk -v p="$1" '$1==p{print $4}')"
  [ -n "$w" ] && MSYS_NO_PATHCONV=1 taskkill /PID "$w" /T /F >/dev/null 2>&1
  kill "$1" 2>/dev/null
  return 0
}

# sc_run_watched <run_fn> [<progress-file>] -> sets OUTPUT and rc, appends to the progress file.
sc_prepare_record() {
  [ -n "$SC_RECORD" ] || return 0
  [ "${SC_LAUNCH_PREPARED:-0}" = 1 ] && return 0
  local launch record prepared
  sc_ensure_launch_id || return 1
  record="$(sc_to_native "$SC_RECORD")" || return 1
  [ -n "$record" ] || return 1
  prepared="$(RECORD_V="$record" PARENT_V="$SC_PARENT_SESSION_ID" \
    LAUNCH_V="$SC_LAUNCH_ID" LABEL_V="$SC_LABEL" \
    STALE_V="$SC_INVENTORY_STALE_SECONDS" LIMIT_V="$SC_INVENTORY_SWEEP_LIMIT" python3 - <<'PY'
import json, os, pathlib, tempfile, time, uuid
record = pathlib.Path(os.environ["RECORD_V"]).absolute()
launch = os.environ["LAUNCH_V"]
if str(uuid.UUID(launch)) != launch:
    raise ValueError("invalid launch identity")
try:
    stale_seconds = max(0, int(os.environ["STALE_V"]))
    sweep_limit = max(0, int(os.environ["LIMIT_V"]))
except (KeyError, ValueError):
    stale_seconds, sweep_limit = 604800, 32
cutoff = time.time() - stale_seconds
stale = []
for pattern in ("fanout-*.inventory.json", ".sidecar-inventory-*"):
    for candidate in record.parent.glob(pattern):
        try:
            mtime = candidate.stat().st_mtime
            if mtime < cutoff:
                stale.append((mtime, candidate))
        except OSError:
            pass
for _, candidate in sorted(stale)[:sweep_limit]:
    try:
        candidate.unlink()
    except OSError:
        pass
path = record.parent / ("fanout-" + launch + ".inventory.json")
data = {"schemaVersion": 1, "fanoutId": launch,
        "parentSessionId": os.environ["PARENT_V"] or None,
        "jobs": [{"label": os.environ["LABEL_V"] or "(unlabeled)",
                  "launchId": launch, "recordPath": str(record)}]}
fd, temp_path = tempfile.mkstemp(prefix=".sidecar-inventory-", dir=record.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.link(temp_path, path)
    os.unlink(temp_path)
except Exception:
    try:
        os.unlink(temp_path)
    except OSError:
        pass
    raise
print(launch + "\t" + str(path))
PY
)" || return 1
  SC_LAUNCH_ID="${prepared%%$'\t'*}"
  SC_INVENTORY="${prepared#*$'\t'}"
  SC_LAUNCH_PREPARED=1
  SC_USAGE_MARKED=0
  rm -f "${SC_RECORD}.usage" 2>/dev/null  # a marker from an earlier launch on this path is not this run's
}

sc_cleanup_published_inventory() {
  local publish_rc="$1"
  [ "$publish_rc" = 0 ] && [ -n "$SC_INVENTORY" ] && rm -f -- "$SC_INVENTORY"
  return "$publish_rc"
}

# sc_watch_step <progress-file|-> — fold the stream bytes after the caller's _sc_off through
# tools/sidecar_resume_check.py --watch ("-" reads stdin). Updates the caller's _sc_off, _sc_run and
# _sc_last (the last work event), _sc_result_at / _sc_result_err (when the first terminal result was
# seen, and its code), and SC_USAGE_LIMITED / SC_USAGE_LIMIT_RESET / SC_USAGE_LIMIT_SID.
sc_watch_step() {
  local src="$1" out o work lim reset sid ended
  [ "$src" = "-" ] || src="$(sc_to_native "$src")"
  out="$(python3 "$SC_ROOT/tools/sidecar_resume_check.py" --watch "$src" "${_sc_off:-0}" "${_sc_run:-0}" "${SC_USAGE_LIMIT_RETRIES:-10}" 2>/dev/null)" || return 0
  # A tab is IFS whitespace, so `read` would merge an empty field into the next; split on a non-blank byte.
  IFS=$'\x1f' read -r o work _sc_run lim reset sid ended <<< "${out//$'\t'/$'\x1f'}"
  case "$_sc_run" in ''|*[!0-9]*) _sc_run=0 ;; esac
  case "$o" in ''|*[!0-9]*) ;; *) _sc_off="$o" ;; esac
  [ "$work" = 1 ] && _sc_last=$(date +%s)
  case "$ended" in 0|1) [ -n "${_sc_result_at:-}" ] || { _sc_result_at=$(date +%s); _sc_result_err="$ended"; } ;; esac
  [ -n "$reset" ] && SC_USAGE_LIMIT_RESET="$reset"
  [ -n "$sid" ] && SC_USAGE_LIMIT_SID="$sid"
  [ "$lim" = 1 ] && SC_USAGE_LIMITED=1
  { [ "$_sc_run" -gt 0 ] || [ "$lim" = 1 ]; } && sc_mark_usage
  return 0
}

# sc_mark_usage -- on the FIRST usage-limit retry (or terminal usage-limit result) the watch step
# sees, write <record>.usage once per launch. The session watcher (watch_records.py --wake) wakes on
# it while the run keeps retrying, so a blocked seat is known at its first 429, not its tenth. A 429
# run that clears inside one poll (SC_WATCH_POLL_SEC) folds back to 0 and is not marked.
sc_mark_usage() {
  [ "${SC_USAGE_MARKED:-0}" = 1 ] && return 0
  SC_USAGE_MARKED=1
  [ -n "${SC_RECORD:-}" ] || return 0
  local marker
  marker="$(sc_to_native "${SC_RECORD}.usage")" || return 0
  MARKER_V="$marker" TRANSPORT_V="${SC_TRANSPORT:-unknown}" RETRIES_V="${_sc_run:-0}" \
    RESET_V="${SC_USAGE_LIMIT_RESET:-}" HOOKS_V="$(sc_to_native "$SC_ROOT/hooks")" python3 -c '
import json, os, sys, time
sys.path.insert(0, os.environ["HOOKS_V"])
from _hook_state import replace_path
reset = os.environ["RESET_V"]
line = json.dumps({"firstSeen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "transport": os.environ["TRANSPORT_V"], "retries": int(os.environ["RETRIES_V"] or 0),
                   "resetsAt": int(reset) if reset.isdigit() else None})
tmp = os.environ["MARKER_V"] + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(line + "\n")
replace_path(tmp, os.environ["MARKER_V"])
' || { echo "[sidecar] could not write the usage marker ${SC_RECORD}.usage" >&2; return 0; }
  echo "[sidecar] usage-limit retry on ${SC_TRANSPORT:-unknown}: wrote ${SC_RECORD}.usage; the run keeps retrying" >&2
}

# A usage-limit stop: synthetic result event, exit 10, exhausted marker. Never resumed (sc_resume_loop).
sc_finish_usage_limit() { # [<progress-file>]
  [ "${SC_USAGE_LIMITED:-0}" = 1 ] || return 0
  local line sid_json="null"
  [ -n "${SC_USAGE_LIMIT_SID:-}" ] && sid_json="\"$SC_USAGE_LIMIT_SID\""
  line="$(printf '{"type":"result","subtype":"provider_usage_limit","is_error":true,"synthetic":true,"terminal_reason":"provider-usage-limit","session_id":%s,"resets_at":%s,"num_turns":0,"result":"PROVIDER USAGE LIMIT: the %s provider rejected this run (HTTP 429, usage limit). The launcher stopped the child and did not resume it. Salvage from the -P stream; resume deliberately with -r <session_id> after the limit resets."}' \
    "$sid_json" "${SC_USAGE_LIMIT_RESET:-null}" "${SC_TRANSPORT:-unknown}")"
  OUTPUT="${OUTPUT}"$'\n'"${line}"
  [ -n "${1:-}" ] && printf '%s\n' "$line" >> "$1"
  rc=10
  SC_FINISH_REASON="[sidecar] PROVIDER USAGE LIMIT: ${SC_TRANSPORT:-unknown} rejected the run; stopped with exit 10, not resumed. Launches on this provider are refused until $(sc_exhausted_marker) expires."
  printf '%s\n' "$SC_FINISH_REASON" >&2
  sc_mark_exhausted
}

# ------------------------------------------------------- launch memory admission (A5)
# Foreground checks once and refuses at once (exit 12). Detached jobs wait through launch_memory.py,
# publishing its WAIT readings in <record>.memory for the session watcher and fan-out status.
# -T / SIDECAR_TIMEOUT bounds that admission wait when supplied; expiry is exit 12, with no child run.
# Once per launch: codex calls this before its proxy, so sc_run_watched and resumes do not recheck.
# Unreadable samples warn and launch; broken overrides exit 2. `--check` never reaches here.
sc_check_launch_memory() {
  [ "${SC_MEMORY_CHECKED:-0}" = 1 ] && return 0
  SC_MEMORY_CHECKED=1
  local said mem_rc
  if [ -n "${SIDECAR_DETACHED:-}" ]; then
    local marker="${SC_RECORD}.memory" line
    local memory_cmd=(python3 "$SC_ROOT/tools/launch_memory.py" wait --label "${SC_LABEL:-sidecar}")
    [ -z "${SC_TIMEOUT:-}" ] || memory_cmd=(timeout "$SC_TIMEOUT" "${memory_cmd[@]}")
    "${memory_cmd[@]}" 2>&1 | while IFS= read -r line; do
      printf '%s\n' "$line" >&2
      case "$line" in
        "WAIT memory headroom"*|"STILL WAITING"*)
          printf '%s\n' "$line" > "${marker}.tmp" && mv "${marker}.tmp" "$marker" ;;
      esac
    done
    mem_rc=${PIPESTATUS[0]}
    said="$(cat "$marker" 2>/dev/null)"
    rm -f "$marker" "${marker}.tmp"
    case $mem_rc in
      0) return 0 ;;
      124) sc_die 12 "REFUSED memory headroom for ${SC_LABEL:-sidecar}: wait bound ${SC_TIMEOUT}s reached; ${said:-no reading emitted}" ;;
      *) sc_die 2 "[sidecar] memory wait failed (exit $mem_rc; its error above)" ;;
    esac
  fi
  said="$(python3 "$SC_ROOT/tools/launch_memory.py" check --label "${SC_LABEL:-sidecar}" 2>&1)"
  mem_rc=$?
  [ -z "$said" ] || printf '%s\n' "$said" >&2
  case $mem_rc in
    0) return 0 ;;
    3) rc=12; sc_finish_reason_from "$said"; exit 12 ;;
    *) rc=2; sc_finish_reason_from "$said"; exit 2 ;;
  esac
}

# ------------------------------------------------------- duplicate launch guard
# Before any process starts, `tools/reap.py live-launch` scans for a live launcher or seat child
# already running this job (this -f prompt file under the same -l label or -R record; a launch with
# neither is keyed by the prompt alone) or resuming this -r session and prints each PID, launch id and
# age with the reap command that stops it; the launch then exits 13. No flag overrides it: stop the
# old run first. Jobs that share a brief under their own label and record (an A/B's arms, a fan-out's
# jobs) are different jobs. An unreadable process table warns and launches; any other check failure exits 13.
sc_check_duplicate_launch() { # <prompt-file> <resume-sid> [label] [record]
  local prompt="$1" resume="$2" label="${3:-}" record="${4:-}" native="" nrecord="" self="" dup_rc said
  [ -n "$prompt" ] || [ -n "$resume" ] || return 0
  if [ -n "$prompt" ]; then
    native="$(sc_to_native "$(cd "$(dirname "$prompt")" 2>/dev/null && pwd)/$(basename "$prompt")")"
  fi
  if [ -n "$record" ]; then
    nrecord="$(sc_to_native "$(cd "$(dirname "$record")" 2>/dev/null && pwd)/$(basename "$record")")"
  fi
  [ -r "/proc/$$/winpid" ] && self="$(cat "/proc/$$/winpid")"
  said="$(python3 "$SC_ROOT/tools/reap.py" live-launch ${native:+--prompt "$native"} ${resume:+--resume "$resume"} \
    ${label:+--label "$label"} ${nrecord:+--record "$nrecord"} \
    ${self:+--self "$self"} ${SC_LAUNCH_ID:+--launch "$SC_LAUNCH_ID"} \
    ${SIDECAR_FANOUT_ID:+--fanout "$SIDECAR_FANOUT_ID"} 2>&1)"
  dup_rc=$?
  [ -z "$said" ] || printf '%s\n' "$said" >&2
  case "$dup_rc" in
    0|4) return 0 ;;
    3) rc=13; sc_finish_reason_from "$said"; exit 13 ;;
    *) rc=13; sc_die 13 "[sidecar] REFUSED: the duplicate-launch check failed (exit $dup_rc, its error above); nothing started" ;;
  esac
}

sc_run_watched() {
  sc_check_launch_memory
  sc_prepare_record || { OUTPUT=""; rc=2; return 2; }
  SC_RUN_STARTED=1
  local run_fn="$1" progress="${2:-}" tmp w now idle line _sc_off=0 _sc_run=0 _sc_last
  local _sc_result_at="" _sc_result_err="" _sc_ended=0
  case "${SC_STALL_SEC:-0}" in
    ''|*[!0-9]*) sc_die 2 "[sidecar] -Z/SIDECAR_STALL_SEC='${SC_STALL_SEC:-}' is not a whole number of seconds; refusing rather than running unwatched" ;;
  esac
  case "${SC_RESULT_GRACE_SEC:-0}" in
    ''|*[!0-9]*) sc_die 2 "[sidecar] SIDECAR_RESULT_GRACE_SEC='${SC_RESULT_GRACE_SEC:-}' is not a whole number of seconds" ;;
  esac
  if [ -z "$progress" ] || [ "$SC_FORMAT" != "stream-json" ]; then
    [ "${SC_STALL_SEC:-0}" -gt 0 ] && [ -z "$progress" ] && \
      echo "[sidecar] no -P progress stream: the stall watchdog is OFF for this run (nothing to watch)" >&2
    if [ -n "$progress" ]; then OUTPUT="$("$run_fn" | tee -a "$progress")"; else OUTPUT="$("$run_fn")"; fi
    rc=$?
    SC_RUN_FINISHED=1
    sc_watch_step - <<< "$OUTPUT"
    sc_finish_usage_limit "$progress"
    return 0
  fi
  tmp="$(mktemp)"
  _sc_last=$(date +%s)
  # Watch only this run's bytes: a resumed run appends to the previous segment's -P file.
  _sc_off=$(stat -c %s "$progress" 2>/dev/null || echo 0)
  ( "$run_fn" | tee -a "$progress" > "$tmp" ) &
  w=$!
  while kill -0 "$w" 2>/dev/null; do
    sleep "${SC_WATCH_POLL_SEC:-10}"
    sc_watch_step "$progress"
    if [ "$SC_USAGE_LIMITED" = 1 ]; then
      echo "[sidecar] usage-limit rejection in the -P stream — killing the child tree" >&2
      sc_kill_tree "$w"
      break
    fi
    now=$(date +%s); idle=$((now - _sc_last))
    if [ -n "$_sc_result_at" ] && [ "${SC_RESULT_GRACE_SEC:-0}" -gt 0 ] \
        && [ $((now - _sc_result_at)) -ge "$SC_RESULT_GRACE_SEC" ] && kill -0 "$w" 2>/dev/null; then
      echo "[sidecar] the child wrote its terminal result $((now - _sc_result_at))s ago and is still running (a background task would re-wake it for another billed turn): ending the child tree" >&2
      sc_kill_tree "$w"
      _sc_ended=1
      break
    fi
    if [ -n "$SC_RECORD" ] && [ "${SC_STALL_SEC:-0}" -gt 0 ]; then
      if [ "$idle" -ge "$SC_STALL_SEC" ]; then
        printf 'idle=%s at=%s\n' "$idle" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${SC_RECORD}.stall" 2>/dev/null || :
      elif [ -f "${SC_RECORD}.stall" ]; then
        rm -f "${SC_RECORD}.stall"
      fi
    fi
  done
  wait "$w" 2>/dev/null; rc=$?
  [ "$_sc_ended" = 1 ] && rc="$_sc_result_err"
  SC_RUN_FINISHED=1
  OUTPUT="$(cat "$tmp")"; rm -f "$tmp"
  if [ -n "$SC_RECORD" ]; then rm -f "${SC_RECORD}.stall"; fi  # the run ended; no stall stands
  # The child can exit on its terminal usage-limit result between two polls.
  [ "$SC_USAGE_LIMITED" = 1 ] || sc_watch_step "$progress"
  sc_finish_usage_limit "$progress"
  return 0
}

sc_resume_loop() {
  _sc_resume_loop "$@"
  sc_json_contract
}

_sc_resume_loop() {
  local run_fn="$1" progress="${2:-}" det n=0 max="${SC_COMPACT_RESUMES:-3}" _r sid reason
  det="$SC_ROOT/tools/sidecar_resume_check.py"
  [ "${SC_USAGE_LIMITED:-0}" = 1 ] && return 0
  # stream-json only: `-o json` emits no compact_boundary and no per-event stream, so neither
  # ending is detectable. Say so once rather than failing silently.
  if [ "$SC_FORMAT" != "stream-json" ]; then
    echo "[sidecar] -o $SC_FORMAT: resume is unavailable (needs stream-json; -P sets it). A run that compacts or hits a transient upstream fault will end there and the record will store that ending as the deliverable." >&2
    return 0
  fi
  [ -f "$det" ] || { echo "[sidecar] resume classifier missing: $det — resume disabled this run" >&2; return 0; }

  [ "${SC_USAGE_LIMITED:-0}" = 1 ] && return 0
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
    [ "${SC_USAGE_LIMITED:-0}" = 1 ] && return 0
    # One boundary recovery is legitimate. Another summary-only ending without a new tool call
    # adds no work; don't spend the remaining budget answering continuation summaries again.
    _r="$(printf '%s\n' "$OUTPUT" | python3 "$det" 2>/dev/null)"
    if [ "${_r#*$'\t'}" = compaction ] && \
        [ "$(printf '%s\n' "$OUTPUT" | python3 "$det" --work 2>/dev/null)" = 0 ]; then
      SC_FINISH_REASON="[sidecar] NO PROGRESS after resume $n: another compaction summary with no new tool work; stopped recovery, NOT the deliverable. Inspect the session and reduce the read footprint before deliberate recovery."
      echo "$SC_FINISH_REASON" >&2
      [ "${rc:-0}" -ne 0 ] || rc=1
      return 0
    fi
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

# ------------------------------------------------------------------ finish row
# Every launch that ends leaves one spend-ledger row, so no tracker shows it running forever. A run
# whose record publishes keeps that row. Any other exit, once a label or -R is parsed, writes
# {timestamp, parentSessionId, label, transport, exitCode, outcome, reason}: `ended-before-result`
# for a refusal or failure, with `reason` the line the launcher printed, and `finished-without-record`
# for a run without -R that finished. Readers: hooks/dispatch_table.py, tools/watch_records.py,
# tools/orchestration_metrics.py. A launch that ended before its provider run started spent nothing, so its
# row carries `costUSD: 0`: a credit plan's balance reads every row after its anchor, and an unpriced one
# refuses every later launch on that plan.
SC_FINISH_REASON=""
SC_FINISH_HANDED_OFF=0
SC_FINISH_REGISTERED=0
SC_RECORD_PUBLISHED=0
SC_RUN_FINISHED=0
SC_RUN_STARTED=0

# sc_die <exit code> <line> [<line>...] -- print the lines on stderr, keep the first as the finish
# row's reason, exit.
sc_die() {
  local code="$1"; shift
  printf '%s\n' "$@" >&2
  sc_finish_reason_from "$1"
  exit "$code"
}

sc_finish_reason_from() { # <text> -> SC_FINISH_REASON = its first non-blank line
  local line
  while IFS= read -r line; do
    case "$line" in *[![:space:]]*) SC_FINISH_REASON="$line"; return 0 ;; esac
  done <<< "${1:-}"
}

sc_register_finish_row() {
  [ "$SC_FINISH_REGISTERED" = 1 ] && return 0
  SC_FINISH_REGISTERED=1
  sc_on_exit 'sc_write_finish_row "$rc"'
}

sc_write_finish_row() {
  local rc="$1" ledger outcome reason=""
  [ "$SC_FINISH_HANDED_OFF" = 1 ] && return 0
  [ "$SC_RECORD_PUBLISHED" = 1 ] && return 0
  [ -n "$SC_LABEL" ] || [ -n "$SC_RECORD" ] || return 0
  ledger="$SC_LEDGER"
  [ "$ledger" = "__default__" ] && ledger="$SC_LEDGER_DEFAULT"
  [ -n "$ledger" ] || return 0
  if [ "$SC_RUN_FINISHED" = 1 ] && [ -z "$SC_RECORD" ] && [ -z "$SC_FINISH_REASON" ]; then
    outcome="finished-without-record"
  else
    outcome="ended-before-result"
    reason="$SC_FINISH_REASON"
    if [ -z "$reason" ] && [ "$SC_RUN_FINISHED" = 1 ]; then
      reason="the run ended (exit $rc) but its record did not publish"
    elif [ -z "$reason" ]; then
      reason="the launcher exited $rc before a result"
    fi
  fi
  LEDGER_V="$(sc_to_native "$ledger")" RC_V="$rc" OUTCOME_V="$outcome" REASON_V="$reason" \
    PARENT_V="$SC_PARENT_SESSION_ID" LABEL_V="$SC_LABEL" TRANSPORT_V="${SC_TRANSPORT:-}" STARTED_V="$SC_RUN_STARTED" \
    python3 - <<'PY' 2>/dev/null || :
import json, os
from datetime import datetime, timezone
row = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
       "parentSessionId": os.environ.get("PARENT_V") or None, "label": os.environ.get("LABEL_V") or None,
       "transport": os.environ.get("TRANSPORT_V") or None, "exitCode": int(os.environ["RC_V"]),
       "outcome": os.environ["OUTCOME_V"]}
if os.environ.get("REASON_V"):
    row["reason"] = os.environ["REASON_V"]
if os.environ.get("STARTED_V") != "1":
    row["costUSD"] = 0
with open(os.environ["LEDGER_V"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row) + "\n")
PY
}

# ------------------------------------------------------------------- disclosure tier
# Constructive disclosure: the child runs isolated and the tier appends exactly what it
# names. `full` = in-repo, no DISCLOSURE appends; `bare`/`pointer` = scratch run-cwd, with the
# repo grant unchanged so the child keeps Read access either way. One append rides EVERY tier
# and every model: the autonomy rail below. It is transport, not disclosure — a sidecar child
# has no human on the other end, and a `-D full` child in a sealed arm root reads that root's
# frozen hooks, so no guard file in this repo can reach it.
# Every part is appended into ONE temp file passed once (the CLI keeps only the last
# --append-system-prompt-file); its path is absolute because the child resolves it against its
# own cwd, not this script's.
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
[sidecar] You run UNATTENDED. No human reads anything you write until you exit, so a question in your final message is answered by nobody and the run is recorded as having delivered nothing. Never end on a question or a request for direction. When a choice is yours to make, make it, state it in the deliverable as an assumption, and finish. When a gate, a failing suite or a missing input blocks a step, record the blocker in the deliverable and still return everything the brief asked for. Run every command whose result you need in the foreground and wait for it. This overrides any run_in_background default your instructions carry: nothing wakes you after your turn ends, so a background job you start dies with your turn. A run that outlasts the foreground Bash cap starts detached and is waited on in foreground until-loop calls (guards/any.md).
SC_AUTONOMY
  sc_on_exit "rm -f \"$auto_tmp\" 2>/dev/null || :"
  # The CLI keeps only the last --append-system-prompt-file, so every part below goes into this one file, passed once.
  _sc_append_part() { printf '\n' >> "$auto_tmp"; cat "$1" >> "$auto_tmp"; }
  case "$SC_DISCLOSURE" in
    bare|pointer)
      SC_RUN_CWD="$(mktemp -d)"
      # The child (a Windows process) may still hold SC_RUN_CWD when EXIT fires;
      # a busy-dir rm must not fail the script.
      sc_on_exit 'rm -rf "$SC_RUN_CWD" 2>/dev/null || :'
      # An omitted -G is `any` on the hook route; use the same default here.
      local shape="${SC_SHAPE:-any}"
      # Assemble exactly what the SessionStart hook would inline — any.md plus the shape
      # file at one tier, followed by this child model's registry driver notes. The raw
      # shape file ships BOTH tiers and omits any.md, and no hook fires in the isolated
      # tiers to correct it. SC_MODEL is already resolved by sc_resolve_model to the id
      # the launcher sends to the CLI/proxy; `detailed` matches the hook's default.
      local guard_tmp
      guard_tmp="$(mktemp)"
      python3 "$SC_ROOT/tools/guard_text.py" "$shape" detailed "$SC_MODEL" > "$guard_tmp" || {
        rm -f "$guard_tmp" 2>/dev/null || :
        sc_die 2 "could not assemble guards for shape '$shape' — refusing to dispatch unguarded"
      }
      _sc_append_part "$guard_tmp"
      rm -f "$guard_tmp" 2>/dev/null || :
      ;;
  esac
  if [ "$SC_DISCLOSURE" = "pointer" ]; then
    local mem="$SC_ROOT/auto-memory/MEMORY.md"
    [ -f "$mem" ] || sc_die 2 "pointer disclosure requires $mem"
    _sc_append_part "$mem"
  fi
  local cf
  for cf in ${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"}; do
    [ -f "$cf" ] || sc_die 2 "context file not found: $cf"
    _sc_append_part "$cf"
  done
  # mktemp hands back an MSYS path (/tmp/...). /tmp is Git Bash's own mount and does not exist for the
  # native child, which would read the flag as a missing file.
  auto_arg="$auto_tmp"
  command -v cygpath >/dev/null 2>&1 && auto_arg="$(cygpath -m "$auto_tmp")"
  SC_APPEND_ARGS=(--append-system-prompt-file "$auto_arg")
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
    [ "$a" = "-W" ] && SC_PEAK_AUTHORIZED=1
    case "$prev" in
      -m) SC_MODEL="$a" ;;
      -p) SC_PERM_MODE="$a" ;;
      -G) SC_SHAPE="$a" ;;
      -Q) SC_TIER="$a" ;;
      -D) SC_DISCLOSURE="$a" ;;
      -Y) SC_PIN_BASIS="$a" ;;
    esac
    prev="$a"
  done
  sc_validate_enums
  sc_check_dispatch_facts "$@"
  return 0
}

sc_check_dispatch_facts() {
  SC_FACT_ROOT="$SC_ROOT" SC_FACT_MODEL="$SC_MODEL" python3 - "$@" <<'PY'
import os, sys
from pathlib import Path
root = Path(os.environ['SC_FACT_ROOT'])
sys.path[:0] = [str(root / 'tools'), str(root / 'hooks')]
import sidecar_argv, dispatch_size, model_registry, orchestration_metrics, _dispatch_checks
args = sys.argv[1:]
flags = sidecar_argv.sidecar_flags(args[1:] if args[:1] == ['--check'] else args)
model = flags.get('m') or os.environ['SC_FACT_MODEL']
effort = flags.get('e') or 'unspecified'
profile = flags.get('G') or 'any'
print(f'requested pin: {model}·{effort}')
brief = flags.get('f')
if brief:
    try:
        content = Path(brief).read_bytes()
        tokens = dispatch_size.window_fit(None, len(content.decode('utf-8', errors='replace')))['tokens']
        print(f'brief: {len(content)} bytes; estimated tokens={tokens}')
        size = dispatch_size.dispatch_size({'jobs': [{'promptPath': brief, 'model': model}]}, root=Path.cwd())
        print(dispatch_size.format_line(size))
    except OSError as exc:
        print(f'brief: unreadable ({exc})')
    except (KeyError, IndexError, ValueError) as exc:
        print(f'brief: unsized ({type(exc).__name__}: {exc})')
else:
    print('brief: not supplied')
try:
    tier = _dispatch_checks.job_tier(profile, flags.get('Q'))[0]
except ValueError as exc:
    tier = None
    print(f'tier: {exc}')
print(f'profile={profile}; tier={tier}' if tier else f'profile={profile}; no tier mapping exists')
try:
    data = model_registry.load()
    entry = model_registry.resolve(model, data)
    if tier:
        columns = ('model', 'strengths', 'evidence', 'intel', 'deleg', 'speed', 'cost', 'billing', 'taste', 'effort', '±')
        rows = model_registry.parse_ladder_rows((root / 'reference/model_ladder_evidence.md').read_text(encoding='utf-8').splitlines(), columns)
        matched = [row for row in rows if tier in model_registry.ladder_cell_tiers(row['model'], data)
                   and entry['id'] in {m['id'] for m in model_registry.ladder_rows(row['model'], data)}]
        for row in matched:
            print(f"ladder row: {row['model']} ({row['strengths']}); rung: {row['effort']}")
        if not matched:
            print('ladder row: none for this tier and requested model')
    rating = orchestration_metrics.latest_shape_rating(profile, model, data)
    print(f"shape rating: {rating['shape']}; note: {rating.get('note') or 'not recorded'}" if rating else 'shape rating: none exists')
except (OSError, ValueError, model_registry.RegistryError) as exc:
    print(f'dispatch facts: unreadable ({exc})')
PY
}

# `--check` runs the SAME refusal gates a dispatch runs (availability, host band, provider
# capacity, price window), so a refusal arrives synchronously before a model request.
sc_check_gates() {
  sc_resolve_model
  sc_gate_availability
  [ -n "${SC_MIN_BAND:-}" ] && sc_gate_band
  sc_gate_capacity
  SC_CHECK_MODE=1 sc_gate_price_window
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
# The -f prompt file as an absolute native path (`C:/...`): a hook opens it from the child's cwd.
sc_prompt_file_abs() {
  local abs
  abs="$(cd "$(dirname "$SC_PROMPT_FILE")" && pwd)/$(basename "$SC_PROMPT_FILE")"
  command -v cygpath >/dev/null 2>&1 && abs="$(cygpath -m "$abs")"
  printf '%s' "$abs"
}

sc_scrub_env() {
  SC_SCRUB=()
  local name _
  while IFS='=' read -r name _; do
    case "$name" in CLAUDE*|ANTHROPIC*|SIDECAR_LAUNCH_ID|SIDECAR_SEAT_LAUNCH_ID|SIDECAR_CHECK_OWNER) SC_SCRUB+=(-u "$name") ;; esac
  done < <(env)
  # PASS-THROUGH after the unsets (`env -u X X=v` re-sets): the print-mode background-wait
  # ceiling. `claude -p` kills a child whose background task is still running at 600 s and exits 0
  # with no deliverable. No sidecar child is granted `Workflow` -- the resolver refuses it and
  # `SC_DISALLOWED` names it (tools/tool_profiles.py) -- but a Bash background task still holds the
  # child after its result, which sc_run_watched ends (SC_RESULT_GRACE_SEC); the ceiling is passed
  # through only because a caller that sets it means it.
  local ceiling="${CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS:-}"
  [ -n "$ceiling" ] && SC_SCRUB+=(CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="$ceiling")
  # The parent-session tag: the scrub drops CLAUDE_CODE_SESSION_ID and a detached child outlives its
  # wrapper, so tools/reap.py proves the child is this session's through this tag.
  [ -n "${SC_PARENT_SESSION_ID:-}" ] && SC_SCRUB+=(SIDECAR_PARENT_SESSION_ID="$SC_PARENT_SESSION_ID")
  # The brief's identity rides every launcher's child: hooks/wide_test_guard.py reads the dispatcher's prompt
  # (and its .amend.md) to learn which wide test runs this delegate was ordered to make.
  [ -n "${SC_PROMPT_FILE:-}" ] && SC_SCRUB+=(CLAUDE_CODE_SIDECAR_PROMPT_FILE="$(sc_prompt_file_abs)")
  [ -n "${SC_LABEL:-}" ] && SC_SCRUB+=(CLAUDE_CODE_SIDECAR_LABEL="$SC_LABEL")
  # The launch id rides the child too: tools/reap.py and watch_records.py find a seat that outlived its
  # launcher by it. The seat mark makes a launch nested in the child take its own id (see SC_LAUNCH_ID).
  [ -n "${SC_LAUNCH_ID:-}" ] && SC_SCRUB+=(SIDECAR_LAUNCH_ID="$SC_LAUNCH_ID" SIDECAR_SEAT_LAUNCH_ID="$SC_LAUNCH_ID")
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

# The one launch-id generator: sc_prepare_record accepts only the canonical hyphenated UUID. Exported,
# so every process the launch starts (proxy, shim, child) carries it.
sc_ensure_launch_id() {
  [ -n "$SC_LAUNCH_ID" ] || SC_LAUNCH_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  [ -n "$SC_LAUNCH_ID" ] || return 1
  export SIDECAR_LAUNCH_ID="$SC_LAUNCH_ID"
}

# sc_write_record <raw-output> <exit-code>
sc_write_record() {
  local output="$1" rc="$2"
  [ -n "$SC_RECORD" ] || return 0
  sc_ensure_launch_id || return 1
  local hb hs dsha raw_tmp ledger_default publish_rc=0
  hb="$(git -C "$SC_WORKDIR" describe --tags --exact-match HEAD 2>/dev/null || echo unknown)"
  hs="$(sc_harness_head)"
  dsha="$(sc_doctrine_sha)"
  # Payload goes via temp file: `python3 -` reads its PROGRAM from stdin, so a heredoc and a
  # data pipe cannot share the channel.
  raw_tmp="$(mktemp)"
  printf '%s' "$output" > "$raw_tmp"
  ledger_default="$SC_LEDGER_DEFAULT"
  sc_check_completion "$SC_RECORD" "$SC_WORKDIR" "$raw_tmp"

  LIBDIR_V="$(sc_to_native "$SC_ROOT/scripts/lib")" RAW_V="$(sc_to_native "$raw_tmp")" EFFORT_V="$SC_EFFORT" MODEL_V="$SC_MODEL" RC_V="$rc" \
    PARENT_SESSION_V="$SC_PARENT_SESSION_ID" LAUNCH_ID_V="$SC_LAUNCH_ID" \
    HB_V="$hb" HS_V="$hs" DSHA_V="$dsha" REC_V="$(sc_to_native "$SC_RECORD")" \
    SCHEMA_V="$(sc_to_native "$SC_SCHEMA_FILE")" LEDGER_V="$SC_LEDGER" LABEL_V="$SC_LABEL" \
    DISCLOSURE_V="$SC_DISCLOSURE" LEDGER_DEFAULT_V="$ledger_default" \
    TRANSPORT_V="$SC_TRANSPORT" COSTMODEL_V="$SC_COST_MODEL" \
    ATTEST_V="${SC_ATTESTED_MODEL:-}" PARSER_V="${SC_RECORD_PARSER:-claude}" \
    REGCLI_V="$SC_REGISTRY_CLI" PROGRESS_V="$(sc_to_native "${SC_PROGRESS:-}")" CTXWIN_V="${SC_CONTEXT_TOKENS:-}" CTXSOURCE_V="${SC_CONTEXT_WINDOW_SOURCE:-}" CTXOVERRIDE_V="${SIDECAR_COMPACT_AT_TOKENS:+${AC_AUTO_COMPACT_WINDOW:-}}" PRICE_AT_V="${SC_PRICE_AT_START:-}" \
    USAGE_LIMIT_V="${SC_USAGE_LIMITED:-0}" RESET_V="${SC_USAGE_LIMIT_RESET:-}" \
    PERM_V="${SC_PERM_MODE:-}" MAXOUT_V="${SC_MAX_OUTPUT:-}" STALL_V="${SC_STALL_SEC:-}" \
    PINBASIS_V="${SC_PIN_BASIS:-}" TOOLFRAME_V="${SC_TOOL_FRAME:-}" \
    RESUMED_V="${SC_RESUMED_FROM:-}" PROF_V="${SC_TOOL_PROFILE:-}" PROMPT_V="${SC_PROMPT_FILE:-}" \
    python3 - <<'PYEOF' || publish_rc=$?
import json, os, sys, tempfile, time
raw = open(os.environ["RAW_V"], encoding="utf-8", errors="replace").read()
parser = os.environ.get("PARSER_V") or "claude"
sys.path.insert(0, os.path.join(os.path.dirname(os.environ["REGCLI_V"])))
sys.path.insert(0, os.environ["LIBDIR_V"])
sys.path.insert(0, os.path.join(os.environ["LIBDIR_V"], "..", "..", "hooks"))
import model_registry as _reg
import plugin_registry as _plugin_registry
from _hook_state import replace_path

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
    cache_write, cache_write_1h = 0, 0
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
    import cli_envelope
    _evts = cli_envelope.events(raw)
    data = cli_envelope.result_event(_evts) or {}
    if not data.get("session_id"):
        # A run that ended before its result event (killed, stalled, crashed) still names its session in every
        # other event; the record keeps it so the run can be resumed.
        _sid = cli_envelope.session_id(_evts)
        if _sid:
            data = dict(data, session_id=_sid)
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
        cache_write = sum(v.get("cacheCreationInputTokens") or 0 for v in mu.values())
    else:
        fresh = usage.get("input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        out_tok = usage.get("output_tokens") or 0
        cache_write = usage.get("cache_creation_input_tokens") or 0
    cache_write, cache_write_1h = _reg.cache_write_split(cache_write, usage.get("cache_creation"))
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
# costUSD is COMPUTED from raw token counts at the per-model LIST rates in
# .claude/reference/external_models.json (the ONE price home - never re-author a rate here,
# in code or in a comment). NEVER read total_cost_usd: Claude Code prices non-Anthropic tokens
# on its Anthropic table, so that field is inflated by the rate ratio - ~39x on flash.
#
# Keyed on servedModel, falling back to requestedModel when modelUsage is absent.
# TRANSPORT_V is the paying plan; a plan owns no rows, so the served row is looked up on its route.
_route = _reg.route_of(os.environ["TRANSPORT_V"]) if os.environ.get("TRANSPORT_V") else None
_served_row = _reg.row_by_id(served[0], transport=_route) if len(served) == 1 else None
_cost_model = (_served_row or {}).get("id") or os.environ["MODEL_V"]
cost_tier_basis = 'untiered'
try:
    cost = _reg.price_run(_cost_model, fresh, cache_read, out_tok,
                          at=os.environ.get("PRICE_AT_V") or None,
                          cache_write=cache_write, cache_write_1h=cache_write_1h)
    cost_basis = _cost_model
    if os.environ.get("COSTMODEL_V") == "plan-quota":
        # A plan-quota run spends no dollars, but its list-price cost is the figure two models'
        # runs compare on. The basis marks it: consumers that total SPEND branch on costModel and
        # skip it, and a null would leave a run that has a price unpriced.
        cost_basis = _reg.LIST_PRICE_BASIS
except Exception as exc:
    # Never lose the record over pricing: keep the tokens, flag the gap.
    cost = None
    cost_basis = ("plan-quota (no list price: %s)" if os.environ.get("COSTMODEL_V") == "plan-quota"
                  else "UNPRICED (%s)") % exc
# The billing window the run STARTED in (sc_gate_price_window captured that instant; a run that
# crosses a boundary is priced at its start), and the registry version of the requested model, so
# board rows can separate versions served behind one versionless vendor name.
try:
    _price_win = _reg.price_window(_reg.resolve(os.environ["MODEL_V"])["transport"],
                                   at=os.environ.get("PRICE_AT_V") or None)
except Exception:
    _price_win = {}
try:
    _model_version = _reg.resolve(os.environ["MODEL_V"]).get("version")
except Exception:
    _model_version = None
def _projects_root(plan):
    return _reg.transport_projects_root(plan)
def _turn1_context(sid):
    try:
        path = _reg.session_transcript(sid, os.environ.get("TRANSPORT_V") or None)
        if path:
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
def _tool_versions(plan):
    # The search tooling the child's login ran, so a board can tell arms or judges on different plugin
    # builds apart (the revision, not the version label): the user-scope semantic-search@dreb in
    # <login>/plugins/installed_plugins.json, null when that login lacks it or the child is not Claude Code (codex runs no Claude plugin).
    version = None
    if parser == "claude":
        try:
            login = os.path.dirname(_projects_root(plan))
            version = _plugin_registry.build_identity(_plugin_registry.user_row(login, "semantic-search@dreb"))
        except Exception:
            version = None
    return {"semanticSearch": version}
def _context_window(model):
    enforced_source = os.environ.get("CTXSOURCE_V")
    if enforced_source:
        return int(os.environ.get("CTXWIN_V") or 0) or None, enforced_source
    # Other launchers retain their existing window contract.
    if model.lower().endswith("[1m]"):
        return 1000000, "model-suffix"
    declared = int(os.environ.get("CTXWIN_V") or 0) or None
    if declared:
        return declared, "launcher-declared"
    try:
        listed = (_reg.resolve(model).get("limits") or {}).get("contextTokens")
    except Exception:
        listed = None
    return (int(listed), "registry") if listed else (None, "unknown")
ctxwin, ctxwin_source = _context_window(os.environ["MODEL_V"])
# A -f brief registers hooks/sidecar_recompact_reprompt.py on a Claude client (sc_settings_with_bench_guard);
# its selected rail names what the child's compactions keep, so a board never pools two rails.
# A stamp that cannot be read is null, never a lost record: the run is already paid for.
compaction_rail = None
rail_applies = bool(os.environ.get("PROMPT_V")) and parser == "claude"
if rail_applies:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.environ["REGCLI_V"])), "hooks"))
    try:
        from sidecar_recompact_reprompt import selected_rail
        compaction_rail = selected_rail(os.environ)
    except Exception as exc:
        print(f'[sidecar] WARN: compactionRail unreadable ({type(exc).__name__}: {exc}); recorded null', file=sys.stderr)
if turn1:
    pct = f' ({100.0 * turn1 / ctxwin:.0f}% of {ctxwin})' if ctxwin else ' (window unknown)'
    print(f'[sidecar] turn-1 context {turn1} tokens{pct}', file=sys.stderr)
record = {
    "launchId": os.environ.get("LAUNCH_ID_V") or None,
    "parentSessionId": os.environ.get("PARENT_SESSION_V") or None,
    "workKey": os.environ.get("SC_WORK_KEY") or None,
    "sessionId": data.get("session_id"),
    "label": os.environ.get("LABEL_V") or None,
    "transport": os.environ.get("TRANSPORT_V") or None,
    "costModel": os.environ.get("COSTMODEL_V") or None,
    "servedModel": served[0] if len(served) == 1 else (served or None),
    "requestedModel": os.environ["MODEL_V"],
    "effort": os.environ["EFFORT_V"] or None,
    # Why this pin was chosen and whether the slice's test intent is clearly defined, as the caller
    # wrote it (`-Y <pick>,<spec>`). Absent means the launch recorded none -- a real answer the
    # readouts count (`pins.spec-unrecorded.all`), never a value to guess at.
    "pinBasis": os.environ.get("PINBASIS_V") or None,
    "disclosureTier": os.environ.get("DISCLOSURE_V") or None,
    "harnessBase": os.environ["HB_V"],
    "harnessSession": os.environ["HS_V"],
    # The .claude/ actually loaded, distinct from harnessBase/harnessSession (which are the
    # CODE tag) -- absent means the root's .claude/ is that tag's own frozen native copy.
    "doctrineSha": os.environ.get("DSHA_V") or None,
    "inputTokens": fresh,
    "cacheReadTokens": cache_read,
    "cacheWriteTokens": cache_write + cache_write_1h,
    "cacheWrite1hTokens": cache_write_1h,
    "outputTokens": out_tok,
    "costUSD": round(cost, 6) if cost is not None else None,
    "costBasis": cost_basis,
    "costTierBasis": cost_tier_basis,
    "priceWindow": _price_win.get("window"),
    "priceMultiplier": _price_win.get("multiplier"),
    "modelVersion": _model_version,
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
    **({"contextOverride": int(os.environ["CTXOVERRIDE_V"])} if os.environ.get("CTXOVERRIDE_V") else {}),
    **({"compactionRail": compaction_rail} if rail_applies else {}),
    "contextWindowSource": ctxwin_source,
    "turn1ContextPct": (round(100.0 * turn1 / ctxwin, 1) if (turn1 and ctxwin) else None),
    # The child's configuration, on every transport, so a board row can state it (F148): the permission
    # mode, the output cap passed to the child (null = the client's own default, nothing passed), the
    # stall watchdog limit (-Z), and the session a -r run resumed (F144).
    "permissionMode": os.environ.get("PERM_V") or None,
    "toolFrame": os.environ.get("TOOLFRAME_V") or None,
    "maxOutputTokens": int(os.environ["MAXOUT_V"]) if (os.environ.get("MAXOUT_V") or "").isdigit() else None,
    "stallSec": int(os.environ["STALL_V"]) if (os.environ.get("STALL_V") or "").isdigit() else None,
    "resumedFrom": os.environ.get("RESUMED_V") or None,
    "toolVersions": _tool_versions(os.environ.get("TRANSPORT_V") or None),
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
# The tool profile this child was LAUNCHED with, resolved before spend by sc_resolve_tools and
# carried through -r unchanged (a resumed run keeps its original inventory; a profile change is a
# new dispatch, not a resume). `enforcement: strict` means the four flags that bound it were all
# passed -- --tools, --strict-mcp-config and --mcp-config from this profile, with --allowedTools
# kept only as permission preapproval. A record with no toolProfile predates the resolver: a
# reader reports it `unknown`, never as the default.
_prof = os.environ.get("PROF_V") or ""
if _prof.strip():
    try:
        record["toolProfile"] = json.loads(_prof)
    except Exception:
        record["toolProfile"] = {"error": "unparseable profile", "raw": _prof[:400]}
if rate_limit_info is not None:
    record["rateLimitInfo"] = rate_limit_info
if os.environ.get("USAGE_LIMIT_V") == "1":
    record["stopReason"] = "provider-usage-limit"
    _reset = os.environ.get("RESET_V") or ""
    record["usageLimitResetsAt"] = int(_reset) if _reset.isdigit() else None
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
compactions = 0
compaction_ms = 0
for _line in raw.splitlines():
    try:
        _o = json.loads(_line)
    except Exception:
        continue
    if isinstance(_o, dict) and _o.get("subtype") == "compact_boundary":
        compactions += 1
        compaction_ms += ((_o.get("compact_metadata") or {}).get("duration_ms") or 0)
    if not isinstance(_o, dict) or _o.get("type") != "assistant":
        continue
    _u = (_o.get("message") or {}).get("usage") or {}
    peak_req = max(peak_req, (_u.get("input_tokens") or 0)
                   + (_u.get("cache_read_input_tokens") or 0)
                   + (_u.get("cache_creation_input_tokens") or 0))
if peak_req:
    record["peakRequestTokens"] = peak_req
    record["compactions"] = compactions  # a board ranks compactionRail only for a cell that compacted
    record["compactionMs"] = compaction_ms  # client wall time inside the summary requests
    if ctxwin and peak_req > 0.8 * ctxwin:  # the recorded window, one source (_context_window)
        record["contextCeilingWarning"] = True
# schemaValid: with -S, the result text must parse as JSON. (Key-level conformance is the
# caller's check; this catches the prose-instead-of-JSON failure mode cheaply.)
if os.environ.get("SCHEMA_V"):
    try:
        json.loads(data.get("result") or "")
        record["schemaValid"] = True
    except Exception:
        record["schemaValid"] = False
# The opencode launcher's rescues (D8): one resume per empty final turn or schema failure, copied unchanged. A
# launcher that reports no list leaves no key, so a reader counts the run unmeasured, never as zero rescues.
if "rescues" in data:
    record["rescues"] = data["rescues"]
pricing_events = _reg.usage_events(raw)
_progress = os.environ.get('PROGRESS_V')
if _progress and os.path.isfile(_progress):
    with open(_progress, encoding='utf-8', errors='replace') as fh:
        pricing_events.extend(_reg.usage_events(fh.read()))
record = _reg.reprice_record(record, os.environ['REC_V'], events=pricing_events)
if record.get('costUSD') is not None:
    record['costUSD'] = round(record['costUSD'], 6)
record_path = os.path.abspath(os.environ["REC_V"])
record_dir = os.path.dirname(record_path) or "."
fd, temp_path = tempfile.mkstemp(prefix=".sidecar-record-", dir=record_dir)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    replace_path(temp_path, record_path)
except Exception:
    try:
        os.unlink(temp_path)
    except OSError:
        pass
    raise
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
  [ "$publish_rc" -eq 0 ] && SC_RECORD_PUBLISHED=1
  sc_cleanup_published_inventory "$publish_rc"
}


# -t names built-in tools, MCP tools (mcp__*) and permission rules (`Bash(git *)`). `--allowedTools`
# takes all three but only pre-approves, so under bypassPermissions it limits nothing; `--tools` is
# the child's actual built-in set. This prints the built-in names for `--tools`: MCP names dropped
# (they stay in --allowedTools), each rule reduced to its tool. An empty result is `--tools ""`,
# which disables every built-in.
sc_builtin_tools() {
  local -a parts; local out="" t
  IFS=, read -ra parts <<< "$1"
  for t in ${parts[@]+"${parts[@]}"}; do
    t="${t%%(*}"
    t="${t#"${t%%[![:space:]]*}"}"; t="${t%"${t##*[![:space:]]}"}"
    case "$t" in ""|mcp__*) continue ;; esac
    out="${out:+$out,}$t"
  done
  printf '%s' "$out"
}

# `--tools` filters built-ins only; a child otherwise loads every MCP server the parent has (user, project,
# local and plugin), write-capable ones included. Every launcher passes `--strict-mcp-config --mcp-config
# "$(sc_mcp_config ...)"`, so the child's servers follow -t: a server loads only when an mcp__<server>
# name in -t names it, resolved from the same config the parent reads (local > project .mcp.json > user
# > installed plugin, whose server is named plugin:<plugin>:<server>). No mcp__ name, or a name nothing
# defines, loads no server. $2 overrides CLAUDE_CONFIG_DIR for a launcher that gives its child another one.
# The config walk itself lives in tools/tool_profiles.py, so a server name resolves identically
# whether the launcher is building this config or the resolver is validating a profile before
# spend. This function is the mechanism; `sc_resolve_tools` is the fail-closed validation.
sc_mcp_config() { # <-t csv> [config-dir] -> {"mcpServers":{...}} on stdout
  local cfg="${2:-${CLAUDE_CONFIG_DIR:-}}"
  # The child is a native process: an MSYS /tmp path would reach it as a missing file.
  if command -v cygpath >/dev/null 2>&1 && [ -n "$cfg" ]; then
    cfg="$(cygpath -m "$cfg")"
  fi
  SC_WORKDIR="$SC_WORKDIR" python3 "$SC_ROOT/tools/tool_profiles.py" mcp-config "$1" "$cfg"
}
