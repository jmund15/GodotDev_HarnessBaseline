"""Close and scoped result authority: the generation claim, the typed historical answer, the bisect, the
comparison at a revision, and the one per-file digest builder.

A close owns its run record through one `Claim`. `publish` rechecks that claim inside the same locked update
that stores the close stamp, so a close a newer one superseded between its last check and its write publishes
nothing. A comparison revision answers each proof once, as `Historical`: a run that could not answer is
`unavailable`, never a pass. `first_reds` names only a commit where the red was seen, and stops `unknown` on
an unavailable answer. `digest_files` is the stamp's whole-tree key, built the same way by the full close and
the scoped merge.
"""
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLAUDE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
from _hook_state import update_json_locked, read_json_salvage  # noqa: E402

RED, PASS, UNAVAILABLE = "red", "pass", "unavailable"
FOUND, UNKNOWN = "found", "unknown"


@dataclass(frozen=True)
class Historical:
    """One proof's answer at a comparison revision: `red` with its failure detail, `pass`, or `unavailable`
    with the cause when the revision could not run it."""
    kind: str
    detail: str = ""
    cause: str = ""


@dataclass(frozen=True)
class FirstRed:
    """Where a proof's history first reproduces its red: `found` names the commit the red was seen at,
    `unknown` names none and carries the cause."""
    kind: str
    commit: str = None
    cause: str = ""


@dataclass(frozen=True)
class Claim:
    """This close's ownership of its run record: the record path and the token naming this run."""
    path: str
    token: str


def claim(path):
    """Claim `path` for this run with one locked write of a fresh token, and return the Claim."""
    token = "%d-%.6f" % (os.getpid(), time.time())
    update_json_locked(path, lambda doc: doc.update(token=token), raising=True)
    return Claim(path=path, token=token)


def owns(claim):
    """Whether `claim` still holds its run record."""
    return (read_json_salvage(claim.path) or {}).get("token") == claim.token


def publish(claim, stamp_path, close):
    """Store `close` as the stamp's close result only while `claim` still owns its record at write time.

    Returns False when a newer close took the record, in which case the stamp keeps its earlier content.
    """
    def write(doc):
        if not owns(claim):
            return False
        doc.update(close=close)
        return True

    _, published = update_json_locked(stamp_path, write, raising=True)
    return published is True


