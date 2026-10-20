"""Brief coverage of a Workflow call's jobs: which lean agent-type shapes each brief allows, the
widening of a job whose declared profile or asked generated agent type lacks a tool its brief needs,
the refusal of what no widening grants, and the names-only skill index.

`hooks/workflow_provider_guard.py` injects `coverage_map` as `args.__coverage` and `skill_index` as
`args.__skillIndex` beside `args.__toolProfiles`; the engines swap a job to its shape's agent type
only when its entry covers that shape. The detector is `tools/tool_profiles.brief_requirements`;
this module only gathers each job's caller-authored brief the way the engines hand it over.
Proof: tests/test_brief_coverage.py.
"""
import os

from _dispatch_pins import _args, _delta, _tp, dispatch_jobs, profile_request, resolve_request

SKILL_INDEX_HEAD = ("Skills and commands in this project (names only; Read .claude/skills/<name>/SKILL.md "
                    "or .claude/commands/<name>.md when your task enters that domain): ")

# Shared context an engine adds to every job prompt: inline text, and a file the child is told to
# read first. Inline: review_fanout.js and idea_fanout.js `contextPrefix`; doc_npc_all.js and
# doc_architecture_audit.js `context`. Files: review_fanout.js and explore_fanout.js
# `contextPrefixPath`; dispatch.js and dispatch_chains.js `contextPath`.
SHARED_TEXT_KEYS = ("contextPrefix", "context")
SHARED_FILE_KEYS = ("contextPrefixPath", "contextPath", "contextFiles")


def _root(root=None):
    """The checkout a job's relative brief path resolves against: the engines' cwd."""
    return root or os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _paths(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value or [] if str(v).strip()]


def _job_briefs(payload, root, tp):
    """[(job, texts, unread)] per job: its prompt or promptPath file plus every shared context."""
    if payload.get("tool_name") != "Workflow":
        return []
    args = _args(payload.get("tool_input") or {})
    shared_texts = [("args:%s" % key, args[key]) for key in SHARED_TEXT_KEYS
                    if isinstance(args.get(key), str) and args[key].strip()]
    shared_files = [p for key in SHARED_FILE_KEYS for p in _paths(args.get(key))]
    out = []
    for job in dispatch_jobs(payload):
        texts, unread = [], []
        if job.get("prompt"):
            texts.append(("%s:prompt" % job["label"], str(job["prompt"])))
        for rel in _paths(job.get("promptPath")) + shared_files + _paths(job.get("contextFiles")):
            try:
                texts.append((rel, tp.read_brief(tp.resolve_brief_path(rel, root))))
            except OSError:
                unread.append(rel)
        if texts:
            texts += shared_texts
        out.append((job, texts, unread))
    return out


_LAST_SCAN = None


def _scan(payload, root):
    """`(table, [(job, texts, report, entry)])` for a payload, computed once per payload object.

    The guard hands one payload to both `coverage_refusal` and `coverage_map`; the second call
    reuses the first call's reads. `entry` is the job's `{covered, needs, unread}`: a job with any
    unreadable file, pointed-to or direct, or no brief at all, covers no shape.
    """
    global _LAST_SCAN
    if _LAST_SCAN and _LAST_SCAN[0] is payload and _LAST_SCAN[1] == root:
        return _LAST_SCAN[2]
    tp = _tp()
    table = tp.load_table()
    out = []
    for job, texts, unread in _job_briefs(payload, root, tp):
        report = None
        try:
            report = tp.brief_requirements(texts, root)
            entry = tp.coverage_of(report, table)
        except Exception:
            entry = {"covered": [], "needs": [], "unread": []}
        entry["unread"] = unread + [p for p in entry["unread"] if p not in unread]
        if entry["unread"] or not texts:
            entry["covered"] = []
        out.append((job, texts, report, entry))
    _LAST_SCAN = (payload, root, (table, out))
    return table, out


def coverage_map(payload, root=None):
    """`{label: {covered, needs, unread}}` for a Workflow payload; {} for anything else. Never raises."""
    try:
        _table, scanned = _scan(payload, _root(root))
        return {job["label"]: entry for job, _texts, _report, entry in scanned}
    except Exception:
        return {}


def _generated_types(table):
    """{agentType: shape} for every table row that renders a generated agent type."""
    return {row["agentType"]: name for name, row in table["shapes"].items() if row.get("agentType")}


