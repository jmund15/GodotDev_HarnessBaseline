#!/usr/bin/env python3
"""Execute the settings merge against planted project hook rows."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sidecar_launch import git_bash  # noqa: E402

CLAUDE = Path(__file__).resolve().parents[1]


class SettingsProof(unittest.TestCase):
    def test_project_rows_and_absent_key(self):
        with tempfile.TemporaryDirectory(prefix="sidecar_adaptation_") as tmp:
            root = Path(tmp) / "project space" / ".claude"
            lib = root / "scripts/lib/sidecar_common.sh"
            lib.parent.mkdir(parents=True)
            shutil.copyfile(CLAUDE / "scripts/lib/sidecar_common.sh", lib)
            (root / "hooks").mkdir()
            shutil.copyfile(CLAUDE / "hooks/_checkout_root.py", root / "hooks/_checkout_root.py")
            config = root / "skills/project_subsystems/adaptation.json"
            config.parent.mkdir(parents=True)
            base = {"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": []}]}, "model": "planted"}
            rows = [{"matcher": "Workflow", "command": "printf '%s' {prompt}",
                     "when": {"SC_SHAPE": "planted", "SC_PROMPT_FILE": None}},
                    {"matcher": "*", "command": "printf '%s' {root}", "when": {"BENCH_ARM": "1"}},
                    {"matcher": "Read", "command": "printf '%s' {ruler_scope}", "when": {"BENCH_RULER_SCOPE": None}}]
            bash = git_bash()
            self.assertIsNotNone(bash)

            def merge(data, shape="planted", prompt="a brief's path", armed="1", **extra):
                config.write_text(json.dumps(data), encoding="utf-8")
                env = dict(os.environ, BENCH_ARM="", BENCH_RULER_SCOPE="", **extra)
                # Sourcing sets SC_WORKDIR to the cwd; a linked-worktree cwd would add the launcher's workdir guard.
                proc = subprocess.run([bash, "-c", 'source "$1"; SC_WORKDIR=""; unset BENCH_ARM BENCH_RULER_SCOPE; BENCH_ARM="$5"; BENCH_RULER_SCOPE="$5"; SC_SHAPE="$2" SC_PROMPT_FILE="$3" sc_settings_with_bench_guard "$4"',
                                       "_", lib.as_posix(), shape, prompt, json.dumps(base), armed],
                                      env=env, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                return json.loads(proc.stdout)

            result = merge({"sidecar_hook_rows": rows})
            groups = result["hooks"]["PreToolUse"]
            self.assertEqual([g["matcher"] for g in groups], ["Read", "Workflow", "*", "Read"])
            self.assertEqual(result["model"], "planted")
            command = groups[1]["hooks"][0]["command"]
            ran = subprocess.run([bash, "-c", command], capture_output=True, text=True)
            self.assertEqual((ran.returncode, ran.stdout), (0, Path("a brief's path").resolve().as_posix()))
            starts = result["hooks"]["SessionStart"]
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0]["matcher"], "compact")
            self.assertIn("sidecar_recompact_reprompt.py", starts[0]["hooks"][0]["command"])
            pre = result["hooks"]["PreCompact"]
            self.assertEqual(len(pre), 1)
            self.assertEqual(pre[0]["hooks"][0]["command"], starts[0]["hooks"][0]["command"])
            with_prompt = dict(base, hooks=dict(base["hooks"], SessionStart=starts, PreCompact=pre))
            self.assertEqual(merge({}), with_prompt)
            self.assertEqual(merge({"sidecar_hook_rows": rows}, shape="review", armed=""), with_prompt)
            self.assertEqual(merge({"sidecar_hook_rows": rows}, prompt="", armed=""), base)
            # A launcher env with MSYS_NO_PATHCONV=1 hands Python the root unconverted; the rows must still load.
            self.assertEqual([g["matcher"] for g in merge({"sidecar_hook_rows": rows}, MSYS_NO_PATHCONV="1")
                              ["hooks"]["PreToolUse"]], ["Read", "Workflow", "*", "Read"])


class RecordTierProof(unittest.TestCase):
    def test_record_profile_carries_the_resolved_job_tier(self):
        """The `-R` record's `toolProfile` is `SC_TOOL_PROFILE`; `orchestration_metrics` archives its `tier`."""
        bash = git_bash()
        self.assertIsNotNone(bash)

        def profile_tier(shape, tier):
            script = ('source "$1"; SC_WORKDIR=""; SC_PROMPT_FILE=""; SC_SHAPE="$2"; SC_TIER="$3"; sc_resolve_tools; '
                      'printf "%s" "$SC_TOOL_PROFILE"')
            proc = subprocess.run([bash, "-c", script, "_", (CLAUDE / "scripts/lib/sidecar_common.sh").as_posix(), shape, tier],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout).get("tier")

        self.assertEqual(profile_tier("review", ""), "executor")
        self.assertEqual(profile_tier("review", "architect"), "architect")
        self.assertEqual(profile_tier("survey", "architect"), "architect")


class CheckFactsTierProof(unittest.TestCase):
    def test_check_facts_resolve_the_tier_through_job_tier(self):
        """`--check` folds a written `-Q validation` onto `fanout` and names an unknown tier, as the launch does."""
        bash = git_bash()
        self.assertIsNotNone(bash)

        def facts(tier):
            script = 'source "$1"; SC_ROOT="$2"; sc_check_dispatch_facts -G survey -Q "$3" -m "$4"'
            proc = subprocess.run([bash, "-c", script, "_", (CLAUDE / "scripts/lib/sidecar_common.sh").as_posix(),
                                   CLAUDE.as_posix(), tier, "sonnet"], capture_output=True, text=True)
            return proc.stdout

        self.assertIn("tier=fanout", facts("validation"))
        self.assertIn("tier: unknown role 'bogus'", facts("bogus"))

    def test_enum_validation_refuses_an_unknown_tier_and_folds_validation(self):
        """A `--check` never passes a `-Q` the launch refuses; `validation` is a legal spelling."""
        bash = git_bash()
        self.assertIsNotNone(bash)

        def validate(tier):
            script = 'source "$1"; SC_ROOT="$2"; SC_TIER="$3"; (sc_validate_enums); echo "rc=$?"'
            proc = subprocess.run([bash, "-c", script, "_", (CLAUDE / "scripts/lib/sidecar_common.sh").as_posix(),
                                   CLAUDE.as_posix(), tier], capture_output=True, text=True)
            return proc.stdout + proc.stderr

        self.assertIn("rc=0", validate("validation"))
        self.assertIn("rc=0", validate("architect"))
        bogus = validate("bogus")
        self.assertIn("rc=2", bogus)
        self.assertIn("invalid tier 'bogus'", bogus)


if __name__ == "__main__":
    unittest.main()
