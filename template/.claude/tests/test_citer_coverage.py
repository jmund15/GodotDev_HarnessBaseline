#!/usr/bin/env python3
"""Proof for tools/citer_coverage.py: the `citers: plan` lens keys come from every shipped registry.

A registry whose file a layer-stripped consumer never received is skipped, not fatal.

    python3 .claude/tests/test_citer_coverage.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import citer_coverage  # noqa: E402
import lens  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def main():
    text = ("# Review agents\n\n## Agent Templates\n\n### citing-lens -- `model: \"opus\"`\nciters: plan\nbody\n\n"
            "### plain-lens -- `model: \"opus\"`\nbody\n")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "review_agents.md").write_text(text, encoding="utf-8")
        saved = lens.AGENTS, lens.REGISTRIES
        lens.AGENTS = Path(tmp)
        lens.REGISTRIES = {"review": ("review_agents.md", ""), "structure_audit": ("structure_audit_agents.md", "stra-")}
        citer_coverage.citer_keys.cache_clear()
        try:
            keys = citer_coverage.citer_keys()
        except SystemExit as exc:
            keys = f"SystemExit: {exc.code}"
        finally:
            lens.AGENTS, lens.REGISTRIES = saved
            citer_coverage.citer_keys.cache_clear()
    check("citer keys resolve while another registry's file is absent", keys == {"citing-lens"}, keys)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
