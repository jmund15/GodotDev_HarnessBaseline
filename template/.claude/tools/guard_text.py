#!/usr/bin/env python3
"""Single home of delegate-guard tier extraction.

`.claude/guards/{any,survey,review,author,arm}.md` is the one home for delegate rails. Every route
delivers this module's assembly and none may re-implement the parse:

  * hooks/workflow_provider_guard.py — INLINES, as `args.__railsText`, for the four Workflow
                                 engines (dispatch, dispatch_chains, explore_fanout, review_fanout);
                                 each engine keeps a Read pointer only as its fallback
  * hooks/session_model_rails  — INLINES, via guard_text(), for a sidecar `claude` child
  * scripts/lib/sidecar_common.sh — INLINES, via this module's CLI, on the -D bare/pointer
                                 tiers where no project hook fires

A second implementation in awk or JS would drift and fail silently: a guard that extracts the
wrong section still looks like a guard.

`any` is CONCATENATED into every other shape rather than chained by reference. Its section
carries the concurrency bar, the read-only bar, the confidence ladder and the terseness rail —
the rules that bind every delegate — so it must not depend on the delegate choosing to follow
a pointer it finds inside another file.

A higher baseline layer adds rails through an overlay, `<shape>.<layer>.md` (layers in
OVERLAY_LAYERS order). Each present overlay's tier section follows its base file's section; an
overlay without that tier adds nothing, and a project without the layer has no overlay file.

The ai-worker clause is rendered against THIS machine. `guards/any.md` names
`mcp__ai-worker__read_files` as the bulk-copyable route, which is false on a machine where the
server is not registered: the delegate would obey an instruction naming a tool that is not there
and silently change search behavior. With `workstation_profile.ai_worker()` saying `"absent"` the
clause renders as the route such a machine does have — bounded Read or a subagent — and every
consumer above gets it for free, because this module is the one assembly they all deliver. A
registration walk that cannot run is unknown, not absent, and keeps the authored text.

CLI:  guard_text.py <shape> <tier> [model]   -> the text on stdout, exit 0; a model appends its
      registry driver notes, as the sidecar launcher's isolated tiers need (no hook fires there)
      bad arguments                  -> the legal set on stderr, exit 2
"""
import os
import re
import sys

VALID_SHAPES = ("any", "survey", "review", "author", "arm", "bench-score")
VALID_TIERS = ("detailed", "condensed", "minimal", "none")
OVERLAY_LAYERS = ("coding", "godot")

_GUARDS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"
)

# The authored clause names a TOOL; the machine-absent rendering names a ROUTE. Both the tool name
# and the route are single strings so the substitution cannot half-apply.
AI_WORKER_TOOL = "mcp__ai-worker__read_files"
AI_WORKER_ABSENT_ROUTE = "bounded Read or a subagent"


def _ai_worker_absent() -> bool:
    """Is ai-worker registered on this machine? `False` whenever that cannot be read.

    `workstation_profile.ai_worker()` owns the walk (user, local, project and plugin scopes) and
    returns `None` for unknown. Unknown is deliberately NOT absent: the authored rails are the safe
    delivery, and narrowing them on a guess would drop a route that works.
    """
    try:
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import workstation_profile
        return workstation_profile.ai_worker() == "absent"
    except Exception:
        return False


def driver_notes_block(model: str, data=None) -> str:
    """The registry's per-model corrections for a model as one block ("" if none), the same text on the
    SessionStart, sidecar-inline and Workflow routes."""
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import model_registry
    notes = model_registry.driver_notes(model, data)
    if not notes:
        return ""
    return "[session] Driver notes (%s):\n%s" % (model, "\n".join("- %s (%s)" % note for note in notes))


def _driver_notes(model: str) -> str:
    """`driver_notes_block` as a suffix after the rails of a sidecar child; "" if none."""
    block = driver_notes_block(model)
    return "\n\n" + block if block else ""


def _section(path: str, tier: str) -> str:
    """Return the body of the `## <tier>` section, exclusive of its heading."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    out, capturing = [], False
    for line in lines:
        if line.startswith("## "):
            if capturing:
                break
            capturing = line[3:].strip() == tier
            continue
        if capturing:
            out.append(line)
    body = "\n".join(out).strip()
    if not body:
        raise ValueError("no '## %s' section in %s" % (tier, path))
    # A section whose whole body points at a sibling ("Read this file's `## condensed` section.")
    # serves a delegate that opens the file; an inline delivery must carry the sibling's text.
    redirect = re.fullmatch(r"Read this file's `## (\w+)` section\.", body)
    if redirect and redirect.group(1) != tier:
        return _section(path, redirect.group(1))
    return body


def guard_text(shape: str, tier: str) -> str:
    """Assemble the rails a delegate of this shape and tier receives.

    Returns "" for tier `none` — an empty string is a valid delivery, distinct from the
    ValueError raised when a requested section is missing.
    """
    if shape not in VALID_SHAPES:
        raise ValueError("unknown shape %r (legal: %s)" % (shape, ", ".join(VALID_SHAPES)))
    if tier not in VALID_TIERS:
        raise ValueError("unknown tier %r (legal: %s)" % (tier, ", ".join(VALID_TIERS)))
    if tier == "none":
        return ""

    names = ["any"] if shape == "any" else ["any", shape]
    homes, parts = [], []
    for name in names:
        homes.append(".claude/guards/%s.md" % name)
        parts.append(_section(os.path.join(_GUARDS, name + ".md"), tier))
        for layer in OVERLAY_LAYERS:
            overlay = os.path.join(_GUARDS, "%s.%s.md" % (name, layer))
            if not os.path.isfile(overlay):
                continue
            try:
                parts.append(_section(overlay, tier))
            except ValueError:
                continue
            homes.append(".claude/guards/%s.%s.md" % (name, layer))
    text = "[delegate rails — shape '%s', %s tier; home: %s]\n%s" % (
        shape, tier, " + ".join(homes), "\n\n".join(parts))
    if AI_WORKER_TOOL in text and _ai_worker_absent():
        text = text.replace(AI_WORKER_TOOL, AI_WORKER_ABSENT_ROUTE)
    return text


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print("usage: guard_text.py <shape> <tier> [model]", file=sys.stderr)
        print("  shape: %s" % ", ".join(VALID_SHAPES), file=sys.stderr)
        print("  tier:  %s" % ", ".join(VALID_TIERS), file=sys.stderr)
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        text = guard_text(sys.argv[1], sys.argv[2])
        if len(sys.argv) == 4:
            text += _driver_notes(sys.argv[3])
        sys.stdout.write(text)
    except Exception as exc:
        print("guard_text: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
