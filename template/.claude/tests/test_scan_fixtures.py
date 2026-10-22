#!/usr/bin/env python3
"""Proof for .claude/tests/_scan_fixtures.py: the assembled tokens reach the scanners.

    python3 .claude/tests/test_scan_fixtures.py

Prints one `ok` or `FAIL` line per case and exits 1 when any case fails.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import _scan_fixtures as fx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "tools"))
import baseline_identity as identity  # noqa: E402
import user_scope  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    env = os.environ.copy()
    env["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _remove(path: Path) -> None:
    for current, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            try:
                os.chmod(Path(current) / name, stat.S_IRWXU)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def _fixture():
    path = Path(tempfile.mkdtemp(prefix="scan_fixtures_"))
    try:
        yield path
    finally:
        _remove(path)


def _digests_doc(profile: dict) -> dict:
    counts = {kind: len(profile.get(kind, ())) for kind in identity.TOKEN_KINDS}
    return {"version": 1, "sources": [{"label": "fixture", "counts": counts,
                                       "kinds": identity.digest_kinds(profile)}]}


def _profile() -> dict:
    return identity.build_profile(home_users=[fx.HOME_USER])


def test_planted_identity_tokens_are_flagged() -> None:
    with _fixture() as path:
        repo = path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "notes.md").write_text(
            "\n".join([
                f"path C:/Users/{fx.HOME_USER}/notes",
                f"owner {fx.HOME_USER} here",
                "plain notes here",
            ]) + "\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        digests = path / "identity_digests.json"
        digests.write_text(json.dumps(_digests_doc(_profile())), encoding="utf-8", newline="\n")
        hit_lines = {hit.line for hit in identity.scan_tree(repo, digests)}
    assert hit_lines == {1, 2}, hit_lines


def test_shipped_tree_is_clean_of_the_fixture_tokens() -> None:
    with _fixture() as path:
        digests = path / "identity_digests.json"
        digests.write_text(json.dumps(_digests_doc(_profile())), encoding="utf-8", newline="\n")
        hits = identity.scan_tree(REPO, digests)
    assert not hits, [hit.to_dict() for hit in hits[:5]]


def test_secret_fixtures_match_their_lint_shapes() -> None:
    secrets = {
        "SK_KEY": fx.SK_KEY,
        "AWS_KEY": fx.AWS_KEY,
        "GITHUB_TOKEN": fx.GITHUB_TOKEN,
        "SLACK_TOKEN": fx.SLACK_TOKEN,
        "PEM_HEADER": fx.PEM_HEADER,
    }
    missed = [name for name, value in secrets.items()
              if not any(pattern.search(value) for _, pattern in user_scope.SECRET_SHAPES)]
    assert not missed, missed


CASES = [
    test_planted_identity_tokens_are_flagged,
    test_shipped_tree_is_clean_of_the_fixture_tokens,
    test_secret_fixtures_match_their_lint_shapes,
]


def main() -> int:
    failed = 0
    for case in CASES:
        try:
            case()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {case.__name__}: {exc}")
        else:
            print(f"ok {case.__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
