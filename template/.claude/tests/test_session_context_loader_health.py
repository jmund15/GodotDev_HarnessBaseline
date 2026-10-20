#!/usr/bin/env python3
"""Startup probes preserve unknowns and never overwrite an existing checkout."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import session_context_loader as scl
import session_context_loader_godot as scg


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # a killed check's shell releases the dir a moment late
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_roster_health_never_reports_ok_without_a_logs_tree(self):
        # No benchmark_campaign under the root: no battery, so no roster status; a battery whose
        # discovery fails says UNKNOWN, never OK.
        self.assertEqual("", scl.roster_health(self.root))
        (self.root / ".claude" / "scripts" / "benchmark_campaign").mkdir(parents=True)
        self.assertTrue(scl.roster_health(self.root).startswith("UNKNOWN ("))

    def test_roster_health_reads_the_live_tree(self):
        # UNKNOWN covers every discovery failure roster_health's `except Exception` catches, not
        # only a missing logs dir -- e.g. a checkout without the benchmark_campaign tooling
        # (never published: peer-owned, project-local) raises ModuleNotFoundError instead.
        result = scl.roster_health(Path(__file__).resolve().parents[2])
        self.assertTrue(result == "" or result.startswith(("OK (", "DEAD ", "UNVERIFIED ", "UNKNOWN (")), result)

    def test_failed_submodule_probe_is_unknown(self):
        for result in (SimpleNamespace(returncode=1, stdout=""), OSError("unavailable")):
            with self.subTest(result=result):
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(scl, "run_bounded", **kwargs):
                    self.assertIsNone(scl.submodule_status(self.root))

    def test_malformed_submodule_status_is_unknown(self):
        with patch.object(scl, "run_bounded", return_value=SimpleNamespace(returncode=0, stdout="unexpected output")):
            self.assertIsNone(scl.submodule_status(self.root))

    def test_empty_successful_probe_means_no_submodules(self):
        with patch.object(scl, "run_bounded", return_value=SimpleNamespace(returncode=0, stdout="")):
            self.assertEqual([], scl.submodule_status(self.root))

    def test_sidecar_refusal_keeps_its_reason(self):
        # Live defect 2026-09-14: the banner read "sidecar-codex: Override: re-run with -A." with no reason.
        script = self.root / ".claude" / "scripts" / "codex_proxy_sidecar.sh"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        refusal = ("[sidecar] codex quota probe note\n"
                   "[sidecar] REFUSING luna-max (gpt-x): codex's OWN quota band is Hot, above the Ahead ceiling.\n"
                   "  This is the PROVIDER's allowance, not this session's.\n"
                   "  Override: re-run with -A.\n")
        with patch.object(scl, "resolve_bash", return_value="bash"), \
                patch.object(scl, "run_bounded", return_value=SimpleNamespace(returncode=8, stdout="", stderr=refusal)):
            result = scl.verify_sidecar(self.root, "codex_proxy_sidecar.sh")
        self.assertIn("OWN quota band is Hot", result)
        self.assertIn("re-run with -A", result)
        with patch.object(scl, "resolve_bash", return_value="bash"), \
                patch.object(scl, "run_bounded", return_value=SimpleNamespace(returncode=0, stdout="preflight\nOK\n", stderr="")):
            self.assertEqual("OK", scl.verify_sidecar(self.root, "codex_proxy_sidecar.sh"))

    def test_shared_launcher_probes_a_model_per_transport(self):
        models = [
            {"transport": "opencode", "id": "oc-model", "alias": "oc", "roles": ["fanout"], "lifecycle": "current"},
            {"transport": "go", "id": "go-model", "alias": "go", "roles": ["fanout"], "lifecycle": "current"},
        ]
        registry = SimpleNamespace(
            available_models=lambda: models,
            transports=lambda: ["opencode", "go"],
            transport_meta=lambda _name: {"launcher": ".claude/scripts/shared_sidecar.sh"},
        )
        launchers = scl.sidecar_launchers(registry, "codex")
        self.assertEqual([("opencode", "shared_sidecar.sh", "oc"),
                          ("go", "shared_sidecar.sh", "go")], launchers)
        script = self.root / ".claude" / "scripts" / "shared_sidecar.sh"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        with patch.object(scl, "resolve_bash", return_value="bash"), patch.object(
            scl, "run_bounded", return_value=SimpleNamespace(returncode=0, stdout="OK", stderr="")
        ) as check:
            messages = [scl.verify_sidecar(self.root, name, alias) for _transport, name, alias in launchers]
        self.assertEqual(["oc: OK", "go: OK"], messages)
        self.assertEqual(["oc", "go"], [call.args[0][-1] for call in check.call_args_list])
        self.assertTrue(all(call.args[0][-3] == "--check" and call.args[0][-2] == "-m"
                            for call in check.call_args_list))

    def test_a_plan_launcher_is_listed_only_while_available(self):
        def registry(state):
            meta = {
                "anthropic": {"launcher": ".claude/scripts/anthropic_sidecar.sh"},
                "anthropic-secondary": {"launcher": ".claude/scripts/anthropic_secondary_sidecar.sh",
                                        "planOf": "anthropic", "status": {"state": state}},
                "codex": {"launcher": ".claude/scripts/codex_proxy_sidecar.sh"},
            }
            return SimpleNamespace(
                available_models=lambda: [
                    {"transport": "anthropic", "id": "host-1", "alias": "host", "roles": ["executor"]},
                    {"transport": "codex", "id": "cx-1", "alias": "cx", "roles": ["fanout"]}],
                transports=lambda: sorted(meta),
                transport_meta=lambda name: meta.get(name),
                route_of=lambda name: (meta.get(name) or {}).get("planOf") or name,
            )
        on = scl.sidecar_launchers(registry("available"), "anthropic")
        self.assertIn(("anthropic-secondary", "anthropic_secondary_sidecar.sh", "host"), on)
        self.assertNotIn("anthropic", [t for t, _l, _a in on])
        off = scl.sidecar_launchers(registry("unavailable"), "anthropic")
        self.assertNotIn("anthropic-secondary", [t for t, _l, _a in off])
        back = scl.sidecar_launchers(registry("available"), "anthropic-secondary")
        self.assertIn(("anthropic", "anthropic_sidecar.sh", "host"), back)
        self.assertNotIn("anthropic-secondary", [t for t, _l, _a in back])

    def test_the_refresh_sentinel_does_not_skip_sidecar_health(self):
        # The probe's refresh child runs in its login dir, outside the repo, so this project hook
        # never loads there; the probe's own sentinel stops recursion.
        with patch.dict(scl.os.environ, {"HARNESS_PLAN_REFRESH": "1"}), \
                patch.object(scl, "sidecar_launchers", return_value=[("t", "t_sidecar.sh", "a")]), \
                patch.object(scl, "verify_sidecar", return_value="OK") as verify:
            self.assertEqual({"sidecar-t": "OK"}, scl.sidecar_health(Path(__file__).resolve().parents[2]))
            verify.assert_called_once()

    def test_a_hung_launcher_check_ends_at_its_deadline(self):
        # A launcher whose probe starts a program that keeps the output open: subprocess.run's timeout
        # waited for that program, 97 s per launcher against 45 s at a real SessionStart.
        script = self.root / ".claude" / "scripts" / "hung_sidecar.sh"
        script.parent.mkdir(parents=True)
        holder = self.root / "holder.py"
        holder.write_text("import subprocess, sys, time\n"
                          "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                          "print('probing', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
        script.write_text('"%s" "%s"\n' % (Path(sys.executable).as_posix(), holder.as_posix()), encoding="utf-8")
        started = time.monotonic()
        with patch.object(scl, "CHECK_SECONDS", 3):
            result = scl.verify_sidecar(self.root, "hung_sidecar.sh")
        elapsed = time.monotonic() - started
        self.assertEqual("UNKNOWN (--check timed out)", result)
        self.assertLess(elapsed, 20, "the check outlived its 3 s deadline by %.1f s" % (elapsed - 3))

    def test_sidecar_health_checks_every_launcher_at_once(self):
        # Every check waits at a barrier that opens only when all four are in flight, so a serial
        # loop breaks the barrier instead of racing a clock.
        gate = threading.Barrier(4, timeout=10)
        lock = threading.Lock()
        live = {"now": 0, "peak": 0}

        def check(_root, _name, alias):
            with lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            try:
                gate.wait()
            finally:
                with lock:
                    live["now"] -= 1
            return "%s: OK" % alias
        launchers = [("t%d" % i, "t_sidecar.sh", "a%d" % i) for i in range(4)]
        with patch.object(scl, "sidecar_launchers", return_value=launchers), \
                patch.object(scl, "verify_sidecar", side_effect=check):
            health = scl.sidecar_health(Path(__file__).resolve().parents[2])
        self.assertEqual({"sidecar-t%d" % i: "a%d: OK" % i for i in range(4)}, health)
        self.assertEqual(4, live["peak"])

    def test_unknown_status_never_reports_ok_or_updates(self):
        with patch.object(scl, "submodule_status", return_value=None), patch.object(scl.subprocess, "run") as run:
            self.assertTrue(scl.setup_submodule(self.root).startswith("UNKNOWN"))
            run.assert_not_called()

    def test_divergent_checkout_is_preserved_even_when_clean(self):
        with patch.object(scl, "submodule_status", return_value=[("+", "a" * 40, "Jmodot")]), \
                patch.object(scl, "_advance_behind_pin", return_value=False), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            self.assertTrue(scl.setup_submodule(self.root).startswith("BROKEN"))
            run.assert_not_called()

    def test_safe_initialization_is_independent_of_preserved_divergence(self):
        before = [("+", "a" * 40, "Jmodot"), ("-", "b" * 40, "Empty")]
        after = [("+", "a" * 40, "Jmodot"), (" ", "b" * 40, "Empty")]
        with patch.object(scl, "submodule_status", side_effect=[before, after]), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            result = scl.setup_submodule(self.root)
        self.assertEqual(["git", "submodule", "update", "--init", "--recursive", "--", "Empty"], run.call_args.args[0])
        self.assertTrue(result.startswith("BROKEN"))
        self.assertIn("initialized Empty", result)
        self.assertIn("Jmodot", result)
        self.assertIn("preserved", result)

    def test_dirty_probe_failure_is_unknown(self):
        with patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="")):
            self.assertIsNone(scl._submodule_is_dirty(self.root, "Jmodot"))

    def test_uninitialized_nonempty_directory_is_preserved(self):
        target = self.root / "Jmodot"
        target.mkdir()
        (target / "unsaved.txt").write_text("work", encoding="utf-8")
        with patch.object(scl, "submodule_status", return_value=[("-", "a" * 40, "Jmodot")]), \
                patch.object(scl.subprocess, "run") as run:
            self.assertTrue(scl.setup_submodule(self.root).startswith("BROKEN"))
            run.assert_not_called()

    def test_successful_init_requires_a_successful_followup_probe(self):
        for after in (None, [("-", "a" * 40, "Jmodot")]):
            with self.subTest(after=after), \
                    patch.object(scl, "submodule_status", side_effect=[[("-", "a" * 40, "Jmodot")], after]), \
                    patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
                self.assertFalse(scl.setup_submodule(self.root).startswith("FIXED"))

    def test_dirty_tree_bypasses_same_head_cache(self):
        with patch.object(scg, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scg.store_build_result(self.root, "OK")
            for dirty in (" M file.cs", "?? untracked.cs"):
                with self.subTest(dirty=dirty), patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=dirty)):
                    self.assertIsNone(scg.cached_build_result(self.root))

    def test_failed_worktree_probe_bypasses_cache(self):
        with patch.object(scg, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scg.store_build_result(self.root, "OK")
            with patch.object(scl.subprocess, "run", side_effect=OSError("unavailable")):
                self.assertIsNone(scg.cached_build_result(self.root))

    def test_stored_success_is_visibly_previous_not_current_health(self):
        with patch.object(scg, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scg.store_build_result(self.root, "OK")
            for source in ("startup", "resume", "compact"):
                with self.subTest(source=source):
                    self.assertTrue(scg.build_status(self.root, source, True).startswith("PREVIOUS: OK"))

    def test_git_probe_failure_does_not_report_clean(self):
        with patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="")):
            self.assertIsNone(scl.get_uncommitted_count())
            self.assertEqual("", scg._git_head(self.root))

    def test_empty_import_output_is_not_success(self):
        with patch.object(scg, "get_godot_bin", return_value="fake-godot"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
            self.assertTrue(scg.setup_import_cache(self.root).startswith("FAILED"))

    def test_clean_initialization_is_verified(self):
        entries = [("-", "a" * 40, "Jmodot")]
        after = [(" ", "a" * 40, "Jmodot")]
        with patch.object(scl, "submodule_status", side_effect=[entries, after]), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            self.assertEqual("FIXED (initialized Jmodot)", scl.setup_submodule(self.root))
            self.assertEqual(["git", "submodule", "update", "--init", "--recursive", "--", "Jmodot"], run.call_args.args[0])

    def test_invalid_cache_time_is_not_fresh(self):
        with patch.object(scg, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scg.store_build_result(self.root, "OK")
            cache_path = scg._build_cache_path(self.root)
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            for stamp in (float("nan"), float("inf"), 99999999999):
                with self.subTest(stamp=stamp):
                    cache["ts"] = stamp
                    cache_path.write_text(json.dumps(cache), encoding="utf-8")
                    self.assertIsNone(scg.cached_build_result(self.root))

    def test_main_does_not_build_or_import_when_submodule_is_not_ready(self):
        (self.root / "project.godot").write_text("; planted\n", encoding="utf-8")
        with contextlib.ExitStack() as stack:
            values = {"get_project_root": self.root, "is_worktree": False, "is_cloud": False,
                      "setup_submodule": "BROKEN (not auto-fixed)",
                      "sidecar_launchers": [], "get_git_branch": "main", "get_uncommitted_count": None,
                      "get_recent_commits": []}
            for name, value in values.items():
                stack.enter_context(patch.object(scl, name, return_value=value))
            for name, value in {"get_jmodot_commits": [], "get_godot_bin": "", "godot_docs_cache_issue": None}.items():
                stack.enter_context(patch.object(scg, name, return_value=value))
            stack.enter_context(patch.object(scl, "_STEP_MODULES", (scg,)))
            build = stack.enter_context(patch.object(scg, "verify_build"))
            imports = stack.enter_context(patch.object(scg, "setup_import_cache", return_value="FIXED"))
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps({"source": "startup"}))))
            out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as exited:
                scl.main()
            self.assertEqual(0, exited.exception.code)
            build.assert_not_called()
            imports.assert_not_called()
            self.assertIn("working tree UNKNOWN", out.getvalue())
            self.assertNotIn("Ready to develop", out.getvalue())


    def test_ladder_banner_is_silent_in_a_project_without_the_battery(self):
        # The loader is shared doctrine; a consuming project has no benchmark board to lag behind.
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual("", scl.ladder_banner(Path(td)))

    def test_skill_listing_banner_reads_the_transcript_folder_the_payload_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".claude" / "reference").mkdir(parents=True)
            (root / ".claude" / "reference" / "skill_listing_kept.json").write_text('{"kept": {}}', encoding="utf-8")
            folder = root / "projects"
            folder.mkdir()
            row = {"type": "attachment", "attachment": {"type": "skill_listing", "names": ["dataviz"]}}
            (folder / "old.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            payload = {"transcript_path": str(folder / "current.jsonl")}
            self.assertIn("dataviz", scl.skill_listing_banner(root, payload))
            self.assertEqual("", scl.skill_listing_banner(root, {}))

    def test_owner_banners_are_silent_in_a_sidecar_delegate(self):
        # A delegate can neither rule a listing entry nor run a board sweep; the lines belong to the orchestrator.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".claude" / "reference").mkdir(parents=True)
            (root / ".claude" / "reference" / "skill_listing_kept.json").write_text('{"kept": {}}', encoding="utf-8")
            folder = root / "projects"
            folder.mkdir()
            row = {"type": "attachment", "attachment": {"type": "skill_listing", "names": ["dataviz"]}}
            (folder / "old.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            payload = {"transcript_path": str(folder / "current.jsonl")}
            with patch.dict("os.environ", {"CLAUDE_CODE_SIDECAR": "1"}):
                self.assertEqual("", scl.skill_listing_banner(root, payload))
                self.assertEqual("", scl.ladder_banner(Path(__file__).resolve().parents[2]))


class RealSubmoduleTests(unittest.TestCase):
    """setup_submodule against real git: a parent whose Jmodot gitlink names the newest commit."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.upstream = base / "upstream"
        self.parent = base / "parent"
        self.upstream.mkdir()
        self.parent.mkdir()
        self.git(self.upstream, "init", "-q", "-b", "master")
        self.commits = [self.commit(self.upstream, "f.txt", str(n)) for n in range(3)]
        self.git(self.parent, "init", "-q", "-b", "main")
        self.git(self.parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(self.upstream), "Jmodot")
        self.git(self.parent, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                 "commit", "-q", "-m", "pin")
        self.sub = self.parent / "Jmodot"

    @staticmethod
    def git(cwd, *args):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self, cwd, name, text):
        (Path(cwd) / name).write_text(text, encoding="utf-8")
        self.git(cwd, "add", name)
        self.git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                 "commit", "-q", "-m", text)
        return self.git(cwd, "rev-parse", "HEAD")

    def head(self):
        return self.git(self.sub, "rev-parse", "HEAD")

    def test_detached_checkout_behind_the_pin_moves_to_it(self):
        self.git(self.sub, "checkout", "-q", "--detach", self.commits[0])
        self.assertEqual("FIXED (advanced Jmodot)", scl.setup_submodule(self.parent))
        self.assertEqual(self.commits[2], self.head())

    def test_branch_checkout_behind_the_pin_fast_forwards_the_branch(self):
        self.git(self.sub, "checkout", "-q", "-B", "master", self.commits[0])
        self.assertEqual("FIXED (advanced Jmodot)", scl.setup_submodule(self.parent))
        self.assertEqual(self.commits[2], self.head())
        self.assertEqual("master", self.git(self.sub, "symbolic-ref", "--short", "HEAD"))

    def test_checkout_that_holds_independent_work_is_preserved(self):
        self.git(self.sub, "checkout", "-q", "--detach", self.commits[0])
        own = self.commit(self.sub, "own.txt", "own")
        self.assertTrue(scl.setup_submodule(self.parent).startswith("BROKEN"))
        self.assertEqual(own, self.head())

    def test_checkout_ahead_of_the_pin_is_preserved(self):
        own = self.commit(self.sub, "own.txt", "own")
        self.assertTrue(scl.setup_submodule(self.parent).startswith("BROKEN"))
        self.assertEqual(own, self.head())

    def test_dirty_checkout_behind_the_pin_is_preserved(self):
        self.git(self.sub, "checkout", "-q", "--detach", self.commits[0])
        (self.sub / "f.txt").write_text("unsaved", encoding="utf-8")
        self.assertTrue(scl.setup_submodule(self.parent).startswith("BROKEN"))
        self.assertEqual(self.commits[0], self.head())
        self.assertEqual("unsaved", (self.sub / "f.txt").read_text(encoding="utf-8"))

    def test_untracked_file_in_a_checkout_behind_the_pin_is_preserved(self):
        self.git(self.sub, "checkout", "-q", "--detach", self.commits[0])
        (self.sub / "scratch.txt").write_text("keep", encoding="utf-8")
        self.assertTrue(scl.setup_submodule(self.parent).startswith("BROKEN"))
        self.assertEqual(self.commits[0], self.head())


if __name__ == "__main__":
    unittest.main()
