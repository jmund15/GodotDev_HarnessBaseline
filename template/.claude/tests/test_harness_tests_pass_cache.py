"""Re-runnable proof for the harness_tests.py pass cache, machine proof slots and run deadline.

A proof's pass is reused while every input it reaches is byte-identical: under the static key, the proof
itself, each `.claude/` file it names, the code those files may run in turn (never a file only a comment or
docstring names), and the runner; under the observed key, what a Python proof actually read, probed and
listed in its last pass, with each unrecorded child's script keyed statically and its data arguments by
their bytes. The slots cap proofs
running at once across every runner process on the machine; a deadline stops a run that would
outlast its caller.

    python3 .claude/tests/test_harness_tests_pass_cache.py
"""
import ast
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
from contextlib import redirect_stdout

# A battery run that reuses passes exports this to every proof; each case here sets it only where it means to.
os.environ.pop("HARNESS_REUSE_PASSES", None)
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402
import _file_lock  # noqa: E402  (harness_tests puts .claude/hooks on sys.path)

FAILS = []
TOTAL = 0


def check(name, ok, detail=""):
    global TOTAL
    TOTAL += 1
    print("%-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILS.append("%s: %s" % (name, detail))


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True, capture_output=True)


def make_repo(close=False):
    """tools/alpha.py imports beta and reads table.json; tools/gamma.py is unrelated. The proof
    test_alpha.py imports alpha and counts its own runs in a file outside the repo. `close` names the
    checkout as a close names its private one."""
    repo = tempfile.mkdtemp(prefix="harnesspasscache_")
    os.environ.pop("HARNESS_SLOT_BUDGET", None)
    os.environ["HARNESS_MACHINE_ROOT"] = os.path.join(repo, "slots")
    if close:
        repo = os.path.join(repo, harness_tests._CHECKOUT_PREFIX + "fixture")
        os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    write(repo, ".claude/settings.json", "{}\n")
    write(repo, ".claude/tools/alpha.py", ALPHA)
    write(repo, ".claude/tools/beta.py", "B = 1\n")
    write(repo, ".claude/tools/table.json", "{}\n")
    write(repo, ".claude/tools/gamma.py", "G = 1\n")
    counter = os.path.join(tempfile.mkdtemp(prefix="harnesspasscount_"), "runs.txt")
    proof = write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    harness_tests.EXCLUDED = {}
    harness_tests.SERIAL = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
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


def run(repo, proofs, env=None):
    """_run_selected over `proofs` with the pass cache; returns {proof: status}."""
    saved = dict(os.environ)
    os.environ.update(env or {})
    os.environ.setdefault("HARNESS_SLOT_DIR", os.path.join(repo, "slots"))
    statuses = {}
    try:
        with redirect_stdout(io.StringIO()):
            harness_tests._run_selected(proofs, False, statuses, None,
                                        harness_tests.RunOptions(harness_tests._pass_cache(repo, proofs)))
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return statuses


def case_reuse_and_invalidation():
    repo, proof, counter = make_repo()
    run(repo, [proof])
    second = run(repo, [proof])
    check("a passed proof is reused while its inputs are unchanged",
          runs(counter) == 1 and second.get(proof) == "pass", "runs=%d status=%s" % (runs(counter), second))
    write(repo, ".claude/tools/gamma.py", "G = 2\n")
    run(repo, [proof])
    check("an edit to a file the proof never reaches keeps the reuse", runs(counter) == 1, "runs=%d" % runs(counter))
    for rel, text in ((".claude/tools/alpha.py", ALPHA + "A = 2\n"),
                      (".claude/tools/beta.py", "B = 2\n"),
                      (".claude/tools/table.json", "{\"a\": 1}\n")):
        before = runs(counter)
        write(repo, rel, text)
        run(repo, [proof])
        check("an edit to %s (reached by the proof) reruns it" % rel.rsplit("/", 1)[-1],
              runs(counter) == before + 1, "runs %d -> %d" % (before, runs(counter)))
    before = runs(counter)
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "# edited\n"))
    run(repo, [proof])
    check("an edit to the proof itself reruns it", runs(counter) == before + 1, "runs=%d" % runs(counter))
    before = runs(counter)
    git(repo, "rm", "-q", "--cached", ".claude/tools/beta.py")
    run(repo, [proof])
    check("a change in whether a reached file is tracked reruns it", runs(counter) == before + 1,
          "runs=%d" % runs(counter))


def case_failure_is_never_reused():
    repo, proof, counter = make_repo()
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 1))
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
    scanner = write(repo, ".claude/tests/test_uncorpused_scan.py", proof_text(scanner_counter, 0))
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
    try:
        with redirect_stdout(io.StringIO()):
            harness_tests.run_all(repo)
    finally:
        del os.environ["HARNESS_SLOT_DIR"]
    check("the full battery ignores the cache", runs(counter) == 3, "runs=%d" % runs(counter))


def case_the_full_battery_reuses_recorded_passes_when_asked():
    repo, proof, counter = make_repo()
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    os.environ["HARNESS_REUSE_PASSES"] = "1"
    out = io.StringIO()
    try:
        with redirect_stdout(io.StringIO()):
            harness_tests.run_all(repo)
        with redirect_stdout(out):
            harness_tests.run_all(repo)
    finally:
        del os.environ["HARNESS_REUSE_PASSES"]
        del os.environ["HARNESS_SLOT_DIR"]
    check("a full battery with HARNESS_REUSE_PASSES reuses a recorded pass",
          runs(counter) == 1 and "reused from the pass cache" in out.getvalue(),
          "runs=%d out=%r" % (runs(counter), out.getvalue()[-300:]))


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


def case_a_proof_never_inherits_the_callers_session_identity():
    repo, proof, counter = make_repo()
    seen = os.path.join(os.path.dirname(counter), "identity.txt")
    names = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_PROMPT_FILE",
             "CLAUDE_CODE_SIDECAR_LABEL", "SIDECAR_PARENT_SESSION_ID")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, extra=(
        "with open(%r, 'w') as fh:\n    fh.write(','.join(n for n in %r if n in os.environ))\n" % (seen, names))))
    statuses = run(repo, [proof], {n: "caller" for n in names})
    with open(seen, encoding="utf-8") as fh:
        leaked = fh.read()
    check("a proof never inherits the calling session's or delegate's identity",
          statuses.get(proof) == "pass" and leaked == "", "status=%s leaked=%r" % (statuses.get(proof), leaked))


def case_a_proof_never_inherits_the_callers_run_knobs():
    repo, proof, counter = make_repo()
    seen = os.path.join(os.path.dirname(counter), "knobs.txt")
    names = ("HARNESS_DEADLINE", "HARNESS_NO_PASS_CACHE", "HARNESS_REUSE_PASSES", "HARNESS_TEST_JOBS",
             "HARNESS_MACHINE_SLOTS")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, extra=(
        "with open(%r, 'w') as fh:\n    fh.write(','.join(n for n in %r if n in os.environ))\n" % (seen, names))))
    statuses = run(repo, [proof], {"HARNESS_DEADLINE": str(time.time() + 600), "HARNESS_NO_PASS_CACHE": "1",
                                   "HARNESS_REUSE_PASSES": "1", "HARNESS_TEST_JOBS": "1", "HARNESS_MACHINE_SLOTS": "1"})
    with open(seen, encoding="utf-8") as fh:
        leaked = fh.read()
    check("a proof never inherits the caller's run knobs: a runner the proof starts decides its own reuse and width",
          statuses.get(proof) == "pass" and leaked == "", "status=%s leaked=%r" % (statuses.get(proof), leaked))


def case_slots():
    repo, proof, counter = make_repo()
    slots = os.path.join(repo, "slots")
    env = {"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": "1"}
    held = _file_lock.acquire(os.path.join(slots, "slot-0.lock"), 5)
    try:
        statuses = run(repo, [proof], dict(env, HARNESS_DEADLINE=str(time.time() + 2)))
    finally:
        _file_lock.release(held)
    check("a proof waits for a free machine slot and is skipped at the deadline",
          runs(counter) == 0 and statuses.get(proof) == "skipped", "runs=%d status=%s" % (runs(counter), statuses))
    statuses = run(repo, [proof], env)
    check("a freed slot runs the proof", runs(counter) == 1 and statuses.get(proof) == "pass",
          "runs=%d status=%s" % (runs(counter), statuses))
    with open(counter, encoding="utf-8") as fh:
        seen = fh.read().splitlines()[-1]
    check("a proof's own runs get a private slot dir, so a nested runner never waits on its parent's slot",
          seen and os.path.normcase(os.path.abspath(seen)) != os.path.normcase(os.path.abspath(slots))
          and "harness_proof_" in seen, "HARNESS_SLOT_DIR in proof=%r" % seen)

    serial_counter = counter + ".serial"
    serial = write(repo, ".claude/tests/test_serial.py", proof_text(serial_counter, 0))
    harness_tests.SERIAL = {"test_serial.py": "owns the machine"}
    write(repo, "slots/width.json", '{"width": 2}')
    held = _file_lock.acquire(os.path.join(slots, "slot-1.lock"), 5)
    try:
        statuses = run(repo, [serial], {"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": "2",
                                        "HARNESS_DEADLINE": str(time.time() + 2)})
    finally:
        _file_lock.release(held)
        harness_tests.SERIAL = {}
    check("a serial proof waits for every machine slot", runs(serial_counter) == 0 and statuses.get(serial) == "skipped",
          "runs=%d status=%s" % (runs(serial_counter), statuses))


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


