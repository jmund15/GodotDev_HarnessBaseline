#!/usr/bin/env bash
# seat_claude.sh <brief.md> <workdir>: one headless Claude seat, the second login. seat.sh sets SEAT_CONFIG_DIR,
# SEAT_MODEL, SEAT_WRITES and SEAT_SETTINGS. The brief is the prompt (stdin), the final answer is stdout.
# A writing seat edits files in its workdir; a read-only seat has no edit tools. Its own binary is found on PATH.
# scripts/seat_local.sh runs this same child against a local server and sets three more values: SEAT_OUTPUT_FORMAT
# (default text), SEAT_DISALLOWED (tools denied beyond the read-only set) and SEAT_EFFORT (passed as --effort).
# --check prints OK, or one UNAVAILABLE line and exits 4, and starts nothing.
if [ "${1:-}" = --check ]; then
  command -v claude >/dev/null 2>&1 && { echo OK; exit 0; }
  echo "UNAVAILABLE (claude CLI not found)"
  exit 4
fi
brief="$1"
workdir="$2"
args=(-p --model "$SEAT_MODEL" --output-format "${SEAT_OUTPUT_FORMAT:-text}" --add-dir "$workdir" --settings "$SEAT_SETTINGS")
[ -n "${SEAT_EFFORT:-}" ] && args+=(--effort "$SEAT_EFFORT")
if [ "$SEAT_WRITES" = yes ]; then
  args+=(--permission-mode acceptEdits)
  [ -n "${SEAT_DISALLOWED:-}" ] && args+=(--disallowedTools "$SEAT_DISALLOWED")
else
  args+=(--disallowedTools "Write,Edit,MultiEdit,NotebookEdit${SEAT_DISALLOWED:+,$SEAT_DISALLOWED}")
fi
cd "$workdir" || exit 1
if [ -n "$SEAT_CONFIG_DIR" ]; then
  export CLAUDE_CONFIG_DIR="$SEAT_CONFIG_DIR"
fi
exec claude "${args[@]}" < "$brief"
