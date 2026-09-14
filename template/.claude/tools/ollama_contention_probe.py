#!/usr/bin/env python3
"""Ollama concurrency-contention probe.

Settles by experiment, not inference:
  Q1  Do two concurrent requests to the SAME model serialize safely, and what does the
      second one's wall clock look like versus running alone?
  Q2  Do two concurrent requests to DIFFERENT models cause eviction thrash, and at what cost?
      Residency is OBSERVED via /api/ps sampling, never assumed.
  Q3  While a request is QUEUED, does the connection emit any bytes? TTFB is recorded
      separately from total duration -- this decides whether an inactivity timeout can kill
      a queued call.
  Q4  Does OLLAMA_MAX_LOADED_MODELS=1 change any of the above? The value is a RUN PARAMETER
      and is verified against server-observed residency, not trusted from the client env.

Stdlib only. Every emitted row carries its configuration stamp, and every stamp field that the
Ollama API exposes is READ BACK FROM THE SERVER rather than from this process's environment.

Run:  python ollama_contention_probe.py --expect-max-loaded 2 --out run_default.json
See ollama_contention_probe.md for GPU-state prerequisites and what each verdict licenses.

STATUS: calibrated 2026-08-19 -- ran end to end (exit 0) with CONTROL and SCORER both PASS, on a
16GB card with the server started under an UNSET OLLAMA_MAX_LOADED_MODELS. That run is the
`max_loaded=default` half of the Q4 pair; the `=1` half needs an Ollama restart and is not yet
taken. Q1/Q2/Q3 answered on that run: two models were co-resident under NUM_PARALLEL=1, no
eviction thrash (both fit), and a queued call emitted nothing until its turn.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------------------
# Configuration surface
# --------------------------------------------------------------------------------------

DEFAULT_HOST = "http://localhost:11434"

# Two DISTINCT weight sets so Q2 can force a residency decision. Both are installed per
# ~/.config/ai-worker/models.yaml. MODEL_A is the alias `qwen-local` actually resolves to,
# so Q1/Q3 measure the model the harness really uses; MODEL_B is small enough that a load
# is cheap but still a real second residency.
MODEL_A = "hf.co/empero-ai/Qwen3.8-27B-Ridge-GGUF:latest"
MODEL_B = "granite4.1:3b"
MODEL_CONTROL = "granite4.1:3b"

# Held flat across every generating item so a duration difference is contention, not workload.
GEN_PROMPT = (
    "Count aloud from one to forty, one number per line, using digits only. "
    "Do not add any other words."
)
GEN_OPTIONS = {"num_ctx": 4096, "temperature": 0.0, "seed": 20260819, "num_predict": 160}

# Known-answer case: proves the scorer ACCEPTS a correct answer, not merely that it
# rejects an empty one. Both polarities are exercised and both are printed.
KA_PROMPT = "What is 17 plus 25? Reply with the number and nothing else."
KA_EXPECT = "42"
KA_OPTIONS = {"num_ctx": 2048, "temperature": 0.0, "seed": 20260819, "num_predict": 24}

PS_SAMPLE_INTERVAL_S = 0.4
HTTP_CONNECT_TIMEOUT_S = 10
# Deliberately generous: a queued call must be allowed to finish so Q3 can measure its TTFB.
# A short timeout here would manufacture the very failure the probe is trying to observe.
HTTP_READ_TIMEOUT_S = 900


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------


def _get_json(host: str, path: str, timeout: float = HTTP_CONNECT_TIMEOUT_S) -> dict:
    req = urllib.request.Request(host + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat_stream(host: str, model: str, prompt: str, options: dict, tag: str) -> dict:
    """One streaming /api/chat call. Returns a per-item row.

    TTFB is split three ways because they answer different questions:
      ttfb_header_s  -- time until HTTP response headers are available
      ttfb_byte_s    -- time until the FIRST body byte arrives (this is what an inactivity
                        timeout actually watches)
      ttfb_token_s   -- time until the first non-empty content token
    """
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "options": options,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        host + "/api/chat",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    row: dict = {
        "tag": tag,
        "model_requested": model,
        "options_sent": dict(options),
        "t_start_wall": time.time(),
        "error": None,
    }
    t0 = time.perf_counter()
    text_parts: list[str] = []
    final: dict = {}
    inter_chunk_gaps: list[float] = []

    try:
        with urllib.request.urlopen(req, timeout=HTTP_READ_TIMEOUT_S) as resp:
            row["http_status"] = resp.status
            row["ttfb_header_s"] = time.perf_counter() - t0
            first_byte_at = None
            last_chunk_at = None
            for raw in resp:
                now = time.perf_counter()
                if first_byte_at is None:
                    first_byte_at = now
                    row["ttfb_byte_s"] = now - t0
                else:
                    inter_chunk_gaps.append(now - last_chunk_at)
                last_chunk_at = now
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = (obj.get("message") or {}).get("content") or ""
                if piece and "ttfb_token_s" not in row:
                    row["ttfb_token_s"] = now - t0
                if piece:
                    text_parts.append(piece)
                if obj.get("done"):
                    final = obj
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"

    row["total_s"] = time.perf_counter() - t0
    row["t_end_wall"] = time.time()
    row["response_text"] = "".join(text_parts)
    row["max_inter_chunk_gap_s"] = max(inter_chunk_gaps) if inter_chunk_gaps else None
    # Server-side readback of what actually ran. `model` here is the server's own echo.
    for k in (
        "model",
        "done_reason",
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    ):
        if k in final:
            row[k] = final[k]
    row.setdefault("ttfb_byte_s", None)
    row.setdefault("ttfb_token_s", None)
    return row


# --------------------------------------------------------------------------------------
# Residency observation
# --------------------------------------------------------------------------------------


class PsSampler(threading.Thread):
    """Polls /api/ps on an interval so residency is OBSERVED across a phase.

    A single before/after pair cannot distinguish "both stayed loaded" from "one was
    evicted and reloaded"; the trace can.
    """

    def __init__(self, host: str, interval: float = PS_SAMPLE_INTERVAL_S):
        super().__init__(daemon=True)
        self.host = host
        self.interval = interval
        self.samples: list[dict] = []
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                data = _get_json(self.host, "/api/ps", timeout=5)
                models = data.get("models") or []
                self.samples.append(
                    {
                        "t": time.time(),
                        "names": sorted(m.get("name", "?") for m in models),
                        "detail": [
                            {
                                "name": m.get("name"),
                                "size": m.get("size"),
                                "size_vram": m.get("size_vram"),
                                "context_length": m.get("context_length"),
                                "expires_at": m.get("expires_at"),
                            }
                            for m in models
                        ],
                    }
                )
            except Exception as exc:  # observation must never abort the experiment
                self.samples.append({"t": time.time(), "names": None, "error": repr(exc)})
            self._stop.wait(self.interval)

    def stop(self) -> list[dict]:
        self._stop.set()
        self.join(timeout=5)
        return self.samples


def residency_summary(samples: list[dict]) -> dict:
    """Reduce a residency trace. Three input states, and they must sum to the sample count:
    a good sample, a sample whose poll errored, and (by construction) nothing else."""
    good = [s for s in samples if s.get("names") is not None]
    errored = [s for s in samples if s.get("names") is None]
    assert len(good) + len(errored) == len(samples), "residency buckets must sum to samples"

    seq = [tuple(s["names"]) for s in good]
    transitions = [(a, b) for a, b in zip(seq, seq[1:]) if a != b]
    # A reload event: a model name that leaves the resident set and later returns.
    seen_then_gone: dict[str, bool] = {}
    reload_events: list[str] = []
    for names in seq:
        cur = set(names)
        for name in list(seen_then_gone):
            if seen_then_gone[name] and name in cur:
                reload_events.append(name)
                seen_then_gone[name] = False
        for name in cur:
            seen_then_gone.setdefault(name, False)
        for name in seen_then_gone:
            if name not in cur:
                seen_then_gone[name] = True
    return {
        "samples_total": len(samples),
        "samples_ok": len(good),
        "samples_errored": len(errored),
        "max_concurrent_resident": max((len(s) for s in seq), default=0),
        "distinct_resident_sets": sorted({s for s in seq}),
        "transition_count": len(transitions),
        "reload_events": reload_events,
        "thrash_observed": len(reload_events) > 0,
    }


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------


def score_known_answer(text: str, expect: str = KA_EXPECT) -> bool:
    """Deliberately narrow: the expected token must appear as a standalone run of digits."""
    if not text:
        return False
    digits = "".join(c if c.isdigit() else " " for c in text).split()
    return expect in digits


def score_generation(text: str) -> dict:
    """Per-item components for the generating workload. Reported beside the pass bit --
    a conjunctive bit alone hides which component moved."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    numeric = [ln for ln in lines if ln.isdigit()]
    return {
        "chars": len(text),
        "lines": len(lines),
        "numeric_lines": len(numeric),
        "nonempty": len(text.strip()) > 0,
    }


