"""Re-runnable proof for the harness_tests.py pass cache, machine proof slots and run deadline.

A proof's pass is reused while every input it reaches is byte-identical: under the static key, the proof
itself, each `.claude/` file it names, the code those files may run in turn (never a file only a comment or
docstring names), and the runner; under the observed key, what a Python proof actually read, probed and
listed in its last pass, with each unrecorded child's script keyed statically and its data arguments by
their bytes. The slots cap proofs
running at once across every runner process on the machine; a deadline stops a run that would
outlast its caller.

Each decision runs in process on planted facts: a planted launch (`plant`, standing for the recorder events a
child would log and the effect it would have) feeds `_run_selected`, and `record_pass` / `holds` take the key
decision over a manifest built from those events. The reads of the real world keep live cases, one per
boundary: the Python recorder (`case_a_recorded_python_run_keys_what_it_read` and the child-environment cases
beside it), the node recorder (js, mjs, a spaced path), the git spawn forms the recorder logs, a nested runner,
a faulty recorder and the runner's command line.

    python3 .claude/tests/test_harness_tests_pass_cache.py
"""
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout

# A battery run that reuses passes exports this to every proof; each case here sets it only where it means to.
os.environ.pop("HARNESS_REUSE_PASSES", None)
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402
import _git_fixture  # noqa: E402
import _hook_call  # noqa: E402
import _file_lock  # noqa: E402  (harness_tests puts .claude/hooks on sys.path)
from _case_pool import check, checked_cases, run_cases  # noqa: E402

def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True, capture_output=True)


_COUNTER = "<counter>"


def _build_seed(path):
    _git_fixture.init_repo(path)
    write(path, ".claude/settings.json", "{}\n")
    write(path, ".claude/tools/alpha.py", ALPHA)
    write(path, ".claude/tools/beta.py", "B = 1\n")
    write(path, ".claude/tools/table.json", "{}\n")
    write(path, ".claude/tools/gamma.py", "G = 1\n")
    write(path, ".claude/tests/test_alpha.py", proof_text(_COUNTER, 0))
    _git_fixture.commit_all(path, "seed")


def _template():
    """The seed repository every `make_repo` copies: built once (`_git_fixture.built`; `main` builds it before the case
    pool starts), so each case copies a built tree instead of paying `git init`, `add` and `commit`."""
    return str(_git_fixture.built("pass_cache.seed", _build_seed))


def make_repo(close=False, live=False):
    """tools/alpha.py imports beta and reads table.json; tools/gamma.py is unrelated. The proof
    test_alpha.py imports alpha and counts its own runs in a file outside the repo. `close` names the
    checkout as a close names its private one. The repository is a copy of `_template` whose seed commit is
    amended, for a `live` case whose child counts its own runs, to carry the case's counter path; every case starts on one committed, clean tree. The
    proof's planted launch (`plant`) logs what that proof reads: itself, alpha, beta and table.json."""
    repo = tempfile.mkdtemp(prefix="harnesspasscache_")
    os.environ.pop("HARNESS_SLOT_BUDGET", None)
    os.environ["HARNESS_MACHINE_ROOT"] = os.path.join(repo, "slots")
    if close:
        repo = os.path.join(repo, harness_tests._CHECKOUT_PREFIX + "fixture")
        os.makedirs(repo)
    _hook_call.copy_repo(_template(), repo, overlay=True)
    counter = os.path.join(tempfile.mkdtemp(prefix="harnesspasscount_"), "runs.txt")
    proof = os.path.join(repo, ".claude", "tests", "test_alpha.py")
    if live:  # a planted launch counts through its plan; only a real child reads the counter path from the proof's text
        write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0))
        git(repo, "commit", "-q", "-a", "--amend", "--no-edit")
    harness_tests.EXCLUDED = {}
    harness_tests.SERIAL = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    plant(repo, proof, counter)
    return repo, proof, counter


ALPHA = ("import os\nimport beta\nTABLE = 'table.json'\n"
         "DATA = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), TABLE)).read()\n")


def proof_text(counter, exit_code, extra=""):
    return ("import os, sys\nsys.dont_write_bytecode = True\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))\n"
            "import alpha\nwith open(%r, 'a') as fh:\n"
            "    fh.write(os.environ.get('HARNESS_SLOT_DIR', '') + '\\n')\n%ssys.exit(%d)\n"
            % (counter, extra, exit_code))


def walk_text(folder):
    """Proof lines that read every file under `folder`, as a real corpus-scanning proof does."""
    return ("for _d, _s, _n in os.walk(%r):\n    for _f in _n:\n"
            "        open(os.path.join(_d, _f), encoding='utf-8', errors='replace').read()\n" % folder)


def runs(counter):
    if not os.path.exists(counter):
        return 0
    with open(counter, encoding="utf-8") as fh:
        return len(fh.read().splitlines())


# ---- planted launches: the recorder events and effect a child would have, with no child --------------------------
_REAL_RUN_PROOF = harness_tests.run_proof
PLANS = {}  # {proof path: Plan}, what a planted launch of that proof logs and does
_TEMP = os.path.join(tempfile.gettempdir(), "harness_proof_planted")  # the temp root a planted run's sink names


class Plan:
    """One proof's planted run: `events()` the recorder log it stands for, `effect()` what the run does besides
    (an edit, a ref moved), `exit` its status, `counter` the file each launch appends to."""

    def __init__(self, counter, events, effect=None, exit=0):
        self.counter, self.events, self.effect, self.exit = counter, events, effect, exit


def _plan_key(path):
    return os.path.normcase(os.path.normpath(path))


def tool(repo, name):
    return os.path.join(repo, ".claude", "tools", name)


def _abs(repo, rel):
    return rel if os.path.isabs(rel) else os.path.join(repo, *rel.split("/"))


def spawn(*argv, cwd=None, genv=None, recorded=False):
    """A recorder `s` event: the child a proof started, as the audit hook logs it."""
    return ["s", {"argv": list(argv), "cwd": cwd, "genv": genv or {}, "recorded": recorded}]


def plant(repo, proof, counter, *, reads=(), probes=(), lists=(), stats=(), spawns=(), writes=(), effect=None,
          exit=0, base=True, own=False):
    """Register what a launch of `proof` logs: the alpha, beta and table.json reads of the standard proof and its own
    (`base`; `own` alone is the proof's own read), then `reads` (a list, or a function giving one when the run starts),
    `probes`, `lists`, `stats`, `spawns` and `writes` (repo-relative paths or absolute ones; a spawn without a cwd runs
    in `repo`)."""
    def events():
        more = reads() if callable(reads) else reads
        paths = ([proof] if base or own else []) + ([tool(repo, n) for n in ("alpha.py", "beta.py", "table.json")]
                                                     if base else []) + [_abs(repo, r) for r in more]
        found = [["r", p] for p in paths]
        for kind, group in (("p", probes), ("l", lists), ("t", stats), ("w", writes)):
            found += [[kind, _abs(repo, p)] for p in group]
        for event in spawns:
            found.append(["s", dict(event[1], cwd=event[1].get("cwd") or repo)])
        return found
    PLANS[_plan_key(proof)] = Plan(counter, events, effect, exit)


def add_proof(repo, name, counter, **plan):
    """A second proof beside test_alpha.py, planted the same way."""
    path = write(repo, ".claude/tests/" + name, proof_text(counter, 0))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add " + name)
    plant(repo, path, counter, **plan)
    return path


def _planted_run_proof(path, timeout=None, root=None, *, reads_sink=None, **_):
    plan = PLANS[_plan_key(path)]
    with open(plan.counter, "a", encoding="utf-8") as fh:
        fh.write(os.environ.get("HARNESS_SLOT_DIR", "") + "\n")
    if plan.effect:
        plan.effect()
    if reads_sink is not None and path.endswith(".py"):
        events = plan.events()
        reads_sink.update(events=events, base=events, cases={}, temp=_TEMP)
    return ("fail", 0.01, "FAIL: planted red") if plan.exit else ("pass", 0.01, "")


def run(repo, proofs, env=None, live=False):
    """_run_selected over `proofs` with the pass cache; returns {proof: status}. A launch is planted (`plant`) unless
    `live`, or the case stubbed `run_proof` itself."""
    saved = dict(os.environ)
    os.environ.update(env or {})
    os.environ.setdefault("HARNESS_SLOT_DIR", os.path.join(repo, "slots"))
    swapped = not live and harness_tests.run_proof is _REAL_RUN_PROOF
    if swapped:
        harness_tests.run_proof = _planted_run_proof
    statuses = {}
    try:
        with redirect_stdout(io.StringIO()):
            harness_tests._run_selected(proofs, False, statuses, None,
                                        harness_tests.RunOptions(harness_tests._pass_cache(repo, proofs)))
    finally:
        if swapped:
            harness_tests.run_proof = _REAL_RUN_PROOF
        os.environ.clear()
        os.environ.update(saved)
    return statuses


def record_pass(repo, proof, manifest=None):
    """Record a pass of `proof` as a finished run would, over its planted events (or `manifest`); returns its manifest."""
    cache = harness_tests._PassCache(repo)
    if manifest is None:
        manifest = cache.manifest(proof, {"events": PLANS[_plan_key(proof)].events(), "temp": _TEMP})
    cache.record(proof, key=cache.key(proof), manifest=manifest, observable=True)
    return manifest


def holds(repo, proof):
    """True when the recorded pass of `proof` still matches the checkout, as a run's lookup judges it."""
    cache = harness_tests._PassCache(repo)
    return cache.hit(proof, cache.lazy_key(proof))


# ---- the observed key over planted reads ----------------------------------------------------------------------------

def case_reuse_and_invalidation():
    repo, proof, counter = make_repo()
    run(repo, [proof])
    second = run(repo, [proof])
    check("a passed proof is reused while its inputs are unchanged",
          runs(counter) == 1 and second.get(proof) == "pass" and holds(repo, proof),
          "runs=%d status=%s" % (runs(counter), second))
    write(repo, ".claude/tools/gamma.py", "G = 2\n")
    check("an edit to a file the proof never reaches keeps the reuse", holds(repo, proof))
    for rel, text in ((".claude/tools/alpha.py", ALPHA + "A = 2\n"),
                      (".claude/tools/beta.py", "B = 2\n"),
                      (".claude/tools/table.json", "{\"a\": 1}\n")):
        write(repo, rel, text)
        check("an edit to %s (reached by the proof) reruns it" % rel.rsplit("/", 1)[-1], not holds(repo, proof))
        record_pass(repo, proof)
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "# edited\n"))
    check("an edit to the proof itself reruns it", not holds(repo, proof))
    record_pass(repo, proof)
    git(repo, "rm", "-q", "--cached", ".claude/tools/beta.py")
    check("a change in whether a reached file is tracked reruns it", not holds(repo, proof))


def case_failure_is_never_reused():
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, exit=1)
    run(repo, [proof])
    statuses = run(repo, [proof])
    check("a failed proof runs again", runs(counter) == 2 and statuses.get(proof) == "fail",
          "runs=%d status=%s" % (runs(counter), statuses))


def case_opt_outs():
    repo, proof, counter = make_repo()
    run(repo, [proof])
    run(repo, [proof], {"HARNESS_NO_PASS_CACHE": "1"})
    check("HARNESS_NO_PASS_CACHE=1 runs a cached proof", runs(counter) == 2, "runs=%d" % runs(counter))
    scanner_counter = counter + ".scan"
    scanner = add_proof(repo, "test_uncorpused_scan.py", scanner_counter)
    saved = harness_tests._catalog.ALWAYS
    harness_tests._catalog.ALWAYS = saved + ("test_uncorpused_scan.py",)
    try:
        run(repo, [scanner])
        run(repo, [scanner])
    finally:
        harness_tests._catalog.ALWAYS = saved
    check("a dir-scanning proof with no corpus entry is never reused", runs(scanner_counter) == 2,
          "runs=%d" % runs(scanner_counter))
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    harness_tests.run_proof = _planted_run_proof
    try:
        with redirect_stdout(io.StringIO()):
            harness_tests.run_all(repo)
    finally:
        harness_tests.run_proof = _REAL_RUN_PROOF
        del os.environ["HARNESS_SLOT_DIR"]
    check("the full battery ignores the cache", runs(counter) == 3, "runs=%d" % runs(counter))


def case_the_full_battery_reuses_recorded_passes_when_asked():
    repo, proof, counter = make_repo()
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    os.environ["HARNESS_REUSE_PASSES"] = "1"
    harness_tests.run_proof = _planted_run_proof
    out = io.StringIO()
    try:
        with redirect_stdout(io.StringIO()):
            harness_tests.run_all(repo)
        with redirect_stdout(out):
            harness_tests.run_all(repo)
    finally:
        harness_tests.run_proof = _REAL_RUN_PROOF
        del os.environ["HARNESS_REUSE_PASSES"]
        del os.environ["HARNESS_SLOT_DIR"]
    check("a full battery with HARNESS_REUSE_PASSES reuses a recorded pass",
          runs(counter) == 1 and "reused from the pass cache" in out.getvalue(),
          "runs=%d out=%r" % (runs(counter), out.getvalue()[-300:]))


def case_slots():
    repo, proof, counter = make_repo()
    slots = os.path.join(repo, "slots")
    env = {"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": "1"}
    held = _file_lock.acquire(os.path.join(slots, "slot-0.lock"), 5)
    try:
        statuses = run(repo, [proof], dict(env, HARNESS_DEADLINE=str(time.time() + 0.5)))
    finally:
        _file_lock.release(held)
    check("a proof waits for a free machine slot and is skipped at the deadline",
          runs(counter) == 0 and statuses.get(proof) == "skipped", "runs=%d status=%s" % (runs(counter), statuses))
    statuses = run(repo, [proof], env)
    check("a freed slot runs the proof", runs(counter) == 1 and statuses.get(proof) == "pass",
          "runs=%d status=%s" % (runs(counter), statuses))
    serial_counter = counter + ".serial"
    serial = add_proof(repo, "test_serial.py", serial_counter)
    harness_tests.SERIAL = {"test_serial.py": "owns the machine"}
    write(repo, "slots/width.json", '{"width": 2}')
    held = _file_lock.acquire(os.path.join(slots, "slot-1.lock"), 5)
    try:
        statuses = run(repo, [serial], {"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": "2",
                                        "HARNESS_DEADLINE": str(time.time() + 0.5)})
    finally:
        _file_lock.release(held)
        harness_tests.SERIAL = {}
    check("a serial proof waits for every machine slot", runs(serial_counter) == 0 and statuses.get(serial) == "skipped",
          "runs=%d status=%s" % (runs(serial_counter), statuses))


def case_a_pass_records_only_the_bytes_it_ran_on():
    """An input that changes between the key and the run leaves no pass: the pre-run key names bytes
    the proof never ran on, and reverting to them must rerun the proof."""
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, effect=lambda: write(repo, ".claude/tools/beta.py", "B = 9\n"))
    run(repo, [proof])
    write(repo, ".claude/tools/beta.py", "B = 1\n")
    run(repo, [proof])
    check("a pass whose reached input moved during the run is not recorded under the pre-run key",
          runs(counter) == 2, "runs=%d" % runs(counter))


def case_a_pass_never_lands_under_a_key_taken_before_the_slot_wait():
    """The key is taken again once the slot is held: bytes that changed while the run waited and were
    put back before the proof ended must not leave a pass under the old bytes' key."""
    repo, proof, counter = make_repo()
    real_take = harness_tests._take_slots

    def take(exclusive, count=1, split=None):
        write(repo, ".claude/tools/beta.py", "B = 7\n")
        return real_take(exclusive, count)

    plant(repo, proof, counter, effect=lambda: write(repo, ".claude/tools/beta.py", "B = 1\n"))
    harness_tests._take_slots = take
    try:
        run(repo, [proof])
    finally:
        harness_tests._take_slots = real_take
    run(repo, [proof])
    check("an input changed during the slot wait and restored during the run leaves no reusable pass",
          runs(counter) == 2, "runs=%d" % runs(counter))


def case_data_named_by_transitively_loaded_code_joins_the_reach():
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/beta.py", "B = 1\nDEEP = 'deep.json'\n")
    write(repo, ".claude/tools/deep.json", "{}\n")
    git(repo, "add", "-A")
    cache = harness_tests._pass_cache(repo, [proof])
    check("the static reach holds data named by a module the proof's module imports",
          ".claude/tools/deep.json" in cache.reach(proof), "reach=%s" % sorted(cache.reach(proof)))
    record_pass(repo, proof)
    write(repo, ".claude/tools/deep.json", "{\"a\": 1}\n")
    check("an edit to data that imported code only names, never reads, keeps the reuse", holds(repo, proof))
    write(repo, ".claude/tools/beta.py", "import os\nB = 1\nDEEP = open(os.path.join(os.path.dirname("
          "os.path.abspath(__file__)), 'deep.json')).read()\n")
    plant(repo, proof, counter, reads=[".claude/tools/deep.json"])
    record_pass(repo, proof)
    write(repo, ".claude/tools/deep.json", "{\"a\": 2}\n")
    check("an edit to data the imported code reads reruns the proof", not holds(repo, proof))


def case_a_probed_absent_path_reruns_when_created():
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, probes=[".claude/tools/maybe.json"])
    record_pass(repo, proof)
    check("a probed path that stays absent keeps the reuse", holds(repo, proof))
    write(repo, ".claude/tools/maybe.json", "{}\n")
    check("creating a path the proof only probed reruns it", not holds(repo, proof))