def case_a_pass_records_only_the_bytes_it_ran_on():
    """An input that changes between the key and the run leaves no pass: the pre-run key names bytes
    the proof never ran on, and reverting to them must rerun the proof."""
    repo, proof, counter = make_repo()
    beta = os.path.join(repo, ".claude", "tools", "beta.py")
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "with open(%r, 'w', newline='\\n') as fh:\n    fh.write('B = 9\\n')\n" % beta))
    run(repo, [proof])
    write(repo, ".claude/tools/beta.py", "B = 1\n")
    run(repo, [proof])
    check("a pass whose reached input moved during the run is not recorded under the pre-run key",
          runs(counter) == 2, "runs=%d" % runs(counter))


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


def case_a_pass_never_lands_under_a_key_taken_before_the_slot_wait():
    """The key is taken again once the slot is held: bytes that changed while the run waited and were
    put back before the proof ended must not leave a pass under the old bytes' key."""
    repo, proof, counter = make_repo()
    beta = os.path.join(repo, ".claude", "tools", "beta.py")
    real_take = harness_tests._take_slots

    def take(exclusive, count=1, split=None):
        write(repo, ".claude/tools/beta.py", "B = 7\n")
        return real_take(exclusive, count)

    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "with open(%r, 'w', newline='\\n') as fh:\n    fh.write('B = 1\\n')\n" % beta))
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
    run(repo, [proof])
    write(repo, ".claude/tools/deep.json", "{\"a\": 1}\n")
    run(repo, [proof])
    check("an edit to data that imported code only names, never reads, keeps the reuse",
          runs(counter) == 1, "runs=%d" % runs(counter))
    write(repo, ".claude/tools/beta.py", "import os\nB = 1\nDEEP = open(os.path.join(os.path.dirname("
          "os.path.abspath(__file__)), 'deep.json')).read()\n")
    run(repo, [proof])
    write(repo, ".claude/tools/deep.json", "{\"a\": 2}\n")
    run(repo, [proof])
    check("an edit to data the imported code reads reruns the proof", runs(counter) == 3, "runs=%d" % runs(counter))


def case_a_probed_absent_path_reruns_when_created():
    repo, proof, counter = make_repo()
    maybe = os.path.join(repo, ".claude", "tools", "maybe.json")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "os.path.exists(%r)\n" % maybe))
    run(repo, [proof])
    run(repo, [proof])
    write(repo, ".claude/tools/maybe.json", "{}\n")
    run(repo, [proof])
    check("creating a path the proof only probed reruns it", runs(counter) == 2, "runs=%d" % runs(counter))


def case_a_listed_directory_gaining_an_entry_reruns():
    repo, proof, counter = make_repo()
    folder = os.path.join(repo, ".claude", "tools")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "os.listdir(%r)\n" % folder))
    run(repo, [proof])
    write(repo, ".claude/tools/gamma.py", "G = 2\n")
    run(repo, [proof])
    check("an edit inside a listed directory that the proof never reads keeps the reuse",
          runs(counter) == 1, "runs=%d" % runs(counter))
    write(repo, ".claude/tools/new_entry.txt", "x\n")
    run(repo, [proof])
    check("a new entry in a directory the proof listed reruns it", runs(counter) == 2, "runs=%d" % runs(counter))


def case_an_ignored_path_is_run_exhaust_and_never_an_input():
    """A close proves in a fresh checkout that its own proofs fill with ignored caches and scratch, so a key
    that held them moved mid-run: a pass recorded after sibling proofs wrote them missed a lookup made before.
    In a close's checkout git's ignored paths are that close's exhaust, not an input; a path git can see still is."""
    repo, proof, counter = make_repo(close=True)
    write(repo, ".gitignore", ".claude/cache/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ignore the cache")
    claude, cache = os.path.join(repo, ".claude"), os.path.join(repo, ".claude", "cache")
    state = write(repo, ".claude/cache/state.txt", "1\n")
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "os.listdir(%r)\nos.listdir(%r)\nos.path.exists(%r)\nopen(%r).read()\n"
                    % (claude, cache, os.path.join(cache, "later.txt"), state)))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "proof lists and reads the cache")
    run(repo, [proof])
    write(repo, ".claude/cache/later.txt", "x\n")
    write(repo, ".claude/cache/state.txt", "2\n")
    run(repo, [proof])
    check("an entry, a probed path or a file's bytes that git ignores never rerun a pass", runs(counter) == 1,
          "runs=%d" % runs(counter))
    shutil.rmtree(cache)
    run(repo, [proof])
    check("an ignored directory the proof listed, here and gone again, keeps the reuse (a proof lists its own "
          "randomly named scratch folder)", runs(counter) == 1, "runs=%d" % runs(counter))
    write(repo, ".claude/cache/state.txt", "3\n")
    write(repo, ".claude/visible.txt", "x\n")
    run(repo, [proof])
    check("an entry git can see in a listed directory still reruns it", runs(counter) == 2, "runs=%d" % runs(counter))


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
        write(repo, ".claude/tests/test_alpha.py", proof_text(
            counter, 0, "os.listdir(%r)\nos.path.exists(%r)\nopen(%r).read()\n"
                        % (cache, os.path.join(cache, "later.txt"), state)))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "proof lists and reads the cache")
        run(repo, [proof])
        run(repo, [proof])
        check("an untouched ignored input keeps the reuse outside a close checkout (%s)" % label,
              runs(counter) == 1, "runs=%d" % runs(counter))
        change(repo)
        run(repo, [proof])
        check("outside a close checkout, %s git ignores reruns the proof that observed it" % label,
              runs(counter) == 2, "runs=%d" % runs(counter))


def case_the_import_system_listing_a_directory_is_not_a_scan():
    """Python's importer lists every sys.path directory it searches; that listing is not the proof
    reading the directory. What the import resolved to is keyed instead: the module it opened, and the
    candidate paths of a name it looked for, so a module appearing where an import failed reruns it."""
    repo, proof, counter = make_repo()
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "try:\n    import optional_mod\nexcept ImportError:\n    pass\n"))
    run(repo, [proof])
    write(repo, ".claude/tools/unrelated_new.py", "U = 1\n")
    run(repo, [proof])
    check("a file added beside an imported module keeps the reuse", runs(counter) == 1, "runs=%d" % runs(counter))
    write(repo, ".claude/tools/optional_mod.py", "O = 1\n")
    run(repo, [proof])
    check("a module appearing where the proof's import failed reruns it", runs(counter) == 2,
          "runs=%d" % runs(counter))


def case_an_unrecorded_child_keys_its_script_statically():
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/child.py", "CHILD_DATA = 'child_data.json'\n")
    write(repo, ".claude/tools/child_data.json", "{}\n")
    child = os.path.join(repo, ".claude", "tools", "child.py")
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "import subprocess\nsubprocess.run([sys.executable, '-I', %r], check=True)\n" % child))
    git(repo, "add", "-A")
    run(repo, [proof])
    run(repo, [proof])
    check("a proof with an isolated Python child is reused while nothing changes", runs(counter) == 1,
          "runs=%d" % runs(counter))
    write(repo, ".claude/tools/child_data.json", "{\"a\": 1}\n")
    run(repo, [proof])
    check("an edit to data the unrecorded child's script names reruns the proof", runs(counter) == 2,
          "runs=%d" % runs(counter))


def case_a_missing_recorder_records_no_observed_key():
    """A checkout that has the runner but not its recorder (a consumer that synced one file) logs no
    events; a manifest built from nothing would reuse the pass forever, so none is recorded."""
    repo, proof, counter = make_repo()
    saved = harness_tests._RECORDER_DIR
    saved_attempt = harness_tests._attempt._RECORDER_DIR
    harness_tests._RECORDER_DIR = harness_tests._attempt._RECORDER_DIR = os.path.join(repo, "no_such_recorder")
    try:
        # Inside a close this proof itself runs under the recorder; an inherited PYTHONPATH would load it, and the
        # recorder and log it names would be the nested run's own.
        run(repo, [proof], {"PYTHONPATH": "", "HARNESS_READ_LOG": "", "HARNESS_READ_RECORDER": ""})
    finally:
        harness_tests._RECORDER_DIR = saved
        harness_tests._attempt._RECORDER_DIR = saved_attempt
    row = (harness_tests._pass_cache(repo, [proof]).variants(os.path.join(repo, ".claude/tests/test_alpha.py")) or [{}])[0]
    check("a run whose recorder never loaded records no observed key", "okey" not in row and row.get("key"),
          "row=%s" % sorted(row))


def case_a_pass_with_no_observed_reach_never_reuses():
    """A Python proof whose run dropped the read recorder recorded no manifest. Its static key is no proof of
    what the pass read, so the next run, with the recorder back, must rerun it."""
    repo, proof, counter = make_repo()
    saved = harness_tests._RECORDER_DIR
    saved_attempt = harness_tests._attempt._RECORDER_DIR
    harness_tests._RECORDER_DIR = harness_tests._attempt._RECORDER_DIR = os.path.join(repo, "no_such_recorder")
    try:
        run(repo, [proof], {"PYTHONPATH": ""})
    finally:
        harness_tests._RECORDER_DIR = saved
        harness_tests._attempt._RECORDER_DIR = saved_attempt
    run(repo, [proof])
    check("a pass that recorded no manifest reruns on the next run", runs(counter) == 2,
          "runs=%d" % runs(counter))


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


def store_file(rel, cases=False):
    """Where the store keeps `rel`'s rows: one file per proof, under the pass store (or the case store)."""
    return harness_tests._store_file(harness_tests.case_store_dir() if cases else harness_tests.pass_store_dir(), rel)


def with_private_store():
    parent = tempfile.mkdtemp(prefix="harnesspassstore_")
    saved = harness_tests.PASSES_PATH
    harness_tests.PASSES_PATH = os.path.join(parent, "passes.json")
    return parent, saved


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


