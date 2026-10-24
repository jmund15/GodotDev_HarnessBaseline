#!/usr/bin/env python3
"""The owner's spend posture: `lean` makes every dispatch pin each tier's cheapest admitted row.

Only the owner sets it, on the owner's literal token (a turn of this session holds `--lean` or `--normal`, word-bounded, outside a
<pasted_content> block), and it is scoped:
`session` (the default) ends with the session; `task` (`--task T`) requires an `--until` and records a `spend`
condition on the task, so a later session that resumes the task reads the same posture. `current()` reads the
posture a session has now: its session record first, else the latest `spend` condition on the session's newest
task, accepted only when `task_record` resolves it as fresh. Past its expiry a lean posture reads `normal`, with
`expired` set, so the posture line can say so.

Lean changes pins, never fields, admission or review width. This module states the posture; the rows a pin may
take are `provider_bands.py lean <tier>`'s facts, and the agent chooses (`orchestration`
reference `spend_lean.md`).

    spend_mode.py set lean|normal [--task T] [--until-reset PLAN | --until ISO]
    spend_mode.py show
    spend_mode.py clear

FAIL POSTURE: a posture that cannot be read is `normal`, never lean; an expiry that cannot be read is expired.
`set` refuses rather than write a posture it cannot verify. `clear` writes `normal`, so it also overrides a task's
lean condition for this session.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "hooks"), HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import _hook_state  # noqa: E402
import _owner_text  # noqa: E402
import provider_bands  # noqa: E402
import provider_capacity  # noqa: E402
import task_record  # noqa: E402

KEY = "spend"
MODES = ("lean", "normal")


class SpendRefused(Exception):
    pass


def _epoch(now):
    """`now` as an epoch: None is the clock, a number is an epoch, a string is an ISO time with a zone."""
    if now is None:
        return time.time()
    if isinstance(now, (int, float)):
        return float(now)
    stamp = task_record.until_epoch(now)
    if stamp is None:
        raise ValueError("now must be an ISO time with a zone, not %r" % (now,))
    return stamp


def _session_id():
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or ""


def _session_posture(session_id):
    rec = _hook_state.read_json_salvage(_hook_state.state_path(session_id)).get(KEY)
    return rec if isinstance(rec, dict) else None


def _task_posture(session_id, root=None):
    """The latest `spend` condition on the session's newest task, when `task_record` resolves it fresh."""
    rec = task_record.active(session_id, directory=root)
    if not rec:
        return None
    spend = [c for c in rec.get("conditions") or [] if isinstance(c, dict) and c.get("kind") == KEY]
    if not spend or task_record._condition_row(spend[-1])["status"] != "fresh":
        return None
    cond = spend[-1]
    return {"mode": cond.get("effect"), "scope": "task", "until": cond.get("until"),
            "quote": cond.get("quote"), "task": rec.get("task_id")}


def current(session_id, root=None, now=None):
    """The spend posture a session reads now: {"mode", "scope", "until", "quote", "expired"}.

    `root` names the task-record directory (default: the project's). A lean posture past its `until` reads
    `normal` with `expired` true; its scope and until stay, so the posture line can name the lapse."""
    at = _epoch(now)
    posture = _session_posture(session_id) or _task_posture(session_id, root) or {}
    mode = posture.get("mode") if posture.get("mode") in MODES else "normal"
    until = posture.get("until")
    expired = False
    if mode == "lean" and until is not None:
        stamp = task_record.until_epoch(until)
        expired = stamp is None or at >= stamp
        if expired:
            mode = "normal"
    return {"mode": mode, "scope": posture.get("scope"), "until": until,
            "quote": posture.get("quote"), "expired": expired}


def _verify_token(session_id, mode, transcript=None):
    """The owner turn of this session that holds the literal token `--<mode>` (word-bounded, outside a pasted
    block): (its uuid, the token). Refuses when no owner turn holds it, or a transcript that cannot be read."""
    quote = "--" + mode
    if not session_id:
        raise SpendRefused("no session id: set CLAUDE_CODE_SESSION_ID")
    transcript = transcript or task_record._session_transcript(session_id)
    turns = task_record._owner_turns(transcript)
    if turns is None:
        raise SpendRefused("cannot read the session transcript %s" % transcript)
    holders = [uuid for uuid, text in turns if _owner_text.holds_token(text, mode)]
    if not holders:
        raise SpendRefused("no owner turn in this session holds the token %s outside a pasted block" % quote)
    return holders[-1], quote


