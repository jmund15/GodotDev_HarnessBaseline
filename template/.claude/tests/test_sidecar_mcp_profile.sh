#!/usr/bin/env bash
# Proof for the sidecar settings merge (sc_settings_with_bench_guard).
#
# The merge adds no plugin switch. A plugin's own hook rows come from its registration
# (test_sidecar_common_adaptation.py). The former T1 fragment — enabledPlugins.semantic-search@dreb=false
# for a non-arm child whose -t named no search tool — is gone: every launcher passes
# --strict-mcp-config with the resolved profile's own config, so a server the profile does not name
# is not loaded on any transport. A plugin switch beside a strict config would be a second,
# silently disagreeing answer to "which servers does this child have", and its cases below assert
# its ABSENCE, so a revert of that removal fails here.
#
#   A. the merge function, fed each shape / -t / BENCH_ARM combination directly;
#   B. the `--settings` VALUE each launcher actually hands its child: the launcher's own settings
#      lines are extracted with awk, run, and the child argv captured by a stub CLI. A JSON-only
#      assertion is not enough — that is why (B) exists. The EFFECTIVE child inventory is asserted
#      in test_sidecar_tool_profiles.sh against a real child's own init report.
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/../scripts"
LIB="$SCRIPTS/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
LAUNCHERS=(anthropic_sidecar.sh codex_proxy_sidecar.sh opencode_sidecar.sh deepseek_sidecar.sh)
SS=mcp__plugin_semantic-search_semantic-search__search
PLUGIN_KEY="semantic-search@dreb"
fail=0
ran=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

check() {  # $1 label, $2 expected, $3 actual
  ran=$((ran + 1))
  if [ "$3" = "$2" ]; then echo "OK   $1 -> '$3'"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}

# ---------------------------------------------------------------- A. the merge function itself
merged() {  # $1 shape, $2 -t value, $3 BENCH_ARM value, $4 base settings json
  ( cd "${TMPDIR:-/tmp}" && . "$LIB" >/dev/null 2>&1
    SC_SHAPE="$1" SC_TOOLS="$2" BENCH_ARM="$3" sc_settings_with_bench_guard "$4" 2>/dev/null )
}

key_of() {  # $1 settings json (empty = no --settings at all) -> true|false|absent|bad-json
  python3 -c 'import json,sys
raw = sys.argv[1] if sys.argv[1].strip() else "{}"
try: d=json.loads(raw)
except Exception: print("bad-json"); raise SystemExit
v = (d.get("enabledPlugins") or {}).get(sys.argv[2], "absent")
print(str(v).lower() if v != "absent" else "absent")' "$1" "$PLUGIN_KEY"
}

# The merge hands every child the loss floor's one entry (hooks/pre_bash.py) unless its checkout registers it, so
# the cases that assert "no arm hook" look past that entry; floor_count proves it is there exactly once.
without_floor() {  # $1 settings json -> the same JSON minus the floor entry, or "" when nothing else is left
  python3 -c 'import json,sys
raw = sys.argv[1] if sys.argv[1].strip() else "{}"
try: d = json.loads(raw)
except Exception: print(raw); raise SystemExit
hooks = d.get("hooks") or {}
rows = [g for g in hooks.get("PreToolUse") or []
        if not any("hooks/pre_bash.py" in h.get("command", "") for h in g.get("hooks", []))]
if rows: hooks["PreToolUse"] = rows
else: hooks.pop("PreToolUse", None)
if not hooks: d.pop("hooks", None)
print(json.dumps(d) if d else "")' "$1"
}

floor_count() {  # $1 settings json -> how many PreToolUse entries run hooks/pre_bash.py
  python3 -c 'import json,sys
d = json.loads(sys.argv[1] if sys.argv[1].strip() else "{}")
print(sum(any("hooks/pre_bash.py" in h.get("command", "") for h in g.get("hooks", []))
          for g in (d.get("hooks") or {}).get("PreToolUse") or []))' "$1"
}

hook_matcher() {  # $1 settings json -> PreToolUse[0].matcher past the floor entry, or <absent>/<KeyError>
  python3 -c 'import json,sys
raw = sys.argv[1] if sys.argv[1].strip() else "{}"
try:
    d = json.loads(raw); print(d["hooks"]["PreToolUse"][0]["matcher"])
except Exception as e: print("<%s>" % type(e).__name__)' "$(without_floor "$1")"
}

session_hook() {  # $1 settings json -> present|absent (the compaction re-prompt registration)
  python3 -c 'import json,sys
raw = sys.argv[1] if sys.argv[1].strip() else "{}"
try: d = json.loads(raw)
except Exception: print("bad-json"); raise SystemExit
m = next((h.get("matcher") for h in (d.get("hooks") or {}).get("SessionStart") or []), None)
print(m or "absent")' "$1"
}

# The plugin override is gone for EVERY shape and every -t, an arm included: the strict config,
# not a plugin switch, is what withholds a server now.
for shape in survey review any author; do
  check "$shape child gets no plugin override" absent "$(key_of "$(merged "$shape" 'Read,Glob,Grep' '' '')")"
  check "$shape child with no -t gets none"    absent "$(key_of "$(merged "$shape" '' '' '')")"
