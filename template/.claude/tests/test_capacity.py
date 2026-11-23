"""Proof for tools/capacity.py: each usage read parsed at, below and above the floor, and unreadable; the exit
codes seat.sh relies on; one spawned case. The live reads (OAuth endpoint, agy /usage) are not run here.

    python -m pytest .claude/tests/test_capacity.py -q --durations=0
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _capacity_fixtures import agy_usage_text, anthropic_payload

CLAUDE = Path(__file__).resolve().parents[1]
CAPACITY = CLAUDE / "tools" / "capacity.py"
spec = importlib.util.spec_from_file_location("capacity_under_test", CAPACITY)
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


def test_anthropic_windows_below_at_and_above_the_floor():
    below = capacity.parse_anthropic(anthropic_payload(five=97.9, seven=98.9))
    assert below["five_hour"]["used"] == 97.9 and below["seven_day"]["used"] == 98.9
    at = capacity.parse_anthropic(anthropic_payload(five=capacity.FLOOR["five_hour"]))
    assert at["five_hour"]["used"] >= capacity.FLOOR["five_hour"]
    above = capacity.parse_anthropic(anthropic_payload(seven=100))
    assert above["seven_day"]["used"] == 100.0


def test_anthropic_absent_window_is_skipped_but_no_window_or_bad_value_is_unreadable():
    assert set(capacity.parse_anthropic(anthropic_payload(seven=10))) == {"seven_day"}
    with pytest.raises(capacity.Unreadable):
        capacity.parse_anthropic({})
    with pytest.raises(capacity.Unreadable):
        capacity.parse_anthropic(anthropic_payload(five=True))
    with pytest.raises(capacity.Unreadable):
        capacity.parse_anthropic(["not", "an", "object"])


def test_agy_rows_convert_percent_left_to_used_and_unknown_rows_are_unreadable():
    windows = capacity.parse_agy(agy_usage_text(five_left=2.0, weekly_left=60.0))
    assert windows["five_hour"]["used"] == 98.0 and windows["seven_day"]["used"] == 40.0
    with pytest.raises(capacity.Unreadable):
        capacity.parse_agy(agy_usage_text(other_bucket_only=True))
    with pytest.raises(capacity.Unreadable):
        capacity.parse_agy("Gemini Models\tMonthly\t50%\t2026-12-01T00:00:00Z\n")


def _run(monkeypatch, windows, argv=("capacity.py", "claude-secondary")):
    monkeypatch.setitem(capacity.READS, "scripts/seat_claude.sh", lambda row: windows)
    return capacity.main(list(argv))


def test_last_weekly_reset_is_the_seven_day_reset_minus_a_week_or_none(monkeypatch):
    seven_day = {"seven_day": {"used": 5.0, "resets": "2026-11-27T10:00:00+00:00"}}
    monkeypatch.setattr(capacity, "read_anthropic", lambda row: seven_day)
    assert capacity.last_weekly_reset() == 1795773600.0 - 7 * 86400
    monkeypatch.setattr(capacity, "read_anthropic", lambda row: {"five_hour": {"used": 1.0, "resets": None}})
    assert capacity.last_weekly_reset() is None

    def fail(row):
        raise capacity.Unreadable("down")
    monkeypatch.setattr(capacity, "read_anthropic", fail)
    assert capacity.last_weekly_reset() is None


def test_main_exit_codes_follow_the_floor(monkeypatch, capsys):
    ok = {"five_hour": {"used": 10.0, "resets": "t"}, "seven_day": {"used": 50.0, "resets": "t"}}
    assert _run(monkeypatch, ok) == capacity.OK
    assert "5-hour 10.0% used" in capsys.readouterr().out
    floor = {"five_hour": {"used": 98.0, "resets": "t"}, "seven_day": {"used": 50.0, "resets": "t"}}
    assert _run(monkeypatch, floor) == capacity.FLOOR_HIT
    assert "at exhaust floor (5-hour)" in capsys.readouterr().out
    weekly = {"seven_day": {"used": 99.5, "resets": None}}
    assert _run(monkeypatch, weekly) == capacity.FLOOR_HIT


def test_main_unreadable_and_unknown_seat_exit_codes(monkeypatch, capsys):
    def boom(row):
        raise capacity.Unreadable("usage endpoint: HTTP Error 401")
    monkeypatch.setitem(capacity.READS, "scripts/seat_claude.sh", boom)
    assert capacity.main(["capacity.py", "claude-secondary"]) == capacity.UNREADABLE
    assert "unreadable: usage endpoint" in capsys.readouterr().err
    assert capacity.main(["capacity.py", "no-such-seat"]) == capacity.UNKNOWN_SEAT
    assert capacity.main(["capacity.py"]) == capacity.UNKNOWN_SEAT


def test_session_and_seat_rows_come_from_the_adapter_table(monkeypatch):
    assert capacity.seat_row("session") == {"adapter": "scripts/seat_claude.sh", "configDir": "~/.claude"}
    assert capacity.seat_row("antigravity")["adapter"] == "scripts/seat_agy.sh"
    monkeypatch.setattr(capacity.launch_check, "seat_rows", lambda claude: {"x": {"configDir": "~/.x"}})
    assert capacity.seat_row("x")["configDir"] == "~/.x" and capacity.seat_row("gone") is None


def test_spawned_agy_read_at_floor_exits_3(tmp_path):
    usage = tmp_path / "usage.txt"
    usage.write_text(agy_usage_text(five_left=1.0, weekly_left=80.0), encoding="utf-8")
    if os.name == "nt":
        stub = tmp_path / "agy.bat"
        stub.write_text(f'@type "{usage}"\r\n', encoding="utf-8")
    else:
        stub = tmp_path / "agy"
        stub.write_text(f'#!/bin/sh\ncat "{usage}"\n', encoding="utf-8")
        stub.chmod(0o755)
    env = dict(os.environ, AGY_BIN=str(stub))
    done = subprocess.run([sys.executable, str(CAPACITY), "antigravity"], env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == capacity.FLOOR_HIT, done.stderr
    assert "at exhaust floor (5-hour)" in done.stdout
