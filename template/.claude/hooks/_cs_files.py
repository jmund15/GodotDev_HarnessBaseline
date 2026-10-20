"""The repository's .cs files, walked without entering skipped directories.

Prune at the directory, never `rglob` then filter: a filter-after walk still visits every
checkout under `.claude/worktrees/`. The set is the same: a file is skipped exactly when one of
its parent directories is named in `skip`."""
import os
from pathlib import Path


def cs_files(root, skip):
    """Sorted (path, rel) pairs for every `*.cs` under `root` whose parent directories avoid `skip`."""
    root = Path(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            if name.endswith(".cs"):
                path = Path(dirpath) / name
                found.append((path, path.relative_to(root)))
    found.sort(key=lambda pair: pair[1].as_posix())
    return found
