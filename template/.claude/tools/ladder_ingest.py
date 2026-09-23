#!/usr/bin/env python3
"""Ladder ingest — turn a same-prompt model comparison into ladder evidence.

An ARM is one model's run of the shared prompt. Four kinds, because a comparison is worth
ingesting however it was dispatched — restricting this to whole sessions left every
Workflow fan-out and every sidecar arm with no route onto the ladder:

    <prefix>              a Claude Code SESSION, by transcript id prefix
    <path>.jsonl          any transcript: a workflow subagent, an Agent-tool subagent
                          (a sibling `<name>.meta.json` supplies model + label)
    <path>.record.json    a SIDECAR dispatch written by tools/sidecar_fanout.py
                          (deliverable read from the sibling `<label>.out.json`)
    <dir>/                a workflow run dir — expands to every `agent-*.jsonl` in it

    python3 .claude/tools/ladder_ingest.py <arm> [<arm> ...] --out <dir> [--slug <name>]
    python3 .claude/tools/ladder_ingest.py --out <dir> --slug <name> --apply <judge_result.json>

Every source normalizes to ONE `info` dict, so COMPARISON.md, the judge brief and the
adjudication never learn how an arm was dispatched. Adding a fifth source is one reader.

Transcript lookup and JSONL row reading reuse `.claude/tools/session_digest.py` — this
file adds no row-shape handling of its own (model lives at `message.model` on assistant
rows; SessionStart rails lines live in `attachment` rows).

**What lands on the ladder is a TENDENCY, never this run.** `/ladder_ingest` owns that authoring
contract; `tools/ladder_prose_check.py` enforces its mechanical half, and the judge brief asks for
tendencies for the same reason.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import session_digest as digest  # noqa: E402

REVIEW_HINTS = ("review", "compare", "other session", "other response")
ADJUDICATION_PENDING = "<!-- adjudication pending: dispatch judge_brief.md -->"
COMPACTION_LEAD = "This session is being continued"

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "winner": {"type": "string"},
                    "margin": {"type": "string", "enum": ["clear", "slight", "tie"]},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "arm": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["arm", "quote"],
                        },
                    },
                    "why": {"type": "string"},
                },
                "required": ["name", "winner", "margin", "evidence", "why"],
            },
        },
        "overall": {"type": "string"},
        "doesNotShow": {"type": "array", "items": {"type": "string"}},
        "ladderClause": {
            "type": "string",
            "description": "one clause for a ± cell: what the model TENDS to do, never what this "
                           "run did. No numbers, dates, arm counts or wall-clock. Empty is valid "
                           "when nothing here would recur.",
        },
        "orchestrationNote": {
            "type": "string",
            "description": "empty unless the finding is about delegation behaviour",
        },
        "couldNotSatisfy": {"type": "string"},
    },
    "required": ["criteria", "overall", "doesNotShow", "ladderClause",
                 "orchestrationNote", "couldNotSatisfy"],
}


def is_cross_review_prompt(text: str) -> bool:
    """Whether a user prompt asks the session to review/compare another session's answer.

    Heuristic, deliberately naive: the prompt contains `review`, `compare`,
    `other session` or `other response` (case-insensitive). It over-matches pasted
    context that happens to contain those words; that is accepted — the per-session
    file is a verbatim record and the judge sees the full turn, not just the hit.
    """
    low = (text or "").lower()
    return any(h in low for h in REVIEW_HINTS)


def _user_text(row: dict) -> str:
    c = (row.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


def _is_hook_echo(text: str) -> bool:
    s = (text or "").lstrip()
    return (not s or s.startswith("<") or s.startswith("[Request")
            or s.startswith(COMPACTION_LEAD))


def _is_real_prompt(text: str) -> bool:
    # Mirrors TranscriptSummaryBuilder: sub-10-char rows (bare `/compact` echoes)
    # are not prompts. Bare slash-command lines are harness invocations, not
    # prompt content, whatever their length.
    s = (text or "").strip()
    return len(s) >= 10 and not re.match(r"^/[a-z][\w-]*(?:\s|$)", s)


def _assistant_parts(row: dict) -> tuple[str | None, list[str], int]:
    """(model, text blocks, tool_use count) for one assistant row."""
    msg = row.get("message") or {}
    model = msg.get("model") or row.get("model")
    texts, tools = [], 0
    for b in msg.get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and (b.get("text") or "").strip():
            texts.append(b["text"])
        elif b.get("type") == "tool_use":
            tools += 1
    return model, texts, tools


def _rails_info(path: Path, sidechain: bool = False) -> tuple[str, str]:
    """(transport, effort) from SessionStart rails attachments.

    Transport is whichever rails marker names it: `[anthropic session` /
    `[codex session`; a bare `[session]` prefix alone names no transport, so
    `unknown`. Effort is `unknown` unless a rails line states a level outright
    (the stock rails say effort is not visible to the session).
    """
    transport, effort = "unknown", "unknown"
    for row in digest._rows(path, "attachment", sidechain):
        att = row.get("attachment") or {}
        if att.get("hookEvent") != "SessionStart":
            continue
        content = att.get("content") or ""
        low = content.lower()
        if transport == "unknown":
            if "[codex session" in low:
                transport = "codex"
            elif "[anthropic session" in low:
                transport = "anthropic"
        if effort == "unknown":
            # `max` included: it is the top rung on every sidecar transport and what the
            # `claude-gpt-*` launcher accepts, so omitting it recorded a max-effort arm as
            # `unknown` — the one field a comparison of efforts cannot do without.
            m = re.search(
                r"effort (?:level )?(?:is |to |set to |:\s*)(low|medium|high|xhigh|max)\b",
                content, re.I)
            if m:
                effort = m.group(1).lower()
        if transport != "unknown" and effort != "unknown":
            break
    return transport, effort


def _structured_output(path: Path, sidechain: bool = False) -> str:
    """The LAST `structured_output` attachment, rendered as JSON text, or ''.

    This is where a Workflow subagent's deliverable actually lives. Rendered rather than summarized:
    a judge scores the arm's own words, and a shape this file invented would be scoring the ingest.
    """
    out = ""
    for row in digest._rows(path, "attachment", sidechain):
        att = row.get("attachment") or {}
        if att.get("type") == "structured_output" and att.get("data") is not None:
            out = json.dumps(att["data"], indent=2, ensure_ascii=False)
    return out


def extract(path: Path, sidechain: bool = False) -> dict:
    """Everything COMPARISON.md and the arm file need, from one transcript.

    `sidechain=True` for a SUBAGENT transcript: every row there is a sidechain, which the
    default filter drops -- the file then reads as empty rather than as a subagent.
    """
    # isMeta rows are harness-injected context (skill docs, command text), not user
    # prompts — TranscriptSummaryBuilder skips them too. Without this, injected
    # skill text both inflates `turns` and trips the cross-review heuristic.
    prompts = [(row.get("timestamp") or "", _user_text(row))
               for row in digest._rows(path, "user", sidechain)
               if not row.get("isMeta")]
    prompts = [(ts, t) for ts, t in prompts
               if not _is_hook_echo(t) and _is_real_prompt(t)]

    models: Counter = Counter()
    efforts: list[str] = []  # distinct values in order of first appearance: a /effort mid-run shows as a->b
    tool_calls = 0
    replies: list[tuple[str, str]] = []  # (timestamp, joined text) per assistant row with text
    for row in digest._rows(path, "assistant", sidechain):
        model, texts, tools = _assistant_parts(row)
        if model:
            models[model] += 1
        effort = row.get("effort")
        if isinstance(effort, str) and effort and (not efforts or efforts[-1] != effort):
            efforts.append(effort)
        tool_calls += tools
        if texts:
            replies.append((row.get("timestamp") or "", "\n".join(texts)))

    if not prompts:
        sys.exit("empty transcript: no user prompt in %s" % path)

    # A Workflow subagent returns through the StructuredOutput TOOL, and the runtime is explicit
    # that the script reads only that call — so the arm's text blocks hold a sign-off sentence and
    # the real deliverable sits in a `structured_output` attachment. Reading text alone scored a
    # six-finding audit as a 52-character answer, which reads as an arm that barely engaged.
    structured = _structured_output(path, sidechain)
    if structured and len(structured) > len(replies[-1][1] if replies else ""):
        replies.append(("", structured))

    cross = []
    for ts, prompt in prompts:
        if is_cross_review_prompt(prompt):
            reply = next((t for rts, t in replies if rts >= ts), "")
            cross.append((prompt, reply))

    ordered = sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "session": path.stem,
        "model": ordered[0][0] if ordered else "unknown",
        "models_seen": sorted(models),
        "efforts_seen": efforts,
        "first_prompt": prompts[0][1],
        "final": replies[-1][1] if replies else "",
        "cross": cross,
        "turns": len(prompts),
        "tool_calls": tool_calls,
    }


def _fence(text: str) -> str:
    back = "````" if "```" in text else "```"
    return "%s\n%s\n%s" % (back, text, back)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def arm_name(info: dict) -> str:
    """A short filesystem-safe name for one arm.

    The dispatcher's label when there is one -- an 8-char slice of an agent id renders a whole
    workflow fan-out as `agent-a0`, `agent-a2`, naming nothing a reader can act on. With no label
    the arm is a session, and its 8-char id prefix IS how sessions are named everywhere else
    (`session_digest.py --list`); the full uuid stem would be less readable, not more.
    """
    raw = info.get("label") or (info.get("session") or "arm")[:8]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw)).strip("-")
    return (name[:48] or "arm")


def _count(n) -> str:
    """A count, or `not recorded` when the source captures none.

    Rendering an absent count as `0` reads as "this arm opened no files", and a judge scored
    grounding on it. Absence and zero are different answers and must not print the same.
    """
    return "not recorded" if n is None else str(n)


def prompt_sha(info: dict) -> str:
    """The arm's verbatim first-prompt hash, or a phrase saying there is none.

    A function, not a key another writer plants: `write_comparison` used to read a field
    `write_session_file` set as a side effect, so calling them in the other order raised KeyError.
    """
    return (hashlib.sha256(info["first_prompt"].encode("utf-8")).hexdigest()
            if info.get("first_prompt") else "(prompt not recorded)")


def write_session_file(outdir: Path, info: dict) -> Path:
    sha = prompt_sha(info)
    info["first_prompt_sha256"] = sha
    head = ["---",
            "session: %s" % info["session"],
            "model: %s" % info["model"],
            "models_seen: %s" % json.dumps(info["models_seen"]),
            "transport: %s" % info["transport"],
            "effort: %s" % info["effort"],
            "first_prompt_sha256: %s" % sha,
            "turns: %d" % info["turns"],
            "tool_calls: %s" % _count(info["tool_calls"]),
            "---", ""]
    body = ["## First prompt", "", _fence(info["first_prompt"]), "",
            "## Final deliverable", "",
            _fence(info["final"]) if info["final"] else "(no assistant text)", ""]
    body += ["## Cross-review turns", ""]
    if not info["cross"]:
        body.append("(none)")
    for i, (prompt, reply) in enumerate(info["cross"], 1):
        body += ["### Turn %d" % i, "", "**Prompt:**", "", _fence(prompt), "",
                 "**Reply:**", "",
                 _fence(reply) if reply else "(no following assistant text)", ""]
    dest = outdir / ("arm-%s.md" % arm_name(info))
    dest.write_text("\n".join(head + body), encoding="utf-8", newline="\n")
    return dest


JUDGE_BRIEF_TMPL = """# Judge brief — arm comparison (`{slug}`)

