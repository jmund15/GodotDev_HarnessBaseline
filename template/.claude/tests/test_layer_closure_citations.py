#!/usr/bin/env python3
"""Proof for tools/layer_closure.py: a "`<skill>` reference `<file>.md`" citation resolves to the skill's
reference dir, and a citation to a file the shipped tree lacks is `missing`.

    python3 .claude/tests/test_layer_closure_citations.py
"""
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import layer_closure as lc  # noqa: E402

FAILURES = []
OWNER = ".claude/skills/orchestration/SKILL.md"
OTHER = ".claude/skills/other/SKILL.md"
TARGET = ".claude/skills/orchestration/reference/spend_lean.md"


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail) if detail else ""))


def planted(files, layers):
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            path = Path(root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return [f.render() for f in lc.scan(Path(root), layers)]


def main():
    cite = {OTHER: "See `orchestration` reference `spend_lean.md`.\n"}
    layers = {OTHER: "pure"}
    out = planted(cite, layers)
    check("shorthand citation to an unshipped file is missing", out == [f"{OTHER}: missing {TARGET}"], out)

    out = planted({**cite, TARGET: "x\n"}, {**layers, TARGET: "pure"})
    check("shorthand citation to a shipped file has no finding", out == [], out)

    out = planted({**cite, TARGET: "x\n"}, {**layers, TARGET: "coding"})
    check("shorthand citation to a higher-layer file is needs-coding", out == [f"{OTHER}: needs-coding {TARGET}"], out)

    out = planted({OWNER: "Read reference `spend_lean.md`.\n"}, {OWNER: "pure"})
    check("bare 'reference `f.md`' inside a skill resolves to that skill's reference dir",
          out == [f"{OWNER}: missing {TARGET}"], out)

    out = planted({OWNER: "Read reference `spend_lean.md`.\n", TARGET: "x\n"}, {OWNER: "pure", TARGET: "pure"})
    check("bare form to a shipped file has no finding", out == [], out)

    out = planted({".claude/rules/r.md": "Read reference `spend_lean.md`.\n"}, {".claude/rules/r.md": "pure"})
    check("bare form outside a skill keeps the old no-finding verdict", out == [], out)

    out = planted({OTHER: "See `spend_lean.md`.\n"}, {OTHER: "pure"})
    check("slash-free token alone keeps the old no-finding verdict", out == [], out)

    out = planted({OTHER: "See [x](reference/gone.md).\n"}, {OTHER: "pure"})
    check("explicit relative link to a missing file is still missing",
          out == [f"{OTHER}: missing .claude/skills/other/reference/gone.md"], out)

    if FAILURES:
        print("\nFAILED:\n  " + "\n  ".join(FAILURES))
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
