#!/usr/bin/env python3
"""This machine's recorded setup facts and the drift checks that read them.

Three things live here and nothing else does:

  record      probes `nvidia-smi` and records `{gpu, local_tier}`. The tier thresholds live in
              this file alone, in MiB, with their basis beside them: a percentage or a GB figure
              is not what `nvidia-smi` reports, so a conversion anywhere else is a second opinion.
  ai_worker   NEVER recorded, always read live through `tools/tool_profiles.load_registrations`
              (the one owner of "is this MCP server registered" across user, local, project and
              plugin scopes). An installed-or-not fact goes stale the moment `~/.claude.json`
              changes, and a stale "present" routes bulk I/O at a server that is not there.
  stamp       hashes the inputs `commands/workstation_setup.md` declares in its `stamp-inputs:`
              frontmatter, so the list sits beside the procedure that consumes it and a change to
              either one shows up as drift at the next session start.
  fingerprint maintains the `SETUP_FINGERPRINT="<sha>"` line of `cloud-setup.sh`. The script
              writes that value to `<prefix>/.cloud-setup.fingerprint`, so a cloud snapshot
              built from an older pasted script can tell that its copy is not the repo's.

Profile: `~/.claude/workstation_profile.json` — never under a redirected Documents folder, and
overridable with WORKSTATION_PROFILE_PATH so a proof can plant one. Writes are whole-file atomic.

Imported by `hooks/budget_posture.py`, whose text the UserPromptSubmit dispatcher runs in-process,
so this module keeps the prompt-dispatch contract: it never rebinds a stream, writes file
descriptor 1, changes the working directory, or registers an exit or signal handler.

CLI:
  workstation_profile.py record [--ai-worker present|absent]
  workstation_profile.py stamp [--root DIR] [--degraded NAME]...
  workstation_profile.py status [--root DIR]
  workstation_profile.py fingerprint --check|--write PATH
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree

__all__ = [
    "AI_WORKER_VALUES", "DEFAULT_PROFILE", "FULL_MIN_MIB", "MARKER_DIRS", "MARKER_NAME",
    "PROFILE_ENV", "SCHEMA", "SMALL_MIN_MIB", "TIERS", "ai_worker", "check_fingerprint",
    "cloud_setup_current", "declared_fingerprint", "fingerprint", "gpu_tier", "input_hashes",
    "local_nuget_feeds", "local_tier", "main_checkout_root", "preflight", "profile_key",
    "profile_path", "read_gpu", "read_profile", "record", "setup_status", "stamp", "stamp_inputs",
    "update_profile", "write_fingerprint", "write_profile",
]

SCHEMA = 1
TIERS = ("full", "small", "none")
AI_WORKER_VALUES = ("present", "absent")

# The one copy of the tier thresholds, in MiB — the unit `nvidia-smi` reports, never in GB. Full is
# 15360 MiB, not 16 GiB: a 16 GB card reports 16376 MiB (RTX 4070 Ti SUPER, 2026-09-25), so a
# `>= 16384` test files this desktop under `small`. Small is 7680 MiB for the same reason on an
# 8 GB card (8188 MiB). The small arms are `local-granite` and `local-lfm` in
# `~/.config/ai-worker/models.yaml`, where the 14B and 30B rows are excluded. Anything below, and a
# machine with no GPU, runs no local arms.
FULL_MIN_MIB = 15360
SMALL_MIN_MIB = 7680

PROFILE_ENV = "WORKSTATION_PROFILE_PATH"
DEFAULT_PROFILE = "~/.claude/workstation_profile.json"
STAMP_INPUTS_KEY = "stamp-inputs:"
SETUP_DOC = os.path.join(".claude", "commands", "workstation_setup.md")

MARKER_NAME = ".cloud-setup.fingerprint"
# The prefixes `cloud-setup.sh` installs into: /opt as root (or with passwordless sudo), else
# $HOME/.local. A snapshot normally has one of the two.
MARKER_DIRS = ("/opt", "~/.local")

GPU_QUERY = ("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader")

_TOOL_PROFILES = None


# ------------------------------------------------------------------ the GPU tier

def gpu_tier(vram_mib):
    """`"full" | "small" | "none"` for a total VRAM in MiB. No GPU (None, 0, junk) is `"none"`."""
    if isinstance(vram_mib, bool) or not isinstance(vram_mib, int) or vram_mib <= 0:
        return "none"
    if vram_mib >= FULL_MIN_MIB:
        return "full"
    if vram_mib >= SMALL_MIN_MIB:
        return "small"
    return "none"


def read_gpu(timeout=10):
    """(name, total MiB) from a live `nvidia-smi`, or (None, None) when there is none to ask.

    The tool runs the probe itself; a caller never supplies a count it read somewhere else.
    """
    try:
        result = subprocess.run(list(GPU_QUERY), capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if result.returncode != 0:
        return None, None
    row = next((line for line in (result.stdout or "").splitlines() if line.strip()), "")
    name, _sep, total = row.rpartition(",")
    digits = re.findall(r"\d+", total)
    if not digits:
        return None, None
    return (name.strip() or None), int(digits[0])


# ------------------------------------------------------------------ the profile

def profile_path():
    return os.path.expanduser(os.environ.get(PROFILE_ENV) or DEFAULT_PROFILE)


def read_profile():
    """The profile dict, or {} when it is absent, unreadable or not a JSON object. Never raises."""
    try:
        with open(profile_path(), encoding="utf-8") as handle:
            raw = handle.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_profile(profile):
    """Whole-file atomic replace (same-directory tempfile + rename). True on success."""
    path = profile_path()
    directory = os.path.dirname(path) or "."
    temp = None
    try:
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix=".workstation_profile_", suffix=".json", dir=directory)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, ensure_ascii=True, indent=2, sort_keys=True)
        os.replace(temp, path)
        return True
    except (OSError, ValueError, TypeError):
        if temp:
            try:
                os.unlink(temp)
            except OSError:
                pass
        return False


def update_profile(updater):
    """Read, change, write. True on success. The writers are CLI runs, so no lock is taken."""
    profile = read_profile()
    profile.setdefault("schema", SCHEMA)
    try:
        if updater(profile) is False:
            return False
    except Exception:
        return False
    return write_profile(profile)


def local_tier():
    """`"full" | "small" | "none"` as recorded, or None when this machine has no profile.

    None means unknown, and a caller keeps its unmodified text for it: only a recorded hardware
    fact narrows the routing text, and an unreadable one must not.
    """
    tier = read_profile().get("local_tier")
    return tier if tier in TIERS else None


def _tool_profiles():
    """`tools/tool_profiles.py`, loaded by sibling path so any cwd works. Cached per process."""
    global _TOOL_PROFILES
    if _TOOL_PROFILES is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_profiles.py")
        spec = importlib.util.spec_from_file_location("tool_profiles", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _TOOL_PROFILES = module
    return _TOOL_PROFILES


def ai_worker():
    """`"present" | "absent"`, read live, or None when the registration walk cannot run.

    `tool_profiles.load_registrations` owns the walk; this only names the logical server. A
    recorded install fact is deliberately not consulted — `~/.claude.json` is the truth and it
    changes without this file hearing about it.
    """
    try:
        tools = _tool_profiles()
        return "present" if tools.registered_key("ai-worker", tools.load_registrations()) else "absent"
    except Exception:
        return None


# ------------------------------------------------------------------ the setup stamp

def _frontmatter(text):
    """The text between the leading `---` fences, or None when there is no closed block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index]
    return None


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def stamp_inputs(root):
    """The inputs declared by `commands/workstation_setup.md`'s `stamp-inputs:`, or None.

    None covers absent, empty and unparsable alike, and a None reads as UNAUDITED at every caller:
    a stamp with no input list would certify the machine forever.
    """
    path = os.path.join(str(root), SETUP_DOC)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    block = _frontmatter(text)
    if block is None:
        return None
    declared, reading = [], False
    for line in block:
        stripped = line.strip()
        if not reading:
            reading = stripped == STAMP_INPUTS_KEY
            continue
        if not stripped or stripped.startswith("#"):
            continue  # a blank line or a comment inside the list does not end it
        if line[:1] not in (" ", "\t"):
            break  # a new top-level key ends the block
        if not stripped.startswith("- "):
            return None
        value = _unquote(stripped[2:].strip())
        if not value:
            return None
        declared.append(value)
    return declared or None


