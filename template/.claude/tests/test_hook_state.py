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
import _hook_state  # noqa: E402
from _hook_state import read_json_salvage, write_json_atomic  # noqa: E402

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


# A read that meets another hook's rename into place gets PermissionError for a moment; read as {}, the state's
# watcher vanished and Stop blocked on "no live watcher" while the watcher ran.
class _RenameInFlight:
    def __init__(self, denials):
        self.denials = denials

    def open(self, *args, **kwargs):
        if self.denials:
            self.denials -= 1
            raise PermissionError(13, "Permission denied", args[0])
        return io.open(*args, **kwargs)


shim = _RenameInFlight(2)
_hook_state.io = shim
try:
    got = read_json_salvage(held)
finally:
    _hook_state.io = io
check("a read that meets a rename in flight waits and reads the state", got == {"held": 1} and shim.denials == 0,
      repr(got))

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
check("a .cjs launcher under tools/ is harness code (the real input: godot_mcp_launch.cjs)",
      is_code is not None and is_code(".claude/tools/godot_mcp_launch.cjs")
      and is_code(".claude/tools/Godot_MCP_Launch.CJS") and is_code(".claude/tools/planted.cjs"))
check("non-code suffixes stay non-code: a .json fixture under tests, a plans .md",
      is_code is not None and not is_code(".claude/tests/fixture.json") and not is_code(".claude/plans/x.md"))

# --- fire_once failure posture: default fires; on_failure=None lets a deny-capable caller pass ---
_real_update = _hook_state.update_json_locked
_hook_state.update_json_locked = lambda path, updater: (False, None)
try:
    check("fire_once on an unwritable state fires by default", _hook_state.fire_once("sess-fail0001", "k") is True)
    check("fire_once on an unwritable state returns the caller's on_failure",
          _hook_state.fire_once("sess-fail0001", "k", on_failure=None) is None)
finally:
    _hook_state.update_json_locked = _real_update

# --- a once-key given back: a line a dispatcher dropped can be told again ----------------------------------------
# `fire_once` spends its key when it is asked; a dispatcher that then drops the line (a later hook denies the call)
# gives the keys it spent back, so the retry still carries the line.
_saved_dir = os.environ.get("HARNESS_HOOK_STATE_DIR")
os.environ["HARNESS_HOOK_STATE_DIR"] = tempfile.mkdtemp(prefix="hookstate_unspend_")
try:
    _sid = "sess-unspend1"
    _mark0 = _hook_state.spent_mark()
    _a = _hook_state.fire_once(_sid, "a")
    _mark1 = _hook_state.spent_mark()
    _b = _hook_state.fire_once_since_compaction(_sid, "b")
    _again = _hook_state.fire_once(_sid, "a")
    check("a key already spent is not journaled again", _a is True and _again is False
          and _hook_state.spent_mark() == _mark1 + 1)
    _hook_state.unspend(_mark1)
    check("unspend gives back the keys spent since the mark, in either bucket",
          _hook_state.fire_once_since_compaction(_sid, "b") is True)
    check("unspend leaves a key spent before the mark spent", _hook_state.fire_once(_sid, "a") is False)
    _hook_state.fire_once(_sid, "c")
    _stop = _hook_state.spent_mark()
    _hook_state.fire_once(_sid, "d")
    _hook_state.unspend(_mark0, _stop)
    check("unspend with a stop gives back only the keys before it",
          _hook_state.fire_once(_sid, "a") is True and _hook_state.fire_once(_sid, "d") is False)
    _hook_state.unspend(_hook_state.spent_mark())
    _hook_state.update_json_locked = lambda path, updater: (False, None)
    try:
        _hook_state.unspend(0)
        _raised = False
    except Exception:
        _raised = True
    finally:
        _hook_state.update_json_locked = _real_update
    check("unspend on an unwritable state never raises", not _raised)
finally:
    if _saved_dir is None:
        os.environ.pop("HARNESS_HOOK_STATE_DIR", None)
    else:
        os.environ["HARNESS_HOOK_STATE_DIR"] = _saved_dir

# --- one owner for every rename onto a file another process may hold ---------------------------
# Windows refuses a rename onto a path another process holds open (WinError 5/32). `replace_path` waits the
# reader out. This scan covers the class: every writer under tools/, hooks/ and scripts/ that replaces a state,
# record, inventory, cache, log, registry or vault file, whichever call reaches the syscall (os.replace,
# os.rename, shutil.move, Path.replace, PowerShell Move-Item/[IO.File]::Move, shell mv). A bare rename fails
# here unless its own line, or a comment line directly above it, carries `bare-rename: <reason>`. The reason
# sits at the site, so this proof names no file of another layer or project.
import re as _re  # noqa: E402
import shutil  # noqa: E402

CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASS_DIRS = ("tools", "hooks", "scripts")
OWNER_FILES = {"hooks/_hook_state.py"}
PY_RENAME_CALLS = {"os.replace", "os.rename", "shutil.move"}
PS_RENAME = _re.compile(r"\[(?:System\.)?IO\.File\]::Move\(|(?:^|[;\s])Move-Item\b")
SH_RENAME = _re.compile(r"^\s*mv\s")
RENAME_REASON = _re.compile(r"#.*\bbare-rename:\s*\S")


def _py_rename_sites(source):
    """(line, enclosing function) for every rename-shaped call in one Python source."""
    tree = ast.parse(source)
    owner = {}

    def label(node, current):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            if hasattr(child, "lineno"):
                owner[child.lineno] = name
            label(child, name)

    label(tree, "<module>")
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and "%s.%s" % (func.value.id, func.attr) in PY_RENAME_CALLS:
            sites.append((node.lineno, owner.get(node.lineno, "<module>")))
        elif isinstance(func, ast.Attribute) and func.attr in ("replace", "rename") \
                and len(node.args) == 1 and not node.keywords:
            # `str.replace` takes two or more arguments, so a one-argument call on any receiver is a Path rename.
            sites.append((node.lineno, owner.get(node.lineno, "<module>")))
    return sites


def _has_reason(lines, number, pattern=RENAME_REASON):
    """True when 1-based line `number`, or a comment-only line directly above it, states the reason."""
    if pattern.search(lines[number - 1]):
        return True
    above = lines[number - 2] if number >= 2 else ""
    return above.lstrip().startswith("#") and bool(pattern.search(above))


def _class_rename_sites(root):
    """["<relative path>:<line> <function>()", ...] for every bare rename under `root`'s class folders that
    states no reason."""
    found = {}
    for folder in CLASS_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, folder)):
            dirnames[:] = [n for n in dirnames if n not in ("__pycache__", "node_modules", "vendor")]
            for name in filenames:
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
                if rel in OWNER_FILES:
                    continue
                try:
                    text = io.open(os.path.join(dirpath, name), encoding="utf-8").read()
                except (OSError, UnicodeDecodeError):
                    continue
                sites = []
                if name.endswith(".py"):
                    try:
                        sites = _py_rename_sites(text)
                    except SyntaxError:
                        sites = []
                elif name.endswith(".ps1"):
                    sites = [(i + 1, "<file>") for i, line in enumerate(text.splitlines())
                             if PS_RENAME.search(line) and not line.lstrip().startswith("#")]
                elif name.endswith(".sh"):
                    sites = [(i + 1, "<file>") for i, line in enumerate(text.splitlines())
                             if SH_RENAME.search(line) and not line.lstrip().startswith("#")]
                lines = text.splitlines()
                for line, func in sites:
                    if not _has_reason(lines, line):
                        found.setdefault(rel, []).append("%s:%d %s()" % (rel, line, func))
    return sorted(site for sites in found.values() for site in sites)


_bare = _class_rename_sites(CLAUDE_DIR)
check("a rename onto a file another process may hold goes through replace_path, or states why not",
      not _bare, "; ".join(_bare))

# The scan on a planted tree: it reports each unexplained rename and passes each explained one.
_plant = tempfile.mkdtemp(prefix="rename_scan_")
try:
    for _rel, _body in {
        "tools/a.py": "import os\ndef save(t, p):\n    os.replace(t, p)\n"
                      "def keep(t, p):\n    os.replace(t, p)  # bare-rename: p is a fresh name\n"
                      "def text(s):\n    return s.replace('a', 'b')\n",
        "hooks/b.py": "import shutil\ndef move(a, b):\n    # bare-rename: b is proven absent\n"
                      "    shutil.move(a, b)\n    # a later, unexplained one\n    shutil.move(b, a)\n",
        "scripts/c.sh": "mv \"$a\" \"$b\"  # bare-rename: a fresh name\nmv \"$b\" \"$c\"\n# mv in a comment\n",
        "scripts/d.ps1": "Move-Item -LiteralPath $t -Destination $p -Force\n"
                         "# bare-rename:\n[IO.File]::Move($t, $p, $true)\n",
        "tools/e.py": "def a(doc, target):\n    doc.replace(target)\n"
                      "def b(src, dst):\n    src.rename(dst)\n"
                      "def c(s, x, y):\n    return s.replace(x, y)\n"
                      "def d(doc, target):\n    doc.replace(target)  # bare-rename: target is a fresh name\n",
        "hooks/_hook_state.py": "import os\ndef replace_path(source, target):\n    os.replace(source, target)\n",
    }.items():
        _path = os.path.join(_plant, _rel)
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with io.open(_path, "w", encoding="utf-8", newline="\n") as _fh:
            _fh.write(_body)
    _planted = _class_rename_sites(_plant)
