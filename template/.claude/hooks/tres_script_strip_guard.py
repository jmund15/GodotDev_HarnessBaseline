#!/usr/bin/env python3
"""Guard against Godot editor-resave stripping script bindings from custom-Resource .tres.

A stale-class-registry editor resave silently drops the `[ext_resource type="Script"]`
+ `script = ExtResource(...)` binding from a custom-Resource .tres. The resource then
loads as a bare Godot.Resource, and any cast to its C# type throws InvalidCastException
-- with a green build and no warning. This bit the encounter/floor/procgen config .tres
TWICE: fixed in 67873ef1, then re-stripped by 55ef215c across 22 files (only 4 tests
cast-and-caught it; the damage was far wider). See
gotcha_godot_editor_resave_hazards.

Detection: in the staged (or ranged) .tres diff, a file that REMOVES more
`script = ExtResource(...)` bindings than it adds has lost a script binding. Counting
net removals (not exact-line pairing) survives the resave also renaming ext_resource
ids. Diff-scoped -- already-committed state isn't re-flagged; this catches the *next*
strip at commit time.

Modes:
    tres_script_strip_guard.py              # scan staged (git diff --cached); exit 1 on strip
    tres_script_strip_guard.py --range A..B # scan a commit range (CI / PR review); exit 1 on strip
    tres_script_strip_guard.py --hook       # PreToolUse: deny a `git commit` that strips a binding
    tres_script_strip_guard.py --worktree   # scan working tree vs HEAD; report, exit 0
    tres_script_strip_guard.py --worktree --repair-inplace  # restore stripped bindings IN PLACE
                                             # from HEAD (keeps the editor's normalization -- the
                                             # fixed-point repair; a git-restore re-plants old-format
                                             # and re-arms the dirty-flag bomb)
    tres_script_strip_guard.py --worktree --revert  # git checkout each stripped file (last resort:
                                             # re-plants old-format, strip recurs at next save)
    tres_script_strip_guard.py --editor-check  # warn when a Godot editor runs on an assembly newer
                                             # than its boot (the preventable bind-failure window)

Escape hatch (rare intentional removal of a genuinely-deleted scripted sub-resource):
set PP_ALLOW_TRES_SCRIPT_REMOVAL=1 in the environment.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows pipes default to cp1252 through Python 3.14; git diff text now decodes as real
# UTF-8, so a non-ASCII .tres string payload or path would raise UnicodeEncodeError on write.
sys.stdout.reconfigure(encoding="utf-8")

REMOVED = re.compile(r"^-\s*script = ExtResource\(")
ADDED = re.compile(r"^\+\s*script = ExtResource\(")
FILE_HDR = re.compile(r"^\+\+\+ (?:b/(.*)|/dev/null)$")


def diff_lines(range_arg, worktree=False):
    cmd = ["git", "diff", "-U0", "--no-color"]
    if worktree:
        cmd += ["HEAD"]
    elif range_arg:
        cmd += [range_arg]
    else:
        cmd += ["--cached"]
    cmd += ["--", "*.tres"]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.splitlines()


def find_strips(range_arg, worktree=False):
    """Return [(path, removed, added)] for files with a net script-binding loss."""
    per_file = {}
    current = None
    for line in diff_lines(range_arg, worktree):
        hdr = FILE_HDR.match(line)
        if hdr:
            # `+++ /dev/null` is a DELETED file (group 1 None): its script binding goes away with the
            # file, which is a deletion and not an editor strip. Clearing `current` is what stops
            # those removals being charged to the PREVIOUS file in the diff — measured 2026-08-13, a
            # deleted .tres reported a phantom strip in its alphabetical neighbour.
            current = hdr.group(1)
            if current is not None:
                per_file.setdefault(current, [0, 0])
            continue
        if current is None:
            continue
        if REMOVED.match(line):
            per_file[current][0] += 1
        elif ADDED.match(line):
            per_file[current][1] += 1
    return [(p, r, a) for p, (r, a) in per_file.items() if r > a]


def report(findings):
    print(
        "[tres-script-strip-guard] Suspected editor-resave script-binding strip "
        f"({len(findings)} file(s)):",
        file=sys.stderr,
    )
    for path, removed, added in findings:
        print(f"  {path}: script bindings removed={removed} added={added}", file=sys.stderr)
    print(
        "\nA custom-Resource .tres that loses its `script = ExtResource(...)` line loads as a "
        "bare Godot.Resource -> casts to its C# type throw at runtime with a green build (the "
        "stale-registry editor-resave strip; gotcha_godot_editor_resave_hazards). "
        "Restore the binding (e.g. `git checkout <pre-resave>^ -- <file>`), or if you genuinely "
        "deleted a scripted sub-resource, set PP_ALLOW_TRES_SCRIPT_REMOVAL=1.",
        file=sys.stderr,
    )


def standalone(range_arg):
    if os.environ.get("PP_ALLOW_TRES_SCRIPT_REMOVAL"):
        return 0
    findings = find_strips(range_arg)
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
    if data.get("tool_name") != "Bash":
        print("{}")
        return 0
    cmd = data.get("tool_input", {}).get("command", "")
    if "git commit" in cmd:
        if os.environ.get("PP_ALLOW_TRES_SCRIPT_REMOVAL"):
            print("{}")
            return 0
        # Hook processes inherit the harness env, not the command's — honor the documented
        # hatch when it is declared inline on the gated command (transcript-auditable).
        if "PP_ALLOW_TRES_SCRIPT_REMOVAL=1" in cmd:
            print("{}")
            return 0

        findings = find_strips(None)
        if not findings:
            print("{}")
            return 0

        report(findings)  # surfaced in the transcript alongside the deny
        reason = (
            f"Blocked: {len(findings)} staged .tres lost a `script = ExtResource(...)` binding "
            "(editor-resave script strip -> bare-Resource load / InvalidCastException). Restore the "
            "binding, or set PP_ALLOW_TRES_SCRIPT_REMOVAL=1 if the removal is intentional."
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
        return 0
    if ("git restore" in cmd or "git checkout" in cmd) and re.search(r"\.tres|restore \.|checkout \.", cmd):
        # A git-restore of a .tres re-plants the OLD-FORMAT dirty-flag bomb (missing ext_resource
        # uids -> the editor rewrites on the next save -> the strip recurs). Allow, but advise.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                "[tres-script-strip-guard] `git restore`/`checkout` of a .tres re-plants the "
                "OLD-FORMAT dirty-flag bomb (ext_resource uid-less -> editor rewrites at the next "
                "save -> strip recurs, the 4x encounter corruption). For a stripped file use "
                "`tres_script_strip_guard.py --worktree --repair-inplace` (keeps the editor's "
                "normalization); godot_files.md §UID handling."
            ),
        }}))
        return 0
    print("{}")
    return 0


def worktree_scan(revert, repair=False):
    """SessionStart surface: report (stdout -> session context) any working-tree strip.

    Always exits 0 -- a SessionStart hook must inform, not block. --revert restores
    each stripped file from HEAD (used manually or by a recovery script, never wired
    as a default: an unstaged legit removal must not be silently clobbered).
    """
    findings = find_strips(None, worktree=True)
    if not findings:
        return 0
    print(
        f"[tres-script-strip-guard] WORKING-TREE script-binding strip detected in "
        f"{len(findings)} file(s) -- the editor-resave corruption is present RIGHT NOW:"
    )
    for path, removed, added in findings:
        print(f"  {path}: script bindings removed={removed} added={added}")
    if repair:
        repair_inplace(findings)
        print(f"[tres-script-strip-guard] Repaired {len(findings)} file(s) in place (bindings restored).")
    elif revert:
        subprocess.run(["git", "checkout", "--"] + [p for p, _, _ in findings])
        print(f"[tres-script-strip-guard] Reverted {len(findings)} file(s) from HEAD.")
        print(
            "WARNING: git-restore re-plants OLD-FORMAT .tres (ext_resource uid backfill pending), "
            "re-arming the dirty-flag bomb -- the strip recurs at the next editor save. Prefer "
            "--repair-inplace, which keeps the editor's normalization and re-adds the binding."
        )
    else:
        print(
            "Fix now: verify each diff is a strip (not an intentional removal), then "
            "`tres_script_strip_guard.py --worktree --repair-inplace` (keeps the editor's "
            "normalization, re-adds bindings from HEAD) or `git restore -- <file>` (re-plants "
            "old-format: last resort only). See gotcha_godot_editor_resave_hazards."
        )
    return 0


def editor_boot_check():
    """Prevention signal: warn when a Godot editor runs on an assembly newer than its boot.

    The editor-resave strip needs a script-bind-failure window; the preventable one is an
    editor booted BEFORE the current assembly build -- its cached resources fail restoration
    on the next rebuild/play cycle, and the next save writes them stripped. Inform-only.
    """
    import datetime

    dll = Path(__file__).resolve().parents[1] / ".godot" / "mono" / "temp" / "bin" / "Debug" / "{{PROJECT_NAME}}.dll"
    if not dll.exists():
        return 0
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.ProcessName -like 'Godot*' } | Select-Object -First 1 "
             "| ForEach-Object { $_.StartTime.ToFileTime() }"],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
        ).stdout.strip()
    except Exception:
        return 0
    if not out:
        return 0  # no editor running
    try:
        editor_start = datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=int(out) / 10)
    except ValueError:
        return 0
    dll_mtime = datetime.datetime.fromtimestamp(dll.stat().st_mtime)
    if editor_start < dll_mtime:
        print(
            f"[tres-script-strip-guard] EDITOR BOOTED BEFORE THE CURRENT ASSEMBLY BUILD "
            f"(editor {editor_start:%H:%M} vs {{PROJECT_NAME}}.dll {dll_mtime:%H:%M}). "
            "Restart the editor so its cached resources bind against the current DLL; an "
            "editor-resave across the stale assembly strips .tres script bindings "
            "(gotcha_godot_editor_resave_hazards)."
        )
    return 0


def repair_inplace(findings):
    """Restore stripped bindings IN PLACE from HEAD, keeping the worktree's editor normalization.

    A git-checkout revert re-plants OLD-FORMAT files (missing ext_resource uids) -> the dirty-flag
    bomb (gotcha rule 4): the editor re-marks them for uid backfill, the next save rewrites them,
    and the strip recurs at the next bind-failure window. In-place keeps the editor's
    serialization and re-adds only the lost script binding, making the file a fixed point.
    """
    for path, _, _ in findings:
        wf = Path(path)
        text = wf.read_text(encoding="utf-8")
        if "resource_local_to_scene = \"\"" not in text:
            print(f"  {path}: no strip marker -- already repaired?")
            continue
        head = subprocess.run(
            ["git", "show", f"HEAD:{path}"], capture_output=True, text=True,
            encoding="utf-8", errors="replace").stdout
        for marker in re.finditer(r'resource_local_to_scene = ""', text):
            block_start = text.rfind("[sub_resource", 0, marker.start())
            if block_start < 0:
                continue
            block_end = text.find("\n[", marker.start())
            if block_end < 0:
                block_end = len(text)
            block_id = re.search(r'id="([^"]+)"', text[block_start:block_start + 200])
            if not block_id:
                continue
            bid = block_id.group(1)
            head_block = re.search(
                r'\[sub_resource[^\n]*id="' + re.escape(bid) + r'"[^\n]*\]\n(.*?)(?=\n\[|\Z)',
                head, re.S)
            if not head_block:
                print(f"  {path}: HEAD has no sub_resource id={bid} -- manual repair required")
                continue
            head_binding = re.search(r'^script = ExtResource\("([^"]+)"\)', head_block.group(1), re.M)
            if not head_binding:
                print(f"  {path}: HEAD's sub_resource id={bid} has no script binding -- manual repair")
                continue
            ref_id = head_binding.group(1)
            head_ext = re.search(
                r'\[ext_resource[^\n]*id="' + re.escape(ref_id) + r'"[^\n]*\]', head)
            if not head_ext:
                print(f"  {path}: HEAD ext_resource id={ref_id} not found -- manual repair")
                continue
            ext_line = head_ext.group(0)
            ext_path = re.search(r'path="([^"]+)"', ext_line).group(1)
            existing = re.search(r'\[ext_resource[^\n]*path="' + re.escape(ext_path) + r'"[^\n]*\]', text)
            if existing:
                final_id = re.search(r'id="([^"]+)"', existing.group(0)).group(1)
            elif re.search(r'id="' + re.escape(ref_id) + r'"', text):
                print(f"  {path}: ext_resource id {ref_id} collides in worktree -- manual repair")
                continue
            else:
                insert_at = text.find("\n[ext_resource")
                text = text[:insert_at] + "\n" + ext_line + text[insert_at:]
                final_id = ref_id
            text = text.replace(
                'resource_local_to_scene = ""', f'script = ExtResource("{final_id}")', 1)
            print(f"  {path}: restored binding -> {ext_path} (id {final_id})")
        wf.write_text(text, encoding="utf-8")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    if "--editor-check" in args:
        editor_boot_check()
        if "--worktree" not in args:
            return 0
    if "--worktree" in args:
        return worktree_scan(revert="--revert" in args, repair="--repair-inplace" in args)
    range_arg = args[1] if len(args) > 1 and args[0] == "--range" else None
    return standalone(range_arg)


if __name__ == "__main__":
    sys.exit(main())
