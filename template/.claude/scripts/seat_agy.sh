#!/usr/bin/env bash
# seat_agy.sh <brief.md> <workdir>: one Antigravity seat, run unchanged through tools/agy_seat.py, which holds every
# agy-specific decision (the run's settings, the stop at the first ungoverned tool, the result envelope).
# seat.sh sets SEAT_MODEL and SEAT_WRITES. Read-only seats get the read built-ins; a writing seat adds the edit and shell ones.
# The brief is the prompt (stdin); the answer is the envelope's result, on stdout. A failed run exits non-zero with the result on stderr.
brief="$1"
workdir="$2"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ag="$here/tools/agy_seat.py"
if [ "$SEAT_WRITES" = yes ]; then
  builtins="Read,Glob,Grep,LS,Write,Edit,Bash"
else
  builtins="Read,Glob,Grep,LS"
fi
model="${SEAT_MODEL#antigravity/}"
settings="$(mktemp)"
stream="$(mktemp)"
envelope="$(mktemp)"
trap 'rm -f "$settings" "$stream" "$envelope"' EXIT
python3 "$ag" settings --builtins "$builtins" --servers "" > "$settings" || exit 2
allowed="$(python3 "$ag" allowed --builtins "$builtins" --servers "")" || exit 2
cd "$workdir" || exit 1
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
