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


def make_repo():
    """tools/alpha.py imports beta and reads table.json; tools/gamma.py is unrelated. The proof
    test_alpha.py imports alpha and counts its own runs in a file outside the repo."""
    repo = tempfile.mkdtemp(prefix="harnesspasscache_")
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
    saved = harness_tests._DIR_SCANNING
    harness_tests._DIR_SCANNING = saved + ("test_uncorpused_scan.py",)
    try:
        run(repo, [scanner])
        run(repo, [scanner])
    finally:
        harness_tests._DIR_SCANNING = saved
    check("a dir-scanning proof with no corpus entry is never reused", runs(scanner_counter) == 2,
          "runs=%d" % runs(scanner_counter))
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    try:
        with redirect_stdout(io.StringIO()):
            harness_tests.run_all(repo)
    finally:
        del os.environ["HARNESS_SLOT_DIR"]
    check("the full battery ignores the cache", runs(counter) == 3, "runs=%d" % runs(counter))


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

    def take(exclusive):
        waiting.set()
        return real_take(exclusive)

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

    def recording(path, timeout=None, root=None):
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


def case_a_retry_owns_the_machine():
    """A pool failure's retry takes every machine slot: a retry under the same peer load cannot tell
    contention from a red, and its pass is reported as a pass alone."""
    repo, _proof, _counter = make_repo()
    slow = write(repo, ".claude/tests/test_slow.py", "import sys\nsys.exit(0)\n")
    taken, results = [], [("fail", 1.0, "TIMEOUT after 1s"), ("pass", 0.1, "")]
    real_run, real_take = harness_tests.run_proof, harness_tests._take_slots

    def take(exclusive):
        taken.append(exclusive)
        return []

    harness_tests.run_proof = lambda path, timeout=None, root=None: results.pop(0)
    harness_tests._take_slots = take
    try:
        run(repo, [slow], {"HARNESS_NO_PASS_CACHE": "1"})
    finally:
        harness_tests.run_proof = real_run
        harness_tests._take_slots = real_take
    check("a pool proof takes one slot and its retry takes every slot", taken == [False, True],
          "exclusive per attempt=%s" % taken)


def case_a_timeout_whose_retry_the_deadline_prevents_is_skipped():
    """A pool timeout under load says nothing about the proof; when the deadline then refuses its exclusive
    retry, the proof is `skipped` (the commit guard's rerun-in-background answer), never a red `fail`."""
    repo, _proof, _counter = make_repo()
    slow = write(repo, ".claude/tests/test_loaded.py", "import sys\nsys.exit(0)\n")
    calls = []
    real_run = harness_tests.run_proof

    def time_out_then_expire(path, timeout=None, root=None):
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

    def take(exclusive):
        write(repo, ".claude/tools/beta.py", "B = 7\n")
        return real_take(exclusive)

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
    harness_tests._RECORDER_DIR = os.path.join(repo, "no_such_recorder")
    try:
        # Inside a close this proof itself runs under the recorder; an inherited PYTHONPATH would load it.
        run(repo, [proof], {"PYTHONPATH": ""})
    finally:
        harness_tests._RECORDER_DIR = saved
    row = (harness_tests._pass_cache(repo, [proof]).variants(os.path.join(repo, ".claude/tests/test_alpha.py")) or [{}])[0]
    check("a run whose recorder never loaded records no observed key", "okey" not in row and row.get("key"),
          "row=%s" % sorted(row))


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
    events = harness_tests._read_events(log)
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
    reads = [e[1] for e in harness_tests._read_events(log) or [] if e[0] == "r"]
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
    manifest = harness_tests._pass_cache(repo, [proof]).manifest(proof, {"events": harness_tests._read_events(log),
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
    spawns = [e[1] for e in harness_tests._read_events(log) or [] if e[0] == "s"]
    check("a spawn whose first sitecustomize is not the recorder is not marked recorded",
          spawns and not any(s.get("recorded") for s in spawns), str(spawns)[:300])
    manifest = harness_tests._pass_cache(repo, [proof]).manifest(proof, {"events": harness_tests._read_events(log),
                                                                         "temp": root})
    check("that unrecorded inline child makes the manifest opaque",
          isinstance(manifest, dict) and manifest.get("opaque") is True, str(manifest))


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
    harness_tests._NODE_RECORDER = recorder
    try:
        status = harness_tests.run_proof(js, root=repo, reads_sink=sink)[0]
    finally:
        harness_tests._NODE_RECORDER = saved
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
    table = getattr(harness_tests, "_CORPUS_SCANNING", {})
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
          getattr(harness_tests, "_CORPUS_SCANNING", {}).get("test_hook_state.py") == (".claude/hooks",),
          "table=%s" % sorted(getattr(harness_tests, "_CORPUS_SCANNING", {})))
    repo, keyed, counter = make_repo()
    bare = write(repo, ".claude/tests/test_bare.py", proof_text(counter, 0))
    scanner = write(repo, ".claude/tests/test_scanner.py",
                    proof_text(counter, 0, walk_text(os.path.join(repo, ".claude", "hooks"))))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "scanners")
    saved = (harness_tests._DIR_SCANNING, dict(harness_tests._CORPUS_SCANNING))
    harness_tests._DIR_SCANNING = ("test_bare.py", "test_scanner.py")
    harness_tests._CORPUS_SCANNING["test_scanner.py"] = (".claude/hooks",)
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
        harness_tests._DIR_SCANNING, harness_tests._CORPUS_SCANNING = saved[0], saved[1]


