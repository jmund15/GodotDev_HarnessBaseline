#!/usr/bin/env python3
"""Guard against the same interface being hand-rolled as a test double over and over.

A test that needs an IAnimationOrchestrator writes a `private sealed partial class
FakeAnimationOrchestrator : Node, IAnimationOrchestrator` at the bottom of its own file. That is
correct once. By the seventh copy the interface has seven independently-drifting stand-ins: adding a
member to the interface means seven edits, each double implements a slightly different no-op, and a
behavioural assumption fixed in one is silently absent from the other six. Tests/Framework/Mocks is
the sanctioned shared home for exactly this -- but nothing pointed at it, so the file-local copy kept
winning on convenience.

Detection: a NON-public (`private`/`internal`/`file`) class/record declared anywhere under Tests/,
whose base list names a type declared in this repository. Nested and file-scoped declarations both
count -- both are file-local doubles. Grouping is per base name, so `: Node, IAnimationOrchestrator`
is attributed to IAnimationOrchestrator and not to Godot's Node.

Externality is decided structurally, not by a hand-maintained name list: PROJECT_TYPES is every
class/interface/record/struct declared in tracked C# ({{PROJECT_NAME}} + Jmodot + Tests). A base the
repo does not declare is by construction engine/BCL surface (Node, RefCounted, IDisposable,
IEquatable<T>, ...) and is skipped.

Tests/Framework is exempt -- that IS the shared-double home, and a double living there is the
resolution this guard asks for, not a violation.

Threshold: families at or above FAIL_AT distinct doubles exit 1 -- but only when they EXCEED the
committed baseline (duplicate_test_double_baseline.json beside this script). The baseline
grandfathers the pre-existing backlog so the gate ratchets against GROWTH instead of blocking every
commit on debt this guard was born into; the backlog itself is the test-curation sweep's job, and
consolidating a family below its baseline count is locked in by regenerating the baseline
(--write-baseline). Families at 2 are reported as advisory and exit 0.

Resolution: promote one double to Tests/Framework/Mocks (public, parameterised over whatever the
per-test variants actually needed -- a recording list, a canned return) and delete the copies.
Blanket-disable with PP_ALLOW_DUPLICATE_TEST_DOUBLES=1.

Modes:
    duplicate_test_double_guard.py                  # scan Tests/; exit 1 on growth past the baseline
    duplicate_test_double_guard.py --json           # machine-readable on stdout, always exit 0
    duplicate_test_double_guard.py --hook           # PreToolUse: deny a `git commit` that grows one
    duplicate_test_double_guard.py --write-baseline # regenerate the grandfather baseline (run after
                                                    # consolidating a family, to lock the gain in)

Not registered in settings.json. Intended consumer is /regression_gate step 1g, alongside
trail_mutation_seam_guard.py and test_suite_gate_coverage_guard.py, which invoke the standalone mode.
"""
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

TESTS_ROOT = REPO / "Tests"

# The shared-double home. A double here is the fix, not the finding.
EXEMPT_TEST_DIRS = {"Framework"}

# Distinct doubles of one base before the guard blocks. 2 is advisory, 3 is a missing abstraction.
FAIL_AT = 3

# Grandfathered counts: {base: count} at baseline time. Blocking requires BOTH >= FAIL_AT and
# > baseline count, so the gate fires on growth only. Regenerate with --write-baseline.
BASELINE_PATH = Path(__file__).with_name("duplicate_test_double_baseline.json")

# Sites printed per family in the human report -- enough to locate the pattern without burying the
# gate's output under a 200-line dump. `--json` always carries the complete set.
MAX_SITES_SHOWN = 5

# Directories that hold no first-party declarations worth indexing as project types.
SKIP_DIRS = {"obj", "bin", ".godot", ".claude", "addons", "Temp", ".git", ".search-index"}

NON_PUBLIC = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*"
    r"(?:private|internal|file)\s+"
    r"(?:(?:sealed|partial|abstract|static|unsafe|new|readonly|ref)\s+)*"
    r"(?:class|record|struct)\s+(\w+)\s*(?:<[^>]*>)?\s*:\s*(.+)$"
)

