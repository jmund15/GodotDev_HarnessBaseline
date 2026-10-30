#!/usr/bin/env bash
# Proof that a sidecar's -t list is a real tool LIMIT, not only a pre-approval list.
# `--allowedTools` pre-approves; under bypassPermissions (codex, opencode, deepseek defaults) it
# removes nothing, so a "-t Read,Glob,Grep" child could still Write, Edit and run Bash (observed
# live 2026-09-23). `--tools` is the CLI's built-in availability list, so every launcher must pass
# the built-in subset of -t there while keeping `--allowedTools "$SC_TOOLS"` unchanged.
#   1. sc_builtin_tools (lib/sidecar_common.sh) keeps built-in names, drops mcp__ names (not
#      built-ins; they stay in --allowedTools) and strips a rule specifier: `--tools "Bash(git *)"`
#      splits on the space and silently drops Bash (observed, CLI 2.1.280).
#   2. Each launcher's run_claude passes `--tools <subset>` to the child and keeps
#      `--allowedTools <-t verbatim>`: codex_proxy_sidecar.sh, opencode_sidecar.sh,
#      deepseek_sidecar.sh — the bypassPermissions transports. anthropic_sidecar.sh runs in auto mode,
#      where the classifier bounds a child, so it keeps its full built-in set. run_claude is extracted from the launcher text
#      and run against a stub CLI that records its argv, so the proof reads real child argv.
#   3. MCP servers follow -t the same way. `--tools` never filters MCP tools, and a child otherwise loads
#      every user, project and plugin server (godot's create_scene/save_scene included). Each launcher
#      passes `--strict-mcp-config --mcp-config "$(sc_mcp_config ...)"`: only servers an mcp__ name in -t
#      names, resolved from the parent's own config (local, project .mcp.json, user, installed plugins),
#      and none for a -t without mcp__ names. An unresolvable name loads nothing (fail closed).
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/../scripts"
LIB="$SCRIPTS/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
LAUNCHERS=(codex_proxy_sidecar.sh opencode_sidecar.sh deepseek_sidecar.sh)
fail=0
ran=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

check() {  # $1 label, $2 expected, $3 actual
  ran=$((ran + 1))
  if [ "$3" = "$2" ]; then echo "OK   $1 -> '$3'"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}

builtin_for() {  # $1 -t value
  ( . "$LIB" >/dev/null 2>&1
    if declare -F sc_builtin_tools >/dev/null; then sc_builtin_tools "$1"; else printf '<no sc_builtin_tools>'; fi )
}

check "read-only list is unchanged"          "Read,Glob,Grep" "$(builtin_for "Read,Glob,Grep")"
check "mcp__ name is kept out of --tools"    "Read,Grep"      "$(builtin_for "Read,mcp__x,Grep")"
check "only-mcp list disables all built-ins" ""               "$(builtin_for "mcp__x")"
check "rule specifier reduces to its tool"   "Bash,Edit"      "$(builtin_for "Bash(git *),Edit")"
check "spaces after commas are trimmed"      "Read,Glob"      "$(builtin_for "Read, Glob")"
check "empty list stays empty"               ""               "$(builtin_for "")"

# --- MCP config, against a hermetic fixture config dir (user server, installed plugin) and workdir (.mcp.json)
CFG="$WORK/cfg"; PLUG="$WORK/plug/semantic-search"; mkdir -p "$CFG/plugins" "$PLUG"
PLUG_NATIVE="$(cygpath -m "$PLUG" 2>/dev/null || printf '%s' "$PLUG")"   # installed_plugins.json holds native paths
printf '%s' '{"mcpServers":{"godot":{"command":"node","args":["g.js"]},"ai-worker":{"command":"aiw"}},"projects":{}}' > "$CFG/.claude.json"
printf '%s' '{"mcpServers":{"semantic-search":{"command":"node","args":["${CLAUDE_PLUGIN_ROOT}/bin/server.js"]}}}' > "$PLUG/.mcp.json"
printf '{"version":2,"plugins":{"semantic-search@dreb":[{"scope":"user","installPath":"%s"}]}}' "$PLUG_NATIVE" > "$CFG/plugins/installed_plugins.json"
printf '%s' '{"mcpServers":{"projsrv":{"command":"p"}}}' > "$WORK/.mcp.json"
SS=mcp__plugin_semantic-search_semantic-search__search

