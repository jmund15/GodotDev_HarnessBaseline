#!/usr/bin/env bash
# Proof of the sidecar tool profile: the kind's row in guards/tool_profiles.json decides the
# child's built-ins and its MCP servers, on all four launchers, and the `-R` record states it.
#
#   A. SC_RESOLVED_* / SC_TOOL_PROFILE, resolved by sc_resolve_tools against a hermetic config
#      dir: the kind vectors, the signed delta, and each fail-closed refusal (an unsigned -t, a
#      retired shape name, an omitted -G).
#   B. each launcher's own run_claude, run against a stub CLI that records its argv — `--tools`,
#      `--strict-mcp-config` and `--mcp-config` all come from the resolved profile. The extracted
#      launcher TEXT is what runs, so this reads real child argv.
#   C. TOOL_PROFILE_LIVE=1 spawns a REAL child per launcher (skipped by default: a standing proof
#      must not spend money or depend on the network). The asserted inventory is the child's own
#      `system/init` event — its reported `mcp_servers` and built-in tool list — never the argv
#      we passed it. A blocked transport is reported, not counted as a pass.
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/../scripts"
LIB="$SCRIPTS/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
LAUNCHERS=(anthropic_sidecar.sh codex_proxy_sidecar.sh deepseek_sidecar.sh opencode_sidecar.sh)
fail=0
ran=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

check() {  # $1 label, $2 expected, $3 actual
  ran=$((ran + 1))
  if [ "$3" = "$2" ]; then echo "OK   $1 -> '$3'"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}

# --------------------------------------------------------------- hermetic config fixture
CFG="$WORK/cfg"; PLUG="$WORK/plug/semantic-search"; mkdir -p "$CFG/plugins" "$PLUG"
PLUG_NATIVE="$(cygpath -m "$PLUG" 2>/dev/null || printf '%s' "$PLUG")"
printf '%s' '{"mcpServers":{"ai-worker":{"command":"aiw"},"godot":{"command":"g"}},"projects":{}}' > "$CFG/.claude.json"
printf '%s' '{"mcpServers":{"semantic-search":{"command":"node","args":["${CLAUDE_PLUGIN_ROOT}/s.js"]}}}' > "$PLUG/.mcp.json"
printf '{"version":2,"plugins":{"semantic-search@dreb":[{"scope":"user","installPath":"%s"}]}}' "$PLUG_NATIVE" > "$CFG/plugins/installed_plugins.json"
SS_KEY="plugin:semantic-search:semantic-search"
# The spec carries the normalized registration (what --allowedTools and sc_mcp_config both take).
SS_NORM="plugin_semantic-search_semantic-search"

