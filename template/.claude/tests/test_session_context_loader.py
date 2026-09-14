#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader.py: resume/compact never spawns
dotnet and reports the stored verify; stale startup state builds; a not-ready submodule skips;
SessionStart probes only available off-transport sidecars. Pure in-process — no git or dotnet.

    python3 .claude/tests/test_session_context_loader.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

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

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
