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


def test_review_agent_defaults_effort_and_model():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/review_fanout.js",
                                            {"agents": [{"key": "k1", "model": "opus", "effort": "low"}, {"key": "k2"}]})))
    assert (jobs["k1"]["model"], jobs["k1"]["effort"]) == ("opus", "low"), jobs
    assert (jobs["k2"]["model"], jobs["k2"]["effort"]) == ("sonnet", "medium"), jobs
    assert pins().REVIEW_FANOUT_DEFAULT_MODEL == "sonnet"


def test_chain_job_defaults_effort():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/dispatch_chains.js",
                                            {"chains": [{"jobs": [{"label": "c1", "model": "opus"}]}]})))
    assert jobs["c1"]["effort"] == pins().ENGINE_DEFAULT_EFFORT == "medium", jobs


def test_explore_lens_defaults_effort():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/explore_fanout.js",
                                            {"lenses": [{"key": "l1", "model": "sonnet"}]})))
    assert jobs["l1"]["effort"] == "medium", jobs


def test_dispatch_job_has_no_effort_default():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/dispatch.js",
                                            {"jobs": [{"label": "d1", "model": "opus"}]})))
    assert jobs["d1"]["effort"] is None and jobs["d1"]["model"] == "opus", jobs


def test_non_review_agents_get_no_model_default():
    jobs = by_label(pins().dispatch_jobs(wf("x/other.js", {"agents": [{"key": "a1"}]})))
    assert jobs["a1"]["effort"] is None, jobs


def test_shape_and_rail_tier_pass_through():
    jobs = by_label(pins().dispatch_jobs(wf(".claude/workflows/dispatch.js", {"jobs": [
        {"label": "s1", "model": "opus", "effort": "low", "shape": "author", "railTier": "condensed"},
        {"label": "s2", "model": "opus", "effort": "low"}]})))
    assert (jobs["s1"]["shape"], jobs["s1"]["railTier"]) == ("author", "condensed"), jobs
    assert (jobs["s2"]["shape"], jobs["s2"]["railTier"]) == (None, None), jobs


def test_string_args_are_parsed():
    jobs = pins().dispatch_jobs(wf(".claude/workflows/dispatch.js", '{"jobs": [{"label": "j", "model": "haiku"}]}'))
    assert [j["label"] for j in jobs] == ["j"], jobs


def test_agent_call_is_one_job():
    jobs = pins().dispatch_jobs({"tool_name": "Agent", "tool_input": {"description": "d", "subagent_type": "Explore"}})
    assert jobs == [{"label": "d", "model": None, "effort": None, "agentType": "Explore"}], jobs


def test_script_blobs_strip_comments_from_inline_and_file():
    fd, path = tempfile.mkstemp(suffix=".js")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("// model: 'haiku'\nconst u = 'https://x.y'\nagent(p, {model: 'opus'})\n")
    blobs, incomplete = pins().script_blobs({"tool_name": "Workflow", "tool_input": {
        "script": "/* model: 'sonnet' */ agent(p, {model: 'fable'})", "scriptPath": path}})
    texts = dict(blobs)
    assert incomplete == [], incomplete
    assert "sonnet" not in texts["inline-script"] and "fable" in texts["inline-script"], texts
    file_text = texts[os.path.basename(path)]
    assert "haiku" not in file_text and "https://x.y" in file_text and "opus" in file_text, file_text
    assert pins().script_blobs({"tool_name": "Agent", "tool_input": {"script": "x"}}) == ([], [])


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
