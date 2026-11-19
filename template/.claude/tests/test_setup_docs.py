#!/usr/bin/env python3
"""Re-runnable proof that the `stamp-inputs:` list in the workstation setup command parses.

`tools/workstation_profile.py` reads `stamp-inputs:` from the command's frontmatter to decide whether a
machine's setup stamp is current, and the shared user-scope tool, manifest and templates must be listed there.
A copy that drops a template, or misspells the key, must read as short or undeclared.

    python3 .claude/tests/test_setup_docs.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
REPO = CLAUDE.parent

DOCS = {"setup": ".claude/commands/workstation_setup.md"}

FAILURES = []


def case(label, fn):
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a proof reports, it does not stop
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s%s" % (label, (" -- " + str(detail)[:220]) if detail else ""))


def read(key, root=REPO):
    return (Path(root) / DOCS[key]).read_text(encoding="utf-8")


def _profile_tool():
    """`tools/workstation_profile.py` from this checkout, loaded by path so either cwd works."""
    spec = importlib.util.spec_from_file_location(
        "docs_workstation_profile", REPO / ".claude" / "tools" / "workstation_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declared_inputs_parse(root=REPO):
    """The tool's own reader returns the declared list from the real command doc.

    A malformed `stamp-inputs:` reads as UNAUDITED on every machine forever, which no other case in
    any proof would show.
    """
    declared = _profile_tool().stamp_inputs(root)
    return bool(declared) and ".mcp.json" in declared, "declared=%r" % (declared,)


def shared_user_scope_paths():
    """The tool and every path the shared manifest names, as repo-relative stamp inputs."""
    import json
    manifest = json.loads((REPO / ".claude/user_scope/manifest.json").read_text(encoding="utf-8"))
    paths = [".claude/tools/user_scope.py", ".claude/user_scope/manifest.json"]
    paths += [".claude/user_scope/" + item["src"] for item in manifest["items"] if "src" in item]
    return paths


def user_scope_inputs_declared(root=REPO):
    """Every shared user-scope path is a declared stamp input, so a template change flags the machine STALE."""
    declared = _profile_tool().stamp_inputs(root) or []
    missing = [path for path in shared_user_scope_paths() if path not in declared]
    return not missing, "undeclared: %r" % (missing,)


def user_scope_inputs_declared_plant():
    """The same case on a doc copy that drops one declared template must fail."""
    with tempfile.TemporaryDirectory(prefix="docs_") as tmp:
        root = Path(tmp)
        (root / ".claude" / "commands").mkdir(parents=True)
        text = read("setup").replace('  - ".claude/user_scope/statusline.py"\n', "", 1)
        (root / DOCS["setup"]).write_text(text, encoding="utf-8")
        ok, detail = user_scope_inputs_declared(root)
    return not ok, detail


def declared_inputs_parse_plant():
    """The same reader on a copy whose key is misspelled must return None, not a list."""
    with tempfile.TemporaryDirectory(prefix="docs_") as tmp:
        root = Path(tmp)
        (root / ".claude" / "commands").mkdir(parents=True)
        text = read("setup").replace("stamp-inputs:", "stamp-input:", 1)
        target = root / DOCS["setup"]
        target.write_text(text, encoding="utf-8")
        declared = _profile_tool().stamp_inputs(root)
    return declared is None, "declared=%r" % (declared,)


def main():
    case("green: the shared user-scope tool, manifest and templates are declared stamp inputs",
         user_scope_inputs_declared)
    case("RED on a planted copy: a dropped template leaves the stamp inputs short",
         user_scope_inputs_declared_plant)
    case("green: the real command doc's `stamp-inputs:` parses", declared_inputs_parse)
    case("RED on a planted copy: a misspelled `stamp-inputs:` reads as no declaration",
         declared_inputs_parse_plant)

    if FAILURES:
        print("\nFAILED:\n  " + "\n  ".join(FAILURES))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
