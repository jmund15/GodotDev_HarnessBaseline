#!/usr/bin/env python3
"""Re-runnable proof for tools/dispatch_size.py and the `/delegate` step that runs it before a launch; the
dispatch hooks add no size line to an admitted call.

    python3 .claude/tests/test_dispatch_size.py
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(ROOT, ".claude", "tools")
HOOKS = os.path.join(ROOT, ".claude", "hooks")
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
import dispatch_size  # noqa: E402
import test_dispatch_mechanism_guard as guard_proof  # noqa: E402

KB = 1024


class Plant(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name

    def file(self, name, kb):
        path = os.path.join(self.root, name)
        with open(path, "wb") as fh:
            fh.write(b"x" * (kb * KB))
        return path

    def text(self, name, body):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def job(self, label, prompt, must=(), model="opus", effort="high", context=()):
        return {"label": label, "promptPath": self.file(prompt, 1), "model": model, "effort": effort,
                "mustRead": [{"path": p, "lines": 10} for p in must], "contextFiles": list(context)}


class SizeTests(Plant):
    def test_a_path_read_by_every_job_counts_once_with_its_multiplier(self):
        shared = self.file("findings.md", 150)
        jobs = [self.job("j%d" % i, "p%d.md" % i, must=[shared]) for i in range(4)]
        size = dispatch_size.dispatch_size(jobs)
        self.assertEqual(size["jobs"], 4)
        self.assertEqual([j["kb"] for j in size["perJob"]], [151] * 4)
        self.assertEqual(size["duplicated"], [{"path": shared, "kb": 150, "readBy": 4}])
        self.assertEqual(size["duplicatedKb"], 150)
        self.assertEqual(size["totalKb"], 604)

    def test_each_shared_path_is_counted_once_at_its_own_multiplier(self):
        a, b, solo = self.file("a.md", 40), self.file("b.md", 10), self.file("solo.md", 5)
        jobs = [self.job("x", "px.md", must=[a, b, solo]), self.job("y", "py.md", must=[a, b]),
                self.job("z", "pz.md", must=[a])]
        size = dispatch_size.dispatch_size(jobs)
        self.assertEqual({d["path"]: (d["kb"], d["readBy"]) for d in size["duplicated"]}, {a: (40, 3), b: (10, 2)})
        self.assertEqual(size["duplicatedKb"], 50)

    def test_a_path_a_job_lists_twice_counts_once_for_that_job(self):
        shared = self.file("s.md", 10)
        size = dispatch_size.dispatch_size([self.job("x", "px.md", must=[shared], context=[shared])])
        self.assertEqual(size["perJob"][0]["kb"], 11)
        self.assertEqual(size["duplicated"], [])

    def test_an_unreadable_path_counts_zero_and_is_named(self):
        gone = os.path.join(self.root, "gone.md")
        size = dispatch_size.dispatch_size([self.job("x", "px.md", must=[gone])])
        self.assertEqual(size["perJob"][0]["kb"], 1)
        self.assertEqual(size["unreadable"], [gone])
        self.assertIn("gone.md", dispatch_size.format_line(size))

    def test_the_line_names_jobs_models_reading_and_duplication(self):
        shared = self.file("findings.md", 150)
        jobs = [self.job("j%d" % i, "p%d.md" % i, must=[shared]) for i in range(4)]
        self.assertEqual(dispatch_size.format_line(dispatch_size.dispatch_size(jobs)),
                         "dispatch size: 4 jobs (opus·high ×4); reading 151/151/151/151 KB; "
                         "150 KB read by all 4 (600 KB total); heaviest reading 7% of its window (j0)")

    def test_native_args_shapes_read_chains_agents_and_the_shared_context(self):
        ctx, shared = self.file("ctx.md", 20), self.file("s.md", 3)
        chains = {"chains": [{"name": "c", "jobs": [self.job("a", "pa.md"), self.job("b", "pb.md")]}],
                  "contextPath": ctx}
        size = dispatch_size.dispatch_size(chains)
        self.assertEqual((size["jobs"], size["duplicatedKb"]), (2, 20))
        agents = {"agents": [{"key": "k1", "promptPath": self.file("l1.md", 2), "model": "sonnet", "effort": "low",
                              "mustRead": [{"path": shared, "lines": 1}]},
                             {"key": "k2", "prompt": "inline", "mustRead": [{"path": shared, "lines": 1}]}],
                  "contextPrefixPath": ctx}
        size = dispatch_size.dispatch_size(agents)
        self.assertEqual(size["jobs"], 2)
        self.assertEqual({d["path"]: d["readBy"] for d in size["duplicated"]}, {ctx: 2, shared: 2})

    def test_cli_prints_one_line_for_a_jobs_file(self):
        shared = self.file("findings.md", 150)
        spec = os.path.join(self.root, "jobs.json")
        with open(spec, "w", encoding="utf-8") as fh:
            json.dump({"jobs": [self.job("j%d" % i, "p%d.md" % i, must=[shared]) for i in range(2)]}, fh)
        result = subprocess.run([sys.executable, os.path.join(TOOLS, "dispatch_size.py"), spec],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "dispatch size: 2 jobs (opus·high ×2); reading 151/151 KB; 150 KB read by all 2 (300 KB total); "
                         "heaviest reading 7% of its window (j0)")


class NamedFileTests(Plant):
    """A file a prompt or context file names in backticks is read by the seat that was told to read it."""

    def named_job(self, label, body, **extra):
        job = {"label": label, "promptPath": self.text("%s.md" % label, body), "model": "opus", "effort": "high"}
        job.update(extra)
        return job

    def test_a_named_file_counts_once_per_job_and_as_shared_across_jobs(self):
        big = self.file("findings.md", 157)
        notes = self.text("notes.md", "also see `%s`" % big)
        jobs = [self.named_job("j%d" % i, "Read `%s` first, then `%s` again." % (big, big)) for i in range(3)]
        jobs.append(self.named_job("j3", "no names here", contextFiles=[notes]))
        size = dispatch_size.dispatch_size(jobs)
        self.assertEqual(size["named"], [{"path": big, "kb": 157, "namedBy": 4}])
        self.assertEqual(size["namedKb"], 157)
        self.assertEqual(size["perJob"][0]["namedKb"], 157)
        self.assertEqual([j["kb"] for j in size["perJob"]][:3], [0, 0, 0])
        self.assertTrue(dispatch_size.format_line(size).endswith("; names 157 KB more (read by all 4: 628 KB)"),
                        dispatch_size.format_line(size))

    def test_a_file_named_by_one_job_prints_no_sharing_clause(self):
        solo = self.file("solo.md", 20)
        jobs = [self.named_job("a", "Read `%s`." % solo), self.named_job("b", "nothing")]
        line = dispatch_size.format_line(dispatch_size.dispatch_size(jobs))
        self.assertTrue(line.endswith("; names 20 KB more"), line)

    def test_each_sharing_multiplier_prints_its_own_group(self):
        a, b = self.file("a.md", 10), self.file("b.md", 4)
        jobs = [self.named_job("x", "`%s` and `%s`" % (a, b)), self.named_job("y", "`%s`" % a),
                self.named_job("z", "`%s`" % a)]
        line = dispatch_size.format_line(dispatch_size.dispatch_size(jobs))
        self.assertTrue(line.endswith("; names 14 KB more (read by all 3: 30 KB)"), line)

    def test_relative_paths_and_a_line_suffix_resolve_against_the_root(self):
        self.file("rel.md", 6)
        size = dispatch_size.dispatch_size([self.named_job("a", "see `rel.md:12-30` and `rel.md`")], root=self.root)
        self.assertEqual([(n["kb"], n["namedBy"]) for n in size["named"]], [(6, 1)])

    def test_a_missing_path_a_directory_and_prose_are_not_named_files(self):
        size = dispatch_size.dispatch_size([self.named_job(
            "a", "`%s` `%s` `not a path` `x<y>|z` `\u0000`" % (os.path.join(self.root, "gone.md"), self.root))])
        self.assertEqual((size["named"], size["namedKb"]), ([], 0))
        self.assertNotIn("names", dispatch_size.format_line(size))

    def test_a_file_the_job_already_lists_is_not_counted_again_as_named(self):
        listed = self.file("listed.md", 9)
        size = dispatch_size.dispatch_size([self.named_job("a", "`%s`" % listed, mustRead=[{"path": listed, "lines": 5}])])
        self.assertEqual(size["named"], [])
        self.assertEqual(size["perJob"][0]["kb"], 9)

    def test_the_shared_context_naming_a_file_names_it_for_every_job(self):
        big = self.file("findings.md", 30)
        ctx = self.text("ctx.md", "CONTEXT: read `%s`" % big)
        spec = {"jobs": [self.named_job("a", "x"), self.named_job("b", "y")], "contextPath": ctx}
        size = dispatch_size.dispatch_size(spec)
        self.assertEqual(size["named"], [{"path": big, "kb": 30, "namedBy": 2}])

    def test_an_inline_prompt_is_scanned(self):
        big = self.file("findings.md", 12)
        size = dispatch_size.dispatch_size({"jobs": [{"label": "a", "prompt": "open `%s`" % big}]})
        self.assertEqual(size["named"], [{"path": big, "kb": 12, "namedBy": 1}])


class HookTests(Plant):
    def run_hook(self, payload):
        tmp = self.root
        loaded = guard_proof.transcript(tmp, "loaded.jsonl", guard_proof.NO_CANON
                                        + json.dumps({"m": guard_proof.MARKER_0}, ensure_ascii=False) + "\n")
        return guard_proof.run(guard_proof.workflow_brief(loaded, payload))

    def test_an_admitted_workflow_call_carries_no_size_line(self):
        shared = self.file("findings.md", 150)
        args = {"jobs": [self.job("j%d" % i, "p%d.md" % i, must=[shared]) for i in range(2)]}
        decision, text = self.run_hook(args)
        self.assertEqual(decision, guard_proof.ALLOW)
        self.assertNotIn("dispatch size", text)

    def test_an_admitted_call_naming_files_or_an_unreadable_path_carries_no_size_line(self):
        big = self.file("findings.md", 157)
        named = {"jobs": [{"label": "j%d" % i, "promptPath": self.text("p%d.md" % i, "read `%s`" % big),
                           "model": "opus", "effort": "high"} for i in range(2)]}
        gone = {"jobs": [self.job("j", "p.md", must=[os.path.join(self.root, "gone.md")])]}
        for args in (named, gone):
            decision, text = self.run_hook(args)
            self.assertEqual(decision, guard_proof.ALLOW)
            self.assertNotIn("dispatch size", text)
            self.assertNotIn("names 157 KB", text)
            self.assertNotIn("gone.md", text)

    def test_workflow_hook_exits_0_and_stays_silent_on_args_it_cannot_size(self):
        for args in ({"jobs": "nonsense"}, {"jobs": [42, None]}, "{not json", {"chains": [{"jobs": 3}]}):
            with self.subTest(args=args):
                decision, text = self.run_hook(args)
                self.assertEqual(decision, guard_proof.ALLOW)
                self.assertNotIn("dispatch size", text)


class DelegateStepTests(unittest.TestCase):
    """The fit line the Pre-Dispatch Checklist tells the dispatcher to read exists before the launch call."""

    DELEGATE = os.path.join(ROOT, ".claude", "commands", "delegate.md")

    def text(self):
        with open(self.DELEGATE, encoding="utf-8") as fh:
            return fh.read()

    def test_allowed_tools_admit_the_size_tool(self):
        head = self.text().split("\n---", 1)[0]
        self.assertIn("Bash(python3 .claude/tools/dispatch_size.py:*)", head)

    def test_the_converged_flow_runs_it_on_the_jobs_file_before_the_launch(self):
        step = self.text().split("2. Write `{\"jobs\"", 1)[1].split("\n3. ", 1)[0]
        self.assertIn("python3 .claude/tools/dispatch_size.py <file>", step)
        self.assertLess(step.index("dispatch_size.py <file>"), step.index("sidecar_fanout.py <file> --detach"))

    def test_the_multi_job_route_runs_it_too(self):
        section = self.text().split("## 3. Dispatch through existing owners", 1)[1].split("## 4.", 1)[0]
        self.assertIn("python3 .claude/tools/dispatch_size.py <file>", section)


class WindowFitTests(Plant):
    """One home for a job's reading and its window fit, at the orchestration Pre-Dispatch Checklist's 2.5 chars
    per token. The pin's usable window comes from the live registry, so a version change on a row moves it."""

    @staticmethod
    def haiku_usable():
        import model_registry
        return model_registry.usable_window(model_registry.resolve("haiku", model_registry.load()))

    def test_a_job_reading_more_than_its_pins_window_is_marked_over(self):
        usable = self.haiku_usable()
        big = self.file("big.md", math.ceil(usable * dispatch_size.CHARS_PER_TOKEN / KB) + 40)
        size = dispatch_size.dispatch_size([self.job("wide", "p.md", must=[big], model="haiku")])
        job = size["perJob"][0]
        self.assertEqual(job["usable"], usable)
        self.assertGreater(job["over"], 0)
        self.assertEqual(job["over"], job["tokens"] - usable)
        self.assertIn("over its usable window: wide", dispatch_size.format_line(size))

    def test_a_job_that_fits_or_pins_an_unknown_model_is_not_marked(self):
        small = self.file("small.md", 10)
        fits = dispatch_size.dispatch_size([self.job("ok", "p.md", must=[small], model="haiku")])
        self.assertEqual((fits["perJob"][0]["over"], fits["perJob"][0]["usable"]), (0, self.haiku_usable()))
        unknown = dispatch_size.dispatch_size([self.job("odd", "p.md", must=[small], model="no-such-row")])
        self.assertEqual((unknown["perJob"][0]["over"], unknown["perJob"][0]["usable"]), (0, None))
        for size in (fits, unknown):
            self.assertNotIn("usable window", dispatch_size.format_line(size))

    def reading_of(self, name, share):
        """A file that, with the 1 KB prompt file, is `share` of the haiku window in tokens exactly."""
        path = os.path.join(self.root, name)
        with open(path, "wb") as fh:
            fh.write(b"x" * (int(self.haiku_usable() * dispatch_size.CHARS_PER_TOKEN * share) - KB))
        return path

    def test_the_line_names_the_heaviest_jobs_share_of_its_window(self):
        light = self.reading_of("light.md", 0.25)
        heavy = self.reading_of("heavy.md", 0.5)
        one = dispatch_size.format_line(dispatch_size.dispatch_size(
            [self.job("only", "p1.md", must=[heavy], model="haiku")]))
        self.assertIn("; heaviest reading 50% of its window", one)
        self.assertNotIn("(only)", one)
        two = dispatch_size.format_line(dispatch_size.dispatch_size([
            self.job("lean", "p2.md", must=[light], model="haiku"),
            self.job("wide", "p3.md", must=[heavy], model="haiku")]))
        self.assertIn("; heaviest reading 50% of its window (wide)", two)

    def test_a_line_with_no_sizable_pin_names_no_share(self):
        small = self.file("small.md", 10)
        odd = dispatch_size.format_line(dispatch_size.dispatch_size(
            [self.job("odd", "p.md", must=[small], model="no-such-row")]))
        self.assertNotIn("of its window", odd)

    def claimant_job(self, label, model, shape, prompt="p.md", kb=20):
        return {"label": label, "promptPath": self.file(prompt, kb), "model": model, "effort": "medium",
                "shape": shape}

    def test_a_pricier_pin_gets_a_pre_launch_line_naming_its_cheaper_claimants_with_the_job_reading(self):
        spec = {"jobs": [self.claimant_job("surv", "claude-sonnet-5-5", "survey", "a.md"),
                         self.claimant_job("exec", "claude-sonnet-5-5", "author", "b.md"),
                         self.claimant_job("cheap", "claude-haiku-5-5", "survey", "c.md")]}
        lines = dispatch_size.fact_lines(spec, self.root)
        self.assertEqual(len(lines), 2, lines)
        survey, execute = lines
        for text, tier in ((survey, "fanout"), (execute, "executor")):
            self.assertIn("surv" if text is survey else "exec", text)
            self.assertIn("`%s`" % tier, text)
            self.assertIn("haiku (cost 10;", text)
            self.assertIn("100,000-token long-context point", text)
            self.assertIn("draw ≈0.05x the pin", text)
            self.assertNotIn("$", text)
        self.assertIn("reading 8,192 tokens fits", survey)
        self.assertNotIn("cheap", "".join(lines).replace("cheaper", "").replace("cheapest", ""))
        self.assertNotIn("pin haiku", survey.lower())

    def test_a_reading_past_the_cheaper_rows_price_point_is_stated_past_it(self):
        spec = {"jobs": [self.claimant_job("wide", "claude-sonnet-5-5", "survey", "w.md", kb=300)]}
        line = dispatch_size.fact_lines(spec, self.root)[0]
        self.assertIn("reading 122,880 tokens", line)
        self.assertIn("past its 100,000-token long-context point", line)
        self.assertIn("draw ≈0.05x the pin", line)
        self.assertNotIn("$", line)

    def test_a_pin_that_is_the_cheapest_claimant_or_unknown_gets_no_fact_line(self):
        spec = {"jobs": [self.claimant_job("a", "claude-haiku-5-5", "survey", "a.md"),
                         self.claimant_job("b", "no-such-row", "survey", "b.md")]}
        self.assertEqual(dispatch_size.fact_lines(spec, self.root), [])
        self.assertEqual(dispatch_size.fact_lines("not a spec", self.root), [])

    def test_main_prints_the_size_line_then_the_fact_lines(self):
        path = os.path.join(self.root, "jobs.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"jobs": [self.claimant_job("surv", "claude-sonnet-5-5", "survey", "a.md")]}, fh)
        done = subprocess.run([sys.executable, os.path.join(TOOLS, "dispatch_size.py"), path], capture_output=True,
                              text=True, encoding="utf-8", stdin=subprocess.DEVNULL, cwd=self.root)
        out = done.stdout.splitlines()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(out[0].startswith("dispatch size:"), out)
        self.assertTrue(any("cheaper claimants" in line and "haiku" in line for line in out[1:]), out)

    def test_the_line_helper_is_one_string_or_none(self):
        self.file("a.md", 1)
        spec = {"jobs": [self.job("j", "a.md")]}
        self.assertEqual(dispatch_size.line(spec, self.root), dispatch_size.format_line(
            dispatch_size.dispatch_size(spec, self.root)))
        self.assertIsNone(dispatch_size.line({"jobs": []}, self.root))
        self.assertIsNone(dispatch_size.line("not a spec", self.root))


if __name__ == "__main__":
    unittest.main()
