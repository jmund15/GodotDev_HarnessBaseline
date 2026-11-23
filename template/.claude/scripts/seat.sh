#!/usr/bin/env bash
# seat.sh <seat> <brief.md> <out.md> [label]
#
# Runs one external seat's job from the current directory, the job's workdir, and writes <out.md>.
# Refuses before it spawns anything, with one stderr line:
#   - an input, the output directory or a read-only job's write target does not resolve (tools/launch_check.py check);
#   - a writing seat is not started in its own linked worktree;
#   - the seat already runs max_concurrent jobs on this machine;
#   - tools/capacity.py reports the login at its exhaust floor (exit 3). Any other exit warns and launches.
# The adapter reads one prompt, <slot>/prompt.md: guards/rails.md, then the brief. The seat's environment carries
# CLAUDE_CODE_SIDECAR=1, which the kept hooks read to know a sidecar child runs.
# Then the project's dispatch.pre_launch hook runs; after the CLI exits 0, dispatch.post_return runs in the workdir.
# Exit 0 means <out.md> is written. Any other exit carries the CLI's stderr verbatim.
set -u

if [ "$#" -lt 3 ]; then
  echo "usage: seat.sh <seat> <brief.md> <out.md> [label]" >&2
  exit 2
fi
seat="$1"
brief="$2"
out="$3"
label="${4:-$(basename "$out" .md)}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
lc="$here/tools/launch_check.py"
workdir="$(pwd)"
root="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "seat.sh: not inside a git checkout" >&2; exit 2; }

assign="$(python3 "$lc" seat "$seat")" || exit 2
eval "$assign"

python3 "$lc" check --brief "$brief" --out "$out" --root "$root" --writes "$SEAT_WRITES" || exit 2

# Absolute paths for the project hooks, which run in the workdir.
brief_abs="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.join(sys.argv[1], sys.argv[2])))' "$root" "$brief")"
out_abs="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.join(sys.argv[1], sys.argv[2])))' "$root" "$out")"

if [ "$SEAT_WRITES" = yes ]; then
  git_dir="$(git -C "$workdir" rev-parse --path-format=absolute --git-dir)"
  common_dir="$(git -C "$workdir" rev-parse --path-format=absolute --git-common-dir)"
  if [ "$git_dir" = "$common_dir" ]; then
    echo "seat.sh: writing seat '$seat' must run in its own linked worktree, not the main checkout ($workdir)" >&2
    exit 2
  fi
  main_checkout="$(dirname "$common_dir")"
fi

# A slot is a directory under the user's home (the login is shared by every session on the machine). A slot whose
# pid no longer runs is stale and is reclaimed.
slots="$HOME/.claude/seat_slots/$seat"
mkdir -p "$slots"
claimed=""
n=1
while [ "$n" -le "$SEAT_MAX" ]; do
  slot="$slots/$n"
  if [ -f "$slot/pid" ] && ! kill -0 "$(cat "$slot/pid")" 2>/dev/null; then
    rm -rf "$slot"
  fi
  if mkdir "$slot" 2>/dev/null; then
    echo "$$" > "$slot/pid"
    claimed="$slot"
    break
  fi
  n=$((n + 1))
done
if [ -z "$claimed" ]; then
  echo "seat.sh: seat '$seat' is full ($SEAT_MAX running)" >&2
  exit 2
fi
trap 'rm -rf "$claimed"' EXIT

cap="$(python3 "$here/tools/capacity.py" "$seat" 2>&1)"
cap_rc=$?
case "$cap_rc" in
  0) ;;
  3) echo "seat.sh: $cap" >&2; exit 2 ;;
  *) echo "seat.sh: warning: capacity unreadable for '$seat' (exit $cap_rc), launching: $cap" >&2 ;;
esac

python3 "$lc" pre_launch "$seat" "$brief_abs" "$out_abs" "$label" || exit 2

# launch_check.py stage writes the seat's prompt (rails, then the brief) and its settings (the floor, plus the workdir
# fence for a writing seat). Its spill dir is the output directory.
python3 "$lc" stage "$claimed" "$brief_abs" "$out_abs" "$here" "$workdir" "${main_checkout:-}" || exit 2

export CLAUDE_CODE_SIDECAR=1
export SEAT_CONFIG_DIR SEAT_MODEL SEAT_WRITES SEAT_SETTINGS="$claimed/settings.json" SEAT_HERE="$here"
bash "$here/$SEAT_ADAPTER" "$claimed/prompt.md" "$workdir" > "$claimed/out.md" 2> "$claimed/err.txt"
rc=$?
if [ "$rc" -ne 0 ]; then
  cat "$claimed/err.txt" >&2
  exit "$rc"
fi
cat "$claimed/out.md" > "$out_abs"

python3 "$lc" post_return "$seat" "$brief_abs" "$out_abs" "$label" || exit 2
exit 0