def input_hashes(root, paths):
    """{declared path: sha256} for `paths` under `root`; a missing file hashes None, so it reads
    as drift rather than as an input that silently left the set."""
    hashes = {}
    for rel in paths:
        try:
            with open(os.path.join(str(root), rel.replace("/", os.sep)), "rb") as handle:
                hashes[rel] = hashlib.sha256(handle.read()).hexdigest()
        except OSError:
            hashes[rel] = None
    return hashes


def main_checkout_root(root):
    """The main checkout a worktree belongs to, via `git rev-parse --git-common-dir`.

    A linked worktree's answer is `<main>/.git` (absolute) and the main checkout's is `.git`
    (relative to itself), so the parent directory is the main root either way — one stamp entry per
    repository, whichever worktree runs the command. Falls back to `root` when git cannot answer.
    """
    root = os.path.abspath(str(root))
    try:
        result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15)
        common = (result.stdout or "").strip()
        if result.returncode == 0 and common:
            resolved = common if os.path.isabs(common) else os.path.join(root, common)
            parent = os.path.dirname(os.path.abspath(resolved))
            if parent:
                return parent
    except (OSError, subprocess.SubprocessError):
        pass
    return root


def profile_key(root):
    """The profile's `setup` entry key for a checkout: its main root, normcased."""
    return os.path.normcase(os.path.abspath(main_checkout_root(root)))


