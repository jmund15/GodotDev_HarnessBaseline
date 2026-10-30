#!/usr/bin/env python3
"""Guard that every runnable test suite is either gated or deliberately excluded.

/regression_gate runs exactly three filters -- `FullyQualifiedName~Tests.{Logic,Integration,Sanity}`.
A `[TestSuite]` living under any other top-level `Tests/<X>/` name compiles, passes review, and is
never executed by any gate: green build, green gate, zero coverage. That shape already shipped 37
never-run Encounter tests (archive/arch_rule_test_namespace_matches_gate_filter.md).

Two exclusions are deliberate -- Tests/Stress and Tests/ProcGenSim are measurement/simulation
batteries, not correctness suites, and both blow past the runner's wall-clock cap by construction.
Until now that exclusion existed only as prose, indistinguishable from an oversight. EXCLUDED below
is the machine-readable version: a named directory plus the rationale that earned it the pass.

Detection follows file-scoped and nested block namespaces and suite attributes on concrete
classes/records, ignoring comments and string literals. Directory placement alone cannot prove
coverage. GATED is cross-checked against executable PowerShell filter sources, not documentation.

Escape hatch: add the directory to EXCLUDED with a written rationale (that IS the sanctioned third
remedy, not a bypass). Blanket-disable with HARNESS_ALLOW_UNGATED_TEST_DIR=1.

Modes:
    test_suite_gate_coverage_guard.py           # scan the tree; exit 1 on any un-accounted suite dir

Not registered in settings.json. Intended consumer is /regression_gate step 1f, alongside
tool_cascade_audit.py and trail_mutation_seam_guard.py, which invoke the standalone mode.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _checkout_root import repo_arg  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

TESTS_ROOT = REPO / "Tests"

# Must equal the suite prefixes the gate actually filters on (verified by check_gated_drift).
GATED = ("Logic", "Integration", "Sanity")

# Directory -> why it is allowed to stay un-gated. An entry without a real rationale is a bug.
EXCLUDED = {
    "Stress": (
        "Trail wall-clock stress battery: measurement scaffolding with zero timing assertions, "
        "7,200 simulated frames and 20x 300s test timeouts -- it cannot fit run_test_suite.ps1's "
        "480s cap, so gating it would report STATUS=HANG on healthy code. Run it manually, one "
        "dimension class at a time, via -Filter \"FullyQualifiedName~Tests.Stress.<Class>\"."
    ),
    "ProcGenSim": (
        "Bulk floor-simulation analytics driven by /procgen_sim: a scenario matrix over seed "
        "ranges that emits a trend report, not a per-commit correctness signal."
    ),
}

# The two files that define what the gate actually runs.
FILTER_SOURCES = (
    Path(".claude/scripts/regression_gate.ps1"),
    Path(".claude/scripts/IntegrationBatches.ps1"),
)
FILTER = re.compile(r"FullyQualifiedName~Tests\.([A-Za-z0-9_]+)")

def runnable_namespaces(lines):
    """Read C# namespace scopes and real suite attributes, ignoring comments/literals."""
    text = "\n".join(lines)
    text = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ', text)
    tokens = re.findall(r'[A-Za-z_][A-Za-z_0-9]*|[.{};\[\],()]', text)
    scopes, namespace, file_namespace, attributes = [], "", "", False
    found = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == 'namespace':
            j = i + 1
            parts = []
            while j < len(tokens) and tokens[j] not in ('{', ';'):
                parts.append(tokens[j]); j += 1
            name = ''.join(parts)
            if j < len(tokens) and tokens[j] == ';':
                file_namespace = name; namespace = name
            else:
                scopes.append(namespace)
                namespace = '.'.join(p for p in (namespace, name) if p)
            i = j + 1; continue
        if token == '[':
            j = i + 1
            while j < len(tokens) and tokens[j] != ']':
                j += 1
            attributes = attributes or any(t in ('TestSuite', 'TestSuiteAttribute') for t in tokens[i+1:j])
            i = j + 1; continue
        if token in ('class', 'record'):
            # Modifiers since the preceding structural delimiter belong to this type.
            start = i - 1
            while start >= 0 and tokens[start] not in ('}', '{', ';', ']'):
                start -= 1
            if attributes and 'abstract' not in tokens[start+1:i]:
                class_index = i + 2 if token == 'record' and tokens[i+1:i+2] in (['class'], ['struct']) else i + 1
                class_name = tokens[class_index] if class_index < len(tokens) else ''
                found.append('.'.join(p for p in (namespace or file_namespace, class_name) if p))
            attributes = False
        if token == '{':
            scopes.append(namespace)
        elif token == '}':
            namespace = scopes.pop() if scopes else file_namespace
            attributes = False
        elif token == ';':
            attributes = False
        i += 1
    return found


def declares_runnable_suite(lines):
    return bool(runnable_namespaces(lines))


