#!/usr/bin/env python3
"""Proof for tools/lens_briefs.py: seat keys resolve against a registry set, and an unknown key is refused.

A registry whose file a layer-stripped consumer never received is skipped, so an unknown key gets the
`no such lens` refusal instead of a missing-registry exit.

    python3 .claude/tests/test_lens_briefs.py
"""
import json
import sys
import tempfile
import types
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
        try:
            absent = lens_briefs.with_companion(cq, lambda f: lens_briefs.sections(f, {"C"}))
        except OSError as exc:
            absent = repr(exc)
        check("a layer that ships no checklist renders it empty", absent == "", absent)
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


def job_tier_checks():
    """A job's `tier` is its lens tier; a written `--tier`/`--tier-for` only fills a seat with no lens tier."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        context = root / "context.md"
        context.write_text("context\n", encoding="utf-8")
        must = root / "must.txt"
        must.write_text(str(context) + "\n", encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({"roster": [{"key": "seat-a"}, {"key": "seat-b"}]}), encoding="utf-8")
        found = {key: types.SimpleNamespace(registry="review", lines=["```\n", "prompt\n", "```\n"], kfm=False, citers=False)
                 for key in ("seat-a", "seat-b")}
        saved = (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal, lens_briefs.seat_overage)
        lens.catalog = lambda: ({"review": "preamble"}, found)
        lens.doctrine = lambda lenses: ""
        lens.seat_tier = lambda key: "executor" if key == "seat-a" else None
        lens_briefs.lens_tier_refusal = lambda **kwargs: []
        lens_briefs.seat_overage = lambda **kwargs: None
        try:
            outcome = run_main(["--plan", str(plan), "--out", str(root / "out"), "--prefix", "p",
                                "--context-path", str(context), "--model", sorted(lens_briefs.NATIVE_PINS)[0],
                                "--effort", sorted(lens_briefs.NATIVE_EFFORTS)[0], "--tier", "fanout",
                                "--tier-for", "seat-a=scout", "--must-read", str(must)])
        finally:
            (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal,
             lens_briefs.seat_overage) = saved
        jobs_file = root / "out" / "jobs_p.json"
        tiers = {job["seat"]: job["tier"] for job in json.loads(jobs_file.read_text(encoding="utf-8"))} \
            if jobs_file.exists() else {}
    check("the job tier run returns without a refusal", outcome == "returned", str(outcome))
    check("a seat's job tier is its lens tier, not the --tier-for written over it",
          tiers.get("seat-a") == "executor", repr(tiers))
    check("a seat with no lens tier takes the written --tier",
          tiers.get("seat-b") == "fanout", repr(tiers))


def bar_checks():
    """A sidecar seat's brief carries the run's bar (the sidecar prompt has no engine contract); a native seat's
    brief never does, since the engine appends it to the native seat's contract."""
    import os
    saved_env = os.environ.get("HARNESS_TASK_RECORD_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        records = root / "tasks"
        records.mkdir()
        (records / "T1.json").write_text(json.dumps({"id": "T1", "updated": "2026-10-26T10:00:00", "rigor": "light",
                                                     "session_id": "sess0001", "requirements": ["ships"],
                                                     "exclusions": []}), encoding="utf-8")
        os.environ["HARNESS_TASK_RECORD_DIR"] = str(records)
        saved_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess0001"
        context = root / "context.md"
        context.write_text("context\n", encoding="utf-8")
        must = root / "must.txt"
        must.write_text(str(context) + "\n", encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({"roster": [{"key": "seat-a"}]}), encoding="utf-8")
        found = {"seat-a": types.SimpleNamespace(registry="review", lines=["```\n", "prompt\n", "```\n"], kfm=False,
                                                 citers=False)}
        saved = (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal,
                 lens_briefs.seat_overage, lens_briefs.resolve_sidecar_entry, lens_briefs.effort_rungs)
        lens.catalog = lambda: ({"review": "preamble"}, found)
        lens.doctrine = lambda lenses: ""
        lens.seat_tier = lambda key: None
        lens_briefs.lens_tier_refusal = lambda **kwargs: []
        lens_briefs.seat_overage = lambda **kwargs: None
        lens_briefs.resolve_sidecar_entry = lambda token, what, data: {"alias": token, "transport": "off"}
        lens_briefs.effort_rungs = lambda entry, data: ["medium"]

        def briefs(target, out, task="T1"):
            argv = ["--plan", str(plan), "--out", str(out), "--prefix", "p", "--target", target,
                    "--context-path", str(context), "--model", sorted(lens_briefs.NATIVE_PINS)[0],
                    "--effort", "medium", "--must-read", str(must)]
            if task:
                argv += ["--task", task]
            outcome = run_main(argv)
            text = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.md")) if out.exists() else ""
            return outcome, text
        try:
            sidecar_outcome, sidecar_text = briefs("sidecar", root / "out_sidecar")
            native_outcome, native_text = briefs("native", root / "out_native")
            taskless_outcome, taskless_text = briefs("sidecar", root / "out_taskless", task=None)
        finally:
            (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal,
             lens_briefs.seat_overage, lens_briefs.resolve_sidecar_entry, lens_briefs.effort_rungs) = saved
            if saved_env is None:
                os.environ.pop("HARNESS_TASK_RECORD_DIR", None)
            else:
                os.environ["HARNESS_TASK_RECORD_DIR"] = saved_env
            if saved_session is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = saved_session
    check("the sidecar brief run returns without a refusal", sidecar_outcome == "returned", str(sidecar_outcome))
    check("a sidecar seat brief carries the bar text of the task's rigor",
          lens_briefs_bar("light") in sidecar_text, "no BAR paragraph in the sidecar brief")
    check("a native seat brief never carries the bar text",
          "BAR: this run" not in native_text, "the native brief repeats the engine contract's bar")
    check("a sidecar seat brief with no --task still carries the session's bar text",
          taskless_outcome == "returned" and lens_briefs_bar("light") in taskless_text,
          "no BAR paragraph in the task-less sidecar brief")


def merged_seat_checks():
    """A merged seat's brief sends each mandate's outcome to `checked.mandates` under the catalog keys the
    engines score, never to `checked.basis` alone: a seat that wrote outcomes only in its basis covered nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        context = root / "context.md"
        context.write_text("context\n", encoding="utf-8")
        must = root / "must.txt"
        must.write_text(str(context) + "\n", encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({"roster": [{"key": "seat-a+seat-b"}]}), encoding="utf-8")
        body = ["```\n", "prompt\n", "```\n"]
        found = {k: types.SimpleNamespace(registry="review", lines=body, kfm=False, citers=False)
                 for k in ("seat-a", "seat-b")}
        saved = (lens.catalog, lens.doctrine, lens.seat_tier, lens.seat_mandates, lens_briefs.lens_tier_refusal,
                 lens_briefs.seat_overage)
        lens.catalog = lambda: ({"review": "preamble"}, found)
        lens.doctrine = lambda lenses: ""
        lens.seat_tier = lambda key: None
        lens.seat_mandates = lambda key: ["m.A", "m.B", "m.C"] if key == "seat-a+seat-b" else None
        lens_briefs.lens_tier_refusal = lambda **kwargs: []
        lens_briefs.seat_overage = lambda **kwargs: None
        try:
            out = root / "out"
            outcome = run_main(["--plan", str(plan), "--out", str(out), "--prefix", "p", "--context-path",
                                str(context), "--model", sorted(lens_briefs.NATIVE_PINS)[0], "--effort", "medium",
                                "--must-read", str(must)])
            text = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.md")) if out.exists() else ""
        finally:
            (lens.catalog, lens.doctrine, lens.seat_tier, lens.seat_mandates, lens_briefs.lens_tier_refusal,
             lens_briefs.seat_overage) = saved
    check("the merged-seat brief run returns without a refusal", outcome == "returned", str(outcome))
    check("a merged seat's brief names checked.mandates with the catalog keys the engines score",
          "`checked.mandates`" in text and "`m.A`, `m.B`, `m.C`" in text, text[:400])
    check("a merged seat's brief never routes outcomes to checked.basis alone",
          "In `checked.basis`, write one" not in text, "the brief still sends outcomes to the basis")


def lens_briefs_bar(rigor):
    import finding_bar
    return finding_bar.bar_contract(rigor)


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
    job_tier_checks()
    merged_seat_checks()
    bar_checks()
    import contextlib
    import io
    saved_tier = lens.seat_tier
    lens.seat_tier = lambda key: "executor" if key == "seat-a" else None
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            refusal = lens_briefs.lens_tier_refusal(panel={"seat-a": "no-such-row"}, native=False,
                                                    data=lens_briefs.model_registry.load())
    finally:
        lens.seat_tier = saved_tier
    check("a seat whose pin does not resolve is named on stderr with its reason, not dropped silently",
          "seat-a" in stderr.getvalue() and "no-such-row" in stderr.getvalue() and refusal == [],
          stderr.getvalue() + repr(refusal))
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
