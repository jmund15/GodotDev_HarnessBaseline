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

Detection: a `.Free()` / `?.Free()` whose receiver is (a) a field or local whose declared type
descends from RefCounted, or (b) a collection field/local declared `List<GodotObject>` /
`List<Resource>` / `HashSet<...>` of the same, or the loop variable of a `foreach` bound over one.
Descent comes from a type map built from each class's `inherits` in the Godot class docs
(`.claude/cache/godot-docs/doc/classes/*.xml`) plus every `class X : Y` in the repo's `.cs` sources
each `adaptation.json` `paired_repos` entry's (generic arguments stripped). A type the map cannot trace to RefCounted -- unknown, or
rooted at Node/Object -- is never flagged, and a name declared with mixed types in one file is
flagged only when every declared type descends from RefCounted. A name with any declaration the
regexes cannot type (tuple deconstruction, `out`, `foreach`, `var x = <not new T>`, lambda
parameters) is unknown for the whole file, so it is never flagged: scoping is per file, not per
method. The map is cached per file by
mtime and size in `.claude/cache/refcounted_type_map.json`. A call already narrowed by `is Node` /
`as Node` on the same line is safe by construction and is not flagged.

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
from collections import defaultdict
from pathlib import Path

from _checkout_root import main_checkout_root

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
TYPED_DECL_RE = re.compile(r"\b([A-Z]\w*)(?:<[^<>]*>)?\??\s+(_?\w+)\s*[;=]")
VAR_NEW_RE = re.compile(r"\bvar\s+(_?\w+)\s*=\s*new\s+(?:[\w.]+\.)?([A-Z]\w*)")
CLASS_RE = re.compile(r"\bclass\s+(\w+)(?:<[^<>]*>)?\s*:\s*([\w.]+)")
DOC_CLASS_RE = re.compile(r'<class\s+name="(\w+)"(?:\s+inherits="(\w+)")?')
FOREACH_RE = re.compile(r"foreach\s*\(\s*(?:var|GodotObject|Resource)\s+(\w+)\s+in\s+(?:this\.)?(\w+)")
FREE_RE = re.compile(r"(?:^|[^\w.])(?:this\.)?(\w+)(?:\?)?\.Free\s*\(\s*\)")
TUPLE_RE = re.compile(r"\(([^()]*,[^()]*)\)\s*=(?![=>])")
TUPLE_PART_RE = re.compile(r"^\s*(?:[\w.<>?\[\]]+\s+)?(\w+)\s*$")
OUT_RE = re.compile(r"\bout\s+(?:[\w.<>?\[\]]+\s+)?(\w+)")
FOREACH_ANY_RE = re.compile(r"foreach\s*\(\s*[\w.<>?\[\]]+\s+(\w+)\s+in\s+(?:this\.)?(\w+)?")
VAR_OTHER_RE = re.compile(r"\bvar\s+(_?\w+)\s*=\s*(?!\s|new\s+(?:[\w.]+\.)?[A-Z])")
LAMBDA_RE = re.compile(r"(?:\b(\w+)|\(([^()]*)\))\s*=>")
PARAM_RE = re.compile(
    r"[(,]\s*(?:(?:ref|in|out|params|this|readonly)\s+)?([A-Za-z_][\w.]*(?:<[^<>]*>)?\??(?:\[\])?)"
    r"\s+(\w+)\s*(?=[,)=])"
)
PATTERN_RE = re.compile(r"\b(?:is|case)\s+(?:not\s+)?([A-Za-z_][\w.]*(?:<[^<>]*>)?)\s+(\w+)")
NODE_NARROWED_RE =re.compile(r"\bis\s+Node\b|\bas\s+Node\b")

_SKIP_DIRS = {"obj", "bin", "node_modules"}


def _common_root():
    return Path(main_checkout_root(REPO))


def _cs_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        for fn in files:
            if fn.endswith(".cs"):
                yield Path(dirpath) / fn


def _source_roots():
    roots = [REPO]
    common = _common_root()
    if common != REPO:
        for name in adaptation.get(_CLAUDE_DIR, "paired_repos"):
            if not next(_cs_files(REPO / name), None):
                roots.append(common / name)
    return roots


def _docs_dir():
    rel = Path(".claude") / "cache" / "godot-docs" / "doc" / "classes"
    for root in (REPO, _common_root()):
        if (root / rel).is_dir():
            return root / rel
    return None


