#!/usr/bin/env python3
"""Proof for tools/vault_read.py: a vault read types every file it could not read, so a sweep never reads clean by omission.

    python3 .claude/tests/test_vault_read.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def main():
    import vault_read

    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        texts, counts = vault_read.read_vault(vault, Path("DevProjects/Nope"))
        check("a missing root is typed absent, with nothing read",
              counts["absent"] is True and counts["read"] == 0 and texts == [] and not vault_read.verified(counts), counts)

        docs = vault / "DevProjects/Mine"
        (docs / "sub").mkdir(parents=True)
        (docs / "a.md").write_text("alpha note\n", encoding="utf-8")
        (docs / "sub" / "b.md").write_text("beta note\n", encoding="utf-8")
        (docs / "empty.md").write_text("", encoding="utf-8")
        (docs / "folder.md").mkdir()
        (docs / "other.txt").write_text("not markdown\n", encoding="utf-8")
        texts, counts = vault_read.read_vault(vault, Path("DevProjects/Mine"))
        check("every markdown file is read, a folder named like one is skipped, other suffixes are ignored",
              sorted(Path(p).name for p, _ in texts) == ["a.md", "b.md", "empty.md"] and counts["read"] == 3, counts)
        check("a fully read root is verified", vault_read.verified(counts), counts)

        real = vault_read.dehydrated
        vault_read.dehydrated = lambda path: Path(path).name == "b.md"
        try:
            texts, counts = vault_read.read_vault(vault, Path("DevProjects/Mine"))
        finally:
            vault_read.dehydrated = real
        check("a dehydrated placeholder is typed unattempted, listed, and never read",
              counts["unattempted"] == 1 and counts["unreadPaths"] == [str(docs / "sub" / "b.md")]
              and all(Path(p).name != "b.md" for p, _ in texts) and not vault_read.verified(counts), counts)

    check("the default vault root derives from the home directory, never a literal user path",
          vault_read.DEFAULT_VAULT == Path.home() / "OneDrive" / "Documents" / "ObsidianVault", vault_read.DEFAULT_VAULT)

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
