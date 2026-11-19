#!/usr/bin/env python3
"""`_regenerable_clone.is_regenerable_clone` passes only a clean, fully pushed standalone clone under
`.claude/scratch/`, and fails closed on every other shape. `pattern_enforcer.py` lets a recursive
delete through when every literal target passes it.

Owner direction 2026-09-14: a stray probe clone left in scratch is bloat and must be removable. The
recursive-delete guard keeps scratch blocked because it holds evidence, so the discriminator is
regenerability proven from the clone itself.

Fixture: a bare remote plus clones under `.claude/scratch/_rc_fix_<pid>/`, since
the rule only admits scratch paths. Several sessions run this battery concurrently:
- Startup removes a same-prefix directory only when it is older than STALE_AFTER_S, so another run's
  live fixture is never touched.
- Teardown clears git's read-only object files and asserts that this run's own fixture is gone.
"""
import glob
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
PROJECT = os.path.dirname(CLAUDE)
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))

# These cases judge the verdict logic, not the hook's runtime budget: at the hook's own git timeout a loaded
# machine fails every "may delete" case closed. The environment reaches the in-process module and the hook
# subprocess the channel cases start.
os.environ["REGENERABLE_CLONE_GIT_TIMEOUT_S"] = "120"
import _regenerable_clone as rc  # noqa: E402

PREFIX = os.path.join(CLAUDE, "scratch", "_rc_fix_")
FIX = PREFIX + str(os.getpid())
STALE_AFTER_S = 1800  # a live run takes well under a minute
PEER_SID = "5eed5eed-aaaa-4bbb-8ccc-0000000000ff"  # a session id names that session's own output folder
# The fixture sits under this checkout's scratch, so a deeply nested checkout (a publish worktree)
# pushes its submodule objects past Windows MAX_PATH. Every git this proof starts, the hook's
# included, runs with long paths on.
# `git worktree add` also refuses a git dir longer than PATH_MAX - 40 ("'$GIT_DIR' too big"), which long paths
# do not lift, so the prefix and the multi-worktree fixture names stay short.
os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.longpaths", GIT_CONFIG_VALUE_0="true")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}


def force_rmtree(path):
    """Remove a fixture tree, clearing the read-only bit git sets on object files.

    Windows keeps a killed child's file handles a moment after the child exits, so a removal it refuses
    (WinError 32/5 on a `.git` file) is retried for up to five seconds: the fixture is gone either way,
    and the verdict must not turn on how fast the OS releases a handle.
    """
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if not os.path.isdir(path):
        return
    for attempt in range(50):
        try:
            shutil.rmtree(path, onerror=on_error)
            return
        except OSError:
            if attempt == 49 or not os.path.isdir(path):
                raise
            time.sleep(0.1)


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def fresh_clone(name, remote):
    path = os.path.join(FIX, name)
    git("clone", "-q", remote, path)
    return path


def rel(path):
    return os.path.relpath(path, PROJECT).replace("\\", "/")


def msys(path):
    """Git Bash's `/c/...` form of a Windows absolute path; the Bash tool's usual absolute path."""
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")


def channel(cmd, project=PROJECT, transcript=None, env=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "regen-clone-proof",
               "hook_event_name": "PreToolUse", "cwd": project}
    if transcript:
        payload["transcript_path"] = transcript
    proc = subprocess.run([sys.executable, os.path.join(CLAUDE, "hooks", "pre_bash_dispatch.py")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_PROJECT_DIR": project, **(env or {})})
    if proc.returncode not in (0, 2) or "Traceback" in proc.stderr:
        return "CRASH", proc
    denied = proc.returncode == 2 or '"deny"' in proc.stdout or "BLOCKED" in (proc.stdout + proc.stderr)
    return ("deny" if denied else "allow"), proc


fails = 0
total = 0


def expect(label, token, want, cwd=PROJECT):
    global fails, total
    total += 1
    got = rc.is_regenerable_clone(token, cwd, PROJECT)
    if got != want:
        fails += 1
        print(f"  FAIL {label}: expected {want}, got {got} ({token})")
    else:
        print(f"  ok   {label}: {'pass' if want else 'block'}")


def expect_clone(label, token, want, project, reason=None):
    """`is_regenerable_clone` against a fixture project; a blocked verdict must name `reason`."""
    global fails, total
    total += 1
    why = []
    got = rc.is_regenerable_clone(token, project, project, why)
    ok = got == want and (want or (bool(why) and (reason is None or any(reason in r for r in why))))
    if not ok:
        fails += 1
        print(f"  FAIL clone {label}: expected {want}, got {got} {why}")
    else:
        print(f"  ok   clone {label}: {'pass' if want else 'block'}")


def expect_channel(label, cmd, want):
    global fails, total
    total += 1
    got, proc = channel(cmd)
    if got != want:
        fails += 1
        print(f"  FAIL channel {label}: expected {want}, got {got} (exit {proc.returncode}) "
              f"{(proc.stdout + proc.stderr)[:240]!r}")
    else:
        print(f"  ok   channel {label}: {want}")


def expect_wt(label, token, want, project, cwd=None, **kwargs):
    global fails, total
    total += 1
    reasons = []
    try:
        got = rc.is_retired_worktree(token, cwd or project, project, reasons=reasons, **kwargs)
    except TypeError as exc:
        got = "TypeError: %s" % exc
    if got != want or (not want and not reasons) or (want and reasons):
        fails += 1
        print(f"  FAIL worktree {label}: expected {want}, got {got} ({token}) {reasons}")
    else:
        print(f"  ok   worktree {label}: {'pass' if want else 'block'}")


def expect_wt_channel(label, cmd, want, project, transcript=None, env=None):
    global fails, total
    total += 1
    got, proc = channel(cmd, project, transcript, env)
    if got != want:
        fails += 1
        print(f"  FAIL worktree channel {label}: expected {want}, got {got} (exit {proc.returncode}) "
              f"{(proc.stdout + proc.stderr)[:240]!r}")
    else:
        print(f"  ok   worktree channel {label}: {want}")


def expect_retire_check(label, argv, want_code, want_lines, project):
    """`--retire-check` prints `<token>: retired` or `<token>: blocked: <reasons>` per worktree; a `want_lines`
    entry ending in `: retired` must match exactly, any other is a prefix of its blocked line."""
    global fails, total
    import io
    from contextlib import redirect_stdout
    from unittest.mock import patch
    total += 1
    buf = io.StringIO()
    with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": project}), redirect_stdout(buf):
        code = rc.main(["--retire-check"] + argv)
    lines = buf.getvalue().splitlines()
    ok = code == want_code and len(lines) == len(want_lines) and all(
        got == want if want.endswith(": retired") else got.startswith(want)
        for got, want in zip(lines, want_lines))
    if not ok:
        fails += 1
        print(f"  FAIL retire-check {label}: expected exit {want_code} {want_lines}, got exit {code} {lines}")
    else:
        print(f"  ok   retire-check {label}: exit {code}")


def created_transcript(path, *worktrees):
    """A transcript in which this session ran `git worktree add` for each path, with timestamps around the
    moment the fixture made it, so `session_created_worktree` proves the creation from the real rows."""
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    start, end = time.time() - 600, time.time() + 600
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for i, wt in enumerate(worktrees):
            fh.write(json.dumps({
                "type": "assistant", "timestamp": iso(start), "cwd": FIX,
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "w%d" % i, "name": "Bash",
                            "input": {"command": "git worktree add " + wt.replace("\\", "/") + " HEAD"}}]}}) + "\n")
            fh.write(json.dumps({
                "type": "user", "timestamp": iso(end),
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w%d" % i,
                            "content": "Preparing worktree"}]}}) + "\n")
    return path


def backgrounded_worktree_cases():
    """A `git worktree add` run with run_in_background: its tool_result is the background notice, so ownership
    waits for the task's `<task-notification>` (the call's end) and reads its output file or exit code."""
    from datetime import datetime
    repo = os.path.join(FIX, "bg_repo")
    git("init", "-q", "-b", "main", repo)
    write(os.path.join(repo, "README.md"), "bg\n")
    git("add", "README.md", cwd=repo)
    git("commit", "-qm", "init", cwd=repo)
    # The background command writes the worktree after its notice row (2 s) and before its notification (5 s).
    START, NOTICE, BORN, DONE = ("2026-10-24T09:00:00.000Z", "2026-10-24T09:00:02.000Z",
                                 "2026-10-24T09:00:03.000Z", "2026-10-24T09:00:05.000Z")
    LATE = "2026-10-24T09:01:00.000Z"
    VISIBLE = 'cd "{repo}" && git worktree add -q -b scratch/{name} .claude/worktrees/{name} HEAD && W=.claude/worktrees/{name} && git -C $W status --short'
    PLAIN = 'cd "{repo}" && git worktree add -q -b scratch/{name} .claude/worktrees/{name} HEAD'
    OK_OUTPUT = "Preparing worktree (new branch 'scratch/{name}')\nHEAD is now at 1234abc init\n"

    def epoch(text):
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()

    def notice(task, out):
        # Cut from the live tool_result (session 0002a8e7, 2026-09-28T07:01:12Z).
        return ("Command running in background with ID: %s. Output is being written to: %s. You will be notified "
                "when it completes. To check interim output, use Read on that file path.\nSession cwd remains %s; "
                "directory changes made by the backgrounded command do not apply to subsequent commands."
                % (task, out, repo))

    def run(name, command, output, notify=True, status="completed", exit_code=0, born=BORN, out_file=True):
        task = "bg" + name
        out = os.path.join(FIX, "bg_out_" + name, "tasks", task + ".output")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if out_file:
            write(out, output.replace("{name}", name))
        wt = os.path.join(repo, ".claude", "worktrees", name)
        git("worktree", "add", "-q", "-b", "scratch/" + name, wt, "HEAD", cwd=repo)
        os.utime(os.path.join(wt, ".git"), (epoch(born), epoch(born)))
        rows = [
            {"type": "assistant", "isSidechain": False, "timestamp": START, "cwd": repo,
             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_" + name, "name": "Bash",
                                                           "input": {"command": command.format(repo=repo.replace("\\", "/"), name=name)}}]}},
            {"type": "user", "isSidechain": False, "timestamp": NOTICE,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_" + name,
                                                      "is_error": False, "content": notice(task, out)}]},
             "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False}},
        ]
        if notify:
            # Cut from the live queue-operation enqueue row (session 0002a8e7, 2026-09-28T07:01:44Z).
            rows.append({"type": "queue-operation", "operation": "enqueue", "timestamp": DONE, "sessionId": "bg-proof",
                         "content": "<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>toolu_%s</tool-use-id>\n"
                                    "<output-file>%s</output-file>\n<status>%s</status>\n<summary>Background command "
                                    "\\\"add\\\" completed (exit code %d)</summary>\n</task-notification>"
                                    % (task, name, out, status, exit_code)})
        transcript = os.path.join(FIX, "bg_%s.jsonl" % name)
        write(transcript, "".join(json.dumps(r) + "\n" for r in rows))
        return rc.session_created_worktree(wt, transcript)

    global fails, total
    for label, want, got in [
        ("backgrounded visible add, completed notice, clean output is owned", True,
         run("a", VISIBLE, OK_OUTPUT)),
        ("backgrounded visible add with no notice is not owned", False,
         run("b", VISIBLE, OK_OUTPUT, notify=False)),
        ("backgrounded visible add whose output holds fatal: is not owned", False,
         run("c", VISIBLE, "fatal: a branch named 'scratch/c' already exists\n")),
        ("backgrounded visible add born after its notice is not owned", False,
         run("d", VISIBLE, OK_OUTPUT, born=LATE)),
        ("backgrounded plain add with notice exit code 0 is owned", True,
         run("e", PLAIN, OK_OUTPUT, exit_code=0)),
        ("backgrounded plain add with notice exit code 1 is not owned", False,
         run("f", PLAIN, OK_OUTPUT, exit_code=1)),
        ("backgrounded add whose notice is not completed is not owned", False,
         run("g", VISIBLE, OK_OUTPUT, status="killed")),
        ("backgrounded visible add with an unreadable output file is not owned", False,
         run("h", VISIBLE, OK_OUTPUT, out_file=False)),
    ]:
        total += 1
        if got != want:
            fails += 1
            print(f"  FAIL {label}: expected {want}, got {got}")
        else:
            print(f"  ok   {label}: {'owned' if want else 'not owned'}")