# One resolution in a subshell; prints "<builtins>|<spec>|<shape> <mode> <servers>".
# sc_resolve_tools exits 2 inside its own subshell, so the subshell's stderr IS the refusal
# message: 2>&1 on the whole subshell folds it into the captured text either way.
resolved() {
  ( . "$LIB" >/dev/null 2>&1
    SC_SHAPE="$1" SC_TOOLS="$2" SC_BASE_BUILTINS="${3:-}" SC_WORKDIR="$WORK" CLAUDE_CONFIG_DIR="$CFG"
    sc_resolve_tools || exit 2
    printf '%s|%s|%s' "$SC_RESOLVED_BUILTINS" "$SC_RESOLVED_TOOL_SPEC" \
      "$(printf '%s' "$SC_TOOL_PROFILE" | python3 -c 'import sys, json
p = json.load(sys.stdin)
sys.stdout.write("%s %s %s %s" % (p["kind"], p["overrideMode"], p["enforcement"],
                                  ",".join(p["mcpServers"])))')" ) 2>&1 | tr -d '\r'
}

READ_SPEC="Read,Glob,Grep,mcp__${SS_NORM},mcp__ai-worker"
for kind in scout lens reviewer judge; do
  check "$kind resolves to the table's row"  "Read,Glob,Grep|$READ_SPEC|$kind shape-default strict semantic-search,ai-worker" "$(resolved $kind '')"
done
check "author resolves to the write set"    "Read,Glob,Grep,Edit,Write,Bash|Read,Glob,Grep,Edit,Write,Bash,mcp__${SS_NORM},mcp__ai-worker|author shape-default strict semantic-search,ai-worker" "$(resolved author '')"
check "arm takes its task row and one server" "Read,Glob,Grep,Edit,Write,Bash,TodoWrite|Read,Glob,Grep,Edit,Write,Bash,TodoWrite,mcp__${SS_NORM}|arm task-base strict semantic-search" "$(resolved arm 'Read,Glob,Grep,Edit,Write,Bash,TodoWrite,mcp__'"${SS_KEY}"'__search')"
check "a signed csv is a delta"             "Read,Glob,Grep|Read,Glob,Grep,mcp__${SS_NORM},mcp__ai-worker,mcp__godot|scout delta strict semantic-search,ai-worker,godot" "$(resolved scout '+mcp:godot')"
check "a removal drops exactly one server"  "Read,Glob,Grep|Read,Glob,Grep,mcp__${SS_NORM}|scout delta strict semantic-search" "$(resolved scout '-mcp:ai-worker')"
# The arm row carries no built-ins, so a delta on an arm needs its task row beside it -- and the
# two grammars cannot share one `-t` CSV. SC_BASE_BUILTINS is that second value; a delta that
# cannot be reached from the command line is a profile no A/B cell can express.
check "an arm delta takes its task row from SC_BASE_BUILTINS" \
      "Read,Glob,Grep|Read,Glob,Grep,mcp__${SS_NORM},mcp__ai-worker|arm delta strict semantic-search,ai-worker" \
      "$(resolved arm '+mcp:ai-worker' 'Read,Glob,Grep')"
check "an arm with a base and no delta is the arm row" \
      "Read,Glob,Grep|Read,Glob,Grep,mcp__${SS_NORM}|arm shape-default strict semantic-search" \
      "$(resolved arm '' 'Read,Glob,Grep')"
refusal_ungrantable() {  # $1 -t -> the CLI cannot grant Workflow/Agent to a headless child
  local out; out="$(resolved judge "$1")"
  ran=$((ran + 1))
  case "$out" in
    *cannot\ grant*) echo "OK   refusing an ungrantable built-in '$1' -> $out" ;;
    *) echo "FAIL refusing '$1': wanted 'cannot grant', got '$out'"; fail=1 ;;
  esac
}

# fail-closed: each refusal names the input, and nothing is dispatched
refusal() {  # $1 shape, $2 -t, $3 needle
  local out; out="$(resolved "$1" "$2")"
  ran=$((ran + 1))
  case "$out" in
    *"$3"*) echo "OK   refusing $1 '$2' -> $out" ;;
    *) echo "FAIL refusing $1 '$2': wanted '$3', got '$out'"; fail=1 ;;
  esac
}
refusal_ungrantable '+Workflow'
refusal_ungrantable '+Agent'
# 2026-09-24: the score daemon's driver makes one Workflow(score_cell.js) call. Its shape row is the one
# place a reserved built-in lives; the launcher's own shape check and the guard text must accept it.
check "bench-score resolves Workflow from its own row, no MCP" \
      "Read,Glob,Grep,Workflow|Read,Glob,Grep,Workflow|bench-score shape-default strict " "$(resolved bench-score '')"
refusal bench-score '+Agent' 'cannot grant'
check "the launcher's shape check accepts bench-score" "rc=0" \
      "$( ( . "$LIB" >/dev/null 2>&1; SC_FORMAT=json; SC_SHAPE=bench-score; ( sc_validate_common >/dev/null 2>&1 ); echo "rc=$?" ) 2>/dev/null | tail -1 )"
check "the launcher still refuses an unknown kind" "rc=2" \
      "$( ( . "$LIB" >/dev/null 2>&1; SC_FORMAT=json; SC_SHAPE=nosuch; ( sc_validate_common >/dev/null 2>&1 ); echo "rc=$?" ) 2>/dev/null | tail -1 )"
check "the launcher refuses a retired shape name" "rc=2" \
      "$( ( . "$LIB" >/dev/null 2>&1; SC_FORMAT=json; SC_SHAPE=survey; ( sc_validate_common >/dev/null 2>&1 ); echo "rc=$?" ) 2>/dev/null | tail -1 )"
check "the launcher refuses the engine-only verify kind" "rc=2" \
      "$( ( . "$LIB" >/dev/null 2>&1; SC_FORMAT=json; SC_SHAPE=verify; ( sc_validate_common >/dev/null 2>&1 ); echo "rc=$?" ) 2>/dev/null | tail -1 )"
