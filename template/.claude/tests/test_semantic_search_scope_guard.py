#!/usr/bin/env python3
"""Re-runnable proof for hooks/semantic_search_scope_guard.py — a semantic-search call whose
searchDir is not a git top-level, or whose restrictToDir is non-POSIX, is blocked (exit 2 +
stderr) through both process() directly and the real pre_read_dispatch.py channel. Other tools and
well-formed calls pass.

    python -m pytest .claude/tests/test_semantic_search_scope_guard.py -q
"""
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HOOKS_DIR = os.path.join(ROOT, ".claude", "hooks")
GUARD = os.path.join(HOOKS_DIR, "semantic_search_scope_guard.py")
DISPATCH = os.path.join(HOOKS_DIR, "pre_read_dispatch.py")
TOOL = "mcp__plugin_semantic-search_semantic-search__search"
# An absolute path outside the project on the running platform: `C:/...` is relative on POSIX.
OUTSIDE = "C:/Users/x/.claude" if os.name == "nt" else "/home/x/.claude"

sys.path.insert(0, HOOKS_DIR)
sys.path.insert(0, os.path.join(ROOT, '.claude', 'tools'))
import adaptation  # noqa: E402
import semantic_search_scope_guard as guard  # noqa: E402

# Design-source roots are the project's `doc_roots`; parent/sibling roots remain external.
ROOTS = [r.replace('\\', '/') for r in adaptation.get(os.path.join(ROOT, '.claude'), 'doc_roots')]
VAULT = ROOTS[0].rsplit('/', 1)[0] if ROOTS else OUTSIDE.rsplit('/', 1)[0] + '/vault'
EXTERNAL = [VAULT, VAULT + '/Other', (ROOTS[0] + '-copy') if ROOTS else VAULT + '/Copy', OUTSIDE]


def run_standalone(payload, env=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    full_env = dict(os.environ)
    full_env["CLAUDE_PROJECT_DIR"] = ROOT
    if env:
        full_env.update(env)
    r = subprocess.run([sys.executable, GUARD, "--hook"], input=raw, capture_output=True,
                       text=True, encoding="utf-8", timeout=60, env=full_env)
    return r.returncode, r.stderr or ""


def run_standalone_out(payload):
    full_env = dict(os.environ, CLAUDE_PROJECT_DIR=ROOT)
    r = subprocess.run([sys.executable, GUARD, "--hook"], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=60, env=full_env)
    return r.returncode, r.stdout or ""


def run_dispatch_out(payload):
    full_env = dict(os.environ, CLAUDE_PROJECT_DIR=ROOT)
    r = subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=60, env=full_env)
    return r.returncode, r.stdout or ""


def run_dispatch(payload):
    """A deny is a permission decision on stdout, exit 0; stderr and stdout are both returned."""
    full_env = dict(os.environ, CLAUDE_PROJECT_DIR=ROOT)
    r = subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=60, env=full_env)
    return r.returncode, (r.stderr or "") + (r.stdout or "")


def rewrite_of(result):
    return (result or {}).get("updatedInput") or {}


# --- standalone and dispatcher channels against the project's own roots --------------------------

def spellings_of(path):
    return [path, path.replace('/', '\\'), path + '/../' + path.rsplit('/', 1)[1]]


@pytest.mark.parametrize("root", ROOTS, ids=[r.rsplit('/', 1)[1] for r in ROOTS])
def test_a_vault_root_is_allowed_in_every_spelling(root):
    for spelling in spellings_of(root):
        rc, err = run_standalone({'tool_name': TOOL, 'tool_input': {'searchDir': spelling}, 'cwd': ROOT})
        assert rc == 0 and not err, spelling


@pytest.mark.parametrize("root", ROOTS, ids=[r.rsplit('/', 1)[1] for r in ROOTS])
def test_a_vault_restrict_is_normalized_against_its_own_root(root):
    payload = {'tool_name': TOOL, 'tool_input': {'searchDir': root, 'restrictToDir': './Claude'}, 'cwd': ROOT}
    rc, out = run_standalone_out(payload)
    assert rc == 0 and json.loads(out or '{}').get('hookSpecificOutput', {}).get('updatedInput', {}) \
        .get('restrictToDir') == 'Claude'


@pytest.mark.parametrize("root", ROOTS, ids=[r.rsplit('/', 1)[1] for r in ROOTS])
def test_a_vault_restrict_cannot_escape_its_root(root):
    rc, err = run_standalone({'tool_name': TOOL, 'tool_input': {'searchDir': root, 'restrictToDir': '../Other'},
                              'cwd': ROOT})
    assert rc == 2 and bool(err)


@pytest.mark.parametrize("path", EXTERNAL)
def test_an_external_root_remains_denied(path):
    rc, err = run_standalone({'tool_name': TOOL, 'tool_input': {'searchDir': path}, 'cwd': ROOT})
    assert rc == 2 and bool(err), path


