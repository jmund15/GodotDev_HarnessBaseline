"""Pins a Workflow or Agent call declares: the jobs its args enumerate and the script text it runs.

Shared by hooks/workflow_provider_guard.py (policy) and hooks/dispatch_table.py (display). Imports no
quota or band code. Proof: tests/test_dispatch_pins.py.
"""
import json
import os
import re
import sys

def _registry_modules():
    """(model_registry, _session_transport), or (None, None) when either cannot import."""
    try:
        import _session_transport  # puts tools/ on sys.path
        import model_registry
        return model_registry, _session_transport
    except Exception:
        return None, None


# ---------------------------------------------------------------- a transport's tier pins

def native_pin(row, transport):
    """The name a native Workflow/Agent call uses for `row`: on the host transport, the alias when
    it is the family name (the only kind its runtime resolves), else the id; the canonical id on a
    proxied endpoint. A versioned alias (`opus5`) or a new version the client does not yet serve
    for the family name reaches the runtime only by id."""
    _mr, st = _registry_modules()
    host = st.HOST_TRANSPORT if st else None
    if transport != host:
        return row.get("id")
    alias, family = row.get("alias"), row.get("family")
    if family and alias != family:
        return row.get("id")
    return alias or row.get("id")


def native_legal_pins(transport, data):
    """Every name a native call on the host `transport` may pin: each dispatchable row's id and its
    `native_pin`. A versioned alias is left out, because the runtime would receive it unresolved."""
    _mr, st = _registry_modules()
    if st is None:
        return set()
    out = set()
    for row in st._dispatchable_rows(transport, data, transport):
        out |= {row.get("id"), native_pin(row, transport)}
    return {name for name in out if name}


def tier_rows(tier, transport, data):
    """Registry rows on `transport` that `transport` may dispatch to and that serve `tier`."""
    mr, _st = _registry_modules()
    return [m for m in mr.rows_for_tier(tier, data, seat=transport)
            if m.get("transport") == transport]


def _call_args(tool_input):
    a = tool_input.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            return {}
    return a if isinstance(a, dict) else {}


# `args.jobsFile`: a Workflow call that carries a path instead of its jobs. A script cannot read files,
# so the PreToolUse chain expands the path (hooks/pre_workflow_dispatch.py) and every reader of a call's
# args sees the same expansion through `_args`. The file holds no key that only the owner's inline words lift.
JOBS_FILE_ROOT = ".claude/scratch/"
_OWNER_LIFTS = ("currency", "currencyReason")


def _jobs_file_load(args):
    """(expanded args, None) or (None, refusal reason) for a call's args that name a `jobsFile`."""
    path = args.get("jobsFile")
    if not isinstance(path, str) or not path.strip():
        return None, "args.jobsFile must be a non-empty path string."
    beside = [k for k in ("jobs", "chains") if k in args]
    if beside:
        return None, ("args.jobsFile sits beside args.%s: a call carries its jobs in the file or inline, never "
                      "both. Send only {jobsFile}." % "/args.".join(beside))
    root = os.path.realpath(_project_dir())
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    scratch = os.path.normcase(os.path.join(root, *JOBS_FILE_ROOT.strip("/").split("/"))) + os.sep
    if not os.path.normcase(full).startswith(scratch):
        return None, "args.jobsFile %r is outside %s of this checkout; the fan-out writes it there." % (path, JOBS_FILE_ROOT)
    try:
        with open(full, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "args.jobsFile %r could not be read as JSON (%s: %s)." % (path, type(exc).__name__, exc)
    if not isinstance(loaded, dict):
        return None, "args.jobsFile %r must hold a JSON object of Workflow args." % path
    units = list(loaded.get("jobs") or []) + [j for c in loaded.get("chains") or []
                                              for j in (c.get("jobs") or [] if isinstance(c, dict) else [])]
    for holder in [loaded] + [u for u in units if isinstance(u, dict)]:
        for key in holder:
            if key.startswith("__") or key in _OWNER_LIFTS:
                return None, ("args.jobsFile %r holds %r. Injected keys and the currency lifts stay inline "
                              "and are the owner's to give (workflow_provider_guard)." % (path, key))
    expanded = {k: v for k, v in args.items() if k != "jobsFile"}
    return dict(loaded, **expanded), None


def jobs_file_refusal(tool_input):
    """The reason a call's `args.jobsFile` cannot be expanded, or None (no jobsFile, or it expands)."""
    args = _call_args(tool_input)
    if "jobsFile" not in args:
        return None
    return _jobs_file_load(args)[1]


def expanded_args(tool_input):
    """The call's `args` dict with `jobsFile` expanded, or None when there is no jobsFile or it is refused."""
    args = _call_args(tool_input)
    if "jobsFile" not in args:
        return None
    return _jobs_file_load(args)[0]


def _args(tool_input):
    a = _call_args(tool_input)
    if "jobsFile" in a:
        return _jobs_file_load(a)[0] or a
    return a


def _is_review_fanout(tool_input):
    return "review_fanout" in os.path.basename(
        str(tool_input.get("scriptPath") or tool_input.get("name") or "")).replace("-", "_")


def _project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _tools_on_path():
    tools = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)