finally:
    shutil.rmtree(_plant, ignore_errors=True)
check("the rename scan reports an unexplained site in each language and passes an explained one",
      _planted == ["hooks/b.py:6 move()", "scripts/c.sh:2 <file>()", "scripts/d.ps1:1 <file>()",
                   "scripts/d.ps1:3 <file>()", "tools/a.py:3 save()", "tools/e.py:2 a()", "tools/e.py:4 b()"],
      repr(_planted))


# --- one writer per shared JSONL append, or a stated reason ------------------------------------
# Concurrent sessions and sidecars append to the same logs. Windows emulates append mode with a seek
# then a write, so two unlocked appenders overwrite each other's tail and tear a line. Every append
# under tools/ goes through `_hook_state.append_jsonl_rotating`, or states on its line or the comment
# directly above it why only one process writes that file: `# bare-append: <reason>`. A binary append
# (a lock file, a byte log) is not a JSONL row and is out of this scan.
BARE_APPEND = _re.compile(r"#.*\bbare-append:\s*\S")


def _append_open_line(node):
    """Line of an append-mode text `open()` call, else None. `open(p, "a")` and `io.open(p, "a")`
    carry the mode second; `p.open("a")` carries it first. A mode with `b` is not a JSONL append."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id == "open":
        mode = node.args[1] if len(node.args) > 1 else next(
            (k.value for k in node.keywords if k.arg == "mode"), None)
    elif isinstance(func, ast.Attribute) and func.attr == "open":
        is_io_function = isinstance(func.value, ast.Name) and func.value.id in ("io", "builtins")
        mode = (node.args[1] if len(node.args) > 1 else None) if is_io_function else (
            node.args[0] if node.args else None)
    else:
        return None
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return None
    if "a" not in mode.value or "b" in mode.value:
        return None
    return node.lineno


def _bare_appends(root):
    """["tools/<file>:<line>", ...] for every append under `root`'s tools folder that states no reason."""
    found = []
    folder = os.path.join(root, "tools")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py"):
            continue
        try:
            text = io.open(os.path.join(folder, name), encoding="utf-8").read()
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            line = _append_open_line(node)
            if line and not _has_reason(lines, line, BARE_APPEND):
                found.append("tools/%s:%d" % (name, line))
    return sorted(found)


_bare_appends_found = _bare_appends(CLAUDE_DIR)
check("every tools/ append takes the file lock, or states why one writer suffices",
      not _bare_appends_found, "; ".join(_bare_appends_found))

# The scan on a planted tree: an unexplained append in each open form is reported; a marked one, a
# binary mode and a truncating write pass.
_append_plant = tempfile.mkdtemp(prefix="append_scan_")
try:
    os.makedirs(os.path.join(_append_plant, "tools"))
    with io.open(os.path.join(_append_plant, "tools", "x.py"), "w", encoding="utf-8", newline="\n") as _fh:
        _fh.write("import io\n"
                  "def a(p):\n    with open(p, 'a') as fh:\n        fh.write('x')\n"
                  "def b(p):\n    # bare-append: one watcher owns it\n"
                  "    with open(p, 'a') as fh:\n        fh.write('x')\n"
                  "def c(p):\n    with p.open('a') as fh:\n        fh.write('x')\n"
                  "def d(p):\n    with io.open(p, 'a+b') as fh:\n        fh.write(b'x')\n"
                  "def e(p):\n    with open(p, 'w') as fh:\n        fh.write('x')\n")
    _append_planted = _bare_appends(_append_plant)
finally:
    shutil.rmtree(_append_plant, ignore_errors=True)
check("the append scan reports an unexplained site in each open form and passes a marked one",
      _append_planted == ["tools/x.py:10", "tools/x.py:3"], repr(_append_planted))


# --- three real writers land their write on a target a reader holds open -----------------------
def _held_target(path, ready, release):
    """Hold `path` open like a watcher mid-read until `release` is set."""
    with io.open(path, "rb"):
        ready.set()
        release.wait(10)


def drive_held(seed_path, write):
    """Run `write()` while another thread holds `seed_path` open; return what the write raised, if anything."""
    ready, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_held_target, args=(seed_path, ready, release))
    holder.start()
    ready.wait(5)
    raised = []

    def run():
        try:
            write()
        except BaseException as exc:  # noqa: BLE001 - the proof reports what a writer did
            raised.append(exc)

    writer = threading.Thread(target=run)
    writer.start()
    time.sleep(0.25)  # a corrected writer is inside replace_path's wait by now
    release.set()
    writer.join(15)
    holder.join(5)
    return raised[0] if raised else None


sys.path.insert(0, os.path.join(CLAUDE_DIR, "tools"))
import worklog  # noqa: E402
import dispatch_result  # noqa: E402
import sidecar_fanout  # noqa: E402

