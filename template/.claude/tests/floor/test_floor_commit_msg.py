"""The commit-time entry: `dangerous_shell_guard.py --commit-msg <file>`, called by `.claude/githooks/commit-msg`.

Rows of PushinPotions' `test_git_commit_msg_hook.py` (the commit-msg proof; not on T1's list, but it is the floor's
second entry). Decision rows call `commit_msg_check` in process with the environment git gives the hook; the rows
whose subject is the githook script itself (a refusal git must honour, a missing guard, a crash, no python3, no
repository root) run real git through the installed hook.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import dangerous_shell_guard as dsg
from conftest import CLAUDE

PEER = "Claude-Session: https://claude.ai/code/session_peer"
MINE = "Claude-Session: https://claude.ai/code/session_mine"
FLOOR_TOOLS = ("index_commit.py", "cli_usage.py")


def g(repo, *args, env=None, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=dict(os.environ, **(env or {})))
    if check and r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r


def write(repo, name, text):
    Path(repo, name).write_text(text, encoding="utf-8", newline="\n")


def sh():
    found = shutil.which("sh")
    if found:
        return found
    git = shutil.which("git") or ""
    candidate = Path(git).resolve().parents[1] / "usr" / "bin" / "sh.exe" if git else None
    return str(candidate) if candidate and candidate.exists() else "sh"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    repo = tmp_path_factory.mktemp("cm") / "repo"
    (repo / ".claude").mkdir(parents=True)
    shutil.copytree(CLAUDE / "githooks", repo / ".claude" / "githooks")
    shutil.copytree(CLAUDE / "hooks", repo / ".claude" / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / ".claude" / "tools").mkdir()
    for name in FLOOR_TOOLS:
        shutil.copy2(CLAUDE / "tools" / name, repo / ".claude" / "tools" / name)
    for name in os.listdir(repo / ".claude" / "githooks"):
        os.chmod(repo / ".claude" / "githooks" / name, 0o755)
    g(repo, "init", "-q")
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false"),
                       ("core.hooksPath", ".claude/githooks"), ("commit.gpgsign", "false")):
        g(repo, "config", key, value)
    write(repo, "f.txt", "base\n")
    write(repo, "g.txt", "base\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "--no-verify", "-m", "base")
    return repo


@pytest.fixture
def repo(template, tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(template, dest)
    return dest


def stale_index(repo, tmp):
    """The race: a temp index read from HEAD, a peer commit lands on f, the temp index gains g."""
    index = tmp / "tmp_index"
    g(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(index)})
    write(repo, "f.txt", "peer\n")
    g(repo, "commit", "-q", "--no-verify", "-m", "peer change\n\n" + PEER, "--", "f.txt")
    write(repo, "g.txt", "mine\n")
    blob = g(repo, "hash-object", "-w", "g.txt").stdout.strip()
    g(repo, "update-index", "--cacheinfo", "100644,%s,g.txt" % blob, env={"GIT_INDEX_FILE": str(index)})
    return index


def observe(repo, message, tmp, env=None):
    path = tmp / "direct-msg"
    path.write_text(message, encoding="utf-8", newline="\n")
    return dsg.commit_msg_check(str(path), dict(env or {}), str(repo))


def head_file(repo, name):
    return g(repo, "show", "HEAD:" + name).stdout


# --- the githook script (spawned: git runs it) ------------------------------------------------------------------
def test_the_incident_a_stale_temp_index_is_refused_when_git_writes_the_commit(repo, tmp_path):
    index = stale_index(repo, tmp_path)
    head = g(repo, "rev-parse", "HEAD").stdout
    r = g(repo, "commit", "-q", "-m", "mine\n\n" + MINE, env={"GIT_INDEX_FILE": str(index)}, check=False)
    assert r.returncode == 1 and "f.txt" in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert g(repo, "rev-parse", "HEAD").stdout == head


def test_a_failed_repository_root_lookup_refuses(tmp_path):
    message = tmp_path / "msg"
    message.write_text("planted commit\n", encoding="utf-8")
    r = subprocess.run([sh(), str(CLAUDE / "githooks" / "commit-msg"), str(message)], cwd=str(tmp_path),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 1 and "BLOCKED commit" in r.stderr and "cannot find the repository root" in r.stderr


def test_a_guard_that_crashes_refuses_the_commit_and_names_the_repair(repo):
    guard = repo / ".claude" / "hooks" / "dangerous_shell_guard.py"
    body = guard.read_text(encoding="utf-8")
    anchor = "def commit_msg_check(message_path, environ=None, cwd=None):\n"
    assert anchor in body
    guard.write_text(body.replace(anchor, anchor + "    raise RuntimeError('planted fault')\n", 1), encoding="utf-8",
                     newline="\n")
    write(repo, "g.txt", "crash\n")
    g(repo, "add", "g.txt")
    r = g(repo, "commit", "-q", "-m", "crash\n\n" + MINE, check=False)
    assert r.returncode != 0 and "planted fault" in r.stderr and "repair" in r.stderr.lower()
    assert head_file(repo, "g.txt") == "base\n"


def test_a_missing_guard_refuses_the_commit_and_names_the_file(repo):
    os.remove(repo / ".claude" / "hooks" / "dangerous_shell_guard.py")
    write(repo, "g.txt", "noguard\n")
    g(repo, "add", "g.txt")
    r = g(repo, "commit", "-q", "-m", "noguard\n\n" + MINE, check=False)
    assert r.returncode != 0 and "dangerous_shell_guard.py" in r.stderr and head_file(repo, "g.txt") == "base\n"


def test_a_hook_that_cannot_find_python3_refuses(repo, tmp_path):
    bare = tmp_path / "bare_bin"
    bare.mkdir()
    git_exe, sh_exe = shutil.which("git"), sh()
    if not git_exe or not os.path.isabs(sh_exe):
        pytest.skip("git and sh must be on PATH to plant a PATH without python3")
    path = os.pathsep.join([os.path.dirname(git_exe), os.path.dirname(sh_exe)])
    if shutil.which("python3", path=path):
        if os.name != "posix":
            pytest.skip("python3 shares a folder with git or sh here")
        for tool, exe in (("git", git_exe), ("sh", sh_exe)):
            os.symlink(exe, bare / tool)
        path = str(bare)
    write(repo, "g.txt", "nopython\n")
    g(repo, "add", "g.txt")
    r = g(repo, "commit", "-q", "-m", "nopython\n\n" + MINE, env={"PATH": path}, check=False)
    assert r.returncode != 0 and "python3" in r.stderr and head_file(repo, "g.txt") == "base\n"


# --- decisions (in process, the environment git hands the hook) -----------------------------------------------
def test_a_temp_index_rebuilt_from_the_current_head_allows(repo, tmp_path):
    index = stale_index(repo, tmp_path)
    g(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(index)})
    blob = g(repo, "hash-object", "-w", "g.txt").stdout.strip()
    g(repo, "update-index", "--cacheinfo", "100644,%s,g.txt" % blob, env={"GIT_INDEX_FILE": str(index)})
    assert observe(repo, "mine\n\n" + MINE, tmp_path, {"GIT_INDEX_FILE": str(index)}) is None


def test_the_stale_temp_index_is_refused_in_process(repo, tmp_path):
    index = stale_index(repo, tmp_path)
    assert "f.txt" in (observe(repo, "mine\n\n" + MINE, tmp_path, {"GIT_INDEX_FILE": str(index)}) or "")


def test_a_session_may_revert_its_own_commit(repo, tmp_path):
    write(repo, "f.txt", "mine\n")
    g(repo, "commit", "-q", "--no-verify", "-m", "mine\n\n" + MINE, "--", "f.txt")
    write(repo, "f.txt", "base\n")
    g(repo, "add", "f.txt")
    assert observe(repo, "undo mine\n\n" + MINE, tmp_path) is None


def test_revert_head_marks_an_intentional_revert(repo, tmp_path):
    write(repo, "f.txt", "peer\n")
    g(repo, "commit", "-q", "--no-verify", "-m", "peer change\n\n" + PEER, "--", "f.txt")
    g(repo, "revert", "--no-commit", "HEAD")
    assert observe(repo, "revert peer change", tmp_path) is None
    os.remove(repo / ".git" / "REVERT_HEAD")
    assert "f.txt" in (observe(repo, "revert peer change", tmp_path) or "")


def test_a_revert_in_progress_does_not_exempt_an_untouched_path(repo, tmp_path):
    write(repo, "g.txt", "peer g\n")
    g(repo, "commit", "-q", "--no-verify", "-m", "peer g change\n\n" + PEER, "--", "g.txt")
    write(repo, "f.txt", "peer\n")
    g(repo, "commit", "-q", "--no-verify", "-m", "peer change\n\n" + PEER, "--", "f.txt")
    g(repo, "revert", "--no-commit", "HEAD")
    write(repo, "g.txt", "base\n")
    g(repo, "add", "g.txt")
    assert "g.txt" in (observe(repo, "revert peer change", tmp_path) or "")


@pytest.mark.parametrize("index_name", ["next-index-1234.lock", "index.lock"])
def test_pathspec_and_all_commits_take_the_working_tree(repo, tmp_path, index_name):
    stale_index(repo, tmp_path)
    env = {"GIT_INDEX_FILE": str(repo / ".git" / index_name)}
    assert observe(repo, "pathspec\n\n" + MINE, tmp_path, env) is None


def test_a_merge_commit_allows(repo, tmp_path):
    g(repo, "checkout", "-q", "-b", "side")
    write(repo, "s.txt", "side\n")
    g(repo, "add", "s.txt")
    g(repo, "commit", "-q", "--no-verify", "-m", "side")
    g(repo, "checkout", "-q", "-")
    write(repo, "f.txt", "main\n")
    g(repo, "commit", "-q", "--no-verify", "-m", "main change\n\n" + PEER, "--", "f.txt")
    g(repo, "merge", "-q", "--no-commit", "side")
    assert observe(repo, "merge side", tmp_path) is None


def test_a_stage_record_whose_base_is_not_a_commit_id_is_refused(repo, tmp_path):
    index = tmp_path / "next_stage_index"
    g(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(index)})
    Path(str(index) + ".paths.json").write_text('{"base_head": 12345}', encoding="utf-8")
    write(repo, "g.txt", "malformed\n")
    blob = g(repo, "hash-object", "-w", "g.txt").stdout.strip()
    g(repo, "update-index", "--cacheinfo", "100644,%s,g.txt" % blob, env={"GIT_INDEX_FILE": str(index)})
    assert "stage record" in (observe(repo, "mine\n\n" + MINE, tmp_path, {"GIT_INDEX_FILE": str(index)}) or "")


def test_a_commit_in_a_fresh_worktree_allows(repo, tmp_path):
    wt = tmp_path / "wt"
    g(repo, "worktree", "add", "-q", "-b", "fresh", str(wt))
    write(wt, "g.txt", "worktree\n")
    g(wt, "add", "g.txt")
    assert observe(wt, "worktree\n\n" + MINE, tmp_path) is None


def test_an_ordinary_commit_with_no_trailer_allows(repo, tmp_path):
    write(repo, "g.txt", "plain\n")
    g(repo, "add", "g.txt")
    assert observe(repo, "plain", tmp_path) is None


def test_commit_msg_refuses_a_commit_that_reverts_a_peers_newer_blob(repo, tmp_path):
    """S5 Check: `--commit-msg` refuses a commit that reverts a peer's newer blob (the CLI, in process)."""
    index = stale_index(repo, tmp_path)
    message = tmp_path / "m"
    message.write_text("mine\n\n" + MINE, encoding="utf-8")
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    r = subprocess.run([sys.executable, str(CLAUDE / "hooks" / "dangerous_shell_guard.py"), "--commit-msg",
                        str(message)], cwd=str(repo), env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "f.txt" in r.stderr