check "the launcher accepts bench-review (the offkey review seat)" "rc=0" \
      "$( ( . "$LIB" >/dev/null 2>&1; SC_FORMAT=json; SC_SHAPE=bench-review; ( sc_validate_common >/dev/null 2>&1 ); echo "rc=$?" ) 2>/dev/null | tail -1 )"
check "bench-score guard text assembles" "0" \
      "$(python3 "$HERE/../tools/guard_text.py" bench-score detailed >/dev/null 2>&1; echo $?)"
refusal scout '+mcp:nosuch'             'not registered'
refusal scout '-mcp:godot'              'does not carry'
refusal scout '-Read,-Glob,-Grep'       'could not read its brief'
refusal scout 'Read,+Bash'              'mixed signed and unsigned'
refusal scout '+Edit'                   'use kind author'
refusal survey ''                       'scout, lens, reviewer, author, judge'
refusal any ''                          'scout, lens, reviewer, author, judge'
refusal review ''                       'scout, lens, reviewer, author, judge'
# An unsigned -t is the retired absolute list: it refuses on every kind but arm, naming -G <kind> and a signed delta.
for kind in scout lens reviewer author judge; do
  refusal $kind 'Read,Glob,Grep'        '-G <kind>'
  refusal $kind 'Read,Glob,Grep'        '+Bash'
done
refusal ''    ''                        'scout, lens, reviewer, author, judge'
# -B base built-ins are the arm row's task list; they are legal only with -G arm.
b_out="$(resolved scout '' 'Read')"
check "-B refuses on a public kind, naming -G arm" "yes" "$(case "$b_out" in *"task row of -G arm"*) echo yes ;; *) echo no ;; esac)"

# The launcher reads no brief: sc_resolve_tools takes the kind row and the signed delta, never a prompt
# file, a positional prompt or a -C file. A brief that names `git diff` or a slash command leaves the
# profile as the kind carries it; a job that needs Bash writes `-t +Bash`.
printf 'Enumerate with `git diff origin/main...HEAD`.\n' > "$WORK/git_brief.md"
printf 'Run /pr_ready over the branch.\n' > "$WORK/pr_ready_brief.md"
unbriefed() {  # $1 kind, $2 -t, $3 -f file -> builtins, or the refusal
  ( . "$LIB" >/dev/null 2>&1
    SC_SHAPE="$1" SC_TOOLS="$2" SC_WORKDIR="$WORK" CLAUDE_CONFIG_DIR="$CFG"
    SC_PROMPT_FILE="$3" SC_PROMPT='' SC_CONTEXT_FILES=("$3")
    sc_resolve_tools >/dev/null 2>&1 && printf '%s' "$SC_RESOLVED_BUILTINS" ) 2>&1 | tr -d '\r'
}
check "a brief naming git diff does not widen a reviewer" "Read,Glob,Grep" "$(unbriefed reviewer '' "$WORK/git_brief.md")"
check "a brief telling the child to run /pr_ready resolves: the launcher scans no brief" "Read,Glob,Grep,Edit,Write,Bash" \
      "$(unbriefed author '' "$WORK/pr_ready_brief.md")"
check "+Bash is the way to ask for a shell" "Read,Glob,Grep,Bash" "$(unbriefed reviewer '+Bash' "$WORK/git_brief.md")"
check "sc_resolve_tools no longer passes --widen or --brief" "0" \
      "$(sed -n '/^sc_resolve_tools() {/,/^}/p' "$LIB" | grep -c -e '--widen' -e '--brief')"

