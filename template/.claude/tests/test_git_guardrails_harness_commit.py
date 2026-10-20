"""Re-runnable proof for the harness-commit branch of hooks/git_guardrails.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds the
hook a real PreToolUse payload against a temp git repo and asserts on the emitted
channel (deny via stderr + exit 2, or allow via exit 0).

    python3 .claude/tests/test_git_guardrails_harness_commit.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_TESTS_DIR)
HOOK = os.path.join(_CLAUDE_DIR, "hooks", "git_guardrails.py")

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "scripts"))
sys.path.insert(0, _TESTS_DIR)
from _case_pool import emit_section, run_sections  # noqa: E402
from _must_allow_cases import must_allow_cases  # noqa: E402

ALLOW, DENY, CRASH = "allow", "deny", "crash"

# A tracked hook whose proof a fresh clone lacks, and a hook the merged branch carries.
HARNESS_PLANTED = {"head": {".claude/hooks/tracked_hook.py": "x = 1\n"},
                   "offend": {".claude/hooks/merged_hook.py": "y = 1\n"}}


def git(repo, args):
    r = subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, r.stderr))
    return r.stdout


def write(repo, rel, content="x = 1\n"):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


def install_nested_parser(hooks):
    tools = os.path.join(os.path.dirname(hooks), 'tools')
    os.makedirs(tools, exist_ok=True)
    shutil.copy(os.path.join(_CLAUDE_DIR, 'tools', 'sidecar_argv.py'), tools)


def make_repo():
    repo = tempfile.mkdtemp(prefix="ggharness_")
    git(repo, ["init", "-q"])
    git(repo, ["config", "user.email", "t@t.example"])
    git(repo, ["config", "user.name", "t"])
    write(repo, "README.md", "seed\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed"])
    return repo


# Runs the hook's `main()` without its `__main__` wrapper, which exits 0 on any exception: through
# that wrapper a crashing guard reads as allow, so a must-allow case could never fail.
_UNGUARDED = ("import importlib.util, sys\n"
              "spec = importlib.util.spec_from_file_location('git_guardrails_unguarded', sys.argv[1])\n"
              "module = importlib.util.module_from_spec(spec)\n"
              "spec.loader.exec_module(module)\n"
              "module.main()\n")


# The hook's bound-proof run takes machine slots; the proof's own dir keeps peers' slot holders from
# starving it past the commit deadline.
_SLOT_DIR = tempfile.mkdtemp(prefix="ggharness_slots_")
atexit.register(shutil.rmtree, _SLOT_DIR, ignore_errors=True)


def run_hook(command, repo, env_extra=None, unguarded=False):
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo,
               "tool_input": {"command": command}}
    env = dict(os.environ, HARNESS_SLOT_DIR=_SLOT_DIR, HARNESS_NO_PASS_CACHE="1")
    if env_extra:
        env.update(env_extra)
    argv = [sys.executable, "-c", _UNGUARDED, HOOK] if unguarded else [sys.executable, HOOK]
    r = subprocess.run(argv, input=json.dumps(payload),
                        capture_output=True, text=True, timeout=120, env=env, cwd=repo)
    if r.returncode == 2:
        return DENY, r.stderr
    if r.returncode != 0 or (unguarded and "Traceback (most recent call last)" in r.stderr):
        return CRASH, r.stderr[-400:]
    return ALLOW, r.stdout


def case(label, got, expected, needle=""):
    got_verdict, reason = got
    needles = (needle,) if isinstance(needle, str) else needle
    ok = got_verdict == expected and all(not part or part in reason for part in needles)
    if expected == DENY and "no re-runnable proof" in reason:
        ok = (ok and "rules/harness_tooling.md" in reason
              and "add a tracked proof" in reason)
    if expected == DENY and "would revert a peer's commit" in reason:
        ok = (ok and "CLAUDE.md §Core Principles: preserve peer work" in reason
              and "Rebuild it from the current HEAD" in reason)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        return "%s\n     expected=%s got=%s reason=%r" % (label, expected, got_verdict, reason[:300])
    return None


def alt_git(repo, index, args):
    subprocess.run(["git"] + args, cwd=repo, env=dict(os.environ, GIT_INDEX_FILE=index),
                   check=True, capture_output=True)


def same_command_write_cases():
    """Every commit guard reads the index before the command runs, so a git index write earlier in the
    same command lands after they judged it (commit a7ea41286: `git add -- $(cat paths) && git commit`
    hid a doc from doc_proof_advisory). Such a commit is denied with the next step; a write after the
    commit, a read-only git call and a quoted mention are not."""
    failures = []
    repo = make_repo()
    write(repo, "a.txt", "a\n")
    os.makedirs(os.path.join(repo, "sub"), exist_ok=True)
    needles = ("earlier in this command", "its own Bash call, then commit")
    for label, command in (
            ("an add then a commit", "git add -- a.txt && git commit -q -m x"),
            ("the a7ea41286 shape", "git add -- $(cat paths) && git commit -F msg"),
            ("an add then a commit split by `;`", "git add a.txt; git commit -q -m x"),
            ("an add from a subdirectory", "cd sub && git add ../a.txt && cd .. && git commit -q -m x"),
            ("an rm --cached then a commit", "git rm -q --cached README.md && git commit -q -m x"),
            ("a soft reset then a commit", "git reset -q --soft HEAD && git commit -q -m x"),
            ("a stash then a commit", "git stash && git commit -q -m x"),
            ("an add then merge --continue", "git add a.txt && git merge --continue"),
            ("an apply --cached then a commit", "git apply --cached p.diff && git commit -q -m x")):
        failures.append(case("W %s -> deny, naming the next step" % label, run_hook(command, repo), DENY, needles))
    for label, command in (
            ("an add after the commit", "git commit -q --allow-empty -m x && git add a.txt"),
            ("an add with no commit", "git add a.txt"),
            ("stash list then a commit", "git stash list && git commit -q --allow-empty -m x"),
            ("a read-only git call then a commit", "git status --short && git commit -q --allow-empty -m x"),
            ("a plain apply (worktree only) then a commit", "git apply p.diff && git commit -q --allow-empty -m x"),
            ("a quoted mention", "echo 'git add a.txt && git commit -m x'")):
        failures.append(case("W %s -> allow" % label, run_hook(command, repo), ALLOW))
    return failures


def index_cases():
    """The guard judges the index a commit will actually publish.

    G1: an index it cannot read (a `$VAR` it cannot expand, or a file not built yet) made git read an
    EMPTY index, so the guard judged a phantom commit: it allowed a temp-index commit that reverted 69
    files, then blocked the next attempt on another session's README. Unreadable now denies.
    G2: a temp index read from an older HEAD, committed after peers moved HEAD, reverts their files.
    Signature: a staged path whose working tree matches HEAD but whose staged blob does not."""
    failures = []

    # G1 ------------------------------------------------------------------------------------
    repo = make_repo()
    write(repo, "a.txt", "a\n")
    failures.append(case(
        "G1 a PWD-derived GIT_INDEX_FILE that does not exist -> deny",
        run_hook('IDX="$PWD/alt.index" && GIT_INDEX_FILE="$IDX" git commit -F msg', repo),
        DENY, ("GIT_INDEX_FILE", "memory gotcha_concurrent_session_hazards #16",
              "does not exist")))
    failures.append(case(
        "G1 an exported PWD-derived missing GIT_INDEX_FILE -> deny",
        run_hook('export GIT_INDEX_FILE="$PWD/alt.index" && git commit -F msg', repo),
        DENY, ("memory gotcha_concurrent_session_hazards #16", "does not exist")))
    missing = os.path.join(repo, "not_built.index").replace("\\", "/")
    failures.append(case(
        "G1 a literal GIT_INDEX_FILE that does not exist yet -> deny",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % missing, repo),
        DENY, ("does not exist", "memory gotcha_concurrent_session_hazards #16",
              "build the index in an earlier call")))
    built = os.path.join(repo, "built.index").replace("\\", "/")
    alt_git(repo, built, ["read-tree", "HEAD"])
    alt_git(repo, built, ["add", "a.txt"])
    failures.append(case(
        "G1 a literal, built GIT_INDEX_FILE is judged and allowed",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % built, repo), ALLOW))
    failures.append(case(
        "G1 a once-assigned literal variable expands and is allowed",
        run_hook('IDX=%s && GIT_INDEX_FILE="$IDX" git commit -F msg' % built, repo), ALLOW))
    msys = "/" + built[0].lower() + built[2:] if built[1] == ":" else built
    failures.append(case(
        "G1 a Git-Bash /c/... index path resolves",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % msys, repo), ALLOW))

    # G2 ------------------------------------------------------------------------------------
    repo = make_repo()
    write(repo, "peer.txt", "v1\n")
    git(repo, ["add", "peer.txt"])
    git(repo, ["commit", "-q", "-m", "v1"])
    stale = os.path.join(repo, "stale.index").replace("\\", "/")
    alt_git(repo, stale, ["read-tree", "HEAD"])          # built from v1
    write(repo, "peer.txt", "v2\n")                       # a peer commits v2 afterwards
    git(repo, ["commit", "-q", "-am", "v2"])
    write(repo, "mine.txt", "mine\n")
    alt_git(repo, stale, ["add", "mine.txt"])
    failures.append(case(
        "G2 a temp index from an older HEAD would revert a peer's commit -> deny",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % stale, repo), DENY, "peer.txt"))
    # a7221db68 (2026-09-24) reverted 23 peer files through exactly this shape: `2>&1` split into a
    # `2>` pathspec, and a pathspec commit skipped the check.
    failures.append(case(
        "G2 the same stale commit with `2>&1 | tail` -> deny",
        run_hook('cd "%s" && GIT_INDEX_FILE=%s git commit -F msg 2>&1 | tail -4' % (repo, stale), repo),
        DENY, "peer.txt"))
    failures.append(case(
        "G2 the same stale commit with `> log` -> deny",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg > out.log' % stale, repo), DENY, "peer.txt"))

    # d31b4eeb4 (2026-09-24) reverted a peer's committed rows in a file the peer kept appending to, so
    # the working tree no longer matched HEAD; the staged blob was still HEAD's older committed version.
    write(repo, "peer.txt", "v2\nuncommitted\n")
    failures.append(case(
        "G2 a stale index reverting a file with newer uncommitted edits -> deny",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % stale, repo), DENY, "peer.txt"))
    write(repo, "peer.txt", "v2\n")

    fresh = os.path.join(repo, "fresh.index").replace("\\", "/")
    alt_git(repo, fresh, ["read-tree", "HEAD"])
    alt_git(repo, fresh, ["add", "mine.txt"])
    failures.append(case(
        "G2 the same commit from a current-HEAD index -> allow",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % fresh, repo), ALLOW))

    repo = make_repo()
    write(repo, "f.txt", "base\n")
    git(repo, ["add", "f.txt"])
    git(repo, ["commit", "-q", "-m", "base"])
    write(repo, "f.txt", "base\nmine\npeer\n")          # the worktree carries a peer's hunk too
    r = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="base\nmine\n",
                       capture_output=True, text=True, check=True)
    git(repo, ["update-index", "--cacheinfo", "100644,%s,f.txt" % r.stdout.strip()])
    failures.append(case(
        "G2 a HEAD-based blob staged beside a peer's unstaged hunk -> allow",
        run_hook("git commit -F msg", repo), ALLOW))

    repo = make_repo()
    write(repo, "g.txt", "one\n")
    git(repo, ["add", "g.txt"])
    git(repo, ["commit", "-q", "-m", "one"])
    old = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="zero\n",
                         capture_output=True, text=True, check=True).stdout.strip()
    git(repo, ["update-index", "--cacheinfo", "100644,%s,g.txt" % old])
    failures.append(case(
        "G2 the default index staging old content for an unchanged worktree file -> deny",
        run_hook("git commit -F msg", repo), DENY, "g.txt"))
    failures.append(case(
        "G2 a pathspec commit takes the worktree, so it cannot revert -> allow",
        run_hook("git commit -F msg -- g.txt", repo), ALLOW))

    repo = make_repo()
    write(repo, "gone.txt", "old\n")
    git(repo, ["add", "gone.txt"])
    git(repo, ["commit", "-q", "-m", "add gone"])
    stale = os.path.join(repo, "stale.index").replace("\\", "/")
    alt_git(repo, stale, ["read-tree", "HEAD"])          # still holds gone.txt
    git(repo, ["rm", "-q", "gone.txt"])                   # a peer deletes it afterwards
    git(repo, ["commit", "-q", "-m", "peer deletes gone"])
    failures.append(case(
        "G2 a temp index from an older HEAD would re-add a file a peer deleted -> deny",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % stale, repo), DENY, "gone.txt"))

    # 2026-09-24: a session removing a line its own commit added restored the exact blob that
    # commit replaced, and the guard denied it as a peer revert. Own commits carry this session's
    # `Claude-Session` trailer, found through the runtime registry entry for the payload session.
    home = tempfile.mkdtemp()
    os.makedirs(os.path.join(home, ".claude", "sessions"))
    with open(os.path.join(home, ".claude", "sessions", "1.json"), "w", encoding="utf-8") as f:
        json.dump({"pid": 1, "sessionId": "s1", "bridgeSessionId": "session_OWN"}, f)
    own_env = {"HOME": home, "USERPROFILE": home}
    for label, trailer, expected in (
            ("G2 restoring a blob this session's own commit replaced -> allow",
             "Claude-Session: https://claude.ai/code/session_OWN", ALLOW),
            ("G2 restoring a blob a peer's commit replaced (other trailer) -> deny",
             "Claude-Session: https://claude.ai/code/session_PEER", DENY)):
        repo = make_repo()
        write(repo, "m.txt", "base\n")
        git(repo, ["add", "m.txt"])
        git(repo, ["commit", "-q", "-m", "base"])
        base = subprocess.run(["git", "rev-parse", "HEAD:m.txt"], cwd=repo, capture_output=True,
                              text=True, check=True).stdout.strip()
        write(repo, "m.txt", "base\nadded\n")
        git(repo, ["commit", "-q", "-am", "add a line\n\n" + trailer])
        write(repo, "m.txt", "base\nadded\npeer edit\n")   # a peer's unstaged hunk
        git(repo, ["update-index", "--cacheinfo", "100644,%s,m.txt" % base])
        failures.append(case(label, run_hook("git commit -F msg", repo, own_env), expected, "" if expected == ALLOW else "m.txt"))

    # Main restored a file to a blob an earlier main commit had replaced; merging main into
    # a branch that held the middle version staged main's blob, and the guard read that as a peer revert.
    for label, side_tip, expected in (
            ("G2 a merge staging the merged-in parent's own blob -> allow", "A\n", ALLOW),
            ("G2 a merge staging a replaced blob the merged-in parent lacks -> deny", "C\n", DENY)):
        repo = make_repo()
        write(repo, "h.txt", "A\n")
        git(repo, ["add", "h.txt"])
        git(repo, ["commit", "-q", "-m", "A"])
        git(repo, ["checkout", "-q", "-b", "side"])
        write(repo, "h.txt", "B\n")
        git(repo, ["commit", "-q", "-am", "A to B"])         # replaces blob A
        git(repo, ["branch", "branch"])
        write(repo, "h.txt", side_tip)
        git(repo, ["commit", "-q", "-am", "side tip"])
        git(repo, ["checkout", "-q", "branch"])
        write(repo, "other.txt", "x\n")
        git(repo, ["add", "other.txt"])
        git(repo, ["commit", "-q", "-m", "branch work"])
        subprocess.run(["git", "merge", "--no-commit", "--no-ff", "side"], cwd=repo, capture_output=True)
        if side_tip == "C\n":
            blob_a = subprocess.run(["git", "rev-parse", "HEAD~2:h.txt"], cwd=repo,
                                    capture_output=True, text=True, check=True).stdout.strip()
            git(repo, ["update-index", "--cacheinfo", "100644,%s,h.txt" % blob_a])
        failures.append(case(label, run_hook("git commit -F msg", repo), expected,
                             "" if expected == ALLOW else "h.txt"))

    repo = make_repo()
    git(repo, ["rm", "-q", "--cached", "README.md"])
    failures.append(case(
        "G2 untracking a file (`git rm --cached`) is not a revert -> allow",
        run_hook("git commit -F msg", repo), ALLOW))

    failures.extend(base_cases())
    return failures


def private_index(repo, name, base):
    """An `index_commit.py stage` private index: `.git/index-<name>` read from `base`, plus its sidecar."""
    index = os.path.join(repo, ".git", "index-" + name).replace("\\", "/")
    alt_git(repo, index, ["read-tree", base])
    with open(index + ".paths.json", "w", encoding="utf-8") as fh:
        json.dump({"name": name, "base_head": base, "index": "index-" + name, "paths": {}}, fh)
    return index


def base_cases():
    """G3: a private index records the HEAD it was read from; publishing it over any other HEAD loses
    every peer change after that base. Staged deletions are invisible to G2, so a peer-added file was lost."""
    failures = []
    for label, peer in (
            ("G3 a private index over a moved base would delete a peer-added file -> deny", "add"),
            ("G3 a private index over a moved base would revert a peer's modification -> deny", "modify"),
            ("G3 a private index over a moved base would re-add a peer-deleted file -> deny", "delete")):
        repo = make_repo()
        write(repo, "peer.txt", "v1\n")
        git(repo, ["add", "peer.txt"])
        git(repo, ["commit", "-q", "-m", "v1"])
        base = git(repo, ["rev-parse", "HEAD"]).strip()
        index = private_index(repo, "mine", base)
        if peer == "add":
            write(repo, "added.txt", "peer\n")
            git(repo, ["add", "added.txt"])
        elif peer == "modify":
            write(repo, "peer.txt", "v2\n")
            git(repo, ["add", "peer.txt"])
        else:
            git(repo, ["rm", "-q", "peer.txt"])
        git(repo, ["commit", "-q", "-m", "peer " + peer])
        write(repo, "mine.txt", "mine\n")
        alt_git(repo, index, ["add", "mine.txt"])
        failures.append(case(label, run_hook('GIT_INDEX_FILE=%s git commit -F msg' % index, repo),
                             DENY, ("index_commit", base[:9])))

    repo = make_repo()
    index = private_index(repo, "fresh", git(repo, ["rev-parse", "HEAD"]).strip())
    write(repo, "mine.txt", "mine\n")
    alt_git(repo, index, ["add", "mine.txt"])
    failures.append(case(
        "G3 a private index whose base is the current HEAD -> allow",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % index, repo), ALLOW))
    with open(index + ".paths.json", "w", encoding="utf-8") as fh:
        fh.write("{not json")
    failures.append(case(
        "G3 an unreadable stage record beside the index -> deny",
        run_hook('GIT_INDEX_FILE=%s git commit -F msg' % index, repo), DENY, "index_commit"))
    return failures


def conflict_merge(resolution):
    """A repo stopped on a conflicting merge of a hook both sides edited, resolved to `resolution`:
    "theirs" takes the incoming side, anything else is written as authored content."""
    repo = make_repo()
    write(repo, ".claude/hooks/h.py", "h = 0\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "base hook"])
    git(repo, ["checkout", "-q", "-b", "upstream"])
    write(repo, ".claude/hooks/h.py", "h = 2  # upstream\n")
    git(repo, ["commit", "-q", "-am", "upstream"])
    git(repo, ["checkout", "-q", "-"])
    write(repo, ".claude/hooks/h.py", "h = 1  # work\n")
    git(repo, ["commit", "-q", "-am", "work"])
    subprocess.run(["git", "merge", "-q", "upstream"], cwd=repo, capture_output=True)
    if resolution == "theirs":
        git(repo, ["checkout", "--theirs", "--", ".claude/hooks/h.py"])
    else:
        write(repo, ".claude/hooks/h.py", resolution)
    git(repo, ["add", ".claude/hooks/h.py"])
    return repo


def staged_blob_cases():
    """A commit's scope includes the blobs its argument shape publishes."""
    failures = []
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "print('ok')\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "hook"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    git(repo, ["add", ".claude/hooks/x.py"])
    write(repo, ".claude/hooks/x.py", "x = 2\npeer = 1\n")
    failures.append(case(
        "S1 a proved staged hook beside a peer's working hunk -> allow",
        run_hook("git commit -F msg", repo), ALLOW))
    for label, command in (
            ('S4 a pathspec commit of a proved hook', 'git commit -F msg -- .claude/hooks/x.py'),
            ('S5 -a publishes a proved working hook', 'git commit -a -F msg')):
        failures.append(case(label + ' -> allow', run_hook(command, repo), ALLOW))
    write(repo, "spec.txt", ".claude/hooks/x.py\n")
    failures.append(case(
        "S7 -i --pathspec-from-file includes a proved hook -> allow",
        run_hook("git commit -i -F msg --pathspec-from-file=spec.txt", repo), ALLOW))
    private = os.path.join(repo, "private.idx")
    alt_git(repo, private, ["read-tree", "HEAD"])
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="x = 3\n",
                          capture_output=True, text=True, check=True).stdout.strip()
    alt_git(repo, private, ["update-index", "--cacheinfo", "100644,%s,.claude/hooks/x.py" % blob])
    failures.append(case(
        "S6 a private index with a new proved hook blob -> allow",
        run_hook('GIT_INDEX_FILE="%s" git commit -F msg' % private.replace("\\", "/"), repo), ALLOW))
    return failures


