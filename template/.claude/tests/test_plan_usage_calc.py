"""Proof for tools/plan_usage_calc.py: the plan-window fit, its refusals, drift and `price`.

Every case plants its own world: a samples file (HARNESS_USAGE_SAMPLES), a projects root of
transcripts, an absent sidecar ledger, and a synthetic registry passed in-process. Planted `used`
levels are exact functions of planted coefficients, so a correct fit recovers them.

    python3 .claude/tests/test_plan_usage_calc.py
"""
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import time

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import plan_usage_calc as calc  # noqa: E402

NOW = 1_790_000_000.0
H = 3600.0
FIVE = 5 * H


def rates(inp, out, hit, w5, w1):
    return {"cacheMissPer1M": inp, "outputPer1M": out, "cacheHitPer1M": hit,
            "cacheWrite5mPer1M": w5, "cacheWrite1hPer1M": w1, "currency": "USD", "asOf": "2026-09-24"}


REGISTRY = {"models": [
    {"id": "claude-alpha-1", "family": "alpha", "price": rates(4, 20, 0.2, 5, 8)},
    {"id": "claude-beta-1", "family": "beta", "price": rates(1, 5, 0.1, 1.25, 2)},
    {"id": "claude-gamma-1", "family": "gamma", "price": rates(2, 10, 0.2, 2.5, 4)},
    {"id": "claude-delta-1", "family": "delta", "price": None},
], "transports": {"anthropic": {}}}
for row in REGISTRY['models']:
    row.update(alias=row['id'], transport='anthropic')
MODEL = {"alpha": "claude-alpha-1", "beta": "claude-beta-1", "gamma": "claude-gamma-1",
         "delta": "claude-delta-1", "zeta": "claude-zeta-9"}


def call_tokens(scale):
    return {"input_tokens": 1000, "output_tokens": int(1000 * scale), "cache_read_input_tokens": 20000,
            "cache_creation_input_tokens": 3000,
            "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 2000}}


def weight(family, usage):
    p = REGISTRY["models"][["alpha", "beta", "gamma"].index(family)]["price"]
    split = usage["cache_creation"]
    return (usage["input_tokens"] * p["cacheMissPer1M"] + usage["output_tokens"] * p["outputPer1M"]
            + usage["cache_read_input_tokens"] * p["cacheHitPer1M"]
            + split["ephemeral_5m_input_tokens"] * p["cacheWrite5mPer1M"]
            + split["ephemeral_1h_input_tokens"] * p["cacheWrite1hPer1M"]) / 1e6


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class World:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="puc_")
        self.projects = os.path.join(self.root, "projects")
        self.samples_path = os.path.join(self.root, "samples.jsonl")
        self.ledger = os.path.join(self.root, "absent_ledger.jsonl")
        self.calls = []          # (ts, family, usage)
        self.samples = []
        self.n = 0

    def call(self, ts, family, scale=1.0):
        self.calls.append((ts, family, call_tokens(scale)))

    def level(self, start, ts, coefs, noise=0.0):
        used = sum(coefs.get(f, 0.0) * weight(f, u) for t, f, u in self.calls
                   if start <= t <= ts and f in ("alpha", "beta", "gamma"))
        return used + noise

    def sample(self, ts, used, resets_at, window="five_hour"):
        self.samples.append({"ts": ts, "provider": "anthropic", "window": window,
                             "used": used, "resets_at": resets_at})

    def write(self):
        os.makedirs(os.path.join(self.projects, "p"), exist_ok=True)
        with open(os.path.join(self.projects, "p", "s-world.jsonl"), "w", encoding="utf-8") as fh:
            for i, (ts, fam, usage) in enumerate(sorted(self.calls, key=lambda c: c[0])):
                fh.write(json.dumps({"type": "assistant", "sessionId": "s-world", "timestamp": iso(ts),
                                     "uuid": "u%d" % i, "message": {"id": "m%d" % i, "model": MODEL[fam],
                                                                     "usage": usage, "content": []}}) + "\n")
        with open(self.samples_path, "w", encoding="utf-8") as fh:
            for s in self.samples:
                fh.write(json.dumps(s) + "\n")
        os.environ["HARNESS_USAGE_SAMPLES"] = self.samples_path
        return self

    def run(self, *argv):
        out = io.StringIO()
        args = list(argv) + ["--projects", self.projects, "--ledger", self.ledger, "--now", str(NOW)]
        with contextlib.redirect_stdout(out):
            code = calc.main(args, registry=REGISTRY)
        return code, out.getvalue()

    def context(self):
        return calc.Context(self.projects, self.ledger, REGISTRY, NOW)


