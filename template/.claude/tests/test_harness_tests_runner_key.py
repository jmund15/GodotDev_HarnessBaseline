"""Re-runnable proof for the launch fingerprint every pass key holds (`harness_tests._launch_fingerprint`).

A pass is keyed on the world its proof ran in: the child launcher's implementation (`_proof_runner/attempt.py`),
the local helper that launcher imports, the recorders it installs, the interpreter, the runner's launch-policy
functions (`run_proof`, `_timeout_for`, `_retry_timeout`, `_launch_options`) and the declared proof policy
(`_PROOF_TIMEOUTS`, `_TIMEOUT_SEC`, `_LOAD_FACTOR`). An edit to any of them reruns every recorded pass; an
edit to scheduling, the close, the pass store or reporting keeps the pass. No checkout path or session id
joins the key, so a pass recorded in one checkout holds in a compatible one. A key-version change costs one
cold generation, and a missing or corrupt row is a miss.

    python3 .claude/tests/test_harness_tests_runner_key.py
"""
import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

os.environ.pop("HARNESS_REUSE_PASSES", None)
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402

FAILS = []
TOTAL = 0
RUNNER = os.path.join(_SCRIPTS_DIR, "harness_tests.py")
ATTEMPT = os.path.join(_SCRIPTS_DIR, "_proof_runner", "attempt.py")
with open(RUNNER, encoding="utf-8") as _fh:
    SOURCE = _fh.read()
with open(ATTEMPT, encoding="utf-8") as _fh:
    ATTEMPT_SOURCE = _fh.read()
TMP = tempfile.mkdtemp(prefix="harnessrunnerkey_")