def must_allow_proof():
    """The justified operations this floor guard sits in front of never deny (`_must_allow_cases`),
    and `merge --continue` is judged on what the resolution authors."""
    failures = []
    for planted in (None, HARNESS_PLANTED):
        allow_cases, cleanup = must_allow_cases("git_guardrails.py", planted=planted)
        try:
            for label, payload, cwd, expected in allow_cases:
                failures.append(case(label, run_hook(payload["tool_input"]["command"], cwd, unguarded=True),
                                     expected))
        finally:
            cleanup()
        left = [cwd for _label, _payload, cwd, _expected in allow_cases if os.path.exists(cwd)]
        print("%-4s the must-allow fixtures are removed by their cleanup" % ("FAIL" if left else "ok"))
        failures.append("must-allow fixtures left behind: %r" % left if left else None)
    failures.append(case(
        "M2 `git merge --continue` after a hook conflict resolved to the incoming side -> allow",
        run_hook("git merge --continue", conflict_merge("theirs"), unguarded=True), ALLOW))
    failures.append(case(
        "M2 block arm: the same conflict resolved to new hook content -> deny",
        run_hook("git merge --continue", conflict_merge("h = 3  # authored in the merge\n"), unguarded=True),
        DENY, "BLOCKED harness commit"))
    return failures


