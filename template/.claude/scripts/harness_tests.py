#!/usr/bin/env python3
"""Run harness proofs; --close proves committed HEAD before push; commits only receive budgeted advice.

    python3 .claude/scripts/harness_tests.py --close [--now] [--base REV] [--repo PATH]
    python3 .claude/scripts/harness_tests.py --for PATH [PATH ...]
    python3 .claude/scripts/harness_tests.py --proofs-for PATH [PATH ...]
    python3 .claude/scripts/harness_tests.py --all
    python3 .claude/scripts/harness_tests.py --tiers [--json]

Close selects proofs bound to every harness path the outgoing commits change that the push remote's
default branch does not already carry (`push_destination`, `close_paths`). It runs on a
private committed-HEAD checkout, compares reds at the base, and bisects each new red through that
proof's input-changing commits. Its close {head, base, verdict, red} record gates the push of that
head. Close waits for this session's author/fold dispatches writing in its checkout unless --now, then proves the HEAD of that
moment once; a later commit never restarts it, and a newer close supersedes it.
Never-committed proofs absent at the compared revision run their
working copy against that revision's inputs; tracked proofs absent there are not excused.

Scoped modes run bound proofs on demand and retain their legacy per-file result entries; these
entries satisfy no commit or push gate. --proofs-for is the no-stamp variant. --all includes excluded
and heavy proofs; the default battery skips heavy proofs. A cannot-run result (exit 2) is missing
coverage, never green. --tiers is read-only: one row per proof with its recorded seconds, budget tier, how it runs and its trigger. Every scoped or full run appends a row to
harness_runs.json beside the stamp (mode, wall and queue seconds, each proof's run and wait seconds).

Scoped and close runs reuse passes while every reached input remains unchanged, from any checkout of
the repository (`passes_path`). Runners share
HARNESS_MACHINE_SLOTS (unset: the recorded width, else 4) under ~/.claude/.harness_slots. HARNESS_DEADLINE stops new proofs
and leaves the run incomplete (exit 3). rules/harness_tooling.md owns cadence and RED-first work.
"""

import argparse
from contextlib import contextmanager, redirect_stdout
import io
import math
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tokenize
from threading import Event
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_SCRIPTS_DIR)
REPO_ROOT = os.path.dirname(_CLAUDE_DIR)
_RECORDER_DIR = os.path.join(_SCRIPTS_DIR, "_read_recorder")
_PROGRESS_MIN, _PROGRESS_EVERY = 25, 25  # a scoped commit run stays quiet; a close reports about 20 lines

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
from _hook_state import update_json_locked, read_json_salvage, HARNESS_DIRS as DIRS, \
    SETUP_SURFACE_FILES as SURFACES, CODE_EXT as _CODE_EXT, SETTINGS_FILES as _SETTINGS_FILES, \
    is_harness_code as _is_code  # noqa: E402
import _file_lock  # noqa: E402
from _bounded_run import ProcessTree  # noqa: E402

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "tools"))
import adaptation  # noqa: E402

_DEFAULT_STAMP = os.path.join(_CLAUDE_DIR, "logs", "harness_tests_stamp.json")
STAMP_PATH = os.environ.get("HARNESS_TEST_STAMP") or _DEFAULT_STAMP

# The template ships no excluded proofs and one built-in timeout, for its own slowest proof. A
# consumer adds project entries via `adaptation.json` `proof_excluded` / `proof_timeouts` (Design
# Doc §8, owner ruling R1) -- never by editing these dicts directly.
EXCLUDED = {}
HEAVY = {}
SERIAL = {}
# None: beside STAMP_PATH, so a caller that redirects the stamp redirects these too.
FLAKY_LOG = None
FAILURE_LINE = re.compile(r"^(FAIL|not ok)\b|^\w*(Error|Exception)\b:")
KNOWN_RED_LOG = None
DURATIONS_PATH = None
PASSES_PATH = None
RUNS_PATH = None
_RUNS_KEPT = 200
_CLOSE_BUDGET_S, COMMIT_BUDGET_S, _PROOF_BUDGET_S = 300, 30, 10


def _beside_stamp(override, name):
    return override or os.path.join(os.path.dirname(os.path.abspath(STAMP_PATH)), name)


