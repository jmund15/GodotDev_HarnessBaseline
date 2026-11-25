"""Planted inputs for the free-pool proofs, cut from the live captures and trimmed.

`.claude/tests/fixtures/` never ships, so a proof that reads it cannot run in another checkout. These
are the same registry rows and catalog lines, trimmed to the fields the proofs read: a frozen registry
whose pool members are all available, the plain and `--verbose` live catalogs, the models.dev entries
the sync cases add rows from, and the row a cat-jam sync produced at capture time. `plant()` writes
them under a caller's temp root.
"""
import json
import re
from pathlib import Path

REGISTRY = {
 "revision": "2026-09-30",
 "reviewGroups": {},
 "familyIssuers": {},
 "transports": {
  "opencode-cli": {
   "imageRoute": {"state": "unverified"},
   "launcher": ".claude/scripts/opencode_run_sidecar.sh",
   "costModel": "marginal-usd",
   "capacityUnsupported": {
    "reason": "The OpenCode CLI's free tier publishes no account amount endpoint."
   },
   "effortValues": ["low", "medium", "high", "xhigh", "max"],
   "_limitsSource": "`opencode models opencode --verbose`, the CLI's live catalog, 2026-09-27. Rows absent from it keep null limits."
  },
  "go": {
   "imageRoute": {"state": "unverified"},
   "costModel": "plan-quota",
   "launcher": ".claude/scripts/opencode_sidecar.sh",
   "effortValues": ["low", "medium", "high", "xhigh", "max"],
   "capacityProbe": {
    "command": "python",
    "args": [".claude/scripts/go_quota_probe.py"],
    "kind": "quota",
    "timeoutSeconds": 20
   },
   "_limitsSource": "models.dev, the `opencode-go` provider's entries only (the cross-provider first hit disagrees for many Go ids) - refreshed by scripts/opencode_refresh_models.py --go. A row without a Go-owned entry keeps null limits."
  }
 },
 "models": [
  {
   "id": "glm-5.3-flash", "alias": "go-glm-5-3-flash", "transport": "go", "label": "glm-5.3-flash (Go)",
   "family": "glm-flash", "version": "glm-5.3-flash", "lifecycle": "current", "order": 182, "costRank": 1,
   "prior": "mid", "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheMissPer1M": 0.15, "cacheHitPer1M": 0.03, "outputPer1M": 0.5, "currency": "USD",
             "asOf": "2026-09-30", "source": "https://models.dev/api.json (the `opencode-go` provider's cost, USD per 1M tokens)"},
   "limits": {"contextTokens": 1000000, "maxOutputTokens": 131072, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "go/hy3", "upstreamId": "hy3", "alias": "go-hy3", "transport": "go", "label": "hy3 (Go)",
   "family": "hy", "version": "hy3", "lifecycle": "current", "order": 186, "costRank": 1, "prior": "mid",
   "roles": ["scout"], "effort": {"evidence": "see-ladder"},
   "price": {"cacheMissPer1M": 0.14, "cacheHitPer1M": 0.035, "outputPer1M": 0.58, "currency": "USD",
             "asOf": "2026-09-30", "source": "https://models.dev/api.json (the `opencode-go` provider's cost, USD per 1M tokens)"},
   "limits": {"contextTokens": 256000, "maxOutputTokens": 128000, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "minimax-m2.5", "alias": "go-minimax-m2-5", "transport": "go", "label": "minimax-m2.5 (Go)",
   "family": "minimax", "version": "minimax-m2.5", "lifecycle": "superseded", "order": 194, "costRank": 1,
   "prior": "mid", "roles": [], "effort": {"evidence": "unmeasured"},
   "priceGap": {"reason": "models.dev lists no complete opencode-go cost for minimax-m2.5.",
                "checked": "https://models.dev/api.json", "asOf": "2026-09-30"},
   "limits": {"contextTokens": 204800, "maxOutputTokens": 65536, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "minimax-m2.7", "alias": "go-minimax-m2-7", "transport": "go", "label": "minimax-m2.7 (Go)",
   "family": "minimax", "version": "minimax-m2.7", "lifecycle": "superseded", "order": 195, "costRank": 1,
   "prior": "mid", "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheMissPer1M": 0.3, "cacheHitPer1M": 0.06, "outputPer1M": 1.2, "cacheWritePer1M": 0.375,
             "currency": "USD", "asOf": "2026-09-30",
             "source": "https://models.dev/api.json (the `opencode-go` provider's cost, USD per 1M tokens)"},
   "limits": {"contextTokens": 204800, "maxOutputTokens": 131072, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "minimax-m3", "alias": "go-minimax-m3", "transport": "go", "label": "minimax-m3 (Go)",
   "family": "minimax", "version": "minimax-m3", "lifecycle": "current", "order": 196, "costRank": 1,
   "prior": "mid", "roles": ["scout"], "effort": {"evidence": "see-ladder"},
   "price": {"cacheMissPer1M": 0.3, "cacheHitPer1M": 0.06, "outputPer1M": 1.2, "currency": "USD",
             "asOf": "2026-09-30", "source": "https://models.dev/api.json (the `opencode-go` provider's cost, USD per 1M tokens)"},
   "limits": {"contextTokens": 1000000, "maxOutputTokens": 131072, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "step-5-preview-free", "alias": "go-step-5-preview-free", "transport": "go",
   "label": "step-5-preview-free (Go)", "family": "step-preview-free", "version": "step-5-preview-free",
   "lifecycle": "current", "order": 219, "costRank": 1, "prior": "mid", "roles": [],
   "effort": {"evidence": "unmeasured"},
   "price": {"cacheMissPer1M": 0, "cacheHitPer1M": 0, "outputPer1M": 0, "currency": "USD", "asOf": "2026-10-10",
             "source": "opencode models opencode --verbose (live CLI catalog; Go's /models lists the id, models.dev has no opencode-go entry yet)"},
   "limits": {"contextTokens": 1000000, "maxOutputTokens": 65536, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/longcat-2.5-preview-free", "upstreamId": "longcat-2.5-preview-free",
   "alias": "cli-longcat-preview", "transport": "opencode-cli", "label": "LongCat 2.5 Preview (CLI)",
   "family": "longcat-preview-free", "version": "longcat-2.5-preview-free", "pool": "free",
   "lifecycle": "current", "order": 206, "costRank": 0, "prior": "mid", "status": {"state": "available"},
   "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-09-27", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 1000000, "maxOutputTokens": 131072, "concurrency": None},
   "effortValues": ["low", "medium", "high"], "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/mimo-v2.6-flash-free", "upstreamId": "mimo-v2.6-flash-free", "alias": "cli-mimo-flash",
   "transport": "opencode-cli", "label": "MiMo V2.6 Flash (CLI)", "family": "mimo-flash",
   "version": "mimo-v2.6-flash-free", "pool": "free", "lifecycle": "current", "order": 207, "costRank": 0,
   "prior": "small", "status": {"state": "available"}, "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-09-27", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 200000, "maxOutputTokens": 32000, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/muse-spark-1.3-contributor-free", "upstreamId": "muse-spark-1.3-contributor-free",
   "alias": "cli-muse", "transport": "opencode-cli", "label": "Muse Spark 1.3 (CLI)", "family": "muse",
   "version": "muse-spark-1.3-contributor-free", "pool": "free", "lifecycle": "current", "order": 208,
   "costRank": 0, "prior": "mid", "status": {"state": "available"}, "roles": [],
   "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-09-27", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 1048576, "maxOutputTokens": 131072, "concurrency": None},
   "effortValues": ["low", "medium", "high", "xhigh"], "authTier": "open",
   "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/space-bunny-free", "upstreamId": "space-bunny-free", "alias": "cli-space-bunny",
   "transport": "opencode-cli", "label": "Space Bunny (CLI)", "family": "space-bunny-free",
   "version": "space-bunny-free", "pool": "free", "lifecycle": "current", "order": 209, "costRank": 0,
   "prior": "mid", "status": {"state": "available"}, "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-09-27", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 1048576, "maxOutputTokens": 524288, "concurrency": None},
   "effortValues": ["low", "medium", "high", "xhigh", "max"], "authTier": "open",
   "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/nemotron-3-ultra-free", "upstreamId": "nemotron-3-ultra-free", "alias": "cli-nemotron-ultra",
   "transport": "opencode-cli", "label": "Nemotron 3 Ultra (CLI)", "family": "nemotron-ultra",
   "version": "nemotron-3-ultra-free", "lifecycle": "current", "order": 210, "costRank": 0, "prior": "small",
   "status": {"state": "unavailable", "since": "2026-09-03",
              "reason": "Owner tested directly: usage limits too low for dispatch (2026-09-03). Re-enable only on the owner's word."},
   "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-09-27", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/nemotron-3.5-lightning-free", "upstreamId": "nemotron-3.5-lightning-free",
   "alias": "cli-lightning", "transport": "opencode-cli", "label": "Nemotron 3.5 Lightning (CLI)",
   "family": "lightning", "version": "nemotron-3.5-lightning-free", "lifecycle": "current", "order": 211,
   "costRank": 0, "prior": "small",
   "status": {"state": "unavailable", "since": "2026-09-03",
              "reason": "Owner tested directly: usage limits too low for dispatch (2026-09-03). Re-enable only on the owner's word."},
   "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-09-27", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 262144, "maxOutputTokens": 262144, "concurrency": None},
   "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/ling-3.1-flash-free", "upstreamId": "ling-3.1-flash-free", "alias": "cli-ling-31",
   "transport": "opencode-cli", "label": "Ling 3.1 Flash (CLI)", "family": "ling",
   "version": "ling-3.1-flash-free", "pool": "free", "lifecycle": "current", "order": 215, "costRank": 0,
   "prior": "small", "status": {"state": "available"}, "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-10-10", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 262144, "maxOutputTokens": 32768, "concurrency": None},
   "effortValues": ["low", "medium", "high"], "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/exo-free", "upstreamId": "exo-free", "alias": "cli-exo", "transport": "opencode-cli",
   "label": "Exo (CLI)", "family": "exo", "version": "exo-free", "pool": "free", "lifecycle": "current",
   "order": 216, "costRank": 0, "prior": "mid", "status": {"state": "available"}, "roles": [],
   "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-10-10", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 1048576, "maxOutputTokens": 131072, "concurrency": None},
   "effortValues": ["high"], "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/step-5-preview-free", "upstreamId": "step-5-preview-free", "alias": "cli-step-preview",
   "transport": "opencode-cli", "label": "Step 5 Preview (CLI)", "family": "step-preview-free",
   "version": "step-5-preview-free", "pool": "free", "lifecycle": "current", "order": 217, "costRank": 0,
   "prior": "mid", "status": {"state": "available"}, "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-10-10", "source": "opencode models opencode --verbose (live CLI catalog)"},
   "limits": {"contextTokens": 1000000, "maxOutputTokens": 65536, "concurrency": None},
   "effortValues": ["low", "medium", "high"], "authTier": "open", "gate": {"minBand": "Surplus"}, "input": ["text"]
  },
  {
   "id": "opencode-cli/cat-jam-free", "upstreamId": "cat-jam-free", "alias": "cli-cat-jam",
   "transport": "opencode-cli", "label": "Cat Jam (CLI)", "family": "cat-jam", "version": "cat-jam-free",
   "pool": "free", "lifecycle": "current", "order": 220, "costRank": 0, "prior": "mid",
   "status": {"state": "available"}, "roles": [], "effort": {"evidence": "unmeasured"},
   "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
             "asOf": "2026-10-13", "source": "opencode models opencode (live CLI catalog); models.dev opencode/cat-jam-free"},
   "limits": {"contextTokens": 262144, "maxOutputTokens": 131072, "concurrency": None},
   "effortValues": ["low", "medium", "high", "xhigh", "max"], "authTier": "open",
   "gate": {"minBand": "Surplus"}, "input": ["text"]
  }
 ]
}

