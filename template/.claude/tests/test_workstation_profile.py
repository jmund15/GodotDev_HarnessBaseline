#!/usr/bin/env python3
"""Re-runnable proof for tools/workstation_profile.py: the recorded GPU tier, the live ai-worker
read, the setup stamp SessionStart compares, and the layer seam `preflight` reads.

Everything is planted: no `nvidia-smi`, no write inside `~/.claude` (the profile path is redirected
with WORKSTATION_PROFILE_PATH, the MCP config walk with CLAUDE_CONFIG_DIR and SC_WORKDIR). The one
real subprocess is `git`, for the main-checkout/worktree keying. The core runs alone in a temp
`tools/` for the seam cases, so they prove what a consumer without the engine layer receives.

    python -m pytest .claude/tests/test_workstation_profile.py -q
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
TOOLS = CLAUDE / "tools"
sys.path.insert(0, str(TOOLS))
import workstation_profile as ws  # noqa: E402

LAYER_MODULE = "workstation_profile_" + "godot"  # the sibling the core's seam reads; spelled in parts so this pure proof cites no file of a higher layer


# ---------------------------------------------------------------- harness

@contextlib.contextmanager
def patched(obj, name, value):
    saved = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, saved)


class Probe:
    """A planted `nvidia-smi` reply."""

    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def probe(stdout, fn, returncode=0):
    """Call `fn` with `subprocess.run` planted to answer a probe; a None stdout raises OSError."""
    def fake(*_args, **_kwargs):
        if stdout is None:
            raise FileNotFoundError("no such command")
        return Probe(stdout, returncode)
    with patched(ws.subprocess, "run", fake):
        return fn()


def cli(argv):
    """(exit code, printed text) for one CLI call."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ws.main(argv)
    return code, out.getvalue()


@pytest.fixture(autouse=True)
def profile_path(tmp_path, monkeypatch):
    """Every case records into its own profile file, never the owner's ~/.claude."""
    path = tmp_path / "profile.json"
    monkeypatch.setenv(ws.PROFILE_ENV, str(path))
    return path


# ---------------------------------------------------------------- fixtures

DECLARED = (".mcp.json", ".claude/reference/project_stack.md", "pins.json",
            ".claude/commands/workstation_setup.md")
PROJECT_STACK = "# Project Stack Pins\n\n- Runtime: **1.0**\n"
MCP_JSON = '{"mcpServers": {"x": {}}}\n'
PINS_JSON = '{"pin": "1.0"}\n'
ENGINE_ROOT_FILES = {"project.godot": "; planted\n",
                     "nuget.config": '<configuration><packageSources><add key="l" value="feed_dir"/>'
                                     "</packageSources></configuration>"}


def stamp_doc(paths=DECLARED, body="stamp-inputs:", front=True):
    """A `commands/workstation_setup.md` carrying the declaration the tool reads."""
    lines = ["---", "description: Provision or audit this harness on a workstation"]
    if body is not None:
        lines.append(body)
        lines.extend('  - "%s"' % p for p in paths)
    lines += ["---", "", "# /workstation_setup", ""]
    return "\n".join(lines) if front else "no frontmatter at all\n"


def reference_doc(name, paths=None, body="stamp-inputs:"):
    """A `commands/reference/<name>` file; `paths` None leaves the declaration out."""
    lines = ["---", "disable-model-invocation: true"]
    if paths is not None:
        lines.append(body)
        lines.extend('  - "%s"' % p for p in paths)
    lines += ["---", "", "# a layer reference", ""]
    return name, "\n".join(lines)


def plant_root(base, doc=None, inputs=DECLARED, references=()):
    """A repo root carrying the declared stamp inputs. Returns the root."""
    root = Path(base)
    commands = root / ".claude" / "commands"
    (commands / "reference").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "reference").mkdir(parents=True, exist_ok=True)
    (commands / "workstation_setup.md").write_text(stamp_doc(inputs) if doc is None else doc, encoding="utf-8")
    for name, text in references:
        (commands / "reference" / name).write_text(text, encoding="utf-8")
    (root / ".claude" / "reference" / "project_stack.md").write_text(PROJECT_STACK, encoding="utf-8")
    (root / ".mcp.json").write_text(MCP_JSON, encoding="utf-8")
    (root / "pins.json").write_text(PINS_JSON, encoding="utf-8")
    return root


