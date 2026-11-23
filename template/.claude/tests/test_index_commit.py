#!/usr/bin/env python3
r"""Proof for tools/index_commit.py, the private-index commit cycle.

A throwaway git repo under TMP holds the whole fixture: the tool and the two modules it
imports are copied in. Nothing here touches the real checkout's index.

Cases:
- a new gitignored file under `.claude/tests/` is force-added;
- a tracked path deleted from the working tree is staged as a deletion;
- a peer file staged in the shared index is absent from the private index and is still staged
  in the shared index after resync;
- resync after a real commit leaves no staged change for the committed paths and prints RESYNC OK;
- resync before any commit refuses with a named error and a non-zero exit;
- `--blob` stages the given content, not the working file, and leaves the working file alone;
- the tool's source carries no `commit` argv in any list/tuple literal (planted control first),
  and the printed command is the GIT_INDEX_FILE commit line;
- usage errors: an unknown path and a path outside the repo each exit
  non-zero with a named error and stage nothing in the shared index;
- a submodule path with a checkout ahead of its gitlink stages as a gitlink at the checkout's
  HEAD and deletes nothing; a submodule with no checkout is refused and names the pathspec route.
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import types
from unittest import mock
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
TOOL = REPO / ".claude" / "tools" / "index_commit.py"
HOOKS = REPO / ".claude" / "hooks"

HEAD_ROWS = {"keep_a.py", "keep_b.py"}

TEMPLATE = []


def check(label, ok, detail=""):
    assert ok, "%s: %s" % (label, detail)
    return ok


def git_env(home):
    env = dict(os.environ)
    env.pop("GIT_INDEX_FILE", None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "proof"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "proof@example.invalid"
    return env


def git(root, args, env, check_rc=True, **kw):
    proc = subprocess.run(["git"] + args, cwd=str(root), env=env, capture_output=True,
                          encoding="utf-8", timeout=120, **kw)
    if check_rc and proc.returncode != 0:
        raise AssertionError("git %s failed: %s%s" % (args, proc.stdout, proc.stderr))
    return proc


def build_template(path):
    """A repo: the tool plus its imports, HEAD_ROWS, and a peer file."""
    root = Path(path)
    (root / ".claude" / "tools").mkdir(parents=True)
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".claude" / "tests").mkdir(parents=True)
    shutil.copy2(TOOL, root / ".claude" / "tools" / "index_commit.py")
    shutil.copy2(TOOL.parent / "cli_usage.py", root / ".claude" / "tools" / "cli_usage.py")
    for module in sorted(p.name for p in HOOKS.glob("_*.py")):  # the tool imports a chain of helpers
        shutil.copy2(HOOKS / module, root / ".claude" / "hooks" / module)
    (root / ".gitignore").write_text(".claude/tests/\n", encoding="utf-8", newline="\n")
    for relpath in sorted(HEAD_ROWS) + ["gone.py", "peer.py"]:
        (root / relpath).write_text("head %s\n" % relpath, encoding="utf-8", newline="\n")
    env = git_env(root)
    git(root, ["init", "-q", "-b", "main"], env)
    git(root, ["add", "-A"], env)
    git(root, ["commit", "-q", "-m", "seed"], env)


@pytest.fixture(scope="module", autouse=True)
def _template(tmp_path_factory):
    path = tmp_path_factory.mktemp("index_commit_tpl") / "tpl"
    build_template(path)
    TEMPLATE[:] = [path]
    yield
    TEMPLATE.clear()


def scene(tmp, name):
    """A copy of the module's template repo, named for its case: (root, env)."""
    root = Path(tmp) / name
    shutil.copytree(TEMPLATE[0], root)
    return root, git_env(root)


