#!/usr/bin/env python3
"""Proof for the transport policy of tools/tool_profiles.py: which MCP schemas a sidecar child loads, by route.

The decision under test: the same kind resolves to a different MCP delivery on each route, because the
client delivers a deferred schema on one route and not on another.
  - anthropic: schemas are deferred behind ToolSearch (ToolSearch joins the built-ins);
  - codex, deepseek, opencode, go: no probe shows a deferred schema reaching the model, so the kind's per-tool
    allowlist becomes a denial of every other tool of the server;
  - local: the ai-worker server is not granted (the child holds the GPU ai-worker's default reader needs).
A resolution with no transport, and a route the table names no policy for, is the profile the table gave
before. The planted config keeps the cases off this machine's registrations.

    python3 .claude/tests/test_tool_profiles_transport.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
import tool_profiles as tp  # noqa: E402

CANDIDATES = [
    ("ai-worker", {"command": "aiw"}),
    ("git", {"command": "gr"}),
    ("plugin:semantic-search:semantic-search", {"command": "node"}),
]
CFG = tempfile.mkdtemp(prefix="tool_profiles_transport_")
with open(os.path.join(CFG, ".claude.json"), "w", encoding="utf-8", newline="\n") as _fh:
    json.dump({"mcpServers": dict(CANDIDATES), "projects": {}}, _fh)

AIW = "mcp__ai-worker__"
HELD_BACK = [AIW + t for t in ("extract_session_chat", "get_stats", "list_models")]
FAIL = 0
RAN = 0


def check(label, expected, actual):
    global FAIL, RAN
    RAN += 1
    if actual == expected:
        print("OK   %s" % label)
    else:
        print("FAIL %s: expected %r, got %r" % (label, expected, actual))
        FAIL = 1


def resolve(kind, transport, tools=""):
    profile, _spec, _keys = tp.resolve_kind(kind, route="launcher", transport=transport, tools_csv=tools,
                                            candidates=CANDIDATES)
    return profile


def attempt(fn):
    try:
        return "ok:%s" % fn()
    except Exception as exc:  # noqa: BLE001
        return "error:%s" % exc


# --- anthropic defers: ToolSearch joins the built-ins, nothing is denied beyond the row
for transport in ("anthropic", "anthropic-secondary", "anthropic-console"):
    p = resolve("scout", transport)
    check("%s: a kind with MCP servers gets ToolSearch" % transport, True, "ToolSearch" in p["builtins"])
    check("%s: the profile says its MCP is deferred" % transport, "deferred", p.get("mcpDelivery"))
check("anthropic: the built-ins keep the row's own, ToolSearch added",
      ["Read", "Glob", "Grep", "Bash", "ToolSearch"], resolve("scout", "anthropic")["builtins"])
check("anthropic: the denial stays the row's own", ["mcp__ai-worker__write_doc"],
      resolve("scout", "anthropic")["denyTools"])
check("anthropic: a kind with no MCP servers has nothing to defer, so no ToolSearch", ["Read", "Grep", "Bash"],
      resolve("gate", "anthropic")["builtins"])
check("anthropic: such a kind records no deferral", None, resolve("gate", "anthropic").get("mcpDelivery"))

# --- the proxied routes allowlist: every ai-worker tool the kind does not name is denied
for transport in ("codex", "deepseek", "opencode", "go"):
    p = resolve("author", transport)
    check("%s: author withholds the ai-worker tools its grant does not name" % transport,
          sorted(HELD_BACK + [AIW + "read_web"]), sorted(p["denyTools"]))
    check("%s: author keeps the tools its grant names" % transport, [],
          [t for t in (AIW + "read_files", AIW + "write_doc", AIW + "check_local_queue") if t in p["denyTools"]])
    check("%s: no ToolSearch, no deferral record" % transport, (False, None),
          ("ToolSearch" in p["builtins"], p.get("mcpDelivery")))
p = resolve("scout", "codex")
check("codex: a read-only kind keeps its own write_doc denial and adds the unnamed tools",
      sorted(HELD_BACK + [AIW + "write_doc"]), sorted(p["denyTools"]))
check("codex: no tool is denied twice", len(p["denyTools"]), len(set(p["denyTools"])))
check("codex: a server with no allowlist is not narrowed (semantic-search, git)", [],
      [t for t in p["denyTools"] if "ai-worker" not in t])
p = resolve("author", "codex", "-mcp:ai-worker")
check("codex: a server the job removed has no tools left to deny", [], p["denyTools"])

# --- an explicit +mcp:<server> lifts that server's allowlist for the job (server-level); the default grant stays narrowed
for transport in ("codex", "go"):
    p = resolve("author", transport, "+mcp:ai-worker")
    check("%s: author asking for ai-worker by name holds all its tools" % transport, [], p["denyTools"])
    p = resolve("scout", transport, "+mcp:ai-worker")
    check("%s: a read-only kind asking for it keeps only its own row's denial" % transport,
          [AIW + "write_doc"], p["denyTools"])
    p = resolve("author", transport, "+mcp:git")
    check("%s: asking for another server leaves ai-worker narrowed" % transport,
          sorted(HELD_BACK + [AIW + "read_web"]), sorted(p["denyTools"]))
check("anthropic: the lift adds nothing, deferral stays", (["mcp__ai-worker__write_doc"], "deferred"),
      (resolve("scout", "anthropic", "+mcp:ai-worker")["denyTools"],
       resolve("scout", "anthropic", "+mcp:ai-worker").get("mcpDelivery")))

# --- local: ai-worker is not granted
p = resolve("author", "local")
check("local: the default grant leaves ai-worker out", ["semantic-search"], p["mcpServers"])
check("local: ai-worker has no registration in the profile", ["plugin:semantic-search:semantic-search"],
      sorted(p["mcpRegistrations"].values()))
check("local: nothing of ai-worker is left to deny", [], p["denyTools"])
check("local: asking for the server by name refuses",
      True, attempt(lambda: resolve("author", "local", "+mcp:ai-worker")).startswith("error:")
      and "ai-worker" in attempt(lambda: resolve("author", "local", "+mcp:ai-worker")))
check("local: a read-only kind drops it too", ["semantic-search", "git"], resolve("scout", "local")["mcpServers"])

# --- an internal kind (a benchmark arm or its review seat) measures a tool frame, so no route policy touches it
for kind, route, tools in (("arm", "anthropic", "Read,Glob,Grep"), ("bench-review", "anthropic", ""),
                           ("bench-review", "codex", ""), ("arm", "local", "Read,Glob,Grep")):
    p, _spec, _keys = tp.resolve_kind(kind, route="launcher", transport=route, tools_csv=tools, candidates=CANDIDATES)
    row = tp.load_table()["kinds"][kind]
    check("%s on %s keeps the row's own frame" % (kind, route),
          (None, row.get("denyTools") or [], row["mcpServers"]),
          (p.get("mcpDelivery"), p["denyTools"], p["mcpServers"]))
    check("%s on %s gets no ToolSearch" % (kind, route), False, "ToolSearch" in p["builtins"])

# --- git instructions: the resolver owns the switch. A non-internal child with no Bash gets gitInstructions "off";
# an internal kind (an arm measures a tool frame) never does, whatever its built-ins. Every public row holds Bash,
# so a job that drops it with -Bash is the child with no shell.
for transport in (None, "anthropic", "codex", "local"):
    check("%r: a scout that drops its shell has its git instructions off" % (transport,), "off",
          resolve("scout", transport, "-Bash").get("gitInstructions"))
    check("%r: a scout holds a shell, so the field is absent" % (transport,), None,
          resolve("scout", transport).get("gitInstructions"))
    check("%r: an author holds a shell, so the field is absent" % (transport,), None,
          resolve("author", transport).get("gitInstructions"))
check("a gate holds a shell", None, resolve("gate", "anthropic").get("gitInstructions"))
for kind, route, tools in (("arm", "anthropic", "Read,Glob,Grep"), ("arm", "codex", "Read,Glob,Grep"),
                           ("bench-review", "anthropic", ""), ("bench-score", "anthropic", "")):
    p, _spec, _keys = tp.resolve_kind(kind, route="launcher", transport=route, tools_csv=tools, candidates=CANDIDATES)
    check("%s on %s: an internal kind keeps its frame, so no git switch" % (kind, route), None,
          p.get("gitInstructions"))

# --- no policy: the profile the table gave before
for transport in (None, "", "antigravity", "opencode-cli"):
    p = resolve("author", transport)
    check("transport %r: author is unchanged" % (transport,), (["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
                                                                 ["semantic-search", "ai-worker"], [], None),
          (p["builtins"], p["mcpServers"], p["denyTools"], p.get("mcpDelivery")))
check("an unregistered transport refuses rather than resolving the old profile", True,
      attempt(lambda: resolve("author", "nosuch")).startswith("error:"))

# --- the table: every allowlisted name is a tool of its server's catalog, and every policy names a known mode
table = tp.load_table()
catalog = table["mcpServerTools"]
check("each kind allowlist names a server the kind holds, with tools the catalog lists", [],
      [(k, s) for k, row in table["kinds"].items() for s, names in (row.get("mcpTools") or {}).items()
       if s not in row["mcpServers"] or not set(names) <= set(catalog.get(s, []))])
check("every transport policy names a known mode", [],
      [r for r, pol in table["transportMcp"].items() if pol.get("mcp") not in ("defer", "allowlist")])
check("no kind allowlists a tool its own row denies", [],
      [k for k, row in table["kinds"].items()
       if "ai-worker" in row["mcpServers"] and "write_doc" in (row.get("mcpTools") or {}).get("ai-worker", [])
       and "write_doc" in (row.get("denyTools") or [])])

# --- the catalog is a hand-kept copy of a server's tool list: a tool the server adds and the catalog lacks is never
# withheld on a proxied route. The captured init events (live sidecar runs) list the server's real tools; a kind's
# denial removes some from one row, so the union over the rows is the server's set.
INIT_EVENTS = os.path.join(HERE, "fixtures", "dispatch_kind_init_events.json")


def catalog_drift(server, names, runs):
    """(tools the captured init events show that the catalog lacks, tools the catalog lists that no event shows)."""
    prefix = "mcp__%s__" % server
    seen = {t[len(prefix):] for r in runs for t in r.get("tools", []) if t.startswith(prefix)}
    return sorted(seen - set(names)), sorted(set(names) - seen)


with open(INIT_EVENTS, encoding="utf-8") as _fh:
    RUNS = json.load(_fh)["runs"]
check("mcpServerTools['ai-worker'] equals the ai-worker tools in the captured init events", ([], []),
      catalog_drift("ai-worker", catalog["ai-worker"], RUNS))
check("a catalog missing a server tool is caught", (["get_stats"], []),
      catalog_drift("ai-worker", [t for t in catalog["ai-worker"] if t != "get_stats"], RUNS))
check("a catalog listing a tool the server lacks is caught", ([], ["no_such_tool"]),
      catalog_drift("ai-worker", catalog["ai-worker"] + ["no_such_tool"], RUNS))

# --- the CLI the launchers call prints the same profile
out = subprocess.run([sys.executable, os.path.join(ROOT, ".claude", "tools", "tool_profiles.py"), "resolve-kind",
                      "--kind", "scout", "--transport", "anthropic", "--config-dir", CFG],
                     capture_output=True, text=True, timeout=60)
check("resolve-kind --transport exits 0", 0, out.returncode)
check("resolve-kind --transport prints the deferred profile", "deferred",
      (json.loads(out.stdout).get("mcpDelivery") if out.returncode == 0 else out.stderr))

if RAN == 0:
    print("FAIL no case ran")
    sys.exit(1)
print("\n%d cases, %s" % (RAN, "FAILED" if FAIL else "all ok"))
sys.exit(1 if FAIL else 0)
