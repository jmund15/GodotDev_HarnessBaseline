#!/usr/bin/env python3
"""The one reader of C# and GDScript type declarations: `declared_types(line)` returns the type names a source line declares.

A declaration is anchored at the start of the code after attributes and C# modifiers, so comment lines,
doc prose and `where T : class` constraints yield nothing. `record class` and `record struct` yield the
name that follows. GDScript `class_name X` and inner `class X:` are kept. Import it from a caller; the
callers hold no second copy of the pattern.
"""
from __future__ import annotations

import re

_MODIFIERS = r"(?:(?:public|internal|private|protected|file|sealed|abstract|partial|static|readonly|ref|unsafe|new)\s+)*"
_DECL = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*" + _MODIFIERS
    + r"(?:record\s+(?:class|struct)|class|struct|enum|interface|record)\s+([A-Za-z_]\w*)"
    r"|^\s*class_name\s+([A-Za-z_]\w*)"
)


def declared_types(line: str) -> list[str]:
    match = _DECL.match(line)
    if not match:
        return []
    return [match.group(1) or match.group(2)]
