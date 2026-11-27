#!/usr/bin/env python3
"""Proof that CLAUDE.core.md stands alone: the shared core never depends on a project's CLAUDE.md.

The core is published to every project; CLAUDE.md is project-owned and imports it. Each section
lives whole in one of the two files, so:

- no heading appears in both files;
- every `§Name` the core cites without a file path names a core heading;
- the core never names the project file's own sections ("Project Guidelines");
- every file, skill and command a layer file cites is held by its own layer or one below it;
- every backticked `/name` names a command or skill in this tree.

The layer overlays `CLAUDE.coding.md` and `CLAUDE.godot.md` follow the same rules, and each may
also cite sections of the layers below it; CLAUDE.md imports each present layer file, core first.
The last rule is `tools/layer_closure.py`'s judgment, which decides on where the cited thing lives
(a file path under a `.claude/` folder, a skill, a command or a memory that exists). A backticked
word naming nothing in any layer is a tool, a parameter or a status, never a cite, so no list of
such words is kept here; a bare skill name that names nothing anywhere is also not caught here.
A `/name` token is unambiguous, so one naming nothing is a stale cite (a renamed or deleted command).

Planted cases prove each check fails on a violation.

    python3 .claude/tests/test_claude_core_standalone.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import layer_closure as lc  # noqa: E402

PROJECT_SECTIONS = ("Project Guidelines",)
LAYER_FILES = ("core", "coding", "godot")  # baseline layer order; CLAUDE.md imports each present one
FILE_LAYER = {"core": "pure", "coding": "coding", "godot": "godot"}  # layer file name -> its manifest layer
SLASH_CITE = re.compile(r"`/([a-z][a-z0-9_]*)`")
ABSOLUTE_PATHS = {"tmp"}  # `/tmp` is a POSIX path the core names, never a command
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + detail if detail else ""))


def headings(text):
    names = set()
    for line in text.splitlines():
        if line.startswith("#"):
            names.add(re.sub(r"^\d+(?:[–-]\d+)?\.\s+", "", line.lstrip("#").strip()))
    return names


def section_cites(text):
    """`§Name` citations not anchored to a backticked file path."""
    for match in re.finditer("§ ?([A-Z][^:;,.|)`]*)", text):
        before = text[:match.start()].rstrip()
        if before.endswith("`"):
            continue
        yield match.group(1).strip()


def resolves(cite, names):
    """A heading resolves by prefix in either direction, whole or after a `<prefix>: ` label."""
    forms = set(names) | {h.split(": ", 1)[1] for h in names if ": " in h}
    return any(cite == h or h.startswith(cite) or cite.startswith(h) for h in forms)


def layered_problems(layers, overlay, root, file_layers):
    """`layers` is [(name, text)] in layer order (core first); `file_layers` maps each shipped relpath
    under `root` (the layer files and the skills, commands and docs they cite) to its layer. Each file
    cites only sections it or a lower layer holds, and no heading repeats across any two files."""
    found = []
    seen = {}
    for name, text in layers + [("CLAUDE.md", overlay)]:
        for heading in sorted(headings(text)):
            if heading in seen:
                found.append(f"heading in both files: {heading} ({seen[heading]}, {name})")
            seen.setdefault(heading, name)
    lower = set()
    for name, text in layers:
        lower |= headings(text)
        for cite in section_cites(text):
            if not resolves(cite, lower):
                found.append(f"{name} cites a section it does not hold: §{cite}")
        for phrase in PROJECT_SECTIONS:
            if phrase in text:
                found.append(f"{name} names a project section: {phrase}")
        for command in sorted(set(SLASH_CITE.findall(text)) - ABSOLUTE_PATHS):
            if not any(Path(root, rel).is_file()
                       for rel in (f".claude/commands/{command}.md", f".claude/skills/{command}/SKILL.md")):
                found.append(f"{name} cites a slash command nothing holds: /{command}")
    sources = {f".claude/CLAUDE.{name}.md" for name, _ in layers}
    for finding in lc.scan(Path(root), file_layers, sources):
        found.append(f"{finding.source} cites a file its layer does not ship: {finding.kind} {finding.target}")
    return found


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as handle:
        return handle.read()


def planted_layers(layers, files=(), overlay="", local=()):
    """`files` is relpaths shipped in the core's layer, or {relpath: layer}; `local` is relpaths on disk that ship in no layer."""
    shipped = dict.fromkeys(files, "pure") if not isinstance(files, dict) else dict(files)
    with tempfile.TemporaryDirectory() as root:
        for name, text in layers:
            shipped[f".claude/CLAUDE.{name}.md"] = FILE_LAYER[name]
            shipped_path = Path(root, f".claude/CLAUDE.{name}.md")
            shipped_path.parent.mkdir(parents=True, exist_ok=True)
            shipped_path.write_text(text, encoding="utf-8")
        for rel in [*shipped, *local]:
            path = Path(root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("x\n", encoding="utf-8")
        return layered_problems(layers, overlay, root, shipped)


def planted(core, overlay, files=()):
    return planted_layers([("core", core)], files, overlay)


def live_layers():
    """relpath -> layer for what this tree's consumers receive: a project's lock over its pinned baseline manifest,
    else the baseline manifest above the template. None when the pinned manifest is unreadable."""
    lock = Path(REPO, ".claude", "baseline.lock.json")
    if lock.exists():
        data = json.loads(lock.read_text(encoding="utf-8"))
        manifest = lc.pinned_manifest(Path(REPO), data, fetch=False)
        return None if manifest is None else lc.consumer_layers(data, manifest)
    manifest = Path(REPO, "..", "baseline.manifest.json")
    return lc.manifest_layers(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.exists() else {}


def main():
    clean_core = "## Alpha Rules\nSee §Alpha Rules and `testing`.\n"
    clean_files = (".claude/skills/testing/SKILL.md",)
    check("a clean planted pair passes", planted(clean_core, "## Beta\n", clean_files) == [])
    check("a heading in both files fails",
          any("heading in both" in p for p in planted(clean_core, "## Alpha Rules\n", clean_files)))
    check("a core cite of an overlay-only section fails",
          any("does not hold" in p for p in planted(clean_core + "Per §Beta Notes.\n", "## Beta Notes\n", clean_files)))
    check("a cite anchored to a backticked file is not judged",
          planted(clean_core + "See `rules/x.md` §Other.\n", "", clean_files + (".claude/rules/x.md",)) == [])
    check("a core naming Project Guidelines fails",
          any("project section" in p for p in planted(clean_core + "Project Guidelines names it.\n", "", clean_files)))
    check("a cite of a skill only a higher layer holds fails",
          any("needs-coding" in p and "change_control" in p for p in planted(
              clean_core + "`change_control` owns it.\n", "",
              {**dict.fromkeys(clean_files, "pure"), ".claude/skills/change_control/SKILL.md": "coding"})))
    check("a cite of a skill only the project holds fails",
          any("local-only" in p and "change_control" in p for p in planted_layers(
              [("core", clean_core + "`change_control` owns it.\n")], clean_files,
              local=(".claude/skills/change_control/SKILL.md",))))
    check("a missing cited path fails",
          any("missing" in p for p in planted(clean_core + "`reference/project_stack.md`\n", "", clean_files)))
    check("a backticked agent status word is not a skill cite",
          planted(clean_core + "Only while `ListAgents` shows it `busy`.\n", "", clean_files) == [])
    check("a backticked git subcommand list is not a cite, whatever its words",
          planted(clean_core + "Run a git index or ref write (`add`, `rm`, `reset`, `stash`, `fetch`, `update-ref`) alone.\n",
                  "", clean_files) == [])
    check("a slash command nothing holds fails",
          any("/nowhere" in p for p in planted(clean_core + "Run `/nowhere`.\n", "", clean_files)))
    check("a slash command the tree holds as a skill passes",
          planted(clean_core + "Run `/testing`.\n", "", clean_files) == [])
    check("an absolute temp path is not a slash command",
          planted(clean_core + "Not `/tmp`.\n", "", clean_files) == [])
    check("a slash command only a higher layer holds fails",
          any("needs-coding" in p and "/nowhere" in p for p in planted(
              clean_core + "Run `/nowhere`.\n", "", {**dict.fromkeys(clean_files, "pure"), ".claude/commands/nowhere.md": "coding"})))
    check("a cite of the gitignored scratch dir is not judged",
          planted(clean_core + "Messages go under `.claude/scratch/`.\n", "", clean_files) == [])

    check("an overlay may cite a core section",
          planted_layers([("core", clean_core), ("coding", "## Gamma\nPer §Alpha Rules.\n")], clean_files) == [])
    check("the core citing an overlay section fails",
          any("core cites a section" in p for p in planted_layers(
              [("core", clean_core + "Per §Gamma.\n"), ("coding", "## Gamma\n")], clean_files)))
    check("a heading in two overlays fails",
          any("heading in both" in p for p in planted_layers(
              [("core", clean_core), ("coding", "## Gamma\n"), ("godot", "## Gamma\n")], clean_files)))

    layers = [(name, read(f".claude/CLAUDE.{name}.md")) for name in LAYER_FILES
              if os.path.exists(os.path.join(REPO, ".claude", f"CLAUDE.{name}.md"))]
    held = live_layers()
    sources = {f".claude/CLAUDE.{name}.md" for name, _ in layers}
    check("the layer map holds every layer file, so the cite judgment is not vacuous",
          held is not None and sources <= set(held),
          "the pinned baseline manifest is unreadable; run `baseline_sync.py check`" if held is None else "")
    live = layered_problems(layers, read(".claude/CLAUDE.md"), REPO, held or {})
    check("the live core and layer overlays stand alone", live == [], "; ".join(live))
    imports = [line.strip() for line in read(".claude/CLAUDE.md").splitlines() if line.startswith("@")]
    if os.path.exists(os.path.join(REPO, ".claude", "baseline.lock.json")):
        check("CLAUDE.md imports every present layer file, core first",
              imports[:len(layers)] == [f"@CLAUDE.{name}.md" for name, _ in layers], str(imports))
    else:
        check("the template seed imports the core; bootstrap writes each adopted layer's import",
              imports == ["@CLAUDE.core.md"], str(imports))

    print()
    print("all ok" if not FAILURES else "%d failure(s)" % len(FAILURES))
    for failure in FAILURES:
        print("  " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
