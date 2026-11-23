"""The loss floor's dispatcher (T1). `hooks/pre_bash.py`, the one PreToolUse entry registered for `MATCHER`, imports
this file and calls `decide_text`; any fault in this file denies there.

Order: a tracked `git add -- <paths> && git commit` folds to a pathspec commit first (`_git_commit.fold_tracked_adds`),
so every check judges the command that will run. Then each FLOOR module's `check(payload)` runs, in order, on the
payload as rewritten so far; the first deny wins, then an `ask`, then a rewrite (`updatedInput`), else allow.

Fails closed: a payload that is not a JSON object denies; a FLOOR module that fails to import denies every call its
matcher selects (the action it judges, never the noun) and names the import error; a FLOOR check that raises denies
its call; a decision whose git reads ran out of `BUDGET_S` denies, since a hook the client kills on its timeout is a
non-blocking error (the call would run unjudged). A HELPERS module (the kept small Bash helpers, V16) that fails to
import or raises is skipped.
"""
import contextlib
import importlib
import json
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MATCHER = "Bash|PowerShell|Monitor|TaskStop"
SHELLS = ("Bash", "PowerShell")
UNREADABLE = ("BLOCKED call (CLAUDE.core.md §Shell Discipline) — the loss floor could not read its input. "
              "Re-issue the same call; if it repeats, report it.")
# The whole decision's git budget, below the client's default 60 s hook timeout with room for one Python start and
# the process-table reads `kill_guard` bounds itself (the registered timeout is set at cutover, at least 60 s).
BUDGET_S = 40.0
OUT_OF_TIME = ("BLOCKED call (CLAUDE.core.md §Shell Discipline) — the loss floor's git reads ran past its %d s budget, "
               "so the call was not judged (a loaded machine or a stalled git). Re-issue the same call; if it "
               "repeats, find what stalls git in that checkout.")
# What a broken kill_guard would have judged: a stop, a window close, or a scratch script it reads for one.
_STOP_WORDS = re.compile(r"kill|stop-process|spps|wmic|terminate|closemainwindow|wm_close|sc_close|0xf060"
                         r"|(?:post|send)message|\.claude[/\\]scratch[/\\]", re.I)


def _command(payload):
    command = (payload.get("tool_input") or {}).get("command")
    return command if isinstance(command, str) else ""


def _shell_call(payload):
    return payload.get("tool_name") in SHELLS


def _stop_call(payload):
    return payload.get("tool_name") == "TaskStop" or (_shell_call(payload) and bool(_STOP_WORDS.search(_command(payload))))


# (module, matcher, the action it judges). The matcher only scopes a failed import; a loaded module judges every call.
FLOOR_MEMBERS = (
    ("dangerous_shell_guard", _shell_call, "destructive-command check"),
    ("kill_guard", _stop_call, "stop/kill check"),
)
# The kept small Bash helpers (V16, §4 Q12), each `check(payload)`: the length cut and the recursive-grep deny, then
# the backslash repair last, since it rewrites the command every deny above must judge first.
HELPER_NAMES = ("bash_length_guard", "unbounded_scan_guard", "bash_backslash_fidelity")


def _fault(exc):
    frames = traceback.extract_tb(exc.__traceback__)
    where = getattr(exc, "filename", None) if isinstance(exc, SyntaxError) else (frames[-1].filename if frames else None)
    return "%s: %s%s" % (type(exc).__name__, exc, " (in %s)" % os.path.basename(where) if where else "")


def _load(name):
    try:
        return importlib.import_module(name), None
    except Exception as exc:
        return None, _fault(exc)


FLOOR = tuple((name, matches, action) + _load(name) for name, matches, action in FLOOR_MEMBERS)
HELPERS = tuple(module for module, _error in (_load(name) for name in HELPER_NAMES) if module is not None)
try:
    import _git_commit
except Exception:
    _git_commit = None
import _git_run


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _fold(payload, contexts):
    """The payload with a tracked add+commit folded to a pathspec commit, or the payload unchanged. A fold that
    cannot run leaves the original command, which the checks judge as written."""
    if payload.get("tool_name") != "Bash" or _git_commit is None:
        return payload
    try:
        folded = _git_commit.fold_tracked_adds(_command(payload), payload.get("cwd") or ".")
    except Exception:
        return payload
    if not folded:
        return payload
    contexts.append("Tracked add+commit folded to a pathspec commit; the floor judged that publication.")
    return dict(payload, tool_input=dict(payload["tool_input"], command=folded))


def decide(payload):
    """The hook output for one PreToolUse payload, or None to allow."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input", {}), dict):
        return _deny(UNREADABLE)
    memo = _git_commit.memoized() if _git_commit is not None else contextlib.nullcontext()
    with memo, _git_run.deadline(BUDGET_S) as clock:
        out = _decide(payload)
    return _deny(OUT_OF_TIME % BUDGET_S) if clock.spent else out


def _decide(payload):
    contexts, asked = [], None
    original = payload.get("tool_input")
    payload = _fold(payload, contexts)
    members = [(name, matches, action, module, error, True) for name, matches, action, module, error in FLOOR]
    members += [(getattr(module, "__name__", "helper"), None, None, module, None, False) for module in HELPERS]
    for name, matches, action, module, error, floor in members:
        if module is None:
            try:
                selected = bool(matches(payload))
            except Exception:
                selected = True
            if selected:
                return _deny("BLOCKED %s — hooks/%s.py failed to import (%s). Repair hooks/%s.py; no %s runs "
                             "until it imports cleanly." % (action, name, error, name, action))
            continue
        try:
            out = module.check(payload)
        except Exception as exc:
            if not floor:
                continue
            return _deny("BLOCKED %s — %s raised %s, so the call was not judged. Repair hooks/%s.py, then "
                         "re-issue the same call." % (action, name, _fault(exc), name))
        hso = (out or {}).get("hookSpecificOutput") or {}
        if hso.get("permissionDecision") == "deny":
            return out
        if hso.get("permissionDecision") == "ask" and asked is None:
            asked = out
        if isinstance(hso.get("updatedInput"), dict):
            payload = dict(payload, tool_input=hso["updatedInput"])
        if hso.get("additionalContext"):
            contexts.append(hso["additionalContext"])
    if asked is not None:
        return asked
    hso = {"hookEventName": "PreToolUse"}
    if payload.get("tool_input") != original:
        hso["updatedInput"] = payload["tool_input"]
    if contexts:
        hso["additionalContext"] = "\n\n".join(contexts)
    return {"hookSpecificOutput": hso} if len(hso) > 1 else None


def decide_text(raw):
    """`decide` on the stdin text of a hook call; text that is not JSON denies."""
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    return decide(payload)
