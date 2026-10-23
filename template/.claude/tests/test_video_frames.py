"""Re-runnable proof for scripts/video_frames.py: sheet fitting, moment spacing and the rendered output.

The fitter, moment and token math run on plain numbers. The render cases cut a small ffmpeg test clip in a
temp root and run the real script on it, so the printed sizes are checked against the PNG that ffmpeg wrote.

    python3 .claude/tests/test_video_frames.py
"""
import importlib.util
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "video_frames.py"

spec = importlib.util.spec_from_file_location("video_frames", SCRIPT)
vf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vf)

ASPECTS = (9 / 16, 16 / 9, 3 / 4, 1.0, 9 / 21, 0.2, 4.0)


class TokenMath(unittest.TestCase):
    def test_patch_formula(self):
        self.assertEqual(vf.image_tokens(28, 28), 1)
        self.assertEqual(vf.image_tokens(29, 28), 2)
        self.assertEqual(vf.image_tokens(1244, 940), math.ceil(1244 / 28) * math.ceil(940 / 28))

    def test_sheet_size_counts_padding_between_cells_only(self):
        self.assertEqual(vf.sheet_size(1, 1, 100, 0.5), (100, 50))
        self.assertEqual(vf.sheet_size(3, 2, 100, 0.5), (3 * 100 + 2 * 4, 2 * 50 + 4))


class Fitter(unittest.TestCase):
    def test_every_layout_stays_inside_both_caps(self):
        for aspect in ASPECTS:
            for want in (100, 640, 2000):
                for n in range(1, vf.MAX_FRAMES + 1):
                    cols, rows, cell = vf.fit_grid(n, aspect, want)
                    with self.subTest(aspect=aspect, want=want, n=n):
                        width, height = vf.sheet_size(cols, rows, cell, aspect)
                        self.assertGreaterEqual(cols * rows, n)
                        self.assertLess(cols * rows - n, cols)
                        self.assertLessEqual(cell, want)
                        self.assertLessEqual(max(width, height), vf.MAX_EDGE)
                        self.assertLessEqual(vf.image_tokens(width, height), vf.MAX_TOKENS)

    def test_five_landscape_moments_do_not_become_a_strip(self):
        cols, rows, cell = vf.fit_grid(5, 9 / 16, 640)
        self.assertGreaterEqual(min(cols, rows), 2)
        self.assertGreaterEqual(cell, 400)

    def test_default_twelve_moments_keep_cells_readable(self):
        cols, rows, cell = vf.fit_grid(12, 9 / 16, 640)
        self.assertGreaterEqual(cell, 380)

    def test_near_ties_in_cell_width_go_to_the_landscape_sheet(self):
        for n in (6, 12, 16, 20):
            cols, rows, cell = vf.fit_grid(n, 9 / 16, 640)
            width, height = vf.sheet_size(cols, rows, cell, 9 / 16)
            with self.subTest(n=n):
                self.assertGreaterEqual(width, height)

    def test_small_want_is_honoured_not_enlarged(self):
        self.assertEqual(vf.fit_grid(1, 9 / 16, 320)[2], 320)


class Labels(unittest.TestCase):
    def test_no_font_means_no_filter(self):
        self.assertEqual(vf.label_filter(None, 3.0, 400), "")

    def test_font_path_colon_is_escaped_for_ffmpeg(self):
        flt = vf.label_filter(Path("C:/Windows/Fonts/consola.ttf"), 3.0, 400)
        self.assertIn("fontfile='C\\:/Windows/Fonts/consola.ttf'", flt)
        self.assertIn("setpts=PTS+3.0/TB", flt)


class EvenMoments(unittest.TestCase):
    def test_even_picks_are_interval_midpoints(self):
        self.assertEqual(vf.even_picks(600, 12), [25 + 50 * i for i in range(12)])

    def test_even_picks_never_exceed_the_candidates(self):
        self.assertEqual(vf.even_picks(3, 12), [0, 1, 2])


class SceneMoments(unittest.TestCase):
    def test_full_sheet_keeps_opening_and_closing_frames(self):
        scores = [0.0] * 100
        scores[40], scores[70] = 0.9, 0.8
        picks = vf.pick_moments(scores, 12)
        self.assertEqual(len(picks), 12)
        self.assertIn(0, picks)
        self.assertIn(99, picks)
        self.assertIn(40, picks)
        self.assertIn(70, picks)

    def test_small_sheet_spends_every_slot_on_motion(self):
        scores = [0.0] * 100
        scores[40], scores[70], scores[10] = 0.9, 0.8, 0.5
        self.assertEqual(vf.pick_moments(scores, 3), [10, 40, 70])

    def test_single_moment_is_the_biggest_change(self):
        scores = [0.0] * 50
        scores[33] = 0.7
        self.assertEqual(vf.pick_moments(scores, 1), [33])

    def test_picks_are_sorted_and_bounded(self):
        picks = vf.pick_moments([0.1 * (i % 7) for i in range(40)], 9)
        self.assertEqual(picks, sorted(picks))
        self.assertLessEqual(len(picks), 9)


