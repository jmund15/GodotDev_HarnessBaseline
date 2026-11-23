"""Proof for hooks/sidecar_workdir_guard.py, the floor guard that keeps a worktree job out of the main checkout.

Each case calls the guard's main() in process (`_hook_call.call`) on a PreToolUse payload, armed with
(workdir, main checkout, spill dir[, brief]):
  deny    Write/Edit/NotebookEdit whose path is in the main checkout and outside the workdir: absolute, `..`
          climb from the workdir, a relative path on a payload cwd in the main checkout, a sibling worktree
  allow   a path in the workdir; the spill dir's report; a path outside the main checkout; a main-checkout job
          (workdir is the main root); a Read; a prefix-sibling (`<workdir>-x`)
  bash    a Bash command whose redirection, tee, cp/mv/install, sed -i/perl -i, git -C index write or python path
          literal lands in the main checkout is denied; reads, workdir/spill/temp writes, quoted mentions and
          unresolvable targets are allowed
  brief   the brief's `targets:` list widens what a job may write; the brief itself is never writable

The seat launcher's registration of this guard is not proved here: it belongs to the launcher's proof.

    python -m pytest .claude/tests/test_sidecar_workdir_guard.py -q
"""
import os
import sys
import tempfile
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_call import call  # noqa: E402

CLAUDE = Path(__file__).resolve().parents[1]
GUARD = CLAUDE / "hooks" / "sidecar_workdir_guard.py"


def _guard(workdir, main, spill, payload, extra=()):
    argv = [str(workdir), str(main), str(spill), *map(str, extra)]
    code, _out, err, _hso = call(GUARD, payload, argv=argv)
    return code, err


def run(workdir, main, spill, tool, path, cwd=None, field="file_path", prompt=None):
    payload = json.dumps({"cwd": str(cwd or workdir), "hook_event_name": "PreToolUse", "tool_name": tool,
                          "tool_input": {field: str(path), "content": "x"}})
    return _guard(workdir, main, spill, payload, [prompt] if prompt else ())


