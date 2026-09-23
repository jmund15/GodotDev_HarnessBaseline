"""Proof for hooks/approving_gates.py. Runs gate shape checks in a planted Git project."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import approving_gates


def main():
    tmp = tempfile.mkdtemp(prefix="approving_gates_")
    root = Path(tmp)
    scripts = root / ".claude" / "scripts"
    tools = root / ".claude" / "tools"
    scripts.mkdir(parents=True)
    tools.mkdir(parents=True)
    digest_script = tools / "session_digest.py"
    digest_script.write_text("#!/usr/bin/env python3\\n", encoding="utf-8")
    digest_data = tools / "digest.json"
    digest_data.write_text("{}\\n", encoding="utf-8")
    launcher = scripts / "codex_proxy_sidecar.sh"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (root / ".claude" / "reference").mkdir()
    (root / ".claude" / "reference" / "prompt.md").write_text("prompt\n", encoding="utf-8")
    peer = root / ".claude" / "worktrees" / "peer"
    (peer / "sub").mkdir(parents=True)
    (peer / "p.md").write_text("peer\n", encoding="utf-8")
    (root / "output").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", ".claude/scripts/codex_proxy_sidecar.sh",
                    ".claude/tools/session_digest.py"], cwd=root, check=True)

    def run_gate(command, tool="Bash", cwd=None, project=None):
        shell_cwd = Path(cwd) if cwd else root
        payload = {"tool_name": tool, "cwd": str(shell_cwd),
                   "tool_input": {"command": command}}
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env.pop("CLAUDE_PROJECT_DIR", None)
        if project != "unset":
            env["CLAUDE_PROJECT_DIR"] = str(project or root)
        result = subprocess.run([sys.executable, str(HOOKS / "approving_gates.py")],
                                input=json.dumps(payload), capture_output=True, text=True,
                                encoding="utf-8", timeout=30, env=env, cwd=shell_cwd)
        text = result.stdout.strip()
        try:
            hso = json.loads(text).get("hookSpecificOutput", {}) if text else {}
        except ValueError:
            hso = {"unparseable": text}
        return result.returncode, hso

    good = "bash .claude/scripts/codex_proxy_sidecar.sh -G survey -f .claude/reference/prompt.md"
    cases = []
    ok, why = approving_gates.sidecar_launch_shape(good, str(root))
    cases.append(("canonical tracked survey launch is approved", ok))
    denied_l1 = ("cd C:/work/project && export CCP_BIN=x && "
                 "bash .claude/scripts/codex_proxy_sidecar.sh -G survey -f .claude/reference/prompt.md > cap.json")
    note = approving_gates.explain({"tool_name": "Bash", "cwd": str(root),
                                    "tool_input": {"command": denied_l1}})
    cases.append(("L1 classifier denial explains cd && and canonical shape",
                  isinstance(note, str) and "cd" in note and "&&" in note and "Canonical shape" in note))

    rejected = [
        ("-A spend grant", "-A", "-A"), ("-W peak-pricing grant", "-W", "-W"),
        ("-U unsuspend grant", "-U", "-U"), ("-X detach grant", "-X", "-X"),
        ("-p permission mode", "-p bypassPermissions", "-p"),
        ("-G author", "-G author", "-G"),
        ("write-capable -t tools", "-t Edit,Write,Bash", "-t"),
        ("unknown flag", "-Q", "-Q"), ("missing flag value", "-m", "missing"),
        ("operand after options", "-f .claude/reference/prompt.md extra", "operand"),
        ("parent traversal", "-f ../x", ".."),
        ("peer worktree file", "-f .claude/worktrees/peer/p.md", "in-project"),
        ("peer worktree directory -d", "-d .claude/worktrees/peer", "in-project"),
        ("peer worktree directory -a", "-a .claude/worktrees/peer", "in-project"),
        ("peer worktree output parent", "-R .claude/worktrees/peer/sub/out.json", "in-project"),
    ]
    for label, suffix, reason_text in rejected:
        passed, reason = approving_gates.sidecar_launch_shape(
            "bash .claude/scripts/codex_proxy_sidecar.sh " + suffix, str(root))
        cases.append((label + " falls through with its named reason",
                      not passed and reason_text.lower() in reason.lower()))

    outside = root.parent / (root.name + "_outside")
    outside.mkdir()
    outside_match = outside / "match.md"
    outside_match.write_text("match\\n", encoding="utf-8")
    outside_ok, outside_reason = approving_gates.sidecar_launch_shape(
        "bash .claude/scripts/codex_proxy_sidecar.sh -a " + str(outside).replace("\\", "/"), str(root))
    cases.append(("-a outside project names the directory grant", not outside_ok and "outside" in outside_reason))
    untracked = scripts / "untracked_sidecar.sh"
    untracked.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    untracked_ok, _ = approving_gates.sidecar_launch_shape(
        "bash .claude/scripts/untracked_sidecar.sh", str(root))
    cases.append(("untracked launcher falls through", not untracked_ok))
    linked = scripts / "linked_sidecar.sh"
    try:
        linked.symlink_to(launcher)
        linked_is_link = True
    except OSError:
        linked.write_text(launcher.read_text(encoding="utf-8"), encoding="utf-8")
        linked_is_link = False
    if not linked_is_link:
        from unittest.mock import patch
        real_is_symlink = Path.is_symlink
        with patch.object(Path, "is_symlink",
                          lambda path: path.name == "linked_sidecar.sh" or real_is_symlink(path)):
            linked_ok, _ = approving_gates.sidecar_launch_shape(
                "bash .claude/scripts/linked_sidecar.sh", str(root))
    else:
        subprocess.run(["git", "add", ".claude/scripts/linked_sidecar.sh"], cwd=root, check=True)
        linked_ok, _ = approving_gates.sidecar_launch_shape(
            "bash .claude/scripts/linked_sidecar.sh", str(root))
    cases.append(("symlinked launcher falls through", not linked_ok))
    from unittest.mock import patch
    dangling_record = ".claude/reference/dangling.json"
    real_is_symlink = Path.is_symlink
    with patch.object(Path, "is_symlink",
                      lambda path: path.name == "dangling.json" or real_is_symlink(path)):
        dangling_ok, _ = approving_gates.sidecar_launch_shape(
            "bash .claude/scripts/codex_proxy_sidecar.sh -R " + dangling_record, str(root))
    cases.append(("dangling symlink output path falls through", not dangling_ok))

    carriers = [
        ("command substitution", "$(echo x)"), ("bare backticks", "`echo x`"),
        ("backticks in double quotes", '"`echo x`"'),
        ("substitution in double quotes", '"$(echo x)"'),
        ("variable expansion", '"$VAR"'), ("assignment prefix", "CCP_BIN=x "),
        ("assignment reassignment", "P=x; "), ("sudo prefix", "sudo "),
        ("env prefix", "env X=1 "), ("xargs prefix", "xargs "),
        ("background operator", " & echo x"), ("semicolon", "; echo x"),
        ("and list", " && echo x"), ("or list", " || echo x"),
        ("pipe", " | head"), ("newline", "\necho x"),
        ("stdout redirection", " > cap.json"), ("stderr redirection", " 2>&1"),
    ]
    for label, carrier in carriers:
        passed, reason = approving_gates.sidecar_launch_shape(good + carrier, str(root))
        cases.append(("shell carrier: " + label, not passed and bool(reason)))

    for label, command, phrase in (
        ("cd chain names prefix", "cd /tmp && " + good, "cd"),
        ("assignment names variable", "CCP_BIN=x " + good, "CCP_BIN"),
        ("redirection names redirect", good + " > cap", "redirection"),
        ("pipe names pipe", good + " | head", "pipe"),
    ):
        argv, why = approving_gates.simple_argv(command)
        cases.append((label, argv is None and phrase.lower() in why.lower()))

    digest_command = "python3 .claude/tools/session_digest.py --session abc123 --brief"
    digest_rc, digest_hso = run_gate(digest_command)
    cases.append(("canonical session-digest command gets allow through main",
                  digest_rc == 0 and digest_hso.get("permissionDecision") == "allow"
                  and "session-digest" in digest_hso.get("permissionDecisionReason", "")))
    rc, hso = run_gate("python3 .claude/tools/session_digest.py --digest-file "
                       ".claude/tools/digest.json --brief")
    cases.append(("digest path under project root is approved", rc == 0
                  and hso.get("permissionDecision") == "allow"))
    for label, command, tool in (
        ("unknown digest option gets no allow", digest_command + " --future-option", "Bash"),
        ("bare digest script gets no allow", "python3 .claude/tools/session_digest.py", "Bash"),
        ("unknown workflow kind gets no allow",
         "python3 .claude/tools/session_digest.py --workflow-kind author "
         "--workflow-dir .claude/tools --workflow-full", "Bash"),
        ("invalid digest page-size gets no allow",
         "python3 .claude/tools/session_digest.py --page-size many --brief", "Bash"),
        ("digest output path outside roots gets no allow",
         "python3 .claude/tools/session_digest.py --digest-file "
         + str(outside / "digest.json").replace("\\", "/"), "Bash"),
        ("digest project-dir outside roots gets no allow",
         "python3 .claude/tools/session_digest.py --project-dir "
         + str(outside).replace("\\", "/") + " --session abc123 --brief", "Bash"),
        ("digest match-file outside roots gets no allow",
         "python3 .claude/tools/session_digest.py --match-file "
         + str(outside_match).replace("\\", "/") + " --match x", "Bash"),
        ("digest workflow-dir outside roots gets no allow",
         "python3 .claude/tools/session_digest.py --workflow-dir "
         + str(outside).replace("\\", "/") + " --workflow-manifest", "Bash"),
        ("python -c is not the session-digest contract",
         "python3 -c 'print(1)'", "Bash"),
        ("PowerShell invocation gets no allow", digest_command, "PowerShell"),
    ):
        rc, hso = run_gate(command, tool)
        cases.append((label, rc == 0 and hso.get("permissionDecision") is None))
    copied = tools / "session_digest_copy.py"
    copied.write_text(digest_script.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "add", ".claude/tools/session_digest_copy.py"], cwd=root, check=True)
    rc, hso = run_gate("python3 .claude/tools/session_digest_copy.py --session abc123 --brief")
    cases.append(("a tracked copy at another path gets no allow", rc == 0 and hso.get("permissionDecision") is None))
    peer_project = peer / "project"
    peer_project.mkdir(parents=True)
    rc, hso = run_gate("python3 .claude/tools/session_digest.py --project-dir "
                       ".claude/worktrees/peer/project --session abc123 --brief")
    cases.append(("digest project-dir inside a peer worktree gets no allow",
                  rc == 0 and hso.get("permissionDecision") is None))
    pipeline = digest_command + " | head -5"
    rc, hso = run_gate(pipeline)
    note = approving_gates.explain({"tool_name": "Bash", "cwd": str(root),
                                    "tool_input": {"command": pipeline}})
    cases.append(("piped digest gets no allow and explain names the pipe",
                  rc == 0 and hso.get("permissionDecision") is None and "pipe" in (note or "").lower()))
    # The shell runs a relative script from its own working directory, so a gate judges the command
    # only when that directory is the session's project root. A second git repo that tracks its own
    # `.claude/tools/session_digest.py` is the exploit shape: its code must never ride the approval.
    other = root.parent / (root.name + "_other_repo")
    (other / ".claude" / "tools").mkdir(parents=True)
    (other / ".claude" / "tools" / "session_digest.py").write_text("import os\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    subprocess.run(["git", "add", ".claude/tools/session_digest.py"], cwd=other, check=True)
    rc, hso = run_gate(digest_command, cwd=other)
    cases.append(("a digest command run from another repo's cwd gets no allow",
                  rc == 0 and hso.get("permissionDecision") is None))
    rc, hso = run_gate(digest_command, cwd=root / ".claude" / "tools")
    cases.append(("a digest command run from a project subdirectory gets no allow",
                  rc == 0 and hso.get("permissionDecision") is None))
    rc, hso = run_gate(digest_command, project="unset")
    cases.append(("no CLAUDE_PROJECT_DIR means no allow",
                  rc == 0 and hso.get("permissionDecision") is None))
    env_before = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(root)
    try:
        cwd_note = approving_gates.explain({"tool_name": "Bash", "cwd": str(other),
                                            "tool_input": {"command": digest_command}})
    finally:
        if env_before is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = env_before
    cases.append(("explain names the working-directory reason when cwd is not the project root",
                  "working directory" in (cwd_note or "")))

    (root / ".claude" / "scratch").mkdir()
    (root / ".claude" / "scratch" / "msg.txt").write_text("feat: x\n", encoding="utf-8")
    (root / "m.txt").write_text("feat: x\n", encoding="utf-8")
    msg = "-F .claude/scratch/msg.txt"
    root_posix = root.resolve().as_posix()
    for label, command in (
        ("a scratch-message commit of the index is approved", "git commit %s" % msg),
        ("a path-scoped commit with a scratch message is approved",
         "git commit %s -- .claude/hooks/a.py .claude/tests/b.py" % msg),
        ("`git -C <project root>` commit is approved",
         "git -C %s commit %s -- a.py" % (root_posix, msg)),
        ("a quiet commit is approved", "git commit -q %s" % msg),
    ):
        rc, hso = run_gate(command)
        cases.append((label, rc == 0 and hso.get("permissionDecision") == "allow"
                      and "git-commit" in hso.get("permissionDecisionReason", "")))
    for label, command in (
        ("a commit with `--` and no paths falls through", "git commit %s --" % msg),
        ("`-a` falls through", "git commit -a %s" % msg),
        ("`--amend` falls through", "git commit --amend %s" % msg),
        ("`--no-verify` falls through", "git commit --no-verify %s" % msg),
        ("`-n` falls through", "git commit -n %s" % msg),
        ("`-m` falls through", "git commit -m msg -- a.py"),
        ("`--author` falls through", "git commit --author=x %s" % msg),
        ("`git -c` config falls through", "git -c core.hooksPath=x commit %s" % msg),
        ("`git -C` another repo falls through", "git -C %s commit %s" % (
            (root / "output").as_posix(), msg)),
        ("a message file outside scratch falls through", "git commit -F m.txt"),
        ("a missing message file falls through", "git commit -F .claude/scratch/none.txt"),
        ("a message file in a peer worktree falls through",
         "git commit -F .claude/worktrees/peer/p.md"),
        ("a `..` path falls through", "git commit %s -- ../a.py" % msg),
        ("an absolute path outside the project falls through", "git commit %s -- /etc/x" % msg),
        ("a peer-worktree path falls through", "git commit %s -- .claude/worktrees/peer/p.md" % msg),
        ("pathspec magic falls through", "git commit %s -- :/" % msg),
        ("a bare pathspec without `--` falls through", "git commit %s a.py" % msg),
        ("a `$(…)` path list falls through",
         "git commit %s -- $(git diff --cached --name-only)" % msg),
        ("`GIT_INDEX_FILE=` prefix falls through", "GIT_INDEX_FILE=x git commit %s" % msg),
        ("`git push` falls through", "git push origin main"),
    ):
        rc, hso = run_gate(command)
        cases.append((label, rc == 0 and not hso))
    rc, hso = run_gate("git commit %s" % msg, cwd=root / "output")
    cases.append(("a commit from another cwd falls through", rc == 0 and not hso))
    env_before = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(root)
    try:
        commit_note = approving_gates.explain({
            "tool_name": "Bash", "cwd": str(root),
            "tool_input": {"command": "git commit %s -- $(git diff --cached --name-only) 2>&1 | tail -3"
                           % msg}})
    finally:
        if env_before is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = env_before
    cases.append(("explain names the git-commit gate for the 09-23 denied commit",
                  "git-commit gate" in (commit_note or "")))

    base = "bash .claude/scripts/codex_proxy_sidecar.sh -G survey -f .claude/reference/prompt.md"
    (root / ".claude" / "scratch" / "used.record.json").write_text("{}\n", encoding="utf-8")
    for letter in "RPL":
        ok, _ = approving_gates.sidecar_launch_shape(
            "%s -%s .claude/scripts/codex_proxy_sidecar.sh" % (base, letter), str(root))
        cases.append(("`-%s` onto a tracked file is refused" % letter, not ok))
        ok, _ = approving_gates.sidecar_launch_shape(
            "%s -%s output/x.json" % (base, letter), str(root))
        cases.append(("`-%s` outside `.claude/scratch/` is refused" % letter, not ok))
        ok, _ = approving_gates.sidecar_launch_shape(
            "%s -%s .claude/scratch/new.json" % (base, letter), str(root))
        cases.append(("`-%s` to a new scratch file is approved" % letter, ok))
    ok, _ = approving_gates.sidecar_launch_shape(
        "%s -R .claude/scratch/used.record.json" % base, str(root))
    cases.append(("`-R` onto an existing record is refused", not ok))
    home_projects = Path.home() / ".claude" / "projects"
    real = next(iter(sorted(home_projects.glob("*/*.jsonl"))), None) if home_projects.is_dir() else None
    if real is not None:
        rel_home = real.relative_to(Path.home()).as_posix()
        ok, _ = approving_gates._digest_shape(
            ["python3", ".claude/tools/session_digest.py", "--match-file", "~/" + rel_home,
             "--match", "x"], str(root))
        cases.append(("a literal `~/` digest path the tool never expands is refused", not ok))
        ok, _ = approving_gates._digest_shape(
            ["python3", ".claude/tools/session_digest.py", "--match-file", real.as_posix(),
             "--match", "x"], str(root))
        cases.append(("an absolute `~/.claude/projects` digest path is approved", ok))
    cases.append(("`git log` is not claimed by the git-commit gate",
                  approving_gates.owning_contract(["git", "log", "--grep", "commit"], str(root)) is None))

    parser_source = (HERE.parent / "tools" / "session_digest.py").read_text(encoding="utf-8")
    import re
    parser_options = set(re.findall(r'add_argument\(["\'](--[a-z0-9-]+)', parser_source))
    cases.append(("reviewed digest options exactly match the parser",
                  parser_options == set(approving_gates._DIGEST_OPTIONS)))

    failures = [label for label, passed in cases if not passed]
    for label, passed in cases:
        print("%-4s %s" % ("ok" if passed else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
