"""The commit fold and the same-command index-write deny: rows of PushinPotions' `test_git_guardrails_index_fold.py`.

The floor folds a tracked `git add -- <paths> && git commit` into a pathspec commit before any check judges it,
and judges the folded command; an index write it cannot fold still denies the commit after it (the index-write
deny stays until the owner rules on its drop-list entry, D-IW).
"""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

import _git_commit as seam
from case import bash


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(scope="module")
def committed(tmp_path_factory):
    root = tmp_path_factory.mktemp("fold_tpl")
    git(root, "init", "-q")
    git(root, "config", "user.name", "Proof")
    git(root, "config", "user.email", "proof@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "tracked file.txt").write_text("before\n", encoding="utf-8")
    (root / ".claude").mkdir()
    (root / ".claude" / "a.json").write_text("{}\n", encoding="utf-8")
    (root / ".claude" / "baseline.lock.json").write_text("{}\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    return root


@pytest.fixture
def repo(tmp_path, monkeypatch, committed):
    shutil.copytree(committed, tmp_path, dirs_exist_ok=True)
    (tmp_path / "tracked file.txt").write_text("after\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("prefix", ["", "printf '%s' x; ", "printf '%s' x > message.txt; ",
                                    "cat > message.txt <<'EOF'\nbody: git add fake && git commit -m fake\nEOF\n"])
def test_tracked_add_folds_before_the_checks(repo, prefix):
    got = bash(prefix + "git add -- 'tracked file.txt' && git commit -m change", repo, timeout=120000)
    assert got.kind == "rewrite", got.reason
    command = got.updated["command"]
    assert "git add" not in seam.executable_text(command)
    assert seam.parse_commit_args(seam.commit_invocations(command, str(repo))[0].rest)[2] == ["tracked file.txt"]
    assert got.updated.get("timeout") is None or got.updated["timeout"] == 120000
    assert git(repo, "diff", "--cached", "--name-only") == ""


def test_the_rewrite_keeps_every_other_tool_input_key(repo):
    from case import decide
    got = decide({"tool_name": "Bash", "cwd": str(repo), "session_id": "s",
                  "tool_input": {"command": "git add -- 'tracked file.txt' && git commit -m change", "timeout": 120000}})
    assert got.kind == "rewrite" and got.updated["timeout"] == 120000


@pytest.mark.parametrize("write", ["git add new.txt", "git add -A", "git reset -- tracked\\ file.txt", "git rm missing",
                                   "git stash push"])
def test_nonfoldable_index_writes_stay_denied(repo, write):
    (repo / "new.txt").write_text("new", encoding="utf-8")
    got = bash(write + " && git commit -m change", repo)
    assert got.denied and ("runs earlier" in got.reason or "BLOCKED" in got.reason)


def test_read_only_mention_is_unchanged(repo):
    assert bash("printf '%s' 'git add x && git commit -m x'", repo).kind == "allow"


def test_distinct_repositories_do_not_fold(repo):
    assert seam.fold_tracked_adds("git add -- 'tracked file.txt' && git -C elsewhere commit -m change", str(repo)) is None


def test_pwd_resolves_after_cd_and_in_the_check(repo):
    command = 'git -C "$PWD" commit -m change -- "tracked file.txt"'
    assert Path(seam.commit_invocations(command, str(repo))[0].cwd).resolve() == repo.resolve()
    assert bash(command, repo).kind == "allow"
    command = "cd " + shlex.quote(str(repo)) + ' && M=$(pwd) && W=$M && git -C "$W" commit -m change'
    assert Path(seam.commit_invocations(command, "/")[0].cwd).resolve() == repo.resolve()


@pytest.mark.parametrize("command", [
    "git add -- 'tracked file.txt' && git commit -m change -- .claude/baseline.lock.json",
    "git -c core.autocrlf=true add -- 'tracked file.txt' && git commit -m change",
    "NOTE='please add data' git add -- 'tracked file.txt' && git commit -m change",
    "NOTE='git add -- tracked' git add -- 'tracked file.txt' && git commit -m change",
    "git add -- 'tracked file.txt' && git commit -F \"$(cat <<'EOF'\nmessage\nEOF\n)\"",
])
def test_fold_preserves_staging_intent_config_assignments_and_captured_heredocs(repo, command):
    assert seam.fold_tracked_adds(command, str(repo)) is None


@pytest.mark.parametrize("suffix", [" # note", " 2>&1 # note"])
def test_comment_fold_never_publishes_a_peers_index(repo, suffix):
    (repo / "peer.txt").write_text("peer\n", encoding="utf-8")
    git(repo, "add", "peer.txt")
    command = seam.fold_tracked_adds("git add -- 'tracked file.txt' && git commit -m change" + suffix, str(repo))
    assert command is not None
    assert seam.parse_commit_args(seam.commit_invocations(command, str(repo))[0].rest)[2] == ["tracked file.txt"]
    shell = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "bash"
    proc = subprocess.run([shell, "-c", command], cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "tracked file.txt"
    assert git(repo, "diff", "--cached", "--name-only") == "peer.txt"


def test_fold_of_a_quoted_glob_uses_a_git_pathspec(repo):
    folded = seam.fold_tracked_adds("git add -- '.claude/*.json' && git commit -m change", str(repo))
    assert seam.parse_commit_args(seam.commit_invocations(folded, str(repo))[0].rest)[2] == [".claude/*.json"]


@pytest.mark.parametrize("command,expected", [
    ("git -C /peer -C . commit -m x", "/peer"), ("git -C relative -C nested commit -m x", "relative/nested"),
    ('git -C /peer -C "$PWD" commit -m x', "."), ('git -C relative -C "" commit -m x', "relative"),
])
def test_commit_repeated_chdir_resolves_in_order(repo, command, expected):
    invocation = seam.commit_invocations(command, str(repo))[0]
    assert Path(invocation.cwd).resolve() == Path(seam.resolve_cd(str(repo), expected)).resolve()


def nested(body):
    return "bash -c " + shlex.quote(body)


STORED = "git checkout -- 'tracked file.txt'"


@pytest.fixture
def stored(repo):
    git(repo, "hash-object", "-w", "--", "tracked file.txt")
    return repo


def test_nested_checkout_with_stored_evidence_is_judged_on_resolved_facts(stored):
    assert bash(STORED, stored).kind == "allow"
    assert bash(nested(nested(STORED)), stored).kind == "allow"


def test_unresolved_target_borrows_no_evidence(stored, monkeypatch):
    monkeypatch.delenv("S2_UNKNOWN_REPO", raising=False)
    target = stored / "$S2_UNKNOWN_REPO"
    shutil.copytree(stored, target, ignore=shutil.ignore_patterns(target.name))
    assert bash(STORED, target).kind == "allow"
    assert bash('git -C "$S2_UNKNOWN_REPO" restore "tracked file.txt"', stored).denied


def test_launcher_body_is_walked_once(repo, monkeypatch):
    walked = []
    original = seam.literal_launcher_body

    def count(tokens, index):
        body = original(tokens, index)
        if body is not None:
            walked.append(body.body)
        return body
    monkeypatch.setattr(seam, "literal_launcher_body", count)
    import dangerous_shell_guard as dsg
    if getattr(dsg, "literal_launcher_body", None) is original:
        monkeypatch.setattr(dsg, "literal_launcher_body", count)
    assert dsg.git_verdict(nested("git status"), str(repo)) is None  # v1 measured the git check alone
    assert walked == ["git status"]


@pytest.mark.parametrize("rider", [
    'printf "%s" "$(git restore -- peer.txt)"', 'printf "%s" "`git restore -- peer.txt`"', "sudo git restore -- peer.txt",
    "env git restore -- peer.txt", "printf peer.txt | xargs git restore --",
    'S2_TARGET=missing; S2_TARGET=peer.txt; git restore -- "$S2_TARGET"',
], ids=["dollar-substitution", "backticks", "sudo", "env", "xargs", "reassignment"])
def test_hidden_discard_in_a_nested_checkout_still_denies(stored, rider):
    (stored / "peer.txt").write_text("saved base\n", encoding="utf-8")
    git(stored, "add", "--", "peer.txt")
    (stored / "peer.txt").write_text("unsaved edit\n", encoding="utf-8")
    got = bash(nested(STORED + "; " + rider), stored)
    assert got.denied and "`git restore`" in got.reason
