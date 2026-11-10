#!/usr/bin/env python3
"""Proof of the native-side tool profile: declared, injected, denied and displayed.

  1. a declared job's profile reaches the launch table WITH its delta — the fixture that used to
     show the job and say nothing about its tools;
  2. a call that declares nothing produces no line, and one whose profile cannot be read says
     `unknown` rather than being read as a default kind;
  3. a standalone Agent's `TOOL-PROFILE:` prompt header is honoured, and an unusable one refuses
     the launch before any agent starts;
  4. a sidecar Bash command's `-G`/`-t` resolve through the SAME resolver, marked strict;
  5. `tools.add`/`tools.remove` reach the launcher as the one signed `-t` grammar;
  6. a row's `agentType` rides in the resolved profile, and `agents --check` fails when the
     generated `.claude/agents/` files drift from the table;
  7. the `agent-type` enforcement label is accepted and rendered.

Native rows default to `inherited`: the Workflow/Agent runtime restricts no per-child MCP, so the
profile recorded is the caller's REQUEST with its effective MCP unknown. Only an engine that
actually launched a job as the row's generated agent type stamps `agent-type` on that job.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)

sys.path.insert(0, HERE)
import _mcp_plant  # noqa: E402

# The servers a profile resolves are registrations of the machine and the project, which differ per
# project: the proof plants the four the table names, so it reads neither this machine's config nor this project's.
_mcp_plant.plant()
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
import _dispatch_pins as dp  # noqa: E402
import dispatch_table  # noqa: E402
import sidecar_fanout  # noqa: E402
import workflow_provider_guard as guard  # noqa: E402

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


def workflow(payload_jobs):
    return {"tool_name": "Workflow",
            "tool_input": {"scriptPath": ".claude/workflows/dispatch.js",
                           "args": json.dumps({"jobs": payload_jobs})}}


REVIEW_JOB = {"label": "k1", "model": "sonnet", "effort": "low", "kind": "reviewer"}
DELTA_JOB = {"label": "k3", "model": "sonnet", "effort": "low", "kind": "reviewer",
             "tools": {"add": ["mcp:godot"], "remove": []}}
PLAIN_JOB = {"label": "k2", "model": "sonnet", "effort": "low"}

# ---- 1. a declared profile reaches the table, with its delta
lines = dispatch_table._profile_lines(workflow([REVIEW_JOB, PLAIN_JOB]))
check("one declared job produces one profile line", 1, len(lines))
check("the line names the job's label", "  tools k1:" in lines[0], True)
check("the line names the kind", " reviewer " in lines[0] + " ", True)
check("the line states inherited, not strict", "inherited (MCP not restricted)" in lines[0], True)
check("the line shows the resolved server set",
      "semantic-search,ai-worker,git" in lines[0], True)
check("the line shows the built-ins", "Read,Glob,Grep" in lines[0], True)
check("a job that declares nothing is not given a line", 1, lines.count(lines[0]))
_delta_line = dispatch_table._profile_lines(workflow([DELTA_JOB]))[0]
check("a native delta with no covering agent file shows as unresolved, naming the missing file", True,
      _delta_line.startswith("  tools k3: unresolved") and "no covering agent file" in _delta_line)

# ---- 1b. one unresolvable job must not erase the jobs beside it
BAD_JOB = {"label": "b1", "model": "sonnet", "effort": "low", "kind": "reviewer",
           "tools": {"add": ["mcp:nosuch"], "remove": []}}
mixed = dispatch_table._profile_lines(workflow([REVIEW_JOB, BAD_JOB]))
check("every declared job keeps its line", 2, len(mixed))
check("the unresolvable job names its own reason, not the call's",
      True, mixed[1].startswith("  tools b1: unresolved") and "nosuch" in mixed[1])
check("a header may carry tools.base too", ["Read", "Glob", "Grep"],
      dp.agent_profile_request({"prompt": 'TOOL-PROFILE: {"kind":"scout","base":["Read","Glob","Grep"]}'
                                })["baseBuiltins"])

# ---- 2. nothing declared is not the default
check("an undeclared job has no request", None, dp.profile_request(PLAIN_JOB))
check("a call with no declared profile prints no line", [], dispatch_table._profile_lines(workflow([PLAIN_JOB])))
check("an unreadable profile says unknown", "  tools k9: unknown (no profile declared)",
      dp.undeclared_profile("k9"))
check("a whole kind vector resolves through the same resolver",
      ["Read", "Glob", "Grep", "Edit", "Write", "Bash"],
      dp.resolve_request({"kind": "author"})["builtins"])
raises("an absent kind is refused, not defaulted", "kind",
       lambda: dp.resolve_request({"kind": None, "add": [], "remove": []}))

# ---- 3. the Agent header
AGENT = {"tool_name": "Agent", "tool_input": {"description": "lens-a", "prompt": "go\n"
         'TOOL-PROFILE: {"kind":"reviewer","add":[],"remove":[]}\nmore'}}
check("a valid header resolves", "reviewer", dp.agent_profile_request(AGENT["tool_input"])["kind"])
check("a valid header reaches the table", True, "  tools lens-a:" in
      "".join(dispatch_table._profile_lines(AGENT)))
check("a header delta with no covering agent file shows as unreadable, naming the missing file", True,
      "no covering agent file" in "".join(dispatch_table._profile_lines(
          {"tool_name": "Agent", "tool_input": {"description": "lens-b", "prompt":
           'TOOL-PROFILE: {"kind":"reviewer","add":[],"remove":["mcp:ai-worker"]}'}})))
check("an absent header is no request", None,
      dp.agent_profile_request({"prompt": "no header here"}))
raises("a malformed header", "not JSON",
       lambda: dp.agent_profile_request({"prompt": "TOOL-PROFILE: {oops}"}))
raises("a header naming nothing", "names no kind",
       lambda: dp.agent_profile_request({"prompt": 'TOOL-PROFILE: {"kind":"","add":[],"remove":[]}'}))
check("a header with an unregistered server is refused", True, "not registered" in
      (guard.tool_profile_refusal(
           {"tool_name": "Agent",
            "tool_input": {"description": "x",
                           "prompt": 'TOOL-PROFILE: {"kind":"scout","add":["mcp:nosuch"],"remove":[]}'}})
       or ""))
check("a header removing what the kind lacks is refused", True, "does not carry" in
      (guard.tool_profile_refusal(
           {"tool_name": "Agent",
            "tool_input": {"description": "x",
                           "prompt": 'TOOL-PROFILE: {"kind":"scout","add":[],"remove":["mcp:godot"]}'}})
       or ""))

# ---- 4. the guard's deny and injection
check("a valid call is not refused", None, guard.tool_profile_refusal(workflow([REVIEW_JOB])))
check("a dispatch.js job naming no kind is refused", True,
      "names no kind" in (guard.tool_profile_refusal(workflow([PLAIN_JOB])) or ""))
check("a header carrying a delta and no kind is refused", True, "kind" in (guard.tool_profile_refusal(
    {"tool_name": "Agent", "tool_input": {"description": "x", "prompt": 'TOOL-PROFILE: {"add":["Bash"]}'}}) or ""))
check("an invalid delta is refused before any agent call",
      True, bool(guard.tool_profile_refusal(workflow([{"label": "k3", "model": "sonnet",
                                                       "kind": "reviewer",
                                                       "tools": {"add": ["mcp:nosuch"],
                                                                 "remove": []}}]))))
check("a signed string grant with no covering agent file is refused before launch", True,
      "no covering agent file" in (guard.tool_profile_refusal(workflow([
          {"label": "shell", "model": "sonnet", "kind": "scout", "tools": "+Bash"}])) or ""))
check("a reviewer's +Bash grant is covered by the lane-review-sh variant", None,
      guard.tool_profile_refusal(workflow([
          {"label": "shell", "model": "sonnet", "kind": "reviewer", "tools": "+Bash"}])))
check("an unsigned Workflow tools string fails before launch", True,
      "signed" in (guard.tool_profile_refusal(workflow([
          {"label": "shell", "model": "sonnet", "kind": "reviewer", "tools": "Bash"}])) or ""))
injected = guard.transport_injection(workflow([REVIEW_JOB]), "anthropic")
args = json.loads(injected["args"]) if isinstance(injected["args"], str) else injected["args"]
check("the resolved profile rides in args.__toolProfiles", "lane-review",
      args["__toolProfiles"]["k1"]["agentType"])
check("the injected profile keeps its registrations",
      "plugin:semantic-search:semantic-search",
      args["__toolProfiles"]["k1"]["mcpRegistrations"]["semantic-search"])
check("the call carries its kind's row and the engine's verifier profile", ["k1", "verify"], sorted(args["__toolProfiles"]))

# ---- 5. a sidecar command's -G/-t resolve through the same resolver
bash = {"tool_name": "Bash",
        "tool_input": {"command": 'bash .claude/scripts/anthropic_sidecar.sh -m haiku '
                                  '-l side-1 -G lens -t "+mcp:godot"'}}
sided = dispatch_table._profile_lines(bash)
check("a sidecar row produces a profile line", 1, len(sided))
check("the sidecar line is strict, not inherited", True, " strict " in sided[0])
check("the sidecar line shows its delta", "+mcp:godot" in sided[0], True)
check("the sidecar line names the label from -l", "  tools side-1:" in sided[0], True)
check("a sidecar command with no -G/-t prints nothing", [],
      dispatch_table._profile_lines({"tool_name": "Bash", "tool_input": {"command": "bash x.sh -m haiku"}}))
check("an absolute -t list is refused: a kind takes a signed delta only", True, "absolute tool list" in
      dispatch_table._profile_lines(
          {"tool_name": "Bash",
           "tool_input": {"command": 'bash .claude/scripts/deepseek_sidecar.sh -G lens -t "Read,Glob,Grep"'}})[0])

# ---- 6. a fan-out job's delta becomes the one signed -t grammar
check("the signed csv", "+Bash,-mcp:ai-worker",
      sidecar_fanout.tool_profiles.signed_csv(["Bash"], ["mcp:ai-worker"]))
import tempfile  # noqa: E402
prompt = os.path.join(tempfile.mkdtemp(), "p.md")
open(prompt, "w").write("brief\n")
job = {"label": "j1", "alias": "haiku", "kind": "lens", "promptFile": prompt, "plan": "anthropic",
       "tools": {"add": ["Bash"], "remove": ["mcp:ai-worker"]}}
_label, argv, _out = sidecar_fanout.plan_job(job, None, tempfile.mkdtemp(), authorize=False)
check("the delta reaches the launcher as -t", "+Bash,-mcp:ai-worker", argv[argv.index("-t") + 1])
raises("an absolute tools csv beside a delta", "cannot be combined",
       lambda: sidecar_fanout.plan_job({"label": "j2", "alias": "haiku", "kind": "lens", "promptFile": prompt,
                                        "plan": "anthropic",
                                        "toolsCsv": "Read,Glob,Grep",
                                        "tools": {"add": ["Bash"]}}, os.environ.get("SC_JOBS"),
                                       tempfile.mkdtemp(), authorize=False))

# ---- 7. nothing falls to a default: the retired default rows are gone
check("the hook carries no profile defaults injection", False, hasattr(dp, "profile_defaults"))
_ti = guard.transport_injection(workflow([REVIEW_JOB]), "anthropic")
_a = json.loads(_ti["args"]) if isinstance(_ti["args"], str) else _ti["args"]
check("the Workflow injection carries no __toolProfileDefaults and no __coverage", [],
      [k for k in ("__toolProfileDefaults", "__coverage", "__skillIndex", "__shapeTiers") if k in _a])
raises("the arm kind is not a native kind", "arm", lambda: dp.resolve_request({"kind": "arm"}))

# ---- 8. a standalone Agent with NO header has no profile
AGENT_BARE = {"tool_name": "Agent", "tool_input": {"description": "bare", "prompt": "no header here"}}
check("an absent header is not an error", None, guard.tool_profile_refusal(AGENT_BARE))
check("an Agent with no header gets no line: no profile anybody chose", [], dispatch_table._profile_lines(AGENT_BARE))
check("the Agent's own header still shows its kind row", True, " reviewer " in
      "".join(dispatch_table._profile_lines(AGENT)))

# ---- 9. every runnable agent() site reports a label-matched TOOL-PROFILES entry
import glob  # noqa: E402
import subprocess  # noqa: E402
WF = os.path.join(CLAUDE, "workflows")
RETIRED = tuple(os.path.basename(p) for p in glob.glob(os.path.join(WF, "*.js"))
                if "RETIRED FOR DISPATCH" in open(p, encoding="utf-8").read())
sites = []
for path in sorted(glob.glob(os.path.join(WF, "*.js"))):
    text = open(path, encoding="utf-8").read()
    if "agent(" in text and os.path.basename(path) not in RETIRED:
        sites.append((os.path.basename(path), text))
# The engine list is enumerated from git, not hand-counted: a new engine should not force an edit here.
tracked = [os.path.basename(f) for f in subprocess.check_output(
    ["git", "-C", os.path.dirname(CLAUDE), "ls-files", "--", ".claude/workflows"], text=True).splitlines()
    if f.endswith(".js")]
unlisted = [n for n in tracked if os.path.isfile(os.path.join(WF, n)) and n not in RETIRED
            and n not in {name for name, _ in sites}
            and "agent(" in open(os.path.join(WF, n), encoding="utf-8").read()]
check("every tracked agent() engine is in the enumeration", [], unlisted)
silent = [name for name, text in sites if "TOOL-PROFILES" not in text]
check("no runnable workflow is silent about its tool profiles", [], silent)
check("the retired 26th is excluded because it refuses before any agent() call", False,
      any(name in RETIRED for name, _ in sites))
retired_reads = [name for name, text in sites if any(
    word in text for word in ("__toolProfileDefaults", "__coverage", "__skillIndex", "__shapeTiers"))]
check("no workflow reads a retired injection", [], retired_reads)

# ---- 10. a row's agentType rides in the resolved profile; a row without one adds none
import shutil  # noqa: E402
import subprocess  # noqa: E402
TP_CLI = os.path.join(CLAUDE, "tools", "tool_profiles.py")
TABLE = os.path.join(CLAUDE, "guards", "tool_profiles.json")
AGENTS_DIR = os.path.join(CLAUDE, "agents")  # the project's committed files: test_tool_profiles_committed_agents.py


def tp_cli(*argv):
    return subprocess.run([sys.executable, TP_CLI] + list(argv), capture_output=True, text=True)


def cli_profile(kind):
    run = tp_cli("resolve-kind", "--kind", kind, "--field", "profile")
    try:
        return json.loads(run.stdout)
    except ValueError:
        return {"__stderr__": run.stderr}


check("resolve-kind reviewer carries the row's agentType", "lane-review",
      cli_profile("reviewer").get("agentType"))
check("resolve-kind arm carries no agentType", False, "agentType" in cli_profile("arm"))

# ---- 11. generated agent definitions match the table; a planted table edit is drift
_tmp = tempfile.mkdtemp()
_gen_agents = os.path.join(_tmp, "generated")
tp_cli("agents", "--write", "--agents-dir", _gen_agents)
check("the rendered agent files check clean against the table", 0,
      tp_cli("agents", "--check", "--agents-dir", _gen_agents).returncode)
_planted = os.path.join(_tmp, "tool_profiles.json")
with open(TABLE, encoding="utf-8") as _h:
    _table = json.load(_h)
_table["variants"][0]["add"].append("TodoWrite")
with open(_planted, "w", encoding="utf-8", newline="\n") as _h:
    json.dump(_table, _h)
_tmp_agents = os.path.join(_tmp, "agents")
shutil.copytree(_gen_agents, _tmp_agents)
check("a planted table edit fails the drift check with the drift code", 1,
      tp_cli("agents", "--check", "--table", _planted, "--agents-dir", _tmp_agents).returncode)
tp_cli("agents", "--write", "--table", _planted, "--agents-dir", _tmp_agents)
check("--write clears the drift", 0,
      tp_cli("agents", "--check", "--table", _planted, "--agents-dir", _tmp_agents).returncode)
_review_agent = os.path.join(_gen_agents, "lane-review.md")
_text = open(_review_agent, encoding="utf-8").read() if os.path.exists(_review_agent) else ""
check("lane-review.md names its type", True, "\nname: lane-review\n" in _text)
check("lane-review.md carries the one-line description", True,
      "\ndescription: Workflow engine delegate for the review tool profile; engines select it, "
      "never pick it for a manual Agent call.\n" in _text)
_tools = next((ln[len("tools: "):] for ln in _text.splitlines() if ln.startswith("tools: ")), "")
check("lane-review.md's tools start with the row's built-ins", ["Read", "Glob", "Grep"],
      _tools.split(", ")[:3])
check("lane-review.md lists ToolSearch because the row names an MCP server", True,
      _tools.endswith(", ToolSearch"))
check("lane-review.md grants no Skill tool", False, "Skill" in _tools.split(", "))
check("lane-review.md's body is the delegate system prompt", True,
      _text.rstrip("\n").endswith("The runtime appends the workflow-return note."))
_declared = sorted(r["agentType"] + ".md" for r in json.load(open(
    os.path.join(os.path.dirname(AGENTS_DIR), "guards", "tool_profiles.json"), encoding="utf-8"))["kinds"].values()
    if r.get("agentType"))
_declared = sorted(set(_declared) | {"lane-review-sh.md"})
check("the table declares survey, review, review-sh, author and verify agent types",
      ["lane-author.md", "lane-review-sh.md", "lane-review.md", "lane-survey.md", "lane-verify.md"], _declared)
check("only kinds and variants that declare agentType render a file", _declared,
      sorted(os.listdir(_gen_agents)))
for _name in _declared:
    _t = open(os.path.join(_gen_agents, _name), encoding="utf-8").read()
    check(_name + " grants no Skill tool", False, "Skill" in _t.split("tools:", 1)[1].split("\n", 1)[0].split(", "))

# ---- 12. the agent-type enforcement label is accepted and rendered
try:
    _at = dp.resolve_request({"kind": "reviewer"}, enforcement="agent-type")
    _line = dp.profile_line("lens", _at)
except Exception as exc:
    _at, _line = {"__error__": str(exc)}, ""
check("an agent-type profile keeps its label", "agent-type", _at.get("enforcement"))
check("an agent-type profile names its row's servers as effective", "semantic-search, ai-worker, git",
      _at.get("effectiveMcp"))
check("the table renders agent-type, not strict", True,
      " agent-type (" in _line and " strict " not in _line)
raises("an unknown enforcement label", "enforcement",
       lambda: dp.resolve_request({"kind": "reviewer"}, enforcement="bogus"))

# ---- 13. a brief's wording never sets tools: the removed coverage surfaces stay removed
import tool_profiles as tp  # noqa: E402
check("the brief-derived coverage functions are gone", [],
      [n for n in ("brief_requirements", "brief_refusal", "check_brief_files", "widenable_needs", "coverage_for",
                   "coverage_of", "job_defaults") if hasattr(tp, n)])
check("resolve-kind has no --brief flag", True, "--brief" not in tp_cli("resolve-kind", "--help").stdout)
check("the coverage module is deleted", False, os.path.exists(os.path.join(CLAUDE, "hooks", "_brief_coverage.py")))

# ---- 14. a planted native command per kind runs as its agent type; the pr_ready lens runs as the variant
for _kind, _tools, _agent in (("scout", None, "lane-survey"), ("lens", None, "lane-survey"), ("reviewer", None, "lane-review"),
                              ("reviewer", "+Bash", "lane-review-sh"), ("author", None, "lane-author"),
                              ("judge", None, "lane-review")):
    _job = {"label": "planted", "model": "sonnet", "effort": "low", "kind": _kind}
    if _tools:
        _job["tools"] = _tools
    check("a native %s job%s runs as %s" % (_kind, " with %s" % _tools if _tools else "", _agent), _agent,
          guard._tool_profiles(workflow([_job]))["planted"]["agentType"])

# ---- 15. every engine that reads a call-level `__kindProfile` is one the hook resolves it for
_WF_DIR = os.path.join(CLAUDE, "workflows")
_readers = sorted(f[:-3] for f in os.listdir(_WF_DIR)
                  if f.endswith(".js") and "__kindProfile" in open(os.path.join(_WF_DIR, f), encoding="utf-8").read())
check("every single-kind engine is in the hook's CALL_KIND_ENGINES", [],
      [e for e in _readers if e not in guard.CALL_KIND_ENGINES])
check("...and a call to one resolves its row", "lane-review",
      guard.call_kind_profile({"tool_name": "Workflow", "tool_input": {
          "scriptPath": ".claude/workflows/" + _readers[0] + ".js", "args": {"kind": "reviewer"}}})["agentType"])
raises("...and a kindless call to one is refused", "kind",
       lambda: guard.call_kind_profile({"tool_name": "Workflow", "tool_input": {
           "scriptPath": ".claude/workflows/" + _readers[0] + ".js", "args": {}}}))

# ---- 16. a read-only kind's agent file withholds write_doc; the author's does not
def _disallowed(name):
    for line in open(os.path.join(_gen_agents, name), encoding="utf-8").read().split("---")[1].splitlines():
        if line.startswith("disallowedTools:"):
            return line.split(":", 1)[1].strip()
    return None
for _name in ("lane-survey.md", "lane-review.md", "lane-review-sh.md"):
    check(_name + " withholds ai-worker write_doc", "mcp__ai-worker__write_doc", _disallowed(_name))
check("lane-author.md carries no disallowedTools line", None, _disallowed("lane-author.md"))
check("render_agents puts disallowedTools in the variant's frontmatter", True,
      "disallowedTools: mcp__ai-worker__write_doc" in tp.render_agents(
          json.load(open(os.path.join(os.path.dirname(AGENTS_DIR), "guards", "tool_profiles.json"), encoding="utf-8")),
          tp.load_registrations()).get("lane-review-sh.md", ""))

if RAN < 70:
    print("FAIL expected >= 70 cases, ran %d" % RAN)
    FAIL = 1
print("%s test_workflow_tool_profiles (%d cases)" % ("PASS" if not FAIL else "FAIL", RAN))
sys.exit(FAIL)