def plant_periods(world, coefs, count, first_reset, spacing=FIVE, noise=0.0, mix=None):
    """`count` fixed 5h blocks; each carries alternating alpha/beta calls whose mix varies."""
    k = 0
    for i in range(count):
        reset = first_reset + i * spacing
        start = reset - FIVE
        a_scale, b_scale = mix(i) if mix else (1.0 + i, float(count + 1 - i))
        for j in range(12):
            t = start + 600 + j * 1200
            if j % 3 == 2:
                world.call(t, "beta", b_scale)
            else:
                world.call(t, "alpha", a_scale * (1 + 0.3 * (j % 2)))
        for h in (1, 2, 3, 4.5):
            ts = start + h * H
            k += 1
            world.sample(ts, world.level(start, ts, coefs, noise * math.sin(3.7 * k)), reset)
    return world


def coef_line(out, family):
    return next((ln for ln in out.splitlines() if ln.strip().startswith(family + " ")), "")


def test_levels_fit_recovers_two_planted_coefficient_sets():
    for coefs in ({"alpha": 2.0, "beta": 0.5}, {"alpha": 0.7, "beta": 3.0}):
        world = plant_periods(World(), coefs, 8, NOW - 20 * FIVE).write()
        code, out = world.run("fit", "--window", "5h")
        assert code == 0, out
        for fam, planted in coefs.items():
            line = coef_line(out, fam)
            assert "% per list-price dollar" in line, out
            fitted = float(line.split()[1])
            assert abs(fitted - planted) / planted < 1e-3, (fam, fitted, planted, out)


def test_collinear_mix_prints_unidentified():
    world = World()
    for i in range(6):
        reset = NOW - (10 - i) * FIVE
        start = reset - FIVE
        for j in range(8):
            t = start + 600 + j * 1500
            world.call(t, "alpha", 1.0 + i)
            world.call(t, "beta", 1.0 + i)
            if i == 0 and j == 0:
                world.call(t, "gamma", 0.2)
        for h in (1, 2.5, 4.5):
            ts = start + h * H
            world.sample(ts, world.level(start, ts, {"alpha": 1.0, "beta": 1.0, "gamma": 1.0}), reset)
    code, out = world.write().run("fit")
    assert code == 0, out
    alpha, beta = coef_line(out, "alpha"), coef_line(out, "beta")
    assert "unidentified (collinear" in alpha and "unidentified (collinear" in beta, out
    assert "unidentified (spend share" in coef_line(out, "gamma"), out


def test_rolling_window_voids_the_fit():
    world = plant_periods(World(), {"alpha": 1.0, "beta": 1.0}, 6, NOW - 12 * FIVE)
    reset = NOW - 2 * FIVE
    world.sample(reset - 4 * H, 10.0, reset)
    world.sample(reset - 3 * H, 8.0, reset)
    code, out = world.write().run("fit")
    assert code == 0, out
    assert "void: used fell from 10% to 8%" in out and "rolls rather than resetting" in out, out
    assert "% per list-price dollar" not in out, out


def test_insufficient_periods_prints_no_number():
    world = plant_periods(World(), {"alpha": 1.0, "beta": 1.0}, 3, NOW - 6 * FIVE).write()
    code, out = world.run("fit")
    assert code == 0, out
    assert "insufficient data (3 periods, 12 samples)" in out, out
    assert "% per list-price dollar" not in out, out
    assert "files scanned" in out and "excluded sessions" in out and "upper bound" in out, out


