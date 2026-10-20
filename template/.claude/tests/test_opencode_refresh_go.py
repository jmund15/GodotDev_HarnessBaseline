"""opencode_refresh_models.py --go: Go-owned roster refresh on temp registry copies, never the live file."""
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/scripts"))
sys.path.insert(0, str(ROOT / ".claude/tools"))
import opencode_refresh_models as refresh
import model_registry as mr

LIVE = ROOT / ".claude/reference/external_models.json"
CONFLICTS = ["zz-c%02d" % n for n in range(1, 15)]
CATALOG = ["zz-alpha-2", "zz-alpha-3", "zz-alpha-3-flash", "zz-shared", "zz-folded-1.5",
           "zz-nomode", "zz-nolimits", *CONFLICTS]
MODES = {"zz-alpha-2": "messages", "zz-alpha-3": "chat", "zz-alpha-3-flash": "chat",
         "zz-shared": "responses", "zz-folded-1.5": "responses", "zz-nomode": None,
         "zz-nolimits": "messages", **{c: "messages" for c in CONFLICTS}}
OWNED = {mid: (1000 + n, 100 + n) for n, mid in enumerate(CATALOG) if mid not in ("zz-nolimits", "zz-shared")}


def planted_registry():
    """The live registry minus its Go rows, plus a legacy Go row and a non-Go row serving `zz-shared`."""
    data = json.loads(LIVE.read_text(encoding="utf-8"))
    data["models"] = [m for m in data["models"] if m["transport"] != "go"]
    top = max(m["order"] for m in data["models"])
    donor = next(m for m in data["models"] if m["transport"] == "deepseek")
    shared = copy.deepcopy(donor)
    shared.update(id="zz-shared", alias="zz-shared-direct", family="zz-line", version="zz-shared",
                  lifecycle="current", order=top + 1, roles=[], effort={"evidence": "unmeasured"})
    shared.pop("status", None)
    shared["supplementary"] = []
    data["models"].append(shared)
    data["models"].append({
        "id": "zz-legacy", "alias": "go-zz-legacy", "transport": "go", "label": "ZZ legacy (Go)",
        "family": "zz-legacy", "version": "zz-legacy", "lifecycle": "current", "order": top + 2,
        "costRank": 1, "prior": "mid", "roles": [], "effort": {"evidence": "unmeasured"},
        "authTier": "open", "gate": {"minBand": "Surplus"}, "apiMode": "messages",
        "priceGap": {"reason": "planted legacy row", "checked": "https://models.dev/api.json",
                     "asOf": "2026-09-01"},
        "windowGap": {"reason": "planted legacy row", "checked": "https://models.dev/api.json",
                      "asOf": "2026-09-01"}})
    mr._validate(data, "planted")
    return data


class GoRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="go_refresh_"))
        self.registry = self.tmp / "external_models.json"
        self.registry.write_text(json.dumps(planted_registry(), indent=2) + "\n", encoding="utf-8",
                                 newline="\n")
        self.files = {}
        self.write("catalog", {"object": "list", "data": [{"id": m, "object": "model"} for m in CATALOG]})
        self.write("protocol", {"results": {m: {"mode": mode} for m, mode in MODES.items()}})
        self.write("caps", {"monthlyUSD": {**{m: 60 for m in CATALOG}, "zz-alpha-3": None}})
        decoy = {m: {"limit": {"context": 9000, "output": 900}} for m in CONFLICTS}
        self.write("modelsdev", {"decoy": {"models": decoy},
                                 GO_PROVIDER: {"models": {m: {"limit": {"context": c, "output": o}}
                                                          for m, (c, o) in OWNED.items()}}})
        served = self.tmp / "served.jsonl"
        served.write_text(json.dumps({"requested": "zz-folded-1.5", "modelHeaders": {
            "x-opencode-upstream-model-id": "zz-shared"}}) + "\n", encoding="utf-8")
        self.files["served"] = served

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, obj):
        path = self.tmp / (name + ".json")
        path.write_text(json.dumps(obj), encoding="utf-8")
        self.files[name] = path

    def run_tool(self, *extra, offline=True):
        argv = ["--go", "--registry", str(self.registry), "--protocol", str(self.files["protocol"]),
                "--caps", str(self.files["caps"]), "--modelsdev", str(self.files["modelsdev"]),
                "--served", str(self.files["served"])]
        if offline:
            argv += ["--catalog", str(self.files["catalog"])]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = refresh.main(argv + list(extra))
        return rc, out.getvalue(), err.getvalue()

    def rows(self):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        return data, {m.get("upstreamId") or m["id"]: m for m in data["models"] if m["transport"] == "go"}

    def test_valid_catalog_registers_every_id_and_keeps_untouched_bytes(self):
        before_text = self.registry.read_text(encoding="utf-8")
        before = json.loads(before_text)
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 0, err)
        data, go = self.rows()
        mr._validate(data, "after")
        self.assertEqual(set(go) | set(go["zz-shared"].get("servedIds", [])) | {"zz-legacy"},
                         set(CATALOG) | {"zz-legacy"})
        self.assertEqual(go["zz-shared"]["id"], "go/zz-shared")
        self.assertEqual(go["zz-shared"]["family"], "zz-line")
        self.assertEqual(go["zz-shared"]["servedIds"], ["zz-folded-1.5"])
        self.assertEqual(go["zz-shared"]["limits"]["contextTokens"], OWNED["zz-folded-1.5"][0])
        self.assertEqual((go["zz-alpha-2"]["family"], go["zz-alpha-2"]["lifecycle"]), ("zz-alpha", "superseded"))
        self.assertEqual((go["zz-alpha-3"]["family"], go["zz-alpha-3"]["lifecycle"]), ("zz-alpha", "current"))
        self.assertEqual((go["zz-alpha-3-flash"]["family"], go["zz-alpha-3-flash"]["lifecycle"]),
                         ("zz-alpha-flash", "current"))
        self.assertEqual(go["zz-alpha-3"]["planCapUSD"], {"monthly": None, "unlimited": True})
        self.assertEqual(go["zz-alpha-2"]["planCapUSD"], {"monthly": 60})
        self.assertEqual(go["zz-nomode"]["status"]["state"], "unavailable")
        self.assertNotIn("apiMode", go["zz-nomode"])
        for mid, mode in MODES.items():
            if mode and mid != "zz-folded-1.5":
                self.assertEqual(go[mid]["apiMode"], mode, mid)
        self.assertIn(refresh.GO_PROVIDER, data["transports"]["go"]["_limitsSource"])
        self.assertEqual(data["transports"]["opencode"]["status"]["state"], "unavailable")
        self.assertEqual([m for m in data["models"] if m["transport"] == "opencode"],
                         [m for m in before["models"] if m["transport"] == "opencode"])
        elements, _ = refresh._array_elements(before_text, before_text.index('"models"') + 10)
        for (a, b), row in zip(elements, before["models"]):
            if row["transport"] != "go":
                self.assertIn(before_text[a:b], self.registry.read_text(encoding="utf-8"), row["id"])

    def test_go_rows_take_the_catalog_list_price_and_a_row_the_catalog_lacks_takes_a_price_gap(self):
        costs = {"zz-alpha-2": {"input": 1.0, "output": 2.0, "cache_read": 0.1},
                 "zz-alpha-3": {"input": 3.0, "output": 6.0, "cache_read": 0.3, "cache_write": 3.75}}
        doc = json.loads(self.files["modelsdev"].read_text(encoding="utf-8"))
        for mid, cost in costs.items():
            doc[GO_PROVIDER]["models"][mid]["cost"] = cost
        self.write("modelsdev", doc)
        rc, out, err = self.run_tool("--dry-run")
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertEqual(report["priced"], 2)
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 0, err)
        data, go = self.rows()
        mr._validate(data, "after")
        price = go["zz-alpha-3"]["price"]
        self.assertEqual((price["cacheMissPer1M"], price["cacheHitPer1M"], price["outputPer1M"],
                          price["cacheWritePer1M"]), (3.0, 0.3, 6.0, 3.75))
        self.assertEqual((price["currency"], price["source"].startswith("https://models.dev")), ("USD", True))
        self.assertNotIn("cacheWritePer1M", go["zz-alpha-2"]["price"])
        self.assertNotIn("priceGap", go["zz-alpha-2"])
        for mid in ("zz-nolimits", "zz-alpha-3-flash"):
            self.assertNotIn("price", go[mid], mid)
            self.assertIn("models.dev", go[mid]["priceGap"]["checked"], mid)
            self.assertTrue(go[mid]["priceGap"]["reason"], mid)
        self.assertEqual(report["priceGaps"],
                         sum(1 for mid, m in go.items() if mid != "zz-legacy" and "priceGap" in m))

    def test_fourteen_cross_provider_limit_conflicts_take_the_go_owned_entry(self):
        rc, out, err = self.run_tool("--dry-run")
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertEqual(sorted(c["id"] for c in report["limitConflictsIgnored"]), CONFLICTS)
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 0, err)
        _, go = self.rows()
        for mid in CONFLICTS:
            self.assertEqual((go[mid]["limits"]["contextTokens"], go[mid]["limits"]["maxOutputTokens"]),
                             OWNED[mid], mid)

    def test_an_id_without_go_owned_metadata_keeps_null_limits(self):
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 0, err)
        _, go = self.rows()
        self.assertIsNone(go["zz-nolimits"]["limits"]["contextTokens"])
        self.assertIsNone(go["zz-nolimits"]["limits"]["maxOutputTokens"])
        self.assertIn(refresh.GO_PROVIDER, go["zz-nolimits"]["_limitsComment"])

    def test_a_catalog_missing_a_legacy_id_keeps_it_resolvable_and_marks_it_unavailable(self):
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 0, err)
        data, go = self.rows()
        self.assertEqual(go["zz-legacy"]["status"]["state"], "unavailable")
        self.assertEqual(mr.row_by_id("zz-legacy", data)["transport"], "go")
        for row in [m for m in data["models"] if m["transport"] == "opencode"]:
            self.assertEqual(mr.row_by_id(row["id"], data)["transport"], "opencode")

    def test_a_malformed_catalog_leaves_the_registry_bytes_intact(self):
        before = self.registry.read_bytes()
        self.write("catalog", {"data": "not a list"})
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 2)
        self.assertIn("malformed catalog", err)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_an_auth_failure_leaves_the_registry_bytes_intact(self):
        before = self.registry.read_bytes()
        denied = urllib.error.HTTPError(refresh.GO_BASE + "/models", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "stub-key"}), \
                mock.patch.object(refresh.urllib.request, "urlopen", side_effect=denied):
            rc, _, err = self.run_tool(offline=False)
        self.assertEqual(rc, 2)
        self.assertIn("auth failure", err)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_a_planted_name_collision_aborts_before_any_write(self):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        next(m for m in data["models"] if m["id"] == "zz-shared")["alias"] = "go-zz-alpha-2"
        self.registry.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        before = self.registry.read_bytes()
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_dry_run_prints_the_exact_rows_it_would_write(self):
        before = self.registry.read_bytes()
        rc, out, err = self.run_tool("--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.registry.read_bytes(), before)
        report = json.loads(out)
        rc, _, err = self.run_tool()
        self.assertEqual(rc, 0, err)
        data, _ = self.rows()
        written = {m["id"]: m for m in data["models"]}
        printed = report["added"] + report["updated"] + report["markedAbsent"]
        self.assertTrue(printed)
        for row in printed:
            self.assertEqual(written[row["id"]], row, row["id"])
        self.assertEqual(data["transports"]["opencode"]["status"], report["transports"]["opencode"]["status"])

    def test_without_go_refuses_and_names_the_free_cli_sync(self):
        err = io.StringIO()
        with mock.patch.object(refresh, "http_json", side_effect=AssertionError("network call")), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            refresh.main(["--dry-run"])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("/model_ingest", err.getvalue())
        # A shared script names no project-local tool: a consuming project would not have it (test_layer_closure.py).
        self.assertNotIn("free_pool", err.getvalue())


GO_PROVIDER = refresh.GO_PROVIDER

if __name__ == "__main__":
    unittest.main()