def _parents_of(path, doc):
    try:
        if doc:
            with open(path, "rb") as fh:
                head = fh.read(512).decode("utf-8", errors="ignore")
            m = DOC_CLASS_RE.search(head)
            return [[m.group(1), m.group(2)]] if m else []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return [[m.group(1), m.group(2).split(".")[-1]] for m in CLASS_RE.finditer(text)]


def load_type_map():
    cache_path = _CLAUDE_DIR / "cache" / "refcounted_type_map.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError, AttributeError):
        cached = {}
    fresh, dirty = {}, False
    docs = _docs_dir()
    sources = [(p, True) for p in sorted(docs.glob("*.xml"))] if docs else []
    for root in _source_roots():
        sources += [(p, False) for p in sorted(_cs_files(root))]
    for path, doc in sources:
        key = path.as_posix()
        try:
            st = path.stat()
        except OSError:
            continue
        sig = [st.st_mtime_ns, st.st_size]
        hit = cached.get(key)
        if hit and hit[:2] == sig:
            fresh[key] = hit
        else:
            fresh[key] = sig + [_parents_of(path, doc)]
            dirty = True
    if dirty or len(fresh) != len(cached):
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"files": fresh}), encoding="utf-8")
        except OSError:
            pass
    parent = {}
    for entry in fresh.values():
        for cls, base in entry[2]:
            parent.setdefault(cls, base)
    return parent


def _is_refcounted(parent, name):
    for _ in range(40):
        if name == "RefCounted":
            return True
        if name is None:
            return False
        name = parent.get(name)
    return False


def _type_name(t):
    if t.endswith("[]"):
        return "[]"
    return re.sub(r"<.*", "", t).rstrip("?").split(".")[-1]


def _untyped_names(text, loose):
    names = set()
    for m in TUPLE_RE.finditer(text):
        for part in m.group(1).split(","):
            pm = TUPLE_PART_RE.match(part)
            if pm:
                names.add(pm.group(1))
    names |= {m.group(1) for m in OUT_RE.finditer(text)}
    names |= {m.group(1) for m in FOREACH_ANY_RE.finditer(text) if m.group(2) not in loose}
    names |= {m.group(1) for m in VAR_OTHER_RE.finditer(text)}
    for m in LAMBDA_RE.finditer(text):
        for part in (m.group(1) or m.group(2) or "").split(","):
            pm = TUPLE_PART_RE.match(part)
            if pm:
                names.add(pm.group(1))
    return names


def _typed_suspects(text, parent, loose):
    types = defaultdict(set)
    for m in TYPED_DECL_RE.finditer(text):
        types[m.group(2)].add(m.group(1))
    for m in VAR_NEW_RE.finditer(text):
        types[m.group(1)].add(m.group(2))
    for rx in (PARAM_RE, PATTERN_RE):
        for m in rx.finditer(text):
            types[m.group(2)].add(_type_name(m.group(1)))
    unknown = _untyped_names(text, loose)
    return {n for n, ts in types.items()
            if n not in unknown and all(_is_refcounted(parent, t) for t in ts)}


def scan():
    findings = []
    parent = load_type_map()
    for dirpath, _dirs, files in os.walk(TESTS_ROOT):
        for fn in sorted(files):
            if not fn.endswith(".cs"):
                continue
            path = Path(dirpath) / fn
            rel = path.relative_to(REPO).as_posix()
            if rel in EXEMPT:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if ".Free()" not in text:
                continue
            loose = set(FIELD_RE.findall(text)) | set(LOCAL_RE.findall(text))
            suspects = loose | {m.group(1) for m in FOREACH_RE.finditer(text) if m.group(2) in loose}
            suspects |= _typed_suspects(text, parent, loose)
            if not suspects:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if ".Free()" not in line or NODE_NARROWED_RE.search(line):
                    continue
                for m in FREE_RE.finditer(line):
                    if m.group(1) in suspects:
                        findings.append({"file": rel, "line": i, "source": line.strip()})
                        break
    return findings


def main():
    global REPO, _CLAUDE_DIR, _TESTS_ROOT_REL, TESTS_ROOT
    if "--repo" in sys.argv:
        i = sys.argv.index("--repo")
        REPO = Path(sys.argv.pop(i + 1)).resolve()
        sys.argv.pop(i)
        _CLAUDE_DIR = REPO / ".claude"
        _TESTS_ROOT_REL = adaptation.get(_CLAUDE_DIR, "tests_root")
        TESTS_ROOT = REPO / _TESTS_ROOT_REL
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