def _writes_tracked(prompt_path):
    """Whether an author job can change tracked files: False only when every path in its brief's `task`
    block writeSet is git-ignored or outside the checkout (scratch output or a vault doc cannot change the
    commits close proves). A brief that is absent, unreadable or declares no writeSet is unknown, so it
    counts as a writer."""
    if not prompt_path or not isinstance(prompt_path, str):
        return True
    root = _project_dir()
    path = prompt_path if os.path.isabs(prompt_path) else os.path.join(root, prompt_path)
    _tools_on_path()
    try:
        import dispatch_result
        with open(path, encoding="utf-8") as fh:
            task = dispatch_result.task_block(fh.read()) or {}
    except (OSError, ValueError, ImportError):
        return True
    write_set = [p for p in task.get("writeSet") or [] if p.strip()]
    if not write_set:
        return True
    top = os.path.normcase(os.path.realpath(root))
    write_set = [p for p in write_set
                 if (os.path.normcase(os.path.realpath(os.path.join(root, p))) + os.sep).startswith(top + os.sep)]
    if not write_set:
        return False
    import subprocess
    try:
        out = subprocess.run(["git", "-C", root, "check-ignore", "--", *write_set],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    ignored = {line.strip() for line in out.stdout.splitlines() if line.strip()}
    return not all(p in ignored for p in write_set)


def author_writes(job):
    """Whether one declared job is an author that can change tracked files."""
    job = job or {}
    return job.get("kind") == "author" and _writes_tracked(job.get("promptPath") or job.get("promptFile"))


def writes(tool_name, tool_input):
    """Whether a dispatch declares an author or fold that can change tracked files, for close's writer wait."""
    ti = tool_input or {}
    if tool_name == "Workflow":
        name = os.path.basename(str(ti.get("scriptPath") or ti.get("name") or "")).replace("-", "_")
        if _is_review_fanout(ti):
            return bool(_args(ti).get("fold"))
        if name in ("dispatch.js", "dispatch_chains.js", "dispatch", "dispatch_chains"):
            return any(author_writes(j) for j in dispatch_jobs(
                {"tool_name": tool_name, "tool_input": ti}))
    if tool_name == "Bash":
        _tools_on_path()
        import sidecar_argv
        import orchestration_metrics
        command = str(ti.get("command") or "")
        for inv in sidecar_argv.launcher_invocations(command):
            flags = sidecar_argv.sidecar_flags(inv.args) if inv.kind == "sidecar" else {}
            if flags.get("G") == "author" and _writes_tracked(flags.get("f")):
                return True
            if inv.kind == "fanout" and any(a == "--fold" or a.startswith("--fold=") for a in inv.args):
                return True
        cwd, calls = orchestration_metrics._resolved_launcher_calls(
            command, os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        for kind, _script, args in calls:
            if kind != "fanout":
                continue
            parsed = orchestration_metrics._parse_fanout_args(args, cwd)
            if not parsed:
                continue
            path, _out_dir = parsed
            try:
                with open(path, encoding="utf-8") as fh:
                    jobs = orchestration_metrics.fanout_spec_jobs(json.load(fh), path)
                if any(author_writes(j) for j in jobs or []):
                    return True
            except (OSError, ValueError):
                pass
    return False


# A native job names its tool inventory by `kind` alone (`guards/tool_profiles.json`); each retired key
# names its replacement, and the engines and the hook refuse a job that still carries one.
RETIRED_JOB_KEYS = {
    "agentType": "the `kind` row sets the agent type",
    "shape": "use `kind` (scout|lens|reviewer|author|judge)",
    "readOnly": "a reviewer, scout, lens or judge job cannot write and an author job can: pick the kind",
}


def dispatch_jobs(payload):
    """[{label, model, effort, kind, ...}] per job this call's ARGS declare, with the caller's literal
    `model`/`effort` (None when omitted). No engine fills an omitted pin: each refuses the call. `retired`
    lists the job keys a kind replaced (`RETIRED_JOB_KEYS`) that the entry still carries."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if tool == "Agent":
        return [{"label": str(ti.get("description") or "agent"), "model": ti.get("model") or None,
                 "effort": None, "agentType": str(ti.get("subagent_type") or "general-purpose")}]
    a = _args(ti)
    out = []

    def job(j, label_key, default_label):
        return {"label": str(j.get(label_key) or default_label), "model": j.get("model") or None,
                "effort": j.get("effort") or None, "agentType": j.get("agentType") or None,
                "kind": j.get("kind") or None, "retired": [k for k in RETIRED_JOB_KEYS if k in j],
                "railTier": j.get("railTier") or None,
                "tools": j.get("tools") or None, "promptPath": j.get("promptPath") or None,
                "prompt": j.get("prompt") or None, "contextFiles": j.get("contextFiles") or None}

    for j in a.get("jobs") or []:
        out.append(job(j or {}, "label", "job"))
    for c in a.get("chains") or []:
        for j in (c or {}).get("jobs") or []:
            out.append(job(j or {}, "label", "job"))
    for lens in a.get("lenses") or []:
        out.append(job(lens or {}, "key", "lens"))
    for ag in a.get("agents") or []:
        out.append(job(ag or {}, "key", "agent"))
    return out


# Characters of a `scriptPath` file this hook will read. The committed workflows are all well under
# it; the cap exists so a caller-supplied path cannot set this hook's cost.
SCRIPT_READ_CAP = 512 * 1024

_COMMENT_RE = re.compile(r"/\*.*?\*/|(?<![:'\"`\\])//[^\n]*", re.S)


def _strip_comments(text):
    """Blank out JS comments before scanning for pins.

    Load-bearing, not tidiness: these scripts DOCUMENT their pin conventions in comments
    (`dispatch.js` explains its pin vocabulary in prose directly above the code), so a
    scanner that reads comments denies a script whose executable pins are all legal. A guard that
    misfires on its own doctrine spends the credibility that makes true positives land
    (`rules/harness_tooling.md`). The `//` arm refuses to match after `:` or a quote so a URL
    (`https://…`) is not read as a comment.
    """
    return _COMMENT_RE.sub(lambda m: " " * len(m.group(0)), text)


def script_blobs(payload):
    """([(label, comment-stripped text)], [incomplete reasons]) for a Workflow's inline script and
    the file its `scriptPath` names."""
    ti = payload.get("tool_input") or {}
    if payload.get("tool_name") != "Workflow":
        return [], []
    incomplete = []
    blobs = []
    inline = ti.get("script")
    if isinstance(inline, str) and inline:
        blobs.append(("inline-script", inline))
    path = str(ti.get("scriptPath") or "")
    if path:
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            full = path if os.path.isabs(path) else os.path.join(os.path.dirname(root), path)
            # Bounded: this runs on EVERY Workflow PreToolUse, and `scriptPath` is caller-supplied.
            # A mispointed path at a multi-MB file would spend the hook's whole budget on a read
            # whose pins all sit in the first few KB anyway. But a TRUNCATED scan is an incomplete
            # one: a pin past the cap would be invisible and read to `decide_vocabulary` exactly
            # like a clean script, so the cap reports itself rather than silently passing.
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read(SCRIPT_READ_CAP + 1)
            if len(text) > SCRIPT_READ_CAP:
                incomplete.append("%s exceeds %dKB" % (os.path.basename(path),
                                                       SCRIPT_READ_CAP // 1024))
            blobs.append((os.path.basename(path), text[:SCRIPT_READ_CAP]))
        except Exception:
            # No claim from THAT FILE -- but pins already collected from the inline blob stand.
            # Returning [] here made an unreadable path ERASE real evidence, and an empty pin list
            # reads to decide_vocabulary exactly like a clean script (instruction_quality 14).
            #
            # NOT escalated to a deny, unlike truncation: this hook resolves `scriptPath` against
            # its own root guess, so "I could not open it" often means the runtime can and the
            # guess was wrong. Denying there would block valid dispatches on a path this hook
            # merely failed to resolve. Truncation is different -- the file WAS found, and the
            # part not read could hold a pin.
            pass
    return [(label, _strip_comments(text)) for label, text in blobs], incomplete


# An object literal's leading run of keys, cut at the first nested brace or template string.
_OBJECT_HEAD_RE = re.compile(r"\{([^{}`]*)")
_STRING_PAIR_RE = re.compile(r"(\w+)\s*:\s*(['\"])([^'\"\n]*)\2\s*(?=,|$)")


def script_jobs(payload):
    """[{label, model, effort, agentType}] for each object literal in a Workflow script whose `label`
    and `model` are plain string literals (an agent() options object or a job-table row). A computed
    label or pin is skipped, never guessed; `effort`/`agentType` are None when not literal."""
    blobs, _ = script_blobs(payload)
    out, seen = [], set()
    for _, text in blobs:
        for head in _OBJECT_HEAD_RE.finditer(text):
            pairs = {m.group(1): m.group(3) for m in _STRING_PAIR_RE.finditer(head.group(1))}
            label, model = pairs.get("label"), pairs.get("model")
            if label and model and label not in seen:
                seen.add(label)
                out.append({"label": label, "model": model, "effort": pairs.get("effort"),
                            "agentType": pairs.get("agentType")})
    return out

# ------------------------------------------------------------------- tool profiles
# A native Workflow or Agent child inherits the session's MCP set: the runtime offers no per-child
# MCP restriction, and the engines invent no `agent()` option for one. So a profile resolved here is
# recorded `inherited`, with its effective MCP unknown -- the CALLER's requested set, never a claim
# of enforcement. A profile-sensitive job belongs on the strict sidecar path
# (scripts/lib/sidecar_common.sh), which passes --tools/--strict-mcp-config/--mcp-config and records
# `enforcement: strict`. A Workflow engine that launches a job as its row's generated agent type
# (`agentType`, `.claude/agents/<name>.md`) stamps `agent-type` on that job's recorded profile itself;
# nothing here predicts the swap. Proof: tests/test_workflow_tool_profiles.py.
INHERITED = "inherited"
AGENT_TYPE = "agent-type"
STRICT = "strict"          # the sidecar path passes every flag that bounds the child's inventory
ENFORCEMENT_LABELS = (INHERITED, AGENT_TYPE, STRICT)
EFFECTIVE_MCP_UNKNOWN = "unknown/inherited"
TOOL_PROFILE_HEADER = re.compile(r"^[ \t]*TOOL-PROFILE:[ \t]*(\{.*\})[ \t]*$", re.M)


def _tp():
    """tools/tool_profiles.py — the one defaults table, resolver and `tools` parser."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import tool_profiles
    return tool_profiles


def _delta(raw):
    """(add, remove) from a job's `tools` value. The parser lives in tools/tool_profiles.py."""
    return _tp().tools_delta(raw)


def _tool_list(value):
    """A built-in list from a `base` entry: a CSV string splits like a `-t` list."""
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def profile_request(job):
    """`{kind, add, remove, baseBuiltins}` a declared job asks for, or None when it asks for nothing.

    Nothing declared is not a default: a job that named no kind and no delta has no profile anybody
    chose, and a reader must see that as absent. A delta with no kind is returned with `kind` None, and
    `resolve_request` refuses it.

    `tools.base` carries an `arm` job's task built-in list; `arm` is not a native kind, so a base with
    a kind other than `arm` is refused at resolution.
    """
    kind = str(job.get("kind") or "").strip() or None
    add, remove = _delta(job.get("tools"))
    raw = job.get("tools") if isinstance(job.get("tools"), dict) else {}
    base = _tool_list(raw.get("base"))
    if not kind and not add and not remove and not base:
        return None
    return {"kind": kind, "add": add, "remove": remove, "baseBuiltins": base or None}


def agent_profile_request(tool_input):
    """`{kind, add, remove}` from a standalone Agent prompt header, or None when absent.

    Raises ValueError on a header that is present but unusable: a malformed profile the caller
    wrote must not be read as "no profile". A header that carries a delta but no `kind` is refused
    by `resolve_request`.
    """
    prompt = str((tool_input or {}).get("prompt") or "")
    match = TOOL_PROFILE_HEADER.search(prompt)
    if not match:
        return None
    raw = match.group(1)
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise ValueError("TOOL-PROFILE header is not JSON (%s): %s" % (exc, raw[:120]))
    if not isinstance(body, dict):
        raise ValueError("TOOL-PROFILE header must be an object: %s" % raw[:120])
    kind = str(body.get("kind") or "").strip() or None
    add, remove = _delta({k: v for k, v in body.items() if k != "kind"})
    base = _tool_list(body.get("base"))
    if not kind and not add and not remove and not base:
        raise ValueError("TOOL-PROFILE header names no kind and no delta: %s" % raw[:120])
    return {"kind": kind, "add": add, "remove": remove, "baseBuiltins": base or None}


def resolve_request(request, table=None, enforcement=None, candidates=None, route="engine"):
    """The resolved profile for a declared request, or a ValueError naming what is wrong.

    A request with no `kind` is refused, whatever else it carries: no tool set is inferred from a
    delta or from brief wording, and a job that writes is an author. A delta must have a covering
    generated agent file (`tool_profiles.native_agent_type`), because `agent()` has no `tools` option.
    """
    tp = _tp()
    table = table or tp.load_table()
    kind = request.get("kind")
    if not kind:
        raise ValueError("no kind: a job names one of %s (guards/tool_profiles.json); a tools delta "
                         "adjusts a kind and never replaces it" % ", ".join(tp.public_kinds(table)))
    profile, _spec, _keys = tp.resolve_kind(
        kind, add=request.get("add") or [], remove=request.get("remove") or [], route=route, table=table,
        base_builtins=request.get("baseBuiltins") or None, candidates=candidates,
        tools_csv=request.get("toolsCsv") or "")
    if route in ("engine", "agent") and profile.get("agentType"):
        profile["agentType"] = tp.native_agent_type(profile, table)
    profile["enforcement"] = enforcement or INHERITED
    if profile["enforcement"] not in ENFORCEMENT_LABELS:
        raise ValueError("unknown enforcement %r (legal: %s)"
                         % (profile["enforcement"], ", ".join(ENFORCEMENT_LABELS)))
    if profile["enforcement"] == INHERITED:
        profile["effectiveMcp"] = EFFECTIVE_MCP_UNKNOWN
    else:
        profile["effectiveMcp"] = ", ".join(profile.get("mcpServers") or [])
    return profile


def profile_line(label, profile):
    """One launch-table line for a resolved profile, or "" when the job declared none."""
    if not profile:
        return ""
    add = profile.get("add") or []
    remove = profile.get("remove") or []
    delta = " ".join(["+%s" % a for a in add] + ["-%s" % r for r in remove])
    mcp = ",".join(profile.get("mcpServers") or []) or "none"
    enforcement = profile.get("enforcement")
    if enforcement == INHERITED:
        note = "inherited (MCP not restricted)"
    elif enforcement == AGENT_TYPE:
        note = "agent-type (%s)" % (profile.get("agentType") or "generated type")
    else:
        note = STRICT
    mode = profile.get("overrideMode") or ""
    mode = "" if mode in ("shape-default", "delta") else " (%s)" % mode
    return "  tools %s: %s %s %s \u00b7 %s%s%s" % (
        label, profile.get("kind"), note, ",".join(profile.get("builtins") or []), mcp,
        (" " + delta) if delta else "", mode)


def undeclared_profile(label):
    """The line for a recorded dispatch whose profile nobody declared: `unknown`, never a default."""
    return "  tools %s: unknown (no profile declared)" % label
