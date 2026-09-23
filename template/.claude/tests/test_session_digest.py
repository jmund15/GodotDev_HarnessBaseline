#!/usr/bin/env python3
"""Proof for tools/session_digest.py transcript selection — `--previous`, `--list`, `--session`.

The failure this guards: `--previous` picked the /clear stub (a transcript holding only hook and
slash-command echoes) instead of the session the user left. A stub carries user rows whose content
starts with `<command-name>` / `<user-prompt-submit-hook>`; those must read as no prompt.

Run: python3 .claude/tests/test_session_digest.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "session_digest.py")
spec = importlib.util.spec_from_file_location("sd", TOOL)
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)


def user_row(content):
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}})


def assistant_row(text):
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                        "content": [{"type": "text", "text": text}]}})


def write_transcript(d, name, rows, mtime):
    p = Path(d) / f"{name}.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def main():
    fails = []
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        older = write_transcript(d, "aaaa1111-old", [user_row("please close out the benchmark")], now - 300)
        stub = write_transcript(d, "bbbb2222-stub", [
            user_row("<command-name>/clear</command-name>"),
            user_row("<user-prompt-submit-hook>x</user-prompt-submit-hook>"),
        ], now - 200)
        newest = write_transcript(d, "cccc3333-new", [user_row("digest the last session")], now - 100)
        pdir = Path(d)

        # --previous anchors to this session and skips the stub.
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": newest.stem}):
            got = sd.pick_transcript(pdir, None, None, previous=True)
            if got != older:
                fails.append(f"--previous picked {got.name}, expected {older.name}")
            if sd.pick_transcript(pdir, None, None) != newest:
                fails.append("default pick did not honor active identity")

        # --session by prefix.
        if sd.pick_transcript(pdir, "aaaa", None) != older:
            fails.append("--session prefix did not resolve")

        # A stub has no prompt; a hook echo is not a prompt.
        if sd.last_prompt(stub) != "":
            fails.append(f"stub last_prompt should be empty, got {sd.last_prompt(stub)!r}")
        if sd.last_prompt(older) != "please close out the benchmark":
            fails.append("last_prompt lost the real prompt")

        # Recency orders the list; it never establishes active or previous identity.
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": older.stem}):
            lines = sd.list_sessions(pdir, 5).splitlines()
        if (len(lines) != 3 or not lines[0].startswith("cccc3333")
                or "(active session)" not in lines[2]
                or any("(active session)" in line for line in lines[:2])
                or any("(this session?)" in line or "(--previous)" in line for line in lines)):
            fails.append("list_sessions shape wrong:\n" + "\n".join(lines))

        # --match: a pasted closing message finds the session whose ASSISTANT wrote it, even after the
        # terminal re-rendered its table and stripped code marks; the session that received the paste
        # (user row) does not match itself; a weak overlap exits instead of guessing.
        closing = ("| Finding | Status |\n|---|---|\n| F1 roaches fall through the floor | Unproven, two "
                   "`[DIAG-fall]` lines added |\n| F2 group spawned into the void | Fixed: falls back onto "
                   "the room's anchors, refuses without floor |\n\nClose the Godot editor, then say go. "
                   "Verification blocked because the runner refuses to build under the editor.")
        rendered = closing.replace("|", " ").replace("`", "").replace("---", "")
        author = write_transcript(d, "eeee5555-author", [
            user_row("fix the playtest findings"),
            assistant_row(closing),
            assistant_row("You've reached your limit. Run /usage-credits to continue or switch models."),
        ], now - 250)
        write_transcript(d, "ffff6666-receiver", [user_row("digest this: " + rendered)], now - 50)
        got, ranked = sd.match_transcript(pdir, rendered)
        if got != author or ranked[0][0] < sd.MATCH_MIN:
            fails.append(f"--match picked {got.name} at {ranked[0][0]:.2f}, expected {author.name}")
        try:
            sd.match_transcript(pdir, "completely unrelated words about benchmark ceilings scoring boards results arms")
            fails.append("--match on unrelated text should exit")
        except SystemExit:
            pass

        # Only one transcript: --previous refuses instead of returning the current session.
        with tempfile.TemporaryDirectory() as d2:
            write_transcript(d2, "dddd4444-only", [user_row("hi")], now)
            try:
                with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "dddd4444-only"}):
                    sd.pick_transcript(Path(d2), None, None, previous=True)
                fails.append("--previous with one transcript should exit")
            except SystemExit:
                pass

    # --- THIS session, never the newest one -------------------------------
    # Concurrent sessions share the transcript directory, and a peer that is actively writing always
    # has the newer mtime. --prompt-tail does not disambiguate them: two sessions both running
    # /session_end both match. Measured 2026-09-08: a /session_end digested a peer's whole session.
    with tempfile.TemporaryDirectory() as d:
        now = time.time()
        mine = "aaaa1111-1111-1111-1111-111111111111"
        peer = "bbbb2222-2222-2222-2222-222222222222"
        # The peer is NEWER and its last prompt also ends in /session_end -- both tiebreakers point
        # at it, so only the env id can pick correctly.
        write_transcript(d, mine, [user_row("do the work"), user_row("/session_end")], now - 600)
        write_transcript(d, peer, [user_row("other work"), user_row("/session_end")], now)
        pdir = Path(d)
        prev = os.environ.get("CLAUDE_CODE_SESSION_ID")
        try:
            os.environ["CLAUDE_CODE_SESSION_ID"] = mine
            got = sd.pick_transcript(pdir, None, None)
            if got.stem != mine:
                fails.append(f"no-arg picked {got.stem}, expected THIS session {mine}")
            got = sd.pick_transcript(pdir, None, "session_end")
            if got.stem != mine:
                fails.append(f"--prompt-tail picked {got.stem}; the env id must outrank mtime")
            # An explicit --session still wins: digesting another session is a real use.
            got = sd.pick_transcript(pdir, peer[:8], None)
            if got.stem != peer:
                fails.append("--session must outrank CLAUDE_CODE_SESSION_ID")
            # An env id with no transcript must not silently become "the newest one is fine".
            os.environ["CLAUDE_CODE_SESSION_ID"] = "cccc3333-nope"
            try:
                sd.pick_transcript(pdir, None, None)
                fails.append("stale identity selected a peer")
            except SystemExit:
                pass
            # Missing identity is unknown, not permission to choose the newest peer.
            del os.environ["CLAUDE_CODE_SESSION_ID"]
            try:
                sd.pick_transcript(pdir, None, None)
                fails.append("missing identity selected a peer")
            except SystemExit:
                pass
        finally:
            if prev is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = prev

    # --- tool census counts the SUBAGENTS too ---------------------------------
    # A session whose lenses did the reading shows zero worker calls in its main transcript while
    # its subagents made a dozen; the census must count both and say which is which.
    def tool_row(name):
        return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": name, "input": {}}]}})
    with tempfile.TemporaryDirectory() as d:
        sid = "abcd9999-9999-9999-9999-999999999999"
        main = write_transcript(d, sid, [user_row("go"), tool_row("Read"), tool_row("Read"), tool_row("Bash")], now)
        sub = Path(d) / sid / "subagents" / "workflows" / "wf_1"
        sub.mkdir(parents=True)
        (sub / "agent-a.jsonl").write_text(tool_row("mcp__ai-worker__read_files") + "\n" + tool_row("Grep") + "\n", encoding="utf-8")
        (sub / "journal.jsonl").write_text(json.dumps({"type": "started", "label": "x"}) + "\n", encoding="utf-8")
        census = sd.tool_census(main)
        if census["main"] != {"Read": 2, "Bash": 1} or census["main_total"] != 3:
            fails.append(f"main census wrong: {census['main']}")
        if census["subagents"] != {"mcp__ai-worker__read_files": 1, "Grep": 1} or census["subagent_transcripts"] != 1:
            fails.append(f"subagent census wrong: {census['subagents']} in {census['subagent_transcripts']}")
        table = "\n".join(sd.render_tools(census))
        if "| mcp__ai-worker__read_files | 0 | 1 |" not in table:
            fails.append("render_tools did not attribute the worker call to subagents:\n" + table)

    # --- full evidence stays durable; model-facing output stays bounded -------
    prompts = [
        {"index": i, "timestamp": f"2026-09-11T00:{i % 60:02d}:00Z",
         "content": (f"prompt-{i}-" + "x" * 500),
         "signals": (["correction"] if i % 7 == 0 else []), "matched_patterns": []}
        for i in range(200)
    ]
    friction = [
        {"index": 1000 + i, "timestamp": None, "tool": "Bash", "input": "cmd " + "i" * 300,
         "input_full": "cmd " + "i" * 1200, "error": "err " + "e" * 500,
         "denied": i % 2 == 0, "response": "next " + "n" * 500}
        for i in range(80)
    ]
    digest = {
        "session_id": "feedface-1111-2222-3333-444444444444",
        "metadata": {"total_messages": 500, "total_tool_calls": 20, "duration_seconds": 60},
        "user_messages": prompts, "friction": friction, "compactions": {"count": 2},
        "files_modified_counts": {}, "tool_census": {}, "errors": {"unresolved": []},
    }
    try:
        index = sd.build_evidence_index(digest)
        if len(index["prompts"]) != 200 or len(index["friction"]) != 80:
            fails.append("evidence index omitted durable prompt or friction rows")
        selected = sd.select_evidence(digest, ["U7", "F1003"])
        if (selected[0]["row"] != prompts[7] or selected[1]["row"] != friction[3]):
            fails.append("selected evidence did not round-trip exact full rows")
        page = sd.evidence_page(index, "friction", 2, 20)
        if page["page"] != 2 or page["pages"] != 4 or len(page["rows"]) != 20:
            fails.append("bounded evidence page metadata or size is wrong")
        pages = [sd.evidence_page(index, "friction", n, 20) for n in range(1, 5)]
        ids = [row["id"] for part in pages for row in part["rows"]]
        if len(ids) != len(set(ids)) or set(ids) != {row["id"] for row in index["friction"]}:
            fails.append("evidence pages duplicate or omit friction rows")
        if sd.evidence_page(index, "friction", 2, 20) != page:
            fails.append("repeated evidence paging is not deterministic")
        try:
            sd.evidence_page(index, "friction", 5, 20)
            fails.append("out-of-range evidence page should fail loudly")
        except ValueError:
            pass
        try:
            sd.select_evidence(digest, ["U9999"])
            fails.append("unknown evidence id should fail loudly")
        except ValueError:
            pass
        rendered = sd.render(digest, Path("session.jsonl"), "done", sd.SESSION, tools=False)
        if len(rendered.encode("utf-8")) > sd.HANDOFF_MAX_BYTES:
            fails.append(f"current-session handoff exceeded byte cap: {len(rendered.encode('utf-8'))}")
        if "U0" not in rendered or "U199" not in rendered or "F1079" not in rendered:
            fails.append("bounded handoff lost first/last stable evidence ids")
        digest["files_modified_counts"] = {"界" * 50000: 1}
        oversized = sd.render(digest, Path("session.jsonl"), "done", sd.SESSION, tools=False)
        # Migrated from the retired small-overview SESSION contract: the default handoff sheds
        # optional rows and keeps the retrieval footer under HANDOFF_MAX_BYTES.
        if len(oversized.encode("utf-8")) > sd.HANDOFF_MAX_BYTES or "--select" not in oversized:
            fails.append("unusual path lengths bypassed the handoff byte cap or lost retrieval")
        with tempfile.TemporaryDirectory() as atomic_dir:
            target = Path(atomic_dir) / "digest.json"
            target.write_text("old", encoding="utf-8")
            try:
                with patch.object(sd.os, "replace", side_effect=OSError("planted")):
                    sd.write_json_atomic(target, {"new": True})
                fails.append("atomic digest publication should surface replace failure")
            except OSError:
                pass
            if target.read_text(encoding="utf-8") != "old" or list(Path(atomic_dir).glob(".session-digest-*")):
                fails.append("failed digest publication changed the target or leaked its temp file")
    except AttributeError as exc:
        fails.append(f"bounded evidence API missing: {exc}")

    for f in fails:
        print("FAIL", f)
    print("test_session_digest: %d checks, %d fail" % (22, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
