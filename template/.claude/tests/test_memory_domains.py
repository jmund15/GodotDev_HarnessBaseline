#!/usr/bin/env python3
"""Proof of hooks/_memory_domains.py: the row loader against a real `adaptation.json` and the path-glob matcher.

    python3 -m pytest -q .claude/tests/test_memory_domains.py
"""
import json
import os
import sys

import pytest

CLAUDE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import _memory_domains as domains  # noqa: E402


def claude_dir(tmp_path, seed):
    target = tmp_path / "skills" / "project_subsystems"
    target.mkdir(parents=True)
    if seed is not None:
        (target / "adaptation.json").write_text(seed if isinstance(seed, str) else json.dumps(seed))
    return str(tmp_path)


def names(rows):
    return [row["name"] for row in rows]


def test_absent_seed_holds_no_rows(tmp_path):
    assert domains.load(claude_dir(tmp_path, None)) == []


def test_a_row_of_paths_alone_loads_with_every_list_field(tmp_path):
    row = {"name": "Widgets", "paths": ["Widgets/**"], "skills": ["w"]}
    got = domains.load(claude_dir(tmp_path, {"memory_domains": [row]}))[-1]
    assert got["name"] == "Widgets" and got["paths"] == ["Widgets/**"] and got["rules"] == [] and got["memory_keywords"] == []


def test_a_row_carrying_the_retired_triggers_key_loads_without_it(tmp_path):
    got = domains.load(claude_dir(tmp_path, {"memory_domains": [{"name": "Old", "triggers": ["x"], "paths": ["a/**"]}]}))
    assert names(got) == ["Old"] and "triggers" not in got[0]


def test_malformed_rows_are_skipped_with_one_line_each_and_siblings_load(tmp_path, capsys):
    seed = {"memory_domains": [{"memory_keywords": ["x"]}, {"name": "Bad", "skills": "no"},
                               {"name": "Bad paths", "paths": "Widgets/**"}, {"name": "Good"}]}
    assert names(domains.load(claude_dir(tmp_path, seed))) == ["Good"]
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 3 and "'Bad'" in err and "'Bad paths'" in err


def test_wrong_typed_key_loads_nothing_and_names_the_key(tmp_path, capsys):
    assert domains.load(claude_dir(tmp_path, {"memory_domains": {"name": "x"}})) == []
    assert "memory_domains" in capsys.readouterr().err


def test_a_later_row_of_the_same_name_replaces_the_earlier(tmp_path):
    seed = {"memory_domains": [{"name": "A", "skills": ["one"]}, {"name": "A", "skills": ["two"]}]}
    got = domains.load(claude_dir(tmp_path, seed))
    assert len(got) == 1 and got[0]["skills"] == ["two"]


@pytest.mark.parametrize("glob,path,expected", [
    ("**/States/**", "Game/States/Idle.cs", True), ("**/States/**", "States/Idle.cs", True),
    ("**/States/**", "Game/Statesman/Idle.cs", False), ("**/*.tscn", "a/b/c.tscn", True), ("**/*.tscn", "c.tscn", True),
    ("*.tscn", "a/c.tscn", True), ("*.tscn", "c.tscn.bak", False), ("Jmodot/**", "C:/repo/Jmodot/Core/a.cs", True),
    ("Jmodot/**", r"C:\repo\Jmodot\Core\a.cs", True), ("Jmodot/**", "C:/repo/NotJmodot/a.cs", False),
    ("**/*Gizmo.cs", "x/RedGizmo.cs", True), ("**/*Gizmo.cs", "x/RedGizmos.cs", False),
    ("project.godot", "C:/repo/project.godot", True), ("art/?.png", "art/a.png", True), ("art/?.png", "art/ab.png", False),
    ("Assets/**", "game/art/", False), ("Assets/**", "game/assets/", False), ("**/Tests/**", "tests/test_x.py", False), ("**/Tests/**", "Game/Tests/a.cs", True), ("**/assets/**", "game/assets/", True),
])
def test_glob_ownership(glob, path, expected):
    assert domains.owns(glob, path) is expected


def test_by_paths_returns_each_row_with_the_paths_it_owns_in_table_order():
    rows = [{"name": "A", "paths": ["**/a/**"]}, {"name": "None", "paths": []}, {"name": "B", "paths": ["**/*.cs", "b/**"]}]
    got = domains.by_paths(["x/a/1.cs", "b/2.txt", "docs/n.md"], rows)
    assert [(row["name"], paths) for row, paths in got] == [("A", ["x/a/1.cs"]), ("B", ["x/a/1.cs", "b/2.txt"])]