def case_a_listed_directory_gaining_an_entry_reruns():
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, lists=[".claude/tools"])
    record_pass(repo, proof)
    write(repo, ".claude/tools/gamma.py", "G = 2\n")
    check("an edit inside a listed directory that the proof never reads keeps the reuse", holds(repo, proof))
    write(repo, ".claude/tools/new_entry.txt", "x\n")
    check("a new entry in a directory the proof listed reruns it", not holds(repo, proof))


def case_an_ignored_path_is_run_exhaust_and_never_an_input():
    """A close proves in a fresh checkout that its own proofs fill with ignored caches and scratch, so a key
    that held them moved mid-run: a pass recorded after sibling proofs wrote them missed a lookup made before.
    In a close's checkout git's ignored paths are that close's exhaust, not an input; a path git can see still is."""
    repo, proof, counter = make_repo(close=True)
    write(repo, ".gitignore", ".claude/cache/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ignore the cache")
    cache = os.path.join(repo, ".claude", "cache")
    state = write(repo, ".claude/cache/state.txt", "1\n")
    plant(repo, proof, counter, reads=[state], lists=[os.path.join(repo, ".claude"), cache],
          probes=[os.path.join(cache, "later.txt")])
    record_pass(repo, proof)
    write(repo, ".claude/cache/later.txt", "x\n")
    write(repo, ".claude/cache/state.txt", "2\n")
    check("an entry, a probed path or a file's bytes that git ignores never rerun a pass", holds(repo, proof))
    shutil.rmtree(cache)
    check("an ignored directory the proof listed, here and gone again, keeps the reuse (a proof lists its own "
          "randomly named scratch folder)", holds(repo, proof))
    write(repo, ".claude/cache/state.txt", "3\n")
    write(repo, ".claude/visible.txt", "x\n")
    check("an entry git can see in a listed directory still reruns it", not holds(repo, proof))


def case_an_ignored_path_is_an_input_outside_a_close_checkout():
    """A session's checkout holds ignored files the close's clean checkout never has (local settings, logs).
    A pass that read one there proves nothing about a checkout without it, so outside a close's own checkout
    an ignored path is keyed like any other."""
    for label, change in (("bytes", lambda repo: write(repo, ".claude/cache/state.txt", "2\n")),
                          ("a probed path", lambda repo: write(repo, ".claude/cache/later.txt", "x\n")),
                          ("a listed entry", lambda repo: write(repo, ".claude/cache/more.txt", "x\n"))):
        repo, proof, counter = make_repo()
        write(repo, ".gitignore", ".claude/cache/\n")
        cache = os.path.join(repo, ".claude", "cache")
        state = write(repo, ".claude/cache/state.txt", "1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "ignore the cache")
        plant(repo, proof, counter, reads=[state], lists=[cache], probes=[os.path.join(cache, "later.txt")])
        record_pass(repo, proof)
        check("an untouched ignored input keeps the reuse outside a close checkout (%s)" % label, holds(repo, proof))
        change(repo)
        check("outside a close checkout, %s git ignores reruns the proof that observed it" % label,
              not holds(repo, proof))


def case_an_unrecorded_child_keys_its_script_statically():
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/child.py", "CHILD_DATA = 'child_data.json'\n")
    write(repo, ".claude/tools/child_data.json", "{}\n")
    git(repo, "add", "-A")
    plant(repo, proof, counter, spawns=[spawn(sys.executable, "-I", tool(repo, "child.py"))])
    record_pass(repo, proof)
    check("a proof with an isolated Python child is reused while nothing changes", holds(repo, proof))
    write(repo, ".claude/tools/child_data.json", "{\"a\": 1}\n")
    check("an edit to data the unrecorded child's script names reruns the proof", not holds(repo, proof))


def case_a_missing_recorder_records_no_observed_key():
    """A checkout that has the runner but not its recorder (a consumer that synced one file) logs no
    events; a manifest built from nothing would reuse the pass forever, so none is recorded. The next run,
    with the recorder back, reruns the proof."""
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, base=False)
    run(repo, [proof])
    row = (harness_tests._pass_cache(repo, [proof]).variants(proof) or [{}])[0]
    check("a run whose recorder never loaded records no observed key", "okey" not in row and row.get("key"),
          "row=%s" % sorted(row))
    plant(repo, proof, counter)
    run(repo, [proof])
    check("a pass that recorded no manifest reruns on the next run", runs(counter) == 2, "runs=%d" % runs(counter))


# ---- live: what the recorders and the launcher really log, one case per boundary ------------------------------------

def commit_all(repo, message, paths=("-A",)):
    """Commit `paths` (default: everything, which also commits the files a run left in the repo)."""
    git(repo, "add", *paths)
    git(repo, "commit", "-q", "-m", message)


def child_proof(repo, counter, body):
    """A proof whose extra lines are `body`, committed."""
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "import subprocess\n" + body))
    commit_all(repo, "proof body")


_CALLER_NAMES = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_PROMPT_FILE",
                 "CLAUDE_CODE_SIDECAR_LABEL", "SIDECAR_PARENT_SESSION_ID", "HARNESS_DEADLINE", "HARNESS_NO_PASS_CACHE",
                 "HARNESS_REUSE_PASSES", "HARNESS_TEST_JOBS", "HARNESS_MACHINE_SLOTS")


def case_a_proofs_environment_drops_the_callers_identity_and_run_knobs():
    """`proof_env` builds what every proof child inherits: never the calling session's or delegate's identity, nor the
    caller's run knobs (a runner the proof starts decides its own reuse and width), and a private slot folder, so a
    nested runner never waits on its parent's slot. The live child confirms it sees the same
    (`case_a_recorded_python_run_keys_what_it_read`)."""
    repo, _proof, _counter = make_repo()
    slots = os.path.join(repo, "slots")
    saved = dict(os.environ)
    os.environ.update({n: "caller" for n in _CALLER_NAMES})
    os.environ["HARNESS_SLOT_DIR"] = slots
    try:
        env = harness_tests._attempt.proof_env(os.path.join(repo, "proof_temp"), 77, repo)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    leaked = [n for n in _CALLER_NAMES if n in env]
    check("a proof never inherits the calling session's or delegate's identity, or the caller's run knobs",
          not leaked, "leaked=%s" % leaked)
    check("a proof learns the timeout it runs under from its environment", env.get("HARNESS_PROOF_TIMEOUT_S") == "77",
          str(env.get("HARNESS_PROOF_TIMEOUT_S")))
    seen = env.get("HARNESS_SLOT_DIR") or ""
    check("a proof's own runs get a private slot dir, so a nested runner never waits on its parent's slot",
          seen and os.path.normcase(os.path.abspath(seen)) != os.path.normcase(os.path.abspath(slots))
          and os.path.normcase(os.path.abspath(seen)).startswith(
              os.path.normcase(os.path.abspath(os.path.join(repo, "proof_temp")))), "HARNESS_SLOT_DIR=%r" % seen)


def case_a_recorded_python_run_keys_what_it_read():
    """One real child under the Python recorder, as `run_proof` starts it: the events it logs build the manifest the
    key rests on. The proof imports a module that reads data, fails an import, stats and copies a file of the checkout,
    and reports the environment it saw; each fact below is the recorder's or the launcher's own decision."""
    repo, proof, counter = make_repo(live=True)
    write(repo, ".claude/tools/copied.txt", "x\n")
    git(repo, "add", "-A")
    seen = os.path.join(os.path.dirname(counter), "seen.json")
    gamma, copied = tool(repo, "gamma.py"), tool(repo, "copied.txt")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, (
        "import json, shutil, tempfile\n"
        "try:\n    import optional_mod\nexcept ImportError:\n    pass\n"
        "os.stat(%r).st_size\n"
        "shutil.copy2(%r, os.path.join(tempfile.mkdtemp(), 'copied.txt'))\n"
        "names = %r\n"
        "with open(%r, 'w') as fh:\n"
        "    json.dump({'leaked': [n for n in names if n in os.environ], 'timeout': os.environ.get('HARNESS_PROOF_TIMEOUT_S'),\n"
        "               'slots': os.environ.get('HARNESS_SLOT_DIR')}, fh)\n" % (gamma, copied, _CALLER_NAMES, seen))))
    git(repo, "commit", "-q", "-a", "--amend", "--no-edit")
    saved, sink = dict(os.environ), {}
    os.environ.update({n: "caller" for n in _CALLER_NAMES})
    try:
        status = harness_tests.run_proof(proof, timeout=77, root=repo, reads_sink=sink)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    with open(seen, encoding="utf-8") as fh:
        child = json.load(fh)
    check("a recorded proof passes and the child sees no caller identity or run knob, and its own timeout",
          status[0] == "pass" and child["leaked"] == [] and child["timeout"] == "77", "%s %s" % (status[:2], child))
    check("the child's slot folder is its private one", "harness_proof_" in (child["slots"] or ""), str(child))
    manifest = harness_tests._PassCache(repo).manifest(proof, sink)
    files = set((manifest or {}).get("files", []))
    check("the imported modules, their data, the stat-ed file and the copy2-copied file join the observed files",
          {".claude/tools/alpha.py", ".claude/tools/beta.py", ".claude/tools/table.json",
           ".claude/tools/gamma.py", ".claude/tools/copied.txt"} <= files and not manifest.get("opaque"), str(manifest))
    check("the import system's own listing of every sys.path folder is no scan of the proof",
          manifest.get("dirs") == [] and ".claude/tools/optional_mod.py" in manifest.get("probes", []), str(manifest))
    record_pass(repo, proof, manifest)
    check("the recorded pass holds while nothing it read has changed", holds(repo, proof))
    write(repo, ".claude/tools/unrelated_new.py", "U = 1\n")
    check("a file added beside an imported module keeps the reuse", holds(repo, proof))
    write(repo, ".claude/tools/optional_mod.py", "O = 1\n")
    check("a module appearing where the proof's import failed reruns it", not holds(repo, proof))
    os.remove(tool(repo, "optional_mod.py"))
    write(repo, ".claude/tools/gamma.py", "G = 100000\n")
    check("a python proof that stat-ed a file reruns when its size changes", not holds(repo, proof))


def case_a_stat_of_a_checkout_file_keys_its_bytes():
    """`os.stat` and `statSync` observe a file's size, which the key held as a presence only. The recorder logs a stat as
    a `t` event; the node recorder's is `case_a_node_proofs_reads_key_its_manifest`."""
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, stats=[".claude/tools/gamma.py"])
    manifest = record_pass(repo, proof)
    check("a stat of a checkout file joins the observed files", ".claude/tools/gamma.py" in manifest["files"],
          str(manifest))
    write(repo, ".claude/tools/gamma.py", "G = 100000\n")
    check("a python proof that stat-ed a file reruns when its size changes", not holds(repo, proof))


def case_a_probed_path_changing_kind_reruns():
    repo, proof, counter = make_repo()
    thing = os.path.join(repo, ".claude", "tools", "thing")
    write(repo, ".claude/tools/thing", "x\n")
    git(repo, "add", "-A")
    plant(repo, proof, counter, probes=[thing])
    record_pass(repo, proof)
    check("a probed file keeps the reuse while it stays a file", holds(repo, proof))
    os.remove(thing)
    os.makedirs(thing)
    check("a probed file that became a directory reruns the proof", not holds(repo, proof))


def _recorded_python(code, pythonpath, log, cwd):
    """Run `code` in a Python whose PYTHONPATH is `pythonpath`, recording into `log`."""
    env = dict(os.environ, HARNESS_READ_LOG=log, PYTHONPATH=os.pathsep.join(pythonpath))
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True)


def case_the_recorder_runs_the_sitecustomize_it_shadows():
    """A proof helper that keeps the recorder first on a child's PYTHONPATH still gets its own
    sitecustomize (the bench sandbox's audit), and the child is still recorded."""
    root = tempfile.mkdtemp(prefix="harnesspasscache_chain_")
    own, log = os.path.join(root, "own"), os.path.join(root, "reads")
    marker = os.path.join(root, "own_ran.txt")
    write(own, "sitecustomize.py", "open(%r, 'w').write('ran')\n" % marker)
    data = write(root, "data.txt", "x\n")
    result = _recorded_python("open(%r).read()" % data, [harness_tests._RECORDER_DIR, own], log, root)
    check("a shadowed sitecustomize still runs under the recorder", os.path.exists(marker),
          result.stdout + result.stderr)
    reads = [e[1] for e in harness_tests._attempt._read_events(log) or [] if e[0] == "r"]
    check("a child whose own sitecustomize the recorder chains is still recorded",
          any(os.path.normcase(r) == os.path.normcase(os.path.abspath(data)) for r in reads), str(reads)[:300])


def case_a_spawned_child_is_recorded_only_when_the_recorder_loads():
    """Python imports only the first sitecustomize on its path. A child that lists the recorder behind another one never
    loads it, so its spawn is not recorded and its reads stay unbounded (the manifest is opaque). The slot bootstrap
    that machine admission puts first starts the next sitecustomize, the recorder's, so that child is recorded. A
    sandboxed child audits every open, so a spawn whose path starts with the recorder reads no sitecustomize of it
    (the bench sandbox proofs failed their read audit on it). One outer child starts the three."""
    repo, proof, _counter = make_repo()
    root = tempfile.mkdtemp(prefix="harnesspasscache_first_")
    own, log, plain = os.path.join(root, "own"), os.path.join(root, "reads"), os.path.join(root, "plain_reads")
    write(own, "sitecustomize.py", "OWN = 1\n")
    data = write(root, "data.txt", "x\n")
    child = write(root, "child.py", "import sys\nopen(sys.argv[1]).read()\n")
    recorder, bootstrap = harness_tests._RECORDER_DIR, os.path.join(_SCRIPTS_DIR, "_slot_bootstrap")
    code = ("import os, subprocess, sys\n"
            "def start(path, log=None):\n"
            "    env = dict(os.environ, PYTHONPATH=os.pathsep.join(path))\n"
            "    if log:\n        env['HARNESS_READ_LOG'] = log\n"
            "    subprocess.run([sys.executable, %r, %r], env=env, check=True)\n"
            "start([%r, %r])\nstart([%r, %r])\nstart([%r], %r)\n"
            % (child, data, own, recorder, bootstrap, recorder, recorder, plain))
    result = _recorded_python(code, [recorder], log, repo)
    events = harness_tests._attempt._read_events(log)
    spawns = [e[1] for e in events if e[0] == "s"]
    check("the first two children started (the third logs to its own folder)", len(spawns) == 2,
          "%s %s" % (spawns, result.stderr[-300:]))
    behind_other, behind_bootstrap = (spawns + [{}, {}])[:2]
    check("a spawn whose first sitecustomize is not the recorder is not marked recorded",
          not behind_other.get("recorded"), str(behind_other)[:300])
    manifest = harness_tests._pass_cache(repo, [proof]).manifest(proof, {"events": [["s", behind_other]], "temp": root})
    check("that unrecorded inline child makes the manifest opaque",
          isinstance(manifest, dict) and manifest.get("opaque") is True, str(manifest))
    check("a spawn behind the slot bootstrap is marked recorded", behind_bootstrap.get("recorded"), str(spawns)[:300])
    check("the bootstrapped child's read reached the log",
          any(e[0] == "r" and os.path.normcase(e[1]) == os.path.normcase(os.path.abspath(data)) for e in events))
    manifest = harness_tests._pass_cache(repo, [proof]).manifest(proof, {"events": [["s", behind_bootstrap]], "temp": root})
    check("so its manifest is not opaque", isinstance(manifest, dict) and not manifest.get("opaque"), str(manifest))
    # The sitecustomize the recorder shadows runs by design (a system Python ships one), so only the recorder's own counts.
    norm = os.path.normcase(os.path.abspath(recorder))
    plain_events = harness_tests._attempt._read_events(plain) or []
    opened = [e[1] for e in plain_events if e[0] == "r" and e[1].endswith("sitecustomize.py")
              and os.path.normcase(os.path.dirname(os.path.abspath(e[1]))) == norm]
    check("a spawn whose path starts with the recorder reads no sitecustomize (and was recorded)",
          not opened and any(e[0] == "r" and os.path.normcase(e[1]) == os.path.normcase(os.path.abspath(data))
                             for e in plain_events), str(opened))


def case_a_quoted_shell_script_spawn_splits_as_its_child_reads_it():
    """Windows hands the spawn audit event the joined command line. A `bash -c` script whose inner quotes
    list2cmdline escaped must split as the child's C runtime reads it, so the repo script it names is keyed
    statically; a quote-blind split marked every such manifest opaque."""
    repo, proof, _counter = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    script = "cd \"%s\" && python3 .claude/tools/gamma.py --say 'it is'" % repo.replace("\\", "/")
    log = tempfile.mkdtemp(prefix="harnesspasscache_reads_")
    spawn_code = ("import subprocess\ntry:\n    subprocess.run(['no_such_shell_for_the_proof', '-c', %r])\n"
                  "except OSError:\n    pass\n" % script)
    _recorded_python(spawn_code, [harness_tests._RECORDER_DIR], log, repo)
    events = harness_tests._attempt._read_events(log)
    manifest = cache.manifest(proof, {"events": events, "temp": log})
    check("a recorded spawn of a quoted shell script is captured", any(e[0] == "s" for e in events or []),
          str(events))
    check("a quoted shell script spawn keys the repo script it names, not an opaque manifest",
          isinstance(manifest, dict) and not manifest.get("opaque")
          and manifest.get("static") == [".claude/tools/gamma.py"], str(manifest))


