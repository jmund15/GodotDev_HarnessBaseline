#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader.py, the generic SessionStart steps: sidecar
probes only for available off-transport routes, the due line, the git and upstream-drift context, the
memory index, the setup stamp, and the step-module seam (an absent module is skipped, a present one is
called, a faulty one is reported and never blocks). The language-server step is proven in
test_session_context_loader_csharp.py; a project's own step modules are proven in that project. Pure in-process — no
git or toolchain spawn, except the one drift case that plants a real clone.

    python -m pytest .claude/tests/test_session_context_loader.py -q
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import session_context_loader as scl  # noqa: E402
from _hook_parts import HOOK_TEXT_CAP  # noqa: E402

PAYLOAD = {"source": "startup", "session_id": "main-proof"}
MAIN_PATCHES = {
    "is_worktree": lambda: False,
    "is_cloud": lambda: False,
    "sweep_stray_search_indexes": lambda _root: None,
    "setup_submodule": lambda _root: "OK",
    "sidecar_health": lambda _root: {"seat-b": "UNAVAILABLE (adapters/b.py not present)"},
    "get_git_branch": lambda: "main",
    "get_uncommitted_count": lambda: 0,
    "get_upstream_drift": lambda: (24, 3, 30.0),
    "get_recent_commits": lambda count=3, cwd=None: [],
}
ROOTS_CASES = [("startup", False), ("compact", False), ("startup", True)]


@pytest.fixture(autouse=True)
def clean_step_faults():
    scl._STEP_FAULTS.clear()
    yield
    scl._STEP_FAULTS.clear()


def run_main(root, patches, source="startup", step_modules=(), sidecar=False):
    """The SessionStart main() output for one run in `root`; asserts it exits 0."""
    stdout = io.StringIO()
    exit_codes = []
    with contextlib.redirect_stdout(stdout), patch.dict(os.environ, {}, clear=False):
        if sidecar:
            os.environ["CLAUDE_CODE_SIDECAR"] = "1"
        else:
            os.environ.pop("CLAUDE_CODE_SIDECAR", None)
        with contextlib.ExitStack() as stack:
            for name, value in dict(patches, get_project_root=lambda: root).items():
                stack.enter_context(patch.object(scl, name, value))
            stack.enter_context(patch.object(scl, "_STEP_MODULES", step_modules))
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(dict(PAYLOAD, source=source)))))
            try:
                scl.main()
            except SystemExit as exc:
                exit_codes.append(exc.code)
    assert exit_codes == [0], repr(exit_codes)
    return stdout.getvalue()


def write_memory(root, text):
    (root / ".claude" / "auto-memory").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "auto-memory" / "MEMORY.md").write_text(text, encoding="utf-8")


# --- the generic SessionStart output for a local main checkout ----------------------------------

@pytest.fixture
def out(tmp_path):
    return run_main(tmp_path, MAIN_PATCHES)


def test_a_fresh_startup_carries_no_reload_reminder_the_session_digest_listing_owns_pickup(out):
    assert "<context-reload-reminder>" not in out and "session_digest.py" not in out, out[-600:]


def test_a_seat_that_is_not_ready_keeps_its_full_status(out):
    assert "seat-b: UNAVAILABLE (adapters/b.py not present)" in out, out[:800]


def test_no_seat_problem_prints_no_seat_line(tmp_path):
    quiet = run_main(tmp_path, dict(MAIN_PATCHES, sidecar_health=lambda _root: {}))
    assert "seat-" not in quiet, quiet[:600]


def test_no_pointer_prints_the_session_id_and_names_no_state_file(out):
    assert "Session id: main-proof" in out and "State file:" not in out, out[:600]


def test_a_pointer_prints_the_state_file_path_beside_the_session_id(tmp_path):
    pointer = tmp_path / ".claude" / "scratch" / "active_state_main-proof"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(".claude/work/slug.md", encoding="utf-8")
    pointed = run_main(tmp_path, MAIN_PATCHES)
    assert "State file: .claude/work/slug.md" in pointed and "Session id: main-proof" in pointed, pointed[:600]


