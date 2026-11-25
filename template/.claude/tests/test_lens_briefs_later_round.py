#!/usr/bin/env python3
"""Proof for `lens_briefs.py prepare`: a seat re-owed a file after an earlier reviewed blob reads the change and its
direct citers, not the whole file; the four cases that keep the whole file read it whole.

Every case runs the real `prepare` in a planted git checkout under its own temp root. The earlier reviewed blob is
planted by the real `review_ledger.py record --inline` (the same row a review writes), and `plan` is the real owner of
the re-owed facts. Assertions are on the decision: which paths a seat must read, and which it reads as context.

    python3 .claude/tests/test_lens_briefs_later_round.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOOLS = REPO / ".claude" / "tools"
EXTERNAL = json.loads((REPO / ".claude" / "reference" / "external_models.json").read_text(encoding="utf-8"))
sys.path[:0] = [str(TOOLS), str(REPO / ".claude" / "hooks"), str(HERE)]
import _mcp_plant  # noqa: E402
from _planted_checkout import enter_planted_checkout  # noqa: E402

enter_planted_checkout()  # the rigor reader asks git about the working directory
_mcp_plant.plant()
import lens  # noqa: E402
import lens_briefs  # noqa: E402

PINS = sorted(lens_briefs.NATIVE_PINS)
EFFORTS = sorted(lens_briefs.NATIVE_EFFORTS)
import model_registry  # noqa: E402
# A native pin that serves the seat's lens tier (`architect`), read from the live registry so a roster change never breaks this proof.
ARCHITECT_PINS = {row["alias"] for row in model_registry.rows_for_tier("architect")}
PANEL = next(pin for pin in PINS if pin in ARCHITECT_PINS)
EFFORT = "medium" if "medium" in EFFORTS else EFFORTS[0]
MANDATE = "plc-test-readiness"
MANDATE_B = "plc-memory-alignment"
SEAT_ONE = MANDATE
SEAT_TWO = MANDATE + "+" + MANDATE_B
SUBJECT = ".claude/plans/subject.md"
CITER = ".claude/notes/citer.md"
BYSTANDER = ".claude/notes/bystander.md"
NEEDS_PLAN_CHECK = "plan_check" in lens.shipped()
PLAN_V1 = "".join(f"Line {i} of the plan.\n" for i in range(1, 21))
PLAN_V2 = PLAN_V1.replace("Line 7 of the plan.\n", "Line 7 of the plan, now edited.\n")
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:500]))


def capacity_cache(path):
    now = time.time()
    cache = {}
    for name, cfg in EXTERNAL["transports"].items():
        entry = {"schemaVersion": 1, "transport": name, "costModel": cfg.get("costModel"),
                 "sourceKind": "live", "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
        if name == "anthropic":
            entry.update(status="available", balance=None, error=None, quota={
                "dispatchBand": "On pace", "routingBand": "On pace", "bindingWindow": "five_hour",
                "routingWindow": "five_hour",
                "windows": [{"name": "five_hour", "usedPercent": 10.0, "resetsAt": now + 3600,
                             "band": "On pace", "pressure": 0.3, "runoutRatio": 0.5,
                             "windowDurationMins": 300, "dispatchFloorPercent": 99}]})
        else:
            entry.update(status="network-error", quota=None, balance=None,
                         error={"kind": "network", "message": "planted"})
        cache[name] = entry
    path.write_text(json.dumps(cache), encoding="utf-8")


class Fixture:
    """A planted checkout: a plan at its base, one committed citer and one bystander, then the plan's review-time edit."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="lbr_proof_")).resolve()
        self.git("init", "-q")
        self.git("config", "user.email", "proof@example.invalid")
        self.git("config", "user.name", "proof")
        self.git("config", "commit.gpgsign", "false")
        self.write(SUBJECT, PLAN_V1)
        self.write(CITER, f"The shape is read from `{SUBJECT}`.\n")
        self.write(BYSTANDER, "Unrelated note.\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.context = self.write("ctx/context.md", "# Context\n\nplanted\n")
        self.run_dir = self.root / ".claude" / "scratch" / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.out = self.run_dir / "out"
        self.state = self.root / "state"
        self.state.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.cache = self.root / "capacity.json"
        capacity_cache(self.cache)
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        CLAUDE_PROJECT_DIR=str(self.root), HARNESS_HOOK_STATE_DIR=str(self.state),
                        HARNESS_TASK_RECORD_DIR=str(self.tasks), PROVIDER_CAPACITY_CACHE=str(self.cache),
                        CLAUDE_CODE_SESSION_ID="laterround", PYTHONPATH="")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def record(self, mandate):
        """Plant the earlier reviewed row for `mandate` over the plan as it stands, through the real `record`."""
        proc = subprocess.run([sys.executable, str(TOOLS / "review_ledger.py"), "record", "--stage", "plan-check",
                               "--subject", "plan:" + SUBJECT, "--inline", "proof", "--mandate", mandate,
                               "--outcome", "clean", "--files", SUBJECT],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(self.root), env=self.env,
                              timeout=180)
        if proc.returncode != 0:
            raise RuntimeError("record refused: " + proc.stderr[-400:])

    def prepare(self, seat_key, **fields):
        spec = {"mode": "plan-check", "prefix": "lbr", "root": str(self.root), "context": str(self.context),
                "roster": [{"key": seat_key, "effort": EFFORT}], "pins": {"panel": PANEL},
                "efforts": {"panel": EFFORT}, "subjectFiles": [SUBJECT], "spillDir": str(self.run_dir / "spill"),
                "target": "native",
                "ledger": {"stage": "plan-check", "subject": "plan:" + SUBJECT, "files": [SUBJECT],
                           "baseRef": self.base}}
        spec.update(fields)
        spec_path = self.run_dir / "spec.json"
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8", newline="\n")
        return subprocess.run([sys.executable, str(TOOLS / "lens_briefs.py"), "prepare", "--spec", str(spec_path),
                               "--out", str(self.out)],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(self.root), env=self.env,
                              timeout=240)

    def seat(self):
        args = json.loads((self.out / "args.json").read_text(encoding="utf-8"))
        return args["agents"][0]

    def must_read(self):
        """The seat's must-read paths, as repo-relative POSIX paths (absolute when outside the checkout)."""
        out = []
        for entry in self.seat()["mustRead"]:
            path = Path(entry["path"])
            if path.is_absolute():
                try:
                    out.append(path.resolve().relative_to(self.root).as_posix())
                except ValueError:
                    out.append(path.resolve().as_posix())
            else:
                out.append(path.as_posix())
        return out

    def brief_text(self):
        return Path(self.seat()["promptPath"]).read_text(encoding="utf-8")


