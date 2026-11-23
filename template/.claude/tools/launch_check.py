#!/usr/bin/env python3
"""The launch checks both dispatch routes share: the seat launcher (`scripts/seat.sh`) and the native Workflow
entry (`hooks/workflow_transport.py`, B3). The three CLI-process refusals (a writing seat outside its own linked
worktree, a full seat, a login at its capacity floor) stay in `seat.sh`; they mean nothing for a Workflow job.

    launch_check.py check --brief <brief.md> --out <out.md> --root <checkout> --writes yes|no
        Refuses (exit 2, one stderr line) when the brief, the output directory or an `inputs:` entry does not
        resolve under --root, or a `writes: no` job's brief names a `targets:` entry other than <out.md>.
    launch_check.py seat <seat>
        Prints the seat's `seats.json` row as shell assignments for seat.sh to eval. Exit 2 for an unknown seat.
    launch_check.py pre_launch <seat> <brief.md> <out.md> <label>
    launch_check.py post_return <seat> <brief.md> <out.md> <label>
        Run the project's launch hook (adaptation key `dispatch.pre_launch` / `dispatch.post_return`) in the
        caller's cwd, the job's workdir. An unset key runs nothing. A non-zero exit refuses with the hook's stderr
        verbatim. A `targets:` header is `targets:` on its own line followed by `- <path>` lines; `inputs:` likewise.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[1]
PRE_KEY = "dispatch.pre_launch"
POST_KEY = "dispatch.post_return"
SEATS = "seats.json"
# Python resolves a bare `bash` to System32ash.exe (WSL) on Windows, which cannot read Git Bash paths.
BASH = shutil.which("bash") or "bash"
LIST_HEADERS = ("inputs:", "targets:")


def parse_brief(text):
    """`{'inputs': [...], 'targets': [...]}` from the brief's `inputs:` and `targets:` list headers."""
    found = {"inputs": [], "targets": []}
    header = None
    for raw in text.splitlines():
        line = raw.strip()
        if line in LIST_HEADERS:
            header = line[:-1]
        elif header and line.startswith("- "):
            found[header].append(line[2:].strip())
        elif line:
            header = None
    return found


def _resolve(root, path):
    p = Path(path)
    return p if p.is_absolute() else Path(root) / p


def check(brief, out, root, writes):
    """The refusal causes for one job, in order. An empty list allows the launch."""
    brief_path = _resolve(root, brief)
    out_path = _resolve(root, out)
    if not brief_path.is_file():
        return ["brief does not resolve: %s" % brief]
    if not out_path.parent.is_dir():
        return ["output directory does not resolve: %s" % out_path.parent]
    parsed = parse_brief(brief_path.read_text(encoding="utf-8", errors="replace"))
    for item in parsed["inputs"]:
        if not _resolve(root, item).exists():
            return ["input does not resolve against %s: %s" % (root, item)]
    if writes == "no":
        for target in parsed["targets"]:
            if _resolve(root, target).resolve() != out_path.resolve():
                return ["read-only seat names a write target other than %s: %s" % (out, target)]
    return []


def seat_rows(claude_dir=CLAUDE_DIR):
    return json.loads((Path(claude_dir) / SEATS).read_text(encoding="utf-8"))["seats"]


def seat_assignments(seat, claude_dir=CLAUDE_DIR):
    """Shell assignments for one seat, or None when the seat is not in seats.json."""
    row = seat_rows(claude_dir).get(seat)
    if row is None:
        return None
    config = row.get("configDir") or ""
    values = {
        "SEAT_ADAPTER": row["adapter"],
        "SEAT_WRITES": row["writes"],
        "SEAT_MAX": str(row["max_concurrent"]),
        "SEAT_CONFIG_DIR": os.path.expanduser(config) if config else "",
        "SEAT_MODEL": row["model"],
    }
    return "\n".join("%s=%s" % (key, shlex.quote(value)) for key, value in values.items())


def _adaptation_get(key):
    sys.path.insert(0, str(CLAUDE_DIR / "tools"))
    import adaptation  # noqa: E402  (tools/adaptation.py: the one reader of adaptation.json)
    return adaptation.get(CLAUDE_DIR, key)


def run_hook(key, args, cwd, lookup=_adaptation_get):
    """Run the project hook named by `key` with `args`; returns (exit code, stderr). Unset runs nothing."""
    command = lookup(key)
    if not command:
        return 0, ""
    line = " ".join([command] + [shlex.quote(arg) for arg in args])  # argv via the string: Git Bash drops positional args of `bash -c`
    done = subprocess.run([BASH, "-c", line], cwd=cwd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stderr


def main(argv=None, lookup=_adaptation_get):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: launch_check.py check|seat|pre_launch|post_return ...", file=sys.stderr)
        return 2
    verb, rest = args[0], args[1:]
    if verb == "check" and len(rest) == 8:
        opts = dict(zip(rest[::2], rest[1::2]))
        causes = check(opts["--brief"], opts["--out"], opts["--root"], opts["--writes"])
        if causes:
            print("launch refused: " + causes[0], file=sys.stderr)
            return 2
        return 0
    if verb == "seat" and len(rest) == 1:
        text = seat_assignments(rest[0])
        if text is None:
            print("launch refused: unknown seat '%s' (seats.json)" % rest[0], file=sys.stderr)
            return 2
        print(text)
        return 0
    if verb in ("pre_launch", "post_return") and len(rest) == 4:
        key = PRE_KEY if verb == "pre_launch" else POST_KEY
        rc, err = run_hook(key, rest, os.getcwd(), lookup=lookup)
        if rc:
            sys.stderr.write(err)
            return 2
        return 0
    print("usage: launch_check.py check|seat|pre_launch|post_return ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
