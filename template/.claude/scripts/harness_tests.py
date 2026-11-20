#!/usr/bin/env python3
"""Run harness proofs; --close proves committed HEAD before push; commits only receive budgeted advice.

    python3 .claude/scripts/harness_tests.py --close [--now] [--base REV] [--repo PATH]
    python3 .claude/scripts/harness_tests.py --close --range-start REV [--also PROOF ...] [--now] [--repo PATH]
    python3 .claude/scripts/harness_tests.py --for PATH [PATH ...]
    python3 .claude/scripts/harness_tests.py --proofs-for PATH [PATH ...]
    python3 .claude/scripts/harness_tests.py --all
    python3 .claude/scripts/harness_tests.py --tiers [--json]

Close selects proofs bound to every harness path the outgoing commits change that the push remote's
default branch does not already carry (`push_destination`, `close_paths`). It runs on a
private committed-HEAD checkout, compares reds at the base, and bisects each new red through that
proof's input-changing commits. Its close {head, base, verdict, red} record gates the push of that
head. `--range-start REV` closes the commits from REV to HEAD that the default branch already holds (the main check): REV is the base,
ahead of the upstream and default-branch lookups, and nothing the default branch carries is subtracted. A ranged
close runs every proof: a red stops no other retry, and a proof whose timeout repeats alone is a red no bisect
runs on. `--also PROOF` adds a proof to a ranged close's selection and runs it once, with no retry. Close waits for
this session's author/fold dispatches writing in its checkout unless --now, then proves the HEAD of that
moment once; a later commit never restarts it, and a newer close supersedes it.
Never-committed proofs absent at the compared revision run their
working copy against that revision's inputs; tracked proofs absent there are not excused.

Scoped modes run bound proofs on demand and retain their legacy per-file result entries; these
entries satisfy no commit or push gate. --proofs-for is the no-stamp variant. --all includes excluded
and heavy proofs; the default battery skips heavy proofs. A cannot-run result (exit 2) is missing
coverage, never green. --tiers is read-only: one row per proof with its recorded seconds, budget tier, how it runs and its trigger. Every scoped or full run appends a row to
harness_runs.json beside the stamp (mode, wall and queue seconds, each proof's run and wait seconds); the newest
200 rows stay whole, and each older one folds into a per-day total under `folded` (`_fold_runs`).
A close ends with `tools/gate_growth.py`'s lines over that history: the proofs above their recorded time
range (kept open in `growth_path()`, which a commit touching one of them reads) and why each rerun proof
missed reuse. It starts by removing orphaned `.tmp_*.json` writes from the shared stores' folder.

Scoped and close runs reuse passes while every reached input remains unchanged, from any checkout of
the repository (`passes_path`). Runners share
HARNESS_MACHINE_SLOTS (unset: the recorded width, else 4) in machine_slots.py's shared pool. HARNESS_DEADLINE stops new proofs
and leaves the run incomplete (exit 3). rules/harness_tooling.md owns cadence and RED-first work.
"""

import argparse
import ast
import base64
from contextlib import contextmanager, nullcontext, redirect_stdout
import functools
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
import symtable
import sys
import tempfile
import time
import tokenize
import urllib.parse
import zlib
from threading import Event
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_SCRIPTS_DIR)
REPO_ROOT = os.path.dirname(_CLAUDE_DIR)
_RECORDER_DIR = os.path.join(_SCRIPTS_DIR, "_read_recorder")
_NODE_RECORDER = os.path.join(_RECORDER_DIR, "node_recorder.js")
_PROGRESS_MIN, _PROGRESS_EVERY = 25, 25  # a scoped commit run stays quiet; a close reports about 20 lines

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
from _hook_state import update_json_locked, read_json_salvage, replace_path, HARNESS_DIRS as DIRS, \
    SETUP_SURFACE_FILES as SURFACES, CODE_EXT as _CODE_EXT, \
    is_harness_code as _is_code  # noqa: E402
import _file_lock  # noqa: E402
from _bounded_run import ProcessTree  # noqa: E402

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "tools"))
import adaptation  # noqa: E402
import machine_slots as _slot_pool  # noqa: E402

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
GROWTH_PATH = None
_RUNS_KEPT = 200
_FOLDED_KEPT = 800  # per-day entries of `harness_runs.json`'s `folded` history (about 150 bytes each; two years of days)
COMMIT_BUDGET_S, _PROOF_BUDGET_S = 30, 10


def _beside_stamp(override, name):
    return override or os.path.join(os.path.dirname(os.path.abspath(STAMP_PATH)), name)


def _shared_path(claude_dir, name):
    """`harness_<name>` as every checkout of `claude_dir`'s repository shares it, under its common git dir;
    beside the stamp when git cannot name that dir. The pass cache lives here because its rows key on input
    bytes, so a pass recorded in one worktree holds in any other whose reached inputs match."""
    try:
        result = subprocess.run(["git", "-C", claude_dir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True)
        common = result.stdout.strip() if result.returncode == 0 else ""
    except OSError:
        common = ""
    return (os.path.join(common, "harness", name) if common
            else os.path.join(os.path.dirname(os.path.abspath(_DEFAULT_STAMP)), "harness_" + name))


def _shared_or_beside(override, name):
    """`override`, else the shared file while the stamp is at its default home, else beside an overridden
    stamp (a proof's private run)."""
    if override:
        return override
    if os.path.normcase(os.path.abspath(STAMP_PATH)) == os.path.normcase(os.path.abspath(_DEFAULT_STAMP)):
        return _shared_path(_CLAUDE_DIR, name)
    return _beside_stamp(None, "harness_" + name)


def passes_path():
    return _shared_or_beside(PASSES_PATH, "proof_passes.json")


def case_passes_path():
    """Beside the pass store, wherever it moved: one pass row per `_case_pool` case (`_PassCache.record_cases`)."""
    base, ext = os.path.splitext(passes_path())
    return base + "_cases" + ext


def pass_store_dir():
    """The pass store: a folder with one file per proof (`_store_file`). `passes_path()` names the one-file store
    it replaced, which `_migrate_legacy_store` still moves in."""
    return os.path.splitext(passes_path())[0]


def case_store_dir():
    """The case store, laid out like `pass_store_dir()`."""
    return os.path.splitext(case_passes_path())[0]


def _store_file(store_dir, rel):
    """Where the store at `store_dir` keeps the rows of the proof `rel` (a `/` path under the repository)."""
    return os.path.join(store_dir, rel.replace("/", "~") + ".json")


def runs_path():
    return _beside_stamp(RUNS_PATH, "harness_runs.json")


def growth_path():
    """Shared like the pass cache: `tools/gate_growth.py`'s open growth entries, which every checkout's commit reads."""
    return _shared_or_beside(GROWTH_PATH, "gate_growth.json")


def durations_path():
    """Shared like the pass cache: a recorded duration admits a failing proof to its alone retry, and a
    checkout that only reused another checkout's pass records none."""
    return _shared_or_beside(DURATIONS_PATH, "proof_durations.json")


_PROOF_EXTS = (".py", ".js", ".mjs", ".sh", ".ps1")
_PATTERNS = tuple(form % ext for ext in _PROOF_EXTS for form in ("test_*%s", "*_test%s"))
_TIMEOUT_SEC = 120
# A proof's timeout is its last whole pass times `_LOAD_FACTOR` (never under the default). A
# `proof_timeouts` entry in adaptation.json overrides it for the proof it names.
_PROOF_TIMEOUTS = {}
_LOAD_FACTOR = 3


def _recorded_time(path, known):
    """A proof's recorded seconds: its last whole pass (`known`, the durations file's `__last_passes__` receipt),
    else its fastest pass; None when it has neither. The last pass carries the load the proof meets; the
    fastest, which growth reads, carries none."""
    name = os.path.basename(path)
    receipts = known.get("__last_passes__")
    receipt = receipts.get(name) if isinstance(receipts, dict) else None
    for recorded in (receipt.get("run_s") if isinstance(receipt, dict) else None, known.get(name)):
        if isinstance(recorded, (int, float)) and not isinstance(recorded, bool) and recorded > 0:
            return float(recorded)
    return None


def _timeout_for(path, known):
    """A proof's timeout at recorded pace: its `proof_timeouts` cap, else the cap of the proof it is a part of
    (`_part_main`), else `_LOAD_FACTOR` times its `_recorded_time` when that beats `_TIMEOUT_SEC`, else
    `_TIMEOUT_SEC`. While the proof runs, the run's `_PACE` stretches it."""
    name = os.path.basename(path)
    if name in _PROOF_TIMEOUTS:
        return _PROOF_TIMEOUTS[name]
    main = _part_main(path)
    if main and os.path.basename(main) in _PROOF_TIMEOUTS:
        return _PROOF_TIMEOUTS[os.path.basename(main)]
    recorded = _recorded_time(path, known)
    return max(_TIMEOUT_SEC, _LOAD_FACTOR * recorded) if recorded else _TIMEOUT_SEC


class _LivePace:
    """How much slower than recorded this run's passing proofs ran: the median of elapsed over recorded seconds,
    never below 1. A proof's deadline stretches by it while the proof runs, so load every proof on the machine
    shares never times one out, and a proof slower than its peers still does."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ratios = []

    def reset(self):
        with self._lock:
            self._ratios = []

    def observe(self, elapsed, recorded):
        if recorded and recorded > 0 and elapsed > 0:
            with self._lock:
                self._ratios.append(elapsed / recorded)

    def factor(self):
        with self._lock:
            ratios = sorted(self._ratios)
        if not ratios:
            return 1.0
        mid = len(ratios) // 2
        return max(1.0, ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2)


_PACE = _LivePace()


def _retry_timeout(path, known, timed_out):
    """An alone retry's timeout: `_timeout_for`, times `_LOAD_FACTOR` after a timeout unless a cap names the proof."""
    capped = os.path.basename(path) in _PROOF_TIMEOUTS
    return _timeout_for(path, known) * (_LOAD_FACTOR if timed_out and not capped else 1)


def _launch_options(path, root, behavior_root, cases, receipts, keyed, reusable):
    """`_launch`'s keywords for one proof `_run_selected` runs: the checkout it runs in, its behavior `cases` with
    their `receipts` sink (run from `behavior_root`), the read recorder's sink for a `keyed` Python or JS proof, and
    for such a Python proof with no behavior cases, `reusable()`, the `_case_pool` cases it skips."""
    options = {"root": root} if root else {}
    if cases:
        options.update(behavior_cases=cases, receipt_sink=receipts, root=behavior_root)
    if keyed and path.endswith((".py", ".js")):
        options["reads_sink"] = {}
        if path.endswith(".py") and not cases:
            options["reuse_cases"] = reusable
    return options


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

# {proof basename: directories it scans whole}, a project's own entries beside `_CORPUS_SCANNING`. A directory
# outside .claude joins the close's sparse checkout, so a proof that reads project data finds it there.
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


_TOP_TEST_RE = re.compile(r"^(?:async\s+)?def\s+(test\w*)\s*\(\s*(\S?)", re.MULTILINE)
_PYTEST_HANDOFF_RE = re.compile(r"pytest\.main\(\s*\[\s*__file__")
_GLOBALS_COLLECTOR_RE = re.compile(r"globals\(\)[\s\S]{0,200}?startswith\(\s*['\"]test_")


def _pytest_cases(path):
    """The pytest node ids a run of the proof needs: the whole file for a proof with top-level pytest cases and no
    `__main__` block (`python <file>` would define its cases and run none); for a proof with one, each top-level
    `test*` function its `main` cannot reach. A `main` reaches every case when it hands the file to
    `pytest.main`, each no-argument case when it collects `test_` names from `globals()`, and otherwise only the
    cases the file names outside their own `def` line."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    if not _MAIN_GUARD_RE.search(text):
        return [path] if _PYTEST_CASE_RE.search(text) else []
    if _PYTEST_HANDOFF_RE.search(text):
        return []
    collector = bool(_GLOBALS_COLLECTOR_RE.search(text))
    return [path + "::" + name for name, first in _TOP_TEST_RE.findall(text)
            if len(re.findall(r"\b%s\b" % name, text)) == 1 and not (collector and first == ")")]


def _runner_cmds(path):
    """The commands that run the proof, in order; the first failing one decides its result."""
    if path.endswith(".py"):
        cases = _pytest_cases(path)
        # Without a confcutdir, a proof outside the cwd has pytest walk conftests through their common ancestor.
        pytest = [[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   "--confcutdir=" + os.path.dirname(os.path.abspath(path))] + cases] if cases else []
        return pytest if cases == [path] else [[sys.executable, path]] + pytest
    return [_runner_cmd(path)]


def _runner_cmd(path):
    if path.endswith(".py"):
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


def _detail_excerpt(detail):
    """A proof's output as printed: its failure-marked lines above the tail, each with the deeper-indented
    lines under it (the detail a case prints beside its FAIL), then the last `_DETAIL_LINES`."""
    lines = detail.strip().splitlines()
    head, tail = lines[:-_DETAIL_LINES], lines[-_DETAIL_LINES:]
    kept, marked_indent = [], None
    for line in head:
        indent = len(line) - len(line.lstrip())
        if FAILURE_LINE.search(line.strip()):
            kept.append(line)
            marked_indent = indent
        elif marked_indent is not None and line.strip() and indent > marked_indent:
            kept.append(line)
        else:
            marked_indent = None
    return kept + tail


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
# The local variables whose value is no path: command-scope config (the runner's core.fscache comes as
# GIT_CONFIG_COUNT=1 on every Windows proof), switches and a ref prefix. Only a path names a repository; a name
# git adds later is read as one.
_GIT_NON_PATH_ENV = frozenset({"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_IMPLICIT_WORK_TREE",
                               "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX"})
STAMP_INDEX_ENV = "HARNESS_STAMP_INDEX"


# Windows GetTempPath reads TMP first, Python's tempfile TMPDIR first, POSIX tools TMPDIR.
_TEMP_ENV = ("TMP", "TEMP", "TMPDIR")
_CALLER_IDENTITY_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_PROMPT_FILE",
                        "CLAUDE_CODE_SIDECAR_LABEL", "SIDECAR_PARENT_SESSION_ID")
_CALLER_RUN_ENV = ("HARNESS_DEADLINE", "HARNESS_NO_PASS_CACHE", "HARNESS_REUSE_PASSES", "HARNESS_TEST_JOBS",
                   "HARNESS_MACHINE_SLOTS", "HARNESS_CALLER_HOME")
# Every run, local or main check, gives each proof what a clean runner has and a session does not, so a proof that
# reads the ambient environment undeclared goes red at its writer's run, and a local pass answers the main check's
# run of the same inputs. `_RUNNER_CLAUDE_DECLARED` is every `CLAUDE_*` name a proof may rely on, beside
# `CLAUDE_CODE_EXECPATH`, which `run_proof` sets where the machine has the binary.
_RUNNER_CLAUDE_DECLARED = ("CLAUDE_PROJECT_DIR",)
_RUNNER_HOME_ENV = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME")


def _land_path(value):
    """`value` without the folders Git Bash prepends on Windows (its install's `usr/bin`, `usr/local/bin`,
    `mingw64/bin` and `bin`, and `~/bin`). The land runner starts proofs from pwsh, whose PATH holds none of
    them, so a proof that needs a Git tool by bare name fails there; without them it fails in a session too."""
    if os.name != "nt":
        return value
    bash = _bash_exe()
    git_root = os.path.dirname(os.path.dirname(bash)) if os.path.isabs(bash) else None
    shell = {os.path.normcase(os.path.normpath(os.path.join(git_root, *parts)))
             for parts in (("usr", "bin"), ("usr", "local", "bin"), ("mingw64", "bin"), ("bin",))} if git_root else set()
    shell.add(os.path.normcase(os.path.normpath(os.path.join(os.path.expanduser("~"), "bin"))))
    return os.pathsep.join(entry for entry in value.split(os.pathsep)
                           if entry and os.path.normcase(os.path.normpath(entry)) not in shell)


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


_STRETCH_RECHECK_S = 5  # how stale a stretched deadline may get; proofs run for seconds to minutes


def _run_child(cmd, env, timeout, out_path, cwd=None, stretch=None):
    """(returncode, output), returncode None on timeout. Output goes to a file, never a pipe: a
    grandchild that outlives the proof keeps a pipe open past the timeout. Every process the proof
    started ends with it, on timeout or not, so none keeps writing into the next proof's world.
    `stretch()`, read again while the child runs, multiplies `timeout`: a proof's deadline follows the load
    the run measures (`_LivePace`), while a fixed budget passes none."""
    with open(out_path, "wb") as sink:
        proc = subprocess.Popen(cmd, stdout=sink, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                                start_new_session=(os.name != "nt"))
        tree = ProcessTree(proc)
        began = time.monotonic()
        try:
            while True:
                left = None if timeout is None else began + timeout * (stretch() if stretch else 1) - time.monotonic()
                if left is not None and left <= 0:
                    returncode = None
                    break
                try:
                    returncode = proc.wait(timeout=left if left is None or not stretch
                                           else min(left, _STRETCH_RECHECK_S))
                    break
                except subprocess.TimeoutExpired:
                    continue
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
        if file_status in ("cannot-run", "skipped", "platform-skip") or "TIMEOUT" in (output or ""):
            status = "uncovered"
        observations.append({"proof": _norm(os.path.relpath(path, REPO_ROOT)), "caseId": cid,
                             "status": status, "inputKey": case["inputKey"],
                             "evidence": [line for _, line in seen] or ["named case not observed"]})
    return observations


def _read_events(log_dir):
    """The recorder's events from every process that logged directly into `log_dir`, and from every runner those
    processes ran (`_nested_log`), in no order; an unreadable line is dropped, and a process that never wrote one
    left no events."""
    events = []
    nested = os.path.join(log_dir, "nested")
    for name in os.listdir(nested) if os.path.isdir(nested) else []:
        inner = os.path.join(nested, name)
        events += _read_events(inner) + [e for case in _case_events(inner).values() for e in case]
    for name in os.listdir(log_dir) if os.path.isdir(log_dir) else []:
        if not os.path.isfile(os.path.join(log_dir, name)):
            continue
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


def _nested_log(temp_root):
    """(read log folder, recorder folder) for a proof's processes. A runner that is itself a proof's child logs under
    the read log this process records into and puts the outer recorder (`HARNESS_READ_RECORDER`) on the proof's
    path: the outer recorder then follows the nested run's processes (it counts a child as followed by its recorder's
    folder, and the close runs the proof's runner from another checkout than the outer runner), and `_read_events`
    returns their events with the outer proof's. Otherwise: the proof's own temp root and this runner's recorder."""
    outer, recorder = os.environ.get("HARNESS_READ_LOG"), os.environ.get("HARNESS_READ_RECORDER")
    if not outer:
        return os.path.join(temp_root, "reads"), _RECORDER_DIR
    if not (recorder and os.path.isfile(os.path.join(recorder, "sitecustomize.py"))):
        recorder = _RECORDER_DIR
    return os.path.join(outer, "nested", os.path.basename(temp_root)), recorder


def _case_events(log_dir):
    """{case name: events} from the folders `tests/_case_pool.case_log_dir` gives each case under `log_dir`."""
    folder = os.path.join(log_dir, "cases")
    return {urllib.parse.unquote(name): _read_events(os.path.join(folder, name))
            for name in (os.listdir(folder) if os.path.isdir(folder) else [])}


def run_proof(path, timeout=None, root=None, *, behavior_cases=None, receipt_sink=None, reads_sink=None,
              reuse_cases=None, stretch=None):
    """(status, seconds, detail) — status is "pass", "cannot-run", "platform-skip" or "fail".

    A `reads_sink` dict turns on `scripts/_read_recorder` for a Python or JS proof and its inheriting Python and
    node children, and receives `events` (what they read, probed, listed and spawned), `cases` ({case name:
    events} of each `_case_pool` case that ran), `base` (the events outside every case) and `temp` (the
    proof's temp root) for the pass cache's observed keys. `reuse_cases` names the `_case_pool` cases the proof
    skips because their recorded passes hold (`HARNESS_CASES_REUSED`). `stretch()` multiplies `timeout` while the
    proof runs (`_run_child`); the scheduler passes the run's live pace, a run-time measure kept out of the pass key.

    Each proof runs with TMP, TEMP and TMPDIR pointed at its own temp root, deleted when the
    proof ends on every exit path, so fixture folders a proof never removes do not pile up in
    the machine's TEMP. Its PROVIDER_CAPACITY_CACHE is a snapshot of the live capacity cache in that root, and
    HARNESS_CAPACITY_WARM=0 keeps it from starting warm probes.

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
    env = {k: v for k, v in os.environ.items()
           if k not in GIT_REPO_ENV and k not in (STAMP_INDEX_ENV, "HARNESS_CASES_REUSED")}
    # A proof that bounds its own waits under this timeout reads it here, not from the durations store under the
    # repository's git dir: a read there keys the proof's pass on the whole repository state.
    env["HARNESS_PROOF_TIMEOUT_S"] = "%g" % timeout
    if reuse_cases:
        env["HARNESS_CASES_REUSED"] = json.dumps(sorted(reuse_cases))
    if os.environ.get("GIT_INDEX_FILE"):
        env[STAMP_INDEX_ENV] = os.environ["GIT_INDEX_FILE"]
    temp_root = tempfile.mkdtemp(prefix="harness_proof_")
    env.setdefault("HARNESS_PROVIDER_GUARD_LOG", os.path.join(temp_root, "workflow_provider_guard.jsonl"))
    env.setdefault("HARNESS_USAGE_SAMPLES", os.path.join(temp_root, "usage_samples.jsonl"))
    # Capacity probes rewrite the live cache every few minutes: a proof reading it keyed its pass on bytes that
    # change between closes and could read a sibling's rewrite. Each proof reads a snapshot taken at its start.
    live_capacity = os.environ.get("PROVIDER_CAPACITY_CACHE") or os.path.join(
        root or REPO_ROOT, ".claude", ".cache", "provider_capacity.json")
    env["PROVIDER_CAPACITY_CACHE"] = os.path.join(temp_root, "provider_capacity.json")
    try:
        shutil.copyfile(live_capacity, env["PROVIDER_CAPACITY_CACHE"])
    except OSError:
        pass
    env["HARNESS_CAPACITY_WARM"] = "0"
    # The runner already runs proofs side by side; a proof's own case pool (tests/_case_pool.py) nests
    # inside it, and 4 x 4 git-spawning processes timed out proofs that pass alone.
    env.setdefault("HARNESS_CASE_JOBS", "2")
    env.update({name: temp_root for name in _TEMP_ENV})
    if "PATH" in env:
        env["PATH"] = _land_path(env["PATH"])
    # A nested runner spends this proof's held lane serially; case pools borrow only free extra lanes.
    env["HARNESS_SLOT_BUDGET"] = "1"
    env.setdefault("HARNESS_MACHINE_ROOT", _slot_dir())
    env["HARNESS_SLOT_DIR"] = os.path.join(temp_root, "slots")
    for name in _CALLER_RUN_ENV:
        env.pop(name, None)
    # A proof is no session and no delegate: the caller's identity would let its doctrine state or
    # delegate guards decide the proof's verdict. A proof that needs one plants its own.
    for name in _CALLER_IDENTITY_ENV:
        env.pop(name, None)
    if root:
        env["CLAUDE_PROJECT_DIR"] = root
    home = os.path.join(temp_root, "home")
    os.makedirs(home, exist_ok=True)
    # HARNESS_CALLER_HOME: a proof whose subject is this machine's live state (its session registry beside the
    # real process table) reads the caller's home through this name, so such a read is declared, never ambient.
    env["HARNESS_CALLER_HOME"] = os.path.expanduser("~")
    env.update({name: home for name in _RUNNER_HOME_ENV})
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # A fixture repository never runs git's detached auto-maintenance: it rewrites `.git` (a `maintenance.lock`,
    # a repack) while the proof copies or reads that tree, and the copy fails on a file that vanished.
    config = [("maintenance.auto", "false"), ("gc.auto", "0")]
    if os.name == "nt":
        # Git for Windows' system config also holds core.fscache, a speed-only cache; without it a git call runs
        # about four times slower than the runner's own git, so it alone comes back as command-scope config.
        config.append(("core.fscache", "true"))
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    for key, value in config:
        env.update({"GIT_CONFIG_KEY_%d" % count: key, "GIT_CONFIG_VALUE_%d" % count: value})
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    for name in [k for k in env if k.startswith("CLAUDE_") and k not in _RUNNER_CLAUDE_DECLARED]:
        del env[name]
    # The installed Claude Code binary is a tool, found by the caller's own home before this one replaces it, so
    # a proof that runs its embedded `rg` or `claude -p` finds it as it would on a machine that has it.
    for exe in (os.environ.get("CLAUDE_CODE_EXECPATH"), os.path.expanduser("~/.local/bin/claude.exe"),
                os.path.expanduser("~/.local/bin/claude")):
        if exe and os.path.isfile(exe):
            env["CLAUDE_CODE_EXECPATH"] = exe
            break
    log_dir = None
    if reads_sink is not None and path.endswith((".py", ".js")):
        log_dir, recorder = _nested_log(temp_root)
        env["HARNESS_READ_LOG"] = log_dir
        env["HARNESS_READ_RECORDER"] = recorder
        # A runner this proof runs inherits an outer runner's recorder, from a folder that may be another path
        # than `_RECORDER_DIR`: two recorders on the path run the shadowed one as a second recorder that
        # `retarget` never reaches, so every read also lands in the proof's base log.
        kept = [e for e in env.get("PYTHONPATH", "").split(os.pathsep)
                if e and os.path.basename(os.path.normpath(e)) != os.path.basename(_RECORDER_DIR)]
        env["PYTHONPATH"] = os.pathsep.join([recorder] + kept)
        outer = re.sub(r'--require\s+(?:"(?:[^"\\]|\\.)*node_recorder\.js"|\S*node_recorder\.js)', "", env.get("NODE_OPTIONS", ""))
        env["NODE_OPTIONS"] = " ".join(outer.split() + ["--require " + json.dumps(_NODE_RECORDER)])
    start = time.time()
    out = ""
    try:
        for command in _runner_cmds(path):
            # pytest's exit 2 is an interrupted run or a collection error, a red; only a proof's own exit 2 means cannot-run.
            pytest_run = command[1:3] == ["-m", "pytest"]
            # A proof resolves `.claude/` paths from its working directory: run it from its checkout, never the caller's.
            returncode, part = _run_child(command, env, timeout, os.path.join(temp_root, "proof_output.txt"),
                                          cwd=root or REPO_ROOT, stretch=stretch)
            out += part
            if returncode != 0:
                break
        if log_dir:
            base, per_case = _read_events(log_dir), _case_events(log_dir)
            reads_sink.update(events=base + [e for case in per_case.values() for e in case], base=base,
                              cases=per_case, temp=temp_root)
        if returncode is None:
            status, detail = "fail", "TIMEOUT after %ds" % (time.time() - start)
        elif returncode == 0 and _PLATFORM_SKIP_RE.search(out):
            status, detail = "platform-skip", out
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


# Windows caps a command line at 32,767 characters. `status` and `ls-files` print a per-path union, so a
# longer pathspec list runs in chunks whose outputs concatenate to the single call's.
_ARGV_BUDGET = 24000
_PATH_UNION_COMMANDS = ("status", "ls-files")


def _git(repo_root, args):
    """Raise on any git failure: a hash over a failed enumeration must never be stamped."""
    if args and args[0] in _PATH_UNION_COMMANDS and "--" in args:
        split = args.index("--") + 1
        head, paths = args[:split], args[split:]
        if sum(len(a) + 1 for a in args) > _ARGV_BUDGET:
            chunks, chunk, size = [], [], sum(len(a) + 1 for a in head)
            for path in paths:
                if chunk and size + len(path) + 1 > _ARGV_BUDGET:
                    chunks.append(chunk)
                    chunk, size = [], sum(len(a) + 1 for a in head)
                chunk.append(path)
                size += len(path) + 1
            chunks.append(chunk)
            if len(chunks) > 1:
                return "".join(_git(repo_root, head + c) for c in chunks)
    result = subprocess.run(["git"] + args, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args[0], repo_root, result.stderr.strip()))
    return result.stdout


def _head(repo_root):
    """HEAD's sha, or None in a repository with no commit yet (a fresh bootstrap); other git failures raise."""
    try:
        return _git(repo_root, ["rev-parse", "--verify", "--quiet", "HEAD"]).strip() or None
    except RuntimeError:
        _git(repo_root, ["rev-parse", "--git-dir"])
        return None


def tree_entries(repo_root):
    """{posix-relpath: sha256(bytes)} for every tracked/untracked-unignored code file under
    `DIRS`, enumerated by `git ls-files`, run with cwd=repo_root — plus every proof
    `discover()` finds: a consumer whose `.gitignore` still ignores `.claude/tests/` hides a
    not-yet-added proof from both `ls-files` calls, which would leave the set unchanged. The stamp stores this map so the commit guard can judge only the
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
    # `.md` that carry pins. `.claude/scripts/benchmark_campaign/`
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
        head = _head(repo_root)
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

# {proof basename: tracked directories it lists and reads without naming each file}. Every file under them
# binds the proof, and the pass cache keys them all, so a proof reuses its pass only while that whole corpus is
# unchanged.
_CORPUS_SCANNING = {
    "test_hook_state.py": (".claude/hooks",),
    "test_claudemd_citations.py": (".claude",),
    "test_guard_text.py": (".claude/guards",),
    # Its live case checks every tracked row's citations, so a new file a row names can turn it red.
    "test_layer_closure.py": (".claude",),
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
        module = pending.pop()
        pattern = _import_re(module)
        for rel, text in sources.items():
            if rel == touched or rel in importers or module not in text or not pattern.search(text):
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
    result = subprocess.run(["git", "ls-files", "--", ".claude"], cwd=repo_root, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    census = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            rel = _norm(line.strip())
            if rel:
                census.setdefault(rel.rsplit("/", 1)[-1], set()).add(rel)
    return census


def _named_for(name, stem):
    """A proof file name binds `stem` as `test_<stem>*`, `<stem>_test.*` or `<stem>_<suffix>_test.*`."""
    return (name.startswith("test_" + stem) or name.startswith(stem + "_test")
            or (name.startswith(stem + "_") and re.search(r"_test\.\w+$", name) is not None))


_PART_RE = re.compile(r"^(?P<stem>.+)_part\d+(?P<ext>\.\w+)$")


def _part_main(path):
    """The proof file `path` is a part of (`<proof>_part<N>.<ext>` beside `<proof>.<ext>`), else None. A part
    runs a share of that proof's cases as its own pool entry, so it is selected and reused as that proof is."""
    found = _PART_RE.match(os.path.basename(path))
    main = os.path.join(os.path.dirname(path), found.group("stem") + found.group("ext")) if found else None
    return main if main and os.path.isfile(main) else None


def _mention_token(rel, census):
    """What a proof's source must contain to name `rel`: its basename, or `<parent>/<basename>` when
    another tracked `.claude/` file shares the basename (every skill's `SKILL.md`)."""
    parts = rel.split("/")
    if len(parts) > 1 and census.get(parts[-1], set()) - {rel}:
        return "/".join(parts[-2:])
    return parts[-1]


def _is_record(path):
    return path.endswith(".jsonl")


def bindings(repo_root, touched, include_excluded=False, proofs=None):
    """{touched path: [bound proof paths]}, in discovery order. A path binds only the proofs that are it,
    are named for it (`test_<stem>*`, `<stem>_test.*`), import it, name it in their runnable text, declare it
    in `PROOF_BINDINGS` (which replaces the naming rule, never the import rule), or scan its folder; a
    `.jsonl` record binds nothing. A proof imports a path its code imports, runs as a `-m` module or
    `require`s (`_PassCache._imports`); it names a path when its runnable text (`_code_text`) holds the
    basename, or the `<parent>/<basename>` when the basename is shared under `.claude/`. A comment or
    docstring runs nothing, so it binds nothing. A part file (`_part_main`) is bound by whatever binds its
    proof. `proofs` restricts the candidates to those paths."""
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir) if proofs is None else list(proofs)
    if not include_excluded:
        discovered = [p for p in discovered if os.path.basename(p) not in EXCLUDED]
    census = _tracked_by_basename(repo_root)
    rules = {}
    for t in dict.fromkeys(_norm(t) for t in touched):
        rules[t] = (
            os.path.splitext(t.rsplit("/", 1)[-1])[0],
            None if t in PROOF_BINDINGS else _mention_token(t, census),
            PROOF_BINDINGS.get(t, ()),
        )
    texts, code, loader = {}, {}, []

    def text_of(path):
        if path not in texts:
            try:
                with open(path, "rb") as fh:
                    texts[path] = fh.read().decode("utf-8", "replace")
            except OSError:
                texts[path] = ""
        return texts[path]

    def code_of(path):
        if path not in code:
            code[path] = _code_text(path, text_of(path))
        return code[path]

    def loads(path, t, stem):
        # A load names the module's stem, so only a proof whose text holds it pays the import resolution.
        if not t.endswith(_CODE_EXT) or stem not in text_of(path):
            return False
        if not loader:
            loader.append(_PassCache(repo_root))
        return t in loader[0]._imports(_norm(os.path.relpath(path, repo_root)))

    def binds(path, t, stem, token, declared):
        name = os.path.basename(path)
        return _norm(os.path.relpath(path, repo_root)) == t or (not _is_record(t) and (
            name in _DIR_SCANNING or name in declared
            or any(t.startswith(folder.rstrip("/") + "/") for folder in _corpus_dirs(name))
            or _named_for(name, stem)
            or (token is not None and token in text_of(path) and token in code_of(path)) or loads(path, t, stem)))

    result = {t: [] for t in rules}
    for path in discovered:
        main = _part_main(path)
        for t, rule in rules.items():
            if binds(path, t, *rule) or (main is not None and binds(main, t, *rule)):
                result[t].append(path)
    return result