def digest_files(files):
    """One freshness digest over a {path: sha256} map, the stamp's whole-tree key."""
    return hashlib.sha256(json.dumps(sorted(files.items())).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BaseVerdict:
    """A red's standing at the comparison revision: `pre_existing` (the same failure is there), `different`
    (another failure or none), or `unavailable` (the revision could not run it)."""
    kind: str
    detail: str = ""
    cause: str = ""


class Comparison:
    """One comparison revision's answers, asked once per proof.

    `blocking` (the run-time decision) and `verdict` (the receipt) read the same answers, so the red the run
    stops on and the red the receipt names cannot disagree. An unavailable answer blocks: an absent answer is
    never an excuse.
    """

    def __init__(self, rev, at, pre_existing):
        self.rev = rev
        self._at = at                      # at(proofs) -> {proof: Historical}
        self._pre_existing = pre_existing  # pre_existing(detail, base detail) -> bool
        self.answers = {}

    def ask(self, proofs):
        """Record the answers for `proofs` not asked yet, in one batch."""
        fresh = [proof for proof in proofs if proof not in self.answers]
        if fresh:
            found = self._at(fresh) or {}
            for proof in fresh:
                self.answers[proof] = found.get(proof) or Historical(
                    UNAVAILABLE, cause="no answer at %s" % str(self.rev)[:8])

    def verdict(self, proof, detail):
        answer = self.answers.get(proof) or Historical(UNAVAILABLE, cause="not asked")
        if answer.kind == UNAVAILABLE:
            return BaseVerdict(UNAVAILABLE, "", answer.cause)
        if answer.kind == RED and self._pre_existing(detail, answer.detail):
            return BaseVerdict("pre_existing", answer.detail)
        return BaseVerdict("different", answer.detail)

    def blocking(self, reds):
        """The proofs in `reds` ({proof: detail}) whose red is not pre-existing at this revision."""
        self.ask(list(reds))
        return [proof for proof, detail in reds.items() if self.verdict(proof, detail).kind != "pre_existing"]


def first_reds(reds, at, pre_existing=None, deadline=None, progress=None, basename=os.path.basename):
    """{proof: FirstRed} for `reds` ({proof: (detail, candidate commits)}), bisected in lockstep.

    One step answers every active proof at one commit (`at(rev, proofs)` -> {proof: Historical}), so a step
    costs one checkout. An unavailable answer, or a passed `deadline`, stops that proof `unknown` without
    moving its span. A commit is named only where the red was seen: when the search ends on an index it never
    tested, one more answer settles it, and a history that never reproduces the red stays unknown.
    """
    progress = progress or (lambda message: None)
    span = {proof: [0, len(commits) - 1] for proof, (_detail, commits) in reds.items()}
    seen, stopped = {}, {}

    def answer(proof, commit, answers):
        got = answers.get(proof) or Historical(UNAVAILABLE, cause="no answer at %s" % commit[:8])
        if got.kind == UNAVAILABLE:
            stopped[proof] = FirstRed(UNKNOWN, cause=got.cause or "unavailable at %s" % commit[:8])
            progress("bisect %s unknown: %s" % (basename(proof), stopped[proof].cause))
            return None
        reproduced = got.kind == RED and (pre_existing is None or pre_existing(reds[proof][0], got.detail))
        progress("bisect %s at %s: %s" % (basename(proof), commit, "red" if reproduced else "not this red"))
        return reproduced

    def expired():
        value = deadline() if deadline is not None else None
        return value is not None and time.time() >= value

    while True:
        active = [proof for proof in span if proof not in stopped and span[proof][0] < span[proof][1]]
        if not active:
            break
        if expired():
            for proof in active:
                stopped[proof] = FirstRed(UNKNOWN, cause="HARNESS_DEADLINE passed")
                progress("bisect %s stopped: HARNESS_DEADLINE passed" % basename(proof))
            break
        groups = {}
        for proof in active:
            groups.setdefault(reds[proof][1][sum(span[proof]) // 2], []).append(proof)
        for commit, proofs in groups.items():
            if expired():
                for proof in proofs:
                    stopped[proof] = FirstRed(UNKNOWN, cause="HARNESS_DEADLINE passed")
                    progress("bisect %s stopped: HARNESS_DEADLINE passed" % basename(proof))
                continue
            answers = at(commit, proofs) or {}
            for proof in proofs:
                if proof in stopped:
                    continue
                low, high = span[proof]
                mid = (low + high) // 2
                reproduced = answer(proof, commit, answers)
                if reproduced is None:
                    continue
                seen[(proof, mid)] = reproduced
                span[proof] = [low, mid] if reproduced else [mid + 1, high]

    found = {}
    for proof, (_detail, commits) in reds.items():
        if proof in stopped:
            found[proof] = stopped[proof]
            continue
        if not commits:
            found[proof] = FirstRed(UNKNOWN, cause="no candidate commit")
            continue
        index = span[proof][0]
        if (proof, index) not in seen:
            if expired():
                found[proof] = FirstRed(UNKNOWN, cause="HARNESS_DEADLINE passed")
                continue
            reproduced = answer(proof, commits[index], at(commits[index], [proof]) or {})
            if reproduced is None:
                found[proof] = stopped[proof]
                continue
            seen[(proof, index)] = reproduced
        if seen[(proof, index)]:
            found[proof] = FirstRed(FOUND, commit=commits[index])
        else:
            reproduced_at = sorted(step for (name, step), hit in seen.items() if name == proof and hit)
            found[proof] = (FirstRed(FOUND, commit=commits[reproduced_at[0]]) if reproduced_at
                            else FirstRed(UNKNOWN, cause="the red never reproduced in this range"))
    return found


@dataclass(frozen=True)
class Reduction:
    """One run's final statuses, bucketed once: `passed` and `failed` counts, and the proofs behind each
    missing-coverage outcome. `incomplete` is every proof that neither passed nor failed."""
    passed: int
    failed: int
    platform: list
    skipped: list
    cannot_run: list

    @property
    def incomplete(self):
        return list(self.skipped) + list(self.cannot_run) + list(self.platform)


def reduce_statuses(selected, statuses):
    """Bucket `selected`'s final statuses: pass, fail, `platform-skip`, deadline `skipped`, and
    `cannot-run` or no status. Each outcome is distinct, so a caller never reads an absent status as a pass."""
    passed = failed = 0
    platform, skipped, cannot_run = [], [], []
    for proof in selected:
        status = statuses.get(proof)
        if status == "pass":
            passed += 1
        elif status == "fail":
            failed += 1
        elif status == "platform-skip":
            platform.append(proof)
        elif status == "skipped":
            skipped.append(proof)
        else:
            cannot_run.append(proof)
    return Reduction(passed, failed, platform, skipped, cannot_run)
