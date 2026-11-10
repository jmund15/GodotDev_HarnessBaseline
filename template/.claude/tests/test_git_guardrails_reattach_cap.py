#!/usr/bin/env python3
"""Proof for git_guardrails._reattach_cap_verdict: the commit-time report of a skill or command body past the
client's re-attach cap (`SKILL_REATTACH_CHARS`, counted in characters by `load_census.reattach_over_cap`).

The report is an advisory, never a deny: a body that is over the cap costs a re-invoke after compaction, not a
broken commit. The cases that carry the design are the negatives (a body under the cap, a non-body file, a
body the commit does not touch) and the character-versus-byte boundary, because the byte-based oversized
trigger in `load_census` is a different measure that this report must not reuse.

Run: python3 .claude/tests/test_git_guardrails_reattach_cap.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
HOOK = os.path.join(CLAUDE, "hooks", "git_guardrails.py")
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
from _hook_state import SKILL_REATTACH_CHARS  # noqa: E402

spec = importlib.util.spec_from_file_location("gg_reattach", HOOK)
gg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gg)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def write(d, rel, text):
    full = os.path.join(d, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def body(chars, filler="a"):
    return "# B\n\n" + (filler * (chars - 5))


def repo():
    d = tempfile.mkdtemp(prefix="ggreattach_")
    write(d, ".claude/commands/small.md", body(100))
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
              ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)
    return d


def verdict(d, *paths):
    return gg._reattach_cap_verdict(d, list(paths))


@case("a planted command body one character over the cap is named with its length and the cap")
def _():
    d = repo()
    write(d, ".claude/commands/big.md", body(SKILL_REATTACH_CHARS + 1))
    v = verdict(d, ".claude/commands/big.md") or ""
    return (".claude/commands/big.md" in v and str(SKILL_REATTACH_CHARS + 1) in v.replace(",", "")
            and str(SKILL_REATTACH_CHARS) in v.replace(",", ""))


@case("a planted skill body over the cap is named")
def _():
    d = repo()
    write(d, ".claude/skills/s/SKILL.md", body(SKILL_REATTACH_CHARS + 500))
    return ".claude/skills/s/SKILL.md" in (verdict(d, ".claude/skills/s/SKILL.md") or "")


@case("a body of exactly the cap length is not named")
def _():
    d = repo()
    write(d, ".claude/commands/edge.md", body(SKILL_REATTACH_CHARS))
    return verdict(d, ".claude/commands/edge.md") is None


@case("the count is characters: a body under the cap in characters but over it in bytes is not named")
def _():
    d = repo()
    write(d, ".claude/commands/wide.md", "# W\n\n" + "—" * (SKILL_REATTACH_CHARS - 10))
    path = os.path.join(d, ".claude/commands/wide.md")
    assert os.path.getsize(path) > SKILL_REATTACH_CHARS
    return verdict(d, ".claude/commands/wide.md") is None


@case("a body under the cap is not named")
def _():
    return verdict(repo(), ".claude/commands/small.md") is None


@case("a reference file over the cap is not a re-attached body and is not named")
def _():
    d = repo()
    write(d, ".claude/commands/reference/long.md", body(SKILL_REATTACH_CHARS + 500))
    write(d, ".claude/skills/s/reference/long.md", body(SKILL_REATTACH_CHARS + 500))
    return verdict(d, ".claude/commands/reference/long.md", ".claude/skills/s/reference/long.md") is None


@case("an over-cap body the commit does not touch is not named")
def _():
    d = repo()
    write(d, ".claude/commands/big.md", body(SKILL_REATTACH_CHARS + 1))
    return verdict(d, ".claude/commands/small.md") is None


@case("a touched body that shrank but is still over the cap is named")
def _():
    d = repo()
    write(d, ".claude/commands/big.md", body(SKILL_REATTACH_CHARS + 3000))
    write(d, ".claude/commands/big.md", body(SKILL_REATTACH_CHARS + 10))
    return ".claude/commands/big.md" in (verdict(d, ".claude/commands/big.md") or "")


@case("two over-cap bodies are both named in one report")
def _():
    d = repo()
    write(d, ".claude/commands/a.md", body(SKILL_REATTACH_CHARS + 1))
    write(d, ".claude/skills/s/SKILL.md", body(SKILL_REATTACH_CHARS + 1))
    v = verdict(d, ".claude/commands/a.md", ".claude/skills/s/SKILL.md") or ""
    return ".claude/commands/a.md" in v and ".claude/skills/s/SKILL.md" in v


@case("the report points at the split rule and says it is a report")
def _():
    d = repo()
    write(d, ".claude/commands/big.md", body(SKILL_REATTACH_CHARS + 1))
    v = verdict(d, ".claude/commands/big.md") or ""
    return v.startswith("[harness commit]") and "instruction_quality" in v and "§5" in v


@case("through the hook a commit staging an over-cap body is allowed with the report as additionalContext")
def _():
    d = repo()
    write(d, ".claude/commands/big.md", body(SKILL_REATTACH_CHARS + 1))
    subprocess.run(["git", "-C", d, "add", ".claude/commands/big.md"], capture_output=True)
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": d,
               "tool_input": {"command": "git commit -q -m x"}}
    env = dict(os.environ, HARNESS_NO_PASS_CACHE="1")
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                       timeout=120, env=env, cwd=d)
    if r.returncode != 0:
        return False
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    return ".claude/commands/big.md" in ctx and "re-attach" in ctx


@case("through the hook a commit staging a body under the cap carries no report")
def _():
    d = repo()
    write(d, ".claude/commands/small.md", body(200))
    subprocess.run(["git", "-C", d, "add", ".claude/commands/small.md"], capture_output=True)
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": d,
               "tool_input": {"command": "git commit -q -m x"}}
    env = dict(os.environ, HARNESS_NO_PASS_CACHE="1")
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                       timeout=120, env=env, cwd=d)
    return r.returncode == 0 and "re-attach" not in r.stdout


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
