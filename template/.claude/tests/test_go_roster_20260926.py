"""The 2026-09-26 Go catalog has an explicit roster disposition for every id."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / ".claude/reference/external_models.json"

CATALOG = """deepseek-flash deepseek-v4-flash deepseek-v4-flash-vision-exp deepseek-v4-pro
 deepseek-v4.1-flash glm-5.1 glm-5.2 glm-5.3 glm-5.3-flash gpt-5.6-luna gpt-6-luna
 grok-4.6 grok-4.7 hy3 hy4-preview kimi-k2.6 kimi-k2.7-code kimi-k3 longcat-2.0
 longcat-2.5-preview-free mimo-v2.5 mimo-v2.5-pro mimo-v2.6-flash mimo-v2.6-pro
 minimax-m2.5 minimax-m2.7 minimax-m3 muse-spark-1.2-contributor
 muse-spark-1.3-contributor omen-alpha qwen3.6-plus qwen3.7-max qwen3.7-plus
 qwen3.8-flash qwen3.8-max space-bunny-free""".split()
# Ids Go's live catalog dropped after the snapshot: the row stays and reads unavailable.
LEFT_CATALOG = {"space-bunny-free"}


class GoRosterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        cls.go = {}
        for row in cls.data["models"]:
            if row["transport"] == "go":
                for name in [row.get("upstreamId") or row["id"], *(row.get("servedIds") or [])]:
                    cls.go[name] = row

    def test_each_catalog_id_has_one_disposition(self):
        self.assertEqual(len(CATALOG), len(set(CATALOG)))
        self.assertEqual(len(CATALOG), 36)
        self.assertTrue(set(CATALOG) <= set(self.go), sorted(set(CATALOG) - set(self.go)))
        self.assertEqual(self.go["muse-spark-1.3-contributor"]["status"]["state"], "unavailable")
        self.assertTrue(self.go["muse-spark-1.3-contributor"]["status"]["reason"])
        for model in set(CATALOG) - {"muse-spark-1.3-contributor"}:
            with self.subTest(model=model):
                self.assertIn(self.go[model].get("apiMode"), ("messages", "chat", "responses"))
                state = (self.go[model].get("status") or {}).get("state")
                if model in LEFT_CATALOG:
                    self.assertEqual(state, "unavailable")
                else:
                    self.assertNotEqual(state, "unavailable")

    def test_go_serves_the_v4_1_flash_request_as_one_deepseek_flash_row(self):
        self.assertIs(self.go["deepseek-v4.1-flash"], self.go["deepseek-flash"])
        self.assertEqual(self.go["deepseek-flash"]["id"], "go/deepseek-flash")
        self.assertEqual(self.go["deepseek-flash"]["upstreamId"], "deepseek-flash")
        self.assertIn("deepseek-v4.1-flash", self.go["deepseek-flash"]["servedIds"])

    def test_parallel_product_lines_do_not_borrow_superseded_lifecycle(self):
        for left, right in (("qwen3.8-max", "qwen3.8-flash"),
                            ("glm-5.3", "glm-5.3-flash"),
                            ("mimo-v2.6-flash", "mimo-v2.6-pro"),
                            ("deepseek-v4.1-flash", "deepseek-v4-flash-vision-exp"),
                            ("kimi-k3", "kimi-k2.7-code")):
            with self.subTest(pair=(left, right)):
                self.assertNotEqual(self.go[left]["family"], self.go[right]["family"])
                self.assertEqual(self.go[left]["lifecycle"], "current")
                self.assertEqual(self.go[right]["lifecycle"], "current")

    def test_go_limits_are_provider_sourced_and_legacy_roster_stays(self):
        self.assertIn("opencode-go", self.data["transports"]["go"].get("_limitsSource", ""))
        self.assertIn("opencode", self.data["transports"])
        self.assertTrue(any(row["transport"] == "opencode" for row in self.data["models"]))
        self.assertEqual(self.data["transports"]["opencode"]["status"]["state"], "unavailable")
        for model in CATALOG:
            row = self.go[model]
            self.assertGreater(row["limits"]["contextTokens"], 0)
            self.assertGreater(row["limits"]["maxOutputTokens"], 0)
        self.assertIn("deepseek-v4.1-flash", self.go["deepseek-flash"]["_limitsComment"])


if __name__ == "__main__":
    unittest.main()
