"""Request pricing proofs. Fixture cut from ctx1m-haiku55-t11-2026-10-08/v13cells/
ARM-T11-HAIKU55-r1.progress.jsonl: first message below and above 100k, all repeats;
content and wire_tool_inputs removed. Record keeps only pricing/timing fields.
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/tools'))
import model_registry as mr
import run_cost
import orchestration_metrics as metrics
import plan_usage_calc

FIX = Path(__file__).parent / 'fixtures/pricing'
MODEL = 'claude-haiku-5-5'

class RequestPricingTests(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(mr.load())
        row = mr.resolve(MODEL, self.data)
        row['price'].update(cacheMissPer1M=1, cacheHitPer1M=0.1, outputPer1M=5,
                            cacheWrite5mPer1M=1.25, cacheWrite1hPer1M=2)
        self.events = [json.loads(s) for s in (FIX / 'haiku55.progress.jsonl').read_text().splitlines()]
        self.rec = json.loads((FIX / 'haiku55.record.json').read_text())
        self.full_rec = dict(self.rec)
        self.rec['sessionId'] = None
        calls = mr.request_usage(self.events)
        for key, field in dict(fresh='inputTokens', cache_read='cacheReadTokens',
                               output='outputTokens', cache_write_1h='cacheWrite1hTokens').items():
            self.rec[field] = sum(c[key] for c in calls)
        self.rec['cacheWriteTokens'] = sum(c['cache_write'] + c['cache_write_1h'] for c in calls)
        # 37,698-token request at base; 126,696-token request at x5, including 1h writes.
        self.expected = (2 + 37696 * 2 + 8 * 5 + 5 * (2 + 97767 * 0.1 + 28927 * 2 + 8 * 5)) / 1e6

    def test_last_usage_per_message_and_both_tiers(self):
        requests = mr.request_usage(self.events)
        self.assertEqual(len(requests), 2)
        self.assertAlmostEqual(mr.price_requests(MODEL, requests, data=self.data, multiplier=1), self.expected)
        changed = copy.deepcopy(self.events)
        changed[-1]['message']['usage']['output_tokens'] = 50
        self.assertAlmostEqual(mr.price_requests(MODEL, mr.request_usage(changed), data=self.data, multiplier=1),
                               self.expected + 42 * 5 * 5 / 1e6)

    def test_no_tier_keeps_base_price(self):
        mr.resolve(MODEL, self.data)['longContextTier'] = None
        requests = mr.request_usage(self.events)
        expected = (2 + 37696 * 2 + 8 * 5 + 2 + 97767 * 0.1 + 28927 * 2 + 8 * 5) / 1e6
        self.assertAlmostEqual(mr.price_requests(MODEL, requests, data=self.data, multiplier=1), expected)

    def test_record_uses_progress_and_parts_keep_full_prompt_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'cell.record.json'
            p.write_text(json.dumps(self.rec))
            p.with_name('cell.progress.jsonl').write_text((FIX / 'haiku55.progress.jsonl').read_text())
            r = run_cost.price(run_cost.load_runs(str(p))[0], self.data)
            self.assertAlmostEqual(r['usd'], self.expected)
            self.assertEqual(r['tier_basis'], 'per-request (stream-partial-output)')
            self.assertAlmostEqual(sum(r['usd_parts'].values()), self.expected)

    def test_no_sibling_is_base_and_labeled(self):
        self.rec = dict(self.full_rec, sessionId=None)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'cell.record.json'
            p.write_text(json.dumps(self.rec))
            r = run_cost.price(run_cost.load_runs(str(p))[0], self.data)
            self.assertEqual(r['tier_basis'], 'untiered')
            self.assertAlmostEqual(r['usd'], (64 + 5449021 * .1 + 300333 * 2 + 145051 * 5) / 1e6)
            self.assertIn('untiered', run_cost.render([r], None, r['usd'], 0))

    def test_disjoint_campaigns_compare_totals_not_empty_intersection(self):
        def side(label):
            r = run_cost._run(label, MODEL, fresh=2, cache_read=0, cache_write=0, output=8)
            return run_cost.price(r, self.data)
        p = run_cost.pair([side('cfgb:t11:r1')], [side('ctx1m:t11:r1')])
        self.assertEqual(p['comparison'], 'side-totals (no shared cells)')
        self.assertGreater(p['a']['usd'], 0)
        self.assertGreater(p['b']['usd'], 0)
        self.assertIn('no shared cells', run_cost.render_pair(p))

    def test_workflow_usage_prices_requests(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'agent-test.jsonl').write_text((FIX / 'haiku55.progress.jsonl').read_text())
            u = metrics.agent_usage(td)['test']
            self.assertAlmostEqual(metrics.list_usd(MODEL, u, data=self.data), self.expected)
            self.assertEqual(u['tier_basis'], 'per-request')
            r = run_cost.price(run_cost._from_dir(td)[0], self.data)
            self.assertAlmostEqual(r['usd'], self.expected)

    def test_sidecar_record_writer_prices_progress_and_marks_basis(self):
        import os
        import subprocess
        import sidecar_launch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'registry.json').write_text(json.dumps(self.data))
            result = json.loads((FIX / 'haiku55.result.json').read_text())
            result['session_id'] = None
            usage = result['usage']
            usage.update(input_tokens=self.rec['inputTokens'], output_tokens=self.rec['outputTokens'],
                         cache_read_input_tokens=self.rec['cacheReadTokens'],
                         cache_creation_input_tokens=self.rec['cacheWriteTokens'],
                         cache_creation=dict(ephemeral_5m_input_tokens=0,
                                             ephemeral_1h_input_tokens=self.rec['cacheWrite1hTokens']))
            for u in result['modelUsage'].values():
                u.update(inputTokens=self.rec['inputTokens'], outputTokens=self.rec['outputTokens'],
                         cacheReadInputTokens=self.rec['cacheReadTokens'],
                         cacheCreationInputTokens=self.rec['cacheWriteTokens'])
            (root / 'raw.json').write_text(json.dumps(result))
            (root / 'run.progress.jsonl').write_text((FIX / 'haiku55.progress.jsonl').read_text())
            script = root / 'writer.sh'
            script.write_text('''set -euo pipefail
source "$COMMON"
SC_RECORD="$FIXTURE/run.record.json"
SC_LEDGER="$FIXTURE/ledger.jsonl"
SC_LEDGER_DEFAULT="$FIXTURE/default.jsonl"
SC_WORKDIR="$FIXTURE"
SC_MODEL="$MODEL"
SC_TRANSPORT="anthropic"
SC_COST_MODEL="plan-quota"
SC_REGISTRY_CLI="$REGISTRY"
SC_RECORD_PARSER="claude"
SC_PROGRESS="$FIXTURE/run.progress.jsonl"
SC_LABEL="tier-fixture"
sc_write_record "$(< "$FIXTURE/raw.json")" 0
''', newline='\n')
            env = dict(os.environ, COMMON=(ROOT / '.claude/scripts/lib/sidecar_common.sh').as_posix(),
                       FIXTURE=root.as_posix(), MODEL=MODEL,
                       REGISTRY=(ROOT / '.claude/tools/model_registry.py').as_posix(),
                       HARNESS_MODEL_REGISTRY=str(root / 'registry.json'))
            proc = subprocess.run([sidecar_launch.git_bash(), str(script)], env=env,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rec = json.loads((root / 'run.record.json').read_text())
            self.assertEqual(rec['costTierBasis'], 'per-request (stream-partial-output)')
            self.assertAlmostEqual(rec['costUSD'], self.expected, places=6)
            self.assertFalse(mr.is_spend(rec['costModel'], rec['costBasis']))

    def test_metrics_sidecar_record_reprices_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'cell.record.json'
            p.write_text(json.dumps(self.rec))
            p.with_name('cell.progress.jsonl').write_text((FIX / 'haiku55.progress.jsonl').read_text())
            row = metrics._sidecar_record_row(str(p))
            self.assertAlmostEqual(metrics._sidecar_list(row, data=self.data), self.expected)
            self.assertEqual(row['tier_basis'], 'per-request (stream-partial-output)')

    def test_benchmark_record_price_reads_progress(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'cell.record.json'
            p.write_text(json.dumps(self.rec))
            p.with_name('cell.progress.jsonl').write_text((FIX / 'haiku55.progress.jsonl').read_text())
            rec = mr.reprice_record(self.rec, str(p), data=self.data)
            self.assertAlmostEqual(rec['costUSD'], self.expected)
            self.assertEqual(rec['costTierBasis'], 'per-request (stream-partial-output)')
            self.assertEqual(rec['costModel'], 'plan-quota')

    def test_vendor_usage_dicts_and_last_usage_not_last_empty_event(self):
        last = {}
        for event in self.events:
            m = event['message']
            last[m['id']] = dict(m['usage'], id=m['id'])
        self.assertAlmostEqual(mr.price_requests(MODEL, last.values(), data=self.data, multiplier=1), self.expected)
        empty = copy.deepcopy(self.events[-1])
        empty['message'].pop('usage')
        self.assertAlmostEqual(mr.price_requests(MODEL, mr.request_usage(self.events + [empty]),
                                               data=self.data, multiplier=1), self.expected)

    def test_registry_price_cli_marks_untiered_without_breaking_numeric_stdout(self):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            mr._cmd_price([MODEL, '1', '0', '1'])
        self.assertGreater(float(out.getvalue()), 0)
        self.assertIn('untiered', err.getvalue())

    def test_registry_price_cli_prices_the_one_hour_cache_write_share(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            mr._cmd_price([MODEL, '0', '0', '0', '0', '1000000'])
        self.assertAlmostEqual(float(out.getvalue()), mr.price_run(MODEL, 0, 0, 0, cache_write_1h=1000000), places=5)
        self.assertGreater(float(out.getvalue()), 0)

    def test_sidecar_rollup_prices_each_served_model(self):
        other = mr.resolve('sonnet', self.data)
        other['price'] = dict(mr.resolve(MODEL, self.data)['price'])
        other['longContextTier'] = None
        events = copy.deepcopy(self.events)
        second = events[-1]['message']['id']
        for e in events:
            if e['message']['id'] == second:
                e['message']['model'] = other['id']
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'mixed.record.json'
            p.write_text(json.dumps(dict(self.rec, servedModel=[MODEL, other['id']])))
            p.with_name('mixed.progress.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            row = metrics._sidecar_record_row(str(p))
            self.assertAlmostEqual(metrics._sidecar_list(row, data=self.data), 0.1431067)

    def test_detached_stub_keeps_record_multiplier_when_progress_exists(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'cell.record.json'
            p.write_text(json.dumps(dict(self.rec, priceMultiplier=0.5)))
            p.with_name('cell.progress.jsonl').write_text((FIX / 'haiku55.progress.jsonl').read_text())
            stub = p.with_name('cell.out.json')
            stub.write_text('DETACHED pid=1')
            run = run_cost.price(run_cost.load_runs(str(stub))[0], self.data)
            self.assertAlmostEqual(run['usd'], self.expected * 0.5)
            self.assertEqual(run['label'], self.rec['label'])

    def test_plan_weight_uses_request_tier(self):
        import transcript_usage
        calls = transcript_usage.read_transcript(str(FIX / 'haiku55.progress.jsonl'), ledger={})['calls']
        _, bad, families = plan_usage_calc.weigh(calls, self.data)
        self.assertFalse(bad)
        self.assertAlmostEqual(sum(v['weight'] for v in families.values()), self.expected)

if __name__ == '__main__':
    unittest.main()
