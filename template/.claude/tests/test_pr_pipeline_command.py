"""Proof that the shared `/pr_pipeline` and the `pr_pipeline` reader agree, and that the loop adds no second per-PR path.

Each hook point `tools/adaptation.py` defines has a row in the command's table and a reader in `/merge_pr` where the
command says so; the command reviews through `/review_pr`, merges through `/merge_pr`, and never merges or force-pushes
itself.
"""
import re
import sys
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "tools"))
import adaptation  # noqa: E402

COMMANDS = CLAUDE / "commands"


def body_of(name):
    text = (COMMANDS / name).read_text(encoding="utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    return head, body


def test_the_table_names_exactly_the_hook_points_the_reader_defines():
    _, body = body_of("pr_pipeline.md")
    rows = re.findall(r"^\| `(\w+)` \|", body, re.M)
    assert sorted(rows) == sorted(adaptation.PIPELINE_POINTS)


def test_the_command_reads_a_point_through_the_dotted_reader():
    _, body = body_of("pr_pipeline.md")
    assert "adaptation.py get pr_pipeline.<point>" in body


def test_the_command_reviews_through_review_pr_and_merges_through_merge_pr():
    head, body = body_of("pr_pipeline.md")
    assert "disable-model-invocation: true" in head
    assert "/review_pr <N>" in body and "/merge_pr <N>" in body
    assert "gh pr merge" not in body
    assert not re.search(r"git push\s+(--force\b|-f\b)", body)


def test_merge_pr_runs_the_sync_entries_in_place_of_its_default_sync():
    _, body = body_of("merge_pr.md")
    assert "adaptation.py get pr_pipeline.sync" in body


def test_the_command_grants_the_fan_out_tool_its_per_pr_review_grants():
    """`/review_pr` fans out through `Workflow`. The nested-fan-out guard derives a fan-out command from its
    `allowed-tools` grant, so the batch command that runs it grants `Workflow` too, and no delegate is told to run it."""
    tools = {name: set(re.search(r"^allowed-tools:\s*(.*)$", body_of(name)[0], re.M).group(1).replace(" ", "").split(","))
             for name in ("review_pr.md", "pr_pipeline.md")}
    assert "Workflow" in tools["review_pr.md"]
    assert "Workflow" in tools["pr_pipeline.md"]
