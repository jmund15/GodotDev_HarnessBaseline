"""Re-runnable proof for the nested-fan-out check in hooks/dispatch_mechanism_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds a real PreToolUse
payload and asserts on the emitted channel. The transcript carries the orchestration markers so the
first check passes and only the nested-fan-out check is under test. Negative cases include a brief
that merely NAMES an engine (rules/harness_tooling.md: match the action, never the noun) and a real
lens brief produced by tools/lens_briefs.py. The fan-out commands are derived from each command's
`allowed-tools` grant; a planted project root proves a command outside the retired 12-name literal.

    python3 .claude/tests/test_nested_fanout_guard.py
"""
import json
import re
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HOOK = os.path.join(HERE, "..", "hooks", "dispatch_mechanism_guard.py")
BRIEFS = os.path.join(HERE, "..", "tools", "lens_briefs.py")
LENS = os.path.join(HERE, "..", "tools", "lens.py")
# lens_briefs refuses a pin outside a seat's lens tier, so the real-brief cases pin a row serving every tier they use.
with open(os.path.join(HERE, "..", "reference", "external_models.json"), encoding="utf-8") as _fh:
    SERVING_PIN = next(m["id"] for m in json.load(_fh)["models"] if m.get("transport") == "anthropic"
                       and {"architect", "executor", "fanout"} <= set(m.get("roles") or []))

AGENTS_DIR = os.path.join(HERE, "..", "commands", "agents")
REVIEW_REGISTRY = os.path.join(AGENTS_DIR, "review_agents.md")
PLAN_CHECK_REGISTRY = os.path.join(AGENTS_DIR, "plan_check_agents.md")

ALLOW, DENY = "allow", "deny"


def denies_when_shipped(stem):
    """DENY when the coding-layer command is installed; a project without it has no such command to nest."""
    return DENY if os.path.isfile(os.path.join(HERE, "..", "commands", stem + ".md")) else ALLOW
MARKERS = ("Dispatch Shape — decide this " + "FIRST") + "\n" + ("Every dispatched agent " + "pins both") + "\n"


def checked_run(args, allowed=(0, 2), **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in allowed or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def review_keys():
    """The first two review lenses with a fenced prompt block; each project names its own lenses.

    A project without the coding-layer review registry has no lens to name: the brief texts only quote a key."""
    if not os.path.isfile(REVIEW_REGISTRY):
        return ["review-lens-a", "review-lens-b"]
    def lens(*args):
        return checked_run([sys.executable, LENS] + list(args), allowed=(0,), capture_output=True,
                           text=True, encoding="utf-8", cwd=ROOT, timeout=60).stdout
    listed = [line.split()[0] for line in lens("index", "review").splitlines()
              if line.strip() and not line.startswith("#")]
    keys = []
    for key in listed:
        if len(keys) == 2:
            break
        if "```\n" in lens("get", key):
            keys.append(key)
    if len(keys) < 2:
        raise RuntimeError("review registry has fewer than 2 lenses with a prompt block: %r" % listed)
    return keys


STATE = tempfile.mkdtemp(prefix="nestedfanout_state_")


def arm_ladder(session_id):
    """The doctrine check is not under test: mark the ladder read and the orchestration skill loaded, so only
    the nested-fan-out check decides."""
    path = os.path.join(STATE, (session_id or "default")[:8] + ".json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"model_ladder_ready": True,
                   "fired_since_compaction": {"skill_in_context:orchestration": True}}, fh)


