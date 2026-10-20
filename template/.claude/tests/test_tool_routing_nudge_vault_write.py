#!/usr/bin/env python3
"""Re-runnable proof for hooks/tool_routing_nudge.py on a direct vault `Write`.

A doc-sized direct `Write` into the Obsidian vault gets NO advisory. The doc-class rule (judgment-dense →
direct Write; templated → `write_doc`) reaches the agent in `CLAUDE.core.md` §9 before the call, and the
routing audit's census row records the route from the call itself, so an advisory that asked the agent to
name the class afterwards changed no decision (`instruction_quality` §3: a note on an admitted call is
deleted, and a class the checked agent writes is never a pass condition). The hook stays silent on every
vault Write, large or small, with or without ai-worker, and never blocks. Grep and Read advisories keep
their own proofs.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_tool_routing_nudge_vault_write.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "tool_routing_nudge.py")
SID = "trvw0001"
VAULT = "C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/Claude/Review.md"


def call(tool_input, env, tool_name="Write"):
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"tool_name": tool_name, "session_id": SID, "tool_input": tool_input}),
                       capture_output=True, text=True, timeout=60, env=env)
    if r.returncode not in (0, 2):
        return "CRASH rc=%d %s" % (r.returncode, (r.stderr or "")[-300:])
    if r.returncode == 2:
        return "BLOCKED " + (r.stderr or "")
    out = (r.stdout or "").strip()
    if not out:
        return ""
    return (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext", "")


def profile_env(base, tier, ai_worker):
    """`base` env with this machine's tier and ai-worker registration planted where the hook reads
    them (`tools/workstation_profile.py`)."""
    root = tempfile.mkdtemp(prefix="trvwprofile_")
    cfg = os.path.join(root, "cfg")
    os.makedirs(os.path.join(cfg, "plugins"))
    servers = {"ai-worker": {"command": "python"}} if ai_worker == "present" else {}
    with open(os.path.join(cfg, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    profile = os.path.join(root, "workstation_profile.json")
    if tier is not None:
        with open(profile, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "local_tier": tier}, fh)
    return dict(base, WORKSTATION_PROFILE_PATH=profile, CLAUDE_CONFIG_DIR=cfg,
                SC_WORKDIR=os.path.join(root, "nowhere"))


def main():
    tmp = tempfile.mkdtemp(prefix="trvw_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    cases = []

    big = {"file_path": VAULT, "content": "# Review\n" + "x" * 5000}
    first = call(big, env)
    cases.append(("a doc-sized vault Write gets no advisory (the rule reached the agent before the call)", first == ""))
    cases.append(("never a block, never a crash", not first.startswith("BLOCKED") and not first.startswith("CRASH")))

    said = "C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/Claude/Other.md"
    absent = call({"file_path": said, "content": "# Review\n" + "x" * 5000}, profile_env(env, "full", "absent"))
    cases.append(("silent on a machine without ai-worker", absent == ""))
    present = call({"file_path": said.replace("Other", "Other2"), "content": "# Review\n" + "x" * 5000},
                   profile_env(env, "full", "present"))
    cases.append(("silent on a machine with ai-worker", present == ""))

    small = call({"file_path": VAULT.replace("Review", "Other"), "content": "one line"}, env)
    cases.append(("a small vault write is silent", small == ""))

    outside = call({"file_path": "C:/repo/.claude/scratch/spill.md", "content": "x" * 9000}, env)
    cases.append(("a large write outside the vault is silent", outside == ""))

    # The Grep advisory is a different decision (it names the NEXT call) and stays: a positive control
    # that the hook still emits advisories at all.
    grep = call({"pattern": "SpellCaster", "glob": "*.cs"}, env, tool_name="Grep")
    cases.append(("positive control: a bare PascalCase Grep on .cs still gets its advisory",
                  "[tool-routing]" in grep and not grep.startswith("CRASH")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
