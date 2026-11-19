"""Live-tree proof for hooks/unbounded_scan_guard.py: the real checkout's tracked-ignored `.claude/plans` files are
behind the same deny as a planted tracked-ignored tree, and the deny's `rg -uu` form reaches them.

The subject is this repository's own tracked-ignored files, so it reads the checkout's index and stays keyed on it.
`test_unbounded_scan_guard.py` proves the same deny against a planted repository.

    python3 .claude/tests/test_unbounded_scan_guard_live.py
"""
import os
import subprocess
import sys
import tempfile

import _platform_skip
import test_unbounded_scan_guard as guard

REPO = os.path.normpath(os.path.join(guard.HERE, "..", ".."))


def main():
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tempfile.mkdtemp(prefix="usgstate_live_"), PYTHONIOENCODING="utf-8")
    listing = subprocess.run(["git", "-C", REPO, "ls-files", "-ci", "--exclude-standard", "--", ".claude/plans"],
                             capture_output=True, text=True, timeout=60)
    rows = listing.stdout.split() if listing.returncode == 0 else []
    cases = []
    if rows:
        cases.append(("the real checkout's tracked-ignored plans are behind the same deny",
                      guard.verdict("grep -rn x .claude/plans", env, REPO)[2] == "deny"))
        if guard.rg_available():
            done = guard.run_rg("-uu", "--files", ".claude/plans", cwd=REPO)
            seen = {line.replace("\\", "/") for line in done.stdout.splitlines()}
            cases.append(("the deny's -uu form reaches the real tracked-ignored tree, which git grep alone cannot",
                          done.returncode == 0 and bool(seen & set(rows))))
        else:
            _platform_skip.skip("the deny's -uu form reaches the real tracked-ignored tree",
                                "no ripgrep on PATH and no embedded rg")
    if not rows:
        _platform_skip.skip("the real checkout's tracked-ignored plans are behind the same deny", "no tracked-ignored file under .claude/plans")
    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
