"""Proof cases for hooks/_dispatch_pins.py — the Workflow/Agent pin parser shared by the provider guard
and the dispatch table: each engine's effort default, the review_fanout model default, kind/railTier
passthrough, and a script read with its JS comments blanked."""
import os
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
sys.path.insert(0, HOOKS)


def pins():
    import _dispatch_pins
    return _dispatch_pins


def wf(script_path, args):
    return {"tool_name": "Workflow", "tool_input": {"scriptPath": script_path, "args": args}}


def by_label(jobs):
    return {j["label"]: j for j in jobs}


# A planted roster: host transport `host`, one row per tier, `h-fan` the fanout row.
SYN = {"transports": {"host": {"costModel": "plan-quota"}},
       "models": [{"transport": "host", "id": "host-arch-1", "alias": "h-arch",
                   "roles": ["orchestrator", "architect", "executor"]},
                  {"transport": "host", "id": "host-fan-1", "alias": "h-fan",
                   "roles": ["fanout", "scout"]}]}
TIERS = {"orchestrator": ["h-arch"], "architect": ["h-arch"], "executor": ["h-arch"],
         "fanout": ["h-fan"], "scout": ["h-fan"]}


def planted(fn):
    """Run `fn` with the registry, ladder and session transport planted as SYN/TIERS on `host`."""
    import _session_transport as st
    import model_registry as mr
    saved = (st.resolve, st.HOST_TRANSPORT, st._registry, mr.load, mr.role_tiers)
    try:
        st.resolve = lambda **_k: ("host", "test")
        st.HOST_TRANSPORT = "host"
        st._registry = lambda: SYN
        mr.load = lambda *_a, **_k: SYN
        mr.role_tiers = lambda *_a, **_k: TIERS
        return fn()
    finally:
        st.resolve, st.HOST_TRANSPORT, st._registry, mr.load, mr.role_tiers = saved


def test_no_engine_default_exists():
    assert not any(hasattr(pins(), name) for name in ("default_pin", "tier_pins", "ENGINE_DEFAULT_EFFORT",
                                                      "OMITTED_PIN_TIER")), "an engine default pin is back"


def test_review_agent_omitted_pin_stays_omitted():
    jobs = by_label(planted(lambda: pins().dispatch_jobs(wf(
        ".claude/workflows/review_fanout.js",
        {"agents": [{"key": "k1", "model": "h-arch", "effort": "low"}, {"key": "k2"}]}))))
    assert (jobs["k1"]["model"], jobs["k1"]["effort"]) == ("h-arch", "low"), jobs
    assert (jobs["k2"]["model"], jobs["k2"]["effort"]) == (None, None), jobs


def test_chain_job_and_lens_omitted_effort_stay_omitted():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/dispatch_chains.js",
                                            {"chains": [{"jobs": [{"label": "c1", "model": "h-arch"}]}]})))
    assert jobs["c1"]["effort"] is None, jobs
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/any_lens_engine.js",
                                            {"lenses": [{"key": "l1", "model": "h-fan"}]})))
    assert jobs["l1"]["effort"] is None, jobs


def test_dispatch_job_has_no_effort_default():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/dispatch.js",
                                            {"jobs": [{"label": "d1", "model": "h-arch"}]})))
    assert jobs["d1"]["effort"] is None and jobs["d1"]["model"] == "h-arch", jobs


def test_non_review_agents_get_no_model_default():
    jobs = by_label(pins().dispatch_jobs(wf("x/other.js", {"agents": [{"key": "a1"}]})))
    assert jobs["a1"]["effort"] is None, jobs


def test_kind_and_rail_tier_pass_through():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/dispatch.js", {"jobs": [
        {"label": "s1", "model": "h-arch", "effort": "low", "kind": "author", "railTier": "condensed"},
        {"label": "s2", "model": "h-arch", "effort": "low"}]})))
    assert (jobs["s1"]["kind"], jobs["s1"]["railTier"]) == ("author", "condensed"), jobs
    assert (jobs["s2"]["kind"], jobs["s2"]["railTier"]) == (None, None), jobs


def test_string_args_are_parsed():
    jobs = pins().dispatch_jobs(wf(".claude/workflows/dispatch.js", '{"jobs": [{"label": "j", "model": "h-scout"}]}'))
    assert [j["label"] for j in jobs] == ["j"], jobs


def test_agent_call_is_one_job():
    jobs = pins().dispatch_jobs({"tool_name": "Agent", "tool_input": {"description": "d", "subagent_type": "Explore"}})
    assert jobs == [{"label": "d", "model": None, "effort": None, "agentType": "Explore"}], jobs


