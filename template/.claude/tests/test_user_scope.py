"""Proof for .claude/tools/user_scope.py: every case runs against a temp home and a planted scope dir.

    python3 .claude/tests/test_user_scope.py

Prints one `ok` or `FAIL` line per case and exits 1 when any case fails.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import _scan_fixtures as fx

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / ".claude" / "tools" / "user_scope.py"
REAL_SCOPE = REPO / ".claude" / "user_scope"
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def tool():
    spec = importlib.util.spec_from_file_location("user_scope", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Sandbox:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="usp-test-"))
        self.home = self.root / fx.HOME_USER
        self.scope = self.root / "scope"
        self.repo = self.root / "repo"
        self.home.mkdir()
        self.scope.mkdir()
        self.repo.mkdir()

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def template(self, src, data):
        path = self.scope / src
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def manifest(self, items, name="manifest.json"):
        (self.scope / name).write_text(json.dumps({"version": 1, "items": items}), encoding="utf-8")

    def run(self, command, *args, scope=None):
        env = {key: value for key, value in os.environ.items() if key != "PYTHONUTF8"}
        argv = [sys.executable, str(TOOL), command, "--home", str(self.home),
                "--scope-dir", str(scope or self.scope), "--repo", str(self.repo), *args]
        done = subprocess.run(argv, capture_output=True, env=env, timeout=120)
        return Run(done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"))

    def rows(self):
        done = self.run("check", "--json")
        return {row["id"]: row for row in json.loads(done.out)["items"]}

    def statuses(self):
        return {item_id: row["status"] for item_id, row in self.rows().items()}


class Run:
    def __init__(self, code, out, err):
        self.code = code
        self.out = out
        self.err = err


def file_item(item_id, src, dest, **extra):
    return {"id": item_id, "kind": "file", "src": src, "dest": dest, **extra}


def default_sandbox():
    sb = Sandbox()
    sb.template("a.txt", b"alpha\n")
    sb.template("sub/b.txt", b"beta\n")
    sb.manifest([file_item("tool-a", "a.txt", "~/.claude/a.txt"),
                 file_item("tool-b", "sub/b.txt", "~/.claude/hooks/b.txt")])
    return sb


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_manifest_error(sb, must_name, command="check"):
    done = sb.run(command)
    expect(done.code == 2, f"exit {done.code}, want 2; stderr: {done.err!r}")
    expect(must_name in done.err, f"stderr does not name {must_name!r}: {done.err!r}")


# --------------------------------------------------------------------------- S1: file items, manifest, check/install

@case
def s1_empty_home_every_file_item_missing():
    sb = default_sandbox()
    try:
        done = sb.run("check", "--json")
        expect(done.code == 1, f"exit {done.code}, want 1")
        expect(sb.statuses() == {"tool-a": "missing", "tool-b": "missing"}, f"statuses {sb.statuses()}")
    finally:
        sb.close()


@case
def s1_install_then_check_exits_zero():
    sb = default_sandbox()
    try:
        expect(sb.run("install").code == 0, "install did not exit 0")
        expect(sb.run("check").code == 0, "check after install did not exit 0")
        expect((sb.home / ".claude" / "hooks" / "b.txt").read_bytes() == b"beta\n", "b.txt bytes differ")
    finally:
        sb.close()


@case
def s1_drift_kept_byte_exact_and_exits_zero():
    sb = default_sandbox()
    try:
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes(b"owner edit\n")
        expect(sb.run("install").code == 0, "install did not exit 0")
        expect((sb.home / ".claude" / "a.txt").read_bytes() == b"owner edit\n", "install overwrote a drifted file")
        done = sb.run("check")
        expect(done.code == 0, f"check exit {done.code}, want 0 for drift")
        expect(sb.statuses()["tool-a"] == "drift", f"status {sb.statuses()}")
    finally:
        sb.close()


@case
def s1_second_install_changes_no_mtime():
    sb = default_sandbox()
    try:
        expect(sb.run("install").code == 0, "first install did not exit 0")
        targets = [sb.home / ".claude" / "a.txt", sb.home / ".claude" / "hooks" / "b.txt"]
        for path in targets:
            os.utime(path, (1_000_000_000, 1_000_000_000))
        expect(sb.run("install").code == 0, "second install did not exit 0")
        for path in targets:
            expect(path.stat().st_mtime_ns == 1_000_000_000 * 10**9, f"second install rewrote {path}")
    finally:
        sb.close()


@case
def s1_drift_row_prints_adopt_and_copy_commands():
    sb = default_sandbox()
    try:
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes(b"owner edit\n")
        done = sb.run("check")
        expect("adopt tool-a" in done.out, f"no adopt command in: {done.out!r}")
        expect((sb.scope / "a.txt").as_posix() in done.out, f"no template path in: {done.out!r}")
        expect((sb.home / ".claude" / "a.txt").as_posix() in done.out, f"no home path in: {done.out!r}")
    finally:
        sb.close()


@case
def s1_manifest_unknown_kind_exits_2():
    sb = Sandbox()
    try:
        sb.manifest([{"id": "k1", "kind": "bogus"}])
        expect_manifest_error(sb, "k1")
    finally:
        sb.close()


@case
def s1_manifest_misspelled_field_exits_2():
    sb = default_sandbox()
    try:
        sb.manifest([file_item("f1", "a.txt", "~/.claude/a.txt", platfrom="win32")])
        expect_manifest_error(sb, "f1")
        expect_manifest_error(sb, "platfrom")
    finally:
        sb.close()


@case
def s1_manifest_duplicate_id_across_files_exits_2():
    sb = default_sandbox()
    try:
        sb.manifest([file_item("dup", "a.txt", "~/.claude/a.txt")])
        sb.manifest([file_item("dup", "a.txt", "~/.claude/a2.txt")], name="manifest.local.json")
        expect_manifest_error(sb, "dup")
    finally:
        sb.close()


@case
def s1_manifest_src_named_claude_md_exits_2():
    sb = Sandbox()
    try:
        sb.template("CLAUDE.md", b"nested memory\n")
        sb.manifest([file_item("memo", "CLAUDE.md", "~/.claude/CLAUDE.md")])
        expect_manifest_error(sb, "memo")
    finally:
        sb.close()


@case
def s1_manifest_dest_escaping_home_exits_2():
    sb = default_sandbox()
    try:
        sb.manifest([file_item("esc", "a.txt", "~/../escape.txt")])
        expect_manifest_error(sb, "esc")
    finally:
        sb.close()


@case
def s1_manifest_missing_src_exits_2():
    sb = default_sandbox()
    try:
        sb.manifest([{"id": "nosrc-item", "kind": "file", "dest": "~/.claude/a.txt"}])
        expect_manifest_error(sb, "nosrc-item")
    finally:
        sb.close()


@case
def s1_manifest_src_absent_on_disk_exits_2():
    sb = default_sandbox()
    try:
        sb.manifest([file_item("ghost", "nope.txt", "~/.claude/a.txt")])
        expect_manifest_error(sb, "ghost")
    finally:
        sb.close()


@case
def s1_malformed_manifest_json_exits_2_and_writes_nothing():
    sb = default_sandbox()
    try:
        (sb.scope / "manifest.json").write_text("{bad", encoding="utf-8")
        expect_manifest_error(sb, "manifest.json", command="install")
        expect(list(sb.home.iterdir()) == [], f"install wrote into home: {list(sb.home.iterdir())}")
    finally:
        sb.close()


@case
def s1_crlf_home_file_equals_lf_template():
    sb = default_sandbox()
    try:
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes(b"alpha\r\n")
        expect(sb.statuses()["tool-a"] == "same", f"status {sb.statuses()}")
    finally:
        sb.close()


@case
def s1_non_ascii_template_and_home_compare_equal():
    sb = Sandbox()
    try:
        sb.template("a.txt", "x — y\n".encode("utf-8"))
        sb.manifest([file_item("tool-a", "a.txt", "~/.claude/a.txt")])
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes("x — y\r\n".encode("utf-8"))
        done = sb.run("check", "--json")
        expect(done.code == 0, f"exit {done.code} stderr {done.err!r}")
        expect({row["id"]: row["status"] for row in json.loads(done.out)["items"]} == {"tool-a": "same"},
               f"output {done.out!r}")
    finally:
        sb.close()


@case
def s1_platform_mismatch_is_skipped_and_writes_nothing():
    sb = Sandbox()
    try:
        sb.template("a.txt", b"alpha\n")
        sb.manifest([file_item("other", "a.txt", "~/.claude/a.txt", platform="plan9")])
        expect(sb.run("check").code == 0, "skipped-platform item did not exit 0")
        expect(sb.statuses() == {"other": "skipped-platform"}, f"statuses {sb.statuses()}")
        expect(sb.run("install").code == 0, "install did not exit 0")
        expect(not (sb.home / ".claude" / "a.txt").exists(), "install wrote a skipped-platform item")
    finally:
        sb.close()


@case
def s1_check_json_lists_every_item_with_id_kind_status():
    sb = default_sandbox()
    try:
        rows = json.loads(sb.run("check", "--json").out)["items"]
        expect([row["id"] for row in rows] == ["tool-a", "tool-b"], f"rows {rows}")
        for row in rows:
            expect(row.get("kind") == "file" and "status" in row, f"row shape {row}")
    finally:
        sb.close()


# --------------------------------------------------------------------------- S2: register settings.json and hooks

def registered_sandbox():
    sb = Sandbox()
    sb.template("sl_tmpl.py", b"print('status')\n")
    sb.template("hooks/h.py", b"print('hook')\n")
    sb.manifest([
        file_item("statusline", "sl_tmpl.py", "~/.claude/statusline.py",
                  register={"setting": "statusLine",
                            "value": {"type": "command", "command": "python {dest}", "refreshInterval": 30}}),
        file_item("hook-a", "hooks/h.py", "~/.claude/hooks/h.py",
                  register={"hook": {"event": "SessionStart", "command": "python {dest}"}}),
    ])
    return sb


def posix(path):
    return Path(path).as_posix()


def settings_path(sb):
    return sb.home / ".claude" / "settings.json"


def write_settings(sb, data):
    settings_path(sb).parent.mkdir(parents=True, exist_ok=True)
    settings_path(sb).write_bytes(data)


def read_settings(sb):
    return json.loads(settings_path(sb).read_text(encoding="utf-8"))


def session_commands(settings):
    return [hook["command"] for group in settings.get("hooks", {}).get("SessionStart", [])
            for hook in group.get("hooks", [])]


@case
def s2_register_on_empty_home_writes_only_registered_keys():
    sb = registered_sandbox()
    try:
        expect(sb.run("check").code == 1, "check on an empty home is not red")
        expect(sb.run("install").code == 0, "install did not exit 0")
        settings = read_settings(sb)
        expect(set(settings) == {"statusLine", "hooks"}, f"keys {sorted(settings)}")
        expect(settings["statusLine"] == {"type": "command",
                                          "command": f"python {posix(sb.home / '.claude' / 'statusline.py')}",
                                          "refreshInterval": 30}, f"statusLine {settings['statusLine']}")
        expect(session_commands(settings) == [f"python {posix(sb.home / '.claude' / 'hooks' / 'h.py')}"],
               f"SessionStart {session_commands(settings)}")
        expect(sb.run("check").code == 0, "check after install is not green")
        expect({k: (r["status"], r["register"]) for k, r in sb.rows().items()}
               == {"statusline": ("same", "registered"), "hook-a": ("same", "registered")},
               f"rows {sb.rows()}")
    finally:
        sb.close()


@case
def s2_install_output_names_each_action():
    sb = registered_sandbox()
    try:
        out = sb.run("install").out.splitlines()
        expect(out == ["copied statusline", "copied hook-a", "registered statusline", "registered hook-a"],
               f"install lines {out}")
    finally:
        sb.close()


@case
def s2_file_present_but_unregistered_is_red():
    sb = registered_sandbox()
    try:
        (sb.home / ".claude" / "hooks").mkdir(parents=True)
        (sb.home / ".claude" / "hooks" / "h.py").write_bytes(b"print('hook')\n")
        expect(sb.run("check").code == 1, "an unregistered hook is not red")
        expect(sb.rows()["hook-a"]["register"] == "unregistered", f"rows {sb.rows()}")
    finally:
        sb.close()


@case
def s2_unrelated_keys_survive_and_identical_hook_is_not_doubled():
    sb = registered_sandbox()
    try:
        hook = posix(sb.home / ".claude" / "hooks" / "h.py")
        before = {"theme": "dark", "permissions": {"allow": ["Bash(ls:*)"]},
                  "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"python {hook}"}]}]}}
        write_settings(sb, json.dumps(before, indent=4).encode("utf-8"))
        expect(sb.run("install").code == 0, "install did not exit 0")
        after = read_settings(sb)
        expect(after["theme"] == "dark" and after["permissions"] == before["permissions"],
               "an unrelated key changed")
        expect("statusLine" in after, "statusLine was not registered")
        expect(session_commands(after) == [f"python {hook}"], f"SessionStart {session_commands(after)}")
        expect(sb.run("install").code == 0, "second install did not exit 0")
        expect(session_commands(read_settings(sb)) == [f"python {hook}"], "second install doubled the hook")
    finally:
        sb.close()


@case
def s2_existing_statusline_is_kept_and_reported_register_differs():
    sb = registered_sandbox()
    try:
        other = {"type": "command", "command": "python other.py", "padding": 2}
        write_settings(sb, json.dumps({"statusLine": other}).encode("utf-8"))
        expect(sb.run("install").code == 0, "install did not exit 0")
        expect(read_settings(sb)["statusLine"] == other, "install replaced an existing statusLine")
        done = sb.run("check", "--json")
        expect(done.code == 0, f"check exit {done.code}, want 0 for register-differs")
        expect(sb.rows()["statusline"]["register"] == "register-differs", f"rows {sb.rows()}")
    finally:
        sb.close()


@case
def s2_backup_holds_original_bytes_and_is_written_once():
    sb = registered_sandbox()
    try:
        original = b'{"theme": "dark"}\n'
        write_settings(sb, original)
        expect(sb.run("install").code == 0, "first install did not exit 0")
        backup = sb.home / ".claude" / "settings.json.pre-user-scope"
        expect(backup.read_bytes() == original, "backup is not the original bytes")
        os.utime(backup, (1_000_000_000, 1_000_000_000))
        settings = read_settings(sb)
        del settings["statusLine"]
        write_settings(sb, json.dumps(settings).encode("utf-8"))
        expect(sb.run("install").code == 0, "second install did not exit 0")
        expect("statusLine" in read_settings(sb), "second install did not register statusLine again")
        expect(backup.read_bytes() == original, "second install rewrote the backup")
        expect(backup.stat().st_mtime_ns == 1_000_000_000 * 10**9, "second install touched the backup")
    finally:
        sb.close()


@case
def s2_malformed_settings_exits_2_and_writes_nothing():
    sb = registered_sandbox()
    try:
        write_settings(sb, b"{bad")
        done = sb.run("install")
        expect(done.code == 2, f"exit {done.code}, want 2")
        expect("settings.json" in done.err, f"stderr {done.err!r}")
        expect(settings_path(sb).read_bytes() == b"{bad", "malformed settings.json was changed")
        names = sorted(p.name for p in (sb.home / ".claude").iterdir())
        expect(names == ["settings.json"], f"install wrote files: {names}")
    finally:
        sb.close()


@case
def s2_failed_settings_write_leaves_original_and_no_tmp():
    sb = registered_sandbox()
    tool_module = tool()
    real_replace = tool_module._replace

    def failing_replace(src, dst):
        if Path(dst).name == "settings.json":
            raise OSError("injected failure between the temp write and the replace")
        return real_replace(src, dst)

    try:
        original = b'{"theme": "dark"}\n'
        write_settings(sb, original)
        tool_module._replace = failing_replace
        with contextlib.redirect_stderr(io.StringIO()):
            code = tool_module.main(["install", "--home", str(sb.home), "--scope-dir", str(sb.scope)])
        expect(code == 2, f"exit {code}, want 2")
        expect(settings_path(sb).read_bytes() == original, "a failed write changed settings.json")
        expect(not (sb.home / ".claude" / "settings.json.tmp").exists(), "temp file left behind")
    finally:
        tool_module._replace = real_replace
        sb.close()


@case
def s2_manifest_register_with_bad_shape_exits_2():
    sb = Sandbox()
    try:
        sb.template("a.txt", b"alpha\n")
        sb.manifest([file_item("reg", "a.txt", "~/.claude/a.txt",
                               register={"setting": "x", "value": {}, "hook": {"event": "E", "command": "c"}})])
        expect_manifest_error(sb, "reg")
        sb.manifest([file_item("reg", "a.txt", "~/.claude/a.txt",
                               register={"hook": {"event": "E", "command": "c", "typo": 1}})])
        expect_manifest_error(sb, "reg")
    finally:
        sb.close()


@case
def s2_matcher_hook_registers_under_its_matcher_group():
    sb = Sandbox()
    try:
        sb.template("hooks/h.py", b"print('hook')\n")
        sb.manifest([file_item("hook-m", "hooks/h.py", "~/.claude/hooks/hm.py",
                               register={"hook": {"event": "PostToolUse", "matcher": "Read",
                                                  "command": "python {dest}"}})])
        expect(sb.run("install").code == 0, "install did not exit 0")
        group = read_settings(sb)["hooks"]["PostToolUse"][0]
        expect(group.get("matcher") == "Read", f"group {group}")
        expect(sb.rows()["hook-m"]["register"] == "registered", f"rows {sb.rows()}")
    finally:
        sb.close()


@case
def s2_settings_write_retries_a_reader_that_holds_the_target():
    sb = registered_sandbox()
    real_replace = os.replace
    held = {"settings": False}

    def flaky_replace(src, dst):
        if Path(dst).name == "settings.json" and not held["settings"]:
            held["settings"] = True
            raise PermissionError(13, "held open by a reader")
        return real_replace(src, dst)

    try:
        with mock.patch("os.replace", side_effect=flaky_replace):
            mod = tool()
            code, _, err = run_main(mod, sb, ["install"])
        expect(held["settings"], "the injected reader hold never fired")
        expect(code == 0, f"install exit {code}: {err}")
        expect(session_commands(read_settings(sb)) == [f"python {posix(sb.home / '.claude' / 'hooks' / 'h.py')}"],
               "settings.json was not registered after the retried write")
    finally:
        sb.close()


@case
def s2_unregistered_rows_name_the_settings_write():
    sb = registered_sandbox()
    try:
        (sb.home / ".claude" / "hooks").mkdir(parents=True)
        (sb.home / ".claude" / "hooks" / "h.py").write_bytes(b"print('hook')\n")
        rows = {row["id"]: row for row in json.loads(sb.run("check", "--json").out)["items"]}
        expect(rows["hook-a"]["detail"] == [f"registers: hook SessionStart -> python {posix(sb.home / '.claude' / 'hooks' / 'h.py')}"],
               f"hook-a detail {rows['hook-a']['detail']}")
        expect(any(line.startswith("registers: settings statusLine = ") for line in rows["statusline"]["detail"]),
               f"statusline detail {rows['statusline']['detail']}")
    finally:
        sb.close()


# --------------------------------------------------------------------------- S3: env, local, adopt, lint

class FakeEnv:
    def __init__(self, value):
        self.value = value
        self.sets = []

    def get(self, name):
        return self.value

    def set(self, name, value):
        self.sets.append((name, value))
        self.value = value


def run_main(mod, sb, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main([*argv, "--home", str(sb.home), "--scope-dir", str(sb.scope)])
    return code, out.getvalue(), err.getvalue()


def env_sandbox(platform="win32"):
    sb = Sandbox()
    sb.manifest([{"id": "bg-shell", "kind": "env", "name": "CLAUDE_TEST_VAR", "value": "1", "platform": platform}])
    return sb


@case
def s3_env_unset_is_set_once_through_the_setter():
    sb = env_sandbox()
    try:
        mod = tool()
        mod.PLATFORM = "win32"
        fake = FakeEnv(None)
        mod.get_user_env, mod.set_user_env = fake.get, fake.set
        code, _, _ = run_main(mod, sb, ["check"])
        expect(code == 1, f"unset env item exit {code}, want 1")
        code, _, err = run_main(mod, sb, ["install"])
        expect(code == 0, f"install exit {code}: {err}")
        expect(fake.sets == [("CLAUDE_TEST_VAR", "1")], f"setter calls {fake.sets}")
        code, out, _ = run_main(mod, sb, ["check", "--json"])
        expect(code == 0 and json.loads(out)["items"][0]["status"] == "set", f"after install: {out!r}")
    finally:
        sb.close()


@case
def s3_env_present_is_not_set_again():
    sb = env_sandbox()
    try:
        mod = tool()
        mod.PLATFORM = "win32"
        fake = FakeEnv("0")
        mod.get_user_env, mod.set_user_env = fake.get, fake.set
        code, _, err = run_main(mod, sb, ["install"])
        expect(code == 0, f"install exit {code}: {err}")
        expect(fake.sets == [] and fake.value == "0", f"present env was set: {fake.sets}")
        code, out, _ = run_main(mod, sb, ["check", "--json"])
        expect(code == 0 and json.loads(out)["items"][0]["status"] == "set", f"present env check: {out!r}")
    finally:
        sb.close()


@case
def s3_env_platform_mismatch_is_skipped():
    sb = env_sandbox(platform="win32")
    try:
        mod = tool()
        mod.PLATFORM = "linux"
        fake = FakeEnv(None)
        mod.get_user_env, mod.set_user_env = fake.get, fake.set
        code, out, _ = run_main(mod, sb, ["check", "--json"])
        expect(code == 0 and json.loads(out)["items"][0]["status"] == "skipped-platform", f"check {out!r}")
        run_main(mod, sb, ["install"])
        expect(fake.sets == [], "install set a skipped-platform env var")
    finally:
        sb.close()


@case
def s3_env_without_a_windows_platform_exits_2():
    item = {"id": "bg-shell", "kind": "env", "name": "CLAUDE_TEST_VAR", "value": "1"}
    for variant in ({}, {"platform": "plan9"}):
        sb = Sandbox()
        sb.manifest([{**item, **variant}])
        try:
            mod = tool()
            fake = FakeEnv(None)
            mod.get_user_env, mod.set_user_env = fake.get, fake.set
            code, _, err = run_main(mod, sb, ["check"])
            expect(code == 2 and "platform" in err, f"env {variant}: exit {code} stderr {err!r}")
            expect(fake.sets == [], f"env {variant}: a rejected manifest wrote an env var")
        finally:
            sb.close()


@case
def s3_unset_env_row_names_the_variable_and_value():
    sb = env_sandbox(platform="win32")
    try:
        mod = tool()
        mod.PLATFORM = "win32"
        fake = FakeEnv(None)
        mod.get_user_env, mod.set_user_env = fake.get, fake.set
        code, out, _ = run_main(mod, sb, ["check", "--json"])
        expect(code == 1, f"unset env exit {code}, want 1")
        expect(json.loads(out)["items"][0]["detail"] == ["sets user env CLAUDE_TEST_VAR=1"], f"detail {out!r}")
    finally:
        sb.close()


@case
def s3_substitute_dest_quotes_only_a_spaced_home_path():
    mod = tool()
    expect(mod.substitute_dest("python {dest}", Path("C:/Users/John Smith/.claude/statusline.py"))
           == 'python "C:/Users/John Smith/.claude/statusline.py"', "a spaced home path was not quoted")
    expect(mod.substitute_dest("python {dest}", Path("C:/Users/zq/.claude/statusline.py"))
           == "python C:/Users/zq/.claude/statusline.py", "a space-free home path changed")


@case
def s3_powershell_hang_becomes_a_fail():
    mod = tool()

    def hung(argv, **kwargs):
        expect(kwargs.get("timeout") is not None, "powershell ran without a timeout")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with mock.patch.object(mod.subprocess, "run", side_effect=hung):
        try:
            mod.powershell("noop")
        except mod.Fail as exc:
            expect("timed out" in str(exc) or "timeout" in str(exc), f"message {exc}")
        else:
            raise AssertionError("a hung powershell did not raise Fail")


@case
def s3_lint_red_on_a_manifest_value():
    sb = Sandbox()
    try:
        sb.manifest([{"id": "bg", "kind": "env", "platform": "win32", "name": "TOOL_NAME",
                      "value": fx.SK_KEY}])
        done = sb.run("lint")
        expect(done.code == 1, f"exit {done.code}: {done.out!r}")
        expect("manifest.json" in done.out, f"lint output does not name the manifest: {done.out!r}")
    finally:
        sb.close()


class FakeRun:
    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.returncode, self.stdout, self.stderr, self.raises, self.argv = returncode, stdout, stderr, raises, None

    def __call__(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs
        if self.raises:
            raise self.raises
        return self

    def script(self):
        return self.argv[-1]


@case
def s3_powershell_boundary_quotes_names_and_values_and_fails_closed():
    mod = tool()
    real_run = mod.subprocess.run
    try:
        fake = FakeRun(stdout="it's 1\r\n")
        mod.subprocess.run = fake
        mod.set_user_env("A'B", "it's 1")
        expect(fake.script() == "[Environment]::SetEnvironmentVariable('A''B', 'it''s 1', 'User')",
               f"set script {fake.script()!r}")
        expect(mod.get_user_env("A'B") == "it's 1", "the read did not strip the line ending")
        expect(fake.script() == "[Environment]::GetEnvironmentVariable('A''B', 'User')", f"get script {fake.script()!r}")
        expect(fake.kwargs.get("timeout"), "the PowerShell call has no timeout")
        mod.subprocess.run = FakeRun(stdout="")
        expect(mod.get_user_env("NOPE") is None, "an empty read is not None")
        for broken, label in ((FakeRun(returncode=1, stderr="boom"), "nonzero exit"),
                              (FakeRun(raises=mod.subprocess.TimeoutExpired("powershell", 60)), "timeout")):
            mod.subprocess.run = broken
            try:
                mod.get_user_env("X")
            except mod.Fail:
                continue
            expect(False, f"{label} did not raise Fail")
    finally:
        mod.subprocess.run = real_run


@case
def s3_local_entries_are_listed_green_and_never_written():
    sb = Sandbox()
    try:
        sb.manifest([{"id": "machine-local", "kind": "local",
                      "entries": [{"name": "CCP_BIN", "reason": "resolves through its own chain"}]}])
        done = sb.run("check", "--json")
        expect(done.code == 0, f"exit {done.code}")
        expect(json.loads(done.out)["items"][0]["status"] == "local", f"output {done.out!r}")
        expect(sb.run("install").code == 0, "install did not exit 0")
        expect(list(sb.home.iterdir()) == [], "install wrote for a local item")
    finally:
        sb.close()


@case
def s3_lint_green_on_placeholder_assignment():
    sb = Sandbox()
    try:
        sb.template("a.txt", b"NAME_KEY=<placeholder>\nNAME_TOKEN=\n")
        sb.manifest([file_item("tool-a", "a.txt", "~/.claude/a.txt")])
        done = sb.run("lint")
        expect(done.code == 0, f"exit {done.code}: {done.out} {done.err}")
    finally:
        sb.close()


@case
def s3_lint_red_for_each_pattern_family():
    families = [
        ("home path", lambda sb: f"path {sb.home.as_posix()}\n"),
        ("user name", lambda sb: f"owner {fx.HOME_USER} here\n"),
        ("sk-", lambda sb: f"key {fx.SK_KEY}\n"),
        ("AKIA", lambda sb: f"id {fx.AWS_KEY}\n"),
        ("ghp_", lambda sb: f"tok {fx.GITHUB_TOKEN}\n"),
        ("xox", lambda sb: f"slack {fx.SLACK_TOKEN}\n"),
        ("-----BEGIN", lambda sb: f"{fx.PEM_HEADER}\n"),
        ("assignment", lambda sb: "NAME_KEY_PRIMARY=<value>\n"),
    ]
    missed = []
    for family, text in families:
        sb = Sandbox()
        try:
            sb.template("a.txt", text(sb).encode("utf-8"))
            sb.manifest([file_item("tool-a", "a.txt", "~/.claude/a.txt")])
            if sb.run("lint").code != 1:
                missed.append(family)
        finally:
            sb.close()
    expect(not missed, f"lint was green for: {missed}")


@case
def s3_lint_green_on_the_real_templates():
    sb = Sandbox()
    try:
        done = sb.run("lint", scope=REAL_SCOPE)
        expect(done.code == 0, f"exit {done.code}: {done.out!r} {done.err!r}")
    finally:
        sb.close()


@case
def s3_adopt_copies_home_over_template_with_crlf_folded():
    sb = default_sandbox()
    try:
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes(b"beta\r\ngamma\r\n")
        done = sb.run("adopt", "tool-a")
        expect(done.code == 0, f"exit {done.code}: {done.err!r}")
        expect((sb.scope / "a.txt").read_bytes() == b"beta\ngamma\n", "template bytes after adopt differ")
    finally:
        sb.close()


@case
def s3_adopt_refuses_an_equal_pair_and_leaves_template():
    sb = default_sandbox()
    try:
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes(b"alpha\n")
        done = sb.run("adopt", "tool-a")
        expect(done.code == 2, f"exit {done.code}, want 2 for an equal pair")
        expect("already matches" in done.err, f"stderr {done.err!r}")
        expect((sb.scope / "a.txt").read_bytes() == b"alpha\n", "template changed")
    finally:
        sb.close()


@case
def s3_adopt_lint_hit_refuses_and_leaves_template():
    sb = default_sandbox()
    try:
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes(f"owner {fx.HOME_USER}\n".encode("utf-8"))
        done = sb.run("adopt", "tool-a")
        expect(done.code == 1, f"exit {done.code}, want 1 for a lint hit")
        expect((sb.scope / "a.txt").read_bytes() == b"alpha\n", "template changed after a lint hit")
    finally:
        sb.close()


@case
def s3_adopt_refuses_local_and_unknown_ids():
    sb = default_sandbox()
    try:
        sb.manifest([file_item("tool-a", "a.txt", "~/.claude/a.txt"),
                     {"id": "machine-local", "kind": "local",
                      "entries": [{"name": "CCP_BIN", "reason": "its own chain"}]}])
        local = sb.run("adopt", "machine-local")
        expect(local.code == 2 and "machine-local" in local.err, f"local item: exit {local.code} {local.err!r}")
        unknown = sb.run("adopt", "no-such-item")
        expect(unknown.code == 2 and "no-such-item" in unknown.err, f"unknown id: exit {unknown.code} {unknown.err!r}")
        missing_id = sb.run("adopt")
        expect(missing_id.code == 2 and "id" in missing_id.err, f"no id: exit {missing_id.code} {missing_id.err!r}")
    finally:
        sb.close()


@case
def s3_adopt_and_lint_keep_non_ascii_bytes_without_utf8_mode():
    sb = Sandbox()
    try:
        sb.template("a.txt", "x — y\n".encode("utf-8"))
        sb.manifest([file_item("tool-a", "a.txt", "~/.claude/a.txt")])
        (sb.home / ".claude").mkdir(parents=True)
        (sb.home / ".claude" / "a.txt").write_bytes("x — é\r\n".encode("utf-8"))
        done = sb.run("adopt", "tool-a")
        expect(done.code == 0, f"adopt exit {done.code}: {done.err!r}")
        expect((sb.scope / "a.txt").read_bytes() == "x — é\n".encode("utf-8"), "adopt bytes differ")
        expect(sb.run("lint").code == 0, "lint did not exit 0 on a non-ASCII template")
    finally:
        sb.close()


# --------------------------------------------------------------------------- S4: settings-key items

def setting_item(item_id="no-connectors", key="disableClaudeAiConnectors", value=True):
    return {"id": item_id, "kind": "setting", "key": key, "value": value}


@case
def s4_unset_setting_is_red_then_installed_once_and_green():
    sb = Sandbox()
    try:
        sb.manifest([setting_item()])
        expect(sb.statuses() == {"no-connectors": "unset"}, f"statuses {sb.statuses()}")
        expect(sb.run("check").code == 1, "an unset setting must exit 1")
        expect("sets settings.json disableClaudeAiConnectors = true" in sb.run("check").out, "detail line missing")
        done = sb.run("install")
        expect(done.code == 0 and "registered no-connectors" in done.out, f"install {done.code} {done.out!r}")
        expect(read_settings(sb) == {"disableClaudeAiConnectors": True}, f"settings {read_settings(sb)}")
        expect(sb.statuses() == {"no-connectors": "set"} and sb.run("check").code == 0, "not green after install")
        expect("nothing to install" in sb.run("install").out, "second install wrote again")
    finally:
        sb.close()


@case
def s4_setting_with_another_value_is_kept_and_green():
    sb = Sandbox()
    try:
        sb.manifest([setting_item()])
        before = b'{"disableClaudeAiConnectors": false, "theme": "dark"}\n'
        write_settings(sb, before)
        expect(sb.statuses() == {"no-connectors": "differs"}, f"statuses {sb.statuses()}")
        expect(sb.run("check").code == 0, "a differing setting is informational and must exit 0")
        sb.run("install")
        expect(settings_path(sb).read_bytes() == before, "install changed an existing setting value")
    finally:
        sb.close()


@case
def s4_setting_compares_by_type_not_by_python_equality():
    sb = Sandbox()
    try:
        sb.manifest([setting_item()])
        write_settings(sb, b'{"disableClaudeAiConnectors": 1}\n')
        expect(sb.statuses() == {"no-connectors": "differs"}, "1 must not read as true")
    finally:
        sb.close()


@case
def s4_install_adds_the_key_beside_hooks_and_statusline_without_touching_them():
    sb = registered_sandbox()
    try:
        items = json.loads((sb.scope / "manifest.json").read_text(encoding="utf-8"))["items"]
        sb.manifest(items + [setting_item("no-chrome", "syncClaudeAiSkills", False)])
        before = {"theme": "dark", "statusLine": {"type": "command", "command": "mine"},
                  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}
        write_settings(sb, json.dumps(before).encode("utf-8"))
        expect(sb.run("install").code == 0, "install failed")
        after = read_settings(sb)
        expect(after["syncClaudeAiSkills"] is False, "the key was not added")
        expect(after["theme"] == "dark" and after["statusLine"] == before["statusLine"]
               and after["hooks"]["Stop"] == before["hooks"]["Stop"], "an existing key changed")
        expect(settings_path(sb).with_name("settings.json.pre-user-scope").exists(), "no backup of the original")
    finally:
        sb.close()


@case
def s4_setting_item_with_a_bad_shape_exits_2():
    for raw in ({"id": "a", "kind": "setting", "key": "k"},
                {"id": "a", "kind": "setting", "value": True},
                {"id": "a", "kind": "setting", "key": "", "value": True},
                {"id": "a", "kind": "setting", "key": "k", "value": True, "platform": "win32"}):
        sb = Sandbox()
        try:
            sb.manifest([raw])
            expect_manifest_error(sb, "'a'")
        finally:
            sb.close()


# --------------------------------------------------------------------------- S5: setting stores

def chrome_item():
    return {"id": "no-chrome", "kind": "setting", "key": "claudeInChromeDefaultEnabled", "value": False,
            "store": "claude-json"}


def memory_item():
    return {"id": "memory-dir", "kind": "setting", "key": "autoMemoryDirectory", "store": "local",
            "value": "{repo}/.claude/auto-memory"}


def claude_json(sb):
    return sb.home / ".claude.json"


def local_settings(sb):
    return sb.repo / ".claude" / "settings.local.json"


@case
def s5_claude_json_key_absent_is_a_manual_step_and_never_written():
    sb = Sandbox()
    try:
        sb.manifest([chrome_item()])
        done = sb.run("check")
        expect(sb.statuses() == {"no-chrome": "manual"}, f"statuses {sb.statuses()}")
        expect(done.code == 0, "a manual step is informational and must exit 0")
        expect("never writes" in done.out, f"no manual step printed: {done.out!r}")
        expect(sb.run("install").code == 0 and not claude_json(sb).exists(), "install created ~/.claude.json")
        planted = json.dumps({"oauthAccount": {"token": fx.CLAUDE_OAUTH_TOKEN}, "numStartups": 3}).encode("utf-8")
        claude_json(sb).write_bytes(planted)
        expect(sb.statuses() == {"no-chrome": "manual"}, "the key is still absent")
        expect(sb.run("install").code == 0 and claude_json(sb).read_bytes() == planted, "install changed ~/.claude.json")
    finally:
        sb.close()


@case
def s5_claude_json_reports_the_key_without_printing_anything_else():
    sb = Sandbox()
    try:
        sb.manifest([chrome_item()])
        claude_json(sb).write_bytes(json.dumps({"oauthAccount": {"token": fx.CLAUDE_OAUTH_TOKEN},
                                                "claudeInChromeDefaultEnabled": False}).encode("utf-8"))
        expect(sb.statuses() == {"no-chrome": "set"}, f"statuses {sb.statuses()}")
        for args in ((), ("--json",)):
            out = sb.run("check", *args).out
            expect(fx.CLAUDE_OAUTH_TOKEN not in out and "oauthAccount" not in out, "check printed ~/.claude.json content")
        claude_json(sb).write_bytes(b'{"claudeInChromeDefaultEnabled": true}')
        expect(sb.statuses() == {"no-chrome": "differs"} and sb.run("check").code == 0, "differs must exit 0")
    finally:
        sb.close()


@case
def s5_unreadable_claude_json_is_reported_not_fatal_and_not_touched():
    sb = Sandbox()
    try:
        sb.manifest([chrome_item()])
        claude_json(sb).write_bytes(b'{"numStartups": 3, "claudeIn')
        expect(sb.statuses() == {"no-chrome": "unreadable"}, f"statuses {sb.statuses()}")
        expect(sb.run("check").code == 0, "a half-written file must not fail the check")
        expect(sb.run("install").code == 0 and claude_json(sb).read_bytes() == b'{"numStartups": 3, "claudeIn',
               "install touched an unreadable ~/.claude.json")
    finally:
        sb.close()


@case
def s5_local_store_is_installed_with_the_repo_path_and_kept_when_it_differs():
    sb = Sandbox()
    try:
        sb.manifest([memory_item()])
        expect(sb.statuses() == {"memory-dir": "unset"} and sb.run("check").code == 1, "absent key must be red")
        expect(sb.run("install").code == 0, "install failed")
        want = str(Path(sb.repo, ".claude", "auto-memory"))
        got = json.loads(local_settings(sb).read_text(encoding="utf-8"))
        expect(got == {"autoMemoryDirectory": want}, f"settings.local.json {got}")
        expect(sb.statuses() == {"memory-dir": "set"} and "nothing to install" in sb.run("install").out, "not idempotent")
        local_settings(sb).write_bytes(b'{"autoMemoryDirectory": "D:/elsewhere"}')
        expect(sb.statuses() == {"memory-dir": "differs"}, "a differing path must read differs")
        sb.run("install")
        expect(local_settings(sb).read_bytes() == b'{"autoMemoryDirectory": "D:/elsewhere"}', "install changed a kept value")
        expect(not settings_path(sb).exists(), "the local store wrote the user settings.json")
    finally:
        sb.close()


@case
def s5_local_store_keeps_other_keys_backs_up_once_and_refuses_bad_json():
    sb = Sandbox()
    try:
        sb.manifest([memory_item()])
        before = b'{"outputStyle": "terse", "permissions": {"allow": ["Bash(ls:*)"]}}'
        local_settings(sb).parent.mkdir(parents=True)
        local_settings(sb).write_bytes(before)
        expect(sb.run("install").code == 0, "install failed")
        after = json.loads(local_settings(sb).read_text(encoding="utf-8"))
        expect(after["outputStyle"] == "terse" and after["permissions"] == {"allow": ["Bash(ls:*)"]}, "a key changed")
        backup = local_settings(sb).with_name("settings.local.json.pre-user-scope")
        expect(backup.read_bytes() == before, "backup is not the original bytes")
        local_settings(sb).write_bytes(b'{"outputStyle": ')
        done = sb.run("install")
        expect(done.code == 2 and local_settings(sb).read_bytes() == b'{"outputStyle": ', f"bad JSON {done.code} {done.err!r}")
    finally:
        sb.close()


@case
def s5_unknown_store_exits_2():
    sb = Sandbox()
    try:
        sb.manifest([{"id": "x", "kind": "setting", "key": "k", "value": 1, "store": "registry"}])
        expect_manifest_error(sb, "store")
    finally:
        sb.close()


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok {fn.__name__}")
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
