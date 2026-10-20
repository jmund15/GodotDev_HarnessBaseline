#!/usr/bin/env python3
"""Proof for the per-model overlay fields in tools/model_registry.py: `railTier` and `driverNotes`.

The load-bearing direction is the fail-safe one: a model the registry cannot place, or a row
that carries no `railTier`, must read `detailed`. A resolver that promoted an unknown model would
hand the least-constrained rails to the model with the least evidence behind it.

Run: python3 .claude/tests/test_model_registry_rails.py
"""
import copy
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mr", os.path.join(HERE, "..", "tools", "model_registry.py"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

failed = 0
total = 0


def check(label, ok, detail=""):
    global failed, total
    total += 1
    failed += not ok
    print(("ok   " if ok else "FAIL ") + label + ("" if ok or not detail else "   [%s]" % detail))


live = mr.load()

# ---- row resolution: the session payload's model string is not a registry id ----
for raw, want in [
    ("claude-opus-5-5[1m]", "claude-opus-5-5"),   # own row, context suffix stripped
    ("claude-opus-5-9", "claude-opus-5"),         # an unregistered longer id reads its prefix row
    ("claude-opus-5", "claude-opus-5"),
    ("opus", "claude-opus-5-5"),                  # the bare alias names the current version
    ("CLAUDE-FABLE-5-1", "claude-fable-5-1"),     # case
    ("gpt-5.6-sol[1m]", "gpt-5.6-sol"),
    ("mythos-x", None),                           # unknown family: no row
    ("", None),
    (None, None),
]:
    row = mr.row_for_model(raw, live)
    got = row["id"] if row else None
    check("row_for_model(%r) -> %r" % (raw, want), got == want, "got %r" % got)

# ---- rail tier: registry data, detailed whenever the data is absent ----
for raw, want in [
    ("claude-opus-5-5[1m]", "condensed"),
    ("claude-fable-5-1", "minimal"),
    ("sonnet", "detailed"),
    ("claude-haiku-4-5-20251001", "detailed"),
    ("gpt-5.6-sol[1m]", "detailed"),              # measured capability is not rail adherence
    ("mythos-x", "detailed"),
    (None, "detailed"),
]:
    got = mr.rail_tier(raw, live)
    check("rail_tier(%r) -> %s" % (raw, want), got == want, "got %r" % got)

check("rail_tier on an unreadable registry -> detailed",
      mr.rail_tier("claude-opus-5", {"models": "broken"}) == "detailed")

# ---- driver notes: model row first, then its transport ----
fable = mr.driver_notes("claude-fable-5-1", live)
check("fable driver notes carry the F12 note with its evidence",
      any("Recognizing a name" in t and "F12" in e for t, e in fable), repr(fable))
check("the shipped codex transport carries no driver note",
      "driverNotes" not in live["transports"]["codex"] and mr.driver_notes("gpt-5.6-sol[1m]", live) == [])
planted = copy.deepcopy(live)
planted["transports"]["codex"]["driverNotes"] = [{"text": "TRANSPORT-NOTE", "evidenceRef": "e",
                                                  "weakness": "w", "removeWhen": "r"}]
sol = mr.driver_notes("gpt-5.6-sol[1m]", planted)
check("a transport-level note reaches that transport's driver",
      any(t == "TRANSPORT-NOTE" for t, _ in sol), repr(sol))
check("opus has no driver notes", mr.driver_notes("claude-opus-5", live) == [])
_shipped = [(m["id"], n) for m in live["models"] for n in (m.get("driverNotes") or [])]
_unscoped = [mid for mid, n in _shipped if not (str(n.get("weakness") or "").strip()
                                               and str(n.get("removeWhen") or "").strip())]
check("every shipped driver note names its weakness and its removal condition (%d notes)" % len(_shipped),
      _shipped and not _unscoped, _unscoped)
check("an unknown model has no driver notes", mr.driver_notes("mythos-x", live) == [])

# ---- validation: bad data fails --check ----
def rejects(mutate, label):
    data = copy.deepcopy(live)
    mutate(data)
    try:
        mr._validate(data, "fixture")
    except mr.RegistryError:
        check(label, True)
        return
    check(label, False, "validated clean")


def row(data, alias):
    return next(m for m in data["models"] if m["alias"] == alias)


rejects(lambda d: row(d, "opus").__setitem__("railTier", "opus"), "railTier outside detailed|condensed|minimal is rejected")
rejects(lambda d: row(d, "opus").__setitem__("railTier", "terse"), "a retired railTier name is rejected")
rejects(lambda d: row(d, "fable").__setitem__("driverNotes", [{"text": "x"}]), "a model note without evidenceRef is rejected")
rejects(lambda d: row(d, "fable").__setitem__("driverNotes", [{"text": " ", "evidenceRef": "e"}]), "a blank model note is rejected")
rejects(lambda d: d["transports"]["codex"].__setitem__("driverNotes", "x"), "a transport driverNotes that is not a list is rejected")
rejects(lambda d: row(d, "fable").__setitem__("driverNotes", [{"text": "x", "evidenceRef": "e"}]),
        "a model note with neither weakness nor removeWhen is rejected")
