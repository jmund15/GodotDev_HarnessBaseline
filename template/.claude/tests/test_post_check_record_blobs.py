"""`post_check.py` `record_panel` records one blob map covering every reviewed file.

A review can carry both a frozen blob map (`--blobs`, the harness packet's files) and fold subjects (the
snapshot the panel read). The ledger row must name each file at the blob its seat read: dropping the fold
subjects' blobs records those files at whatever blob `record` finds, which is not the reviewed one.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import post_check  # noqa: E402

FROZEN = "a" * 40
FOLD = "b" * 40
FOLD_OLD = "c" * 40


def test_frozen_and_fold_blobs_record_as_one_map_with_the_frozen_blob_winning(tmp_path, monkeypatch):
    frozen = tmp_path / "frozen_blobs.json"
    frozen.write_text(json.dumps({"doc.md": FROZEN, "shared.py": FROZEN}), encoding="utf-8")
    subject = SimpleNamespace(files=["code.py", "shared.py"], root=tmp_path)
    monkeypatch.setattr(post_check, "reviewed_blobs", lambda s: {"code.py": FOLD, "shared.py": FOLD_OLD})
    calls = []
    monkeypatch.setattr(post_check, "ledger", lambda cwd, *cmd: calls.append(cmd) or (0, "", ""))
    args = SimpleNamespace(blobs=str(frozen), scope_json=None, stage="candidate", subject="session:x",
                           roster="roster.json", out=str(tmp_path / "post_check.json"), seats_file=None,
                           premise_commits=None, head=None, task=None, no_new_decision=False)
    post_check.record_panel(tmp_path, args, SimpleNamespace(record=("--result", "r.json")), [subject])
    cmd = calls[0]
    recorded = json.loads(Path(cmd[cmd.index("--blobs") + 1]).read_text(encoding="utf-8"))
    assert recorded == {"doc.md": FROZEN, "shared.py": FROZEN, "code.py": FOLD}
