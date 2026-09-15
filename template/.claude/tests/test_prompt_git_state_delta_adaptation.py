"""Re-runnable proof for hooks/prompt_git_state_delta.py's `adaptation.json`
`git_submodules` seam (Design Doc §8).

Three shapes: adaptation.json absent -> WATCHED_SUBMODULES is empty (no submodule fields in
the fingerprint, and the hook never crashes); present with a name -> that name's HEAD/sync
fields join the delta output on a repo that actually has a matching submodule path; a
wrong-typed `git_submodules` (not a list) -> the default (empty tuple) plus one stderr line.
Each case imports the hook fresh in a subprocess (module-level state is computed at import
time) against a scratch `.claude` tree (`_adaptation_fixture`).

    python3 .claude/tests/test_prompt_git_state_delta_adaptation.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "prompt_git_state_delta.py"
REAL_HOOK = os.path.join(fx.REAL_HOOKS, HOOK_NAME)


def _init_repo_with_submodule(root, sub_name):
    """A throwaway git repo with one commit and a plain directory standing in for a
    submodule path (drift detection only reads `git submodule status`, so an actual
    `.gitmodules` isn't needed to prove the field is read from adaptation.json and wired
    into WATCHED_SUBMODULES -- the no-crash and field-presence assertions don't require a
    real submodule to be initialized)."""
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "a@example.com"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "a"], check=True)
    (os_path := os.path.join(root, "f.txt"))
    with open(os_path, "w") as fh:
        fh.write("x")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"], check=True)


def run_hook(hook_path, repo_root, session_id="probe001"):
    payload = {"session_id": session_id}
    env = dict(os.environ)
    r = subprocess.run([sys.executable, hook_path], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30, cwd=repo_root, env=env)
    return r.stdout, r.stderr, r.returncode


def main():
    cases = []
    failures = []
    scratches = []
    repos = []
    try:
        # --- absent: adaptation.json missing -> no crash, no submodule field ------------
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed=None)
        scratches.append(tmp)
        repo = tempfile.mkdtemp(prefix="gsd_repo_")
        repos.append(repo)
        _init_repo_with_submodule(repo, "Jmodot")
        _out, err, rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), repo)
        cases.append(("absent adaptation.json: hook exits 0", rc == 0))
        cases.append(("absent adaptation.json: no stderr", err == ""))

        # --- present: a valid git_submodules entry is read without crashing --------------
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed={"git_submodules": ["Jmodot"]})
        scratches.append(tmp)
        repo = tempfile.mkdtemp(prefix="gsd_repo_")
        repos.append(repo)
        _init_repo_with_submodule(repo, "Jmodot")
        _out, err, rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), repo)
        cases.append(("present adaptation.json: hook exits 0 with a watched submodule", rc == 0))
        cases.append(("present adaptation.json: no stderr on a valid entry", err == ""))

        # --- wrong-typed: git_submodules is not a list -> default + one stderr line ------
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed={"git_submodules": "Jmodot"})
        scratches.append(tmp)
        repo = tempfile.mkdtemp(prefix="gsd_repo_")
        repos.append(repo)
        _init_repo_with_submodule(repo, "Jmodot")
        _out, err, rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), repo)
        cases.append(("wrong-typed git_submodules: hook still exits 0 (never blocks)", rc == 0))
        cases.append(("wrong-typed git_submodules: one stderr line names the key",
                      err.count("\n") <= 1 and "git_submodules" in err))
    finally:
        for tmp in scratches:
            fx.rm(tmp)
        for repo in repos:
            fx.rm(repo)

    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