rejects(lambda d: row(d, "fable").__setitem__(
    "driverNotes", [{"text": "x", "evidenceRef": "e", "weakness": "the model broke the rule"}]),
    "a model note without removeWhen is rejected")
rejects(lambda d: row(d, "fable").__setitem__(
    "driverNotes", [{"text": "x", "evidenceRef": "e", "weakness": "  ", "removeWhen": "r"}]),
    "a whitespace-only weakness is rejected")
rejects(lambda d: row(d, "fable").__setitem__(
    "driverNotes", [{"text": "x", "evidenceRef": "e", "weakness": "w", "removeWhen": ""}]),
    "an empty removeWhen is rejected")
rejects(lambda d: d["transports"]["codex"].__setitem__(
    "driverNotes", [{"text": "x", "evidenceRef": "e", "weakness": "w"}]), "a transport note without removeWhen is rejected")
# ---- railTierEvidence: a tier above `detailed` names its evidence (instruction_quality §3) ----
rejects(lambda d: row(d, "opus").pop("railTierEvidence", None), "a condensed row without railTierEvidence is rejected")
rejects(lambda d: row(d, "opus").__setitem__("railTierEvidence", {"evidence": "guessed"}), "an unknown evidence kind is rejected")
rejects(lambda d: row(d, "opus").__setitem__("railTierEvidence", {"evidence": "measured", "route": "dispatch.js", "effort": "low"}),
        "measured evidence without evidenceRef is rejected")
check("unmeasured evidence validates", mr.rail_evidence_problems(live, mr.Path(HERE).parents[1]) == ([], []))

# ---- contextSuffix: the one legal value, an accessor by name, the shipped claim and its retire condition ----
rejects(lambda d: row(d, "sonnet").__setitem__("contextSuffix", "[2m]"), "a contextSuffix other than [1m] is rejected")
rejects(lambda d: row(d, "sonnet").__setitem__("contextSuffix", "1m"), "a contextSuffix without brackets is rejected")
rejects(lambda d: row(d, "sonnet").__setitem__("contextSuffix", ""), "an empty contextSuffix is rejected")
rejects(lambda d: row(d, "sonnet").__setitem__("contextSuffix", 1), "a non-string contextSuffix is rejected")
rejects(lambda d: row(d, "sonnet").__setitem__("contextSuffix", ["[1m]"]), "a list contextSuffix is rejected")
for raw, want in [
    ("claude-sonnet-5-5", "[1m]"),
    ("sonnet", "[1m]"),
    ("claude-sonnet-5-5[1m]", "[1m]"),
    ("claude-opus-5-5", ""),                      # Opus 5.5 subagents already get 1M unsuffixed
    ("claude-sonnet-5", ""),
    ("haiku", ""),
    ("mythos-x", ""),
    (None, ""),
]:
    got = mr.context_suffix(raw, live)
    check("context_suffix(%r) -> %r" % (raw, want), got == want, "got %r" % got)
check("context_suffix on an unreadable registry -> no suffix", mr.context_suffix("claude-sonnet-5-5", {"models": "broken"}) == "")
sonnet55 = row(live, "sonnet")
check("the claude-sonnet-5-5 row declares contextSuffix [1m]", sonnet55.get("contextSuffix") == "[1m]", sonnet55.get("contextSuffix"))
comment = str(sonnet55.get("_contextSuffixComment") or "")
check("its _contextSuffixComment names the evidence run and the retire condition",
      "wf_4fe964ac-937" in comment and "delete" in comment and "200k" in comment, comment)
check("only the claude-sonnet-5-5 row declares contextSuffix",
      [m["id"] for m in live["models"] if "contextSuffix" in m] == ["claude-sonnet-5-5"],
      [m["id"] for m in live["models"] if "contextSuffix" in m])
check("context_suffix_names maps id and alias of a declaring row, restricted to a transport",
      mr.context_suffix_names(live, "anthropic") == {"claude-sonnet-5-5": "[1m]", "sonnet": "[1m]"}
      and mr.context_suffix_names(live, "codex") == {}, mr.context_suffix_names(live, "anthropic"))

import json as _json  # noqa: E402
import tempfile as _tempfile  # noqa: E402
_repo = mr.Path(HERE).parents[1]
_tmp = mr.Path(_tempfile.mkdtemp(prefix="rte_"))


def _report(**over):
    rep = {"phase": "B", "modelId": "claude-opus-5-5", "tier": "condensed", "route": "dispatch.js", "effort": "low",
           "verdict": "PASS", "treatmentHashes": mr._rail_treatment_hashes(_repo)}
    rep.update(over)
    path = _tmp / ("r%d.json" % len(list(_tmp.iterdir())))
    path.write_text(_json.dumps(rep), encoding="utf-8")
    return str(path)