# The isolated disclosure tiers pass the registry-resolved model into guard_text.py. Inspect
# the actual append files after the shared launcher path, without starting a paid child.
appended_notes() {  # $1 disclosure, $2 kind, $3 guard name the rails header carries
  ( SC_TRANSPORT=codex
    . "$LIB" >/dev/null 2>&1
    SC_MODEL=luna SC_SHAPE="$2" SC_DISCLOSURE="$1" SC_WORKDIR="$(cd "$HERE/../.." && pwd)"
    sc_resolve_model && sc_build_disclosure
    count=0
    for ((i=0; i<${#SC_APPEND_ARGS[@]}; i+=2)); do
      file="${SC_APPEND_ARGS[i+1]}"
      if [ -f "$file" ] && python3 -c 'import pathlib,sys
p=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
sys.exit(not ("[delegate rails — shape %r" % sys.argv[2] in p
              and "Driver notes (gpt-6-luna)" in p
              and "Work unattended: never stop to ask." in p))' "$file" "$3"; then
        count=$((count + 1))
      fi
    done
    printf '%s:%s' "$SC_MODEL" "$count"
  )
}
check "bare: resolved luna's notes occur in exactly one append file" "gpt-6-luna:1" "$(appended_notes bare scout survey)"
check "pointer: resolved luna's notes occur in exactly one append file" "gpt-6-luna:1" "$(appended_notes pointer lens survey)"
check "a judge kind appends the common rails and luna notes" "gpt-6-luna:1" "$(appended_notes bare judge any)"
check "an omitted kind assembles no guards" "refused" \
      "$( ( SC_TRANSPORT=codex; . "$LIB" >/dev/null 2>&1; SC_MODEL=luna SC_SHAPE='' SC_DISCLOSURE=bare SC_WORKDIR="$(cd "$HERE/../.." && pwd)"
           ( sc_resolve_model && sc_build_disclosure ) >/dev/null 2>&1 && echo built || echo refused ) )"

# Every tier appends the autonomy rail, and it tells the child that nothing wakes it after its turn.
autonomy_says() {  # $1 disclosure, $2 phrase (default: a background job dies with the turn) -> yes when an append file holds it
  ( . "$LIB" >/dev/null 2>&1
    SC_SHAPE=author SC_DISCLOSURE="$1" SC_WORKDIR="$(cd "$HERE/../.." && pwd)"
    sc_build_disclosure >/dev/null 2>&1
    for ((i=0; i<${#SC_APPEND_ARGS[@]}; i+=2)); do
      grep -qF -- "${2:-dies with your turn}" "${SC_APPEND_ARGS[i+1]}" 2>/dev/null && { echo yes; exit 0; }
    done
    echo no )
}
check "full: the autonomy rail says a background job dies with the child's turn" "yes" "$(autonomy_says full)"
check "full: the autonomy rail overrides the background default without citing a section the child may not load" "yes" "$(autonomy_says full 'overrides any run_in_background default your instructions carry')"
check "full: the autonomy rail sends a job past the foreground cap detached, then waited on in foreground until-loop calls" "yes" "$(autonomy_says full 'starts detached and is waited on in foreground until-loop calls (guards/any.md)')"
check "full: the autonomy rail states the foreground action before the reason for it" "yes" "$(autonomy_says full 'in the foreground and wait for it. This overrides')"

# --------------------------------------------------------------- B. each launcher's own argv
cat > "$WORK/stub_claude" <<'STUB'
#!/usr/bin/env bash
: > "$STUB_ARGV"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_ARGV"; done
[ -n "${STUB_ENV:-}" ] && printf '%s\n' "${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-<unset>}" > "$STUB_ENV"
[ -n "${STUB_STDIN:-}" ] && cat > "$STUB_STDIN"
STUB
chmod +x "$WORK/stub_claude"

argv_value() {  # $1 argv file, $2 flag -> the value following the flag, or <absent>
  awk -v f="$2" 'prev == f { print; found = 1; exit } { prev = $0 } END { if (!found) print "<absent>" }' "$1"
}

servers_of() {  # $1 --mcp-config json -> sorted registered keys, comma-joined
  python3 -c 'import json,sys
try: print(",".join(sorted(json.loads(sys.argv[1])["mcpServers"])))
except Exception: print("<bad json>")' "$1"
}

for launcher in "${LAUNCHERS[@]}"; do
  src="$SCRIPTS/$launcher"
  body="$(awk '/^run_claude\(\) \{/ { on = 1 } on { print } on && /^}/ { exit }' "$src")"
  if [ -z "$body" ]; then
    ran=$((ran + 1)); echo "FAIL $launcher: run_claude not found"; fail=1; continue
  fi
  for shape_tools in "scout:" "author:+mcp:godot"; do
    shape="${shape_tools%%:*}"; tools="${shape_tools#*:}"
    want="$(resolved "$shape" "$tools")"
    want_builtins="${want%%|*}"; want="${want#*|}"
    want_spec="${want%%|*}"; want="${want#*|}"
    case "$shape$tools" in
      scout) want_servers="ai-worker,$SS_KEY" ;;
      author+mcp:godot) want_servers="ai-worker,godot,$SS_KEY" ;;
    esac
    argv="$WORK/argv.$launcher.$shape${tools:+x}"
    ( . "$LIB" >/dev/null 2>&1
      set +u
      SC_CLAUDE="$WORK/stub_claude" SC_RUN_CWD="$WORK" SC_WORKDIR="$WORK"
      SC_MODEL=m SC_FORMAT=json SC_PROMPT=p SC_TIMEOUT="" SC_DISALLOWED="" SC_MAX_TURNS=""
      SC_RESUME_SID="" SC_SCRUB=() SC_APPEND_ARGS=() EXTRA_ARGS=() VERBOSE_ARGS=() EFFORT_ARGS=() SC_ADD_DIRS=()
      SC_SHAPE="$shape" SC_TOOLS="$tools" CLAUDE_CONFIG_DIR="$CFG"
      export STUB_ARGV="$argv" CLAUDE_CONFIG_DIR="$CFG"
      sc_resolve_tools 2>/dev/null
      [ "${tools:+x}" = x ] || :
      eval "$body"
      run_claude </dev/null >/dev/null 2>&1 )
    if [ ! -s "$argv" ]; then
      ran=$((ran + 1)); echo "FAIL $launcher $shape: stub CLI never ran"; fail=1; continue
    fi
    check "$launcher $shape$tools: --tools"            "$want_builtins" "$(argv_value "$argv" --tools)"
    check "$launcher $shape$tools: --allowedTools"     "$want_spec"     "$(argv_value "$argv" --allowedTools)"
    check "$launcher $shape$tools: --strict-mcp-config" "yes" "$(grep -qx -- --strict-mcp-config "$argv" && echo yes || echo no)"
    check "$launcher $shape$tools: --mcp-config names the resolved keys" "$want_servers" \
          "$(servers_of "$(argv_value "$argv" --mcp-config)")"
  done
done

# 2026-10-01: a child left at the CLI's 32K default output cap lost its paid reasoning on every long reply.
# Every launcher hands the child the cap it resolved (SC_MAX_OUTPUT, from the registry's max-output).
for launcher in "${LAUNCHERS[@]}"; do
  body="$(awk '/^run_claude\(\) \{/ { on = 1 } on { print } on && /^}/ { exit }' "$SCRIPTS/$launcher")"
  argv="$WORK/argv.cap.$launcher"; envf="$WORK/env.cap.$launcher"
  ( . "$LIB" >/dev/null 2>&1
    set +u
    SC_CLAUDE="$WORK/stub_claude" SC_RUN_CWD="$WORK" SC_WORKDIR="$WORK"
    SC_MODEL=m SC_FORMAT=json SC_PROMPT=p SC_TIMEOUT="" SC_DISALLOWED="" SC_MAX_TURNS=""
    SC_RESUME_SID="" SC_SCRUB=() SC_APPEND_ARGS=() EXTRA_ARGS=() VERBOSE_ARGS=() EFFORT_ARGS=() SC_ADD_DIRS=()
    SC_SHAPE=scout SC_TOOLS="" CLAUDE_CONFIG_DIR="$CFG" SC_MAX_OUTPUT=123456
    export STUB_ARGV="$argv" STUB_ENV="$envf" CLAUDE_CONFIG_DIR="$CFG"
    sc_resolve_tools 2>/dev/null
    eval "$body"
    run_claude </dev/null >/dev/null 2>&1 )
  check "$launcher: the child gets the resolved output cap" "123456" "$(cat "$envf" 2>/dev/null || echo '<never ran>')"
done

# sc_max_output resolves the cap: the env override when it is a number, else the registry's max-output
# (--max under -M), bounded by a quarter of a window the launcher declares in place of the registry's.
cat > "$WORK/stub_registry.py" <<'PY'
import os, sys
with open(os.environ["STUB_REG_ARGV"], "w") as fh:
    fh.write(" ".join(sys.argv[1:]))
sys.stdout.write(os.environ.get("STUB_CAP", ""))
PY
max_output() {  # $1 SIDECAR_MAX_OUTPUT_TOKENS, $2 registry stub output, $3 SC_LONG_CONTEXT, $4 declared window
  ( . "$LIB" >/dev/null 2>&1
    export SC_REGISTRY_CLI="$WORK/stub_registry.py" SC_MODEL=m STUB_REG_ARGV="$WORK/reg.argv" STUB_CAP="$2"
    SIDECAR_MAX_OUTPUT_TOKENS="$1" SC_LONG_CONTEXT="$3"
    sc_max_output ${4:+"$4"} 2>/dev/null || printf '%s' '<CRASH>' ) | tr -d '\r'
}
check "SIDECAR_MAX_OUTPUT_TOKENS overrides the registry cap"        "777"  "$(max_output 777 5000 '' '')"
check "a non-numeric SIDECAR_MAX_OUTPUT_TOKENS falls back to the registry cap" "5000" "$(max_output 12k 5000 '' '')"
check "a zero SIDECAR_MAX_OUTPUT_TOKENS falls back to the registry cap"        "5000" "$(max_output 0 5000 '' '')"
check "a zero-padded zero SIDECAR_MAX_OUTPUT_TOKENS falls back to the registry cap" "5000" "$(max_output 00 5000 '' '')"
check "a non-numeric registry answer prints nothing"                ""     "$(max_output '' abc '' '')"
: > "$WORK/reg.argv"; max_output '' 5000 1 '' >/dev/null
check "-M asks the registry for the max-window cap"                 "max-output m --max" "$(tr -d '\r' < "$WORK/reg.argv")"
check "a declared window bounds the registry cap at a quarter of it" "2000" "$(max_output '' 5000 '' 8000)"
check "a declared window above four caps leaves the cap alone"      "5000" "$(max_output '' 5000 '' 400000)"

# Each launcher's top-level resolution, run as written: an inherited SC_MAX_OUTPUT is not an override,
# deepseek's MAX_CONTEXT_TOKENS bounds its cap, and codex_proxy keeps its own 16000 default (its cap is
# also its output reserve; reference/sidecar_runs.md).
launcher_cap() {  # $1 launcher, $2 MAX_CONTEXT_TOKENS
  local line
  line="$(grep -m1 '^SC_MAX_OUTPUT=' "$SCRIPTS/$1")"
  ( . "$LIB" >/dev/null 2>&1
    export SC_REGISTRY_CLI="$WORK/stub_registry.py" SC_MODEL=m STUB_REG_ARGV="$WORK/reg.argv" STUB_CAP=5000
    unset SIDECAR_MAX_OUTPUT_TOKENS SC_LONG_CONTEXT
    SC_MAX_OUTPUT=999 MAX_CONTEXT_TOKENS="$2"
    eval "$line"
    printf '%s' "$SC_MAX_OUTPUT" ) 2>/dev/null | tr -d '\r'
}
check "anthropic_sidecar.sh resolves its cap from the registry, not an inherited SC_MAX_OUTPUT" "5000" "$(launcher_cap anthropic_sidecar.sh '')"
check "opencode_sidecar.sh resolves its cap from the registry, not an inherited SC_MAX_OUTPUT"  "5000" "$(launcher_cap opencode_sidecar.sh '')"
check "deepseek_sidecar.sh resolves its cap from the registry, not an inherited SC_MAX_OUTPUT"  "5000" "$(launcher_cap deepseek_sidecar.sh '')"
check "deepseek_sidecar.sh bounds its cap at a quarter of MAX_CONTEXT_TOKENS"                  "2000" "$(launcher_cap deepseek_sidecar.sh 8000)"
check "codex_proxy_sidecar.sh keeps its 16000 default"                                          "16000" "$(launcher_cap codex_proxy_sidecar.sh '')"

# 2026-09-24: a 31.5 KB score-driver brief passed as `-p "<prompt>"` failed with "Argument list too long"
# (Windows caps a command line near 32 KB). Every launcher feeds the prompt on stdin, as codex/opencode did.
BIG="$(head -c 40000 /dev/zero | tr '\0' x)"
for launcher in "${LAUNCHERS[@]}"; do
  body="$(awk '/^run_claude\(\) \{/ { on = 1 } on { print } on && /^}/ { exit }' "$SCRIPTS/$launcher")"
  argv="$WORK/argv.big.$launcher"; stdin="$WORK/stdin.big.$launcher"
  ( . "$LIB" >/dev/null 2>&1
    set +u
    SC_CLAUDE="$WORK/stub_claude" SC_RUN_CWD="$WORK" SC_WORKDIR="$WORK"
    SC_MODEL=m SC_FORMAT=json SC_PROMPT="$BIG" SC_TIMEOUT="" SC_DISALLOWED="" SC_MAX_TURNS=""
    SC_RESUME_SID="" SC_SCRUB=() SC_APPEND_ARGS=() EXTRA_ARGS=() VERBOSE_ARGS=() EFFORT_ARGS=() SC_ADD_DIRS=()
    SC_SHAPE=scout SC_TOOLS="" CLAUDE_CONFIG_DIR="$CFG"
    export STUB_ARGV="$argv" STUB_STDIN="$stdin" CLAUDE_CONFIG_DIR="$CFG"
    sc_resolve_tools 2>/dev/null
    eval "$body"
    run_claude </dev/null >/dev/null 2>&1 )
  check "$launcher: a 40 KB prompt rides stdin" "40000" "$(tr -d '\n' < "$stdin" 2>/dev/null | wc -c | tr -d ' ')"
  check "$launcher: the prompt is not an argv element" "no" "$(grep -q '^xxxxxxxxxx' "$argv" 2>/dev/null && echo yes || echo no)"
done

# --------------------------------------------------------------- D. the flag and the fan-out
# `-B` is a real getopts flag, not only an SC_ variable a proof sets by hand: an arm A/B job has to
# reach it from the command line, and the fan-out has to forward it.
flag_base="$( . "$LIB" >/dev/null 2>&1; sc_parse_flags -B Read,Glob,Grep -m m -G arm -D pointer >/dev/null 2>&1
              printf '%s' "$SC_BASE_BUILTINS" )"
check "-B carries the arm task row through flag parsing" "Read,Glob,Grep" "$flag_base"
printf 'Reply with the single word READY.\n' > "$WORK/prompt.txt"
JOBS="$WORK/jobs.json"
# The fan-out is native Python: an MSYS `/tmp/...` path reaches it as a missing file, so the paths
# IT opens are passed native.
NATIVE_WORK="$(cygpath -m "$WORK" 2>/dev/null || printf '%s' "$WORK")"
cat > "$JOBS" <<JSON
[{"label":"ab-base","alias":"haiku","promptFile":"$NATIVE_WORK/prompt.txt","transport":"anthropic","kind":"arm","armDisclosure":"bare","baseBuiltins":"Read,Glob,Grep"},
 {"label":"sc","alias":"haiku","promptFile":"$NATIVE_WORK/prompt.txt","transport":"anthropic","kind":"scout"},
 {"label":"ab-plus","alias":"haiku","promptFile":"$NATIVE_WORK/prompt.txt","transport":"anthropic","kind":"arm","armDisclosure":"pointer","baseBuiltins":"Read,Glob,Grep","tools":{"add":["mcp:ai-worker"]}}]
JSON
dry="$(python3 "$SCRIPTS/../tools/sidecar_fanout.py" "$JOBS" --dry-run \
       --out-dir "$NATIVE_WORK/fanout-dry" 2>&1)"
check "the fan-out forwards a job's tools delta as a signed -t" "yes" \
      "$(case "$dry" in *'-t +mcp:ai-worker'*) echo yes ;; *) echo no ;; esac)"
check "the fan-out forwards a job's arm task row as -B" "yes" \
      "$(case "$dry" in *'-B Read,Glob,Grep'*) echo yes ;; *) echo no ;; esac)"
check "the fan-out passes -G <kind> directly, and -D only for arm from armDisclosure" "arm-bare arm-pointer no-scout-D scout" \
      "$(case "$dry" in *'-G arm -D bare'*) printf 'arm-bare ' ;; esac)$(case "$dry" in *'-G arm -D pointer'*) printf 'arm-pointer ' ;; esac)$(case "$dry" in *'-G scout -D'*) printf 'scout-D ' ;; *) printf 'no-scout-D ' ;; esac)$(case "$dry" in *'-G scout'*) printf 'scout' ;; esac)"
check "the dry run dispatches nothing" "3 job(s), 4 at a time. Nothing dispatched." \
      "$(printf '%s' "$dry" | tail -1)"

# --------------------------------------------------------------- C. real children (opt-in)
if [ "${TOOL_PROFILE_LIVE:-}" = "1" ]; then
  printf 'Reply with the single word READY.\n' > "$WORK/prompt.txt"
  # $1 tag, $2 launcher, $3 model, $4 expected servers, $5 expected built-ins, $6 expected record
  # servers, rest: the launcher's own args.
  live() {
    local tag="$1" launcher="$2" model="$3" exp_servers="$4" exp_builtins="$5" exp_record="$6"; shift 6
    local tagdir="$WORK/live"; mkdir -p "$tagdir"
    rm -f "$tagdir/$tag.stream" "$tagdir/$tag.rec.json"
    timeout 300 bash "$SCRIPTS/$launcher" -m "$model" -e low -d "$tagdir" \
      -P "$tagdir/$tag.stream" -R "$tagdir/$tag.rec.json" -f "$WORK/prompt.txt" -N -T 240 -Z 180 "$@" \
      >/dev/null 2>"$tagdir/$tag.err"
    local rc=$?
    if [ ! -s "$tagdir/$tag.stream" ]; then
      ran=$((ran + 1))
      echo "FAIL $tag: no stream (rc=$rc) -- $(tail -1 "$tagdir/$tag.err")"; fail=1; return
    fi
    local seen; seen="$(python3 - "$tagdir/$tag.stream" <<'PY'
import json, sys
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try: d = json.loads(line)
    except Exception: continue
    if d.get("type") == "system" and d.get("subtype") == "init":
        print(",".join(sorted(s["name"] for s in (d.get("mcp_servers") or []))))
        print(",".join(sorted(t for t in (d.get("tools") or []) if not t.startswith("mcp__"))))
        break
else:
    print("<no init event>")
PY
)"
    local servers builtins
    servers="$(printf '%s' "$seen" | sed -n 1p)"; builtins="$(printf '%s' "$seen" | sed -n 2p)"
    check "$tag: the child reports exactly the resolved servers" "$exp_servers" "$servers"
    check "$tag: the child reports the resolved built-ins" "$exp_builtins" "$builtins"
    check "$tag: the record states the same profile" "$exp_record" \
      "$(python3 -c 'import json,sys
p=(json.load(open(sys.argv[1])) or {}).get("toolProfile") or {}
print(",".join(p.get("mcpServers") or []) or "<absent>")' "$tagdir/$tag.rec.json")"
  }

  # The live registrations on this machine, which the probes read with no fixture override.
  SS_REG="plugin:semantic-search:semantic-search"
  live survey-anthropic anthropic_sidecar.sh haiku \
       "ai-worker,$SS_REG" "Glob,Grep,Read" "semantic-search,ai-worker" -G scout
  live survey-codex codex_proxy_sidecar.sh luna \
       "ai-worker,$SS_REG" "Glob,Grep,Read" "semantic-search,ai-worker" -G scout
  live survey-deepseek deepseek_sidecar.sh flash \
       "ai-worker,$SS_REG" "Glob,Grep,Read" "semantic-search,ai-worker" -G scout
  live arm-anthropic anthropic_sidecar.sh haiku \
       "$SS_REG" "Glob,Grep,Read" "semantic-search" \
       -G arm -D bare -t "Read,Glob,Grep,mcp__${SS_REG}__search"
  live add-godot anthropic_sidecar.sh haiku \
       "ai-worker,godot,$SS_REG" "Glob,Grep,Read" "semantic-search,ai-worker,godot" \
       -G scout -t "+mcp:godot"
  live rm-worker anthropic_sidecar.sh haiku \
       "$SS_REG" "Bash,Edit,Glob,Grep,Read,Write" "semantic-search" \
       -G author -t "-mcp:ai-worker"
  ran=$((ran + 1))
  echo "NOTE opencode live inventory BLOCKED: transport UNAVAILABLE (FreeTierError 403 at SessionStart); its argv is covered in section B"
else
  echo "SKIP section C: real-child inventory needs TOOL_PROFILE_LIVE=1 (the standing battery must not spend)"
fi

expected=$((5 + 3 + 5 + 4 + 3 + 3 + 11 + ${#LAUNCHERS[@]} + 2))
[ "$ran" -ge "$expected" ] || { echo "FAIL expected >= $expected cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_tool_profiles" || echo "FAIL test_sidecar_tool_profiles"
exit "$fail"
