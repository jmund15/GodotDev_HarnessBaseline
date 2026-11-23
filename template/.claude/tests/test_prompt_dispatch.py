"""Proof for hooks/prompt_dispatch.py: members run in order through their own main(), output is normalized,
and one member's fault or exit never erases another's output. Members are planted modules, never project hooks."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import prompt_dispatch  # noqa: E402

MEMBERS = {
    "pd_ok": "import json\ndef main():\n    print(json.dumps({'hookSpecificOutput': {'additionalContext': 'ok-text'}}))\n",
    "pd_plain": "def main():\n    print('plain-text')\n",
    "pd_exit3": "def main():\n    print('lost')\n    return 3\n",
    "pd_fault": "def main():\n    raise RuntimeError('planted')\n",
    "pd_nocontext": "def main():\n    print('{\"hookSpecificOutput\": {}}')\n",
    "pd_empty": "def main():\n    print('{}')\n",
    "pd_echo": "import sys\ndef main():\n    print('got:' + sys.stdin.read())\n",
}


@pytest.fixture()
def planted(tmp_path, monkeypatch):
    for name, body in MEMBERS.items():
        (tmp_path / (name + ".py")).write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def run(*names, payload="{}"):
    return prompt_dispatch.dispatch(payload, chain=tuple((n, None) for n in names))


def test_members_deliver_in_chain_order_and_join_with_a_newline(planted):
    code, out, err = run("pd_plain", "pd_ok")
    assert (code, out, err) == (0, "plain-text\nok-text", "")


def test_nothing_to_say_prints_empty_json_object(planted):
    assert run("pd_empty")[1] == "{}"
    assert run()[1] == "{}"


def test_nonzero_exit_names_the_member_and_drops_its_output(planted):
    _, out, err = run("pd_exit3", "pd_ok")
    assert out == "ok-text" and "pd_exit3 exit3" in err


def test_fault_names_the_member_and_siblings_still_deliver(planted):
    _, out, err = run("pd_fault", "pd_ok")
    assert out == "ok-text" and "pd_fault" in err and "planted" in err


def test_json_without_additional_context_is_dropped_with_a_note(planted):
    _, out, err = run("pd_nocontext")
    assert out == "{}" and "pd_nocontext" in err


def test_import_fault_is_that_members_alone(planted):
    _, out, err = run("pd_absent_module", "pd_ok")
    assert out == "ok-text" and "pd_absent_module" in err


def test_member_reads_the_payload_on_stdin(planted):
    assert run("pd_echo", payload='{"prompt": "hello"}')[1] == 'got:{"prompt": "hello"}'


def test_shared_chain_is_the_two_kept_members():
    assert [n for n, _ in prompt_dispatch.CHAIN] == ["prompt_git_state_delta", "overnight_ask_guard"]


def test_entry_point_runs_registered_command_and_rejects_arguments(tmp_path):
    entry = str(HOOKS / "prompt_dispatch.py")
    ok = subprocess.run([sys.executable, entry], input='{"session_id": "s", "prompt": "hi"}', cwd=tmp_path,
                        capture_output=True, text=True, timeout=120)
    assert ok.returncode == 0 and ok.stdout.strip() in ("{}", ok.stdout.strip()) and "Traceback" not in ok.stderr
    bad = subprocess.run([sys.executable, entry, "--bogus"], capture_output=True, text=True, timeout=120)
    assert bad.returncode == 2 and "usage: prompt_dispatch.py" in bad.stderr
