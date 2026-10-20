"""Re-runnable proof for hooks/gate_cadence_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case below
feeds the hook a real PreToolUse payload and asserts on the channel it emits, so a
regression shows up as a failing case rather than as a surprise denial mid-session.

    python3 .claude/tests/test_gate_cadence_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "hooks", "gate_cadence_guard.py")
GATE = "pwsh -NoProfile -File .claude/scripts/regression_gate.ps1"
VERIFY = "pwsh -NoProfile -File .claude/scripts/verify.ps1"

ALLOW, DENY, NUDGE = "allow", "deny", "nudge"


def run(command, session, repo):
    payload = {"tool_name": "Bash", "session_id": session, "cwd": repo,
               "tool_input": {"command": command}}
    # The hook roots its state on CLAUDE_PROJECT_DIR before the payload cwd. Inherited from a session,
    # it points every case at one real checkout, so gate counts leak across runs. Each case owns its repo.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=90, env=env)
    if r.returncode not in (0, 2) or 'Traceback' in r.stderr:
        return 'CRASH', r.stderr
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    try:
        doc = json.loads(out)
    except ValueError:
        return "UNPARSED", out
    hook = doc.get("hookSpecificOutput") or {}
    if hook.get("permissionDecision") == "deny":
        return DENY, hook.get("permissionDecisionReason", "")
    if hook.get("additionalContext"):
        return NUDGE, hook["additionalContext"]
    return ALLOW, out


CASES = [
    # --- reading the gate script is not invoking it -------------------------
    ("read-only grep of the gate script is not an invocation",
     'grep -n "QueueStatus" .claude/scripts/regression_gate.ps1', ALLOW, ""),
    ("read-only wc of the gate script",
     "wc -l .claude/scripts/regression_gate.ps1", ALLOW, ""),
    ("git show of the gate script",
     "git show HEAD:.claude/scripts/regression_gate.ps1 | head -40", ALLOW, ""),
    ("editing the gate's doc is not an invocation",
     'sed -i "s/x/y/" .claude/commands/regression_gate.md', ALLOW, ""),
    ("a pwsh -Command that merely NAMES the script is not an invocation",
     'pwsh -NoProfile -Command "ParseFile(.claude/scripts/regression_gate.ps1)"', ALLOW, ""),

    # --- a missing marker is a nudge, never a deny -------------------------------------------
    ("bare invocation with no marker gets the nudge",
     GATE, NUDGE, "declares no position"),
    ("bogus marker gets the nudge",
     GATE + "  # gate: whenever", NUDGE, "declares no position"),
    ("the retired `mid` marker is no longer a position",
     GATE + "  # gate: mid", NUDGE, "declares no position"),
    ("a marker after a pipe declares the one piped gate",
     GATE + " -Detach 2>&1 | tail -3  # gate: final", ALLOW, ""),
    ("a piped gate with no marker still gets the nudge",
     GATE + " -Detach 2>&1 | tail -3", NUDGE, "declares no position"),
    ("-StaticOnly is exempt from the marker",
     GATE + " -StaticOnly", ALLOW, ""),
    ("-RouteOnly runs no gate, so it is exempt from the marker",
     GATE + " -RouteOnly", ALLOW, ""),
    ("-Watch is exempt: it streams a detached run's OUT and runs no gate",
     GATE + ' -Watch -Out "C:/x/.claude/scratch/test_runs/gate_run_1.out"', ALLOW, ""),
    ("-Detach -Watch spawns a gate before it watches, so it gets the marker nudge",
     GATE + " -Detach -Watch", NUDGE, "declares no position"),
    ("-Watch -Detach in either order still gets the marker nudge",
     GATE + " -Watch -Detach", NUDGE, "declares no position"),

    # --- the mandatory stops ------------------------------------------------
    ("final is allowed uncapped",
     GATE + "  # gate: final", ALLOW, ""),
    ("prepush is allowed",
     GATE + "  # gate: prepush", ALLOW, ""),

    # --- retired width flags name their replacement -------------------------
    ("-Smoke is retired and names verify.ps1",
     GATE + " -Smoke  # gate: final", DENY, "verify.ps1"),
    ("-Targeted is retired",
     GATE + " -Targeted NPCs  # gate: final", DENY, "verify.ps1"),
    ("-SkipStatic is retired",
     GATE + " -SkipStatic  # gate: final", DENY, "verify.ps1"),
    ("a retired flag is caught even with no marker at all",
     GATE + " -SmokeImport", DENY, "verify.ps1"),

    # --- the narrow instrument is never gated -------------------------------
    ("verify.ps1 needs no marker and is never denied",
     VERIFY + " -Scope NPCs,AI", ALLOW, ""),
    ("whole-tier filter is nudged, not denied",
     'pwsh -File .claude/scripts/run_test_suite.ps1 '
     '-Filter "FullyQualifiedName~Tests.Integration" -Label x', NUDGE, "blast-zone"),
    ("blast-zone filter is silent",
     'pwsh -File .claude/scripts/run_test_suite.ps1 '
     '-Filter "FullyQualifiedName~Tests.Integration.NPCs" -Label x', ALLOW, ""),
]

# Cases that depend on accumulated per-session state, run in order on one session.
SEQUENCE = [
    ("first checkpoint allowed", GATE + "  # gate: checkpoint", ALLOW, ""),
    ("second checkpoint allowed", GATE + "  # gate: checkpoint", ALLOW, ""),
    ("third checkpoint allowed", GATE + "  # gate: checkpoint", ALLOW, ""),
    ("final is never capped", GATE + "  # gate: final", ALLOW, ""),
    ("prepush is never capped", GATE + "  # gate: prepush", ALLOW, ""),
]

COMPOUND = [
    ("marked checkpoint then unmarked gate nudged",
     GATE + "  # gate: checkpoint\n" + GATE, NUDGE, "declares no position"),
    ("commented gate mention does not execute",
     GATE + "  # gate: checkpoint; " + GATE, ALLOW, ""),
    ("subshell gate without marker nudged",
     "(" + GATE + ")", NUDGE, "declares no position"),
    ("brace group gate without marker nudged",
     "{ " + GATE + "; }", NUDGE, "declares no position"),
    ("if body gate without marker nudged",
     "if true; then " + GATE + "; fi", NUDGE, "declares no position"),
    ("else body gate without marker nudged",
     "if false; then true; else " + GATE + "; fi", NUDGE, "declares no position"),
    ("and chain gate without marker nudged",
     "true && " + GATE, NUDGE, "declares no position"),
    ("or chain gate without marker nudged",
     "false || " + GATE, NUDGE, "declares no position"),
    ("marked subshell gate allowed",
     "(" + GATE + "  # gate: checkpoint\n)", ALLOW, ""),
    ("marked brace gate allowed",
     "{ " + GATE + "  # gate: checkpoint\n}", ALLOW, ""),
    ("marked if body gate allowed",
     "if true; then " + GATE + "  # gate: checkpoint\nfi", ALLOW, ""),
    ("marked and chain gate allowed",
     "true && " + GATE + "  # gate: checkpoint", ALLOW, ""),
    ("marked gate then unmarked subshell nudged",
     GATE + "  # gate: checkpoint\n(" + GATE + ")", NUDGE, "declares no position"),
    ("unmarked gate then marked checkpoint nudged",
     GATE + "; " + GATE + "  # gate: checkpoint", NUDGE, "declares no position"),
    ("exempt gate then unmarked gate nudged",
     GATE + " -StaticOnly; " + GATE, NUDGE, "declares no position"),
    ("unmarked gate then exempt gate nudged",
     GATE + "; " + GATE + " -StaticOnly", NUDGE, "declares no position"),
    ("narrowed flag is denied", GATE + " -Smoke  # gate: final", DENY, "verify.ps1"),
]


def stale_state_case():
    repo = tempfile.mkdtemp(prefix="stale_gate_state_")
    directory = os.path.join(repo, ".claude", "scratch", "gate_positions")
    os.makedirs(directory, exist_ok=True)
    stale = os.path.join(directory, "old-session.json")
    with open(stale, "w", encoding="utf-8") as fh:
        fh.write("{}")
    old = time.time() - 8 * 24 * 3600
    os.utime(stale, (old, old))
    got = run(VERIFY + " -Scope NPCs", "sweep-session", repo)
    return got, not os.path.exists(stale)


def write_gate_last(repo, verdict, age_sec=0, session='red001'):
    """Plant a gate_last.md carrying `verdict`, aged `age_sec` seconds into the past."""
    d = os.path.join(repo, ".claude", "scratch", "test_runs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "gate_last.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('# Regression Gate detail\n\nVERDICT=%s (exit 1)\n' % verdict)
        if session is not None:
            fh.write('SESSION=%s\n' % session)
    stamp = time.time() - age_sec
    os.utime(p, (stamp, stamp))
    return p


def post_red_cases(run_one):
    """The post-FAIL reflex check. Each case owns its repo — the signal is on disk."""
    out = []

    # 1. an open red, nothing narrower since -> the full gate is denied
    repo = tempfile.mkdtemp(prefix="postred_a_")
    write_gate_last(repo, "FAIL")
    out.append(("open red + no narrow run -> full gate denied",
                run_one(GATE + "  # gate: final", "red001", repo), DENY, "nothing narrower has run"))

    # 2. -RetryOnly is the sanctioned instrument, never blocked
    out.append(("-RetryOnly is exempt while a red is open",
                run_one(GATE + " -RetryOnly  # gate: final", "red002", repo), ALLOW, ""))

    # 3. a narrow run since the red clears the block
    repo2 = tempfile.mkdtemp(prefix="postred_b_")
    write_gate_last(repo2, "FAIL", age_sec=60, session='red003')
    run_one(VERIFY + " -Scope NPCs", "red003", repo2)          # the narrow run
    out.append(("narrow run since the red -> full gate allowed",
                run_one(GATE + "  # gate: final", "red003", repo2), ALLOW, ""))

    # 4. a peer's red advises rather than inheriting the peer's narrow-run debt
    out.append(("another session's red advises",
                run_one(GATE + "  # gate: final", "red004", repo2), NUDGE, 'another session'))
    write_gate_last(repo2, 'FAIL', session='red004')
    out.append(("a peer's narrow run does not clear this session's red",
                run_one(GATE + "  # gate: final", "red004", repo2), DENY, 'nothing narrower'))

    # 5. a green last verdict blocks nothing
    repo3 = tempfile.mkdtemp(prefix="postred_c_")
    write_gate_last(repo3, "PASS")
    out.append(("a green last verdict blocks nothing",
                run_one(GATE + "  # gate: final", "red005", repo3), ALLOW, ""))

    # 6. a stale red is history, not this drive's open failure
    repo4 = tempfile.mkdtemp(prefix="postred_d_")
    write_gate_last(repo4, "FAIL", age_sec=13 * 3600)
    out.append(("a red older than the staleness bound is history",
                run_one(GATE + "  # gate: final", "red006", repo4), ALLOW, ""))

    # 7. no gate_last.md at all -> nothing to key on
    repo5 = tempfile.mkdtemp(prefix="postred_e_")
    out.append(("no prior verdict on disk -> allowed",
                run_one(GATE + "  # gate: final", "red007", repo5), ALLOW, ""))

    return out


def completed_peer_cases():
    """A completed narrow run must cover the red on the current content, not its session id."""
    import hashlib
    from datetime import datetime, timezone
    from pathlib import Path
    repo = Path(tempfile.mkdtemp(prefix='postred_record_'))
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, env=env)
    git('init', '-q', '-b', 'main')
    (repo / 'input.txt').write_text('tested tree\n')
    git('add', 'input.txt')
    git('-c', 'user.name=proof', '-c', 'user.email=proof@local', 'commit', '-qm', 'init')
    peer = repo / 'peer'
    git('worktree', 'add', '-q', '--detach', str(peer))
    # Ignore the linked fixture checkout, as the real .claude/worktrees directory is ignored.
    (repo / '.git/info/exclude').write_text('peer/\n.claude/\n')
    name = ('App.Tests.Integration.ScenarioSuite.SomeScenarioTests.'
            'Scenario_PlacesEverySubject_InsideItsOwnSight.'
            'Scenario_PlacesEverySubject_InsideItsOwnSight:2 ("case_two")')
    gate = Path(write_gate_last(str(repo), 'FAIL', age_sec=60, session='record-owner'))
    # Shape from gate_last_20261017-052956-13996.md.
    with gate.open('a', encoding='utf-8') as fh:
        fh.write('\n## Integration — FAILING\n\nfailed=1 — genuine test failure, not a count-drift signal\n'
                 '### Integration failing tests\n- ' + name + ' :: sight assertion\n')
    red_at = time.time() - 60
    os.utime(gate, (red_at, red_at))
    script = Path(HOOK).resolve().parents[1] / 'scripts/TreeDigest.ps1'
    ps = ". '%s'; (Get-TreeDigest -Repo '%s').digest" % (script, repo)
    result = subprocess.run(['pwsh', '-NoProfile', '-Command', ps], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    digest = result.stdout.strip()
    # TRX fields cut from TestResults/sandbox_g6.trx; keep its parameterized identifier intact.
    import xml.sax.saxutils as xml_escape
    trx = peer / '.claude/scratch/test_runs/narrow.trx'
    trx.parent.mkdir(parents=True)
    trx.write_text('<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
                   '<Results><UnitTestResult testName=' + xml_escape.quoteattr(name) + ' outcome="Passed" /></Results>'
                   '<ResultSummary outcome="Completed"><Counters total="1" executed="1" passed="1" failed="0" />'
                   '</ResultSummary></TestRun>', encoding='utf-8')
    stamp = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    record = dict(filter='FullyQualifiedName~Tests.Integration.ScenarioSuite.SomeScenarioTests', elapsedSec=1,
                  treeDigest=digest, startedAt=stamp(time.time() - 20), finishedAt=stamp(time.time() - 10),
                  trxPath=str(trx), trxSha256=hashlib.sha256(trx.read_bytes()).hexdigest())
    path = trx.with_suffix('.green')
    writer = repo / 'producer.ps1'
    writer.write_text('''param($Source, $DigestScript, $Repo, $Record, $Trx, $Digest, $Started)
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in @('Get-SuiteTreeDigest', 'Save-SuiteGreen')) {
    $fn = $ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    if (-not $fn) { throw "missing producer $name" }
    Invoke-Expression $fn.Extent.Text
}
Save-SuiteGreen -Repo $Repo -Path $Record -Trx $Trx -TreeDigest $Digest -DigestScript $DigestScript -StartedAt ([datetime]$Started) -Filter 'FullyQualifiedName~Tests.Integration.ScenarioSuite.SomeScenarioTests' -ElapsedSec 1
''', encoding='utf-8')
    # This script is proof machinery, not a build input in the planted checkout.
    with (repo / '.git/info/exclude').open('a') as fh:
        fh.write('producer.ps1\n')
    source = script.parent / 'run_test_suite.ps1'
    producer = subprocess.run(['pwsh', '-NoProfile', '-File', str(writer), str(source), str(script), str(repo),
                               str(path), str(trx), digest, stamp(time.time() - 20)],
                              capture_output=True, text=True, env=env)
    assert producer.returncode == 0, producer.stderr
    produced = json.loads(path.read_text(encoding='utf-8-sig'))
    assert produced['treeDigest'] == digest and produced['trxSha256'] == record['trxSha256'], produced
    assert produced['filter'] == record['filter'], produced
    def observed(value):
        path.write_text(json.dumps(value), encoding='utf-8')
        return run(GATE + ' # gate: final', 'record-owner', str(repo))
    raw_trx = trx.read_bytes()
    out = [('runner-produced record covers red', observed(produced), ALLOW, ''),
           ('completed same-tree peer narrow run covers red', observed(record), ALLOW, '')]
    for label, mutation in (
        ('foreign tree', dict(treeDigest='0' * 40)),
        ('pre-red run', dict(startedAt=stamp(red_at - 20), finishedAt=stamp(red_at - 10))),
        ('unfinished run', dict(finishedAt='')),
        ('missing filter', dict(filter='')),
        ('overwritten TRX', dict(trxSha256='0' * 64)),
    ):
        out.append((label + ' does not clear red', observed(dict(record, **mutation)), DENY, 'nothing narrower'))
    trx.write_text(trx.read_text().replace(name.split('.')[0], 'Unrelated'), encoding='utf-8')
    unrelated = dict(record, trxSha256=hashlib.sha256(trx.read_bytes()).hexdigest())
    out.append(('unrelated peer pass does not clear red', observed(unrelated), DENY, 'nothing narrower'))
    for label, changed in (
        ('zero executed', raw_trx.replace(b'executed="1"', b'executed="0"')),
        ('non-green test', raw_trx.replace(b'outcome="Passed"', b'outcome="Failed"')),
    ):
        trx.write_bytes(changed)
        out.append((label + ' does not clear red',
                    observed(dict(record, trxSha256=hashlib.sha256(changed).hexdigest())), DENY, 'nothing narrower'))
    trx.write_bytes(raw_trx)
    detail = gate.read_text(encoding='utf-8')
    gate.write_text(detail.replace('failed=1', 'failed=2'), encoding='utf-8')
    os.utime(gate, (red_at, red_at))
    out.append(('incomplete red failure listing stays denied', observed(record), DENY, 'nothing narrower'))
    gate.write_text(detail, encoding='utf-8')
    os.utime(gate, (red_at, red_at))
    # Even a once-valid record is stale after an input edit in this checkout.
    (repo / 'input.txt').write_text('changed after verification\n')
    out.append(('edited checkout does not inherit peer proof', observed(record), DENY, 'nothing narrower'))
    return out


def main():
    repo = tempfile.mkdtemp(prefix="gatecadence_")
    failures = []

    for i, (label, command, expected, needle) in enumerate(CASES):
        got, reason = run(command, "iso%03d" % i, repo)
        ok = got == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    retired_status, retired_reason = run(GATE + " -Smoke  # gate: final", "retired-cite", repo)
    retired_ok = (retired_status == DENY and "regression_gate §Invocation" in retired_reason
                  and "verify.ps1" in retired_reason)
    print("%-4s retired width denial cites regression_gate invocation and keeps verify step"
          % ("ok" if retired_ok else "FAIL"))
    if not retired_ok:
        failures.append("retired width denial lacks invocation cite or verify.ps1 next step")

    print("  -- sequenced (one session) --")
    for label, command, expected, needle in SEQUENCE:
        got, reason = run(command, "seq001", repo)
        ok = got == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    print("  -- compound invocations --")
    for i, (label, command, expected, needle) in enumerate(COMPOUND):
        got, reason = run(command, "compound%03d" % i, repo)
        ok = got == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    stale_result, stale_removed = stale_state_case()
    stale_ok = stale_result[0] == ALLOW and stale_removed
    print("%-4s planted stale state swept on narrow verification"
          % ("ok" if stale_ok else "FAIL"))
    if not stale_ok:
        failures.append("stale state file was not swept: %r" % (stale_result,))

    print("  -- post-red reflex check --")
    post = post_red_cases(lambda cmd, sid, rp: run(cmd, sid, rp)) + completed_peer_cases()
    for label, (got, reason), expected, needle in post:
        ok = got == expected and (not needle or needle in reason)
        if expected == DENY and 'nothing narrower' in needle:
            ok = ok and ('Rerun the failing `-Filter`' in reason
                         and 'rerun the failing `-Filter`, not the scope' not in reason
                         and 'change_control §Post-failure re-verification' in reason
                         and 'run_test_suite.ps1' in reason and 'Say which' not in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    total = len(CASES) + len(SEQUENCE) + len(COMPOUND) + len(post) + 2
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


def test_existing_cases():
    assert main() == 0


def test_old_format_and_unknown_session_advise():
    with tempfile.TemporaryDirectory(prefix='red_owner_') as repo:
        for session in (None, ''):
            write_gate_last(repo, 'FAIL', session=session)
            status, reason = run(GATE + ' # gate: final', 'this-session', repo)
            assert status == NUDGE, reason
            assert 'owner' in reason, reason
        write_gate_last(repo, 'FAIL', session='peer')
        status, reason = run(GATE + ' # gate: final', None, repo)
        assert status == NUDGE, reason


def test_real_complete_gate_writes_session(tmp_path):
    from pathlib import Path
    gate = Path(HOOK).resolve().parents[1] / 'scripts/regression_gate.ps1'
    script = tmp_path / 'writer.ps1'
    script.write_text('''param($Source, $Log)
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$fn = $ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Complete-Gate'}, $true)
Invoke-Expression $fn.Extent.Text
function Exit-GateHeld {}
function Emit { param($Text) Write-Output $Text }
$logDir = $Log; $detailPath = Join-Path $Log 'detail.md'
$script:Detail = @(); $suiteResults = @{}; $runId = 'proof'; $queueDir = Join-Path $Log 'queue'
Complete-Gate 'FAIL' 1
''', encoding='utf-8')
    for key in ('CLAUDE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID'):
        env = {k: v for k, v in os.environ.items() if k not in ('CLAUDE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID')}
        env[key] = 'producer-session'
        result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script), str(gate), str(tmp_path)],
                                capture_output=True, text=True, env=env, timeout=90)
        assert result.returncode == 1, result.stderr
        body = (tmp_path / 'gate_last.md').read_text(encoding='utf-8-sig')
        assert 'SESSION=producer-session' in body[:400], body[:400]
        assert 'VERDICT=FAIL' in body[:400]


if __name__ == "__main__":
    from pathlib import Path
    test_old_format_and_unknown_session_advise()
    with tempfile.TemporaryDirectory(prefix='gate_writer_') as tmp:
        test_real_complete_gate_writes_session(Path(tmp))
    sys.exit(main())
