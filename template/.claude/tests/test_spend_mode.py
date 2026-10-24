#!/usr/bin/env python3
"""Proof for tools/spend_mode.py: the owner-set lean posture, its scopes, its expiry and its refusals.

    python3 .claude/tests/test_spend_mode.py

Each case runs against a planted environment: a planted transcript holding owner rows cut from a captured
session (trimmed), a planted hook-state directory and a planted task-record directory. Every case that
drives the CLI asserts on the exit code and the `REFUSED:` channel, never on a traceback.
"""
import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "spend_mode.py")
TASK_TOOL = os.path.join(HERE, "..", "tools", "task_record.py")
HOOKS = os.path.join(HERE, "..", "hooks")
sys.stdout.reconfigure(encoding="utf-8")
FAILURES = []

SID = "7b8b1489-e6b9-4ddd-8bc0-8d242f22dc8b"
OWNER_UUID = "e644b14b-34a6-45da-8d52-1efb3eb606fc"
TOKEN = "--lean"
QUOTE = "Build job J1 of the lean-spend plan " + TOKEN
FUTURE = "2031-01-01T00:00:00Z"
BEFORE = "2030-06-01T00:00:00Z"
AFTER = "2031-06-01T00:00:00Z"


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail)[:300] if detail else ""))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def owner_prompt(uuid, text):
    # Shape cut from a captured prompt row (session 7b8b1489), trimmed to the keys the classifier reads.
    return {"type": "user", "uuid": uuid, "parentUuid": None, "isSidechain": False, "promptSource": "sdk",
            "entrypoint": "sdk-cli", "userType": "external", "sessionId": SID,
            "timestamp": "2026-10-11T00:54:07.277Z", "message": {"role": "user", "content": text}}


def tool_result_row(uuid, text):
    # Shape cut from a captured tool_result row (session 7b8b1489), trimmed.
    return {"type": "user", "uuid": uuid, "isSidechain": False, "sessionId": SID,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_fixture", "content": text}]}}


class Fixture:
    def __init__(self, root):
        self.root = root
        self.proj = os.path.join(root, "proj")
        self.cfg = os.path.join(root, "cfg")
        self.state_dir = os.path.join(root, "state")
        self.records = os.path.join(root, "records")
        self.transcript = os.path.join(self.cfg, "projects", re.sub(r"[^A-Za-z0-9]", "-", self.proj),
                                       SID + ".jsonl")
        self.env = dict(os.environ, CLAUDE_PROJECT_DIR=self.proj, CLAUDE_CONFIG_DIR=self.cfg,
                        CLAUDE_CODE_SESSION_ID=SID, HARNESS_HOOK_STATE_DIR=self.state_dir,
                        HARNESS_TASK_RECORD_DIR=self.records)
        self.env.pop("CLAUDE_CODE_SIDECAR", None)

    def plant(self, rows):
        os.makedirs(os.path.dirname(self.transcript), exist_ok=True)
        with open(self.transcript, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def cli(self, *args, extra_env=None):
        env = dict(self.env, **(extra_env or {}))
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                              encoding="utf-8", cwd=self.root, timeout=60, env=env)

    def state_file(self):
        return os.path.join(self.state_dir, SID[:8] + ".json")


@contextlib.contextmanager
def fixture():
    with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=False):
        f = Fixture(root)
        os.environ.pop("CLAUDE_CODE_SIDECAR", None)
        os.environ.update({k: v for k, v in f.env.items() if k.startswith(("CLAUDE_", "HARNESS_"))})
        yield f


def refused(r):
    return r.returncode == 1 and r.stderr.startswith("REFUSED:") and "Traceback" not in r.stderr


def case_refuses_unheld_quote():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, "start the work"), tool_result_row("t1", QUOTE)])
        held_by_tool = f.cli("set", "lean", "--until", FUTURE)
        ok = refused(held_by_tool) and not os.path.exists(f.state_file())
        return ok, (held_by_tool.returncode, held_by_tool.stderr[:200])


def case_refuses_under_sidecar():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        r = f.cli("set", "lean", "--until", FUTURE, extra_env={"CLAUDE_CODE_SIDECAR": "1"})
        return refused(r) and not os.path.exists(f.state_file()), (r.returncode, r.stderr[:200])


def case_refuses_task_scope_without_until():
    with fixture() as f:
        tr = load_module("task_record", TASK_TOOL)
        tr.open_record("sp-no-until", session_id=SID, title="No until")
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        r = f.cli("set", "lean", "--task", "sp-no-until")
        stored = (tr.load("sp-no-until") or {}).get("conditions")
        return refused(r) and not stored and not os.path.exists(f.state_file()), (r.returncode, r.stderr[:200])


def case_refuses_unreadable_reset():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        r = f.cli("set", "lean", "--until-reset", "no-such-plan")
        return refused(r) and not os.path.exists(f.state_file()), (r.returncode, r.stderr[:200])


def case_expired_reads_normal():
    with fixture() as f:
        sm = load_module("spend_mode", TOOL)
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        r = f.cli("set", "lean", "--until", FUTURE)
        if r.returncode != 0:
            return False, (r.returncode, r.stderr[:200])
        live = sm.current(SID, now=BEFORE)
        lapsed = sm.current(SID, now=AFTER)
        ok = (live["mode"] == "lean" and live["expired"] is False
              and lapsed["mode"] == "normal" and lapsed["expired"] is True and lapsed["until"] == FUTURE)
        return ok, (live, lapsed)