def peer_login(name, *rows):
    """A login directory whose `sessions/<pid>.json` registry holds one live session per `(name, sessionId, cwd)`
    row: the runtime's local registry `peer_message_guard.live_sessions` reads, reached through
    CLAUDE_CONFIG_DIR. Returns the environment that points the hook at it."""
    login = os.path.join(FIX, "login_" + name)
    os.makedirs(os.path.join(login, "sessions"))
    for pid, (peer, sid, cwd) in enumerate(rows, 4000):
        write(os.path.join(login, "sessions", "%d.json" % pid), json.dumps(
            {"pid": pid, "sessionId": sid, "name": peer, "status": "idle", "kind": "interactive", "cwd": cwd}))
    return {"CLAUDE_CONFIG_DIR": login}


def expect_wt_deny_names(label, cmd, needle, project, transcript=None, env=None):
    global fails, total
    total += 1
    verdict, proc = channel(cmd, project, transcript, env)
    if verdict != "deny" or needle not in proc.stdout + proc.stderr:
        fails += 1
        print(f"  FAIL worktree {label}: {verdict} {(proc.stdout + proc.stderr)[-240:]!r}")
    else:
        print(f"  ok   worktree {label}")


def landed_worktree_cases(proj_remote):
    """A finished job worktree: its commits sit on the project's local default branch (cherry-picked or
    squashed, never pushed), its scratch holds only the job's own output, and a tracked file may be missing
    from its checkout. Each is no loss; a peer session's folder, a commit no default branch holds, a staged edit
    and a nested repository still block. Own clone, so the project's other cases keep their pushed main."""
    lp = fresh_clone("lproj", proj_remote)
    home = os.path.join(lp, ".claude", "worktrees")
    os.makedirs(home)

    def job(name, *edits):
        """A worktree on `scratch/<name>` with one commit per `(file, text)` edit."""
        path = os.path.join(home, name)
        git("worktree", "add", "-q", "-b", "scratch/" + name, path, cwd=lp)
        for i, (fname, text) in enumerate(edits):
            write(os.path.join(path, fname), text)
            git("add", fname, cwd=path)
            git("commit", "-q", "-m", "%s %d" % (name, i), cwd=path)
        return path

    def sha(cwd):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, env=ENV, capture_output=True, text=True,
                              check=True).stdout.strip()

    picked = job("picked", ("p.txt", "picked\n"))
    git("cherry-pick", sha(picked), cwd=lp)
    expect_wt("commit cherry-picked onto the local default branch, never pushed", picked, True, lp)

    squashed = job("squashed", ("q1.txt", "one\n"), ("q2.txt", "two\n"))
    git("merge", "--squash", "scratch/squashed", cwd=lp)
    git("commit", "-q", "-m", "squash of scratch/squashed", cwd=lp)
    expect_wt("commits squashed onto the local default branch, never pushed", squashed, True, lp)

    ahead = job("ahead", ("z.txt", "only here\n"))
    expect_wt("commit no default branch holds", ahead, False, lp)

    drifted = job("drifted", ("a.txt", "branch edit\n"))
    git("cherry-pick", sha(drifted), cwd=lp)
    write(os.path.join(lp, "a.txt"), "main edited it again\n")
    git("commit", "-q", "-a", "-m", "main moves on", cwd=lp)
    expect_wt("cherry-picked commit still held by patch after main edits the same file", drifted, True, lp)
    sq2 = job("sq2", ("w.txt", "1\n"), ("w.txt", "2\n"))
    git("merge", "--squash", "scratch/sq2", cwd=lp)
    git("commit", "-q", "-m", "squash of scratch/sq2", cwd=lp)
    write(os.path.join(lp, "w.txt"), "3\n")
    git("commit", "-q", "-a", "-m", "main rewrites w.txt", cwd=lp)
    expect_wt("squashed commits whose file main has since rewritten: the content stays in main's history",
              sq2, True, lp)

    # The default branch can hold a branch's work in a form `git cherry`, a patch-id and a tip diff all
    # miss: a commit re-applied onto a moved line (the same edit, a different base), a squash main later
    # built on, and a cherry-pick a later main commit extended. Each is no loss, and a near miss still blocks.
    write(os.path.join(lp, "ctx.txt"), "one\n")
    git("add", "ctx.txt", cwd=lp)
    git("commit", "-q", "-m", "ctx base", cwd=lp)
    reapply = os.path.join(home, "reapply")
    git("worktree", "add", "-q", "-b", "scratch/reapply", reapply, cwd=lp)
    write(os.path.join(lp, "ctx.txt"), "one\nmain\n")
    git("commit", "-q", "-a", "-m", "main owns the next line", cwd=lp)
    write(os.path.join(reapply, "ctx.txt"), "one\nshared fix\n")
    git("commit", "-q", "-a", "-m", "branch adds the fix", cwd=reapply)
    write(os.path.join(lp, "ctx.txt"), "one\nmain\nshared fix\n")
    git("commit", "-q", "-a", "-m", "main adds the same fix on its line", cwd=lp)
    expect_wt("commit re-applied onto a moved line (patch id differs, the edit is the same)", reapply, True, lp)
    partial = os.path.join(home, "reapply_partial")
    git("worktree", "add", "-q", "-b", "scratch/reapply_partial", partial, cwd=lp)
    write(os.path.join(partial, "ctx.txt"), "one\nmain\nshared fix\nan extra line\n")
    git("commit", "-q", "-a", "-m", "branch adds an extra line", cwd=partial)
    expect_wt("commit whose edit main made only in part", partial, False, lp)

    held = os.path.join(home, "held_content")
    git("worktree", "add", "-q", "-b", "scratch/held_content", held, cwd=lp)
    write(os.path.join(held, "a.txt"), "v2\n")
    git("commit", "-q", "-a", "-m", "branch a v2", cwd=held)
    write(os.path.join(held, "z.txt"), "z\n")
    git("add", "z.txt", cwd=held)
    git("commit", "-q", "-m", "branch z", cwd=held)
    write(os.path.join(lp, "a.txt"), "v2\n")
    write(os.path.join(lp, "z.txt"), "z\n")
    git("add", "a.txt", "z.txt", cwd=lp)
    git("commit", "-q", "-m", "land a and z in one commit", cwd=lp)
    write(os.path.join(lp, "a.txt"), "v3\n")
    git("commit", "-q", "-a", "-m", "main moves a past it", cwd=lp)
    expect_wt("branch content landed in one commit the default branch then moved past", held, True, lp)

    write(os.path.join(lp, "lock.txt"), "A\nB\nC\nD\nE\n")
    git("add", "lock.txt", cwd=lp)
    git("commit", "-q", "-m", "lock base", cwd=lp)
    merged_elsewhere = os.path.join(home, "merged_elsewhere")
    git("worktree", "add", "-q", "-b", "scratch/merged_elsewhere", merged_elsewhere, cwd=lp)
    write(os.path.join(merged_elsewhere, "lock.txt"), "A\nB\nROW\nC\nD\nE\n")
    git("commit", "-q", "-a", "-m", "branch adds a row", cwd=merged_elsewhere)
    write(os.path.join(lp, "lock.txt"), "A\nB\nROW\nC\nD\nE2\n")
    git("commit", "-q", "-a", "-m", "main adds the row and edits E", cwd=lp)
    expect_wt("branch whose row main holds beside its own edit (merging the branch adds nothing)",
              merged_elsewhere, True, lp)

    side = job("side", ("s.txt", "side\n"))
    git("branch", "elsewhere", sha(side), cwd=lp)
    expect_wt("commit held only by another local branch", side, False, lp)

    gone = job("gone")
    os.remove(os.path.join(gone, "a.txt"))
    expect_wt("tracked file missing from the checkout, nothing added", gone, True, lp)
    gone_staged = job("gone_staged")
    write(os.path.join(gone_staged, "a.txt"), "staged edit\n")
    git("add", "a.txt", cwd=gone_staged)
    os.remove(os.path.join(gone_staged, "a.txt"))
    expect_wt("staged edit of a file since deleted from the checkout", gone_staged, False, lp)
    gone_new = job("gone_new")
    write(os.path.join(gone_new, "extra.txt"), "x\n")
    os.remove(os.path.join(gone_new, "a.txt"))
    expect_wt("tracked file missing and an untracked file added", gone_new, False, lp)

    scratch = job("scratch")
    os.makedirs(os.path.join(scratch, ".claude", "scratch", "job_x", "run"))
    write(os.path.join(scratch, ".claude", "scratch", "job_x", "run", "args.json"), "{}\n")
    write(os.path.join(scratch, ".claude", "scratch", "notes.md"), "launcher notes\n")
    expect_wt("ignored scratch holding only the job's own output", scratch, True, lp)
    peer = job("peer")
    os.makedirs(os.path.join(peer, ".claude", "scratch", PEER_SID))
    write(os.path.join(peer, ".claude", "scratch", PEER_SID, "out.txt"), "peer\n")
    expect_wt("ignored scratch holding another session's output folder", peer, False, lp)
    # A file a session id merely names (a guard's per-session state, a copied fixture) is no output folder.
    peer_file = job("peer_file")
    os.makedirs(os.path.join(peer_file, ".claude", "scratch", "gate_positions"))
    write(os.path.join(peer_file, ".claude", "scratch", "gate_positions", PEER_SID + ".json"), "{}\n")
    expect_wt("ignored scratch holding a file named by a session id", peer_file, True, lp)
    # `.claude/tmp` is where a proof plants a fixture under a name unique to the run: judged like scratch.
    tmp_log = job("tmp_log")
    os.makedirs(os.path.join(tmp_log, ".claude", "tmp", "run_x"))
    write(os.path.join(tmp_log, ".claude", "tmp", "run_x", "out.log"), "log\n")
    expect_wt("ignored .claude/tmp holding only a log", tmp_log, True, lp)
    tmp_unborn = job("tmp_unborn")
    unborn = os.path.join(tmp_unborn, ".claude", "tmp", "run_x", "probe")
    os.makedirs(unborn)
    git("init", "-q", cwd=unborn)
    expect_wt("ignored .claude/tmp holding a repository with no commit and no file", tmp_unborn, True, lp)
    tmp_work = job("tmp_work")
    probe = os.path.join(tmp_work, ".claude", "tmp", "run_x", "probe")
    os.makedirs(probe)
    git("init", "-q", cwd=probe)
    git("commit", "-q", "--allow-empty", "-m", "only here", cwd=probe)
    expect_wt("ignored .claude/tmp holding a repository with an unpushed commit", tmp_work, False, lp)
    tmp_file = job("tmp_file")
    unborn_file = os.path.join(tmp_file, ".claude", "tmp", "run_x", "probe")
    os.makedirs(unborn_file)
    git("init", "-q", cwd=unborn_file)
    write(os.path.join(unborn_file, "notes.txt"), "never committed\n")
    expect_wt("ignored .claude/tmp holding a repository with no commit and an untracked file", tmp_file, False, lp)
    # A folder a proof planted (`is_proof_plant`) holds that proof's own fixtures: a session-id folder loses
    # nothing. One holding a git repository at any depth is judged like any other `.claude/tmp` folder.
    plant_work = job("plant_work")
    plant_dir = os.path.join(plant_work, ".claude", "tmp", "pe_absent_delete_dxf68feq")
    os.makedirs(os.path.join(plant_dir, ".claude", "scratch", PEER_SID))
    write(os.path.join(plant_dir, ".claude", "scratch", PEER_SID, "o.txt"), "x\n")
    expect_wt("ignored .claude/tmp holding a proof's planted session-id folder", plant_work, True, lp)
    plant_repo = job("plant_repo")
    plant_repo_probe = os.path.join(plant_repo, ".claude", "tmp", "pe_absent_delete_dxf68feq", "repo")
    os.makedirs(plant_repo_probe)
    git("init", "-q", cwd=plant_repo_probe)
    git("commit", "-q", "--allow-empty", "-m", "only here", cwd=plant_repo_probe)
    expect_wt("ignored .claude/tmp holding a proof's planted folder with a repository with an unpushed commit",
              plant_repo, False, lp)
    plant_beside = job("plant_beside")
    shutil.copytree(plant_dir, os.path.join(plant_beside, ".claude", "tmp", "pe_absent_delete_dxf68feq"))
    real_probe = os.path.join(plant_beside, ".claude", "tmp", "notes", "probe")
    os.makedirs(real_probe)
    git("init", "-q", cwd=real_probe)
    git("commit", "-q", "--allow-empty", "-m", "only here", cwd=real_probe)
    expect_wt("ignored .claude/tmp holding a planted folder beside a repository with an unpushed commit",
              plant_beside, False, lp)
    plant_like = job("plant_like")
    like_probe = os.path.join(plant_like, ".claude", "tmp", "pe_absent_delete_dxf68feq0", "probe")
    os.makedirs(like_probe)
    git("init", "-q", cwd=like_probe)
    git("commit", "-q", "--allow-empty", "-m", "only here", cwd=like_probe)
    expect_wt("ignored .claude/tmp holding a look-alike name with an unpushed commit", plant_like, False, lp)
    nested = job("nested")
    os.makedirs(os.path.join(nested, ".claude", "scratch", "clone"))
    git("init", "-q", cwd=os.path.join(nested, ".claude", "scratch", "clone"))
    write(os.path.join(nested, ".claude", "scratch", "clone", "w.txt"), "unpushed work\n")
    expect_wt("ignored scratch holding a git repository", nested, False, lp)
    # What a removal that failed on read-only object files leaves: git cannot open it and nothing names a commit.
    stripped = job("stripped")
    stripped_git = os.path.join(stripped, ".claude", "scratch", "recon_fixture_x", "repo", ".git")
    os.makedirs(os.path.join(stripped_git, "objects", "ab"))
    write(os.path.join(stripped_git, "objects", "ab", "cdef"), "blob\n")
    expect_wt("ignored scratch holding a .git directory with objects only, no HEAD and no ref", stripped, True, lp)
    stripped_ref = job("stripped_ref")
    named = os.path.join(stripped_ref, ".claude", "scratch", "x", "repo", ".git", "refs", "heads")
    os.makedirs(named)
    write(os.path.join(named, "work"), "0" * 40 + "\n")
    expect_wt("ignored scratch holding a .git directory with no HEAD that still names a branch", stripped_ref, False, lp)

    # A job's scratch may hold a standalone clone a publish job made. The same clean-pushed rule decides it
    # as in the main checkout's scratch: a clean clone loses nothing, one holding unpushed work stays.
    inner_seed = os.path.join(FIX, "inner_seed")
    os.makedirs(inner_seed)
    git("init", "-q", "-b", "main", cwd=inner_seed)
    write(os.path.join(inner_seed, "i.txt"), "i\n")
    git("add", "i.txt", cwd=inner_seed)
    git("commit", "-q", "-m", "i", cwd=inner_seed)
    inner_remote = os.path.join(FIX, "inner_remote.git")
    git("clone", "-q", "--bare", inner_seed, inner_remote)
    nested_ok = job("nested_ok")
    ok_clone = os.path.join(nested_ok, ".claude", "scratch", "pubcopy")
    git("clone", "-q", inner_remote, ok_clone)
    nested_bad = job("nested_bad")
    bad_clone = os.path.join(nested_bad, ".claude", "scratch", "pubcopy")
    git("clone", "-q", inner_remote, bad_clone)
    write(os.path.join(bad_clone, "i.txt"), "unpushed\n")
    git("commit", "-q", "-a", "-m", "only here", cwd=bad_clone)
    nested_link = job("nested_link")
    link_clone = os.path.join(nested_link, ".claude", "scratch", "tpl")
    git("worktree", "add", "-q", "--detach", link_clone, cwd=lp)
    expect_clone("a clean clone under a job's scratch", ok_clone, True, lp)
    expect_clone("a clone under a job's scratch with an unpushed commit", bad_clone, False, lp, "no remote branch")
    expect_clone("a linked worktree under a job's scratch", link_clone, False, lp, "not a standalone clone")
    expect_wt("job scratch holding a clean pushed clone", nested_ok, True, lp)
    expect_wt("job scratch holding a clone with an unpushed commit", nested_bad, False, lp)
    expect_wt("job scratch holding a linked worktree", nested_link, False, lp)
    other_ignored = job("other_ignored")
    write(os.path.join(other_ignored, ".env.local"), "TOKEN=1\n")
    expect_wt("ignored local config outside scratch", other_ignored, False, lp)
    with open(os.path.join(lp, ".git", "info", "exclude"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write("*.tmp\n*.TMP\nstray.tmp.keep\n")
    import_tmp = job("import_tmp")
    os.makedirs(os.path.join(import_tmp, "Visual"))
    write(os.path.join(import_tmp, "Visual", "icon.png.import119908465.tmp"), "t\n")
    write(os.path.join(import_tmp, "Visual", "icon.png.import~RF2c3d449d.TMP"), "t\n")
    expect_wt("ignored Godot import temp files", import_tmp, True, lp)
    spaced_tmp = job("spaced_tmp")
    os.makedirs(os.path.join(spaced_tmp, "Visual", "Prompts Export 256"))
    write(os.path.join(spaced_tmp, "Visual", "Prompts Export 256", "icon.png.import119908465.tmp"), "t\n")
    expect_wt("ignored Godot import temp file under a folder with a space (git quotes the path)", spaced_tmp, True, lp)
    other_tmp = job("other_tmp")
    write(os.path.join(other_tmp, "draft.tmp"), "an author's draft\n")
    expect_wt("ignored temp file Godot did not write", other_tmp, False, lp)
    from pathlib import Path
    # A planted project: the live project's own list is its data, and a consumer of this proof holds another one.
    planted = os.path.join(FIX, "patterns_project")
    write(os.path.join(planted, ".claude", "skills", "project_subsystems", "adaptation.json"),
          json.dumps({"regenerable_ignored": [".runsettings", "logs", "addons/*/libs/~*"]}))
    listed = rc._project_patterns(Path(planted))
    for name in (".runsettings", "logs/run.log", "addons/debug_draw_3d/libs/~libdd3d.windows.editor.x86_64.dll"):
        global total, fails
        total += 1
        if not rc._listed(name, listed):
            fails += 1
            print(f"  FAIL the project lists the output a test run writes: {name}")
        else:
            print(f"  ok   the project lists the output a test run writes: {name}")

    for label, cmd, want in (
        ("cherry-picked job worktree", "rm -rf .claude/worktrees/picked", "allow"),
        ("squashed job worktree", "rm -rf .claude/worktrees/squashed", "allow"),
        ("job worktree with its own scratch", "rm -rf .claude/worktrees/scratch", "allow"),
        ("job worktree missing a tracked file", "rm -rf .claude/worktrees/gone", "allow"),
        ("job worktree holding a commit no default branch holds", "rm -rf .claude/worktrees/ahead", "deny"),
        ("job worktree holding a peer's scratch folder", "rm -rf .claude/worktrees/peer", "deny"),
        ("job worktree holding a nested repository", "rm -rf .claude/worktrees/nested", "deny"),
        ("job scratch holding a clean pushed clone", "rm -rf .claude/worktrees/nested_ok", "allow"),
        ("job scratch holding a clone with an unpushed commit", "rm -rf .claude/worktrees/nested_bad", "deny"),
        ("job scratch holding a linked worktree", "rm -rf .claude/worktrees/nested_link", "deny"),
        ("a clean clone under a job's scratch, deleted alone",
         "rm -rf .claude/worktrees/nested_ok/.claude/scratch/pubcopy", "allow"),
        ("a clone with an unpushed commit under a job's scratch, deleted alone",
         "rm -rf .claude/worktrees/nested_bad/.claude/scratch/pubcopy", "deny"),
    ):
        expect_wt_channel(label + ", worktree this session did not create", cmd, want, lp)
    expect_wt_deny_names("a clone under a job's scratch with unpushed work names the loss",
                         "rm -rf .claude/worktrees/nested_bad", "holds a git repository that loses work", lp)
    expect_wt_channel("peer session working in a landed job worktree", "rm -rf .claude/worktrees/picked", "deny", lp,
                      env=peer_login("landed", ("peer-landed", "5eed5eed-aaaa-4bbb-8ccc-000000000006", picked)))
    expect_wt_channel("the inner scratch delete stays refused", "rm -rf .claude/worktrees/scratch/.claude/scratch",
                      "deny", lp)


def project_remote():
    """A bare remote of a fixture project that carries a submodule, since `git worktree remove` refuses every
    worktree that carries one. Each part of the proof builds its own."""
    sub_seed = os.path.join(FIX, "sub_seed")
    os.makedirs(sub_seed)
    git("init", "-q", "-b", "main", cwd=sub_seed)
    write(os.path.join(sub_seed, "s.txt"), "s\n")
    git("add", "s.txt", cwd=sub_seed)
    git("commit", "-q", "-m", "s", cwd=sub_seed)
    sub_remote = os.path.join(FIX, "sub_remote.git")
    git("clone", "-q", "--bare", sub_seed, sub_remote)

    proj_seed = os.path.join(FIX, "proj_seed")
    os.makedirs(proj_seed)
    git("init", "-q", "-b", "main", cwd=proj_seed)
    write(os.path.join(proj_seed, "a.txt"), "a\n")
    write(os.path.join(proj_seed, ".gitignore"),
          ".claude/worktrees/\n.claude/scratch/\n.claude/tmp/\n.godot/\n.devdb/\n.env.local\ndist-preview/\nlogs/\ntest-results/\n")
    git("-c", "protocol.file.allow=always", "submodule", "add", "-q", sub_remote, "Sub", cwd=proj_seed)
    git("add", ".", cwd=proj_seed)
    git("commit", "-q", "-m", "a", cwd=proj_seed)
    proj_remote = os.path.join(FIX, "proj_remote.git")
    git("clone", "-q", "--bare", proj_seed, proj_remote)
    return proj_remote


def retired_worktree_cases():
    """A linked worktree under a fixture project's `.claude/worktrees/`, with a submodule."""
    proj_remote = project_remote()
    proj = os.path.join(FIX, "proj")
    git("clone", "-q", proj_remote, proj)
    home = os.path.join(proj, ".claude", "worktrees")
    os.makedirs(home)

    def add_wt(name, submodule=False):
        path = os.path.join(home, name)
        git("worktree", "add", "-q", "--detach", path, cwd=proj)
        if submodule:
            git("-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init", cwd=path)
        return path

    clean = add_wt("clean")
    expect_wt("clean, pushed, relative token", ".claude/worktrees/clean", True, proj)
    expect_wt("clean, pushed, absolute token", clean, True, proj)
    if os.name == "nt":
        expect_wt("clean, pushed, Git Bash /c/ token", msys(clean), True, proj)

    with_sub = add_wt("with_sub", submodule=True)
    expect_wt("populated submodule, clean and pushed", with_sub, True, proj)

    imports = add_wt("imports")
    write(os.path.join(imports, "icon.png.import"), "generated\n")
    expect_wt("untracked Godot .import only", imports, True, proj)

    cache = add_wt("cache")
    os.makedirs(os.path.join(cache, ".godot", "imported"))
    write(os.path.join(cache, ".godot", "imported", "icon.ctex"), "c\n")
    expect_wt("ignored regenerable .godot cache", cache, True, proj)

    evidence = add_wt("evidence")
    os.makedirs(os.path.join(evidence, ".claude", "scratch", PEER_SID))
    write(os.path.join(evidence, ".claude", "scratch", PEER_SID, "gate.out"), "VERDICT=PASS\n")
    expect_wt("ignored .claude/scratch holding another session's output folder", evidence, False, proj)

    emptied = add_wt("emptied")
    os.makedirs(os.path.join(emptied, ".claude", "scratch", "moved_out"))
    expect_wt("ignored .claude/scratch holding only empty directories", emptied, True, proj)

    untracked = add_wt("untracked")
    write(os.path.join(untracked, "notes.md"), "n\n")
    expect_wt("untracked authored file", untracked, False, proj)

    dirty = add_wt("dirty")
    write(os.path.join(dirty, "a.txt"), "changed\n")
    expect_wt("modified tracked file", dirty, False, proj)
    if os.name == "nt":
        expect_wt("modified tracked file, Git Bash /c/ token", msys(dirty), False, proj)

    ahead = add_wt("ahead")
    write(os.path.join(ahead, "b.txt"), "b\n")
    git("add", "b.txt", cwd=ahead)
    git("commit", "-q", "-m", "local only", cwd=ahead)
    expect_wt("local-only HEAD commit", ahead, False, proj)

    from unittest.mock import patch

    def add_branch_wt(name, branch):
        path = os.path.join(home, name)
        git("worktree", "add", "-q", "-b", branch, path, cwd=proj)
        return path

    pr_sync = add_branch_wt("sync-pr7", "sync/pr7")
    pr_head = add_branch_wt("pr_head", "feat/open-pr-head")
    with patch.object(rc, "_open_prs", lambda root: [(7, "feat/seven")]):
        expect_wt("sync/pr<N> branch of an open PR", pr_sync, False, proj)
        expect_wt("sync/pr<N> branch of an open PR, ignored files allowed", pr_sync, False, proj, allow_ignored=True)
    with patch.object(rc, "_open_prs", lambda root: [(9, "feat/open-pr-head")]):
        expect_wt("branch that is an open PR's head", pr_head, False, proj)
    with patch.object(rc, "_open_prs", lambda root: [(8, "feat/eight")]):
        expect_wt("pushed branches of no open PR", pr_sync, True, proj)
    with patch.object(rc, "_open_prs", lambda root: None):
        expect_wt("open PRs unreadable", pr_sync, False, proj)

    sub_dirty = add_wt("sub_dirty", submodule=True)
    write(os.path.join(sub_dirty, "Sub", "s.txt"), "changed\n")
    expect_wt("modified file inside the submodule", sub_dirty, False, proj)

    locked = add_wt("locked")
    git("worktree", "lock", locked, cwd=proj)
    expect_wt("locked worktree", locked, False, proj)

    stray = os.path.join(home, "stray")
    os.makedirs(stray)
    expect_wt("unregistered empty directory loses nothing", stray, True, proj)
    expect_wt("the worktrees directory itself", home, False, proj)
    expect_wt("a directory inside a worktree", os.path.join(with_sub, "Sub"), False, proj)
    elsewhere = os.path.join(FIX, "elsewhere_wt")
    git("worktree", "add", "-q", "--detach", elsewhere, cwd=proj)
    expect_wt("registered worktree outside .claude/worktrees", elsewhere, False, proj)
    expect_wt("traversal token", ".claude/worktrees/clean/../clean", False, proj)
    expect_wt("glob token", ".claude/worktrees/cl*", False, proj)
    expect_wt("a clean worktree of another project", clean, False, os.path.join(FIX, "proj_seed"))
    expect_retire_check("retired worktree prints retired, exit 0", [clean], 0, [clean + ": retired"], proj)
    expect_retire_check("blocked worktrees print their reasons, exit 1", [clean, dirty, locked], 1,
                        [clean + ": retired", dirty + ": blocked: ", locked + ": blocked: "], proj)
    expect_retire_check("--created judges as a worktree this session created", ["--created", evidence], 0,
                        [evidence + ": retired"], proj)
    expect_retire_check("without --created, ignored evidence blocks", [evidence], 1, [evidence + ": blocked: "], proj)
    expect_retire_check("the retired --owner-approved flag grants nothing", ["--owner-approved", evidence], 1,
                        [evidence + ": blocked: "], proj)

    from unittest.mock import patch
    from pathlib import Path
    listing = 'worktree ' + with_sub.replace('\\', '/') + '\n\n'
    for label, overrides, reason in (
        ('listing unavailable', {'worktree': None}, 'cannot read worktree registration'),
        ('not registered', {'worktree': ''}, 'worktree is not registered'),
        ('locked registration', {'worktree': listing.rstrip() + '\nlocked\n'}, 'worktree is locked'),
        ('status unavailable', {'status': None}, 'cannot read worktree status'),
        ('dirty status', {'status': ' M a.txt\n'}, 'worktree has local files or changes:  M a.txt'),
        ('HEAD query unavailable', {'rev-list': None}, 'cannot verify HEAD reachability'),
        ('unpushed HEAD', {'rev-list': 'abc\n'},
         'HEAD abc is on no remote branch (save a bundle of its refs under .claude/scratch named for the '
         'worktree, then retry)'),
        ('submodule list unavailable', {'submodule': None}, 'cannot read submodule status'),
        ('malformed submodule row', {'submodule': ' bad\n'}, 'cannot parse submodule status'),
        ('submodule status unavailable', {'sub_status': None}, 'submodule Sub: cannot read status'),
        ('submodule HEAD unavailable', {'sub_pushed': None}, 'submodule Sub: cannot verify HEAD reachability'),
        ('submodule unpushed', {'sub_pushed': 'abc\n'},
         'submodule Sub: HEAD abc is on no remote branch (push it to a remote, then retry)'),
        ('submodule dirty', {'sub_status': '?? local.txt\n'}, 'submodule Sub has local files or changes: ?? local.txt'),
    ):
        def planted(args, repo):
            key = args[0]
            if Path(repo) == Path(with_sub) / 'Sub':
                key = {'status': 'sub_status', 'rev-list': 'sub_pushed'}.get(key, key)
            defaults = {'worktree': listing, 'status': '', 'rev-list': '',
                        'submodule': ' abc Sub (heads/main)\n', 'sub_status': '',
                        'sub_pushed': '', 'rev-parse': 'abc\n', 'branch': '', 'symbolic-ref': '',
                        'config': 'submodule.Sub.path Sub\n'}
            return overrides.get(key, defaults[key])
        global total, fails
        total += 1
        reasons = []
        try:
            with patch.object(rc, '_git', side_effect=planted):
                verdict = rc.is_retired_worktree(with_sub, proj, proj, reasons=reasons)
            ok = not verdict and reasons == [reason]
        except TypeError:
            ok = False
        if not ok:
            fails += 1
        print(('  ok   ' if ok else '  FAIL ') + 'retirement reason ' + label + ': ' + repr(reasons))

    expect_wt_channel("rm -rf retired worktree this session did not create (nothing is lost)", "rm -rf .claude/worktrees/clean", "allow", proj)
    expect_wt_channel("rm -rf dirty worktree", "rm -rf .claude/worktrees/dirty", "deny", proj)
    if os.name == "nt":
        expect_wt_channel("rm -rf dirty worktree, Git Bash /c/ path", "rm -rf " + msys(dirty), "deny", proj)
        expect_wt_channel("rm -rf retired worktree, Git Bash /c/ path", "rm -rf " + msys(clean), "allow", proj)
    expect_wt_channel("rm -rf retired worktree chained", "rm -rf .claude/worktrees/clean && rm -rf .claude/worktrees/dirty", "deny", proj)
    expect_wt_channel("rm -rf worktrees directory", "rm -rf .claude/worktrees", "deny", proj)

    # Ignored files that are no regenerable cache go only with a worktree this session created; tracked
    # edits, untracked files and unpushed commits block whoever created it.
    expect_wt("ignored scratch evidence, ignored files allowed", evidence, True, proj, allow_ignored=True)
    expect_wt("untracked authored file, ignored files allowed", untracked, False, proj, allow_ignored=True)
    made = created_transcript(os.path.join(FIX, "t_made.jsonl"), evidence, dirty)
    rm_evidence = "rm -rf .claude/worktrees/evidence"
    expect_wt_channel("scratch evidence, worktree not created by this session", rm_evidence, "deny", proj)
    expect_wt_channel("scratch evidence, worktree this session created", rm_evidence, "allow", proj, made)
    expect_wt_channel("modified tracked file, worktree this session created", "rm -rf .claude/worktrees/dirty",
                      "deny", proj, made)
    expect_wt_deny_names("scratch evidence deny names the ignored path", rm_evidence, "!! .claude/scratch", proj)

    # A worktree a live peer session works in stays, clean and pushed or not: deleting it breaks that session.
    # The fact is the runtime registry's `cwd` for a session other than this one (`peer_message_guard`).
    rm_clean = "rm -rf .claude/worktrees/clean"
    in_clean = peer_login("at", ("peer-at", "5eed5eed-aaaa-4bbb-8ccc-000000000001", clean))
    expect_wt_deny_names("peer session working in the worktree: deny names the session", rm_clean, "peer-at", proj,
                         env=in_clean)
    expect_wt_channel("peer session working inside the worktree", rm_clean, "deny", proj, env=peer_login(
        "inside", ("peer-in", "5eed5eed-aaaa-4bbb-8ccc-000000000002", os.path.join(clean, "Sub"))))
    expect_wt_channel("peer session working in another worktree", rm_clean, "allow", proj, env=peer_login(
        "other", ("peer-other", "5eed5eed-aaaa-4bbb-8ccc-000000000003", dirty)))
    expect_wt_channel("this session's own registry row does not hold the worktree", rm_clean, "allow", proj,
                      os.path.join(FIX, "5eed5eed-aaaa-4bbb-8ccc-000000000004.jsonl"), peer_login(
        "self", ("self-row", "5eed5eed-aaaa-4bbb-8ccc-000000000004", clean)))
    expect_wt_channel("peer session working in a worktree this session created", rm_evidence, "deny", proj, made,
                      peer_login("made", ("peer-made", "5eed5eed-aaaa-4bbb-8ccc-000000000005", evidence)))
    expect_wt_channel("no peer session registered", rm_clean, "allow", proj, env=peer_login("none"))

    # A dirty path loses nothing when its content is a blob in the object store
    # (saved with `git hash-object -w`) or its working file is gone (index blobs are always stored).
    stored = add_wt("stored")
    write(os.path.join(stored, "a.txt"), "a saved edit\n")
    write(os.path.join(stored, "new.txt"), "a saved new file\n")
    git("hash-object", "-w", "a.txt", "new.txt", cwd=stored)
    expect_wt("edits whose content git stores, ignored files allowed", stored, True, proj, allow_ignored=True)
    expect_wt("edits whose content git stores, ignored files not allowed", stored, False, proj)
    half = add_wt("half")
    write(os.path.join(half, "a.txt"), "a saved edit\n")
    write(os.path.join(half, "new.txt"), "an unsaved new file\n")
    git("hash-object", "-w", "a.txt", cwd=half)
    expect_wt("one edit stored, one not, ignored files allowed", half, False, proj, allow_ignored=True)
    gone = add_wt("gone")
    os.remove(os.path.join(gone, "a.txt"))
    git("add", "-A", cwd=gone)
    expect_wt("tracked file deleted and staged, ignored files allowed", gone, True, proj, allow_ignored=True)
    hollow = add_wt("hollow", submodule=True)
    git("rm", "-q", "-r", "--cached", ".", cwd=os.path.join(hollow, "Sub"))
    os.remove(os.path.join(hollow, "Sub", "s.txt"))
    expect_wt("submodule checkout emptied with its deletions staged, ignored files allowed", hollow, True, proj,
              allow_ignored=True)
    expect_wt("submodule checkout emptied with its deletions staged, ignored files not allowed", hollow, False, proj)
    made_stored = created_transcript(os.path.join(FIX, "t_made_stored.jsonl"), stored, half)
    expect_wt_channel("stored edits, worktree this session created", "rm -rf .claude/worktrees/stored", "allow", proj,
                      made_stored)
    expect_wt_channel("stored edits, worktree not created by this session", "rm -rf .claude/worktrees/stored", "deny",
                      proj)
    expect_wt_channel("one edit unstored, worktree this session created", "rm -rf .claude/worktrees/half", "deny",
                      proj, made_stored)

    # An orphan: git pruned the worktree's admin directory, so git can no longer read its state.
    # Its commits live in the shared object store; only file content git does not hold is loss.
    def orphan(name, submodule=False, edit=None):
        path = add_wt(name, submodule)
        if edit:
            edit(path)
        with open(os.path.join(path, ".git"), encoding="utf-8") as fh:
            admin = fh.read().split("gitdir:", 1)[1].strip()
        force_rmtree(admin if os.path.isabs(admin) else os.path.join(path, admin))
        return path

    o_clean = orphan("o_clean")
    expect_wt("orphan, content held by git, ignored files allowed", o_clean, True, proj, allow_ignored=True)
    expect_wt("orphan, content held by git, ignored files not allowed", o_clean, True, proj)
    o_godot = orphan("o_godot", edit=lambda p: (os.makedirs(os.path.join(p, ".godot", "imported")),
                                                  write(os.path.join(p, ".godot", "imported", "c.ctex"), "c\n"),
                                                  os.makedirs(os.path.join(p, "empty_ignored_dir"))))
    expect_wt("orphan, ignored .godot cache, ignored files not allowed", o_godot, True, proj)
    o_json = orphan("o_json", edit=lambda p: (os.makedirs(os.path.join(p, ".claude", "scratch", PEER_SID)),
                                                write(os.path.join(p, ".claude", "scratch", PEER_SID, "x.json"), "{}\n")))
    expect_wt("orphan, ignored non-regenerable file, ignored files not allowed", o_json, False, proj)
    named = []
    rc.is_retired_worktree(o_json, proj, proj, reasons=named)
    total += 1
    if not (len(named) == 1 and "x.json" in named[0] and "not a regenerable cache" in named[0]):
        fails += 1
        print(f"  FAIL orphan deny names the non-regenerable ignored file: {named}")
    else:
        print("  ok   orphan deny names the non-regenerable ignored file")
    expect_wt("orphan, ignored non-regenerable file, ignored files allowed", o_json, True, proj, allow_ignored=True)
    o_scratch = orphan("o_scratch", edit=lambda p: (os.makedirs(os.path.join(p, ".claude", "scratch")),
                                                     write(os.path.join(p, ".claude", "scratch", "x.out"), "x\n")))
    expect_wt("orphan, ignored scratch, ignored files allowed", o_scratch, True, proj, allow_ignored=True)
    o_import = orphan("o_import", edit=lambda p: write(os.path.join(p, "icon.png.import"), "generated\n"))
    expect_wt("orphan, untracked Godot .import, ignored files allowed", o_import, True, proj, allow_ignored=True)
    o_new = orphan("o_new", edit=lambda p: write(os.path.join(p, "notes.md"), "unsaved notes\n"))
    expect_wt("orphan, authored file git never saw", o_new, False, proj, allow_ignored=True)
    expect_wt("orphan, authored file git never saw, ignored files not allowed", o_new, False, proj)
    o_dirty = orphan("o_dirty", edit=lambda p: write(os.path.join(p, "a.txt"), "edited, never committed\n"))
    expect_wt("orphan, tracked file edited", o_dirty, False, proj, allow_ignored=True)
    o_sub = orphan("o_sub", submodule=True)
    expect_wt("orphan with a submodule, main checkout has no submodule store", o_sub, False, proj,
              allow_ignored=True)
    git("-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init", cwd=proj)
    expect_wt("orphan with a submodule, content held by git", o_sub, True, proj, allow_ignored=True)
    o_sub_dirty = orphan("o_sub_dirty", submodule=True,
                         edit=lambda p: write(os.path.join(p, "Sub", "s.txt"), "edited in the submodule\n"))
    expect_wt("orphan, submodule file edited", o_sub_dirty, False, proj, allow_ignored=True)

    # An orphan that sits beside the project (a build worktree made as a sibling folder) is judged like one
    # under `.claude/worktrees/` when its `.git` file names this repository's gone admin directory.
    def sibling(name, repo=proj, edit=None):
        path = os.path.join(FIX, name)
        git("worktree", "add", "-q", "--detach", path, cwd=repo)
        if edit:
            edit(path)
        with open(os.path.join(path, ".git"), encoding="utf-8") as fh:
            admin = fh.read().split("gitdir:", 1)[1].strip()
        force_rmtree(admin if os.path.isabs(admin) else os.path.join(path, admin))
        return path

    s_clean = sibling("s_clean")
    expect_wt("sibling orphan of this repository, content held by git", s_clean, True, proj)
    s_godot = sibling("s_godot", edit=lambda p: (os.makedirs(os.path.join(p, ".godot", "imported")),
                                                   write(os.path.join(p, ".godot", "imported", "c.ctex"), "c\n")))
    expect_wt("sibling orphan, ignored .godot cache", s_godot, True, proj)
    s_new = sibling("s_new", edit=lambda p: write(os.path.join(p, "notes.md"), "unsaved notes\n"))
    expect_wt("sibling orphan, authored file git never saw", s_new, False, proj)
    s_json = sibling("s_json", edit=lambda p: (os.makedirs(os.path.join(p, ".claude", "scratch", PEER_SID)),
                                                 write(os.path.join(p, ".claude", "scratch", PEER_SID, "n.json"), "{}\n")))
    expect_wt("sibling orphan, ignored non-regenerable file", s_json, False, proj)
    other = fresh_clone("other", proj_remote)
    s_other = sibling("s_other", repo=other)
    expect_wt("sibling orphan of another repository", s_other, False, proj)
    s_live = os.path.join(FIX, "s_live")
    git("worktree", "add", "-q", "--detach", s_live, cwd=proj)
    expect_wt("sibling worktree still registered", s_live, False, proj)
    expect_wt("the project itself", proj, False, proj)
    s_path = lambda path: path.replace("\\", "/")
    expect_wt_channel("sibling orphan of this repository", "rm -rf " + s_path(s_clean), "allow", proj)
    expect_wt_channel("sibling orphan with an ignored .godot cache", "rm -rf " + s_path(s_godot), "allow", proj)
    expect_wt_deny_names("sibling orphan with unsaved work names the file", "rm -rf " + s_path(s_new), "notes.md", proj)
    expect_wt_channel("sibling orphan with an ignored non-regenerable file", "rm -rf " + s_path(s_json), "deny", proj)
    expect_wt_channel("sibling orphan of another repository", "rm -rf " + s_path(s_other), "deny", proj)
    expect_wt_channel("sibling worktree still registered", "rm -rf " + s_path(s_live), "deny", proj)

    # A project lists its own regenerable outputs in `adaptation.json` `regenerable_ignored`
    # (paths from the worktree root; a listed directory covers everything under it).
    def outputs(p):
        for name, text in ((".devdb/local/default/backend.sqlite3", "db\n"),
                           (".devdb/preview.env", "DEVDB_TARGET=local\n"),
                           (".env.local", "APP_BACKEND_URL=http://127.0.0.1:1\n"),
                           ("dist-preview/index.html", "<html>\n"), ("logs/screens/a.png", "png\n"),
                           ("test-results/r.txt", "r\n")):
            os.makedirs(os.path.dirname(os.path.join(p, name)), exist_ok=True)
            write(os.path.join(p, name), text)

    s_out = sibling("s_out", edit=outputs)
    s_out_dirty = sibling("s_out_dirty", edit=lambda p: (outputs(p), write(os.path.join(p, "a.txt"), "unsaved edit\n")))
    s_out_extra = sibling("s_out_extra", edit=lambda p: (outputs(p), os.makedirs(os.path.join(p, ".devdb", "tmp")),
                                                           write(os.path.join(p, ".devdb", "tmp", "an.py"), "x\n")))
    s_out_nested = sibling("s_out_nested", edit=lambda p: (os.makedirs(os.path.join(p, "src", ".devdb", "local")),
                                                             write(os.path.join(p, "src", ".devdb", "local", "x.db"), "x\n")))
    s_out_logs = sibling("s_out_logs", edit=lambda p: (os.makedirs(os.path.join(p, "src", "logs")),
                                                        write(os.path.join(p, "src", "logs", "unique.txt"), "only copy\n")))
    s_out_other = sibling("s_out_other", repo=other, edit=outputs)
    live_out = add_wt("live_out")
    outputs(live_out)
    live_extra = add_wt("live_extra")
    outputs(live_extra)
    os.makedirs(os.path.join(live_extra, ".devdb", "tmp"))
    write(os.path.join(live_extra, ".devdb", "tmp", "an.py"), "x\n")
    expect_wt("sibling orphan with build outputs, project lists none", s_out, False, proj)
    expect_wt_channel("sibling orphan with build outputs, project lists none", "rm -rf " + s_path(s_out), "deny", proj)
    adaptation_dir = os.path.join(proj, ".claude", "skills", "project_subsystems")
    os.makedirs(adaptation_dir)
    adaptation_file = os.path.join(adaptation_dir, "adaptation.json")
    write(adaptation_file, json.dumps({"regenerable_ignored": [
        ".devdb/local", ".devdb/preview*", ".env.local", "dist-preview", "logs", "test-results"]}))
    expect_wt("sibling orphan with listed build outputs", s_out, True, proj)
    expect_wt("sibling orphan, listed outputs and an unsaved tracked edit", s_out_dirty, False, proj)
    expect_wt("sibling orphan, listed outputs and an unlisted ignored file", s_out_extra, False, proj)
    expect_wt("sibling orphan, a listed name nested below the root", s_out_nested, False, proj)
    expect_wt("sibling orphan, an ignored `logs` folder below the root is not the project's listed `logs`", s_out_logs, False, proj)
    expect_wt("sibling orphan of another repository, listed outputs", s_out_other, False, proj)
    expect_wt("registered worktree with listed build outputs", live_out, True, proj)
    expect_wt("registered worktree, listed outputs and an unlisted ignored file", live_extra, False, proj)
    expect_wt_channel("sibling orphan with listed build outputs", "rm -rf " + s_path(s_out), "allow", proj)
    expect_wt_deny_names("sibling orphan with an unsaved tracked edit names the file", "rm -rf " + s_path(s_out_dirty),
                         "a.txt", proj)
    expect_wt_deny_names("sibling orphan with an unlisted ignored file names it", "rm -rf " + s_path(s_out_extra),
                         ".devdb/tmp/an.py", proj)
    expect_wt_channel("sibling orphan of another repository, listed outputs", "rm -rf " + s_path(s_out_other), "deny", proj)
    expect_wt_channel("registered worktree with listed build outputs", "rm -rf " + s_path(live_out), "allow", proj)
    for bad, label in (('{"regenerable_ignored": ".devdb/local"}', "not a list"), ('{"regenerable_ignored": ["*"]}', "a bare wildcard"),
                       ('{"regenerable_ignored": ["../**", "/c/x", ""]}', "escaping and empty entries"), ("{not json", "unparseable")):
        write(adaptation_file, bad)
        expect_wt("sibling orphan with build outputs, the list is " + label, s_out, False, proj)
    os.remove(adaptation_file)

    # A worktree's submodule clone keeps its own remote refs, which go stale: its HEAD is pushed when
    # the main checkout's same submodule sees it on a remote.
    sub_main =os.path.join(proj, "Sub")
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=sub_main, env=ENV, capture_output=True,
                            text=True, check=True).stdout.strip()
    write(os.path.join(sub_main, "s2.txt"), "pushed from the main checkout\n")
    git("add", "s2.txt", cwd=sub_main)
    git("commit", "-q", "-m", "pushed later", cwd=sub_main)
    git("push", "-q", "origin", "HEAD:main", cwd=sub_main)
    git("fetch", "-q", "origin", cwd=sub_main)
    git("add", "Sub", cwd=proj)
    git("commit", "-q", "-m", "bump Sub", cwd=proj)
    git("push", "-q", "origin", "HEAD:main", cwd=proj)
    git("fetch", "-q", "origin", cwd=proj)
    stale = add_wt("stale_sub_refs", submodule=True)
    git("update-ref", "refs/remotes/origin/main", before, cwd=os.path.join(stale, "Sub"))
    expect_wt("submodule HEAD pushed, seen only by the main checkout's refs", stale, True, proj)

    # A submodule whose `.git` names a pruned admin directory: git cannot read it, so its files are judged
    # like an orphan's, against the main checkout's submodule store, and only on the owner's pick.
    def broken_sub(name, edit=None):
        path = add_wt(name, submodule=True)
        if edit:
            edit(path)
        with open(os.path.join(path, "Sub", ".git"), encoding="utf-8") as fh:
            admin = fh.read().split("gitdir:", 1)[1].strip()
        force_rmtree(admin if os.path.isabs(admin) else os.path.join(path, "Sub", admin))
        return path

    b_held = broken_sub("b_held")
    expect_wt("broken submodule link, content held by git, ignored files allowed", b_held, True, proj, allow_ignored=True)
    expect_wt("broken submodule link, ignored files not allowed", b_held, False, proj)
    b_reasons = []
    rc.is_retired_worktree(b_held, proj, proj, reasons=b_reasons)
    total += 1
    if not any("Sub" in r and "missing admin directory" in r for r in b_reasons):
        fails += 1
        print(f"  FAIL broken submodule link names the broken link: {b_reasons}")
    else:
        print("  ok   broken submodule link names the broken link")
    b_edit = broken_sub("b_edit", edit=lambda p: write(os.path.join(p, "Sub", "s.txt"), "edited\n"))
    expect_wt("broken submodule link, submodule file edited", b_edit, False, proj, allow_ignored=True)

    # Every unmet condition reaches one deny, so a retry never meets a second, unseen block.
    both = add_wt("two_blocks")
    write(os.path.join(both, "notes.md"), "n\n")
    write(os.path.join(both, "b2.txt"), "b\n")
    git("add", "b2.txt", cwd=both)
    git("commit", "-q", "-m", "local only", cwd=both)
    two = []
    rc.is_retired_worktree(both, proj, proj, reasons=two)
    total += 1
    if not (len(two) == 2 and "notes.md" in two[0] and "no remote branch" in two[1]):
        fails += 1
        print(f"  FAIL every unmet condition is named in one verdict: {two}")
    else:
        print("  ok   every unmet condition is named in one verdict")
    # A leftover whose `.git` file is gone too (an interrupted removal): the same held-content test decides it.
    def gitless(name, edit=None):
        path = orphan(name, edit=edit)
        os.remove(os.path.join(path, ".git"))
        return path

    g_clean = gitless("g_clean")
    expect_wt("gitless leftover, content held by git, ignored files allowed", g_clean, True, proj, allow_ignored=True)
    expect_wt("gitless leftover, content held by git, ignored files not allowed", g_clean, True, proj)
    g_new = gitless("g_new", edit=lambda p: write(os.path.join(p, "notes.md"), "unsaved notes\n"))
    expect_wt("gitless leftover, authored file git never saw", g_new, False, proj, allow_ignored=True)
    # One deny names every unheld file, so removing a worktree never walks through a deny per file.
    def three_files(p):
        for n in ("a_notes.md", "b_notes.md", "c_notes.md"):
            write(os.path.join(p, n), "unsaved " + n + "\n")

    def named_all(label, reasons):
        global fails, total
        total += 1
        text = " | ".join(reasons)
        if len(reasons) == 1 and all(n in text for n in ("a_notes.md", "b_notes.md", "c_notes.md")):
            print(f"  ok   {label} with three unheld files names all three in one deny")
        else:
            fails += 1
            print(f"  FAIL {label} with three unheld files names all three in one deny: {reasons}")

    many_o3 = orphan("many_o3", edit=three_files)
    got = []
    rc.lossless_orphan_worktree(many_o3, proj, proj, got)
    named_all("orphan", got)
    many_l3 = add_wt("many_l3")
    three_files(many_l3)
    got = []
    rc.is_retired_worktree(many_l3, proj, proj, reasons=got)
    named_all("live worktree", [r for r in got if "local files" in r])
    many_cap = orphan("many_cap", edit=lambda p: [write(os.path.join(p, "f%02d.md" % i), "x%d\n" % i) for i in range(11)])
    got = []
    rc.lossless_orphan_worktree(many_cap, proj, proj, got)
    total += 1
    if not (got and "f00.md" in got[0] and "(and 3 more)" in got[0] and "f10.md" not in got[0]):
        fails += 1
        print(f"  FAIL a long list is capped and counts the rest: {got}")
    else:
        print("  ok   a long list is capped and counts the rest")
    expect_wt_channel("lossless orphan", "rm -rf .claude/worktrees/o_clean", "allow", proj)
    expect_wt_channel("lossless orphan a peer session works in", "rm -rf .claude/worktrees/o_clean", "deny", proj,
                      env=peer_login("orphan", ("peer-orphan", "5eed5eed-aaaa-4bbb-8ccc-000000000006", o_clean)))
    expect_wt_channel("orphan with an ignored .godot cache", "rm -rf .claude/worktrees/o_godot", "allow",
                      proj)
    expect_wt_channel("orphan with unsaved work", "rm -rf .claude/worktrees/o_new", "deny", proj)
    expect_wt_channel("orphan with ignored non-regenerable file", "rm -rf .claude/worktrees/o_json",
                      "deny", proj)
    expect_wt_channel("registered worktree holding an unpushed commit, not created by this session",
                      "rm -rf .claude/worktrees/ahead", "deny", proj)

    # A lane landed by rebase: its local commit's patch is on the remote under another hash, so
    # retirement loses nothing (2026-10-08, session b48600e4: eight such lanes read as unpushed).
    rebased = add_wt("rebased")
    write(os.path.join(rebased, "c.txt"), "c\n")
    git("add", "c.txt", cwd=rebased)
    git("commit", "-q", "-m", "lane commit", cwd=rebased)
    git("checkout", "-q", "main", cwd=proj)
    write(os.path.join(proj, "c.txt"), "c\n")
    git("add", "c.txt", cwd=proj)
    git("commit", "-q", "-m", "landed by rebase", cwd=proj)
    git("push", "-q", "origin", "main", cwd=proj)
    git("fetch", "-q", "origin", cwd=proj)
    expect_wt("lane commit landed on the remote by rebase", rebased, True, proj)
    expect_wt("local-only HEAD commit, after another patch landed", ahead, False, proj)

    # A scratch folder that is no clone (a copy, a study's output) holds no git state to lose, so it is
    # removable; a registered worktree inside it, or a repository whose commits are on no remote, is not.
    scratch = os.path.join(proj, ".claude", "scratch")
    os.makedirs(os.path.join(scratch, "copytree"))
    write(os.path.join(scratch, "copytree", "big.bin"), "x\n")
    held = os.path.join(scratch, "heldtree")
    git("worktree", "add", "-q", "--detach", os.path.join(held, "wt"), cwd=proj)
    unsaved = os.path.join(scratch, "unsaved_repo")
    os.makedirs(unsaved)
    git("init", "-q", "-b", "main", cwd=unsaved)
    write(os.path.join(unsaved, "n.txt"), "unpushed\n")
    git("add", "n.txt", cwd=unsaved)
    git("commit", "-q", "-m", "only here", cwd=unsaved)
    expect_wt_channel("scratch copy", "rm -rf .claude/scratch/copytree", "allow", proj)
    expect_wt_deny_names("scratch copy a peer session works in: deny names the session",
                         "rm -rf .claude/scratch/copytree", "peer-scratch", proj, env=peer_login(
                             "scratch", ("peer-scratch", "5eed5eed-aaaa-4bbb-8ccc-000000000007",
                                         os.path.join(scratch, "copytree"))))
    expect_wt_channel("scratch folder holding a registered worktree", "rm -rf .claude/scratch/heldtree", "deny", proj)
    expect_wt_channel("scratch repository with an unpushed commit", "rm -rf .claude/scratch/unsaved_repo", "deny", proj)
    expect_wt_deny_names("scratch repository deny names push, not a by-file delete",
                         "rm -rf .claude/scratch/unsaved_repo", "push its commits to a remote", proj)
    expect_wt_channel("whole scratch dir", "rm -rf .claude/scratch", "deny", proj)
    peer_dir = os.path.join(scratch, "peers", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d")
    os.makedirs(peer_dir)
    write(os.path.join(peer_dir, "out.txt"), "x\n")
    os.makedirs(os.path.join(scratch, "no_ids"))
    write(os.path.join(scratch, "no_ids", "f.txt"), "x\n")
    expect_wt_channel("scratch folder of another session",
                      "rm -rf .claude/scratch/peers/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "deny", proj)
    expect_wt_channel("glob over a folder holding another session's folder", "rm -rf .claude/scratch/peers/*",
                      "deny", proj)
    expect_wt_channel("glob over a folder holding no session folder", "rm -rf .claude/scratch/no_ids/*", "allow", proj)

    # A saved copy under the project's `.claude/scratch` that the guard verifies makes the same work
    # regenerable: a bundle for an unpushed HEAD, a tar or patch for a nested copy's files.
    saved = os.path.join(scratch, "saved")
    os.makedirs(saved)

    bundled = add_wt("bundled")
    write(os.path.join(bundled, "only.txt"), "bundled work\n")
    git("add", "only.txt", cwd=bundled)
    git("commit", "-q", "-m", "unpushed lane commit", cwd=bundled)
    expect_wt("unpushed HEAD with no saved copy", bundled, False, proj)
    git("bundle", "create", "-q", os.path.join(saved, "bundled.bundle"), "--all", cwd=bundled)
    expect_wt("unpushed HEAD held by a bundle named for the worktree", bundled, True, proj)
    expect_retire_check("saved-copy worktree retires", [bundled], 0, [bundled + ": retired"], proj)

    stale = add_wt("stale")
    write(os.path.join(stale, "s1.txt"), "s1\n")
    git("add", "s1.txt", cwd=stale)
    git("commit", "-q", "-m", "covered", cwd=stale)
    git("bundle", "create", "-q", os.path.join(saved, "stale.bundle"), "--all", cwd=stale)
    write(os.path.join(stale, "s2.txt"), "s2\n")
    git("add", "s2.txt", cwd=stale)
    git("commit", "-q", "-m", "not covered", cwd=stale)
    expect_wt("a bundle whose tip is not HEAD", stale, False, proj)

    def nested_repo(wt_name, repo_name, commit=False, untracked=True):
        path = add_wt(wt_name)
        repo = os.path.join(path, ".claude", "scratch", repo_name)
        os.makedirs(repo)
        git("init", "-q", "-b", "main", cwd=repo)
        write(os.path.join(repo, "kept.txt"), "kept work\n")
        if untracked:
            write(os.path.join(repo, "side.txt"), "side work\n")
        git("add", "kept.txt", cwd=repo)
        if commit:
            git("commit", "-q", "-m", "nested", cwd=repo)
        return path, repo

    def save_tar(repo, name):
        import tarfile
        target = os.path.join(saved, name + ".tgz")
        with tarfile.open(target, "w:gz") as archive:
            archive.add(repo, arcname=os.path.basename(repo))
        return target

    tar_wt, tar_repo = nested_repo("tarred", "copy")
    expect_wt("staged-only nested copy with no saved copy", tar_wt, False, proj)
    save_tar(tar_repo, "tarred_copy")
    expect_wt("staged-only nested copy held by a tar named for it", tar_wt, True, proj)
    write(os.path.join(tar_repo, "extra.txt"), "extra work\n")
    expect_wt("a tar missing a file the copy holds", tar_wt, False, proj)
    save_tar(tar_repo, "tarred_copy")
    expect_wt("the retaken tar covers the added file", tar_wt, True, proj)

    patch_wt, patch_repo = nested_repo("patched", "tplmap", commit=True, untracked=False)
    nested_remote = os.path.join(FIX, "tplmap_remote.git")
    git("clone", "-q", "--bare", patch_repo, nested_remote)
    git("remote", "add", "origin", nested_remote, cwd=patch_repo)
    git("fetch", "-q", "origin", cwd=patch_repo)
    git("push", "-q", "origin", "main", cwd=patch_repo)
    write(os.path.join(patch_repo, "kept.txt"), "changed work\n")
    write(os.path.join(patch_repo, "added.txt"), "added work\n")
    git("add", "added.txt", cwd=patch_repo)
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=patch_repo, env=ENV,
                          capture_output=True, text=True).stdout
    expect_wt("nested copy changed with no saved copy", patch_wt, False, proj)
    write(os.path.join(saved, "patched_tplmap.patch"), diff)
    expect_wt("nested copy held by a patch named for it", patch_wt, True, proj)
    write(os.path.join(patch_repo, "kept.txt"), "changed again\n")
    expect_wt("a patch taken before a later edit", patch_wt, False, proj)

    ahead_wt, ahead_repo = nested_repo("ahead_nested", "aheadcopy", commit=True)
    save_tar(ahead_repo, "ahead_nested_aheadcopy")
    expect_wt("nested copy whose commit is on no remote, even with a tar", ahead_wt, False, proj)
    git("bundle", "create", "-q", os.path.join(saved, "ahead_nested_aheadcopy.bundle"), "--all", cwd=ahead_repo)
    expect_wt("nested copy whose commit a bundle names", ahead_wt, True, proj)

    link_wt = add_wt("link_saved")
    os.makedirs(os.path.join(link_wt, ".claude", "scratch"))
    link_repo = os.path.join(link_wt, ".claude", "scratch", "copied_wt")
    git("worktree", "add", "-q", "--detach", link_repo, cwd=proj)
    write(os.path.join(link_repo, "a.txt"), "edited\n")
    expect_wt("a nested linked worktree with no saved copy", link_wt, False, proj)
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=link_repo, env=ENV,
                          capture_output=True, text=True).stdout
    write(os.path.join(saved, "link_saved_copied_wt.patch"), diff)
    expect_wt("a nested linked worktree held by a patch named for it", link_wt, True, proj)


def scratch_clone_cases(outside):
    global fails, total
    seed = os.path.join(FIX, "seed")
    os.makedirs(seed)
    git("init", "-q", "-b", "main", cwd=seed)
    write(os.path.join(seed, "a.txt"), "a\n")
    git("add", "a.txt", cwd=seed)
    git("commit", "-q", "-m", "a", cwd=seed)
    remote = os.path.join(FIX, "remote.git")
    git("clone", "-q", "--bare", seed, remote)

    clean = fresh_clone("clean", remote)
    expect("clean clone, relative token", rel(clean), True)
    expect("clean clone, absolute token", clean, True)
    if os.name == "nt":
        expect("clean clone, Git Bash /c/ token", msys(clean), True)

    dirty = fresh_clone("dirty", remote)
    write(os.path.join(dirty, "a.txt"), "changed\n")
    expect("modified tracked file", dirty, False)

    untracked = fresh_clone("untracked", remote)
    write(os.path.join(untracked, "new.txt"), "n\n")
    expect("untracked file", untracked, False)

    # An ignored regenerable cache (the semantic-search index a test root builds) is not loss; any
    # other ignored file is, and the reason names it.
    cached = fresh_clone("cached", remote)
    write(os.path.join(cached, ".git", "info", "exclude"), ".search-index/\n")
    os.makedirs(os.path.join(cached, ".search-index"))
    write(os.path.join(cached, ".search-index", "search.db"), "x\n")
    expect("ignored regenerable cache", cached, True)
    noted = fresh_clone("noted", remote)
    write(os.path.join(noted, ".git", "info", "exclude"), "notes.local\n")
    write(os.path.join(noted, "notes.local"), "keep me\n")
    expect("ignored non-cache file", noted, False)
    total += 1
    why = []
    rc.is_regenerable_clone(noted, PROJECT, PROJECT, reasons=why)
    if len(why) != 1 or "notes.local" not in why[0]:
        fails += 1
        print(f"  FAIL clone reason names the ignored file: {why}")
    else:
        print("  ok   clone reason names the ignored file")

    stashed = fresh_clone("stashed", remote)
    write(os.path.join(stashed, "a.txt"), "stash me\n")
    git("stash", "-q", cwd=stashed)
    expect("non-empty stash", stashed, False)

    ahead = fresh_clone("ahead", remote)
    write(os.path.join(ahead, "b.txt"), "b\n")
    git("add", "b.txt", cwd=ahead)
    git("commit", "-q", "-m", "local only", cwd=ahead)
    expect("local-only commit", ahead, False)

    tagged = fresh_clone("tagged", remote)
    git("tag", "-a", "local-tag", "-m", "t", cwd=tagged)
    expect("tag at a pushed commit", tagged, True)
    write(os.path.join(tagged, "c.txt"), "c\n")
    git("add", "c.txt", cwd=tagged)
    git("commit", "-q", "-m", "tagged local", cwd=tagged)
    git("reset", "-q", "--hard", "HEAD~1", cwd=tagged)
    git("tag", "local-only-tag", "HEAD@{1}", cwd=tagged)
    expect("tag on a local-only commit", tagged, False)

    no_remote = fresh_clone("no_remote", remote)
    git("remote", "remove", "origin", cwd=no_remote)
    expect("clone without a remote", no_remote, False)

    multi = fresh_clone("m", remote)
    worktree = os.path.join(FIX, "w")
    git("worktree", "add", "-q", worktree, cwd=multi)
    expect("clone with a second worktree", multi, False)
    expect("worktree checkout (.git file)", worktree, False)

    plain = os.path.join(FIX, "plain_dir")
    os.makedirs(plain)
    write(os.path.join(plain, "evidence.txt"), "x\n")
    expect("plain directory, no .git", plain, False)

    expect("scratch itself", ".claude/scratch", False)
    expect("scratch itself, absolute", os.path.join(CLAUDE, "scratch"), False)
    outside_clone = os.path.join(outside, "clone")
    git("clone", "-q", remote, outside_clone)
    expect("clean clone outside scratch", outside_clone, False)
    expect("traversal token", rel(clean) + "/../clean", False)
    expect("glob token", ".claude/scratch/_rc_fix_*/clean", False)
    expect("variable token", "$HOME/clean", False)
    expect("home token", "~/clean", False)
    expect("nonexistent path", os.path.join(FIX, "missing"), False)
    expect("empty token", "", False)

    # The real pre_bash_dispatch.py channel: a crash or traceback is never an allow.
    expect_channel("rm -rf clean clone", "rm -rf " + rel(clean), "allow")
    expect_channel("rm -rf dirty clone", "rm -rf " + rel(dirty), "deny")
    expect_channel("rm -rf clone holding only an ignored cache", "rm -rf " + rel(cached), "allow")
    # A redirection is shell plumbing, never a delete target.
    expect_channel("rm -rf clean clone with 2>&1 and a pipe", "rm -rf " + rel(clean) + " 2>&1 | tail -1", "allow")
    expect_channel("rm -rf clean clone with a spaced redirect", "rm -rf " + rel(clean) + " > /dev/null", "allow")
    expect_channel("rm -rf clean clone with a fused redirect", "rm -rf " + rel(clean) + " 2>/dev/null", "allow")
    expect_channel("rm -rf dirty clone with 2>&1", "rm -rf " + rel(dirty) + " 2>&1", "deny")
    total += 1
    verdict, proc = channel("rm -rf " + rel(noted))
    if verdict != "deny" or "notes.local" not in proc.stdout + proc.stderr:
        fails += 1
        print(f"  FAIL channel clone deny names its reason: {verdict} {(proc.stdout + proc.stderr)[-240:]!r}")
    else:
        print("  ok   channel clone deny names its reason")
    # A plain scratch folder holds no git state to lose; a folder holding a dirty clone does.
    expect_channel("rm -rf clean clone plus plain dir", "rm -rf " + rel(clean) + " " + rel(plain), "allow")
    expect_channel("rm -rf plain dir holding a dirty clone", "rm -rf " + rel(FIX), "deny")
    expect_channel("rm -rf clean clone chained", "rm -rf " + rel(clean) + " && rm -rf " + rel(dirty), "deny")
    expect_channel("rm -rf scratch", "rm -rf .claude/scratch", "deny")

    # An empty leftover directory under scratch (no file, no .git at any depth) is removable, and so is one
    # holding plain files; a nested .git keeps the deny; an empty directory outside scratch keeps its verdict.
    empty_leftover = os.path.join(FIX, "empty_leftover", "redcheck")
    os.makedirs(empty_leftover)
    expect_channel("rm -r empty leftover under scratch", "rm -r " + rel(empty_leftover), "allow")
    filled_leftover = os.path.join(FIX, "filled_leftover", "redcheck")
    os.makedirs(filled_leftover)
    write(os.path.join(filled_leftover, "notes.txt"), "keep\n")
    expect_channel("rm -r leftover holding one file", "rm -r " + rel(filled_leftover), "allow")
    git_leftover = os.path.join(FIX, "git_leftover", "redcheck")
    os.makedirs(os.path.join(git_leftover, ".git"))
    expect_channel("rm -r leftover holding an empty .git", "rm -r " + rel(git_leftover), "deny")
    empty_outside = os.path.join(CLAUDE, "rc_empty_outside_%d" % os.getpid())
    os.makedirs(empty_outside)
    expect_channel("rm -r empty directory outside scratch", "rm -r " + rel(empty_outside), "deny")
    force_rmtree(empty_outside)


def unstored_loss_cases():
    """The owner's removal pick: a dirty path whose working file is missing loses work unless git's status says it
    was deleted. A quoted porcelain path (octal escapes) does not decode to a file, so it must fail closed."""
    global fails, total
    import pathlib

    print("_regenerable_clone — unstored loss of a missing working file")
    with tempfile.TemporaryDirectory() as empty_root:
        for label, status, want in [
            ("deleted working file loses nothing", " D gone.txt\n", None),
            ("modified path missing from disk is a loss", " M missing.txt\n", " M missing.txt"),
            ("quoted octal path missing from disk is a loss", ' M "caf\\303\\251.txt"\n', ' M "caf\\303\\251.txt"'),
        ]:
            total += 1
            got = rc._first_unstored_loss(status, pathlib.Path(empty_root))
            if got != want:
                fails += 1
                print(f"  FAIL {label}: got {got!r}, want {want!r}")
            else:
                print(f"  ok   {label}")

    # A non-integer timeout variable must not stop the module loading: pattern_enforcer imports it, and a crash exits 1, which never blocks.
    total += 1
    bad_env = dict(os.environ, REGENERABLE_CLONE_GIT_TIMEOUT_S="ten")
    load = subprocess.run([sys.executable, "-c", "import _regenerable_clone as m; print(m._GIT_TIMEOUT_S)"],
                          cwd=os.path.join(CLAUDE, "hooks"), env=bad_env, capture_output=True, text=True)
    if load.returncode != 0 or load.stdout.strip() != "10":
        fails += 1
        print(f"  FAIL non-integer timeout variable falls back to 10: rc={load.returncode} {(load.stdout + load.stderr)[-200:]!r}")
    else:
        print("  ok   non-integer timeout variable falls back to 10")


PARTS = 3
# Part 1 runs the scratch-clone cases, part 2 the retired-worktree cases, part 3 the landed and backgrounded ones;
# each builds its own fixture under its own FIX, so the three run beside each other.
WORKTREE_PARTS = {2: ("retired worktree discriminator", lambda: retired_worktree_cases()),
                  3: ("landed and backgrounded worktree discriminator",
                      lambda: (backgrounded_worktree_cases(), landed_worktree_cases(project_remote())))}


def main(part: int = 1) -> int:
    global fails, total
    for other in range(2, PARTS + 1):  # a deleted part file would silently drop its cases
        assert os.path.isfile(os.path.join(HERE, "test_regenerable_clone_part%d.py" % other)), "missing part %d" % other
    for stale in glob.glob(PREFIX + "*"):
        if stale != FIX and time.time() - os.path.getmtime(stale) > STALE_AFTER_S:
            force_rmtree(stale)
    os.makedirs(FIX)
    outside = tempfile.mkdtemp(prefix="regen_clone_outside_")
    try:
        if part == 1:
            print("_regenerable_clone — regenerable scratch clone discriminator")
            scratch_clone_cases(outside)
        else:
            title, run = WORKTREE_PARTS[part]
            print("_regenerable_clone — " + title)
            if os.name == "nt" and len(FIX) > 150:
                print("  skip worktree cases: the fixture path is too long for git's receiving side on Windows")
            else:
                run()
    finally:
        force_rmtree(FIX)
        force_rmtree(outside)

    total += 1
    if os.path.exists(FIX) or os.path.exists(outside):
        fails += 1
        print(f"  FAIL teardown left a fixture directory: {FIX if os.path.exists(FIX) else outside}")
    else:
        print("  ok   teardown removed this run's fixture directories")
    if part == 1:
        unstored_loss_cases()

    print()
    print(f"{total - fails}/{total} cases pass" if fails else f"ALL PASS ({total} cases)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