def push_base_cases():
    failures = []
    repo = make_repo()
    write(repo, '.claude/hooks/base.py', 'BASE = 1\n')
    git(repo, ['add', '-A'])
    git(repo, ['commit', '-qm', 'existing harness'])
    base = git(repo, ['rev-parse', 'HEAD']).strip()
    git(repo, ['update-ref', 'refs/remotes/origin/main', base])
    write(repo, 'README.md', 'app only\n')
    git(repo, ['commit', '-qam', 'app only'])
    failures.append(case('unpublished ref with only app changes does not count inherited harness paths',
                         run_hook('git push origin HEAD:new', repo), ALLOW))
    write(repo, '.claude/hooks/base.py', 'BASE = 2\n')
    git(repo, ['commit', '-qam', 'new harness'])
    failures.append(case('unpublished ref with new harness changes still needs close',
                         run_hook('git push origin HEAD:new', repo), DENY, 'harness_tests.py --close'))
    head = git(repo, ['rev-parse', 'HEAD']).strip()
    stamp = os.path.join(repo, 'stamp.json')
    with open(stamp, 'w', encoding='utf-8') as source:
        json.dump({'close': {'head': head, 'base': base, 'verdict': 'green', 'red': [],
                             'paths': ['.claude/hooks/base.py']}}, source)
    failures.append(case('unpublished ref accepts close coverage of only its outgoing harness changes',
                         run_hook('git push origin HEAD:new', repo, {'HARNESS_TEST_STAMP': stamp}), ALLOW))
    return failures


