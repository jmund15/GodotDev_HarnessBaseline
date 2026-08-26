#!/usr/bin/env python3
"""Single home of delegate-guard tier extraction.

`.claude/guards/{any,survey,review,author}.md` is the one home for delegate rails.
Four transports deliver them and none may re-implement the parse:

  * workflows/dispatch.js      — REFERENCES both files (in-process subagents, no SessionStart)
  * workflows/review_fanout.js — REFERENCES both files
  * hooks/session_model_rails  — INLINES, via guard_text(), for a sidecar `claude` child
  * scripts/lib/sidecar_common.sh — INLINES, via this module's CLI, on the -D bare/pointer
                                 tiers where no project hook fires
    (scripts/codex_sidecar.sh, a former CLI caller, is retired — refusing stub since 2026-08-20)

The two inlining callers share this parse. A second implementation in awk or JS is the drift
that dispatch.js:73 already warns against, and it would fail silently: a guard that extracts
the wrong section still looks like a guard.

`any` is CONCATENATED into every other shape rather than chained by reference. Its section
carries the concurrency bar, the read-only bar, the confidence ladder and the terseness rail —
the rules that bind every delegate — so it must not depend on the delegate choosing to follow
a pointer it finds inside another file.

CLI:  guard_text.py <shape> <tier>   -> the text on stdout, exit 0
      bad arguments                  -> the legal set on stderr, exit 2
"""
import os
import sys

VALID_SHAPES = ("any", "survey", "review", "author")
VALID_TIERS = ("strict", "terse", "none")

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
    return body


def guard_text(shape: str, tier: str) -> str:
    """Assemble the rails a delegate of this shape and tier receives.

    Returns "" for tier `none` (the fable row in dispatch.js TIER_OF) — an empty string is a
    valid delivery, distinct from the ValueError raised when a requested section is missing.
    """
    if shape not in VALID_SHAPES:
        raise ValueError("unknown shape %r (legal: %s)" % (shape, ", ".join(VALID_SHAPES)))
    if tier not in VALID_TIERS:
        raise ValueError("unknown tier %r (legal: %s)" % (tier, ", ".join(VALID_TIERS)))
    if tier == "none":
        return ""

    universal = _section(os.path.join(_GUARDS, "any.md"), tier)
    if shape == "any":
        return "[delegate rails — shape 'any', %s tier; home: .claude/guards/any.md]\n%s" % (
            tier,
            universal,
        )

    specific = _section(os.path.join(_GUARDS, shape + ".md"), tier)
    return (
        "[delegate rails — shape '%s', %s tier; home: .claude/guards/any.md + "
        ".claude/guards/%s.md]\n%s\n\n%s" % (shape, tier, shape, universal, specific)
    )


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
