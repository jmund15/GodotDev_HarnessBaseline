"""Re-runnable proof for hooks/cloud_test_enforcer.py.

Each case feeds a real PreToolUse payload. Cloud bare tests get updatedInput plus a note;
wrapped tests, non-test tools and non-cloud calls pass unchanged.

    python3 .claude/tests/test_cloud_test_enforcer.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "cloud_test_enforcer.py")


def run(command, cloud=True, tool="Bash", flag=None):
    payload = {"tool_name": tool, "tool_input": {"command": command}}
    env = dict(os.environ, CLAUDE_CODE_REMOTE="true" if cloud else "false")
    if flag is not None:
        env["CLAUDE_CODE_REMOTE"] = flag
    return subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=30, env=env)


def rewritten(result):
    assert result.returncode == 0 and not result.stderr, (result.returncode, result.stderr)
    return json.loads(result.stdout)['hookSpecificOutput']['updatedInput']['command']


def test_cloud_test_rewrite():
    result = run('cd /repo && dotnet test --filter Tests.Logic')
    assert rewritten(result) == 'cd /repo && xvfb-run --auto-servernum dotnet test --settings .runsettings --filter Tests.Logic'
    assert 'additionalContext' in json.loads(result.stdout)['hookSpecificOutput']


def test_cloud_read_only_mentions():
    for command in ("printf '%s' 'dotnet test'", "python3 - <<'EOF'\nprint('dotnet test')\nEOF"):
        result = run(command)
        assert result.returncode == 0
        assert 'updatedInput' not in json.loads(result.stdout).get('hookSpecificOutput', {})
    result = run("printf '%s' 'dotnet test'; dotnet test")
    assert rewritten(result) == "printf '%s' 'dotnet test'; xvfb-run --auto-servernum dotnet test --settings .runsettings"


def test_assignment_mentions_are_data():
    for prefix in ("NOTE='abc dotnet test xyz' ", 'NOTE="run dotnet test" ', "NOTE='dotnet test' "):
        command = prefix + 'dotnet test > result.log # dotnet test'
        assert rewritten(run(command)) == prefix + 'xvfb-run --auto-servernum dotnet test --settings .runsettings > result.log # dotnet test'
    command = "cat > message <<'EOF'\ndotnet test\nEOF\nNOTE='dotnet test' dotnet test"
    assert rewritten(run(command)) == command.rsplit(' dotnet test', 1)[0] + ' xvfb-run --auto-servernum dotnet test --settings .runsettings'
    captured = 'NOTE="$(printf dotnet)" dotnet test'
    assert json.loads(run(captured).stdout) == {}


def main():
    failures = []
    cases = (
        ("cloud bare dotnet test cites Quick Start and names xvfb-run",
         run("dotnet test --settings .runsettings --filter Tests.Logic"),
         lambda r: rewritten(r) == 'xvfb-run --auto-servernum dotnet test --settings .runsettings --filter Tests.Logic'),
        ("cloud compound command: the rewrite puts xvfb-run in front of dotnet test, not the whole line",
         run("cd /repo && dotnet test --filter Tests.Logic"),
         lambda r: rewritten(r) == 'cd /repo && xvfb-run --auto-servernum dotnet test --settings .runsettings --filter Tests.Logic'),
        ("cloud rewrite keeps the caller's own --settings",
         run("dotnet test --settings custom.runsettings --filter Tests.Logic"),
         lambda r: rewritten(r) == 'xvfb-run --auto-servernum dotnet test --settings custom.runsettings --filter Tests.Logic'),
        ("cloud xvfb-run test is allowed",
         run("xvfb-run --auto-servernum dotnet test --settings .runsettings"),
         lambda r: r.returncode == 0 and r.stdout.strip() == "{}"),
        ("cloud dotnet build is allowed", run("dotnet build"),
         lambda r: r.returncode == 0 and r.stdout.strip() == "{}"),
        ("local bare dotnet test is allowed", run("dotnet test", cloud=False),
         lambda r: r.returncode == 0 and r.stdout.strip() == "{}"),
        ("non-Bash bare test is allowed", run("dotnet test", tool="PowerShell"),
         lambda r: r.returncode == 0 and r.stdout.strip() == "{}"),
        ("cloud flag 1 counts as cloud (the shared truthy set)", run("dotnet test", flag="1"),
         lambda r: rewritten(r).startswith("xvfb-run --auto-servernum dotnet test")),
        ("cloud flag YES counts as cloud (case-insensitive)", run("dotnet test", flag="YES"),
         lambda r: rewritten(r).startswith("xvfb-run --auto-servernum dotnet test")),
        ("cloud flag 0 does not count as cloud", run("dotnet test", flag="0"),
         lambda r: r.returncode == 0 and r.stdout.strip() == "{}"),
    )
    for label, result, predicate in cases:
        ok = predicate(result) and "Traceback (most recent call last)" not in result.stderr
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s :: exit=%s stderr=%r stdout=%r" % (
                label, result.returncode, result.stderr[:300], result.stdout[:300]))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for failure in failures:
        print("  FAIL " + failure)
    return 1 if failures else 0


if __name__ == "__main__":
    rc = main()
    test_cloud_test_rewrite()
    test_cloud_read_only_mentions()
    test_assignment_mentions_are_data()
    sys.exit(rc)
