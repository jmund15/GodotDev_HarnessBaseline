"""Branch force-delete and path discards judged on observed loss, never on an owner answer: the rows of v1's
`test_git_guardrails_owner_branch_delete.py`, `test_git_guardrails_owner_discard.py` and
`test_git_guardrails_scratch_branch_delete.py` (both repos; MaddManager's copies hold a subset). Each file's
spawned case is the one spawned Bash case in `test_floor_entry.py`.

Each repository is built once per module and copied per row, so no row's ref or object write reaches another.
"""
import json
import shutil

import pytest

from case import bash
from conftest import git

PRESERVE_HINT = "git update-ref refs/preserve/<name> <branch>"
PRESERVE_STEP = "git update-ref refs/preserve/"


def answer_row(question, label="Delete the branch (Recommended)", sidechain=False):
    return {"type": "user", "isSidechain": sidechain, "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_q",
        "content": 'Your questions have been answered: "%s"="%s".' % (question, label)}]},
        "toolUseResult": {"questions": [{"question": question, "header": "Discard", "multiSelect": False,
                                         "options": [{"label": label, "description": ""}]}],
                          "answers": {question: label}}}


def prompt_row(text):
    return {"type": "user", "message": {"role": "user", "content": text}, "uuid": "later-prompt"}


def run(repo, command, rows=(), tmp=None):
    transcript = (tmp or repo.parent) / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return bash(command, repo, session_id="owner", transcript_path=transcript)