def case_a_node_proofs_reads_key_its_manifest():
    """A JS proof's reads and its node children's are recorded by the Node preload, so a repo file it
    reads keys the pass and an edit to an unread file does not rerun it; a stat observes a file's size."""
    repo, proof, _counter = make_repo()
    write(repo, ".claude/tools/data.txt", "x\n")
    write(repo, ".claude/tools/child.js", "require('fs').readFileSync(process.argv[2]);\n")
    js = write(repo, ".claude/tests/child_test.js",
               "const fs = require('fs'), cp = require('child_process'), path = require('path');\n"
               "const tools = path.join(__dirname, '..', 'tools');\n"
               "fs.readFileSync(path.join(tools, 'table.json'));\n"
               "fs.statSync(path.join(tools, 'gamma.py')).size;\n"
               "fs.readdirSync(tools);\n"
               "cp.execFileSync('node', [path.join(tools, 'child.js'), path.join(tools, 'data.txt')]);\n")
    sink = {}
    status = harness_tests.run_proof(js, root=repo, reads_sink=sink)[0]
    manifest = harness_tests._pass_cache(repo, [js]).manifest(js, sink) if sink.get("events") else None
    check("a recorded JS proof passes", status == "pass", status)
    check("a JS proof's own read and its node child's read join the observed files",
          isinstance(manifest, dict) and not manifest.get("opaque")
          and {".claude/tools/table.json", ".claude/tools/data.txt"} <= set(manifest.get("files", [])),
          str(manifest))
    check("a JS proof's stat of a checkout file joins the observed files",
          isinstance(manifest, dict) and ".claude/tools/gamma.py" in manifest.get("files", []), str(manifest))
    check("a JS proof's directory listing joins the observed dirs",
          isinstance(manifest, dict) and ".claude/tools" in manifest.get("dirs", []), str(manifest))
    check("a JS proof's node child is not keyed statically",
          isinstance(manifest, dict) and manifest.get("static") == [], str(manifest))


def case_an_mjs_proofs_reads_key_its_manifest():
    """`node --test` runs an ESM proof through the same node runner, so an `.mjs` proof gets the recorder too:
    its read keys the pass instead of leaving an `observed: False` row that reruns on every close."""
    repo, proof, _counter = make_repo()
    write(repo, ".claude/tools/data.txt", "x\n")
    mjs = write(repo, ".claude/tests/tables_test.mjs",
                "import fs from 'node:fs';\n"
                "import path from 'node:path';\n"
                "import test from 'node:test';\n"
                "import { fileURLToPath } from 'node:url';\n"
                "const here = path.dirname(fileURLToPath(import.meta.url));\n"
                "test('reads', () => { fs.readFileSync(path.join(here, '..', 'tools', 'data.txt')); });\n")
    sink = {}
    status = harness_tests.run_proof(mjs, root=repo, reads_sink=sink)[0]
    manifest = harness_tests._pass_cache(repo, [mjs]).manifest(mjs, sink) if sink.get("events") else None
    check("a recorded mjs proof passes", status == "pass", status)
    check("an mjs proof's read joins the observed files",
          isinstance(manifest, dict) and ".claude/tools/data.txt" in manifest.get("files", []), str(manifest))


def case_a_node_recorder_in_a_spaced_path_still_records():
    """--require <path> splits at a space; the path is quoted on both sides, so a checkout path with a space
    still starts node and the node child is recorded."""
    repo, proof, _counter = make_repo()
    spaced = os.path.join(repo, "spaced dir")
    os.makedirs(spaced)
    recorder = os.path.join(spaced, "node_recorder.js")
    shutil.copyfile(harness_tests._NODE_RECORDER, recorder)
    write(repo, ".claude/tools/data.txt", "x\n")
    write(repo, ".claude/tools/child.js", "require('fs').readFileSync(process.argv[2]);\n")
    js = write(repo, ".claude/tests/spaced_test.js",
               "const cp = require('child_process'), path = require('path');\n"
               "const tools = path.join(__dirname, '..', 'tools');\n"
               "cp.execFileSync('node', [path.join(tools, 'child.js'), path.join(tools, 'data.txt')]);\n")
    sink = {}
    saved = harness_tests._NODE_RECORDER
    saved_attempt = harness_tests._attempt._NODE_RECORDER
    harness_tests._NODE_RECORDER = harness_tests._attempt._NODE_RECORDER = recorder
    try:
        status = harness_tests.run_proof(js, root=repo, reads_sink=sink)[0]
    finally:
        harness_tests._NODE_RECORDER = saved
        harness_tests._attempt._NODE_RECORDER = saved_attempt
    manifest = harness_tests._pass_cache(repo, [js]).manifest(js, sink) if sink.get("events") else None
    check("a JS proof passes with the recorder in a spaced path", status == "pass", status)
    check("the node child's read is still recorded",
          isinstance(manifest, dict) and ".claude/tools/data.txt" in manifest.get("files", []), str(manifest))


def nested_runner_proof(repo, counter, inner_body):
    """A proof that runs this runner on a fixture proof under its own temp root, as the runner's own proofs do."""
    inner = os.path.join(repo, ".claude", "tests", "inner_fixture.txt")
    write(repo, ".claude/tests/inner_fixture.txt", inner_body)
    child_proof(repo, counter, (
        "sys.path.insert(0, %r)\nimport harness_tests, tempfile, shutil\n"
        "_fixture = os.path.join(tempfile.mkdtemp(), 'test_inner.py')\n"
        "shutil.copyfile(%r, _fixture)\n"
        "_status = harness_tests.run_proof(_fixture, reads_sink={})\n"
        "assert _status[0] == 'pass', _status\n" % (_SCRIPTS_DIR, inner)))


def case_a_nested_runner_keeps_the_outer_recorder_on_its_child():
    """The runner points a proof's child at its own read log. A proof that runs the runner on a fixture proof
    (the runner's own proofs do) must still have its outer recorder follow that child: what the child read keys the
    outer pass, so an edit to it reruns the proof and an edit to anything else does not. A close runs the outer
    runner from one checkout and the proof's runner from its private checkout, so the two recorders sit in different
    folders: the outer runner here records from a copy."""
    saved = (harness_tests._RECORDER_DIR, harness_tests._NODE_RECORDER,
             harness_tests._attempt._RECORDER_DIR, harness_tests._attempt._NODE_RECORDER)
    copy = os.path.join(tempfile.mkdtemp(prefix="harnesspassrecorder_"), "_read_recorder")
    shutil.copytree(saved[0], copy, ignore=shutil.ignore_patterns("__pycache__"))
    harness_tests._RECORDER_DIR, harness_tests._NODE_RECORDER = copy, os.path.join(copy, "node_recorder.js")
    harness_tests._attempt._RECORDER_DIR = copy
    harness_tests._attempt._NODE_RECORDER = os.path.join(copy, "node_recorder.js")
    try:
        repo, proof, counter = make_repo(live=True)
        write(repo, ".claude/tools/unrelated.py", "U = 0\n")
        nested_runner_proof(repo, counter, "import os\nopen(%r).read()\n" % tool(repo, "gamma.py"))
        outside = tempfile.mkdtemp(prefix="harnesspassout_")
        harness_tests.STAMP_PATH = os.path.join(outside, "stamp.json")
        run(repo, [proof], {"HARNESS_SLOT_DIR": os.path.join(outside, "slots")}, live=True)
        check("the live run recorded a pass that holds", runs(counter) == 1 and holds(repo, proof),
              "runs=%d" % runs(counter))
        write(repo, ".claude/tools/unrelated.py", "U = 1\n")
        check("an edit to a file the nested child never read keeps the proof whose nested runner ran the child",
              holds(repo, proof))
        write(repo, ".claude/tools/gamma.py", "X = 9\n")
        check("an edit to a file the nested child read reruns the proof whose nested runner ran the child",
              not holds(repo, proof))
    finally:
        (harness_tests._RECORDER_DIR, harness_tests._NODE_RECORDER,
         harness_tests._attempt._RECORDER_DIR, harness_tests._attempt._NODE_RECORDER) = saved


# ---- git: which state a spawned git can observe, and which moves of the repository move that state --------------------

def _git_world(repo):
    """The places a spawned git can run against: a repository outside the checkout (`source`), a folder of the same
    repository (`sibling`, a linked worktree), one nested in the checkout, a plain folder (`elsewhere`), and a blob
    in the sibling pinned by its full id."""
    elsewhere = tempfile.mkdtemp(prefix="harnesspassgit_")
    git(elsewhere, "init", "-q", "src")
    sibling = os.path.join(tempfile.mkdtemp(prefix="harnesspasssibling_"), "sib")
    git(repo, "worktree", "add", "-q", "-b", "sib", sibling)
    write(sibling, "pinned.txt", "v1\n")
    commit_all(sibling, "pin", ["pinned.txt"])
    sha = subprocess.run(["git", "commit-tree", "HEAD^{tree}", "-m", "loose"], cwd=sibling, capture_output=True,
                         text=True).stdout.strip()
    write(repo, ".gitignore", "nested/\n")
    git(repo, "init", "-q", "nested")
    return {"elsewhere": elsewhere, "source": os.path.join(elsewhere, "src"), "sibling": sibling,
            "nested": os.path.join(repo, "nested"), "sha": sha}


_WHOLE = "head+changes+refs+config+worktrees+untracked"


def case_a_spawned_git_names_the_state_it_can_observe():
    """`_git_scope` over each form of git a proof runs, as the recorder logs it: the parts of the repository (`_GIT_SCOPES`)
    that git can print, none for a git that reads nothing of this checkout's state, every part for a git the rule cannot
    place, `sibling` (never reused) for a git in another worktree of the repository. Which moves of the repository move
    each part is `case_the_parts_of_the_repository_key_the_moves_that_can_change_them`; a pass is reused only when no part
    its git can print moved."""
    repo, proof, _counter = make_repo()
    world = _git_world(repo)
    cache = harness_tests._PassCache(repo)
    elsewhere, source, sib, nested, sha = (world[k] for k in ("elsewhere", "source", "sibling", "nested", "sha"))
    git_exe = "C:/Program Files/Git/cmd/git.exe"
    git_dir = os.path.join(repo, ".git")
    rows = (
        # a git that reads nothing of this checkout's state
        ("init at a plain path under the temp root", ["git", "init", "-q", os.path.join(elsewhere, "fresh")], {}, ""),
        ("rev-parse that prints where a repository lives", ["git", "rev-parse", "--git-common-dir"], {}, ""),
        ("rev-parse --path-format=absolute --git-dir", ["git", "rev-parse", "--path-format=absolute", "--git-dir"], {}, ""),
        ("rev-parse --local-env-vars", ["git", "rev-parse", "--local-env-vars"], {}, ""),
        ("rev-parse --show-superproject-working-tree", ["git", "rev-parse", "--show-superproject-working-tree"], {}, ""),
        ("a clone between two other repositories", ["git", "clone", "-q", source, os.path.join(elsewhere, "dst")], {}, ""),
        ("--version", ["git", "--version"], {}, ""),
        ("-C <checkout> rev-parse --git-dir", ["git", "-C", repo, "rev-parse", "--git-dir"], {}, ""),
        ("status of a repository outside the checkout", ["git", "-C", source, "status", "--porcelain"], {}, None),
        ("config --global", ["git", "config", "--global", "--list"], {}, ""),
        ("a Python child with git config by count only",
         [sys.executable, "-c", "pass"], {"genv": {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fscache",
                                                     "GIT_CONFIG_VALUE_0": "true"}, "recorded": True}, None),
        ("merge-file on files under the temp root",
         ["git", "merge-file", "-p", os.path.join(elsewhere, "o"), os.path.join(elsewhere, "b"),
          os.path.join(elsewhere, "t")], {}, "config"),
        # the parts of the repository a git command can print
        ("log", ["git", "log", "-1"], {}, "head+config"),
        ("log with a config override", ["git", "-c", "core.quotepath=off", "log", "-1"], {}, "head+config"),
        ("rev-parse HEAD", ["git", "rev-parse", "HEAD"], {}, "head+config"),
        ("rev-parse of a named ref", ["git", "rev-parse", "sib"], {}, "head+refs+config"),
        ("branch --list", ["git", "branch", "--list"], {}, "head+refs+config"),
        ("config --list", ["git", "config", "--list"], {}, "config"),
        ("diff", ["git", "diff"], {}, "head+changes+config"),
        ("status", ["git", "status", "--porcelain"], {}, "head+changes+config+upstream+untracked"),
        ("status -uno", ["git", "status", "--porcelain", "--untracked-files=no"], {}, "head+changes+config+upstream"),
        ("ls-files --error-unmatch", ["git", "ls-files", "--error-unmatch", "--", ".claude/tools/alpha.py"], {}, "tracked"),
        ("ls-files -z", ["git", "ls-files", "-z"], {}, "tracked"),
        ("ls-files -zo --exclude-standard", ["git", "ls-files", "-zo", "--exclude-standard", "--", ".claude"], {},
         "tracked+untracked+config"),
        ("ls-files -o --exclude-standard", ["git", "ls-files", "-o", "--exclude-standard"], {},
         "tracked+untracked+config"),
        ("ls-files -s prints blob ids", ["git", "ls-files", "-s"], {}, _WHOLE),
        ("ls-files with a magic pathspec, which can read attributes", ["git", "ls-files", ":(attr:text)"], {}, _WHOLE),
        # a git the rule cannot place
        ("a command the rule does not list", ["git", "frobnicate"], {}, _WHOLE),
        ("a clone of the checkout started elsewhere", ["git", "clone", "-q", repo, os.path.join(elsewhere, "copy")],
         {"cwd": elsewhere}, _WHOLE),
        ("--git-dir pointed at the checkout's git dir", ["git", "--git-dir=" + git_dir, "log", "-1"],
         {"cwd": elsewhere}, _WHOLE),
        ("ls-files inside a repository nested in the checkout", ["git", "-C", nested, "ls-files"], {}, _WHOLE),
        ("a git named inside a shell line", ["cmd /c \"git ls-files & echo x\""], {}, _WHOLE),
        ("a git after an operator with no space",
         ["C:\\WINDOWS\\system32\\cmd.exe /c \"cd /d %s&&git log -1\"" % repo.replace("\\", "/")], {}, _WHOLE),
        ("a git started by its full path inside a shell line",
         ["cmd /c \"\\\"%s\\\" -C %s log -1\"" % (git_exe, repo.replace("\\", "/"))], {}, _WHOLE),
        ("GIT_DIR in the child's environment, started from another folder", ["git", "log", "-1"],
         {"cwd": elsewhere, "genv": {"GIT_DIR": git_dir}}, "repo"),
        # another worktree of the repository
        ("log in a sibling worktree", ["git", "-C", sib, "log", "-1"], {}, "sibling"),
        ("show of a ref in a sibling worktree", ["git", "-C", sib, "show", "HEAD:pinned.txt"], {}, "sibling"),
        ("show of an abbreviated id in a sibling worktree", ["git", "-C", sib, "show", sha[:12] + ":pinned.txt"], {}, "sibling"),
        ("show of a full id with a flag in a sibling worktree",
         ["git", "-C", sib, "show", "--textconv", sha + ":pinned.txt"], {}, "sibling"),
        ("a full-id show beside a log in a sibling worktree", None, {}, "sibling"),
    )
    wrong = []
    for label, argv, extra, expected in rows:
        if argv is None:  # two spawns: the pinned show is no read of a worktree, the log beside it is
            events = [spawn("git", "-C", sib, "show", sha + ":pinned.txt", cwd=repo),
                      spawn("git", "-C", sib, "log", "-1", cwd=repo)]
        else:
            events = [spawn(*argv, cwd=extra.get("cwd") or repo, genv=extra.get("genv"), recorded=extra.get("recorded", False))]
        found = cache.manifest(proof, {"events": events, "temp": _TEMP})["git"]
        want = [] if not expected else expected.split("+")
        if expected is None:
            want = []
        if sorted(found) != sorted(want):
            wrong.append("%s: %s, expected %s" % (label, found, sorted(want)))
    check("every form of git names the parts of the repository it can observe", not wrong, "; ".join(wrong))
    saved = tempfile.tempdir
    try:
        tempfile.tempdir = tempfile.mkdtemp(prefix="harnesspasstemp_")
        outside = harness_tests._PassCache(repo).manifest(
            proof, {"events": [spawn("git", "init", "-q", os.path.join(elsewhere, "fresh"), cwd=repo)], "temp": _TEMP})
    finally:
        tempfile.tempdir = saved
    check("git init at a path outside the temp root keeps the whole state", outside["git"] == ["repo"], str(outside))
    pinned = []
    for label, argv in (("a full-id show", ["show", sha + ":pinned.txt"]),
                        ("a full-id cat-file", ["cat-file", "-p", sha + ":pinned.txt"])):
        found = cache.manifest(proof, {"events": [spawn("git", "-C", sib, *argv, cwd=repo)], "temp": _TEMP})
        if found["git"] or not found["objects"]:
            pinned.append("%s: %s" % (label, found))
    check("a full-id show or cat-file keys the object it prints and no git state", not pinned, "; ".join(pinned))
    check("a git run in a worktree the key cannot name never reuses",
          cache.observed_parts(proof, cache.manifest(proof, {"events": [spawn("git", "-C", sib, "log", "-1", cwd=repo)],
                                                             "temp": _TEMP})) is None)


def _states(cache):
    """Each scope's digest now (`_GIT_SCOPES`, read fresh and side by side), except the `repo` union."""
    scopes = [(name, method) for name, method in harness_tests._GIT_SCOPES.items() if name != "repo"]
    with ThreadPoolExecutor(len(scopes)) as pool:
        return dict(zip((n for n, _ in scopes), pool.map(lambda s: getattr(cache, s[1])(fresh=True), scopes)))