def case_a_quoted_shell_script_spawn_splits_as_its_child_reads_it():
    """Windows hands the spawn audit event the joined command line. A `bash -c` script whose inner quotes
    list2cmdline escaped must split as the child's C runtime reads it, so the repo script it names is keyed
    statically; a quote-blind split marked every such manifest opaque."""
    repo, proof, _counter = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    script = "cd \"%s\" && python3 .claude/tools/gamma.py --say 'it is'" % repo.replace("\\", "/")
    log = tempfile.mkdtemp(prefix="harnesspasscache_reads_")
    spawn = ("import subprocess\ntry:\n    subprocess.run(['no_such_shell_for_the_proof', '-c', %r])\n"
             "except OSError:\n    pass\n" % script)
    subprocess.run([sys.executable, "-c", spawn], cwd=repo, check=True,
                   env=dict(os.environ, HARNESS_READ_LOG=log, PYTHONPATH=harness_tests._RECORDER_DIR))
    events = harness_tests._attempt._read_events(log)
    manifest = cache.manifest(proof, {"events": events, "temp": log})
    check("a recorded spawn of a quoted shell script is captured", any(e[0] == "s" for e in events or []),
          str(events))
    check("a quoted shell script spawn keys the repo script it names, not an opaque manifest",
          isinstance(manifest, dict) and not manifest.get("opaque")
          and manifest.get("static") == [".claude/tools/gamma.py"], str(manifest))


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


def case_a_file_copied_with_copy2_is_a_read():
    """shutil.copy2 and copytree copy through _winapi.CopyFile2 on Windows, which raises no `open` event: a
    proof that copies the tools it runs into a sandbox must still key their bytes."""
    repo, proof, _counter = make_repo()
    root = tempfile.mkdtemp(prefix="harnesspasscache_copy_")
    log = os.path.join(root, "reads")
    gamma = os.path.join(repo, ".claude", "tools", "gamma.py")
    result = _recorded_python("import shutil, sys\nshutil.copy2(%r, %r)\n" % (gamma, os.path.join(root, "gamma.py")),
                              [harness_tests._RECORDER_DIR], log, root)
    manifest = harness_tests._pass_cache(repo, [proof]).manifest(proof, {"events": harness_tests._attempt._read_events(log),
                                                                         "temp": root})
    check("a file copied with shutil.copy2 joins the observed reads",
          isinstance(manifest, dict) and ".claude/tools/gamma.py" in manifest.get("files", []),
          str(manifest) + result.stderr)


def case_a_child_whose_first_sitecustomize_is_another_is_not_recorded():
    """Python imports only the first sitecustomize on its path. A child that lists the recorder behind
    another one never loads it, so its spawn is not recorded and its reads stay unbounded."""
    repo, proof, _counter = make_repo()
    root = tempfile.mkdtemp(prefix="harnesspasscache_first_")
    own, log = os.path.join(root, "own"), os.path.join(root, "reads")
    write(own, "sitecustomize.py", "OWN = 1\n")
    spawn = ("import os, subprocess, sys\nenv = dict(os.environ, PYTHONPATH=os.pathsep.join([%r, %r]))\n"
             "subprocess.run([sys.executable, '-c', 'pass'], env=env)\n" % (own, harness_tests._RECORDER_DIR))
    _recorded_python(spawn, [harness_tests._RECORDER_DIR], log, repo)
    spawns = [e[1] for e in harness_tests._attempt._read_events(log) or [] if e[0] == "s"]
    check("a spawn whose first sitecustomize is not the recorder is not marked recorded",
          spawns and not any(s.get("recorded") for s in spawns), str(spawns)[:300])
    manifest = harness_tests._pass_cache(repo, [proof]).manifest(proof, {"events": harness_tests._attempt._read_events(log),
                                                                         "temp": root})
    check("that unrecorded inline child makes the manifest opaque",
          isinstance(manifest, dict) and manifest.get("opaque") is True, str(manifest))


def case_a_child_behind_the_slot_bootstrap_is_recorded():
    """The slot bootstrap that machine admission puts first on a child's PYTHONPATH starts the next sitecustomize,
    the recorder's, so the child's reads are logged: its spawn is recorded, never an opaque child. A sitecustomize
    that does not start the next one still hides the recorder (the case above)."""
    repo, proof, _counter = make_repo()
    root = tempfile.mkdtemp(prefix="harnesspasscache_boot_")
    log = os.path.join(root, "reads")
    data = write(root, "data.txt", "x\n")
    child = write(root, "child.py", "import sys\nopen(sys.argv[1]).read()\n")
    spawn = ("import os, subprocess, sys\nenv = dict(os.environ, PYTHONPATH=os.pathsep.join([%r, %r]))\n"
             "subprocess.run([sys.executable, %r, %r], env=env, check=True)\n"
             % (os.path.join(_SCRIPTS_DIR, "_slot_bootstrap"), harness_tests._RECORDER_DIR, child, data))
    _recorded_python(spawn, [harness_tests._RECORDER_DIR], log, repo)
    events = harness_tests._attempt._read_events(log)
    spawns = [e[1] for e in events if e[0] == "s"]
    check("a spawn behind the slot bootstrap is marked recorded", spawns and all(s.get("recorded") for s in spawns),
          str(spawns)[:300])
    check("that child's read reached the log",
          any(e[0] == "r" and os.path.normcase(e[1]) == os.path.normcase(os.path.abspath(data)) for e in events))
    manifest = harness_tests._pass_cache(repo, [proof]).manifest(proof, {"events": events, "temp": root})
    check("so its manifest is not opaque", isinstance(manifest, dict) and not manifest.get("opaque"), str(manifest))
    # A sandboxed child audits every open, and a spawn whose path starts with the recorder has nothing to look up:
    # the check reads no file there (the bench sandbox proofs failed their read audit on it).
    plain = os.path.join(root, "plain_reads")
    spawn = ("import os, subprocess, sys\nenv = dict(os.environ, PYTHONPATH=%r)\n"
             "subprocess.run([sys.executable, %r, %r], env=env, check=True)\n"
             % (harness_tests._RECORDER_DIR, child, data))
    _recorded_python(spawn, [harness_tests._RECORDER_DIR], plain, repo)
    # The sitecustomize the recorder shadows runs by design (a system Python ships one), so only the recorder's own counts.
    recorder = os.path.normcase(os.path.abspath(harness_tests._RECORDER_DIR))
    opened = [e[1] for e in harness_tests._attempt._read_events(plain) if e[0] == "r" and e[1].endswith("sitecustomize.py")
              and os.path.normcase(os.path.dirname(os.path.abspath(e[1]))) == recorder]
    check("a spawn whose path starts with the recorder reads no sitecustomize", not opened, str(opened))


def case_a_node_proofs_reads_key_its_manifest():
    """A JS proof's reads and its node children's are recorded by the Node preload, so a repo file it
    reads keys the pass and an edit to an unread file does not rerun it."""
    repo, proof, _counter = make_repo()
    write(repo, ".claude/tools/data.txt", "x\n")
    write(repo, ".claude/tools/child.js", "require('fs').readFileSync(process.argv[2]);\n")
    js = write(repo, ".claude/tests/child_test.js",
               "const fs = require('fs'), cp = require('child_process'), path = require('path');\n"
               "const tools = path.join(__dirname, '..', 'tools');\n"
               "fs.readFileSync(path.join(tools, 'table.json'));\n"
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


def case_a_long_run_prints_its_progress():
    """A close ran 22 minutes printing nothing; a run of many proofs prints N of M as it goes."""
    repo, proof, counter = make_repo()
    second = write(repo, ".claude/tests/test_beta_two.py", proof_text(counter + ".2", 0))
    saved = (harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY)
    harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY = 2, 1
    out = io.StringIO()
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    try:
        with redirect_stdout(out):
            harness_tests._run_selected([proof, second], False, {}, None,
                                        harness_tests.RunOptions(harness_tests._pass_cache(repo, [proof, second])))
    finally:
        harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY = saved
        del os.environ["HARNESS_SLOT_DIR"]
    text = out.getvalue()
    check("a run of many proofs prints how many of them are done", "2 of 2 proofs done" in text, text[-300:])


def case_progress_states_the_time_left_at_recorded_durations():
    """A close's progress line gave only elapsed seconds, so its reader guessed the end from the rate while
    the durations that ordered the run sat unread: each line states the recorded time left and the pace."""
    repo, proof, counter = make_repo()
    second = write(repo, ".claude/tests/test_beta_two.py", proof_text(counter + ".2", 0))
    durations = os.path.join(repo, "durations.json")
    with open(durations, "w", newline="\n") as f:
        json.dump({os.path.basename(proof): 100.0, os.path.basename(second): 50.0}, f)
    saved = (harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY, harness_tests.DURATIONS_PATH)
    harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY, harness_tests.DURATIONS_PATH = 2, 1, durations
    out = io.StringIO()
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    os.environ["HARNESS_TEST_JOBS"] = "1"
    try:
        with redirect_stdout(out):
            harness_tests._run_selected([proof, second], False, {}, None, harness_tests.RunOptions())
    finally:
        harness_tests._PROGRESS_MIN, harness_tests._PROGRESS_EVERY, harness_tests.DURATIONS_PATH = saved
        del os.environ["HARNESS_SLOT_DIR"]
        del os.environ["HARNESS_TEST_JOBS"]
    text = out.getvalue()
    check("the first progress line states the recorded time left for the proofs not yet done",
          re.search(r"1 of 2 proofs done \(0 reused\), \d+s; ~50s left at recorded times, pace x[\d.]+", text), text[-400:])
    check("the last progress line states no time left", re.search(r"2 of 2 proofs done \(0 reused\), \d+s; ~0s left", text),
          text[-400:])


def case_git_on_the_real_repo_keys_its_state():
    repo, proof, counter = make_repo()
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "import subprocess\nsubprocess.run(['git', 'rev-parse', 'HEAD'], cwd=%r, check=True, "
                    "capture_output=True)\n" % repo))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "proof runs git")
    run(repo, [proof])
    run(repo, [proof])
    check("a proof that runs git on its own checkout is reused while the repo is unchanged", runs(counter) == 1,
          "runs=%d" % runs(counter))
    write(repo, "notes.txt", "x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "unrelated")
    run(repo, [proof])
    check("a new commit reruns a proof that runs git on its own checkout", runs(counter) == 2,
          "runs=%d" % runs(counter))


def commit_all(repo, message, paths=("-A",)):
    """Commit `paths` (default: everything, which also commits the files a run left in the repo)."""
    git(repo, "add", *paths)
    git(repo, "commit", "-q", "-m", message)


def git_proof(repo, counter, calls, preface=""):
    """A proof that runs each of `calls` (argument lists) as a child git from the checkout's directory."""
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "import subprocess\n%sfor _c in %r:\n    subprocess.run(['git'] + _c, cwd=%r, capture_output=True)\n"
                    % (preface, calls, repo)))
    commit_all(repo, "proof runs git")


