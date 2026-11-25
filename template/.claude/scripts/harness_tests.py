#!/usr/bin/env python3
"""Run harness proofs; --close proves committed HEAD before push; commits only receive budgeted advice.

    python3 .claude/scripts/harness_tests.py --close [--now] [--base REV] [--repo PATH]
    python3 .claude/scripts/harness_tests.py --close --range-start REV [--also PROOF ...] [--now] [--repo PATH]
    python3 .claude/scripts/harness_tests.py --for PATH [PATH ...]
    python3 .claude/scripts/harness_tests.py --proofs-for PATH [PATH ...]
    python3 .claude/scripts/harness_tests.py --all
    python3 .claude/scripts/harness_tests.py --tiers [--json]

Close selects proofs bound to every harness path the outgoing commits change that the push remote's
default branch does not already carry (`_push_range.push_destination`, `_push_range.close_paths`). It runs on a
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

from contextlib import contextmanager, nullcontext, redirect_stdout
import io
import math
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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

if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
from _hook_state import update_json_locked, read_json_salvage, replace_path, HARNESS_DIRS as DIRS, \
    SETUP_SURFACE_FILES as SURFACES, CODE_EXT as _CODE_EXT, \
    is_harness_code as _is_code  # noqa: E402
import _file_lock  # noqa: E402
import _git_run  # noqa: E402
import _push_range  # noqa: E402
from _push_range import _default_ref, _is_empty_base, _outgoing  # noqa: E402  (the planted proofs read `_default_ref`)

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "tools"))
import adaptation  # noqa: E402
import machine_slots as _slot_pool  # noqa: E402
from _proof_runner import attempt as _attempt, authority as _authority, catalog as _catalog, \
    evidence as _evidence  # noqa: E402

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
COMMIT_BUDGET_S, _PROOF_BUDGET_S = _catalog.COMMIT_BUDGET_S, _catalog.PROOF_BUDGET_S


def _beside_stamp(override, name):
    return override or os.path.join(os.path.dirname(os.path.abspath(STAMP_PATH)), name)


def _shared_path(claude_dir, name):
    """`harness_<name>` as every checkout of `claude_dir`'s repository shares it, under its common git dir;
    beside the stamp when git cannot name that dir. The pass cache lives here because its rows key on input
    bytes, so a pass recorded in one worktree holds in any other whose reached inputs match."""
    common = (_git_run.soft_text(["-C", claude_dir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                 timeout=None) or "").strip()
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


_PROOF_EXTS, _PATTERNS = _catalog.PROOF_EXTS, _catalog.PATTERNS
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
    `_TIMEOUT_SEC`. While the proof runs, the run's live pace (`attempt.LivePace`) stretches it."""
    name = os.path.basename(path)
    if name in _PROOF_TIMEOUTS:
        return _PROOF_TIMEOUTS[name]
    main = _part_main(path)
    if main and os.path.basename(main) in _PROOF_TIMEOUTS:
        return _PROOF_TIMEOUTS[os.path.basename(main)]
    recorded = _recorded_time(path, known)
    return max(_TIMEOUT_SEC, _LOAD_FACTOR * recorded) if recorded else _TIMEOUT_SEC


def _retry_timeout(path, known, timed_out):
    """An alone retry's timeout: `_timeout_for`, times `_LOAD_FACTOR` after a timeout unless a cap names the proof."""
    capped = os.path.basename(path) in _PROOF_TIMEOUTS
    return _timeout_for(path, known) * (_LOAD_FACTOR if timed_out and not capped else 1)


def _launch_options(path, root, behavior_root, cases, receipts, keyed, reusable):
    """`_launch`'s keywords for one proof `_run_selected` runs: the checkout it runs in, its behavior `cases` with
    their `receipts` sink (run from `behavior_root`), the read recorder's sink for a `keyed` Python or node proof,
    and for such a Python proof with no behavior cases, `reusable()`, the `_case_pool` cases it skips."""
    options = {"root": root} if root else {}
    if cases:
        options.update(behavior_cases=cases, receipt_sink=receipts, root=behavior_root)
    if keyed and path.endswith(_attempt.RECORDABLE):
        options["reads_sink"] = {}
        if path.endswith(".py") and not cases:
            options["reuse_cases"] = reusable
    return options


def _merge_adaptation_proof_config(claude_dir=_CLAUDE_DIR) -> None:
    """Merge `claude_dir`'s `adaptation.json` `proof_excluded` into `EXCLUDED`, `proof_heavy` into `HEAVY` and
    `proof_serial` into `SERIAL` and `proof_timeouts` into `_PROOF_TIMEOUTS`. A `proof_timeouts` value that is not a positive int is skipped with
    one stderr line, as is a `proof_corpus` value that is not a list of directories; `proof_excluded` values are reasons and need no
    further validation. `proof_bindings` fills `PROOF_BINDINGS`; `proof_corpus` fills `PROOF_CORPUS`."""
    policy = _catalog.Policy.read_worktree(claude_dir)
    EXCLUDED.update(policy.excluded)
    HEAVY.update(policy.heavy)
    PROOF_CORPUS.update(policy.corpus)
    PROOF_BINDINGS.update(policy.declared)
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

# {proof basename: directories it scans whole}, a project's own entries beside the catalog's `CORPUS`. A directory
# outside .claude joins the close's sparse checkout, so a proof that reads project data finds it there.
PROOF_CORPUS = {}


_merge_adaptation_proof_config()

discover = _catalog.discover


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
    text = _git_run.soft_text(["rev-parse", "--local-env-vars"], timeout=None,
                              env={name: None for name in _GIT_LOCAL_ENV_FALLBACK})
    names = tuple(line.strip() for line in (text or "").splitlines() if line.strip())
    return names or _GIT_LOCAL_ENV_FALLBACK


GIT_REPO_ENV = git_local_env_vars()
# The local variables whose value is no path: command-scope config (the runner's core.fscache comes as
# GIT_CONFIG_COUNT=1 on every Windows proof), switches and a ref prefix. Only a path names a repository; a name
# git adds later is read as one.
_GIT_NON_PATH_ENV = frozenset({"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_IMPLICIT_WORK_TREE",
                               "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX"})
_remove_temp_root = _attempt._remove_temp_root
_chmod_and_retry = _attempt._chmod_and_retry


def run_proof(path, timeout=None, root=None, *, behavior_cases=None, receipt_sink=None, reads_sink=None,
              reuse_cases=None, stretch=None, typed=False):
    """(status, seconds, detail), or the typed `AttemptResult` with `typed=True`. The timeout policy lives with
    the runner's proof tables, so it resolves here; everything else is `_proof_runner.attempt.run_proof`."""
    if timeout is None:
        timeout = _PROOF_TIMEOUTS.get(os.path.basename(path), _TIMEOUT_SEC)
    return _attempt.run_proof(path, timeout, root, behavior_cases=behavior_cases, receipt_sink=receipt_sink,
                              reads_sink=reads_sink, reuse_cases=reuse_cases, stretch=stretch, typed=typed)


def _default_launch(job):
    """The context's launch function, looked up here so a proof that stubs `run_proof` at module level still
    intercepts the scheduler's calls."""
    return run_proof(job.proof, job.timeout, job.root, behavior_cases=job.behavior_cases,
                     receipt_sink=job.receipt_sink, reads_sink=job.reads_sink, reuse_cases=job.reuse_cases,
                     stretch=job.stretch, typed=True)


