#!/usr/bin/env python3
"""Re-runnable proof for hooks/pre_bash_dispatch.py — the single PreToolUse entry for the
Bash/PowerShell/Monitor family.

Parity, not re-derivation: for each payload the twelve sub-hooks run standalone by subprocess
(their own CLI contract) and the dispatcher runs once; the dispatcher's verdict must equal the
native aggregation of the standalone results (any block ⇒ blocked; else deny > ask > allow; every
additionalContext present). A malformed payload and a non-matching tool_name must exit 0 silent.
Also prints the wall-clock of twelve parallel spawns vs one dispatcher spawn (evidence, not an
assertion — timing is machine-bound).

    python3 .claude/tests/test_pre_bash_dispatch.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS_DIR = os.path.normpath(os.path.join(HERE, "..", "hooks"))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(HOOKS_DIR, "pre_bash_dispatch.py")

sys.path.insert(0, HOOKS_DIR)
import pre_bash_dispatch  # noqa: E402

SUB_HOOKS = [(os.path.basename(m.__file__), list(argv)) for m, argv in pre_bash_dispatch.HOOKS]


def run(cmd, payload_text, env, timeout=90):
    return subprocess.run(cmd, input=payload_text, capture_output=True, text=True,
                          timeout=timeout, env=env, encoding="utf-8", cwd=REPO)


def hso(proc):
    text = proc.stdout.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}


def standalone_verdict(payload_text, env):
    """Native aggregation over the twelve standalone runs."""
    blocked, decision, contexts = False, None, []
    rank = {"deny": 3, "ask": 2, "allow": 1}
    for name, argv in SUB_HOOKS:
        p = run([sys.executable, os.path.join(HOOKS_DIR, name)] + argv, payload_text, env)
        if p.returncode == 2:
            blocked = True
            continue
        out = hso(p)
        if out.get("permissionDecision") == "deny":
            blocked = True
        pd = out.get("permissionDecision")
        if pd in rank and rank[pd] > rank.get(decision, 0):
            decision = pd
        ctx = out.get("additionalContext")
        if isinstance(ctx, str) and ctx.strip():
            contexts.append(ctx)
    return blocked, decision, contexts


def dispatcher_verdict(payload_text, env):
    p = run([sys.executable, DISPATCH], payload_text, env)
    out = hso(p)
    blocked = p.returncode == 2 or out.get("permissionDecision") == "deny"
    return blocked, out.get("permissionDecision"), out.get("additionalContext") or "", p


def payload(tool, command):
    return json.dumps({"tool_name": tool, "session_id": "pbd" + uuid.uuid4().hex[:8],
                       "cwd": REPO, "tool_input": {"command": command}})


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="pbd_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=os.path.join(tmp, "state"),
               CLAUDE_PROJECT_DIR=REPO, PYTHONIOENCODING="utf-8")

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + detail[:300] if detail else ""))

    cases = [
        ("Bash", "git status --short | head -3"),
        ("Bash", "rm -rf build_out"),
        ("Bash", 'cd "%s" && git status --short' % REPO.replace("\\", "/")),
        ("Bash", "grep -r TODO ."),
        ("PowerShell", "Get-ChildItem"),
        ("Monitor", "git status"),
    ]
    for tool, command in cases:
        # Distinct session ids: advisories dedupe per session, so a shared id would let the
        # standalone pass consume the nudge the dispatcher pass is then measured against.
        s_blocked, s_decision, s_contexts = standalone_verdict(payload(tool, command), env)
        d_blocked, d_decision, d_context, proc = dispatcher_verdict(payload(tool, command), env)
        label = "%s `%s`" % (tool, command[:40])
        check(label + " — blocked parity (%s)" % s_blocked, s_blocked == d_blocked, proc.stderr)
        if not s_blocked:
            check(label + " — decision parity (%s)" % s_decision, s_decision == d_decision,
                  repr((s_decision, d_decision)))
            check(label + " — every standalone advisory present (%d)" % len(s_contexts),
                  all(c in d_context for c in s_contexts) and bool(d_context) == bool(s_contexts),
                  d_context[:300])
            check(label + " — exit 0", proc.returncode == 0, proc.stderr)

    # A planted violation must FIRE through the dispatcher, not just agree with a silent chain.
    d_blocked, _, _, proc = dispatcher_verdict(payload("Bash", "rm -rf build_out"), env)
    check("rm -rf is blocked through the dispatcher (exit 2, stderr names it)",
          proc.returncode == 2 and "rm" in proc.stderr, proc.stderr)

    p = run([sys.executable, DISPATCH], "not json", env)
    check("malformed payload exits 0 silent", p.returncode == 0 and not p.stdout.strip(), p.stderr)
    p = run([sys.executable, DISPATCH], payload("Read", ""), env)
    check("non-matching tool_name exits 0 silent", p.returncode == 0 and not p.stdout.strip(), p.stderr)

    # Evidence: twelve parallel spawns vs one dispatcher spawn, same benign payload, best of 3.
    text = payload("Bash", "git status --short | head -3")
    best_par, best_one = 1e9, 1e9
    for _ in range(3):
        t0 = time.time()
        procs = [subprocess.Popen([sys.executable, os.path.join(HOOKS_DIR, n)] + a, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=REPO,
                                  text=True, encoding="utf-8") for n, a in SUB_HOOKS]
        for pr in procs:
            pr.communicate(text, timeout=90)
        best_par = min(best_par, time.time() - t0)
        t0 = time.time()
        run([sys.executable, DISPATCH], text, env)
        best_one = min(best_one, time.time() - t0)
    print("timing: 12 parallel spawns %.2fs | dispatcher %.2fs (best of 3)" % (best_par, best_one))

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