done
check "unshaped child gets none"             absent "$(key_of "$(merged '' 'Read,Glob,Grep' '' '')")"
check "a child naming the search tool gets none" absent "$(key_of "$(merged review "Read,Glob,Grep,$SS" '' '')")"
check "an ARM gets none"                     absent "$(key_of "$(merged review 'Read,Glob,Grep' 1 '')")"
check "an author ARM also gets none"         absent "$(key_of "$(merged author 'Read,Glob,Grep' 1 '')")"
check "no hook is added without an ARM"      '<KeyError>' "$(hook_matcher "$(merged review 'Read,Glob,Grep' '' '')")"
check "a no-arm merge adds only the floor"   ''     "$(without_floor "$(merged author 'Read,Glob,Grep' '' '')")"
check "a no-arm merge of empty settings adds only the floor" '' "$(without_floor "$(merged any '' '' '')")"
check "a child in a checkout with no floor registration gets it once" 1 "$(floor_count "$(merged review 'Read,Glob,Grep' '' '')")"

# an existing compaction-hook registration survives each merge
base='{"hooks":{"SessionStart":[{"matcher":"compact","hooks":[{"type":"command","command":"x","timeout":15}]}]}}'
check "compaction hook survives a non-arm merge" compact "$(session_hook "$(merged review 'Read,Glob,Grep' '' "$base")")"
check "compaction hook survives an arm merge"    compact "$(session_hook "$(merged review 'Read,Glob,Grep' 1 "$base")")"

# ------------------------------------------------------------------- B. the launcher's own wiring
cat > "$WORK/stub_claude" <<'STUB'
#!/usr/bin/env bash
: > "$STUB_ARGV"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_ARGV"; done
STUB
chmod +x "$WORK/stub_claude"

argv_value() {  # $1 argv file, $2 flag -> the value following the flag, or <absent>
  awk -v f="$2" 'prev == f { print; found = 1; exit } { prev = $0 } END { if (!found) print "<absent>" }' "$1"
}

settings_block() {  # $1 launcher path -> its own two lines that build and pass --settings
  awk '/SC_SETTINGS_JSON="\$\(sc_settings_with_bench_guard/ { on = 1 } on { print } on && /EXTRA_ARGS\+=\(--settings/ { exit }' "$1"
}

for launcher in "${LAUNCHERS[@]}"; do
  src="$SCRIPTS/$launcher"
  body="$(awk '/^run_claude\(\) \{/ { on = 1 } on { print } on && /^}/ { exit }' "$src")"
  block="$(settings_block "$src")"
  if [ -z "$body" ]; then
    ran=$((ran + 1)); echo "FAIL $launcher: run_claude not found"; fail=1; continue
  fi
  if [ -z "$block" ]; then
    ran=$((ran + 1)); echo "FAIL $launcher: it never passes sc_settings_with_bench_guard to --settings"; fail=1; continue
  fi
  for shape in review author; do
    argv="$WORK/argv.$launcher.$shape"
    ( . "$LIB" >/dev/null 2>&1
      set +u
      SC_CLAUDE="$WORK/stub_claude" SC_RUN_CWD="$WORK" SC_WORKDIR="$WORK" SC_TOOLS="Read,Glob,Grep"
      SC_MODEL=m SC_FORMAT=json SC_PROMPT=p SC_TIMEOUT="" SC_DISALLOWED="" SC_MAX_TURNS=""
      SC_SHAPE="$shape" BENCH_ARM="" SC_RESUME_SID="sid-1234"
      SC_SCRUB=() SC_APPEND_ARGS=() EXTRA_ARGS=() VERBOSE_ARGS=() EFFORT_ARGS=() SC_ADD_DIRS=()
      export STUB_ARGV="$argv"
      eval "$block"
      eval "$body"
      run_claude </dev/null >/dev/null 2>&1 )
    if [ ! -s "$argv" ]; then
      ran=$((ran + 1)); echo "FAIL $launcher $shape: stub CLI never ran"; fail=1; continue
    fi
    json="$(argv_value "$argv" --settings)"
    [ "$json" = "<absent>" ] && json=""
    check "$launcher $shape: --settings is valid JSON" "ok" "$(python3 -c 'import json,sys
try:
    json.loads(sys.argv[1] if sys.argv[1].strip() else "{}"); print("ok")
except Exception: print("bad")' "$json")"
    check "$launcher $shape: no plugin override"  absent "$(key_of "$json")"
    check "$launcher $shape: no arm escape hook"  '<KeyError>' "$(hook_matcher "$json")"
    check "$launcher $shape: --resume passed through" "sid-1234" "$(argv_value "$argv" --resume)"
  done
  # an arm gets no plugin override either, through the launcher's own code
  argv="$WORK/argv.$launcher.arm"
  ( cd "${TMPDIR:-/tmp}" && . "$LIB" >/dev/null 2>&1
    set +u
    SC_CLAUDE="$WORK/stub_claude" SC_RUN_CWD="$WORK" SC_WORKDIR="$WORK" SC_TOOLS="Read,Glob,Grep"
    SC_MODEL=m SC_FORMAT=json SC_PROMPT=p SC_TIMEOUT="" SC_DISALLOWED="" SC_MAX_TURNS=""
    SC_SHAPE=review BENCH_ARM=1 SC_RESUME_SID=""
    SC_SCRUB=() SC_APPEND_ARGS=() EXTRA_ARGS=() VERBOSE_ARGS=() EFFORT_ARGS=() SC_ADD_DIRS=()
    export STUB_ARGV="$argv"
    eval "$block"
    eval "$body"
    run_claude </dev/null >/dev/null 2>&1 )
  if [ ! -s "$argv" ]; then
    ran=$((ran + 1)); echo "FAIL $launcher ARM: stub CLI never ran"; fail=1
  else
    check "$launcher ARM: plugin override absent"  absent "$(key_of "$(argv_value "$argv" --settings)")"
  fi
done

expected=$((18 + ${#LAUNCHERS[@]} * 9))
[ "$ran" -eq "$expected" ] || { echo "FAIL expected $expected cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_mcp_profile" || echo "FAIL test_sidecar_mcp_profile"
exit "$fail"
