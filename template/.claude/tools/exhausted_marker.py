"""The one Python reader of the sidecar usage-limit marker files.

`sidecar_common.sh` (`sc_exhausted_marker`) writes them and resolves the same directory in shell, which cannot
import this: keep its default path in step. Layout: <dir>/<transport>.json, and for one pool member
<dir>/members/<transport>/<member>.json."""
import json
import os
from pathlib import Path


def exhausted_dir():
    return Path(os.environ.get("SIDECAR_EXHAUSTED_DIR") or Path.home() / ".claude" / "sidecar-exhausted")


def marker_path(transport, member=None):
    base = exhausted_dir()
    return base / "members" / transport / f"{member}.json" if member else base / f"{transport}.json"


def read(transport, member=None):
    """The marker's dict, or None when it is missing, unreadable or not an object."""
    try:
        with open(marker_path(transport, member), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
