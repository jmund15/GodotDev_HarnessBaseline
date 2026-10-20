#!/usr/bin/env python3
"""Proof for tools/lens_briefs.py: seat keys resolve against every shipped registry.

A registry whose file a layer-stripped consumer never received is skipped, so an unknown key gets the
`no such lens` refusal instead of a missing-registry exit.

    python3 .claude/tests/test_lens_briefs.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import lens  # noqa: E402
import lens_briefs  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def run_main(argv):
    saved_argv = sys.argv
    sys.argv = ["lens_briefs.py"] + argv
    try:
        lens_briefs.main()
        return "returned"
    except SystemExit as exc:
        return str(exc.code)
    finally:
        sys.argv = saved_argv


def main():
    text = "# Review agents\n\n## Agent Templates\n\n### code-reviewer -- `model: \"opus\"`\nbody\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "review_agents.md").write_text(text, encoding="utf-8")
        (root / "context.md").write_text("context\n", encoding="utf-8")
        saved = lens.AGENTS, lens.REGISTRIES
        lens.AGENTS = root
        lens.REGISTRIES = {"review": ("review_agents.md", ""), "structure_audit": ("structure_audit_agents.md", "stra-")}
        try:
            outcome = run_main(["--keys", "no-such-key", "--out", str(root / "out"), "--prefix", "p",
                                "--context-path", str(root / "context.md"), "--model", sorted(lens_briefs.NATIVE_PINS)[0], "--effort",
                                sorted(lens_briefs.NATIVE_EFFORTS)[0]])
        finally:
            lens.AGENTS, lens.REGISTRIES = saved
    check("an unknown key is refused by name while another registry's file is absent",
          "no such lens: no-such-key" in outcome, outcome)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
