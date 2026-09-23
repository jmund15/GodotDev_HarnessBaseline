"""Pins a Workflow or Agent call declares: the jobs its args enumerate and the script text it runs.

Shared by hooks/workflow_provider_guard.py (policy) and hooks/dispatch_table.py (display). Imports no
quota or band code. Proof: tests/test_dispatch_pins.py.
"""
import json
import os
import re

REVIEW_FANOUT_DEFAULT_MODEL = "sonnet"   # review_fanout.js DEFAULT_MODEL — an omitted pin lands here
# DEFAULT_EFFORT of review_fanout.js, dispatch_chains.js and explore_fanout.js on the host transport.
ENGINE_DEFAULT_EFFORT = "medium"


def _args(tool_input):
    a = tool_input.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            return {}
    return a if isinstance(a, dict) else {}


def _is_review_fanout(tool_input):
    return "review_fanout" in os.path.basename(
        str(tool_input.get("scriptPath") or tool_input.get("name") or "")).replace("-", "_")


def dispatch_jobs(payload):
    """[{label, model, effort, agentType}] per job this call's ARGS declare.

    `model`/`effort` are the caller's literal values, None when omitted, except where the engine
    fills an omission: a review_fanout model, and the effort of a chain job, an explore lens or a
    review agent (ENGINE_DEFAULT_EFFORT). dispatch.js requires every pin.
    """
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if tool == "Agent":
        return [{"label": str(ti.get("description") or "agent"), "model": ti.get("model") or None,
                 "effort": None, "agentType": str(ti.get("subagent_type") or "general-purpose")}]
    a = _args(ti)
    out = []

    def job(j, label_key, default_label, default_effort=None):
        return {"label": str(j.get(label_key) or default_label), "model": j.get("model") or None,
                "effort": j.get("effort") or default_effort, "agentType": j.get("agentType") or None,
                "shape": j.get("shape") or None, "railTier": j.get("railTier") or None}

    for j in a.get("jobs") or []:
        out.append(job(j or {}, "label", "job"))
    for c in a.get("chains") or []:
        for j in (c or {}).get("jobs") or []:
            out.append(job(j or {}, "label", "job", ENGINE_DEFAULT_EFFORT))
    for lens in a.get("lenses") or []:
        out.append(job(lens or {}, "key", "lens", ENGINE_DEFAULT_EFFORT))
    review = _is_review_fanout(ti)
    for ag in a.get("agents") or []:
        row = job(ag or {}, "key", "agent", ENGINE_DEFAULT_EFFORT if review else None)
        # An omitted review_fanout pin is the engine's default, not an absent pin.
        row["model"] = row["model"] or REVIEW_FANOUT_DEFAULT_MODEL
        out.append(row)
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
