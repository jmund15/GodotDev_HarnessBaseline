"""Proof for tools/usage_samples.py: the locked plan-usage sample writer and its reader.

The hook-level cases (budget_posture's sampler) live in test_budget_posture_cadence.py and the
Codex cases in test_codex_quota_probe.py; these pin the writer's own contract. Every case writes
under a temp dir.

    python3 .claude/tests/test_usage_samples.py
"""
import json
import os
import sys
import tempfile

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import usage_samples as us  # noqa: E402

NOW = 1_790_000_000.0
DAY = 86400.0


def fresh():
    return os.path.join(tempfile.mkdtemp(prefix="us_"), "samples.jsonl")


def sample(ts, provider="anthropic", window="five_hour", used=10):
    return {"ts": ts, "provider": provider, "window": window, "used": used, "resets_at": ts + 3600}


def lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_the_last_kept_sample_is_per_provider_and_window():
    path = fresh()
    assert us.record([sample(NOW - 5)], now=NOW, path=path) == 1
    batch = [sample(NOW - 5, window="seven_day"), sample(NOW - 5, provider="codex"), sample(NOW - 5)]
    assert us.record(batch, now=NOW, path=path) == 2
    assert us.record([sample(NOW - 4)], now=NOW, path=path) == 1
    assert len(lines(path)) == 4, lines(path)


def test_retention_prunes_on_the_first_append_of_each_day_only():
    path = fresh()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(sample(NOW - 40 * DAY)) + "\n")
    us.record([sample(NOW - 1)], now=NOW, path=path)
    assert [s["ts"] for s in lines(path)] == [NOW - 1], lines(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sample(NOW - 40 * DAY, window="seven_day")) + "\n")
    us.record([sample(NOW)], now=NOW, path=path)
    assert len(lines(path)) == 3, "a second append the same day must not prune"
    us.record([sample(NOW + DAY)], now=NOW + DAY, path=path)
    assert NOW - 40 * DAY not in [s["ts"] for s in lines(path)], lines(path)


def test_the_override_names_the_file():
    path = fresh()
    saved = os.environ.get("HARNESS_USAGE_SAMPLES")
    os.environ["HARNESS_USAGE_SAMPLES"] = path
    try:
        assert us.samples_path() == path
        us.record([sample(NOW - 1)], now=NOW)
    finally:
        if saved is None:
            os.environ.pop("HARNESS_USAGE_SAMPLES", None)
        else:
            os.environ["HARNESS_USAGE_SAMPLES"] = saved
    assert len(lines(path)) == 1


def test_an_unwritable_path_returns_none_and_never_raises():
    blocker = os.path.join(tempfile.mkdtemp(prefix="us_block_"), "a_file")
    with open(blocker, "w", encoding="utf-8") as fh:
        fh.write("x")
    assert us.record([sample(NOW - 1)], now=NOW, path=os.path.join(blocker, "s.jsonl")) is None


def test_read_counts_malformed_lines():
    path = fresh()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(sample(NOW)) + "\n{torn\n[1]\n")
    bad = {}
    assert len(us.read(path, bad)) == 1 and bad.get("lines") == 2, bad


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, "-", repr(exc)[:300])
    sys.exit(1 if fails else 0)
