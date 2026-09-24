#!/usr/bin/env python3
"""Re-runnable proof for tools/audit_baseline.py (baseline-repo root).

    python3 tests/test_audit_baseline.py

Every planted token is assembled at runtime from fragments so no committed line
in this file matches either scanner it exercises.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools" / "audit_baseline.py"

if not AUDIT.exists():
    print("CANNOT-RUN: %s is missing" % AUDIT, file=sys.stderr)
    sys.exit(2)


def _load_audit():
    # audit_baseline.py does `import gen_manifest as gm` (a plain sibling import) —
    # running it as a script auto-adds its own directory to sys.path; loading it via
    # spec_from_file_location does not, so this proof adds it explicitly.
    tools_dir = str(AUDIT.parent)
    added = tools_dir not in sys.path
    if added:
        sys.path.insert(0, tools_dir)
    try:
        spec = importlib.util.spec_from_file_location("audit_baseline_under_test", AUDIT)
        if spec is None or spec.loader is None:
            print("CANNOT-RUN: %s could not be loaded" % AUDIT, file=sys.stderr)
            sys.exit(2)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            print("CANNOT-RUN: %s failed to import: %s" % (AUDIT, exc), file=sys.stderr)
            sys.exit(2)
        return module
    finally:
        if added:
            sys.path.remove(tools_dir)


def _run(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUDIT), "--strict"], cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _env(root: Path) -> dict:
    env = os.environ.copy()
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=cwd, env=_env(cwd),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    for current, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            target = Path(current) / name
            try:
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            except OSError:
                pass
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass


def _remove(path: Path) -> None:
    _make_writable(path)
    shutil.rmtree(path, ignore_errors=True)


def test_strict_exits_0_from_repo_root() -> None:
    result = _run(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_strict_exits_0_from_another_cwd() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="audit_baseline_cwd_"))
    try:
        result = _run(tmp)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        _remove(tmp)


def test_identity_scan_flags_a_planted_leak() -> None:
    """Wires an isolated fixture git repo through the real check_identity() —
    the real worktree's git index and template/ tree are never touched."""
    audit = _load_audit()
    # "SubsysNode" — fragment-built, and a two-word compound (S3 fix2's topology
    # compound-word rule requires >= 2 camelCase/snake/kebab words per token).
    planted_name = "S" + "u" + "b" + "sy" + "s" + "N" + "ode"

    profile = audit.identity.build_profile(
        subsystems=[{"id": planted_name, "paths": []}], template_tokens=set(),
    )
    digests_doc = {
        "version": 1,
        "sources": [{
            "label": "s3fixture1",
            "counts": {k: len(v) for k, v in profile.items()},
            "kinds": audit.identity.digest_kinds(profile),
        }],
    }

    tmp = Path(tempfile.mkdtemp(prefix="audit_baseline_identity_"))
    original_root, original_digests = audit.ROOT, audit.IDENTITY_DIGESTS
    try:
        repo = tmp / "repo"
        repo.mkdir(parents=True)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "fixture@example.invalid")
        _git(repo, "config", "user.name", "fixture")
        (repo / "notes.md").write_text(f"the {planted_name} identifier\n",
                                        encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        digests_path = tmp / "identity_digests.json"
        digests_path.write_text(json.dumps(digests_doc), encoding="utf-8", newline="\n")

        audit.ROOT = repo
        audit.IDENTITY_DIGESTS = digests_path
        findings = audit.Findings()
        audit.check_identity(findings)
        hits = [i for i in findings.items if i["check"] == "identity-scan"]
        assert hits, "expected an identity-scan finding for the planted leak"
        assert any("notes.md" in h["path"] for h in hits), hits
    finally:
        audit.ROOT, audit.IDENTITY_DIGESTS = original_root, original_digests
        _remove(tmp)


def test_scanner_flags_every_identity_form_in_the_corpus() -> None:
    """Every identity form the retired plaintext scanner flagged, plus the glued form it
    missed, must produce a hit from the current scan_tree; near misses must not. The
    comparison against that scanner itself ran once, at S3, from an author commit that was
    never published -- CI clones cannot read it, so the corpus keeps the expectations
    and drops the dependency. Every planted token is built from short fragments at runtime,
    so no committed line here matches the scanner."""
    audit = _load_audit()

    base1, base2 = "Push" + "in", "Pot" + "ions"
    pascal = base1 + base2                  # PascalCase
    lower = pascal.lower()                  # lowercase
    upper = pascal.upper()                  # UPPERCASE
    spaced = base1 + " " + base2            # spaced
    snake = base1.lower() + "_" + base2.lower()   # snake_case
    kebab = base1.lower() + "-" + base2.lower()   # kebab-case
    suffix = "Su" + "ite"
    concatenated = pascal + suffix          # inside a longer identifier

    abbrev = "P" + "P"
    not_abbrev = abbrev + "T"               # near miss -- must never hit
    contributor = "jm" + "und"              # the source contributor's user name

    generic_word = "gen" + "eric"           # matches no kind -- must never hit
    short_type_name = "Am" + "mo"           # 4 chars, below the topology floor

    # 616ea42's `identifier_leaks` never split camelCase within one glued alpha
    # run, so a name form written as a single unbroken run (pascal/lower/upper)
    # or split by a single non-letter separator (spaced/snake/kebab, via its
    # adjacent-segment-pair candidate) was flagged; a name glued to a THIRD word
    # inside one run (`concatenated`) was not -- that miss is exactly the S3
    # regression this fix adds coverage for, so it is checked as a "new must
    # flag" case below without requiring the old scanner to have caught it too.
    old_parity_form_lines = [pascal, lower, upper, spaced, snake, kebab]
    new_only_form_lines = [concatenated]
    non_name_lines = (
        [f"the {abbrev} value", f"the_{abbrev.lower()}_value", f"the-{abbrev.lower()}-value"]
        + [f"ask {contributor} directly"]
        + [
            f"path C:/Users/{contributor}/data", f"path C:\\Users\\{contributor}\\data",
            f"path /c/Users/{contributor}/data", f"path /home/{contributor}/data",
        ]
    )

    positive_lines = (
        [f"identifier {v} appears" for v in old_parity_form_lines + new_only_form_lines]
        + non_name_lines
    )
    miss_lines = [
        f"the {not_abbrev} token", f"a {generic_word} sentence", f"the {short_type_name} type",
    ]

    # A fixture consumer whose identity matches the corpus above, digested fresh.
    profile = audit.identity.build_profile(
        substitutions={"{{NAME}}": pascal}, abbreviations=[abbrev], home_users=[contributor],
    )
    digests_doc = {
        "version": 1,
        "sources": [{
            "label": "s3parityfixture",
            "counts": {k: len(v) for k, v in profile.items()},
            "kinds": audit.identity.digest_kinds(profile),
        }],
    }

    tmp = Path(tempfile.mkdtemp(prefix="audit_baseline_parity_"))
    try:
        repo = tmp / "repo"
        repo.mkdir(parents=True)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "fixture@example.invalid")
        _git(repo, "config", "user.name", "fixture")
        all_lines = positive_lines + miss_lines
        (repo / "notes.md").write_text("\n".join(all_lines) + "\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        digests_path = tmp / "identity_digests.json"
        digests_path.write_text(json.dumps(digests_doc), encoding="utf-8", newline="\n")

        hits = audit.identity.scan_tree(repo, digests_path)
        hit_lines = {h.line for h in hits}
        positive_line_numbers = {i for i, line in enumerate(all_lines, 1) if line in positive_lines}
        miss_line_numbers = {i for i, line in enumerate(all_lines, 1) if line in miss_lines}
        missed = positive_line_numbers - hit_lines
        assert not missed, [all_lines[i - 1] for i in sorted(missed)]
        assert not (hit_lines & miss_line_numbers), (hit_lines, miss_line_numbers)
    finally:
        _remove(tmp)


def test_secret_scan_flags_a_planted_secret_shape() -> None:
    """Plants a fake AWS-access-key SHAPE (never a real credential) in an isolated
    fixture template tree, run through the real check_secrets()."""
    audit = _load_audit()
    fake_key = "AKIA" + "0" * 16  # matches SECRET_PATTERNS' AWS-access-key shape

    tmp = Path(tempfile.mkdtemp(prefix="audit_baseline_secret_"))
    original_template = audit.TEMPLATE
    try:
        (tmp / ".claude").mkdir(parents=True)
        (tmp / ".claude" / "notes.md").write_text(f"key = {fake_key}\n",
                                                    encoding="utf-8", newline="\n")
        audit.TEMPLATE = tmp
        findings = audit.Findings()
        audit.check_secrets(findings)
        hits = [i for i in findings.items if i["check"] == "secret-scan"]
        assert hits, "expected a secret-scan finding for the planted key shape"
    finally:
        audit.TEMPLATE = original_template
        _remove(tmp)


def test_layer_closure_flags_a_pure_file_citing_a_godot_file() -> None:
    audit = _load_audit()
    tmp = Path(tempfile.mkdtemp(prefix="audit_baseline_closure_"))
    try:
        template = tmp / "template"
        tools = template / ".claude" / "tools"
        tools.mkdir(parents=True)
        shutil.copy(ROOT / "template" / ".claude" / "tools" / "layer_closure.py", tools / "layer_closure.py")
        (template / ".claude" / "commands").mkdir()
        (template / ".claude" / "commands" / "doc.md").write_text("Run `/gate`.\n", encoding="utf-8")
        (template / ".claude" / "commands" / "gate.md").write_text("# gate\n", encoding="utf-8")
        manifest = {"files": [{"path": ".claude/commands/doc.md", "layer": "pure", "sync": "auto"},
                              {"path": ".claude/commands/gate.md", "layer": "godot", "sync": "auto"},
                              {"path": ".claude/tools/layer_closure.py", "layer": "pure", "sync": "auto"}]}
        findings = audit.Findings()
        audit.check_layer_closure(findings, manifest, template)
        hits = [i for i in findings.items if i["check"] == "layer-closure"]
        assert [(h["severity"], h["path"]) for h in hits] == [("ERROR", ".claude/commands/doc.md")], hits
        manifest["files"][1]["layer"] = "pure"
        clean = audit.Findings()
        audit.check_layer_closure(clean, manifest, template)
        assert not clean.items, clean.items
    finally:
        _remove(tmp)


def test_staleness_accepts_an_offer_memory_row() -> None:
    audit = _load_audit()
    rel = next(r for _, r in audit.iter_template_files()
               if r.startswith(".claude/auto-memory/") and r != ".claude/auto-memory/MEMORY.md")
    layer = audit.gm.classify(rel)
    offer = audit.Findings()
    audit.check_manifest_staleness(offer, {"files": [{"path": rel, "layer": layer, "sync": "offer"}]})
    assert not [i for i in offer.items if i["check"] == "manifest-staleness"], offer.items
    stale = audit.Findings()
    audit.check_manifest_staleness(stale, {"files": [{"path": rel, "layer": layer, "sync": "auto"}]})
    assert [i["path"] for i in stale.items if i["check"] == "manifest-staleness"] == [rel], stale.items


def main() -> int:
    cases = [
        test_staleness_accepts_an_offer_memory_row,
        test_layer_closure_flags_a_pure_file_citing_a_godot_file,
        test_strict_exits_0_from_repo_root,
        test_strict_exits_0_from_another_cwd,
        test_identity_scan_flags_a_planted_leak,
        test_scanner_flags_every_identity_form_in_the_corpus,
        test_secret_scan_flags_a_planted_secret_shape,
    ]
    failures = []
    for case in cases:
        try:
            case()
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
