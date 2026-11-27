"""Re-runnable proof for scripts/harness_tests.py scoped binding and per-input stamping.

Covers the mention rule for a basename shared across `.claude/` (binds by path only), declared
`proof_bindings` (replace the mention rule for that input), and a scoped run that stamps each
input whose own bound proofs passed while naming each input a red proof blocked.

    python3 .claude/tests/test_harness_tests_scoped_binding.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


def make_repo(files, proofs):
    """`files` {rel-under-.claude: text}; `proofs` {proof name: python source}. Committed."""
    repo = tempfile.mkdtemp(prefix="harnessbinding_")
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=repo, check=True)
    for d in ("hooks", "tools", "scripts", "workflows", "tests"):
        os.makedirs(os.path.join(repo, ".claude", d), exist_ok=True)
    for rel, text in list(files.items()) + [("tests/" + n, s) for n, s in proofs.items()]:
        path = os.path.join(repo, ".claude", *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    subprocess.run(["git", "add", "-A", "-f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    harness_tests.EXCLUDED = {}
    harness_tests.HEAVY = {}
    harness_tests.PROOF_BINDINGS = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    for attr, name in (("DURATIONS_PATH", "durations.json"), ("FLAKY_LOG", "flaky.jsonl")):
        if hasattr(harness_tests, attr):
            setattr(harness_tests, attr, os.path.join(repo, name))
    return repo


def proof(exit_code, mention=""):
    return "import sys\nNAMES = %r\nsys.exit(%d)\n" % (mention, exit_code)


def names(paths):
    return sorted(os.path.basename(p) for p in paths)


def quiet(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return rc, buf.getvalue()


def case_shared_basename_binds_by_path_only():
    repo = make_repo(
        {"skills/alpha/SKILL.md": "a\n", "skills/beta/SKILL.md": "b\n"},
        {"test_mentions_basename.py": proof(0, "reads SKILL.md"),
         "test_mentions_parent_path.py": proof(0, "reads alpha/SKILL.md"),
         "test_mentions_repo_path.py": proof(0, "reads .claude/skills/alpha/SKILL.md"),
         "test_mentions_other_path.py": proof(0, "reads beta/SKILL.md")})
    got = names(harness_tests.select_for(repo, [".claude/skills/alpha/SKILL.md"]))
    check("a shared basename binds a proof naming its parent-dir or repo path, not one naming only the basename",
          got == ["test_mentions_parent_path.py", "test_mentions_repo_path.py"], got)


def case_unique_basename_binds_by_mention_and_name():
    repo = make_repo(
        {"tools/solo.py": "X = 1\n", "skills/alpha/SKILL.md": "a\n", "skills/beta/SKILL.md": "b\n"},
        {"test_mentions_solo.py": proof(0, "runs solo.py"),
         "test_solo_contract.py": proof(0),
         "test_unrelated.py": proof(0, "reads SKILL.md")})
    got = names(harness_tests.select_for(repo, [".claude/tools/solo.py"]))
    check("a unique basename still binds by mention and by the test_<stem> name rule",
          got == ["test_mentions_solo.py", "test_solo_contract.py"], got)


def case_declared_binding_replaces_mention():
    lock = ".claude/baseline.lock.json"
    repo = make_repo(
        {"baseline.lock.json": "{}\n"},
        {"test_fixture_names_lock.py": proof(0, "writes a fixture baseline.lock.json"),
         "test_reads_real_lock.py": proof(0, "reads the live lock rows"),
         "test_unrelated.py": proof(0)})
    before = names(harness_tests.select_for(repo, [lock]))
    harness_tests.PROOF_BINDINGS = {lock: ["test_reads_real_lock.py"]}
    after = names(harness_tests.select_for(repo, [lock]))
    check("a declared proof_bindings input binds exactly its declared proofs, never by mention",
          before == ["test_fixture_names_lock.py"] and after == ["test_reads_real_lock.py"],
          "before=%s after=%s" % (before, after))


def _per_input_repo(red_exit):
    repo = make_repo({"hooks/alpha.py": "X = 1\n", "hooks/beta.py": "X = 1\n"},
                     {"test_alpha.py": proof(0), "test_beta.py": proof(0)})
    # The red lands after the seed commit: a proof red at HEAD too is a pre-existing red that never blocks.
    with open(os.path.join(repo, ".claude", "tests", "test_alpha.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(proof(red_exit))
    harness_tests._write_stamp(repo, 2, 2, [])
    before = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    for rel in ("hooks/alpha.py", "hooks/beta.py"):
        with open(os.path.join(repo, ".claude", *rel.split("/")), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("Y = 2\n")
    rc, out = quiet(harness_tests.run_scoped, repo, [".claude/hooks/alpha.py", ".claude/hooks/beta.py"])
    after = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    return repo, rc, out, before, after


def _named_unstamped(out, rel, blocker):
    return any("unstamped" in line and rel in line and blocker in line for line in out.splitlines())


def case_red_proof_leaves_only_its_inputs_unstamped():
    repo, rc, out, before, after = _per_input_repo(1)
    cur = harness_tests.tree_entries(repo)
    ok = (rc == 1
          and after[".claude/hooks/beta.py"] == cur[".claude/hooks/beta.py"]
          and after[".claude/hooks/alpha.py"] == before[".claude/hooks/alpha.py"]
          and _named_unstamped(out, ".claude/hooks/alpha.py", "test_alpha.py"))
    check("one red proof leaves only its own inputs unstamped, names them, and stamps the rest",
          ok, "rc=%s beta_fresh=%s alpha_kept=%s out=%r" % (
              rc, after.get(".claude/hooks/beta.py") == cur[".claude/hooks/beta.py"],
              after.get(".claude/hooks/alpha.py") == before[".claude/hooks/alpha.py"], out[-400:]))


def case_cannot_run_proof_leaves_only_its_inputs_unstamped():
    repo, rc, out, before, after = _per_input_repo(2)
    cur = harness_tests.tree_entries(repo)
    ok = (rc == 2
          and after[".claude/hooks/beta.py"] == cur[".claude/hooks/beta.py"]
          and after[".claude/hooks/alpha.py"] == before[".claude/hooks/alpha.py"]
          and _named_unstamped(out, ".claude/hooks/alpha.py", "test_alpha.py"))
    check("a cannot-run proof leaves only its own inputs unstamped and the run exits 2",
          ok, "rc=%s out=%r" % (rc, out[-400:]))


def main():
    for case in (case_shared_basename_binds_by_path_only, case_unique_basename_binds_by_mention_and_name,
                 case_declared_binding_replaces_mention, case_red_proof_leaves_only_its_inputs_unstamped,
                 case_cannot_run_proof_leaves_only_its_inputs_unstamped):
        try:
            case()
        except Exception as exc:  # a crash in one case must not hide the others
            check(case.__name__, False, "%s: %s" % (type(exc).__name__, exc))
    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for failure in FAILURES:
        print("  FAIL " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
