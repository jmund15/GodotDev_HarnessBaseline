#!/usr/bin/env python3
"""Proof for tools/lens_briefs.py: seat keys resolve against a registry set, and an unknown key is refused.

A registry whose file a layer-stripped consumer never received is skipped, so an unknown key gets the
`no such lens` refusal instead of a missing-registry exit.

    python3 .claude/tests/test_lens_briefs.py
"""
import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _planted_checkout import enter_planted_checkout  # noqa: E402

PLANTED = enter_planted_checkout()  # the rigor reader asks git about the working directory
import lens  # noqa: E402
import lens_briefs  # noqa: E402


# The two reads that ask git about the tool's own checkout root (the rigor, the base sizes) run the real code
# against the planted repository; every other read of REPO (config, templates) keeps the real root.
_real_sizes, _real_rigor = lens_briefs._base_sizes, lens_briefs.finding_bar.run_rigor


def _planted(root):
    return PLANTED if root == lens_briefs.REPO else root


lens_briefs._base_sizes = lambda root, base, files: _real_sizes(_planted(root), base, files)
lens_briefs.finding_bar.run_rigor = lambda task, root, *rest, **kw: _real_rigor(task, _planted(root), *rest, **kw)

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
        found = {key: types.SimpleNamespace(registry="review", lines=["```\n", "prompt\n", "```\n"], kfm=False, citers=False, fixes="")
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


def packet_view_checks():
    """A frozen session-audit packet's per-mandate views are a seat's must-read list, each artifact once."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        one, two = root / "one.diff", root / "two.diff"
        one.write_text("diff one\n", encoding="utf-8")
        two.write_text("diff two\n", encoding="utf-8")
        packet = root / "inputs.json"
        packet.write_text(json.dumps({"views": {
            "harness-quality": {"mustRead": [str(one)]},
            "cq.C": {"mustRead": [str(one), str(two)]}}}), encoding="utf-8")
        views = lens_briefs.packet_views(str(packet))
        check("a packet's views are keyed by mandate", sorted(views) == ["cq.C", "harness-quality"], repr(views))
        entries = lens_briefs.packet_entries(views, ["harness-quality", "cq.C"])
        check("a seat's mandates are unioned with each artifact listed once",
              [entry["path"] for entry in entries] == [str(one), str(two)]
              and entries[0]["lines"] == 1, repr(entries))
        packet.write_text(json.dumps({"views": {"harness-quality": {"mustRead": [str(root / "gone.diff")]}}}),
                          encoding="utf-8")
        try:
            lens_briefs.packet_views(str(packet))
            refusal = None
        except SystemExit as exc:
            refusal = str(exc.code)
        check("a view naming an artifact that is gone is refused, naming it",
              refusal is not None and "gone.diff" in refusal, repr(refusal))
        packet.write_text(json.dumps({"mustRead": [str(one)]}), encoding="utf-8")
        try:
            lens_briefs.packet_views(str(packet))
            refusal = None
        except SystemExit as exc:
            refusal = str(exc.code)
        check("a packet with no typed views is refused", refusal is not None and "views" in refusal, repr(refusal))


def packet_seat_checks():
    """--must-read-from takes each seat's list from the packet's view of its mandates, with no list file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        context = root / "context.md"
        context.write_text("context\n", encoding="utf-8")
        artifact = root / "view.diff"
        artifact.write_text("evidence\n", encoding="utf-8")
        packet = root / "inputs.json"
        packet.write_text(json.dumps({"views": {"harness-quality": {"mustRead": [artifact.as_posix()]}}}),
                          encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({"roster": [{"key": "seat-a"}]}), encoding="utf-8")
        found = {"seat-a": types.SimpleNamespace(registry="review", lines=["```\n", "prompt\n", "```\n"],
                                                 kfm=False, citers=False, fixes="", mandates=["harness-quality"])}
        saved = (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal, lens_briefs.seat_overage)
        lens.catalog = lambda: ({"review": "preamble"}, found)
        lens.doctrine = lambda lenses: ""
        lens.seat_tier = lambda key: "fanout"
        lens_briefs.lens_tier_refusal = lambda **kwargs: []
        lens_briefs.seat_overage = lambda **kwargs: None
        try:
            outcome = run_main(["--plan", str(plan), "--out", str(root / "out"), "--prefix", "p",
                                "--context-path", str(context), "--model", sorted(lens_briefs.NATIVE_PINS)[0],
                                "--effort", sorted(lens_briefs.NATIVE_EFFORTS)[0],
                                "--must-read-from", str(packet)])
        finally:
            (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal,
             lens_briefs.seat_overage) = saved
        jobs_file = root / "out" / "jobs_p.json"
        entries = [job["mustRead"] for job in json.loads(jobs_file.read_text(encoding="utf-8"))] \
            if jobs_file.exists() else []
    check("a seat's must-read list comes from the packet's view of its mandates, with no list file",
          outcome == "returned" and entries == [[{"path": artifact.as_posix(), "lines": 1}]],
          repr(outcome) + repr(entries))


def packet_placeholder_defaults():
    """A frozen session-audit packet settles the placeholders `prepare` fills, with no --sub flags."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        context = root / "context.md"
        context.write_text("context\n", encoding="utf-8")
        diff = root / "subject-0.diff"
        diff.write_text("diff\n", encoding="utf-8")
        doctrine = root / "doctrine-commits.md"
        doctrine.write_text("commits\n", encoding="utf-8")
        packet = root / "inputs.json"
        packet.write_text(json.dumps({
            "base": "HEAD", "sessionBase": "HEAD", "doctrineCommits": doctrine.as_posix(),
            "artifacts": [{"kind": "subject-diff", "artifact": diff.as_posix(), "file": "src/thing.py"}],
            "views": {"harness-quality": {"mustRead": [diff.as_posix(), doctrine.as_posix()]}}}),
            encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({"roster": [{"key": "seat-a"}]}), encoding="utf-8")
        body = ["```\n", "Doctrine: {{DOCTRINE_COMMITS}}\nHunks: {{HARNESS_HUNKS}}\nHot: {{HOT_SET}}\n", "```\n"]
        found = {"seat-a": types.SimpleNamespace(registry="review", lines=body, kfm=False, citers=False, fixes="",
                                                 mandates=["harness-quality"])}
        saved = (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal, lens_briefs.seat_overage)
        lens.catalog = lambda: ({"review": "preamble"}, found)
        lens.doctrine = lambda lenses: ""
        lens.seat_tier = lambda key: "fanout"
        lens_briefs.lens_tier_refusal = lambda **kwargs: []
        lens_briefs.seat_overage = lambda **kwargs: None
        try:
            out = root / "out"
            outcome = run_main(["--plan", str(plan), "--out", str(out), "--prefix", "p",
                                "--context-path", str(context), "--model", sorted(lens_briefs.NATIVE_PINS)[0],
                                "--effort", sorted(lens_briefs.NATIVE_EFFORTS)[0],
                                "--must-read-from", str(packet)])
            brief = (out / "p_seat-a.md").read_text(encoding="utf-8") if (out / "p_seat-a.md").exists() else ""
        finally:
            (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal,
             lens_briefs.seat_overage) = saved
    check("a packet settles the session-audit placeholders with no --sub", outcome == "returned", str(outcome))
    check("the brief leaves no session-audit placeholder unresolved", "{{" not in brief, brief[:600])
    check("the doctrine pointer names the packet's frozen commit artifact", doctrine.as_posix() in brief, brief[:600])
    check("the harness-hunks list names the packet's subject diff", diff.as_posix() in brief, brief[:600])
    check("a HOT_SET the packet does not fill reads none", "Hot: none" in brief, brief[:600])


def bar_checks():
    """A sidecar seat's brief carries the run's bar (the sidecar prompt has no engine contract); a native seat's
    brief never does, since the engine appends it to the native seat's contract."""
    import os
    saved_env = os.environ.get("HARNESS_TASK_RECORD_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        records = root / "tasks"
        records.mkdir()
        (records / "T1.json").write_text(json.dumps({"id": "T1", "task_id": "T1", "updated": "2026-10-26T10:00:00", "rigor": "light",
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
                                                 citers=False, fixes="")}
        saved = (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal,
                 lens_briefs.seat_overage, lens_briefs.resolve_sidecar_entry, lens_briefs.effort_rungs)
        lens.catalog = lambda: ({"review": "preamble"}, found)
        lens.doctrine = lambda lenses: ""
        lens.seat_tier = lambda key: None
        lens_briefs.lens_tier_refusal = lambda **kwargs: []
        lens_briefs.seat_overage = lambda **kwargs: None
        lens_briefs.resolve_sidecar_entry = lambda token, what, data: {"alias": token, "transport": "off"}
        lens_briefs.effort_rungs = lambda entry, data: ["medium"]

        def briefs(target, out, task="T1", no_task=False):
            argv = ["--plan", str(plan), "--out", str(out), "--prefix", "p", "--target", target,
                    "--context-path", str(context), "--model", sorted(lens_briefs.NATIVE_PINS)[0],
                    "--effort", "medium", "--must-read", str(must)]
            if task:
                argv += ["--task", task]
            if no_task:
                argv += ["--no-task"]
            outcome = run_main(argv)
            text = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.md")) if out.exists() else ""
            return outcome, text
        try:
            sidecar_outcome, sidecar_text = briefs("sidecar", root / "out_sidecar")
            native_outcome, native_text = briefs("native", root / "out_native")
            taskless_outcome, taskless_text = briefs("sidecar", root / "out_taskless", task=None)
            optout_outcome, optout_text = briefs("sidecar", root / "out_optout", task=None, no_task=True)
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
    check("a seat brief with no --task carries the session's record as its acceptance test by default",
          taskless_outcome == "returned" and "## Acceptance test (task T1)" in taskless_text,
          taskless_outcome)
    check("--no-task opts a seat brief out of the session's acceptance test",
          optout_outcome == "returned" and optout_text and "## Acceptance test" not in optout_text,
          optout_outcome)


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
        found = {k: types.SimpleNamespace(registry="review", lines=body, kfm=False, citers=False, fixes="")
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


FIX_JOB_MAP = """# Plan

## Job map

| Slice | Write set | Waits on | Route |
|---|---|---|---|
| S1 | `tools/mechanism_census.py`, `tests/test_mechanism_census.py` | — | job A |
| S5 | `tools/lens.py`, `tools/missing_tool.py` | S2 | job C |
"""


def planted_fix_repo(root):
    """A git repo with one fix commit to `.claude/tools/mechanism_census.py`; the fix is inside the census window."""
    repo = root / "repo"
    (repo / ".claude" / "tools").mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=proof", "-c", "user.email=proof@example.invalid",
                        *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    git("init", "-q")
    (repo / ".claude" / "tools" / "mechanism_census.py").write_text("a = 1\n", encoding="utf-8")
    (repo / ".claude" / "tools" / "lens.py").write_text("b = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "feat: start")
    (repo / ".claude" / "tools" / "mechanism_census.py").write_text("a = 2\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "fix(tools): census reads the wrong row")
    return repo


def fix_brief(root, plan_text, fixes):
    """One seat's native brief built over a planted repo, with its subject a code plan of `plan_text`."""
    repo = planted_fix_repo(root)
    plan = root / "subject.md"
    plan.write_text(plan_text, encoding="utf-8")
    roster = root / "roster.json"
    roster.write_text(json.dumps({"roster": [{"key": "seat-a"}]}), encoding="utf-8")
    context = root / "context.md"
    context.write_text("context\n", encoding="utf-8")
    must = root / "must.txt"
    must.write_text(str(context) + "\n", encoding="utf-8")
    found = {"seat-a": types.SimpleNamespace(registry="review", lines=["```\n", "prompt\n", "```\n"], kfm=False,
                                             citers=False, fixes=fixes)}
    saved = (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal, lens_briefs.seat_overage,
             lens_briefs.REPO)
    lens.catalog = lambda: ({"review": "preamble"}, found)
    lens.doctrine = lambda lenses: ""
    lens.seat_tier = lambda key: None
    lens_briefs.lens_tier_refusal = lambda **kwargs: []
    lens_briefs.seat_overage = lambda **kwargs: None
    lens_briefs.REPO = repo
    out = root / "out"
    try:
        outcome = run_main(["--plan", str(roster), "--out", str(out), "--prefix", "p", "--context-path", str(context),
                            "--model", sorted(lens_briefs.NATIVE_PINS)[0], "--effort", "medium", "--must-read", str(must),
                            "--subject-plan", "code:" + str(plan), "--no-task"])
    finally:
        (lens.catalog, lens.doctrine, lens.seat_tier, lens_briefs.lens_tier_refusal, lens_briefs.seat_overage,
         lens_briefs.REPO) = saved
    text = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.md")) if out.exists() else ""
    return outcome, text, out


def fix_rows(text):
    """The FIX_HISTORY rows of a brief, keyed by the path each row names."""
    rows = (json.loads(line[2:]) for line in text.splitlines() if line.startswith('> {"path"'))
    return {row["path"]: row for row in rows}


def fix_history_checks():
    """The `fixes: plan` seat gets a FIX_HISTORY section: one count row per code file its subject plan touches, a
    bare Job-map path resolved under `.claude/`, an unresolved path named rather than counted as zero."""
    with tempfile.TemporaryDirectory() as tmp:
        outcome, text, out = fix_brief(Path(tmp), FIX_JOB_MAP, "plan")
        rows = fix_rows(text)
        census = ".claude/tools/mechanism_census.py"
        check("a fixes seat's brief returns without a refusal", outcome == "returned", str(outcome))
        check("a fixes seat's brief names its counted fixes per code file the subject plan touches",
              "# FIX_HISTORY" in text and rows.get(census, {}).get("fixes") == 1, repr(rows))
        check("the full fix history of each subject plan goes to a fixes file beside the briefs",
              (out / "p_subject_fixes.json").is_file() and census in json.loads(
                  (out / "p_subject_fixes.json").read_text(encoding="utf-8")), str(list(out.glob("*"))))
        check("a Job-map path without the `.claude/` prefix resolves to its tracked file",
              ".claude/tools/lens.py" in rows and not rows[".claude/tools/lens.py"].get("unresolved")
              and rows[".claude/tools/lens.py"].get("fixes") == 0, repr(rows))
        check("a path that resolves nowhere is named unresolved, never counted as zero fixes",
              rows.get("tools/missing_tool.py", {}).get("unresolved") is True
              and "fixes" not in rows.get("tools/missing_tool.py", {}), repr(rows))
        check("a write-set test file is never counted", not any("tests/" in path for path in rows), repr(rows))
        check("with no census reading recorded, a row's listing is unknown, never `false`",
              census in rows and rows[census].get("listed") is None and "no census reading" in text, repr(rows))
    with tempfile.TemporaryDirectory() as tmp:
        prose = FIX_JOB_MAP.replace("`tools/lens.py`, `tools/missing_tool.py`",
                                    "`tools/lens.py`, `test_lens_{a,b}.py`, `lens_briefs_test.py`, `tools/x*.py`")
        _outcome, text, _out = fix_brief(Path(tmp), prose, "plan")
        rows = fix_rows(text)
        check("a test file or a glob named without its folder is never a row",
              ".claude/tools/lens.py" in rows and set(rows) == {".claude/tools/mechanism_census.py", ".claude/tools/lens.py"},
              repr(sorted(rows)))
    with tempfile.TemporaryDirectory() as tmp:
        _outcome, text, _out = fix_brief(Path(tmp), FIX_JOB_MAP, "")
        check("a seat without a `fixes: plan` header gets no FIX_HISTORY section", "FIX_HISTORY" not in text,
              text[:300])
    with tempfile.TemporaryDirectory() as tmp:
        no_code = FIX_JOB_MAP.replace("`tools/lens.py`, `tools/missing_tool.py`", "`commands/agents/x.md`")
        no_code = no_code.replace("`tools/mechanism_census.py`, `tests/test_mechanism_census.py`", "`CLAUDE.md`")
        outcome, text, _out = fix_brief(Path(tmp), no_code, "plan")
        check("a plan that names no code file gets an empty FIX_HISTORY section",
              outcome == "returned" and "# FIX_HISTORY" in text and fix_rows(text) == {}, repr(fix_rows(text)))


def entry_checks():
    """`build_briefs` takes params and returns the jobs: no argv, no stdout capture, and a per-call kfm root, so
    two calls in one process each keep their own snapshot. Real lens registry, real model registry."""
    registry = json.loads((lens_briefs.REPO / ".claude/reference/external_models.json").read_text(encoding="utf-8"))
    pin = next(m["id"] for m in registry["models"] if m.get("transport") == "anthropic"
               and {"architect", "executor", "fanout"} <= set(m.get("roles") or []))
    key = "plc-memory-alignment"
    if lens.seat_tier(key) is None:  # the plan-check registry ships in the coding layer
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        context = root / "context.md"
        context.write_text("context\n", encoding="utf-8")
        must = root / "must.txt"
        must.write_text(str(context) + "\n", encoding="utf-8")
        roster = root / "roster.json"
        roster.write_text(json.dumps({"roster": [{"key": key}]}), encoding="utf-8")
        seen = []
        for name in ("first", "second"):
            kfm_root = root / ("kfm_" + name)
            params = lens_briefs.BriefParams(
                plan=str(roster), out=str(root / name), prefix=name, context_path=str(context), model=pin,
                effort="medium", target="sidecar", must_read=str(must), kfm_root=str(kfm_root))
            jobs = lens_briefs.build_briefs(params)
            written = json.loads((root / name / ("jobs_%s.json" % name)).read_text(encoding="utf-8"))
            body = (root / name / ("%s_%s.md" % (name, key))).read_text(encoding="utf-8")
            seen.append((jobs, written, body, kfm_root))
        check("build_briefs returns the job rows it wrote to jobs_<prefix>.json",
              all(jobs == (written["jobs"] if isinstance(written, dict) else written) and len(jobs) == 1
                  for jobs, written, _body, _kfm in seen), repr([s[0] for s in seen]))
        check("each call's brief names the snapshot under its own kfm_root",
              all(kfm_root.as_posix() in body.replace("\\", "/") for _j, _w, body, kfm_root in seen)
              and seen[0][2] != seen[1][2])
        refusal = None
        try:
            lens_briefs.build_briefs(lens_briefs.BriefParams(
                plan=str(roster), out=str(root / "bad"), prefix="bad", context_path=str(context),
                model="not-a-pin", target="native", effort="medium", must_read=str(must)))
        except lens_briefs.ArgumentRefused as exc:
            refusal = str(exc)
        check("an invalid native pin raises ArgumentRefused, not SystemExit",
              refusal is not None and "invalid choice" in refusal, repr(refusal))


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
    packet_view_checks()
    packet_seat_checks()
    packet_placeholder_defaults()
    merged_seat_checks()
    fix_history_checks()
    bar_checks()
    entry_checks()
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
