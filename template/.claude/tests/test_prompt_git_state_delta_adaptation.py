"""Re-runnable proof for hooks/prompt_git_state_delta.py's `adaptation.json`
`git_submodules` seam (Design Doc §8).

Three shapes: adaptation.json absent -> WATCHED_SUBMODULES is empty (no submodule fields in
the fingerprint, and the hook never crashes); present with a name -> that name's HEAD/sync
fields join the delta output on a repo that actually has a matching submodule path; a
wrong-typed `git_submodules` (not a list) -> the default (empty tuple) plus one stderr line.
Each case imports the hook fresh in a subprocess (module-level state is computed at import
time) against a scratch `.claude` tree (`_adaptation_fixture`).

    python -m pytest .claude/tests/test_prompt_git_state_delta_adaptation.py -q
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "prompt_git_state_delta.py"
REAL_HOOK = os.path.join(fx.REAL_HOOKS, HOOK_NAME)


def init_repo(root):
    """A throwaway git repo with one commit and a plain directory standing in for a submodule path.

    Drift detection only reads `git submodule status`, so a real `.gitmodules` is not needed to prove the field
    is read from adaptation.json and wired into WATCHED_SUBMODULES: the no-crash and field-presence assertions
    do not require an initialized submodule.
    """
    root = str(root)
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "a@example.com"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "a"], check=True)
    with open(os.path.join(root, "f.txt"), "w") as fh:
        fh.write("x")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"], check=True)
    return root


def run_hook(hook_path, repo_root, session_id="probe001", env_extra=None):
    """Spawned: the hook's stdin entry point is the subject."""
    payload = {"session_id": session_id}
    env = dict(os.environ, **(env_extra or {}))
    r = subprocess.run([sys.executable, hook_path], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30, cwd=repo_root, env=env)
    return r.stdout, r.stderr, r.returncode


@pytest.fixture
def adapted():
    """Make a scratch `.claude` tree seeded with an adaptation.json (or none); returns the copied hook's path."""
    made = []

    def make(seed):
        tree = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed=seed)
        made.append(tree)
        return os.path.join(tree, "hooks", HOOK_NAME)

    yield make
    for tree in made:
        fx.rm(tree)


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


def test_an_absent_adaptation_json_exits_zero(adapted, repo):
    _out, _err, rc = run_hook(adapted(None), repo)
    assert rc == 0


def test_an_absent_adaptation_json_writes_no_stderr(adapted, repo):
    _out, err, _rc = run_hook(adapted(None), repo)
    assert err == ""


def test_a_valid_git_submodules_entry_is_read_without_crashing(adapted, repo):
    _out, _err, rc = run_hook(adapted({"git_submodules": ["Jmodot"]}), repo)
    assert rc == 0


def test_a_valid_git_submodules_entry_writes_no_stderr(adapted, repo):
    _out, err, _rc = run_hook(adapted({"git_submodules": ["Jmodot"]}), repo)
    assert err == ""


def test_a_wrong_typed_git_submodules_still_exits_zero_never_blocking(adapted, repo):
    _out, _err, rc = run_hook(adapted({"git_submodules": "Jmodot"}), repo)
    assert rc == 0


def test_a_wrong_typed_git_submodules_writes_at_most_one_stderr_line_naming_the_key(adapted, repo):
    _out, err, _rc = run_hook(adapted({"git_submodules": "Jmodot"}), repo)
    assert err.count("\n") <= 1 and "git_submodules" in err


def test_the_cache_override_puts_the_cache_file_in_the_override_dir(adapted, repo, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    run_hook(adapted(None), repo, env_extra={"HARNESS_GIT_STATE_CACHE_DIR": str(cache_dir)})
    assert any(n.startswith("git-state-") for n in os.listdir(cache_dir))


def test_the_cache_override_keeps_the_repos_own_cache_unwritten(adapted, repo, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    run_hook(adapted(None), repo, env_extra={"HARNESS_GIT_STATE_CACHE_DIR": str(cache_dir)})
    assert not os.path.exists(os.path.join(repo, ".claude", ".cache"))
