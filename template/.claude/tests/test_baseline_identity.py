#!/usr/bin/env python3
"""Re-runnable S3 proof for the consumer-side identity scanner.

    python3 .claude/tests/test_baseline_identity.py

Every planted token is assembled at runtime from fragments so no committed line
in this file matches either scanner it exercises.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

IDENTITY = Path(__file__).resolve().parents[1] / "tools" / "baseline_identity.py"


def _load_identity():
    if not IDENTITY.exists():
        print("CANNOT-RUN: %s is missing" % IDENTITY, file=sys.stderr)
        sys.exit(2)
    spec = importlib.util.spec_from_file_location("baseline_identity_under_test", IDENTITY)
    if spec is None or spec.loader is None:
        print("CANNOT-RUN: %s could not be loaded" % IDENTITY, file=sys.stderr)
        sys.exit(2)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        print("CANNOT-RUN: %s failed to import: %s" % (IDENTITY, exc), file=sys.stderr)
        sys.exit(2)
    return module


identity = _load_identity()

# ---------------------------------------------------------------------------
# Fragment-built planted tokens (see module docstring: no literal match here).
# ---------------------------------------------------------------------------

_ABBREV = "P" + "P"  # abbreviation
_NOT_ABBREV = _ABBREV + "T"  # abbreviation plus one letter, must miss
_PROJECT_NAME = "Pushin" + "Potions"  # project name
_SUFFIX_WORD = "Su" + "ite"  # generic suffix word
_CONCATENATION = _PROJECT_NAME + _SUFFIX_WORD  # project name plus suffix word
_HOME_USER = "some" + "one"  # home-path user segment
_SUBSYSTEM_ID = "wiz" + "ardGuild"  # compound subsystem id
_SINGLE_WORD_SUBSYSTEM_ID = "wiz" + "ard"  # single-word subsystem id, not a topology token
_TYPE_NAME = "Spell" + "Crafter"  # compound type name, 5+ characters
_SINGLE_WORD_TYPE_NAME = "Spe" + "llbook"  # single-word type name, not a topology token
_TREE_PRESENT_TYPE_NAME = "Spell" + "Weaver"  # compound type name already present in the fixture tree
_SHORT_TYPE_NAME = "Am" + "mo"  # type name under 5 characters, excluded
_GENERIC_WORD = "gen" + "eric"  # generic word, matches no kind


def _fixture_profile(template_tokens: set | None = None) -> dict:
    return identity.build_profile(
        substitutions={"{{A}}": _PROJECT_NAME, "{{B}}": _SUFFIX_WORD},
        abbreviations=[_ABBREV],
        subsystems=[{"id": _SUBSYSTEM_ID, "paths": ["Wizard/"]}],
        type_names=[_TYPE_NAME, _SHORT_TYPE_NAME],
        content_nouns=[],
        home_users=[_HOME_USER],
        template_tokens=template_tokens,
    )


def _diff_add(path: str, first_line: int, lines: list[str]) -> str:
    hunk = "@@ -0,0 +%d,%d @@\n" % (first_line, len(lines))
    body = "".join("+" + line + "\n" for line in lines)
    return f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n{hunk}{body}"


# ---------------------------------------------------------------------------
# scan_changed — positive cases
# ---------------------------------------------------------------------------

def test_scan_changed_hits_abbreviation() -> None:
    profile = _fixture_profile()
    diff = _diff_add("a.md", 1, [f"the {_ABBREV} token"])
    hits = identity.scan_changed(diff, profile)
    assert any(h.token_kind == "abbreviation" for h in hits), [h.to_dict() for h in hits]


def test_scan_changed_hits_concatenation() -> None:
    profile = _fixture_profile()
    diff = _diff_add("a.md", 1, [f"a {_CONCATENATION} class"])
    hits = identity.scan_changed(diff, profile)
    assert any(h.token_kind == "concatenation" for h in hits), [h.to_dict() for h in hits]


def test_scan_changed_hits_home_forward_slash() -> None:
    profile = _fixture_profile()
    home_shape = "C:/Users/" + _HOME_USER
    diff = _diff_add("a.md", 1, [f"path is {home_shape}/Documents"])
    hits = identity.scan_changed(diff, profile)
    assert any(h.token_kind == "home" for h in hits), [h.to_dict() for h in hits]


def test_scan_changed_hits_home_posix() -> None:
    profile = _fixture_profile()
    home_shape = "/home/" + _HOME_USER
    diff = _diff_add("a.md", 1, [f"path is {home_shape}/config"])
    hits = identity.scan_changed(diff, profile)
    assert any(h.token_kind == "home" for h in hits), [h.to_dict() for h in hits]


def test_scan_changed_hits_subsystem_id() -> None:
    profile = _fixture_profile()
    diff = _diff_add("a.md", 1, [f"the {_SUBSYSTEM_ID} subsystem"])
    hits = identity.scan_changed(diff, profile)
    assert any(h.token_kind == "topology" for h in hits), [h.to_dict() for h in hits]


def test_scan_changed_hits_declared_type_name() -> None:
    profile = _fixture_profile()
    diff = _diff_add("a.md", 1, [f"the {_TYPE_NAME} type"])
    hits = identity.scan_changed(diff, profile)
    assert any(h.token_kind == "topology" for h in hits), [h.to_dict() for h in hits]


def test_hit_id_shape() -> None:
    profile = _fixture_profile()
    diff = _diff_add("dir/a.md", 5, [f"the {_ABBREV} token"])
    hits = [h for h in identity.scan_changed(diff, profile) if h.token_kind == "abbreviation"]
    assert hits, "expected an abbreviation hit"
    hit = hits[0]
    assert hit.id == f"dir/a.md:5:abbreviation:{hit.token_digest}"
    assert len(hit.token_digest) == 12


# ---------------------------------------------------------------------------
# scan_changed — negative cases
# ---------------------------------------------------------------------------

def test_scan_changed_misses_near_miss_abbreviation() -> None:
    profile = _fixture_profile()
    diff = _diff_add("a.md", 1, [f"the {_NOT_ABBREV} token"])
    hits = identity.scan_changed(diff, profile)
    assert not hits, [h.to_dict() for h in hits]


def test_scan_changed_misses_generic_word() -> None:
    profile = _fixture_profile()
    diff = _diff_add("a.md", 1, [f"a {_GENERIC_WORD} sentence"])
    hits = identity.scan_changed(diff, profile)
    assert not hits, [h.to_dict() for h in hits]


def test_scan_changed_misses_short_type_name() -> None:
    profile = _fixture_profile()
    diff = _diff_add("a.md", 1, [f"the {_SHORT_TYPE_NAME} type"])
    hits = identity.scan_changed(diff, profile)
    assert not hits, [h.to_dict() for h in hits]


# ---------------------------------------------------------------------------
# S3 fix2 — topology compound-word rule and pinned-template-tree absence rule
# ---------------------------------------------------------------------------

def test_build_profile_excludes_single_word_subsystem_id() -> None:
    profile = identity.build_profile(
        subsystems=[{"id": _SINGLE_WORD_SUBSYSTEM_ID, "paths": []}],
    )
    assert _SINGLE_WORD_SUBSYSTEM_ID not in profile["topology"], profile["topology"]


def test_build_profile_excludes_single_word_type_name() -> None:
    profile = identity.build_profile(type_names=[_SINGLE_WORD_TYPE_NAME])
    assert _SINGLE_WORD_TYPE_NAME not in profile["topology"], profile["topology"]


def test_build_profile_includes_compound_type_name_absent_from_template_tree() -> None:
    profile = identity.build_profile(type_names=[_TYPE_NAME], template_tokens=set())
    assert _TYPE_NAME in profile["topology"], profile["topology"]


def test_build_profile_excludes_compound_type_name_present_in_template_tree() -> None:
    profile = identity.build_profile(
        type_names=[_TREE_PRESENT_TYPE_NAME], template_tokens={_TREE_PRESENT_TYPE_NAME},
    )
    assert _TREE_PRESENT_TYPE_NAME not in profile["topology"], profile["topology"]


def test_scan_changed_ignores_removed_lines() -> None:
    profile = _fixture_profile()
    diff = (
        f"diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n"
        f"@@ -1,1 +1,1 @@\n-the {_ABBREV} token\n+a plain line\n"
    )
    hits = identity.scan_changed(diff, profile)
    assert not hits, [h.to_dict() for h in hits]


# ---------------------------------------------------------------------------
# scan_tree — fixture git repo + digests file
# ---------------------------------------------------------------------------

def _env(root: Path) -> dict:
    env = os.environ.copy()
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=cwd, env=_env(cwd),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "fixture@example.invalid")
    _git(path, "config", "user.name", "fixture")


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    for current, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            target = Path(current) / name
            try:
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            except OSError:
                pass
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass


def _remove(path: Path) -> None:
    _make_writable(path)
    shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def _fixture():
    path = Path(tempfile.mkdtemp(prefix="baseline_identity_"))
    try:
        yield path
    finally:
        _remove(path)


def _digests_doc(profile: dict) -> dict:
    counts = {kind: len(profile.get(kind, ())) for kind in identity.TOKEN_KINDS}
    kinds = identity.digest_kinds(profile)
    return {"version": 1, "sources": [{"label": "fixturelabel1", "counts": counts, "kinds": kinds}]}


def test_scan_tree_hits_planted_tokens_across_kinds() -> None:
    profile = _fixture_profile()
    with _fixture() as path:
        repo = path / "repo"
        _init_repo(repo)
        (repo / "notes.md").write_text(
            "\n".join([
                f"abbrev {_ABBREV} here",
                f"concat {_CONCATENATION} here",
                f"home C:/Users/{_HOME_USER}/x here",
                f"home /home/{_HOME_USER}/x here",
                f"subsystem {_SUBSYSTEM_ID} here",
                f"type {_TYPE_NAME} here",
                f"miss {_NOT_ABBREV} here",
                f"miss {_GENERIC_WORD} here",
                f"miss {_SHORT_TYPE_NAME} here",
            ]) + "\n",
            encoding="utf-8", newline="\n",
        )
        _git(repo, "add", "-A")
        digests_path = path / "identity_digests.json"
        digests_path.write_text(json.dumps(_digests_doc(profile)), encoding="utf-8", newline="\n")

        hits = identity.scan_tree(repo, digests_path)
        kinds_hit = {h.token_kind for h in hits}
        # "name" joins this set under the case-insensitive word-run matcher (fix 2):
        # the fixture profile's own second substitution value, _SUFFIX_WORD, is
        # itself a "name", so the concatenation line's leading word run also reads
        # as a plain "name" hit -- a true positive the word-run engine adds, not a
        # regression in the old whole-token exact-match behavior.
        assert kinds_hit == {"abbreviation", "concatenation", "home", "name", "topology"}, kinds_hit
        # Lines 7-9 ("miss ...") must contribute no hit at all.
        hit_lines = {h.line for h in hits}
        assert not (hit_lines & {7, 8, 9}), hit_lines


def test_scan_tree_exits_on_missing_digests_file() -> None:
    with _fixture() as path:
        repo = path / "repo"
        _init_repo(repo)
        (repo / "notes.md").write_text("hello\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        try:
            identity.scan_tree(repo, path / "absent.json")
        except identity.IdentityError:
            pass
        else:
            raise AssertionError("scan_tree accepted a missing digests file")


def test_scan_tree_exits_on_unparseable_digests_file() -> None:
    with _fixture() as path:
        repo = path / "repo"
        _init_repo(repo)
        (repo / "notes.md").write_text("hello\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        bad = path / "bad.json"
        bad.write_text("{not json", encoding="utf-8", newline="\n")
        try:
            identity.scan_tree(repo, bad)
        except identity.IdentityError:
            pass
        else:
            raise AssertionError("scan_tree accepted an unparseable digests file")


def test_scan_tree_exits_on_empty_kind_list_with_nonzero_count() -> None:
    with _fixture() as path:
        repo = path / "repo"
        _init_repo(repo)
        (repo / "notes.md").write_text("hello\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        malformed = {
            "version": 1,
            "sources": [{
                "label": "fixturelabel2",
                "counts": {"name": 1, "abbreviation": 0, "concatenation": 0,
                           "home": 0, "topology": 0, "noun": 0},
                "kinds": {"name": [], "abbreviation": [], "concatenation": [],
                          "home": [], "topology": [], "noun": []},
            }],
        }
        bad = path / "malformed.json"
        bad.write_text(json.dumps(malformed), encoding="utf-8", newline="\n")
        try:
            identity.scan_tree(repo, bad)
        except identity.IdentityError:
            pass
        else:
            raise AssertionError("scan_tree accepted a count/kind-list mismatch")


# ---------------------------------------------------------------------------
# `digest --out` CLI — builds a real profile from a fixture consumer repo.
# ---------------------------------------------------------------------------

def _write_fixture_consumer(root: Path, project_name: str, abbrev: str, subsystem_id: str,
                             type_name: str, baseline_repo: str) -> None:
    lock = {
        "baseline_repo": baseline_repo,
        "substitutions": {"{{PROJECT_NAME}}": project_name},
        "identity": {"abbreviations": [abbrev]},
    }
    claude = root / ".claude"
    (claude).mkdir(parents=True, exist_ok=True)
    (claude / "baseline.lock.json").write_text(json.dumps(lock), encoding="utf-8", newline="\n")
    skill_dir = claude / "skills" / "project_subsystems"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "```yaml\n"
        "subsystems:\n"
        f"  - id: {subsystem_id}\n"
        "    paths: [Sub/]\n"
        "    summary: fixture\n"
        "```\n",
        encoding="utf-8", newline="\n",
    )
    (skill_dir / "adaptation.json").write_text(
        json.dumps({"content_nouns": []}), encoding="utf-8", newline="\n",
    )
    sub_dir = root / "Sub"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / "Thing.cs").write_text(f"public class {type_name} {{}}\n", encoding="utf-8", newline="\n")
    jmodot_dir = root / "Sub" / "Jmodot"
    jmodot_dir.mkdir(parents=True, exist_ok=True)
    (jmodot_dir / "Framework.cs").write_text("public class FrameworkExcludedType {}\n",
                                              encoding="utf-8", newline="\n")


def test_digest_command_replaces_only_its_own_source() -> None:
    with _fixture() as path:
        consumer_a = path / "consumer_a"
        _write_fixture_consumer(
            consumer_a, project_name=_PROJECT_NAME, abbrev=_ABBREV,
            subsystem_id=_SUBSYSTEM_ID, type_name=_TYPE_NAME,
            baseline_repo="https://example.invalid/baseline-a.git",
        )
        consumer_b = path / "consumer_b"
        other_name = "Other" + "Project"
        _write_fixture_consumer(
            consumer_b, project_name=other_name, abbrev="X" + "Y",
            subsystem_id="oth" + "er", type_name="Other" + "TypeHere",
            baseline_repo="https://example.invalid/baseline-b.git",
        )
        out = path / "identity_digests.json"
        assert identity.main(["digest", "--out", str(out), "--repo-root", str(consumer_a)]) == 0
        assert identity.main(["digest", "--out", str(out), "--repo-root", str(consumer_b)]) == 0

        doc = json.loads(out.read_text(encoding="utf-8"))
        assert len(doc["sources"]) == 2, doc
        assert not out.read_bytes().count(b"\r")

        # A Jmodot-tree declaration must never reach the digest source.
        profile_a = identity.build_profile_for_repo(consumer_a)
        assert "FrameworkExcludedType" not in profile_a["topology"]
        assert _TYPE_NAME in profile_a["topology"]

        labels_before = {s["label"]: s for s in doc["sources"]}
        label_a = identity._consumer_label(
            json.loads((consumer_a / ".claude" / "baseline.lock.json").read_text())["baseline_repo"],
            consumer_a.name,
        )
        label_b = next(l for l in labels_before if l != label_a)

        # Re-running for consumer_a alone must replace only its own entry.
        assert identity.main(["digest", "--out", str(out), "--repo-root", str(consumer_a)]) == 0
        doc2 = json.loads(out.read_text(encoding="utf-8"))
        labels_after = {s["label"]: s for s in doc2["sources"]}
        assert set(labels_after) == set(labels_before), (labels_before.keys(), labels_after.keys())
        assert labels_after[label_b] == labels_before[label_b], "consumer_b's source must be untouched"


# ---------------------------------------------------------------------------
# fix 3 — the contributor's bare user name is also a "home" token
# ---------------------------------------------------------------------------

def test_scan_changed_hits_bare_contributor_home_token() -> None:
    profile = _fixture_profile()  # home_users=[_HOME_USER], len(_HOME_USER) == 7 >= 5
    diff = _diff_add("a.md", 1, [f"ask {_HOME_USER} for help"])
    hits = identity.scan_changed(diff, profile)
    assert any(h.token_kind == "home" for h in hits), [h.to_dict() for h in hits]


def test_build_profile_excludes_short_home_user_as_bare_token() -> None:
    short_user = "ab" + "cd"  # 4 chars, below the 5-char floor
    profile = identity.build_profile(home_users=[short_user])
    assert short_user not in profile["home"], profile["home"]
    assert f"/home/{short_user}" in profile["home"]  # the path shape still applies


# ---------------------------------------------------------------------------
# fix 4 — `--subsystems <path>`, no hardcoded legacy directory name
# ---------------------------------------------------------------------------

def test_digest_command_honors_explicit_subsystems_path() -> None:
    with _fixture() as path:
        consumer = path / "consumer_custom_subsystems"
        _write_fixture_consumer(
            consumer, project_name=_PROJECT_NAME, abbrev=_ABBREV,
            subsystem_id=_SUBSYSTEM_ID, type_name=_TYPE_NAME,
            baseline_repo="https://example.invalid/baseline-c.git",
        )
        # Move the fixture's SKILL.md out of the default project_subsystems path
        # into a differently-named directory, reachable only via --subsystems.
        default_skill = consumer / ".claude" / "skills" / "project_subsystems" / "SKILL.md"
        default_adapt = consumer / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
        custom_dir = consumer / ".claude" / "skills" / "custom_subsystems_dir"
        custom_dir.mkdir(parents=True, exist_ok=True)
        (custom_dir / "SKILL.md").write_text(default_skill.read_text(encoding="utf-8"),
                                               encoding="utf-8", newline="\n")
        (custom_dir / "adaptation.json").write_text(default_adapt.read_text(encoding="utf-8"),
                                                      encoding="utf-8", newline="\n")
        shutil.rmtree(default_skill.parent)

        out = path / "identity_digests.json"
        rc = identity.main([
            "digest", "--out", str(out), "--repo-root", str(consumer),
            "--subsystems", ".claude/skills/custom_subsystems_dir/SKILL.md",
        ])
        assert rc == 0
        profile = identity.build_profile_for_repo(
            consumer, subsystems_rel_path=".claude/skills/custom_subsystems_dir/SKILL.md",
        )
        assert _SUBSYSTEM_ID in profile["topology"], profile["topology"]


def test_build_profile_for_repo_raises_on_zero_row_subsystems_file() -> None:
    with _fixture() as path:
        consumer = path / "consumer_zero_rows"
        (consumer / ".claude" / "skills" / "project_subsystems").mkdir(parents=True, exist_ok=True)
        (consumer / ".claude" / "skills" / "project_subsystems" / "SKILL.md").write_text(
            "no fenced yaml block here at all\n", encoding="utf-8", newline="\n",
        )
        (consumer / ".claude" / "baseline.lock.json").write_text(
            json.dumps({"substitutions": {}}), encoding="utf-8", newline="\n",
        )
        try:
            identity.build_profile_for_repo(consumer)
        except identity.IdentityError:
            pass
        else:
            raise AssertionError("build_profile_for_repo accepted a zero-row subsystems file")


# ---------------------------------------------------------------------------
# fix 5 — `--abbrev <csv>` on `digest`, only when the lock has no abbreviations
# ---------------------------------------------------------------------------

def test_digest_command_abbrev_flag_used_when_lock_has_none() -> None:
    with _fixture() as path:
        consumer = path / "consumer_no_lock_abbrev"
        _write_fixture_consumer(
            consumer, project_name=_PROJECT_NAME, abbrev="",  # written but overwritten below
            subsystem_id=_SUBSYSTEM_ID, type_name=_TYPE_NAME,
            baseline_repo="https://example.invalid/baseline-d.git",
        )
        lock_path = consumer / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock.pop("identity", None)  # no identity.abbreviations at all
        lock_path.write_text(json.dumps(lock), encoding="utf-8", newline="\n")

        flag_abbrev = "Q" + "Z"  # distinct from _ABBREV, so the source is unambiguous
        profile = identity.build_profile_for_repo(consumer, abbrev_csv=flag_abbrev)
        assert flag_abbrev in profile["abbreviation"], profile["abbreviation"]


def test_digest_command_lock_abbreviations_win_over_abbrev_flag() -> None:
    with _fixture() as path:
        consumer = path / "consumer_lock_abbrev_wins"
        _write_fixture_consumer(
            consumer, project_name=_PROJECT_NAME, abbrev=_ABBREV,
            subsystem_id=_SUBSYSTEM_ID, type_name=_TYPE_NAME,
            baseline_repo="https://example.invalid/baseline-e.git",
        )
        flag_abbrev = "Q" + "Z"
        profile = identity.build_profile_for_repo(consumer, abbrev_csv=flag_abbrev)
        assert _ABBREV in profile["abbreviation"], profile["abbreviation"]
        assert flag_abbrev not in profile["abbreviation"], profile["abbreviation"]


# ---------------------------------------------------------------------------
# parse_subsystems_yaml — small, hand-authored parser
# ---------------------------------------------------------------------------

def test_parse_subsystems_yaml_reads_id_and_paths() -> None:
    text = (
        "```yaml\n"
        "subsystems:\n"
        f"  - id: {_SUBSYSTEM_ID}\n"
        "    paths: [Wizard/, Other/]\n"
        "    summary: fixture text\n"
        "```\n"
    )
    rows = identity.parse_subsystems_yaml(text)
    assert rows == [{"id": _SUBSYSTEM_ID, "paths": ["Wizard/", "Other/"]}], rows


def main() -> int:
    cases = [
        test_scan_changed_hits_abbreviation,
        test_scan_changed_hits_concatenation,
        test_scan_changed_hits_home_forward_slash,
        test_scan_changed_hits_home_posix,
        test_scan_changed_hits_subsystem_id,
        test_scan_changed_hits_declared_type_name,
        test_hit_id_shape,
        test_scan_changed_misses_near_miss_abbreviation,
        test_scan_changed_misses_generic_word,
        test_scan_changed_misses_short_type_name,
        test_build_profile_excludes_single_word_subsystem_id,
        test_build_profile_excludes_single_word_type_name,
        test_build_profile_includes_compound_type_name_absent_from_template_tree,
        test_build_profile_excludes_compound_type_name_present_in_template_tree,
        test_scan_changed_ignores_removed_lines,
        test_scan_tree_hits_planted_tokens_across_kinds,
        test_scan_tree_exits_on_missing_digests_file,
        test_scan_tree_exits_on_unparseable_digests_file,
        test_scan_tree_exits_on_empty_kind_list_with_nonzero_count,
        test_digest_command_replaces_only_its_own_source,
        test_scan_changed_hits_bare_contributor_home_token,
        test_build_profile_excludes_short_home_user_as_bare_token,
        test_digest_command_honors_explicit_subsystems_path,
        test_build_profile_for_repo_raises_on_zero_row_subsystems_file,
        test_digest_command_abbrev_flag_used_when_lock_has_none,
        test_digest_command_lock_abbreviations_win_over_abbrev_flag,
        test_parse_subsystems_yaml_reads_id_and_paths,
    ]
    failures = []
    for case in cases:
        try:
            case()
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
