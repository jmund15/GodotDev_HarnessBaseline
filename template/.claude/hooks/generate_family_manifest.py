#!/usr/bin/env python3
"""Generate the abstraction-family manifest -- every project base and who implements it.

"Inventory existing abstractions before proposing new types" is a planning rule with no cheap
lookup behind it: answering it means guessing a name, grepping, and hoping the guess was close.
Missing a 12-implementor strategy family and inventing a parallel one is the failure that produces.
This emits the whole inventory once, deterministically, so the lookup is a read instead of a search.

Scope: all first-party C# -- {{PROJECT_NAME}} plus the Jmodot submodule. Tests are excluded from the
family scan (a test-local subclass is a double, not a family member) but Tests/Framework IS scanned
separately for the second table: the sanctioned shared doubles, which is the other half of the same
question ("does a mock for this already exist?").

Families: `abstract class X` / `interface IX` declarations, matched against every class/record/struct
whose base list names them. Only families with at least one implementor are emitted -- a base with
none is either brand new or dead, and neither is what a planner is looking for.

Output: .claude/generated/abstraction_families.md, regenerated wholesale. Never hand-edit it; the
header says so. Regeneration is wired into /reindex_search, so it refreshes on the same cadence as
the semantic-search index.

Usage:
    generate_family_manifest.py            # write the manifest, print summary stats
    generate_family_manifest.py --stdout   # write nothing, dump the manifest to stdout
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

OUT_PATH = REPO / ".claude" / "generated" / "abstraction_families.md"
SCRIPT_REF = ".claude/hooks/generate_family_manifest.py"

# Non-first-party or non-production trees. Tests is excluded from the family scan and handled
# separately below; addons is third-party; the rest are build/tooling artifacts.
SKIP_DIRS = {"Tests", "addons", ".godot", "Temp", "obj", "bin", ".git", ".claude", ".search-index"}

# Where the sanctioned shared test doubles live.
DOUBLES_ROOT = REPO / "Tests" / "Framework"

MODIFIERS = r"(?:public|internal|private|protected|file|sealed|partial|abstract|static|new|unsafe|ref|readonly)"

ABSTRACT_CLASS = re.compile(
    rf"^\s*(?:{MODIFIERS}\s+)*abstract\s+(?:{MODIFIERS}\s+)*class\s+(\w+)\b"
)
INTERFACE = re.compile(
    rf"^\s*(?:{MODIFIERS}\s+)*interface\s+(\w+)\b"
)
TYPE_WITH_BASES = re.compile(
    rf"^\s*(?:\[[^\]]*\]\s*)*(?:{MODIFIERS}\s+)*"
    r"(?:class|record|struct)\s+(\w+)\s*(?:<[^>]*>)?\s*:\s*(.+)$"
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
        name = re.sub(r"<.*", "", part.strip()).rsplit(".", 1)[-1]
        if re.fullmatch(r"\w+", name):
            names.append(name)
    return names


def read_lines(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def production_files():
    for path in sorted(REPO.rglob("*.cs")):
        rel = path.relative_to(REPO)
        if SKIP_DIRS & set(rel.parts[:-1]):
            continue
        yield path, rel.as_posix()


def is_comment(line):
    return line.lstrip().startswith(("//", "///", "*", "/*"))


def scan():
    """Return (bases, implementors) -- {name: file} and {base_name: [(impl_name, file)]}."""
    bases, implementors = {}, {}
    for path, rel in production_files():
        for line in read_lines(path):
            if is_comment(line):
                continue
            abstract = ABSTRACT_CLASS.match(line)
            if abstract:
                bases.setdefault(abstract.group(1), rel)
            iface = INTERFACE.match(line)
            if iface:
                bases.setdefault(iface.group(1), rel)
            derived = TYPE_WITH_BASES.match(line)
            if derived:
                name = derived.group(1)
                for base in split_bases(derived.group(2)):
                    if base != name:
                        implementors.setdefault(base, []).append((name, rel))
    return bases, implementors


def scan_doubles():
    """Return [(double_name, base_name, rel_path)] for the shared doubles in Tests/Framework."""
    rows = []
    if not DOUBLES_ROOT.is_dir():
        return rows
    for path in sorted(DOUBLES_ROOT.rglob("*.cs")):
        rel = path.relative_to(REPO).as_posix()
        for line in read_lines(path):
            if is_comment(line):
                continue
            match = TYPE_WITH_BASES.match(line)
            if not match:
                continue
            name = match.group(1)
            candidates = [b for b in split_bases(match.group(2)) if b != name]
            if not candidates:
                continue
            # The interface is the contract the double stands in for; a Godot base class is just
            # the host it needed to be a Node. Prefer the former when both are present.
            interfaces = [b for b in candidates if re.match(r"^I[A-Z]", b)]
            rows.append((name, (interfaces or candidates)[0], rel))
    return rows


def render(families, doubles):
    lines = [
        f"# GENERATED — do not hand-edit; regenerate via `{SCRIPT_REF}`",
        "",
        "Every first-party abstract class and interface ({{PROJECT_NAME}} + Jmodot) that has at least",
        "one implementor, with its implementors. Read this before proposing a new type — extending a",
        "family that already exists beats inventing a parallel one. Regenerated by `/reindex_search`.",
        "",
        "## Abstraction families",
        "",
        "| Family (base) | Base file | Impl count | Impls (name → file) |",
        "|---|---|---:|---|",
    ]
    for base, base_file, impls in families:
        rendered = ", ".join(f"`{name}` → {file}" for name, file in impls)
        lines.append(f"| `{base}` | {base_file} | {len(impls)} | {rendered} |")

    lines += [
        "",
        "## Shared test doubles (`Tests/Framework/`)",
        "",
        "Reach for one of these before hand-rolling a file-local double.",
        "",
        "| Double | Interface | File |",
        "|---|---|---|",
    ]
    for name, base, file in doubles:
        lines.append(f"| `{name}` | `{base}` | {file} |")
    lines.append("")
    return "\n".join(lines)


def main():
    bases, implementors = scan()
    families = sorted(
        (
            (base, base_file, sorted(set(implementors.get(base, []))))
            for base, base_file in bases.items()
            if implementors.get(base)
        ),
        key=lambda item: (-len(item[2]), item[0]),
    )
    doubles = scan_doubles()
    content = render(families, doubles)

    if "--stdout" in sys.argv[1:]:
        print(content)
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(content, encoding="utf-8")

    total_impls = sum(len(impls) for _, _, impls in families)
    top = ", ".join(f"{base} ({len(impls)})" for base, _, impls in families[:5])
    print(
        f"[generate-family-manifest] wrote {OUT_PATH.relative_to(REPO).as_posix()} - "
        f"{len(families)} families, {total_impls} implementors, {len(doubles)} shared doubles."
    )
    print(f"[generate-family-manifest] top families: {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
