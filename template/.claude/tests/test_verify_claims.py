#!/usr/bin/env python3
"""Proof for tools/verify_claims.py: one evidence contract (`reference/evidence_contract.md`), four item kinds,
one claim verdict.

Part 1 is the original quote proof: a quote present in its artifact returns VERIFIED, including when an
unclosed `<` (a comparison, a `< ~2000 lines` aside) sits before it and a stray `>` after it. The two real cases
are plan-check round two's F29 and F37 quotes, which returned UNVERIFIED while present.

Part 2 is the contract: a positive and a negative control per leg (file, command, owner, url), the legacy
adapter, and the claim-level rule that any UNCHECKED item keeps a claim from VERIFIED.

Part 3 runs the real fixtures (`fixtures/evidence/`: two explore rounds and one review journal, files trimmed by
`build.py`) against an oracle that is a separate expression: a raw substring after `" ".join(text.split())`. Every
oracle-versus-checker disagreement carries a label in `fixtures/evidence/oracle_labels.json` read from the source.

    python3 .claude/tests/test_verify_claims.py
"""
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import _scan_fixtures as fx

REPO = Path(__file__).resolve().parent.parent.parent
TOOL = REPO / ".claude" / "tools" / "verify_claims.py"
FIXTURES = REPO / ".claude" / "tests" / "fixtures" / "evidence"
sys.path.insert(0, str(REPO / ".claude" / "tools"))
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def verdicts(claims, root=None):
    proc = subprocess.run([sys.executable, str(TOOL), "-", *(["--root", str(root)] if root else [])], input=json.dumps(claims), text=True,
                          capture_output=True, cwd=str(REPO), timeout=60, encoding="utf-8")
    if proc.returncode not in (0, 1) or "Traceback" in proc.stderr:
        return ["CRASH"] * len(claims), proc.stderr
    rows = [line.split()[0] for line in proc.stdout.splitlines()
            if line.split() and line.split()[0] in ("VERIFIED", "UNVERIFIED", "UNCHECKED")]
    return rows, proc.stdout


F29_QUOTE = "verify with a deterministic script over the FULL output set, not the agent's report or a spot-check."
F29_LINES = (
    "8. **Bundle scattered sources** into one file (< ~2000 lines) and have agents Read those.\n"
    "9. **Batch-mechanical output → " + F29_QUOTE + "** When an agent batch-edits N files, run one script.\n"
    "10. Recover from the agent transcript (`<transcript dir>/agent-*.jsonl`) before redispatching.\n"
)
F37 = (".claude/workflows/dispatch_chains.js", "out.push([j.label, r])")
P19_CLAIMS = FIXTURES / "p19" / "claims.json"


def check_p19_claims():
    wanted = {"F1-3": "VERIFIED", "F11-4": "VERIFIED", "F12-1": "UNVERIFIED",
              "F12-3": "VERIFIED", "F11-3": "UNVERIFIED"}
    source = json.loads(P19_CLAIMS.read_text(encoding="utf-8"))
    claims = [row for row in source if row.get("subject") in wanted]
    check("P19 round-two claims each have one fixture", len(claims) == len(wanted)
          and {row["subject"] for row in claims} == set(wanted))
    for row in claims:
        path = REPO / row["file"]
        body = path.read_text(encoding="utf-8")
        check(f"{row['subject']} cited artifact is readable", bool(body))
        if row["subject"] == "F12-1":
            check("F12-1 second fragment is absent after its semicolon",
                  'ALPHA *= albedo.a * albedo_tex.a;" }' not in body
                  and r'ALPHA *= albedo.a * albedo_tex.a;\n";' in body)
        if row["subject"] == "F11-3":
            check("F11-3 paraphrase is absent from its source", row["evidence"] not in body)
    got, out = verdicts(claims)
    for row, actual in zip(claims, got + [None] * len(claims)):
        subject = row["subject"]
        # F12-1 omits the literal backslash-n and string terminator after ALPHA *= ...;.
        # F11-3 paraphrases the source's <see cref="Node3D"/> as plain Node3D.
        check(f"P19 {subject} -> {wanted[subject]}", actual == wanted[subject], out)


