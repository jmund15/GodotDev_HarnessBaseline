#!/usr/bin/env python3
"""Re-runnable proof for tools/workstation_profile_godot.py: the `SETUP_FINGERPRINT` line of
`cloud-setup.sh` is a fixed point of its own digest, a stale or missing value is reported, and
`cloud_setup_current` is true only when a marker records the repo script's fingerprint.

    python3 .claude/tests/test_workstation_profile_godot.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools")))
import workstation_profile_godot as wpg  # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + str(detail)[:200] if detail else ""))

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

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