def _load_tool():
    spec = importlib.util.spec_from_file_location("index_commit_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(TOOL.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TOOL.parent))
    return module


MODULE = _load_tool()


def tool(root, env, args, extra_env=None):
    """The tool's `main(args)` in process, from `root` with `env` planted; a result shaped like a process's."""
    out, err = io.StringIO(), io.StringIO()
    run_env = dict(env)
    run_env.update(extra_env or {})
    here = os.getcwd()
    os.chdir(root)
    try:
        with mock.patch.dict(os.environ, run_env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = MODULE.main(args) or 0
            except SystemExit as exc:  # argparse refusals exit 2
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        os.chdir(here)
    return types.SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())


def idx_env(env, root, name):
    run_env = dict(env)
    run_env["GIT_INDEX_FILE"] = str(root / ".git" / ("index-" + name))
    return run_env


def blob_at(root, env, relpath):
    """The blob the given index (env's GIT_INDEX_FILE) holds for a path, or None."""
    listing = git(root, ["ls-files", "-s", "--", relpath], env).stdout.split()
    if not listing:
        return None
    return git(root, ["cat-file", "-p", listing[1]], env).stdout




def names(root, env, cached):
    args = ["diff", "--cached", "--name-only"] if cached else ["diff", "--name-only"]
    return sorted(x for x in git(root, args, env).stdout.splitlines() if x.strip())


def commit_index(root, env, name, message):
    return subprocess.run(["git", "commit", "-q", "-m", message], cwd=str(root),
                          env=idx_env(env, root, name), capture_output=True, encoding="utf-8",
                          timeout=120)


def test_new_gitignored_file(tmp_path):
    root, env = scene(tmp_path, "newfile")
    (root / ".claude" / "tests" / "new_proof.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "newfile", "--", ".claude/tests/new_proof.py"])
    check("new gitignored path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    staged = names(root, idx_env(env, root, "newfile"), cached=True)
    check("new gitignored path is force-added to the private index",
          staged == [".claude/tests/new_proof.py"], staged)
    check("stage prints the GIT_INDEX_FILE commit line",
          "GIT_INDEX_FILE=.git/index-newfile git commit" in proc.stdout, proc.stdout)
    commit = commit_index(root, env, "newfile", "new proof")
    check("new-file scene: the private-index commit lands", commit.returncode == 0, commit.stderr)
    res = tool(root, env, ["resync", "--name", "newfile"])
    check("new-file scene: resync exits 0", res.returncode == 0, res.stdout + res.stderr)
    check("new-file scene: nothing is left staged in the shared index",
          names(root, env, cached=True) == [], names(root, env, cached=True))


def test_deleted_path(tmp_path):
    root, env = scene(tmp_path, "deleted")
    (root / "gone.py").unlink()
    proc = tool(root, env, ["stage", "--name", "deleted", "--", "gone.py"])
    check("deleted path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "deleted")
    tracked = git(root, ["ls-files", "--", "gone.py"], private).stdout.strip()
    check("deleted path is staged as a deletion in the private index", tracked == "", tracked)






def test_peer_and_resync(tmp_path):
    root, env = scene(tmp_path, "resync")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    (root / "peer.py").write_text("peer staged\n", encoding="utf-8", newline="\n")
    git(root, ["add", "--", "peer.py"], env)

    proc = tool(root, env, ["stage", "--name", "resync", "--", "keep_a.py"])
    check("peer scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "resync")
    check("the peer's staged change is absent from the private index",
          blob_at(root, private, "peer.py") == "head peer.py\n"
          and git(root, ["show", "HEAD:peer.py"], env).stdout == "head peer.py\n",
          repr(blob_at(root, private, "peer.py")))
    check("the peer's file is still staged in the shared index before resync",
          "peer.py" in names(root, env, cached=True), names(root, env, cached=True))

    commit = commit_index(root, env, "resync", "mine")
    check("the private-index commit lands", commit.returncode == 0, commit.stdout + commit.stderr)

    res = tool(root, env, ["resync", "--name", "resync"])
    check("resync exits 0 after a real commit", res.returncode == 0, res.stdout + res.stderr)
    check("resync prints RESYNC OK", "RESYNC OK" in res.stdout, res.stdout)
    check("the committed path has no staged change left in the shared index",
          "keep_a.py" not in names(root, env, cached=True), names(root, env, cached=True))
    check("the peer's file is still staged in the shared index after resync",
          "peer.py" in names(root, env, cached=True), names(root, env, cached=True))
    check("the peer's working copy is untouched",
          (root / "peer.py").read_text(encoding="utf-8") == "peer staged\n")
    check("resync removes the private index and its sidecar",
          not (root / ".git" / "index-resync").exists()
          and not (root / ".git" / "index-resync.paths.json").exists())




def test_peer_staged_same_path(tmp_path):
    """The peer staged its own content of a path this commit carries through --blob."""
    root, env = scene(tmp_path, "samepath")
    (root / "keep_a.py").write_text("peer version\n", encoding="utf-8", newline="\n")
    git(root, ["add", "--", "keep_a.py"], env)
    mine = Path(tmp_path) / "samepath_mine.txt"
    mine.write_text("my hunk only\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "samepath", "--blob", "keep_a.py=%s" % mine])
    check("same-path scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    commit = commit_index(root, env, "samepath", "mine")
    check("same-path scene: the private-index commit lands", commit.returncode == 0, commit.stderr)
    res = tool(root, env, ["resync", "--name", "samepath"])
    check("a committed path left peer-staged is reported, not a mismatch: resync exits 0",
          res.returncode == 0 and "left (peer-staged" in res.stdout and "MISMATCH" not in res.stdout,
          res.stdout + res.stderr)
    check("the peer's staged content of the committed path survives resync",
          blob_at(root, env, "keep_a.py") == "peer version\n", repr(blob_at(root, env, "keep_a.py")))


def test_head_moved(tmp_path):
    """A commit lands between stage and the private-index commit, which then reverts it."""
    root, env = scene(tmp_path, "moved")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "moved", "--", "keep_a.py"])
    check("moved scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    (root / "keep_b.py").write_text("a peer's commit\n", encoding="utf-8", newline="\n")
    git(root, ["add", "--", "keep_b.py"], env)
    git(root, ["commit", "-q", "-m", "peer"], env)
    commit_index(root, env, "moved", "mine")
    res = tool(root, env, ["resync", "--name", "moved"])
    out = res.stdout + res.stderr
    check("resync after HEAD moved exits non-zero and names the reverted path",
          res.returncode != 0 and "keep_b.py" in out and "revert" in out, out)


def test_resync_after_later_commit(tmp_path):
    """The private-index commit landed, then a later commit changed the same path before resync."""
    root, env = scene(tmp_path, "later")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "later", "--", "keep_a.py"])
    check("later scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    commit_index(root, env, "later", "mine")
    (root / "keep_a.py").write_text("later fix\n", encoding="utf-8", newline="\n")
    git(root, ["commit", "-q", "-m", "later", "--", "keep_a.py"], env)
    res = tool(root, env, ["resync", "--name", "later"])
    out = res.stdout + res.stderr
    check("resync after a later commit on the same path exits 0, not 'did not land'",
          res.returncode == 0 and "did not land" not in out, out)
    check("the later commit's path has no staged change left", "keep_a.py" not in names(root, env, cached=True),
          names(root, env, cached=True))
    check("later scene: resync removes the private index and its sidecar",
          not (root / ".git" / "index-later").exists()
          and not (root / ".git" / "index-later.paths.json").exists())


RACE_DRIVER = """
import subprocess, sys
sys.argv = ["index_commit.py", "stage", "--name", "race", "--", "keep_a.py"]
sys.path.insert(0, ".claude/tools")
import index_commit
real = index_commit._git
moved = []
def racing(root, args, index=None, **kw):
    if args and args[0] == "read-tree" and not moved:
        moved.append(1)
        open("raced.py", "w").write("a peer's commit\\n")
        subprocess.run(["git", "add", "--", "raced.py"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "peer"], check=True)
    return real(root, args, index=index, **kw)
index_commit._git = racing
sys.exit(index_commit.main(sys.argv[1:]))
"""


def test_seeded_from_recorded_base(tmp_path):
    """A commit landing inside `stage` must not change the tree the private index starts from: the
    index is the recorded base plus the staged paths, so the guard's base check describes it exactly."""
    root, env = scene(tmp_path, "race")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    run_env = dict(env)
    proc = subprocess.run([sys.executable, "-c", RACE_DRIVER], cwd=str(root), env=run_env,
                          capture_output=True, encoding="utf-8", timeout=180)
    check("race scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    sidecar = root / ".git" / "index-race.paths.json"
    base = json.loads(sidecar.read_text(encoding="utf-8")).get("base_head")
    check("the stage record sits at <index>.paths.json and names the base",
          bool(base) and base != git(root, ["rev-parse", "HEAD"], env).stdout.strip(), str(base))
    listed = git(root, ["ls-files", "--", "raced.py"], idx_env(env, root, "race")).stdout.strip()
    check("the private index holds the recorded base's tree, not the HEAD that moved during stage",
          listed == "", "private index lists %r" % listed)




def test_resync_before_commit(tmp_path):
    root, env = scene(tmp_path, "early")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "early", "--", "keep_a.py"])
    check("early scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    res = tool(root, env, ["resync", "--name", "early"])
    check("resync before any commit exits non-zero", res.returncode != 0, res.stdout)
    check("resync before any commit names the unlanded commit",
          "did not land" in (res.stdout + res.stderr), res.stdout + res.stderr)
    check("resync before any commit stages nothing in the shared index",
          "keep_a.py" not in names(root, env, cached=True), names(root, env, cached=True))


def test_blob(tmp_path):
    root, env = scene(tmp_path, "blob")
    (root / "keep_a.py").write_text("WORKTREE CONTENT\n", encoding="utf-8", newline="\n")
    (root / "mine.blob").write_text("BLOB CONTENT\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "blob", "--blob", "keep_a.py=mine.blob", "--", "keep_a.py"])
    check("--blob: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "blob")
    shown = git(root, ["show", ":keep_a.py"], private).stdout
    check("--blob stages the given content, not the working file",
          shown == "BLOB CONTENT\n", repr(shown))
    check("--blob leaves the working file alone",
          (root / "keep_a.py").read_text(encoding="utf-8") == "WORKTREE CONTENT\n")
    check("--blob leaves the working file as an unstaged modification",
          "keep_a.py" in names(root, env, cached=False) and "keep_a.py" not in names(root, env, cached=True))


def test_blob_keeps_head_mode(tmp_path):
    root, env = scene(tmp_path, "blobmode")
    git(root, ["update-index", "--chmod=+x", "--", "keep_a.py"], env)
    git(root, ["commit", "-q", "-m", "keep_a.py is executable"], env)
    (root / "mine.blob").write_text("BLOB CONTENT\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "blobmode", "--blob", "keep_a.py=mine.blob", "--", "keep_a.py"])
    check("--blob on a 100755 path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    listing = git(root, ["ls-files", "-s", "--", "keep_a.py"], idx_env(env, root, "blobmode")).stdout.split()
    check("--blob keeps HEAD's file mode 100755", listing[:1] == ["100755"], listing)


def test_no_commit_argv(tmp_path):
    control = Path(tmp_path) / "planted_commit_argv.py"
    control.write_text('import subprocess\nsubprocess.run(["git", "commit", "-m", "x"])\n',
                       encoding="utf-8", newline="\n")

    def commit_argv_literals(source):
        tree = ast.parse(source)
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)):
                if any(isinstance(e, ast.Constant) and e.value == "commit" for e in node.elts):
                    hits.append(getattr(node, "lineno", 0))
        return hits

    check("control: the detector finds a planted `commit` argv",
          commit_argv_literals(control.read_text(encoding="utf-8")) != [])
    source = TOOL.read_text(encoding="utf-8")
    check("the tool holds no `commit` argv in any list/tuple literal",
          commit_argv_literals(source) == [], commit_argv_literals(source))
    check("the tool never passes shell=True",
          not re.search(r"shell\s*=\s*True", source))
    check("the tool prints the GIT_INDEX_FILE commit command as text",
          "git commit -F" in source)


def test_usage_errors(tmp_path):
    root, env = scene(tmp_path, "usage")
    proc = tool(root, env, ["stage", "--name", "u", "--", "nope.py"])
    check("an unknown path exits non-zero with a named error",
          proc.returncode != 0 and "unknown path" in (proc.stdout + proc.stderr),
          proc.stdout + proc.stderr)
    check("a refused stage states the shape it accepts and one valid call",
          "accepts:" in proc.stderr and "example:" in proc.stderr, proc.stderr)
    proc = tool(root, env, ["stage"])
    check("a stage with no --name states the shape it accepts and one valid call",
          proc.returncode == 2 and "accepts:" in proc.stderr and "example:" in proc.stderr, proc.stderr)
    proc = tool(root, env, ["stage", "--name", "u", "--", "../outside.py"])
    check("a path outside the repo exits non-zero with a named error",
          proc.returncode != 0 and "outside the repo" in (proc.stdout + proc.stderr),
          proc.stdout + proc.stderr)
    check("a refused stage discards the private index",
          not (root / ".git" / "index-u").exists())
    check("a refused stage stages nothing in the shared index",
          names(root, env, cached=True) == [],
          names(root, env, cached=True))


def test_entries_keep_modes_and_peer(tmp_path):
    root, env = scene(tmp_path, 'entries')
    sha = git(root, ['rev-parse', 'HEAD:keep_a.py'], env).stdout.strip()
    git(root, ['update-index', '--chmod=+x', '--', 'peer.py'], env)
    before_peer = git(root, ['ls-files', '-s', '--', 'peer.py'], env).stdout
    for mode, rel in [('100755', '.claude/tests/executable.py'), ('120000', '.claude/tests/link.py')]:
        (root / rel).write_text('working content differs\n', encoding='utf-8')
    proc = tool(root, env, ['stage', '--name', 'entries',
                           '--entry', '.claude/tests/executable.py=100755,' + sha,
                           '--entry', '.claude/tests/link.py=120000,' + sha,
                           '--entry', 'keep_a.py=100755,' + sha])
    check('--entry stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode:
        return
    private = idx_env(env, root, 'entries')
    for rel, mode in [('.claude/tests/executable.py', '100755'), ('.claude/tests/link.py', '120000'), ('keep_a.py', '100755')]:
        listing = git(root, ['ls-files', '-s', '--', rel], private).stdout.split()
        check('--entry keeps mode/blob for ' + rel, listing[:2] == [mode, sha], listing)
    commit = commit_index(root, env, 'entries', 'entries')
    check('--entry commit lands', commit.returncode == 0, commit.stderr)
    res = tool(root, env, ['resync', '--name', 'entries'])
    check('--entry resync succeeds', res.returncode == 0, res.stdout + res.stderr)
    check('unselected peer chmod survives resync', git(root, ['ls-files', '-s', '--', 'peer.py'], env).stdout == before_peer)


def test_resync_keeps_same_blob_peer_mode(tmp_path):
    root, env = scene(tmp_path, 'peermode')
    sha = git(root, ['rev-parse', 'HEAD:keep_a.py'], env).stdout.strip()
    proc = tool(root, env, ['stage', '--name', 'peermode', '--entry', 'keep_a.py=120000,' + sha])
    check('peer-mode stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode:
        return
    early = tool(root, env, ['resync', '--name', 'peermode'])
    check('same-blob changed mode is still an unlanded commit', early.returncode != 0 and 'did not land' in early.stderr, early.stdout + early.stderr)
    git(root, ['update-index', '--chmod=+x', '--', 'keep_a.py'], env)
    peer_before = git(root, ['ls-files', '-s', '--', 'keep_a.py'], env).stdout
    commit = commit_index(root, env, 'peermode', 'modes')
    check('peer-mode commit lands', commit.returncode == 0, commit.stderr)
    res = tool(root, env, ['resync', '--name', 'peermode'])
    check('peer-mode resync reports peer staging', res.returncode == 0 and 'left (peer-staged' in res.stdout, res.stdout + res.stderr)
    check('same-blob peer mode survives resync', git(root, ['ls-files', '-s', '--', 'keep_a.py'], env).stdout == peer_before)


def test_resync_nochange_entry(tmp_path):
    root, env = scene(tmp_path, 'nochange')
    (root / 'peer.py').write_text('previous committed change\n', encoding='utf-8')
    git(root, ['commit', '-q', '-m', 'previous', '--', 'peer.py'], env)
    sha = git(root, ['rev-parse', 'HEAD:keep_a.py'], env).stdout.strip()
    proc = tool(root, env, ['stage', '--name', 'nochange', '--entry', 'keep_a.py=100644,' + sha])
    check('unchanged entry stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
    res = tool(root, env, ['resync', '--name', 'nochange'])
    check('unchanged entry resync does not mistake HEAD parent for an intervening commit', res.returncode == 0, res.stdout + res.stderr)


FAILED_STAGE_DRIVER = '''
import json, sys
from pathlib import Path
sys.path.insert(0, '.claude/tools')
import index_commit
real = index_commit._index_entries

def peer_then_fail(root, paths, index=None):
    real(root, paths, index=index)
    raise index_commit.IndexCommitError('planted later stage failure')

index_commit._index_entries = peer_then_fail
sys.exit(index_commit.main(['stage', '--name', 'failedstage', '--', '.claude/tests/failedstage.py']))
'''


def test_failed_stage_cleans_only_private_artifacts(tmp_path):
    root, env = scene(tmp_path, 'failedstage')
    (root / '.claude/tests/failedstage.py').write_text('x = 1\n', encoding='utf-8')
    run_env = dict(env)
    proc = subprocess.run([sys.executable, '-c', FAILED_STAGE_DRIVER], cwd=root, env=run_env,
                          capture_output=True, text=True)
    check('later stage failure is reported', proc.returncode != 0 and 'planted later stage failure' in proc.stderr, proc.stdout + proc.stderr)
    check('failed stage cleans only private artifacts', not (root / '.git/index-failedstage').exists() and not (root / '.git/index-failedstage.paths.json').exists())
    check('failed stage leaves shared index untouched', names(root, env, cached=True) == [])


RESYNC_DRIVER = '''
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, '.claude/tools')
import index_commit
mode = sys.argv[1]
real_entries = index_commit._index_entries
real_git = index_commit._git
real_replace = os.replace
fired = []

def entries(root, paths, index=None):
    result = real_entries(root, paths, index=index)
    if mode == 'peer' and not fired:
        fired.append(True)
        Path('keep_a.py').write_text('peer at boundary\\n', encoding='utf-8')
        proc = subprocess.run(['git', 'add', '--', 'keep_a.py'], capture_output=True, text=True)
        print('PEER_RC', proc.returncode)
        print(proc.stderr)
    return result

def run_git(root, args, index=None, **kw):
    result = real_git(root, args, index=index, **kw)
    if mode == 'reset-failure' and args[0] == 'reset':
        raise index_commit.IndexCommitError('planted failure after reset before publish')
    return result

def replace(src, dst):
    if mode == 'publish-failure' and Path(dst).name == 'index':
        raise OSError('planted publication failure')
    return real_replace(src, dst)

index_commit._index_entries = entries
index_commit._git = run_git
index_commit.os.replace = replace
index_commit.INDEX_LOCK_TIMEOUT = 0.05
sys.exit(index_commit.main(['resync', '--name', 'transaction']))
'''


@pytest.mark.parametrize('mode', ['peer', 'reset-failure', 'publish-failure', 'timeout'])
def test_resync_transaction(tmp_path, mode):
    root, env = scene(tmp_path, 'transaction-' + mode)
    (root / 'keep_a.py').write_text('mine\n', encoding='utf-8')
    proc = tool(root, env, ['stage', '--name', 'transaction', '--', 'keep_a.py'])
    check(mode + ': stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
    commit = commit_index(root, env, 'transaction', 'mine')
    check(mode + ': private commit lands', commit.returncode == 0, commit.stderr)
    index = root / '.git/index'
    lock = root / '.git/index.lock'
    before = index.read_bytes()
    if mode == 'timeout':
        lock.write_bytes(b'peer-owned lock')
    proc = subprocess.run([sys.executable, '-c', RESYNC_DRIVER, mode], cwd=root, env=env,
                          capture_output=True, text=True, timeout=30)
    detail = proc.stdout + proc.stderr
    if mode == 'peer':
        check('peer add at old boundary fails with Git index.lock error',
              'PEER_RC 128' in detail and 'index.lock' in detail and 'File exists' in detail, detail)
        check('resync succeeds without silently clearing an accepted peer add',
              proc.returncode == 0 and 'RESYNC OK' in detail
              and blob_at(root, env, 'keep_a.py') == 'mine\n', detail)
    else:
        check(mode + ': failure is reported without traceback',
              proc.returncode != 0 and 'error:' in detail and 'Traceback' not in detail
              and ('index.lock' if mode == 'timeout' else 'planted') in detail, detail)
        check(mode + ': shared index remains byte-identical', index.read_bytes() == before)
    if mode == 'timeout':
        check('timeout leaves the peer lock untouched', lock.read_bytes() == b'peer-owned lock')
        lock.unlink()
    else:
        check(mode + ': owned index.lock and transaction copies are removed',
              not lock.exists() and not list((root / '.git').glob('index-resync-*')))


def test_submodule_gitlink(tmp_path):
    root, env = scene(tmp_path, "gitlink")
    sub = root / "Paired"
    sub.mkdir()
    git(sub, ["init", "-q", "-b", "main"], env)
    (sub / "one.gd").write_text("one\n", encoding="utf-8", newline="\n")
    git(sub, ["add", "-A"], env)
    git(sub, ["commit", "-q", "-m", "sub one"], env)
    pinned = git(sub, ["rev-parse", "HEAD"], env).stdout.strip()
    git(root, ["update-index", "--add", "--cacheinfo", "160000,%s,Paired" % pinned], env)
    git(root, ["commit", "-q", "-m", "pin Paired"], env)
    (sub / "two.gd").write_text("two\n", encoding="utf-8", newline="\n")
    git(sub, ["add", "-A"], env)
    git(sub, ["commit", "-q", "-m", "sub two"], env)
    ahead = git(sub, ["rev-parse", "HEAD"], env).stdout.strip()

    proc = tool(root, env, ["stage", "--name", "gitlink", "--", "Paired"])
    check("submodule path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "gitlink")
    listing = git(root, ["ls-files", "-s", "--", "Paired"], private).stdout.split()
    check("private index holds the submodule as a gitlink at its checkout HEAD",
          listing[:2] == ["160000", ahead], listing)
    status = git(root, ["diff", "--cached", "--name-status"], private).stdout.split()
    check("stage deletes nothing: the only staged change is a modification of the gitlink",
          status == ["M", "Paired"], status)
    commit = commit_index(root, env, "gitlink", "move Paired")
    check("submodule path: the private-index commit lands", commit.returncode == 0, commit.stderr)
    landed = git(root, ["ls-tree", "HEAD", "--", "Paired"], env).stdout.split()
    check("HEAD holds the gitlink at the new sha", landed[:3] == ["160000", "commit", ahead], landed)


def test_submodule_without_checkout(tmp_path):
    root, env = scene(tmp_path, "nocheckout")
    git(root, ["update-index", "--add", "--cacheinfo", "160000,%s,Paired" % ("1" * 40)], env)
    git(root, ["commit", "-q", "-m", "pin Paired with no checkout"], env)
    proc = tool(root, env, ["stage", "--name", "nocheckout", "--", "Paired"])
    check("submodule without a checkout: stage refuses with a non-zero exit", proc.returncode == 2,
          proc.returncode)
    check("the refusal names the pathspec-commit route",
          "git commit -m <msg> -- Paired" in proc.stderr, proc.stderr)
    check("a refused submodule stage leaves no private index",
          not (root / ".git" / "index-nocheckout").exists(), "")


def test_cli_entry_point_runs_as_a_script(tmp_path):
    root, env = scene(tmp_path, "cli")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = subprocess.run([sys.executable, str(root / ".claude" / "tools" / "index_commit.py"), "stage", "--name", "cli",
                           "--", "keep_a.py"], cwd=str(root), env=env, capture_output=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0 and "GIT_INDEX_FILE=.git/index-cli git commit" in proc.stdout, proc.stdout + proc.stderr
