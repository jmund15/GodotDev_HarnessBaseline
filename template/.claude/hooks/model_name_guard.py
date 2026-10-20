#!/usr/bin/env python3
"""Hook: PreToolUse Write|Edit, run by pre_edit_dispatch.py — mechanism-layer files name roles, never models.

The rule lives in instruction_quality §12. A model name in a hook, tool, script, workflow,
skill, command, rule, guard, agent or CLAUDE*.md file couples the harness to today's roster.

Names come from the model registry at run time (ids, aliases, served ids, versions, labels,
families, aliasHistory keys), never from a list here. A name that is one bare word can also be an
ordinary English word, so it counts only in a pin-shaped context: a code span holding only
registry names (`x`, `x·high`, `x|y`), a quoted string that is the name and not a key or subscript,
a `model` key or `--model` flag, a launcher `-m`, or an alias·effort pair whose rung the registry
declares. Any other name counts wherever it appears.

Only text the call adds is checked: a name fires when the new text carries it more often than the
text it replaces (an Edit's old_string, a Write's current file). Paths matching an entry in
hooks/model_name_exemptions.json pass; `.claude/tests/` gets an advisory instead of a deny. An
unreadable registry fails open with an advisory.

Contract: `process(payload)` returns {"deny": reason}, {"context": text} or None.
"""
import json
import os
import re
import sys

HOOKS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HOOKS)
from _claude_scope import harness_tail  # noqa: E402

MARK = "[model-name-guard]"
EXEMPTIONS_ENV = "HARNESS_MODEL_NAME_EXEMPTIONS"
EXEMPTIONS_PATH = os.path.join(HOOKS, "model_name_exemptions.json")
LAYER_DIRS = ("hooks/", "tools/", "scripts/", "workflows/", "skills/", "commands/", "rules/", "guards/",
              "agents/")
ADVISORY_DIRS = ("tests/",)
_WORD = re.compile(r"[a-z]+")
_CODE_SPAN = re.compile(r"`[^`\n]+`")


def roster():
    """(names, effort rungs) from the registry; raises on an unreadable or nameless registry."""
    sys.path.insert(0, os.path.join(os.path.dirname(HOOKS), "tools"))
    import model_registry
    with open(model_registry.find_registry(), encoding="utf-8") as fh:
        data = json.load(fh)
    names, rungs = set(), set()
    for row in [*data.get("models", []), *(data.get("transports") or {}).values()]:
        if not isinstance(row, dict):
            continue
        rungs.update(str(v).lower() for v in row.get("effortValues") or [])
        if "id" not in row:
            continue
        for key in ("id", "alias", "version", "label", "family"):
            if row.get(key):
                names.add(str(row[key]).strip().lower())
        names.update(str(s).strip().lower() for s in row.get("servedIds") or [])
    names.update(str(k).lower() for k in data.get("aliasHistory") or {})
    names.discard("")
    if not names:
        raise ValueError("no model names in the registry")
    return names, rungs


def _core(name):
    return re.escape(name).replace(r"\ ", r"\s+")


def _bounded(name):
    return r"(?<![\w.-])(?P<n>" + _core(name) + r")(?![\w-]|\.\w)"


def _pin_patterns(name, rungs):
    core = _core(name)
    rung = "|".join(re.escape(r) for r in sorted(rungs, key=len, reverse=True))
    pats = [
        r"(?<![\w\]]\[)([\"'])(?P<n>" + core + r")(?:[·:/][\w.-]+)?\1(?!\s*:)",
        r"(?:\bmodels?[\"']?\s*[:=]\s*|(?<![\w-])--?models?[ =]\s*)[\"'`]?(?P<n>" + core + r")(?![\w-])",
        r"(?<![\w-])-m\s+[\"']?(?P<n>" + core + r")(?![\w-])",
    ]
    if rung:
        pats.append(r"(?<![\w.-])(?P<n>" + core + r")[·:/](?:" + rung + r")\b")
    return [re.compile(p, re.IGNORECASE) for p in pats]


def _names_only(content, names):
    """True when a code span's content is registry names only, each optionally carrying a suffix."""
    tokens = [re.split(r"[·:/@]", t, maxsplit=1)[0].lower() for t in re.split(r"[\s|,]+", content.strip())]
    return bool(tokens) and all(t in names for t in tokens)


