#!/usr/bin/env bash
# seat_claude.sh <brief.md> <workdir>: one headless Claude seat, the second login. seat.sh sets SEAT_CONFIG_DIR,
# SEAT_MODEL, SEAT_WRITES and SEAT_SETTINGS. The brief is the prompt (stdin), the final answer is stdout.
# A writing seat edits files in its workdir; a read-only seat has no edit tools. Its own binary is found on PATH.
brief="$1"
workdir="$2"
args=(-p --model "$SEAT_MODEL" --output-format text --add-dir "$workdir" --settings "$SEAT_SETTINGS")
if [ "$SEAT_WRITES" = yes ]; then
  args+=(--permission-mode acceptEdits)
else
  args+=(--disallowedTools "Write,Edit,MultiEdit,NotebookEdit")
fi
cd "$workdir" || exit 1
if [ -n "$SEAT_CONFIG_DIR" ]; then
  export CLAUDE_CONFIG_DIR="$SEAT_CONFIG_DIR"
fi
exec claude "${args[@]}" < "$brief"