def suite_files(directory):
    """Sorted rel-posix paths under `directory` that declare a runnable [TestSuite]."""
    found = []
    for path in sorted(directory.rglob("*.cs")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if declares_runnable_suite(lines):
            found.append(path.relative_to(REPO).as_posix())
    return found


def declared_filters():
    """Suite names the gate actually issues `FullyQualifiedName~Tests.<X>` filters for."""
    names = set()
    for rel in FILTER_SOURCES:
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            text = re.sub(r'<#[\s\S]*?#>|(?m:^\s*#.*$)', '', text)
        except OSError:
            continue
        names.update(FILTER.findall(text))
    return names


def covered_by_filters(qualified_name, filters):
    for name in filters:
        if name != 'Integration' and 'Tests.' + name in qualified_name:
            return True
    if 'Integration' not in filters:
        return False
    directory = TESTS_ROOT / 'Integration'
    if not directory.is_dir():
        return False
    # Match Get-IntegrationSegments: direct directories and the first class in
    # each root file generate trailing-dot clauses (class methods add that dot).
    segments = [p.name for p in directory.iterdir() if p.is_dir()]
    for path in directory.glob('*.cs'):
        match = re.search(r'class\s+(\w+)', path.read_text(encoding='utf-8'))
        if match:
            segments.append(match[1])
    return any('Tests.Integration.' + segment + '.' in qualified_name + '.' for segment in segments)


def find_violations(authored=None):
    """Return [(kind, name, detail)] -- kind is 'ungated' (suite dir) or 'drift' (stale GATED).
    `authored` is `_commit_scope.merge_authored`'s answer -- None to judge every suite file. The
    'drift' arm compares the GATED constant against the gate's own filter text, names no suite
    path, and stays unscoped: it is a repo-wide invariant, not this commit's content."""
    findings = []
    filters = declared_filters()
    for name in GATED:
        if name not in filters:
            findings.append((
                "drift", name,
                "GATED lists it but no `FullyQualifiedName~Tests.%s` filter exists in %s"
                % (name, " or ".join(rel.as_posix() for rel in FILTER_SOURCES)),
            ))
    if not TESTS_ROOT.is_dir():
        return findings
    for directory in sorted(p for p in TESTS_ROOT.iterdir() if p.is_dir()):
        name = directory.name
        if name in EXCLUDED:
            continue
        files = []
        for path in sorted(directory.rglob("*.cs")):
            rel = path.relative_to(REPO).as_posix()
            if authored is not None and rel not in authored:
                continue
            try:
                namespaces = runnable_namespaces(path.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeError) as exc:
                findings.append(("uncovered", name, rel + " " + type(exc).__name__))
                continue
            if any(not covered_by_filters(ns, filters) for ns in namespaces):
                files.append(rel)
        if files:
            findings.append(("ungated", name, ", ".join(files)))
    return findings


def report(findings):
    print(
        f"[test-suite-gate-coverage-guard] {len(findings)} suite-coverage violation(s):",
        file=sys.stderr,
    )
    for kind, name, detail in findings:
        print(f"  {kind}  Tests/{name}  |  {detail}", file=sys.stderr)
    print(
        "\nA [TestSuite] under a top-level Tests/<X>/ that the gate does not filter on is compiled, "
        "reviewed, and never run. Resolve each one of three ways: (1) move it under Tests/Logic, "
        "Tests/Integration, or Tests/Sanity and rename its namespace to match; (2) gate it -- add "
        "the filter call to /regression_gate step 3 AND a `suites.<X>` entry to "
        "Tests/regression_baseline.json AND the name to this script's GATED; (3) if it is "
        "measurement or simulation scaffolding rather than a correctness suite, add it to EXCLUDED "
        "with a written rationale. A `drift` finding means GATED no longer matches the filters the "
        "gate issues -- reconcile the two. Blanket-disable with HARNESS_ALLOW_UNGATED_TEST_DIR=1.",
        file=sys.stderr,
    )


def standalone():
    if os.environ.get("HARNESS_ALLOW_UNGATED_TEST_DIR"):
        print("[test-suite-gate-coverage-guard] SKIPPED - HARNESS_ALLOW_UNGATED_TEST_DIR is set.")
        return 0
    findings = find_violations()
    if not findings:
        excluded = ", ".join(sorted(EXCLUDED))
        print(
            "[test-suite-gate-coverage-guard] OK - every runnable [TestSuite] is under a gated "
            f"prefix; sanctioned exclusions: {excluded}."
        )
        return 0
    report(findings)
    return 1


def main():
    global REPO, TESTS_ROOT
    repo = repo_arg(sys.argv)
    if repo:
        REPO = repo
        TESTS_ROOT = REPO / "Tests"
    return standalone()


if __name__ == "__main__":
    sys.exit(main())
