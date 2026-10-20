#!/usr/bin/env python3
"""The cloud setup fingerprint: `cloud-setup.sh` carries a `SETUP_FINGERPRINT="<sha>"` line.

The script writes that value to `<prefix>/.cloud-setup.fingerprint`, so a cloud snapshot built from an
older pasted script can tell that its copy is not the repo's. `hooks/session_context_loader_godot.py`
loads this module by path and asks `cloud_setup_current`.

CLI:
  workstation_profile_godot.py fingerprint --check|--write PATH
"""

import argparse
import hashlib
import os
import re
import sys
import tempfile

__all__ = [
    "MARKER_DIRS", "MARKER_NAME", "check_fingerprint", "cloud_setup_current", "declared_fingerprint",
    "fingerprint", "write_fingerprint",
]

MARKER_NAME = ".cloud-setup.fingerprint"
# The prefixes `cloud-setup.sh` installs into: /opt as root (or with passwordless sudo), else
# $HOME/.local. A snapshot normally has one of the two.
MARKER_DIRS = ("/opt", "~/.local")

_FINGERPRINT_LINE = re.compile(rb'(?m)^SETUP_FINGERPRINT="[^"]*"')
_FINGERPRINT_VALUE = re.compile(rb'(?m)^SETUP_FINGERPRINT="([^"]*)"')


def fingerprint(path):
    """sha256 of `path` with the `SETUP_FINGERPRINT="..."` value blanked, or None.

    Blanking the value makes the digest a fixed point: `--write` can store what `--check` will
    then agree with, and the value itself is not part of what it certifies. A file without the
    line has no fingerprint, which reads as "not this script" everywhere below.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    blanked, count = _FINGERPRINT_LINE.subn(b'SETUP_FINGERPRINT=""', raw)
    if count != 1:
        return None
    return hashlib.sha256(blanked).hexdigest()


def declared_fingerprint(path):
    """The value written in the file's own `SETUP_FINGERPRINT` line, or None."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    match = _FINGERPRINT_VALUE.search(raw)
    return match.group(1).decode("ascii", "replace") if match else None


def write_fingerprint(path):
    """Store the file's own digest in its `SETUP_FINGERPRINT` line. (ok, message)."""
    digest = fingerprint(path)
    if digest is None:
        return False, "no single SETUP_FINGERPRINT=\"\" line in %s; add one first" % path
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return False, "cannot read %s (%s)" % (path, exc)
    updated, count = _FINGERPRINT_LINE.subn(b'SETUP_FINGERPRINT="%s"' % digest.encode("ascii"), raw)
    if count != 1:
        return False, "no single SETUP_FINGERPRINT line in %s" % path
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp = None
    try:
        handle, temp = tempfile.mkstemp(prefix=".fingerprint_", suffix=".sh", dir=directory)
        with os.fdopen(handle, "wb") as fh:
            fh.write(updated)
        os.replace(temp, path)
    except OSError as exc:
        if temp:
            try:
                os.unlink(temp)
            except OSError:
                pass
        return False, "cannot write %s (%s)" % (path, exc)
    return True, "SETUP_FINGERPRINT=%s" % digest


def check_fingerprint(path):
    """(ok, message): is the file's declared value its own digest?"""
    computed = fingerprint(path)
    if computed is None:
        return False, "%s has no SETUP_FINGERPRINT=\"\" line" % path
    declared = declared_fingerprint(path)
    if declared == computed:
        return True, "current (%s)" % computed
    return False, "stale: declared %s, computed %s (run: workstation_profile_godot.py fingerprint --write %s)" % (
        declared, computed, path)


def cloud_setup_current(root, marker_dirs=None):
    """True when a marker records the repo `cloud-setup.sh`'s fingerprint.

    `cloud-setup.sh` writes its own `SETUP_FINGERPRINT` value to `<prefix>/<MARKER_NAME>`. No
    marker (an older pasted script) and a mismatched one both mean the toolchain came from a
    different script than the repo's, so SessionStart re-runs the repo copy.
    """
    script = os.path.join(str(root), ".claude", "cloud-setup.sh")
    expected = fingerprint(script)
    if not expected:
        return False
    for directory in (MARKER_DIRS if marker_dirs is None else marker_dirs):
        try:
            with open(os.path.join(os.path.expanduser(directory), MARKER_NAME), encoding="utf-8",
                      errors="replace") as handle:
                recorded = handle.read().strip()
        except OSError:
            continue
        if recorded and recorded == expected:
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="workstation_profile_godot.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fin = commands.add_parser("fingerprint", help="the cloud-setup.sh setup fingerprint")
    fin.add_argument("--check", action="store_true", help="exit 1 when the stored value is stale")
    fin.add_argument("--write", action="store_true", help="store the current digest")
    fin.add_argument("path")
    args = parser.parse_args(argv)
    if args.write:
        ok, message = write_fingerprint(args.path)
    else:
        ok, message = check_fingerprint(args.path)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
