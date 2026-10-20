#!/usr/bin/env python3
"""Proof for tools/sentence_diff.py: the sentence comparison the harness-growth hook and citers.py share.

    python3 .claude/tests/test_sentence_diff.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def main():
    import sentence_diff as sd

    text = "# Title\n\n- Never delete a rule to fit a size threshold. Re-home it instead.\n```\ncode line is not prose here\n```\n|---|---|\nshort one\n"
    found = sd.sentences(text)
    check("sentences drops headings, fences, table rules and short fragments",
          found == ["Never delete a rule to fit a size threshold.", "Re-home it instead."], found)

    before = "Alpha rule stays exactly as written here. Beta rule is about caching old results. Gamma rule moves elsewhere entirely."
    now = "Alpha rule stays exactly as written here. A caching rule covers old results now."
    gone = sd.removed(before, now)
    check("removed pairs each missing sentence with whether an added sentence rewords it",
          gone == [("Beta rule is about caching old results.", True),
                   ("Gamma rule moves elsewhere entirely.", False)], gone)
    check("an identical text removes nothing", sd.removed(before, before) == [])

    corpus = [("a.md", "x Gamma rule moves elsewhere entirely. y"), ("self.md", "Gamma rule moves elsewhere entirely.")]
    homes = sd.homes(["Gamma rule moves elsewhere entirely."], corpus, "self.md")
    check("a removed sentence's home is another file carrying its fragment, never the file itself",
          homes == {"Gamma rule moves elsewhere entirely.": "a.md"}, homes)

    check("a doctrine-scope path is a .claude markdown file outside EXCLUDED",
          sd.in_doctrine_scope(".claude/rules/a.md") and not sd.in_doctrine_scope(".claude/plans/a.md")
          and not sd.in_doctrine_scope(".claude/auto-memory/archive/a.md") and not sd.in_doctrine_scope(".claude/tools/a.py")
          and not sd.in_doctrine_scope("docs/a.md"))

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