Adjudicate N=1 runs of the same prompt across ARMS (different models, transports or
efforts). An arm is one model's run, whatever dispatched it — a session, a workflow
subagent, a sidecar call. n=1 per arm on one prompt shows a hypothesis, never a ranking.

## Arms

{session_lines}

Schema: {schema_path}

## Task

Read each arm file fully — first prompt, final deliverable, cross-review turns.
For grounding checks, the transcript's tool calls are the evidence (transcript paths
above; per-arm `tool_calls` counts are in each file's frontmatter).

Score one entry per criterion below:

- correctness of the answer
- delegation-decision quality (only if the prompt is about orchestration/delegation;
  otherwise winner is `tie` with `why` saying the prompt is not an orchestration prompt)
- grounding (claims tied to files/tools actually read)
- completeness against the prompt
- cost (turns, tool calls — the cheaper arm wins ties only, never substance)

Each entry needs `winner` (an arm name from the table, or `tie`),
`margin` (`clear` / `slight` / `tie`), `evidence` (arm + verbatim quote pairs
from the arm files) and `why`.

`doesNotShow` MUST say explicitly what this comparison does not show — at minimum
that n=1 per arm on one prompt supports a hypothesis, never a model ranking.

`ladderClause` is one clause for a ± cell on the affected ladder row, stating what the
model TENDS to do — never what it did in this run. "exhaustive over a diff, slow" is a
tendency; "found 26 defects on the 5-lens audit" is a run description and is rejected.
No numbers, no dates, no arm counts, no wall-clock, no reference to this comparison.
If nothing here would recur, return an empty `ladderClause` and say why in `doesNotShow`
— an ingest that adds no clause is a correct outcome, not a failed one.
`orchestrationNote` is empty unless the finding is about delegation behaviour.
`couldNotSatisfy` names anything in this brief the judge could not satisfy, or empty.

