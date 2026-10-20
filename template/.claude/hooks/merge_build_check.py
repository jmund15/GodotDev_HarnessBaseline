#!/usr/bin/env python3
"""
Git hook body: a merge whose tree changes C# builds before its commit exists. Class: floor.

Two lane branches can each compile while their textual merge does not, and `git merge --no-edit` commits a clean merge
at once, so no build runs before the commit. `githooks/pre-merge-commit` (the auto-commit of a clean merge) and
`githooks/pre-commit` (the `git commit` that finishes a conflicted merge, while MERGE_HEAD exists) both call this file
with their hook name as the one argument.

Runs only when the staged tree differs from HEAD in a `.cs` or `.csproj` path, or in a submodule pointer (a mode
160000 row: a Jmodot bump changes C# too). It then builds the first root `*.csproj` with `dotnet build`;
`HARNESS_MERGE_BUILD_CMD` replaces that command in proofs and receives the csproj path as its last argument. A failed
build prints the first 10 `error CS` lines and exits 1; git then refuses the commit and leaves the merge in progress.
Fix the tree, then `git commit`. `--no-verify` bypasses the hook.

Live-checkout safety: an in-place build on a checkout whose Godot editor or playtest is live rewrites the assemblies
the editor holds and can strip `.tres` scripts (`scripts/run_test_suite.ps1` entry editor guard). Liveness is the one
detector `editor_live_build_guard._is_live`. A live checkout is never built in place and never routed to its twin
(`scripts/TwinCheckout.ps1`): the twin syncs HEAD plus the working-tree files that differ from the INDEX, and a
merge's result is already staged, so the twin would build HEAD without the merge. The commit is refused with the
reason; close the editor or playtest, or use `--no-verify`.

`git merge` does not update a submodule checkout, so a moved pointer is built only when the checkout already sits at
that commit. Otherwise the build would compile the old Jmodot and the commit is refused until `git submodule update`.
"""
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SHOWN_ERRORS = 10
BYPASS = "`--no-verify` skips this check."


def child_env():
    """git exports GIT_DIR and GIT_INDEX_FILE to its hooks; a child that names another repo or builds must not inherit them."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(*args, env=None):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def changed_paths():
    """(paths, gitlinks): staged paths vs HEAD, and the submodule pointers among them with their index sha."""
    out = git("diff", "--cached", "--raw", "--no-abbrev", "--no-renames", "-z", "HEAD").stdout.split("\0")
    paths, gitlinks = [], {}
    for meta, path in zip(out[0::2], out[1::2]):
        paths.append(path)
        fields = meta.lstrip(":").split()
        if len(fields) >= 4 and "160000" in fields[:2]:
            gitlinks[path] = fields[3]
    return paths, gitlinks


def refuse(why):
    sys.stderr.write(f"BLOCKED merge commit -- {why}\n"
                     f"The merge stays in progress. Resolve that, then `git commit`. {BYPASS}\n")
    return 1


def error_lines(output):
    seen = []
    for line in output.splitlines():
        line = line.strip()
        if "error CS" in line and line not in seen:
            seen.append(line)
    return seen


def main(argv):
    mode = argv[1] if len(argv) > 1 else "pre-merge-commit"
    if mode == "pre-commit" and git("rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0:
        return 0
    paths, gitlinks = changed_paths()
    if not gitlinks and not any(p.endswith((".cs", ".csproj")) for p in paths):
        return 0
    root = git("rev-parse", "--show-toplevel").stdout.strip()
    projects = sorted(Path(root).glob("*.csproj"))
    if not projects:
        return 0

    from editor_live_build_guard import _is_live, _root
    live_root = _root(root)
    if live_root and _is_live(live_root):
        return refuse(f"a Godot editor or playtest is live on {root}, and a build here would rewrite the assemblies "
                      "it has loaded (scripts/run_test_suite.ps1 entry editor guard). The twin checkout cannot build "
                      "an in-progress merge tree. Close the editor or playtest.")
    for path, sha in gitlinks.items():
        checkout = os.path.join(root, path)
        initialized = os.path.exists(os.path.join(checkout, ".git"))
        have = git("-C", checkout, "rev-parse", "HEAD", env=child_env()).stdout.strip() if initialized else ""
        if have != sha:
            return refuse(f"the merge moves {path} to {sha[:9]} but the {path} checkout is at {have[:9] or 'nothing'}, "
                          f"so a build would compile the old {path}. Run `git submodule update {path}`.")

    cmd = shlex.split(os.environ.get("HARNESS_MERGE_BUILD_CMD") or "dotnet build -nologo -v q") + [str(projects[0])]
    sys.stderr.write(f"merge_build_check: building {projects[0].name} before the merge commit...\n")
    started = time.monotonic()
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root,
                             env=child_env())
    except OSError as exc:
        return refuse(f"the build command `{cmd[0]}` did not start ({exc}).")
    seconds = time.monotonic() - started
    if run.returncode == 0:
        sys.stderr.write(f"merge_build_check: build ok in {seconds:.1f}s\n")
        return 0
    output = run.stdout + "\n" + run.stderr
    shown = error_lines(output)[:SHOWN_ERRORS] or [l for l in output.splitlines() if l.strip()][-SHOWN_ERRORS:]
    sys.stderr.write("\n".join([f"BLOCKED merge commit -- the merged tree does not compile ({seconds:.1f}s):", *shown,
                                f"Fix the tree, then `git commit`; the merge stays in progress. {BYPASS}"]) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