def case_git_that_reads_none_of_the_checkouts_state_keeps_the_reuse():
    """`git init <elsewhere>`, a `rev-parse` that prints where a repository lives and a clone between two other
    repositories read nothing of the checkout's state; a commit must not rerun the proof that ran them."""
    repo, proof, counter = make_repo()
    elsewhere = tempfile.mkdtemp(prefix="harnesspassgit_")
    source, target = os.path.join(elsewhere, "src"), os.path.join(elsewhere, "dst")
    git(elsewhere, "init", "-q", "src")
    git_proof(repo, counter, [["init", "-q", os.path.join(elsewhere, "fresh")], ["rev-parse", "--git-common-dir"],
                              ["rev-parse", "--path-format=absolute", "--git-dir"], ["rev-parse", "--local-env-vars"],
                              ["rev-parse", "--show-superproject-working-tree"], ["clone", "-q", source, target], ["--version"],
                              ["-C", repo, "rev-parse", "--git-dir"], ["-C", source, "status", "--porcelain"]])
    run(repo, [proof])
    write(repo, "notes.txt", "x\n")
    commit_all(repo, "unrelated")
    run(repo, [proof])
    check("a commit keeps the pass of a proof whose git read none of the checkout's state", runs(counter) == 1,
          "runs=%d" % runs(counter))


def case_git_that_copies_or_reads_the_checkout_keys_its_whole_state():
    repo, proof, counter = make_repo()
    elsewhere = tempfile.mkdtemp(prefix="harnesspassgit_")
    git_proof(repo, counter, [["clone", "-q", repo, os.path.join(elsewhere, "copy")]])
    run(repo, [proof])
    write(repo, "notes.txt", "x\n")
    commit_all(repo, "unrelated")
    run(repo, [proof])
    check("a commit reruns a proof that cloned the checkout", runs(counter) == 2, "runs=%d" % runs(counter))
    git_proof(repo, counter, [["status", "--porcelain"]])
    run(repo, [proof])
    write(repo, "more.txt", "x\n")
    commit_all(repo, "unrelated again")
    run(repo, [proof])
    check("a commit reruns a proof that ran an unlisted git command", runs(counter) == 4, "runs=%d" % runs(counter))
    git_proof(repo, counter, [["-c", "core.quotepath=off", "log", "-1"], ["rev-parse", "HEAD"]])
    run(repo, [proof])
    write(repo, "more2.txt", "x\n")
    commit_all(repo, "unrelated third")
    run(repo, [proof])
    check("a rev-parse of anything but a location reruns on a commit", runs(counter) == 6, "runs=%d" % runs(counter))
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "import subprocess\nsubprocess.run('git ls-files & echo x', shell=True, cwd=%r, "
                    "capture_output=True)\n" % repo))
    commit_all(repo, "proof runs git inside a shell line")
    run(repo, [proof])
    write(repo, "more3.txt", "x\n")
    commit_all(repo, "unrelated fourth")
    run(repo, [proof])
    check("a git named inside another program's command line keeps the whole state", runs(counter) == 8,
          "runs=%d" % runs(counter))
    git_proof(repo, counter, [["ls-files", ":(attr:text)"]])
    run(repo, [proof])
    write(repo, "more4.txt", "x\n")
    commit_all(repo, "unrelated fifth")
    run(repo, [proof])
    check("a magic pathspec can read attributes, so ls-files with one keeps the whole state", runs(counter) == 10,
          "runs=%d" % runs(counter))


def case_git_that_lists_files_keys_the_tracked_names_only():
    repo, proof, counter = make_repo()
    git_proof(repo, counter, [["ls-files", "--error-unmatch", "--", ".claude/tools/alpha.py"], ["ls-files", "-z"]])
    run(repo, [proof])
    write(repo, ".claude/tools/gamma.py", "G = 2\n")
    commit_all(repo, "edit a file the proof never read", [".claude/tools/gamma.py"])
    run(repo, [proof])
    check("an edit to a tracked file's bytes keeps the pass of a proof that only listed tracked names",
          runs(counter) == 1, "runs=%d" % runs(counter))
    write(repo, ".claude/tools/added.py", "A = 1\n")
    commit_all(repo, "add a tracked file", [".claude/tools/added.py"])
    run(repo, [proof])
    check("a file added to the tracked set reruns it", runs(counter) == 2, "runs=%d" % runs(counter))
    os.remove(os.path.join(repo, ".claude", "tools", "added.py"))
    commit_all(repo, "remove it", ["-A", ".claude/tools"])
    run(repo, [proof])
    check("a file removed from the tracked set reruns it", runs(counter) == 3, "runs=%d" % runs(counter))
    write(repo, ".claude/tools/staged.py", "S = 1\n")
    git(repo, "add", ".claude/tools/staged.py")
    run(repo, [proof])
    check("a file staged without a commit reruns it", runs(counter) == 4, "runs=%d" % runs(counter))
    git_proof(repo, counter, [["ls-files", "-s"]])
    run(repo, [proof])
    write(repo, ".claude/tools/gamma.py", "G = 3\n")
    commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])
    run(repo, [proof])
    check("ls-files -s prints blob ids, so an edit to tracked bytes reruns it", runs(counter) == 6,
          "runs=%d" % runs(counter))


def case_a_git_directory_existence_probe_is_no_git_read():
    repo, proof, counter = make_repo()
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "for probe in (os.path.exists, os.path.isfile, os.path.isdir, os.path.lexists):\n"
                    "    probe(%r)\n    probe(%r)\n" % (os.path.join(repo, ".git"),
                                                       os.path.join(repo, ".git", "pyvenv.cfg"))))
    commit_all(repo, "proof probes .git")
    run(repo, [proof])
    write(repo, "notes.txt", "x\n")
    commit_all(repo, "unrelated")
    run(repo, [proof])
    check("an existence probe of .git keeps the pass across a commit", runs(counter) == 1, "runs=%d" % runs(counter))
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "open(%r).read()\n" % os.path.join(repo, ".git", "HEAD")))
    commit_all(repo, "proof reads .git/HEAD")
    run(repo, [proof])
    write(repo, "notes2.txt", "x\n")
    commit_all(repo, "unrelated again")
    run(repo, [proof])
    check("a read of a file under .git reruns on a commit", runs(counter) == 3, "runs=%d" % runs(counter))
    marker = os.path.join(repo, ".git", "MERGE_HEAD")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "os.path.exists(%r)\n" % marker))
    commit_all(repo, "proof probes a merge marker")
    run(repo, [proof])
    run(repo, [proof])
    check("a probe of an absent path under .git is reused while it stays absent", runs(counter) == 4,
          "runs=%d" % runs(counter))
    write(repo, ".git/MERGE_HEAD", "0" * 40 + "\n")
    run(repo, [proof])
    check("a probed path under .git appearing reruns the proof", runs(counter) == 5, "runs=%d" % runs(counter))


def child_proof(repo, counter, body):
    """A proof whose extra lines are `body`, committed."""
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "import subprocess\n" + body))
    commit_all(repo, "proof body")


def case_a_git_the_rule_cannot_place_keeps_the_whole_state():
    """Git that reaches this repository by a route other than its own directory (a path argument, another
    folder of the same repository, a nested repository, a path outside the temp root) keeps the whole state."""
    repo, proof, counter = make_repo()
    elsewhere = tempfile.mkdtemp(prefix="harnesspassgit_")
    expected = 0

    def reruns_on_a_commit(label, body, note):
        nonlocal expected
        child_proof(repo, counter, body)
        run(repo, [proof])
        write(repo, ".claude/tools/gamma.py", "G = %r\n" % note)
        commit_all(repo, "edit bytes: " + note, [".claude/tools/gamma.py"])
        run(repo, [proof])
        expected += 2
        check(label, runs(counter) == expected, "runs=%d, expected %d" % (runs(counter), expected))

    reruns_on_a_commit("a clone of the checkout started from another directory reruns on a commit",
                       "subprocess.run(['git', 'clone', '-q', %r, %r], cwd=%r, capture_output=True)\n"
                       % (repo, os.path.join(elsewhere, "copy"), elsewhere), "clone")
    reruns_on_a_commit("a git pointed at the checkout's git dir by an option reruns on a commit",
                       "subprocess.run(['git', '--git-dir=' + %r, 'log', '-1'], cwd=%r, capture_output=True)\n"
                       % (os.path.join(repo, ".git"), elsewhere), "git-dir")
    nested = os.path.join(repo, "nested")
    write(repo, ".gitignore", "nested/\n")
    git(repo, "init", "-q", "nested")
    reruns_on_a_commit("ls-files inside a repository nested in the checkout keeps the whole state",
                       "subprocess.run(['git', '-C', %r, 'ls-files'], cwd=%r, capture_output=True)\n" % (nested, repo),
                       "nested")
    saved = tempfile.tempdir
    try:
        tempfile.tempdir = tempfile.mkdtemp(prefix="harnesspasstemp_")
        reruns_on_a_commit("git init at a path outside the temp root keeps the whole state",
                           "subprocess.run(['git', 'init', '-q', %r], cwd=%r, capture_output=True)\n"
                           % (os.path.join(elsewhere, "fresh"), repo), "init")
    finally:
        tempfile.tempdir = saved


