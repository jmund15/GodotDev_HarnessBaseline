#!/usr/bin/env python3
"""Re-runnable proof for hooks/retire_trigger_advisory.py.

Feeds real PostToolUse Write/Edit payloads through `process()` and asserts on the
`{"context": ...}` channel — silent (None) vs an advisory line.

    python3 .claude/tests/test_retire_trigger_advisory.py
"""
import importlib.util
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
HOOK_PATH = os.path.join(CLAUDE, "hooks", "retire_trigger_advisory.py")

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" — " + detail) if detail else ""))
        FAILURES.append(label)


def load_hook():
    spec = importlib.util.spec_from_file_location("retire_trigger_advisory_probe", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_TRIGGER = (
    "---\n"
    "name: valid-rule\n"
    "description: has a well-formed trigger\n"
    "retire_when: \"review-by: 2999-01-01\"\n"
    "---\n\n"
    "Body.\n"
)

NO_TRIGGER = (
    "---\n"
    "name: no-trigger-rule\n"
    "description: an ordinary memory file\n"
    "---\n\n"
    "Body.\n"
)

MALFORMED_TRIGGER = (
    "---\n"
    "name: malformed-rule\n"
    "description: declares retire_when with no value\n"
    "retire_when:\n"
    "---\n\n"
    "Body.\n"
)


def payload(tool_name, file_path, content, cwd, response_type="create"):
    data = {"tool_name": tool_name, "cwd": cwd,
            "tool_input": {"file_path": file_path, "content": content}}
    if response_type is not None:
        data["tool_response"] = {"type": response_type, "filePath": file_path}
    return data


def main():
    if not os.path.exists(HOOK_PATH):
        raise SystemExit("RED: %s does not exist" % HOOK_PATH)
    hook = load_hook()

    with tempfile.TemporaryDirectory() as repo:
        in_scope = os.path.join(repo, ".claude", "auto-memory", "archive", "probe.md")
        outside_scope = os.path.join(repo, ".claude", "commands", "probe.md")
        rules_scope = os.path.join(repo, ".claude", "rules", "probe.md")

        print("case 1 — a new memory file with no trigger is valid and stays silent")
        result = hook.process(payload("Write", in_scope, NO_TRIGGER, repo))
        check("a markerless archive memory file is silent", result is None, str(result))
        hot = os.path.join(repo, ".claude", "auto-memory", "probe.md")
        result = hook.process(payload("Write", hot, NO_TRIGGER, repo))
        check("a markerless hot memory file is silent", result is None, str(result))

        print("case 2 — a new in-scope memory file with a malformed trigger fires")
        result = hook.process(payload("Write", in_scope, MALFORMED_TRIGGER, repo))
        check("fires with a context line naming the malformed row",
              bool(result) and "retire_when declared with no trigger" in result["context"], str(result))

        print("case 3 — a valid trigger stays silent")
        result = hook.process(payload("Write", in_scope, VALID_TRIGGER, repo))
        check("returns None", result is None, str(result))

        print("case 4 — an Edit of an existing file stays silent")
        result = hook.process(payload("Edit", in_scope, NO_TRIGGER, repo))
        check("Edit never fires, even with no trigger", result is None, str(result))

        print("case 5 — a file outside the memory scope stays silent")
        result = hook.process(payload("Write", outside_scope, NO_TRIGGER, repo))
        check("commands/ is outside scope", result is None, str(result))

        print("case 11 — a rules file warns only on a malformed declared retire-when comment")
        rule_body = "---\npaths:\n  - \"**/*.cs\"\n---\n\nRule.\n"
        result = hook.process(payload("Write", rules_scope, rule_body, repo))
        check("a new rules file with no retire-when comment is silent", result is None, str(result))
        result = hook.process(payload("Write", rules_scope,
                                      rule_body + "\n<!-- retire-when: review-by: 2999-01-01 -->\n", repo))
        check("a new rules file with a valid retire-when comment stays silent", result is None, str(result))
        result = hook.process(payload("Write", rules_scope, rule_body + "\n<!-- retire-when: -->\n", repo))
        check("an empty retire-when comment warns, naming the file",
              bool(result) and "rules/probe.md" in result["context"]
              and "[retire-trigger-advisory]" in result["context"], str(result))
        result = hook.process(payload("Write", rules_scope,
                                      rule_body + "\n<!-- retire-when: someday maybe -->\n", repo))
        check("an unknown retire-when trigger warns, naming the trigger",
              bool(result) and "someday maybe" in result["context"], str(result))
        result = hook.process(payload("Write", rules_scope, rule_body + "\n<!-- retire-when: -->\n", repo,
                                      response_type="update"))
        check("an update Write of a rules file stays silent", result is None, str(result))
        result = hook.process(payload("Write", os.path.join(repo, ".claude", "rules", "sub", "probe.md"),
                                      rule_body + "\n<!-- retire-when: -->\n", repo))
        check("a malformed comment in a rules subdirectory also warns", bool(result), str(result))

        print("case 6 — a non-.md file inside auto-memory/ stays silent")
        result = hook.process(payload(
            "Write", os.path.join(repo, ".claude", "auto-memory", "archive", "probe.txt"),
            NO_TRIGGER, repo))
        check("a .txt file is outside scope", result is None, str(result))

        print("case 8 — an update Write of an existing memory file stays silent")
        result = hook.process(payload("Write", in_scope, NO_TRIGGER, repo, response_type="update"))
        check("type=update never fires", result is None, str(result))

        print("case 9 — a Write with a missing or unknown tool_response stays silent")
        result = hook.process(payload("Write", in_scope, NO_TRIGGER, repo, response_type=None))
        check("missing tool_response is silent", result is None, str(result))
        result = hook.process(payload("Write", in_scope, NO_TRIGGER, repo, response_type="patch"))
        check("unknown tool_response type is silent", result is None, str(result))

        print("case 10 — MEMORY.md never fires, even on create")
        memory_index = os.path.join(repo, ".claude", "auto-memory", "MEMORY.md")
        result = hook.process(payload("Write", memory_index, NO_TRIGGER, repo))
        check("MEMORY.md create is silent", result is None, str(result))

        print("case 7 — a malformed payload never raises")
        try:
            result = hook.process({})
            ok = result is None
        except Exception as exc:  # pragma: no cover - failure path only
            ok = False
            result = exc
        check("an empty payload returns None without raising", ok, str(result))

    print()
    if FAILURES:
        print("FAILED %d case(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("PASS — retire_trigger_advisory.py proof green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
