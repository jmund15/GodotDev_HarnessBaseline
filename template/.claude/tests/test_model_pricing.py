"""Proof for list pricing: the registry validator, price_run, the metrics weights and the live registry.

Fixtures plant explicit rates on copies of the live registry, so no expectation here rots when the
vendor moves a price. Run: python3 .claude/tests/test_model_pricing.py
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".claude/tools"
sys.path.insert(0, str(TOOLS))
import model_registry as mr  # noqa: E402

LIVE = json.loads((ROOT / ".claude/reference/external_models.json").read_text(encoding="utf-8"))
# Synthetic ids: planted() renames four live rows so a roster change never breaks a planted case. The
# reference row keeps the alias `sonnet` because quota weights are relative to that alias.
SONNET, OPUS, SOL, ASTRA = "zz-quota-a", "zz-quota-b", "zz-plan-a", "zz-plan-b"
LIVE_ROWS = {"claude-sonnet-5-5": SONNET, "claude-opus-5-5": OPUS, "gpt-6.1-sol": SOL, "gpt-6-astra": ASTRA}
GAP = {"reason": "the vendor publishes no rate", "checked": "https://example.test/pricing", "asOf": "2026-09-30"}


def planted():
    """The live registry with explicit rates on the rows the cases price."""
    data = copy.deepcopy(LIVE)
    for row in data["models"]:
        if row["alias"] == "sonnet":
            row["alias"] = "zz-old-reference"
    for row in data["models"]:
        if row["id"] in LIVE_ROWS:
            new = LIVE_ROWS[row["id"]]
            row.update(id=new, alias="sonnet" if new == SONNET else new + "-alias", version=new, family=new)
            row.pop("servedIds", None)
    data["aliasHistory"] = {}
    rates = {
        SONNET: dict(cacheMissPer1M=2, cacheHitPer1M=0.2, outputPer1M=10, cacheWrite5mPer1M=2.5,
                     cacheWrite1hPer1M=4),
        OPUS: dict(cacheMissPer1M=4, cacheHitPer1M=0.2, outputPer1M=20, cacheWrite5mPer1M=5,
                   cacheWrite1hPer1M=8),
        SOL: dict(cacheMissPer1M=2, cacheHitPer1M=0.1, outputPer1M=10, cacheWritePer1M=2.5),
        ASTRA: dict(cacheMissPer1M=10, cacheHitPer1M=1, outputPer1M=50, cacheWritePer1M=12.5),
    }
    for row in data["models"]:
        if row["id"] in rates:
            row.pop("priceGap", None)
            row["price"] = dict(rates[row["id"]], currency="USD", asOf="2026-09-30",
                                source="https://example.test/pricing")
    data["transports"]["codex"]["longContextTier"] = {
        "thresholdInputTokens": 272000, "multipliers": {"input": 2.0, "cachedInput": 2.0, "output": 1.5},
        "appliesPer": "request", "asOf": "2026-09-30", "source": "https://example.test/pricing"}
    row_of(data, ASTRA)["longContextTier"] = None
    return data


def row_of(data, mid):
    return next(m for m in data["models"] if m["id"] == mid)


def plant_callable(data, **overrides):
    """A copy of the sol row under a new id on the plan-quota codex transport, with no price."""
    row = copy.deepcopy(row_of(data, SOL))
    row.update(id="zz-probe", alias="zz-probe-alias", label="ZZ probe", family="zz-probe", version="zz-probe",
               lifecycle="current", order=max(m["order"] for m in data["models"]) + 1, roles=[])
    row.pop("price", None)
    row.pop("priceGap", None)
    row.pop("effort", None)
    row["effort"] = {"evidence": "unmeasured"}
    row.update(overrides)
    data["models"].append(row)
    return row


class ValidatorTests(unittest.TestCase):
    def test_a_callable_plan_quota_row_with_neither_price_nor_gap_fails(self):
        data = planted()
        plant_callable(data)
        with self.assertRaises(mr.RegistryError) as ctx:
            mr._validate(data, "planted")
        self.assertIn("zz-probe", str(ctx.exception))
        self.assertIn("priceGap", str(ctx.exception))

    def test_a_price_gap_passes_and_a_malformed_gap_fails(self):
        data = planted()
        plant_callable(data, priceGap=dict(GAP))
        mr._validate(data, "planted")
        for broken in ({k: v for k, v in GAP.items() if k != "checked"}, {**GAP, "reason": ""}, "no rate"):
            bad = planted()
            plant_callable(bad, priceGap=broken)
            with self.assertRaises(mr.RegistryError, msg=str(broken)):
                mr._validate(bad, "planted")

    def test_a_retired_unavailable_row_passes_bare(self):
        data = planted()
        plant_callable(data, lifecycle="retired", status={"state": "unavailable", "reason": "retired"})
        mr._validate(data, "planted")

    def test_a_complete_price_needs_its_source(self):
        data = planted()
        del row_of(data, SOL)["price"]["source"]
        with self.assertRaises(mr.RegistryError):
            mr._validate(data, "planted")

    def test_a_row_with_both_a_price_and_a_gap_fails(self):
        data = planted()
        row_of(data, SOL)["priceGap"] = dict(GAP)
        with self.assertRaises(mr.RegistryError):
            mr._validate(data, "planted")

    def test_a_price_gap_row_is_not_priced_and_says_why(self):
        data = planted()
        plant_callable(data, priceGap=dict(GAP))
        with self.assertRaises(mr.RegistryError) as ctx:
            mr.price_run("zz-probe", 1, 1, 1, data=data)
        self.assertIn(GAP["reason"], str(ctx.exception))


class PriceRunTests(unittest.TestCase):
    def setUp(self):
        self.data = planted()

    def cost(self, model, *args, **kw):
        return mr.price_run(model, *args, data=self.data, **kw)

    def test_the_old_positional_call_is_unchanged_when_there_are_no_writes(self):
        self.assertAlmostEqual(self.cost(SONNET, 1e6, 1e6, 1e6), 2 + 0.2 + 10)

    def test_an_anthropic_row_prices_writes_at_the_five_minute_rate_and_one_hour_at_its_own(self):
        self.assertAlmostEqual(self.cost(SONNET, 0, 0, 0, cache_write=1e6), 2.5)
        self.assertAlmostEqual(self.cost(SONNET, 0, 0, 0, cache_write=1e6, cache_write_1h=1e6), 2.5 + 4)

    def test_a_single_write_rate_row_prices_both_write_arguments_at_that_rate(self):
        self.assertAlmostEqual(self.cost(SOL, 1e6, 1e6, 1e6, cache_write=1e6), 2 + 0.1 + 10 + 2.5)

    def test_a_row_with_no_write_rate_prices_writes_at_its_miss_rate(self):
        del row_of(self.data, SOL)["price"]["cacheWritePer1M"]
        self.assertAlmostEqual(self.cost(SOL, 0, 0, 0, cache_write=1e6), 2.0)

    def test_a_request_past_the_threshold_prices_every_rate_at_the_long_tier(self):
        below = self.cost(SOL, 1e6, 1e6, 1e6, cache_write=1e6, request_input=272000)
        above = self.cost(SOL, 1e6, 1e6, 1e6, cache_write=1e6, request_input=272001)
        self.assertAlmostEqual(below, 2 + 0.1 + 10 + 2.5)
        self.assertAlmostEqual(above, 2 * 2 + 2 * 0.1 + 1.5 * 10 + 2 * 2.5)

    def test_a_row_whose_tier_is_null_stays_at_its_base_rates(self):
        self.assertAlmostEqual(self.cost(ASTRA, 1e6, 0, 1e6, request_input=300000), 10 + 50)

    def test_an_explicit_multiplier_replaces_the_window_lookup(self):
        self.assertAlmostEqual(self.cost(SONNET, 1e6, 0, 0, multiplier=0.5), 1.0)

    def test_a_transport_with_no_long_tier_ignores_request_input(self):
        self.assertAlmostEqual(self.cost(SONNET, 1e6, 0, 0, request_input=900000), 2)

    def test_a_row_tier_prices_that_row_alone_past_its_own_threshold(self):
        row_of(self.data, OPUS)["longContextTier"] = {
            "thresholdInputTokens": 100000, "multipliers": {"input": 5.0, "cachedInput": 5.0, "output": 5.0},
            "appliesPer": "request", "asOf": "2026-10-07"}
        self.assertAlmostEqual(self.cost(OPUS, 1e6, 1e6, 1e6, request_input=100000), 4 + 0.2 + 20)
        self.assertAlmostEqual(self.cost(OPUS, 1e6, 1e6, 1e6, request_input=100001), 5 * (4 + 0.2 + 20))
        self.assertAlmostEqual(self.cost(SONNET, 1e6, 0, 0, request_input=900000), 2)

    def test_the_tier_resolver_and_its_phrase_serve_every_reader(self):
        self.assertEqual(mr.long_context_tier(row_of(self.data, ASTRA), self.data), None)
        self.assertEqual(mr.long_context_tier(row_of(self.data, SOL), self.data)["thresholdInputTokens"], 272000)
        self.assertEqual(mr.long_context_tier(row_of(self.data, SONNET), self.data), None)
        self.assertEqual(mr.tier_phrase(mr.long_context_tier(row_of(self.data, SOL), self.data)),
                         "input x2, output x1.5 past 272k input tokens per request")
        self.assertEqual(mr.tier_phrase({"thresholdInputTokens": 100000,
                                         "multipliers": {"input": 5.0, "cachedInput": 5.0, "output": 5.0}}),
                         "x5 past 100k input tokens per request")
        self.assertEqual(mr.tier_phrase(None), "")


class TierCheckTests(unittest.TestCase):
    def check(self, data):
        mr._validate(data, "planted")

    def test_a_well_formed_row_tier_and_a_null_opt_out_pass(self):
        data = planted()
        row_of(data, OPUS)["longContextTier"] = {
            "thresholdInputTokens": 100000, "multipliers": {"input": 5.0, "output": 5.0},
            "appliesPer": "request", "asOf": "2026-10-07", "source": "https://example.test/pricing"}
        self.check(data)

    def test_a_malformed_tier_fails_naming_its_owner(self):
        for bad in ({"thresholdInputTokens": 0, "multipliers": {"input": 5, "output": 5}, "asOf": "x", "source": "y"},
                    {"thresholdInputTokens": 100000, "multipliers": {"input": 5}, "asOf": "x", "source": "y"},
                    {"thresholdInputTokens": 100000, "multipliers": {"input": 5, "output": 5}}):
            data = planted()
            row_of(data, OPUS)["longContextTier"] = bad
            with self.assertRaises(mr.RegistryError) as ctx:
                self.check(data)
            self.assertIn("longContextTier", str(ctx.exception))
        data = planted()
        data["transports"]["codex"]["longContextTier"] = None
        with self.assertRaises(mr.RegistryError):
            self.check(data)


class MetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import orchestration_metrics as om
        cls.om = om

    def setUp(self):
        self.data = planted()

    def test_the_quota_weight_is_the_registry_output_rate_relative_to_sonnet(self):
        self.assertAlmostEqual(self.om.quota_weight(SONNET, data=self.data), 1.0)
        self.assertAlmostEqual(self.om.quota_weight(OPUS, data=self.data), 2.0)
        row_of(self.data, OPUS)["price"]["outputPer1M"] = 30
        self.assertAlmostEqual(self.om.quota_weight(OPUS + "-alias", data=self.data), 3.0)

    def test_an_unpriced_unregistered_or_other_plan_model_has_no_weight(self):
        self.assertIsNone(self.om.quota_weight("mystery-model", data=self.data))
        self.assertIsNone(self.om.quota_weight(SOL, data=self.data))
        plant_callable(self.data, priceGap=dict(GAP))
        self.assertIsNone(self.om.quota_weight("zz-probe", data=self.data))
        self.assertIsNone(self.om.quota_cost(1000.0, "mystery-model", data=self.data))

    def test_a_sidecar_plan_quota_run_carries_list_usd_and_no_spend(self):
        ledger = Path(tempfile.mkdtemp(prefix="ledger_")) / "ledger.jsonl"
        base = {"label": "a", "timestamp": "2026-09-30T00:00:00Z", "exitCode": 0, "servedModel": SOL,
                "effort": "max"}
        rows = [dict(base, label="quota", costModel="plan-quota", costUSD=4.0,
                     costBasis=mr.LIST_PRICE_BASIS),
                dict(base, label="paid", costModel="marginal-usd", costUSD=3.0, costBasis="deepseek-flash"),
                dict(base, label="old", costModel="plan-quota", costUSD=None,
                     costBasis="plan-quota (no marginal cost)")]
        ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        by = {r["label"]: r for r in self.om.collect_sidecar(str(ledger))}
        self.assertIsNone(by["quota"]["cost_usd"])
        self.assertEqual(by["quota"]["list_usd"], 4.0)
        self.assertEqual((by["paid"]["cost_usd"], by["paid"]["list_usd"]), (3.0, 3.0))
        self.assertIsNone(by["old"]["cost_usd"])
        self.assertIsNone(by["old"]["list_usd"])
        cells = self.om.model_effort_cells([("sc", r) for r in by.values()])
        self.assertEqual((cells[0]["usd"], cells[0]["usd_n"]), (3.0, 1))

    def test_a_workflow_agent_row_is_priced_through_price_run(self):
        usage = dict(inp=1000000, out=100000, cw=1000000, cr=2000000)
        self.assertAlmostEqual(self.om.list_usd(SONNET, usage, data=self.data), 2 + 0.4 + 1.0 + 2.5)
        self.assertIsNone(self.om.list_usd("mystery-model", usage, data=self.data))
        self.assertIsNone(self.om.list_usd(SONNET, dict(usage, inp=None), data=self.data))


class LiveRegistryTests(unittest.TestCase):
    def test_every_callable_row_carries_a_price_or_a_price_gap(self):
        bare = [m["id"] for m in LIVE["models"] if not m.get("price") and not m.get("priceGap")
                and not (m.get("lifecycle") == "retired" and (m.get("status") or {}).get("state") == "unavailable")]
        self.assertEqual(bare, [])
        mr._validate(copy.deepcopy(LIVE), "live")


if __name__ == "__main__":
    unittest.main(verbosity=2)
