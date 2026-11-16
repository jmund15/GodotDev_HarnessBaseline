"""Re-runnable proof for the runner part of the pass cache's keys (`harness_tests._launch_digest`).

A runner edit reruns a recorded pass only when it can change that proof's verdict: an edit to the code that fixes
the process a proof runs as, its timeout and the reading of its exit (`_LAUNCH_ROOTS` and every module name they
reach), to data that code reads, or to a function that rebinds one of those names or changes the environment a
proof inherits. An edit to scheduling, the close, the pass store or reporting keeps the pass. A runner that uses its
namespace dynamically is keyed whole. Each edit is made to the real runner's source.

    python3 .claude/tests/test_harness_tests_runner_key.py
"""
import ast
import hashlib
import io
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
with open(RUNNER, encoding="utf-8") as _fh:
    SOURCE = _fh.read()
NAMESPACE = vars(harness_tests)


def check(name, ok, detail=""):
    global TOTAL
    TOTAL += 1
    print("%-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILS.append("%s: %s" % (name, detail))


def digest(source, namespace=None):
    fn = getattr(harness_tests, "_launch_digest", None)
    return fn(source, NAMESPACE if namespace is None else namespace) if fn else None


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


def case_the_real_runner_is_keyed_by_its_launch():
    first, again = digest(SOURCE), digest(SOURCE)
    check("the launch digest of the real runner is stable", first is not None and first == again, (first, again))
    check("the real runner is keyed by its launch, not its whole file",
          first is not None and first != hashlib.sha256(SOURCE.encode("utf-8")).hexdigest(), first)
    code = harness_tests._launch_code(SOURCE) if first else None
    tied = [name for name in (code[1] if code else ())
            if os.path.normcase(harness_tests.REPO_ROOT) in os.path.normcase(str(NAMESPACE.get(name)))]
    check("no value the key reads names this checkout, so a pass holds in every checkout", code and not tied, tied)
    elsewhere = dict(NAMESPACE, STAMP_PATH=os.path.join(tempfile.gettempdir(), "other_checkout", "stamp.json"),
                     REPO_ROOT=os.path.join(tempfile.gettempdir(), "other_checkout"))
    check("another checkout's paths keep the runner key", first is not None and digest(SOURCE, elsewhere) == first)


def case_an_edit_a_proof_run_depends_on_reruns_it():
    base = digest(SOURCE)
    edits = {
        "run_proof's body": insert_into(SOURCE, "run_proof", "_edited = 0"),
        "a helper run_proof calls (_land_path)": insert_into(SOURCE, "_land_path", "_edited = 0"),
        "the child launcher (_run_child)": insert_into(SOURCE, "_run_child", "_edited = 0"),
        "the timeout a proof gets (_timeout_for)": insert_into(SOURCE, "_timeout_for", "_edited = 0"),
        "an alone retry's timeout (_retry_timeout)": insert_into(SOURCE, "_retry_timeout", "_edited = 0"),
        "the keywords a proof launches with (_launch_options)": insert_into(SOURCE, "_launch_options", "_edited = 0"),
        "the deadline cap and keyword hand-off (_admitted_run)": insert_into(SOURCE, "_admitted_run", "_edited = 0"),
        "an import a launch function uses (subprocess)": SOURCE.replace("\nimport subprocess\n",
                                                                         "\nimport subprocess as subprocess\n", 1),
        "a constant run_proof reads (_TIMEOUT_SEC)": set_value(SOURCE, "_TIMEOUT_SEC", "121"),
        "a pattern defined far from run_proof (_PLATFORM_SKIP_RE)":
            set_value(SOURCE, "_PLATFORM_SKIP_RE", "re.compile('x')"),
        "a new function rebinding a launch name": append_def(
            SOURCE, "def _swap():\n    global _land_path\n    _land_path = str\n"),
        "a new function setting the inherited environment": append_def(
            SOURCE, "def _env():\n    os.environ['HARNESS_EDITED'] = '1'\n"),
        "a new function changing the environment through an alias": append_def(
            SOURCE, "def _env():\n    env = os.environ\n    env['HARNESS_EDITED'] = '1'\n"),
        "a new dynamic namespace write": append_def(
            SOURCE, "def _dyn():\n    globals()['_TIMEOUT_SEC'] = 1\n"),
    }
    for label, edited in edits.items():
        after = digest(edited)
        check("an edit to %s changes the runner key" % label, base is not None and after != base, after)
    changed = dict(NAMESPACE, _PROOF_TIMEOUTS={"test_x.py": 5})
    check("a change in the timeout table a proof runs under changes the runner key",
          base is not None and digest(SOURCE, changed) != base)


def case_an_edit_elsewhere_keeps_the_pass():
    base = digest(SOURCE)
    edits = {
        "the close (run_close)": insert_into(SOURCE, "run_close", "_edited = 0"),
        "the budget line (_budget_line)": insert_into(SOURCE, "_budget_line", "_edited = 0"),
        "the known-red note (_known_red_note)": insert_into(SOURCE, "_known_red_note", "_edited = 0"),
        "the pass cache class (_PassCache)": insert_into(SOURCE, "_PassCache", "_edited = 0"),
        "slot admission (_launch)": insert_into(SOURCE, "_launch", "_edited = 0"),
        "the scheduling loop (_run_selected)": insert_into(SOURCE, "_run_selected", "_edited = 0"),
        "a new import no launch code uses": SOURCE.replace("\nimport argparse\n", "\nimport argparse\nimport calendar\n", 1),
        "a new unrelated function": append_def(SOURCE, "def _unrelated():\n    return 1\n"),
        "a comment in run_proof": insert_into(SOURCE, "run_proof", "# a comment"),
        "run_proof's docstring": edit_docstring(SOURCE, "run_proof"),
    }
    for label, edited in edits.items():
        after = digest(edited)
        check("an edit to %s keeps the runner key" % label, base is not None and after == base, after)


def git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True, capture_output=True)