def detail(proc):
    return proc.returncode, proc.stdout[-300:], proc.stderr[-600:]


def later_round_case():
    """Rows over the plan at its base, then an edit: the seat reads the change and its citer, not the whole plan."""
    f = Fixture()
    f.record(MANDATE)
    f.write(SUBJECT, PLAN_V2)
    proc = f.prepare(SEAT_ONE)
    check("later round: prepare exits 0", proc.returncode == 0, detail(proc))
    if proc.returncode != 0:
        return
    reads = f.must_read()
    diffs = [p for p in reads if p.startswith(".claude/scratch/run/out/delta/")]
    check("later round: the whole plan is not a must-read", SUBJECT not in reads, reads)
    check("later round: the seat must-reads the citer of the changed plan", CITER in reads, reads)
    check("later round: the seat must-reads one change file in its run dir", len(diffs) == 1, reads)
    if diffs:
        change = (f.root / diffs[0]).read_text(encoding="utf-8")
        check("later round: the change file holds the edited line",
              "+Line 7 of the plan, now edited." in change and "-Line 7 of the plan." in change, change[:300])
    check("later round: the full plan is named in the brief as context", SUBJECT in f.brief_text(),
          f.brief_text()[-300:])
    check("later round: the bystander is not a must-read", BYSTANDER not in reads, reads)


def first_round_case():
    """No covering row: the whole file is the must-read."""
    f = Fixture()
    f.write(SUBJECT, PLAN_V2)
    proc = f.prepare(SEAT_ONE)
    check("first round: prepare exits 0", proc.returncode == 0, detail(proc))
    if proc.returncode == 0:
        check("first round: the whole plan is the only must-read", f.must_read() == [SUBJECT], f.must_read())


def uncovered_mandate_case():
    """One mandate of the seat has an earlier blob, the other has none: the whole plan is the must-read."""
    f = Fixture()
    f.record(MANDATE)
    f.write(SUBJECT, PLAN_V2)
    proc = f.prepare(SEAT_TWO)
    check("uncovered mandate: prepare exits 0", proc.returncode == 0, detail(proc))
    if proc.returncode == 0:
        check("uncovered mandate: the whole plan is the only must-read", f.must_read() == [SUBJECT], f.must_read())


def ignore_ledger_case():
    """`ignoreLedger` reads the whole plan even where a later round would apply."""
    f = Fixture()
    f.record(MANDATE)
    f.write(SUBJECT, PLAN_V2)
    proc = f.prepare(SEAT_ONE, ledger={"stage": "plan-check", "subject": "plan:" + SUBJECT, "files": [SUBJECT],
                                       "baseRef": f.base, "ignoreLedger": True})
    check("ignoreLedger: prepare exits 0", proc.returncode == 0, detail(proc))
    if proc.returncode == 0:
        check("ignoreLedger: the whole plan is the only must-read", f.must_read() == [SUBJECT], f.must_read())