def test_a_local_session_gets_no_cloud_contract(out):
    assert "<cloud-session>" not in out


def test_a_fresh_startup_repeats_no_branch_or_commit_block_but_keeps_the_upstream_drift(out):
    assert ("Git: main" not in out and "Recent commits:" not in out
            and "24 ahead, 3 behind" in out and "30h" in out), out[-900:]


def test_a_fresh_startup_in_the_main_checkout_prints_no_worktree_line(out):
    assert "Worktree:" not in out, out[:300]


def test_a_healthy_item_is_silent_in_the_env_line(out):
    assert "rosters OK" not in out and "submodule OK" not in out, out[:600]


def test_compact_prints_the_branch_drift_and_commit_block(tmp_path):
    compact_out = run_main(tmp_path, MAIN_PATCHES, source="compact")
    assert ("Git: main | clean" in compact_out and "24 ahead, 3 behind" in compact_out
            and "Recent commits:" in compact_out), compact_out[-900:]


def test_compact_folds_the_reload_reminder_into_one_compaction_line(tmp_path):
    compact_out = run_main(tmp_path, MAIN_PATCHES, source="compact")
    assert ("<context-reload-reminder>" not in compact_out
            and sum(1 for ln in compact_out.splitlines() if ln.startswith("[compaction]")) == 1), compact_out[-500:]


@pytest.mark.parametrize("source", ["clear", "resume"])
def test_clear_and_resume_print_the_branch_and_commit_block(tmp_path, source):
    quiet_out = run_main(tmp_path, MAIN_PATCHES, source=source)
    assert "Git: main" in quiet_out and "Recent commits:" in quiet_out, quiet_out[-600:]


def test_a_fresh_startup_with_no_drift_prints_no_git_line(tmp_path):
    clean_drift = run_main(tmp_path, dict(MAIN_PATCHES, get_upstream_drift=lambda: (0, 0, 0.0)))
    assert "ahead" not in clean_drift and "Git:" not in clean_drift, clean_drift[:400]


def test_a_linked_worktree_still_reports_itself(tmp_path):
    worktree_out = run_main(tmp_path, dict(MAIN_PATCHES, is_worktree=lambda: True))
    assert "Worktree: YES" in worktree_out, worktree_out[:300]


def test_no_step_module_installed_means_no_engine_jmodot_or_language_server_lines(out):
    assert ("GODOT_BIN" not in out and "Jmodot" not in out and "lsp-early-load" not in out
            and "Session step modules" not in out), out[-700:]


def test_a_project_with_no_document_roots_gets_no_locations_line(out):
    assert "[locations]" not in out


@pytest.mark.parametrize("source, sidecar", ROOTS_CASES, ids=["startup", "compact", "startup-sidecar"])
def test_the_projects_document_roots_print_as_one_locations_line(tmp_path, source, sidecar):
    (tmp_path / ".claude" / "skills" / "project_subsystems").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "project_subsystems" / "adaptation.json").write_text(
        json.dumps({"doc_roots": ["D:/Vault/DOCROOT-MARKER"]}), encoding="utf-8")
    roots_out = run_main(tmp_path, MAIN_PATCHES, source=source, sidecar=sidecar)
    assert roots_out.count("[locations]") == 1 and roots_out.count("D:/Vault/DOCROOT-MARKER") == 1, roots_out[-900:]


def test_document_roots_sharing_a_prefix_print_it_once():
    shared = scl.locations_line_from(["C:\\Vault\\Dev\\Alpha", "C:\\Vault\\Dev\\Beta", "C:\\Vault\\Dev\\Gamma"])
    assert shared.count("C:\\Vault\\Dev") == 1 and all(n in shared for n in ("Alpha", "Beta", "Gamma")), shared


def test_roots_with_no_shared_directory_print_in_full():
    apart = scl.locations_line_from(["C:/a/x", "D:/b/y"])
    assert "C:/a/x" in apart and "D:/b/y" in apart, apart


