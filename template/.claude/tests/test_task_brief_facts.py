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
import math
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


NOTE = "draw ratios compare models within one plan (list-price ratio, one measured point); across plans compare room"
ROW_SHAPE = re.compile(r"\| draw \d+\.\d{2}x \| \S+ \| \S+ \| (fits|over by|window unjudged)")
PLANT_LIMITS = {"contextTokens": 200000, "maxOutputTokens": 32000}


def planted_facts(module, brief_path, routes, pay, seat="alpha"):
    """The sub-hook's `_facts` over planted route rows: `lean_candidates` stubbed to `routes` and `pay`, the real
    brief file as the reading, and the ladder stubbed out so only the table's own cells are under test."""
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import provider_bands
    import model_registry
    models, transports = [], {"alpha": {"costModel": "plan-quota"}, "beta": {"costModel": "plan-quota"},
                              "ds": {"costModel": "marginal-usd"}}
    for transport, alias, out in [("alpha", "a-top", 10), ("alpha", "a-half", 5), ("beta", "b-only", 2),
                                  ("ds", "d-pay", 4)]:
        models.append({"transport": transport, "id": alias, "alias": alias, "roles": ["executor"],
                       "limits": PLANT_LIMITS, "price": {"cacheMissPer1M": out / 5, "outputPer1M": out,
                                                         "cacheHitPer1M": out / 50}})
    data = {"transports": transports, "models": models}
    got = {"tier": "executor", "seat": seat, "refused": None, "supplementary": {}, "routes": routes,
           "payOptions": pay}
    saved_lean, saved_ladder = provider_bands.lean_candidates, module._ladder
    provider_bands.lean_candidates = lambda tier, session, data=None, read=None, **kw: got
    module._ladder = lambda _mr, _data: {}
    try:
        return module._facts({"tier": "executor"}, brief_path, data, seat)
    finally:
        provider_bands.lean_candidates = saved_lean
        module._ladder = saved_ladder