CATALOG_LIVE = "".join(f"opencode/{cid}\n" for cid in (
    "cat-jam-free", "exo-free", "ling-3.1-flash-free", "longcat-2.5-preview-free", "mimo-v2.6-flash-free",
    "muse-spark-1.3-contributor-free", "nemotron-3-ultra-free", "nemotron-3.5-lightning-free",
    "qwen3.8-flash", "space-bunny-free", "step-5-preview-free"))

# The `variants` keys each `opencode models opencode --verbose` header carried; only those keys are read.
VARIANTS = {
    "cat-jam-free": ["low", "medium", "high", "xhigh", "max"],
    "exo-free": ["high"],
    "ling-3.1-flash-free": ["low", "medium", "high"],
    "longcat-2.5-preview-free": ["low", "medium", "high"],
    "mimo-v2.6-flash-free": [],
    "muse-spark-1.3-contributor-free": ["minimal", "low", "medium", "high", "xhigh"],
    "space-bunny-free": ["low", "medium", "high", "xhigh", "max"],
    "step-5-preview-free": ["low", "medium", "high"],
}

MODELS_DEV_CAT_JAM = {
 "opencode": {
  "id": "opencode",
  "env": ["OPENCODE_API_KEY"],
  "npm": "@ai-sdk/openai-compatible",
  "api": "https://opencode.ai/inference/v1",
  "name": "OpenCode",
  "doc": "https://opencode.ai/v2/docs/console/models/",
  "models": {
   "cat-jam-free": {
    "id": "cat-jam-free",
    "name": "Cat Jam Free",
    "description": "A text-only reasoning model for coding and agentic workflows, with tool calling and structured JSON outputs.",
    "attachment": False,
    "reasoning": True,
    "reasoning_options": [{"type": "effort", "values": ["low", "medium", "high", "xhigh", "max"]}],
    "tool_call": True,
    "interleaved": {"field": "reasoning_content"},
    "structured_output": True,
    "temperature": True,
    "release_date": "2026-10-12",
    "last_updated": "2026-10-12",
    "modalities": {"input": ["text"], "output": ["text"]},
    "open_weights": False,
    "limit": {"context": 262144, "output": 131072},
    "cost": {"input": 0, "output": 0, "cache_read": 0}
   }
  }
 }
}

