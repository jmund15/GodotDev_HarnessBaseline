"""Proof for the free pool's S1 slice: `free_pool.py` ingest and member state, the registry's `pool` and
`pubScore` fields, the ladder's pool-name set, and the member-keyed exhausted marker in sidecar_common.sh.

The inputs are planted by `_free_pool_planted` (a trimmed cut of the live captures: `opencode models
opencode`, https://models.dev/api.json and `git show a08677e16`), so the proof runs without the
never-ship fixture tree.
"""
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _free_pool_planted as free_pool_planted  # noqa: E402

ROOT = HERE.parent
TOOLS = ROOT / "tools"
LIB = ROOT / "scripts" / "lib" / "sidecar_common.sh"
FIX = free_pool_planted.plant(Path(tempfile.mkdtemp(prefix="free_pool_planted_")))
LIVE_REGISTRY = ROOT / "reference" / "external_models.json"
# A frozen registry whose pool members are all available, so a live status change never moves a case.
POOL_REGISTRY = FIX / "registry_pool_s1.json"
TODAY = "2026-10-13"
# Git Bash, never the WSL `bash.exe` on PATH: the sidecar helper needs the Git Bash path translation.
BASH = next((c for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")
             if os.path.exists(c)), "bash")

# The registry rows that carry `pool: free`: every available opencode-cli row whose id contains `-free`.
POOL_MEMBERS = sorted([
    "cat-jam-free", "exo-free", "ling-3.1-flash-free", "longcat-2.5-preview-free",
    "mimo-v2.6-flash-free", "muse-spark-1.3-contributor-free", "space-bunny-free", "step-5-preview-free",
])


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mr():
    sys.path.insert(0, str(TOOLS))
    return _load("mr_free_pool", TOOLS / "model_registry.py")


@pytest.fixture(scope="module")
def fp():
    sys.path.insert(0, str(TOOLS))
    return _load("free_pool", TOOLS / "free_pool.py")


@pytest.fixture
def exhausted_dir(tmp_path, monkeypatch):
    path = tmp_path / "exhausted"
    monkeypatch.setenv("SIDECAR_EXHAUSTED_DIR", str(path))
    return path


def _copy(tmp_path, source, name="external_models.json"):
    dest = tmp_path / name
    dest.write_bytes(Path(source).read_bytes())
    return dest


def _pre_cat_jam(tmp_path):
    """The planted registry as it stood before the cat-jam row was ingested."""
    data = copy.deepcopy(free_pool_planted.REGISTRY)
    data["models"] = [m for m in data["models"] if m.get("upstreamId") != "cat-jam-free"]
    path = tmp_path / "registry_pre_cat_jam.json"
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    return path


def _row(data, upstream):
    return next(m for m in data["models"] if m.get("upstreamId") == upstream)


