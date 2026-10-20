"""Re-runnable proof for hooks/sidecar_dispatch_context.py.

PREFLIGHT: a launch whose launcher `--check` exits non-zero is DENIED with the refusal text;
`-A`, `-U` and `-W` pass through. A passing check is not denied.
APPROVAL: a canonical read-only launch gets allow. A whole-command, project-contained review
with only `+Bash` added gets ask after preflight; shell compounds and wider
grants do not. The real PreToolUse dispatcher preserves that ask over ordinary allow decisions.
BACKGROUND: a lib launcher is not denied for running in the background. The hook adds no pointer,
no wake-route line and no background advisory: those facts are in `reference/sidecar_dispatch.md`
steps 5 and 6 and `/delegate`, read before the call. A mere mention is not a launch.
CONTEXT: an admitted launch carries no `additionalContext` at all unless the doctrine gate could not
read its state (a fault report): no recipe pointer, no launch card, no currency, probation,
cheaper-claimant, long-context or size line.
TIER FIT (hooks/_dispatch_checks.py, the check the Workflow route runs): a launch or fan-out job whose
shape implies a tier its alias's row does not serve on the alias's transport is DENIED.

Fake launchers stand in for the real ones so the proof spends nothing and needs no network.
State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.
SIDECAR_DISPATCH_HOOK overrides the hook path under test (used to capture a RED record against
the pre-change hook; unset, it targets the live `hooks/sidecar_dispatch_context.py`).

    python3 .claude/tests/test_sidecar_dispatch_context.py
"""
import importlib.util
import io
import json
import os
import subprocess
import types
import sys
import tempfile
import time
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _settings_probe  # noqa: E402
import _transport_fixture  # noqa: E402
HOOKS = os.path.join(HERE, "..", "hooks")
REPO = os.path.abspath(os.path.join(HERE, ".."))
HOOK = os.environ.get("SIDECAR_DISPATCH_HOOK") or os.path.join(HOOKS, "sidecar_dispatch_context.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")
spec = importlib.util.spec_from_file_location("sidecar_dispatch_context_probe", HOOK)
sidecar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecar)
PRE_BASH_DISPATCH = os.path.join(HOOKS, "pre_bash_dispatch.py")

SID = "sdc00001"

WAKE_COMMAND = "python3 .claude/tools/watch_records.py --session --wake"
# Every late advisory the hook used to add to an admitted call; none may come back.
LATE_ADVISORY_MARKERS = ("currency (orchestration", "probation (orchestration", "cheaper claimants", "[long-context]",
                         "dispatch size:", "[sidecar dispatch", "Launch card", "stopped notice", "background limit",
                         "launcher's own exit is not the run's", "also paid by", WAKE_COMMAND)


def late_advice(hso):
    """The late-advisory markers an admitted call's additionalContext carries, else []."""
    text = (hso or {}).get("additionalContext", "")
    return [m for m in LATE_ADVISORY_MARKERS if m in text]

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


def arm_skill(env, session):
    """The orchestration skill in context, through the real marker seam (_hook_state)."""
    sys.path.insert(0, os.path.join(REPO, "hooks"))
    import _hook_state
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=env["HARNESS_HOOK_STATE_DIR"]):
        _hook_state.mark_skill_in_context(session or "", "orchestration")


def names_ladder_route(reason):
    """A ladder deny names the full Read when the launch's tiers are unknown, else the slice command."""
    return "model_ladder_evidence.md" in reason or "model_registry.py ladder-slice" in reason


def arm_slice(env, session, tiers):
    """The orchestration skill and a ladder slice for `tiers` in context, through the real marker seam."""
    arm_skill(env, session)
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=env["HARNESS_HOOK_STATE_DIR"]):
        import _hook_state
        assert _hook_state.mark_ladder_slice(session, tiers)


def run(command, env, session=SID, run_in_background=False, ladder=True, cwd=None, skill=True):
    # Relative jobs belong to the planted project, not the checkout running this proof.
    cwd = cwd or env["CLAUDE_PROJECT_DIR"]
    if ladder:
        arm_ladder(env, session)
    if skill:
        arm_skill(env, session)
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
                capture_output=True, text=True, timeout=60,
                env=dict(env, CLAUDE_PROJECT_DIR=env["HARNESS_HOOK_STATE_DIR"]))


