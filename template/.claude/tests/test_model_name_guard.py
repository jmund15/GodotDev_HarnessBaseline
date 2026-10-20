#!/usr/bin/env python3
"""Proof for hooks/model_name_guard.py, run through pre_edit_dispatch.py: mechanism-layer files
name roles, never models (instruction_quality §12).

Real PreToolUse payloads go to the live dispatcher on stdin against a planted registry with a
synthetic roster (HARNESS_MODEL_REGISTRY) and a planted exemption file
(HARNESS_MODEL_NAME_EXEMPTIONS). Each case asserts on the guard's own channel: a deny whose reason
carries MARK, an additionalContext carrying MARK, or silence. An exit outside {0, 2} or a traceback
is CRASH, never allow. A deny from another sub-hook hides this guard, so it fails the case.

    python3 .claude/tests/test_model_name_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(CLAUDE, ".."))
DISPATCH = os.path.join(CLAUDE, "hooks", "pre_edit_dispatch.py")
EXEMPTIONS = os.path.join(CLAUDE, "hooks", "model_name_exemptions.json")
MARK = "[model-name-guard]"
FAILURES = []

REGISTRY = {
    "transports": {"t1": {"effortValues": ["low", "high"]}},
    "aliasHistory": {"alpha": [{"id": "vendor-alpha-2"}]},
    "models": [
        {"id": "vendor-alpha-2", "alias": "alpha", "label": "Alpha 2", "family": "alpha", "transport": "t1"},
        {"id": "vendor-beta-1", "alias": "beta", "label": "Beta 1", "family": "beta", "transport": "t1",
         "servedIds": ["vendor-beta-1-0801"]},
        {"id": "spark-9-free", "alias": "spark", "label": "Spark 9", "family": "spark", "transport": "t1"},
    ],
}
PLANTED_EXEMPTIONS = {"exemptions": [
    {"glob": "skills/exempt_probe/**", "justification": "planted: a measurement names what it measures"},
]}


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:400]))


def run(payload, env):
    r = subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=120, env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), r.stderr or ""


def channel(rc, out, err):
    """This guard's channel: deny | advise | silent, else CRASH or other-deny / other-block."""
    if rc not in (0, 2) or "Traceback" in err:
        return "CRASH"
    if rc == 2:
        return "block" if MARK in err else "other-block"
    spec = (json.loads(out).get("hookSpecificOutput") or {}) if out and out != "{}" else {}
    if spec.get("permissionDecision") == "deny":
        return "deny" if MARK in (spec.get("permissionDecisionReason") or "") else "other-deny"
    return "advise" if MARK in (spec.get("additionalContext") or "") else "silent"


def edit(path, new, old="placeholder line"):
    return {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "mng00001",
            "tool_input": {"file_path": path, "old_string": old, "new_string": new}}


def write(path, content):
    return {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "mng00001",
            "tool_input": {"file_path": path, "content": content}}