servers_of() {  # $1 --mcp-config json -> sorted server names, comma-joined; <bad json> if unparseable
  python3 -c 'import json,sys
try: print(",".join(sorted(json.loads(sys.argv[1])["mcpServers"])))
except Exception: print("<bad json>")' "$1"
}
mcp_for() {  # $1 -t value -> server names the helper resolves
  ( . "$LIB" >/dev/null 2>&1
    if declare -F sc_mcp_config >/dev/null; then
      SC_WORKDIR="$WORK" CLAUDE_CONFIG_DIR="$CFG" servers_of "$(SC_WORKDIR="$WORK" CLAUDE_CONFIG_DIR="$CFG" sc_mcp_config "$1" 2>/dev/null)"
    else printf '<no sc_mcp_config>'; fi )
}

check "no mcp__ name loads no server"        ""                                       "$(mcp_for "Read,Glob,Grep")"
check "plugin tool loads its plugin server"  "plugin:semantic-search:semantic-search" "$(mcp_for "Read,$SS")"
check "user tool loads its server only"      "godot"                                  "$(mcp_for "mcp__godot__get_uid")"
check "server-level name loads the server"   "godot"                                  "$(mcp_for "mcp__godot")"
check "project .mcp.json server resolves"    "projsrv"                                "$(mcp_for "mcp__projsrv__a")"
check "unknown server loads nothing"         ""                                       "$(mcp_for "mcp__nosuch__x")"
check "rule specifier beside mcp name"       "godot"                                  "$(mcp_for "Bash(git *),mcp__godot__get_uid")"
plugin_arg="$( ( . "$LIB" >/dev/null 2>&1; declare -F sc_mcp_config >/dev/null && SC_WORKDIR="$WORK" CLAUDE_CONFIG_DIR="$CFG" sc_mcp_config "$SS" 2>/dev/null ) \
  | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["mcpServers"]["plugin:semantic-search:semantic-search"]["args"][0])
except Exception: print("<missing>")')"
check "plugin root is substituted"           "$PLUG_NATIVE/bin/server.js"             "$plugin_arg"

# --- child argv, per launcher
cat > "$WORK/stub_claude" <<'STUB'
#!/usr/bin/env bash
: > "$STUB_ARGV"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_ARGV"; done
STUB
chmod +x "$WORK/stub_claude"

argv_value() {  # $1 argv file, $2 flag -> the value following the flag, or <absent>
  awk -v f="$2" 'prev == f { print; found = 1; exit } { prev = $0 } END { if (!found) print "<absent>" }' "$1"
}

for launcher in "${LAUNCHERS[@]}"; do
  src="$SCRIPTS/$launcher"
  body="$(awk '/^run_claude\(\) \{/ { on = 1 } on { print } on && /^}/ { exit }' "$src")"
  if [ -z "$body" ]; then
    ran=$((ran + 1)); echo "FAIL $launcher: run_claude not found"; fail=1; continue
  fi
  for tools in "Read,Glob,Grep" "Read,mcp__x" "Read,mcp__godot__get_uid"; do
    argv="$WORK/argv.$launcher.$ran"
    ( . "$LIB" >/dev/null 2>&1
      set +u
      SC_CLAUDE="$WORK/stub_claude" SC_RUN_CWD="$WORK" SC_WORKDIR="$WORK" SC_TOOLS="$tools"
      SC_MODEL=m SC_FORMAT=json SC_PROMPT=p SC_TIMEOUT="" SC_DISALLOWED="" SC_MAX_TURNS=""
      export CLAUDE_CONFIG_DIR="$CFG"
      SC_RESUME_SID="" SC_SCRUB=() SC_APPEND_ARGS=() EXTRA_ARGS=() VERBOSE_ARGS=() EFFORT_ARGS=() SC_ADD_DIRS=()
      export STUB_ARGV="$argv"
      eval "$body"
      run_claude </dev/null >/dev/null 2>&1 )
    if [ ! -s "$argv" ]; then
      ran=$((ran + 1)); echo "FAIL $launcher -t $tools: stub CLI never ran"; fail=1; continue
    fi
    want_tools="${tools%%,mcp__*}"
    check "$launcher -t $tools: --tools"        "$want_tools" "$(argv_value "$argv" --tools)"
    check "$launcher -t $tools: --allowedTools" "$tools"      "$(argv_value "$argv" --allowedTools)"
    check "$launcher -t $tools: --strict-mcp-config" "yes" "$(grep -qx -- --strict-mcp-config "$argv" && echo yes || echo no)"
    case "$tools" in *mcp__godot*) want_mcp="godot" ;; *) want_mcp="" ;; esac
    check "$launcher -t $tools: --mcp-config"  "$want_mcp"    "$(servers_of "$(argv_value "$argv" --mcp-config)")"
  done
done