def rooms_cache(folder, shares):
    """A fresh provider-capacity cache: {transport: (used %, fraction of the week left)} per plan."""
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import model_registry
    import time
    now = time.time()
    blob = {}
    for name, cfg in (model_registry.load().get("transports") or {}).items():
        if cfg.get("costModel") != "plan-quota" or name not in shares:
            continue
        used, left = shares[name]
        blob[name] = {
            "schemaVersion": 1, "transport": name, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "status": "available",
            "error": None, "liveFailure": None, "balance": None,
            "quota": {"windows": [{"name": "seven_day", "usedPercent": used, "resetsAt": now + left * 604800,
                                   "windowDurationMins": 10080, "pressure": 1.0, "band": "Hot"}],
                      "routingWindow": "seven_day", "bindingWindow": "seven_day",
                      "routingBand": "Hot", "dispatchBand": "Hot", "credits": None}}
    path = os.path.join(folder, "rooms_cache.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    return path


def currency_cases(tmp, ok, env):
    """[(label, ok)] for tier fit on the sidecar route, and for the silence of an admitted launch against
    planted rooms where the job's plan spends faster than its window (the rooms are `[budget-posture]`'s)."""
    import model_registry
    data = model_registry.load()
    rows = {m["alias"]: m for m in model_registry.available_models(data)}

    def serving(transport, want, refuse=()):
        return next((a for a, m in sorted(rows.items()) if m["transport"] == transport
                     and set(want) <= set(model_registry.entry_tiers(m))
                     and not set(refuse) & set(model_registry.entry_tiers(m))), None)

    go_exec = serving("go", ["executor"])
    go_fan_only = serving("go", ["fanout"], refuse=["executor"])
    host_exec = serving("anthropic", ["executor"])
    paid_exec = next((a for a, m in sorted(rows.items()) if "executor" in model_registry.entry_tiers(m)
                      and model_registry.cost_model_for(m["transport"], data) == "marginal-usd"), None)
    cases = [("the live registry has the rows the currency cases need",
              all((go_exec, go_fan_only, host_exec, paid_exec)))]
    if not cases[0][1]:
        return cases
    cache = rooms_cache(tmp, {"anthropic": (22, 6.1 / 7), "anthropic-secondary": (60, 6.1 / 7), "go": (30, 6.3 / 7),
                               "codex": (77, 5.1 / 7)})
    cenv = dict(env, PROVIDER_CAPACITY_CACHE=cache, CLAUDE_CODE_TRANSPORT="anthropic")

    def launch(alias, shape, session):
        return run("bash %s -G %s -m %s -f brief.md" % (ok, shape, alias), cenv, session=session)

    unfit = launch(go_fan_only, "review", "sdcfit01")
    reason = unfit.get("permissionDecisionReason", "")
    cases.append(("a Go alias outside the review shape's tier is DENIED, naming the tier's rows on go",
                  unfit.get("permissionDecision") == "deny" and go_fan_only in reason
                  and "`executor` rows on go" in reason and go_exec in reason and "orchestration §5" in reason))
    fit = launch(go_exec, "review", "sdcfit02")
    cases.append(("a Go alias serving the review shape's tier passes tier fit",
                  fit.get("permissionDecision") != "deny"))
    cases.append(("an alias the registry does not know is left to the launcher's own gate",
                  launch("fake", "review", "sdcfit03").get("permissionDecision") != "deny"))
    # The plan rooms, the probation mark and the cheaper claimants reach the session before the call; an
    # admitted launch carries none of them, on a plan that spends faster than its window or not.
    any_shape = launch(go_exec, "any", "sdccur01")
    cases.append(("a -G any Go launch with anthropic ranked higher is admitted with no currency advisory",
                  any_shape.get("permissionDecision") != "deny" and late_advice(any_shape) == []))
    cases.append(("...the same verdict as the -G review launch", late_advice(fit) == []))
    host = launch(host_exec, "review", "sdccur02")
    cases.append(("a launch on the plan with the most quota for its window carries no late advisory",
                  late_advice(host) == []))
    marked = json.loads(json.dumps(data))
    row = next(m for m in marked["models"] if m["alias"] == go_exec)
    row["probation"] = [{"tier": "executor", "text": "planted mark", "evidenceRef": "fixture"}]
    registry = os.path.join(tmp, "probation_registry.json")
    with open(registry, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(marked, fh)
    on_probation = run("bash %s -G review -m %s -f brief.md" % (ok, go_exec),
                       dict(cenv, HARNESS_MODEL_REGISTRY=registry), session="sdcprob1")
    cases.append(("a sidecar pin the owner marked for its shape's tier is admitted with no probation line",
                  on_probation.get("permissionDecision") != "deny" and late_advice(on_probation) == []
                  and "planted mark" not in on_probation.get("additionalContext", "")))
    costs = model_registry.ladder_costs(data)
    ranked = sorted((costs[m["id"]], a) for a, m in rows.items() if m["transport"] == "anthropic"
                    and "executor" in model_registry.entry_tiers(m) and costs.get(m["id"]) is not None)
    if len(ranked) >= 2 and ranked[0][0] < ranked[-1][0]:
        dear = ranked[0][1]
        cases.append(("a pricier executor pin is admitted with no cheaper-claimants line",
                      late_advice(launch(dear, "author", "sdccheap1")) == []))
    else:
        cases.append(("the live ladder has two anthropic executor rows at different costs", False))
    paid = launch(paid_exec, "review", "sdccur03")
    cases.append(("a pay-per-token launch while a fit plan still has quota is admitted with no currency line",
                  paid.get("permissionDecision") != "deny" and late_advice(paid) == []))

    root = env["CLAUDE_PROJECT_DIR"]
    scratch = os.path.join(root, ".claude", "scratch")
    os.makedirs(scratch, exist_ok=True)
    prompt = os.path.join(scratch, "fan_prompt.md")
    with open(prompt, "w", encoding="utf-8") as fh:
        fh.write("survey\n")

    def fanout(jobs, session):
        path = os.path.join(scratch, "cur_jobs.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(jobs, fh)
        return run("python3 .claude/tools/sidecar_fanout.py .claude/scratch/cur_jobs.json "
                   "--out-dir .claude/scratch/cur_out", cenv, session=session, cwd=root)

    denied = fanout([{"label": "wide", "alias": go_fan_only, "promptFile": prompt, "shape": "review"}],
                    "sdcfan01")
    cases.append(("a fan-out job outside its shape's tier is DENIED naming the job",
                  denied.get("permissionDecision") == "deny"
                  and "wide" in denied.get("permissionDecisionReason", "")))
    denied = fanout([{"label": "wide2", "model": go_fan_only, "promptPath": prompt, "shape": "review"}],
                    "sdcfan02")
    cases.append(("a fan-out job written in dispatch.js field names is checked the same way",
                  denied.get("permissionDecision") == "deny"
                  and "wide2" in denied.get("permissionDecisionReason", "")))
    advised = fanout([{"label": "scan", "alias": go_exec, "promptFile": prompt, "shape": "survey"}],
                     "sdcfan03")
    cases.append(("a fitting Go fan-out job is admitted with no currency advisory and no size line",
                  advised.get("permissionDecision") != "deny" and late_advice(advised) == []))

    def verdict(result):
        return (result.get("permissionDecision"), result.get("permissionDecisionReason", "").replace("wide", "J"),
                result.get("additionalContext", ""))

    old_deny = fanout([{"label": "wide", "alias": go_fan_only, "promptFile": prompt, "shape": "review"}],
                      "sdcfan04")
    new_deny = fanout({"jobs": [{"label": "wide", "model": go_fan_only, "promptPath": prompt,
                                 "shape": "review"}], "contextPath": prompt}, "sdcfan05")
    cases.append(("a wrapper-object fan-out in dispatch.js names gets the old-name tier deny",
                  new_deny.get("permissionDecision") == "deny" and verdict(new_deny) == verdict(old_deny)))
    old_adv = fanout([{"label": "scan", "alias": go_exec, "promptFile": prompt, "shape": "survey"}],
                     "sdcfan06")
    new_adv = fanout([{"label": "scan", "model": go_exec, "promptPath": prompt, "shape": "survey"}],
                     "sdcfan07")
    wrap_adv = fanout({"jobs": [{"label": "scan", "model": go_exec, "promptPath": prompt,
                                 "shape": "survey"}]}, "sdcfan08")
    cases.append(("a new-name list and a wrapper object get the old-name verdict",
                  verdict(new_adv) == verdict(old_adv) == verdict(wrap_adv)))
    same_pair = fanout([{"label": "scan", "alias": go_exec, "model": go_exec, "promptPath": prompt,
                         "shape": "survey"}], "sdcfan09")
    cases.append(("an agreeing mixed pair is read as the old-name job",
                  verdict(same_pair) == verdict(old_adv)))
    clash = fanout([{"label": "wide", "alias": go_exec, "model": go_fan_only, "promptFile": prompt,
                     "shape": "review"}], "sdcfan10")
    malformed = fanout({"jobs": "none"}, "sdcfan11")
    cases.append(("a conflicting mixed pair is treated as a malformed jobs file: no deny, no advisory",
                  verdict(clash) == verdict(malformed) and clash.get("permissionDecision") is None
                  and late_advice(clash) == []))
    return cases

def plan_cases(tmp):
    """[(label, ok)] for seam 6: a launch or fan-out job carries its paying plan into the checks.

    In-process, against the live registry with `anthropic-secondary` forced available and a planted
    capacity cache on which the primary is roomier; `set_deadline` bars every live capacity read."""
    import copy
    import time
    import model_registry
    forced = copy.deepcopy(model_registry.load())
    forced["transports"]["anthropic-secondary"]["status"] = {"state": "available"}
    host_exec = next(m["alias"] for m in model_registry.available_models(forced)
                     if m["transport"] == "anthropic" and "executor" in model_registry.entry_tiers(m))
    wrapper = forced["transports"]["anthropic-secondary"]["launcher"]
    cache = rooms_cache(tmp, {"anthropic": (22, 6.1 / 7), "anthropic-secondary": (60, 6.1 / 7)})
    root = os.path.join(tmp, "plan-root")
    scratch = os.path.join(root, ".claude", "scratch")
    os.makedirs(scratch, exist_ok=True)
    prompt = os.path.join(scratch, "plan_prompt.md")
    with open(prompt, "w", encoding="utf-8") as fh:
        fh.write("survey\n")
    jobs_path = os.path.join(scratch, "plan_jobs.json")
    with open(jobs_path, "w", encoding="utf-8") as fh:
        json.dump([{"label": "paid-b", "alias": host_exec, "promptFile": prompt, "shape": "survey",
                    "plan": "anthropic-secondary"}], fh)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_TRANSPORT", "CLAUDE_CONFIG_DIR", "ANTHROPIC_BASE_URL")}
    env["PROVIDER_CAPACITY_CACHE"] = cache
    cases = []
    with patch.dict(os.environ, env, clear=True), patch.object(model_registry, "load",
                                                                lambda *_a, **_k: forced):
        sidecar._dispatch_checks.set_deadline(time.monotonic())
        try:
            direct = sidecar.sidecar_launch("bash %s -G review -m %s -f brief.md -l wrap1" % (wrapper, host_exec))
            jobs = sidecar.launch_jobs(direct, None, root)
            cases.append(("a direct wrapper launch carries its plan as the job's 4th element",
                          [j[3:] for j in jobs] == [("anthropic-secondary",)]))
            cases.append(("a wrapper launch whose shape implies no tier it fails is not refused, and builds no advisory",
                          sidecar.dispatch_checks(jobs) is None))
            with patch.dict(os.environ, {"PROVIDER_CAPACITY_CACHE": rooms_cache(
                    tmp, {"anthropic": (10, 0.3), "anthropic-secondary": (60, 6.1 / 7)})}):
                spare = sidecar.dispatch_checks(jobs)
            cases.append(("...whatever the plans' rooms: the check returns a refusal or None, never lines",
                          spare is None))
            fan = sidecar.fanout_launch("python3 .claude/tools/sidecar_fanout.py %s"
                                        % jobs_path.replace("\\", "/"))
            fjobs = sidecar.launch_jobs(None, fan, root)
            cases.append(("a fan-out job carries its `plan` as the 4th element",
                          [j[3:] for j in fjobs] == [("anthropic-secondary",)]))
            folder, name = os.path.split(jobs_path.replace("\\", "/"))
            for shape in ("S=%s && python3 .claude/tools/sidecar_fanout.py $S/%s",
                          "S=%s; cat > /dev/null <<'EOF'\n$S stays\nEOF\npython3 .claude/tools/sidecar_fanout.py ${S}/%s --detach"):
                vjobs = sidecar.launch_jobs(None, sidecar.fanout_launch(shape % (folder, name)), root)
                cases.append(("a fan-out jobs path held in a variable the command assigned reads its jobs: %r"
                              % shape.split(" ")[0][:12], vjobs == fjobs))
            bare_path = os.path.join(scratch, "plan_bare_jobs.json")
            with open(bare_path, "w", encoding="utf-8") as fh:
                json.dump([{"label": "bare", "alias": host_exec, "promptFile": prompt, "shape": "review"}], fh)
            bare = sidecar.launch_jobs(None, sidecar.fanout_launch(
                "python3 .claude/tools/sidecar_fanout.py %s" % bare_path.replace("\\", "/")), root)
            with patch.dict(os.environ, {"PROVIDER_CAPACITY_CACHE": rooms_cache(
                    tmp, {"anthropic": (80, 0.5), "anthropic-secondary": (5, 0.5)})}):
                bare_refusal = sidecar.dispatch_checks(bare)
            cases.append(("a fan-out job naming no plan carries none: the fan-out picks the roomiest plan",
                          bool(bare) and bare[0][3] is None and bare_refusal is None))
            unset = sidecar.launch_jobs(None, sidecar.fanout_launch(
                "python3 .claude/tools/sidecar_fanout.py $UNSET_DIR/%s" % name), root)
            cases.append(("an unassigned variable stays as written and reads no jobs", unset == []))
            job = {"label": "paid-b", "alias": host_exec, "promptFile": prompt, "shape": "survey",
                   "workdir": ".", "transport": "anthropic", "plan": "anthropic-secondary"}
            with patch.object(sidecar, "_regular_project_file", lambda *_a: True):
                cases.append(("a fan-out job with `plan` stays auto-approvable",
                              sidecar._safe_fanout_job(job, root, forced) is True))
                cases.append(("...but not with a plan that does not pay for its route",
                              sidecar._safe_fanout_job(dict(job, plan="codex"), root, forced) is False))
                cases.append(("...and `transport` naming a plan still fails agreement",
                              sidecar._safe_fanout_job(dict(job, transport="anthropic-secondary"),
                                                       root, forced) is False))
        finally:
            sidecar._dispatch_checks.set_deadline(None)
    return cases


def long_context_cases(tmp, ok, env):
    """[(label, ok)]: a launch that runs a FRESH child at the long-context window (`-M`, fan-out
    `longContext: true`) carries no advisory. The rule (resume only, never a fresh job) is a flag-list fact in
    `reference/sidecar_dispatch.md`, read before the call; the hook never denies it either."""
    scratch = os.path.join(tmp, "lc-root", ".claude", "scratch")
    os.makedirs(scratch, exist_ok=True)
    prompt = os.path.join(scratch, "lc_prompt.md")
    with open(prompt, "w", encoding="utf-8") as fh:
        fh.write("work\n")
    cases = [("the hook keeps no long-context line builder",
              not hasattr(sidecar, "long_context_lines"))]
    out = run("bash %s -m fake -M -f brief.md -l chan-long" % ok, env, session="sdclc001")
    cases.append(("a fresh direct -M launch is not denied and carries no long-context line",
                  out.get("permissionDecision") != "deny" and late_advice(out) == []
                  and "chan-long" not in out.get("additionalContext", "")))
    out = run("bash %s -m fake -M -r abc -f brief.md -l chan-resume" % ok, env, session="sdclc002")
    cases.append(("a resuming -M launch carries none either", late_advice(out) == []))
    return cases

def test_preflight_gets_its_full_timeout_and_no_check_reads_capacity(tmp_path):
    project = tmp_path / "project"
    scripts = project / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "ok_sidecar.sh"
    launcher.write_text(OK_LAUNCHER, encoding="utf-8")
    asked = []
    payload = {"tool_name": "Bash", "session_id": "s13budget", "tool_input": {
        "command": "bash .claude/scripts/ok_sidecar.sh -m fake -f brief.md"}}

    def bounded(_argv, timeout, **_kwargs):
        asked.append(timeout)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(project)), \
            patch.object(sidecar, "run_bounded", side_effect=bounded), \
            patch.object(sidecar._dispatch_checks, "read_capacity",
                         side_effect=AssertionError("the tier check reads no capacity")), \
            patch.object(sidecar.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch.object(sidecar.sys, "stdout", io.StringIO()):
        sidecar.main()
    assert asked == [sidecar.PREFLIGHT_TIMEOUT], asked

def test_first_launch_injects_no_recipe_card_or_pointer(tmp_path, monkeypatch):
    ref = tmp_path / ".claude/reference/sidecar_dispatch.md"
    ref.parent.mkdir(parents=True)
    ref.write_text("# Sidecar recipe\n\n## Launch card\n\nCARD-LINE-MARKER\n\n## Procedure\n\nBODY-MARKER\n",
                   encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sidecar, "dispatch_checks", lambda jobs: None)
    monkeypatch.setattr(sidecar, "launch_jobs", lambda *args: [])
    monkeypatch.setattr(sidecar, "run_preflight", lambda *args: ("skipped", None))
    monkeypatch.setattr(sidecar.model_ladder_gate, "doctrine_check", lambda payload, tiers=None: (None, None), raising=False)
    payload = {"tool_name": "Bash", "session_id": "card0001",
               "tool_input": {"command": "bash .claude/scripts/ok_sidecar.sh -m fake -f brief.md"}}
    for _ in range(2):
        out = io.StringIO()
        with patch.object(sidecar.sys, "stdin", io.StringIO(json.dumps(payload))), \
                patch.object(sidecar.sys, "stdout", out):
            sidecar.main()
        context = json.loads(out.getvalue() or "{}").get("hookSpecificOutput", {}).get("additionalContext", "")
        assert context == "", context

def test_the_recipe_carries_what_the_late_advisories_said():
    """The facts the deleted notes carried are on the recipe, read before the launch."""
    with open(os.path.join(REPO, "reference", "sidecar_dispatch.md"), encoding="utf-8") as fh:
        text = fh.read()
    step6 = text.split("6. **Watch", 1)[1].split("7. **Consume", 1)[0]
    assert "stopped notice" in step6 and "<record>.exit" in step6
    assert "its own exit is not the run's" in text


def test_a_doctrine_fault_is_the_only_context_an_admitted_launch_carries(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sidecar, "dispatch_checks", lambda jobs: None)
    monkeypatch.setattr(sidecar, "launch_jobs", lambda *args: [])
    monkeypatch.setattr(sidecar, "run_preflight", lambda *args: ("skipped", None))
    fault = sidecar.model_ladder_gate.FAULT_NOTE
    monkeypatch.setattr(sidecar.model_ladder_gate, "doctrine_check",
                        lambda payload, tiers=None: (None, fault), raising=False)
    payload = {"tool_name": "Bash", "session_id": "fault001",
               "tool_input": {"command": "bash .claude/scripts/ok_sidecar.sh -m fake -f brief.md"}}
    out = io.StringIO()
    with patch.object(sidecar.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch.object(sidecar.sys, "stdout", out):
        sidecar.main()
    context = json.loads(out.getvalue()).get("hookSpecificOutput", {}).get("additionalContext", "")
    assert context == fault, context

def _deny_reason(payload):
    out = io.StringIO()
    with patch.object(sidecar.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch.object(sidecar.sys, "stdout", out):
        sidecar.main()
    hso = json.loads(out.getvalue() or "{}").get("hookSpecificOutput", {})
    return hso.get("permissionDecisionReason") if hso.get("permissionDecision") == "deny" else None


def test_a_fanout_whose_jobs_file_the_call_writes_defers_the_doctrine_to_its_launchers(tmp_path, monkeypatch):
    """The jobs file a call writes does not exist when its PreToolUse hook runs, so the hook cannot read the
    tiers the doctrine needs; the launcher reads them once the file exists."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sidecar, "run_preflight", lambda *args: ("skipped", None))
    monkeypatch.setattr(sidecar.model_ladder_gate, "doctrine_check",
                        lambda payload, tiers=None: ("DOCTRINE-UNMET", None), raising=False)
    (tmp_path / "written.json").write_text('{"jobs": []}', encoding="utf-8")
    launch = "python3 .claude/tools/sidecar_fanout.py %s --detach"
    write = "cat > unwritten.json <<'EOF'\n{\"jobs\": []}\nEOF\n"
    deferred = _deny_reason({"tool_name": "Bash", "session_id": "defer001", "cwd": str(tmp_path),
                             "tool_input": {"command": write + launch % "unwritten.json"}})
    assert deferred is None, deferred
    present = _deny_reason({"tool_name": "Bash", "session_id": "defer002", "cwd": str(tmp_path),
                            "tool_input": {"command": launch % "written.json"}})
    assert present and "DOCTRINE-UNMET" in present, present


def test_the_launcher_checks_the_doctrine_for_a_launch_no_hook_checked(monkeypatch, capsys):
    sys.path.insert(0, HOOKS)
    import dispatch_table
    calls = []

    def doctrine(payload, tiers=None):
        calls.append(payload.get("session_id"))
        return "DOCTRINE-UNMET", None

    monkeypatch.setattr(sidecar.model_ladder_gate, "doctrine_check", doctrine, raising=False)
    monkeypatch.setattr(dispatch_table, "sidecar_entries", lambda payload: ([{"id": "x"}], []))
    monkeypatch.setattr(dispatch_table, "register_sidecars", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidecar, "dispatch_checks", lambda jobs: None)
    argv = ["-m", "fake", "-f", "brief.md", "-G", "author"]
    launcher = os.path.join(REPO, "scripts", "anthropic_sidecar.sh")

    monkeypatch.setattr(sidecar, "tracking_session", lambda: "launch01")
    monkeypatch.setattr(sidecar, "take_checks", lambda *args: (False, True))
    assert sidecar.launcher_launch(launcher, argv) == 2
    assert "DOCTRINE-UNMET" in capsys.readouterr().err

    monkeypatch.setattr(sidecar, "take_checks", lambda *args: (True, True))
    assert sidecar.launcher_launch(launcher, argv) == 0

    monkeypatch.setattr(sidecar, "tracking_session", lambda: None)
    monkeypatch.setattr(sidecar, "take_checks", lambda *args: (False, True))
    assert sidecar.launcher_launch(launcher, argv) == 0
    assert calls == ["launch01"], calls


import pytest
from pathlib import Path
import shlex


@pytest.fixture(autouse=True)
def isolated_session_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "session-state"))


@pytest.fixture
def checked_fanout(tmp_path, monkeypatch):
    import _hook_state
    import model_registry
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fold0001")
    monkeypatch.delenv("SIDECAR_DISPATCH_OWNER", raising=False)
    monkeypatch.setattr(sidecar, "run_preflight", lambda *a: ("skipped", None))
    data = json.loads(json.dumps(model_registry.load()))
    data["transports"]["anthropic-secondary"]["status"] = {"state": "available"}
    bad = next(row for row in data["models"] if row.get("alias") == "haiku")
    bad["roles"], bad["supplementary"] = ["fanout"], []
    monkeypatch.setattr(model_registry, "load", lambda *a, **k: data)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("HARNESS_MODEL_REGISTRY", str(registry_path))
    monkeypatch.setenv("HARNESS_SIDECAR_PREFLIGHT", "0")
    jobs = [{"label": "pc-plc-test-readiness", "alias": "sonnet", "effort": "medium",
             "shape": "review", "promptFile": str(tmp_path / "pc_plc-test-readiness.md")}]
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(jobs), encoding="utf-8")
    out = tmp_path / "run"
    out.mkdir()
    command = shlex.join(["python3", ".claude/tools/sidecar_fanout.py", str(path),
                          "--review", "--out-dir", str(out), "--max-parallel", "1",
                          "--fold-alias", "sonnet", "--fold-effort", "medium",
                          "--fold-subject", ".::snap_a.json::.claude/plans/a.md",
                          "--fold-subject", ".::snap_b.json::.claude/plans/b.md"])

    def check(loaded=True, suffix="", mode=None):
        if loaded:
            arm_slice(dict(os.environ), "fold0001", ["executor"])
        cmd = command
        if mode:
            cmd = shlex.join(["python3", ".claude/tools/sidecar_fanout.py", str(path),
                              "--out-dir", str(out), "--max-parallel", "1"])
            if mode == "chains":
                path.write_text(json.dumps({"chains": [{"name": "lane", "jobs": jobs}]}), encoding="utf-8")
            elif mode == "resume":
                path.write_text(json.dumps([dict(jobs[0], plan="anthropic-secondary",
                                                 extraArgs=["-r", "saved-session"])]), encoding="utf-8")
            elif mode == "compare":
                cmd += " --compare"
        return _deny_reason({"tool_name": "Bash", "session_id": "fold0001", "cwd": str(tmp_path),
                             "tool_use_id": "fold-call", "tool_input": {"command": cmd + suffix}})

    def compact():
        _hook_state.clear_compaction_keys("fold0001")

    def launch(label="review-fold-1", plan="anthropic", attempt=1, alias="sonnet", directory=None,
               effort="medium"):
        # Launcher/alias/prompt prefixes cut from plan_check_60e59a75/run/fanout-driver.log [dispatch].
        directory = directory or out
        name = label + ("" if attempt == 1 else f".attempt{attempt}") + ".record.json"
        argv = ["-m", alias, "-f", str(directory / (label + "_prompt.md")), "-l", label,
                "-X", "-R", str(directory / name), "-e", effort, "-D", "full", "-G",
                "author" if label.startswith("review-fold-") else "review",
                "-P", str(directory / (label + ".stream"))]
        if label == "pc-plc-test-readiness":
            loaded, _shared, _spill = sidecar.sidecar_fanout.load_jobs(path)
            argv += loaded[0].get("extraArgs") or []
        launcher = model_registry.transport_meta(plan, data)["launcher"]
        result = checked_run([sys.executable, HOOK, "--launcher", launcher, *argv],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             env=dict(os.environ), timeout=30)
        sys.stderr.write(result.stderr)
        return result.returncode

    return check, compact, launch, path, out


@pytest.mark.parametrize("label,plan,attempt", [
    ("review-fold-1", "anthropic", 1), ("review-fold-2", "anthropic", 1),
    ("pc-plc-test-readiness", "anthropic-secondary", 1),
    ("pc-plc-test-readiness", "anthropic", 1),
    ("pc-plc-test-readiness", "anthropic-secondary", 2),
])
def test_checked_fanout_survives_compaction(checked_fanout, capsys, label, plan, attempt):
    check, compact, launch, _path, _out = checked_fanout
    assert check() is None
    compact()
    assert launch(label, plan, attempt) == 0, capsys.readouterr().err


@pytest.mark.parametrize("change", [{"alias": "opus"}, {"label": "unplanned"},
                                   {"effort": "high"}, {"directory": "elsewhere"}])
def test_unplanned_fanout_child_still_checks_current_doctrine(checked_fanout, capsys, change):
    check, compact, launch, _path, out = checked_fanout
    assert check() is None
    compact()
    if "directory" in change:
        change = dict(change, directory=out.parent / change["directory"])
    assert launch(**change) == 2
    assert "not in context" in capsys.readouterr().err


def test_explicit_seat_plan_binds_the_inherited_fold_plan(checked_fanout, capsys):
    check, compact, launch, path, _out = checked_fanout
    jobs = json.loads(path.read_text())
    jobs[0]["plan"] = "anthropic-secondary"
    path.write_text(json.dumps(jobs), encoding="utf-8")
    assert check() is None
    compact()
    assert launch("pc-plc-test-readiness", "anthropic-secondary") == 0, capsys.readouterr().err
    assert launch(plan="anthropic-secondary") == 0, capsys.readouterr().err
    assert launch(plan="anthropic", attempt=2) == 2
    assert "not in context" in capsys.readouterr().err


def test_direct_launch_receipt_remains_one_use(checked_fanout, capsys):
    _check, compact, launch, _path, out = checked_fanout
    arm_slice(dict(os.environ), "fold0001", ["executor"])
    command = shlex.join(["bash", ".claude/scripts/anthropic_sidecar.sh", "-m", "sonnet", "-e", "medium",
                          "-G", "author", "-l", "review-fold-1", "-f", str(out / "brief.md"),
                          "-R", str(out / "review-fold-1.record.json")])
    assert _deny_reason({"tool_name": "Bash", "session_id": "fold0001", "cwd": str(out.parent),
                         "tool_input": {"command": command}}) is None
    compact()
    assert launch() == 0, capsys.readouterr().err
    assert launch() == 2
    assert "not in context" in capsys.readouterr().err


@pytest.mark.parametrize("spill", [False, True])
def test_fanout_receipts_resolve_paths_at_the_calls_cwd(checked_fanout, monkeypatch, capsys, spill):
    _check, compact, launch, path, out = checked_fanout
    arm_ladder(dict(os.environ), "fold0001")
    arm_skill(dict(os.environ), "fold0001")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(path.parent / "doctrine-root"))
    if spill:
        monkeypatch.setattr(sidecar.sidecar_fanout, "REPO", path.parent / "tool-root")
        path.write_text(json.dumps({"jobs": json.loads(path.read_text()), "spillDir": "spill"}), encoding="utf-8")
        out = path.parent / "tool-root" / "spill"
    command = ("python3 .claude/tools/sidecar_fanout.py jobs.json "
               + ("" if spill else "--out-dir run ") + "--review "
               "--fold-alias sonnet --fold-effort medium --fold-subject .::snap.json::a.md")
    assert _deny_reason({"tool_name": "Bash", "session_id": "fold0001", "cwd": str(path.parent),
                         "tool_input": {"command": command}}) is None
    compact()
    assert launch(directory=out) == 0, capsys.readouterr().err


def test_unloaded_fanout_call_is_denied(checked_fanout):
    check, _compact, _launch, _path, _out = checked_fanout
    assert "not in context" in check(loaded=False)


@pytest.mark.parametrize("mode", ["chains", "resume", "compare"])
def test_other_fanout_routes_use_the_same_hook_receipts(checked_fanout, capsys, mode):
    check, compact, launch, _path, _out = checked_fanout
    assert check(mode=mode) is None
    compact()
    assert launch("pc-plc-test-readiness", "anthropic-secondary") == 0, capsys.readouterr().err


@pytest.mark.parametrize("plan", ["anthropic", "anthropic-secondary"])
def test_memory_retry_and_plan_fallback_consume_new_attempts(checked_fanout, capsys, plan):
    check, compact, launch, _path, _out = checked_fanout
    assert check() is None
    compact()
    assert launch("pc-plc-test-readiness", "anthropic") == 0, capsys.readouterr().err
    assert launch("pc-plc-test-readiness", plan, attempt=2) == 0, capsys.readouterr().err
    assert launch("pc-plc-test-readiness", plan, attempt=2) == 2


def test_large_fanout_keeps_the_first_queued_childs_receipt(checked_fanout, capsys):
    import dispatch_table
    check, compact, launch, path, _out = checked_fanout
    job = json.loads(path.read_text())[0]
    path.write_text(json.dumps([dict(job, label=f"pc-queued-{index}")
                               for index in range(dispatch_table.MAX_PENDING + 1)]), encoding="utf-8")
    assert check(mode="queued") is None
    compact()
    assert launch("pc-queued-0", "anthropic-secondary") == 0, capsys.readouterr().err


def test_refold_call_receipts_cover_authors_but_no_seat(checked_fanout, capsys):
    import _hook_state
    check, compact, launch, _path, out = checked_fanout
    assert check(suffix=" --refold --fold-plan anthropic-secondary") is None
    state = _hook_state.read_json_salvage(_hook_state.state_path("fold0001"))
    assert [row["fanout"]["job"][0] for row in state[sidecar.CHECKS_KEY][0]["children"]] == [
        "review-fold-1", "review-fold-2"]
    compact()
    assert launch(directory=out / "refold", plan="anthropic-secondary") == 0, capsys.readouterr().err
    assert launch("pc-plc-test-readiness") == 2


def test_fold_requires_executor_doctrine_even_with_only_survey_seats(checked_fanout):
    check, _compact, _launch, path, _out = checked_fanout
    jobs = json.loads(path.read_text())
    jobs[0]["shape"] = "survey"
    path.write_text(json.dumps(jobs), encoding="utf-8")
    arm_slice(dict(os.environ), "fold0001", ["fanout"])
    assert "ladder-slice executor" in check(loaded=False)


def test_fanout_attempt_receipt_cannot_be_consumed_twice(checked_fanout, capsys):
    check, compact, launch, _path, _out = checked_fanout
    assert check() is None
    compact()
    assert launch() == 0, capsys.readouterr().err
    assert launch() == 2
    assert "not in context" in capsys.readouterr().err


def test_fold_pin_is_tier_checked_at_hook_time(checked_fanout):
    check, _compact, _launch, _path, _out = checked_fanout
    assert "review-fold-1" in (check(suffix=" --fold-alias haiku") or "")


def main():
    tmp = tempfile.mkdtemp(prefix="sdcstate_")
    project = os.path.join(tmp, "project")
    fdir = os.path.join(project, ".claude", "scripts").replace("\\", "/")
    reference_dir = os.path.join(project, ".claude", "reference")
    scratch_dir = os.path.join(project, ".claude", "scratch")
    os.makedirs(fdir)
    os.makedirs(reference_dir)
    os.makedirs(scratch_dir)
    for name in ("consent_brief.md", "consent_schema.json"):
        with open(os.path.join(reference_dir, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("review\n")
    with open(os.path.join(REPO, "reference", "sidecar_dispatch.md"), encoding="utf-8") as source:
        reference = source.read()
    with open(os.path.join(reference_dir, "sidecar_dispatch.md"), "w", encoding="utf-8") as target:
        target.write(reference)
    env = dict(_transport_fixture.hook_env(), HARNESS_HOOK_STATE_DIR=tmp, CLAUDE_PROJECT_DIR=project)
    env.pop("HARNESS_SIDECAR_PREFLIGHT", None)
    ok = fdir + "/ok_sidecar.sh"
    refusing = fdir + "/hot_sidecar.sh"
    for path, body in ((ok, OK_LAUNCHER), (refusing, REFUSING_LAUNCHER)):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", ".claude/scripts"], cwd=project, check=True)
    launch = "bash %s -p prompt.md -m fake" % ok
    outside = os.path.join(tmp, "outside_sidecar.sh").replace("\\", "/")
    outside_marker = os.path.join(tmp, "outside-ran").replace("\\", "/")
    with open(outside, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\nif [ \"$1\" = --check ]; then printf x > \"%s\"; exit 8; fi\n"
                 % outside_marker)
    cases = []
    from pathlib import Path
    from pytest import MonkeyPatch
    for label, check in (
        ("a doctrine fault is the only context an admitted launch carries",
         test_a_doctrine_fault_is_the_only_context_an_admitted_launch_carries),
        ("a first launch injects no recipe, card or pointer", test_first_launch_injects_no_recipe_card_or_pointer),
        ("a fan-out whose jobs file the call writes defers the doctrine to its launchers",
         test_a_fanout_whose_jobs_file_the_call_writes_defers_the_doctrine_to_its_launchers),
    ):
        with MonkeyPatch.context() as monkeypatch:
            check(Path(tmp) / "ctx", monkeypatch)
        cases.append((label, True))
    try:
        test_preflight_gets_its_full_timeout_and_no_check_reads_capacity(Path(tmp) / "s13")
        cases.append(("the preflight gets its full timeout and the tier check reads no capacity", True))
    except Exception as exc:
        cases.append(("preflight timeout: " + repr(exc), False))
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
    unsuspend = fdir + "/suspended_sidecar.sh"
    with open(unsuspend, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('#!/usr/bin/env bash\nif [ "$1" = --check ]; then\n'
                 '  case " $* " in *" -U "*) echo "OK (-U)"; exit 0;; esac\n'
                 '  echo suspended >&2; exit 7\nfi\n')
    without_u = run("bash %s -m fake -f brief.md" % unsuspend, env, session="sdcj5001")
    cases.append(("D28: a suspended model without -U is still denied",
                  without_u.get("permissionDecision") == "deny"
                  and "exit 7" in without_u.get("permissionDecisionReason", "")))
    with_u = run("bash %s -U -m fake -f brief.md" % unsuspend, env, session="sdcj5002")
    cases.append(("D28: -U reaches preflight but does not auto-approve its authority grant",
                  with_u.get("permissionDecision") is None and late_advice(with_u) == []))
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
    broken = fdir + "/capacity_sidecar.sh"
    with open(broken, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\nif [ \"$1\" = --check ]; then\n"
                 "  echo '[sidecar] REFUSING fake: provider capacity runner failed (exit 1).' >&2; exit 8\nfi\n"
                 "echo dispatched\n")
    broken_result = run("bash %s -A -m fake -f brief.md" % broken, env, session="sdc00914")
    broken_reason = broken_result.get("permissionDecisionReason", "")
    cases.append(("a capacity-check failure (exit 8) is DENIED even with -A, never offers -A, and names the repair",
                  broken_result.get("permissionDecision") == "deny"
                  and "exit 8" in broken_reason and "pass -A" not in broken_reason
                  and "-A does not lift" in broken_reason and "capacity check" in broken_reason))
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
    no_ladder = run(launch, env, session="sdc00960", ladder=False)
    no_ladder_reason = no_ladder.get("permissionDecisionReason", "")
    cases.append(("a direct dispatch without a fresh ladder Read is denied naming the ladder",
                  no_ladder.get("permissionDecision") == "deny"
                  and "model_ladder_evidence.md" in no_ladder_reason))
    cases.append(("the ladder deny cites orchestration §5 and names no loaded skill",
                  "orchestration §5" in no_ladder_reason and 'Skill(skill="orchestration")' not in no_ladder_reason))
    repeated = run(launch, env, session="sdc00960", ladder=False)
    cases.append(("an unread ladder denies every launch until the Read",
                  repeated.get("permissionDecision") == "deny"))
    no_skill = run(launch, env, session="sdc00961", skill=False)
    no_skill_reason = no_skill.get("permissionDecisionReason", "")
    cases.append(("a direct dispatch without the orchestration skill is denied naming the Skill retry",
                  no_skill.get("permissionDecision") == "deny"
                  and 'Skill(skill="orchestration")' in no_skill_reason and "CLAUDE.core.md §9" in no_skill_reason))
    neither = run(launch, env, session="sdc00962", ladder=False, skill=False)
    neither_reason = neither.get("permissionDecisionReason", "")
    cases.append(("an unloaded skill and ladder: one deny names both retries",
                  neither.get("permissionDecision") == "deny"
                  and 'Skill(skill="orchestration")' in neither_reason and "model_ladder_evidence.md" in neither_reason))
    tiered_launch = "bash %s -p prompt.md -m fake -G survey" % ok
    arm_slice(env, "sdc00964", ["fanout"])
    sliced = run(tiered_launch, env, session="sdc00964", ladder=False)
    cases.append(("a shape-tiered launch is admitted by the slice of that shape's tier",
                  sliced.get("permissionDecision") is None))
    arm_slice(env, "sdc00965", ["architect"])
    wrong_slice = run(tiered_launch, env, session="sdc00965", ladder=False)
    wrong_reason = wrong_slice.get("permissionDecisionReason", "")
    cases.append(("a slice of another tier denies the launch naming the slice command for the shape's tier",
                  wrong_slice.get("permissionDecision") == "deny"
                  and "model_registry.py ladder-slice fanout" in wrong_reason))
    arm_slice(env, "sdc00966", ["fanout"])
    untiered = run(launch, env, session="sdc00966", ladder=False)
    cases.append(("a launch with no shape tier needs the full ladder whatever slice is in context",
                  untiered.get("permissionDecision") == "deny"
                  and "model_ladder_evidence.md" in untiered.get("permissionDecisionReason", "")))
    with open(os.path.join(project, "jobs.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write('{"jobs": []}')
    fan_no_skill = run("python3 .claude/tools/sidecar_fanout.py jobs.json --out-dir out", env,
                       session="sdc00963", skill=False)
    cases.append(("a sidecar fan-out without the orchestration skill is denied",
                  fan_no_skill.get("permissionDecision") == "deny"
                  and 'Skill(skill="orchestration")' in fan_no_skill.get("permissionDecisionReason", "")))
    fan_dry = run("python3 .claude/tools/sidecar_fanout.py jobs.json --out-dir out --dry-run", env,
                  session="sdc00964", ladder=False, skill=False)
    cases.append(("a dry-run fan-out dispatches nothing and is never denied for doctrine",
                  fan_dry.get("permissionDecision") != "deny"))
    approved_launch = "bash .claude/scripts/ok_sidecar.sh -G survey -m fake"
    approved = run(approved_launch, env, session="sdc01001")
    cases.append(("a tracked canonical direct launch with preflight and ladder is allowed",
                  approved.get("permissionDecision") == "allow"
                  and "canonical sidecar launch" in approved.get("permissionDecisionReason", "")))
    consent = (
        "bash .claude/scripts/ok_sidecar.sh -m fake -e high -D full -G review "
        "-t +Bash -f .claude/reference/consent_brief.md "
        "-S .claude/reference/consent_schema.json "
        "-R .claude/scratch/consent.record.json -P .claude/scratch/consent.progress.jsonl "
        "-l consent -d %s" % project.replace("\\", "/")
    )
    asked = run(consent, env, session="sdc01070")
    cases.append(("a verified +Bash review reaches one user permission ask, not autoapproval",
                  asked.get("permissionDecision") == "ask"
                  and "+Bash" in asked.get("permissionDecisionReason", "")
                  and "fake" in asked.get("permissionDecisionReason", "")))
    arm_ladder(env, "sdc01071"); arm_skill(env, "sdc01071")
    dispatch = checked_run(
        [sys.executable, PRE_BASH_DISPATCH],
        input=json.dumps({"tool_name": "Bash", "session_id": "sdc01071", "cwd": project,
                          "tool_input": {"command": consent}}),
        capture_output=True, text=True, timeout=90, env=env, cwd=project,
    )
    dispatch_hso = json.loads(dispatch.stdout or "{}").get("hookSpecificOutput") or {}
    cases.append(("the real PreToolUse dispatcher passes the +Bash ask through",
                  dispatch.returncode == 0
                  and dispatch_hso.get("permissionDecision") == "ask"
                  and "+Bash" in dispatch_hso.get("permissionDecisionReason", "")))
    settings = _settings_probe.settings(REPO)
    ask_rule = "Bash(bash .claude/scripts/*_sidecar.sh * -t +Bash *)"
    cases.append(("project permissions force a visible +Bash prompt before auto mode",
                  ask_rule in settings.get("permissions", {}).get("ask", [])))
    for index, (label, command) in enumerate((
        ("semicolon", consent + "; git status"),
        ("pipe", consent + " | git status"),
        ("newline", consent + "\ngit status"),
        ("subshell", "(" + consent + ")"),
        ("bash -c", "bash -c '" + consent + "'"),
        ("wider tools", consent.replace("+Bash", "+Bash,+Write")),
        ("permission override", consent + " -p bypassPermissions"),
    )):
        result = run(command, env, session="sdc011%02d" % index)
        cases.append(("an extra-shell review with " + label + " gets no ask",
                      result.get("permissionDecision") != "ask"))
    no_preflight_consent = run(consent, dict(env, HARNESS_SIDECAR_PREFLIGHT="0"),
                               session="sdc01170")
    cases.append(("a consent request with preflight disabled gets no ask",
                  no_preflight_consent.get("permissionDecision") != "ask"))
    without_ladder_consent = run(consent, env, session="sdc01171", ladder=False)
    cases.append(("a consent request without a ladder Read is denied before any ask",
                  without_ladder_consent.get("permissionDecision") == "deny"
                  and names_ladder_route(without_ladder_consent.get("permissionDecisionReason", ""))))
    preflight_state, preflight_refusal = sidecar.run_preflight(approved_launch, project)
    cases.append(("run_preflight reports passed only after the fake check succeeds",
                  preflight_state == "passed" and preflight_refusal is None))
    with patch.dict(os.environ, HARNESS_SIDECAR_PREFLIGHT="0"):
        skipped_state, skipped_refusal = sidecar.run_preflight(approved_launch, project)
    cases.append(("run_preflight reports skipped when preflight is disabled",
                  skipped_state == "skipped" and skipped_refusal is None))
    refusing_state, refusing_text = sidecar.run_preflight(
        "bash .claude/scripts/hot_sidecar.sh -m fake -f brief.md -P run.jsonl", project)
    cases.append(("run_preflight reports refused with the launcher refusal",
                  refusing_state == "refused" and "REFUSING fake" in (refusing_text or "")))
    for label, command in (
        ("a command-substitution carrier gets no decision", approved_launch + " $(echo x)"),
        ("a sudo-prefixed launch gets no decision", "sudo " + approved_launch),
        ("an unsuspend flag gets no decision", "bash %s -U -G survey" % ok),
    ):
        hso = run(command, env, session="sdc01002")
        cases.append((label, hso.get("permissionDecision") is None))
    subdir = os.path.join(project, ".claude", "scripts")
    cases.append(("a canonical launch whose shell cwd is not the project root gets no decision",
                  run(approved_launch, env, session="sdc01004", cwd=subdir).get("permissionDecision") is None))
    cases.append(("the same launch with the shell cwd at the project root is still allowed",
                  run(approved_launch, env, session="sdc01005", cwd=project).get("permissionDecision") == "allow"))
    no_preflight = dict(env, HARNESS_SIDECAR_PREFLIGHT="0")
    cases.append(("a canonical launch with preflight disabled gets no decision",
                  run(approved_launch, no_preflight, session="sdc01003").get("permissionDecision") is None))
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
    cases.append(("a worktree-cwd sidecar ladder deny names the primary checkout's ladder absolutely",
                  wt_denial.get("permissionDecision") == "deny" and primary_ladder in wt_reason))
    cases.append(("a worktree-cwd sidecar ladder deny never names the worktree's stale copy",
                  worktree_ladder not in wt_reason))
    first = run(launch, env, session="sdc00951")
    second = run(launch, env, session="sdc00951", ladder=False)
    cases.append(("one ladder Read authorizes later sidecar dispatches without a re-read",
                  first.get("permissionDecision") is None
                  and second.get("permissionDecision") is None))
    with patch.object(sidecar, "_git_bash", return_value="bash"), patch.object(
        sidecar,
        "run_bounded",
        return_value=types.SimpleNamespace(returncode=None, stdout="", stderr=""),
    ):
        timeout_refusal = sidecar.run_preflight(launch, project)[1]
    cases.append(("E7: a timed-out preflight is refused instead of treated as success",
                  bool(timeout_refusal) and "timed out" in timeout_refusal.lower()))
    # One budget for the whole hook: a preflight past the hook's deadline is refused unstarted.
    asked = []

    def recording_bounded(argv, timeout, **_kwargs):
        asked.append(timeout)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(sidecar, "_git_bash", return_value="bash"), patch.object(
        sidecar, "run_bounded", side_effect=recording_bounded
    ):
        exhausted = sidecar.run_preflight(launch, project, time.monotonic() - 1)
        sidecar.run_preflight(launch, project)
    cases.append(("a preflight with no time left is refused without running the check",
                  exhausted[0] == "refused" and "never started" in (exhausted[1] or "") and len(asked) == 1))
    cases.append(("an unbounded caller keeps the full preflight timeout", asked[-1] == sidecar.PREFLIGHT_TIMEOUT))
    registered = _settings_probe.settings(os.path.join(os.path.dirname(HOOK), ".."))
    dispatch_timeouts = [h.get("timeout") for entries in registered["hooks"].values() for e in entries
                         for h in e.get("hooks", []) if "pre_bash_dispatch" in str(h.get("command", ""))]
    cases.append(("the hook budget plus the emit margin fits the dispatcher's settings timeout",
                  len(dispatch_timeouts) == 1 and sidecar.HOOK_BUDGET_SECONDS < dispatch_timeouts[0]))
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
    failed_helpers = subprocess.run(
        [sys.executable, "-c", "import runpy,sys; sys.modules['approving_gates']=None; "
         "runpy.run_path(sys.argv[1], run_name='__main__')", HOOK],
        input=json.dumps({"tool_name": "Bash", "session_id": "sdc01005", "cwd": project,
                          "tool_input": {"command": approved_launch}}),
        capture_output=True, text=True, timeout=60, env=env,
    )
    try:
        helper_hso = json.loads(failed_helpers.stdout).get("hookSpecificOutput", {})
    except ValueError:
        helper_hso = {}
    cases.append(("a broken shared path-helper import refuses direct dispatch without allowing it",
                  failed_helpers.returncode == 0
                  and helper_hso.get("permissionDecision") == "deny"
                  and "safe launcher path checks are unavailable" in helper_hso.get(
                      "permissionDecisionReason", "")))
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
                "shape": "survey", "workdir": ".", "transport": "opencode"}

    def write_jobs(**changes):
        job = dict(base_job)
        job.update(changes)
        with open(jobs, "w", encoding="utf-8") as fh:
            json.dump([job], fh)

    outside_early = os.path.join(tmp, "outside_early.md")
    with open(outside_early, "w", encoding="utf-8") as fh:
        fh.write("outside\n")
    write_jobs()
    # The fixture job's alias must be available whatever the live roster says.
    planted_registry = _transport_fixture.available_transport(tmp, "opencode")
    fanout_env = dict(env, CLAUDE_PROJECT_DIR=fanout_root, HARNESS_MODEL_REGISTRY=planted_registry)
    fanout_cmd = ("python3 .claude/tools/sidecar_fanout.py .claude/scratch/jobs.json "
                  "--out-dir .claude/scratch/results")
    fanout = run(fanout_cmd, fanout_env, session="sdc00013")
    cases.append(("the existing sidecar gate approves a validated fan-out wrapper",
                  fanout.get("permissionDecision") == "allow"
                  and "budget and quota gates" in fanout.get("permissionDecisionReason", "")))
    fanout_subdir = os.path.join(fanout_root, ".claude")
    cases.append(("a validated fan-out whose shell cwd is not the project root gets no decision",
                  run(fanout_cmd, fanout_env, session="sdc00016",
                      cwd=fanout_subdir).get("permissionDecision") is None))
    cases.append(("a relative interpreter path falls through",
                  run("./python3" + fanout_cmd[len("python3"):], fanout_env,
                      session="sdc00025").get("permissionDecision") is None))
    cases.append(("an arbitrary absolute interpreter path falls through",
                  run("C:/untrusted/python3" + fanout_cmd[len("python3"):], fanout_env,
                      session="sdc00026").get("permissionDecision") is None))
    old_decision = fanout.get("permissionDecision"), fanout.get("permissionDecisionReason")

    def write_raw(spec):
        with open(jobs, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)

    new_job = {"label": "audit", "model": "muse", "promptPath": prompt,
               "shape": "survey", "workdir": ".", "transport": "opencode"}
    write_raw([new_job])
    new_list = run(fanout_cmd, fanout_env, session="sdcnew01")
    cases.append(("a fan-out file in dispatch.js field names is approved as the old-name file is",
                  (new_list.get("permissionDecision"), new_list.get("permissionDecisionReason")) == old_decision))
    write_raw({"jobs": [new_job], "contextPath": prompt, "spillDir": ".claude/scratch/spill"})
    wrapped = run(fanout_cmd, fanout_env, session="sdcnew02")
    cases.append(("a dispatch.js args object is approved as the old-name file is",
                  (wrapped.get("permissionDecision"), wrapped.get("permissionDecisionReason")) == old_decision))
    write_raw({"jobs": [new_job], "contextPath": outside_early})
    cases.append(("a wrapper whose shared context lies outside the project falls through",
                  run(fanout_cmd, fanout_env, session="sdcnew03").get("permissionDecision") is None))
    write_raw([dict(new_job, alias="opus")])
    cases.append(("a conflicting alias/model pair falls through like a malformed file",
                  run(fanout_cmd, fanout_env, session="sdcnew04").get("permissionDecision") is None))
    write_raw([dict(new_job, alias="muse")])
    agreeing = run(fanout_cmd, fanout_env, session="sdcnew05")
    cases.append(("an agreeing alias/model pair is approved as the old-name file is",
                  (agreeing.get("permissionDecision"), agreeing.get("permissionDecisionReason")) == old_decision))
    write_jobs(alias="opus", transport="anthropic", shape="review")
    cases.append(("plan-quota jobs use the same approval path",
                  run(fanout_cmd, fanout_env, session="sdc00014").get("permissionDecision") == "allow"))
    write_jobs(alias="opus", transport="anthropic", shape="author")
    cases.append(("author jobs retain the child auto-permission gate",
                  run(fanout_cmd, fanout_env, session="sdc00015").get("permissionDecision") == "allow"))
    write_jobs(effort="turbo")
    cases.append(("unknown effort pins fall through",
                  run(fanout_cmd, fanout_env, session="sdc00023").get("permissionDecision") is None))
    write_jobs(disclosure="everything")
    cases.append(("unknown disclosure tiers fall through",
                  run(fanout_cmd, fanout_env, session="sdc00024").get("permissionDecision") is None))
    write_jobs()
    cases.append(("--authorize falls through instead of bypassing a budget gate",
                  run(fanout_cmd + " --authorize", fanout_env, session="sdc00016").get("permissionDecision") is None))
    write_jobs(extraArgs=[])
    cases.append(("extra launcher arguments fall through",
                  run(fanout_cmd, fanout_env, session="sdc00017").get("permissionDecision") is None))
    outside = os.path.join(tmp, "outside.md")
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("outside\n")
    write_jobs(contextFiles=[outside])
    cases.append(("inputs outside the project fall through",
                  run(fanout_cmd, fanout_env, session="sdc00018").get("permissionDecision") is None))
    peer_prompt = os.path.join(fanout_root, ".claude", "worktrees", "peer", "prompt.md")
    os.makedirs(os.path.dirname(peer_prompt), exist_ok=True)
    with open(peer_prompt, "w", encoding="utf-8") as fh:
        fh.write("peer prompt\n")
    cases.append(("sidecar dispatch imports the shared path resolver",
                  sidecar._resolve is sidecar.approving_gates._resolve
                  and sidecar._path_has_symlink is sidecar.approving_gates._path_has_symlink))
    cases.append(("the shared project-file helper excludes peer worktree files",
                  not sidecar._regular_project_file(fanout_root, peer_prompt)))
    write_jobs(promptFile=peer_prompt)
    cases.append(("a fan-out prompt in a peer worktree falls through",
                  run(fanout_cmd, fanout_env, session="sdc01004").get("permissionDecision") is None))
    write_jobs()
    cases.append(("outputs outside scratch fall through",
                  run(fanout_cmd.replace(".claude/scratch/results", "results"),
                      fanout_env, session="sdc00019").get("permissionDecision") is None))
    cases.append(("fan-out widths below the safe range fall through",
                  run(fanout_cmd + " --max-parallel 0", fanout_env, session="sdc00021").get("permissionDecision") is None))
    cases.append(("fan-out widths above the safe range fall through",
                  run(fanout_cmd + " --max-parallel 5", fanout_env, session="sdc00022").get("permissionDecision") is None))
    cases.append(("shell compounds fall through",
                  run(fanout_cmd + " && git status", fanout_env, session="sdc00020").get("permissionDecision") is None))
    # Allow-path negatives: _parse_fanout_command is the validator, not a launch parser, and these
    # pin it through the sidecar_argv refactor.
    cases.append(("a $(...) substitution in the fan-out command falls through",
                  run(fanout_cmd + " --max-parallel $(echo 2)", fanout_env, session="sdc00060").get("permissionDecision") is None))
    cases.append(("a backtick in the fan-out command falls through",
                  run(fanout_cmd + " --max-parallel `echo 2`", fanout_env, session="sdc00061").get("permissionDecision") is None))
    cases.append(("a py interpreter falls through",
                  run("py" + fanout_cmd[len("python3"):], fanout_env, session="sdc00062").get("permissionDecision") is None))
    cases.append(("a python3.11 interpreter falls through",
                  run("python3.11" + fanout_cmd[len("python3"):], fanout_env, session="sdc00063").get("permissionDecision") is None))
    outside_jobs = os.path.join(fanout_root, ".claude", "jobs.json")
    with open(outside_jobs, "w", encoding="utf-8") as fh:
        json.dump([base_job], fh)
    cases.append(("a jobs file outside scratch falls through",
                  run(fanout_cmd.replace(".claude/scratch/jobs.json", ".claude/jobs.json"),
                      fanout_env, session="sdc00064").get("permissionDecision") is None))
    linked_jobs = os.path.join(scratch, "linked.json")
    linked_cmd = fanout_cmd.replace("jobs.json", "linked.json")
    try:
        os.symlink(jobs, linked_jobs)
    except OSError:
        linked_jobs = None
    if linked_jobs:
        cases.append(("a symlinked jobs file falls through",
                      run(linked_cmd, fanout_env, session="sdc00065").get("permissionDecision") is None))
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
                                   fanout_env, session="sdc00067").get("permissionDecision") is None))
    cases.append(("a jobs path with a .. segment falls through",
                  run(fanout_cmd.replace(".claude/scratch/jobs.json", ".claude/scratch/../scratch/jobs.json"),
                      fanout_env, session="sdc00066").get("permissionDecision") is None))

    # --- ladder gate on EVERY executed fan-out or launch. The ladder gate must not share the
    # auto-approve recognizer: a shape the approver does not recognize still spends, so it must
    # still be denied without a ladder Read (observed 2026-09-23: a cd-prefixed, piped, worktree-path
    # `--max-parallel 5` fan-out dispatched five billed jobs with no ladder Read).
    # The worktree is planted so the hook can read the jobs file the observed launch named; a jobs file
    # that does not exist yet defers the doctrine to the launchers instead.
    observed_wt = os.path.join(fanout_root, ".claude", "worktrees", "sync-tileset-alignment")
    os.makedirs(os.path.join(observed_wt, ".claude", "scratch", "pr_ready"), exist_ok=True)
    with open(os.path.join(observed_wt, ".claude", "scratch", "pr_ready", "jobs_lenses.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write('{"jobs": []}')
    observed = ("cd %s && python3 .claude/tools/sidecar_fanout.py "
                ".claude/scratch/pr_ready/jobs_lenses.json --max-parallel 5 --out-dir "
                ".claude/scratch/pr_ready/out 2>&1 | tail -20" % observed_wt.replace("\\", "/"))
    wt_tool = (fanout_root.replace("\\", "/")
               + "/.claude/worktrees/peer/.claude/tools/sidecar_fanout.py")
    wt_launcher = (project.replace("\\", "/")
                   + "/.claude/worktrees/wt1/.claude/scripts/ok_sidecar.sh")
    for index, (label, command, case_env) in enumerate((
        ("the observed cd-prefixed piped worktree fan-out", observed, fanout_env),
        ("a worktree copy of sidecar_fanout.py", "python3 %s .claude/scratch/jobs.json" % wt_tool,
         fanout_env),
        ("a fan-out at --max-parallel 5", fanout_cmd + " --max-parallel 5", fanout_env),
        ("a piped fan-out", fanout_cmd + " 2>&1 | tail -20", fanout_env),
        ("a cd && fan-out", "cd %s && %s" % (fanout_root.replace("\\", "/"), fanout_cmd), fanout_env),
        ("a fan-out after a ; separator", "echo go; " + fanout_cmd, fanout_env),
        ("a --flag=value fan-out", fanout_cmd + " --max-parallel=5", fanout_env),
        ("a cd && direct launch", "cd /tmp && bash %s -m fake -f b.md" % ok, env),
        ("a worktree-path direct launch", "bash %s -m fake -f b.md" % wt_launcher, env),
        ("a piped direct launch", "bash %s -m fake -f b.md 2>&1 | tail -5" % ok, env),
        ("a nohup-wrapped direct launch", "nohup bash %s -m fake -f b.md" % ok, env),
        ("a timeout-wrapped direct launch", "timeout 600 bash %s -m fake -f b.md" % ok, env),
        ("an env-wrapped fan-out", "env PYTHONUTF8=1 " + fanout_cmd, fanout_env),
        ("a bash -c carried fan-out", "bash -c '%s'" % fanout_cmd, fanout_env),
        ("a bash -c carried direct launch", "bash -c 'bash %s -m fake -f b.md'" % ok, env),
        # S40: compound shapes and carriers, one per family.
        ("a subshell fan-out", "(%s)" % fanout_cmd, fanout_env),
        ("a brace-group direct launch", "{ bash %s -m fake -f b.md; }" % ok, env),
        ("an if/then-body direct launch", "if true; then bash %s -m fake -f b.md; fi" % ok, env),
        ("a for/do-body fan-out", "for i in 1; do %s; done" % fanout_cmd, fanout_env),
        ("an eval-carried direct launch", "eval 'bash %s -m fake -f b.md'" % ok, env),
        ("a $()-substituted direct launch", "echo $(bash %s -m fake -f b.md)" % ok, env),
        ("a bash -o pipefail -c carried fan-out", "bash -o pipefail -c '%s'" % fanout_cmd,
         fanout_env),
    )):
        gated = run(command, case_env, session="sdc02%03d" % index, ladder=False)
        cases.append(("LADDER: %s without a ladder Read is denied naming the ladder" % label,
                      gated.get("permissionDecision") == "deny"
                      and names_ladder_route(gated.get("permissionDecisionReason", ""))))
    for index, (label, command) in enumerate((
        ("grep of the fan-out tool", "grep -n max-parallel .claude/tools/sidecar_fanout.py"),
        ("cat of the fan-out tool", "cat .claude/tools/sidecar_fanout.py"),
        ("sed of the fan-out tool", "sed -n 1,5p .claude/tools/sidecar_fanout.py"),
        ("git show of the fan-out tool", "git show HEAD:.claude/tools/sidecar_fanout.py"),
        ("py_compile of the fan-out tool", "python3 -m py_compile .claude/tools/sidecar_fanout.py"),
        ("a fan-out --status call",
         "python3 .claude/tools/sidecar_fanout.py --status .claude/scratch/results"),
        ("a fan-out --dry-run call", fanout_cmd + " --dry-run"),
        ("a fan-out --help call", "python3 .claude/tools/sidecar_fanout.py --help"),
        ("a quoted fan-out mention", "echo 'python3 .claude/tools/sidecar_fanout.py x.json'"),
        ("a subshell fan-out --dry-run call", "(%s --dry-run)" % fanout_cmd),
        ("a single-quoted $() fan-out mention",
         "echo '$(python3 .claude/tools/sidecar_fanout.py x.json)'"),
        ("an unrelated Bash call", "git status"),
    )):
        cases.append(("LADDER negative: %s is not judged" % label,
                      run(command, fanout_env, session="sdc03%03d" % index, ladder=False) == {}))
    loaded = run(observed, fanout_env, session="sdc03900")
    cases.append(("LADDER: the observed fan-out in a ladder-loaded session is not denied",
                  loaded.get("permissionDecision") is None))
    cases.append(("LADDER: an unrecognized fan-out shape is never auto-approved",
                  run(fanout_cmd + " --max-parallel 5", fanout_env,
                      session="sdc03901").get("permissionDecision") is None))

    # --- background: a backgrounded lib launcher is never denied, and the hook adds no pointer, wake route or
    # background advisory to the admitted call (sidecar_dispatch.md steps 5 and 6 carry those facts).
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
    cases.append(("a backgrounded launch without -R carries no advisory", late_advice(allowed_no_r) == []))
    allowed_with_r = run("bash %s -R %s -m fake" % (reexec, record), env, session="sdc00031",
                         run_in_background=True)
    cases.append(("a backgrounded launch with -R is not denied", allowed_with_r.get("permissionDecision") is None))
    cases.append(("a backgrounded launch with -R carries no .exit pointer, wake route or recipe line",
                  late_advice(allowed_with_r) == [] and ".exit" not in allowed_with_r.get("additionalContext", "")))
    foreground = run("bash %s -R %s -m fake" % (reexec, record), env, session="sdc00032")
    cases.append(("a foreground launch with -R carries no advisory", late_advice(foreground) == []))
    detached = run("bash %s -R %s -X -m fake" % (reexec, record), env, session="sdc00033")
    cases.append(("a foreground -X launch is not denied and carries no detached advisory or wake route",
                  detached.get("permissionDecision") is None and late_advice(detached) == []))
    for index in range(2):
        again = run("bash %s -R %s -m fake" % (reexec, record), env, session="sdc00039",
                    run_in_background=True)
        cases.append(("launch %d of one session carries no recipe pointer or card" % (index + 1),
                      late_advice(again) == [] and again.get("additionalContext", "") == ""))
    cases.append(("the hook keeps no .exit, detached or background advisory builders",
                  not any(hasattr(sidecar, name) for name in (
                      "watcher_live", "_backgrounds_lib_launcher", "FANOUT_BACKGROUND_ADVISORY", "DETACHED_ADVISORY",
                      "WATCH_TAIL", "launch_card", "size_lines"))))
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

    for index in range(2):
        fanout_bg = run(fanout_cmd, fanout_env, session="sdc00040", run_in_background=True)
        cases.append(("backgrounded fan-out %d of one session is allowed and carries no advisory, recipe or card" % (index + 1),
                      fanout_bg.get("permissionDecision") == "allow" and late_advice(fanout_bg) == []
                      and "--detach" not in fanout_bg.get("additionalContext", "")))

    fanout_detached = run(fanout_cmd + " --detach", fanout_env, session="sdc00049", run_in_background=True)
    cases.append(("a backgrounded fan-out that detaches its driver gets no background-limit advisory",
                  fanout_detached.get("permissionDecision") == "allow" and late_advice(fanout_detached) == []))
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
    arm_ladder(real_env, "sdc00041"); arm_skill(real_env, "sdc00041")
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
                  "call and carries no .exit advisory" % real_launcher,
                  not real_crashed
                  and real.returncode == 0
                  and real_hso.get("permissionDecision") is None
                  and ("read %s.exit" % real_record) not in real_hso.get("additionalContext", "")
                  and late_advice(real_hso) == []))

    # Exercise the actual PreToolUse dispatcher in clean -> planted -> clean order. The preflight
    # is stubbed off; this proves the variable deny is delivered on the hook channel before launch.
    def real_channel(command, session):
        arm_ladder(real_env, session); arm_skill(real_env, session)
        result = subprocess.run(
            [sys.executable, PRE_BASH_DISPATCH],
            input=json.dumps({"tool_name": "Bash", "session_id": session,
                              "tool_input": {"command": command}}),
            capture_output=True, text=True, timeout=90, env=real_env, cwd=real_root)
        output = (result.stdout or "").strip()
        return json.loads(output).get("hookSpecificOutput", {}) if output else {}

    tracked_record = os.path.join(tmp, "literal.record.json").replace("\\\\", "/")
    clean_cmd = "bash .claude/scripts/%s -R %s -l literal -m sonnet" % (real_launcher, tracked_record)
    clean_before = real_channel(clean_cmd, "sdcvar10")
    planted = real_channel("bash .claude/scripts/%s -R .claude/scratch/$n/exec.record.json -l test -m sonnet"
                           % real_launcher, "sdcvar11")
    clean_after = real_channel(clean_cmd, "sdcvar12")
    cases.append(("real PreToolUse channel sequence is clean -> variable deny -> clean",
                  clean_before.get("permissionDecision") != "deny"
                  and planted.get("permissionDecision") == "deny"
                  and "Launch with literal -R and -l values" in planted.get("permissionDecisionReason", "")
                  and clean_after.get("permissionDecision") != "deny"))
    assigned_cmd = ("D=.claude/scratch/x; bash .claude/scripts/%s -R $D/r.json -l lbl -m sonnet"
                    % real_launcher)
    assigned_real = real_channel(assigned_cmd, "sdcvar13")
    heredoc_real = real_channel(
        "cat > ignored.txt <<'EOF'\n"
        "bash .claude/scripts/%s -R $n/r.json -l label\nEOF" % real_launcher, "sdcvar14")
    loop_real = real_channel(
        "for n in a b; do bash .claude/scripts/%s -R $n/r.json -l x-$n -m sonnet; done"
        % real_launcher, "sdcvar15")
    substitution_real = real_channel(
        "bash .claude/scripts/%s -R $(pwd)/r.json -l label -m sonnet" % real_launcher, "sdcvar16")
    cases.append(("real PreToolUse allows assigned -R and heredoc data, but denies loop and $() values",
                  assigned_real.get("permissionDecision") != "deny"
                  and heredoc_real.get("permissionDecision") is None
                  and loop_real.get("permissionDecision") == "deny"
                  and substitution_real.get("permissionDecision") == "deny"))

    # --- context: an admitted launch carries no reference injection, first, later or after a compaction
    cases.append(("the first launch injects no reference, card or pointer", context(launch, env) == ""))
    cases.append(("the second launch gets no pointer either", context(launch, env) == ""))
    precompact(env)
    cases.append(("a compaction re-arms nothing", context(launch, env) == ""))

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

    # Unexpanded shell variables in tracking fields are rejected before preflight/spend.
    var_record = run("bash %s -R .claude/scratch/$n/exec.record.json -l test" % ok,
                     env, session="sdcvar01")
    cases.append(("a variable in -R is denied before launch",
                  var_record.get("permissionDecision") == "deny"
                  and "Launch with literal -R and -l values" in var_record.get("permissionDecisionReason", "")))
    var_label = run("bash %s -l dpl-$n -R literal.record.json" % ok,
                    env, session="sdcvar02")
    cases.append(("a variable in -l is denied before launch",
                  var_label.get("permissionDecision") == "deny"
                  and "Launch with literal -R and -l values" in var_label.get("permissionDecisionReason", "")))
    same_command_assignment = run("D=.claude/scratch/x; bash %s -R $D/r.json -l lbl" % ok,
                                   env, session="sdcvar06")
    cases.append(("same-command literal assignments resolve before checking -R",
                  same_command_assignment.get("permissionDecision") is None))
    heredoc_command = ("cat > ignored.txt <<'EOF'\n"
                       "bash .claude/scripts/ok_sidecar.sh -R $n/r.json -l label\nEOF")
    cases.append(("a launcher mention in a heredoc body is treated as data",
                  run(heredoc_command, env, session="sdcvar07") == {}))
    loop_command = "for n in a b; do bash %s -R $n/r.json -l x-$n; done" % ok
    cases.append(("an unexpanded loop variable in -R and -l is still denied",
                  run(loop_command, env, session="sdcvar08").get("permissionDecision") == "deny"))
    var_substitution = run("bash %s -R $(printf literal.record.json) -l test" % ok,
                           env, session="sdcvar05")
    cases.append(("a command substitution in -R is denied before launch",
                  var_substitution.get("permissionDecision") == "deny"))
    literal_result = run("bash %s -R literal.record.json -l literal" % ok,
                         env, session="sdcvar03")
    cases.append(("literal -R/-l launch passes the variable check",
                  literal_result.get("permissionDecision") is None))
    cases.append(("fan-out calls are unaffected by literal tracking validation",
                  run("python3 .claude/tools/sidecar_fanout.py --help", fanout_env,
                      session="sdcvar04", ladder=False) == {}))

    cases.extend(currency_cases(tmp, ok, env))
    cases.extend(plan_cases(tmp))
    cases.extend(long_context_cases(tmp, ok, env))

    failures = [label for label, ok_ in cases if not ok_]
    for label, ok_ in cases:
        print("%-4s %s" % ("ok" if ok_ else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
