#!/usr/bin/env python3
"""Authoring-time guard: a written .tres must match the editor's canonical serialization.

A .tres that differs from the editor's serializer -- header missing script_class=,
ext_resource lines without the target's uid= -- is marked dirty at EVERY editor load;
the next save (including F5 play's save-before-run) rewrites it, and a rewrite landing
in the editor's C# rebuild window serializes scripted sub-resources stripped
(resource_local_to_scene = "" -> bare-Resource loads -> InvalidCastException). This is
the 4x-recurring encounter corruption; the fixed-point form is mandatory for NEW files
(godot_files.md §UID handling). Inform-only -- the commit-time strip/nullstrip guards
remain the hard gate.

Checks (a written .tres, PostToolUse Write|Edit):
  1. header carries script_class=<root script class> when a root Script ext_resource exists
  2. every Script ext_resource carries uid= when <target>.cs.uid exists (Jmodot excluded --
     foreign uid cache, path-only is correct there)
  3. every Resource/PackedScene ext_resource carries uid= when the target's HEADER line has one

Modes:
    tres_format_guard.py --hook  # PostToolUse(Write|Edit): validate the written file; exit 0
"""
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

REPO = pathlib.Path(__file__).resolve().parents[1]
SUBMODULE = "Jmodot/"


def target_uid(rel_path: str) -> str | None:
    """The target's own uid: .cs -> .cs.uid companion; .tres/.tscn -> HEADER line only.

    Only the header line carries the file's own uid; an ext_resource line further down is
    the root SCRIPT's uid, not the file's (backfilling that breaks resolution).
    """
    p = REPO / rel_path
    if rel_path.endswith(".cs"):
        comp = p.with_suffix(".cs.uid")
        if comp.exists():
            return comp.read_text(encoding="utf-8", errors="replace").strip()
        return None
    if p.exists() and rel_path.endswith((".tres", ".tscn")):
        head = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        m = re.search(r'uid="(uid://[^"]+)"', head)
        return m.group(1) if m else None
    return None


def check_file(path_str: str) -> list[str]:
    p = pathlib.Path(path_str)
    if p.suffix not in (".tres", ".tscn") or not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    issues: list[str] = []
    header = text.split("\n", 1)[0]

    # 1. script_class
    if 'script_class="' not in header:
        # Godot writes type uid path id (in that order) -- id must come AFTER path.
        root = re.search(r'\[ext_resource[^\n]*type="Script"[^\n]*path="res://([^"]+\.cs)"[^\n]*id="1_[^"]*"', text)
        if root:
            cls = pathlib.Path(root.group(1)).stem
            issues.append(f"header lacks script_class=\"{cls}\" (root script {root.group(1)})")

    # 2+3. ext_resource uids
    for m in re.finditer(r'\[ext_resource type="([^"]+)"(?: uid="([^"]+)")?[^\n]*path="res://([^"]+)"[^\n]*\]', text):
        if m.group(2):
            continue
        tgt = m.group(3)
        if tgt.startswith(SUBMODULE):
            continue  # foreign uid cache; path-only is correct (measured 2026-08-13)
        t_uid = target_uid(tgt)
        if t_uid:
            issues.append(f"ext_resource to {tgt} missing uid= (target carries {t_uid})")

    return issues


def hook():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0
    path = (data.get("tool_input", {}) or {}).get("file_path", "") or ""
    if not path.endswith((".tres", ".tscn")):
        print("{}")
        return 0
    issues = check_file(path)
    if not issues:
        print("{}")
        return 0
    rel = path.replace("\\", "/")
    lines = [
        f"[tres-format-guard] {rel} is not in the editor's canonical serialization "
        "(godot_files.md §UID handling) -- the editor will mark it dirty at load and rewrite "
        "it on the next save, the corruption window that stripped encounter .tres 4x:",
        *[f"  - {i}" for i in issues],
        "Fix now: add the script_class/uid= attributes (target uids from .cs.uid companions or "
        "target headers; omit load_steps).",
    ]
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "\n".join(lines),
    }}))
    return 0


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