def shipped_proofs_by_tool(repo_root, tools):
    """{tool: repo-relative paths of the non-fixture proofs bound to it by name or by an importer's name,
    never by a mention: a proof that only names the tool in its source does not prove it}. One module
    scan and one discovery serve every tool."""
    tools = list(dict.fromkeys(tools))
    sources = _module_sources(repo_root) if any(t.endswith(".py") for t in tools) else {}
    stems = {}
    for tool in tools:
        found = {os.path.splitext(tool.rsplit("/", 1)[-1])[0]}
        if tool.endswith(".py"):
            found |= {os.path.splitext(n.rsplit("/", 1)[-1])[0] for n in _importers(tool, sources)}
        stems[tool] = found
    proofs = [(os.path.basename(p), _norm(os.path.relpath(p, repo_root)))
              for p in discover(os.path.join(repo_root, ".claude", "tests"))]
    return {tool: [rel for name, rel in proofs if any(_named_for(name, stem) for stem in found)]
            for tool, found in stems.items()}


def shipped_proofs(repo_root, tool):
    return shipped_proofs_by_tool(repo_root, [tool])[tool]


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


def _recorded_passes(repo_root, bound, cache):
    """The members of `bound` whose recorded pass still holds under `cache`: reusing one costs no run."""
    if cache is None or not bound:
        return set()
    rels = {p: _norm(os.path.relpath(p, repo_root)) for p in bound}
    cases = _behavior_plan(repo_root, list(rels.values())).get("cases", {})
    cache.snapshot_git()
    held = set()
    for proof, rel in rels.items():
        try:
            key = cache.lazy_key(proof)
            if key and (cache.hit(proof, key, cases[rel]) if cases.get(rel) else cache.hit(proof, key)):
                held.add(proof)
        except Exception:  # noqa: BLE001 - a cache fault leaves the proof to the budgets
            continue
    return held


def select_for_commit(repo_root, touched, cache=None):
    """(selected, how many are left to close), in binding order; `_commit_selection` names the ones left."""
    selected, left = _commit_selection(repo_root, touched, cache)
    return selected, len(left)


def _commit_selection(repo_root, touched, cache=None):
    """(selected, left to close), each a list of proofs in binding order. A proof with a recorded pass under
    `cache` is always selected. Another proof's unknown time exceeds the inner budget; after the first eligible
    proof would exceed the total budget, no later proof is added."""
    if not touched:
        return [], []
    bound = select_for(repo_root, touched)
    held = _recorded_passes(repo_root, bound, cache)
    known = read_json_salvage(durations_path()) if os.path.exists(durations_path()) else {}
    chosen, total, full = set(held), 0.0, False
    for proof in bound:
        seconds = known.get(os.path.basename(proof))
        if proof in held or full:
            continue
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0 or seconds > _PROOF_BUDGET_S):
            continue
        if total + seconds > COMMIT_BUDGET_S:
            full = True
            continue
        chosen.add(proof)
        total += seconds
    return [p for p in bound if p in chosen], [p for p in bound if p not in chosen]


def unproven_command(touched):
    """The one command that proves a commit's paths now."""
    return "python3 .claude/scripts/harness_tests.py --proofs-for %s" % " ".join(touched)


def _unproven(touched, why, signature):
    """The one advisory for a commit whose bound proofs did not all pass here: why, and the command that
    proves it now. `signature` names the cause, so a repeat of the same cause in a session is one line."""
    line = "[harness commit] unproven, not passed: %s; `%s` runs them." % (why, unproven_command(touched))
    return {"signature": json.dumps(signature), "line": line}


def run_commit(repo_root, touched):
    """Select and run cheap bound proofs with the scoped pass cache; return facts and advisories,
    never a commit verdict or stamp. The caller bounds this whole run, including cache/slot waits."""
    with _policy_from(os.path.join(repo_root, ".claude")), redirect_stdout(io.StringIO()):
        cache = _pass_cache(repo_root, list(touched))
        selected, left_proofs = _commit_selection(repo_root, touched, cache=cache)
        left = len(left_proofs)
        statuses, details = {}, {}
        if selected:
            _run_selected(selected, False, statuses, details,
                          RunOptions(cache=cache, root=repo_root, repo=repo_root))
    advisories = []
    for proof in selected:
        if statuses[proof] != "fail":
            continue
        lines = [line.strip() for line in details[proof].splitlines()]
        first = next((line for line in lines if (FAILURE_LINE.search(line) or _FAILURE_MARK_RE.search(line))
                      and not _UNITTEST_SUMMARY_RE.match(line)), _last_line(details[proof]))
        advisories.append("[harness commit] %s: %s; the main check proves it on main after a push."
                          % (os.path.basename(proof), first or "failed without a failure-marked line"))
    unavailable = [p for p in selected if statuses[p] in ("cannot-run", "skipped")]
    why = []
    if unavailable:
        why.append("%d could not run (%s: %s)" % (len(unavailable), os.path.basename(unavailable[0]),
                                                   _last_line(details[unavailable[0]])))
    if left:
        why.append("%d over the commit budget" % left)
    unproven = _unproven(touched, "; ".join(why),
                         [sorted(os.path.basename(p) for p in unavailable),
                          sorted(os.path.basename(p) for p in left_proofs)]) if why else None
    if unproven:
        advisories.append(unproven["line"])
    advisories += _growth_advisories(touched)
    return {"selected": [_norm(os.path.relpath(p, repo_root)) for p in selected],
            "left": left, "advisories": advisories, "unproven": unproven,
            "bound": sorted(os.path.basename(p) for p in selected + left_proofs)}


