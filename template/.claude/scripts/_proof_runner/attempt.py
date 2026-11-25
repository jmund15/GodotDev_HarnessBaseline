"""One proof in an explicit world: its argv, environment, working directory, temp root, deadline, pace and
lease, and the typed result.

`RunContext` is built once per run and carries the resolved checkout root, the deadline, the live pace, the
slot pool's functions, a cancel check and the launch function. `run()` is one attempt after admission, and
`serial()` is the SERIAL proofs' single whole-machine take. Both take and release through the context, so no
module global is mutated and two runs in one process never share an admission state. `run_proof()` runs the
child itself and keeps the three-field shape every direct caller unpacks; `run()` returns the typed
`AttemptResult`, whose `timeout` field replaces reading the detail text.
"""
import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import NamedTuple

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLAUDE_DIR = os.path.dirname(_SCRIPTS_DIR)
REPO_ROOT = os.path.dirname(_CLAUDE_DIR)
_RECORDER_DIR = os.path.join(_SCRIPTS_DIR, "_read_recorder")
_NODE_RECORDER = os.path.join(_RECORDER_DIR, "node_recorder.js")
# Extensions whose runner process the read recorder can instrument: Python and node. A shell or PowerShell proof
# runs its own reads outside either recorder, so its manifest would be partial and its pass unsafely reusable.
RECORDABLE = (".py", ".js", ".mjs")

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
from _bounded_run import ProcessTree  # noqa: E402


class AttemptJob(NamedTuple):
    """What one attempt runs and the knobs it hands the launch function.

    `purpose` is `first` (the pool's uncached attempt), `confirm` (a retry), `diagnostic` (a red rerun outside
    a run's cache) or `judge` (the baseline shipped-set judge). `recheck` answers from the pass cache once the
    slot is held. `exclusive` is the SERIAL proof's own whole-machine claim.
    """
    proof: str
    purpose: str = "first"
    timeout: object = None
    root: object = None
    recheck: object = None
    behavior_cases: object = None
    receipt_sink: object = None
    reads_sink: object = None
    reuse_cases: object = None
    exclusive: bool = False
    stretch: object = None


class AttemptResult(NamedTuple):
    """One run's answer. The first three fields are the tuple every caller already unpacks; `timeout` is the
    typed replacement for reading the detail text."""
    status: str
    run_s: float
    detail: str
    timeout: bool = False
    wait_s: float = 0.0
    wait_split: object = None
    observations: object = ()


@dataclass
class RunContext:
    """One run's world. `held` is set while `serial()` holds every slot; the launch function is looked up
    from the runner when the context is built, so a proof that stubs it still intercepts."""
    root: str
    deadline: object = None
    pace: object = None
    take: object = None
    release: object = None
    slot_error: type = Exception
    stop: object = None
    launch: object = None
    default_timeout: object = None
    inherited: bool = False
    held: bool = False


SKIPPED = AttemptResult("skipped", 0.0, "not run: HARNESS_DEADLINE passed")
MOVED = AttemptResult("skipped", 0.0, "not run: a newer close superseded this one")


class LivePace:
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


_PYTEST_CASE_RE = re.compile(r"^(?:async\s+)?def\s+test\w*\s*\(|^class\s+Test\w*\b", re.MULTILINE)
_MAIN_GUARD_RE = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", re.MULTILINE)
_TOP_TEST_RE = re.compile(r"^(?:async\s+)?def\s+(test\w*)\s*\(\s*(\S?)", re.MULTILINE)
_PYTEST_HANDOFF_RE = re.compile(r"pytest\.main\(\s*\[\s*__file__")
_GLOBALS_COLLECTOR_RE = re.compile(r"globals\(\)[\s\S]{0,200}?startswith\(\s*['\"]test_")
_PLATFORM_SKIP_RE = re.compile(r"^(?:# )?PLATFORM-SKIP-PROOF ", re.M)  # tests/_platform_skip.skip_proof

# A bare "bash" on Windows PATH can resolve to System32's WSL shim, a different filesystem
# namespace that cannot see a Windows-style path. Prefer Git's bash.exe when present.
_GIT_BASH_CANDIDATES = (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")

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
_GIT_LOCAL_ENV_FALLBACK = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)
_STAMP_INDEX_ENV = "HARNESS_STAMP_INDEX"
_STRETCH_RECHECK_S = 5  # how stale a stretched deadline may get; proofs run for seconds to minutes
DEFAULT_TIMEOUT = 120


