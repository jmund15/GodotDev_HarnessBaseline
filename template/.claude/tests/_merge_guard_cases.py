"""Shared merge-scope cases for every PreToolUse guard that gates `git commit`.

A merge commit re-commits content its parents already had judged. `hooks/_commit_scope.py` decides
the two questions every such guard asks: `publishes_index` (does this invocation publish the
index? -- the closing `git commit`, or the `--continue` finishing a merge / cherry-pick / revert)
and `authored_paths` (which of these paths did THIS commit write -- staged content differing from
BOTH parents). A guard that keeps `c.sub == "commit"` lets a `--continue` close with no check, and
a guard that judges the whole staged diff denies content that arrived unchanged from `MERGE_HEAD`.

Each guard is proven against a copy of itself installed at `<temp repo>/.claude/hooks/`, so the
`REPO = Path(__file__).parents[2]` tree-scanning guards scan the planted repo rather than this
checkout. The payload is a real PreToolUse document on stdin; the verdict is read from the emitted
channel (exit 2 / `permissionDecision: deny` vs exit 0 / `{}`). An exit outside {0, 2}, or a
traceback, is a CRASH and fails its case -- a hook that cannot import must not read as allow.

Case arms per guard, from `MERGE_PLANTED[<hook>] = {"head": {...}, "offend": {...}}`:
    A  the offending content arrives unchanged from MERGE_HEAD, closed by `git commit`  -> allow
    B  the same repo, closed by `git merge --continue`                                  -> allow
    C  the offending content differs from BOTH parents (a conflict resolved to it)      -> deny
    D  the same repo, closed by `git merge --continue`                                  -> deny
`setup` (optional) plants fixtures the guard reads but does not judge, e.g. the gate filter file
`test_suite_gate_coverage_guard.py` cross-checks its GATED list against.

Each guard also gets the must-allow cases of `_must_allow_cases.py` (a merge in a fresh worktree, a
commit in a fresh clone lacking the gitignored inputs), built from the same spec.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)
from _must_allow_cases import must_allow_cases  # noqa: E402
HOOKS = os.path.join(_REPO, ".claude", "hooks")

# The guard under test plus the parser/scope modules it imports. Copied together: a guard whose
# sibling is missing cannot import, and every case would then read as CRASH rather than as a pass.
SUPPORT = ("_git_commit.py", "_command_text.py", "_commit_scope.py", "_cs_files.py", "_checkout_root.py")


def _filter_file(*names):
    """A `.claude/scripts/regression_gate.ps1` declaring the filters a GATED name must match."""
    return "".join("-Filter \"FullyQualifiedName~Tests.%s\"\n" % n for n in names)


MERGE_PLANTED = {
    "prototype_containment_guard.py": {
        "head": {},
        "offend": {"prototypes/planted/scene.tscn": "[gd_scene format=3]\n\n[node name=\"Planted\" type=\"Node\"]\n"},
    },
    "provisional_totality_guard.py": {
        "head": {},
        "offend": {"Game/Planted.cs": "// PROVISIONAL(planted-slug)\npublic partial class Planted\n{\n}\n"},
    },
    "tres_nullstrip_guard.py": {
        "head": {"Game/Inv.cs": "public partial class Inv\n{\n    [Export] public int MaxSlots { get; set; } = 24;\n}\n",
                 "Game/inv.tscn": "[gd_scene format=3]\n\n[node name=\"Inv\" type=\"Node\"]\nMaxSlots = 24\n"},
        "offend": {"Game/inv.tscn": "[gd_scene format=3]\n\n[node name=\"Inv\" type=\"Node\"]\nMaxSlots = null\n"},
    },
    "tres_script_strip_guard.py": {
        "head": {"Data/planted.tres": (
            '[gd_resource type="Resource" format=3]\n\next_resource/1\n'
            '[resource]\nscript = ExtResource("1_x")\n')},
        "offend": {"Data/planted.tres": '[gd_resource type="Resource" format=3]\n\n[resource]\n'},
    },
}


def _git(repo, args, check=True):
    r = subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args, repo, r.stderr))
    return r


def _write(repo, files):
    for rel, content in files.items():
        full = os.path.join(repo, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)


def _init_repo():
    repo = os.path.realpath(tempfile.mkdtemp(prefix="mguard_"))
    _git(repo, ["init", "-q"])
    _git(repo, ["config", "user.email", "t@t.example"])
    _git(repo, ["config", "user.name", "t"])
    _git(repo, ["config", "commit.gpgsign", "false"])
    return repo


def _install_hooks(repo, hook):
    dst = os.path.join(repo, ".claude", "hooks")
    os.makedirs(dst, exist_ok=True)
    for name in SUPPORT + (hook,):
        shutil.copy(os.path.join(HOOKS, name), dst)
    tools = os.path.join(repo, ".claude", "tools")
    os.makedirs(tools, exist_ok=True)
    shutil.copy(os.path.join(_REPO, ".claude", "tools", "sidecar_argv.py"), tools)


def _decoy(rel, tag, head):
    """One parent's side of the conflict: the path's HEAD content plus a marker unique to this
    parent. Derived from HEAD -- not synthetic -- so a guard that detects by DIFFING the index
    against HEAD still sees the shape it looks for (`tres_script_strip_guard` sees a script
    binding removed only when HEAD held one). Distinct from the other parent's decoy, and from the
    offending content, so resolving to the offending content differs from BOTH parents."""
    return head.get(rel, "") + "// merge decoy %s for %s\n" % (tag, rel)


def _merge_repo(spec, authored):
    """A repo stopped mid-merge. `authored=False`: the offending content is what MERGE_HEAD holds,
    so the index equals the parent. `authored=True`: both parents hold a decoy and the conflict is
    resolved to the offending content, which therefore differs from both."""
    repo = _init_repo()
    _write(repo, dict(spec["head"], **{"README.md": "seed\n"}))
    if spec.get("setup"):
        spec["setup"](repo)
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", "base"])
    base = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()

    _git(repo, ["checkout", "-q", "-b", "work", base])
    if authored:
        _write(repo, {rel: _decoy(rel, "work", spec["head"]) for rel in spec["offend"]})
        _git(repo, ["add", "-A"])
        _git(repo, ["commit", "-q", "-m", "work"])
    _git(repo, ["checkout", "-q", "-b", "upstream", base])
    _write(repo, spec["offend"] if not authored
           else {rel: _decoy(rel, "upstream", spec["head"]) for rel in spec["offend"]})
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", "upstream"])
    _git(repo, ["checkout", "-q", "work"])

    stopped = _git(repo, ["merge", "--no-commit", "--no-ff", "upstream"], check=False)
    if authored:
        if stopped.returncode == 0:
            raise RuntimeError("fixture did not conflict: the merge committed cleanly")
        _write(repo, spec["offend"])
        _git(repo, ["add", "-A"])
    _install_hooks(repo, spec["_hook"])
    return repo


def _git_path(repo, name):
    rel = _git(repo, ["rev-parse", "--git-path", name]).stdout.strip()
    return rel if os.path.isabs(rel) else os.path.join(repo, rel)


def _run(repo, hook, command):
    payload = {"tool_name": "Bash", "session_id": "mguard_proof", "cwd": repo,
               "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, os.path.join(repo, ".claude", "hooks", hook), "--hook"],
                       input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180, cwd=repo)
    if r.returncode not in (0, 2) or "Traceback (most recent call last)" in (r.stderr or ""):
        return "crash", (r.stdout + r.stderr)[-400:]
    denied = r.returncode == 2 or '"deny"' in (r.stdout or "")
    return ("deny" if denied else "allow"), (r.stdout + r.stderr)[-400:]


def _case(failures, label, got, expected):
    verdict, reason = got
    ok = verdict == expected
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        failures.append("%s\n     expected=%s got=%s reason=%r" % (label, expected, verdict, reason))


def _setup_checks(failures, repo, spec, authored):
    merge_head = os.path.exists(_git_path(repo, "MERGE_HEAD"))
    _case(failures, "setup: MERGE_HEAD is planted", ("allow" if merge_head else "deny", ""), "allow")
    staged = _git(repo, ["diff", "--cached", "--name-only"]).stdout
    wanted = [rel for rel in spec["offend"]]
    missing = [rel for rel in wanted if rel not in staged]
    _case(failures, "setup: the index holds the offending path(s)", ("allow" if not missing else "deny",
                                                                    "staged=%r missing=%r" % (staged, missing)),
          "allow")
    if not authored:
        same = _git(repo, ["diff", "--cached", "--name-only", "MERGE_HEAD"]).stdout
        leaked = [rel for rel in wanted if rel in same]
        _case(failures, "setup: the offending content equals MERGE_HEAD's blob",
              ("allow" if not leaked else "deny", "differs=%r" % leaked), "allow")
    else:
        vs_head = set(_git(repo, ["diff", "--cached", "--name-only", "HEAD"]).stdout.split())
        vs_merge = set(_git(repo, ["diff", "--cached", "--name-only", "MERGE_HEAD"]).stdout.split())
        same = [rel for rel in wanted if rel not in vs_head or rel not in vs_merge]
        _case(failures, "setup: the offending content differs from BOTH parents",
              ("allow" if not same else "deny", "not-authored=%r" % same), "allow")


def merge_run_for(hook):
    """The merge-scope arms for `hook`, or nothing when it has no planted merge fixture."""
    spec = MERGE_PLANTED.get(hook)
    if spec is None:
        return 0
    failures = []

    for authored in (False, True):
        repo = _merge_repo(dict(spec, _hook=hook), authored)
        _setup_checks(failures, repo, spec, authored)
        expected = "deny" if authored else "allow"
        tail = "authored in the merge" if authored else "equals MERGE_HEAD"
        _case(failures, "%s a merge whose offending content %s, closed by `git commit` -> %s"
              % (hook, tail, expected), _run(repo, hook, "git commit -q -F m"), expected)
        _case(failures, "%s a merge whose offending content %s, closed by `git merge --continue` -> %s"
              % (hook, tail, expected), _run(repo, hook, "git merge --continue"), expected)
    total = 4

    allow_cases, cleanup = must_allow_cases(hook, planted=spec)
    try:
        for label, payload, cwd, expected in allow_cases:
            _install_hooks(cwd, hook)
            _case(failures, label, _run(cwd, hook, payload["tool_input"]["command"]), expected)
            total += 1
    finally:
        cleanup()

    print("\n%d/%d merge cases pass (%s)" % (total - len(failures), total, hook))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0
