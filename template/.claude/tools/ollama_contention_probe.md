# Ollama contention probe

`ollama_contention_probe.py` settles, by measurement, whether concurrent ai-worker/Ollama calls
from two sessions serialize safely. Stdlib only; no dependencies.

## Required GPU state: IDLE

The probe **aborts** if `/api/ps` shows any resident model at start, because a peer workload
contaminates every duration it records. Wait for the GPU to clear, or pass `--force` to stamp a
contaminated run (its Q1/Q2 timings are then advisory only).

Verify by hand first:

    curl -s http://localhost:11434/api/ps        # want {"models":[]}

## How to run

Two runs make the Q4 pair. `--expect-max-loaded` states the value the **server** was started
with; the script asserts it against server-observed residency rather than trusting it.

    # A: server started normally (this machine's current env sets OLLAMA_NUM_PARALLEL=1 only)
    python ollama_contention_probe.py --expect-max-loaded 2 --out run_default.json

    # B: restart `ollama serve` with OLLAMA_MAX_LOADED_MODELS=1, then
    python ollama_contention_probe.py --expect-max-loaded 1 --out run_maxloaded1.json

    # then diff the two verdict blocks / JSON files

The script never restarts or reconfigures the server, and never writes an env var — changing the
server's env is a host action outside its authority, and a client-side `os.environ` write would
not reach the already-running `ollama serve` anyway.

## Expected runtime

Roughly 4-9 minutes per run on an idle GPU: one 3B control call, one 3B known-answer call, three
solo 27B baselines, one same-model concurrent pair, one cross-model concurrent pair. The 27B cold
load dominates; a warm second run is faster. `--reps 1` cuts it to ~2-3 minutes but drops the
baseline below the 3-rep floor a calibration claim needs.

## What the phases are for

| phase | purpose |
|---|---|
| stamp | server version, installed models, residency at start. Client env is recorded **separately** and labelled `client_belief` — it is never used as evidence. |
| control | proves the instrument reaches the field: a small model returns non-empty streamed output. If CONTROL fails, every verdict below it is void. |
| known-answer | proves the scorer **accepts a correct answer** (`42`) as well as rejecting an empty one and a wrong one. Both polarities print. |
| solo baseline | `--reps` runs of model A alone. The denominator for every contention ratio. |
| same-model pair | Q1 and Q3. |
| cross-model pair | Q2 and Q4, with `/api/ps` sampled every 0.4s throughout. |

## What each verdict licenses

- **`CONTROL FAIL`** — nothing else in the run may be cited. Fix the instrument and re-run.
- **`SCORER FAIL`** — the pass/fail bits are meaningless; the durations are still readable.
- **`Q1 SERIALIZED`** — the second concurrent call waited. Licenses: two sessions may call the same
  local model concurrently without corrupting each other, at the cost of wall time.
  **`Q1 OVERLAPPED-OR-BATCHED`** — the calls ran together. Licenses nothing about safety on its
  own; check `OLLAMA_NUM_PARALLEL` on the server and re-read the per-item durations.
  Either way, `all_succeeded` is the safety bit; the ratio is only the cost.
- **`Q2 NO THRASH OBSERVED`** — across the sampled trace no model left and returned. Licenses:
  cross-model concurrency at this pair of sizes did not pay a reload. It does **not** license the
  claim for larger pairs that will not co-fit in VRAM — that is a different experiment.
  **`Q2 THRASH OBSERVED`** — the named models reloaded; `cost` is the wall-clock price.
- **`Q3 QUEUED-TTFB <t>s`** — the single most actionable number here. Any inactivity timeout on an
  ai-worker/MCP call must exceed this, or it will kill queued calls that would have succeeded.
  Set the timeout from the observed value plus headroom, never from total duration.
- **`Q4 CONSISTENT/CONTRADICTED`** — one run stamps one side. `observed > 1` **disproves**
  `MAX_LOADED_MODELS=1`; `observed <= 1` is only consistent with it, because a 0.4s sampler can
  miss a brief overlap. The Q4 answer proper comes from diffing the two runs.

## Known limits (read before quoting a result)

1. **Ollama exposes no configuration endpoint.** `OLLAMA_MAX_LOADED_MODELS` and
   `OLLAMA_NUM_PARALLEL` cannot be read back from the server. The probe's substitute is behavioural
   — observed maximum concurrent residency — which is asymmetric evidence: it can refute a limit of
   1, never confirm one.
2. **Absence of thrash is sampled, not continuous.** At 0.4s a load/evict cycle shorter than one
   interval is invisible. `samples_ok/samples_total` prints so a degraded trace is visible.
3. **n=2 concurrency only.** Whether a third caller queues gracefully is not tested.
4. **One PASS is conclusive, N failures are not.** A single clean run does not establish that
   concurrency is always safe; it establishes that it can be.
