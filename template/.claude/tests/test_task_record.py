#!/usr/bin/env python3
"""Proof for tools/task_record.py: one durable record per task, bounded, locked, checkable.

    python3 .claude/tests/test_task_record.py
"""
import importlib.util
import json
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "task_record.py")
sys.stdout.reconfigure(encoding="utf-8")
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail)[:300] if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("task_record", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cli(*args, cwd, env=None):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=cwd, timeout=60, env=env)


def worker(args):
    root, i = args
    os.environ["HARNESS_TASK_RECORD_DIR"] = root
    mod = load()
    mod.add_item("t-lock", "requirement", "req %d" % i)
    return True


def test_condition_command_args(tmp_path, monkeypatch):
    tr = load()
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(tmp_path / "records"))
    monkeypatch.delenv("CLAUDE_CODE_SIDECAR", raising=False)
    tr.open_record("command", "owner-session", "Command condition")
    transcript = tmp_path / "session.jsonl"
    # Trimmed from ffd4976e's live /session_digest owner row, UUID preserved.
    quote = "Please execute and /overnight this session to completion and /session_end."
    text = ("<command-message>session_digest</command-message>\n"
            "<command-name>/session_digest</command-name>\n"
            "<command-args>And handoff here. " + quote + "</command-args>")
    row = {"uuid": "59237639-d8c9-4fe2-abce-25896b568012", "type": "user",
           "message": {"role": "user", "content": text}}
    for content in (text, [{"type": "text", "text": text}]):
        row["message"]["content"] = content
        transcript.write_text(json.dumps(row) + "\n", encoding="utf-8")
        condition = tr.record_condition("command", "request", quote, "execute unattended",
                                        session_id="owner-session", transcript=transcript)
        assert condition["message_id"] == row["uuid"]
        assert tr._condition_row(condition)["status"] == "fresh"
    row["message"]["content"] = text.replace(quote, "Different owner words")
    transcript.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert tr._condition_row(condition)["status"] == "UNRESOLVED"


def test_condition_command_args_refuses_non_owner(tmp_path, monkeypatch):
    import pytest
    tr = load()
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(tmp_path / "records"))
    tr.open_record("refuse", "owner-session", "Refusal")
    quote = "make it perfect"
    text = "<command-name>/session_digest</command-name><command-args>" + quote + "</command-args>"
    transcript = tmp_path / "session.jsonl"
    rows = [
        {"type": "assistant", "message": {"role": "assistant", "content": text}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": text}]}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": text}},
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": text}},
        {"type": "user", "message": {"role": "user", "content":
            "<cross-session-message>" + text + "</cross-session-message>"}},
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>" + quote + "</command-name><command-args></command-args>"}},
    ]
    for row in rows:
        transcript.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with pytest.raises(tr.ConditionRefused, match="no owner turn"):
            tr.record_condition("refuse", "request", quote, "change rigor", transcript=transcript)
    assert not tr.load("refuse").get("conditions")


def test_rigor_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(tmp_path / "records"))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
    assert cli("open", "rigor", "--session", "rigor-session", "--title", "Rigor", cwd=tmp_path, env=env).returncode == 0
    for level in ("light", "normal", "thorough"):
        r = cli("set", "rigor", "--rigor", level, cwd=tmp_path, env=env)
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["rigor"] == level
    r = cli("set", "rigor", "--rigor", "unknown", cwd=tmp_path, env=env)
    assert r.returncode != 0 and "invalid choice" in r.stderr
    assert load().load("rigor")["rigor"] == "thorough"


