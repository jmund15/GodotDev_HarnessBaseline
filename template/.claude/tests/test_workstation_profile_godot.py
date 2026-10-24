#!/usr/bin/env python3
"""Re-runnable proof for tools/workstation_profile_godot.py: the toolchain checks the core's `preflight`
takes from it (the .NET SDK, `GODOT_BIN` and the local package feeds), and the `SETUP_FINGERPRINT` line of
`cloud-setup.sh`, a fixed point of its own digest whose stale or missing value is reported, with
`cloud_setup_current` true only when a marker records the repo script's fingerprint.

    python3 .claude/tests/test_workstation_profile_godot.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools")))
import workstation_profile as wp  # noqa: E402
import workstation_profile_godot as wpg  # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + str(detail)[:200] if detail else ""))

    bare_env = {k: v for k, v in os.environ.items() if k != "GODOT_BIN"}
    with tempfile.TemporaryDirectory(prefix="wpg_pre_") as tmp, patch.dict(os.environ, bare_env, clear=True), \
            patch.object(wpg, "_cmd_exists", lambda _name: False):
        check("godot_project is False without project.godot", wpg.godot_project(tmp) is False)
        check("a tree with no engine project and no feed has no failure", wpg.preflight(tmp) == [], wpg.preflight(tmp))

        with open(os.path.join(tmp, "project.godot"), "w", encoding="utf-8") as handle:
            handle.write("; planted\n")
        check("godot_project is True with project.godot", wpg.godot_project(tmp) is True)
        failures_seen = wpg.preflight(tmp)
        check("a Godot project without dotnet is refused", ".NET SDK not on PATH" in failures_seen, failures_seen)
        check("a Godot project without GODOT_BIN is refused", "GODOT_BIN is not set" in failures_seen,
              failures_seen)

        with patch.dict(os.environ, {"GODOT_BIN": os.path.join(tmp, "missing")}):
            check("a GODOT_BIN that is not a file is refused",
                  any(f.startswith("GODOT_BIN does not resolve") for f in wpg.preflight(tmp)), wpg.preflight(tmp))

        os.remove(os.path.join(tmp, "project.godot"))
        with open(os.path.join(tmp, "nuget.config"), "w", encoding="utf-8") as handle:
            handle.write('<configuration><packageSources><add key="l" value="feed_dir"/>'
                         '<add key="remote" value="https://example.invalid/v3/index.json"/>'
                         "</packageSources></configuration>")
        check("only the folder-valued NuGet source is a local feed",
              wpg.local_nuget_feeds(tmp) == [os.path.join(tmp, "feed_dir")], wpg.local_nuget_feeds(tmp))
        check("a missing local NuGet feed is refused in any project",
              any("local feed folder is missing" in f for f in wpg.preflight(tmp)), wpg.preflight(tmp))
        os.makedirs(os.path.join(tmp, "feed_dir"))
        check("a present local NuGet feed passes", wpg.preflight(tmp) == [], wpg.preflight(tmp))

        with open(os.path.join(tmp, "project.godot"), "w", encoding="utf-8") as handle:
            handle.write("; planted\n")
        check("the core's preflight carries the engine failures this module supplies",
              "GODOT_BIN is not set" in wp.preflight(tmp), wp.preflight(tmp))

    with tempfile.TemporaryDirectory(prefix="wpg_") as tmp:
        script_dir = os.path.join(tmp, ".claude")
        os.makedirs(script_dir)
        script = os.path.join(script_dir, "cloud-setup.sh")
        marker_dir = os.path.join(tmp, "prefix")
        os.makedirs(marker_dir)

        with open(script, "wb") as handle:
            handle.write(b'#!/bin/bash\necho hi\n')
        check("a script without the line has no fingerprint", wpg.fingerprint(script) is None)
        check("writing needs the line first", wpg.write_fingerprint(script)[0] is False)
        check("an unreadable path has no fingerprint", wpg.fingerprint(os.path.join(tmp, "missing")) is None)
        check("cloud_setup_current is false without a fingerprint line",
              wpg.cloud_setup_current(tmp, marker_dirs=[marker_dir]) is False)

        with open(script, "wb") as handle:
            handle.write(b'#!/bin/bash\nSETUP_FINGERPRINT=""\necho hi\n')
        check("a blank line is stale against its own digest", wpg.check_fingerprint(script)[0] is False)
        ok, message = wpg.write_fingerprint(script)
        digest = wpg.fingerprint(script)
        check("writing stores the digest the check then agrees with",
              ok and wpg.check_fingerprint(script)[0] is True and wpg.declared_fingerprint(script) == digest, message)
        check("the digest is a fixed point of a second write", wpg.write_fingerprint(script)[1].endswith(digest))

        with open(script, "ab") as handle:
            handle.write(b"echo changed\n")
        check("a changed script reads as stale", wpg.check_fingerprint(script)[0] is False)
        wpg.write_fingerprint(script)
        expected = wpg.fingerprint(script)

        check("no marker: not current", wpg.cloud_setup_current(tmp, marker_dirs=[marker_dir]) is False)
        marker = os.path.join(marker_dir, wpg.MARKER_NAME)
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("not-the-fingerprint\n")
        check("a mismatched marker: not current", wpg.cloud_setup_current(tmp, marker_dirs=[marker_dir]) is False)
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(expected + "\n")
        check("a marker recording the script's fingerprint: current",
              wpg.cloud_setup_current(tmp, marker_dirs=[os.path.join(tmp, "absent"), marker_dir]) is True)

        check("the CLI --check exits 0 on a current script", wpg.main(["fingerprint", "--check", script]) == 0)
        with open(script, "ab") as handle:
            handle.write(b"echo drift\n")
        check("the CLI --check exits 1 on a stale script", wpg.main(["fingerprint", "--check", script]) == 1)
        check("the CLI --write repairs it", wpg.main(["fingerprint", "--write", script]) == 0
              and wpg.main(["fingerprint", "--check", script]) == 0)

    # The loader reads the manifest cloud-setup.sh writes; both must name the same file.
    here = os.path.normpath(os.path.join(HERE, ".."))
    with open(os.path.join(here, "cloud-setup.sh"), encoding="utf-8") as handle:
        setup_text = handle.read()
    with open(os.path.join(here, "hooks", "session_context_loader_godot.py"), encoding="utf-8") as handle:
        loader_text = handle.read()
    check("cloud-setup.sh writes the install manifest the loader reads",
          '${PREFIX}/.cloud-install.json' in setup_text and 'CLOUD_INSTALL_NAME = ".cloud-install.json"' in loader_text)

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
