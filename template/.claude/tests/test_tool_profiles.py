#!/usr/bin/env python3
"""Proof for tools/tool_profiles.py: the one defaults table and the one resolver.

Cases, each named for the decision it pins:
  1. the shape vectors of guards/tool_profiles.json — the table IS the default owner;
  2. the `-t` grammars: signed delta, unsigned legacy replacement, an arm's task row;
  3. fail-closed validation — unregistered server, unknown shape, a removal the shape does not
     carry, all read tools removed, mixed signed/unsigned, duplicates and add∩remove;
  4. one config walk: the logical -> registered-key mapping, and the strict config built from it;
  5. the reserved built-ins (Workflow/Agent) that only a shape row carries;
  6. the shared quoted-data rule the brief-coverage scan and the mechanism guard both read;
  7. the capability preflight: a slash command a brief tells its child to run needs that
     command's `allowed-tools` grants, and the fan-out set derives from them;
  8. `command-coverage`: the tools a command's body invokes against its grants.

Every case must run and report; a zero-case run is a failure, never a pass. The table is injected
as a fixture in most cases so the proof does not depend on this machine's registrations.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
import tool_profiles as tp  # noqa: E402

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


def raises(label, needle, fn):
    """A case that must FAIL, and with a message a reader can act on."""
    global FAIL, RAN
    RAN += 1
    try:
        fn()
    except Exception as exc:
        if needle in str(exc):
            print("OK   %s -> %s" % (label, exc))
        else:
            print("FAIL %s: raised %r, wanted %r" % (label, str(exc), needle))
            FAIL = 1
        return
    print("FAIL %s: no error raised" % label)
    FAIL = 1


CANDIDATES = [
    ("ai-worker", {"command": "aiw"}),
    ("git", {"command": "gr"}),
    ("godot", {"command": "g"}),
    ("plugin:context7:context7", {"command": "c7"}),
    ("plugin:semantic-search:semantic-search", {"command": "ss"}),
]
TABLE = tp.load_table()
# The read-only kind rows carry the read-only git server.
READ_ONLY_SERVERS = ["semantic-search", "ai-worker", "git"]
SURVEY_SPEC = "Read,Glob,Grep,mcp__plugin_semantic-search_semantic-search,mcp__ai-worker,mcp__git"


def spec(shape, **kw):
    kw.setdefault("candidates", CANDIDATES)
    return tp.resolve(shape, **kw)[1]


# ---- 1. the shape vectors ---------------------------------------------------------------
for shape in ("judge", "scout", "reviewer"):
    profile, _, _ = tp.resolve(shape, candidates=CANDIDATES)
    check("%s builtins" % shape, ["Read", "Glob", "Grep"], profile["builtins"])
    servers = READ_ONLY_SERVERS if shape != "any" else ["semantic-search", "ai-worker"]
    check("%s servers" % shape, servers, profile["mcpServers"])
    check("%s spec" % shape, (SURVEY_SPEC if shape != "any"
                              else SURVEY_SPEC.replace(",mcp__git", "")), spec(shape))
    check("%s mode" % shape, "shape-default", profile["overrideMode"])
    check("%s enforcement" % shape, "strict", profile["enforcement"])
check("author builtins",
      ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
      tp.resolve("author", candidates=CANDIDATES)[0]["builtins"])
for shape, tier in (("reviewer", "executor"), ("author", "executor"), ("scout", "fanout")):
    check("%s carries its row's role tier" % shape, tier, tp.resolve(shape, candidates=CANDIDATES)[0].get("tier"))
check("resolve(tier=) overrides the row's tier and canonicalizes a folded alias", ("architect", "fanout", "executor"),
      (tp.resolve("reviewer", candidates=CANDIDATES, tier="architect")[0].get("tier"),
       tp.resolve("reviewer", candidates=CANDIDATES, tier="validation")[0].get("tier"),
       tp.resolve("reviewer", candidates=CANDIDATES, tier=None)[0].get("tier")))
check("a shape with no tier has no tier key", False,
      any("tier" in tp.resolve(s, base_builtins=["Read"], candidates=CANDIDATES)[0] for s in ("bench-score", "arm")))
check("arm declares no built-ins of its own", None, TABLE["kinds"]["arm"]["builtins"])
check("arm servers", ["semantic-search"],
      tp.resolve("arm", base_builtins=["Read", "Glob", "Grep"], candidates=CANDIDATES)[0]["mcpServers"])
raises("arm without its task built-ins", "task's built-in list",
       lambda: tp.resolve("arm", candidates=CANDIDATES))
check("godot is opt-in only", [],
      [s for s in TABLE["kinds"]["author"]["mcpServers"] if s == "godot"])
check("logical name resolves to the plugin key", "plugin:semantic-search:semantic-search",
      tp.registered_key("semantic-search", CANDIDATES))
check("a bare logical name resolves to its own key", "ai-worker",
      tp.registered_key("ai-worker", CANDIDATES))
check("an unregistered logical name resolves to nothing", None,
      tp.registered_key("nosuch", CANDIDATES))

# ---- 2. the -t grammars ------------------------------------------------------------------
check("signed csv is a delta", ("delta", ["Bash"], ["Grep"]), tp.parse_tools_csv("+Bash,-Grep"))
check("unsigned csv is a legacy replacement", ("legacy-replace", [], []),
      tp.parse_tools_csv("Read,Glob,Grep"))
check("empty -t asserts nothing", ("shape-default", [], []), tp.parse_tools_csv(""))
check("delta adds a server", SURVEY_SPEC + ",mcp__godot", spec("scout", tools_csv="+mcp:godot"))
check("delta removes a server",
      "Read,Glob,Grep,mcp__plugin_semantic-search_semantic-search,mcp__git",
      spec("scout", tools_csv="-mcp:ai-worker"))
check("delta adds and removes in one csv",
      "Read,Glob,Grep,mcp__plugin_semantic-search_semantic-search,mcp__git,mcp__godot",
      spec("scout", tools_csv="-mcp:ai-worker,+mcp:godot"))
check("delta removes a read tool", "Read,Bash,mcp__plugin_semantic-search_semantic-search,"
      "mcp__ai-worker,mcp__git", spec("scout", tools_csv="+Bash,-Glob,-Grep"))
check("delta is canonical order, not written order", "Read,Glob,Grep,Edit,Write,Bash",
      spec("author", tools_csv="+Bash,+Edit").split(",mcp__")[0])
check("a structured delta merges with -t", "Read,Glob,Grep,Edit,Write,Bash,mcp__godot",
      spec("author", tools_csv="-mcp:semantic-search,-mcp:ai-worker", add=["mcp:godot"]))
check("structured add and remove work alone",
      "Read,Glob,Grep,mcp__git,mcp__godot", spec("scout", tools_csv="-mcp:semantic-search,-mcp:ai-worker",
                                        add=["mcp:godot"]))
check("a Workflow signed tools string keeps its Bash grant", (["Bash"], ["Grep"]),
      tp.tools_delta("+Bash,-Grep"))
check("a Workflow signed tools list keeps its Bash grant", (["Bash"], ["Grep"]),
      tp.tools_delta(["+Bash", "-Grep"]))
raises("an unsigned Workflow tools string does not silently lose its grant", "signed",
       lambda: tp.tools_delta("Bash"))
raises("an unsigned Workflow tools list does not become a legacy sidecar replacement", "signed",
       lambda: tp.tools_delta(["Bash"]))
raises("an unsupported Workflow tools value is not treated as absent", "tools",
       lambda: tp.tools_delta(17))
legacy, _, _ = tp.resolve("reviewer", tools_csv="Read,Glob,Grep,Bash", candidates=CANDIDATES)
check("legacy -t replaces the built-ins", ["Read", "Glob", "Grep", "Bash"], legacy["builtins"])
check("legacy -t takes no MCP the csv did not name", [], legacy["mcpServers"])
check("legacy -t is recorded as such", "legacy-replace", legacy["overrideMode"])
check("legacy -t keeps an mcp__ name", ["godot"],
      tp.resolve("reviewer", tools_csv="Read,mcp__godot__get_uid", candidates=CANDIDATES)[0]["mcpServers"])
arm_profile, arm_spec, _ = tp.resolve(
    "arm", tools_csv="Read,Glob,Grep,Edit,Write,Bash,TodoWrite,mcp__plugin_semantic-search_semantic-search__search",
    candidates=CANDIDATES)
check("an arm's task row supplies its built-ins",
      ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "TodoWrite"], arm_profile["builtins"])
check("an arm's task row does not add servers", ["semantic-search"], arm_profile["mcpServers"])
check("an arm's task row is its own mode", "task-base", arm_profile["overrideMode"])
check("an arm's spec carries only its row's server",
      "Read,Glob,Grep,Edit,Write,Bash,TodoWrite,mcp__plugin_semantic-search_semantic-search", arm_spec)
check("an arm's delta can add a server",
      ["semantic-search", "ai-worker"],
      tp.resolve("arm", base_builtins=["Read", "Glob", "Grep"], add=["mcp:ai-worker"],
                 candidates=CANDIDATES)[0]["mcpServers"])

# ---- 3. fail-closed ----------------------------------------------------------------------
raises("unregistered server", "not registered",
       lambda: spec("scout", tools_csv="+mcp:nosuch"))
raises("unknown kind", "unknown kind", lambda: tp.resolve("nosuch", candidates=CANDIDATES))
raises("removing a server the shape lacks", "does not carry",
       lambda: spec("scout", tools_csv="-mcp:godot"))
raises("removing a built-in the shape lacks", "does not carry",
       lambda: spec("scout", tools_csv="-Edit"))
raises("removing every read tool", "could not read its brief",
       lambda: spec("scout", tools_csv="-Read,-Glob,-Grep"))
raises("mixed signed and unsigned csv", "mixed signed and unsigned",
       lambda: spec("scout", tools_csv="Read,+Bash"))
raises("a name in both add and remove", "named in both",
       lambda: spec("scout", tools_csv="+Bash,-Bash"))
raises("a repeated entry", "repeated",
       lambda: spec("scout", tools_csv="+mcp:godot,+mcp:godot"))
raises("an empty server name", "empty MCP server name",
       lambda: spec("scout", tools_csv="+mcp:"))
raises("a delta beside a legacy csv", "cannot be combined",
       lambda: tp.resolve("scout", tools_csv="Read,Glob,Grep", add=["Bash"], candidates=CANDIDATES))
raises("a bad --field", "unknown field", lambda: tp._report(({}, "", []), "nope", CANDIDATES))

# ---- 4. one config walk ------------------------------------------------------------------
check("the strict config holds exactly the resolved keys",
      ["ai-worker", "git", "plugin:semantic-search:semantic-search"],
      sorted(tp.strict_mcp_config(
          list(tp.resolve("scout", candidates=CANDIDATES)[0]["mcpRegistrations"].values()),
          CANDIDATES)["mcpServers"]))
check("an unknown spec name is dropped, not raised", [],
      list(tp.mcp_config_for("mcp__nosuch__x", workdir=ROOT)["mcpServers"]))
check("registration names ride in the profile",
      {"semantic-search": "plugin:semantic-search:semantic-search", "git": "git"},
      tp.resolve("scout", tools_csv="-mcp:ai-worker",
                 candidates=CANDIDATES)[0]["mcpRegistrations"])

# ---- 5. built-ins reserved from ordinary dispatches ---------------------------------------
# Owner rule 2026-09-24: a dispatch never orchestrates (Workflow/Agent), except the benchmark score
# driver, whose whole job is one Workflow(score_cell.js) call. The CLI can grant both (init event of
# `claude -p --tools Workflow,Agent,Read` lists Workflow and Task, client 2.1.281), so this is policy:
# only a shape row that lists a reserved built-in carries it, and no -t or delta adds one.
check("the reserved set is named once",
      ("Workflow", "Agent"), tuple(getattr(tp, "RESERVED_BUILTINS", ())))
check("bench-score is the one shape row carrying a reserved built-in", ["bench-score"],
      sorted(n for n, r in tp.load_table()["kinds"].items()
             if set(r.get("builtins") or []) & set(getattr(tp, "RESERVED_BUILTINS", ("Workflow", "Agent")))))
check("bench-score resolves Workflow for the score driver", "Read,Glob,Grep,Workflow",
      spec("bench-score").split(",mcp__")[0])
raises("bench-score cannot be widened to Agent by a delta", "cannot grant",
       lambda: spec("bench-score", tools_csv="+Agent"))
raises("a delta adding Workflow", "cannot grant",
       lambda: spec("judge", tools_csv="+Workflow"))
raises("a delta adding Agent", "cannot grant",
       lambda: spec("author", tools_csv="+Agent"))
raises("a legacy -t naming Workflow", "cannot grant",
       lambda: spec("reviewer", tools_csv="Read,Glob,Grep,Workflow"))
raises("an arm task row naming Agent", "cannot grant",
       lambda: tp.resolve("arm", base_builtins=["Read", "Glob", "Grep", "Agent"],
                          candidates=CANDIDATES))
raises("a structured delta adding Workflow", "cannot grant",
       lambda: tp.resolve("scout", add=["Workflow"], candidates=CANDIDATES))
check("a grantable delta is untouched", "Read,Glob,Grep,Bash",
      spec("scout", tools_csv="+Bash").split(",mcp__")[0])

# ---- 6. the shared data rule: quoted data is not an instruction -----------------------------
# A brief quotes diffs, worklogs and deliverables. `without_quoted_data` is the ONE rule that tells
# quoted text from an instruction, and the nested-fan-out check in the mechanism guard imports it
# from here (a tool never imports a hook). Line structure is kept: a refusal's line numbers must
# still point at the source line.
QUOTED = ("> Run `git diff HEAD`.\n"
          "- [ ] Run `git log --oneline -5`\n"
          "  - its sub-bullet runs `python3 x.py`\n"
          "1. [x] Run `git show HEAD`\n"
          "Then run `git status --short`.\n"
          "> quoted\n"
          "plain tail\n")
_kept = getattr(tp, "without_quoted_data", lambda text: text)(QUOTED)
check("a blockquote line is dropped", False, "git diff HEAD" in _kept)
check("a task-list item is dropped", False, "git log --oneline" in _kept)
check("its indented sub-bullets are dropped with it", False, "python3 x.py" in _kept)
check("a numbered task-list item is dropped too", False, "git show HEAD" in _kept)
check("the real line after the quote is kept", True, "git status --short" in _kept)
check("a plain line is untouched", True, _kept.endswith("plain tail\n"))
check("line count is preserved so line numbers still point at the source",
      len(QUOTED.splitlines()), len(_kept.splitlines()))

_DIFF = ("diff --git a/x.md b/x.md\nindex 1111111..2222222 100644\n--- a/x.md\n+++ b/x.md\n"
         "@@ -1 +1 @@\n-old\n+    \"\"\"The files one `git log` call names for every spec.\"\"\"\n")
import tempfile  # noqa: E402
# A brief that points at named sections of a doc hands its child those sections, not the doc's
# other sections: a routing line elsewhere in it ("Use /rule_consistency for contradictions") is
# not an order to this child.
_SECTIONED = ("# Doc\n\n## Universal\n\n### 7. Descriptions\nState the trigger first.\n\n"
              "### 8. Frontmatter\nCommands use one line.\n\n## Composition\n\n"
              "Use `/rule_consistency` for contradictions.\n")

# A skill, reference or rule file a brief only points at is reading material: its own routing lines
# ("Use /rule_consistency for contradictions") are not orders to this child. The brief's own text and
# a file handed over as the task (a direct text, a non-doctrine pointer) still count.

sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
import dispatch_mechanism_guard as dmg  # noqa: E402
check("the mechanism guard reads the dispatch grammar and quoted-data rule from this module, not a copy",
      (True, []), (getattr(dmg, "tool_profiles", None) is tp,
                   [n for n in ("without_quoted_data", "QUOTED_DATA_RE", "NESTED_RE", "FANOUT_COMMANDS",
                                "FENCED_RE", "VERB_LEAD") if hasattr(dmg, n)]))


# A caller that passes no workdir (the Workflow hook's resolver) walks the repo root, not `.claude/`:
# a local-scope entry keyed by the repo root and the root `.mcp.json` must both be seen.
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as wd:
    with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {"godot": {"command": "user-scope"}},
                   "projects": {ROOT: {"mcpServers": {"probe-local": {"command": "local"}}}}}, fh)
    saved = os.environ.pop("SC_WORKDIR", None)
    try:
        keys = [k for k, _c in tp.load_registrations(config_dir=home)]
    finally:
        if saved is not None:
            os.environ["SC_WORKDIR"] = saved
    check("no workdir: the local-scope entry keyed by the repo root is seen", True, "probe-local" in keys)

    with open(os.path.join(wd, ".mcp.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {"godot": {"command": "cmd", "env": {
            "SET": "${TOOL_PROFILE_PROBE_SET}", "DEFAULTED": "${TOOL_PROFILE_PROBE_UNSET:-dflt}",
            "UNSET": "${TOOL_PROFILE_PROBE_UNSET}"}}}}, fh)
    os.environ["TOOL_PROFILE_PROBE_SET"] = "C:/x/godot.exe"
    os.environ.pop("TOOL_PROFILE_PROBE_UNSET", None)
    try:
        regs = dict(tp.load_registrations(config_dir=home, workdir=wd))
    finally:
        os.environ.pop("TOOL_PROFILE_PROBE_SET", None)
    check("project .mcp.json outranks the user-scope entry of the same name", "cmd",
          regs.get("godot", {}).get("command"))
    env = regs.get("godot", {}).get("env", {})
    check("${VAR} expands from the environment, as Claude Code expands .mcp.json",
          "C:/x/godot.exe", env.get("SET"))
    check("${VAR:-default} takes the default when VAR is unset", "dflt", env.get("DEFAULTED"))
    check("an unset ${VAR} without a default stays literal", "${TOOL_PROFILE_PROBE_UNSET}", env.get("UNSET"))

# C5: Claude Code sets CLAUDE_PROJECT_DIR in the SPAWNED server's env, never in its own, so a child
# that expanded it from `os.environ` would get `.` — relative to the CHILD's workdir, not the
# repository. The walk supplies the absolute directory the config was read from, and that value
# wins over whatever this process has in its environment.
with tempfile.TemporaryDirectory() as pd_home, tempfile.TemporaryDirectory() as pd_wd:
    def registrations():
        with open(os.path.join(pd_wd, ".mcp.json"), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"godot": {"command": "cmd", "env": {
                "DEFAULTED": "${CLAUDE_PROJECT_DIR:-.}",
                "REQUIRED": "${CLAUDE_PROJECT_DIR}"}}}}, fh)
        return dict(tp.load_registrations(config_dir=pd_home, workdir=pd_wd)) \
            .get("godot", {}).get("env", {})

    saved_pd = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = os.path.join(pd_home, "planted", "elsewhere")
    try:
        polluted = registrations()
    finally:
        if saved_pd is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = saved_pd
    check("${CLAUDE_PROJECT_DIR:-.} is the workdir the .mcp.json was read from, not the environment's",
          os.path.abspath(pd_wd), polluted.get("DEFAULTED"))
    check("${CLAUDE_PROJECT_DIR} without a default takes that same workdir",
          os.path.abspath(pd_wd), polluted.get("REQUIRED"))

    saved_pd = os.environ.pop("CLAUDE_PROJECT_DIR", None)   # the unset control
    try:
        unset = registrations()
    finally:
        if saved_pd is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = saved_pd
    check("with CLAUDE_PROJECT_DIR unset the default `.` never reaches the child",
          os.path.abspath(pd_wd), unset.get("DEFAULTED"))

# ---- 7. capability preflight: a slash command the brief tells its child to run --------------
# A brief that tells the child to run `/<name>` needs that command's `allowed-tools` grants
# (`command_grants`), and one whose grants include Workflow or Agent is a nested fan-out, which no
# delegate profile carries (orchestration §0). `nested_fanout_hits` derives the fan-out set from those
# grants; `Task` reads as Agent. Planted commands keep the grammar cases hermetic; the
# /pr_ready and /clean_pull cases read the live commands, whose grants are the contract under test.
PR_READY_SHIPPED = os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "commands", "pr_ready.md"))
CMD_ROOT = tempfile.mkdtemp(prefix="tp_cmds_")
os.makedirs(os.path.join(CMD_ROOT, ".claude", "commands"))


def plant(name, frontmatter, body):
    with open(os.path.join(CMD_ROOT, ".claude", "commands", name + ".md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write("---\ndescription: planted\n%s---\n\n%s\n" % (frontmatter, body))
    return os.path.join(CMD_ROOT, ".claude", "commands", name + ".md")


P_WF = plant("planted_wf", "allowed-tools: Read, Workflow\n",
                "Workflow({scriptPath: '.claude/workflows/dispatch.js', args: {}})")
P_TASK = plant("planted_task", "allowed-tools: Read, Task\n", "Spawn one helper.")
P_GITDIFF = plant("planted_gitdiff", "allowed-tools: Bash(git diff:*), Read\n",
                  "1. Run `git diff HEAD`.\n2. Run `git rev-parse HEAD` to record the base.")
P_NOLINE_WF = plant("planted_noline_wf", "", "Dispatch:\n\nWorkflow({scriptPath: '.claude/workflows/dispatch.js', args: {}})")
P_NOLINE = plant("planted_noline", "", "Run `git status --short` and report.")
P_BARE = plant("planted_bare", "allowed-tools: Bash, Read\n", "Run `git rev-parse HEAD`, then `python3 x.py`.")


def attempt(fn):
    """fn()'s value, or the exception's text: a missing function is a failing case, not a crash."""
    try:
        return fn()
    except (Exception, SystemExit) as exc:  # noqa: BLE001  (argparse exits on an unknown subcommand)
        return "raised %s: %s" % (type(exc).__name__, exc)




check("command_grants reads Task as Agent, in written order", ["Read", "Agent"],
      attempt(lambda: tp.command_grants("planted_task", CMD_ROOT)))
check("command_grants reads Bash(<prefix>:*) as Bash", ["Bash", "Read"],
      attempt(lambda: tp.command_grants("planted_gitdiff", CMD_ROOT)))
check("command_grants is [] for a command with no allowed-tools line", [],
      attempt(lambda: tp.command_grants("planted_noline", CMD_ROOT)))
# F24 cut from plancheck_s2/fanout/review-fold-1_prompt.md:11; that line is an array of findings.
_FINDING_F24 = {
    "id": "F24",
    "rationale": "Read: dispatch.js:32-36 shows a provider guard checking pin vs tier, and dispatch.js:69 only logs PINS. The plan's claim is about what the guard covers, so name the guard and its coverage. Without that, the cut rests on an unbacked claim of no loss. Load-bearing because it justifies a deletion (Q5). Rung: read (dispatch.js), traced (guard's exact coverage not opened).",
}
for label, value in (("object", _FINDING_F24), ("array", [_FINDING_F24])):
    check("serialized F24 %s is data, not nested fan-out" % label, [],
          tp.nested_fanout_hits(json.dumps(value), CMD_ROOT))
    _body = json.dumps(value) + "\nNext, run /planted_wf."
    check("plain instruction after F24 %s keeps its source line" % label, [2],
          [hit["line"] for hit in tp.nested_fanout_hits(_body, CMD_ROOT)])
check("plain engine instruction still matches", True,
      bool(tp.nested_fanout_hits("Run dispatch.js now.", CMD_ROOT)))
check("malformed JSON cannot hide an instruction", True,
      bool(tp.nested_fanout_hits(json.dumps(_FINDING_F24)[:-1], CMD_ROOT)))
# Cut from the survey brief the provider guard denied on 2026-11-01 (route-split scout.md line 15,
# before its terms were rewritten): a list of search terms read as an order to dispatch.
_SEARCH_TERMS_BRIEF = (
    "Search beyond these. Run `git grep` over `.claude/` (skills, commands, rules, reference, hooks, "
    "tools, workflows, tests, CLAUDE*.md) for: `sidecar_fanout`, `fan-out`, `fanout`, `dispatch.js`, "
    "`Workflow`, `--detach`, `watch_records`, `jobs file`, `the Agent tool is denied`, "
    "`one-native-job`, `currencyReason`, `transport`. Also find any hook registered in "
    "`settings*.json` that inspects a `Workflow` or a `sidecar_fanout` call.")
check("a brief naming the engine as a search term is no dispatch order", [],
      tp.nested_fanout_hits(_SEARCH_TERMS_BRIEF, CMD_ROOT))
check("...the same list under `rg` is a search too", [],
      tp.nested_fanout_hits("Run `rg` over `.claude/` for: `fanout`, `dispatch.js`.", CMD_ROOT))
check("...a brief that reads or changes the engine file is no order", [],
      tp.nested_fanout_hits("Search for dispatch.js with `rg`. Read dispatch.js and change its spill path.", CMD_ROOT))
check("a brief that searches, then tells the child to launch the engine, is an order", True,
      bool(tp.nested_fanout_hits("Run `git grep` for the term, then launch dispatch.js for each lens.", CMD_ROOT)))
check("...so is a second imperative sentence after the search terms", True,
      bool(tp.nested_fanout_hits(_SEARCH_TERMS_BRIEF + " Then run dispatch.js with the jobs file.", CMD_ROOT)))
check("...and a Workflow call after a search stays an order", True,
      bool(tp.nested_fanout_hits("Run `rg` for the lenses. Use Workflow({ to dispatch them.", CMD_ROOT)))

_hit = attempt(lambda: tp.nested_fanout_hit("Next, run /planted_wf.", CMD_ROOT))
check("nested_fanout_hit matches a derived fan-out command", "Next, run /planted_wf", _hit)
check("nested_fanout_hit ignores a command without a fan-out grant", None,
      attempt(lambda: tp.nested_fanout_hit("Next, run /planted_noline.", CMD_ROOT)))
check("a `run:<id>` key heading a ledger unit row is data, not a dispatch verb", None,
      attempt(lambda: tp.nested_fanout_hit("run:wf_1a2b\t.claude/commands/planted_wf.md,.claude/x.md", CMD_ROOT)))
check("...while the verb before a command path still dispatches", True,
      attempt(lambda: tp.nested_fanout_hit("Next, run .claude/commands/planted_wf.md.", CMD_ROOT)) is not None)





# ---- 8. command-coverage: the tools a command's body invokes against its grants -------------
check("Bash(git diff:*) does not cover `git rev-parse HEAD`", ["Bash"],
      attempt(lambda: tp.command_coverage(P_GITDIFF)["gaps"]))
check("...and the gap names the uncovered command line", True,
      "git rev-parse HEAD" in str(attempt(lambda: tp.command_coverage(P_GITDIFF))))
check("no allowed-tools line and a line-start Workflow({ call is a Workflow gap", ["Workflow"],
      attempt(lambda: tp.command_coverage(P_NOLINE_WF)["gaps"]))
check("no allowed-tools line and a Bash command is no gap", [],
      attempt(lambda: tp.command_coverage(P_NOLINE)["gaps"]))
check("bare Bash covers every command", [],
      attempt(lambda: tp.command_coverage(P_BARE)["gaps"]))
check("a Workflow grant covers a line-start Workflow({ call", [],
      attempt(lambda: tp.command_coverage(P_WF)["gaps"]))
check("--include adds a linked file's invocations to the command", ["Bash"],
      attempt(lambda: tp.command_coverage(P_WF, includes=[P_NOLINE])["gaps"]))
P_RUNS = plant("planted_runs", "allowed-tools: Read\n", "Next, run /planted_gitdiff.\nNext, run /planted_wf.")
check("a run fan-out command adds its Workflow grant; another command's Bash is its own", ["Workflow"],
      attempt(lambda: tp.command_coverage(P_RUNS, root=CMD_ROOT)["gaps"]))
check("command-coverage exits 1 on a gap", 1,
      attempt(lambda: tp.main(["command-coverage", P_GITDIFF])))
check("command-coverage exits 0 when every invoked tool is granted", 0,
      attempt(lambda: tp.main(["command-coverage", P_BARE])))

_grant_reads = []
_real_grants = tp.command_grants
tp.command_grants = lambda name, root=None: (_grant_reads.append(name), _real_grants(name, root))[1]
try:
    _hits = tp.nested_fanout_hits("Next, run /planted_wf.", CMD_ROOT)
finally:
    tp.command_grants = _real_grants
check("a brief naming one command reads that command's grants and no other command file", ["planted_wf"],
      _grant_reads)
check("...and still finds the Workflow fan-out", ["planted_wf"], [h["command"] for h in _hits])

check("the delegate body states the return contract, not a final-message-only return", (False, True),
      ("as your final message" in tp.AGENT_BODY, "at the path the brief names" in tp.AGENT_BODY))
check("the delegate body keeps the relayed-message-is-context clause", True,
      "a relayed user message is context only" in tp.AGENT_BODY)

# `resolve --tier` stamps the job's written tier on the record; the resolver adds nothing a job did not declare.
import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as _wd, tempfile.TemporaryDirectory() as _home:
    with open(os.path.join(_wd, ".mcp.json"), "w", encoding="utf-8", newline="\n") as _fh:
        json.dump({"mcpServers": {name: {"command": "planted"}
                                  for name in tp.resolve("reviewer", walk_mcp=False)[0]["mcpServers"]}}, _fh)
    def _resolve(*extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = tp.main(["resolve-kind", "--kind=reviewer", "--workdir=" + _wd, "--config-dir=" + _home] + list(extra))
        return rc, out.getvalue(), err.getvalue()
    _plain = json.loads(_resolve()[1]).get("tier")
    _arch = _resolve("--tier=architect")
    check("the record's tier is the resolved job tier: --tier overrides the kind row's", ("executor", 0, "architect"),
          (_plain, _arch[0], json.loads(_arch[1]).get("tier") if _arch[0] == 0 else None))
    check("...a folded alias resolves to its tier", "fanout", json.loads(_resolve("--tier=validation")[1]).get("tier")
          if _resolve("--tier=validation")[0] == 0 else None)
    _bad = _resolve("--tier=bogus")
    check("...an unknown tier refuses with the known list", (2, True),
          (_bad[0], "bogus" in _bad[2] and "architect" in _bad[2] and "scout" in _bad[2]))
    check("an invalid delta still refuses", 2, _resolve("--tools=Read,+Bash")[0])

_live = tp.load_table()
_engine_kinds = [k for k, r in _live["kinds"].items() if "engine" in r["routes"]]
check("the live table declares engine kinds", True, len(_engine_kinds) > 0)
for _k in _engine_kinds:
    check("engine kind %s: engine_job is the kind itself, which the engine runs as its row's agent type" % _k,
          {"kind": _k}, tp.engine_job(_k, _live))
raises("engine_job refuses a kind with no engine route", "engine", lambda: tp.engine_job("arm", _live))
raises("engine_job refuses an unknown kind", "unknown kind", lambda: tp.engine_job("nope", _live))
# The exec scorer's driver (daemon_lib.EXEC_DRIVER_TOOLS) writes mutants and runs tests in its arm worktree.
check("the bench-score driver takes the exec scorer's write delta", ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "Workflow"],
      tp.resolve_kind("bench-score", add=["Edit", "Write", "Bash"], route="launcher", candidates=CANDIDATES)[0]["builtins"])
try:
    tp.resolve_kind("lens", add=["Write"], route="fanout", candidates=CANDIDATES)
    check("+Write on a lens is refused, naming author", "refused", "accepted")
except ValueError as exc:
    check("+Write on a lens is refused, naming author", True, "use kind author" in str(exc))

# A kind row's default `git` is repo-scoped: the repo's `.mcp.json` registers it, so a workdir outside
# every repo (a benchmark arm root) resolves without it, and a subdirectory of the repo resolves with it.
def _repo_scope_case():
    with tempfile.TemporaryDirectory() as base:
        home = os.path.join(base, "home")
        outside = os.path.join(base, "BenchmarkArms")
        repo = os.path.join(base, "repo")
        deep = os.path.join(repo, "a", "b")
        for d in (home, outside, deep, os.path.join(repo, ".git")):
            os.makedirs(d)
        with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"mcpServers": {"ai-worker": {"command": "aiw"},
                                      "semantic-search": {"command": "ss"}}}, fh)
        with open(os.path.join(repo, ".mcp.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"mcpServers": {"git": {"command": "python3",
                                              "env": {"GIT_RO_ROOT": "${CLAUDE_PROJECT_DIR:-.}"}}}}, fh)

        def kind(name, wd, add=()):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                try:
                    cands = tp.load_registrations(home, wd)
                    return tp.resolve_kind(name, add=list(add), route="launcher", candidates=cands)[0], err.getvalue(), cands
                except ValueError as exc:
                    return str(exc), err.getvalue(), []
        prof, err, _c = kind("scout", outside)
        check("a workdir in no repo resolves scout without git and without error",
              (["semantic-search", "ai-worker"], True),
              (prof["mcpServers"] if isinstance(prof, dict) else prof, "git" in err and "repo" in err))
        prof, err, cands = kind("scout", deep)
        check("a repo subdirectory resolves scout with git", (["semantic-search", "ai-worker", "git"], ""),
              (prof["mcpServers"] if isinstance(prof, dict) else prof, err))
        check("...and git's CLAUDE_PROJECT_DIR is the repo root, not the subdirectory",
              os.path.normcase(os.path.abspath(repo)),
              os.path.normcase(os.path.abspath(dict(cands).get("git", {}).get("env", {}).get("GIT_RO_ROOT", ""))))
        prof, _e, _c = kind("author", outside, add=["mcp:git"])
        check("an explicit +git outside a repo still errors", True,
              isinstance(prof, str) and "not registered in this config: git" in prof)
        with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"mcpServers": {"ai-worker": {"command": "aiw"}}}, fh)
        prof, _e, _c = kind("scout", outside)
        check("an unregistered non-repo-scoped default (semantic-search) still errors", True,
              isinstance(prof, str) and "not registered in this config: semantic-search" in prof)


_repo_scope_case()

# ---- the kind table: one required `kind` per job sets tools, -D, guard and agent type ----------
_TB = tp.load_table()
_PUBLIC = ["scout", "lens", "reviewer", "author", "judge"]
check("PUBLIC_KINDS derive from the table's non-internal rows", _PUBLIC,
      sorted(tp.public_kinds(_TB), key=_PUBLIC.index))
for _k, _b, _d, _a, _t in (
        ("scout", ["Read", "Glob", "Grep"], "bare", "lane-survey", "fanout"),
        ("lens", ["Read", "Glob", "Grep"], "pointer", "lane-survey", "fanout"),
        ("reviewer", ["Read", "Glob", "Grep"], "full", "lane-review", "executor"),
        ("author", ["Read", "Glob", "Grep", "Edit", "Write", "Bash"], "full", "lane-author", "executor"),
        ("judge", ["Read", "Glob", "Grep"], "full", "lane-review", "executor")):
    _p = tp.resolve_kind(_k, route="fanout", candidates=CANDIDATES)[0]
    check("resolve_kind %s: builtins, disclosure, agentType, tier" % _k, (_b, _d, _a, _t),
          (_p["builtins"], _p["disclosure"], _p["agentType"], _p["tier"]))
raises("an unknown kind names the public kinds", "scout, lens, reviewer, author, judge",
       lambda: tp.resolve_kind("nonesuch", route="fanout", candidates=CANDIDATES))
raises("an internal kind on the wrong route names its routes and the fix", "internal to",
       lambda: tp.resolve_kind("bench-score", route="fanout", candidates=CANDIDATES))
raises("+Edit on a reviewer is refused, naming author", "use kind author",
       lambda: tp.resolve_kind("reviewer", add=["Edit"], route="fanout", candidates=CANDIDATES))
raises("+Write on a lens is refused, naming author", "use kind author",
       lambda: tp.resolve_kind("lens", add=["Write"], route="fanout", candidates=CANDIDATES))
check("+Bash on a lens is accepted", ["Read", "Glob", "Grep", "Bash"],
      tp.resolve_kind("lens", add=["Bash"], route="fanout", candidates=CANDIDATES)[0]["builtins"])
check("kind_launcher_flags is gone: launchers take -G <kind>", False, hasattr(tp, "kind_launcher_flags"))
check("kind_guard reads the kind row's guard name", "survey", tp.kind_guard("scout"))
check("kind_guard of a reviewer is review", "review", tp.kind_guard("reviewer"))
check("kind_guard of no kind is None", None, tp.kind_guard(None))


def _bad_kinds(mutate):
    import copy
    import tempfile as _tf
    table = copy.deepcopy(_TB)
    mutate(table["kinds"])
    with _tf.TemporaryDirectory() as d:
        path = os.path.join(d, "t.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(table, fh)
        return tp.load_table(path)


raises("two kinds sharing an agentType with different built-ins are refused", "agentType",
       lambda: _bad_kinds(lambda k: k["judge"].update(builtins=["Read", "Glob", "Grep", "Bash"])))
check("a table whose kinds carry no legacy field loads", True,
      bool(_bad_kinds(lambda k: [row.pop("legacy", None) for row in k.values()])))
check("no kind row carries a legacy field", [], [n for n, r in _TB["kinds"].items() if "legacy" in r])
check("bench-review expresses its inputs directly: reviewer guard text, bare disclosure, launcher route",
      ("review", "bare", True),
      (_TB["kinds"]["bench-review"]["guard"], _TB["kinds"]["bench-review"]["disclosure"],
       "launcher" in _TB["kinds"]["bench-review"]["routes"]))

# ---- resolve-kind: the launcher's resolver ----------------------------------------------------
def _rk(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = tp.main(["resolve-kind"] + list(argv))
    return rc, out.getvalue(), err.getvalue()


_rc, _out, _err = _rk("--kind=lens")
_prof = json.loads(_out) if _rc == 0 else {}
check("resolve-kind lens prints builtins, disclosure and guard", (0, ["Read", "Glob", "Grep"], "pointer", "survey"),
      (_rc, _prof.get("builtins"), _prof.get("disclosure"), _prof.get("guard")))
_rc, _out, _err = _rk("--kind=author", "--tools=-mcp:ai-worker")
_prof = json.loads(_out) if _rc == 0 else {}
check("resolve-kind takes a signed removal", (0, ["semantic-search"]), (_rc, _prof.get("mcpServers")))
_rc, _out, _err = _rk("--kind=scout", "--tools=+Bash")
check("resolve-kind takes a signed +Bash", (0, "Read,Glob,Grep,Bash"),
      (_rc, ",".join(json.loads(_out)["builtins"]) if _rc == 0 else ""))
_rc, _out, _err = _rk("--kind=reviewer", "--tools=Read,Glob,Grep")
check("resolve-kind refuses an unsigned -t naming -G <kind> and a signed delta", True,
      _rc == 2 and "-G <kind>" in _err and "+Bash" in _err)
_rc, _out, _err = _rk("--kind=scout", "--tools=+Edit")
check("resolve-kind refuses +Edit off an author via -t", True, _rc == 2 and "use kind author" in _err)
_rc, _out, _err = _rk("--kind=survey")
check("resolve-kind refuses a retired shape name, naming the public kinds", True,
      _rc == 2 and "scout, lens, reviewer, author, judge" in _err)
_rc, _out, _err = _rk("--kind=verify")
check("resolve-kind refuses an engine-only kind", True, _rc == 2 and "internal" in _err)
_rc, _out, _err = _rk("--kind=scout", "--base-builtins=Read")
check("-B base built-ins are legal only with arm", True, _rc == 2 and "arm" in _err)
_rc, _out, _err = _rk("--kind=arm", "--tools=Read,Glob,Grep")
check("an arm still takes its unsigned task row", (0, ["Read", "Glob", "Grep"]),
      (_rc, json.loads(_out)["builtins"] if _rc == 0 else None))
_rc, _out, _err = _rk("--kind=bench-score")
check("bench-score resolves Workflow from its own row", (0, True), (_rc, "Workflow" in json.loads(_out)["builtins"] if _rc == 0 else None))
_rc, _out, _err = _rk("--kind=bench-review")
check("bench-review resolves through the launcher route", 0, _rc)
raises("a kind with no routes is refused", "routes",
       lambda: _bad_kinds(lambda k: k["scout"].update(routes=[])))

# ---- native_agent_type: a native job runs only as a generated agent type -----------------------
_NT = {"variants": [{"kind": "reviewer", "add": ["Bash"], "remove": [], "agentType": "lane-review-bash"}]}
check("a reviewer with no delta runs as its kind row's agent type", "lane-review",
      tp.native_agent_type({"kind": "reviewer", "agentType": "lane-review", "add": [], "remove": []}, _NT))
check("an author with no delta runs as lane-author", "lane-author",
      tp.native_agent_type({"kind": "author", "agentType": "lane-author"}, _NT))
check("a delta a variants entry names runs as that entry", "lane-review-bash",
      tp.native_agent_type({"kind": "reviewer", "agentType": "lane-review", "add": ["Bash"], "remove": []}, _NT))
raises("a delta with no covering agent file is refused", "no covering agent file",
       lambda: tp.native_agent_type({"kind": "reviewer", "agentType": "lane-review", "add": ["Write"], "remove": []}, _NT))
raises("the same delta on another kind is refused", "no covering agent file",
       lambda: tp.native_agent_type({"kind": "lens", "agentType": "lane-survey", "add": ["Bash"], "remove": []}, _NT))
raises("a kind row with no agentType cannot run natively", "names no agentType",
       lambda: tp.native_agent_type({"kind": "judge", "add": [], "remove": []}, _NT))

# ---- the live table: the kind row owns the tools; one enforced variant (D4c, OF1) ----------------
def _native(kind, add=(), remove=()):
    profile = tp.resolve_kind(kind, list(add), list(remove), route="agent", walk_mcp=False)[0]
    return tp.native_agent_type(profile)


def _bad_variants(variants):
    import copy
    import tempfile as _tf
    table = copy.deepcopy(_TB)
    table["variants"] = variants
    with _tf.TemporaryDirectory() as d:
        path = os.path.join(d, "t.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(table, fh)
        return tp.load_table(path)


check("the table has no `shapes` key: the kind rows own the defaults", False, "shapes" in TABLE)
check("the table has no job_kind_shape reader left", False, hasattr(tp, "job_kind_shape"))
check("the live table ships the reviewer+Bash variant",
      [{"kind": "reviewer", "add": ["Bash"], "agentType": "lane-review-sh"}],
      [{k: v for k, v in row.items() if k != "remove"} for row in TABLE.get("variants", [])])
check("a reviewer+Bash delta resolves to lane-review-sh", "lane-review-sh", _native("reviewer", ["Bash"]))
raises("a reviewer+Write delta is refused", "author", lambda: _native("reviewer", ["Write"]))
raises("a scout+Bash delta has no covering agent file", "no covering agent file", lambda: _native("scout", ["Bash"]))
for _kind, _agent in (("scout", "lane-survey"), ("lens", "lane-survey"), ("reviewer", "lane-review"),
                      ("author", "lane-author"), ("judge", "lane-review")):
    check("a planted native %s job resolves to %s" % (_kind, _agent), _agent, _native(_kind))
_rendered = tp.render_agents(TABLE, CANDIDATES)
check("render_agents writes the variant file with Bash after the reviewer's built-ins", True,
      "tools: Read, Glob, Grep, Bash, " in _rendered.get("lane-review-sh.md", ""))
check("render_agents writes one file per agent type", ["lane-author.md", "lane-review-sh.md", "lane-review.md", "lane-survey.md", "lane-verify.md"],
      sorted(_rendered))
raises("a variant adding Write to a non-author kind is refused at load", "author",
       lambda: _bad_variants([{"kind": "reviewer", "add": ["Write"], "agentType": "lane-review-w"}]))
raises("a variant reusing a kind's agentType is refused at load", "agentType",
       lambda: _bad_variants([{"kind": "reviewer", "add": ["Bash"], "agentType": "lane-review"}]))

WRITE_DOC = "mcp__ai-worker__write_doc"
for _kind in ("scout", "lens", "reviewer", "judge"):
    check("%s withholds ai-worker write_doc" % _kind, [WRITE_DOC], tp.resolve_kind(_kind, route="fanout", walk_mcp=False)[0]["denyTools"])
check("author keeps ai-worker write_doc", [], tp.resolve_kind("author", route="fanout", walk_mcp=False)[0]["denyTools"])
check("a read-only kind's spec still names the ai-worker server", True,
      "mcp__ai-worker" in tp.resolve_kind("scout", route="launcher", candidates=CANDIDATES)[1].split(","))
raises("denyTools on a kind must be a list of mcp__ names", "denyTools",
       lambda: tp._validate_kinds({"x": dict(TABLE["kinds"]["scout"], denyTools=["write_doc"])}))

# ---- the brief scan is gone; a retired flag names its replacement
import re  # noqa: E402
import subprocess  # noqa: E402


def _cli(*args):
    run = subprocess.run([sys.executable, os.path.join(ROOT, ".claude", "tools", "tool_profiles.py"), *args],
                         capture_output=True, text=True)
    return run.returncode, run.stderr


for _args in (("resolve", "--brief", "x.md"), ("resolve-kind", "--kind=scout", "--brief", "x.md"),
              ("resolve-kind", "--kind=scout", "--widen")):
    _rc, _err = _cli(*_args)
    check("`%s` exits 2" % " ".join(_args), 2, _rc)
    check("`%s` names kind and a signed tools delta" % " ".join(_args), (True, True),
          ("Declare `kind`" in _err, "signed `tools` delta" in _err))
_dead = ("brief_requirements", "brief_refusal", "check_brief_files", "widenable_needs", "coverage_for",
         "_cover_brief", "_with_pointed", "read_brief")
_live = []
for _dir in ("tools", "hooks"):
    for _f in sorted(os.listdir(os.path.join(ROOT, ".claude", _dir))):
        if _f.endswith(".py"):
            _src = open(os.path.join(ROOT, ".claude", _dir, _f), encoding="utf-8", errors="replace").read()
            _live += ["%s/%s:%s" % (_dir, _f, n) for n in _dead if re.search(r"def %s" % n, _src)]
check("no tools/ or hooks/ module defines a deleted brief-scan function", [], _live)

# ---- the table names no `any`, and the common rails file keeps the godot overlay
check("the table has no shapes block or defaultShape", [], [k for k in ("shapes", "defaultShape") if k in TABLE])
check("no kind row is named or guarded `any`", [],
      sorted(k for k, r in TABLE["kinds"].items() if k == "any" or r.get("guard") == "any"))
import guard_text as _gt  # noqa: E402
import glob as _glob  # noqa: E402
_rails = _gt.guard_text("author", "detailed")
_overlays = sorted(_glob.glob(os.path.join(ROOT, ".claude", "guards", "common.*.md")))
_missing = []
for _o in _overlays:
    _body = open(_o, encoding="utf-8").read().split("## detailed", 1)[1].split("\n## ", 1)[0]
    _first = next((l for l in _body.splitlines() if l.startswith("- ")), "")
    if not _first or _first not in _rails:
        _missing.append(os.path.basename(_o))
check("author/detailed rails carry the first detailed line of every common overlay file present", [], _missing)
_surf = [json.dumps({"kind": "path", "surface": ".claude/guards/%s" % o, "change": "rename", "to": ".claude/guards/%s" % n})
         for o, n in (("any.md", "common.md"), ("any.godot.md", "common.godot.md"))]
_out = subprocess.run([sys.executable, os.path.join(ROOT, ".claude", "tools", "citers.py"), "--json", "--repo", ROOT] + _surf,
                      capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
_history = (".claude/auto-memory/archive/", ".claude/tests/fixtures/", ".claude/scripts/rail_battery/reports/",
            ".claude/plans/", ".claude/scratch/", ".claude/autolearn_outcomes.jsonl", ".claude/orchestration_verdicts.json",
            ".claude/orchestration_metrics.jsonl", ".claude/self_evaluate_archive.json",
            ".claude/baseline.lock.json", ".claude/tests/test_tool_profiles.py")
_stale = sorted({"%s:%s" % (c["path"], c["line"]) for c in json.loads(_out.stdout or '{"citers": []}')["citers"]
                 if c["path"].startswith(".claude/") and not c["path"].startswith(_history)})
check("the citer sweep reports no live reference to guards/any*.md", [], _stale)

import re  # noqa: E402

_doc = open(os.path.join(ROOT, ".claude", "reference", "sidecar_dispatch.md"), encoding="utf-8").read()
_sect = _doc.split("## Job kind", 1)[1].split("\n## ", 1)[0]
_doc_kinds = sorted(k for k in re.findall(r"^\| (\w[\w-]*) \|", _sect, re.M) if k != "kind")
_table_kinds = sorted(k for k, row in tp.load_table()["kinds"].items() if not row.get("internal"))
check("sidecar_dispatch.md §Job kind lists exactly the non-internal kinds of the table", _table_kinds, _doc_kinds)

if RAN < 201:
    print("FAIL expected >= 201 cases, ran %d" % RAN)
    FAIL = 1
print("%s test_tool_profiles (%d cases)" % ("PASS" if not FAIL else "FAIL", RAN))
sys.exit(FAIL)