# --- the roots come from the project's own adaptation data: a planted project proves the guard carries none --

@pytest.fixture
def planted_project(tmp_path):
    """A planted project whose adaptation.json lists the doc_roots a case plants; returns a runner for the guard."""
    claude = tmp_path / '.claude'
    for sub in ('hooks', 'tools', os.path.join('skills', 'project_subsystems')):
        (claude / sub).mkdir(parents=True)
    shutil.copyfile(GUARD, claude / 'hooks' / 'semantic_search_scope_guard.py')
    shutil.copyfile(os.path.join(ROOT, '.claude', 'tools', 'adaptation.py'), claude / 'tools' / 'adaptation.py')
    guard_copy = str(claude / 'hooks' / 'semantic_search_scope_guard.py')

    def planted(search_dir, doc_roots):
        with open(claude / 'skills' / 'project_subsystems' / 'adaptation.json', 'w', encoding='utf-8') as fh:
            json.dump({'doc_roots': doc_roots}, fh)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
        r = subprocess.run([sys.executable, guard_copy, '--hook'], capture_output=True, text=True,
                           encoding='utf-8', timeout=60, env=env,
                           input=json.dumps({'tool_name': TOOL, 'tool_input': {'searchDir': search_dir},
                                             'cwd': str(tmp_path)}))
        return r.returncode, r.stderr or ''
    return planted


PLANTED_ROOT = 'C:/Users/dev/notes/Design'
STALE_ROOT = 'C:/Users/dev/Documents/Vault/Projects/Game'


def test_a_root_the_project_lists_in_doc_roots_is_allowed(planted_project):
    rc, err = planted_project(PLANTED_ROOT, [PLANTED_ROOT])
    assert rc == 0 and not err


def test_a_project_listing_no_doc_roots_allows_no_external_root(planted_project):
    rc, err = planted_project(PLANTED_ROOT, [])
    assert rc == 2 and bool(err)


def test_a_root_the_project_does_not_list_stays_denied(planted_project):
    rc, err = planted_project(STALE_ROOT, [PLANTED_ROOT])
    assert rc == 2 and bool(err)


# --- process() direct -----------------------------------------------------------------------------

def test_a_project_root_searchDir_is_allowed_by_process():
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT}, "cwd": ROOT}) is None


