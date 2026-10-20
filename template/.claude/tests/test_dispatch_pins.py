"""Proof cases for hooks/_dispatch_pins.py — the Workflow/Agent pin parser shared by the provider guard
and the dispatch table: each engine's effort default, the review_fanout model default, shape/railTier
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


def test_shape_and_rail_tier_pass_through():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/dispatch.js", {"jobs": [
        {"label": "s1", "model": "h-arch", "effort": "low", "shape": "author", "railTier": "condensed"},
        {"label": "s2", "model": "h-arch", "effort": "low"}]})))
    assert (jobs["s1"]["shape"], jobs["s1"]["railTier"]) == ("author", "condensed"), jobs
    assert (jobs["s2"]["shape"], jobs["s2"]["railTier"]) == (None, None), jobs


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
            json.dump({"jobs": [{"label": "fix", "shape": "author"}]}, fh)
        command = 'python3 .claude/tools/sidecar_fanout.py "%s"' % path.replace("\\", "/")
        assert pins().writes("Bash", {"command": command}), command


def test_writer_classification():
    cases = [
        (wf(".claude/workflows/review_fanout.js", {"fold": True}), True),
        (wf(".claude/workflows/review_fanout.js", {}), False),
        (wf(".claude/workflows/dispatch.js", {"jobs": [{"shape": "author"}]}), True),
        (wf(".claude/workflows/dispatch.js", {"jobs": [{"shape": "survey"}]}), False),
        (wf(".claude/workflows/dispatch_chains.js", {"chains": [{"jobs": [{"shape": "author"}]}]}), True),
        ({"tool_name": "Bash", "tool_input": {"command": "python3 .claude/tools/sidecar_fanout.py jobs.json --fold brief.md"}}, True),
        ({"tool_name": "Bash", "tool_input": {"command": "bash .claude/scripts/claude_sidecar.sh -G author -l fix"}}, True),
        ({"tool_name": "Bash", "tool_input": {"command": "bash .claude/scripts/claude_sidecar.sh -G survey -l read"}}, False),
    ]
    for payload, expected in cases:
        assert pins().writes(payload["tool_name"], payload["tool_input"]) is expected, payload


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
