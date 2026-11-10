"""Proof for scripts/freesound_search.py: the search API's responses are planted, so no network runs.
Covers argument handling, the CC0 filter and query the request carries, a normal result, an empty result,
the duration gate and its bounds, de-duplication across queries, an API error, a missing key, and the
candidate file each run writes. The tool is loaded from a copied `.claude/scripts` layout under a temp
root, so its writes never reach the repository."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "freesound_search.py"
CLI_USAGE = ROOT / ".claude" / "tools" / "cli_usage.py"

FIELDS = "id,name,license,username,duration,channels,samplerate,type,tags,url,previews"


def sound(sid, name, duration, **overrides):
    row = {
        "id": sid,
        "name": name,
        "license": "Creative Commons 0",
        "username": "uploader_a",
        "duration": duration,
        "channels": 2,
        "samplerate": 44100,
        "type": "wav",
        "tags": ["impact", "punch", "foley"],
        "url": f"https://freesound.example/s/{sid}/",
        "previews": {"preview-hq-ogg": f"https://cdn.freesound.example/previews/{sid}_hq.ogg"},
    }
    row.update(overrides)
    return row


def payload(*rows, count=None):
    return {"count": len(rows) if count is None else count,
            "next": None, "previous": None, "results": list(rows)}


def response(payload):
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def http_error(status, body):
    return urllib.error.HTTPError("https://freesound.example/apiv2/search/text/", status,
                                  "planted", {}, io.BytesIO(body))


def plant_tool(root):
    """Reproduce the layout the tool resolves its imports and output against, under `root`."""
    scripts = root / ".claude" / "scripts"
    tools = root / ".claude" / "tools"
    scripts.mkdir(parents=True)
    tools.mkdir(parents=True)
    shutil.copyfile(SCRIPT, scripts / SCRIPT.name)
    shutil.copyfile(CLI_USAGE, tools / "cli_usage.py")
    return scripts / SCRIPT.name


def load_tool(path):
    spec = importlib.util.spec_from_file_location("freesound_search_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SearchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="freesound_search_proof_")
        self.root = Path(self.tmp.name)
        self.module = load_tool(plant_tool(self.root))
        self.home = self.root / "home"
        (self.home / ".config" / "freesound").mkdir(parents=True)
        self.planted_key = "test-key"
        self.candidates = self.root / ".claude" / "scratch" / "freesound_candidates.json"

    def tearDown(self):
        self.tmp.cleanup()

    def plant_key(self):
        (self.home / ".config" / "freesound" / "api_key").write_text(
            self.planted_key + "\n", encoding="utf-8")

    def run_tool(self, queries, answers=None):
        answers = answers or {}

        def respond(req, **kwargs):
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
            answer = answers[parsed["query"][0]]
            if isinstance(answer, Exception):
                raise answer
            return response(answer)

        opener = mock.Mock(side_effect=respond)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"USERPROFILE": str(self.home), "HOME": str(self.home)}), \
                mock.patch.object(sys, "argv", ["freesound_search.py"] + queries), \
                mock.patch.object(urllib.request, "urlopen", opener), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                self.module.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue(), opener

    def written(self):
        self.assertTrue(self.candidates.is_file(), "freesound_candidates.json was not written")
        return json.loads(self.candidates.read_text(encoding="utf-8"))

    def query_of(self, opener, call=0):
        request = opener.call_args_list[call][0][0]
        self.assertIsInstance(request, urllib.request.Request)
        return request, urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)


class ArgumentHandling(SearchCase):
    def test_no_query_prints_usage_and_exits_1(self):
        code, out, err, opener = self.run_tool([])
        self.assertEqual(code, 1)
        self.assertIn("Search Freesound for CC0 sounds and print ledger-ready metadata", out)
        self.assertIn('Usage: python3 freesound_search.py "cartoon punch" ["comic impact" ...]', out)
        opener.assert_not_called()
        self.assertFalse(self.candidates.exists())

    def test_missing_key_exits_2_and_writes_nothing(self):
        self.plant_key()
        (self.home / ".config" / "freesound" / "api_key").unlink()
        code, out, err, opener = self.run_tool(["cartoon punch"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR: no Freesound key at", err)
        self.assertIn(str(self.home / ".config" / "freesound" / "api_key"), err)
        opener.assert_not_called()
        self.assertFalse(self.candidates.exists())


class Searching(SearchCase):
    def test_normal_result_lists_gated_rows_and_writes_candidates(self):
        self.plant_key()
        rows = [sound(101, "punch_yelp.ogg", 0.4),
                sound(202, "slow_rumble.ogg", 2.5),
                sound(303, "quick_snap.ogg", 0.8)]
        code, out, err, opener = self.run_tool(
            ["cartoon punch"], {"cartoon punch": payload(*rows, count=3)})

        self.assertEqual(code, 0, err)
        self.assertIn("### 'cartoon punch' — count=3, returned=3", out)
        self.assertIn("  [101] punch_yelp.ogg", out)
        self.assertIn("      0.40s | 2ch | 44100Hz | wav | by uploader_a | Creative Commons 0", out)
        self.assertIn("      tags: impact, punch, foley", out)
        self.assertIn("      url: https://freesound.example/s/101/", out)
        self.assertNotIn("[202]", out)
        self.assertIn("  -> 2 within duration gate 0.05-1.2s", out)
        self.assertIn("=== 2 unique candidate(s) across all queries ===", out)
        self.assertIn("Full metadata written to " + str(self.candidates), out)

        request, params = self.query_of(opener)
        self.assertEqual(request.full_url.split("?")[0], "https://freesound.org/apiv2/search/text/")
        self.assertEqual(params["query"], ["cartoon punch"])
        self.assertEqual(params["filter"], ['license:"Creative Commons 0"'])
        self.assertEqual(params["fields"], [FIELDS])
        self.assertEqual(params["page_size"], ["25"])
        self.assertEqual(params["sort"], ["score"])
        self.assertEqual(request.get_header("Authorization"), "Token " + self.planted_key)

        written = self.written()
        self.assertEqual(set(written), {"101", "303"})
        self.assertEqual(written["101"]["name"], "punch_yelp.ogg")
        self.assertEqual(written["101"]["duration"], 0.4)
        self.assertEqual(written["101"]["previews"]["preview-hq-ogg"],
                         "https://cdn.freesound.example/previews/101_hq.ogg")

    def test_zero_count_reports_zero_and_writes_an_empty_object(self):
        self.plant_key()
        code, out, err, opener = self.run_tool(
            ["hopeless query"], {"hopeless query": payload(count=0)})

        self.assertEqual(code, 0, err)
        self.assertIn("### 'hopeless query' — count=0, returned=0", out)
        self.assertIn("  (zero results — widen by dropping a term, not by rewording)", out)
        self.assertEqual(self.written(), {})

    def test_rows_outside_the_duration_gate_are_dropped(self):
        self.plant_key()
        rows = [sound(1, "too_short.ogg", 0.01), sound(2, "too_long.ogg", 5.0)]
        code, out, err, opener = self.run_tool(
            ["edge case"], {"edge case": payload(*rows, count=2)})

        self.assertEqual(code, 0, err)
        self.assertIn("  -> 0 within duration gate 0.05-1.2s", out)
        self.assertNotIn("[1]", out)
        self.assertNotIn("[2]", out)
        self.assertEqual(self.written(), {})

    def test_duration_gate_includes_its_own_bounds(self):
        self.plant_key()
        rows = [sound(1, "floor.ogg", 0.05), sound(2, "ceiling.ogg", 1.2),
                sound(3, "below.ogg", 0.049), sound(4, "above.ogg", 1.201)]
        code, out, err, opener = self.run_tool(
            ["bounds"], {"bounds": payload(*rows, count=4)})

        self.assertEqual(code, 0, err)
        self.assertIn("  -> 2 within duration gate 0.05-1.2s", out)
        self.assertEqual(set(self.written()), {"1", "2"})

    def test_duplicate_ids_across_queries_are_kept_once(self):
        self.plant_key()
        first = payload(sound(101, "first_name.ogg", 0.4), count=1)
        second = payload(sound(101, "second_name.ogg", 0.4), sound(303, "other.ogg", 0.6), count=2)
        code, out, err, opener = self.run_tool(
            ["query one", "query two"], {"query one": first, "query two": second})

        self.assertEqual(code, 0, err)
        self.assertIn("=== 2 unique candidate(s) across all queries ===", out)
        written = self.written()
        self.assertEqual(set(written), {"101", "303"})
        self.assertEqual(written["101"]["name"], "first_name.ogg")
        _, second_params = self.query_of(opener, call=1)
        self.assertEqual(second_params["query"], ["query two"])

    def test_http_error_reports_the_request_failure_and_still_writes_the_file(self):
        self.plant_key()
        failure = http_error(429, b'{"detail": "throttled"}')
        code, out, err, opener = self.run_tool(["throttled"], {"throttled": failure})
        failure.close()

        self.assertEqual(code, 0)
        self.assertIn("### 'throttled' — REQUEST FAILED", out)
        self.assertIn('HTTP 429: {"detail": "throttled"}', err)
        self.assertIn("=== 0 unique candidate(s) across all queries ===", out)
        self.assertEqual(self.written(), {})


if __name__ == "__main__":
    unittest.main()