MAIN_CHECK_REF = "refs/remotes/origin/main-check-state"


def open_main_lines(repo_root, bound):
    """One `open on main` line per open main-check proof row named by `bound`, the basenames of the proofs the
    commit's paths bind. Reads the local state ref only: no fetch, and no import of main_check.py, so an edit to
    that script never reaches a commit. An absent or malformed state prints nothing. Takes names, not paths: the
    runner's launch key reaches what this function's callers reach, and binding costs its module values there."""
    try:
        shown = subprocess.run(["git", "-C", repo_root, "show", MAIN_CHECK_REF + ":main_check.json"],
                               capture_output=True, text=True, timeout=10)
        if shown.returncode != 0:
            return []
        rows = json.loads(shown.stdout)["rows"]
    except Exception:  # noqa: BLE001 - an advisory fault never costs the commit
        return []
    lines = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("clearedAt") is not None:
            continue
        name = row.get("name")
        if isinstance(name, str) and os.path.basename(name) in bound:
            lines.append("[harness commit] open on main: %s %s" % (os.path.basename(name), row.get("platform") or "unknown"))
    return lines


def commit_advisories(repo_root, touched, git_env=None, session_id=""):
    """Hard-bound advisory run, including imports and selection. Leave cleanup headroom under the
    hook timeout; child proofs still use the runner's shared slots, deadlines and pass cache. An unproven
    line whose cause this session already saw since its last compaction is not printed again. The committed
    checkout's own runner runs, when it has one: its close uses that runner, and the launch code keys every pass."""
    if not touched:
        return []
    env = dict(os.environ)
    env.update(git_env or {})
    env.setdefault("HARNESS_TEST_STAMP", os.path.join(repo_root, ".claude", "logs", "harness_tests_stamp.json"))
    env["HARNESS_DEADLINE"] = str(time.time() + max(0, COMMIT_BUDGET_S - 3))
    own = os.path.join(repo_root, ".claude", "scripts", "harness_tests.py")
    temp_root = tempfile.mkdtemp(prefix="harness_commit_")
    try:
        code, output = _run_child([sys.executable, own if os.path.isfile(own) else os.path.abspath(__file__),
                                   "--repo", repo_root,
                                   "--commit"] + list(touched), env, COMMIT_BUDGET_S,
                                  os.path.join(temp_root, "result.json"), cwd=repo_root)
        if code is None:
            raise TimeoutError("timeout after %ds" % COMMIT_BUDGET_S)
        if code != 0:
            raise RuntimeError(_last_line(output) or "runner exit %s" % code)
        result = json.loads(output)
        unproven = result.get("unproven")
    except Exception as exc:
        unproven = _unproven(touched, "the bound-proof run failed (%s: %s)" % (type(exc).__name__, exc),
                             type(exc).__name__)
        result = {"advisories": [unproven["line"]]}
    finally:
        _remove_temp_root(temp_root)
    advisories = open_main_lines(repo_root, result.get("bound", [])) + result["advisories"]
    if unproven and session_id:
        from _hook_state import fire_once_since_compaction
        if not fire_once_since_compaction(session_id, "harness-unproven:" + unproven["signature"]):
            advisories = [a for a in advisories if a != unproven["line"]]
    return advisories


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
    """Pool width: `HARNESS_TEST_JOBS` when a positive int, else the machine's own recorded width,
    else 4. A runner started outside the client that sets the variable (the land runner service)
    then pools as wide as the slot store records. A runner inside an inherited lane (`HARNESS_SLOT_BUDGET=1`)
    runs one proof at a time whatever `HARNESS_TEST_JOBS` says."""
    if _slot_pool.inherited():
        return 1
    try:
        jobs = int(os.environ.get("HARNESS_TEST_JOBS", ""))
    except ValueError:
        jobs = 0
    if jobs > 0:
        return jobs
    slots = _machine_slots()
    return slots if slots > 0 else 4


def _timed_out(result):
    return result[0] == "fail" and str(result[2] or "").startswith("TIMEOUT")


def _timeout_detail(detail):
    """True when a red's detail is a timeout: the runner's own `TIMEOUT after Ns`, or a proof's captured
    traceback whose last line is the `subprocess.TimeoutExpired` its own call raised."""
    text = (detail or "").strip()
    if text.startswith("TIMEOUT"):
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and lines[-1].startswith("subprocess.TimeoutExpired")


def _deadline():
    """`HARNESS_DEADLINE` (epoch seconds) when set and numeric, else None. A caller with a hard time
    limit sets it so the run stops starting proofs instead of being killed."""
    try:
        return float(os.environ["HARNESS_DEADLINE"])
    except (KeyError, ValueError):
        return None


_SKIPPED = ("skipped", 0.0, "not run: HARNESS_DEADLINE passed")


def _slot_dir():
    return _slot_pool.slot_dir()


def _machine_slots():
    return _slot_pool.capacity(_slot_dir())


SlotStoreError = _slot_pool.SlotStoreError


def _claim_width(folder, count):
    return _slot_pool.claim_width(folder, count)


def _take_slots(exclusive, count=1, split=None):
    """Admission shared with publish, case pools, runtime work and WSL's Windows broker. A take that queues
    prints a queued line naming the holders; `split` receives where its wait went (`machine_slots.take`)."""
    return _slot_pool.take(exclusive, count, folder=_slot_dir(), width=_machine_slots(), deadline=_deadline(),
                           notice=True, split=split)


@contextmanager
def _slot_guard(exclusive=False, count=1):
    """Hold machine slots for the block, through `_take_slots`, the only admission. Raises
    `SlotStoreError` when the slots cannot be taken, so a caller never runs its work uncharged."""
    handles = _take_slots(exclusive, count)
    if handles is None:
        raise SlotStoreError("deadline passed before the machine slots were free")
    try:
        yield handles
    finally:
        _slot_pool.release_all(handles)


_MACHINE_HELD = False


@contextmanager
def _machine_hold():
    """Every machine slot, taken once for a run's alone work (its `SERIAL` proofs and alone retries), which
    then runs back to back. Each separate exclusive take waits for every proof running on the machine to end,
    and no run there starts a proof meanwhile; one take per run pays that drain once. Yields False when the
    deadline passes first or the slot store is unusable: each launch then takes or reports its own slots."""
    global _MACHINE_HELD
    if _MACHINE_HELD:
        yield True
        return
    try:
        handles = _take_slots(True)
    except SlotStoreError:
        handles = None
    if handles is None:
        yield False
        return
    _MACHINE_HELD = True
    try:
        yield True
    finally:
        _MACHINE_HELD = False
        _slot_pool.release_all(handles)


def _launch(path, timeout=None, exclusive=False, root=None, recheck=None, timing=None, *, behavior_cases=None, receipt_sink=None,
            reads_sink=None, reuse_cases=None):
    """Start one proof after admission; exclusive proofs drain the shared machine pool.
    Nested runners spend the inherited lane serially. Case pools can borrow free extra lanes.
    Queue time is outside the proof timeout and is returned in `timing.wait_s`.
    A missing slot store is cannot-run; an expired run deadline is `_SKIPPED`."""
    asked = time.time()
    split = {}
    try:
        handles = [] if _MACHINE_HELD else _take_slots(exclusive or os.path.basename(path) in SERIAL, split=split)
    except SlotStoreError as exc:
        return ("cannot-run", 0.0, str(exc))
    finally:
        if timing is not None:
            timing["wait_s"] = round(time.time() - asked, 2)
            timing["wait_split"] = split
    if handles is None:
        return _SKIPPED
    try:
        return _admitted_run(path, timeout, root, recheck, behavior_cases=behavior_cases, receipt_sink=receipt_sink,
                             reads_sink=reads_sink, reuse_cases=reuse_cases, stretch=_PACE.factor)
    finally:
        _slot_pool.release_all(handles)


def _admitted_run(path, timeout, root, recheck, *, behavior_cases, receipt_sink, reads_sink, reuse_cases,
                  stretch=None):
    """`_launch`'s run once its slot is held: `recheck()` may answer from the pass cache, the deadline caps the
    timeout and its live-pace stretch or refuses the run, and `run_proof` runs with the keywords given. A run the cap cut short is `_SKIPPED`."""
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
    if stretch is not None:
        if deadline is not None:
            pace, ceiling = stretch, max(1.0, left / timeout)
            stretch = lambda: min(pace(), ceiling)  # noqa: E731
        extra["stretch"] = stretch
    if reuse_cases:
        extra["reuse_cases"] = reuse_cases()
    if behavior_cases:
        result = run_proof(path, timeout, root, behavior_cases=behavior_cases, receipt_sink=receipt_sink, **extra)
    else:
        result = run_proof(path, timeout, root, **extra) if root else run_proof(path, timeout, **extra)
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
# What `git rev-parse` prints about where a repository lives, never what it holds, and the `ls-files` flags that
# keep it to the tracked names (a pathspec is a name too). Any other argument widens the scope to `repo`.
_GIT_LOCATION_ARGS = frozenset({"--local-env-vars", "--git-dir", "--git-common-dir", "--absolute-git-dir",
                                "--show-toplevel", "--show-cdup", "--show-prefix", "--show-superproject-working-tree",
                                "--is-inside-work-tree", "--is-inside-git-dir", "--is-bare-repository",
                                "--path-format=absolute", "--path-format=relative"})
_GIT_TRACKED_ARGS = frozenset({"-z", "--error-unmatch", "-c", "--cached", "--full-name", "--"})
# `repo` plus `untracked`: the scope of a git whose commands are unknown, so what it can see is all of it.
_GIT_WHOLE = "repo+untracked"
# `git show <id>:<path>` and `git cat-file -p|-t|-s|-e <id>` with a full object id print bytes the id fixes, whatever
# the worktree, refs or config say: the key holds the object's presence (`_objects_state`), never a repository's state.
_GIT_OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?(?::.+)?\Z")
_GIT_OBJECT_FLAGS = {"show": frozenset(), "cat-file": frozenset({"-p", "-t", "-s", "-e"})}
# Commands that read objects, refs and the tracked files and never list an untracked file: an untracked file cannot
# change what they print. Any other command (status, clean, add, ls-files -o, checkout) can, and keys `untracked`.
_GIT_UNTRACKED_BLIND = frozenset({"log", "show", "rev-parse", "rev-list", "cat-file", "diff", "diff-tree", "diff-index",
                                  "ls-tree", "show-ref", "for-each-ref", "config", "branch", "tag", "describe", "blame",
                                  "merge-base", "name-rev", "shortlog", "reflog", "remote", "symbolic-ref", "hash-object",
                                  "count-objects", "archive", "format-patch", "cherry", "whatchanged"})
# A word `git` or `git.exe` inside a command line: after a space, an operator, a quote or a path separator, and before
# a space, an operator or a quote (`cd root&&git log`, `"C:\Program Files\Git\cmd\git.exe" log`).
_GIT_WORD_RE = re.compile(r"""(?:^|[\s;&|(`'"/\\])git(?:\.exe)?(?=$|[\s;&|)`'"])""", re.IGNORECASE)
_SHELL_SPLIT_RE = re.compile(r"""[\s;&|()<>`'"]+""")
# pytest's cache steers only --lf/--ff/--nf/--sw, which no proof passes; every pytest proof rewrites it.
_TOOL_CACHE_RE = re.compile(r"(?:^|/)(?:\.pytest_cache|pytest-cache-files-[^/]*)(?:/|$)")
# The gitignored folders every hook and tool fills as it runs, and where a proof plants a fixture under a name unique
# to the run: a proof that wrote here did not leave an input behind for the next run.
_WRITE_EXHAUST = (".claude/logs/", ".claude/scratch/", "logs/", ".claude/cache/", ".claude/.cache/", ".claude/tmp/")


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


_ROWS_MEMO, _ROWS_LOCK = {}, threading.Lock()
_KEY_SCHEME = 5  # what the observed key holds; a change retires every row recorded under another
# The git state a proof's key can hold, one entry per scope a manifest names: the `_PassCache` method that reads it
# (a digest, or None when git cannot be read). A new git input is one entry here and its reader.
_GIT_SCOPES = {"repo": "_repo_state", "tracked": "_tracked_names", "untracked": "_untracked_names"}


def _pack_manifest(manifest):
    """A manifest as one short string. Raw, its probed import paths were 80% of a 118 MB cache file that
    every proof's record parsed and rewrote whole; packed, a row costs about 6 KB."""
    return base64.b64encode(zlib.compress(json.dumps(manifest, separators=(",", ":")).encode("utf-8"))).decode("ascii")


def _cause(event):
    """A recorder event as a manifest's `why` keeps it: a path event whole, a spawn by its argv, each argument
    cut short (a `-c` program can run to kilobytes)."""
    kind, value = event
    if kind == "s":
        argv = value.get("argv") if isinstance(value, dict) else None
        return ["s", [a[:200] for a in argv if isinstance(a, str)][:12] if isinstance(argv, list) else []]
    return [kind, value]


def _file_digests(keyed):
    """{path: [short digest, tracked]} of a key's file rows: enough to name which file moved, never part of a key."""
    return {r: [d[:12] if d else None, t] for r, d, t in keyed}


def _merge_manifests(manifests):
    """One manifest holding every read of `manifests`; opaque when any is. Each `why` cause is the first
    manifest's that names one."""
    merged = {"files": set(), "probes": set(), "dirs": set(), "static": set(), "objects": set(), "git": set(),
              "opaque": False}
    why = {}
    for manifest in manifests:
        for field in ("files", "probes", "dirs", "static", "objects"):
            merged[field].update(manifest.get(field) or ())
        scopes = manifest.get("git")
        merged["git"].update({"repo"} if scopes is True else scopes or ())
        merged["opaque"] = merged["opaque"] or bool(manifest.get("opaque"))
        for scope, cause in (manifest.get("why") or {}).items():
            why.setdefault(scope, cause)
    merged["probes"] -= merged["files"]
    return dict({k: sorted(v) if isinstance(v, set) else v for k, v in merged.items()}, why=why)


def _packed_row(row):
    """`row` with an inline `manifest` (written before manifests were packed) moved to `zm`."""
    if not isinstance(row.get("manifest"), dict):
        return row
    return dict({k: v for k, v in row.items() if k != "manifest"}, zm=_pack_manifest(row["manifest"]))


_ROW_WINDOW_S = 30 * 86400


def _sweep_rows(rows, now, standing):
    """Drop from `rows` ({name: [row]}) each row past the 30-day window and each row of a removed checkout, and
    pack a row whose manifest is inline; True when anything changed. `standing` memoizes which checkouts exist.
    A close checkout's rows stay: the checkout is removed when its close ends, and the next close replaces them."""
    def lives(row):
        where = row.get("checkout")
        if where in (None, _CLOSE_CHECKOUT):
            return True
        if where not in standing:
            standing[where] = os.path.isdir(where)
        return standing[where]

    changed = False
    for name in list(rows):
        entry = rows[name]
        listed = [entry] if isinstance(entry, dict) else entry if isinstance(entry, list) else []
        kept = [_packed_row(r) for r in listed if isinstance(r, dict) and now - r.get("ts", 0) <= _ROW_WINDOW_S and lives(r)]
        if kept:
            changed = changed or kept != listed or listed is not entry
            rows[name] = kept
        else:
            changed = True
            rows.pop(name)
    return changed


def _fold_legacy_rows(rows, legacy):
    """Fold `legacy` ({name: rows}) into a proof file's `rows`: one row per checkout, the newest, newest first."""
    for name, entry in legacy.items():
        newest = {}
        for row in (rows.get(name) or []) + ([entry] if isinstance(entry, dict) else entry if isinstance(entry, list) else []):
            if isinstance(row, dict) and (row.get("checkout") not in newest
                                          or row.get("ts", 0) > newest[row.get("checkout")].get("ts", 0)):
                newest[row.get("checkout")] = row
        if newest:
            rows[name] = sorted(newest.values(), key=lambda r: -r.get("ts", 0))


def _migrate_legacy_store(legacy, store_dir):
    """Move the rows of the one-file store `legacy`, which held every proof's rows and was rewritten whole on each
    record, into `store_dir`'s proof files, then remove it. An older runner that still records there leaves a
    file for the next call to move. A row a proof file already holds for the same checkout keeps its newer one."""
    if not os.path.isfile(legacy):
        return

    def split(doc):
        by_proof = {}
        for name, entry in doc.items():
            by_proof.setdefault(name.partition("::")[0], {})[name] = entry
        for proof, part in by_proof.items():
            update_json_locked(_store_file(store_dir, proof), lambda rows, part=part: _fold_legacy_rows(rows, part),
                               raising=True)
        doc.clear()
    try:
        os.makedirs(store_dir, exist_ok=True)
        update_json_locked(legacy, split, raising=True)
        os.remove(legacy)
    except (OSError, TimeoutError, RuntimeError, ValueError):
        pass


def sweep_pass_stores():
    """Remove from the pass and case stores each proof file no record touched in 30 days, then drop its dead rows
    from each file that holds any (`_sweep_rows`) and remove a file left with none; return how many files went.
    A record sweeps only its own proof's file, so a proof that stops recording keeps its rows until this runs."""
    now, standing, removed = time.time(), {}, 0
    for folder in (pass_store_dir(), case_store_dir()):
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".tmp_") or not entry.name.endswith(".json"):
                continue
            try:
                if now - entry.stat().st_mtime > _ROW_WINDOW_S:
                    os.remove(entry.path)
                    removed += 1
                    continue
                rows = read_json_salvage(entry.path)
                if _sweep_rows(rows, now, standing):
                    update_json_locked(entry.path, lambda doc: _sweep_rows(doc, now, standing))
                    rows = read_json_salvage(entry.path)
                if not rows:
                    os.remove(entry.path)
                    removed += 1
            except OSError:
                continue
    return removed


def _unpack_manifest(row):
    """A row's manifest, packed or inline; None when its packed form does not decode."""
    packed = row.get("zm")
    if not packed:
        return row.get("manifest")
    try:
        return json.loads(zlib.decompress(base64.b64decode(packed)))
    except (ValueError, zlib.error, TypeError):
        return None


_RUNNER_SOURCE = os.path.abspath(__file__)
# The runner code a proof's verdict rests on: what fixes the process a proof runs as (command, environment, working
# directory, read recorder, skipped cases), the timeout that bounds it and the reading of its exit, and the code that
# takes this key. `_launch_code` adds every module name they reach. Scheduling (slots, order, width, progress, whether
# a failed run is retried alone, the live pace `_launch` hands in as `stretch`) and the pass store's bookkeeping stay
# out: neither changes what a passing proof's run returns.
_LAUNCH_ROOTS = ("run_proof", "_timeout_for", "_retry_timeout", "_launch_options", "_admitted_run",
                 "_launch_digest", "_launch_code", "_runner_launch_digest")
_MUTATING_METHODS = frozenset({"append", "extend", "insert", "update", "setdefault", "pop", "popitem", "clear",
                               "remove", "discard", "add", "sort", "reverse", "__setitem__", "__delitem__"})
# Reads and writes of the module namespace that no name reference shows.
_DYNAMIC_CALLS = frozenset({"globals", "vars", "locals", "setattr", "delattr", "exec", "eval", "compile",
                            "__import__"})
_DYNAMIC_ATTRS = frozenset({"__dict__", "__globals__"})
# Process state every proof inherits, and the calls that change it.
_PROCESS_STATE = frozenset({"os.environ", "sys.path"})
_PROCESS_CALLS = frozenset({"os.putenv", "os.unsetenv", "os.chdir", "os.umask"})
# Calls whose result their arguments, the runner's location and the process environment fix: a name bound to one
# is keyed by its code. Passes are shared across checkouts on purpose, and no key holds the environment a proof
# inherits, so keying such a value (a stamp path under this checkout) would only stop reuse elsewhere.
_FIXED_CALLS = frozenset({"os.path.join", "os.path.dirname", "os.path.abspath", "os.path.basename",
                          "os.path.normpath", "os.path.expanduser", "os.environ.get", "os.getenv", "re.compile",
                          "frozenset", "tuple", "str", "int", "float", "bool"})


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id] + parts[::-1]) if isinstance(node, ast.Name) else None


