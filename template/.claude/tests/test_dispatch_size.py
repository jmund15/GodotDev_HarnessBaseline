#!/usr/bin/env python3
"""Re-runnable proof for tools/dispatch_size.py and the `/delegate` step that runs it before a launch; the
dispatch hooks add no size line to an admitted call.

    python3 .claude/tests/test_dispatch_size.py
"""
import json
import math
import os
import shutil
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
        self.login = os.path.join(self.root, "login")
        os.mkdir(self.login)
        self.ledger = os.path.join(self.root, "sidecar_ledger.jsonl")
        for name, value in (("CLAUDE_CONFIG_DIR", self.login),
                            ("SIDECAR_LEDGER_PATH", self.ledger),
                            ("PROVIDER_CAPACITY_CACHE", os.path.join(self.root, "no_capacity_cache.json"))):
            saved = os.environ.get(name)
            os.environ[name] = value
            self.addCleanup(lambda name=name, saved=saved: os.environ.pop(name, None) if saved is None
                            else os.environ.__setitem__(name, saved))

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
                         "150 KB read by all 4 (600 KB total); heaviest reading 6% of its compaction point, 967000 tokens (j0)")

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
        env = dict(os.environ)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env["HARNESS_HOOK_STATE_DIR"] = os.path.join(self.root, "state")
        result = subprocess.run([sys.executable, os.path.join(TOOLS, "dispatch_size.py"), spec],
                                capture_output=True, text=True, encoding="utf-8", env=env,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0],
                         "dispatch size: 2 jobs (opus·high ×2); reading 151/151 KB; 150 KB read by all 2 (300 KB total); "
                         "heaviest reading 6% of its compaction point, 967000 tokens (j0)")
        self.assertEqual(result.stdout.splitlines()[1:], [
            "cheaper claimants: `j0` (opus) not compared: neither its kind row nor its own tier names one",
            "cheaper claimants: `j1` (opus) not compared: neither its kind row nor its own tier names one",
            "preflight: not checked: ladder and skill credit (CLAUDE_CODE_SESSION_ID is unset)"])


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
        self.assertIn("628", dispatch_size.format_line(size), dispatch_size.format_line(size))

    def test_a_file_named_by_one_job_prints_no_sharing_clause(self):
        solo = self.file("solo.md", 20)
        jobs = [self.named_job("a", "Read `%s`." % solo), self.named_job("b", "nothing")]
        line = dispatch_size.format_line(dispatch_size.dispatch_size(jobs))
        self.assertIn("20", line)

    def test_each_sharing_multiplier_prints_its_own_group(self):
        a, b = self.file("a.md", 10), self.file("b.md", 4)
        jobs = [self.named_job("x", "`%s` and `%s`" % (a, b)), self.named_job("y", "`%s`" % a),
                self.named_job("z", "`%s`" % a)]
        line = dispatch_size.format_line(dispatch_size.dispatch_size(jobs))
        self.assertIn("14", line)
        self.assertIn("30", line)

    def test_a_file_past_the_read_cap_is_sized_by_stat_without_reading_it(self):
        big = self.file("weights.gguf", 10)
        saved_cap, saved_read = dispatch_size.MAX_SCAN_BYTES, dispatch_size.Path.read_bytes
        dispatch_size.MAX_SCAN_BYTES = 4 * KB

        def refuse(path_self):
            if str(path_self) == big:
                raise AssertionError("read a file past the cap")
            return saved_read(path_self)

        dispatch_size.Path.read_bytes = refuse
        try:
            self.assertEqual(dispatch_size._size(big, self.root), (10 * KB, 10 * KB))
        finally:
            dispatch_size.MAX_SCAN_BYTES, dispatch_size.Path.read_bytes = saved_cap, saved_read

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
        return model_registry.compaction_point(model_registry.resolve("haiku", model_registry.load()))[0]

    def test_a_job_reading_more_than_its_pins_window_is_marked_over(self):
        usable = self.haiku_usable()
        big = self.file("big.md", math.ceil(usable * dispatch_size.CHARS_PER_TOKEN / KB) + 40)
        size = dispatch_size.dispatch_size([self.job("wide", "p.md", must=[big], model="haiku")])
        job = size["perJob"][0]
        self.assertEqual(job["usable"], usable)
        self.assertGreater(job["over"], 0)
        self.assertEqual(job["over"], job["tokens"] - usable)

    def test_a_job_that_fits_or_pins_an_unknown_model_is_not_marked(self):
        small = self.file("small.md", 10)
        fits = dispatch_size.dispatch_size([self.job("ok", "p.md", must=[small], model="haiku")])
        self.assertEqual((fits["perJob"][0]["over"], fits["perJob"][0]["usable"]), (0, self.haiku_usable()))
        unknown = dispatch_size.dispatch_size([self.job("odd", "p.md", must=[small], model="no-such-row")])
        self.assertEqual((unknown["perJob"][0]["over"], unknown["perJob"][0]["usable"]), (0, None))
        for size in (fits, unknown):
            self.assertNotIn("usable window", dispatch_size.format_line(size))

    def test_a_job_listing_rows_is_sized_against_its_tightest_row(self):
        import model_registry
        data = model_registry.load()
        windows = {alias: model_registry.compaction_point(model_registry.resolve(alias, data))[0]
                   for alias in ("haiku", "opus")}
        self.assertNotEqual(windows["haiku"], windows["opus"], "the proof needs two rows with different windows")
        small = self.file("small.md", 10)
        job = {"label": "multi", "promptFile": self.file("p.md", 1), "contextFiles": [small],
               "rows": {"opus": "high", "haiku": "low"}}
        sized = dispatch_size.dispatch_size([job])["perJob"][0]
        self.assertEqual(sized["usable"], min(windows.values()))
        listed = dispatch_size.dispatch_size([dict(job, rows={"opus": "high"})])["perJob"][0]
        pinned = dispatch_size.dispatch_size([{k: v for k, v in job.items() if k != "rows"} | {
            "alias": "opus", "effort": "high"}])["perJob"][0]
        self.assertEqual((listed["usable"], listed["effort"]), (pinned["usable"], "high"))

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
        self.assertIn("%d" % self.haiku_usable(), one)
        self.assertNotIn("(only)", one)
        two = dispatch_size.format_line(dispatch_size.dispatch_size([
            self.job("lean", "p2.md", must=[light], model="haiku"),
            self.job("wide", "p3.md", must=[heavy], model="haiku")]))
        self.assertIn("%d" % self.haiku_usable(), two)
        self.assertIn("(wide)", two)

    def test_a_line_with_no_sizable_pin_names_no_share(self):
        small = self.file("small.md", 10)
        odd = dispatch_size.format_line(dispatch_size.dispatch_size(
            [self.job("odd", "p.md", must=[small], model="no-such-row")]))

    def claimant_job(self, label, model, kind, prompt="p.md", kb=20):
        return {"label": label, "promptPath": self.file(prompt, kb), "model": model, "effort": "medium",
                "kind": kind}

    def test_a_review_seat_takes_its_claimant_tier_from_its_own_lens(self):
        """A `plc-*` seat's tier is `lens.seat_tier`, never a `kind` row's default."""
        import lens
        seats = [("plc-memory-alignment", "architect"), ("plc-instruction-quality", "fanout")]
        if any(lens.seat_tier(key) is None for key, _tier in seats):  # the plan-check registry ships in the coding layer
            self.skipTest("this checkout's lens registries carry no plan-check seats")
        agents = [{"key": key, "promptPath": self.file(key + ".md", 4), "model": "claude-opus-5-5",
                   "effort": "high", "kind": "judge"} for key, _tier in seats]
        lines = dispatch_size.fact_lines({"agents": agents}, self.root)
        for key, tier in seats:
            self.assertEqual(lens.seat_tier(key), tier, key)
            matching = [text for text in lines if "`%s`" % key in text]
            self.assertEqual(len(matching), 1, lines)
            self.assertIn("for `%s`" % tier, matching[0])

    def test_a_pricier_pin_gets_a_pre_launch_line_naming_its_cheaper_claimants_with_the_job_reading(self):
        spec = {"jobs": [self.claimant_job("surv", "claude-sonnet-5-5", "scout", "a.md"),
                         self.claimant_job("exec", "claude-sonnet-5-5", "author", "b.md"),
                         self.claimant_job("cheap", "claude-haiku-5-5", "scout", "c.md")]}
        lines = dispatch_size.fact_lines(spec, self.root, read=lambda name, _data=None: None)
        self.assertEqual(len(lines), 3, lines)
        survey, execute, closing = lines
        for text, tier in ((survey, "fanout"), (execute, "executor")):
            self.assertIn("surv" if text is survey else "exec", text)
            self.assertIn("`%s`" % tier, text)
            self.assertIn("haiku (cost 10;", text)
            self.assertIn("100,000", text)
            self.assertIn("draw ≈0.05x the pin", text)
            self.assertNotIn("$", text)
        self.assertIn("reading 8,192 tokens fits", survey)
        self.assertEqual(closing, "pin is the cheapest claimant: cheap")
        self.assertNotIn("pin haiku", survey.lower())

    def test_a_written_tier_outranks_the_kind_row(self):
        job = dict(self.claimant_job("ex", "claude-sonnet-5-5", "scout", "x.md"), tier="executor")
        line = dispatch_size.fact_lines({"jobs": [job]}, self.root)[0]
        self.assertIn("`executor`", line)
        self.assertNotIn("`fanout`", line)

    def test_a_reading_past_the_cheaper_rows_price_point_is_stated_past_it(self):
        spec = {"jobs": [self.claimant_job("wide", "claude-sonnet-5-5", "scout", "w.md", kb=300)]}
        line = dispatch_size.fact_lines(spec, self.root)[0]
        self.assertIn("reading 122,880 tokens", line)
        self.assertIn("100,000", line)
        self.assertIn("draw ≈0.05x the pin", line)
        self.assertNotIn("$", line)

    def test_a_haiku_executor_pin_prints_pin_is_the_cheapest_claimant(self):
        spec = {"jobs": [self.claimant_job("a", "claude-haiku-5-5", "author", "a.md")]}
        self.assertEqual(dispatch_size.fact_lines(spec, self.root, read=lambda name, _data=None: None),
                         ["pin is the cheapest claimant: a"])
        self.assertEqual(dispatch_size.fact_lines("not a spec", self.root), [])

    def test_the_m6_capture_names_haiku_for_each_sonnet_executor_pin_and_sizes_every_job(self):
        with open(os.path.join(HERE, "fixtures", "dispatch_m6_jobs.json"), encoding="utf-8") as fh:
            spec = json.load(fh)
        spec["contextPath"] = self.text("xcontext.md", "context\n")
        for job in spec["jobs"]:
            job["promptPath"] = self.text(job["promptPath"], "x" * 6 * KB)
        size = dispatch_size.dispatch_size(spec, self.root)
        lines = dispatch_size.fact_lines(spec, self.root, read=lambda name, _data=None: None)
        sonnet = [job["label"] for job in spec["jobs"] if job["model"].startswith("claude-sonnet-5-5")]
        for label in sonnet:
            matching = [text for text in lines if "`%s`" % label in text]
            self.assertEqual(len(matching), 1, lines)
            self.assertIn("cheaper there: haiku", matching[0])
        self.assertEqual(lines[-1], "pin is the cheapest claimant: m6-sync, m6-repo", lines)
        self.assertEqual(len(lines), 4, lines)
        self.assertTrue(all(job["usable"] for job in size["perJob"]), size["perJob"])
        self.assertIn("heaviest reading", dispatch_size.format_line(size))

    def test_an_unresolvable_pin_names_itself_in_one_line(self):
        spec = {"jobs": [self.claimant_job("odd", "no-such-row", "author", "o.md")]}
        lines = dispatch_size.fact_lines(spec, self.root)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("`odd`", lines[0])
        self.assertIn("no-such-row", lines[0])
        self.assertIn("does not resolve", lines[0])

    def test_a_pin_no_row_matches_prints_not_compared_and_not_sized_never_empty_output(self):
        spec = {"jobs": [self.claimant_job("odd", "no-such-row", "author", "o.md"),
                         self.claimant_job("fine", "claude-haiku-5-5", "author", "f.md")]}
        lines = dispatch_size.fact_lines(spec, self.root)
        self.assertIn("`odd`", lines[0])
        self.assertIn("no-such-row", lines[0])
        self.assertEqual(lines[-1], "pin is the cheapest claimant: fine", lines)
        summary = dispatch_size.format_line(dispatch_size.dispatch_size(spec, self.root))
        self.assertIn("not sized", summary)
        self.assertIn("odd", summary)

    def test_main_prints_the_size_line_then_the_fact_lines(self):
        path = os.path.join(self.root, "jobs.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"jobs": [self.claimant_job("surv", "claude-sonnet-5-5", "scout", "a.md")]}, fh)
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=self.root)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        done = subprocess.run([sys.executable, os.path.join(TOOLS, "dispatch_size.py"), path], capture_output=True,
                              text=True, encoding="utf-8", stdin=subprocess.DEVNULL, cwd=self.root, env=env)
        out = done.stdout.splitlines()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(out[0].startswith("dispatch size:"), out)
        self.assertTrue(any("cheaper claimants" in line and "haiku" in line for line in out[1:]), out)

    def test_the_minus_cell_is_dropped_when_the_tiers_rows_reached_context(self):
        """A `for-role` receipt or the full ladder in context already carries the `±` cell: state it once."""
        spec = {"jobs": [self.claimant_job("surv", "claude-sonnet-5-5", "scout", "a.md")]}
        plain = dispatch_size.fact_lines(spec, self.root)[0]
        self.assertIn("± ", plain)
        state = os.path.join(self.root, "state")
        saved = os.environ.get("HARNESS_HOOK_STATE_DIR")
        os.environ["HARNESS_HOOK_STATE_DIR"] = state
        try:
            import _hook_state
            _hook_state.mark_ladder_slice("size-in-context", ["fanout"])
            dropped = dispatch_size.fact_lines(spec, self.root, session_id="size-in-context")[0]
        finally:
            if saved is None:
                os.environ.pop("HARNESS_HOOK_STATE_DIR", None)
            else:
                os.environ["HARNESS_HOOK_STATE_DIR"] = saved
        self.assertNotIn("± ", dropped)
        self.assertIn("cheaper there: haiku", dropped)

    def test_a_claimant_line_names_another_plan_whose_quota_will_expire_unused(self):
        data = {"transports": {"anthropic": {"costModel": "plan-quota"}, "go": {"costModel": "plan-quota"}},
                "models": [
                    {"transport": "anthropic", "id": "pin-x", "alias": "pin-a", "roles": ["executor"],
                     "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000}},
                    {"transport": "anthropic", "id": "cheap-x", "alias": "cheap-a", "roles": ["executor"],
                     "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000}},
                    {"transport": "go", "id": "go-exec", "alias": "go-exec", "roles": ["executor"]}]}
        ladder = self.text("ladder.md", "# Ladder\n\n## Role guidance\n\n| model | cost | ± |\n|---|---|---|\n"
                                        "| pin-a | 7 | + the strong default |\n| cheap-a | 10 | + cheap |\n")
        job = self.claimant_job("exec", "pin-x", "author", "e.md")
        job.pop("kind")
        job["tier"] = "executor"
        now = 1_800_000_000.0
        go = {"schemaVersion": 1, "transport": "go", "costModel": "plan-quota", "sourceKind": "live",
              "observedAt": "2027-01-15T08:00:00Z", "status": "available",
              "quota": {"windows": [{"name": "seven_day", "usedPercent": 20,
                                     "resetsAt": now + 2 * 86400, "windowDurationMins": 10080}],
                        "routingWindow": "seven_day", "bindingWindow": "seven_day"}}
        readings = {"anthropic": None, "go": go}
        lines = dispatch_size.fact_lines({"jobs": [job]}, self.root, data=data, ladder=ladder,
                                         now=now, read=lambda name, _data=None: readings.get(name))
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("cheaper there: cheap-a", lines[0])
        self.assertIn("72%", lines[0])
        quiet = dispatch_size.fact_lines({"jobs": [job]}, self.root, data=data, ladder=ladder,
                                         now=now, read=lambda name, _data=None: None)
        self.assertEqual(quiet, [lines[0].split("; go-exec on go")[0]], quiet)

    def test_the_line_helper_is_one_string_or_none(self):
        self.file("a.md", 1)
        spec = {"jobs": [self.job("j", "a.md")]}
        self.assertEqual(dispatch_size.line(spec, self.root), dispatch_size.format_line(
            dispatch_size.dispatch_size(spec, self.root)))
        self.assertIsNone(dispatch_size.line({"jobs": []}, self.root))
        self.assertIsNone(dispatch_size.line("not a spec", self.root))


class HarnessLoadTests(Plant):
    """What the harness loads before a job's brief, read from the spend ledger's own `turn1ContextTokens` rows
    (`spend_ledger.iter_lines`), added to the fit so a job is judged on load plus reading against the point."""

    def rows(self, transport, kind, tokens, days_ago=0, tier="full"):
        import time
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))
        with open(self.ledger, "a", encoding="utf-8") as fh:
            for value in tokens:
                fh.write(json.dumps({"transport": transport, "turn1ContextTokens": value, "timestamp": stamp,
                                     "disclosureTier": tier, "toolProfile": {"kind": kind} if kind else {}}) + "\n")

    def sidecar(self, kind="author", **extra):
        return dict({"label": "j", "promptFile": self.file("brief.md", 1), "kind": kind}, **extra)

    def test_a_route_and_kind_with_enough_rows_loads_their_median(self):
        self.rows("anthropic", "author", [30000, 28000, 32000, 29000, 31000, 90000])
        load = dispatch_size.harness_load("anthropic", "author", sidecar=True)
        self.assertEqual((load["tokens"], load["n"]), (30500, 6))

    def test_an_anthropic_plan_variant_counts_toward_its_route(self):
        self.rows("anthropic", "author", [30000] * 3)
        self.rows("anthropic-secondary", "author", [30000] * 3)
        self.assertEqual(dispatch_size.harness_load("anthropic", "author", sidecar=True)["n"], 6)

    def test_only_the_newest_rows_of_a_group_count(self):
        self.rows("anthropic", "author", [90000] * 10, days_ago=2)
        self.rows("anthropic", "author", [30000] * dispatch_size.HARNESS_LOAD_ROWS, days_ago=1)
        self.assertEqual(dispatch_size.harness_load("anthropic", "author", sidecar=True)["tokens"], 30000)

    def test_rows_past_the_window_and_rows_with_no_kind_or_no_reading_count_nothing(self):
        self.rows("anthropic", "author", [90000] * 6, days_ago=dispatch_size.HARNESS_LOAD_DAYS + 2)
        self.rows("anthropic", None, [20000] * 6)
        self.rows("anthropic", "author", [None] * 6)
        self.assertIsNone(dispatch_size.harness_load("anthropic", "author", sidecar=True)["tokens"])

    def test_a_route_short_of_rows_takes_the_lightest_route_that_has_them_as_a_floor(self):
        self.rows("anthropic", "author", [30000] * 6)
        self.rows("codex", "author", [19000] * 6)
        self.rows("go", "author", [21000] * 3)
        load = dispatch_size.harness_load("go", "author", sidecar=True)
        self.assertEqual(load["tokens"], 19000)
        self.assertIn("floor", load["source"])

    def test_a_native_job_takes_the_floor_never_a_sidecar_route_median(self):
        self.rows("anthropic", "author", [30000] * 6)
        self.rows("codex", "author", [19000] * 6)
        load = dispatch_size.harness_load("anthropic", "author", sidecar=False)
        self.assertEqual(load["tokens"], 19000)

    def test_no_rows_for_the_kind_anywhere_is_unmeasured_never_a_guess(self):
        self.rows("anthropic", "scout", [10000] * 6)
        load = dispatch_size.harness_load("anthropic", "author", sidecar=True)
        self.assertEqual((load["tokens"], load["source"]), (None, None))
        self.assertIsNone(dispatch_size.harness_load("anthropic", None, sidecar=True)["tokens"])

    def test_rows_loaded_at_another_disclosure_tier_never_stand_for_the_kinds_current_load(self):
        # A scout loaded `bare` until A5 made it `full`: its old rows understate the chain it now loads.
        self.rows("anthropic", "scout", [11600] * 6, tier="bare")
        load = dispatch_size.harness_load("anthropic", "scout", sidecar=True)
        self.assertEqual((load["tokens"], load["source"]), (None, None))

    def test_a_kind_reads_only_its_current_tier_for_both_its_route_median_and_its_floor(self):
        self.rows("anthropic", "scout", [11600] * 6, tier="bare")
        self.rows("anthropic", "scout", [21000] * 5, tier="full")
        self.rows("codex", "scout", [8800] * 6, tier="bare")
        self.assertEqual(dispatch_size.harness_load("anthropic", "scout", sidecar=True)["tokens"], 21000)
        floor = dispatch_size.harness_load("go", "scout", sidecar=True)
        self.assertEqual((floor["tokens"], "floor" in floor["source"]), (21000, True))

    def test_a_kind_with_no_disclosure_in_its_row_reads_every_tier(self):
        self.rows("anthropic", "arm", [9000] * 3, tier="bare")
        self.rows("anthropic", "arm", [9000] * 3, tier="pointer")
        self.assertEqual(dispatch_size.harness_load("anthropic", "arm", sidecar=True)["n"], 6)

    def test_an_author_group_is_untouched_by_a_scouts_tier_rows(self):
        self.rows("anthropic", "scout", [11600] * 6, tier="bare")
        self.rows("anthropic", "author", [30000, 28000, 32000, 29000, 31000, 90000])
        self.assertEqual(dispatch_size.harness_load("anthropic", "author", sidecar=True)["tokens"], 30500)

    def test_a_missing_ledger_is_unmeasured(self):
        self.assertIsNone(dispatch_size.harness_load("anthropic", "author", sidecar=True)["tokens"])

    def test_the_fit_adds_the_load_to_the_reading_before_comparing_with_the_point(self):
        self.rows("anthropic", "author", [30000] * 6)
        import model_registry
        point = model_registry.compaction_point(model_registry.resolve("haiku", model_registry.load()))[0]
        fit = dispatch_size.fit_tokens("haiku", point - 20000, None, self.sidecar())
        self.assertEqual((fit["load"], fit["over"]), (30000, 10000))
        self.assertEqual(dispatch_size.fit_tokens("haiku", point - 40000, None, self.sidecar())["over"], 0)

    def test_a_job_with_no_load_data_is_fit_on_its_reading_and_says_the_load_is_unmeasured(self):
        fit = dispatch_size.fit_tokens("haiku", 1000, None, self.sidecar())
        self.assertEqual((fit["load"], fit["loadSource"]), (0, None))
        size = dispatch_size.dispatch_size([self.sidecar(model="haiku", effort="low")])
        self.assertIn("harness load unmeasured", dispatch_size.fit_lines(size)[0])

    def test_a_resident_seat_whose_reading_fits_but_whose_reading_and_load_do_not_is_refused(self):
        self.rows("anthropic", "reviewer", [31000] * 6)
        import model_registry
        point = model_registry.compaction_point(model_registry.resolve("haiku", model_registry.load()))[0]
        big = self.file("ctx.md", math.ceil((point - 20000) * dispatch_size.CHARS_PER_TOKEN / KB))
        job = self.sidecar(kind="reviewer", model="haiku", effort="low", contextFiles=[big])
        size = dispatch_size.dispatch_size([job])
        per = size["perJob"][0]
        self.assertLess(per["residentTokens"], point)
        self.assertEqual((per["load"], per["residentOver"] > 0), (31000, True))
        self.assertEqual(len(dispatch_size.over_window_lines(size)), 1)

    def test_the_ceiling_counts_the_load_too(self):
        self.rows("anthropic", "author", [30000] * 6)
        import model_registry
        point = model_registry.compaction_point(model_registry.resolve("haiku", model_registry.load()))[0]
        self.file("spans.py", math.ceil((point - 20000) * dispatch_size.CHARS_PER_TOKEN / KB))
        brief = "Edit `spans.py`.\n```task\n" + json.dumps({"tier": "executor", "check": ["true"],
                                                              "writeSet": ["spans.py"]}) + "\n```\n"
        job = {"label": "c", "prompt": brief, "kind": "author", "model": "haiku", "effort": "low", "workdir": self.root}
        per = dispatch_size.dispatch_size([job], root=self.root)["perJob"][0]
        self.assertEqual((per["over"], per["ceilingOver"]), (0, True))


