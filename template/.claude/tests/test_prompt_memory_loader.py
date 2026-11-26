#!/usr/bin/env python3
"""Proof for hooks/prompt_memory_loader.py.

The hook decides on state, never on the prompt's words: it speaks when `.claude/auto-memory/` holds entries and
the session's own transcript (`payload.transcript_path`) shows no read of an entry since the last compaction,
once per compaction window. The transcript rows are cut from live transcripts (`fixtures/memory_consulted/rows.json`);
a case plants each shape by editing one field of such a row. A prompt under the length floor is a confirmation and
spends nothing. Whether a person typed the prompt is the dispatcher's decision (`prompt_dispatch.AUDIENCE`, proven
in test_prompt_dispatch.py).

The hook runs from a scratch `.claude` tree (its memory directory resolves from its own location), in process
through `_hook_call`; one case spawns it to prove it imports, runs and exits 0. State is redirected with
HARNESS_HOOK_STATE_DIR.

    python3 .claude/tests/test_prompt_memory_loader.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _hook_call  # noqa: E402
from _module_closure import copy_closure  # noqa: E402

REAL = os.path.join(HERE, "..")
ROWS = json.load(open(os.path.join(HERE, "fixtures", "memory_consulted", "rows.json"), encoding="utf-8"))
COMPACT_START = os.path.join(REAL, "hooks", "subagent_compaction_rail.py")

TASK = "Add a new widget tier to the gizmo roster"
FIX = "Debug why the freeze effect is not working on critters"
APPLY = "Go ahead and apply the fix we agreed on"


def build_tree(entries, archive=()):
    root = tempfile.mkdtemp(prefix="pml_tree_")
    for sub in ("hooks", "auto-memory/archive"):
        os.makedirs(os.path.join(root, sub))
    copy_closure(root, "hooks/prompt_memory_loader.py")
    with open(os.path.join(root, "auto-memory", "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n")
    for name in entries:
        with open(os.path.join(root, "auto-memory", name), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\n---\nbody\n" % name)
    for name in archive:
        with open(os.path.join(root, "auto-memory", "archive", name), "w", encoding="utf-8") as fh:
            fh.write("body\n")
    return root


def main():
    cases = []

    def case(label, ok):
        cases.append((label, ok))

    state_dir = tempfile.mkdtemp(prefix="pml_state_")
    tx_dir = tempfile.mkdtemp(prefix="pml_tx_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir, CLAUDE_PROJECT_DIR=state_dir, PYTHONIOENCODING="utf-8")

    def speaks(root, prompt, session, **extra):
        code, out, err, _ = _hook_call.call(os.path.join(root, "hooks", "prompt_memory_loader.py"),
                                            dict({"session_id": session, "prompt": prompt}, **extra), env=env)
        assert code == 0 and "Traceback" not in err, err
        return "auto-memory" in out

    def transcript(session, *rows):
        path = os.path.join(tx_dir, session + ".jsonl")
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return path

    def block(row, **changes):
        """A copy of a live row with its first content block's fields replaced."""
        row = json.loads(json.dumps(row))
        row["message"]["content"][0].update(changes)
        return row

    def reads(path):
        return block(ROWS["read_use"], input={"file_path": path})

    tree = build_tree(["gotcha_a.md", "feedback_b.md"], archive=["old.md"])
    bare = build_tree([], archive=["old.md"])
    try:
        case("a memory with entries and no logged hit gets the nudge", speaks(tree, TASK, "pml00001"))
        case("the same session's next prompt is silent", not speaks(tree, FIX, "pml00001"))
        out = _hook_call.call(os.path.join(tree, "hooks", "prompt_memory_loader.py"),
                              {"session_id": "pml00002", "prompt": TASK}, env=env)[1]
        case("the nudge names the entry count (archive and MEMORY.md excluded)", "2" in out and "3" not in out)

        case("a prompt under the length floor is silent", not speaks(tree, "thanks", "pml00003"))
        case("and it spent nothing: the first substantive prompt still speaks", speaks(tree, TASK, "pml00003"))

        case("the words do not decide: an execution directive on a fresh session speaks as the task did",
             speaks(tree, APPLY, "pml00004"))
        case("and a high-risk keyword on a fresh session speaks once, like any prompt", speaks(tree, FIX, "pml00005"))
        case("a plan-mode prompt carries no branch of its own",
             not speaks(tree, FIX, "pml00005", permission_mode="plan"))

        ENTRY = "C:/r/.claude/auto-memory/gotcha_a.md"
        tx = transcript("pml00006", ROWS["read_use"], ROWS["read_result"])
        case("a session whose transcript shows a Read of an entry is silent from its first prompt",
             not speaks(tree, TASK, "pml00006", transcript_path=tx))
        case("and the hit is remembered: the next prompt is silent with the transcript gone",
             not speaks(tree, FIX, "pml00006", transcript_path=os.path.join(tx_dir, "gone.jsonl")))
        tx = transcript("pml00007", ROWS["search_use"], ROWS["search_result"])
        case("a session whose search result named an entry is silent",
             not speaks(tree, TASK, "pml00007", transcript_path=tx))
        tx = transcript("pml00012", ROWS["empty_use"], ROWS["empty_result"])
        case("a search that found nothing is no consultation", speaks(tree, TASK, "pml00012", transcript_path=tx))
        tx = transcript("pml00013", ROWS["search_use"], block(ROWS["search_result"], tool_use_id="toolu_other"))
        case("an entry path in the result of another call is no consultation",
             speaks(tree, TASK, "pml00013", transcript_path=tx))
        tx = transcript("pml00014", reads("C:/r/.claude/auto-memory/MEMORY.md"), reads("C:/r/src/gotcha_a.md"))
        case("the MEMORY.md index and a file outside the memory are no entry",
             speaks(tree, TASK, "pml00014", transcript_path=tx))
        tx = transcript("pml00015", reads("C:\\r\\.claude\\auto-memory\\gotcha_a.md"))
        case("a Windows-spelled path names an entry", not speaks(tree, TASK, "pml00015", transcript_path=tx))
        tx = transcript("pml00016", dict(reads(ENTRY), isSidechain=True))
        case("a subagent's read is not this session's", speaks(tree, TASK, "pml00016", transcript_path=tx))
        tx = transcript("pml00017", {"type": "api-request-blob", "message": reads(ENTRY)["message"]})
        case("a row that is no message of the thread carries no read", speaks(tree, TASK, "pml00017", transcript_path=tx))
        case("a transcript that cannot be read leaves the nudge standing",
             speaks(tree, TASK, "pml00018", transcript_path=os.path.join(tx_dir, "missing.jsonl")))
        case("another session's transcript does not silence this one", speaks(tree, TASK, "pml00008"))

        tx = transcript("pml00019", reads(ENTRY))
        case("the read silences the prompt that finds it", not speaks(tree, TASK, "pml00019", transcript_path=tx))
        transcript("pml00019", ROWS["boundary"])
        _hook_call.compact_start(COMPACT_START, env, "pml00019")
        case("a read before the last compaction boundary no longer counts: the nudge speaks again",
             speaks(tree, TASK, "pml00019", transcript_path=tx))
        tx = transcript("pml00020", reads(ENTRY), ROWS["boundary"], reads(ENTRY))
        case("a read after the boundary counts", not speaks(tree, TASK, "pml00020", transcript_path=tx))

        speaks(tree, TASK, "pml00009")
        _hook_call.compact_start(COMPACT_START, env, "pml00009")
        case("a compaction re-arms the nudge for a session still without a hit", speaks(tree, TASK, "pml00009"))

        case("a memory with no entries is silent", not speaks(bare, TASK, "pml00010"))
        case("and it spent nothing: entries added later speak at once",
             (open(os.path.join(bare, "auto-memory", "gotcha_new.md"), "w").close() or True)
             and speaks(bare, TASK, "pml00010"))

        hook = os.path.join(tree, "hooks", "prompt_memory_loader.py")
        raw = [subprocess.run([sys.executable, hook], input=text, capture_output=True, text=True, timeout=90, env=env)
               for text in ('"notice"', "[1, 2]", "null", "42", "not json")]
        case("non-object and unreadable payloads are no-ops that exit 0",
             all(r.returncode == 0 and r.stdout.strip() in ("", "{}") and not r.stderr.strip() for r in raw))
        proc = subprocess.run([sys.executable, hook], input=json.dumps({"session_id": "pml00011", "prompt": TASK}),
                              capture_output=True, text=True, timeout=90, env=env)
        case("the registered command shape imports, runs and exits 0 with the nudge",
             proc.returncode == 0 and "Traceback" not in proc.stderr and "auto-memory" in proc.stdout)
    finally:
        shutil.rmtree(tree, ignore_errors=True)
        shutil.rmtree(bare, ignore_errors=True)

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