def write(repo, rel, text):
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


def case_a_recorded_pass_follows_the_launch_key():
    """End to end through `_run_selected` and the store: the runner file the keys read is swapped for an edited
    copy, as a commit to the runner swaps it."""
    if not hasattr(harness_tests, "_RUNNER_SOURCE"):
        check("the runner keys read the runner source from _RUNNER_SOURCE", False, "absent")
        return
    tmp = tempfile.mkdtemp(prefix="harnessrunnerkey_")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    write(repo, ".claude/settings.json", "{}\n")
    counter = os.path.join(tmp, "runs.txt")
    proofs = [write(repo, ".claude/tests/test_unit.py",
                    "import sys\nwith open(%r, 'a') as fh:\n    fh.write('ran\\n')\nsys.exit(0)\n" % counter),
              write(repo, ".claude/tests/test_shell.sh", "#!/bin/sh\necho ran >> '%s'\n" % counter.replace("\\", "/"))]
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    saved = {name: getattr(harness_tests, name) for name in ("EXCLUDED", "SERIAL", "STAMP_PATH", "_RUNNER_SOURCE")}
    env = dict(os.environ)
    harness_tests.EXCLUDED, harness_tests.SERIAL = {}, {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(tmp, "slots")
    os.environ.pop("HARNESS_NO_PASS_CACHE", None)

    def run(source=None):
        if source is not None:
            path = os.path.join(tmp, "runner_%d.py" % runs(counter))
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(source)
            harness_tests._RUNNER_SOURCE = path
        statuses = {}
        cache = harness_tests._pass_cache(repo, proofs)
        with redirect_stdout(io.StringIO()):
            harness_tests._run_selected(proofs, False, statuses, None, harness_tests.RunOptions(cache))
        return statuses, cache
    try:
        harness_tests._RUNNER_SOURCE = RUNNER
        run()
        first = runs(counter)
        statuses, _ = run(insert_into(SOURCE, "run_close", "_edited = 0"))
        check("a runner edit outside the launch keeps both recorded passes (observed and static key)",
              first == 2 and runs(counter) == 2 and set(statuses.values()) == {"pass"},
              "runs %d -> %d, %s" % (first, runs(counter), statuses))
        statuses, cache = run(insert_into(SOURCE, "run_proof", "_edited = 0"))
        missed = cache.misses.get(".claude/tests/test_unit.py", [])
        check("a runner edit to the launch reruns both proofs, and the miss names the runner",
              runs(counter) == 4 and "runner" in missed, "runs %d, missed %s" % (runs(counter), missed))
    finally:
        for name, value in saved.items():
            setattr(harness_tests, name, value)
        os.environ.clear()
        os.environ.update(env)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for case in (case_the_real_runner_is_keyed_by_its_launch, case_an_edit_a_proof_run_depends_on_reruns_it,
                 case_an_edit_elsewhere_keeps_the_pass, case_a_recorded_pass_follows_the_launch_key):
        case()
    print("%d of %d checks passed" % (TOTAL - len(FAILS), TOTAL))
    if FAILS:
        print("FAILURES:\n  " + "\n  ".join(FAILS))
        sys.exit(1)
