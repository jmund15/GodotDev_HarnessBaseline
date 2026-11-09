"""Re-runnable proof for hooks/wide_test_guard.py and the .ps1 entry points that call it.

Planted: test classes, brief files, transcripts and a looping script live under a temp root; the delegate
facts (CLAUDE_CODE_SIDECAR, a `/subagents/` transcript) are set per case. The wiring cases send real
PreToolUse payloads through hooks/pre_bash_dispatch.py and read the emitted channel.

    python3 .claude/tests/test_wide_test_guard.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
HOOKS = os.path.join(CLAUDE, "hooks")
sys.path.insert(0, HOOKS)
import wide_test_guard as guard  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wide_guard_")
RUN = "pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Repo . -Filter "
guard._classes[guard.ROOT] = {"FooTests", "BarTests"}
guard._methods[guard.ROOT] = {"Spits_AfterAPursue"}

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append("%s %s" % (name, detail))


def write(name, text):
    path = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path.replace("\\", "/")


def sidecar_env(prompt=None):
    for key in ("CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_PROMPT_FILE", "HARNESS_WIDE_RUN_ADMITTED"):
        os.environ.pop(key, None)
    os.environ["CLAUDE_CODE_SIDECAR"] = "test-transport"
    if prompt:
        os.environ["CLAUDE_CODE_SIDECAR_PROMPT_FILE"] = prompt


def main_env():
    for key in ("CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_PROMPT_FILE", "HARNESS_WIDE_RUN_ADMITTED"):
        os.environ.pop(key, None)


def verdict(command, transcript="C:/x/session.jsonl", tool="Bash"):
    return guard.hook({"tool_name": tool, "transcript_path": transcript, "cwd": TMP,
                       "tool_input": {"command": command}})


def transcript_with(brief, name, extra=()):
    records = [{"parentUuid": None, "isSidechain": True, "type": "user",
                "message": {"role": "user", "content": brief}}]
    records += list(extra)
    return write(name, "\n".join(json.dumps(r) for r in records) + "\n")


def case_filters():
    sidecar_env()
    ns = verdict(RUN + '"FullyQualifiedName~Tests.Logic."')
    check("namespace refused", bool(ns) and "guards/author.md" in ns and "class filters" in ns, str(ns))
    check("class admitted", verdict(RUN + '"FullyQualifiedName~Tests.Logic.FooTests"') is None)
    check("method admitted", verdict(RUN + '"FullyQualifiedName~Tests.Logic.FooTests.Works"') is None)
    check("two classes admitted",
          verdict(RUN + '"FullyQualifiedName~Tests.A.FooTests|FullyQualifiedName~Tests.B.BarTests"') is None)
    check("bare known method admitted", verdict(RUN + '"FullyQualifiedName~Spits_AfterAPursue"') is None)
    check("bare unknown name refused", bool(verdict(RUN + '"FullyQualifiedName~Spits_Nothing"')))
    check("bare method plus namespace refused",
          bool(verdict(RUN + '"FullyQualifiedName~Spits_AfterAPursue|FullyQualifiedName~Tests.Logic."')))
    check("class plus namespace refused",
          bool(verdict(RUN + '"FullyQualifiedName~Tests.A.FooTests|FullyQualifiedName~Tests.Integration.Alpha."')))
    check("namespace without trailing dot refused", bool(verdict(RUN + '"FullyQualifiedName~Tests.Logic"')))
    check("bare Name~ refused", bool(verdict(RUN + '"Name~Works"')))
    check("tier refused", bool(verdict(RUN + '"FullyQualifiedName~Tests.Sanity"')))
    check("exclusion only narrows",
          bool(verdict(RUN + '"(FullyQualifiedName~Tests.Integration.Alpha.)&FullyQualifiedName!~FooTests"')))
    check("class with exclusion admitted",
          verdict(RUN + '"(FullyQualifiedName~Tests.Integration.FooTests)&FullyQualifiedName!~Slow"') is None)
    check("run-time filter refused", bool(verdict(RUN + '"FullyQualifiedName~Tests.Integration.$d."')))
    check("dotnet class admitted", verdict('dotnet test --filter "FullyQualifiedName~Tests.X.FooTests"') is None)
    check("dotnet unfiltered refused", bool(verdict("dotnet test")))
    check("dotnet namespace refused", bool(verdict('dotnet test --filter "FullyQualifiedName~Tests.Logic."')))
    check("verify scope refused", bool(verdict("pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope Alpha,Beta")))
    check("verify class filter admitted",
          verdict('pwsh -NoProfile -File .claude/scripts/verify.ps1 -Filter "FullyQualifiedName~Tests.X.FooTests"') is None)
    check("gate refused", bool(verdict("pwsh -NoProfile -File .claude/scripts/regression_gate.ps1")))
    check("gate -StaticOnly admitted",
          verdict("pwsh -NoProfile -File .claude/scripts/regression_gate.ps1 -StaticOnly") is None)
    check("PowerShell tool refused", bool(verdict(RUN + '"FullyQualifiedName~Tests.Logic."', tool="PowerShell")))
    both = verdict(RUN + '"FullyQualifiedName~Tests.Logic." && ' + RUN + '"FullyQualifiedName~Tests.Sanity"')
    check("every unmet run is named once", bool(both) and "Tests.Logic." in both and "Tests.Sanity" in both, str(both))


def case_lint_namespace():
    sidecar_env()
    with_lint = os.path.join(TMP, "lintwt")
    write("lintwt/Tests/Logic/Lint/SomeLintTests.cs", "public class SomeLintTests {}\n")
    os.makedirs(os.path.join(with_lint, ".claude"), exist_ok=True)

    def at(checkout, filter_text):
        return guard.hook({"tool_name": "Bash", "transcript_path": "C:/x/s.jsonl", "cwd": checkout,
                           "tool_input": {"command": RUN + '"%s"' % filter_text}})

    check("lint namespace admitted without a brief", at(with_lint, "FullyQualifiedName~Tests.Logic.Lint") is None)
    check("qualified lint namespace admitted",
          at(with_lint, "FullyQualifiedName~Game.Tests.Logic.Lint.") is None)
    check("Tests.Logic still refused", bool(at(with_lint, "FullyQualifiedName~Tests.Logic")))
    check("a sibling of the lint folder still refused", bool(at(with_lint, "FullyQualifiedName~Tests.Logic.Data")))
    check("lint namespace plus another namespace refused",
          bool(at(with_lint, "FullyQualifiedName~Tests.Logic.Lint|FullyQualifiedName~Tests.Logic.Data")))
    saved = list(guard._roots)
    try:
        without = os.path.join(TMP, "nolintwt")
        write("nolintwt/Tests/Logic/FooTests.cs", "public class FooTests {}\n")
        guard._roots[:] = [without]
        check("a repo without the folder keeps today's behavior",
              bool(guard.filter_wide_reason("FullyQualifiedName~Tests.Logic.Lint")))
    finally:
        guard._roots[:] = saved


def case_delegate_worktree_classes():
    sidecar_env()
    checkout = os.path.dirname(write("wt/Tests/Logic/NewThingTests.cs", "public class NewThingTests {}\n"))
    checkout = os.path.dirname(os.path.dirname(checkout))
    os.makedirs(os.path.join(checkout, ".claude"), exist_ok=True)
    run = RUN + '"FullyQualifiedName~Tests.Logic.NewThingTests"'
    check("class unknown to the hook's checkout is refused", bool(verdict(run)))
    check("class written in the cwd's checkout admitted",
          guard.hook({"tool_name": "Bash", "transcript_path": "C:/x/s.jsonl", "cwd": checkout,
                      "tool_input": {"command": run}}) is None)
    check("class written in a cd target admitted", verdict("cd %s && %s" % (checkout, run)) is None)
    check("class written in a -Repo target admitted", verdict(RUN.replace("-Repo .", "-Repo " + checkout)
                                                               + '"FullyQualifiedName~Tests.Logic.NewThingTests"') is None)
    check("roots reset between calls", bool(verdict(run)))


def case_mentions_are_not_runs():
    sidecar_env()
    lib = write("TwinLib.ps1", "function Get-Thing { & pwsh -File run_test_suite.ps1 }\n# dotnet test\n")
    check("dot-sourcing a library is not a run",
          verdict('pwsh -NoProfile -Command ". %s; Get-Thing"' % lib) is None)
    helped = write("Launcher.ps1", "<#\n.SYNOPSIS\n  Builds the `dotnet test` argv.\n#>\nparam([string]$Path)\n"
                                   "& godot --path $Path --import\n")
    check("a help block naming dotnet test is not a run", verdict("pwsh -NoProfile -File %s" % helped) is None)
    real = write("Runner.ps1", "<#\n.SYNOPSIS\n  Runs tests.\n#>\ndotnet test\n")
    check("dotnet test after a help block is still a run", bool(verdict("pwsh -NoProfile -File %s" % real)))
    batched = "pwsh -NoProfile -File .claude/scripts/run_integration_batched.ps1"
    check("the integration batcher is a wide run", bool(verdict(batched)))
    check("the batcher's -Only domains are a wide run", bool(verdict(batched + " -Only Alpha")))
    check("the batcher's dry run is not a run", verdict(batched + " -DryRun") is None)
    import shlex
    source = "from pathlib import Path; p=Path('.claude/scripts/run_test_suite.ps1'); print(p.read_text())"
    check('python inline source read is not a run', verdict('python3 -c ' + shlex.quote(source)) is None)
    source = "import subprocess; subprocess.run(['pwsh','-File','run_test_suite.ps1'])"
    check('python inline subprocess still refused', bool(verdict('python3 -c ' + shlex.quote(source))))
    source = "reader.run(['pwsh','-File','run_test_suite.ps1'])"
    check('unrelated Python run method is not a process', verdict('python3 -c ' + shlex.quote(source)) is None)
    source = "from subprocess import run as launch; launch(['pwsh','-File','run_test_suite.ps1'])"
    check('imported Python process alias still refused', bool(verdict('python3 -c ' + shlex.quote(source))))
    reader = write('read_source.py', "from pathlib import Path\nprint(Path('run_test_suite.ps1').read_text())\n")
    check('python file source read is not a run', verdict('python3 ' + reader) is None)
    for command in ("grep -n Filter .claude/scripts/run_test_suite.ps1", "cat .claude/scripts/verify.ps1",
                    "git show HEAD:.claude/scripts/regression_gate.ps1 | head -5",
                    "sed -i s/a/b/ .claude/commands/regression_gate.md",
                    "wc -l .claude/scripts/run_test_suite.ps1", "ls Tests/Logic"):
        check("mention admitted: " + command, verdict(command) is None)


def case_admit_variable():
    narrow = '"FullyQualifiedName~Tests.FooTests"'
    sidecar_env()
    check("a delegate's class filter admitted", verdict(RUN + narrow) is None)
    check("a delegate's inline admit variable refuses", bool(verdict("HARNESS_WIDE_RUN_ADMITTED=1 " + RUN + narrow)))
    check("a delegate's exported admit variable refuses",
          bool(verdict("export HARNESS_WIDE_RUN_ADMITTED=1; bash .claude/scratch/loop.sh")))
    check("a delegate's PowerShell admit variable refuses",
          bool(verdict("$env:HARNESS_WIDE_RUN_ADMITTED = '1'; " + RUN + narrow, tool="PowerShell")))
    check("a delegate reading the variable's name admitted", verdict("grep -n HARNESS_WIDE_RUN_ADMITTED .claude/scripts/WideRunGuard.ps1") is None)
    main_env()
    check("main session's admit variable admitted", verdict("HARNESS_WIDE_RUN_ADMITTED=1 " + RUN + narrow) is None)


def case_main_session():
    main_env()
    check("main session admitted", verdict(RUN + '"FullyQualifiedName~Tests.Logic."') is None)
    check("main session admitted for the gate", verdict("pwsh -File .claude/scripts/regression_gate.ps1") is None)


def case_sidecar_brief():
    wide = '"FullyQualifiedName~Tests.Integration.Alpha."'
    prompt = write("brief.md", "Run exactly this: -Filter " + wide + " once.")
    sidecar_env(prompt)
    check("brief names filter: admitted", verdict(RUN + wide) is None)
    check("brief does not name another filter",
          bool(verdict(RUN + '"FullyQualifiedName~Tests.Integration.Beta."')))
    amended = write("amend_brief.md", "Run the tests.")
    write("amend_brief.md.amend.md", "Also run " + wide)
    sidecar_env(amended)
    check(".amend.md names filter: admitted", verdict(RUN + wide) is None)
    sidecar_env(write("scope_brief.md", "Bisect with verify.ps1 -Scope Alpha, Beta"))
    check("brief names verify and each scope: admitted",
          verdict("pwsh -File .claude/scripts/verify.ps1 -Scope Alpha,Beta") is None)
    check("brief missing one scope: refused", bool(verdict("pwsh -File .claude/scripts/verify.ps1 -Scope Alpha,Gamma")))
    sidecar_env(write("gate_brief.md", "Close with the regression_gate."))
    check("brief names the gate: admitted", verdict("pwsh -File .claude/scripts/regression_gate.ps1") is None)
    sidecar_env(write("loop_brief.md", "Run " + 'FullyQualifiedName~Tests.Integration.$d.'))
    check("run-time filter is never named", bool(verdict(RUN + '"FullyQualifiedName~Tests.Integration.$d."')))


def case_wrapped_script():
    sidecar_env()
    script = write("run_domains.sh", "#!/bin/bash\nfor d in Alpha Beta Gamma; do\n  pwsh -NoProfile -File "
                   ".claude/scripts/run_test_suite.ps1 -Repo . -Filter \"FullyQualifiedName~Tests.Integration.$d.\"\ndone\n")
    denied = verdict("bash " + script)
    check("looping script refused", bool(denied) and "Tests.Integration." in denied, str(denied))
    check("script run directly refused", bool(verdict(script)))
    check("script under bash -c refused", bool(verdict('bash -c "bash %s"' % script)))
    nested = write("outer.sh", "bash %s\n" % script)
    check("script that runs a script refused", bool(verdict("bash " + nested)))
    quiet = write("quiet.sh", "# run_test_suite.ps1 -Filter x\necho done\n")
    check("commented mention admitted", verdict("bash " + quiet) is None)
    narrow = write("narrow.sh", "pwsh -File .claude/scripts/run_test_suite.ps1 -Filter "
                   "\"FullyQualifiedName~Tests.Logic.FooTests\"\n")
    check("script with a class filter admitted", verdict("bash " + narrow) is None)
    py = write("loop.py", "import subprocess\nsubprocess.run(['pwsh','-File','run_test_suite.ps1','-Filter','Tests.Logic.'])\n")
    check("python script refused", bool(verdict("python3 " + py)))


def case_subagents():
    main_env()
    wide = '"FullyQualifiedName~Tests.Integration.Alpha."'
    for kind, folder in (("agent-tool", "s/subagents/"), ("workflow", "s/subagents/workflows/run1/")):
        named = transcript_with("Run -Filter " + wide, folder + "agent-a1.jsonl")
        check(kind + " brief names filter: admitted", verdict(RUN + wide, named) is None)
        unnamed = transcript_with("Fix the bug.", folder + "agent-a2.jsonl")
        check(kind + " brief silent: refused", bool(verdict(RUN + wide, unnamed)))
    own_words = transcript_with("Fix the bug.", "s/subagents/agent-a3.jsonl", extra=[
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Run " + wide}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "-Filter " + wide}]}}])
    check("agent-written and tool-result text never admit", bool(verdict(RUN + wide, own_words)))
    follow = transcript_with("Fix the bug.", "s/subagents/agent-a4.jsonl", extra=[
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Also run " + wide}]}}])
    check("dispatcher follow-up message admits", verdict(RUN + wide, follow) is None)
    check("unreadable transcript refuses", bool(verdict(RUN + wide, TMP + "/missing/subagents/agent-zz.jsonl")))


def run_py(args, env_extra, payload=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_SIDECAR")}
    env.pop("HARNESS_WIDE_RUN_ADMITTED", None)
    env.update(env_extra)
    return subprocess.run([sys.executable] + args, input=payload, capture_output=True, text=True, timeout=120, env=env)


def case_entry_cli():
    wide = "FullyQualifiedName~Tests.Logic."
    base = [os.path.join(HOOKS, "wide_test_guard.py"), "--entry", "run_test_suite.ps1", "--filter", wide]
    r = run_py(base, {"CLAUDE_CODE_SIDECAR": "t"})
    check("entry refuses a sidecar's wide filter", r.returncode == 2 and "guards/author.md" in r.stderr, r.stderr)
    prompt = write("entry_brief.md", "Run " + wide)
    r = run_py(base, {"CLAUDE_CODE_SIDECAR": "t", "CLAUDE_CODE_SIDECAR_PROMPT_FILE": prompt})
    check("entry admits a named filter", r.returncode == 0, r.stderr)
    check("entry admits the main session", run_py(base, {}).returncode == 0)
    check("entry honors the child admission",
          run_py(base, {"CLAUDE_CODE_SIDECAR": "t", "HARNESS_WIDE_RUN_ADMITTED": "1"}).returncode == 0)
    r = run_py([base[0], "--entry", "verify.ps1", "--scope", "Alpha,Beta"], {"CLAUDE_CODE_SIDECAR": "t"})
    check("entry refuses verify -Scope", r.returncode == 2, r.stderr)
    r = run_py([base[0], "--entry", "regression_gate.ps1", "--flags", "StaticOnly"], {"CLAUDE_CODE_SIDECAR": "t"})
    check("entry admits the gate's static-only run", r.returncode == 0, r.stderr)


def case_dispatcher_wiring():
    def through(command, env_extra):
        payload = json.dumps({"tool_name": "Bash", "session_id": "wide-guard-proof", "cwd": TMP,
                              "transcript_path": "C:/x/session.jsonl", "tool_input": {"command": command}})
        r = run_py([os.path.join(HOOKS, "pre_bash_dispatch.py")], dict(env_extra, CLAUDE_PROJECT_DIR=TMP), payload)
        return r
    r = through(RUN + '"FullyQualifiedName~Tests.Logic."', {"CLAUDE_CODE_SIDECAR": "t"})
    check("dispatcher denies a sidecar's wide run", "Wide test run refused" in (r.stdout + r.stderr), r.stdout + r.stderr)
    r = through(RUN + '"FullyQualifiedName~Tests.Logic."', {})
    check("dispatcher admits the main session", "Wide test run refused" not in (r.stdout + r.stderr), r.stdout + r.stderr)


def case_ps1_entry_points():
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return
    scripts = os.path.join(CLAUDE, "scripts")
    env = {"CLAUDE_CODE_SIDECAR": "t"}
    for args, name in ((["run_test_suite.ps1", "-Repo", TMP, "-Filter", "FullyQualifiedName~Tests.Logic."], "run_test_suite"),
                       (["verify.ps1", "-Repo", TMP, "-Scope", "Alpha"], "verify -Scope"),
                       (["regression_gate.ps1"], "gate")):
        full = dict(os.environ, **env)
        full.pop("HARNESS_WIDE_RUN_ADMITTED", None)
        r = subprocess.run([pwsh, "-NoProfile", "-File", os.path.join(scripts, args[0])] + args[1:],
                           capture_output=True, text=True, timeout=120, env=full)
        check("ps1 entry %s refuses a sidecar's wide run" % name,
              r.returncode == 2 and "Wide test run refused" in (r.stdout + r.stderr), "%s %s" % (r.returncode, (r.stdout + r.stderr)[-300:]))


def case_printed_action():
    sidecar_env()
    printed = write("Printer.ps1", "param([switch]$SelfTest)\n"
                    "$action = \"pwsh .claude/scripts/run_test_suite.ps1 -Filter 'FullyQualifiedName~$test' - note\"\n"
                    "Write-Host \"Run: pwsh -File .claude/scripts/run_test_suite.ps1 -Filter 'FullyQualifiedName~$test'\"\n")
    check("a script printing a run text is not a run", verdict("pwsh -NoProfile -File %s -SelfTest" % printed) is None)
    real = write("Caller.ps1", "$action = \"note\"\npwsh .claude/scripts/run_test_suite.ps1 -Filter 'FullyQualifiedName~$test'\n")
    check("a script that runs a run text is still a run", bool(verdict("pwsh -NoProfile -File %s" % real)))
    literal = write("Literal.ps1", "pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter FullyQualifiedName~Tests.Integration\n")
    check("a script's literal wide run is still a run", bool(verdict("pwsh -NoProfile -File %s" % literal)))
    check("batch_diagnosis -SelfTest is not a run",
          verdict("pwsh -NoProfile -File .claude/scripts/batch_diagnosis.ps1 -SelfTest") is None)
    check("the adaptation proof is not a run",
          verdict("pwsh -NoProfile -File .claude/tests/test_run_integration_batched_adaptation.ps1") is None)
    launched = write("Launcher2.ps1", "Start-Process pwsh -ArgumentList '-File .claude/scripts/run_test_suite.ps1 "
                     "-Filter FullyQualifiedName~Tests.Integration'\n")
    check("a script launching a wide run through an argument string is still a run",
          bool(verdict("pwsh -NoProfile -File %s" % launched)))
    check("the real wide run is still refused",
          bool(verdict("pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter FullyQualifiedName~Tests.Integration")))


if __name__ == "__main__":
    try:
        for fn in (case_filters, case_lint_namespace, case_delegate_worktree_classes, case_mentions_are_not_runs, case_admit_variable,
                   case_main_session, case_sidecar_brief,
                   case_wrapped_script, case_subagents, case_entry_cli, case_dispatcher_wiring,
                   case_ps1_entry_points, case_printed_action):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failure, never an allow
                failures.append("CRASH %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    for line in failures:
        print("FAIL", line)
    print("FAILED %d" % len(failures) if failures else "OK")
    sys.exit(1 if failures else 0)