def test_a_claude_searchDir_is_rewritten_to_the_root_with_restrictToDir_claude_by_process():
    fixed = rewrite_of(guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"}, "cwd": ROOT}))
    assert os.path.normcase(fixed.get("searchDir", "")) == os.path.normcase(ROOT) and fixed.get("restrictToDir") == ".claude"


def test_an_absolute_claude_searchDir_with_restrictToDir_hooks_becomes_restrictToDir_claude_hooks_by_process():
    fixed = rewrite_of(guard.process({"tool_name": TOOL, "tool_input": {"searchDir": os.path.join(ROOT, ".claude"),
                                                                          "restrictToDir": "hooks"}, "cwd": ROOT}))
    assert os.path.normcase(fixed.get("searchDir", "")) == os.path.normcase(ROOT) \
        and fixed.get("restrictToDir") == ".claude/hooks"


@pytest.fixture
def git_tree(tmp_path, monkeypatch):
    """A project with a nested submodule git top-level and a sibling repo outside it."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    project = tmp_path / "project"
    nested = project / "vendor" / "Sub"
    sibling = tmp_path / "sibling"
    os.makedirs(project / ".git")
    os.makedirs(nested)
    os.makedirs(sibling / ".git")
    (nested / ".git").write_text("gitdir: ../../.git/modules/Sub\n", encoding="utf-8")
    return SimpleNamespace(project=str(project), nested=str(nested), sibling=str(sibling))


def test_a_submodule_git_top_level_inside_the_project_is_allowed_by_process(git_tree):
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": git_tree.nested},
                          "cwd": git_tree.project}) is None


def test_a_git_top_level_outside_the_project_is_denied_by_process(git_tree):
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": git_tree.sibling},
                          "cwd": git_tree.project}) is not None


def test_a_relative_parent_sibling_git_top_level_is_denied_by_process(git_tree):
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": "../sibling"},
                          "cwd": git_tree.project}) is not None


def test_a_restrictToDir_of_claude_auto_memory_is_allowed_by_process():
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT, "restrictToDir": ".claude/auto-memory"},
                          "cwd": ROOT}) is None


def test_an_absolute_outside_searchDir_is_denied_by_process():
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": OUTSIDE}, "cwd": ROOT}) is not None


@pytest.mark.parametrize("raw, want", [
    (".claude\\hooks", ".claude/hooks"), (".claude/../Scripts", "Scripts"),
    ("./.claude", ".claude"), (".claude/./hooks", ".claude/hooks"),
    (".claude//hooks", ".claude/hooks"),
    (os.path.join(ROOT, ".claude", "hooks"), ".claude/hooks"),
])
def test_a_restrictToDir_is_rewritten_to_its_canonical_form_by_process(raw, want):
    got = rewrite_of(guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT, "restrictToDir": raw},
                                    "cwd": ROOT})).get("restrictToDir")
    assert got == want


@pytest.mark.parametrize("bad", ["../Tests", ".."])
def test_a_restrictToDir_that_leaves_the_project_is_denied_by_process(bad):
    assert (guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT, "restrictToDir": bad},
                           "cwd": ROOT}) or {}).get("deny")


def test_one_trailing_slash_is_allowed_by_process():
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT, "restrictToDir": ".claude/auto-memory/"},
                          "cwd": ROOT}) is None


def test_a_dotted_segment_name_is_allowed_by_process():
    assert guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT, "restrictToDir": ".claude/.cache"},
                          "cwd": ROOT}) is None


def test_an_adjacent_Grep_tool_with_path_claude_is_untouched_by_process():
    assert guard.process({"tool_name": "Grep", "tool_input": {"path": ".claude"}, "cwd": ROOT}) is None


def test_a_non_dict_payload_does_not_crash_process():
    assert guard.process("not a dict") is None


def test_a_payload_missing_tool_input_does_not_crash_process():
    assert guard.process({"tool_name": TOOL}) is None


def test_an_absent_searchDir_is_allowed_by_process():
    assert guard.process({"tool_name": TOOL, "tool_input": {}, "cwd": ROOT}) is None


def test_the_deny_message_cites_the_searchDir_rule_owner_and_names_the_field_to_change():
    msg = (guard.process({"tool_name": TOOL, "tool_input": {"searchDir": OUTSIDE}, "cwd": ROOT}) or {}).get("deny") or ""
    assert bool(msg) and "BLOCKED (reference/semantic_search.md)" in msg and "searchDir" in msg \
        and ".claude/auto-memory" in msg


# --- standalone --hook channel --------------------------------------------------------------------

def test_standalone_hook_an_outside_searchDir_exits_two_with_stderr():
    rc, err = run_standalone({"tool_name": TOOL, "tool_input": {"searchDir": OUTSIDE}, "cwd": ROOT})
    assert rc == 2 and bool(err)


def test_standalone_hook_a_claude_searchDir_exits_zero_with_updatedInput():
    rc, out = run_standalone_out({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"}, "cwd": ROOT})
    assert rc == 0 and json.loads(out or "{}").get("hookSpecificOutput", {}).get("updatedInput", {}) \
        .get("restrictToDir") == ".claude"


def test_standalone_hook_a_project_root_searchDir_exits_zero_silently():
    rc, err = run_standalone({"tool_name": TOOL, "tool_input": {"searchDir": ROOT}, "cwd": ROOT})
    assert rc == 0 and not err


def test_standalone_hook_a_malformed_payload_exits_outside_the_crash_range():
    rc, _ = run_standalone("{not json")
    assert rc in (0, 2)


# --- real pre_read_dispatch.py channel -----------------------------------------------------------

def test_the_dispatcher_rewrites_a_claude_searchDir_and_notes_it():
    rc, out = run_dispatch_out({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"}, "cwd": ROOT})
    hso = json.loads(out or "{}").get("hookSpecificOutput", {})
    assert (rc == 0 and hso.get("updatedInput", {}).get("restrictToDir") == ".claude"
            and "reference/semantic_search.md" in hso.get("additionalContext", ""))


def test_the_dispatcher_denies_an_outside_searchDir():
    rc, err = run_dispatch({"tool_name": TOOL, "tool_input": {"searchDir": OUTSIDE}, "cwd": ROOT})
    assert rc == 0 and '"deny"' in err and "BLOCKED (reference/semantic_search.md)" in err and "searchDir" in err


def test_the_dispatcher_cites_the_memory_rule_and_next_step_for_a_non_canonical_restrictToDir():
    rc, err = run_dispatch({"tool_name": TOOL, "tool_input": {"searchDir": ROOT, "restrictToDir": "../Tests"}, "cwd": ROOT})
    assert rc == 0 and '"deny"' in err and "reference/semantic_search.md Roots" in err and "searchDir" in err


def test_the_dispatcher_allows_a_project_root_searchDir():
    rc, _ = run_dispatch({"tool_name": TOOL, "tool_input": {"searchDir": ROOT}, "cwd": ROOT})
    assert rc == 0


def test_the_dispatcher_leaves_an_adjacent_Grep_untouched():
    rc, _ = run_dispatch({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": ".claude"}, "cwd": ROOT})
    assert rc == 0
