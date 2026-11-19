"""Proof: `git commit --amend` is denied unless HEAD is this session's own commit or a lane's own commit: in a
shared checkout an amend of another author's HEAD rewrites their commit."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_git_guardrails_harness_commit import ALLOW, DENY, case, git, make_repo, run_hook, write  # noqa: E402

OWN = "Claude-Session: https://claude.ai/code/session_OWN"
PEER = "Claude-Session: https://claude.ai/code/session_PEER"


def session_home(bridge="session_OWN"):
    home = tempfile.mkdtemp()
    os.makedirs(os.path.join(home, ".claude", "sessions"))
    if bridge:
        with open(os.path.join(home, ".claude", "sessions", "1.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": 1, "sessionId": "s1", "bridgeSessionId": bridge}, fh)
    return {"HOME": home, "USERPROFILE": home}


def repo_with_head(trailer):
    """A repo whose HEAD commit carries `trailer`, with a staged change an amend would fold in."""
    repo = make_repo()
    write(repo, "m.txt", "one\n")
    git(repo, ["add", "m.txt"])
    git(repo, ["commit", "-q", "-m", "change" + ("\n\n" + trailer if trailer else "")])
    write(repo, "m.txt", "two\n")
    git(repo, ["add", "m.txt"])
    return repo


def head(repo):
    return git(repo, ["rev-parse", "--short=9", "HEAD"]).strip()


def main():
    own = session_home()
    failures = []
    peer_repo, owner_repo = repo_with_head(PEER), repo_with_head("")
    failures.append(case("amend of this session's own HEAD -> allow",
                         run_hook("git commit --amend --no-edit", repo_with_head(OWN), own), ALLOW))
    failures.append(case("amend of a peer session's HEAD -> deny, naming HEAD",
                         run_hook("git commit --amend --no-edit", peer_repo, own), DENY, head(peer_repo)))
    failures.append(case("amend of a commit with no session trailer (the owner's) -> deny",
                         run_hook("git commit --amend -m reworded", owner_repo, own), DENY, head(owner_repo)))
    failures.append(case("a new commit on a peer's HEAD -> allow",
                         run_hook("git commit -m next", repo_with_head(PEER), own), ALLOW))
    failures.append(case("amend while this session's trailer cannot be resolved -> deny",
                         run_hook("git commit --amend --no-edit", repo_with_head(OWN), session_home(None)), DENY))

    lane_base = repo_with_head(OWN)
    git(lane_base, ["commit", "-q", "-m", "base"])
    git(lane_base, ["update-ref", "refs/remotes/origin/main", "HEAD"])
    lane = tempfile.mkdtemp(prefix="ggamend_lane_")
    os.rmdir(lane)
    git(lane_base, ["worktree", "add", "-q", "-b", "lane", lane])
    write(lane, "l.txt", "delegate\n")
    git(lane, ["add", "l.txt"])
    git(lane, ["commit", "-q", "-m", "delegate work\n\n" + PEER])
    write(lane, "l.txt", "delegate, fixed\n")
    git(lane, ["add", "l.txt"])
    failures.append(case("amend of a linked worktree's own commit (no peer commits there) -> allow",
                         run_hook("git commit --amend --no-edit", lane, own), ALLOW))

    failures = [f for f in failures if f]
    for f in failures:
        print("FAIL " + f)
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
