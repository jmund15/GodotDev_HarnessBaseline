#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader.py: resume/compact never spawns
dotnet and reports the stored verify; stale startup state builds; a not-ready submodule skips;
SessionStart probes only available off-transport sidecars. Pure in-process — no git or dotnet.

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
import time
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
            failures.append(label + (": " + detail[:200] if detail else ""))

    root = Path(tempfile.mkdtemp(prefix="scl_"))
    scl._git_head = lambda _root: "abc123"
    scl._submodule_is_dirty = lambda _root, _path: False
    calls = []

    def fake_verify(_root):
        calls.append(1)
        return "OK (0 warnings)"

    # Stored FAILED verify, 3h old, HEAD moved: resume reports it, tagged, without building.
    scl.store_build_result(root, "FAILED (6 errors, 0 warnings)")
    cache_path = scl._build_cache_path(root)
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    data["ts"] = time.time() - 3 * 3600
    data["head"] = "old"
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    for source in scl.NO_BUILD_SOURCES:
        out = scl.build_status(root, source, True, verify=fake_verify)
        check("%s: no dotnet spawn" % source, not calls)
        check("%s: reports the stored FAILED verify" % source, out.startswith("PREVIOUS: FAILED (6 errors"), out)
        check("%s: tag names age, HEAD move and the skip" % source,
              "180m ago" in out and "HEAD moved" in out and "%s skips the build" % source in out, out)

    # Startup with that stale/failed cache builds and stores the fresh result.
    out = scl.build_status(root, "startup", True, verify=fake_verify)
    check("startup: builds when cache is stale", len(calls) == 1 and out == "OK (0 warnings)", out)
    check("startup: stores the fresh verify",
          json.loads(cache_path.read_text(encoding="utf-8"))["result"] == "OK (0 warnings)")

    # Startup again inside the TTL, same HEAD: served from cache, no second build.
    out = scl.build_status(root, "startup", True, verify=fake_verify)
    check("startup: fresh same-HEAD OK is served from cache", len(calls) == 1 and "[cached" in out, out)

    # Submodule not ready wins over everything; resume with no cache says so.
    check("submodule not ready skips", scl.build_status(root, "startup", False, verify=fake_verify)
          .startswith("SKIPPED (submodule"))
    cache_path.unlink()
    out = scl.build_status(root, "compact", True, verify=fake_verify)
    check("compact with no stored verify: SKIPPED, still no build",
          out == "SKIPPED (compact: no stored verify)" and len(calls) == 1, out)

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

    for source in scl.NO_BUILD_SOURCES:
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
    # the continuation guidance emitted to stdout.
    with tempfile.TemporaryDirectory(prefix="scl_main_") as main_dir:
        main_root = Path(main_dir)
        patches = {
            "get_project_root": lambda: main_root,
            "is_worktree": lambda: False,
            "is_cloud": lambda: False,
            "sweep_stray_search_indexes": lambda _root: None,
            "setup_submodule": lambda _root: "OK",
            "setup_import_cache": lambda _root: "OK",
            "build_status": lambda _root, _source, _ready: "OK",
            "verify_lsp_plugin": lambda: "OK",
            "sidecar_health": lambda _root: {
                "sidecar-a": "opus: OK (model=m endpoint=ENDPOINT-DETAIL) window=off-peak until 01:00 UTC",
                "sidecar-b": "sol: shape rating: none exists"},
            "roster_health": lambda _root: "OK",
            "get_git_branch": lambda: "main",
            "get_uncommitted_count": lambda: 0,
            "get_upstream_drift": lambda: (24, 3, 30.0),
            "get_recent_commits": lambda count=3, cwd=None: [],
            "get_jmodot_commits": lambda _root, count=3: [],
            "get_godot_bin": lambda: "",
            "self_improvement_advisory": lambda _root, _source: "",
            "godot_docs_cache_issue": lambda _root: None,
        }
        payload = {"source": "startup", "session_id": "main-proof", "cwd": str(main_root)}
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch.dict(os.environ, {}, clear=False):
            with contextlib.ExitStack() as stack:
                for name, value in patches.items():
                    stack.enter_context(patch.object(scl, name, value))
                stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(payload))))
                try:
                    scl.main()
                except SystemExit as exc:
                    check("SessionStart main exits successfully", exc.code == 0, repr(exc.code))
        output = stdout.getvalue()
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

        # A cloud VM has no user or local settings, so neither path-scoped rules nor the
        # autoMemoryDirectory index reach the model unless SessionStart injects them.
        (main_root / ".claude" / "rules").mkdir(parents=True)
        (main_root / ".claude" / "auto-memory").mkdir(parents=True)
        (main_root / ".claude" / "rules" / "cloud_dev.md").write_text(
            "---\npaths:\n  - \"**/cloud-setup.sh\"\n---\n\n# Cloud\nCONTRACT-MARKER\n", encoding="utf-8")
        (main_root / ".claude" / "auto-memory" / "MEMORY.md").write_text(
            "# Memory Index\n- MEMORY-MARKER\n", encoding="utf-8")
        sidecar_probes = []
        cloud_patches = dict(patches, is_cloud=lambda: True,
                             sidecar_health=lambda _root: sidecar_probes.append(1) or {"sidecar-x": "UNAVAILABLE"},
                             run_cloud_setup=lambda _root: "OK (toolchain present)",
                             setup_runsettings=lambda _root: "OK",
                             persist_cloud_env=lambda _godot_bin: None)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with contextlib.ExitStack() as stack:
                for name, value in cloud_patches.items():
                    stack.enter_context(patch.object(scl, name, value))
                stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(
                    dict(payload, source="compact")))))
                try:
                    scl.main()
                except SystemExit:
                    pass
        cloud_out = stdout.getvalue()
        check("cloud session gets the cloud contract body", "<cloud-session>" in cloud_out
              and "CONTRACT-MARKER" in cloud_out, cloud_out[-800:])
        check("cloud contract drops the rule frontmatter", "paths:" not in cloud_out)
        check("cloud session gets the memory index", "MEMORY-MARKER" in cloud_out)
        check("cloud session is not told to load an unprovisioned plugin tool",
              "select:mcp__plugin_semantic-search" not in cloud_out)
        check("cloud reload reminder names the Grep fallback for memory search",
              "Grep over .claude/auto-memory/" in cloud_out)
        check("cloud session probes no sidecar (the contract lists them absent) and prints no status line for one",
              not sidecar_probes and "sidecar-x" not in cloud_out, (sidecar_probes, cloud_out[:600]))
        check("cloud status key names the setup script", "cloud_setup OK" in cloud_out, cloud_out[:600])

        # An unterminated frontmatter fence or an undecodable byte must not crash SessionStart.
        (main_root / ".claude" / "rules" / "cloud_dev.md").write_text("---\npaths: x\nBODY-MARKER\n",
                                                                      encoding="utf-8")
        (main_root / ".claude" / "auto-memory" / "MEMORY.md").write_bytes(b"# idx\n\xff\xfe bad\n")
        try:
            blocks = "\n".join(scl.cloud_session_blocks(main_root))
            check("unterminated frontmatter keeps the body and does not raise", "BODY-MARKER" in blocks, blocks)
        except Exception as exc:
            check("unterminated frontmatter keeps the body and does not raise", False, repr(exc))

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
        ws_patches = dict(patches, get_project_root=lambda: ws_root)

        def run_ws_main(env_extra, extra_patches=None):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), patch.dict(os.environ, env_extra):
                with contextlib.ExitStack() as stack:
                    for name, value in ws_patches.items():
                        stack.enter_context(patch.object(scl, name, value))
                    for name, value in (extra_patches or {}).items():
                        stack.enter_context(patch.object(scl, name, value))
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

    # run_cloud_setup branches, with the toolchain probes, the snapshot marker and the script run
    # planted. `cloud_setup_current` is the seam for the marker: the tool's own compare is proven in
    # test_workstation_profile.py; here it only has to reach the right branch.
    class Done:
        def __init__(self, out):
            self.stdout, self.returncode = out, 0

    with tempfile.TemporaryDirectory(prefix="scl_setup_") as tmp:
        root = Path(tmp)
        (root / ".claude").mkdir()
        env = {"CLAUDE_CODE_REMOTE": "true"}
        with patch.dict(os.environ, env), patch.object(scl, "_cmd_exists", lambda _c: False), \
                patch.object(scl, "get_godot_bin", lambda: ""), \
                patch.object(scl, "cloud_setup_current", lambda _root: True):
            check("missing cloud-setup.sh is FAILED", scl.run_cloud_setup(root).startswith("FAILED"))
            (root / ".claude" / "cloud-setup.sh").write_text("exit 0\n", encoding="utf-8")
            out = ("[cloud-setup] prefix=/opt\n[cloud-setup] FAILED: apt install xvfb\n"
                   "[cloud-setup] FAILED: .NET SDK install\n")
            with patch.object(scl.subprocess, "run", lambda *a, **k: Done(out)):
                status = scl.run_cloud_setup(root)
            check("every FAILED line reaches the status",
                  "apt install xvfb" in status and ".NET SDK install" in status, status)
        docs_dir = root / ".claude" / "scripts"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "godot_docs_cache.sh").write_text("exit 0\n", encoding="utf-8")
        ran = []

        def record(argv, *a, **k):
            ran.append(Path(argv[-1]).name)
            return Done("[cloud-setup] FAILED: .NET installer download (dot.net)\n")

        with patch.dict(os.environ, env), patch.object(scl, "_cmd_exists", lambda _c: False), \
                patch.object(scl, "get_godot_bin", lambda: ""), \
                patch.object(scl, "cloud_setup_current", lambda _root: True), \
                patch.object(scl.subprocess, "run", record):
            status = scl.run_cloud_setup(root)
        check("a failed toolchain install still builds the docs cache (it needs only GitHub)",
              status.startswith("FAILED") and "godot_docs_cache.sh" in ran, (status, ran))

        for label, current, want in (("a current marker", True, "OK"),
                                     ("a stale marker", False, "FIXED (snapshot script is stale")):
            with patch.dict(os.environ, env), patch.object(scl, "_cmd_exists", lambda _c: True), \
                    patch.object(scl, "get_godot_bin", lambda: "/opt/godot/g"), \
                    patch.object(scl, "cloud_setup_current", lambda _root, c=current: c), \
                    patch.object(scl.subprocess, "run", lambda *a, **k: Done("")):
                status = scl.run_cloud_setup(root)
            check("a present toolchain with %s reports %s" % (label, want),
                  status.startswith(want), status)
            if current:
                check("a current marker never re-runs the script", "FIXED" not in status, status)
            else:
                check("a stale snapshot script says to re-paste the repo copy",
                      "paste .claude/cloud-setup.sh" in status, status)

        def unloadable(_root):
            raise OSError("profile gone")

        with patch.object(scl, "_load_workstation_profile", unloadable):
            check("an unreadable profile keeps the toolchain probe's answer (fail open, no re-run)",
                  scl.cloud_setup_current(root) is True, scl.cloud_setup_current(root))

    # CLAUDE_ENV_FILE lines must export: a sourced plain assignment never reaches child processes.
    with tempfile.TemporaryDirectory(prefix="scl_env_") as tmp:
        env_file = os.path.join(tmp, "env")
        with patch.dict(os.environ, {"CLAUDE_ENV_FILE": env_file, "DOTNET_ROOT": "/opt/dotnet"}):
            scl.persist_cloud_env("/opt/godot/g")
        with open(env_file, encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line]
        check("every persisted env line exports",
              bool(lines) and all(line.startswith("export ") for line in lines), repr(lines))

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

    with tempfile.TemporaryDirectory(prefix="scl_rs_") as tmp:
        root = Path(tmp)
        cli = [sys.executable, os.path.join(HERE, "..", "hooks", "session_context_loader.py"), "--setup-runsettings", str(root)]
        env = dict(os.environ, GODOT_BIN=sys.executable)
        run = lambda: subprocess.run(cli, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        no_template = run()
        check("--setup-runsettings without a template prints SKIPPED, exits 1, writes nothing",
              no_template.returncode == 1 and no_template.stdout.startswith("SKIPPED")
              and not (root / ".runsettings").exists(), no_template.stdout + no_template.stderr)
        (root / ".runsettings.template").write_text("<B>{{GODOT_BIN}}</B>", encoding="utf-8")
        made = run()
        check("--setup-runsettings generates .runsettings from the template, exit 0",
              made.returncode == 0 and made.stdout.startswith("FIXED")
              and (root / ".runsettings").read_text(encoding="utf-8") == "<B>%s</B>" % sys.executable,
              made.stdout + made.stderr)
        (root / ".runsettings").write_text("keep", encoding="utf-8")
        again = run()
        check("--setup-runsettings never overwrites an existing .runsettings",
              again.returncode == 0 and again.stdout.strip() == "OK"
              and (root / ".runsettings").read_text(encoding="utf-8") == "keep", again.stdout)

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
