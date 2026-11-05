"""Real-CLI replay: a mechanical fold inherits coverage; the parent's own design owes the game round; harness text owes none."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLAUDE = Path(__file__).resolve().parents[1]
TOOLS = CLAUDE / "tools"
CODE = "Combat/Demo.cs"
sys.path.insert(0, str(TOOLS))
import review_ledger  # noqa: E402  (a lens key fixes its seat tier)

MANDATES = ["harness-quality", "cq.D", "architecture-sweep"]
ROSTER = [{"key": key, "mandates": [mandate], "tier": review_ledger._lens_tier(key), "effort": "high"}
          for key, mandate in zip(["sa-harness-quality", "sa-design-semantics", "sa-architecture-sweep"], MANDATES)]
SUBJECT = "session:replay"


class Replay(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = dict(os.environ, HARNESS_REVIEW_LEDGER=str(self.root / "ledger.jsonl"),
                        HARNESS_ESCAPE_LOG=str(self.root / "escapes.jsonl"),
                        HARNESS_REVIEW_CANDIDATE_KEYS=",".join(MANDATES),
                        CLAUDE_PROJECT_DIR=str(self.root),
                        HARNESS_HOOK_STATE_DIR=str(self.root / "state"))
        self.git("init", "-q")
        self.git("config", "user.name", "fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.git("config", "core.autocrlf", "false")
        for path, text in {"fix.md": "teh default\n", CODE: "def choose():\n    return 0\n",
                           "adapted.md": "local cache old\nmismtach\n", "ask.md": "pending answer\n"}.items():
            self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        self.save("roster.json", ROSTER)
        self.rows = [
            {"id": "F1", "action": "FIX", "file": "fix.md:1", "agent": "harness-quality",
             "old": "teh default", "new": "the default", "evidence": [{"file": "fix.md", "quote": "teh default"}]},
            {"id": "F2", "action": "PLAN", "file": CODE + ":2", "agent": "cq.D", "question": None,
             "old": "return 0", "new": "return 1", "options": ["return 1 (Recommended)"],
             "evidence": [{"file": CODE, "quote": "return 0"}]},
            {"id": "F3", "action": "FIX", "file": "adapted.md:1", "agent": "harness-quality",
             "old": "local cache old", "new": "local cache fresh",
             "evidence": [{"file": "adapted.md", "quote": "local cache old"}]},
            {"id": "F4", "action": "FIX", "file": "adapted.md:2", "agent": "harness-quality",
             "old": "mismtach", "new": "mismatch"},
            {"id": "F5", "action": "ASK", "file": "ask.md:1", "agent": "harness-quality",
             "old": "pending answer", "new": "answered choice", "question": "which choice?"},
        ]
        self.save("findings.json", {"findings": self.rows})
        panel = {"findings": self.rows, "perAgent": [
            {"key": seat["key"], "status": "completed", "count": 0, "tier": seat["tier"], "effort": "high",
             "mandates": {seat["mandates"][0]: "clean"}, "checkedTargets": ["Read:" + p for p in self.files]}
            for seat in ROSTER]}
        self.save("panel.json", panel)
        self.cli("review_ledger", "record", "--stage", "candidate", "--subject", SUBJECT,
                 "--result", str(self.root / "panel.json"), "--roster", str(self.root / "roster.json"),
                 "--files", *self.files)

    @property
    def files(self):
        return ["fix.md", CODE, "adapted.md", "ask.md"]

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def save(self, path, value):
        self.write(path, json.dumps(value))
        return str(self.root / path)

    def git(self, *args):
        done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def cli(self, tool, *args, code=0):
        done = subprocess.run([sys.executable, str(TOOLS / (tool + ".py")), *args], cwd=self.root,
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(done.returncode, code, done.stdout + done.stderr)
        return done

    def read(self, path):
        return json.loads((self.root / path).read_text(encoding="utf-8"))

    def assert_only_unchecked(self, path):
        """F5 cites no evidence, so the report fails on UNCHECKED alone and records the application."""
        report = self.read(path)
        self.assertEqual((report["status"], report["failures"]), ("fail", []))
        self.assertTrue(report["counts"]["UNCHECKED"] and not report["counts"]["UNVERIFIED"], report["counts"])

    def ledger(self):
        return [json.loads(line) for line in (self.root / "ledger.jsonl").read_text().splitlines()]

    def fold(self):
        """The fold gets only the text files; the PLAN (code) and the ASK fall outside its subject and stay the parent's."""
        self.cli("post_check", "snapshot", "--root", str(self.root), "--files", "fix.md", "adapted.md",
                 "--out", str(self.root / "snapshot.json"))
        self.write("fix.md", "the default\n")
        self.write("adapted.md", "local cache fresh\nmismatch\n")
        digest = {"applied": [{"id": i, "change": "x", "source": "finding"} for i in ("F1", "F3", "F4")],
                  "deferred": [], "changedPaths": ["fix.md", "adapted.md"]}
        self.save("digest.json", digest)
        self.cli("post_check", "review", "--findings", str(self.root / "findings.json"),
                 "--digest", str(self.root / "digest.json"), "--snapshot", str(self.root / "snapshot.json"),
                 "--stage", "candidate", "--subject", SUBJECT, "--roster", str(self.root / "roster.json"),
                 "--task", "replay", "--out", str(self.root / "report.json"), code=0)
        report = self.read("report.json")
        self.assertEqual((report["status"], report["failures"]), ("fail", []))
        self.assertNotIn("nextRound", report)
        derived = {p for row in self.ledger() if "from" in row for p in row["files"]}
        self.assertEqual(derived, {"fix.md", "adapted.md"})
        self.assertEqual({h["id"] for h in report["held"]}, {"F2", "F5"})
        return report

    def parent_applies(self, finding_id, path, text, code=0):
        self.write(path, text)
        self.cli("post_check", "review", "--findings", str(self.root / "findings.json"),
                 "--applied", finding_id, "--files", path, "--root", str(self.root),
                 "--stage", "candidate", "--subject", SUBJECT, "--roster", str(self.root / "roster.json"),
                 "--out", str(self.root / (finding_id + ".json")), code=code)
        return self.read(finding_id + ".json")

    def owed(self, *files, mandates="candidate"):
        done = self.cli("review_ledger", "uncovered", "--files", *files, "--mandates", mandates,
                        "--roster", str(self.root / "roster.json"))
        return {line.split("\t")[0] for line in done.stdout.splitlines() if line.strip() and "\t" in line}

    def finish_panel(self, files):
        self.git("add", "--", *files)
        panel = {"findings": [], "perAgent": [
            {"key": seat["key"], "status": "completed", "count": 0, "tier": seat["tier"], "effort": "medium",
             "mandates": {seat["mandates"][0]: "clean"}, "checkedTargets": ["Read:" + p for p in files]}
            for seat in ROSTER]}
        self.save("later-panel.json", panel)
        self.cli("review_ledger", "record", "--stage", "candidate", "--subject", SUBJECT,
                 "--result", str(self.root / "later-panel.json"), "--roster", str(self.root / "roster.json"),
                 "--files", *files)
        self.assertEqual(self.owed(*self.files), set())
        plan = self.cli("review_ledger", "plan", "--stage", "candidate", "--subject", SUBJECT,
                        "--files", *self.files, "--roster", str(self.root / "roster.json"))
        self.assertEqual(json.loads(plan.stdout)["roster"], [])
        self.git("add", "--", *self.files)
        self.assertNotIn("[review]", self.hook())

    def hook(self, command='git commit -m "fixture"', session="replay"):
        payload = {"tool_name": "Bash", "cwd": str(self.root), "session_id": session,
                   "tool_input": {"command": command}}
        done = subprocess.run([sys.executable, str(CLAUDE / "hooks/triage_commit_check.py")],
                              input=json.dumps(payload), capture_output=True, text=True, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_mechanical_fold_inherits_coverage_and_the_parent_s_design_owes_the_game_round(self):
        self.fold()
        self.assertEqual(self.owed(*self.files), set(), "the fold's two transcriptions owe nothing")
        report = self.parent_applies("F2", CODE, "def choose():\n    return 1\n")
        self.assertEqual((report["status"], report["failures"], report["ledger"]["inlineRows"]), ("pass", [], 0))
        self.assertNotIn("nextRound", report)
        self.parent_applies("F5", "ask.md", "answered choice\n")
        self.assert_only_unchecked("F5.json")
        self.assertEqual(self.owed(*self.files), {CODE, "ask.md"},
                         "the session's own coverage query over the revised diff names the parent's design")
        self.finish_panel([CODE, "ask.md"])
        self.write("fix.md", "the default\nan unrelated mechanism\n")
        self.git("add", "fix.md")
        self.assertEqual(self.owed("fix.md"), {"fix.md"})
        self.assertIn("1 edited after a reviewed blob", self.hook())
        self.assertNotIn("[review]", self.hook())
        broad = ('git commit -m "fixture" -m "Triage: reach=shared reversibility=clean '
                 'design=settled grounding=verified oracle=gate size=milestones kind=decision"')
        self.assertIn("1 edited after a reviewed blob", self.hook(broad, session="broad-replay"))
        self.assertNotIn("[review]", self.hook(broad, session="broad-replay"))

    def test_harness_text_fold_leaves_no_review_debt(self):
        note = ".claude/commands/note.md"
        self.write(note, "teh default\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "harness note")
        self.cli("post_check", "snapshot", "--root", str(self.root), "--files", note,
                 "--out", str(self.root / "harness.snapshot.json"))
        self.write(note, "the default\n")
        row = {"id": "H1", "action": "FIX", "file": note + ":1", "agent": "cq.D", "old": "teh default",
               "new": "the default", "evidence": [{"file": note, "quote": "teh default"}]}
        self.save("harness.findings.json", {"findings": [row]})
        self.save("harness.digest.json", {"applied": [{"id": "H1", "change": "x", "source": "finding"}],
                                          "deferred": [], "changedPaths": [note]})
        self.cli("post_check", "review", "--findings", str(self.root / "harness.findings.json"),
                 "--digest", str(self.root / "harness.digest.json"),
                 "--snapshot", str(self.root / "harness.snapshot.json"), "--stage", "candidate",
                 "--subject", SUBJECT, "--roster", str(self.root / "roster.json"),
                 "--out", str(self.root / "harness.json"), code=0)
        report = self.read("harness.json")
        self.assertEqual((report["status"], report["held"], report["failures"]), ("pass", [], []))
        self.assertEqual(self.owed(note, mandates="cq.D,architecture-sweep"), set(),
                         "a prose-only harness scope owes no candidate mandate")
        self.assertEqual(self.owed(note, mandates="harness-quality"), {note}, "the harness lens keeps its own duty")

    def test_content_failure_voids_only_its_named_file(self):
        self.fold()
        self.cli("post_check", "snapshot", "--root", str(self.root), "--files", "fix.md", "adapted.md",
                 "--out", str(self.root / "negative.snapshot.json"))
        self.write("fix.md", "the default\na rogue line\n")
        self.write("adapted.md", "a corrected cache\nmismatch\n")
        rows = [{"id": "N1", "action": "FIX", "file": "fix.md:1", "agent": "cq.D",
                 "old": "the default", "new": "correct default"},
                {"id": "N2", "action": "FIX", "file": "adapted.md:1", "agent": "cq.D",
                 "old": "local cache fresh", "new": "a corrected cache"}]
        self.save("negative.findings.json", {"findings": rows})
        self.save("negative.digest.json", {"applied": [{"id": row["id"], "change": "x", "source": "finding"} for row in rows],
                  "deferred": [], "changedPaths": ["fix.md", "adapted.md"]})
        self.cli("post_check", "review", "--findings", str(self.root / "negative.findings.json"),
                 "--digest", str(self.root / "negative.digest.json"), "--snapshot", str(self.root / "negative.snapshot.json"),
                 "--stage", "candidate", "--subject", SUBJECT, "--roster", str(self.root / "roster.json"),
                 "--out", str(self.root / "negative.json"), code=0)
        failures = self.read("negative.json")["failures"]
        self.assertEqual([f["files"] for f in failures if f["kind"] == "fold-not-applied"], [["fix.md"]])
        derived = [row for row in self.ledger() if "from" in row]
        current = self.git("hash-object", "adapted.md")
        self.assertEqual({row["mandate"] for row in derived if row["files"].get("adapted.md") == current}, set(MANDATES))
        current = self.git("hash-object", "fix.md")
        self.assertFalse(any(row["files"].get("fix.md") == current for row in derived))

    def test_derived_row_over_unreviewed_predecessor_covers_nothing(self):
        self.write(".claude/new.py", "unreviewed\n")
        old = self.git("hash-object", "-w", ".claude/new.py")
        self.write(".claude/new.py", "derived without a root\n")
        origin = self.save("origin.json", {".claude/new.py": old})
        for mandate in MANDATES:
            self.cli("review_ledger", "record", "--stage", "candidate", "--subject", SUBJECT,
                     "--inline", "fold", "--mandate", mandate, "--outcome", "clean", "--files", ".claude/new.py",
                     "--from", origin)
        uncovered = self.cli("review_ledger", "uncovered", "--files", ".claude/new.py", "--mandates", "candidate",
                             "--roster", str(self.root / "roster.json"))
        self.assertEqual(len(uncovered.stdout.strip().splitlines()), len(MANDATES))
        self.assertNotIn("after:", uncovered.stdout)


class Binding(unittest.TestCase):
    def test_replay_is_bound_to_every_ja_code_artifact(self):
        data = json.loads((CLAUDE / "skills/project_subsystems/adaptation.json").read_text())
        if "proof_bindings" not in data:  # each consumer's adaptation.json binds its own proofs; the template's never ships this table
            self.skipTest("this checkout's adaptation.json carries no proof_bindings")
        binding = {path for path, proofs in data["proof_bindings"].items()
                   if "test_review_rounds_replay.py" in proofs}
        self.assertEqual(binding, {".claude/tools/review_ledger.py", ".claude/tools/post_check.py",
                                       ".claude/tools/fold_check.py", ".claude/hooks/triage_commit_check.py",
                                       ".claude/workflows/review_fanout.js"})


if __name__ == "__main__":
    unittest.main()
