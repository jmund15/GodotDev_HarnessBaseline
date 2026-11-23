r"""The backslash repair (`hooks/bash_backslash_fidelity.py`, a `_floor.HELPER_NAMES` helper, V17's backslash class):
every backslash-class case of v1's `test_bash_backslash_fidelity.py` as a row through `_floor.decide`.

Client model (measured on 2.1.270|win32): a maximal backslash run followed by `"` arrives intact; every other run of
n arrives as n - n//2. The record carries one bool per run class: `collapses` and `collapses_before_quote`. A row
asserts the channel the floor emits: `updatedInput` (with `client(updated) == original`), `additionalContext`, a
deny, or nothing. v1 cases not carried, with their reason: `test_floor_ported.md`.
"""
import json
import os
import re
import sys

import pytest

import _floor

VERSION = "9.9.9-proof"
KEY = "%s|%s" % (VERSION, sys.platform)
BS = "\\"
PAIR = BS * 2
DQ = '"'
PROBE_COMMAND = "python3 .claude/tools/bash_fidelity_probe.py 'x" + PAIR + "y' 'x" + PAIR + DQ + "y'"

PROBE_STRINGS = [
    "printf '%s|' 'a" + PAIR + "b' | od -c",
    "cat <<'EOF' | od -c\na" + PAIR + "b\nEOF",
    "printf '%s|' 'a" + PAIR + BS + "b' | od -c",
    "printf '%s' 'x" + PAIR + PAIR + "y'",
    "sed -n 's/" + PAIR + "n/X/p' f.txt; printf 'a" + BS + "nb'",
    "echo one " + PAIR + "\ntwo",
]
# Commands that mix a collapsing pair with backslash runs before `"`: the live defect shapes.
QUOTE_STRINGS = [
    "printf '%s' " + DQ + "dq" + BS + DQ + "x" + DQ + " 'a" + PAIR + "b'",
    "cat <<'EOF' | od -c\nA" + BS + DQ + "a\nB" + PAIR + DQ + "b\nC" + BS * 3 + DQ + "c\nD" + PAIR + "d\nEOF",
    "python3 - <<'EOF'\nimport re\nre.compile(r" + DQ + "[a" + PAIR + "-](?=" + BS + DQ + "|:)" + DQ + ")\nEOF",
]
QUOTE_ONLY = "printf '%s' 'a" + PAIR + DQ + "b'"
RUN = re.compile(r"\\+")


def client(text, collapses=True, before_quote=False):
    """The measured client: each maximal backslash run of n arrives as n - n//2 when its class collapses; a run
    followed by `"` is its own class."""
    def sub(match):
        quoted = text[match.end():match.end() + 1] == DQ
        if not (before_quote if quoted else collapses):
            return match.group(0)
        n = len(match.group(0))
        return BS * (n - n // 2)
    return RUN.sub(sub, text)


@pytest.fixture
def plant(tmp_path, monkeypatch):
    """(transcript path, write_record): a transcript whose newest version row is VERSION, a planted record path
    and hook state dir."""
    transcript = tmp_path / "t.jsonl"
    rows = [{"type": "user", "uuid": "u1", "version": "0.0.1-old", "message": {"content": "hi"}},
            {"type": "assistant", "uuid": "a1", "message": {"content": "x"}},
            {"type": "user", "uuid": "u2", "version": VERSION, "message": {"content": "later"}}]
    transcript.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    record = tmp_path / "record.json"
    monkeypatch.setenv("HARNESS_BASH_FIDELITY_RECORD", str(record))
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))

    def write_record(rows):
        record.write_text(json.dumps(rows) if isinstance(rows, dict) else rows, encoding="utf-8")
    return transcript, write_record


def standard(collapses=True, before_quote=False, include_quote=True):
    row = {"collapses": collapses, "observed": "2026-09-15"}
    if include_quote:
        row["collapses_before_quote"] = before_quote
    return {KEY: row}