def _catalog_without(tmp_path, vanished_id):
    kept = [line for line in (FIX / "catalog_live.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() != f"opencode/{vanished_id}"]
    path = tmp_path / "catalog_without.txt"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return path


def _bash(script, *args, env=None):
    return subprocess.run([BASH, "-c", script, "bash", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, **(env or {})})


# ----------------------------------------------------------------- registry: pool and pubScore fields

def test_pool_free_marks_only_free_cli_rows(mr):
    data = mr.load(LIVE_REGISTRY)
    pooled = [m for m in data["models"] if m.get("pool") == "free"]
    assert pooled
    assert all(m["transport"] == "opencode-cli" and m["upstreamId"].endswith("-free") for m in pooled)
    assert not [m["id"] for m in data["models"] if m.get("pool") and m.get("transport") != "opencode-cli"]


def test_validate_rejects_a_non_string_or_empty_pool(mr):
    base = mr.load(POOL_REGISTRY)
    for bad in (3, "", "   "):
        data = copy.deepcopy(base)
        _row(data, "cat-jam-free")["pool"] = bad
        with pytest.raises(mr.RegistryError, match="pool"):
            mr._validate(data, "fixture")


def test_validate_pubscore_needs_value_source_and_date(mr):
    base = mr.load(POOL_REGISTRY)
    good = {"value": 0.61, "source": "named public board", "date": TODAY}
    data = copy.deepcopy(base)
    _row(data, "cat-jam-free")["pubScore"] = good
    mr._validate(data, "fixture")
    for key in good:
        partial = {k: v for k, v in good.items() if k != key}
        data = copy.deepcopy(base)
        _row(data, "cat-jam-free")["pubScore"] = partial
        with pytest.raises(mr.RegistryError, match="pubScore"):
            mr._validate(data, "fixture")
    for field, value in (("date", "2026-13-45"), ("value", "high"), ("source", "  ")):
        data = copy.deepcopy(base)
        _row(data, "cat-jam-free")["pubScore"] = {**good, field: value}
        with pytest.raises(mr.RegistryError, match="pubScore"):
            mr._validate(data, "fixture")


# ----------------------------------------------------------------- ladder: pool-name set

def test_pool_names_are_scout_plus_each_registry_pool(mr):
    data = mr.load(POOL_REGISTRY)
    assert mr.pool_names(data) == {"scout pool", "free pool"}


def test_scout_pool_cell_still_covers_its_listed_families(mr):
    data = mr.load(POOL_REGISTRY)
    assert mr.ladder_members("scout pool (minimax, hy)") == ["minimax", "hy"]
    assert not [m for m in mr.ladder_rows("scout pool (minimax, hy)", data) if m.get("pool")]


def _ladder_with_free_pool():
    """The live ladder plus a test-only `free pool` row beside its `scout pool` row."""
    lines = (ROOT / "reference" / "model_ladder_evidence.md").read_text(encoding="utf-8").splitlines(True)
    scout = next(line for line in lines if line.startswith("| scout pool"))
    free = re.sub(r"^\| scout pool[^|]*\|", "| free pool |", scout)
    index = lines.index(scout)
    return lines[:index + 1] + [free] + lines[index + 1:]


def test_free_pool_cell_resolves_to_the_pool_free_rows(mr):
    data = mr.load(POOL_REGISTRY)
    for m in data["models"]:
        if m.get("pool") == "free":
            m["roles"] = ["scout"]
    rows = mr.ladder_rows("free pool", data)
    assert sorted(m["upstreamId"] for m in rows) == POOL_MEMBERS
    tiers = mr.parse_role_tiers(_ladder_with_free_pool(), data)
    assert "free pool" in tiers["scout"]
    assert "scout pool" in tiers["scout"]


def test_for_role_scout_still_lists_scout_pool(tmp_path, mr):
    """The scout tier's ladder cut names the free pool; the table reads a planted registry and capacity cache."""
    # Planted: the live plans may leave every scout-pool family unclaimed or exhausted; the case tests the listing, not the roster.
    data = json.loads(LIVE_REGISTRY.read_text(encoding="utf-8"))
    ladder = (ROOT / "reference" / "model_ladder_evidence.md").read_text(encoding="utf-8")
    cell = next(line.split("|")[1].strip() for line in ladder.splitlines() if line.startswith("| scout pool"))
    pool = {m["alias"] for m in mr.ladder_rows(cell, data)}
    member = next(m for m in data["models"] if m["alias"] in pool
                  and (m.get("effort") or {}).get("evidence") != "unmeasured")
    member["roles"] = sorted(set(member.get("roles") or []) | {"scout"})
    reference = tmp_path / ".claude" / "reference"
    reference.mkdir(parents=True)
    (reference / "model_ladder_evidence.md").write_text(ladder, encoding="utf-8")
    registry = reference / "external_models.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    now = datetime.now(timezone.utc)
    reading = {"schemaVersion": 1, "transport": "anthropic", "costModel": "plan-quota", "sourceKind": "live",
               "observedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "available",
               "error": None, "liveFailure": None, "balance": None,
               "quota": {"windows": [{"name": "seven_day", "usedPercent": 40,
                                      "resetsAt": now.timestamp() + 3 * 86400, "windowDurationMins": 10080,
                                      "pressure": 1.0, "band": "Hot"}],
                         "dispatchBand": "Hot", "routingBand": "Hot", "routingWindow": "seven_day",
                         "bindingWindow": "seven_day", "credits": None}}
    cache = tmp_path / "capacity.json"
    cache.write_text(json.dumps({"anthropic": reading, member["transport"]: dict(reading, transport=member["transport"])}),
                     encoding="utf-8")
    env = dict(os.environ, PROVIDER_CAPACITY_CACHE=str(cache), HARNESS_MODEL_REGISTRY=str(registry))
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    out = subprocess.run([sys.executable, str(TOOLS / "model_registry.py"), "for-role", "scout",
                          "--from", "anthropic"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env)
    assert out.returncode == 0, out.stderr
    assert "scout pool" in out.stdout


# ----------------------------------------------------------------- sync: add, vanish, unchanged, parity

def test_sync_adds_cat_jam_row_identical_to_a08677e16(fp, tmp_path):
    registry = _pre_cat_jam(tmp_path)
    report = fp.sync(registry_path=registry, catalog_file=FIX / "catalog_verbose_live.txt",
                     models_dev_file=FIX / "models_dev_opencode_cat_jam.json", today=TODAY)
    assert report["added"] == ["cat-jam-free"]
    # The frozen row predates the registry rule that a row states its catalog input modalities.
    expected = dict(json.loads((FIX / "cat_jam_row_a08677e16.json").read_text(encoding="utf-8")), input=["text"])
    assert _row(json.loads(registry.read_text(encoding="utf-8")), "cat-jam-free") == expected


def test_sync_vanish_marks_the_pool_member_unavailable_and_dated(fp, tmp_path):
    registry = _copy(tmp_path, POOL_REGISTRY)
    report = fp.sync(registry_path=registry, catalog_file=_catalog_without(tmp_path, "exo-free"),
                     models_dev_file=FIX / "models_dev_opencode_cat_jam.json", today=TODAY)
    assert report["vanished"] == ["exo-free"]
    row = _row(json.loads(registry.read_text(encoding="utf-8")), "exo-free")
    assert row["status"]["state"] == "unavailable"
    assert TODAY in row["status"]["reason"]


def test_a_generated_cli_row_claims_no_tier_and_validates(mr, fp):
    data = copy.deepcopy(mr.load())
    entry = {"name": "Zz Test Free", "cost": {"input": 0, "output": 0, "cache_read": 0},
             "limit": {"context": 131072, "output": 8192}}
    row = fp._cli_row("zz-test-9-free", entry, ["low"], "2026-09-25",
                      max(m["order"] for m in data["models"]) + 1, data)
    data["models"].append(row)
    mr._validate(data, "fixture")
    assert row["roles"] == []


def test_a_generated_cli_row_states_the_catalog_modalities_in_canonical_order(mr, fp):
    data = copy.deepcopy(mr.load())
    entry = {"name": "Zz Test Free", "cost": {"input": 0, "output": 0, "cache_read": 0},
             "limit": {"context": 131072, "output": 8192}, "modalities": {"input": ["video", "text", "image"]}}
    row = fp._cli_row("zz-test-9-free", entry, ["low"], "2026-09-25", 1, data)
    assert row["input"] == ["text", "image", "video"] and "inputGap" not in row


def test_a_generated_cli_row_with_no_catalog_modalities_states_an_input_gap_never_text_only(mr, fp):
    data = copy.deepcopy(mr.load())
    entry = {"name": "Zz Test Free", "cost": {"input": 0, "output": 0, "cache_read": 0},
             "limit": {"context": 131072, "output": 8192}}
    row = fp._cli_row("zz-test-9-free", entry, ["low"], "2026-09-25", 1, data)
    assert "input" not in row and row["inputGap"]["asOf"] == "2026-09-25"


def test_sync_unchanged_catalog_writes_nothing(fp, tmp_path):
    registry = _copy(tmp_path, POOL_REGISTRY)
    before = registry.read_bytes()
    report = fp.sync(registry_path=registry, catalog_file=FIX / "catalog_live.txt",
                     models_dev_file=FIX / "models_dev_opencode_cat_jam.json", today=TODAY)
    assert report["added"] == [] and report["vanished"] == []
    assert registry.read_bytes() == before


def test_sync_dry_run_reports_without_writing(fp, tmp_path):
    registry = _pre_cat_jam(tmp_path)
    before = registry.read_bytes()
    report = fp.sync(registry_path=registry, catalog_file=FIX / "catalog_verbose_live.txt",
                     models_dev_file=FIX / "models_dev_opencode_cat_jam.json", today=TODAY, dry_run=True)
    assert report["added"] == ["cat-jam-free"]
    assert registry.read_bytes() == before


@pytest.mark.parametrize("label, content", [
    ("truncated JSON", "{\"opencode\": {\"models\": {"),
    ("no opencode provider", "{\"anthropic\": {}}"),
    ("entry without a cost block", json.dumps({"opencode": {"models": {"cat-jam-free": {
        "id": "cat-jam-free", "name": "Cat Jam Free", "limit": {"context": 262144, "output": 131072}}}}})),
])
def test_sync_refuses_malformed_models_dev_and_leaves_the_registry(fp, tmp_path, label, content):
    registry = _pre_cat_jam(tmp_path)
    before = registry.read_bytes()
    models_dev = tmp_path / "models_dev.json"
    models_dev.write_text(content, encoding="utf-8")
    with pytest.raises(fp.SyncError):
        fp.sync(registry_path=registry, catalog_file=FIX / "catalog_verbose_live.txt",
                models_dev_file=models_dev, today=TODAY)
    assert registry.read_bytes() == before, label


def test_sync_cli_refusal_exits_2_with_a_message(tmp_path):
    registry = _pre_cat_jam(tmp_path)
    models_dev = tmp_path / "models_dev.json"
    models_dev.write_text("{not json", encoding="utf-8")
    out = subprocess.run([sys.executable, str(TOOLS / "free_pool.py"), "sync", "--registry", str(registry),
                          "--catalog-file", str(FIX / "catalog_verbose_live.txt"),
                          "--models-dev-file", str(models_dev)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert out.returncode == 2
    assert "models.dev" in out.stderr
    assert "Traceback" not in out.stderr


# ----------------------------------------------------------------- S7: a member with no variants is `none`

def _registry_without_mimo(tmp_path):
    """A planted copy with the mimo row removed, so the live catalog reads it as a new id."""
    data = json.loads((FIX / "registry_pool_s1.json").read_text(encoding="utf-8"))
    data["models"] = [m for m in data["models"] if m.get("upstreamId") != "mimo-v2.6-flash-free"]
    path = tmp_path / "external_models.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def test_sync_adds_a_variantless_member_with_effort_none(fp, tmp_path):
    registry = _registry_without_mimo(tmp_path)
    report = fp.sync(registry_path=registry, catalog_file=FIX / "catalog_verbose_live.txt",
                     models_dev_file=FIX / "models_dev_opencode_mimo_v26.json", today=TODAY)
    assert report["added"] == ["mimo-v2.6-flash-free"]
    row = _row(json.loads(registry.read_text(encoding="utf-8")), "mimo-v2.6-flash-free")
    assert row["effortValues"] == ["none"]
    assert "pool" not in row


def _cat_jam_entry_as(cli_id):
    entry = json.loads((FIX / "models_dev_opencode_cat_jam.json").read_text(encoding="utf-8"))
    return {**entry["opencode"]["models"]["cat-jam-free"], "id": cli_id}


def test_new_row_alias_is_dotless_and_family_inherits_the_registered_line(fp):
    data = json.loads(POOL_REGISTRY.read_text(encoding="utf-8"))
    row = fp._cli_row("glm-5.3-flash-free", _cat_jam_entry_as("glm-5.3-flash-free"), ["low"], TODAY, 999, data)
    assert row["alias"] == "cli-glm-5-3-flash"
    assert row["family"] == "glm-flash"


def test_new_row_with_no_registered_line_takes_its_product_line(fp):
    data = json.loads(POOL_REGISTRY.read_text(encoding="utf-8"))
    row = fp._cli_row("nemotron-3.5-super-vl-free", _cat_jam_entry_as("x"), ["low"], TODAY, 999, data)
    assert row["alias"] == "cli-nemotron-3-5-super-vl"
    assert row["family"] == "nemotron-super-vl"


def test_new_row_alias_collision_refuses(fp):
    data = json.loads(POOL_REGISTRY.read_text(encoding="utf-8"))
    with pytest.raises(fp.SyncError):
        fp._cli_row("cat-jam-free", _cat_jam_entry_as("cat-jam-free"), ["low"], TODAY, 999, data)


def test_effort_values_keeps_none_through_the_transport_narrowing(mr):
    data = mr.load(POOL_REGISTRY)
    row = copy.deepcopy(_row(data, "mimo-v2.6-flash-free"))
    row["effortValues"] = ["none"]
    assert mr.effort_values(row, data) == ["none"]


def test_effort_values_cli_prints_none_for_a_variantless_member(mr, tmp_path):
    data = json.loads((FIX / "registry_pool_s1.json").read_text(encoding="utf-8"))
    _row(data, "mimo-v2.6-flash-free")["effortValues"] = ["none"]
    path = tmp_path / "external_models.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    out = subprocess.run([sys.executable, str(TOOLS / "model_registry.py"), "effort-values", "cli-mimo-flash"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         env={**os.environ, mr.ENV_OVERRIDE: str(path)})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == '["none"]'


def test_a_row_with_variants_keeps_its_variant_list(fp, tmp_path):
    registry = _pre_cat_jam(tmp_path)
    fp.sync(registry_path=registry, catalog_file=FIX / "catalog_verbose_live.txt",
            models_dev_file=FIX / "models_dev_opencode_cat_jam.json", today=TODAY)
    row = _row(json.loads(registry.read_text(encoding="utf-8")), "cat-jam-free")
    expected = json.loads((FIX / "cat_jam_row_a08677e16.json").read_text(encoding="utf-8"))
    assert row["effortValues"] == expected["effortValues"]
    assert "none" not in row["effortValues"]


def test_stealth_new_id_is_not_pooled_so_it_is_not_ready(fp, tmp_path, exhausted_dir):
    registry = _pre_cat_jam(tmp_path)
    fp.sync(registry_path=registry, catalog_file=FIX / "catalog_verbose_live.txt",
            models_dev_file=FIX / "models_dev_opencode_cat_jam.json", today=TODAY)
    row = _row(json.loads(registry.read_text(encoding="utf-8")), "cat-jam-free")
    assert "pool" not in row and "pubScore" not in row
    ready, members = fp.ready_count(registry_path=registry)
    assert "cat-jam-free" not in members


# ----------------------------------------------------------------- member markers

def test_cool_refuses_only_that_member_and_never_the_transport(fp, exhausted_dir):
    fp.cool("cat-jam-free", 420)
    ready, members = fp.ready_count(registry_path=POOL_REGISTRY)
    assert members == POOL_MEMBERS
    assert ready == len(POOL_MEMBERS) - 1
    assert not (exhausted_dir / "opencode-cli.json").exists()


def test_cool_without_a_reset_keeps_the_stated_fallback(fp, exhausted_dir):
    # The launcher passes no reset (opencode_run_sidecar.sh oc_run): no captured rate-limit line names one,
    # so the marker takes the bounded fallback and states no reset.
    before = time.time()
    marker = fp.cool("synthetic-free")
    after = time.time()
    assert int(before + fp.COOLDOWN_SECONDS) <= marker["expiresAt"] <= int(after + fp.COOLDOWN_SECONDS)
    assert marker["resetsAt"] is None


def test_shell_member_mark_refuses_only_that_member(exhausted_dir):
    script = ('SC_TRANSPORT=opencode-cli; source "$1"; '
              'SC_USAGE_LIMIT_RESET=$(( $(date +%s) + 420 )) sc_mark_exhausted cat-jam-free; '
              '( sc_gate_exhausted exo-free ); echo "exo=$?"; '
              '( sc_gate_exhausted cat-jam-free ); echo "cat=$?"; '
              '( sc_gate_exhausted ); echo "transport=$?"')
    out = _bash(script, LIB.as_posix(), env={"SIDECAR_EXHAUSTED_DIR": exhausted_dir.as_posix()})
    assert "exo=0" in out.stdout, out.stderr
    assert "cat=10" in out.stdout, out.stderr
    assert "transport=0" in out.stdout, out.stderr
    assert not (exhausted_dir / "opencode-cli.json").exists()


def test_transport_wide_marker_path_is_unchanged(exhausted_dir):
    out = _bash('SC_TRANSPORT=opencode-cli; source "$1"; sc_exhausted_marker', LIB.as_posix(),
                env={"SIDECAR_EXHAUSTED_DIR": exhausted_dir.as_posix()})
    assert out.stdout.strip().endswith("opencode-cli.json"), out.stderr


def test_python_cool_and_shell_member_marker_share_one_format(fp, exhausted_dir):
    fp.cool("cat-jam-free", 420)
    py_marker = json.loads((exhausted_dir / "members" / "opencode-cli" / "cat-jam-free.json").read_text(encoding="utf-8"))
    shell_script = ('SC_TRANSPORT=opencode-cli; source "$1"; '
                    'SC_USAGE_LIMIT_RESET=$(( $(date +%s) + 420 )) sc_mark_exhausted exo-free; '
                    '( sc_gate_exhausted exo-free ); echo "exo=$?"')
    out = _bash(shell_script, LIB.as_posix(), env={"SIDECAR_EXHAUSTED_DIR": exhausted_dir.as_posix()})
    assert "exo=10" in out.stdout, out.stderr
    shell_marker = json.loads((exhausted_dir / "members" / "opencode-cli" / "exo-free.json").read_text(encoding="utf-8"))
    assert set(py_marker) == set(shell_marker)
    ready, members = fp.ready_count(registry_path=POOL_REGISTRY)
    assert ready == len(POOL_MEMBERS) - 2


def test_status_shows_cooled_member_and_its_failure_count(fp, exhausted_dir):
    fp.cool("cat-jam-free", 420)
    lines = fp.status_lines(registry_path=POOL_REGISTRY)
    line = next(text for text in lines if "cat-jam-free" in text)
    assert "cooled" in line and "failures 1" in line
    other = next(text for text in lines if "exo-free" in text)
    assert "ready" in other


def test_cool_cli_cools_the_named_member_only(fp, exhausted_dir):
    done = subprocess.run([sys.executable, str(TOOLS / "free_pool.py"), "cool", "cat-jam-free"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, "SIDECAR_EXHAUSTED_DIR": str(exhausted_dir)})
    assert done.returncode == 0, done.stderr
    ready, members = fp.ready_count(registry_path=POOL_REGISTRY)
    assert ready == len(POOL_MEMBERS) - 1
    assert not (exhausted_dir / "opencode-cli.json").exists()


def test_the_unavailable_ling_row_leaves_the_pool(fp):
    data = json.loads(LIVE_REGISTRY.read_text(encoding="utf-8"))
    row = _row(data, "ling-3.1-flash-free")
    assert row["status"]["state"] == "unavailable" and row["status"]["reason"]
    ready, members = fp.ready_count()
    assert "ling-3.1-flash-free" not in members


# ----------------------------------------------------------------- member state (the bench grid's dispatch gate)

def test_member_state_ready_cooled_and_unknown(fp, exhausted_dir):
    assert fp.member_state("cli-exo", POOL_REGISTRY) == ("ready", "")
    fp.cool("exo-free", 600)
    state, detail = fp.member_state("cli-exo", POOL_REGISTRY)
    assert state == "cooled" and 590 <= int(detail) <= 600
    assert fp.member_state("cli-cat-jam", POOL_REGISTRY) == ("ready", "")
    assert fp.member_state("no-such-alias", POOL_REGISTRY) == ("ready", "")


def test_member_state_unavailable_names_the_registry_reason(fp, exhausted_dir, tmp_path):
    data = json.loads(POOL_REGISTRY.read_text(encoding="utf-8"))
    _row(data, "exo-free")["status"] = {"state": "unavailable", "since": TODAY, "reason": "Zen answers Model is unavailable"}
    registry = tmp_path / "external_models.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    fp.cool("exo-free", 600)
    state, detail = fp.member_state("cli-exo", registry)
    assert state == "unavailable" and "Model is unavailable" in detail


def test_state_cli_prints_one_line_and_honors_the_registry_env(tmp_path, exhausted_dir):
    env = {**os.environ, "SIDECAR_EXHAUSTED_DIR": str(exhausted_dir), "HARNESS_MODEL_REGISTRY": str(POOL_REGISTRY)}
    run = lambda alias: subprocess.run([sys.executable, str(TOOLS / "free_pool.py"), "state", alias], capture_output=True,
                                       text=True, stdin=subprocess.DEVNULL, env=env)
    assert run("cli-exo").stdout.strip() == "ready"
    subprocess.run([sys.executable, str(TOOLS / "free_pool.py"), "cool", "exo-free"], env=env, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)
    out = run("cli-exo")
    assert out.returncode == 0 and re.fullmatch(r"cooled \d+", out.stdout.strip()), out.stdout + out.stderr
