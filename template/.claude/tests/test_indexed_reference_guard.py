#!/usr/bin/env python3
"""Re-runnable proof for hooks/indexed_reference_guard.py — a whole-file Read of an index-served
reference advises the main session and blocks a delegate; bounded reads and other tools pass.

    python3 .claude/tests/test_indexed_reference_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HOOK = os.path.join(ROOT, ".claude", "hooks", "indexed_reference_guard.py")
DISPATCH = os.path.join(ROOT, ".claude", "hooks", "pre_read_dispatch.py")
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
import indexed_reference_guard as guard  # noqa: E402

CATALOG = os.path.join(ROOT, ".claude", "commands", "agents", "plan_check_agents.md")
SUBSYS_REL = ("skills", "project_subsystems", "reference", "subsystem_details.md")
PLANTED = tempfile.TemporaryDirectory()  # the catalog is project-generated; the proof plants its own
SUBSYS = os.path.join(PLANTED.name, ".claude", *SUBSYS_REL)
os.makedirs(os.path.dirname(SUBSYS))
with open(SUBSYS, "w", encoding="utf-8", newline="\n") as _fh:
    _fh.write("# planted subsystem catalog\n" * 40)


def run(payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    r = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    return r.returncode, r.stderr or ""


def run_dispatch(payload):
    raw = json.dumps(payload)
    r = subprocess.run([sys.executable, DISPATCH], input=raw, capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    return r.returncode, r.stdout or "", r.stderr or ""


def main():
    cases = []
    rc, err = run({"tool_name": "Read", "agent_id": "delegate-proof",
                   "tool_input": {"file_path": CATALOG}})
    cases.append(("delegate whole-file Read is blocked with the accessor on the real channel",
                  rc == 2 and "BLOCKED" in err and "lens.py" in err))
    rc, out, err = run_dispatch({"tool_name": "Read", "tool_input": {"file_path": CATALOG}})
    try:
        advisory = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except (KeyError, ValueError):
        advisory = ""
    cases.append(("main-session Read proceeds with accessor advisory on the model channel",
                  rc == 0 and not err and "lens.py get --shared" in advisory))
    rc, out, err = run_dispatch({"tool_name": "Read", "agent_id": "delegate-proof",
                                  "tool_input": {"file_path": CATALOG}})
    cases.append(("delegate deny cites guards/any.md and routes through the accessor",
                  rc == 2 and "guards/any.md" in err
                  and "read a catalog with an accessor through it" in err
                  and "lens.py get --shared" in err))
    for actor, extra in (("main", {}), ("delegate", {"agent_id": "delegate-proof"})):
        rc, out, err = run_dispatch({"tool_name": "Read", **extra,
                                     "tool_input": {"file_path": CATALOG, "offset": 10, "limit": 40}})
        cases.append((f"bounded Read passes for {actor}", rc == 0 and not err))
    for suffix, (accessor, _gives) in guard.INDEXED_REFERENCES.items():
        path = os.path.join(ROOT, ".claude", suffix.replace("/", os.sep))
        if not os.path.isfile(path):  # project-generated catalog a published template never holds: plant it
            path = os.path.join(PLANTED.name, ".claude", suffix.replace("/", os.sep))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# planted catalog\n" * 40)
        rc, out, err = run_dispatch({"tool_name": "Read", "tool_input": {"file_path": path}})
        try:
            advice = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        except (KeyError, ValueError):
            advice = ""
        cases.append((f"main advisory names accessor for {suffix}",
                      rc == 0 and not err and accessor in advice))
        rc, out, err = run_dispatch({"tool_name": "Read", "agent_id": "delegate-proof",
                                     "tool_input": {"file_path": path}})
        cases.append((f"delegate block cites rail and accessor for {suffix}",
                      rc == 2 and "guards/any.md" in err
                      and "read a catalog with an accessor through it" in err
                      and accessor in err))

    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": CATALOG, "offset": 10, "limit": 40}})
    cases.append(("bounded Read of the same catalog passes", rc == 0 and not err))
    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": os.path.join(ROOT, ".claude", "CLAUDE.md")}})
    cases.append(("a non-indexed file passes", rc == 0 and not err))
    rc, err = run({"tool_name": "Grep", "tool_input": {"pattern": "plc-", "path": CATALOG}})
    cases.append(("another tool on the catalog passes", rc == 0 and not err))
    rc, err = run({"tool_name": "Read", "agent_id": "delegate-proof",
                   "tool_input": {"file_path": CATALOG.replace("\\", "/")}})
    cases.append(("forward-slash path still matches the delegate block", rc == 2))
    variant = os.path.join(ROOT, ".CLAUDE", "commands", "agents", "..", "agents",
                           "PLAN_CHECK_AGENTS.MD")
    rc, err = run({"tool_name": "Read", "agent_id": "delegate-proof",
                   "tool_input": {"file_path": variant}})
    cases.append(("Windows case and dot-segment variants cannot bypass the same indexed file",
                  rc == 2 and "BLOCKED" in err))
    rc, err = run("{not json")
    cases.append(("malformed payload fails open (exit 0)", rc == 0))
    rc, err = run('"a json string, not an object"')
    cases.append(("a non-object JSON payload fails open, never crashes", rc == 0))
    cases.append(("the guard's message names the accessor for every registered reference",
                  all("python3" in cmd or "grep" in cmd for cmd, _ in guard.INDEXED_REFERENCES.values())))
    cases.append(("every registered reference exists on disk or is known project-local content",
                  all(s in guard.LOCAL_ONLY_TARGETS or os.path.isfile(os.path.join(ROOT, ".claude", s))
                      for s in guard.INDEXED_REFERENCES)))
    cases.append(("process() returns None for a payload without tool_input",
                  guard.process({"tool_name": "Read"}) is None))

    rc, out, err = run_dispatch({"tool_name": "Read", "tool_input": {"file_path": SUBSYS}})
    try:
        subsys_advice = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except (KeyError, ValueError):
        subsys_advice = ""
    cases.append(("main whole-file Read of the subsystem catalog gets the subsys.py accessor advice",
                  rc == 0 and not err and "subsys.py index" in subsys_advice))
    rc, out, err = run_dispatch({"tool_name": "Read", "agent_id": "delegate-proof",
                                 "tool_input": {"file_path": SUBSYS}})
    cases.append(("delegate whole-file Read of the subsystem catalog is blocked with the accessor",
                  rc == 2 and "subsys.py index" in err and "guards/any.md" in err))
    for actor, extra in (("main", {}), ("delegate", {"agent_id": "delegate-proof"})):
        rc, out, err = run_dispatch({"tool_name": "Read", **extra,
                                     "tool_input": {"file_path": SUBSYS, "offset": 1, "limit": 5}})
        cases.append((f"bounded Read of the subsystem catalog passes for {actor}", rc == 0 and not err))
    near = os.path.join(os.path.dirname(SUBSYS), "subsystem_details_notes.md")
    with open(near, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("notes\n")
    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": near}})
    cases.append(("a near-name path beside the subsystem catalog passes", rc == 0 and not err))
    for actor, extra in (("main", {}), ("delegate", {"agent_id": "delegate-proof"})):
        absent = os.path.join(ROOT, "no-such-checkout", ".claude", "skills", "project_subsystems",
                              "reference", "subsystem_details.md")
        rc, err = run({"tool_name": "Read", **extra, "tool_input": {"file_path": absent}})
        cases.append((f"an absent subsystem catalog is allowed for {actor}", rc == 0 and not err))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
