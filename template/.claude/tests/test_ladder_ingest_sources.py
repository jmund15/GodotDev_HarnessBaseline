#!/usr/bin/env python3
"""Proof for ladder_ingest's ARM SOURCES — the layer that decides what can be compared.

Restricting the ingest to whole sessions left every Workflow fan-out and every sidecar arm with no
route onto the ladder, so the load-bearing cases are the three non-session sources: each must
produce the SAME `info` dict shape the session path produces, since nothing downstream branches
on how an arm was dispatched.

Fixtures are written to temp dirs in the layouts the runtime actually writes. No live transcript is
read except by the one case that asserts the session path still works.

Run: python3 .claude/tests/test_ladder_ingest_sources.py
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "ladder_ingest.py")
spec = importlib.util.spec_from_file_location("li", MOD)
li = importlib.util.module_from_spec(spec)
spec.loader.exec_module(li)

PROMPT = "Audit this diff and report every defect you can justify from the file."


def _row(kind, **extra):
    d = {"type": kind, "timestamp": "2026-09-08T00:00:00Z"}
    d.update(extra)
    return json.dumps(d)


def subagent_transcript(tmp, name, model, sidechain=True, with_meta=True):
    """A workflow-subagent transcript in the runtime's layout: rows are sidechains, meta names
    the pin."""
    p = Path(tmp) / ("agent-%s.jsonl" % name)
    lines = [
        _row("user", isSidechain=sidechain, message={"role": "user", "content": PROMPT}),
        _row("assistant", isSidechain=sidechain,
             message={"model": "claude-opus-5-served", "content": [
                 {"type": "tool_use", "name": "Read"},
                 {"type": "text", "text": "Finding: the guard never fires."}]}),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    if with_meta:
        p.with_suffix(".meta.json").write_text(json.dumps({
            "agentType": "workflow-subagent", "description": "review:%s" % name,
            "model": model}), encoding="utf-8", newline="\n")
    return p


def sidecar_record(tmp, label, served, requested=None, effort="max", with_out=True):
    """A `-R` record + `.out.json` pair as tools/sidecar_fanout.py writes them."""
    d = Path(tmp)
    (d / ("%s.record.json" % label)).write_text(json.dumps({
        "label": label, "servedModel": served, "requestedModel": requested or served,
        "transport": "codex", "effort": effort, "numTurns": 42}),
        encoding="utf-8", newline="\n")
    if with_out:
        (d / ("%s.out.json" % label)).write_text(json.dumps({
            "type": "result", "result": "Finding: the seat filter is blind.", "prompt": PROMPT}),
            encoding="utf-8", newline="\n")
    return d / ("%s.record.json" % label)


TMP = tempfile.mkdtemp(prefix="li_src_")
WF = tempfile.mkdtemp(prefix="li_wf_")
SUB = subagent_transcript(TMP, "aaa", "opus")
REC = sidecar_record(TMP, "luna-arm", "gpt-5.6-luna")
subagent_transcript(WF, "w1", "opus")
subagent_transcript(WF, "w2", "sonnet")

SUB_INFO = li._transcript_arm(SUB)
REC_INFO = li._record_arm(REC)
KEYS = {"session", "model", "models_seen", "first_prompt", "final", "cross",
        "turns", "tool_calls", "transport", "effort", "source"}


def infos_for(token):
    return li.arm_infos(token, Path(TMP))


CASES = [
    # ---- the subagent source ----------------------------------------------
    # Every row in a subagent transcript is a sidechain; the default row filter drops those, so
    # before the flag existed this file read as empty and the ingest exited "no user prompt".
    ("a subagent transcript is read at all -- its rows are sidechains",
     lambda: SUB_INFO["first_prompt"] == PROMPT),

    ("...and the SAME file without sidechain rows still reads, so the flag is not load-bearing "
     "on session transcripts",
     lambda: li._transcript_arm(
         subagent_transcript(TMP, "plain", "opus", sidechain=False))["first_prompt"] == PROMPT),

    # The meta pin, not the served id: a role pin resolves to an id the ladder does not key on.
    ("the dispatcher's pin wins over the served model id",
     lambda: SUB_INFO["model"] == "opus"),

    ("...and the served id is still visible, so a disagreement is not hidden",
     lambda: "claude-opus-5-served" in SUB_INFO["models_seen"]),

    ("the label comes from the meta description, not an agent id slice",
     lambda: SUB_INFO["label"] == "review:aaa"),

    ("a transcript with NO meta falls back to the served model rather than failing",
     lambda: li._transcript_arm(
         subagent_transcript(TMP, "nometa", "x", with_meta=False))["model"]
         == "claude-opus-5-served"),

    ("tool calls are counted from the transcript",
     lambda: SUB_INFO["tool_calls"] == 1),

    # A Workflow subagent returns through the StructuredOutput TOOL and the runtime tells it not to
    # put the answer in text. Reading text alone scored a six-finding audit as a 52-char answer.
    ("a StructuredOutput deliverable is the arm's final answer, not its sign-off sentence",
     lambda: "the guard never fires" in _structured_arm()["final"]
             and "sign-off" not in _structured_arm()["final"]),

    ("...and a longer TEXT answer still wins when there is no structured output",
     lambda: SUB_INFO["final"] == "Finding: the guard never fires."),

    # ---- the sidecar source -----------------------------------------------
    ("a sidecar record becomes an arm with its served model",
     lambda: REC_INFO["model"] == "gpt-5.6-luna"),

    ("...carrying the record's transport, effort and turn count",
     lambda: (REC_INFO["transport"], REC_INFO["effort"], REC_INFO["turns"])
             == ("codex", "max", 42)),

    ("the deliverable is read from the sibling .out.json",
     lambda: "seat filter is blind" in REC_INFO["final"]),

    ("a record whose pin and served model DISAGREE keeps both",
     lambda: set(li._record_arm(sidecar_record(TMP, "swap", "gpt-5.6-terra", "luna"))
                 ["models_seen"]) == {"gpt-5.6-terra", "luna"}),

    ("a record with no .out.json yields an empty deliverable, not a crash",
     lambda: li._record_arm(sidecar_record(TMP, "noout", "m", with_out=False))["final"] == ""),

    # ---- one normalized shape ---------------------------------------------
    # This is the whole design: downstream never learns how an arm was dispatched.
    ("subagent and sidecar arms carry the same key set",
     lambda: KEYS <= set(SUB_INFO) and KEYS <= set(REC_INFO)),

    ("...and each names its own source and origin",
     lambda: SUB_INFO["source"] == "workflow-subagent" and REC_INFO["source"] == "sidecar"
             and REC_INFO["origin"].endswith("luna-arm.record.json")),

    # ---- the directory source ---------------------------------------------
    ("a workflow run dir fans out to one arm per agent transcript",
     lambda: len(infos_for(WF)) == 2),

    ("...and a dir with no agent transcripts is refused, not silently empty",
     lambda: _exits(lambda: infos_for(tempfile.mkdtemp(prefix="li_empty_")))),

    # ---- refusals ---------------------------------------------------------
    ("a file that is neither a transcript nor a record is refused by name",
     lambda: _exits(lambda: infos_for(_plain_file()))),

    ("an unresolvable session prefix is still refused",
     lambda: _exits(lambda: infos_for("zzzznotasession"))),

    # `--slug ..` survived basename() and wrote the comparison one level ABOVE --out.
    ("a traversing slug is refused rather than escaping --out",
     lambda: _exits(lambda: li.main(["x", "--out", TMP, "--slug", ".."]))
             and _exits(lambda: li.main(["x", "--out", TMP, "--slug", "a/b"]))),

    # ---- an absent prompt is not a matching prompt ------------------------
    # sha256("") is a real hash: folding no-prompt arms into the equality test made
    # "we cannot see the prompt" render as "the prompt matched".
    ("arms with no recorded prompt force same_prompt false and are named",
     lambda: _same_prompt_false_with_missing()),
]


def _structured_arm():
    """An arm whose real deliverable is a structured_output attachment, text a bare sign-off."""
    p = Path(TMP) / "agent-structured.jsonl"
    p.write_text("\n".join([
        _row("user", isSidechain=True, message={"role": "user", "content": PROMPT}),
        _row("assistant", isSidechain=True, message={
            "model": "m", "content": [{"type": "text", "text": "Done; sign-off."}]}),
        _row("attachment", isSidechain=True, attachment={
            "type": "structured_output", "toolUseID": "t1",
            "data": {"findings": [{"file": "a.py:1",
                                   "description": "the guard never fires on the real path"}]}}),
    ]) + "\n", encoding="utf-8", newline="\n")
    return li._transcript_arm(p)


def _plain_file():
    fd, p = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    return p


def _exits(fn):
    try:
        fn()
    except SystemExit:
        return True
    return False


def _same_prompt_false_with_missing():
    out = Path(tempfile.mkdtemp(prefix="li_cmp_"))
    a = li._record_arm(sidecar_record(TMP, "hasprompt", "m1"))
    b = li._record_arm(sidecar_record(TMP, "noprompt2", "m2", with_out=False))
    text = li.write_comparison(out, "s", [a, b]).read_text(encoding="utf-8")
    return "same_prompt: false" in text and "prompt_not_recorded" in text


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