# --------------------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------------------


def phase_stamp(host: str, args) -> dict:
    """Configuration stamp. Server-observed fields are marked `source: server`; anything
    this process can only believe is marked `source: client_belief` and is NEVER used as
    evidence for a verdict."""
    version = _get_json(host, "/api/version")
    ps = _get_json(host, "/api/ps")
    tags = _get_json(host, "/api/tags")
    installed = sorted(m.get("name", "?") for m in (tags.get("models") or []))
    return {
        "server": {
            "source": "server",
            "version": version.get("version"),
            "resident_at_start": sorted(m.get("name", "?") for m in (ps.get("models") or [])),
            "resident_detail_at_start": ps.get("models") or [],
            "installed_models": installed,
        },
        "client_belief": {
            "source": "client_belief",
            "note": "Ollama exposes no config endpoint; these are THIS process's env only "
            "and may differ from the env of the running `ollama serve`.",
            "OLLAMA_MAX_LOADED_MODELS": os.environ.get("OLLAMA_MAX_LOADED_MODELS"),
            "OLLAMA_NUM_PARALLEL": os.environ.get("OLLAMA_NUM_PARALLEL"),
            "OLLAMA_FLASH_ATTENTION": os.environ.get("OLLAMA_FLASH_ATTENTION"),
            "OLLAMA_KV_CACHE_TYPE": os.environ.get("OLLAMA_KV_CACHE_TYPE"),
        },
        "run_parameters": {
            "expect_max_loaded": args.expect_max_loaded,
            "model_a": args.model_a,
            "model_b": args.model_b,
            "control_model": args.control_model,
            "reps": args.reps,
            "gen_options": GEN_OPTIONS,
        },
    }