## Output

Return ONLY the JSON object matching the schema — no prose, no markdown fences.
"""


def write_comparison(outdir: Path, slug: str, infos: list[dict]) -> Path:
    lines = ["# Arm comparison — %s" % slug, "",
             "arms: %s" % ", ".join(arm_name(i) for i in infos), "",
             "| arm | model | transport | effort | turns | tool calls | final chars |",
             "|---|---|---|---|---|---|---|"]
    for i in infos:
        lines.append("| %s | %s | %s | %s | %d | %s | %d |"
                     % (arm_name(i), i["model"], i["transport"], i["effort"],
                        i["turns"], _count(i["tool_calls"]), len(i["final"])))
    lines.append("")
    # Arms with NO recorded prompt are excluded from the equality test, not folded into it: they all
    # hash to sha256("") and would agree with each other, turning "we cannot see the prompt" into
    # "the prompt matched" — the one claim this line exists to make honestly.
    missing = [arm_name(i) for i in infos if not _norm(i["first_prompt"])]
    hashes = {hashlib.sha256(_norm(i["first_prompt"]).encode("utf-8")).hexdigest()
              for i in infos if _norm(i["first_prompt"])}
    same = len(hashes) == 1 and not missing
    lines.append("same_prompt: %s" % ("true" if same else "false"))
    if missing:
        lines.append("prompt_not_recorded: %s — these arms carry no prompt text, so `same_prompt` "
                     "cannot be true whatever the rest agree on." % ", ".join(missing))
    lines.append("same_prompt compares sha256 over whitespace-normalized first prompts "
                 "(line-wrap differences are not a prompt change); verbatim hashes are "
                 "in each arm file's frontmatter.")
    if not same:
        lines.append("first_prompt_sha256: %s"
                     % ", ".join("%s=%s" % (arm_name(i), prompt_sha(i))
                                 for i in infos))
    lines += ["", "## Adjudication", "", ADJUDICATION_PENDING, ""]
    dest = outdir / "COMPARISON.md"
    dest.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return dest


def write_judge_files(outdir: Path, slug: str, infos: list[dict]) -> tuple[Path, Path]:
    # `origin` rides on the info dict rather than a parallel list: one source can yield several arms
    # (a workflow dir), so a positional zip silently mispaired every arm after the first.
    session_lines = "\n".join(
        "- arm %s (%s; model %s, transport %s, effort %s): %s ; source: %s"
        % (i.get("label") or arm_name(i), i.get("source", "transcript"), i["model"],
           i["transport"], i["effort"],
           outdir / ("arm-%s.md" % arm_name(i)), i.get("origin", "(inline)"))
        for i in infos)
    schema_path = outdir / "judge_schema.json"
    schema_path.write_text(json.dumps(JUDGE_SCHEMA, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    brief_path = outdir / "judge_brief.md"
    brief_path.write_text(
        JUDGE_BRIEF_TMPL.format(slug=slug, session_lines=session_lines,
                                schema_path=schema_path),
        encoding="utf-8", newline="\n")
    return brief_path, schema_path


def apply_judge(comparison: Path, result: dict) -> tuple[str, str]:
    for key in JUDGE_SCHEMA["required"]:
        if key not in result:
            sys.exit("judge JSON missing required key: %s" % key)
    lines = ["## Adjudication", "", "overall: %s" % result["overall"], "",
             "| criterion | winner | margin | why |",
             "|---|---|---|---|"]
    for c in result["criteria"]:
        lines.append("| %s | %s | %s | %s |"
                     % (c.get("name", ""), c.get("winner", ""),
                        c.get("margin", ""), (c.get("why") or "").replace("\n", " ")))
    lines += ["", "### Evidence", ""]
    for c in result["criteria"]:
        for e in c.get("evidence") or []:
            # `arm` is the schema's field; `session` is what it was called before the rename, and
            # a judge result written under the old schema still has to adjudicate rather than
            # silently attribute every quote to "".
            lines.append("- %s [%s]: \"%s\""
                         % (c.get("name", ""), e.get("arm") or e.get("session", ""),
                            (e.get("quote") or "").replace("\n", " ")))
    lines += ["", "### Does not show", ""]
    lines += ["- %s" % s for s in result["doesNotShow"]] or ["- (none stated)"]
    lines += ["", "### Clauses", "",
              "ladderClause: %s" % result["ladderClause"],
              "orchestrationNote: %s" % result["orchestrationNote"], ""]
    text = comparison.read_text(encoding="utf-8")
    head, sep, _ = text.partition("## Adjudication")
    if not sep:
        sys.exit("no ## Adjudication section in %s" % comparison)
    comparison.write_text(head + "\n".join(lines), encoding="utf-8", newline="\n")
    return result["ladderClause"], result["orchestrationNote"]


def resolve(pdir: Path, prefix: str) -> Path:
    hits = sorted(p for p in pdir.glob("*.jsonl") if p.stem.startswith(prefix))
    if not hits:
        sys.exit("no transcript starts with %r under %s" % (prefix, pdir))
    if len(hits) > 1:
        print("ambiguous prefix %r matches %d transcripts: %s"
              % (prefix, len(hits), ", ".join(p.stem[:8] for p in hits)),
              file=sys.stderr)
        sys.exit(2)
    return hits[0]


# ---------------------------------------------------------------- arm sources
# Each returns the same `info` dict `extract()` returns, plus `transport`, `effort` and `source`.
# Nothing downstream branches on which one produced it.

def _is_subagent_transcript(path: Path) -> bool:
    """Is this file a SUBAGENT transcript rather than a session one?

    Decided by reading the file, not by its location: a workflow run dir, an Agent-tool spill and a
    copied-aside transcript all differ in path but agree in content -- a subagent's rows carry
    `isSidechain`. The first matching row settles it, so this stays a partial read.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"isSidechain"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") in ("user", "assistant"):
                    return bool(row.get("isSidechain"))
    except OSError:
        return False
    return False


