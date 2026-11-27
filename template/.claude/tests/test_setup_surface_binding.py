#!/usr/bin/env python3
"""Setup surfaces bind their parity proof.

`_hook_state.SETUP_SURFACE_FILES` names the setup pins outside the harness code directories.
This proof exercises the set and the proof selection it binds with temp repos.
Run: python3 .claude/tests/test_setup_surface_binding.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(TESTS)
REPO = os.path.dirname(CLAUDE)
PARITY_PROOF = "test_setup_surface_parity.py"

sys.path.insert(0, os.path.join(CLAUDE, "scripts"))
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import harness_tests  # noqa: E402
import _hook_state  # noqa: E402

# What the constant must name: the pins SSOT, the cloud script that copies two of its pins, and the
# project MCP registration whose args and env the parity proof reads.
SURFACES = (".mcp.json", ".claude/cloud-setup.sh", ".claude/reference/project_stack.md")
# Proof policy: its edits change which inputs a battery certifies, so it is a setup surface too. The
# proof that reads its proof_heavy / proof_excluded tables binds it.
POLICY_SURFACE = ".claude/skills/project_subsystems/adaptation.json"
POLICY_PROOF = "test_harness_tests_adaptation.py"
# Neither harness code nor a setup surface: nothing binds it, so nothing may be selected for it.
UNBOUND = "docs/notes.md"

FAILURES = []


def case(label, fn):
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a proof reports, it does not stop
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s%s" % (label, (" -- " + str(detail)[:220]) if detail else ""))


# ---------------------------------------------------------------- the temp repo

def git(repo, args):
    result = subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, result.stderr))
    return result.stdout


def write(repo, rel, content):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


def make_repo():
    """A repo carrying the three surfaces, HEAD-committed, plus the parity proof on disk."""
    repo = tempfile.mkdtemp(prefix="surfaces_")
    git(repo, ["init", "-q"])
    git(repo, ["config", "user.email", "t@t.example"])
    git(repo, ["config", "user.name", "t"])
    for rel in SURFACES:
        write(repo, rel, "seed %s\n" % rel)
    write(repo, UNBOUND, "seed\n")
    os.makedirs(os.path.join(repo, ".claude", "tests"), exist_ok=True)
    shutil.copyfile(os.path.join(TESTS, PARITY_PROOF),
                    os.path.join(repo, ".claude", "tests", PARITY_PROOF))
    git(repo, ["add", "-A", "-f"])
    git(repo, ["commit", "-q", "-m", "seed"])
    return repo


# ---------------------------------------------------------------- 1. the set itself

def names_the_surface(rel):
    def check():
        named = tuple(getattr(_hook_state, "SETUP_SURFACE_FILES", ()))
        if rel not in named:
            return False, "%s is not in SETUP_SURFACE_FILES %r" % (rel, named)
        under = [d for d in _hook_state.HARNESS_DIRS if rel == d or rel.startswith(d + "/")]
        if under:
            return False, "%s is already harness code under %s" % (rel, under)
        return True, ""
    return check


# ---------------------------------------------------------------- 2. a change selects the proof

def change_selects(rel, proof=PARITY_PROOF):
    """`harness_tests.py --for <rel>` binds proofs through select_for, which must see the surface."""
    def check():
        repo = make_repo()
        try:
            shutil.copyfile(os.path.join(TESTS, proof), os.path.join(repo, ".claude", "tests", proof))
            selected = [os.path.basename(p) for p in harness_tests.select_for(repo, [rel])]
            if proof not in selected:
                return False, "select_for returned %r" % selected
            return True, ""
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    return check


def unbound_selects_nothing():
    """The negative control: a path that is neither harness code nor a surface must stay invisible,
    or the set would be 'any path at all'."""
    def check():
        repo = make_repo()
        try:
            selected = [os.path.basename(p) for p in harness_tests.select_for(repo, [UNBOUND])]
            if selected:
                return False, "select_for returned %r" % selected
            return True, ""
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    return check


def main():
    case("the harness set does not already match .mcp.json",
         lambda: (not any(".mcp.json" in d for d in _hook_state.HARNESS_DIRS), ""))
    for rel in SURFACES + (POLICY_SURFACE,):
        case("SETUP_SURFACE_FILES names %s, and it is not harness code" % rel, names_the_surface(rel))
    for rel in SURFACES:
        case("a change to %s selects %s" % (rel, PARITY_PROOF), change_selects(rel))
    case("a change to %s selects %s" % (POLICY_SURFACE, POLICY_PROOF),
         change_selects(POLICY_SURFACE, POLICY_PROOF))
    case("a path that is neither harness code nor a surface selects nothing", unbound_selects_nothing())

    if FAILURES:
        print("\nFAILED:\n  " + "\n  ".join(FAILURES))
        sys.exit(1)
    print("all ok (%d surfaces)" % len(SURFACES))


if __name__ == "__main__":
    main()
