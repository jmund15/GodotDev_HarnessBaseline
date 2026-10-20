#!/usr/bin/env python3
"""Re-runnable proof for tools/workstation_profile.py: the Godot toolchain checks `preflight` runs
before a stamp apply only to a Godot project, and a project without one is not refused for them.

    python3 .claude/tests/test_workstation_profile.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools")))
import workstation_profile as wp  # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + str(detail)[:200] if detail else ""))

    bare_env = {k: v for k, v in os.environ.items() if k != "GODOT_BIN"}
    with tempfile.TemporaryDirectory(prefix="wp_") as tmp, patch.dict(os.environ, bare_env, clear=True), \
            patch.object(wp, "_cmd_exists", lambda _name: False):
        check("godot_project is False without project.godot", wp.godot_project(tmp) is False)
        check("a non-Godot project has no toolchain preflight failure", wp.preflight(tmp) == [], wp.preflight(tmp))

        with open(os.path.join(tmp, "project.godot"), "w", encoding="utf-8") as handle:
            handle.write("; planted\n")
        check("godot_project is True with project.godot", wp.godot_project(tmp) is True)
        failures_seen = wp.preflight(tmp)
        check("a Godot project without dotnet is refused", ".NET SDK not on PATH" in failures_seen, failures_seen)
        check("a Godot project without GODOT_BIN is refused", "GODOT_BIN is not set" in failures_seen,
              failures_seen)

        with patch.dict(os.environ, {"GODOT_BIN": os.path.join(tmp, "missing")}):
            check("a GODOT_BIN that is not a file is refused",
                  any(f.startswith("GODOT_BIN does not resolve") for f in wp.preflight(tmp)), wp.preflight(tmp))

        os.remove(os.path.join(tmp, "project.godot"))
        with open(os.path.join(tmp, "nuget.config"), "w", encoding="utf-8") as handle:
            handle.write('<configuration><packageSources><add key="l" value="feed_dir"/></packageSources>'
                         "</configuration>")
        check("a missing local NuGet feed is refused in any project",
              any("local feed folder is missing" in f for f in wp.preflight(tmp)), wp.preflight(tmp))

    with tempfile.TemporaryDirectory(prefix="wp_status_") as tmp:
        os.environ["WORKSTATION_PROFILE_PATH"] = os.path.join(tmp, "profile.json")
        check("a checkout without /workstation_setup has no setup status", wp.setup_status(tmp) is None,
              wp.setup_status(tmp))
        os.makedirs(os.path.join(tmp, ".claude", "commands"))
        with open(os.path.join(tmp, wp.SETUP_DOC), "w", encoding="utf-8") as handle:
            handle.write("no frontmatter")
        check("a checkout with the command but no declared inputs is UNAUDITED",
              str(wp.setup_status(tmp)).startswith("UNAUDITED"), wp.setup_status(tmp))

    for name, cond in (("gpu_tier 16376 MiB is full", wp.gpu_tier(16376) == "full"),
                       ("gpu_tier 8188 MiB is small", wp.gpu_tier(8188) == "small"),
                       ("gpu_tier None is none", wp.gpu_tier(None) == "none")):
        check(name, cond)

    print("%d failure(s)" % len(failures) if failures else "all ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
