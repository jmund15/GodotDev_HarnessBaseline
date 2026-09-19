"""Re-runnable proof for the harness-commit branch of hooks/git_guardrails.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds the
hook a real PreToolUse payload against a temp git repo and asserts on the emitted
channel (deny via stderr + exit 2, or allow via exit 0).

    python3 .claude/tests/test_git_guardrails_harness_commit.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_TESTS_DIR)
HOOK = os.path.join(_CLAUDE_DIR, "hooks", "git_guardrails.py")

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "scripts"))
from harness_tests import tree_hash, tree_entries  # noqa: E402

ALLOW, DENY = "allow", "deny"


def git(repo, args):
    r = subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, r.stderr))
    return r.stdout


def write(repo, rel, content="x = 1\n"):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


def make_repo():
    repo = tempfile.mkdtemp(prefix="ggharness_")
    git(repo, ["init", "-q"])
    git(repo, ["config", "user.email", "t@t.example"])
    git(repo, ["config", "user.name", "t"])
    write(repo, "README.md", "seed\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed"])
    return repo


def write_stamp(repo, stamp_path, tree_hash_value, legacy=False):
    """The runner's stamp shape: whole-tree hash + per-file digests. `legacy=True` writes the
    pre-per-file shape (hash only) to prove the coarse fallback."""
    os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
    stamp = {"tree_hash": tree_hash_value, "run": 1, "pass": 1, "excluded": []}
    if not legacy:
        stamp["files"] = tree_entries(repo)
    with open(stamp_path, "w", encoding="utf-8") as fh:
        json.dump(stamp, fh)


def run_hook(command, repo, env_extra=None):
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo,
               "tool_input": {"command": command}}
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                        capture_output=True, text=True, timeout=30, env=env, cwd=repo)
    if r.returncode == 2:
        return DENY, r.stderr
    return ALLOW, r.stdout


def case(label, got, expected, needle=""):
    got_verdict, reason = got
    ok = got_verdict == expected and (not needle or needle in reason)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        return "%s\n     expected=%s got=%s reason=%r" % (label, expected, got_verdict, reason[:300])
    return None


def main():
    failures = []

    # 0. A Git-Bash `cd /c/...` prefix must resolve. `os.path.join` on Windows turns the MSYS
    #    drive form into `<cwd>\c\Users\...`, a path that does not exist -- so `git rev-parse`
    #    failed there and the guard BLOCKED a commit for a repo it had merely failed to find.
    #    Both halves are asserted: the MSYS form resolves, and a genuinely bad path still denies.
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "Tests/Foo.cs"])
    # The MSYS drive form exists only on Windows; elsewhere the plain path is the shell's own form.
    msys = "/" + repo[0].lower() + repo[2:].replace("\\", "/") if os.name == "nt" else repo
    failures.append(case(
        "a `cd /c/...` prefix resolves to the repo, so a clean commit is not blocked",
        run_hook("cd %s; git commit -F msg -- Tests/Foo.cs" % msys, repo), ALLOW))

    # 1. commit of a non-harness path -> allow, no stamp needed at all.
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "Tests/Foo.cs"])
    failures.append(case(
        "commit of Tests/Foo.cs only -> allow",
        run_hook('git commit -F msg -- Tests/Foo.cs', repo), ALLOW))

    # 2. staged hook with a fresh stamp + proof -> allow.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tools/y.py", "y = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    write(repo, ".claude/tests/y_test.py", "# proof\n")
    git(repo, ["add", "-A"])
    stamp_path = os.path.join(repo, "stamp.json")
    failures.append(case(
        "staged hook+tool w/ proofs but no stamp -> deny (no stamp)",
        run_hook('git commit -F msg -- .claude', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "no stamp"))
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "staged hook+tool w/ proofs + fresh stamp -> allow",
        run_hook('git commit -F msg -- .claude', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        ALLOW))

    # 2b. `instruction_quality` before a harness edit is enforced at the Write|Edit call
    #     (harness_edit_skill_reminder.py), which any other write route dodges: a Bash heredoc,
    #     `sed -i`, a python script run through Bash. The staged set is the one signal no route can
    #     dodge, so the commit is the backstop. Boundary: with NO session state file there is no
    #     session to judge -- CI runs this battery that way -- so the rule stays silent there.
    skill_state = tempfile.mkdtemp(prefix="ggskill_")
    skill_env = {"HARNESS_TEST_STAMP": stamp_path, "HARNESS_HOOK_STATE_DIR": skill_state}

    def mark_loaded(skills):
        with open(os.path.join(skill_state, "s1.json"), "w", encoding="utf-8") as fh:
            json.dump({"skills_loaded": skills}, fh)

    mark_loaded(["something_else"])
    failures.append(case(
        "staged harness commit, skill not loaded -> deny naming instruction_quality",
        run_hook('git commit -F msg -- .claude', repo, skill_env), DENY, "instruction_quality"))
    mark_loaded(["instruction_quality"])
    failures.append(case(
        "staged harness commit, skill loaded -> allow",
        run_hook('git commit -F msg -- .claude', repo, skill_env), ALLOW))
    failures.append(case(
        "no session state at all (CI shape) -> the skill rule stays silent",
        run_hook('git commit -F msg -- .claude', repo,
                 {"HARNESS_TEST_STAMP": stamp_path,
                  "HARNESS_HOOK_STATE_DIR": tempfile.mkdtemp(prefix="ggnosess_")}), ALLOW))

    # 3. stale stamp: edit a covered hook after stamping -> deny.
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    failures.append(case(
        "editing a hook after stamping -> stale stamp denied",
        run_hook('git commit -F msg -- .claude', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "stale stamp"))

    # 4. staged hook without a proof -> deny naming the expected proof path.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    stamp_path = os.path.join(repo, "stamp.json")
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "staged hook without proof -> deny naming tests/test_x*.py",
        run_hook('git commit -F msg -- .claude', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "tests/test_x*.py"))

    # 5. staged tool without a proof -> deny.
    repo = make_repo()
    write(repo, ".claude/tools/y.py", "y = 1\n")
    git(repo, ["add", "-A"])
    stamp_path = os.path.join(repo, "stamp.json")
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "staged tool without proof -> deny naming tests/test_y*.py",
        run_hook('git commit -F msg -- .claude', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "tests/test_y*.py"))

    # 6. git show naming a hook path is read-only, never the commit branch -> allow.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "add hook"])
    failures.append(case(
        "git show HEAD:.claude/hooks/x.py -> allow",
        run_hook("git show HEAD:.claude/hooks/x.py", repo), ALLOW))

    # 7. -a form reaches the same verdict as an explicit pathspec.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed hook"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")  # unstaged dirty edit
    stamp_path = os.path.join(repo, "stamp.json")
    failures.append(case(
        "-a form with no stamp -> deny (no stamp)",
        run_hook('git commit -a -F msg', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "no stamp"))
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "explicit pathspec form after the same edit -> same allow verdict",
        run_hook('git commit -F msg -- .claude/hooks/x.py', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        ALLOW))

    # 8. git failure (cwd not a repo) -> deny.
    not_repo = tempfile.mkdtemp(prefix="ggharness_norepo_")
    write(not_repo, ".claude/hooks/x.py", "x = 1\n")
    failures.append(case(
        "cwd is not a git repo -> deny",
        run_hook('git commit -F msg -- .claude', not_repo), DENY))

    # 9. HARNESS_ALLOW_UNSTAMPED_HARNESS=1 bypasses the stamp entirely.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        "HARNESS_ALLOW_UNSTAMPED_HARNESS=1 -> allow with no stamp and no proof",
        run_hook('git commit -F msg -- .claude', repo,
                  {"HARNESS_TEST_STAMP": os.path.join(repo, "no_such_stamp.json"),
                   "HARNESS_ALLOW_UNSTAMPED_HARNESS": "1"}),
        ALLOW))

    # 10. directory pathspec: an untouched tracked hook without a proof is NOT demanded;
    #     a changed untested hook under the same pathspec still is.
    repo = make_repo()
    write(repo, ".claude/hooks/untouched.py", "u = 1\n")
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed hooks"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    git(repo, ["add", ".claude/hooks/x.py"])
    stamp_path = os.path.join(repo, "stamp.json")
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "dir pathspec: untouched untested hook not demanded -> allow",
        run_hook('git commit -F msg -- .claude/hooks', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        ALLOW))
    write(repo, ".claude/hooks/untouched.py", "u = 2\n")
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "dir pathspec: changed untested hook -> deny naming tests/test_untouched*.py",
        run_hook('git commit -F msg -- .claude/hooks', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "tests/test_untouched*.py"))

    # 11. combined short flags: `-am` must read as `-a` (dirty worktree edit committed).
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed hook"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    stamp_path = os.path.join(repo, "stamp.json")
    failures.append(case(
        "-am form with a dirty hook and no stamp -> deny (no stamp)",
        run_hook('git commit -am "tweak hook"', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "no stamp"))

    # 12. --amend republishes HEAD's harness paths even with a clean index.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "add hook"])
    failures.append(case(
        "--amend --no-edit with a clean index and no stamp -> deny (no stamp)",
        run_hook('git commit --amend --no-edit', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "no stamp"))

    # 13. A broken runner import denies harness commits instead of killing the whole guard:
    #     a copy of the hook in a tree with no scripts/ dir cannot import harness_tests.
    broken_root = tempfile.mkdtemp(prefix="ggharness_broken_")
    broken_hooks = os.path.join(broken_root, ".claude", "hooks")
    os.makedirs(broken_hooks)
    for sibling in ("git_guardrails.py", "_hook_state.py", "_git_commit.py", "_command_text.py"):
        shutil.copy(os.path.join(_CLAUDE_DIR, "hooks", sibling), os.path.join(broken_hooks, sibling))
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo,
               "tool_input": {"command": "git commit -F msg -- .claude"}}
    r = subprocess.run([sys.executable, os.path.join(broken_hooks, "git_guardrails.py")],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=repo)
    got = (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)
    failures.append(case("broken harness_tests import -> deny naming the machinery", got, DENY, "machinery unavailable"))
    write(repo, "README.md", "seed2\n")
    git(repo, ["add", "-A"])
    payload["tool_input"]["command"] = "git push --force"
    r = subprocess.run([sys.executable, os.path.join(broken_hooks, "git_guardrails.py")],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=repo)
    got = (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)
    failures.append(case("broken import still blocks force-push", got, DENY, "force-push"))

    # 14. A heredoc BODY quoting a destructive command is data, not a command -> allow.
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "Tests/Foo.cs"])
    heredoc = ("cat > msg <<'EOF'\nfix(guard): deny git reset --hard and git commit -am bypass\nEOF\n"
               "git commit -F msg -- Tests/Foo.cs")
    failures.append(case(
        "heredoc body quoting `git reset --hard` -> allow (body is data)",
        run_hook(heredoc, repo), ALLOW))
    failures.append(case(
        "the same text OUTSIDE a heredoc still denies",
        run_hook("git reset --hard", repo), DENY, "reset --hard"))

    # 15. The harness check runs against the repo the commit TARGETS, not the payload cwd.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    elsewhere = make_repo()
    stamp_missing = {"HARNESS_TEST_STAMP": os.path.join(repo, "no_such_stamp.json")}
    failures.append(case(
        "git -C <repo> commit from another cwd -> deny (no stamp) against the target repo",
        run_hook('git -C "%s" commit -F msg -- .claude' % repo, elsewhere, stamp_missing),
        DENY, "no stamp"))
    failures.append(case(
        "cd <repo> && git commit from another cwd -> deny (no stamp) against the target repo",
        run_hook('cd "%s" && git commit -F msg -- .claude' % repo, elsewhere, stamp_missing),
        DENY, "no stamp"))

    # 16. A proof on disk that git does not track satisfies nothing (.claude/tests/ is gitignored).
    repo = make_repo()
    write(repo, ".gitignore", ".claude/tests/\n")
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])   # the ignore rule keeps the proof out of the index
    stamp_path = os.path.join(repo, "stamp.json")
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "gitignored, untracked proof -> deny naming tests/test_x*.py",
        run_hook('git commit -F msg -- .claude', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "tests/test_x*.py"))
    git(repo, ["add", "-f", ".claude/tests/test_x_ok.py"])
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "the same proof after `git add -f` -> allow",
        run_hook('git commit -F msg -- .claude', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        ALLOW))

    # 17. The bypass works in the shape the deny text names: an inline env prefix on the command.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        "inline HARNESS_ALLOW_UNSTAMPED_HARNESS=1 prefix -> allow with no stamp and no proof",
        run_hook('HARNESS_ALLOW_UNSTAMPED_HARNESS=1 git commit -F msg -- .claude', repo,
                 {"HARNESS_TEST_STAMP": os.path.join(repo, "no_such_stamp.json")}),
        ALLOW))

    # 18. Without `_hook_state` the guard cannot name the harness set: every commit denies,
    #     the message names the repair, and the inline bypass still reaches it.
    lone_root = tempfile.mkdtemp(prefix="ggharness_lone_")
    lone_hooks = os.path.join(lone_root, ".claude", "hooks")
    os.makedirs(lone_hooks)
    for sibling in ("git_guardrails.py", "_git_commit.py", "_command_text.py"):   # no _hook_state.py
        shutil.copy(os.path.join(_CLAUDE_DIR, "hooks", sibling), os.path.join(lone_hooks, sibling))
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "-A"])
    for command, expected, needle, label in (
        ("git commit -F msg -- Tests/Foo.cs", DENY, "guard state unavailable",
         "missing _hook_state -> even a non-harness commit denies, naming the repair"),
        ("HARNESS_ALLOW_UNSTAMPED_HARNESS=1 git commit -F msg -- Tests/Foo.cs", ALLOW, "",
         "missing _hook_state -> inline bypass still allows"),
    ):
        payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo,
                   "tool_input": {"command": command}}
        r = subprocess.run([sys.executable, os.path.join(lone_hooks, "git_guardrails.py")],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=repo)
        got = (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)
        failures.append(case(label, got, expected, needle))

    # 19. Per-file freshness: a peer's edit to an UNTOUCHED hook after stamping does not stale
    #     this commit; an edit to the committed hook still does; a legacy hash-only stamp
    #     falls back to the coarse whole-tree comparison.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    write(repo, ".claude/hooks/peer.py", "p = 1\n")
    write(repo, ".claude/tests/test_peer_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    git(repo, ["add", ".claude/hooks/x.py"])
    stamp_path = os.path.join(repo, "stamp.json")
    write_stamp(repo, stamp_path, tree_hash(repo))
    write(repo, ".claude/hooks/peer.py", "p = 2\n")   # peer edits an unrelated, unstaged hook
    failures.append(case(
        "peer edit to an untouched hook after stamping -> allow (per-file freshness)",
        run_hook('git commit -F msg -- .claude/hooks/x.py', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        ALLOW))
    write(repo, ".claude/hooks/x.py", "x = 3\n")
    failures.append(case(
        "edit to the committed hook after stamping -> deny naming it",
        run_hook('git commit -F msg -- .claude/hooks/x.py', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, ".claude/hooks/x.py"))
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    write_stamp(repo, stamp_path, tree_hash(repo), legacy=True)
    write(repo, ".claude/hooks/peer.py", "p = 3\n")
    failures.append(case(
        "legacy hash-only stamp -> coarse fallback still denies on any tree change",
        run_hook('git commit -F msg -- .claude/hooks/x.py', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "stale stamp"))

    # 20. Merge / cherry-pick that bring harness code in are denied unless --no-commit;
    #     a merge of non-harness content passes.
    repo = make_repo()
    git(repo, ["checkout", "-q", "-b", "feature"])
    write(repo, ".claude/hooks/h.py", "h = 1\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "feature hook"])
    git(repo, ["checkout", "-q", "-b", "docs-only"])
    git(repo, ["reset", "-q", "--hard", "HEAD~1"])
    write(repo, "README.md", "docs\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "docs"])
    default = git(repo, ["rev-parse", "--abbrev-ref", "HEAD~1"]).strip()
    git(repo, ["checkout", "-q", git(repo, ["log", "--format=%H", "-1", "HEAD~1"]).strip()])
    git(repo, ["checkout", "-q", "-b", "trunk"])
    failures.append(case(
        "git merge <branch carrying a hook> -> deny naming --no-commit",
        run_hook("git merge feature", repo), DENY, "--no-commit"))
    failures.append(case(
        "git merge --no-commit <branch carrying a hook> -> allow (the later commit is judged)",
        run_hook("git merge --no-commit feature", repo), ALLOW))
    failures.append(case(
        "git cherry-pick <rev carrying a hook> -> deny naming --no-commit",
        run_hook("git cherry-pick feature", repo), DENY, "--no-commit"))
    failures.append(case(
        "git merge <branch with no harness content> -> allow",
        run_hook("git merge docs-only", repo), ALLOW))

    # Live block 2026-09-14: a pathspec commit publishes only its pathspec (git's default --only
    # mode) and other staged paths stay staged, yet the guard judged the whole index.
    repo = make_repo()
    write(repo, ".claude/hooks/unproven.py", "x = 1\n")
    write(repo, "docs/note.md", "note\n")
    write(repo, "docs_list.txt", "docs/note.md\n")
    write(repo, "hook_list.txt", ".claude/hooks/unproven.py\n")
    git(repo, ["add", ".claude/hooks/unproven.py", "docs/note.md"])
    failures.append(case(
        "commit -- <docs> with an unproven hook staged elsewhere -> allow",
        run_hook("git commit -F msg -- docs/note.md", repo), ALLOW))
    failures.append(case(
        "commit --pathspec-from-file=<docs list> -> allow",
        run_hook("git commit -F msg --pathspec-from-file=docs_list.txt", repo), ALLOW))
    failures.append(case(
        "commit --pathspec-from-file <list naming the hook> -> deny",
        run_hook("git commit -F msg --pathspec-from-file hook_list.txt", repo), DENY))
    failures.append(case(
        "commit -i -- <docs> also publishes the staged hook -> deny",
        run_hook("git commit -i -F msg -- docs/note.md", repo), DENY))
    failures.append(case(
        "commit --pathspec-from-file=- (stdin cannot be read here) -> deny",
        run_hook("git commit -F msg --pathspec-from-file=-", repo), DENY))
    failures.append(case(
        "plain commit with the unproven hook staged -> deny",
        run_hook("git commit -F msg", repo), DENY))

    # Live block 2026-09-14: a removed hook was asked for a proof, which leaves in the same commit.
    repo = make_repo()
    write(repo, ".claude/hooks/gone.py", "x = 1\n")
    write(repo, ".claude/tests/test_gone.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "add gone"])
    git(repo, ["rm", "-q", ".claude/hooks/gone.py", ".claude/tests/test_gone.py"])
    stamp_path = os.path.join(repo, "stamp.json")
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "staged deletion of a hook and its proof + fresh stamp -> allow",
        run_hook("git commit -F msg -- .claude", repo, {"HARNESS_TEST_STAMP": stamp_path}), ALLOW))
    repo = make_repo()
    write(repo, ".claude/hooks/index_only.py", "x = 1\n")
    git(repo, ["add", "-A"])
    os.remove(os.path.join(repo, ".claude", "hooks", "index_only.py"))
    stamp_path = os.path.join(repo, "stamp.json")
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "a hook still in the index but gone from the worktree needs its proof -> deny",
        run_hook("git commit -F msg", repo, {"HARNESS_TEST_STAMP": stamp_path}), DENY,
        "no re-runnable proof"))

    # A commit through another index is judged against THAT index. The guard read the default
    # index, found no harness path staged there and allowed an unstamped, unproven hook commit.
    repo = make_repo()
    write(repo, ".claude/hooks/alt_index_hook.py", "x = 1\n")
    alt_index = os.path.join(repo, "alt.index").replace("\\", "/")
    alt_env = dict(os.environ, GIT_INDEX_FILE=alt_index)
    subprocess.run(["git", "read-tree", "HEAD"], cwd=repo, env=alt_env, check=True, capture_output=True)
    subprocess.run(["git", "add", ".claude/hooks/alt_index_hook.py"], cwd=repo, env=alt_env,
                   check=True, capture_output=True)
    no_stamp = {"HARNESS_TEST_STAMP": os.path.join(repo, "no_such_stamp.json")}
    failures.append(case(
        "inline GIT_INDEX_FILE staging an unproven hook -> deny",
        run_hook("GIT_INDEX_FILE=%s git commit -F msg" % alt_index, repo, no_stamp), DENY))
    failures.append(case(
        "exported GIT_INDEX_FILE staging an unproven hook -> deny",
        run_hook("export GIT_INDEX_FILE=%s && git commit -F msg" % alt_index, repo, no_stamp), DENY))
    failures.append(case(
        "the default index, with nothing staged, still allows",
        run_hook("git commit -F msg", repo, no_stamp), ALLOW))
    # 21. S8: `.claude/settings.base.json` is a HARNESS_DIRS member same as `settings.json` --
    #     a commit touching it with no fresh stamp is denied, and a fresh stamp allows it.
    repo = make_repo()
    write(repo, ".claude/settings.base.json", '{"permissions": {"allow": []}}\n')
    git(repo, ["add", "-A"])
    stamp_path = os.path.join(repo, "stamp.json")
    failures.append(case(
        "settings.base.json with no stamp -> deny (no stamp)",
        run_hook('git commit -F msg -- .claude/settings.base.json', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        DENY, "no stamp"))
    write_stamp(repo, stamp_path, tree_hash(repo))
    failures.append(case(
        "settings.base.json with a fresh stamp -> allow",
        run_hook('git commit -F msg -- .claude/settings.base.json', repo, {"HARNESS_TEST_STAMP": stamp_path}),
        ALLOW))

    total = len(failures)
    failures = [f for f in failures if f]
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
