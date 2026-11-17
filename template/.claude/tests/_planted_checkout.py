"""Stand a proof in a repository of its own.

A hook or tool that is handed no checkout asks git about `CLAUDE_PROJECT_DIR`, else the process's working
directory: the rigor reader runs `git status`, the checkout locator runs `git rev-parse`. A proof that leaves both
on this checkout therefore reads its HEAD, index and worktree, and its pass is keyed to all of them.
`enter_planted_checkout()` makes an empty `git init` folder in the proof's temp root, points both at it (children
inherit them) and removes it at exit; the proof's own files are still found by absolute path from `__file__`.

Not named `test_*.py` / `*_test.py` on purpose: the runner discovers proofs by those patterns.

    from _planted_checkout import enter_planted_checkout
    enter_planted_checkout()
"""
import atexit
import os
import shutil
import subprocess
import tempfile


def enter_planted_checkout(prefix="planted_checkout_"):
    """Create a `git init` folder, make it the working directory, and return its path."""
    folder = tempfile.mkdtemp(prefix=prefix)
    subprocess.run(["git", "init", "-q", folder], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    previous, previous_project = os.getcwd(), os.environ.get("CLAUDE_PROJECT_DIR")
    os.chdir(folder)
    os.environ["CLAUDE_PROJECT_DIR"] = folder

    def leave():
        os.chdir(previous)
        if previous_project is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = previous_project
        shutil.rmtree(folder, ignore_errors=True)

    atexit.register(leave)
    return folder