ANY_DECL = re.compile(
    r"\b(?:class|interface|record|struct)\s+(\w+)\s*(?:<[^>]*>)?\s*"
)


def split_bases(base_text):
    """Split a C# base list on top-level commas, tolerating generic arguments."""
    text = base_text.split("//")[0]
    for terminator in ("{", " where "):
        cut = text.find(terminator)
        if cut != -1:
            text = text[:cut]
    parts, depth, current = [], 0, ""
    for char in text:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)

    names = []
    for part in parts:
        name = part.strip()
        if not name:
            continue
        name = re.sub(r"<.*", "", name)          # drop generic arguments
        name = name.rsplit(".", 1)[-1]           # drop namespace qualification
        if re.fullmatch(r"\w+", name):
            names.append(name)
    return names


def source_files(root):
    for path in sorted(root.rglob("*.cs")):
        rel = path.relative_to(REPO)
        if SKIP_DIRS & set(rel.parts[:-1]):
            continue
        yield path, rel


def read_lines(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def project_types():
    """Every type name this repository declares -- anything else is engine/BCL surface."""
    names = set()
    for path, _rel in source_files(REPO):
        for line in read_lines(path):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "///")):
                continue
            names.update(ANY_DECL.findall(stripped))
    return names


def find_doubles(known_types):
    """Return {base_name: [(rel_path, line_no, double_name)]} for file-local test doubles."""
    families = {}
    if not TESTS_ROOT.is_dir():
        return families
    for path, rel in source_files(TESTS_ROOT):
        parts = rel.parts
        if len(parts) > 1 and parts[1] in EXEMPT_TEST_DIRS:
            continue
        for index, line in enumerate(read_lines(path)):
            match = NON_PUBLIC.match(line)
            if not match:
                continue
            double, base_text = match.group(1), match.group(2)
            for base in split_bases(base_text):
                if base not in known_types or base == double:
                    continue
                families.setdefault(base, []).append((rel.as_posix(), index + 1, double))
    return families


def load_baseline():
    try:
        with open(BASELINE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def find_violations():
    """Return (blocking, grandfathered, advisory) lists, each [(base, [(rel, line, name), ...])].

    blocking: >= FAIL_AT doubles AND grown past the committed baseline.
    grandfathered: >= FAIL_AT but within baseline -- pre-existing debt, curation-sweep territory.
    advisory: exactly 2 doubles.
    """
    baseline = load_baseline()
    families = find_doubles(project_types())
    ranked = sorted(
        ((base, sites) for base, sites in families.items() if len(sites) >= 2),
        key=lambda item: (-len(item[1]), item[0]),
    )
    blocking, grandfathered, advisory = [], [], []
    for base, sites in ranked:
        if len(sites) < FAIL_AT:
            advisory.append((base, sites))
        elif len(sites) > baseline.get(base, 0):
            blocking.append((base, sites))
        else:
            grandfathered.append((base, sites))
    return blocking, grandfathered, advisory


def write_baseline():
    """Snapshot every family at/above FAIL_AT as the new grandfather baseline."""
    families = find_doubles(project_types())
    snapshot = {base: len(sites) for base, sites in families.items() if len(sites) >= FAIL_AT}
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(snapshot.items())), fh, indent=2)
        fh.write("\n")
    print(f"[duplicate-test-double-guard] baseline written: {len(snapshot)} grandfathered "
          f"families -> {BASELINE_PATH.name}")
    return 0


def render(families, stream):
    for base, sites in families:
        print(f"  {base}  ({len(sites)} doubles)", file=stream)
        for rel, line_no, double in sites[:MAX_SITES_SHOWN]:
            print(f"      {rel}:{line_no}  {double}", file=stream)
        if len(sites) > MAX_SITES_SHOWN:
            print(f"      ... +{len(sites) - MAX_SITES_SHOWN} more (--json for the full set)",
                  file=stream)