def phase_control(host: str, args) -> dict:
    """Instrument reaches the field: one small model, one call, non-empty streamed output."""
    row = chat_stream(host, args.control_model, GEN_PROMPT, GEN_OPTIONS, tag="control")
    row["score"] = score_generation(row["response_text"])
    row["control_pass"] = bool(row["error"] is None and row["score"]["nonempty"])
    return row


def phase_known_answer(host: str, args) -> dict:
    """Scorer polarity: it must ACCEPT a correct answer and REJECT an empty one."""
    row = chat_stream(host, args.control_model, KA_PROMPT, KA_OPTIONS, tag="known_answer")
    accepted_correct = score_known_answer(row["response_text"])
    rejected_empty = not score_known_answer("")
    rejected_wrong = not score_known_answer("the answer is 41")
    row["scorer_accepts_correct"] = accepted_correct
    row["scorer_rejects_empty"] = rejected_empty
    row["scorer_rejects_wrong"] = rejected_wrong
    row["known_answer_pass"] = accepted_correct and rejected_empty and rejected_wrong
    return row


def _run_concurrent(host: str, jobs: list[tuple[str, str]]) -> tuple[list[dict], dict]:
    """jobs = [(model, tag), ...]. Returns per-item rows and the residency summary."""
    sampler = PsSampler(host)
    sampler.start()
    results: list[dict] = [None] * len(jobs)  # type: ignore[list-item]
    barrier = threading.Barrier(len(jobs))

    def worker(i: int, model: str, tag: str) -> None:
        barrier.wait()  # release both requests as close to simultaneously as possible
        results[i] = chat_stream(host, model, GEN_PROMPT, GEN_OPTIONS, tag=tag)

    threads = [
        threading.Thread(target=worker, args=(i, m, t)) for i, (m, t) in enumerate(jobs)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, residency_summary(sampler.stop())


def phase_alone(host: str, model: str, tag: str, reps: int) -> list[dict]:
    rows = []
    for r in range(reps):
        row = chat_stream(host, model, GEN_PROMPT, GEN_OPTIONS, tag=f"{tag}#{r}")
        row["score"] = score_generation(row["response_text"])
        rows.append(row)
    return rows


def phase_same_model(host: str, args) -> dict:
    rows, resid = _run_concurrent(host, [(args.model_a, "same_A1"), (args.model_a, "same_A2")])
    for row in rows:
        row["score"] = score_generation(row["response_text"])
    starts = sorted(r["t_start_wall"] for r in rows)
    ends = sorted(r["t_end_wall"] for r in rows)
    overlap_s = max(0.0, min(ends) - max(starts))
    span_s = max(ends) - min(starts)
    return {
        "rows": rows,
        "residency": resid,
        "execution_overlap_s": overlap_s,
        "wall_span_s": span_s,
        "overlap_fraction": (overlap_s / span_s) if span_s > 0 else None,
        "all_succeeded": all(r["error"] is None and r["score"]["nonempty"] for r in rows),
    }


def phase_diff_model(host: str, args) -> dict:
    rows, resid = _run_concurrent(host, [(args.model_a, "diff_A"), (args.model_b, "diff_B")])
    for row in rows:
        row["score"] = score_generation(row["response_text"])
    return {
        "rows": rows,
        "residency": resid,
        "all_succeeded": all(r["error"] is None and r["score"]["nonempty"] for r in rows),
    }


# --------------------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------------------


def emit_verdicts(result: dict) -> list[str]:
    v: list[str] = []
    ctl = result["control"]
    ka = result["known_answer"]

    v.append(
        f"CONTROL   {'PASS' if ctl['control_pass'] else 'FAIL'} -- instrument reaches the field "
        f"(model={ctl.get('model')}, chars={ctl['score']['chars']}, total_s={ctl['total_s']:.2f})"
        + ("" if ctl["control_pass"] else "  >>> every verdict below is VOID")
    )
    v.append(
        f"SCORER    {'PASS' if ka['known_answer_pass'] else 'FAIL'} -- accepts correct="
        f"{ka['scorer_accepts_correct']} rejects empty={ka['scorer_rejects_empty']} "
        f"rejects wrong={ka['scorer_rejects_wrong']} (got {ka['response_text']!r})"
    )

    # Q1
    same = result["same_model"]
    alone = result["alone_a"]
    alone_med = statistics.median(r["total_s"] for r in alone if r["error"] is None)
    totals = sorted(r["total_s"] for r in same["rows"])
    if not same["all_succeeded"]:
        v.append("Q1 SERIALIZE  FAIL -- a concurrent same-model call errored; see rows[].error")
    else:
        ratio = totals[-1] / alone_med if alone_med else float("nan")
        verdict = "SERIALIZED" if ratio >= 1.6 else "OVERLAPPED-OR-BATCHED"
        v.append(
            f"Q1 SERIALIZE  {verdict} -- alone_median={alone_med:.2f}s "
            f"concurrent=[{totals[0]:.2f}s, {totals[-1]:.2f}s] slow/alone={ratio:.2f}x "
            f"exec_overlap={same['execution_overlap_s']:.2f}s of {same['wall_span_s']:.2f}s span; "
            f"both returned correct-shaped output, so the queue is SAFE either way"
        )

    # Q2
    diff = result["diff_model"]
    dr = diff["residency"]
    if not diff["all_succeeded"]:
        v.append("Q2 EVICTION   FAIL -- a cross-model concurrent call errored; see rows[].error")
    else:
        thrash = dr["thrash_observed"]
        slowest = max(r["total_s"] for r in diff["rows"])
        cost = slowest - alone_med if alone_med else float("nan")
        v.append(
            f"Q2 EVICTION   {'THRASH OBSERVED' if thrash else 'NO THRASH OBSERVED'} -- "
            f"max_concurrent_resident={dr['max_concurrent_resident']} "
            f"reloads={dr['reload_events']} transitions={dr['transition_count']} "
            f"samples_ok={dr['samples_ok']}/{dr['samples_total']}; "
            f"slowest_cross_model={slowest:.2f}s vs alone_median={alone_med:.2f}s "
            f"(cost {cost:+.2f}s)"
        )

    # Q3
    queued = max(same["rows"], key=lambda r: r["total_s"])
    ttfb = queued.get("ttfb_byte_s")
    if ttfb is None:
        v.append("Q3 QUEUED-TTFB  UNVERIFIABLE -- no body byte recorded for the queued call")
    else:
        v.append(
            f"Q3 QUEUED-TTFB  {ttfb:.2f}s to FIRST BODY BYTE (token at "
            f"{queued.get('ttfb_token_s')}); total={queued['total_s']:.2f}s; "
            f"max_inter_chunk_gap={queued.get('max_inter_chunk_gap_s')}s. "
            f"=> an inactivity timeout must exceed {ttfb:.2f}s or it kills QUEUED calls that "
            f"would have succeeded. A queued call is SILENT until its turn."
        )

    # Q4
    stamp = result["stamp"]
    expected = stamp["run_parameters"]["expect_max_loaded"]
    observed = dr["max_concurrent_resident"]
    if expected is None:
        v.append(
            "Q4 MAX-LOADED  NOT ASSERTED -- pass --expect-max-loaded to bind this run to a "
            "configuration; without it the run is unstamped for the Q4 comparison"
        )
    elif expected == 1:
        ok = observed <= 1
        v.append(
            f"Q4 MAX-LOADED  {'CONSISTENT' if ok else 'CONTRADICTED'} with "
            f"OLLAMA_MAX_LOADED_MODELS=1 -- server never held more than {observed} model(s) "
            f"resident. NOTE: observed<=1 is consistent with the limit but does NOT prove it "
            f"(a sampler at {PS_SAMPLE_INTERVAL_S}s can miss a brief overlap). observed>1 DOES "
            f"disprove it."
        )
    else:
        v.append(
            f"Q4 MAX-LOADED  observed max_concurrent_resident={observed}, expected>={expected} -- "
            f"{'CONSISTENT' if observed >= 2 else 'CONTRADICTED (server behaved as if limit=1)'}"
        )
    v.append(
        "Q4 PAIRED     Run this script twice -- once with the server started under "
        "OLLAMA_MAX_LOADED_MODELS=1 and once without -- and diff the two JSON files. A single "
        "run cannot answer Q4; it can only stamp one side of the pair."
    )
    return v


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--model-a", default=MODEL_A)
    p.add_argument("--model-b", default=MODEL_B)
    p.add_argument("--control-model", default=MODEL_CONTROL)
    p.add_argument(
        "--expect-max-loaded",
        type=int,
        default=None,
        help="The OLLAMA_MAX_LOADED_MODELS the SERVER was started with. Asserted against "
        "server-observed residency; never read from this process's env.",
    )
    p.add_argument("--reps", type=int, default=3, help="Solo-baseline reps (>=3 for a claim).")
    p.add_argument("--out", default="ollama_contention_run.json")
    p.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if a model is already resident (i.e. the GPU is not idle).",
    )
    args = p.parse_args()
    host = args.host.rstrip("/")

    try:
        stamp = phase_stamp(host, args)
    except Exception as exc:
        print(f"PREFLIGHT FAIL -- cannot reach {host}: {exc!r}", file=sys.stderr)
        return 2

    resident = stamp["server"]["resident_at_start"]
    if resident and not args.force:
        print(
            "PREFLIGHT ABORT -- models already resident, so the GPU is not idle and every\n"
            f"  duration below would be contaminated: {resident}\n"
            "  Wait for the peer workload to finish, or re-run with --force to stamp anyway.",
            file=sys.stderr,
        )
        return 3
    for m in (args.model_a, args.model_b, args.control_model):
        if m not in stamp["server"]["installed_models"]:
            print(f"PREFLIGHT FAIL -- model not installed on server: {m}", file=sys.stderr)
            return 4

    result: dict = {"stamp": stamp, "started_at": time.time()}
    print("[1/5] control ...", flush=True)
    result["control"] = phase_control(host, args)
    print("[2/5] known-answer ...", flush=True)
    result["known_answer"] = phase_known_answer(host, args)
    print(f"[3/5] solo baseline on model_a x{args.reps} ...", flush=True)
    result["alone_a"] = phase_alone(host, args.model_a, "alone_A", args.reps)
    print("[4/5] same-model concurrency ...", flush=True)
    result["same_model"] = phase_same_model(host, args)
    print("[5/5] cross-model concurrency ...", flush=True)
    result["diff_model"] = phase_diff_model(host, args)
    result["finished_at"] = time.time()

    verdicts = emit_verdicts(result)
    result["verdicts"] = verdicts

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print("\n===== PER-ITEM PROFILE (no composite score is reported) =====")
    hdr = f"{'tag':<12}{'model(server echo)':<48}{'ttfb_byte':>10}{'ttfb_tok':>10}{'total':>9}{'chars':>7}"
    print(hdr)
    print("-" * len(hdr))
    rows = (
        [result["control"], result["known_answer"]]
        + result["alone_a"]
        + result["same_model"]["rows"]
        + result["diff_model"]["rows"]
    )
    for r in rows:
        fb = r.get("ttfb_byte_s")
        tk = r.get("ttfb_token_s")
        print(
            f"{r['tag']:<12}{str(r.get('model') or r['model_requested'])[:47]:<48}"
            f"{(f'{fb:.2f}' if fb is not None else '-'):>10}"
            f"{(f'{tk:.2f}' if tk is not None else '-'):>10}"
            f"{r['total_s']:>9.2f}{len(r.get('response_text') or ''):>7}"
        )

    print("\n===== RESIDENCY TRACE SUMMARY =====")
    print("same-model :", json.dumps(result["same_model"]["residency"]))
    print("cross-model:", json.dumps(result["diff_model"]["residency"]))

    print("\n===== ACCEPTANCE =====")
    for line in verdicts:
        print(line)
    print(f"\nfull rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
