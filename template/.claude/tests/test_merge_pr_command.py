"""Proof that the shared `/merge_pr` never stashes, switches or checks out in the primary tree: every build, gate and
conflict step runs in a worktree the command creates and retires (the PR's own branch is read through `origin/`)."""
import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "commands" / "merge_pr.md"
PRIMARY_MUTATION = re.compile(r"git\s+(?:-C\s+\S+\s+)?(stash|checkout|pull|switch)\b")


def split(text):
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    return head, body


def test_the_command_names_no_stash_or_checkout_step():
    head, body = split(DOC.read_text(encoding="utf-8"))
    tools = re.search(r"^allowed-tools:\s*(.*)$", head, re.M).group(1)
    assert not re.search(r"git (stash|checkout)", tools)
    # A submodule path under the worktree is the one `-C` target the doc may switch, never a bare primary-tree call.
    bare = [m.group(0) for m in PRIMARY_MUTATION.finditer(body) if "-C" not in m.group(0)]
    assert bare == []


def test_the_command_creates_and_retires_its_worktree():
    _, body = split(DOC.read_text(encoding="utf-8"))
    assert "git worktree add" in body
    assert "git worktree remove" in body