def case_the_parts_of_the_repository_key_the_moves_that_can_change_them():
    """Each move of the repository, and the parts that move with it: a commit moves HEAD and the refs, a sibling
    worktree's commit only the refs and the worktree list, an amend that keeps the tree still moves the commit id, a
    second edit of an already modified file moves the changes, an untracked file only the untracked names, the
    upstream ref only the upstream. A proof's pass is reused unless a part its git can print moved
    (`case_a_spawned_git_names_the_state_it_can_observe`)."""
    repo, proof, _counter = make_repo()
    git(repo, "branch", "tracked_base")
    git(repo, "branch", "--set-upstream-to=tracked_base")
    sibling = os.path.join(tempfile.mkdtemp(prefix="harnesspasssibling_"), "sib")
    git(repo, "worktree", "add", "-q", "-b", "sib", sibling)
    git(repo, "config", "extensions.worktreeConfig", "true")

    def edit(text):
        return lambda: write(repo, ".claude/tools/gamma.py", text)

    def commit_gamma():
        write(repo, ".claude/tools/gamma.py", "G = 9\n")
        commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])

    def move_upstream():
        sha = subprocess.run(["git", "commit-tree", "HEAD^{tree}", "-m", "base moved"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
        git(repo, "branch", "-f", "tracked_base", sha)

    moves = (
        ("a commit", commit_gamma, {"head", "refs", "worktrees"}),
        ("an amend that keeps the tree", lambda: git(repo, "commit", "-q", "--amend", "-m", "reworded"),
         {"head", "refs", "worktrees"}),
        ("a new branch", lambda: git(repo, "branch", "side"), {"refs"}),
        ("a repository config edit", lambda: git(repo, "config", "demo.key", "v"), {"config"}),
        ("a worktree config edit", lambda: git(repo, "config", "--worktree", "demo.wt", "v"), {"config"}),
        ("an edit to a tracked file", edit("G = 2\n"), {"changes"}),
        ("a second edit of the already modified file", edit("G = 3\n"), {"changes"}),
        ("an untracked file", lambda: write(repo, "new_untracked.txt", "x\n"), {"untracked"}),
        ("a staged new file", lambda: (write(repo, ".claude/tools/staged.py", "S = 1\n"),
                                       git(repo, "add", ".claude/tools/staged.py")), {"tracked", "changes"}),
        ("a commit in a sibling worktree", lambda: git(sibling, "commit", "-q", "--allow-empty", "-m", "moved"),
         {"refs", "worktrees"}),
        ("the upstream ref moving", move_upstream, {"upstream", "refs"}),
    )
    wrong, moved_by = [], {}
    cache = harness_tests._PassCache(repo)
    before = _states(cache)
    for label, move, expected in moves:
        move()
        after = _states(cache)
        moved = moved_by[label] = {name for name in after if after[name] != before[name]}
        if moved != expected:
            wrong.append("%s moved %s, expected %s" % (label, sorted(moved), sorted(expected)))
        before = after
    check("each move of the repository moves exactly the parts it can change", not wrong, "; ".join(wrong))
    # A pass is reused unless a part its git can print moved: each headline, from the scope of the git a proof ran and
    # the parts the move changed.
    plain = tempfile.mkdtemp(prefix="harnesspassmerge_")
    consequences = (
        (["rev-parse", "HEAD"], "an amend that keeps the tree", True),
        (["branch", "--list"], "a new branch", True),
        (["config", "--list"], "a repository config edit", True),
        (["config", "--list"], "a worktree config edit", True),
        (["config", "--global", "--list"], "a repository config edit", False),
        (["merge-file", "-p"] + [os.path.join(plain, n) for n in "obt"], "a commit", False),
        (["diff"], "a second edit of the already modified file", True),
        (["status", "--porcelain"], "a commit in a sibling worktree", False),
        (["rev-parse", "HEAD"], "a commit in a sibling worktree", False),
        (["ls-files", "-zo", "--exclude-standard"], "a commit in a sibling worktree", False),
        (["status", "--porcelain"], "the upstream ref moving", True),
        (["ls-files", "-z"], "an edit to a tracked file", False),
        (["ls-files", "-z"], "a commit", False),
        (["ls-files", "-z"], "a staged new file", True),
        (["status", "--porcelain"], "an untracked file", True),
        (["ls-files", "-o", "--exclude-standard"], "an untracked file", True),
        (["status", "--porcelain", "--untracked-files=no"], "an untracked file", False),
        (["log", "-1"], "an untracked file", False),
    )
    wrong = []
    for argv, label, reruns in consequences:
        found = cache.manifest(proof, {"events": [spawn("git", *argv, cwd=repo)], "temp": _TEMP})["git"]
        parts = {part for scope in found for part in scope.split("+")}
        if (bool(parts & moved_by[label]) != reruns):
            wrong.append("git %s after %s: %s moved %s, reruns=%s" % (" ".join(argv), label, sorted(parts),
                                                                    sorted(moved_by[label]), reruns))
    check("a proof's pass is reused unless a part its git can print moved", not wrong, "; ".join(wrong))
    # The key's tracked names and every other part are read as the proof reads them: a caller's private index
    # (`GIT_INDEX_FILE`, set while a commit runs) is dropped, so the names come from the index the proof read.
    private = os.path.join(tempfile.mkdtemp(prefix="harnesspassindex_"), "index")
    shutil.copyfile(os.path.join(repo, ".git", "index"), private)
    write(repo, "staged_elsewhere.txt", "x\n")
    subprocess.run(["git", "add", "staged_elsewhere.txt"], cwd=repo, check=True, capture_output=True,
                   env=dict(os.environ, GIT_INDEX_FILE=private))
    names = _states(cache)["tracked"]
    saved = os.environ.get("GIT_INDEX_FILE")
    os.environ["GIT_INDEX_FILE"] = private
    try:
        with_private = _states(cache)["tracked"]
    finally:
        os.environ.pop("GIT_INDEX_FILE", None) if saved is None else os.environ.update(GIT_INDEX_FILE=saved)
    git(repo, "add", "staged_elsewhere.txt")
    check("a name staged only in a caller's private index is not in the key; staging it for real moves it",
          with_private == names and _states(cache)["tracked"] != names)


def case_a_full_object_id_is_keyed_by_the_object_not_the_worktree():
    """`git show <full id>:<path>` prints bytes the id fixes, so a pass whose only git is that read in a sibling worktree
    keeps across commits while the object exists, and reruns once the object is gone."""
    repo, proof, counter = make_repo()
    world = _git_world(repo)
    sib, sha = world["sibling"], world["sha"]
    plant(repo, proof, counter, spawns=[spawn("git", "-C", sib, "show", sha + ":pinned.txt", cwd=repo)])
    manifest = record_pass(repo, proof)
    check("the pass keys the object, not a git state", manifest["objects"] and not manifest["git"], str(manifest))
    git(sib, "commit", "-q", "--allow-empty", "-m", "moved")
    write(repo, ".claude/tools/gamma.py", "G = 9\n")
    commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])
    check("commits in the sibling and in the checkout keep the pass while the object exists", holds(repo, proof))
    git(sib, "prune", "--expire=now")
    check("the object gone reruns it", not holds(repo, proof))


def case_git_state_that_moved_during_the_run_records_no_pass():
    """The key's git state is read once, before the proofs start. A proof whose run ends on another state
    observed one the row cannot name, so no row is stored for it."""
    repo, proof, counter = make_repo()
    added = os.path.join(repo, ".claude", "scratch", "late.txt")

    def stage():
        write(repo, ".claude/scratch/late.txt", "x\n")
        git(repo, "add", "-f", added)
    plant(repo, proof, counter, spawns=[spawn("git", "ls-files", cwd=repo)], effect=stage)
    run(repo, [proof])
    git(repo, "rm", "-q", "--cached", ".claude/scratch/late.txt")
    write(repo, ".claude/tools/table.json", '{"a": 1}\n')
    run(repo, [proof])
    git(repo, "rm", "-q", "--cached", ".claude/scratch/late.txt")
    run(repo, [proof])
    check("a pass that listed an index its own run had changed is not reused on the index the run started from",
          runs(counter) == 3, "runs=%d" % runs(counter))
    run(repo, [proof])
    run(repo, [proof])
    check("a run that leaves the index as it found it records a pass the next run reuses", runs(counter) == 4,
          "runs=%d" % runs(counter))


def case_a_first_run_that_moves_the_git_it_read_records_no_pass():
    """A proof with no row yet names no scope a lookup reads, so the run's first launch reads every scope before
    the proof starts. A proof that listed the refs and then moved one ended on a state it did not observe: a scope
    read only at record time would agree with itself and key the pass to the state the proof left."""
    repo, proof, counter = make_repo()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    plant(repo, proof, counter, spawns=[spawn("git", "for-each-ref", cwd=repo)],
          effect=lambda: write(repo, ".git/refs/heads/late", head))
    run(repo, [proof])
    run(repo, [proof])
    check("a first pass that listed refs its own run then moved is not reused on the moved refs",
          runs(counter) == 2, "runs=%d" % runs(counter))
    run(repo, [proof])
    check("a run that leaves the refs as it found them records a pass the next run reuses", runs(counter) == 2,
          "runs=%d" % runs(counter))


def case_a_commit_in_another_worktree_during_the_run_keeps_only_a_key_that_could_not_print_it():
    """Proof reuse is the main lever on land time. A proof whose git prints only this checkout's HEAD cannot observe
    another worktree's commit, so a commit there during its run still records its key; one that printed the ref that
    commit moved observed a state its row cannot name, so it records none and its row names `refs`."""
    for target, kept in (("HEAD", True), ("sib", False)):
        repo, proof, counter = make_repo()
        sibling = os.path.join(tempfile.mkdtemp(prefix="harnesspasssibling_"), "sib")
        git(repo, "worktree", "add", "-q", "-b", "sib", sibling)
        plant(repo, proof, counter, spawns=[spawn("git", "rev-parse", target, cwd=repo)],
              effect=lambda sibling=sibling: git(sibling, "commit", "-q", "--allow-empty", "-m", "moved"))
        run(repo, [proof])
        first = _recorded_row(repo, proof)
        run(repo, [proof])
        detail = "runs=%d okey=%s why=%r" % (runs(counter), bool(first.get("okey")), (first.get("why") or "")[:120])
        if kept:
            check("a sibling worktree's commit during the run keeps the key of a proof that printed only HEAD",
                  runs(counter) == 1 and bool(first.get("okey")), detail)
        else:
            check("a sibling worktree's commit during the run drops the key of a proof that printed the moved ref",
                  runs(counter) == 2 and not first.get("okey") and "refs" in (first.get("why") or ""), detail)


def case_git_state_that_cannot_be_read_leaves_no_reusable_pass():
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, spawns=[spawn("git", "ls-files", cwd=repo)])
    saved = harness_tests._PassCache._tracked_names
    try:
        harness_tests._PassCache._tracked_names = lambda self, fresh=False: None
        run(repo, [proof])
    finally:
        harness_tests._PassCache._tracked_names = saved
    run(repo, [proof])
    check("a pass recorded while the tracked names could not be read is never reused", runs(counter) == 2,
          "runs=%d" % runs(counter))


def case_the_runners_own_state_under_the_git_dir_keys_its_bytes():
    """The runner keeps its pass store and durations under the repository's git dir. That is the runner's state,
    not git state: a commit elsewhere moves no byte of it, and an edit to it reruns the proof that read it. A linked
    worktree's git dir lies outside the checkout, where every touch used to be repository state; the runner's own
    folder there is its state too."""
    for where in ("checkout", "linked worktree"):
        repo, proof, counter = make_repo()
        shared = write(repo, ".git/harness/fixture_state.json", '{"v": 1}\n')
        root = repo
        if where == "linked worktree":
            root = os.path.join(tempfile.mkdtemp(prefix="harnesspasstree_"), "tree")
            git(repo, "worktree", "add", "-q", "-b", "twin", root)
            proof = os.path.join(root, ".claude", "tests", "test_alpha.py")
        plant(root, proof, counter, reads=[shared])
        record_pass(root, proof)
        check("%s: a proof that reads the runner's state under the git dir is reused while the bytes hold" % where,
              holds(root, proof))
        git(repo, "branch", "side")
        check("%s: a new ref does not move the key of a proof that read only the runner's state" % where,
              holds(root, proof))
        write(repo, ".git/harness/fixture_state.json", '{"v": 2}\n')
        check("%s: an edit to the runner's state the proof read reruns it" % where, not holds(root, proof))


def case_a_linked_worktree_git_dir_touch_keys_the_whole_state():
    """In a linked worktree the git dir lies outside the checkout. A location `rev-parse` keys nothing, so a
    read or a probe of the git dir it printed must key the repository's state itself."""
    repo, _, counter = make_repo()
    tree = os.path.join(tempfile.mkdtemp(prefix="harnesspasstree_"), "tree")
    git(repo, "worktree", "add", "-q", "-b", "linked", tree)
    proof = os.path.join(tree, ".claude", "tests", "test_alpha.py")
    outer = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=tree, capture_output=True,
                           text=True).stdout.strip()
    cache = harness_tests._PassCache(tree)
    for label, event in (("a read", ["r", os.path.join(outer, "HEAD")]), ("a probe", ["p", os.path.join(outer, "MERGE_HEAD")])):
        manifest = cache.manifest(proof, {"events": [["r", proof], event], "temp": _TEMP})
        check("%s of a linked worktree's git dir keys the repository state" % label, manifest["git"] == ["repo"],
              str(manifest))


def case_a_manifest_names_the_first_event_behind_each_scope():
    """`why` holds, per git scope and for `opaque`, the first event that set it; the key never reads it."""
    repo, _, _ = make_repo()
    tree = os.path.join(tempfile.mkdtemp(prefix="harnesspasstree_"), "tree")
    git(repo, "worktree", "add", "-q", "-b", "linked", tree)
    proof = os.path.join(tree, ".claude", "tests", "test_alpha.py")
    outer = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=tree, capture_output=True,
                           text=True).stdout.strip()
    cache = harness_tests._PassCache(tree)
    probe, head = ["p", os.path.join(outer, "MERGE_HEAD")], ["r", os.path.join(outer, "HEAD")]
    listing = ["s", {"argv": ["git", "ls-files"], "cwd": tree}]
    log = ["s", {"argv": ["git", "log", "-1"], "cwd": tree}]
    written = ["w", os.path.join(tree, ".claude", "tools", "out.txt")]
    events = [["r", proof], probe, head, listing, log, written]
    manifest = cache.manifest(proof, {"events": events, "temp": None})
    why = manifest.get("why") or {}
    check("a probe under a linked worktree's outer git dir is named as what set repo",
          why.get("repo") == probe, str(why))
    check("the git command that set tracked is named by its argv",
          why.get("tracked") == ["s", ["git", "ls-files"]], str(why))
    check("the write that made the manifest opaque is named", why.get("opaque") == written, str(why))
    plain = cache.manifest(proof, {"events": [["r", proof], log], "temp": None})
    bare = {k: v for k, v in plain.items() if k != "why"}
    with_why, without = cache.observed_parts(proof, plain), cache.observed_parts(proof, bare)
    check("why leaves the observed key unchanged", with_why is not None and with_why == without
          and cache._combine(with_why) == cache._combine(without), "%s vs %s" % (with_why, without))
    merged = harness_tests._merge_manifests([plain, manifest])
    check("a merged manifest keeps the first manifest's cause per scope",
          merged.get("why", {}).get("head") == ["s", ["git", "log", "-1"]]
          and merged.get("why", {}).get("repo") == probe
          and merged["why"].get("opaque") == written, str(merged.get("why")))
    clean = cache.manifest(proof, {"events": [["r", proof]], "temp": None})
    check("a manifest with no scope and no opaque read names no cause", clean.get("why") == {}, str(clean))


def case_a_git_directory_existence_probe_is_no_git_read():
    repo, proof, counter = make_repo()
    git_dir = os.path.join(repo, ".git")
    probes = [git_dir, os.path.join(git_dir, "pyvenv.cfg")]
    plant(repo, proof, counter, probes=probes)
    manifest = record_pass(repo, proof)
    write(repo, "notes.txt", "x\n")
    commit_all(repo, "unrelated")
    check("an existence probe of .git keeps the pass across a commit", holds(repo, proof) and not manifest["git"],
          str(manifest))
    plant(repo, proof, counter, reads=[os.path.join(git_dir, "HEAD")])
    manifest = record_pass(repo, proof)
    write(repo, "notes2.txt", "x\n")
    commit_all(repo, "unrelated again")
    check("a read of a file under .git reruns on a commit", manifest["git"] == ["repo"] and not holds(repo, proof),
          str(manifest))
    marker = os.path.join(git_dir, "MERGE_HEAD")
    plant(repo, proof, counter, probes=[marker])
    record_pass(repo, proof)
    check("a probe of an absent path under .git is reused while it stays absent", holds(repo, proof))
    write(repo, ".git/MERGE_HEAD", "0" * 40 + "\n")
    check("a probed path under .git appearing reruns the proof", not holds(repo, proof))


def case_a_shell_script_child_that_runs_git_keys_the_repository_state():
    """A child that is not git and not Python (a `.sh` script) runs git itself: no spawn event names git, so the
    script's own text decides, with the scripts it loads."""
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/probe.sh", "#!/bin/sh\ngit log -1 > /dev/null\n")
    write(repo, ".claude/tools/plain.sh", "#!/bin/sh\necho hello\n")
    git(repo, "add", "-A")
    for script, whole in (("probe.sh", True), ("plain.sh", False)):
        plant(repo, proof, counter, spawns=[spawn("bash", tool(repo, script), cwd=repo)])
        record_pass(repo, proof)
        write(repo, ".claude/tools/gamma.py", "G = %r\n" % script)
        commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])
        check("a shell script that %s git %s across a commit" % ("runs" if whole else "runs no",
                                                                    "reruns" if whole else "keeps the pass"),
              holds(repo, proof) != whole)


