#!/usr/bin/env python3
"""Discovers every re-runnable proof under `.claude/tests/`, runs it, and on an all-green
run stamps the harness tree (whole-tree hash + per-file digests) to
`.claude/logs/harness_tests_stamp.json` via `write_json_atomic`. `git_guardrails.py` imports
`tree_entries` and `STAMP_PATH` from this module so the commit guard and this runner never
compute a digest two different ways.

    python3 .claude/scripts/harness_tests.py [--repo PATH] [--all] [--hash] [--verbose]
        [--allow-cannot-run FILE]

Any `cannot-run` proof (exit 2) makes the run INCOMPLETE: no passing stamp is written and the
run exits 2. `--allow-cannot-run FILE` names a JSON list of `{"proof", "platform", "reason"}`
objects — proofs that this machine cannot bind (a runner absent from the CI image, for example).
A listed proof's `cannot-run` no longer makes the run INCOMPLETE; an unlisted `cannot-run` still
does. The flag exits 2 before running anything when the file is missing, unparseable, holds an
entry missing `proof`, `platform` or `reason`, or lists a proof `discover()` never found. Without
the flag, an unlisted (i.e. every) `cannot-run` still makes the run INCOMPLETE.

If any tested input or HEAD changes while the proofs are running, the run exits 1 and writes no
stamp — a hash over a moving target is not a fact about any single tree state.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import time

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_SCRIPTS_DIR)
REPO_ROOT = os.path.dirname(_CLAUDE_DIR)

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
from _hook_state import write_json_atomic, HARNESS_DIRS as DIRS  # noqa: E402

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "tools"))
import adaptation  # noqa: E402

STAMP_PATH = os.environ.get("HARNESS_TEST_STAMP") or os.path.join(
    _CLAUDE_DIR, "logs", "harness_tests_stamp.json"
)

# The template ships no excluded proofs and one built-in timeout, for its own slowest proof. A
# consumer adds project entries via `adaptation.json` `proof_excluded` / `proof_timeouts` (Design
# Doc §8, owner ruling R1) -- never by editing these dicts directly.
EXCLUDED = {}

_PATTERNS = ("test_*.py", "*_test.py", "*_test.js", "*.sh", "*.ps1")
_TIMEOUT_SEC = 120
# The publish suite builds 24 git fixtures: 61 s alone, past 120 s beside a second battery.
_BUILTIN_PROOF_TIMEOUTS = {"test_baseline_publish.py": 300}
_PROOF_TIMEOUTS = dict(_BUILTIN_PROOF_TIMEOUTS)


def _merge_adaptation_proof_config() -> None:
    """Merge `adaptation.json` `proof_excluded` into `EXCLUDED` and `proof_timeouts` into
    `_PROOF_TIMEOUTS`. A `proof_timeouts` value that is not a positive int is skipped with
    one stderr line; `proof_excluded` values are reasons and need no further validation."""
    EXCLUDED.update(adaptation.get(_CLAUDE_DIR, "proof_excluded"))
    for name, seconds in adaptation.get(_CLAUDE_DIR, "proof_timeouts").items():
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            print(
                f"harness_tests: proof_timeouts entry {name!r}={seconds!r} is not a "
                "positive integer -- skipped",
                file=sys.stderr,
            )
            continue
        _PROOF_TIMEOUTS[name] = seconds


_merge_adaptation_proof_config()

# A bare "bash" on Windows PATH can resolve to System32's WSL shim, a different filesystem
# namespace that cannot see a Windows-style path. Prefer Git's bash.exe when present.
_GIT_BASH_CANDIDATES = (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")


def _bash_exe():
    if os.name == "nt":
        for candidate in _GIT_BASH_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate
    return "bash"


def discover(tests_dir):
    """Top-level proof files in `tests_dir` matching `_PATTERNS`, sorted. No recursion."""
    if not os.path.isdir(tests_dir):
        return []
    matched = []
    for name in sorted(os.listdir(tests_dir)):
        full = os.path.join(tests_dir, name)
        if os.path.isfile(full) and any(fnmatch.fnmatch(name, p) for p in _PATTERNS):
            matched.append(full)
    return matched


def _runner_cmd(path):
    if path.endswith(".py"):
        return [sys.executable, path]
    if path.endswith(".js"):
        return ["node", path]
    if path.endswith(".sh"):
        return [_bash_exe(), path]
    if path.endswith(".ps1"):
        return ["pwsh", "-NoProfile", "-File", path]
    raise ValueError("no runner for %s" % path)


_DETAIL_LINES = 20


def run_proof(path, timeout=None):
    """(status, seconds, detail) — status is "pass", "cannot-run" or "fail".

    Exit 2 is unavailable coverage, not a passed or failed assertion. Its reason is
    reported, and a run with unavailable coverage does not issue a passing stamp.
    """
    if timeout is None:
        timeout = _PROOF_TIMEOUTS.get(os.path.basename(path), _TIMEOUT_SEC)
    start = time.time()
    try:
        result = subprocess.run(_runner_cmd(path), capture_output=True, text=True, timeout=timeout)
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            status, detail = "pass", ""
        elif result.returncode == 2:
            status, detail = "cannot-run", out
        else:
            status, detail = "fail", out
    except subprocess.TimeoutExpired:
        status, detail = "fail", "TIMEOUT after %ds" % timeout
    except OSError as exc:
        status, detail = "fail", "runner unavailable: %s" % exc
    return status, time.time() - start, detail


class AllowCannotRunError(ValueError):
    """Raised for any malformed --allow-cannot-run input; the caller turns this into exit 2."""


def _load_allow_cannot_run(path):
    """{proof-basename: entry} from an --allow-cannot-run FILE. Raises AllowCannotRunError on any
    malformed input; does not check the entries against discovered proofs — the caller does that
    once it knows which proofs exist."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise AllowCannotRunError("--allow-cannot-run file not found: %s (%s)" % (path, exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AllowCannotRunError("--allow-cannot-run file is not valid JSON: %s (%s)" % (path, exc))
    if not isinstance(data, list):
        raise AllowCannotRunError("--allow-cannot-run file must be a JSON list: %s" % path)
    entries = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise AllowCannotRunError("--allow-cannot-run entry is not an object: %r" % (entry,))
        missing = [key for key in ("proof", "platform", "reason") if not entry.get(key)]
        if missing:
            raise AllowCannotRunError(
                "--allow-cannot-run entry missing %s: %r" % (", ".join(missing), entry)
            )
        entries[entry["proof"]] = entry
    return entries