def _transcript_arm(path: Path) -> dict:
    """A transcript arm: a session, a workflow subagent, or an Agent-tool subagent.

    A subagent has no SessionStart rails — it inherits the parent's endpoint and is handed its
    model by the dispatcher — so model, effort and label come from the sibling `.meta.json` the
    runtime writes. Falling back to the rails reader alone reported `unknown` for every fan-out arm.
    """
    sidechain = _is_subagent_transcript(path)
    info = extract(path, sidechain)
    info["transport"], rails_effort = _rails_info(path, sidechain)
    # The runtime stamps the live effort on every assistant row; that beats a rails line, and a
    # mid-session /effort shows as `a->b` (the judge must know the arm was not one effort).
    info["effort"] = "->".join(info.get("efforts_seen") or []) or rails_effort
    info["source"] = "transcript"
    info["origin"] = str(path)
    meta_path = path.with_suffix(".meta.json")
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return info
        info["source"] = meta.get("agentType") or "subagent"
        # The dispatcher's pin is the authority for a subagent: `models_seen` records the ids the
        # API actually served, which for a role pin is a resolved id the ladder does not key on.
        if meta.get("model"):
            info["model"] = meta["model"]
        if meta.get("effort"):
            info["effort"] = meta["effort"]
        if meta.get("description"):
            info["label"] = meta["description"]
        # A subagent runs on its PARENT's endpoint -- it has no rails of its own, so reading only
        # its own file reported `unknown` and a comparison table could not tell an Anthropic arm
        # from a provider one. The parent transcript sits above `subagents/`.
        if info["transport"] == "unknown":
            info["transport"] = _parent_transport(path)
    return info


