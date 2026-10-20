"""Proof for tools/run_cost.py: one table that prices and times any mix of runs from the registry.

Fixtures plant explicit rates through test_model_pricing.planted(), so no expectation rots when a vendor
moves a price. Run: python3 .claude/tests/test_run_cost.py
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_model_pricing import GAP, SOL, SONNET, TOOLS, planted, plant_callable  # noqa: E402


def load_run_cost():
    path = TOOLS / "run_cost.py"
    spec = importlib.util.spec_from_file_location("run_cost", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run_cost_"))
        self.data = planted()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, body):
        path = self.tmp / name
        path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        return str(path)

    def sonnet_result(self):
        return {"type": "result", "duration_ms": 600000, "duration_api_ms": 300000, "num_turns": 10,
                "total_cost_usd": 999.0,
                "usage": {"cache_creation": {"ephemeral_5m_input_tokens": 300000,
                                             "ephemeral_1h_input_tokens": 200000}},
                "modelUsage": {SONNET: {"inputTokens": 1000000, "outputTokens": 100000,
                                        "cacheReadInputTokens": 2000000, "cacheCreationInputTokens": 500000,
                                        "canonicalModel": SONNET, "costUSD": 999.0}}}

    def sol_record(self, label):
        return {"label": label, "servedModel": SOL, "costModel": "plan-quota", "inputTokens": 1000000,
                "cacheReadTokens": 10000000, "outputTokens": 100000, "durationMs": 1200000,
                "apiDurationMs": None, "numTurns": 20, "costUSD": 123.0}

    def run_tool(self, *argv):
        rc = load_run_cost()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rc.main(list(argv), data=self.data)
        return code, out.getvalue()

    def test_an_anthropic_result_prices_from_tokens_never_from_total_cost_usd(self):
        path = self.put("sonnet-a.json", self.sonnet_result())
        _, out = self.run_tool("--json", path)
        run = json.loads(out)["runs"][0]
        self.assertEqual(run["model"], SONNET)
        self.assertAlmostEqual(run["usd"], 2 + 0.4 + 1.0 + 0.3 * 2.5 + 0.2 * 4)
        self.assertEqual((run["fresh"], run["cache_read"], run["cache_write"], run["output"]),
                         (1000000, 2000000, 500000, 100000))
        self.assertEqual((run["turns"], run["wall_min"], run["api_min"]), (10, 10.0, 5.0))
        self.assertAlmostEqual(run["out_tok_per_sec"], 100000 / 300)
        self.assertEqual(run["rate_basis"], "api")
        self.assertAlmostEqual(run["sec_per_turn"], 60.0)

    def test_a_codex_record_prices_at_the_list_rate_and_marks_the_wall_second_rate(self):
        path = self.put("sol-a.record.json", self.sol_record("sol-a"))
        _, out = self.run_tool("--json", path)
        run = json.loads(out)["runs"][0]
        self.assertEqual((run["label"], run["model"]), ("sol-a", SOL))
        self.assertAlmostEqual(run["usd"], 2 + 1.0 + 1.0)
        self.assertIsNone(run["api_min"])
        self.assertEqual(run["rate_basis"], "wall")
        self.assertAlmostEqual(run["out_tok_per_sec"], 100000 / 1200)

    def test_a_mixed_model_record_prices_each_model_from_its_stream_once(self):
        """Slice 7: a daemon record lists its driver and its judges (servedModel is a list) and keeps only the summed
        tokens. Its stream's result event carries the per-model split, and stream-json repeats that event."""
        rec = dict(self.sol_record("daemon:ARM-T1-X"), servedModel=[SOL, SONNET])
        record = self.put("cell.t1.daemon.record.json", rec)
        result = {"type": "result", "duration_ms": 60000, "duration_api_ms": 30000, "num_turns": 4,
                  "modelUsage": {SOL: {"inputTokens": 10, "outputTokens": 1000, "cacheReadInputTokens": 0,
                                       "cacheCreationInputTokens": 0, "canonicalModel": SOL},
                                 SONNET: {"inputTokens": 20, "outputTokens": 2000, "cacheReadInputTokens": 0,
                                          "cacheCreationInputTokens": 0, "canonicalModel": SONNET}}}
        self.put("cell.t1.daemon.stream.jsonl", json.dumps(result) + "\n" + json.dumps(result) + "\n")
        _, out = self.run_tool("--json", record)
        doc = json.loads(out)
        self.assertEqual(len(doc["runs"]), 2, out)
        runs = {r["model"]: r for r in doc["runs"]}
        self.assertEqual(set(runs), {SOL, SONNET}, out)
        self.assertTrue(all(r.get("usd") is not None for r in runs.values()), out)
        self.assertEqual((runs[SOL]["output"], runs[SONNET]["output"]), (1000, 2000), "each model counted once")
        self.assertTrue(all(r["label"].startswith("daemon:ARM-T1-X") for r in runs.values()), out)

    def mixed_result(self):
        return {"type": "result", "duration_ms": 60000, "duration_api_ms": 30000, "num_turns": 4,
                "modelUsage": {SOL: {"inputTokens": 10, "outputTokens": 1000, "cacheReadInputTokens": 0,
                                     "cacheCreationInputTokens": 0, "canonicalModel": SOL},
                               SONNET: {"inputTokens": 20, "outputTokens": 2000, "cacheReadInputTokens": 0,
                                        "cacheCreationInputTokens": 0, "canonicalModel": SONNET}}}

    def test_a_mixed_model_record_keeps_its_price_multiplier_on_every_recovered_row(self):
        rec = dict(self.sol_record("daemon:ARM-T1-M"), servedModel=[SOL, SONNET])
        for side, body in (("full", rec), ("half", dict(rec, priceMultiplier=0.5))):
            (self.tmp / side).mkdir()
            self.put(side + "/cell.record.json", body)
            self.put(side + "/cell.stream.jsonl", json.dumps(self.mixed_result()) + "\n")
        _, full = self.run_tool("--json", str(self.tmp / "full" / "cell.record.json"))
        _, half = self.run_tool("--json", str(self.tmp / "half" / "cell.record.json"))
        full = {r["model"]: r["usd"] for r in json.loads(full)["runs"]}
        half = {r["model"]: r["usd"] for r in json.loads(half)["runs"]}
        self.assertEqual(set(half), {SOL, SONNET}, half)
        for model in (SOL, SONNET):
            self.assertAlmostEqual(half[model], full[model] * 0.5, msg=model)

    def test_a_mixed_model_record_with_a_custom_name_reads_its_launcher_out_file(self):
        rec = dict(self.sol_record("daemon:ARM-T1-R"), servedModel=[SOL, SONNET])
        record = self.put("rec.json", rec)
        self.put("rec.json.out", json.dumps(self.mixed_result()) + "\n")
        _, out = self.run_tool("--json", record)
        runs = {r["model"]: r for r in json.loads(out)["runs"]}
        self.assertEqual(set(runs), {SOL, SONNET}, out)
        self.assertTrue(all(r.get("usd") is not None for r in runs.values()), out)

    def test_an_attested_upstream_model_outranks_the_clients_served_model(self):
        rec = dict(self.sol_record("proxy-a"), servedModel=SONNET, attestedModel=SOL)
        _, out = self.run_tool("--json", self.put("proxy-a.record.json", rec))
        run = json.loads(out)["runs"][0]
        self.assertEqual(run["model"], SOL, out)
        self.assertAlmostEqual(run["usd"], 4.0)

    def test_an_attested_proxy_record_whose_client_reports_several_models_prices_the_upstream(self):
        rec = dict(self.sol_record("proxy-b"), servedModel=[SOL, SONNET], attestedModel=SOL)
        _, out = self.run_tool("--json", self.put("proxy-b.record.json", rec))
        run = json.loads(out)["runs"][0]
        self.assertEqual(run["model"], SOL, out)
        self.assertAlmostEqual(run["usd"], 4.0)

    def test_a_mixed_model_record_without_a_stream_stays_unpriced(self):
        rec = dict(self.sol_record("daemon:ARM-T1-Y"), servedModel=[SOL, SONNET])
        _, out = self.run_tool("--json", self.put("lone.t1.daemon.record.json", rec))
        run = json.loads(out)["runs"][0]
        self.assertIsNone(run.get("usd"), out)
        self.assertIn("several served models", run.get("problem") or "", out)

    def test_a_detached_stub_reads_its_sibling_record(self):
        self.put("sol-b.record.json", self.sol_record("sol-b"))
        stub = self.put("sol-b.out.json", "DETACHED pid=1\nOUT=x\n")
        _, out = self.run_tool("--json", stub)
        run = json.loads(out)["runs"][0]
        self.assertAlmostEqual(run["usd"], 4.0)

    def test_a_file_with_no_usage_is_reported_and_never_priced_as_zero(self):
        empty = self.put("none.json", {"type": "system"})
        good = self.put("sol-a.record.json", self.sol_record("sol-a"))
        code, out = self.run_tool("--json", empty, good)
        doc = json.loads(out)
        bad = next(r for r in doc["runs"] if r["label"] == "none")
        self.assertIsNone(bad["usd"])
        self.assertIn("no usage", bad["problem"])
        self.assertAlmostEqual(doc["total_usd"], 4.0)
        self.assertEqual(doc["unpriced"], 1)
        self.assertNotEqual(code, 0)
        _, text = self.run_tool(empty, good)
        self.assertIn("NO USAGE", text)

    def test_a_model_usage_entry_without_token_counts_is_never_priced_as_zero(self):
        result = self.sonnet_result()
        result["modelUsage"][SONNET] = {"canonicalModel": SONNET, "costUSD": 1.0}
        code, out = self.run_tool("--json", self.put("torn.json", result))
        run = json.loads(out)["runs"][0]
        self.assertIsNone(run["usd"])
        self.assertIn("no usage", run["problem"])
        self.assertNotEqual(code, 0)

    def test_a_model_the_registry_does_not_price_is_reported_unpriced(self):
        plant_callable(self.data, priceGap=dict(GAP))
        rec = self.sol_record("gap")
        rec["servedModel"] = "zz-probe"
        _, out = self.run_tool("--json", self.put("gap.record.json", rec))
        run = json.loads(out)["runs"][0]
        self.assertIsNone(run["usd"])
        self.assertIn(GAP["reason"], run["problem"])

    def test_by_groups_runs_by_label_prefix_and_totals_each_group(self):
        files = [self.put("sol-a.record.json", self.sol_record("sol-a")),
                 self.put("sol-b.record.json", self.sol_record("sol-b")),
                 self.put("sonnet-a.json", self.sonnet_result())]
        _, out = self.run_tool("--json", "--by", "^(sol|sonnet)", *files)
        groups = json.loads(out)["groups"]
        self.assertEqual({g: v["runs"] for g, v in groups.items()}, {"sol": 2, "sonnet": 1})
        self.assertAlmostEqual(groups["sol"]["usd"], 8.0)
        _, text = self.run_tool("--by", "^(sol|sonnet)", *files)
        self.assertRegex(text, r"(?m)^sol\s+2\s")
        self.assertRegex(text, r"(?m)^sonnet\s+1\s")

    def test_each_run_splits_its_list_usd_into_the_four_billed_parts(self):
        _, out = self.run_tool("--json", self.put("sonnet-a.json", self.sonnet_result()))
        run = json.loads(out)["runs"][0]
        parts = run["usd_parts"]
        self.assertAlmostEqual(parts["fresh"], 2.0)
        self.assertAlmostEqual(parts["cache_read"], 0.4)
        self.assertAlmostEqual(parts["cache_write"], 0.3 * 2.5 + 0.2 * 4)
        self.assertAlmostEqual(parts["output"], 1.0)
        self.assertAlmostEqual(sum(parts.values()), run["usd"])

    def test_pair_joins_the_same_cell_across_two_sides_and_ratios_only_shared_cells(self):
        (self.tmp / "a").mkdir()
        (self.tmp / "b").mkdir()
        two_models = self.sonnet_result()
        two_models["modelUsage"][SOL] = {"inputTokens": 0, "outputTokens": 100000, "cacheReadInputTokens": 0,
                                         "cacheCreationInputTokens": 0, "canonicalModel": SOL}
        a1 = self.put("a/cell1.stream", "\n".join(json.dumps(x) for x in (two_models, two_models)))
        a2 = self.put("a/cell2.json", self.sonnet_result())
        b1 = self.put("b/cell1.record.json", self.sol_record("cell1"))
        b3 = self.put("b/cell3.record.json", self.sol_record("cell3"))
        _, out = self.run_tool("--json", "--a", a1, a2, "--b", b1, b3)
        pair = json.loads(out)["pair"]
        a_cell1 = 2 + 0.4 + 1.0 + 0.5 * 2.5 + 1.0    # two models: the 1h write share is unattributable, so 5m rate
        self.assertEqual([c["cell"] for c in pair["cells"]], ["cell1"])
        self.assertAlmostEqual(pair["cells"][0]["a_usd"], a_cell1)
        self.assertAlmostEqual(pair["cells"][0]["b_usd"], 4.0)
        self.assertAlmostEqual(pair["ratio_b_over_a"], 4.0 / a_cell1)
        self.assertEqual((pair["only_a"], pair["only_b"]), (["cell2"], ["cell3"]))
        self.assertAlmostEqual(pair["a"]["usd_parts"]["output"], 2.0)
        self.assertEqual(pair["a"]["tokens"]["output"], 200000)
        _, text = self.run_tool("--a", a1, a2, "--b", b1, b3)
        self.assertIn("B/A", text)
        self.assertIn("only in A: cell2", text)

    def test_paths_beside_a_pair_are_refused_never_dropped(self):
        a = self.put("a1.json", self.sonnet_result())
        b = self.put("b1.record.json", self.sol_record("a1"))
        extra = self.put("extra.json", self.sonnet_result())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_tool(extra, "--a", a, "--b", b)
        self.assertEqual(raised.exception.code, 2)

    def test_by_beside_a_pair_is_refused_never_dropped(self):
        a = self.put("a1.json", self.sonnet_result())
        b = self.put("b1.record.json", self.sol_record("a1"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_tool("--by", "^(a)", "--a", a, "--b", b)
        self.assertEqual(raised.exception.code, 2)

    def test_a_workflow_run_dir_prices_each_agent_transcript(self):
        run_dir = self.tmp / "wf"
        run_dir.mkdir()
        lines = []
        for n, (inp, out, cr, cw) in enumerate([(1000000, 100000, 2000000, 1000000)], start=1):
            lines.append({"type": "assistant", "timestamp": "2026-09-30T10:00:00Z",
                          "message": {"id": "m%d" % n, "model": SONNET,
                                      "usage": {"input_tokens": inp, "output_tokens": out,
                                                "cache_read_input_tokens": cr,
                                                "cache_creation_input_tokens": cw}, "content": []}})
        (run_dir / "agent-a1.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        (run_dir / "journal.jsonl").write_text("{}\n", encoding="utf-8")
        _, out = self.run_tool("--json", str(run_dir))
        run = json.loads(out)["runs"][0]
        self.assertEqual(run["model"], SONNET)
        self.assertAlmostEqual(run["usd"], 2 + 0.4 + 1.0 + 2.5)
        self.assertEqual(run["turns"], 1)

    def test_a_haiku_request_over_its_long_context_threshold_bills_the_tier_per_request(self):
        row = next(r for r in self.data["models"] if r["id"] == "claude-haiku-5-5")
        tier, price = row["longContextTier"], row["price"]
        limit = tier["thresholdInputTokens"]
        run_dir = self.tmp / "wf_haiku"
        run_dir.mkdir()
        lines = []
        for n, cache_read in enumerate((limit - 1000, limit + 1000), start=1):
            lines.append({"type": "assistant", "timestamp": "2026-10-13T10:00:00Z",
                          "message": {"id": "h%d" % n, "model": "claude-haiku-5-5",
                                      "usage": {"input_tokens": 0, "output_tokens": 1000,
                                                "cache_read_input_tokens": cache_read,
                                                "cache_creation_input_tokens": 0}, "content": []}})
        (run_dir / "agent-h1.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        (run_dir / "journal.jsonl").write_text("{}\n", encoding="utf-8")
        _, out = self.run_tool("--json", str(run_dir))
        run = json.loads(out)["runs"][0]
        below = ((limit - 1000) * price["cacheHitPer1M"] + 1000 * price["outputPer1M"]) / 1e6
        above = ((limit + 1000) * price["cacheHitPer1M"] * tier["multipliers"]["cachedInput"]
                 + 1000 * price["outputPer1M"] * tier["multipliers"]["output"]) / 1e6
        self.assertGreater(tier["multipliers"]["output"], 1.0)
        self.assertAlmostEqual(run["usd"], below + above)
        self.assertEqual(run["tier_basis"], "per-request")


if __name__ == "__main__":
    unittest.main(verbosity=2)