def _fixed_value(node, inner=False):
    """True when the code alone fixes an assigned value: a literal, a name, an arithmetic or `or` of fixed parts, or
    a `_FIXED_CALLS` call over them. A list or set display is fixed only as such a call's argument."""
    if isinstance(node, (ast.Constant, ast.Name, ast.Attribute)):
        return True
    if isinstance(node, ast.Tuple) or (inner and isinstance(node, (ast.List, ast.Set))):
        return all(_fixed_value(e, inner) for e in node.elts)
    if isinstance(node, ast.BinOp):
        return _fixed_value(node.left, inner) and _fixed_value(node.right, inner)
    if isinstance(node, ast.BoolOp):
        return all(_fixed_value(v, inner) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        return _fixed_value(node.operand, inner)
    if isinstance(node, ast.JoinedStr):
        return all(_fixed_value(v.value if isinstance(v, ast.FormattedValue) else v, inner) for v in node.values)
    if isinstance(node, ast.Call):
        return (_dotted(node.func) in _FIXED_CALLS and all(_fixed_value(a, True) for a in node.args)
                and all(_fixed_value(k.value, True) for k in node.keywords))
    return False


def _touches_process(node, modules):
    """True when `node` may change what a proof process inherits: it calls a `_PROCESS_CALLS` function, stores into
    a module's attribute, or uses `_PROCESS_STATE` other than to read it (an alias or an argument counts)."""
    parents = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and _dotted(n.func) in _PROCESS_CALLS:
            return True
        if isinstance(n, ast.Attribute) and _dotted(n) in _PROCESS_STATE:
            parent = parents.get(n)
            if isinstance(parent, ast.Attribute) and parent.attr not in _MUTATING_METHODS:
                continue
            if isinstance(parent, ast.Subscript) and isinstance(parent.ctx, ast.Load):
                continue
            if isinstance(parent, ast.Compare) and n is not parent.left:
                continue
            return True
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]
            for target in targets:
                base = _dotted(target.value) if isinstance(target, (ast.Attribute, ast.Subscript)) else None
                if base and base.split(".")[0] in modules:
                    return True
    return False


class _Undocumented(ast.NodeTransformer):
    """Drops each docstring: it runs nothing."""

    def _strip(self, node):
        self.generic_visit(node)
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
        return node

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _strip