def _shared_passes_path(claude_dir):
    """The pass cache every checkout of `claude_dir`'s repository shares, under its common git dir;
    beside the stamp when git cannot name that dir. Rows key on input bytes, so a pass recorded in
    one worktree holds in any other whose reached inputs match."""
    try:
        result = subprocess.run(["git", "-C", claude_dir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True)
        common = result.stdout.strip() if result.returncode == 0 else ""
    except OSError:
        common = ""
    return (os.path.join(common, "harness", "proof_passes.json") if common
            else os.path.join(os.path.dirname(os.path.abspath(_DEFAULT_STAMP)), "harness_proof_passes.json"))


def passes_path():
    """`PASSES_PATH`, else the shared cache while the stamp is at its default home, else beside an
    overridden stamp (a proof's private run)."""
    if PASSES_PATH:
        return PASSES_PATH
    if os.path.normcase(os.path.abspath(STAMP_PATH)) == os.path.normcase(os.path.abspath(_DEFAULT_STAMP)):
        return _shared_passes_path(_CLAUDE_DIR)
    return _beside_stamp(None, "harness_proof_passes.json")


def runs_path():
    return _beside_stamp(RUNS_PATH, "harness_runs.json")


def durations_path():
    return _beside_stamp(DURATIONS_PATH, "harness_proof_durations.json")


_PATTERNS = ("test_*.py", "*_test.py", "*_test.js", "test_*.mjs", "test_*.sh", "*_test.sh", "test_*.ps1")
_TIMEOUT_SEC = 120
# Git-fixture suites. Alone: publish 191-248 s, sync 132 s. With 2-3 peer suites running the
# publish proof passed 480 s three times on 2026-09-24, so the caps hold 3x headroom.
_BUILTIN_PROOF_TIMEOUTS = {"test_baseline_publish.py": 720, "test_baseline_sync.py": 450}
_PROOF_TIMEOUTS = dict(_BUILTIN_PROOF_TIMEOUTS)
# The same 3x headroom for a proof with no cap: its last recorded pass times 3, never under the default.
_LOAD_FACTOR = 3


def _timeout_for(path, known):
    """A proof's timeout: its `proof_timeouts` cap, else `_LOAD_FACTOR` times its last recorded pass
    (`known`, the durations file) when that beats `_TIMEOUT_SEC`, else `_TIMEOUT_SEC`."""
    name = os.path.basename(path)
    if name in _PROOF_TIMEOUTS:
        return _PROOF_TIMEOUTS[name]
    recorded = known.get(name)
    if isinstance(recorded, (int, float)) and recorded > 0:
        return max(_TIMEOUT_SEC, _LOAD_FACTOR * recorded)
    return _TIMEOUT_SEC


def _merge_adaptation_proof_config(claude_dir=_CLAUDE_DIR) -> None:
    """Merge `claude_dir`'s `adaptation.json` `proof_excluded` into `EXCLUDED`, `proof_heavy` into `HEAVY` and
    `proof_serial` into `SERIAL` and `proof_timeouts` into `_PROOF_TIMEOUTS`. A `proof_timeouts` value that is not a positive int is skipped with
    one stderr line, as is a `proof_corpus` value that is not a list of directories; `proof_excluded` values are reasons and need no
    further validation. `proof_bindings` fills `PROOF_BINDINGS`; `proof_corpus` fills `PROOF_CORPUS`."""
    EXCLUDED.update(adaptation.get(claude_dir, "proof_excluded"))
    HEAVY.update(adaptation.get(claude_dir, "proof_heavy"))
    SERIAL.update(adaptation.get(claude_dir, "proof_serial"))
    for name, seconds in adaptation.get(claude_dir, "proof_timeouts").items():
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            print(
                f"harness_tests: proof_timeouts entry {name!r}={seconds!r} is not a "
                "positive integer -- skipped",
                file=sys.stderr,
            )
            continue
        _PROOF_TIMEOUTS[name] = seconds
    for name, folders in adaptation.get(claude_dir, "proof_corpus").items():
        if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
            print(f"harness_tests: proof_corpus entry {name!r} is not a list of directories -- skipped", file=sys.stderr)
            continue
        PROOF_CORPUS[name] = tuple(folders)
    PROOF_BINDINGS.update(_load_proof_bindings(claude_dir))


@contextmanager
def _policy_from(claude_dir):
    """Proof policy read from `claude_dir` alone for the block (close reads the HEAD snapshot's, never
    the working tree's), then the policy loaded before it."""
    tables = (EXCLUDED, HEAVY, SERIAL, _PROOF_TIMEOUTS, PROOF_BINDINGS, PROOF_CORPUS)
    saved = [dict(t) for t in tables]
    for table in tables:
        table.clear()
    _PROOF_TIMEOUTS.update(_BUILTIN_PROOF_TIMEOUTS)
    _merge_adaptation_proof_config(claude_dir)
    try:
        yield
    finally:
        for table, before in zip(tables, saved):
            table.clear()
            table.update(before)


# {input repo-relpath: [proof basename, ...]}. A declared input binds exactly these proofs in place
# of the mention rule: a file every commit touches but that most proofs only name as a fixture.
PROOF_BINDINGS = {}

# {proof basename: directories it scans whole}, a project's own entries beside `_CORPUS_SCANNING`.
PROOF_CORPUS = {}


def _load_proof_bindings(claude_dir=_CLAUDE_DIR, *, text=None):
    """`adaptation.json` `proof_bindings`, optionally from snapshot text.
    A malformed table or entry is skipped with one stderr line."""
    try:
        if text is None:
            with open(adaptation.adaptation_path(claude_dir), encoding="utf-8") as fh:
                table = json.load(fh).get("proof_bindings", {})
        else:
            table = json.loads(text).get("proof_bindings", {})
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(table, dict):
        print("harness_tests: adaptation.json proof_bindings is not an object -- skipped", file=sys.stderr)
        return {}
    good = {}
    for path, proofs in table.items():
        if isinstance(proofs, list) and all(isinstance(p, str) for p in proofs):
            good[path] = list(proofs)
        else:
            print("harness_tests: proof_bindings entry %r is not a list of proof names -- skipped" % path,
                  file=sys.stderr)
    return good


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


def discover(tests_dir, *, paths=None):
    """Top-level proof files matching `_PATTERNS`; paths may supply a committed inventory.
    A `_`-prefixed file is a fixture or helper, never a proof. No recursion."""
    if paths is None:
        if not os.path.isdir(tests_dir):
            return []
        paths = [os.path.join(tests_dir, name) for name in os.listdir(tests_dir)
                 if os.path.isfile(os.path.join(tests_dir, name))]
    return sorted(path for path in paths if os.path.dirname(path) == tests_dir
                  and not os.path.basename(path).startswith("_")
                  and any(fnmatch.fnmatch(os.path.basename(path), p) for p in _PATTERNS))


_PYTEST_CASE_RE = re.compile(r"^(?:async\s+)?def\s+test\w*\s*\(|^class\s+Test\w*\b", re.MULTILINE)
_MAIN_GUARD_RE = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", re.MULTILINE)


def _pytest_style(path):
    """A Python proof with top-level pytest cases and no `__main__` block: `python <file>` defines its
    cases and exits 0 having run none, so it runs under pytest."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return False
    return bool(_PYTEST_CASE_RE.search(text)) and not _MAIN_GUARD_RE.search(text)


def _runner_cmd(path):
    if path.endswith(".py"):
        if _pytest_style(path):
            # Without a confcutdir, a proof outside the cwd has pytest walk conftests through their common ancestor.
            return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    "--confcutdir=" + os.path.dirname(os.path.abspath(path)), path]
        return [sys.executable, path]
    if path.endswith(".js"):
        return ["node", path]
    if path.endswith(".mjs"):
        return ["node", "--test", path]
    if path.endswith(".sh"):
        return [_bash_exe(), path]
    if path.endswith(".ps1"):
        return ["pwsh", "-NoProfile", "-File", path]
    raise ValueError("no runner for %s" % path)


_DETAIL_LINES = 20


# What `git rev-parse --local-env-vars` printed on git 2.x; used only when git cannot answer.
_GIT_LOCAL_ENV_FALLBACK = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)


def git_local_env_vars():
    """Git's own list of repository-local environment variables (`git rev-parse
    --local-env-vars`), or `_GIT_LOCAL_ENV_FALLBACK` when git cannot answer."""
    try:
        result = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True,
                                env={k: v for k, v in os.environ.items() if k not in _GIT_LOCAL_ENV_FALLBACK})
        names = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
        if result.returncode == 0 and names:
            return names
    except OSError:
        pass
    return _GIT_LOCAL_ENV_FALLBACK


GIT_REPO_ENV = git_local_env_vars()
NO_HEAD_RERUN_ENV = "HARNESS_NO_HEAD_RERUN"
STAMP_INDEX_ENV = "HARNESS_STAMP_INDEX"


# Windows GetTempPath reads TMP first, Python's tempfile TMPDIR first, POSIX tools TMPDIR.
_TEMP_ENV = ("TMP", "TEMP", "TMPDIR")
_CALLER_IDENTITY_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_PROMPT_FILE",
                        "CLAUDE_CODE_SIDECAR_LABEL", "SIDECAR_PARENT_SESSION_ID")


def _chmod_and_retry(func, path, _exc):
    # Git fixtures leave read-only object files, which Windows refuses to unlink.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_temp_root(root):
    """Delete a proof's temp root. A child the proof left running can still hold a file for a
    moment after a timeout kill, so retry briefly, then name what stayed on stderr."""
    for attempt in range(5):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(root, onexc=_chmod_and_retry)
            else:
                shutil.rmtree(root, onerror=lambda f, p, i: _chmod_and_retry(f, p, i[1]))
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if attempt == 4:
                print("harness_tests: could not remove proof temp root %s: %s" % (root, exc), file=sys.stderr)
                return
            time.sleep(0.5)


def _run_child(cmd, env, timeout, out_path, cwd=None):
    """(returncode, output), returncode None on timeout. Output goes to a file, never a pipe: a
    grandchild that outlives the proof keeps a pipe open past the timeout. Every process the proof
    started ends with it, on timeout or not, so none keeps writing into the next proof's world."""
    with open(out_path, "wb") as sink:
        proc = subprocess.Popen(cmd, stdout=sink, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                                start_new_session=(os.name != "nt"))
        tree = ProcessTree(proc)
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            returncode = None
        finally:
            tree.end()
    with open(out_path, "rb") as fh:
        return returncode, fh.read().decode("utf-8", "replace")


def _case_receipts(path, descriptors, output, file_status):
    observations = []
    for case in descriptors or []:
        cid, selector = case["caseId"], case["selector"]
        selector = selector.get('type') if isinstance(selector, dict) else selector
        seen = []
        for line in (output or "").splitlines():
            stripped = line.strip()
            status = None
            if selector == "check-label":
                match = re.fullmatch(r"(ok|FAIL|not ok|skipped)\s+(.+)", stripped)
                if match and match[2] == cid:
                    status = {"ok": "pass", "FAIL": "fail", "not ok": "fail", "skipped": "uncovered"}[match[1]]
            elif selector == "unittest-case":
                failed_case = re.fullmatch(r'(FAIL|ERROR): \w+ \(([^)]+)\)', stripped)
                if failed_case and (failed_case[2] == cid or failed_case[2].endswith('.' + cid)):
                    status = 'fail'
                match = re.fullmatch(r"\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR|skipped.*)", stripped)
                if match and (match[1] == cid or match[1].endswith("." + cid)):
                    status = "pass" if match[2] == "ok" else "uncovered" if match[2].startswith("skipped") else "fail"
            elif selector == "case-result":
                try:
                    record = json.loads(stripped)
                except ValueError:
                    record = None
                if isinstance(record, dict) and record.get("type") == "case-result" and record.get("caseId") == cid:
                    status = record.get("status") if record.get("status") in ("pass", "fail", "uncovered") else "uncovered"
            if status:
                seen.append((status, stripped))
        status = "fail" if any(s == "fail" for s, _ in seen) else "uncovered" if not seen or any(s == "uncovered" for s, _ in seen) else "pass"
        if file_status in ("cannot-run", "skipped") or "TIMEOUT" in (output or ""):
            status = "uncovered"
        observations.append({"proof": _norm(os.path.relpath(path, REPO_ROOT)), "caseId": cid,
                             "status": status, "inputKey": case["inputKey"],
                             "evidence": [line for _, line in seen] or ["named case not observed"]})
    return observations


def _read_events(log_dir):
    """The recorder's events from every process of one proof run, in no order; an unreadable line is
    dropped, and a process that never wrote one left no events."""
    events = []
    for name in os.listdir(log_dir) if os.path.isdir(log_dir) else []:
        try:
            with open(os.path.join(log_dir, name), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
    return events


def run_proof(path, timeout=None, root=None, *, behavior_cases=None, receipt_sink=None, reads_sink=None):
    """(status, seconds, detail) — status is "pass", "cannot-run" or "fail".

    A `reads_sink` dict turns on `scripts/_read_recorder` for a Python proof and its inheriting Python
    children, and receives `events` (what they read, probed, listed and spawned) and `temp` (the
    proof's temp root) for the pass cache's observed key.

    Each proof runs with TMP, TEMP and TMPDIR pointed at its own temp root, deleted when the
    proof ends on every exit path, so fixture folders a proof never removes do not pile up in
    the machine's TEMP.

    Exit 2 is unavailable coverage, not a passed or failed assertion, and so is a proof whose
    interpreter cannot launch: it asserted nothing. Its reason is reported, and a run with
    unavailable coverage does not issue a passing stamp.
    Proofs run without the caller's GIT_REPO_ENV: a private commit index must not reach a
    proof's temp-repo fixture. The stamp's own enumeration (`_git`) still honors it. The
    caller's GIT_INDEX_FILE reaches the proof as HARNESS_STAMP_INDEX, so a proof that reads
    the real repo's index passes it back as GIT_INDEX_FILE for that one call. A `root` runs the
    proof from that checkout: its cwd and CLAUDE_PROJECT_DIR, which about a third of proofs resolve
    the repo from.
    """
    if timeout is None:
        timeout = _PROOF_TIMEOUTS.get(os.path.basename(path), _TIMEOUT_SEC)
    env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_ENV and k != STAMP_INDEX_ENV}
    if os.environ.get("GIT_INDEX_FILE"):
        env[STAMP_INDEX_ENV] = os.environ["GIT_INDEX_FILE"]
    temp_root = tempfile.mkdtemp(prefix="harness_proof_")
    env.setdefault("HARNESS_PROVIDER_GUARD_LOG", os.path.join(temp_root, "workflow_provider_guard.jsonl"))
    # The runner already runs proofs side by side; a proof's own case pool (tests/_case_pool.py) nests
    # inside it, and 4 x 4 git-spawning processes timed out proofs that pass alone.
    env.setdefault("HARNESS_CASE_JOBS", "2")
    env.update({name: temp_root for name in _TEMP_ENV})
    # A runner the proof starts takes its slots here: waiting on the machine slot this proof holds
    # would deadlock. The caller's deadline binds this run, not the proof's own runs.
    env["HARNESS_SLOT_DIR"] = os.path.join(temp_root, "slots")
    env.pop("HARNESS_DEADLINE", None)
    # A proof is no session and no delegate: the caller's identity would let its doctrine state or
    # delegate guards decide the proof's verdict. A proof that needs one plants its own.
    for name in _CALLER_IDENTITY_ENV:
        env.pop(name, None)
    if root:
        env["CLAUDE_PROJECT_DIR"] = root
    log_dir = None
    if reads_sink is not None and path.endswith(".py"):
        log_dir = os.path.join(temp_root, "reads")
        env["HARNESS_READ_LOG"] = log_dir
        env["HARNESS_READ_RECORDER"] = _RECORDER_DIR
        env["PYTHONPATH"] = os.pathsep.join([_RECORDER_DIR] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    start = time.time()
    out = ""
    try:
        command = _runner_cmd(path)
        # pytest's exit 2 is an interrupted run or a collection error, a red; only a proof's own exit 2 means cannot-run.
        pytest_run = command[1:3] == ["-m", "pytest"]
        returncode, out = _run_child(command, env, timeout, os.path.join(temp_root, "proof_output.txt"), cwd=root)
        if log_dir:
            reads_sink.update(events=_read_events(log_dir), temp=temp_root)
        if returncode is None:
            status, detail = "fail", "TIMEOUT after %ds" % timeout
        elif returncode == 0:
            status, detail = "pass", ""
        elif returncode == 2 and not pytest_run:
            status, detail = "cannot-run", out
        else:
            status, detail = "fail", out
    except OSError as exc:
        status, detail = "cannot-run", "runner unavailable: %s" % exc
    finally:
        _remove_temp_root(temp_root)
    if receipt_sink is not None:
        receipts = _case_receipts(path, behavior_cases, detail if "TIMEOUT" in detail else out, status)
        for receipt in receipts:
            receipt["proof"] = _norm(os.path.relpath(path, root or REPO_ROOT))
        receipt_sink.extend(receipts)
    return status, time.time() - start, detail


def _git(repo_root, args):
    """Raise on any git failure: a hash over a failed enumeration must never be stamped."""
    result = subprocess.run(["git"] + args, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args[0], repo_root, result.stderr.strip()))
    return result.stdout


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
    # The setup surfaces sit outside `DIRS` (a root `.json`, `.claude/cloud-setup.sh`), so they are
    # enumerated from the constant rather than from `ls-files`.
    for rel in SURFACES:
        if os.path.isfile(os.path.join(repo_root, rel)):
            files.add(rel)

    # The stamp covers harness CODE, plus the setup surfaces (`SURFACES`): a `.json`, a `.sh` and a
    # `.md` that carry pins, each proven by `test_setup_surface_parity.py`. `.claude/scripts/benchmark_campaign/`
    # also holds campaign data a peer session writes mid-run (.env, score .json, .bak); hashing it
    # makes every peer write a stale stamp for a commit that changed no code.
    files = {f for f in files if _is_code(f) or _norm(f) in SURFACES}

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


def _write_stamp(repo_root, run, passed, excluded_list, indeterminate=(), entries=None, head=None, withheld=()):
    """Publish a full run's stamp under the stamp's lock. A claim a peer's scoped run stored meanwhile
    survives when it still matches the file's current bytes, unless this run `withheld` that input."""
    if entries is None:
        entries = tree_entries(repo_root)
    if head is None:
        head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    current = tree_entries(repo_root)

    def publish(doc):
        files = dict(entries)
        for path, digest in (doc.get("files") or {}).items():
            if path not in files and path not in withheld and digest == current.get(path):
                files[path] = digest
        blob = json.dumps(sorted(files.items())).encode("utf-8")
        close = {"close": doc["close"]} if "close" in doc else {}
        doc.clear()
        doc.update(close)
        doc.update({
            "ts": time.time(),
            "tree_hash": hashlib.sha256(blob).hexdigest(),
            "files": files,
            "head": head,
            "run": run,
            "pass": passed,
            # Recorded so a green stamp cannot claim more coverage than it had: these proofs ran and
            # could not bind their target, which is not the same as passing.
            "cannotRun": [os.path.basename(p) for p in indeterminate],
            "excluded": excluded_list,
        })

    written, _ = update_json_locked(STAMP_PATH, publish)
    if not written:
        print("harness_tests: stamp not written: the stamp file was locked or unwritable")


# Proofs that scan every hook rather than one target; a scoped run always includes them.
_DIR_SCANNING = ("test_hook_state.py",)

# {proof basename: tracked directories it lists and reads without naming each file}. The pass cache keys
# every file under them, so a proof reuses its pass only while that whole corpus is unchanged.
_CORPUS_SCANNING = {
    "test_hook_state.py": (".claude/hooks",),
    "test_claudemd_citations.py": (".claude",),
    "test_guard_text.py": (".claude/guards",),
}


def _corpus_dirs(name):
    return _CORPUS_SCANNING.get(name) or PROOF_CORPUS.get(name, ())


def _norm(rel):
    return rel.replace(os.sep, "/")


def _import_re(module):
    return re.compile(r"^\s*(?:from\s+%s\s+import\b|import\s+(?:[\w.]+\s*,\s*)*%s\b)"
                      % (re.escape(module), re.escape(module)), re.MULTILINE)


# Folders whose `.py` modules import one another by bare module name.
_IMPORT_DIRS = tuple(".claude/%s%s" % (d, sub) for d in ("hooks", "tools", "scripts") for sub in ("", "/lib"))


def _module_sources(repo_root):
    """{repo-relpath: text} of every `.py` directly under an `_IMPORT_DIRS` folder."""
    sources = {}
    for folder in _IMPORT_DIRS:
        full = os.path.join(repo_root, *folder.split("/"))
        if not os.path.isdir(full):
            continue
        for name in os.listdir(full):
            if name.endswith(".py"):
                try:
                    with open(os.path.join(full, name), "rb") as fh:
                        sources[folder + "/" + name] = fh.read().decode("utf-8", "replace")
                except OSError:
                    continue
    return sources


def _importers(touched, sources):
    """Repo-relpaths of the `sources` modules that import `touched`, a function-local import included.
    The closure goes past a direct importer only when it is a `_`-prefixed hooks helper: full transitive
    reach selects nearly every proof."""
    folder, _, name = touched.rpartition("/")
    if folder not in _IMPORT_DIRS or not name.endswith(".py"):
        return set()
    pending, seen, importers = [name[:-3]], {name[:-3]}, set()
    while pending:
        pattern = _import_re(pending.pop())
        for rel, text in sources.items():
            if rel == touched or rel in importers or not pattern.search(text):
                continue
            importers.add(rel)
            stem = rel.rsplit("/", 1)[-1][:-3]
            if rel.startswith(".claude/hooks/") and rel.count("/") == 2 and stem.startswith("_") and stem not in seen:
                seen.add(stem)
                pending.append(stem)
    return importers


def _tracked_by_basename(repo_root):
    """{basename: {repo-relpath}} over the files git tracks under `.claude/`; {} when git cannot
    answer, which leaves every basename unique (the plain basename mention rule)."""
    result = subprocess.run(["git", "ls-files", "--", ".claude"], cwd=repo_root, capture_output=True, text=True)
    census = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            rel = _norm(line.strip())
            if rel:
                census.setdefault(rel.rsplit("/", 1)[-1], set()).add(rel)
    return census


def _mention_token(rel, census):
    """What a proof's source must contain to name `rel`: its basename, or `<parent>/<basename>` when
    another tracked `.claude/` file shares the basename (every skill's `SKILL.md`)."""
    parts = rel.split("/")
    if len(parts) > 1 and census.get(parts[-1], set()) - {rel}:
        return "/".join(parts[-2:])
    return parts[-1]


def bindings(repo_root, touched, include_excluded=False, proofs=None):
    """{touched path: [bound proof paths]}, in discovery order. A file binds: itself when it is a
    proof, `test_<stem>*` / `<stem>_test.*` by name, any proof whose source names it (its basename,
    or its `<parent>/<basename>` when the basename is shared under `.claude/`), the dir-scanning
    proofs, and every proof that names a settings file when it is one. A `.py` module under
    `.claude/hooks/`, `tools/` or `scripts/` (top level or `lib/`) also binds the name and mention
    proofs of the modules there that import it, a function-local import included; the closure goes
    further only through a `_`-prefixed hooks helper. Each of those files binds as it would when
    touched: a `PROOF_BINDINGS` input binds its declared proofs in place of its mention rule. Over-selection is harmless;
    under-selection is what the name and mention rules guard. `proofs` restricts the candidates to
    those paths."""
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir) if proofs is None else list(proofs)
    if not include_excluded:
        discovered = [p for p in discovered if os.path.basename(p) not in EXCLUDED]
    census = _tracked_by_basename(repo_root)
    rules, sources = {}, None
    for t in dict.fromkeys(_norm(t) for t in touched):
        if sources is None and t.endswith(".py") and t.rpartition("/")[0] in _IMPORT_DIRS:
            sources = _module_sources(repo_root)
        named = [t] + sorted(_importers(t, sources or {}))
        rules[t] = (
            {os.path.splitext(n.rsplit("/", 1)[-1])[0] for n in named},
            [_mention_token(n, census) for n in named if n not in PROOF_BINDINGS],
            {proof for n in named for proof in PROOF_BINDINGS.get(n, ())},
            t in _SETTINGS_FILES,
        )
    texts = {}

    def text_of(path):
        if path not in texts:
            try:
                with open(path, "rb") as fh:
                    texts[path] = fh.read().decode("utf-8", "replace")
            except OSError:
                texts[path] = ""
        return texts[path]

    result = {t: [] for t in rules}
    for path in discovered:
        name = os.path.basename(path)
        rel = _norm(os.path.relpath(path, repo_root))
        for t, (stems, tokens, declared, settings) in rules.items():
            if (rel == t or name in _DIR_SCANNING or name in declared
                    or any(name.startswith("test_" + s) or name.startswith(s + "_test") for s in stems)
                    or any(tok in text_of(path) for tok in tokens)
                    or (settings and any(os.path.basename(f) in text_of(path) for f in _SETTINGS_FILES))):
                result[t].append(path)
    return result


def _behavior_plan(repo_root, paths=None):
    try:
        import extract_numbers
    except ImportError:
        return {"proofs": [], "cases": {}, "dependencies": {},
                "unresolved": [{"reason": "behavior checker unavailable"}]}
    return extract_numbers.behavior_proofs(repo_root, paths)


def select_for(repo_root, touched, include_excluded=False, proofs=None):
    """Every proof `bindings` binds to any of `touched`, in discovery order."""
    bound = {p for paths in bindings(repo_root, touched, include_excluded, proofs).values() for p in paths}
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    bound.update(os.path.join(repo_root, *rel.split('/')) for rel in _behavior_plan(repo_root, touched)["proofs"])
    return [p for p in (discover(tests_dir) if proofs is None else list(proofs)) if p in bound]


def select_for_commit(repo_root, touched):
    """(selected, left to close), in binding order. Unknown times exceed the inner budget;
    after the first eligible proof would exceed the total budget, no later proof is added."""
    if not touched:
        return [], 0
    bound = select_for(repo_root, touched)
    known = read_json_salvage(durations_path()) if os.path.exists(durations_path()) else {}
    selected, total = [], 0.0
    for proof in bound:
        seconds = known.get(os.path.basename(proof))
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0 or seconds > _PROOF_BUDGET_S):
            continue
        if total + seconds > COMMIT_BUDGET_S:
            break
        selected.append(proof)
        total += seconds
    return selected, len(bound) - len(selected)


def unproven_note(touched):
    """The clause every commit advisory about an unrun or unfinished proof ends with: the commit is
    unproven, and the command that proves it now."""
    return ("unproven, not passed: `python3 .claude/scripts/harness_tests.py --proofs-for %s` runs them now, "
            "and the close runs them before push." % " ".join(touched))


def run_commit(repo_root, touched):
    """Select and run cheap bound proofs with the scoped pass cache; return facts and advisories,
    never a commit verdict or stamp. The caller bounds this whole run, including cache/slot waits."""
    with _policy_from(os.path.join(repo_root, ".claude")), redirect_stdout(io.StringIO()):
        selected, left = select_for_commit(repo_root, touched)
        statuses, details = {}, {}
        if selected:
            _run_selected(selected, False, statuses, details,
                          RunOptions(cache=_pass_cache(repo_root, selected), root=repo_root, repo=repo_root))
    advisories = []
    for proof in selected:
        if statuses[proof] != "fail":
            continue
        lines = [line.strip() for line in details[proof].splitlines()]
        first = next((line for line in lines if (FAILURE_LINE.search(line) or _FAILURE_MARK_RE.search(line))
                      and not _UNITTEST_SUMMARY_RE.match(line)), _last_line(details[proof]))
        advisories.append("[harness commit] %s: %s; must be fixed before the close."
                          % (os.path.basename(proof), first or "failed without a failure-marked line"))
    unavailable = [p for p in selected if statuses[p] in ("cannot-run", "skipped")]
    if unavailable:
        proof = unavailable[0]
        advisories.append("[harness commit] proof run incomplete (%d proof(s)): %s: %s; %s"
                          % (len(unavailable), os.path.basename(proof), _last_line(details[proof]),
                             unproven_note(touched)))
    if left:
        advisories.append("[harness commit] %d bound proof(s) left to the close (recorded proof/commit budgets); %s"
                          % (left, unproven_note(touched)))
    return {"selected": [_norm(os.path.relpath(p, repo_root)) for p in selected],
            "left": left, "advisories": advisories}


def commit_advisories(repo_root, touched, git_env=None):
    """Hard-bound advisory run, including imports and selection. Leave cleanup headroom under the
    hook timeout; child proofs still use the runner's shared slots, deadlines and pass cache."""
    if not touched:
        return []
    env = dict(os.environ)
    env.update(git_env or {})
    env.setdefault("HARNESS_TEST_STAMP", os.path.join(repo_root, ".claude", "logs", "harness_tests_stamp.json"))
    env["HARNESS_DEADLINE"] = str(time.time() + max(0, COMMIT_BUDGET_S - 3))
    temp_root = tempfile.mkdtemp(prefix="harness_commit_")
    try:
        code, output = _run_child([sys.executable, os.path.abspath(__file__), "--repo", repo_root,
                                   "--commit"] + list(touched), env, COMMIT_BUDGET_S,
                                  os.path.join(temp_root, "result.json"), cwd=repo_root)
        if code is None:
            raise TimeoutError("timeout after %ds" % COMMIT_BUDGET_S)
        if code != 0:
            raise RuntimeError(_last_line(output) or "runner exit %s" % code)
        return json.loads(output)["advisories"]
    except Exception as exc:
        return ["[harness commit] bound proofs could not run: %s: %s; %s"
                % (type(exc).__name__, exc, unproven_note(touched))]
    finally:
        _remove_temp_root(temp_root)


def _budget_tier(seconds):
    if not isinstance(seconds, (int, float)):
        return "unrecorded"
    return "inner" if seconds <= _PROOF_BUDGET_S else "commit" if seconds <= COMMIT_BUDGET_S else "close"


def tiers(repo_root):
    """One row per discovered proof: its recorded pass `seconds` (or "unrecorded"), `budget` (inner 10 s,
    commit 30 s, else close), how the runner `runs` it (excluded, heavy, always, serial or bound), the
    harness inputs whose change `bindings` binds to it (`trigger`: count and up to 3 examples, the proof's
    own file not counted), and `flags` for a proof over the inner budget that runs on every scoped run
    (`slow-always`) and one no input triggers (`no-trigger`)."""
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir)
    own = {_norm(os.path.relpath(p, repo_root)) for p in discovered}
    inputs = sorted(set(tree_entries(repo_root)) - own)
    triggers = {p: [] for p in discovered}
    for path, bound in bindings(repo_root, inputs, include_excluded=True).items():
        for proof in bound:
            triggers[proof].append(path)
    durations = durations_path()
    known = read_json_salvage(durations) if os.path.exists(durations) else {}
    rows = []
    for proof in discovered:
        name = os.path.basename(proof)
        seconds = known.get(name) if isinstance(known.get(name), (int, float)) else "unrecorded"
        runs = ("excluded" if name in EXCLUDED else "heavy" if name in HEAVY else "always" if name in _DIR_SCANNING
                else "serial" if name in SERIAL else "bound")
        bound = sorted(triggers[proof])
        flags = []
        if runs == "always" and isinstance(seconds, (int, float)) and seconds > _PROOF_BUDGET_S:
            flags.append("slow-always")
        if not bound:
            flags.append("no-trigger")
        rows.append({"proof": name, "seconds": seconds, "budget": _budget_tier(seconds), "runs": runs,
                     "trigger": {"count": len(bound), "examples": bound[:3]}, "flags": flags})
    return rows


def format_tiers(rows):
    lines = ["%-48s %9s %-10s %-8s %s" % ("proof", "seconds", "budget", "runs", "trigger")]
    for row in rows:
        trig = row["trigger"]
        trigger = "%d: %s" % (trig["count"], ", ".join(trig["examples"])) if trig["count"] else "none"
        flag = ("  [%s]" % ", ".join(row["flags"])) if row["flags"] else ""
        lines.append("%-48s %9s %-10s %-8s %s%s" % (row["proof"], row["seconds"], row["budget"], row["runs"],
                                                    trigger, flag))
    return "\n".join(lines)


def _jobs():
    """Pool width: `HARNESS_TEST_JOBS` when a positive int, else 4. Proofs spend their time
    spawning git and shell processes, not on CPU, and 8 wide timed out proofs that pass alone."""
    try:
        jobs = int(os.environ.get("HARNESS_TEST_JOBS", ""))
    except ValueError:
        jobs = 0
    return jobs if jobs > 0 else 4


def _timed_out(result):
    return result[0] == "fail" and str(result[2] or "").startswith("TIMEOUT")


def _deadline():
    """`HARNESS_DEADLINE` (epoch seconds) when set and numeric, else None. A caller with a hard time
    limit sets it so the run stops starting proofs instead of being killed."""
    try:
        return float(os.environ["HARNESS_DEADLINE"])
    except (KeyError, ValueError):
        return None


_SKIPPED = ("skipped", 0.0, "not run: HARNESS_DEADLINE passed")


def _slot_dir():
    return os.environ.get("HARNESS_SLOT_DIR") or os.path.join(os.path.expanduser("~"), ".claude", ".harness_slots")


def _machine_slots():
    """`HARNESS_MACHINE_SLOTS` when a non-negative int; else the width the slot directory recorded, so a
    process started outside the client that sets the variable (the land runner service, a plain terminal)
    joins the machine's width; else 4, the width at which proofs that pass alone stopped timing out.
    0 turns slots off."""
    try:
        slots = int(os.environ.get("HARNESS_MACHINE_SLOTS", ""))
    except ValueError:
        slots = -1
    if slots >= 0:
        return slots
    path = os.path.join(_slot_dir(), "width.json")
    recorded = read_json_salvage(path).get("width") if os.path.exists(path) else None
    return recorded if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > 0 else 4


class SlotStoreError(OSError):
    """The machine slot directory cannot exist: waiting longer would never free a slot."""


def _claim_width(folder, count):
    """Record `count` as the slot directory's width. A caller whose width differs from the recorded
    one is refused while any slot of either width is held: the lock namespace is shared but each
    process counts its own slots, so a runner of another width would overlap the holder."""
    path = os.path.join(folder, "width.json")
    recorded = read_json_salvage(path).get("width") if os.path.exists(path) else None
    if recorded == count:
        return

    def claim(state):
        was = state.get("width")
        if isinstance(was, int) and was != count:
            for i in range(max(was, count)):
                handle = _file_lock.acquire(os.path.join(folder, "slot-%d.lock" % i), 0)
                if not handle:
                    raise SlotStoreError("machine slot width %d conflicts with width %d held by a running "
                                         "proof in %s; set HARNESS_MACHINE_SLOTS to %d or wait" % (count, was, folder, was))
                _file_lock.release(handle)
        state["width"] = count

    try:
        update_json_locked(path, claim, raising=True)
    except (OSError, TimeoutError) as exc:
        raise SlotStoreError("machine slot width cannot be recorded in %s (%s)" % (folder, exc))


def _take_slots(exclusive):
    """Handles for one machine slot, or for every slot when `exclusive` (a `SERIAL` proof owns the
    machine); None when the deadline passes first; raises `SlotStoreError` when the slot directory
    cannot be created or another width holds it. An exclusive taker holds `drain.lock` while it
    collects, so pool takers from every run stop claiming the slots it waits for."""
    count, folder = _machine_slots(), _slot_dir()
    if count == 0:
        return []
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise SlotStoreError("machine slot directory unusable: %s (%s)" % (folder, exc))
    _claim_width(folder, count)
    paths = [os.path.join(folder, "slot-%d.lock" % i) for i in range(count)]
    drain_path = os.path.join(folder, "drain.lock")
    deadline, held, drain = _deadline(), [], None
    while True:
        if exclusive:
            drain = drain or _file_lock.acquire(drain_path, 0)
            if drain:
                for path in paths[len(held):]:
                    handle = _file_lock.acquire(path, 0)
                    if not handle:
                        break
                    held.append(handle)
                if len(held) == count:
                    _file_lock.release(drain)
                    return held
        else:
            probe = _file_lock.acquire(drain_path, 0)
            if probe:
                _file_lock.release(probe)
                for path in paths:
                    handle = _file_lock.acquire(path, 0)
                    if handle:
                        return [handle]
        if deadline is not None and time.time() >= deadline:
            for handle in held:
                _file_lock.release(handle)
            _file_lock.release(drain)
            return None
        time.sleep(0.25)


def _launch(path, timeout=None, exclusive=False, root=None, recheck=None, timing=None, *, behavior_cases=None, receipt_sink=None,
            reads_sink=None):
    """The one place a proof process starts, for first runs, retries and HEAD comparisons: it takes the
    machine slot (`exclusive` or a `SERIAL` proof takes every slot), then `recheck()` may answer from
    the pass cache, then the deadline caps or refuses the run. Returns `_SKIPPED` when the deadline
    passes first, `cannot-run` when the slot store is unusable, else the `run_proof` result. A `timing`
    dict receives `wait_s`, the seconds until the slots were held (0 with slots off)."""
    asked = time.time()
    try:
        handles = _take_slots(exclusive or os.path.basename(path) in SERIAL)
    except SlotStoreError as exc:
        return ("cannot-run", 0.0, str(exc))
    finally:
        if timing is not None:
            timing["wait_s"] = round(time.time() - asked, 2)
    if handles is None:
        return _SKIPPED
    try:
        answer = recheck() if recheck else None
        if answer:
            return answer
        cut, deadline = False, _deadline()
        if deadline is not None:
            left = deadline - time.time()
            if left <= 0:
                return _SKIPPED
            if timeout is None:
                timeout = _PROOF_TIMEOUTS.get(os.path.basename(path), _TIMEOUT_SEC)
            cut, timeout = left < timeout, min(timeout, left)
        extra = {"reads_sink": reads_sink} if reads_sink is not None else {}
        if behavior_cases:
            result = run_proof(path, timeout, root, behavior_cases=behavior_cases, receipt_sink=receipt_sink, **extra)
        else:
            result = run_proof(path, timeout, root, **extra) if root else run_proof(path, timeout, **extra)
    finally:
        for handle in handles:
            _file_lock.release(handle)
    return _SKIPPED if cut and _timed_out(result) else result


_TOKEN_RE = re.compile(r"[\w][\w.\-]*")
_WORD_RE = re.compile(r"\w+")
_IMPORT_RE = re.compile(
    r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import\b[ \t]*(\([^)]*\)|[^\n#;]*)|import[ \t]+([\w., \t]+))", re.MULTILINE)
_STRING_MODULE_RE = re.compile(r"""(?<![\w.])[rRuU]?(['"])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\1""")
_RUN_MODULE_RE = re.compile(r"""(?:^|[\s'"])-m['"]?[ \t]*,?[ \t]*['"]?([A-Za-z_][\w.]*)""")
_JS_REQUIRE_RE = re.compile(r"""(?:\brequire\(|\bimport\(|\bfrom)\s*(['"])(\.{1,2}/[^'"]+)\1""")
_LINE_COMMENT = {".ps1": "#", ".sh": "#", ".js": "//", ".mjs": "//"}
# A block comment counts only as whole lines whose first closer ends its line, so code never shares it.
_BLOCK_COMMENT_RE = {".ps1": re.compile(r"^[ \t]*<#(?:(?!#>).)*#>[ \t]*$", re.MULTILINE | re.DOTALL),
                     ".js": re.compile(r"^[ \t]*/\*(?:(?!\*/).)*\*/[ \t]*$", re.MULTILINE | re.DOTALL)}
_BLOCK_COMMENT_RE[".mjs"] = _BLOCK_COMMENT_RE[".js"]
# pytest's cache steers only --lf/--ff/--nf/--sw, which no proof passes; every pytest proof rewrites it.
_TOOL_CACHE_RE = re.compile(r"(?:^|/)(?:\.pytest_cache|pytest-cache-files-[^/]*)(?:/|$)")


def _blank(text, spans):
    """`text` with each (start, end) offset span blanked, its line breaks kept."""
    out, at = [], 0
    for start, end in sorted(spans):
        out.append(text[at:start])
        out.append(re.sub(r"[^\n]", " ", text[start:end]))
        at = end
    return "".join(out) + text[at:]


def _python_code(text):
    """Python source without its comments and bare string statements (docstrings); None when it does
    not tokenize."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    def offset(row_col):
        return starts[row_col[0] - 1] + row_col[1]

    spans, statement_start, i = [], True, 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == tokenize.COMMENT:
            spans.append((offset(tok.start), offset(tok.end)))
        elif tok.type == tokenize.STRING and statement_start:
            j = i
            while tokens[j].type in (tokenize.STRING, tokenize.NL, tokenize.COMMENT):
                j += 1
            if tokens[j].type in (tokenize.NEWLINE, tokenize.ENDMARKER):
                spans += [(offset(t.start), offset(t.end)) for t in tokens[i:j]]
                i = j
                continue
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            statement_start = tok.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
        i += 1
    return _blank(text, spans)


def _code_text(rel, text):
    """The text of code file `rel` that may run: its comments and docstrings blanked. They never run or
    open a file, and following the files they name joined nearly every proof's reach to the whole tree.
    A Python file that does not tokenize keeps its whole text."""
    ext = os.path.splitext(rel)[1]
    if ext == ".py":
        code = _python_code(text)
        return text if code is None else code
    if ext in _BLOCK_COMMENT_RE:
        text = _BLOCK_COMMENT_RE[ext].sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    marker = _LINE_COMMENT.get(ext)
    if marker:
        text = "".join("\n" if line.lstrip().startswith(marker) else line
                       for line in text.splitlines(keepends=True))
    return text


def _split_command_line(line):
    """`line` split as a Windows child's C runtime reads it, the inverse of `subprocess.list2cmdline`;
    None when a double quote stays open, since no argument list joins to that."""
    args, buf, quoted, started, i = [], [], False, False, 0
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            j = i
            while j < len(line) and line[j] == "\\":
                j += 1
            if j < len(line) and line[j] == '"':
                buf.append("\\" * ((j - i) // 2) + ('"' if (j - i) % 2 else ""))
                i = j + 1 if (j - i) % 2 else j
            else:
                buf.append(line[i:j])
                i = j
            started = True
            continue
        if ch == '"':
            quoted, started = not quoted, True
        elif ch in " \t" and not quoted:
            if started:
                args.append("".join(buf))
            buf, started = [], False
        else:
            buf.append(ch)
            started = True
        i += 1
    if quoted:
        return None
    return args + ["".join(buf)] if started else args


class _PassCache:
    """A proof's pass, reused while every input it reaches is byte-identical and equally tracked.

    A proof reaches (`reach`): itself; every `.claude/` file whose basename, or whose code stem, its
    text names; its name-rule targets (`test_<stem>*`); its `proof_bindings` inputs; and the code any
    reached code loads, transitively, with the data files that each named or loaded code file names. Past
    the proof, only text that may run counts (`_code_text`): a comment or docstring loads nothing. The
    runner's own bytes join every key. The reach is the binding relation read from the proof's side, so a
    scoped run reuses a pass only when no input that would select the proof has changed since.
    A dir-scanning proof with no corpus entry (`_corpus_dirs`) is never reused. Rows live at `passes_path()`;
    a proof keeps one variant per live checkout of the repository, so worktrees whose inputs differ never
    evict each other's pass."""

    def __init__(self, repo_root, *, rev=None):
        self.root = repo_root
        self.rev = rev
        if rev:
            listed = _git(repo_root, ["ls-tree", "-r", "--name-only", rev, "--", ".claude"]).splitlines()
            self.tracked = {_norm(line) for line in listed if line}
            files = set(self.tracked)
        else:
            listed = _git(repo_root, ["ls-files", "--", ".claude"]).splitlines()
            others = _git(repo_root, ["ls-files", "-o", "--exclude-standard", "--", ".claude"]).splitlines()
            self.tracked = {_norm(line.strip()) for line in listed if line.strip()}
            files = self.tracked | {_norm(line.strip()) for line in others if line.strip()}
            tests = os.path.join(repo_root, ".claude", "tests")
            for folder, subdirs, names in os.walk(tests):
                subdirs[:] = [d for d in subdirs if d not in ("__pycache__", "logs")]
                files.update(_norm(os.path.relpath(os.path.join(folder, n), repo_root)) for n in names)
            files.update(rel for rel in SURFACES if os.path.isfile(os.path.join(repo_root, rel)))
        self.files = files
        self.by_base, self.by_stem, self.by_module = {}, {}, {}
        for rel in files:
            base = rel.rsplit("/", 1)[-1]
            self.by_base.setdefault(base, set()).add(rel)
            if rel.endswith(_CODE_EXT):
                self.by_stem.setdefault(os.path.splitext(base)[0], set()).add(rel)
            if rel.endswith(".py"):
                parts = rel[:-3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                for i in range(len(parts)):
                    self.by_module.setdefault("/".join(parts[i:]), set()).add(rel)
        bindings = PROOF_BINDINGS
        self.corpus = dict(PROOF_CORPUS)
        if rev:
            text = self._text(".claude/skills/project_subsystems/adaptation.json")
            bindings = _load_proof_bindings(text=text)
            try:
                corpus = json.loads(text).get("proof_corpus", {})
            except (ValueError, AttributeError):
                corpus = {}
            self.corpus = {name: tuple(folders) for name, folders in corpus.items()
                           if isinstance(folders, list) and all(isinstance(f, str) for f in folders)} if isinstance(corpus, dict) else {}
        self.declared = {}
        for rel, proofs in bindings.items():
            for proof in proofs:
                self.declared.setdefault(proof, set()).add(rel)
        self.path = passes_path()
        self._named, self._loaded, self._digests, self._code = {}, {}, {}, {}
        self.runner = self._digest("<runner>", os.path.abspath(__file__))

    def _read_digest(self, rel, full=None):
        try:
            with open(full or os.path.join(self.root, rel), "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return None

    def _digest(self, rel, full=None):
        if rel not in self._digests:
            self._digests[rel] = self._read_digest(rel, full)
        return self._digests[rel]

    def _text(self, rel):
        if self.rev:
            return _git(self.root, ["show", self.rev + ":" + rel])
        try:
            with open(os.path.join(self.root, rel), "rb") as fh:
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def _code_of(self, rel):
        if rel not in self._code:
            text = self._text(rel)
            self._code[rel] = _code_text(rel, text) if rel.endswith(_CODE_EXT) else text
        return self._code[rel]

    def _names(self, rel, stems=True, raw=False):
        """Every file `rel` names by basename, and by code stem when `stems`; in its runnable text
        (`_code_text`) unless `raw`."""
        if (rel, stems, raw) not in self._named:
            text, found = self._text(rel) if raw else self._code_of(rel), set()
            for token in _TOKEN_RE.findall(text):
                found |= self.by_base.get(token.rstrip(".-"), set())
            if stems:
                for word in set(_WORD_RE.findall(text)):
                    found |= self.by_stem.get(word, set())
            self._named[rel, stems, raw] = found
        return self._named[rel, stems, raw]

    def _module_files(self, importer, dotted, level):
        """The `.py` files a module path names: the module (or its package `__init__`), every
        parent package's `__init__`, found by the path's tail so a package that lives under a
        directory on `sys.path` still resolves. A relative import (`level` leading dots) starts at
        the importer's own directory."""
        parts = [p for p in dotted.split(".") if p]
        if level:
            base = importer.split("/")[:-1]
            base = base[:max(0, len(base) - (level - 1))]
            exact = "/".join(base + parts)
            found = {r for r in (exact + ".py", exact + "/__init__.py") if r in self.files}
        elif parts:
            found = set(self.by_module.get("/".join(parts), ()))
        else:
            found = set()
        for rel in list(found):
            folder = rel.split("/")[:-1]
            while folder:
                init = "/".join(folder + ["__init__.py"])
                if init in self.files:
                    found.add(init)
                folder = folder[:-1]
        return found

    def _loads(self, rel):
        """The code `rel` may run: the code files its runnable text names by file name, which it may
        start or source; a module it runs by `-m`; a JS file's relative `require`/`import`; and a Python
        file's imported modules, resolved by their qualified or relative path as well as by the first
        name's stem, with every string that spells a module name (`importlib.import_module("x")`)."""
        if rel not in self._loaded:
            found, code = set(), self._code_of(rel)
            for match in _RUN_MODULE_RE.finditer(code):
                found |= self._module_files(rel, match.group(1), 0)
            if rel.endswith((".js", ".mjs")):
                folder = rel.rsplit("/", 1)[0]
                for match in _JS_REQUIRE_RE.finditer(code):
                    target = posixpath.normpath(posixpath.join(folder, match.group(2)))
                    found |= {t for t in (target, target + ".js", target + ".mjs", target + "/index.js")
                              if t in self.files}
            if rel.endswith(".py"):
                for match in _STRING_MODULE_RE.finditer(code):
                    found |= self._module_files(rel, match.group(2), 0)
                for match in _IMPORT_RE.finditer(code):
                    if match.group(3):
                        for name in match.group(3).split(","):
                            name = name.strip().split(" ")[0]
                            found |= self._module_files(rel, name, 0)
                            found |= {r for r in self.by_stem.get(name.split(".")[0], ()) if r.endswith(".py")}
                        continue
                    module = match.group(1)
                    level = len(module) - len(module.lstrip("."))
                    module = module.lstrip(".")
                    found |= self._module_files(rel, module, level)
                    found |= {r for r in self.by_stem.get(module.split(".")[0], ()) if r.endswith(".py")}
                    for name in match.group(2).replace("(", " ").replace(")", " ").split(","):
                        name = name.split("#")[0].strip().split(" ")[0]
                        if name and name != "*":
                            found |= self._module_files(rel, (module + "." if module else "") + name, level)
            found |= {r for r in self._names(rel, stems=False) if r.endswith(_CODE_EXT)}
            self._loaded[rel] = found
        return self._loaded[rel]

    def reach(self, proof):
        """The proof, what it names, the corpus it scans, the data its named or loaded code names, and the code any of those
        load, transitively. The proof's whole text names, as `bindings` reads it; past the proof, a file only
        a comment or docstring names is not reached: that chain joins every file to every other and would
        reuse nothing."""
        rel = _norm(os.path.relpath(proof, self.root))
        name = rel.rsplit("/", 1)[-1]
        seen = {rel} | self.declared.get(name, set()) | self._names(rel, raw=True)
        for stem, rels in self.by_stem.items():
            if name.startswith("test_" + stem) or name.startswith(stem + "_test"):
                seen |= rels
        for folder in (_CORPUS_SCANNING.get(name) or self.corpus.get(name, ())):
            seen |= {r for r in self.files if r.startswith(folder + "/")}
        return self._expand(seen, rel)

    def _expand(self, seen, rel):
        """`seen` plus the data its named code names and the code any of it loads, transitively."""
        for named in [r for r in seen if r.endswith(_CODE_EXT) and r != rel]:
            seen |= {r for r in self._names(named) if not r.endswith(_CODE_EXT)}
        pending = [r for r in seen if r.endswith(_CODE_EXT)]
        while pending:
            code = pending.pop()
            for found in self._loads(code):
                if found not in seen:
                    seen.add(found)
                    pending.append(found)
                    seen |= {r for r in self._names(found) if not r.endswith(_CODE_EXT)}
        return seen

    def _within(self, path, base):
        """`path` relative to `base` in `/` form when it lies inside it, else None."""
        path, base = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(base))
        if path == base or not path.startswith(base.rstrip(os.sep) + os.sep):
            return None
        return _norm(os.path.relpath(path, base))

    def manifest(self, proof, sink):
        """What a Python proof's pass used, from its recorder events, relative to this cache's root:
        `files` it read (an imported module's `.pyc` counts as its source), `probes` it tested for
        existence, `dirs` it listed, `static` scripts of children the recorder could not follow (keyed by
        their static reach), and `git` when git ran on this checkout. `opaque` marks reads the cache cannot
        trust: a write into the checkout outside caches, logs and scratch, or a child it cannot resolve."""
        events, temp = sink.get("events"), sink.get("temp")
        if not events:
            # A recorded Python process opens at least its own script; no events means the recorder
            # never loaded, and a manifest built from nothing would reuse the pass forever.
            return None
        found = {"files": set(), "probes": set(), "dirs": set(), "static": set(), "git": False, "opaque": False}
        written = set()

        def inside(path):
            if not isinstance(path, str) or (temp and self._within(path, temp) is not None):
                return None
            return self._within(path, self.root)

        for event in events:
            if not (isinstance(event, list) and len(event) == 2):
                continue
            kind, value = event
            if kind == "s":
                self._classify_spawn(value if isinstance(value, dict) else {}, temp, inside, found)
                continue
            rel = inside(value)
            if rel is None:
                continue
            if rel == ".git" or rel.startswith(".git/"):
                found["git"] = True
                continue
            if _TOOL_CACHE_RE.search(rel):
                continue
            if kind == "w":
                if "__pycache__/" not in rel:
                    written.add(rel)
                    if not rel.startswith((".claude/logs/", ".claude/scratch/")):
                        found["opaque"] = True
            elif kind == "r":
                cached = re.match(r"(.*?)(?:^|/)__pycache__/([^/.]+)\.[^/]*\.pyc$", rel)
                found["files"].add((cached.group(1) + "/" if cached.group(1) else "") + cached.group(2) + ".py"
                                   if cached else rel)
            elif kind == "p":
                found["probes"].add(rel)
            elif kind == "l":
                found["dirs"].add(rel)
        found["files"] -= written
        found["probes"] -= found["files"] | written
        return {k: sorted(v) if isinstance(v, set) else v for k, v in found.items()}

    def _classify_spawn(self, spawn, temp, inside, found):
        """Fold one spawned process into `found`: an inheriting Python child logged its own reads; any
        other child's repo scripts join `static`, git on this checkout sets `git`, and a child whose
        reads cannot be bounded sets `opaque`."""
        argv = [a for a in spawn.get("argv") or [] if isinstance(a, str)]
        if len(argv) == 1 and any(ch.isspace() for ch in argv[0]):
            # Windows hands the audit event the joined command line, not the list.
            argv = _split_command_line(argv[0])
            if argv is None:
                found["opaque"] = True
                return
        cwd = spawn.get("cwd") or self.root
        if not argv:
            found["opaque"] = True
            return
        prog = os.path.basename(argv[0]).lower()
        if prog.startswith("python") or prog in ("py", "py.exe"):
            rest = argv[1:]
            flags = []
            while rest and rest[0].startswith("-") and rest[0] not in ("-c", "-m"):
                flags.append(rest.pop(0))
            isolated = any(not f.startswith("--") and any(c in f[1:] for c in "IES") for f in flags)
            if spawn.get("recorded") and not isolated:
                return
            script = os.path.join(cwd, rest[0]) if rest and rest[0] not in ("-c", "-m") else None
            rel = inside(script) if script and os.path.isfile(script) else None
            if rel is None:
                found["opaque"] = True
            else:
                found["static"] |= {rel} | self._repo_files(rest[1:], cwd, inside)
            return
        tokens = []
        for arg in argv:
            tokens += arg.split() if any(ch.isspace() for ch in arg) else [arg]
        if prog in ("git", "git.exe") or "git" in tokens[1:]:
            where = os.path.join(cwd, tokens[tokens.index("-C") + 1].strip("'\"") if "-C" in tokens[:-1] else ".")
            at_root = os.path.normcase(os.path.abspath(where)) == os.path.normcase(os.path.abspath(self.root))
            if at_root or inside(where) is not None:
                found["git"] = True
        found["static"] |= self._repo_files(tokens, cwd, inside)

    @staticmethod
    def _repo_files(args, cwd, inside):
        """The checkout files that command-line `args` name, as a whole argument or an `--opt=<path>` value."""
        found = set()
        for arg in args:
            arg = arg.strip("'\"")
            for candidate in {arg, arg.partition("=")[2].strip("'\"")} - {""}:
                path = os.path.join(cwd, candidate)
                rel = inside(path) if os.path.isfile(path) else None
                if rel is not None:
                    found.add(rel)
        return found

    def _listing(self, rel):
        full = os.path.join(self.root, rel)
        return sorted(n for n in os.listdir(full) if n != "__pycache__") if os.path.isdir(full) else None

    def _repo_state(self):
        """HEAD's tree and the tracked files' uncommitted changes. Untracked files stay out: the runner's
        own stamp, slots and pass rows are untracked, and counting them would rerun every git-using proof."""
        if not hasattr(self, "_state"):
            try:
                status = _git(self.root, ["status", "--porcelain", "--untracked-files=no"])
                self._state = [_git(self.root, ["rev-parse", "HEAD^{tree}"]).strip(),
                               hashlib.sha256(status.encode("utf-8")).hexdigest()]
            except RuntimeError:
                self._state = None
        return self._state

    def observed_key(self, proof, manifest, fresh=False):
        """The reuse key over what the proof's last pass used (`manifest`), or None when it has none or
        its reads are opaque. Any change to a file it read, a path it probed appearing or vanishing, an
        entry added to a directory it listed, an unrecorded child's static reach, or this checkout's
        state when git ran on it, changes the key."""
        if not isinstance(manifest, dict) or manifest.get("opaque"):
            return None
        digest = self._read_digest if fresh else self._digest
        rel = _norm(os.path.relpath(proof, self.root))
        used = set(manifest.get("files", [])) | {rel}
        for script in manifest.get("static", []):
            if script.endswith(_CODE_EXT) or self._text(script).startswith("#!"):
                used |= self._expand({script} | self._names(script), script)
            else:
                # A data argument runs nothing: its bytes are the input, not the code its prose names.
                used.add(script)
        rows = sorted([r, digest(r), r in self.tracked] for r in used)
        probes = [[r, os.path.lexists(os.path.join(self.root, r))] for r in manifest.get("probes", [])]
        dirs = [[r, self._listing(r)] for r in manifest.get("dirs", [])]
        state = self._repo_state() if manifest.get("git") else None
        if manifest.get("git") and state is None:
            return None
        runner = self._read_digest("<runner>", os.path.abspath(__file__)) if fresh else self.runner
        recorder = self._digest("<recorder>", os.path.join(_RECORDER_DIR, "sitecustomize.py"))
        behavior = _behavior_plan(self.root, [rel]).get("dependencies", {}).get(rel, {})
        blob = json.dumps([runner, recorder, rows, probes, dirs, state,
                           behavior.get("inputKey"), behavior.get("bindingHash")]).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def key(self, proof, fresh=False):
        """The proof's reuse key. `fresh` rereads every input instead of the run's memoized digests:
        a pass is recorded only when that equals the key taken before the run."""
        name = os.path.basename(proof)
        if name in _DIR_SCANNING and not _corpus_dirs(name):
            return None
        digest = self._read_digest if fresh else self._digest
        rows = sorted([rel, digest(rel), rel in self.tracked] for rel in self.reach(proof))
        runner = self._read_digest("<runner>", os.path.abspath(__file__)) if fresh else self.runner
        rel = _norm(os.path.relpath(proof, self.root))
        behavior = _behavior_plan(self.root, [rel]).get("dependencies", {}).get(rel, {})
        blob = json.dumps([runner, rows, behavior.get("inputKey"), behavior.get("bindingHash")]).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def _rows(self):
        rows = read_json_salvage(self.path) if os.path.exists(self.path) else {}
        return rows if isinstance(rows, dict) else {}

    def variants(self, proof):
        """The proof's recorded passes, newest first."""
        entry = self._rows().get(_norm(os.path.relpath(proof, self.root)))
        entry = [entry] if isinstance(entry, dict) else entry
        return [row for row in entry if isinstance(row, dict)] if isinstance(entry, list) else []

    def _matched(self, proof, key):
        """The proof's row when its key still holds, else None. A row with an observed key is judged by it
        alone: the static reach cannot see a listed directory gain an entry or git state move, so a static
        match there would reuse a stale pass. A row without one is judged by the static key."""
        for row in self.variants(proof):
            okey = row.get("okey")
            if okey:
                if self.observed_key(proof, row.get("manifest")) == okey:
                    return row
            elif key and row.get("key") == key:
                return row
        return None

    def observations(self, proof, key, cases=None):
        row = self._matched(proof, key) or {}
        rows = row.get("observations", [])
        if cases and any(not any(r.get("caseId") == c["caseId"] and r.get("inputKey") == c["inputKey"]
                                 and r.get("status") == "pass" for r in rows) for c in cases):
            return None
        return rows

    def hit(self, proof, key, cases=None):
        return bool(key) and self._matched(proof, key) is not None and self.observations(proof, key, cases) is not None

    def record(self, proof, key=None, observations=None, manifest=None):
        key = key or self.key(proof)
        if not key:
            return
        rel = _norm(os.path.relpath(proof, self.root))
        okey = self.observed_key(proof, manifest, fresh=True) if manifest else None
        checkout = os.path.normcase(os.path.abspath(self.root))
        # A close proves HEAD in a temp checkout it removes when it ends; its rows are the ones the next close
        # reuses, so they record under one name that outlives the directory and the next close replaces.
        if os.path.basename(checkout).startswith(_CHECKOUT_PREFIX):
            checkout = _CLOSE_CHECKOUT

        def update(rows):
            now = time.time()
            for name in list(rows):
                entry = rows[name]
                entry = [entry] if isinstance(entry, dict) else entry if isinstance(entry, list) else []
                entry = [r for r in entry if isinstance(r, dict) and now - r.get("ts", 0) <= 30 * 86400]
                if entry:
                    rows[name] = entry
                else:
                    rows.pop(name)
            row = {"key": key, "ts": round(now, 1), "observations": observations or [], "checkout": checkout}
            if okey:
                row.update(manifest=manifest, okey=okey)
            rows[rel] = [row] + [r for r in rows.get(rel, [])
                                 if r.get("checkout") not in (checkout, None)
                                 and (r.get("key"), r.get("okey")) != (key, okey)
                                 and (r["checkout"] == _CLOSE_CHECKOUT or os.path.isdir(r["checkout"]))]
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            update_json_locked(self.path, update)
        except (OSError, TimeoutError, RuntimeError, ValueError):
            pass


def _pass_cache(repo_root, proofs):
    """A `_PassCache` for a scoped run, or None when `HARNESS_NO_PASS_CACHE` is set or git cannot
    list the tree: an unknown reach reuses nothing."""
    if os.environ.get("HARNESS_NO_PASS_CACHE") or not proofs:
        return None
    try:
        return _PassCache(repo_root)
    except (RuntimeError, OSError):
        return None


def _record_durations(results):
    """A pass records its time; an assertion failure drops the record, so a proof red alone is not
    retried. A timeout keeps it: running out of time under load says nothing about the proof."""
    def update(state):
        for path, result in results.items():
            if result[0] == "pass":
                state[os.path.basename(path)] = round(result[1], 1)
            elif result[0] == "fail" and not _timed_out(result):
                state.pop(os.path.basename(path), None)
    update_json_locked(durations_path(), update)


_LOG_LOCK_SECONDS = 2.0


def _record_run(row):
    """Append one run row to `harness_runs.json`, keeping the newest `_RUNS_KEPT`; best-effort."""
    def update(state):
        runs = state.get("runs")
        state["runs"] = ((runs if isinstance(runs, list) else []) + [row])[-_RUNS_KEPT:]
    update_json_locked(runs_path(), update)


def _budget_line(row):
    """The advisory close line: wall time against the close budget, the slowest bound proofs over
    the inner-loop budget, and the total queue wait. It never decides an exit code."""
    slow = sorted((p for p in row.get("proofs", []) if p.get("run_s", 0) > _PROOF_BUDGET_S),
                  key=lambda p: -p["run_s"])[:5]
    named = ", ".join("%s %.1fs" % (p["proof"], p["run_s"]) for p in slow) or "none"
    return ("harness_tests: close budget: %.1fs of %ds; proofs over %ds: %s; queue wait %.1fs"
            % (row.get("wall_s", 0), _CLOSE_BUDGET_S, _PROOF_BUDGET_S, named, row.get("queue_s", 0)))


def _record_flaky(path, first_detail, retry_elapsed):
    """Append one row per pool failure that passed alone, so a flaky proof stays visible and gets
    fixed. `failures` keeps the failing case lines: the summary alone cannot name the case to trace
    once the retry has passed. Capped at the newest 200 rows."""
    lines = [line.strip() for line in (first_detail or "").strip().splitlines()]
    last = lines[-1:] or [""]
    failures = [line[:200] for line in lines if FAILURE_LINE.search(line)][:5]
    row = json.dumps({"ts": time.time(), "proof": os.path.basename(path), "first": last[0],
                      "failures": failures, "retry_seconds": round(retry_elapsed, 1)})
    log = _beside_stamp(FLAKY_LOG, "harness_flaky.jsonl")
    _append_diagnostic(log, row)


def _append_diagnostic(log, row):
    """Bounded, locked JSONL history shared by flaky and known-red diagnostics."""
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with _file_lock.locked(log + ".lock", _LOG_LOCK_SECONDS):
            with open(log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(row + "\n")
            with open(log, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
            if len(rows) > 400:
                tmp = "%s.%d.tmp" % (log, os.getpid())
                with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write("\n".join(rows[-200:]) + "\n")
                os.replace(tmp, log)
    except (OSError, TimeoutError):
        pass


def _flaky_history(days=30):
    """{proof: count} of FLAKY rows newer than `days`; {} when the log is absent or unreadable."""
    log = _beside_stamp(FLAKY_LOG, "harness_flaky.jsonl")
    cutoff, counts = time.time() - days * 86400, {}
    try:
        with open(log, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("ts", 0) >= cutoff and row.get("proof"):
                    counts[row["proof"]] = counts.get(row["proof"], 0) + 1
    except OSError:
        return {}
    return counts


class RunOptions(NamedTuple):
    """How `_run_selected` runs. `cache` is a `_PassCache` or None; `root` is the checkout the proofs
    run in (None means the live one). A run is recorded to `harness_runs.json` only when `mode` names
    it; `row`, when given, also receives the recorded row. `stop` latches a newer close superseding this
    one: queued proofs and retries skip while already running proofs drain and cache their passes."""
    cache: object = None
    root: object = None
    mode: object = None
    row: object = None
    stop: object = None
    paths: object = None
    base: object = "HEAD"
    behavior_out: object = None
    repo: object = None


def _run_selected(selected, verbose, statuses=None, details=None, options=RunOptions()):
    """Run the pool-safe proofs `_jobs()` wide, slowest recorded first, then each `SERIAL` proof
    alone, each with `_timeout_for`. Every proof first takes a machine slot (`_take_slots`; a `SERIAL`
    proof takes all of them), so overlapping runs queue instead of timing each other out. An `options.cache`
    (`_PassCache`) reuses a proof whose reach is unchanged since its last pass, checked again once
    the slot is taken so a pass a peer run landed meanwhile counts, and records each pass as it lands
    unless a reached input moved since its key was taken.
    A failing proof with a recorded pass is re-run once alone, holding every machine slot: a pass
    there is reported FLAKY and logged, not red, because contention (a timeout under load, a shared
    temp race) is a runner condition; a second failure is red. A timeout is re-run alone even with no
    recorded pass, at `_LOAD_FACTOR` times its timeout, or at its `proof_timeouts` cap, which already holds
    that headroom, and its pass there is not flaky: the first timeout came from a missing duration; an assertion failure with no recorded pass is
    not re-run, because a known red would only repeat. Past `HARNESS_DEADLINE` no proof starts or
    retries, and one the deadline cuts short is `skipped`, counted with the indeterminate ones.
    Returns (passed, failed, indeterminate, wall seconds); a `statuses` dict
    receives each proof's final status and a `details` dict its final detail. A run with an `options.mode`
    records its row (`mode`, wall and queue seconds, each proof's status, run and wait seconds, and whether
    it was reused) and the row is appended to `harness_runs.json`."""
    cache, root, mode = options.cache, options.root, options.mode
    behavior_root = options.repo or root or getattr(cache, 'root', REPO_ROOT)
    plan = _behavior_plan(behavior_root, [_norm(os.path.relpath(p, behavior_root)) for p in selected])
    cases = {p: plan.get('cases', {}).get(_norm(os.path.relpath(p, behavior_root)), []) for p in selected}
    observations = {p: [] for p in selected}
    start = time.time()
    durations = durations_path()
    known = read_json_salvage(durations) if os.path.exists(durations) else {}
    keys = {p: cache.key(p) for p in selected} if cache else {}
    deadline = _deadline()
    reused = set()
    waits = {}

    def expected(path):
        value = known.get(os.path.basename(path), 0)
        return float(value) if isinstance(value, (int, float)) else 0.0

    stop = options.stop
    moved = ("skipped", 0.0, "not run: a newer close superseded this one")

    def attempt(path, timeout, exclusive=False):
        if stop and stop():
            return moved
        key = keys.get(path)
        if key and (cache.hit(path, key, cases[path]) if cases[path] else cache.hit(path, key)):
            reused.add(path)
            if cases[path]:
                observations[path].extend(cache.observations(path, key, cases[path]))
            return ("pass", 0.0, "")

        def recheck():
            nonlocal key
            if stop and stop():
                return moved
            if not key:
                return None
            key = cache.key(path, fresh=True)
            if cache.hit(path, key, cases[path]) if cases[path] else cache.hit(path, key):
                reused.add(path)
                if cases[path]:
                    observations[path].extend(cache.observations(path, key, cases[path]))
                return ("pass", 0.0, "")
            return None

        launch_options = {"root": root} if root else {}
        if cases[path]:
            launch_options.update(behavior_cases=cases[path], receipt_sink=observations[path], root=behavior_root)
        reads = {} if key and path.endswith(".py") else None
        if reads is not None:
            launch_options["reads_sink"] = reads
        timing = {}
        result = _launch(path, timeout, exclusive, recheck=recheck if key or stop else None, timing=timing, **launch_options)
        if stop:
            stop()
        waits[path] = waits.get(path, 0.0) + timing.get("wait_s", 0.0)
        if result[0] == "pass" and key and path not in reused and cache.key(path, fresh=True) == key:
            try:
                manifest = cache.manifest(path, reads) if reads else None
            except Exception:  # noqa: BLE001 - a manifest fault costs only the observed key, never the run
                manifest = None
            if cases[path]:
                if all(r['status'] == 'pass' for r in observations[path]):
                    cache.record(path, key, observations[path], manifest=manifest)
            else:
                cache.record(path, key, manifest=manifest)
        return result

    done, done_lock = {"n": 0, "at": time.time(), "left": sum(expected(p) for p in selected), "ran": 0.0}, threading.Lock()

    def progressed(path, result):
        """Print `N of M proofs done` every `_PROGRESS_EVERY` proofs or minute on a run of at least
        `_PROGRESS_MIN`: a whole close otherwise prints nothing until it ends. Each line also states the
        recorded seconds the unfinished proofs need at this pool width, and the pace: elapsed over the
        recorded time of the proofs that ran, so a reader sees whether the run keeps its recorded schedule."""
        with done_lock:
            done["n"] += 1
            done["left"] -= expected(path)
            if path not in reused:
                done["ran"] += expected(path)
            now = time.time()
            if len(selected) >= _PROGRESS_MIN and (done["n"] % _PROGRESS_EVERY == 0 or now - done["at"] >= 60
                                                   or done["n"] == len(selected)):
                done["at"] = now
                pace = (now - start) / (done["ran"] / _jobs()) if done["ran"] else 0.0
                _progress("%d of %d proofs done (%d reused), %ds; ~%ds left at recorded times, pace x%.1f"
                          % (done["n"], len(selected), len(reused), now - start, max(0.0, done["left"]) / _jobs(), pace))
        return result

    ordered = sorted(selected, key=lambda p: -expected(p))
    pooled = [p for p in ordered if os.path.basename(p) not in SERIAL]
    alone = [p for p in ordered if os.path.basename(p) in SERIAL]
    results = {}
    with ThreadPoolExecutor(max_workers=_jobs()) as pool:
        for path, result in zip(pooled, pool.map(lambda p: progressed(p, attempt(p, _timeout_for(p, known))), pooled)):
            results[path] = result
    for path in alone:
        results[path] = progressed(path, attempt(path, _timeout_for(path, known)))
    flaky = []
    for path in selected:
        timed_out = _timed_out(results[path])
        cold = timed_out and os.path.basename(path) not in known
        if not cases[path] and results[path][0] == "fail" and (os.path.basename(path) in known or timed_out):
            capped = os.path.basename(path) in _PROOF_TIMEOUTS
            timeout = _timeout_for(path, known) * (_LOAD_FACTOR if timed_out and not capped else 1)
            retry = attempt(path, timeout, exclusive=True)
            if retry[0] == "skipped":
                if timed_out:
                    results[path] = retry
                continue
            if retry[0] == "pass" and not cold:
                flaky.append((path, results[path][2]))
                _record_flaky(path, results[path][2], retry[1])
            results[path] = retry
    _record_durations({p: r for p, r in results.items() if p not in reused and r[0] != "skipped"})

    passed, failed, indeterminate = 0, 0, []
    if reused:
        print("harness_tests: %d of %d proofs reused from the pass cache (%s)"
              % (len(reused), len(selected), passes_path()))
    label = {"pass": "OK", "cannot-run": "CANNOT-RUN", "fail": "FAIL", "skipped": "SKIPPED"}
    for path, first_detail in flaky:
        tail = (first_detail or "").strip().splitlines()[-1:] or [""]
        print("FLAKY %s: failed in the pool (%s), passed alone; logged to %s"
              % (path, tail[0], _beside_stamp(FLAKY_LOG, "harness_flaky.jsonl")))
    for path in selected:
        status, elapsed, detail = results[path]
        if statuses is not None:
            statuses[path] = status
        if details is not None:
            details[path] = detail
        if verbose or status != "pass":
            print("%s %.1fs %s" % (label[status], elapsed, path))
        if detail:
            for line in detail.strip().splitlines()[-_DETAIL_LINES:]:
                print("    | " + line)
        if status == "pass":
            passed += 1
        elif status in ("cannot-run", "skipped"):
            indeterminate.append(path)
        else:
            failed += 1
    wall = time.time() - start
    receipts = [r for p in selected for r in observations[p]]
    behavior_report = None
    behavior_acceptance = 'pass'
    if mode is not None or receipts or options.behavior_out or plan.get('proofs'):
        try:
            import extract_numbers
        except ImportError:
            behavior_report = {'status': 'uncovered', 'counts': {'uncovered': 1}, 'units': [], 'claims': [],
                'uncovered': [{'status': 'uncovered', 'reason': 'behavior checker unavailable'}],
                'scope': {'affected': [], 'changedUnits': [], 'initialDebt': [],
                          'errors': ['behavior checker unavailable']}}
        else:
            behavior_report = extract_numbers.behavior_report(behavior_root, paths=options.paths,
                base=options.base or 'HEAD', proof_results={'observations': receipts})
        behavior_report['observations'] = receipts
        if options.behavior_out:
            os.makedirs(os.path.dirname(os.path.abspath(options.behavior_out)), exist_ok=True)
            with open(options.behavior_out, 'w', encoding='utf-8') as sink:
                json.dump(behavior_report, sink, indent=2)
        affected = set(behavior_report.get('scope', {}).get('affected', []))
        # Obligations are affected claims, never changed units no claim names: the claim census does not run,
        # so those units stay advisory in the report (doctrine-tool-conformance cut list T2.11).
        obligations = [r for r in behavior_report.get('claims', []) if r.get('claimId') in affected]
        obligations += [dict(u, status=u.get('status', 'uncovered')) for u in plan.get('unresolved', []) if u.get('claimId')]
        if options.behavior_out and behavior_report.get('scope', {}).get('errors'):
            obligations.append({'status': 'uncovered'})
        if any(r.get('status') == 'fail' for r in receipts + obligations):
            behavior_acceptance = 'fail'
            failed += 1
        elif any(r.get('status') != 'pass' for r in receipts + obligations):
            behavior_acceptance = 'uncovered'
            indeterminate.append('<behavior claims>')
    if mode is not None:
        record = options.row if options.row is not None else {}
        record.update({'observations': receipts, 'behavior': behavior_report, 'behaviorAcceptance': behavior_acceptance})
        try:
            head = _git(cache.root if cache else root or REPO_ROOT, ["rev-parse", "HEAD"]).strip()
        except (RuntimeError, OSError):
            head = ""
        record.update({
            "ts": time.time(), "session": os.environ.get("CLAUDE_CODE_SESSION_ID") or None, "mode": mode,
            "head": head, "wall_s": round(wall, 1), "selected": len(selected), "reused": len(reused),
            "queue_s": round(sum(waits.values()), 1),
            "proofs": [{"proof": os.path.basename(p), "status": results[p][0], "run_s": round(results[p][1], 1),
                        "wait_s": round(waits.get(p, 0.0), 1), "reused": p in reused} for p in selected]})
        _record_run(record)
    return passed, failed, indeterminate, wall


def _last_line(detail):
    return ((detail or "").strip().splitlines()[-1:] or [""])[0]


_FAILURE_MARK_RE = re.compile(r"FAIL|ERROR|AssertionError|not ok")
_UNITTEST_SUMMARY_RE = re.compile(r"^FAILED \(")
_VOLATILE_RES = (
    (re.compile(r"(?:[A-Za-z]:)?[\\/][^\s'\"]*(?:harness_\w+|[Tt]emp|tmp)[\\/][^\s'\"]*"), "<path>"),
    (re.compile(r"\bharness_\w+"), "<temp>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b"), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds)\b"), "<time>"),
)


def _checkout_normalized(detail, root):
    if not root:
        return detail or ""
    text = (detail or "").replace("\\\\", "/").replace("\\", "/")
    checkout = root.replace("\\", "/")
    if not re.match(r"[A-Za-z]:/|/", checkout):  # a drive or rooted path is already absolute on any host
        checkout = os.path.abspath(root).replace("\\", "/")
    checkout = checkout.rstrip("/")
    return re.sub(re.escape(checkout) + r"(?=/|['\"\s:]|$)", "<checkout>", text, flags=re.IGNORECASE if os.name == "nt" else 0)


def _failure_lines(detail, root=None):
    """The identity of a red proof: its failure-marked output lines (unittest `FAIL:`/`ERROR:` headers, a
    Traceback's final exception line, any line naming FAIL, ERROR, AssertionError or `not ok`) with
    durations, temp paths and hex ids normalized. The unittest `FAILED (...)` count line is left out: a
    count names no failure. Empty when the output carries no failure line."""
    marked, in_traceback = set(), False
    for raw in _checkout_normalized(detail, root).splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if in_traceback:
            if raw[:1] in (" ", "\t"):
                continue
            in_traceback = False
            marked.add(line)
        elif line.startswith("Traceback (most recent call last)"):
            in_traceback = True
        elif (_FAILURE_MARK_RE.search(line) or FAILURE_LINE.search(line)) and not _UNITTEST_SUMMARY_RE.match(line):
            marked.add(line)
    out = set()
    for line in marked:
        for pattern, token in _VOLATILE_RES:
            line = pattern.sub(token, line)
        out.add(line.strip())
    return out


def _is_pre_existing(worktree_detail, head_detail, worktree_root=None, head_root=None):
    """True when the worktree's failure lines are non-empty and all appear at HEAD. An output with no
    failure line (a count, a timeout) never excuses."""
    failures = _failure_lines(worktree_detail, worktree_root)
    return bool(failures) and failures <= _failure_lines(head_detail, head_root)


def _record_known_red(proof, head, tail_worktree, tail_head):
    row = json.dumps({"ts": time.time(), "proof": proof, "head": head,
                      "tail_worktree": tail_worktree, "tail_head": tail_head})
    log = _beside_stamp(KNOWN_RED_LOG, "harness_known_red.jsonl")
    _append_diagnostic(log, row)


_CHECKOUT_PREFIX = "harness_head_"
_CLOSE_CHECKOUT = "close"


def _reap_abandoned_checkouts(repo_root, env):
    """Remove private checkouts whose close died before its cleanup. Each live checkout holds the lock
    beside it (`<dir>.lock`) for its whole run; a checkout with no lock file is left alone."""
    try:
        listed = subprocess.run(["git", "-C", repo_root, "worktree", "list", "--porcelain"],
                                capture_output=True, text=True, env=env).stdout
    except OSError:
        return
    for line in listed.splitlines():
        path = line[len("worktree "):] if line.startswith("worktree ") else ""
        if not os.path.basename(path).startswith(_CHECKOUT_PREFIX) or not os.path.exists(path + ".lock"):
            continue
        # Without waiting: the first try deletes a dead holder's lock, the second takes it; a live holder refuses both.
        held = _file_lock.acquire(path + ".lock", 0) or _file_lock.acquire(path + ".lock", 0)
        if held is None:
            continue
        try:
            subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", path],
                           capture_output=True, text=True, env=env)
            _remove_temp_root(path)
        finally:
            _file_lock.release(held)


def _admin_gaps(checkout):
    """What a linked checkout's admin dir lacks: git reports a missing admin dir, HEAD or commondir only as
    `not a git repository: (NULL)`, which names neither the checkout nor the missing piece."""
    try:
        with open(os.path.join(checkout, ".git"), encoding="utf-8") as fh:
            admin = fh.read().strip()[len("gitdir: "):]
    except OSError:
        return ""
    missing = [name for name in ("", "HEAD", "commondir", "gitdir") if not os.path.exists(os.path.join(admin, name))]
    return " (admin dir %s lacks: %s)" % (admin, ", ".join(n or "the dir itself" for n in missing)) if missing else ""


@contextmanager
def _revision_checkout(repo_root, rev, proofs=()):
    """Private sparse checkout; copy only never-committed proofs absent at this revision."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_ENV}
    _reap_abandoned_checkouts(repo_root, env)
    tmp = tempfile.mkdtemp(prefix=_CHECKOUT_PREFIX)
    held = _file_lock.acquire(tmp + ".lock", 0)

    def git(*args):
        result = subprocess.run(["git", "-C", tmp] + list(args), capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (args[0], result.stderr.strip()))

    try:
        result = subprocess.run(["git", "-C", repo_root, "worktree", "add", "--detach", "--no-checkout", tmp, rev],
                                capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError("git worktree add failed: %s%s" % (result.stderr.strip(), _admin_gaps(repo_root)))
        git("sparse-checkout", "set", "--cone", ".claude")
        git("checkout", "-q", "-f", rev)
        for path in proofs:
            rel = _norm(os.path.relpath(path, repo_root))
            target = os.path.join(tmp, rel)
            if not os.path.isfile(target) and not _git(repo_root, ["log", "--all", "-1", "--format=%H", "--", rel]).strip():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(path, target)
        yield tmp
    finally:
        subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", tmp],
                       capture_output=True, text=True, env=env)
        _remove_temp_root(tmp)
        _file_lock.release(held)


def _reds_at(repo_root, rev, failing):
    """Failing outputs at `rev`; never-committed proofs run against its inputs. A path a proof prints under
    the comparison checkout reads as the same path under `repo_root`, so the two outputs compare."""
    with _revision_checkout(repo_root, rev, failing) as tmp:
        reds = {}
        for path in failing:
            at_rev = os.path.join(tmp, os.path.relpath(path, repo_root))
            if os.path.isfile(at_rev):
                status, _elapsed, detail = _launch(at_rev, root=tmp)
                if status == "fail":
                    # Preserve the public repo-root projection, including Python's
                    # escaped Windows repr; failure identity normalizes it below.
                    for there, here in ((tmp.replace('\\', '\\\\'), repo_root.replace('\\', '\\\\')),
                                        (tmp, repo_root),
                                        (tmp.replace('\\', '/'), repo_root.replace('\\', '/'))):
                        detail = (detail or '').replace(there, here)
                    reds[path] = detail
        return reds


def _git_soft(repo_root, args):
    result = subprocess.run(["git"] + args, cwd=repo_root, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def push_destination(repo_root, git_env=None):
    """(remote, target branch) of a bare `git push` from `repo_root`, in git's own order: the branch's
    pushRemote, remote.pushDefault, the branch's remote, else origin; the target is the branch's merge
    ref, else the branch itself. The close and the push guard both scope by this remote."""
    from _git_commit import git_environ

    def get(*args):
        result = subprocess.run(["git", "-C", repo_root] + list(args), capture_output=True, text=True,
                                env=git_environ(git_env))
        return result.stdout.strip() if result.returncode == 0 else ""

    branch = get("branch", "--show-current")
    config = (lambda key: get("config", "--get", key % branch)) if branch else (lambda key: "")
    remote = config("branch.%s.pushRemote") or get("config", "--get", "remote.pushDefault") \
        or config("branch.%s.remote") or "origin"
    target = config("branch.%s.merge").replace("refs/heads/", "", 1) or branch or "main"
    return remote, target


def _default_ref(repo_root, remote="origin"):
    """`remote`'s default-branch tracking ref, whose commits a branch's close leaves to the default
    branch; None when neither `<remote>/HEAD` nor `<remote>/main` resolves."""
    for ref in (remote + "/HEAD", remote + "/main"):
        if _git_soft(repo_root, ["rev-parse", "--verify", "-q", ref + "^{commit}"]):
            return ref
    return None


def _default_merge_base(repo_root, head, base, remote="origin"):
    """The merge-base of `head` with `remote`'s default branch when a sync brought commits the close `base`
    lacks, else None: a red the branch inherited from that merge is the default branch's, not the branch's."""
    ref = _default_ref(repo_root, remote)
    found = _git_soft(repo_root, ["merge-base", head, ref]) if ref else ""
    if not found:
        return None
    return found if found != base and _git_soft(repo_root, ["rev-list", "-1", base + ".." + found]) else None


def close_base(repo_root, base=None, *, push_ref=None, head="HEAD", git_env=None, remote="origin"):
    """Explicit base or push destination; an unpublished ref uses `remote`'s default-branch merge-base."""
    from _git_commit import git_environ

    def resolve(args):
        result = subprocess.run(["git", "-C", repo_root] + args, capture_output=True, text=True,
                                env=git_environ(git_env))
        return result.stdout.strip() if result.returncode == 0 else None

    if base:
        resolved = resolve(["rev-parse", "--verify", base])
        if not resolved:
            raise RuntimeError("cannot resolve explicit close base " + base)
        return resolved
    refs = (push_ref,) if push_ref is not None else (
        "@{push}", "@{upstream}", remote + "/" + (resolve(["branch", "--show-current"]) or ""))
    for ref in refs:
        resolved = resolve(["rev-parse", "--verify", ref + "^{commit}"])
        if resolved:
            return resolved
    default = _default_ref(repo_root, remote)
    resolved = resolve(["merge-base", head, default]) if default else None
    if resolved:
        return resolved
    result = subprocess.run(["git", "-C", repo_root, "hash-object", "-w", "-t", "tree", "--stdin"],
                            input=b"", capture_output=True, env=git_environ(git_env))
    if result.returncode:
        raise RuntimeError("git could not create the empty base tree")
    return result.stdout.decode().strip()


def _is_empty_base(repo_root, base):
    return _git(repo_root, ["cat-file", "-t", base]).strip() == "tree"


def close_paths(repo_root, base, head, remote="origin"):
    """Every harness path changed by an outgoing commit the default branch lacks, including later
    reversions. A merge adds only the paths that differ from every parent, so a sync's merged-in
    commits, already closed on the default branch, add none."""
    if _is_empty_base(repo_root, base):
        out = _git(repo_root, ["ls-tree", "-r", "--name-only", head])
    else:
        out = _git(repo_root, ["log", "--format=", "--name-only", "--no-renames", "-c"] + _outgoing(repo_root, base, head, remote))
    return sorted({p for p in out.splitlines() if p.startswith(".claude/") or p in SURFACES})


def _progress(message):
    print("harness_tests: " + message, flush=True)


def _input_commits(repo_root, base, head, reached, remote="origin"):
    revision = [head] if _is_empty_base(repo_root, base) else _outgoing(repo_root, base, head, remote)
    return _git(repo_root, ["rev-list", "--first-parent", "--reverse"] + revision + ["--"] + sorted(reached)).splitlines()


def _outgoing(repo_root, base, head, remote="origin"):
    """The revision range of commits `head` adds over `base` that `remote`'s default branch lacks."""
    ref = _default_ref(repo_root, remote)
    return [base + ".." + head] + (["^" + ref] if ref else [])


def _first_red(repo_root, base, head, proof, detail, reached, *, commits=None):
    """Bisect this proof's first-parent input-changing commits, never the shared checkout."""
    if commits is None:
        commits = _input_commits(repo_root, base, head, reached)
    low, high = 0, len(commits) - 1
    while low < high:
        mid = (low + high) // 2
        red = _reds_at(repo_root, commits[mid], [proof]).get(proof)
        matches = red is not None and _is_pre_existing(detail, red, repo_root, repo_root)
        _progress("bisect %s at %s: %s" % (os.path.basename(proof), commits[mid],
                                         "red" if matches else "not this red"))
        if matches:
            high = mid
        else:
            low = mid + 1
    return commits[low] if commits else None


def _side_commit(repo_root, merge, proof, detail, reached):
    """The commit that introduced the red when the first-parent bisect landed on a merge: bisect the merged-in
    side when the merge's first parent passes and its second parent reproduces the red. Else `merge`."""
    while True:
        parents = _git(repo_root, ["rev-list", "--parents", "-n", "1", merge]).split()[1:]
        if len(parents) < 2:
            return merge
        def reproduces(rev):
            red = _reds_at(repo_root, rev, [proof]).get(proof)
            return red is not None and _is_pre_existing(detail, red, repo_root, repo_root)
        if reproduces(parents[0]) or not reproduces(parents[1]):
            return merge
        side = _git(repo_root, ["rev-list", "--first-parent", "--reverse", parents[1], "^" + parents[0],
                                "--"] + sorted(reached)).splitlines()
        if not side:
            return merge
        found = _first_red(repo_root, None, None, proof, detail, reached, commits=side)
        if found == merge:
            return merge
        merge = found


def close_run_path():
    return _beside_stamp(None, "harness_close_run.json")


_RUNNER_TEMP_PREFIXES = ("harness_proof_", "harness_commit_", "harness_head_")
_MKDTEMP_PREFIX = re.compile(r"""mkdtemp\([^)]*?\bprefix=["']([^"']{4,})["']""")
STALE_TEMP_AGE_S = 86400


def sweep_stale_proof_temp(repo_root, temp_dir=None, max_age_s=STALE_TEMP_AGE_S):
    """Remove stale proof folders from the machine's TEMP; return how many went.

    The runner deletes each proof's own temp root, but a proof run directly (RED-first work) leaves
    its mkdtemp folders in TEMP. A top-level folder qualifies when it is older than `max_age_s` and
    its name starts with a prefix a tracked proof passes to mkdtemp (4+ characters, so a generic
    one never matches) or with one of the runner's own roots.
    """
    temp_dir = temp_dir or tempfile.gettempdir()
    prefixes = set(_RUNNER_TEMP_PREFIXES)
    tests = os.path.join(repo_root, ".claude", "tests")
    try:
        names = os.listdir(tests)
    except OSError:
        names = []
    for name in names:
        if name.endswith((".py", ".sh", ".js")):
            try:
                with open(os.path.join(tests, name), encoding="utf-8", errors="replace") as fh:
                    prefixes.update(_MKDTEMP_PREFIX.findall(fh.read()))
            except OSError:
                continue
    prefixes = tuple(prefixes)
    cutoff = time.time() - max_age_s
    removed = 0
    try:
        entries = list(os.scandir(temp_dir))
    except OSError:
        return 0
    for entry in entries:
        try:
            if not entry.name.startswith(prefixes) or not entry.is_dir(follow_symlinks=False) \
                    or entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
        except OSError:
            continue
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(entry.path, onexc=_chmod_and_retry)
            else:
                shutil.rmtree(entry.path, onerror=lambda f, p, i: _chmod_and_retry(f, p, i[1]))
            removed += 1
        except OSError:
            continue  # in use: the next close takes it
    return removed


def run_close(repo_root, base=None, verbose=False, behavior_out=None, now=False):
    """Wait for this session's writers in this checkout, then prove the committed HEAD of that moment, once.

    A later commit never restarts the run: the private checkout keeps the started commit's results
    valid, and the push publishes that commit. A newer close supersedes this one, which stops at its
    next proof without a verdict. `now` skips the wait: a mid-drive red sweep gets the same
    pre-existing and FIRST RED classification while an author job still runs.
    """
    import watch_records
    swept = sweep_stale_proof_temp(repo_root)
    if swept:
        print("harness_tests: removed %d stale proof temp folder(s)" % swept)
    seen = set()
    token = "%d-%.6f" % (os.getpid(), time.time())
    try:
        update_json_locked(close_run_path(), lambda doc: doc.update(token=token), raising=True)
        update_json_locked(STAMP_PATH, lambda doc: doc.update(close=dict(
            doc.get("close") or {}, verdict="incomplete", ts=time.time())), raising=True)
        while not now:
            if (read_json_salvage(close_run_path()) or {}).get("token") != token:
                print("harness_tests: a newer close superseded this one; it stops without a verdict", flush=True)
                return 2
            writers = watch_records.running_writers(os.environ.get("CLAUDE_CODE_SESSION_ID"), repo=repo_root)
            for writer in writers:
                identity = (writer["name"], tuple(writer["labels"]), writer["model"])
                if identity not in seen:
                    seen.add(identity)
                    print("harness_tests: close waits on %s %s (%s, %.1fs)" % (
                        writer["name"], ", ".join(writer["labels"]), writer["model"], writer["elapsed_s"]), flush=True)
            deadline = _deadline()
            if deadline is not None and time.time() >= deadline:
                raise RuntimeError("HARNESS_DEADLINE passed during close")
            if not writers:
                break
            time.sleep(min(watch_records.POLL_SECONDS, max(deadline - time.time(), 0))
                       if deadline is not None else watch_records.POLL_SECONDS)
        deadline = _deadline()
        if deadline is not None and time.time() >= deadline:
            raise RuntimeError("HARNESS_DEADLINE passed during close")
        return _close_once(repo_root, base, verbose, token, **({"behavior_out": behavior_out} if behavior_out else {}))
    except (RuntimeError, OSError, TimeoutError, ValueError) as exc:
        print("harness_tests: close INCOMPLETE: %s" % exc)
        return 2


_NAMED_FILE_RE = re.compile(r"[\w./-]+\.(?:py|js|json|jsonl|md|sh|txt|tres|tscn)\b")


@contextmanager
def _planted(source, target, rels):
    """Each of `rels` copied from checkout `source` into `target`, or removed from `target` when `source`
    lacks it; `target` is restored on exit."""
    saved, made = {}, []
    try:
        for rel in rels:
            src, dst = (os.path.join(root, *rel.split("/")) for root in (source, target))
            try:
                with open(dst, "rb") as fh:
                    saved[dst] = fh.read()
            except OSError:
                saved[dst] = None
            if os.path.isfile(src):
                folder = os.path.dirname(dst)
                while not os.path.isdir(folder):
                    made.append(folder)
                    folder = os.path.dirname(folder)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
            elif saved[dst] is not None:
                os.remove(dst)
        yield
    finally:
        for dst, data in saved.items():
            try:
                if data is None:
                    if os.path.isfile(dst):
                        os.remove(dst)
                else:
                    with open(dst, "wb") as fh:
                        fh.write(data)
            except OSError:
                pass
        for folder in sorted(made, key=len, reverse=True):
            try:
                os.rmdir(folder)
            except OSError:
                pass


def _uncommitted_inputs(repo_root, snapshot, proof, detail):
    """(needs, tried) for a red proof that passes in the shared checkout; None when it fails there too.
    `tried` are its inputs that are dirty, untracked or ignored there, from the files the failure names
    and then from its reach; each set is planted into the committed-HEAD checkout and the proof reruns.
    `needs` is the first set whose rerun passes, else empty: the two checkouts then differ by something
    else (the checkout's folder, or an input outside the reach), and naming `tried` would send the fix to
    the wrong files."""
    rel = _norm(os.path.relpath(proof, snapshot))
    here = os.path.join(repo_root, *rel.split("/"))
    if not os.path.isfile(here) or _launch(here, root=repo_root)[0] != "pass":
        return None
    cache = _PassCache(repo_root)
    failed_on = set()
    for token in _NAMED_FILE_RE.findall(_checkout_normalized(detail, snapshot)):
        token = _norm(token)
        if ".claude/" in token:
            failed_on.add(token[token.index(".claude/"):])
        else:
            failed_on |= cache.by_base.get(token.rsplit("/", 1)[-1], set())
    tried = set()
    for named in (failed_on - {rel}, cache.reach(here) - {rel}):
        if not named:
            continue
        status = _git(repo_root, ["status", "--porcelain", "--ignored", "--untracked-files=all", "--"] + sorted(named))
        dirty = sorted({_norm(line[3:].strip().split(" -> ")[-1].strip('"'))
                        for line in status.splitlines() if len(line) > 3})
        if dirty and not tried.issuperset(dirty):
            tried.update(dirty)
            with _planted(repo_root, snapshot, dirty):
                if _launch(proof, root=snapshot)[0] == "pass":
                    return dirty, sorted(tried)
    return [], sorted(tried)


def _close_once(repo_root, base=None, verbose=False, token=None, behavior_out=None):
    """Prove the committed HEAD of this moment; 2 without a verdict when a newer close supersedes it."""
    stop = Event()

    def superseded():
        if token and not stop.is_set() and (read_json_salvage(close_run_path()) or {}).get("token") != token:
            stop.set()
        return stop.is_set()

    try:
        head = _git(repo_root, ["rev-parse", "HEAD"]).strip()
        remote, target = push_destination(repo_root)
        base = close_base(repo_root, base, remote=remote)
        paths = close_paths(repo_root, base, head, remote)
        update_json_locked(STAMP_PATH, lambda doc: doc.update(close={
            "head": head, "base": base, "verdict": "incomplete", "red": [], "ts": time.time()}), raising=True)
        proofs = discover(os.path.join(repo_root, ".claude", "tests"))
        with _revision_checkout(repo_root, head, proofs) as snapshot, \
                _policy_from(os.path.join(snapshot, ".claude")):
            roster_error = _roster_error()
            if roster_error:
                raise RuntimeError(roster_error)
            selected = select_for(snapshot, paths)
            if paths and not selected:
                raise RuntimeError("no proof bound to outgoing harness paths")
            statuses, details = {}, {}
            cache = _pass_cache(snapshot, selected)
            row = {}
            _run_selected(selected, verbose, statuses, details,
                          RunOptions(cache, snapshot, "close", row, superseded, paths, base, behavior_out))
            if superseded():
                print("harness_tests: a newer close superseded this one; it stops without a verdict", flush=True)
                return 2
            failing = [p for p in selected if statuses.get(p) == "fail"]
            base_reds = {} if _is_empty_base(repo_root, base) else _reds_at(snapshot, base, failing)
            main_base, main_reds = _default_merge_base(snapshot, head, base, remote), {}
            if main_base:
                main_reds = _reds_at(snapshot, main_base, [p for p in failing if not (
                    p in base_reds and _is_pre_existing(details[p], base_reds[p], snapshot, snapshot))])
            for proof, detail in main_reds.items():
                if _is_pre_existing(details[proof], detail, snapshot, snapshot):
                    print("harness_tests: pre-existing red on the default branch at %s: %s"
                          % (main_base[:8], os.path.basename(proof)))
                    base_reds[proof] = details[proof]
            new_red, old_red = [], []
            candidates = {}
            bound = bindings(snapshot, paths) if failing else {}
            uncommitted, unexplained, reach_of = {}, {}, {}
            for proof in failing:
                if proof in base_reds and _is_pre_existing(details[proof], base_reds[proof], snapshot, snapshot):
                    continue
                found = _uncommitted_inputs(repo_root, snapshot, proof, details[proof])
                if found and found[0]:
                    uncommitted[proof] = found[0]
                    continue
                if found:
                    unexplained[proof] = found[1]
                reach = _PassCache(snapshot).reach(proof) & set(paths)
                reach |= {path for path, proofs in bound.items() if proof in proofs}
                candidates[proof] = _input_commits(snapshot, base, head, reach, remote)
                reach_of[proof] = reach
            if candidates:
                _progress("bisect starts: %d new red, %d candidate commits" % (
                    len(candidates), sum(len(commits) for commits in candidates.values())))
            for proof in failing:
                name = os.path.basename(proof)
                if proof in base_reds and _is_pre_existing(details[proof], base_reds[proof], snapshot, snapshot):
                    print("harness_tests: pre-existing red at base %s: %s" % (base[:8], name))
                    _record_known_red(name, base, _last_line(details[proof]), _last_line(base_reds[proof]))
                    old_red.append(name)
                elif proof in uncommitted:
                    print("NEEDS UNCOMMITTED %s: passes in the working tree, fails at committed HEAD; needs %s; "
                          "commit them or make the proof plant them" % (name, ", ".join(uncommitted[proof])))
                    new_red.append({"proof": name, "commit": None, "detail": details[proof],
                                    "uncommittedInputs": uncommitted[proof]})
                else:
                    red = {"proof": name}
                    if proof in unexplained:
                        tried = unexplained[proof]
                        print("PASSES ONLY IN THE WORKING TREE %s: fails at committed HEAD in a private checkout%s; "
                              "the cause is unknown: the checkout's location, or an input outside its reach"
                              % (name, " even with %s planted" % ", ".join(tried) if tried else ""))
                        red["passesInWorkingTree"] = True
                    first = _first_red(snapshot, base, head, proof, details[proof], (), commits=candidates[proof])
                    if first:
                        first = _side_commit(snapshot, first, proof, details[proof], reach_of[proof])
                    subject = _git(snapshot, ["show", "-s", "--format=%s", first]).strip() if first else "untraced"
                    print("FIRST RED %s at %s %s" % (name, first or "unknown", subject))
                    for trailer in (_git(snapshot, ["show", "-s", "--format=%(trailers:key=Claude-Session,valueonly)",
                                                    first]).split() if first else ()):
                        print("  Claude-Session: " + trailer)
                    red.update(commit=first, detail=details[proof])
                    new_red.append(red)
            incomplete = [os.path.basename(p) for p in selected if statuses.get(p) not in ("pass", "fail")]
            if row.get('behaviorAcceptance') == 'fail':
                new_red.append({'proof': '<behavior claims>', 'detail': row.get('behavior')})
            elif row.get('behaviorAcceptance') == 'uncovered':
                incomplete.append('<behavior claims>')
        if superseded():
            print("harness_tests: a newer close superseded this one; it stops without a verdict", flush=True)
            return 2
        verdict = "red" if new_red else "incomplete" if incomplete else "green"
        result = {"ts": time.time(), "head": head, "base": base, "verdict": verdict,
                  "red": new_red, "preExisting": old_red, "cannotRun": incomplete,
                  "paths": paths, "proofs": [os.path.basename(p) for p in selected],
                  "behavior": row.get('behavior'), "observations": row.get('observations', [])}
        update_json_locked(STAMP_PATH, lambda doc: doc.update(close=result), raising=True)
        dirty_red = sum(1 for red in new_red if red.get("uncommittedInputs"))
        print("harness_tests: close %s: %d paths, %d proofs, %d new red, %d need uncommitted inputs, %d pre-existing red"
              % (verdict, len(paths), len(selected), len(new_red) - dirty_red, dirty_red, len(old_red)))
        print(_budget_line(row))
        if verdict == "green":
            print("harness_tests: push the commit this close proved: git push %s %s:%s" % (remote, head, target))
        return 0 if verdict == "green" else 1 if verdict == "red" else 2
    except (RuntimeError, OSError, TimeoutError, ValueError) as exc:
        print("harness_tests: close INCOMPLETE: %s" % exc)
        return 2


def _roster_error():
    """A proof in both proof_heavy and proof_excluded has two rules, so no stamping run may start."""
    both = sorted(set(HEAVY) & set(EXCLUDED))
    if both:
        return "proof_heavy and proof_excluded both name %s; keep each proof in one table" % ", ".join(both)
    return None


def _overlay_digests(entries, repo_root, paths):
    for path in paths:
        full = os.path.join(repo_root, path)
        if os.path.isfile(full):
            with open(full, "rb") as source:
                entries[path] = hashlib.sha256(source.read()).hexdigest()


def run_scoped(repo_root, touched, verbose=False, mode="for", base=None, behavior_out=None):
    """Run bound proofs on demand and retain per-file results, not push coverage.

    Reds whose non-empty failure lines all appear at the comparison revision are reported as
    pre-existing. That revision is `base` when given, else HEAD while a given path or selected
    proof differs from it. With all of them committed, HEAD carries the change, so no red is
    excused without `base`. A never-committed proof absent there runs its working copy against its inputs. New
    failure lines, unavailable coverage and changed inputs block; HARNESS_NO_HEAD_RERUN skips comparison.
    """
    if base:
        try:
            base = _git(repo_root, ["rev-parse", "--verify", base + "^{commit}"]).strip()
        except RuntimeError as exc:
            print("harness_tests: --base does not name a commit: %s" % exc)
            return 2
    roster_error = _roster_error()
    if roster_error:
        print("harness_tests: " + roster_error)
        return 2
    tested_entries = tree_entries(repo_root)
    tested_head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    touched = [_norm(t) for t in touched]
    _overlay_digests(tested_entries, repo_root, touched)
    in_scope = list(dict.fromkeys(touched))
    if not in_scope:
        print("harness_tests: none of the given paths is a stamped harness input: %s" % ", ".join(touched))
        return 1
    bound = bindings(repo_root, in_scope)
    union = {p for paths in bound.values() for p in paths}
    union.update(os.path.join(repo_root, *rel.split('/')) for rel in _behavior_plan(repo_root, in_scope)['proofs'])
    selected = [p for p in discover(os.path.join(repo_root, ".claude", "tests")) if p in union]
    if not selected:
        print("harness_tests: no proof is bound to %s; add test_<name>*.py or run the full battery" % ", ".join(in_scope))
        return 1
    statuses, details = {}, {}
    passed, failed, indeterminate, elapsed_total = _run_selected(
        selected, verbose, statuses, details, RunOptions(_pass_cache(repo_root, selected), repo=repo_root,
            mode=mode, paths=in_scope, base=base or 'HEAD', behavior_out=behavior_out))
    failing = [p for p in selected if statuses.get(p) == "fail"]
    skipped = [p for p in indeterminate if statuses.get(p) == "skipped"]
    compare, label = (base, "base") if base else (tested_head, "HEAD")
    # HEAD is a "before" only while a tracked input or selected proof differs from it.
    # A never-committed proof alone does not make HEAD precede the change under test.
    carried = bool(not base and tested_head and not _git(
        repo_root, ["status", "--porcelain", "--untracked-files=no", "--"] + in_scope + selected).strip())
    if failing and os.environ.get(NO_HEAD_RERUN_ENV):
        print("harness_tests: HEAD rerun skipped (%s), every red proof keeps blocking; run --for in the "
              "background to excuse a pre-existing red" % NO_HEAD_RERUN_ENV)
    elif failing and carried:
        print("harness_tests: the given paths are committed at HEAD %s, so HEAD carries the change and excuses "
              "no red; pass --base <the revision before the change> to compare there" % tested_head[:8])
    elif failing and compare:
        try:
            for path, head_detail in _reds_at(repo_root, compare, failing).items():
                tail_worktree = _last_line(details.get(path))
                tail_head = _last_line(head_detail)
                if not _is_pre_existing(details.get(path), head_detail, repo_root, repo_root):
                    print("harness_tests: %s is red at %s %s too, but fails differently or shows no failure "
                          "line; it keeps blocking"
                          "\n    worktree: %s\n    %s: %s"
                          % (os.path.basename(path), label, compare[:8], tail_worktree, label, tail_head))
                    continue
                print("harness_tests: pre-existing red at %s %s: %s\n    worktree: %s\n    %s: %s"
                      % (label, compare[:8], os.path.basename(path), tail_worktree, label, tail_head))
                _record_known_red(os.path.basename(path), compare, tail_worktree, tail_head)
                statuses[path] = "pre-existing-red"
                failed -= 1
        except (RuntimeError, OSError) as exc:
            print("harness_tests: %s checkout failed, every red proof keeps blocking: %s" % (label, exc))
    elif failing:
        print("harness_tests: HEAD unknown, every red proof keeps blocking")
    print("harness_tests: scoped to %d input(s): %d run, %d pass, %d fail, %d cannot-run, %.1fs wall, %d jobs"
          % (len(in_scope), len(selected), passed, failed, len(indeterminate), elapsed_total, _jobs()))
    blocked = {}
    for t in in_scope:
        red = [os.path.basename(p) for p in bound[t] if statuses.get(p) not in ("pass", "pre-existing-red")]
        if red:
            blocked[t] = red
            print("harness_tests: unstamped %s: blocked by %s" % (t, ", ".join(red)))
    if skipped:
        print("harness_tests: HARNESS_DEADLINE passed before %d proof(s) ran; their inputs stay unstamped. "
              "Passes so far are cached, so a rerun starts where this one stopped" % len(skipped))
    if len(indeterminate) > len(skipped):
        print("harness_tests: INCOMPLETE; inputs bound to a cannot-run proof stay unstamped")
    current_entries = tree_entries(repo_root)
    _overlay_digests(current_entries, repo_root, in_scope)
    moved = [t for t in in_scope if t not in blocked and tested_entries.get(t) != current_entries.get(t)]
    if moved:
        print("harness_tests: input(s) changed during the scoped run stay unstamped: " + ", ".join(moved))
    rc = 1 if failed or moved else 2 if len(indeterminate) > len(skipped) else 3 if skipped else 0
    in_scope = [t for t in in_scope if t not in blocked and t not in moved]
    if not in_scope:
        return rc
    def merge(doc):
        # Re-read under the lock: a run that overlapped this one stamped its own inputs meanwhile,
        # and writing back a stamp read at start would erase them.
        files = dict(doc.get("files") or {})
        for t in in_scope:
            if t in tested_entries:
                files[t] = tested_entries[t]
            else:
                files.pop(t, None)
        blob = json.dumps(sorted(files.items())).encode("utf-8")
        # A scoped run into a base stamp extends the full run's claim; a standalone one records only
        # its own counts, so a missing key never reads as a full battery that covered more.
        doc.setdefault("run", len(selected))
        doc.setdefault("pass", passed)
        doc.setdefault("excluded", [])
        doc.setdefault("cannotRun", [])
        doc["files"] = files
        doc["tree_hash"] = hashlib.sha256(blob).hexdigest()
        doc["ts"] = time.time()
        doc["scoped"] = {"ts": time.time(), "head": tested_head, "paths": in_scope,
                         "proofs": [os.path.basename(p) for p in selected]}

    try:
        update_json_locked(STAMP_PATH, merge, raising=True)
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        print("harness_tests: stamp not written: %s" % exc)
        return 1
    print("harness_tests: scoped stamp refreshed for %d input(s) (%d proofs)" % (len(in_scope), len(selected)))
    return rc


def _heavy_bound(repo_root, entries, heavy_proofs):
    """The inputs among `entries` that `bindings` binds to any of `heavy_proofs`: the one binding
    relation, read in the reverse direction."""
    if not heavy_proofs:
        return set()
    bound = bindings(repo_root, list(entries), include_excluded=True, proofs=heavy_proofs)
    return {path for path, proofs in bound.items() if proofs}


def run_all(repo_root, include_excluded=False, verbose=False, behavior_out=None):
    tested_entries = tree_entries(repo_root)
    tested_head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir)
    present = {os.path.basename(p) for p in discovered}

    # `.claude/tests/` is gitignored, so a worktree or sparse checkout lacks the excluded
    # proofs. An absent entry is reported and skipped, never a FAIL that stops discovery:
    # git cannot tell "untracked file not in this checkout" from "file deleted".
    absent = sorted(name for name in EXCLUDED if name not in present)
    for name in absent:
        print("SKIP absent EXCLUDED entry: %s (%s)" % (name, EXCLUDED[name]))
    for name in sorted(name for name in HEAVY if name not in present):
        print("SKIP absent HEAVY entry: %s (%s)" % (name, HEAVY[name]))
    roster_error = _roster_error()
    if roster_error:
        print("harness_tests: " + roster_error)
        return 2

    heavy_skipped = []
    if include_excluded:
        selected = discovered
        excluded_list = []
    else:
        selected = [p for p in discovered if os.path.basename(p) not in EXCLUDED]
        excluded_list = sorted(
            os.path.basename(p) for p in discovered if os.path.basename(p) in EXCLUDED
        )
        heavy_skipped = [p for p in selected if os.path.basename(p) in HEAVY]
        selected = [p for p in selected if os.path.basename(p) not in HEAVY]
        for p in heavy_skipped:
            print("SKIP heavy: %s (%s)" % (os.path.basename(p), HEAVY[os.path.basename(p)]))

    if not selected:
        print("FAIL no proofs to run: %d discovered under %s (patterns %s), %d excluded"
              % (len(discovered), tests_dir, ", ".join(_PATTERNS), len(excluded_list)))
        print("harness_tests: 0 run, 0 pass, 0 fail, 0 cannot-run, %d excluded, %d heavy skipped, 0.0s"
              % (len(excluded_list), len(heavy_skipped)))
        return 1

    statuses = {}
    passed, failed, indeterminate, elapsed_total = _run_selected(
        selected, verbose, statuses, options=RunOptions(repo=repo_root, mode="all", behavior_out=behavior_out))
    skipped = [p for p in indeterminate if statuses.get(p) == "skipped"]
    indeterminate = [p for p in indeterminate if statuses.get(p) != "skipped"]

    print(
        "harness_tests: %d run, %d pass, %d fail, %d cannot-run, %d excluded, %d heavy skipped, %.1fs wall, %d jobs"
        % (len(selected), passed, failed, len(indeterminate), len(excluded_list), len(heavy_skipped), elapsed_total,
           _jobs())
    )
    flaky = _flaky_history()
    if flaky:
        # A FLAKY pass still stamps, so the history is printed on every full run until the proof is fixed.
        print("harness_tests: flaky in the last 30 days (fix the proof or add it to proof_serial): "
              + ", ".join("%s x%d" % (p, n) for p, n in sorted(flaky.items(), key=lambda kv: (-kv[1], kv[0]))))
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
    if indeterminate:
        print("harness_tests: INCOMPLETE; no passing stamp written")
        return 2
    if skipped:
        print("harness_tests: HARNESS_DEADLINE passed before %d proof(s) ran; no passing stamp written. "
              "Passes so far are cached, so a rerun starts where this one stopped" % len(skipped))
        return 3

    # A HEAD move is not a verdict: tree_entries hashes working-tree bytes, so a peer commit that touched a
    # stamped input shows up below as a changed file, and one that touched only docs, ledgers or benchmark
    # results changes nothing the stamp certifies. The stamp records the tested HEAD; the commit guard judges
    # per touched file. (2026-09-15: three green batteries, 20 minutes, discarded by a head-equality gate.)
    current_entries = tree_entries(repo_root)
    changed = sorted(path for path in tested_entries.keys() | current_entries.keys()
                     if tested_entries.get(path) != current_entries.get(path))
    unproven = _heavy_bound(repo_root, tested_entries, heavy_skipped)
    if unproven:
        print("harness_tests: %d input(s) bound to a heavy proof that did not pass stay unstamped"
              % len(unproven))
    stamped = {path: digest for path, digest in tested_entries.items()
               if path not in changed and path not in unproven}
    if changed:
        # A changed source is never certified and the run is not green (exit 1). The stamp is per file and the commit
        # guard judges only the paths a commit touches, so the UNCHANGED files keep their proof: a peer's edit
        # mid-run stales that file alone, exactly as the same edit one second after the run would.
        print("harness_tests: %d input(s) changed during verification; they stay unstamped:" % len(changed))
        for path in changed[:20]:
            print("    - " + path)
        if len(changed) > 20:
            print("    ... %d more changed inputs" % (len(changed) - 20))
        if stamped:
            _write_stamp(repo_root, len(selected), passed, excluded_list, indeterminate,
                         entries=stamped, head=tested_head, withheld=unproven)
            print("harness_tests: partial stamp written for the %d unchanged input(s)" % len(stamped))
        return 1
    _write_stamp(repo_root, len(selected), passed, excluded_list, indeterminate,
                 entries=stamped, head=tested_head, withheld=unproven)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Run harness proofs and stamp unchanged passing inputs.")
    parser.add_argument("--repo", default=REPO_ROOT, help="repository containing the proofs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="include normally excluded proofs")
    mode.add_argument("--tiers", action="store_true",
                      help="read-only: print each proof's recorded seconds, budget tier, how it runs and its trigger")
    mode.add_argument("--for", dest="for_paths", nargs="+", metavar="PATH",
                      help="scoped: run only the proofs bound to these harness files and refresh their stamp entries")
    mode.add_argument("--commit", dest="commit_paths", nargs="+", metavar="PATH",
                      help=argparse.SUPPRESS)
    mode.add_argument("--proofs-for", dest="proofs_for_paths", nargs="+", metavar="PATH",
                      help="run only proofs bound to these paths without reading or writing a stamp")
    parser.add_argument('--behavior-report', metavar='OUT', help='write typed claim results and named-case observations')
    parser.add_argument("--verbose", action="store_true", help="print every proof result, not only failures and summary")
    mode.add_argument("--close", action="store_true", help="prove outgoing harness changes at committed HEAD")
    parser.add_argument("--now", action="store_true",
                        help="with --close, start without waiting for this session's author and fold dispatches")
    parser.add_argument("--base", help="upstream revision for --close; for --for, the revision before "
                                       "the change that a red must also fail at to be pre-existing")
    parser.add_argument("--json", action="store_true", help="with --tiers, print the rows as JSON")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.base and not (args.close or args.for_paths):
        parser.error("--base requires --close or --for")
    if args.json and not args.tiers:
        parser.error("--json requires --tiers")
    if args.now and not args.close:
        parser.error("--now requires --close")
    if args.commit_paths:
        print(json.dumps(run_commit(args.repo, args.commit_paths)))
        return 0
    if args.tiers:
        rows = tiers(args.repo)
        print(json.dumps(rows, indent=2) if args.json else format_tiers(rows))
        return 0
    if args.close:
        extra = dict(behavior_out=args.behavior_report) if args.behavior_report else {}
        return run_close(args.repo, args.base, args.verbose, now=args.now, **extra)
    if args.proofs_for_paths:
        selected = select_for(args.repo, args.proofs_for_paths)
        if not selected:
            print("harness_tests: no proof selected for %s" % ", ".join(args.proofs_for_paths))
            return 1
        statuses = {}
        passed, failed, indeterminate, elapsed_total = _run_selected(
            selected, verbose=True, statuses=statuses,
            options=RunOptions(_pass_cache(args.repo, selected), repo=args.repo, mode="proofs-for",
                               paths=args.proofs_for_paths, behavior_out=args.behavior_report))
        skipped = [p for p in indeterminate if statuses.get(p) == "skipped"]
        indeterminate = [p for p in indeterminate if statuses.get(p) != "skipped"]
        print("harness_tests: scoped: %d run, %d pass, %d fail, %d cannot-run, %d skipped, %.1fs wall, %d jobs"
              % (len(selected), passed, failed, len(indeterminate), len(skipped), elapsed_total, _jobs()))
        if failed:
            return 1
        if skipped and not indeterminate:
            print("harness_tests: HARNESS_DEADLINE passed before %d proof(s) ran" % len(skipped))
            return 3
        if indeterminate:
            print("cannot-run (exit 2: the proof could not bind its target; the scoped run is incomplete):")
            for proof in indeterminate:
                print("    - %s" % proof)
            return 2
        return 0
    if args.for_paths:
        return run_scoped(args.repo, args.for_paths, verbose=args.verbose, base=args.base,
                          behavior_out=args.behavior_report)
    return run_all(
        args.repo,
        include_excluded=args.all,
        verbose=args.verbose,
        **({'behavior_out': args.behavior_report} if args.behavior_report else {}),
    )


if __name__ == "__main__":
    sys.exit(main())