def _problems(ref, **ev):
    data = copy.deepcopy(live)
    row(data, "opus")["railTierEvidence"] = dict({"evidence": "measured", "evidenceRef": ref, "route": "dispatch.js",
                                                  "effort": "low"}, **ev)
    return mr.rail_evidence_problems(data, _repo)


errs, stale = _problems(_report())
check("a matching PASS report validates and is fresh", errs == [] and stale == [], (errs, stale))
check("a missing report file is an error", _problems(str(_tmp / "nope.json"))[0] != [])
check("a report for another model is an error", _problems(_report(modelId="claude-fable-5-1"))[0] != [])
check("a report at another effort is an error", _problems(_report(effort="medium"))[0] != [])
check("a non-PASS report is an error", _problems(_report(verdict="INCONCLUSIVE"))[0] != [])
errs, stale = _problems(_report(treatmentHashes={"guards/any.md": "0" * 64}))
check("a report whose guard hashes moved is STALE, not an error", errs == [] and len(stale) == 1, (errs, stale))

def _mrow(probe, stage, verdict, model="claude-opus-5-5", effort="low", runs=("4ee3c043",)):
    return {"model": model, "effort": effort, "probe": probe, "stage": stage, "verdict": verdict, "runs": list(runs)}


def _matrix(rows=None, **over):
    rows = [_mrow("P5", "necessity", "NEEDED"), _mrow("P5", "carriage:condensed", "CARRIED"),
            _mrow("P9", "necessity", "WEAK"), _mrow("P9", "carriage:condensed", "CARRIED"),
            _mrow("P1", "necessity", "HELD")] if rows is None else rows
    rep = {"rows": rows, "scorerSha": "ab" * 32, "builtAt": "2026-09-29T00:00:00+00:00"}
    rep.update(over)
    path = _tmp / ("m%d.json" % len(list(_tmp.iterdir())))
    path.write_text(_json.dumps(rep), encoding="utf-8")
    return str(path)


errs, stale = _problems(_matrix())
check("an S4 matrix with every NEEDED/WEAK rule CARRIED validates", errs == [], str((errs, stale)))
check("an S4 matrix with a DROPPED carriage row is refused",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"), _mrow("P5", "carriage:condensed", "DROPPED")]))[0] != [])
check("an S4 matrix with an UNDERPOWERED carriage row is refused",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"), _mrow("P5", "carriage:condensed", "UNDERPOWERED")]))[0] != [])
check("a NEEDED rule with no carriage row is refused",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"), _mrow("P9", "necessity", "WEAK"),
                         _mrow("P9", "carriage:condensed", "CARRIED")]))[0] != [])
check("a matrix with no carriage row for this model is refused",
      _problems(_matrix([_mrow("P1", "necessity", "HELD")]))[0] != [])
check("a carriage row at another tier is refused",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"), _mrow("P5", "carriage:minimal", "CARRIED")]))[0] != [])
check("a carriage row for another model does not count",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"),
                         _mrow("P5", "carriage:condensed", "CARRIED", model="claude-fable-5-1")]))[0] != [])
check("a carriage row at another effort does not count",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"),
                         _mrow("P5", "carriage:condensed", "CARRIED", effort="medium")]))[0] != [])
check("a carriage row without runs is refused",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"), _mrow("P5", "carriage:condensed", "CARRIED", runs=())]))[0] != [])
check("a carriage-only matrix with no necessity row for this model is refused",
      _problems(_matrix([_mrow("P5", "carriage:condensed", "CARRIED")]))[0] != [])
check("a necessity row for another effort does not stand in for this model's",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED", effort="medium"),
                         _mrow("P5", "carriage:condensed", "CARRIED")]))[0] != [])
check("a DROPPED carriage rerun hidden behind a later CARRIED row is refused",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"), _mrow("P5", "carriage:condensed", "DROPPED"),
                         _mrow("P5", "carriage:condensed", "CARRIED")]))[0] != [])
check("duplicate carriage rows that are all CARRIED validate",
      _problems(_matrix([_mrow("P5", "necessity", "NEEDED"), _mrow("P5", "carriage:condensed", "CARRIED"),
                         _mrow("P5", "carriage:condensed", "CARRIED")]))[0] == [])
check("a matrix without scorerSha is refused", _problems(_matrix(scorerSha=""))[0] != [])
check("a matrix whose rows are not a list is refused", _problems(_matrix(rows="x"))[0] != [])


try:
    mr._validate(copy.deepcopy(live), "live")
    check("the shipped registry validates", True)
except mr.RegistryError as err:
    check("the shipped registry validates", False, str(err))

print("\n%d/%d passed" % (total - failed, total))
sys.exit(1 if failed else 0)