def _slot_root():
    """The machine pool a proof's `HARNESS_MACHINE_ROOT` names: the caller's own when set, else the default
    every checkout shares."""
    return (os.environ.get("HARNESS_MACHINE_ROOT") or os.environ.get("HARNESS_SLOT_DIR")
            or os.path.join(os.path.expanduser("~"), ".claude", ".harness_slots"))


def _norm(rel):
    return rel.replace(os.sep, "/")


def _bash_exe():
    if os.name == "nt":
        for candidate in _GIT_BASH_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate
    return "bash"


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


_DEFINITIONS = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
                ast.AnnAssign, ast.AugAssign, ast.Pass)


def _path_edit(call):
    """`sys.path.insert(...)`, `.append(...)` or `.extend(...)`: the one call a pytest file makes at its top level."""
    func = getattr(call, "func", None)
    return (isinstance(func, ast.Attribute) and func.attr in ("insert", "append", "extend")
            and ast.unparse(func.value) == "sys.path")


def _only_defines(body):
    """True when every statement of `body` defines something: an import, a def or class, an assignment, a docstring or a
    `sys.path` edit, or an `if` or `try` holding no more. Anything else (a call, a `with`, a loop) is a script body."""
    for node in body:
        if isinstance(node, _DEFINITIONS):
            continue
        if isinstance(node, ast.Expr) and (isinstance(node.value, ast.Constant) or _path_edit(node.value)):
            continue
        if isinstance(node, ast.If) and _only_defines(node.body + node.orelse):
            continue
        if isinstance(node, ast.Try) and _only_defines(
                node.body + node.orelse + node.finalbody + [s for handler in node.handlers for s in handler.body]):
            continue
        return False
    return True


def pytest_native(path):
    """True when pytest can import `path` in its own process and lose nothing the runner's `pytest -q <file>` runs: the
    runner reads it as a pytest file (`_pytest_cases` is the file itself) and importing it only defines. A pytest file
    with a script body (a column-0 `def test_` under statements that run at import) is not native: pytest would run
    that script at collection, in the controller and in every xdist worker, while the runner runs it once in its
    own process."""
    if _pytest_cases(path) != [path]:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return _only_defines(ast.parse(fh.read()).body)
    except (OSError, SyntaxError, ValueError):
        return False


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


def _run_child(cmd, env, timeout, out_path, cwd=None, stretch=None):
    """(returncode, output), returncode None on timeout. Output goes to a file, never a pipe: a
    grandchild that outlives the proof keeps a pipe open past the timeout. Every process the proof
    started ends with it, on timeout or not, so none keeps writing into the next proof's world.
    `stretch()`, read again while the child runs, multiplies `timeout`: a proof's deadline follows the load
    the run measures (`LivePace`), while a fixed budget passes none."""
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


_RECORDER_FAULT_RE = re.compile(
    r"sitecustomize|NODE_OPTIONS|Cannot find module|ModuleNotFoundError|ImportError|Traceback", re.I)


def _recorder_faults(out):
    """The lines in a proof's output that name why a recorder did not load (Python's `Error in sitecustomize` and
    its exception line, node's module/option error), else the output's tail, single-line and bounded."""
    lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
    faults = [line for line in lines if _RECORDER_FAULT_RE.search(line)]
    picked = []
    for line in (faults or lines[-3:]):
        if line not in picked:
            picked.append(line[:300])
        if len(picked) == 4:
            break
    return " | ".join(picked)[:800]


def _log_files(log_dir, suffix):
    """How many `suffix` files the processes logging under `log_dir` left, nested runs included."""
    count = 0
    for base, _dirs, files in os.walk(log_dir):
        count += sum(1 for name in files if name.endswith(suffix))
    return count


