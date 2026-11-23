#!/usr/bin/env python3
"""Proof for tools/vault_read.py: a vault read types every file it could not read, so a sweep never reads clean by omission.

    python -m pytest .claude/tests/test_vault_read.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import vault_read  # noqa: E402

MINE = Path("DevProjects/Mine")


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / MINE
    (folder / "sub").mkdir(parents=True)
    (folder / "a.md").write_text("alpha note\n", encoding="utf-8")
    (folder / "sub" / "b.md").write_text("beta note\n", encoding="utf-8")
    (folder / "empty.md").write_text("", encoding="utf-8")
    (folder / "folder.md").mkdir()
    (folder / "other.txt").write_text("not markdown\n", encoding="utf-8")
    return tmp_path


def test_a_missing_root_is_typed_absent_with_nothing_read(tmp_path):
    texts, counts = vault_read.read_vault(tmp_path, Path("DevProjects/Nope"))
    assert counts["absent"] is True and counts["read"] == 0 and texts == [] and not vault_read.verified(counts)


def test_every_markdown_file_is_read_and_a_fully_read_root_is_verified(docs):
    """A folder named like a markdown file is skipped and other suffixes are ignored."""
    texts, counts = vault_read.read_vault(docs, MINE)
    assert sorted(Path(p).name for p, _ in texts) == ["a.md", "b.md", "empty.md"] and counts["read"] == 3
    assert vault_read.verified(counts)


def test_a_dehydrated_placeholder_is_typed_unattempted_listed_and_never_read(docs, monkeypatch):
    monkeypatch.setattr(vault_read, "dehydrated", lambda path: Path(path).name == "b.md")
    texts, counts = vault_read.read_vault(docs, MINE)
    assert counts["unattempted"] == 1 and counts["unreadPaths"] == [str(docs / MINE / "sub" / "b.md")]
    assert all(Path(p).name != "b.md" for p, _ in texts) and not vault_read.verified(counts)


def test_the_default_vault_is_the_home_fallback_when_adaptation_sets_no_vault_root(tmp_path):
    assert vault_read.default_vault(tmp_path) == vault_read.FALLBACK_VAULT


def test_the_default_vault_is_adaptations_vault_root_when_set(tmp_path):
    (tmp_path / "skills" / "project_subsystems").mkdir(parents=True)
    (tmp_path / "skills" / "project_subsystems" / "adaptation.json").write_text('{"vault_root": "/v/root"}',
                                                                               encoding="utf-8")
    assert vault_read.default_vault(tmp_path) == Path("/v/root")


def test_the_fallback_vault_root_derives_from_the_home_directory_never_a_literal_user_path():
    assert vault_read.FALLBACK_VAULT == Path.home() / "OneDrive" / "Documents" / "ObsidianVault"