def part_one():
    if P19_CLAIMS.is_file():  # p19 cites this project's own game files, so it never ships
        check_p19_claims()
    path, quote = F37
    check(f"F37 fixture precondition: the quote occurs verbatim in {path}",
          quote in (REPO / path).read_text(encoding="utf-8"))
    got, out = verdicts([{"subject": "F37", "file": path, "evidence": quote}])
    check("F37 real quote returns VERIFIED", got == ["VERIFIED"], out)

    with tempfile.TemporaryDirectory() as tmp:
        f29 = os.path.join(tmp, "feedback_delegate_output_trust.md")
        with open(f29, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(F29_LINES)
        got, out = verdicts([{"subject": "F29", "file": f29, "evidence": F29_QUOTE}])
        check("F29 quote between `< ~2000 lines` and `<transcript dir>` returns VERIFIED",
              got == ["VERIFIED"], out)
        planted = os.path.join(tmp, "planted.md")
        with open(planted, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("Keep bundles small (< 2000 lines).\n"
                     "The quoted sentence sits between the brackets.\n"
                     "for (let i = 0; i < n; i++) { total += 1 }\n"
                     "Later a stray arrow -> closes nothing.\n"
                     "The README pins <strong>4.6.1</strong> as the engine.\n"
                     "First quote fragment, then the middle, and last fragment.\n"
                     "[sub_resource type=\"CapsuleShape3D\" id=\"CapsuleShape3D_segment_hurt\"]\n"
                     "Call [method Node.get_parent] to [b]walk up[/b] the tree.\n"
                     "    [Signal]\n")
        cases = [
            ("a C# attribute line that is exactly a BBCode tag name verifies", "[Signal]", "VERIFIED"),
            ("an absent attribute line stays UNVERIFIED", "[Export]", "UNVERIFIED"),
            ("a .tscn bracket line whose name opens like a BBCode tag verifies",
             "[sub_resource type=\"CapsuleShape3D\" id=\"CapsuleShape3D_segment_hurt\"]", "VERIFIED"),
            ("an absent .tscn bracket line stays UNVERIFIED",
             "[sub_resource type=\"SphereShape3D\" id=\"SphereShape3D_hurtbox\"]", "UNVERIFIED"),
            ("a quote across class-reference BBCode still matches", "Call to walk up the tree.", "VERIFIED"),
            ("planted quote after an unclosed `<` and before a stray `>`",
             "The quoted sentence sits between the brackets.", "VERIFIED"),
            ("a comparison line quoted verbatim", "for (let i = 0; i < n; i++) { total += 1 }", "VERIFIED"),
            ("a quote across an inline HTML tag still matches", "pins 4.6.1 as the engine", "VERIFIED"),
            ("ordered quote fragments separated by three dots verify",
             "First quote fragment...last fragment.", "VERIFIED"),
            ("ordered quote fragments separated by an ellipsis character verify",
             "First quote fragment…last fragment.", "VERIFIED"),
            ("out-of-order quote fragments stay UNVERIFIED",
             "last fragment...First quote fragment", "UNVERIFIED"),
            ("an absent quote stays UNVERIFIED", "a sentence this file never contains", "UNVERIFIED"),
        ]
        got, out = verdicts([{"subject": label, "file": planted, "evidence": quote}
                             for label, quote, _ in cases])
        for (label, _quote, want), verdict in zip(cases, got + [None] * len(cases)):
            check(f"{label} -> {want}", verdict == want, out)
        got, out = verdicts([{"subject": "no artifact", "file": os.path.join(tmp, "absent.md"),
                              "evidence": "anything"}])
        check("a missing local file -> UNVERIFIED", got == ["UNVERIFIED"], out)
        legacy = ["context7:/godotengine/godot-docs", "WebSearch", "memory"]
        got, out = verdicts([{"subject": cite, "file": cite, "evidence": "text"} for cite in legacy])
        check("legacy context7/WebSearch/memory citations -> UNCHECKED",
              got == ["UNCHECKED"] * len(legacy), out)
        doc = TOOL.read_text(encoding="utf-8").split('"""')[1]
        check("the docstring's UNCHECKED row names each non-path citation",
              all(name in doc.split("UNCHECKED  ")[1].splitlines()[0]
                  for name in ("context7", "WebSearch", "memory")), doc)
        proc = subprocess.run([sys.executable, str(TOOL), "-"], input=json.dumps([
            {"subject": "relative", "file": F37[0], "evidence": F37[1]}]), text=True,
            capture_output=True, cwd=tmp, timeout=60, encoding="utf-8")
        check("a repo-relative legacy citation resolves from outside the repo root",
              proc.stdout.startswith("VERIFIED"), proc.stdout + proc.stderr)
        manifest = os.path.join(tmp, "manifest.tsv")
        with open(manifest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("OK\t200\t10\t%s\thttps://example.test/page\n" % planted)
        proc = subprocess.run([sys.executable, str(TOOL), "-", "--manifest", manifest], input=json.dumps([
            {"subject": "fetched", "file": "https://example.test/page", "evidence": "pins 4.6.1 as the engine"},
            {"subject": "unfetched", "file": "https://example.test/other", "evidence": "pins 4.6.1 as the engine"}]),
            text=True, capture_output=True, cwd=tmp, timeout=60, encoding="utf-8")
        rows = [line.split()[0] for line in proc.stdout.splitlines() if line.split()[:1] and line.split()[0].isupper()]
        check("a legacy URL claim resolves through the fetch manifest and is UNCHECKED without a row",
              rows[:2] == ["VERIFIED", "UNCHECKED"], proc.stdout + proc.stderr)
        proc = subprocess.run([sys.executable, str(TOOL), "-"], input=json.dumps([
            {"subject": "ranged", "file": F37[0] + ":2", "evidence": F37[1]}]), text=True,
            capture_output=True, cwd=tmp, timeout=60, encoding="utf-8")
        check("a legacy citation with a `:N` suffix resolves (legacy mode took the path whole before)",
              proc.stdout.startswith("VERIFIED"), proc.stdout + proc.stderr)


def part_two(vc):
    import task_record

    def run(items, root, polarity=None, **kw):
        row = {"id": "x", "evidence": items}
        if polarity:
            row["polarity"] = polarity
        report = vc.check_claims([row], root=root, **kw)
        entry = report["verdicts"]["x"]
        first = entry["items"][0] if entry["items"] else {}
        return entry["verdict"], first.get("verdict"), first.get("reason"), report

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "doc.md").write_text("Alpha line one.\nThe Quick brown fox jumps.\nGamma line three.\nDelta end.\n",
                                     encoding="utf-8")
        (root / "sub").mkdir()
        (root / "sub" / "dir.md").mkdir()
        doc = str(root / "doc.md")

        # ---- file leg
        check("file: a present quote is VERIFIED", run([{"file": doc, "quote": "The Quick brown fox"}], root)[:2]
              == ("VERIFIED", "VERIFIED"))
        check("file: a repo-relative path resolves against the given root",
              run([{"file": "doc.md", "quote": "Gamma line three."}], root)[0] == "VERIFIED")
        check("file: an absent quote is UNVERIFIED",
              run([{"file": doc, "quote": "never written here"}], root)[0] == "UNVERIFIED")
        v = run([{"file": "nowhere.md", "quote": "anything"}], root)
        check("file: a missing path is UNVERIFIED", v[0] == "UNVERIFIED" and v[2] == "missing-file", v[:3])
        v = run([{"file": "sub/dir.md", "quote": "anything"}], root)
        check("file: a path that exists but cannot be read is UNCHECKED (unreadable), never UNVERIFIED",
              v[0] == "UNCHECKED" and v[2] == "unreadable", v[:3])
        check("file: a `path:N:` grep prefix on the quote is stripped",
              run([{"file": doc, "quote": "doc.md:2:The Quick brown fox jumps."}], root)[0] == "VERIFIED"
              and run([{"file": doc, "quote": "2:The Quick brown fox jumps."}], root)[0] == "VERIFIED")
        check("file: case is folded on both sides",
              run([{"file": doc, "quote": "the quick BROWN fox"}], root)[0] == "VERIFIED")
        check("file: in-order `...` gaps verify and out-of-order gaps do not",
              run([{"file": doc, "quote": "Alpha...Gamma line"}], root)[0] == "VERIFIED"
              and run([{"file": doc, "quote": "Gamma line...Alpha"}], root)[0] == "UNVERIFIED")
        v = run([{"file": doc + ":3", "quote": "Gamma line three."}], root)
        check("file: a `:N` suffix is stripped and the range reported, a hit inside it",
              v[0] == "VERIFIED" and v[3]["verdicts"]["x"]["items"][0].get("rangeHit") is True, v[3])
        v = run([{"file": doc + ":1-2", "quote": "Gamma line three."}], root)
        check("file: a range that misses the quote is reported and never decisive",
              v[0] == "VERIFIED" and v[3]["verdicts"]["x"]["items"][0].get("rangeHit") is False, v[3])
        check("file: an empty quote is UNVERIFIED", run([{"file": doc, "quote": "  "}], root)[0] == "UNVERIFIED")
        for cite in ("context7:/godotengine/godot-docs", "WebSearch: godot 4.6", "memory: feedback_x"):
            check(f"file: `{cite}` is UNCHECKED, not a path",
                  run([{"file": cite, "quote": "text"}], root)[0] == "UNCHECKED")

        # ---- command leg
        # The command leg reads a committed repo holding the tool: a scratch install is a git checkout whose
        # files are untracked and uncommitted, so the proof plants its own history.
        cmd_repo = root / "cmd_repo"
        (cmd_repo / ".claude" / "tools").mkdir(parents=True)
        (cmd_repo / ".claude" / "tools" / "verify_claims.py").write_bytes(TOOL.read_bytes())
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "proof@example.test"),
                     ("config", "user.name", "Proof"), ("config", "commit.gpgsign", "false"),
                     ("add", "-A"), ("commit", "-qm", "tool")):
            subprocess.run(["git", "-C", str(cmd_repo), *args], check=True, capture_output=True, timeout=60)
        token = fx.ABSENT_TERM
        v = run([{"command": "git ls-files -- .claude/tools/verify_claims.py",
                  "output": ".claude/tools/verify_claims.py"}], cmd_repo)
        check("command: true output is VERIFIED", v[0] == "VERIFIED", v[:3])
        v = run([{"command": "git ls-files -- .claude/tools/verify_claims.py", "output": "wrong.py"}], cmd_repo)
        check("command: wrong output is UNVERIFIED", v[0] == "UNVERIFIED", v[:3])
        v = run([{"command": f"git grep -n -F {token} -- .claude/tools", "output": ""}], cmd_repo)
        check("command: empty output for an absent term over a real pathspec is VERIFIED",
              v[0] == "VERIFIED", v[:3])
        for label, pathspec in (("a wrong-case pathspec", ".CLAUDE/TOOLS"), ("a nonexistent pathspec", "no/such/dir")):
            v = run([{"command": f"git grep -n -F {token} -- {pathspec}", "output": ""}], cmd_repo)
            check(f"command: empty output over {label} is UNCHECKED (vacuous-scope), never VERIFIED",
                  v[0] == "UNCHECKED" and v[2] == "vacuous-scope", v[:3])
        v = run([{"command": f"git grep -c -F {token} -- .claude/tools", "output": ""}], cmd_repo)
        check("command: `git grep -c` is on the allow-list", v[2] != "not-runnable", v[:3])
        v = run([{"command": f"git grep -n -F {token}", "output": "something"}], cmd_repo)
        check("command: output that the stdout lacks is UNVERIFIED", v[0] == "UNVERIFIED", v[:3])
        v = run([{"command": "git log --oneline -n 1 -- .claude/tools/verify_claims.py", "output": ""}], cmd_repo)
        check("command: empty `output` over non-empty stdout is UNVERIFIED", v[0] == "UNVERIFIED", v[:3])
        for ref in ("HEAD", "main", "HEAD~1", "origin/main"):
            v = run([{"command": f"git show {ref}:.claude/tools/verify_claims.py", "output": "import"}], cmd_repo)
            check(f"command: `git show {ref}:<path>` names a moving ref, UNCHECKED (moving-ref)",
                  v[0] == "UNCHECKED" and v[2] == "moving-ref", v[:3])
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cmd_repo, capture_output=True, text=True).stdout.strip()
        v = run([{"command": f"git show {sha}:./.claude/tools/verify_claims.py", "output": "import"}], cmd_repo)
        check("command: `git show <sha>:<path>` with a fixed revision still verifies", v[0] == "VERIFIED", v[:3])
        v = run([{"command": f"git show {sha}:./.claude/tools/verify_claims.py", "output": "the one executable evidence contract"}],
                cmd_repo)
        check("command: `git show <rev>:<path>` reads history", v[0] == "VERIFIED", v[:3])
        v = run([{"command": "git ls-files -- .claude/tools/no_such_tool.py", "output": "x"}], cmd_repo)
        check("command: an UNVERIFIED claim keeps no hidden UNCHECKED when the output simply is not there",
              v[0] == "UNVERIFIED", v[:3])
        v = run([{"command": "git rev-parse --verify no_such_ref_zq", "output": "x"}], cmd_repo)
        check("command: a failing command (non-zero exit with stderr) is UNCHECKED, never UNVERIFIED",
              v[0] == "UNCHECKED", v[:3])

        refused = [
            "python3 -c print(1)", "ls -la", "git ls-files | head", "git ls-files; echo hi",
            "git ls-files && echo hi", "git ls-files > out.txt", "git ls-files $HOME", "git ls-files `echo x`",
            "git grep -O calc x", "git log --output=planted.txt", "git log --ext-diff", "git show --textconv x",
            "git --exec-path=. ls-files", "git --git-dir=. ls-files", "git -c core.pager=x log",
            "git grep -nOcalc x", "git log --outp=planted.txt", "git diff --stat --ext-d", "git show --textc x",
            "git branch newbranch", "git branch -- newbranch", "git branch -D x", "git status", "git diff", "git log -p --output planted.txt",
            "git cat-file --batch", "git rev-parse --git-dir", "git ls-files --stdin", "git",
        ]
        for command in refused:
            v = run([{"command": command, "output": ""}], root)
            check(f"command: `{command}` is refused as not-runnable", v[0] == "UNCHECKED" and v[2] == "not-runnable", v[:3])
        check("command: a refused `--output`/`--outp` never wrote its file",
              not (root / "planted.txt").exists())
        # A real 1 ms limit races a fast git (CI saw it finish first), so the run is made to overrun.
        seen_timeouts = []
        real_run = vc.subprocess.run

        def overrun(*args, **kwargs):
            seen_timeouts.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

        vc.subprocess.run = overrun
        try:
            v = run([{"command": f"git grep -n -F {token} -- .claude/tools", "output": ""}], cmd_repo, command_timeout=7)
        finally:
            vc.subprocess.run = real_run
        check("command: a run past its timeout is UNCHECKED (timeout)",
              v[0] == "UNCHECKED" and v[2] == "timeout" and seen_timeouts[:1] == [7], (v[:3], seen_timeouts))

        # ---- command leg: the absence checks guards/survey.md names
        planted = root / "planted_repo"
        planted.mkdir()

        def git(*args):
            done = subprocess.run(["git", "-C", str(planted), *args], capture_output=True, text=True,
                                  encoding="utf-8", timeout=60)
            assert done.returncode == 0, (args, done.stderr)
            return done.stdout

        git("init", "-q", "-b", "main")
        git("config", "user.email", "proof@example.test")
        git("config", "user.name", "Proof")
        git("config", "commit.gpgsign", "false")
        (planted / "present.txt").write_text("alpha\n", encoding="utf-8")
        (planted / ".gitignore").write_text("build/\n", encoding="utf-8")
        (planted / "build").mkdir()
        (planted / "build" / "artifact.txt").write_text("compiled\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "main has present.txt")
        git("checkout", "-q", "-b", "feature")
        (planted / "feature_only.txt").write_text("bravo\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "feature adds feature_only.txt")
        git("checkout", "-q", "main")
        v = run([{"command": "git grep -n zz_absent_term feature -- .", "output": ""}], planted)
        check("command: a branch-scoped `git grep` absence names the branch and is VERIFIED",
              v[0] == "VERIFIED", v[:3])
        v = run([{"command": "git grep -n bravo feature -- .", "output": ""}], planted)
        check("command: a branch-scoped absence claim whose term IS on the branch is UNVERIFIED",
              v[0] == "UNVERIFIED", v[:3])
        v = run([{"command": "git check-ignore -v build", "output": ".gitignore:1:build/"}], planted)
        check("command: `git check-ignore -v` on an ignored directory verifies against its output",
              v[0] == "VERIFIED", v[:3])

        # ---- owner leg
        project = root / "project"
        project.mkdir()
        cfg = root / "cfg"
        sid, other = "sess-aaaa-0001", "sess-bbbb-0002"
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(project))
        tdir = cfg / "projects" / slug
        tdir.mkdir(parents=True)

        def plant(session, rows):
            with open(tdir / (session + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")

        def prompt(uuid, text):
            return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}}

        ask = "Which route?"
        answer = {"type": "user", "uuid": "ans1", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "q1", "content": f'answered: "{ask}"="Use the sidecar route"'}]},
            "toolUseResult": {"questions": [{"question": ask}], "answers": {ask: "Use the sidecar route"}}}
        envelope = prompt("env1", "<task-notification>the owner said ship it at dawn</task-notification>")
        env = {"CLAUDE_CONFIG_DIR": str(cfg), "CLAUDE_PROJECT_DIR": str(project),
               "HARNESS_TASK_RECORD_DIR": str(root / "records")}
        saved = {k: os.environ.get(k) for k in list(env) + ["CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SESSION_ID"]}
        os.environ.update(env)
        os.environ.pop("CLAUDE_CODE_SIDECAR", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            plant(sid, [prompt("p1", "Ship the owner leg by tonight, please."), answer, envelope])
            owner = lambda text, **extra: [dict({"owner": text}, **extra)]
            v = run(owner("Ship the owner leg by tonight"), root, session_id=sid)
            check("owner: a planted transcript owner turn is VERIFIED", v[0] == "VERIFIED", v[:3])
            v = run(owner("Use the sidecar route", question=ask), root, session_id=sid)
            check("owner: a planted AskUserQuestion answer with its question is VERIFIED", v[0] == "VERIFIED", v[:3])
            v = run(owner("Use the sidecar route"), root, session_id=sid)
            check("owner: a bare picked label without its question is UNVERIFIED",
                  v[0] == "UNVERIFIED" and v[2] == "answer-without-question", v[:3])
            v = run(owner("Use the sidecar route", question="Which other route?"), root, session_id=sid)
            check("owner: a picked label under a question the owner did not answer is UNVERIFIED",
                  v[0] == "UNVERIFIED" and v[2] == "label-question-mismatch", v[:3])
            check("owner: the mismatch detail names the question the owner did answer",
                  ask in str(v[3]["verdicts"]["x"]["items"][0].get("detail")), v[:3])
            v = run(owner("Ship the owner leg by tonight", question="irrelevant"), root, session_id=sid)
            check("owner: a typed prompt still verifies by its text alone", v[0] == "VERIFIED", v[:3])
            v = run(owner(ask), root, session_id=sid)
            check("owner: the assistant's question beside an answer is not the owner's words",
                  v[0] == "UNVERIFIED", v[:3])
            v = run(owner("ship it at dawn"), root, session_id=sid)
            check("owner: the same words only inside a <task-notification> envelope are not an owner turn",
                  v[0] == "UNVERIFIED", v[:3])
            v = run(owner("a"), root, session_id=sid)
            check("owner: a one-letter quote inside a longer word is not an owner turn", v[0] == "UNVERIFIED", v[:3])
            check("owner: a one-letter quote matches only a body that is exactly that letter",
                  not vc._match(vc.prepare("pick a thing"), "a", whole_words=True)
                  and vc._match(vc.prepare("A"), "a", whole_words=True))
            v = run(owner("leg by tonight, ple"), root, session_id=sid)
            check("owner: a quote that ends inside a word is UNVERIFIED", v[0] == "UNVERIFIED", v[:3])
            v = run(owner("words nobody said"), root, session_id=sid)
            check("owner: words no owner turn holds are UNVERIFIED", v[0] == "UNVERIFIED", v[:3])
            v = run(owner("anything"), root, session_id="no-such-session")
            check("owner: an unreadable transcript is UNCHECKED, never UNVERIFIED",
                  v[0] == "UNCHECKED" and v[2] == "transcript-unreadable", v[:3])
            v = run(owner("anything"), root)
            check("owner: no session id and no env is UNCHECKED", v[0] == "UNCHECKED", v[:3])

            # a ruling made in a planning session, cited from an executing one through a task record
            plant(other, [prompt("q1", "Plan A may not drop the Stop audit.")])
            task_record.open_record("t-own", session_id=other, title="owner ruling")
            task_record.record_condition("t-own", "request", "may not drop the Stop audit", "keep the audit",
                                         session_id=other, transcript=str(tdir / (other + ".jsonl")))
            v = run(owner("may not drop the Stop audit"), root, session_id=sid)
            check("owner: a quote the current transcript lacks is UNVERIFIED without a task",
                  v[0] == "UNVERIFIED", v[:3])
            v = run(owner("may not drop the Stop audit", task="t-own"), root, session_id=sid)
            check("owner: a quote held only in a task-record condition row named by `task` is VERIFIED",
                  v[0] == "VERIFIED", v[:3])
            os.environ["CLAUDE_CODE_SIDECAR"] = "1"
            task_record.open_record("t-del", session_id=other, title="delegate ruling")
            task_record.record_condition("t-del", "request", "may not drop the Stop audit", "keep the audit",
                                         session_id=other, transcript=str(tdir / (other + ".jsonl")))
            os.environ.pop("CLAUDE_CODE_SIDECAR")
            v = run(owner("may not drop the Stop audit", task="t-del"), root, session_id=sid)
            check("owner: a delegate-recorded row is UNCHECKED", v[0] == "UNCHECKED", v[:3])
            task_record.open_record("t-hand", session_id=other, title="hand written")
            record = json.loads((root / "records" / "t-hand.json").read_text(encoding="utf-8"))
            record["conditions"] = [{"id": "C1", "kind": "request", "quote": "may not drop the Stop audit",
                                     "effect": "x", "message_id": "not-the-turn", "session_id": other,
                                     "transcript": str(tdir / (other + ".jsonl")), "delegate": False}]
            (root / "records" / "t-hand.json").write_text(json.dumps(record), encoding="utf-8")
            v = run(owner("may not drop the Stop audit", task="t-hand"), root, session_id=sid)
            check("owner: a hand-written row whose message_id turn does not hold the quote is UNCHECKED",
                  v[0] == "UNCHECKED", v[:3])
            v = run(owner("may not drop the Stop audit", task="t-none"), root, session_id=sid)
            check("owner: a named task with no readable record falls back to the transcript verdict",
                  v[0] == "UNVERIFIED", v[:3])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        # ---- url leg
        v = run([{"url": "https://example.test/doc", "quote": "Gamma line", "artifact": doc}], root)
        check("url: a fetched artifact is checked like a file", v[0] == "VERIFIED", v[:3])
        v = run([{"url": "https://example.test/doc", "quote": "not there", "artifact": doc}], root)
        check("url: a fetched artifact lacking the quote is UNVERIFIED", v[0] == "UNVERIFIED", v[:3])
        v = run([{"url": "https://example.test/doc", "quote": "Gamma line"}], root)
        check("url: without an artifact the item is UNCHECKED (external)",
              v[0] == "UNCHECKED" and v[2] == "external", v[:3])
        v = run([{"url": "https://example.test/doc", "quote": "Gamma line"}], root,
                manifest={"https://example.test/doc": doc})
        check("url: a manifest row supplies the artifact", v[0] == "VERIFIED", v[:3])

        # ---- claim verdict
        good = {"file": doc, "quote": "Gamma line three."}
        bad = {"file": doc, "quote": "never written here"}
        ext = {"url": "https://example.test/doc", "quote": "Gamma line"}
        check("claim: every item VERIFIED is VERIFIED", run([good, good], root)[0] == "VERIFIED")
        check("claim: any UNVERIFIED item is UNVERIFIED, even beside an UNCHECKED one",
              run([good, ext, bad], root)[0] == "UNVERIFIED")
        check("claim: a true item beside an UNCHECKED item is never VERIFIED", run([good, ext], root)[0] == "UNCHECKED")
        check("claim: no items is UNCHECKED", run([], root)[0] == "UNCHECKED")
        check("claim: only external evidence is UNCHECKED", run([ext], root)[0] == "UNCHECKED")
        check("claim: polarity unclear is UNCHECKED even when its item is VERIFIED",
              run([good], root, polarity="unclear")[0] == "UNCHECKED")
        check("claim: an item of no kind is UNCHECKED (malformed-item)",
              run([{"nothing": 1}], root)[2] == "malformed-item" and run(["text"], root)[0] == "UNCHECKED")
        check("claim: a {path, quote} item reads as {file, quote}",
              run([{"path": doc, "quote": "Gamma line three."}], root)[0] == "VERIFIED"
              and run([{"path": doc, "quote": "never written here"}], root)[0] == "UNVERIFIED")
        rep = vc.check_claims([{"id": "a", "evidence": [good]}, {"id": "b", "evidence": [bad]},
                               {"id": "c", "evidence": []}], root=root)
        check("report: counts hold one verdict per claim and status fails on an UNVERIFIED one",
              rep["counts"] == {"VERIFIED": 1, "UNVERIFIED": 1, "UNCHECKED": 1} and rep["status"] == "fail"
              and rep["errors"] == [], rep)
        rep = vc.check_claims([{"id": "a", "evidence": [good]}, {"id": "c", "evidence": []}], root=root)
        check("report: UNCHECKED is counted and prevents pass",
              rep["status"] == "fail" and rep["counts"]["UNCHECKED"] == 1, rep)
        rep = vc.check_claims([{"id": "a", "evidence": [good]}, {"id": "a", "evidence": [bad]}], root=root)
        check("report: a duplicate claim id is an error and the status is not pass",
              rep["errors"] and rep["status"] == "fail", rep)

        # ---- legacy adapter
        tracked = ["tools/extract_numbers.py", "docs/README.md", "other/README.md"]

        def adapt(claim, **kw):
            return vc.items_of(claim, root=root, tracked=tracked, **kw)

        def quotes(items):
            return [item.get("quote") for item in items]

        base = {"file": "doc.md"}
        check("legacy: ` | ` splits into one item per fragment",
              quotes(adapt(dict(base, evidence="Alpha line one. | Gamma line three."))) == ["Alpha line one.", "Gamma line three."])
        check("legacy: ` ; ` splits", len(adapt(dict(base, evidence="Alpha line one. ; Gamma line three."))) == 2)
        check("legacy: a two-space slash splits and a lone ` / ` stays inside the quote",
              len(adapt(dict(base, evidence="Alpha line one.  /  Gamma line three."))) == 2
              and len(adapt(dict(base, evidence='REPO / ".claude/x.json"'))) == 1)
        check("legacy: quoted fragments split and lose their wrapping quotes",
              quotes(adapt(dict(base, evidence='"Alpha line one." "Gamma line three."'))) == ["Alpha line one.", "Gamma line three."]
              and quotes(adapt(dict(base, evidence="'Alpha line one.', 'Gamma line three.'"))) == ["Alpha line one.", "Gamma line three."])
        check("legacy: a `line N:` label is stripped",
              quotes(adapt(dict(base, evidence="line 3: Gamma line three."))) == ["Gamma line three."])
        items = adapt(dict(base, evidence="extract_numbers.py:511-512: Delta end."))
        check("legacy: a fragment labelled with another file resolves by unique tracked basename",
              items[0].get("file") == "tools/extract_numbers.py" and items[0].get("quote") == "Delta end.", items)
        items = adapt(dict(base, evidence="README.md: Delta end."))
        check("legacy: an ambiguous basename label is UNCHECKED, never guessed",
              items[0].get("file") is None and items[0].get("why") == "unresolved-label", items)
        items = adapt(dict(base, evidence="doc.md:2: Gamma line three."))
        check("legacy: a label naming the claim's own file keeps that file",
              items[0].get("file") == "doc.md" and items[0].get("quote") == "Gamma line three.", items)
        v = run(adapt({"file": None, "evidence": "Gamma line three."}), root)
        check("legacy: `file: null` is UNCHECKED (no-file)", v[0] == "UNCHECKED" and v[2] == "no-file", v[:3])
        check("legacy: a fragment of no letter or digit is dropped, every other is kept",
              quotes(adapt(dict(base, evidence="Alpha line one. | ---"))) == ["Alpha line one."]
              and len(adapt(dict(base, evidence="Alpha line one. | a"))) == 2)
        check("legacy: an empty evidence string gives no items", adapt(dict(base, evidence="  ")) == [])
        check("legacy: structured evidence passes through unchanged",
              adapt(dict(base, evidence=[{"file": "doc.md", "quote": "Delta"}])) == [{"file": "doc.md", "quote": "Delta"}])
        check("legacy: quoted fragments joined by ` and ` or `...` split, and a quote character inside a fragment does not",
              quotes(adapt(dict(base, evidence='"Alpha line one." and "Gamma line three."'))) == ["Alpha line one.", "Gamma line three."]
              and quotes(adapt(dict(base, evidence='"Alpha ... one" ... "Gamma"'))) == ["Alpha ... one", "Gamma"]
              and quotes(adapt(dict(base, evidence='"Fires on (`python -c`, `python -`) here" ... "Never denies"')))
              == ["Fires on (`python -c`, `python -`) here", "Never denies"])
        check("legacy: a pipe inside a double-quoted span stays inside it, a pipe outside splits",
              quotes(adapt(dict(base, evidence='"| Grounding | assumed | `/explore` |" | "Delta end."')))
              == ["| Grounding | assumed | `/explore` |", "Delta end."]
              and len(adapt(dict(base, evidence='Alpha | Gamma | Delta'))) == 3
              and len(adapt(dict(base, evidence='odd " quote count | split here'))) == 2)
        check("legacy: a prose label before a fully quoted fragment is stripped",
              quotes(adapt(dict(base, evidence='Reach row: "Gamma line three."'))) == ["Gamma line three."])
        items = adapt(dict(base, evidence='extract_numbers.py build_rows: "Delta end." and "Alpha line one."'))
        check("legacy: a file label with a symbol word and quoted runs after it routes every run to that file",
              [(i.get("file"), i.get("quote")) for i in items]
              == [("tools/extract_numbers.py", "Delta end."), ("tools/extract_numbers.py", "Alpha line one.")], items)
        check("legacy: a label naming the claim's own file with a section word keeps that file",
              adapt(dict(base, evidence='doc.md section 2: "Gamma line three."'))[0].get("file") == "doc.md")
        (root / "short.md").write_text("ab cd ab ef. A unique marker line.\n", encoding="utf-8")
        v = run(adapt({"file": "short.md", "evidence": "ab"}), root)
        check("legacy: a fragment too short to locate uniquely is UNCHECKED (short-fragment), not dropped",
              v[0] == "UNCHECKED" and v[2] == "short-fragment", v[:3])
        v = run(adapt({"file": "short.md", "evidence": "ef"}), root)
        check("legacy: a short fragment that occurs once still locates and verifies", v[0] == "VERIFIED", v[:3])
        v = run(adapt({"file": "short.md", "evidence": "A unique marker line."}), root)
        check("legacy: a long fragment verifies whatever its repeat count", v[0] == "VERIFIED", v[:3])
        v = run(adapt(dict(base, evidence="Alpha line one. | not in the file at all")), root)
        check("legacy: splitting cannot make a false pass (one absent fragment fails the claim)",
              v[0] == "UNVERIFIED", v[:3])
        v = run(adapt({"file": "https://example.test/doc", "evidence": "Gamma line", "artifact": doc}), root)
        check("legacy: a URL `file` with an artifact becomes a url item and verifies", v[0] == "VERIFIED", v[:3])

    # ---- schema twin
    schema = json.loads((REPO / ".claude/schemas/evidence.json").read_text(encoding="utf-8"))
    kinds = schema["items"]["oneOf"]
    required = sorted(tuple(sorted(k["required"])) for k in kinds)
    check("schema: evidence.json lists exactly the four item kinds the checker reads",
          required == sorted([("file", "quote"), ("command", "output"), ("owner",), ("quote", "url")])
          and all(k.get("additionalProperties") is False and k.get("description") for k in kinds)
          and set(vc.ITEM_KINDS) == {"file", "command", "owner", "url"}, required)
    standard = {"type", "properties", "items", "required", "additionalProperties", "description", "oneOf", "enum"}

    def stray(node):
        out = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in standard:
                    out.append(key)
                if key != "properties":
                    out += stray(value)
                else:
                    out += [bad for sub in value.values() for bad in stray(sub)]
        elif isinstance(node, list):
            out += [bad for sub in node for bad in stray(sub)]
        return out

    check("schema: evidence.json uses only standard JSON Schema keywords (the CLI validates in ajv strict mode)",
          not stray(schema), stray(schema))
    contract = (REPO / ".claude/reference/evidence_contract.md").read_text(encoding="utf-8")
    check("contract: the owner doc names the four kinds, the three verdicts and the NOT-APPLICABLE rename",
          all(word in contract for word in ("{file, quote}", "{command, output}", "{owner, question?, task?}", "{url, quote",
                                            "VERIFIED", "UNVERIFIED", "UNCHECKED", "NOT-APPLICABLE")))


def words(text):
    return " ".join(text.split())


ORACLE_SPLIT = re.compile(r"\s\|\s|\s;\s|\s{2}/\s{2}|\.\.\.|…")


def oracle_fragments(evidence):
    return [words(p).strip("\"'` ") for p in ORACLE_SPLIT.split(evidence) if len(words(p).strip("\"'` ")) >= 6]


def oracle_state(texts, frags):
    hit = [any(f in words(t) for t in texts) for f in frags]
    return "present" if frags and all(hit) else "absent" if frags and not any(hit) else "mixed"


def tree_texts(tree, claim_file, evidence):
    texts = []
    path = re.sub(r":\d+(?:-\d+)?$", "", claim_file or "")
    if path and (tree / path).is_file():
        texts.append((tree / path).read_text(encoding="utf-8"))
    for file in tree.rglob("*"):
        if file.is_file() and file.name != path.rsplit("/", 1)[-1] and re.search(
                r"\b" + re.escape(file.name) + r"\b", evidence):
            texts.append(file.read_text(encoding="utf-8"))
    return texts


def part_three(vc):
    labels = json.loads((FIXTURES / "oracle_labels.json").read_text(encoding="utf-8")) \
        if (FIXTURES / "oracle_labels.json").is_file() else {}
    for name, size in (("r1", 74), ("r2", 98)):
        base = FIXTURES / name
        if not (base / "claims.json").is_file():
            continue  # the r1/r2 trees quote this project's own files, so they never ship
        claims = json.loads((base / "claims.json").read_text(encoding="utf-8"))
        check(f"fixtures {name}: the dossier holds {size} claims", len(claims) == size, len(claims))
        tracked = (base / "tracked.txt").read_text(encoding="utf-8").split()
        tree = base / "tree"
        rows = [{"id": c["id"], "evidence": vc.items_of(c["claim"], root=tree, tracked=tracked),
                 "polarity": c["claim"].get("polarity")} for c in claims]
        report = vc.check_claims(rows, root=tree)
        check(f"fixtures {name}: every claim has a verdict and the report carries no errors",
              len(report["verdicts"]) == size and not report["errors"], report["errors"])
        disagreements = {}
        for c in claims:
            claim = c["claim"]
            evidence = claim.get("evidence") if isinstance(claim.get("evidence"), str) else ""
            frags = oracle_fragments(evidence)
            if not frags or not claim.get("file"):
                continue
            state = oracle_state(tree_texts(tree, claim["file"], evidence), frags)
            verdict = report["verdicts"][c["id"]]["verdict"]
            if (state == "absent" and verdict == "VERIFIED") or (state == "present" and verdict == "UNVERIFIED"):
                disagreements[c["id"]] = f"oracle {state}, checker {verdict}"
        unlabelled = sorted(set(disagreements) - set(labels.get(name, {})))
        check(f"fixtures {name}: no claim whose fragments are all absent is VERIFIED, and none whose fragments "
              f"are all present is UNVERIFIED, except the ones oracle_labels.json labels from the source",
              not unlabelled, {i: disagreements[i] for i in unlabelled})
        stale = sorted(set(labels.get(name, {})) - set(disagreements))
        check(f"fixtures {name}: every label still names a live disagreement", not stale, stale)
        design_present = {"r1": "C22 C25 C26 C40 C55 C57 C58 C60 C62 C65 C67 C69".split(), "r2": ["C24", "C81"]}[name]
        check(f"fixtures {name}: the claims the design found fully present (C22 ... C81) are never UNVERIFIED",
              not [i for i in design_present if report["verdicts"][i]["verdict"] == "UNVERIFIED"],
              {i: report["verdicts"][i]["verdict"] for i in design_present})
        counts = report["counts"]
        check(f"fixtures {name}: the three verdicts account for every claim",
              sum(counts.values()) == size, counts)

    base = FIXTURES / "review"
    out = base / "verdicts.out.json"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "verdicts.json"
        code = vc.verify_findings(str(base), str(out), {}, False, root=base / "tree")
        payload = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
        check("fixtures review: the journal's findings verify to a complete verdict file",
              code == 0 and payload.get("status") == "complete" and payload.get("verdicts"), (code, payload.get("errors")))
        raw = {}
        archive_items = vc.load_findings(base)[0]
        for row in archive_items:
            raw[row["id"]] = row["evidence"]
        wrong = []
        for row in payload.get("verdicts", []):
            texts = [t for ev in raw[row["id"]] if isinstance(ev, dict) and ev.get("file")
                     for t in tree_texts(base / "tree", ev["file"], "")]
            frags = [f for ev in raw[row["id"]] if isinstance(ev, dict) and isinstance(ev.get("quote"), str)
                     for f in oracle_fragments(ev["quote"])]
            state = oracle_state(texts, frags)
            if (state == "absent" and row["verdict"] == "VERIFIED") or (state == "present" and row["verdict"] == "UNVERIFIED"):
                if row["id"] not in labels.get("review", {}):
                    wrong.append((row["id"], state, row["verdict"]))
        check("fixtures review: findings agree with the oracle, except labelled ones", not wrong, wrong)


def part_git_dir(vc):
    """`git -C <repo-relative dir>` is the one git-level option the command leg runs; `check-ignore --no-index` is runnable."""
    def git(root, *args):
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                       capture_output=True)

    def run(command, output, root):
        report = vc.check_claims([{"id": "x", "evidence": [{"command": command, "output": output}]}], root=root)
        entry = report["verdicts"]["x"]
        first = entry["items"][0] if entry["items"] else {}
        return entry["verdict"], first.get("reason")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        (root / "sub").mkdir()
        (root / "sub" / "a.txt").write_text("hello world\n", encoding="utf-8")
        (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (root / "ignored.txt").write_text("x\n", encoding="utf-8")
        git(root, "add", "sub/a.txt", ".gitignore")
        check("git -C: a repo-relative directory runs the command there and verifies its output",
              run("git -C sub grep -n -F hello", "hello world", root) == ("VERIFIED", "output-found"),
              run("git -C sub grep -n -F hello", "hello world", root))
        check("git -C: output the command does not print is UNVERIFIED",
              run("git -C sub grep -n -F hello", "goodbye", root)[0] == "UNVERIFIED")
        outside = str(root.parent)
        for command in ("git -C .. grep -n -F hello", "git -C sub/../.. grep -n -F hello",
                        f"git -C {root} grep -n -F hello", f"git -C {outside} grep -n -F hello",
                        "git -C sub -c core.pager=x grep -n -F hello", "git -C sub --no-pager grep -n -F hello",
                        "git -C sub -C sub grep -n -F hello", "git -C nosuch grep -n -F hello",
                        "git -C sub/a.txt grep -n -F hello", "git -C grep -n -F hello", "git -C"):
            check(f"git -C: `{command}` stays not-runnable",
                  run(command, "", root) == ("UNCHECKED", "not-runnable"), run(command, "", root))
        check("check-ignore: `--no-index` is an allowed flag",
              run("git check-ignore --no-index ignored.txt", "ignored.txt", root) == ("VERIFIED", "output-found"),
              run("git check-ignore --no-index ignored.txt", "ignored.txt", root))


def part_command_inputs(vc):
    """An empty-output command records the blob hashes of the tracked files its pathspecs name; an edit to one moves them."""
    def git(root, *args):
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                       capture_output=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        (root / "sub").mkdir()
        (root / "sub" / "a.txt").write_text("hello world\n", encoding="utf-8")
        (root / "other.txt").write_text("goodbye\n", encoding="utf-8")
        git(root, "add", "sub/a.txt", "other.txt")
        scoped = {"id": "x", "evidence": [{"command": "git grep -n -F goodbye -- sub", "output": ""}]}
        before = vc.check_claims([scoped], root=root)["verdicts"]["x"]["items"][0]
        check("empty-output command over a tracked path is VERIFIED and records the blob it reads",
              before["verdict"] == "VERIFIED" and (before.get("inputs") or {}).get("files") == 1, before)
        (root / "sub" / "a.txt").write_text("hello world\ngoodbye\n", encoding="utf-8")
        after = vc.check_claims([scoped], root=root)["verdicts"]["x"]["items"][0]
        check("an edit that changes the command's output reads output-differs with the blob moved",
              after["reason"] == "output-differs" and (after.get("inputs") or {}).get("files") == 1
              and after["inputs"] != before["inputs"], after)
        git(root, "add", "sub/a.txt")
        git(root, "-c", "core.autocrlf=false", "commit", "-qm", "c")
        (root / "other.txt").write_text("changed\n", encoding="utf-8")
        reads = []
        real = vc.Path.read_bytes

        def spy(self):
            reads.append(self.name)
            return real(self)

        whole = {"id": "z", "evidence": [{"command": "git grep -n -F nomatch -- .", "output": ""}]}
        vc.Path.read_bytes = spy
        try:
            item = vc.check_claims([whole], root=root)["verdicts"]["z"]["items"][0]
        finally:
            vc.Path.read_bytes = real
        check("a `-- .` pathspec reads only the files git reports changed", reads == ["other.txt"], reads)
        check("a clean file's index blob id and a changed file's bytes both feed the digest",
              (item.get("inputs") or {}).get("files") == 2, item)
        free = {"id": "y", "evidence": [{"command": "git grep -n -F goodbye", "output": ""}]}
        unscoped = vc.check_claims([free], root=root)["verdicts"]["y"]["items"][0]
        check("a command that names no path records no inputs", not unscoped.get("inputs"), unscoped)


def part_body_cache(vc):
    """Callers check one claim per call (session_digest, fold_check): a cited body is prepared once per
    process, and a changed file is prepared again."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "doc.md"
        src.write_text("alpha line one\nbeta line two\n", encoding="utf-8")
        seen = []
        real = vc.prepare
        vc.prepare = lambda text: (seen.append(text), real(text))[1]
        try:
            def run(key, quote):
                row = {"id": key, "evidence": [{"file": "doc.md", "quote": quote}]}
                return vc.check_claims([row], root=root)["verdicts"][key]["verdict"]
            body = src.read_text(encoding="utf-8")
            exact = run("x", "beta line two")
            check("an exact quote verifies without preparing the body", exact == vc.VERIFIED and body not in seen,
                  (exact, seen.count(body)))
            first = [run("a", "ALPHA  line one"), run("b", "Beta line TWO"), run("c", "alpha   LINE")]
            check("one cited body is prepared once across per-claim calls", seen.count(body) == 1, seen.count(body))
            check("cached bodies still verify every quote", first == [vc.VERIFIED] * 3, first)
            src.write_text("gamma line three\n", encoding="utf-8")
            check("a changed file is prepared again", run("d", "GAMMA line three") == vc.VERIFIED)
            stat = src.stat()
            src.write_text("delta line four\n", encoding="utf-8")
            os.utime(src, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
            check("a same-size rewrite with a new mtime is prepared again", run("e", "DELTA line four") == vc.VERIFIED)
            check("an absent quote is still refused", run("f", "epsilon five") == vc.UNVERIFIED)
            exe = root / "tool.exe"
            exe.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64 + b"alpha line one")
            seen.clear()
            row = {"id": "g", "evidence": [{"file": "tool.exe", "quote": "alpha line one"}]}
            item = vc.check_claims([row], root=root)["verdicts"]["g"]["items"][0]
            check("a binary source is unchecked, never decoded or prepared",
                  item["verdict"] == vc.UNCHECKED and item["reason"] == "binary" and not seen, item)
        finally:
            vc.prepare = real


def part_owner_turn_cache(vc):
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(json.dumps({"type": "user", "uuid": "p1", "message":
                                          {"role": "user", "content": "Keep owner turn cached."}}) + "\n",
                              encoding="utf-8")
        import _owner_text
        import task_record
        real_turns, real_pairs, real_prepare, real_transcript = (
            _owner_text.owner_turns, _owner_text.owner_answer_pairs, vc.prepare, task_record._session_transcript)
        parsed, prepared = [], []
        _owner_text.owner_turns = lambda path: (parsed.append(path), real_turns(path))[1]
        _owner_text.owner_answer_pairs = lambda path: (parsed.append(path), real_pairs(path))[1]
        vc.prepare = lambda text: (prepared.append(text), real_prepare(text))[1]
        task_record._session_transcript = lambda sid: str(transcript)
        try:
            def run(key, quote):
                row = {"id": key, "evidence": [{"owner": quote}]}
                return vc.check_claims([row], session_id="cache-test")['verdicts'][key]['verdict']
            first = run("a", "Keep owner turn")
            second = run("b", "owner turn cached")
            check("owner: transcript parse reused across check_claims calls", len(parsed) == 2, len(parsed))
            check("owner: turns are prepared once across owner claims", prepared.count("Keep owner turn cached.") == 1,
                  prepared)
            check("owner: cached evidence preserves golden verdicts", [first, second] == [vc.VERIFIED, vc.VERIFIED],
                  [first, second])
            stat = transcript.stat()
            transcript.write_text(json.dumps({"type": "user", "uuid": "p2", "message":
                                              {"role": "user", "content": "Changed owner transcript."}}) + "\n",
                                  encoding="utf-8")
            os.utime(transcript, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
            third = run("c", "Changed owner transcript")
            check("owner: changed transcript stat reparses", len(parsed) == 4 and third == vc.VERIFIED,
                  (len(parsed), third))
            held = [key for key in vc._OWNER_TURNS
                    if str(transcript.resolve()) in (key if isinstance(key, tuple) else (key,))]
            check("owner: a re-parsed transcript replaces its old entry", len(held) == 1, held)
        finally:
            _owner_text.owner_turns, _owner_text.owner_answer_pairs = real_turns, real_pairs
            vc.prepare, task_record._session_transcript = real_prepare, real_transcript


def part_contract_examples():
    """The premises examples the evidence contract prints are the block shape the checker reads."""
    text = (REPO / ".claude" / "reference" / "evidence_contract.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```premises\n(.*?)\n```", text, re.S)
    check("the contract prints at least one premises example", bool(blocks))
    claims = [json.loads(line) for block in blocks for line in block.splitlines()]
    check("each example line is a claim with an evidence list",
          bool(claims) and all(isinstance(c["claim"], str) and isinstance(c["evidence"], list) for c in claims))
    check("an example names a task-record item", any("task" in item for c in claims for item in c["evidence"]))
    rows, out = verdicts(claims)
    check("the checker gives every printed example a verdict, no crash", len(rows) == len(claims) and "CRASH" not in rows, out[-300:])


COMMIT_SUBJECT = "a planted subject that only the plant's commit holds"


def plant_commit(root):
    """A throwaway repo with one commit: the sha, the subject and the file text come from the plant."""
    def git(*args):
        return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
    git("init", "-q")
    (root / ".claude" / "tools").mkdir(parents=True)
    (root / ".claude" / "tools" / "verify_claims.py").write_text(
        "def claim_verdict(item_verdicts, polarity=None):\n    pass\n", encoding="utf-8", newline="\n")
    git("add", ".")
    git("commit", "-q", "-m", COMMIT_SUBJECT)
    return git("rev-parse", "--short=9", "HEAD")


def part_commit_citations():
    """A file field may name a commit (`git <sha>`, `git show <sha>`, `commit <sha>`, a bare sha, `<sha>:<path>`):
    the quote is checked against that commit's text. A sha that does not resolve is UNVERIFIED, not UNCHECKED."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        commit_cases(root, plant_commit(root))


def commit_cases(root, COMMIT_SHA):
    cases = [
        ("git <sha> names a commit: its subject is VERIFIED", f"git {COMMIT_SHA}", COMMIT_SUBJECT, "VERIFIED"),
        ("git show <sha> names a commit", f"git show {COMMIT_SHA}", COMMIT_SUBJECT, "VERIFIED"),
        ("commit <sha> names a commit", f"commit {COMMIT_SHA}", COMMIT_SUBJECT, "VERIFIED"),
        ("a bare sha names a commit", COMMIT_SHA, COMMIT_SUBJECT, "VERIFIED"),
        ("<sha>:<path> reads that path at that commit", f"{COMMIT_SHA}:.claude/tools/verify_claims.py",
         "def claim_verdict(item_verdicts, polarity=None):", "VERIFIED"),
        ("a quote absent from the commit is UNVERIFIED", f"git {COMMIT_SHA}",
         "no commit holds this zz-absent sentence", "UNVERIFIED"),
        ("a path absent at the commit is UNVERIFIED", f"{COMMIT_SHA}:.claude/tools/no_such_file.py",
         "def main():", "UNVERIFIED"),
        ("a sha that does not resolve is UNVERIFIED", "git deadbee00", COMMIT_SUBJECT, "UNVERIFIED"),
        ("a git command that names no commit stays UNCHECKED", "git status", COMMIT_SUBJECT, "UNCHECKED"),
        ("a non-git command with a sha stays UNCHECKED", f"bash echo {COMMIT_SHA}", COMMIT_SUBJECT, "UNCHECKED"),
    ]
    claims = [{"id": f"K{index}", "evidence": [{"file": file, "quote": quote}]}
              for index, (_, file, quote, _) in enumerate(cases)]
    rows, out = verdicts(claims, root)
    for index, (label, file, _quote, want) in enumerate(cases):
        got = rows[index] if index < len(rows) else "MISSING"
        check(label, got == want, f"{file!r}: want {want}, got {got}\n{out[-300:]}")


def main():
    part_one()
    import verify_claims as vc
    part_two(vc)
    part_git_dir(vc)
    part_command_inputs(vc)
    part_three(vc)
    part_body_cache(vc)
    part_owner_turn_cache(vc)
    part_contract_examples()
    part_commit_citations()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for failure in FAILURES:
            print(" -", failure[:400])
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