def hso(command, transcript, tool="Bash", cwd=None):
    """The floor's hookSpecificOutput for one call ({} when it allows with no output)."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "bbf-proof",
               "transcript_path": str(transcript), "cwd": cwd or os.getcwd(),
               "tool_input": {"command": command, "description": "proof call", "timeout": 1234}}
    return (_floor.decide(payload) or {}).get("hookSpecificOutput") or {}


def command_of(out):
    return (out.get("updatedInput") or {}).get("command", "")


# --- a recorded client -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("command", PROBE_STRINGS + QUOTE_STRINGS)
def test_the_standard_record_doubles_each_collapsing_run_and_keeps_the_other_fields(plant, command):
    transcript, write_record = plant
    write_record(standard())
    out = hso(command, transcript)
    assert command_of(out) != command and client(command_of(out)) == command
    assert out["updatedInput"]["description"] == "proof call" and out["updatedInput"]["timeout"] == 1234
    assert "permissionDecision" not in out


def test_a_command_whose_only_pair_sits_before_a_double_quote_emits_nothing(plant):
    transcript, write_record = plant
    write_record(standard())
    assert hso(QUOTE_ONLY, transcript) == {}


@pytest.mark.parametrize("command", QUOTE_STRINGS + [QUOTE_ONLY])
def test_a_record_whose_runs_before_a_quote_collapse_doubles_those_too(plant, command):
    transcript, write_record = plant
    write_record(standard(True, True))
    out = command_of(hso(command, transcript))
    assert out != command and client(out, True, True) == command


def test_a_client_that_does_not_collapse_emits_nothing(plant):
    transcript, write_record = plant
    write_record(standard(False, False))
    assert hso(PROBE_STRINGS[0], transcript) == {}


# --- an unrecorded client --------------------------------------------------------------------------------------
def test_a_record_without_the_before_quote_measurement_is_unrecorded(plant):
    transcript, write_record = plant
    write_record(standard(True, include_quote=False))
    out = hso(QUOTE_STRINGS[0], transcript)
    assert "updatedInput" not in out and PROBE_COMMAND in out.get("additionalContext", "")


def test_an_unrecorded_version_is_not_repaired_and_advises_once_per_machine(plant):
    transcript, write_record = plant
    write_record({"other|plat": {"collapses": True, "collapses_before_quote": False, "observed": "2026-09-15"}})
    first, again = hso(PROBE_STRINGS[0], transcript), hso(PROBE_STRINGS[0], transcript)
    assert "updatedInput" not in first and VERSION in first["additionalContext"]
    assert PROBE_COMMAND in first["additionalContext"] and again == {}


def test_an_unrecorded_version_carries_the_newest_complete_row_on_this_platform(plant):
    transcript, write_record = plant
    write_record({"1.0.0|" + sys.platform: {"collapses": False, "collapses_before_quote": False, "observed": "2026-09-01"},
                  "1.0.1|" + sys.platform: {"collapses": True, "collapses_before_quote": False, "observed": "2026-09-20"},
                  "1.0.2|" + sys.platform: {"collapses": True, "observed": "2026-09-25"},
                  "other|plat": {"collapses": False, "collapses_before_quote": False, "observed": "2026-09-29"}})
    first, again = hso(PROBE_STRINGS[0], transcript), hso(PROBE_STRINGS[0], transcript)
    assert command_of(first) != PROBE_STRINGS[0] and client(command_of(first)) == PROBE_STRINGS[0]
    assert all(token in first["additionalContext"] for token in (VERSION, "1.0.1", "bash_fidelity_probe.py 'x" + PAIR + "y'"))
    assert client(command_of(again)) == PROBE_STRINGS[0] and "additionalContext" not in again


@pytest.mark.parametrize("case", ["unreadable record", "unreadable transcript", "a transcript with no version row"])
def test_no_readable_record_or_version_means_no_repair(plant, tmp_path, case):
    transcript, write_record = plant
    write_record("{not json" if case == "unreadable record" else standard())
    if case == "unreadable transcript":
        transcript = tmp_path / "missing.jsonl"
    elif case == "a transcript with no version row":
        transcript.write_text(json.dumps({"type": "assistant", "uuid": "a1", "message": {"content": "x"}}) + "\n",
                              encoding="utf-8")
    assert "updatedInput" not in hso(PROBE_STRINGS[0], transcript)


# --- the probe -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("command", [PROBE_COMMAND, PROBE_COMMAND.replace(".claude/tools/", "/c/some/where/.claude/tools/")],
                         ids=["relative path", "absolute path"])
def test_a_probe_invocation_is_never_doubled_and_gains_the_client_version(plant, command):
    transcript, write_record = plant
    write_record(standard())
    assert command_of(hso(command, transcript)) == command + " --client-version " + VERSION


def test_a_probe_invocation_with_no_readable_version_passes_unchanged(plant, tmp_path):
    transcript, write_record = plant
    write_record(standard())
    assert hso(PROBE_COMMAND, tmp_path / "missing.jsonl") == {}


OBSERVED = ('cd "<repo>" && ' + PROBE_COMMAND.replace("bash_fidelity_probe.py ", "bash_fidelity_probe.py --client-version 2.1.290 ")
            + " 2>&1 | tail -1; git grep bash_fidelity_probe.py")
PROBE_DENIED = {
    "the retired one-argument probe": "python3 .claude/tools/bash_fidelity_probe.py 'x" + PAIR + "y'",
    "a probe call inside a larger command": PROBE_COMMAND + " && echo done",
    "an observed noncanonical run": OBSERVED,
    "a probe piped onward": PROBE_COMMAND + " | tail -1",
    "an absolute interpreter": "/usr/bin/python3 .claude/tools/bash_fidelity_probe.py x y",
    "an interpreter flag": "python3 -u .claude/tools/bash_fidelity_probe.py x y",
}


@pytest.mark.parametrize("label", list(PROBE_DENIED))
def test_a_probe_run_in_any_other_form_is_denied_with_the_whole_command(plant, label):
    transcript, write_record = plant
    write_record(standard())
    out = hso(PROBE_DENIED[label], transcript)
    assert out.get("permissionDecision") == "deny" and PROBE_COMMAND in out["permissionDecisionReason"]
    assert "updatedInput" not in out


PROBE_MENTIONS = ["git grep -n bash_fidelity_probe.py", "cat <<'EOF'\npython3 .claude/tools/bash_fidelity_probe.py x y\nEOF",
                  "printf '%s' 'python3 .claude/tools/bash_fidelity_probe.py x y'",
                  "python3 editor.py .claude/tools/bash_fidelity_probe.py"]


@pytest.mark.parametrize("command", PROBE_MENTIONS)
def test_a_command_that_only_mentions_the_probe_passes_unchanged(plant, command):
    transcript, write_record = plant
    write_record(standard())
    assert hso(command, transcript) == {}


def test_a_command_that_mentions_the_probe_path_is_repaired_normally(plant):
    transcript, write_record = plant
    write_record(standard())
    mention = "grep -n 'x" + PAIR + "y' .claude/tools/bash_fidelity_probe.py"
    out = command_of(hso(mention, transcript))
    assert out != mention and client(out) == mention and "--client-version" not in out


# --- scope -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tool,command", [("PowerShell", PROBE_STRINGS[0]), ("Monitor", PROBE_STRINGS[0]),
                                          ("Bash", "git status --short | head -3"), ("Bash", "printf 'a" + BS + "nb'")],
                         ids=["PowerShell", "Monitor", "backslash-free", "a lone backslash"])
def test_outside_the_repairs_scope_nothing_is_emitted(plant, tool, command):
    transcript, write_record = plant
    write_record(standard())
    assert hso(command, transcript, tool=tool) == {}


def test_the_repair_runs_last_so_every_deny_wins_first(plant):
    transcript, write_record = plant
    write_record(standard())
    assert _floor.HELPER_NAMES[-1] == "bash_backslash_fidelity"
    # From the filesystem root: the checkout may sit under the system temp dir, which the delete rule exempts.
    out = hso("rm -rf build" + PAIR + "out", transcript, cwd=os.path.abspath(os.sep))
    assert out.get("permissionDecision") == "deny" and "updatedInput" not in out
