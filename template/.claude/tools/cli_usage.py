"""Extract the concise summary and usage section from CLI help text."""

import re


_USAGE = re.compile(r"^( *)(?:usage|cli|subcommands)(?::)?(?:\s|$)", re.IGNORECASE)


def usage(doc) -> str:
    if not doc:
        return ""
    lines = doc.splitlines()
    if not lines:
        return ""
    summary = lines[0].strip()
    start = next((i for i, line in enumerate(lines) if _USAGE.match(line)), None)
    if start is None:
        return summary
    indent = len(lines[start]) - len(lines[start].lstrip(" "))
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= indent:
            break
        end += 1
    return summary + "\n\n" + "\n".join(lines[start:end]).rstrip()