def _parent_transport(path: Path) -> str:
    """The transport of the session that spawned this subagent, or `unknown`.

    Walks up to the `subagents/` boundary and reads `<session>.jsonl` beside that directory. Only
    the layout the runtime writes is understood; anything else stays `unknown` rather than guessed.
    """
    for parent in path.parents:
        if parent.name != "subagents":
            continue
        sibling = parent.parent.with_suffix(".jsonl")
        if sibling.is_file():
            return _rails_info(sibling)[0]
        break
    return "unknown"


def _record_arm(path: Path) -> dict:
    """A sidecar arm, from the `-R` record `sidecar_fanout.py` wrote beside its output.

    The record is the attestation: `servedModel` is what the endpoint reported serving, which is
    the only field that can contradict the pin. The deliverable is the sibling `<label>.out.json`
    — a launcher result envelope whose `result` holds the text.
    """
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit("cannot read sidecar record %s: %s" % (path, exc))

    label = rec.get("label") or path.stem.replace(".record", "")
    served = rec.get("servedModel") or rec.get("requestedModel") or "unknown"
    out_path = path.parent / ("%s.out.json" % label)
    final, prompt = "", ""
    if out_path.is_file():
        final, prompt = _launcher_result(out_path)

    return {
        "session": label,
        "label": label,
        "model": served,
        # A served model that disagrees with the pin is the one thing a reader must not miss.
        "models_seen": sorted({served, rec.get("requestedModel") or served}),
        "first_prompt": prompt,
        "final": final,
        "cross": [],
        "turns": rec.get("numTurns") or 0,
        # None, not 0: a launcher record does not itemize tool calls, and printing `0` told a judge
        # this arm opened no files — it read "did not look" as "looked and found nothing", then
        # scored grounding on it.
        "tool_calls": None,
        "transport": rec.get("transport") or "unknown",
        "effort": rec.get("effort") or "unknown",
        "source": "sidecar",
        "origin": str(path),
    }