def case_a_linked_worktree_git_dir_touch_keys_the_whole_state():
    """In a linked worktree the git dir lies outside the checkout. A location `rev-parse` keys nothing, so a
    read or a probe of the git dir it printed must key the repository's state itself."""
    repo, _, counter = make_repo()
    tree = os.path.join(tempfile.mkdtemp(prefix="harnesspasstree_"), "tree")
    git(repo, "worktree", "add", "-q", "-b", "linked", tree)
    proof, expected = os.path.join(tree, ".claude", "tests", "test_alpha.py"), 0
    for label, touch in (("a read", "open(os.path.join(_d, 'HEAD')).read()\n"),
                         ("a probe", "os.path.exists(os.path.join(_d, 'MERGE_HEAD'))\n")):
        child_proof(tree, counter, "_d = subprocess.run(['git', 'rev-parse', '--absolute-git-dir'], cwd=%r, "
                                   "capture_output=True, text=True).stdout.strip()\n%s" % (tree, touch))
        run(tree, [proof])
        write(tree, ".claude/tools/gamma.py", "G = %r\n" % label)
        commit_all(tree, "edit bytes", [".claude/tools/gamma.py"])
        run(tree, [proof])
        expected += 2
        check("%s of a linked worktree's git dir reruns on a commit" % label, runs(counter) == expected,
              "runs=%d, expected %d" % (runs(counter), expected))


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
        for label, rel, reruns in (("a file the nested child read", ".claude/tools/gamma.py", True),
                                   ("a file it never read", ".claude/tools/unrelated.py", False)):
            repo, proof, counter = make_repo()
            write(repo, ".claude/tools/unrelated.py", "U = 0\n")
            nested_runner_proof(repo, counter, "import os\nopen(%r).read()\n"
                                % os.path.join(repo, ".claude", "tools", "gamma.py"))

            def edit():
                write(repo, rel, "X = 9\n")
                commit_all(repo, "edit " + rel, [rel])
            before, after = reruns_once_after(repo, proof, counter, edit)
            check("an edit to %s %s the proof whose nested runner ran the child"
                  % (label, "reruns" if reruns else "keeps"),
                  (before, after) == (1, 2 if reruns else 1), "runs %d -> %d" % (before, after))
    finally:
        (harness_tests._RECORDER_DIR, harness_tests._NODE_RECORDER,
         harness_tests._attempt._RECORDER_DIR, harness_tests._attempt._NODE_RECORDER) = saved


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


def case_a_proof_learns_its_own_timeout_without_reading_shared_state():
    """The runner hands a proof the timeout it runs under. A proof that scaled its waits by reading the shared
    durations store read the repository's git dir, which keys its pass on every commit."""
    repo, proof, counter = make_repo()
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "open(%r, 'w').write(os.environ.get('HARNESS_PROOF_TIMEOUT_S', 'unset'))\n"
        % (counter + ".timeout")))
    status = harness_tests.run_proof(proof, timeout=77, root=repo)[0]
    seen = open(counter + ".timeout", encoding="utf-8").read() if os.path.exists(counter + ".timeout") else "absent"
    check("a proof sees the timeout it runs under", status == "pass" and seen == "77", "%s %s" % (status, seen))


def case_git_state_that_moved_during_the_run_records_no_pass():
    """The key's git state is read once, before the proofs start. A proof whose run ends on another state
    observed one the row cannot name, so no row is stored for it."""
    repo, proof, counter = make_repo()
    added = os.path.join(repo, ".claude", "scratch", "late.txt")
    child_proof(repo, counter, "os.makedirs(os.path.dirname(%r), exist_ok=True)\nopen(%r, 'w').close()\n"
                               "subprocess.run(['git', 'add', '-f', %r], cwd=%r, capture_output=True)\n"
                               "subprocess.run(['git', 'ls-files'], cwd=%r, capture_output=True)\n"
                               % (added, added, added, repo, repo))
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


def case_the_key_reads_git_as_the_proof_does():
    """A caller's private index (`GIT_INDEX_FILE`, set while a commit runs) is dropped from a proof's
    environment, so the key's tracked names come from the index the proof read."""
    repo, proof, counter = make_repo()
    git_proof(repo, counter, [["ls-files"]])
    private = os.path.join(tempfile.mkdtemp(prefix="harnesspassindex_"), "index")
    shutil.copyfile(os.path.join(repo, ".git", "index"), private)
    write(repo, "staged_elsewhere.txt", "x\n")
    subprocess.run(["git", "add", "staged_elsewhere.txt"], cwd=repo, check=True, capture_output=True,
                   env=dict(os.environ, GIT_INDEX_FILE=private))
    run(repo, [proof], env={"GIT_INDEX_FILE": private})
    git(repo, "add", "staged_elsewhere.txt")
    run(repo, [proof])
    check("a name staged only in a caller's private index is not in the key; staging it for real reruns",
          runs(counter) == 2, "runs=%d" % runs(counter))


def case_git_state_that_cannot_be_read_leaves_no_reusable_pass():
    repo, proof, counter = make_repo()
    git_proof(repo, counter, [["ls-files"]])
    saved = harness_tests._PassCache._tracked_names
    try:
        harness_tests._PassCache._tracked_names = lambda self, fresh=False: None
        run(repo, [proof])
    finally:
        harness_tests._PassCache._tracked_names = saved
    run(repo, [proof])
    check("a pass recorded while the tracked names could not be read is never reused", runs(counter) == 2,
          "runs=%d" % runs(counter))


def reruns_once_after(repo, proof, counter, mutate):
    """Run `proof` twice (the second must reuse), apply `mutate`, run it again; the run counts before and after.
    The runner's stamp and slots live outside the checkout, as in a real run: inside it they are untracked files."""
    outside = tempfile.mkdtemp(prefix="harnesspassout_")
    harness_tests.STAMP_PATH = os.path.join(outside, "stamp.json")
    env = {"HARNESS_SLOT_DIR": os.path.join(outside, "slots")}
    run(repo, [proof], env)
    run(repo, [proof], env)
    before = runs(counter)
    mutate()
    run(repo, [proof], env)
    return before, runs(counter)


def case_the_commit_id_other_refs_config_and_dirty_content_key_the_repository_state():
    """`_repo_state` held HEAD's tree and the porcelain codes: an amend, a new ref, a config edit and a second edit
    of an already modified file left it equal, and a proof that printed any of them kept a stale pass."""
    repo, proof, counter = make_repo()
    git_proof(repo, counter, [["rev-parse", "HEAD"]])
    before, after = reruns_once_after(repo, proof, counter, lambda: git(repo, "commit", "-q", "--amend", "-m", "reworded"))
    check("a proof that read the commit id reruns when an amend keeps the tree", (before, after) == (1, 2),
          "runs %d -> %d" % (before, after))
    repo, proof, counter = make_repo()
    git_proof(repo, counter, [["branch", "--list"]])
    before, after = reruns_once_after(repo, proof, counter, lambda: git(repo, "branch", "side"))
    check("a proof that listed branches reruns when another ref appears", (before, after) == (1, 2),
          "runs %d -> %d" % (before, after))
    repo, proof, counter = make_repo()
    git_proof(repo, counter, [["config", "--list"]])
    before, after = reruns_once_after(repo, proof, counter, lambda: git(repo, "config", "demo.key", "v"))
    check("a proof that read the config reruns when it changes", (before, after) == (1, 2),
          "runs %d -> %d" % (before, after))
    repo, proof, counter = make_repo()
    git_proof(repo, counter, [["diff"]])
    write(repo, ".claude/tools/gamma.py", "G = 2\n")
    before, after = reruns_once_after(repo, proof, counter, lambda: write(repo, ".claude/tools/gamma.py", "G = 3\n"))
    check("a proof that read the diff reruns on a second edit of an already modified file",
          (before, after) == (1, 2), "runs %d -> %d" % (before, after))


def case_git_config_by_count_names_no_repository():
    """The runner sets core.fscache through GIT_CONFIG_COUNT/KEY/VALUE, and every child inherits them. A count names
    no path, so a child started inside the checkout with them keeps its pass across a commit that changes nothing the
    proof reads."""
    repo, proof, counter = make_repo()
    child_proof(repo, counter, "import sys\nsubprocess.run([sys.executable, '-c', 'pass'], cwd=%r, env=dict(os.environ, "
                "GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='core.fscache', GIT_CONFIG_VALUE_0='true'), "
                "capture_output=True)\n" % repo)

    def commit():
        write(repo, ".claude/tools/gamma.py", "G = 9\n")
        commit_all(repo, "edit an unread file", [".claude/tools/gamma.py"])
    before, after = reruns_once_after(repo, proof, counter, commit)
    check("a child started in the checkout with command-scope git config keeps its pass across an unrelated commit",
          (before, after) == (1, 1), "runs %d -> %d" % (before, after))


