"""Proof for scripts/freesound_fetch.py: the Freesound API's metadata and the preview download are planted,
so no network runs. Covers argument handling, a fetched sound with its full provenance sidecar, a non-CC0
refusal, a missing hq-ogg preview, a mixed batch, an API error mid-batch and a missing key. The tool is
loaded from a copied `.claude/scripts` layout under a temp root, so its writes never reach the repository."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "freesound_fetch.py"
CLI_USAGE = ROOT / ".claude" / "tools" / "cli_usage.py"

CC0 = "http://creativecommons.org/publicdomain/zero/1.0/"
BY_NC = "http://creativecommons.org/licenses/by-nc/4.0/"
PREVIEW = "https://cdn.freesound.example/previews/42_123_hq.ogg"
FIELDS = "id,name,type,license,username,url,duration,channels,samplerate,bitdepth,filesize,previews"

META = {
    "id": 42,
    "name": "Wind Whoosh",
    "type": "wav",
    "license": CC0,
    "username": "uploader_a",
    "url": "https://freesound.example/s/42/",
    "duration": 0.83,
    "channels": 2,
    "samplerate": 44100,
    "bitdepth": 16,
    "filesize": 123456,
    "previews": {"preview-hq-ogg": PREVIEW, "preview-lq-ogg": "https://cdn.example/low.ogg"},
}


def response(payload):
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def http_error(status, body):
    return urllib.error.HTTPError("https://freesound.example/apiv2/", status, "planted", {},
                                  io.BytesIO(body))


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
    spec = importlib.util.spec_from_file_location("freesound_fetch_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="freesound_fetch_proof_")
        self.root = Path(self.tmp.name)
        self.module = load_tool(plant_tool(self.root))
        self.home = self.root / "home"
        (self.home / ".config" / "freesound").mkdir(parents=True)
        self.out = self.root / "out"
        self.planted_key = "test-key"

    def tearDown(self):
        self.tmp.cleanup()

    def plant_key(self, token=None):
        (self.home / ".config" / "freesound" / "api_key").write_text(
            (self.planted_key if token is None else token) + "\n", encoding="utf-8")

    def download(self, url, dest):
        Path(dest).write_bytes(b"OggSdata")
        return dest, None

    def run_tool(self, argv, respond=None, download=None):
        respond = respond or (lambda req: response(META))
        opener = mock.Mock(side_effect=lambda req, **kwargs: respond(req))
        retrieve = mock.Mock(side_effect=download or self.download)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"USERPROFILE": str(self.home), "HOME": str(self.home)}), \
                mock.patch.object(sys, "argv", ["freesound_fetch.py"] + [str(a) for a in argv]), \
                mock.patch.object(urllib.request, "urlopen", opener), \
                mock.patch.object(urllib.request, "urlretrieve", retrieve), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                self.module.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue(), opener, retrieve

    def sidecar(self):
        path = self.out / "provenance.json"
        self.assertTrue(path.is_file(), "provenance.json was not written")
        return json.loads(path.read_text(encoding="utf-8"))

    def request_url(self, opener):
        request = opener.call_args[0][0]
        self.assertIsInstance(request, urllib.request.Request)
        return request


class ArgumentHandling(FetchCase):
    def test_no_arguments_prints_usage_and_exits_1(self):
        code, out, err, opener, retrieve = self.run_tool([])
        self.assertEqual(code, 1)
        self.assertIn("Fetch specific Freesound sounds by id into a directory", out)
        self.assertIn("Usage: python3 freesound_fetch.py <out_dir> <id> [<id> ...]", out)
        opener.assert_not_called()
        retrieve.assert_not_called()

    def test_output_dir_alone_is_still_usage(self):
        code, out, err, opener, retrieve = self.run_tool([self.out])
        self.assertEqual(code, 1)
        self.assertIn("Usage: python3 freesound_fetch.py <out_dir> <id> [<id> ...]", out)
        opener.assert_not_called()

    def test_missing_key_exits_2_and_writes_no_sidecar(self):
        self.plant_key()
        (self.home / ".config" / "freesound" / "api_key").unlink()
        code, out, err, opener, retrieve = self.run_tool([self.out, "42"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR: no Freesound key at", err)
        self.assertIn(str(self.home / ".config" / "freesound" / "api_key"), err)
        opener.assert_not_called()
        self.assertFalse((self.out / "provenance.json").exists())


class Fetching(FetchCase):
    def test_fetched_sound_lands_with_full_provenance(self):
        self.plant_key()
        code, out, err, opener, retrieve = self.run_tool([self.out, "42"])

        self.assertEqual(code, 0, err)
        self.assertIn("  [42] Wind Whoosh -> 42.ogg (8 bytes)", out)
        self.assertIn("        original: .wav 44100Hz 2ch 16bit 123456 bytes", out)
        self.assertIn("1 fetched. Provenance sidecar:", out)

        request = self.request_url(opener)
        self.assertEqual(request.full_url,
                         "https://freesound.org/apiv2/sounds/42/?fields=" + FIELDS)
        self.assertEqual(request.get_header("Authorization"), "Token " + self.planted_key)

        retrieve.assert_called_once_with(PREVIEW, str(self.out / "42.ogg"))
        self.assertEqual((self.out / "42.ogg").read_bytes(), b"OggSdata")

        self.assertEqual(self.sidecar(), [{
            "id": 42,
            "name": "Wind Whoosh",
            "author": "uploader_a",
            "license": "CC0-1.0",
            "license_url": CC0,
            "source_url": "https://freesound.example/s/42/",
            "fetched_file": "42.ogg",
            "fetched_variant": "preview-hq-ogg",
            "fetched_url": PREVIEW,
            "original_format": "wav",
            "original_samplerate": 44100,
            "original_channels": 2,
            "original_bitdepth": 16,
            "duration": 0.83,
        }])

    def test_non_cc0_license_is_refused_and_not_downloaded(self):
        self.plant_key()
        meta = dict(META, license=BY_NC)
        code, out, err, opener, retrieve = self.run_tool(
            [self.out, "43"], respond=lambda req: response(meta))

        self.assertEqual(code, 0, err)
        self.assertIn("[43] REFUSED — license is not CC0: " + BY_NC, out)
        retrieve.assert_not_called()
        self.assertEqual(self.sidecar(), [])

    def test_missing_hq_ogg_preview_is_skipped(self):
        self.plant_key()
        meta = dict(META, previews={"preview-lq-ogg": "https://cdn.example/low.ogg"})
        code, out, err, opener, retrieve = self.run_tool(
            [self.out, "44"], respond=lambda req: response(meta))

        self.assertEqual(code, 0, err)
        self.assertIn("[44] no hq-ogg preview — skipped", out)
        retrieve.assert_not_called()
        self.assertEqual(self.sidecar(), [])

    def test_mixed_batch_keeps_only_what_was_fetched(self):
        self.plant_key()
        metas = {
            "42": META,
            "43": dict(META, id=43, license=BY_NC),
            "44": dict(META, id=44, previews={}),
        }

        def respond(req):
            sid = req.full_url.split("/sounds/")[1].split("/")[0]
            return response(metas[sid])

        code, out, err, opener, retrieve = self.run_tool([self.out, "42", "43", "44"], respond=respond)

        self.assertEqual(code, 0, err)
        self.assertIn("[43] REFUSED — license is not CC0:", out)
        self.assertIn("[44] no hq-ogg preview — skipped", out)
        self.assertIn("1 fetched. Provenance sidecar:", out)
        self.assertEqual([r["id"] for r in self.sidecar()], [42])

    def test_api_error_reports_and_still_writes_the_sidecar(self):
        self.plant_key()

        def respond(req):
            if "/sounds/999/" in req.full_url:
                raise http_error(404, b'{"detail": "Not found."}')
            return response(META)

        code, out, err, opener, retrieve = self.run_tool([self.out, "42", "999"], respond=respond)

        self.assertEqual(code, 0, err)
        self.assertIn("[999] REQUEST FAILED", out)
        self.assertIn('HTTP 404: {"detail": "Not found."}', err)
        self.assertEqual([r["id"] for r in self.sidecar()], [42])
        self.assertEqual((self.out / "42.ogg").read_bytes(), b"OggSdata")


if __name__ == "__main__":
    unittest.main()
