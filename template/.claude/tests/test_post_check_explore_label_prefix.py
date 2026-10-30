"""post_check claims mode keys an explore_fanout journal as the engine does (`<lens>#<index>`) whatever `labelPrefix` the caller chose.

The fixture is cut from a live /research run's journal (labelPrefix "research"), trimmed to one claim."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / '.claude/tools'))
import post_check  # noqa: E402

STARTED = {"type": "started", "key": "v2:b0b3b632c22a2766867cd45ea15746f874cf0bd1dffbab07afce3e1cf9460ed8",
           "agentId": "a07af3d28e249941e", "label": "research:res-official", "phase": "Explore"}
CLAIM = {"subject": "Claude in Chrome: context cost", "polarity": "partial",
         "claim": "The page says enabling Chrome by default increases context usage. [P1] page unversioned upstream, fetched 2026-10-23.",
         "bearing": "constraint", "confidence": "verified", "file": "https://code.claude.com/docs/en/chrome.md",
         "evidence": [{"url": "https://code.claude.com/docs/en/chrome.md",
                       "artifact": ".claude/scratch/research_web_verify/code.claude.com_docs_en_chrome.md",
                       "quote": "Enabling Chrome by default increases context usage since the browser tools and their instructions are always loaded."}]}
RESULT = {"type": "result", "key": STARTED["key"], "agentId": STARTED["agentId"],
          "result": {"claims": [CLAIM, dict(CLAIM, subject="second")],
                     "checked": {"toolsUsed": ["Read:context.md"], "stoppedAt": "exhausted-leads", "basis": "fixture"}, "gaps": []}}


def rows_for(label, phase):
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / 'session-id/subagents/workflows/wf_fixture'
        run.mkdir(parents=True)
        started = dict(STARTED, label=label, phase=phase)
        (run / 'journal.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in ({"type": "launched"}, started, RESULT)),
                                           encoding='utf-8')
        args = argparse.Namespace(run_label=None, workflow_dir=str(run), sidecar_dir=None, result=None, root=str(REPO), spill_dir=None)
        rows, errors, _ = post_check.claims_rows(args)
        return [row['id'] for row in rows], errors


def test_custom_label_prefix_keys_as_engine():
    ids, errors = rows_for('research:res-official', 'Explore')
    assert ids == ['res-official#0', 'res-official#1'], (ids, errors)


def test_default_explore_prefix_still_keys_as_engine():
    ids, _ = rows_for('explore:res-official', 'Explore')
    assert ids == ['res-official#0', 'res-official#1'], ids


def test_non_explore_journal_keeps_label_keys():
    ids, _ = rows_for('audit:seat', 'Review')
    assert all(i.startswith('audit:seat#') for i in ids), ids


if __name__ == '__main__':
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('PASS', name)
            except AssertionError as exc:
                failed += 1
                print('FAIL', name, exc)
    sys.exit(1 if failed else 0)
