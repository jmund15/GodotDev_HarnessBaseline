#!/usr/bin/env python3
"""Validate .tres/.tscn `load_steps` headers against the real block count.

In Godot 4.x text resources, the header value `load_steps = N` must equal the number of
`[ext_resource ...]` blocks + the number of `[sub_resource ...]` blocks + 1 (the main
resource). A drift between the two is cosmetic hygiene, NOT correctness: the engine
re-derives the count on load and rewrites the header on the next editor resave, so a
mismatch never breaks loading or casts -- it only leaves the file dirty (a misleading
diff). 36 files were found mismatched and corrected (2026-07-24); this hook prevents the
count drifting again on .tres/.tscn writes.

Detection: for a file whose header line carries `load_steps = N`, count lines starting
with `[ext_resource` and `[sub_resource` and compare `N` to `ext + sub + 1`. A file whose
header OMITS `load_steps` is valid (Godot omits it when it can -- 379 files in this repo
do, some with ext/sub blocks present) and is skipped, never flagged.

Posture: warn-only, never blocks. A load_steps mismatch has no runtime consequence, so
unlike the correctness guards (tres_script_strip_guard / tres_nullstrip_guard) this does
not deny a commit. It surfaces the drift at the write seam and in CI/regression-gate so
the author can tidy the header in the same edit.

Modes:
    load_steps_validator.py               # scan staged (git diff --cached); report; exit 1 on mismatch
    load_steps_validator.py --range A..B  # scan a commit range (CI / PR review); exit 1 on mismatch
    load_steps_validator.py --hook        # PostToolUse(Write|Edit): validate the written file; exit 0
    load_steps_validator.py --worktree    # SessionStart: scan working tree vs HEAD; report; exit 0
"""
import json
import re
import subprocess
import sys

# Windows pipes default to cp1252 through Python 3.14; git diff text now decodes as real
# UTF-8, so a non-ASCII .tres string payload or path would raise UnicodeEncodeError on write.
sys.stdout.reconfigure(encoding="utf-8")

FILE_HDR = re.compile(r"^\+\+\+ b/(.*)$")
HDR_LOADSTEPS = re.compile(r"load_steps\s*=\s*(\d+)")
EXT = re.compile(r"^\[ext_resource\b")
SUB = re.compile(r"^\[sub_resource\b")


def validate_text(text):
    """Return (load_steps_or_None, ext_count, sub_count).

    load_steps is None when the header omits it (valid; caller skips).
    """
    lines = text.splitlines()
    if not lines:
        return None, 0, 0
    m = HDR_LOADSTEPS.search(lines[0])
    ls = int(m.group(1)) if m else None
    ext = sum(1 for line in lines if EXT.match(line))
    sub = sum(1 for line in lines if SUB.match(line))
    return ls, ext, sub


def validate_file(path):
    """Return (path, load_steps, ext, sub, expected) if the file has a real mismatch, else None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    ls, ext, sub = validate_text(text)
    if ls is None:
        return None
    expected = ext + sub + 1
    if ls != expected:
        return (path, ls, ext, sub, expected)
    return None


def diff_lines(range_arg, worktree=False):
    cmd = ["git", "diff", "-U0", "--no-color"]
    if worktree:
        cmd += ["HEAD"]
    elif range_arg:
        cmd += [range_arg]
    else:
        cmd += ["--cached"]
    cmd += ["--", "*.tres", "*.tscn"]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.splitlines()


def paths_in_diff(range_arg, worktree=False):
    """Return the set of .tres/.tscn paths touched by the diff (content parsed from the working tree)."""
    paths = []
    current = None
    for line in diff_lines(range_arg, worktree):
        hdr = FILE_HDR.match(line)
        if hdr:
            current = hdr.group(1)
            paths.append(current)
    # The paths from the b/ header are repo-relative and on-disk; validate the current state.
    out = set()
    for p in paths:
        if p.endswith((".tres", ".tscn")):
            out.add(p)
    return out


def find_mismatches(range_arg, worktree=False):
    findings = []
    for path in paths_in_diff(range_arg, worktree):
        r = validate_file(path)
        if r:
            findings.append(r)
    return findings


def report(findings, surface="guard"):
    tag = f"[{surface}]"
    print(
        f"{tag} .tres/.tscn load_steps header mismatch ({len(findings)} file(s)) -- cosmetic "
        "hygiene, not a load error; Godot rewrites the header on next editor resave:",
        file=sys.stderr,
    )
    for path, ls, ext, sub, expected in findings:
        print(f"  {path}: load_steps={ls} but ext={ext} + sub={sub} + 1 = {expected}", file=sys.stderr)
    print(
        f"\nFix by setting the header to `load_steps={findings[0][4]}' (ext + sub + 1) so the "
        "file stays clean (no resave diff). A file that omits load_steps entirely is valid and "
        "skipped.",
        file=sys.stderr,
    )


def standalone(range_arg):
    findings = find_mismatches(range_arg)
    if not findings:
        return 0
    report(findings)
    return 1


def hook():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0
    tool_input = data.get("tool_input", {}) or {}
    path = tool_input.get("file_path", "") or ""
    if not path.endswith((".tres", ".tscn")):
        print("{}")
        return 0
    r = validate_file(path)
    if not r:
        print("{}")
        return 0
    path, ls, ext, sub, expected = r
    rel = path.replace("\\", "/")
    # PostToolUse stderr is NOT model-visible; additionalContext is the only advisory
    # channel (per check_logger_tag_prefix.py). Never deny -- cosmetic hygiene only.
    lines = [
        f"[load_steps_validator] {rel}: header `load_steps={ls}` but the file has {ext} "
        f"[ext_resource] + {sub} [sub_resource] + 1 = {expected}. Cosmetic: Godot rewrites "
        f"the header on next editor resave (no load impact). Set it to {expected} to keep "
        "the file clean."
    ]
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "\n".join(lines),
    }}))
    return 0


def worktree_scan():
    """SessionStart surface: report any working-tree mismatch (stdout -> session context).

    Always exits 0 -- a SessionStart hook must inform, not block.
    """
    findings = find_mismatches(None, worktree=True)
    if not findings:
        return 0
    print(
        f"[load-steps-validator] WORKING-TREE load_steps drift in {len(findings)} file(s) "
        "-- headers disagree with their block count (cosmetic; Godot rewrites on resave):"
    )
    for path, ls, ext, sub, expected in findings:
        print(f"  {path}: load_steps={ls} but ext={ext} + sub={sub} + 1 = {expected}")
    return 0


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    if args and args[0] == "--worktree":
        return worktree_scan()
    range_arg = args[1] if len(args) > 1 and args[0] == "--range" else None
    return standalone(range_arg)


if __name__ == "__main__":
    sys.exit(main())