def matcher(names, rungs):
    """{name: count(text)} over the given names; bare-word names count only in pin contexts."""
    compiled = []
    for name in sorted(names):
        anywhere = re.compile(_bounded(name), re.IGNORECASE)
        pins = _pin_patterns(name, rungs) if _WORD.fullmatch(name) else None
        compiled.append((name, anywhere, pins))

    def count(text):
        found = {}
        for name, anywhere, pins in compiled:
            if pins is None:
                n = sum(1 for _ in anywhere.finditer(text))
            else:
                starts = {m.start("n") for p in pins for m in p.finditer(text)}
                for span in _CODE_SPAN.finditer(text):
                    if _names_only(span.group()[1:-1], names):
                        starts.update(span.start() + m.start("n") for m in anywhere.finditer(span.group()))
                n = len(starts)
            if n:
                found[name] = n
        return found
    return count


def _glob_regex(glob):
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append(r"(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(r".*")
            i += 2
        elif glob[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z", re.IGNORECASE)


def exemptions():
    """[(glob, justification)] from the exemption file; raises when it is unreadable."""
    with open(os.environ.get(EXEMPTIONS_ENV) or EXEMPTIONS_PATH, encoding="utf-8") as fh:
        return [(e["glob"], e["justification"]) for e in json.load(fh)["exemptions"]]


def scope(path):
    """`deny`, `advise`, `exempt` or None (outside the mechanism layer) for a file path."""
    norm = (path or "").replace("\\", "/")
    name = os.path.basename(norm)
    claude_md = name.startswith("CLAUDE") and name.endswith(".md")
    tail = harness_tail(norm)
    if tail is None:
        return "deny" if claude_md else None
    in_layer = tail.startswith(LAYER_DIRS) or ("/" not in tail and claude_md)
    if not in_layer and not tail.startswith(ADVISORY_DIRS):
        return None
    try:
        if any(_glob_regex(g).match(tail) for g, _ in exemptions()):
            return "exempt"
    except (OSError, ValueError, KeyError, TypeError):
        pass  # an unreadable exemption file exempts nothing
    return "deny" if in_layer else "advise"


def _before_after(payload):
    tool_input = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Edit":
        return tool_input.get("old_string") or "", tool_input.get("new_string") or ""
    try:
        with open(tool_input.get("file_path") or "", encoding="utf-8", errors="replace") as fh:
            before = fh.read()
    except OSError:
        before = ""
    return before, tool_input.get("content") or ""


def added_names(before, after, names, rungs):
    count = matcher(names, rungs)
    old = count(before)
    return sorted(n for n, k in count(after).items() if k > old.get(n, 0))


def process(payload):
    if payload.get("tool_name") not in ("Write", "Edit"):
        return None
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    posture = scope(path)
    if posture in (None, "exempt"):
        return None
    shown = ".claude/" + harness_tail(path) if harness_tail(path) is not None else path
    try:
        names, rungs = roster()
    except Exception as exc:
        return {"context": "%s The model registry is unreadable (%s: %s), so the model-name check did not "
                           "run on %s." % (MARK, type(exc).__name__, exc, shown)}
    before, after = _before_after(payload)
    hits = added_names(before, after, names, rungs)
    if not hits:
        return None
    named = ", ".join("`%s`" % h for h in hits)
    if posture == "advise":
        return {"context": "%s %s adds model name(s) %s. Test fixtures should use a synthetic roster "
                           "(plant a registry through HARNESS_MODEL_REGISTRY), so a roster change "
                           "never breaks a proof." % (MARK, shown, named)}
    return {"deny": "%s %s would add model name(s) %s. Rule (instruction_quality §12): mechanism-layer "
                    "files name roles, never models; model facts live in reference/external_models.json "
                    "and reference/model_ladder_evidence.md. Fix: name the tier and resolve it at run time "
                    "with `python3 .claude/tools/model_registry.py for-role <tier>`, or, when this file must "
                    "name what it measures, add its glob and justification to "
                    ".claude/hooks/model_name_exemptions.json." % (MARK, shown, named)}


if __name__ == "__main__":
    try:
        result = process(json.load(sys.stdin))
    except (ValueError, OSError):
        sys.exit(0)
    if result and result.get("deny"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": result["deny"]}}))
    elif result:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": result["context"]}}))
    sys.exit(0)
