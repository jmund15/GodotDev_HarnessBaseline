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

Threshold: a repo-declared interface family exits 1 at 2 or more doubles only when its count
exceeds the committed baseline (duplicate_test_double_baseline.json beside this script).
An interface family whose doubles each derive a different project class (no two can share one
type) is advisory too. Class (abstract or not), record and struct families stay advisory at 2 or more; the baseline
grandfathers existing families and --write-baseline locks in consolidation gains.

Resolution: promote one double to Tests/Framework/Mocks (public, parameterised over whatever the
per-test variants actually needed -- a recording list, a canned return) and delete the copies.
Blanket-disable with HARNESS_ALLOW_DUPLICATE_TEST_DOUBLES=1.

Modes:
    duplicate_test_double_guard.py                  # scan Tests/; exit 1 on growth past the baseline
    duplicate_test_double_guard.py --json           # machine-readable on stdout, always exit 0
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cs_files import cs_files  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

TESTS_ROOT = REPO / "Tests"

# The shared-double home. A double here is the fix, not the finding.
EXEMPT_TEST_DIRS = {"Framework"}

INTERFACE_FAIL_AT = 2
REPORT_AT = 2

# Grandfathered counts: {base: count} at baseline time. Only interface growth blocks.
# Regenerate with --write-baseline.
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
    r"\b(class|interface|record|struct)\s+(\w+)\s*(?:<[^>]*>)?\s*"
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
    for path, _ in cs_files(root, SKIP_DIRS):
        yield path, path.relative_to(REPO)


def read_lines(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def project_types():
    """Return {type_name: declaration_kind}; anything else is engine/BCL surface."""
    kinds = {}
    for path, _rel in source_files(REPO):
        for line in read_lines(path):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "///")):
                continue
            for kind, name in ANY_DECL.findall(stripped):
                kinds[name] = kind
    return kinds


def find_doubles(known_types, authored=None):
    """Return {base_name: [(rel_path, line_no, double_name)]} for file-local test doubles.
    `authored` is `_commit_scope.merge_authored`'s answer -- None to judge every file."""
    families = {}
    if not TESTS_ROOT.is_dir():
        return families
    for path, rel in source_files(TESTS_ROOT):
        parts = rel.parts
        if len(parts) > 1 and parts[1] in EXEMPT_TEST_DIRS:
            continue
        if authored is not None and rel.as_posix() not in authored:
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


def consolidatable(sites, known_types):
    """True when two doubles of one interface can share a type: both name the same project class
    base (or neither names one). A double also deriving a project class needs that class, so two
    doubles on different project classes cannot be merged into one shared double."""
    seen = set()
    for rel, line_no, _double in sites:
        lines = read_lines(REPO / rel)
        match = NON_PUBLIC.match(lines[line_no - 1]) if 0 < line_no <= len(lines) else None
        bases = split_bases(match.group(2)) if match else []
        key = tuple(sorted(b for b in bases if known_types.get(b) == "class"))
        if key in seen:
            return True
        seen.add(key)
    return False


def load_baseline():
    try:
        with open(BASELINE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def find_violations(authored=None, known_types=None):
    """Return (blocking, grandfathered, advisory) lists, each [(base, [(rel, line, name), ...])].

    blocking: interface families at INTERFACE_FAIL_AT or more, grown past baseline.
    grandfathered: interface families at threshold but within baseline.
    advisory: class/record/struct families at REPORT_AT or more.
    `authored` narrows the counted files to what this commit wrote, or None for all of them.
    """
    baseline = load_baseline()
    if known_types is None:
        known_types = project_types()
    families = find_doubles(known_types, authored)
    ranked = sorted(
        ((base, sites) for base, sites in families.items() if len(sites) >= REPORT_AT),
        key=lambda item: (-len(item[1]), item[0]),
    )
    blocking, grandfathered, advisory = [], [], []
    for base, sites in ranked:
        if (known_types[base] != "interface" or len(sites) < INTERFACE_FAIL_AT
                or not consolidatable(sites, known_types)):
            advisory.append((base, sites))
        elif len(sites) > baseline.get(base, 0):
            blocking.append((base, sites))
        else:
            grandfathered.append((base, sites))
    return blocking, grandfathered, advisory


def write_baseline():
    """Snapshot every reported family as the new grandfather baseline."""
    families = find_doubles(project_types())
    snapshot = {base: len(sites) for base, sites in families.items() if len(sites) >= REPORT_AT}
    with open(BASELINE_PATH, "w", encoding="utf-8", newline="\n") as fh:
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
        f"[duplicate-test-double-guard] {len(blocking)} interface(s) hand-rolled as "
        f"{INTERFACE_FAIL_AT}+ separate test doubles AND grown past the committed baseline:",
        file=sys.stderr,
    )
    render(blocking, sys.stderr)
    if grandfathered:
        print(f"\n  grandfathered ({len(grandfathered)} families within baseline -- curation-sweep "
              "backlog, not blocking):", file=sys.stderr)
        render(grandfathered[:3], sys.stderr)
    if advisory:
        print(f"\n  advisory ({len(advisory)} class/record/struct families at {REPORT_AT}+ doubles, "
              "not blocking):", file=sys.stderr)
        render(advisory, sys.stderr)
    print(
        "\nEach blocking interface copy is an independently-drifting stand-in: a member added later means N "
        "edits, and a behavioural assumption fixed in one double is silently absent from the rest. "
        "Promote ONE of them to Tests/Framework/Mocks as a public double, parameterised over "
        "whatever the per-test variants actually differ on (a recording list, a canned return), and "
        "delete the file-local copies. Blanket-disable with HARNESS_ALLOW_DUPLICATE_TEST_DOUBLES=1.",
        file=sys.stderr,
    )


def emit_json():
    known_types = project_types()
    blocking, grandfathered, advisory = find_violations(known_types=known_types)

    def rows(families):
        return [
            {"base": base, "kind": known_types[base], "count": len(sites),
             "sites": [{"file": rel, "line": line, "double": name} for rel, line, name in sites]}
            for base, sites in families
        ]

    print(json.dumps({
        "interface_fail_at": INTERFACE_FAIL_AT,
        "baseline": load_baseline(),
        "blocking": rows(blocking),
        "grandfathered": rows(grandfathered),
        "advisory": rows(advisory),
    }, indent=2))
    return 0


def standalone():
    if os.environ.get("HARNESS_ALLOW_DUPLICATE_TEST_DOUBLES"):
        print("[duplicate-test-double-guard] SKIPPED - HARNESS_ALLOW_DUPLICATE_TEST_DOUBLES is set.")
        return 0
    blocking, grandfathered, advisory = find_violations()
    if not blocking:
        summary = []
        if grandfathered:
            summary.append(f"{len(grandfathered)} grandfathered (within baseline)")
        if advisory:
            summary.append(f"{len(advisory)} advisory at {REPORT_AT}+")
        print(
            "[duplicate-test-double-guard] OK - no family grew past the baseline"
            + (f" ({'; '.join(summary)})" if summary else "")
            + "."
        )
        return 0
    report(blocking, grandfathered, advisory)
    return 1


def main():
    args = sys.argv[1:]
    if args and args[0] == "--json":
        return emit_json()
    if args and args[0] == "--write-baseline":
        return write_baseline()
    return standalone()


if __name__ == "__main__":
    sys.exit(main())