def test_string_constants_advise_but_branch_denies():
    repo = make_repo()
    path = ".claude/tools/unproven.py"
    write(repo, path, 'MESSAGE = "old"\nPATTERN = r"^old$"\n')
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed tool"])
    try:
        for content in ('MESSAGE = "new"\nPATTERN = r"^old$"\n',
                        'MESSAGE = "old"\nPATTERN = r"^new$"\n'):
            write(repo, path, content)
            git(repo, ["add", path])
            verdict, output = run_hook("git commit -F msg -- " + path, repo)
            assert verdict == ALLOW, output
            context = json.loads(output)["hookSpecificOutput"]["additionalContext"]
            assert "if a changed string is a pattern" in context
        write(repo, path, 'MESSAGE = "old"\nPATTERN = r"^old$"\nif MESSAGE:\n    print(MESSAGE)\n')
        git(repo, ["add", path])
        verdict, output = run_hook("git commit -F msg -- " + path, repo)
        assert verdict == DENY and "no re-runnable proof" in output
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def commit_proof_cases():
    failures = []
    for label, times, codes, staged, expected_run, needles in (
            ("red doc proof advises without denying", [0.1], [1], True, [0],
             ("test_bound_0.py", "FAIL planted doc contract", "fixed before the close")),
            ("green doc proof has no failure advisory", [0.1], [0], True, [0], ()),
            ("unavailable doc proof advises without denying", [0.1], [2], True, [0],
             ("proof run incomplete", "test_bound_0.py", "unproven, not passed",
              "--proofs-for .claude/reference/planted_contract.md")),
            ("over-inner-budget proof stays at close", [10.1], [1], True, [],
             ("1 bound proof(s) left to the close", "unproven, not passed")),
            ("unrecorded proof stays at close", [None], [1], True, [],
             ("1 bound proof(s) left to the close",)),
            ("total cap stops adding even a later smaller proof", [10, 10, 10, 1, 0.1],
             [0] * 5, True, [0, 1, 2], ("2 bound proof(s) left to the close",)),
            ("non-harness commit runs no proof", [0.1], [1], False, [], ())):
        repo = make_repo()
        try:
            doc = ".claude/reference/planted_contract.md"
            write(repo, doc, "old\n")
            for i, code in enumerate(codes):
                write(repo, ".claude/tests/test_bound_%d.py" % i,
                      "# planted_contract.md\nimport pathlib, sys\n"
                      "pathlib.Path(%r).write_text('ran')\n"
                      "print('preamble')\nprint('FAIL planted doc contract')\nsys.exit(%d)\n"
                      % (os.path.join(repo, "ran_%d" % i), code))
            git(repo, ["add", "-A"])
            git(repo, ["commit", "-qm", "planted inputs"])
            write(repo, doc, "new\n")
            write(repo, "README.md", "new\n")
            git(repo, ["add", doc if staged else "README.md"])
            stamp = os.path.join(repo, ".claude/logs/harness_tests_stamp.json")
            # Only the reuse case needs the nested runner's pass cache; with it on, the nested proof records into its
            # own read log and the outer run sees an unrecorded child.
            cache_off = "" if label == "green doc proof has no failure advisory" else "1"
            write(repo, ".claude/logs/harness_proof_durations.json",
                  json.dumps({"test_bound_%d.py" % i: t for i, t in enumerate(times) if t is not None}))
            got = run_hook("git commit -F msg", repo, {"HARNESS_TEST_STAMP": stamp, "HARNESS_NO_PASS_CACHE": cache_off}, unguarded=True)
            ran = [i for i in range(len(codes)) if os.path.exists(os.path.join(repo, "ran_%d" % i))]
            if ran != expected_run or (not any(codes) and "fixed before the close" in got[1]):
                got = (CRASH, "ran=%r expected=%r output=%r" % (ran, expected_run, got[1]))
            failures.append(case("C " + label, got, ALLOW, needles))
            if label == "green doc proof has no failure advisory":
                os.remove(os.path.join(repo, "ran_0"))
                reused = run_hook("git commit -F msg", repo, {"HARNESS_TEST_STAMP": stamp, "HARNESS_NO_PASS_CACHE": cache_off}, unguarded=True)
                if os.path.exists(os.path.join(repo, "ran_0")):
                    reused = (CRASH, "unchanged passing proof ran again")
                failures.append(case("C unchanged pass costs no proof run", reused, ALLOW))
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    return failures


