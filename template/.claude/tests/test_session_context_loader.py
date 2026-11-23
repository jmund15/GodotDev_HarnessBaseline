#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader.py, the generic SessionStart steps: sidecar
probes only for available off-transport routes, the due line, the git and upstream-drift context, the
memory index, the setup stamp, and the step-module seam (an absent module is skipped, a present one is
called, a faulty one is reported and never blocks). The engine and language-server steps are proven in
test_session_context_loader_godot.py and test_session_context_loader_csharp.py. Pure in-process — no
git or toolchain spawn.

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

    # The SessionStart proof must exercise the registered script's main() output path, not only
    # inspect a helper string. Every expensive setup branch is planted so the assertion observes
    # the continuation guidance emitted to stdout. No step module is installed for these runs.
    payload = {"source": "startup", "session_id": "main-proof"}

    def run_main(main_root, patches, source="startup", step_modules=(), sidecar=False):
        stdout = io.StringIO()
        exit_codes = []
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
                    exit_codes.append(exc.code)
        check("SessionStart main exits successfully", exit_codes == [0], repr(exit_codes))
        return stdout.getvalue()

    with tempfile.TemporaryDirectory(prefix="scl_main_") as main_dir:
        main_root = Path(main_dir)
        patches = {
            "is_worktree": lambda: False,
            "is_cloud": lambda: False,
            "sweep_stray_search_indexes": lambda _root: None,
            "setup_submodule": lambda _root: "OK",
            "sidecar_health": lambda _root: {"seat-b": "UNAVAILABLE (adapters/b.py not present)"},
            "get_git_branch": lambda: "main",
            "get_uncommitted_count": lambda: 0,
            "get_upstream_drift": lambda: (24, 3, 30.0),
            "get_recent_commits": lambda count=3, cwd=None: [],
        }
        output = run_main(main_root, patches)
        check("a fresh startup carries no reload reminder (the session_digest listing owns pickup)",
              "<context-reload-reminder>" not in output and "session_digest.py" not in output, output[-600:])
        check("a seat that is not ready keeps its full status",
              "seat-b: UNAVAILABLE (adapters/b.py not present)" in output, output[:800])
        quiet_seats = run_main(main_root, dict(patches, sidecar_health=lambda _root: {}))
        check("no seat problem: no seat line", "seat-" not in quiet_seats, quiet_seats[:600])
        check("no pointer: the session id prints and no state file is named",
              "Session id: main-proof" in output and "State file:" not in output, output[:600])
        pointer = main_root / ".claude" / "scratch" / "active_state_main-proof"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(".claude/work/slug.md", encoding="utf-8")
        pointed = run_main(main_root, patches)
        check("a pointer prints the state file path beside the session id",
              "State file: .claude/work/slug.md" in pointed and "Session id: main-proof" in pointed, pointed[:600])
        shutil.rmtree(main_root / ".claude")
        check("a local session gets no cloud contract", "<cloud-session>" not in output)
        check("a fresh startup repeats no branch or commit block the client already shows, but keeps the upstream drift",
              "Git: main" not in output and "Recent commits:" not in output
              and "24 ahead, 3 behind" in output and "30h" in output, output[-900:])
        check("a fresh startup in the main checkout prints no Worktree line", "Worktree:" not in output, output[:300])
        check("a healthy item (an OK step, an OK roster) is silent in the Env line",
              "rosters OK" not in output and "submodule OK" not in output, output[:600])
        compact_out = run_main(main_root, patches, source="compact")
        check("compact prints the branch, drift and commit block",
              "Git: main | clean" in compact_out and "24 ahead, 3 behind" in compact_out
              and "Recent commits:" in compact_out, compact_out[-900:])
        check("compact folds the reload reminder into one [compaction] line",
              "<context-reload-reminder>" not in compact_out
              and sum(1 for ln in compact_out.splitlines() if ln.startswith("[compaction]")) == 1, compact_out[-500:])
        for quiet_source in ("clear", "resume"):
            quiet_out = run_main(main_root, patches, source=quiet_source)
            check("%s prints the branch and commit block" % quiet_source,
                  "Git: main" in quiet_out and "Recent commits:" in quiet_out, quiet_out[-600:])
        clean_drift = run_main(main_root, dict(patches, get_upstream_drift=lambda: (0, 0, 0.0)))
        check("a fresh startup with no drift prints no git line",
              "ahead" not in clean_drift and "Git:" not in clean_drift, clean_drift[:400])
        worktree_out = run_main(main_root, dict(patches, is_worktree=lambda: True))
        check("a linked worktree still reports itself", "Worktree: YES" in worktree_out, worktree_out[:300])
        check("no step module installed: no engine, Jmodot or language-server lines",
              "GODOT_BIN" not in output and "Jmodot" not in output and "lsp-early-load" not in output
              and "Session step modules" not in output, output[-700:])

        check("a project with no document roots gets no locations line", "[locations]" not in output)
        # The locations line is this project's addition to a keep-local hook; a checkout whose hook lacks it skips.
        roots_cases = (("startup", False), ("compact", False), ("startup", True)) \
            if hasattr(scl, "locations_line") else ()
        with tempfile.TemporaryDirectory(prefix="scl_roots_") as roots_dir:
            roots_root = Path(roots_dir)
            (roots_root / ".claude" / "skills" / "project_subsystems").mkdir(parents=True)
            (roots_root / ".claude" / "skills" / "project_subsystems" / "adaptation.json").write_text(
                json.dumps({"doc_roots": ["D:/Vault/DOCROOT-MARKER"]}), encoding="utf-8")
            for roots_source, roots_sidecar in roots_cases:
                roots_out = run_main(roots_root, patches, source=roots_source, sidecar=roots_sidecar)
                check("%s%s: the project's document roots print as one locations line"
                      % (roots_source, " sidecar" if roots_sidecar else ""),
                      roots_out.count("[locations]") == 1 and roots_out.count("D:/Vault/DOCROOT-MARKER") == 1,
                      roots_out[-900:])

        shared = scl.locations_line_from(["C:\\Vault\\Dev\\Alpha", "C:\\Vault\\Dev\\Beta", "C:\\Vault\\Dev\\Gamma"])
        check("document roots sharing a prefix print it once",
              shared.count("C:\\Vault\\Dev") == 1 and all(n in shared for n in ("Alpha", "Beta", "Gamma")), shared)
        apart = scl.locations_line_from(["C:/a/x", "D:/b/y"])
        check("roots with no shared directory print in full", "C:/a/x" in apart and "D:/b/y" in apart, apart)
        near = scl.locations_line_from(["C:/Vault/Dev1", "C:/Vault/Dev2"])
        check("a prefix ends at a directory boundary, never mid-name",
              "Dev1" in near and "Dev2" in near and "C:/Vault" in near and "C:/Vault/Dev:" not in near, near)

        # The client shows a 2,000-character preview of one command's output past HOOK_TEXT_CAP.
        from _hook_parts import HOOK_TEXT_CAP
        small_out = run_main(main_root, patches, source="compact")
        check("an output within the cap prints whole and writes no overflow file",
              "session_context_full" not in small_out and not (main_root / ".claude" / "scratch").exists(),
              small_out[-300:])
        planted_lines = ["  PLANTED-ENV-%03d %s" % (i, "x" * 60) for i in range(400)]
        big_step = types.SimpleNamespace(env_lines=lambda root, cloud: planted_lines,
                                         report_lines=lambda root: ["", "STEP-REPORT"])
        big_out = run_main(main_root, patches, source="compact", step_modules=(big_step,))
        big_bytes = len(big_out.encode("utf-8"))
        full_path = next((ln.split("Read ", 1)[1].split(" ", 1)[0]
                          for ln in big_out.splitlines() if "Read " in ln and "session-context" in ln), "")
        full_text = Path(full_path).read_text(encoding="utf-8") if full_path and os.path.isfile(full_path) else ""
        check("an oversize loader output stays within the client's hook-text cap", big_bytes <= HOOK_TEXT_CAP,
              "%d bytes" % big_bytes)
        check("an oversize loader output keeps its opening whole lines and its compaction line",
              big_out.startswith("<session-context>") and "PLANTED-ENV-000 " in big_out
              and "[compaction]" in big_out and all(ln.endswith("x" * 60) for ln in big_out.splitlines()
                                                    if ln.startswith("  PLANTED-ENV-")), big_out[-500:])
        check("an oversize loader output names the file that holds all of it, absolute",
              os.path.isabs(full_path) and "PLANTED-ENV-399 " in full_text and "STEP-REPORT" in full_text
              and "24 ahead" in full_text, (full_path, big_out[-400:]))

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
        (main_root / ".claude" / "auto-memory" / "MEMORY.md").write_text(
            "# Memory Index\n" + "".join("- MEMORY-ROW-%04d %s\n" % (i, "y" * 60) for i in range(300)),
            encoding="utf-8")
        report_step = types.SimpleNamespace(report_lines=lambda root: ["", "STEP-REPORT"])
        cloud_big = run_main(main_root, cloud_patches, source="compact", step_modules=(report_step,))
        check("an oversize cloud output cuts the bulk memory index before the closing lines, within the cap",
              len(cloud_big.encode("utf-8")) <= HOOK_TEXT_CAP and "[compaction]" in cloud_big
              and "STEP-REPORT" in cloud_big, "%d bytes; %s" % (len(cloud_big.encode("utf-8")), cloud_big[-400:]))
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
              "step_early" not in output and "step_setup: FIXED (x)" in output and "step_local" not in output
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
              and "24 ahead" in output and "STEP-REPORT" in output, output[-500:])

        # An installed module that cannot be imported is named, and its siblings still load.
        scl._STEP_FAULTS.clear()
        check("an import fault is recorded by the module fault hook",
              scl._step_fault("mod", ImportError("boom")) is None and "mod import ImportError: boom" in scl._STEP_FAULTS)
        scl._STEP_FAULTS.clear()

    check("the loader carries no runsettings entry point", not hasattr(scl, "runsettings_cli"))
    src = Path(scl.__file__).read_text(encoding="utf-8")
    check("the loader carries no project literal (baseline, fixed offset, inline worklog)",
          not any(x in src for x in ("237/wk", "-06:00", "WORKLOG_INLINE", "sidecar-")))
    seat_root = Path(tempfile.mkdtemp(prefix="scl_seat_"))
    (seat_root / ".claude").mkdir()
    check("a root with no seats.json reports no seat problem", scl.sidecar_health(seat_root) == {})
    shutil.rmtree(seat_root, ignore_errors=True)

    # A project gets the generic context.
    with tempfile.TemporaryDirectory(prefix="scl_plain_") as plain_dir:
        plain_root = Path(plain_dir)
        for is_cloud_session in (False, True):
            plain_out = run_main(plain_root, dict(patches, is_cloud=lambda: is_cloud_session,
                                                  is_worktree=lambda: True), source="compact")
            label = "cloud" if is_cloud_session else "local"
            check("plain %s session: no engine or language-server lines" % label,
                  "GODOT_BIN" not in plain_out and "Jmodot" not in plain_out
                  and "lsp-early-load" not in plain_out, plain_out[-700:])
            check("plain %s session: the git context is still injected" % label,
                  "Git: main |" in plain_out and "Recent commits:" in plain_out, plain_out[-700:])

    # A sidecar child executes a brief; the owner's Worktree, Env, Git and commit lines are noise to it.
    with tempfile.TemporaryDirectory(prefix="scl_sidecar_") as sidecar_dir:
        for sidecar_source in ("startup", "compact"):
            delegate_out = run_main(Path(sidecar_dir), patches, source=sidecar_source, sidecar=True)
            check("sidecar %s: no Worktree, Env, Git or commit block" % sidecar_source,
                  not any(marker in delegate_out for marker in
                          ("Worktree:", "Env: ", "Git: main |", "Recent commits:")),
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
              "workstation: UNAUDITED" in env_line(out), env_line(out))

        with patch.dict(os.environ, env_ws):
            ws_module = load_by_path(ws_root / ".claude" / "tools" / "workstation_profile.py")
            stamped = ws_module.stamp(ws_root, preflight_failures=[])
        check("the fixture checkout stamps", stamped[0], stamped)
        out = run_ws_main(env_ws)
        check("a stamped checkout: the Env: line does not mention the workstation",
              "workstation" not in env_line(out), env_line(out))

        (ws_root / ".mcp.json").write_text("drifted\n", encoding="utf-8")
        out = run_ws_main(env_ws)
        check("a changed setup input: the Env: line names it STALE",
              "workstation: STALE" in env_line(out) and ".mcp.json" in env_line(out),
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

    # A process hoarding handles pins kernel memory no RAM listing shows: the step names it with its pid.
    rows = [(20296, "SystemSettings.exe", 3_563_258), (10028, "ServiceShell.exe", 45_661), (4, "System", 9_000)]
    status = scl.handle_hoarders(rows)
    check("a hoarder above the floor is named with its pid and count; the processes below it are not",
          "SystemSettings.exe pid 20296" in status and "3,563,258" in status and "ServiceShell" not in status,
          status)
    check("no process above the floor: no status", scl.handle_hoarders(rows[1:]) == "", scl.handle_hoarders(rows[1:]))
    check("an unreadable process table reads UNKNOWN, never healthy",
          scl.handle_hoarders(None).startswith("UNKNOWN"), scl.handle_hoarders(None))
    check("no process table to read (off Windows): no status", scl.handle_hoarders([]) == "")
    if os.name == "nt":
        live = scl.process_handle_counts()
        check("the live process table lists this process with a handle count",
              any(pid == os.getpid() and count > 0 for pid, _name, count in live or ()), repr((live or [])[:3]))

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")



def test_proof():
    assert main() in (None, 0)


if __name__ == "__main__":
    main()
