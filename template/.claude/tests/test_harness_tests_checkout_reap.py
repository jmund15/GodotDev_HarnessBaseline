"""Floor proof: a close killed mid-run leaves its private checkout registered as a worktree; the next
private checkout removes it once no live process holds its lock, and never removes a live one.

    python3 .claude/tests/test_harness_tests_checkout_reap.py
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
HOOKS = os.path.abspath(os.path.join(HERE, "..", "hooks"))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HOOKS)
import harness_tests  # noqa: E402
import _file_lock  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILURES.append(name + (": " + detail if detail else ""))


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout


def make_repo():
    repo = tempfile.mkdtemp(prefix="harness_reap_repo_")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    os.makedirs(os.path.join(repo, ".claude"))
    with open(os.path.join(repo, ".claude", "x.txt"), "w", encoding="utf-8") as fh:
        fh.write("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def checkouts(repo):
    return [line.split(" ", 1)[1] for line in git(repo, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ") and "harness_head_" in line]


CHILD = """
import sys, time
sys.path.insert(0, %r)
import harness_tests
with harness_tests._revision_checkout(%r, "HEAD") as snap:
    print(snap, flush=True)
    time.sleep(600)
"""


def case_a_killed_close_checkout_is_removed_by_the_next():
    repo = make_repo()
    child = subprocess.Popen([sys.executable, "-c", CHILD % (SCRIPTS, repo)], stdout=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True)
    leaked = child.stdout.readline().strip()
    live = os.path.isdir(leaked) and leaked.replace("\\", "/") in [c.replace("\\", "/") for c in checkouts(repo)]
    with harness_tests._revision_checkout(repo, "HEAD") as other:
        kept = os.path.isdir(leaked)
        check("a live close's checkout survives another close starting", live and kept,
              "live=%s kept=%s" % (live, kept))
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(child.pid)], capture_output=True)
    else:
        child.kill()
    child.wait(timeout=30)
    time.sleep(0.5)
    check("a killed close leaves its checkout behind", os.path.isdir(leaked))
    with harness_tests._revision_checkout(repo, "HEAD") as snap:
        gone = not os.path.isdir(leaked)
        unregistered = not any(c.replace("\\", "/") == leaked.replace("\\", "/") for c in checkouts(repo))
        check("the next close removes the dead close's checkout and its worktree entry", gone and unregistered,
              "dir_gone=%s unregistered=%s" % (gone, unregistered))
        check("the next close keeps its own checkout", os.path.isdir(snap))
    check("a finished close leaves no checkout", checkouts(repo) == [], str(checkouts(repo)))


def case_a_checkout_with_no_lock_file_is_left_alone():
    repo = make_repo()
    foreign = tempfile.mkdtemp(prefix="harness_head_")
    os.rmdir(foreign)
    git(repo, "worktree", "add", "-q", "--detach", foreign, "HEAD")
    with harness_tests._revision_checkout(repo, "HEAD"):
        pass
    check("a checkout without a lock (an older runner's) is never removed", os.path.isdir(foreign))
    git(repo, "worktree", "remove", "--force", foreign)


def main():
    case_a_killed_close_checkout_is_removed_by_the_next()
    case_a_checkout_with_no_lock_file_is_left_alone()
    print()
    if FAILURES:
        for f in FAILURES:
            print("  FAIL " + f)
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
