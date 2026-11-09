#!/usr/bin/env python3
"""Lookup shape has one owner: `CLAUDE.core.md` §9 states the breadth route, and every other surface cites it.

Cases assert cites and anchors, never prose, so the owner text can be reworded freely:
- the breadth-route sentence (anchor: BREADTH) appears in `CLAUDE.core.md` and in no other doctrine file;
- `/delegate` ABORT, `/explore` §1a and the orchestration §0 litmus cite
  `CLAUDE.core.md` §9 on that line;
- both tiers of `guards/survey.godot.md` cite `CLAUDE.coding.md` §Semantic Search MCP for names in Godot text data,
  and no longer cite `CLAUDE.godot.md` for that rule;
- every cited anchor resolves to a heading in its file.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
BREADTH = "scout survey, even when no system is named"
CORE_CITE = "`CLAUDE.core.md` §9"
CODING_CITE = "`CLAUDE.coding.md` §Semantic Search MCP"
# Each citer's decision line, found by its opening words: the cite must sit on that line, not elsewhere in the file.
CITERS = [("commands/delegate.md", "- `ABORT`:"), ("commands/explore.md", "Name the capability"),
          ("skills/orchestration/SKILL.md", "Litmus")]
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def text(rel):
    return (CLAUDE / rel).read_text(encoding="utf-8")


def doctrine_files():
    # A scratch install leaves some doctrine files unstaged, so the listing takes untracked, unignored files too.
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "--",
                          ".claude/*.md", ".claude/**/*.md"],
                         capture_output=True, text=True, check=True).stdout.split()
    skip = (".claude/plans/", ".claude/scratch/", ".claude/auto-memory/archive/", ".claude/tests/")
    return [p for p in out if not p.startswith(skip)]


def section(md, tier):
    found = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % tier, md, re.S | re.M)
    return found.group(1) if found else ""


@case
def breadth_route_has_one_owner():
    holders = [p for p in doctrine_files() if BREADTH in (ROOT / p).read_text(encoding="utf-8")]
    assert holders == [".claude/CLAUDE.core.md"], holders


@case
def every_surface_cites_core_section_9():
    missing = []
    for rel, opening in CITERS:
        lines = [line for line in text(rel).splitlines() if line.startswith(opening)]
        if len(lines) != 1 or CORE_CITE not in lines[0]:
            missing.append((rel, opening, len(lines)))
    assert not missing, missing


@case
def survey_godot_tiers_cite_coding_semantic_search():
    md = text("guards/survey.godot.md")
    for tier in ("detailed", "condensed"):
        body = section(md, tier)
        assert CODING_CITE in body, (tier, body)
    pascal = [line for line in md.splitlines() if ".tres" in line and "CLAUDE.godot.md" in line]
    assert not pascal, pascal


@case
def cited_anchors_resolve():
    assert re.search(r"^### 9\. ", text("CLAUDE.core.md"), re.M), "CLAUDE.core.md has no §9 heading"
    assert re.search(r"^## Semantic Search MCP\b", text("CLAUDE.coding.md"), re.M), "CLAUDE.coding.md has no §Semantic Search MCP"


@case
def the_close_selects_this_proof_for_the_docs_it_reads():
    sys.path.insert(0, str(CLAUDE / "scripts"))
    import harness_tests
    docs = [".claude/CLAUDE.core.md", ".claude/CLAUDE.coding.md"]
    bound = harness_tests.bindings(str(ROOT), docs)
    missing = [d for d in docs if not any(Path(p).name == Path(__file__).name for p in bound[d])]
    assert not missing, missing


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        else:
            print(f"ok   {fn.__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
