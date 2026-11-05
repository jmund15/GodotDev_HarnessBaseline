#!/usr/bin/env python3
"""Print Python function and class declarations without importing source files."""

import argparse
import ast
import json
import sys
import tokenize
from pathlib import Path

# Statements whose bodies can hold a def; `try*` (ast.TryStar) exists from Python 3.11.
BLOCKS = tuple(getattr(ast, name) for name in
               ("If", "For", "AsyncFor", "While", "With", "AsyncWith", "Try", "TryStar", "Match")
               if hasattr(ast, name))

def _outline(path):
    try:
        with tokenize.open(path) as source_file:
            source = source_file.read()
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError, LookupError) as exc:
        return None, f"{type(exc).__name__}: {exc}"

    rows = []

    def visit(body, scope=()):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = (*scope, node.name)
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                first_line = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                rows.append({
                    "line": node.lineno,
                    "kind": kind,
                    "name": ".".join(name),
                    "start": first_line,
                    "end": node.end_lineno,
                })
                visit(node.body, name)
            elif isinstance(node, BLOCKS):
                visit(getattr(node, "body", ()), scope)
                for handler in getattr(node, "handlers", ()):
                    visit(handler.body, scope)
                visit(getattr(node, "orelse", ()), scope)
                visit(getattr(node, "finalbody", ()), scope)
                for case in getattr(node, "cases", ()):
                    visit(case.body, scope)

    visit(tree.body)
    return rows, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    results = []
    failed = False
    for path in args.paths:
        rows, error = _outline(path)
        failed |= error is not None
        results.append({"path": str(path), "rows": rows, "error": error})

    if args.as_json:
        print(json.dumps(results, ensure_ascii=False))
    else:
        for result in results:
            if result["error"]:
                print(f"{result['path']}: {result['error']}", file=sys.stderr)
                continue
            for row in result["rows"]:
                print(
                    f"{result['path']}:{row['line']} {row['kind']} "
                    f"{row['name']} [{row['start']}:{row['end']}]"
                )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
