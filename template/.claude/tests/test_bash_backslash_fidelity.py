#!/usr/bin/env python3
r"""Proof for hooks/bash_backslash_fidelity.py, the pre_bash_dispatch sub-hook that repairs the
Bash tool's `\\` -> `\` collapse on clients whose record says it collapses.

Real PreToolUse payloads, asserted on the emitted channel (`updatedInput`, `additionalContext`,
or empty). Exit codes outside {0, 2} and tracebacks are CRASH, never a pass.

Client model (measured on 2.1.270|win32): a maximal backslash run followed by `"` arrives intact;
every other run of n arrives as n - n//2. The record carries one bool per run class:
`collapses` (a run not followed by `"`) and `collapses_before_quote`.

Cases:
- the standard record (true, false) -> client(updated) == original over the probe strings, including
  commands that mix `\\` with `\"`; other tool_input fields kept;
- a record whose runs before `"` also collapse (true, true) -> the quote runs are doubled too;
- a command whose only pair sits before `"` needs no repair and emits nothing;
- collapses:false, a record without `collapses_before_quote`, an unrecorded version, an unreadable
  transcript and a tail with no version row each emit no updatedInput; the unrecorded cases advise
  once per client version and platform, machine-wide, naming the two-argument probe command;
- an unrecorded version with a complete row recorded on this platform repairs with the newest such
  row (by `observed`) on every call and advises once per window, naming both versions;
- a two-argument probe invocation is never doubled: it gains `--client-version <version>`, or passes
  unchanged when no version is readable; other executing forms are denied with PROBE_COMMAND;
- a command that only mentions the probe path is rewritten normally;
- a probe run without `--client-version` exits 2;
- the reaper's PostToolUse matcher equals the anchored string exactly;
- non-Bash and backslash-free commands emit nothing;
- a dangerous_shell_guard deny still wins through the registered dispatcher, which is registered last.

    python3 .claude/tests/test_bash_backslash_fidelity.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid

import _settings_probe

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
HOOKS = os.path.join(REPO, ".claude", "hooks")
HOOK = os.path.join(HOOKS, "bash_backslash_fidelity.py")
DISPATCH = os.path.join(HOOKS, "pre_bash_dispatch.py")
PROBE = os.path.join(REPO, ".claude", "tools", "bash_fidelity_probe.py")
SETTINGS = _settings_probe.settings_path(os.path.join(REPO, ".claude"))
REAPER_MATCHER = "^(?:Agent|Workflow|TaskStop|TaskOutput|Skill|ToolSearch|LSP|NotebookEdit|mcp__.*)$"
VERSION = "9.9.9-proof"
KEY = "%s|%s" % (VERSION, sys.platform)

BS = "\\"
PAIR = BS * 2
DQ = '"'

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

failures = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        failures.append(label + (" :: " + str(detail)[:400] if detail else ""))


RUN = re.compile(r"\\+")


def client(text, collapses=True, before_quote=False):
    """The measured client: each maximal backslash run of n arrives as n - n//2 when its class
    collapses; a run followed by `"` is its own class."""
    def sub(match):
        quoted = text[match.end():match.end() + 1] == DQ
        if not (before_quote if quoted else collapses):
            return match.group(0)
        n = len(match.group(0))
        return BS * (n - n // 2)
    return RUN.sub(sub, text)


def write_transcript(path, version=VERSION):
    rows = [{"type": "user", "uuid": "u1", "version": "0.0.1-old", "message": {"content": "hi"}},
            {"type": "assistant", "uuid": "a1", "message": {"content": "x"}}]
    if version is not None:
        rows.append({"type": "user", "uuid": "u2", "version": version, "message": {"content": "later"}})
    else:
        rows = [{"type": "assistant", "uuid": "a1", "message": {"content": "no version here"}}]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def write_record(path, collapses, before_quote=False, include_quote=True):
    row = {"collapses": collapses, "observed": "2026-09-15"}
    if include_quote:
        row["collapses_before_quote"] = before_quote
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({KEY: row}, fh)


def run(script, payload, env, args=()):
    p = subprocess.run([sys.executable, script] + list(args), input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", timeout=90, env=env, cwd=REPO)
    # A missing script also exits 2 ("can't open file"); that is a CRASH, not a deny.
    crashed = p.returncode not in (0, 2) or "Traceback" in p.stderr or "can't open file" in p.stderr
    hso = {}
    if p.stdout.strip():
        try:
            hso = json.loads(p.stdout).get("hookSpecificOutput") or {}
        except ValueError:
            hso = {"UNPARSEABLE": p.stdout}
    return p, hso, crashed


def payload(command, transcript, tool="Bash", sid=None, extra=None):
    ti = {"command": command, "description": "proof call", "timeout": 1234}
    ti.update(extra or {})
    return {"hook_event_name": "PreToolUse", "tool_name": tool,
            "session_id": sid or ("bbf" + uuid.uuid4().hex[:8]), "transcript_path": transcript,
            "cwd": REPO, "tool_input": ti}


def test_s9_colon_operands(tmp_path):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path / 'state'),
               HARNESS_BASH_FIDELITY_RECORD=str(tmp_path / 'absent.json'))
    for command in ('git show HEAD:.claude/CLAUDE.core.md | head -1',
                    'git -C /repo show refs/heads/main:folder/file',
                    'gh api repos/owner/repo/contents/HEAD:README.md'):
        p, out, crashed = run(HOOK, payload(command, ''), env)
        assert not crashed and p.returncode == 0
        assert out['updatedInput']['command'] == 'MSYS_NO_PATHCONV=1 ' + command
        p, again, crashed = run(HOOK, payload(out['updatedInput']['command'], ''), env)
        assert not crashed and 'updatedInput' not in again
    command = 'cd /repo && git show HEAD:README.md'
    p, out, crashed = run(HOOK, payload(command, ''), env)
    assert not crashed and out['updatedInput']['command'] == 'cd /repo && MSYS_NO_PATHCONV=1 git show HEAD:README.md'
    command = 'git show HEAD:README.md; git show HEAD:README.md'
    p, out, crashed = run(HOOK, payload(command, ''), env)
    assert not crashed and out['updatedInput']['command'] == 'MSYS_NO_PATHCONV=1 git show HEAD:README.md; MSYS_NO_PATHCONV=1 git show HEAD:README.md'
    command = 'git show :.claude/baseline.lock.json'
    p, out, crashed = run(HOOK, payload(command, ''), env)
    assert not crashed and out['updatedInput']['command'] == 'MSYS_NO_PATHCONV=1 ' + command
    for command in ('git status', 'git -C C:/repo status', 'git remote add x https://example.invalid/repo',
                    "printf '%s' 'git show HEAD:path'", "python3 - <<'EOF'\nprint('git show HEAD:path')\nEOF"):
        p, out, crashed = run(HOOK, payload(command, ''), env)
        assert not crashed and p.returncode == 0 and 'updatedInput' not in out


def main():
    tmp = tempfile.mkdtemp(prefix="bbf_")
    transcript = os.path.join(tmp, "t.jsonl")
    write_transcript(transcript)
    noversion = os.path.join(tmp, "nov.jsonl")
    write_transcript(noversion, version=None)
    record = os.path.join(tmp, "record.json")
    base_env = dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_PROJECT_DIR=REPO,
                    HARNESS_HOOK_STATE_DIR=os.path.join(tmp, "state"), HARNESS_BASH_FIDELITY_RECORD=record)
    probe_cmd = "python3 .claude/tools/bash_fidelity_probe.py 'x" + PAIR + "y' 'x" + PAIR + DQ + "y'"

    check("hook file exists", os.path.isfile(HOOK), HOOK)

    # --- standard record: pairs collapse, runs before `"` arrive intact -------------------------
    write_record(record, True, False)
    for s in PROBE_STRINGS + QUOTE_STRINGS:
        p, hso, crashed = run(HOOK, payload(s, transcript), base_env)
        upd = (hso.get("updatedInput") or {})
        check("standard record rewrites %r and client(updated) == original" % s[:40],
              not crashed and client(upd.get("command", "")) == s and upd.get("command") != s,
              p.stdout + p.stderr)
        check("standard record keeps every other tool_input field for %r" % s[:20],
              upd.get("description") == "proof call" and upd.get("timeout") == 1234, upd)
        check("standard record carries no permission decision for %r" % s[:20],
              "permissionDecision" not in hso, hso)
    p, hso, crashed = run(HOOK, payload(QUOTE_ONLY, transcript), base_env)
    check("a command whose only pair sits before a double quote emits nothing",
          not crashed and not p.stdout.strip(), p.stdout + p.stderr)

    # --- a client whose runs before `"` also collapse -------------------------------------------
    write_record(record, True, True)
    for s in QUOTE_STRINGS + [QUOTE_ONLY]:
        p, hso, crashed = run(HOOK, payload(s, transcript), base_env)
        upd = (hso.get("updatedInput") or {}).get("command", "")
        check("before-quote record doubles quote runs too for %r" % s[:40],
              not crashed and client(upd, True, True) == s and upd != s, p.stdout + p.stderr)

    # --- collapses: false ------------------------------------------------------------------------
    write_record(record, False, False)
    p, hso, crashed = run(HOOK, payload(PROBE_STRINGS[0], transcript), base_env)
    check("collapses:false emits nothing", not crashed and not p.stdout.strip(), p.stdout + p.stderr)

    # --- a record without the before-quote measurement is unrecorded ------------------------------
    write_record(record, True, include_quote=False)
    sid = "bbfold" + uuid.uuid4().hex[:4]
    p, hso, crashed = run(HOOK, payload(QUOTE_STRINGS[0], transcript, sid=sid), base_env)
    ctx = hso.get("additionalContext", "")
    check("a record without collapses_before_quote emits no updatedInput",
          not crashed and "updatedInput" not in hso, p.stdout + p.stderr)
    check("a record without collapses_before_quote advises the two-argument probe",
          "bash_fidelity_probe.py 'x" + PAIR + "y' 'x" + PAIR + DQ + "y'" in ctx, ctx)

    # --- unrecorded version: no rewrite, advise once ---------------------------------------------
    with open(record, "w", encoding="utf-8") as fh:
        json.dump({"other|plat": {"collapses": True, "collapses_before_quote": False, "observed": "2026-09-15"}}, fh)
    base_env['HARNESS_HOOK_STATE_DIR'] = os.path.join(tmp, 'unrecorded-state')
    sid = "bbfonce" + uuid.uuid4().hex[:4]
    p1, h1, c1 = run(HOOK, payload(PROBE_STRINGS[0], transcript, sid=sid), base_env)
    p2, h2, c2 = run(HOOK, payload(PROBE_STRINGS[0], transcript, sid=sid), base_env)
    ctx = h1.get("additionalContext", "")
    check("unrecorded version emits no updatedInput", not c1 and "updatedInput" not in h1, p1.stdout + p1.stderr)
    check("unrecorded version advises, naming the version and the probe command",
          VERSION in ctx and "bash_fidelity_probe.py 'x" + PAIR + "y' 'x" + PAIR + DQ + "y'" in ctx, ctx)
    check("unrecorded version advises once per client version and platform, machine-wide",
          not c2 and not p2.stdout.strip(), p2.stdout + p2.stderr)

    # --- unrecorded version, a recorded row on this platform: the newest one carries forward ------
    with open(record, "w", encoding="utf-8") as fh:
        json.dump({"1.0.0|" + sys.platform: {"collapses": False, "collapses_before_quote": False,
                                             "observed": "2026-09-01"},
                   "1.0.1|" + sys.platform: {"collapses": True, "collapses_before_quote": False,
                                             "observed": "2026-09-20"},
                   "1.0.2|" + sys.platform: {"collapses": True, "observed": "2026-09-25"},
                   "other|plat": {"collapses": False, "collapses_before_quote": False, "observed": "2026-09-29"}},
                  fh)
    base_env['HARNESS_HOOK_STATE_DIR'] = os.path.join(tmp, 'carried-state')
    sid = "bbfcarry" + uuid.uuid4().hex[:4]
    p1, h1, c1 = run(HOOK, payload(PROBE_STRINGS[0], transcript, sid=sid), base_env)
    p2, h2, c2 = run(HOOK, payload(PROBE_STRINGS[0], transcript, sid=sid), base_env)
    upd1 = (h1.get("updatedInput") or {}).get("command", "")
    upd2 = (h2.get("updatedInput") or {}).get("command", "")
    ctx = h1.get("additionalContext", "")
    check("unrecorded version repairs with the newest complete row recorded on this platform",
          not c1 and client(upd1) == PROBE_STRINGS[0] and upd1 != PROBE_STRINGS[0], p1.stdout + p1.stderr)
    check("the carried-forward repair advises, naming both versions and the probe command",
          VERSION in ctx and "1.0.1" in ctx and "bash_fidelity_probe.py 'x" + PAIR + "y'" in ctx, ctx)
    check("the carried-forward repair repeats every call; its advisory fires once per window",
          not c2 and client(upd2) == PROBE_STRINGS[0] and "additionalContext" not in h2, p2.stdout + p2.stderr)

    # --- unreadable record ---------------------------------------------------------------------
    with open(record, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    p, hso, crashed = run(HOOK, payload(PROBE_STRINGS[0], transcript), base_env)
    check("unreadable record emits no updatedInput", not crashed and "updatedInput" not in hso, p.stdout + p.stderr)

    # --- unreadable transcript / tail without a version row ---------------------------------------
    write_record(record, True, False)
    p, hso, crashed = run(HOOK, payload(PROBE_STRINGS[0], os.path.join(tmp, "missing.jsonl")), base_env)
    check("unreadable transcript emits no updatedInput", not crashed and "updatedInput" not in hso, p.stdout + p.stderr)
    p, hso, crashed = run(HOOK, payload(PROBE_STRINGS[0], noversion), base_env)
    check("transcript tail with no version row emits no updatedInput",
          not crashed and "updatedInput" not in hso, p.stdout + p.stderr)

    # --- probe invocation ------------------------------------------------------------------------
    p, hso, crashed = run(HOOK, payload(probe_cmd, transcript), base_env)
    upd = (hso.get("updatedInput") or {}).get("command")
    check("probe invocation is not doubled and gains --client-version <version>",
          not crashed and upd == probe_cmd + " --client-version " + VERSION, repr(upd) + p.stderr)
    abs_probe_cmd = probe_cmd.replace(".claude/tools/", "/c/some/where/.claude/tools/")
    p, hso, crashed = run(HOOK, payload(abs_probe_cmd, transcript), base_env)
    upd = (hso.get("updatedInput") or {}).get("command")
    check("probe invocation with an absolute path is recognized",
          not crashed and upd == abs_probe_cmd + " --client-version " + VERSION, repr(upd))
    p, hso, crashed = run(HOOK, payload(probe_cmd, os.path.join(tmp, "missing.jsonl")), base_env)
    check("probe invocation with no readable version passes unchanged (no output)",
          not crashed and not p.stdout.strip(), p.stdout + p.stderr)
    one_arg = "python3 .claude/tools/bash_fidelity_probe.py 'x" + PAIR + "y'"
    p, hso, crashed = run(HOOK, payload(one_arg, transcript), base_env)
    upd = (hso.get("updatedInput") or {}).get("command", "")
    check("the retired one-argument probe execution is denied",
          not crashed and hso.get("permissionDecision") == "deny"
          and probe_cmd in hso.get("permissionDecisionReason", ""), hso)

    mention = "grep -n 'x" + PAIR + "y' .claude/tools/bash_fidelity_probe.py"
    p, hso, crashed = run(HOOK, payload(mention, transcript), base_env)
    upd = (hso.get("updatedInput") or {}).get("command", "")
    check("a command that only mentions the probe path is rewritten normally",
          not crashed and upd != mention and client(upd) == mention and "--client-version" not in upd, repr(upd))
    chained = probe_cmd + " && echo done"
    p, hso, crashed = run(HOOK, payload(chained, transcript), base_env)
    upd = (hso.get("updatedInput") or {}).get("command", "")
    check("a probe call inside a larger command is denied",
          not crashed and hso.get("permissionDecision") == "deny"
          and probe_cmd in hso.get("permissionDecisionReason", ""), hso)
    observed = ('cd "<repo>" && ' + probe_cmd.replace(
        'bash_fidelity_probe.py ', 'bash_fidelity_probe.py --client-version 2.1.290 ')
        + ' 2>&1 | tail -1; git grep bash_fidelity_probe.py')
    for command in (observed, probe_cmd + ' | tail -1',
                    '/usr/bin/python3 .claude/tools/bash_fidelity_probe.py x y',
                    'python3 -u .claude/tools/bash_fidelity_probe.py x y'):
        p, hso, crashed = run(HOOK, payload(command, transcript), base_env)
        check("noncanonical probe execution denied: " + command[:60],
              not crashed and hso.get("permissionDecision") == "deny"
              and probe_cmd in hso.get("permissionDecisionReason", "")
              and "updatedInput" not in hso, hso)
    for command in ('git grep -n bash_fidelity_probe.py',
                    "cat <<'EOF'\npython3 .claude/tools/bash_fidelity_probe.py x y\nEOF",
                    "printf '%s' 'python3 .claude/tools/bash_fidelity_probe.py x y'",
                    'python3 editor.py .claude/tools/bash_fidelity_probe.py'):
        p, hso, crashed = run(HOOK, payload(command, transcript), base_env)
        check("probe mention passes unchanged: " + command[:60],
              not crashed and not hso, hso)

    # --- probe without --client-version exits 2 ----------------------------------------------------
    if os.path.isfile(PROBE):
        pp = subprocess.run([sys.executable, PROBE, "x" + BS + "y", "x" + PAIR + DQ + "y"], capture_output=True,
                            text=True, encoding="utf-8", timeout=60, env=base_env, cwd=REPO)
        check("probe run without --client-version exits 2", pp.returncode == 2 and "Traceback" not in pp.stderr,
              "rc=%s %s" % (pp.returncode, pp.stderr))
    else:
        check("probe file exists", False, PROBE)

    # --- non-Bash and backslash-free ------------------------------------------------------------
    p, hso, crashed = run(HOOK, payload(PROBE_STRINGS[0], transcript, tool="PowerShell"), base_env)
    check("PowerShell command with a pair emits nothing", not crashed and not p.stdout.strip(), p.stdout + p.stderr)
    p, hso, crashed = run(HOOK, payload(PROBE_STRINGS[0], transcript, tool="Monitor"), base_env)
    check("Monitor command with a pair emits nothing", not crashed and not p.stdout.strip(), p.stdout + p.stderr)
    p, hso, crashed = run(HOOK, payload("git status --short | head -3", transcript), base_env)
    check("backslash-free Bash command emits nothing", not crashed and not p.stdout.strip(), p.stdout + p.stderr)
    p, hso, crashed = run(HOOK, payload("printf 'a" + BS + "nb'", transcript), base_env)
    check("a lone backslash (no pair) emits nothing", not crashed and not p.stdout.strip(), p.stdout + p.stderr)
    p = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True,
                       encoding="utf-8", timeout=60, env=base_env, cwd=REPO)
    check("malformed payload exits 0 silent", p.returncode == 0 and not p.stdout.strip() and "Traceback" not in p.stderr,
          p.stdout + p.stderr)

    # --- registration: last in the chain; deny still wins; rewrite surfaces through the dispatcher --
    sys.path.insert(0, HOOKS)
    try:
        import pre_bash_dispatch
        last = pre_bash_dispatch.HOOKS[-1]
        check("bash_backslash_fidelity is registered last in pre_bash_dispatch.HOOKS",
              last[0].__name__ == "bash_backslash_fidelity" and tuple(last[1]) == (), last)
    except Exception as exc:
        check("pre_bash_dispatch imports", False, repr(exc))
    write_record(record, True, False)
    deny_cmd = "rm -rf build" + PAIR + "out"
    p, hso, crashed = run(DISPATCH, payload(deny_cmd, transcript), base_env)
    check("dangerous_shell_guard deny still wins through the dispatcher (exit 2, no updatedInput)",
          p.returncode == 2 and "updatedInput" not in p.stdout, "rc=%s %s %s" % (p.returncode, p.stdout, p.stderr))
    p, hso, crashed = run(DISPATCH, payload(QUOTE_STRINGS[0], transcript), base_env)
    upd = (hso.get("updatedInput") or {}).get("command", "")
    check("the dispatcher forwards the rewrite (client(updated) == original)",
          not crashed and p.returncode == 0 and client(upd) == QUOTE_STRINGS[0] and upd != QUOTE_STRINGS[0],
          p.stdout + p.stderr)

    # --- reaper matcher --------------------------------------------------------------------------
    with open(SETTINGS, encoding="utf-8") as fh:
        hooks = json.load(fh)["hooks"]
    matchers = [e.get("matcher") for e in hooks.get("PostToolUse", [])
                for h in e.get("hooks", []) if "runaway_scan_reaper.py" in h.get("command", "")]
    check("the reaper's PostToolUse matcher equals the anchored string exactly",
          matchers == [REAPER_MATCHER], matchers)

    print("\n%d failure(s)" % len(failures) if failures else "\nall ok")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    legacy_rc = main()
    import pytest
    pytest_rc = pytest.main([__file__, '-q', '-s'])
    sys.exit(legacy_rc or pytest_rc)
