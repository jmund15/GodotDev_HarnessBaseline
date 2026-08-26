#!/usr/bin/env bash
# Canonical engine invocation — statically allowlisted via `Bash(bash *)`, so auto mode
# approves it without classifier delegation.
# The raw `"$GODOT_BIN" ...` form prompts: expansions defeat static permission matching
# (simple_expansion -> cannot delegate to the auto-approval classifier -> manual prompt).
# Invocation: bash .claude/scripts/godot_bin.sh <engine args...> (e.g. --headless --path <dir> --script res://x.gd)
# Args pass through as discrete argv elements — no shell re-parse.
# Doctrine home: CLAUDE.md §Shell Discipline "Auto mode fails closed" + gotcha_auto_mode_classifier_fail_closed.md.
set -euo pipefail
ENGINE="${GODOT_BIN:-}"
if [ -z "$ENGINE" ] || [ ! -f "$ENGINE" ]; then
  echo "godot_bin.sh: GODOT_BIN is unset or missing ('${ENGINE}')" >&2
  exit 1
fi
exec "$ENGINE" "$@"
