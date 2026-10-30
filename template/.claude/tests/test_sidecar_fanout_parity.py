#!/usr/bin/env python3
"""Proof: `tools/sidecar_fanout.py` takes the same job spec and gives the same return as
`workflows/dispatch.js` (plan `computed-currency-pick-2026-09-28.md` D5).

1. A jobs file in dispatch.js field names (`promptPath`, `model`, `contextPath`, optionally wrapped
   as `{jobs, contextPath, spillDir}`) plans; a conflicting native/dispatch.js pair refuses.
2. Every ended job writes `<outDir>/<label>.md` holding its run's final message (the stdout
   `result` event the `-R` record summarizes), so the child needs no Write tool.
3. A multi-job run at --max-parallel > 1 hands every child the single-flight guard as a `-C` file
   rendered from `.claude/guards/single_flight.json`, the source dispatch.js's guard is pinned to.

The end-to-end cases run `main()` against a planted registry whose launcher is a fake bash script,
so no provider is called. The planted row claims the tiers its jobs' shapes imply (`roles`), because `main()`
runs the launchers' tier fit before anything starts and refuses a pin outside the shape's tier.

Run: python3 .claude/tests/test_sidecar_fanout_parity.py
"""
import importlib.util
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("SIDECAR_FANOUT_POLL_SEC", "0.05")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MOD = os.path.join(ROOT, "tools", "sidecar_fanout.py")
spec = importlib.util.spec_from_file_location("sf_parity", MOD)
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

TMP = tempfile.mkdtemp(prefix="sfanout_parity_")
PROMPT = os.path.join(TMP, "p.md")
PROMPT2 = os.path.join(TMP, "p2.md")
CTX = os.path.join(TMP, "c.md")
for path, text in ((PROMPT, "lens body\n"), (PROMPT2, "other body\n"), (CTX, "shared context\n")):
    open(path, "w", encoding="utf-8").write(text)


def posix(p):
    return str(p).replace("\\", "/")


