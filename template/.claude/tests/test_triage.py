#!/usr/bin/env python3
"""Proof for tools/triage.py: the tag-registry lookup, the Triage: trailer and the raise comparison.

Planted registries prove each behavior; the live case proves the real `adaptation.json` registry
validates and every runtime tag names its probe.

    python3 .claude/tests/test_triage.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(CLAUDE_DIR, "tools"))
sys.stdout.reconfigure(encoding="utf-8")
import triage  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail)[:300] if detail else ""))


def tag(id_, paths, **extra):
    entry = {"id": id_, "paths": paths, "sets": extra.pop("sets", {"reach": "shared"}),
             "source": "test:1"}
    entry.update(extra)
    return entry


def claude_dir_with(root, tags):
    skill = os.path.join(root, ".claude", "skills", "project_subsystems")
    os.makedirs(skill, exist_ok=True)
    with open(os.path.join(skill, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"sensitivity_tags": tags}, fh)
    return os.path.join(root, ".claude")


def tags_of(entries):
    with tempfile.TemporaryDirectory() as root:
        return triage.load_tags(claude_dir_with(root, entries))


def lookup(entries, changes):
    return triage.derive(changes, tags_of(entries))


def ch(path, status="M", hunk="", file_text=None):
    return triage.Change(path, status, hunk, file_text)


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def test_rigor_recorded_level(tmp_path, monkeypatch):
    directory = tmp_path / "records"
    directory.mkdir()
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(directory))
    (directory / "recorded.json").write_text(json.dumps({"task_id": "recorded", "rigor": "thorough"}), encoding="utf-8")
    assert triage.rigor("recorded", tmp_path) == "thorough"


def test_rigor_record_and_defaults(tmp_path, monkeypatch):
    from pathlib import Path
    import task_record
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(tmp_path / "records"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "rigor-session")
    task_record.open_record("rigor", "rigor-session", "Rigor")
    task_record.set_fields("rigor", files=["tools/helper.py"])
    assert triage.rigor("rigor", tmp_path) == "light"
    for level in triage.RIGORS:
        task_record.set_fields("rigor", rigor=level)
        assert triage.rigor("rigor", tmp_path) == level
        assert triage.rigor(root=tmp_path) == level
        assert "rigor: " + level in task_record.render_block("rigor")
    task_record.open_record("unset", "other-session", "Unset")
    task_record.set_fields("unset", files=[".claude/tools/a.py"])
    # `rigor` falls back to the session's last named level before the per-path default, so the
    # defaults below are read under the record's own session.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "other-session")
    assert triage.rigor("unset", tmp_path) == "light"
    task_record.set_fields("unset", files=["Game/A.cs"])
    assert triage.rigor("unset", tmp_path) == "normal"
    task_record.set_fields("unset", files=["tools/a.py", "Game/A.cs"])
    assert triage.rigor("unset", tmp_path) == "normal"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "no-task")
    git(tmp_path, "init", "-q")
    (tmp_path / ".git/info/exclude").write_text("records/\n", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/helper.py").write_text("pass\n", encoding="utf-8")
    git(tmp_path, "add", "tools/helper.py")
    assert triage.rigor(root=tmp_path) == "light"
    config = Path(claude_dir_with(str(tmp_path), [])) / "skills/project_subsystems/adaptation.json"
    config.write_text(json.dumps({"rigor_defaults": {
        "side-tools": {"paths": ["tools/**"], "level": "thorough"}}}), encoding="utf-8")
    assert triage.rigor(root=tmp_path) == "thorough"


def own_tags():
    """This project's tag registry, or None when the checkout carries none.

    The tests below quote one project's own tags (scenes, `.tres`, `[Tool]` classes); a template copy ships
    an `adaptation.json` without `sensitivity_tags`, and another consumer's tags name other paths, so those
    tests have nothing to read there."""
    loaded = triage.load_tags(CLAUDE_DIR)
    return loaded if {"tool-script", "global-class-rename"} <= {tag.id for tag in loaded.valid} else None


def test_contract_refactor_does_not_inherit_owner_from_spell_directory():
    tags = own_tags()
    if tags is None:
        return
    change = ch("Environment/Weather/GustEffect.cs",
                hunk="return handle.SegmentCount;\n", file_text="[Tool] public class Effect {}")
    result = triage.derive([change], tags)
    assert "owner" not in result.oracle
    assert "runtime" in result.oracle


def test_changed_tuning_or_explicit_criterion_admits_owner():
    tags = own_tags()
    if tags is None:
        return
    change = ch("Environment/Weather/gust_config.tres", hunk="Speed = 4\nSpeed = 8\n")
    assert "owner" not in triage.derive([change], tags).oracle
    for acceptance in ("tuning", "feel", "visuals", "owner-criterion"):
        result = triage.derive([change], tags, acceptance=acceptance)
        assert "owner" in result.oracle
    import pytest
    with pytest.raises(ValueError, match="acceptance"):
        triage.derive([change], tags, acceptance="contract")


def test_combined_csharp_attributes_admit_tool_and_globalclass_checks():
    tags = own_tags()
    if tags is None:
        return
    for text in ("[GlobalClass, Tool]\npublic partial class A {}",
                 "[Tool, GlobalClass] public partial class A {}",
                 '[Obsolete("Tool, GlobalClass"), Godot.ToolAttribute, GlobalClassAttribute]\npublic partial class A {}'):
        change = ch("Resources/A.cs", status="R", hunk=text, file_text=text)
        result = triage.derive([change], tags)
        hits = {m.tag for m in result.matches if not m.conditional}
        assert {"tool-script", "global-class-rename", "global-class-file-rename"} <= hits


def test_csharp_attribute_matcher_ignores_comments_strings_and_arguments():
    tags = triage.load_tags(CLAUDE_DIR)
    for text in ('// [GlobalClass, Tool]\npublic partial class A {}',
                 'public class A { string note = "[GlobalClass, Tool]"; }',
                 '[Obsolete("Tool"), NotTool]\npublic partial class A {}',
                 'public class A { object Read() => items[Tool]; }'):
        result = triage.derive([ch("Resources/A.cs", status="R", hunk=text, file_text=text)], tags)
        assert not ({"tool-script", "global-class-rename", "global-class-file-rename"}
                    & {m.tag for m in result.matches if not m.conditional})


def test_lookup_requires_subjective_acceptance_judgment():
    if own_tags() is None:
        return
    result = subprocess.run([sys.executable, os.path.join(CLAUDE_DIR, "tools/triage.py"),
                             "lookup", "Environment/Weather/gust_config.tres",
                             "--acceptance", "tuning"], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert "oracle=owner" in result.stdout


def globs():
    entries = [tag("any-scene", ["**/*.tscn"]), tag("top-only", ["*.cfg"], sets={"oracle": "gate"}),
               tag("gitlink", ["Jmodot"], sets={"reach": "external"}), tag("deep", ["hooks/**"],
                                                                       sets={"oracle": "new-test"})]
    res = lookup(entries, [ch("a.tscn"), ch("x/y/a.tscn"), ch("x/a.cfg"), ch("a.cfg"), ch("Jmodot"),
                           ch("Jmodot/Core/x.cs"), ch("hooks/a/b.py")])
    hit = {(m.tag, m.path) for m in res.matches}
    check("`**/*.tscn` matches a root file and a nested one", {("any-scene", "a.tscn"), ("any-scene", "x/y/a.tscn")} <= hit, hit)
    check("`*.cfg` does not cross `/`", ("top-only", "x/a.cfg") not in hit and ("top-only", "a.cfg") in hit, hit)
    check("an exact name matches only that path", ("gitlink", "Jmodot") in hit and ("gitlink", "Jmodot/Core/x.cs") not in hit, hit)
    check("`hooks/**` crosses `/`", ("deep", "hooks/a/b.py") in hit, hit)
    check("invalid is empty for well-formed entries", res.invalid == [], res.invalid)


def conditions():
    wiring = tag("scene-wiring", ["**/*.tscn", "**/*.tres"], hunk=r"^\[(?:node|connection)\b|ExtResource|NodePath",
                 sets={"oracle": "runtime"}, probe="run the scene and read the log")
    value_only = ch("Dungeon/a.tres", hunk='damage = 4\ndamage = 5\n')
    node_edit = ch("Dungeon/a.tscn", hunk='[node name="Hit" type="Area3D" parent="."]\n')
    signal_edit = ch("Dungeon/a.tscn", hunk='[connection signal="body_entered" from="Hit" to="." method="_on"]\n')
    check("a value-only data edit whose hunk misses the wiring regex raises nothing",
          lookup([wiring], [value_only]).oracle == frozenset()
          and [m for m in lookup([wiring], [value_only]).matches if not m.conditional] == [])
    check("a node hunk sets oracle runtime", lookup([wiring], [node_edit]).oracle == frozenset({"runtime"}))
    check("a signal hunk sets oracle runtime", lookup([wiring], [signal_edit]).oracle == frozenset({"runtime"}))
    m = lookup([wiring], [node_edit]).matches
    check("the match carries its probe and unvalidated state",
          m and m[0].probe == "run the scene and read the log" and m[0].probe_validated is None
          and m[0].conditional is False, m)
    planned = lookup([wiring], triage.planned_changes(["Dungeon/a.tscn"]))
    check("a planned path under a conditioned tag is conditional and raises nothing",
          len(planned.matches) == 1 and planned.matches[0].conditional
          and planned.oracle == frozenset() and planned.reach == "local", planned)
    check("a planned path under an unconditioned tag raises",
          lookup([tag("g", ["hooks/**"], sets={"oracle": "new-test"})],
                 triage.planned_changes(["hooks/a.py"])).oracle == frozenset({"new-test"}))
    filed = tag("needs-file", ["**/*.cs"], file=r"\[Tool\]", sets={"oracle": "runtime"}, probe="headless import")
    check("a file condition reads the post-image",
          lookup([filed], [ch("A.cs", file_text="[Tool]\npublic partial class A {}")]).oracle == frozenset({"runtime"})
          and lookup([filed], [ch("A.cs", file_text="public class A {}")]).oracle == frozenset())
    renamed = tag("rename", ["**/*.cs"], status="R", sets={"oracle": "runtime"}, probe="headless import")
    check("a status condition reads the git status letter",
          lookup([renamed], [ch("A.cs", status="R")]).oracle == frozenset({"runtime"})
          and lookup([renamed], [ch("A.cs", status="M")]).oracle == frozenset())
    gitlink = tag("jmodot-pointer", ["Jmodot"], sets={"reach": "external"})
    check("the gitlink path sets reach external",
          lookup([gitlink], [ch("Jmodot", hunk="-Subproject commit a\n+Subproject commit b\n")]).reach == "external")
    both = [tag("shared-a", ["hooks/**"], sets={"reach": "shared", "oracle": "new-test"}),
            gitlink, tag("run-b", ["hooks/**"], sets={"oracle": "runtime"}, probe="launch")]
    res = lookup(both, [ch("hooks/a.py"), ch("Jmodot")])
    check("the highest reach wins across tags", res.reach == "external", res.reach)
    check("oracle collects every value the matches set", res.oracle == frozenset({"new-test", "runtime"}), res.oracle)


def invalid_entries():
    good = tag("ok", ["a/**"])
    cases = {
        "not an object": ("not-a-dict", "object"),
        "missing id": ({"paths": ["a"], "sets": {"reach": "shared"}, "source": "s"}, "id"),
        "id not kebab-case": (tag("Not_Kebab", ["a"]), "kebab"),
        "duplicate id": (tag("ok", ["b"]), "duplicate"),
        "empty paths": (tag("e1", []), "paths"),
        "missing paths": ({"id": "e2", "sets": {"reach": "shared"}, "source": "s"}, "paths"),
        "bad regex": (tag("e3", ["a"], hunk="[unclosed"), "hunk"),
        "bad file regex": (tag("e4", ["a"], file="(x"), "file"),
        "empty sets": (tag("e5", ["a"], sets={}), "sets"),
        "reach not shared or external": (tag("e6", ["a"], sets={"reach": "local"}), "reach"),
        "oracle unknown": (tag("e7", ["a"], sets={"oracle": "vibes"}), "oracle"),
        "unknown sets key": (tag("e8", ["a"], sets={"size": "parts"}), "sets"),
        "runtime without probe": (tag("e9", ["a"], sets={"oracle": "runtime"}), "probe"),
        "missing source": ({"id": "e10", "paths": ["a"], "sets": {"reach": "shared"}}, "source"),
        "bad status letters": (tag("e11", ["a"], status="Q"), "status"),
        "unknown key": (tag("e12", ["a"], hunks="x"), "hunks"),
        "probe_validated of a bad type": (tag("e13", ["a"], probe_validated=3), "probe_validated"),
    }
    loaded = tags_of([good] + [entry for entry, _ in cases.values()])
    check("the well-formed entry stays valid beside the broken ones", [t.id for t in loaded.valid] == ["ok"],
          [t.id for t in loaded.valid])
    reasons = dict(loaded.invalid)
    check("every broken entry is reported once", len(loaded.invalid) == len(cases), loaded.invalid)
    for offset, (label, (_entry, needle)) in enumerate(cases.items(), start=1):
        reason = reasons.get(offset, "")
        check("invalid entry rejected with its reason: %s" % label, needle in reason, reason)
    res = triage.derive([ch("a/x.py")], loaded)
    check("derive reports the invalid entries and matches only the valid ones",
          len(res.invalid) == len(cases) and {m.tag for m in res.matches} == {"ok"}, res)
    check("a runtime tag with a probe is valid",
          [t.id for t in tags_of([tag("r", ["a"], sets={"oracle": "runtime"}, probe="p")]).valid] == ["r"])


def trailer():
    line = "Triage: reach=local reversibility=clean design=settled grounding=verified oracle=gate+new-test size=one-context"
    fields, error = triage.parse_trailer("fix(x): y\n\nbody\n\n" + line + "\n")
    check("a valid trailer parses to the six fields",
          error is None and fields and fields["reach"] == "local" and fields["reversibility"] == "clean"
          and fields["design"] == "settled" and fields["grounding"] == "verified"
          and fields["size"] == "one-context", (fields, error))
    check("the `+` oracle set parses to its members", fields and frozenset(fields["oracle"]) == frozenset({"gate", "new-test"}), fields)
    check("no Triage: line reads (None, None)", triage.parse_trailer("fix: y\n\nbody\n") == (None, None))
    fields, error = triage.parse_trailer(line.replace(" size=one-context", ""))
    check("a missing key is unreadable and names the key", fields is None and error and "size" in error, (fields, error))
    fields, error = triage.parse_trailer(line.replace("reach=local", "reach=galactic"))
    check("an unknown value is unreadable and names it", fields is None and error and "galactic" in error, (fields, error))
    fields, error = triage.parse_trailer(line + " colour=red")
    check("an unknown key is unreadable and names it", fields is None and error and "colour" in error, (fields, error))
    fields, error = triage.parse_trailer(line.replace("oracle=gate+new-test", "oracle=" + triage._format_oracle(frozenset())))
    check("the empty oracle the tool itself prints parses back to the empty set",
          error is None and fields and fields["oracle"] == frozenset(), (fields, error))
    fields, error = triage.parse_trailer(line.replace("gate+new-test", "none+gate"))
    check("none joined with a value is unreadable", fields is None and error and "none" in error, (fields, error))
    fields, error = triage.parse_trailer(line.replace("gate+new-test", "gate+vibes"))
    check("an unknown oracle member is unreadable", fields is None and error and "vibes" in error, (fields, error))
    later = line.replace("reach=local", "reach=shared").replace("size=one-context", "size=milestones")
    fields, error = triage.parse_trailer(line + "\nnote\n" + later)
    check("the last Triage: line counts", error is None and fields and fields["reach"] == "shared"
          and fields["size"] == "milestones", (fields, error))
    check("the key order is free", triage.parse_trailer(
        "Triage: size=parts oracle=owner grounding=assumed design=open reversibility=none reach=external")[0]
          is not None)
    check("FIELDS names the six fields",
          set(triage.FIELDS) == {"reach", "reversibility", "design", "grounding", "oracle", "size"}, triage.FIELDS)


def raise_lines():
    entries = [tag("jm", ["Jmodot"], sets={"reach": "external"}),
               tag("wire", ["**/*.tscn"], hunk="node", sets={"oracle": "runtime"}, probe="launch"),
               tag("guard", ["hooks/**"], sets={"reach": "shared", "oracle": "new-test"})]
    base = {"reach": "local", "reversibility": "clean", "design": "settled", "grounding": "verified",
            "oracle": frozenset({"gate"}), "size": "one-context"}
    res = lookup(entries, [ch("Jmodot"), ch("a.tscn", hunk="[node]"), ch("hooks/a.py")])
    got = triage.raises(base, res)
    fields = sorted((r.field, r.derived) for r in got)
    check("raises names reach and each oracle value the tags add",
          fields == [("oracle", "new-test"), ("oracle", "runtime"), ("reach", "external")], fields)
    reach = [r for r in got if r.field == "reach"][0]
    check("a raise carries the recorded value and the tag that set it",
          reach.recorded == "local" and any(getattr(t, "tag", None) == "jm" for t in reach.tags), reach)
    at_or_above = dict(base, reach="external", oracle=frozenset({"gate", "new-test", "runtime"}))
    check("recorded at or above derived raises nothing", triage.raises(at_or_above, res) == [])
    check("a recorded oracle given as a list compares the same", triage.raises(dict(at_or_above, oracle=["gate", "new-test", "runtime"]), res) == [])
    check("a conditional match raises nothing",
          triage.raises(base, lookup(entries, triage.planned_changes(["a.tscn"]))) == [])
    check("only reach and oracle are compared", all(r.field in ("reach", "oracle") for r in got))


def rederive_range():
    with tempfile.TemporaryDirectory() as root:
        claude_dir = claude_dir_with(root, [tag("guard", ["hooks/**"], sets={"reach": "shared"})])
        git(root, "init", "-q")
        git(root, "config", "user.email", "t@t")
        git(root, "config", "user.name", "t")
        os.makedirs(os.path.join(root, "hooks"))
        with open(os.path.join(root, "seed.txt"), "w") as fh:
            fh.write("seed\n")
        git(root, "add", "seed.txt")
        git(root, "commit", "-q", "-m", "seed")
        base = git(root, "rev-parse", "HEAD").strip()
        trailer_line = "Triage: reach=%s reversibility=clean design=settled grounding=verified oracle=gate size=one-context"
        shas = []
        for name, reach in (("a.py", "shared"), ("b.py", "local")):
            with open(os.path.join(root, "hooks", name), "w") as fh:
                fh.write("x = 1\n")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "feat: %s\n\n%s" % (name, trailer_line % reach))
            shas.append(git(root, "rev-parse", "HEAD").strip())
        got = triage.rederive(root, "%s..HEAD" % base, triage.load_tags(claude_dir))
        flagged = [item[0] for item in got]
        check("rederive flags only the commit whose trailer sits below its diff", flagged == [shas[1]], (flagged, shas))
        changes = triage.changes_from_commit(root, shas[1])
        check("changes_from_commit reads status, path and added lines",
              [(c.path, c.status) for c in changes] == [("hooks/b.py", "A")] and "x = 1" in (changes[0].hunk or ""), changes)


def index_changes():
    with tempfile.TemporaryDirectory() as root:
        git(root, "init", "-q")
        git(root, "config", "user.email", "t@t")
        git(root, "config", "user.name", "t")
        with open(os.path.join(root, "a.tres"), "w") as fh:
            fh.write("[gd_resource]\nvalue = 1\nkeep = 1\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "seed")
        with open(os.path.join(root, "a.tres"), "w") as fh:
            fh.write("[gd_resource]\nvalue = 2\nkeep = 1\n")
        git(root, "add", "-A")
        got = triage.changes_from_index(root, {"a.tres"}, {}, [])
        check("changes_from_index carries the changed lines and no context lines",
              len(got) == 1 and got[0].path == "a.tres" and got[0].status == "M"
              and "value = 2" in got[0].hunk and "value = 1" in got[0].hunk and "keep" not in got[0].hunk, got)
        check("its post-image is the staged text", len(got) == 1 and "value = 2" in (got[0].file_text or ""), got)


def failed_patch_read():
    with tempfile.TemporaryDirectory() as root:
        git(root, "init", "-q")
        git(root, "config", "user.email", "t@t")
        git(root, "config", "user.name", "t")
        with open(os.path.join(root, "a.tres"), "w") as fh:
            fh.write("value = 1\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "seed")
        with open(os.path.join(root, "a.tres"), "w") as fh:
            fh.write("value = 2\n")
        git(root, "add", "-A")
        real = triage._git_commit.run_git
        triage._git_commit.run_git = lambda args, cwd, env=None: None if "-U0" in args else real(args, cwd, env)
        try:
            got = triage.changes_from_index(root, {"a.tres"}, {}, [])
            try:
                got[0].hunk
                outcome = "no error"
            except OSError:
                outcome = "OSError"
        finally:
            triage._git_commit.run_git = real
        check("a failed patch read raises OSError, not an empty hunk", outcome == "OSError", outcome)


def empty_task_oracle():
    from pathlib import Path
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as root, patch.dict(os.environ):
        claude = claude_dir_with(root, [tag("oracle", ["notes.txt"], sets={"oracle": "new-test"})])
        git(root, "init", "-q")
        git(root, "config", "user.email", "t@t")
        git(root, "config", "user.name", "t")
        Path(root, "notes.txt").write_text("note\n", encoding="utf-8")
        git(root, "add", "notes.txt")
        git(root, "commit", "-qm", "plain")
        sha = git(root, "rev-parse", "HEAD").strip()
        directory = Path(root, "records")
        directory.mkdir()
        os.environ["HARNESS_TASK_RECORD_DIR"] = str(directory)
        fields = dict(reach="local", reversibility="clean", design="settled", grounding="verified",
                      oracle=[], size="parts", kind="mechanical")
        (directory / "task.json").write_text(json.dumps({"sha": sha, "triage": {"fields": fields}}), encoding="utf-8")
        recorded = triage._task_triage(root, sha)
        check("empty task oracle survives trailer serialization", recorded == dict(fields, oracle=frozenset()), recorded)
        got = triage.rederive(root, "HEAD", triage.load_tags(claude))
        check("rederive flags an oracle above an empty task trailer instead of skipping it",
              len(got) == 1 and got[0][0] == sha and [(r.field, r.derived) for r in got[0][1]] == [("oracle", "new-test")], got)
        current = triage.current(Path(root), rev=sha)
        check("empty task oracle preserves recorded kind and size", (current.kind, current.size) == ("mechanical", "parts"), current)
        line = "Triage: reach=local design=settled grounding=verified oracle=none size=parts"
        git(root, "commit", "--amend", "-qm", "recorded\n\n" + line)
        got = triage.rederive(root, "HEAD", triage.load_tags(claude))
        check("rederive flags a recorded oracle=none trailer", len(got) == 1 and got[0][1][0].derived == "new-test", got)


def oracle_grammar():
    for values, expected in (([], "none"), (["runtime", "gate", "gate"], "gate+runtime")):
        formatted = triage._format_oracle(values)
        check("oracle format/parse round-trip: " + expected,
              formatted == expected and triage.parse_oracle(formatted) == sorted(set(values)), formatted)
    check("parse_oracle returns the sorted member set", triage.parse_oracle("new-test+gate") == ["gate", "new-test"])
    try:
        triage.parse_oracle("gate+vibes")
        bad = False
    except ValueError:
        bad = True
    check("parse_oracle raises ValueError on an unknown member", bad)


def live_registry():
    loaded = triage.load_tags(CLAUDE_DIR)
    if not loaded.valid:
        return
    check("every live entry validates", loaded.invalid == [], loaded.invalid)
    runtime = [t for t in loaded.valid if t.sets.get("oracle") == "runtime"]
    check("every live runtime tag names a probe", all(t.probe for t in runtime), [t.id for t in runtime])
    check("every live entry names its source", all(t.source for t in loaded.valid))
    if own_tags() is None:
        return
    check("the live registry holds a runtime tag", bool(runtime), [t.id for t in loaded.valid])

    def live(*changes):
        return triage.derive(list(changes), loaded)

    scene = '[gd_scene format=3]\n[node name="Spawn" type="Marker3D"]\n'
    check("live: a value-only .tres edit raises nothing",
          live(ch("Dungeon/x/a.tres", hunk="damage = 4\ndamage = 5\n")).oracle == frozenset())
    check("live: an added node line in a scene sets oracle runtime",
          "runtime" in live(ch("Dungeon/x/room.tscn", hunk='[node name="Hit" type="Area3D" parent="."]\n')).oracle)
    check("live: a transform edit in a scene holding a Marker3D sets oracle runtime",
          "runtime" in live(ch("Dungeon/x/room.tscn", hunk="transform = Transform3D(1, 0, 0)\n",
                               file_text=scene)).oracle)
    check("live: a transform edit in a scene with no Marker3D or Area3D and no wiring sets no runtime oracle",
          "runtime" not in live(ch("Environment/x/block.tscn", hunk="transform = Transform3D(1, 0, 0)\n",
                                   file_text='[node name="Mesh" type="MeshInstance3D"]\n')).oracle)
    global_class = "using Godot;\n[GlobalClass]\npublic partial class Encounter : Resource {}\n"
    check("live: a class-only rename inside an unrenamed [GlobalClass] .cs sets oracle runtime",
          "runtime" in live(ch("Combat/Encounter.cs", hunk="public partial class Encounter : Resource {}\n"
                                                     "public partial class LevelDefinition : Resource {}\n",
                               file_text=global_class)).oracle)
    check("live: a method-body edit in a [GlobalClass] .cs raises no runtime oracle",
          "runtime" not in live(ch("Combat/Encounter.cs", hunk="    return 1;\n    return 2;\n",
                                   file_text=global_class)).oracle)
    check("live: a stripped value-type export line sets reach shared",
          live(ch("Combat/x/a.tres", hunk="crit_chance = null\n")).reach == "shared")
    check("live: the Jmodot gitlink and .gitmodules set reach external",
          live(ch("Jmodot")).reach == "external" and live(ch(".gitmodules")).reach == "external")
    check("live: a hook edit sets reach shared and oracle new-test",
          live(ch(".claude/hooks/x.py")).reach == "shared" and "new-test" in live(ch(".claude/hooks/x.py")).oracle)
    for data in (".claude/hooks/tool_resource_classes.txt", "Tests/regression_baseline.json"):
        got = live(ch(data))
        check("live: gate-written %s is not harness guard logic" % data,
              got.reach != "shared" and "new-test" not in got.oracle, (got.reach, sorted(got.oracle)))
    for path, reach, oracle in ((".claude/tools/x.py", "shared", {"new-test"}),
                                (".claude/tests/test_x.py", "shared", {"new-test"}),
                                (".claude/scripts/benchmark_campaign/x.py", "shared", {"new-test"}),
                                (".claude/commands/x.md", "shared", set()),
                                (".claude/skills/orchestration/SKILL.md", "shared", set()),
                                (".claude/hooks/x.py", "shared", {"new-test"}),
                                (".claude/scripts/validate_x.py", "shared", {"new-test"}),
                                ("Wizard/x.cs", "local", set()),
                                (".claude/auto-memory/x.md", "local", set())):
        got = live(ch(path))
        check("live: %s derives reach %s oracle %s" % (path, reach, sorted(oracle) or "unset"),
              got.reach == reach and set(got.oracle) == oracle, (got.reach, sorted(got.oracle)))
    check("live: a [Tool] class edit sets oracle runtime; the same file without [Tool] does not",
          "runtime" in live(ch("Tools/x/a.cs", file_text="[Tool]\npublic partial class A {}")).oracle
          and "runtime" not in live(ch("Tools/x/a.cs", file_text="public partial class A {}")).oracle)


def test_current_kind_and_standing(tmp_path, monkeypatch):
    from pathlib import Path
    import load_census
    if not hasattr(load_census, "_import_rows"):  # this checkout's census lists no `@` imports, so none is standing
        return
    root = tmp_path
    claude_dir_with(str(root), [])
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    (root / ".claude/CLAUDE.md").write_text("@CLAUDE.core.md\n", encoding="utf-8")
    core = root / ".claude/CLAUDE.core.md"
    core.write_text("Keep the old rule.\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "seed")
    core.write_text("Use the new rule.\n", encoding="utf-8")
    git(root, "add", ".")
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(root / "records"))
    fields = triage.current(root, staged=True)
    assert fields.reach == "standing"
    assert fields.kind == "decision"
    assert fields.size == "milestones"
    line = "Triage: reach=local design=settled grounding=verified oracle=gate size=one-context"
    parsed, error = triage.parse_trailer(line + " kind=mechanical")
    assert error is None and parsed["kind"] == "mechanical"
    assert triage.parse_trailer(line.replace("reach=local", "reach=standing"))[0] is None
    assert triage.parse_trailer(line + " kind=unknown")[0] is None
    git(root, "commit", "-qm", "mechanical\n\n" + line + " kind=mechanical")
    fields = triage.current(root, rev="HEAD")
    assert (fields.kind, fields.reach) == ("mechanical", "standing")
    assert triage.sentences(root, rev="HEAD") == [{
        "path": ".claude/CLAUDE.core.md", "added": ["Use the new rule."], "removed": ["Keep the old rule."]}]
    assert "kind=mechanical" in triage.trailer_text(parsed)


def test_current_record_precedence(tmp_path, monkeypatch):
    import task_record
    claude_dir_with(str(tmp_path), [])
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "notes.txt").write_text("old\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "seed")
    (tmp_path / "notes.txt").write_text("new\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    monkeypatch.setenv("HARNESS_TASK_RECORD_DIR", str(tmp_path / "records"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-test")
    task_record.open_record("task", "current-test", "Task")
    fields = dict(reach="external", reversibility="clean", design="settled", grounding="verified",
                  oracle=["gate"], size="parts", kind="mechanical")
    task_record.record_triage("task", fields, ["notes.txt"])
    assert triage.current(tmp_path, staged=True).size == "parts"
    assert triage.current(tmp_path, task_id="task").kind == "mechanical"
    git(tmp_path, "commit", "-qm", "plain")
    sha = git(tmp_path, "rev-parse", "HEAD").strip()
    task_record.add_item("task", "artifact", sha)
    assert triage.current(tmp_path, rev=sha).kind == "mechanical"
    line = "Triage: reach=local design=settled grounding=verified oracle=gate size=one-context"
    git(tmp_path, "commit", "--allow-empty", "-qm", "trailer\n\n" + line)
    assert triage.current(tmp_path, rev="HEAD").kind == "decision"
    assert triage.current(tmp_path, rev="HEAD").reach == "local"


def main():
    for case in (globs, conditions, invalid_entries, trailer, raise_lines, rederive_range, index_changes,
                 failed_patch_read, oracle_grammar, empty_task_oracle, live_registry,
                 test_contract_refactor_does_not_inherit_owner_from_spell_directory,
                 test_changed_tuning_or_explicit_criterion_admits_owner,
                 test_lookup_requires_subjective_acceptance_judgment,
                 test_combined_csharp_attributes_admit_tool_and_globalclass_checks,
                 test_csharp_attribute_matcher_ignores_comments_strings_and_arguments):
        try:
            case()
        except Exception as exc:  # a crash is a failure of that group, not a silent pass
            check("%s ran without an exception" % case.__name__, False, repr(exc))
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch
    for case in (test_rigor_recorded_level, test_rigor_record_and_defaults,
                 test_current_kind_and_standing, test_current_record_precedence):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ):
            try:
                case(Path(root), SimpleNamespace(setenv=lambda k, v: os.environ.__setitem__(k, v)))
                check(case.__name__, True)
            except Exception as exc:
                check(case.__name__, False, repr(exc))
    print("\n%d failure(s)" % len(FAILURES))
    for failure in FAILURES:
        print("  FAIL " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
