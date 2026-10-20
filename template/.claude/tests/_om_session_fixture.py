"""A session cut from real artifacts: the 2026-10-18 launches of session 30b7e19d (transcript lines, fan-out
inventories, trimmed records, a reaped run's empty record and pid file), rebuilt under a temp root.

`materialize(root)` writes `<root>/<SESSION_ID>.jsonl`, the out dirs under `<root>/.claude/scratch/`, and returns
the session path. Every real repo path in the fixtures is rewritten to `root`; relative paths stay as written."""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "om_session_pending")
SESSION_ID = "30b7e19d-8945-4de9-ab38-4b02699ef340"
REAL_REPO = "C:/Users/dev/proj"
OUT_DIRS = {"run2": ("codify_30b7e19d_run2",), "exec": ("codify_30b7e19d_exec",),
            "exec_out": ("laneb", "parts", "exec_out")}
GATEFIX_LAUNCH = "2109d988-73f6-4f6d-87a5-781ad8d7e51a"


def _swap(value, root):
    if isinstance(value, str):
        swapped = (value.replace(REAL_REPO, root.replace("\\", "/"))
                   .replace(REAL_REPO.replace("/", "\\"), root))
        if os.sep == "/" and swapped != value and swapped.startswith(root):
            # A recorded Windows path (`<repo>\.claude\...`) must open on a POSIX host.
            swapped = swapped.replace("\\", "/")
        return swapped
    if isinstance(value, list):
        return [_swap(v, root) for v in value]
    if isinstance(value, dict):
        return {k: _swap(v, root) for k, v in value.items()}
    return value


def out_dir(root, name):
    return os.path.join(root, ".claude", "scratch", *OUT_DIRS[name])


def record_path(root, name, label):
    return os.path.join(out_dir(root, name), label + ".record.json")


def materialize(root):
    for name in OUT_DIRS:
        target = out_dir(root, name)
        os.makedirs(target)
        for entry in sorted(os.listdir(os.path.join(FIXTURES, name))):
            source = os.path.join(FIXTURES, name, entry)
            if entry.endswith(".json") and os.path.getsize(source):
                with open(source, encoding="utf-8") as fh:
                    data = _swap(json.load(fh), root)
                with open(os.path.join(target, entry), "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(data, fh)
            else:
                shutil.copyfile(source, os.path.join(target, entry))
    with open(os.path.join(FIXTURES, "transcript.jsonl"), encoding="utf-8") as src, \
            open(os.path.join(root, SESSION_ID + ".jsonl"), "w", encoding="utf-8", newline="\n") as dst:
        for line in src:
            dst.write(json.dumps(_swap(json.loads(line), root)) + "\n")
    session = os.path.join(root, SESSION_ID)
    os.makedirs(os.path.join(session, "workflows"))
    return session
