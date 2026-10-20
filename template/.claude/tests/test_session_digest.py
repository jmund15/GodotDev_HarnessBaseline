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


def _parses(row):
    """The builder SKIPS an unparseable line, so a transcribed fixture row must be checked here."""
    try:
        return isinstance(json.loads(row), dict)
    except ValueError:
        return False


# A live transcript's row shapes (field set and order unchanged; project path, session id and prose
# neutralised): a prose prompt, two model-a/high turns, a `<synthetic>` turn, the plumbed `/model`
# command, and "continue".
LIVE_ROWS_MIXED = [
    r"""{"parentUuid":"25264d52-11ec-4dce-bebc-5524a78ec374","isSidechain":false,"promptId":"7b498c1c-38e2-4ca5-ade9-69fe81b73837","type":"user","message":{"role":"user","content":"In the sample repo, the file src/Stats/Modifier.cs declares a class whose name does not match its file name. This raises a duplicate-key exception on every editor start.\n\nTask: rename the file to match the class following the refactor procedure. Notes: (1) the file lives inside a nested repository, so commit there first and then bump the parent pointer; (2) move the .cs.uid sidecar alongside via git mv; (3) update every path reference to the old file name; (4) run the build and the regression gate."},"uuid":"dcefc9fc-945f-41cd-8119-741c09ce3402","timestamp":"2026-07-24T12:56:12.181Z","permissionMode":"auto","origin":{"kind":"human"},"promptSource":"sdk","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.217","gitBranch":"main"}""",
    r"""{"parentUuid":"4a909478-c780-41dd-a505-2719f27bdeac","isSidechain":false,"message":{"model":"model-a","id":"msg_011CdLvG4KmTAnmjATe6XkEc","type":"message","role":"assistant","content":[{"type":"text","text":"I'll start by loading the refactor skill and searching memory for refactoring gotchas."}],"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":45495,"cache_read_input_tokens":28034,"output_tokens":531,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":45495,"ephemeral_5m_input_tokens":0},"inference_geo":"not_available","iterations":[{"input_tokens":2,"output_tokens":531,"cache_read_input_tokens":28034,"cache_creation_input_tokens":45495,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":45495},"type":"message"}],"speed":"standard"},"diagnostics":null},"requestId":"req_011CdLvG2PgoMfUftcMQhMDU","type":"assistant","uuid":"9a673795-bfbb-43ec-9cfe-1c157feaab00","timestamp":"2026-07-24T12:56:26.916Z","effort":"high","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.217","gitBranch":"main"}""",
    r"""{"parentUuid":"15136698-be8d-44e8-895a-bbaea0891183","isSidechain":false,"message":{"model":"model-a","id":"msg_011CdLvMZJRytuXpKwE31nmv","type":"message","role":"assistant","content":[{"type":"text","text":"Both renames staged cleanly (git detected them as `R` renames, preserving history). Now building:"}],"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":1758,"cache_read_input_tokens":95049,"output_tokens":233,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":1758,"ephemeral_5m_input_tokens":0},"inference_geo":"not_available","iterations":[{"input_tokens":2,"output_tokens":233,"cache_read_input_tokens":95049,"cache_creation_input_tokens":1758,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":1758},"type":"message"}],"speed":"standard"},"diagnostics":null},"requestId":"req_011CdLvMXCvMyjjxHSauR9qP","attributionSkill":"refactor_procedure","attributionMcpServer":"plugin:semantic-search:semantic-search","attributionMcpTool":"search","type":"assistant","uuid":"864951aa-4525-4b03-af1f-de751243e673","timestamp":"2026-07-24T12:57:31.520Z","effort":"high","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.217","gitBranch":"main"}""",
    r"""{"parentUuid":"e6e0c944-2b81-485b-85de-02e807359163","isSidechain":false,"type":"assistant","uuid":"1c5dc638-37c0-4cc5-a9c5-2f687aadd9b9","timestamp":"2026-07-24T22:06:44.449Z","message":{"id":"1521ae51-d2b4-471c-95c6-5677c4ec97d1","container":null,"model":"<synthetic>","role":"assistant","stop_details":null,"stop_reason":"stop_sequence","stop_sequence":"","type":"message","usage":{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":null,"cache_creation":{"ephemeral_1h_input_tokens":0,"ephemeral_5m_input_tokens":0},"inference_geo":null,"iterations":null,"speed":null},"content":[{"type":"text","text":"No response requested."}],"context_management":null},"isApiErrorMessage":false,"userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.219","gitBranch":"main"}""",
    r"""{"parentUuid":"c72b7d48-5ea9-449c-b529-d34ba532b0d4","isSidechain":false,"promptId":"b2851b5a-fc43-4230-8bb5-81b3358a9e91","type":"user","message":{"role":"user","content":"<local-command-caveat>Caveat: The messages below were generated by the user while running local commands. DO NOT respond to these messages or otherwise consider them in your response unless the user explicitly asks you to.</local-command-caveat>"},"isMeta":true,"uuid":"bc754e63-8da4-4103-b5eb-624d4e0f9f2f","timestamp":"2026-07-24T22:06:47.255Z","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.219","gitBranch":"main"}""",
    r"""{"parentUuid":"bc754e63-8da4-4103-b5eb-624d4e0f9f2f","isSidechain":false,"promptId":"b2851b5a-fc43-4230-8bb5-81b3358a9e91","type":"user","message":{"role":"user","content":"<command-name>/model</command-name>\n            <command-message>model</command-message>\n            <command-args>model-b</command-args>"},"uuid":"91b4192a-070f-4e6d-b27b-6351811bdb2f","timestamp":"2026-07-24T22:06:47.255Z","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.219","gitBranch":"main"}""",
    r"""{"parentUuid":"91b4192a-070f-4e6d-b27b-6351811bdb2f","isSidechain":false,"promptId":"b2851b5a-fc43-4230-8bb5-81b3358a9e91","type":"user","message":{"role":"user","content":"<local-command-stdout>Set model to model-b</local-command-stdout>"},"uuid":"14cfc194-78b3-4611-abbd-c706e0f6f966","timestamp":"2026-07-24T22:06:47.255Z","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.219","gitBranch":"main"}""",
    r"""{"parentUuid":"14cfc194-78b3-4611-abbd-c706e0f6f966","isSidechain":false,"promptId":"b2851b5a-fc43-4230-8bb5-81b3358a9e91","type":"user","message":{"role":"user","content":"continue"},"uuid":"9c0f90a3-e1df-47dd-8f42-e5ebed841f03","timestamp":"2026-07-24T22:06:51.736Z","permissionMode":"auto","origin":{"kind":"human"},"promptSource":"sdk","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.219","gitBranch":"main"}""",
]

