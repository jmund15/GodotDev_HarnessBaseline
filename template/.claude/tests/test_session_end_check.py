#!/usr/bin/env python3
"""Re-runnable proof for tools/session_end_check.py.

instruction_quality §14: registration proves wiring, not matching. Each case builds a
synthetic transcript JSONL and, where relevant, a synthetic self-evaluate archive, then
asserts on stdout and the exit code -- so a regression in the phase table, `--resume`'s
RESUME AT line, or `--artifacts`' Phase 3 artifact check shows up as a failing case.

    python3 .claude/tests/test_session_end_check.py
"""
import json
import importlib.util
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

TOOL = os.environ.get('SESSION_CLOSE_MODULE', str(Path(__file__).resolve().parents[1] / 'tools/session_end_check.py'))
spec = importlib.util.spec_from_file_location('session_end_check', TOOL)
checker = importlib.util.module_from_spec(spec)
sys.modules['session_end_check'] = checker
spec.loader.exec_module(checker)
SID = "abc12345-0000-0000-0000-000000000001"
OTHER_SID = "def67890-0000-0000-0000-000000000002"


def tool_use(kind, value):
    key = "file_path" if kind == "read" else "command"
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": value, "name": "Read" if kind == "read" else "Bash", "input": {key: value}}]}}


def tool_result(tool_id, is_error=False):
    return {"type": "user", "message": {"content": [{
        "type": "tool_result", "tool_use_id": tool_id, "is_error": is_error,
        "content": "finished",
    }]}}


def write_transcript(tmpdir, lines, sid=SID):
    path = os.path.join(tmpdir, sid + ".jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")
    return path


def archive_entry(session_id, entry_id):
    return {
        "session_id": session_id,
        "id": entry_id,
        "title": "fixture",
        "date": "2026-09-12",
        "outcome": "clean",
        "pattern": None,
        "domains": ["meta"],
        "corrections": [],
    }


def write_archive(tmpdir, session_ids, name="archive.json"):
    path = os.path.join(tmpdir, name)
    entries = [archive_entry(sid, i) for i, sid in enumerate(session_ids)]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"Self_Evaluate_Themes": {"patterns": {"A": "fixture"}},
                   "structured_entries": entries, "legacy_entries": []}, fh)
    return path


def write_receipts(tmpdir, sid):
    directory = Path(tmpdir) / (sid + '.receipts')
    directory.mkdir(exist_ok=True)
    evidence = Path(tmpdir) / 'fixture-evidence.json'
    evidence.write_text('{}')
    prune = Path(tmpdir) / 'fixture-harness-prune.md'
    prune.write_text('### Plans\nfixture\n\n### Worktrees\nfixture\n\n### Scratch\nfixture\n')
    for stem in checker.required_phases('precommit'):
        phase_evidence = prune if stem == 'harness_prune' else evidence
        receipt = checker.make_receipt(sid, stem, 'completed', [str(evidence)],
            [str(phase_evidence)], 'Synthetic phase proof')
        (directory / (stem + '.json')).write_text(json.dumps(receipt))


# The checker re-evaluates a false-predicate skip through `current_predicates`; a fixture's repo holds no
# session, so every run binds that seam to the predicate values the case supplies (absent: still false).
# A stem whose value is "raise" makes the seam raise. Each call's stems are appended to `calls`, if given.
SEAM_LAUNCH = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('session_end_check', sys.argv[1])
checker = importlib.util.module_from_spec(spec)
sys.modules['session_end_check'] = checker
spec.loader.exec_module(checker)
now, calls = json.loads(sys.argv[2]), sys.argv[3]
def current_predicates(stems, repo, sid, transcript):
    if calls:
        with open(calls, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(sorted(stems)) + '\\n')
    if any(now.get(stem) == 'raise' for stem in stems):
        raise RuntimeError('fixture evaluator failure')
    return {stem: {'value': now.get(stem, False), 'predicate': 'fixture', 'basis': {}} for stem in stems}
