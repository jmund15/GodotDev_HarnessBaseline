"""Proof for check_learnings.sh, the Stop hook's text scan. The script has no failing exit: it always answers `ok: true`.
The proof asserts its two answers on planted transcripts: a transcript with corrections suggests autolearn, and a
missing transcript path gives the safe default. No network, no spend."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import launch_env, run_bash  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "check_learnings.sh"


def contract(temp_root):
    temp_root = Path(temp_root)
    env = launch_env(temp_root)
    transcript = temp_root / "transcript.jsonl"
    lines = ["the assistant said something", "no, use the other helper", "from now on keep it short"]
    transcript.write_text("\n".join(json.dumps({"text": t}) for t in lines) + "\n", encoding="utf-8", newline="\n")
    hook_input = json.dumps({"transcript_path": transcript.as_posix()})

    learned = run_bash(SCRIPT, temp_root, env, stdin=hook_input)
    assert learned.returncode == 0 and "POTENTIAL LEARNINGS DETECTED" in learned.stdout, (learned.returncode, learned.stdout)

    absent = run_bash(SCRIPT, temp_root, env, stdin=json.dumps({"transcript_path": (temp_root / "gone.jsonl").as_posix()}))
    assert absent.returncode == 0 and '"ok": true' in absent.stdout and "Session complete" in absent.stdout, absent.stdout


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="check_learnings_proof_") as tmp:
        contract(tmp)
    print("ok check_learnings.sh")
