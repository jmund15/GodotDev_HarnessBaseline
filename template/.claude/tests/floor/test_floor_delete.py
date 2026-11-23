"""Recursive delete and drive format: every case of v1's five `test_dangerous_shell_guard_*` proofs as a row.

Ported from PushinPotions origin/main and MaddManager main (the five files are byte-identical but for
`rm_scope`, whose MaddManager copy holds a subset of these rows). Each row runs through `_floor.decide`; the
v1 "channel" rows that spawned the hook are in-process rows here, and `test_floor_entry.py` holds the one spawned
case per entry point (T6).

Placeholders in a command: {temp} a plain temp dir holding no repository, {project} the planted project root.
"""
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import _floor

from case import bash
from conftest import git, write

# --- test_dangerous_shell_guard_rm_scope.py -------------------------------------------------------------------
RM_SCOPE_BLOCK = [
    'echo "$(rm -rf src)"',
    'echo "x `rm -rf src` y"',
    "echo $'\\'' ; rm -rf src",
    'echo "$(case x in a) rm -rf src;; esac)"',
    "rm -rf /some/dir",
    "rm -r build/",
    "rm -R build/",
    "rm --recursive build/",
    "rm -f -r dir/",
    "rm foo -r",
    "echo hi; rm -rf /important",
    "rm -rf .claude/scratch",
    "rm -rf .claude/scratch/*",
    "rm -rf /tmp",
    "rm -rf /tmp/*",
    "rm -rf $UNSET/x",
    "rm -rf ~",
    "rm -rf .claude/scratch/../../src",
    "rm -rf .claude/scratch/x src",
    "cd /tmp && rm -rf ../Users",
    "mkdir -p src && rm -rf src",
    "rm -rf .claude/scratch/x; echo $(rm -rf src)",
    "rm -rf .claude/scratch/x; echo `rm -rf src`",
    "rm -rf .claude/scratch/x; sudo rm -rf ~",
    "rm -rf .claude/scratch/x; ls | xargs rm -rf",
    "P=.claude/scratch/x; read P; rm -rf $P",
    "P=.claude/scratch/x; P=src; rm -rf $P",
    "P=.claude/scratch/x; for P in src; do rm -rf $P; done",
    'rm "-r" build/',
    "rm '-rf' build/",
]
RM_SCOPE_PASS = [
    "rm -rf .claude/scratch/ladder_audit/fanout && echo ok",
    "P=.claude/scratch/recompact_probe; rm -rf $P/out",
    'R="."; rm -rf "$R/.claude/scratch/commitmsg"',
    'Remove-Item -Recurse -Force ".claude/scratch/old_probe" -Confirm:$false',
    "rm -rf .claude/scratch/fmt_test/*",
    'echo "a $(grep -c \'"rm -rf src\' f) b"',
    "cat > brief.md <<'EOF'\ntry: find . -exec rm -r {} +\nEOF\necho done",
    "rm -f a.txt",
    "rm -f a.txt; grep -r pattern .",
    "rm -f out.json; bash launcher.sh -R record.json -P prog.log",
    "rm -f x && tool --recursive-check",
    "git rm -r --cached path/",
    'grep "rm -rf" notes.md',
    'cd "{temp}" && rm -rf gt && mkdir gt && cd gt && git init -q',
    'cd "{temp}" && rm -rf junk',
    "rm -rf {temp}/red_check",
    "rm -rf .claude/worktrees/uid-header-backfill/.godot",
    "rm -rf .godot && echo reimport",
    "rm -rf src/.godot",
]
# v1 spawned the hook for these; the deny and its citation carry, in process.
RM_SCOPE_DANGEROUS = [
    "del /s /q C:\\temp\\x",
    "rd /s /q C:\\temp\\x",
    "rmdir /s /q C:\\temp\\x",
    "format C:",
    "Remove-Item -Recurse -Force C:\\temp\\x",
    "rm -rf /important",
    "rm --recursive /important",
]
RM_SCOPE_ENV = [
    ('rm -rf "$PE_PLAIN_TEMP/env_check"', False),
    ("rm -rf ${PE_PLAIN_TEMP}/env_check", False),
    ('rm -rf "$PE_UNSET_VAR/env_check"', True),
    ('rm -rf "$PE_PROJECT"', True),
    ('rm -rf "$PE_PLAIN_TEMP/x/*"', False),
    ('P="$PE_PLAIN_TEMP/env_check"; rm -rf "$P"', False),
    ('PE_PLAIN_TEMP=/c/Users; rm -rf "$PE_PLAIN_TEMP"', True),
]


