"""List a folder's files by walking it, so a proof's pass is keyed to the files it reads and to no git state.

`git ls-files` names the files the index tracks, and the pass cache keys that listing to every tracked name in the
repository. A proof that scans a folder's sources reads the files themselves, so it walks the folder: the same
names while the working tree matches the index, and no git process.

Not named `test_*.py` / `*_test.py` on purpose: the runner discovers proofs by those patterns.

    from _walk_files import walk_files
    walk_files(REPO, ".claude/scripts/benchmark_campaign", (".py", ".js"))   # repository-relative posix paths
"""
import os

GENERATED = frozenset({"__pycache__", ".git", "node_modules"})


def walk_files(root, folder, suffixes=None, prune=GENERATED):
    """Sorted paths under `root/folder`, relative to `root` with `/` separators, whose name ends with one of
    `suffixes` (every file when None). A folder named in `prune` is not entered."""
    base = os.path.join(os.fspath(root), *folder.strip("/").split("/"))
    found = []
    for here, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in prune]
        for name in names:
            if suffixes is None or name.endswith(tuple(suffixes)):
                found.append(os.path.relpath(os.path.join(here, name), root).replace(os.sep, "/"))
    return sorted(found)
