"""Extract the concise summary and usage section from CLI help text, and print a refusal's shape.

Every measured tool's refusal ends with the same two lines — what the argument accepts and one call
that works. `refusal` writes them; `ShapeParser` makes an argparse usage error carry them too.
"""

import argparse
import re
import sys


_USAGE = re.compile(r"^( *)(?:usage|cli|subcommands)(?::)?(?:\s|$)", re.IGNORECASE)


def refusal(accepts, example) -> str:
    return "accepts: %s\nexample: %s\n" % (accepts, example)


class ShapeParser(argparse.ArgumentParser):
    """ArgumentParser whose usage error ends with `accepts:` and `example:`.

    Each parser sets `accepts`/`example` for its own operation, so a subparser's error never names
    another operation's flags.
    """
    accepts = ""
    example = ""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("error: %s\n%s" % (message, refusal(self.accepts, self.example)))
        raise SystemExit(2)


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