def _run_context(options=None, *, root=None, stop=None, pace=None):
    """The one `RunContext` a run builds. The proof cwd resolves here, never at launch: a `--repo` run proves
    that checkout, and a run with none proves the runner's own."""
    proof_root = root or (options.root if options else None) or REPO_ROOT
    if stop is None and options is not None:
        stop = options.stop
    return _attempt.RunContext(
        root=proof_root, deadline=_deadline, pace=pace or _attempt.LivePace(), take=_take_slots,
        release=_slot_pool.release_all, slot_error=SlotStoreError, stop=stop, launch=_default_launch,
        default_timeout=lambda proof: _PROOF_TIMEOUTS.get(os.path.basename(proof), _TIMEOUT_SEC),
        inherited=_slot_pool.inherited())


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
    result = _git_run.run(args, cwd=repo_root, timeout=None)
    if result.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s"
                           % (args[0], repo_root, result.stderr.decode("utf-8", "replace").strip()))
    return result.stdout.decode("utf-8", "replace")


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
    # `.md` that carry pins.
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
    return _authority.digest_files(tree_entries(repo_root))


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
        close = {"close": doc["close"]} if "close" in doc else {}
        doc.clear()
        doc.update(close)
        doc.update({
            "ts": time.time(),
            "tree_hash": _authority.digest_files(files),
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


_norm = _catalog.norm
_named_for = _catalog.named_for
_part_main = _catalog.part_main
_behavior_plan = _catalog.behavior_plan


def _corpus_dirs(name):
    return _catalog.CORPUS.get(name) or PROOF_CORPUS.get(name, ())


def _is_record(path):
    return path.endswith(".jsonl")


def _open_catalog(repo_root, proofs=None):
    view = _catalog.View.worktree(repo_root)
    policy = _catalog.Policy(EXCLUDED, HEAVY, SERIAL, PROOF_BINDINGS, PROOF_CORPUS)
    ids = None if proofs is None else [_norm(os.path.relpath(p, repo_root)) for p in proofs]
    catalog = _catalog.Catalog.open(view, policy, proofs=ids)
    catalog.sources.parse_code = _code_text
    return catalog


def _full_bindings(catalog, obligations):
    return {rel: [catalog.view.full(p) for p in proofs] for rel, proofs in obligations.items()}


def bindings(repo_root, touched, include_excluded=False, proofs=None):
    return _full_bindings(c := _open_catalog(repo_root, proofs), c.bindings(touched, include_excluded))


def shipped_proofs_by_tool(repo_root, tools):
    return {tool: c.shipped_for(tool) for c in [_open_catalog(repo_root)] for tool in tools}


def shipped_proofs(repo_root, tool):
    return shipped_proofs_by_tool(repo_root, [tool])[tool]


def select_for(repo_root, touched, include_excluded=False, proofs=None):
    return _open_catalog(repo_root, proofs).full_selection(touched, include_excluded, behavior=_behavior_plan)


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
            if key and isinstance(_evidence.lookup(cache, proof, key, cases.get(rel)), _evidence.Hit):
                held.add(proof)
        except Exception:  # noqa: BLE001 - a cache fault leaves the proof to the budgets
            continue
    return held


def _commit_selection(repo_root, touched, cache=None):
    """(proven, unproven), each a list of bound proofs in binding order. A proof with a recorded pass under `cache`
    is proven for these inputs; every other bound proof is left to a run this commit never makes."""
    if not touched:
        return [], []
    bound = select_for(repo_root, touched)
    held = _recorded_passes(repo_root, bound, cache)
    return [p for p in bound if p in held], [p for p in bound if p not in held]


def unproven_command(touched):
    """The one command that proves a commit's paths now."""
    return "python3 .claude/scripts/harness_tests.py --proofs-for %s" % " ".join(touched)


def _unproven(touched, why, signature):
    """The one advisory for a commit whose bound proofs did not all pass here: why, and the command that
    proves it now. `signature` names the cause, so a repeat of the same cause in a session is one line."""
    line = "[harness commit] unproven, not passed: %s; `%s` runs them." % (why, unproven_command(touched))
    return {"signature": json.dumps(signature), "line": line}


def run_commit(repo_root, touched):
    """Report the bound proofs whose recorded pass still holds for these inputs, and the command for the rest.
    It launches no proof: the main check proves the landed range and `--proofs-for` proves a path set on request.
    It returns facts and advisories, never a commit verdict or stamp."""
    with _policy_from(os.path.join(repo_root, ".claude")), redirect_stdout(io.StringIO()):
        cache = _pass_cache(repo_root, list(touched))
        proven, left = _commit_selection(repo_root, touched, cache=cache)
    advisories = []
    if proven:
        advisories.append("[harness commit] already passed for these inputs: %s."
                          % ", ".join(os.path.basename(p) for p in proven))
    unproven = None
    if left:
        unproven = _unproven(touched, "%d bound proof(s) without a recorded pass" % len(left),
                             sorted(os.path.basename(p) for p in left))
        advisories.append(unproven["line"])
    advisories += _growth_advisories(touched)
    return {"proven": sorted(os.path.basename(p) for p in proven), "unproven": unproven, "advisories": advisories,
            "bound": sorted(os.path.basename(p) for p in proven + left)}


MAIN_CHECK_REF = "refs/remotes/origin/main-check-state"


def open_main_lines(repo_root, bound):
    """One `open on main` line per open main-check proof row named by `bound`, the basenames of the proofs the
    commit's paths bind. Reads the local state ref only: no fetch, and no import of main_check.py, so an edit to
    that script never reaches a commit. An absent or malformed state prints nothing. Takes names, not paths: the
    runner's launch key reaches what this function's callers reach, and binding costs its module values there."""
    try:
        shown = _git_run.run(["-C", repo_root, "show", MAIN_CHECK_REF + ":main_check.json"], timeout=10)
        if shown.returncode != 0:
            return []
        rows = json.loads(shown.stdout.decode("utf-8", "replace"))["rows"]
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
    own = os.path.join(repo_root, ".claude", "scripts", "harness_tests.py")
    temp_root = tempfile.mkdtemp(prefix="harness_commit_")
    try:
        code, output = _attempt._run_child([sys.executable, own if os.path.isfile(own) else os.path.abspath(__file__),
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


def tiers(repo_root):
    return _open_catalog(repo_root).tiers(tree_entries(repo_root), read_json_salvage(durations_path()))


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
    """The typed field when the attempt backend answered; a stubbed three-field result keeps the old read."""
    if isinstance(result, _attempt.AttemptResult):
        return result.status == "fail" and result.timeout
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


def _launch(path, timeout=None, root=None, recheck=None, timing=None, *, ctx=None, behavior_cases=None,
            receipt_sink=None, reads_sink=None, reuse_cases=None):
    """Start one proof after admission, through `_proof_runner.attempt.run`; only `SERIAL` proofs take all slots,
    if not already held. Nested runners spend the inherited lane serially. Queue time is outside the proof timeout
    and lands in `timing.wait_s`. A missing slot store is cannot-run; an expired run deadline is skipped."""
    if ctx is None:
        ctx = _run_context(root=root)
    job = _attempt.AttemptJob(proof=path, purpose="first", timeout=timeout, root=root or ctx.root,
                              recheck=recheck, behavior_cases=behavior_cases, receipt_sink=receipt_sink,
                              reads_sink=reads_sink, reuse_cases=reuse_cases,
                              exclusive=os.path.basename(path) in SERIAL)
    result = _attempt.run(ctx, job)
    if timing is not None:
        timing["wait_s"] = result.wait_s
        timing["wait_split"] = result.wait_split or {}
    return result


_code_text = _catalog.code_text
# What `git rev-parse` prints about where a repository lives, never what it holds, and the `ls-files` flags that
# keep it to the tracked names (a pathspec is a name too). Any other argument widens the scope to `repo`.
_GIT_LOCATION_ARGS = frozenset({"--local-env-vars", "--git-dir", "--git-common-dir", "--absolute-git-dir",
                                "--show-toplevel", "--show-cdup", "--show-prefix", "--show-superproject-working-tree",
                                "--is-inside-work-tree", "--is-inside-git-dir", "--is-bare-repository",
                                "--path-format=absolute", "--path-format=relative"})
# The parts of the repository a git command can print. `head` is what this checkout's HEAD names (branch, commit,
# tree), `changes` its tracked files' uncommitted content, `refs` every ref the repository holds (which any checkout
# moves), `config` the repository's own config, `worktrees` every worktree's path and HEAD; `tracked` and `untracked`
# are the names the index holds and the ones it does not. `repo` is the union older rows recorded and is read only
# for their manifests. A command whose reads are not known gets every part (`_GIT_WHOLE`). The object store's shape
# is no part: a full id's object is keyed by its presence (`_objects_state`), and `count-objects`' counts and sizes
# move with every commit anywhere, so keying them would rerun every git proof on every sibling commit.
_GIT_WHOLE = "head+changes+refs+config+worktrees+untracked"
# Commands that print HEAD's history when no revision is named, and refs when one is (or when a flag selects them).
_GIT_HEAD_COMMANDS = frozenset({"log", "show", "rev-list", "cat-file", "ls-tree", "blame", "archive",
                                "format-patch", "whatchanged", "shortlog", "rev-parse", "diff-tree", "diff-index",
                                "merge-base", "verify-commit", "verify-tag"})
# Commands whose job is to list or resolve refs.
_GIT_REF_COMMANDS = frozenset({"for-each-ref", "show-ref", "branch", "tag", "symbolic-ref", "describe", "name-rev",
                               "reflog", "cherry", "show-branch", "remote"})
# Flags that make a command read every ref, or a pattern of refs, instead of HEAD alone.
_GIT_REF_FLAGS = frozenset({"--all", "--branches", "--tags", "--remotes", "--reflog", "--glob", "--exclude",
                            "--exclude-hidden"})
# `git show <id>:<path>` and `git cat-file -p|-t|-s|-e <id>` with a full object id print bytes the id fixes, whatever
# the worktree, refs or config say: the key holds the object's presence (`_objects_state`), never a repository's state.
_GIT_OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?(?::.+)?\Z")
_GIT_OBJECT_FLAGS = {"show": frozenset(), "cat-file": frozenset({"-p", "-t", "-s", "-e"})}
# A word `git` or `git.exe` inside a command line: after a space, an operator, a quote or a path separator, and before
# a space, an operator or a quote (`cd root&&git log`, `"C:\Program Files\Git\cmd\git.exe" log`).
_GIT_WORD_RE = re.compile(r"""(?:^|[\s;&|(`'"/\\])git(?:\.exe)?(?=$|[\s;&|)`'"])""", re.IGNORECASE)
_SHELL_SPLIT_RE = re.compile(r"""[\s;&|()<>`'"]+""")
# pytest's cache steers only --lf/--ff/--nf/--sw, which no proof passes; every pytest proof rewrites it.
_TOOL_CACHE_RE = re.compile(r"(?:^|/)(?:\.pytest_cache|pytest-cache-files-[^/]*)(?:/|$)")
# The gitignored folders every hook and tool fills as it runs, and where a proof plants a fixture under a name unique
# to the run: a proof that wrote here did not leave an input behind for the next run.
_WRITE_EXHAUST = (".claude/logs/", ".claude/scratch/", "logs/", ".claude/cache/", ".claude/.cache/", ".claude/tmp/")


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
_KEY_SCHEME = 6  # what the observed key holds; a change retires every row recorded under another
# The git state a proof's key can hold, one entry per scope a manifest names: the `_PassCache` method that reads it
# (a digest, or None when git cannot be read). A new git input is one entry here and its reader. `repo` is the union
# older manifests recorded; a new manifest names the parts its git can print, so no row's key changes.
_GIT_SCOPES = {"head": "_head_state", "changes": "_changes_state", "refs": "_refs_state",
               "config": "_config_state", "worktrees": "_worktrees_state", "upstream": "_upstream_state",
               "tracked": "_tracked_names", "untracked": "_untracked_names", "repo": "_repo_state"}


def _git_refs_named(args):
    """True when a git command's arguments name a ref other than this checkout's HEAD, or every ref: a positional
    argument before `--` that is not HEAD and its history, or a flag that selects refs."""
    paths = False
    for arg in args:
        if arg == "--":
            paths = True
        elif paths:
            continue
        elif arg.startswith("-"):
            if arg.partition("=")[0] in _GIT_REF_FLAGS:
                return True
        elif not (arg == "HEAD" or arg.startswith(("HEAD^", "HEAD~", "HEAD@"))):
            return True
    return False


def _git_ls_files_scope(args):
    """`tracked` for the `ls-files` arguments that print only the index's names, `tracked+untracked+config` for the
    ones that also list the files the index does not hold (whose ignore rules come from the config), else None: an
    argument not read here can print index content or attributes, so the caller keys the whole state."""
    others = False
    for arg in args:
        if arg == "--":
            continue
        if not arg.startswith("-") or arg == "-":
            if arg.startswith(":"):
                return None  # a magic pathspec can read attributes
            continue
        if arg in ("-o", "--others"):
            others = True
        elif not arg.startswith("--"):
            flags = arg[1:]
            if "o" in flags:
                others = True
            if any(ch not in "zoc" for ch in flags):
                return None
        elif arg not in ("--exclude-standard", "--error-unmatch", "--cached", "--full-name", "--others"):
            return None
    if others:
        return "tracked+untracked+config" if "--exclude-standard" in args else None
    return "tracked"


_pack_manifest = _evidence.pack_manifest


def _cause(event):
    """A recorder event as a manifest's `why` keeps it: a path event whole, a spawn by its argv, each argument
    cut short (a `-c` program can run to kilobytes)."""
    kind, value = event
    if kind == "s":
        argv = value.get("argv") if isinstance(value, dict) else None
        return ["s", [a[:200] for a in argv if isinstance(a, str)][:12] if isinstance(argv, list) else []]
    return [kind, value]


_file_digests = _evidence.file_digests
_merge_manifests = _evidence.merge_manifests
_packed_row = _evidence.packed_row


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


_unpack_manifest = _evidence.unpack_manifest


_RUNNER_SOURCE = os.path.abspath(__file__)
_ATTEMPT_SOURCE = os.path.abspath(_attempt.__file__)
# Where the launcher's local imports resolve: its own package and the hooks it may start.
_LAUNCH_HELPER_ROOTS = (_SCRIPTS_DIR, os.path.join(_CLAUDE_DIR, "hooks"))
# The runner code a proof's launch depends on: the timeout that bounds it, the launch keywords and the delegation
# to the launcher. Scheduling (slots, order, width, progress, whether a failed run is retried alone, the live pace)
# and the pass store's bookkeeping stay out: neither changes what a passing proof's run returns.
_LAUNCH_POLICY = ("run_proof", "_timeout_for", "_retry_timeout", "_launch_options")


def _interpreter():
    """The interpreter a proof runs under: its real path (a venv resolves to the base) and full version."""
    return [os.path.realpath(sys.executable), sys.version]


def _launch_fingerprint():
    """The launch world every pass key holds: the launcher's implementation (`attempt`), the local helper it
    imports and the recorders it installs, the interpreter, the runner's launch-policy code and the declared proof
    policy (`_PROOF_TIMEOUTS`, `_TIMEOUT_SEC`, `_LOAD_FACTOR`). No checkout path, session id or proof name joins
    it, so a pass recorded in one checkout holds in a compatible one. None when an input cannot be read."""
    try:
        with open(_RUNNER_SOURCE, encoding="utf-8") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    policy = _evidence.launch_policy(source, _LAUNCH_POLICY)
    if policy is None:
        return None
    return _evidence.launch_key(
        _ATTEMPT_SOURCE, _LAUNCH_HELPER_ROOTS,
        [os.path.join(_RECORDER_DIR, "sitecustomize.py"), _NODE_RECORDER], _interpreter(),
        [policy, sorted(_PROOF_TIMEOUTS.items()), _TIMEOUT_SEC, _LOAD_FACTOR])


class _PassCache(_evidence.Reach):
    """A proof's pass, reused while every input it reaches is byte-identical and equally tracked.

    A proof reaches (`reach`): itself; every `.claude/` file whose basename, or whose code stem, its
    text names; its name-rule targets (`test_<stem>*`); its `proof_bindings` inputs; and the code any
    reached code loads, transitively, with the data files that each named or loaded code file names. Past
    the proof, only text that may run counts (`_code_text`): a comment or docstring loads nothing. The
    launch fingerprint joins every key (`_launch_fingerprint`): an edit outside the launch world keeps every pass. The reach is the binding relation read from the proof's side, so a
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
        view = _catalog.View.revision(repo_root, rev) if rev else _catalog.View.worktree(repo_root)
        policy = _catalog.Policy.read(view) if rev else _catalog.Policy(EXCLUDED, HEAVY, SERIAL, PROOF_BINDINGS, PROOF_CORPUS)
        super().__init__(view, policy.declared, policy.corpus)
        self.tracked = set(view.tracked)
        self.path, self.case_path = pass_store_dir(), case_store_dir()
        self._legacy = {self.path: passes_path(), self.case_path: case_passes_path()}
        self._digests = {}
        self._ignore, self.misses, self._on_disk = {}, {}, {}
        self.miss_files, self._reads = {}, {}
        self._close_checkout = os.path.basename(os.path.abspath(self.root)).startswith(_CHECKOUT_PREFIX)
        self._repo_dirs, self._outer_git, common = self._repository_dirs()
        self._shared_dir = os.path.normcase(os.path.abspath(os.path.join(common, "harness"))) if common else None
        self._siblings = self._other_worktrees()
        self._temp_real = self._real(tempfile.gettempdir())
        self.runner = _launch_fingerprint()
        self._behavior, self._behavior_lock = None, threading.Lock()

    def _behavior_dependencies(self):
        """Each behavior proof's binding, built once per cache: a proof's entry reads only its own rows and
        reach, so the whole-repo plan answers every proof, and building it per proof is a repo scan each."""
        with self._behavior_lock:
            if self._behavior is None:
                self._behavior = _behavior_plan(self.root).get("dependencies", {})
            return self._behavior

    def _behavior_of(self, rel, fresh):
        """`rel`'s behavior binding; `fresh` rebuilds it, as fresh digests re-read bytes, for a bound proof only."""
        dependencies = self._behavior_dependencies()
        if fresh and rel in dependencies:
            return _behavior_plan(self.root).get("dependencies", {}).get(rel, {})
        return dependencies.get(rel, {})

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
        return self.view.text(rel)

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
        presence), `shared` the runner's own state under the git dir (its pass store, durations and growth file:
        keyed by bytes, kind or entries, never as git state), and `git`, the parts of the repository its git use can
        observe (`_git_scope`; empty when git ran on no state of this checkout). `opaque` marks reads the cache
        cannot trust: a write into the checkout outside caches, logs and scratch, or a child it cannot resolve.
        `why` names, per git scope and for `opaque`, the first event that set it (`_cause`); no key reads it."""
        events, temp = sink.get("events"), sink.get("temp")
        if not events:
            # A recorded Python process opens at least its own script; no events means the recorder
            # never loaded, and a manifest built from nothing would reuse the pass forever.
            return None
        found = {"files": set(), "probes": set(), "dirs": set(), "static": set(), "objects": set(), "shared": set(),
                 "git": set(), "opaque": False}
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
        if isinstance(value, str) and self._shared_dir and (
                os.path.normcase(os.path.abspath(value)) == self._shared_dir
                or (os.path.normcase(os.path.abspath(value)) + os.sep).startswith(self._shared_dir + os.sep)):
            # The runner's own state lives under `harness/` in the shared git dir (a worktree's lies outside the
            # checkout). It is not git state: a read, stat, probe or listing of it is keyed by what it shows, and
            # a write is the runner's exhaust like any cache.
            if kind in ("r", "t", "p", "l"):
                found["shared"].add(("r" if kind == "t" else kind, os.path.normcase(os.path.abspath(value))))
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
            env = {k: None for k in GIT_REPO_ENV}
            for folder, ids in sorted(by_folder.items()):
                try:
                    result = _git_run.run(["-C", folder, "cat-file", "--batch-check"],
                                          input=("\n".join(ids) + "\n").encode("utf-8"), env=env, timeout=None)
                    lines = result.stdout.decode("utf-8", "replace").splitlines() if result.returncode == 0 else []
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
        if command == "config":
            # A read or write in one scope: the user's or the system's config is not this checkout's state, and a
            # proof cannot read either (its environment points both at the null device).
            if any(a in ("--global", "--system") or a.startswith(("--global=", "--system=")) for a in args):
                return ""
            return "config"
        if command == "merge-file":
            # It merges the files its arguments name and discovers no repository: on files outside this checkout's
            # repository it reads only merge config.
            paths = [a for a in args if not a.startswith("-")]
            if all(not self._in_repository(os.path.join(folder, p), resolved) for p in paths):
                return "config"
            return _GIT_WHOLE
        if command == "ls-files":
            scope = _git_ls_files_scope(args)
            return _GIT_WHOLE if scope is None else scope
        if command == "worktree":
            return "worktrees"
        if command == "status":
            # Status prints the branch's divergence from the one ref its config names as upstream, and nothing of
            # any other branch.
            blind = any(a in ("-uno", "--untracked-files=no") for a in args)
            return "head+changes+config+upstream" + ("" if blind else "+untracked")
        if command == "diff":
            if "--no-index" in args:
                paths = [a for a in args if not a.startswith("-")]
                return "" if all(not self._in_repository(os.path.join(folder, p), resolved) for p in paths) \
                    else _GIT_WHOLE
            return _GIT_WHOLE if _git_refs_named(args) else "head+changes+config"
        if command in _GIT_REF_COMMANDS:
            return "head+refs+config"
        if command in _GIT_HEAD_COMMANDS:
            return "head+refs+config" if _git_refs_named(args) else "head+config"
        return _GIT_WHOLE

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
        checkout (a linked worktree's), as path prefixes in their given and their resolved spelling. Third:
        the shared git dir, where the runner keeps its own state under `harness/`."""
        roots = {self._real(self.root), os.path.normcase(os.path.abspath(self.root))}
        dirs, outer = [self._real(self.root)], []
        try:
            listed = _git(self.root, ["rev-parse", "--path-format=absolute", "--absolute-git-dir", "--git-common-dir"])
        except (RuntimeError, OSError):
            listed = ""
        lines = [x.strip() for x in listed.splitlines() if x.strip()]
        for line in lines:
            for spelled in (os.path.normcase(os.path.abspath(line)), self._real(line)):
                if spelled not in dirs:
                    dirs.append(spelled)
                if not any((spelled + os.sep).startswith(r + os.sep) for r in roots) and spelled + os.sep not in outer:
                    outer.append(spelled + os.sep)
            if os.path.basename(self._real(line)) == ".git" and os.path.dirname(self._real(line)) not in dirs:
                dirs.append(os.path.dirname(self._real(line)))
        return tuple(dirs), tuple(outer), (lines[1] if len(lines) > 1 else "")

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
        env = {k: None for k in GIT_REPO_ENV}
        result = _git_run.run(args, cwd=self.root, env=env, timeout=None)
        if result.returncode != 0:
            raise RuntimeError("git %s failed in %s: %s"
                               % (args[0], self.root, result.stderr.decode("utf-8", "replace").strip()))
        return result.stdout.decode("utf-8", "replace")

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
                result = _git_run.run(["check-ignore", "-z", "--stdin"], cwd=self.root,
                                      input=("\0".join(todo) + "\0").encode("utf-8"), timeout=None)
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

    def _head_state(self, fresh=False):
        """A digest of what this checkout's HEAD names: its branch, its commit and that commit's tree. A proof that
        resolves HEAD prints one of them; a commit in another worktree moves none of them."""
        if fresh or not hasattr(self, "_head_digest"):
            try:
                state = hashlib.sha256("\n".join(self._git_read(a) for a in (
                    ["rev-parse", "--abbrev-ref", "HEAD"], ["rev-parse", "HEAD^{tree}"], ["rev-parse", "HEAD"])
                ).encode("utf-8")).hexdigest()
            except (RuntimeError, OSError):
                state = None
            if fresh:
                return state
            self._head_digest = state
        return self._head_digest

    def _changes_state(self, fresh=False):
        """A digest of the tracked files' uncommitted changes, staged and unstaged: their status codes and their
        content, so a second edit of an already modified file moves it."""
        if fresh or not hasattr(self, "_changes_digest"):
            try:
                state = hashlib.sha256("\n".join(self._git_read(a) for a in (
                    ["status", "--porcelain", "--untracked-files=no"], ["diff", "HEAD", "--binary"])
                ).encode("utf-8")).hexdigest()
            except (RuntimeError, OSError):
                state = None
            if fresh:
                return state
            self._changes_digest = state
        return self._changes_digest

    def _refs_state(self, fresh=False):
        """A digest of every ref the repository holds: what any checkout of it moves with a commit, a checkout, a
        tag or a stash."""
        if fresh or not hasattr(self, "_refs_digest"):
            try:
                state = hashlib.sha256(self._git_read(
                    ["for-each-ref", "--format=%(refname) %(objectname)"]).encode("utf-8")).hexdigest()
            except (RuntimeError, OSError):
                state = None
            if fresh:
                return state
            self._refs_digest = state
        return self._refs_digest

    def _config_state(self, fresh=False):
        """A digest of the config a proof reads: the repository's and, with `extensions.worktreeConfig`, its
        worktree's. The global and system files are the ones a proof cannot see (its environment points both at
        the null device), so the read pins them the same way."""
        if fresh or not hasattr(self, "_config_digest"):
            env = {k: None for k in GIT_REPO_ENV}
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            try:
                result = _git_run.run(["config", "--list"], cwd=self.root, env=env, timeout=None)
                state = hashlib.sha256(result.stdout).hexdigest() if result.returncode == 0 else None
            except OSError:
                state = None
            if fresh:
                return state
            self._config_digest = state
        return self._config_digest

    def _worktrees_state(self, fresh=False):
        """A digest of the repository's worktree list: every worktree's path and HEAD, which no file of this
        checkout shows."""
        if fresh or not hasattr(self, "_worktrees_digest"):
            try:
                state = hashlib.sha256(self._git_read(["worktree", "list", "--porcelain"]).encode("utf-8")).hexdigest()
            except (RuntimeError, OSError):
                state = None
            if fresh:
                return state
            self._worktrees_digest = state
        return self._worktrees_digest

    def _upstream_state(self, fresh=False):
        """A digest of the ref this checkout's branch tracks, which `git status` prints the divergence from: its
        name and its commit, or a digest of nothing when the branch tracks none."""
        if fresh or not hasattr(self, "_upstream_digest"):
            env = {k: None for k in GIT_REPO_ENV}
            try:
                name = _git_run.run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                                    cwd=self.root, env=env, timeout=None)
                if name.returncode == 0:
                    value = _git_run.run(["rev-parse", "@{upstream}"], cwd=self.root, env=env, timeout=None)
                    state = hashlib.sha256(name.stdout + b"\n" + value.stdout).hexdigest() \
                        if value.returncode == 0 else None
                elif b"no upstream configured" in name.stderr:
                    state = hashlib.sha256(b"").hexdigest()
                else:
                    state = None
            except OSError:
                state = None
            if fresh:
                return state
            self._upstream_digest = state
        return self._upstream_digest

    def _shared_value(self, kind, path, memo):
        """What a key holds of one touch of the runner's own state: the file's bytes for a read or a stat, the entry
        kind for a probe, the entries for a listing."""
        token = ("shared", kind, path)
        if token not in memo:
            if kind == "p":
                memo[token] = None if not os.path.lexists(path) else \
                    "link" if os.path.islink(path) else "dir" if os.path.isdir(path) else "file"
            elif kind == "l":
                try:
                    memo[token] = sorted(n for n in os.listdir(path) if n != "__pycache__")
                except OSError:
                    memo[token] = None
            else:
                memo[token] = self._read_digest(None, path)
        return memo[token]

    def snapshot_git(self):
        """Read the git state the keys use, before the run's proofs start. A pass records only when the state
        its run ended on is this one (`observed_parts`): a proof that ran across a change observed a state
        no row can name. Every part is read, `repo` included: an older manifest, or a child whose shell script
        names git, still keys it."""
        for method in _GIT_SCOPES.values():
            getattr(self, method)()

    def observed_parts(self, proof, manifest, fresh=False, memo=None, files=None, why=None):
        """The parts of the observed key, each a short digest, or None when the proof has no manifest, its
        reads are opaque, or the git state its git use observes cannot be read or (`fresh`, at record time)
        is no longer the one the run started on; `why`, a list, receives the reason. A pass is reused while every
        part holds: the launch fingerprint (`_launch_fingerprint`, which holds both recorders' bytes); each file it read, each unrecorded child's static reach;
        each path it probed, as absent, a file, a directory or a link; each directory it listed; each touch of the runner's own
        state under the shared git dir (`shared`, keyed by bytes, kind or entries); and the parts of the repository its
        git use can observe (`_git_scope`): `head` is what this checkout's HEAD names, `changes` the tracked files'
        uncommitted content, `refs` every ref, `config` the repository's own config, `worktrees` the worktree list,
        `upstream` the ref the branch tracks, `tracked` the names the index holds, `untracked` the unignored
        untracked names; a script child whose text runs
        git has every part (`_GIT_WHOLE`); each object printed by full id is its presence (`_objects_state`);
        `sibling` (another worktree) never reuses. An older row's `repo` is the union of the five repository parts
        (`_repo_state`). A close's exhaust counts as absent in the probes, files and listings (`_ignored`).
        What a path is on disk is looked up once per `memo` (the run's own when not `fresh`): a case pool's
        cases share thousands of import probes, and each case's key asks about all of them. A `files` list receives
        the keyed file rows ([path, digest, tracked]) the files part hashes."""
        if not isinstance(manifest, dict) or manifest.get("opaque"):
            if why is not None:
                why.append("no usable manifest" if not isinstance(manifest, dict) else "opaque reads")
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
                # No recorder follows a non-Python child, so no spawn event names the git its script runs: its
                # commands are unknown, and `repo` is the union every one of them can read. The older manifests
                # recorded this scope and still validate under it.
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
            if why is not None:
                why.append("reads a sibling worktree's git state")
            return None  # another worktree's HEAD and files: no key of this checkout names them, so the pass never reuses
        held = {k: getattr(self, m)() for k, m in _GIT_SCOPES.items() if k in scopes}
        if any(v is None for v in held.values()):
            if why is not None:
                why.append("git state unreadable: " + ",".join(sorted(k for k, v in held.items() if v is None)))
            return None
        moved = [k for k, v in held.items() if fresh and getattr(self, _GIT_SCOPES[k])(fresh=True) != v]
        if moved:
            if why is not None:
                why.append("git state moved while the run's proofs ran: " + ",".join(sorted(moved)))
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
        runner = _launch_fingerprint() if fresh else self.runner
        recorder = [self._digest("<recorder>", os.path.join(_RECORDER_DIR, "sitecustomize.py")),
                    self._digest("<node-recorder>", _NODE_RECORDER)]
        behavior = self._behavior_of(rel, fresh)
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
        for name in ("head", "changes", "refs", "config", "worktrees", "upstream"):
            if name in held:
                parts[name] = held[name]
        shared = manifest.get("shared") or ()
        if shared:
            parts["shared"] = [[kind, path, self._shared_value(kind, path, memo)] for kind, path in shared]
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
        if name in _catalog.ALWAYS and not _corpus_dirs(name):
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
        if name in _catalog.ALWAYS and not _corpus_dirs(name):
            return None
        digest = self._read_digest if fresh else self._digest
        rows = sorted([rel, digest(rel), rel in self.tracked] for rel in self.reach(proof))
        runner = _launch_fingerprint() if fresh else self.runner
        rel = _norm(os.path.relpath(proof, self.root))
        behavior = self._behavior_of(rel, fresh)
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
        if store in self._legacy:
            _migrate_legacy_store(self._legacy[store], store)
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
        return _evidence.lookup_cases(self, proof).held

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

    def matched(self, proof, key):
        """The proof's row when its key still holds (`_matched`); one lookup asks this once and carries the row's
        receipts, so a caller never asks a second time for them."""
        return self._matched(proof, key)

    def miss_reasons(self, proof):
        """The parts `_matched` named when it last missed `proof`."""
        return tuple(self.misses.get(_norm(os.path.relpath(proof, self.root)), ()))

    def unobserved_note(self, proof):
        """{`unobserved`: why} when this checkout's newest row for `proof` kept no observed key and named the
        reason, so the close's own record carries the trigger; {} otherwise."""
        if self.miss_reasons(proof) != ("unobserved",):
            return {}
        entry = self._rows(self.path, proof).get(_norm(os.path.relpath(proof, self.root)))
        rows = [entry] if isinstance(entry, dict) else entry or []
        for row in rows:
            if isinstance(row, dict) and row.get("checkout") == self._checkout_name() and row.get("why"):
                return {"unobserved": row["why"]}
        return {}

    def observations(self, proof, key, cases=None):
        """The row's receipts, or None when the row or one of `cases`' receipts does not hold."""
        found = _evidence.lookup(self, proof, key, cases)
        return list(found.receipts) if isinstance(found, _evidence.Hit) else None

    def hit(self, proof, key, cases=None):
        return bool(key) and isinstance(_evidence.lookup(self, proof, key, cases), _evidence.Hit)

    def record(self, proof, key=None, observations=None, manifest=None, observable=False, why=None):
        """Store this pass. Returns the reason it was stored with no observed key, when an observable run could
        not key what it ran on (the row then never reuses); else None. `why`, when the caller already knows it,
        names the trigger better than the manifest alone can."""
        key = key or self.key(proof)
        if not key:
            return None
        keyed, parts_why = [], []
        parts = self.observed_parts(proof, manifest, fresh=True, files=keyed, why=parts_why) if manifest else None
        okey = self._combine(parts) if parts else None
        row = {"key": key, "observations": observations or []}
        if okey:
            row.update(zm=_pack_manifest(dict(manifest, digests=_file_digests(keyed))), okey=okey, parts=parts)
        unobserved = None
        if observable:
            # A manifest whose key could not be taken (git state unreadable or moved mid-run) proves nothing:
            # only an opaque one, whose reads the static key covers, falls back to that key.
            row["observed"] = manifest is not None and (okey is not None or bool(manifest.get("opaque")))
            if not row["observed"]:
                unobserved = why or ("no observed key: " + ", ".join(parts_why or ["nothing keyed"]))
                row["why"] = unobserved
        self._store(self.path, {_norm(os.path.relpath(proof, self.root)): row})
        return unobserved

    def _store(self, store, new):
        """Write each {name: row} of `new` into the store folder `store` as its newest row for this checkout. A
        proof's rows (and its cases', named `proof::case`) share one file, so a record rewrites that file alone."""
        if store in self._legacy:
            _migrate_legacy_store(self._legacy[store], store)
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


def _unobserved_evidence(reads, error):
    """One line naming why a passing observable run recorded no manifest: the manifest fault, or the recorder
    evidence the launcher kept for a run with no events (event and beacon files, and the output's fault)."""
    if error is not None:
        return "manifest failed: %s: %s" % (type(error).__name__, error)
    if not reads:
        return "no read sink"
    return "no recorder events (%d event file(s), %d recorder beacon(s))%s" % (
        reads.get("log_files", 0), reads.get("beacons", 0),
        ("; " + reads["faults"]) if reads.get("faults") else "")


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


_DIAGNOSTIC_LOCK = threading.Lock()


def _append_diagnostic(log, row, retain):
    """Locked JSONL history shared by flaky and known-red diagnostics. Past `_DIAGNOSTIC_ROWS` rows the file keeps
    `retain(rows)`: the rows its reader still counts, so a trim never changes what the reader reports. The thread
    lock keeps this process's confirmations from queueing on each other's file-lock holds; a row a peer process
    keeps from landing is printed, never dropped in silence."""
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with _DIAGNOSTIC_LOCK, _file_lock.locked(log + ".lock", _LOG_LOCK_SECONDS):
            with open(log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(row + "\n")
            with open(log, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
            if len(rows) > _DIAGNOSTIC_ROWS:
                tmp = "%s.%d.tmp" % (log, os.getpid())
                with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write("".join(line + "\n" for line in retain(rows)))
                replace_path(tmp, log)
    except (OSError, TimeoutError) as exc:
        print("harness_tests: could not append to %s: %s; row: %s" % (log, exc, row), file=sys.stderr, flush=True)


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


class RedRule:
    """What one confirmation that failed alone does to the run. `always_red()` makes the run red and skips
    every later confirmation (the default); `never_red()` runs them all and leaves their verdict to the caller,
    for a close that classifies its reds afterwards; `judge(decide)` asks `decide({path: detail})`, and a False
    keeps the rest running. Each constructor states one behavior, so no branch can read an empty judge as its
    opposite."""
    __slots__ = ("_decide", "_never")

    def __init__(self, *args, **kwargs):
        raise TypeError("use RedRule.always_red(), RedRule.never_red(), or RedRule.judge()")

    @classmethod
    def _make(cls, decide=None, never=False):
        self = object.__new__(cls)
        self._decide, self._never = decide, never
        return self

    @classmethod
    def always_red(cls):
        return cls._make()

    @classmethod
    def never_red(cls):
        return cls._make(never=True)

    @classmethod
    def judge(cls, decide):
        if not callable(decide):
            raise TypeError("RedRule.judge requires a callable, got %r" % (decide,))
        return cls._make(decide=decide)

    def order_matters(self):
        """Whether the order the confirmations were chosen in is load-bearing: `never_red()` runs them in any."""
        return not self._never

    def makes_run_red(self, red_alone):
        """Whether the confirmations that failed alone ({path: detail}) make the run red."""
        if self._never:
            return False
        return True if self._decide is None else bool(self._decide(red_alone))


class RunOptions(NamedTuple):
    """How `_run_selected` runs. `cache` is a `_PassCache` or None; `root` is the checkout the proofs
    run in (None means the live one). A run is recorded to `harness_runs.json` only when `mode` names
    it; `row`, when given, also receives the recorded row. `stop` latches a newer close superseding this
    one: queued proofs and retries skip while already running proofs drain and cache their passes. `red` is
    the `RedRule` a confirmation that failed alone is judged by: `always_red()` (the default) ends the
    confirmations, `never_red()` runs them all, `judge()` decides from the red set. `no_retry` names proofs
    that run once, in the pool, with no timeout or alone retry: the close's `--also` proofs, whose hangs
    would otherwise cost a serial timeout each."""
    cache: object = None
    root: object = None
    mode: object = None
    row: object = None
    stop: object = None
    paths: object = None
    base: object = "HEAD"
    behavior_out: object = None
    repo: object = None
    red: object = RedRule.always_red()
    no_retry: object = ()
    ctx: object = None


def _run_selected(selected, verbose, statuses=None, details=None, options=RunOptions()):
    """Run the pool-safe proofs in one pool of `min(machine slots, _jobs())` width, slowest recorded
    first, then each `SERIAL` proof alone, each with `_timeout_for`. Every proof first takes a machine slot (`_take_slots`; a `SERIAL`
    proof takes all of them), so overlapping runs queue instead of timing each other out. An `options.cache`
    (`_PassCache`) reuses a proof whose reach is unchanged since its last pass, checked again once
    the slot is taken so a pass a peer run landed meanwhile counts, and records each pass as it lands
    unless a reached input moved since its key was taken. A Python proof the cache cannot reuse whole
    skips the `_case_pool` cases whose own pass rows hold; its pass then rests on their recorded reads
    too, and its time, which covers only some cases, is not recorded as the proof's duration.
    Each passing proof feeds the run's live pace (`attempt.LivePace`), which stretches every running proof's deadline.
    After releasing the initial `SERIAL` hold, confirm failures in a pool of the same width, each taking its
    own lane as a pool proof did. A confirmation runs one at a time instead only where the latch can fire:
    when a confirmation can fail alone and `options.red` may agree it makes the run red, the order is what
    stops every later one. `RedRule.never_red()` never stops them, and a run whose confirmations all timed
    out has no red to latch on, so both confirm in parallel. An assertion failure with a recorded pass is
    confirmed once: a pass is FLAKY and logged; a second failure is red. A cold assertion failure is not
    confirmed. A timeout is confirmed even with no recorded pass, at `_LOAD_FACTOR` times its timeout or at
    its `proof_timeouts` cap; a pass is FLAKY unless the proof had no recorded duration.
    Assertion failures retry first, shortest recorded first. Once a confirmation fails and `options.red`
    agrees it makes the run red, no later confirmation runs. Unconfirmed timeouts are `skipped`;
    unconfirmed assertions keep their first failure and gain a note. A proof in `options.no_retry`
    never retries. Past `HARNESS_DEADLINE` no proof starts or retries, and one the deadline cuts short
    is `skipped`, counted with the indeterminate ones.
    Returns (passed, failed, indeterminate, wall seconds); a `statuses` dict
    receives each proof's final status and a `details` dict its final detail. A run with an `options.mode`
    records its row (`mode`, wall and queue seconds, each proof's status, run and wait seconds, and whether
    it was reused) and the row is appended to `harness_runs.json`."""
    start = time.time()
    phases = {}
    ctx = options.ctx if options.ctx is not None else _run_context(options, pace=_attempt.LivePace())
    run_pace = ctx.pace
    run_pace.reset()
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
    timing_lock = threading.Lock()  # every attempt writes the run's totals; the pool and confirmations run on threads

    def expected(path):
        value = known.get(os.path.basename(path), 0)
        return float(value) if isinstance(value, (int, float)) else 0.0

    stop = options.stop
    no_retry = set(options.no_retry or ())
    moved = _attempt.MOVED

    def attempt(path, timeout):
        if stop and stop():
            return moved
        if cache and path not in keys:
            keys[path] = cache.lazy_key(path)
        lazy = keys.get(path)
        if lazy:
            found = _evidence.lookup(cache, path, lazy, cases[path] or None)
            if isinstance(found, _evidence.Hit):
                reused.add(path)
                observations[path].extend(found.receipts)
                return _attempt.AttemptResult("pass", 0.0, "")
        key = lazy() if lazy else None

        def recheck():
            nonlocal key
            if stop and stop():
                return moved
            if not key:
                return None
            key = cache.key(path, fresh=True)
            found = _evidence.lookup(cache, path, key, cases[path] or None)
            if isinstance(found, _evidence.Hit):
                reused.add(path)
                observations[path].extend(found.receipts)
                return _attempt.AttemptResult("pass", 0.0, "")
            return None

        held = {}

        def reusable():
            held.update(_evidence.lookup_cases(cache, path).held)
            return sorted(held)
        launch_options = _launch_options(path, root, behavior_root, cases[path], observations[path], bool(key), reusable)
        reads = launch_options.get("reads_sink")
        timing = {}
        result = _attempt._as_result(_launch(path, timeout, recheck=recheck if key or stop else None, timing=timing,
                                             ctx=ctx, **launch_options))
        if stop:
            stop()
        with timing_lock:  # every attempt writes the run's totals, and confirmations run on threads
            waits[path] = waits.get(path, 0.0) + timing.get("wait_s", 0.0)
            for kind, seconds in timing.get("wait_split", {}).items():
                wait_split[kind] = wait_split.get(kind, 0.0) + seconds
        if held and path not in reused and result[0] != "skipped":
            partial[path] = len(held)
        else:
            partial.pop(path, None)
        if result[0] == "pass" and key and path not in reused and cache.key(path, fresh=True) == key:
            manifest_error = None
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
            except Exception as exc:  # noqa: BLE001 - a manifest fault costs only the observed key, never the run
                manifest, case_manifests, manifest_error = None, {}, exc
            observable = reads is not None
            evidence = _unobserved_evidence(reads, manifest_error) if observable and manifest is None else None
            unobserved = None
            if cases[path]:
                if all(r['status'] == 'pass' for r in observations[path]):
                    unobserved = cache.record(path, key, observations[path], manifest=manifest,
                                              observable=observable, why=evidence)
            else:
                unobserved = cache.record(path, key, manifest=manifest, observable=observable, why=evidence)
                if case_manifests:
                    cache.record_cases(path, case_manifests)
            if unobserved:
                _progress("UNOBSERVED %s: %s" % (os.path.basename(path), unobserved))
        return result

    done, done_lock = {"n": 0, "at": time.time(), "left": sum(expected(p) for p in selected), "ran": 0.0}, threading.Lock()

    def progressed(path, result):
        """Print `N of M proofs done` every `_PROGRESS_EVERY` proofs or minute on a run of at least
        `_PROGRESS_MIN`: a whole close otherwise prints nothing until it ends. Each line also states the
        recorded seconds the unfinished proofs need at this pool width, and the pace: elapsed over the
        recorded time of the proofs that ran, so a reader sees whether the run keeps its recorded schedule."""
        if result[0] == "pass" and path not in reused and path not in partial:
            run_pace.observe(result[1], _recorded_time(path, known))
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
    retry_lock = threading.Lock()

    def retry(index, path):
        nonlocal decided, judged, alone_n, alone_s
        timed_out = _timed_out(results[path])
        cold = timed_out and os.path.basename(path) not in known
        if latching and not decided and len(red_alone) > judged:
            judged = len(red_alone)
            decided = options.red.makes_run_red(red_alone)
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
                results[path] = _attempt.AttemptResult(
                    "skipped", 0.0, "not run: the run is already red (%s failed alone)" % named)
            else:
                result = results[path]
                results[path] = _attempt.AttemptResult(
                    result.status, result.run_s, (result.detail or "").rstrip("\n")
                    + "\n(red in the pool, not retried alone: the run is already red (%s failed alone))\n" % named)
            return
        _progress("%s retry %d of %d: %s; first failure: %s"
                  % ("timeout" if timed_out else "alone", index, len(retrying), os.path.basename(path),
                     _last_line(results[path][2])))
        again = attempt(path, _retry_timeout(path, known, timed_out))
        with retry_lock:
            if again[0] != "skipped" and not timed_out:
                alone_n, alone_s = alone_n + 1, alone_s + again[1]
            if again[0] == "pass" and not cold:
                flaky.append((path, results[path][2]))
            if again[0] == "fail" and not timed_out:
                red_alone[path] = again[2]
        if again[0] == "pass" and not cold:
            _record_flaky(path, results[path][2], again[1])
        if again[0] == "skipped":
            if timed_out:
                results[path] = again
            return
        results[path] = again

    phase_start = time.time()
    with _attempt.serial(ctx) if alone else nullcontext():
        phases["serial_wait"] = time.time() - phase_start
        phase_start = time.time()
        for path in alone:
            results[path] = progressed(path, attempt(path, _timeout_for(path, known)))
        phases["serial"] = time.time() - phase_start
    pool_s = time.time() - start
    # Try the likely reds first: assertion failures before timeouts, shortest recorded first.
    retrying[:] = sorted((p for p in selected if not cases[p] and results[p][0] == "fail"
                          and (os.path.basename(p) in known or _timed_out(results[p]))
                          and p not in no_retry),
                         key=lambda p: (_timed_out(results[p]), expected(p)))
    # A confirmation takes its own lane as a pool proof did, and the batch that starved it has ended. They run
    # at the pool's width unless the latch can fire: only a confirmation that can fail alone, under a `red`
    # policy that may stop the run, makes the order load-bearing. Their admission waits are recorded per proof.
    latching = options.red.order_matters() and any(not _timed_out(results[p]) for p in retrying)
    phases["retry_wait"] = 0.0
    phase_start = time.time()
    if latching:
        for index, path in enumerate(retrying, 1):
            retry(index, path)
    else:
        with ThreadPoolExecutor(max_workers=max(1, min(width, len(retrying)))) as confirmations:
            futures = [confirmations.submit(retry, index, path) for index, path in enumerate(retrying, 1)]
            for future in as_completed(futures):
                future.result()
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
        result = results[path]
        status, elapsed, detail = result.status, result.run_s, result.detail
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
                               else {}),
                            **(cache.unobserved_note(p) if cache else {})) for p in selected],
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
    listed = _git_run.soft_text(["-C", repo_root, "worktree", "list", "--porcelain"], env=env, timeout=None) or ""
    for line in listed.splitlines():
        path = line[len("worktree "):] if line.startswith("worktree ") else ""
        if not os.path.basename(path).startswith(_CHECKOUT_PREFIX) or not os.path.exists(path + ".lock"):
            continue
        # Without waiting: the first try deletes a dead holder's lock, the second takes it; a live holder refuses both.
        held = _file_lock.acquire(path + ".lock", 0) or _file_lock.acquire(path + ".lock", 0)
        if held is None:
            continue
        try:
            _git_run.run(["-C", repo_root, "worktree", "remove", "--force", path], env=env, timeout=None)
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
    env = {k: None for k in GIT_REPO_ENV}
    _reap_abandoned_checkouts(repo_root, env)
    tmp = tempfile.mkdtemp(prefix=_CHECKOUT_PREFIX)
    held = _file_lock.acquire(tmp + ".lock", 0)

    def git(*args):
        result = _git_run.run(["-C", tmp] + list(args), env=env, timeout=None)
        if result.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (args[0], result.stderr.decode("utf-8", "replace").strip()))

    try:
        result = _git_run.run(["-C", repo_root, "worktree", "add", "--detach", "--no-checkout", tmp, rev],
                              env=env, timeout=None)
        if result.returncode != 0:
            raise RuntimeError("git worktree add failed: %s%s"
                               % (result.stderr.decode("utf-8", "replace").strip(), _admin_gaps(repo_root)))
        corpus = sorted({_norm(d).strip("/") for dirs in PROOF_CORPUS.values() for d in dirs
                         if not _norm(d).startswith(".claude")} - {""})
        git("sparse-checkout", "set", "--cone", ".claude", *corpus)
        git("checkout", "-q", "-f", "--detach", rev)
        for path in proofs:
            rel = _norm(os.path.relpath(path, repo_root))
            target = os.path.join(tmp, rel)
            if not os.path.isfile(target) and _never_committed(repo_root, rel):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(path, target)
        yield tmp
    finally:
        _git_run.run(["-C", repo_root, "worktree", "remove", "--force", tmp], env=env, timeout=None)
        _remove_temp_root(tmp)
        _file_lock.release(held)