def _git(repo_root, args):
    """Raise on any git failure: a hash over a failed enumeration must never be stamped."""
    result = subprocess.run(["git"] + args, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args[0], repo_root, result.stderr.strip()))
    return result.stdout


_CODE_EXT = (".py", ".ps1", ".sh", ".js")


_SETTINGS_FILES = (".claude/settings.json", ".claude/settings.base.json", ".claude/settings.project.json")


def _is_code(rel):
    rel = rel.replace(os.sep, "/")
    return rel in _SETTINGS_FILES or rel.endswith(_CODE_EXT)


def tree_entries(repo_root):
    """{posix-relpath: sha256(bytes)} for every tracked/untracked-unignored code file under
    `DIRS`, enumerated by `git ls-files`, run with cwd=repo_root — plus every proof
    `discover()` finds: new files under `.claude/tests/` are gitignored (existing proofs were
    force-added), so a not-yet-added proof is invisible to both `ls-files` calls and would
    leave the set unchanged. The stamp stores this map so the commit guard can judge only the
    paths a commit touches."""
    files = set()
    for out in (
        _git(repo_root, ["ls-files", "--"] + list(DIRS)),
        _git(repo_root, ["ls-files", "-o", "--exclude-standard", "--"] + list(DIRS)),
    ):
        for line in out.splitlines():
            line = line.strip()
            if line:
                files.add(line)
    for full in discover(os.path.join(repo_root, ".claude", "tests")):
        files.add(os.path.relpath(full, repo_root).replace(os.sep, "/"))

    files = {f for f in files if _is_code(f)}

    entries = {}
    for rel in sorted(files):
        full = os.path.join(repo_root, rel)
        try:
            with open(full, "rb") as fh:
                entries[rel.replace(os.sep, "/")] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            continue
    return entries


def tree_hash(repo_root):
    """One digest over `tree_entries` — the whole-tree freshness key."""
    blob = json.dumps(sorted(tree_entries(repo_root).items())).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _write_stamp(repo_root, run, passed, excluded_list, indeterminate=(), entries=None, head=None):
    if entries is None:
        entries = tree_entries(repo_root)
    if head is None:
        head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    blob = json.dumps(sorted(entries.items())).encode("utf-8")
    stamp = {
        "ts": time.time(),
        "tree_hash": hashlib.sha256(blob).hexdigest(),
        "files": entries,
        "head": head,
        "run": run,
        "pass": passed,
        # Recorded so a green stamp cannot claim more coverage than it had: these proofs ran and
        # could not bind their target, which is not the same as passing.
        "cannotRun": [os.path.basename(p) for p in indeterminate],
        "excluded": excluded_list,
    }
    write_json_atomic(STAMP_PATH, stamp)


