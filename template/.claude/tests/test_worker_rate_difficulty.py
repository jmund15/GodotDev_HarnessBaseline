"""Difficulty coordinates on worker ratings: computed breadth, rated derivation.

Why these cases and not others: the axis exists to stop an unstratified `faithful` from
pooling a hard call with an easy one, so the load-bearing properties are (1) the guard that
refuses an uncharacterised fidelity verdict, (2) breadth being DERIVED rather than stored,
and (3) an unmeasured cell reading UNKNOWN rather than clean or absent. Each is a way the
instrument could look healthy while measuring nothing.

Boundary cases are included because a band threshold is exactly where an off-by-one hides,
and a misfiled call is worse than an unrated one — it reports a coordinate it was never
measured at.

    python .claude/tests/test_worker_rate_difficulty.py
"""

import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import worker_rate as wr  # noqa: E402

FAILURES = []


def check(cond, msg):
    if cond:
        print(f"PASS: {msg}")
    else:
        print(f"FAIL: {msg}")
        FAILURES.append(msg)


def ns(**kw):
    base = dict(outcome="faithful", anchor="c | f:1", derivation=None, note=None,
                ts=None, tool=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


tmp = pathlib.Path(tempfile.mkdtemp(prefix="worker-rate-test-"))
wr.LEDGER = tmp / "calls.jsonl"
wr.RATINGS = tmp / "ratings.jsonl"

ROWS = [
    # ts, tool, paths_requested, prompt_tokens
    ("2026-01-01T00:00:01", "read_files", 4, 5_000),    # narrow: under both
    ("2026-01-01T00:00:02", "read_files", 5, 5_000),    # wide: files at threshold
    ("2026-01-01T00:00:03", "read_files", 4, 10_000),   # wide: tokens at threshold
    ("2026-01-01T00:00:04", "read_files", None, None),  # '?': row cannot say
    ("2026-01-01T00:00:05", "write_doc", None, 800),    # narrow: tokens only
]
wr.LEDGER.write_text("".join(
    json.dumps({"ts": t, "tool": tool, "model": "ollama_chat/test-model",
                "kv_cache_type": "q4_0", "paths_requested": p, "prompt_tokens": k,
                "chars": 100, "latency_ms": 1000}) + "\n"
    for t, tool, p, k in ROWS), encoding="utf-8")

# ── a. breadth bands, including both thresholds ───────────────────────────
print("[a] breadth is computed, and the thresholds land where documented")
want = {"2026-01-01T00:00:01": "narrow", "2026-01-01T00:00:02": "wide",
        "2026-01-01T00:00:03": "wide", "2026-01-01T00:00:04": "?",
        "2026-01-01T00:00:05": "narrow"}
got = {r["ts"]: wr.breadth_of(r) for r in wr.read_jsonl(wr.LEDGER)}
check(got == want, f"breadth bands at boundaries: {got}")
check(wr.breadth_of({"paths_requested": wr.BREADTH_FILES - 1,
                     "prompt_tokens": wr.BREADTH_TOKENS - 1}) == "narrow",
      "one below both thresholds is narrow")

# ── b. an unknown coordinate must NOT default into a band ─────────────────
# Defaulting would file a never-measured call into a real cell, which is the one error a
# difficulty axis cannot survive: it manufactures evidence at a coordinate.
print("[b] a row that cannot supply the coordinate groups separately")
check(wr.breadth_of({}) == "?", "empty row is '?', not narrow")
check(wr.breadth_of({"paths_requested": 0, "prompt_tokens": 0}) == "?",
      "zero/zero is '?' rather than a spurious narrow")

# ── c. the guard: a fidelity verdict without --derivation is refused ──────
print("[c] fidelity verdicts require --derivation")
err = io.StringIO()
_real, sys.stderr = sys.stderr, err
try:
    rc = wr.cmd_rate(ns(outcome="faithful", ts="2026-01-01T00:00:01"))
finally:
    sys.stderr = _real
check(rc == 2, "uncharacterised 'faithful' is refused with exit 2")
check("--derivation" in err.getvalue(), "the refusal names the flag that fixes it")
check(all(k in err.getvalue() for k in wr.DERIVATION),
      "the refusal lists every level, so the fix needs no second lookup")
check(not wr.RATINGS.exists(), "a refused rating writes nothing")

# ── d. 'unusable' is exempt — there is no answer to characterise ──────────
print("[d] 'unusable' does not require the axis")
rc = wr.cmd_rate(ns(outcome="unusable", anchor=None, ts="2026-01-01T00:00:04"))
check(rc == 0, "unusable rates without --derivation")

# ── e. derivation is stored; breadth is NOT ───────────────────────────────
# The invariant that keeps the two axes honest: one authored home per value. A stored
# breadth would be a second copy of a ledger field and could disagree with it.
print("[e] the rating stores derivation only, never the derived coordinate")
rc = wr.cmd_rate(ns(outcome="faithful", derivation="derived", ts="2026-01-01T00:00:02"))
check(rc == 0, "a characterised fidelity verdict is accepted")
rec = wr.read_jsonl(wr.RATINGS)[-1]
check(rec.get("derivation") == "derived", "derivation persisted")
check("breadth" not in rec, "breadth is NOT written to the ratings file")

# ── f. reading joins the rated axis and recomputes the derived one ────────
print("[f] load_joined merges derivation and computes breadth fresh")
joined = {r["ts"]: r for r in wr.load_joined()}
check(joined["2026-01-01T00:00:02"]["derivation"] == "derived", "derivation merged")
check(joined["2026-01-01T00:00:02"]["breadth"] == "wide", "breadth computed on read")
check(joined["2026-01-01T00:00:01"]["derivation"] is None,
      "an unrated call carries no derivation rather than a default level")

# ── g. breadth follows the LEDGER, not the rating ─────────────────────────
# Proves the derived coordinate is genuinely derived: change the source row and the
# coordinate moves, with no re-rating.
print("[g] editing the ledger moves the coordinate without re-rating")
rows = wr.read_jsonl(wr.LEDGER)
for r in rows:
    if r["ts"] == "2026-01-01T00:00:02":
        r["paths_requested"] = 1
        r["prompt_tokens"] = 100
wr.LEDGER.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
joined = {r["ts"]: r for r in wr.load_joined()}
check(joined["2026-01-01T00:00:02"]["breadth"] == "narrow",
      "breadth tracked the ledger edit; it was never a stored copy")
check(joined["2026-01-01T00:00:02"]["derivation"] == "derived",
      "the rated axis survived the ledger edit")

# ── h. an unmeasured cell reads UNKNOWN, not clean and not absent ─────────
print("[h] empty grid cells announce themselves")
buf = io.StringIO()
_real, sys.stdout = sys.stdout, buf
try:
    wr.print_grid(wr.load_joined())
finally:
    sys.stdout = _real
out = buf.getvalue()
check("UNKNOWN" in out, "empty cells render as UNKNOWN")
check("cells are UNKNOWN" in out, "the count of unmeasured cells is stated")
check("narrow" in out and "wide" in out and "derived" in out,
      "both axes are labelled in the grid")

# ── i. the CLI is wired, not just the function ────────────────────────────
# Registration proves wiring; the cases above prove matching. Both are needed.
#
# The subprocess MUST be pointed at the temp store. Reassigning wr.LEDGER above isolates
# only in-process callers; a subprocess re-imports and resolves the real paths. An earlier
# version omitted this, and a mutation run with the guard disabled wrote two unanchored
# `faithful` records into the live ratings file — a test that corrupts the data it measures.
env = dict(os.environ,
           AI_WORKER_LEDGER=str(wr.LEDGER),
           AI_WORKER_RATINGS=str(tmp / "subprocess-ratings.jsonl"),
           AI_WORKER_ARTIFACTS=str(tmp / "artifacts"))
print("[i] the real CLI rejects an uncharacterised verdict")
proc = subprocess.run(
    [sys.executable, str(TOOLS / "worker_rate.py"), "rate", "faithful", "--anchor", "a | b"],
    capture_output=True, text=True, timeout=60, env=env)
check(proc.returncode == 2, f"CLI exit 2 (got {proc.returncode})")
check("--derivation" in (proc.stderr or ""), "CLI refusal names the flag")
proc = subprocess.run(
    [sys.executable, str(TOOLS / "worker_rate.py"), "rate", "faithful",
     "--anchor", "a | b", "--derivation", "nonsense"],
    capture_output=True, text=True, timeout=60, env=env)
check(proc.returncode != 0, "an unknown derivation level is rejected by argparse")

# The blast-radius assertion: even with the guard gone, nothing may reach the real store.
# This is what makes the mutation probe safe to run.
real = pathlib.Path.home() / ".claude" / "ai_worker_ratings.jsonl"
before = real.read_text(encoding="utf-8") if real.exists() else ""
subprocess.run(
    [sys.executable, str(TOOLS / "worker_rate.py"), "rate", "faithful",
     "--anchor", "a | b", "--derivation", "copyable"],
    capture_output=True, text=True, timeout=60, env=env)
after = real.read_text(encoding="utf-8") if real.exists() else ""
check(before == after, "an accepted CLI rating did NOT touch the real ratings store")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL DIFFICULTY-AXIS ASSERTIONS PASSED")
print("tmp dir:", tmp)
