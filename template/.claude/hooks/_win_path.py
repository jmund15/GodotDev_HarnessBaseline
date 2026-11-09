"""Library (not a hook): convert a Git Bash drive spelling, without resolving a path.

`drive_path` rewrites only an initial ASCII `/c/` (or bare `/c`) to `C:/`, on any OS.
It preserves the suffix, including trailing slashes. UNC, `/mnt/c`, native drive paths,
relative paths and non-path strings stay unchanged; None and empty strings pass through.
Quoting, backslash cleanup, case folding and relative resolution belong to the caller.
`native_path` applies that rewrite only on Windows, for paths Python will open locally.
"""
import os
import posixpath
import re


def drive_path(path):
    match = re.match(r"^/([A-Za-z])(?:/|\Z)", path) if path else None
    return "%s:%s" % (match.group(1).upper(), path[2:] or "/") if match else path


def native_path(path):
    return drive_path(path) if os.name == "nt" else path


def identity(path, root):
    """`(repo_id, rel)`: the checkout `path` names under `root`, and its POSIX path inside that checkout.

    `repo_id` is `root`'s resolved spelling, so `C:/Repo` and `/c/Repo` are one checkout and two checkouts
    are two. `rel` is relative to `root`, case kept; a relative `path` already is. A path outside `root`
    keeps its absolute POSIX form, so it never matches a path inside `root`.
    """
    text = drive_path(str(path or "")).replace("\\", "/")
    base = drive_path(str(root or "")).replace("\\", "/").rstrip("/")
    repo_id = os.path.normcase(os.path.realpath(native_path(str(root or ""))))
    if not re.match(r"^(/|[A-Za-z]:)", text):
        return repo_id, posixpath.normpath(text) if text else ""
    if text.lower() == base.lower():
        return repo_id, ""
    if base and text.lower().startswith(base.lower() + "/"):
        return repo_id, text[len(base) + 1:]
    return repo_id, posixpath.normpath(text)


def same(a, b):
    """True when two `identity` pairs name one file: one checkout, and paths equal in letter case."""
    return bool(a[1]) and a[0] == b[0] and a[1].lower() == b[1].lower()