def planted_row(alias, plans=None):
    return {"alias": alias, "id": alias, "cost": None, "plans": plans or [], "minus": None, "effort": None,
            "probation": None, "supplementary": False}


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
    os.environ.update(HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir)
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
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import model_registry as registry_oracle
    registry_data = registry_oracle.load()

    def plan_alias(alias):
        return registry_oracle.cost_model_for(registry_oracle.row_for_model(alias, registry_data)["transport"],
                                              registry_data) == "plan-quota"

    plan_aliases = {a for a in expected if plan_alias(a)}
    pay_aliases = expected - plan_aliases
    case("each alias row carries a draw cell on a plan route, and a spend cell with USD on a pay-per-token route",
         expected and all(any(ROW_SHAPE.search(line) for line in alias_lines(text, alias)) for alias in plan_aliases)
         and all(any("| spend " in line and "$" in line for line in alias_lines(text, alias))
                 for alias in pay_aliases))
    case("each alias row quotes its ladder ± cell's first + and first − clause",
         expected and all(any('+ "' in line and '− "' in line for line in alias_lines(text, alias))
                          for alias in expected))
    case("a plan row prints no USD figure, and every plan row's ratio is labelled draw",
         plan_aliases and all(alias_lines(text, alias) and all("$" not in line and "draw " in line
                                                               for line in alias_lines(text, alias))
                              for alias in plan_aliases))
    case("the draw note prints once per table", text.count(NOTE) == 1)
    case("a cold capacity cache prints room unknown on a route header, and no row names a room",
         "room unknown" in text and not any("room" in line for alias in expected for line in alias_lines(text, alias)))
    case("a cold capacity cache runs no subprocess and no network call", bool(text) and calls == [])
    big_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "big_brief.md"),
                     BRIEF + "filler " * 200_000)
    big_text = ""
    if module is not None and callable(getattr(module, "process", None)):
        with planted_env(CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache):
            big_text = (module.process(written(big_path, BRIEF + "filler " * 200_000, session="big-sess"))
                        or {}).get("context") or ""
    case("a brief over a row's window prints its excess tokens", "over by" in big_text)
    named_text = "y" * 100_000
    named_file = plant(os.path.join(tmp, "repo", "named_reference.md"), named_text)
    named_brief_text = BRIEF + "Read `%s` before you start.\n" % named_file.replace("\\", "/")
    named_brief = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "named_brief.md"), named_brief_text)
    reading_named = math.ceil((len(named_brief_text) + len(named_text)) / 2.5)
    named_out = ""
    if module is not None and callable(getattr(module, "process", None)):
        with planted_env(CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache):
            named_out = (module.process(written(named_brief, named_brief_text, session="named-sess"))
                         or {}).get("context") or ""
    case("the window fit reads the brief plus the file it names, not the brief alone",
         bool(named_out) and all(any("(%s of " % "{:,}".format(reading_named) in line
                                     for line in alias_lines(named_out, alias)) for alias in expected))
    cited_brief_text = BRIEF + "Search `%s:10-20` for the rows.\n" % named_file.replace("\\", "/")
    cited_brief = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "cited_brief.md"), cited_brief_text)
    lower_cited = math.ceil(len(cited_brief_text) / 2.5)
    upper_cited = math.ceil((len(cited_brief_text) + len(named_text)) / 2.5)
    cited_out = ""
    if module is not None and callable(getattr(module, "process", None)):
        with planted_env(CLAUDE_CODE_TRANSPORT=SEAT, PROVIDER_CAPACITY_CACHE=cold_cache):
            cited_out = (module.process(written(cited_brief, cited_brief_text, session="cited-sess"))
                         or {}).get("context") or ""
    case("a line-cited file is a span: the cell prints the lower number, then the upper if read whole",
         bool(cited_out) and all(any("(%s of " % "{:,}".format(lower_cited) in line
                                     and "; %s if read whole)" % "{:,}".format(upper_cited) in line
                                     for line in alias_lines(cited_out, alias)) for alias in expected))
    if module is not None and callable(getattr(module, "_fit", None)):
        import model_registry as real_registry
        login = os.path.join(tmp, "login")
        plant(os.path.join(login, "settings.json"), json.dumps({"autoCompactWindow": 500000}))
        real = real_registry.load()
        opus = real_registry.resolve("opus", real)["id"]
        with planted_env(CLAUDE_CONFIG_DIR=login):
            try:
                cell = module._fit(module._owners()[1], opus, 1000, 2000, real, "anthropic")
            except TypeError as err:
                cell = repr(err)
        case("the cell's point is the row's compaction point, not its usable window",
             cell == "fits (1,000 of 467,000; 2,000 if read whole)")
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

    if module is not None and callable(getattr(module, "_facts", None)):
        plant_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "plant_brief.md"), BRIEF)
        routes = {"alpha": [planted_row("a-top", [{"plan": "alpha-plan", "room": 0.4}]),
                            planted_row("a-half", [{"plan": "alpha-plan", "room": 0.4}])],
                  "beta": [planted_row("b-only", [{"plan": "beta-plan", "room": None}])]}
        pay = [{"alias": "d-pay", "id": "d-pay", "transport": "ds", "cost": None, "plans": None, "minus": None,
                "effort": None, "probation": None, "supplementary": False}]
        planted = planted_facts(module, plant_path, routes, pay)
        rows = {alias: alias_lines(planted, alias) for alias in ("a-top", "a-half", "b-only", "d-pay")}
        case("ratios restart per route: the cheapest plan row on each route reads draw 1.00x, others against it",
             bool(rows["a-top"]) and "| draw 2.00x |" in rows["a-top"][0] and "| draw 1.00x |" in rows["a-half"][0]
             and "| draw 1.00x |" in rows["b-only"][0])
        case("a plan row prints no USD in the planted table", all(rows[a] and "$" not in rows[a][0]
                                                                  for a in ("a-top", "a-half", "b-only")))
        case("a pay-per-token row prints spend with its USD figure",
             bool(rows["d-pay"]) and "| spend " in rows["d-pay"][0] and "$" in rows["d-pay"][0])
        case("each route header names its plan room once, with its route",
             "route alpha (plan, session route): alpha-plan room 0.40" in planted
             and "route beta (plan): beta-plan room unknown" in planted and planted.count("alpha-plan room") == 1)
        case("the planted table prints the draw note once and names no room on a row",
             planted.count(NOTE) == 1 and not any("room" in line for a in rows for line in rows[a]))
    else:
        for label in ("ratios restart per route: the cheapest plan row on each route reads draw 1.00x",
                      "a plan row prints no USD in the planted table",
                      "a pay-per-token row prints spend with its USD figure",
                      "each route header names its plan room once, with its route",
                      "the planted table prints the draw note once and names no room on a row"):
            case(label, False)
    if module is not None and callable(getattr(module, "process", None)):
        sys.path.insert(0, HOOKS)
        import _hook_state
        sess = "brief-table-sess"
        arch_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "arch_brief.md"),
                          BRIEF.replace('"tier": "executor"', '"tier": "architect"'))
        wide_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "wide_brief.md"),
                          BRIEF + "filler " * 200_000)
        with planted_env(HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir, CLAUDE_CODE_TRANSPORT=SEAT,
                         PROVIDER_CAPACITY_CACHE=cold_cache), no_probe_spy([]):
            first = (module.process(written(brief_path, BRIEF, session=sess)) or {}).get("context") or ""
            second = (module.process(written(brief_path, BRIEF, session=sess)) or {}).get("context") or ""
            bigger_path = plant(os.path.join(tmp, "repo", ".claude", "scratch", "ocd", "bigger_brief.md"),
                                BRIEF + "filler " * 500)
            bigger = (module.process(written(bigger_path, "", session=sess)) or {}).get("context") or ""
            wide =(module.process(written(wide_path, "", session=sess)) or {}).get("context") or ""
            arch = (module.process(written(arch_path, "", session=sess)) or {}).get("context") or ""
            _hook_state.clear_compaction_keys(sess)
            after = (module.process(written(brief_path, BRIEF, session=sess)) or {}).get("context") or ""
        case("the first brief of a tier in a session prints the full table", "model | draw or spend" in first)
        case("a second brief of that tier in the session prints one short line, not the table",
             "table as earlier this session" in second and "model | draw or spend" not in second
             and "reading" in second and len(second) < len(first) / 2)
        case("a bigger reading whose rows all still fit prints no fit row: only a verdict change is news",
             "fits as earlier" in bigger and "fit changed" not in bigger and "table as earlier this session" in bigger)
        case("a short line names the fit of a row whose fit changed: a wider reading reports the excess",
             "over by" in wide and "table as earlier this session" in wide and "model | draw or spend" not in wide)
        case("a brief of a new tier in the session prints its full table",
             "model | draw or spend" in arch and "table as earlier" not in arch)
        case("after a compaction the executor brief prints its full table again",
             "model | draw or spend" in after and "table as earlier" not in after)
    if text:
        print("real executor brief table: %d bytes (target under 1536; measured, not gated)" % len(text.encode("utf-8")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for label in failures:
        print("  FAIL " + label)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
