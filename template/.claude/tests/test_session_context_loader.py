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
import sys
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
                {"transport": "codex"},
                {"transport": "opencode"},
                {"transport": "anthropic"},
            ]

    launchers = scl.sidecar_launchers(FakeRegistry, "codex")
    check("sidecar checks skip the current and unavailable transports",
          launchers == [("opencode", "opencode_sidecar.sh"),
                        ("anthropic", "anthropic_sidecar.sh")], str(launchers))

    # --- self_improvement_advisory: startup-only, silent when nothing is due -----------
    # self_improvement_due.py's own dependency chain (self_eval_archive_store, _file_lock,
    # self_eval_archive_guard) is stdlib-only, so it is copyable into an isolated fixture root
    # without dragging in the rest of .claude/tools or .claude/hooks.
    due_root = Path(tempfile.mkdtemp(prefix="scl_due_"))
    claude_dir = due_root / ".claude"
    (claude_dir / "tools").mkdir(parents=True)
    (claude_dir / "hooks").mkdir(parents=True)
    (claude_dir / "logs").mkdir(parents=True)
    real_claude = Path(__file__).resolve().parents[1]
    for name in ("self_improvement_due.py", "self_eval_archive_store.py", "rule_retirement.py"):
        (claude_dir / "tools" / name).write_text(
            (real_claude / "tools" / name).read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("_file_lock.py", "self_eval_archive_guard.py"):
        (claude_dir / "hooks" / name).write_text(
            (real_claude / "hooks" / name).read_text(encoding="utf-8"), encoding="utf-8")
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
            "sidecar_health": lambda _root: {},
            "roster_health": lambda _root: "OK",
            "get_git_branch": lambda: "main",
            "get_uncommitted_count": lambda: 0,
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
        check("live SessionStart output points pickup to handoff", "--handoff" in output)
        check("live SessionStart output has no stale brief pickup flag",
              "session_digest.py --session <id-prefix> --brief" not in output)
        check("live SessionStart output has no full pickup flag",
              "session_digest.py --session <id-prefix> --full" not in output)

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