def case_the_recorder_logs_each_form_of_git_spawn_the_rule_reads():
    """One real child starts git in the forms whose events the rule reads: a direct argv, a shell line past an operator,
    a git steered by `GIT_DIR` from another folder, through its own environment or one it inherits. Each recorded spawn is classified alone (`manifest`), so the live
    event shapes agree with the planted ones of `case_a_spawned_git_names_the_state_it_can_observe`. The recorder logs an
    identical spawn once, so each form runs a distinct command."""
    repo, proof, _counter = make_repo()
    elsewhere = tempfile.mkdtemp(prefix="harnesspassgit_")
    marks = tempfile.mkdtemp(prefix="harnesspassmarks_")
    log = os.path.join(marks, "reads")
    cd = "cd /d " if os.name == "nt" else "cd "
    code = ("import os, subprocess\n"
            "def mark(i):\n    os.path.exists(os.path.join(%r, 'm%%d' %% i))\n"
            "mark(0)\nsubprocess.run(['git', 'init', '-q', %r], cwd=%r, capture_output=True)\n"
            "mark(1)\nsubprocess.run(%r, shell=True, capture_output=True)\n"
            "mark(2)\nsubprocess.run(['git', 'log', '-1'], cwd=%r, env=dict(os.environ, GIT_DIR=%r), capture_output=True)\n"
            "mark(3)\nsubprocess.run(['git', 'rev-parse', 'HEAD'], cwd=%r, capture_output=True)\n"
            "mark(4)\nos.environ['GIT_DIR'] = %r\nsubprocess.run(['git', 'log', '-2'], cwd=%r, capture_output=True)\n"
            "mark(5)\n" % (marks, os.path.join(elsewhere, "fresh"), repo, cd + repo.replace("\\", "/") + "&&git log -1",
                           elsewhere, os.path.join(repo, ".git"), repo, os.path.join(repo, ".git"), elsewhere))
    result = _recorded_python(code, [harness_tests._RECORDER_DIR], log, repo)
    events, segments, current = harness_tests._attempt._read_events(log), {}, None
    for event in events:
        name = os.path.basename(event[1]) if event[0] == "p" else ""
        if name in ("m0", "m1", "m2", "m3", "m4", "m5"):
            current = name
        elif current:
            segments.setdefault(current, []).append(event)
    cache = harness_tests._PassCache(repo)
    found = {mark: sorted(cache.manifest(proof, {"events": segments.get(mark, []), "temp": log})["git"])
             for mark in ("m0", "m1", "m2", "m3", "m4")}
    check("a git init under the temp root reads none of the checkout's state", found["m0"] == [], "%s %s" % (found, result.stderr))
    check("a git after an operator in a shell line keys the whole state", found["m1"] == sorted(_WHOLE.split("+")), str(found))
    check("a git whose environment names the repository, started from another folder, keys the repository",
          "repo" in found["m2"], str(found))
    check("a git that resolves HEAD keys HEAD and the config", found["m3"] == ["config", "head"], str(found))
    check("a git that inherits a `GIT_DIR` naming the repository, started from another folder, keys the repository",
          "repo" in found["m4"], str(found))


def case_a_miss_names_the_part_that_moved():
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, lists=[".claude/tools"])
    record_pass(repo, proof)
    rel = harness_tests._norm(os.path.relpath(proof, repo))
    write(repo, ".claude/tools/table.json", '{"a": 1}\n')
    cache = harness_tests._PassCache(repo)
    check("an edit to a file the proof read misses", not cache.hit(proof, cache.key(proof)))
    check("and the miss names the files part alone", cache.misses.get(rel) == ["files"], str(cache.misses))
    write(repo, ".claude/tools/new_entry.txt", "x\n")
    cache = harness_tests._PassCache(repo)
    cache.hit(proof, cache.key(proof))
    check("a new entry in a listed directory names the listing too", "dirs" in cache.misses.get(rel, []),
          str(cache.misses))


def case_a_miss_names_the_input_files_that_moved():
    """A row packs a short digest per file it keyed; a miss then names the files that moved, and each close
    counts, per named file, the selected proofs that read it. The digests never join the key."""
    repo, proof, counter = make_repo()
    other = add_proof(repo, "test_other.py", counter, base=False, own=True, reads=[".claude/tools/beta.py"])
    record_pass(repo, proof)
    record_pass(repo, other)
    rel = harness_tests._norm(os.path.relpath(proof, repo))
    cache = harness_tests._PassCache(repo)
    row = cache.variants(proof)[0]
    manifest = harness_tests._unpack_manifest(row)
    digests = manifest.get("digests") or {}
    check("a row packs a digest per keyed file", ".claude/tools/table.json" in digests and rel in digests,
          sorted(digests))
    bare = {k: v for k, v in manifest.items() if k != "digests"}
    check("the digests leave the key unchanged",
          cache._combine(cache.observed_parts(proof, manifest)) == cache._combine(cache.observed_parts(proof, bare))
          == row["okey"], row.get("okey"))
    write(repo, ".claude/tools/table.json", '{"moved": 1}\n')
    cache = harness_tests._PassCache(repo)
    check("an edit to one read file misses", not cache.hit(proof, cache.key(proof)))
    cache.hit(other, cache.key(other))
    check("the miss names that file alone", cache.miss_files.get(rel) == [".claude/tools/table.json"],
          str(cache.miss_files))
    check("readers count the selected proofs that read a file",
          cache.readers([".claude/tools/beta.py", ".claude/tools/table.json"])
          == {".claude/tools/beta.py": 2, ".claude/tools/table.json": 1}, str(cache.readers([".claude/tools/beta.py"])))
    inputs = harness_tests._input_totals(cache, [proof, other], {other},
                                         {proof: ("pass", 5.0, ""), other: ("pass", 0.0, "")})
    check("the close row totals each moved input's rerun seconds, reruns and readers",
          inputs == {".claude/tools/table.json": [5.0, 1, 1]}, str(inputs))
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    import gate_growth
    line = gate_growth.reuse_line({"selected": 2, "reused": 1, "proofs": [], "inputs": inputs})
    check("the close reuse line names that input", "inputs: .claude/tools/table.json 5s (1 reran, read by 1)" in line,
          line)


def case_a_miss_is_typed_with_its_reason():
    """Every way a recorded pass stops holding is a typed Miss naming why: a read file moves, a listed
    directory gains an entry, and a run whose recorder never loaded leaves no reusable pass. None is a stale
    hit."""
    repo, proof, counter = make_repo()
    parent, saved = with_private_store()
    try:
        from _proof_runner import evidence
        plant(repo, proof, counter, lists=[".claude/tools"])
        record_pass(repo, proof)
        cache = harness_tests._PassCache(repo)
        key = cache.key(proof)
        held = evidence.lookup(cache, proof, key)
        write(repo, ".claude/tools/table.json", '{"moved": 1}\n')
        moved = evidence.lookup(harness_tests._PassCache(repo), proof, key)
        check("a moved read is a typed Miss naming the files part",
              isinstance(held, evidence.Hit) and isinstance(moved, evidence.Miss) and "files" in moved.reasons,
              "%r -> %r" % (held, moved))
        write(repo, ".claude/tools/new_entry.txt", "x\n")
        listed = evidence.lookup(harness_tests._PassCache(repo), proof, key)
        check("a listed directory that gained an entry names the dirs part",
              isinstance(listed, evidence.Miss) and "dirs" in listed.reasons, repr(listed))
        cache = harness_tests._PassCache(repo)
        cache.record(proof, key="unobserved", observable=True)
        blind = evidence.lookup(cache, proof, "unobserved")
        check("a run whose recorder never loaded is a typed Miss, never a stale hit",
              isinstance(blind, evidence.Miss) and blind.reasons == ("unobserved",), repr(blind))
    finally:
        harness_tests.PASSES_PATH = saved


def case_a_tracked_file_an_ignore_pattern_names_is_still_an_input():
    """A tracked file can match an ignore pattern. In a close's checkout it is an input, not exhaust, in
    whatever letter case the platform reports its path."""
    repo, proof, counter = make_repo(close=True)
    write(repo, ".gitignore", "*.ini\n")
    data = write(repo, "Data/Mixed.ini", "1\n")
    git(repo, "add", "-f", "Data/Mixed.ini")
    commit_all(repo, "a tracked file an ignore pattern names")
    plant(repo, proof, counter, reads=[data])
    record_pass(repo, proof)
    check("a tracked file an ignore pattern names keeps the reuse while it holds", holds(repo, proof))
    write(repo, "Data/Mixed.ini", "2\n")
    check("an edit to a tracked file an ignore pattern names reruns the proof in a close checkout",
          not holds(repo, proof))


def case_qualified_and_relative_imports_join_the_reach():
    """The static key follows dotted and relative imports through the packages they name, so an edit to any module
    reached that way moves the key (the observed key of a real import is `case_a_recorded_python_run_keys_what_it_read`)."""
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/alpha.py", "import beta\nfrom pkg.sub import X\nfrom zeta.zorn import Z\nTABLE = 'table.json'\n")
    write(repo, ".claude/tools/pkg/__init__.py", "INIT = 1\n")
    write(repo, ".claude/tools/pkg/sub.py", "from . import leaf\nfrom .twig import T\nX = 1\n")
    write(repo, ".claude/tools/pkg/leaf.py", "L = 1\n")
    write(repo, ".claude/tools/pkg/twig.py", "T = 1\n")
    write(repo, ".claude/tools/zeta/zorn.py", "Z = 1\n")
    git(repo, "add", "-A")
    for rel in ("pkg/__init__.py", "pkg/leaf.py", "pkg/twig.py", "zeta/zorn.py", "pkg/sub.py"):
        before = harness_tests._PassCache(repo).key(proof)
        with open(os.path.join(repo, ".claude", "tools", rel), encoding="utf-8") as fh:
            kept = fh.read()
        write(repo, ".claude/tools/" + rel, kept + "EDITED = 1\n")
        check("an edit to %s, reached by a dotted or relative import, moves the proof's key" % rel,
              harness_tests._PassCache(repo).key(proof) != before)


def case_an_unrecorded_childs_data_argument_is_keyed_by_its_bytes():
    """A repo file handed to an unrecorded child is an input by its bytes, whatever runs it. A data file
    runs nothing, so the code its prose names by stem is not the proof's input; the child's script stays
    expanded as code."""
    children = (("child.js", "require('fs').readFileSync(process.argv[2]);\n", ["node"]),
                ("child.py", "import sys\nopen(sys.argv[1]).read()\n", [sys.executable, "-I"]))
    for script, body, launcher in children:
        repo, proof, counter = make_repo()
        write(repo, ".claude/tools/" + script, body)
        write(repo, ".claude/tools/notes.md", "The gamma table is unrelated.\n")
        git(repo, "add", "-A")
        plant(repo, proof, counter, spawns=[spawn(*(launcher + [tool(repo, script), tool(repo, "notes.md")]), cwd=repo)])
        record_pass(repo, proof)
        write(repo, ".claude/tools/gamma.py", "G = 2\n")
        check("%s: an edit to code its data argument only names in prose keeps the reuse" % script, holds(repo, proof))
        write(repo, ".claude/tools/notes.md", "The gamma table changed.\n")
        check("%s: an edit to its data argument reruns the proof" % script, not holds(repo, proof))
        record_pass(repo, proof)
        write(repo, ".claude/tools/" + script, body + "\n")
        check("%s: an edit to the unrecorded child's script reruns the proof" % script, not holds(repo, proof))


def _scanning_plan(repo, proof, counter, folders):
    """A proof that reads every file under `folders`, as a real corpus-scanning proof does: it lists each folder and
    reads each file at the run."""
    def members():
        return [os.path.join(d, f) for folder in folders for d, _s, names in os.walk(os.path.join(repo, folder))
                for f in names]
    plant(repo, proof, counter, reads=members, lists=[os.path.join(repo, f) for f in folders])


def case_a_corpus_scanning_proof_is_keyed_by_the_corpus_it_scans():
    """A proof that lists a tracked directory reads files its text never names; the key covers them."""
    table = harness_tests._catalog.CORPUS
    check("the corpus-scanning table names the proofs that read a whole directory",
          {"test_claudemd_citations.py", "test_guard_text.py"} <= set(table),
          "table=%s" % sorted(table))
    repo, _proof, counter = make_repo()
    scanners = {name: write(repo, ".claude/tests/" + name, "# scanner\n") for name in table}
    commit_all(repo, "the scanners")
    wrong = []
    for name, folders in sorted(table.items()):
        _scanning_plan(repo, scanners[name], counter, folders)
        record_pass(repo, scanners[name])
        corpus_file = folders[0] + "/unnamed_corpus_member.md"
        write(repo, corpus_file, "first\n")
        if holds(repo, scanners[name]):
            wrong.append("%s kept its pass when a file it scans but never names was added" % name)
        record_pass(repo, scanners[name])
        write(repo, corpus_file, "second\n")
        if holds(repo, scanners[name]):
            wrong.append("%s kept its pass when that file was edited" % name)
    check("each corpus-scanning proof reruns when a file it scans but never names is added or edited", not wrong,
          "; ".join(wrong))


def case_a_dir_scanning_proof_is_reused_only_with_a_corpus_entry():
    """test_hook_state.py is selected by every scoped run and lists .claude/hooks; keyed over that corpus it
    is reused while the hooks are unchanged, and a dir-scanning proof with no corpus entry is never reused."""
    check("test_hook_state.py is keyed over the hooks directory it lists",
          harness_tests._catalog.CORPUS.get("test_hook_state.py") == (".claude/hooks",),
          "table=%s" % sorted(harness_tests._catalog.CORPUS))
    repo, keyed, counter = make_repo()
    bare = add_proof(repo, "test_bare.py", counter)
    scanner = add_proof(repo, "test_scanner.py", counter)
    _scanning_plan(repo, scanner, counter, [".claude/hooks"])
    saved = (harness_tests._catalog.ALWAYS, dict(harness_tests._catalog.CORPUS))
    harness_tests._catalog.ALWAYS = ("test_bare.py", "test_scanner.py")
    harness_tests._catalog.CORPUS["test_scanner.py"] = (".claude/hooks",)
    try:
        write(repo, ".claude/hooks/member.py", "M = 1\n")
        run(repo, [scanner])
        run(repo, [scanner])
        check("a dir-scanning proof with a corpus entry is reused while its corpus is unchanged",
              runs(counter) == 1, "runs=%d" % runs(counter))
        write(repo, ".claude/hooks/member.py", "M = 2\n")
        run(repo, [scanner])
        check("a dir-scanning proof with a corpus entry reruns after a corpus file is edited",
              runs(counter) == 2, "runs=%d" % runs(counter))
        before = runs(counter)
        run(repo, [bare])
        run(repo, [bare])
        check("a dir-scanning proof with no corpus entry is never reused",
              runs(counter) == before + 2, "runs %d -> %d" % (before, runs(counter)))
    finally:
        harness_tests._catalog.ALWAYS, harness_tests._catalog.CORPUS = saved[0], saved[1]


def case_a_long_run_prints_its_progress():
    """A close ran 22 minutes printing nothing; a run of many proofs prints N of M as it goes."""
    repo, proof, counter = make_repo()
    second = add_proof(repo, "test_beta_two.py", counter + ".2")
    saved = (harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY)
    harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY = 2, 1
    out = io.StringIO()
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    harness_tests.run_proof = _planted_run_proof
    try:
        with redirect_stdout(out):
            harness_tests._run_selected([proof, second], False, {}, None,
                                        harness_tests.RunOptions(harness_tests._pass_cache(repo, [proof, second])))
    finally:
        harness_tests.run_proof = _REAL_RUN_PROOF
        harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY = saved
        del os.environ["HARNESS_SLOT_DIR"]
    text = out.getvalue()
    check("a run of many proofs prints how many of them are done", "2 of 2 proofs done" in text, text[-300:])


def case_progress_states_the_time_left_at_recorded_durations():
    """A close's progress line gave only elapsed seconds, so its reader guessed the end from the rate while
    the durations that ordered the run sat unread: each line states the recorded time left and the pace."""
    repo, proof, counter = make_repo()
    second = add_proof(repo, "test_beta_two.py", counter + ".2")
    durations = os.path.join(repo, "durations.json")
    with open(durations, "w", newline="\n") as f:
        json.dump({os.path.basename(proof): 100.0, os.path.basename(second): 50.0}, f)
    saved = (harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY, harness_tests.DURATIONS_PATH)
    harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY, harness_tests.DURATIONS_PATH = 2, 1, durations
    out = io.StringIO()
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    os.environ["HARNESS_TEST_JOBS"] = "1"
    harness_tests.run_proof = _planted_run_proof
    try:
        with redirect_stdout(out):
            harness_tests._run_selected([proof, second], False, {}, None, harness_tests.RunOptions())
    finally:
        harness_tests.run_proof = _REAL_RUN_PROOF
        harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY, harness_tests.DURATIONS_PATH = saved
        del os.environ["HARNESS_SLOT_DIR"]
        del os.environ["HARNESS_TEST_JOBS"]
    text = out.getvalue()
    check("the first progress line states the recorded time left for the proofs not yet done",
          re.search(r"1 of 2 proofs done \(0 reused\), \d+s; ~50s left at recorded times, pace x[\d.]+", text), text[-400:])
    check("the last progress line states no time left", re.search(r"2 of 2 proofs done \(0 reused\), \d+s; ~0s left", text),
          text[-400:])