@functools.lru_cache(maxsize=8)
def _launch_code(source):
    """(digest of the launch code, the reached names whose run-time values join the key) of runner `source`, or
    None when its reach cannot be bounded: it does not parse, or code outside `_runner_launch_digest` uses the
    module namespace dynamically (`_DYNAMIC_CALLS`, `_DYNAMIC_ATTRS`, `sys.modules`).

    The launch code is every top-level statement binding a name `_LAUNCH_ROOTS` reach, transitively, with names
    resolved by scope (`symtable`: a local never reaches a module name); every function that rebinds a reached
    name (`global`) or may change the process state a proof inherits (`_touches_process`); and every other top-level
    statement that binds no name (module-level calls, the main guard, a star import). A reached name whose value its code
    does not fix (`_fixed_value`: a dict or set filled at run time, a call's result) is keyed by its value, so the
    code that fills or mutates it never needs tracing. Comments, docstrings and line numbers are not code."""
    try:
        tree = ast.parse(source)
        tables = {(t.get_name(), t.get_lineno()): t
                  for t in symtable.symtable(source, "<runner>", "exec").get_children()}
    except (SyntaxError, ValueError):
        return None
    body = tree.body
    for node in body:
        if isinstance(node, ast.FunctionDef) and node.name == "_runner_launch_digest":
            continue
        for n in ast.walk(node):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _DYNAMIC_CALLS) or \
                    (isinstance(n, ast.Attribute) and (n.attr in _DYNAMIC_ATTRS or _dotted(n) == "sys.modules")):
                return None
    keep_docs = any((isinstance(n, ast.Attribute) and n.attr == "__doc__") or (isinstance(n, ast.Name) and n.id == "__doc__")
                    for n in ast.walk(tree))

    def scoped(table, out):
        for symbol in table.get_symbols():
            if (symbol.is_global() and symbol.is_referenced()) or symbol.is_declared_global():
                out.add(symbol.get_name())
        for child in table.get_children():
            scoped(child, out)
        return out
    defs, runtime, always, used, declared = {}, set(), set(), [], []
    modules = {alias.asname or alias.name.split(".")[0] for node in body if isinstance(node, (ast.Import, ast.ImportFrom))
               for alias in node.names}
    for i, node in enumerate(body):
        declared.append({name for n in ast.walk(node) if isinstance(n, ast.Global) for name in n.names})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            table = tables.get((node.name, node.lineno))
            if table is None:
                return None
            outer = list(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                outer += node.bases + [k.value for k in node.keywords]
            else:
                outer += node.args.defaults + [d for d in node.args.kw_defaults if d is not None]
            used.append(scoped(table, set()) | {n.id for e in outer for n in ast.walk(e) if isinstance(n, ast.Name)})
            defs.setdefault(node.name, []).append(i)
            continue
        used.append({n.id for n in ast.walk(node) if isinstance(n, ast.Name)})
        if isinstance(node, (ast.Import, ast.ImportFrom)) and all(alias.name != "*" for alias in node.names):
            for alias in node.names:
                defs.setdefault(alias.asname or alias.name.split(".")[0], []).append(i)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for t in target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]:
                    base = t
                    while isinstance(base, (ast.Subscript, ast.Attribute, ast.Starred)):
                        base = base.value
                    if not isinstance(base, ast.Name):
                        always.add(i)
                        continue
                    defs.setdefault(base.id, []).append(i)
                    if t is not base or isinstance(node, ast.AugAssign) or node.value is None \
                            or not _fixed_value(node.value):
                        runtime.add(base.id)
            continue
        if i == 0 and not keep_docs and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        always.add(i)
        for n in ast.walk(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs.setdefault(n.name, []).append(i)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                defs.setdefault(n.id, []).append(i)
                runtime.add(n.id)
    process = {i for i, node in enumerate(body) if i not in always and _touches_process(node, modules)}
    included, reached, queue = set(), set(), list(_LAUNCH_ROOTS)
    while queue:
        while queue:
            name = queue.pop()
            if name in reached:
                continue
            reached.add(name)
            for i in defs.get(name, ()):
                if i not in included:
                    included.add(i)
                    queue.extend(used[i] & defs.keys())
        for i in range(len(body)):
            if i not in included and i not in always and (declared[i] & reached or i in process):
                included.add(i)
                queue.extend(used[i] & defs.keys())
    stripper = None if keep_docs else _Undocumented()
    parts = [ast.dump(stripper.visit(body[i]) if stripper else body[i]) for i in sorted(included | always)]
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest(), tuple(sorted(runtime & reached))


def _stable(value):
    """`value` as JSON whose text its contents fix; an object with no such form keeps its repr, which differs per
    run, so a key over it never matches and the pass reruns."""
    if isinstance(value, dict):
        return sorted(([_stable(k), _stable(v)] for k, v in value.items()), key=repr)
    if isinstance(value, (set, frozenset)):
        return sorted((_stable(v) for v in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, re.Pattern):
        return [value.pattern, value.flags]
    return repr(value)


def _launch_digest(source, namespace):
    """The runner part of every reuse key: `_launch_code` of runner `source` with each run-time value it names read
    from `namespace`, or the whole source's digest when that reach cannot be bounded. A runner edit outside the
    launch code cannot change what `run_proof` returns for a proof and its inputs, so it keeps every pass; the
    runner's own proofs read this file, so its files part reruns them on any edit."""
    code = _launch_code(source)
    if code is None:
        return hashlib.sha256(source.encode("utf-8")).hexdigest()
    digest, names = code
    values = [[name, _stable(namespace.get(name))] for name in names]
    return hashlib.sha256(json.dumps([digest, values]).encode("utf-8")).hexdigest()


def _runner_launch_digest():
    """`_launch_digest` of the runner file the keys name (`_RUNNER_SOURCE`) over this process's module values, or
    None when the file cannot be read."""
    try:
        with open(_RUNNER_SOURCE, encoding="utf-8") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    return _launch_digest(source, run_proof.__globals__)


class _PassCache:
    """A proof's pass, reused while every input it reaches is byte-identical and equally tracked.

    A proof reaches (`reach`): itself; every `.claude/` file whose basename, or whose code stem, its
    text names; its name-rule targets (`test_<stem>*`); its `proof_bindings` inputs; and the code any
    reached code loads, transitively, with the data files that each named or loaded code file names. Past
    the proof, only text that may run counts (`_code_text`): a comment or docstring loads nothing. The
    runner's launch code joins every key (`_launch_digest`): a runner edit elsewhere keeps every pass. The reach is the binding relation read from the proof's side, so a
    scoped run reuses a pass only when no input that would select the proof has changed since.
    A dir-scanning proof with no corpus entry (`_corpus_dirs`) is never reused. A Python or JS proof's row is
    judged by its observed key alone (`observed_parts`): what that run read, probed and listed, with a
    close's own exhaust left out (`_ignored`), and only the repository state its git use can observe. Rows live in `pass_store_dir()`, one file per proof;
    a proof keeps one variant per live checkout of the repository, so worktrees whose inputs differ never
    evict each other's pass. A `tests/_case_pool.run_cases` proof also keeps one row per case at
    `case_store_dir()`, keyed on what that case and the proof's module-level code read: a proof whose own
    row misses still skips each case whose row holds (`reusable_cases`)."""

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
        self.path, self.case_path = pass_store_dir(), case_store_dir()
        _migrate_legacy_store(passes_path(), self.path)
        _migrate_legacy_store(case_passes_path(), self.case_path)
        self._named, self._loaded, self._digests, self._code = {}, {}, {}, {}
        self._ignore, self.misses, self._on_disk = {}, {}, {}
        self.miss_files, self._reads = {}, {}
        self._close_checkout = os.path.basename(os.path.abspath(self.root)).startswith(_CHECKOUT_PREFIX)
        self._repo_dirs, self._outer_git = self._repository_dirs()
        self._siblings = self._other_worktrees()
        self._temp_real = self._real(tempfile.gettempdir())
        self.runner = _runner_launch_digest()

    def _read_digest(self, rel, full=None):
        try:
            with open(full or os.path.join(self.root, rel), "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return None

    def _digest(self, rel, full=None):
        """The file's digest, reread when its (mtime, size, inode) moved since the last look: a lookup late in a run
        takes the bytes a proof would read now, not the ones the run started on."""
        try:
            info = os.stat(full or os.path.join(self.root, rel))
            stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        except OSError:
            stamp = None
        held = self._digests.get(rel)
        if held is None or held[0] != stamp:
            held = self._digests[rel] = (stamp, self._read_digest(rel, full))
        return held[1]

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
        """The code `rel` may run: what it imports (`_imports`) and the code files its runnable text names
        by file name, which it may start or source."""
        return self._imports(rel) | {r for r in self._names(rel, stems=False) if r.endswith(_CODE_EXT)}

    def _imports(self, rel):
        """The code `rel` loads by module name: a module it runs by `-m`; a JS file's relative
        `require`/`import`; and a Python file's imported modules, resolved by their qualified or relative
        path as well as by the first name's stem, with every string that spells a module name
        (`importlib.import_module("x")`)."""
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
            self._loaded[rel] = found
        return self._loaded[rel]

    def reach(self, proof):
        """The proof, what it names, the corpus it scans, the data its named or loaded code names, and the code any of those
        load, transitively. The proof's whole text names, prose included, so the reach holds every file
        `bindings` binds the proof to from its runnable text and loads; past the proof, a file only
        a comment or docstring names is not reached: that chain joins every file to every other and would
        reuse nothing. A part file (`_part_main`) also reaches all its proof reaches."""
        rel = _norm(os.path.relpath(proof, self.root))
        main = _part_main(proof)
        seen = set()
        for own in [rel] + ([_norm(os.path.relpath(main, self.root))] if main else []):
            name = own.rsplit("/", 1)[-1]
            seen |= {own} | self.declared.get(name, set()) | self._names(own, raw=True)
            for stem, rels in self.by_stem.items():
                if _named_for(name, stem):
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
        their static reach), `objects` the git objects it printed by full id (`_pinned_objects`: keyed by their
        presence), and `git`, the repository state its git use can observe (`_git_scope`: `tracked`
        or `repo`; empty when git ran on no state of this checkout). `opaque` marks reads the cache cannot
        trust: a write into the checkout outside caches, logs and scratch, or a child it cannot resolve. `why` names,
        per git scope and for `opaque`, the first event that set it (`_cause`); no key reads it."""
        events, temp = sink.get("events"), sink.get("temp")
        if not events:
            # A recorded Python process opens at least its own script; no events means the recorder
            # never loaded, and a manifest built from nothing would reuse the pass forever.
            return None
        found = {"files": set(), "probes": set(), "dirs": set(), "static": set(), "objects": set(), "git": set(),
                 "opaque": False}
        written = set()

        def inside(path):
            if not isinstance(path, str) or (temp and self._within(path, temp) is not None):
                return None
            return self._within(path, self.root)

        why, seen, resolved = {}, set(), {}
        for event in events:
            if not (isinstance(event, list) and len(event) == 2):
                continue
            # Child recorders repeat imports and probes. These folds only union sets; stat events
            # still run each time because they inspect the filesystem here rather than logged bytes.
            token = (event[0], event[1]) if event[0] in ("r", "p", "l", "w") and isinstance(event[1], str) else \
                json.dumps(event, sort_keys=True) if event[0] == "s" else None
            if token is not None:
                if token in seen:
                    continue
                seen.add(token)
            self._take(event, temp, inside, found, written, resolved)
            for scope in sorted(found["git"]) + (["opaque"] if found["opaque"] else []):
                why.setdefault(scope, _cause(event))
        found["files"] -= written
        found["probes"] -= found["files"] | written
        return dict({k: sorted(v) if isinstance(v, set) else v for k, v in found.items()}, why=why)

    def _take(self, event, temp, inside, found, written, resolved=None):
        """Fold one recorder event into `found` (`manifest`)."""
        kind, value = event
        if kind == "s":
            self._classify_spawn(value if isinstance(value, dict) else {}, temp, inside, found, resolved)
            return
        rel = inside(value)
        if rel is None:
            # A linked worktree's git dir lies outside the checkout, so no path there can join the probes:
            # any touch of it reads repository state.
            if self._outer_git and isinstance(value, str) and \
                    (os.path.normcase(os.path.abspath(value)) + os.sep).startswith(self._outer_git):
                found["git"].add("repo")
            return
        if rel == ".git" or rel.startswith(".git/"):
            # An existence probe (the interpreter's venv check) reads no repository state; any other touch does.
            if kind == "p":
                found["probes"].add(rel)
            else:
                found["git"].add("repo")
            return
        if _TOOL_CACHE_RE.search(rel):
            return
        if kind == "w":
            if "__pycache__/" not in rel:
                written.add(rel)
                if not rel.startswith(_WRITE_EXHAUST):
                    found["opaque"] = True
        elif kind == "r":
            cached = re.match(r"(.*?)(?:^|/)__pycache__/([^/.]+)\.[^/]*\.pyc$", rel)
            found["files"].add((cached.group(1) + "/" if cached.group(1) else "") + cached.group(2) + ".py"
                               if cached else rel)
        elif kind == "p":
            found["probes"].add(rel)
        elif kind == "l":
            found["dirs"].add(rel)
        elif kind == "t":
            # A stat observes a file's size and times, a directory's entries: key the bytes or the listing.
            full = os.path.join(self.root, rel)
            found["dirs" if os.path.isdir(full) else "files" if os.path.lexists(full) else "probes"].add(rel)

    def _classify_spawn(self, spawn, temp, inside, found, resolved=None):
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
        # A git variable that names the repository steers any child that runs git, whatever its cwd or its program.
        reached = {self._locate(os.path.join(cwd, p), resolved) for k, v in (spawn.get("genv") or {}).items()
                   if k in GIT_REPO_ENV and k not in _GIT_NON_PATH_ENV and isinstance(v, str)
                   for p in v.split(os.pathsep) if p}
        if "sibling" in reached:
            found["git"].add("sibling")
        elif reached & {"checkout", "repository"}:
            found["git"].add("repo")
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
        if prog in ("node", "node.exe") and spawn.get("recorded"):
            return
        tokens = []
        for arg in argv:
            tokens += arg.split() if any(ch.isspace() for ch in arg) else [arg]
        pinned = self._pinned_objects(argv, cwd, spawn.get("genv")) if prog in ("git", "git.exe") else None
        if pinned is not None:
            found["objects"] |= pinned
        elif prog in ("git", "git.exe") or _GIT_WORD_RE.search(" ".join(argv[1:])):
            scope = self._git_scope(argv if prog in ("git", "git.exe") else None,
                                    tokens if prog in ("git", "git.exe") else [t for t in _SHELL_SPLIT_RE.split(" ".join(argv)) if t],
                                    cwd, inside, resolved)
            if scope is not None:
                found["git"] |= set(scope.split("+")) - {""}
        elif temp and any(self._within(os.path.join(cwd, t), temp) is not None and os.path.isfile(os.path.join(cwd, t))
                          for t in tokens[1:]):
            # No recorder follows a non-Python child, and a temp copy sits outside the static reach.
            found["opaque"] = True
        found["static"] |= self._repo_files(tokens, cwd, inside)

    @staticmethod
    def _pinned_objects(argv, cwd, genv):
        """The `"<id> <repository folder>"` entries of a git run that only prints objects by full id (`_GIT_OBJECT_ID_RE`,
        no flag outside `_GIT_OBJECT_FLAGS`), else None. A git steered by a repository variable reads another
        repository than the folder names, so it is no pinned read."""
        if any(k in GIT_REPO_ENV and k not in _GIT_NON_PATH_ENV for k in genv or ()):
            return None
        rest, folder = argv[1:], cwd
        while rest and rest[0] in ("-C", "--no-pager", "--no-optional-locks", "-P"):
            if rest[0] != "-C":
                rest = rest[1:]
            elif len(rest) > 2:
                folder, rest = os.path.join(folder, rest[1]), rest[2:]
            else:
                return None
        if not rest or rest[0] not in _GIT_OBJECT_FLAGS:
            return None
        ids = [a for a in rest[1:] if a not in _GIT_OBJECT_FLAGS[rest[0]]]
        if not ids or not all(_GIT_OBJECT_ID_RE.match(a) for a in ids):
            return None
        return {"%s %s" % (a.partition(":")[0], os.path.normpath(folder)) for a in ids}

    def _objects_state(self, objects, memo):
        """What a key holds of the pinned `objects` (`_pinned_objects`): each id's `cat-file --batch-check` line, or
        None when its repository cannot be opened. An id fixes what git prints of it, so only an object that has
        gone changes the answer."""
        wanted = tuple(sorted(objects))
        if ("objects", wanted) not in memo:
            by_folder, state = {}, []
            for entry in wanted:
                sha, _, folder = entry.partition(" ")
                by_folder.setdefault(folder, []).append(sha)
            env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_ENV}
            for folder, ids in sorted(by_folder.items()):
                try:
                    result = subprocess.run(["git", "-C", folder, "cat-file", "--batch-check"],
                                            input="\n".join(ids) + "\n", capture_output=True, text=True,
                                            encoding="utf-8", errors="replace", env=env)
                    lines = result.stdout.splitlines() if result.returncode == 0 else []
                except OSError:
                    lines = []
                state += [[sha, lines[i] if i < len(lines) else None] for i, sha in enumerate(ids)]
            memo["objects", wanted] = state
        return memo["objects", wanted]

    def _git_scope(self, argv, tokens, cwd, inside, resolved=None):
        """What one git process can observe of this checkout's repository: None when it runs elsewhere, `""`
        when it observes none of its state, `"tracked"` when it observes only which files the index tracks,
        `"sibling"` when it reaches another worktree of the repository (its state is in no key here), else `"repo"`. Only a git started directly (`argv`) with a plain command line is read; a git named
        inside another program's arguments, an option this does not know, or a command not listed here is
        `"repo"`: its result may depend on any state, so the key holds all of it. So is a git that reaches the
        repository by another route than running in the checkout: an argument naming a path of the repository
        (the checkout, its git dir, the main checkout), a folder of the repository outside this checkout, or a
        repository nested in the checkout, whose state the checkout's own never shows."""
        here = self._repo_dirs[0]

        def on_checkout(folder):
            real = self._real(folder, resolved)
            return real == here or real.startswith(here + os.sep) or inside(folder) is not None

        def names_repository(folder, args):
            values = {v for a in args for v in (a, a.partition("=")[2]) if v}
            values |= {v[len(p):] for v in values for p in ("file:///", "file://") if v.lower().startswith(p)}
            return self._in_repository(folder, resolved) or any(self._in_repository(os.path.join(folder, v), resolved) for v in values)

        def reaches_sibling(folder, args):
            values = {v for a in args for v in (a, a.partition("=")[2]) if v}
            return self._locate(folder, resolved) == "sibling" or any(self._locate(os.path.join(folder, v), resolved) == "sibling"
                                                            for v in values)

        if argv is None:
            where = os.path.join(cwd, tokens[tokens.index("-C") + 1].strip("'\"") if "-C" in tokens[:-1] else ".")
            if reaches_sibling(where, tokens[1:]):
                return "sibling"
            return _GIT_WHOLE if on_checkout(where) or names_repository(where, tokens[1:]) else None
        rest, folder = argv[1:], cwd
        while rest and rest[0].startswith("-"):
            if rest[0] == "-C" and len(rest) > 1:
                folder, rest = os.path.join(folder, rest[1]), rest[2:]
            elif rest[0] == "-c" and len(rest) > 1:
                rest = rest[2:]
            elif rest[0] in ("--no-pager", "--no-optional-locks", "-P"):
                rest = rest[1:]
            else:
                break
        if reaches_sibling(folder, rest):
            return "sibling"
        if not on_checkout(folder):
            return _GIT_WHOLE if names_repository(folder, rest) else None
        if rest == ["--version"]:
            return ""
        if not os.path.isdir(folder) or self._nested(folder):
            return _GIT_WHOLE
        if not rest or rest[0].startswith("-") or any(ch in arg for arg in argv for ch in "|&;<>$`()\n"):
            return _GIT_WHOLE
        command, args = rest[0], rest[1:]
        if command in ("init", "clone"):
            # Creates or copies a repository at the paths it is given: none of this checkout's state is read when
            # every path is a plain one under the temp root, where a proof builds its fixtures, and names no
            # checkout of a repository (a `.git` file). A flag not named here, a URL, a path anywhere else and a
            # path count other than the command's are `repo`.
            valued = {"init": ("-b", "--initial-branch"), "clone": ("-b", "--branch", "--depth")}[command]
            plain = ("-q", "--quiet", "--bare", "-n", "--no-checkout", "--no-local", "--local", "--single-branch")
            paths, i = [], 0
            while i < len(args):
                arg = args[i]
                if arg in valued:
                    i += 2
                elif arg in plain or (arg.startswith("--") and arg.partition("=")[0] in valued):
                    i += 1
                elif arg.startswith("-"):
                    return "repo"
                else:
                    paths.append(arg[len("file:///"):] if arg.lower().startswith("file:///") else arg)
                    i += 1
            if len(paths) != (2 if command == "clone" else 1):
                return "repo"
            for path in paths:
                full = self._real(os.path.join(folder, path), resolved)
                if "://" in path or path.startswith("git@") or not full.startswith(self._temp_real + os.sep) \
                        or self._in_repository(full, resolved) or os.path.isfile(os.path.join(full, ".git")):
                    return "repo"
            return ""
        if command == "rev-parse" and args and all(a in _GIT_LOCATION_ARGS for a in args):
            return ""
        if command in ("--version", "version") and not args:
            return ""
        if command == "ls-files" and all(a in _GIT_TRACKED_ARGS or not a.startswith(("-", ":")) for a in args):
            return "tracked"
        blind = command in _GIT_UNTRACKED_BLIND and "--no-index" not in args or (
            command == "status" and any(a in ("-uno", "--untracked-files=no") for a in args))
        return "repo" if blind else _GIT_WHOLE

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

    @staticmethod
    def _real(path, resolved=None):
        """Resolve links, short names and case, once per manifest when `resolved` is supplied."""
        if resolved is not None and path in resolved:
            return resolved[path]
        try:
            value = os.path.normcase(os.path.realpath(path))
        except (OSError, ValueError):
            value = os.path.normcase(str(path))
        if resolved is not None:
            resolved[path] = value
        return value

    def _repository_dirs(self):
        """Where this checkout's repository lives: the checkout first, then its git dir, the git dir its
        worktrees share and the main checkout holding that. Second value: the git dirs lying outside the
        checkout (a linked worktree's), as path prefixes in their given and their resolved spelling."""
        roots = {self._real(self.root), os.path.normcase(os.path.abspath(self.root))}
        dirs, outer = [self._real(self.root)], []
        try:
            listed = _git(self.root, ["rev-parse", "--path-format=absolute", "--absolute-git-dir", "--git-common-dir"])
        except (RuntimeError, OSError):
            listed = ""
        for line in filter(None, (x.strip() for x in listed.splitlines())):
            for spelled in (os.path.normcase(os.path.abspath(line)), self._real(line)):
                if spelled not in dirs:
                    dirs.append(spelled)
                if not any((spelled + os.sep).startswith(r + os.sep) for r in roots) and spelled + os.sep not in outer:
                    outer.append(spelled + os.sep)
            if os.path.basename(self._real(line)) == ".git" and os.path.dirname(self._real(line)) not in dirs:
                dirs.append(os.path.dirname(self._real(line)))
        return tuple(dirs), tuple(outer)

    def _other_worktrees(self):
        """The real paths of the repository's other worktrees (the main checkout and every linked one): each has
        its own HEAD and files, none of which this checkout's key holds."""
        try:
            listed = self._git_read(["worktree", "list", "--porcelain"])
        except (RuntimeError, OSError):
            return ()
        found = []
        for line in listed.splitlines():
            if line.startswith("worktree "):
                real = self._real(line[len("worktree "):].strip())
                if real != self._repo_dirs[0] and real not in found:
                    found.append(real)
        return tuple(found)

    def _locate(self, path, resolved=None):
        """Where `path` lies: `checkout` (this one), `repository` (a git dir the worktrees share), `sibling` (inside
        another worktree) or None."""
        real = self._real(path, resolved)
        here = self._repo_dirs[0]
        if real == here or real.startswith(here + os.sep):
            return "checkout"
        in_repository = any(real == d or real.startswith(d + os.sep) for d in self._repo_dirs)
        if ".git" in real.split(os.sep) and in_repository:
            return "repository"
        if any(real == s or real.startswith(s + os.sep) for s in self._siblings):
            return "sibling"
        return "repository" if in_repository else None

    def _in_repository(self, path, resolved=None):
        """True when `path` is one of the repository's own places (`_repository_dirs`) or lies under one."""
        real = self._real(path, resolved)
        return any(real == d or real.startswith(d + os.sep) for d in self._repo_dirs)

    def _nested(self, folder):
        """True when a repository other than the checkout's holds `folder`: a `.git` at it or between it and the root."""
        here, at = self._repo_dirs[0], self._real(folder)
        while at != here and at.startswith(here + os.sep):
            if os.path.lexists(os.path.join(at, ".git")):
                return True
            at = os.path.dirname(at)
        return False

    def _git_read(self, args):
        """Git's answer in this checkout as a proof's own git gets it: the repository variables a caller set
        (a commit's private index) are dropped, as `run_proof` drops them from a proof's environment."""
        env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_ENV}
        result = subprocess.run(["git"] + args, cwd=self.root, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=env)
        if result.returncode != 0:
            raise RuntimeError("git %s failed in %s: %s" % (args[0], self.root, result.stderr.strip()))
        return result.stdout

    def _known(self, rel):
        """True when a tracked file anywhere in the repository is `rel` or lies under it, in any letter case:
        the platform lower-cases a manifest's paths. None when the index cannot be read."""
        if not hasattr(self, "_tracked_all"):
            try:
                low = {n.lower() for n in self._git_read(["ls-files", "-z"]).split("\0") if n}
                self._tracked_all = low | {n.rsplit("/", i)[0] for n in low for i in range(1, n.count("/") + 1)}
            except (RuntimeError, OSError):
                self._tracked_all = None
        return None if self._tracked_all is None else rel.lower() in self._tracked_all

    def _ignored(self, rels):
        """The members of `rels`, checkout paths no tracked file covers, that are a close's exhaust: paths git
        ignores in a close's own checkout (caches, logs, scratch, locks). A close proves in a fresh checkout
        that only its own proofs fill, so a key that held them moved mid-run and missed every lookup made
        before a sibling proof wrote. Any other checkout may hold ignored files a clean one lacks (local
        settings, a session's logs), so there nothing is exhaust and every path is keyed. Memoized per run;
        when git cannot answer, nothing is ignored."""
        if not self._close_checkout or self._known("") is None:
            return set()
        rels = [r for r in rels if not self._known(r)]
        todo = sorted({r for r in rels if r not in self._ignore})
        if todo:
            hit = set()
            try:
                result = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=self.root,
                                        input=("\0".join(todo) + "\0").encode("utf-8"), capture_output=True)
                if result.returncode in (0, 1):
                    hit = {x for x in result.stdout.decode("utf-8", "replace").split("\0") if x}
            except OSError:
                pass
            for r in todo:
                self._ignore[r] = r in hit
        return {r for r in rels if self._ignore.get(r)}

    def _hidden(self, rels):
        """The members of `rels` that are exhaust: present here, untracked and ignored."""
        if not self._close_checkout:
            return set()
        return self._ignored([r for r in rels if os.path.lexists(os.path.join(self.root, r))])

    def _listing(self, rel):
        """The entries of a directory that are not exhaust; None when it is no directory or is exhaust itself
        (a proof lists the randomly named scratch folder it made; the next run lists another)."""
        full = os.path.join(self.root, rel)
        if not os.path.isdir(full) or self._hidden([rel]):
            return None
        names = [n for n in os.listdir(full) if n != "__pycache__"]
        hidden = self._hidden([rel + "/" + n for n in names])
        return sorted(n for n in names if rel + "/" + n not in hidden)

    def _repo_state(self, fresh=False):
        """What a git command in this checkout can print of the repository, untracked files aside: HEAD's commit and
        tree, every ref, the config, and the tracked files' uncommitted changes as status codes and as content (a
        second edit of a modified file moves no code). Untracked files are their own scope (`_untracked_names`): the
        runner's own stamp, slots and pass rows are untracked, and counting them would rerun every git-using proof.
        Read once per run (`snapshot_git`, before any proof starts); `fresh` reads it again without keeping it."""
        if fresh or not hasattr(self, "_state"):
            try:
                reads = (["status", "--porcelain", "--untracked-files=no"], ["diff", "HEAD", "--binary"],
                         ["for-each-ref", "--format=%(refname) %(objectname)"], ["config", "--list"],
                         ["rev-parse", "HEAD^{tree}"], ["rev-parse", "HEAD"])
                with ThreadPoolExecutor(len(reads)) as pool:
                    state = [hashlib.sha256(out.encode("utf-8")).hexdigest() for out in pool.map(self._git_read, reads)]
            except (RuntimeError, OSError):
                state = None
            if fresh:
                return state
            self._state = state
        return self._state

    def _tracked_names(self, fresh=False):
        """A digest of the names the index tracks: what a proof that only lists files can observe of git.
        Read once per run like `_repo_state`; `fresh` reads it again without keeping it."""
        if fresh or not hasattr(self, "_names_digest"):
            try:
                names = hashlib.sha256(self._git_read(["ls-files", "-z"]).encode("utf-8")).hexdigest()
            except (RuntimeError, OSError):
                names = None
            if fresh:
                return names
            self._names_digest = names
        return self._names_digest

    def _untracked_names(self, fresh=False):
        """A digest of the untracked files git does not ignore: what `git status` and `ls-files -o` list and
        `_repo_state` leaves out. Read once per run like `_repo_state`; `fresh` reads it again without keeping it."""
        if fresh or not hasattr(self, "_untracked"):
            try:
                names = hashlib.sha256(self._git_read(["ls-files", "-o", "--exclude-standard", "-z"]).encode("utf-8")).hexdigest()
            except (RuntimeError, OSError):
                names = None
            if fresh:
                return names
            self._untracked = names
        return self._untracked

    def snapshot_git(self):
        """Read the git state the keys use, before the run's proofs start. A pass records only when the state
        its run ended on is this one (`observed_parts`): a proof that ran across a change observed a state
        no row can name."""
        for method in _GIT_SCOPES.values():
            getattr(self, method)()

    def observed_parts(self, proof, manifest, fresh=False, memo=None, files=None):
        """The parts of the observed key, each a short digest, or None when the proof has no manifest, its
        reads are opaque, or the git state its git use observes cannot be read or (`fresh`, at record time)
        is no longer the one the run started on. A pass is reused while every
        part holds: the runner's launch code (`_launch_digest`) and both read recorders' bytes; each file it read, each unrecorded child's static reach;
        each path it probed, as absent, a file, a directory or a link; each directory it listed; and the repository state its git use
        can observe (`_git_scope`): `repo` is the commit, every ref, the config and the tracked files' changes
        (`_repo_state`), `tracked` the names the index holds, `untracked` the unignored untracked names; a
        script child whose text runs git has `repo` and `untracked`; each object printed by full id is its presence
        (`_objects_state`); `sibling` (another worktree) never reuses. A close's exhaust counts as absent in the probes, files and listings (`_ignored`).
        What a path is on disk is looked up once per `memo` (the run's own when not `fresh`): a case pool's
        cases share thousands of import probes, and each case's key asks about all of them. A `files` list receives
        the keyed file rows ([path, digest, tracked]) the files part hashes."""
        if not isinstance(manifest, dict) or manifest.get("opaque"):
            return None
        memo = self._on_disk if memo is None and not fresh else {} if memo is None else memo

        def on_disk(r):
            if r not in memo:
                full = os.path.join(self.root, r)
                memo[r] = None if not os.path.lexists(full) else \
                    "link" if os.path.islink(full) else "dir" if os.path.isdir(full) else "file"
            return memo[r]

        def digest(r):
            if not fresh:
                return self._digest(r)
            if ("digest", r) not in memo:
                memo["digest", r] = self._read_digest(r)
            return memo["digest", r]
        rel = _norm(os.path.relpath(proof, self.root))
        used = set(manifest.get("files", [])) | {rel}
        scopes = manifest.get("git")
        scopes = {"repo"} if scopes is True else set(scopes or ())
        for script in manifest.get("static", []):
            if script.endswith(_CODE_EXT) or self._text(script).startswith("#!"):
                loaded = self._expand({script} | self._names(script), script)
                used |= loaded
                # No recorder follows a non-Python child, so no spawn event names the git its script runs.
                if any(_GIT_WORD_RE.search(self._code_of(r)) for r in loaded | {script}
                       if r.endswith(_CODE_EXT) or r == script):
                    scopes |= {"repo", "untracked"}
            else:
                # A data argument runs nothing: its bytes are the input, not the code its prose names.
                used.add(script)
        probed = manifest.get("probes", [])
        present = {r for r in used | set(probed) if on_disk(r)}
        hidden = self._ignored(present)
        if "sibling" in scopes:
            return None  # another worktree's HEAD and files: no key of this checkout names them, so the pass never reuses
        held = {k: getattr(self, m)() for k, m in _GIT_SCOPES.items() if k in scopes}
        if any(v is None for v in held.values()):
            return None
        if fresh and any(getattr(self, _GIT_SCOPES[k])(fresh=True) != v for k, v in held.items()):
            return None  # the state moved while the run's proofs ran: which one this proof saw is unknown
        state, names, loose = held.get("repo"), held.get("tracked"), held.get("untracked")

        def kind(r):
            return None if r not in present or r in hidden else on_disk(r)
        def listing(r):
            if not fresh:
                return self._listing(r)
            key = ("listing", r)
            if key not in memo:
                memo[key] = self._listing(r)
            return memo[key]
        runner = _runner_launch_digest() if fresh else self.runner
        recorder = [self._digest("<recorder>", os.path.join(_RECORDER_DIR, "sitecustomize.py")),
                    self._digest("<node-recorder>", _NODE_RECORDER)]
        behavior = _behavior_plan(self.root, [rel]).get("dependencies", {}).get(rel, {})
        keyed = sorted([r, None if r in hidden else digest(r), r in self.tracked] for r in used)
        if files is not None:
            files.extend(keyed)
        parts = {
            "runner": runner, "recorder": recorder,
            "files": keyed,
            "probes": [[r, kind(r)] for r in probed],
            "dirs": [[r, listing(r)] for r in manifest.get("dirs", [])],
            "state": state, "tracked": names, "untracked": loose,
            "behavior": [behavior.get("inputKey"), behavior.get("bindingHash")]}
        if manifest.get("objects"):
            parts["objects"] = self._objects_state(manifest["objects"], memo)
        return {k: hashlib.sha256(json.dumps(v).encode("utf-8")).hexdigest()[:12] for k, v in parts.items()}

    @staticmethod
    def _combine(parts):
        """The key over observed parts. The scheme number retires every row an earlier key recorded: a row's
        key was taken over what that scheme held, and a hash that happens to agree proves nothing about it."""
        return hashlib.sha256(json.dumps([_KEY_SCHEME, sorted(parts.items())]).encode("utf-8")).hexdigest()

    def observed_key(self, proof, manifest, fresh=False):
        """The reuse key over what the proof's last pass used (`manifest`), or None when it has none or
        its reads are opaque: `observed_parts` combined."""
        parts = self.observed_parts(proof, manifest, fresh)
        return None if parts is None else self._combine(parts)

    def lazy_key(self, proof):
        """`key(proof)` as a callable that computes it on first call, or None when the proof has no static key. A
        reuse check passes it to `hit`, which calls it only for a row the observed key cannot judge."""
        name = os.path.basename(proof)
        if name in _DIR_SCANNING and not _corpus_dirs(name):
            return None
        memo = []

        def key():
            if not memo:
                memo.append(self.key(proof))
            return memo[0]
        return key

    def key(self, proof, fresh=False):
        """The proof's reuse key. `fresh` rereads every input instead of the run's memoized digests:
        a pass is recorded only when that equals the key taken before the run."""
        name = os.path.basename(proof)
        if name in _DIR_SCANNING and not _corpus_dirs(name):
            return None
        digest = self._read_digest if fresh else self._digest
        rows = sorted([rel, digest(rel), rel in self.tracked] for rel in self.reach(proof))
        runner = _runner_launch_digest() if fresh else self.runner
        rel = _norm(os.path.relpath(proof, self.root))
        behavior = _behavior_plan(self.root, [rel]).get("dependencies", {}).get(rel, {})
        blob = json.dumps([runner, rows, behavior.get("inputKey"), behavior.get("bindingHash")]).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def _checkout_name(self):
        """The name this checkout's rows carry. A close proves HEAD in a temp checkout it removes when it ends; its
        rows are the ones the next close reuses, so they record under one name that outlives the directory and the
        next close replaces."""
        return _CLOSE_CHECKOUT if self._close_checkout else os.path.normcase(os.path.abspath(self.root))

    def _rows(self, store, proof):
        """The stored rows of `proof` (a repository path) in `store` (`self.path` or `self.case_path`), parsed once
        per file state: a run asks twice per proof. The file's (mtime, size, inode) says when a peer or `record`
        replaced it. Callers read the rows and never change them."""
        path = _store_file(store, _norm(os.path.relpath(proof, self.root)))
        try:
            info = os.stat(path)
        except OSError:
            return {}
        state = (info.st_mtime_ns, info.st_size, info.st_ino)
        with _ROWS_LOCK:
            held = _ROWS_MEMO.get(path)
            if held is None or held[0] != state:
                rows = read_json_salvage(path)
                held = _ROWS_MEMO[path] = (state, rows if isinstance(rows, dict) else {})
            return held[1]

    def reusable_cases(self, proof):
        """{case name: manifest} of the proof's `_case_pool` cases whose recorded pass still holds: the case's
        observed key, over what it and the proof's module-level code read, is unchanged (`observed_parts`)."""
        prefix, found = _norm(os.path.relpath(proof, self.root)) + "::", {}
        for name, entry in self._rows(self.case_path, proof).items():
            if not name.startswith(prefix):
                continue
            for row in entry if isinstance(entry, list) else []:
                manifest = _unpack_manifest(row) if isinstance(row, dict) and row.get("okey") else None
                parts = self.observed_parts(proof, manifest) if manifest else None
                if parts is not None and self._combine(parts) == row["okey"]:
                    found[name[len(prefix):]] = manifest
                    break
        return found

    def record_cases(self, proof, manifests):
        """Record a pass row per case from `manifests` ({case name: manifest}); a case whose key cannot be
        taken (opaque reads, git state moved) records none and runs next time."""
        rel = _norm(os.path.relpath(proof, self.root))
        rows, memo = {}, {}
        for case, manifest in manifests.items():
            keyed = []
            parts = self.observed_parts(proof, manifest, fresh=True, memo=memo, files=keyed) if manifest else None
            if parts:
                rows[rel + "::" + case] = {"okey": self._combine(parts), "parts": parts,
                                           "zm": _pack_manifest(dict(manifest, digests=_file_digests(keyed)))}
        if rows:
            self._store(self.case_path, rows)

    def variants(self, proof):
        """The proof's recorded passes, newest first."""
        entry = self._rows(self.path, proof).get(_norm(os.path.relpath(proof, self.root)))
        entry = [entry] if isinstance(entry, dict) else entry
        return [row for row in entry if isinstance(row, dict)] if isinstance(entry, list) else []

    def _matched(self, proof, key):
        """The proof's row when its key still holds, else None. `key` is the static key or a callable giving it
        (`lazy_key`), which only a row with no observed key calls. A row with an observed key is judged by it
        alone: the static reach cannot see a listed directory gain an entry or git state move, so a static
        match there would reuse a stale pass. A row without one is judged by the static key, unless the
        observable run recorded no manifest (`observed` is False): its reads are unknown, so no key proves it.
        A miss is explained by this checkout's own newest row (`_checkout_name`): the parts that moved, and for a
        files part the files (`miss_files`) when the row packs their digests; an own pass whose reads could not be
        keyed is `unobserved`. A checkout with no row of its own is explained by the newest row of another checkout,
        each cause marked `other:` (`other:scheme` for a row recorded under an earlier key scheme, `other:static` for
        one with no observed key): the other checkout's inputs differ by whatever they held, so no file is charged
        to this one. The newest observed row's files count toward `readers`."""
        rel, diff, mine = _norm(os.path.relpath(proof, self.root)), None, self._checkout_name()
        elsewhere = None
        for row in self.variants(proof):
            own = row.get("checkout") in (mine, None)
            okey = row.get("okey")
            if okey:
                manifest, keyed = _unpack_manifest(row), []
                if rel not in self._reads and isinstance(manifest, dict):
                    self._reads[rel] = set(manifest.get("digests") or manifest.get("files") or ())
                parts = self.observed_parts(proof, manifest, files=keyed)
                if parts is not None and self._combine(parts) == okey:
                    self.misses.pop(rel, None)
                    self.miss_files.pop(rel, None)
                    return row
                if diff is None and own:
                    diff = self._moved(row, parts)
                    digests = manifest.get("digests") if "files" in diff and isinstance(manifest, dict) else None
                    if isinstance(digests, dict):
                        now = _file_digests(keyed)
                        self.miss_files[rel] = sorted(f for f in set(digests) | set(now) if digests.get(f) != now.get(f))
                elif elsewhere is None and not own:
                    elsewhere = ["other:" + name for name in self._moved(row, parts)]
            elif row.get("observed") is False:
                if diff is None and own:
                    diff = ["unobserved"]
            elif (static := key() if callable(key) else key) and row.get("key") == static:
                self.misses.pop(rel, None)
                self.miss_files.pop(rel, None)
                return row
            else:
                if diff is None and own:
                    diff = ["static"]  # the static key keeps no parts, so which input moved is unknown
                elif elsewhere is None and not own:
                    elsewhere = ["other:static"]
        diff = diff or elsewhere
        if diff:
            self.misses[rel] = diff
        return None

    def _moved(self, row, parts):
        """The names of what moved between `row`, an observed row recorded earlier, and the `parts` this checkout
        takes now: `unrecorded` when this checkout cannot take its key now, `scheme` when the row's key was taken
        under another key scheme (its parts, when it kept any, no longer combine to it, so no part names the miss)
        or every part holds, else the parts that differ."""
        if parts is None:
            return ["unrecorded"]
        then = row.get("parts") or {}
        if self._combine(then) != row.get("okey"):
            return ["scheme"]
        return [k for k in sorted(set(parts) | set(then)) if parts.get(k) != then.get(k)] or ["scheme"]

    def readers(self, files):
        """{file: how many of the proofs this cache judged read it}, for each of `files`."""
        return {f: sum(1 for read in self._reads.values() if f in read) for f in files}

    def observations(self, proof, key, cases=None):
        row = self._matched(proof, key) or {}
        rows = row.get("observations", [])
        if cases and any(not any(r.get("caseId") == c["caseId"] and r.get("inputKey") == c["inputKey"]
                                 and r.get("status") == "pass" for r in rows) for c in cases):
            return None
        return rows

    def hit(self, proof, key, cases=None):
        return bool(key) and self._matched(proof, key) is not None and self.observations(proof, key, cases) is not None

    def record(self, proof, key=None, observations=None, manifest=None, observable=False):
        key = key or self.key(proof)
        if not key:
            return
        keyed = []
        parts = self.observed_parts(proof, manifest, fresh=True, files=keyed) if manifest else None
        okey = self._combine(parts) if parts else None
        row = {"key": key, "observations": observations or []}
        if okey:
            row.update(zm=_pack_manifest(dict(manifest, digests=_file_digests(keyed))), okey=okey, parts=parts)
        if observable:
            # A manifest whose key could not be taken (git state unreadable or moved mid-run) proves nothing:
            # only an opaque one, whose reads the static key covers, falls back to that key.
            row["observed"] = manifest is not None and (okey is not None or bool(manifest.get("opaque")))
        self._store(self.path, {_norm(os.path.relpath(proof, self.root)): row})

    def _store(self, store, new):
        """Write each {name: row} of `new` into the store folder `store` as its newest row for this checkout. A
        proof's rows (and its cases', named `proof::case`) share one file, so a record rewrites that file alone."""
        checkout = self._checkout_name()
        by_proof = {}
        for name, fields in new.items():
            by_proof.setdefault(name.partition("::")[0], {})[name] = fields

        def update(part):
            def apply(rows):
                # The file's own rows past the 30-day window or of a removed checkout go, and a row from before
                # manifests were packed is packed now. Other proofs' files wait for `sweep_pass_stores`.
                now = time.time()
                _sweep_rows(rows, now, {})
                for name, fields in part.items():
                    row = dict(fields, ts=round(now, 1), checkout=checkout)
                    rows[name] = [row] + [r for r in rows.get(name, [])
                                          if r.get("checkout") not in (checkout, None)
                                          and (r.get("key"), r.get("okey")) != (row.get("key"), row.get("okey"))]
            return apply
        try:
            os.makedirs(store, exist_ok=True)
            for proof, part in by_proof.items():
                update_json_locked(_store_file(store, proof), update(part))
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
    """Record the fastest whole pass, not machine load. Callers exclude reused and partial passes.
    Actual wall times stay in the run log. `__last_passes__` holds one dated whole-pass receipt per
    proof so growth can distinguish recovery from a fastest time that predates it. An assertion
    failure drops retry eligibility and its receipt; a timeout keeps them. Numeric readers use the floor."""
    def update(state):
        samples = state.setdefault("__last_passes__", {})
        if not isinstance(samples, dict):
            samples = state["__last_passes__"] = {}
        for path, result in results.items():
            name = os.path.basename(path)
            if result[0] == "pass":
                seconds = round(result[1], 1)
                samples[name] = {"ts": time.time(), "run_s": seconds}
                previous = state.get(name)
                if isinstance(previous, (int, float)) and not isinstance(previous, bool) and math.isfinite(previous):
                    seconds = min(seconds, previous) if previous >= 0 else seconds
                state[name] = seconds
            elif result[0] == "fail" and not _timed_out(result):
                state.pop(name, None)
                samples.pop(name, None)
    update_json_locked(durations_path(), update)


