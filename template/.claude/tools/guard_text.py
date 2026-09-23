#!/usr/bin/env python3
"""Single home of delegate-guard tier extraction.

`.claude/guards/{any,survey,review,author}.md` is the one home for delegate rails. Every route
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

CLI:  guard_text.py <shape> <tier>   -> the text on stdout, exit 0
      bad arguments                  -> the legal set on stderr, exit 2
"""
import os
import re
import sys

VALID_SHAPES = ("any", "survey", "review", "author")
VALID_TIERS = ("detailed", "condensed", "minimal", "none")
OVERLAY_LAYERS = ("coding", "godot")

_GUARDS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"
)


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
    return "[delegate rails — shape '%s', %s tier; home: %s]\n%s" % (
        shape, tier, " + ".join(homes), "\n\n".join(parts))


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: guard_text.py <shape> <tier>", file=sys.stderr)
        print("  shape: %s" % ", ".join(VALID_SHAPES), file=sys.stderr)
        print("  tier:  %s" % ", ".join(VALID_TIERS), file=sys.stderr)
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(guard_text(sys.argv[1], sys.argv[2]))
    except Exception as exc:
        print("guard_text: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