def _reds_at(repo_root, rev, failing, answers=None):
    """Failing outputs at `rev`; never-committed proofs run against its inputs. A path a proof prints under
    the comparison checkout reads as the same path under `repo_root`, so the two outputs compare.

    When `answers` is given it receives every proof's typed answer (`red`, `pass` or `unavailable`), so a
    caller can tell a proof that passed there from one the revision could not run."""
    with _revision_checkout(repo_root, rev, failing) as tmp:
        reds = {}
        ctx = _run_context(root=tmp)

        def rerun(i, path):
            at_rev = os.path.join(tmp, os.path.relpath(path, repo_root))
            if os.path.isfile(at_rev):
                _progress("classifying red %d of %d: rerun %s at %s" % (i, len(failing), os.path.basename(path), rev[:8]))
                attempt = _attempt.run(ctx, _attempt.AttemptJob(proof=at_rev, purpose="diagnostic", root=tmp))
                status, detail = attempt.status, attempt.detail
                if answers is not None:
                    answers[path] = ("red" if status == "fail"
                                     else "pass" if status == "pass" else "unavailable")
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


def _historical(repo_root, rev, proofs):
    """{proof: Historical} at `rev`: the typed answer every comparison and bisect reads."""
    answers = {}
    reds = _reds_at(repo_root, rev, list(proofs), answers)
    return {p: _authority.Historical(answers.get(p, _authority.UNAVAILABLE), reds.get(p, "")) for p in proofs}