def test_a_prefix_ends_at_a_directory_boundary_never_mid_name():
    near = scl.locations_line_from(["C:/Vault/Dev1", "C:/Vault/Dev2"])
    assert "Dev1" in near and "Dev2" in near and "C:/Vault" in near and "C:/Vault/Dev:" not in near, near


# --- the hook-text cap ----------------------------------------------------------------------------

def test_an_output_within_the_cap_prints_whole_and_writes_no_overflow_file(tmp_path):
    small_out = run_main(tmp_path, MAIN_PATCHES, source="compact")
    assert "session_context_full" not in small_out and not (tmp_path / ".claude" / "scratch").exists(), small_out[-300:]


@pytest.fixture
def oversize(tmp_path):
    """A compact run whose planted step env lines overflow the client's hook-text cap."""
    planted_lines = ["  PLANTED-ENV-%03d %s" % (i, "x" * 60) for i in range(400)]
    big_step = types.SimpleNamespace(env_lines=lambda root, cloud: planted_lines,
                                     report_lines=lambda root: ["", "STEP-REPORT"])
    text = run_main(tmp_path, MAIN_PATCHES, source="compact", step_modules=(big_step,))
    full_path = next((ln.split("Read ", 1)[1].split(" ", 1)[0]
                      for ln in text.splitlines() if "Read " in ln and "session-context" in ln), "")
    full_text = Path(full_path).read_text(encoding="utf-8") if full_path and os.path.isfile(full_path) else ""
    return {"text": text, "bytes": len(text.encode("utf-8")), "full_path": full_path, "full_text": full_text}


def test_an_oversize_loader_output_stays_within_the_clients_hook_text_cap(oversize):
    assert oversize["bytes"] <= HOOK_TEXT_CAP, "%d bytes" % oversize["bytes"]


def test_an_oversize_loader_output_keeps_its_opening_whole_lines_and_its_compaction_line(oversize):
    big_out = oversize["text"]
    assert (big_out.startswith("<session-context>") and "PLANTED-ENV-000 " in big_out
            and "[compaction]" in big_out and all(ln.endswith("x" * 60) for ln in big_out.splitlines()
                                                  if ln.startswith("  PLANTED-ENV-"))), big_out[-500:]


def test_an_oversize_loader_output_names_the_file_that_holds_all_of_it_absolute(oversize):
    full_path, full_text = oversize["full_path"], oversize["full_text"]
    assert (os.path.isabs(full_path) and "PLANTED-ENV-399 " in full_text and "STEP-REPORT" in full_text
            and "24 ahead" in full_text), (full_path, oversize["text"][-400:])


# --- cloud sessions: the memory index and the reload contract ----------------------------------

@pytest.fixture
def cloud(tmp_path):
    """A cloud VM run with a marker memory index and a sidecar probe that records each call."""
    write_memory(tmp_path, "# Memory Index\n- MEMORY-MARKER\n")
    probes = []
    patches = dict(MAIN_PATCHES, is_cloud=lambda: True,
                   sidecar_health=lambda _root: probes.append(1) or {"sidecar-x": "UNAVAILABLE"})
    text = run_main(tmp_path, patches, source="compact")
    return {"text": text, "probes": probes}


def test_a_cloud_session_gets_the_memory_index(cloud):
    assert "MEMORY-MARKER" in cloud["text"]


def test_a_cloud_session_probes_no_sidecar_and_prints_no_status_line_for_one(cloud):
    assert not cloud["probes"] and "sidecar-x" not in cloud["text"], (cloud["probes"], cloud["text"][:600])


def test_a_cloud_session_is_not_told_to_load_an_unprovisioned_plugin_tool(cloud):
    assert "select:mcp__plugin_semantic-search" not in cloud["text"]


def test_the_cloud_reload_reminder_names_the_grep_fallback_for_memory_search(cloud):
    assert "Grep over .claude/auto-memory/" in cloud["text"]


