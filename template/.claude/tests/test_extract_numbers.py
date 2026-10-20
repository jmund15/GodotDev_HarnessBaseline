"""Re-runnable proof for tools/extract_numbers.py, the rule-text number check (S1b Job A).

Every case that expects silence has a sibling on the same fixture that fires. Fixtures are planted
git repos under the system temp directory with a planted `instruction_scope`, removed on teardown.
The live cases (scope covers both manifests, tokenizer parity with the two pass scripts) run only
when the redesign's scratch inputs exist in this checkout and report SKIP otherwise.

    python3 .claude/tests/test_extract_numbers.py
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(CLAUDE, ".."))
TOOLS = os.path.join(CLAUDE, "tools")
TOOL = os.path.join(TOOLS, "extract_numbers.py")
sys.path.insert(0, TOOLS)

SCOPE = {"paths": [".claude/rules/*.md", ".claude/workflows/*.js"], "rows_dir": ".claude/instruction_rows"}
RECON = re.compile(r"scope files \d+, changed files \d+, changed lines \d+, flagged tokens \d+, with rows \d+")


def _rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def write(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def mkrepo(tmp, name, files, scope=SCOPE, rows=None):
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    if scope is not None:
        write(repo, ".claude/skills/project_subsystems/adaptation.json", json.dumps({"instruction_scope": scope}))
    for rel, text in files.items():
        write(repo, rel, text)
    if rows is not None:
        write(repo, ".claude/instruction_rows/numbers.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def run(repo, *args):
    p = subprocess.run([sys.executable, TOOL, "--repo", repo, *args], capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


def row(en, path, sentence, token, **kw):
    r = {"path": path, "hash": en.sentence_hash(en.norm(sentence)), "token": token, "sentence": en.norm(sentence),
         "kind": "non-rule", "class": "structure", "note": "t", "source": "seed:pass2"}
    r.update(kw)
    if r["kind"] == "rule":
        r.pop("class", None)
        r.setdefault("verdict", "confirm")
        r.setdefault("basisQuote", None)
    return r


def test_revision_binding_ignores_later_proof_and_dependency_inventory(tmp_path):
    import extract_numbers as en
    from pathlib import Path
    path = ".claude/rules/a.md"
    proof = ".claude/tests/test_bound.py"
    dependency = ".claude/tools/dep.py"
    implementation = ".claude/tools/bound.py"
    text = "# Behavior\n\nBound behavior stays covered.\n"
    repo = mkrepo(str(tmp_path), "history", {
        path: text, implementation: "import dep\nDECISION = True\n",
        dependency: "VALUE = 1\n",
        proof: "import bound\ncase_id = 'bound-case'\nassertion = 'assert bound.DECISION'\n",
    })
    unit = next(u for u in en.behavior_units(path, text) if u.norm == "bound behavior stays covered.")
    binding = {"path": path, "sentence": unit.norm, "hash": unit.hash, "section": unit.heading,
               "class": "behavior", "source": "proof", "classificationQuote": unit.raw,
               "claimId": "bound", "implementation": [{"path": implementation, "symbol": "DECISION",
                                                          "quote": "DECISION = True"}],
               "proofs": [{"path": proof, "caseId": "bound-case", "assertion": "assert bound.DECISION",
                           "selector": {"type": "check-label"}}]}
    heading = en.behavior_units(path, text)[0]
    nonclaim = {"path": path, "sentence": heading.norm, "hash": heading.hash, "section": heading.heading,
                "class": "nonclaim", "source": "proof", "classificationQuote": heading.raw}
    write(repo, ".claude/instruction_rows/behaviors.jsonl", json.dumps(binding) + "\n" + json.dumps(nonclaim) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "bind")
    rev = git(repo, "rev-parse", "HEAD").strip()
    cases, dependencies, missing = en._behavior_binding_state(Path(repo), [binding], rev)
    assert not missing
    assert dependency in dependencies[proof]["paths"]
    key = dependencies[proof]["inputKey"]
    receipt = [{"proof": proof, "caseId": "bound-case", "inputKey": key, "status": "pass",
                "evidence": "ok bound-case"}]
    before = en.behavior_report(repo, paths=[path], rev=rev, proof_results=receipt)
    assert before["status"] == "pass", before
    git(repo, "rm", proof)
    git(repo, "mv", dependency, ".claude/tools/renamed.py")
    write(repo, implementation, "import renamed\nDECISION = True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "remove proof and rename dependency")
    after = en.behavior_report(repo, paths=[path], rev=rev, proof_results=receipt)
    assert after == before
    cases, dependencies, missing = en._behavior_binding_state(Path(repo), [binding], rev)
    assert not missing
    assert dependencies[proof]["inputKey"] == key
    assert dependency in dependencies[proof]["paths"]
    assert ".claude/tools/renamed.py" not in dependencies[proof]["paths"]


def test_behavior_check_reports_only_changed_units(tmp_path):
    import extract_numbers as en
    path = ".claude/rules/a.md"
    before = "# Stable\n\nKeep old behavior.\n\n# Changed\n\nKeep former behavior. Keep stable adjacent behavior.\n"
    after = before.replace("former", "new")
    repo = mkrepo(str(tmp_path), "delta", {path: before})
    stable = next(u for u in en.behavior_units(path, before) if u.norm == "keep old behavior.")
    binding = {"path": path, "sentence": stable.norm, "hash": stable.hash, "section": stable.heading,
               "class": "behavior", "source": "proof", "classificationQuote": stable.raw,
               "claimId": "unchanged", "implementation": [], "proofs": []}
    write(repo, ".claude/instruction_rows/behaviors.jsonl", json.dumps(binding) + "\n")
    git(repo, "add", ".claude/instruction_rows/behaviors.jsonl")
    git(repo, "commit", "-qm", "seed binding")
    write(repo, path, after)
    rc, out, err = run(repo, "check", "--claims", "behavior", "--paths", path, "--json")
    assert rc == 1, (rc, err)
    report = json.loads(out)
    assert [u["sentence"] for u in report["units"]] == ["keep new behavior."], report["counts"]
    assert report["scope"]["initialDebt"] == []
    assert report["claims"] == [] and report["scope"]["affected"] == []
    assert report["counts"]["changedUnits"] == 1
    # The runner and list caller still need whole-file conformance evidence.
    full = en.behavior_report(repo, paths=[path], proof_results=[])
    assert len(full["units"]) == 5
    rc, out, err = run(repo, "list", "--claims", "behavior", path, "--json")
    assert rc == 0 and len(json.loads(out)["units"]) == 5, err
    git(repo, "add", path)
    rc, out, err = run(repo, "check", "--claims", "behavior", "--staged", "--paths", path, "--json")
    assert len(json.loads(out)["units"]) == 1, err
    git(repo, "commit", "-qm", "change")
    revision = git(repo, "rev-parse", "HEAD").strip()
    write(repo, path, before.replace("former", "dirty"))
    rc, out, err = run(repo, "check", "--claims", "behavior", "--rev", revision, "--paths", path, "--json")
    assert rc == 1, (rc, err)
    report = json.loads(out)
    assert [u["sentence"] for u in report["units"]] == ["keep new behavior."]
    assert report["scope"]["base"] == revision + "^"
    assert report["scope"]["source"] == "revision"


def main():
    import extract_numbers as en
    cases = []

    def case(label, ok):
        cases.append((label, bool(ok)))

    # ---- units ---------------------------------------------------------------------
    text = ("---\nname: x\n---\n# Heading 5\n\nFirst sentence here. Second one; Third?\n\n"
            "```\nfenced 7 line.\n```\n\n| a | b |\n|---|---|\n| 3 | four |\n\n- item e.g. lower case stays.\n")
    us = en.units("x.md", text)
    prose = [u.raw for u in us if u.kind == "prose"]
    case("units: sentence split on . ; ? before an uppercase letter",
         prose[:3] == ["First sentence here.", "Second one;", "Third?"])
    case("units: lowercase after a period does not split", any(u.raw.endswith("lower case stays.") for u in us))
    case("units: a table row is one unit and the separator row is none",
         [u.raw for u in us if u.kind == "table"] == ["| a | b |", "| 3 | four |"])
    case("units: heading, fence and frontmatter make no unit",
         not any(w in u.raw for u in us for w in ("Heading", "fenced", "name: x")))
    # ---- word forms: a bare one or single is an article or adjective; a bare once is a count
    wt = lambda s: [w.lower() for toks in en.word_tokens("x.md", s + "\n").values() for w in toks]
    case("words: a bare 'one' or 'single' is not flagged, and a bare 'once' is",
         wt("Pick one of the files once, from a single source.") == ["once"])
    case("words: 'one' and 'once' inside a count frame are flagged",
         wt("Retry at most one time.") == ["at most one time"] and wt("Run it at least once.") == ["at least once"])
    case("words: a 'once' that opens a clause is a conjunction, not a count",
         wt("A guard leaves once the models stop showing the weakness.") == []
         and wt("It runs once its input lands.") == [] and wt("Run it once per session.") == ["once"])
    case("words: 'only one' and 'exactly one' are count frames",
         wt("Only one job runs.") == ["only one job"] and wt("Keep exactly one seat.") == ["exactly one seat"])
    case("words: two through twelve stay flagged bare", wt("Use two seats.") == ["two seats"])
    case("words: an ordinal before a pass noun is a bound on repeat passes",
         wt("Never run a third round.") == ["third round"] and wt("A second fold runs only when held ids need writing in.") == ["second fold"])
    case("words: 'at most a' and 'at most an' bound a set",
         wt("Seat at most a design and a grounding seat.") == ["at most a design"] and wt("Keep at most an hour.") == ["at most an hour"])
    case("words: a plain ordinal in prose is not flagged", wt("See the second paragraph and the third item.") == [])
    other = en.units("x.md", "Intro.\n\nMore text.\n\nFirst sentence here.\n")
    hit = [u for u in other if u.raw == "First sentence here."][0]
    case("units: the same sentence on another line has the same hash and a new line",
         hit.hash == us[0].hash and hit.line_start == 5 and us[0].line_start == 6)
    edited = en.units("x.md", "First sentence here now.\n")
    case("units: a one-word edit changes the hash", edited[0].hash != us[0].hash)
    case("units: hash is 16 hex characters", re.fullmatch(r"[0-9a-f]{16}", us[0].hash) is not None)
    case("norm: strips emphasis, backticks and list markers, folds case",
         en.norm("- **Never** use `X`.  Twice") == "never use x. twice")
    tricky = "- **- 2026-05-13** initial roadmap. 7 parts."
    case("norm is idempotent, so a stored sentence re-hashes to its row's hash", en.norm(en.norm(tricky)) == en.norm(tricky))
    js = en.units("w.js", "const a = 1;\n// keep the cap at 40 rows. Second.\nif (n > 5) go();\n")
    case("units: in .js only comments and pass-1 code shapes make units",
         [u.kind for u in js] == ["js", "js"])

    tmp = tempfile.mkdtemp(prefix="extract_numbers_proof_")
    try:
        # ---- scope ------------------------------------------------------------------
        bare = mkrepo(tmp, "noscope", {".claude/rules/a.md": "Keep 400 lines.\n"}, scope=None)
        rc, out, err = run(bare, "check")
        case("missing instruction_scope: check exits 2 naming the key", rc == 2 and "instruction_scope" in (out + err))
        rc, out, err = run(bare, "list", ".claude/rules/a.md")
        case("missing instruction_scope: list exits 2 naming the key", rc == 2 and "instruction_scope" in (out + err))
        sc = en.load_scope(os.path.join(bare, ".claude"))
        case("load_scope reports a missing key as invalid", sc.invalid)
        good = mkrepo(tmp, "scope", {".claude/rules/a.md": "Plain.\n"})
        sc = en.load_scope(os.path.join(good, ".claude"))
        case("load_scope reads paths and rows_dir", sc.paths == SCOPE["paths"] and sc.rows_dir == SCOPE["rows_dir"] and not sc.invalid)
        case("scope globs: ** crosses / and * does not",
             en.in_scope(en.Scope(["a/**/*.md"], "r", []), "a/b/c/d.md")
             and not en.in_scope(en.Scope(["a/*.md"], "r", []), "a/b/d.md")
             and en.in_scope(en.Scope(["a/*.md"], "r", []), "a/d.md"))

        # ---- check: added token with and without a row ------------------------------
        base = {".claude/rules/a.md": "Intro line.\n", ".claude/other/b.md": "Intro line.\n"}
        r1 = mkrepo(tmp, "c1", base)
        write(r1, ".claude/rules/a.md", "Intro line.\n\nKeep the cap at 400 retries.\n")
        rc, out, _ = run(r1, "check")
        case("check: an added digit token with no row fails with path:line token: rule",
             rc == 1 and re.search(r"^\.claude/rules/a\.md:3 400: ", out, re.M))
        case("check: prints the reconciliation line", RECON.search(out))
        write(r1, ".claude/instruction_rows/numbers.jsonl",
              json.dumps(row(en, ".claude/rules/a.md", "Keep the cap at 400 retries.", "400")) + "\n")
        rc, out, _ = run(r1, "check")
        case("check: the same token with a non-rule row passes", rc == 0 and RECON.search(out))
        rc, out, _ = run(r1, "check", "--rows-dir", os.path.join(tmp, "nowhere"))
        case("check: --rows-dir replaces the scope's rows_dir", rc == 1)

        # ---- word forms and fences --------------------------------------------------
        r2 = mkrepo(tmp, "c2", base)
        write(r2, ".claude/rules/a.md", "Intro line.\n\nRetry at least two times.\n\n```\nrun two things\n```\n")
        rc, out, _ = run(r2, "check")
        case("check: 'at least two' is flagged", rc == 1 and "at least two times" in out)
        case("check: a count word inside a fenced block is not flagged", ":6 " not in out and "two things" not in out)
        write(r2, ".claude/rules/a.md", "Intro line.\n\nRetry.\n\nUse two of them.\n")
        rc, out, _ = run(r2, "check")
        case("check: the same count word outside a fence fires (sibling)", rc == 1 and re.search(r":5 two", out))

        # ---- drops ------------------------------------------------------------------
        r3 = mkrepo(tmp, "c3", base)
        write(r3, ".claude/rules/a.md", "Intro line.\n\nSee §3 on 2026-05-01 for v2.1 and the 400 cap.\n")
        rc, out, _ = run(r3, "check")
        flagged = re.findall(r"^\.claude/rules/a\.md:\d+ (\S+):", out, re.M)
        case("check: a section ref, a date and a version drop while a bare 400 fires", flagged == ["400"])

        # ---- moved vs edited --------------------------------------------------------
        sent = "Keep the cap at 400 retries."
        seeded = [row(en, ".claude/rules/a.md", sent, "400")]
        r4 = mkrepo(tmp, "c4", {".claude/rules/a.md": sent + "\n\nTail.\n"}, rows=seeded)
        write(r4, ".claude/rules/a.md", "Tail.\n\nHead line.\n\n" + sent + "\n")
        rc, out, _ = run(r4, "check")
        case("check: a moved sentence passes on its old row", rc == 0)
        write(r4, ".claude/rules/a.md", "Tail.\n\nKeep the cap at 500 retries.\n")
        rc, out, _ = run(r4, "check")
        case("check: an edited sentence fails (sibling of the moved case)", rc == 1 and re.search(r":3 500:", out))
        write(r4, ".claude/rules/a.md", "Tail.\n\nKeep the cap at 400 retry.\n")
        rc, out, _ = run(r4, "check")
        case("check: a one-word edit that keeps the token still needs a new row", rc == 1 and re.search(r":3 400:", out))

        # ---- a sentence is the changed unit, not its physical line ---------------------
        r4b = mkrepo(tmp, "c4b", {".claude/rules/a.md": "Intro line.\n\nKeep the cap at 400 retries. Tail here.\n"})
        write(r4b, ".claude/rules/a.md", "Intro line.\n\nKeep the cap at 400 retries. Tail here. Added plain text.\n")
        rc, out, _ = run(r4b, "check")
        case("check: an unchanged sentence on an edited paragraph line owes no row", rc == 0)
        write(r4b, ".claude/rules/a.md", "Intro line.\n\nKeep the cap at 400 retries. Tail here. Added 500 text.\n")
        rc, out, _ = run(r4b, "check")
        case("check: the added sentence on that line still owes its row (sibling)",
             rc == 1 and re.search(r":3 500:", out) and not re.search(r":3 400:", out))

        # ---- basis quote ------------------------------------------------------------
        para = "Cap is 400 lines. The basis is the worker window."
        q = [row(en, ".claude/rules/a.md", "Cap is 400 lines.", "400", kind="rule", verdict="basis-found",
                 basisQuote="The basis is the worker window.", source="author:t")]
        r5 = mkrepo(tmp, "c5", {".claude/rules/a.md": "Intro.\n"}, rows=q)
        write(r5, ".claude/rules/a.md", "Intro.\n\n" + para + "\n")
        rc, out, _ = run(r5, "check")
        case("check: a rule row whose basisQuote is present in the paragraph passes", rc == 0)
        write(r5, ".claude/rules/a.md", "Intro.\n\nCap is 400 lines. Other text.\n")
        rc, out, _ = run(r5, "check")
        case("check: the same row with the quote deleted fails 'basis sentence absent'",
             rc == 1 and "basis sentence absent" in out)
        write(r5, ".claude/rules/a.md", "Intro.\n\nCap is 400 lines.\nThe basis is the worker window.\n")
        rc, out, _ = run(r5, "check")
        case("check: a quote on the next line of the paragraph counts as present", rc == 0)

        # ---- seeded litmus surfaces on edit, not on move ----------------------------
        lit = [row(en, ".claude/rules/a.md", sent, "400", kind="rule", verdict="litmus", note="read only what the question needs")]
        r6 = mkrepo(tmp, "c6", {".claude/rules/a.md": sent + "\n\nTail.\n"}, rows=lit)
        write(r6, ".claude/rules/a.md", "Tail.\n\n" + sent + "\n")
        rc, out, _ = run(r6, "check")
        case("check: a moved unedited sentence with a litmus row passes", rc == 0)
        write(r6, ".claude/rules/a.md", "Tail.\n\nKeep the cap at 400 retries now.\n")
        rc, out, _ = run(r6, "check")
        case("check: an edited sentence fails rule 1 and prints the seeded litmus verdict",
             rc == 1 and re.search(r":3 400:", out) and "litmus" in out and "read only what the question needs" in out)
        write(r6, ".claude/instruction_rows/numbers_unresolved.jsonl",
              json.dumps({"source": "seed:pass2", "path": ".claude/rules/a.md", "token": "500", "verdict": "delete",
                          "reason": "sentence changed"}) + "\n")
        write(r6, ".claude/rules/a.md", "Tail.\n\nThe cap is 500 now.\n")
        rc, out, _ = run(r6, "check")
        case("check: an unresolved entry for the same path and token is printed beside the failure",
             rc == 1 and "delete" in out and "sentence changed" in out)

        # ---- scope, untracked, staged, paths ----------------------------------------
        r7 = mkrepo(tmp, "c7", base)
        write(r7, ".claude/other/b.md", "Intro line.\n\nKeep 400 here.\n")
        rc, out, _ = run(r7, "check")
        case("check: an out-of-scope edit is silent", rc == 0 and RECON.search(out))
        write(r7, ".claude/rules/a.md", "Intro line.\n\nKeep 400 here.\n")
        rc, out, _ = run(r7, "check")
        case("check: the same edit in an in-scope file fires", rc == 1)
        r8 = mkrepo(tmp, "c8", base)
        write(r8, ".claude/rules/new.md", "Nothing.\nSecond line keeps 400.\n")
        rc, out, _ = run(r8, "check")
        case("check: an untracked in-scope file is checked whole", rc == 1 and re.search(r"new\.md:2 400:", out))
        rc, out, _ = run(r8, "check", "--staged")
        case("check --staged skips an untracked file", rc == 0)
        git(r8, "add", ".claude/rules/new.md")
        rc, out, _ = run(r8, "check", "--staged")
        case("check --staged reads the same file once it is staged", rc == 1)
        r9 = mkrepo(tmp, "c9", base)
        write(r9, ".claude/rules/a.md", "Intro line.\n\nKeep 400 here.\n")
        rc, out, _ = run(r9, "check")
        rc_s, out_s, _ = run(r9, "check", "--staged")
        case("check --staged ignores an unstaged edit the default mode flags",
             rc == 1 and rc_s == 0 and RECON.search(out_s))
        write(r9, ".claude/rules/c.md", "Intro line.\n\nKeep 700 here.\n")
        rc, out, _ = run(r9, "check", "--paths", ".claude/rules/c.md")
        case("check --paths keeps the named path and drops the other in-scope edit",
             rc == 1 and "c.md" in out and "a.md" not in out.split("scope files")[0])
        rc, out, _ = run(r9, "check", "--paths", ".claude/rules/zzz.md")
        case("check --paths with no changed named path is silent and still reconciles", rc == 0 and RECON.search(out))
        git(r9, "add", "-A")
        git(r9, "commit", "-q", "-m", "next")
        rc, out, _ = run(r9, "check")
        m = RECON.search(out)
        case("check: a run with no changed lines still prints the reconciliation line",
             rc == 0 and m and "changed lines 0" in m.group(0))
        rc, out, _ = run(r9, "check", "--base", "HEAD~1")
        case("check --base reads the diff against that revision", rc == 1)
        rc, out, _ = run(r9, "check", "--base", "HEAD~1", "--json")
        try:
            data = json.loads(out)
        except ValueError:
            data = {}
        case("check --json prints one object with failures and counts",
             isinstance(data, dict) and data.get("failures") and "counts" in data)

        # ---- stub / add / list --------------------------------------------
        r10 = mkrepo(tmp, "c10", {".claude/rules/a.md": "Intro.\n"})
        write(r10, ".claude/rules/a.md", "Intro.\n\nCap is 400 lines. See the basis.\n")
        rc, out, _ = run(r10, "stub")
        stub = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
        case("stub prints a JSONL row stub with path, hash, token, sentence and empty kind",
             rc == 0 and len(stub) == 1 and stub[0]["token"] == "400" and stub[0]["kind"] == ""
             and stub[0]["hash"] == en.sentence_hash(en.norm("Cap is 400 lines.")))
        scratch = os.path.join(tmp, "rows_scratch")
        bad = dict(stub[0], kind="rule", verdict="basis-found", basisQuote=None, source="author:t", **{"note": "n"})
        src = os.path.join(tmp, "bad.jsonl")
        write(tmp, "bad.jsonl", json.dumps(bad) + "\n")
        rc, out, err = run(r10, "add", src, "--rows-dir", scratch)
        case("add refuses an author rule row without basisQuote", rc == 1 and "basisQuote" in (out + err)
             and not os.path.exists(os.path.join(scratch, "numbers.jsonl")))
        ok_row = dict(bad, basisQuote="See the basis.")
        write(tmp, "ok.jsonl", json.dumps(ok_row) + "\n")
        rc, out, err = run(r10, "add", os.path.join(tmp, "ok.jsonl"), "--rows-dir", scratch)
        case("add accepts the same row with a basisQuote", rc == 0
             and os.path.exists(os.path.join(scratch, "numbers.jsonl")))
        rc, out, err = run(r10, "add", os.path.join(tmp, "ok.jsonl"), "--rows-dir", scratch)
        case("add refuses a row whose key already exists", rc == 1)
        seed_null = row(en, ".claude/rules/a.md", "Also 9 here.", "9", kind="rule", verdict="confirm")
        write(tmp, "seed.jsonl", json.dumps(seed_null) + "\n")
        rc, out, err = run(r10, "add", os.path.join(tmp, "seed.jsonl"), "--rows-dir", scratch)
        case("add accepts a seeded confirm row with a null basisQuote", rc == 0)
        nr = dict(stub[0], kind="non-rule", **{"class": "banana", "source": "author:t", "note": "n"})
        write(tmp, "nr.jsonl", json.dumps(nr) + "\n")
        rc, out, err = run(r10, "add", os.path.join(tmp, "nr.jsonl"), "--rows-dir", scratch)
        case("add refuses a non-rule row whose class is not identifier|example|structure", rc == 1 and "class" in (out + err))
        rc, out, _ = run(r10, "check", "--rows-dir", scratch)
        case("check passes once add wrote the row into the scratch rows dir", rc == 0)
        rc, out, err = run(r10, "add", os.path.join(tmp, "no_such.jsonl"), "--rows-dir", scratch)
        case("add exits 2 naming a rows file that does not exist", rc == 2 and "no such rows file" in (out + err))
        with open(os.path.join(scratch, "numbers.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("{not json\n")
        rc, out, err = run(r10, "check", "--rows-dir", scratch)
        case("check fails and counts a corrupt row line", rc == 1 and "unparseable rows 1" in out)
        r12u = mkrepo(tmp, "c12u", {".claude/rules/a.md": "Intro.\n"})
        with open(os.path.join(r12u, ".claude", "rules", "new.md"), "wb") as fh:
            fh.write(b"Cap is 400 lines. \xff\xfe\n")
        rc, out, err = run(r12u, "check")
        case("check fails an untracked in-scope file it cannot decode", rc == 1 and "file unreadable" in out)

        r11 = mkrepo(tmp, "c11", {".claude/rules/a.md": "Cap is 400 lines. Also 900 here.\n"},
                     rows=[row(en, ".claude/rules/a.md", "Cap is 400 lines.", "400")])
        write(r11, ".claude/instruction_rows/numbers_unresolved.jsonl",
              json.dumps({"source": "seed:pass2", "path": ".claude/rules/a.md", "token": "900", "verdict": "litmus",
                          "reason": "changed"}) + "\n")
        rc, out, _ = run(r11, "list", ".claude/rules/a.md", "--json")
        try:
            listed = json.loads(out)
        except ValueError:
            listed = []
        status = {e["token"]: e["status"] for e in listed} if isinstance(listed, list) else {}
        case("list exits 0 and gives each token's row status", rc == 0 and status.get("400") == "row"
             and status.get("900") == "unresolved")
        write(r11, ".claude/rules/b.md", "Third 77 here.\n")
        rc, out, _ = run(r11, "list", ".claude/rules/b.md")
        case("list reports a token with neither as none", rc == 0 and "none" in out)
        write(r11, "outside.md", "Out 55 of scope.\n")
        rc, out, _ = run(r11, "list", "outside.md")
        case("list ignores the scope paths", rc == 0 and "55" in out)

        # ---- changed_lines directly --------------------------------------------------
        r13 = mkrepo(tmp, "c13", base)
        write(r13, ".claude/rules/a.md", "Intro line.\n\nAdded.\n")
        write(r13, ".claude/rules/u.md", "u1\nu2\n")
        sc = en.load_scope(os.path.join(r13, ".claude"))
        ch = en.changed_lines(r13, "HEAD", sc)
        case("changed_lines: added side of the diff plus every line of an untracked in-scope file",
             ch.get(".claude/rules/a.md") == {2, 3} and ch.get(".claude/rules/u.md") == {1, 2})
        case("changed_lines: an untracked file is whole", ch.get(".claude/rules/u.md") == {1, 2})
        case("changed_lines: staged mode drops the untracked file",
             ".claude/rules/u.md" not in en.changed_lines(r13, "HEAD", sc, staged=True))
        case("changed_lines: paths narrows",
             list(en.changed_lines(r13, "HEAD", sc, paths=[".claude/rules/u.md"])) == [".claude/rules/u.md"])
    finally:
        _rm(tmp)

    # ---- live: scope covers both manifests --------------------------------------------
    man_paths = _manifest_paths()
    if man_paths is None:
        print("SKIP live cases: the redesign's scratch manifests are absent in this checkout")
    else:
        live_scope = en.load_scope(CLAUDE)
        case("live: the instruction_scope globs load", not live_scope.invalid)
        missing = [p for p in man_paths if not en.in_scope(live_scope, p)]
        case("live: the scope globs cover every path in both pass manifests (%d paths)" % len(man_paths),
             not missing)
        if missing:
            print("  uncovered: " + ", ".join(missing[:5]))
        for label, ok in _parity_cases(en):
            case(label, ok)

    failures = []
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


def _pass_dirs():
    base = os.path.join(CLAUDE, "scratch", "drive_redesign")
    return (os.path.join(base, "inventory"), os.path.join(base, "pass3"))


def _manifest_paths():
    inv, p3 = _pass_dirs()
    m1 = os.path.join(inv, "manifest.json")
    m3 = os.path.join(p3, "sx_numbers", "manifest.json")
    if not (os.path.isfile(m1) and os.path.isfile(m3) and os.path.isfile(os.path.join(inv, "extract_numbers.py"))
            and os.path.isfile(os.path.join(p3, "sx_words.py"))):
        return None
    out = []
    for m in (m1, m3):
        with open(m, encoding="utf-8") as fh:
            for files in json.load(fh).values():
                out.extend(f["path"] for f in files)
    return out


def _parity_cases(en):
    """Run both pass scripts over every manifest file at HEAD in a scratch tree and compare
    their tokens per line with the promoted tokenizer's."""
    inv, p3 = _pass_dirs()
    with open(os.path.join(inv, "manifest.json"), encoding="utf-8") as fh:
        man1 = json.load(fh)
    with open(os.path.join(p3, "sx_numbers", "manifest.json"), encoding="utf-8") as fh:
        man3 = json.load(fh)
    tmp = tempfile.mkdtemp(prefix="extract_numbers_parity_")
    try:
        texts = {}
        for files in list(man1.values()) + list(man3.values()):
            for f in files:
                p = f["path"]
                if p in texts:
                    continue
                r = subprocess.run(["git", "-C", REPO, "show", "HEAD:" + p], capture_output=True)
                if r.returncode == 0:
                    texts[p] = r.stdout.decode("utf-8")
                    dest = os.path.join(tmp, p)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(texts[p])
        merged = {}
        for cl, files in list(man1.items()) + [("sx_" + k, v) for k, v in man3.items()]:
            merged[cl] = [{"path": f["path"]} for f in files if f["path"] in texts]
        d1 = os.path.join(tmp, ".claude", "scratch", "drive_redesign", "inventory")
        d3 = os.path.join(tmp, ".claude", "scratch", "drive_redesign", "pass3")
        os.makedirs(d1)
        os.makedirs(os.path.join(d3, "sx_numbers"))
        with open(os.path.join(d1, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(merged, fh)
        sx = {cl: [{"path": f["path"]} for f in files if f["path"] in texts] for cl, files in man3.items()}
        with open(os.path.join(d3, "sx_numbers", "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(sx, fh)
        shutil.copy(os.path.join(p3, "sx_words.py"), d3)
        subprocess.run([sys.executable, os.path.join(inv, "extract_numbers.py")], cwd=tmp, check=True, capture_output=True)
        subprocess.run([sys.executable, os.path.join(d3, "sx_words.py")], cwd=tmp, check=True, capture_output=True)

        theirs_d = {}
        for name in os.listdir(d1):
            if name.startswith("numbers_") and name.endswith(".jsonl"):
                with open(os.path.join(d1, name), encoding="utf-8") as fh:
                    for l in fh:
                        r = json.loads(l)
                        theirs_d.setdefault((r["path"], r["line"]), []).extend(r["tokens"])
        theirs_w = {}
        with open(os.path.join(d3, "sx_numbers", "word_candidates.jsonl"), encoding="utf-8") as fh:
            for l in fh:
                r = json.loads(l)
                theirs_w.setdefault((r["path"], r["line"]), []).append(r["match"])
        # a path listed in two clusters is read twice by a pass script; the promoted tokenizer reads it once
        seen1 = {}
        for cl, files in merged.items():
            for f in files:
                seen1[f["path"]] = seen1.get(f["path"], 0) + 1
        mine_d, mine_w = {}, {}
        for p, t in texts.items():
            for ln, toks in en.digit_tokens(p, t).items():
                mine_d[(p, ln)] = toks * seen1.get(p, 1)
        sx_paths = {f["path"] for files in sx.values() for f in files}
        seen3 = {}
        for files in sx.values():
            for f in files:
                seen3[f["path"]] = seen3.get(f["path"], 0) + 1
        for p in sx_paths:
            for ln, toks in en.word_tokens(p, texts[p]).items():
                mine_w[(p, ln)] = toks * seen3.get(p, 1)
        cases = []
        cases.append(("live: digit tokens match pass 1 on every line of %d manifest files at HEAD (%d lines)"
                      % (len(texts), len(theirs_d)),
                      theirs_d == mine_d))
        if theirs_d != mine_d:
            diff = [k for k in set(theirs_d) | set(mine_d) if theirs_d.get(k) != mine_d.get(k)][:3]
            print("  digit diff sample: " + repr([(k, theirs_d.get(k), mine_d.get(k)) for k in diff]))
        # the promoted pattern drops bare one/single and a once that opens a clause, and adds the only/exactly
        # frames; a frame covers the token the pass-3 scan found inside it, so compare the rest
        bare = re.compile(r"(?:one|single)\b", re.I)
        frame = re.compile(r"(?:only|exactly)\s+", re.I)
        conj_once = re.compile(r"\bonce\s+(?:%s)\b" % en.CLAUSE_OPENERS, re.I)

        def unframed(k, ws):
            ws = [w for w in ws if not bare.match(w)]
            line = texts[k[0]].split("\n")[k[1] - 1] if k[0] in texts else ""
            for _ in conj_once.findall(line):
                if "once" in [w.lower() for w in ws]:
                    ws.remove(next(w for w in ws if w.lower() == "once"))
            for f in mine_w.get(k, []):
                if frame.match(f):
                    inner = frame.sub("", f, count=1).lower()
                    hit = next((w for w in ws if inner.startswith(w.lower())), None)
                    if hit is not None:
                        ws.remove(hit)
            return ws
        theirs_w = {k: v for k, v in ((k, unframed(k, ws)) for k, ws in theirs_w.items()) if v}
        mine_w = {k: v for k, v in ((k, [w for w in ws if not frame.match(w)]) for k, ws in mine_w.items()) if v}
        cases.append(("live: word tokens match the pass-3 word scan on every line (%d lines)" % len(theirs_w),
                      theirs_w == mine_w))
        if theirs_w != mine_w:
            diff = [k for k in set(theirs_w) | set(mine_w) if theirs_w.get(k) != mine_w.get(k)][:3]
            print("  word diff sample: " + repr([(k, theirs_w.get(k), mine_w.get(k)) for k in diff]))
        return cases
    finally:
        _rm(tmp)


if __name__ == "__main__":
    import pytest
    result = pytest.main([__file__, "-q", "-s"])
    sys.exit(result or main())