class SheetPlan(unittest.TestCase):
    def test_ten_minutes_every_ten_seconds_makes_five_sheets_of_twelve(self):
        plan = vf.plan_sheets(600.0, 10.0, 12)
        self.assertEqual([count for _, _, count in plan], [12] * 5)
        self.assertEqual([offset for offset, _, _ in plan], [0, 120, 240, 360, 480])

    def test_interval_shrinks_to_cover_the_window_evenly(self):
        plan = vf.plan_sheets(25.0, 10.0, 12)
        self.assertEqual(plan, [(0.0, 25.0, 3)])

    def test_every_longer_than_window_gives_one_moment(self):
        self.assertEqual(vf.plan_sheets(4.0, 10.0, 12), [(0.0, 4.0, 1)])

    def test_counts_balance_and_windows_are_contiguous(self):
        plan = vf.plan_sheets(130.0, 10.0, 12)
        self.assertEqual([count for _, _, count in plan], [7, 6])
        offset = 0.0
        for start, length, _ in plan:
            self.assertAlmostEqual(start, offset)
            offset += length
        self.assertAlmostEqual(offset, 130.0)

    def test_float_noise_does_not_add_a_moment(self):
        self.assertEqual(sum(count for _, _, count in vf.plan_sheets(0.3, 0.1, 12)), 3)

    def test_too_many_sheets_is_refused(self):
        with self.assertRaises(ValueError):
            vf.plan_sheets(vf.MAX_SHEETS * 12 * 10.0 + 10, 10.0, 12)

    def test_the_sheet_limit_itself_is_allowed(self):
        self.assertEqual(len(vf.plan_sheets(vf.MAX_SHEETS * 12 * 10.0, 10.0, 12)), vf.MAX_SHEETS)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not on PATH")
class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="vf_test_"))
        cls.clip = cls.tmp / "clip.mp4"
        res = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=20:size=640x360:rate=10",
                              "-pix_fmt", "yuv420p", str(cls.clip)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_tool(self, *args):
        out = self.tmp / ("out_" + str(abs(hash(args))))
        res = subprocess.run([sys.executable, str(SCRIPT), str(self.clip), "--out", str(out), *args],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout, res.stderr

    def sheets(self, stdout):
        paths = [Path(p) for p in re.findall(r"^sheet(?: \d+/\d+)?: (.+)$", stdout, re.M)]
        sizes = [tuple(int(v) for v in m) for m in re.findall(r"^\s+(\d+)x(\d+), ", stdout, re.M)]
        return paths, sizes

    def test_printed_size_is_the_png_and_inside_the_caps(self):
        stdout, _ = self.run_tool("--mode", "even", "--frames", "8")
        (path,), (printed,) = self.sheets(stdout)
        self.assertEqual(vf.png_size(path), printed)
        self.assertLessEqual(max(printed), vf.MAX_EDGE)
        tokens = vf.image_tokens(*printed)
        self.assertLessEqual(tokens, vf.MAX_TOKENS)
        self.assertIn(f"~{tokens} tokens", stdout)
        self.assertIn(f"cap: {vf.MAX_TOKENS} tokens", stdout)

    def test_a_grid_with_room_fills_its_empty_cells(self):
        stdout, _ = self.run_tool("--mode", "even", "--frames", "8")
        match = re.search(r"(\d+) moments in (\d+)x(\d+)", stdout)
        moments, cols, rows = (int(v) for v in match.groups())
        self.assertEqual(moments, cols * rows)

    def test_every_splits_into_several_sheets_and_totals_them(self):
        stdout, _ = self.run_tool("--every", "1", "--frames", "5")
        paths, sizes = self.sheets(stdout)
        self.assertEqual(len(paths), 4)
        self.assertTrue(all(p.exists() for p in paths))
        total = sum(vf.image_tokens(*s) for s in sizes)
        self.assertIn(f"total: 4 sheets, ~{total} tokens", stdout)
        times = [float(t) for line in re.findall(r"^times: (.+)$", stdout, re.M) for t in line.split()]
        self.assertEqual(len(times), 20)
        self.assertEqual(times, sorted(times))

    def test_missing_font_is_reported_and_the_sheet_still_renders(self):
        code = ("import importlib.util, sys; s = importlib.util.spec_from_file_location('v', sys.argv[1]); "
                "m = importlib.util.module_from_spec(s); s.loader.exec_module(m); m.FONTS = (); "
                "sys.argv = ['v', *sys.argv[2:]]; m.main()")
        out = self.tmp / "nofont"
        res = subprocess.run([sys.executable, "-c", code, str(SCRIPT), str(self.clip), "--mode", "even",
                              "--frames", "4", "--out", str(out)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("labels: off", res.stdout)
        self.assertTrue(list(out.glob("sheet_*.png")))

    def test_labels_are_not_reported_off_when_a_font_exists(self):
        if vf.find_font() is None:
            self.skipTest("no monospace font on this machine")
        stdout, _ = self.run_tool("--mode", "even", "--frames", "4")
        self.assertNotIn("labels: off", stdout)

    def test_every_refuses_scene_mode(self):
        res = subprocess.run([sys.executable, str(SCRIPT), str(self.clip), "--every", "2", "--mode", "scene"],
                             capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("--every", res.stderr)


if __name__ == "__main__":
    unittest.main()
