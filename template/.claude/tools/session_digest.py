#!/usr/bin/env python3
"""Session digest — the whole-session record rebuilt from the LIVE transcript (append-only across
compactions), so a session with N compactions gets the same input as one with zero.

Two readers:
  /session_end, /autolearn, /self_evaluate — a bounded prompt/friction index with stable IDs.
      The full evidence stays in JSON; fetch only rows a phase needs:
        python3 .claude/tools/session_digest.py --prompt-tail session_end
        python3 .claude/tools/session_digest.py --digest-file <json> --select U123 --select F456
  a session picking up ANOTHER session's work (after /clear, a handoff doc, a parallel session)
      — the same, plus what that session left behind: files it modified and its last message.
      `--brief` caps prompt and friction text so a day-long session lands in a few KB:
        python3 .claude/tools/session_digest.py --session <uuid-prefix> --brief
  the user pasted a session's closing message and wants THAT session — write the paste to a file
      and let token overlap find the transcript whose assistant wrote it (the terminal re-renders
      tables and strips code marks, so substring search misses; the pasted message need not be the
      session's last, since the session may have run on to a limit or a goodbye afterwards):
        python3 .claude/tools/session_digest.py --match-file .claude/scratch/digest_paste.txt --brief

Writes logs/session_digest_<sid8>.json (full record) and prints the markdown digest.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hooks"))
from _transcript_summary import TranscriptSummaryBuilder  # noqa: E402

BRIEF = {"prompt": 800, "prompt_rows": 35, "friction_rows": 25, "friction_text": 160, "outcome": 2500, "files": 60}
SESSION = {"prompt": 240, "prompt_rows": 35, "friction_rows": 20, "friction_text": 160, "outcome": 1500, "files": 40}
FULL = {"prompt": 0, "prompt_rows": 0, "friction_rows": 0, "friction_text": 200, "outcome": 4000, "files": 200}
SESSION_MAX_BYTES = 32_000


def projects_dir(cwd: str) -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _rows(path: Path, kind: str, include_sidechain: bool = False):
    """Parse complete JSON rows before filtering by type; JSON whitespace is not identity.

    Sidechain rows are a subagent's turns. Excluded by default, because in a SESSION transcript
    they are delegated work the session digest should not read as the session's own. Pass
    `include_sidechain=True` when the file IS a subagent transcript — every row there is a
    sidechain, so the default yields nothing and the file reads as empty rather than as a subagent.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("type") == kind and (include_sidechain or not row.get("isSidechain")):
                if not isinstance(row.get('message'), dict):
                    row = dict(row, message={'content': row.get('content', '')})
                yield row


def last_prompt(path: Path) -> str:
    """Last real user prompt (string content) in a transcript, or ''."""
    last = ""
    for row in _rows(path, "user"):
        c = (row.get("message") or {}).get("content")
        # Hook/slash-command echoes (`<user-prompt-submit-hook>`, `<command-name>`, `<local-command-…>`)
        # are user rows too; a /clear stub is nothing but them.
        if isinstance(c, str) and not c.lstrip().startswith(("<", "[Request")):
            last = c
    return last