def check(name, ok, detail=""):
    global TOTAL
    TOTAL += 1
    print("%-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILS.append("%s: %s" % (name, detail))


def fingerprint():
    fn = getattr(harness_tests, "_launch_fingerprint", None)
    return fn() if fn else None


_ABSENT = object()


class patched:
    """Module attributes held for the block; the values are restored even when the block raises."""

    def __init__(self, **names):
        self.names = names
        self.saved = {}

    def __enter__(self):
        for name, value in self.names.items():
            self.saved[name] = getattr(harness_tests, name, _ABSENT)
            setattr(harness_tests, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is _ABSENT:
                delattr(harness_tests, name)
            else:
                setattr(harness_tests, name, value)


def write(text, name="edited.py"):
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def top(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node
    raise LookupError(name)


def insert_into(source, func, line):
    """`line` as the first statement of top-level `func`, after its docstring."""
    node = top(source, func)
    first = node.body[1] if ast.get_docstring(node) is not None and len(node.body) > 1 else node.body[0]
    lines = source.splitlines(keepends=True)
    indent = lines[first.lineno - 1][:first.col_offset]
    lines.insert(first.lineno - 1, indent + line + "\n")
    return "".join(lines)


def set_value(source, name, expr):
    node = top(source, name)
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = ["%s = %s\n" % (name, expr)]
    return "".join(lines)


def append_def(source, text):
    """`text` as a new top-level definition, before the main guard."""
    guard = ast.parse(source).body[-1]
    lines = source.splitlines(keepends=True)
    lines.insert(guard.lineno - 1, text + "\n\n")
    return "".join(lines)


def edit_docstring(source, func):
    node = top(source, func)
    doc = node.body[0]
    lines = source.splitlines(keepends=True)
    lines[doc.lineno - 1] = lines[doc.lineno - 1].replace('"""', '"""Edited. ', 1)
    return "".join(lines)


def case_the_real_runner_key_covers_the_launch_world():
    first, again = fingerprint(), fingerprint()
    check("the real runner exposes a stable launch fingerprint", first is not None and first == again, (first, again))
    check("the key is the launch world, not the runner file or the launcher alone",
          first is not None and first not in (hashlib.sha256(SOURCE.encode("utf-8")).hexdigest(),
                                              hashlib.sha256(ATTEMPT_SOURCE.encode("utf-8")).hexdigest()), first)
    other = os.path.join(tempfile.gettempdir(), "other_checkout")
    os.environ["CLAUDE_CODE_SESSION_ID"] = "session-that-must-not-join-a-key"
    try:
        with patched(STAMP_PATH=os.path.join(other, "stamp.json"), REPO_ROOT=other):
            check("another checkout's paths and a session id keep the key", first is not None and fingerprint() == first)
    finally:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)


def case_an_edit_the_launch_world_depends_on_changes_the_key():
    base = fingerprint()
    launcher = {name: insert_into(ATTEMPT_SOURCE, name, "_edited = 0") for name in
                ("run_proof", "_run_child", "_runner_cmds", "_runner_cmd", "_pytest_cases", "_land_path",
                 "_core_root", "_nested_log", "_case_receipts", "_read_events", "_admitted_run")}
    launcher["a pattern the launcher reads"] = set_value(ATTEMPT_SOURCE, "_PLATFORM_SKIP_RE", "re.compile('x')")
    launcher["the environment it declares"] = set_value(
        ATTEMPT_SOURCE, "_RUNNER_CLAUDE_DECLARED", '("CLAUDE_PROJECT_DIR", "CLAUDE_EDITED")')
    for label, source in launcher.items():
        with patched(_ATTEMPT_SOURCE=write(source)):
            check("an edit to the child launcher's %s changes the key" % label, base is not None and fingerprint() != base)
    helper = os.path.join(TMP, "helpers")
    os.makedirs(helper, exist_ok=True)
    with open(os.path.join(helper, "_bounded_run.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(open(os.path.join(_SCRIPTS_DIR, "..", "hooks", "_bounded_run.py"), encoding="utf-8").read()
                 + "\n_EDITED = 1\n")
    with patched(_LAUNCH_HELPER_ROOTS=(helper,)):
        check("an edit to the helper the launcher imports changes the key", base is not None and fingerprint() != base)
    recorder = os.path.join(TMP, "recorder")
    shutil.copytree(harness_tests._RECORDER_DIR, recorder, ignore=shutil.ignore_patterns("__pycache__"),
                    dirs_exist_ok=True)
    with open(os.path.join(recorder, "node_recorder.js"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n// edited recorder\n")
    with patched(_RECORDER_DIR=recorder, _NODE_RECORDER=os.path.join(recorder, "node_recorder.js")):
        check("an edit the recorder records changes the key", base is not None and fingerprint() != base)
    for func in ("run_proof", "_timeout_for", "_retry_timeout", "_launch_options"):
        with patched(_RUNNER_SOURCE=write(insert_into(SOURCE, func, "_edited = 0"))):
            check("an edit to the launch policy's %s changes the key" % func, base is not None and fingerprint() != base)
    for name, value in (("_TIMEOUT_SEC", harness_tests._TIMEOUT_SEC + 1),
                        ("_LOAD_FACTOR", harness_tests._LOAD_FACTOR + 1)):
        with patched(**{name: value}):
            check("a change to the declared policy %s changes the key" % name, base is not None and fingerprint() != base)
    with patched(_PROOF_TIMEOUTS=dict(harness_tests._PROOF_TIMEOUTS, **{"test_edited.py": 5})):
        check("a change to the proof timeout table changes the key", base is not None and fingerprint() != base)
    with patched(_interpreter=lambda: ["another-interpreter", "3.0.0 fake"]):
        check("a changed interpreter changes the key", base is not None and fingerprint() != base)
    with patched(_ATTEMPT_SOURCE=os.path.join(TMP, "absent_attempt.py")):
        check("an unreadable launcher is no key at all", fingerprint() is None)


def case_an_edit_elsewhere_keeps_the_pass():
    base = fingerprint()
    edits = {
        "the close (run_close)": insert_into(SOURCE, "run_close", "_edited = 0"),
        "the budget line (_budget_line)": insert_into(SOURCE, "_budget_line", "_edited = 0"),
        "the report (_detail_excerpt)": insert_into(SOURCE, "_detail_excerpt", "_edited = 0"),
        "the pass cache class (_PassCache)": insert_into(SOURCE, "_PassCache", "_edited = 0"),
        "slot admission (_launch)": insert_into(SOURCE, "_launch", "_edited = 0"),
        "the scheduling loop (_run_selected)": insert_into(SOURCE, "_run_selected", "_edited = 0"),
        "a new import no launch code uses": SOURCE.replace("\nimport argparse\n", "\nimport argparse\nimport calendar\n", 1),
        "a new unrelated function": append_def(SOURCE, "def _unrelated():\n    return 1\n"),
        "a comment in run_proof": insert_into(SOURCE, "run_proof", "# a comment"),
        "run_proof's docstring": edit_docstring(SOURCE, "run_proof"),
        "_timeout_for's docstring": edit_docstring(SOURCE, "_timeout_for"),
    }
    for label, edited in edits.items():
        with patched(_RUNNER_SOURCE=write(edited)):
            check("an edit to %s keeps the key" % label, base is not None and fingerprint() == base)


def git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True, capture_output=True)


def write_repo(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def runs(counter):
    if not os.path.exists(counter):
        return 0
    with open(counter, encoding="utf-8") as fh:
        return len(fh.read().splitlines())


def repo_with_proofs(tmp, names=("test_unit.py", "test_shell.sh")):
    """A temp checkout holding proofs that append one line per run to `runs.txt`."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo, exist_ok=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    write_repo(repo, ".claude/settings.json", "{}\n")
    counter = os.path.join(tmp, "runs.txt")
    paths = [write_repo(repo, ".claude/tests/" + name,
                        "import sys\nwith open(%r, 'a') as fh:\n    fh.write('ran\\n')\nsys.exit(0)\n" % counter)
             if name.endswith(".py") else
             write_repo(repo, ".claude/tests/" + name, "#!/bin/sh\necho ran >> '%s'\n" % counter.replace("\\", "/"))
             for name in names]
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo, counter, paths


class run_fixture:
    """Run proofs through `_run_selected` with a private pass store, as a scoped run does."""

    def __init__(self, repo):
        self.repo = repo
        self.saved = {name: getattr(harness_tests, name) for name in
                      ("EXCLUDED", "SERIAL", "STAMP_PATH", "PASSES_PATH", "_RUNNER_SOURCE")}
        harness_tests.EXCLUDED, harness_tests.SERIAL = {}, {}
        harness_tests.STAMP_PATH = os.path.join(os.path.dirname(repo), "stamp.json")
        harness_tests.PASSES_PATH = os.path.join(os.path.dirname(repo), "proof_passes.json")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(harness_tests, name, value)

    def __call__(self, proofs, source=None):
        if source is not None:
            harness_tests._RUNNER_SOURCE = write(source, "runner_%d.py" % runs(os.path.join(
                os.path.dirname(self.repo), "runs.txt")))
        statuses = {}
        cache = harness_tests._pass_cache(self.repo, proofs)
        with redirect_stdout(io.StringIO()):
            harness_tests._run_selected(proofs, False, statuses, None, harness_tests.RunOptions(cache))
        return statuses, cache


def case_a_recorded_pass_follows_the_launch_key():
    """End to end through `_run_selected` and the store: the runner file the keys read is swapped for an edited
    copy, as a commit to the runner swaps it."""
    tmp = tempfile.mkdtemp(prefix="harnessrunnerkeyrun_")
    repo, counter, proofs = repo_with_proofs(tmp)
    saved = dict(os.environ)
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(tmp, "slots")
    os.environ.pop("HARNESS_NO_PASS_CACHE", None)
    try:
        with run_fixture(repo) as run:
            run(proofs)
            first = runs(counter)
            statuses, _ = run(proofs, insert_into(SOURCE, "run_close", "_edited = 0"))
            check("a runner edit outside the launch keeps both recorded passes",
                  first == 2 and runs(counter) == 2 and set(statuses.values()) == {"pass"},
                  "runs %d -> %d, %s" % (first, runs(counter), statuses))
            statuses, cache = run(proofs, insert_into(SOURCE, "run_proof", "_edited = 0"))
            missed = cache.misses.get(".claude/tests/test_unit.py", [])
            check("a runner edit to the launch reruns both proofs, and the miss names the launch part",
                  runs(counter) == 4 and "runner" in missed, "runs %d, missed %s" % (runs(counter), missed))
    finally:
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)


def case_the_first_run_after_a_key_version_is_one_cold_miss():
    """A key-version change retires every row the earlier version recorded: the next run reruns, the one after
    reuses."""
    tmp = tempfile.mkdtemp(prefix="harnessrunnerkeyversion_")
    repo, counter, proofs = repo_with_proofs(tmp, ("test_unit.py",))
    saved = dict(os.environ)
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(tmp, "slots")
    os.environ.pop("HARNESS_NO_PASS_CACHE", None)
    version = harness_tests._KEY_SCHEME
    try:
        with run_fixture(repo) as run:
            harness_tests._KEY_SCHEME = version - 1
            run(proofs)
            harness_tests._KEY_SCHEME = version
            run(proofs)
            cold = runs(counter)
            run(proofs)
            check("the first run after a key version is one cold miss, the next a hit",
                  cold == 2 and runs(counter) == 2, "runs after the version change: %d -> %d" % (cold, runs(counter)))
    finally:
        harness_tests._KEY_SCHEME = version
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)


def case_a_missing_or_corrupt_row_is_a_miss():
    tmp = tempfile.mkdtemp(prefix="harnessrunnerkeycorrupt_")
    repo, counter, proofs = repo_with_proofs(tmp, ("test_unit.py",))
    saved = dict(os.environ)
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(tmp, "slots")
    os.environ.pop("HARNESS_NO_PASS_CACHE", None)
    try:
        with run_fixture(repo) as run:
            run(proofs)
            cache = harness_tests._PassCache(repo)
            rel = ".claude/tests/test_unit.py"
            store = harness_tests._store_file(harness_tests.pass_store_dir(), rel)
            with open(store, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("{not json")
            check("a corrupt row is a miss", not cache.hit(os.path.join(repo, rel), cache.key(os.path.join(repo, rel))))
            run(proofs)
            os.remove(store)
            run(proofs)
            check("a missing row is a miss", runs(counter) == 3)
    finally:
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for case in (case_the_real_runner_key_covers_the_launch_world,
                 case_an_edit_the_launch_world_depends_on_changes_the_key,
                 case_an_edit_elsewhere_keeps_the_pass,
                 case_a_recorded_pass_follows_the_launch_key,
                 case_the_first_run_after_a_key_version_is_one_cold_miss,
                 case_a_missing_or_corrupt_row_is_a_miss):
        case()
    shutil.rmtree(TMP, ignore_errors=True)
    print("%d of %d checks passed" % (TOTAL - len(FAILS), TOTAL))
    if FAILS:
        print("FAILURES:\n  " + "\n  ".join(FAILS))
        sys.exit(1)