def test_an_oversize_cloud_output_cuts_the_bulk_memory_index_before_the_closing_lines_within_the_cap(tmp_path):
    write_memory(tmp_path, "# Memory Index\n" + "".join("- MEMORY-ROW-%04d %s\n" % (i, "y" * 60) for i in range(300)))
    report_step = types.SimpleNamespace(report_lines=lambda root: ["", "STEP-REPORT"])
    cloud_big = run_main(tmp_path, dict(MAIN_PATCHES, is_cloud=lambda: True), source="compact",
                         step_modules=(report_step,))
    assert (len(cloud_big.encode("utf-8")) <= HOOK_TEXT_CAP and "[compaction]" in cloud_big
            and "STEP-REPORT" in cloud_big), "%d bytes; %s" % (len(cloud_big.encode("utf-8")), cloud_big[-400:])


def test_an_undecodable_memory_index_does_not_raise(tmp_path):
    (tmp_path / ".claude" / "auto-memory").mkdir(parents=True)
    (tmp_path / ".claude" / "auto-memory" / "MEMORY.md").write_bytes(b"# idx\n\xff\xfe bad\n")
    blocks = "\n".join(scl.cloud_session_blocks(tmp_path))
    assert "<auto-memory-index" in blocks, blocks


# --- the step-module seam -----------------------------------------------------------------------

def make_step(order):
    """A planted step module that records each seam it is called on, in order."""
    return types.SimpleNamespace(
        early_setup=lambda root, cloud: order.append("early_setup") or {"step_early": "OK"},
        setup=lambda root, source, worktree, cloud, ready: order.append("setup") or {"step_setup": "FIXED (x)"},
        local_checks=lambda root: order.append("local_checks") or {"step_local": "OK"},
        env_lines=lambda root, cloud: ["  STEP-ENV-LINE"],
        context_lines=lambda root, recent: ["", "STEP-CONTEXT"],
        nudge_lines=lambda results, cloud: ["", "STEP-NUDGE:" + results.get("step_local", "?")],
        report_lines=lambda root: ["", "STEP-REPORT"],
        persist_env=lambda root, cloud: order.append("persist_env"),
    )


@pytest.fixture
def stepped(tmp_path):
    order = []
    output = run_main(tmp_path, MAIN_PATCHES, step_modules=(make_step(order),))
    return {"order": order, "output": output}


def test_a_step_modules_setup_statuses_reach_the_env_line_in_session_order(stepped):
    output = stepped["output"]
    assert ("step_early" not in output and "step_setup: FIXED (x)" in output and "step_local" not in output
            and stepped["order"] == ["early_setup", "setup", "local_checks", "persist_env"]), (stepped["order"], output[:600])


def test_a_step_modules_lines_reach_the_output(stepped):
    assert all(text in stepped["output"] for text in ("STEP-ENV-LINE", "STEP-CONTEXT", "STEP-NUDGE:OK", "STEP-REPORT")), \
        stepped["output"][-600:]


def test_a_fixed_step_status_is_summarised_as_auto_fixed(stepped):
    assert "Auto-fixed the items marked FIXED" in stepped["output"]


def test_local_only_checks_do_not_run_on_cloud(tmp_path):
    cloud_order = []
    step = make_step([])
    step.local_checks = lambda root: cloud_order.append("local_checks") or {}
    cloud_patches = dict(MAIN_PATCHES, is_cloud=lambda: True)
    run_main(tmp_path, cloud_patches, step_modules=(step,))
    assert cloud_order == [], cloud_order


def test_a_raising_hook_is_reported_and_the_session_context_still_prints(tmp_path):
    def broken(*_args):
        raise ValueError("hook bug")
    step = make_step([])
    step.setup = broken
    output = run_main(tmp_path, MAIN_PATCHES, step_modules=(step,))
    assert ("Session step modules: FAILED (setup ValueError: hook bug)" in output
            and "24 ahead" in output and "STEP-REPORT" in output), output[-500:]


def test_an_import_fault_is_recorded_by_the_module_fault_hook_and_siblings_still_load():
    assert scl._step_fault("mod", ImportError("boom")) is None and "mod import ImportError: boom" in scl._STEP_FAULTS


# --- loader structure ------------------------------------------------------------------------------

def test_the_loader_carries_no_runsettings_entry_point():
    assert not hasattr(scl, "runsettings_cli")


