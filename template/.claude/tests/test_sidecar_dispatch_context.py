"""Re-runnable proof for hooks/sidecar_dispatch_context.py.

Two duties. PREFLIGHT: a launch whose launcher `--check` exits non-zero is DENIED with the
refusal text before the command runs; `-A` is passed through; a passing check is not denied.
CONTEXT: `reference/sidecar_dispatch.md` is injected on a sidecar LAUNCH, once per session, and
again after a compaction dropped it. A mere mention of the launcher is not a launch.

Fake launchers stand in for the real ones so the proof spends nothing and needs no network.
State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_sidecar_dispatch_context.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
REPO = os.path.abspath(os.path.join(HERE, ".."))
HOOK = os.path.join(HOOKS, "sidecar_dispatch_context.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")

SID = "sdc00001"

OK_LAUNCHER = "#!/usr/bin/env bash\n[ \"$1\" = --check ] && { echo OK; exit 0; }\necho dispatched\n"
REFUSING_LAUNCHER = ("#!/usr/bin/env bash\nif [ \"$1\" = --check ]; then\n"
                     "  case \" $* \" in *\" -A \"*) echo 'OK (-A)'; exit 0;; esac\n"
                     "  echo '[sidecar] REFUSING fake (fake-model): band Hot, above the Ahead ceiling.' >&2; exit 8\nfi\n"
                     "echo dispatched\n")


def checked_run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in (0, 2) or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def run(command, env, session=SID):
    r = checked_run([sys.executable, HOOK],
                    input=json.dumps({"tool_name": "Bash", "session_id": session,
                                      "tool_input": {"command": command}}),
                    capture_output=True, text=True, timeout=90, env=env)
    out = (r.stdout or "").strip()
    if not out:
        return {}
    return json.loads(out).get("hookSpecificOutput") or {}


def context(command, env, session=SID):
    return run(command, env, session).get("additionalContext", "")


def precompact(env, session=SID):
    checked_run([sys.executable, PRECOMPACT],
                input=json.dumps({"session_id": session, "trigger": "auto"}),
                capture_output=True, text=True, timeout=60, env=env)


def main():
    tmp = tempfile.mkdtemp(prefix="sdcstate_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8",
               CLAUDE_PROJECT_DIR=os.path.dirname(REPO))
    env.pop("HARNESS_SIDECAR_PREFLIGHT", None)
    fdir = tempfile.mkdtemp(prefix="sdcfake_").replace("\\", "/")
    ok = fdir + "/ok_sidecar.sh"
    refusing = fdir + "/hot_sidecar.sh"
    for path, body in ((ok, OK_LAUNCHER), (refusing, REFUSING_LAUNCHER)):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    launch = "bash %s -p prompt.md -m flash" % ok
    cases = []
    for label, source in (
        ("the proof helper rejects an abnormal exit", "raise SystemExit(7)"),
        ("the proof helper rejects a traceback even with exit zero",
         "print('Traceback (most recent call last):')"),
    ):
        try:
            checked_run([sys.executable, "-c", source], capture_output=True, text=True, timeout=60)
            rejected = False
        except RuntimeError:
            rejected = True
        cases.append((label, rejected))

    # --- preflight (the guard bites)
    denied = run("bash %s -m fake -f brief.md -P run.jsonl" % refusing, env, session="sdc00010")
    cases.append(("a launcher whose --check exits 8 is DENIED before dispatch",
                  denied.get("permissionDecision") == "deny"
                  and "REFUSING fake" in denied.get("permissionDecisionReason", "")
                  and "exit 8" in denied.get("permissionDecisionReason", "")))
    with_a = run("bash %s -A -m fake -f brief.md" % refusing, env, session="sdc00011")
    cases.append(("-A is passed through to --check and lifts the refusal",
                  with_a.get("permissionDecision") is None))
    cases.append(("a passing --check is never denied",
                  run(launch, env, session="sdc00012").get("permissionDecision") is None))
    missing_registry = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; sys.modules['model_registry']=None; "
         "runpy.run_path(sys.argv[1], run_name='__main__')", HOOK],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}),
        capture_output=True, text=True, timeout=60, env=env,
    )
    cases.append(("a missing registry does not crash unrelated Bash hooks",
                  missing_registry.returncode == 0 and not missing_registry.stderr.strip()))

    # The Python fan-out hides its child launchers from auto mode. The existing sidecar gate
    # approves the validated wrapper; each child launcher still enforces its own budget gate.
    fanout_root = os.path.join(tmp, "project")
    scratch = os.path.join(fanout_root, ".claude", "scratch")
    os.makedirs(os.path.join(fanout_root, ".claude", "tools"))
    os.makedirs(os.path.join(fanout_root, ".claude", "reference"))
    os.makedirs(scratch)
    with open(os.path.join(fanout_root, ".claude", "tools", "sidecar_fanout.py"),
              "w", encoding="utf-8") as fh:
        fh.write("# fixture\n")
    with open(os.path.join(fanout_root, ".claude", "reference", "sidecar_dispatch.md"),
              "w", encoding="utf-8") as fh:
        fh.write("sidecar reference\n")
    prompt = os.path.join(scratch, "prompt.md")
    with open(prompt, "w", encoding="utf-8") as fh:
        fh.write("review\n")
    jobs = os.path.join(scratch, "jobs.json")
    base_job = {"label": "audit", "alias": "muse", "promptFile": prompt,
                "shape": "review", "workdir": ".", "transport": "opencode"}

    def write_jobs(**changes):
        job = dict(base_job)
        job.update(changes)
        with open(jobs, "w", encoding="utf-8") as fh:
            json.dump([job], fh)

    write_jobs()
    fanout_env = dict(env, CLAUDE_PROJECT_DIR=fanout_root)
    fanout_cmd = ("python3 .claude/tools/sidecar_fanout.py .claude/scratch/jobs.json "
                  "--out-dir .claude/scratch/results")
    fanout = run(fanout_cmd, fanout_env, session="sdc00013")
    cases.append(("the existing sidecar gate approves a validated fan-out wrapper",
                  fanout.get("permissionDecision") == "allow"
                  and "budget and quota gates" in fanout.get("permissionDecisionReason", "")))
    cases.append(("a relative interpreter path falls through",
                  run("./python3" + fanout_cmd[len("python3"):], fanout_env,
                      session="sdc00025") == {}))
    cases.append(("an arbitrary absolute interpreter path falls through",
                  run("C:/untrusted/python3" + fanout_cmd[len("python3"):], fanout_env,
                      session="sdc00026") == {}))
    write_jobs(alias="opus", transport="anthropic")
    cases.append(("plan-quota jobs use the same approval path",
                  run(fanout_cmd, fanout_env, session="sdc00014").get("permissionDecision") == "allow"))
    write_jobs(shape="author")
    cases.append(("author jobs retain the child auto-permission gate",
                  run(fanout_cmd, fanout_env, session="sdc00015").get("permissionDecision") == "allow"))
    write_jobs(effort="turbo")
    cases.append(("unknown effort pins fall through",
                  run(fanout_cmd, fanout_env, session="sdc00023") == {}))
    write_jobs(disclosure="everything")
    cases.append(("unknown disclosure tiers fall through",
                  run(fanout_cmd, fanout_env, session="sdc00024") == {}))
    write_jobs()
    cases.append(("--authorize falls through instead of bypassing a budget gate",
                  run(fanout_cmd + " --authorize", fanout_env, session="sdc00016") == {}))
    write_jobs(extraArgs=[])
    cases.append(("extra launcher arguments fall through",
                  run(fanout_cmd, fanout_env, session="sdc00017") == {}))
    outside = os.path.join(tmp, "outside.md")
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("outside\n")
    write_jobs(contextFiles=[outside])
    cases.append(("inputs outside the project fall through",
                  run(fanout_cmd, fanout_env, session="sdc00018") == {}))
    write_jobs()
    cases.append(("outputs outside scratch fall through",
                  run(fanout_cmd.replace(".claude/scratch/results", "results"),
                      fanout_env, session="sdc00019") == {}))
    cases.append(("fan-out widths below the safe range fall through",
                  run(fanout_cmd + " --max-parallel 0", fanout_env, session="sdc00021") == {}))
    cases.append(("fan-out widths above the safe range fall through",
                  run(fanout_cmd + " --max-parallel 5", fanout_env, session="sdc00022") == {}))
    cases.append(("shell compounds fall through",
                  run(fanout_cmd + " && git status", fanout_env, session="sdc00020") == {}))

    # --- context injection
    out = context(launch, env)
    cases.append(("the first launch injects the reference",
                  out.startswith("[sidecar dispatch") and len(out) > 200))
    cases.append(("the second launch is silent", context(launch, env) == ""))
    precompact(env)
    cases.append(("a compaction re-arms the injection",
                  context(launch, env).startswith("[sidecar dispatch")))

    # Negatives — the guard matches the invocation, never the noun.
    cases.append(("--check is not a launch", run("bash %s --check" % ok, env, session="sdc00002") == {}))
    cases.append(("grepping the launcher is not a launch",
                  run("grep -n 'model' %s" % ok, env, session="sdc00003") == {}))
    cases.append(("an unrelated command is silent", run("git status", env, session="sdc00004") == {}))
    cases.append(("a launcher named as an ARGUMENT is not a launch (--launcher x.sh --tasks)",
                  run("python3 grid.py run zz --launcher %s --tasks t4" % refusing, env, session="sdc00005") == {}))

    failures = [label for label, ok_ in cases if not ok_]
    for label, ok_ in cases:
        print("%-4s %s" % ("ok" if ok_ else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