def case_git_reached_through_the_environment_or_a_shell_line_keys_the_repository_state():
    """A git whose cwd is elsewhere but whose env names this repository, and a git a shell line starts past an
    operator or by its full path, read the repository: a commit must rerun the proof."""
    gitexe = shutil.which("git")
    elsewhere = tempfile.mkdtemp(prefix="harnesspassgit_")
    bodies = (
        ("GIT_DIR in a child's environment", lambda repo:
         "subprocess.run(['git', 'log', '-1'], cwd=%r, env=dict(os.environ, GIT_DIR=os.path.join(%r, '.git')), "
         "capture_output=True)\n" % (elsewhere, repo)),
        ("GIT_DIR set in os.environ before the spawn", lambda repo:
         "os.environ['GIT_DIR'] = os.path.join(%r, '.git')\n"
         "subprocess.run(['git', 'log', '-1'], cwd=%r, capture_output=True)\n" % (repo, elsewhere)),
        ("a git after an operator with no space", lambda repo:
         "subprocess.run('cd /d ' + %r + '&&git log -1', shell=True, capture_output=True)\n" % repo),
        ("a git started by its full path inside a shell line", lambda repo:
         "subprocess.run('\"' + %r + '\" -C ' + %r + ' log -1', shell=True, capture_output=True)\n" % (gitexe, repo)))
    for label, body in bodies:
        repo, proof, counter = make_repo()
        child_proof(repo, counter, body(repo))

        def commit():
            write(repo, ".claude/tools/gamma.py", "G = 9\n")
            commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])
        before, after = reruns_once_after(repo, proof, counter, commit)
        check("%s keys the whole state" % label, (before, after) == (1, 2), "runs %d -> %d" % (before, after))


def case_a_git_in_another_worktree_of_the_repository_is_never_reused():
    """A sibling worktree is the same repository with its own HEAD and files, none in this checkout's key."""
    repo, proof, counter = make_repo()
    sibling = os.path.join(tempfile.mkdtemp(prefix="harnesspasssibling_"), "sib")
    git(repo, "worktree", "add", "-q", "-b", "sib", sibling)
    child_proof(repo, counter, "subprocess.run(['git', '-C', %r, 'log', '-1'], capture_output=True)\n" % sibling)
    run(repo, [proof])
    run(repo, [proof])
    check("a git run in a sibling worktree reruns: its state is not in this checkout's key", runs(counter) == 2,
          "runs=%d" % runs(counter))


def sibling_worktree(repo, branch="sib"):
    """A second worktree of `repo` on `branch`: its commits move one ref and nothing in `repo`."""
    path = os.path.join(tempfile.mkdtemp(prefix="harnesspasssibling_"), "sib")
    git(repo, "worktree", "add", "-q", "-b", branch, path)
    return path


def commit_during_run(sibling, marker, message="moved"):
    """A thread that lands an empty commit in `sibling` once the check's proof has started (`marker` exists). A
    proof writes the marker first, so the run's git snapshot is already taken when the ref moves."""
    def commit():
        for _ in range(500):
            if os.path.exists(marker):
                break
            time.sleep(0.01)
        subprocess.run(["git", "-C", sibling, "commit", "-q", "--allow-empty", "-m", message],
                       check=True, capture_output=True)
    thread = threading.Thread(target=commit, daemon=True)
    thread.start()
    return thread


def case_a_commit_in_another_worktree_keeps_a_proof_that_reads_only_this_checkout():
    """Proof reuse is the main lever on land time. A proof whose git prints only this checkout's HEAD cannot
    observe another worktree's commit, so the key holds the git parts it can print and not every ref."""
    repo, proof, counter = make_repo()
    sibling = sibling_worktree(repo)
    marker = os.path.join(tempfile.mkdtemp(prefix="harnesspassstarted_"), "started")
    child_proof(repo, counter, "import time\nopen(%r, 'w').close()\n"
                               "subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=%r, capture_output=True)\n"
                               "time.sleep(1.0)\n" % (marker, repo))
    thread = commit_during_run(sibling, marker)
    run(repo, [proof])
    thread.join()
    first = _recorded_row(repo, proof)
    run(repo, [proof])
    check("a commit in another worktree during the run keeps the key of a proof that read only this checkout",
          runs(counter) == 1 and bool(first.get("okey")),
          "runs=%d okey=%s why=%r" % (runs(counter), bool(first.get("okey")), (first.get("why") or "")[:120]))


def case_a_ref_the_proof_read_still_drops_its_key_when_another_worktree_moves_it():
    """The other side of the split: a proof that printed a ref another worktree moves observed a state its row
    cannot name, so its pass is not recorded under either side of the move."""
    repo, proof, counter = make_repo()
    sibling = sibling_worktree(repo, "sib")
    marker = os.path.join(tempfile.mkdtemp(prefix="harnesspassstarted_"), "started")
    child_proof(repo, counter, "import time\nopen(%r, 'w').close()\n"
                               "subprocess.run(['git', 'rev-parse', 'sib'], cwd=%r, capture_output=True)\n"
                               "time.sleep(1.0)\n" % (marker, repo))
    thread = commit_during_run(sibling, marker)
    run(repo, [proof])
    thread.join()
    first = _recorded_row(repo, proof)
    run(repo, [proof])
    check("a ref the proof read still drops its key when another worktree moves it",
          runs(counter) == 2 and not first.get("okey"),
          "runs=%d okey=%s" % (runs(counter), bool(first.get("okey"))))
    check("the row names the part that moved", "refs" in (first.get("why") or ""), "why=%r" % (first.get("why") or ""))


def case_a_commit_in_another_worktree_keeps_the_key_of_a_status_proof():
    """`git status` prints the branch's upstream divergence, not other branches: a sibling worktree's commit moves
    none of what it printed, so the key holds."""
    repo, proof, counter = make_repo()
    sibling = sibling_worktree(repo, "sib")
    git_proof(repo, counter, [["status", "--porcelain"]])
    before, after = reruns_once_after(repo, proof, counter, lambda: subprocess.run(
        ["git", "-C", sibling, "commit", "-q", "--allow-empty", "-m", "moved"],
        check=True, capture_output=True))
    check("a commit in another worktree keeps the key of a status proof",
          (before, after) == (1, 1), "runs %d -> %d" % (before, after))


def case_a_moved_upstream_ref_still_drops_a_status_proofs_key():
    """The other side: status prints the divergence from the ref the branch tracks (`branch.<name>.merge`), so that
    ref's movement must rerun it even when HEAD does not move."""
    repo, proof, counter = make_repo()
    git(repo, "branch", "tracked_base")
    git(repo, "branch", "--set-upstream-to=tracked_base")
    git_proof(repo, counter, [["status", "--porcelain"]])

    def move_upstream():
        sha = subprocess.run(["git", "commit-tree", "HEAD^{tree}", "-m", "base moved"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
        git(repo, "branch", "-f", "tracked_base", sha)
    before, after = reruns_once_after(repo, proof, counter, move_upstream)
    check("a moved upstream ref reruns the status proof that printed its divergence",
          (before, after) == (1, 2), "runs %d -> %d" % (before, after))


def case_the_runners_own_state_under_the_git_dir_keys_its_bytes():
    """The runner keeps its pass store and durations under the repository's git dir. That is the runner's state,
    not git state: a commit elsewhere moves no byte of it, and an edit to it reruns the proof that read it."""
    repo, proof, counter = make_repo()
    shared = write(repo, ".git/harness/fixture_state.json", '{"v": 1}\n')
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "open(%r, encoding='utf-8').read()\n" % shared))
    commit_all(repo, "proof reads the runner's state")
    run(repo, [proof])
    run(repo, [proof])
    check("a proof that reads the runner's state under the git dir is reused while the bytes hold",
          runs(counter) == 1, "runs=%d" % runs(counter))
    git(repo, "branch", "side")
    run(repo, [proof])
    check("a new ref does not move the key of a proof that read only the runner's state",
          runs(counter) == 1, "runs=%d" % runs(counter))
    write(repo, ".git/harness/fixture_state.json", '{"v": 2}\n')
    run(repo, [proof])
    check("an edit to the runner's state the proof read reruns it", runs(counter) == 2, "runs=%d" % runs(counter))


def case_the_runners_own_state_read_from_a_linked_worktree_keys_its_bytes():
    """A linked worktree's git dir lies outside the checkout, where every touch used to be repository state. The
    runner's own folder there is its state: a new ref moves no byte of it."""
    repo, proof, counter = make_repo()
    shared = write(repo, ".git/harness/fixture_state.json", '{"v": 1}\n')
    write(repo, ".claude/tests/test_alpha.py", proof_text(
        counter, 0, "open(%r, encoding='utf-8').read()\n" % shared))
    commit_all(repo, "proof reads the runner's state")
    twin = sibling_worktree(repo, "twin")
    twin_proof = os.path.join(twin, ".claude", "tests", "test_alpha.py")
    run(twin, [twin_proof])
    run(twin, [twin_proof])
    check("a linked worktree reuses a pass over the runner's state while the bytes hold",
          runs(counter) == 1, "runs=%d" % runs(counter))
    git(repo, "branch", "side")
    run(twin, [twin_proof])
    check("a new ref does not move a linked worktree's key over the runner's state",
          runs(counter) == 1, "runs=%d" % runs(counter))


def case_a_global_config_read_keys_no_repository_state():
    """`git config --global` in the checkout reads the user's config, which a proof cannot see (its environment
    pins GIT_CONFIG_GLOBAL to the null device). The repository's config is not a state it observed."""
    repo, proof, counter = make_repo()
    child_proof(repo, counter, "subprocess.run(['git', 'config', '--global', '--list'], cwd=%r, "
                               "capture_output=True)\n" % repo)
    before, after = reruns_once_after(repo, proof, counter, lambda: git(repo, "config", "demo.key", "v"))
    check("a proof that read only the global config keeps its key when the repository config changes",
          (before, after) == (1, 1), "runs %d -> %d" % (before, after))


def case_a_worktree_config_edit_reruns_a_config_reader():
    """With `extensions.worktreeConfig`, a proof's `git config --list` reads the repository's config and the
    worktree's own; the key must hold the same two files a proof reads."""
    repo, proof, counter = make_repo()
    git(repo, "config", "extensions.worktreeConfig", "true")
    git_proof(repo, counter, [["config", "--list"]])
    before, after = reruns_once_after(repo, proof, counter, lambda: subprocess.run(
        ["git", "config", "--worktree", "demo.wt", "v"], cwd=repo, check=True, capture_output=True))
    check("a proof that read the config reruns when the worktree config changes",
          (before, after) == (1, 2), "runs %d -> %d" % (before, after))