def run_bash(workdir, main, spill, command, cwd=None, prompt=None):
    payload = json.dumps({"cwd": str(cwd or workdir), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": command}})
    return _guard(workdir, main, spill, payload, [prompt] if prompt else ())


def test_guard_decisions():
    fails = []

    def ck(name, ok, detail=""):
        if not ok:
            fails.append(f"{name}: {str(detail)[:300]}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        main_root = tmp / "repo"
        work = main_root / ".claude" / "worktrees" / "w1"
        sibling = main_root / ".claude" / "worktrees" / "w2"
        spill = main_root / ".claude" / "scratch" / "drive" / "out"
        outside = tmp / "elsewhere"
        for d in (work, sibling, spill, outside, Path(str(work) + "-x")):
            d.mkdir(parents=True)
        a = (work, main_root, spill)

        def case(name, want, tool, path, **kw):
            rc, err = run(*a, tool, path, **kw)
            ck(name, rc == want and (want == 0 or work.as_posix() in err.replace(chr(92), "/")), (rc, err))

        case("deny Write to the main tests dir", 2, "Write", main_root / ".claude" / "tests" / "x.py")
        case("deny Edit to a main tracked file", 2, "Edit", main_root / ".claude" / "tools" / "eval_findings.py")
        case("deny NotebookEdit in the main checkout", 2, "NotebookEdit", main_root / "n.ipynb", field="notebook_path")
        case("deny a `..` climb from the workdir", 2, "Write", work / ".." / ".." / "tests" / "x.py")
        case("deny a relative path on a main-checkout cwd", 2, "Write", "a.py", cwd=main_root)
        case("deny a sibling worktree", 2, "Write", sibling / "a.py")
        case("deny a prefix-sibling-free main root file", 2, "Write", main_root / "README.md")
        case("allow a workdir file", 0, "Write", work / ".claude" / "tests" / "x.py")
        case("allow a relative workdir path", 0, "Edit", "a.py")
        case("allow the spill dir report", 0, "Write", spill / "lane.md")
        case("allow a path outside the main checkout", 0, "Write", outside / "a.txt")
        case("allow `<workdir>-x` outside the main checkout rule", 0, "Write", outside / "b.txt")
        rc, err = run(main_root, main_root, spill, "Write", main_root / "a.py")
        ck("allow a main-checkout job (workdir is the main root)", rc == 0, (rc, err))
        rc, err = run(work, main_root, "", "Write", work / "a.py")
        ck("allow with no spill dir", rc == 0, (rc, err))
        rc, err = run(work, main_root, spill, "Read", main_root / "a.py")
        ck("allow a Read of the main checkout", rc == 0, (rc, err))
        rc, err = _guard(work, main_root, spill, "not json")
        ck("deny an unparseable payload (floor guard fails closed)", rc == 2, (rc, err))
        rc, err = run(work, main_root, spill, "Write", main_root / "a.py", field="other")
        ck("allow a payload with no path field", rc == 0, (rc, err))

        M, W, S, O = main_root.as_posix(), work.as_posix(), spill.as_posix(), outside.as_posix()

        def bash(name, want, command, cwd=None):
            rc, err = run_bash(*a, command, cwd=cwd)
            ck(name, rc == want and (want == 0 or W in err.replace(chr(92), "/")), (rc, err, command))

        bash("deny a redirect into main", 2, "echo x > %s/.claude/tests/a.py" % M)
        bash("deny an append into main", 2, "echo x >> %s/a.py" % M)
        bash("deny a fd redirect into main", 2, "cat a 1> %s/a.py" % M)
        bash("deny a quoted redirect target in main", 2, 'echo x > "%s/a b.py"' % M)
        bash("deny tee into main", 2, "echo x | tee %s/a.py" % M)
        bash("deny tee -a into main", 2, "echo x | tee -a %s/a.py" % M)
        bash("deny cp into main", 2, "cp a.py %s/.claude/tests/a.py" % M)
        bash("deny cp -r flags into main dir", 2, "cp -r a %s/b" % M)
        bash("deny mv into main", 2, "mv a.py %s/a.py" % M)
        bash("deny install into main", 2, "install -m644 a.py %s/a.py" % M)
        bash("deny cp -t main", 2, "cp -t %s/d a.py" % M)
        bash("deny sed -i on a main file", 2, "sed -i 's/a/b/' %s/a.py" % M)
        bash("deny perl -pi on a main file", 2, "perl -pi -e 's/a/b/' %s/a.py" % M)
        bash("deny dd of= into main", 2, "dd if=a.py of=%s/a.py" % M)
        bash("deny cp --target-directory= main", 2, "cp --target-directory=%s/d a.py" % M)
        bash("deny node writeFileSync into main", 2, "node -e \"require('fs').writeFileSync('%s/a.py','x')\"" % M)
        bash("deny pwsh Set-Content into main from Bash", 2, "pwsh -NoProfile -Command \"Set-Content %s/a.py 'x'\"" % M)
        bash("allow dd of= in the workdir", 0, "dd if=%s/a.py of=%s/a.py" % (M, W))
        bash("deny git -C main add", 2, "git -C %s add a.py" % M)
        bash("deny git -C main checkout", 2, "git -C %s checkout -- a.py" % M)
        bash("deny git -C main commit", 2, "git -C %s commit -m x" % M)
        bash("deny python -c writing a main path", 2, "python3 -c \"open('%s/a.py','w').write('x')\"" % M)
        bash("deny python Path write_text into main", 2,
             "python3 -c \"from pathlib import Path; Path('%s/a.py').write_text('x')\"" % M)
        bash("deny python shutil.copy into main", 2, "python3 -c \"import shutil; shutil.copy('a.py','%s/a.py')\"" % M)
        bash("deny python os.remove of a main file", 2, "python3 -c \"import os; os.remove('%s/a.py')\"" % M)
        bash("deny python open append keyword into main", 2, "python3 -c \"open('%s/a.py', mode='a')\"" % M)
        bash("deny a python heredoc writing a main path", 2,
             "python3 - <<'EOF'\nfrom pathlib import Path\nPath('%s/a.py').write_text('x')\nEOF" % M)
        bash("deny a write after cd into main", 2, "cd %s && echo x > a.py" % M)
        bash("deny a relative redirect on a main cwd", 2, "echo x > a.py", cwd=main_root)
        bash("deny a write in the second segment", 2, "cd %s && echo y > a && cp a %s/b" % (W, M))
        bash("allow a read of main", 0, "cat %s/a.py" % M)
        bash("allow grep of main into the workdir", 0, "grep x %s/a.py > %s/o.txt" % (M, W))
        bash("allow a redirect in the workdir", 0, "echo x > %s/a.py" % W)
        bash("allow a relative redirect on the workdir cwd", 0, "echo x > a.py")
        bash("allow a redirect to the spill dir", 0, "echo x > %s/lane.md" % S)
        bash("allow a redirect to a temp dir", 0, "echo x > %s/a.txt" % O)
        bash("allow cp from main into the workdir", 0, "cp %s/a.py %s/a.py" % (M, W))
        bash("allow cp -t workdir from main", 0, "cp -t %s %s/a.py" % (W, M))
        bash("allow sed -i in the workdir", 0, "sed -i 's/a/b/' %s/a.py" % W)
        bash("allow git -C main status", 0, "git -C %s status --short" % M)
        bash("allow git -C main log", 0, "git -C %s log -1" % M)
        bash("allow git -C workdir add", 0, "git -C %s add a.py" % W)
        bash("allow cd main then a read", 0, "cd %s && git status && cat a.py" % M)
        bash("allow cd main then back to workdir write", 0, "cd %s && cat a && cd %s && echo x > a" % (M, W))
        bash("allow /dev/null and fd dups", 0, "cat %s/a.py > /dev/null 2>&1" % M)
        bash("allow an unresolvable variable target", 0, "echo x > $TARGET/a.py")
        bash("allow a command-substitution target", 0, "echo x > $(mktemp)")
        bash("allow a quoted mention of a main redirect", 0, 'echo "x > %s/a.py"' % M)
        bash("allow a heredoc body mentioning a main redirect", 0, "cat > %s/n.txt <<'EOF'\necho x > %s/a.py\nEOF" % (W, M))
        bash("allow a python read of main", 0, "python3 -c \"print(open('%s/a.py').read())\"" % M)
        bash("allow a python write to the workdir", 0, "python3 -c \"open('%s/a.py','w').write('x')\"" % W)
        bash("allow python reads of two main literals beside a workdir write", 0,
             "python3 -c \"print(open('%s/a.py').read()); print(open('%s/b.py').read()); open('%s/o.txt','w').write('x')\"" % (M, M, W))
        bash("deny python open of a name bound to a main literal", 2,
             "python3 -c \"P = '%s/a.py'; open(P, 'w').write('x')\"" % M)
        bash("deny python write_text on a Path bound to a main literal", 2,
             "python3 -c \"from pathlib import Path; p = Path('%s/a.py'); p.write_text('x')\"" % M)
        bash("deny python Path open for write into main", 2,
             "python3 -c \"from pathlib import Path; Path('%s/a.py').open('w').write('x')\"" % M)
        bash("deny python Path unlink of a main file", 2,
             "python3 -c \"from pathlib import Path; Path('%s/a.py').unlink()\"" % M)
        bash("allow python read of a name bound to a main literal", 0,
             "python3 -c \"P = '%s/a.py'; print(open(P).read())\"" % M)
        bash("allow python shutil.copy from main into the workdir", 0,
             "python3 -c \"import shutil; shutil.copy('%s/a.py','%s/b.py')\"" % (M, W))
        bash("deny a write through the second binding of a ; chain", 2,
             "python3 -c \"import os; A = '%s/x.py'; B = '%s/a.py'; open(B, 'w').write('x')\"" % (W, M))
        bash("allow a write through the workdir binding of a ; chain", 0,
             "python3 -c \"import os; A = '%s/x.py'; B = '%s/a.py'; open(A, 'w').write('x')\"" % (W, M))
        bash("deny python os.remove of a name bound to a main literal", 2,
             "python3 -c \"import os; P = '%s/a.py'; os.remove(P)\"" % M)
        bash("deny python Path rename of a main source", 2,
             "python3 -c \"from pathlib import Path; Path('%s/a.py').rename('%s/b.py')\"" % (M, W))
        bash("deny python os.rmdir of a main path", 2, "python3 -c \"import os; os.rmdir('%s/d')\"" % M)
        bash("deny python Path rmdir of a bound main path", 2,
             "python3 -c \"from pathlib import Path; p = Path('%s/d'); p.rmdir()\"" % M)
        bash("allow python os.remove inside the workdir", 0, "python3 -c \"import os; os.remove('%s/a.py')\"" % W)
        bash("allow python os.remove of a workdir name beside a main binding", 0,
             "python3 -c \"import os; Q = '%s/a.py'; P = '%s/a.py'; os.remove(P)\"" % (M, W))
        bash("allow python read of a bound main path beside a delete in the workdir", 0,
             "python3 -c \"import os; P = '%s/a.py'; print(open(P).read()); os.remove('%s/b.py')\"" % (M, W))
        rc, err = run_bash(main_root, main_root, spill, "echo x > %s/a.py" % M)
        ck("allow a Bash write in a main-checkout job", rc == 0, (rc, err))

        deck = main_root / ".claude" / "scratch" / "deck"
        brief = outside / "brief.md"
        brief.write_text("# t\n\ninputs:\n- a.md\ntargets:\n- %s\n- %s\n- %s\n" % (
            W + "/**", deck.as_posix() + "/**", M + "/notes/one.txt"), encoding="utf-8")
        bare = outside / "bare.md"
        bare.write_text("# no targets list\n", encoding="utf-8")

        def declared(name, want, tool, path, prompt=brief):
            rc, err = run(work, main_root, spill, tool, path, prompt=prompt)
            ck(name, rc == want, (rc, err))

        declared("allow a Write under a declared targets glob", 0, "Write", deck / "pieces.jsonl")
        declared("allow an Edit of a declared targets file", 0, "Edit", main_root / "notes" / "one.txt")
        declared("deny a main file the targets do not name", 2, "Write", main_root / "notes" / "two.txt")
        declared("deny a prefix-sibling of a declared dir", 2, "Write", main_root / ".claude" / "scratch" / "deck2" / "a")
        declared("deny with a brief that declares no targets", 2, "Write", deck / "pieces.jsonl", prompt=bare)
        declared("deny with an unreadable brief", 2, "Write", deck / "pieces.jsonl", prompt=outside / "gone.md")
        rc, err = run_bash(*a, "cp a.jsonl %s/pieces.jsonl" % deck.as_posix(), prompt=brief)
        ck("allow a Bash cp into a declared targets dir", rc == 0, (rc, err))
        rc, err = run_bash(*a, "cp a.jsonl %s/b.jsonl" % M, prompt=brief)
        ck("deny a Bash cp to an undeclared main path with a brief", rc == 2, (rc, err))

        # The child cannot widen its own allowance: the brief the guard re-reads is never a path it may write,
        # wherever the dispatcher put it (the spill dir, the workdir, outside the checkout, a declared dir).
        in_spill = spill / "job_prompt.md"
        in_work = work / "brief.md"
        in_declared = deck / "brief.md"
        for place in (in_spill, in_work, in_declared):
            place.parent.mkdir(parents=True, exist_ok=True)
            place.write_text(brief.read_text(encoding="utf-8"), encoding="utf-8")
        for label, place in (("the spill dir", in_spill), ("the workdir", in_work), ("a declared dir", in_declared),
                             ("a folder outside the checkout", brief)):
            declared("deny a Write of the brief in " + label, 2, "Write", place, prompt=place)
            declared("deny an Edit of the brief in " + label, 2, "Edit", place, prompt=place)
            rc, err = run_bash(*a, "echo x >> %s" % place.as_posix(), prompt=place)
            ck("deny a Bash append to the brief in " + label, rc == 2, (rc, err))
            rc, err = run_bash(*a, "cat %s" % place.as_posix(), prompt=place)
            ck("allow a Bash read of the brief in " + label, rc == 0, (rc, err))
        declared("allow a report beside the brief in the spill dir", 0, "Write", spill / "job.md", prompt=in_spill)
        declared("allow a workdir file beside the brief", 0, "Write", work / "a.py", prompt=in_work)
        declared("deny the brief named by a path that climbs out of its folder", 2, "Write",
                 spill / "sub" / ".." / "job_prompt.md", prompt=in_spill)

    assert not fails, "\n".join(fails)