@pytest.fixture
def root(tmp_path):
    return plant_root(tmp_path / "repo")


@pytest.fixture
def stamped(root):
    """A planted root with a recorded, current setup stamp."""
    ok, detail = ws.stamp(root, preflight_failures=[])
    assert ok, detail
    return root


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- GPU tier and probe

@pytest.mark.parametrize("mib, want", [
    (16376, "full"), (16384, "full"), (15360, "full"), (15359, "small"),
    (8188, "small"), (8192, "small"), (7680, "small"), (7679, "none"),
    (4096, "none"), (0, "none"), (None, "none"),
], ids=lambda v: str(v))
def test_a_gpu_memory_reading_maps_to_its_tier(mib, want):
    assert ws.gpu_tier(mib) == want


def test_read_gpu_parses_the_live_nvidia_smi_csv_row():
    assert probe("NVIDIA GeForce RTX 4070 Ti SUPER, 16376 MiB\n", ws.read_gpu) == ("NVIDIA GeForce RTX 4070 Ti SUPER", 16376)


def test_read_gpu_returns_no_gpu_on_a_failed_probe():
    assert probe("", ws.read_gpu, returncode=9) == (None, None)


def test_read_gpu_survives_a_missing_nvidia_smi():
    assert probe(None, ws.read_gpu) == (None, None)


def test_the_default_profile_path_is_the_user_profile_under_claude(monkeypatch):
    monkeypatch.delenv(ws.PROFILE_ENV, raising=False)
    assert ws.profile_path() == os.path.expanduser("~/.claude/workstation_profile.json")


# ---------------------------------------------------------------- declarations

def test_stamp_inputs_reads_the_declared_list_in_order(root):
    assert ws.stamp_inputs(root) == list(DECLARED)


def test_a_blank_line_inside_the_declaration_does_not_truncate_the_list(tmp_path):
    spaced = plant_root(tmp_path / "repo_spaced",
                        doc=stamp_doc(DECLARED).replace('  - "pins.json"', '\n  - "pins.json"'))
    assert ws.stamp_inputs(spaced) == list(DECLARED)


def test_a_different_declaration_is_what_the_tool_reads_not_a_hard_coded_copy(tmp_path):
    other = plant_root(tmp_path / "repo_other", doc=stamp_doc(("MADE-UP.md",)), inputs=("MADE-UP.md",))
    write(other / "MADE-UP.md", "x\n")
    assert ws.stamp_inputs(other) == ["MADE-UP.md"]


@pytest.mark.parametrize("doc", [
    stamp_doc(body=None),
    "---\nstamp-inputs:\n---\nbody\n",
    stamp_doc(front=False),
    stamp_doc(body="stamp-inputs: .mcp.json"),
], ids=["missing-declaration", "no-entries", "missing-frontmatter", "inline-scalar"])
def test_a_declaration_that_is_absent_or_malformed_declares_no_inputs(tmp_path, doc):
    assert ws.stamp_inputs(plant_root(tmp_path / "bad", doc=doc)) is None


# ---------------------------------------------------------------- reference declarations

def test_a_reference_declaration_joins_the_commands_after_it(tmp_path):
    root = plant_root(tmp_path / "with_ref", references=[reference_doc("workstation_setup_extra.md", ["extra.json"])])
    assert ws.stamp_inputs(root) == list(DECLARED) + ["extra.json"]


def test_an_input_declared_twice_is_listed_once_at_its_first_place(tmp_path):
    repeated = reference_doc("workstation_setup_extra.md", [".mcp.json", "extra.json"])
    assert ws.stamp_inputs(plant_root(tmp_path / "dup", references=[repeated])) == list(DECLARED) + ["extra.json"]


def test_a_reference_with_no_declaration_adds_nothing(tmp_path):
    silent = reference_doc("workstation_setup_silent.md", None)
    assert ws.stamp_inputs(plant_root(tmp_path / "silent", references=[silent])) == list(DECLARED)


