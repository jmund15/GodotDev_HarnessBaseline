#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader.py, the generic SessionStart steps: sidecar
probes only for available off-transport routes, the due line, the git and upstream-drift context, the
memory index, the setup stamp, and the step-module seam (an absent module is skipped, a present one is
called, a faulty one is reported and never blocks). The engine and language-server steps are proven in
test_session_context_loader_godot.py and test_session_context_loader_csharp.py. Pure in-process — no
git or dotnet.

    python3 .claude/tests/test_session_context_loader.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import subprocess
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import session_context_loader as scl  # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + str(detail)[:200] if detail else ""))

    class FakeRegistry:
        @staticmethod
        def transports():
            return ["deepseek", "codex", "opencode", "anthropic"]

        @staticmethod
        def transport_meta(name):
            return {"launcher": ".claude/scripts/%s_sidecar.sh" % name}

        @staticmethod
        def available_models():
            return [
                {"transport": "codex", "alias": "codex-row"},
                {"transport": "opencode", "alias": "opencode-row"},
                {"transport": "anthropic", "alias": "anthropic-row"},
            ]

    launchers = scl.sidecar_launchers(FakeRegistry, "codex")
    check("sidecar checks skip the current and unavailable transports",
          launchers == [("opencode", "opencode_sidecar.sh", "opencode-row"),
                        ("anthropic", "anthropic_sidecar.sh", "anthropic-row")], str(launchers))

    # --- self_improvement_advisory: startup-only, silent when nothing is due -----------
    # Run the live tool against isolated data, without copying its dependency graph.
    due_root = Path(tempfile.mkdtemp(prefix="scl_due_"))
    claude_dir = due_root / ".claude"
    (claude_dir / "tools").mkdir(parents=True)
    (claude_dir / "logs").mkdir(parents=True)
    real_claude = Path(__file__).resolve().parents[1]
    (claude_dir / "tools" / "self_improvement_due.py").write_text(
        "import runpy, sys\n"
        + f"sys.path.insert(0, {str(real_claude / 'tools')!r})\n"
        + f"runpy.run_path({str(real_claude / 'tools/self_improvement_due.py')!r}, run_name='__main__')\n",
        encoding="utf-8", newline="\n")
    (claude_dir / "self_evaluate_archive.json").write_text(
        json.dumps({"structured_entries": [{"session_id": "s1"}, {"session_id": "s2"}]}),
        encoding="utf-8")
    (claude_dir / "orchestration_candidates.json").write_text("{}", encoding="utf-8")
    (claude_dir / "logs" / "eval_dashboard_last.json").write_text(
        json.dumps({"row_count": 2, "date": "2026-09-14"}), encoding="utf-8")

    for source in ("resume", "compact"):
        out = scl.self_improvement_advisory(due_root, source)
        check("%s: self-improvement advisory prints nothing on re-entry" % source, out == "", repr(out))

    out = scl.self_improvement_advisory(due_root, "startup")
    check("startup: nothing due prints nothing", out == "", repr(out))

    (claude_dir / "self_evaluate_archive.json").write_text(
        json.dumps({"structured_entries": [{"session_id": "s%d" % i} for i in range(12)]}),
        encoding="utf-8")
    out = scl.self_improvement_advisory(due_root, "startup")
    check("startup: a due condition prints one non-empty line",
          out.startswith("Self-improvement due:") and "\n" not in out, repr(out))

    # Startup only: `clear`, a missing source and an empty source print nothing while the line is due.
    for source in ("clear", None, ""):
        out = scl.self_improvement_advisory(due_root, source)
        check("%r: a due line prints nothing outside startup" % (source,), out == "", repr(out))

    # The due tool waits up to its store's 10 s lock; the loader must outwait it.
    seen = {}
    real_run = scl.subprocess.run

    def recording_run(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return real_run(*args, **kwargs)

    scl.subprocess.run = recording_run
    try:
        scl.self_improvement_advisory(due_root, "startup")
    finally:
        scl.subprocess.run = real_run
    check("due-line subprocess timeout is 15 s, above the store's 10 s lock wait",
          seen.get("timeout") == 15, repr(seen))

    # The SessionStart proof must exercise the registered script's main() output path, not only
    # inspect a helper string. Every expensive setup branch is planted so the assertion observes
    # the continuation guidance emitted to stdout. No step module is installed for these runs.
    payload = {"source": "startup", "session_id": "main-proof"}

    def run_main(main_root, patches, source="startup", step_modules=(), sidecar=False):
        stdout = io.StringIO()
        scl._STEP_FAULTS.clear()
        with contextlib.redirect_stdout(stdout), patch.dict(os.environ, {}, clear=False):
            if sidecar:
                os.environ["CLAUDE_CODE_SIDECAR"] = "1"
            else:
                os.environ.pop("CLAUDE_CODE_SIDECAR", None)
            with contextlib.ExitStack() as stack:
                for name, value in dict(patches, get_project_root=lambda: main_root).items():
                    stack.enter_context(patch.object(scl, name, value))
                stack.enter_context(patch.object(scl, "_STEP_MODULES", step_modules))
                stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(dict(payload, source=source)))))
                try:
                    scl.main()
                except SystemExit as exc:
                    check("SessionStart main exits successfully", exc.code == 0, repr(exc.code))
        return stdout.getvalue()

    with tempfile.TemporaryDirectory(prefix="scl_main_") as main_dir:
        main_root = Path(main_dir)
        patches = {
            "is_worktree": lambda: False,
            "is_cloud": lambda: False,
            "sweep_stray_search_indexes": lambda _root: None,
            "setup_submodule": lambda _root: "OK",
            "sidecar_health": lambda _root: {
                "sidecar-a": "opus: OK (model=m endpoint=ENDPOINT-DETAIL) window=off-peak until 01:00 UTC",
                "sidecar-b": "sol: shape rating: none exists"},
            "roster_health": lambda _root: "OK",
            "get_git_branch": lambda: "main",
            "get_uncommitted_count": lambda: 0,
            "get_upstream_drift": lambda: (24, 3, 30.0),
            "get_recent_commits": lambda count=3, cwd=None: [],
            "self_improvement_advisory": lambda _root, _source: "",
        }
        output = run_main(main_root, patches)
        check("a fresh startup carries no reload reminder (the session_digest listing owns pickup)",
              "<context-reload-reminder>" not in output and "session_digest.py" not in output, output[-600:])
        check("an OK sidecar prints its model and trailing facts, not its probe detail",
              "sidecar-a: opus OK window=off-peak until 01:00 UTC" in output
              and "ENDPOINT-DETAIL" not in output, output[:800])
        check("a sidecar that is not OK keeps its full status",
              "sidecar-b: sol: shape rating: none exists" in output, output[:800])
        check("a local session gets no cloud contract", "<cloud-session>" not in output)
        check("the git line states how far main is from its upstream and how old the oldest unpushed commit is",
              "Git: main | clean | 24 ahead, 3 behind upstream as last fetched (oldest unpushed 30h)" in output, output[-900:])
        check("no step module installed: no engine, Jmodot or language-server lines",
              "GODOT_BIN" not in output and "Jmodot" not in output and "lsp-early-load" not in output
              and "Session step modules" not in output, output[-700:])

        # A cloud VM has no user or local settings, so the autoMemoryDirectory index does not
        # reach the model unless SessionStart injects it.
        (main_root / ".claude" / "auto-memory").mkdir(parents=True)
        (main_root / ".claude" / "auto-memory" / "MEMORY.md").write_text(
            "# Memory Index\n- MEMORY-MARKER\n", encoding="utf-8")
        sidecar_probes = []
        cloud_patches = dict(patches, is_cloud=lambda: True,
                             sidecar_health=lambda _root: sidecar_probes.append(1) or {"sidecar-x": "UNAVAILABLE"})
        cloud_out = run_main(main_root, cloud_patches, source="compact")
        check("cloud session gets the memory index", "MEMORY-MARKER" in cloud_out)
        check("cloud session is not told to load an unprovisioned plugin tool",
              "select:mcp__plugin_semantic-search" not in cloud_out)
        check("cloud reload reminder names the Grep fallback for memory search",
              "Grep over .claude/auto-memory/" in cloud_out)
        check("cloud session probes no sidecar (the contract lists them absent) and prints no status line for one",
              not sidecar_probes and "sidecar-x" not in cloud_out, (sidecar_probes, cloud_out[:600]))

        # An undecodable byte must not crash SessionStart.
        (main_root / ".claude" / "auto-memory" / "MEMORY.md").write_bytes(b"# idx\n\xff\xfe bad\n")
        try:
            blocks = "\n".join(scl.cloud_session_blocks(main_root))
            check("an undecodable memory index does not raise", "<auto-memory-index" in blocks, blocks)
        except Exception as exc:
            check("an undecodable memory index does not raise", False, repr(exc))

        # The step-module seam: a present module's hooks are called in session order and add their
        # lines; a hook that raises is reported once and never blocks the session.
        order = []
        step = types.SimpleNamespace(
            early_setup=lambda root, cloud: order.append("early_setup") or {"step_early": "OK"},
            setup=lambda root, source, worktree, cloud, ready: order.append("setup") or {"step_setup": "FIXED (x)"},
            local_checks=lambda root: order.append("local_checks") or {"step_local": "OK"},
            env_lines=lambda root, cloud: ["  STEP-ENV-LINE"],
            context_lines=lambda root, recent: ["", "STEP-CONTEXT"],
            nudge_lines=lambda results, cloud: ["", "STEP-NUDGE:" + results.get("step_local", "?")],
            report_lines=lambda root: ["", "STEP-REPORT"],
            persist_env=lambda root, cloud: order.append("persist_env"),
        )
        output = run_main(main_root, patches, step_modules=(step,))
        check("a step module's setup statuses reach the Env: line in session order",
              "step_early OK" in output and "step_setup: FIXED (x)" in output and "step_local OK" in output
              and order == ["early_setup", "setup", "local_checks", "persist_env"], (order, output[:600]))
        check("a step module's lines reach the output",
              all(text in output for text in ("STEP-ENV-LINE", "STEP-CONTEXT", "STEP-NUDGE:OK", "STEP-REPORT")),
              output[-600:])
        check("a FIXED step status is summarised as auto-fixed", "Auto-fixed the items marked FIXED" in output)
        cloud_order = []
        step.local_checks = lambda root: cloud_order.append("local_checks") or {}
        run_main(main_root, cloud_patches, step_modules=(step,))
        check("local-only checks do not run on cloud", cloud_order == [], cloud_order)

        def broken(*_args):
            raise ValueError("hook bug")

        step.setup = broken
        output = run_main(main_root, patches, step_modules=(step,))
        check("a raising hook is reported and the session context still prints",
              "Session step modules: FAILED (setup ValueError: hook bug)" in output
              and "Git: main |" in output and "STEP-REPORT" in output, output[-500:])

        # An installed module that cannot be imported is named, and its siblings still load.
        scl._STEP_FAULTS.clear()
        check("an import fault is recorded by the module fault hook",
              scl._step_fault("mod", ImportError("boom")) is None and "mod import ImportError: boom" in scl._STEP_FAULTS)
        scl._STEP_FAULTS.clear()

    # The runsettings entry point without the engine module installed generates nothing.
    with patch.object(scl, "_STEP_MODULES", ()), contextlib.redirect_stdout(io.StringIO()) as printed:
        code = scl.runsettings_cli(["unused"])
    check("--setup-runsettings without the engine module prints SKIPPED and exits 1",
          code == 1 and printed.getvalue().startswith("SKIPPED"), printed.getvalue())

    # A project without the benchmark battery gets the generic context and no roster status.
    with tempfile.TemporaryDirectory(prefix="scl_plain_") as plain_dir:
        plain_root = Path(plain_dir)
        check("a project without the benchmark battery reports no roster status",
              scl.roster_health(plain_root) == "", scl.roster_health(plain_root))
        for is_cloud_session in (False, True):
            plain_out = run_main(plain_root, dict(patches, is_cloud=lambda: is_cloud_session,
                                                  is_worktree=lambda: True))
            label = "cloud" if is_cloud_session else "local"
            check("plain %s session: no engine or language-server lines" % label,
                  "GODOT_BIN" not in plain_out and "Jmodot" not in plain_out
                  and "lsp-early-load" not in plain_out, plain_out[-700:])
            check("plain %s session: the git context is still injected" % label,
                  "Git: main |" in plain_out and "Recent {{PROJECT_NAME}} commits:" in plain_out, plain_out[-700:])

    # A sidecar child executes a brief; the owner's Worktree, Env, Git and commit lines are noise to it.
    with tempfile.TemporaryDirectory(prefix="scl_sidecar_") as sidecar_dir:
        for sidecar_source in ("startup", "compact"):
            delegate_out = run_main(Path(sidecar_dir), patches, source=sidecar_source, sidecar=True)
            check("sidecar %s: no Worktree, Env, Git or commit block" % sidecar_source,
                  not any(marker in delegate_out for marker in
                          ("Worktree:", "Env: ", "Git: main |", "Recent {{PROJECT_NAME}} commits:")),
                  delegate_out[-700:])
            check("sidecar %s: still prints the session-context envelope" % sidecar_source,
                  "<session-context>" in delegate_out and "</session-context>" in delegate_out, delegate_out[-300:])

    # The local setup stamp reaches the model through the Env: line. The tool is copied into the
    # fixture root and the command's own frontmatter is the declaration under test, so this is the
    # hook's real path resolution reading the real declaration — not an import of the repo's tool.
    def load_by_path(path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fixture_workstation_profile", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def env_line(text):
        return next((line for line in text.splitlines() if line.startswith("Env: ")), "")

    with tempfile.TemporaryDirectory(prefix="scl_ws_") as ws_dir:
        ws_root = Path(ws_dir)
        real_claude = Path(__file__).resolve().parents[1]
        (ws_root / ".claude" / "tools").mkdir(parents=True)
        (ws_root / ".claude" / "commands").mkdir(parents=True)
        shutil.copyfile(real_claude / "tools" / "workstation_profile.py",
                        ws_root / ".claude" / "tools" / "workstation_profile.py")
        (ws_root / ".claude" / "commands" / "workstation_setup.md").write_text(
            "---\ndescription: planted\nstamp-inputs:\n" + "".join(
                '  - "%s"\n' % rel for rel in (".mcp.json", ".claude/reference/project_stack.md", "global.json",
                                             "nuget.config", ".claude/commands/workstation_setup.md"))
            + "---\n", encoding="utf-8", newline="\n")
        for rel in (".mcp.json", ".claude/reference/project_stack.md", "global.json", "nuget.config"):
            target = ws_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("planted\n", encoding="utf-8")
        ws_profile = ws_root / "profile.json"

        def run_ws_main(env_extra, extra_patches=None):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), patch.dict(os.environ, env_extra):
                os.environ.pop("CLAUDE_CODE_SIDECAR", None)
                with contextlib.ExitStack() as stack:
                    for name, value in dict(patches, get_project_root=lambda: ws_root, **(extra_patches or {})).items():
                        stack.enter_context(patch.object(scl, name, value))
                    stack.enter_context(patch.object(scl, "_STEP_MODULES", ()))
                    stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(payload))))
                    try:
                        scl.main()
                    except SystemExit:
                        pass
            return stdout.getvalue()

        env_ws = {"WORKSTATION_PROFILE_PATH": str(ws_profile)}
        out = run_ws_main(env_ws)
        check("no recorded stamp: the Env: line says UNAUDITED",
              "workstation: UNAUDITED (run /workstation_setup)" in env_line(out), env_line(out))

        with patch.dict(os.environ, env_ws):
            ws_module = load_by_path(ws_root / ".claude" / "tools" / "workstation_profile.py")
            stamped = ws_module.stamp(ws_root, preflight_failures=[])
        check("the fixture checkout stamps", stamped[0], stamped)
        out = run_ws_main(env_ws)
        check("a stamped checkout: the Env: line says workstation OK", "workstation OK" in env_line(out),
              env_line(out))

        (ws_root / ".mcp.json").write_text("drifted\n", encoding="utf-8")
        out = run_ws_main(env_ws)
        check("a changed setup input: the Env: line names it STALE",
              "workstation: STALE (changed: .mcp.json; run /workstation_setup)" in env_line(out),
              env_line(out))

        def unloadable(_root):
            raise OSError("tool gone")

        out = run_ws_main(env_ws, {"_load_workstation_profile": unloadable})
        check("a tool that cannot be loaded omits the step (fail open)",
              "workstation" not in env_line(out), env_line(out))

    # The drift reader against a planted clone: two local commits on top of its upstream.
    with tempfile.TemporaryDirectory(prefix="scl_drift_") as tmp:
        def g(*args, cwd):
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
        origin, clone = os.path.join(tmp, "origin.git"), os.path.join(tmp, "clone")
        g("init", "-q", "--bare", "-b", "main", origin, cwd=tmp)
        g("clone", "-q", origin, clone, cwd=tmp)
        for n in range(3):
            g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c%d" % n, cwd=clone)
            if n == 0:
                g("push", "-q", "origin", "HEAD:main", cwd=clone)
                g("branch", "-q", "--set-upstream-to=origin/main", cwd=clone)
        cwd = os.getcwd()
        os.chdir(clone)
        try:
            drift = scl.get_upstream_drift()
        finally:
            os.chdir(cwd)
        check("the drift reader counts unpushed commits against the upstream",
              drift is not None and drift[:2] == (2, 0) and 0 <= drift[2] < 1, repr(drift))

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