def case_a_head_comparison_takes_a_slot_and_honours_the_deadline():
    repo, proof, _counter = make_repo()
    head = harness_tests._git(repo, ["rev-parse", "HEAD"]).strip()
    taken, started = [], []
    real_run, real_take = harness_tests.run_proof, harness_tests._take_slots

    def take(exclusive):
        taken.append(exclusive)
        return None

    harness_tests.run_proof = lambda path, timeout=None, root=None: (started.append(path), ("fail", 0.0, "FAIL x"))[1]
    harness_tests._take_slots = take
    try:
        reds = harness_tests._reds_at(repo, head, [proof])
    finally:
        harness_tests.run_proof = real_run
        harness_tests._take_slots = real_take
    check("a HEAD comparison waits for a machine slot, and a deadline that passes there starts nothing",
          taken == [False] and not started and not reds, "slot requests=%s started=%s reds=%s" % (taken, started, reds))


def case_a_runner_of_another_width_is_refused_while_work_holds_a_slot():
    repo, _proof, _counter = make_repo()
    slots = os.path.join(repo, "slots")
    saved = dict(os.environ)

    def take(width, exclusive):
        os.environ.update({"HARNESS_SLOT_DIR": slots, "HARNESS_MACHINE_SLOTS": str(width)})
        os.environ.pop("HARNESS_DEADLINE", None)
        try:
            return harness_tests._take_slots(exclusive)
        except harness_tests.SlotStoreError as exc:
            return exc

    try:
        serial = take(1, True)
        refused = take(2, False)
        for handle in serial + (refused if isinstance(refused, list) else []):
            _file_lock.release(handle)
        free = take(2, False)
        same = take(2, False)
        for handle in (free if isinstance(free, list) else []) + (same if isinstance(same, list) else []):
            _file_lock.release(handle)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check("a runner whose slot width differs from the one holding a slot is refused instead of overlapping it",
          isinstance(refused, harness_tests.SlotStoreError), "second runner got %r" % (refused,))
    check("a different width is accepted once no slot is held, and the same width always is",
          isinstance(free, list) and isinstance(same, list), "free=%r same=%r" % (free, same))


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


def main():
    case_a_timeout_whose_retry_the_deadline_prevents_is_skipped()
    case_a_capped_proof_retries_at_its_cap()
    case_reuse_and_invalidation()
    case_failure_is_never_reused()
    case_opt_outs()
    case_deadline_skips_and_blocks_the_stamp()
    case_slots()
    case_a_proof_never_inherits_the_callers_session_identity()
    case_a_waiting_run_reuses_a_peer_pass()
    case_a_pass_records_only_the_bytes_it_ran_on()
    case_a_retry_owns_the_machine()
    case_a_pass_never_lands_under_a_key_taken_before_the_slot_wait()
    case_data_named_by_transitively_loaded_code_joins_the_reach()
    case_a_probed_absent_path_reruns_when_created()
    case_a_listed_directory_gaining_an_entry_reruns()
    case_the_import_system_listing_a_directory_is_not_a_scan()
    case_an_unrecorded_child_keys_its_script_statically()
    case_git_on_the_real_repo_keys_its_state()
    case_a_missing_recorder_records_no_observed_key()
    case_two_checkouts_keep_their_own_passes()
    case_a_close_checkout_pass_outlives_its_removed_checkout()
    case_an_unparseable_spawn_marks_the_manifest_opaque()
    case_a_quoted_shell_script_spawn_splits_as_its_child_reads_it()
    case_a_non_python_child_running_a_temp_copy_is_opaque()
    case_the_recorder_runs_the_sitecustomize_it_shadows()
    case_a_file_copied_with_copy2_is_a_read()
    case_a_child_whose_first_sitecustomize_is_another_is_not_recorded()
    case_a_node_proofs_reads_key_its_manifest()
    case_a_long_run_prints_its_progress()
    case_progress_states_the_time_left_at_recorded_durations()
    case_qualified_and_relative_imports_join_the_reach()
    case_a_comment_or_docstring_mention_is_not_a_load()
    case_an_unrecorded_childs_data_argument_is_keyed_by_its_bytes()
    case_pytests_own_cache_leaves_the_reads_observable()
    case_a_node_recorder_in_a_spaced_path_still_records()
    case_a_deadline_skipped_proof_exits_3_not_2()
    case_an_unusable_slot_store_is_reported_not_waited_on()
    case_a_corpus_scanning_proof_is_keyed_by_the_corpus_it_scans()
    case_a_dir_scanning_proof_is_reused_only_with_a_corpus_entry()
    case_a_head_comparison_takes_a_slot_and_honours_the_deadline()
    case_a_runner_of_another_width_is_refused_while_work_holds_a_slot()
    case_a_runner_with_no_width_set_adopts_the_recorded_width()
    print("\n%d/%d cases pass" % (TOTAL - len(FAILS), TOTAL))
    for f in FAILS:
        print("  FAIL " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
