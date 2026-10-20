#!/usr/bin/env python3
"""Re-runnable proof: `orchestration_metrics.py --rollup` prices a whole session from computed rows.

Each case plants a throwaway ~/.claude/projects tree (HOME redirected, a planted one-model registry,
CLAUDE_PROJECT_DIR pointing at the temp dir so no real archive or verdict file is read) and runs the
real CLI with `--rollup --json`. The rollup is read-only: it never writes the archive or the pending
verdict store.

Planted model `model-a` lists at fresh $4, cache read $0.20, output $20, 5-minute cache write $5 and
1-hour cache write $8 per million tokens.

    python3 .claude/tests/test_orchestration_metrics_rollup.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")
LIVE_REGISTRY = os.path.join(HERE, "..", "reference", "external_models.json")
SID = "0a1b2c3d-4e5f-4a6b-8c7d-rollup-proof-01"
T0 = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def iso(offset):
    return (T0 + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh)


def write_jsonl(path, rows, raw_tail=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
        fh.write(raw_tail)


def planted_registry(path):
    with open(LIVE_REGISTRY, encoding="utf-8") as fh:
        data = json.load(fh)
    row = copy.deepcopy(next(m for m in data["models"] if m.get("transport") == "anthropic"
                                    and m.get("lifecycle") == "current" and m.get("price")))
    row.update(id="model-a", version="model-a", alias="plantedmodel", label="Model A")
    row["price"] = dict(cacheMissPer1M=4, outputPer1M=20, cacheHitPer1M=0.2, cacheWrite5mPer1M=5,
                        cacheWrite1hPer1M=8, currency="USD", asOf="2026-09-24", source="planted")
    data.update(models=[row], aliasHistory={})
    write_json(path, data)


def usage(inp=0, cr=0, cw=0, out=0, cw1h=0):
    return {"input_tokens": inp, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw, "output_tokens": out,
            "cache_creation": {"ephemeral_5m_input_tokens": cw - cw1h, "ephemeral_1h_input_tokens": cw1h}}


def call(offset, mid, u, blocks=None, model="model-a", session=SID):
    return {"type": "assistant", "timestamp": iso(offset), "sessionId": session,
            "message": {"id": mid, "role": "assistant", "model": model, "usage": u,
                        "content": blocks if blocks is not None else [{"type": "text", "text": "ok"}]}}


def owner(offset, text="go on"):
    return {"type": "user", "timestamp": iso(offset), "message": {"role": "user", "content": text}}


def tool_result(offset, use_id):
    return {"type": "user", "timestamp": iso(offset), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": use_id, "content": "done"}]}}


def notification(offset, use_id):
    return owner(offset, "<task-notification><tool-use-id>%s</tool-use-id></task-notification>" % use_id)


def main_transcript(extra=()):
    rows = [owner(0),
            call(2, "m1", usage(inp=1000, cw=2000, out=500),
                 [{"type": "tool_use", "id": "u1", "name": "Bash",
                   "input": {"command": "sleep 5", "run_in_background": True}}]),
            tool_result(3, "u1"),
            call(4, "m2", usage(inp=10, cr=3000, out=100)),
            notification(34, "u1"),
            owner(40),
            call(45, "m3", usage(inp=10, cr=3000, out=100))]
    return rows + list(extra)


def agent_rows(agent, start, end, u, model="model-a"):
    return [call(start, "ma-" + agent, u, model=model, session=SID) | {"agentId": agent},
            call(end, "mb-" + agent, usage(), model=model, session=SID) | {"agentId": agent}]


class Plant:
    def __init__(self, root):
        self.root = root
        self.project = os.path.join(root, ".claude", "projects", "C--proof")
        self.session = os.path.join(self.project, SID)
        self.registry = os.path.join(root, "registry.json")
        planted_registry(self.registry)
        write_jsonl(self.session + ".jsonl", main_transcript())

    def run(self, name="wf_r1", workflow="planted-run", agents=(), progress=None, journal=None):
        """One Workflow run. `agents` is [(agentId, start, end, usage)]; `progress` lists the agentIds the
        host record names (default all, in order); `journal` is a list of rows or None for no journal."""
        rdir = os.path.join(self.session, "subagents", "workflows", name)
        labels = {}
        for aid, start, end, u in agents:
            write_jsonl(os.path.join(rdir, "agent-%s.jsonl" % aid), agent_rows(aid, start, end, u))
        for row in journal or []:
            if row.get("type") == "started":
                labels[row["agentId"]] = row["label"]
        listed = [a[0] for a in agents] if progress is None else progress
        record = {"runId": name, "workflowName": workflow, "status": "completed",
                  "timestamp": iso(0), "startTime": int((T0.timestamp()) * 1000),
                  "logs": ["PINS " + json.dumps({"lane": "high"})],
                  "workflowProgress": [{"type": "workflow_agent", "label": labels.get(a, "lane"), "agentId": a,
                                        "model": "model-a", "state": "done", "phaseTitle": "Work"}
                                       for a in listed]}
        write_json(os.path.join(self.session, "workflows", name + ".json"), record)
        if journal is not None:
            write_jsonl(os.path.join(rdir, "journal.jsonl"), journal)
        return rdir

    def verdicts(self, mapping):
        write_json(os.path.join(self.root, ".claude", "orchestration_verdicts.json"), mapping)

    def archive(self, rows):
        write_jsonl(os.path.join(self.root, ".claude", "orchestration_metrics.jsonl"), rows)

    def sidecar(self, label="side", tokens=1000000, cost=99.0, model="model-a"):
        """A direct sidecar launch in the main transcript plus its -R record."""
        record = os.path.join(self.root, "records", label + ".record.json")
        write_json(record, {"timestamp": iso(60), "parentSessionId": SID, "label": label, "transport": "opencode",
                            "costModel": "marginal-usd", "servedModel": model, "effort": "low",
                            "inputTokens": tokens, "outputTokens": 0, "cacheReadTokens": 0,
                            "cacheWriteTokens": 0, "numTurns": 1, "durationMs": 90000, "costUSD": cost,
                            "exitCode": 0})
        launch = call(46, "m4", usage(),
                      [{"type": "tool_use", "id": "u2", "name": "Bash",
                        "input": {"command": "bash .claude/scripts/opencode_sidecar.sh -m x -l %s -R %s"
                                  % (label, record.replace(os.sep, "/"))}}]) | {"cwd": self.root}
        write_jsonl(self.session + ".jsonl", main_transcript([launch]))

    def env(self):
        env = dict(os.environ, HOME=self.root, USERPROFILE=self.root, CLAUDE_PROJECT_DIR=self.root,
                   HARNESS_MODEL_REGISTRY=self.registry, PYTHONIOENCODING="utf-8",
                   HARNESS_TEST_STAMP=os.path.join(self.root, ".claude", "logs", "harness_tests_stamp.json"))
        for key in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR", "SIDECAR_LEDGER_PATH"):
            env.pop(key, None)
        return env

    def cli(self, *args, sid=SID):
        r = subprocess.run([sys.executable, TOOL, "--session", sid, *args], capture_output=True, text=True,
                           env=self.env(), cwd=self.root, encoding="utf-8", errors="replace", timeout=120)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def rollup(self, sid=SID, extra=()):
        out = os.path.join(self.root, "rollup.json")
        env = self.env()
        r = subprocess.run([sys.executable, TOOL, "--session", sid, "--rollup", "--json", out, *extra],
                           capture_output=True, text=True, env=env, cwd=self.root, encoding="utf-8",
                           errors="replace", timeout=120)
        doc = None
        if os.path.exists(out):
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
        return r.returncode, (r.stdout or "") + (r.stderr or ""), doc


def rows_by_id(doc):
    return {row["id"]: row for row in doc["rows"]}


def key(n):
    return "v2:" + ("%064x" % n)


def started(aid, label, n):
    return {"type": "started", "key": key(n), "agentId": aid, "label": label, "phase": "Work"}


def finished(aid, n, kind="result"):
    return {"type": kind, "key": key(n), "agentId": aid, "result": {}}


class RollupTracerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plant = Plant(self.tmp.name)

    def test_planted_rerun_is_waste(self):
        self.plant.run(
            agents=[("a1", 100, 160, usage(inp=500000)), ("a2", 200, 230, usage(inp=250000))],
            progress=["a2"],
            journal=[{"type": "launched"}, started("a1", "lane", 1), finished("a1", 1),
                     started("a2", "lane", 2), finished("a2", 2)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        rows = rows_by_id(doc)
        dispatch = [r for r in doc["rows"] if r["id"].startswith("D")]
        self.assertEqual(len(dispatch), 2)
        by_agent = {r["tokens"]["fresh"]: r for r in dispatch}
        first, second = by_agent[500000], by_agent[250000]
        self.assertAlmostEqual(first["list"], 2.0, places=6)
        self.assertAlmostEqual(second["list"], 1.0, places=6)
        self.assertEqual(first["attempt"], "superseded")
        self.assertEqual(second["attempt"], "current")
        self.assertEqual(second["waste"], "rerun")
        self.assertFalse(first["waste"])
        self.assertAlmostEqual(rows["X"]["list"], 1.0, places=6)
        self.assertAlmostEqual(rows["T"]["list"]["workflow"], 3.0, places=6)
        wall = rows["O"]["wall"]
        self.assertEqual(sum(wall["buckets"].values()), wall["span"])
        self.assertEqual(wall["span"], 45.0)
        self.assertEqual(wall["buckets"]["idle:shell_other_bg"], 30.0)
        self.assertEqual(wall["buckets"]["owner"], 6.0)

    def test_waste_minutes_are_the_union_of_parallel_agent_spans(self):
        self.plant.run(
            agents=[("a1", 10, 20, usage(inp=1)), ("a2", 10, 20, usage(inp=1)),
                    ("a3", 100, 160, usage(inp=1)), ("a4", 130, 190, usage(inp=1))],
            progress=["a3", "a4"],
            journal=[{"type": "launched"}, started("a1", "s1", 1), started("a2", "s2", 2), finished("a1", 1),
                     finished("a2", 2), started("a3", "s1", 3), started("a4", "s2", 4), finished("a3", 3),
                     finished("a4", 4)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        x = rows_by_id(doc)["X"]
        self.assertEqual(len(x["rows"]), 2)
        self.assertAlmostEqual(x["minutes"], 1.5, places=6)

    def test_orchestrator_row_prices_the_main_transcript(self):
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        o = rows_by_id(doc)["O"]
        self.assertEqual((o["calls"], o["fresh"], o["cache_read"], o["cache_write_5m"], o["cache_write_1h"],
                          o["output"]), (3, 1020, 6000, 2000, 0, 700))
        self.assertAlmostEqual(o["list"], 0.02928, places=6)
        self.assertEqual(o["unpriced_calls"], 0)
        self.assertEqual(doc["session"], SID)
        self.assertIn("list", doc["unit"])
        self.assertIn("never spend", doc["unit"])

    def test_rollup_writes_neither_archive_nor_pending_verdicts(self):
        self.plant.run(agents=[("a1", 100, 160, usage(inp=1))], journal=[{"type": "launched"},
                                                                         started("a1", "lane", 1)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        for name in ("orchestration_metrics.jsonl", "orchestration_verdicts.json"):
            self.assertFalse(os.path.exists(os.path.join(self.tmp.name, ".claude", name)), name)


def dispatch_rows(doc):
    return [r for r in doc["rows"] if r["id"].startswith("D")]


def by_label(doc, label):
    return [r for r in dispatch_rows(doc) if r["label"] == label]


class RollupClassTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plant = Plant(self.tmp.name)

    def three_lanes(self):
        agents = [("a%d" % i, 100 + i, 130 + i, usage(inp=250000)) for i in (1, 2, 3)]
        journal = ([{"type": "launched"}] + [started("a%d" % i, "l%d" % i, i) for i in (1, 2, 3)]
                   + [finished("a%d" % i, i) for i in (1, 2, 3)])
        self.plant.run(agents=agents, journal=journal)

    def test_outcome_comes_from_pending_then_archive_else_unrated(self):
        self.three_lanes()
        self.plant.verdicts({"wf_r1:l1": "discarded"})
        self.plant.archive([{"run": "wf_r1", "label": "l2", "outcome": "rework"},
                            {"run": "wf_r1", "label": "l1", "outcome": "clean"}])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        self.assertEqual(by_label(doc, "l1")[0]["outcome"], "discarded")
        self.assertEqual(by_label(doc, "l2")[0]["outcome"], "rework")
        self.assertEqual(by_label(doc, "l3")[0]["outcome"], "unrated")
        self.assertEqual(by_label(doc, "l1")[0]["waste"], "discarded")
        self.assertEqual(by_label(doc, "l2")[0]["waste"], "rework")
        self.assertFalse(by_label(doc, "l3")[0]["waste"])
        self.assertAlmostEqual(rows_by_id(doc)["X"]["list"], 2.0, places=6)

    def test_resume_is_not_waste_and_retry_is_its_own_class(self):
        self.plant.run(
            agents=[("a1", 100, 130, usage(inp=250000)), ("a2", 140, 170, usage(inp=250000)),
                    ("a3", 100, 130, usage(inp=250000)), ("a4", 140, 170, usage(inp=250000))],
            progress=["a2", "a4"],
            journal=[{"type": "launched"}, started("a1", "resumed", 1), started("a2", "resumed", 2),
                     finished("a2", 2), started("a3", "retried", 3), finished("a3", 3),
                     {"type": "started", "key": key(3), "agentId": "a4", "label": "retried", "phase": "Work"},
                     finished("a4", 3)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        resumed = [r for r in by_label(doc, "resumed") if r["attempt"] == "current"][0]
        retried = [r for r in by_label(doc, "retried") if r["attempt"] == "current"][0]
        self.assertEqual((resumed["rerun"], resumed["waste"]), ("resume", None))
        self.assertEqual((retried["rerun"], retried["waste"]), ("retry", None))
        self.assertEqual(rows_by_id(doc)["X"]["list"], 0.0)

    def test_one_journal_reads_a_retried_job_as_two_attempts_and_one_finished_job(self):
        rdir = self.plant.run(
            agents=[("a1", 100, 130, usage(inp=1)), ("a2", 140, 170, usage(inp=1))],
            journal=[{"type": "launched"}, started("a1", "retried", 3), finished("a1", 3, "failed"),
                     {"type": "started", "key": key(3), "agentId": "a2", "label": "retried", "phase": "Work"},
                     finished("a2", 3)])
        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
        script = ("import json, sys; sys.path.insert(0, sys.argv[1]); import orchestration_metrics as om, "
                  "watch_records as wr; "
                  "print(json.dumps([len(om.journal_attempts(sys.argv[2])['starts']), "
                  "wr.workflow_progress({'run_dir': sys.argv[2], 'jobs': [{'label': 'retried'}]})[0]]))")
        done = subprocess.run([sys.executable, "-c", script, tools, rdir], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [2, 1])

    def test_transcript_the_journal_never_started_is_an_orphan(self):
        self.plant.run(agents=[("a1", 100, 130, usage(inp=250000)), ("zz", 100, 130, usage(inp=250000))],
                       progress=["a1"], journal=[{"type": "launched"}, started("a1", "lane", 1), finished("a1", 1)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        orphan = [r for r in dispatch_rows(doc) if r["attempt"] == "orphan"]
        self.assertEqual(len(orphan), 1)
        self.assertEqual((orphan[0]["label"], orphan[0]["outcome"]), ("?", "n/a"))
        self.assertAlmostEqual(orphan[0]["list"], 1.0, places=6)

    def test_journal_absent_and_unreadable_keep_the_host_rows_and_say_so(self):
        self.plant.run(name="wf_none", agents=[("n1", 100, 130, usage(inp=250000))])
        self.plant.run(name="wf_bad", agents=[("b1", 100, 130, usage(inp=250000)),
                                              ("b2", 100, 130, usage(inp=250000))], progress=["b2"],
                       journal=[{"type": "launched"}, started("b1", "lane", 1), finished("b1", 1)])
        with open(os.path.join(self.plant.session, "subagents", "workflows", "wf_bad", "journal.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write("{not json\n")
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        none_rows = [r for r in dispatch_rows(doc) if r["run"] == "wf_none"]
        bad_rows = [r for r in dispatch_rows(doc) if r["run"] == "wf_bad"]
        self.assertEqual([r["journal"] for r in none_rows], ["absent"])
        self.assertEqual([(r["attempt"], r["journal"]) for r in bad_rows], [("current", "unreadable at line 4")])

    def test_sidecar_row_is_priced_from_tokens_never_cost_usd(self):
        self.plant.sidecar(tokens=1000000, cost=99.0)
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        rows = [r for r in dispatch_rows(doc) if r["engine"] == "sidecar"]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["list"], 4.0, places=6)
        self.assertEqual(rows[0]["kind"], "sidecar")
        self.assertAlmostEqual(rows_by_id(doc)["T"]["list"]["sidecar"], 4.0, places=6)

    def test_instrument_runs_count_in_totals_and_never_in_waste(self):
        self.plant.run(workflow="score-cell", agents=[("a1", 100, 130, usage(inp=250000)),
                                                      ("a2", 140, 170, usage(inp=250000))], progress=["a2"],
                       journal=[{"type": "launched"}, started("a1", "cell", 1), finished("a1", 1),
                                started("a2", "cell", 2), finished("a2", 2)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        rows = dispatch_rows(doc)
        self.assertEqual({r["kind"] for r in rows}, {"instrument"})
        self.assertEqual({r["outcome"] for r in rows}, {"n/a"})
        self.assertFalse(any(r["waste"] for r in rows))
        self.assertAlmostEqual(rows_by_id(doc)["T"]["list"]["instrument"], 2.0, places=6)
        self.assertEqual(rows_by_id(doc)["X"]["list"], 0.0)

    def test_review_seat_yield_states_pass_through_and_duplicate_share_scales_the_list(self):
        self.plant.run(workflow="review-fanout", agents=[("a1", 100, 130, usage(inp=250000))],
                       journal=[{"type": "launched"}, started("a1", "lane", 1)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        got = dispatch_rows(doc)[0]["yield"]
        self.assertIn(got["state"], ("unmeasured", "unreadable"))
        self.assertTrue(got["reason"])
        self.assertFalse(dispatch_rows(doc)[0]["waste"])
        overlaps = {"wf_r1": {"seats": {"x": {"label": "lane", "found": 4, "unique": 3, "duplicate": 1,
                                              "unique_critical": 0}}, "distinct": 4, "shared": 1,
                              "uncovered": []}}
        script = ("import json, sys; sys.path.insert(0, sys.argv[1]); import orchestration_metrics as om; "
                  "print(json.dumps(om.build_rollup(sys.argv[2], overlaps=json.loads(sys.argv[3]))))")
        r = subprocess.run([sys.executable, "-c", script, os.path.join(HERE, "..", "tools"), self.plant.session,
                            json.dumps(overlaps)], capture_output=True, text=True, env=self.plant.env(),
                           cwd=self.tmp.name, encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        built = json.loads(r.stdout.strip().splitlines()[-1])
        row = dispatch_rows(built)[0]
        self.assertEqual(row["yield"], {"found": 4, "unique": 3, "duplicate": 1})
        self.assertEqual(row["waste"], "duplicate-share")
        self.assertAlmostEqual(row["waste_list"], 0.25, places=6)
        # A seat with partly duplicate findings still ran for its unique ones, in parallel with its
        # siblings: its duplicates cost list, never session wall time.
        self.assertEqual(row["waste_min"], 0.0)
        self.assertEqual([r for r in built["rows"] if r["id"] == "X"][0]["minutes"], 0.0)

    def test_default_report_and_run_report_name_superseded_attempts(self):
        self.plant.run(agents=[("a1", 100, 160, usage(inp=500000)), ("a2", 200, 230, usage(inp=250000))],
                       progress=["a2"],
                       journal=[{"type": "launched"}, started("a1", "lane", 1), finished("a1", 1),
                                started("a2", "lane", 2), finished("a2", 2)])
        for args in ((), ("--run", "wf_r1")):
            code, out = self.plant.cli(*args)
            self.assertEqual(code, 0, out)
            line = next((l for l in out.splitlines() if "superseded" in l.lower()), "")
            self.assertIn("wf_r1", line, out)
            self.assertIn("has 1 superseded", line)
            self.assertIn("2.00", line)
            self.assertIn("--rollup", line)


def column_transcript():
    compact = owner(5, "summary")
    compact["isCompactSummary"] = True
    git_status = [{"type": "tool_use", "id": "g1", "name": "Bash", "input": {"command": "git status"}}]
    git_commit = [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "git commit -m x"}}]
    big = tool_result(3, "g1")
    big["message"]["content"][0]["content"] = "x" * 20001
    return [owner(0), call(2, "m1", usage(inp=10, cw=2000, out=10), git_status), big,
            call(4, "m2", usage(cr=450000, out=10)), compact,
            call(3705, "m3", usage(cw=60000)), call(3706, "m4", usage(cw=40000)),
            call(3707, "m5", usage(), git_commit), tool_result(3747, "c1")]


class RollupColumnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plant = Plant(self.tmp.name)

    def runs_file(self, payload):
        write_json(os.path.join(self.tmp.name, ".claude", "logs", "harness_runs.json"), payload)

    def test_orchestrator_waste_columns(self):
        write_jsonl(self.plant.session + ".jsonl", column_transcript())
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        o = rows_by_id(doc)["O"]
        self.assertEqual(o["calls_over_400k"], 1)
        self.assertEqual(o["wall"]["compactions"], 1)
        self.assertEqual(o["idle_rewrites"]["n"], 1)
        self.assertEqual(o["idle_rewrites"]["tokens"], 60000)
        self.assertAlmostEqual(o["idle_rewrites"]["list"], 0.3, places=6)
        self.assertEqual((o["bookkeeping"]["messages"], o["bookkeeping"]["of"]), (2, 2))
        self.assertAlmostEqual(o["bookkeeping"]["list"], 0.01024, places=6)
        self.assertEqual(o["big_results"], {"n": 1, "chars": 20001})
        self.assertEqual(o["wall"]["buckets"]["away"], 1900.0)

    def test_proofs_row_counts_only_this_sessions_runs(self):
        write_jsonl(self.plant.session + ".jsonl", column_transcript())
        self.runs_file({"runs": [
            {"ts": "t1", "session": SID, "mode": "close", "wall_s": 10.0, "selected": 4, "reused": 3, "queue_s": 1.0},
            {"ts": "t2", "session": SID, "mode": "for", "wall_s": 20.0, "selected": 5, "reused": 1, "queue_s": 2.0},
            {"ts": "t3", "session": "another-session", "mode": "close", "wall_s": 99.0, "selected": 9,
             "reused": 0, "queue_s": 9.0}]})
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        p = rows_by_id(doc)["P"]
        self.assertEqual(p["state"], "ok")
        self.assertEqual((p["runs"], p["wall_s"], p["queue_s"], p["reused"], p["ran"]), (2, 30.0, 3.0, 4, 5))
        self.assertEqual(p["commits"], {"n": 1, "secs": 40.0, "over_budget": 1, "budget_s": 30})
        self.assertEqual(p["fg_test_s"], 0.0)

    def test_commit_budget_is_the_runners_constant(self):
        write_jsonl(self.plant.session + ".jsonl", column_transcript())
        self.runs_file({"runs": []})
        shim = os.path.join(self.tmp.name, "sitecustomize.py")
        with open(shim, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("import sys\nsys.path.insert(0, %r)\nimport harness_tests\nharness_tests.COMMIT_BUDGET_S = 45\n"
                     % os.path.abspath(os.path.join(HERE, "..", "scripts")))
        saved = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = self.tmp.name
        try:
            code, out, doc = self.plant.rollup()
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH")
            else:
                os.environ["PYTHONPATH"] = saved
        self.assertEqual(code, 0, out)
        self.assertEqual(rows_by_id(doc)["P"]["commits"],
                         {"n": 1, "secs": 40.0, "over_budget": 0, "budget_s": 45})
        self.assertIn("commits 1 (0 over 45s)", out)

    def test_proofs_row_states_are_distinct(self):
        code, out, doc = self.plant.rollup()
        self.assertEqual(rows_by_id(doc)["P"]["state"], "record-absent")
        write_jsonl(os.path.join(self.tmp.name, ".claude", "logs", "harness_runs.json"), [], raw_tail="{not json")
        code, out, doc = self.plant.rollup()
        self.assertEqual(rows_by_id(doc)["P"]["state"], "unreadable")
        self.runs_file({"runs": [{"ts": "t", "session": "another-session", "wall_s": 1.0}]})
        code, out, doc = self.plant.rollup()
        self.assertEqual((rows_by_id(doc)["P"]["state"], rows_by_id(doc)["P"]["runs"]), ("ok", 0))

    def test_proofs_row_flags_a_record_window_that_starts_after_the_session(self):
        write_jsonl(self.plant.session + ".jsonl", column_transcript())
        t0 = T0.timestamp()
        self.runs_file({"runs": [{"ts": t0 + 100, "session": SID, "wall_s": 1.0}]})
        code, out, doc = self.plant.rollup()
        self.assertEqual(rows_by_id(doc)["P"]["state"], "window-after-start", out)
        self.runs_file({"runs": [{"ts": t0 - 100, "session": "another-session", "wall_s": 1.0},
                                 {"ts": t0 + 100, "session": SID, "wall_s": 1.0}]})
        code, out, doc = self.plant.rollup()
        self.assertEqual((rows_by_id(doc)["P"]["state"], rows_by_id(doc)["P"]["runs"]), ("ok", 1), out)

    def test_dispatch_cache_writes_are_never_labeled_5_minute(self):
        self.plant.run(agents=[("a1", 100, 160, usage(cw=3000))],
                       journal=[{"type": "launched"}, started("a1", "lane", 1)])
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        tokens = rows_by_id(doc)["T"]["tokens"]
        self.assertEqual((tokens["cache_write_5m"], tokens["cache_write_1h"], tokens["cache_write_unsplit"]),
                         (2000, 0, 3000))

    def test_dispatch_free_session_keeps_the_unsplit_cache_write_column_at_zero(self):
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        self.assertEqual(rows_by_id(doc)["T"]["tokens"]["cache_write_unsplit"], 0)

    def test_total_row_counts_priced_rows_and_keeps_rows_for_id_lists(self):
        self.plant.run(agents=[("a1", 100, 160, usage(inp=1))],
                       journal=[{"type": "launched"}, started("a1", "lane", 1)])
        code, out, doc = self.plant.rollup()
        total = rows_by_id(doc)["T"]
        self.assertEqual(total["priced_rows"], 2, out)
        self.assertNotIn("rows", total)

    def test_background_proof_wait_counts_as_proofs_under_the_consumer_key(self):
        rows = [
            owner(0),
            call(2, "m1", usage(inp=10), [{"type": "tool_use", "id": "u1", "name": "Bash",
                                           "input": {"command": "python3 .claude/scripts/harness_tests.py --close",
                                                     "run_in_background": True}}]),
            tool_result(3, "u1"), call(4, "m2", usage(inp=10)), notification(34, "u1")]
        write_jsonl(self.plant.session + ".jsonl", rows)
        code, out, doc = self.plant.rollup()
        self.assertEqual(code, 0, out)
        split = rows_by_id(doc)["T"]["wall"]["split"]
        self.assertEqual(split["proofs"], 30.0, split)
        self.assertEqual(split["dispatches"], 0.0, split)
        self.assertNotIn("dispatch_idle", split)


if __name__ == "__main__":
    unittest.main()
