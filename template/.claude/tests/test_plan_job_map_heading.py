#!/usr/bin/env python3
"""Proof for the `## Job map` heading rule (`tools/lens_briefs.py` `has_job_map`).

A numbered section heading ("## 9. Job map") is a job map; prose that only mentions one is not.

    python3 .claude/tests/test_plan_job_map_heading.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "tools"))

import lens_briefs  # noqa: E402

PLAN = "# Plan\n\n## 8. Slices\ntext\n\n{heading}\n| slice | route |\n| S1 | `hooks/a.py` |\n\n## 10. Surfaces\n`hooks/b.py`\n"


def test_numbered_and_plain_headings_are_job_maps():
    for heading in ("## Job map", "## 9. Job map", "### 4.2 Job map", "## 11 Job map"):
        text = PLAN.format(heading=heading)
        assert lens_briefs.has_job_map(text), heading
        section = lens_briefs.JOB_MAP_SECTION_RE.search(text)
        assert section and "`hooks/a.py`" in section.group(0) and "hooks/b.py" not in section.group(0), heading


def test_a_mention_is_not_a_job_map():
    for text in ("## Slices\nSee the Job map below.\n", "Job map\n", "## The job mapping\n"):
        assert not lens_briefs.has_job_map(text), text


if __name__ == "__main__":
    test_numbered_and_plain_headings_are_job_maps()
    test_a_mention_is_not_a_job_map()
    print("ok")
