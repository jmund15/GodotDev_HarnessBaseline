#!/usr/bin/env python3
"""Git merge driver for `.claude/baseline.lock.json`: `lock_merge.py [--measurement] %O %A %B`.

Merges the three versions by key, not by line. `files` merges per path; every other top-level
key merges by its own value. A change one side made applies; equal changes coalesce. Exit 0 writes
the merge into %A. Exit 1 leaves %A untouched and names the conflict on stderr: a path or key both
sides changed differently, a row one side deleted while the other edited, or a key repeated in any
input (json.loads would keep only the last one).

`--measurement` is the policy for a runner-written measurement cache (such as a test runner's batch-duration file):
a JSON object whose `updated_at` stamp every write rewrites and whose object-valued sections hold one
measurement per key. Any recent measurement is valid, so a section key both sides changed takes the side with
the newer `updated_at` (ours on a tie), `updated_at` takes the max, and one-sided changes and deletions apply.
A key one side deleted and the other re-measured keeps the measurement: a stale row is dropped by the next
write, while a missing row bins at the default until a run measures it.
A scalar both sides changed differently still conflicts. The output is `json.dumps(indent=2)`, which is the
bytes the runner writes.

`commands/workstation_setup.md` registers the drivers as `merge.baseline-lock.driver` and
`merge.measurement-cache.driver`; an unregistered driver falls back to git's text merge.
"""
import json
import sys
from pathlib import Path

MISSING = object()


class LockConflict(Exception):
    pass


def _no_repeated_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise LockConflict("key %r appears twice in one object" % key)
        obj[key] = value
    return obj


def load(path):
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text, object_pairs_hook=_no_repeated_keys)


def _value(value):
    return json.dumps(value, ensure_ascii=False)


def dump_lock(lock):
    """The lock's bytes: one top-level key per line and one `files` row per line, so a row edit is one
    line. `index_commit` writes through this too, so neither tool reformats the other's rows."""
    lines = ["{"]
    keys = list(lock)
    for index, key in enumerate(keys):
        comma = "," if index < len(keys) - 1 else ""
        value = lock[key]
        if key != "files" or not isinstance(value, dict) or not value:
            lines.append("  " + _value(key) + ": " + _value(value) + comma)
            continue
        lines.append('  "files": {')
        rows = list(value.items())
        for row_index, (path, row) in enumerate(rows):
            row_comma = "," if row_index < len(rows) - 1 else ""
            lines.append("    " + _value(path) + ": " + _value(row) + row_comma)
        lines.append("  }" + comma)
    lines.append("}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def merge_value(base, ours, theirs, where):
    """Three-way merge of one value. The side that changed it wins; both sides changing it differently conflicts."""
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    raise LockConflict(where)


def merge_lock(base, ours, theirs):
    merged = {}
    for key in dict.fromkeys([*ours, *theirs, *base]):
        if key == "files":
            files = {}
            base_rows, our_rows, their_rows = base.get(key, {}), ours.get(key, {}), theirs.get(key, {})
            for path in dict.fromkeys([*our_rows, *their_rows, *base_rows]):
                row = merge_value(base_rows.get(path, MISSING), our_rows.get(path, MISSING),
                                  their_rows.get(path, MISSING), "row %r" % path)
                if row is not MISSING:
                    files[path] = row
            merged[key] = files
            continue
        value = merge_value(base.get(key, MISSING), ours.get(key, MISSING), theirs.get(key, MISSING),
                            "key %r" % key)
        if value is not MISSING:
            merged[key] = value
    return merged


def merge_measurement(base, ours, theirs):
    newer = theirs if str(theirs.get("updated_at", "")) > str(ours.get("updated_at", "")) else ours
    older = ours if newer is theirs else theirs
    merged = {}
    for key in dict.fromkeys([*ours, *theirs, *base]):
        b, o, t = base.get(key, MISSING), ours.get(key, MISSING), theirs.get(key, MISSING)
        if key == "updated_at":
            merged[key] = newer[key] if key in newer else older[key]
        elif all(isinstance(v, dict) for v in (o, t)):
            section = {}
            base_rows = b if isinstance(b, dict) else {}
            for name in dict.fromkeys([*o, *t, *base_rows]):
                bv, ov, tv = base_rows.get(name, MISSING), o.get(name, MISSING), t.get(name, MISSING)
                if ov != bv and tv != bv and ov != tv:
                    row = tv if ov is MISSING else ov if tv is MISSING else newer[key][name]
                else:
                    row = merge_value(bv, ov, tv, "%s row %r" % (key, name))
                if row is not MISSING:
                    section[name] = row
            merged[key] = section
        else:
            value = merge_value(b, o, t, "key %r" % key)
            if value is not MISSING:
                merged[key] = value
    return merged


def dump_measurement(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv):
    measurement = "--measurement" in argv
    argv = [arg for arg in argv if arg != "--measurement"]
    if len(argv) != 4:
        print("usage: lock_merge.py [--measurement] <base> <ours> <theirs>   (git passes %O %A %B)", file=sys.stderr)
        return 2
    base_path, ours_path, theirs_path = argv[1:]
    try:
        merge = merge_measurement if measurement else merge_lock
        merged = merge(load(base_path), load(ours_path), load(theirs_path))
    except LockConflict as exc:
        print("lock_merge: conflict: %s" % exc, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print("lock_merge: could not read a version: %s" % exc, file=sys.stderr)
        return 1
    Path(ours_path).write_bytes((dump_measurement if measurement else dump_lock)(merged))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