_LOG_LOCK_SECONDS = 2.0


def _record_run(row):
    """Append one run row to `harness_runs.json`, keeping the newest `_RUNS_KEPT`; best-effort. The row's
    `behavior` report (98% of the file's bytes, read by nobody from disk) stays in memory, not in the file."""
    def update(state):
        runs = state.get("runs")
        runs = [{k: v for k, v in r.items() if k != "behavior"} if isinstance(r, dict) else r
                for r in (runs if isinstance(runs, list) else [])]
        runs = runs + [{k: v for k, v in row.items() if k != "behavior"}]
        if len(runs) > _RUNS_KEPT:
            state["folded"] = _fold_runs(state.get("folded"), runs[:-_RUNS_KEPT])
        state["runs"] = runs[-_RUNS_KEPT:]
    update_json_locked(runs_path(), update)


def _fold_runs(folded, rows):
    """`folded` (the per-day history of runs no longer kept) plus `rows`, one entry per UTC day: runs, proofs selected
    and reused, wall, queue and pool seconds, runs per mode. The newest `_FOLDED_KEPT` days stay. A row with no
    timestamp folds into day `undated`; a row that is no object folds into nothing."""
    days = {d["day"]: d for d in (folded if isinstance(folded, list) else []) if isinstance(d, dict) and "day" in d}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("ts")
        day = time.strftime("%Y-%m-%d", time.gmtime(ts)) if isinstance(ts, (int, float)) and not isinstance(ts, bool) \
            else "undated"
        entry = days.setdefault(day, {"day": day, "runs": 0, "selected": 0, "reused": 0, "wall_s": 0.0, "queue_s": 0.0,
                                      "pool_s": 0.0, "modes": {}})
        entry["runs"] += 1
        for field in ("selected", "reused", "wall_s", "queue_s", "pool_s"):
            value = row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                entry[field] = round(entry[field] + value, 1) if isinstance(entry[field], float) or isinstance(value, float) \
                    else entry[field] + value
        mode = row.get("mode") if isinstance(row.get("mode"), str) else "unknown"
        entry["modes"][mode] = entry["modes"].get(mode, 0) + 1
    return [days[k] for k in sorted(days)][-_FOLDED_KEPT:]


def _fastest_passes(row, runs):
    """{proof: its fastest pass in `runs`}, a pass that ran (never reused) in any row but `row` itself."""
    fastest = {}
    for earlier in runs:
        if not isinstance(earlier, dict) or earlier.get("ts") == row.get("ts"):
            continue
        for p in earlier.get("proofs", []):
            if (p.get("status") == "pass" and not p.get("reused") and not p.get("cases_reused")
                    and p.get("run_s", 0) > 0):
                fastest[p["proof"]] = min(p["run_s"], fastest.get(p["proof"], p["run_s"]))
    return fastest


def _wall_floor(row, runs):
    """The fewest wall seconds the close's own run proofs have needed, and the basis it states: the larger of its
    longest run proof's fastest recorded pass and the run proofs' fastest passes summed over the pool width (`width`).
    It is the unloaded best case; `_time_line`'s load ratio says how far a close ran from it. A reused proof costs
    nothing; a proof with no recorded pass is left out and counted. None, with the reason, when the row names no
    width or no run proof has a recorded pass."""
    if not row.get("width"):
        return None, "no pool width recorded"
    fastest = _fastest_passes(row, runs)
    ran = [p["proof"] for p in row.get("proofs", []) if not p.get("reused") and not p.get("cases_reused")]
    known = [name for name in ran if name in fastest]
    if not known:
        return None, "no run proof has a recorded pass"
    longest = max(known, key=lambda name: fastest[name])
    summed = sum(fastest[name] for name in known)
    width = row["width"]
    return max(fastest[longest], summed / width), (
        "%.1fs summed over %d slot%s, longest %s %.1fs; %d of %d run proofs with no recorded pass"
        % (summed, width, "" if width == 1 else "s", longest, fastest[longest], len(ran) - len(known), len(ran)))


def _budget_line(row, runs):
    """The advisory close line: wall time against the floor this close's own proofs' recorded passes set
    (`_wall_floor`), the slowest bound proofs over the inner-loop budget, and the total queue wait. It never
    decides an exit code."""
    slow = sorted((p for p in row.get("proofs", []) if p.get("run_s", 0) > _PROOF_BUDGET_S),
                  key=lambda p: -p["run_s"])[:5]
    named = ", ".join("%s %.1fs" % (p["proof"], p["run_s"]) for p in slow) or "none"
    floor, basis = _wall_floor(row, runs)
    stated = ("floor %.0fs from fastest recorded passes (%s)" % (floor, basis) if floor is not None
              else "floor unknown (%s)" % basis)
    return ("harness_tests: close wall: %.1fs, %s; proofs over %ds: %s; queue wait %.1fs"
            % (row.get("wall_s", 0), stated, _PROOF_BUDGET_S, named, row.get("queue_s", 0)))


def _time_line(row, runs):
    """The close's time facts beside its wall line: pool seconds, the alone retries' count and seconds, and the
    load ratio: this run's summed proof time over the same proofs' fastest passes in `runs` (this run's own row
    excluded). A proof with no earlier pass stays out of both sums and is counted."""
    fastest = _fastest_passes(row, runs)
    ran = [p for p in row.get("proofs", []) if p.get("status") == "pass" and not p.get("reused")
           and not p.get("cases_reused") and p.get("run_s", 0) > 0]
    seen = [p for p in ran if p["proof"] in fastest]
    load = ("load x%.1f (%.1fs over %.1fs fastest, %d of %d proofs with history)"
            % (sum(p["run_s"] for p in seen) / sum(fastest[p["proof"]] for p in seen),
               sum(p["run_s"] for p in seen), sum(fastest[p["proof"]] for p in seen), len(seen), len(ran))
            if seen else "load unknown (0 of %d proofs with history)" % len(ran))
    return ("harness_tests: close time: pool %.1fs; alone retries %d, %.1fs; %s"
            % (row.get("pool_s", 0), row.get("alone_n", 0), row.get("alone_s", 0), load))


def _phase_line(row):
    """Wall phases before the run-log write; pool includes its workers' cache processing."""
    phases = row.get("phase_s") or {}
    return "harness_tests: close phases: " + "; ".join(
        "%s %.1fs" % (name.replace("_", " "), seconds) for name, seconds in phases.items())


def _growth_lines(row, runs, repo_root):
    """The close's growth and reuse lines from `tools/gate_growth.py`; none when it cannot run, since they
    decide nothing."""
    try:
        import gate_growth
        return gate_growth.close_lines(row, runs, growth_path(), os.path.join(repo_root, ".claude", "tests"),
                                       _PROOF_BUDGET_S, lasting=True)
    except Exception:  # noqa: BLE001 - an advisory fault never costs the close
        return []


def _growth_advisories(touched):
    try:
        import gate_growth
        return gate_growth.commit_lines(touched, growth_path())
    except Exception:  # noqa: BLE001 - an advisory fault never costs the commit
        return []


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
    _append_diagnostic(log, row, _recent_flaky)


_DIAGNOSTIC_ROWS = 400
_FLAKY_DAYS = 30


