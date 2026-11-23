# PushinPotions shared-file commits since the cutover base

Base: PushinPotions `d9f2cbe1a`. Each row is one PushinPotions commit that touched a file its `baseline.lock.json` marks `tracked` or `forked`, with its fate on `v2`. Evidence for a drop is the v2 commit that deleted the file (`git log --diff-filter=D -- <path>`) and its entry in `delete_list.md`.

| PushinPotions commit | File | Fate | v2 evidence or port commit |
|---|---|---|---|
| `b775577fd` composed-output guard judges the checkout the path is in | `hooks/composed_output_guard.py` | drop | Deleted by `5696d6f`; `delete_list.md` lists the hook and `tests/test_composed_output_guard.py`. |
| `e06db59d6` opencode_sidecar `--check` | `scripts/opencode_sidecar.sh`, `tests/test_opencode_sidecar_check.sh` | drop | Deleted by `5696d6f` (`delete_list.md`: `scripts/opencode_sidecar.sh`, three `test_opencode_sidecar_*`). `scripts/seat_claude.sh` and `seat_agy.sh` are the adapters. |
| `50fbaf522` `SC_UPSTREAM_KEY` | same | drop | Same deletion; its only consumer, `local_llama_sidecar.sh`, is not carried (see the launcher row). |
| `5f32dc314` register local transport and inkling-local | `reference/external_models.json` | drop | Deleted by `5696d6f`. `seats.json` holds two seats (`claude-secondary`, `antigravity`) and no local seat, so no row is owed. |
| `5c173d4fc` inkling-local ladder row | `reference/model_ladder_evidence.md` | drop | Deleted by `5696d6f`; `carried_dropped.md` says the model table in `orchestration` replaces it (T4), and v2 keeps no local model. |
| `d9d30a937` ladder row follow-up | same | drop | Same. |
| `2a95dd1ea` agy billing note | same | drop | Same. `seats.json` `antigravity` row carries its own price and source. |
| `9d4d57184` llama-server backend, GPU lock | `commands/workstation_setup.md` | port | `48d99b6` (GPU-lock text in step 5) and `54ef556` (step 5a). The healthcheck sentence is left out: v2 deleted that hook. |
| `8480aa3ed` exe-or-gguf rule | `commands/workstation_setup.md` | port | `54ef556`: "a missing exe or gguf on a `full` box is BLOCKED-ON-USER with its path". |
| `c4aa0aa3a` ollama_healthcheck knows llama_server aliases | `user_scope/hooks/ollama_healthcheck.py` | drop | Deleted by `efdfbce`; `local_rewires.md` line 42: a machine that wants it registers it as its own user-scope hook. |
| `eaffb8438` same, follow-up | same | drop | Same. |
| `e1caa1787` ai_worker_metrics `--by` | `tools/ai_worker_metrics.py`, `tests/test_ai_worker_metrics.py` | drop | Deleted by `5696d6f` (`delete_list.md`: the tool and its test). |
| `6b9e8cee2` worker_rate skip | `tools/worker_rate.py` | drop | Deleted by `5696d6f` (`delete_list.md`: the tool and four `test_worker_rate_*`). |
| `483845777` dispatch_size sizes a big file by stat | `tools/dispatch_size.py`, its test | drop | Deleted by `5696d6f`; `carried_dropped.md` says the launch check refuses unresolved inputs and a job past one context splits. |
| `5b9da4cc0` bench rerun resumes paid work | `tools/work_key.py` | drop | Deleted by `ac490ec` ("no kept command needs a finished-work refusal"); the bench it served is gone. |
| `d176542f8`, `1de683c1e`, `33ed89375` local-transport launcher | `scripts/local_llama_sidecar.sh`, `tests/test_local_llama_sidecar_flags.sh` | drop | Not shared: `baseline.lock.json` has no row for either, so the harness never carried them. v2's seat model has no local seat, so there is nothing to launch. |
