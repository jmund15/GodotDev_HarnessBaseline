#!/usr/bin/env python3
"""Contract proof for commands/delegate.md (v2): the seat route's call shape, the capacity read it
prints before a fan-out, and every `.claude/` path the command names resolves.

The route's behaviour is proved where it runs (the seat launcher's and the Workflow entry's tests); this
file proves only what the command text promises a session to run: `seat.sh <seat> <brief.md> <out.md>`,
`capacity.py` before a fan-out, and paths that exist. It does not match prose.

    python -m pytest .claude/tests/test_delegate_command.py -q
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
COMMAND = CLAUDE / "commands" / "delegate.md"

SEAT_CALL = re.compile(r"seat\.sh <seat> <brief\.md> <out\.md>")
CAPACITY_CALL = re.compile(r"capacity\.py <seat>")
PATH_TOKEN = re.compile(r"`((?:\.claude/)?(?:tools|scripts|hooks|workflows|guards|skills|rules|reference)/[\w./-]+\.(?:py|sh|js|json|md|ps1))`")
# A direct SDK call that pins a model on the agent tool is the shortcut the route exists to avoid.
BARE_AGENT_PIN = re.compile(r"Agent\s*\(\s*\{")


@pytest.fixture(scope="module")
def text():
    return COMMAND.read_text(encoding="utf-8")


def test_command_exists():
    assert COMMAND.is_file(), COMMAND


def test_names_the_seat_call_shape(text):
    assert SEAT_CALL.search(text), "delegate.md no longer names seat.sh <seat> <brief.md> <out.md>"


def test_reads_capacity_before_a_fan_out(text):
    assert CAPACITY_CALL.search(text), "delegate.md no longer prints capacity.py <seat> before a fan-out"


def test_no_bare_agent_pin(text):
    assert not BARE_AGENT_PIN.search(text)


@pytest.mark.parametrize("token", sorted(set(PATH_TOKEN.findall(COMMAND.read_text(encoding="utf-8")))))
def test_named_path_resolves(token):
    path = token if token.startswith(".claude/") else ".claude/" + token
    assert (ROOT / path).exists() or (CLAUDE / token).exists(), f"delegate.md names {token}, which is absent"