def _launcher_result(path: Path) -> tuple[str, str]:
    """(final text, first prompt) from a launcher's `.out.json`.

    Three shapes ship: a single result object, a JSON array of every event (`-o json` under the
    user setting `"verbose": true`), and a JSONL stream. In the last two the last `result` event
    carries the text. All are read, because the shape depends on `-P` and the user's settings.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    events = None
    try:
        doc = json.loads(raw)
        if isinstance(doc, dict):
            return str(doc.get("result") or ""), str(doc.get("prompt") or "")
        if isinstance(doc, list):
            events = doc
    except json.JSONDecodeError:
        pass
    final, prompt = "", ""
    for line in (events if events is not None else raw.splitlines()):
        try:
            o = line if isinstance(line, dict) else json.loads(line)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if not isinstance(o, dict):
            continue
        if o.get("type") == "result" and o.get("result"):
            final = str(o["result"])
        if not prompt and o.get("type") == "user":
            prompt = _user_text(o)
    return final, prompt


def arm_infos(token: str, pdir: Path) -> list[dict]:
    """Every arm a CLI operand names. A directory fans out; everything else is one arm."""
    p = Path(token)
    if p.is_dir():
        agents = sorted(p.glob("agent-*.jsonl"))
        if not agents:
            sys.exit("no agent-*.jsonl under %s — not a workflow run dir" % p)
        return [_transcript_arm(a) for a in agents]
    if p.is_file():
        if p.name.endswith(".record.json"):
            return [_record_arm(p)]
        if p.suffix == ".jsonl":
            return [_transcript_arm(p)]
        sys.exit("%s is neither a .jsonl transcript nor a .record.json sidecar record" % p)
    # Not a path: a session id prefix, the original shape.
    return [_transcript_arm(resolve(pdir, token))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prefix", nargs="*", help="session id prefixes to compare")
    ap.add_argument("--out", required=True, help="output root; files go under <out>/<slug>/")
    ap.add_argument("--slug", default="ladder-" + date.today().isoformat())
    ap.add_argument("--apply", metavar="JUDGE_JSON",
                    help="fill ## Adjudication in COMPARISON.md from a judge result")
    ap.add_argument("--projects-dir",
                    help="transcript dir holding the .jsonl files (test hook; "
                         "default: the projects dir session_digest.py uses for this cwd)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # `basename` alone leaves `..` intact, and `<out>/..` writes a comparison one level ABOVE the
    # directory the caller named. The slug names a folder; anything that traverses is not one.
    slug = os.path.basename(a.slug.rstrip("/\\")) or a.slug
    if slug in (".", "..") or slug != os.path.basename(slug.replace("\\", "/")):
        sys.exit("--slug %r names this run's output folder: no path separator and no `..`" % a.slug)
    outdir = Path(a.out) / slug
    pdir = Path(a.projects_dir) if a.projects_dir else digest.projects_dir(os.getcwd())

    if a.apply:
        comparison = outdir / "COMPARISON.md"
        if not comparison.is_file():
            sys.exit("no COMPARISON.md at %s — run the ingest first" % comparison)
        try:
            result = json.loads(Path(a.apply).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            sys.exit("cannot read judge JSON: %s" % exc)
        clause, note = apply_judge(comparison, result)
        print("ladderClause: %s" % clause)
        print("orchestrationNote: %s" % note)
        overall = result.get("overall", "")
        print("route via /codify: ladder row %s ± clause; orchestration §5 note" % overall)
        return 0

    if len(a.prefix) < 1:
        sys.exit("need at least one arm: a session id prefix, a .jsonl transcript, "
                 "a .record.json sidecar record, or a workflow run dir")
    infos = []
    for token in a.prefix:
        infos.extend(arm_infos(token, pdir))
    if len(infos) < 2:
        print("only %d arm — a comparison needs at least two; the files below describe it but "
              "nothing is comparable" % len(infos), file=sys.stderr)

    outdir.mkdir(parents=True, exist_ok=True)
    for info in infos:
        dest = write_session_file(outdir, info)
        print("wrote %s" % dest)
    comp = write_comparison(outdir, slug, infos)
    print("wrote %s" % comp)
    brief, schema = write_judge_files(outdir, slug, infos)
    print("wrote %s" % brief)
    print("wrote %s" % schema)
    print("couldNotSatisfy: transcript tool calls are counted, not quoted — a judge "
          "checking grounding must open the transcript paths named in judge_brief.md; "
          "effort comes from the assistant rows' effort stamps (a->b when it changed mid-run), "
          "falling back to a rails line, else unknown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