def coverage_fixes(payload, root=None):
    """`(payload, notes)` with each job widened to what its brief proves it runs.

    An uncovered generated agentType becomes general-purpose. A declared tool profile gains every
    missing tool its brief runs, merged into the job's `tools.add`, and a note names each one with
    its matched lines. Never added, so `coverage_refusal` still denies them: a tool the job's own
    `tools.remove` names (the author chose the narrower profile), a reserved built-in (no delta grants
    Workflow or Agent), and anything on an `arm` job (its task row is the measured variable).
    """
    import copy
    import json

    if payload.get('tool_name') != 'Workflow' or os.environ.get('HARNESS_WORKFLOW_ARGS_OWNER'):
        return payload, []
    table, scanned = _scan(payload, _root(root))
    generated = _generated_types(table)
    fixed = copy.deepcopy(payload)
    ti = fixed.get('tool_input') or {}
    raw = ti.get('args')
    args = _args(ti)
    jobs = list(args.get('jobs') or [])
    jobs += [job for chain in args.get('chains') or [] for job in chain.get('jobs') or []]
    jobs += list(args.get('lenses') or []) + list(args.get('agents') or [])
    notes = []
    tp = _tp()
    for target, (job, texts, report, entry) in zip(jobs, scanned):
        asked = job.get('agentType')
        if asked in generated and generated[asked] not in entry['covered']:
            target['agentType'] = 'general-purpose'
            why = ('brief needs ' + ', '.join(entry['needs']) if entry['needs'] else
                   'brief coverage is unproven')
            notes.append("job %r: changed agentType %r to general-purpose (%s)" %
                         (job['label'], asked, why))
            continue
        added = _missing_grantable(job, texts, report, table, tp)
        if added:
            tools, matches = added
            raw = target.get('tools')
            add, remove = _delta(raw)
            kept = {k: v for k, v in raw.items() if k in ('shape', 'base')} if isinstance(raw, dict) else {}
            target['tools'] = dict(kept, add=add + [t for t in tools if t not in add],
                                   **({'remove': remove} if remove else {}))
            notes.append("job %r: added %s to its tool profile, which its brief runs:\n%s" %
                         (job['label'], ', '.join(tools), tp._matched_lines(matches)))
    if notes:
        ti['args'] = json.dumps(args) if isinstance(raw, str) else args
        return fixed, notes
    return payload, []


def _missing_grantable(job, texts, report, table, tp):
    """`(tools, matches)` a declared job's brief runs that its profile lacks and a delta may grant,
    else None: no declared profile, an arm job, an unscanned brief, or nothing missing."""
    request = profile_request(job)
    if not request or not texts or report is None or request.get("shape") == "arm":
        return None
    try:
        builtins = resolve_request(request, table=table)["builtins"]
    except Exception:
        return None  # an unresolvable profile is tool_profile_refusal's deny
    removed = set(request.get("remove") or [])
    matches = [m for m in report["matches"] if not m["negated"] and m["tool"] not in builtins
               and m["tool"] not in tp.RESERVED_BUILTINS and m["tool"] not in removed]
    if not matches:
        return None
    return tp._canonical([m["tool"] for m in matches], table["canonicalOrder"]), matches


def coverage_refusal(payload, root=None):
    """The deny reason when a job's brief needs a tool its declared profile lacks after
    `coverage_fixes` (an explicitly removed tool, a reserved built-in, or a rewrite left to
    HARNESS_WORKFLOW_ARGS_OWNER), or when a job asks for a generated agent type whose shape its brief
    is not proven to fit; else None."""
    try:
        table, scanned = _scan(payload, _root(root))
        tp = _tp()
    except Exception:
        return None
    generated = _generated_types(table)
    refusals = []
    for job, texts, report, entry in scanned:
        asked = job.get("agentType")
        if asked in generated and generated[asked] not in entry["covered"]:
            if entry["needs"]:
                why = "its brief needs %s" % ", ".join(entry["needs"])
            elif entry["unread"]:
                why = "its brief could not be read in full (%s)" % ", ".join(entry["unread"])
            else:
                why = "its brief could not be scanned"
            refusals.append("job %r asks for agentType %r directly, but %s, so the %r shape is not "
                            "proven to cover it. Ask for `general-purpose` and let the engine swap "
                            "to the lean type when coverage proves it."
                            % (job["label"], asked, why, generated[asked]))
            continue
        request = profile_request(job)
        if not request or not texts or report is None or request.get("shape") == "arm":
            continue  # an arm's task row is the measured variable of a benchmark cell, never widened
        try:
            builtins = resolve_request(request, table=table)["builtins"]
            text = tp.brief_refusal(builtins, report, "job", table)
        except Exception:
            continue  # an unresolvable profile is tool_profile_refusal's deny, not this one's
        if text:
            refusals.append("job %r: %s" % (job["label"], text))
    if not refusals:
        return None
    return "brief coverage: a job's tool profile does not cover its brief.\n" + "\n".join(refusals)


def skill_index(claude_dir=None):
    """`args.__skillIndex`: the sorted names the model's skill listing shows (load_census rules)."""
    import sys
    from pathlib import Path
    claude = Path(claude_dir) if claude_dir else Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(claude / "tools"))
    import load_census
    names = set()
    for row in load_census.listing_census(claude)["rows"]:
        if row["kind"] == "skill":
            names.add(row["name"])
        elif row["kind"] == "command":
            names.add(Path(row["name"]).stem)
    return SKILL_INDEX_HEAD + ", ".join(sorted(names))
