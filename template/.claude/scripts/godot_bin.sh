#!/usr/bin/env bash
# Canonical engine invocation — statically allowlisted via `Bash(bash *)`, so auto mode
# approves it without classifier delegation.
# The raw `"$GODOT_BIN" ...` form prompts: expansions defeat static permission matching
# (simple_expansion -> cannot delegate to the auto-approval classifier -> manual prompt).
# Invocation: bash .claude/scripts/godot_bin.sh <engine args...> (e.g. --headless --path <dir> --script res://x.gd)
# Args pass through as discrete argv elements — no shell re-parse.
# Agent runs isolate APPDATA under LOCALAPPDATA/{{PROJECT_NAME}}/agent-appdata/<checkout>.
# ENGINE_OWNER_USERDATA=1 explicitly opts into inherited owner data. USERDIR on stderr
# names the actual user:// folder (including Godot/app_userdata/{{PROJECT_NAME}}), once per run.
# Doctrine home: CLAUDE.md §Shell Discipline "Auto mode fails closed" + gotcha_auto_mode_classifier_fail_closed.md.
set -euo pipefail
ENGINE="${GODOT_BIN:-}"
if [ -z "$ENGINE" ] || [ ! -f "$ENGINE" ]; then
  echo "godot_bin.sh: GODOT_BIN is unset or missing ('${ENGINE}')" >&2
  exit 1
fi
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
# cygpath is needed when a native Python receives a Git Bash /c/... script path.
if command -v cygpath >/dev/null 2>&1; then
  SCRIPT_DIR="$(cygpath -am "$SCRIPT_DIR")"
fi
ISOLATION="$(python3 "$SCRIPT_DIR/engine_userdata.py" --repo "$SCRIPT_DIR/../.." --shell)"
eval "$ISOLATION"
printf 'USERDIR=%s\n' "$USERDIR" >&2
exec "$ENGINE" "$@"