def case_git_that_lists_untracked_names_keys_the_names_not_the_whole_state():
    """`ls-files -o --exclude-standard` prints the untracked names; it cannot print a ref or a commit. A commit
    in another worktree moves neither the tracked names nor the untracked ones, and a new untracked file does."""
    repo, proof, counter = make_repo()
    sibling = sibling_worktree(repo, "sib")
    git_proof(repo, counter, [["ls-files", "-zo", "--exclude-standard", "--", ".claude"]])
    outside = tempfile.mkdtemp(prefix="harnesspassout_")
    harness_tests.STAMP_PATH = os.path.join(outside, "stamp.json")
    env = {"HARNESS_SLOT_DIR": os.path.join(outside, "slots")}
    run(repo, [proof], env)
    run(repo, [proof], env)
    before = runs(counter)
    subprocess.run(["git", "-C", sibling, "commit", "-q", "--allow-empty", "-m", "moved"],
                   check=True, capture_output=True)
    run(repo, [proof], env)
    check("a commit in another worktree keeps the pass of a proof that listed untracked names",
          (before, runs(counter)) == (1, 1), "runs %d -> %d" % (before, runs(counter)))
    write(repo, ".claude/tools/fresh.py", "F = 1\n")
    run(repo, [proof], env)
    check("a new untracked file the proof listed reruns it", runs(counter) == 2, "runs=%d" % runs(counter))


def case_a_git_text_merge_on_temp_files_reads_no_checkout_state():
    """`git merge-file` merges the three files its arguments name; run on files under the temp root it reads nothing
    of the checkout, so a commit must keep the pass."""
    repo, proof, counter = make_repo()
    elsewhere = tempfile.mkdtemp(prefix="harnesspassmerge_")
    ours = write(elsewhere, "ours.txt", "a\nb\n")
    base = write(elsewhere, "base.txt", "a\n")
    theirs = write(elsewhere, "theirs.txt", "a\nc\n")
    child_proof(repo, counter, "subprocess.run(['git', 'merge-file', '-p', %r, %r, %r], cwd=%r, "
                               "capture_output=True)\n" % (ours, base, theirs, repo))

    def commit():
        write(repo, ".claude/tools/gamma.py", "G = 9\n")
        commit_all(repo, "edit an unread file", [".claude/tools/gamma.py"])
    before, after = reruns_once_after(repo, proof, counter, commit)
    check("a commit keeps the pass of a proof that merged files under the temp root",
          (before, after) == (1, 1), "runs %d -> %d" % (before, after))


def case_a_git_read_of_a_full_object_id_is_keyed_by_the_object_not_the_worktree():
    """`git show <full id>:<path>` prints bytes the id fixes, so a proof that runs it in a sibling worktree keeps its
    pass across commits while the object exists, and reruns once the object is gone. A ref, an abbreviated id, a
    flag, or any other git on the sibling still never reuses."""
    show, log = ["show", "%(sha)s:pinned.txt"], ["log", "-1"]
    scenarios = (
        ("a full-id show", [show], "commit", (1, 1)),
        ("a full-id cat-file", [["cat-file", "-p", "%(sha)s:pinned.txt"]], "commit", (1, 1)),
        ("a full-id show whose object is pruned", [show], "prune", (1, 2)),
        ("a show of a ref", [["show", "HEAD:pinned.txt"]], "commit", (2, 3)),
        ("a show of an abbreviated id", [["show", "%(short)s:pinned.txt"]], "commit", (2, 3)),
        ("a full-id show with a flag", [["show", "--textconv", "%(sha)s:pinned.txt"]], "commit", (2, 3)),
        ("a full-id show beside a log", [show, log], "commit", (2, 3)))
    for label, calls, mutation, expected in scenarios:
        repo, proof, counter = make_repo()
        sibling = os.path.join(tempfile.mkdtemp(prefix="harnesspasssibling_"), "sib")
        git(repo, "worktree", "add", "-q", "-b", "sib", sibling)
        write(sibling, "pinned.txt", "v1\n")
        commit_all(sibling, "pin", ["pinned.txt"])
        sha = subprocess.run(["git", "commit-tree", "HEAD^{tree}", "-m", "loose"], cwd=sibling, capture_output=True,
                             text=True).stdout.strip()
        names = {"sha": sha, "short": sha[:12]}
        calls = [[a % names for a in call] for call in calls]
        child_proof(repo, counter, "for _c in %r:\n    subprocess.run(['git', '-C', %r] + _c, capture_output=True)\n"
                    % (calls, sibling))

        def mutate():
            if mutation == "prune":
                git(sibling, "prune", "--expire=now")
            else:
                write(repo, ".claude/tools/gamma.py", "G = 9\n")
                commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])
        before, after = reruns_once_after(repo, proof, counter, mutate)
        check("%s: runs %s" % (label, expected), (before, after) == expected,
              "runs %d -> %d, expected %s" % (before, after, expected))




def case_a_shell_script_child_that_runs_git_keys_the_repository_state():
    """A child that is not git and not Python (a `.sh` script) runs git itself: no spawn event names git, so the
    script's own text decides, with the scripts it loads."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    from sidecar_launch import git_bash
    try:
        shell = git_bash()
    except (OSError, RuntimeError):
        return
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/probe.sh", "#!/bin/sh\ngit log -1 > /dev/null\n")
    child_proof(repo, counter, "subprocess.run([%r, %r], cwd=%r, capture_output=True)\n"
                % (shell, os.path.join(repo, ".claude", "tools", "probe.sh"), repo))

    def commit():
        write(repo, ".claude/tools/gamma.py", "G = 9\n")
        commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])
    before, after = reruns_once_after(repo, proof, counter, commit)
    check("a shell script that runs git keys the repository state", (before, after) == (1, 2),
          "runs %d -> %d" % (before, after))
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/plain.sh", "#!/bin/sh\necho hello\n")
    child_proof(repo, counter, "subprocess.run([%r, %r], cwd=%r, capture_output=True)\n"
                % (shell, os.path.join(repo, ".claude", "tools", "plain.sh"), repo))

    def commit_plain():
        write(repo, ".claude/tools/gamma.py", "G = 9\n")
        commit_all(repo, "edit bytes", [".claude/tools/gamma.py"])
    before, after = reruns_once_after(repo, proof, counter, commit_plain)
    check("a shell script that runs no git keeps the pass across a commit", (before, after) == (1, 1),
          "runs %d -> %d" % (before, after))


def case_untracked_files_key_only_the_git_that_can_see_them():
    """`git status` and `ls-files -o` list untracked files, which the state key leaves out; `log` and `status -uno`
    cannot see them, so an untracked file must not rerun their proofs."""
    for calls, reruns, label in ((["status", "--porcelain"], True, "git status"),
                                 (["ls-files", "-o", "--exclude-standard"], True, "git ls-files -o"),
                                 (["status", "--porcelain", "--untracked-files=no"], False, "git status -uno"),
                                 (["log", "-1"], False, "git log")):
        repo, proof, counter = make_repo()
        git_proof(repo, counter, [calls])
        before, after = reruns_once_after(repo, proof, counter, lambda: write(repo, "new_untracked.txt", "x\n"))
        check("an untracked file %s the pass of a proof that ran %s" % ("reruns" if reruns else "keeps", label),
              (before, after) == (1, 2 if reruns else 1), "runs %d -> %d" % (before, after))


def case_a_stat_of_a_checkout_file_keys_its_bytes():
    """`os.stat` and `statSync` observe a file's size, which the key held as a presence only."""
    repo, proof, counter = make_repo()
    target = os.path.join(repo, ".claude", "tools", "gamma.py")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "os.stat(%r).st_size\n" % target))
    commit_all(repo, "proof stats a file")
    before, after = reruns_once_after(repo, proof, counter, lambda: write(repo, ".claude/tools/gamma.py", "G = 100000\n"))
    check("a python proof that stat-ed a file reruns when its size changes", (before, after) == (1, 2),
          "runs %d -> %d" % (before, after))
    if shutil.which("node"):
        repo, _, counter = make_repo()
        script = write(repo, ".claude/tests/test_stat.js",
                       "require('fs').statSync(%s).size;\nrequire('fs').appendFileSync(%s, 'x\\n');\n"
                       % (json.dumps(os.path.join(repo, ".claude", "tools", "gamma.py")), json.dumps(counter)))
        commit_all(repo, "node proof stats a file")
        before, after = reruns_once_after(repo, script, counter, lambda: write(repo, ".claude/tools/gamma.py", "G = 100000\n"))
        check("a node proof that stat-ed a file reruns when its size changes", (before, after) == (1, 2),
              "runs %d -> %d" % (before, after))


def case_a_file_edited_mid_run_changes_the_key_a_later_lookup_takes():
    repo, proof, counter = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    first = cache.key(proof)
    write(repo, ".claude/tools/beta.py", "B = 2\n")
    check("a lookup after an edit within one run sees the edited bytes", cache.key(proof) != first,
          "the key stayed on the digests taken at the run's start")


def case_a_probed_path_changing_kind_reruns():
    repo, proof, counter = make_repo()
    thing = os.path.join(repo, ".claude", "tools", "thing")
    write(repo, ".claude/tools/thing", "x\n")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "os.path.isdir(%r)\n" % thing))
    commit_all(repo, "proof asks whether a path is a directory")
    run(repo, [proof])
    run(repo, [proof])
    os.remove(thing)
    os.makedirs(thing)
    run(repo, [proof])
    check("a probed file that became a directory reruns the proof", runs(counter) == 2, "runs=%d" % runs(counter))


