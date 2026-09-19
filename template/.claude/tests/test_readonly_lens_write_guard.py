#!/usr/bin/env python3
"""Re-runnable proof for hooks/readonly_lens_write_guard.py.

The dispatcher-level test (test_pre_edit_dispatch.py) exercises this hook as one
sub-hook in a chain; this file is its own dedicated proof, required by
git_guardrails.py `_has_proof` before a commit touching the hook is allowed.

Covers: a clean payload (no live marker) emits nothing; a planted violation (live
marker, agent_id present, target outside allow_prefixes) emits the
`[readonly-lens-write]` stderr note without blocking; restoring clean (marker
removed) goes silent again; the standalone `--hook`/stdin->stdout channel via a
real subprocess on the same planted payload, asserting stdout/exit code and
classifying anything other than exit 0 with the expected stderr text, or a
traceback, as CRASH rather than allow; and a malformed/non-dict payload does not
raise.

State is redirected with HOME/USERPROFILE to a tempdir — the hook resolves its
marker path via os.path.expanduser("~/.claude/.routing_state"), not
HARNESS_HOOK_STATE_DIR, so HOME/USERPROFILE is the only redirection that works for it.
This never touches ~/.claude/.routing_state on the real machine.

    python3 .claude/tests/test_readonly_lens_write_guard.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
HOOK = os.path.join(HOOKS, "readonly_lens_write_guard.py")


def load_module():
    spec = importlib.util.spec_from_file_location("readonly_lens_write_guard_probe", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_marker(state_dir, session, allow_prefixes, ttl=600):
    os.makedirs(state_dir, exist_ok=True)
    short = session[:8]
    path = os.path.join(state_dir, f"readonly-{short}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"expires_at": time.time() + ttl, "allow_prefixes": allow_prefixes}, fh)
    return path


def payload(target, session="prelens1", agent_id="agent-42", **extra):
    body = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": session,
            "agent_id": agent_id, "tool_input": {"file_path": target, "old_string": "x",
                                                  "new_string": "y"}}
    body.update(extra)
    return body


def run_subprocess(payload_obj, env):
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload_obj),
                       capture_output=True, text=True, timeout=60, env=env,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def main():
    tmp = tempfile.mkdtemp(prefix="readonly_lens_write_guard_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               PYTHONIOENCODING="utf-8")

    cases = []
    target = os.path.join(tmp, "repo", "Scripts", "SpellInstance.cs")
    allowed_dir = os.path.join(tmp, "allowed")

    # --- (a) clean payload: no live marker -> None, no emission -----------
    env_backup = dict(os.environ)
    os.environ["HOME"] = home
    os.environ["USERPROFILE"] = home
    try:
        module = load_module()
        result = module.process(payload(target, session="cleanses"))
        cases.append(("(a) clean payload (no marker) emits nothing", result is None))

        # --- (b) planted violation: live marker, off-lens target ----------
        marker = write_marker(state_dir, "prelens1", [allowed_dir])
        result = module.process(payload(target, session="prelens1"))
        cases.append(("(b) planted violation emits the readonly-lens-write stderr note",
                      isinstance(result, dict)
                      and "[readonly-lens-write]" in (result.get("stderr") or "")
                      and target in result["stderr"]))
        cases.append(("(b) planted violation is advisory only (no block/deny key)",
                      isinstance(result, dict) and "block" not in result and "deny" not in result))

        # --- (b-neg) same marker, target under an allowed prefix -> None --
        allowed_target = os.path.join(allowed_dir, "SpellInstance.cs")
        result = module.process(payload(allowed_target, session="prelens1"))
        cases.append(("(b-neg) a target under allow_prefixes emits nothing", result is None))

        # --- (c) restored clean: marker removed -> silent again -----------
        os.unlink(marker)
        result = module.process(payload(target, session="prelens1"))
        cases.append(("(c) restored clean (marker removed) emits nothing again", result is None))

        # --- (c-neg) expired marker is treated as absent, and unlinked ----
        expired = write_marker(state_dir, "prelens2", [], ttl=-10)
        result = module.process(payload(target, session="prelens2"))
        cases.append(("(c-neg) an expired marker is treated as absent", result is None))
        cases.append(("(c-neg) an expired marker is unlinked",
                      not os.path.exists(expired)))
    finally:
        os.environ.clear()
        os.environ.update(env_backup)

    # --- (d) standalone stdin->stdout channel, real subprocess ------------
    live_marker = write_marker(state_dir, "prelens3", [allowed_dir])
    rc, out, err = run_subprocess(payload(target, session="prelens3"), env)
    crash = rc not in (0,) or "Traceback" in err
    cases.append(("(d) standalone channel exits 0 (else CRASH)", not crash))
    cases.append(("(d) standalone channel writes the note to stderr, nothing to stdout",
                  not crash and "[readonly-lens-write]" in err and out.strip() == ""))
    os.unlink(live_marker) if os.path.exists(live_marker) else None

    # clean payload through the real subprocess too (no marker at all)
    rc, out, err = run_subprocess(payload(target, session="preclean"), env)
    crash = rc not in (0,) or "Traceback" in err
    cases.append(("(d-neg) standalone channel is silent on a clean payload (else CRASH)",
                  not crash and out.strip() == "" and err == ""))

    # --- (e) malformed / non-dict payload does not crash -------------------
    module = load_module()
    for bad, label in ((None, "None"), ("a string", "string"), ([1, 2, 3], "list"), (42, "int")):
        try:
            result = module.process(bad)
            ok = result is None
        except Exception as exc:  # pragma: no cover - the assertion below fails loudly
            ok = False
        cases.append((f"(e) process({label}) does not raise and returns None", ok))

    rc, out, err = run_subprocess({}, env)  # empty dict: valid JSON, no fields
    crash = rc not in (0,) or "Traceback" in err
    cases.append(("(e) standalone channel on an empty-dict payload does not crash",
                  not crash))

    proc = subprocess.run([sys.executable, HOOK], input="not json at all",
                          capture_output=True, text=True, timeout=60, env=env,
                          encoding="utf-8", errors="replace")
    # main() has no its own JSONDecodeError guard (unlike pre_edit_dispatch.py's main);
    # the module __main__ try/except wraps main() and exits 0 on any exception, so this
    # is expected to stay non-crashing. Classify strictly: non-zero exit or a traceback
    # on stderr means CRASH rather than allow.
    is_crash = proc.returncode != 0 or "Traceback" in (proc.stderr or "")
    cases.append(("(e) malformed (non-JSON) stdin does not crash (else CRASH)",
                  not is_crash))

    passed = sum(1 for _, ok in cases if ok)
    total = len(cases)
    for name, ok in cases:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"\n{passed}/{total} passed")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
