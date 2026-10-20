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
    ("godot", {"command": "g"}),
    ("plugin:context7:context7", {"command": "c7"}),
    ("plugin:semantic-search:semantic-search", {"command": "ss"}),
]
TABLE = tp.load_table()
SURVEY_SPEC = "Read,Glob,Grep,mcp__plugin_semantic-search_semantic-search,mcp__ai-worker"


def spec(shape, **kw):
    kw.setdefault("candidates", CANDIDATES)
    return tp.resolve(shape, **kw)[1]


# ---- 1. the shape vectors ---------------------------------------------------------------
for shape in ("any", "survey", "review"):
    profile, _, _ = tp.resolve(shape, candidates=CANDIDATES)
    check("%s builtins" % shape, ["Read", "Glob", "Grep"], profile["builtins"])
    check("%s servers" % shape, ["semantic-search", "ai-worker"], profile["mcpServers"])
    check("%s spec" % shape, SURVEY_SPEC, spec(shape))
    check("%s mode" % shape, "shape-default", profile["overrideMode"])
    check("%s enforcement" % shape, "strict", profile["enforcement"])
check("author builtins",
      ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
      tp.resolve("author", candidates=CANDIDATES)[0]["builtins"])
for shape, tier in (("review", "executor"), ("author", "executor"), ("survey", "fanout")):
    check("%s carries its row's role tier" % shape, tier, tp.resolve(shape, candidates=CANDIDATES)[0].get("tier"))
check("a shape with no tier has no tier key", False,
      any("tier" in tp.resolve(s, base_builtins=["Read"], candidates=CANDIDATES)[0] for s in ("any", "bench-score", "arm")))
check("arm declares no built-ins of its own", None, TABLE["shapes"]["arm"]["builtins"])
check("arm servers", ["semantic-search"],
      tp.resolve("arm", base_builtins=["Read", "Glob", "Grep"], candidates=CANDIDATES)[0]["mcpServers"])
raises("arm without its task built-ins", "task's built-in list",
       lambda: tp.resolve("arm", candidates=CANDIDATES))
check("godot is opt-in only", [],
      [s for s in TABLE["shapes"]["author"]["mcpServers"] if s == "godot"])
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
check("delta adds a server", SURVEY_SPEC + ",mcp__godot", spec("survey", tools_csv="+mcp:godot"))
check("delta removes a server",
      "Read,Glob,Grep,mcp__plugin_semantic-search_semantic-search",
      spec("survey", tools_csv="-mcp:ai-worker"))
check("delta adds and removes in one csv",
      "Read,Glob,Grep,mcp__plugin_semantic-search_semantic-search,mcp__godot",
      spec("survey", tools_csv="-mcp:ai-worker,+mcp:godot"))
check("delta removes a read tool", "Read,Bash,mcp__plugin_semantic-search_semantic-search,"
      "mcp__ai-worker", spec("survey", tools_csv="+Bash,-Glob,-Grep"))
check("delta is canonical order, not written order", "Read,Glob,Grep,Edit,Write,Bash",
      spec("author", tools_csv="+Bash,+Edit").split(",mcp__")[0])
check("a structured delta merges with -t", "Read,Glob,Grep,Edit,Write,Bash,mcp__godot",
      spec("author", tools_csv="-mcp:semantic-search,-mcp:ai-worker", add=["mcp:godot"]))
