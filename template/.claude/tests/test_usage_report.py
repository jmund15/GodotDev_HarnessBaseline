"""Proof for tools/usage_report.py over tests/fixtures/usage_report/projects.

Expected costs are hand-computed from seats.json's prices (USD per 1M tokens):
sonnet 2 fresh / 0.1 cache read / 10 output; gemini-4-neon 3 fresh / 15 output.

    python -m pytest .claude/tests/test_usage_report.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)
import transcript_usage  # noqa: E402
import usage_report  # noqa: E402

PROJECTS = os.path.join(HERE, "fixtures", "usage_report", "projects")


def tables():
    found = transcript_usage.scan([PROJECTS])
    return usage_report.report(found["calls"], usage_report.load_prices()), found


def test_prices_come_from_seats_json_and_the_registry_is_not_imported():
    assert usage_report.load_prices()["claude-sonnet-5-5"]["outputPer1M"] == 10
    with open(usage_report.__file__, encoding="utf-8") as fh:
        assert "model_registry" not in fh.read()


def test_each_session_model_row_sums_its_calls_with_a_message_counted_once():
    rep, _ = tables()
    sonnet = rep["sessions"][("session-1", "claude-sonnet-5-5")]
    assert sonnet["calls"] == 2                     # m1 (last record, 300 out) and the subagent's m3
    assert (sonnet["inp"], sonnet["cr"], sonnet["cw"], sonnet["out"]) == (2000, 2000, 500, 350)
    assert sonnet["usd"] == pytest.approx((2000 * 2 + 2000 * 0.1 + 350 * 10) / 1e6)
    assert sonnet["unpriced_calls"] == 0


def test_a_model_without_a_price_row_counts_its_tokens_and_no_cost():
    rep, _ = tables()
    opus = rep["sessions"][("session-1", "claude-opus-5-5")]
    assert (opus["calls"], opus["inp"], opus["out"]) == (1, 800, 50)
    assert opus["usd"] == 0 and opus["unpriced_calls"] == 1


def test_proxied_seat_session_is_priced_and_tagged_by_transport():
    rep, _ = tables()
    gemini = rep["sessions"][("session-2", "antigravity/gemini-4-neon")]
    assert gemini["usd"] == pytest.approx((1000 * 3 + 100 * 15) / 1e6)
    assert rep["transports"]["proxied"]["calls"] == 1
    assert rep["transports"]["anthropic"]["calls"] == 3


def test_cache_writes_are_counted_and_left_out_of_the_cost():
    rep, _ = tables()
    assert rep["models"]["claude-sonnet-5-5"]["cw"] == 500
    assert rep["models"]["claude-sonnet-5-5"]["usd"] == pytest.approx((2000 * 2 + 2000 * 0.1 + 350 * 10) / 1e6)


def test_cli_prints_the_sessions_models_transports_and_the_cache_write_note(capsys):
    assert usage_report.main(["--root", PROJECTS, "--days", "100000"]) == 0
    out = capsys.readouterr().out
    assert "session-1" in out and "claude-opus-5-5" in out
    assert "antigravity/gemini-4-neon" in out
    assert "By transport" in out and "proxied" in out
    assert "Unpriced (no seats.json row; tokens counted, cost not): claude-opus-5-5" in out
    assert "Cost excludes 500 cache-write tokens" in out


def test_cli_names_a_missing_root_as_a_gap(tmp_path, capsys):
    missing = str(tmp_path / "absent")
    usage_report.main(["--root", missing, "--days", "100000"])
    assert "missing transcript root: %s" % missing in capsys.readouterr().out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