MODELS_DEV_MIMO = {
 "opencode": {
  "models": {
   "mimo-v2.6-flash-free": {
    "id": "mimo-v2.6-flash-free",
    "name": "MiMo-V2.6-Flash Free",
    "description": "MiMo Flash model for multimodal coding agents and long-context automation",
    "family": "mimo",
    "attachment": True,
    "reasoning": True,
    "reasoning_options": [],
    "tool_call": True,
    "interleaved": {"field": "reasoning_content"},
    "temperature": True,
    "release_date": "2026-09-22",
    "last_updated": "2026-09-22",
    "modalities": {"input": ["text", "image", "audio", "video"], "output": ["text"]},
    "open_weights": True,
    "limit": {"context": 200000, "output": 32000},
    "cost": {"input": 0, "output": 0, "cache_read": 0},
    "canonical_model_id": "xiaomi/mimo-v2.6-flash"
   }
  }
 }
}

CAT_JAM_ROW = {
 "id": "opencode-cli/cat-jam-free",
 "upstreamId": "cat-jam-free",
 "alias": "cli-cat-jam",
 "transport": "opencode-cli",
 "label": "Cat Jam (CLI)",
 "family": "cat-jam",
 "version": "cat-jam-free",
 "lifecycle": "current",
 "order": 220,
 "costRank": 0,
 "prior": "mid",
 "status": {"state": "available"},
 "roles": [],
 "limits": {"contextTokens": 262144, "maxOutputTokens": 131072, "concurrency": None},
 "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
           "asOf": "2026-10-13", "source": "opencode models opencode (live CLI catalog); models.dev opencode/cat-jam-free"},
 "effort": {"evidence": "unmeasured"},
 "effortValues": ["low", "medium", "high", "xhigh", "max"],
 "authTier": "open",
 "gate": {"minBand": "Surplus"}
}


def _verbose_text():
    lines = []
    for cid, values in VARIANTS.items():
        block = {"variants": {v: {"reasoningEffort": v} for v in values}}
        lines.append("opencode/%s\n%s\n" % (cid, json.dumps(block)))
    return "".join(lines)


def _registry_text():
    """The registry in the file's own layout: an available status stays on one line, as `free_pool.sync`
    rewrites it in place."""
    text = json.dumps(REGISTRY, indent=1, ensure_ascii=False)
    return re.sub(r'"status": \{\s*"state": "available"\s*\}', '"status": {"state": "available"}', text) + "\n"


def plant(root):
    """Write the planted inputs under `root` and return it."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "registry_pool_s1.json": _registry_text(),
        "catalog_live.txt": CATALOG_LIVE,
        "catalog_verbose_live.txt": _verbose_text(),
        "models_dev_opencode_cat_jam.json": json.dumps(MODELS_DEV_CAT_JAM, indent=2) + "\n",
        "models_dev_opencode_mimo_v26.json": json.dumps(MODELS_DEV_MIMO, indent=2) + "\n",
        "cat_jam_row_a08677e16.json": json.dumps(CAT_JAM_ROW, indent=2) + "\n",
    }
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return root
