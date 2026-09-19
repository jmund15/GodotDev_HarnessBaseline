#!/usr/bin/env python3
"""Guard against `Free()` on a tracked object whose declared type permits a RefCounted.

Godot has two ownership models and one `Free()`. Nodes are manually owned and must be freed;
everything else a test touches is RefCounted -- every Resource is one -- and is owned by its
reference count, so dropping the reference IS the teardown.

Calling Free() on a RefCounted is not merely redundant, it is destructive. Godot logs
`ERROR: Can't free a RefCounted object` and continues, but the object's gchandle has already been
released while the managed wrapper lives on; the wrapper's finalizer later trips
`FATAL: Condition "gchandle.is_released()" is true`, which kills the entire GdUnit4 host process.

The reason this needs a guard rather than a code review is that the fault is ERROR-level
individually and FATAL only in aggregate. It therefore fires as a function of how many faulting
calls land in ONE host process -- so it surfaces as an UNRELATED suite hanging, as a batched gate
disagreeing with a narrow --filter run, or as a crash that appears and vanishes when the Integration
batch manifest is rebalanced. None of those symptoms point back at the teardown that caused it, and
the runner reports the aftermath as a runtime-executor connect failure, which is the wrong cause.

Detection (deliberately narrow -- no type resolution, so only the idiom that actually caused this is
matched): a `.Free()` whose receiver is a collection field/local declared `List<GodotObject>` /
`List<Resource>` / `HashSet<...>` of the same, or the loop variable of a `foreach` bound over one.
A call already narrowed by `is Node` / `as Node` on the same line is safe by construction and is not
flagged.

Resolution: route the call through `TestObjectTeardown.FreeIfNode(x)`
(Tests/Framework/Helpers/TestObjectTeardown.cs), or -- when the tracker is Resource-only -- delete
the loop entirely, because `Clear()` beneath it is the whole teardown.

Modes:
    refcounted_free_guard.py           # scan Tests/; exit 1 on any hit
    refcounted_free_guard.py --json    # machine-readable on stdout, always exit 0

Blanket-disable with HARNESS_ALLOW_REFCOUNTED_FREE=1.

Not registered in settings.json. Intended consumer is /regression_gate's static guard block,
alongside duplicate_test_double_guard.py and test_suite_gate_coverage_guard.py.
Memory: gotcha_accumulating_fatal_makes_batch_and_filter_disagree.
"""
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_CLAUDE_DIR = REPO / ".claude"
_TOOLS_DIR = _CLAUDE_DIR / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import adaptation  # noqa: E402

# `adaptation.json` `tests_root` (Design Doc §8), defaulted to "Tests". A root that does not
# exist as a directory inside the repo is a CANNOT-RUN, never a silent "Tests" fallback and
# never a claimed OK -- `main()` checks this before scanning.
_TESTS_ROOT_REL = adaptation.get(_CLAUDE_DIR, "tests_root")
TESTS_ROOT = REPO / _TESTS_ROOT_REL


def _load_exempt() -> set:
    """The sanctioned teardown-helper paths: `adaptation.json` `teardown_helpers`, each
    validated to be an existing `.cs` file under `TESTS_ROOT`; a rejected entry is skipped
    with one stderr line and never joins EXEMPT."""
    accepted = set()
    for entry in adaptation.get(_CLAUDE_DIR, "teardown_helpers"):
        if not isinstance(entry, str) or not entry.endswith(".cs"):
            print(f"refcounted_free_guard: teardown_helpers entry {entry!r} is not a .cs "
                  "path -- skipped", file=sys.stderr)
            continue
        full = REPO / entry
        try:
            under_tests_root = full.resolve().is_relative_to(TESTS_ROOT.resolve())
        except (OSError, ValueError):
            under_tests_root = False
        if not (under_tests_root and full.is_file()):
            print(f"refcounted_free_guard: teardown_helpers entry {entry!r} is not an "
                  "existing .cs file under tests_root -- skipped", file=sys.stderr)
            continue
        accepted.add(entry)
    return accepted


EXEMPT = _load_exempt()

FIELD_RE = re.compile(
    r"\b(?:List|IList|IReadOnlyList|HashSet)<\s*(?:Godot\.)?(?:GodotObject|Resource)\s*>\s+(\w+)"
)
LOCAL_RE = re.compile(
    r"\bvar\s+(\w+)\s*=\s*new\s+(?:List|HashSet)<\s*(?:Godot\.)?(?:GodotObject|Resource)\s*>"
)
FOREACH_RE = re.compile(r"foreach\s*\(\s*(?:var|GodotObject|Resource)\s+(\w+)\s+in\s+(?:this\.)?(\w+)")
FREE_RE = re.compile(r"(?:^|[^\w.])(?:this\.)?(\w+)(?:\?)?\.Free\s*\(\s*\)")
NODE_NARROWED_RE = re.compile(r"\bis\s+Node\b|\bas\s+Node\b")


def scan():
    findings = []
    for dirpath, _dirs, files in os.walk(TESTS_ROOT):
        for fn in sorted(files):
            if not fn.endswith(".cs"):
                continue
            path = Path(dirpath) / fn
            rel = path.relative_to(REPO).as_posix()
            if rel in EXEMPT:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            loose = set(FIELD_RE.findall(text)) | set(LOCAL_RE.findall(text))
            if not loose:
                continue
            suspects = loose | {m.group(1) for m in FOREACH_RE.finditer(text) if m.group(2) in loose}
            for i, line in enumerate(text.splitlines(), 1):
                if ".Free()" not in line or NODE_NARROWED_RE.search(line):
                    continue
                for m in FREE_RE.finditer(line):
                    if m.group(1) in suspects:
                        findings.append({"file": rel, "line": i, "source": line.strip()})
                        break
    return findings


def main():
    if not TESTS_ROOT.is_dir():
        print(f"refcounted_free: CANNOT-RUN (tests_root {_TESTS_ROOT_REL!r} is not an "
              "existing directory)")
        return 2
    findings = scan()
    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
        return 0
    if os.environ.get("HARNESS_ALLOW_REFCOUNTED_FREE") == "1":
        print("refcounted_free: SKIPPED (HARNESS_ALLOW_REFCOUNTED_FREE=1)")
        return 0
    if not findings:
        print("refcounted_free: OK")
        return 0
    print(f"refcounted_free: {len(findings)} Free() call(s) on a receiver that may be RefCounted\n")
    for f in findings:
        print(f"  {f['file']}:{f['line']}: {f['source']}")
    print(
        "\nEvery Resource is a RefCounted, and Free() on one releases its gchandle while the managed"
        "\nwrapper lives on -- the finalizer then trips FATAL gchandle.is_released() and kills the"
        "\ntest host, in a LATER and unrelated suite. Route the call through"
        "\nTestObjectTeardown.FreeIfNode(x), or delete the loop outright when the tracker is"
        "\nResource-only (the Clear() beneath it is the whole teardown)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