def case_a_tracked_file_an_ignore_pattern_names_is_still_an_input():
    """A tracked file can match an ignore pattern. In a close's checkout it is an input, not exhaust, in
    whatever letter case the platform reports its path."""
    repo, proof, counter = make_repo(close=True)
    write(repo, ".gitignore", "*.ini\n")
    data = write(repo, "Data/Mixed.ini", "1\n")
    git(repo, "add", "-f", "Data/Mixed.ini")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "open(%r).read()\n" % data))
    commit_all(repo, "proof reads a tracked file an ignore pattern names")
    run(repo, [proof])
    run(repo, [proof])
    write(repo, "Data/Mixed.ini", "2\n")
    run(repo, [proof])
    check("an edit to a tracked file an ignore pattern names reruns the proof in a close checkout",
          runs(counter) == 2, "runs=%d" % runs(counter))


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


def case_a_miss_names_the_part_that_moved():
    repo, proof, counter = make_repo()
    folder = os.path.join(repo, ".claude", "tools")
    write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "os.listdir(%r)\n" % folder))
    commit_all(repo, "proof lists tools")
    run(repo, [proof])
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
    other = write(repo, ".claude/tests/test_other.py", proof_text(counter, 0).replace("import alpha\n", "import beta\n"))
    commit_all(repo, "a second proof that reads beta only")
    run(repo, [proof, other])
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


def case_a_miss_is_typed_with_its_reason():
    """Every way a recorded pass stops holding is a typed Miss naming why: a read file moves, a listed
    directory gains an entry, and a run whose recorder never loaded leaves no reusable pass. None is a stale
    hit."""
    repo, proof, counter = make_repo()
    folder = os.path.join(repo, ".claude", "tools")
    parent, saved = with_private_store()
    try:
        from _proof_runner import evidence
        write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, "os.listdir(%r)\n" % folder))
        commit_all(repo, "proof reads the tools folder")
        run(repo, [proof])
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


def case_qualified_and_relative_imports_join_the_reach():
    repo, proof, counter = make_repo()
    write(repo, ".claude/tools/alpha.py", "import beta\nfrom pkg.sub import X\nfrom zeta.zorn import Z\nTABLE = 'table.json'\n")
    write(repo, ".claude/tools/pkg/__init__.py", "INIT = 1\n")
    write(repo, ".claude/tools/pkg/sub.py", "from . import leaf\nfrom .twig import T\nX = 1\n")
    write(repo, ".claude/tools/pkg/leaf.py", "L = 1\n")
    write(repo, ".claude/tools/pkg/twig.py", "T = 1\n")
    write(repo, ".claude/tools/zeta/zorn.py", "Z = 1\n")
    run(repo, [proof])
    for rel in ("pkg/__init__.py", "pkg/leaf.py", "pkg/twig.py", "zeta/zorn.py", "pkg/sub.py"):
        before = runs(counter)
        with open(os.path.join(repo, ".claude", "tools", rel), encoding="utf-8") as fh:
            kept = fh.read()
        write(repo, ".claude/tools/" + rel, kept + "EDITED = 1\n")
        run(repo, [proof])
        check("an edit to %s, reached by a dotted or relative import, reruns the proof" % rel,
              runs(counter) == before + 1, "runs %d -> %d" % (before, runs(counter)))


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
    for name, want in [(n, False) for n in false_edges] + [(n, True) for n in true_edges]:
        before = harness_tests._PassCache(repo).key(proof)
        path = os.path.join(repo, ".claude", "tools", name)
        with open(path, encoding="utf-8") as fh:
            kept = fh.read()
        write(repo, ".claude/tools/" + name, kept + "\n# edited\n")
        moved = harness_tests._PassCache(repo).key(proof) != before
        check("an edit to %s, which reached code %s, %s the static key" % (
            name, "may load" if want else "only mentions in a comment or docstring", "changes" if want else "keeps"),
            moved == want, "reach=%s" % sorted(harness_tests._PassCache(repo).reach(proof)))


# Events a recorded proof logged across two runs of `pytest.main` on a file in its checkout.
PYTEST_EVENTS = [["r", ".pytest_cache/v/cache/lastfailed"], ["r", ".pytest_cache/v/cache/nodeids"],
                 ["p", ".pytest_cache"], ["p", ".pytest_cache/pyvenv.cfg"], ["p", ".pytest_cache/conda-meta/history"],
                 ["p", ".pytest_cache/v/cache"], ["w", "pytest-cache-files-xqagvd1i/README.md"],
                 ["w", "pytest-cache-files-xqagvd1i/.gitignore"], ["w", "pytest-cache-files-xqagvd1i/CACHEDIR.TAG"],
                 ["w", ".pytest_cache/v/cache/nodeids"]]


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


def case_an_unrecorded_childs_data_argument_is_keyed_by_its_bytes():
    """A repo file handed to an unrecorded child is an input by its bytes, whatever runs it. A data file
    runs nothing, so the code its prose names by stem is not the proof's input; the child's script stays
    expanded as code."""
    children = (("child.js", "require('fs').readFileSync(process.argv[2]);\n", "'node'"),
                ("child.py", "import sys\nopen(sys.argv[1]).read()\n", "sys.executable, '-I'"))
    for script, body, launcher in children:
        repo, proof, counter = make_repo()
        write(repo, ".claude/tools/" + script, body)
        write(repo, ".claude/tools/notes.md", "The gamma table is unrelated.\n")
        child = os.path.join(repo, ".claude", "tools", script)
        notes = os.path.join(repo, ".claude", "tools", "notes.md")
        write(repo, ".claude/tests/test_alpha.py", proof_text(counter, 0, (
            "import subprocess\nsubprocess.run([%s, %r, %r], check=True)\n" % (launcher, child, notes))))
        git(repo, "add", "-A")
        run(repo, [proof])
        write(repo, ".claude/tools/gamma.py", "G = 2\n")
        run(repo, [proof])
        check("%s: an edit to code its data argument only names in prose keeps the reuse" % script,
              runs(counter) == 1, "runs=%d" % runs(counter))
        write(repo, ".claude/tools/notes.md", "The gamma table changed.\n")
        run(repo, [proof])
        check("%s: an edit to its data argument reruns the proof" % script, runs(counter) == 2,
              "runs=%d" % runs(counter))
        write(repo, ".claude/tools/" + script, body + "\n")
        before = runs(counter)
        run(repo, [proof])
        check("%s: an edit to the unrecorded child's script reruns the proof" % script, runs(counter) == before + 1,
              "runs %d -> %d" % (before, runs(counter)))


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


def case_a_corpus_scanning_proof_is_keyed_by_the_corpus_it_scans():
    """A proof that lists a tracked directory reads files its text never names; the key covers them."""
    table = harness_tests._catalog.CORPUS
    check("the corpus-scanning table names the proofs that read a whole directory",
          {"test_claudemd_citations.py", "test_guard_text.py"} <= set(table),
          "table=%s" % sorted(table))
    for name, folders in sorted(table.items()):
        repo, _proof, counter = make_repo()
        scanner = write(repo, ".claude/tests/" + name,
                        proof_text(counter, 0, "".join(walk_text(os.path.join(repo, f)) for f in folders)))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "scanner")
        run(repo, [scanner])
        corpus_file = folders[0] + "/unnamed_corpus_member.md"
        write(repo, corpus_file, "first\n")
        run(repo, [scanner])
        write(repo, corpus_file, "second\n")
        run(repo, [scanner])
        check("%s reruns when a file it scans but never names is added or edited" % name,
              runs(counter) == 3, "runs=%d" % runs(counter))


def case_a_dir_scanning_proof_is_reused_only_with_a_corpus_entry():
    """test_hook_state.py is selected by every scoped run and lists .claude/hooks; keyed over that corpus it
    is reused while the hooks are unchanged, and a dir-scanning proof with no corpus entry is never reused."""
    check("test_hook_state.py is keyed over the hooks directory it lists",
          harness_tests._catalog.CORPUS.get("test_hook_state.py") == (".claude/hooks",),
          "table=%s" % sorted(harness_tests._catalog.CORPUS))
    repo, keyed, counter = make_repo()
    bare = write(repo, ".claude/tests/test_bare.py", proof_text(counter, 0))
    scanner = write(repo, ".claude/tests/test_scanner.py",
                    proof_text(counter, 0, walk_text(os.path.join(repo, ".claude", "hooks"))))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "scanners")
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


def _recorded_row(repo, proof):
    return (harness_tests._pass_cache(repo, [proof]).variants(os.path.join(repo, ".claude/tests/test_alpha.py")) or [{}])[0]


def case_a_zero_event_pass_keeps_the_evidence_that_names_it():
    """The close's 92 permanently rerun passes: an observable run whose recorder never loaded records no
    observed key, and the row must carry why it did not, so the next close names the trigger instead of
    rerunning the proof forever in silence."""
    repo, proof, counter = make_repo()
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


class PoolCase:
    """One `case_*` function as a `_case_pool` case: it runs in its own process and fails with the checks it recorded."""

    def __init__(self, name):
        self.__name__ = name

    def __call__(self):
        globals()[self.__name__]()
        if FAILS:
            raise AssertionError("; ".join(FAILS)[:600])


PARTS = 3


def main(part=1):
    """Part `part` of PARTS: the `case_*` functions of this file split round-robin, each part in the pool beside the
    other parts' files. Each case builds its own repo and slot folder, so no case depends on another part."""
    for other in range(2, PARTS + 1):  # a deleted part file would silently drop its cases
        assert os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "test_harness_tests_pass_cache_part%d.py" % other)), "missing part %d" % other
    from _case_pool import run_cases
    return run_cases([PoolCase(name) for name in [n for n in list(globals()) if n.startswith("case_")][part - 1::PARTS]])


if __name__ == "__main__":
    sys.exit(main())
