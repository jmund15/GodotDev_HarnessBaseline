#!/usr/bin/env python3
"""Proof: a relative --out-dir or promptFile resolves against the CALLER's cwd before any child sees it.

The children run with cwd=REPO, so a relative -R/-P path handed to them named a directory that did not exist
there; the launcher's noclobber .pid claim then failed and reported "record path already used" (2026-10-01,
every job of a fan-out refused). --dry-run prints each child's argv and spends nothing.
Run: python3 .claude/tests/test_sidecar_fanout_outdir.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "sidecar_fanout.py"
FAILS = []


def check(ok, label):
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        FAILS.append(label)


def cross_transport_alias():
    """The first architect-tier alias the pick facts list as a sidecar hop: read live from the registry's own
    dict report (`route_rows`, the data behind `for-role`), so no roster name or table layout is pinned here."""
    sys.path[:0] = [str(TOOL.parent), str(TOOL.parents[1] / "hooks")]
    import model_registry
    import _session_transport
    rows = model_registry.route_rows("architect", _session_transport.session_plan())["crossTransport"]
    return rows[0]["alias"] if rows else None


def detach_cases(cwd):
    """--detach validates in the caller, then hands the run to a detached copy of itself and returns, so a
    capped or killed caller shell (the Bash tool's background limit) stops neither queued jobs nor the
    `<label>.md` writes. Popen and run_jobs are planted: nothing launches."""
    import importlib.util
    import io
    import contextlib
    spec = importlib.util.spec_from_file_location("sf_selfdetach", TOOL)
    sf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sf)
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append((list(argv), kw))
            self.pid = 4242

        def poll(self):
            return 0   # the planted driver is already gone, so await_admission never waits

    def no_run(*_a, **_k):
        raise AssertionError("run_jobs ran in the caller")
    sf.subprocess.Popen, sf.run_jobs = FakePopen, no_run
    sf.duplicate_launches = lambda planned: ""
    out = io.StringIO()
    old = os.getcwd()
    # The dispatch-doctrine gate in a --detach call is not under test here (its own proof is
    # test_sidecar_fanout_detach.py): mark the doctrine loaded in a throwaway session state.
    import _hook_state
    sid, state_dir = "outdir_detach", tempfile.mkdtemp(prefix="outdir_state_")
    saved = {k: os.environ.get(k) for k in ("HARNESS_HOOK_STATE_DIR", "CLAUDE_CODE_SESSION_ID",
                                            "SIDECAR_DISPATCH_OWNER", "PROVIDER_CAPACITY_CACHE")}
    os.environ["HARNESS_HOOK_STATE_DIR"], os.environ["CLAUDE_CODE_SESSION_ID"] = state_dir, sid
    # A checkout with no fresh capacity cache would run the live balance probe through the planted Popen.
    import _transport_fixture
    os.environ["PROVIDER_CAPACITY_CACHE"] = _transport_fixture._available_capacity_cache()
    os.environ.pop("SIDECAR_DISPATCH_OWNER", None)
    _hook_state.mark_skill_in_context(sid, "orchestration")
    _hook_state.update_json_locked(_hook_state.state_path(sid),
                                   lambda state: (state.__setitem__("model_ladder_ready", True), True)[1])
    os.chdir(str(cwd))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = sf.main(["jobs.json", "--out-dir", "det/out", "--max-parallel", "2", "--detach"])
            bad_rc = sf.main(["jobs.json", "--out-dir", "det/out", "--max-parallel", "0", "--detach"])
    except AssertionError as err:
        rc, bad_rc = "raised: %s" % err, None
    finally:
        os.chdir(old)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    text = out.getvalue()
    argv, kw = spawned[0] if spawned else ([], {})
    check(rc == 0 and len(spawned) == 1, "--detach returns 0 after spawning one detached driver (rc=%r) %s"
          % (rc, text[-300:]))
    check(argv[:2] == [sys.executable, str(TOOL)] and "--detach" not in argv and "--max-parallel" in argv
          and "jobs.json" not in argv and any(a.endswith("jobs.json") and os.path.isabs(a) for a in argv),
          "the driver reruns the fan-out without --detach, with an absolute jobs path (%r)" % argv)
    check(bool(kw.get("creationflags") or kw.get("start_new_session")) and kw.get("stdin") == subprocess.DEVNULL,
          "the driver is spawned detached from the caller (%r)" % sorted(kw))
    check("pid 4242" in text and "watch_records.py --session --wake" in text and "fanout-driver.log" in text,
          "the caller prints the driver pid, its log and the watcher command (%s)" % text[-300:])
    check(bad_rc == 2 and len(spawned) == 1, "a bad flag still fails in the caller, before any spawn (rc=%r)" % bad_rc)


def main():
    alias = cross_transport_alias()
    check(bool(alias), "the registry lists a cross-transport architect alias (%r)" % alias)
    if not alias:
        print("\ntest_sidecar_fanout_outdir: %d failed" % len(FAILS))
        return 1
    with tempfile.TemporaryDirectory(prefix="fanout out ") as raw:
        cwd = Path(raw)
        (cwd / "p.md").write_text("lens body\n", encoding="utf-8")
        jobs = {"jobs": [{"label": "j1", "alias": alias, "effort": "high", "kind": "reviewer", "tier": "architect",
                          "promptFile": "p.md"}]}
        (cwd / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        r = subprocess.run([sys.executable, str(TOOL), "jobs.json", "--out-dir", "rel/out", "--dry-run"],
                           cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        check(r.returncode == 0, "dry run exits 0 (rc=%r) %s" % (r.returncode, out[-300:] if r.returncode else ""))
        def has(flag, path):
            return re.search(r" %s %s(?=\s|$)" % (flag, re.escape(path.resolve().as_posix())), out, re.I) is not None
        for flag, expected in (
            ("-R", cwd / "rel" / "out" / "j1.record.json"),
            ("-P", cwd / "rel" / "out" / "j1.stream"),
            ("-f", cwd / "p.md"),
        ):
            check(has(flag, expected), "%s is the caller's absolute path (%s)" % (flag, expected.resolve().as_posix()))
        check(not has("-R", cwd / "rel" / "output" / "j1.record.json"),
              "a sibling directory (rel/output) does not satisfy the -R check")
        detach_cases(cwd)
    print("\ntest_sidecar_fanout_outdir: %d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