def copy_of(template, tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(template, dest)
    return dest


# --- owner_branch_delete ------------------------------------------------------------------------------------------
BRANCH, OTHER = "worktree-wf_old-1", "feature/kept"


@pytest.fixture(scope="module")
def branch_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("branch_tpl") / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    for name in (BRANCH, OTHER, BRANCH + "-x"):
        git(root, "checkout", "-q", "-b", name, "main")
        (root / "a.txt").write_text(name + "\n", encoding="utf-8")
        git(root, "commit", "-qam", "unapplied " + name)
    git(root, "checkout", "-q", "main")
    return root


@pytest.fixture
def branches(branch_template, tmp_path):
    return copy_of(branch_template, tmp_path)


def test_unreachable_branch_denies_and_names_the_preserve_step(branches):
    got = run(branches, "git branch -D %s" % BRANCH)
    assert got.denied and PRESERVE_HINT in got.reason and "holds commits no other ref has" in got.reason


def test_preserve_ref_makes_the_same_delete_pass(branches):
    git(branches, "update-ref", "refs/preserve/kept", git(branches, "rev-parse", "refs/heads/" + BRANCH).strip())
    assert run(branches, "git branch -D %s" % BRANCH).kind == "allow"


def test_owner_answer_does_not_unlock_a_branch_delete(branches):
    rows = [answer_row("Force-delete branch %s (superseded)?" % BRANCH), prompt_row("delete " + BRANCH)]
    assert run(branches, "git branch -D %s" % BRANCH, rows).denied


def test_scratch_branch_allows(branches):
    git(branches, "branch", "scratch/fx", "main")
    assert run(branches, "git branch -D scratch/fx").kind == "allow"


def test_patch_equivalent_branch_allows(branches):
    git(branches, "checkout", "-q", "-b", "cherried", "main")
    (branches / "a.txt").write_text("cherried\n", encoding="utf-8")
    git(branches, "commit", "-qam", "cherried work")
    git(branches, "checkout", "-q", "main")
    git(branches, "cherry-pick", "cherried")
    assert run(branches, "git branch -D cherried").kind == "allow"


def test_merged_branch_allows(branches):
    git(branches, "checkout", "-q", "-b", "merged", "main")
    (branches / "a.txt").write_text("merged\n", encoding="utf-8")
    git(branches, "commit", "-qam", "merged work")
    git(branches, "checkout", "-q", "main")
    git(branches, "merge", "-q", "--no-ff", "-m", "merge", "merged")
    assert run(branches, "git branch -D merged").kind == "allow"


def test_tag_holding_the_tip_allows(branches):
    git(branches, "tag", "keep-tip", BRANCH)
    assert run(branches, "git branch -D %s" % BRANCH).kind == "allow"


@pytest.mark.parametrize("command", ["git branch -D feature/missing", "git branch -r -D %s" % OTHER])
def test_missing_branch_or_unknown_flag_denies(branches, command):
    assert run(branches, command).denied


def test_two_branches_where_one_is_unreachable_deny_naming_both(branches):
    got = run(branches, "git branch -D %s %s" % (BRANCH, OTHER))
    assert got.denied and PRESERVE_HINT in got.reason and BRANCH in got.reason and OTHER in got.reason
    assert "holds commits no other ref has" in got.reason


# --- owner_discard -------------------------------------------------------------------------------------------------
A, B = "Tests/regression_baseline.json", "Tests/integration_batch_durations.json"
EXTRA = ("LICENSE", "addons/foo.v2/x.txt", ".claude/x.txt")
QUESTION = "Discard the uncommitted edits to %s and %s?" % (A, B)


@pytest.fixture(scope="module")
def discard_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("discard_tpl") / "repo"
    (root / "Tests").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    for path in (A, B, "Tests/other.json", *EXTRA):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("{}\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    for path in (A, B, "Tests/other.json", *EXTRA):
        (root / path).write_text('{"dirty": 1}\n', encoding="utf-8")
    return root


@pytest.fixture
def dirty(discard_template, tmp_path):
    return copy_of(discard_template, tmp_path)


def _approve(repo):
    return answer_row(QUESTION.replace(A, str(repo / A)).replace(B, str(repo / B)), "Yes, discard (Recommended)")


@pytest.mark.parametrize("command", ["git restore %s" % A, "git checkout -- %s" % A])
def test_stored_bytes_allow_discard(dirty, command):
    git(dirty, "hash-object", "-w", A)
    assert run(dirty, command).kind == "allow"


def test_restore_of_unsaved_bytes_denies_and_names_the_preserve_step(dirty):
    got = run(dirty, "git restore %s" % A)
    assert got.denied and "save it first" in got.reason and PRESERVE_STEP in got.reason


def test_checkout_of_unsaved_bytes_denies(dirty):
    assert run(dirty, "git checkout -- %s" % A).denied


def test_saving_the_bytes_makes_the_same_discard_pass(dirty):
    assert run(dirty, "git restore %s" % A).denied
    git(dirty, "hash-object", "-w", A)
    assert run(dirty, "git restore %s" % A).kind == "allow"


def test_checkout_from_head_deny_names_a_next_step_that_passes(dirty):
    got = run(dirty, 'git checkout HEAD -- %s %s 2>&1 | tail -3; git status --short | grep -v "^??"' % (A, B))
    assert got.denied and PRESERVE_STEP in got.reason and "git restore --source=HEAD --" in got.reason
    for path in (A, B):
        git(dirty, "hash-object", "-w", path)
    assert run(dirty, "git restore --source=HEAD -- %s %s" % (A, B)).kind == "allow"


def test_restore_from_head_still_denies_an_unsaved_path(dirty):
    (dirty / B).write_text('{"dirty": 2}\n', encoding="utf-8")
    git(dirty, "hash-object", "-w", A)
    assert run(dirty, "git restore --source=HEAD -- %s %s" % (A, B)).denied


def test_owner_answer_does_not_unlock_a_discard(dirty):
    for rows in ([_approve(dirty)], [prompt_row("reset " + A)]):
        assert run(dirty, "git checkout -- %s" % A, rows).denied


def test_sidechain_answer_does_not_unlock(dirty):
    side = _approve(dirty)
    side["isSidechain"] = True
    assert run(dirty, "git restore %s" % A, [side]).denied


def test_checkout_from_head_of_a_clean_path_allows(dirty):
    (dirty / A).write_text("{}\n", encoding="utf-8")
    assert run(dirty, "git checkout HEAD -- %s" % A).kind == "allow"


def test_checkout_from_another_ref_over_an_edit_denies(dirty):
    assert run(dirty, "git checkout HEAD -- %s" % A).denied


def test_pycache_path_allows(dirty):
    cache = dirty / "Tools/Calc/__pycache__/calc.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"bytecode")
    assert run(dirty, "git checkout -- Tools/Calc/__pycache__/calc.pyc").kind == "allow"


@pytest.mark.parametrize("command", ["git checkout -- .", "git restore Tests/*.json", "git checkout -- Tests",
                                     "git restore ."])
def test_broad_pathspec_denies(dirty, command):
    assert run(dirty, command, [_approve(dirty)]).denied


def test_rider_on_an_unnamed_path_denies(dirty):
    assert run(dirty, "git checkout -- %s; git checkout -- Tests/other.json" % A, [_approve(dirty)]).denied


def test_restore_staged_alone_allows(dirty):
    assert run(dirty, "git restore --staged " + A).kind == "allow"


def test_worktree_bytes_the_store_lacks_deny(dirty):
    (dirty / A).write_text('{"unsaved": 2}\n', encoding="utf-8")
    assert run(dirty, "git restore %s" % A).denied


# --- scratch_branch_delete -----------------------------------------------------------------------------------------
SCRATCH_HEAD, SCRATCH_WORK, FEATURE = "scratch/wl-q4-fix", "scratch/wl-q4-work", "feature/kept"


@pytest.fixture(scope="module")
def scratch_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("scratch_tpl") / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    git(root, "branch", SCRATCH_HEAD)
    git(root, "checkout", "-q", "-b", SCRATCH_WORK, "main")
    (root / "a.txt").write_text("work\n", encoding="utf-8")
    git(root, "commit", "-qam", "unapplied scratch work")
    git(root, "checkout", "-q", "-b", FEATURE, "main")
    (root / "a.txt").write_text("feature\n", encoding="utf-8")
    git(root, "commit", "-qam", "unapplied feature")
    git(root, "checkout", "-q", "main")
    return root


@pytest.fixture
def scratch_repo(scratch_template, tmp_path):
    return copy_of(scratch_template, tmp_path)


@pytest.mark.parametrize("command,kind", [
    ("git branch -D %s" % SCRATCH_HEAD, "allow"),
    ("git branch -q -D %s" % SCRATCH_HEAD, "allow"),
    ("git branch -q -D %s" % SCRATCH_WORK, "allow"),
    ("git branch -D %s" % FEATURE, "deny"),
    ("git branch -q -D %s" % FEATURE, "deny"),
    ("git branch -q -D %s %s" % (SCRATCH_HEAD, FEATURE), "deny"),
    ("git branch -q -D scratch/missing", "deny"),
    ("git branch -r -D %s" % SCRATCH_WORK, "deny"),
])
def test_scratch_branch_delete(scratch_repo, command, kind):
    assert run(scratch_repo, command).kind == kind


def test_owner_answer_does_not_unlock_an_unapplied_branch(scratch_repo):
    got = run(scratch_repo, "git branch -q -D %s" % FEATURE, [answer_row("Force-delete branch %s?" % FEATURE)])
    assert got.denied and "refs/preserve/" in got.reason


def test_preserve_ref_lets_the_same_quiet_delete_pass(scratch_repo):
    git(scratch_repo, "update-ref", "refs/preserve/kept",
        git(scratch_repo, "rev-parse", "refs/heads/" + FEATURE).strip())
    assert run(scratch_repo, "git branch -q -D %s" % FEATURE).kind == "allow"


# --- MaddManager main's owner_discard rows (a file deleted from the worktree) ------------------------------------
def test_restore_of_a_file_deleted_from_the_worktree_allows(dirty):
    (dirty / A).unlink()
    assert run(dirty, "git restore -- %s" % A).kind == "allow"
    assert run(dirty, "git restore --source=HEAD -- %s" % A).kind == "allow"


def test_restore_of_a_deleted_file_beside_unsaved_bytes_denies(dirty):
    (dirty / A).unlink()
    assert run(dirty, "git restore -- %s %s" % (A, B)).denied


def test_checkout_from_a_ref_of_a_deleted_file_with_a_staged_edit_denies(dirty):
    git(dirty, "add", A)
    (dirty / A).unlink()
    assert run(dirty, "git checkout HEAD -- %s" % A).denied
