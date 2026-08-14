#!/usr/bin/env python3
"""Generate the committed, semantic-searchable index of the Godot class reference.

The bulk cache (`.claude/cache/godot-docs/`) is gitignored `.xml` — invisible to
semantic-search on both counts (CLAUDE.md §8: `.xml` is not an indexed extension, and
gitignored paths are excluded). This index is the committed `.md` counterpart that
restores discovery ("which class handles navigation baking?"); precision lookup
("what does Node.reparent do?") reads the XML directly.

Brief descriptions only — including member/method name lists measured 415 KB versus
81 KB, which is too much churn for a file regenerated on every engine bump.

Usage: gen_godot_class_index.py <classes-dir> <out-md> <engine-version>
"""
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

# Godot BBCode-ish markup that would otherwise leak into the index prose.
_TAG = re.compile(r"\[/?(?:b|i|code|codeblock|url|param|member|method|constant|enum|"
                  r"signal|theme_item|annotation|class)[^\]]*\]")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", text or "")).strip()


def main() -> int:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    classes_dir, out_md, version = sys.argv[1], sys.argv[2], sys.argv[3]

    paths = sorted(glob.glob(os.path.join(classes_dir, "*.xml")))
    if not paths:
        print(f"ERROR: no .xml under {classes_dir}", file=sys.stderr)
        return 1

    entries, failed = [], []
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            failed.append(f"{os.path.basename(path)}: {exc}")
            continue
        name = root.get("name", "")
        if not name:
            failed.append(f"{os.path.basename(path)}: no name attribute")
            continue
        entries.append((name, root.get("inherits", ""), clean(root.findtext("brief_description"))))

    # A partial index is worse than none: it reads as authoritative while missing classes.
    if failed:
        print(f"ERROR: {len(failed)} class file(s) unparseable; refusing to write a partial index:",
              file=sys.stderr)
        for line in failed[:10]:
            print(f"  {line}", file=sys.stderr)
        return 1

    out = [
        f"# Godot Class Index — {version}",
        "",
        f"Generated from the class-reference cache at engine **{version}**. "
        f"{len(entries)} classes.",
        "",
        "Discovery layer only — brief descriptions, no member lists. For the verbatim "
        "signature, parameters, or description of any member, read "
        "`.claude/cache/godot-docs/doc/classes/<Class>.xml` (gitignored; populate with "
        "`.claude/scripts/godot_docs_cache.sh`). That XML is the source the official HTML "
        "class reference is generated from, so it is first-party P1 evidence.",
        "",
        "Do not hand-edit — regenerated on every engine bump.",
        "",
    ]
    for name, inherits, brief in entries:
        out.append(f"## {name}")
        if inherits:
            out.append(f"Inherits: {inherits}")
        out.append(brief or "_(no brief description)_")
        out.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out_md)), exist_ok=True)
    with open(out_md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))

    print(f"wrote {out_md}  ({len(entries)} classes, {os.path.getsize(out_md) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