def _comparison(repo_root, rev):
    """One `Comparison` at `rev`, over this checkout's historical reader and failure identity."""
    return _authority.Comparison(rev, lambda proofs: _historical(repo_root, rev, proofs),
                                 lambda detail, base_detail: _is_pre_existing(
                                     detail, base_detail, repo_root, repo_root))


def _new_reds(repo_root, base, reds, memo):
    """The proofs of `reds` ({path: detail}) whose failure is not pre-existing at `base`. Each proof reruns at
    base once: `memo` keeps its typed answer for the caller's classification."""
    comparison = _comparison(repo_root, base)
    comparison.answers.update(memo)
    found = comparison.blocking(reds)
    memo.update(comparison.answers)
    return found


def _git_soft(repo_root, args):
    return (_git_run.soft_text(args, cwd=repo_root, timeout=None) or "").strip()


def _default_merge_base(repo_root, head, base, remote="origin"):
    """The merge-base of `head` with `remote`'s default branch when a sync brought commits the close `base`
    lacks, else None: a red the branch inherited from that merge is the default branch's, not the branch's."""
    ref = _default_ref(repo_root, remote)
    found = _git_soft(repo_root, ["merge-base", head, ref]) if ref else ""
    if not found:
        return None
    return found if found != base and _git_soft(repo_root, ["rev-list", "-1", base + ".." + found]) else None


