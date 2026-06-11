#!/usr/bin/env python3
"""PostToolUse(Write|Edit) — soft-warn on JmoLogger.Info/Debug calls missing [Tag] prefix.

Producer side of the contract documented in .claude/skills/logging_methodology/SKILL.md.
The Tagged corpus is what makes /analyze_godot_logs --target [Tag] work — every untagged
Info/Debug call site is invisible to the analyzer's subsystem filter.

Soft warning by design: emits hookSpecificOutput.additionalContext JSON, always exits 0.
(stderr+exit-0 on PostToolUse is NOT model-visible — verified 2026-06-09, see
archive_hook_gotchas.md; additionalContext is the only advisory channel that reaches the
model.) False positives on multi-line interpolations or unusual call shapes are
tolerated. The agent (and human reviewer) makes the call on whether the warning is real.

Compliant call shapes recognized:
  JmoLogger.Info(this, "[Foo] msg")           ← magic-string tag
  JmoLogger.Info(this, $"[Foo] {x}")          ← interpolated, magic-string tag
  JmoLogger.Info(this, $"{InstrumentationTags.Hit} {x}")  ← hypothesis-tag constant

Non-compliant (warns):
  JmoLogger.Info(this, "plain message")
  JmoLogger.Debug(this, $"plain interpolated {x}")
"""
import json
import re
import sys

# Tagged: literal "[Tag]" or interpolated $"[Tag]" or $"{InstrumentationTags.X} ...".
# Captures the JmoLogger.(Info|Debug)( portion + the first argument shape afterward.
# Group 1 = level (Info/Debug). The pattern asserts that the message argument starts
# with either a bracketed tag literal OR the InstrumentationTags.X constant interpolation.
LEVEL_PATTERN = re.compile(
    r'JmoLogger\.(Info|Debug)\([^,]+,\s*\$?"',
)

# Compliant message-start shapes (any of these after the opening quote → tagged):
#   [Foo  → literal bracket tag
#   {InstrumentationTags. → constant tag interpolation
COMPLIANT_START = re.compile(r'^\$?"\s*(?:\[|\{InstrumentationTags\.)')


def scan_added_lines(text: str) -> list[tuple[int, str]]:
    """Return (line_offset, line) for each line that adds an untagged Info/Debug call."""
    findings = []
    for offset, line in enumerate(text.splitlines(), start=1):
        # Find each JmoLogger.Info/Debug call on this line.
        for match in LEVEL_PATTERN.finditer(line):
            # Re-scan starting at the second argument to confirm tag shape.
            tail = line[match.end() - 1:]  # rewind to the opening quote
            if not COMPLIANT_START.match(tail):
                findings.append((offset, line.strip()))
                break  # one warning per line is enough
    return findings


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        print("{}")
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or ""
    if not file_path.endswith(".cs"):
        print("{}")
        return 0

    # Edit gives us new_string; Write gives us content. MultiEdit isn't in the matcher
    # but if it were, edits[].new_string would be the field — bail safely.
    candidate_text = (
        tool_input.get("new_string")
        or tool_input.get("content")
        or ""
    )
    if not candidate_text:
        print("{}")
        return 0

    findings = scan_added_lines(candidate_text)
    if findings:
        rel = file_path.replace("\\", "/").rsplit("/{{PROJECT_NAME}}/", 1)[-1]
        lines = [
            f"[logger_tag_prefix] {rel}: {len(findings)} JmoLogger.Info/Debug call(s) "
            f"appear untagged. Per logging_methodology skill, prefix with [Subsystem]:"
        ]
        for offset, snippet in findings[:5]:
            # offset is within new_string, not the file — useful for short edits, advisory only.
            display = snippet if len(snippet) <= 140 else snippet[:137] + "..."
            lines.append(f"  · {display}")
        if len(findings) > 5:
            lines.append(f"  · (+{len(findings) - 5} more)")
        lines.append(
            "  (Soft warning. Use [Subsystem] tag or $\"{InstrumentationTags.X}\" "
            "for hypothesis tags.)"
        )
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(lines),
            }
        }
        print(json.dumps(payload))
        return 0

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
