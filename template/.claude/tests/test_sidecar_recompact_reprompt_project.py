"""Project cases for hooks/sidecar_recompact_reprompt.py: the shared settings merge, the neutral prompt path, the precompact summary and rails, the reread advisory and the env contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sidecar_launch import git_bash  # noqa: E402
import _mcp_plant  # noqa: E402

# `resolve-kind` validates a profile against the MCP servers the caller's config registers; a runner has none.
_mcp_plant.plant()

HOOK = Path(__file__).resolve().parents[1] / "hooks/sidecar_recompact_reprompt.py"
TOOL_PROFILES = HOOK.parents[1] / "tools/tool_profiles.py"


def kind_builtins(kind):
    """A launcher kind's resolved built-in list, from the real guards/tool_profiles.json."""
    return subprocess.run([sys.executable, str(TOOL_PROFILES), "resolve-kind", "--kind=" + kind,
                           "--field=builtins"], capture_output=True, text=True, check=True).stdout.strip()


class RepromptTest(unittest.TestCase):
    def test_shared_settings_merge(self):
        common = HOOK.parents[1] / "scripts/lib/sidecar_common.sh"
        base = {"env": {"KEEP": "yes"}, "hooks": {"SessionStart": [
            {"matcher": "startup", "hooks": [{"type": "command", "command": "keep"}]}]}}
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief.md"
            prompt.write_text("Read t6_input.json", encoding="utf-8")
            script = Path(tmp) / "merge.sh"
            script.write_text('. "$1"; SC_PROMPT_FILE="$2"; sc_settings_with_bench_guard "$BASE_SETTINGS"\n',
                              encoding="utf-8", newline="\n")
            run = subprocess.run([git_bash(), script.as_posix(), common.as_posix(), prompt.as_posix()],
                                 capture_output=True, text=True,
                                 env=dict(os.environ, BENCH_ARM="1", BASE_SETTINGS=json.dumps(base)))
            self.assertEqual(run.returncode, 0, run.stderr)
            settings = json.loads(run.stdout)
            self.assertEqual(settings["env"], base["env"])
            self.assertEqual(settings["hooks"]["SessionStart"][0], base["hooks"]["SessionStart"][0])
            compact = [h for h in settings["hooks"]["SessionStart"] if h["matcher"] == "compact"]
            self.assertEqual(len(compact), 1, settings)
            command = compact[0]["hooks"][0]["command"]
            self.assertIn("sidecar_recompact_reprompt.py", command)
            injected = subprocess.run([git_bash(), "-c", command],
                                      input=json.dumps({"source": "compact"}),
                                      capture_output=True, text=True)
            self.assertEqual(injected.returncode, 0, injected.stderr)
            self.assertIn("Read t6_input.json", injected.stdout)
            again = subprocess.run([git_bash(), script.as_posix(), common.as_posix(), prompt.as_posix()],
                                   capture_output=True, text=True,
                                   env=dict(os.environ, BENCH_ARM="1", BASE_SETTINGS=json.dumps(settings)))
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(len([h for h in json.loads(again.stdout)["hooks"]["SessionStart"]
                                  if h["matcher"] == "compact"]), 1)
            # The bench guard rows come from the project's `adaptation.json` `sidecar_hook_rows`, the one source
            # the merge reads; a lane whose seed configures none must merge none.
            try:
                project = json.loads((HOOK.parents[1] / "skills/project_subsystems/adaptation.json")
                                     .read_text(encoding="utf-8"))
            except (OSError, ValueError):
                project = {}
            bench_rows = [row for row in project.get("sidecar_hook_rows", [])
                          if (row.get("when") or {}).get("BENCH_ARM") == "1"]
            commands = [h["hooks"][0]["command"] for h in settings["hooks"].get("PreToolUse", [])]
            self.assertEqual(any("bench_arm_escape_guard" in command for command in commands), bool(bench_rows), settings)
            pre = [h for h in json.loads(again.stdout)["hooks"].get("PreCompact", [])
                   if "sidecar_recompact_reprompt.py" in h["hooks"][0]["command"]]
            self.assertEqual(len(pre), 1, settings)
            asked = subprocess.run([git_bash(), "-c", pre[0]["hooks"][0]["command"]],
                                   input=json.dumps({"hook_event_name": "PreCompact", "trigger": "auto"}),
                                   capture_output=True, text=True)
            self.assertEqual(asked.returncode, 0, asked.stderr)
            self.assertIn("Recent results", asked.stdout)

    def test_save_hold_wired_only_below_the_real_window(self):
        common = HOOK.parents[1] / "scripts/lib/sidecar_common.sh"
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief.md"
            prompt.write_text("Write report.md", encoding="utf-8")
            script = Path(tmp) / "merge.sh"
            script.write_text('. "$1"; SC_PROMPT_FILE="$2"; sc_settings_with_bench_guard ""\n',
                              encoding="utf-8", newline="\n")
            live = json.loads((HOOK.parents[1] / "reference/external_models.json").read_text(encoding="utf-8"))
            row = json.loads(json.dumps(live["models"][0]))
            row.update(id="proof-window-model", alias="proof-window", family="proof-window", order=max(m.get("order", 0) for m in live["models"]) + 1, limits={"contextTokens": 640000, "maxOutputTokens": 32000})
            live["models"].append(row)
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps(live), encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("AC_AUTO_COMPACT_WINDOW", "SC_MODEL", "SC_RESOLVED_BUILTINS")}
            env.update(HARNESS_MODEL_REGISTRY=str(registry), SC_MODEL="proof-window",
                       SC_RESOLVED_BUILTINS=kind_builtins("author"))
            held = subprocess.run([git_bash(), script.as_posix(), common.as_posix(), prompt.as_posix()],
                                  capture_output=True, text=True, env=dict(env, AC_AUTO_COMPACT_WINDOW="250000"))
            self.assertEqual(held.returncode, 0, held.stderr)
            hooks = json.loads(held.stdout)["hooks"]
            post = [h for h in hooks.get("PostToolUse", []) if "sidecar_recompact_reprompt.py" in h["hooks"][0]["command"]]
            self.assertEqual(len(post), 1, hooks)
            self.assertIn("--hard-window 640000", post[0]["hooks"][0]["command"])
            pre = [h for h in hooks["PreCompact"] if "sidecar_recompact_reprompt.py" in h["hooks"][0]["command"]]
            self.assertIn("--hard-window 640000", pre[0]["hooks"][0]["command"])
            plain = subprocess.run([git_bash(), script.as_posix(), common.as_posix(), prompt.as_posix()],
                                   capture_output=True, text=True, env=env)
            self.assertEqual(plain.returncode, 0, plain.stderr)
            hooks = json.loads(plain.stdout)["hooks"]
            self.assertFalse(any("sidecar_recompact_reprompt.py" in h["hooks"][0]["command"]
                                 for h in hooks.get("PostToolUse", [])), hooks)
            self.assertNotIn("--hard-window", json.dumps(hooks))

    def test_save_hold_not_wired_for_a_read_only_child(self):
        common = HOOK.parents[1] / "scripts/lib/sidecar_common.sh"
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief.md"
            prompt.write_text("Write report.md", encoding="utf-8")
            script = Path(tmp) / "merge.sh"
            script.write_text('. "$1"; SC_PROMPT_FILE="$2"; sc_settings_with_bench_guard ""\n',
                              encoding="utf-8", newline="\n")
            live = json.loads((HOOK.parents[1] / "reference/external_models.json").read_text(encoding="utf-8"))
            row = json.loads(json.dumps(live["models"][0]))
            row.update(id="proof-window-model", alias="proof-window", family="proof-window",
                       order=max(m.get("order", 0) for m in live["models"]) + 1,
                       limits={"contextTokens": 640000, "maxOutputTokens": 32000})
            live["models"].append(row)
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps(live), encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("AC_AUTO_COMPACT_WINDOW", "SC_MODEL", "SC_RESOLVED_BUILTINS")}
            env.update(HARNESS_MODEL_REGISTRY=str(registry), SC_MODEL="proof-window",
                       SC_RESOLVED_BUILTINS=kind_builtins("scout"))
            run = subprocess.run([git_bash(), script.as_posix(), common.as_posix(), prompt.as_posix()],
                                 capture_output=True, text=True, env=dict(env, AC_AUTO_COMPACT_WINDOW="250000"))
            self.assertEqual(run.returncode, 0, run.stderr)
            hooks = json.loads(run.stdout)["hooks"]
            self.assertFalse(any("sidecar_recompact_reprompt.py" in h["hooks"][0]["command"]
                                 for h in hooks.get("PostToolUse", [])), hooks)
            self.assertNotIn("--hard-window", json.dumps(hooks))
            pre = [h for h in hooks["PreCompact"] if "sidecar_recompact_reprompt.py" in h["hooks"][0]["command"]]
            self.assertEqual(len(pre), 1, hooks)

    def _merged_hooks(self, tmp, kind, record):
        """The hooks the launcher's shared merge wires for a `kind` child, from the real tool_profiles row."""
        common = HOOK.parents[1] / "scripts/lib/sidecar_common.sh"
        prompt = Path(tmp) / "brief.md"
        prompt.write_text("Report findings.", encoding="utf-8")
        script = Path(tmp) / "merge.sh"
        script.write_text('. "$1"; SC_PROMPT_FILE="$2"; SC_RECORD="$3"; sc_settings_with_bench_guard ""\n',
                          encoding="utf-8", newline="\n")
        env = {k: v for k, v in os.environ.items()
               if k not in ("AC_AUTO_COMPACT_WINDOW", "SC_MODEL", "SC_RESOLVED_BUILTINS")}
        env["SC_RESOLVED_BUILTINS"] = kind_builtins(kind)
        run = subprocess.run([git_bash(), script.as_posix(), common.as_posix(), prompt.as_posix(), Path(record).as_posix()],
                             capture_output=True, text=True, env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)["hooks"]

    @staticmethod
    def _fire(hooks, event, payload, matcher=None):
        for row in hooks.get(event, []):
            if row.get("matcher") == matcher and "sidecar_recompact_reprompt.py" in row["hooks"][0]["command"]:
                return subprocess.run([git_bash(), "-c", row["hooks"][0]["command"]], input=json.dumps(payload),
                                      capture_output=True, text=True)
        return None

    def test_read_only_child_compaction_summary_is_spilled_beside_its_record(self):
        """A scout cannot write its findings before it compacts; the hook copies each continuation summary the
        client wrote into the transcript to <record>.compaction.md, where the parent's recovery reads it."""
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "scout1.record.json"
            transcript = Path(tmp) / "child.jsonl"
            rows = [{"type": "user", "message": {"role": "user", "content": "go"}},
                    {"type": "system", "subtype": "compact_boundary"},
                    {"type": "user", "isCompactSummary": True,
                     "message": {"role": "user", "content": "Recent results\n- a/b.cs:12 `int x = 7;` checked"}}]
            transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            hooks = self._merged_hooks(tmp, "scout", record)
            payload = {"session_id": "s-spill", "transcript_path": str(transcript)}
            spill = Path(str(record) + ".compaction.md")
            run = self._fire(hooks, "PostToolUse", dict(payload, hook_event_name="PostToolUse", tool_name="Read"))
            self.assertIsNotNone(run, "no PostToolUse spill row wired for a read-only child: %s" % json.dumps(hooks))
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout, "")
            self.assertIn("a/b.cs:12 `int x = 7;`", spill.read_text(encoding="utf-8"))
            before = spill.stat().st_mtime_ns
            self._fire(hooks, "PostToolUse", dict(payload, hook_event_name="PostToolUse", tool_name="Read"))
            self.assertEqual(spill.stat().st_mtime_ns, before, "a second call with no new summary rewrote the spill")
            with transcript.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "isCompactSummary": True,
                                     "message": {"role": "user", "content": "Earlier results\n- a/b.cs: 12"}}) + "\n")
            stop = self._fire(hooks, "Stop", dict(payload, hook_event_name="Stop"))
            self.assertIsNotNone(stop, "no Stop spill row wired for a read-only child")
            text = spill.read_text(encoding="utf-8")
            self.assertIn("int x = 7;", text)
            self.assertIn("Earlier results", text)

    def test_a_child_that_can_write_is_not_given_the_spill(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = self._merged_hooks(tmp, "author", Path(tmp) / "a.record.json")
            self.assertNotIn("--spill", json.dumps(hooks))

    def test_launcher_neutral_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief with spaces.md"
            prompt.write_text("Read t6_input.json; write result.json.", encoding="utf-8")
            env = dict(os.environ)
            env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
            run = subprocess.run([sys.executable, str(HOOK), "--prompt-file", str(prompt)],
                                 input=json.dumps({"source": "compact"}), env=env,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn(prompt.read_text(), run.stdout)
            self.assertIn("STATE", run.stdout)
            run = subprocess.run([sys.executable, str(HOOK), "--prompt-file", str(prompt)],
                                 input=json.dumps({"source": "startup"}), env=env,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout, "")

    def test_precompact_asks_the_summary_for_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief.md"
            prompt.write_text("UNIQUE-BRIEF-TEXT review the plan", encoding="utf-8")
            env = dict(os.environ)
            env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
            env["SIDECAR_COMPACTION_RAIL"] = "3"
            run = subprocess.run([sys.executable, str(HOOK), "--prompt-file", str(prompt)],
                                 input=json.dumps({"hook_event_name": "PreCompact", "trigger": "auto"}),
                                 env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Results so far", run.stdout)
            self.assertIn("Remaining", run.stdout)
            self.assertNotIn("UNIQUE-BRIEF-TEXT", run.stdout)  # the brief comes back after compaction
            self.assertLess(len(run.stdout), 10000)  # the client's hook-output cap
            for default_section in ("Key Technical Concepts", "Files and Code Sections", "All user messages"):
                self.assertIn(default_section, run.stdout)  # named, so the summary drops them
            self.assertIn("copy its Results so far lines verbatim", run.stdout)
            self.assertIn("12,000 characters", run.stdout)
            run = subprocess.run([sys.executable, str(HOOK)],
                                 input=json.dumps({"hook_event_name": "PreCompact", "trigger": "auto"}),
                                 env=env, capture_output=True, text=True)
            self.assertEqual((run.returncode, run.stdout), (0, ""))  # not a sidecar child

    def test_rail_four_tiers_the_results_and_is_chosen_per_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief.md"
            prompt.write_text("brief", encoding="utf-8")
            def precompact(rail):
                env = dict(os.environ)
                env.pop("SIDECAR_COMPACTION_RAIL", None)
                if rail is not None:
                    env["SIDECAR_COMPACTION_RAIL"] = rail
                run = subprocess.run([sys.executable, str(HOOK), "--prompt-file", str(prompt)],
                                     input=json.dumps({"hook_event_name": "PreCompact"}), env=env,
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                return run.stdout
            four = precompact("4")
            for marker in ("Recent results", "Earlier results", "one line per file", "Never copy an earlier summary's Recent work",
                           "delete a result a later tool result refuted"):
                self.assertIn(marker, four)
            self.assertNotIn("copy its Results so far lines verbatim", four)
            self.assertEqual(four, precompact(None))  # rail 4 is the default
            self.assertEqual(precompact("9"), precompact(None))  # an unknown rail falls back to the default
        sys.path.insert(0, str(HOOK.parent))
        import sidecar_recompact_reprompt as hook
        self.assertEqual(hook.selected_rail({"SIDECAR_COMPACTION_RAIL": "4"}), 4)
        self.assertEqual(hook.selected_rail({}), hook.DEFAULT_RAIL)

    def test_reprompt_asks_for_a_reread_before_citing(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief.md"
            prompt.write_text("brief", encoding="utf-8")
            run = subprocess.run([sys.executable, str(HOOK), "--prompt-file", str(prompt)],
                                 input=json.dumps({"hook_event_name": "SessionStart", "source": "compact"}),
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Read it again before you cite it", run.stdout)

    def test_existing_env_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "brief.md"
            prompt.write_text("Original Codex brief", encoding="utf-8")
            env = dict(os.environ, CLAUDE_CODE_SIDECAR_PROMPT_FILE=str(prompt))
            run = subprocess.run([sys.executable, str(HOOK)],
                                 input=json.dumps({"source": "compact"}), env=env,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Original Codex brief", run.stdout)


if __name__ == "__main__":
    unittest.main()
