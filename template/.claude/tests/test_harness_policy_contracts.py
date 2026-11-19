"""Token checks over shared harness docs: a path, a command or an id a reader acts on, never a phrase."""
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

# Tokens a reader of rules/harness_tooling.md acts on when building a guard's must-allow proof.
HARNESS_TOOLING_TOKENS = ('tests/_must_allow_cases.py', 'merge --continue')


def missing_tokens(text, tokens):
    return [t for t in tokens if t not in text]


class PolicyContracts(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding='utf-8')

    def test_verify_path_is_prefixed_in_active_doctrine(self):
        for path in ('CLAUDE.godot.md', 'guards/common.godot.md'):
            with self.subTest(path=path):
                self.assertNotRegex(self.read(path), r'(?<!\.claude/)scripts/verify\.ps1')

    def test_the_verify_prefix_check_detects_a_bare_path(self):
        self.assertIsNotNone(re.search(r'(?<!\.claude/)scripts/verify\.ps1', 'run scripts/verify.ps1'))
        self.assertIsNone(re.search(r'(?<!\.claude/)scripts/verify\.ps1', 'run .claude/scripts/verify.ps1'))

    def test_harness_tooling_names_the_must_allow_helper_and_it_exists(self):
        self.assertEqual(missing_tokens(self.read('rules/harness_tooling.md'), HARNESS_TOOLING_TOKENS), [])
        self.assertTrue((ROOT / 'tests' / '_must_allow_cases.py').is_file())

    def test_the_token_check_detects_each_removed_token(self):
        text = self.read('rules/harness_tooling.md')
        for token in HARNESS_TOOLING_TOKENS:
            with self.subTest(token=token):
                self.assertEqual(missing_tokens(text.replace(token, ''), HARNESS_TOOLING_TOKENS), [token])


if __name__ == '__main__':
    unittest.main()
