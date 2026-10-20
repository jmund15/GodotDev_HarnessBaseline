"""Re-runnable proof for tools/model_tier_readout.py and its --archive-summary section.

The readout groups archived agent rows by (served registry row, role tier) and prints a verdict per cell
from counts alone. Every case plants its rows and its registry (through HARNESS_MODEL_REGISTRY for the
console cases, through the `registry=` argument for the rest); no live archive or roster is read.

    python3 .claude/tests/test_model_tier_readout.py
"""
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
TOOLS = os.path.join(CLAUDE, "tools")
sys.path.insert(0, TOOLS)

import model_registry  # noqa: E402
import ladder_prose_check  # noqa: E402

spec = importlib.util.spec_from_file_location("mtr_under_test", os.path.join(TOOLS, "model_tier_readout.py"))
try:
    mtr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mtr)
except (FileNotFoundError, ImportError):
    mtr = None

LIVE_REGISTRY = os.path.join(CLAUDE, "reference", "external_models.json")


def plant_registry(roles_by_id):
    """A registry of synthetic rows cloned from one real row's shape; returns (path, loaded data)."""
    with open(LIVE_REGISTRY, encoding="utf-8") as handle:
        data = json.load(handle)
    base = data["models"][0]
    models = []
    for order, (model_id, roles) in enumerate(sorted(roles_by_id.items()), 1):
        row = copy.deepcopy(base)
        row.update(id=model_id, alias=model_id.replace("-", ""), family=model_id, roles=list(roles), order=order)
        row.pop("supplementary", None)
        models.append(row)
    data["models"] = models
    data.pop("aliasHistory", None)
    path = os.path.join(tempfile.mkdtemp(prefix="mtr_registry_"), "external_models.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle)
    return path, model_registry.load(path)


REGISTRY_PATH, REGISTRY = plant_registry({
    "proof-a": ["executor"], "proof-b": [], "proof-c": ["architect", "executor"], "proof-d": ["fanout"],
    "proof-e": ["fanout"]})


def row(n, outcome, *, model="proof-a", tier="executor", session=None, effort="medium", **extra):
    out = {"run": "wf_%d" % n, "label": "lab-%d" % n, "workflow": "proof", "model": "proof-req", "effort": effort,
           "requested_effort": effort, "state": "done", "qcost": 1000.0, "cost": 1000.0,
           "session_id": session or "sess-%d" % (n % 3)}
    if model is not None:
        out["served_model"] = model
    if tier is not None:
        out["tier"] = tier
    if outcome is not None:
        out["outcome"] = outcome
    out.update(extra)
    return out


def side(n, outcome, *, model="proof-a", tier="executor", usd=0.5, session="sess-0", effort="medium"):
    return {"source": "sidecar", "run": "sidecar-%d" % n, "label": "sc-%d" % n, "workflow": "sidecar", "model": model,
            "effort": effort, "requested_effort": effort, "state": "completed", "cost_usd": usd, "tier": tier,
            "session_id": session, "outcome": outcome, "transport": "deepseek"}


def cell_of(result, model, tier):
    return next((c for c in result["cells"] if (c["model"], c["tier"]) == (model, tier)), None)


def rated(outcomes, **kw):
    return [row(i, outcome, **kw) for i, outcome in enumerate(outcomes)]


def read_cells(rows, run_sessions=None, registry=None):
    return mtr.cells(rows, run_sessions=run_sessions or {}, registry=registry or REGISTRY)


def report_lines(rows, run_sessions=None, registry=None):
    lines = []
    mtr.report(rows, run_sessions=run_sessions or {}, registry=registry or REGISTRY, write=lines.append)
    return "\n".join(lines).split("\n")


def main():
    failures, ran = [], []

    def check(ok, label, detail=""):
        ran.append(label)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + detail if detail else ""))

    if mtr is None:
        check(False, "tools/model_tier_readout.py exists")
        print("\n0/1 cases pass")
        return 1

    # --- 1. the console: one cell row, a footer whose buckets sum to the row total ------------
    root = tempfile.mkdtemp(prefix="mtr_root_")
    os.makedirs(os.path.join(root, ".claude"))
    planted = rated(["clean"] * 8, session=None)
    planted += [row(20, "clean", tier=None), row(21, "clean", tier="bogus"), row(22, "clean", tier="Wide")]
    with open(os.path.join(root, ".claude", "orchestration_metrics.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for r in planted:
            fh.write(json.dumps(r) + "\n")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root, HOME=root, USERPROFILE=root, PYTHONIOENCODING="utf-8",
               HARNESS_MODEL_REGISTRY=REGISTRY_PATH)
    result = subprocess.run([sys.executable, os.path.join(TOOLS, "orchestration_metrics.py"), "--archive-summary"],
                            capture_output=True, text=True, env=env, encoding="utf-8", errors="replace", timeout=120)
    out = result.stdout.replace("\r\n", "\n")
    cell_rows = [l for l in out.splitlines() if l.startswith("proof-a") and " executor " in l]
    check(result.returncode == 0 and len(cell_rows) == 1,
          "console: one journal-tagged model+tier prints exactly one cell row",
          "rc=%s rows=%r err=%r" % (result.returncode, cell_rows, result.stderr[-300:]))
    footer = next((l for l in out.splitlines() if l.startswith("buckets:")), "")
    counts = {name.strip(): int(n) for name, n in re.findall(r"([a-z -]+?) (\d+)", footer.replace("buckets:", "").replace("(", "|"))
              if name.strip() != "of which sampled-out"}
    total = int(re.search(r"total (\d+)", footer).group(1)) if "total" in footer else -1
    check(total == len(planted) and counts.get("tier absent") == 1 and counts.get("tier unparseable") == 2
          and counts.get("cell members") == 8,
          "console: the footer counts tier absent, tier unparseable (bogus and Wide) and members and totals the rows",
          repr(footer))

    # --- 2. an unreadable registry degrades the readout; the summary still finishes ----------------
    bad_registry = os.path.join(tempfile.mkdtemp(prefix="mtr_badreg_"), "external_models.json")
    with open(bad_registry, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    candidates_file = os.path.join(root, ".claude", "orchestration_candidates.json")
    if os.path.exists(candidates_file):
        os.remove(candidates_file)
    bad = subprocess.run([sys.executable, os.path.join(TOOLS, "orchestration_metrics.py"), "--archive-summary"],
                         capture_output=True, text=True, env=dict(env, HARNESS_MODEL_REGISTRY=bad_registry),
                         encoding="utf-8", errors="replace", timeout=120)
    check(bad.returncode == 0 and "Traceback" not in bad.stderr and "registry unreadable" in bad.stdout
          and "over-pin candidates" in bad.stdout.lower()
          and os.path.isfile(candidates_file),
          "registry: an unreadable registry prints one readout line, exits 0 and still persists the candidate queue",
          "rc=%s err=%r out=%r" % (bad.returncode, bad.stderr[-300:], bad.stdout[-300:]))
    os.environ["HARNESS_MODEL_REGISTRY"] = bad_registry
    try:
        degraded = mtr.cells(rated(["clean"] * 3), run_sessions={})
        lines_bad = []
        mtr.report(rated(["clean"] * 3), run_sessions={}, write=lines_bad.append)
    finally:
        del os.environ["HARNESS_MODEL_REGISTRY"]
    check(degraded["cells"] == [] and degraded["total"] == 3 and "registry" in degraded.get("degraded", "")
          and sum("registry unreadable" in l for l in lines_bad) == 1,
          "registry: cells() returns no cell and names the registry problem; report() prints it once",
          repr((degraded.get("degraded"), lines_bad)))

    # --- 3/8. the buckets, one row of each kind ------------------------------------------------
    kinds = [
        row(1, "unrated"), row(2, "unrated", sampled_out=True), row(3, None), row(4, "bogus"),
        row(5, "clean", tier=None), row(6, "clean", tier="Wide"), row(7, "clean", model=None),
        row(8, "clean", model="mixed:proof-a,proof-b"), row(9, "clean")]
    result = read_cells(kinds)
    b = result["buckets"]
    check(b == {"unrated": 2, "sampled_out": 1, "no_outcome": 1, "outcome_unparseable": 1, "tier_absent": 1,
                "tier_unparseable": 1, "requested_only": 1, "mixed": 1, "members": 1,
                "measurement_off_role": 0} and result["total"] == 9,
          "buckets: one row of each kind lands in exactly one bucket and the buckets reconcile to the total", repr(b))
    check(sum(v for k, v in b.items() if k != "sampled_out") == result["total"] == len(kinds),
          "buckets: unrated + no outcome + unparseable + tier + requested-only + mixed + members = rows")

    # --- 4. the minimum-N gate -------------------------------------------------------------------
    holds = read_cells(rated(["clean"] * 8))
    c = cell_of(holds, "proof-a", "executor")
    check(c and c["verdict"] == "holds" and c["proposal"] == [] and c["n"] == 8 and c["sessions"] == 3,
          "verdict: eight clean rows in three sessions on a claimed tier hold, with no proposal", repr(c))
    seven = cell_of(read_cells(rated(["clean"] * 5 + ["defects"] * 2)), "proof-a", "executor")
    check(seven["verdict"] == "insufficient" and seven["proposal"] == [],
          "verdict: seven rows with two falsifications are insufficient, not contradicting", repr(seven))
    two_sessions = [row(i, "defects" if i < 2 else "clean", session="sess-%d" % (i % 2)) for i in range(8)]
    cs = cell_of(read_cells(two_sessions), "proof-a", "executor")
    check(cs["verdict"] == "insufficient" and cs["sessions"] == 2 and cs["proposal"] == [],
          "verdict: eight rows in two sessions with two falsifications are insufficient", repr(cs))

    # --- 5. contradicts, holds at one falsification, candidate, unproven -------------------------
    ctr = cell_of(read_cells(rated(["clean"] * 6 + ["defects", "rework"], model="proof-c", effort="low")),
                  "proof-c", "executor")
    lines = ctr["proposal"]
    check(ctr["verdict"] == "contradicts" and len(lines) == 2 and "effort" in lines[0].lower()
          and "low" in lines[0] and "Agents tab" in lines[0]
          and '"roles": ["architect"]' in lines[1] and lines[0].startswith("ADVISORY") and lines[1].startswith("ADVISORY"),
          "verdict: two falsifications on a claimed tier contradict; effort first, then the roles list without the tier",
          repr(ctr))
    one = cell_of(read_cells(rated(["clean"] * 7 + ["defects"], model="proof-c")), "proof-c", "executor")
    check(one["verdict"] == "holds" and one["proposal"] == [], "verdict: one falsification holds", repr(one))
    def share_verdict(n, failed):
        return cell_of(read_cells(rated(["clean"] * (n - failed) + ["defects"] * failed, model="proof-c")),
                       "proof-c", "executor")["verdict"]

    shares = [share_verdict(10, 2), share_verdict(11, 2), share_verdict(264, 44), share_verdict(24, 10),
              share_verdict(8, 1)]
    check(shares == ["contradicts", "holds", "holds", "contradicts", "holds"],
          "verdict: contradicts needs two falsifications and a 20% share (2 of 10 contradicts, 2 of 11 and 44 of 264 "
          "hold, 10 of 24 contradicts, 1 of 8 holds)", repr(shares))
    check(read_cells(rated(["clean"] * 8))["constants"].get("CONTRADICT_MIN_SHARE") == 0.2
          and "CONTRADICT_MIN_SHARE 0.2" in "\n".join(report_lines(rated(["clean"] * 8))),
          "verdict: the share floor is in the constants and printed with every verdict")
    cand = cell_of(read_cells(rated(["clean"] * 8, model="proof-d", tier="architect")), "proof-d", "architect")
    check(cand["verdict"] == "candidate" and len(cand["proposal"]) == 1
          and '"roles": ["architect", "fanout"]' in cand["proposal"][0],
          "verdict: an unclaimed tier with eight clean rows is a candidate, the tier added in TIER_ORDER", repr(cand))
    unproven = cell_of(read_cells(rated(["clean"] * 7 + ["discarded"], model="proof-d", tier="architect")),
                       "proof-d", "architect")
    check(unproven["verdict"] == "unproven" and unproven["proposal"] == [] and unproven["notes"] == [],
          "verdict: one discarded row makes an unclaimed tier unproven", repr(unproven))
    spec_line = cell_of(read_cells(rated(["clean"] * 6 + ["discarded"] * 2, model="proof-d", tier="architect")),
                        "proof-d", "architect")
    check(spec_line["verdict"] == "unproven" and "audit the spec, not the model" in spec_line["notes"],
          "verdict: two discarded rows add the audit-the-spec note", repr(spec_line))

    supp_registry = copy.deepcopy(REGISTRY)
    for entry in supp_registry["models"]:
        if entry["id"] in ("proof-d", "proof-c"):
            entry["supplementary"] = ["architect"]
    supp_cell = cell_of(read_cells(rated(["clean"] * 8, model="proof-d", tier="architect"), registry=supp_registry),
                        "proof-d", "architect")
    check(supp_cell and supp_cell["verdict"] != "candidate" and supp_cell["proposal"] == []
          and not any("roles" in line for line in supp_cell["proposal"]),
          "verdict: a clean-rated cell at a supplementary tier proposes no `roles` edit", repr(supp_cell))
    supp_text = "\n".join(report_lines(rated(["clean"] * 8, model="proof-d", tier="architect"), registry=supp_registry))
    check('"roles"' not in supp_text, "report: a supplementary cell prints no roles proposal", supp_text)
    plain_supp = cell_of(read_cells(rated(["clean"] * 6 + ["defects", "rework"], model="proof-c", tier="architect",
                                          effort="low"), registry=supp_registry), "proof-c", "architect")
    check(plain_supp and plain_supp["verdict"] == "contradicts" and '"roles": ["executor"]' in plain_supp["proposal"][1],
          "verdict: a tier held in roles and supplementary is still judged as a claim", repr(plain_supp))

    order_cand = cell_of(read_cells(rated(["clean"] * 8, model="proof-e", tier="orchestrator")), "proof-e", "orchestrator")
    check(order_cand and '"roles": ["orchestrator", "fanout"]' in order_cand["proposal"][0],
          "verdict: a proposed roles list follows TIER_ORDER, not alphabetical order", repr(order_cand and order_cand["proposal"]))
    order_drop = cell_of(read_cells(rated(["clean"] * 6 + ["defects", "rework"], model="proof-c", tier="architect",
                                          effort="low")), "proof-c", "architect")
    check(order_drop and '"roles": ["executor"]' in order_drop["proposal"][1],
          "verdict: dropping a tier keeps the remaining roles in TIER_ORDER", repr(order_drop and order_drop["proposal"]))

    # --- 5b. mismatch: a row whose requested pin differs from its served model --------------------
    def pinned(n, pin, served="proof-a"):
        out = row(n, "clean", model=served)
        out["model"] = pin
        return out

    honored = [pinned(i, "proof-a[1m]") for i in range(4)]
    family = [pinned(4 + i, "sonnet", "claude-sonnet-5") for i in range(2)]
    differs = [pinned(6 + i, "proof-req") for i in range(3)]
    wrong_family = [pinned(9, "opus", "claude-sonnet-5")]
    unknown_pin = [pinned(10, "?"), pinned(11, "")]
    mismatched = read_cells(honored + family + differs + wrong_family + unknown_pin + [side(30, "clean", model="proof-a")])
    check(cell_of(mismatched, "proof-a", "executor")["mismatch"] == 3
          and cell_of(mismatched, "claude-sonnet-5", "executor")["mismatch"] == 1,
          "mismatch: a pin that differs from the served model counts; a [1m] suffix, a family pin, an unknown pin "
          "and a sidecar row do not", repr([(c["model"], c["mismatch"]) for c in mismatched["cells"]]))
    check("mism" in report_lines(honored + differs)[2] and cell_of(read_cells(honored), "proof-a", "executor")["mismatch"] == 0,
          "mismatch: the console table has a mism column and an all-honored cell reads 0")

    # --- 6. attribution -------------------------------------------------------------------------
    mixed_rows = [row(i, "clean", model="mixed:<synthetic>,proof-a") for i in range(4)] + \
                 [row(i, "clean") for i in range(4, 8)] + \
                 [row(8, "clean", model="mixed:proof-a,proof-b"), row(9, "clean", model=None),
                  row(10, "clean", model="<synthetic>")]
    got = read_cells(mixed_rows)
    check(cell_of(got, "proof-a", "executor")["n"] == 8 and got["buckets"]["mixed"] == 1
          and got["buckets"]["requested_only"] == 2,
          "attribution: a synthetic-plus-one mixed list counts as that id, two real ids are mixed, and a pin "
          "with no served id is requested-only and outside n", repr(got["buckets"]))
    ghost = cell_of(read_cells(rated(["clean"] * 8, model="ghost-9")), "ghost-9", "executor")
    check(ghost and ghost["verdict"] == "unproven" and ghost["proposal"] == [] and ghost["registered"] is False,
          "attribution: an id the registry does not know keys as itself and never gets a proposal", repr(ghost))
    unmapped = [row(i, "clean", session="") for i in range(8)]
    for r in unmapped:
        r.pop("session_id")
    mapped = cell_of(read_cells(unmapped, run_sessions={"wf_0": "/x/sess-a", "wf_1": "C:\\x\\sess-b"}),
                     "proof-a", "executor")
    check(mapped["n"] == 8 and mapped["sessions"] == 2 and mapped["verdict"] == "insufficient",
          "attribution: a row with no session_id resolves through its run; unresolved rows count toward n, "
          "never toward sessions", repr(mapped))
    none_mapped = cell_of(read_cells(unmapped), "proof-a", "executor")
    check(none_mapped["sessions"] == 0, "attribution: with no run mapping the session span is zero")

    # --- 7. two currencies, prose discipline ------------------------------------------------------
    both = [row(i, "clean", qcost=2000.0) for i in range(6)] + [side(i, "clean", usd=1.5) for i in range(100, 103)]
    c = cell_of(read_cells(both), "proof-a", "executor")
    text = "\n".join(report_lines(both))
    check(c["qcost"] == 12000.0 and c["qcost_n"] == 6 and c["usd"] == 4.5 and c["usd_n"] == 3
          and "qcost/agent" in text and "USD/agent" in text,
          "cost: Workflow quota cost and sidecar USD sit in separate columns, each with its priced count", repr(c))
    proposals = []
    for rows in (rated(["clean"] * 6 + ["defects", "rework"], model="proof-c", effort="low"),
                 rated(["clean"] * 8, model="proof-d", tier="architect")):
        proposals += [l for l in report_lines(rows) if l.startswith("ADVISORY")]
    bad = [(name, l) for l in proposals for name, pat, _ in ladder_prose_check.BANNED if re.search(pat, l, re.I)]
    check(len(proposals) == 3 and not bad, "prose: every ADVISORY line passes the ladder prose regexes", repr(bad))
    check(all("external_models.json" in l and "/ladder_ingest" in l for l in proposals),
          "prose: every ADVISORY line names the roles owner's file and /ladder_ingest")
    check(not any(re.search(r"intel|deleg|taste|speed|\"cost\"", l) for l in proposals),
          "prose: no proposal touches intel, deleg, taste, speed or cost")

    check(mtr.bare_model("proof-a[1m]") == "proof-a" and mtr.bare_model("proof-a") == "proof-a"
          and mtr.bare_model(None) == "?" and mtr.bare_model("") == "?"
          and mtr.bare_effort("proof/medium") == "medium" and mtr.bare_effort("high") == "high",
          "identity: bare_model strips a trailing context suffix and names an absent model `?`; bare_effort keeps the "
          "part after `/`")
    check(not mtr.pin_differs({"model": "proof-a[1m]"}, "proof-a") and mtr.pin_differs({"model": "proof-b[1m]"}, "proof-a")
          and not mtr.pin_differs({"model": None}, "proof-a") and not mtr.pin_differs({"model": "?"}, "proof-a"),
          "identity: a pin check reads the model through the same normalization: a suffix is dropped, an absent pin never differs")

    # --- 9. one model, one identity: served id, else pin, resolved through the registry's alias data -----------
    named = copy.deepcopy(REGISTRY)

    def model_row(model_id, alias, family, **extra):
        entry = copy.deepcopy(named["models"][0])
        entry.update(id=model_id, alias=alias, family=family, transport="anthropic", **extra)
        entry.pop("upstreamId", None)
        return entry

    named["models"] = [model_row("proof-h-2025", "h", "haikuish", servedIds=["proof-h"]),
                       model_row("proof-o-5", "o5", "octo", lifecycle="superseded"),
                       model_row("proof-o-55", "octo", "octo")]

    def pin_row(pin, served=None, **extra):
        out = {"run": "wf_x", "label": "l", "model": pin, "effort": "high", "outcome": "clean"}
        if served is not None:
            out["served_model"] = served
        out.update(extra)
        return out

    def who(row_, data=named):
        return mtr.model_identity(row_, data)[0]

    check(who(pin_row("proof-h")) == who(pin_row("proof-h-2025")) == who(pin_row("proof-h[1m]")) == "proof-h-2025"
          and who(pin_row("x", served="proof-h")) == "proof-h-2025",
          "identity: a dated id, an undated served spelling and a pin with a context suffix name one model",
          repr([who(pin_row("proof-h")), who(pin_row("proof-h-2025")), who(pin_row("x", served="proof-h"))]))
    check(who(pin_row("octo")) == "octo (version unrecorded)" and mtr.model_identity(pin_row("octo"), named)[1] is None
          and who(pin_row("octo", served="<synthetic>")) == "octo (version unrecorded)"
          and who(pin_row("octo", served="mixed:proof-o-5,proof-o-55")) == "octo (version unrecorded)",
          "identity: a family alias with no served id cannot name a version; a synthetic or mixed served field records none",
          repr(who(pin_row("octo"))))
    check(who(pin_row("octo", served="proof-o-5")) == "proof-o-5" and who(pin_row("o5")) == "proof-o-5"
          and who(pin_row("octo", served="mixed:<synthetic>,proof-o-55")) == "proof-o-55",
          "identity: a served id wins over a family pin; a versioned alias names its row; a synthetic entry is ignored")
    check(who(pin_row("ghost")) == "ghost" and who(pin_row(None)) == "?" and who(pin_row("?")) == "?"
          and who(pin_row("ghost", served="ghost-9")) == "ghost-9"
          and mtr.model_identity(pin_row("proof-h"), named)[1]["id"] == "proof-h-2025",
          "identity: an unregistered pin or served id names itself, an absent model is `?`, and the registry row rides along")
    sidecar_row = {"source": "sidecar", "run": "s", "label": "l", "model": "proof-h", "effort": "low", "outcome": "clean"}
    check(who(sidecar_row) == "proof-h-2025" and who(dict(sidecar_row, model="?")) == "?" and who(dict(sidecar_row, model=None)) == "?",
          "identity: a sidecar row's model is its served model and resolves the same way")
    ident_rows = [dict(row(i, "clean", model="proof-h", tier="executor"), model="x") for i in range(4)] + \
                 [row(4 + i, "clean", model="proof-h-2025", tier="executor") for i in range(4)]
    ladder_cells = mtr.cells(ident_rows, run_sessions={}, registry=named)["cells"]
    check([(c["model"], c["n"]) for c in ladder_cells] == [("proof-h-2025", 8)],
          "identity: the ladder readout groups on the same identity, so a dated and an undated id share one cell",
          repr([(c["model"], c["n"]) for c in ladder_cells]))
    broken = mtr.model_identity(pin_row("proof-h"), {"models": []})
    check(broken == ("proof-h", None), "identity: with no registry rows a pin names itself", repr(broken))

    # --- 10. per-cell spec sub-counts, cells not split (S9) ---------------------------------------
    mix = [row(0, "clean", spec="tight"), row(1, "clean", spec="tight"), row(2, "defects", spec="tight"),
           row(3, "clean", spec="judged"), row(4, "rework", spec="judged"),
           row(5, "clean"), row(6, "clean"), row(7, "discarded")]
    sub = cell_of(read_cells(mix), "proof-a", "executor") or {}
    check(sub.get("spec") == {"tight": {"n": 3, "falsified": 1}, "judged": {"n": 2, "falsified": 1},
                              "unrecorded": {"n": 3, "falsified": 0}},
          "spec: a cell carries n and falsified per tight, judged and unrecorded, and no other bucket",
          repr(sub.get("spec")))
    check(sub.get("n") == 8 and len(read_cells(mix)["cells"]) == 1,
          "spec: the sub-counts never split the cell: one row per model and tier regardless of spec",
          repr(len(read_cells(mix)["cells"])))
    illegal = [row(i, "clean", spec="vague") for i in range(8)]
    bad_spec = (cell_of(read_cells(illegal), "proof-a", "executor") or {}).get("spec") or {}
    check((bad_spec.get("unrecorded") or {}).get("n") == 8 and (bad_spec.get("tight") or {}).get("n") == 0,
          "spec: an illegal value is counted unrecorded, never guessed into a bucket", repr(bad_spec))
    check("t/j/u" in "\n".join(report_lines(mix)),
          "spec: the console table shows the per-cell spec sub-counts", repr(report_lines(mix)[:4]))

    # --- measurement arms: a row on a tier its model does not claim stays out of the readout -----
    off_role = [row(50 + i, "clean", model="proof-a", tier="architect", measurement=True) for i in range(8)]
    off = read_cells(off_role)
    check(off["buckets"].get("measurement_off_role") == 8 and off["buckets"]["members"] == 0
          and cell_of(off, "proof-a", "architect") is None,
          "measurement: an off-role comparison row lands in measurement_off_role and writes no cell",
          repr(off["buckets"]))

    on_role = [row(60 + i, "clean", model="proof-a", tier="executor", measurement=True) for i in range(8)]
    on = read_cells(on_role)
    check((cell_of(on, "proof-a", "executor") or {}).get("n") == 8 and on["buckets"].get("measurement_off_role") == 0,
          "measurement: an on-role comparison row counts in its cell as before", repr(on["buckets"]))

    plain_off = [row(70 + i, "clean", model="proof-a", tier="architect") for i in range(8)]
    plain = read_cells(plain_off)
    check((cell_of(plain, "proof-a", "architect") or {}).get("n") == 8 and plain["buckets"].get("measurement_off_role") == 0,
          "measurement: a non-measurement off-role row still counts in its cell", repr(plain["buckets"]))

    unregistered = [row(80, "clean", model="ghost-9", tier="executor", measurement=True)]
    ghost = read_cells(unregistered)
    check(ghost["buckets"].get("measurement_off_role") == 1 and ghost["buckets"]["members"] == 0 and ghost["cells"] == [],
          "measurement: a comparison row on an unregistered model is off-role", repr(ghost["buckets"]))

    mixed = off_role + on_role + plain_off + unregistered + [row(90, "unrated"), row(91, None)]
    mixed_result = read_cells(mixed)
    reconciled = sum(v for k, v in mixed_result["buckets"].items() if k != "sampled_out")
    check(reconciled == mixed_result["total"] == len(mixed),
          "measurement: the buckets still reconcile to the row total with off-role rows present",
          repr((reconciled, mixed_result["total"], len(mixed))))

    print("\n%d/%d cases pass" % (len(ran) - len(failures), len(ran)))
    for line in failures:
        print("  FAIL " + line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
