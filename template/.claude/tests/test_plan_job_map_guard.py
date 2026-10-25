#!/usr/bin/env python3
"""Re-runnable proof for hooks/plan_job_map_guard.py.

Cases feed real PostToolUse / PreToolUse payloads on stdin against a planted project (a git repo
with an ignored directory), a planted plan without `## Job map`, and a planted plan outside the project.
Every case asserts on the emitted channel: `permissionDecision: deny` (DENY), no output or `{}`
(ALLOW), `additionalContext` starting `[plan-job-map] advisory` with exit 0 (ADVISE), or
`additionalContext` starting `[plan-job-map] guard failed open` (LOST, the fail-open report). A
non-zero exit or a traceback is CRASH, never ALLOW.

The advisory fires once per recorded plan per session. Must-allow cases run in a fresh session that
records the plan first, so each one would advise if its exclusion failed.

    python3 .claude/tests/test_plan_job_map_guard.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
HOOK = os.path.join(CLAUDE, "hooks", "plan_job_map_guard.py")
VAULT_PLAN_TEXT = "# Vault plan\n\n## Slices\n\n" + "".join("%d. slice %d\n" % (n, n) for n in range(1, 12))

ALLOW, DENY, ADVISE, LOST, CRASH = "allow", "deny", "advise", "lost-check", "crash"
ADVISORY_PREFIX = "[plan-job-map] advisory"
LOST_PREFIX = "[plan-job-map] guard failed open"
JOB_MAP = "\n## Job map\n\n| Slice | Write set | Waits on | Route |\n|---|---|---|---|\n| 1 | src/app.cs | none | job |\n"

TMP = tempfile.mkdtemp(prefix="plan_job_map_guard_")
PROJ = os.path.join(TMP, "proj")
VAULT = os.path.join(TMP, "vault")
ENV = dict(os.environ, CLAUDE_PROJECT_DIR=PROJ, HARNESS_HOOK_STATE_DIR=os.path.join(TMP, "state"),
           PYTHONIOENCODING="utf-8")


def p(*parts):
    return os.path.join(PROJ, *parts)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def run_hook(payload, raw=None):
    stdin = raw if raw is not None else json.dumps(payload)
    r = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True, text=True,
                       timeout=60, env=ENV, encoding="utf-8", errors="replace")
    if r.returncode != 0 or "Traceback" in (r.stderr or ""):
        return CRASH, r.stderr
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    hook_out = json.loads(out).get("hookSpecificOutput") or {}
    if hook_out.get("permissionDecision") == "deny":
        return DENY, hook_out.get("permissionDecisionReason", "")
    text = hook_out.get("additionalContext") or ""
    if text.startswith(ADVISORY_PREFIX):
        return ADVISE, text
    if text:
        return LOST, text
    return ALLOW, ""


def pre(tool, target, sid, key="file_path"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": sid, "cwd": PROJ,
            "tool_input": {key: target, "content": "x"}}


def post(tool, target, sid):
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": sid, "cwd": PROJ,
            "tool_input": {"file_path": target}, "tool_response": {"success": True}}


def via(dispatcher, payload):
    r = subprocess.run([sys.executable, os.path.join(CLAUDE, "hooks", dispatcher)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=90, env=ENV, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout or ""


def posix(path):
    return os.path.abspath(path).replace("\\", "/")


def main():
    results = []

    def check(label, got, want, detail=""):
        ok = got == want
        results.append((label, ok, got, want, detail))

    os.makedirs(PROJ, exist_ok=True)
    subprocess.run(["git", "-C", PROJ, "init", "-q"], check=True)
    write(p(".gitignore"), "ignored/\n")
    plan_a = write(p(".claude", "plans", "plan-a.md"), "# Plan A\n\n## Slices\n\n1. app\n")
    plan_b = write(p(".claude", "plans", "plan-b.md"), "# Plan B\n" + JOB_MAP)
    vault_plan = write(os.path.join(VAULT, "BrainstormingDesigns", "2026-10-22-maddmanager", "plan-mvp1.md"),
                       VAULT_PLAN_TEXT)
    SRC = p("src", "app.cs")
    S = "jm_proof_0001"

    # 1. no recorded plan: a production write passes.
    check("1 no recorded plan: production write allowed", run_hook(pre("Write", SRC, S))[0], ALLOW)

    # 2. record the plan lacking the section.
    check("2 PostToolUse on a plan under .claude/plans records it (no output)",
          run_hook(post("Write", plan_a, S))[0], ALLOW)

    # 3. advise once: recorded plan lacks the section, first production write.
    verdict, reason = run_hook(pre("Write", SRC, S))
    check("3 recorded plan without `## Job map`: first production write advised", verdict, ADVISE)
    check("3 advisory names the failing plan path", posix(plan_a) in reason, True, reason)
    check("3 advisory says to add `## Job map` (plan_file_format §Job map)",
          "## Job map" in reason and "plan_file_format.md" in reason, True, reason)

    # 3b. once per plan: the same plan stays silent for the rest of the session.
    check("3b second production write for the same plan: no advisory", run_hook(pre("Write", SRC, S))[0], ALLOW)
    check("3b second production write via Edit: no advisory",
          run_hook(pre("Edit", SRC, S))[0], ALLOW)

    # 3c. a second mapless plan recorded later gets its own advisory, naming only itself.
    plan_c = write(p(".claude", "plans", "plan-c.md"), "# Plan C\n")
    check("3c PostToolUse on a second mapless plan records it", run_hook(post("Write", plan_c, S))[0], ALLOW)
    verdict, reason = run_hook(pre("Write", SRC, S))
    check("3c second mapless plan: one advisory", verdict, ADVISE)
    check("3c advisory names the second plan", posix(plan_c) in reason, True, reason)
    check("3c advisory does not repeat the first plan", posix(plan_a) in reason, False, reason)
    check("3c the next production write after that advisory: silent",
          run_hook(pre("Write", SRC, S))[0], ALLOW)

    # 3d. the registered matcher is Write|Edit: Edit advises through the dispatcher; the tools the
    # matcher never delivers (MultiEdit, NotebookEdit) are outside the hook's contract and stay silent.
    E = "edit_sid_0009"
    via("post_edit_dispatch.py", post("Write", plan_a, E))
    code, out = via("pre_edit_dispatch.py", pre("Edit", SRC, E))
    check("3d Edit on a production path advised via the dispatcher", code == 0 and ADVISORY_PREFIX in out, True, out)
    for label, tool, target, key, sid in [
        ("3d NotebookEdit is outside the hook's contract", "NotebookEdit", p("nb", "a.ipynb"), "notebook_path",
         "nb_sid_0008"),
        ("3d MultiEdit is outside the hook's contract", "MultiEdit", SRC, "file_path", "multi_sid_0010"),
    ]:
        run_hook(post("Write", plan_a, sid))
        check(label, run_hook(pre(tool, target, sid, key=key))[0], ALLOW)

    # 4. the ignore check decides before the advisory is spent: an ignored write passes; the next
    # production write still advises.
    G = "ignore_sid_0010"
    run_hook(post("Write", plan_a, G))
    check("4 git-ignored production path allowed (ignore check decides)",
          run_hook(pre("Write", p("ignored", "gen.cs"), G))[0], ALLOW)
    check("4 production write after an ignored one still advised", run_hook(pre("Write", SRC, G))[0], ADVISE)

    # 5. must-allow: each case runs in a fresh session that recorded plan_a first, so an
    # exclusion that failed would advise here.
    must_allow = [
        ("5 .claude/hooks path allowed", pre("Write", p(".claude", "hooks", "y.py"), S)),
        ("5 .claude/tests path allowed", pre("Edit", p(".claude", "tests", "t.py"), S)),
        ("5 .claude/scratch path allowed", pre("Write", p(".claude", "scratch", "x.cs"), S)),
        ("5 logs path allowed", pre("Write", p("logs", "run.txt"), S)),
        (".git path allowed", pre("Write", p(".git", "config"), S)),
        ("5 node_modules path allowed", pre("Write", p("node_modules", "pkg", "i.js"), S)),
        ("5 .search-index path allowed", pre("Write", p(".search-index", "idx.cs"), S)),
        ("5 .md file allowed", pre("Write", p("docs", "notes.md"), S)),
        ("5 the plan itself allowed (the fix path)", pre("Edit", plan_a, S)),
        ("5 vault path allowed", pre("Write", os.path.join(VAULT, "other.cs"), S)),
        ("5 out-of-project path allowed", pre("Write", os.path.join(TMP, "elsewhere", "x.cs"), S)),
        ("5 non-write tool allowed", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": S,
                                      "cwd": PROJ, "tool_input": {"command": "ls"}}),
    ]
    for i, (label, payload) in enumerate(must_allow):
        sid = "must_allow_%02d_0011" % i
        run_hook(post("Write", plan_a, sid))
        check(label, run_hook(dict(payload, session_id=sid))[0], ALLOW)

    # 5b. another session's write is silent; the owner session advises (control).
    O = "owner_sid_0012"
    run_hook(post("Write", plan_a, O))
    check("5b another session's write allowed", run_hook(pre("Write", SRC, "other_sid_0002"))[0], ALLOW)
    check("5b owner session's write advised (control)", run_hook(pre("Write", SRC, O))[0], ADVISE)

    # 6. scratch plans are not recorded.
    scratch_plan = write(p(".claude", "scratch", "plan-s.md"), "# scratch\n")
    check("6 PostToolUse on a scratch plan records nothing", run_hook(post("Write", scratch_plan, "scratch_sid"))[0], ALLOW)
    check("6 a session with only a scratch plan: production write allowed",
          run_hook(pre("Write", SRC, "scratch_sid"))[0], ALLOW)

    # 7. vault plan: recorded from outside the repo, advises, silent once the section is added.
    V = "vault_sid_0003"
    check("7 PostToolUse on the vault plan records it", run_hook(post("Write", vault_plan, V))[0], ALLOW)
    verdict, reason = run_hook(pre("Write", SRC, V))
    check("7 vault plan without `## Job map`: production write advised", verdict, ADVISE)
    check("7 advisory names the vault plan path", posix(vault_plan) in reason, True, reason)
    write(vault_plan, open(vault_plan, encoding="utf-8").read() + JOB_MAP)
    check("7 vault plan with `## Job map` added: same write allowed", run_hook(pre("Write", SRC, V))[0], ALLOW)

    # 8. a plan that already has the section never advises.
    F8 = "fixed_sid_0013"
    write(plan_a, open(plan_a, encoding="utf-8").read() + JOB_MAP)
    run_hook(post("Write", plan_a, F8))
    check("8 plan with section present: production write allowed", run_hook(pre("Write", SRC, F8))[0], ALLOW)

    # 9. heading rule: level 1 and a non-matching word do not count; case-insensitive level 3 does.
    H = "heading_sid_0004"
    plan_h = write(p(".claude", "plans", "plan-h.md"), "# Plan H\n\n# Job map\n\n1. app\n")
    run_hook(post("Write", plan_h, H))
    check("9 level-1 `# Job map` does not satisfy the rule", run_hook(pre("Write", SRC, H))[0], ADVISE)
    write(plan_h, "# Plan H\n\n### job MAP\n\n1. app\n")
    H2 = "heading_sid_0014"
    run_hook(post("Write", plan_h, H2))
    check("9 `### job MAP` satisfies the rule: no advisory", run_hook(pre("Write", SRC, H2))[0], ALLOW)

    # 10. deleted plan: a recorded plan that no longer exists does not advise.
    D = "deleted_sid_0005"
    plan_d = write(p(".claude", "plans", "plan-d.md"), "# Plan D\n")
    run_hook(post("Write", plan_d, D))
    os.remove(plan_d)
    check("10 recorded plan deleted since: production write allowed", run_hook(pre("Write", SRC, D))[0], ALLOW)

    # 11. bounded state: 40 recorded plans name at most 32 in the advisory.
    M = "many_sid_0006"
    for i in range(40):
        run_hook(post("Write", write(p(".claude", "plans", "plan-many-%02d.md" % i), "# M\n"), M))
    verdict, reason = run_hook(pre("Write", SRC, M))
    named = reason.count("plan-many-")
    check("11 advisory with 40 recorded plans is an advisory", verdict, ADVISE)
    check("11 advisory names at most 32 plans (bounded state)", 1 <= named <= 32, True, "named=%d" % named)
    check("11 the next write after that advisory: silent", run_hook(pre("Write", SRC, M))[0], ALLOW)

    # 12. fail posture: a recorded path that cannot be read is a lost check, not a block.
    F = "failopen_sid_0007"
    bad = p(".claude", "plans", "plan-dir.md")
    os.makedirs(bad, exist_ok=True)
    run_hook(post("Write", bad, F))
    verdict, text = run_hook(pre("Write", SRC, F))
    check("12 unreadable recorded plan: allowed with a lost-check report", verdict, LOST)
    check("12 lost-check report names the failed open", LOST_PREFIX in text, True, text)

    # 13. malformed stdin and a non-object payload do not crash.
    check("13 non-JSON stdin does not crash", run_hook(None, raw="not json at all")[0] in (ALLOW, LOST), True)
    check("13 non-object payload does not crash", run_hook(None, raw="[1, 2]")[0] in (ALLOW, LOST), True)

    # 14. wiring: the edit dispatchers run the guard as a member (process()), with no settings entry of its own.
    W = "dispatch_sid_0008"
    plan_w = write(p(".claude", "plans", "plan-dispatch.md"), "# Plan\n\n## Slices\n\nS1, S2\n")

    via("post_edit_dispatch.py", post("Write", plan_w, W))
    code, out = via("pre_edit_dispatch.py", pre("Write", SRC, W))
    check("14 pre_edit_dispatch carries the member's advisory", code == 0 and ADVISORY_PREFIX in out, True, out)
    check("14 advisory via the dispatcher names the plan", posix(plan_w) in out, True, out)
    code, out = via("pre_edit_dispatch.py", pre("Write", SRC, W))
    check("14 second write via the dispatcher: no advisory", ADVISORY_PREFIX in out, False, out)

    shutil.rmtree(TMP, ignore_errors=True)

    passed = 0
    for label, ok, got, want, detail in results:
        if ok:
            passed += 1
        print(f"{'PASS' if ok else 'FAIL'}: {label}" + ("" if ok else f" (got {got!r}, want {want!r}) {detail[:300]}"))
    print(f"\n{passed}/{len(results)} passed")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