def test_script_blobs_strip_comments_from_inline_and_file():
    fd, path = tempfile.mkstemp(suffix=".js")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("// model: 'h-scout'\nconst u = 'https://x.y'\nagent(p, {model: 'h-arch'})\n")
    blobs, incomplete = pins().script_blobs({"tool_name": "Workflow", "tool_input": {
        "script": "/* model: 'h-fan' */ agent(p, {model: 'h-orch'})", "scriptPath": path}})
    texts = dict(blobs)
    assert incomplete == [], incomplete
    assert "h-fan" not in texts["inline-script"] and "h-orch" in texts["inline-script"], texts
    file_text = texts[os.path.basename(path)]
    assert "h-scout" not in file_text and "https://x.y" in file_text and "h-arch" in file_text, file_text
    assert pins().script_blobs({"tool_name": "Agent", "tool_input": {"script": "x"}}) == ([], [])


def test_host_pins_a_versioned_alias_by_id():
    """The host runtime resolves only a family-name alias; a versioned alias reaches it by id."""
    def check():
        pin = pins().native_pin
        assert pin({"id": "claude-x-5-5", "alias": "x55", "family": "x"}, "host") == "claude-x-5-5"
        assert pin({"id": "claude-x-5", "alias": "x", "family": "x"}, "host") == "x"
        assert pin({"id": "host-fan-1", "alias": "h-fan"}, "host") == "h-fan"
        assert pin({"id": "p-1", "alias": "p", "family": "q"}, "proxy") == "p-1"

    planted(check)


def test_host_legal_pins_drop_a_versioned_alias():
    """A versioned alias is not a native pin on the host: the runtime would receive it unresolved."""
    import _session_transport as st
    rows = [{"transport": "host", "id": "claude-x-5-5", "alias": "x55", "family": "x", "roles": ["fanout"]},
            {"transport": "host", "id": "claude-x-5", "alias": "x", "family": "x", "roles": []},
            {"transport": "host", "id": "host-fan-1", "alias": "h-fan", "roles": ["scout"]}]
    data = {"transports": {"host": {"costModel": "plan-quota"}}, "models": rows}
    saved = st._dispatchable_rows
    try:
        st._dispatchable_rows = lambda transport, d, seat: [m for m in d["models"] if m["transport"] == transport]
        legal = planted(lambda: pins().native_legal_pins("host", data))
    finally:
        st._dispatchable_rows = saved
    assert legal == {"claude-x-5-5", "claude-x-5", "x", "host-fan-1", "h-fan"}, legal


def test_sidecar_fanout_author_spec_is_a_writer():
    import json
    with tempfile.TemporaryDirectory(prefix="writer_spec_") as root:
        path = os.path.join(root, "jobs.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"jobs": [{"label": "fix", "kind": "author"}]}, fh)
        command = 'python3 .claude/tools/sidecar_fanout.py "%s"' % path.replace("\\", "/")
        assert pins().writes("Bash", {"command": command}), command


def test_author_writing_only_gitignored_paths_is_not_a_writer():
    """Close waits on a writer because it can change tracked files; a brief whose writeSet is all
    gitignored or outside the checkout (a vault doc) cannot, and a brief with no writeSet is unknown,
    so it still waits."""
    import json
    repo = os.path.dirname(os.path.dirname(HOOKS))
    with tempfile.TemporaryDirectory(prefix="writer_set_") as root:
        def brief(name, write_set):
            task = {"check": ["claims"]}
            if write_set is not None:
                task["writeSet"] = write_set
            path = os.path.join(root, name).replace("\\", "/")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Do the thing.\n\n```task\n%s\n```\n" % json.dumps(task))
            return path

        scratch = brief("scratch.md", [".claude/scratch/voice/out.md", ".claude/scratch/voice/"])
        tracked = brief("tracked.md", ["src/app/Gate.tsx"])
        mixed = brief("mixed.md", [".claude/scratch/out.md", "convex/columns.ts"])
        undeclared = brief("undeclared.md", None)
        odd = brief("odd.md", [".claude/scratch/out.md", 3])
        not_a_list = brief("not_a_list.md", ".claude/scratch/out.md")
        vault = os.path.join(root, "vault", "decisions.md").replace("\\", "/")
        outside = brief("outside.md", [vault, "../sibling/notes.md", ".claude/scratch/out.md"])
        outside_tracked = brief("outside_tracked.md", [vault, "convex/columns.ts"])

        def fanout(prompt):
            path = os.path.join(root, "jobs-%s.json" % os.path.basename(prompt)).replace("\\", "/")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"jobs": [{"label": "w", "kind": "author", "promptPath": prompt}]}, fh)
            return {"command": 'python3 .claude/tools/sidecar_fanout.py "%s"' % path}

        saved = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = repo
        try:
            for prompt, expected in [(scratch, False), (tracked, True), (mixed, True), (undeclared, True),
                                     (odd, True), (not_a_list, True), (outside, False),
                                     (outside_tracked, True)]:
                assert pins().writes("Bash", fanout(prompt)) is expected, ("fanout", prompt)
                sidecar = {"command": "bash .claude/scripts/claude_sidecar.sh -G author -l w -f %s" % prompt}
                assert pins().writes("Bash", sidecar) is expected, ("sidecar", prompt)
                workflow = wf(".claude/workflows/dispatch.js", {"jobs": [{"kind": "author", "promptPath": prompt}]})
                assert pins().writes("Workflow", workflow["tool_input"]) is expected, ("workflow", prompt)
        finally:
            if saved is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = saved