def explicit_must_read_case():
    """A seat's own `mustRead` list is used as given, even where a later round would apply."""
    f = Fixture()
    f.record(MANDATE)
    f.write(SUBJECT, PLAN_V2)
    proc = f.prepare(SEAT_ONE, roster=[{"key": SEAT_ONE, "effort": EFFORT, "mustRead": [BYSTANDER]}])
    check("explicit mustRead: prepare exits 0", proc.returncode == 0, detail(proc))
    if proc.returncode == 0:
        check("explicit mustRead: the seat's own list is the only must-read", f.must_read() == [BYSTANDER],
              f.must_read())


def seat_reads(f, mandate):
    """One prepared seat's must-read paths, found by the mandate it carries."""
    for agent in json.loads((f.out / "args.json").read_text(encoding="utf-8"))["agents"]:
        if mandate in (agent.get("mandates") or []):
            reads = []
            for entry in agent["mustRead"]:
                path = Path(entry["path"])
                reads.append(path.resolve().relative_to(f.root).as_posix() if path.is_absolute() else path.as_posix())
            return reads
    return None


def one_diff_per_pair_case():
    """Two seats over one changed pair materialize one diff and one artifact, and the artifact records the pair."""
    f = Fixture()
    f.record(MANDATE)
    f.record(MANDATE_B)
    f.write(SUBJECT, PLAN_V2)
    roster = [{"key": MANDATE, "effort": EFFORT}, {"key": MANDATE_B, "effort": EFFORT}]
    proc = f.prepare(MANDATE, roster=roster)
    check("one diff: prepare exits 0", proc.returncode == 0, detail(proc))
    if proc.returncode != 0:
        return
    diffs = sorted((f.out / "delta").glob("*.diff")) if (f.out / "delta").is_dir() else []
    check("one diff: the pair materializes one diff for both seats", len(diffs) == 1, [p.name for p in diffs])
    artifact = f.out / "delta.json"
    check("one diff: prepare writes the delta artifact", artifact.is_file())
    for mandate in (MANDATE, MANDATE_B):
        reads = seat_reads(f, mandate) or []
        check("one diff: seat %s must-reads the shared diff" % mandate,
              any(path.startswith(".claude/scratch/run/out/delta/") for path in reads), reads)
    if not artifact.is_file():
        return
    data = json.loads(artifact.read_text(encoding="utf-8"))
    hunks = data.get(SUBJECT, {}).get("hunks") or []
    recorded = subprocess.run(
        [sys.executable, str(TOOLS / "review_ledger.py"), "record", "--stage", "plan-check",
         "--subject", "plan:" + SUBJECT, "--inline", "proof", "--mandate", MANDATE, "--outcome", "clean",
         "--delta", str(artifact), "--accounted", *[hunk["id"] for hunk in hunks]],
        capture_output=True, text=True, encoding="utf-8", cwd=str(f.root), env=f.env, timeout=180)
    check("one diff: the artifact records a delta row", recorded.returncode == 0, recorded.stderr[-400:])
    ledger = f.root / ".claude" / "logs" / "review_ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if ledger.is_file() else []
    delta_rows = [row for row in rows if row.get("delta")]
    check("one diff: the row stores base and accounted hunks",
          bool(delta_rows) and delta_rows[-1]["delta"][SUBJECT]["base"] == data[SUBJECT]["base"]
          and delta_rows[-1]["from"] == {SUBJECT: data[SUBJECT]["base"]},
          delta_rows[-1:] or rows[-1:])


def mixed_seats_case():
    """A seat with a base reads the delta; a seat whose mandate was never covered reads the whole plan."""
    f = Fixture()
    f.record(MANDATE)
    f.write(SUBJECT, PLAN_V2)
    roster = [{"key": MANDATE, "effort": EFFORT}, {"key": MANDATE_B, "effort": EFFORT}]
    proc = f.prepare(MANDATE, roster=roster)
    check("mixed seats: prepare exits 0", proc.returncode == 0, detail(proc))
    if proc.returncode != 0:
        return
    covered = seat_reads(f, MANDATE) or []
    check("mixed seats: the covered mandate reads the delta", SUBJECT not in covered and len(covered) > 1, covered)
    fresh = seat_reads(f, MANDATE_B) or []
    check("mixed seats: the never-covered mandate reads the whole plan", fresh == [SUBJECT], fresh)


CASES = (later_round_case, first_round_case, uncovered_mandate_case, ignore_ledger_case, explicit_must_read_case,
         one_diff_per_pair_case, mixed_seats_case)


def main():
    check("test_lens_briefs_later_round.py finds the tool it proves", (TOOLS / "lens_briefs.py").is_file())
    if not NEEDS_PLAN_CHECK:
        print("skip: the plan_check lens registry is not installed")
        return 0
    for case in CASES:
        try:
            case()
        except Exception as exc:  # a crash is a failure, never a skip
            check("%s ran" % case.__name__, False, repr(exc))
    print("\n%d failed" % len(FAILURES) if FAILURES else "\nall pass")
    for failure in FAILURES:
        print("  FAIL " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
