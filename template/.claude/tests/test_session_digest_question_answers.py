#!/usr/bin/env python3
"""Proof: session_digest counts an owner's AskUserQuestion answers as owner messages.

The transcript builder routes every tool_result row to friction capture, so a decision the owner
gives through a question ("Do all recommendations") never reached the digest's prompt list: Phase 0
accounting and the post-compaction directive anchor both missed it. Observed 2026-09-14 in session
3259384b, where the owner's closeout decisions arrived only as question answers.

    python3 .claude/tests/test_session_digest_question_answers.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("sd", os.path.join(HERE, "..", "tools", "session_digest.py"))
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

ANSWER = 'The user answered: "Which edits?"="Do all recommendations"'
MISSING_ID_ANSWER = 'The user answered: "Missing ids?"="Must not match"'


def rows():
    return [
        {"type": "user", "timestamp": "2026-09-14T10:00:00Z",
         "message": {"role": "user", "content": "Drive the harness directive to completion."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_q1", "name": "AskUserQuestion", "input": {"questions": []}}]}},
        {"type": "user", "timestamp": "2026-09-14T10:05:00Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_q1", "content": ANSWER}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_b1", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "timestamp": "2026-09-14T10:06:00Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_b1", "content": "a.txt"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_q2", "name": "AskUserQuestion", "input": {"questions": []}}]}},
        {"type": "user", "timestamp": "2026-09-14T10:07:00Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_q2", "is_error": True,
             "content": "Error: question rejected"}]}},
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_q1", "content": "subagent echo"}]}},
    ]


def main():
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sess.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows()) + "\n", encoding="utf-8")

        recover = getattr(sd, "recover_question_answers", None)
        found = recover(path) if recover else None
        cases.append(("recover_question_answers exists", recover is not None))
        cases.append(("exactly the one answered question is recovered",
                      bool(found) and len(found) == 1 and ANSWER in found[0]["content"]))
        cases.append(("the answer keeps its transcript line as its index",
                      bool(found) and found[0]["index"] == 3))
        cases.append(("a Bash result, an errored question and a sidechain row are not answers",
                      bool(found) and not any(t in r["content"] for r in found
                                              for t in ("a.txt", "rejected", "subagent echo"))))

        owner = getattr(sd, "owner_prompts", None)
        messages = owner(path) if owner else []
        cases.append(("owner_prompts returns the typed prompt then the answer, in transcript order",
                      [m["index"] for m in messages] == [1, 3]))
        index = sd.build_evidence_index({"user_messages": messages})
        cases.append(("the evidence index names the answer as U3", any(p["id"] == "U3" for p in index["prompts"])))
        try:
            selected = sd.select_evidence({"user_messages": messages}, ["U3"])
            cases.append(("select_evidence returns the answer row", ANSWER in selected[0]["row"]["content"]))
        except ValueError as exc:
            cases.append((f"select_evidence returns the answer row ({exc})", False))

        dup = [{"index": 3, "content": "text typed beside a tool result"}]
        merged = sd.merge_recovered_prompts(dup, [{"index": 3, "content": "(answer) x"}])
        cases.append(("a recovered row never duplicates an existing evidence index",
                      [m["index"] for m in merged].count(3) == 1
                      and "text typed beside a tool result" in merged[0]["content"]
                      and "(answer) x" in merged[0]["content"]))

        envelope_path = Path(tmp) / "agent-message.jsonl"
        envelope_path.write_text("\n".join(json.dumps(r) for r in (
            {"type": "user", "message": {"role": "user", "content": "Owner prompt before the peer."}},
            {"type": "user", "message": {"role": "user", "content":
                '<agent-message from="peer">Peer update. Stop agent-123.</agent-message>'}},
        )) + "\n", encoding="utf-8")
        envelope_prompts = [m["content"] for m in sd.owner_prompts(envelope_path)]
        cases.append(("an <agent-message> envelope row is not an owner prompt",
                      envelope_prompts == ["Owner prompt before the peer."]))

        malformed_path = Path(tmp) / "malformed-message.jsonl"
        malformed_path.write_text("\n".join(json.dumps(r) for r in (
            {"type": "assistant", "message": []},
            {"type": "assistant", "message": ["not", "an", "object"]},
            {"type": "user", "message": "a bare string"},
        )) + "\n", encoding="utf-8")
        try:
            malformed_found = sd.recover_question_answers(malformed_path)
            malformed_ok = malformed_found == []
        except Exception:
            malformed_ok = False
        cases.append(("a non-object message row is skipped", malformed_ok))

        missing_id_rows = [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "AskUserQuestion", "input": {}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": MISSING_ID_ANSWER}]}},
        ]
        missing_id_path = Path(tmp) / "missing-ids.jsonl"
        missing_id_path.write_text("\n".join(json.dumps(r) for r in missing_id_rows) + "\n",
                                   encoding="utf-8")
        missing_id_found = sd.recover_question_answers(missing_id_path)
        cases.append(("a question and result without ids never match",
                      not any(MISSING_ID_ANSWER in r["content"] for r in missing_id_found)))

        real_argv = sys.argv
        real_pick = sd.pick_transcript
        sys.argv = ["session_digest.py", "--session", "sess", "--project-dir", tmp, "--json-only"]
        sd.pick_transcript = lambda *_args, **_kwargs: path
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                sd.main()
        finally:
            sys.argv = real_argv
            sd.pick_transcript = real_pick
        digest = json.loads((Path(tmp) / "logs" / "session_digest_sess.json").read_text(encoding="utf-8"))
        cases.append(("coverage counts the merged prompt list",
                      digest["evidence_coverage"]["collected_user_messages"]
                      == len(digest["user_messages"]) == 2))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
