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

Detection: a top-level `Tests/<X>/` directory containing a `.cs` file whose stripped line is exactly
`[TestSuite]` on a NON-abstract class. Exactness matters -- Tests/Framework/JmoLoggerSpy.cs and its
suite carry the marker inside doc comments and `JmoLogger.Info("[TestSuite]", ...)` string literals.
Abstract carriers matter too -- Tests/Framework/Fixtures/*.cs mark abstract base fixtures whose
concrete subclasses live (and run) under the gated namespaces.

GATED is also cross-checked against the filter strings the gate actually issues, so renaming a
filter without updating this constant fails loudly instead of silently un-gating a whole suite.

Escape hatch: add the directory to EXCLUDED with a written rationale (that IS the sanctioned third
remedy, not a bypass). Blanket-disable with PP_ALLOW_UNGATED_TEST_DIR=1.

Modes:
    test_suite_gate_coverage_guard.py           # scan the tree; exit 1 on any un-accounted suite dir
    test_suite_gate_coverage_guard.py --hook    # PreToolUse: deny a `git commit` that introduces one

Not registered in settings.json. Intended consumer is /regression_gate step 1f, alongside
tool_cascade_audit.py and trail_mutation_seam_guard.py, which invoke the standalone mode.
"""
import json
import os
import re
import sys
from pathlib import Path

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
    # The orchestrator issues the Logic/Sanity filters; the command file documents them. Both are
    # listed because either is a legitimate home and dropping one produces a false `drift` finding.
    Path(".claude/scripts/regression_gate.ps1"),
    Path(".claude/commands/regression_gate.md"),
    Path(".claude/scripts/run_integration_batched.ps1"),
)
FILTER = re.compile(r"FullyQualifiedName~Tests\.([A-Za-z0-9_]+)")

SUITE_MARKER = "[TestSuite]"
DECL = re.compile(r"\b(?:class|record)\b")

# How far below the marker the class declaration may sit -- enough for stacked attributes
# ([RequireGodotRuntime], [Ignore], ...) without spilling into the next type.
DECL_LOOKAHEAD = 10


def declares_runnable_suite(lines):
    """True if the file marks a [TestSuite] on a class GdUnit4 can actually instantiate."""
    for index, line in enumerate(lines):
        if line.strip() != SUITE_MARKER:
            continue
        for decl in lines[index + 1:index + 1 + DECL_LOOKAHEAD]:
            if not DECL.search(decl):
                continue
            if "abstract" not in decl.split("//")[0]:
                return True
            break
    return False


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
        except OSError:
            continue
        names.update(FILTER.findall(text))
    return names


def find_violations():
    """Return [(kind, name, detail)] -- kind is 'ungated' (suite dir) or 'drift' (stale GATED)."""
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
        if name in GATED or name in EXCLUDED:
            continue
        files = suite_files(directory)
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
        "gate issues -- reconcile the two. Blanket-disable with PP_ALLOW_UNGATED_TEST_DIR=1.",
        file=sys.stderr,
    )


def standalone():
    if os.environ.get("PP_ALLOW_UNGATED_TEST_DIR"):
        print("[test-suite-gate-coverage-guard] SKIPPED - PP_ALLOW_UNGATED_TEST_DIR is set.")
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
    if os.environ.get("PP_ALLOW_UNGATED_TEST_DIR"):
        print("{}")
        return 0

    findings = find_violations()
    if not findings:
        print("{}")
        return 0

    # permissionDecisionReason is the only model-visible channel here (stderr on an exit-0
    # PreToolUse is not), so the offenders go in the reason itself rather than a stderr report.
    sites = "; ".join(f"Tests/{name} ({detail})" for _, name, detail in findings[:5])
    overflow = "" if len(findings) <= 5 else f" (+{len(findings) - 5} more)"
    reason = (
        f"Blocked: {len(findings)} test-suite gate-coverage violation(s) -- [TestSuite] classes "
        f"the gate's ~Tests.{{Logic,Integration,Sanity}} filters never run: {sites}{overflow}. "
        "Move them under a gated prefix (namespace too), extend the gate (filter line + "
        "suites.<X> baseline entry + GATED), or add the directory to "
        "test_suite_gate_coverage_guard.py's EXCLUDED map with a written rationale. "
        "Blanket-disable with PP_ALLOW_UNGATED_TEST_DIR=1."
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
    return standalone()


if __name__ == "__main__":
    sys.exit(main())