def _reset_until(plan):
    """The routing-window reset of a plan-quota transport, read through `provider_bands`, as an ISO time with a
    zone. Refuses a plan that is not plan-quota, or whose future reset cannot be read: an unknown reset is never
    assumed."""
    if plan not in provider_bands.plan_quota_transports():
        raise SpendRefused("--until-reset %s: not a plan-quota transport" % plan)
    try:
        got = provider_bands.read_capacity(plan)
    except Exception as exc:
        raise SpendRefused("--until-reset %s: cannot read its capacity (%s)" % (plan, str(exc)[:120]))
    quota = got.get("quota") if isinstance(got, dict) and isinstance(got.get("quota"), dict) else {}
    name = quota.get("routingWindow")
    window = next((w for w in quota.get("windows") or [] if isinstance(w, dict) and w.get("name") == name), None)
    reset = provider_capacity._epoch(window.get("resetsAt")) if window else None
    if reset is None or reset <= time.time():
        raise SpendRefused("--until-reset %s: no future routing-window reset can be read" % plan)
    return datetime.fromtimestamp(reset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_session(session_id, posture):
    path = _hook_state.state_path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def updater(state):
        state[KEY] = posture

    written, _ = _hook_state.update_json_locked(path, updater, raising=True)
    if not written:
        raise OSError("could not write the spend posture to %s" % path)


def set_spend(mode, scope_task=None, until=None, until_reset=None, session_id=None, transcript=None):
    """Set this session's spend posture to `mode` on the owner's literal `--<mode>` token; returns the stored posture.

    A `--task` scope also records a `spend` condition on that task (`task_record.record_condition` refuses a
    delegate, a missing until and a bad effect); the session record is written either way, so the session reads
    the posture at once. An `until_reset` is resolved to its ISO time here."""
    if mode not in MODES:
        raise SpendRefused("mode must be lean or normal")
    if os.environ.get("CLAUDE_CODE_SIDECAR"):
        raise SpendRefused("a delegate cannot set the owner's spend posture; the owner sets lean or normal")
    session_id = session_id or _session_id()
    holder, quote = _verify_token(session_id, mode, transcript)
    if until is not None and until_reset is not None:
        raise SpendRefused("give one expiry: --until or --until-reset")
    if until_reset is not None:
        until = _reset_until(until_reset)
    if scope_task and not until:
        raise SpendRefused("task scope needs --until: lean cannot outlive the renewal it was set for")
    if until is not None and task_record.until_epoch(until) is None:
        raise SpendRefused("--until must be an ISO time with a zone, e.g. 2031-01-01T00:00:00Z")
    if scope_task:
        task_record.record_condition(scope_task, KEY, quote, mode, session_id=session_id,
                                     transcript=transcript, until=until)
    posture = {"mode": mode, "scope": "task" if scope_task else "session", "until": until, "quote": quote,
               "task": scope_task, "untilReset": until_reset, "message_id": holder, "set_at": task_record.now()}
    _write_session(session_id, posture)
    return posture


def clear_spend(session_id=None):
    """Write `normal` as this session's posture: it ends a lean posture and a task's lean condition alike."""
    session_id = session_id or _session_id()
    if not session_id:
        raise SpendRefused("no session id: set CLAUDE_CODE_SESSION_ID")
    posture = {"mode": "normal", "scope": None, "until": None, "quote": None, "task": None, "untilReset": None,
               "message_id": None, "set_at": task_record.now()}
    _write_session(session_id, posture)
    return posture


def main(argv=None):
    ap = argparse.ArgumentParser(description="The owner's spend posture: lean or normal, scoped and expiring.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("set"); p.add_argument("mode", choices=MODES)
    p.add_argument("--task", help="also record the posture on this task, so a resumed session reads it")
    expiry = p.add_mutually_exclusive_group()
    expiry.add_argument("--until-reset", dest="until_reset", metavar="PLAN",
                        help="expire at the plan's routing-window reset")
    expiry.add_argument("--until", metavar="ISO", help="expire at this ISO time with a zone")
    sub.add_parser("show"); sub.add_parser("clear")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    try:
        if a.cmd == "set":
            print(json.dumps(set_spend(a.mode, scope_task=a.task, until=a.until,
                                       until_reset=a.until_reset), indent=1))
        elif a.cmd == "show":
            print(json.dumps(current(_session_id()), indent=1))
        elif a.cmd == "clear":
            print(json.dumps(clear_spend(), indent=1))
    except (SpendRefused, task_record.ConditionRefused) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 1
    except KeyError as exc:
        print("REFUSED: %s" % exc.args[0], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