@pytest.fixture
def plain_temp(plant):
    path = Path(tempfile.mkdtemp(prefix="pe_plain_temp_"))
    return str(path).replace("\\", "/")


@pytest.mark.parametrize("command", RM_SCOPE_BLOCK)
def test_rm_scope_must_block(plant, plain_temp, command):
    assert bash(command.replace("{temp}", plain_temp), plant).denied


@pytest.mark.parametrize("command", RM_SCOPE_PASS)
def test_rm_scope_must_pass(plant, plain_temp, command, monkeypatch):
    # The subject is the delete check: a chained `grep -r` is the recursive-grep helper's deny, not a delete.
    monkeypatch.setattr(_floor, "HELPERS", ())
    assert bash(command.replace("{temp}", plain_temp), plant).kind == "allow"


@pytest.mark.parametrize("command", RM_SCOPE_DANGEROUS)
def test_rm_scope_dangerous_denies_with_citation(plant, command):
    if os.name != "nt" and "C:" in command and "format" not in command:
        pytest.skip("Windows drive targets have no Windows filesystem meaning here")
    got = bash(command, plant)
    assert got.denied and "Shell Discipline" in got.reason


def test_rm_scope_deny_names_the_retired_worktree_recipe(plant):
    got = bash("rm -rf /important", plant)
    assert got.denied and "worktrees.md" in got.reason


@pytest.mark.parametrize("command,blocked", [
    ("rm -rf .claude/scratch/{sid}/x", False),
    ("cd .claude/scratch/{sid}/* && rm -rf .claude/logs/x", True),
])
def test_rm_scope_resolved_walker(plant, tmp_path, command, blocked):
    sid = "12345678-1234-1234-1234-123456789abc"
    got = bash(command.format(sid=sid), plant, transcript_path=tmp_path / (sid + ".jsonl"))
    assert got.denied is blocked


@pytest.mark.parametrize("command,blocked", RM_SCOPE_ENV)
def test_rm_scope_env_var_target(plant, plain_temp, monkeypatch, command, blocked):
    monkeypatch.setenv("PE_PLAIN_TEMP", plain_temp)
    monkeypatch.setenv("PE_PROJECT", str(plant))
    monkeypatch.delenv("PE_UNSET_VAR", raising=False)
    assert bash(command, plant).denied is blocked


# --- test_dangerous_shell_guard_absent_target.py --------------------------------------------------------------
ABSENT_ALLOWED = [
    'cd "{project}" && T=.claude/scratch/explore_t1key4/applytest && rm -rf $T && mkdir -p $T',
    "rm -rf .claude/scratch/applytest",
    "rm -rf .claude/scratch/absent/deeper",
    'rm -rf ".claude/scratch/applytest"',
    "rm -rf -f -r .claude/scratch/applytest",
    "cd .claude/scratch && rm -rf applytest",
    "rm -rf {project}/.claude/scratch/applytest",
    "rm -rf .claude/scratch/evidence_tree",
    "rm -rf .claude/scratch/evidence_tree/run.json",
    "rm -r .claude/scratch/applytest",
    "rm --recursive .claude/scratch/applytest",
    "rm -rf .claude/scratch/applytest .claude/scratch/evidence_tree",
    "rm -rf .claude/scratch/applytest && rm -rf .claude/scratch/evidence_tree",
    "rm -rf .claude/scratch/applytest; rm -rf .claude/scratch/evidence_tree",
    "Remove-Item -Recurse -Force .claude/scratch/applytest",
    "ls .claude",
]
ABSENT_BLOCKED = [
    "rm -rf .claude/scratch/absent*",
    "rm -rf .claude/scratch/absent?",
    "rm -rf $UNSET/x",
    "rm -rf ${UNSET}/x",
    "rm -rf .claude/scratch/../applytest",
    "rm -rf .claude/scratch",
    "rm -rf .claude/worktrees/absent",
    "rm -rf Assets/absent",
    "rm -rf .claude/scratch/probe",
    "rm -rf .claude/scratch/0f0e0d0c-1b1a-2918-1716-151413121110",
]