def run_all(repo_root, include_excluded=False, verbose=False, allow_cannot_run=None):
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir)
    present = {os.path.basename(p) for p in discovered}

    allowed_entries = None
    if allow_cannot_run is not None:
        try:
            allowed_entries = _load_allow_cannot_run(allow_cannot_run)
        except AllowCannotRunError as exc:
            print("harness_tests: %s" % exc)
            return 2
        unknown = sorted(name for name in allowed_entries if name not in present)
        if unknown:
            print(
                "harness_tests: --allow-cannot-run lists a proof discover() never found: %s"
                % ", ".join(unknown)
            )
            return 2

    stale = sorted(name for name in EXCLUDED if name not in present)
    if stale:
        for name in stale:
            print("FAIL stale EXCLUDED entry: %s (%s)" % (name, EXCLUDED[name]))
        print("harness_tests: 0 run, 0 pass, 0 fail, 0 cannot-run, %d excluded, 0.0s"
              % len(EXCLUDED))
        return 1

    if include_excluded:
        selected = discovered
        excluded_list = []
    else:
        selected = [p for p in discovered if os.path.basename(p) not in EXCLUDED]
        excluded_list = sorted(
            os.path.basename(p) for p in discovered if os.path.basename(p) in EXCLUDED
        )

    if not selected:
        print("harness_tests: 0 run, 0 pass, 0 fail, 0 cannot-run, %d excluded, 0.0s"
              % len(excluded_list))
        return 1

    tested_entries = tree_entries(repo_root)
    tested_head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    passed = 0
    failed = 0
    indeterminate = []
    elapsed_total = 0.0
    label = {"pass": "OK", "cannot-run": "CANNOT-RUN", "fail": "FAIL"}
    for path in selected:
        status, elapsed, detail = run_proof(path)
        elapsed_total += elapsed
        if verbose or status != "pass":
            print("%s %.1fs %s" % (label[status], elapsed, path))
        if detail:
            for line in detail.strip().splitlines()[-_DETAIL_LINES:]:
                print("    | " + line)
        if status == "pass":
            passed += 1
        elif status == "cannot-run":
            indeterminate.append(path)
        else:
            failed += 1

    print(
        "harness_tests: %d run, %d pass, %d fail, %d cannot-run, %d excluded, %.1fs"
        % (len(selected), passed, failed, len(indeterminate), len(excluded_list), elapsed_total)
    )
    # Named, never a footnote: a proof that stopped binding its target is how a guard quietly stops
    # guarding, and the reader has to be able to tell "a peer is refactoring" from "mine broke".
    if indeterminate:
        # ASCII only: this runner prints to a Windows console whose default codepage mangles an
        # em-dash, and a summary line nobody can read is a summary line nobody reads.
        print("cannot-run (exit 2: the proof could not bind its target; not counted as failure):")
        for p in indeterminate:
            print("    - %s" % p)

    if allowed_entries is not None:
        allowed_hits = [p for p in indeterminate if os.path.basename(p) in allowed_entries]
        unlisted_hits = [p for p in indeterminate if os.path.basename(p) not in allowed_entries]
        print("harness_tests: %d cannot-run exception(s) allowed" % len(allowed_hits))
    else:
        unlisted_hits = list(indeterminate)

    if failed:
        return 1
    if unlisted_hits:
        print("harness_tests: INCOMPLETE; no passing stamp written")
        return 2

    current_entries = tree_entries(repo_root)
    current_head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    if current_entries != tested_entries or current_head != tested_head:
        changed = sorted(path for path in tested_entries.keys() | current_entries.keys()
                         if tested_entries.get(path) != current_entries.get(path))
        print("harness_tests: inputs changed during verification; no passing stamp written")
        for path in changed[:20]:
            print("    - " + path)
        if len(changed) > 20:
            print("    ... %d more changed inputs" % (len(changed) - 20))
        if current_head != tested_head:
            print("    - HEAD changed")
        return 1

    _write_stamp(repo_root, len(selected), passed, excluded_list, indeterminate,
                 entries=tested_entries, head=tested_head)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run harness proofs and stamp unchanged passing inputs.")
    parser.add_argument("--repo", default=REPO_ROOT, help="repository containing the proofs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="include normally excluded proofs")
    mode.add_argument("--hash", action="store_true", help="print the current input hash without running proofs")
    parser.add_argument("--verbose", action="store_true", help="print every proof result, not only failures and summary")
    parser.add_argument(
        "--allow-cannot-run",
        metavar="FILE",
        default=None,
        help="JSON list of {proof, platform, reason} objects whose cannot-run does not INCOMPLETE the run",
    )
    args = parser.parse_args(argv)
    if args.hash:
        print(tree_hash(args.repo))
        return 0
    return run_all(
        args.repo,
        include_excluded=args.all,
        verbose=args.verbose,
        allow_cannot_run=args.allow_cannot_run,
    )


if __name__ == "__main__":
    sys.exit(main())
