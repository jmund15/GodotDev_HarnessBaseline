#!/usr/bin/env python3
"""Execute the settings merge against a planted plugin registration."""
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
    def plant_launcher(self, tmp):
        """A planted project whose `.claude` (the launcher's SC_ROOT) holds the merge, the reader and the kind table."""
        root = Path(tmp) / "project space" / ".claude"
        lib = root / "scripts/lib/sidecar_common.sh"
        lib.parent.mkdir(parents=True)
        shutil.copyfile(CLAUDE / "scripts/lib/sidecar_common.sh", lib)
        for rel in ("hooks/_checkout_root.py", "hooks/_git_run.py", "tools/adaptation.py", "tools/tool_profiles.py",
                    "tools/plugin_registry.py", "guards/tool_profiles.json"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CLAUDE / rel, root / rel)
        (root / "skills/project_subsystems").mkdir(parents=True)
        (root / "stub_entry.py").write_text("", encoding="utf-8")
        return root, lib

    def merge_proc(self, root, lib, adaptation, registration, shape="planted", prompt="a brief's path", armed="1", **extra):
        (root / "skills/project_subsystems/adaptation.json").write_text(json.dumps(adaptation), encoding="utf-8")
        if registration is None:
            (root / "plugin.bench.json").unlink(missing_ok=True)
        else:
            (root / "plugin.bench.json").write_text(json.dumps(registration), encoding="utf-8")
        base = {"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": []}]}, "model": "planted"}
        env = dict(os.environ, BENCH_ARM="", BENCH_RULER_SCOPE="", **extra)
        # Sourcing sets SC_WORKDIR to the cwd; a linked-worktree cwd would add the launcher's workdir guard.
        return subprocess.run([git_bash(), "-c", 'source "$1"; SC_ROOT="$6"; SC_WORKDIR=""; unset BENCH_ARM BENCH_RULER_SCOPE; BENCH_ARM="$5"; BENCH_RULER_SCOPE="$5"; SC_SHAPE="$2" SC_PROMPT_FILE="$3" sc_settings_with_bench_guard "$4"',
                               "_", lib.as_posix(), shape, prompt, json.dumps(base), armed, root.as_posix()],
                              env=env, capture_output=True, text=True), base

    def test_project_rows_and_absent_plugin(self):
        with tempfile.TemporaryDirectory(prefix="sidecar_adaptation_") as tmp:
            root, lib = self.plant_launcher(tmp)
            rows = [{"matcher": "Workflow", "command": "printf '%s' {prompt}",
                     "when": {"SC_SHAPE": "planted", "SC_PROMPT_FILE": None}},
                    {"matcher": "*", "command": "printf '%s' {plugin_root}", "when": {"BENCH_ARM": "1"}},
                    {"matcher": "Read", "command": "printf '%s' {ruler_scope}", "when": {"BENCH_RULER_SCOPE": None}}]
            registration = {"schema": 1, "core_root": ".", "entry": ".claude/stub_entry.py", "sidecar_hooks": rows}
            self.assertIsNotNone(git_bash())

            def merge(registration=registration, adaptation={"plugins": {"bench": "."}}, **kw):
                proc, base = self.merge_proc(root, lib, adaptation, registration, **kw)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                result = json.loads(proc.stdout)
                # The launcher's floor row is ChildGuardsProof's subject; this case compares the plugin and brief rows.
                pre = [g for g in result["hooks"]["PreToolUse"] if not any("hooks/pre_bash.py" in h["command"] for h in g["hooks"])]
                result["hooks"]["PreToolUse"] = pre
                return result, base

            result, base = merge()
            groups = result["hooks"]["PreToolUse"]
            self.assertEqual([g["matcher"] for g in groups], ["Read", "Workflow", "*", "Read"])
            self.assertEqual(result["model"], "planted")
            command = groups[1]["hooks"][0]["command"]
            ran = subprocess.run([git_bash(), "-c", command], capture_output=True, text=True)
            self.assertEqual((ran.returncode, ran.stdout), (0, Path("a brief's path").resolve().as_posix()))
            plugin_root = subprocess.run([git_bash(), "-c", groups[2]["hooks"][0]["command"]], capture_output=True, text=True)
            self.assertEqual(Path(plugin_root.stdout).resolve(), root.parent.resolve())
            starts = result["hooks"]["SessionStart"]
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0]["matcher"], "compact")
            self.assertIn("sidecar_recompact_reprompt.py", starts[0]["hooks"][0]["command"])
            pre = result["hooks"]["PreCompact"]
            self.assertEqual(len(pre), 1)
            self.assertEqual(pre[0]["hooks"][0]["command"], starts[0]["hooks"][0]["command"])
            with_prompt = dict(base, hooks=dict(base["hooks"], SessionStart=starts, PreCompact=pre))
            self.assertEqual(merge(None, {})[0], with_prompt)
            self.assertEqual(merge(shape="review", armed="")[0], with_prompt)
            self.assertEqual(merge(prompt="", armed="")[0], base)
            # An author child in a checkout whose branch predates the amend hook still gets it, from the launcher's root.
            author = merge(None, {}, shape="author", armed="")[0]
            amend = [(event, row.get("matcher"), hook["command"]) for event in ("PreToolUse", "Stop")
                     for row in author["hooks"].get(event, []) for hook in row["hooks"]
                     if "amend_delivery.py" in hook["command"]]
            self.assertEqual([(e, m) for e, m, _ in amend], [("PreToolUse", "*"), ("Stop", None)])
            self.assertTrue(all(root.as_posix() in c.replace("\\", "/") for _, _, c in amend), amend)
            self.assertEqual(merge(None, {}, shape="author", prompt="", armed="")[0], base)
            # A launcher env with MSYS_NO_PATHCONV=1 hands Python the root unconverted; the rows must still load.
            self.assertEqual([g["matcher"] for g in merge(MSYS_NO_PATHCONV="1")[0]["hooks"]["PreToolUse"]],
                             ["Read", "Workflow", "*", "Read"])

    def test_an_unreadable_plugin_refuses_only_the_kinds_it_owns(self):
        """An arm launched without its escape guard is a silent safety loss; a review launch loses nothing."""
        with tempfile.TemporaryDirectory(prefix="sidecar_adaptation_") as tmp:
            root, lib = self.plant_launcher(tmp)
            adaptation = {"plugins": {"bench": "."}}
            for shape in ("arm", "bench-score", "bench-review"):
                proc, _ = self.merge_proc(root, lib, adaptation, None, shape=shape, armed="")
                self.assertEqual(proc.returncode, 2, (shape, proc.stderr))
                self.assertIn("bench", proc.stderr)
                self.assertIn("unreadable", proc.stderr)
            proc, base = self.merge_proc(root, lib, adaptation, None, shape="review", armed="")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("unreadable", proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["model"], "planted")


