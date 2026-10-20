#!/usr/bin/env python3
"""Re-runnable proof for hooks/task_brief_facts.py, the post-edit facts a `task` brief carries.

A Write or Edit that leaves a file holding a ```task block gets one advisory context line per
row claiming the block's tier: route, ladder cost, each paying plan's room (`unknown` on a cold
capacity cache, read with no probe) and the brief's size fit on that row's window. The sub-hook
never denies. The alias oracle is `model_registry.py for-role <tier>`, run as a subprocess.

The payload helper `written()` is copied from tests/test_post_edit_dispatch.py's fixtures.
State is redirected with HOME/USERPROFILE/HARNESS_HOOK_STATE_DIR; the capacity cache is planted
cold through PROVIDER_CAPACITY_CACHE.

    python3 .claude/tests/test_task_brief_facts.py
"""
import contextlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(CLAUDE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
TOOLS = os.path.join(CLAUDE, "tools")
SUB = os.path.join(HOOKS, "task_brief_facts.py")
DISPATCH = os.path.join(HOOKS, "post_edit_dispatch.py")
REGISTRY = os.path.join(TOOLS, "model_registry.py")
SEAT = "anthropic-console"
TAG = "[task-brief-facts]"

BRIEF = """# Planted slice brief

Execute the planted slice and report the result.

```task
{"tier": "executor", "check": ["python3 .claude/tests/test_x.py"], "writeSet": [".claude/tools/x.py"]}
```
"""

EMPTY_CHECK_BRIEF = """# Planted slice brief

```task
{"tier": "executor", "check": []}
```
"""


def written(path, content, session="post0001", cwd=None, tool="Write"):
    payload = {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": session,
               "tool_input": {"file_path": path, "content": content},
               "tool_response": {"filePath": path}}
    if cwd:
        payload["cwd"] = cwd
    return payload


def plant(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def context(stdout):
    if not stdout or stdout == "{}":
        return ""
    specific = (json.loads(stdout).get("hookSpecificOutput") or {})
    assert specific.get("hookEventName") == "PostToolUse", specific
    return specific.get("additionalContext") or ""


def run_dispatch(payload, env):
    r = subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=120, env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def load_module():
    """The sub-hook module, or None while the file is absent (each case then reports FAIL)."""
    if not os.path.exists(SUB):
        return None
    spec = importlib.util.spec_from_file_location("task_brief_facts_probe", SUB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_chain():
    spec = importlib.util.spec_from_file_location("post_edit_dispatch_probe", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.CHAIN)


@contextlib.contextmanager
def planted_env(**values):
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def no_probe_spy(calls):
    """Records, and refuses, every subprocess spawn and socket open made while it is active."""
    def spawn(*args, **kwargs):
        calls.append(("subprocess", args[:1]))
        raise RuntimeError("planted: subprocess call on a cold cache")

    def connect(*args, **kwargs):
        calls.append(("socket", args[:1]))
        raise RuntimeError("planted: network call on a cold cache")

    saved = (subprocess.Popen, socket.socket, socket.create_connection)
    subprocess.Popen = spawn
    socket.socket = connect
    socket.create_connection = connect
    try:
        yield
    finally:
        subprocess.Popen, socket.socket, socket.create_connection = saved


def registry_aliases(env):
    """Aliases `model_registry.py for-role executor` lists: the row lines, never the `bash` launch lines."""
    r = subprocess.run([sys.executable, REGISTRY, "for-role", "executor"], capture_output=True,
                       text=True, timeout=120, env=env, encoding="utf-8", errors="replace")
    found = set()
    for line in (r.stdout or "").splitlines():
        match = re.match(r"^  (\S+)\s+\S+\s+\S+\s+effort ", line)
        if match:
            found.add(match.group(1))
    return found


def alias_lines(text, alias):
    return [line for line in text.splitlines() if re.match(r"^\s*%s\s" % re.escape(alias), line)]


def main():
    tmp = tempfile.mkdtemp(prefix="task_brief_facts_")
    try:
        return _main(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _main(tmp):
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    cold_cache = os.path.join(tmp, "no-such-capacity-cache.json")
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8",
               CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache)
    cases = []

    def case(label, ok):
        cases.append((label, bool(ok)))

    brief_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "slice3_brief.md"), BRIEF)
    expected = registry_aliases(env)
    case("the registry oracle lists at least one executor row", expected)

    module = load_module()
    case("hooks/task_brief_facts.py exists and loads", module is not None)
    calls = []
    result = None
    if module is not None and callable(getattr(module, "process", None)):
        with planted_env(CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache), no_probe_spy(calls):
            result = module.process(written(brief_path, BRIEF))
    text = (result or {}).get("context") or ""

    case("a planted executor brief yields a context", text.startswith(TAG))
    case("the context names every alias for-role executor lists",
         expected and all(alias_lines(text, alias) for alias in expected))
    case("each alias line carries its ladder cost and a plan room",
         expected and all(any("cost " in line and "room " in line for line in alias_lines(text, alias))
                          for alias in expected))
    case("a cold capacity cache prints room unknown", "room unknown" in text)
    case("a cold capacity cache runs no subprocess and no network call", bool(text) and calls == [])
    big_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "big_brief.md"),
                     BRIEF + "filler " * 200_000)
    big_text = ""
    if module is not None and callable(getattr(module, "process", None)):
        with planted_env(CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache):
            big_text = (module.process(written(big_path, BRIEF + "filler " * 200_000)) or {}).get("context") or ""
    case("a brief over a row's window prints its excess tokens", "over by" in big_text)
    unquoted = re.sub(r'"[^"]*"', "", text).lower()
    case("the context holds no pick words (pick, recommend, use ) outside quoted row text",
         bool(text) and not any(word in unquoted for word in ("pick", "recommend", "use ")))

    plain_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "plain.md"), "# no fence\n")
    bad = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "empty_check.md"), EMPTY_CHECK_BRIEF)
    if module is not None and callable(getattr(module, "process", None)):
        with planted_env(CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache):
            case("a write without a task block yields None",
                 module.process(written(plain_path, "# no fence\n")) is None)
            defect = (module.process(written(bad, EMPTY_CHECK_BRIEF)) or {}).get("context") or ""
            case("a malformed task block yields a context naming the defect",
                 defect.startswith(TAG) and "empty check" in defect)
        elsewhere = plant(os.path.join(tmp, "repo", "plans", "doc_with_fence.md"), BRIEF)
        with planted_env(CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache):
            case("a file outside the scratch directory that holds a task block yields None",
                 module.process(written(elsewhere, BRIEF)) is None)
    else:
        case("a write without a task block yields None", False)
        case("a malformed task block yields a context naming the defect", False)

    case("the sub-hook is registered in post_edit_dispatch.py's chain",
         "task_brief_facts" in load_chain())

    rc, out, err = run_dispatch(written(brief_path, BRIEF, session="taskfacts"), env)
    ctx = context(out)
    case("post_edit_dispatch.py fires the planted brief through its chain", rc == 0 and TAG in ctx)
    case("post_edit_dispatch.py names every for-role executor alias",
         expected and all(alias_lines(ctx, alias) for alias in expected))

    rc, out, err = run_dispatch(written(bad, EMPTY_CHECK_BRIEF, session="taskfactsbad"), env)
    ctx = context(out)
    case("a malformed brief through the dispatcher is advisory: exit 0, names the defect, never denies",
         rc == 0 and TAG in ctx and "empty check" in ctx and "permissionDecision" not in out)

    rc, out, err = run_dispatch(written(plain_path, "# no fence\n", session="taskfactsplain"), env)
    case("a write without a task block adds no task-brief-facts line through the dispatcher",
         rc == 0 and TAG not in context(out))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for label in failures:
        print("  FAIL " + label)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
