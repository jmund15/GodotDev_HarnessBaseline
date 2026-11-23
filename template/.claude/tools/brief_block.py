#!/usr/bin/env python3
"""The brief's `task` block: the one fenced JSON object that names a job's check list and write set.

    task_block(text) -> dict | None    None when the brief holds no `task` fence

It raises ValueError naming the defect when the fence does not hold exactly one JSON object or its `check`
list is empty. A `check` given as one string is that one-entry list. Readers: `hooks/sidecar_workdir_guard.py`
(the `writeSet`) and MaddManager's `ui_round.py`, which imports this module by name.
"""
import json
import re


def _fenced_bodies(text, name):
    """The body of every fence opened with `name` in `text`, in order."""
    return re.findall(r"```" + re.escape(name) + r"[ \t]*\r?\n(.*?)```", text, re.S)


def task_block(text):
    found = _fenced_bodies(text, "task")
    if not found:
        return None
    if len(found) != 1:
        raise ValueError(f"the brief must hold one task block of one JSON object; found {len(found)} blocks")
    try:
        obj = json.loads(found[0])
    except ValueError as exc:
        raise ValueError(f"the task block is not one JSON object ({exc})")
    if not isinstance(obj, dict):
        raise ValueError("the task block is not a JSON object")
    checks = obj.get("check")
    if isinstance(checks, str):
        checks = obj["check"] = [checks]
    if not isinstance(checks, list) or not checks:
        raise ValueError("the task block has an empty check list")
    if not all(isinstance(c, str) and c.strip() for c in checks):
        raise ValueError("every check entry must be a non-empty string")
    write_set = obj.get("writeSet", [])
    if not isinstance(write_set, list) or not all(isinstance(p, str) for p in write_set):
        raise ValueError("writeSet must be a list of paths")
    return obj
