#!/usr/bin/env python3
"""RED-first proof: a launcher-read path that does not resolve refuses `sidecar_fanout.py --detach` before anything
spawns, naming the job, the field, the path as written and the root it resolved against, and the preflight
(`dispatch_size.preflight_lines`) calls the same jobs file unmet, from the one resolver `sidecar_argv.launcher_inputs`.

Relative paths resolve against the launching checkout (the cwd). A jobs file whose `contextPath` names a file only
another checkout holds launched fine from that checkout and failed after launch from a worktree.

Fakes only: detach and run are replaced, nothing is billed.

Run: python3 .claude/tests/test_sidecar_fanout_unresolved_input.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
import dispatch_size  # noqa: E402
import model_registry  # noqa: E402

spec = importlib.util.spec_from_file_location("sf_unresolved", os.path.join(CLAUDE, "tools", "sidecar_fanout.py"))
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

CALLS = {"detach": 0, "run": 0}


def _detach(*a, **k):
    CALLS["detach"] += 1
    return 0


class Launched(Exception):
    pass


def _run(*a, **k):
    CALLS["run"] += 1
    raise Launched()


sf.detach_driver = _detach
sf.run_jobs = _run
sf.duplicate_launches = lambda planned: ""
sf.admission_refusal = lambda jobs, data, read=None, now=None: None
sf.doctrine_refusal = lambda argv: None

TMP = tempfile.mkdtemp(prefix="sfanout_unresolved_")
HOLDER = os.path.join(TMP, "holder")
LAUNCH = os.path.join(TMP, "launch")
for d in (HOLDER, LAUNCH):
    os.makedirs(os.path.join(d, "ui-lock"))
for name in ("reader-common.md", "brief.md"):
    with open(os.path.join(HOLDER, "ui-lock", name), "w", encoding="utf-8") as fh:
        fh.write("x\n")
with open(os.path.join(LAUNCH, "ui-lock", "brief.md"), "w", encoding="utf-8") as fh:
    fh.write("x\n")

DATA = model_registry.load()
ALIAS = next(r["alias"] for r in DATA["models"] if r.get("transport") == "anthropic" and r.get("alias")
             and model_registry.model_available(r["id"], DATA))


def jobs_spec(field):
    job = {"label": "reader", "alias": ALIAS, "effort": "high", "kind": "scout", "plan": "anthropic",
           "promptFile": "ui-lock/brief.md"}
    spec = {"jobs": [job]}
    if field == "contextPath":
        spec["contextPath"] = "ui-lock/reader-common.md"
    else:
        job["contextFiles"] = ["ui-lock/reader-common.md"]
    return spec


def launch(field):
    path = os.path.join(TMP, "jobs-%s.json" % field)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(jobs_spec(field), fh)
    CALLS.update(detach=0, run=0)
    out, err = io.StringIO(), io.StringIO()
    previous = os.getcwd()
    os.chdir(LAUNCH)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = sf.main([path, "--detach", "--out-dir", os.path.join(TMP, "out-" + field)])
            except Launched:
                code = "launched"
    finally:
        os.chdir(previous)
    return code, out.getvalue() + err.getvalue()


CASES = []


def refuses(field):
    def body():
        code, text = launch(field)
        return (code == 2 and CALLS["detach"] == 0 and CALLS["run"] == 0 and "reader" in text
                and "ui-lock/reader-common.md" in text and "resolved against" in text and LAUNCH.replace("\\", "/") in text)
    return body


def preflight(field):
    def body():
        lines = dispatch_size.preflight_lines(jobs_spec(field), root=LAUNCH, session_id="x", transcript_path="x")
        hit = [line for line in lines if "ui-lock/reader-common.md" in line]
        return (not any("preflight: clean" in line for line in lines) and len(hit) == 1
                and "preflight: unmet" in hit[0] and "reader" in hit[0] and LAUNCH.replace("\\", "/") in hit[0])
    return body


def resolving_input_is_clean():
    with open(os.path.join(LAUNCH, "ui-lock", "reader-common.md"), "w", encoding="utf-8") as fh:
        fh.write("x\n")
    try:
        lines = dispatch_size.preflight_lines(jobs_spec("contextPath"), root=LAUNCH, session_id="x",
                                              transcript_path="x")
        return not any("ui-lock/reader-common.md" in line for line in lines)
    finally:
        os.remove(os.path.join(LAUNCH, "ui-lock", "reader-common.md"))


for field in ("contextPath", "contextFiles"):
    CASES.append(("--detach refuses a missing %s from another cwd before spawning" % field, refuses(field)))
    CASES.append(("the preflight is unmet for a missing %s" % field, preflight(field)))
CASES.append(("a launcher input that resolves adds no unmet line", resolving_input_is_clean))

failed = []
for name, fn in CASES:
    try:
        ok = bool(fn())
    except Exception as exc:
        ok = False
        name += "  [%s: %s]" % (type(exc).__name__, exc)
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failed.append(name)
print("%d/%d passed" % (len(CASES) - len(failed), len(CASES)))
sys.exit(1 if failed else 0)