def main():
    tmp = tempfile.mkdtemp(prefix="model_name_guard_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    registry = os.path.join(tmp, "registry.json")
    with open(registry, "w", encoding="utf-8") as fh:
        json.dump(REGISTRY, fh)
    exemptions = os.path.join(tmp, "exemptions.json")
    with open(exemptions, "w", encoding="utf-8") as fh:
        json.dump(PLANTED_EXEMPTIONS, fh)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8",
               HARNESS_MODEL_REGISTRY=registry, HARNESS_MODEL_NAME_EXEMPTIONS=exemptions)
    subprocess.run([sys.executable, os.path.join(CLAUDE, "hooks", "skill_load_marker.py")],
                   input=json.dumps({"tool_name": "Skill", "session_id": "mng00001",
                                     "tool_input": {"skill": "instruction_quality"}}),
                   capture_output=True, text=True, timeout=60, env=env)

    def expect(label, payload, want, run_env=None):
        rc, out, err = run(payload, run_env or env)
        got = channel(rc, out, err)
        check("%s -> %s" % (label, want), got == want, "got %s; rc=%s out=%s err=%s" % (got, rc, out, err))
        return out

    skill = os.path.join(CLAUDE, "skills", "x", "SKILL.md")
    out = expect("an Edit adding `alpha` to skills/x/SKILL.md", edit(skill, "Dispatch it on `alpha`."), "deny")
    reason = ((json.loads(out or "{}").get("hookSpecificOutput") or {}).get("permissionDecisionReason") or "")
    check("the deny names the name, the file, the rule and both fixes",
          all(s in reason for s in ("alpha", "skills/x/SKILL.md", "name roles, never models",
                                    "model_registry.py for-role", "model_name_exemptions.json")), reason)
    expect("the same Edit to an exempt path", edit(os.path.join(CLAUDE, "skills", "exempt_probe", "SKILL.md"),
                                                   "Dispatch it on `alpha`."), "silent")
    expect("an Edit keeping an existing `alpha` unchanged",
           edit(skill, "Dispatch it on `alpha`, then review.", old="Dispatch it on `alpha`."), "silent")
    expect("`spark` in plain prose", edit(skill, "Add a spark effect when the projectile lands."), "silent")
    expect("`spark` backticked", edit(skill, "Pin `spark` for fan-out."), "deny")
    expect('model: "spark"', edit(skill, 'model: "spark"'), "deny")
    expect("a launcher -m spark", edit(skill, "bash .claude/scripts/x_sidecar.sh -m spark --check"), "deny")
    expect("an alias-effort pair spark·high", edit(skill, "Run the lens at spark·high."), "deny")
    expect("a registered id in prose", edit(skill, "Route it to vendor-beta-1 today."), "deny")
    expect("a served id in prose", edit(skill, "The child served vendor-beta-1-0801."), "deny")
    expect("a versioned label in prose", edit(skill, "Alpha 2 handles this."), "deny")
    expect("a role name", edit(skill, "Resolve the `fanout` tier with for-role."), "silent")
    expect("`spark` as a quoted key or subscript", edit(skill, 'row = {"spark": 1}; x = row["spark"]'), "silent")
    expect("`spark` inside a prose code span", edit(skill, "Set `[ExportGroup(\"Spark Visuals\")]` here."),
           "silent")
    expect("a code span listing names", edit(skill, "Pins in scope: `beta|spark`."), "deny")

    expect("a Write of a new hooks/ file carrying alpha",
           write(os.path.join(CLAUDE, "hooks", "zz_model_name_probe.py"), 'MODEL = "alpha"\n'), "deny")
    expect("a Write of a new tests/ file carrying alpha",
           write(os.path.join(CLAUDE, "tests", "zz_model_name_probe.py"), 'MODEL = "alpha"\n'), "advise")
    planted_hooks = os.path.join(tmp, "repo", ".claude", "hooks")
    os.makedirs(planted_hooks, exist_ok=True)
    existing = os.path.join(planted_hooks, "existing.py")
    with open(existing, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('MODEL = "alpha"\n')
    expect("a Write keeping the file's existing alpha", write(existing, 'MODEL = "alpha"\nX = 1\n'), "silent")
    expect("a Write adding a second alpha", write(existing, 'MODEL = "alpha"\nOTHER = "alpha"\n'), "deny")
    expect("CLAUDE.core.md gaining a pin", edit(os.path.join(CLAUDE, "CLAUDE.core.md"), "Pin `beta`."), "deny")

    for rel in (".claude/auto-memory/x.md", ".claude/scratch/x.md", "Scripts/Spells/Probe.cs",
                ".claude/reference/model_ladder_evidence.md"):
        expect("%s outside the mechanism layer" % rel,
               edit(os.path.join(ROOT, *rel.split("/")), "Dispatch it on `alpha` (vendor-alpha-2)."), "silent")

    broken = dict(env, HARNESS_MODEL_REGISTRY=os.path.join(tmp, "missing.json"))
    expect("an unreadable registry advises", edit(skill, "Dispatch it on `alpha`."), "advise", broken)

    real = dict(env)
    real.pop("HARNESS_MODEL_NAME_EXEMPTIONS")
    expect("the shipped exemptions cover a benchmark campaign file",
           edit(os.path.join(CLAUDE, "scripts", "benchmark_campaign", "campaigns", "probe.env"),
                'BENCH_MODEL="alpha"'), "silent", real)
    reference = '{"referencePin": {"alias": "alpha", "effort": "high"}}'
    expect("the measured reference pin's exact catalog path is exempt",
           edit(os.path.join(CLAUDE, "scripts", "benchmark_campaign", "core", "instruments.json"), reference),
           "silent", real)
    expect("a sibling core catalog still cannot name a model",
           edit(os.path.join(CLAUDE, "scripts", "benchmark_campaign", "core", "sibling.json"), reference),
           "deny", real)
    try:
        with open(EXEMPTIONS, encoding="utf-8") as fh:
            entries = json.load(fh)["exemptions"]
        ok = bool(entries) and all(isinstance(e.get("glob"), str) and e["glob"].strip()
                                   and isinstance(e.get("justification"), str) and e["justification"].strip()
                                   for e in entries)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        ok, entries = False, exc
    check("every shipped exemption is a glob plus its justification", ok, entries)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for f in FAILURES:
        print("  " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
