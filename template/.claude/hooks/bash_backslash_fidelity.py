#!/usr/bin/env python3
r"""pre_bash_dispatch sub-hook: repair the Bash tool's backslash collapse on verified clients.

Measured on Claude Code 2.1.270, Git Bash, win32 (2026-09-14 and 2026-09-15), inside single quotes,
double quotes and quoted heredocs alike:
- a maximal backslash run NOT followed by `"` arrives as n - n//2 (a pair becomes one, three become
  two, a lone backslash survives), before every other ASCII punctuation mark, a letter, a digit,
  a space or a line end;
- a run followed by `"` arrives intact.
Doubling exactly the runs whose class collapses is the inverse. Doubling a run before `"` on that
client turns `\"` into `\\"`, which ends a double-quoted string early.

A later client may change either class, so the rewrite is gated on a per-client record rather than
applied unconditionally:

- The client version is the newest top-level `version` field in the last 64 KB of
  `transcript_path`. This hook is the only reader of it. The record lives at
  `.claude/cache/bash_backslash_fidelity.json` as `{"<version>|<sys.platform>": {"collapses": bool,
  "collapses_before_quote": bool, "observed": "YYYY-MM-DD"}}`, written only by
  `tools/bash_fidelity_probe.py`. A row missing either bool counts as unrecorded.
- A probe invocation is a command whose WHOLE text is `python3 <path>/bash_fidelity_probe.py <arg>
  <arg>`. It is never doubled (the probe must see what the client delivers). With a readable version
  it gains ` --client-version <version>`; without one it passes unchanged and the probe exits 2. A
  command that merely mentions the probe path is not a probe invocation.
- Any other Bash command containing `\\`:
    recorded -> `updatedInput` whose `command` doubles each run of a collapsing class; nothing when
                that changes no run;
    unrecorded key, unreadable version or record -> no rewrite, one advisory per compaction
                window naming the version and the probe command.
- Any internal error: silent.

Scope: the `command` field of Bash only. Monitor's backslash behavior is unmeasured (an excluded
check, re-entered on the first Monitor command containing `\\`), and a `claude -p` child showed no
PreToolUse event on a Workflow call, so repair inside such a child is unverified.

Channel: hookSpecificOutput.updatedInput (no permission decision) or additionalContext.
Registered last in pre_bash_dispatch.HOOKS, so an earlier deny wins before this runs.
Proofs: tests/test_bash_backslash_fidelity.py, tests/test_bash_fidelity_probe.py.
"""
import json
import os
import re
import sys

CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD_ENV = "HARNESS_BASH_FIDELITY_RECORD"  # test-only override of the record path
TAIL_BYTES = 64 * 1024
BS = "\\"
PAIR = BS * 2
DQ = '"'
PROBE_COMMAND = "python3 .claude/tools/bash_fidelity_probe.py 'x" + PAIR + "y' 'x" + PAIR + DQ + "y'"
RUN_RE = re.compile(r"\\+")

# The version is appended to a shell command, so it must be a plain version token.
VERSION_RE = re.compile(r"[0-9A-Za-z._+-]{1,64}")

_PROBE_PATH = (r"(?:(?:[^\s'\"]*[/\\])?bash_fidelity_probe\.py"
               r"|'(?:[^']*[/\\])?bash_fidelity_probe\.py'"
               r"|\"(?:[^\"]*[/\\])?bash_fidelity_probe\.py\")")
_ONE_ARG = r"(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)"
PROBE_INVOCATION = re.compile(r"\A[ \t]*python3?[ \t]+" + _PROBE_PATH
                              + r"[ \t]+" + _ONE_ARG + r"[ \t]+" + _ONE_ARG + r"[ \t]*\Z")


def record_path():
    return os.environ.get(RECORD_ENV) or os.path.join(CLAUDE_DIR, "cache", "bash_backslash_fidelity.json")


def client_version(transcript_path):
    """Newest top-level `version` in the transcript tail, or None."""
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - TAIL_BYTES)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return None
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line of a mid-file tail is partial
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        version = row.get("version")
        if isinstance(version, str) and VERSION_RE.fullmatch(version.strip()):
            return version.strip()
    return None


def lookup(key):
    """(collapses, collapses_before_quote) for `key`, or None when unrecorded, partial or unreadable."""
    try:
        with open(record_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    row = data.get(key) if isinstance(data, dict) else None
    if not isinstance(row, dict):
        return None
    collapses, before_quote = row.get("collapses"), row.get("collapses_before_quote")
    if isinstance(collapses, bool) and isinstance(before_quote, bool):
        return collapses, before_quote
    return None


def is_probe_invocation(command):
    return bool(PROBE_INVOCATION.match(command))


def repair(command, collapses, before_quote):
    """Double each maximal backslash run whose class collapses on this client."""
    def sub(match):
        quoted = command[match.end():match.end() + 1] == DQ
        return match.group(0) * 2 if (before_quote if quoted else collapses) else match.group(0)
    return RUN_RE.sub(sub, command)


def _fire_once(payload, key):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _hook_state
        return _hook_state.fire_once_since_compaction(str(payload.get("session_id") or ""),
                                                      "bash_backslash_fidelity:" + key)
    except Exception:
        return True


def advisory(version):
    return ("[bash-fidelity] This Bash command carries `%s`, but client %s on %s has no complete backslash-fidelity "
            "record, so it ran unrepaired and each `%s` may have reached bash as `%s`. Record this client once "
            "with the whole command: %s"
            % (PAIR, version or "(version unreadable)", sys.platform, PAIR, BS, PROBE_COMMAND))


def evaluate(payload):
    """The hookSpecificOutput dict to emit, or None."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None

    if is_probe_invocation(command):
        version = client_version(payload.get("transcript_path"))
        if not version:
            return None
        return {"hookEventName": "PreToolUse",
                "updatedInput": dict(tool_input, command=command.rstrip() + " --client-version " + version)}

    if PAIR not in command:
        return None
    version = client_version(payload.get("transcript_path"))
    key = "%s|%s" % (version or "unknown", sys.platform)
    rule = lookup(key) if version else None
    if rule is not None:
        repaired = repair(command, *rule)
        if repaired == command:
            return None
        return {"hookEventName": "PreToolUse", "updatedInput": dict(tool_input, command=repaired)}
    if not _fire_once(payload, key):
        return None
    return {"hookEventName": "PreToolUse", "additionalContext": advisory(version)}


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        out = evaluate(payload)
        if out:
            sys.stdout.write(json.dumps({"hookSpecificOutput": out}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