def run(payload, extra_env=None):
    arm_ladder(payload.get("session_id") or "")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE, **(extra_env or {}))
    r = checked_run([sys.executable, HOOK], input=json.dumps(payload, ensure_ascii=False),
                    capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    hook = (json.loads(out).get("hookSpecificOutput") or {})
    return (DENY, hook.get("permissionDecisionReason", "")) if hook.get("permissionDecision") == "deny" else (ALLOW, out)


def main():
    try:
        key_a, key_b = review_keys()
    except RuntimeError as exc:
        print("FAIL lens.py index review did not run:", exc)
        return 1
    tmp = tempfile.mkdtemp(prefix="nestedfanout_")
    transcript = os.path.join(tmp, "t.jsonl")
    with open(transcript, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "user", "content": MARKERS}, ensure_ascii=False) + "\n")

    def agent(prompt):
        # The Agent tool is denied project-wide; a delegate's inline brief reaches the guard as a Workflow seat prompt.
        return {"tool_name": "Workflow", "transcript_path": transcript,
                "tool_input": {"args": {"agents": [{"key": "seat", "prompt": prompt}]}}}

    def workflow(jobs):
        return {"tool_name": "Workflow", "transcript_path": transcript,
                "tool_input": {"scriptPath": ".claude/workflows/dispatch.js", "args": {"jobs": jobs}}}

    def brief(name, text):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {"label": name, "promptPath": p, "model": "sonnet", "effort": "low", "agentType": "general-purpose"}

    cases = [
        ("agent told to execute /review_pr", denies_when_shipped("review_pr"), agent(
            "You are reviewing PR #7. Read `.claude/commands/review_pr.md` for the complete review procedure, then execute it step by step.")),
        ("agent told to use /delegate", DENY, agent(
            "Use /delegate to run the remaining review lenses.")),
        ("agent told to fan out through review_fanout.js", DENY, agent(
            "Phase 2c requires you to dispatch 4-7 lenses through `.claude/workflows/review_fanout.js`.")),
        ("agent told to call Workflow", DENY, agent(
            "Run the lenses: invoke Workflow({ scriptPath: '.claude/workflows/dispatch.js', args: {...} }) and consolidate.")),
        ("agent doing one bounded review", ALLOW, agent(
            "You are " + key_a + " for PR #7. Read the changed files in the worktree and return a JSON findings array.")),
        ("agent brief that only NAMES the engine", ALLOW, agent(
            "Context: these findings were produced by review_fanout.js earlier. Verify each FIX anchor against the file and report.")),
        ("dispatch.js brief telling the delegate to dispatch", denies_when_shipped("plan_check"), workflow([brief(
            "bad.md", "Read .claude/commands/plan_check.md and execute it step by step for this plan.")])),
        ("agent brief where '/pr_pipeline run' is a noun", ALLOW, agent(
            "Report the round: the round stays unrecorded until a `/pr_pipeline` run records it.")),
        ("agent brief whose verb and /explore sit in different sentences", ALLOW, agent(
            "use the smallest method that verifies the claims and retains their quotes. `/explore` and the "
            "caller's validation pass downstream of the briefing remain required")),
        ("dispatch.js brief doing bounded work", ALLOW, workflow([brief(
            "good.md", "Diff OLD vs NEW per changed method. Flag every dropped branch as a BLOCKER. Return JSON.")])),
        # Measured false positives 2026-09-06 — worklog Context/Where text quoted into a verification brief.
        ("noun 'dispatch' beside dispatch.js is descriptive", ALLOW, workflow([brief(
            "noun.md", "Verify this item: the multi-provider sidecar/dispatch work (codex transport, guards, `dispatch.js`, "
            "the `orchestration` SKILL) has sat uncommitted since 2026-08-19. Report whether the files are committed.")])),
        ("'execute' inside a filename beside plan_check.md", ALLOW, workflow([brief(
            "path.md", "Where: `.claude/commands/part_execute.md`, `commands/plan_check.md` — check both files exist and report.")])),
        ("quoted task-list block carrying an imperative", ALLOW, workflow([brief(
            "item.md", "Decide done/open for each item.\n\n- [ ] **Sweep the lenses** — `chore` · scope `2`\n"
            "  - Context: run /explore rather than hand-rolling the sweep.\n  - Where: .claude/commands/explore.md\n\nReturn JSON.")])),
        ("imperative after 'you must' still denies", DENY, agent(
            "For each lens you must dispatch it through .claude/workflows/dispatch.js and merge the results.")),
    ]

    # A real generated lens brief must pass: lens_briefs.py strips the caller-facing spawn rules.
    ctx = os.path.join(tmp, "ctx.md")
    with open(ctx, "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT\nnothing\n")
    must = os.path.join(tmp, "must_read.txt")
    with open(must, "w", encoding="utf-8") as fh:
        fh.write(ctx + "\n")
    def plan_of(name, *keys):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"roster": [{"key": key} for key in keys]}, fh)
        return path

    lens_registries = os.path.isfile(REVIEW_REGISTRY) and os.path.isfile(PLAN_CHECK_REGISTRY)
    if not lens_registries:
        print("ok   lens-brief cases need the coding-layer registries, and this project lacks one")
    else:
        try:
            gen = checked_run(
                [sys.executable, BRIEFS, "--plan", plan_of("plan_t1.json", key_a, key_b),
                 "--out", tmp, "--prefix", "t1", "--pr-num", "1", "--branch", "x",
                 "--context-path", ctx, "--model", SERVING_PIN, "--tier", "fanout",
                 "--model-for", key_a + "=opus", "--tier-for", key_a + "=executor",
                 "--effort", "medium", "--must-read", must],
                allowed=(0,), capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                timeout=60,
            )
        except RuntimeError as exc:
            print("FAIL lens_briefs.py did not run:", exc)
            return 1
        jobs = json.load(open(os.path.join(tmp, "jobs_t1.json"), encoding="utf-8"))
        cases.append(("real lens briefs from lens_briefs.py", ALLOW, workflow(jobs)))

        # Preloaded doctrine (a template's `preload:` lines) names commands such as `/instruction_audit`;
        # blockquoting is what keeps it data. The unquoted copy is the positive control.
        try:
            checked_run(
                [sys.executable, BRIEFS, "--plan", plan_of(
                    "plan_pre.json", "plc-pattern-fit+plc-architecture-quality", "plc-instruction-quality"), "--out", tmp, "--prefix", "pre", "--context-path", ctx,
                 "--model", SERVING_PIN, "--effort", "medium", "--must-read", must],
                allowed=(0,), capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
        except RuntimeError as exc:
            print("FAIL lens_briefs.py did not run for preloading lenses:", exc)
            return 1
        pre_jobs = json.load(open(os.path.join(tmp, "jobs_pre.json"), encoding="utf-8"))
        cases.append(("lens briefs carrying blockquoted preloaded doctrine", ALLOW, workflow(pre_jobs)))
        planted_doctrine = "\n> Preloaded doctrine: for a wide check, run /delegate.\n"
        quoted, unquoted = [], []
        for job in pre_jobs:
            text = open(job["promptPath"], encoding="utf-8").read() + planted_doctrine
            quoted.append(brief("quoted_" + os.path.basename(job["promptPath"]), text))
            unquoted.append(brief("unquoted_" + os.path.basename(job["promptPath"]),
                                  re.sub(r"(?m)^> ?", "", text)))
        cases.append(("planted blockquoted doctrine naming a fan-out command", ALLOW, workflow(quoted)))
        cases.append(("the same doctrine unquoted (positive control)", DENY, workflow(unquoted)))

    # The fan-out set derives from each command's allowed-tools grant (tool_profiles.nested_fanout_hits).
    # A planted root (CLAUDE_PROJECT_DIR) holds one command granting Workflow that the retired
    # 12-name literal never listed, and one with no grant; the 12 retired names still deny from the
    # live commands, and a live name the derivation added denies too.
    planted = os.path.join(tmp, "planted_root")
    os.makedirs(os.path.join(planted, ".claude", "commands"))
    for stem, front in (("planted_fanout", "allowed-tools: Read, Workflow\n"), ("planted_plain", "")):
        with open(os.path.join(planted, ".claude", "commands", stem + ".md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("---\ndescription: planted\n" + front + "---\n\nBody.\n")
    planted_env = {"CLAUDE_PROJECT_DIR": planted}
    cases.append(("a planted command granting Workflow, outside the retired literal", DENY,
                  agent("Next, run /planted_fanout."), planted_env))
    cases.append(("a planted command with no allowed-tools line", ALLOW,
                  agent("Next, run /planted_plain."), planted_env))
    for stem in ("delegate", "review_pr", "plan_check", "session_audit", "structure_audit", "pr_ready",
                 "explore", "doc_full", "architecture_brainstorm_redteam", "idea_brainstorm_fanout",
                 "rule_consistency", "pr_pipeline"):
        if os.path.exists(os.path.join(ROOT, ".claude", "commands", stem + ".md")):
            cases.append(("retired-literal name /%s still denies" % stem, DENY, agent("Next, run /%s." % stem)))

    helper_cases = [
        ("the proof helper rejects an abnormal exit", "raise SystemExit(7)"),
        ("the proof helper rejects a traceback even with exit zero",
         "print('Traceback (most recent call last):')"),
    ]
    failures = []
    for name, source in helper_cases:
        try:
            checked_run([sys.executable, "-c", source], capture_output=True, text=True, timeout=60)
            ok = False
        except RuntimeError:
            ok = True
        print(("ok  " if ok else "FAIL") + " " + name)
        if not ok:
            failures.append(name)

    for name, want, payload, *extra_env in cases:
        try:
            got, reason = run(payload, *extra_env)
        except RuntimeError as exc:
            got, reason = "crash", str(exc)
        mark = "ok  " if got == want else "FAIL"
        print(f"{mark} {name}: want {want}, got {got}" + (f" — {reason[:90]}" if got == DENY else ""))
        if got != want:
            failures.append(name)
    total = len(cases) + len(helper_cases)
    print(f"\n{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