def last_assistant_text(path: Path, min_len: int = 40) -> str:
    """The assistant's last substantive text block — the message the session ended on, which is
    where a handoff, a recap or an open question lives."""
    last = ""
    for row in _rows(path, "assistant"):
        content = (row.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = (block.get("text") or "").strip()
                if len(t) >= min_len:
                    last = t
    return last


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{4,}")
MATCH_MIN = 0.6
MATCH_SCAN = 40


def _tokens(text: str) -> set[str]:
    """Distinctive words (5+ chars, case-folded). Markdown, punctuation and table pipes fall away,
    which is what survives the terminal re-rendering a pasted message."""
    return {w.lower() for w in _WORD.findall(text or "")}


def assistant_blocks(path: Path, min_len: int = 200):
    for row in _rows(path, "assistant"):
        content = (row.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = (block.get("text") or "").strip()
                if len(t) >= min_len:
                    yield t


def match_transcript(pdir: Path, text: str, scan: int = MATCH_SCAN) -> tuple[Path, list[tuple[float, Path]]]:
    """The transcript whose assistant wrote `text` (a pasted closing message): best single assistant
    block by token overlap across the newest `scan` transcripts. Assistant blocks only — the session
    that RECEIVED the paste holds it in a user row and must not match itself."""
    want = _tokens(text)
    if len(want) < 8:
        sys.exit(f"match text has {len(want)} distinctive words; paste at least a paragraph")
    files = sorted(pdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:scan]
    scored = []
    for p in files:
        best = 0.0
        for block in assistant_blocks(p):
            score = len(want & _tokens(block)) / len(want)
            if score > best:
                best = score
        scored.append((best, p))
    scored.sort(key=lambda s: -s[0])
    if not scored or scored[0][0] < MATCH_MIN:
        ranked = "\n".join(f"  {p.stem[:8]}  {s:.2f}" for s, p in scored[:3])
        sys.exit(f"no assistant message overlaps the paste at >= {MATCH_MIN:.0%} of its words "
                 f"(newest {len(scored)} transcripts); nearest:\n{ranked}\npass --session <id>")
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        sys.exit('ambiguous assistant-message match; pass an exact --session <id>')
    return scored[0][1], scored[:3]


def list_sessions(pdir: Path, n: int) -> str:
    """The newest N transcripts, one line each: prefix, last write, size, first real prompt. The
    listing order is modification time, not session ownership. --previous anchors to active identity."""
    import datetime
    files = sorted(pdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    out = []
    for i, p in enumerate(files):
        st = p.stat()
        when = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")
        first = ""
        for row in _rows(p, "user"):
            c = (row.get("message") or {}).get("content") or []
            text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            if text and not text.lstrip().startswith("<"):
                first = clip(" ".join(text.split()), 110)
                break
        tag = "  (active session)" if p.stem == os.environ.get("CLAUDE_CODE_SESSION_ID") else ""
        out.append(f"{p.stem[:8]}  {when}  {st.st_size // 1024:>6} KB  {first}{tag}")
    return "\n".join(out)


def pick_transcript(pdir: Path, session: str | None, prompt_tail: str | None, previous: bool = False) -> Path:
    """Use exact ownership; discovery modes never silently resolve ambiguous matches."""
    files = sorted(pdir.glob('*.jsonl'), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        sys.exit(f'no transcripts under {pdir}')
    if session:
        exact = [path for path in files if path.stem == session]
        hits = exact or [path for path in files if path.stem.startswith(session)]
        if len(hits) != 1:
            sys.exit(f'{len(hits)} transcripts match {session!r}; pass an exact session id')
        return hits[0]
    if previous:
        active = (os.environ.get('CLAUDE_CODE_SESSION_ID') or '').strip()
        anchor = next((path for path in files if path.stem == active), None)
        if anchor is None:
            sys.exit('active session is missing or unknown; pass --session <id>')
        for path in files[files.index(anchor) + 1:]:
            if last_prompt(path):
                return path
        sys.exit('no previous transcript with a user prompt; pass --session <id>')
    env_sid = (os.environ.get('CLAUDE_CODE_SESSION_ID') or '').strip()
    if env_sid:
        mine = [path for path in files if path.stem == env_sid]
        if len(mine) == 1:
            return mine[0]
        sys.exit(f'active session {env_sid!r} is missing under {pdir}; refusing peer fallback')
    if prompt_tail:
        hits = [path for path in files if prompt_tail in last_prompt(path)]
        if len(hits) == 1:
            return hits[0]
        sys.exit(f'{len(hits)} transcripts match the prompt selector; pass --session <id>')
    sys.exit('session identity is unknown; pass --session <id>')


def _tool_counts(path: Path, *, include_sidechain: bool = False) -> dict:
    """{tool name: calls} over one transcript's assistant tool_use blocks."""
    counts: dict = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "tool_use" not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (not isinstance(row, dict) or row.get("type") != "assistant" or row.get('isMeta')
                    or (row.get('isSidechain') and not include_sidechain)):
                continue
            message = row.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name") or "?"
                    counts[name] = counts.get(name, 0) + 1
    return counts


def tool_census(path: Path) -> dict:
    """Every tool call the session made, by name, split by WHO made it: the session model (main
    transcript) and its Workflow/Agent subagents (`<pdir>/<sid>/subagents/**/*.jsonl`).

    The main transcript alone under-counts: a session whose lenses did the reading shows zero
    worker calls at the top level while its subagents made a dozen. Sidecar children run outside
    this directory and are not counted here — their record is the `-R` file each dispatch wrote.
    """
    main = _tool_counts(path)
    sub: dict = {}
    sub_dir = path.parent / path.stem / "subagents"
    files = sorted(sub_dir.rglob("*.jsonl")) if sub_dir.is_dir() else []
    n = 0
    for p in files:
        c = _tool_counts(p, include_sidechain=True)
        if not c:
            continue  # workflow journals carry no tool_use rows
        n += 1
        for k, v in c.items():
            sub[k] = sub.get(k, 0) + v
    return {"main": main, "subagents": sub, "subagent_transcripts": n,
            "main_total": sum(main.values()), "subagent_total": sum(sub.values())}


def render_tools(census: dict) -> list:
    names = sorted(set(census["main"]) | set(census["subagents"]),
                   key=lambda k: -(census["main"].get(k, 0) + census["subagents"].get(k, 0)))
    out = ["", f"## Tool calls — main {census['main_total']} · subagents {census['subagent_total']} "
               f"in {census['subagent_transcripts']} transcript(s) (sidecar children not included)",
           "| tool | main | subagents |", "|---|---|---|"]
    out += [f"| {k} | {census['main'].get(k, 0)} | {census['subagents'].get(k, 0)} |" for k in names]
    return out


def clip(text: str, n: int) -> str:
    text = text or ""
    if n and len(text) > n:
        return text[:n].rstrip() + f" …[+{len(text) - n} chars]"
    return text


def _evidence_id(prefix: str, row: dict) -> str:
    return prefix + str(row.get("index"))


def build_evidence_index(d: dict) -> dict:
    """Compact, complete lookup index for the full durable digest."""
    prompts = []
    for row in d.get("user_messages") or []:
        content = row.get("content") or ""
        prompts.append({
            "id": _evidence_id("U", row), "timestamp": row.get("timestamp"),
            "signals": row.get("signals") or [], "chars": len(content),
            "excerpt": clip(" ".join(content.split()), 160),
        })
    friction = []
    for row in d.get("friction") or []:
        error = row.get("error") or ""
        friction.append({
            "id": _evidence_id("F", row), "timestamp": row.get("timestamp"),
            "tool": row.get("tool") or "unknown", "denied": bool(row.get("denied")),
            "has_response": bool(row.get("response")), "error_chars": len(error),
            "excerpt": clip(" ".join(error.split()), 160),
        })
    return {"schema_version": "1.0", "session_id": d.get("session_id"),
            "prompts": prompts, "friction": friction}


def select_evidence(d: dict, ids: list[str]) -> list[dict]:
    """Return exact full rows by stable source-line ID; unknown IDs fail loudly."""
    available = {}
    for kind, prefix, key in (("prompt", "U", "user_messages"), ("friction", "F", "friction")):
        for row in d.get(key) or []:
            evidence_id = _evidence_id(prefix, row)
            if evidence_id in available:
                raise ValueError(f"duplicate evidence id: {evidence_id}")
            available[evidence_id] = {"id": evidence_id, "kind": kind, "row": row}
    missing = [evidence_id for evidence_id in ids if evidence_id not in available]
    if missing:
        raise ValueError("unknown evidence id(s): " + ", ".join(missing))
    return [available[evidence_id] for evidence_id in ids]


def evidence_page(index: dict, kind: str, page: int, page_size: int) -> dict:
    """Return one bounded page from the compact evidence index."""
    if kind not in ("prompts", "friction"):
        raise ValueError("evidence kind must be prompts or friction")
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise ValueError("page size must be between 1 and 50")
    rows = index.get(kind) or []
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    if page > pages:
        raise ValueError(f"page {page} exceeds {pages}")
    start = (page - 1) * page_size
    return {"kind": kind, "page": page, "pages": pages, "total": len(rows),
            "rows": rows[start:start + page_size]}


def write_json_atomic(path: Path, value: dict) -> None:
    """Publish JSON without exposing a partial target."""
    payload = (json.dumps(value, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".session-digest-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _prompt_preview(rows: list[dict], cap: int) -> list[dict]:
    if not cap or len(rows) <= cap:
        return rows
    selected = {row.get("index"): row for row in rows[:5] + rows[-20:]}
    for row in reversed(rows):
        if len(selected) >= cap:
            break
        if row.get("signals"):
            selected.setdefault(row.get("index"), row)
    return sorted(selected.values(), key=lambda row: row.get("index", -1))


def render(d: dict, path: Path, outcome: str, caps: dict, project_dir: str = "", tools: bool = False) -> str:
    m = d["metadata"]
    root = os.path.normpath(project_dir).replace("/", "\\") + "\\" if project_dir else ""
    files = {(p[len(root):] if root and p.replace("/", "\\").startswith(root) else p): c
             for p, c in (d.get("files_modified_counts") or {}).items()}
    census = d.get("tool_census") or {}
    sub_note = (f" (+{census['subagent_total']} in {census['subagent_transcripts']} subagent transcripts; --tools for the table)"
                if census.get("subagent_total") else "")
    prompt_rows = _prompt_preview(d["user_messages"], caps["prompt_rows"])
    out = [f"# Session digest — {d['session_id'][:8]}  ({path.name})",
           f"messages {m['total_messages']} · tool calls {m['total_tool_calls']}{sub_note} · duration {(m['duration_seconds'] or 0) // 60} min · "
           f"compactions {d['compactions']['count']} · user prompts {len(d['user_messages'])} · friction rows {len(d['friction'])} · "
           f"files modified {len(files)}",
           "", "## User prompt index" + (f" — {len(prompt_rows)} of {len(d['user_messages'])}; select full rows by ID" if len(prompt_rows) < len(d["user_messages"]) else "")]
    for u in prompt_rows:
        tags = ",".join(u["signals"]) or "-"
        ts = (u.get("timestamp") or "")[11:16]
        out.append(f"- [{_evidence_id('U', u)} {ts} {tags}] {clip(u['content'], caps['prompt'])}")
    fr = d["friction"]
    shown = fr[-caps["friction_rows"]:] if caps["friction_rows"] else fr
    out += ["", "## Friction — tool errors / denials / interrupts, each with the assistant's next move"
            + (f" (last {len(shown)} of {len(fr)})" if len(shown) < len(fr) else "")]
    if not fr:
        out.append("(none)")
    n = caps["friction_text"]
    for f in shown:
        kind = "DENIED" if f["denied"] else "error"
        out.append(f"- [{_evidence_id('F', f)} {kind}] {f['tool']}: `{f['input'][:120]}`\n  error: {f['error'][:n]}\n  next: {(f.get('response') or '(no text before the next prompt)')[:n]}")
    out += ["", f"## Files modified ({len(files)}) — edit/write count each"]
    items = sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))
    out += [f"- {p} ×{c}" for p, c in items[:caps["files"]]] or ["(none)"]
    if len(items) > caps["files"]:
        out.append(f"- … +{len(items) - caps['files']} more (full list in the JSON)")
    if tools and census:
        out += render_tools(census)
    out += ["", "## Outcome — the session's last message", clip(outcome, caps["outcome"]) or "(no assistant text)"]
    out += ["", f"Full JSON: logs/session_digest_{d['session_id'][:8]}.json",
            f"Evidence index: logs/session_digest_{d['session_id'][:8]}.index.json",
            "Page summaries: session_digest.py --digest-file <full-json> --evidence-page friction --page <N>",
            "Select exact rows: session_digest.py --digest-file <full-json> --select <ID> [--select <ID> ...]"]
    text = "\n".join(out)
    if caps == SESSION and len(text.encode("utf-8")) > SESSION_MAX_BYTES:
        footer_at = text.rfind("\nFull JSON:")
        footer = text[footer_at:] if footer_at >= 0 else ""
        marker = "\n\n[OUTPUT TRUNCATED at 32,000 bytes; page summaries or select exact rows by ID.]"
        budget = SESSION_MAX_BYTES - len((marker + footer).encode("utf-8"))
        prefix = text.encode("utf-8")[:max(0, budget)].decode("utf-8", errors="ignore").rstrip()
        text = prefix + marker + footer
    return text


def render_context(d: dict) -> str:
    census = d.get("context_census") or {}
    boundaries = census.get("boundaries", [])
    boundary_count = census.get("boundary_count", len(boundaries))
    first = census.get("first_assistant") or {}
    known = lambda value: "unknown" if value is None else str(value)
    sid = str(d.get("session_id") or "unknown")[:8]
    out = [f"# Context census — {sid}",
           f"Main assistant messages: {census.get('assistant_messages', 0)}; compact boundaries: {boundary_count}",
           f"First assistant: {first.get('model') or 'unknown'}; reported input sum: {known(first.get('reported_input_sum'))}",
           "Usage is provider-reported. UTF-8 bytes below are cumulative evidence, not live context.",
           "", "| Usage field | Observed total | Measured messages | Missing |", "|---|---:|---:|---:|"]
    for field, counts in census.get("usage", {}).items():
        out.append(f"| {field} | {known(counts['observed_total'])} | {counts['measured_messages']} | {counts['missing_messages']} |")
    shown = boundaries[-10:]
    location = "full set in JSON" if boundary_count == len(boundaries) else f"{boundary_count - len(boundaries)} absent from this backup"
    out += ["", f"## Compact boundaries (last {len(shown)} of {boundary_count}; {location})",
            "| Timestamp | Pre | Post | Next reported input | Duration ms |", "|---|---:|---:|---:|---:|"]
    for boundary in shown:
        next_message = boundary.get("next_assistant") or {}
        values = [boundary.get('timestamp'), boundary.get('pre_tokens'), boundary.get('post_tokens'),
                  next_message.get('reported_input_sum'), boundary.get('duration_ms')]
        out.append("| " + " | ".join(known(value) for value in values) + " |")
    results = census.get("tool_results") or {}
    out += ["", f"Tool result text: {known(results.get('text_utf8_bytes'))} UTF-8 bytes in {results.get('count', 0)} results."]
    tools = sorted(results.get("by_tool", {}).items(), key=lambda pair: -(pair[1]['text_utf8_bytes'] or 0))
    out += [f"- {name}: {known(counts['text_utf8_bytes'])} text bytes; {counts['missing_text_results']} missing text measurements"
            for name, counts in tools[:6]]
    out += ["", "Coverage: " + json.dumps(census.get("coverage", {}), sort_keys=True),
            f"Full context JSON: logs/session_digest_{sid}.context.json"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="session id or unique prefix")
    ap.add_argument("--prompt-tail", help="text the session's latest real prompt must contain (e.g. session_end)")
    ap.add_argument("--project-dir", default=os.getcwd(), help="repo root the session ran in (default cwd)")
    ap.add_argument("--brief", action="store_true",
                    help="clip prompts to %d chars, keep the last %d friction rows — the continuation shape"
                         % (BRIEF["prompt"], BRIEF["friction_rows"]))
    ap.add_argument("--full", action="store_true", help="print every full prompt and friction row")
    ap.add_argument("--digest-file", metavar="PATH", help="saved full JSON used by --select")
    ap.add_argument("--select", action="append", metavar="ID",
                    help="print one exact U<prompt-index> or F<friction-index> row; repeatable")
    ap.add_argument("--evidence-page", choices=("prompts", "friction"),
                    help="print one bounded page from the compact evidence index")
    ap.add_argument("--page", type=int, default=1, help="evidence page number (default 1)")
    ap.add_argument("--page-size", type=int, default=20,
                    help="evidence rows per page, 1-50 (default 20)")
    ap.add_argument("--json-only", action="store_true", help="print only the JSON path")
    ap.add_argument("--context-only", action="store_true",
                    help="print bounded context accounting; skip outcome and child-transcript scans")
    ap.add_argument("--previous", action="store_true",
                    help="the nearest older prompt-bearing transcript before the exact active session")
    ap.add_argument("--list", type=int, metavar="N", help="list the newest N transcripts and exit")
    ap.add_argument("--match", metavar="TEXT",
                    help="pick the transcript whose assistant wrote TEXT (a pasted closing message); token overlap, "
                         "so re-rendered markdown still matches")
    ap.add_argument("--match-file", metavar="PATH", help="like --match, text read from PATH (long pastes, quotes)")
    ap.add_argument("--tools", action="store_true",
                    help="print the per-tool call census, main transcript and subagent transcripts separately")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.select or a.evidence_page:
        if not a.digest_file:
            ap.error("--select/--evidence-page requires --digest-file")
        if a.select and a.evidence_page:
            ap.error("--select and --evidence-page are mutually exclusive")
        saved = json.loads(Path(a.digest_file).read_text(encoding="utf-8"))
        try:
            value = (select_evidence(saved, a.select) if a.select else
                     evidence_page(build_evidence_index(saved), a.evidence_page,
                                   a.page, a.page_size))
            print(json.dumps(value, indent=1, ensure_ascii=True))
        except ValueError as exc:
            ap.error(str(exc))
        return
    if a.digest_file:
        ap.error("--digest-file requires --select or --evidence-page")
    if a.brief and a.full:
        ap.error("--brief and --full are mutually exclusive")

    pdir = projects_dir(a.project_dir)
    if a.list:
        print(list_sessions(pdir, a.list))
        return
    if a.match or a.match_file:
        text = Path(a.match_file).read_text(encoding="utf-8", errors="replace") if a.match_file else a.match
        path, ranked = match_transcript(pdir, text)
        print("MATCH " + "  ".join(f"{p.stem[:8]}={s:.2f}" for s, p in ranked) + "  (first is used)")
    else:
        path = pick_transcript(pdir, a.session, a.prompt_tail, a.previous)
    b = TranscriptSummaryBuilder(path.stem, str(path), full_evidence=not a.context_only)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            b.process_line(line)
    d = b.finalize(backup_limits=False)
    outcome = ""
    if not a.context_only:
        outcome = last_assistant_text(path)
        d["outcome_last_assistant_text"] = outcome
        d["tool_census"] = tool_census(path)
    else:
        d = {"session_id": path.stem, "context_census": d["context_census"],
             "omitted_scans": ["outcome", "child_tool_census"]}

    logs = Path(a.project_dir) / "logs"
    logs.mkdir(exist_ok=True)
    suffix = ".context" if a.context_only else ""
    out = logs / f"session_digest_{path.stem[:8]}{suffix}.json"
    write_json_atomic(out, d)
    if not a.context_only:
        index_out = logs / f"session_digest_{path.stem[:8]}.index.json"
        write_json_atomic(index_out, build_evidence_index(d))
    if a.json_only:
        print(out)
    elif a.context_only:
        print(render_context(d))
    else:
        caps = BRIEF if a.brief else (FULL if a.full else SESSION)
        print(render(d, path, outcome, caps, a.project_dir, a.tools))


if __name__ == "__main__":
    main()
