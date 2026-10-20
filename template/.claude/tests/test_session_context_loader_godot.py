#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader_godot.py, the godot step module the loader
loads through `_optional_hooks`: the build verdict and its cache, the cloud toolchain install, the
`.runsettings` generator, the cloud contract and the lines each hook adds to the loader's output.
Pure in-process, no git, dotnet or engine.

    python3 .claude/tests/test_session_context_loader_godot.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import session_context_loader as scl  # noqa: E402
import session_context_loader_godot as scg  # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + detail[:200] if detail else ""))

    root = Path(tempfile.mkdtemp(prefix="scg_"))
    scg._git_head = lambda _root: "abc123"
    scg._tree_dirty = lambda _root: False
    calls = []

    def fake_verify(_root):
        calls.append(1)
        return "OK (0 warnings)"

    # Stored FAILED verify, 3h old, HEAD moved: resume reports it, tagged, without building.
    scg.store_build_result(root, "FAILED (6 errors, 0 warnings)")
    cache_path = scg._build_cache_path(root)
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    data["ts"] = time.time() - 3 * 3600
    data["head"] = "old"
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    for source in scg.NO_BUILD_SOURCES:
        out = scg.build_status(root, source, True, verify=fake_verify)
        check("%s: no dotnet spawn" % source, not calls)
        check("%s: reports the stored FAILED verify" % source, out.startswith("PREVIOUS: FAILED (6 errors"), out)
        check("%s: tag names age, HEAD move and the skip" % source,
              "180m ago" in out and "HEAD moved" in out and "%s skips the build" % source in out, out)

    # Startup with that stale/failed cache builds and stores the fresh result.
    out = scg.build_status(root, "startup", True, verify=fake_verify)
    check("startup: builds when cache is stale", len(calls) == 1 and out == "OK (0 warnings)", out)
    check("startup: stores the fresh verify",
          json.loads(cache_path.read_text(encoding="utf-8"))["result"] == "OK (0 warnings)")

    # Startup again inside the TTL, same HEAD: served from cache, no second build.
    out = scg.build_status(root, "startup", True, verify=fake_verify)
    check("startup: fresh same-HEAD OK is served from cache", len(calls) == 1 and "[cached" in out, out)

    # Submodule not ready wins over everything; resume with no cache says so.
    check("submodule not ready skips", scg.build_status(root, "startup", False, verify=fake_verify)
          .startswith("SKIPPED (submodule"))
    cache_path.unlink()
    out = scg.build_status(root, "compact", True, verify=fake_verify)
    check("compact with no stored verify: SKIPPED, still no build",
          out == "SKIPPED (compact: no stored verify)" and len(calls) == 1, out)

    # The hooks reach the loader's output through main(). Every expensive branch is planted.
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a Godot-only step ran in a non-Godot project")

    def run_main(main_root, cloud, source, scl_patches=None, scg_patches=None, env=None):
        base = {
            "get_project_root": lambda: main_root,
            "is_worktree": lambda: False,
            "is_cloud": lambda: cloud,
            "sweep_stray_search_indexes": lambda _root: None,
            "setup_submodule": lambda _root: "OK",
            "sidecar_health": lambda _root: {},
            "roster_health": lambda _root: "",
            "workstation_status": lambda _root: None,
            "get_git_branch": lambda: "main",
            "get_uncommitted_count": lambda: 0,
            "get_upstream_drift": lambda: None,
            "get_recent_commits": lambda count=3, cwd=None: [],
            "self_improvement_advisory": lambda _root, _source: "",
        }
        godot = {
            "setup_import_cache": lambda _root: "OK",
            "build_status": lambda _root, _source, _ready: "OK",
            "get_jmodot_commits": lambda _root, _recent, count=3: [],
            "get_godot_bin": lambda: "",
            "godot_docs_cache_issue": lambda _root: None,
        }
        base.update(scl_patches or {})
        godot.update(scg_patches or {})
        scl._STEP_FAULTS.clear()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch.dict(os.environ, env or {}), contextlib.ExitStack() as stack:
            for name, value in base.items():
                stack.enter_context(patch.object(scl, name, value))
            for name, value in godot.items():
                stack.enter_context(patch.object(scg, name, value))
            stack.enter_context(patch.object(scl, "_STEP_MODULES", (scg,)))
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(
                {"source": source, "session_id": "godot-proof"}))))
            try:
                scl.main()
            except SystemExit as exc:
                check("SessionStart main exits successfully", exc.code == 0, repr(exc.code))
            except AssertionError as exc:
                check("a Godot step ran where it must not", False, str(exc))
        return stdout.getvalue()

    with tempfile.TemporaryDirectory(prefix="scg_main_") as main_dir:
        main_root = Path(main_dir)
        check("godot_project is False without project.godot", scg.godot_project(main_root) is False)
        (main_root / "project.godot").write_text("; planted\n", encoding="utf-8")
        check("godot_project is True with project.godot", scg.godot_project(main_root) is True)

        out = run_main(main_root, False, "startup", scg_patches={"get_jmodot_commits": lambda *_a, **_k: ["abc jm"]})
        check("a Godot project without a binary says so", "GODOT_BIN: NOT FOUND" in out, out[-600:])
        check("a Godot project lists the Jmodot commits", "Recent Jmodot commits:\n  abc jm" in out, out[-600:])
        check("the hooks' setup statuses reach the Env: line", "import_cache OK" in out and "build OK" in out, out)
        out = run_main(main_root, False, "startup", scg_patches={"get_godot_bin": lambda: "/opt/godot/g"})
        check("a resolved binary prints the GODOT_BIN line",
              "  GODOT_BIN: /opt/godot/g" in out and "Recent Jmodot commits:\n  (none)" in out, out[-600:])
        out = run_main(main_root, False, "startup", scg_patches={"godot_docs_cache_issue": lambda _r: "DOCS-ISSUE"})
        check("a docs cache issue is reported", "\nDOCS-ISSUE" in out, out[-400:])
        check("a local session gets no cloud contract", "<cloud-session>" not in out)

        # A cloud VM has no user or local settings, so neither path-scoped rules nor the
        # autoMemoryDirectory index reach the model unless SessionStart injects them.
        (main_root / ".claude" / "rules").mkdir(parents=True)
        (main_root / ".claude" / "auto-memory").mkdir(parents=True)
        (main_root / ".claude" / "rules" / "cloud_dev.md").write_text(
            "---\npaths:\n  - \"**/cloud-setup.sh\"\n---\n\n# Cloud\nCONTRACT-MARKER\n", encoding="utf-8")
        (main_root / ".claude" / "auto-memory" / "MEMORY.md").write_text(
            "# Memory Index\n- MEMORY-MARKER\n", encoding="utf-8")
        persisted = []
        cloud_out = run_main(
            main_root, True, "compact",
            scg_patches={"run_cloud_setup": lambda _root: "OK (toolchain present)",
                         "setup_runsettings": lambda _root: "OK",
                         "persist_cloud_env": lambda godot_bin: persisted.append(godot_bin),
                         "get_godot_bin": lambda: "/opt/godot/g"})
        check("cloud session gets the cloud contract body", "<cloud-session>" in cloud_out
              and "CONTRACT-MARKER" in cloud_out, cloud_out[-800:])
        check("cloud contract drops the rule frontmatter", "paths:" not in cloud_out)
        check("cloud session still gets the memory index", "MEMORY-MARKER" in cloud_out)
        check("cloud status key names the setup script", "cloud_setup OK" in cloud_out, cloud_out[:600])
        check("cloud session persists the resolved binary", persisted == ["/opt/godot/g"], repr(persisted))
        check("no hook faulted", "Session step modules: FAILED" not in cloud_out, cloud_out[-300:])

        # An unterminated frontmatter fence must not crash SessionStart.
        (main_root / ".claude" / "rules" / "cloud_dev.md").write_text("---\npaths: x\nBODY-MARKER\n",
                                                                      encoding="utf-8")
        try:
            blocks = "\n".join(scg.cloud_lines(main_root))
            check("unterminated frontmatter keeps the body and does not raise", "BODY-MARKER" in blocks, blocks)
        except Exception as exc:
            check("unterminated frontmatter keeps the body and does not raise", False, repr(exc))
        (main_root / ".claude" / "rules" / "cloud_dev.md").unlink()
        check("a missing contract is named, not raised",
              "Cloud contract missing" in "\n".join(scg.cloud_lines(main_root)))

    # A project without project.godot gets the generic context and none of the Godot steps: the
    # Godot-only functions are planted to fail if a hook reaches them.
    with tempfile.TemporaryDirectory(prefix="scg_plain_") as plain_dir:
        plain_root = Path(plain_dir)
        planted = {name: must_not_run for name in (
            "get_godot_bin", "get_jmodot_commits", "setup_import_cache", "build_status",
            "godot_docs_cache_issue", "setup_runsettings", "run_cloud_setup", "persist_cloud_env")}
        for is_cloud_session in (False, True):
            plain_out = run_main(plain_root, is_cloud_session, "startup",
                                 scl_patches={"is_worktree": lambda: True}, scg_patches=planted)
            label = "cloud" if is_cloud_session else "local"
            check("non-Godot %s session: no Godot or Jmodot lines" % label,
                  "GODOT_BIN" not in plain_out and "Jmodot" not in plain_out, plain_out[-700:])
            check("non-Godot %s session: the git context is still injected" % label,
                  "Git: main |" in plain_out and "Recent {{PROJECT_NAME}} commits:" in plain_out, plain_out[-700:])
            check("non-Godot %s session: no hook faulted" % label,
                  "Session step modules: FAILED" not in plain_out, plain_out[-300:])

    # run_cloud_setup branches, with the toolchain probes, the snapshot marker and the script run
    # planted. `cloud_setup_current` is the seam for the marker: the tool's own compare is proven in
    # test_workstation_profile_godot.py; here it only has to reach the right branch.
    class Done:
        def __init__(self, out):
            self.stdout, self.returncode = out, 0

    with tempfile.TemporaryDirectory(prefix="scg_setup_") as tmp:
        root = Path(tmp)
        (root / ".claude").mkdir()
        env = {"CLAUDE_CODE_REMOTE": "true"}
        with patch.dict(os.environ, env), patch.object(scg, "_cmd_exists", lambda _c: False), \
                patch.object(scg, "get_godot_bin", lambda: ""), \
                patch.object(scg, "cloud_setup_current", lambda _root: True):
            check("missing cloud-setup.sh is FAILED", scg.run_cloud_setup(root).startswith("FAILED"))
            (root / ".claude" / "cloud-setup.sh").write_text("exit 0\n", encoding="utf-8")
            out = ("[cloud-setup] prefix=/opt\n[cloud-setup] FAILED: apt install xvfb\n"
                   "[cloud-setup] FAILED: .NET SDK install\n")
            with patch.object(scg.subprocess, "run", lambda *a, **k: Done(out)):
                status = scg.run_cloud_setup(root)
            check("every FAILED line reaches the status",
                  "apt install xvfb" in status and ".NET SDK install" in status, status)
        docs_dir = root / ".claude" / "scripts"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "godot_docs_cache.sh").write_text("exit 0\n", encoding="utf-8")
        ran = []

        def record(argv, *a, **k):
            ran.append(Path(argv[-1]).name)
            return Done("[cloud-setup] FAILED: .NET installer download (dot.net)\n")

        with patch.dict(os.environ, env), patch.object(scg, "_cmd_exists", lambda _c: False), \
                patch.object(scg, "get_godot_bin", lambda: ""), \
                patch.object(scg, "cloud_setup_current", lambda _root: True), \
                patch.object(scg.subprocess, "run", record):
            status = scg.run_cloud_setup(root)
        check("a failed toolchain install still builds the docs cache (it needs only GitHub)",
              status.startswith("FAILED") and "godot_docs_cache.sh" in ran, (status, ran))

        for label, current, want in (("a current marker", True, "OK"),
                                     ("a stale marker", False, "FIXED (snapshot script is stale")):
            with patch.dict(os.environ, env), patch.object(scg, "_cmd_exists", lambda _c: True), \
                    patch.object(scg, "get_godot_bin", lambda: "/opt/godot/g"), \
                    patch.object(scg, "cloud_setup_current", lambda _root, c=current: c), \
                    patch.object(scg.subprocess, "run", lambda *a, **k: Done("")):
                status = scg.run_cloud_setup(root)
            check("a present toolchain with %s reports %s" % (label, want),
                  status.startswith(want), status)
            if current:
                check("a current marker never re-runs the script", "FIXED" not in status, status)
            else:
                check("a stale snapshot script says to re-paste the repo copy",
                      "paste .claude/cloud-setup.sh" in status, status)

        def unloadable(_root):
            raise OSError("profile gone")

        with patch.object(scg, "_load_cloud_profile", unloadable):
            check("an unreadable profile keeps the toolchain probe's answer (fail open, no re-run)",
                  scg.cloud_setup_current(root) is True, scg.cloud_setup_current(root))

        marker_tool = root / ".claude" / "tools"
        marker_tool.mkdir(parents=True)
        (marker_tool / "workstation_profile_godot.py").write_text(
            "def cloud_setup_current(root):\n    return False\n", encoding="utf-8", newline="\n")
        check("the fixture checkout's own fingerprint tool answers the marker question",
              scg.cloud_setup_current(root) is False)

    # CLAUDE_ENV_FILE lines must export: a sourced plain assignment never reaches child processes.
    with tempfile.TemporaryDirectory(prefix="scg_env_") as tmp:
        env_file = os.path.join(tmp, "env")
        with patch.dict(os.environ, {"CLAUDE_ENV_FILE": env_file, "DOTNET_ROOT": "/opt/dotnet"}):
            scg.persist_cloud_env("/opt/godot/g")
        with open(env_file, encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line]
        check("every persisted env line exports",
              bool(lines) and all(line.startswith("export ") for line in lines), repr(lines))

    with tempfile.TemporaryDirectory(prefix="scg_rs_") as tmp:
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
