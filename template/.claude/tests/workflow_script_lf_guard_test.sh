#!/usr/bin/env bash
# Firing proof for hooks/workflow_script_lf_guard.py — a CRLF script under .claude is rewritten to LF
# before the permission handler inlines it; an LF script, a non-Workflow tool, a path outside .claude
# and a malformed payload are all left alone. Run: bash .claude/tests/workflow_script_lf_guard_test.sh
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cygpath -m "$ROOT" 2>/dev/null || echo "$ROOT")"   # C:/ form — Windows Python cannot open /c/ paths
HOOK="$ROOT/hooks/workflow_script_lf_guard.py"
TMP="$ROOT/scratch/lf_guard_probe"; mkdir -p "$TMP"
fail=0
crs() { python3 -c "import sys;print(open(sys.argv[1],'rb').read().count(b'\r'))" "$1"; }
check() { local name="$1" want="$2" got="$3"; if [ "$got" = "$want" ]; then echo "  PASS  $name"; else echo "  FAIL  $name (got $got, want $want)"; fail=1; fi; }
echo "workflow_script_lf_guard — firing proof"

printf 'export const meta = {}\r\nawait agent("x")\r\n' > "$TMP/crlf.js"
out=$(printf '{"tool_name":"Workflow","tool_input":{"scriptPath":"%s"}}' "$TMP/crlf.js" | python3 "$HOOK")
check "CRLF script rewritten to LF" 0 "$(crs "$TMP/crlf.js")"
check "rewrite is reported to the model" 1 "$(printf '%s' "$out" | grep -c 'rewrote 2 CRLF')"

printf 'export const meta = {}\r\n' > "$TMP/msys.js"
printf '{"tool_name":"Workflow","tool_input":{"scriptPath":"%s"}}' "$(cygpath -u "$TMP/msys.js" 2>/dev/null || echo "$TMP/msys.js")" | python3 "$HOOK" >/dev/null
check "MSYS /c/ path is translated and rewritten" 0 "$(crs "$TMP/msys.js")"

WT="$ROOT/worktrees/scope_probe"
mkdir -p "$WT/.CLAUDE/Hooks"
printf 'export const meta = {}\r\n' > "$WT/.CLAUDE/Hooks/upper.js"
out=$(printf '{"tool_name":"Workflow","tool_input":{"scriptPath":"%s"}}' "$WT/.CLAUDE/./Hooks/upper.js" | python3 "$HOOK")
check "uppercase worktree harness path is rewritten" 0 "$(crs "$WT/.CLAUDE/Hooks/upper.js")"
check "uppercase worktree rewrite is reported" 1 "$(printf '%s' "$out" | grep -c 'rewrote 1 CRLF')"

mkdir -p "$WT/.claude/hooks"
printf 'export const meta = {}\r\n' > "$WT/Game.js"
out=$(printf '{"tool_name":"Workflow","tool_input":{"scriptPath":"%s"}}' "$WT/.claude/./hooks/../../Game.js" | python3 "$HOOK")
check "dot-segment escape keeps a worktree game script untouched" "1|" "$(crs "$WT/Game.js")|$out"

printf 'export const meta = {}\nawait agent("x")\n' > "$TMP/lf.js"
out=$(printf '{"tool_name":"Workflow","tool_input":{"scriptPath":"%s"}}' "$TMP/lf.js" | python3 "$HOOK")
check "LF script untouched and silent" "0|" "$(crs "$TMP/lf.js")|$out"

printf 'a\r\n' > "$TMP/agent.js"
printf '{"tool_name":"Agent","tool_input":{"prompt":"x"}}' | python3 "$HOOK" >/dev/null
check "Agent tool ignored" 1 "$(crs "$TMP/agent.js")"

OUT="$(cygpath -m "${TEMP:-/tmp}" 2>/dev/null || echo "${TEMP:-/tmp}")/lf_guard_outside.js"; printf 'a\r\n' > "$OUT"
printf '{"tool_name":"Workflow","tool_input":{"scriptPath":"%s"}}' "$OUT" | python3 "$HOOK" >/dev/null
check "path outside .claude left alone" 1 "$(crs "$OUT")"; rm -f "$OUT"

printf 'not json' | python3 "$HOOK" >/dev/null; check "malformed payload exits 0" 0 "$?"
rm -f "$TMP/crlf.js" "$TMP/msys.js" "$TMP/lf.js" "$TMP/agent.js"; rmdir "$TMP" 2>/dev/null
rm -rf "$WT"
exit $fail