def test_the_loader_carries_no_project_literal():
    src = Path(scl.__file__).read_text(encoding="utf-8")
    assert not any(x in src for x in ("237/wk", "-06:00", "WORKLOG_INLINE", "sidecar-"))


def test_a_root_with_no_seats_json_reports_no_seat_problem(tmp_path):
    (tmp_path / ".claude").mkdir()
    assert scl.sidecar_health(tmp_path) == {}


# --- plain projects and sidecar children ------------------------------------------------------------

@pytest.mark.parametrize("is_cloud_session", [False, True], ids=["local", "cloud"])
def test_a_plain_session_has_no_engine_or_language_server_lines(tmp_path, is_cloud_session):
    plain_out = run_main(tmp_path, dict(MAIN_PATCHES, is_cloud=lambda: is_cloud_session,
                                        is_worktree=lambda: True), source="compact")
    assert "GODOT_BIN" not in plain_out and "Jmodot" not in plain_out and "lsp-early-load" not in plain_out, plain_out[-700:]


@pytest.mark.parametrize("is_cloud_session", [False, True], ids=["local", "cloud"])
def test_a_plain_session_still_gets_the_git_context(tmp_path, is_cloud_session):
    plain_out = run_main(tmp_path, dict(MAIN_PATCHES, is_cloud=lambda: is_cloud_session,
                                        is_worktree=lambda: True), source="compact")
    assert "Git: main |" in plain_out and "Recent commits:" in plain_out, plain_out[-700:]


@pytest.mark.parametrize("sidecar_source", ["startup", "compact"])
def test_a_sidecar_child_gets_no_worktree_env_git_or_commit_block(tmp_path, sidecar_source):
    delegate_out = run_main(tmp_path, MAIN_PATCHES, source=sidecar_source, sidecar=True)
    assert not any(marker in delegate_out for marker in ("Worktree:", "Env: ", "Git: main |", "Recent commits:")), \
        delegate_out[-700:]


@pytest.mark.parametrize("sidecar_source", ["startup", "compact"])
def test_a_sidecar_child_still_prints_the_session_context_envelope(tmp_path, sidecar_source):
    delegate_out = run_main(tmp_path, MAIN_PATCHES, source=sidecar_source, sidecar=True)
    assert "<session-context>" in delegate_out and "</session-context>" in delegate_out, delegate_out[-300:]


# --- the local setup stamp reaches the model through the Env: line ------------------------------

def env_line(text):
    return next((line for line in text.splitlines() if line.startswith("Env: ")), "")


@pytest.fixture
def ws_root(tmp_path):
    """The tool copied into a fixture root, with the command's own frontmatter as the declaration under test.

    This is the hook's real path resolution reading the real declaration, not an import of the repo's tool.
    """
    real_claude = Path(__file__).resolve().parents[1]
    (tmp_path / ".claude" / "tools").mkdir(parents=True)
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    shutil.copyfile(real_claude / "tools" / "workstation_profile.py",
                    tmp_path / ".claude" / "tools" / "workstation_profile.py")
    (tmp_path / ".claude" / "commands" / "workstation_setup.md").write_text(
        "---\ndescription: planted\nstamp-inputs:\n" + "".join(
            '  - "%s"\n' % rel for rel in (".mcp.json", ".claude/reference/project_stack.md", "global.json",
                                         "nuget.config", ".claude/commands/workstation_setup.md"))
        + "---\n", encoding="utf-8", newline="\n")
    for rel in (".mcp.json", ".claude/reference/project_stack.md", "global.json", "nuget.config"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("planted\n", encoding="utf-8")
    return tmp_path


def run_ws_main(root, profile_path, extra_patches=None):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout), patch.dict(os.environ, {"WORKSTATION_PROFILE_PATH": str(profile_path)}):
        os.environ.pop("CLAUDE_CODE_SIDECAR", None)
        with contextlib.ExitStack() as stack:
            for name, value in dict(MAIN_PATCHES, get_project_root=lambda: root, **(extra_patches or {})).items():
                stack.enter_context(patch.object(scl, name, value))
            stack.enter_context(patch.object(scl, "_STEP_MODULES", ()))
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(PAYLOAD))))
            try:
                scl.main()
            except SystemExit:
                pass
    return stdout.getvalue()


