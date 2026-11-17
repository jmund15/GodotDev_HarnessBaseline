"""Each Claude-client launcher's live init tool list equals its job kind's tool set.

The rows are stream-json system/init events captured from real fan-out runs, one per launcher and kind,
read from tests/fixtures/dispatch_kind_init_events.json (trimmed to type, subtype and tools). The
session transcripts hold no init event for these launchers, so the captured file is the source. If a
launcher ever hands a child tools its kind does not grant (or drops one), the recapture fails here:
argv is the whole enforcement, so this is its check.

A row may carry `stale` when a profile change landed while its launcher's plan could not run
(a plan quota exhausted): the missing-server direction is skipped and named, because only a
fresh capture can answer it, and the extra-server direction still runs.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
import tool_profiles as tp  # noqa: E402

SCHEMA_TOOL = "StructuredOutput"
# The captured rows: stream-json system/init events of live `sidecar_fanout -P` runs, trimmed to
# launcher, kind, type, subtype and tools (plus the codex row's denyApplied flag). A project transcript
# holds no such event for these launchers, so the captured file is the only source; the proof reads it.
FIXTURE = os.path.join(ROOT, ".claude", "tests", "fixtures", "dispatch_kind_init_events.json")
with open(FIXTURE, encoding="utf-8") as fh:
    RUNS = json.load(fh)["runs"]

failures = []
skipped = []
passed = 0


def check(name, want, got):
    global passed
    if want == got:
        passed += 1
    else:
        failures.append(f"{name}: want {want!r}, got {got!r}")


def split_tools(tools):
    builtins = {t for t in tools if not t.startswith("mcp__") and t != SCHEMA_TOOL}
    servers = {t.split("__")[1] for t in tools if t.startswith("mcp__")}
    return builtins, servers


def server_granted(server, granted):
    return any(server == g or server.endswith("_" + g) for g in granted)


runs = RUNS
launchers = {r["launcher"] for r in runs}
check("the fixture covers the four Claude-client launcher families", 4, len(launchers))

for run in runs:
    name = f"{run['launcher']} {run['kind']}"
    check(f"{name}: the event is a system init", ("system", "init"), (run["type"], run["subtype"]))
    profile, _spec, _keys = tp.resolve_kind(run["kind"], route="fanout", walk_mcp=False)
    builtins, servers = split_tools(run["tools"])
    check(f"{name}: built-ins equal the kind's", sorted(profile["builtins"]), sorted(builtins))
    check(f"{name}: every MCP server is one the kind grants", [],
          sorted(s for s in servers if not server_granted(s, profile["mcpServers"])))
    if run.get("stale"):
        skipped.append(f"{name}: {run['stale']}")
    else:
        check(f"{name}: every granted MCP server is present", [],
              sorted(g for g in profile["mcpServers"] if not any(server_granted(s, [g]) for s in servers)))
    check(f"{name}: the schema tool appears only on a reviewer",
          run["kind"] == "reviewer", SCHEMA_TOOL in run["tools"])

# A row cut after S5d (`denyApplied`) must also lack the tools its kind withholds. Rows cut before it
# still list ai-worker write_doc; recapture them with a live `-P` run and set the flag.
for run in runs:
    if run.get("denyApplied"):
        check(f"{run['launcher']} {run['kind']}: the init list lacks exactly the tools the kind withholds",
              sorted(tp.resolve_kind(run["kind"], route="fanout", walk_mcp=False)[0]["denyTools"]),
              sorted(t for t in ("mcp__ai-worker__write_doc",) if t not in run["tools"]))

for run in runs:
    if run["kind"] != "author":
        check(f"{run['launcher']} {run['kind']}: a non-author holds no write tool", [],
              sorted({"Edit", "Write", "Bash"} & set(run["tools"])))

# a planted drift is caught: an Edit slipped into a scout's list
planted = dict(runs[0], tools=runs[0]["tools"] + ["Edit"])
profile, _s, _k = tp.resolve_kind(planted["kind"], route="fanout", walk_mcp=False)
check("a planted extra Edit on a scout differs from its kind", False,
      sorted(profile["builtins"]) == sorted(split_tools(planted["tools"])[0]))

# a stale row skips only the missing-server direction; an extra server still fails it
stale_row = dict(runs[0], stale="planted", tools=runs[0]["tools"] + ["mcp__nosuch__tool"])
stale_profile, _s, _k = tp.resolve_kind(stale_row["kind"], route="fanout", walk_mcp=False)
_stale_builtins, stale_servers = split_tools(stale_row["tools"])
check("a stale row still fails when it carries a server the kind does not grant", ["nosuch"],
      sorted(s for s in stale_servers if not server_granted(s, stale_profile["mcpServers"])))
check("a stale row is named, never silently dropped", True, bool(skipped) or not any(
    r.get("stale") for r in runs))

if failures:
    print("\n".join("FAIL " + f for f in failures))
    print(f"FAIL test_tool_profiles_init_events ({passed} passed, {len(failures)} failed)")
    sys.exit(1)
for line in skipped:
    print("SKIP " + line)
print(f"PASS test_tool_profiles_init_events ({passed} cases, {len(skipped)} skipped)")
