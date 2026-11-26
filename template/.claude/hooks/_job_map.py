"""The `## Job map` section of a plan file, read in one place (`skills/_brainstorm_shared/plan_file_format.md`).

The plan format requires the section and states its table: one row per slice, its write set among the columns. Both
readers take their facts from it: `tools/lens_briefs.py` (a plan without the section is refused; the paths it names feed
the fix history) and `plan_memory_reminder` (the write-set cells declare the files the plan edits).

Standard library only: a hook imports it.
"""
import re

JOB_MAP_HEADING = r"^#{2,6}\s*(?:\d+(?:\.\d+)*\.?\s+)?Job map\b"
JOB_MAP_RE = re.compile(JOB_MAP_HEADING, re.IGNORECASE | re.MULTILINE)
JOB_MAP_SECTION_RE = re.compile(JOB_MAP_HEADING + r".*?(?=^#{1,6}\s|\Z)", re.IGNORECASE | re.MULTILINE | re.DOTALL)
WRITE_SET = "write set"  # the format's own name for the column


def has_job_map(text):
    """True when `text` has a `## Job map`-style heading (levels 2 to 6, case-insensitive, a section number allowed)."""
    return JOB_MAP_RE.search(text or "") is not None


def write_set_cells(text):
    """The cell text under each table header of the Job map section that names the write set, one per row. A section
    with no such column, or none at all, declares nothing."""
    section = JOB_MAP_SECTION_RE.search(text or "")
    cells, column = [], None
    for line in (section.group(0) if section else "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            column = None
            continue
        row = [cell.strip() for cell in line.strip("|").split("|")]
        if column is None:
            column = next((i for i, cell in enumerate(row) if WRITE_SET in cell.lower()), -1)
        elif 0 <= column < len(row) and set(row[column]) - set("-: "):
            cells.append(row[column])
    return cells
