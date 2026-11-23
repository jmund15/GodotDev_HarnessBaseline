#!/usr/bin/env bash
# seat_agy.sh <brief.md> <workdir>: one Antigravity seat, run unchanged through tools/agy_seat.py, which holds every
# agy-specific decision (the run's settings, the stop at the first ungoverned tool, the result envelope).
# seat.sh sets SEAT_MODEL and SEAT_WRITES. The seat gets the read built-ins; agy_workspace.py writes the floor bridge
# (.agents/hooks.json) into the workdir first, so a shell call the run still makes passes the floor.
# The brief is the prompt (stdin); the answer is the envelope's result, on stdout. A failed run exits non-zero with the result on stderr.
# --check prints OK, or one UNAVAILABLE line and exits 4, and starts nothing.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ag="$here/tools/agy_seat.py"
if [ "${1:-}" = --check ]; then
  python3 -c 'import os, sys; sys.exit(0 if os.path.isfile(sys.argv[1]) else 1)' "$(python3 "$ag" bin)" && { echo OK; exit 0; }
  echo "UNAVAILABLE (agy CLI not found)"
  exit 4
fi
brief="$1"
workdir="$2"
# No fence exists for a writing agy seat, and none has been asked for: refuse the row.
if [ "$SEAT_WRITES" = yes ]; then
  echo "seat_agy.sh: refused: a writing agy seat has no workdir fence; set the row to writes: no" >&2
  exit 2
fi
builtins="Read,Glob,Grep,LS"
model="${SEAT_MODEL#antigravity/}"
settings="$(mktemp)"
stream="$(mktemp)"
envelope="$(mktemp)"
trap 'rm -f "$settings" "$stream" "$envelope"' EXIT
python3 "$ag" settings --builtins "$builtins" --servers "" > "$settings" || exit 2
allowed="$(python3 "$ag" allowed --builtins "$builtins" --servers "")" || exit 2
cd "$workdir" || exit 1
python3 "$here/tools/agy_workspace.py" write --root "$workdir" > /dev/null || exit 2
agy_bin="$(python3 "$ag" bin)" || exit 2
python3 "$ag" run --stream "$stream" --servers "" -- "$agy_bin" --output-format stream-json --model "$model" \
  --settings "$settings" --disable-slash-commands < "$brief" > /dev/null
rc=$?
python3 "$ag" parse --stream "$stream" --model "$model" --rc "$rc" --allowed "$allowed" --root "$workdir" > "$envelope" || exit 2
python3 -c 'import json, sys
env = json.load(open(sys.argv[1], encoding="utf-8"))
if env.get("is_error"):
    sys.stderr.write(str(env.get("result", "")) + "\n")
    sys.exit(1)
sys.stdout.write(str(env.get("result", "")) + "\n")' "$envelope"
