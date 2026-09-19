#!/usr/bin/env python3
r"""Record how this client's Bash tool delivers backslash runs.

Issue it through the Bash tool as the WHOLE command:

    python3 .claude/tools/bash_fidelity_probe.py 'x\\y' 'x\\"y'

`hooks/bash_backslash_fidelity.py` recognizes that invocation, never doubles it, and appends
`--client-version <version>` read from the running session's transcript. The probe never runs
`claude --version`: the PATH binary can differ from the running client after an auto-update.

Each argument measures one run class:
- argument 1: `x\y` means a run not followed by `"` collapsed (`collapses: true`); `x\\y` means intact;
- argument 2: `x\"y` means a run followed by `"` collapsed (`collapses_before_quote: true`); `x\\"y`
  means intact.
Any other shape, a missing or malformed `--client-version`, or an unknown flag exits 2 and writes
nothing. The record `<version>|<sys.platform>` is merged into `.claude/cache/bash_backslash_fidelity.json`
under `_hook_state.update_json_locked` (atomic replace) and printed.

Test-only override: HARNESS_BASH_FIDELITY_RECORD (record path).
Proof: tests/test_bash_fidelity_probe.py.
"""
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE_DIR, "hooks"))

BS = "\\"
DQ = '"'
PLAIN = {"x" + BS + "y": True, "x" + BS + BS + "y": False}
BEFORE_QUOTE = {"x" + BS + DQ + "y": True, "x" + BS + BS + DQ + "y": False}
VERSION_RE = re.compile(r"[0-9A-Za-z._+-]{1,64}")
USAGE = ("usage: python3 .claude/tools/bash_fidelity_probe.py 'x" + BS + BS + "y' 'x" + BS + BS + DQ + "y'\n"
         "  Issue that exact command through the Bash tool; the bash_backslash_fidelity hook appends\n"
         "  --client-version <version>. Without it the probe exits 2.\n")


def record_path():
    return os.environ.get("HARNESS_BASH_FIDELITY_RECORD") or os.path.join(CLAUDE_DIR, "cache", "bash_backslash_fidelity.json")


def _reject(message):
    sys.stderr.write("bash_fidelity_probe: %s\n%s" % (message, USAGE))
    return 2


def parse(argv):
    """(plain, before_quote, version) or an int exit code."""
    positional, version = [], None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        if arg == "--client-version":
            if i + 1 >= len(argv):
                return _reject("--client-version needs a value")
            version = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--client-version="):
            version = arg.split("=", 1)[1]
            i += 1
            continue
        if arg.startswith("-"):
            return _reject("unknown flag %r" % arg)
        positional.append(arg)
        i += 1
    if version is None:
        return _reject("--client-version is required; issue the probe through the Bash tool so the hook supplies it")
    if not VERSION_RE.fullmatch(version):
        return _reject("malformed --client-version %r" % version)
    if len(positional) != 2:
        return _reject("expected exactly two arguments, got %d" % len(positional))
    return positional[0], positional[1], version


def main(argv=None):
    parsed = parse(sys.argv[1:] if argv is None else argv)
    if isinstance(parsed, int):
        return parsed
    plain, quoted, version = parsed
    if plain not in PLAIN:
        return _reject("argument 1 %r is neither the collapsed nor the intact form of x%s%sy" % (plain, BS, BS))
    if quoted not in BEFORE_QUOTE:
        return _reject("argument 2 %r is neither the collapsed nor the intact form of x%s%s%sy" % (quoted, BS, BS, DQ))

    import _hook_state

    key = "%s|%s" % (version, sys.platform)
    row = {"collapses": PLAIN[plain], "collapses_before_quote": BEFORE_QUOTE[quoted],
           "observed": datetime.date.today().isoformat()}
    path = record_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as exc:
        sys.stderr.write("bash_fidelity_probe: cannot create %s: %s\n" % (os.path.dirname(path), exc))
        return 1

    def update(state):
        state[key] = row
        return True

    written, _ = _hook_state.update_json_locked(path, update)
    if not written:
        sys.stderr.write("bash_fidelity_probe: could not write %s\n" % path)
        return 1
    sys.stdout.write(json.dumps({key: row}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
