#!/usr/bin/env bash
# seat.sh <seat> <brief.md> <out.md> [label]
#
# Runs one external seat's job from the current directory, the job's workdir, and writes <out.md>.
# Refuses before it spawns anything, with one stderr line:
#   - an input, the output directory or a read-only job's write target does not resolve (tools/launch_check.py check);
#   - a writing seat is not started in its own linked worktree;
#   - the seat already runs max_concurrent jobs on this machine;
#   - tools/capacity.py reports the login at its exhaust floor (exit 3). Any other exit warns and launches.
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

# The wait checks ride into every seat, whatever its workdir: the seat gets no completion wake of its own.
# A writing seat also gets the workdir fence, which keeps its writes out of the main checkout.
fence=""
if [ "$SEAT_WRITES" = yes ]; then
  fence=",{\"matcher\": \"Write|Edit|NotebookEdit|Bash\", \"hooks\": [{\"type\": \"command\", \"command\": \"python3 '$here/hooks/sidecar_workdir_guard.py' '$workdir' '$main_checkout' '$claimed' '$brief_abs'\"}]}"
fi
cat > "$claimed/settings.json" <<EOF
{"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": "python3 '$here/hooks/foreground_wait_guard.py'"}]}$fence]}}
EOF

export SEAT_CONFIG_DIR SEAT_MODEL SEAT_WRITES SEAT_SETTINGS="$claimed/settings.json" SEAT_HERE="$here"
bash "$here/$SEAT_ADAPTER" "$brief_abs" "$workdir" > "$claimed/out.md" 2> "$claimed/err.txt"
rc=$?
if [ "$rc" -ne 0 ]; then
  cat "$claimed/err.txt" >&2
  exit "$rc"
fi
cat "$claimed/out.md" > "$out_abs"

python3 "$lc" post_return "$seat" "$brief_abs" "$out_abs" "$label" || exit 2
exit 0
