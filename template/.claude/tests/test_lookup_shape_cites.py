#!/usr/bin/env python3
"""The anchors the lookup-shape cites name resolve, and the close binds this proof to the docs it reads.

Cases assert anchors, never prose, so the owner text can be reworded freely.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def text(rel):
    return (CLAUDE / rel).read_text(encoding="utf-8")


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