def report(blocking, grandfathered, advisory):
    print(
        f"[duplicate-test-double-guard] {len(blocking)} interface(s)/base(s) hand-rolled as "
        f"{FAIL_AT}+ separate test doubles AND grown past the committed baseline:",
        file=sys.stderr,
    )
    render(blocking, sys.stderr)
    if grandfathered:
        print(f"\n  grandfathered ({len(grandfathered)} families within baseline -- curation-sweep "
              "backlog, not blocking):", file=sys.stderr)
        render(grandfathered[:3], sys.stderr)
    if advisory:
        print(f"\n  advisory ({FAIL_AT - 1} doubles, not blocking):", file=sys.stderr)
        render(advisory, sys.stderr)
    print(
        "\nEach copy is an independently-drifting stand-in: an interface member added later means N "
        "edits, and a behavioural assumption fixed in one double is silently absent from the rest. "
        "Promote ONE of them to Tests/Framework/Mocks as a public double, parameterised over "
        "whatever the per-test variants actually differ on (a recording list, a canned return), and "
        "delete the file-local copies. Blanket-disable with PP_ALLOW_DUPLICATE_TEST_DOUBLES=1.",
        file=sys.stderr,
    )


def emit_json():
    blocking, grandfathered, advisory = find_violations()

    def rows(families):
        return [
            {"base": base, "count": len(sites),
             "sites": [{"file": rel, "line": line, "double": name} for rel, line, name in sites]}
            for base, sites in families
        ]

    print(json.dumps({
        "fail_at": FAIL_AT,
        "baseline": load_baseline(),
        "blocking": rows(blocking),
        "grandfathered": rows(grandfathered),
        "advisory": rows(advisory),
    }, indent=2))
    return 0


def standalone():
    if os.environ.get("PP_ALLOW_DUPLICATE_TEST_DOUBLES"):
        print("[duplicate-test-double-guard] SKIPPED - PP_ALLOW_DUPLICATE_TEST_DOUBLES is set.")
        return 0
    blocking, grandfathered, advisory = find_violations()
    if not blocking:
        summary = []
        if grandfathered:
            summary.append(f"{len(grandfathered)} grandfathered (within baseline)")
        if advisory:
            summary.append(f"{len(advisory)} advisory at {FAIL_AT - 1}")
        print(
            "[duplicate-test-double-guard] OK - no family grew past the baseline"
            + (f" ({'; '.join(summary)})" if summary else "")
            + "."
        )
        return 0
    report(blocking, grandfathered, advisory)
    return 1


def hook():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0
    if data.get("tool_name") != "Bash":
        print("{}")
        return 0
    if "git commit" not in data.get("tool_input", {}).get("command", ""):
        print("{}")
        return 0
    if os.environ.get("PP_ALLOW_DUPLICATE_TEST_DOUBLES"):
        print("{}")
        return 0

    blocking, _grandfathered, _advisory = find_violations()
    if not blocking:
        print("{}")
        return 0

    # permissionDecisionReason is the only model-visible channel here (stderr on an exit-0
    # PreToolUse is not), so the families go in the reason itself rather than a stderr report.
    families = "; ".join(f"{base} ({len(sites)})" for base, sites in blocking[:5])
    overflow = "" if len(blocking) <= 5 else f" (+{len(blocking) - 5} more)"
    reason = (
        f"Blocked: {len(blocking)} interface(s)/base(s) hand-rolled as {FAIL_AT}+ separate "
        f"file-local test doubles, grown past the committed baseline: {families}{overflow}. Each "
        "copy drifts independently -- promote one to Tests/Framework/Mocks as a public "
        "parameterised double and delete the copies, or set PP_ALLOW_DUPLICATE_TEST_DOUBLES=1."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    if args and args[0] == "--json":
        return emit_json()
    if args and args[0] == "--write-baseline":
        return write_baseline()
    return standalone()


if __name__ == "__main__":
    sys.exit(main())
