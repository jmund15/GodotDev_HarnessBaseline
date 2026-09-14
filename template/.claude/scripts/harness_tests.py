#!/usr/bin/env python3
"""Discovers every re-runnable proof under `.claude/tests/`, runs it, and on an all-green
run stamps the harness tree (whole-tree hash + per-file digests) to
`.claude/logs/harness_tests_stamp.json` via `write_json_atomic`. `git_guardrails.py` imports
`tree_entries` and `STAMP_PATH` from this module so the commit guard and this runner never
compute a digest two different ways.

    python3 .claude/scripts/harness_tests.py [--repo PATH] [--all] [--hash]
"""

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

STAMP_PATH = os.environ.get("HARNESS_TEST_STAMP") or os.path.join(
    _CLAUDE_DIR, "logs", "harness_tests_stamp.json"
)

EXCLUDED = {}

_PATTERNS = ("test_*.py", "*_test.py", "*_test.js", "*.sh", "*.ps1")
_TIMEOUT_SEC = 120

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


def run_proof(path, timeout=_TIMEOUT_SEC):
    """(status, seconds, detail) — status is "pass", "cannot-run" or "fail".

    Exit 2 is CANNOT RUN, not a failure: the proof could not bind its target, which is what a
    peer's in-flight refactor of that target looks like from here. Counting it as red made one
    peer's half-finished rename block every harness commit on the machine, so the runner reported
    a defect nobody had introduced and the only way past was a bypass flag.

    It is NOT silent. Indeterminates are printed, counted, and carried into the summary, because a
    proof that stops binding its own target is exactly how a guard quietly stops guarding
    (`instruction_quality` §14). A green stamp with indeterminates says so.
    """
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


def _git(repo_root, args):
    """Raise on any git failure: a hash over a failed enumeration must never be stamped."""
    result = subprocess.run(["git"] + args, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args[0], repo_root, result.stderr.strip()))
    return result.stdout


_CODE_EXT = (".py", ".ps1", ".sh", ".js")


def _is_code(rel):
    rel = rel.replace(os.sep, "/")
    return rel == ".claude/settings.json" or rel.endswith(_CODE_EXT)


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


def _write_stamp(repo_root, run, passed, excluded_list, indeterminate=()):
    entries = tree_entries(repo_root)
    blob = json.dumps(sorted(entries.items())).encode("utf-8")
    stamp = {
        "ts": time.time(),
        "tree_hash": hashlib.sha256(blob).hexdigest(),
        "files": entries,
        "head": _git(repo_root, ["rev-parse", "HEAD"]).strip() or None,
        "run": run,
        "pass": passed,
        # Recorded so a green stamp cannot claim more coverage than it had: these proofs ran and
        # could not bind their target, which is not the same as passing.
        "cannotRun": [os.path.basename(p) for p in indeterminate],
        "excluded": excluded_list,
    }
    write_json_atomic(STAMP_PATH, stamp)


def run_all(repo_root, include_excluded=False):
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir)
    present = {os.path.basename(p) for p in discovered}

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

    passed = 0
    failed = 0
    indeterminate = []
    elapsed_total = 0.0
    label = {"pass": "OK", "cannot-run": "CANNOT-RUN", "fail": "FAIL"}
    for path in selected:
        status, elapsed, detail = run_proof(path)
        elapsed_total += elapsed
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

    if failed:
        return 1

    _write_stamp(repo_root, len(selected), passed, excluded_list, indeterminate)
    return 0


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    repo_root = REPO_ROOT
    if "--repo" in argv:
        idx = argv.index("--repo")
        repo_root = argv[idx + 1]
        del argv[idx : idx + 2]

    if "--hash" in argv:
        print(tree_hash(repo_root))
        return 0

    return run_all(repo_root, include_excluded="--all" in argv)


if __name__ == "__main__":
    sys.exit(main())
