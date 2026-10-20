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
              frontmatter, plus those each `commands/reference/workstation_setup_*.md` declares the
              same way, so the list sits beside the procedure that consumes it and a change to
              either one shows up as drift at the next session start.

The checks `stamp` re-runs first (`preflight`) come from the layer modules installed beside this
file. The core has none of its own: a consumer without a layer module is refused for nothing.

Profile: `~/.claude/workstation_profile.json` — never under a redirected Documents folder, and
overridable with WORKSTATION_PROFILE_PATH so a proof can plant one. Writes are whole-file atomic.

Imported by `hooks/budget_posture.py`, whose text the UserPromptSubmit dispatcher runs in-process,
so this module keeps the prompt-dispatch contract: it never rebinds a stream, writes file
descriptor 1, changes the working directory, or registers an exit or signal handler.

CLI:
  workstation_profile.py record [--ai-worker present|absent]
  workstation_profile.py stamp [--root DIR] [--degraded NAME]...
  workstation_profile.py status [--root DIR]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

__all__ = [
    "AI_WORKER_VALUES", "DEFAULT_PROFILE", "FULL_MIN_MIB", "PROFILE_ENV", "SCHEMA", "SMALL_MIN_MIB",
    "TIERS", "ai_worker", "gpu_tier", "input_hashes", "local_tier", "main_checkout_root", "preflight",
    "profile_key", "profile_path", "read_gpu", "read_profile", "record", "setup_status", "stamp",
    "stamp_inputs", "update_profile", "write_profile",
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
REFERENCE_DIR = os.path.join(".claude", "commands", "reference")
REFERENCE_PREFIX = "workstation_setup_"

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


def _declaration(text):
    """(present, inputs) for one document's `stamp-inputs:` frontmatter key.

    `present` is whether the key is there at all; `inputs` is its list, or None when the key is
    present but empty or malformed.
    """
    block = _frontmatter(text)
    if block is None:
        return False, None
    declared, reading = [], False
    for line in block:
        stripped = line.strip()
        if not reading:
            if stripped == STAMP_INPUTS_KEY:
                reading = True
            elif stripped.startswith(STAMP_INPUTS_KEY):
                return True, None  # an inline scalar is not a list
            continue
        if not stripped or stripped.startswith("#"):
            continue  # a blank line or a comment inside the list does not end it
        if line[:1] not in (" ", "\t"):
            break  # a new top-level key ends the block
        if not stripped.startswith("- "):
            return True, None
        value = _unquote(stripped[2:].strip())
        if not value:
            return True, None
        declared.append(value)
    return reading, declared or None


def _read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def stamp_inputs(root):
    """The inputs declared by `commands/workstation_setup.md`'s `stamp-inputs:`, then by each
    `commands/reference/workstation_setup_*.md` that declares any, or None.

    None covers absent, empty and unparsable alike, in the command or in a reference that has the
    key, and a None reads as UNAUDITED at every caller: a stamp with no input list would certify
    the machine forever. A reference without the key adds nothing. A path declared twice is listed
    once, at its first place.
    """
    text = _read_text(os.path.join(str(root), SETUP_DOC))
    if text is None:
        return None
    present, declared = _declaration(text)
    if not present or not declared:
        return None
    directory = os.path.join(str(root), REFERENCE_DIR)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for name in names:
        if not (name.startswith(REFERENCE_PREFIX) and name.endswith(".md")):
            continue
        text = _read_text(os.path.join(directory, name))
        if text is None:
            continue
        present, extra = _declaration(text)
        if not present:
            continue
        if not extra:
            return None
        declared.extend(value for value in extra if value not in declared)
    return declared


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
    /workstation_setup)"` for the checkout `root` is in, or None when the checkout ships no
    `/workstation_setup` command: there is nothing to run, so the status is not applicable."""
    if not os.path.isfile(os.path.join(str(root), SETUP_DOC)):
        return None
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


def _sibling_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")


def _load_module(name, path):
    """A module loaded from `path`, so any cwd and sys.path work."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preflight(root):
    """The cheap checks `stamp` re-runs before it records anything. A non-empty list refuses.

    These are the stamp's own observations, never a count handed in by the caller: a stamp
    certifying a machine whose toolchain is absent is worse than no stamp at all. The checks belong
    to the layer module installed beside this file; with none installed there are none to run. An
    installed module that cannot load, or has no `preflight`, refuses the stamp: a broken check
    must not read as a passed one.
    """
    try:
        name = "workstation_profile_godot"
        if not os.path.isfile(_sibling_path(name)):
            return []
        return list(_load_module(name, _sibling_path(name)).preflight(root))
    except Exception as exc:
        return ["%s could not load or run: %s: %s" % (name, type(exc).__name__, exc)]


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


if __name__ == "__main__":
    sys.exit(main())
