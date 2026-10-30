"""Proof for `model_registry.py ladder-slice <tier>...`: the per-tier slice of the model ladder.

The slice is what a dispatcher reads instead of the whole ladder: the role definitions and terms, the
Role guidance rows whose registry `roles` claim a named tier, the selection-line bullets and the axis
definitions. A successful run records the covered tiers in the compaction-scoped hook state, so the
dispatch gate admits a call whose tiers the slice covers.
"""
import contextlib
import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
HOOKS = HERE.parent / "hooks"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mr():
    sys.path.insert(0, str(TOOLS))
    return _load("mr_ladder_slice", TOOLS / "model_registry.py")


@pytest.fixture(scope="module")
def live_lines(mr):
    return mr.ladder_path().read_text(encoding="utf-8").splitlines()


def _slice(mr, *tiers):
    return mr.ladder_slice(list(tiers))


def _guidance_rows(text):
    rows, in_guidance = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            in_guidance = line.strip() == "## Role guidance"
            continue
        if in_guidance and line.startswith("| "):
            cell = line.strip().strip("|").split("|")[0].strip()
            if cell.lower() != "model" and not set(cell) <= {"-", " ", ":"}:
                rows.append(cell)
    return rows


def test_slice_keeps_exactly_the_rows_claiming_a_named_tier(mr):
    data = mr.load()
    ladder = mr.ladder_path().read_text(encoding="utf-8")
    every = _guidance_rows(ladder)
    wanted = {"architect"}
    claiming = [c for c in every if wanted & set(mr.ladder_cell_tiers(c, data, supplementary=True))]
    assert claiming and len(claiming) < len(every)
    assert _guidance_rows(_slice(mr, "architect")) == claiming


def test_slice_covers_the_union_of_two_tiers_in_ladder_order(mr):
    data = mr.load()
    every = _guidance_rows(mr.ladder_path().read_text(encoding="utf-8"))
    wanted = {"architect", "scout"}
    expected = [c for c in every if wanted & set(mr.ladder_cell_tiers(c, data, supplementary=True))]
    assert _guidance_rows(_slice(mr, "architect", "scout")) == expected


def test_slice_carries_definitions_selection_and_axes_whole(mr, live_lines):
    text = _slice(mr, "architect")
    start = live_lines.index("## Role definitions")
    guidance = live_lines.index("## Role guidance")
    for line in live_lines[start:guidance]:
        assert line in text.splitlines(), line
    table_end = max(i for i, l in enumerate(live_lines) if l.startswith("| "))
    axes = live_lines.index("## Axis definitions")
    for line in live_lines[table_end + 1:]:
        assert line in text.splitlines(), line
    assert axes > table_end


def test_slice_of_one_tier_is_smaller_than_the_whole_ladder_on_the_live_ladder(mr):
    whole = len(mr.ladder_path().read_bytes())
    for tier in mr.TIER_ORDER:
        assert len(_slice(mr, tier).encode("utf-8")) < whole, tier


def test_validation_folds_onto_fanout(mr):
    assert _slice(mr, "validation") == _slice(mr, "fanout")


def test_unknown_tier_fails_closed(mr):
    with pytest.raises(mr.RegistryError):
        _slice(mr, "wizard")


def test_a_ladder_without_a_claimant_fails_closed(mr, tmp_path):
    ladder = tmp_path / "ladder.md"
    ladder.write_text("# L\n\n## Role definitions\n\nx\n\n## Role guidance\n\n"
                      "| model | strengths | evidence |\n|---|---|---|\n| nomodel | x | pub |\n\n"
                      "## Axis definitions\n\nx\n", encoding="utf-8")
    with pytest.raises(mr.RegistryError):
        mr.ladder_slice(["architect"], path=ladder)


def test_a_drifted_table_fails_closed(mr, tmp_path):
    ladder = tmp_path / "ladder.md"
    ladder.write_text("## Role definitions\n\nx\n\n## Role guidance\n\n| name | notes |\n|---|---|\n| a | b |\n",
                      encoding="utf-8")
    with pytest.raises(mr.RegistryError):
        mr.ladder_slice(["architect"], path=ladder)


def test_cli_prints_the_slice_and_records_the_covered_tiers(mr, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "slicecli-0000-aaaa")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert mr.main(["ladder-slice", "architect", "validation"]) == 0
    assert out.getvalue().strip() == _slice(mr, "architect", "validation").strip()
    hook_state = _load("_hook_state_slice_probe", HOOKS / "_hook_state.py")
    assert hook_state.ladder_slice_tiers("slicecli-0000-aaaa") == ["architect", "fanout"]


def test_cli_without_a_session_id_prints_and_says_nothing_was_recorded(mr, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert mr.main(["ladder-slice", "architect"]) == 0
    captured = capsys.readouterr()
    assert "## Role definitions" in captured.out
    assert "not recorded" in captured.err
    assert not list(Path(tmp_path).glob("*.json"))


def test_cli_rejects_no_tier(mr, capsys):
    assert mr.main(["ladder-slice"]) == 2
    assert "usage" in capsys.readouterr().err


def test_two_runs_accumulate_covered_tiers(mr, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "slicecum-0000-aaaa")
    with contextlib.redirect_stdout(io.StringIO()):
        mr.main(["ladder-slice", "executor"])
        mr.main(["ladder-slice", "architect"])
    hook_state = _load("_hook_state_slice_probe2", HOOKS / "_hook_state.py")
    assert hook_state.ladder_slice_tiers("slicecum-0000-aaaa") == ["architect", "executor"]
    assert os.path.isdir(str(tmp_path))


def _supplementary_ladder(mr, tmp_path):
    ladder = tmp_path / "ladder.md"
    ladder.write_text("# L\n\n## Role definitions\n\nx\n\n## Role guidance\n\n"
                      "| model | strengths | evidence |\n|---|---|---|\n"
                      "| supfam | a | pub |\n| nonefam | b | pub |\n\n## Axis definitions\n\nx\n",
                      encoding="utf-8")
    data = {"transports": {"t": {}}, "models": [
        {"transport": "t", "id": "sup-1", "alias": "sup", "family": "supfam", "roles": ["fanout"],
         "supplementary": ["architect"]},
        {"transport": "t", "id": "none-1", "alias": "none", "family": "nonefam", "roles": ["fanout"]},
    ]}
    return ladder, data


def test_slice_keeps_a_row_holding_the_tier_only_as_supplementary(mr, tmp_path):
    ladder, data = _supplementary_ladder(mr, tmp_path)
    text = mr.ladder_slice(["architect"], path=ladder, data=data)
    assert _guidance_rows(text) == ["supfam"]


def test_slice_still_drops_a_row_holding_neither(mr, tmp_path):
    ladder, data = _supplementary_ladder(mr, tmp_path)
    assert "nonefam" not in _guidance_rows(mr.ladder_slice(["architect"], path=ladder, data=data))
    assert "nonefam" in _guidance_rows(mr.ladder_slice(["fanout"], path=ladder, data=data))
