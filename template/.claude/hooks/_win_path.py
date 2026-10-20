"""Library (not a hook): convert a Git Bash drive spelling, without resolving a path.

`drive_path` rewrites only an initial ASCII `/c/` (or bare `/c`) to `C:/`, on any OS.
It preserves the suffix, including trailing slashes. UNC, `/mnt/c`, native drive paths,
relative paths and non-path strings stay unchanged; None and empty strings pass through.
Quoting, backslash cleanup, case folding and relative resolution belong to the caller.
`native_path` applies that rewrite only on Windows, for paths Python will open locally.
"""
import os
import re


def drive_path(path):
    match = re.match(r"^/([A-Za-z])(?:/|\Z)", path) if path else None
    return "%s:%s" % (match.group(1).upper(), path[2:] or "/") if match else path


def native_path(path):
    return drive_path(path) if os.name == "nt" else path