def setup_status(root):
    """`"OK"`, `"STALE (changed: <paths>; run /workstation_setup)"` or `"UNAUDITED (run
    /workstation_setup)"` for the checkout `root` is in."""
    declared = stamp_inputs(root)
    if not declared:
        return "UNAUDITED (run /workstation_setup)"
    entry = (read_profile().get("setup") or {}).get(profile_key(root)) or {}
    recorded = entry.get("inputs")
    if not isinstance(recorded, dict) or not recorded:
        return "UNAUDITED (run /workstation_setup)"
    current = input_hashes(root, declared)
    changed = [rel for rel in declared if recorded.get(rel) != current.get(rel)]
    if changed:
        return "STALE (changed: %s; run /workstation_setup)" % ", ".join(changed)
    return "OK"


def _cmd_exists(name):
    return shutil.which(name) is not None


def local_nuget_feeds(root):
    """The `nuget.config` package sources that name a local folder (not a URL)."""
    path = os.path.join(str(root), "nuget.config")
    try:
        tree = xml.etree.ElementTree.parse(path)
    except (OSError, xml.etree.ElementTree.ParseError):
        return []
    feeds = []
    for node in tree.iter("add"):
        value = (node.get("value") or "").strip()
        if value and "://" not in value:
            feeds.append(os.path.join(str(root), value))
    return feeds


def preflight(root):
    """The cheap checks `stamp` re-runs before it records anything. A non-empty list refuses.

    These are the stamp's own observations, never a count handed in by the caller: a stamp
    certifying a machine whose toolchain is absent is worse than no stamp at all.
    """
    failures = []
    if not _cmd_exists("dotnet"):
        failures.append(".NET SDK not on PATH")
    godot = os.environ.get("GODOT_BIN")
    if not godot:
        failures.append("GODOT_BIN is not set")
    elif not os.path.isfile(godot):
        failures.append("GODOT_BIN does not resolve to a file: %s" % godot)
    for feed in local_nuget_feeds(root):
        if not os.path.isdir(feed):
            failures.append("nuget.config local feed folder is missing: %s" % feed)
    return failures