def _append_diagnostic(log, row, retain):
    """Locked JSONL history shared by flaky and known-red diagnostics. Past `_DIAGNOSTIC_ROWS` rows the file keeps
    `retain(rows)`: the rows its reader still counts, so a trim never changes what the reader reports."""
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with _file_lock.locked(log + ".lock", _LOG_LOCK_SECONDS):
            with open(log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(row + "\n")
            with open(log, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
            if len(rows) > _DIAGNOSTIC_ROWS:
                tmp = "%s.%d.tmp" % (log, os.getpid())
                with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write("".join(line + "\n" for line in retain(rows)))
                replace_path(tmp, log)
    except (OSError, TimeoutError):
        pass


def _json_rows(lines):
    """(line, row) for each line of a diagnostic log that holds a JSON object; the readers skip any other line."""
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield line, row


def _recent_flaky(lines):
    """The flaky rows `_flaky_history` counts: those inside its `_FLAKY_DAYS` window."""
    cutoff = time.time() - _FLAKY_DAYS * 86400
    return [line for line, row in _json_rows(lines) if isinstance(row.get("ts"), (int, float)) and row["ts"] >= cutoff]


def _fold_known_red(lines):
    """The newest half of `_DIAGNOSTIC_ROWS` known-red rows whole, and every older row folded into one row per proof
    holding its first `ts` and its `count`: all `_known_red_history` reads of them. One folded row per proof ever
    recorded red bounds the file."""
    keep = _DIAGNOSTIC_ROWS // 2
    folded = {}
    for _line, row in _json_rows(lines[:-keep]):
        if not row.get("proof"):
            continue
        entry = folded.setdefault(row["proof"], {"proof": row["proof"], "ts": None, "count": 0, "folded": True})
        ts = row.get("ts")
        if isinstance(ts, (int, float)) and (entry["ts"] is None or ts < entry["ts"]):
            entry["ts"] = ts
        entry["count"] += _row_count(row)
    return [json.dumps(entry) for entry in folded.values()] + lines[-keep:]


def _row_count(row):
    """The closes a known-red row stands for: a folded row's `count`, else one."""
    count = row.get("count", 1)
    return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 1


def _flaky_history(days=_FLAKY_DAYS):
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


def _known_red_history(proof, log=None):
    """(first ts, row count) of the closes that recorded `proof` red in the known-red log, before this run
    appends its own row; (None, 0) when the log has no earlier row. `log` names the file to read."""
    path = log or _beside_stamp(KNOWN_RED_LOG, "harness_known_red.jsonl")
    first, count = None, 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("proof") != proof:
                    continue
                count += _row_count(row)
                ts = row.get("ts")
                if isinstance(ts, (int, float)) and (first is None or ts < first):
                    first = ts
    except OSError:
        return None, 0
    return first, count


def _known_red_note(proof, log=None):
    """The clause a pre-existing red line carries: when the proof was first recorded red, and how many
    closes did before this one. A proof with no earlier row says so."""
    first, count = _known_red_history(proof, log)
    if not count:
        return " (no earlier row in harness_known_red.jsonl)"
    return " (first recorded red %s, %d earlier close%s)" % (
        time.strftime("%Y-%m-%d", time.gmtime(first)), count, "" if count == 1 else "s")


class RunOptions(NamedTuple):
    """How `_run_selected` runs. `cache` is a `_PassCache` or None; `root` is the checkout the proofs
    run in (None means the live one). A run is recorded to `harness_runs.json` only when `mode` names
    it; `row`, when given, also receives the recorded row. `stop` latches a newer close superseding this
    one: queued proofs and retries skip while already running proofs drain and cache their passes. `red`
    judges the proofs that failed again alone ({path: detail}): True when they make the run red, so its
    timeout retries are not run. None counts every such proof. `no_retry` names proofs that run once,
    in the pool, with no timeout or alone retry: the close's `--also` proofs, whose hangs would
    otherwise cost a serial timeout each."""
    cache: object = None
    root: object = None
    mode: object = None
    row: object = None
    stop: object = None
    paths: object = None
    base: object = "HEAD"
    behavior_out: object = None
    repo: object = None
    red: object = None
    no_retry: object = ()


def _run_selected(selected, verbose, statuses=None, details=None, options=RunOptions()):
    """Run the pool-safe proofs in one pool of `min(machine slots, _jobs())` width, slowest recorded
    first, then each `SERIAL` proof alone, each with `_timeout_for`. Every proof first takes a machine slot (`_take_slots`; a `SERIAL`
    proof takes all of them), so overlapping runs queue instead of timing each other out. An `options.cache`
    (`_PassCache`) reuses a proof whose reach is unchanged since its last pass, checked again once
    the slot is taken so a pass a peer run landed meanwhile counts, and records each pass as it lands
    unless a reached input moved since its key was taken. A Python proof the cache cannot reuse whole
    skips the `_case_pool` cases whose own pass rows hold; its pass then rests on their recorded reads
    too, and its time, which covers only some cases, is not recorded as the proof's duration.
    Each passing proof feeds the run's live pace (`_LivePace`), which stretches every running proof's deadline.
    An assertion failure with a recorded pass is re-run once alone, holding every machine slot: a pass
    there is reported FLAKY and logged, not red, because a shared temp race is a runner condition; a second
    failure is red. A timeout is re-run once on one slot, even with no recorded pass, at `_LOAD_FACTOR` times
    its timeout, or at its `proof_timeouts` cap, which already holds that headroom; its pass is FLAKY unless the
    proof had no recorded duration. An assertion failure with no recorded pass is
    not re-run, because a known red would only repeat. The run takes the machine once for its serial proofs and
    assertion retries together (`_machine_hold`). Assertion failures retry first, shortest recorded first; once one fails alone and
    `options.red` agrees it makes the run red, no later retry runs: each could only tell flaky from red in a run
    that cannot pass. A timeout is then `skipped`; an assertion failure stays red with its pool output and a note
    that it was not retried alone. A proof in `options.no_retry` never retries. Past `HARNESS_DEADLINE` no proof starts or
    retries, and one the deadline cuts short is `skipped`, counted with the indeterminate ones.
    Returns (passed, failed, indeterminate, wall seconds); a `statuses` dict
    receives each proof's final status and a `details` dict its final detail. A run with an `options.mode`
    records its row (`mode`, wall and queue seconds, each proof's status, run and wait seconds, and whether
    it was reused) and the row is appended to `harness_runs.json`."""
    start = time.time()
    phases = {}
    _PACE.reset()
    cache, root, mode = options.cache, options.root, options.mode
    behavior_root = options.repo or root or getattr(cache, 'root', REPO_ROOT)
    plan = _behavior_plan(behavior_root, [_norm(os.path.relpath(p, behavior_root)) for p in selected])
    cases = {p: plan.get('cases', {}).get(_norm(os.path.relpath(p, behavior_root)), []) for p in selected}
    observations = {p: [] for p in selected}
    durations = durations_path()
    known = read_json_salvage(durations) if os.path.exists(durations) else {}
    keys = {}
    if cache:
        cache.snapshot_git()
    deadline = _deadline()
    reused = set()
    partial = {}  # {proof: cases reused} for a run that skipped `_case_pool` cases
    waits, wait_split = {}, {}

    def expected(path):
        value = known.get(os.path.basename(path), 0)
        return float(value) if isinstance(value, (int, float)) else 0.0

    stop = options.stop
    no_retry = set(options.no_retry or ())
    moved = ("skipped", 0.0, "not run: a newer close superseded this one")

    def attempt(path, timeout, exclusive=False):
        if stop and stop():
            return moved
        if cache and path not in keys:
            keys[path] = cache.lazy_key(path)
        lazy = keys.get(path)
        if lazy and (cache.hit(path, lazy, cases[path]) if cases[path] else cache.hit(path, lazy)):
            reused.add(path)
            if cases[path]:
                observations[path].extend(cache.observations(path, lazy, cases[path]))
            return ("pass", 0.0, "")
        key = lazy() if lazy else None

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

        held = {}

        def reusable():
            held.update(cache.reusable_cases(path))
            return sorted(held)
        launch_options = _launch_options(path, root, behavior_root, cases[path], observations[path], bool(key), reusable)
        reads = launch_options.get("reads_sink")
        timing = {}
        result = _launch(path, timeout, exclusive, recheck=recheck if key or stop else None, timing=timing, **launch_options)
        if stop:
            stop()
        waits[path] = waits.get(path, 0.0) + timing.get("wait_s", 0.0)
        for kind, seconds in timing.get("wait_split", {}).items():
            wait_split[kind] = wait_split.get(kind, 0.0) + seconds
        if held and path not in reused and result[0] != "skipped":
            partial[path] = len(held)
        else:
            partial.pop(path, None)
        if result[0] == "pass" and key and path not in reused and cache.key(path, fresh=True) == key:
            try:
                case_manifests = {}
                if reads and (reads.get("cases") or held):
                    # Each case's key holds what it read and what the proof read outside every case. The
                    # proof's own pass rests on all of it, the skipped cases' recorded reads included.
                    base = cache.manifest(path, {"events": reads.get("base"), "temp": reads.get("temp")})
                    for name, events in reads.get("cases", {}).items():
                        own = cache.manifest(path, {"events": events, "temp": reads["temp"]})
                        if base is not None and own is not None:
                            case_manifests[name] = _merge_manifests([base, own])
                    manifest = None if base is None or len(case_manifests) < len(reads.get("cases", {})) else \
                        _merge_manifests([base] + list(case_manifests.values()) + list(held.values()))
                else:
                    manifest = cache.manifest(path, reads) if reads else None
            except Exception:  # noqa: BLE001 - a manifest fault costs only the observed key, never the run
                manifest, case_manifests = None, {}
            observable = reads is not None
            if cases[path]:
                if all(r['status'] == 'pass' for r in observations[path]):
                    cache.record(path, key, observations[path], manifest=manifest, observable=observable)
            else:
                cache.record(path, key, manifest=manifest, observable=observable)
                if case_manifests:
                    cache.record_cases(path, case_manifests)
        return result

    done, done_lock = {"n": 0, "at": time.time(), "left": sum(expected(p) for p in selected), "ran": 0.0}, threading.Lock()

    def progressed(path, result):
        """Print `N of M proofs done` every `_PROGRESS_EVERY` proofs or minute on a run of at least
        `_PROGRESS_MIN`: a whole close otherwise prints nothing until it ends. Each line also states the
        recorded seconds the unfinished proofs need at this pool width, and the pace: elapsed over the
        recorded time of the proofs that ran, so a reader sees whether the run keeps its recorded schedule."""
        if result[0] == "pass" and path not in reused and path not in partial:
            _PACE.observe(result[1], _recorded_time(path, known))
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
    slots = _machine_slots()
    width = min(slots, _jobs()) if slots > 0 else _jobs()
    # Submitted slowest recorded first: the longest proof sets the run's floor, so it must take its
    # machine slot first: a short proof admitted ahead of it holds the slots and delays the longest proof.
    phase_start = time.time()
    phases["setup"] = phase_start - start
    with ThreadPoolExecutor(max_workers=width) as pool:
        futures = {pool.submit(
            lambda p=p: progressed(p, attempt(p, _timeout_for(p, known)))): p for p in pooled}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    phases["pool"] = time.time() - phase_start
    flaky, alone_n, alone_s = [], 0, 0.0
    retrying, red_alone, decided, judged = [], {}, None, 0

    def retry(index, path):
        nonlocal decided, judged, alone_n, alone_s
        timed_out = _timed_out(results[path])
        cold = timed_out and os.path.basename(path) not in known
        if not decided and len(red_alone) > judged:
            judged = len(red_alone)
            decided = options.red(red_alone) if options.red else True
            if decided:
                named = ", ".join(sorted(os.path.basename(p) for p in red_alone))
                rest = [_timed_out(results[p]) for p in retrying[index - 1:]]
                for kind, count in (("assertion", rest.count(False)), ("timeout", rest.count(True))):
                    if count:
                        _progress("%d %s retr%s not run: the run is already red (%s failed alone)"
                                  % (count, kind, "y" if count == 1 else "ies", named))
        if decided:
            named = ", ".join(sorted(os.path.basename(p) for p in red_alone))
            if timed_out:
                results[path] = ("skipped", 0.0, "not run: the run is already red (%s failed alone)" % named)
            else:
                status, elapsed, detail = results[path]
                results[path] = (status, elapsed, (detail or "").rstrip("\n")
                                 + "\n(red in the pool, not retried alone: the run is already red (%s failed alone))\n" % named)
            return
        _progress("%s retry %d of %d: %s; first failure: %s"
                  % ("timeout" if timed_out else "alone", index, len(retrying), os.path.basename(path),
                     _last_line(results[path][2])))
        again = attempt(path, _retry_timeout(path, known, timed_out), exclusive=not timed_out)
        if again[0] != "skipped" and not timed_out:
            alone_n, alone_s = alone_n + 1, alone_s + again[1]
        if again[0] == "skipped":
            if timed_out:
                results[path] = again
            return
        if again[0] == "pass" and not cold:
            flaky.append((path, results[path][2]))
            _record_flaky(path, results[path][2], again[1])
        if again[0] == "fail" and not timed_out:
            red_alone[path] = again[2]
        results[path] = again

    phase_start = time.time()
    with _machine_hold() if alone else nullcontext():
        phases["serial_wait"] = time.time() - phase_start
        phase_start = time.time()
        for path in alone:
            results[path] = progressed(path, attempt(path, _timeout_for(path, known)))
        phases["serial"] = time.time() - phase_start
        pool_s = time.time() - start
        # Assertion failures first, shortest first: they are the likely reds, and once one fails alone the run
        # cannot pass, so every retry after it, which can only tell flaky from red, does not run.
        retrying[:] = sorted((p for p in selected if not cases[p] and results[p][0] == "fail"
                              and (os.path.basename(p) in known or _timed_out(results[p]))
                              and p not in no_retry),
                             key=lambda p: (_timed_out(results[p]), expected(p)))
        # An assertion failure can be a race on shared state, so its retry holds the whole machine, inside the
        # serial proofs' hold when they took one. A timeout's retry takes one slot: the live pace already
        # stretched its deadline, and holding the machine would stall every other run there for one proof.
        asserting = [p for p in retrying if not _timed_out(results[p])]
        phase_start = time.time()
        with _machine_hold() if asserting else nullcontext():
            phases["retry_wait"] = time.time() - phase_start
            phase_start = time.time()
            for index, path in enumerate(retrying, 1):
                if path in asserting:
                    retry(index, path)
    for index, path in enumerate(retrying, 1):
        if path not in asserting:
            retry(index, path)
    phases["retry"] = time.time() - phase_start
    # A pass that skipped cases timed only some of them, and the next whole run's timeout comes from it.
    _record_durations({p: r for p, r in results.items()
                       if p not in reused and r[0] != "skipped" and not (p in partial and r[0] == "pass")})

    passed, failed, indeterminate = 0, 0, []
    if reused:
        print("harness_tests: %d of %d proofs reused from the pass cache (%s)"
              % (len(reused), len(selected), pass_store_dir()))
    label = {"pass": "OK", "cannot-run": "CANNOT-RUN", "fail": "FAIL", "skipped": "SKIPPED",
             "platform-skip": "SKIPPED-PLATFORM"}
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
            for line in _detail_excerpt(detail):
                print("    | " + line)
        if status == "pass":
            passed += 1
        elif status == "platform-skip":
            pass  # printed SKIPPED-PLATFORM above; a proof this platform cannot run is neither green nor missing
        elif status in ("cannot-run", "skipped"):
            indeterminate.append(path)
        else:
            failed += 1
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
    wall = time.time() - start
    if mode is not None:
        record = options.row if options.row is not None else {}
        record.update({'observations': receipts, 'behavior': behavior_report, 'behaviorAcceptance': behavior_acceptance})
        try:
            head = _git(cache.root if cache else options.repo or root or REPO_ROOT, ["rev-parse", "HEAD"]).strip()
        except (RuntimeError, OSError):
            head = ""
        record.update({
            "ts": time.time(), "session": os.environ.get("CLAUDE_CODE_SESSION_ID") or None, "mode": mode,
            "head": head, "selected": len(selected), "reused": len(reused),
            "queue_s": round(sum(waits.values()), 1), "queue_split_s": {k: round(v, 1) for k, v in wait_split.items()}, "width": width,
            "pool_s": round(pool_s, 1), "alone_n": alone_n, "alone_s": round(alone_s, 1),
            "proofs": [dict({"proof": os.path.basename(p), "status": results[p][0], "run_s": round(results[p][1], 1),
                             "wait_s": round(waits.get(p, 0.0), 1), "reused": p in reused},
                            **({"cases_reused": partial[p]} if p in partial else {}),
                            **({"miss": cache.misses[_norm(os.path.relpath(p, cache.root))]}
                               if cache and p not in reused and _norm(os.path.relpath(p, cache.root)) in cache.misses
                               else {})) for p in selected],
            "inputs": _input_totals(cache, selected, reused, results) if cache else {}})
        wall = time.time() - start
        phases["post"] = max(0.0, wall - sum(phases.values()))
        record.update(wall_s=round(wall, 1), phase_s={name: round(seconds, 3) for name, seconds in phases.items()})
        _record_run(record)
    # Every phase's time reaches the reader: a phase that only labels reds once outgrew the whole pool unseen.
    # A close prints its own phase line (`close phases:`).
    if mode != "close":
        _progress("phase times: %s; wall %ds" % (", ".join("%s %ds" % (name, seconds) for name, seconds in phases.items()
                                                           if name != "post" and seconds >= 1) or "none over 1s", wall))
    if wait_split and sum(wait_split.values()) >= 1:
        _progress("slot wait, summed over jobs: %s" % ", ".join("%s %ds" % item for item in sorted(wait_split.items())))
    return passed, failed, indeterminate, wall


def _input_totals(cache, selected, reused, results, top=30):
    """{file: [rerun seconds, reran, readers]} over the input files whose change made a selected proof rerun
    (`_PassCache.miss_files`), the `top` largest by rerun seconds. A proof two changed files reran counts under both;
    readers are the run's selected proofs that read the file (`_PassCache.readers`). `tools/gate_growth.py` prints them."""
    totals = {}
    for p in selected:
        if p in reused or p not in results:
            continue
        for f in cache.miss_files.get(_norm(os.path.relpath(p, cache.root)), ()):
            seconds, reran = totals.get(f, (0.0, 0))
            totals[f] = (seconds + float(results[p][1] or 0), reran + 1)
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1][0], kv[0]))[:top]
    readers = cache.readers([f for f, _ in ranked])
    return {f: [round(s, 1), n, readers.get(f, 0)] for f, (s, n) in ranked}


def _last_line(detail):
    return ((detail or "").strip().splitlines()[-1:] or [""])[0]


_FAILURE_MARK_RE = re.compile(r"FAIL|ERROR|AssertionError|not ok")
# tests/_platform_skip.py's marker: a proof that skipped on this platform asserted nothing, so it is never a pass.
# `node --test` in TAP mode prints a child's stdout line as a `# ` comment, so the marker may carry that prefix.
_PLATFORM_SKIP_RE = re.compile(r"^(?:# )?PLATFORM-SKIP-PROOF ", re.M)  # tests/_platform_skip.skip_proof
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
    _append_diagnostic(log, row, _fold_known_red)


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


def _never_committed(repo_root, rel):
    return not _git(repo_root, ["log", "--all", "-1", "--format=%H", "--", rel]).strip()


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
        corpus = sorted({_norm(d).strip("/") for dirs in PROOF_CORPUS.values() for d in dirs
                         if not _norm(d).startswith(".claude")} - {""})
        git("sparse-checkout", "set", "--cone", ".claude", *corpus)
        git("checkout", "-q", "-f", rev)
        for path in proofs:
            rel = _norm(os.path.relpath(path, repo_root))
            target = os.path.join(tmp, rel)
            if not os.path.isfile(target) and _never_committed(repo_root, rel):
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

        def rerun(i, path):
            at_rev = os.path.join(tmp, os.path.relpath(path, repo_root))
            if os.path.isfile(at_rev):
                _progress("classifying red %d of %d: rerun %s at %s" % (i, len(failing), os.path.basename(path), rev[:8]))
                status, _elapsed, detail = _launch(at_rev, root=tmp)
                if status == "fail":
                    # Preserve the public repo-root projection, including Python's
                    # escaped Windows repr; failure identity normalizes it below.
                    for there, here in ((tmp.replace('\\', '\\\\'), repo_root.replace('\\', '\\\\')),
                                        (tmp, repo_root),
                                        (tmp.replace('\\', '/'), repo_root.replace('\\', '/'))):
                        detail = (detail or '').replace(there, here)
                    reds[path] = detail

        # Side by side, each holding its own machine slot: one at a time cost the sum of every red's run.
        slots = _machine_slots()
        width = min(slots, _jobs()) if slots > 0 else _jobs()
        with ThreadPoolExecutor(max_workers=max(1, min(width, len(failing)))) as pool:
            for future in [pool.submit(rerun, i, path) for i, path in enumerate(failing, 1)]:
                future.result()
        return reds


def _new_reds(repo_root, base, reds, memo):
    """The proofs of `reds` ({path: detail}) whose failure is not pre-existing at `base`. Each proof reruns at
    base once: `memo` keeps its result ({path: base detail or None}) for the close's classification."""
    fresh = [p for p in reds if p not in memo]
    if fresh:
        found = _reds_at(repo_root, base, fresh)
        memo.update({p: found.get(p) for p in fresh})
    return [p for p in reds if not (memo.get(p) and _is_pre_existing(reds[p], memo[p], repo_root, repo_root))]


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
    branch; None when neither `<remote>/HEAD` nor `<remote>/main` resolves, and for `remote=None`: a close of a
    range that is already on the default branch (`--range-start`) subtracts nothing."""
    if remote is None:
        return None
    for ref in (remote + "/HEAD", remote + "/main"):
        if _git_soft(repo_root, ["rev-parse", "--verify", "-q", ref + "^{commit}"]):
            return ref
    return None


def default_base_covers(repo_root, base, head, remote="origin"):
    """True when `base` is `remote`'s default-branch commit and `head` contains it: a branch that merged the
    default branch may close against that commit, so the push guard accepts it as the base."""
    ref = _default_ref(repo_root, remote)
    if not base or not ref or base != _git_soft(repo_root, ["rev-parse", "--verify", "-q", ref + "^{commit}"]):
        return False
    return subprocess.run(["git", "-C", repo_root, "merge-base", "--is-ancestor", base, head],
                          capture_output=True).returncode == 0


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


def _selection_estimate(selected):
    """The close's pre-run line: how many proofs it selected and the recorded seconds they need at this pool
    width, before pass reuse. A proof with no recorded time is unknown, named as such, never counted as zero."""
    durations = durations_path()
    known = read_json_salvage(durations) if os.path.exists(durations) else {}
    timed = [known[os.path.basename(p)] for p in selected
             if isinstance(known.get(os.path.basename(p)), (int, float))]
    return "close: %d selected proof(s), ~%ds at recorded times on %d job(s), %d with no recorded time (unknown)" % (
        len(selected), sum(timed) / _jobs(), _jobs(), len(selected) - len(timed))


def _input_commits(repo_root, base, head, reached, remote="origin"):
    revision = [head] if _is_empty_base(repo_root, base) else _outgoing(repo_root, base, head, remote)
    return _git(repo_root, ["rev-list", "--first-parent", "--reverse"] + revision + ["--"] + sorted(reached)).splitlines()


def _outgoing(repo_root, base, head, remote="origin"):
    """The revision range of commits `head` adds over `base` that `remote`'s default branch lacks."""
    ref = _default_ref(repo_root, remote)
    return [base + ".." + head] + (["^" + ref] if ref else [])


