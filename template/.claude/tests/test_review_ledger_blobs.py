"""`--blobs` takes a path->blob map or the blob snapshot manifest a review already wrote."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))

import review_ledger  # noqa: E402

SHA = "e3b94a36e552708c792d0fa3fbf571dcf89c0229"


def read(tmp, name, data):
    path = Path(tmp) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return review_ledger.blob_map(str(path))


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        plain = read(tmp, "map.json", {"a.md": SHA})
        if plain != {"a.md": SHA}:
            failures.append(f"plain map: {plain}")
        snap = read(tmp, "snap.json", {"kind": "blobs", "root": tmp, "head": SHA, "files": {"a.md": SHA}})
        if snap != {"a.md": SHA}:
            failures.append(f"snapshot manifest: {snap}")
        for name, bad in (("list.json", ["a.md"]), ("sha.json", {"a.md": "not-a-sha"}),
                          ("kind.json", {"kind": "blobs", "files": ["a.md"]})):
            try:
                read(tmp, name, bad)
                failures.append(f"{name} loaded")
            except review_ledger.ToolError:
                pass
    for line in failures:
        print("FAIL", line)
    print(f"{'FAIL' if failures else 'PASS'} test_review_ledger_blobs ({5 - len(failures)}/5)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