def test_a_file_outside_the_workstation_setup_naming_is_not_read(tmp_path):
    stranger = reference_doc("unrelated_notes.md", ["stranger.json"])
    assert ws.stamp_inputs(plant_root(tmp_path / "stranger", references=[stranger])) == list(DECLARED)


@pytest.mark.parametrize("bad", [
    reference_doc("workstation_setup_bad.md", []),
    reference_doc("workstation_setup_bad.md", [], body="stamp-inputs: x.json"),
], ids=["empty", "inline-scalar"])
def test_an_empty_or_inline_reference_declaration_reads_as_no_inputs_at_all(tmp_path, bad):
    assert ws.stamp_inputs(plant_root(tmp_path / "badref", references=[bad])) is None


@pytest.fixture
def stamped_with_reference(tmp_path):
    root = plant_root(tmp_path / "ref_stamp", references=[reference_doc("workstation_setup_extra.md", ["extra.json"])])
    write(root / "extra.json", "{}\n")
    ws.stamp(root, preflight_failures=[])
    return root


def test_a_stamped_root_with_a_reference_input_reads_ok(stamped_with_reference):
    assert ws.setup_status(stamped_with_reference) == "OK"


def test_an_edited_reference_input_reads_stale_and_names_it(stamped_with_reference):
    write(stamped_with_reference / "extra.json", '{"changed": true}\n')
    status = ws.setup_status(stamped_with_reference)
    assert status.startswith("STALE") and "extra.json" in status


# ---------------------------------------------------------------- stamping

def test_the_cli_default_root_is_the_repository_and_its_declaration_resolves():
    assert ws.stamp_inputs(ws._repo_root()) is not None


def test_stamp_refuses_with_no_readable_declaration(tmp_path):
    assert ws.stamp(plant_root(tmp_path / "nodecl", doc=stamp_doc(body=None)), preflight_failures=[])[0] is False


def test_stamp_refuses_on_a_failed_preflight_and_records_nothing(root):
    refused = ws.stamp(root, preflight_failures=["toolchain is missing"])
    assert not refused[0] and "refusing" in refused[1] and ws.setup_status(root) != "OK", refused


def test_a_stamped_root_reports_ok(stamped):
    assert ws.setup_status(stamped) == "OK"


def test_an_edited_input_reads_stale_and_names_it(stamped):
    write(stamped / ".mcp.json", '{"mcpServers": {}}\n')
    status = ws.setup_status(stamped)
    assert status.startswith("STALE") and ".mcp.json" in status


def test_a_missing_declared_input_reads_stale_and_names_it(stamped):
    (stamped / "pins.json").unlink()
    status = ws.setup_status(stamped)
    assert status.startswith("STALE") and "pins.json" in status, status


def test_stamping_again_clears_the_stale_report(stamped):
    write(stamped / ".mcp.json", '{"mcpServers": {}}\n')
    ok, detail = ws.stamp(stamped, preflight_failures=[])
    assert ok and ws.setup_status(stamped) == "OK", detail


def test_a_broken_declaration_reads_unaudited_even_with_a_recorded_entry(stamped):
    write(stamped / ".claude" / "commands" / "workstation_setup.md", stamp_doc(body="stamp-inputs: .mcp.json"))
    assert ws.setup_status(stamped).startswith("UNAUDITED"), ws.setup_status(stamped)


def test_restoring_the_declaration_restores_ok(stamped):
    command = stamped / ".claude" / "commands" / "workstation_setup.md"
    write(command, stamp_doc(body="stamp-inputs: .mcp.json"))
    write(command, stamp_doc())
    assert ws.setup_status(stamped) == "OK"


def test_an_unstamped_root_reads_unaudited(root):
    status = ws.setup_status(root)
    assert status.startswith("UNAUDITED"), status


def test_a_checkout_without_workstation_setup_has_no_setup_status(tmp_path):
    assert ws.setup_status(tmp_path / "no_such_checkout") is None


