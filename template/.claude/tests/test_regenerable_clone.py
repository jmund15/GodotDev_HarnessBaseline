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
# The fixture sits under this checkout's scratch, so a deeply nested checkout (a publish worktree)
# pushes its submodule objects past Windows MAX_PATH. Every git this proof starts, the hook's
# included, runs with long paths on.
# `git worktree add` also refuses a git dir longer than PATH_MAX - 40 ("'$GIT_DIR' too big"), which long paths
# do not lift, so the prefix and the multi-worktree fixture names stay short.
os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.longpaths", GIT_CONFIG_VALUE_0="true")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}


def force_rmtree(path):
    """Remove a fixture tree, clearing the read-only bit git sets on object files."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=on_error)


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True)


def write(path, text):
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


def retired_worktree_cases():
    """A linked worktree under a fixture project's `.claude/worktrees/`, with a submodule, since
    `git worktree remove` refuses every worktree that carries one."""
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
    write(os.path.join(proj_seed, ".gitignore"), ".claude/worktrees/\n.claude/scratch/\n.godot/\n")
    git("-c", "protocol.file.allow=always", "submodule", "add", "-q", sub_remote, "Sub", cwd=proj_seed)
    git("add", ".", cwd=proj_seed)
    git("commit", "-q", "-m", "a", cwd=proj_seed)
    proj_remote = os.path.join(FIX, "proj_remote.git")
    git("clone", "-q", "--bare", proj_seed, proj_remote)
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
    os.makedirs(os.path.join(evidence, ".claude", "scratch"))
    write(os.path.join(evidence, ".claude", "scratch", "gate.out"), "VERDICT=PASS\n")
    expect_wt("ignored .claude/scratch evidence", evidence, False, proj)

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
        ('unpushed HEAD', {'rev-list': 'abc\n'}, 'HEAD abc is on no remote branch'),
        ('submodule list unavailable', {'submodule': None}, 'cannot read submodule status'),
        ('malformed submodule row', {'submodule': ' bad\n'}, 'cannot parse submodule status'),
        ('submodule status unavailable', {'sub_status': None}, 'submodule Sub: cannot read status'),
        ('submodule HEAD unavailable', {'sub_pushed': None}, 'submodule Sub: cannot verify HEAD reachability'),
        ('submodule unpushed', {'sub_pushed': 'abc\n'}, 'submodule Sub: HEAD abc is on no remote branch'),
        ('submodule dirty', {'sub_status': '?? local.txt\n'}, 'submodule Sub has local files or changes: ?? local.txt'),
    ):
        def planted(args, repo):
            key = args[0]
            if Path(repo) == Path(with_sub) / 'Sub':
                key = {'status': 'sub_status', 'rev-list': 'sub_pushed'}.get(key, key)
            defaults = {'worktree': listing, 'status': '', 'rev-list': '',
                        'submodule': ' abc Sub (heads/main)\n', 'sub_status': '',
                        'sub_pushed': '', 'rev-parse': 'abc\n', 'branch': '',
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
    o_json = orphan("o_json", edit=lambda p: (os.makedirs(os.path.join(p, ".claude", "scratch")),
                                                write(os.path.join(p, ".claude", "scratch", "x.json"), "{}\n")))
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

    # A worktree's submodule clone keeps its own remote refs, which go stale: its HEAD is pushed when
    # the main checkout's same submodule sees it on a remote.
    sub_main = os.path.join(proj, "Sub")
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


print("_regenerable_clone — regenerable scratch clone discriminator")
for stale in glob.glob(PREFIX + "*"):
    if stale != FIX and time.time() - os.path.getmtime(stale) > STALE_AFTER_S:
        force_rmtree(stale)
os.makedirs(FIX)
outside = tempfile.mkdtemp(prefix="regen_clone_outside_")
try:
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

    print("_regenerable_clone — retired worktree discriminator")
    if os.name == "nt" and len(FIX) > 150:
        print("  skip retired worktree cases: the fixture path is too long for git's receiving side on Windows")
    else:
        retired_worktree_cases()
finally:
    force_rmtree(FIX)
    force_rmtree(outside)

total += 1
if os.path.exists(FIX) or os.path.exists(outside):
    fails += 1
    print(f"  FAIL teardown left a fixture directory: {FIX if os.path.exists(FIX) else outside}")
else:
    print("  ok   teardown removed this run's fixture directories")

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

print()
print(f"{total - fails}/{total} cases pass" if fails else f"ALL PASS ({total} cases)")
sys.exit(1 if fails else 0)
