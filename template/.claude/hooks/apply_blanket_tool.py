#!/usr/bin/env python3
"""
One-shot applier for the blanket-on-Resources [Tool] policy (Tool Attribute Audit, Phase 5).

Adds `[Tool]` to every {{PROJECT_NAME}} `[GlobalClass]` Resource that lacks it, by rewriting
the class's `[GlobalClass]` attribute line in place. Reuses tool_cascade_audit.py's parser
so the worklist and line-locations are identical to the audit.

Transformation (idempotent — skips classes that already have Tool):
  [GlobalClass]            -> [GlobalClass, Tool]
  [GlobalClass, X]         -> [GlobalClass, Tool, X]
  [X, GlobalClass]         -> [X, GlobalClass, Tool]

Usage:
  python3 apply_blanket_tool.py --dry-run   # report planned edits + anomalies, write nothing
  python3 apply_blanket_tool.py             # apply edits in place
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tool_cascade_audit import (  # noqa: E402
    parse_files, resolve_godot_kind, ATTR_LINE_RE, GLOBALCLASS_TOKEN_RE, TOOL_TOKEN_RE,
)

CLASS_LINE_RE = re.compile(r"^\s*(?:public|internal|private|protected|abstract|sealed|"
                           r"static|partial|new|file|\s)*\b(?:class|record)\s+(\w+)\b")


def find_repo_root():
    d = os.getcwd()
    while d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, "project.godot")):
            return d
        d = os.path.dirname(d)
    return os.getcwd()


def transform_globalclass_line(line):
    """Return the line with Tool added to its [GlobalClass] attribute, or None if it can't."""
    if not GLOBALCLASS_TOKEN_RE.search(line) or TOOL_TOKEN_RE.search(line):
        return None
    if "[GlobalClass]" in line:
        return line.replace("[GlobalClass]", "[GlobalClass, Tool]", 1)
    # Compound: [GlobalClass, X...] -> [GlobalClass, Tool, X...]
    m = re.search(r"\[\s*GlobalClass\s*,", line)
    if m:
        return line[:m.start()] + "[GlobalClass, Tool," + line[m.end():]
    # Compound: [X..., GlobalClass] -> [X..., GlobalClass, Tool]
    m = re.search(r",\s*GlobalClass\s*\]", line)
    if m:
        return line[:m.start()] + ", GlobalClass, Tool]" + line[m.end():]
    return None


def locate_globalclass_line(lines, decl_line_idx):
    """Scan upward from a class declaration through its attribute block; return the index of
    the [GlobalClass]-bearing attribute line (that lacks Tool), or None."""
    i = decl_line_idx - 1
    while i >= 0:
        ln = lines[i]
        stripped = ln.strip()
        if ATTR_LINE_RE.match(ln):
            if GLOBALCLASS_TOKEN_RE.search(ln) and not TOOL_TOKEN_RE.search(ln):
                return i
            i -= 1
        elif stripped == "" or stripped.startswith("//") or stripped.startswith("/*") \
                or stripped.startswith("*"):
            i -= 1
        else:
            break
    return None


def main():
    dry_run = "--dry-run" in sys.argv
    root = find_repo_root()
    types, files = parse_files(root)
    for nm, e in types.items():
        e["is_attr"] = (nm.endswith("Attribute") and nm != "Attribute"
                        and "Attribute" in e["bases"])
    kind_memo = {}
    for name in list(types):
        resolve_godot_kind(name, types, kind_memo)

    worklist = sorted(
        n for n, e in types.items()
        if e["module"] == "{{PROJECT_NAME}}" and e["has_globalclass"]
        and not e["has_tool"] and not e.get("is_attr")
        and kind_memo.get(n) == "Resource")

    planned = []     # (rel, lineno_1based, name, old, new)
    anomalies = []   # (name, reason)
    # Group edits by file so multiple target classes in one file are written together.
    edits_by_file = {}

    for name in worklist:
        e = types[name]
        located = None
        for (rel, line_idx) in e["decl_lines"]:
            abspath = os.path.join(root, rel)
            try:
                with open(abspath, "r", encoding="utf-8") as f:
                    flines = f.read().split("\n")
            except OSError:
                continue
            # Confirm the decl at line_idx is this class (parser line_idx is 0-based).
            if line_idx < len(flines) and CLASS_LINE_RE.match(flines[line_idx]) \
                    and re.search(rf"\b(class|record)\s+{re.escape(name)}\b", flines[line_idx]):
                gc_idx = locate_globalclass_line(flines, line_idx)
                if gc_idx is not None:
                    located = (rel, abspath, flines, gc_idx)
                    break
        if located is None:
            anomalies.append((name, "no [GlobalClass]-without-Tool line found above decl"))
            continue
        rel, abspath, flines, gc_idx = located
        old = flines[gc_idx]
        new = transform_globalclass_line(old)
        if new is None:
            anomalies.append((name, f"could not transform line: {old.strip()!r}"))
            continue
        planned.append((rel, gc_idx + 1, name, old.strip(), new.strip()))
        edits_by_file.setdefault(abspath, {"lines": flines, "edits": []})
        edits_by_file[abspath]["edits"].append((gc_idx, new))

    print(f"Worklist (PP [GlobalClass] Resources lacking [Tool]): {len(worklist)}")
    print(f"Planned edits: {len(planned)}   Anomalies: {len(anomalies)}\n")
    for rel, lineno, name, old, new in planned:
        print(f"  {rel}:{lineno}  [{name}]  {old}  ->  {new}")
    if anomalies:
        print("\nANOMALIES (need manual handling):")
        for name, reason in anomalies:
            print(f"  {name}: {reason}")

    if dry_run:
        print("\n[dry-run] no files written.")
        return 0

    written = 0
    for abspath, payload in edits_by_file.items():
        flines = payload["lines"]
        for (idx, new) in payload["edits"]:
            flines[idx] = new
        with open(abspath, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(flines))
        written += 1
    print(f"\nApplied edits to {written} files ({len(planned)} classes).")
    if anomalies:
        print(f"WARNING: {len(anomalies)} anomalies were NOT edited — handle manually.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
