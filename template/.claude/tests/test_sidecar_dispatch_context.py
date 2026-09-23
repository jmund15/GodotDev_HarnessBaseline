"""Re-runnable proof for hooks/sidecar_dispatch_context.py.

Three duties. PREFLIGHT: a launch whose launcher `--check` exits non-zero is DENIED with the
refusal text before the command runs; `-A` is passed through; a passing check is not denied.
BACKGROUND ALLOW (Design §1/§2, this revision — replaces the required-`-X` deny landed in
30640c097): a backgrounded direct launch of a launcher that calls `sc_reexec_snapshot "$@"` is
never denied — the lib itself writes `<record>.exit`/`.out` on every exit path now, foreground or
not. With `-R` it carries one advisory line pointing at that record; without `-R` it carries none.
A launcher that does not call `sc_reexec_snapshot`, or a mere mention, is not judged; a
backgrounded fan-out is not denied and carries its own advisory (fan-out children run detached).
CONTEXT: `reference/sidecar_dispatch.md` is injected on a sidecar LAUNCH, once per session, and
again after a compaction dropped it. A mere mention of the launcher is not a launch.

Fake launchers stand in for the real ones so the proof spends nothing and needs no network.
State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.
SIDECAR_DISPATCH_HOOK overrides the hook path under test (used to capture a RED record against
the pre-change hook; unset, it targets the live `hooks/sidecar_dispatch_context.py`).

    python3 .claude/tests/test_sidecar_dispatch_context.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
REPO = os.path.abspath(os.path.join(HERE, ".."))
HOOK = os.environ.get("SIDECAR_DISPATCH_HOOK") or os.path.join(HOOKS, "sidecar_dispatch_context.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")
spec = importlib.util.spec_from_file_location("sidecar_dispatch_context_probe", HOOK)
sidecar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecar)
PRE_BASH_DISPATCH = os.path.join(HOOKS, "pre_bash_dispatch.py")

SID = "sdc00001"

# Mirrors hooks/sidecar_dispatch_context.py's FANOUT_BACKGROUND_ADVISORY — kept as a literal so the
# proof fails loudly if the two texts drift apart, rather than importing and trivially matching.
FANOUT_BACKGROUND_ADVISORY = (
    "fan-out children run detached; a killed notice on the fan-out does not stop them: read each "
    "child's record .exit."
)

OK_LAUNCHER = "#!/usr/bin/env bash\n[ \"$1\" = --check ] && { echo OK; exit 0; }\necho dispatched\n"
REFUSING_LAUNCHER = ("#!/usr/bin/env bash\nif [ \"$1\" = --check ]; then\n"
                     "  case \" $* \" in *\" -A \"*) echo 'OK (-A)'; exit 0;; esac\n"
                     "  echo '[sidecar] REFUSING fake (fake-model): band Hot, above the Ahead ceiling.' >&2; exit 8\nfi\n"
                     "echo dispatched\n")


def reexec_launcher(marker_path):
    """A fake launcher shaped like a D1 lib launcher: it calls `sc_reexec_snapshot "$@"` and its
    `--check` path writes `marker_path`, so a denied case can prove preflight never ran."""
    marker = marker_path.replace("\\", "/")
    return ("#!/usr/bin/env bash\n"
            "sc_reexec_snapshot \"$@\"   # run from a snapshot copy; see lib\n"
            "if [ \"$1\" = --check ]; then printf x > \"%s\"; echo OK; exit 0; fi\n"
            "echo dispatched\n" % marker)


def checked_run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in (0, 2) or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def arm_ladder(env, session):
    state_dir = env["HARNESS_HOOK_STATE_DIR"]
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, (session or "default")[:8] + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        state = {}
    state["model_ladder_ready"] = True
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh)


def run(command, env, session=SID, run_in_background=False, ladder=True, cwd=None):
    if ladder:
        arm_ladder(env, session)
    tool_input = {"command": command}
    if run_in_background:
        tool_input["run_in_background"] = True
    payload = {"tool_name": "Bash", "session_id": session, "tool_input": tool_input}
    if cwd:
        payload["cwd"] = cwd
    r = checked_run([sys.executable, HOOK], input=json.dumps(payload),
                    capture_output=True, text=True, timeout=90, env=env, cwd=cwd)
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
    project = os.path.join(tmp, "project")
    fdir = os.path.join(project, ".claude", "scripts").replace("\\", "/")
    reference_dir = os.path.join(project, ".claude", "reference")
    os.makedirs(fdir)
    os.makedirs(reference_dir)
    with open(os.path.join(REPO, "reference", "sidecar_dispatch.md"), encoding="utf-8") as source:
        reference = source.read()
    with open(os.path.join(reference_dir, "sidecar_dispatch.md"), "w", encoding="utf-8") as target:
        target.write(reference)
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8",
               CLAUDE_PROJECT_DIR=project)
    env.pop("HARNESS_SIDECAR_PREFLIGHT", None)
    ok = fdir + "/ok_sidecar.sh"
    refusing = fdir + "/hot_sidecar.sh"
    for path, body in ((ok, OK_LAUNCHER), (refusing, REFUSING_LAUNCHER)):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    launch = "bash %s -p prompt.md -m flash" % ok
    outside = os.path.join(tmp, "outside_sidecar.sh").replace("\\", "/")
    outside_marker = os.path.join(tmp, "outside-ran").replace("\\", "/")
    with open(outside, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\nif [ \"$1\" = --check ]; then printf x > \"%s\"; exit 8; fi\n"
                 % outside_marker)
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
    spent = fdir + "/spent_sidecar.sh"
    with open(spent, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\nif [ \"$1\" = --check ]; then\n"
                 "  echo '[sidecar] REFUSING codex: provider usage limit reached.' >&2; exit 10\nfi\n"
                 "echo dispatched\n")
    spent_result = run("bash %s -A -m fake -f brief.md" % spent, env, session="sdc00913")
    spent_reason = spent_result.get("permissionDecisionReason", "")
    cases.append(("an exhausted provider (exit 10) is DENIED even with -A, and the refusal never offers -A",
                  spent_result.get("permissionDecision") == "deny"
                  and "exit 10" in spent_reason and "pass -A" not in spent_reason
                  and "-A does not lift" in spent_reason))
    peak = fdir + "/peak_sidecar.sh"
    with open(peak, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\nif [ \"$1\" = --check ]; then\n"
                 "  case \" $* \" in *\" -W \"*) echo 'OK (-W)'; exit 0;; esac\n"
                 "  echo '[sidecar] REFUSING fake: peak pricing window until 04:00 UTC.' >&2; exit 11\nfi\n"
                 "echo dispatched\n")
    peak_result = run("bash %s -A -m fake -f brief.md" % peak, env, session="sdc00941")
    peak_reason = peak_result.get("permissionDecisionReason", "")
    cases.append(("a peak-window refusal (exit 11) is DENIED even with -A, never offers -A, and names -W as the user's call",
                  peak_result.get("permissionDecision") == "deny"
                  and "exit 11" in peak_reason and "pass -A" not in peak_reason
                  and "-W" in peak_reason and "user" in peak_reason))
    cases.append(("-W is passed through to --check and lifts a peak-window refusal",
                  run("bash %s -W -m fake -f brief.md" % peak, env, session="sdc00942").get("permissionDecision") is None))
    cases.append(("a passing --check is never denied",
                  run(launch, env, session="sdc00012").get("permissionDecision") is None))
    no_ladder = run(launch, env, session="sdc00950", ladder=False)
    cases.append(("a direct dispatch without a fresh ladder Read is denied",
                  no_ladder.get("permissionDecision") == "deny"
                  and "model_ladder_evidence.md" in no_ladder.get("permissionDecisionReason", "")))
    # A session working inside a worktree: the gate accepts only the primary checkout's ladder
    # (CLAUDE_PROJECT_DIR), so the denial must name that file absolutely.
    worktree = os.path.join(project, ".claude", "worktrees", "wt1")
    os.makedirs(os.path.join(worktree, ".claude", "reference"), exist_ok=True)
    primary_ladder = os.path.realpath(
        os.path.join(project, ".claude", "reference", "model_ladder_evidence.md"))
    worktree_ladder = os.path.realpath(
        os.path.join(worktree, ".claude", "reference", "model_ladder_evidence.md"))
    wt_denial = run(launch, env, session="sdc00952", ladder=False, cwd=worktree)
    wt_reason = wt_denial.get("permissionDecisionReason", "")
    cases.append(("a worktree-cwd sidecar denial names the primary checkout's ladder absolutely",
                  wt_denial.get("permissionDecision") == "deny" and primary_ladder in wt_reason))
    cases.append(("a worktree-cwd sidecar denial never names the worktree's stale copy",
                  wt_denial.get("permissionDecision") == "deny" and worktree_ladder not in wt_reason))
    first = run(launch, env, session="sdc00951")
    second = run(launch, env, session="sdc00951", ladder=False)
    cases.append(("one ladder Read authorizes later sidecar dispatches without a re-read",
                  first.get("permissionDecision") is None
                  and second.get("permissionDecision") is None))
    with patch.object(sidecar, "_git_bash", return_value="bash"), patch.object(
        sidecar.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(["bash", ok, "--check"], 45),
    ):
        timeout_refusal = sidecar.preflight(launch, project)
    cases.append(("E7: a timed-out preflight is refused instead of treated as success",
                  bool(timeout_refusal) and "timed out" in timeout_refusal.lower()))
    outside_result = run("bash %s -m fake -f brief.md" % outside, env, session="sdc00027")
    cases.append(("an outside launcher is never executed before approval",
                  outside_result.get("permissionDecision") is None
                  and not os.path.exists(outside_marker)))
    missing_registry = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; sys.modules['model_registry']=None; "
         "runpy.run_path(sys.argv[1], run_name='__main__')", HOOK],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}),
        capture_output=True, text=True, timeout=60, env=env,
    )
    cases.append(("a missing registry does not crash unrelated Bash hooks",
                  missing_registry.returncode == 0 and not missing_registry.stderr.strip()))
    for label, payload in (
        ("a non-object payload does not crash the hook", []),
        ("a non-object tool_input does not crash the hook",
         {"tool_name": "Bash", "tool_input": "not-an-object"}),
    ):
        malformed = subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload),
            capture_output=True, text=True, timeout=60, env=env,
        )
        cases.append((label, malformed.returncode == 0
                      and not malformed.stderr.strip()
                      and not malformed.stdout.strip()))

    # The Python fan-out hides its child launchers from auto mode. The existing sidecar gate
    # approves the validated wrapper; each child launcher still enforces its own budget gate.
    fanout_root = os.path.join(tmp, "fanout-project")
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
    # Allow-path negatives: _parse_fanout_command is the validator, not a launch parser, and these
    # pin it through the sidecar_argv refactor.
    cases.append(("a $(...) substitution in the fan-out command falls through",
                  run(fanout_cmd + " --max-parallel $(echo 2)", fanout_env, session="sdc00060") == {}))
    cases.append(("a backtick in the fan-out command falls through",
                  run(fanout_cmd + " --max-parallel `echo 2`", fanout_env, session="sdc00061") == {}))
    cases.append(("a py interpreter falls through",
                  run("py" + fanout_cmd[len("python3"):], fanout_env, session="sdc00062") == {}))
    cases.append(("a python3.11 interpreter falls through",
                  run("python3.11" + fanout_cmd[len("python3"):], fanout_env, session="sdc00063") == {}))
    outside_jobs = os.path.join(fanout_root, ".claude", "jobs.json")
    with open(outside_jobs, "w", encoding="utf-8") as fh:
        json.dump([base_job], fh)
    cases.append(("a jobs file outside scratch falls through",
                  run(fanout_cmd.replace(".claude/scratch/jobs.json", ".claude/jobs.json"),
                      fanout_env, session="sdc00064") == {}))
    linked_jobs = os.path.join(scratch, "linked.json")
    linked_cmd = fanout_cmd.replace("jobs.json", "linked.json")
    try:
        os.symlink(jobs, linked_jobs)
    except OSError:
        linked_jobs = None
    if linked_jobs:
        cases.append(("a symlinked jobs file falls through",
                      run(linked_cmd, fanout_env, session="sdc00065") == {}))
    else:
        # No symlink privilege (Windows without developer mode): the link path itself reports
        # as a symlink. This covers the component walk only; a real link (Linux CI, developer
        # mode) also proves the validator never resolves through the link to its target.
        from pathlib import Path as _Path
        with open(linked_jobs_path := os.path.join(scratch, "linked.json"), "w", encoding="utf-8") as fh:
            json.dump([base_job], fh)
        real_is_symlink = _Path.is_symlink
        with patch.object(_Path, "is_symlink",
                          lambda p: p.name == "linked.json" or real_is_symlink(p)):
            linked_allowed = sidecar.fanout_allowed(
                {"cwd": fanout_root, "tool_input": {"command": linked_cmd}})
        os.remove(linked_jobs_path)
        cases.append(("a symlinked jobs file falls through (is_symlink simulated; no symlink privilege)",
                      linked_allowed is False))
    if os.name == "nt":
        # A directory junction is not is_symlink(), and mklink /J needs no privilege.
        away = os.path.join(tmp, "junction-target")
        os.makedirs(away)
        with open(os.path.join(away, "jobs.json"), "w", encoding="utf-8") as fh:
            json.dump([base_job], fh)
        made = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(scratch, "jdir"), away],
                              capture_output=True).returncode == 0
        cases.append(("a jobs file reached through a directory junction falls through",
                      made and run(fanout_cmd.replace(".claude/scratch/jobs.json",
                                                      ".claude/scratch/jdir/jobs.json"),
                                   fanout_env, session="sdc00067") == {}))
    cases.append(("a jobs path with a .. segment falls through",
                  run(fanout_cmd.replace(".claude/scratch/jobs.json", ".claude/scratch/../scratch/jobs.json"),
                      fanout_env, session="sdc00066") == {}))

    # --- background allow (this revision, Design §1/§2): a backgrounded lib launcher is never
    # denied — the lib itself writes <record>.exit/.out on every exit path now. With -R it
    # carries one advisory line naming the record; without -R it carries none. Preflight runs
    # exactly as it does in the foreground (§1's numbering is unchanged).
    reexec_marker = os.path.join(tmp, "reexec-check-ran").replace("\\", "/")
    reexec = fdir + "/reexec_sidecar.sh"
    with open(reexec, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(reexec_launcher(reexec_marker))
    record = os.path.join(tmp, "detach.record.json").replace("\\", "/")

    allowed_no_r = run("bash %s -m fake -f brief.md" % reexec, env, session="sdc00030",
                       run_in_background=True)
    cases.append(("a backgrounded launch without -R is not denied and its --check DOES run",
                  allowed_no_r.get("permissionDecision") is None
                  and os.path.exists(reexec_marker)))
    cases.append(("a backgrounded launch without -R carries no .exit advisory",
                  "if a killed or stopped notice arrives" not in allowed_no_r.get("additionalContext", "")))
    allowed_with_r = run("bash %s -R %s -m fake" % (reexec, record), env, session="sdc00031",
                        run_in_background=True)
    cases.append(("a backgrounded launch with -R is not denied",
                  allowed_with_r.get("permissionDecision") is None))
    cases.append(("a backgrounded launch with -R carries the .exit advisory naming the record",
                  ("read %s.exit" % record) in allowed_with_r.get("additionalContext", "")
                  and "arm a Monitor on it if it is absent" in allowed_with_r.get("additionalContext", "")))
    cases.append(("a foreground launch with -R carries no .exit advisory (advisory is background-only)",
                  "if a killed or stopped notice arrives" not in run(
                      "bash %s -R %s -m fake" % (reexec, record), env,
                      session="sdc00032").get("additionalContext", "")))
    cases.append(("a backgrounded `--check -m <alias>` probe is not judged",
                  run("bash %s --check -m fake" % reexec, env, session="sdc00034",
                      run_in_background=True) == {}))
    cases.append(("a backgrounded `--check;echo x` still counts as a check (shlex, not substring)",
                  run("bash %s --check;echo x" % reexec, env, session="sdc00035",
                      run_in_background=True) == {}))
    cases.append(("a backgrounded launch whose -l value CONTAINS --check is still not a check, not denied",
                  run('bash %s -l "--check-marker" -m fake' % reexec, env, session="sdc00036",
                      run_in_background=True).get("permissionDecision") is None))
    cases.append(("a backgrounded grep naming a lib launcher is not judged",
                  run("grep -n 'model' %s" % reexec, env, session="sdc00037",
                      run_in_background=True) == {}))
    cases.append(("a backgrounded non-lib fake launcher (no sc_reexec_snapshot) is not judged",
                  run("bash %s -m fake -f brief.md" % ok, env, session="sdc00038",
                      run_in_background=True).get("permissionDecision") is None))

    fanout_first = run(fanout_cmd, fanout_env, session="sdc00040", run_in_background=True)
    cases.append(("a backgrounded fan-out is not denied and carries the advisory beside the ref doc",
                  fanout_first.get("permissionDecision") == "allow"
                  and fanout_first.get("additionalContext", "").startswith("[sidecar dispatch")
                  and "run detached" in fanout_first.get("additionalContext", "")))
    fanout_second = run(fanout_cmd, fanout_env, session="sdc00040", run_in_background=True)
    cases.append(("a later backgrounded fan-out in the same session still carries the advisory alone",
                  fanout_second.get("permissionDecision") == "allow"
                  and fanout_second.get("additionalContext") == FANOUT_BACKGROUND_ADVISORY))

    real_root = os.path.abspath(os.path.join(HERE, "..", ".."))
    real_record = os.path.join(tmp, "detach-payload-probe.record.json").replace("\\", "/")
    real_env = dict(os.environ, CLAUDE_PROJECT_DIR=real_root, HARNESS_HOOK_STATE_DIR=tmp,
                    PYTHONIOENCODING="utf-8")
    # This case proves the real dispatcher's allow + advisory channel. The real launcher's --check
    # can exceed the 45 s preflight budget on a loaded machine, and a timed-out preflight now refuses,
    # so preflight is off here; the fake-launcher cases above own the preflight contract.
    real_env["HARNESS_SIDECAR_PREFLIGHT"] = "0"
    # anthropic_sidecar.sh is consumer-local, so the baseline template ships no such file and the
    # case would fail there for an absence that is correct. The dispatcher classifies the command
    # text, so any provider launcher this tree ships proves the same allow + advisory channel.
    real_launcher = next(
        (n for n in ("anthropic_sidecar.sh", "codex_proxy_sidecar.sh", "deepseek_sidecar.sh",
                     "opencode_sidecar.sh")
         if os.path.isfile(os.path.join(real_root, ".claude", "scripts", n))),
        None)
    real_cmd = ("bash .claude/scripts/%s -m sonnet -R %s" % (real_launcher, real_record))
    arm_ladder(real_env, "sdc00041")
    real = subprocess.run(
        [sys.executable, PRE_BASH_DISPATCH],
        input=json.dumps({"tool_name": "Bash", "session_id": "sdc00041",
                          "tool_input": {"command": real_cmd, "run_in_background": True}}),
        capture_output=True, text=True, timeout=90, env=real_env, cwd=real_root,
    )
    real_combined = (real.stdout or "") + "\n" + (real.stderr or "")
    real_out = (real.stdout or "").strip()
    real_crashed = (real.returncode not in (0, 2)
                    or "Traceback (most recent call last)" in real_combined)
    real_hso = {}
    if not real_crashed and real_out:
        try:
            real_hso = json.loads(real_out).get("hookSpecificOutput") or {}
        except ValueError:
            real_crashed = True
    cases.append(("the real pre_bash_dispatch.py channel allows a backgrounded %s "
                  "call and carries the .exit advisory" % real_launcher,
                  not real_crashed
                  and real.returncode == 0
                  and real_hso.get("permissionDecision") is None
                  and ("read %s.exit" % real_record) in real_hso.get("additionalContext", "")))

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
                  run("python3 bench.py campaign zz --launcher %s --tasks t4" % refusing, env, session="sdc00005") == {}))
    cases.append(("a launch line inside a quoted python -c string is not a launch",
                  run("python3 -c \"print(parse('bash %s -m fake -f b.md'))\"" % refusing, env, session="sdc00006") == {}))
    cases.append(("a launch line echoed in quotes is not a launch",
                  run("echo 'bash %s -m fake -f b.md'" % refusing, env, session="sdc00007") == {}))
    cases.append(("an env-prefixed launch after && is still a launch",
                  run("cd /tmp && FOO=1 bash %s -m fake -f b.md" % refusing, env, session="sdc00008")
                  .get("permissionDecision") == "deny"))

    failures = [label for label, ok_ in cases if not ok_]
    for label, ok_ in cases:
        print("%-4s %s" % ("ok" if ok_ else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
