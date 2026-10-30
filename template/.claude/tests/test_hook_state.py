"""Direct proof for hooks/_hook_state.py.

The library exists because two hooks writing the shared per-session state file interleaved into
one complete JSON document followed by a fragment of another, which bricked harness editing for a
whole session. Its two properties are therefore load-bearing and are asserted here directly rather
than through a consumer: atomic writes stop NEW damage, salvage stops EXISTING damage from being
permanent. A consumer test exercises the happy path and would miss either.

    python3 .claude/tests/test_hook_state.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import fnmatch  # noqa: E402
import _hook_state  # noqa: E402
from _hook_state import HARNESS_DIRS, SETTINGS_FILES, in_harness_dirs, is_harness_code, is_settings_file, read_json_salvage, write_json_atomic  # noqa: E402

failures = []
ran = 0


def check(label, cond, detail=""):
    global ran
    ran += 1
    print("%-4s %s" % ("ok" if cond else "FAIL", label))
    if not cond:
        failures.append("%s %s" % (label, detail))


d = tempfile.mkdtemp(prefix="hookstate_")

# --- salvage: the exact corruption shape that caused the incident -----------
torn = os.path.join(d, "torn.json")
good = {"skills_loaded": ["instruction_quality"], "memory_check_fires": 2}
io.open(torn, "w", encoding="utf-8").write(
    json.dumps(good) + 'h__search", "target": "SEARCH:x", "ts": 1}], "skills_loaded": ["a"]}')
got = read_json_salvage(torn)
check("salvages a complete document followed by debris",
      got.get("skills_loaded") == ["instruction_quality"], repr(got)[:120])
check("salvage keeps every field of the recovered document",
      got.get("memory_check_fires") == 2, repr(got)[:120])

# --- salvage: fail-open, never raise ---------------------------------------
io.open(os.path.join(d, "empty.json"), "w").write("")
io.open(os.path.join(d, "junk.json"), "w").write("not json at all")
io.open(os.path.join(d, "list.json"), "w").write("[1,2,3]")
io.open(os.path.join(d, "frag.json"), "w").write('{"a": 1, "b')
check("missing file -> {}", read_json_salvage(os.path.join(d, "nope.json")) == {})
check("empty file -> {}", read_json_salvage(os.path.join(d, "empty.json")) == {})
check("garbage -> {}", read_json_salvage(os.path.join(d, "junk.json")) == {})
check("non-dict JSON -> {}", read_json_salvage(os.path.join(d, "list.json")) == {})
check("leading fragment with no complete document -> {}",
      read_json_salvage(os.path.join(d, "frag.json")) == {})

# --- atomic write ----------------------------------------------------------
tgt = os.path.join(d, "nested", "state.json")
check("write creates missing parent dirs", write_json_atomic(tgt, {"a": 1}) is True)
check("write round-trips", json.load(io.open(tgt, encoding="utf-8")) == {"a": 1})
check("write leaves no temp file behind",
      not [f for f in os.listdir(os.path.dirname(tgt)) if f.startswith(".tmp_")],
      str(os.listdir(os.path.dirname(tgt))))

# The replacement must be all-or-nothing: a reader sees the old doc or the new one.
write_json_atomic(tgt, {"a": 2, "b": 3})
check("overwrite is complete, never partial",
      json.load(io.open(tgt, encoding="utf-8")) == {"a": 2, "b": 3})

# Windows refuses a rename onto a file another process holds open (WinError 5): the write waits the reader out.
held = os.path.join(d, "held.json")
real_replace, refusals = os.replace, [2]


def held_replace(src, dst):
    if refusals[0]:
        refusals[0] -= 1
        raise PermissionError(13, "Access is denied", dst)
    real_replace(src, dst)


os.replace = held_replace
try:
    raised = None
    try:
        write_json_atomic(held, {"held": 1}, raising=True)
    except PermissionError as exc:
        raised = exc
finally:
    os.replace = real_replace
check("a write onto a briefly held file waits and lands", raised is None and refusals[0] == 0
      and read_json_salvage(held) == {"held": 1}, repr(raised))

# --- locked read-modify-write -----------------------------------------------
update_json_locked = getattr(_hook_state, "update_json_locked", None)
check("shared state exposes one locked read-modify-write helper",
      update_json_locked is not None)
if update_json_locked is not None:
    shared = os.path.join(d, "shared.json")
    write_json_atomic(shared, {"seed": True})

    def set_field(key, value):
        def update(state):
            time.sleep(0.03)
            state[key] = value
        update_json_locked(shared, update)

    threads = [threading.Thread(target=set_field, args=("last_prompt", "full prompt")),
               threading.Thread(target=set_field, args=("reminded_domains", ["Harness"]))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    merged = read_json_salvage(shared)
    check("locked updates preserve fields from concurrent sibling hooks",
          merged.get("last_prompt") == "full prompt"
          and merged.get("reminded_domains") == ["Harness"], repr(merged))

# `pretty` is the format knob for an AUTHORED file a locked write touches: without it, a declared park rewrites
# `core/lifecycle.json` as one compact line and the reader loses the indentation the file is written in.
pretty_tgt = os.path.join(d, "authored.json")
with io.open(pretty_tgt, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps({"rows": [{"key": "a", "reason": "café"}]}, indent=2, ensure_ascii=False) + "\n")
update_json_locked(pretty_tgt, lambda state: state["rows"].append({"key": "b", "reason": "naïve"}), pretty=True)
raw = io.open(pretty_tgt, "rb").read()
check("a locked update keeps an authored file's indented form", b"\n  " in raw and raw.endswith(b"\n"), repr(raw[:80]))
check("...and its non-ASCII characters", b"na\xc3\xafve" in raw, repr(raw))
check("...and stays LF", b"\r" not in raw, repr(raw))
update_json_locked(os.path.join(d, "compact.json"), lambda state: state.update({"a": 1}))
check("without `pretty` the default compact form stands",
      b"\n" not in io.open(os.path.join(d, "compact.json"), "rb").read())

# Every hook that writes a state file does it through update_json_locked (R2, 2026-09-14): an unlocked
# read, change and write between two concurrent hooks drops the other hook's field. The scan covers every
# hook, so a new writer fails here without joining a hand-kept list.
import ast  # noqa: E402

STATE_PATH_NAMES = {"state_path", "_state_path", "_session_record", "STATE_DIR", "state_dir"}


def _node_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _state_write_line(fn, sources=None):
    """Follow state-path values to write targets, rather than co-occurring names."""
    sources = sources or STATE_PATH_NAMES
    tainted = set()

    def flows(node):
        if node is None:
            return False
        if isinstance(node, ast.Call) and _node_name(node.func) in {"read_json_salvage", "update_json_locked", "json_load", "load"}:
            return False
        if isinstance(node, ast.Name):
            return node.id in tainted or node.id in {"STATE_DIR", "state_dir"}
        if isinstance(node, ast.Call) and _node_name(node.func) in sources:
            return True
        return any(flows(child) for child in ast.iter_child_nodes(node))

    # Aliases and opened file handles carry path provenance. Resolve chains before
    # checking sinks; variable spelling is immaterial.
    for _ in range(len(list(ast.walk(fn)))):
        before = set(tainted)
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and flows(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                tainted.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
            if isinstance(node, ast.withitem) and flows(node.context_expr) and node.optional_vars:
                tainted.update(n.id for n in ast.walk(node.optional_vars) if isinstance(n, ast.Name))
        if before == tainted:
            break
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _node_name(node.func)
        target = None
        if name == "write_json_atomic" and node.args:
            target = node.args[0]
        elif name in ("replace", "dump") and len(node.args) > 1:
            target = node.args[1]
        elif name in ("write_text", "write_bytes", "write") and isinstance(node.func, ast.Attribute):
            target = node.func.value
        elif name == "open":
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if isinstance(mode, ast.Constant) and any(c in str(mode.value) for c in "wa+"):
                target = node.args[0] if node.args else None
        if target is not None and flows(target):
            return node.lineno
    return None


check("aliased state writer is detected", _state_write_line(ast.parse('''
def planted(s):
    arbitrary = state_path(s)
    alias = arbitrary
    with open(alias, "w") as handle:
        json.dump({}, handle)
''').body[0]) is not None)
check("report artifact with locked state access is not a state writer", _state_write_line(ast.parse('''
def audit(s, record):
    update_json_locked(state_path(s), lambda state: state.update({"ok": True}))
    write_json_atomic(record + ".verification_audit.json", {})
''').body[0]) is None)

hooks_dir = os.path.join(os.path.dirname(__file__), "..", "hooks")
unlocked = []
for hook_name in sorted(os.listdir(hooks_dir)):
    if not hook_name.endswith(".py") or hook_name == "_hook_state.py":
        continue
    tree = ast.parse(io.open(os.path.join(hooks_dir, hook_name), encoding="utf-8").read())
    sources = set(STATE_PATH_NAMES)
    # Local path factories for per-session marker state (e.g. cc-routing).
    for factory in ast.walk(tree):
        if isinstance(factory, ast.FunctionDef) and any(
                isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.startswith("cc-routing-") for n in ast.walk(factory)):
            sources.add(factory.name)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        line = _state_write_line(fn, sources)
        if line:
            unlocked.append("%s:%d %s" % (hook_name, line, fn.name))
check("no hook writes a state file outside update_json_locked", not unlocked, "; ".join(unlocked))

# --- write fail-open -------------------------------------------------------
check("unserializable payload returns False, does not raise",
      write_json_atomic(os.path.join(d, "bad.json"), {"k": {1, 2}}) is False)
check("a failed write leaves no temp file",
      not [f for f in os.listdir(d) if f.startswith(".tmp_")], str(os.listdir(d)))

# --- round trip through both ------------------------------------------------
rt = os.path.join(d, "rt.json")
write_json_atomic(rt, {"skills_loaded": ["testing"], "n": 4})
check("salvage reads what atomic write produced",
      read_json_salvage(rt) == {"skills_loaded": ["testing"], "n": 4})

# --- the harness code set: one predicate, which the proof runner imports -------
is_code = getattr(_hook_state, "is_harness_code", None)
check("is_harness_code takes scripts and settings files, not docs, whatever the suffix case",
      is_code is not None and is_code(".claude/tools/a.py") and is_code(".claude/tools/B.PY")
      and is_code(".claude/settings.base.json") and not is_code(".claude/commands/a.md"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import harness_tests  # noqa: E402
check("the proof runner's _is_code is _hook_state.is_harness_code",
      is_code is not None and harness_tests._is_code is is_code)

# --- fire_once failure posture: default fires; on_failure=None lets a deny-capable caller pass ---
_real_update = _hook_state.update_json_locked
_hook_state.update_json_locked = lambda path, updater: (False, None)
try:
    check("fire_once on an unwritable state fires by default", _hook_state.fire_once("sess-fail0001", "k") is True)
    check("fire_once on an unwritable state returns the caller's on_failure",
          _hook_state.fire_once("sess-fail0001", "k", on_failure=None) is None)
finally:
    _hook_state.update_json_locked = _real_update

print("\n%d/%d checks pass" % (ran - len(failures), ran))
for f in failures:
    print("  FAIL " + f)
sys.exit(1 if failures else 0)