vault = os.path.join(d, "vault.md")
with io.open(vault, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("old\n")
_vault_err = drive_held(vault, lambda: worklog.write_atomic(vault, "new\n"))
check("the worklog vault writer lands on a held target",
      _vault_err is None and io.open(vault, encoding="utf-8").read() == "new\n", repr(_vault_err))

out_dir = os.path.join(d, "fanout")
os.makedirs(out_dir)
deliverable = dispatch_result.deliverable_path(out_dir, "wr1")
with io.open(str(deliverable), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("old\n")
_deliverable_err = drive_held(str(deliverable),
                              lambda: dispatch_result.write_deliverable(out_dir, "wr1", "new\n"))
check("the dispatch deliverable writer lands on a held target",
      _deliverable_err is None and io.open(str(deliverable), encoding="utf-8").read() == "new\n",
      repr(_deliverable_err))

inventory = os.path.join(d, "fanout-x.inventory.json")
with io.open(inventory, "w", encoding="utf-8") as fh:
    fh.write('{"old":1}')
_inventory_err = drive_held(inventory, lambda: sidecar_fanout.save_fanout_inventory(
    inventory, {"schemaVersion": 1, "jobs": []}))
check("the fan-out inventory writer lands on a held target",
      _inventory_err is None and json.load(io.open(inventory, encoding="utf-8"))["schemaVersion"] == 1,
      repr(_inventory_err))

# --- importing the library reads no local settings listing --------------------
# The proof runner keys a pass on every directory a proof lists. A listing of `.claude` at import time put
# each ignored local file into the key of every proof that imports this module (or the pre-edit guard that
# reads the overlay list), so a session's pass never matched the close checkout's.
import subprocess  # noqa: E402

_hooks = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
_claude = os.path.normcase(os.path.abspath(os.path.join(_hooks, "..")))
_probe = (
    "import os, sys\n"
    "seen = []\n"
    "def hook(event, args):\n"
    "    if event in ('os.listdir', 'os.scandir') and args and args[0] is not None:\n"
    "        seen.append(os.path.normcase(os.path.abspath(os.fsdecode(args[0]))))\n"
    "sys.addaudithook(hook)\n"
    "sys.path.insert(0, %r)\n"
    "import _hook_state, composed_output_guard\n"
    "print(repr(seen))\n" % os.path.abspath(_hooks)
)
_run = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True)
check("importing _hook_state and composed_output_guard lists no `.claude` directory",
      _run.returncode == 0 and _claude not in eval(_run.stdout or "[None]"), (_run.stdout + _run.stderr)[-300:])

# Concurrent JSONL appends: Windows' CRT emulates O_APPEND with a seek then a write, so two hooks appending
# at once overwrite each other's tail and leave a torn row that makes the whole log unreadable.
_append_dir = tempfile.mkdtemp()
_append_log = os.path.join(_append_dir, "race.jsonl")
_writer = (
    "import sys\n"
    "sys.path.insert(0, %r)\n"
    "import _hook_state\n"
    "for i in range(120):\n"
    "    _hook_state.append_jsonl_rotating(%r, [{'w': sys.argv[1], 'i': i, 'pad': 'x' * (2500 + (i * 37) %% 900)}],\n"
    "                                      max_bytes=None)\n" % (os.path.abspath(_hooks), _append_log)
)
_writers = [subprocess.Popen([sys.executable, "-c", _writer, str(n)]) for n in range(8)]
for _proc in _writers:
    _proc.wait()
_rows, _torn = 0, 0
with io.open(_append_log, encoding="utf-8") as _fh:
    for _line in _fh:
        _rows += 1
        try:
            json.loads(_line)
        except ValueError:
            _torn += 1
check("eight concurrent appenders leave every row whole", _rows == 960 and _torn == 0,
      "rows %d of 960, torn %d" % (_rows, _torn))

# The append takes the file's lock itself, which is not re-entrant: a caller holding that lock around it waits
# out the lock timeout and drops its rows. Drive the real caller that once did.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import review_ledger  # noqa: E402
from pathlib import Path  # noqa: E402

_ledger = Path(tempfile.mkdtemp()) / "review_ledger.jsonl"
_started = time.time()
_landed = review_ledger._append_rows(_ledger, [{"id": "r1"}])
check("the review ledger's append lands its row without waiting out a lock",
      _landed is True and time.time() - _started < 1.0 and _ledger.read_text(encoding="utf-8").count("\n") == 1,
      "returned %r after %.1fs" % (_landed, time.time() - _started))

print("\n%d/%d checks pass" % (ran - len(failures), ran))
for f in failures:
    print("  FAIL " + f)
sys.exit(1 if failures else 0)
