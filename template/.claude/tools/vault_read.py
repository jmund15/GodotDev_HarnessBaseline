"""Read the markdown of an Obsidian vault root, typing every file it could not read.

`read_vault` returns the texts it read and counts that keep read, unreadable, unattempted (a dehydrated
OneDrive placeholder, which a read would download) and absent apart, so a caller never reads a sweep as
clean by omission. `verified` is true only when nothing was left unread.
"""
import os
from pathlib import Path

RECALL_ON_DATA_ACCESS = 0x400000
DEFAULT_VAULT = Path.home() / "OneDrive" / "Documents" / "ObsidianVault"


def dehydrated(path):
    attrs = getattr(os.stat(path), "st_file_attributes", 0)
    return bool(attrs & RECALL_ON_DATA_ACCESS)


def read_vault(vault, docs):
    """([(path, text)], counts) for every `.md` under `<vault>/<docs>`."""
    root = Path(vault) / docs
    counts = {"root": str(root), "read": 0, "unreadable": 0, "unattempted": 0, "absent": False, "unreadPaths": []}
    texts = []
    if not root.is_dir():
        counts["absent"] = True
        return texts, counts
    for path in sorted(root.rglob("*.md")):
        if path.is_dir():  # an archived entry keeps its name, so `notes.md/` can be a folder
            continue
        try:
            if dehydrated(path):
                counts["unattempted"] += 1
                counts["unreadPaths"].append(str(path))
                continue
            size = os.stat(path).st_size
            data = path.read_bytes()
        except OSError:
            counts["unreadable"] += 1
            counts["unreadPaths"].append(str(path))
            continue
        if size and not data:
            counts["unreadable"] += 1
            counts["unreadPaths"].append(str(path))
            continue
        counts["read"] += 1
        texts.append((str(path), data.decode("utf-8", "replace")))
    return texts, counts


def verified(counts):
    return not counts["absent"] and not counts["unreadable"] and not counts["unattempted"]
