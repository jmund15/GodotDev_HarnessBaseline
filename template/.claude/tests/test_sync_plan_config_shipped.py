"""Proof for sync_plan_config.py's file stage (--files-only), in process. A temp `seats.json` and a temp default login
stand in for the real ones, so the proof never reads or writes a login. The default login holds the one whole-file
config: check reports it as drift (exit 1), a sync writes it to the seat with a `configDir`, a second check is clean
(exit 0); a seat with no `configDir` is not synced; a missing seat and a bad flag are refused (exit 2); a seat whose
login dir is absent is BLOCKED-ON-USER (exit 3). The plugins stage is not run: it calls the claude CLI."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_plan_config.py"
GUIDE = "ai_worker_model_guide.md"


@pytest.fixture
def sync(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("sync_plan_config_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    source, dest = tmp_path / "default-login", tmp_path / "seat-login"
    source.mkdir()
    dest.mkdir()
    (source / GUIDE).write_text("planted guide\n", encoding="utf-8", newline="\n")
    seats = tmp_path / "seats.json"
    seats.write_text(json.dumps({"seats": {"login-seat": {"configDir": dest.as_posix()},
                                           "no-login-seat": {"configDir": ""}}}), encoding="utf-8")
    monkeypatch.setattr(mod, "SEATS", seats)
    monkeypatch.setattr(mod, "DEFAULT_LOGIN", source)
    mod.dest = dest
    return mod


def test_check_then_sync_then_clean(sync, capsys):
    assert sync.main(["--check", "--files-only"]) == 1 and GUIDE in capsys.readouterr().out
    assert not (sync.dest / GUIDE).exists(), "--check wrote the destination"
    assert sync.main(["--files-only"]) == 0
    capsys.readouterr()
    assert (sync.dest / GUIDE).read_text(encoding="utf-8") == "planted guide\n"
    assert sync.main(["--check", "--files-only"]) == 0 and GUIDE not in capsys.readouterr().out


def test_only_seats_with_a_login_dir_pair(sync):
    assert [name for name, _src, _dst in sync.pairs()] == ["login-seat"]
    assert sync.pairs("login-seat")[0][2] == sync.dest


def test_refusals(sync, capsys):
    assert sync.main(["--check", "--files-only", "--seat", "no-such-seat"]) == 2
    assert "no seat" in capsys.readouterr().err
    assert sync.main(["--bogus"]) == 2


def test_absent_login_dir_is_blocked_on_user(sync, capsys):
    sync.dest.rmdir()
    assert sync.main(["--check", "--files-only"]) == 3
    assert "BLOCKED-ON-USER: login-seat" in capsys.readouterr().out