def _read_events(log_dir):
    """The recorder's events from every process that logged directly into `log_dir`, and from every runner those
    processes ran (`_nested_log`), in no order; an unreadable line is dropped, and a process that never wrote one
    left no events. A `.beacon` file is the recorder's load marker, not an event."""
    events = []
    nested = os.path.join(log_dir, "nested")
    for name in os.listdir(nested) if os.path.isdir(nested) else []:
        inner = os.path.join(nested, name)
        events += _read_events(inner) + [e for case in _case_events(inner).values() for e in case]
    for name in os.listdir(log_dir) if os.path.isdir(log_dir) else []:
        if not name.endswith(".jsonl") or not os.path.isfile(os.path.join(log_dir, name)):
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


def _core_root(root):
    """The core a proof's plugin code reaches: the checkout it runs in, unless that holds no core (a bench checkout)."""
    if root and os.path.isfile(os.path.join(root, ".claude", "tools", "adaptation.py")):
        return root
    return REPO_ROOT


def add_git_config(env, pairs):
    """`pairs` of (key, value) appended to the GIT_CONFIG_COUNT list in `env` (a dict or `os.environ`), after any entry
    already there; a pair that is there stays once."""
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    held = {(env.get("GIT_CONFIG_KEY_%d" % n), env.get("GIT_CONFIG_VALUE_%d" % n)) for n in range(count)}
    for key, value in pairs:
        if (key, value) not in held:
            env["GIT_CONFIG_KEY_%d" % count] = key
            env["GIT_CONFIG_VALUE_%d" % count] = value
            count += 1
    env["GIT_CONFIG_COUNT"] = str(count)


def proof_env(temp_root, timeout, root=None, reuse_cases=None, *, pytest_lane=False):
    """The environment a proof runs in: the caller's, with the local git state (a private index, `GIT_DIR`) and the
    caller's run and identity variables removed, every temp and home variable pointed into `temp_root` (the folder
    the caller made and removes), no global or system git config, and the `CLAUDE_*` variables a clean runner has.
    `run_proof` runs a child in it; the tests conftest gives each in-process pytest case the same one."""
    env = {k: v for k, v in os.environ.items()
           if k not in _GIT_LOCAL_ENV_FALLBACK and k not in (_STAMP_INDEX_ENV, "HARNESS_CASES_REUSED")}
    env["HARNESS_PROOF_TIMEOUT_S"] = "%g" % timeout
    if reuse_cases:
        env["HARNESS_CASES_REUSED"] = json.dumps(sorted(reuse_cases))
    if os.environ.get("GIT_INDEX_FILE"):
        env[_STAMP_INDEX_ENV] = os.environ["GIT_INDEX_FILE"]
    env.setdefault("HARNESS_PROVIDER_GUARD_LOG", os.path.join(temp_root, "workflow_provider_guard.jsonl"))
    env.setdefault("HARNESS_USAGE_SAMPLES", os.path.join(temp_root, "usage_samples.jsonl"))
    live_capacity = os.environ.get("PROVIDER_CAPACITY_CACHE") or os.path.join(
        root or REPO_ROOT, ".claude", ".cache", "provider_capacity.json")
    env["PROVIDER_CAPACITY_CACHE"] = os.path.join(temp_root, "provider_capacity.json")
    try:
        shutil.copyfile(live_capacity, env["PROVIDER_CAPACITY_CACHE"])
    except OSError:
        pass
    env["HARNESS_CAPACITY_WARM"] = "0"
    # No proof tests this machine's free memory; a proof of the launch gate plants its own floors.
    env["LAUNCH_MIN_COMMIT_GIB"] = "0"
    env["LAUNCH_MIN_PHYSICAL_GIB"] = "0"
    env.setdefault("HARNESS_CASE_JOBS", "2")
    env.update({name: temp_root for name in _TEMP_ENV})
    if "PATH" in env:
        env["PATH"] = _land_path(env["PATH"])
    sandbox_slots = os.path.join(temp_root, "slots")
    env["HARNESS_SLOT_DIR"] = sandbox_slots
    # A lane is loaned to the proof: the code it exercises (RuntimeSlots.ps1, a case pool) takes none of its own.
    env["HARNESS_SLOT_BUDGET"] = "1"
    if pytest_lane:
        # A pytest run holds no lane at all (`machine_slots.py run` passes it through), so the loan is empty and a case
        # pool borrows extras from a private folder, never from the live pool, whatever the caller's shell held.
        env["HARNESS_MACHINE_ROOT"] = sandbox_slots
        env.pop("HARNESS_LANE_CORES", None)
    else:
        env.setdefault("HARNESS_MACHINE_ROOT", _slot_root())
    for name in _CALLER_RUN_ENV:
        env.pop(name, None)
    for name in _CALLER_IDENTITY_ENV:
        env.pop(name, None)
    if root:
        env["CLAUDE_PROJECT_DIR"] = root
    env["HARNESS_CORE_ROOT"] = _core_root(root)
    home = os.path.join(temp_root, "home")
    os.makedirs(home, exist_ok=True)
    env["HARNESS_CALLER_HOME"] = os.path.expanduser("~")
    env.update({name: home for name in _RUNNER_HOME_ENV})
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    config = [("maintenance.auto", "false"), ("gc.auto", "0")]
    if os.name == "nt":
        config.append(("core.fscache", "true"))
    add_git_config(env, config)
    for name in [k for k in env if k.startswith("CLAUDE_") and k not in _RUNNER_CLAUDE_DECLARED]:
        del env[name]
    for exe in (os.environ.get("CLAUDE_CODE_EXECPATH"), os.path.expanduser("~/.local/bin/claude.exe"),
                os.path.expanduser("~/.local/bin/claude")):
        if exe and os.path.isfile(exe):
            env["CLAUDE_CODE_EXECPATH"] = exe
            break
    return env


