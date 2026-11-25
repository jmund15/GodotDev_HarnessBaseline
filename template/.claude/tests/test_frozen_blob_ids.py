"""A blob id that a later step resolves must be stored when it is computed.

`git hash-object` without `-w` only computes an id. A tool that records that id for a later reader (a review
ledger row, a fold snapshot) strands it the moment the file moves on: the object was never written and the disk
no longer holds the bytes. Every unwritten hash-object call site therefore sits on COMPARE_ONLY with the reason it
is only ever compared against a fresh hash; a new call site must either pass `-w` or join that list.
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = [".claude/tools", ".claude/hooks", ".claude/scripts", ".claude/workflows"]
CALL = re.compile(r"hash-object")
# path -> a text fragment of the call line; each is compared with a fresh hash in the same run, never resolved later.
COMPARE_ONLY = {
    ".claude/hooks/_delete_rule.py": 'ids = run(["hash-object", "--stdin-paths"]',
    ".claude/hooks/dangerous_shell_guard.py": 'work = run_git(["hash-object", "--"] + paths, root, git_env)',
    ".claude/hooks/gate_cadence_guard.py": "'hash-object', '--', rel]",
    ".claude/hooks/triage_commit_check.py": 'run_git(["hash-object", "--"] + present',
    ".claude/scripts/TreeDigest.ps1": "hash-object --stdin-paths",
    ".claude/tools/citer_coverage.py": '"hash-object", str(subject)',
    ".claude/tools/citers.py": 'git(repo, "hash-object", str(plan))',
    ".claude/tools/review_snapshot.py": '["hash-object", "--stdin-paths"]',
}
NOT_A_CALL = ("raise ", "# ", "#", "Unusable(", "OSError(")


def unwritten_calls(root, scanned=SCANNED):
    """[(path, line)] of every hash-object call line with no -w that COMPARE_ONLY does not name."""
    out = []
    for rel in scanned:
        base = Path(root) / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in (".py", ".ps1", ".sh", ".js", ".mjs") or not path.is_file():
                continue
            key = path.relative_to(root).as_posix()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                text = line.strip()
                if not CALL.search(text) or text.startswith(NOT_A_CALL) or "`git hash-object`" in text:
                    continue
                if re.search(r"""(?:^|[\s"'\[,])-w(?:$|[\s"',\]])""", text):
                    continue
                if COMPARE_ONLY.get(key) and COMPARE_ONLY[key] in text:
                    continue
                out.append((key, text))
    return out


class FrozenBlobIds(unittest.TestCase):
    def test_every_unwritten_hash_object_is_compare_only(self):
        self.assertEqual(unwritten_calls(ROOT), [],
                         "pass -w where the id is recorded for a later reader; add a compare-only site to "
                         "COMPARE_ONLY with its reason")

    def test_every_compare_only_entry_still_matches_a_call(self):
        for rel, fragment in COMPARE_ONLY.items():
            self.assertIn(fragment, (ROOT / rel).read_text(encoding="utf-8"), f"stale COMPARE_ONLY entry: {rel}")

    def test_a_planted_recorder_without_w_is_caught_and_with_w_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            tools = Path(td) / ".claude/tools"
            tools.mkdir(parents=True)
            (tools / "recorder.py").write_text('blob = git(root, "hash-object", "--", path)\n', encoding="utf-8")
            (tools / "stored.py").write_text('blob = git(root, "hash-object", "-w", "--", path)\n', encoding="utf-8")
            found = unwritten_calls(td, [".claude/tools"])
        self.assertEqual([p for p, _ in found], [".claude/tools/recorder.py"])


if __name__ == "__main__":
    sys.exit(unittest.main())
