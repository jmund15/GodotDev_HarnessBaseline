"""Proof for opencode_refresh_models.py --go on a planted catalog, models.dev input, protocol and caps file, against a copy of
the registry. A dry run reports one added row and leaves the copy alone; a real run writes that row into the copy only.
A missing --go and a malformed catalog are refused and leave the copy's bytes unchanged. No network: --modelsdev is
planted, so the script never fetches models.dev."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "opencode_refresh_models.py"
REGISTRY = ROOT / ".claude" / "reference" / "external_models.json"
MODEL = "planted-model"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def contract(temp_root):
    temp_root = Path(temp_root)
    registry = temp_root / "external_models.json"
    shutil.copyfile(REGISTRY, registry)
    before = registry.read_bytes()

    catalog = temp_root / "catalog.json"
    catalog.write_text(json.dumps({"data": [{"id": MODEL}]}), encoding="utf-8", newline="\n")
    modelsdev = temp_root / "modelsdev.json"
    modelsdev.write_text("{}", encoding="utf-8", newline="\n")
    protocol = temp_root / "protocol.json"
    protocol.write_text(json.dumps({"results": {MODEL: {"mode": "messages"}}}), encoding="utf-8", newline="\n")
    caps = temp_root / "caps.json"
    caps.write_text(json.dumps({"monthlyUSD": {MODEL: None}}), encoding="utf-8", newline="\n")
    refresh = ["--go", "--catalog", str(catalog), "--modelsdev", str(modelsdev), "--protocol", str(protocol),
               "--caps", str(caps), "--registry", str(registry)]

    no_go = run("--dry-run", *refresh[1:])
    assert no_go.returncode == 2 and "--go" in no_go.stderr, (no_go.returncode, no_go.stderr[-300:])
    assert registry.read_bytes() == before

    dry = run("--dry-run", *refresh)
    assert dry.returncode == 0, (dry.returncode, dry.stderr[-300:])
    report = json.loads(dry.stdout)
    assert [row["id"] for row in report["added"]] == [MODEL], report["added"]
    assert registry.read_bytes() == before, "a dry run wrote the registry copy"

    bad_catalog = temp_root / "bad_catalog.json"
    bad_catalog.write_text(json.dumps({"data": "not a list"}), encoding="utf-8", newline="\n")
    malformed = run(*[bad_catalog.as_posix() if a == str(catalog) else a for a in refresh])
    assert malformed.returncode == 2 and "registry unchanged" in malformed.stderr, (malformed.returncode, malformed.stderr[-300:])
    assert registry.read_bytes() == before

    real = run(*refresh)
    assert real.returncode == 0 and "1 added" in real.stdout, (real.returncode, real.stdout[-300:], real.stderr[-300:])
    written = json.loads(registry.read_text(encoding="utf-8"))
    assert any(m.get("upstreamId") == MODEL or m.get("id") == MODEL for m in written["models"]), "row not written"
    assert json.loads(REGISTRY.read_text(encoding="utf-8")) == json.loads(before.decode("utf-8")), "live registry changed"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="opencode_refresh_models_proof_") as tmp:
        contract(tmp)
    print("ok opencode_refresh_models.py")
