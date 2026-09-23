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


def run(args):
    transcript = Path(args[args.index('--transcript') + 1])
    args += ['--session', transcript.stem,
             '--receipts', str(transcript.parent / (transcript.stem + '.receipts')),
             '--repo', str(transcript.parent)]
    r = subprocess.run([sys.executable, '-B', "-X", "utf8", TOOL] + args,
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
        transcripts = os.path.join(home, ".claude", "projects", session_end_check.project_key(repo))
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
        git(repo, "push", "-q")
        rc, out = run_final()
        check("...and passes once the branch is pushed",
              rc == 0 and "[completed]" in line_with(out, "commit_push"), detail=out[-500:])
        # Phase 8 is hygiene: a concurrent session's lock on the index blocks it, never the close.
        blocked = checker.make_receipt(final_sid, "reindex_search", "blocked", [str(ev)], [str(ev)],
                                       "EBUSY: another session holds search.db")
        (receipts_dir / "reindex_search.json").write_text(json.dumps(blocked))
        rc, out = run_final()
        check("a blocked reindex does not fail the final close",
              rc == 0 and "[blocked]" in line_with(out, "reindex_search"), detail=out[-500:])
        mandatory_blocked = checker.make_receipt(final_sid, "worklog", "blocked", [str(ev)], [str(ev)], "x")
        (receipts_dir / "worklog.json").write_text(json.dumps(mandatory_blocked))
        rc, out = run_final()
        check("...while a blocked mandatory phase still does", rc == 1, detail=out[-500:])

    print()
    n_checks = check.calls
    print("%d/%d cases pass" % (n_checks - len(failures), n_checks))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