@pytest.fixture
def absent_plant(plant):
    scratch = plant / ".claude" / "scratch"
    write(scratch, "evidence_tree/run.json", "{}\n")
    clone = scratch / "probe"
    clone.mkdir()
    git(clone, "init", "-q")
    git(clone, "commit", "-q", "--allow-empty", "-m", "unpushed")
    write(scratch, "0f0e0d0c-1b1a-2918-1716-151413121110/out/job.log", "peer output\n")
    return plant


@pytest.mark.parametrize("command", ABSENT_ALLOWED)
def test_absent_target_allowed(absent_plant, command):
    got = bash(command.format(project=str(absent_plant).replace("\\", "/")), absent_plant)
    assert got.kind == "allow", got.reason


@pytest.mark.parametrize("command", ABSENT_BLOCKED)
def test_absent_target_blocked(absent_plant, command):
    assert bash(command, absent_plant).denied


def test_absent_target_symlink_escape_blocked(absent_plant, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, absent_plant / ".claude" / "scratch" / "escape", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create a symlink")
    assert bash("rm -rf .claude/scratch/escape/x", absent_plant).denied


@pytest.fixture
def linked(plant, monkeypatch):
    """A main repository with a registered linked worktree and an unregistered stray checkout, each holding a
    scratch file and a scratch entry with a repository."""
    main = plant / "main_repo"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    write(main, "README.md")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "init")
    linked = plant / "linked_wt"
    git(main, "worktree", "add", "-q", "--detach", str(linked))
    stray = plant / "stray_checkout"
    stray.mkdir()
    git(stray, "init", "-q", "-b", "main")
    for tree in (linked, stray):
        write(tree, ".claude/scratch/fan_dbg.json")
        write(tree, ".claude/scratch/dir_with_repo/.keep")
        git(tree / ".claude" / "scratch" / "dir_with_repo", "init", "-q")
    return main, linked, stray


@pytest.mark.parametrize("which,command,blocked", [
    ("linked", "rm -rf .claude/scratch/fan_dbg.json", False),
    ("linked", "rm -rf {linked}/.claude/scratch/fan_dbg.json", False),
    ("linked", "rm -rf .claude/scratch/dir_with_repo", True),
    ("stray", "rm -rf .claude/scratch/fan_dbg.json", True),
    ("linked", "rm -rf .claude/scratch", True),
    ("linked", "rm -rf README.md", True),
])
def test_absent_target_worktree_scratch(linked, monkeypatch, which, command, blocked):
    main, wt, stray = linked
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(main))
    cwd = wt if which == "linked" else stray
    assert bash(command.format(linked=str(wt).replace("\\", "/")), cwd).denied is blocked


@pytest.mark.parametrize("command,blocked", [
    ("rm -rf README.md", True),
    ("rm -rf .claude/.search-index", False),
    ("rm -rf .claude/scratch/fan_dbg.json", False),
])
def test_absent_target_from_the_projects_own_linked_worktree(linked, monkeypatch, command, blocked):
    _main, wt, _stray = linked
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    assert bash(command, wt).denied is blocked


