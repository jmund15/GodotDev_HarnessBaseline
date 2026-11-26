#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse on Write|Edit — surface design litmus #1 when an edit adds a bool beside a family slot.

The litmus says a behavior-selecting bool is a strategy slot in disguise and sends the author to the family that
already owns the concern; a seam needs a second implementation. This hook reads two facts to find that case:
- the edit introduced an exported bool (present after the edit, absent before);
- the class already exports a slot whose type the repo shows to be a family: declared in a tracked `.cs` file as an
  abstract class or an interface, or as a base of two or more declared types. No type name decides, and a type the
  repo does not declare (an engine type) is never a family. It names the bool and each slot with its evidence.

States read:
- after: the file on disk (PostToolUse runs once the write has landed).
- before: `tool_response.originalFile` (null for a create); an Edit without it is undone from
  `tool_input` (`new_string` back to `old_string`); a Write without it is unknown, so the hook is silent.
- the repo: one `git grep --recurse-submodules` over the `.cs` files, run only for an edit that introduced a bool
  (bounded by _SEARCH_SECONDS).A file outside a repository, a missing git or
  a timeout reads as no family. A declaration whose base list wraps onto a later line counts fewer implementations.

Not decided here: an orthogonal axis (litmus #2) is a judgment no member settles; `rules/design_litmus.md` loads on
every .cs read, which an edit requires first.

Advisory only. Never blocks, never exits 2, fails open on any internal error.
"""

import json
import os
import re
import subprocess
import sys

# [Export] / [Export(...)] / [Export, RequiredExport], more attribute lists, modifiers, type, name.
_EXPORTED_MEMBER = re.compile(
    r"\[\s*Export\b[^\]]*\](?:\s*\[[^\]]*\])*"
    r"(?:\s+(?:public|protected|internal|private|static|new|virtual|override|required|readonly))*"
    r"\s+(?P<type>[\w.]+(?:<[^<>;{}]*>)?(?:\[\])?\??)\s+(?P<name>\w+)\s*(?:[;={]|$)",
    re.MULTILINE,
)
_WORD = re.compile(r"[A-Za-z_]\w*")
_DECLARED = re.compile(r"\b(class|interface|record|struct)\s+(\w+)")
_DERIVED = re.compile(r"\b(?:class|record)\s+(\w+)[^{;=:]*:([^{;]*)")
_MAX_TYPES = 40       # names in one search pattern
_SEARCH_SECONDS = 3.0  # the dispatcher gives a sub-hook 5 s


def exported_members(text):
    """{name: type} of every [Export] member in `text`."""
    return {m["name"]: m["type"].rstrip("?") for m in _EXPORTED_MEMBER.finditer(text or "")}


def _family_evidence(path, types):
    """{type: evidence} for each of `types` the repo shows to be a family: declared abstract or as an interface, or the
    base of two or more declared types. One search for every candidate; any failure of it reads as no family."""
    if not types:
        return {}
    names = "|".join(types)
    declared = rf"\b(class|interface|record|struct)[[:space:]]+({names})\b"
    derived = rf"\b(class|record)[[:space:]]+[A-Za-z0-9_]+[^{{;=]*:[^{{;]*\b({names})\b"
    try:
        found = subprocess.run(
            ["git", "grep", "--recurse-submodules", "-h", "-E", "-e", declared, "-e", derived, "--", ":/*.cs"],
            cwd=os.path.dirname(os.path.abspath(path)), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_SEARCH_SECONDS).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    kind, bases = {}, {name: set() for name in types}
    for line in found.splitlines():
        head = _DECLARED.search(line)
        if head and head.group(2) in bases:
            abstract = re.search(r"\babstract\b", line[:head.start()])
            kind[head.group(2)] = "abstract" if abstract else head.group(1)
        child = _DERIVED.search(line)
        if child:
            for base in _WORD.findall(re.split(r"\bwhere\b", child.group(2))[0]):
                if base in bases and base != child.group(1):
                    bases[base].add(child.group(1))
    evidence = {}
    for name in types:
        if kind.get(name) in ("abstract", "interface"):
            evidence[name] = kind[name]
        elif name in kind and len(bases[name]) >= 2:
            evidence[name] = f"{len(bases[name])} implementations"
    return evidence


def _text_before(input_data, tool_input, after):
    response = input_data.get("tool_response")
    if isinstance(response, dict) and "originalFile" in response:
        return response["originalFile"] or ""
    if input_data.get("tool_name") == "Edit" and "new_string" in tool_input:
        return after.replace(str(tool_input["new_string"]), str(tool_input.get("old_string", "")), 1)
    return None


def process(input_data):
    """Dispatcher entry: `{"context": reminder}` when the edit introduced an exported bool beside a family slot, else None."""
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None
    tool_input = input_data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = str(tool_input.get("file_path", ""))
    if not path.lower().endswith(".cs"):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            after = fh.read()
    except OSError:
        return None
    before = _text_before(input_data, tool_input, after)
    if before is None:
        return None

    members, earlier = exported_members(after), exported_members(before)
    added = [n for n, t in members.items() if t == "bool" and n not in earlier]
    if not added:
        return None
    others = {n: t for n, t in members.items() if t != "bool"}
    types = list(dict.fromkeys(w for t in others.values() for w in _WORD.findall(t)))[:_MAX_TYPES]
    family = _family_evidence(path, types)
    slots = {n: [f"{w}: {family[w]}" for w in _WORD.findall(t) if w in family] for n, t in others.items()}
    slots = {n: shown for n, shown in slots.items() if shown}
    if not slots:
        return None
    named = ", ".join("%s (%s, %s)" % (n, members[n], "; ".join(shown)) for n, shown in slots.items())
    return {"context": (
        f"[design-litmus] This edit adds exported bool {', '.join(added)}; the class already exports {named}. "
        ".claude/rules/design_litmus.md #1: if the bool selects behavior, it belongs in that family. "
        "Name the family that owns the concern, or record \"none exists\"."
    )}


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    result = process(input_data) if isinstance(input_data, dict) else None
    if result:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": result["context"],
        }}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # Advisory hook — never block on an internal error
