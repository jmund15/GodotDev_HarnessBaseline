#!/usr/bin/env bash
# antigravity_sidecar.sh — the Antigravity CLI seat (transport antigravity).
#
# Google's official Antigravity CLI (`agy`), run unchanged and headless on the owner's Google AI Pro login,
# serves one job of kind scout, lens, reviewer, author, judge, gate or arm. The launcher never reads, copies or
# forwards anything under ~/.gemini and calls no Google endpoint itself: agy holds its own login.
# Every agy-specific decision lives in tools/agy_seat.py: the run's --settings (the kind's built-in
# families allowed, the rest denied, no access outside the workspace), the live stop (the run ends at the
# first tool no settings rule reaches) and the result envelope with its audit. A run root that is a checkout
# of this project first gets its `.agents/` workspace (tools/agy_workspace.py): agy then loads the CLAUDE
# layers as rules, finds the skills, runs the tool, SessionStart and Stop hooks through `python3 .claude/hooks/agy_hook_bridge.py <event>`
# and serves a read kind the read-only MCP servers. A kind's own MCP registrations are dropped and recorded in
# toolProfile.seatDropped. A kind that may write runs in any checkout: the bridge's guards see no Claude
# transcript, and the ownership checks that read one then deny more, never less.
# `-r` is refused: one fresh conversation per job bounds quota use. agy has no system-prompt flag, so the
# repository root, the autonomy rail, the kind's guards and the -C files ride stdin ahead of the brief.
set -uo pipefail