# --- test_dangerous_shell_guard_cache_cleanup.py --------------------------------------------------------------
OTHER_CHECKOUT = "C:/any/other/checkout" if os.name == "nt" else "/any/other/checkout"
OTHER_HOME = "C:/Users/someone" if os.name == "nt" else "/home/someone"
CACHE_PASS = [
    "rm -rf .claude/.search-index",
    "rm -rf .claude/auto-memory/.search-index .claude/hooks/.search-index Tests/.search-index",
    "rm -rf {project}/.claude/.search-index",
    "rm -r .search-index/",
    "rm -rf .claude/hooks/__pycache__",
    "rm -rf .pytest_cache",
    "rm -rf .claude/.cache/baseline-repo",
    "rm -rf .claude/logs/transcript_backups",
    "rm -rf .claude/cache/godot-docs",
    "rm -rf .claude/cache",
    "rm -rf Tests/TestResults",
    "rm -rf .claude/hooks/.cache",
    "Remove-Item -Recurse -Force .claude/hooks/.search-index",
    'rm -rf ".search-index"',
    "rm -rf '.claude/.cache'",
    'rm -rf "Tests/.search-index" "Tests/__pycache__"',
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq",
    "rm -rf .claude/tmp/pe_absent_delete_o_kj1zsx .claude/tmp/pe_absent_outside_fcmdrh_4",
    "rm -rf .claude/tmp/pe_absent_guardtemp_9itfqwag/",
    "rm -rf {project}/.claude/tmp/pe_absent_delete_689z3vmv",
    "rm -rf Tests/.search-index",
]
CACHE_BLOCK = [
    "rm -rf .claude/.cache/../..",
    "rm -rf .search-index/../.claude",
    "rm -rf .claude/logs/../../Assets",
    "rm -rf .claude/logs-old",
    "rm -rf .search-index-backup",
    "rm -rf my.search-index",
    "rm -rf .claude/.cachex",
    "rm -rf .claude/cache-old",
    "rm -rf .claude/cache/../..",
    "rm -rf .claude/hooks",
    "rm -rf */.search-index",
    "rm -rf .search-index*",
    "rm -rf $HOME/.search-index",
    "rm -rf ~/.search-index",
    "rm -rf .search-index Assets",
    "rm -rf .claude/scratch",
    "rm -rf .claude/worktrees/audio_system",
    "rm -rf .claude",
    "rm -rf .",
    "rm -rf .search-index && rm -rf Assets",
    "rm -rf " + OTHER_CHECKOUT + "/.search-index",
    "rm -rf /elsewhere/__pycache__",
    "rm -rf " + OTHER_HOME + "/.claude/.cache",
    'rm -rf "' + OTHER_CHECKOUT + '/.search-index"',
    'rm -rf ".search-index" Assets',
    'rm -rf "$HOME/.search-index"',
    'rm -rf ".search-index" && rm -rf Assets',
    'rm -rf ".search-index;rm -rf Assets"',
    "rm -rf .claude/tmp",
    "rm -rf .claude/tmp/procgen_video_script_draft.md",
    "rm -rf .claude/tmp/DestructibleTerrainUsingTilesets_YouTubeTranscript.txt",
    "rm -rf .claude/tmp/notes_dir",
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq*",
    "rm -rf .claude/tmp/pe_absent_delete",
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq0",
    "rm -rf .claude/tmp/xpe_absent_delete_dxf68feq",
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq/../procgen_video_script_draft.md",
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq .claude/tmp/dump_stack.txt",
    "rm -rf " + OTHER_CHECKOUT + "/.claude/tmp/pe_absent_delete_dxf68feq",
]


@pytest.mark.parametrize("command", CACHE_PASS)
def test_cache_must_pass(plant, command):
    assert bash(command.format(project=str(plant).replace("\\", "/")), plant).kind == "allow"


@pytest.mark.parametrize("command", CACHE_BLOCK)
def test_cache_must_block(plant, command):
    assert bash(command, plant).denied


@pytest.mark.parametrize("command", ["rm -rf ~/peer", "rm -rf $HOME/peer", "rm -rf %USERPROFILE%/peer",
                                     "rm -rf peer*/x"])
def test_cache_expansion_token_under_a_temp_cwd(plant, command):
    work = Path(tempfile.gettempdir()) / "work"
    work.mkdir()
    assert bash(command, work).denied


@pytest.mark.parametrize("command,blocked", [
    ("rm -rf .claude/tmp/pe_absent_repo_abcdefgh", True),
    ("rm -rf .claude/tmp/pe_absent_repo_abcdefgh/repo", True),
    ("rm -rf .claude/tmp/pe_absent_empty_abcdefgh", False),
    ("rm -rf .claude/tmp/pe_absent_empty_abcdefgh/repo", False),
    ("rm -rf .claude/tmp/pe_absent_files_abcdefgh", False),
    ("rm -rf .claude/tmp/pe_absent_files_abcdefgh/sub", False),
])
def test_cache_planted_proof_folder(plant, command, blocked):
    tmp = plant / ".claude" / "tmp"
    write(tmp, "pe_absent_files_abcdefgh/sub/fixture.txt", "a proof's own fixture\n")
    repo = tmp / "pe_absent_repo_abcdefgh" / "repo"
    write(repo, "work.txt", "unpushed work\n")
    git(repo, "init", "-q")
    git(repo, "add", "work.txt")
    git(repo, "commit", "-q", "-m", "only here")
    empty = tmp / "pe_absent_empty_abcdefgh" / "repo"
    empty.mkdir(parents=True)
    git(empty, "init", "-q")
    git(empty, "commit", "-q", "--allow-empty", "-m", "unpushed")
    assert bash(command, plant).denied is blocked


