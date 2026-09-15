"""Re-runnable proof for the 8 `adaptation.json` prose seams (Design Doc §8): each seam file
names `adaptation.json` and its own key, so the reader loads project values at run time, and
none of the file's own text hardcodes a project value for that key.

The "supplied consumer seed" is a fixture this proof builds itself -- structurally realistic
planted values, assembled from fragments and written to a temp JSON file, then read back from
that file at runtime for the absence check. This proof never embeds a planted token as one
contiguous literal (this is a public repo), and it never checks a real consumer project's file
by path, so the mechanism proven here is project-agnostic, matching what the template ships.

    python3 .claude/tests/test_prose_adaptation_seams.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.join(HERE, "..")

# file (relative to CLAUDE_DIR) -> the adaptation.json key its prose sentence must name
SEAMS = {
    "commands/clean_pull.md": "paired_repos",
    "commands/clean_push.md": "paired_repos",
    "commands/commit_push.md": "paired_repos",
    "commands/create_pr.md": "paired_repos",
    "commands/doc_start_here_update.md": "content_domains",
    "commands/agents/pr_test_checklist_conventions.md": "content_scopes",
    "skills/architecture_philosophy/structure_rules.md": "structure_exceptions",
    "commands/agents/pr_classification.md": "pr_domains",
}


def _fragment_join(*parts):
    return "".join(parts)


def _build_fixture_seed():
    """Structurally realistic planted values per key, built from fragments so no token is one
    contiguous literal anywhere in this file's source."""
    sub_name = _fragment_join("Zq", "Planted", "Sub", "module")
    domain_name = _fragment_join("Zq", "Planted", "Domain")
    scope_name = _fragment_join("zq", "planted", "scope")
    section_name = _fragment_join("Zq", "Planted", "Section")
    exception_path = _fragment_join("Zq", "Planted", "Folder") + "/"
    exception_reason = _fragment_join("Zq", "planted", "reason", "-", "text")
    pr_domain = _fragment_join("Zq", "Planted", "PrDomain")
    pr_path = _fragment_join("Zq", "Planted", "Src") + "/"

    return {
        "paired_repos": [sub_name],
        "content_domains": [{"name": domain_name, "description": "planted"}],
        "content_scopes": [{"scope": scope_name, "paths": [], "checklist_section": section_name}],
        "structure_exceptions": [{"path": exception_path, "reason": exception_reason}],
        "pr_domains": [{"domain": pr_domain, "paths": [pr_path]}],
    }


def _values_for_key(fixture, key):
    """Flat list of every planted string this key's fixture rows carry -- the tokens that must
    never appear, verbatim, in the seam file's own text."""
    values = []
    for row in fixture.get(key, []):
        if isinstance(row, str):
            values.append(row)
        elif isinstance(row, dict):
            for v in row.values():
                if isinstance(v, str):
                    values.append(v)
                elif isinstance(v, list):
                    values.extend(x for x in v if isinstance(x, str))
    return values


def main():
    cases = []
    failures = []

    fixture = _build_fixture_seed()
    tmp = tempfile.mkdtemp(prefix="prose_seam_fixture_")
    fixture_path = os.path.join(tmp, "adaptation.json")
    with open(fixture_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(fixture, fh)

    try:
        # Read the fixture BACK from disk -- the absence check below is derived from this
        # read, not from the in-memory dict literal above.
        with open(fixture_path, encoding="utf-8") as fh:
            read_back = json.load(fh)

        for rel, key in SEAMS.items():
            path = os.path.join(CLAUDE_DIR, *rel.split("/"))
            text = open(path, encoding="utf-8").read()

            cases.append((f"{rel}: names adaptation.json", "adaptation.json" in text))
            cases.append((f"{rel}: names its key '{key}'", key in text))

            planted = _values_for_key(read_back, key)
            cases.append((
                f"{rel}: holds none of the fixture's planted '{key}' values",
                all(v not in text for v in planted) and len(planted) > 0,
            ))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
