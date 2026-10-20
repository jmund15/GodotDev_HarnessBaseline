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


def companion_checks():
    base = "---\nd: x\n---\n# Q\n\n## Compliance (C)\n\n- [ ] **a**\n\n## Robustness (R)\n\n- [ ] **r**\n"
    godot = "---\nx: y\n---\n# G\n\n## Godot Compliance (C)\n\n- [ ] **g**\n\n## Godot Intuitiveness (I)\n\n- [ ] **i**\n"
    with tempfile.TemporaryDirectory() as tmp:
        cq = Path(tmp) / "code_quality.md"
        cq.write_text(base, encoding="utf-8")
        alone = lens_briefs.with_companion(cq, lambda f: lens_briefs.sections(f, {"C"}))
        check("without a companion a letter yields the base sections only",
              "**a**" in alone and "Godot" not in alone, alone)
        (Path(tmp) / "code_quality_godot.md").write_text(godot, encoding="utf-8")
        merged = lens_briefs.with_companion(cq, lambda f: lens_briefs.sections(f, {"C"}))
        check("with a companion a letter adds that companion's sections after the base",
              merged.index("**a**") < merged.index("**g**") and "**i**" not in merged, merged)
        only_godot = lens_briefs.with_companion(cq, lambda f: lens_briefs.sections(f, {"I"}))
        check("a letter only the companion holds yields the companion's section alone",
              "**i**" in only_godot and "**a**" not in only_godot, only_godot)
        whole = lens_briefs.with_companion(cq, lambda f: lens_briefs.below_frontmatter(f.read_text(encoding="utf-8")))
        check("the whole-file form joins both files below their frontmatter",
              "**a**" in whole and "**g**" in whole and "x: y" not in whole and "d: x" not in whole, whole)
        tq = Path(tmp) / "test_quality.md"
        tq.write_text("---\nd: x\n---\n# T\n\n## Quality\n\n- [ ] **t**\n", encoding="utf-8")
        check("the test-quality brief is the base alone without a companion",
              "**t**" in lens_briefs.checklist_for_tests(tq) and "**tg**" not in lens_briefs.checklist_for_tests(tq))
        (Path(tmp) / "test_quality_godot.md").write_text("---\nx: y\n---\n# TG\n\n## Godot Quality\n\n- [ ] **tg**\n", encoding="utf-8")
        merged_tq = lens_briefs.checklist_for_tests(tq)
        check("the test-quality brief adds its godot companion after the base",
              merged_tq.index("**t**") < merged_tq.index("**tg**") and "x: y" not in merged_tq, merged_tq)


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
    companion_checks()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