def record(ai_worker_value=None):
    """Probe this machine and record the GPU tier. (ok, message).

    The probe and the thresholds are this file's, so a caller passes no count. `ai_worker_value`
    is the last observation `/workstation_setup` made, kept for the report; nothing routes on it —
    `ai_worker()` reads live.
    """
    name, vram_mib = read_gpu()
    tier = gpu_tier(vram_mib)
    observed = ai_worker_value if ai_worker_value in AI_WORKER_VALUES else None

    def put(profile):
        profile["gpu"] = {"name": name, "vram_mib": vram_mib}
        profile["local_tier"] = tier
        if observed:
            profile["ai_worker"] = observed
        else:
            profile.setdefault("ai_worker", None)
        profile["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        profile.setdefault("setup", {})
        return True

    if not update_profile(put):
        return False, "could not write %s" % profile_path()
    return True, "local_tier=%s (gpu=%s, %s MiB)" % (tier, name or "none", vram_mib)


def stamp(root, preflight_failures=None, degraded=()):
    """Record the declared inputs' hashes for `root`'s main checkout. (ok, message).

    Refuses on any preflight failure. `degraded` names the DEGRADED rows the `/workstation_setup`
    report carried, so the stamp keeps what the machine is weak at beside what it has.
    """
    root = os.path.abspath(str(root))
    declared = stamp_inputs(root)
    if not declared:
        return False, "no readable `stamp-inputs:` in %s; nothing stamped" % SETUP_DOC
    failures = preflight(root) if preflight_failures is None else list(preflight_failures)
    if failures:
        return False, "refusing to stamp: " + "; ".join(failures)
    entry = {"inputs": input_hashes(root, declared), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "degraded": [name for name in degraded if name]}
    key = profile_key(root)

    def put(profile):
        setup = profile.setdefault("setup", {})
        if not isinstance(setup, dict):
            setup = {}
            profile["setup"] = setup
        setup[key] = entry
        return True

    if not update_profile(put):
        return False, "could not write %s" % profile_path()
    return True, "stamped %d input(s) for %s" % (len(declared), key)


# ------------------------------------------------------------------ the cloud fingerprint

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
    return False, "stale: declared %s, computed %s (run: workstation_profile.py fingerprint --write %s)" % (
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


# ------------------------------------------------------------------ CLI

def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="workstation_profile.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    rec = commands.add_parser("record", help="probe this machine and record the GPU tier")
    rec.add_argument("--ai-worker", choices=AI_WORKER_VALUES, default=None,
                     help="the registration /workstation_setup observed (never routed on)")

    stp = commands.add_parser("stamp", help="record the declared setup inputs' hashes")
    stp.add_argument("--root", default=None, help="the checkout to stamp (default: this repo)")
    stp.add_argument("--degraded", action="append", default=[],
                     help="a DEGRADED row from the /workstation_setup report (repeatable)")

    sta = commands.add_parser("status", help="print this checkout's setup status")
    sta.add_argument("--root", default=None, help="the checkout to check (default: this repo)")

    fin = commands.add_parser("fingerprint", help="the cloud-setup.sh setup fingerprint")
    fin.add_argument("--check", action="store_true", help="exit 1 when the stored value is stale")
    fin.add_argument("--write", action="store_true", help="store the current digest")
    fin.add_argument("path")

    args = parser.parse_args(argv)

    if args.command == "record":
        ok, message = record(ai_worker_value=args.ai_worker)
        print(message)
        return 0 if ok else 1
    if args.command == "stamp":
        ok, message = stamp(args.root or _repo_root(), degraded=args.degraded)
        print(message)
        return 0 if ok else 1
    if args.command == "status":
        print(setup_status(args.root or _repo_root()))
        return 0
    if args.write:
        ok, message = write_fingerprint(args.path)
    else:
        ok, message = check_fingerprint(args.path)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
