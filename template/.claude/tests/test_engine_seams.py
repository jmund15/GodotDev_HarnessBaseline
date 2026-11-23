#!/usr/bin/env python3
"""Proof that the Workflow entry, the engines and the claim check agree on one contract (S6b, seams 1-3).

Each case builds its launch payload with `hooks/workflow_transport.inject` against this tree's own data files
and hands it to an engine untouched (`_engine_run.js` with `launched: true`), so nothing a test helper plants
can stand in for a key the entry fails to inject.

    python -m pytest .claude/tests/test_engine_seams.py -q
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "hooks"))
sys.path.insert(0, str(CLAUDE / "tools"))
import quote_check  # noqa: E402
import workflow_transport  # noqa: E402

RUN = CLAUDE / "tests" / "_engine_run.js"
SINGLE_KIND = {"doc_architecture_audit": {"context": "ctx", "docRoot": "docs"},
               "idea_fanout": {"topic": "t"},
               "worklog_relevance": {"scope": "s"},
               "test_skill_pressure": {"skillName": "n", "skillBody": "b"}}
PINS = {"model": "sonnet", "effort": "low", "tier": "fanout"}


def launch(engine, args, reply):
    """The engine's return for a call the entry has injected, as parsed JSON."""
    payload = workflow_transport.inject(dict(args, **PINS), cwd=None)
    done = subprocess.run(["node", str(RUN)], capture_output=True, text=True, timeout=60, input=json.dumps(
        {"script": str(CLAUDE / "workflows" / (engine + ".js")), "args": payload, "reply": reply, "launched": True}))
    assert done.returncode == 0, done.stderr[-600:]
    return json.loads(done.stdout)


@pytest.mark.parametrize("engine", sorted(SINGLE_KIND))
def test_a_single_kind_engine_accepts_the_entrys_own_payload(engine):
    """Seam 1: the entry resolves `args.kind` into `__kindProfile`, the key these engines refuse without."""
    out = launch(engine, dict(SINGLE_KIND[engine], kind="lens"), {})
    assert "args.kind is required" not in str(out.get("error")), out


def test_every_kind_a_single_kind_engine_names_resolves_from_the_profile_table():
    """Seam 1: a kind whose row does not serve the engine route gets no profile, so the engine refuses it by name."""
    payload = workflow_transport.inject({"kind": "no-such-kind"}, cwd=None)
    assert "__kindProfile" not in payload


def schema_kinds(schema):
    return sorted(next(iter(set(item["required"]) - {"quote", "output"})) for item in schema["items"]["oneOf"])


def test_the_schema_the_engines_and_quote_check_name_the_same_two_evidence_kinds():
    """Seam 2: quote_check checks `{file, quote}` and `{command, output}` and nothing else."""
    schema = json.loads((CLAUDE / "schemas" / "evidence.json").read_text(encoding="utf-8"))
    assert schema_kinds(schema) == ["command", "file"]
    for engine in ("explore_fanout", "doc_architecture_audit", "worklog_relevance"):
        text = (CLAUDE / "workflows" / (engine + ".js")).read_text(encoding="utf-8")
        rules = re.search(r"^const EVIDENCE_RULES = (.*)$", text, re.M).group(1)
        assert "two kinds" in rules and "four kinds" not in rules and "verify_claims" not in rules, engine
        assert '"owner"' not in text.split("// EVIDENCE-SSOT-END")[0], engine
        assert '"url"' not in text.split("// EVIDENCE-SSOT-END")[0], engine
    assert "verify_claims" not in json.dumps(schema)


def test_an_engines_returned_claims_are_checked_by_quote_check_end_to_end(tmp_path):
    """Seam 3: the worklog engine's own return goes to `quote_check.py`; the planted bad quote is the one miss."""
    (tmp_path / "Worklog.md").write_text("## Combat\n- Parry window rework\n", encoding="utf-8")
    reply = {"overlaps": [{"title": "Parry window rework", "domain": "Combat", "relation": "same-scope", "why": "w",
                           "recommendation": "read-first",
                           "evidence": [{"file": "Worklog.md", "quote": "Parry window rework"},
                                        {"file": "Worklog.md", "quote": "Dash cooldown rework"}]}],
             "checked": {"indexRead": True, "obsidianOpened": True, "shortlisted": ["Parry window rework"],
                         "stoppedAt": "verified-overlaps", "basis": "b",
                         "evidence": [{"file": "Worklog.md", "quote": "## Combat"}]}}
    result = launch("worklog_relevance", {"scope": "parry", "kind": "lens"}, reply)
    out = tmp_path / "result.json"
    out.write_text(json.dumps(result), encoding="utf-8")
    done = subprocess.run([sys.executable, str(CLAUDE / "tools" / "quote_check.py"), str(out), "--root", str(tmp_path)],
                          capture_output=True, text=True)
    assert done.returncode == 1, done.stdout + done.stderr
    misses = [line for line in done.stdout.splitlines() if line.startswith("MISS")]
    assert len(misses) == 1 and "Dash cooldown" not in misses[0] and "quote not in file" in misses[0], done.stdout
    assert "3 items, 1 misses" in done.stdout
