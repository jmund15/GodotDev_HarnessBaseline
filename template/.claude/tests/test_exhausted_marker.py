"""exhausted_marker: the one reader of the sidecar usage-limit marker files."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import exhausted_marker as em  # noqa: E402


def test_dir_env_then_home_default(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDECAR_EXHAUSTED_DIR", str(tmp_path))
    assert em.exhausted_dir() == tmp_path
    monkeypatch.delenv("SIDECAR_EXHAUSTED_DIR")
    assert em.exhausted_dir() == Path.home() / ".claude" / "sidecar-exhausted"


def test_transport_and_member_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDECAR_EXHAUSTED_DIR", str(tmp_path))
    assert em.marker_path("codex") == tmp_path / "codex.json"
    assert em.marker_path("opencode-cli", "m1") == tmp_path / "members" / "opencode-cli" / "m1.json"


def _cap(observed, *used):
    return {"observedAt": observed, "quota": {"windows": [{"name": f"w{i}", "usedPercent": u} for i, u in enumerate(used)]}}


def test_a_later_reading_with_room_supersedes_the_marker():
    marker = {"writtenAt": 1795118038}
    assert em.superseded(marker, _cap("2026-11-19T20:02:16Z", 0, 0))


def test_the_marker_holds_without_proof_of_a_lifted_limit():
    marker = {"writtenAt": 1795118038}
    assert not em.superseded(marker, _cap("2026-11-19T19:00:00Z", 0, 0))      # read before the stop
    assert not em.superseded(marker, _cap("2026-11-19T20:02:16Z", 100, 0))    # a window still full
    assert not em.superseded(marker, _cap("2026-11-19T20:02:16Z"))            # no windows
    assert not em.superseded(marker, None)                                    # no reading
    assert not em.superseded({"writtenAt": 1795118038, "reason": "auth"}, _cap("2026-11-19T20:02:16Z", 0))


def test_read_returns_dict_or_none(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDECAR_EXHAUSTED_DIR", str(tmp_path))
    assert em.read("codex") is None
    (tmp_path / "codex.json").write_text(json.dumps({"expiresAt": 5}), encoding="utf-8")
    assert em.read("codex") == {"expiresAt": 5}
    (tmp_path / "codex.json").write_text("[1]", encoding="utf-8")
    assert em.read("codex") is None
    (tmp_path / "codex.json").write_text("{bad", encoding="utf-8")
    assert em.read("codex") is None