FAKE = os.path.join(TMP, "fake_launcher.sh")
with open(FAKE, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(
        "#!/usr/bin/env bash\n"
        "orig=(\"$@\"); record=''; label=''\n"
        "while [ $# -gt 0 ]; do case \"$1\" in -R) record=\"$2\"; shift 2;; -l) label=\"$2\"; shift 2;; *) shift;; esac; done\n"
        "printf '%s\\n' \"${orig[@]}\" > \"${record}.args\"\n"
        "if [ \"$label\" != mute ]; then\n"
        "  printf '%s\\n' '{\"type\":\"system\",\"subtype\":\"init\"}' "
        "\"{\\\"type\\\":\\\"result\\\",\\\"subtype\\\":\\\"success\\\",\\\"result\\\":\\\"deliverable of $label\\\\nsecond line\\\"}\" > \"${record}.out\"\n"
        "fi\n"
        "echo \"${SIDECAR_FULL_WINDOW:-}\" > \"${record}.fw\"\n"
        "echo 0 > \"${record}.exit\"\n"
    )

DATA = {
    "transports": {"fake": {"costModel": "plan-quota", "launcher": posix(FAKE)}},
    "models": [{"transport": "fake", "id": "fake-model-1", "alias": "fakem", "roles": ["executor", "fanout"]}],
}
sf.model_registry.load = lambda *a, **k: DATA

SOURCE = json.load(open(os.path.join(ROOT, "guards", "single_flight.json"), encoding="utf-8"))


def rendered(shell):
    return (SOURCE["header"] + "\n" + SOURCE["rule"]
            + (SOURCE["withShell"] if shell else SOURCE["noShell"]) + SOURCE["stop"] + "\n")


def plan(job):
    return sf.plan_job(dict(job), DATA, TMP, False)


def refuses(job, needle):
    try:
        plan(job)
    except sf.JobError as err:
        return needle in str(err)
    except Exception:
        return False
    return False


def flag_values(argv, f):
    return [argv[i + 1] for i, tok in enumerate(argv) if tok == f and i + 1 < len(argv)]


def run_main(jobs_obj, name, *extra):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    jobs_path = os.path.join(d, "jobs.json")
    json.dump(jobs_obj, open(jobs_path, "w", encoding="utf-8"))
    out = os.path.join(d, "out")
    code = sf.main([jobs_path, "--out-dir", out, *extra])
    return code, out


def args_of(out, label):
    try:
        return open(os.path.join(out, label + ".record.json.args"), encoding="utf-8").read().splitlines()
    except OSError:
        return None


CASES = []


def case(name, fn):
    CASES.append((name, fn))


# ---- 1. one job spec ---------------------------------------------------------------------------
DJOB = {"label": "dj", "model": "fake-model-1", "promptPath": PROMPT, "contextPath": CTX, "kind": "lens"}

case("a job in dispatch.js field names plans",
     lambda: flag_values(plan(DJOB)[1], "-f") == [posix(PROMPT)]
     and flag_values(plan(DJOB)[1], "-m") == ["fake-model-1"]
     and flag_values(plan(DJOB)[1], "-C") == [posix(CTX)])
case("an agreeing native/dispatch.js pair plans",
     lambda: flag_values(plan(dict(DJOB, promptFile=PROMPT, alias="fake-model-1",
                                   contextFiles=[CTX]))[1], "-f") == [posix(PROMPT)])
case("promptFile and promptPath that differ refuse",
     lambda: refuses(dict(DJOB, promptFile=PROMPT2), "promptPath"))
case("alias and model that differ refuse",
     lambda: refuses(dict(DJOB, alias="fakem-other"), "model"))
case("contextFiles and contextPath that differ refuse",
     lambda: refuses(dict(DJOB, contextFiles=[PROMPT2]), "contextPath"))
case("a non-string dispatch.js field refuses",
     lambda: refuses(dict(DJOB, contextPath=[CTX]), "contextPath"))

WRAP = {"contextPath": posix(CTX), "jobs": [
    {"label": "w1", "model": "fakem", "promptPath": PROMPT, "kind": "author"},
    {"label": "w2", "model": "fakem", "promptPath": PROMPT2, "kind": "lens"},
]}
_wrap_code, _wrap_out = run_main(WRAP, "wrap", "--max-parallel", "2")
case("a dispatch.js args object {jobs, contextPath} runs end to end",
     lambda: _wrap_code == 0 and args_of(_wrap_out, "w1") is not None and args_of(_wrap_out, "w2") is not None)
case("the wrapper's shared contextPath reaches every job as -C",
     lambda: posix(CTX) in flag_values(args_of(_wrap_out, "w1"), "-C")
     and posix(CTX) in flag_values(args_of(_wrap_out, "w2"), "-C"))
case("a wrapper spillDir that conflicts with --out-dir refuses before launch",
     lambda: run_main(dict(WRAP, spillDir=posix(os.path.join(TMP, "elsewhere"))), "conflict",
                      "--max-parallel", "2")[0] == 2)

# ---- 2. one return -----------------------------------------------------------------------------
case("every ended job writes <label>.md equal to its final message",
     lambda: open(os.path.join(_wrap_out, "w1.md"), encoding="utf-8").read() == "deliverable of w1\nsecond line"
     and open(os.path.join(_wrap_out, "w2.md"), encoding="utf-8").read() == "deliverable of w2\nsecond line")

_mute_code, _mute_out = run_main([{"label": "mute", "alias": "fakem", "promptFile": PROMPT, "kind": "lens"}],
                                 "mute")
case("a job with no final message fails its close check and writes no <label>.md",
     lambda: _mute_code == 1 and not os.path.exists(os.path.join(_mute_out, "mute.md")))

CODEX = os.path.join(TMP, "codex.out.json")
with open(CODEX, "w", encoding="utf-8") as fh:
    fh.write('{"type":"thread.started","thread_id":"t"}\n'
             '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}\n'
             '{"type":"item.completed","item":{"type":"agent_message","text":"codex final"}}\n'
             '{"type":"turn.completed","usage":{}}\n')
case("a codex event stream's final message is its last agent_message",
     lambda: sf.dispatch_result.final_message_of(CODEX) == "codex final")

# ---- 2b. a legal context suffix on the model id ------------------------------------------------
case("a `[1m]` model id plans the bare row (the launcher adds the suffix for a 1M-window row)",
     lambda: flag_values(plan(dict(DJOB, model="fake-model-1[1m]"))[1], "-m") == ["fake-model-1"])
case("a `[1m]` alias plans the bare alias",
     lambda: flag_values(plan(dict(DJOB, model="fakem[1m]"))[1], "-m") == ["fakem"])
case("a `[1m]` model beside the same bare native alias plans",
     lambda: flag_values(plan(dict(DJOB, model="fake-model-1[1m]", alias="fakem"))[1], "-m") == ["fakem"])
case("an unknown context suffix still refuses",
     lambda: refuses(dict(DJOB, model="fake-model-1[2m]"), "unknown model"))

# ---- 3. the single-flight guard ----------------------------------------------------------------
def guard_files(out, label):
    return [p for p in flag_values(args_of(out, label) or [], "-C") if "single_flight" in p]


case("a two-job parallel run hands both children one guard -C file",
     lambda: len(guard_files(_wrap_out, "w1")) == 1 and len(guard_files(_wrap_out, "w2")) == 1)
case("the author child's guard is the shell variant rendered from the shared source",
     lambda: open(guard_files(_wrap_out, "w1")[0], encoding="utf-8").read() == rendered(True))
case("the survey child's guard is the no-shell variant rendered from the shared source",
     lambda: open(guard_files(_wrap_out, "w2")[0], encoding="utf-8").read() == rendered(False))

_serial_code, _serial_out = run_main(WRAP, "serial", "--max-parallel", "1")
case("--max-parallel 1 adds no guard",
     lambda: _serial_code == 0 and not guard_files(_serial_out, "w1") and not guard_files(_serial_out, "w2"))
_solo_code, _solo_out = run_main({"jobs": WRAP["jobs"][:1]}, "solo", "--max-parallel", "4")
case("a single job adds no guard",
     lambda: _solo_code == 0 and not guard_files(_solo_out, "w1"))


_fw_code, _fw_out = run_main({"jobs": [
    {"label": "fw1", "alias": "fakem", "effort": "high", "kind": "scout", "promptFile": PROMPT, "window": "full"},
    {"label": "fw2", "alias": "fakem", "effort": "high", "kind": "scout", "promptFile": PROMPT2}]}, "fullwin", "--max-parallel", "1")


def _fw(label):
    try:
        return open(os.path.join(_fw_out, label + ".record.json.fw"), encoding="utf-8").read().strip()
    except OSError:
        return None


case("a job with window full launches with SIDECAR_FULL_WINDOW=1; the default job's env does not carry it",
     lambda: _fw_code == 0 and _fw("fw1") == "1" and _fw("fw2") == "")
case("a window value other than full is refused before launch",
     lambda: refuses({"label": "fw3", "alias": "fakem", "effort": "high", "kind": "scout", "promptFile": PROMPT, "window": "huge"},
                     "`window`"))


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