def inline_cases():
    failures = []

    # 0. A Git-Bash `cd /c/...` prefix must resolve. `os.path.join` on Windows turns the MSYS
    #    drive form into `<cwd>\c\Users\...`, a path that does not exist -- so `git rev-parse`
    #    failed there and the guard BLOCKED a commit for a repo it had merely failed to find.
    #    Both halves are asserted: the MSYS form resolves, and a genuinely bad path still denies.
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "Tests/Foo.cs"])
    # The MSYS drive form exists only on Windows; elsewhere the plain path is the shell's own form.
    msys = "/" + repo[0].lower() + repo[2:].replace("\\", "/") if os.name == "nt" else repo
    failures.append(case(
        "a `cd /c/...` prefix resolves to the repo, so a clean commit is not blocked",
        run_hook("cd %s; git commit -F msg -- Tests/Foo.cs" % msys, repo), ALLOW))

    # 0b. `git -C /c/...` is the same MSYS form through the other retarget route.
    failures.append(case(
        "a `git -C /c/...` operand resolves to the repo, so a clean commit is not blocked",
        run_hook("git -C %s commit -F msg -- Tests/Foo.cs" % msys, repo), ALLOW))

    # 0c. A target named through a variable this command assigns resolves like the literal.
    #     Unresolvable targets still deny.
    failures.append(case(
        "`D=<repo>; cd $D; git commit` resolves the variable -> allow",
        run_hook('D=%s; cd $D; git commit -F msg -- Tests/Foo.cs' % msys, repo), ALLOW))
    failures.append(case(
        "`W=\"<repo>\"; git -C \"$W\" commit` resolves the variable -> allow",
        run_hook('W="%s"; git -C "$W" commit -F msg -- Tests/Foo.cs' % repo.replace("\\", "/"), repo), ALLOW))
    failures.append(case(
        "`git -C $UNSET commit` stays unresolvable -> deny",
        run_hook('git -C $UNSET commit -F msg', repo), DENY, "rev-parse"))
    fresh = os.path.join(tempfile.mkdtemp(prefix="ggfresh_"), "probe").replace("\\", "/")
    failures.append(case(
        "a repo this command creates with `git init` skips the existing-index checks -> allow",
        run_hook('D=%s; mkdir -p $D && cd $D && git init -q; git add f && git commit -qm i' % fresh, repo),
        ALLOW))

    # 1. Commit of a non-harness path -> allow.
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "Tests/Foo.cs"])
    failures.append(case(
        "commit of Tests/Foo.cs only -> allow",
        run_hook('git commit -F msg -- Tests/Foo.cs', repo), ALLOW))

    # 2. Staged hook and tool with tracked proofs -> allow.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tools/y.py", "y = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    write(repo, ".claude/tests/y_test.py", "# proof\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        'staged hook and tool with tracked proofs -> allow',
        run_hook('git commit -F msg -- .claude', repo), ALLOW))
    # 2b. `instruction_quality` before a harness edit is enforced at the Write|Edit call
    #     (harness_edit_skill_reminder.py), which any other write route dodges: a Bash heredoc,
    #     `sed -i`, a python script run through Bash. The staged set is the one signal no route can
    #     dodge, so the commit is the backstop. Boundary: with NO session state file there is no
    #     session to judge -- CI runs this battery that way -- so the rule stays silent there.
    skill_state = tempfile.mkdtemp(prefix="ggskill_")
    skill_env = {"HARNESS_HOOK_STATE_DIR": skill_state}

    def mark_loaded(skills):
        with open(os.path.join(skill_state, "s1.json"), "w", encoding="utf-8") as fh:
            json.dump({"skills_loaded": skills}, fh)

    mark_loaded(["something_else"])
    failures.append(case(
        "staged harness commit, skill not loaded -> allow with an advisory naming instruction_quality",
        run_hook('git commit -F msg -- .claude', repo, skill_env), ALLOW,
        ("instruction_quality", "rules/harness_tooling.md", "Load the skill, check the edits")))
    mark_loaded(["instruction_quality"])
    failures.append(case(
        "staged harness commit, skill loaded -> allow",
        run_hook('git commit -F msg -- .claude', repo, skill_env), ALLOW))
    failures.append(case(
        "no session state at all (CI shape) -> the skill rule stays silent",
        run_hook('git commit -F msg -- .claude', repo,
                 {"HARNESS_HOOK_STATE_DIR": tempfile.mkdtemp(prefix="ggnosess_")}), ALLOW))
    write(repo, ".claude/hooks/guide.md", "# guide\n")
    git(repo, ["add", ".claude/hooks/guide.md"])
    mark_loaded(["something_else"])
    failures.append(case(
        "a staged harness .md without instruction_quality is allowed with an advisory citing the skill",
        run_hook('git commit -F msg -- .claude', repo, skill_env), ALLOW,
        ("skills/instruction_quality/SKILL.md", "Load the skill, check the edits")))
    mark_loaded(["instruction_quality"])
    write(repo, ".claude/hooks/guide.md", "# guide\n\n**A packed rule.** " + ("padding words " * 40) + "\n")
    git(repo, ["add", ".claude/hooks/guide.md"])
    failures.append(case(
        "a staged .md unit over the audit trigger is allowed with an advisory, never denied",
        run_hook('git commit -F msg -- .claude', repo, skill_env), ALLOW,
        ("audit trigger", ".claude/hooks/guide.md")))
    # 2c. The skill advisory names harness surfaces: a plan, a memory or the worklog mirror is not one
    #     (harness_edit_skill_reminder.is_harness_file), though the commit still runs its bound proofs.
    mark_loaded(["something_else"])
    for rel in (".claude/plans/p.md", ".claude/auto-memory/m.md", ".claude/worklog-titles.md"):
        repo = make_repo()
        write(repo, rel, "# note\n")
        git(repo, ["add", "-A"])
        verdict, reason = run_hook('git commit -F msg -- .claude', repo, skill_env)
        ok = verdict == ALLOW and "was never loaded" not in reason
        print("%-4s %s" % ("ok" if ok else "FAIL", "a staged %s alone gets no skill advisory" % rel))
        failures.append(None if ok else "%s: verdict=%s reason=%r" % (rel, verdict, reason[:300]))
    repo = make_repo()
    write(repo, ".claude/skills/s/SKILL.md", "# skill\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        "a staged skill doc without instruction_quality gets the advisory citing the skill",
        run_hook('git commit -F msg -- .claude', repo, skill_env), ALLOW,
        ("skills/instruction_quality/SKILL.md", ".claude/skills/s/SKILL.md")))

    # 4. staged hook without a proof -> deny naming the expected proof path.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        "staged hook without proof -> deny naming tests/test_x*.py",
        run_hook('git commit -F msg -- .claude', repo),
        DENY, "tests/test_x*.py"))

    # 5. staged tool without a proof -> deny.
    repo = make_repo()
    write(repo, ".claude/tools/y.py", "y = 1\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        "staged tool without proof -> deny naming tests/test_y*.py",
        run_hook('git commit -F msg -- .claude', repo),
        DENY, "tests/test_y*.py"))

    # 6. git show naming a hook path is read-only, never the commit branch -> allow.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "add hook"])
    failures.append(case(
        "git show HEAD:.claude/hooks/x.py -> allow",
        run_hook("git show HEAD:.claude/hooks/x.py", repo), ALLOW))

    # 7. -a form reaches the same verdict as an explicit pathspec.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed hook"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")  # unstaged dirty edit
    failures.append(case('-a form includes a dirty proved hook -> allow',
                         run_hook('git commit -a -F msg', repo), ALLOW))
    failures.append(case(
        "explicit pathspec form after the same edit -> same allow verdict",
        run_hook('git commit -F msg -- .claude/hooks/x.py', repo),
        ALLOW))

    # 8. git failure (cwd not a repo) -> deny.
    not_repo = tempfile.mkdtemp(prefix="ggharness_norepo_")
    write(not_repo, ".claude/hooks/x.py", "x = 1\n")
    failures.append(case(
        "cwd is not a git repo -> deny",
        run_hook('git commit -F msg -- .claude', not_repo), DENY))

    # 9. The explicit harness-repair bypass skips the tracked-proof check.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        "HARNESS_ALLOW_UNSTAMPED_HARNESS=1 -> allow with no proof",
        run_hook('git commit -F msg -- .claude', repo,
                  {"HARNESS_ALLOW_UNSTAMPED_HARNESS": "1"}),
        ALLOW))

    # 10. directory pathspec: an untouched tracked hook without a proof is NOT demanded;
    #     a changed untested hook under the same pathspec still is.
    repo = make_repo()
    write(repo, ".claude/hooks/untouched.py", "u = 1\n")
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed hooks"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    git(repo, ["add", ".claude/hooks/x.py"])
    failures.append(case(
        "dir pathspec: untouched untested hook not demanded -> allow",
        run_hook('git commit -F msg -- .claude/hooks', repo),
        ALLOW))
    write(repo, ".claude/hooks/untouched.py", "u = 2\n")
    failures.append(case(
        "dir pathspec: changed untested hook -> deny naming tests/test_untouched*.py",
        run_hook('git commit -F msg -- .claude/hooks', repo),
        DENY, "tests/test_untouched*.py"))

    # 11. combined short flags: `-am` must read as `-a` (dirty worktree edit committed).
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed hook"])
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    failures.append(case('-am includes a dirty proved hook -> allow',
                         run_hook('git commit -am "tweak hook"', repo), ALLOW))

    # 12. --amend republishes HEAD's harness paths even with a clean index.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "add hook"])
    failures.append(case('--amend --no-edit of a proved hook -> allow',
                         run_hook('git commit --amend --no-edit', repo), ALLOW))

    # 13. A broken runner import leaves the commit proof-presence and force-push checks live.
    broken_root = tempfile.mkdtemp(prefix="ggharness_broken_")
    broken_hooks = os.path.join(broken_root, ".claude", "hooks")
    os.makedirs(broken_hooks)
    for sibling in ("git_guardrails.py", "_hook_state.py", "_git_commit.py", "_command_text.py",
                    "_commit_scope.py"):
        shutil.copy(os.path.join(_CLAUDE_DIR, "hooks", sibling), os.path.join(broken_hooks, sibling))
    install_nested_parser(broken_hooks)
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo,
               "tool_input": {"command": "git commit -F msg -- .claude"}}
    r = subprocess.run([sys.executable, os.path.join(broken_hooks, "git_guardrails.py")],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=repo)
    got = (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)
    failures.append(case("broken runner still denies a missing tracked proof", got, DENY, "no re-runnable proof"))
    write(repo, ".claude/reference/repair.md", "repair\n")
    git(repo, ["add", ".claude/reference/repair.md"])
    payload["tool_input"]["command"] = "git commit -F msg -- .claude/reference/repair.md"
    r = subprocess.run([sys.executable, os.path.join(broken_hooks, "git_guardrails.py")],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=repo)
    got = (DENY, r.stderr) if r.returncode == 2 else (CRASH, r.stderr) if r.returncode else (ALLOW, r.stdout)
    failures.append(case("runner import error is advisory on an authored doc commit", got, ALLOW,
                         ("bound proofs could not run", "runner import error", "unproven, not passed")))
    write(repo, "README.md", "seed2\n")
    git(repo, ["add", "-A"])
    payload["tool_input"]["command"] = "git push --force"
    r = subprocess.run([sys.executable, os.path.join(broken_hooks, "git_guardrails.py")],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=repo)
    got = (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)
    failures.append(case("broken import still blocks force-push", got, DENY, "force-push"))

    # 13b. The merge-scope helper is guarded the same way: without it the guard cannot tell whether
    #      an invocation commits the index or what a merge authored, so it denies and names the
    #      repair. Its other verdicts (force-push) must survive the broken import.
    scope_hooks = os.path.join(tempfile.mkdtemp(prefix="ggharness_scope_"), ".claude", "hooks")
    os.makedirs(scope_hooks)
    for sibling in ("git_guardrails.py", "_hook_state.py", "_git_commit.py", "_command_text.py"):
        shutil.copy(os.path.join(_CLAUDE_DIR, "hooks", sibling), os.path.join(scope_hooks, sibling))
    install_nested_parser(scope_hooks)
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    for command, expected, needle, label in (
        ("git commit -F msg -- .claude", DENY, ("merge-scope state unavailable", "hooks/_commit_scope.py"),
         "missing _commit_scope -> deny naming the repair, not a crash"),
        ("git push --force", DENY, "force-push",
         "missing _commit_scope still blocks force-push"),
    ):
        r = subprocess.run([sys.executable, os.path.join(scope_hooks, "git_guardrails.py")],
                           input=json.dumps({"tool_name": "Bash", "session_id": "s1", "cwd": repo,
                                             "tool_input": {"command": command}}),
                           capture_output=True, text=True, timeout=30, cwd=repo)
        got = (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)
        failures.append(case(label, got, expected, needle))

    # 14. A heredoc BODY quoting a destructive command is data, not a command -> allow.
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "Tests/Foo.cs"])
    heredoc = ("cat > msg <<'EOF'\nfix(guard): deny git reset --hard and git commit -am bypass\nEOF\n"
               "git commit -F msg -- Tests/Foo.cs")
    failures.append(case(
        "heredoc body quoting `git reset --hard` -> allow (body is data)",
        run_hook(heredoc, repo), ALLOW))
    failures.append(case(
        "the same text OUTSIDE a heredoc still denies",
        run_hook("git reset --hard", repo), DENY, "reset --hard"))

    # 15. The harness check runs against the repo the commit TARGETS, not the payload cwd.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    elsewhere = make_repo()
    failures.append(case('git -C <repo> judges a proved hook in the target checkout -> allow',
                         run_hook('git -C "%s" commit -F msg -- .claude' % repo, elsewhere), ALLOW))
    failures.append(case('cd <repo> judges a proved hook in the target checkout -> allow',
                         run_hook('cd "%s" && git commit -F msg -- .claude' % repo, elsewhere), ALLOW))

    # 16. A proof on disk that git does not track satisfies nothing (.claude/tests/ is gitignored).
    repo = make_repo()
    write(repo, ".gitignore", ".claude/tests/\n")
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "# proof\n")
    git(repo, ["add", "-A"])   # the ignore rule keeps the proof out of the index
    failures.append(case(
        "gitignored, untracked proof -> deny naming tests/test_x*.py",
        run_hook('git commit -F msg -- .claude', repo),
        DENY, "tests/test_x*.py"))
    git(repo, ["add", "-f", ".claude/tests/test_x_ok.py"])
    failures.append(case(
        "the same proof after `git add -f` -> allow",
        run_hook('git commit -F msg -- .claude', repo),
        ALLOW))

    # 17. The bypass works in the shape the deny text names: an inline env prefix on the command.
    repo = make_repo()
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    git(repo, ["add", "-A"])
    failures.append(case(
        "inline HARNESS_ALLOW_UNSTAMPED_HARNESS=1 prefix -> allow with no proof",
        run_hook('HARNESS_ALLOW_UNSTAMPED_HARNESS=1 git commit -F msg -- .claude', repo),
        ALLOW))

    # 18. Without `_hook_state` the guard cannot name the harness set: every commit denies,
    #     the message names the repair, and the inline bypass still reaches it.
    lone_root = tempfile.mkdtemp(prefix="ggharness_lone_")
    lone_hooks = os.path.join(lone_root, ".claude", "hooks")
    os.makedirs(lone_hooks)
    for sibling in ("git_guardrails.py", "_git_commit.py", "_command_text.py"):   # no _hook_state.py
        shutil.copy(os.path.join(_CLAUDE_DIR, "hooks", sibling), os.path.join(lone_hooks, sibling))
    install_nested_parser(lone_hooks)
    repo = make_repo()
    write(repo, "Tests/Foo.cs", "class Foo {}\n")
    git(repo, ["add", "-A"])
    for command, expected, needle, label in (
        ("git commit -F msg -- Tests/Foo.cs", DENY, "guard state unavailable",
         "missing _hook_state -> even a non-harness commit denies, naming the repair"),
        ("HARNESS_ALLOW_UNSTAMPED_HARNESS=1 git commit -F msg -- Tests/Foo.cs", ALLOW, "",
         "missing _hook_state -> inline bypass still allows"),
    ):
        payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo,
                   "tool_input": {"command": command}}
        r = subprocess.run([sys.executable, os.path.join(lone_hooks, "git_guardrails.py")],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=repo)
        got = (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)
        failures.append(case(label, got, expected, needle))

    # 20. Merge and cherry-pick do not re-author source content. The commit closing a
    #     --no-commit merge is judged only on what it authors beyond MERGE_HEAD.
    repo = make_repo()
    git(repo, ["checkout", "-q", "-b", "feature"])
    write(repo, ".claude/hooks/h.py", "h = 1\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "feature hook"])
    git(repo, ["checkout", "-q", "-b", "docs-only"])
    git(repo, ["reset", "-q", "--hard", "HEAD~1"])
    write(repo, "README.md", "docs\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "docs"])
    default = git(repo, ["rev-parse", "--abbrev-ref", "HEAD~1"]).strip()
    git(repo, ["checkout", "-q", git(repo, ["log", "--format=%H", "-1", "HEAD~1"]).strip()])
    git(repo, ["checkout", "-q", "-b", "trunk"])
    failures.append(case(
        "git merge <branch carrying a hook> -> allow (judged when committed on the branch)",
        run_hook("git merge feature", repo), ALLOW))
    failures.append(case(
        "git cherry-pick <rev carrying a hook> -> allow",
        run_hook("git cherry-pick feature", repo), ALLOW))
    failures.append(case(
        "git merge <branch with no harness content> -> allow",
        run_hook("git merge docs-only", repo), ALLOW))
    git(repo, ["merge", "-q", "--no-commit", "--no-ff", "feature"])
    failures.append(case(
        "commit closing a merge whose harness files equal MERGE_HEAD -> allow",
        run_hook("git commit -F msg", repo), ALLOW))
    write(repo, ".claude/hooks/h.py", "h = 2  # edited during the merge\n")
    git(repo, ["add", ".claude/hooks/h.py"])
    failures.append(case(
        "commit closing a merge with an unproved hook edited beyond MERGE_HEAD -> deny",
        run_hook("git commit -F msg", repo), DENY, "BLOCKED harness commit"))
    git(repo, ["merge", "--abort"])

    # Live block 2026-09-14: a pathspec commit publishes only its pathspec (git's default --only
    # mode) and other staged paths stay staged, yet the guard judged the whole index.
    repo = make_repo()
    write(repo, ".claude/hooks/unproven.py", "x = 1\n")
    write(repo, "docs/note.md", "note\n")
    write(repo, "docs_list.txt", "docs/note.md\n")
    write(repo, "hook_list.txt", ".claude/hooks/unproven.py\n")
    git(repo, ["add", ".claude/hooks/unproven.py", "docs/note.md"])
    failures.append(case(
        "commit -- <docs> with an unproven hook staged elsewhere -> allow",
        run_hook("git commit -F msg -- docs/note.md", repo), ALLOW))
    failures.append(case(
        "commit --pathspec-from-file=<docs list> -> allow",
        run_hook("git commit -F msg --pathspec-from-file=docs_list.txt", repo), ALLOW))
    failures.append(case(
        "commit --pathspec-from-file <list naming the hook> -> deny",
        run_hook("git commit -F msg --pathspec-from-file hook_list.txt", repo), DENY))
    failures.append(case(
        "commit -i -- <docs> also publishes the staged hook -> deny",
        run_hook("git commit -i -F msg -- docs/note.md", repo), DENY))
    failures.append(case(
        "commit --pathspec-from-file=- (stdin cannot be read here) -> deny",
        run_hook("git commit -F msg --pathspec-from-file=-", repo), DENY))
    failures.append(case(
        "plain commit with the unproven hook staged -> deny",
        run_hook("git commit -F msg", repo), DENY))

    # Live block 2026-09-14: a removed hook was asked for a proof, which leaves in the same commit.
    repo = make_repo()
    write(repo, ".claude/hooks/gone.py", "x = 1\n")
    write(repo, ".claude/tests/test_gone.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "add gone"])
    git(repo, ["rm", "-q", ".claude/hooks/gone.py", ".claude/tests/test_gone.py"])
    failures.append(case(
        "staged deletion of a hook and its proof -> allow",
        run_hook("git commit -F msg -- .claude", repo), ALLOW))
    repo = make_repo()
    write(repo, ".claude/hooks/index_only.py", "x = 1\n")
    git(repo, ["add", "-A"])
    os.remove(os.path.join(repo, ".claude", "hooks", "index_only.py"))
    failures.append(case(
        "a hook still in the index but gone from the worktree needs its proof -> deny",
        run_hook("git commit -F msg", repo), DENY,
        "no re-runnable proof"))

    # A proof guards behavior. A docstring- or comment-only edit to an unproven hook changes none,
    # so demanding a new proof file for it is friction (live block 2026-09-23 on json_merge.py).
    repo = make_repo()
    write(repo, ".claude/hooks/legacy.py", '"""Old doc."""\n\ndef f(x):\n    return x + 1\n')
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "add legacy"])
    write(repo, ".claude/hooks/legacy.py",
          '"""New doc, naming no deleted caller."""\n\ndef f(x):\n    # comment\n    return x + 1\n')
    git(repo, ["add", "-A"])
    failures.append(case(
        "docstring/comment-only edit to an unproven hook -> allow",
        run_hook("git commit -F msg", repo), ALLOW))
    write(repo, ".claude/hooks/legacy.py", '"""Doc only."""\n\ndef f(x):\n    return x + 1\n')
    git(repo, ["add", "-A"])
    write(repo, ".claude/hooks/legacy.py", '"""Doc only."""\n\ndef f(x):\n    return x + 3\n')
    failures.append(case(
        "index docstring-only but worktree code change, pathspec commit -> deny",
        run_hook("git commit -F msg -- .claude/hooks/legacy.py", repo),
        DENY, "no re-runnable proof"))
    doc_repo = make_repo()
    write(doc_repo, ".claude/hooks/usage.py", '"""Usage: run me."""\nimport sys\nsys.exit(__doc__)\n')
    git(doc_repo, ["add", "-A"])
    git(doc_repo, ["commit", "-q", "-m", "add usage"])
    write(doc_repo, ".claude/hooks/usage.py", '"""Usage: changed."""\nimport sys\nsys.exit(__doc__)\n')
    git(doc_repo, ["add", "-A"])
    failures.append(case(
        "a docstring edit in a file that reads __doc__ is behavior -> deny",
        run_hook("git commit -F msg", doc_repo), DENY,
        "no re-runnable proof"))
    write(repo, ".claude/hooks/legacy.py", '"""New doc."""\n\ndef f(x):\n    return x + 2\n')
    git(repo, ["add", "-A"])
    failures.append(case(
        "a code change to an unproven hook still needs its proof -> deny",
        run_hook("git commit -F msg", repo), DENY,
        "no re-runnable proof"))
    write(repo, ".claude/hooks/legacy.py", '"""New doc."""\n\ndef f(x):\n    return x +\n')
    git(repo, ["add", "-A"])
    failures.append(case(
        "an unparseable staged hook needs its proof -> deny",
        run_hook("git commit -F msg", repo), DENY,
        "no re-runnable proof"))

    # A commit through another index is judged against THAT index. The guard read the default
    # index, found no harness path staged there and allowed an unstamped, unproven hook commit.
    repo = make_repo()
    write(repo, ".claude/hooks/alt_index_hook.py", "x = 1\n")
    alt_index = os.path.join(repo, "alt.index").replace("\\", "/")
    alt_env = dict(os.environ, GIT_INDEX_FILE=alt_index)
    subprocess.run(["git", "read-tree", "HEAD"], cwd=repo, env=alt_env, check=True, capture_output=True)
    subprocess.run(["git", "add", ".claude/hooks/alt_index_hook.py"], cwd=repo, env=alt_env,
                   check=True, capture_output=True)
    failures.append(case(
        "inline GIT_INDEX_FILE staging an unproven hook -> deny",
        run_hook("GIT_INDEX_FILE=%s git commit -F msg" % alt_index, repo), DENY))
    failures.append(case(
        "exported GIT_INDEX_FILE staging an unproven hook -> deny",
        run_hook("export GIT_INDEX_FILE=%s && git commit -F msg" % alt_index, repo), DENY))
    failures.append(case(
        "the default index, with nothing staged, still allows",
        run_hook("git commit -F msg", repo), ALLOW))
    # 21. A settings surface takes the same skill-load check as harness code.
    repo = make_repo()
    write(repo, ".claude/settings.base.json", '{"permissions": {"allow": []}}\n')
    git(repo, ["add", "-A"])
    mark_loaded(['something_else'])
    failures.append(case('settings.base.json without instruction_quality -> allow with the advisory',
                         run_hook('git commit -F msg -- .claude/settings.base.json', repo, skill_env),
                         ALLOW, 'instruction_quality'))
    mark_loaded(['instruction_quality'])
    failures.append(case('settings.base.json with instruction_quality -> allow',
                         run_hook('git commit -F msg -- .claude/settings.base.json', repo, skill_env), ALLOW))
    return failures


def string_constants_cases():
    test_string_constants_advise_but_branch_denies()
    return []


def retargeted_merge_cases():
    """A merge, cherry-pick or revert addressed to another repository (`-C`, `cd`) is scoped in that
    repository, including a ref an earlier `fetch` of the same command creates there; a ref that
    does not exist in the target keeps failing closed."""
    failures = []
    base = tempfile.mkdtemp(prefix="ggretarget_")
    try:
        parent = os.path.join(base, "parent")
        sub = os.path.join(parent, "sub")
        other = os.path.join(base, "other")
        for repo in (sub, other):
            os.makedirs(repo)
            git(repo, ["init", "-q"])
            git(repo, ["config", "user.email", "t@t.example"])
            git(repo, ["config", "user.name", "t"])
        write(sub, "a.txt", "1\n")
        git(sub, ["add", "-A"])
        git(sub, ["commit", "-q", "-m", "seed"])
        git(sub, ["branch", "feat"])
        git(sub, ["checkout", "-q", "feat"])
        write(sub, ".claude/hooks/sub_hook.py", "x = 1\n")
        git(sub, ["add", "-A"])
        git(sub, ["commit", "-q", "-m", "feat"])
        git(sub, ["checkout", "-q", "-"])
        git(other, ["fetch", "-q", sub, "HEAD:main"])
        git(other, ["checkout", "-q", "main"])
        git(other, ["checkout", "-q", "-b", "x"])
        write(other, ".claude/hooks/other_hook.py", "y = 1\n")
        git(other, ["add", "-A"])
        git(other, ["commit", "-q", "-m", "x"])
        git(parent, ["init", "-q"])
        git(parent, ["config", "user.email", "t@t.example"])
        git(parent, ["config", "user.name", "t"])
        write(parent, "README.md", "p\n")
        git(parent, ["add", "README.md"])
        git(parent, ["commit", "-q", "-m", "p"])
        src = other.replace("\\", "/")
        for label, command, expected, needle in (
            ("`-C <sub> merge` of a branch only the sub holds -> allow", "git -C sub merge --ff-only -q feat", ALLOW, ""),
            ("`-C <sub> cherry-pick` of a commit only the sub holds -> allow", "git -C sub cherry-pick feat", ALLOW, ""),
            ("`-C <sub> revert` of a commit only the sub holds -> allow", "git -C sub revert --no-edit feat", ALLOW, ""),
            ("`-C <sub> merge` of a ref the sub lacks -> deny", "git -C sub merge --ff-only -q nosuch", DENY,
             "cannot verify commit scope"),
            ("`-C <sub> fetch X && -C <sub> merge` of the ref the fetch creates -> allow",
             "git -C sub fetch -q %s x:jmodot/x && git -C sub merge --ff-only -q jmodot/x" % src, ALLOW, ""),
            ("`cd <sub> && fetch && merge` of the ref the fetch creates -> allow",
             "cd sub && git fetch -q %s x:jmodot/y && git merge --ff-only -q jmodot/y" % src, ALLOW, ""),
            ("a merge of a ref the earlier fetch lands in a DIFFERENT repo -> deny",
             "git fetch -q %s x:jmodot/z; git -C sub merge --ff-only -q jmodot/z" % src, DENY,
             "cannot verify commit scope"),
            ("a fetch, then a merge of a ref it did not create -> deny",
             "git -C sub fetch -q %s x:jmodot/x && git -C sub merge --ff-only -q nosuch" % src, DENY,
             "cannot verify commit scope"),
        ):
            failures.append(case(label, run_hook(command, parent, unguarded=True), expected, needle))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return failures


GROUPS = (string_constants_cases, commit_proof_cases, inline_cases, index_cases, push_base_cases,
          same_command_write_cases, staged_blob_cases, must_allow_proof, retargeted_merge_cases)


def main():
    """Each group builds its own temp repos, so the groups run at once, one child process each."""
    failures = []
    for text, found in run_sections(os.path.abspath(__file__), "--group", len(GROUPS),
                                    names=[group.__name__ for group in GROUPS]):
        sys.stdout.write(text)
        failures.extend("" if ok else (label + ": " if label else "") + detail for label, ok, detail in found)

    total = len(failures)
    failures = [f for f in failures if f]
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--group"]:
        emit_section([("", not f, f or "") for f in GROUPS[int(sys.argv[2])]()])
        sys.exit(0)
    sys.exit(main())