check("structured add and remove work alone",
      "Read,Glob,Grep,mcp__godot", spec("survey", tools_csv="-mcp:semantic-search,-mcp:ai-worker",
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
legacy, _, _ = tp.resolve("review", tools_csv="Read,Glob,Grep,Bash", candidates=CANDIDATES)
check("legacy -t replaces the built-ins", ["Read", "Glob", "Grep", "Bash"], legacy["builtins"])
check("legacy -t takes no MCP the csv did not name", [], legacy["mcpServers"])
check("legacy -t is recorded as such", "legacy-replace", legacy["overrideMode"])
check("legacy -t keeps an mcp__ name", ["godot"],
      tp.resolve("review", tools_csv="Read,mcp__godot__get_uid", candidates=CANDIDATES)[0]["mcpServers"])
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
       lambda: spec("survey", tools_csv="+mcp:nosuch"))
raises("unknown shape", "unknown shape", lambda: tp.resolve("lens", candidates=CANDIDATES))
raises("removing a server the shape lacks", "does not carry",
       lambda: spec("survey", tools_csv="-mcp:godot"))
raises("removing a built-in the shape lacks", "does not carry",
       lambda: spec("survey", tools_csv="-Edit"))
raises("removing every read tool", "could not read its brief",
       lambda: spec("survey", tools_csv="-Read,-Glob,-Grep"))
raises("mixed signed and unsigned csv", "mixed signed and unsigned",
       lambda: spec("survey", tools_csv="Read,+Bash"))
raises("a name in both add and remove", "named in both",
       lambda: spec("survey", tools_csv="+Bash,-Bash"))
raises("a repeated entry", "repeated",
       lambda: spec("survey", tools_csv="+mcp:godot,+mcp:godot"))
raises("an empty server name", "empty MCP server name",
       lambda: spec("survey", tools_csv="+mcp:"))
raises("a delta beside a legacy csv", "cannot be combined",
       lambda: tp.resolve("survey", tools_csv="Read,Glob,Grep", add=["Bash"], candidates=CANDIDATES))
raises("a bad --field", "unknown field", lambda: tp._report(({}, "", []), "nope", CANDIDATES))

# ---- 4. one config walk ------------------------------------------------------------------
check("the strict config holds exactly the resolved keys",
      ["ai-worker", "plugin:semantic-search:semantic-search"],
      sorted(tp.strict_mcp_config(
          list(tp.resolve("survey", candidates=CANDIDATES)[0]["mcpRegistrations"].values()),
          CANDIDATES)["mcpServers"]))
check("an unknown spec name is dropped, not raised", [],
      list(tp.mcp_config_for("mcp__nosuch__x", workdir=ROOT)["mcpServers"]))
check("registration names ride in the profile", {"semantic-search": "plugin:semantic-search:semantic-search"},
      tp.resolve("survey", tools_csv="-mcp:ai-worker",
                 candidates=CANDIDATES)[0]["mcpRegistrations"])

# ---- 5. built-ins reserved from ordinary dispatches ---------------------------------------
# Owner rule 2026-09-24: a dispatch never orchestrates (Workflow/Agent), except the benchmark score
# driver, whose whole job is one Workflow(score_cell.js) call. The CLI can grant both (init event of
# `claude -p --tools Workflow,Agent,Read` lists Workflow and Task, client 2.1.281), so this is policy:
# only a shape row that lists a reserved built-in carries it, and no -t or delta adds one.
check("the reserved set is named once",
      ("Workflow", "Agent"), tuple(getattr(tp, "RESERVED_BUILTINS", ())))
check("bench-score is the one shape row carrying a reserved built-in", ["bench-score"],
      sorted(n for n, r in tp.load_table()["shapes"].items()
             if set(r.get("builtins") or []) & set(getattr(tp, "RESERVED_BUILTINS", ("Workflow", "Agent")))))
check("bench-score resolves Workflow for the score driver", "Read,Glob,Grep,Workflow",
      spec("bench-score").split(",mcp__")[0])
raises("bench-score cannot be widened to Agent by a delta", "cannot grant",
       lambda: spec("bench-score", tools_csv="+Agent"))
raises("a delta adding Workflow", "cannot grant",
       lambda: spec("any", tools_csv="+Workflow"))
raises("a delta adding Agent", "cannot grant",
       lambda: spec("author", tools_csv="+Agent"))
raises("a legacy -t naming Workflow", "cannot grant",
       lambda: spec("review", tools_csv="Read,Glob,Grep,Workflow"))
raises("an arm task row naming Agent", "cannot grant",
       lambda: tp.resolve("arm", base_builtins=["Read", "Glob", "Grep", "Agent"],
                          candidates=CANDIDATES))
raises("a structured delta adding Workflow", "cannot grant",
       lambda: tp.resolve("survey", add=["Workflow"], candidates=CANDIDATES))
check("a grantable delta is untouched", "Read,Glob,Grep,Bash",
      spec("survey", tools_csv="+Bash").split(",mcp__")[0])

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

_REPORT = tp.brief_requirements([("b", "Run `git diff HEAD`.\n"
                                       'TOOLS-ACK: Bash "git diff HEAD" — the diff is attached below\n')])
check("a TOOLS-ACK line is plain text, never a pass condition", ["Bash"],
      tp.coverage_of(_REPORT)["needs"])
check("a match carries no `waived` field", {"tool", "source", "line", "text", "negated"},
      set(_REPORT["matches"][0]))
check("imperative Run python3 needs Bash", ["Bash"], tp.coverage_of(
      tp.brief_requirements([("b", "Run python3 .claude/tools/kfm.py index")]))["needs"])
check("Never run python3 remains a negative instruction", [], tp.coverage_of(
      tp.brief_requirements([("b", "Never run python3 .claude/tools/kfm.py index")]))["needs"])
check("blockquoted Run python3 is judged data", [], tp.coverage_of(
      tp.brief_requirements([("b", "> Run python3 .claude/tools/kfm.py index")]))["needs"])
_DIFF = ("diff --git a/x.md b/x.md\nindex 1111111..2222222 100644\n--- a/x.md\n+++ b/x.md\n"
         "@@ -1 +1 @@\n-old\n+    \"\"\"The files one `git log` call names for every spec.\"\"\"\n")
check("a unified diff passed as a brief file is judged data", [], tp.coverage_of(
      tp.brief_requirements([("prompt", "Review the hunks."), ("d.diff", _DIFF)]))["needs"])
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as _diff_root:
    with open(os.path.join(_diff_root, "hunks.diff"), "w", encoding="utf-8", newline="\n") as _f:
        _f.write(_DIFF)
    check("a unified diff a brief points at is judged data", [], tp.coverage_of(tp.brief_requirements(
        [("prompt", "The hunks are in sub/../hunks.diff; read sub/../hunks.diff first.")],
        root=_diff_root))["needs"])
check("the same line outside a diff still needs Bash", ["Bash"], tp.coverage_of(tp.brief_requirements(
    [("prompt", "Review the hunks."), ("notes.md", "The files one `git log` call names for every spec.\n")]))["needs"])
# A brief that points at named sections of a doc hands its child those sections, not the doc's
# other sections: a routing line elsewhere in it ("Use /rule_consistency for contradictions") is
# not an order to this child.
_SECTIONED = ("# Doc\n\n## Universal\n\n### 7. Descriptions\nState the trigger first.\n\n"
              "### 8. Frontmatter\nCommands use one line.\n\n## Composition\n\n"
              "Use `/rule_consistency` for contradictions.\n")
with tempfile.TemporaryDirectory() as _sec_root:
    _sec = os.path.join(_sec_root, "doc.md").replace("\\", "/")
    with open(_sec, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(_SECTIONED)
    check("a pointer naming sections scans only those sections", [], tp.coverage_of(tp.brief_requirements(
        [("prompt", "Read `%s` §7 and §8 first." % _sec)]))["needs"])
    check("a pointer naming no section still scans the whole doc", True, "Workflow" in tp.coverage_of(
        tp.brief_requirements([("prompt", "Read `%s` first." % _sec)]))["needs"])
    check("a pointer naming the section that orders the fan-out still needs it", True, "Workflow" in
          tp.coverage_of(tp.brief_requirements([("prompt", "Read `%s` §Composition first." % _sec)]))["needs"])

# A skill, reference or rule file a brief only points at is reading material: its own routing lines
# ("Use /rule_consistency for contradictions") are not orders to this child. The brief's own text and
# a file handed over as the task (a direct text, a non-doctrine pointer) still count.
with tempfile.TemporaryDirectory() as _doc_root:
    _doc_rel = ".claude/skills/planted_skill/SKILL.md"
    _doc_abs = os.path.join(_doc_root, *_doc_rel.split("/"))
    os.makedirs(os.path.dirname(_doc_abs))
    with open(_doc_abs, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(_SECTIONED)
    check("a pointed skill's command mention is reference, not an order", [], tp.coverage_of(
        tp.brief_requirements([("prompt", "Read `%s` first." % _doc_rel)], root=_doc_root))["needs"])
    check("a pointed skill's mention is reference with an absolute path too", [], tp.coverage_of(
        tp.brief_requirements([("prompt", "Read `%s` first." % _doc_abs.replace("\\", "/"))],
                              root=_doc_root))["needs"])
    check("a brief that itself orders the fan-out still needs it beside a pointed skill", True,
          "Workflow" in tp.coverage_of(tp.brief_requirements(
              [("prompt", "Read `%s` first.\nThen run /rule_consistency on the result." % _doc_rel)],
              root=_doc_root))["needs"])
    check("the same skill text handed over as a direct brief text still needs the fan-out", True,
          "Workflow" in tp.coverage_of(tp.brief_requirements([(_doc_rel, _SECTIONED)],
                                                             root=_doc_root))["needs"])

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


def refusal_for(line, shape, root=None):
    builtins = tp.resolve(shape, candidates=CANDIDATES)[0]["builtins"]
    return tp.brief_refusal(builtins, tp.brief_requirements([("brief.md", line)], root))


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

_hit = attempt(lambda: tp.nested_fanout_hit("Next, run /planted_wf.", CMD_ROOT))
check("nested_fanout_hit matches a derived fan-out command", "Next, run /planted_wf", _hit)
check("nested_fanout_hit ignores a command without a fan-out grant", None,
      attempt(lambda: tp.nested_fanout_hit("Next, run /planted_noline.", CMD_ROOT)))
check("a `run:<id>` key heading a ledger unit row is data, not a dispatch verb", None,
      attempt(lambda: tp.nested_fanout_hit("run:wf_1a2b\t.claude/commands/planted_wf.md,.claude/x.md", CMD_ROOT)))
check("...while the verb before a command path still dispatches", True,
      attempt(lambda: tp.nested_fanout_hit("Next, run .claude/commands/planted_wf.md.", CMD_ROOT)) is not None)
_pr = str(attempt(lambda: refusal_for("Run /pr_ready over the branch.", "author")))
check("Run /pr_ready under author is refused with the orchestration §0 text", True,
      "orchestration §0" in _pr and "every lens of a nested fan-out itself" in _pr)
check("...and offers no delta or wider shape for Workflow/Agent", False, "Fix:" in _pr)
_pull = str(attempt(lambda: refusal_for("Then run /clean_pull.", "review")))
check("Then run /clean_pull under review is refused for Bash", True,
      _pull.startswith("the brief needs Bash,") and "brief.md:1" in _pull)
check("a descriptive /pr_ready mention passes", None,
      attempt(lambda: refusal_for("This output feeds /pr_ready later.", "author")))
check("a negated run after a /pr_ready mention passes", None,
      attempt(lambda: refusal_for("This output feeds /pr_ready later; do not run it.", "author")))
check("a negated run of /pr_ready passes", None,
      attempt(lambda: refusal_for("Never run /pr_ready yourself.", "author")))
_neg_then_run = str(attempt(lambda: refusal_for("Run /pr_ready; do not skip it.", "author")))
check("a run whose later clause is negated is still refused", True, "orchestration §0" in _neg_then_run)
check("a blockquoted /pr_ready passes", None,
      attempt(lambda: refusal_for("> Run /pr_ready over the branch.", "author")))
check("a fenced /pr_ready passes", None,
      attempt(lambda: refusal_for("Context:\n```\nRun /pr_ready over the branch.\n```\n", "author")))
check("a command with no allowed-tools line needs nothing", None,
      attempt(lambda: refusal_for("Next, run /planted_noline.", "review", CMD_ROOT)))


def fanout_tools(line):
    report = attempt(lambda: tp.brief_requirements([("brief.md", line)]))
    return sorted({m["tool"] for m in report["matches"] if m["tool"] in tp.RESERVED_BUILTINS}) \
        if isinstance(report, dict) else report


check("a noun 'a /pr_pipeline run records it' is no instruction to run it", [],
      fanout_tools("Report the round: the round stays unrecorded until a `/pr_pipeline` run records it."))
check("a verb and a /explore mention in different sentences are no instruction", [],
      fanout_tools("use the smallest method that verifies the claims and retains their quotes. `/explore` "
                   "and the caller's validation pass downstream of the briefing remain required"))
check("'Otherwise run /pr_ready now' is still refused", True,
      "orchestration §0" in str(attempt(lambda: refusal_for(
          "Admit no mandate for the PR. Otherwise run `/pr_ready` now if it hasn't run.", "author"))))

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

# The resolver widens a profile that lacks a built-in the brief runs, and refuses what no delta fixes.
import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as _wd, tempfile.TemporaryDirectory() as _home:
    _bf = os.path.join(_wd, "brief.md")
    with open(_bf, "w", encoding="utf-8", newline="\n") as _fh:
        _fh.write("Enumerate with `git diff HEAD`.\n")
    with open(os.path.join(_wd, ".mcp.json"), "w", encoding="utf-8", newline="\n") as _fh:
        json.dump({"mcpServers": {name: {"command": "planted"}
                                  for name in tp.resolve("review", walk_mcp=False)[0]["mcpServers"]}}, _fh)
    def _resolve(*extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = tp.main(["resolve", "--widen", "--shape=review", "--brief=" + _bf, "--workdir=" + _wd,
                          "--config-dir=" + _home] + list(extra))
        return rc, out.getvalue(), err.getvalue()
    _rc, _out, _err = _resolve()
    _prof = json.loads(_out) if _rc == 0 else {}
    check("a brief needing Bash widens a review profile", (0, ["Read", "Glob", "Grep", "Bash"], ["Bash"]),
          (_rc, _prof.get("builtins"), _prof.get("add")))
    check("...and prints the one-line notice with file:line", True,
          "[sidecar] profile widened by +Bash: the brief runs it (" in _err and "brief.md:1" in _err)
    check("an invalid delta still refuses", 2, _resolve("--tools=Read,+Bash")[0])
    _out, _err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(_out), contextlib.redirect_stderr(_err):
        _lint = tp.main(["resolve", "--shape=review", "--brief=" + _bf, "--workdir=" + _wd])
    check("without --widen the resolver still refuses: authored-brief lints keep their check", 2, _lint)
    check("a removed tool the brief needs still refuses", 2, _resolve("--tools=+Bash,-Bash")[0])
    _review = tp.resolve("review", walk_mcp=False)[0]["builtins"]
    check("a pre-launch check that defers to the launcher's widening passes a grantable need", None,
          tp.check_brief_files(_review, [_bf], grammar="job", root=_wd, widen=True))
    check("...and without widen it still refuses", True,
          bool(tp.check_brief_files(_review, [_bf], grammar="job", root=_wd)))
    _wf = os.path.join(_wd, "wf.md")
    with open(_wf, "w", encoding="utf-8", newline="\n") as _fh:
        _fh.write("Run `Workflow({script: 'x'})` over the three lenses.\n")
    check("...but a fan-out need no delta grants still refuses under widen", True,
          bool(tp.check_brief_files(_review, [_wf], grammar="job", root=_wd, widen=True)))

if RAN < 110:
    print("FAIL expected >= 110 cases, ran %d" % RAN)
    FAIL = 1
print("%s test_tool_profiles (%d cases)" % ("PASS" if not FAIL else "FAIL", RAN))
sys.exit(FAIL)
