#!/usr/bin/env python3
"""Re-runnable proof for hooks/semantic_search_scope_guard.py — a semantic-search call whose
searchDir is not a git top-level, or whose restrictToDir is non-POSIX, is blocked (exit 2 +
stderr) through both process() directly and the real pre_read_dispatch.py channel. Other tools and
well-formed calls pass.

    python3 .claude/tests/test_semantic_search_scope_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

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


def rewrite_of(result):
    return (result or {}).get("rewrite") or {}


def run_dispatch(payload, env=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    full_env = dict(os.environ)
    full_env["CLAUDE_PROJECT_DIR"] = ROOT
    if env:
        full_env.update(env)
    r = subprocess.run([sys.executable, DISPATCH], input=raw, capture_output=True,
                        text=True, encoding="utf-8", timeout=60, env=full_env)
    return r.returncode, r.stderr or ""


def main():
    cases = []

    # Design-source roots are the project's `doc_roots`; parent/sibling roots remain external.
    roots = [r.replace('\\', '/') for r in adaptation.get(os.path.join(ROOT, '.claude'), 'doc_roots')]
    vault = roots[0].rsplit('/', 1)[0] if roots else OUTSIDE.rsplit('/', 1)[0] + '/vault'
    for path in roots:
        name = path.rsplit('/', 1)[1]
        for spelling in (path, path.replace('/', '\\'), path + '/../' + name):
            payload = {'tool_name': TOOL, 'tool_input': {'searchDir': spelling}, 'cwd': ROOT}
            rc, err = run_standalone(payload)
            cases.append(('vault root allowed: ' + spelling, rc == 0 and not err))
        payload = {'tool_name': TOOL, 'tool_input': {'searchDir': path, 'restrictToDir': './Claude'}, 'cwd': ROOT}
        rc, out = run_standalone_out(payload)
        cases.append(('vault restrict normalized against its own root: ' + name,
                      rc == 0 and json.loads(out or '{}').get('hookSpecificOutput', {})
                      .get('updatedInput', {}).get('restrictToDir') == 'Claude'))
        payload['tool_input']['restrictToDir'] = '../Other'
        rc, err = run_standalone(payload)
        cases.append(('vault restrict cannot escape: ' + name, rc == 2 and bool(err)))
    for path in (vault, vault + '/Other', (roots[0] + '-copy') if roots else vault + '/Copy', OUTSIDE):
        rc, err = run_standalone({'tool_name': TOOL, 'tool_input': {'searchDir': path}, 'cwd': ROOT})
        cases.append(('external root remains denied: ' + path, rc == 2 and bool(err)))

    # The roots come from the project's own adaptation data: a planted project proves the guard carries none.
    import shutil
    with tempfile.TemporaryDirectory() as td:
        claude = os.path.join(td, '.claude')
        for sub in ('hooks', 'tools', os.path.join('skills', 'project_subsystems')):
            os.makedirs(os.path.join(claude, sub))
        shutil.copyfile(GUARD, os.path.join(claude, 'hooks', 'semantic_search_scope_guard.py'))
        shutil.copyfile(os.path.join(ROOT, '.claude', 'tools', 'adaptation.py'), os.path.join(claude, 'tools', 'adaptation.py'))
        planted_guard = os.path.join(claude, 'hooks', 'semantic_search_scope_guard.py')
        planted_root = 'C:/Users/dev/notes/Design'
        stale_root = 'C:/Users/dev/Documents/Vault/Projects/Game'

        def planted(search_dir, doc_roots):
            with open(os.path.join(claude, 'skills', 'project_subsystems', 'adaptation.json'), 'w', encoding='utf-8') as fh:
                json.dump({'doc_roots': doc_roots}, fh)
            env = dict(os.environ, CLAUDE_PROJECT_DIR=td)
            r = subprocess.run([sys.executable, planted_guard, '--hook'], capture_output=True, text=True,
                               encoding='utf-8', timeout=60, env=env,
                               input=json.dumps({'tool_name': TOOL, 'tool_input': {'searchDir': search_dir}, 'cwd': td}))
            return r.returncode, r.stderr or ''
        rc, err = planted(planted_root, [planted_root])
        cases.append(('a root the project lists in doc_roots is allowed', rc == 0 and not err))
        rc, err = planted(planted_root, [])
        cases.append(('a project listing no doc_roots allows no external root', rc == 2 and bool(err)))
        rc, err = planted(stale_root, [planted_root])
        cases.append(('a root the project does not list stays denied', rc == 2 and bool(err)))

    # --- process() direct ---
    cases.append(("project-root searchDir allowed (process)",
                  guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT},
                                  "cwd": ROOT}) is None))
    fixed = rewrite_of(guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"}, "cwd": ROOT}))
    cases.append((".claude searchDir is rewritten to the root with restrictToDir .claude (process)",
                  os.path.normcase(fixed.get("searchDir", "")) == os.path.normcase(ROOT)
                  and fixed.get("restrictToDir") == ".claude"))
    abs_claude = os.path.join(ROOT, ".claude")
    fixed = rewrite_of(guard.process({"tool_name": TOOL, "tool_input": {"searchDir": abs_claude,
                                                                          "restrictToDir": "hooks"}, "cwd": ROOT}))
    cases.append(("an absolute .claude searchDir with restrictToDir hooks becomes restrictToDir .claude/hooks (process)",
                  os.path.normcase(fixed.get("searchDir", "")) == os.path.normcase(ROOT)
                  and fixed.get("restrictToDir") == ".claude/hooks"))
    with tempfile.TemporaryDirectory() as td:
        project = os.path.join(td, "project")
        nested = os.path.join(project, "vendor", "Sub")
        sibling = os.path.join(td, "sibling")
        os.makedirs(os.path.join(project, ".git"))
        os.makedirs(nested)
        os.makedirs(os.path.join(sibling, ".git"))
        with open(os.path.join(nested, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: ../../.git/modules/Sub\n")
        saved_project_dir = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        try:
            cases.append(("submodule git top-level inside the project allowed (process)",
                          guard.process({"tool_name": TOOL, "tool_input": {"searchDir": nested},
                                         "cwd": project}) is None))
            cases.append(("git top-level outside the project denied (process)",
                          guard.process({"tool_name": TOOL, "tool_input": {"searchDir": sibling},
                                         "cwd": project}) is not None))
            cases.append(("relative ../sibling git top-level denied (process)",
                          guard.process({"tool_name": TOOL, "tool_input": {"searchDir": "../sibling"},
                                         "cwd": project}) is not None))
        finally:
            if saved_project_dir is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = saved_project_dir
    cases.append(("restrictToDir .claude/auto-memory allowed (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ROOT,
                                                 "restrictToDir": ".claude/auto-memory"},
                                  "cwd": ROOT}) is None))
    cases.append(("absolute outside searchDir denied (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": OUTSIDE},
                                  "cwd": ROOT}) is not None))
    for raw, want in ((".claude\\hooks", ".claude/hooks"), (".claude/../Scripts", "Scripts"),
                      ("./.claude", ".claude"), (".claude/./hooks", ".claude/hooks"),
                      (".claude//hooks", ".claude/hooks"),
                      (os.path.join(ROOT, ".claude", "hooks"), ".claude/hooks")):
        got = rewrite_of(guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT, "restrictToDir": raw},
                                        "cwd": ROOT})).get("restrictToDir")
        cases.append(("restrictToDir %r is rewritten to %r (process)" % (raw, want), got == want))
    for bad in ("../Tests", ".."):
        cases.append(("restrictToDir %r that leaves the project is denied (process)" % bad,
                      (guard.process({"tool_name": TOOL,
                                      "tool_input": {"searchDir": ROOT, "restrictToDir": bad},
                                      "cwd": ROOT}) or {}).get("deny")))
    cases.append(("one trailing slash .claude/auto-memory/ allowed (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ROOT, "restrictToDir": ".claude/auto-memory/"},
                                  "cwd": ROOT}) is None))
    cases.append(("dotted segment name .claude/.cache allowed (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ROOT, "restrictToDir": ".claude/.cache"},
                                  "cwd": ROOT}) is None))
    cases.append(("adjacent Grep tool with path .claude untouched (process)",
                  guard.process({"tool_name": "Grep",
                                  "tool_input": {"path": ".claude"}, "cwd": ROOT}) is None))
    cases.append(("non-dict payload no crash (process)", guard.process("not a dict") is None))
    cases.append(("payload missing tool_input no crash (process)",
                  guard.process({"tool_name": TOOL}) is None))
    cases.append(("absent searchDir allowed (process)",
                  guard.process({"tool_name": TOOL, "tool_input": {}, "cwd": ROOT}) is None))

    # --- deny message names the correct call ---
    result = guard.process({"tool_name": TOOL, "tool_input": {"searchDir": OUTSIDE}, "cwd": ROOT})
    msg = (result or {}).get("deny") or ""
    cases.append(("deny message cites the searchDir rule's owner and names the canonical root + restrictToDir next step",
                  bool(msg) and "BLOCKED (reference/semantic_search.md)" in msg
                  and "Call again with searchDir = the project root" in msg
                  and ".claude/auto-memory" in msg))

    # --- standalone --hook channel ---
    rc, err = run_standalone({"tool_name": TOOL, "tool_input": {"searchDir": OUTSIDE},
                               "cwd": ROOT})
    cases.append(("standalone --hook channel: an outside searchDir -> exit 2 + stderr",
                  rc == 2 and bool(err)))
    rc, out = run_standalone_out({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"}, "cwd": ROOT})
    cases.append(("standalone --hook channel: a .claude searchDir -> exit 0 with updatedInput",
                  rc == 0 and json.loads(out or "{}").get("hookSpecificOutput", {}).get("updatedInput", {})
                  .get("restrictToDir") == ".claude"))
    rc, err = run_standalone({"tool_name": TOOL, "tool_input": {"searchDir": ROOT}, "cwd": ROOT})
    cases.append(("standalone --hook channel: project-root searchDir -> exit 0",
                  rc == 0 and not err))
    rc, err = run_standalone("{not json")
    cases.append(("standalone --hook channel: malformed payload -> exit outside crash range",
                  rc in (0, 2)))

    # --- real pre_read_dispatch.py channel ---
    rc, out = run_dispatch_out({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"}, "cwd": ROOT})
    hso = json.loads(out or "{}").get("hookSpecificOutput", {})
    cases.append(("real pre_read_dispatch.py channel: a .claude searchDir is rewritten and noted",
                  rc == 0 and hso.get("updatedInput", {}).get("restrictToDir") == ".claude"
                  and "reference/semantic_search.md" in hso.get("additionalContext", "")))
    rc, err = run_dispatch({"tool_name": TOOL, "tool_input": {"searchDir": OUTSIDE},
                             "cwd": ROOT})
    cases.append(("real pre_read_dispatch.py channel: an outside searchDir denied",
                  rc == 2 and "BLOCKED (reference/semantic_search.md)" in err
                  and "Call again with searchDir = the project root" in err))
    rc, err = run_dispatch({"tool_name": TOOL,
                            "tool_input": {"searchDir": ROOT, "restrictToDir": "../Tests"},
                            "cwd": ROOT})
    cases.append(("real dispatcher channel: non-canonical restrictToDir cites memory rule + next step",
                  rc == 2 and "CLAUDE.md §2 Memory" in err
                  and "Call again with searchDir = the project root" in err))
    rc, err = run_dispatch({"tool_name": TOOL, "tool_input": {"searchDir": ROOT}, "cwd": ROOT})
    cases.append(("real pre_read_dispatch.py channel: project-root searchDir allowed",
                  rc == 0))
    rc, err = run_dispatch({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": ".claude"},
                             "cwd": ROOT})
    cases.append(("real pre_read_dispatch.py channel: adjacent Grep untouched", rc == 0))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
