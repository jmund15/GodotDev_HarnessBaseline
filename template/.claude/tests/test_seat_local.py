"""Proof for the local-model seat (`inkling-local`): scripts/seat_local.sh through scripts/seat.sh, and the record and
seat-row reads in tools/launch_check.py. The process is the subject, so each launcher case spawns seat.sh in a fixture
checkout whose `.claude` holds the shipped scripts, with planted fakes first on PATH: `uv` (runs the fake
backend_hold.py the way `uv run` would), `litellm` (serves /health/liveliness and keeps the config it was given) and
`claude` (records argv and environment and prints a result cut from a live `claude -p --output-format json` run,
tests/fixtures/seat_local/result.json). No llama-server, model or real seat run starts.

    python -m pytest .claude/tests/test_seat_local.py -q --durations=0
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import launch_check as lc  # noqa: E402

CLAUDE = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")  # Git Bash: a bare `bash` from Python is WSL on Windows
FIXTURE = CLAUDE / "tests" / "fixtures" / "seat_local" / "result.json"
FILES = ("scripts/seat.sh", "scripts/seat_claude.sh", "scripts/seat_agy.sh", "scripts/seat_local.sh",
         "tools/launch_check.py", "tools/agy_seat.py", "tools/adaptation.py", "hooks/_hook_state.py",
         "guards/rails.md", "seats.json")
MODEL = "inkling-2-mini-ud-q3_k_xl"
SERVER_ARGV = ('llama-server --temp 1.0 --top-p 1.0 --chat-template-kwargs {"reasoning_effort": "%s"}')

STUB_UV = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$STUB_DIR/uv_argv.txt"
[ "$1" = run ] || exit 1
shift 4
exec "$STUB_PYTHON" "$@"
"""
STUB_LITELLM = """#!/usr/bin/env bash
exec "$STUB_PYTHON" "$STUB_DIR/fake_litellm.py" "$@"
"""
FAKE_LITELLM = '''
import http.server, os, sys, threading
if "--version" in sys.argv:
    print("litellm " + os.environ.get("FAKE_LITELLM_VERSION", "1.110.0"))
    sys.exit(0)
args = sys.argv[1:]
stub = os.environ["STUB_DIR"]
open(os.path.join(stub, "litellm_config.yaml"), "w").write(open(args[args.index("--config") + 1]).read())
open(os.path.join(stub, "litellm_argv.txt"), "w").write(" ".join(args))


class Alive(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"I'm alive!")

    def log_message(self, *a):
        pass


threading.Timer(120, lambda: os._exit(0)).start()  # a proxy the launcher failed to stop ends itself
http.server.HTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), Alive).serve_forever()
'''
FAKE_HOLD = '''
import json, os, subprocess, sys
argv = sys.argv[1:]
cmd = []
if "--" in argv:
    i = argv.index("--")
    argv, cmd = argv[:i], argv[i + 1:]
opts = dict(zip(argv[1::2], argv[2::2]))
stub = os.environ["STUB_DIR"]
open(os.path.join(stub, "hold_argv.json"), "w").write(json.dumps({"action": argv[0], "opts": opts, "cmd": cmd}))
if argv[0] == "check":
    if os.environ.get("FAKE_HOLD_CHECK") == "bad":
        print("UNAVAILABLE (registry port 8091 != models.yaml port 1)")
        sys.exit(2)
    print("OK")
    sys.exit(0)
if os.environ.get("FAKE_HOLD_FAIL"):
    print("UNAVAILABLE (llama-server did not start: planted)", flush=True)
    sys.exit(3)
print("[backend_hold] server argv: " + os.environ["FAKE_SERVER_ARGV"] % opts.get("--effort"), file=sys.stderr, flush=True)
if os.environ.get("FAKE_HOLD_RESTART"):
    print("[backend_hold] llama-server exited with code 1 while the command runs; restarting it", file=sys.stderr, flush=True)
    print("[backend_hold] llama-server restarted", file=sys.stderr, flush=True)
rc = subprocess.call(cmd)
open(os.path.join(stub, "hold_after_child.txt"), "w").write(str(rc))
sys.exit(rc)
'''
STUB_CLAUDE = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$STUB_DIR/argv.txt"
cat > "$STUB_DIR/stdin.txt"
env | grep -E '^(ANTHROPIC|CLAUDE)' | sort > "$STUB_DIR/env.txt"
"$STUB_PYTHON" -c 'import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1] + "/health/liveliness", timeout=5).status)' \
  "${ANTHROPIC_BASE_URL:-http://127.0.0.1:1}" > "$STUB_DIR/health.txt" 2>/dev/null