def test_optional_kind_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(tmp_path / "records"))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
    assert cli("open", "kind", "--session", "kind-session", "--title", "Kind", cwd=tmp_path, env=env).returncode == 0
    args = ("triage", "kind", "--reach", "local", "--reversibility", "clean", "--design", "settled",
            "--grounding", "verified", "--oracle", "gate", "--size", "one-context", "--paths", "notes.txt")
    r = cli(*args, "--kind", "mechanical", cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["fields"]["kind"] == "mechanical"
    r = cli(*args, "--kind", "decision", "--reason", "new rule", cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr
    tr = load()
    assert any("kind mechanical -> decision" in d["text"] for d in tr.load("kind")["decisions"])
    assert "kind=decision" in tr.render_block("kind")
    r = cli(*tuple("standing" if a == "local" else a for a in args), cwd=tmp_path, env=env)
    assert r.returncode != 0 and "invalid choice" in r.stderr


def main():
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch
    for case in (test_optional_kind_cli, test_rigor_cli, test_condition_command_args,
                 test_condition_command_args_refuses_non_owner):
        with tempfile.TemporaryDirectory() as fixture, patch.dict(os.environ):
            try:
                case(Path(fixture), SimpleNamespace(
                    setenv=lambda k, v: os.environ.__setitem__(k, v),
                    delenv=lambda k, raising=False: os.environ.pop(k, None)))
                check(case.__name__, True)
            except Exception as exc:
                check(case.__name__, False, repr(exc))
    root = tempfile.mkdtemp(prefix="task_record_")
    os.environ["HARNESS_TASK_RECORD_DIR"] = root
    ledger = os.path.join(root, "review_ledger.jsonl")
    os.environ["HARNESS_REVIEW_LEDGER"] = ledger
    try:
        tr = load()
    except Exception as exc:
        check("tools/task_record.py imports", False, repr(exc))
        print("\n%d failure(s)" % len(FAILURES))
        return 1
    check("tools/task_record.py imports", True)

    with tempfile.TemporaryDirectory(prefix="accepted_executor_") as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "proof"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "proof@example.test"], check=True)
        subject = os.path.join(repo, "subject.txt")
        with open(subject, "w", encoding="utf-8") as fh:
            fh.write("base\n")
        subprocess.run(["git", "-C", repo, "add", "subject.txt"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
        base = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD:subject.txt"], text=True).strip()
        env = dict(os.environ, CLAUDE_PROJECT_DIR=repo)
        cli("open", "executor", "--session", "s1", "--title", "tracer", cwd=repo, env=env)
        launched = cli("job", "executor", "--run", "run.record.json", "--label", "exec", "--source", "sidecar",
                       "--state", "launched", "--subject", "subject.txt", cwd=repo, env=env)
        check("dispatch captures subject bases", launched.returncode == 0, launched.stderr)
        cli("job", "executor", "--run", "run.record.json", "--label", "exec", "--source", "sidecar",
            "--state", "done", cwd=repo, env=env)
        with open(subject, "w", encoding="utf-8") as fh:
            fh.write("accepted\n")
        accepted = cli("job", "executor", "--run", "run.record.json", "--label", "exec", "--source", "sidecar",
                       "--state", "accepted", "--role", "executor", "--subject", "subject.txt", cwd=repo, env=env)
        check("archive acceptance stores role and retained dispatch base", accepted.returncode == 0
              and json.loads(accepted.stdout).get("baseFiles") == {"subject.txt": base}, accepted.stderr)
        jobs = tr.all_jobs("executor")
        check("launched done accepted has one effective archived job", len(jobs) == 1
              and jobs[0].get("role") == "executor" and jobs[0].get("files", {}).get("subject.txt") != base, jobs)
        actor = os.path.join(repo, "missing-attestation.json")
        with open(actor, "w", encoding="utf-8") as fh:
            json.dump({"transport": "codex", "modelVersion": "gpt-6.1-sol", "servedModel": "client"}, fh)
        missing = cli("job", "executor", "--run", actor, "--label", "missing", "--source", "sidecar",
                      "--state", "accepted", "--role", "executor", "--subject", "subject.txt", cwd=repo, env=env)
        check("an accepted proxy job without attestation retains unknown actor", missing.returncode == 0
              and not json.loads(missing.stdout).get("modelObserved"), missing.stderr)
        with open(actor, "w", encoding="utf-8") as fh:
            json.dump([], fh)
        malformed = cli("job", "executor", "--run", actor, "--label", "malformed", "--source", "sidecar",
                        "--state", "accepted", "--role", "executor", "--subject", "subject.txt", cwd=repo, env=env)
        check("a malformed runtime record retains unknown actor rather than crashing", malformed.returncode == 0
              and not json.loads(malformed.stdout).get("modelObserved"), malformed.stderr)
        with open(subject, "w", encoding="utf-8") as fh:
            fh.write("staged\n")
        subprocess.run(["git", "-C", repo, "add", "subject.txt"], check=True)
        staged = subprocess.check_output(["git", "-C", repo, "rev-parse", ":subject.txt"], text=True).strip()
        with open(subject, "w", encoding="utf-8") as fh:
            fh.write("peer disk\n")
        indexed = cli("job", "executor", "--run", "index", "--label", "index", "--source", "workflow",
                      "--state", "accepted", "--role", "executor", "--subject", "subject.txt", cwd=repo, env=env)
        check("acceptance binds the staged blob rather than peer disk bytes", indexed.returncode == 0
              and json.loads(indexed.stdout).get("files") == {"subject.txt": staged}, indexed.stderr)
        subprocess.run(["git", "-C", repo, "rm", "-qf", "subject.txt"], check=True)
        deleted = cli("job", "executor", "--run", "delete", "--label", "delete", "--source", "workflow",
                     "--state", "accepted", "--role", "executor", "--subject", "subject.txt", cwd=repo, env=env)
        check("acceptance stores a staged deletion as null", deleted.returncode == 0
              and json.loads(deleted.stdout).get("files") == {"subject.txt": None}, deleted.stderr)
        absent = cli("job", "executor", "--run", "absent", "--label", "absent", "--source", "workflow",
                    "--state", "accepted", "--role", "executor", "--subject", "never-present.txt", cwd=repo, env=env)
        check("acceptance refuses a neither-present-nor-tracked subject", absent.returncode != 0
              and "neither present nor tracked" in absent.stderr, absent.stderr)

    with tempfile.TemporaryDirectory(prefix="lineage_roles_") as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "proof"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "proof@example.test"], check=True)
        from pathlib import Path
        from unittest.mock import patch
        import review_ledger
        Path(repo, "subject.txt").write_text("base\n", encoding="utf-8")
        Path(repo, "design.txt").write_text("design\n", encoding="utf-8")
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
        projects = Path(repo, "projects", "project")
        projects.mkdir(parents=True)
        (projects / "inline-author.jsonl").write_text(json.dumps({"type": "assistant",
            "message": {"model": "claude-opus-5-5"}}) + "\n", encoding="utf-8")
        import model_registry
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=repo, CLAUDE_CODE_SESSION_ID="inline-author"), \
                patch.object(model_registry, "session_projects_dir", return_value=str(projects.parent)):
            tr.open_record("roles", "inline-author", "role chain")
            tr.add_job("roles", "inline-author", "design", "session", "launched", subject=["subject.txt"])
            Path(repo, "subject.txt").write_text("draft\n", encoding="utf-8")
            architect = tr.add_job("roles", "inline-author", "design", "session", "accepted",
                                  role="architect", subject=["subject.txt"])
            check("inline architect has a transcript-observed actor", architect.get("modelObserved") is True
                  and architect.get("identityBasis") == "sessionTranscript", architect)
            try:
                tr.add_job("roles", "other-session", "fake", "session", "accepted", role="architect",
                           subject=["subject.txt"])
                check("inline authorship refuses another session", False)
            except KeyError:
                check("inline authorship refuses another session", True)
            try:
                reviewed = review_ledger.Ctx(Path(repo), "HEAD").hash_files(["subject.txt"])
                fold = tr.add_job("roles", "fold-record", "fold", "workflow", "accepted", role="fold",
                                  subject=["subject.txt"], base_files=reviewed, inputs=["design.txt"])
                check("checked fold keeps reviewed base and hashed inputs", fold.get("baseFiles") == reviewed
                      and fold.get("inputFiles", {}).get("design.txt") == review_ledger.Ctx(Path(repo), "HEAD").hash("design.txt"), fold)
            except TypeError as exc:
                check("checked fold keeps reviewed base and hashed inputs", False, str(exc))
            d = tr.add_item("roles", "decision", "accepted recommendation", evidence=[str(Path(repo, "design.txt"))])
            tr._mutate("roles", lambda state: state.setdefault("archived", []).append(state["decisions"].pop()))
            archived = [r for r in tr.archived_rows("roles") if r.get("kind") == "decision"]
            check("archive preserves decision evidence for lineage joins", archived[0].get("evidence") == d["evidence"], archived)
            try:
                check("all decisions deduplicate archive references", len(tr.all_decisions("roles")) == 1)
            except AttributeError as exc:
                check("all decisions deduplicate archive references", False, str(exc))
            with patch.object(review_ledger, "_journal_source", return_value=({}, {}, {},
                    {"fold": {"served_model": "gpt-6.1-sol"}}, {})):
                native = tr.add_job("roles", "native-fold", "fold", "workflow", "accepted", role="fold",
                                    subject=["subject.txt"])
            check("native accepted actor comes from its runtime row", native.get("model") == "gpt-6.1-sol"
                  and native.get("modelObserved") is True and native.get("identityBasis") == "workflowTranscript", native)
            lineage = review_ledger.subject_lineage(Path(repo), "roles", "plan:subject.txt", ["subject.txt"], "plan-check")
            check("inline architect joins the accepted blob chain", any(r.get("role") == "architect"
                  and r.get("group") == "anthropic" for r in lineage["primary"]), lineage)
            runtime = Path(repo, "legacy.record.json")
            runtime.write_text(json.dumps({"transport": "anthropic", "modelVersion": "claude-opus-5-5"}), encoding="utf-8")
            tr.add_job("roles", str(runtime), "legacy", "sidecar", "done")
            before = review_ledger.subject_lineage(Path(repo), "roles", "plan:subject.txt", ["subject.txt"], "design")
            backfill = tr.add_job("roles", str(runtime), "legacy", "sidecar", "accepted", role="architect", subject=["subject.txt"])
            after = review_ledger.subject_lineage(Path(repo), "roles", "plan:subject.txt", ["subject.txt"], "design")
            check("legacy backfill needs explicit role revision and runtime evidence", any("legacy" in r["reason"]
                  for r in before["unknown"]) and not any(r.get("run") == str(runtime) for r in after["unknown"])
                  and backfill.get("modelObserved") is True, (before, after))
            input_cli = cli("job", "roles", "--run", str(runtime), "--label", "input-cli", "--source", "sidecar",
                            "--state", "accepted", "--role", "drafter", "--subject", "subject.txt",
                            "--input", "design.txt", cwd=repo,
                            env=dict(os.environ, CLAUDE_PROJECT_DIR=repo))
            check("job CLI stores inherited input blobs through the hashing owner", input_cli.returncode == 0
                  and json.loads(input_cli.stdout).get("inputFiles") == {"design.txt": review_ledger.Ctx(Path(repo), "HEAD").hash("design.txt")},
                  input_cli.stderr)
            jobs = tr.all_jobs("roles")
            check("adopted architect survives archive with role actor and hashes", any(j.get("role") == "architect"
                  and j.get("model") == "claude-opus-5-5" and j.get("files") == architect["files"] for j in jobs), jobs)

    rec = tr.open_record("s1-drive", session_id="s1", title="Drive")
    check("open writes a record with every field present", all(k in rec for k in (
        "task_id", "session_id", "title", "opened", "updated", "requirements", "exclusions", "decisions", "scope",
        "phase", "active_jobs", "accepted_artifacts", "next_action")), sorted(rec))
    tr.set_fields("s1-drive", phase="build", next_action="run proofs")
    check("set updates phase and next action", tr.load("s1-drive")["phase"] == "build"
          and tr.load("s1-drive")["next_action"] == "run proofs")

    evidence = os.path.join(root, "evidence.txt")
    with open(evidence, "w", encoding="utf-8") as fh:
        fh.write("v1\n")
    d = tr.add_item("s1-drive", "decision", "keep the hook", evidence=[evidence])
    check("a decision gets an id and hashed evidence", d["id"] == "D1" and d["evidence"][0]["sha256"]
          and d["evidence"][0]["path"] == evidence, d)
    tr.add_item("s1-drive", "requirement", "no push")
    tr.add_item("s1-drive", "exclusion", "no .cs edits")
    tr.add_job("s1-drive", run_id="wf_1", label="review:a", source="workflow", state="running")
    check("a job row lands", tr.load("s1-drive")["active_jobs"][0]["run_id"] == "wf_1")
    # A sidecar record named relative to another directory, then by its longer path, is one job.
    tr.add_job("s1-drive", run_id="side/m1.record.json", label="m1", source="sidecar", state="running")
    tr.add_job("s1-drive", run_id=".claude/scratch/x/exec\\side/m1.record.json", label="m1", source="sidecar",
               state="done")
    m1 = [j for j in tr.all_jobs("s1-drive") if j.get("label") == "m1"]
    check("a path-suffix run id with the same label updates the one job row",
          len(m1) == 1 and m1[0]["state"] == "done", m1)
    tr.add_job("s1-drive", run_id="other/m1.record.json", label="m1", source="sidecar", state="running")
    check("a different path with the same basename and label is a second job",
          len([j for j in tr.all_jobs("s1-drive") if j.get("label") == "m1"]) == 2)

    # A superseding decision moves the decision it replaces out of the live record into the archive file.
    old = tr.add_item("s1-drive", "decision", "first ruling")
    new = tr.add_item("s1-drive", "decision", "corrected ruling", supersedes=[old["id"]])
    live = [d["id"] for d in tr.load("s1-drive")["decisions"]]
    archived = [r for r in tr.archived_rows("s1-drive") if r.get("id") == old["id"]]
    check("--supersedes moves the old decision to the archive with the id that replaced it",
          old["id"] not in live and new["id"] in live and archived and archived[0].get("supersededBy") == new["id"],
          (live, archived))
    check("an archived decision still resolves by id",
          {d["id"]: d["text"] for d in tr.all_decisions("s1-drive")}.get(old["id"]) == "first ruling")
    try:
        tr.add_item("s1-drive", "decision", "replaces nothing", supersedes=["D999"])
        check("--supersedes of an unknown live decision is refused", False, "no refusal")
    except KeyError as exc:
        check("--supersedes of an unknown live decision is refused", "D999" in str(exc), str(exc))
    check("the refused supersede wrote nothing", "replaces nothing" not in [d["text"] for d in tr.load("s1-drive")["decisions"]])
    earlier = tr.add_item("s1-drive", "decision", "ruling recorded before its correction")
    later = tr.add_item("s1-drive", "decision", "the correction, recorded first")
    tr.supersede("s1-drive", earlier["id"], by=later["id"])
    moved = [r for r in tr.archived_rows("s1-drive") if r.get("id") == earlier["id"]]
    check("supersede moves an existing decision to the archive under a live replacement",
          earlier["id"] not in [d["id"] for d in tr.load("s1-drive")["decisions"]]
          and moved and moved[0].get("supersededBy") == later["id"], moved)
    try:
        tr.supersede("s1-drive", later["id"], by="D998")
        check("supersede by a decision that is not live is refused", False, "no refusal")
    except KeyError as exc:
        check("supersede by a decision that is not live is refused", "D998" in str(exc), str(exc))
    try:
        tr.supersede("s1-drive", later["id"], by=later["id"])
        check("supersede of a decision by itself is refused", False, "no refusal")
    except KeyError as exc:
        check("supersede of a decision by itself is refused", later["id"] in str(exc), str(exc))
    check("the self-supersede refusal left the decision live",
          later["id"] in [d["id"] for d in tr.load("s1-drive")["decisions"]])

    big = "x" * (tr.MAX_BYTES + 200)
    try:
        tr.add_item("s1-drive", "requirement", big)
        check("a write past MAX_BYTES is refused and names the field", False, "no refusal")
    except tr.RecordTooLarge as exc:
        check("a write past MAX_BYTES is refused and names the field", "requirements" in str(exc), str(exc))
    check("the refused write left the record unchanged", len(tr.load("s1-drive")["requirements"]) == 1)

    # freshness check: fresh -> STALE -> MISSING; a decision without a reproduction is hash-only
    rows = tr.check_record("s1-drive")
    check("check reports fresh rows and hash-only for a decision without a reproduction",
          all(r["status"] == "fresh" for r in rows) and any(r.get("repro") == "hash-only" for r in rows), rows)
    with open(evidence, "w", encoding="utf-8") as fh:
        fh.write("v2\n")
    rows = tr.check_record("s1-drive")
    check("a changed evidence file reads STALE", any(r["status"] == "STALE" for r in rows), rows)
    os.remove(evidence)
    rows = tr.check_record("s1-drive")
    check("a removed evidence file reads MISSING", any(r["status"] == "MISSING" for r in rows), rows)
    # A legacy record's `archived` list and its finished jobs leave the capped record on the next write.
    tr.open_record("s1-legacy", session_id="s1l", title="Legacy")
    path = os.path.join(root, "s1-legacy.json")
    legacy = json.load(open(path, encoding="utf-8"))
    legacy["archived"] = [{"id": "D%d" % i, "text": "x" * 900, "at": "t"} for i in range(1, 30)]
    legacy["verification"] = [{"check": "proof", "result": "pass", "evidence": [], "at": "t"}]
    legacy["active_jobs"] = [{"run_id": "r%d" % i, "label": "j", "source": "workflow", "state": "done", "at": "t"}
                             for i in range(40)] + [{"run_id": "live", "label": "j", "source": "workflow",
                                                     "state": "running", "at": "t"}]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(legacy, fh)
    tr.set_fields("s1-legacy", phase="P2")
    after = tr.load("s1-legacy")
    side = os.path.join(root, "s1-legacy.archive.jsonl")
    side_rows = [json.loads(l) for l in open(side, encoding="utf-8")] if os.path.exists(side) else []
    check("the next write moves a legacy archived list to the archive file",
          "archived" not in after and sum(r.get("kind") == "decision" for r in side_rows) == 29,
          (list(after), len(side_rows)))
    check("the next write moves finished jobs to the archive file and keeps running ones",
          [j["run_id"] for j in after["active_jobs"]] == ["live"]
          and sum(r.get("kind") == "job" for r in side_rows) == 40, after["active_jobs"])
    check("a new decision id skips ids held only in the archive file",
          tr.add_item("s1-legacy", "decision", "fresh")["id"] == "D30")
    check("a legacy record's verification rows still load, check and render",
          tr.check_record("s1-legacy") is not None and tr.render_block("s1-legacy").startswith("[task-record]"))

    # usage errors surface as KeyError (the CLI's REFUSED path), never as a substitute OSError
    try:
        tr.add_item("never-opened", "requirement", "x")
        check("a mutator on a record that was never opened raises KeyError naming the task", False, "no error")
    except KeyError as exc:
        check("a mutator on a record that was never opened raises KeyError naming the task",
              "never-opened" in str(exc), str(exc))
    except Exception as exc:  # noqa: BLE001
        check("a mutator on a record that was never opened raises KeyError naming the task", False, repr(exc))
    r = cli("add", "never-opened", "requirement", "x", cwd=root)
    check("the CLI reports a usage error as REFUSED with exit 1, not a traceback",
          r.returncode == 1 and r.stderr.startswith("REFUSED:") and "Traceback" not in r.stderr,
          (r.returncode, r.stderr[:200]))

    tr.open_record("s1-later", session_id="s1", title="Later")
    check("active picks the newest record of the session", tr.active("s1")["task_id"] == "s1-later")
    check("active is None for an unknown session", tr.active("nope") is None)
    # A record opened with the 8-char sid the hooks print must re-inject for the full session id the
    # compaction hook passes, and the reverse (2026-09-22: bench-ready-0922 opened as `fd4ff546`
    # was reported as "No task record" after compaction).
    full = "fd4ff546-999d-4ad3-add7-12dfa2498b7c"
    tr.open_record("sid8-drive", session_id=full[:8], title="Opened with sid8")
    got = tr.active(full)
    check("a record opened with the 8-char sid is active for the full session id",
          bool(got) and got["task_id"] == "sid8-drive", got and got.get("task_id"))
    other = "0a1b2c3d-1111-2222-3333-444455556666"
    tr.open_record("full-drive", session_id=other, title="Opened with the full id")
    got = tr.active(other[:8])
    check("a record opened with the full session id is active for its 8-char sid",
          bool(got) and got["task_id"] == "full-drive", got and got.get("task_id"))
    check("a 7-char prefix matches no session", tr.active(full[:7]) is None)

    # bounded block: requirements and exclusions whole, decisions water-filled, omissions named
    tr.open_record("s1-block", session_id="s1b", title="Block")
    for i in range(3):
        tr.add_item("s1-block", "requirement", "requirement %d" % i)
    for i in range(18):
        tr.add_item("s1-block", "decision", "decision %d " % i + "y" * 60)
    check("18 decisions of ~75 B fit under the record cap (first data point for the cap)",
          len(json.dumps(tr.load("s1-block")).encode("utf-8")) <= tr.MAX_BYTES, len(json.dumps(tr.load("s1-block")).encode("utf-8")))
    blk = tr.render_block("s1-block", max_bytes=700)
    check("the block fits its byte cap", len(blk.encode("utf-8")) <= 700, len(blk.encode("utf-8")))
    check("requirements stay whole in a tight block", all(("requirement %d" % i) in blk for i in range(3)), blk)
    check("omitted decisions are named with the show command", "omitted" in blk and "task_record.py show s1-block" in blk, blk)
    check("an empty session renders no block", tr.render_block("missing-task", max_bytes=700) == "")
    # The 30-compaction audit (2026-10-01): a resumed session re-searched for the design doc, plan and
    # worklog item a task reads. The record's scope files ride in its block.
    tr.open_record("s1-files", session_id="s1f", title="Files")
    tr.set_fields("s1-files", files=["vault:Claude/Meta/DOC-MARKER.md", ".claude/plans/PLAN-MARKER.md"])
    fblk = tr.render_block("s1-files", max_bytes=700)
    check("a record's scope files ride in its block", "DOC-MARKER" in fblk and "PLAN-MARKER" in fblk, fblk)
    check("a record without files renders no files line", "files:" not in tr.render_block("s1-block", max_bytes=700))

    # CLI round trip and locking under concurrent writers
    r = cli("open", "c-1", "--session", "c", "--title", "CLI", cwd=root)
    r2 = cli("show", "c-1", cwd=root)
    check("CLI open then show round-trips", r.returncode == 0 and r2.returncode == 0
          and json.loads(r2.stdout)["task_id"] == "c-1", (r.stderr, r2.stdout[:200]))
    tr.open_record("t-lock", session_id="l", title="Lock")
    with multiprocessing.Pool(4) as pool:
        pool.map(worker, [(root, i) for i in range(12)])
    check("12 concurrent adds through the lock all survive", len(tr.load("t-lock")["requirements"]) == 12,
          len(tr.load("t-lock")["requirements"]))

    # triage: six fields plus the registry-derived block, refused when lowered, rebound per session
    proj = os.path.join(root, "proj")
    skill = os.path.join(proj, ".claude", "skills", "project_subsystems")
    os.makedirs(skill)
    with open(os.path.join(skill, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"sensitivity_tags": [
            {"id": "guard", "paths": ["hooks/**"], "sets": {"reach": "shared", "oracle": "new-test"}, "source": "t:1"},
            {"id": "wire", "paths": ["**/*.tscn"], "hunk": "^\\[node", "sets": {"oracle": "runtime"},
             "probe": "launch it", "source": "t:2"}]}, fh)

    def tri_env(session):
        return dict(os.environ, CLAUDE_PROJECT_DIR=proj, CLAUDE_CODE_SESSION_ID=session)

    def triage_cli(task, session, reach="shared", oracle="gate+new-test", size="one-context",
                   paths=("hooks/a.py", "a.tscn"), extra=()):
        return cli("triage", task, "--reach", reach, "--reversibility", "clean", "--design", "settled",
                   "--grounding", "verified", "--oracle", oracle, "--size", size, "--paths", *paths, *extra,
                   cwd=root, env=tri_env(session))

    def for_session(session):
        fn = getattr(tr, "triage_for_session", None)
        return fn(session) if fn else {"missing": "triage_for_session"}

    def stored(name):
        return (((tr.load("tri-1") or {}).get("triage") or {}).get("fields") or {}).get(name)

    tr.open_record("tri-1", session_id="sess-one-aaaa", title="Triage")
    r = triage_cli("tri-1", "sess-one-aaaa", extra=("--outward", "edit the PR body"))
    got = (tr.load("tri-1") or {}).get("triage") or {}
    check("triage stores the six fields", r.returncode == 0 and got.get("fields", {}).get("reach") == "shared"
          and got["fields"].get("size") == "one-context"
          and sorted(got["fields"].get("oracle", [])) == ["gate", "new-test"]
          and got["fields"].get("reversibility") == "clean", (r.stderr, got))
    derived = got.get("derived") or {}
    check("triage stores the derived block: reach, oracle and the matches, a conditional one marked",
          derived.get("reach") == "shared" and derived.get("oracle") == ["new-test"]
          and any(m.get("tag") == "guard" and not m.get("conditional") for m in derived.get("matches", []))
          and any(m.get("tag") == "wire" and m.get("conditional") for m in derived.get("matches", [])), derived)
    check("triage stores the planned paths, the outward actions and a stamp",
          got.get("paths") == ["hooks/a.py", "a.tscn"] and got.get("outward") == ["edit the PR body"]
          and got.get("at"), got)
    r = triage_cli("tri-1", "sess-one-aaaa", reach="local")
    check("a recorded reach below the derived reach is refused, naming field and tag",
          r.returncode == 1 and "reach" in r.stderr and "guard" in r.stderr and "Traceback" not in r.stderr, r.stderr)
    r = triage_cli("tri-1", "sess-one-aaaa", oracle="gate", extra=("--reason", "x"))
    check("an oracle set missing a derived value is refused, naming the value and the tag",
          r.returncode == 1 and "new-test" in r.stderr and "guard" in r.stderr, r.stderr)
    check("a refused triage leaves the stored fields unchanged",
          stored("reach") == "shared")
    r = triage_cli("tri-1", "sess-one-aaaa", size="milestones")
    check("a re-triage without --reason exits non-zero and changes nothing",
          r.returncode != 0 and stored("size") == "one-context",
          (r.returncode, r.stderr))
    r = triage_cli("tri-1", "sess-one-aaaa", size="milestones", extra=("--reason", "the diff grew"))
    texts = [d.get("text", "") for d in tr.load("tri-1").get("decisions", [])]
    check("a re-triage logs a decision naming the field, old, new and reason",
          r.returncode == 0 and "re-triage: size one-context -> milestones; the diff grew" in texts, (r.stderr, texts))
    check("the re-triage stored the new field", stored("size") == "milestones")
    check("triage_for_session returns the fields of the session's record",
          (for_session("sess-one-aaaa") or {}).get("reach") == "shared",
          for_session("sess-one-aaaa"))
    r = triage_cli("tri-1", "sess-two-bbbb", size="milestones", extra=("--reason", "resumed in a new session"))
    check("a triage from a second session rebinds the record to it",
          r.returncode == 0 and (tr.load("tri-1") or {}).get("session_id") == "sess-two-bbbb"
          and (for_session("sess-two-bbbb") or {}).get("size") == "milestones"
          and for_session("sess-one-aaaa") is None, (r.stderr, tr.load("tri-1").get("session_id")))
    tr.open_record("tri-none", session_id="sess-three-cccc", title="No triage")
    check("triage_for_session is None when the session's record holds no triage",
          for_session("sess-three-cccc") is None and for_session("nobody") is None)
    blk = tr.render_block("tri-1")
    lines = [l for l in blk.splitlines() if l.startswith("triage:")]
    check("block prints one triage: line with the six values", len(lines) == 1 and all(
        k in lines[0] for k in ("reach=shared", "reversibility=clean", "design=settled", "grounding=verified",
                                "oracle=gate+new-test", "size=milestones")), blk)
    tr.open_record("tri-empty", session_id="empty-oracle", title="Empty oracle")
    r = triage_cli("tri-empty", "empty-oracle", reach="local", oracle="none", paths=("notes.txt",))
    check("task preview prints oracle=none for an empty oracle",
          r.returncode == 0 and "oracle=none" in tr.render_block("tri-empty"), (r.stderr, tr.render_block("tri-empty")))
    r = triage_cli("tri-empty", "empty-oracle", reach="local", oracle="gate", paths=("notes.txt",),
                   extra=("--reason", "add gate"))
    check("re-triage names the empty oracle as none",
          r.returncode == 0 and any("oracle none -> gate" in d["text"] for d in tr.load("tri-empty")["decisions"]), r.stderr)

    tr.open_record("tri-retired", session_id="sess-four-dddd", title="Retired size")
    r = triage_cli("tri-retired", "sess-four-dddd", size="multi-part")
    got_size = (((tr.load("tri-retired") or {}).get("triage") or {}).get("fields") or {}).get("size")
    check("a retired --size value records as its replacement, the reading triage.py gives old trailers",
          r.returncode == 0 and got_size == "milestones", (r.returncode, r.stderr[-300:], got_size))
    tr.open_record("tri-nopaths", session_id="sess-one-aaaa", title="No paths")
    r = cli("triage", "tri-nopaths", "--reach", "local", "--reversibility", "clean", "--design", "settled",
            "--grounding", "verified", "--oracle", "gate", "--size", "one-context", cwd=root, env=tri_env("sess-one-aaaa"))
    check("a triage with no --paths is refused, not recorded against an empty floor",
          r.returncode == 1 and r.stderr.startswith("REFUSED:") and "paths" in r.stderr
          and not (tr.load("tri-nopaths") or {}).get("triage"), (r.returncode, r.stderr))
    try:
        tr.record_triage("tri-1", {"reach": "shared", "reversibility": "clean", "design": "settled",
                                   "grounding": "verified", "oracle": ["gate", "new-test"], "size": "milestones"},
                         ["hooks/a.py"])
        kind = "no error"
    except tr.TriageRefused:
        kind = "TriageRefused"
    except KeyError:
        kind = "KeyError"
    check("a re-triage without a reason raises TriageRefused, not KeyError", kind == "TriageRefused", kind)
    r = triage_cli("tri-1", "sess-one-aaaa", reach="local")
    check("the floor refusal says the derived floor, not the previous record",
          "below the derived" in r.stderr, r.stderr)
    r = triage_cli("never-opened", "sess-one-aaaa")
    check("triage on a task never opened is refused, not a traceback",
          r.returncode == 1 and r.stderr.startswith("REFUSED:") and "Traceback" not in r.stderr, r.stderr)

    # conditions: an owner deadline or request, bound to the owner turn whose text holds the quote
    cfg = os.path.join(root, "cfg")
    sid = "cond-sess-0001"
    quote = "I need this by tonight"
    transcript = os.path.join(cfg, "projects", re.sub(r"[^A-Za-z0-9]", "-", proj), sid + ".jsonl")
    os.makedirs(os.path.dirname(transcript))

    def cond_env(**extra):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SIDECAR"}
        env.update(CLAUDE_PROJECT_DIR=proj, CLAUDE_CONFIG_DIR=cfg, CLAUDE_CODE_SESSION_ID=sid, **extra)
        return env

    def plant(rows):
        with open(transcript, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def owner_row(uuid, text, **extra):
        return dict({"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}}, **extra)

    def cond_cli(kind="urgency", q=quote, extra_env=None, task="cond-1"):
        return cli("condition", task, "--kind", kind, "--quote", q, "--effect", "review runs beside execution",
                   cwd=root, env=cond_env(**(extra_env or {})))

    def check_cli(task="cond-1"):
        return cli("check", task, cwd=root, env=cond_env())

    def stored_conditions(task="cond-1"):
        return (tr.load(task) or {}).get("conditions")

    tr.open_record("cond-1", session_id=sid, title="Conditions")
    plant([owner_row("u0", "start the work"), owner_row("u1", quote + ", please")])
    r = cond_cli()
    row = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    check("condition stores C1 with the uuid of the owner turn holding the quote",
          r.returncode == 0 and row.get("id") == "C1" and row.get("kind") == "urgency"
          and row.get("message_id") == "u1" and row.get("session_id") == sid and row.get("delegate") is False
          and row.get("effect") == "review runs beside execution" and row.get("transcript")
          and (stored_conditions() or [{}])[0].get("id") == "C1", (r.returncode, r.stdout[:300], r.stderr[:300]))
    r = check_cli()
    check("check prints a fresh condition row and exits 0",
          r.returncode == 0 and re.search(r"^fresh\s+C1\s+condition", r.stdout, re.M) is not None,
          (r.returncode, r.stdout, r.stderr[:200]))
    plant([owner_row("u0", "start the work"), owner_row("u1", quote + ", please"),
           owner_row("u2", "drop the plan check")])
    r = cond_cli(kind="request", q="drop the plan check")
    check("a later condition is C2 and binds its own turn",
          r.returncode == 0 and json.loads(r.stdout).get("id") == "C2"
          and json.loads(r.stdout).get("message_id") == "u2", (r.returncode, r.stdout[:200], r.stderr[:200]))

    ask = "Run the review beside execution?"
    plant([owner_row("u0", "start the work"),
           {"type": "user", "uuid": "a1", "message": {"role": "user", "content": [
               {"type": "tool_result", "tool_use_id": "q1", "content": 'User has answered: "%s"="Yes, beside it"' % ask}]},
            "toolUseResult": {"questions": [{"question": ask}], "answers": {ask: "Yes, beside it"}}}])
    r = cond_cli(kind="request", q="Yes, beside it")
    check("a ruling the owner made by AskUserQuestion answer is stored against the answer's turn",
          r.returncode == 0 and json.loads(r.stdout).get("message_id") == "a1", (r.returncode, r.stdout[:200], r.stderr[:200]))
    r = cond_cli(kind="request", q=ask)
    check("the assistant's question beside an answer is not the owner's words",
          r.returncode == 1 and r.stderr.startswith("REFUSED:"), (r.returncode, r.stdout[:200], r.stderr[:200]))

    tr.open_record("cond-2", session_id=sid, title="Refusals")
    for label, rows in (
            ("a sidechain row", [owner_row("s1", quote, isSidechain=True)]),
            ("a tool-result row", [{"type": "user", "uuid": "t1", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": quote}]}}]),
            ("a cross-session envelope row", [owner_row(
                "e1", '<cross-session-message from="peer">%s</cross-session-message>' % quote)]),
            ("no row at all", [owner_row("n1", "something else")])):
        plant(rows)
        r = cond_cli(task="cond-2")
        check("condition is refused when the quote sits only in %s" % label,
              r.returncode == 1 and r.stderr.startswith("REFUSED:") and "owner turn" in r.stderr
              and not stored_conditions("cond-2"), (r.returncode, r.stderr[:200]))

    tr.open_record("cond-3", session_id=sid, title="Check statuses")
    plant([owner_row("u1", quote)])
    cond_cli(task="cond-3")
    plant([owner_row("u1", "the words changed")])
    r = check_cli("cond-3")
    check("check prints UNRESOLVED when the turn no longer holds the quote",
          r.returncode == 1 and re.search(r"^UNRESOLVED\s+C1", r.stdout, re.M) is not None, (r.returncode, r.stdout))
    plant([owner_row("u9", "another turn")])
    r = check_cli("cond-3")
    check("check prints UNRESOLVED when no row carries the stored uuid",
          r.returncode == 1 and "UNRESOLVED" in r.stdout, (r.returncode, r.stdout))
    os.remove(transcript)
    r = check_cli("cond-3")
    check("check prints MISSING when the transcript is gone",
          r.returncode == 1 and re.search(r"^MISSING\s+C1", r.stdout, re.M) is not None, (r.returncode, r.stdout))

    tr.open_record("cond-4", session_id=sid, title="Delegate")
    plant([owner_row("u1", quote)])
    r = cond_cli(task="cond-4", extra_env={"CLAUDE_CODE_SIDECAR": "1"})
    check("a delegate's condition is stored with delegate true, not refused",
          r.returncode == 0 and json.loads(r.stdout).get("delegate") is True
          and (stored_conditions("cond-4") or [{}])[0].get("delegate") is True, (r.returncode, r.stderr[:200]))
    r = check_cli("cond-4")
    check("check prints DELEGATE and exits 1",
          r.returncode == 1 and re.search(r"^DELEGATE\s+C1", r.stdout, re.M) is not None, (r.returncode, r.stdout))

    tr.open_record("cond-5", session_id=sid, title="Retriage")
    plant([owner_row("u1", quote)])
    triage_cli("cond-5", sid)
    cond_cli(task="cond-5")
    r = triage_cli("cond-5", sid, size="milestones", extra=("--reason", "the diff grew"))
    kept = stored_conditions("cond-5")
    check("a re-triage keeps the recorded conditions",
          r.returncode == 0 and bool(kept) and kept[0].get("id") == "C1", (r.stderr[:200], kept))
    blk_lines = tr.render_block("cond-5").splitlines()
    idx = [i for i, line in enumerate(blk_lines) if line.startswith("triage:")]
    check("block prints the condition line right after the triage: line",
          bool(idx) and idx[0] + 1 < len(blk_lines)
          and blk_lines[idx[0] + 1] == '- condition C1 urgency: review runs beside execution (owner: "%s")' % quote,
          blk_lines)
    check("a record with no condition renders no condition line",
          "condition" not in tr.render_block("tri-1"))
    r = cond_cli(kind="deadline", task="cond-5")
    check("an unknown kind is refused by the CLI", r.returncode != 0)

    # spend conditions (the owner's lean posture): effect lean or normal, never a delegate, always an until
    tr.open_record("cond-6", session_id=sid, title="Spend")
    plant([owner_row("u1", quote)])
    spend_until = "2031-01-01T00:00:00Z"

    def spend_cli(effect="lean", until=spend_until, extra_env=None):
        args = ["condition", "cond-6", "--kind", "spend", "--quote", quote, "--effect", effect]
        if until is not None:
            args += ["--until", until]
        return cli(*args, cwd=root, env=cond_env(**(extra_env or {})))

    refusals = (
        ("an effect other than lean or normal", spend_cli(effect="fast")),
        ("a delegate's CLAUDE_CODE_SIDECAR", spend_cli(extra_env={"CLAUDE_CODE_SIDECAR": "1"})),
        ("no until", spend_cli(until=None)),
        ("an until that is not an ISO time with a zone", spend_cli(until="next tuesday")))
    for label, r in refusals:
        check("a spend condition is refused for %s" % label,
              r.returncode == 1 and r.stderr.startswith("REFUSED:") and "Traceback" not in r.stderr,
              (r.returncode, r.stderr[:200]))
    check("the refused spend conditions store nothing", not stored_conditions("cond-6"), stored_conditions("cond-6"))
    r = spend_cli()
    row = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    check("a spend condition with the owner's quote and an until stores C1 with effect lean and the until",
          r.returncode == 0 and row.get("id") == "C1" and row.get("kind") == "spend" and row.get("effect") == "lean"
          and row.get("until") == spend_until and row.get("message_id") == "u1" and row.get("delegate") is False,
          (r.returncode, r.stdout[:300], r.stderr[:200]))
    r = check_cli("cond-6")
    check("task_record check re-resolves a spend condition as fresh",
          r.returncode == 0 and re.search(r"^fresh\s+C1\s+condition", r.stdout, re.M) is not None,
          (r.returncode, r.stdout, r.stderr[:200]))

    # Decisions use the four-kind evidence contract, not an exit-code-only shell command.
    tr.open_record("ev-1", session_id=sid, title="Evidence items")
    plant([owner_row("u1", quote)])

    def evidence_cli(*items, task="ev-1", extra=()):
        flags = [part for item in items for part in ("--evidence-item", json.dumps(item))]
        return cli("add", task, "decision", "keep the premise", *flags, *extra,
                   cwd=root, env=cond_env())

    r = evidence_cli({"owner": "the owner never said this"})
    check("a decision with an unheld owner quote is refused at add",
          r.returncode == 1 and "REFUSED:" in r.stderr and "quote-absent" in r.stderr
          and tr.load("ev-1")["decisions"] == [], (r.returncode, r.stderr))
    cited = os.path.join(root, "cited.txt")
    with open(cited, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("the full cited evidence\n")
    items = [{"owner": quote}, {"file": cited, "quote": "full cited evidence"},
             {"url": "https://example.test/source", "quote": "full cited evidence", "artifact": cited},
             {"command": "git rev-parse --is-inside-work-tree", "output": "true"}]
    subprocess.run(["git", "init", proj], capture_output=True, check=True)
    r = evidence_cli(*items)
    d = json.loads(r.stdout) if r.returncode == 0 else {}
    check("repeatable --evidence-item accepts and stores all four kinds",
          r.returncode == 0 and d.get("evidence_items") == items, (r.stderr, d))
    r = cli("check", "ev-1", "--rerun", cwd=root, env=cond_env())
    check("check --rerun verifies stored evidence items through the checker",
          r.returncode == 0 and "VERIFIED" in r.stdout, (r.stdout, r.stderr))
    subprocess.run(["git", "config", "core.bare", "true"], cwd=proj, check=True)
    r = cli("check", "ev-1", cwd=root, env=cond_env())
    check("check without --rerun does not execute a stored command item",
          r.returncode == 0 and "stored" in r.stdout, (r.stdout, r.stderr))
    r = cli("check", "ev-1", "--rerun", cwd=root, env=cond_env())
    check("check --rerun detects changed command output, not just its exit code",
          r.returncode == 1 and "UNVERIFIED" in r.stdout and "output-absent" in r.stdout,
          (r.stdout, r.stderr))
    for item in ({"url": "https://example.test/unfetched", "quote": "words"},
                 {"command": "python3 -c 'print(1)'", "output": "1"}, {"file": cited}):
        r = evidence_cli(item)
        check("unchecked or malformed evidence is refused: " + next(iter(item)),
              r.returncode == 1 and "REFUSED:" in r.stderr and "Traceback" not in r.stderr, r.stderr)
    r = cli("add", "ev-1", "decision", "bad JSON", "--evidence-item", "{bad", cwd=root, env=cond_env())
    check("malformed evidence JSON is refused without a traceback",
          r.returncode == 1 and "REFUSED:" in r.stderr and "Traceback" not in r.stderr, r.stderr)
    r = evidence_cli(extra=("--reproduction", "must-not-run"))
    check("retired --reproduction parses, refuses and prints --evidence-item replacement",
          r.returncode == 1 and "retired" in r.stderr and "--evidence-item" in r.stderr
          and "unrecognized arguments" not in r.stderr and len(tr.load("ev-1")["decisions"]) == 1,
          (r.returncode, r.stderr))
    r = cli("add", "ev-1", "requirement", "wrong kind", "--evidence-item", json.dumps(items[0]),
            cwd=root, env=cond_env())
    check("evidence items on a non-decision are refused, not silently lost",
          r.returncode == 1 and "decision" in r.stderr, r.stderr)
    legacy_path = os.path.join(root, "ev-legacy.json")
    tr.open_record("ev-legacy", session_id=sid, title="Old reproduction")
    legacy = tr.load("ev-legacy")
    legacy["decisions"] = [{"id": "D1", "text": "old", "evidence": [], "reproduction": "must-not-run"}]
    with open(legacy_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(legacy, fh)
    r = cli("check", "ev-legacy", "--rerun", cwd=root, env=cond_env())
    check("a legacy reproduction is not run and names its replacement",
          r.returncode == 1 and "--evidence-item" in r.stdout and "retired" in r.stdout
          and "Traceback" not in r.stderr, (r.stdout, r.stderr))

    # review-ledger pointer: one line when the ledger holds rows naming the task
    def ledger_rows(*rows, raw=()):
        with open(ledger, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join([json.dumps(r) for r in rows] + list(raw)) + "\n")

    def led(task, outcome):
        return {"v": 1, "id": "run:seat:m", "mandate": "tq", "outcome": outcome, "task": task}

    tr.open_record("led-1", session_id="sled", title="Ledger")
    tr.add_item("led-1", "requirement", "keep it small")
    check("no ledger file renders no review line", "review:" not in tr.render_block("led-1"))
    ledger_rows(led("other", "clean"))
    check("rows naming another task render no review line", "review:" not in tr.render_block("led-1"))
    ledger_rows(led("led-1", "clean"), led("led-1", "findings"), led("led-1", "incomplete"), led("other", "clean"),
                raw=("{not json",))
    blk = tr.render_block("led-1")
    want = "review: 3 ledger rows, 1 non-covering outcomes (review_ledger.py rows --task led-1)"
    check("a planted ledger renders one review line counting rows and non-covering rows",
          blk.splitlines().count(want) == 1, blk)
    cli_out = cli("block", "led-1", cwd=root, env=dict(os.environ)).stdout
    check("the block CLI prints the review line", want in cli_out, cli_out)
    ledger_rows(led("led-1", "clean"), led("led-1", "failed"), led("led-1", "not-reached"), led("led-1", "blocked"))
    check("failed, not-reached and blocked rows count as non-covering outcomes",
          "review: 4 ledger rows, 3 non-covering outcomes" in tr.render_block("led-1"))
    tight = tr.render_block("led-1", max_bytes=tr.BLOCK_BYTES)
    check("the block with the review line stays under BLOCK_BYTES", len(tight.encode("utf-8")) <= tr.BLOCK_BYTES,
          len(tight.encode("utf-8")))
    for i in range(18):
        tr.add_item("led-1", "decision", "decision %d " % i + "y" * 60)
    fat = tr.render_block("led-1")
    check("a record with many decisions keeps the review line and the byte cap",
          "review: 4 ledger rows" in fat and len(fat.encode("utf-8")) <= tr.BLOCK_BYTES, len(fat.encode("utf-8")))
    os.environ["HARNESS_REVIEW_LEDGER"] = root
    check("an unreadable ledger path drops the line and keeps the block", "review:" not in tr.render_block("led-1")
          and "[task-record] led-1" in tr.render_block("led-1"))
    os.environ["HARNESS_REVIEW_LEDGER"] = ledger

    tr.open_record("sl-1", session_id="s1", title="Slices")
    check("open writes an empty slices list", tr.load("sl-1").get("slices") == [])
    tr.add_slice("sl-1", "S1", "in_progress")
    tr.add_slice("sl-1", "S2", "pending")
    tr.add_slice("sl-1", "S1", "completed")
    rows = tr.load("sl-1")["slices"]
    check("a slice row upserts by id and keeps order",
          [(r["id"], r["state"]) for r in rows] == [("S1", "completed"), ("S2", "pending")], rows)
    check("the block lists each slice with its state",
          "- slice S1 completed" in tr.render_block("sl-1") and "- slice S2 pending" in tr.render_block("sl-1"))
    out = cli("slice", "sl-1", "--id", "S2", "--state", "in_progress", cwd=root, env=dict(os.environ))
    check("the slice CLI updates a row", out.returncode == 0 and tr.load("sl-1")["slices"][1]["state"] == "in_progress",
          out.stderr)
    bad = cli("slice", "sl-1", "--id", "S3", "--state", "bogus", cwd=root, env=dict(os.environ))
    check("the slice CLI refuses an unknown state", bad.returncode != 0)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for f in FAILURES:
        print("  " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