def _first_red(repo_root, base, head, proof, detail, reached, *, commits=None):
    """Bisect this proof's first-parent input-changing commits, never the shared checkout. None once
    `HARNESS_DEADLINE` passes: a run the deadline skipped is no pass, so no commit can be named."""
    if commits is None:
        commits = _input_commits(repo_root, base, head, reached)
    return _first_reds(repo_root, {proof: (detail, commits)})[proof]


def _first_reds(repo_root, reds):
    """{proof: first red commit or None} for `reds` ({proof: (detail, candidate commits)}), bisected in lockstep:
    each step groups the proofs by the commit they test next and reruns each group at that commit in one checkout,
    side by side (`_reds_at`). One proof's whole bisect after another cost the sum of every bisect."""
    span = {proof: [0, len(commits) - 1] for proof, (_detail, commits) in reds.items()}
    found = {proof: (commits[0] if commits else None) for proof, (_detail, commits) in reds.items()}
    while True:
        active = [proof for proof, (low, high) in span.items() if low < high]
        if not active:
            return found
        deadline = _deadline()
        groups = {}
        for proof in active:
            groups.setdefault(reds[proof][1][sum(span[proof]) // 2], []).append(proof)
        for commit, proofs in groups.items():
            if deadline is not None and time.time() >= deadline:
                for proof in proofs:
                    _progress("bisect %s stopped: HARNESS_DEADLINE passed" % os.path.basename(proof))
                    found[proof], span[proof] = None, [0, 0]
                continue
            at = _reds_at(repo_root, commit, proofs)
            for proof in proofs:
                red = at.get(proof)
                if red is None and deadline is not None and time.time() >= deadline:
                    _progress("bisect %s stopped: HARNESS_DEADLINE passed" % os.path.basename(proof))
                    found[proof], span[proof] = None, [0, 0]
                    continue
                matches = red is not None and _is_pre_existing(reds[proof][0], red, repo_root, repo_root)
                _progress("bisect %s at %s: %s" % (os.path.basename(proof), commit, "red" if matches else "not this red"))
                low, high = span[proof]
                mid = (low + high) // 2
                span[proof] = [low, mid] if matches else [mid + 1, high]
                if span[proof][0] == span[proof][1]:
                    found[proof] = reds[proof][1][span[proof][0]]


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
        if found is None or found == merge:
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


def sweep_store_temp(store_dir, max_age_s=STALE_TEMP_AGE_S):
    """Remove `.tmp_*.json` files older than `max_age_s` from the shared stores' folder; return how many went.
    `update_json_locked` writes each store through one such file and renames it in place; a writer killed
    mid-dump leaves it, and nothing else reads it. A live writer touches its file within the dump."""
    cutoff, removed = time.time() - max_age_s, 0
    try:
        entries = list(os.scandir(store_dir))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.name.startswith(".tmp_") and entry.name.endswith(".json") and entry.is_file(follow_symlinks=False) \
                    and entry.stat(follow_symlinks=False).st_mtime <= cutoff:
                os.remove(entry.path)
                removed += 1
        except OSError:
            continue
    return removed


def run_close(repo_root, base=None, verbose=False, behavior_out=None, now=False, ranged=False, also=()):
    """Wait for this session's writers in this checkout, then prove the committed HEAD of that moment, once.

    A later commit never restarts the run: the private checkout keeps the started commit's results
    valid, and the push publishes that commit. A newer close supersedes this one, which stops at its
    next proof without a verdict. `now` skips the wait: a mid-drive red sweep gets the same
    pre-existing and FIRST RED classification while an author job still runs. `ranged` closes the commits
    from `base` to HEAD that the default branch already holds, so it subtracts nothing the default branch carries.
    `also` names proofs a ranged close adds to its selection and runs once, with no retry.
    """
    import watch_records
    swept = sweep_stale_proof_temp(repo_root)
    if swept:
        print("harness_tests: removed %d stale proof temp folder(s)" % swept)
    swept = sum(sweep_store_temp(folder) for folder in
                (os.path.dirname(os.path.abspath(passes_path())), pass_store_dir(), case_store_dir()))
    if swept:
        print("harness_tests: removed %d orphaned store temp file(s)" % swept)
    swept = sweep_pass_stores()
    if swept:
        print("harness_tests: removed %d dead pass-store file(s)" % swept)
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
            writers = watch_records.running_writers(os.environ.get("CLAUDE_CODE_SESSION_ID"), repo=repo_root,
                                                    own_launch=os.environ.get("SIDECAR_LAUNCH_ID") or None)
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
        return _close_once(repo_root, base, verbose, token, ranged=ranged, also=also,
                           **({"behavior_out": behavior_out} if behavior_out else {}))
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


def _working_tree_status(repo_root, snapshot, proofs):
    """{proof: status} of each red proof rerun in the shared checkout, side by side; a proof absent there is None.
    `_uncommitted_inputs` starts from this rerun, and one red's after another cost the sum of every rerun."""
    def run(proof):
        here = os.path.join(repo_root, *_norm(os.path.relpath(proof, snapshot)).split("/"))
        return _launch(here, root=repo_root)[0] if os.path.isfile(here) else None
    if not proofs:
        return {}
    slots = _machine_slots()
    width = min(slots, _jobs()) if slots > 0 else _jobs()
    with ThreadPoolExecutor(max_workers=max(1, min(width, len(proofs)))) as pool:
        return dict(zip(proofs, pool.map(run, proofs)))


def _uncommitted_inputs(repo_root, snapshot, proof, detail, here_status=None):
    """(needs, tried) for a red proof that passes in the shared checkout; None when it fails there too.
    `tried` are its inputs that are dirty, untracked or ignored there, from the files the failure names
    and then from its reach; each set is planted into the committed-HEAD checkout and the proof reruns.
    `needs` is the first set whose rerun passes, else empty: the two checkouts then differ by something
    else (the checkout's folder, or an input outside the reach), and naming `tried` would send the fix to
    the wrong files."""
    rel = _norm(os.path.relpath(proof, snapshot))
    here = os.path.join(repo_root, *rel.split("/"))
    if here_status is None:
        here_status = _launch(here, root=repo_root)[0] if os.path.isfile(here) else None
    if here_status != "pass":
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


def _also_paths(root, names):
    """The proofs `also` names in the checkout `root`: a repo-relative path or a bare proof file name. A name
    the revision has no file for prints one line and is skipped, so a row whose proof is gone cannot stop a close."""
    found = []
    for name in names:
        rel = _norm(name)
        if not rel.startswith(".claude/tests/"):
            rel = ".claude/tests/" + rel.rsplit("/", 1)[-1]
        path = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(path):
            print("harness_tests: --also names no proof at this revision: %s" % name)
        elif path not in found:
            found.append(path)
    return found


def _close_once(repo_root, base=None, verbose=False, token=None, behavior_out=None, ranged=False, also=()):
    """Prove the committed HEAD of this moment; 2 without a verdict when a newer close supersedes it.

    `ranged` (the main check's `--range-start` mode) runs every proof: no red stops another proof's retry,
    and a proof whose timeout repeats alone is a red no bisect runs on, since a timeout names no commit and a
    bisect costs a run per step. `also` names proofs a ranged close adds to its selection and runs once in
    the pool, with no timeout or alone retry."""
    stop = Event()

    def superseded():
        if token and not stop.is_set() and (read_json_salvage(close_run_path()) or {}).get("token") != token:
            stop.set()
        return stop.is_set()

    try:
        head = _git(repo_root, ["rev-parse", "HEAD"]).strip()
        remote, target = push_destination(repo_root)
        base = close_base(repo_root, base, push_ref="refs/remotes/%s/%s" % (remote, target), remote=remote)
        outgoing_of = None if ranged else remote
        paths = close_paths(repo_root, base, head, outgoing_of)
        update_json_locked(STAMP_PATH, lambda doc: doc.update(close={
            "head": head, "base": base, "verdict": "incomplete", "red": [], "ts": time.time()}), raising=True)
        proofs = discover(os.path.join(repo_root, ".claude", "tests"))
        with _revision_checkout(repo_root, head, proofs) as snapshot, \
                _policy_from(os.path.join(snapshot, ".claude")):
            roster_error = _roster_error()
            if roster_error:
                raise RuntimeError(roster_error)
            selected = select_for(snapshot, paths)
            also_paths = _also_paths(snapshot, also)
            selected += [p for p in also_paths if p not in selected]
            if not selected and any(not _is_record(p) for p in paths):
                raise RuntimeError("no proof bound to outgoing harness paths")
            statuses, details = {}, {}
            _progress(_selection_estimate(selected))
            cache = _pass_cache(snapshot, selected)
            row = {}
            empty_base, at_base = _is_empty_base(repo_root, base), {}

            def blocks(reds):
                """A red alone blocks this close unless its proof was never committed or it is pre-existing at base."""
                committed = {p: d for p, d in reds.items()
                             if not _never_committed(repo_root, _norm(os.path.relpath(p, snapshot)))}
                return bool(committed) and (empty_base or bool(_new_reds(snapshot, base, committed, at_base)))

            def stop_reds(reds):
                """A ranged close runs every retry: its reds are classified once, after the run, against the base."""
                return False

            _run_selected(selected, verbose, statuses, details,
                          RunOptions(cache, snapshot, "close", row, superseded, paths, base, behavior_out,
                                     red=blocks if not ranged else stop_reds, no_retry=frozenset(also_paths)))
            if superseded():
                print("harness_tests: a newer close superseded this one; it stops without a verdict", flush=True)
                return 2
            failing = [p for p in selected if statuses.get(p) == "fail"]
            red_started = time.time()
            untracked_red = [os.path.basename(p) for p in failing
                             if _never_committed(repo_root, _norm(os.path.relpath(p, snapshot)))]
            failing = [p for p in failing if os.path.basename(p) not in untracked_red]
            for name in untracked_red:
                print("UNCOMMITTED PROOF RED %s (not in this push)" % name)
            bisectable = [p for p in failing if not _timeout_detail(details[p])]
            if not empty_base:
                _new_reds(snapshot, base, {p: details[p] for p in bisectable}, at_base)
            base_reds = {p: at_base[p] for p in failing if at_base.get(p)}
            main_base, main_reds = _default_merge_base(snapshot, head, base, outgoing_of), {}
            if main_base:
                main_reds = _reds_at(snapshot, main_base, [p for p in bisectable if not (
                    p in base_reds and _is_pre_existing(details[p], base_reds[p], snapshot, snapshot))])
            for proof, detail in main_reds.items():
                if _is_pre_existing(details[proof], detail, snapshot, snapshot):
                    print("harness_tests: pre-existing red on the default branch at %s: %s%s"
                          % (main_base[:8], os.path.basename(proof), _known_red_note(os.path.basename(proof))))
                    base_reds[proof] = details[proof]
            new_red, old_red = [], []
            candidates = {}
            bound = bindings(snapshot, paths) if failing else {}
            uncommitted, unexplained, reach_of = {}, {}, {}
            here = _working_tree_status(repo_root, snapshot, [
                p for p in failing if not (p in base_reds and _is_pre_existing(details[p], base_reds[p], snapshot, snapshot))
                and not _timeout_detail(details[p])])
            for proof in failing:
                if proof in base_reds and _is_pre_existing(details[proof], base_reds[proof], snapshot, snapshot):
                    continue
                if _timeout_detail(details[proof]):
                    continue  # a timeout names no commit, and a bisect costs a run per step
                found = _uncommitted_inputs(repo_root, snapshot, proof, details[proof], here.get(proof))
                if found and found[0]:
                    uncommitted[proof] = found[0]
                    continue
                if found:
                    unexplained[proof] = found[1]
                reach = _PassCache(snapshot).reach(proof) & set(paths)
                reach |= {path for path, proofs in bound.items() if proof in proofs}
                candidates[proof] = _input_commits(snapshot, base, head, reach, outgoing_of)
                reach_of[proof] = reach
            if candidates:
                _progress("bisect starts: %d new red, %d candidate commits" % (
                    len(candidates), sum(len(commits) for commits in candidates.values())))
            firsts = _first_reds(snapshot, {p: (details[p], candidates[p]) for p in candidates})
            for proof in failing:
                name = os.path.basename(proof)
                if proof in base_reds and _is_pre_existing(details[proof], base_reds[proof], snapshot, snapshot):
                    print("harness_tests: pre-existing red at base %s: %s%s" % (base[:8], name, _known_red_note(name)))
                    _record_known_red(name, base, _last_line(details[proof]), _last_line(base_reds[proof]))
                    old_red.append(name)
                elif _timeout_detail(details[proof]):
                    new_red.append({"proof": name, "commit": None, "detail": details[proof], "cause": "timeout"})
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
                              "the cause is unknown: the checkout's location, or an input outside its reach; "
                              "next: the bisect below names the commit, or make the proof plant what it reads"
                              % (name, " even with %s planted" % ", ".join(tried) if tried else ""))
                        red["passesInWorkingTree"] = True
                    first = firsts.get(proof)
                    if first:
                        first = _side_commit(snapshot, first, proof, details[proof], reach_of[proof])
                    subject = _git(snapshot, ["show", "-s", "--format=%s", first]).strip() if first else "untraced"
                    print("FIRST RED %s at %s %s; next: fix this commit, or hand it to its owner's fix job"
                          % (name, first or "unknown", subject))
                    for trailer in (_git(snapshot, ["show", "-s", "--format=%(trailers:key=Claude-Session,valueonly)",
                                                    first]).split() if first else ()):
                        print("  Claude-Session: " + trailer)
                    red.update(commit=first, detail=details[proof])
                    new_red.append(red)
            if failing:
                _progress("red handling after the run: %ds for %d red"
                          % (time.time() - red_started, len(failing)))
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
                  "red": new_red, "preExisting": old_red, "cannotRun": incomplete, "uncommittedProofRed": untracked_red,
                  "paths": paths, "proofs": [os.path.basename(p) for p in selected],
                  "behavior": row.get('behavior'), "observations": row.get('observations', [])}
        update_json_locked(STAMP_PATH, lambda doc: doc.update(close=result), raising=True)
        dirty_red = sum(1 for red in new_red if red.get("uncommittedInputs"))
        print("harness_tests: close %s: %d paths, %d proofs, %d new red, %d need uncommitted inputs, %d pre-existing red"
              % (verdict, len(paths), len(selected), len(new_red) - dirty_red, dirty_red, len(old_red)))
        if verdict == "incomplete":
            status = {os.path.basename(p): statuses.get(p) or "not run" for p in selected}
            print("harness_tests: not passed or failed: " + ", ".join(
                "%s (%s)" % (name, status[name]) if name in status else name for name in incomplete))
        runs = read_json_salvage(runs_path()).get("runs", []) if os.path.exists(runs_path()) else []
        print(_budget_line(row, runs if isinstance(runs, list) else []))
        print(_time_line(row, runs if isinstance(runs, list) else []))
        print(_phase_line(row))
        for line in _growth_lines(row, runs if isinstance(runs, list) else [], repo_root):
            print(line)
        if verdict == "green" and not ranged:
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
    failure lines, unavailable coverage and changed inputs block.
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
    tested_head = _head(repo_root)
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
        print("harness_tests: no proof is bound to %s; add test_<name>*.py; the close runs the full battery" % ", ".join(in_scope))
        return 1
    statuses, details = {}, {}
    compare, label = (base, "base") if base else (tested_head, "HEAD")
    # HEAD is a "before" only while a tracked input or selected proof differs from it.
    # A never-committed proof alone does not make HEAD precede the change under test.
    carried = bool(not base and tested_head and not _git(
        repo_root, ["status", "--porcelain", "--untracked-files=no", "--"] + in_scope + selected).strip())
    at_compare = {}

    def blocks(reds):
        """A red alone ends the alone retries only when it is not pre-existing at the comparison revision."""
        if carried or not compare:
            return True
        try:
            return bool(_new_reds(repo_root, compare, reds, at_compare))
        except (RuntimeError, OSError):
            return True
    passed, failed, indeterminate, elapsed_total = _run_selected(
        selected, verbose, statuses, details, RunOptions(_pass_cache(repo_root, selected), repo=repo_root,
            mode=mode, paths=in_scope, base=base or 'HEAD', behavior_out=behavior_out, red=blocks))
    failing = [p for p in selected if statuses.get(p) == "fail"]
    skipped = [p for p in indeterminate if statuses.get(p) == "skipped"]
    if failing and carried:
        print("harness_tests: the given paths are committed at HEAD %s, so HEAD carries the change and excuses "
              "no red; pass --base <the revision before the change> to compare there" % tested_head[:8])
    elif failing and compare:
        try:
            missing = [p for p in failing if p not in at_compare]
            if missing:
                found = _reds_at(repo_root, compare, missing)
                at_compare.update({p: found.get(p) for p in missing})
            for path, head_detail in {p: at_compare[p] for p in failing if at_compare.get(p)}.items():
                tail_worktree = _last_line(details.get(path))
                tail_head = _last_line(head_detail)
                if not _is_pre_existing(details.get(path), head_detail, repo_root, repo_root):
                    print("harness_tests: %s is red at %s %s too, but fails differently or shows no failure "
                          "line; it keeps blocking"
                          "\n    worktree: %s\n    %s: %s"
                          % (os.path.basename(path), label, compare[:8], tail_worktree, label, tail_head))
                    continue
                print("harness_tests: pre-existing red at %s %s: %s%s\n    worktree: %s\n    %s: %s"
                      % (label, compare[:8], os.path.basename(path),
                         _known_red_note(os.path.basename(path)), tail_worktree, label, tail_head))
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
    tested_head = _head(repo_root)
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir)
    present = {os.path.basename(p) for p in discovered}

    # A worktree or sparse checkout can lack the excluded
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
    # A caller that asks (the publish battery) reuses recorded passes; a bare `--all` stays fresh.
    cache = _pass_cache(repo_root, selected) if os.environ.get("HARNESS_REUSE_PASSES") else None
    passed, failed, indeterminate, elapsed_total = _run_selected(
        selected, verbose, statuses, options=RunOptions(cache=cache, repo=repo_root, mode="all", behavior_out=behavior_out))
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
    parser.add_argument("--range-start", metavar="REV",
                        help="with --close, prove the commits from REV to HEAD that origin's default branch already holds; "
                             "REV is the close's base, ahead of its upstream and default-branch lookups")
    parser.add_argument("--also", nargs="+", metavar="PROOF",
                        help="with --close --range-start, add these proofs to the selection and run each once, no retry")
    parser.add_argument("--json", action="store_true", help="with --tiers, print the rows as JSON")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.base and not (args.close or args.for_paths):
        parser.error("--base requires --close or --for")
    if args.range_start and not args.close:
        parser.error("--range-start requires --close")
    if args.range_start and args.base:
        parser.error("--range-start replaces --base for --close: pass one of them")
    if args.also and not args.range_start:
        parser.error("--also requires --close --range-start")
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
        return run_close(args.repo, args.range_start or args.base, args.verbose, now=args.now,
                         ranged=bool(args.range_start), also=args.also or (), **extra)
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