def case_a_run_that_launches_nothing_reads_only_the_git_its_reused_rows_name():
    """A run whose proofs are all reused starts no proof, so no read of the snapshot can stop a record: a lookup
    reads the scopes its row's key holds and nothing else. A proof that reads no git costs the run no git state;
    one that lists the index costs it the index's names. `status` and `diff HEAD` cost seconds on a large
    repository, and every scoped rerun and gate pays them when it reuses every pass."""
    real = harness_tests._git_run.run
    here = os.path.normcase(os.path.abspath(harness_tests.__file__))

    def asked_by_snapshot(action):
        asked = []

        def spy(args, **kwargs):
            if os.path.normcase(os.path.abspath(sys._getframe(1).f_code.co_filename)) == here:
                asked.append((tuple(args), (kwargs.get("env") or {}).get("GIT_CONFIG_GLOBAL")))
            return real(args, **kwargs)
        harness_tests._git_run.run = spy
        try:
            action()
        finally:
            harness_tests._git_run.run = real
        return asked

    repo, proof, counter = make_repo()
    every = set(asked_by_snapshot(harness_tests._PassCache(repo).snapshot_git))
    run(repo, [proof])
    plain = [a for a in asked_by_snapshot(lambda: run(repo, [proof])) if a in every]
    check("a run reusing a proof that reads no git asks git for none of the snapshot",
          runs(counter) == 1 and not plain, "runs=%d asked=%s" % (runs(counter), plain))
    repo, proof, counter = make_repo()
    plant(repo, proof, counter, spawns=[spawn("git", "ls-files", cwd=repo)])
    run(repo, [proof])
    listed = [a for a in asked_by_snapshot(lambda: run(repo, [proof])) if a in every]
    check("a run reusing a proof that lists the index asks git for the index's names alone",
          runs(counter) == 1 and [" ".join(a) for a, _ in listed] == ["ls-files -z"],
          "runs=%d asked=%s" % (runs(counter), listed))



