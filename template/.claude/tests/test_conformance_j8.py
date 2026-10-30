#!/usr/bin/env python3
"""Executed J8 authority/recovery cases; no destructive cleanup or model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

CLAUDE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CLAUDE / "tools"), str(CLAUDE / "hooks")]
import triage
import overnight_ask_guard as overnight
import denial_coach
import _regenerable_clone as regenerable

TRAILER = "Triage: reach=local design=settled grounding=verified oracle=gate size=one-context"
Q = ("**Q1 — Which option? Options: A or B. Park: owner-domain — owner preference. "
     "Recommendation: A{suffix}. Default: nothing merged.")


class Conformance(unittest.TestCase):
    def test_rv6_optional_trailer(self):
        fields, error = triage.parse_trailer(TRAILER)
        self.assertIsNone(error)
        self.assertNotIn("reversibility", fields)
        self.assertEqual(triage.parse_trailer(TRAILER + " reversibility=residue")[0]["reversibility"], "residue")
        for extra in (" reversibility=invalid", " reach=shared", " surprise=clean"):
            self.assertIsNotNone(triage.parse_trailer(TRAILER + extra)[1])
        for key in ("reach", "design", "grounding", "oracle", "size"):
            incomplete = " ".join(t for t in TRAILER.split() if not t.startswith(key + "="))
            self.assertIsNotNone(triage.parse_trailer(incomplete)[1])

    def test_rv6_consumers_and_full_task_schema(self):
        import triage_commit_check
        import escape_log
        import review_calibration
        import task_record
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skill = root / ".claude/skills/project_subsystems"
            skill.mkdir(parents=True)
            (skill / "adaptation.json").write_text(json.dumps({"sensitivity_tags": [{
                "id": "hook", "paths": ["hook.py"], "sets": {"reach": "shared"}, "source": "fixture"}]}))
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "proof")
            git("config", "user.email", "proof@example.invalid")
            (root / "seed").write_text("seed")
            git("add", "seed")
            git("commit", "-qm", "seed")
            (root / "hook.py").write_text("x = 1")
            git("add", "hook.py")
            message = root / "message.txt"
            message.write_text("fix: fixture\n\n" + TRAILER)
            payload = {"tool_name": "Bash", "session_id": "j8", "cwd": str(root),
                       "tool_input": {"command": 'git commit -F "' + message.as_posix() + '"'}}
            proc = subprocess.run([sys.executable, str(CLAUDE / "hooks/triage_commit_check.py")],
                                  input=json.dumps(payload), capture_output=True, text=True,
                                  env=dict(os.environ, CLAUDE_PROJECT_DIR=str(root)))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("reach local -> shared", context)
            self.assertNotIn("unreadable", context)
            git("commit", "-qF", str(message))
            sha = git("rev-parse", "HEAD")
            with patch.object(escape_log, "_REPO", str(root)):
                text = escape_log._triage_text(sha)
            self.assertNotEqual(text, "unrecorded")
            self.assertNotIn("reversibility", text)
            findings = review_calibration._triage_rules([{"id": "optional", "triage": text, "paths": ["hook.py"]}], str(root / ".claude"))
            self.assertEqual(len(findings), 1)
            self.assertIn("reach local -> shared", findings[0]["evidence"])
            with patch.dict(os.environ, {"HARNESS_TASK_RECORD_DIR": str(root / "tasks"),
                                         "CLAUDE_PROJECT_DIR": str(root), "CLAUDE_CODE_SESSION_ID": "j8"}):
                task_record.open_record("full", "j8", "full schema")
                fields = dict(triage.parse_trailer(TRAILER + " reversibility=clean")[0], reach="shared", oracle=["gate"])
                task_record.record_triage("full", fields, ["hook.py"], session_id="j8")
                self.assertEqual(set(task_record.triage_for_session("j8")), set(triage.FIELDS))

    def test_v23_real_recursive_submodules(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            def git(repo, *args):
                return subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=repo,
                                      check=True, capture_output=True, text=True).stdout.strip()
            def init(name):
                repo = home / name
                repo.mkdir()
                git(repo, "init", "-q")
                git(repo, "config", "user.name", "proof")
                git(repo, "config", "user.email", "proof@example.invalid")
                (repo / ".gitignore").write_text("*.local\n.cache/\nempty/\n")
                git(repo, "add", ".gitignore")
                git(repo, "commit", "-qm", "seed")
                return repo
            grand = init("grand-source")
            child = init("child-source")
            git(child, "submodule", "add", "-q", str(grand), "grand")
            git(child, "commit", "-qam", "grandchild")
            root = init("project")
            git(root, "submodule", "add", "-q", str(child), "child")
            git(root, "commit", "-qam", "child")
            git(root, "update-ref", "refs/remotes/proof/main", "HEAD")
            target = root / ".claude/worktrees/retired"
            target.parent.mkdir(parents=True)
            git(root, "worktree", "add", "-q", "--detach", str(target), "HEAD")
            git(target, "submodule", "update", "--init", "--recursive", "-q")
            def qualifies(allow=False):
                return regenerable.is_retired_worktree(str(target), str(root), str(root), allow_ignored=allow)
            self.assertTrue(qualifies())
            for sub in (target / "child", target / "child/grand"):
                unique = sub / "secret.local"
                unique.write_text("irreplaceable fixture evidence")
                self.assertFalse(qualifies(), str(sub))
                self.assertTrue(qualifies(True), str(sub))
                unique.unlink()
                cache = sub / ".cache"
                cache.mkdir()
                (cache / "generated").write_text("cache")
                self.assertTrue(qualifies(), str(sub))
                (sub / "empty").mkdir()
                self.assertTrue(qualifies(), str(sub))
            (target / "child/grand/.gitignore").write_text("modified tracked data")
            self.assertFalse(qualifies(True))

    def test_v25_owner_goal_disarms(self):
        for text in ("/overnight", "/overnight   ",
                     "<command-message>overnight</command-message>\n<command-name>/overnight</command-name>"):
            self.assertFalse(overnight.is_owner_prompt(text), text)
        for text in ("/overnight a new goal", "/overnight\nnew goal",
                     "<command-message>overnight</command-message>\n<command-name>/overnight</command-name>\n<command-args>new goal</command-args>"):
            self.assertTrue(overnight.is_owner_prompt(text), text)
            with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "j8"}):
                with contextlib.redirect_stdout(io.StringIO()):
                    overnight.arm("old goal", root)
                    overnight.owner_return({"session_id": "j8", "cwd": root, "prompt": text})
                self.assertFalse(overnight.armed(root, "j8"))
        self.assertFalse(overnight.is_owner_prompt('<agent-message>/overnight new goal</agent-message>'))

    def test_rv4_default_independent(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "j8"}):
            with contextlib.redirect_stdout(io.StringIO()):
                overnight.arm("goal", root)
            doc = Path(root, "close-j8.md")
            def write(row):
                doc.write_text("## Outcome\nNext session: USD · model\n## Decisions for you\n" + row +
                               "\n## Left undone\n- None.\n", encoding="utf-8")
            for suffix in ("", ". Reversibility: residue"):
                write(Q.format(suffix=suffix))
                with contextlib.redirect_stdout(io.StringIO()):
                    overnight.validate_close(doc, root)
                self.assertEqual(len(overnight.open_questions(root)["open"]), 1)
            for row in (Q.format(suffix=". Reversibility: invalid"),
                        Q.format(suffix="").replace(". Default: nothing merged", ""),
                        Q.format(suffix=". Reversibility: clean").replace(". Default: nothing merged", "")):
                write(row)
                with self.assertRaises(ValueError):
                    overnight.validate_close(doc, root)
                found = overnight.open_questions(root)
                self.assertEqual(len(found["open"]) + len(found["malformed"]), 1)
                self.assertEqual(overnight.archive_closed(root, root)["archived"], [])

    def test_rv7_denial_command_and_authority(self):
        state = {}
        def update(_path, mutate):
            return state, mutate(state)
        commands = ["python3 .claude/tests/test_triage.py", "python3 .claude/tests/test_escape_log.py"]
        with patch.object(denial_coach, "update_json_locked", update), patch.object(denial_coach, "_overnight_armed", return_value=True):
            for command in commands:
                denial_coach._record({"session_id": "j8", "tool_name": "Bash", "tool_input": {"command": command},
                                      "reason": "[Security Test Removal] denied"})
            out = denial_coach._coach({"session_id": "j8"})
        self.assertEqual(out["decision"], "block")
        self.assertIn("Step 2", out["reason"])
        self.assertNotIn("Park: irreversible", out["reason"])
        for command in commands:
            self.assertIn(command, out["reason"])
        self.assertNotIn(denial_coach.KEY, state)

    def test_v23_ignored_child_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / ".claude/worktrees/retired"
            sub = target / "child"
            sub.mkdir(parents=True)
            admin = root / ".git/worktrees/retired"
            admin.mkdir(parents=True)
            (target / ".git").write_text("gitdir: " + str(admin))
            (sub / ".git").write_text("gitdir: placeholder")
            (sub / "secret.local").write_text("unique data")
            def git(args, cwd):
                if args[:2] == ["worktree", "list"]:
                    return "worktree " + str(target) + "\n\n"
                if args[:2] == ["submodule", "status"]:
                    return " " + "a" * 40 + " child (heads/main)\n"
                if args[:3] == ["symbolic-ref", "-q", "--short"]:
                    return ""
                if args[0] == "status":
                    return "!! secret.local\n" if cwd == sub and "--ignored=matching" in args else ""
                if args[0] == "rev-list":
                    return ""
                raise AssertionError(args)
            with patch.object(regenerable, "_git", side_effect=git):
                self.assertFalse(regenerable.is_retired_worktree(str(target), str(root), str(root)))
                self.assertTrue(regenerable.is_retired_worktree(str(target), str(root), str(root), allow_ignored=True))

    def test_v02_task_linked_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "proof")
            git("config", "user.email", "proof@example.invalid")
            (root / "seed").write_text("seed")
            git("add", "seed")
            git("commit", "-qm", "seed")
            base = git("rev-parse", "HEAD")
            (root / "hook.py").write_text("x = 1")
            git("add", "hook.py")
            git("commit", "-qm", "no trailer")
            sha = git("rev-parse", "HEAD")
            tasks = root / ".claude/logs/tasks"
            tasks.mkdir(parents=True)
            fields = dict(triage.parse_trailer(TRAILER + " reversibility=clean")[0])
            fields["oracle"] = list(fields["oracle"])
            record = {"accepted_artifacts": [{"text": "landed " + sha}], "triage": {"fields": fields}}
            (tasks / "linked.json").write_text(json.dumps(record))
            tag, error = triage._validate({"id": "hook", "paths": ["hook.py"], "sets": {"reach": "shared"}, "source": "fixture"}, set())
            self.assertIsNone(error)
            tags = triage.Tags([tag], [])
            with patch.dict(os.environ, {"HARNESS_TASK_RECORD_DIR": str(tasks)}):
                got = triage.rederive(str(root), base + "..HEAD", tags)
                self.assertEqual([s for s, _ in got], [sha])
                self.assertEqual(got[0][1][0].recorded, "local")
                (tasks / "linked.json").write_text('{"commit": "' + sha + '", broken')
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    self.assertEqual(triage.rederive(str(root), base + "..HEAD", tags), [])
                self.assertIn("malformed", output.getvalue())
                self.assertIn(sha[:9], output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
