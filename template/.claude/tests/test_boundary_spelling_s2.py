"""Proof for X3 S2: a payload path in Git Bash `/c/...` spelling decides the same as its `C:/...` spelling.

Each case feeds one guard decision site the `/c/` form a Git Bash payload carries, against a real checkout on
disk, and asserts the same verdict the `C:/` form gets. Windows only: the spelling bug is Windows Python's.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import approving_gates  # noqa: E402
import composed_output_guard  # noqa: E402


def native(path):
    path = os.fspath(path).replace("\\", "/")
    return path[0].upper() + path[1:] if path[1:2] == ":" else path


def msys(path):
    path = os.fspath(path).replace("\\", "/")
    return "/" + path[0].lower() + path[2:] if path[1:2] == ":" else path


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else "  " + detail))
    return ok


def same(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def main():
    if os.name != "nt":
        print("SKIP: Windows-only spelling proof")
        return 0
    results = []
    root = Path(tempfile.mkdtemp(prefix="x3s2_boundary_")).resolve()
    (root / "plans").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    os.environ["CLAUDE_PROJECT_DIR"] = native(root)
    spelled = msys(root)

    # approving_gates: the payload cwd is a /c/ spelling of the project root.
    got = approving_gates.trusted_root({"cwd": spelled})
    results.append(check("approving_gates.trusted_root: /c/ cwd is the project root",
                         got is not None and same(got, root), "got %r" % (got,)))

    # approving_gates: `git -C <root>` in /c/ form equals the C:/ form.
    cmd_c = ["git", "-C", spelled, "commit"]
    cmd_w = ["git", "-C", native(root), "commit"]
    ok_c, why_c = approving_gates._commit_shape(cmd_c, root)
    ok_w, why_w = approving_gates._commit_shape(cmd_w, root)
    results.append(check("approving_gates._commit_shape: /c/ -C root decides as C:/ does",
                         (ok_c, why_c) == (ok_w, why_w), "msys=%r native=%r" % ((ok_c, why_c), (ok_w, why_w))))

    # approving_gates: a read-only probe's `git -C <root>` in /c/ form is accepted.
    index_c, why_pc = approving_gates._probe_subcommand(["git", "-C", spelled, "status"], root)
    results.append(check("approving_gates._probe_subcommand: /c/ -C root is accepted",
                         index_c == 3, "index=%r why=%r" % (index_c, why_pc)))

    # approving_gates: a leading `cd /c/<root>` wrapper is stripped as the project root.
    stripped = approving_gates.without_output_wrappers("cd %s && git status" % spelled, root)
    results.append(check("approving_gates.without_output_wrappers: cd to /c/ root is stripped",
                         stripped.strip() == "git status", "got %r" % (stripped,)))

    # composed_output_guard: a ~/.claude path in /c/ form is the user-level Claude dir.
    user_file = os.path.join(os.path.expanduser("~"), ".claude", "x.json")
    results.append(check("composed_output_guard._in_user_claude: /c/ spelling of ~/.claude",
                         composed_output_guard._in_user_claude(msys(user_file))))

    failed = results.count(False)
    print("%d of %d cases failed" % (failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