class CompactionPointTests(Plant):
    """A job's fit counts the files its brief names, against the input point it compacts at, for its own flags."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.login, "settings.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"autoCompactWindow": 500000}, fh)

    @staticmethod
    def anthropic_point(alias, **flags):
        import model_registry
        data = model_registry.load()
        entry = model_registry.resolve(alias, data)
        auto = model_registry.anthropic_window(entry, **flags)["autoCompactWindow"]
        return auto - 20000 - 13000

    def named_job(self, label, kb, **extra):
        big = self.file("named-%s.md" % label, kb)
        job = {"label": label, "prompt": "Read `%s` and report." % big, "model": "opus", "effort": "high"}
        job.update(extra)
        return job, big

    def test_the_fit_counts_a_file_the_brief_names(self):
        job, _big = self.named_job("named", 400)
        per = dispatch_size.dispatch_size([job])["perJob"][0]
        self.assertGreater(per["tokens"], 400 * KB / dispatch_size.CHARS_PER_TOKEN)
        self.assertEqual(per["tokens"], per["readingTokens"])

    def test_a_named_image_counts_its_visual_token_ceiling_not_its_bytes(self):
        photo = self.file("screen.jpg", 400)
        job = {"label": "img", "prompt": "Read `%s` and report." % photo, "model": "opus", "effort": "high"}
        per = dispatch_size.dispatch_size([job])["perJob"][0]
        self.assertLessEqual(per["tokens"], dispatch_size.IMAGE_TOKENS + 100)
        self.assertGreaterEqual(per["tokens"], dispatch_size.IMAGE_TOKENS)

    def test_no_job_field_lifts_the_login_cap(self):
        import model_registry
        entry = model_registry.resolve("opus", model_registry.load())
        plain = model_registry.compaction_point(entry, {"transport": "anthropic"})
        self.assertEqual(plain, (467000, "auto-compact"))
        self.assertEqual(model_registry.compaction_point(entry, {"transport": "anthropic", "window": "full"}), plain)

    def test_an_anthropic_job_is_measured_against_the_login_capped_point_whatever_its_marks(self):
        job, _big = self.named_job("res", 700, resident=True, kind="author")
        job["prompt"] += " And `%s`." % self.file("named-res2.md", 700)
        per = dispatch_size.dispatch_size([job])["perJob"][0]
        self.assertEqual(per["usable"], self.anthropic_point("opus"))
        self.assertEqual(per["usable"], 467000)
        self.assertEqual(per["over"], per["tokens"] - per["usable"])
        self.assertGreater(per["over"], 0)

    def test_a_review_shaped_job_and_a_plain_job_share_one_point(self):
        review, _ = self.named_job("rev", 1, kind="reviewer")
        plain, _ = self.named_job("plain", 1, kind="author")
        per = dispatch_size.dispatch_size([review, plain])["perJob"]
        self.assertEqual(per[0]["usable"], self.anthropic_point("opus"))
        self.assertEqual(per[1]["usable"], per[0]["usable"])

    def test_haiku_keeps_its_own_operating_point_under_the_login_cap(self):
        job, _ = self.named_job("hai", 1, kind="reviewer")
        job["model"] = "haiku"
        self.assertEqual(dispatch_size.dispatch_size([job])["perJob"][0]["usable"], self.anthropic_point("haiku"))
        self.assertLess(self.anthropic_point("haiku"), 467000)

    def test_another_transport_keeps_its_current_point(self):
        import model_registry
        sol = model_registry.compaction_point(model_registry.resolve("sol", model_registry.load()))[0]
        job, _big = self.named_job("sol", 1, resident=True)
        job["model"] = "sol"
        self.assertEqual(dispatch_size.dispatch_size([job])["perJob"][0]["usable"], sol)

    def test_the_line_prints_the_share_and_the_point_it_used(self):
        job, _big = self.named_job("res", 700, resident=True)
        line = dispatch_size.format_line(dispatch_size.dispatch_size([job]))
        self.assertRegex(line, r"heaviest reading \d+% of its compaction point, 467000 tokens")
        self.assertNotIn("of its window", line)

    def test_a_named_path_that_resolves_to_no_file_is_counted_and_never_read(self):
        prompt = ("Read `V/Notes/Design.md`, `V/Notes/Other.md:12-20` and `%s`. Not paths: `x/*.py`, `<dir>/a.md`, "
                  "`https://host/a/b.md`, `plain`, `a b/c.md`." % self.file("real.md", 1))
        size = dispatch_size.dispatch_size([{"label": "j", "prompt": prompt, "model": "opus"}])
        self.assertEqual(size["perJob"][0]["unresolved"], 2)
        self.assertEqual(size["unresolved"], ["V/Notes/Design.md", "V/Notes/Other.md"])
        self.assertEqual(size["namedKb"], 1)
        self.assertIn("2 named paths not resolved, counted 0: V/Notes/Design.md, V/Notes/Other.md",
                      dispatch_size.format_line(size))

    def test_no_unresolved_clause_when_every_named_path_resolves(self):
        job, _big = self.named_job("ok", 1)
        self.assertNotIn("not resolved", dispatch_size.format_line(dispatch_size.dispatch_size([job])))

    def test_fit_lines_state_the_share_the_point_and_the_unresolved_count_per_pinned_job(self):
        job, _big = self.named_job("res", 700, resident=True)
        job["prompt"] += " Also `V/a/b.md`."
        unpinned = {"label": "none", "prompt": "no pin"}
        lines = dispatch_size.fit_lines(dispatch_size.dispatch_size([job, unpinned]))
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^fit: `res` \(opus\) reading \d+ tokens \(\d+ if every named file is read whole\), \d+% of its "
                                   r"compaction point, 467000 tokens \(auto-compact\); harness load unmeasured[^;]*; largest: .*named-res\.md \d+k; "
                                   r"1 named path not resolved, counted 0$")

    def test_the_lens_seat_overage_inherits_the_fix_through_the_shared_fit(self):
        import lens_briefs
        import model_registry
        data = model_registry.load()
        point = model_registry.compaction_point(model_registry.resolve("haiku", data))[0]
        big = self.file("named-big.md", math.ceil(point * dispatch_size.CHARS_PER_TOKEN / KB) + 40)
        context = self.file("ctx.md", 1)
        refusal = lens_briefs.seat_overage(key="k", token="haiku", body="Read the list.", context_path=context,
                                           entries=[{"path": big, "lines": 10}], data=data)
        self.assertIsNotNone(refusal)


class BucketTests(Plant):
    """A named file is read whole only when nothing in the brief says otherwise: a writeSet file or a `path:N-M`
    citation is a span, counted in the upper number only."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.login, "settings.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"autoCompactWindow": 500000}, fh)

    def sized(self, body, write_set=(), **extra):
        brief = body + "\n```task\n" + json.dumps({"tier": "executor", "check": ["true"],
                                                     "writeSet": list(write_set)}) + "\n```\n"
        job = {"label": "j", "prompt": brief, "model": "opus", "effort": "high"}
        job.update(extra)
        return dispatch_size.dispatch_size([job], root=self.root)

    def test_a_brief_whose_writeset_covers_its_named_files_counts_them_in_the_upper_number_only(self):
        self.file("a.py", 100)
        self.file("b.py", 100)
        per = self.sized("Edit `a.py` and `b.py`.", ["a.py", "b.py"])["perJob"][0]
        self.assertLess(per["lowerTokens"], 1000)
        self.assertEqual(per["readingTokens"], per["lowerTokens"])
        self.assertGreaterEqual(per["upperTokens"] - per["lowerTokens"], 2 * 100 * KB // 2.5 - 2)

    def test_a_bare_named_file_outside_the_writeset_counts_in_the_lower_number(self):
        self.file("ref.md", 100)
        self.file("a.py", 100)
        per = self.sized("Read `ref.md`; edit `a.py`.", ["a.py"])["perJob"][0]
        self.assertGreaterEqual(per["lowerTokens"], 100 * KB / 2.5)
        self.assertLess(per["lowerTokens"], 1.5 * 100 * KB / 2.5)
        self.assertGreater(per["upperTokens"], per["lowerTokens"] + 100 * KB / 2.5 - 2)

    def test_a_line_cited_file_counts_in_the_upper_number_only_unless_also_cited_bare(self):
        self.file("c.md", 100)
        per = self.sized("See `c.md:10-20`.")["perJob"][0]
        self.assertLess(per["lowerTokens"], 1000)
        self.assertGreater(per["upperTokens"], 100 * KB / 2.5)
        both = self.sized("See `c.md:10-20` and `c.md`.")["perJob"][0]
        self.assertGreater(both["lowerTokens"], 100 * KB / 2.5)

    def test_a_named_file_over_the_point_counts_zero_and_is_a_search_source(self):
        big = self.file("huge.jsonl", 1300)
        per = self.sized("Search `huge.jsonl` for the rows.")["perJob"][0]
        self.assertLess(per["upperTokens"], 1000)
        self.assertEqual([os.path.basename(p) for p in per["searchSources"]], ["huge.jsonl"])
        self.assertEqual(per["searchSources"], [big])

    def test_a_named_file_the_job_lists_as_a_search_source_counts_zero_in_both_numbers(self):
        drawing = self.file("drawing.html", 300)
        self.file("ref.md", 10)
        per = self.sized("Grep `drawing.html` for a screen; read `ref.md`.", searchSources=["drawing.html"])["perJob"][0]
        self.assertLess(per["upperTokens"], 10 * KB / 2.5 + 1000)
        self.assertEqual(per["searchSources"], [drawing])
        unlisted = self.sized("Grep `drawing.html` for a screen; read `ref.md`.")["perJob"][0]
        self.assertGreater(unlisted["lowerTokens"], 300 * KB / 2.5)
        self.assertEqual(unlisted["searchSources"], [])

    def test_the_fit_line_names_the_largest_files_and_says_ceiling_over_when_only_the_upper_number_is_over(self):
        for name in ("m1.py", "m2.py", "m3.py"):
            self.file(name, 400)
        size = self.sized("Edit `m1.py`, `m2.py`, `m3.py`.", ["m1.py", "m2.py", "m3.py"])
        per = size["perJob"][0]
        self.assertEqual(len(per["largest"]), 3)
        text = dispatch_size.fit_lines(size)[0]
        self.assertRegex(text, r"reading \d+ tokens \(\d+ if every named file is read whole\), <?\d+%? of its compaction")
        self.assertIn("largest: ", text)
        self.assertIn("m1.py 164k", text)
        self.assertIn("ceiling over", text)
        self.assertNotIn("over its point", text)

    def test_a_job_whose_lower_number_is_over_is_named_over_not_ceiling_over(self):
        self.file("r1.md", 600)
        self.file("r2.md", 600)
        size = self.sized("Read `r1.md` and `r2.md`.")
        text = dispatch_size.fit_lines(size)[0]
        self.assertIn("over its point", text)
        self.assertNotIn("ceiling over", text)


class DenyTests(Plant):
    """A path a brief names, and a job's workdir, are reported when a deny rule of the effective settings hits them."""

    def settings(self, *deny, name="settings.json", root=None):
        folder = os.path.join(root or self.root, ".claude")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"permissions": {"deny": list(deny)}}, fh)

    def planted(self, relative, kb=1):
        path = os.path.join(self.root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x" * (kb * KB))
        return path.replace("\\", "/")

    def facts(self, job):
        return dispatch_size.denied_lines(dispatch_size.dispatch_size([job], root=self.root))

    def test_a_denied_path_is_named_with_its_tool_and_rule_text(self):
        self.settings("Read(.claude/worktrees/**)", "Grep(.claude/worktrees/**)")
        target = self.planted(".claude/worktrees/ui-sit1/design/live/build.mjs")
        facts = self.facts(self.job("j", "p.md", must=[target]))
        self.assertEqual(len(facts), 1)
        self.assertIn("denied: %s (Read, rule \"Read(.claude/worktrees/**)\")" % target, facts[0])
        self.assertIn("`j`", facts[0])

    def test_the_fit_line_of_a_pinned_job_carries_the_denial(self):
        self.settings("Read(.claude/worktrees/**)")
        target = self.planted(".claude/worktrees/w/a.md")
        size = dispatch_size.dispatch_size([self.job("j", "p.md", must=[target])], root=self.root)
        text = dispatch_size.fit_lines(size)[0]
        self.assertIn("denied: %s (Read, rule" % target, text)

    def test_a_path_no_rule_matches_stays_unmarked(self):
        self.settings("Read(.claude/worktrees/**)", "Edit(/secrets/**)")
        target = self.planted(".claude/scratch/a.md")
        job = self.job("j", "p.md", must=[target])
        size = dispatch_size.dispatch_size([job], root=self.root)
        self.assertEqual(dispatch_size.denied_lines(size), [])
        self.assertNotIn("denied", " ".join(dispatch_size.fit_lines(size)))
        self.assertNotIn("settings unreadable", dispatch_size.format_line(size))

    def test_a_workflow_job_is_anchored_at_the_project_root_and_says_so(self):
        self.settings("Read(.claude/worktrees/**)")
        target = self.planted(".claude/worktrees/ui-sit1/design/a.md")
        fact = self.facts(self.job("flow", "p.md", must=[target]))[0]
        self.assertIn("%s (Read, rule \"Read(.claude/worktrees/**)\") against project root" % target, fact)

    def test_a_sidecar_job_whose_workdir_sits_in_the_denied_folder_is_not_flagged_by_that_rule(self):
        self.settings("Read(.claude/worktrees/**)")
        workdir = os.path.join(self.root, ".claude", "worktrees", "ui-sit1")
        inside = self.planted(".claude/worktrees/ui-sit1/design/a.md")
        job = {"label": "side", "promptFile": self.file("p.md", 1), "workdir": workdir,
               "mustRead": [{"path": inside, "lines": 10}]}
        os.makedirs(workdir, exist_ok=True)
        self.assertEqual(self.facts(job), [])

    def test_a_sidecar_job_is_anchored_at_its_workdir_and_says_so(self):
        self.settings("Read(vault/notes/**)")
        inside = self.planted("sub/vault/notes/a.md")
        beside = self.planted("vault/notes/b.md")
        job = {"label": "side", "promptFile": self.file("p.md", 1), "workdir": os.path.join(self.root, "sub"),
               "mustRead": [{"path": inside, "lines": 10}, {"path": beside, "lines": 10}]}
        fact = self.facts(job)[0]
        self.assertIn("%s (Read, rule \"Read(vault/notes/**)\") against workdir" % inside, fact)
        self.assertNotIn(beside, fact)

    def test_a_workdir_an_absolute_rule_denies_is_named_as_the_workdir(self):
        folder = os.path.join(self.root, "closed").replace("\\", "/")
        self.settings("Edit(//" + folder.replace(":", "").lstrip("/") + "/**)")
        workdir = os.path.join(self.root, "closed", "w")
        os.makedirs(workdir)
        job = {"label": "w", "promptFile": self.file("p.md", 1), "workdir": workdir}
        self.assertIn("(workdir; Edit, rule", self.facts(job)[0])

    def test_a_named_path_that_does_not_exist_is_still_tested(self):
        self.settings("Read(.claude/worktrees/**)")
        gone = os.path.join(self.root, ".claude", "worktrees", "gone", "design", "a.md").replace("\\", "/")
        job = {"label": "j", "promptPath": self.file("p.md", 1), "model": "opus", "effort": "high",
               "prompt": "Read `%s` first." % gone}
        self.assertFalse(os.path.exists(gone))
        fact = self.facts(job)[0]
        self.assertIn("denied: %s (Read, rule \"Read(.claude/worktrees/**)\") against project root" % gone, fact)

    def test_a_directory_a_brief_file_names_is_tested_though_it_has_no_extension_and_does_not_exist(self):
        self.settings("Read(.claude/worktrees/**)")
        gone = os.path.join(self.root, ".claude", "worktrees", "ui-sit1").replace("\\", "/")
        brief = self.text("common.md", "- Worktree `%s`, branch `ui-sit1`. Edit only files under it.\n" % gone)
        job = {"label": "j", "promptPath": brief, "model": "opus", "effort": "high"}
        self.assertFalse(os.path.exists(gone))
        self.assertIn("denied: %s (Read, rule" % gone, self.facts(job)[0])

    def test_a_local_settings_rule_and_a_user_settings_rule_count(self):
        self.settings("Read(a_dir/**)", name="settings.local.json")
        with open(os.path.join(self.login, "settings.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"permissions": {"deny": ["Read(//" + self.root.replace("\\", "/").replace(":", "").lstrip("/") + "/b_dir/**)"]}}, fh)
        one = self.planted("a_dir/x.md")
        two = self.planted("b_dir/x.md")
        facts = self.facts(self.job("j", "p.md", must=[one, two]))[0]
        self.assertIn("denied: %s" % one, facts)
        self.assertIn("denied: %s" % two, facts)

    def test_an_unreadable_settings_file_is_named_not_read_as_no_rules(self):
        folder = os.path.join(self.root, ".claude")
        os.makedirs(folder)
        with open(os.path.join(folder, "settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        size = dispatch_size.dispatch_size([self.job("j", "p.md")], root=self.root)
        self.assertIn("settings unreadable, deny rules not checked: settings.json (unparseable", dispatch_size.format_line(size))

    def test_a_missing_settings_file_is_no_rules_and_says_nothing(self):
        size = dispatch_size.dispatch_size([self.job("j", "p.md")], root=self.root)
        self.assertNotIn("settings unreadable", dispatch_size.format_line(size))


class PreflightTests(Plant):
    """The preflight states every unmet launch precondition in the output `/delegate` already reads before a launch."""

    def setUp(self):
        super().setUp()
        state = os.path.join(self.root, "state")
        os.makedirs(state, exist_ok=True)
        saved = os.environ.get("HARNESS_HOOK_STATE_DIR")
        os.environ["HARNESS_HOOK_STATE_DIR"] = state
        self.addCleanup(lambda: os.environ.pop("HARNESS_HOOK_STATE_DIR", None) if saved is None
                        else os.environ.__setitem__("HARNESS_HOOK_STATE_DIR", saved))
        # The skill-credit reader resolves skills from CLAUDE_PROJECT_DIR; the plant owns it so the runner's checkout never leaks in.
        project = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = self.root
        self.addCleanup(lambda: os.environ.pop("CLAUDE_PROJECT_DIR", None) if project is None
                        else os.environ.__setitem__("CLAUDE_PROJECT_DIR", project))
        self.sid = "pf000001"
        self.transcript = self.text("t.jsonl", "")

    def lines(self, spec, **kwargs):
        kwargs.setdefault("session_id", self.sid)
        kwargs.setdefault("transcript_path", self.transcript)
        return dispatch_size.preflight_lines(spec, root=self.root, **kwargs)

    def workflow_job(self, body):
        return {"label": "j1", "promptPath": self.text("p.md", body), "model": "opus", "effort": "high",
                "tier": "executor", "kind": "author"}

    def test_a_job_the_fan_out_refuses_by_kind_is_named_in_its_own_text(self):
        brief = self.text("brief.md", "Write the report to `reports/out.md`.\n")
        job = {"label": "codify-derive", "promptFile": brief, "kind": "lens", "model": "opus",
               "effort": "high", "tier": "executor", "tools": "+Write"}
        lines = self.lines([job])
        self.assertTrue(any("job codify-derive: Write on kind 'lens': a job that writes is an author: "
                            "use kind author" in line for line in lines), lines)

    def test_a_job_field_the_fan_out_refuses_is_unmet_never_clean(self):
        """`plan_job` refuses a retired `window`, a bad `cacheTtl`, a string where a list is due and a conflicting
        `rows` + `alias` from the job's fields alone; the preflight runs those same predicates, so a clean
        preflight never precedes that refusal."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        brief = self.text("brief2.md", "Do the work.\n")
        base = {"label": "j1", "promptFile": brief, "kind": "author", "model": "opus", "effort": "high",
                "tier": "executor"}
        for extra, token in (({"window": "full"}, "`window` is retired"),
                             ({"cacheTtl": "forever"}, "`cacheTtl` must be"),
                             ({"contextFiles": "a.md"}, "`contextFiles` must be a list"),
                             ({"excludePlans": "x"}, "`excludePlans` must be a list"),
                             ({"rows": {"opus": "high"}}, "`rows` or `alias` + `effort`")):
            lines = self.lines([dict(base, **extra)])
            unmet = [line for line in lines if line.startswith("preflight: unmet:") and token in line]
            self.assertEqual(len(unmet), 1, (extra, lines))
            self.assertIn("job j1", unmet[0], unmet[0])
            self.assertFalse(any("preflight: clean" in line for line in lines), lines)
        self.assertEqual(self.lines([dict(base, cacheTtl="1h")]), ["preflight: clean: every launch precondition is met"])

    def test_a_malformed_pin_basis_is_refused_naming_its_field_and_accepted_shape(self):
        """A job's `pick`/`spec` is one of the strings `slice_pins.md` names; a dict once reached the size
        reading and crashed it with a traceback instead of the preflight naming the field."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        for field, value, shape in (("spec", {"pick": "cell"}, "tight|judged"),
                                    ("pick", ["cell"], "cell|room|owner|default"),
                                    ("spec", "wide", "tight|judged")):
            job = dict(self.workflow_job("Do the work."), **{field: value})
            lines = self.lines([job])
            unmet = [line for line in lines if line.startswith("preflight: unmet:") and field in line]
            self.assertEqual(len(unmet), 1, (field, lines))
            self.assertIn("job j1", unmet[0], unmet[0])
            self.assertIn(shape, unmet[0], unmet[0])
            self.assertFalse(any("preflight: clean" in line for line in lines), lines)
            result = self.cli({"jobs": [job]})
            self.assertNotIn("Traceback", result.stderr, result.stderr)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_a_valid_pin_basis_adds_no_preflight_line(self):
        """A `pick` and `spec` the enums name are a record, not a refusal: the preflight stays clean."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        job = dict(self.workflow_job("Do the work."), pick="cell", spec="tight")
        self.assertEqual(self.lines([job]), ["preflight: clean: every launch precondition is met"])

    def test_no_reading_of_a_brief_s_prose_adds_a_preflight_line(self):
        """Whether a brief orders or forbids a run is prose the preflight cannot judge (it once read "Do not run
        `harness_tests.py --close`" as an order); the author rail carries that rule to every delegate."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        for text in ("Do not run `harness_tests.py --close` or `--all`.",
                     "run python3 .claude/scripts/harness_tests.py --close",
                     "pwsh -File .claude/scripts/regression_gate.ps1 -Detach"):
            self.assertEqual(self.lines([self.workflow_job("Fix it.\n" + text + "\n")]),
                             ["preflight: clean: every launch precondition is met"], text)

    def test_a_pinned_tier_without_a_ladder_slice_is_named_with_its_load_step(self):
        lines = self.lines([self.workflow_job("Do the work.")])
        tier = [line for line in lines if "tier `executor`" in line]
        self.assertEqual(len(tier), 1, lines)
        self.assertIn("for-role executor", tier[0])

    def _ladder(self, body):
        os.makedirs(os.path.join(self.root, ".claude", "reference"), exist_ok=True)
        return self.text(os.path.join(".claude", "reference", "model_ladder_evidence.md"), body)

    def test_a_slice_cut_from_ladder_text_since_changed_is_named_stale_not_absent(self):
        """A ladder edit voids every credit cut from the old text (`model_ladder_gate.ladder_credit`); the line
        names that cause, since "no ladder slice" after a load sends the agent looking for a load that happened."""
        import _hook_state
        import model_ladder_gate
        self._ladder("# ladder\nold rows\n")
        _hook_state.mark_ladder_slice(self.sid, ["executor"], digest=model_ladder_gate.ladder_digest({"cwd": self.root}))
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        job = self.workflow_job("Do the work.")
        self.assertEqual(self.lines([job]), ["preflight: clean: every launch precondition is met"])
        self._ladder("# ladder\nnew rows\n")
        tier = [line for line in self.lines([job]) if "tier `executor`" in line]
        self.assertEqual(len(tier), 1, tier)
        self.assertIn(model_ladder_gate.STALE_CAUSE, tier[0])
        self.assertIn("for-role executor", tier[0])

    def test_the_ladder_credit_is_read_under_the_preflight_root_not_the_cwd(self):
        """With no CLAUDE_PROJECT_DIR the credit once resolved the ladder from the cwd, so a run from any other
        directory read every recorded load as cut from other text."""
        import _hook_state
        import model_ladder_gate
        self._ladder("# ladder\nrows\n")
        _hook_state.mark_ladder_slice(self.sid, ["executor"], digest=model_ladder_gate.ladder_digest({"cwd": self.root}))
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        elsewhere = tempfile.mkdtemp(prefix="pf_cwd_")
        here = os.getcwd()
        os.chdir(elsewhere)
        self.addCleanup(os.chdir, here)
        self.assertEqual(self.lines([self.workflow_job("Do the work.")]),
                         ["preflight: clean: every launch precondition is met"])

    def test_a_skill_path_in_a_brief_demands_nothing_only_orchestration_is_required(self):
        """Which skills a job follows is the briefing session's judgment; no reading of the brief's prose decides it."""
        os.makedirs(os.path.join(self.root, ".claude", "skills", "product_ui"))
        self.text(".claude/skills/product_ui/SKILL.md", "# Product UI\n")
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        self.assertEqual(self.lines([self.workflow_job("Follow `.claude/skills/product_ui/SKILL.md`.")]),
                         ["preflight: clean: every launch precondition is met"])

    def test_a_brief_ordering_a_wide_run_is_a_preflight_unmet_line(self):
        os.makedirs(os.path.join(self.root, ".claude", "skills", "project_subsystems"))
        self.text(".claude/skills/project_subsystems/adaptation.json", json.dumps(
            {"gate_cadence": {"wide": [{"runner": r"npm\s+run\s+verify\b", "exempt": [], "narrow": None}]}}))
        lines = self.lines([self.workflow_job("Run `npm run verify` before you finish.\n")])
        unmet = [line for line in lines if line.startswith("preflight: unmet:") and "npm run verify" in line]
        self.assertEqual(len(unmet), 1, lines)
        self.assertIn("gate", unmet[0])
        gate = dict(self.workflow_job("Run `npm run verify` before you finish.\n"), kind="gate")
        self.assertFalse(any("npm run verify" in line for line in self.lines([gate])))

    def cli(self, spec):
        path = os.path.join(self.root, "jobs.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID=self.sid)
        return subprocess.run([sys.executable, os.path.join(TOOLS, "dispatch_size.py"), path],
                              capture_output=True, text=True, encoding="utf-8", env=env, cwd=self.root,
                              stdin=subprocess.DEVNULL)

    def test_the_cli_exits_nonzero_when_the_preflight_reports_an_unmet_precondition(self):
        """The CLI's exit 1 is the go a caller chaining `sidecar_fanout.py` behind it reads; `/delegate` runs the
        two as separate calls and reads the printed lines, so an unmet line is visible there whatever the exit
        code."""
        os.makedirs(os.path.join(self.root, ".claude", "skills", "project_subsystems"))
        self.text(".claude/skills/project_subsystems/adaptation.json", json.dumps(
            {"gate_cadence": {"wide": [{"runner": r"npm\s+run\s+verify\b", "exempt": [], "narrow": None}]}}))
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        result = self.cli({"jobs": [self.workflow_job("Run `npm run verify` before you finish.\n")]})
        self.assertIn("preflight: unmet:", result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_the_cli_exits_zero_when_no_precondition_is_unmet(self):
        """A `preflight: not checked` line is a check that could not run, never an unmet precondition: it does
        not refuse the launch."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        result = self.cli({"jobs": [self.workflow_job("Do the work.")]})
        self.assertNotIn("preflight: unmet:", result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_credited_tier_and_skill_report_one_clean_line(self):
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        self.assertEqual(self.lines([self.workflow_job("Do the work.")]),
                         ["preflight: clean: every launch precondition is met"])

    def test_a_launcher_read_file_that_does_not_resolve_is_unmet_never_clean(self):
        """The launcher opens a job's brief, `contextPath`, `contextFiles` and `schemaFile` from the launching
        checkout; one that does not resolve there fails after launch, so the preflight names it first."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        elsewhere = tempfile.mkdtemp(prefix="pf_elsewhere_")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with open(os.path.join(elsewhere, "reader-common.md"), "w", encoding="utf-8") as fh:
            fh.write("context\n")
        job = self.workflow_job("Do the work.")
        for field, where in (("contextPath", "spec"), ("contextFiles", "job"), ("promptPath", "job")):
            spec = {"jobs": [dict(job)]}
            if field == "contextPath":
                spec["contextPath"] = "ui-lock/reader-common.md"
            elif field == "contextFiles":
                spec["jobs"][0]["contextFiles"] = ["ui-lock/reader-common.md"]
            else:
                spec["jobs"][0]["promptPath"] = "ui-lock/brief.md"
            lines = self.lines(spec)
            self.assertFalse(any("preflight: clean" in line for line in lines), (field, lines))
            hit = [line for line in lines if "ui-lock/" in line]
            self.assertEqual(len(hit), 1, (field, lines))
            for part in ("preflight: unmet:", "job j1", field, "resolved against", self.root.replace("\\", "/")):
                self.assertIn(part, hit[0], (field, hit))

    def test_a_named_path_a_brief_mentions_stays_a_note_not_unmet(self):
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        self.assertEqual(self.lines([self.workflow_job("Write the report to `reports/out.md`.")]),
                         ["preflight: clean: every launch precondition is met"])

    def over_window_job(self, kind):
        """A job whose reading is past its pin's usable window, for `kind`."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        job = dict(self.workflow_job("Do the work."), kind=kind)
        point = dispatch_size.dispatch_size([job], self.root)["perJob"][0]["usable"]  # the point depends on the login
        job["promptPath"] = self.file("big.md", math.ceil(point * dispatch_size.CHARS_PER_TOKEN / KB) + 40)
        return job, point

    def test_a_review_seat_past_its_window_is_unmet_and_never_clean(self):
        """2026-11-10: a job measured 523,580 tokens over a 467,000-token window and the preflight still printed
        `clean`, the launch's go. A review seat keeps one subject resident, so the launch refuses it and the
        preflight says so."""
        job, point = self.over_window_job("reviewer")
        lines = self.lines([job])
        hit = [line for line in lines if line.startswith("preflight: unmet:") and "past its usable window" in line]
        self.assertEqual(len(hit), 1, lines)
        self.assertIn("`j1`", hit[0])
        self.assertIn(str(point), hit[0])
        self.assertRegex(hit[0], r"reading \d+ tokens")
        self.assertFalse(any(line.startswith("preflight: clean") for line in lines), lines)

    def test_a_resident_seat_past_its_point_is_refused_even_carrying_window_full(self):
        job, _point = self.over_window_job("lens")
        job["window"] = "full"
        self.assertEqual(len(dispatch_size.over_window_lines(dispatch_size.dispatch_size([job], self.root))), 1)

    def named_only_reviewer(self):
        """A reviewer whose brief only names two files that together pass its window; neither is listed or inlined."""
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        job = dict(self.workflow_job("Search"), kind="reviewer")
        point = dispatch_size.dispatch_size([job], self.root)["perJob"][0]["usable"]
        half_kb = math.ceil(point * dispatch_size.CHARS_PER_TOKEN * 0.6 / KB)
        first, second = self.file("named-a.md", half_kb), self.file("named-b.md", half_kb)
        job["promptPath"] = self.text("named_brief.md", "Search `%s` and `%s` for the claim." % (first, second))
        return job, point

    def test_a_reviewer_whose_brief_only_names_files_past_its_window_is_not_refused(self):
        """2026-11-22: naming files is a pointer; the brief decides what is read. The overage stays a fact on the
        fit line and the launch is not refused."""
        job, _point = self.named_only_reviewer()
        size = dispatch_size.dispatch_size([job], self.root)
        self.assertGreater(size["perJob"][0]["over"], 0)
        self.assertEqual(dispatch_size.over_window_lines(size), [])
        lines = self.lines([job])
        self.assertFalse([line for line in lines if "past its usable window" in line], lines)
        self.assertTrue([line for line in dispatch_size.fit_lines(size) if "over its point" in line])

    def test_a_review_seat_whose_body_only_names_files_past_its_window_is_not_refused(self):
        import lens_briefs
        import model_registry
        data = model_registry.load()
        point = model_registry.compaction_point(model_registry.resolve("haiku", data))[0]
        half_kb = math.ceil(point * dispatch_size.CHARS_PER_TOKEN * 0.6 / KB)
        body = "Search `%s` and `%s`." % (self.file("seat-a.md", half_kb), self.file("seat-b.md", half_kb))
        self.assertIsNone(lens_briefs.seat_overage(key="k", token="haiku", body=body, context_path=self.file("sctx.md", 1),
                                                   entries=[], data=data))

    def test_an_author_past_its_window_launches_and_its_fit_line_states_the_overage(self):
        """A lane of independent items compacts and carries on (`reference/model_ladder_evidence.md` §Compaction),
        so an author over its window is a fact on the fit line, not an unmet launch precondition."""
        import sidecar_fanout
        job, point = self.over_window_job("author")
        lines = self.lines([job])
        self.assertFalse([line for line in lines if line.startswith("preflight: unmet:")], lines)
        fit = [line for line in dispatch_size.fit_lines(dispatch_size.dispatch_size([job], self.root)) if "`j1`" in line]
        self.assertEqual(len(fit), 1, fit)
        self.assertIn("over its point", fit[0])
        self.assertIsNone(sidecar_fanout.over_window_refusal(dispatch_size.dispatch_size([job], self.root)))

    def test_a_job_that_fits_its_window_stays_clean(self):
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        job = self.workflow_job("Do the work.")
        job["promptPath"] = self.file("fits.md", 40)
        self.assertEqual(self.lines([job]), ["preflight: clean: every launch precondition is met"])

    def test_a_project_without_the_ui_round_tool_stays_clean(self):
        import _hook_state
        _hook_state.mark_ladder_slice(self.sid, ["executor"])
        _hook_state.mark_skill_in_context(self.sid, "orchestration")
        saved = sys.modules.get("ui_round")
        sys.modules["ui_round"] = None  # the import raises ModuleNotFoundError, as in a consumer without the file
        self.addCleanup(lambda: sys.modules.__setitem__("ui_round", saved) if saved is not None
                        else sys.modules.pop("ui_round", None))
        self.assertEqual(self.lines([self.workflow_job("Do the work.")]),
                         ["preflight: clean: every launch precondition is met"])

    def test_without_a_session_id_the_credit_checks_are_not_checked_never_clean(self):
        saved = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.addCleanup(lambda: os.environ.__setitem__("CLAUDE_CODE_SESSION_ID", saved) if saved else None)
        lines = self.lines([self.workflow_job("Do the work.")], session_id="")
        self.assertTrue(any(line.startswith("preflight: not checked: ladder and skill credit") for line in lines),
                        lines)
        self.assertFalse(any(line.startswith("preflight: clean") for line in lines), lines)


class AttributionTests(Plant):
    BARE = "The owners' map rule: ticking or finishing a task never repacks the map (src/features/map/focus.ts line 178)."

    def flags(self, body):
        job = {"label": "j1", "promptPath": self.text("p.md", body), "model": "opus", "effort": "high"}
        return dispatch_size.attribution_lines([job], root=self.root)

    def test_a_decision_attributed_to_the_owners_without_a_source_id_is_flagged(self):
        lines = self.flags(self.BARE + "\n")
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("advisory: "), lines)
        self.assertIn("j1", lines[0])

    def test_the_same_sentence_with_an_owner_source_id_is_not_flagged(self):
        for source in ("(U16365)", "(thread M9c4f2a10-0042)", "[2026-10-26 decision playtest-mvp2]"):
            body = self.BARE.replace("(src/features/map/focus.ts line 178)", source)
            self.assertEqual(self.flags(body + "\n"), [], source)

    def test_an_id_in_another_sentence_does_not_cover_the_attribution(self):
        self.assertEqual(len(self.flags("See U16365. " + self.BARE + "\n")), 1)

    def test_a_sentence_with_no_attribution_is_not_flagged(self):
        self.assertEqual(self.flags("Decided from facts in the job: the brief sizes the read.\nLock the file first.\n"), [])

    def test_main_prints_the_advisory_and_still_exits_zero(self):
        path = os.path.join(self.root, "jobs.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"jobs": [{"label": "j1", "promptPath": self.text("p.md", self.BARE + "\n"),
                                 "model": "opus", "effort": "high"}]}, fh)
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=self.root)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        done = subprocess.run([sys.executable, os.path.join(TOOLS, "dispatch_size.py"), path], capture_output=True,
                              text=True, encoding="utf-8", stdin=subprocess.DEVNULL, cwd=self.root, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(any(line.startswith("advisory: ") for line in done.stdout.splitlines()), done.stdout)


if __name__ == "__main__":
    unittest.main()