def case_falls_back_to_task():
    with fixture() as f:
        sm = load_module("spend_mode", TOOL)
        tr = load_module("task_record", TASK_TOOL)
        tr.open_record("sp-fallback", session_id=SID, title="Fallback")
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        r = f.cli("set", "lean", "--task", "sp-fallback", "--until", FUTURE)
        if r.returncode != 0:
            return False, (r.returncode, r.stderr[:200])
        stored = (tr.load("sp-fallback") or {}).get("conditions") or [{}]
        # A new session has no session record: only the task's condition carries the posture.
        if os.path.exists(f.state_file()):
            os.remove(f.state_file())
        got = sm.current(SID, now=BEFORE)
        ok = (stored[-1].get("kind") == "spend" and stored[-1].get("effect") == "lean"
              and got["mode"] == "lean" and got["scope"] == "task" and got["until"] == FUTURE
              and got["expired"] is False)
        return ok, (got, stored[-1].get("effect"))


def case_clear_overrides_task():
    with fixture() as f:
        sm = load_module("spend_mode", TOOL)
        tr = load_module("task_record", TASK_TOOL)
        tr.open_record("sp-clear", session_id=SID, title="Clear")
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        if f.cli("set", "lean", "--task", "sp-clear", "--until", FUTURE).returncode != 0:
            return False, "set refused"
        r = f.cli("clear")
        got = sm.current(SID, now=BEFORE)
        # The task's lean condition still stands on the record; the session's clear is what this session reads.
        return r.returncode == 0 and got["mode"] == "normal" and got["expired"] is False, (r.returncode, got)


def case_session_scope_ends_with_session():
    with fixture() as f:
        sm = load_module("spend_mode", TOOL)
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        r = f.cli("set", "lean")
        if r.returncode != 0:
            return False, (r.returncode, r.stderr[:200])
        got = sm.current(SID, now=AFTER)
        ok = (got["mode"] == "lean" and got["scope"] == "session" and got["until"] is None
              and got["expired"] is False and got["quote"] == TOKEN)
        return ok, got


def case_show_prints_the_posture():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, QUOTE)])
        if f.cli("set", "lean").returncode != 0:
            return False, "set refused"
        r = f.cli("show")
        try:
            got = json.loads(r.stdout)
        except ValueError:
            return False, (r.returncode, r.stdout[:200], r.stderr[:200])
        return r.returncode == 0 and got.get("mode") == "lean", (r.returncode, got)


def case_refuses_substring_without_token():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, "keep spend lean for this plan, the cheap rows please")])
        r = f.cli("set", "lean", "--until", FUTURE)
        return refused(r) and not os.path.exists(f.state_file()), (r.returncode, r.stderr[:200])


def case_refuses_token_inside_pasted_block():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, "see this: <pasted_content id=\"x1\">run it with --lean</pasted_content>")])
        r = f.cli("set", "lean", "--until", FUTURE)
        return refused(r) and not os.path.exists(f.state_file()), (r.returncode, r.stderr[:200])


def case_refuses_longer_flag_and_wrong_mode():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, "use --leanest and --normalize")])
        a = f.cli("set", "lean")
        b = f.cli("set", "normal")
        return refused(a) and refused(b), (a.stderr[:120], b.stderr[:120])


def case_token_passes_both_directions():
    with fixture() as f:
        f.plant([owner_prompt(OWNER_UUID, "go --lean now"), owner_prompt("u2", "back to --normal")])
        a = f.cli("set", "lean")
        b = f.cli("set", "normal")
        return a.returncode == 0 and b.returncode == 0, (a.stderr[:120], b.stderr[:120])


CASES = [
    ("refuses a token only a tool result holds", case_refuses_unheld_quote),
    ("refuses under CLAUDE_CODE_SIDECAR and writes no session record", case_refuses_under_sidecar),
    ("refuses task scope without --until and stores no condition", case_refuses_task_scope_without_until),
    ("refuses --until-reset on an unreadable plan", case_refuses_unreadable_reset),
    ("current() reads normal after expiry with expired true, and lean before it", case_expired_reads_normal),
    ("falls back to the task's spend condition when the session has no record", case_falls_back_to_task),
    ("clear makes the session read normal over a task's lean condition", case_clear_overrides_task),
    ("a session-scoped set ends with the session: scope session, no until", case_session_scope_ends_with_session),
    ("refuses an owner turn that holds words but not the --lean token", case_refuses_substring_without_token),
    ("refuses --lean inside a pasted block", case_refuses_token_inside_pasted_block),
    ("refuses a longer flag and the other mode's token", case_refuses_longer_flag_and_wrong_mode),
    ("--lean and --normal each pass in an owner turn", case_token_passes_both_directions),
    ("show prints the posture JSON with mode lean", case_show_prints_the_posture),
]


def main():
    for label, fn in CASES:
        try:
            ok, detail = fn()
        except Exception as exc:  # a missing module or a traceback is a red case, never a silent pass
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        check(label, ok, detail)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for f in FAILURES:
        print("  " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