SC_TRANSPORT=antigravity
case "${BASH_SOURCE[0]}" in
  */sidecar-snapshots/*) SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}" ;;
  *) SC_LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" ;;
esac
. "$SC_LAUNCHER_DIR/lib/sidecar_common.sh"
sc_reexec_snapshot "$@"

AG_SEAT="$SC_ROOT/tools/agy_seat.py"

ag_ensure_output() {
  [ -n "${OUTPUT:-}" ] && return 0
  OUTPUT="$(python3 -c 'import json, sys
print(json.dumps({"type": "result", "result": "Launcher stopped before an agy result (exit %s); see stderr." % sys.argv[1],
                  "structured_output": None, "is_error": True}))' "$1")"
}

ag_preparse_refusal() {
  echo "$1" >&2
  if [ -n "$SC_RECORD" ] && [ -z "${SIDECAR_DETACHED:-}" ]; then
    sc_pid_line "$$" > "${SC_RECORD}.pid" 2>/dev/null || :
    ag_ensure_output 2
    sc_write_durable_files 2
  fi
  exit 2
}

# ag_validate_flags <value-flags> <switches> "$@": any other flag refuses before parsing.
ag_validate_flags() {
  local valued="$1" switches="$2" token takes_value=0 previous=""
  shift 2
  for token in "$@"; do
    if [ "$takes_value" -eq 1 ]; then
      [ "$previous" != '-R' ] || SC_RECORD="$token"
      takes_value=0
      continue
    fi
    previous="$token"
    case "$token" in
      -r) ag_preparse_refusal '-r refused: the antigravity seat runs one fresh conversation per job' ;;
      --) break ;;
      -?)
        case " $valued " in *" $token "*) takes_value=1; continue ;; esac
        case " $switches " in *" $token "*) continue ;; esac
        ag_preparse_refusal "flag $token refused: the antigravity seat does not serve it" ;;
      -*) ag_preparse_refusal "flag $token refused: the antigravity seat does not serve it" ;;
    esac
  done
  [ "$takes_value" -eq 0 ] || ag_preparse_refusal 'missing flag value'
}

# The installed binary's path has one home, tools/agy_seat.py `bin`.
ag_bin() {
  local bin
  bin="$(python3 "$AG_SEAT" bin)" || return 1
  command -v cygpath >/dev/null 2>&1 && bin="$(cygpath -u "$bin")"
  [ -f "$bin" ] || return 1
  printf '%s' "$bin"
}

ag_upstream() {
  python3 - "$SC_MODEL" "$SC_ROOT/tools" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
import model_registry
row = model_registry.resolve(sys.argv[1])
if not row['transport'].startswith('antigravity'):
    raise SystemExit(f"{sys.argv[1]} is on transport {row['transport']}, not an antigravity bucket")
print(row.get('upstreamId') or row['id'])
PY
}

# Each Pro-plan quota bucket is its own transport on this launcher (registry transport antigravity's
# _capacityComment), so the row names the paying bucket: the capacity gate, markers, ledger and record key on it.
ag_set_transport() {
  local transport
  transport="$(python3 "$SC_REGISTRY_CLI" resolve "$SC_MODEL" | python3 -c 'import json, sys; print(json.load(sys.stdin)["transport"])')" ||
    sc_die 2 "[sidecar] cannot resolve the transport of $SC_MODEL"
  case "$transport" in antigravity|antigravity-*) SC_TRANSPORT="$transport" ;;
    *) sc_die 2 "[sidecar] $SC_MODEL is on transport $transport, not an antigravity bucket" ;; esac
}

# `agy models` proves the login and the row's id with no model request. The verdict keys on the exit code
# and the listing, never on message text.
ag_models_gate() {
  local out rc
  out="$("$SC_BIN" models 2>&1)"; rc=$?
  if printf '%s\n' "$out" | grep -qF -- "$SC_UPSTREAM" && [ "$rc" -eq 0 ]; then return 0; fi
  if [ "$rc" -ne 0 ] || ! printf '%s\n' "$out" | grep -qE '[a-z0-9]+(-[a-z0-9.]+)+'; then
    AG_UNAVAILABLE='agy not signed in; run agy once in a terminal'
  else
    AG_UNAVAILABLE="agy lists no $SC_UPSTREAM"
  fi
  return 1
}

ag_record_seat_drops() {
  SC_TOOL_PROFILE="$(printf '%s' "$SC_TOOL_PROFILE" | python3 -c 'import json, sys
p = json.load(sys.stdin)
p["seatDropped"] = {"mcpServers": p["mcpServers"], "tools": []}
p["mcpServers"] = []
p["mcpRegistrations"] = {}
print(json.dumps(p, indent=2))')" || sc_die 2 "[sidecar] could not record the seat's tool drops"
}

if [ "${1:-}" = '--check' ]; then
  ag_validate_flags '-m' '-A -U -W' "${@:2}"
  sc_check_model_override "$@"
  sc_resolve_model
  ag_set_transport
  sc_gate_availability
  SC_BIN="$(ag_bin)" || { echo 'UNAVAILABLE (agy CLI missing)'; exit 4; }
  SC_UPSTREAM="$(ag_upstream)" || exit 2
  ag_models_gate || { echo "UNAVAILABLE ($AG_UNAVAILABLE)"; exit 3; }
  sc_gate_band
  sc_gate_capacity
  SC_CHECK_MODE=1 sc_gate_price_window
  printf 'OK (model=%s cli=agy; no model request)\n' "$SC_UPSTREAM"
  exit 0
fi

ag_validate_flags '-m -e -t -l -d -f -R -P -G -S -D -C -x -T -B -Q -Y -a' '-X -A -W' "$@"
sc_parse_flags "$@"
sc_on_exit 'ag_ensure_output "$rc"'
shift "$SC_SHIFT"
[ "${1:-}" != '--' ] || shift
sc_read_prompt "$@"
sc_validate_enums

case "${SC_SHAPE:-}" in scout|lens|reviewer|author|judge|gate) ;; arm) ;; *) sc_die 2 "-G ${SC_SHAPE:-(missing)} refused: the antigravity seat serves scout, lens, reviewer, author, judge, gate and arm only" ;; esac
[ "$SC_SHAPE" != reviewer ] || [ -n "$SC_SCHEMA_FILE" ] || sc_die 2 '-G reviewer requires -S <schema>'
[ -d "$SC_WORKDIR" ] || sc_die 2 "-d directory missing: $SC_WORKDIR"
[ -z "$SC_SCHEMA_FILE" ] || [ -f "$SC_SCHEMA_FILE" ] || sc_die 2 "-S schema missing: $SC_SCHEMA_FILE"
[ -z "$SC_SCHEMA_FILE" ] || python3 "$SC_ROOT/scripts/lib/json_schema_lite.py" "$(sc_to_native "$SC_SCHEMA_FILE")" || exit 2
sc_resolve_tools
AG_BUILTINS="$(python3 "$AG_SEAT" granted --resolved "${SC_RESOLVED_BUILTINS:-}" --disallowed "${SC_DISALLOWED:-}")" ||
  sc_die 2 "[sidecar] could not map the kind's built-ins"
AG_SERVERS="$(python3 "$AG_SEAT" servers --profile "$SC_TOOL_PROFILE")" || sc_die 2 "[sidecar] could not read the kind's MCP servers"
ag_record_seat_drops
sc_resolve_model
ag_set_transport
sc_gate_availability
SC_BIN="$(ag_bin)" || sc_die 4 'agy CLI missing'
sc_gate_band
sc_gate_capacity
sc_gate_price_window
sc_admit_launch
sc_validate_effort
SC_UPSTREAM="$(ag_upstream)" || exit 2
ag_models_gate || sc_die 3 "[sidecar] REFUSING $SC_ALIAS ($SC_MODEL): $AG_UNAVAILABLE"

SC_PROMPT_TMP="$(mktemp)"
AG_SETTINGS="$(mktemp)"
AG_RAW="$(mktemp)"
sc_on_exit 'rm -f "$SC_PROMPT_TMP" "$AG_SETTINGS" "$AG_RAW" 2>/dev/null || :'
python3 "$AG_SEAT" settings --builtins "$AG_BUILTINS" --servers "$AG_SERVERS" > "$AG_SETTINGS" || sc_die 2 "[sidecar] could not write the run's settings"
# A checkout of this project gets its `.agents/` workspace: the CLAUDE layers as rules, skills, the PreToolUse guards
# through hooks/agy_hook_bridge.py and the read-only MCP servers. Any other root (a bench cell) gets nothing.
AG_WORKSPACE="$(python3 "$SC_ROOT/tools/agy_workspace.py" write --root "$(sc_to_native "$SC_WORKDIR")")" ||
  sc_die 2 "[sidecar] could not write the run root's .agents workspace"
# The workspace bridges SessionStart, whose rails hook reads the exported kind. A root without one fires no project
# hook, so sc_build_disclosure sends the kind's guards on stdin at every tier.
case "$AG_WORKSPACE" in wrote*) ;; *) SC_HOOKLESS=1 ;; esac
# sc_build_disclosure assembles the autonomy rail, the kind's guards (bare/pointer) and the -C files into
# one file, the part a Claude child gets as --append-system-prompt-file; agy takes it on stdin.
sc_build_disclosure
{
  printf 'Repository root: %s\n\n' "$(sc_to_native "$SC_WORKDIR")"
  cat "${SC_APPEND_ARGS[1]}"
  printf '\n'
  case "$AG_WORKSPACE" in
    wrote*) ;;  # agy loads the CLAUDE layers itself, as always-on rules
    *) if [ "$SC_DISCLOSURE" = full ]; then cat "$SC_ROOT/CLAUDE.md" "$SC_ROOT/CLAUDE.core.md"; printf '\n'; fi ;;
  esac
  [ -z "$SC_SCHEMA_FILE" ] || printf 'Return ONLY one JSON object that matches the schema given to the CLI: no markdown fence, no prose.\n\n'
  printf '%s\n' "$SC_PROMPT"
} > "$SC_PROMPT_TMP"

AG_ARGS=(--output-format stream-json --model "$SC_UPSTREAM" --settings "$(sc_to_native "$AG_SETTINGS")" --disable-slash-commands)
[ -z "${SC_EFFORT:-}" ] || [ "$SC_EFFORT" = none ] || AG_ARGS+=(--effort "$SC_EFFORT")
[ -z "$SC_SCHEMA_FILE" ] || AG_ARGS+=(--json-schema "$(sc_to_native "$SC_SCHEMA_FILE")")
[ -z "${SC_TIMEOUT:-}" ] || AG_ARGS+=(--print-timeout "${SC_TIMEOUT}s")
AG_DIRS=()  # -a's folders join the workspace; the seat's audit reads the same list
for ((i = 1; i < ${#SC_ADD_DIRS[@]}; i += 2)); do AG_DIRS+=(--add-dir "$(sc_to_native "${SC_ADD_DIRS[$i]}")"); done
AG_ARGS+=("${AG_DIRS[@]}")
AG_STREAM="${SC_PROGRESS:-$AG_RAW}"
[ -z "$SC_PROGRESS" ] || : > "$SC_PROGRESS"
brief_abs=""
[ -z "${SC_PROMPT_FILE:-}" ] || brief_abs="$(sc_prompt_file_abs)"
SC_RUN_STARTED=1
( cd "$SC_WORKDIR" && export CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" CLAUDE_CODE_SIDECAR_KIND="$SC_SHAPE" \
    ${brief_abs:+CLAUDE_CODE_SIDECAR_PROMPT_FILE="$brief_abs"} &&
  exec python3 "$(sc_to_native "$AG_SEAT")" run --stream "$(sc_to_native "$AG_STREAM")" --servers "$AG_SERVERS" -- "$(sc_to_native "$SC_BIN")" "${AG_ARGS[@]}" < "$SC_PROMPT_TMP" )
rc=$?
OUTPUT="$(python3 "$AG_SEAT" parse --stream "$(sc_to_native "$AG_STREAM")" --model "$SC_UPSTREAM" --rc "$rc" \
  --allowed "$(python3 "$AG_SEAT" allowed --builtins "$AG_BUILTINS" --servers "$AG_SERVERS")" --root "$(sc_to_native "$SC_WORKDIR")" "${AG_DIRS[@]}" \
  ${SC_SCHEMA_FILE:+--schema "$(sc_to_native "$SC_SCHEMA_FILE")"})"
[ -n "$OUTPUT" ] || OUTPUT='{"type":"result","result":"agy produced no parseable result","structured_output":null,"is_error":true}'
# Fold the agy stream into the shared usage-limit path every Claude-transport launcher uses: a refusal
# that names its reset ends exit 10 and writes the plan's exhausted marker at that reset, so no launch
# repeats it. A child's own status is never this launcher's contract code (agy exits 3 on a quota
# refusal); any other failure is the launcher's child-failure code 1.
sc_watch_step "${SC_PROGRESS:-$AG_RAW}"
sc_finish_usage_limit ""   # the progress file gets the line once, with OUTPUT, below
[ "$SC_USAGE_LIMITED" = 1 ] || rc="$(sc_child_rc "$rc")"
SC_RUN_FINISHED=1
if [ "$rc" -eq 0 ] && printf '%s' "$OUTPUT" | python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("is_error") else 1)'; then rc=1; fi
printf '%s\n' "$OUTPUT"
[ -z "$SC_PROGRESS" ] || printf '%s\n' "$OUTPUT" >> "$SC_PROGRESS"
sc_write_record "$OUTPUT" "$rc"
exit "$rc"