if [ -n "${STUB_FAIL:-}" ]; then echo "claude: forced failure" >&2; exit 5; fi
cat "$STUB_DIR/result.json"
"""
BRIEF = "inputs:\n- a.md\n"


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    repo = tmp_path_factory.mktemp("seat_local_template") / "repo"
    (repo / ".claude").mkdir(parents=True)
    for rel in FILES:
        target = repo / ".claude" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CLAUDE / rel, target)
    (repo / "a.md").write_text("input\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture(autouse=True)
def _checkout_of(_template, tmp_path):
    shutil.copytree(_template, tmp_path / "repo")


def _stub(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _env(tmp_path, extra=None):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    aiw = tmp_path / "aiw"
    for d in (home, bin_dir, aiw):
        d.mkdir(exist_ok=True)
    for name, text in (("uv", STUB_UV), ("litellm", STUB_LITELLM), ("claude", STUB_CLAUDE)):
        _stub(bin_dir / name, text)
    (aiw / "backend_hold.py").write_text(FAKE_HOLD, encoding="utf-8", newline="\n")
    (tmp_path / "fake_litellm.py").write_text(FAKE_LITELLM, encoding="utf-8", newline="\n")
    shutil.copyfile(FIXTURE, tmp_path / "result.json")
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), STUB_DIR=str(tmp_path), STUB_PYTHON=sys.executable,
               AI_WORKER_DIR=str(aiw), FAKE_SERVER_ARGV=SERVER_ARGV,
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    for name in ("STUB_FAIL", "FAKE_HOLD_FAIL", "FAKE_HOLD_CHECK", "FAKE_HOLD_RESTART", "FAKE_LITELLM_VERSION", "UV_BIN"):
        env.pop(name, None)
    env.update(extra or {})
    return env


def _msys(path):
    posix = Path(path).as_posix()
    return "/" + posix[0].lower() + posix[2:] if posix[1:3] == ":/" else posix


def _seat(tmp_path, env, *args, brief=True):
    repo = tmp_path / "repo"
    if brief:
        (repo / "brief.md").write_text(BRIEF, encoding="utf-8")
    return subprocess.run([BASH, _msys(repo / ".claude" / "scripts" / "seat.sh"), *args], cwd=str(repo), env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _dispatch(tmp_path, env, *flags):
    return _seat(tmp_path, env, *flags, "inkling-local", "brief.md", "out.md")


def _json(tmp_path, name):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def _lines(tmp_path, name):
    return (tmp_path / name).read_text(encoding="utf-8").split("\n")


# --- --check (requirement 6) ---------------------------------------------------------------------------------

def test_check_hands_backend_hold_the_rows_port_window_and_backend_and_starts_nothing(tmp_path):
    started = time.time()
    done = _seat(tmp_path, _env(tmp_path), "--check", "inkling-local", brief=False)
    took = time.time() - started
    assert done.returncode == 0 and done.stdout.strip() == "OK", done.stderr
    held = _json(tmp_path, "hold_argv.json")
    assert held["action"] == "check"
    assert held["opts"] == {"--backend": MODEL, "--port": "8091", "--ctx": "98304"}
    for started_nothing in ("litellm_config.yaml", "argv.txt", "hold_after_child.txt"):
        assert not (tmp_path / started_nothing).exists()
    assert took < 20, took  # no snapshot re-exec: the bench preflight times a check out


def test_check_prints_the_one_unavailable_line_and_exit_code_of_a_mismatch(tmp_path):
    done = _seat(tmp_path, _env(tmp_path, {"FAKE_HOLD_CHECK": "bad"}), "--check", "inkling-local", brief=False)
    assert done.returncode == 2 and done.stdout.startswith("UNAVAILABLE") and len(done.stdout.splitlines()) == 1


def test_check_refuses_a_litellm_below_the_chat_usage_floor(tmp_path):
    done = _seat(tmp_path, _env(tmp_path, {"FAKE_LITELLM_VERSION": "1.109.9"}), "--check", "inkling-local", brief=False)
    assert done.returncode == 4 and done.stdout.startswith("UNAVAILABLE")
    assert not (tmp_path / "hold_argv.json").exists()


def test_check_is_answered_by_every_adapter(tmp_path):
    (tmp_path / "home" / ".claude-secondary").mkdir(parents=True)
    done = _seat(tmp_path, _env(tmp_path), "--check", "claude-secondary", brief=False)
    assert done.returncode == 0 and done.stdout.strip() == "OK"


# --- the refusals (requirements 2 and 3) ----------------------------------------------------------------------

@pytest.mark.parametrize("flags", [(), ("-e", "turbo"), ("-X", "-e", "low"), ("-e", "low", "-X"), ("-e", "low", "-d")])
def test_a_dispatch_without_a_known_effort_or_with_a_detach_flag_refuses_before_anything_starts(tmp_path, flags):
    done = _dispatch(tmp_path, _env(tmp_path), *flags)
    assert done.returncode == 2
    for started in ("uv_argv.txt", "hold_argv.json", "argv.txt", "litellm_config.yaml"):
        assert not (tmp_path / started).exists()
    assert not (tmp_path / "repo" / "out.md").exists()


def test_a_seat_whose_model_lists_no_efforts_takes_no_effort(tmp_path):
    (tmp_path / "home" / ".claude-secondary").mkdir(parents=True)
    done = _seat(tmp_path, _env(tmp_path), "-e", "low", "claude-secondary", "brief.md", "out.md")
    assert done.returncode == 2 and not (tmp_path / "argv.txt").exists()


def test_the_adapter_alone_refuses_a_dispatch_with_no_effort(tmp_path):
    env = _env(tmp_path, {"SEAT_MODEL": MODEL, "SEAT_CONTEXT": "98304", "SEAT_BASE_URL": "http://127.0.0.1:8091/v1",
                          "SEAT_EFFORT": ""})
    done = subprocess.run([BASH, _msys(tmp_path / "repo" / ".claude" / "scripts" / "seat_local.sh"), "p.md", "wd"],
                          env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 2 and not (tmp_path / "uv_argv.txt").exists()


# --- a dispatch (requirements 1, 4, 5, 8, 9) ------------------------------------------------------------------

def test_a_dispatch_runs_the_child_under_backend_hold_at_the_effort_and_returns_the_answer(tmp_path):
    done = _dispatch(tmp_path, _env(tmp_path), "-e", "low")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "repo" / "out.md").read_text(encoding="utf-8").strip() == "STUB ANSWER"
    held = _json(tmp_path, "hold_argv.json")
    assert held["action"] == "run"
    assert held["opts"] == {"--backend": MODEL, "--port": "8091", "--ctx": "98304", "--effort": "low"}
    uv = _lines(tmp_path, "uv_argv.txt")
    assert uv[:2] == ["run", "--project"] and "python" in uv and "-u" in uv
    # Spawned, never exec'd: the fake backend_hold writes this only after its child returned.
    assert (tmp_path / "hold_after_child.txt").read_text(encoding="utf-8") == "0"


def test_the_child_runs_by_this_git_bash_through_an_absolute_path(tmp_path):
    assert _dispatch(tmp_path, _env(tmp_path), "-e", "low").returncode == 0
    cmd = _json(tmp_path, "hold_argv.json")["cmd"]
    # backend_hold spawns from Windows Python, where a bare `bash` is WSL's and cannot open a C:/ path.
    assert cmd[0] != "bash" and Path(cmd[0]).name.lower() in ("bash", "bash.exe") and os.path.isabs(cmd[0])
    if sys.platform == "win32":
        assert re.match(r"^[A-Za-z]:/", cmd[0])
    assert cmd[1].endswith("scripts/seat_local.sh") and cmd[2] == "--held" and "--" in cmd


def test_the_proxy_runs_in_front_of_the_server_with_the_literal_local_key(tmp_path):
    assert _dispatch(tmp_path, _env(tmp_path), "-e", "low").returncode == 0
    config = (tmp_path / "litellm_config.yaml").read_text(encoding="utf-8")
    assert "api_key: local\n" in config and "model: openai/" + MODEL in config
    assert "api_base: http://127.0.0.1:8091/v1" in config and "master_key: sk-" in config
    assert "use_chat_completions_url_for_anthropic_messages: true" in config
    assert (tmp_path / "health.txt").read_text(encoding="utf-8").strip() == "200"  # the child reached a live proxy
    env = (tmp_path / "env.txt").read_text(encoding="utf-8")
    assert "ANTHROPIC_BASE_URL=http://127.0.0.1:" in env and "ANTHROPIC_BASE_URL=http://127.0.0.1:8091" not in env


def test_the_child_declares_the_servers_window_and_a_bounded_output_cap_and_scrubs_the_parents_login(tmp_path):
    env = _env(tmp_path, {"ANTHROPIC_API_KEY": "sk-ant-decoy", "CLAUDE_CODE_OAUTH_TOKEN": "decoy-oauth"})
    assert _dispatch(tmp_path, env, "-e", "low").returncode == 0
    seen = (tmp_path / "env.txt").read_text(encoding="utf-8")
    # Auto-compaction through the proxy rests on these two: the window the server holds and a quarter-window cap.
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS=98304" in seen and "CLAUDE_CODE_MAX_OUTPUT_TOKENS=24576" in seen
    assert "decoy" not in seen and "CLAUDE_CODE_SIDECAR=1" in seen
    assert f"ANTHROPIC_SMALL_FAST_MODEL={MODEL}" in seen


def test_the_child_is_seat_claudes_headless_read_only_child_with_ai_worker_denied(tmp_path):
    assert _dispatch(tmp_path, _env(tmp_path), "-e", "high").returncode == 0
    argv = _lines(tmp_path, "argv.txt")
    assert argv[0] == "-p" and argv[argv.index("--model") + 1] == MODEL
    assert argv[argv.index("--output-format") + 1] == "json" and argv[argv.index("--effort") + 1] == "high"
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "mcp__ai-worker" in denied and "Write" in denied and "acceptEdits" not in argv
    stdin = (tmp_path / "stdin.txt").read_text(encoding="utf-8")
    assert stdin.endswith(BRIEF) and stdin.startswith((CLAUDE / "guards" / "rails.md").read_text(encoding="utf-8").rstrip())


@pytest.mark.parametrize("writes", ["yes", "no"])
def test_seat_claude_denies_what_the_local_seat_names_in_either_write_mode(tmp_path, writes):
    (tmp_path / "wd").mkdir()
    (tmp_path / "p.md").write_text("p", encoding="utf-8")
    env = _env(tmp_path, {"SEAT_MODEL": "m", "SEAT_WRITES": writes, "SEAT_CONFIG_DIR": "", "SEAT_SETTINGS": "s.json",
                          "SEAT_DISALLOWED": "mcp__ai-worker", "SEAT_EFFORT": ""})
    if writes == "yes":
        env["SEAT_OUTPUT_FORMAT"] = "json"
    done = subprocess.run([BASH, _msys(CLAUDE / "scripts" / "seat_claude.sh"), _msys(tmp_path / "p.md"),
                           _msys(tmp_path / "wd")], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr
    argv = _lines(tmp_path, "argv.txt")
    assert "mcp__ai-worker" in argv[argv.index("--disallowedTools") + 1].split(",")
    assert ("acceptEdits" in argv) == (writes == "yes") and "--effort" not in argv
    assert argv[argv.index("--output-format") + 1] == ("json" if writes == "yes" else "text")  # text is the default


# --- the hold verb: any child under the hold and the proxy ----------------------------------------------------

CHILD = """#!/usr/bin/env bash
env | grep -E '^(ANTHROPIC|CLAUDE|SEAT_)' | sort > "$STUB_DIR/child_env.txt"
printf '%s\n' "$@" > "$STUB_DIR/child_argv.txt"
"$STUB_PYTHON" -c 'import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1] + "/health/liveliness", timeout=5).status)'   "$ANTHROPIC_BASE_URL" > "$STUB_DIR/child_health.txt" 2>/dev/null
printf 'out-one\n'
echo "err-one" >&2
if [ -n "${CHILD_WAIT:-}" ]; then until [ -e "$CHILD_WAIT" ]; do sleep 0.1; done; fi
printf 'out-two'
exit "${CHILD_RC:-0}"
"""


def _verb(tmp_path, env, *args, popen=False):
    local = _msys(tmp_path / "repo" / ".claude" / "scripts" / "seat_local.sh")
    _stub(tmp_path / "child.sh", CHILD)
    kw = dict(cwd=str(tmp_path / "repo"), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
              stderr=subprocess.PIPE, text=True)
    if popen:
        return subprocess.Popen([BASH, local, "hold", *args], **kw)
    return subprocess.run([BASH, local, "hold", *args], **kw)


def _child_args(tmp_path):
    return ["--", BASH, _msys(tmp_path / "child.sh"), "a b", "c"]


def test_the_verb_runs_an_arbitrary_child_under_the_hold_with_the_documented_env(tmp_path):
    seen = {"ANTHROPIC_API_KEY": "sk-ant-decoy", "CLAUDE_CODE_OAUTH_TOKEN": "decoy-oauth", "ANTHROPIC_MODEL": "decoy"}
    done = _verb(tmp_path, _env(tmp_path, seen), "-e", "high", *_child_args(tmp_path))
    assert done.returncode == 0, done.stderr
    held = _json(tmp_path, "hold_argv.json")
    assert held["action"] == "run" and held["opts"] == {"--backend": MODEL, "--port": "8091", "--ctx": "98304", "--effort": "high"}
    assert (tmp_path / "hold_after_child.txt").read_text(encoding="utf-8") == "0"
    assert _lines(tmp_path, "child_argv.txt")[:2] == ["a b", "c"]
    env = dict(line.split("=", 1) for line in _lines(tmp_path, "child_env.txt") if line)
    config = (tmp_path / "litellm_config.yaml").read_text(encoding="utf-8")
    route = re.search(r"model_name: (\S+)", config).group(1)
    assert env["SEAT_MODEL"] == route == MODEL
    assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:") and not env["ANTHROPIC_BASE_URL"].endswith(":8091")
    assert env["ANTHROPIC_AUTH_TOKEN"] in config and env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "98304" and env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "24576"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == env["CLAUDE_CODE_SUBAGENT_MODEL"] == MODEL
    assert env["SEAT_PROXY_VERSION"] == "1.110.0"
    assert "ANTHROPIC_MODEL" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    # Nothing else is added: no settings, deny list or output format.
    extra = {k for k in env if k not in ("SEAT_MODEL", "SEAT_PROXY_VERSION", "CLAUDE_CODE_SIDECAR") and not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_M", "CLAUDE_CODE_S"))}
    assert extra <= {"SEAT_NAME", "SEAT_WRITES", "SEAT_CONTEXT", "SEAT_MAX_OUTPUT", "SEAT_BASE_URL", "SEAT_EFFORT",
                     "SEAT_EFFORTS", "SEAT_CONFIG_DIR", "SEAT_HOLD_SCRATCH"}, extra
    assert "SEAT_SETTINGS" not in env and "SEAT_OUTPUT_FORMAT" not in env and "SEAT_DISALLOWED" not in env
    assert (tmp_path / "child_health.txt").read_text(encoding="utf-8").strip() == "200"


def test_the_verbs_stderr_is_backend_holds_lines_byte_for_byte_beside_the_childs(tmp_path):
    done = _verb(tmp_path, _env(tmp_path, {"FAKE_HOLD_RESTART": "1"}), "-e", "low", *_child_args(tmp_path))
    lines = done.stderr.splitlines()
    assert "[backend_hold] server argv: " + SERVER_ARGV % "low" in lines
    assert "[backend_hold] llama-server exited with code 1 while the command runs; restarting it" in lines
    assert "[backend_hold] llama-server restarted" in lines and "err-one" in lines
    assert done.stdout == "out-one\nout-two"


def test_the_verb_streams_the_childs_stdout_live_and_returns_its_code(tmp_path):
    go = tmp_path / "go"
    proc = _verb(tmp_path, _env(tmp_path, {"CHILD_WAIT": str(go), "CHILD_RC": "7"}), "-e", "low", *_child_args(tmp_path), popen=True)
    try:
        assert proc.stdout.readline() == "out-one\n"  # arrives while the child still runs
        assert proc.poll() is None
        go.write_text("x")
        assert proc.wait(timeout=60) == 7
    finally:
        proc.kill()


@pytest.mark.parametrize("args", [(), ("--", "true"), ("-e", "low"), ("-e", "low", "--"), ("-e", "low", "true"), ("-e", "turbo", "--", "true"),
                                  ("-e", "low", "-m", "nope", "--", "true")])
def test_the_verb_refuses_without_an_effort_a_known_seat_or_a_child(tmp_path, args):
    done = _verb(tmp_path, _env(tmp_path), *args)
    assert done.returncode == 2 and done.stderr.strip()
    assert not (tmp_path / "hold_argv.json").exists() and not (tmp_path / "litellm_config.yaml").exists()


def test_the_verb_runs_the_preflight_first(tmp_path):
    done = _verb(tmp_path, _env(tmp_path, {"FAKE_LITELLM_VERSION": "1.109.9"}), "-e", "low", *_child_args(tmp_path))
    assert done.returncode == 4 and "UNAVAILABLE" in done.stdout
    assert not (tmp_path / "hold_argv.json").exists() and not (tmp_path / "child_env.txt").exists()


# --- the record (requirements 2 and 9) -------------------------------------------------------------------------

def test_the_record_keeps_the_model_proxy_version_usage_and_the_server_argv_line_beside_the_answer(tmp_path):
    assert _dispatch(tmp_path, _env(tmp_path), "-e", "low").returncode == 0
    record = json.loads((tmp_path / "repo" / "out.record.json").read_text(encoding="utf-8"))
    live = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert record["seat"] == "inkling-local" and record["model"] == MODEL and record["effort"] == "low"
    assert record["proxyVersion"] == "1.110.0" and record["rc"] == 0
    assert record["serverArgv"] == SERVER_ARGV % "low" and record["serverArgvHasEffort"] is True
    assert record["usage"] == live["usage"] and record["modelUsage"] == live["modelUsage"]
    assert record["numTurns"] == live["num_turns"] and record["isError"] is False


def test_a_failed_server_start_returns_its_code_and_line_and_still_writes_the_record(tmp_path):
    done = _dispatch(tmp_path, _env(tmp_path, {"FAKE_HOLD_FAIL": "1"}), "-e", "low")
    assert done.returncode == 3 and "llama-server did not start" in done.stderr
    assert not (tmp_path / "repo" / "out.md").exists() and not (tmp_path / "argv.txt").exists()
    record = json.loads((tmp_path / "repo" / "out.record.json").read_text(encoding="utf-8"))
    assert record["rc"] == 3 and record["serverArgv"] is None and record["serverArgvHasEffort"] is False


def test_a_child_failure_comes_back_with_its_code_and_stderr(tmp_path):
    done = _dispatch(tmp_path, _env(tmp_path, {"STUB_FAIL": "1"}), "-e", "low")
    assert done.returncode == 5 and "claude: forced failure" in done.stderr
    assert not (tmp_path / "repo" / "out.md").exists()
    assert json.loads((tmp_path / "repo" / "out.record.json").read_text(encoding="utf-8"))["rc"] == 5


# --- the units in process ---------------------------------------------------------------------------------------

def _record(raw, hold):
    return lc.record("inkling-local", MODEL, "low", 0, "1.110.0", raw, hold)


def test_record_reads_the_answer_from_a_result_object_and_the_raw_text_from_anything_else():
    live = FIXTURE.read_text(encoding="utf-8")
    answer, made = _record(live, "")
    assert answer == "STUB ANSWER" and made["sessionId"] == json.loads(live)["session_id"]
    raw, made = _record("Error: not json", "")
    assert raw == "Error: not json" and made["usage"] is None and made["isError"] is None


def test_record_keeps_only_backend_holds_lines_and_names_whether_the_argv_carries_an_effort():
    held = "[backend_hold] server argv: " + SERVER_ARGV % "low" + "\nnoise\n[backend_hold] llama-server exited with code 1; restarting\n"
    _, made = _record("{}", held)
    assert made["serverArgv"] == SERVER_ARGV % "low" and made["serverArgvHasEffort"] is True
    assert len(made["holdLines"]) == 2
    _, bare = _record("{}", "[backend_hold] server argv: llama-server --temp 1.0\n")
    assert bare["serverArgvHasEffort"] is False


def test_the_shipped_rows_carry_what_the_adapter_reads(monkeypatch):
    monkeypatch.setattr(os.path, "isdir", lambda path: True)
    got = dict(line.split("=", 1) for line in lc.seat_assignments("inkling-local").splitlines())
    assert got["SEAT_ADAPTER"] == "scripts/seat_local.sh" and got["SEAT_WRITES"] == "no" and got["SEAT_MAX"] == "1"
    assert got["SEAT_MODEL"] == MODEL and got["SEAT_CONTEXT"] == "98304" and got["SEAT_MAX_OUTPUT"] == "32768"
    assert got["SEAT_BASE_URL"] == "http://127.0.0.1:8091/v1"
    assert got["SEAT_EFFORTS"].strip("'").split() == ["low", "medium", "high", "xhigh", "max"]
    price = lc.table(CLAUDE)["models"][MODEL]["price"]
    assert price["cacheMissPer1M"] == price["cacheHitPer1M"] == price["outputPer1M"] == 0
    claude_row = dict(line.split("=", 1) for line in lc.seat_assignments("claude-secondary").splitlines())
    assert claude_row["SEAT_EFFORTS"] == "''"
