"""Routing contract: each scenario type reaches one entry command, and the Size vocabulary is one hierarchy.

The Entry routing table in execution_depth.md is read top-down and the first matching row wins, so each
scenario asserts the row it must hit and that no earlier row claims it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))

import triage  # noqa: E402

EXECUTION_DEPTH = ".claude/skills/_brainstorm_shared/execution_depth.md"
SPINE = ".claude/skills/_brainstorm_shared/drive_spine.md"
PLAN_FORMAT = ".claude/skills/_brainstorm_shared/plan_file_format.md"
FEATURE_DRIVE = ".claude/commands/feature_drive.md"
DESIGN_DRIVE = ".claude/commands/design_drive.md"
ROUTING_DOCS = (EXECUTION_DEPTH, SPINE, PLAN_FORMAT, FEATURE_DRIVE, DESIGN_DRIVE)
RETIRED_SIZES = ("multi-part", "programme")


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def section(body: str, heading: str) -> str:
    assert heading + "\n" in body, heading
    return body.split(heading + "\n", 1)[1].split("\n#", 1)[0]


def table_rows(block: str) -> list:
    rows = []
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 2 and not set(cells[0]) <= set("-: ") and cells[0] != "Fields":
            rows.append(cells)
    return rows


def entry_rows() -> list:
    return table_rows(section(text(EXECUTION_DEPTH), "### Entry routing"))


def admission_row(element: str) -> str:
    for cells in table_rows(section(text(EXECUTION_DEPTH), "### Admission table")):
        if cells[0] == element:
            return cells[1]
    raise AssertionError("no admission row " + element)


# (scenario, its matching row's condition, the entry that row names)
SCENARIOS = (
    ("an approved plan resumes", "an approved plan file exists", "/part_execute"),
    ("a Part of an existing roadmap", "the work is a roadmap Part", "/part_drive"),
    ("several Parts over an approved design doc", "size = parts with an approved design doc", "Decomposition"),
    ("several Parts, design still open", "size = parts", "/design_drive"),
    ("one plan, settled or open technical design", "size below parts", "/feature_drive"),
)


def test_size_values_are_one_hierarchy():
    assert triage.FIELDS["size"] == ("one-context", "milestones", "parts"), triage.FIELDS["size"]
    fields = section(text(EXECUTION_DEPTH), "## Fields")
    for term in ("A Part is one plan", "a roadmap orders two or", "A milestone is a checkpoint inside one plan",
                 "A slice is one test-first change"):
        assert term in fields, term
    for rel in ROUTING_DOCS:
        for retired in RETIRED_SIZES:
            assert retired not in text(rel), (rel, retired)


def test_retired_size_values_still_parse_from_old_trailers():
    for old, new in (("multi-part", "milestones"), ("programme", "parts")):
        line = ("Triage: reach=local reversibility=clean design=settled grounding=verified "
                "oracle=gate size=%s" % old)
        fields, error = triage.parse_trailer(line)
        assert error is None and fields["size"] == new, (old, fields, error)


def test_each_scenario_reaches_one_entry():
    rows = entry_rows()
    for scenario, phrase, entry in SCENARIOS:
        hits = [i for i, cells in enumerate(rows) if cells[0] == phrase]
        assert len(hits) == 1, (scenario, hits)
        assert entry in rows[hits[0]][1], (scenario, rows[hits[0]])
    body = section(text(EXECUTION_DEPTH), "### Entry routing")
    assert "first matching row wins" in body


def test_size_is_decided_up_front_and_changed_only_by_retriage():
    size_row = next(c for c in table_rows(section(text(EXECUTION_DEPTH), "## Fields")) if c[0] == "Size")
    assert "after grounding" in size_row[2] and "changes it only through §Re-triage" in size_row[2], size_row[2]
    assert "hands the dossier to `/feature_drive`" in text(DESIGN_DRIVE)


def test_red_team_runs_only_where_a_design_doc_feeds_several_plans():
    challenge = admission_row("Design challenge")
    assert "size = parts" in challenge, challenge
    assert "reach = external or reversibility" not in challenge, challenge
    assert "redteam" not in text(FEATURE_DRIVE).lower()


def test_taste_is_a_gate_not_a_route():
    body = section(text(EXECUTION_DEPTH), "### Entry routing")
    assert "inside the chosen route" in body
    for cells in entry_rows():
        assert "taste" not in cells[0], cells


def test_routing_docs_name_no_effort_value():
    effort = re.compile(r"`(low|medium|high|xhigh|max)`|·(low|medium|high|xhigh|max)\b")
    for rel in ROUTING_DOCS:
        assert not effort.search(text(rel)), (rel, effort.search(text(rel)).group(0))


# A benchmark states the fixed pin it measures (instruction_quality §12).
BENCHMARKS = (".claude/commands/rail_battery.md",)


def test_doctrine_names_no_effort_value():
    import subprocess
    effort = re.compile(r"`(low|medium|high|xhigh|max)`|·(low|medium|high|xhigh|max)\b"
                        r"|effort: ?\"(low|medium|high|xhigh|max)\"")
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", ".claude/commands", ".claude/skills",
                             ".claude/rules"], capture_output=True, text=True, check=True).stdout.split()
    docs = [rel for rel in listed if rel.endswith(".md") and rel not in BENCHMARKS]
    assert len(docs) > 50, len(docs)
    hits = [(rel, m.group(0)) for rel in docs for m in effort.finditer(text(rel))]
    assert not hits, hits[:5]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