def run_proof(path, timeout=None, root=None, *, behavior_cases=None, receipt_sink=None, reads_sink=None,
              reuse_cases=None, stretch=None, typed=False, pytest_lane=False):
    """(status, seconds, detail) — status is "pass", "cannot-run", "platform-skip" or "fail"; `typed=True`
    returns the same answer as an `AttemptResult` carrying the runner's own timeout as a field.

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
        timeout = DEFAULT_TIMEOUT
    temp_root = tempfile.mkdtemp(prefix="harness_proof_")
    env = proof_env(temp_root, timeout, root, reuse_cases, pytest_lane=pytest_lane)
    log_dir = None
    if reads_sink is not None and path.endswith(RECORDABLE):
        log_dir, recorder = _nested_log(temp_root)
        env["HARNESS_READ_LOG"] = log_dir
        env["HARNESS_READ_RECORDER"] = recorder
        kept = [e for e in env.get("PYTHONPATH", "").split(os.pathsep)
                if e and os.path.basename(os.path.normpath(e)) != os.path.basename(_RECORDER_DIR)]
        env["PYTHONPATH"] = os.pathsep.join([recorder] + kept)
        outer = re.sub(r'--require\s+(?:"(?:[^"\\]|\\.)*node_recorder\.js"|\S*node_recorder\.js)', "",
                       env.get("NODE_OPTIONS", ""))
        env["NODE_OPTIONS"] = " ".join(outer.split() + ["--require " + json.dumps(_NODE_RECORDER)])
    start = time.time()
    out = ""
    try:
        for command in _runner_cmds(path):
            pytest_run = command[1:3] == ["-m", "pytest"]
            returncode, part = _run_child(command, env, timeout, os.path.join(temp_root, "proof_output.txt"),
                                          cwd=root or REPO_ROOT, stretch=stretch)
            out += part
            if returncode != 0:
                break
        if log_dir:
            base, per_case = _read_events(log_dir), _case_events(log_dir)
            events = base + [e for case in per_case.values() for e in case]
            reads_sink.update(events=events, base=base, cases=per_case, temp=temp_root)
            if not events:
                # Keep what discriminates the two causes of a zero-event pass: a recorder that never loaded
                # (no beacon, and the output names the import fault) and one that loaded but wrote nothing.
                reads_sink.update(log=log_dir, log_files=_log_files(log_dir, ".jsonl"),
                                  beacons=_log_files(log_dir, ".beacon"), faults=_recorder_faults(out))
        if returncode is None:
            status, detail, timed_out = "fail", "TIMEOUT after %ds" % (time.time() - start), True
        elif returncode == 0 and _PLATFORM_SKIP_RE.search(out):
            status, detail, timed_out = "platform-skip", out, False
        elif returncode == 0:
            status, detail, timed_out = "pass", "", False
        elif returncode == 2 and not pytest_run:
            status, detail, timed_out = "cannot-run", out, False
        else:
            status, detail, timed_out = "fail", out, False
    except OSError as exc:
        status, detail, timed_out = "cannot-run", "runner unavailable: %s" % exc, False
    finally:
        _remove_temp_root(temp_root)
    if receipt_sink is not None:
        receipts = _case_receipts(path, behavior_cases, detail if "TIMEOUT" in detail else out, status)
        for receipt in receipts:
            receipt["proof"] = _norm(os.path.relpath(path, root or REPO_ROOT))
        receipt_sink.extend(receipts)
    elapsed = time.time() - start
    result = AttemptResult(status, elapsed, detail, timeout=timed_out)
    return result if typed else (status, elapsed, detail)


def run(ctx, job):
    """One attempt after admission: take, recheck, cap by the deadline, launch, release. The result is a
    typed `AttemptResult`; a missing slot store is cannot-run, an expired deadline or a cancel is skipped."""
    asked = time.time()
    split = {}
    try:
        handles = [] if ctx.held else ctx.take(job.exclusive, split=split)
    except ctx.slot_error as exc:
        return AttemptResult("cannot-run", 0.0, str(exc), wait_s=round(time.time() - asked, 2), wait_split=split)
    wait = round(time.time() - asked, 2)
    if handles is None:
        return SKIPPED._replace(wait_s=wait, wait_split=split)
    try:
        if ctx.stop is not None and ctx.stop():
            return MOVED._replace(wait_s=wait, wait_split=split)
        return _admitted_run(ctx, job)._replace(wait_s=wait, wait_split=split)
    finally:
        ctx.release(handles)


def _admitted_run(ctx, job):
    """`run`'s body once its slot is held: `recheck()` may answer from the pass cache, the deadline caps the
    timeout and its live-pace stretch or refuses the run. A run the cap cut short is skipped."""
    answer = job.recheck() if job.recheck else None
    if answer:
        return _as_result(answer)
    cut, deadline = False, ctx.deadline() if callable(ctx.deadline) else ctx.deadline
    timeout = job.timeout
    if deadline is not None:
        left = deadline - time.time()
        if left <= 0:
            return SKIPPED
        if timeout is None:
            timeout = ctx.default_timeout(job.proof)
        cut, timeout = left < timeout, min(timeout, left)
    stretch = job.stretch
    if stretch is None and ctx.pace is not None:
        stretch = ctx.pace.factor
    if stretch is not None and deadline is not None:
        pace, ceiling = stretch, max(1.0, left / timeout)
        stretch = lambda: min(pace(), ceiling)  # noqa: E731
    if job.reuse_cases:
        reuse_cases = job.reuse_cases()
    else:
        reuse_cases = None
    result = _as_result(ctx.launch(job._replace(timeout=timeout, root=job.root or ctx.root,
                                                reuse_cases=reuse_cases, stretch=stretch)))
    return SKIPPED if cut and result.timeout else result


def _as_result(value):
    """`value` as an `AttemptResult`; a three-field tuple from a stubbed launch keeps its words and gets its
    timeout read from the detail, the way every call read it before the field existed."""
    if isinstance(value, AttemptResult):
        return value
    status, run_s, detail = value[0], value[1], value[2]
    return AttemptResult(status, run_s, detail,
                         timeout=(status == "fail" and str(detail or "").startswith("TIMEOUT")))


@contextmanager
def serial(ctx):
    """Take every machine slot for the SERIAL proofs' first attempts, or spend the parent's inherited lane. An
    inherited lane has no exclusivity to claim, so it reports that and never pretends one; an expired deadline or
    unusable store yields False and lets each launch report its own admission."""
    if ctx.held:
        yield True
        return
    if ctx.inherited:
        print("harness_tests: SERIAL proofs run without an exclusive lease: this run spends an inherited lane",
              file=sys.stderr)
        yield False
        return
    try:
        handles = ctx.take(True)
    except ctx.slot_error:
        handles = None
    if handles is None:
        yield False
        return
    ctx.held = True
    try:
        yield True
    finally:
        ctx.held = False
        ctx.release(handles)