class RecordTierProof(unittest.TestCase):
    def test_record_profile_carries_the_resolved_job_tier(self):
        """The `-R` record's `toolProfile` is `SC_TOOL_PROFILE`; `orchestration_metrics` archives its `tier`."""
        bash = git_bash()
        self.assertIsNotNone(bash)
        config = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, config, True)
        servers = {"semantic-search": {"command": "x"}, "ai-worker": {"command": "x"}}
        Path(config, ".claude.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
        env = dict(os.environ, CLAUDE_CONFIG_DIR=config)

        def profile_tier(shape, tier):
            script = ('source "$1"; SC_WORKDIR=""; SC_PROMPT_FILE=""; SC_SHAPE="$2"; SC_TIER="$3"; sc_resolve_tools; '
                      'printf "%s" "$SC_TOOL_PROFILE"')
            proc = subprocess.run([bash, "-c", script, "_", (CLAUDE / "scripts/lib/sidecar_common.sh").as_posix(), shape, tier],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout).get("tier")

        self.assertEqual(profile_tier("reviewer", ""), "executor")
        self.assertEqual(profile_tier("reviewer", "architect"), "architect")
        self.assertEqual(profile_tier("scout", "validation"), "fanout")


class AmendLineProof(unittest.TestCase):
    def test_author_prompt_carries_the_amendment_key_the_hook_parses(self):
        """hooks/amend_delivery.py delivers the file this line names; the line orders no poll."""
        bash = git_bash()
        self.assertIsNotNone(bash)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        brief = Path(tmp, "brief.md")
        brief.write_text("task", encoding="utf-8")
        script = 'source "$1"; SC_PROMPT_FILE="$2"; SC_SHAPE=author; sc_read_prompt; printf "%s" "$SC_PROMPT"'
        proc = subprocess.run([bash, "-c", script, "_", (CLAUDE / "scripts/lib/sidecar_common.sh").as_posix(),
                               brief.as_posix()], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sys.path.insert(0, str(CLAUDE / "hooks"))
        import amend_delivery
        named = amend_delivery.amend_path_in(proc.stdout)
        self.assertIsNotNone(named, proc.stdout[-300:])
        self.assertEqual(os.path.normcase(os.path.abspath(named)), os.path.normcase(os.path.abspath(str(brief) + ".amend.md")))
        self.assertNotIn("before returning, Read", proc.stdout)


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


class ChildGuardsProof(unittest.TestCase):
    """A sidecar child never runs with fewer delegate guards than its dispatcher, whatever its workdir holds."""
    FLOOR = "hooks/pre_bash.py"  # the loss floor's one entry; it carries the wait clamp (foreground_wait_guard)

    def merge(self, workdir):
        proc = subprocess.run([git_bash(), "-c", 'source "$1" >/dev/null 2>&1; SC_ROOT="$2"; SC_WORKDIR="$3"; unset BENCH_ARM BENCH_RULER_SCOPE; sc_settings_with_bench_guard ""',
                               "_", (CLAUDE / "scripts/lib/sidecar_common.sh").as_posix(), CLAUDE.as_posix(), workdir.as_posix()],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip() or "{}")

    def floor_rows(self, merged):
        return [(row["matcher"], hook["command"]) for row in merged.get("hooks", {}).get("PreToolUse", [])
                for hook in row["hooks"] if self.FLOOR in hook["command"]]

    def test_a_workdir_with_no_harness_gets_the_floor_from_the_launcher_root(self):
        with tempfile.TemporaryDirectory(prefix="sidecar_childguards_") as tmp:
            rows = self.floor_rows(self.merge(Path(tmp)))
            self.assertEqual(len(rows), 1, rows)
            matcher, command = rows[0]
            self.assertEqual(matcher, "Bash|PowerShell|Monitor|TaskStop")
            self.assertNotIn("CLAUDE_PROJECT_DIR", command)
            self.assertTrue((CLAUDE / self.FLOOR).is_file())
            self.assertIn((CLAUDE / self.FLOOR).as_posix(), command)

    def test_a_workdir_whose_harness_registers_the_floor_gets_it_once(self):
        with tempfile.TemporaryDirectory(prefix="sidecar_childguards_") as tmp:
            (Path(tmp) / ".claude").mkdir()
            (Path(tmp) / ".claude/settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{
                "matcher": "Bash", "hooks": [{"type": "command",
                                              "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/pre_bash.py"'}]}]}}), encoding="utf-8")
            self.assertEqual(self.floor_rows(self.merge(Path(tmp))), [])

    def test_a_workdir_harness_that_predates_the_floor_still_gets_it(self):
        with tempfile.TemporaryDirectory(prefix="sidecar_childguards_") as tmp:
            (Path(tmp) / ".claude").mkdir()
            (Path(tmp) / ".claude/settings.json").write_text("{}", encoding="utf-8")
            self.assertEqual(len(self.floor_rows(self.merge(Path(tmp)))), 1)


if __name__ == "__main__":
    unittest.main()