def test_a_corrupt_profile_is_not_a_crash_it_reads_unaudited_tier_none_and_an_empty_read(stamped, profile_path):
    write(profile_path, "{not json at all")
    assert ws.setup_status(stamped).startswith("UNAUDITED")
    assert ws.local_tier() is None and ws.read_profile() == {}


# ---------------------------------------------------------------- git keying

@pytest.fixture
def git_pair(tmp_path):
    """A real main checkout plus a linked worktree of it. Returns (main, worktree)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "proof",
                "GIT_AUTHOR_EMAIL": "proof@example.invalid", "GIT_COMMITTER_NAME": "proof",
                "GIT_COMMITTER_EMAIL": "proof@example.invalid"})

    def git(cwd, *args):
        result = subprocess.run(["git"] + list(args), cwd=str(cwd), env=env,
                                capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (args[0], result.stderr.strip()))
        return result.stdout

    main_co = tmp_path / "main"
    main_co.mkdir()
    git(main_co, "init", "-q")
    git(main_co, "commit", "-q", "--allow-empty", "-m", "init")
    worktree = tmp_path / "wt"
    git(main_co, "worktree", "add", "-q", "--detach", str(worktree))
    return main_co, worktree


def test_a_linked_worktree_keys_to_its_main_checkout(git_pair):
    main_co, worktree = git_pair
    assert ws.profile_key(main_co) == ws.profile_key(worktree)


def test_a_worktree_of_the_stamped_checkout_reads_ok(git_pair):
    main_co, worktree = git_pair
    plant_root(main_co)
    plant_root(worktree)
    ws.stamp(main_co, preflight_failures=[])
    assert ws.setup_status(worktree) == "OK"


# ---------------------------------------------------------------- the layer seam

def load_core_alone(base, sibling_source=None):
    """`workstation_profile.py` copied into a `tools/` of its own and loaded from there. With
    `sibling_source`, that text is the installed layer module beside it. Returns
    the module, which sees exactly the files a consumer of this layer set would hold."""
    directory = Path(base) / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(TOOLS / "workstation_profile.py", directory / "workstation_profile.py")
    if sibling_source is not None:
        (directory / (LAYER_MODULE + ".py")).write_text(sibling_source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("wp_alone_%s" % Path(base).name, directory / "workstation_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine_tree(tmp_path):
    tree = plant_root(tmp_path / "engine_tree")
    for name, text in ENGINE_ROOT_FILES.items():
        write(tree / name, text)
    return tree


def test_a_core_with_no_layer_module_runs_no_engine_dotnet_or_feed_check_on_an_engine_tree(tmp_path, engine_tree, monkeypatch):
    monkeypatch.delenv("GODOT_BIN", raising=False)
    core = load_core_alone(tmp_path / "core_only")
    assert core.preflight(engine_tree) == []


@pytest.mark.parametrize("name", ["godot_project", "local_nuget_feeds", "_cmd_exists"])
def test_the_core_carries_no_engine_or_toolchain_probe(tmp_path, name):
    assert not hasattr(load_core_alone(tmp_path / "core_probe"), name)


def test_a_layer_module_beside_the_core_supplies_the_preflight_failures(tmp_path, engine_tree):
    layered = load_core_alone(tmp_path / "core_plus_layer", "def preflight(root):\n    return ['planted layer failure']\n")
    assert layered.preflight(engine_tree) == ["planted layer failure"]


def test_a_layer_module_that_cannot_load_refuses_the_stamp_rather_than_passing_it(tmp_path, engine_tree):
    broken = load_core_alone(tmp_path / "core_plus_broken", "def preflight(root:\n")
    failures = broken.preflight(engine_tree)
    assert len(failures) == 1 and LAYER_MODULE in failures[0] and "load" in failures[0], failures


def test_a_layer_module_with_no_preflight_refuses_the_stamp_rather_than_passing_it(tmp_path, engine_tree):
    headless = load_core_alone(tmp_path / "core_plus_headless", "VALUE = 1\n")
    failures = headless.preflight(engine_tree)
    assert len(failures) == 1 and LAYER_MODULE in failures[0], failures


# ---------------------------------------------------------------- ai-worker registration walk

@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    (cfg / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("SC_WORKDIR", str(tmp_path / "nowhere"))
    return cfg


def test_ai_worker_is_absent_without_a_registration(config_dir):
    write(config_dir / ".claude.json", json.dumps({"mcpServers": {"other": {}}}))
    assert ws.ai_worker() == "absent"


def test_ai_worker_is_present_with_a_user_scope_registration(config_dir):
    write(config_dir / ".claude.json", json.dumps({"mcpServers": {"ai-worker": {"command": "python"}}}))
    assert ws.ai_worker() == "present"


def test_ai_worker_is_present_from_the_project_scope_the_walk_reads(config_dir, tmp_path):
    write(config_dir / ".claude.json", json.dumps(
        {"projects": {str(tmp_path / "nowhere"): {"mcpServers": {"ai-worker": {"command": "python"}}}}}))
    assert ws.ai_worker() == "present"


def test_an_unreadable_registration_file_reads_absent_never_present(config_dir):
    write(config_dir / ".claude.json", "{not json")
    assert ws.ai_worker() == "absent"


def test_a_config_walk_that_cannot_run_is_none_not_absent(config_dir, monkeypatch):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("registration read unavailable")
    monkeypatch.setattr(ws, "_registered_servers", _raise)
    assert ws.ai_worker() is None


# ---------------------------------------------------------------- record

GPU_ROW = "NVIDIA GeForce RTX 4070 Ti SUPER, 16376 MiB\n"


def test_record_probes_the_machine_and_writes_the_tier():
    probe(GPU_ROW, ws.record)
    profile = ws.read_profile()
    assert (profile.get("local_tier") == "full" and profile.get("gpu") == {
        "name": "NVIDIA GeForce RTX 4070 Ti SUPER", "vram_mib": 16376} and ws.local_tier() == "full"), profile


def test_record_keeps_the_ai_worker_observation_the_caller_names():
    ws.record(ai_worker_value="absent")
    assert ws.read_profile().get("ai_worker") == "absent", ws.read_profile()


def test_a_machine_with_no_gpu_records_tier_none():
    probe(None, ws.record)
    assert ws.local_tier() == "none", ws.read_profile()


def test_record_leaves_the_setup_stamp_alone(stamped):
    before = ws.read_profile().get("setup")
    probe(None, ws.record)
    assert before and ws.read_profile().get("setup") == before, (before, ws.read_profile().get("setup"))


# ---------------------------------------------------------------- CLI

def test_the_cli_record_exits_zero_and_writes_a_tier():
    """The probe is planted: no real nvidia-smi is read."""
    code, _ = probe(None, lambda: cli(["record", "--ai-worker", "absent"]))
    assert code == 0 and ws.local_tier() in ws.TIERS


@pytest.fixture
def cli_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "preflight", lambda _root: [])
    return plant_root(tmp_path / "cli")


def test_the_cli_stamp_exits_zero(cli_root):
    code, _ = cli(["stamp", "--root", str(cli_root), "--degraded", "WSL"])
    assert code == 0


def test_the_cli_status_prints_the_recorded_status(cli_root):
    cli(["stamp", "--root", str(cli_root), "--degraded", "WSL"])
    code, out = cli(["status", "--root", str(cli_root)])
    assert (code, out.strip()) == (0, "OK")


def test_the_cli_stamp_records_the_degraded_rows_it_is_given(cli_root):
    cli(["stamp", "--root", str(cli_root), "--degraded", "WSL"])
    assert ws.read_profile()["setup"][ws.profile_key(cli_root)].get("degraded") == ["WSL"]


def test_a_later_stamp_keeps_the_record_readable(cli_root):
    cli(["stamp", "--root", str(cli_root), "--degraded", "WSL"])
    code, _ = cli(["stamp", "--root", str(cli_root)])
    assert (code, ws.setup_status(cli_root)) == (0, "OK")


def test_the_cli_stamp_refuses_with_a_nonzero_exit_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "preflight", lambda _root: ["toolchain is missing"])
    code, out = cli(["stamp", "--root", str(plant_root(tmp_path / "refused"))])
    assert code != 0 and "toolchain is missing" in out, (code, out)
