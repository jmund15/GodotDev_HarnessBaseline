#!/usr/bin/env python3
"""Proof for tools/lens.py: a registry's lenses are the `###` headings under its templates section.

A registry with an empty key prefix must not turn preamble subsections or a lens's own `###`
subsections into lenses.

    python3 .claude/tests/test_lens.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import lens  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def main():
    registry = "review"
    filename, prefix = lens.REGISTRIES[registry]
    text = (
        "# Review agents\n\n## Shared Scoping Rules\n\n### Large PR Handling (>300 files)\nsplit it\n\n"
        "### Batch Sizing Rules\nsize it\n\n## Agent Templates\n\n"
        f"### {prefix}code-reviewer (aspect: `code`) -- `model: \"opus\"`\n\n## Your Checklist\n- a\n\n"
        f"### {prefix}pool-lifecycle (aspect: `pool`) -- `model: \"opus\"`\n\n## Your Checklist\n\n"
        "### Reset Ordering\n- b\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / filename).write_text(text, encoding="utf-8")
        saved = lens.AGENTS
        lens.AGENTS = Path(tmp)
        try:
            preamble, lenses = lens.parse(registry)
        finally:
            lens.AGENTS = saved
    keys = [item.key for item in lenses]
    check("only the template headings are lenses", keys == [f"{prefix}code-reviewer", f"{prefix}pool-lifecycle"], keys)
    check("the preamble keeps the shared sections", "Batch Sizing Rules" in preamble, preamble[-80:])
    check("a lens keeps its own subsections", "Reset Ordering" in lenses[-1].body() if lenses else False)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
