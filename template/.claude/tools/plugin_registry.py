"""A login's user-scope plugin registration and the one build identity of an installed plugin.

`<login>/plugins/installed_plugins.json` is read here and nowhere else that needs a plugin's user row. The
identity of a build is its source revision (`gitCommitSha`), else a digest of its `dist/` tree; `version` is a
label two different builds can share.
"""
import hashlib
import json
import os
from pathlib import Path

REGISTRY_LEAF = Path("plugins") / "installed_plugins.json"


def registry_path(login):
    return Path(login) / REGISTRY_LEAF


def read(source):
    """The parsed registry of a login directory or a registry file; {} when absent. An unreadable or
    malformed registry raises, since a login whose plugins cannot be read is not a login without them."""
    path = Path(source)
    if path.is_dir():
        path = registry_path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("%s is not a JSON object" % path)
    return data


def user_rows(source, plugin):
    """Every user-scope registration of `plugin` in a login directory or registry file."""
    rows = (read(source).get("plugins") or {}).get(plugin) or []
    return [r for r in rows if isinstance(r, dict) and r.get("scope") == "user"]


def user_row(source, plugin):
    """The one user-scope registration of `plugin`, or None when there are none or several."""
    rows = user_rows(source, plugin)
    return rows[0] if len(rows) == 1 else None


def build_identity(row):
    """The revision a row's build came from, else a digest of its `dist/` tree; None when neither exists."""
    if not isinstance(row, dict):
        return None
    if row.get("gitCommitSha"):
        return str(row["gitCommitSha"])
    dist = Path(row.get("installPath") or os.devnull) / "dist"
    if not dist.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(dist.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(dist.parent).as_posix().encode() + b"\0" + path.read_bytes())
    return "dist-sha256:" + digest.hexdigest()