def test_writer_classification():
    cases = [
        (wf(".claude/workflows/review_fanout.js", {"fold": True}), True),
        (wf(".claude/workflows/review_fanout.js", {}), False),
        (wf(".claude/workflows/dispatch.js", {"jobs": [{"kind": "author"}]}), True),
        (wf(".claude/workflows/dispatch.js", {"jobs": [{"kind": "scout"}]}), False),
        (wf(".claude/workflows/dispatch_chains.js", {"chains": [{"jobs": [{"kind": "author"}]}]}), True),
        ({"tool_name": "Bash", "tool_input": {"command": "python3 .claude/tools/sidecar_fanout.py jobs.json --fold brief.md"}}, True),
        ({"tool_name": "Bash", "tool_input": {"command": "bash .claude/scripts/claude_sidecar.sh -G author -l fix"}}, True),
        ({"tool_name": "Bash", "tool_input": {"command": "bash .claude/scripts/claude_sidecar.sh -G survey -l read"}}, False),
    ]
    for payload, expected in cases:
        assert pins().writes(payload["tool_name"], payload["tool_input"]) is expected, payload


def _scratch_file(name, body, root=None):
    import json
    root = root or tempfile.mkdtemp(prefix="pins_jf_")
    d = os.path.join(root, ".claude", "scratch", "t")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body if isinstance(body, str) else json.dumps(body))
    return root, path


def _with_root(root, fn):
    saved = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = root
    try:
        return fn()
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = saved


JOB = {"label": "a", "promptPath": "a.md", "model": "m", "effort": "low", "agentType": "Explore"}


def test_jobs_file_expands():
    root, path = _scratch_file("native.args.json", {"jobs": [JOB], "spillDir": ".claude/scratch/t"})
    ti = {"scriptPath": ".claude/workflows/dispatch.js", "args": {"jobsFile": path}}
    assert _with_root(root, lambda: pins().jobs_file_refusal(ti)) is None
    got = _with_root(root, lambda: pins()._args(ti))
    assert got.get("jobs") == [JOB] and "jobsFile" not in got, got
    rel = {"args": {"jobsFile": ".claude/scratch/t/native.args.json"}}
    assert _with_root(root, lambda: pins()._args(rel)).get("jobs") == [JOB]
    as_str = {"args": __import__("json").dumps({"jobsFile": path})}
    assert _with_root(root, lambda: pins()._args(as_str)).get("jobs") == [JOB]
    assert pins()._args({"args": {"jobs": [JOB]}}) == {"jobs": [JOB]}


def test_jobs_file_refusals():
    root, good = _scratch_file("g.json", {"jobs": [JOB]})
    outside = os.path.join(tempfile.mkdtemp(prefix="pins_out_"), "o.json")
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write('{"jobs": []}')
    _r, bad_key = _scratch_file("k.json", {"jobs": [JOB], "__transport": {"ids": ["x"]}}, root=root)
    _r, cur = _scratch_file("c.json", {"jobs": [JOB], "currency": "paid"}, root=root)
    _r, curr = _scratch_file("r.json", {"jobs": [JOB], "currencyReason": "why"}, root=root)
    _r, junk = _scratch_file("j.json", "{not json", root=root)
    _r, arr = _scratch_file("a.json", "[1]", root=root)
    cases = [
        ("jobs beside jobsFile", {"jobsFile": good, "jobs": [JOB]}, "beside"),
        ("chains beside jobsFile", {"jobsFile": good, "chains": []}, "beside"),
        ("outside scratch", {"jobsFile": outside}, ".claude/scratch/"),
        ("dotdot escape", {"jobsFile": ".claude/scratch/../plans/x.json"}, ".claude/scratch/"),
        ("missing file", {"jobsFile": ".claude/scratch/t/none.json"}, "read"),
        ("not json", {"jobsFile": junk}, "read as JSON"),
        ("array file", {"jobsFile": arr}, "JSON object"),
        ("__ key", {"jobsFile": bad_key}, "__transport"),
        ("currency", {"jobsFile": cur}, "currency"),
        ("currencyReason", {"jobsFile": curr}, "currencyReason"),
        ("non-string path", {"jobsFile": 5}, "jobsFile"),
    ]
    for name, args, needle in cases:
        reason = _with_root(root, lambda: pins().jobs_file_refusal({"args": args}))
        assert isinstance(reason, str) and needle in reason, (name, reason)
    assert pins().jobs_file_refusal({"args": {"jobs": [JOB]}}) is None


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001 - proof runner reports every case
                fails += 1
                print("FAIL", name, "-", type(exc).__name__, str(exc)[:300])
    sys.exit(1 if fails else 0)