def test_drift_flags_a_planted_change_with_combined_se():
    world = World()
    day = 86400.0
    plant_periods(world, {"alpha": 1.0, "beta": 1.0}, 12, NOW - 27 * day, spacing=day, noise=0.02)
    plant_periods(world, {"alpha": 2.0, "beta": 1.0}, 6, NOW - 6.5 * day, spacing=day, noise=0.02)
    world.write()
    result = calc.drift(world.context(), "5h")
    assert result["status"] == "ok", result
    alpha, beta = result["moves"]["alpha"], result["moves"]["beta"]
    assert alpha["status"] == "DRIFT" and beta["status"] == "stable", result["moves"]
    se_r = result["recent"]["families"]["alpha"]["se"]
    se_p = result["prior"]["families"]["alpha"]["se"]
    assert se_r > 0 and se_p > 0, (se_r, se_p)
    assert abs(alpha["limit"] - 2 * math.sqrt(se_r ** 2 + se_p ** 2)) < 1e-12, alpha
    code, out = world.run("drift")
    assert "alpha" in out and "DRIFT" in out and "2*sqrt(se1^2+se2^2)" in out, out
    short = World()
    plant_periods(short, {"alpha": 1.0, "beta": 1.0}, 12, NOW - 27 * day, spacing=day)
    plant_periods(short, {"alpha": 1.0, "beta": 1.0}, 3, NOW - 3.5 * day, spacing=day)
    code, out = short.write().run("drift")
    assert "recent segment: insufficient data (3 periods, 12 samples)" in out, out
    assert "DRIFT" not in out and "stable" not in out, out


def _stream_record(mid, model, usage, sid):
    return {"type": "assistant", "session_id": sid,
            "message": {"id": mid, "model": model, "usage": usage, "content": []}}


def test_price_prints_no_dollars_and_reads_journal_stream_transcript():
    coefs = {"alpha": 2.0, "beta": 0.5}
    world = plant_periods(World(), coefs, 8, NOW - 20 * FIVE).write()
    usage = call_tokens(3.0)
    expected = "%.3g%%" % (coefs["alpha"] * 2 * weight("alpha", usage))
    elsewhere = tempfile.mkdtemp(prefix="puc_price_")
    session = os.path.join(elsewhere, "s-journal")
    run_dir = os.path.join(session, "subagents", "workflows", "wf_1")
    os.makedirs(run_dir)
    os.makedirs(os.path.join(session, "workflows"))
    journal = os.path.join(session, "workflows", "wf_1.json")
    with open(journal, "w", encoding="utf-8") as fh:
        json.dump({"runId": "wf_1", "workflowProgress": [{"type": "workflow_agent", "agentId": "a1"}]}, fh)
    stream = os.path.join(elsewhere, "stream.jsonl")
    transcript = os.path.join(elsewhere, "transcript.jsonl")
    for path, sid in ((os.path.join(run_dir, "agent-a1.jsonl"), "s-journal"), (stream, "s-stream"),
                      (transcript, "s-transcript")):
        with open(path, "w", encoding="utf-8") as fh:
            for mid in ("m1", "m2"):
                rec = _stream_record(mid + sid, MODEL["alpha"], usage, sid)
                if path == transcript:
                    rec = dict(rec, sessionId=sid, timestamp=iso(NOW))
                fh.write(json.dumps(rec) + "\n")
    for path in (journal, stream, transcript):
        code, out = world.run("price", path)
        assert code == 0, out
        assert "input 2000, output 6000, cache read 40000, cache write 5m 2000, cache write 1h 4000" in out, out
        assert "5h window: " + expected in out, (expected, out)
        assert "7d window: n/a (insufficient data (0 periods, 0 samples))" in out, out
        assert "$" not in out and "dollar" not in out.lower() and "usd" not in out.lower(), out


def test_unpriced_model_is_reported_not_defaulted():
    world = plant_periods(World(), {"alpha": 1.0, "beta": 1.0}, 6, NOW - 12 * FIVE)
    start = NOW - 12 * FIVE - FIVE
    for j in range(3):
        world.call(start + 700 + j * 1200, "delta")
        world.call(start + 800 + j * 1200, "zeta")
    code, out = world.write().run("fit")
    assert code == 0, out
    assert "unpriced: claude-delta-1, 3 calls (no registry price)" in out, out
    assert "unpriced: claude-zeta-9, 3 calls (not in the registry)" in out, out
    assert not coef_line(out, "delta") and not coef_line(out, "zeta"), out
    assert "% per list-price dollar" in coef_line(out, "alpha"), out


if __name__ == "__main__":
    fails = 0
    saved = os.environ.get("HARNESS_USAGE_SAMPLES")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, "-", repr(exc)[:400])
    if saved is None:
        os.environ.pop("HARNESS_USAGE_SAMPLES", None)
    sys.exit(1 if fails else 0)
