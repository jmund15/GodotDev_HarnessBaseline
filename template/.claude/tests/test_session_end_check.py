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


def write_transcript(tmpdir, lines, sid=SID):
    path = os.path.join(tmpdir, sid + ".jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")
    return path


def write_archive(tmpdir, session_ids, name="archive.json"):
    path = os.path.join(tmpdir, name)
    entries = [{"session_id": sid, "id": i} for i, sid in enumerate(session_ids)]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"structured_entries": entries, "legacy_entries": []}, fh)
    return path


def write_receipts(tmpdir, sid):
    directory = Path(tmpdir) / (sid + '.receipts')
    directory.mkdir(exist_ok=True)
    evidence = Path(tmpdir) / 'fixture-evidence.json'
    evidence.write_text('{}')
    for stem in checker.required_phases('precommit'):
        receipt = checker.make_receipt(sid, stem, 'completed', [str(evidence)],
            [str(evidence)], 'Synthetic phase proof')
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

    print()
    n_checks = check.calls
    print("%d/%d cases pass" % (n_checks - len(failures), n_checks))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
