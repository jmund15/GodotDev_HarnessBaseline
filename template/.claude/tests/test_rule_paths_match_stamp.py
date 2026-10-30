"""Path-glob coverage proof for rules/harness_tooling.md (J9-E15, the globs half).

A rule's `paths:` list is a load set: the client surfaces the rule on an edit under a matching path.
`hooks/_hook_state.HARNESS_DIRS` is the set the commit stamp binds, so any path the stamp covers and
the rule's globs do not leaves an author editing stamped harness code with no harness-tooling rule
loaded. This proof pins the rule's globs against HARNESS_DIRS.

`load_census.py` reads the same frontmatter but groups rules by exact glob string, with no matcher,
so the matcher lives here.

    python3 .claude/tests/test_rule_paths_match_stamp.py
"""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # .claude/

sys.path.insert(0, str(ROOT / "hooks"))
from _hook_state import HARNESS_DIRS  # noqa: E402

_spec = importlib.util.spec_from_file_location("load_census_for_paths", ROOT / "tools" / "load_census.py")
_lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lc)
parse_paths_frontmatter = _lc.parse_paths_frontmatter

RULE = ROOT / "rules" / "harness_tooling.md"

# One path per HARNESS_DIRS entry. A glob only has to match the SHAPE, so nothing here is read and a
# rename cannot break this proof.
REPRESENTATIVE = {
    ".claude/hooks": ".claude/hooks/pre_bash_dispatch.py",
    ".claude/tools": ".claude/tools/load_census.py",
    ".claude/scripts": ".claude/scripts/harness_tests.py",
    ".claude/workflows": ".claude/workflows/dispatch.js",
    ".claude/tests": ".claude/tests/test_rule_paths_match_stamp.py",
    ".claude/settings.json": ".claude/settings.json",
    ".claude/settings.*.json": ".claude/settings.base.json",
}


def glob_to_re(glob):
    """A `paths:` glob as a compiled regex. Gitignore semantics: `**/` matches zero or more
    directories, `**` matches anything, and `*`/`?` stop at a separator. `fnmatch` lets `*` cross a
    separator; `PurePath.match` collapses `**` to `*`; both make this coverage check vacuous."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def uncovered(globs, entries=None):
    """HARNESS_DIRS entries with no matching glob in `globs`."""
    entries = HARNESS_DIRS if entries is None else entries
    return [e for e in entries
            if not any(glob_to_re(g).match(REPRESENTATIVE[e]) for g in globs)]


class RulePathsCoverStampTests(unittest.TestCase):
    def test_every_stamped_harness_dir_has_a_matching_rule_glob(self):
        globs = parse_paths_frontmatter(RULE)
        self.assertTrue(globs, f"{RULE}: paths: frontmatter parsed to nothing")
        self.assertEqual(uncovered(globs), [],
                         "HARNESS_DIRS entries no paths: glob reaches: "
                         + ", ".join(uncovered(globs)))

    def test_the_proof_covers_every_harness_dir_entry(self):
        self.assertEqual(sorted(REPRESENTATIVE), sorted(HARNESS_DIRS),
                         "a HARNESS_DIRS entry has no representative path in this proof")

    def test_dropping_a_glob_is_uncovered(self):
        globs = [g for g in parse_paths_frontmatter(RULE) if g != ".claude/tests/**"]
        self.assertIn(".claude/tests", uncovered(globs))

    def test_matcher_is_path_aware(self):
        self.assertTrue(glob_to_re(".claude/tests/**").match(".claude/tests/test_x.py"))
        self.assertTrue(glob_to_re(".claude/hooks/**/*.py").match(".claude/hooks/x.py"))
        self.assertTrue(glob_to_re(".claude/hooks/**/*.py").match(".claude/hooks/a/b.py"))
        self.assertIsNone(glob_to_re(".claude/*.py").match(".claude/a/b.py"))
        self.assertIsNone(glob_to_re(".claude/hooks/**/*.py").match(".claude/hooks/x.js"))


if __name__ == "__main__":
    unittest.main()
