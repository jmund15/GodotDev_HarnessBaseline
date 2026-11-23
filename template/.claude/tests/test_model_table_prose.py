"""The orchestration model table states tendencies an orchestrator can act on, never a narrated run.

Owner ruling (2026-11-23, U120): a model line says what the model is good or weak at and when to move off it; a date,
a duration, a score, a task id, a ratio, a price or a source-line citation pins the line to one run and turns the
table into a changelog. HarnessBench holds the runs. This checks the half a machine can see; whether a clause reads
as a tendency is the author's call.
"""
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "orchestration" / "SKILL.md"

BANNED = [
    ("date", r"\b20\d\d-\d\d-\d\d\b"),
    ("duration", r"\b\d+(?:\.\d+)?\s*(?:s|sec|seconds|min|minutes|h|hours)\b"),
    ("score", r"\b\d+\s*/\s*\d+\b"),
    ("task id", r"\bT\d+[A-Za-z]?\b"),
    ("ratio", r"~?\d+(?:\.\d+)?\s*[x×](?![\w-])"),
    ("points", r"\b\d+\s*points?\b"),
    ("price", r"\$\s?\d|per million tokens"),
    ("line citation", r"\b[\w./-]+\.\w+:\d+"),
    ("run narration", r"\b(?:battery|batteries|in the \w+ cell|scored|on one run)\b"),
]


def table_rows(text):
    body = text.split("<!-- model-table-begin -->", 1)[1].split("<!-- model-table-end -->", 1)[0]
    return [line for line in body.splitlines() if line.startswith("| ") and not line.startswith("| Model")]


def findings(rows):
    return [(row.split("|")[1].strip(), name) for row in rows for name, pattern in BANNED
            if re.search(pattern, row, re.IGNORECASE)]


def test_the_shipped_model_table_states_tendencies_only():
    rows = table_rows(SKILL.read_text(encoding="utf-8"))
    assert rows, "the model table markers moved; this check reads between them"
    assert findings(rows) == []


def test_each_kind_of_run_narration_is_caught():
    narrated = [
        "| x | scored 19/20 on the T9 cell | `low` | slow |",
        "| x | took 45 min on 2026-11-23 | `low` | |",
        "| x | 2x tokens; see seat_local.sh:120 | `low` | |",
    ]
    assert {name for _, name in findings(narrated)} >= {"score", "task id", "duration", "date", "ratio", "line citation"}
