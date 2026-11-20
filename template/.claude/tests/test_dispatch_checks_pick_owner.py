#!/usr/bin/env python3
"""Proof: a job recorded `pick: owner` may pin a row outside its tier; no other pin is lifted by owner words.

`hooks/_dispatch_checks.py` `tier_fit` and `seat_verdicts` pass an out-of-tier row only when the job's pick is
`owner` (`owner_pinned=True`, or a seat whose `pick` is `owner`), and append one `pick: owner` line to `notes`
so the deviation shows in the dispatch record. The owner's words in the transcript never lift a row. The four
routes (provider guard, sidecar context hook, `lens_briefs.py`, `sidecar_fanout.py`) decide from the job's own
recorded pick. Route cases pick an unclaimed row from the live registry (never named here), so they keep meaning
as the registry moves; transcript rows are cut from the captured live rows in `fixtures/owner_text/live_rows.jsonl`.

    python3 .claude/tests/test_dispatch_checks_pick_owner.py < /dev/null
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
TMP = tempfile.mkdtemp(prefix="pick_owner_")

for name in ("CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR"):
    os.environ.pop(name, None)

LIVE = {item["label"]: item["row"] for item in map(
    json.loads, open(os.path.join(HERE, "fixtures", "owner_text", "live_rows.jsonl"), encoding="utf-8"))}

cases = []


def case(name, ok, detail=""):
    cases.append((name, bool(ok), detail))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompt(text, **extra):
    row = copy.deepcopy(LIVE["owner_prompt"])
    row["message"]["content"] = text
    row.update(extra)
    return row


def transcript(*rows, name="session.jsonl"):
    path = os.path.join(tempfile.mkdtemp(dir=TMP), name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(json.dumps(row) + "\n" for row in rows)
    return path


try:
    dc = load("dc_pick_owner", os.path.join(CLAUDE, "hooks", "_dispatch_checks.py"))
    import model_registry
    wpg = load("wpg_pick_owner", os.path.join(CLAUDE, "hooks", "workflow_provider_guard.py"))
    ctx = load("ctx_pick_owner", os.path.join(CLAUDE, "hooks", "sidecar_dispatch_context.py"))
    lens_briefs = load("lb_pick_owner", os.path.join(CLAUDE, "tools", "lens_briefs.py"))
    sf = load("sf_pick_owner", os.path.join(CLAUDE, "tools", "sidecar_fanout.py"))
except Exception as exc:
    case("the modules import", False, "%s: %s" % (type(exc).__name__, exc))
    dc = None


def row(transport, rid, alias, label, family, version, roles=()):
    return {"transport": transport, "id": rid, "alias": alias, "label": label, "family": family,
            "version": version, "roles": list(roles)}


# Rows copied field-for-field from the shipped registry shape.
PLANTED = {
    "transports": {"go": {"costModel": "plan-quota"}, "codex": {"costModel": "plan-quota"}},
    "models": [
        row("go", "glm-5.3", "go-glm", "GLM 5.3 (Go)", "glm", "glm-5.3"),
        row("go", "go/deepseek-flash", "go-deepseek-flash", "deepseek-flash (Go)", "flash", "DeepSeek-V4.1-Flash-1017",
            ["architect", "executor"]),
    ],
}


def tier_units():
    # --- tier_fit and seat_verdicts: the pick decides, the line records it -----------------------------------
    refused = dc.tier_fit("go-glm", "architect", "go", PLANTED)
    case("1. an out-of-tier row with no pick is refused", bool(refused), refused)
    notes = []
    case("2. an out-of-tier row with `pick: owner` passes and records one `pick: owner` line",
         dc.tier_fit("go-glm", "architect", "go", PLANTED, owner_pinned=True, notes=notes) is None
         and len(notes) == 1 and "go-glm" in notes[0] and "owner" in notes[0], notes)
    notes = []
    case("3. the same row without `pick: owner` is still refused, and adds no line",
         dc.tier_fit("go-glm", "architect", "go", PLANTED, owner_pinned=False, notes=notes) == refused and notes == [],
         notes)
    notes = []
    case("4. an in-tier row with `pick: owner` adds no line", dc.tier_fit(
        "go-deepseek-flash", "architect", "go", PLANTED, owner_pinned=True, notes=notes) is None and notes == [], notes)

    def seat(label, model, mandates, pick=None, transport="go"):
        entry = {"label": label, "model": model, "tier": "architect", "mandates": mandates, "transport": transport}
        if pick:
            entry["pick"] = pick
        return entry

    sdata = copy.deepcopy(PLANTED)
    case("5. an out-of-tier review seat with no pick is refused",
         len(dc.seat_verdicts([seat("s1", "go-glm", ["m1"])], sdata)) == 1)
    notes = []
    verdicts = dc.seat_verdicts([seat("s1", "go-glm", ["m1", "m2"], pick="owner")], sdata, notes=notes)
    case("6. an out-of-tier review seat with `pick: owner` is admitted alone and records one line",
         verdicts == [] and len(notes) == 1 and "go-glm" in notes[0], (verdicts, notes))


def owner_words_do_not_lift():
    # The owner naming a row in the transcript lifts nothing: decide_tiers over a real Workflow payload.
    data = model_registry.load()
    pick = pick_unclaimed(data)
    case("7. the live registry holds an unclaimed go row to pick", pick is not None)
    if pick is None:
        return
    alias = pick["alias"]
    named = transcript(prompt("run it on %s, max effort" % alias))
    verdict, body, _pins = wpg.decide_tiers(job_payload(named, pick["id"]), "go", data)
    case("8. an owner message naming the row, with no `pick`, is still refused", verdict == "deny", (verdict, body))


def pick_unclaimed(data):
    """An available `go` row that does not fit `architect`: the one the job pins out of tier."""
    for r in data["models"]:
        if (r.get("transport") == "go" and r.get("alias") and model_registry.model_available(r["id"], data)
                and dc.tier_fit(r["alias"], "architect", "go", data) is not None):
            return r
    return None


def job_payload(path, model, pick=None):
    job = {"label": "j0", "promptPath": "x", "effort": "high", "kind": "author", "model": model, "tier": "architect"}
    if pick:
        job["pick"] = pick
    return {"tool_name": "Workflow", "session_id": "s0000001", "transcript_path": path, "tool_input": {
        "scriptPath": ".claude/workflows/dispatch.js", "args": {"jobs": [job]}}}


def routes():
    """Every caller route admits an out-of-tier row only on `pick: owner`, and never on owner words."""
    data = model_registry.load()
    pick = pick_unclaimed(data)
    if pick is None:
        return
    alias = pick["alias"]
    unnamed = transcript(prompt("run it on something cheap"))
    named = transcript(prompt("run it on %s, max effort" % alias))
    line_word = "owner"

    # provider guard: decide_tiers on a Workflow dispatch.js job
    verdict, body, _pins = wpg.decide_tiers(job_payload(unnamed, pick["id"], "owner"), "go", data)
    case("9a. provider guard: a job with `pick: owner` is admitted and records the line",
         verdict is None and any(line_word in n and alias in n for n in body), (verdict, body))
    verdict, body, _pins = wpg.decide_tiers(job_payload(unnamed, pick["id"]), "go", data)
    case("9b. provider guard: the same job without `pick` is refused", verdict == "deny", (verdict, body))

    # sidecar context hook: dispatch_checks over a job whose written tier is architect
    job = ("j0", alias, "author", "go", "architect")
    notes = []
    case("10a. sidecar hook: `pick: owner` on the job admits it and records the line",
         ctx.dispatch_checks([job], owner_labels={"j0"}, notes=notes) is None
         and len(notes) == 1 and line_word in notes[0], notes)
    case("10b. sidecar hook: no pick refuses it", bool(ctx.dispatch_checks([job], notes=[])))

    # lens_briefs: a review seat's tier check
    seat_tier, seat_mandates = lens_briefs.lens.seat_tier, lens_briefs.lens.seat_mandates
    lens_briefs.lens.seat_tier = lambda key: "architect"
    lens_briefs.lens.seat_mandates = lambda key: ["m1"]
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            refused = lens_briefs.lens_tier_refusal(panel={"s1": alias}, native=False, data=data)
        case("11a. lens_briefs: a seat with no pick is refused", len(refused) == 1, refused)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            admitted = lens_briefs.lens_tier_refusal(panel={"s1": alias}, native=False, data=data,
                                                     owner_keys={"s1"})
        case("11b. lens_briefs: a seat with `pick: owner` is admitted and records the line",
             admitted == [] and line_word in err.getvalue() and alias in err.getvalue(), (admitted, err.getvalue()))
    finally:
        lens_briefs.lens.seat_tier, lens_briefs.lens.seat_mandates = seat_tier, seat_mandates

    # sidecar_fanout: the row-list tier check of check_row
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            sf.check_row({"rows": {alias: "high"}, "kind": "author", "tier": "architect", "pick": "owner"},
                         "j0", alias, "high", data)
        got = "admitted"
    except sf.JobError as err_:
        got = "refused: %s" % err_
    case("12a. sidecar_fanout: `pick: owner` admits the row and prints the line",
         got == "admitted" and line_word in out.getvalue(), (got, out.getvalue()))
    try:
        sf.check_row({"rows": {alias: "high"}, "kind": "author", "tier": "architect"}, "j0", alias, "high", data)
        got = "admitted"
    except sf.JobError:
        got = "refused"
    case("12b. sidecar_fanout: no pick refuses the row", got == "refused", got)


def main():
    if dc is None:
        return report()
    for fn in (tier_units, owner_words_do_not_lift, routes):
        try:
            fn()
        except Exception as exc:  # RED state: a name or argument the change adds is absent
            case("%s runs to its end" % fn.__name__, False, "%s: %s" % (type(exc).__name__, exc))
    return report()


def report():
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