def case_a_comment_or_docstring_mention_is_not_a_load():
    """The static reach follows what reached code may run or open, not what its comments and docstrings
    name: following those joined almost every proof to the whole tree, so any commit reran ~97% of a close.
    Code that names a file in a string, runs or imports a module by name, requires a relative JS module or
    dot-sources a script stays reached."""
    repo, proof, _counter = make_repo()
    write(repo, ".claude/tools/alpha.py", MENTIONS)
    for name, text in HELPERS.items():
        write(repo, ".claude/tools/" + name, text)
    false_edges = ("noted_doc.py", "noted_doc.json", "noted_comment.py", "noted_func.py", "noted_block.py",
                   "noted_line.py", "noted_jsblock.py", "noted_jsline.py")
    true_edges = ("beta.py", "table.json", "helper.ps1", "helper.js", "ran.py", "run_mod.py", "by_name.py",
                  "sourced.ps1", "inline.ps1", "required.js")
    for name in false_edges + true_edges[4:]:
        write(repo, ".claude/tools/" + name, "N = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "mentions")
    key = harness_tests._PassCache(repo).key(proof)  # each edit is judged against the key the one before left
    for name, want in [(n, False) for n in false_edges] + [(n, True) for n in true_edges]:
        path = os.path.join(repo, ".claude", "tools", name)
        with open(path, encoding="utf-8") as fh:
            kept = fh.read()
        write(repo, ".claude/tools/" + name, kept + "\n# edited\n")
        cache = harness_tests._PassCache(repo)
        moved, key = cache.key(proof) != key, cache.key(proof)
        check("an edit to %s, which reached code %s, %s the static key" % (
            name, "may load" if want else "only mentions in a comment or docstring", "changes" if want else "keeps"),
            moved == want, "reach=%s" % sorted(cache.reach(proof)))


def store_file(rel, cases=False):
    """Where the store keeps `rel`'s rows: one file per proof, under the pass store (or the case store)."""
    return harness_tests._store_file(harness_tests.case_store_dir() if cases else harness_tests.pass_store_dir(), rel)


def with_private_store():
    parent = tempfile.mkdtemp(prefix="harnesspassstore_")
    saved = harness_tests.PASSES_PATH
    harness_tests.PASSES_PATH = os.path.join(parent, "passes.json")
    return parent, saved


def _store_rows(store, limit=40):
    """The rows `store` already held, read as text: a row written by this checkout's earlier codec."""
    rows = []
    try:
        names = sorted(os.listdir(store))[:limit]
    except OSError:
        return rows
    for name in names:
        try:
            with open(os.path.join(store, name), encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        for entry in (doc.values() if isinstance(doc, dict) else []):
            for row in (entry if isinstance(entry, list) else [entry]):
                if isinstance(row, dict) and isinstance(row.get("zm"), str):
                    rows.append(row)
    return rows


MENTIONS = (
    '"""Alpha. See noted_doc.py and noted_doc.json."""\n'
    "import importlib, os, subprocess, sys\n"
    "import beta  # beside noted_comment.py\n"
    "HERE = os.path.dirname(os.path.abspath(__file__))\n"
    "TABLE = 'table.json'\n"
    "HELPERS = ('helper.ps1', 'helper.js')\n\n\n"
    "def run():\n"
    '    """Then noted_func.py."""\n'
    "    subprocess.run([sys.executable, os.path.join(HERE, 'ran.py')])\n"
    "    subprocess.run('python -m run_mod', shell=True)\n"
    "    return importlib.import_module('by_name')\n")


HELPERS = {"helper.ps1": '<# inline #> . "$PSScriptRoot/inline.ps1"\n<#\nA block naming noted_block.py.\n#>\n# A line naming noted_line.py.\n. "$PSScriptRoot/sourced.ps1"\n',
           "helper.js": "/*\n * A block naming noted_jsblock.py.\n */\n// A line naming noted_jsline.py.\n"
                        "const required = require('./required')\n"}


# Events a recorded proof logged across two runs of `pytest.main` on a file in its checkout.
PYTEST_EVENTS = [["r", ".pytest_cache/v/cache/lastfailed"], ["r", ".pytest_cache/v/cache/nodeids"],
                 ["p", ".pytest_cache"], ["p", ".pytest_cache/pyvenv.cfg"], ["p", ".pytest_cache/conda-meta/history"],
                 ["p", ".pytest_cache/v/cache"], ["w", "pytest-cache-files-xqagvd1i/README.md"],
                 ["w", "pytest-cache-files-xqagvd1i/.gitignore"], ["w", "pytest-cache-files-xqagvd1i/CACHEDIR.TAG"],
                 ["w", ".pytest_cache/v/cache/nodeids"]]


def _recorded_row(repo, proof):
    return (harness_tests._pass_cache(repo, [proof]).variants(os.path.join(repo, ".claude/tests/test_alpha.py")) or [{}])[0]

def case_deadline_skips_and_blocks_the_stamp():
    repo, proof, counter = make_repo()
    harness_tests._write_stamp(repo, 1, 1, [])
    write(repo, ".claude/tools/alpha.py", "import beta\nTABLE = 'table.json'\nA = 3\n")
    saved = dict(os.environ)
    os.environ.update({"HARNESS_DEADLINE": str(time.time() - 1), "HARNESS_SLOT_DIR": os.path.join(repo, "slots")})
    try:
        with redirect_stdout(io.StringIO()) as out:
            rc = harness_tests.run_scoped(repo, [".claude/tools/alpha.py"])
    finally:
        os.environ.clear()
        os.environ.update(saved)
    with open(harness_tests.STAMP_PATH, encoding="utf-8") as fh:
        stamped = json.load(fh)["files"].get(".claude/tools/alpha.py")
    check("a run past its deadline starts no proof, exits 3 and leaves the input unstamped",
          rc == 3 and runs(counter) == 0 and stamped != harness_tests.tree_entries(repo)[".claude/tools/alpha.py"],
          "rc=%s runs=%d out=%r" % (rc, runs(counter), out.getvalue()[-300:]))


def case_a_waiting_run_reuses_a_peer_pass():
    """Two runs want the same proof: the one that waited for a slot finds the other's pass."""
    repo, proof, counter = make_repo()
    slots = os.path.join(repo, "slots")
    env = {"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": "1"}
    held = _file_lock.acquire(os.path.join(slots, "slot-0.lock"), 5)
    result = {}
    waiting, real_take = threading.Event(), harness_tests._take_slots

    def take(exclusive, count=1, split=None):
        waiting.set()
        return real_take(exclusive, count)

    harness_tests._take_slots = take
    try:
        worker = threading.Thread(target=lambda: result.update(run(repo, [proof], env)))
        worker.start()
        entered = waiting.wait(30)
        harness_tests._pass_cache(repo, [proof]).record(proof)  # the peer's pass lands while this run waits
        _file_lock.release(held)
        worker.join(30)
    finally:
        harness_tests._take_slots = real_take
    check("the worker passed its first cache check and reached the slot wait", entered)
    check("a run that waited for a slot reuses the pass a peer recorded meanwhile",
          runs(counter) == 0 and result.get(proof) == "pass", "runs=%d status=%s" % (runs(counter), result))


def case_a_capped_proof_retries_at_its_cap():
    """A `proof_timeouts` cap already holds the load headroom, so its timeout retry runs at the cap, not
    three times it: a 1400 s cap retried at 4200 s held every machine slot for over an hour."""
    repo, _proof, _counter = make_repo()
    hang = write(repo, ".claude/tests/test_hang.py", "import time\ntime.sleep(30)\n")
    seen = []
    real_run, real_caps = harness_tests.run_proof, dict(harness_tests._PROOF_TIMEOUTS)

    def recording(path, timeout=None, root=None, **_):
        seen.append(timeout)
        return ("fail", timeout, "TIMEOUT after %ss" % timeout)

    harness_tests.run_proof = recording
    harness_tests._PROOF_TIMEOUTS["test_hang.py"] = 5
    try:
        run(repo, [hang], {"HARNESS_NO_PASS_CACHE": "1"})
    finally:
        harness_tests.run_proof = real_run
        harness_tests._PROOF_TIMEOUTS.clear()
        harness_tests._PROOF_TIMEOUTS.update(real_caps)
    check("a capped proof that times out retries at its cap", seen == [5, 5], "timeouts=%s" % seen)


def case_a_timeout_retry_takes_one_slot():
    """A pool timeout's retry takes one slot, not the machine: the live pace already stretched its deadline,
    and holding every slot for it would stall every other run there. (A red's exclusive retry:
    test_harness_tests_gate_time.py.)"""
    repo, _proof, _counter = make_repo()
    slow = write(repo, ".claude/tests/test_slow.py", "import sys\nsys.exit(0)\n")
    taken, results = [], [("fail", 1.0, "TIMEOUT after 1s"), ("pass", 0.1, "")]
    real_run, real_take = harness_tests.run_proof, harness_tests._take_slots

    def take(exclusive, count=1, split=None):
        taken.append(exclusive)
        return []

    harness_tests.run_proof = lambda path, timeout=None, root=None, **_: results.pop(0)
    harness_tests._take_slots = take
    try:
        run(repo, [slow], {"HARNESS_NO_PASS_CACHE": "1"})
    finally:
        harness_tests.run_proof = real_run
        harness_tests._take_slots = real_take
    check("a pool proof takes one slot and its timeout retry takes one slot", taken == [False, False],
          "exclusive per attempt=%s" % taken)


def case_a_timeout_whose_retry_the_deadline_prevents_is_skipped():
    """A pool timeout under load says nothing about the proof; when the deadline then refuses its exclusive
    retry, the proof is `skipped` (the commit guard's rerun-in-background answer), never a red `fail`."""
    repo, _proof, _counter = make_repo()
    slow = write(repo, ".claude/tests/test_loaded.py", "import sys\nsys.exit(0)\n")
    calls = []
    real_run = harness_tests.run_proof

    def time_out_then_expire(path, timeout=None, root=None, **_):
        calls.append(timeout)
        os.environ["HARNESS_DEADLINE"] = str(time.time() - 1)
        return ("fail", timeout, "TIMEOUT after %ss" % timeout)

    harness_tests.run_proof = time_out_then_expire
    try:
        statuses = run(repo, [slow], {"HARNESS_NO_PASS_CACHE": "1", "HARNESS_DEADLINE": str(time.time() + 86400)})
    finally:
        harness_tests.run_proof = real_run
    check("a timeout whose retry the deadline prevents is skipped, not failed",
          len(calls) == 1 and statuses.get(slow) == "skipped", "calls=%s statuses=%s" % (calls, statuses))


def case_a_close_checkout_pass_outlives_its_removed_checkout():
    """A close proves HEAD in a temp checkout it removes when it ends. Its passes are the ones the next close
    reuses, so a later record from another checkout keeps them; the next close's pass replaces them."""
    repo, proof, counter = make_repo()
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    parent = tempfile.mkdtemp(prefix="harnesspassclose_")
    saved = harness_tests.PASSES_PATH
    harness_tests.PASSES_PATH = os.path.join(parent, "passes.json")
    try:
        closes = []
        for n in (1, 2):
            head = os.path.join(parent, harness_tests._CHECKOUT_PREFIX + str(n))
            git(repo, "worktree", "add", "-q", "--detach", head)
            harness_tests._PassCache(head).record(os.path.join(head, os.path.relpath(proof, repo)), key="close-%d" % n)
            git(repo, "worktree", "remove", "--force", head)
            closes.append(head)
            if n == 1:
                harness_tests._PassCache(repo).record(proof, key="main")
                main = harness_tests._PassCache(repo)
                check("a removed close checkout's pass survives a record from another checkout",
                      main.hit(proof, "close-1") and main.hit(proof, "main"),
                      str([r.get("key") for r in main.variants(proof)]))
        main = harness_tests._PassCache(repo)
        check("the next close's pass replaces the last close's",
              main.hit(proof, "close-2") and not main.hit(proof, "close-1") and main.hit(proof, "main"),
              str([r.get("key") for r in main.variants(proof)]))
    finally:
        harness_tests.PASSES_PATH = saved


def case_a_miss_names_the_difference_from_its_own_checkouts_row():
    """A checkout that holds a row is judged against its own, not against the newest row of another. One that holds
    none still names which part another checkout's newest row differs in, marked `other:`, so a close's reuse line
    splits the miss by cause; no input of this checkout moved, so no file is charged to it (`miss_files`)."""
    repo, proof, counter = make_repo()
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    parent, saved = with_private_store()
    manifest = {"files": [".claude/tools/beta.py"], "probes": [], "dirs": [".claude/tools"], "static": [],
                "git": False, "opaque": False}
    try:
        head = os.path.join(parent, harness_tests._CHECKOUT_PREFIX + "1")
        git(repo, "worktree", "add", "-q", "--detach", head)
        head_proof = os.path.join(head, os.path.relpath(proof, repo))
        rel = os.path.relpath(proof, repo).replace(os.sep, "/")
        write(repo, ".claude/tools/beta.py", "B = 2\n")
        harness_tests._PassCache(repo).record(proof, key="session", manifest=manifest)
        close = harness_tests._PassCache(head)
        check("a close with no row of its own misses", not close.hit(head_proof, "close-key"))
        check("the miss names the part another checkout's newest row differs in, marked as another checkout's",
              close.misses.get(rel) == ["other:files"], str(close.misses.get(rel)))
        check("and charges no file to this checkout, whose own inputs did not move", not close.miss_files.get(rel),
              str(close.miss_files))
        close.record(head_proof, key="close-1", manifest=manifest)
        write(head, ".claude/tools/extra.py", "X = 1\n")
        harness_tests._PassCache(repo).record(proof, key="session-2", manifest=manifest)
        close = harness_tests._PassCache(head)
        check("a close with a row of its own misses", not close.hit(head_proof, "close-key"))
        check("the miss names the difference from the checkout's own row, not from the newer session row",
              close.misses.get(rel) == ["dirs"], str(close.misses.get(rel)))
    finally:
        harness_tests.PASSES_PATH = saved


def case_a_miss_with_no_comparable_row_names_why_none_compares():
    """The reasons a checkout has no row to compare against each get their own cause: another checkout's row
    recorded under an earlier key scheme (`other:scheme`), another checkout's row with no observed key
    (`other:static`), and this checkout's own pass whose reads could not be keyed (`unobserved`), which the lookup
    skips and which therefore reruns at every close until its reads can be keyed."""
    repo, proof, counter = make_repo()
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    parent, saved = with_private_store()
    scheme = harness_tests._KEY_SCHEME
    manifest = {"files": [".claude/tools/beta.py"], "probes": [], "dirs": [], "static": [],
                "git": False, "opaque": False}
    try:
        head = os.path.join(parent, harness_tests._CHECKOUT_PREFIX + "1")
        git(repo, "worktree", "add", "-q", "--detach", head)
        head_proof = os.path.join(head, os.path.relpath(proof, repo))
        rel = os.path.relpath(proof, repo).replace(os.sep, "/")
        harness_tests._KEY_SCHEME = scheme - 1
        harness_tests._PassCache(repo).record(proof, key="session", manifest=manifest)
        harness_tests._KEY_SCHEME = scheme
        close = harness_tests._PassCache(head)
        check("a row another checkout recorded under an earlier scheme misses", not close.hit(head_proof, "close-key"))
        check("the miss names the scheme, since every part but the scheme holds",
              close.misses.get(rel) == ["other:scheme"], str(close.misses.get(rel)))
        harness_tests._PassCache(repo).record(proof, key="session-1", manifest=manifest)
        path = store_file(rel)
        with open(path, encoding="utf-8") as fh:
            held = json.load(fh)
        for row in held[rel]:
            row.pop("parts", None)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(held, fh)
        write(head, ".claude/tools/beta.py", "B = 3\n")  # the two checkouts' bytes differ whatever the machine's line endings
        close = harness_tests._PassCache(head)
        check("a row that kept no parts misses as an earlier scheme's", not close.hit(head_proof, "close-key")
              and close.misses.get(rel) == ["other:scheme"], str(close.misses.get(rel)))
        harness_tests._PassCache(repo).record(proof, key="session-2")
        close = harness_tests._PassCache(head)
        check("a row another checkout recorded with no observed key misses", not close.hit(head_proof, "close-key"))
        check("the miss names the static key", close.misses.get(rel) == ["other:static"], str(close.misses.get(rel)))
        harness_tests._PassCache(head).record(head_proof, key="close-1", manifest=None, observable=True)
        close = harness_tests._PassCache(head)
        check("the checkout's own pass with no observed key misses", not close.hit(head_proof, "close-key"))
        check("the miss names that, not the other checkout's row", close.misses.get(rel) == ["unobserved"],
              str(close.misses.get(rel)))
    finally:
        harness_tests._KEY_SCHEME = scheme
        harness_tests.PASSES_PATH = saved


def case_two_checkouts_keep_their_own_passes():
    """Every checkout shares one pass cache, so two worktrees whose reached inputs differ must each keep
    their pass: one row per proof would let each run evict the other's and rerun the proof every time."""
    repo, proof, counter = make_repo()
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    git(repo, "worktree", "add", "-q", "--detach", os.path.join(repo, "..", os.path.basename(repo) + "_wt"))
    saved = harness_tests.PASSES_PATH
    harness_tests.PASSES_PATH = os.path.join(repo, "..", os.path.basename(repo) + "_passes.json")
    try:
        twin = os.path.join(repo, "..", os.path.basename(repo) + "_wt")
        main, other = harness_tests._PassCache(repo), harness_tests._PassCache(twin)
        twin_proof = os.path.join(twin, os.path.relpath(proof, repo))
        main.record(proof, key="checkout-a")
        other.record(twin_proof, key="checkout-b")
        check("a second checkout's pass keeps the first checkout's",
              main.hit(proof, "checkout-a") and other.hit(twin_proof, "checkout-b"))
        main.record(proof, key="checkout-a2")
        check("a checkout's new pass replaces only its own older one",
              not main.hit(proof, "checkout-a") and main.hit(proof, "checkout-a2")
              and other.hit(twin_proof, "checkout-b"), str(len(main.variants(proof))))
    finally:
        harness_tests.PASSES_PATH = saved


def case_the_rows_are_parsed_once_until_the_file_changes():
    """A run asks for a proof's rows at least twice, and the file holds every proof's: parse it once per
    file state, and again after a record replaces it."""
    repo, proof, counter = make_repo()
    parent = tempfile.mkdtemp(prefix="harnesspassrows_")
    saved, real, reads = harness_tests.PASSES_PATH, harness_tests.read_json_salvage, []
    harness_tests.PASSES_PATH = os.path.join(parent, "passes.json")
    harness_tests.read_json_salvage = lambda path: reads.append(path) or real(path)
    try:
        cache = harness_tests._PassCache(repo)
        cache.record(proof, key="k1")
        hits = [cache.hit(proof, "k1") for _ in range(3)]
        check("three lookups of an unchanged file parse it once", all(hits) and len(reads) == 1, str(len(reads)))
        cache.record(proof, key="k2")
        check("a record that replaced the file is read back", cache.hit(proof, "k2") and not cache.hit(proof, "k1")
              and len(reads) == 2, str(len(reads)))
    finally:
        harness_tests.PASSES_PATH, harness_tests.read_json_salvage = saved, real


def case_a_record_packs_an_inline_manifest():
    """A row from before manifests were packed still hits, and the next record of its proof packs it."""
    repo, proof, counter = make_repo()
    parent, saved = with_private_store()
    try:
        manifest = {"files": [".claude/tools/beta.py"], "probes": [], "dirs": [], "static": [], "git": False,
                    "opaque": False}
        twin = os.path.join(parent, "twin")
        os.makedirs(twin)
        cache = harness_tests._PassCache(repo)
        cache.record(proof, key="k", manifest=manifest)
        rel = os.path.relpath(proof, repo).replace(os.sep, "/")
        with open(store_file(rel), encoding="utf-8") as fh:
            rows = json.load(fh)
        row = rows[rel][0]
        packed = "zm" in row and "manifest" not in row
        row["manifest"] = harness_tests._unpack_manifest(row)
        del row["zm"]
        row["checkout"] = twin
        with open(store_file(rel), "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        legacy_hits = harness_tests._PassCache(repo).hit(proof, "k")
        harness_tests._PassCache(repo).record(proof, key="o")
        with open(store_file(rel), encoding="utf-8") as fh:
            kept = {r["key"]: r for r in json.load(fh)[rel]}
        check("a manifest is stored packed, an inline one still hits, and the next record packs it",
              packed and legacy_hits and set(kept) == {"o", "k"} and "zm" in kept["k"] and "manifest" not in kept["k"]
              and harness_tests._PassCache(repo).hit(proof, "k"), str({k: sorted(v) for k, v in kept.items()}))
    finally:
        harness_tests.PASSES_PATH = saved


def case_a_record_writes_only_its_own_proof_file():
    """Each proof keeps its rows in its own file, so a record touches that file alone and a damaged file costs
    one proof its pass."""
    repo, proof, counter = make_repo()
    parent, saved = with_private_store()
    try:
        second = write(repo, ".claude/tests/test_second.py", proof_text(counter, 0))
        cache = harness_tests._PassCache(repo)
        cache.record(proof, key="a")
        rel_a, rel_b = (os.path.relpath(p, repo).replace(os.sep, "/") for p in (proof, second))
        with open(store_file(rel_a), "rb") as fh:
            before = fh.read()
        stamp = os.stat(store_file(rel_a)).st_mtime_ns
        cache.record(second, key="b")
        with open(store_file(rel_a), "rb") as fh:
            same = fh.read() == before and os.stat(store_file(rel_a)).st_mtime_ns == stamp
        check("a record leaves another proof's file untouched", same and os.path.isfile(store_file(rel_b)))
        listed = sorted(os.listdir(harness_tests.pass_store_dir()))
        check("the store folder holds one file per proof",
              listed == sorted(os.path.basename(store_file(r)) for r in (rel_a, rel_b)), str(listed))
        with open(store_file(rel_a), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        fresh = harness_tests._PassCache(repo)
        check("a damaged proof file costs only that proof its pass", fresh.hit(second, "b") and not fresh.hit(proof, "a"))
        fresh.record(proof, key="a2")
        check("a record after the damage starts that proof's file over", harness_tests._PassCache(repo).hit(proof, "a2"))
    finally:
        harness_tests.PASSES_PATH = saved


def case_a_legacy_store_migrates_into_the_proof_files():
    """The store was one file for every proof, and one for every pooled case. Opening the cache moves nothing; the
    first read against a store moves its rows into the proof files and removes it, so the pass and case stores
    migrate separately; a row already in a proof file keeps its newer pass."""
    repo, proof, counter = make_repo()
    parent, saved = with_private_store()
    try:
        rel = os.path.relpath(proof, repo).replace(os.sep, "/")
        here, twin = os.path.normcase(os.path.abspath(repo)), os.path.join(parent, "twin")
        os.makedirs(twin)
        now = time.time()
        old = {"key": "legacy", "observations": [], "ts": now - 50, "checkout": here}
        elsewhere = {"key": "elsewhere", "observations": [], "ts": now - 40, "checkout": twin}
        other = {"key": "other", "observations": [], "ts": now - 30, "checkout": None}
        case_row = {"okey": "case-key", "parts": {}, "ts": now - 20, "checkout": here}
        with open(harness_tests.passes_path(), "w", encoding="utf-8") as fh:
            json.dump({rel: [old, elsewhere], ".claude/tests/test_other.py": [other]}, fh)
        with open(harness_tests.case_passes_path(), "w", encoding="utf-8") as fh:
            json.dump({rel + "::one": [case_row]}, fh)
        cache = harness_tests._PassCache(repo)
        check("opening the cache moves no legacy row",
              not os.path.exists(store_file(rel)) and not os.path.exists(store_file(rel, cases=True))
              and os.path.isfile(harness_tests.passes_path()) and os.path.isfile(harness_tests.case_passes_path()))
        check("a legacy row migrates and still hits", cache.hit(proof, "legacy") and cache.hit(proof, "elsewhere"),
              str([r.get("key") for r in cache.variants(proof)]))
        check("the pass read migrates every proof's legacy rows and leaves the case store",
              os.path.isfile(store_file(".claude/tests/test_other.py"))
              and not os.path.exists(harness_tests.passes_path())
              and os.path.isfile(harness_tests.case_passes_path()))
        cache.reusable_cases(proof)
        with open(store_file(rel, cases=True), encoding="utf-8") as fh:
            cases = json.load(fh)
        check("a legacy case row lands in its proof's case file", [r["okey"] for r in cases[rel + "::one"]] == ["case-key"],
              str(cases))
        check("the legacy files are gone once migrated", not os.path.exists(harness_tests.case_passes_path()))
        cache.record(proof, key="new")
        with open(harness_tests.passes_path(), "w", encoding="utf-8") as fh:
            json.dump({rel: [dict(old, key="stale-again", ts=now - 5)]}, fh)
        keys = [r.get("key") for r in harness_tests._PassCache(repo).variants(proof)]
        check("a later legacy write migrates without evicting this checkout's newer row",
              "new" in keys and "elsewhere" in keys and "legacy" not in keys and "stale-again" not in keys, str(keys))
    finally:
        harness_tests.PASSES_PATH = saved


def case_the_close_sweep_drops_dead_rows_and_aged_files():
    """A proof file is rewritten only when its proof records, so the close's sweep drops rows of a removed
    checkout and files no record has touched in 30 days: neither can ever be reused."""
    repo, proof, counter = make_repo()
    parent, saved = with_private_store()
    try:
        folder = harness_tests.pass_store_dir()
        os.makedirs(folder)
        now = time.time()
        gone = {"key": "g", "ts": now, "observations": [], "checkout": os.path.join(parent, "removed")}
        live = {"key": "l", "ts": now, "observations": [], "checkout": os.path.normcase(os.path.abspath(repo))}
        mixed, dead, aged = (store_file(r) for r in ("mixed.py", "dead.py", "aged.py"))
        for path, rows in ((mixed, {"mixed.py": [gone, live]}), (dead, {"dead.py": [gone]}),
                           (aged, {"aged.py": [dict(live, ts=now - 40 * 86400)]})):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
        os.utime(aged, (now - 40 * 86400, now - 40 * 86400))
        removed = harness_tests.sweep_pass_stores()
        with open(mixed, encoding="utf-8") as fh:
            kept = json.load(fh)["mixed.py"]
        check("the sweep drops a removed checkout's row and keeps a live one", [r["key"] for r in kept] == ["l"], str(kept))
        check("the sweep removes a file left with no row and a file past the window",
              not os.path.exists(dead) and not os.path.exists(aged) and os.path.exists(mixed) and removed == 2,
              str(os.listdir(folder)))
    finally:
        harness_tests.PASSES_PATH = saved


def case_the_run_log_keeps_no_behavior_report():
    """The behavior report was 98% of `harness_runs.json` and no reader opens it from disk; the caller's
    own row keeps it."""
    parent = tempfile.mkdtemp(prefix="harnessruns_")
    saved = harness_tests.RUNS_PATH
    harness_tests.RUNS_PATH = os.path.join(parent, "runs.json")
    try:
        with open(harness_tests.RUNS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"runs": [{"mode": "old", "behavior": {"claims": [1, 2]}, "wall_s": 1.0}]}, fh)
        row = {"mode": "close", "behavior": {"claims": [3]}, "wall_s": 2.0, "reused": 4}
        harness_tests._record_run(row)
        with open(harness_tests.RUNS_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)["runs"]
        check("a recorded run keeps its figures and drops the behavior report, old rows included",
              [r["mode"] for r in stored] == ["old", "close"] and stored[1]["reused"] == 4
              and not any("behavior" in r for r in stored) and "behavior" in row, str(stored))
    finally:
        harness_tests.RUNS_PATH = saved


def case_an_unparseable_spawn_marks_the_manifest_opaque():
    """A real close died on a spawned command line with an unbalanced quote (`No closing quotation`):
    the manifest builder must mark such reads opaque, never raise into the run."""
    repo, proof, _counter = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    events = [["r", os.path.join(repo, ".claude", "tools", "alpha.py")],
              ["s", {"argv": ['git log "--format=%h'], "cwd": repo, "recorded": True}]]
    try:
        manifest = cache.manifest(proof, {"events": events, "temp": tempfile.gettempdir()})
        ok = isinstance(manifest, dict) and manifest.get("opaque") is True
    except Exception as exc:  # noqa: BLE001 - the case is that nothing escapes
        ok, manifest = False, repr(exc)
    check("an unparseable spawned command line marks the manifest opaque instead of raising", ok, str(manifest))


def case_a_non_python_child_running_a_temp_copy_is_opaque():
    """`node <temp>/x.js` runs a copy outside the repo: no recorder follows a non-Python child and the copy
    is outside the static reach, so its reads of the real repo go unkeyed. Mark the manifest opaque, as a
    Python child whose script lies outside the repo already is. A repo script the child names stays keyed."""
    repo, proof, _counter = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    scratch = tempfile.mkdtemp(prefix="harnesspasscache_scratch_")
    copy = write(scratch, "x.js", "// copy of a repo script\n")
    alpha = ["r", os.path.join(repo, ".claude", "tools", "alpha.py")]
    manifest = cache.manifest(proof, {"events": [alpha, ["s", {"argv": ["node", copy], "cwd": repo}]],
                                      "temp": scratch})
    check("a non-Python child running a temp copy marks the manifest opaque",
          isinstance(manifest, dict) and manifest.get("opaque") is True, str(manifest))
    repo_script = os.path.join(repo, ".claude", "tools", "gamma.py")
    manifest = cache.manifest(proof, {"events": [alpha, ["s", {"argv": ["node", repo_script], "cwd": repo}]],
                                      "temp": scratch})
    check("a non-Python child running a repo script keys it statically, not opaque",
          isinstance(manifest, dict) and not manifest.get("opaque")
          and manifest.get("static") == [".claude/tools/gamma.py"], str(manifest))


def case_a_file_edited_mid_run_changes_the_key_a_later_lookup_takes():
    repo, proof, counter = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    first = cache.key(proof)
    write(repo, ".claude/tools/beta.py", "B = 2\n")
    check("a lookup after an edit within one run sees the edited bytes", cache.key(proof) != first,
          "the key stayed on the digests taken at the run's start")


def case_a_row_recorded_under_another_key_scheme_is_never_reused():
    """A row's key was taken over what its scheme held. A row an earlier runner wrote cannot be told to cover what
    the current key holds, even when every part it hashed still agrees."""
    repo, proof, counter = make_repo()
    saved = harness_tests._KEY_SCHEME
    try:
        harness_tests._KEY_SCHEME = saved - 1
        run(repo, [proof])
        check("a pass recorded under the scheme in force is reused by it", runs(counter) == 1)
        cache = harness_tests._PassCache(repo)
        check("a row of the previous scheme matches under that scheme", cache.hit(proof, cache.key(proof)))
        harness_tests._KEY_SCHEME = saved
        harness_tests._ROWS_MEMO.clear()
        cache = harness_tests._PassCache(repo)
        check("a row of the previous scheme is not a match under the current one", not cache.hit(proof, cache.key(proof)))
        run(repo, [proof])
        check("it reruns once, and the new row is reused", runs(counter) == 2)
        run(repo, [proof])
        check("then it is reused again", runs(counter) == 2, "runs=%d" % runs(counter))
    finally:
        harness_tests._KEY_SCHEME = saved


def case_a_lookup_matches_once():
    """A reuse check asks the row matcher once: the receipts travel with the match, so a hit is not looked up
    twice. The pass cache's `hit` and `observations` each called `_matched` on HEAD."""
    repo, proof, _counter = make_repo()
    parent, saved = with_private_store()
    try:
        from _proof_runner import evidence
        cache = harness_tests._PassCache(repo)
        cache.record(proof, key="k")
        calls, real = [], cache._matched
        cache._matched = lambda p, k: (calls.append(p), real(p, k))[1]
        hit = evidence.lookup(cache, proof, "k")
        after_hit = len(calls)
        missed = evidence.lookup(cache, proof, "other")
        check("a lookup answers a Hit carrying the receipts from one match",
              isinstance(hit, evidence.Hit) and hit.receipts == () and after_hit == 1,
              "%r, %d match(es)" % (hit, after_hit))
        check("a lookup that misses is a typed Miss naming the part, still from one match",
              isinstance(missed, evidence.Miss) and missed.reasons == ("static",) and len(calls) == after_hit + 1,
              "%r, %d match(es)" % (missed, len(calls) - after_hit))
    finally:
        harness_tests.PASSES_PATH = saved


def case_whole_and_case_rows_round_trip_through_one_codec():
    """One codec packs and decodes both a whole-run row and a case row, and a row the shared store already
    held (written by HEAD's codec) still decodes and re-packs to its bytes."""
    real_rows = _store_rows(harness_tests.pass_store_dir())
    repo, proof, _counter = make_repo()
    parent, saved = with_private_store()
    try:
        from _proof_runner import evidence
        manifest = {"files": [".claude/tools/beta.py"], "probes": [], "dirs": [], "static": [], "git": False,
                    "opaque": False}
        cache = harness_tests._PassCache(repo)
        cache.record(proof, key="k", manifest=manifest,
                     observations=[{"proof": "p", "caseId": "c", "inputKey": "i", "status": "pass"}])
        cache.record_cases(proof, {"one": manifest})
        rel = os.path.relpath(proof, repo).replace(os.sep, "/")
        with open(store_file(rel), encoding="utf-8") as fh:
            row = json.load(fh)[rel][0]
        hit = evidence.lookup(cache, proof, "k")
        cases = evidence.lookup_cases(cache, proof)
        decoded = evidence.unpack_manifest(row)
        check("a whole row and a case row decode through the one codec",
              isinstance(decoded, dict) and decoded.get("files") == manifest["files"]
              and isinstance(hit, evidence.Hit) and hit.receipts == ({"proof": "p", "caseId": "c",
                                                                     "inputKey": "i", "status": "pass"},)
              and isinstance(cases, evidence.CaseReuse) and sorted(cases.held) == ["one"],
              "row=%r hit=%r cases=%r" % (decoded, hit, cases))
        check("a stored row re-packs to its bytes through the codec",
              all(evidence.pack_manifest(evidence.unpack_manifest(r)) == r["zm"] for r in real_rows),
              "%d stored rows" % len(real_rows))
    finally:
        harness_tests.PASSES_PATH = saved


def case_pytests_own_cache_leaves_the_reads_observable():
    """pytest reads, probes and rewrites `.pytest_cache` in its rootdir on every run; that cache steers
    only flags no proof passes, so it neither marks the reads opaque nor joins the key. A proof's other
    write into the checkout still does."""
    repo, proof, _counter = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    case = os.path.join(repo, ".claude", "tools", "alpha.py")
    temp = tempfile.mkdtemp(prefix="harnesspasscache_temp_")
    events = [["r", case]] + [[kind, os.path.join(repo, *rel.split("/"))] for kind, rel in PYTEST_EVENTS]
    manifest = cache.manifest(proof, {"events": events, "temp": temp})
    kept = [r for key in ("files", "probes", "dirs") for r in (manifest or {}).get(key, []) if "pytest" in r]
    check("pytest's own cache leaves a proof's reads observable and out of its key",
          isinstance(manifest, dict) and manifest["opaque"] is False and not kept
          and ".claude/tools/alpha.py" in manifest["files"], str(manifest))
    events.append(["w", os.path.join(repo, ".claude", "tools", "out.json")])
    manifest = cache.manifest(proof, {"events": events, "temp": temp})
    check("a write into the checkout outside pytest's cache still marks the reads opaque",
          isinstance(manifest, dict) and manifest.get("opaque") is True, str(manifest))


def case_a_deadline_skipped_proof_exits_3_not_2():
    repo, proof, counter = make_repo()
    saved = dict(os.environ)
    os.environ.update({"HARNESS_DEADLINE": str(time.time() - 1), "HARNESS_SLOT_DIR": os.path.join(repo, "slots")})
    try:
        with redirect_stdout(io.StringIO()) as out:
            rc = harness_tests.run_all(repo)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check("a full run whose proof the deadline skipped exits 3 and writes no stamp",
          rc == 3 and runs(counter) == 0 and not os.path.exists(harness_tests.STAMP_PATH),
          "rc=%s runs=%d out=%r" % (rc, runs(counter), out.getvalue()[-300:]))
    saved = dict(os.environ)
    os.environ.update({"HARNESS_DEADLINE": str(time.time() - 1), "HARNESS_SLOT_DIR": os.path.join(repo, "slots")})
    try:
        with redirect_stdout(io.StringIO()):
            rc = harness_tests.main(["--repo", repo, "--proofs-for", ".claude/tools/alpha.py"])
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check("--proofs-for with a deadline-skipped proof exits 3, not 2", rc == 3, "rc=%s" % rc)


def case_an_unusable_slot_store_is_reported_not_waited_on():
    """Run in a child: a runner that waits forever must fail this case, not hang the proof."""
    repo, proof, counter = make_repo()
    blocker = write(repo, "slots_is_a_file", "x")
    # The child's stamp, and the run log beside it, stay in the temp repo: never the live checkout's logs.
    stamp = os.path.join(repo, "child_stamp.json")
    env = dict(os.environ, HARNESS_SLOT_DIR=blocker, HARNESS_NO_PASS_CACHE="1", HARNESS_TEST_STAMP=stamp)
    env.pop("HARNESS_DEADLINE", None)
    try:
        done = subprocess.run([sys.executable, os.path.join(_SCRIPTS_DIR, "harness_tests.py"), "--repo", repo,
                               "--proofs-for", ".claude/tools/alpha.py"],
                              env=env, capture_output=True, text=True, timeout=25)
        rc, out = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as exc:
        rc, out = "timeout", str(exc.stdout)[-300:]
    check("a slot directory that cannot exist ends the run as cannot-run (exit 2) instead of waiting forever",
          rc == 2 and runs(counter) == 0, "rc=%s runs=%d out=%r" % (rc, runs(counter), out[-300:]))
    check("the child's run row lands beside its redirected stamp, not in the live run log",
          os.path.isfile(os.path.join(repo, "harness_runs.json")), "no harness_runs.json in %s" % repo)


def case_a_head_comparison_takes_a_slot_and_honours_the_deadline():
    repo, proof, _counter = make_repo()
    head = harness_tests._git(repo, ["rev-parse", "HEAD"]).strip()
    taken, started = [], []
    real_run, real_take = harness_tests.run_proof, harness_tests._take_slots

    def take(exclusive, count=1, split=None):
        taken.append(exclusive)
        return None

    harness_tests.run_proof = lambda path, timeout=None, root=None, **_: (started.append(path), ("fail", 0.0, "FAIL x"))[1]
    harness_tests._take_slots = take
    try:
        reds = harness_tests._reds_at(repo, head, [proof])
    finally:
        harness_tests.run_proof = real_run
        harness_tests._take_slots = real_take
    check("a HEAD comparison waits for a machine slot, and a deadline that passes there starts nothing",
          taken == [False] and not started and not reds, "slot requests=%s started=%s reds=%s" % (taken, started, reds))


def case_a_runner_of_another_width_joins_the_width_holding_a_slot():
    repo, _proof, _counter = make_repo()
    slots = os.path.join(repo, "slots")
    saved = dict(os.environ)

    def take(width, exclusive, deadline=None):
        os.environ.update({"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": str(width)})
        os.environ.pop("HARNESS_DEADLINE", None)
        if deadline:
            os.environ["HARNESS_DEADLINE"] = str(time.time() + deadline)
        try:
            return harness_tests._take_slots(exclusive)
        except harness_tests.SlotStoreError as exc:
            return exc

    def recorded():
        with open(os.path.join(slots, "width.json"), encoding="utf-8") as fh:
            return json.load(fh).get("width")

    try:
        serial = take(1, True)
        joined = take(2, False, deadline=1)
        width_while_held = recorded()
        for handle in serial + (joined if isinstance(joined, list) else []):
            _file_lock.release(handle)
        free = take(2, False)
        same = take(2, False)
        width_after = recorded()
        for handle in (free if isinstance(free, list) else []) + (same if isinstance(same, list) else []):
            _file_lock.release(handle)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check("a runner whose width differs from the one holding a slot joins the held width and waits for its slot, "
          "never failing as cannot-run", joined is None and width_while_held == 1,
          "second runner got %r, width %r" % (joined, width_while_held))
    check("a different width is recorded once no slot is held, and the same width always is accepted",
          isinstance(free, list) and isinstance(same, list) and width_after == 2,
          "free=%r same=%r width=%r" % (free, same, width_after))


def case_a_runner_with_no_width_set_adopts_the_recorded_width():
    repo, _proof, _counter = make_repo()
    slots = os.path.join(repo, "slots")
    saved = dict(os.environ)
    try:
        os.environ.update({"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": "3"})
        os.environ.pop("HARNESS_DEADLINE", None)
        held = harness_tests._take_slots(False)
        os.environ.pop("HARNESS_MACHINE_SLOTS")
        try:
            adopted = harness_tests._take_slots(False)
        except harness_tests.SlotStoreError as exc:
            adopted = exc
        width = harness_tests._machine_slots()
        for handle in held + (adopted if isinstance(adopted, list) else []):
            _file_lock.release(handle)
        os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "fresh_slots")
        fresh = harness_tests._machine_slots()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check("a runner with HARNESS_MACHINE_SLOTS unset joins the width a running proof recorded, not a default",
          isinstance(adopted, list) and width == 3, "adopted=%r width=%r" % (adopted, width))
    check("with no width recorded and none set, the width is the default 4", fresh == 4, "fresh=%r" % (fresh,))


def case_the_behavior_plan_is_built_once_per_cache():
    """Every proof's key folds in its behavior binding, and the plan behind it scans the whole repo: built per
    proof, a scoped run spends minutes before its first proof starts."""
    repo, proof, counter = make_repo()
    real, calls = harness_tests._behavior_plan, []
    harness_tests._behavior_plan = lambda root, paths=None: calls.append(paths) or real(root, paths)
    try:
        cache = harness_tests._PassCache(repo)
        rel = os.path.relpath(proof, repo).replace(os.sep, "/")
        manifest = {"files": [rel], "probes": [], "dirs": [], "static": [], "git": False, "opaque": False}
        keys = [cache.observed_key(proof, manifest) for _ in range(3)]
        check("three proof keys build the behavior plan once", len(calls) == 1 and len(set(keys)) == 1,
              str(calls))
    finally:
        harness_tests._behavior_plan = real


def case_a_run_asks_git_each_distinct_read_of_its_snapshot_once():
    """The git scopes overlap (`repo` is the union of head, changes and refs), so a snapshot that read every scope
    alone asked for `status`, `diff`, `for-each-ref` and `rev-parse HEAD` twice per run, the cost of every run."""
    repo, proof, _counter = make_repo()
    cache = harness_tests._PassCache(repo)
    real, asked = harness_tests._git_run.run, []

    def spy(args, **kwargs):
        asked.append((tuple(args), (kwargs.get("env") or {}).get("GIT_CONFIG_GLOBAL")))
        return real(args, **kwargs)
    harness_tests._git_run.run = spy
    try:
        cache.snapshot_git()
        snapshot, held = list(asked), {name: getattr(cache, method)()
                                       for name, method in harness_tests._GIT_SCOPES.items()}
        del asked[:]
        fresh = {name: getattr(cache, method)(fresh=True) for name, method in harness_tests._GIT_SCOPES.items()}
    finally:
        harness_tests._git_run.run = real
    repeated = sorted({" ".join(args) for args, _ in snapshot if snapshot.count((args, _)) > 1})
    check("a snapshot asks git each distinct read once", not repeated, "asked twice: %s" % repeated)
    check("a later fresh read asks git again and agrees with the snapshot", len(asked) > 0 and held == fresh,
          "fresh reads=%d agree=%s" % (len(asked), held == fresh))


def case_the_head_and_upstream_scopes_ask_git_once_and_key_as_the_single_reads_did():
    """`head` was three `rev-parse` calls and `upstream` two, read at the snapshot and again at every record of a
    proof that resolves HEAD or runs `status`. One call each now; the digest must stay the one the single reads
    gave, or every recorded row stops matching. Detached and unborn HEADs fail the batch, which answers as the
    single reads do."""
    def single(repo, args):
        return subprocess.run(["git"] + args, cwd=repo, capture_output=True)

    def expected(repo):
        reads = [single(repo, a) for a in (["rev-parse", "--abbrev-ref", "HEAD"], ["rev-parse", "HEAD^{tree}"],
                                          ["rev-parse", "HEAD"])]
        head = None if any(r.returncode for r in reads) else \
            hashlib.sha256("\n".join(r.stdout.decode("utf-8") for r in reads).encode("utf-8")).hexdigest()
        name = single(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
        if name.returncode == 0:
            value = single(repo, ["rev-parse", "@{upstream}"])
            upstream = hashlib.sha256(name.stdout + b"\n" + value.stdout).hexdigest() if value.returncode == 0 else None
        else:
            upstream = hashlib.sha256(b"").hexdigest() if b"no upstream configured" in name.stderr else None
        return head, upstream

    real = harness_tests._git_run.run
    repo, _proof, _counter = make_repo()
    unborn = tempfile.mkdtemp(prefix="harnesspassunborn_")
    git(unborn, "init", "-q")
    states = [("no upstream", repo, None)]
    states.append(("tracks a branch", repo, lambda: (git(repo, "branch", "-q", "base"),
                                                     git(repo, "branch", "-q", "--set-upstream-to=base"))))
    states.append(("detached", repo, lambda: git(repo, "checkout", "-q", "--detach")))
    states.append(("unborn", unborn, None))
    for label, where, prepare in states:
        if prepare:
            prepare()
        cache, asked = harness_tests._PassCache(where), []

        def spy(args, **kwargs):
            asked.append(args)
            return real(args, **kwargs)
        harness_tests._git_run.run = spy
        try:
            got = (cache._head_state(fresh=True), cache._upstream_state(fresh=True))
        finally:
            harness_tests._git_run.run = real
        check("%s: head and upstream key as the single reads did" % label, got == expected(where),
              "got %s expected %s" % (got, expected(where)))
        if label in ("no upstream", "tracks a branch"):
            check("%s: head and upstream are one git call each" % label, len(asked) == 2,
                  "asked %s" % [" ".join(a) for a in asked])


def case_the_pool_width_defaults_to_the_machines_recorded_slots():
    repo, _proof, _counter = make_repo()
    slots = os.path.join(repo, "slots")
    saved = dict(os.environ)
    try:
        os.environ.update({"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": "5"})
        os.environ.pop("HARNESS_TEST_JOBS", None)
        held = harness_tests._take_slots(False)
        for handle in held:
            _file_lock.release(handle)
        os.environ.pop("HARNESS_MACHINE_SLOTS")
        adopted = harness_tests._jobs()
        os.environ["HARNESS_TEST_JOBS"] = "3"
        explicit = harness_tests._jobs()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check("a pool started with HARNESS_TEST_JOBS unset is as wide as the machine's recorded slots, "
          "not the hard-coded 4 every recorded close ran at",
          adopted == 5, "pool width %r" % adopted)
    check("an explicit HARNESS_TEST_JOBS still wins", explicit == 3, "pool width %r" % explicit)


def case_a_zero_event_pass_keeps_the_evidence_that_names_it():
    """The close's 92 permanently rerun passes: an observable run whose recorder never loaded records no
    observed key, and the row must carry why it did not, so the next close names the trigger instead of
    rerunning the proof forever in silence."""
    repo, proof, counter = make_repo(live=True)
    broken = os.path.join(repo, ".claude", "bad_recorder")
    write(repo, ".claude/bad_recorder/sitecustomize.py", "raise ImportError('planted recorder fault')\n")
    saved = harness_tests._RECORDER_DIR, harness_tests._attempt._RECORDER_DIR
    harness_tests._RECORDER_DIR = harness_tests._attempt._RECORDER_DIR = broken
    out = io.StringIO()
    saved_env = dict(os.environ)
    try:
        os.environ.update({"PYTHONPATH": "", "HARNESS_READ_LOG": "", "HARNESS_READ_RECORDER": ""})
        os.environ.setdefault("HARNESS_SLOT_DIR", os.path.join(repo, "slots"))
        with redirect_stdout(out):
            harness_tests._run_selected([proof], False, {}, None,
                                        harness_tests.RunOptions(harness_tests._pass_cache(repo, [proof])))
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        harness_tests._RECORDER_DIR, harness_tests._attempt._RECORDER_DIR = saved
    row = _recorded_row(repo, proof)
    why = row.get("why") or ""
    check("a zero-event pass records why it kept no observed key",
          bool(why) and not row.get("observed"), "row=%r" % row)
    check("the kept reason names the recorder fault the child printed",
          "sitecustomize" in why and "planted recorder fault" in why, "why=%r" % why[:300])
    check("the close log names the unobserved proof",
          "UNOBSERVED test_alpha.py" in out.getvalue(),
          out.getvalue()[-400:])


def case_a_recorder_beacon_is_not_an_event():
    """A beacon file says the recorder loaded; a run that keeps zero events can then be told apart from one
    whose recorder never started. It is a marker, never a read event."""
    log = tempfile.mkdtemp(prefix="harnessbeacon_")
    write(log, "123.beacon", '{"recorder": "python"}\n')
    write(log, "456.jsonl", '["r", "C:/x/y.py"]\n')
    events = harness_tests._attempt._read_events(log)
    check("a recorder beacon is not a read event", events == [["r", "C:/x/y.py"]], repr(events))
    check("the launcher counts beacons apart from event files",
          harness_tests._attempt._log_files(log, ".beacon") == 1
          and harness_tests._attempt._log_files(log, ".jsonl") == 1)


def case_a_manifest_fault_is_named_in_the_row():
    """The other cause of a permanently rerun pass: the recorder worked, but building the manifest raised.
    The row keeps the fault, so a swallowed exception is never an anonymous rerun."""
    repo, proof, counter = make_repo()
    saved = harness_tests._PassCache.manifest

    def boom(self, *args, **kwargs):
        raise RuntimeError("planted manifest fault")

    harness_tests._PassCache.manifest = boom
    try:
        run(repo, [proof])
    finally:
        harness_tests._PassCache.manifest = saved
    row = _recorded_row(repo, proof)
    why = row.get("why") or ""
    check("a manifest fault is kept in the row",
          bool(why) and "RuntimeError: planted manifest fault" in why and not row.get("observed"),
          "row=%r" % row)


def main():
    """Every `case_*` function in the pool. Each case builds its own repo and slot folder, so no case depends on another."""
    _template()
    return run_cases(checked_cases(globals()))


if __name__ == "__main__":
    sys.exit(main())