# The same shapes again: one model-a/high turn and two model-b/low turns, so the dominant driver and
# the mixed flag are both observable.
LIVE_ROWS_ONE_MODEL = [
    r"""{"parentUuid":"4a909478-c780-41dd-a505-2719f27bdeac","isSidechain":false,"message":{"model":"model-a","id":"msg_011CdLvG4KmTAnmjATe6XkEc","type":"message","role":"assistant","content":[{"type":"text","text":"I'll start by loading the refactor skill and searching memory for refactoring gotchas."}],"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":45495,"cache_read_input_tokens":28034,"output_tokens":531,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":45495,"ephemeral_5m_input_tokens":0},"inference_geo":"not_available","iterations":[{"input_tokens":2,"output_tokens":531,"cache_read_input_tokens":28034,"cache_creation_input_tokens":45495,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":45495},"type":"message"}],"speed":"standard"},"diagnostics":null},"requestId":"req_011CdLvG2PgoMfUftcMQhMDU","type":"assistant","uuid":"9a673795-bfbb-43ec-9cfe-1c157feaab00","timestamp":"2026-07-24T12:56:26.916Z","effort":"high","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.217","gitBranch":"main"}""",
    r"""{"parentUuid":"878e6362-781b-4f4a-a3d0-d388d8eb529d","isSidechain":false,"message":{"model":"model-b","id":"msg_011CdMeFcLxw3Z4GJ3rE14b8","type":"message","role":"assistant","content":[{"type":"text","text":"Proceeding with the acknowledged-known-failure path. Committing the first change, then the second with a pathspec so I don't sweep unrelated work."}],"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":117839,"cache_read_input_tokens":0,"output_tokens":918,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":117839,"ephemeral_5m_input_tokens":0},"inference_geo":"not_available","iterations":[{"input_tokens":2,"output_tokens":918,"cache_read_input_tokens":0,"cache_creation_input_tokens":117839,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":117839},"type":"message"}],"speed":"standard"},"diagnostics":{"cache_miss_reason":{"type":"previous_message_not_found"}}},"requestId":"req_011CdMeFarvCYhufCPEowDcE","type":"assistant","uuid":"c3e60fff-733b-442e-b81f-bc6521980585","timestamp":"2026-07-24T22:06:57.286Z","effort":"low","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.219","gitBranch":"main"}""",
    r"""{"parentUuid":"62decbb5-6db4-47fd-a117-fa0ca0f5e4ef","isSidechain":false,"message":{"model":"model-b","id":"msg_011CdMeJFLFUtWRjod78YdiQ","type":"message","role":"assistant","content":[{"type":"tool_use","id":"toolu_01PA6PHv8raUGJ6gLXaXEzPc","name":"Bash","input":{"command":"ls /c/repo/.claude/auto-memory/ | grep -i -E \"script_class|filename\"","description":"Check for existing memory covering this gotcha"},"caller":{"type":"direct"}}],"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":767,"cache_read_input_tokens":120818,"output_tokens":708,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":767,"ephemeral_5m_input_tokens":0},"inference_geo":"not_available","iterations":[{"input_tokens":2,"output_tokens":708,"cache_read_input_tokens":120818,"cache_creation_input_tokens":767,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":767},"type":"message"}],"speed":"standard"},"diagnostics":null},"requestId":"req_011CdMeJDvApTCMEG2oBXkpU","type":"assistant","uuid":"7c816e50-a28a-47e6-80c4-9ef40be74ef6","timestamp":"2026-07-24T22:07:40.466Z","effort":"low","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\repo","sessionId":"00000000-0000-4000-8000-000000000001","version":"2.1.219","gitBranch":"main"}""",
]


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

    # A session under another account's config dir keeps its transcripts there.
    with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": os.path.join("X:", os.sep, "second")}):
        got = sd.projects_dir("C:\\repo")
        if got != Path("X:", os.sep, "second", "projects", "C--repo"):
            fails.append(f"CLAUDE_CONFIG_DIR ignored: {got}")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        if sd.projects_dir("C:\\repo") != Path.home() / ".claude" / "projects" / "C--repo":
            fails.append("default config dir is not ~/.claude")

    # --session resolves in another account's store when this login holds no match: a session run on
    # the second account is still digestible from the first.
    with tempfile.TemporaryDirectory() as home:
        primary = Path(home, ".claude", "projects", "C--repo")
        second = Path(home, ".claude-secondary", "projects", "C--repo")
        third = Path(home, ".claude-third", "projects", "C--repo")
        stale = Path(home, ".claude-stale", "projects", "C--repo")
        for folder, name in ((primary, "aaaa1111"), (second, "bbbb2222"), (third, "cccc3333"), (second, "cccc4444"),
                             (stale, "eeee5555")):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{name}.jsonl").write_text("{}\n", encoding="utf-8")
        declared = [str(Path(home, d)) for d in (".claude", ".claude-secondary", ".claude-third")]
        with patch.object(sd, "login_dirs", lambda: declared, create=True):
            try:
                sd.pick_transcript(primary, "eeee", None)
                fails.append("a login store the registry does not declare must not be searched")
            except SystemExit:
                pass
            if sd.pick_transcript(primary, "aaaa", None) != primary / "aaaa1111.jsonl":
                fails.append("a match in this login's store must win")
            try:
                got = sd.pick_transcript(primary, "bbbb", None)
                if got != second / "bbbb2222.jsonl":
                    fails.append(f"another account's unique match not resolved: {got}")
            except SystemExit as exc:
                fails.append(f"another account's unique match refused: {exc}")
            for prefix, why in (("cccc", "ambiguous across stores"), ("dddd", "absent everywhere")):
                try:
                    sd.pick_transcript(primary, prefix, None)
                    fails.append(f"{why} must refuse")
                except SystemExit as exc:
                    if ".claude-secondary" not in str(exc):
                        fails.append(f"{why}: refusal must name the stores searched: {exc}")

    # --- facts: the numbers a self-evaluation row carries, derived here and never by the model ----
    # The fixtures keep a live transcript's row shapes, so the pin is a definition and not a shape
    # someone imagined. Every value below is counted from the fixture's own rows: two model-a/high
    # turns and two prompts (the prose task and "continue"), while the `<synthetic>` turn is not a
    # driver, the bare /model control command is not an owner prompt, and the caveat/stdout plumbing
    # never reaches the prompt list. 12:56:12.181Z to 22:06:51.736Z floors to 550 whole minutes.
    with tempfile.TemporaryDirectory() as d:
        facts_fn = getattr(sd, "session_facts", None)
        if facts_fn is None:
            fails.append("session_facts is missing: facts must be store-derived")
        else:
            for name in ("LIVE_ROWS_MIXED", "LIVE_ROWS_ONE_MODEL"):
                rows = globals()[name]
                if not all(isinstance(row, str) and _parses(row) for row in rows):
                    fails.append("%s holds a row that is not one JSON line" % name)
            mixed = facts_fn(write_transcript(d, "11112222-mixed", LIVE_ROWS_MIXED, now))
            one_model = facts_fn(write_transcript(d, "33334444-one-model", LIVE_ROWS_ONE_MODEL, now))
            if mixed.get("driver") != {"model": "model-a", "effort": "high"}:
                fails.append("facts driver (most assistant turns) wrong: %r" % (mixed.get("driver"),))
            if mixed.get("owner_prompts") != 2:
                fails.append("facts owner_prompts counted %r, expected the two substantive rows"
                             % (mixed.get("owner_prompts"),))
            if mixed.get("duration_min") != 550:
                fails.append("facts duration_min is %r, expected 550 whole minutes"
                             % (mixed.get("duration_min"),))
            if "driver_mixed" in mixed:
                fails.append("a single-pair session must not read as mixed")
            if one_model.get("driver") != {"model": "model-b", "effort": "low"}:
                fails.append("facts driver ignored the row counts: %r" % (one_model.get("driver"),))
            if one_model.get("driver_mixed") is not True:
                fails.append("two model/effort pairs must set driver_mixed: %r"
                             % (one_model.get("driver_mixed"),))
            if one_model.get("owner_prompts") != 0:
                fails.append("a transcript with no user row counted %r prompts"
                             % (one_model.get("owner_prompts"),))

    for f in fails:
        print("FAIL", f)
    print("test_session_digest: %d checks, %d fail" % (38, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