def test_a_checkout_with_no_recorded_stamp_says_unaudited_in_the_env_line(ws_root):
    out = run_ws_main(ws_root, ws_root / "profile.json")
    assert "workstation: UNAUDITED" in env_line(out), env_line(out)


@pytest.fixture
def stamped(ws_root):
    import importlib.util
    profile = ws_root / "profile.json"
    with patch.dict(os.environ, {"WORKSTATION_PROFILE_PATH": str(profile)}):
        spec = importlib.util.spec_from_file_location("fixture_workstation_profile",
                                                      ws_root / ".claude" / "tools" / "workstation_profile.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stamped_result = module.stamp(ws_root, preflight_failures=[])
    assert stamped_result[0], stamped_result
    return ws_root, profile


def test_a_stamped_checkout_does_not_mention_the_workstation_in_the_env_line(stamped):
    root, profile = stamped
    out = run_ws_main(root, profile)
    assert "workstation" not in env_line(out), env_line(out)


def test_a_changed_setup_input_is_named_stale_in_the_env_line(stamped):
    root, profile = stamped
    (root / ".mcp.json").write_text("drifted\n", encoding="utf-8")
    out = run_ws_main(root, profile)
    assert "workstation: STALE" in env_line(out) and ".mcp.json" in env_line(out), env_line(out)


def test_a_tool_that_cannot_be_loaded_omits_the_step_fail_open(stamped):
    def unloadable(_root):
        raise OSError("tool gone")
    root, profile = stamped
    out = run_ws_main(root, profile, {"_load_workstation_profile": unloadable})
    assert "workstation" not in env_line(out), env_line(out)


# --- the drift reader against a planted clone ----------------------------------------------------

def test_the_drift_reader_counts_unpushed_commits_against_the_upstream(tmp_path, monkeypatch):
    """Spawned: real git. Two local commits on top of the clone's upstream."""
    def g(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    origin, clone = str(tmp_path / "origin.git"), str(tmp_path / "clone")
    g("init", "-q", "--bare", "-b", "main", origin, cwd=str(tmp_path))
    g("clone", "-q", origin, clone, cwd=str(tmp_path))
    for n in range(3):
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c%d" % n, cwd=clone)
        if n == 0:
            g("push", "-q", "origin", "HEAD:main", cwd=clone)
            g("branch", "-q", "--set-upstream-to=origin/main", cwd=clone)
    monkeypatch.chdir(clone)
    drift = scl.get_upstream_drift()
    assert drift is not None and drift[:2] == (2, 0) and 0 <= drift[2] < 1, repr(drift)


# --- handle hoarders ----------------------------------------------------------------------------

HOARD_ROWS = [(20296, "SystemSettings.exe", 3_563_258), (10028, "ServiceShell.exe", 45_661), (4, "System", 9_000)]


def test_a_hoarder_above_the_floor_is_named_with_its_pid_and_count_and_the_small_ones_are_not():
    status = scl.handle_hoarders(HOARD_ROWS)
    assert "SystemSettings.exe pid 20296" in status and "3,563,258" in status and "ServiceShell" not in status, status


def test_no_process_above_the_floor_gives_no_status():
    assert scl.handle_hoarders(HOARD_ROWS[1:]) == "", scl.handle_hoarders(HOARD_ROWS[1:])


def test_an_unreadable_process_table_reads_unknown_never_healthy():
    assert scl.handle_hoarders(None).startswith("UNKNOWN"), scl.handle_hoarders(None)


def test_no_process_table_to_read_off_windows_gives_no_status():
    assert scl.handle_hoarders([]) == ""


@pytest.mark.skipif(os.name != "nt", reason="reads the live Windows process table")
def test_the_live_process_table_lists_this_process_with_a_handle_count():
    live = scl.process_handle_counts()
    assert any(pid == os.getpid() and count > 0 for pid, _name, count in live or ()), repr((live or [])[:3])