SID = "b48600e4-2ced-4055-895c-18867ef93b99"
OTHER = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("command,with_transcript,blocked", [
    ("rm -rf .claude/scratch/session_end/%s/p1/inputs" % SID, True, False),
    ("rm -rf .claude/scratch/%s" % SID, True, False),
    ("rm -rf .claude/scratch/session_end/%s/p1" % OTHER, True, True),
    ("rm -rf .claude/scratch/session_end/%s/p1" % SID, False, True),
    ("rm -rf .claude/scratch/session_end/%s/../.." % SID, True, True),
    ("rm -rf .claude/scratch/x%s" % SID, True, True),
    ("rm -rf .claude/%s" % SID, True, True),
    ("rm -rf .claude/scratch/%s Assets" % SID, True, True),
])
def test_cache_own_session_scratch(plant, tmp_path, command, with_transcript, blocked):
    transcript = tmp_path / (SID + ".jsonl") if with_transcript else None
    for target in command.split()[2:]:
        if ".." not in target:
            write(plant, target + "/out.txt")  # a present target: an absent one is rewritten to `true`
    assert bash(command, plant, transcript_path=transcript).denied is blocked


def test_cache_target_outside_scratch_asks_git_no_toplevel(plant):
    real_run, calls = subprocess.run, []

    def watch(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "--show-toplevel" in cmd:
            calls.append(cmd)
        return real_run(cmd, *args, **kwargs)
    with mock.patch.object(subprocess, "run", watch):
        for command in ("rm -rf .claude/.cache/../..", "rm -rf Assets", "rm -rf .claude/worktrees/audio_system"):
            bash(command, plant)
    assert not calls


def test_cache_three_scratch_targets_share_one_worktree_list(plant):
    for name in ("a", "b", "c"):
        write(plant, ".claude/scratch/%s/output.txt" % name, "a study's output")
    real_run, calls = subprocess.run, []

    def watch(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "worktree" in cmd and "list" in cmd:
            calls.append(cmd)
        return real_run(cmd, *args, **kwargs)
    with mock.patch.object(subprocess, "run", watch):
        bash("rm -rf .claude/scratch/a .claude/scratch/b .claude/scratch/c", plant)
    assert len(calls) == 1


# --- test_dangerous_shell_guard_owned_temp_cleanup.py ---------------------------------------------------------
F8303_COMMAND = (
    'cd /c/Users/dev/Projects/Game && '
    'T=$(ls /c/Users/dev/.claude/projects/C--Users-dev-Projects-Game/9e798553*.jsonl | head -1); '
    'W=$(cygpath -m "$T"); '
    'TMPD=$(mktemp -d); '
    'printf \'{"source":"compact","session_id":"9e798553-probe","transcript_path":"%s"}\' "$W" '
    '| CLAUDE_PROJECT_DIR="$TMPD" python3 .claude/hooks/compact_directive_anchor.py '
    '| grep -E \'^\\[owner-directives\\]|^--- U\' '
    '| cut -c1-300; '
    'rm -rf "$TMPD"'
)
OWNED_BLOCK = [
    'T=$(ls foo.jsonl | head -1); rm -rf "$T"',
    'TMPD=$(mktemp -d); TMPD="/other/path"; rm -rf "$TMPD"',
    'TMPD=$(mktemp -d); rm -rf "$TMPD" /extra/path',
    'TMPD=$(mktemp -d); rm -rf "$TMPD/.."',
    'T=$(mktemp -d); echo $(rm -rf /); rm -rf "$T"',
    'T=$(mktemp -d); echo `rm -rf /`; rm -rf "$T"',
    'T=$(mktemp -d); sudo rm -rf /; rm -rf "$T"',
    'T=$(mktemp -d); rm -rf "$T" & rm -rf /',
    'T=$(mktemp -d); read T < paths.txt; rm -rf "$T"',
    'T=$(mktemp -d); for T in /; do rm -rf "$T"; done',
    'NAME=$(mktemp -d)/../../important && rm -rf "$NAME"',
    'NAME=$(mktemp -d)/.. ; rm -rf "$NAME"',
    'NAME=$(mktemp -d)/* && rm -rf "$NAME"',
    'TMPD=$(mktemp -d); rm -rf "$TMPD" && rm -rf /important',
    'TMPD=$(mktemp -d); rm -rf "$TMPD"; format C:',
    'TMPD=$(mktemp -d -t foo/bar); rm -rf "$TMPD"',
]
OWNED_PASS = [
    F8303_COMMAND,
    'A=$(mktemp -d); B=$(mktemp -d); rm -rf "$A"; rm -rf "$B"',
    'TMPD=$(mktemp -d); rm -rf ${TMPD}',
    'TMPD=$(mktemp -d -t fooXXXXXX); rm -rf "$TMPD"',
    'd=$(mktemp -d) && cd "$d" && git init -q && cd / && rm -rf "$d"',
    'TMP=$(mktemp -d); awk "/x/" f > "$TMP/o"; rm -r' 'f "$TMP"',
    'T=$(mktemp -d)\necho hi > "$T/a"\nrm -rf "$T"',
    'A=$(mktemp -d); B=$(mktemp -d); rm -rf "$A" "$B"',
]


@pytest.mark.parametrize("command", OWNED_BLOCK)
def test_owned_temp_must_block(plant, command):
    assert bash(command, plant).denied


@pytest.mark.parametrize("command", OWNED_PASS)
def test_owned_temp_must_pass(plant, command):
    assert bash(command, plant).kind == "allow"


# --- test_dangerous_shell_guard_session_dir.py ----------------------------------------------------------------
def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _msys(path):
    p = str(path).replace("\\", "/")
    return "/%s%s" % (p[0].lower(), p[2:]) if len(p) > 1 and p[1] == ":" else p


def _transcript(base, name, command, start, end, is_error=False, sidechain=False):
    use = {"type": "assistant", "timestamp": _iso(start), "cwd": str(base), "isSidechain": sidechain,
           "message": {"role": "assistant", "content": [
               {"type": "tool_use", "id": "toolu_plant", "name": "Bash",
                "input": {"command": command, "description": "Download and extract"}}]}}
    result = {"type": "user", "timestamp": _iso(end), "cwd": str(base), "isSidechain": sidechain,
              "message": {"role": "user", "content": [
                  {"tool_use_id": "toolu_plant", "type": "tool_result", "content": "./out.exe",
                   "is_error": is_error}]},
              "toolUseResult": {"stdout": "./out.exe", "stderr": "", "interrupted": False}}
    path = Path(base, name + ".jsonl")
    path.write_text(json.dumps(use) + "\n" + json.dumps(result) + "\n", encoding="utf-8", newline="\n")
    return path


def _live_shape(folder):
    return ('D="%s"; mkdir "$D" && cd "$D" && curl -sL -o artifact.zip "https://example.invalid/a" '
            '&& unzip -q artifact.zip && rm artifact.zip' % _msys(folder))


SESSION_DIR_CASES = ["live_msys", "live_windows", "chained", "mixed", "flag_p", "older", "errored", "sidechain",
                     "heredoc", "withgit", "parent", "other", "no_transcript"]


@pytest.mark.parametrize("label", SESSION_DIR_CASES)
def test_session_made_dir(plant, tmp_path, label):
    downloads = tmp_path / "downloads"
    downloads.mkdir(exist_ok=True)
    if not hasattr(os.stat(downloads), "st_birthtime"):
        pytest.skip("this platform reports no creation time; the allow fails closed here")

    def made(name, command_for):
        folder = downloads / name
        start = time.time() - 1
        folder.mkdir()
        (folder / "payload.bin").write_bytes(b"x")
        return folder, _transcript(tmp_path, name, command_for(folder), start, time.time() + 1)

    plain = lambda f: 'mkdir "%s"' % _msys(f)  # noqa: E731
    if label in ("live_msys", "live_windows"):
        folder, tx = made("live", _live_shape)
        command = 'rm -r "%s"' % _msys(folder) if label == "live_msys" else 'rm -rf "%s"' % folder
        want = "allow"
    elif label == "chained":
        folder, tx = made("chained", _live_shape)
        command, want = 'rm -r "%s"; rm -r "%s"' % (_msys(folder), _msys(downloads)), "deny"
    elif label == "mixed":
        folder, tx = made("mixed", _live_shape)
        command, want = 'rm -r "%s"; rm -r "%s"' % (_msys(folder), plant / ".claude" / "logs"), "allow"
    elif label == "flag_p":
        folder, tx = made("flag-p", lambda f: 'mkdir -p "%s"' % _msys(f))
        command, want = 'rm -r "%s"' % _msys(folder), "deny"
    elif label == "older":
        folder = downloads / "older"
        folder.mkdir()
        later = time.time() + 30
        tx = _transcript(tmp_path, "older", plain(folder), later, later + 5)
        command, want = 'rm -r "%s"' % _msys(folder), "deny"
    elif label in ("errored", "sidechain"):
        folder, _ = made(label, plain)
        tx = _transcript(tmp_path, label, plain(folder), os.stat(folder).st_ctime - 1, time.time() + 1,
                         is_error=label == "errored", sidechain=label == "sidechain")
        command, want = 'rm -r "%s"' % _msys(folder), "deny"
    elif label == "heredoc":
        folder, tx = made("heredoc", lambda f: 'cat > notes.txt <<EOF\nmkdir "%s"\nEOF' % _msys(f))
        command, want = 'rm -r "%s"' % _msys(folder), "deny"
    elif label == "withgit":
        folder, tx = made("withgit", plain)
        (folder / "repo" / ".git").mkdir(parents=True)
        command, want = 'rm -r "%s"' % _msys(folder), "deny"
    elif label == "parent":
        folder, tx = made("parent", plain)
        (folder / "inner").mkdir()
        command, want = 'rm -r "%s"' % _msys(folder / "inner"), "deny"
    elif label == "other":
        folder, tx = made("other", plain)
        command, want = 'rm -r "%s"' % _msys(downloads), "deny"
    else:
        folder, _ = made("notx", plain)
        command, want, tx = 'rm -r "%s"' % _msys(folder), "deny", None
    assert bash(command, plant, transcript_path=tx).kind == want


# --- S4's two rows (plan S5 input): a plugin cache, and a temp tree this session made ----------------------------
@pytest.fixture
def home(plant, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    return home


def test_s4_a_plugin_cache_folder_holding_no_repository_allows(plant, home):
    write(home, ".claude/plugins/cache/mkt/spike/1.0.0/hooks/h.py")
    target = (home / ".claude/plugins/cache/mkt").as_posix()
    assert bash('rm -rf "%s"' % target, plant).kind == "allow"


@pytest.mark.parametrize("rel", [".claude/plugins/cache", ".claude/plugins", ".claude/plugins/cache/mkt/../.."])
def test_s4_the_plugin_cache_root_and_above_stay_denied(plant, home, rel):
    write(home, ".claude/plugins/cache/mkt/spike/1.0.0/hooks/h.py")
    assert bash('rm -rf "%s/%s"' % (home.as_posix(), rel), plant).denied


def test_s4_a_plugin_cache_folder_holding_a_repository_stays_denied(plant, home):
    write(home, ".claude/plugins/cache/mkt/spike/1.0.0/hooks/h.py")
    git(home / ".claude/plugins/cache/mkt/spike", "init", "-q")
    assert bash('rm -rf "%s"' % (home / ".claude/plugins/cache/mkt").as_posix(), plant).denied


def test_s4_a_temp_tree_this_session_made_holding_no_repository_allows(plant):
    tree = Path(tempfile.mkdtemp(prefix="v2s4_"))
    write(tree, "out/hook_ran.txt")
    assert bash('rm -rf "%s"' % tree.as_posix(), plant).kind == "allow"


def test_s4_a_temp_tree_holding_git_repositories_allows(plant):
    """D-TT (owner, 2026-11-23): the core doctrine exempts temp dirs, repositories inside them included."""
    tree = Path(tempfile.mkdtemp(prefix="v2s4_"))
    (tree / "repo").mkdir()
    git(tree / "repo", "init", "-q")
    write(tree, "repo/work.txt")
    assert bash('rm -rf "%s"' % tree.as_posix(), plant).kind == "allow"


def test_s4_a_repository_outside_temp_stays_denied(plant, tmp_path):
    (tmp_path / "elsewhere" / "repo").mkdir(parents=True)
    git(tmp_path / "elsewhere" / "repo", "init", "-q")
    assert bash('rm -rf "%s"' % (tmp_path / "elsewhere").as_posix(), plant).denied


def test_s4_a_temp_tree_holding_the_project_or_its_worktree_stays_denied(plant, monkeypatch):
    """The temp exemption never reaches the checkout the floor protects or a worktree registered to it."""
    import tempfile as _tempfile
    monkeypatch.setattr(_tempfile, "tempdir", str(plant.parent))
    assert bash('rm -rf "%s"' % plant.as_posix(), plant).denied
    outside = Path(_tempfile.mkdtemp(prefix="v2s4_wt_", dir=str(plant.parent)))
    git(plant, "worktree", "add", "-q", (outside / "lane").as_posix(), "HEAD")
    assert bash('rm -rf "%s"' % outside.as_posix(), plant).denied
