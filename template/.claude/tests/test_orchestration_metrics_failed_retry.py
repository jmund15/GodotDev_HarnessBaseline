"""A spawn that failed without an agentId and was retried under the same label is one dispatch.

The session is cut from the captured run wf_3193d0a9-1e4 (workflow record and journal, text fields emptied):
its `workflowProgress` lists an errored first attempt ("agent type 'lane-author' not found", no agentId)
and the retry that ran, both under label m3-S. `--rate`, `--pending`, the report rows and `--manifest-seed`
must each see one row; a label whose only attempt failed stays one failed dispatch.

    python3 .claude/tests/test_orchestration_metrics_failed_retry.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "om_failed_retry")
RUN = "wf_3193d0a9-1e4"
LABEL = "m3-S"
spec = importlib.util.spec_from_file_location("orchestration_metrics", os.path.join(HERE, "..", "tools", "orchestration_metrics.py"))
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)


class FailedRetry(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="om_failed_retry_")
        self.session = os.path.join(self.root, "session")
        shutil.copytree(FIXTURE, self.session)
        om.PENDING_VERDICTS = os.path.join(self.root, "orchestration_verdicts.json")
        om.LEGACY_PENDING_VERDICTS = os.path.join(self.root, "legacy_verdicts.json")
        om.ARCHIVE = os.path.join(self.root, "orchestration_metrics.jsonl")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def drop_retry(self):
        path = os.path.join(self.session, "workflows", RUN + ".json")
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        record["workflowProgress"] = [e for e in record["workflowProgress"] if not e.get("agentId")]
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle)

    def run_quiet(self, fn, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fn(*args, **kwargs)
        return rc, out.getvalue()

    def test_fixture_is_the_captured_failed_then_retried_shape(self):
        with open(os.path.join(self.session, "workflows", RUN + ".json"), encoding="utf-8") as handle:
            agents = [e for e in json.load(handle)["workflowProgress"] if e.get("type") == "workflow_agent"]
        self.assertEqual([(e["label"], e["state"], bool(e.get("agentId"))) for e in agents],
                         [(LABEL, "error", False), (LABEL, "done", True)])

    def test_pending_lists_the_label_once(self):
        remaining = om.pending_detail(self.session, with_sidecar=False)["remaining"]
        self.assertEqual(remaining, [(RUN, LABEL)])

    def test_rate_accepts_the_label(self):
        rc, text = self.run_quiet(om.rate, self.session, LABEL, "clean", effort="high", with_sidecar=False)
        self.assertEqual(rc, 0, text)
        self.assertIn("rated %s:%s" % (RUN, LABEL), text)
        self.assertNotIn("REFUSED", text)

    def test_rate_batch_accepts_the_exact_key(self):
        rc, text = self.run_quiet(om.rate_batch, self.session,
                                  [dict(label="%s:%s" % (RUN, LABEL), outcome="clean", effort="high")], False)
        self.assertEqual(rc, 0, text)

    def test_report_rows_hold_one_row_for_the_label(self):
        rows = [r for r in om.collect(self.session) if r["label"] == LABEL]
        self.assertEqual([r["agent_id"] for r in rows], ["ac11a8499168f476d"])

    def test_manifest_seed_sees_one_row(self):
        seed = os.path.join(self.root, "seed.json")
        with open(seed, "w", encoding="utf-8") as handle:
            json.dump({"schemaVersion": 1, "jobs": [{"label": LABEL}]}, handle)
        manifest = om.write_manifest(seed, os.path.join(self.root, "manifest.json"), session=self.session)
        self.assertEqual([j.get("label") for j in manifest["jobs"]], [LABEL])
        self.assertEqual(manifest["jobs"][0].get("status"), "completed", manifest["jobs"][0])

    def test_a_label_whose_only_attempt_failed_stays_one_failed_dispatch(self):
        self.drop_retry()
        rows = [r for r in om.collect(self.session) if r["label"] == LABEL]
        self.assertEqual([r["state"] for r in rows], ["error"])
        self.assertEqual(om.pending_detail(self.session, with_sidecar=False)["remaining"], [(RUN, LABEL)])


if __name__ == "__main__":
    unittest.main()
