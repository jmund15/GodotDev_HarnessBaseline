"""Shared cases for every PreToolUse guard that gates `git commit` through hooks/_git_commit.py.

A guard's own domain scan is its own proof; these cases prove the DETECTION half every guard
shares: a non-Bash tool is ignored, a `git commit` quoted inside a heredoc body does not fire,
and a real commit of a non-domain file in a clean temp repo passes. A guard that reads the staged
diff or index also gets a PLANTED violation: it must deny from the default index, and from another
index the command names (`GIT_INDEX_FILE=<f> git commit`, or an earlier `export`). Each `test_<guard>_commit_
detection.py` is one line: `run_for("<guard>.py")`.
"""
import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")

# `head` is committed; `staged` is what the commit publishes.
PLANTED = {
    "provisional_totality_guard.py": {
        "head": {},
        "staged": {"Game/Planted.cs": "// PROVISIONAL(planted-slug)\nclass Planted {}\n"},
    },
    "prototype_containment_guard.py": {
        "head": {},
        "staged": {"prototypes/planted/scene.tscn": "[gd_scene format=3]\n"},
    },
    "tres_script_strip_guard.py": {
        "head": {"Data/planted.tres": '[gd_resource type="Resource" format=3]\n\n[resource]\nscript = ExtResource("1_x")\n'},
        "staged": {"Data/planted.tres": '[gd_resource type="Resource" format=3]\n\n[resource]\n'},
    },
    "tres_nullstrip_guard.py": {
        "head": {"Game/Inv.cs": "public partial class Inv\n{\n    [Export] public int MaxSlots { get; set; } = 24;\n}\n",
                 "Game/inv.tscn": '[node name="Inv" type="Node"]\nMaxSlots = 24\n'},
        "staged": {"Game/inv.tscn": '[node name="Inv" type="Node"]\nMaxSlots = null\n'},
    },
}


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _make_repo():
    repo = tempfile.mkdtemp(prefix="cgcases_")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    with open(os.path.join(repo, "README.md"), "a") as fh:
        fh.write("more\n")
    _git(repo, "add", "README.md")
    return repo


def _write_files(repo, files):
    for rel, content in files.items():
        full = os.path.join(repo, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)


def _planted_repo(spec):
    repo = _make_repo()
    _write_files(repo, spec["head"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "planted head")
    return repo


def _planted_cases(spec):
    control = _planted_repo(spec)
    _write_files(control, spec["staged"])
    _git(control, "add", "-A")

    alt_repo = _planted_repo(spec)
    alt_index = os.path.join(alt_repo, "alt.index").replace("\\", "/")
    alt_env = dict(os.environ, GIT_INDEX_FILE=alt_index)
    subprocess.run(["git", "read-tree", "HEAD"], cwd=alt_repo, env=alt_env, check=True, capture_output=True)
    _write_files(alt_repo, spec["staged"])
    subprocess.run(["git", "add", "--", *spec["staged"]], cwd=alt_repo, env=alt_env, check=True,
                   capture_output=True)
    for rel in spec["staged"]:  # the worktree returns to HEAD: only the other index holds the violation
        if rel in spec["head"]:
            _write_files(alt_repo, {rel: spec["head"][rel]})
        else:
            os.remove(os.path.join(alt_repo, *rel.split("/")))

    def payload(repo, command):
        return {"tool_name": "Bash", "session_id": "s1", "cwd": repo, "tool_input": {"command": command}}

    return [
        ("a planted violation in the default index denies (control)",
         payload(control, "git commit -q -F m"), control, "deny"),
        ("the same violation only in GIT_INDEX_FILE=<other index> denies",
         payload(alt_repo, "GIT_INDEX_FILE=%s git commit -q -F m" % alt_index), alt_repo, "deny"),
        ("the same violation behind an earlier `export GIT_INDEX_FILE` denies",
         payload(alt_repo, "export GIT_INDEX_FILE=%s && git commit -q -F m" % alt_index), alt_repo, "deny"),
    ]


def _run(hook, payload, cwd):
    r = subprocess.run([sys.executable, os.path.join(HOOKS, hook), "--hook"],
                       input=json.dumps(payload), capture_output=True, text=True,
                       timeout=120, cwd=cwd)
    # A crashed hook exits 1 with a traceback and the harness treats it as non-blocking —
    # the same silence as an allow. Name it, or a broken guard reads as a clean proof.
    if r.returncode not in (0, 2) or "Traceback" in (r.stderr or ""):
        return "crash", (r.stdout + r.stderr)[-300:]
    denied = r.returncode == 2 or '"deny"' in (r.stdout or "")
    return "deny" if denied else "allow", (r.stdout + r.stderr)[-300:]


def run_for(hook):
    repo = _make_repo()
    base = {"tool_name": "Bash", "session_id": "s1", "cwd": repo}
    cases = [
        ("a non-Bash tool is ignored",
         dict(base, tool_name="Read", tool_input={"file_path": "x"})),
        ("`git commit` inside a heredoc body does not fire the guard",
         dict(base, tool_input={"command": "cat > m <<'EOF'\ngit commit -am x\nEOF\necho done"})),
        ("a real commit of a non-domain file in a clean repo passes",
         dict(base, tool_input={"command": "git commit -q -F m -- README.md"})),
    ]
    cases = [(label, payload, repo, "allow") for label, payload in cases]
    if hook in PLANTED:
        cases += _planted_cases(PLANTED[hook])
    failures = []
    for label, payload, cwd, expected in cases:
        verdict, tail = _run(hook, payload, cwd)
        ok = verdict == expected
        print("%-4s %s: %s" % ("ok" if ok else "FAIL", hook, label))
        if not ok:
            failures.append("%s: %s -> %r" % (hook, label, tail))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0