checker.current_predicates = current_predicates
sys.argv = [sys.argv[1]] + sys.argv[4:]
sys.exit(checker.main())
"""


def run(args, now=None, calls=None):
    transcript = Path(args[args.index('--transcript') + 1])
    args += ['--session', transcript.stem,
             '--receipts', str(transcript.parent / (transcript.stem + '.receipts')),
             '--repo', str(transcript.parent)]
    r = subprocess.run([sys.executable, '-B', "-X", "utf8", "-c", SEAM_LAUNCH, TOOL,
                        json.dumps(now or {}), str(calls or '')] + args,
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    return r.returncode, r.stdout


def line_with(out, needle):
    """The single output line containing `needle`, or '' if absent."""
    for ln in out.splitlines():
        if needle in ln:
            return ln
    return ""


# Some, not all, phase instructions accessed; 5.4 via scripts/roadmap_atlas.py.
PARTIAL_LINES = [
    tool_use("read", ".claude/commands/session_audit.md"),
    tool_use("bash", "cat .claude/commands/autolearn.md"),
    tool_use("read", ".claude/commands/self_evaluate.md"),
    tool_use("read", ".claude/commands/routing_audit.md"),
    tool_use("bash", "python3 .claude/scripts/roadmap_atlas.py --render --json"),
    tool_use("read", ".claude/commands/regression_gate.md"),
    tool_use("read", ".claude/commands/worklog.md"),
    tool_use("read", ".claude/commands/commit_push.md"),
    # session_digest (Phase 0) and reindex_search (Phase 8) never opened.
]

ALL_MANDATORY_LINES = PARTIAL_LINES + [
    tool_use("bash", "python3 .claude/tools/session_digest.py --prompt-tail session_end"),
    tool_use("read", ".claude/commands/reindex_search.md"),
]


def main():
    failures = []

    def check(label, cond, detail=""):
        check.calls += 1
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + ("\n     " + detail if detail else ""))
    check.calls = 0

    with tempfile.TemporaryDirectory() as tmp:
        partial_path = write_transcript(tmp, PARTIAL_LINES, sid=SID)
        all_path = write_transcript(tmp, ALL_MANDATORY_LINES, sid=OTHER_SID)
        archive_hit = write_archive(tmp, [OTHER_SID], name="archive_hit.json")
        archive_miss = write_archive(tmp, ["some-other-session"], name="archive_miss.json")
        archive_ledger = write_archive(tmp, ["some-other-session"], name="archive_ledger.json")
        with open(archive_ledger[:-5] + ".jsonl", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(archive_entry(OTHER_SID, 3)) + "\n")

        # --- default mode: missing mandatory phases -> exit 1, phases printed -----------
        rc, out = run(["--transcript", partial_path])
        check("default mode exits 1 on missing mandatory phases", rc == 1)
        check("default mode reports unknown session_digest completion", "[unknown]" in out and "session_digest" in out)
        check("5.4 atlas access is observed but not completed",
              "[unknown]" in line_with(out, "5.4 Roadmap atlas") and "accessed=True" in line_with(out, "5.4 Roadmap atlas"))

        # --- default mode: everything opened without receipts -> exit 1 -----------------------------------
        rc, out = run(["--transcript", all_path])
        check("command reads alone do not finish closeout", rc == 1, detail=out[-400:])
        check("command reads produce no all-clear", "All checked phases have current receipts" not in out)
        write_receipts(tmp, OTHER_SID)

        # --- successful invocation is still only an observation -------------------------
        digest_call = "python3 .claude/tools/session_digest.py --prompt-tail session_end"
        succeeded_path = write_transcript(
            tmp, ALL_MANDATORY_LINES + [tool_result(digest_call)], sid=SID + "-success")
        observed = checker.phase_observations(succeeded_path)["session_digest"]
        check("a successful tool result is observed but never marks completion",
              observed["tool_succeeded"] and not observed["completed"])

        own_path = write_transcript(tmp, [tool_use("read", ".claude/commands/worklog.md")], sid=SID + "-own")
        check("this checkout's own phase command read is observed (control for the peer cases)",
              checker.phase_observations(own_path)["worklog"]["accessed"])
        for layout in (".claude/worktrees", ".claude/.cache/task-worktrees", ".claude/.cache/baseline-worktrees"):
            peer_path = write_transcript(
                tmp, [tool_use("read", layout + "/peer/.claude/commands/worklog.md")],
                sid=SID + "-peer-" + layout.rsplit("/", 1)[-1])
            check("a peer checkout under %s opening a phase command is not this session's phase" % layout,
                  not checker.phase_observations(peer_path)["worklog"]["accessed"])
        rc, out = run(["--transcript", succeeded_path])
        check("successful invocation without a receipt does not complete the phase",
              rc == 1 and "[unknown]" in line_with(out, "session_digest"), detail=out[-400:])

        # --- --resume: some phases missing -> names the first, always exit 0 -------------
        rc, out = run(["--transcript", partial_path, "--resume"])
        check("--resume exits 0 even with gaps", rc == 0)
        check("--resume names phase 0 as RESUME AT", "RESUME AT: 0" in out,
              detail=out[-300:])

        # --- --resume: nothing missing (artifact present) -> the none-branch sentence -------
        rc, out = run(["--transcript", all_path, "--resume", "--archive", archive_hit])
        check("--resume exits 0 when nothing is missing", rc == 0)
        check("--resume prints the none-branch sentence",
              "RESUME AT: none" in out and "receipts are current" in out)

        # --- --artifacts: Phase 3 receipted but archive has no matching session_id ------------
        rc, out = run(["--transcript", all_path, "--artifacts", "--archive", archive_miss])
        check("--artifacts exits 1 on a missing Phase 3 artifact", rc == 1)
        check("--artifacts flags Phase 3 artifact missing", "[artifact-missing]" in out)

        # --- --artifacts: Phase 3 receipted and archive has the matching session_id ------------
        rc, out = run(["--transcript", all_path, "--artifacts", "--archive", archive_hit])
        check("--artifacts exits 0 when the archive holds this session's row", rc == 0, detail=out[-400:])
        check("--artifacts reports completed self-evaluate", "[completed]" in line_with(out, "self_evaluate"))

        # --- --artifacts: Phase 3 accepts a target row held only in the bounded ledger -------
        rc, out = run(["--transcript", all_path, "--artifacts", "--archive", archive_ledger])
        check("--artifacts accepts a ledger-only Phase 3 row", rc == 0,
              detail=out[-400:])

        # --- --resume --artifacts: a receipted phase whose artifact is missing moves RESUME AT ----
        rc, out = run(["--transcript", all_path, "--resume", "--artifacts", "--archive", archive_miss])
        check("--resume points at Phase 3 when its artifact is missing", "RESUME AT: 3" in out,
              detail=line_with(out, "RESUME AT"))

    # --- the default archive path is project-anchored, not cwd-relative ---------------
    sys.path.insert(0, os.path.dirname(TOOL))
    import session_end_check  # noqa: E402
    check("DEFAULT_ARCHIVE is absolute (anchored like --repo)",
          os.path.isabs(session_end_check.DEFAULT_ARCHIVE), detail=session_end_check.DEFAULT_ARCHIVE)

    with tempfile.TemporaryDirectory() as home:
        repo = os.path.join(home, "repo")
        transcripts = os.path.join(home, ".claude", "projects", __import__('session_digest').projects_dir(repo).name)
        os.makedirs(transcripts)
        active = write_transcript(transcripts, PARTIAL_LINES, sid=SID)
        peer = write_transcript(transcripts, ALL_MANDATORY_LINES, sid=OTHER_SID)
        os.utime(active, (100, 100))
        os.utime(peer, (200, 200))
        for label, cli, environment_sid, expected_sid, expected_rc in [
            ("default selects the active session, not the newest peer", [], SID, SID, 1),
            ("explicit session takes precedence over the environment", ["--session", OTHER_SID], SID, OTHER_SID, 1),
            ("standalone invocation requires explicit session identity", [], None, None, 2),
            ("missing active session does not select the peer", [], "missing-session", None, 2),
        ]:
            environment = dict(os.environ)
            environment.pop("CLAUDE_CODE_SESSION_ID", None)
            environment.pop("CLAUDE_CONFIG_DIR", None)
            if environment_sid is not None:
                environment["CLAUDE_CODE_SESSION_ID"] = environment_sid
            with patch.dict(os.environ, environment, clear=True), \
                 patch.object(sys, "argv", [TOOL, "--repo", repo] + cli), \
                 patch.object(session_end_check.os.path, "expanduser", return_value=home), \
                 redirect_stdout(StringIO()) as output:
                rc = session_end_check.main()
            text = output.getvalue()
            check(label, (f"transcript: {expected_sid}.jsonl" in text) if expected_sid else
                  ("UNKNOWN" in text and "transcript:" not in text), detail=text)
            check(label + ": selected transcript determines exit code", rc == expected_rc, detail=str(rc))

    # --- receipt freshness propagates only through declared inputs/dependencies --------
    with tempfile.TemporaryDirectory() as tmp:
        checked_input = Path(tmp) / "checked-input.txt"
        upstream_evidence = Path(tmp) / "upstream-result.json"
        downstream_evidence = Path(tmp) / "downstream-result.json"
        selected_archive_row = Path(tmp) / "selected-self-eval.json"
        unrelated_archive = Path(tmp) / "append-only-archive.jsonl"
        checked_input.write_text("v1", encoding="utf-8")
        upstream_evidence.write_text("{}", encoding="utf-8")
        downstream_evidence.write_text("{}", encoding="utf-8")
        selected_archive_row.write_text("{}", encoding="utf-8")
        unrelated_archive.write_text("{}\n", encoding="utf-8")

        upstream = checker.make_receipt(
            SID, "session_digest", "completed", [str(checked_input)],
            [str(upstream_evidence)], "digest complete")
        downstream = checker.make_receipt(
            SID, "session_audit", "completed", [str(checked_input)],
            [str(downstream_evidence)], "audit complete",
            dependencies={"session_digest": upstream},
            available={"session_digest": upstream})
        receipts = {"session_digest": upstream, "session_audit": downstream}
        check("fresh declared inputs and dependencies keep both receipts current",
              checker.receipt_status(upstream, SID, receipts) == "completed"
              and checker.receipt_status(downstream, SID, receipts) == "completed")
        checked_input.write_text("v2", encoding="utf-8")
        check("a changed checked input invalidates its receipt",
              checker.receipt_status(upstream, SID, receipts) == "stale")
        check("a changed upstream input invalidates dependent phase receipts",
              checker.receipt_status(downstream, SID, receipts) == "stale")

        stable = checker.make_receipt(
            SID, "self_evaluate", "completed", [str(selected_archive_row)],
            [str(selected_archive_row)], "selected row verified")
        unrelated_archive.write_text("{}\n{\"session_id\":\"peer\"}\n", encoding="utf-8")
        check("an unrelated archive append does not stale selected-row evidence",
              checker.receipt_status(stable, SID, {"self_evaluate": stable}) == "completed")

    # --- stale receipts are handled by their delta: named, refreshed, conclusion-bound -----
    with tempfile.TemporaryDirectory() as tmp:
        rsid = "refr0000-0000-0000-0000-000000000004"
        rpath = write_transcript(tmp, [], sid=rsid)
        rdir = Path(tmp) / (rsid + ".receipts")
        rdir.mkdir()
        r_in, r_ev = Path(tmp) / "digest-input.txt", Path(tmp) / "digest-result.json"
        a_in, a_ev = Path(tmp) / "audit-input.txt", Path(tmp) / "audit-result.json"
        extra = Path(tmp) / "extra-input.txt"
        for f in (r_in, r_ev, a_in, a_ev, extra):
            f.write_text("v1", encoding="utf-8")

        def record_pair():
            digest = checker.make_receipt(rsid, "session_digest", "completed", [str(r_in)], [str(r_ev)], "digest ok")
            audit = checker.make_receipt(rsid, "session_audit", "completed", [str(a_in)], [str(a_ev)], "audit ok",
                                         dependencies={"session_digest": digest}, available={"session_digest": digest})
            (rdir / "session_digest.json").write_text(json.dumps(digest))
            (rdir / "session_audit.json").write_text(json.dumps(audit))

        def stored(stem):
            return json.loads((rdir / (stem + ".json")).read_text())

        def refresh(*args):
            return run(["--transcript", rpath, "--refresh", *args])

        record_pair()
        r_in.write_text("v2", encoding="utf-8")
        for flag in ("--resume", "--artifacts"):
            _, out = run(["--transcript", rpath, flag])
            line = line_with(out, "(session_digest)")
            check("A1 " + flag + " names the changed input on a stale receipt",
                  "[stale]" in line and "changed: inputs digest-input.txt" in line, detail=line)
            check("A1 " + flag + " names only changed declared paths",
                  "evidence" not in line and "digest-result.json" not in line, detail=line)
        r_in.unlink()
        _, out = run(["--transcript", rpath, "--resume"])
        line = line_with(out, "(session_digest)")
        check("A1 a missing file reads as missing, not changed",
              "missing: inputs digest-input.txt" in line and "changed" not in line, detail=line)
        r_in.write_text("v2", encoding="utf-8")

        before = stored("session_digest")
        rc, out = refresh("session_digest", "--reason", "late commit; conclusion unchanged", "--input", str(extra))
        after = stored("session_digest")
        check("A2 refresh exits 0 and keeps phase, status, original reason, dependency stems",
              rc == 0 and all(after[k] == before[k] for k in ("phase", "status", "reason", "dependencies")), detail=out)
        check("A2 refresh appends {reason, at, changed} and adds the extra input",
              len(after.get("refreshes", [])) == 1
              and after["refreshes"][0]["reason"] == "late commit; conclusion unchanged"
              and after["refreshes"][0]["at"] and str(r_in) in after["refreshes"][0]["changed"]
              and str(extra) in after["inputs"], detail=json.dumps(after.get("refreshes")))
        _, out = run(["--transcript", rpath, "--resume"])
        check("A2 a refreshed receipt reads current", "[completed]" in line_with(out, "(session_digest)"), detail=out)
        check("A3 an input-only refresh leaves the dependent current", "[completed]" in line_with(out, "(session_audit)"),
              detail=line_with(out, "(session_audit)"))
        extra.write_text("v2", encoding="utf-8")
        refresh("session_digest", "--reason", "second late change")
        check("A2 a second refresh appends to the history",
              [e["reason"] for e in stored("session_digest").get("refreshes", [])]
              == ["late commit; conclusion unchanged", "second late change"])

        rc, out = refresh("session_digest", "--reason", "nothing moved")
        check("A2 refresh refuses when nothing changed", rc == 2 and "REFUSED" in out, detail=out)
        rc, out = refresh("autolearn", "--reason", "no receipt")
        check("A2 refresh refuses when there is no receipt", rc == 2 and "REFUSED" in out, detail=out)

        r_ev.write_text("v2", encoding="utf-8")
        parents = checker.load_receipts(str(rdir), rsid)
        check("A3 changed parent evidence stales the dependent (before refresh)",
              checker.receipt_status(parents["session_audit"], rsid, parents) == "stale")
        refresh("session_digest", "--reason", "evidence changed")
        _, out = run(["--transcript", rpath, "--resume"])
        line = line_with(out, "(session_audit)")
        check("A3 refreshed parent with new evidence still stales the dependent, naming the dependency",
              "[stale]" in line and "dependency session_digest" in line, detail=line)
        rc, out = refresh("session_audit", "--reason", "digest moved")
        check("A2 refresh re-binds dependencies and the dependent reads current",
              rc == 0 and "[completed]" in line_with(run(["--transcript", rpath, "--resume"])[1], "(session_audit)"),
              detail=out)

        old_parent = stored("session_digest")
        old_child = stored("session_audit")
        old_child["dependencies"] = {"session_digest": checker._receipt_hash(old_parent)}
        check("A3 a dependency stored as the old whole-receipt hash still reads current",
              checker.receipt_status(old_child, rsid, {"session_digest": old_parent}) == "completed")

        record_pair()
        blocked = checker.make_receipt(rsid, "autolearn", "blocked", [], [], "waiting on peer")
        (rdir / "autolearn.json").write_text(json.dumps(blocked))
        a_in.write_text("v2", encoding="utf-8")
        rc, out = refresh("autolearn", "--reason", "try")
        check("A2 refresh refuses a blocked receipt", rc == 2 and "REFUSED" in out and "blocked" in out, detail=out)
        r_in.write_text("v3", encoding="utf-8")
        rc, out = refresh("session_audit", "--reason", "parent is stale")
        check("A2 refresh refuses when a dependency is not current",
              rc == 2 and "REFUSED" in out and "session_digest" in out, detail=out)

    # --- cold replay records and resumes only inside an isolated fixture ----------------
    with tempfile.TemporaryDirectory() as tmp:
        cold_sid = "cold0000-0000-0000-0000-000000000003"
        cold_path = write_transcript(tmp, ALL_MANDATORY_LINES, sid=cold_sid)
        cold_archive = write_archive(tmp, ["peer-session"], name="cold-archive.json")
        with open(cold_archive[:-5] + ".jsonl", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(archive_entry(cold_sid, 9)) + "\n")
        recorded = {}
        dependency_map = {
            "session_audit": ["session_digest"],
            "autolearn": ["session_digest", "session_audit"],
            "self_evaluate": ["session_digest", "session_audit", "autolearn"],
            "update_roadmap": ["roadmap_atlas"],
        }
        for _, stem, mandatory in checker.PHASES:
            if stem not in checker.required_phases("precommit"):
                continue
            input_path = Path(tmp) / (stem + "-input.txt")
            evidence_path = Path(tmp) / (stem + "-result.json")
            input_path.write_text(stem, encoding="utf-8")
            evidence_path.write_text(
                "### Plans\nfixture\n\n### Worktrees\nfixture\n\n### Scratch\nfixture\n"
                if stem == "harness_prune" else "{}", encoding="utf-8")
            status = "completed" if mandatory else "skipped"
            args = ["--transcript", cold_path, "--record", stem, "--status", status,
                    "--input", str(input_path), "--reason", "isolated cold replay"]
            if status == "skipped" and stem in checker.GATED:
                predicate_path = Path(tmp) / (stem + "-predicate.json")
                predicate_path.write_text(json.dumps({stem: {"value": False, "predicate": "fixture", "basis": {}}}))
                args += ["--predicate", str(predicate_path)]
            if status == "completed":
                args += ["--evidence", str(evidence_path)]
            for dependency in dependency_map.get(stem, []):
                args += ["--depends", dependency]
            rc, out = run(args)
            recorded[stem] = (rc, out)
        check("cold replay records every precommit phase without shared state",
              all(rc == 0 for rc, _ in recorded.values()), detail=repr(recorded))
        rc, out = run(["--transcript", cold_path, "--artifacts", "--archive", cold_archive])
        check("cold replay reaches a complete precommit closeout", rc == 0, detail=out[-500:])
        with open(cold_archive[:-5] + ".jsonl", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(archive_entry("unrelated-session", 10)) + "\n")
        rc, out = run(["--transcript", cold_path, "--resume", "--artifacts",
                       "--archive", cold_archive])
        check("cold replay ignores an unrelated archive append",
              rc == 0 and "RESUME AT: none" in out, detail=out[-500:])

        # --- final stage: a completed commit_push with the branch still ahead of its upstream -------
        # Phase 7 invokes /commit_push, which pushes. A receipt recorded after local commits alone
        # passed the close (2026-09-23, session e33ca9f0: 35 commits sat unpushed).
        git = lambda cwd, *a: subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True,
                                             env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                                      GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
        bare, repo = os.path.join(tmp, "up.git"), os.path.join(tmp, "work")
        git(tmp, "init", "-q", "--bare", bare)
        git(tmp, "clone", "-q", bare, repo)
        Path(repo, "a.txt").write_text("1")
        git(repo, "add", "a.txt"); git(repo, "commit", "-q", "-m", "one"); git(repo, "push", "-q", "-u", "origin", "HEAD")
        Path(repo, "a.txt").write_text("2")
        git(repo, "commit", "-q", "-am", "two")
        final_sid = SID + "-final"
        final_path = write_transcript(tmp, ALL_MANDATORY_LINES, sid=final_sid)
        receipts_dir = Path(tmp) / (final_sid + '.receipts')
        receipts_dir.mkdir(exist_ok=True)
        ev = Path(tmp) / 'final-evidence.json'
        ev.write_text('{}')
        prune = Path(tmp) / 'final-harness-prune.md'
        prune.write_text('### Plans\nfixture\n\n### Worktrees\nfixture\n\n### Scratch\nfixture\n')
        for stem in checker.required_phases('final'):
            receipt = checker.make_receipt(final_sid, stem, 'completed', [str(ev)],
                                           [str(prune if stem == 'harness_prune' else ev)], 'Synthetic phase proof')
            (receipts_dir / (stem + '.json')).write_text(json.dumps(receipt))

        def run_final():
            r = subprocess.run([sys.executable, '-B', "-X", "utf8", TOOL, "--transcript", final_path,
                                "--session", final_sid, "--receipts", str(receipts_dir), "--repo", repo,
                                "--artifacts", "--archive", archive_hit, "--stage", "final"],
                               capture_output=True, text=True, encoding="utf-8", timeout=60)
            return r.returncode, r.stdout
        # archive_hit holds OTHER_SID, not final_sid; point Phase 3 at a row for this sid.
        archive_hit = write_archive(tmp, [final_sid], name="archive_final.json")
        rc, out = run_final()
        check("final stage fails a completed commit_push while the branch is ahead of its upstream",
              rc == 1 and "[unpushed]" in line_with(out, "commit_push"), detail=out[-500:])
        git(repo, "push", "-q", "origin", "HEAD:refs/heads/feature/x")
        rc, out = run_final()
        check("...and a push to a branch outside the land prefix does not count as pushed",
              rc == 1 and "[unpushed]" in line_with(out, "commit_push"), detail=out[-500:])
        land = checker.land_prefix()
        if land:  # a project with no land runner has no land branch to push to
            git(repo, "push", "-q", "origin", "HEAD:refs/heads/" + land + "abcd1234")
            rc, out = run_final()
            check("...and passes once HEAD is on the land branch, before the runner moves the upstream",
                  rc == 0 and "[completed]" in line_with(out, "commit_push"), detail=out[-500:])
            git(repo, "push", "-q", "origin", "--delete", land + "abcd1234")
        git(repo, "push", "-q")
        rc, out = run_final()
        check("...and passes once the branch is pushed",
              rc == 0 and "[completed]" in line_with(out, "commit_push"), detail=out[-500:])
        # Phase 7a defers the push when it would publish another session's commits: a skipped commit_push
        # stands only while the unpushed range holds a commit whose Claude-Session trailer is not this session's.
        own, peer = "https://claude.ai/code/session_OWN", "https://claude.ai/code/session_PEER"
        write_transcript(tmp, ALL_MANDATORY_LINES + [{
            "type": "attachment", "timestamp": "2026-10-12T00:00:00Z",
            "attachment": {"type": "remote_session_change", "commit": "Claude-Session: " + own}}], sid=final_sid)
        skip = checker.make_receipt(final_sid, "commit_push", "skipped", [str(ev)], [str(ev)], "7a: peers open")
        (receipts_dir / "commit_push.json").write_text(json.dumps(skip))
        Path(repo, "a.txt").write_text("3")
        git(repo, "commit", "-q", "-am", "own\n\nClaude-Session: " + own)
        rc, out = run_final()
        check("a skipped commit_push with only this session's commits unpushed is invalid",
              rc == 1 and "[invalid-skip]" in line_with(out, "commit_push"), detail=out[-500:])
        Path(repo, "a.txt").write_text("4")
        git(repo, "commit", "-q", "-am", "peer\n\nClaude-Session: " + peer)
        rc, out = run_final()
        check("...and stands while the unpushed range holds another session's commit",
              rc == 0 and "[skipped]" in line_with(out, "commit_push"), detail=out[-500:])
        write_transcript(tmp, ALL_MANDATORY_LINES, sid=final_sid)
        rc, out = run_final()
        check("...and is invalid when the transcript names no trailer of its own (fails closed)",
              rc == 1 and "[invalid-skip]" in line_with(out, "commit_push"), detail=out[-500:])
        git(repo, "push", "-q")
        done = checker.make_receipt(final_sid, "commit_push", "completed", [str(ev)], [str(ev)], 'Synthetic phase proof')
        (receipts_dir / "commit_push.json").write_text(json.dumps(done))
        # Phase 8 is hygiene: a concurrent session's lock on the index blocks it, never the close.
        blocked = checker.make_receipt(final_sid, "reindex_search", "blocked", [str(ev)], [str(ev)],
                                       "EBUSY: another session holds search.db")
        (receipts_dir / "reindex_search.json").write_text(json.dumps(blocked))
        rc, out = run_final()
        check("a blocked reindex does not fail the final close",
              rc == 0 and "[blocked]" in line_with(out, "reindex_search"), detail=out[-500:])
        # Phase 5.4's errors are non-blocking, and a project that ships no atlas generator can only block it.
        atlas = checker.make_receipt(final_sid, "roadmap_atlas", "blocked", [str(ev)], [str(ev)],
                                     "the project ships no roadmap-atlas generator")
        (receipts_dir / "roadmap_atlas.json").write_text(json.dumps(atlas))
        rc, out = run_final()
        check("a blocked roadmap atlas does not fail the final close",
              rc == 0 and "[blocked]" in line_with(out, "roadmap_atlas"), detail=out[-500:])
        gate_path = receipts_dir / "regression_gate.json"
        gate_before = gate_path.read_bytes() if gate_path.exists() else None
        gate_blocked = checker.make_receipt(final_sid, "regression_gate", "blocked", [str(ev)], [str(ev)],
                                            "red gate left unfixed")
        gate_path.write_text(json.dumps(gate_blocked))
        rc, out = run_final()
        check("a blocked regression gate fails the close: the gate is floor whenever it applies",
              rc == 1 and "[blocked]" in line_with(out, "regression_gate"), detail=out[-500:])
        if gate_before is None:
            gate_path.unlink()
        else:
            gate_path.write_bytes(gate_before)
        mandatory_blocked = checker.make_receipt(final_sid, "worklog", "blocked", [str(ev)], [str(ev)], "x")
        (receipts_dir / "worklog.json").write_text(json.dumps(mandatory_blocked))
        rc, out = run_final()
        check("...while a blocked mandatory phase still does", rc == 1, detail=out[-500:])

    # --- gated phases: a skip records the predicate that was false ---------------------------
    with tempfile.TemporaryDirectory() as tmp:
        gate_sid = "gate0000-0000-0000-0000-000000000004"
        gate_path = write_transcript(tmp, ALL_MANDATORY_LINES, sid=gate_sid)
        gate_input = Path(tmp) / "gate-input.txt"
        gate_input.write_text("scope")

        def predicate_file(stem, value, name=None):
            path = Path(tmp) / (name or stem + "-predicate.json")
            path.write_text(json.dumps({stem: {"value": value, "predicate": "the predicate text", "basis": {}}}))
            return str(path)

        def record_skip(stem, predicate=None, reason="the predicate text"):
            args = ["--transcript", gate_path, "--record", stem, "--status", "skipped",
                    "--input", str(gate_input), "--reason", reason]
            if predicate:
                args += ["--predicate", predicate]
            return run(args)

        def phase_line(stem, now=None):
            return line_with(run(["--transcript", gate_path, "--resume"], now)[1], "(" + stem + ")")

        rc, out = record_skip("autolearn")
        check("a gated skip without --predicate exits 2", rc == 2, detail=out)
        for value in (True, None):
            rc, out = run(["--transcript", gate_path, "--record", "autolearn", "--status", "skipped",
                           "--input", str(gate_input), "--predicate", predicate_file("autolearn", value)])
            check("a gated skip on a %s predicate with no --reason exits 2" % value, rc == 2, detail=out)
            rc, out = record_skip("autolearn", predicate_file("autolearn", value))
            check("...and on a %s predicate whose reason only restates it" % value, rc == 2, detail=out)
        rc, out = record_skip("autolearn", predicate_file("routing_audit", False, "other.json"))
        check("a predicate file that names another stem exits 2", rc == 2, detail=out)
        rc, out = record_skip("autolearn", str(Path(tmp) / "absent.json"))
        check("a missing predicate file exits 2", rc == 2, detail=out)
        rc, out = record_skip("session_digest", predicate_file("session_digest", False))
        check("--predicate on a stem that is not gated exits 2", rc == 2, detail=out)
        rc, out = record_skip("autolearn", predicate_file("autolearn", True), reason="the digest had friction but no action")
        check("a true predicate plus an in-body reason records", rc == 0, detail=out)
        check("...and the final check reads it as skipped", "[skipped]" in phase_line("autolearn"), detail=phase_line("autolearn"))
        rc, out = record_skip("routing_audit", predicate_file("routing_audit", False))
        check("a false predicate records a skip of a mandatory gated phase", rc == 0, detail=out)
        check("...and the final check passes it", "[skipped]" in phase_line("routing_audit"), detail=phase_line("routing_audit"))
        for value in (True, None, "raise"):
            line = phase_line("routing_audit", {"routing_audit": value})
            check("a false-predicate skip whose predicate now reads %s is stale on resume" % value,
                  "[stale-skip]" in line, detail=line)
        rc, out = run(["--transcript", gate_path, "--stage", "final"], {"routing_audit": True})
        check("...and the final check fails on it", rc == 1 and "[stale-skip]" in line_with(out, "(routing_audit)"),
              detail=out[-500:])
        calls = Path(tmp) / "seam-calls.jsonl"
        line = phase_line("autolearn", {"autolearn": True})
        run(["--transcript", gate_path, "--resume"], {"autolearn": True}, calls)
        asked = [json.loads(l) for l in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []
        check("a skip a true predicate already ran stays skipped: only false-predicate skips are re-evaluated",
              "[skipped]" in line and asked == [["routing_audit"]], detail=line + " " + repr(asked))
        rc, out = record_skip("session_digest")
        check("a skipped mandatory phase that is not gated is an invalid-skip",
              "[invalid-skip]" in phase_line("session_digest"), detail=phase_line("session_digest"))

        receipts_dir = Path(tmp) / (gate_sid + ".receipts")
        receipt = json.loads((receipts_dir / "routing_audit.json").read_text())
        check("the receipt stores the predicate object", receipt.get("predicate", {}).get("value") is False,
              detail=repr(receipt.get("predicate")))
        def hand_edit(stem, edit):
            edited = json.loads((receipts_dir / "routing_audit.json").read_text())
            edited["phase"] = stem
            edit(edited)
            (receipts_dir / (stem + ".json")).write_text(json.dumps(edited))
            return phase_line(stem)
        line = hand_edit("autolearn", lambda r: r.pop("predicate"))
        check("a skipped gated receipt with no stored predicate reads invalid-skip", "[invalid-skip]" in line, detail=line)
        line = hand_edit("autolearn", lambda r: r["predicate"].update(value=True))
        check("a true predicate whose reason only restates it reads invalid-skip", "[invalid-skip]" in line, detail=line)
        line = hand_edit("autolearn", lambda r: (r["predicate"].update(value=None), r.update(reason="  ")))
        check("a null predicate with a blank reason reads invalid-skip", "[invalid-skip]" in line, detail=line)
        def refuses_gated_skip_without_predicate(module):
            try:
                module.make_receipt(gate_sid, "autolearn", "skipped", [str(gate_input)], [], "no friction")
            except ValueError:
                return True
            return False
        mutant_source = Path(TOOL).read_text(encoding="utf-8").replace(
            "return 'a gated phase skip needs --predicate' if status == 'skipped' else None", "return None")
        mutant_path = Path(tmp) / "session_end_check_mutant.py"
        mutant_path.write_text(mutant_source, encoding="utf-8")
        mutant_spec = importlib.util.spec_from_file_location("session_end_check_mutant", mutant_path)
        mutant = importlib.util.module_from_spec(mutant_spec)
        mutant_spec.loader.exec_module(mutant)
        check("a copy that accepts a gated skip with no predicate is caught",
              refuses_gated_skip_without_predicate(checker) and not refuses_gated_skip_without_predicate(mutant))
        check("every gated stem is a phase", set(checker.GATED) <= {stem for _, stem, _ in checker.PHASES})
        for stem in ("worklog", "reindex_search"):
            rc, out = record_skip(stem, predicate_file(stem, False))
            check("%s accepts a checked false-predicate skip" % stem, rc == 0, detail=out)
            line = line_with(run(["--transcript", gate_path, "--resume", "--stage", "final"],
                                 {stem: True})[1], "(" + stem + ")")
            check("%s re-evaluates its false skip when input appears" % stem,
                  "[stale-skip]" in line, detail=line)
        check("hot_set_review is gated and not mandatory",
              any(stem == "hot_set_review" and not mandatory for _, stem, mandatory in checker.PHASES)
              and "hot_set_review" in checker.GATED)

    with tempfile.TemporaryDirectory() as tmp:
        blocked_sid = "blok0000-0000-0000-0000-000000000005"
        blocked_path = write_transcript(tmp, ALL_MANDATORY_LINES, sid=blocked_sid)
        write_receipts(tmp, blocked_sid)
        evidence = Path(tmp) / "fixture-evidence.json"
        blocked = checker.make_receipt(blocked_sid, "hot_set_review", "blocked", [str(evidence)], [str(evidence)],
                                       "review seat unavailable")
        (Path(tmp) / (blocked_sid + ".receipts") / "hot_set_review.json").write_text(json.dumps(blocked))
        archive = write_archive(tmp, [blocked_sid], name="archive_blocked.json")
        rc, out = run(["--transcript", blocked_path, "--artifacts", "--archive", archive])
        check("a blocked hot_set_review does not fail the close",
              rc == 0 and "[blocked]" in line_with(out, "hot_set_review"), detail=out[-500:])

    # --- --current-inputs: retired in favour of the review ledger ---
    with tempfile.TemporaryDirectory() as tmp:
        cur_path = write_transcript(tmp, PARTIAL_LINES, sid=SID)
        rc, out = run(['--transcript', cur_path, '--current-inputs', 'session_audit'])
        check("--current-inputs exits 2 naming review_ledger.py uncovered",
              rc == 2 and 'retired: use review_ledger.py uncovered (session_end.md Phase 1)' in out, detail=out[-300:])

    with tempfile.TemporaryDirectory() as config:
        repo_dir = os.path.join(config, 'repo')
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': config}):
            import session_digest
            store = str(session_digest.projects_dir(repo_dir))
            os.makedirs(store)
            Path(store, 'abcd1234-second.jsonl').write_text('{}\n')
            got = checker.transcript_for('abcd1234-second', repo_dir)
        check("a session under CLAUDE_CONFIG_DIR finds its transcript there",
              got is not None and os.path.dirname(got) == store, detail=repr(got))

    print()
    n_checks = check.calls
    print("%d/%d cases pass" % (n_checks - len(failures), n_checks))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
