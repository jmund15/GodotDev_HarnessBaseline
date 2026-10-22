#!/usr/bin/env python3
"""Proof for tools/escape_log.py -- the append-only record of defects found after they landed.

The load-bearing cases are the ones the log's value rests on: a row must carry the triage the
commit actually recorded, the checks that were on record for the path, and the sensitivity-tag
names the path touches, because the review yield rows key on those three. The refusals matter as much
as the writes: a row over a typo'd mandate key or a sha that is not a commit is a false record
that later reads as evidence.

Every case runs against a planted repo (git init + one commit) with a planted adaptation.json
catalog and a planted review ledger, so no case reads the live tree. The documented `/self_evaluate` and
debugging invocations are fed verbatim.

Run: python3 .claude/tests/test_escape_log.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "escape_log.py")
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import review_ledger  # noqa: E402
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

TAGS = [{"id": "tool-script", "paths": ["**/*.py"], "sets": {"reach": "shared"}, "source": "fixture"},
        {"id": "harness-guard-logic", "paths": ["hooks/*.py"], "sets": {"reach": "shared"}, "source": "fixture"}]
MANDATES = {"cq.C": {"home": "commands/checklists/code_quality.md#Compliance", "interaction": False},
            "tq": {"home": "commands/checklists/test_quality.md", "interaction": False}}

HOMES = {"commands/checklists/code_quality.md": "# Code quality\n\n## Compliance\n\n- c1\n",
         "commands/checklists/test_quality.md": "# Test quality\n\n- t1\n"}

TRAILER = ("Triage: reach=shared reversibility=residue design=settled grounding=verified "
           "oracle=gate size=milestones")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _git(root, *args):
    return subprocess.run(["git"] + list(args), cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _setup_git(root, *args):
    """A fixture git step: a failure raises here, at the step, naming git's own message."""
    result = _git(root, *args)
    if result.returncode != 0:
        raise RuntimeError("fixture git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result


def plant(tmp, changed=("pkg/a.py",), trailer=TRAILER, tags=TAGS, mandates=MANDATES, ledger=None):
    """A repo with one commit, an adaptation catalog and (optionally) a review ledger."""
    root = os.path.join(tmp, "repo")
    catalog = {"sensitivity_tags": tags}
    if mandates is not None:
        catalog["review_mandates"] = mandates
    _write(os.path.join(root, ".claude", "skills", "project_subsystems", "adaptation.json"),
           json.dumps(catalog, indent=1))
    for rel, text in HOMES.items():
        _write(os.path.join(root, ".claude", rel), text)
    os.makedirs(os.path.join(root, ".claude", "logs"), exist_ok=True)
    if ledger is not None:
        _write(os.path.join(root, ".claude", "logs", "review_ledger.jsonl"),
               "".join(json.dumps(row) + "\n" for row in ledger))
    _setup_git(root, "init", "-q")
    _setup_git(root, "config", "user.email", "fixture@example.invalid")
    _setup_git(root, "config", "user.name", "Fixture")
    _setup_git(root, "config", "commit.gpgsign", "false")
    for rel in changed:
        _write(os.path.join(root, rel), "x = 1\n")
    if changed:
        _setup_git(root, "add", "--", *changed)
    message = "subject line\n\nbody\n\n%s\n" % trailer if trailer else "subject line\n"
    path = os.path.join(tmp, "commit_message.txt")
    _write(path, message)
    extra = [] if changed else ["--allow-empty"]
    _setup_git(root, "commit", "-q", *extra, "-F", path)
    return root, _setup_git(root, "rev-parse", "HEAD").stdout.strip()


def run(argv, root, log=None, cwd=None, script=MOD, project=True):
    env = dict(os.environ, HARNESS_REVIEW_CLAUDE_DIR=os.path.join(root, ".claude"))
    if project:
        env["CLAUDE_PROJECT_DIR"] = root
    else:
        env.pop("CLAUDE_PROJECT_DIR", None)
    for name in ("HARNESS_ESCAPE_LOG", "HARNESS_REVIEW_LEDGER"):
        env.pop(name, None)
    if log:
        env["HARNESS_ESCAPE_LOG"] = log
    return subprocess.run([sys.executable, script] + argv, cwd=cwd or root, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def rows_of(root):
    path = os.path.join(root, ".claude", "escape_log.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def build(tmp):
    """One planted repo and one run of every case against it."""
    root, sha = plant(tmp)
    log = os.path.join(root, ".claude", "escape_log.jsonl")
    cases = []

    def check(name, ok, detail=""):
        cases.append((name, ok, detail))

    import escape_log
    layer_dir = os.path.join(tmp, "layer_only", ".claude")
    _write(os.path.join(layer_dir, "skills", "project_subsystems", "adaptation.json"), "{}")
    _write(os.path.join(layer_dir, "skills", "project_subsystems", "adaptation.coding.defaults.json"),
           json.dumps({"review_mandates": {
               "cq.C": {"home": "commands/checklists/code_quality.md#Compliance (C)", "interaction": False},
               "architecture-sweep": {"home": "commands/agents/session_audit_agents.md#sa-architecture-sweep",
                                      "interaction": False}}}))
    layer_keys = escape_log.mandate_keys(layer_dir)
    check("`mandate_keys` over an empty project file lists the coding layer keys",
          {"cq.C", "architecture-sweep"} <= layer_keys, str(sorted(layer_keys)))

    ok = run(["add", "--sha", sha, "--writer", "self_evaluate", "--should-have-caught", "none"], root)
    check("an escape row is written for a landed sha", ok.returncode == 0, ok.stderr.strip())
    written = rows_of(root)
    check("one row lands, with the sha, the writer and the key", len(written) == 1
          and written[0]["sha"] == sha and written[0]["writer"] == "self_evaluate"
          and written[0]["shouldHaveCaught"] == "none", json.dumps(written))
    row = written[0] if written else {}
    check("`--paths` defaults to the files the sha changed", row.get("paths") == ["pkg/a.py"], str(row.get("paths")))
    check("`--author-model` defaults to unknown and `--tier` to inline",
          row.get("authorModel") == "unknown" and row.get("tier") == "inline", json.dumps(row))
    check("`triage` carries the sha's Triage: trailer",
          row.get("triage") == "reach=shared reversibility=residue design=settled grounding=verified "
                                "oracle=gate size=milestones", str(row.get("triage")))
    check("`domain` carries the sensitivity-tag names over the paths",
          row.get("domain") == ["tool-script"], str(row.get("domain")))
    check("`checksRan` is empty when no ledger row names the path", row.get("checksRan") == [], str(row.get("checksRan")))
    check("a row is identified by an id", row.get("id") not in (None, ""), str(row.get("id")))

    # -- derived from the ledger: review_ledger's coverage rule at the sha's blobs ------------------
    root2, sha2 = plant(os.path.join(tmp, "led"))
    blob = _setup_git(root2, "rev-parse", "%s:pkg/a.py" % sha2).stdout.strip()
    claude2 = os.path.join(root2, ".claude")
    from pathlib import Path as _P
    rule = review_ledger.rule_hash("cq.C", _P(claude2), MANDATES)
    covering = {"v": 1, "ts": "2026-01-01T00:00:00Z", "stage": "candidate", "subject": "session:s1",
                "mandate": "cq.C", "ruleHash": rule, "head": sha2, "mergeBase": None, "reviewer": "script:x",
                "files": {"pkg/a.py": blob}, "outcome": "clean"}
    ledger = [dict(covering, id="run1:s1:cq.C"),
              dict(covering, id="run1:stale:cq.C", files={"pkg/a.py": "deadbeef"}),
              dict(covering, id="run1:s2:tq", mandate="tq", files={"pkg/other.py": blob}),
              dict(covering, id="run1:s3:cq.C", outcome="incomplete"),
              dict(covering, id="run1:oldrule:cq.C", ruleHash="sha256:old")]
    _write(os.path.join(claude2, "logs", "review_ledger.jsonl"), "".join(json.dumps(r) + "\n" for r in ledger))
    run(["add", "--sha", sha2, "--writer", "debugging", "--should-have-caught", "cq.C"], root2)
    got = rows_of(root2)
    check("`checksRan` lists the rows that cover the path at the sha's blob, and only those",
          got and got[0]["checksRan"] == ["run1:s1:cq.C"], json.dumps(got and got[0]["checksRan"]))

    # -- one path home: --repo, resolved through review_ledger, per checkout ------------------------
    root8, sha8 = plant(os.path.join(tmp, "wt"))
    tree = os.path.join(tmp, "wt", "tree")
    _setup_git(root8, "worktree", "add", "-q", "--detach", tree, sha8)
    for rel, text in list(HOMES.items()) + [("skills/project_subsystems/adaptation.json",
                                             json.dumps({"sensitivity_tags": TAGS, "review_mandates": MANDATES}))]:
        _write(os.path.join(tree, ".claude", rel), text)
    elsewhere = run(["add", "--repo", tree, "--sha", sha8, "--writer", "debugging", "--should-have-caught", "none"],
                    root8, cwd=tmp)
    tree_log = os.path.join(tree, ".claude", "escape_log.jsonl")
    check("`--repo` names the checkout: a worktree's row lands in its own log, not the project dir's",
          elsewhere.returncode == 0 and os.path.isfile(tree_log) and rows_of(root8) == [],
          elsewhere.stdout + elsewhere.stderr)
    from pathlib import Path as _P2
    check("...the path review_ledger resolves for that checkout",
          os.path.normcase(os.path.normpath(str(review_ledger.escape_log_path(_P2(tree))))) ==
          os.path.normcase(os.path.normpath(tree_log)), str(review_ledger.escape_log_path(_P2(tree))))
    overlay = os.path.join(root8, ".claude", "logs", "escape_overlay.jsonl")
    overlay_rows = [json.loads(l) for l in open(overlay, encoding="utf-8") if l.strip()] \
        if os.path.isfile(overlay) else []
    tracked_rows = [json.loads(l) for l in open(tree_log, encoding="utf-8") if l.strip()] \
        if os.path.isfile(tree_log) else []
    check("...and the same row lands in the shared overlay beside the ledger every worktree reads",
          len(overlay_rows) == 1 and overlay_rows == tracked_rows, "overlay=%s tracked=%s" % (overlay_rows, tracked_rows))
    primary_escapes, _problem = review_ledger.read_escapes(_P2(root8))
    check("...so the primary checkout's revocation reads it before the row is merged there",
          rows_of(root8) == [] and [r.get("id") for r in primary_escapes or []] == [r.get("id") for r in tracked_rows],
          repr(primary_escapes))
    bare = run(["add", "--repo", root8, "--sha", sha8, "--writer", "debugging", "--should-have-caught", "none"],
               root8, cwd=tmp, project=False)
    check("`--repo` alone resolves the sha and the log with no CLAUDE_PROJECT_DIR",
          bare.returncode == 0 and len(rows_of(root8)) == 1, bare.stdout + bare.stderr)

    # -- triage fallbacks ------------------------------------------------------------------------
    root3, sha3 = plant(os.path.join(tmp, "noT"), trailer=None)
    run(["add", "--sha", sha3, "--writer", "debugging", "--should-have-caught", "none"], root3)
    got3 = rows_of(root3)
    check("a sha with no trailer and no task record records `unrecorded`",
          got3 and got3[0]["triage"] == "unrecorded", str(got3 and got3[0].get("triage")))

    tasks = os.path.join(root3, ".claude", "logs", "tasks")
    _write(os.path.join(tasks, "drive-x.json"),
           json.dumps({"task": "drive-x", "triage": {"fields": {"reach": "shared", "reversibility": "residue",
                                                                "design": "settled", "grounding": "verified",
                                                                "oracle": ["gate", "new-test"], "size": "milestones"}},
                       "decisions": [{"text": "landed in %s" % sha3}]}))
    run(["add", "--sha", sha3, "--writer", "debugging", "--should-have-caught", "none"], root3)
    got3 = rows_of(root3)
    last3 = got3[-1] if got3 else {}
    check("a task record naming the sha supplies the triage",
          str(last3.get("triage", "")).startswith("reach=shared reversibility=residue")
          and str(last3.get("triage", "")).endswith("oracle=gate+new-test size=milestones"),
          str(last3.get("triage")))

    # -- domain when no tag matches ---------------------------------------------------------------
    root4, sha4 = plant(os.path.join(tmp, "untag"), changed=("notes.txt",))
    run(["add", "--sha", sha4, "--writer", "debugging", "--should-have-caught", "none"], root4)
    got4 = rows_of(root4)
    check("a path no tag matches records the domain `untagged`",
          got4 and got4[0]["domain"] == ["untagged"], str(got4 and got4[0].get("domain")))
    try:
        import triage
        claude4 = os.path.join(root4, ".claude")
        keys = (triage.domain_keys(["pkg/a.py"], claude4), triage.domain_keys(["notes.txt"], claude4))
    except Exception as exc:  # noqa: BLE001 -- a missing function is this case's failure
        keys = repr(exc)
    check("`triage.domain_keys` is the one domain function a yield row calls",
          keys == (["tool-script"], ["untagged"]), str(keys))
    try:
        import escape_log
        escape_log._REPO = root4
        alias = escape_log.domain_keys(["pkg/a.py"])
    except Exception as exc:  # noqa: BLE001
        alias = repr(exc)
    check("...and `escape_log.domain_keys` answers the same for its checkout", alias == ["tool-script"], str(alias))

    # -- --paths are stored repo-relative, as coverage clause 5 compares them ----------------------
    root9, sha9 = plant(os.path.join(tmp, "norm"))
    top9 = _setup_git(root9, "rev-parse", "--show-toplevel").stdout.strip()
    for cited in (top9 + "/pkg/a.py", "./pkg/a.py", top9.replace("/", "\\") + "\\pkg\\a.py"):
        run(["add", "--sha", sha9, "--writer", "debugging", "--should-have-caught", "none", "--paths", cited], root9)
    check("an absolute, `./` or backslash --paths value is stored repo-relative",
          [r["paths"] for r in rows_of(root9)] == [["pkg/a.py"]] * 3, json.dumps([r["paths"] for r in rows_of(root9)]))

    # -- --base-ref reaches the coverage check -----------------------------------------------------
    based = run(["add", "--sha", sha9, "--writer", "debugging", "--should-have-caught", "none",
                 "--base-ref", "HEAD"], root9)
    check("`--base-ref` is accepted and a row lands", based.returncode == 0 and len(rows_of(root9)) == 4,
          based.stdout + based.stderr)

    # -- the lock spans only the id and the append --------------------------------------------------
    import escape_log as el
    held = {}
    real = (el._checks_ran, el._triage_text)
    lock = os.path.join(root9, ".claude", "escape_log.jsonl.lock")

    def probe(name, fn):
        def wrapped(*a, **k):
            held[name] = os.path.exists(lock)
            return fn(*a, **k)
        return wrapped

    el._checks_ran, el._triage_text = probe("checksRan", real[0]), probe("triage", real[1])
    saved = dict(os.environ)
    os.environ.update(HARNESS_REVIEW_CLAUDE_DIR=os.path.join(root9, ".claude"), CLAUDE_PROJECT_DIR=root9)
    for name in ("HARNESS_ESCAPE_LOG", "HARNESS_REVIEW_LEDGER"):
        os.environ.pop(name, None)
    try:
        code = el.main(["add", "--repo", root9, "--sha", sha9, "--writer", "debugging", "--should-have-caught", "none"])
    finally:
        el._checks_ran, el._triage_text = real
        os.environ.clear()
        os.environ.update(saved)
    check("the evidence fields are computed before the log's lock is taken",
          code == 0 and held == {"checksRan": False, "triage": False}, "%s %s" % (code, held))

    # -- explicitly passed fields -----------------------------------------------------------------
    root5, sha5 = plant(os.path.join(tmp, "flags"))
    run(["add", "--sha", sha5[:8], "--writer", "review:pr-review", "--should-have-caught", "tq",
         "--paths", "pkg/a.py", "--author-model", "fixture-model", "--tier", "executor"],
        root5)
    got5 = rows_of(root5)
    check("a short sha resolves, and the passed fields are stored",
          got5 and got5[0]["sha"] == sha5 and got5[0]["tier"] == "executor"
          and got5[0]["authorModel"] == "fixture-model" and got5[0]["writer"] == "review:pr-review",
          json.dumps(got5[0] if got5 else {}))

    # -- refusals ----------------------------------------------------------------------------------
    bad_key = run(["add", "--sha", sha, "--writer", "debugging", "--should-have-caught", "cq.Z"], root)
    check("an unknown mandate key is refused (exit 2)", bad_key.returncode == 2, bad_key.stdout + bad_key.stderr)
    check("...and writes no row", len(rows_of(root)) == 1, str(len(rows_of(root))))
    bad_sha = run(["add", "--sha", "0" * 40, "--writer", "debugging", "--should-have-caught", "none"], root)
    check("a sha that is not a commit is refused (exit 2)", bad_sha.returncode == 2, bad_sha.stderr)
    root_empty, sha_empty = plant(os.path.join(tmp, "empty"), changed=())
    empty = run(["add", "--sha", sha_empty, "--writer", "debugging", "--should-have-caught", "none"], root_empty)
    check("an empty path list is refused (exit 2)", empty.returncode == 2 and len(rows_of(root_empty)) == 0,
          empty.stdout + empty.stderr)
    named = run(["add", "--sha", sha_empty, "--writer", "debugging", "--should-have-caught", "none",
                 "--paths", "pkg/a.py"], root_empty)
    check("...while naming a path on that sha lands", named.returncode == 0, named.stderr)
    bad_writer = run(["add", "--sha", sha, "--writer", "somebody", "--should-have-caught", "none"], root)
    check("a writer outside the three forms is refused (exit 2)", bad_writer.returncode == 2, bad_writer.stderr)
    no_catalog = os.path.join(tmp, "nocat")
    root6, sha6 = plant(no_catalog, mandates=None)
    refused = run(["add", "--sha", sha6, "--writer", "debugging", "--should-have-caught", "cq.C"], root6)
    check("a mandate key with no catalog to declare it is refused (exit 2)", refused.returncode == 2, refused.stderr)
    accepted = run(["add", "--sha", sha6, "--writer", "debugging", "--should-have-caught", "none"], root6)
    check("...while `none` still lands", accepted.returncode == 0 and len(rows_of(root6)) == 1, accepted.stderr)

    # -- a write that fails ------------------------------------------------------------------------
    blocker = os.path.join(tmp, "blocker")
    _write(blocker, "not a directory\n")
    failed = run(["add", "--sha", sha, "--writer", "debugging", "--should-have-caught", "none"],
                 root, log=os.path.join(blocker, "escape_log.jsonl"))
    check("a write that fails exits 1 naming the path",
          failed.returncode == 1 and "escape_log.jsonl" in failed.stderr, failed.stderr)

    # -- list ----------------------------------------------------------------------------------------
    listed = run(["list", "--json"], root)
    try:
        parsed = [json.loads(line) for line in listed.stdout.splitlines() if line.strip()]
    except ValueError:
        parsed = []
    check("`list --json` prints every row", listed.returncode == 0 and len(parsed) == len(rows_of(root)),
          listed.stdout + listed.stderr)
    by_path = run(["list", "--path", "pkg/a.py", "--json"], root)
    check("`list --path` filters on the recorded path", by_path.returncode == 0 and by_path.stdout.strip() != "",
          by_path.stdout + by_path.stderr)
    none_path = run(["list", "--path", "pkg/nope.py", "--json"], root)
    check("...and prints nothing for a path no row names", none_path.returncode == 0 and none_path.stdout.strip() == "", none_path.stdout + none_path.stderr)
    by_tier = run(["list", "--tier", "inline", "--json"], root)
    check("`list --tier` filters", by_tier.returncode == 0 and by_tier.stdout.count("\n") == len(rows_of(root)),
          by_tier.stdout)
    none_tier = run(["list", "--tier", "architect", "--json"], root)
    check("...and prints nothing for a tier no row holds", none_tier.returncode == 0 and none_tier.stdout.strip() == "", none_tier.stdout + none_tier.stderr)
    by_model = run(["list", "--model", "unknown", "--json"], root)
    check("`list --model` filters", by_model.returncode == 0 and by_model.stdout.strip() != "", by_model.stdout)
    none_model = run(["list", "--model", "fixture-model", "--json"], root)
    check("...and prints nothing for a model no row names", none_model.returncode == 0 and none_model.stdout.strip() == "", none_model.stdout + none_model.stderr)
    future = run(["list", "--since", "2999-01-01", "--json"], root)
    check("`list --since` drops older rows", future.returncode == 0 and future.stdout.strip() == "", future.stdout + future.stderr)
    old = run(["list", "--since", "2000-01-01", "--json"], root)
    check("...and keeps newer ones", old.stdout.strip() != "", old.stdout)
    human = run(["list"], root)
    check("`list` without --json prints one line per row",
          human.returncode == 0 and len([l for l in human.stdout.splitlines() if l.strip()]) == len(rows_of(root)),
          human.stdout)

    # -- the documented /self_evaluate and debugging invocations --------------------------------------
    lit = os.path.join(tmp, "literal")
    root7, sha7 = plant(lit)
    for writer in ("self_evaluate", "debugging"):
        literal = ["add", "--writer", writer, "--sha", sha7, "--should-have-caught", "none"]
        got = run(literal, root7, cwd=REPO_ROOT, script=".claude/tools/escape_log.py")
        check("the %s literal exits 0 against a planted repo" % writer, got.returncode == 0, got.stderr)
    check("...and both landed, with the planted sha", [r["writer"] for r in rows_of(root7)] ==
          ["self_evaluate", "debugging"], json.dumps([r["writer"] for r in rows_of(root7)]))

    # -- a lock that cannot be taken writes nothing -------------------------------------------------
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    import _file_lock  # noqa: E402
    root11, sha11 = plant(os.path.join(tmp, "locked"))
    lock_handle = _file_lock.acquire(os.path.join(root11, ".claude", "escape_log.jsonl.lock"), 2.0)
    try:
        blocked = run(["add", "--sha", sha11, "--writer", "debugging", "--should-have-caught", "none"], root11)
    finally:
        _file_lock.release(lock_handle)
    check("a lock held past the wait exits 1 and writes no row",
          lock_handle is not None and blocked.returncode == 1 and rows_of(root11) == [],
          "held=%s rc=%s rows=%s %s" % (lock_handle is not None, blocked.returncode, rows_of(root11), blocked.stderr))

    # -- a row names the review call that found it -------------------------------------------------
    root10, sha10 = plant(os.path.join(tmp, "foundby"))
    found = run(["add", "--sha", sha10, "--writer", "review:candidate", "--should-have-caught", "none",
                 "--found-by", "call-1"], root10)
    check("--found-by is stored as foundBy on the row",
          found.returncode == 0 and [r.get("foundBy") for r in rows_of(root10)] == ["call-1"],
          "rc=%s %s %s" % (found.returncode, found.stderr, rows_of(root10)))

    root_none, sha_none = plant(os.path.join(tmp, "oracle-none"), trailer=TRAILER.replace("oracle=gate", "oracle=none"))
    result_none = run(["add", "--sha", sha_none, "--writer", "debugging", "--should-have-caught", "none"], root_none)
    check("an escape trailer prints oracle=none for an empty oracle",
          result_none.returncode == 0 and "oracle=none" in str(rows_of(root_none)[0].get("triage")), result_none.stderr)

    return cases


def main():
    tmp = tempfile.mkdtemp(prefix="escape_log_")
    try:
        cases = build(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [c for c in cases if not c[1]]
    for name, ok, detail in cases:
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  |  " + detail))
    print("\n%d/%d cases pass" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