_PROGRESS_LOCK = threading.Lock()


def _progress(message):
    """One line, one writer: pool and confirmation threads print at once, and a half line spliced into another
    reads as neither."""
    with _PROGRESS_LOCK:
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


def _first_red(repo_root, base, head, proof, detail, reached, *, commits=None):
    """Bisect this proof's first-parent input-changing commits, never the shared checkout. `unknown` once
    `HARNESS_DEADLINE` passes: a run the deadline skipped is no pass, so no commit can be named."""
    if commits is None:
        commits = _input_commits(repo_root, base, head, reached)
    return _first_reds(repo_root, {proof: (detail, commits)})[proof]


def _first_reds(repo_root, reds):
    """{proof: FirstRed} for `reds` ({proof: (detail, candidate commits)}), bisected in lockstep: each step
    groups the proofs by the commit they test next and answers each group at that commit in one checkout
    (`_reds_at`). A commit is named only where the red was seen; an unanswered commit leaves the proof unknown."""
    return _authority.first_reds(
        reds, lambda rev, proofs: _historical(repo_root, rev, proofs),
        pre_existing=lambda detail, base_detail: _is_pre_existing(detail, base_detail, repo_root, repo_root),
        deadline=_deadline, progress=_progress)


def _side_commit(repo_root, merge, proof, detail, reached):
    """The commit that introduced the red when the first-parent bisect landed on a merge: bisect the merged-in
    side when the merge's first parent passes and its second parent reproduces the red. Else `merge`."""
    while True:
        parents = _git(repo_root, ["rev-list", "--parents", "-n", "1", merge]).split()[1:]
        if len(parents) < 2:
            return merge
        def reproduces(rev):
            red = _historical(repo_root, rev, [proof])[proof]
            return red.kind == _authority.RED and _is_pre_existing(detail, red.detail, repo_root, repo_root)
        if reproduces(parents[0]) or not reproduces(parents[1]):
            return merge
        side = _git(repo_root, ["rev-list", "--first-parent", "--reverse", parents[1], "^" + parents[0],
                                "--"] + sorted(reached)).splitlines()
        if not side:
            return merge
        found = _first_red(repo_root, None, None, proof, detail, reached, commits=side)
        if found.kind != _authority.FOUND or found.commit == merge:
            return merge
        merge = found.commit


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
    try:
        claim = _authority.claim(close_run_path())
        update_json_locked(STAMP_PATH, lambda doc: doc.update(close=dict(
            doc.get("close") or {}, verdict="incomplete", ts=time.time())), raising=True)
        while not now:
            if not _authority.owns(claim):
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
        return _close_once(repo_root, base, verbose, claim, ranged=ranged, also=also,
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
    ctx = _run_context(root=repo_root)

    def run(proof):
        here = os.path.join(repo_root, *_norm(os.path.relpath(proof, snapshot)).split("/"))
        return (_attempt.run(ctx, _attempt.AttemptJob(proof=here, purpose="diagnostic", root=repo_root)).status
                if os.path.isfile(here) else None)
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


def _close_once(repo_root, base=None, verbose=False, claim=None, behavior_out=None, ranged=False, also=()):
    """Prove the committed HEAD of this moment; 2 without a verdict when a newer close supersedes it.

    `ranged` (the main check's `--range-start` mode) runs every proof: no red stops another proof's retry,
    and a proof whose timeout repeats alone is a red no bisect runs on, since a timeout names no commit and a
    bisect costs a run per step. `also` names proofs a ranged close adds to its selection and runs once in
    the pool, with no timeout or alone retry."""
    stop = Event()
    claim = claim or _authority.claim(close_run_path())

    def superseded():
        if not stop.is_set() and not _authority.owns(claim):
            stop.set()
        return stop.is_set()

    try:
        head = _git(repo_root, ["rev-parse", "HEAD"]).strip()
        remote, target = _push_range.push_destination(repo_root)
        base = _push_range.close_base(repo_root, base, push_ref="refs/remotes/%s/%s" % (remote, target), remote=remote)
        outgoing_of = None if ranged else remote
        paths = _push_range.close_paths(repo_root, base, head, outgoing_of)
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
            empty_base = _is_empty_base(repo_root, base)
            comparison = None if empty_base else _comparison(snapshot, base)

            def blocks(reds):
                """A red alone blocks this close unless its proof was never committed or it is pre-existing at base."""
                committed = {p: d for p, d in reds.items()
                             if not _never_committed(repo_root, _norm(os.path.relpath(p, snapshot)))}
                return bool(committed) and (empty_base or bool(comparison.blocking(committed)))

            _run_selected(selected, verbose, statuses, details,
                          # A ranged close runs every retry: its reds are classified once, after the run, against
                          # the base, so no confirmation may stop a later one.
                          RunOptions(cache, snapshot, "close", row, superseded, paths, base, behavior_out,
                                     red=RedRule.never_red() if ranged else RedRule.judge(blocks),
                                     no_retry=frozenset(also_paths)))
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
            pre_existing = {}
            if comparison is not None:
                comparison.ask(bisectable)
                for proof in failing:
                    verdict = comparison.verdict(proof, details[proof])
                    if verdict.kind == "pre_existing":
                        pre_existing[proof] = verdict.detail
            main_base = _default_merge_base(snapshot, head, base, outgoing_of)
            if main_base:
                main_comparison = _comparison(snapshot, main_base)
                main_comparison.ask([p for p in bisectable if p not in pre_existing])
                for proof in main_comparison.answers:
                    if main_comparison.verdict(proof, details[proof]).kind == "pre_existing":
                        print("harness_tests: pre-existing red on the default branch at %s: %s%s"
                              % (main_base[:8], os.path.basename(proof), _known_red_note(os.path.basename(proof))))
                        pre_existing[proof] = details[proof]
            new_red, old_red = [], []
            candidates = {}
            bound = bindings(snapshot, paths) if failing else {}
            uncommitted, unexplained, reach_of = {}, {}, {}
            here = _working_tree_status(repo_root, snapshot, [
                p for p in failing if p not in pre_existing and not _timeout_detail(details[p])])
            for proof in failing:
                if proof in pre_existing:
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
                if proof in pre_existing:
                    print("harness_tests: pre-existing red at base %s: %s%s" % (base[:8], name, _known_red_note(name)))
                    _record_known_red(name, base, _last_line(details[proof]), _last_line(pre_existing[proof]))
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
                    first = firsts.get(proof) or _authority.FirstRed(_authority.UNKNOWN)
                    commit = first.commit if first.kind == _authority.FOUND else None
                    if commit:
                        commit = _side_commit(snapshot, commit, proof, details[proof], reach_of[proof])
                    subject = _git(snapshot, ["show", "-s", "--format=%s", commit]).strip() if commit else "untraced"
                    print("FIRST RED %s at %s %s; next: fix this commit, or hand it to its owner's fix job"
                          % (name, commit or "unknown", subject))
                    for trailer in (_git(snapshot, ["show", "-s", "--format=%(trailers:key=Claude-Session,valueonly)",
                                                    commit]).split() if commit else ()):
                        print("  Claude-Session: " + trailer)
                    red.update(commit=commit, detail=details[proof])
                    new_red.append(red)
            if failing:
                _progress("red handling after the run: %ds for %d red"
                          % (time.time() - red_started, len(failing)))
            incomplete = [os.path.basename(p) for p in _authority.reduce_statuses(selected, statuses).incomplete]
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
        if not _authority.publish(claim, STAMP_PATH, result):
            print("harness_tests: a newer close superseded this one; it stops without a verdict", flush=True)
            return 2
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
    except (RuntimeError, OSError, TimeoutError, ValueError, subprocess.CalledProcessError) as exc:
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
    catalog = _open_catalog(repo_root)
    selection = catalog.select(in_scope, behavior=_behavior_plan)
    bound = _full_bindings(catalog, selection.obligations)
    selected = [catalog.view.full(p) for p in selection.proofs]
    if not selected:
        print("harness_tests: no proof is bound to %s; add test_<name>*.py; the close runs the full battery" % ", ".join(in_scope))
        return 1
    statuses, details = {}, {}
    compare, label = (base, "base") if base else (tested_head, "HEAD")
    # HEAD is a "before" only while a tracked input or selected proof differs from it.
    # A never-committed proof alone does not make HEAD precede the change under test.
    carried = bool(not base and tested_head and not _git(
        repo_root, ["status", "--porcelain", "--untracked-files=no", "--"] + in_scope + selected).strip())
    comparison = None if carried or not compare else _comparison(repo_root, compare)

    def blocks(reds):
        """A red alone ends the alone retries only when it is not pre-existing at the comparison revision."""
        if comparison is None:
            return True
        try:
            return bool(comparison.blocking(reds))
        except (RuntimeError, OSError):
            return True
    passed, failed, indeterminate, elapsed_total = _run_selected(
        selected, verbose, statuses, details, RunOptions(_pass_cache(repo_root, selected), repo=repo_root,
            mode=mode, paths=in_scope, base=base or 'HEAD', behavior_out=behavior_out, red=RedRule.judge(blocks)))
    reduction = _authority.reduce_statuses(selected, statuses)
    extra_incomplete = len([p for p in indeterminate if p not in statuses])  # a `<behavior claims>` row
    failing = [p for p in selected if statuses.get(p) == "fail"]
    skipped = reduction.skipped
    if failing and carried:
        print("harness_tests: the given paths are committed at HEAD %s, so HEAD carries the change and excuses "
              "no red; pass --base <the revision before the change> to compare there" % tested_head[:8])
    elif failing and comparison is not None:
        try:
            comparison.ask(failing)
        except (RuntimeError, OSError) as exc:
            print("harness_tests: %s checkout failed, every red proof keeps blocking: %s" % (label, exc))
        else:
            for path in failing:
                verdict = comparison.verdict(path, details.get(path))
                tail_worktree = _last_line(details.get(path))
                if verdict.kind == "unavailable":
                    print("harness_tests: %s %s could not run %s (%s); it keeps blocking"
                          % (label, compare[:8], os.path.basename(path), verdict.cause))
                elif verdict.kind == "different" and verdict.detail:
                    print("harness_tests: %s is red at %s %s too, but fails differently or shows no failure "
                          "line; it keeps blocking"
                          "\n    worktree: %s\n    %s: %s"
                          % (os.path.basename(path), label, compare[:8], tail_worktree, label,
                             _last_line(verdict.detail)))
                elif verdict.kind == "pre_existing":
                    print("harness_tests: pre-existing red at %s %s: %s%s\n    worktree: %s\n    %s: %s"
                          % (label, compare[:8], os.path.basename(path),
                             _known_red_note(os.path.basename(path)), tail_worktree, label,
                             _last_line(verdict.detail)))
                    _record_known_red(os.path.basename(path), compare, tail_worktree, _last_line(verdict.detail))
                    statuses[path] = "pre-existing-red"
                    failed -= 1
    elif failing:
        print("harness_tests: HEAD unknown, every red proof keeps blocking")
    print("harness_tests: scoped to %d input(s): %d run, %d pass, %d fail, %d cannot-run, %.1fs wall, %d jobs"
          % (len(in_scope), len(selected), passed, failed,
             len(reduction.cannot_run) + len(reduction.skipped) + extra_incomplete, elapsed_total, _jobs()))
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
        # A scoped run into a base stamp extends the full run's claim; a standalone one records only
        # its own counts, so a missing key never reads as a full battery that covered more.
        doc.setdefault("run", len(selected))
        doc.setdefault("pass", passed)
        doc.setdefault("excluded", [])
        doc.setdefault("cannotRun", [])
        doc["files"] = files
        doc["tree_hash"] = _authority.digest_files(files)
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
        selected, verbose, statuses, options=RunOptions(cache=cache, root=repo_root, repo=repo_root, mode="all",
                                                         behavior_out=behavior_out))
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
    return _catalog.build_parser(REPO_ROOT)


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
            options=RunOptions(_pass_cache(args.repo, selected), root=args.repo, repo=args.repo, mode="proofs-for",
                               paths=args.proofs_for_paths, behavior_out=args.behavior_report))
        reduction = _authority.reduce_statuses(selected, statuses)
        skipped = reduction.skipped
        incomplete = list(reduction.cannot_run) + [p for p in indeterminate if p not in statuses]
        print("harness_tests: scoped: %d run, %d pass, %d fail, %d cannot-run, %d skipped, %.1fs wall, %d jobs"
              % (len(selected), passed, failed, len(incomplete), len(skipped), elapsed_total, _jobs()))
        if failed:
            return 1
        if skipped and not incomplete:
            print("harness_tests: HARNESS_DEADLINE passed before %d proof(s) ran" % len(skipped))
            return 3
        if incomplete:
            print("cannot-run (exit 2: the proof could not bind its target; the scoped run is incomplete):")
            for proof in incomplete:
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