# --- -G <kind>: the kind row sets --tools on every Claude-client launcher; flags that contradict it refuse
KINDS="scout lens reviewer author judge"
builtins_of_kind() {  # $1 kind -> the row's built-ins, comma-joined, read from the table
  python3 -c 'import json,sys
t=json.load(open(sys.argv[1]))["kinds"][sys.argv[2]]
print(",".join(t["builtins"]))' "$SCRIPTS/../guards/tool_profiles.json" "$1" | tr -d '\r'
}
for launcher in "${LAUNCHERS[@]}"; do
  body="$(awk '/^run_claude\(\) \{/ { on = 1 } on { print } on && /^}/ { exit }' "$SCRIPTS/$launcher")"
  for kind in $KINDS; do
    argv="$WORK/argv.kind.$launcher.$kind"
    ( . "$LIB" >/dev/null 2>&1
      set +u
      SC_CLAUDE="$WORK/stub_claude" SC_RUN_CWD="$WORK" SC_WORKDIR="$WORK"
      SC_TIMEOUT="" SC_DISALLOWED="" SC_MAX_TURNS=""
      SC_RESUME_SID="" SC_SCRUB=() SC_APPEND_ARGS=() EXTRA_ARGS=() VERBOSE_ARGS=() EFFORT_ARGS=() SC_ADD_DIRS=()
      export STUB_ARGV="$argv" CLAUDE_CONFIG_DIR="$CFG"
      sc_parse_flags -m m -G "$kind" -f "$WORK/.mcp.json" >/dev/null 2>&1
      SC_FORMAT=json SC_PROMPT=p
      sc_validate_common >/dev/null 2>&1
      eval "$body"
      run_claude </dev/null >/dev/null 2>&1 )
    if [ ! -s "$argv" ]; then
      ran=$((ran + 1)); echo "FAIL $launcher -G $kind: stub CLI never ran"; fail=1; continue
    fi
    check "$launcher -G $kind: --tools equals the kind's built-ins" "$(builtins_of_kind "$kind")" "$(argv_value "$argv" --tools)"
  done
done

launch_text() {  # launcher flags -> combined output and exit code of parse + validate, in a subshell
  ( . "$LIB" >/dev/null 2>&1
    export CLAUDE_CONFIG_DIR="$CFG"
    SC_WORKDIR="$WORK"
    ( sc_parse_flags "$@" && SC_FORMAT=json && sc_validate_common ) 2>&1
    echo "rc=$?" ) | tr -d '\r'
}
refuses() {  # $1 label, $2 needle, rest: flags
  local label="$1" needle="$2" out; shift 2
  out="$(launch_text "$@")"
  ran=$((ran + 1))
  case "$out" in
    *"$needle"*"rc=2") echo "OK   $label" ;;
    *) echo "FAIL $label: wanted exit 2 naming '$needle', got '$out'"; fail=1 ;;
  esac
}
accepts() {  # $1 label, rest: flags
  local label="$1" out; shift
  out="$(launch_text "$@")"
  ran=$((ran + 1))
  case "$out" in
    *"rc=0") echo "OK   $label" ;;
    *) echo "FAIL $label: wanted exit 0, got '$out'"; fail=1 ;;
  esac
}
refuses "an omitted -G is refused, naming the public kinds" "scout, lens, reviewer, author, judge" -m m
refuses "-G survey is refused, naming the public kinds" "scout, lens, reviewer, author, judge" -m m -G survey
refuses "-G any is refused, naming the public kinds" "scout, lens, reviewer, author, judge" -m m -G any
refuses "-D on a kind refuses, naming -G <kind>" "disclosure follows the kind: pass -G <kind>" -m m -G scout -D bare
refuses "-D that matches the kind's own disclosure still refuses" "disclosure follows the kind: pass -G <kind>" -m m -G reviewer -D full
accepts "-D rides -G arm" -m m -G arm -D bare -t Read,Glob,Grep
refuses "-G arm without -D refuses" "-D" -m m -G arm -t Read,Glob,Grep
for kind in $KINDS; do
  refuses "an unsigned -t on $kind names -G <kind> and a signed delta" "-t +Bash" -m m -G "$kind" -t Read,Glob,Grep
done
accepts "a signed +Bash delta on a scout resolves" -m m -G scout -t +Bash
accepts "a signed removal resolves" -m m -G lens -t -mcp:ai-worker
refuses "-B refuses off arm" "arm" -m m -G scout -B Read
accepts "-G bench-review resolves" -m m -G bench-review

expected=$((14 + ${#LAUNCHERS[@]} * 3 * 4 + ${#LAUNCHERS[@]} * 5 + 16))
[ "$ran" -eq "$expected" ] || { echo "FAIL expected $expected cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_tools_limit" || echo "FAIL test_sidecar_tools_limit"
exit "$fail"
