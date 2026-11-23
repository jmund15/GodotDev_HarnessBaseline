"""Every workflow engine carries its close-check or an exemption marker naming its class (`workflows/*.js`)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXEMPT_MARK = re.compile(r'^// POST-CHECK exempt \(([^)]+)\): \S', re.M)
APPLIED = re.compile(r'\bverification\s*(?:[:,}])')
CLAIM_ENGINE = re.compile(r'\bpost_check\b')


def post_check_contract_errors(texts):
    """An engine is exempt when its own marker names a class and a reason; every other engine must carry the
    close-check block and an applied verification. An engine that runs the post-check emits claims, so it may
    never exempt itself: the marker would drop the block that verifies its claims."""
    errors = []
    for name, text in sorted(texts.items()):
        exemption = EXEMPT_MARK.search(text)
        checked = ('// CLOSE-CHECK-BEGIN' in text and '// CLOSE-CHECK-END' in text and APPLIED.search(text))
        if exemption and (checked or CLAIM_ENGINE.search(text)):
            errors.append(name + ': an engine that runs the post-check may not exempt itself')
        elif not exemption and not checked:
            errors.append(name + ': missing close-check/applied verification or exemption')
    return errors


def workflow_sources():
    return {p.name: p.read_text(encoding='utf-8') for p in sorted((ROOT / '.claude' / 'workflows').glob('*.js'))}


LEDGER_TOOLS = ('review_ledger', 'pr_scripted_checks', 'post_check')
LEDGER_GRANTED = ('commands/session_audit.md', 'commands/review_pr.md', 'commands/pr_ready.md',
                  'commands/pr_pipeline.md', 'commands/plan_check.md',
                  'commands/architecture_brainstorm_redteam.md')
SNAPSHOT_COMMANDS = ('architecture_brainstorm_redteam', 'plan_check', 'pr_pipeline',
                     'review_pr', 'session_audit', 'structure_audit', 'rule_consistency')


def grant_errors(texts):
    """Claude Code parses `allowed-tools` from a command's frontmatter: a tool the body runs and the grant
    omits stops the step at run time. Only grant tokens are asserted, never doc wording."""
    errors = []
    for rel in LEDGER_GRANTED:
        if rel not in texts:
            continue  # a profile lane ships only its own commands; an absent command is not this proof's subject
        head, _, body = texts.get(rel, '').partition('\n---\n')
        grant = re.search(r'^allowed-tools:(.*)$', head, re.M)
        called = [t for t in LEDGER_TOOLS if re.search(r'python3 \.claude/tools/%s\.py' % t, body)]
        if called and not grant:  # a command that no longer runs a ledger tool owes no grant line
            errors.append('%s has no allowed-tools line' % rel)
            continue
        for tool in called:
            if 'Bash(python3 .claude/tools/%s.py:*)' % tool not in grant.group(1) and 'Bash(python3:*)' not in grant.group(1):
                errors.append('%s calls %s.py without a grant' % (rel, tool))
    for name in SNAPSHOT_COMMANDS:
        rel = 'commands/%s.md' % name
        if rel not in texts:
            continue
        head, _, body = texts[rel].partition('\n---\n')
        # A command that reviews a ledger runs post_check for it, so its grant must cover that call. A lane's
        # older command text (no ledger step) owes no grant, whatever the project version carries.
        if 'ledger' in body.lower() and 'Bash(python3 .claude/tools/post_check.py:*)' not in head:
            errors.append(rel + ': incorrect post_check grant')
    return errors


def command_sources():
    return {'commands/' + p.name: p.read_text(encoding='utf-8')
            for p in sorted((ROOT / '.claude' / 'commands').glob('*.md'))}


class NativeCallerContracts(unittest.TestCase):
    def test_every_engine_names_its_post_check_or_its_exemption(self):
        texts = workflow_sources()
        self.assertEqual(post_check_contract_errors(texts), [])

    def test_the_post_check_contract_fires_on_planted_defects(self):
        bare = 'export const meta = {}\nconst x = 1\n'
        self.assertEqual(len(post_check_contract_errors({'a.js': bare})), 1)
        both = 'export const meta = {}\n// POST-CHECK exempt: ideas\nreturn { postCheck: X }\n'
        self.assertEqual(len(post_check_contract_errors({'b.js': both})), 1)
        self.assertNotEqual(post_check_contract_errors({'c.js': bare + '// POST-CHECK exempt: ideas\n'}), [])
        self.assertEqual(post_check_contract_errors(
            {'notes.js': bare + '// POST-CHECK exempt (notes not claims): brainstorm only\n'}), [])
        claim = '// CLOSE-CHECK-BEGIN\nconst x = 1\n// CLOSE-CHECK-END\nreturn {verification}\n'
        self.assertNotEqual(post_check_contract_errors(
            {'d.js': claim + '// POST-CHECK exempt (ideas not claims): brainstorm only\n'}), [])
        self.assertNotEqual(post_check_contract_errors(  # a claim engine may not drop its block and exempt itself
            {'claims.js': 'const x = 1\nrunPostCheck("post_check.py")\n'
                          '// POST-CHECK exempt (notes not claims): brainstorm only\n'}), [])
        for name in ('engine_alpha.js', 'engine_beta.js', 'engine_gamma.js'):
            with self.subTest(engine=name):
                valid = '// CLOSE-CHECK-BEGIN\nconst x = 1\n// CLOSE-CHECK-END\nreturn {verification}\n'
                self.assertEqual(post_check_contract_errors({name: valid}), [])
                self.assertNotEqual(post_check_contract_errors({name: valid.replace('verification', 'postCheck')}), [])
                self.assertNotEqual(post_check_contract_errors({name: valid.replace('CLOSE-CHECK-BEGIN', 'REMOVED')}), [])

    def test_ledger_callers_and_post_check_commands_hold_their_grants(self):
        self.assertEqual(grant_errors(command_sources()), [])

    def test_the_grant_check_fires_on_a_stripped_or_added_grant(self):
        texts = command_sources()
        for rel in LEDGER_GRANTED:
            if rel not in texts:
                continue
            head, sep, body = texts[rel].partition('\n---\n')
            for tool in LEDGER_TOOLS:
                if not re.search(r'python3 \.claude/tools/%s\.py' % tool, body):
                    continue
                stripped = re.sub(r',? ?Bash\(python3 \.claude/tools/%s\.py:\*\)' % tool, '', head)
                if stripped == head:
                    continue  # covered by a wildcard grant
                with self.subTest(grant=rel, tool=tool):
                    self.assertNotEqual(grant_errors(dict(texts, **{rel: stripped + sep + body})), [])
        for name in SNAPSHOT_COMMANDS:
            rel = 'commands/%s.md' % name
            if rel not in texts:
                continue
            head, sep, body = texts[rel].partition('\n---\n')
            if 'ledger' not in body.lower() or 'Bash(python3 .claude/tools/post_check.py:*)' not in head:
                continue  # this lane's command text reviews no ledger, so it owes no grant
            with self.subTest(post_check=name):
                stripped = head.replace('Bash(python3 .claude/tools/post_check.py:*)', '')
                self.assertNotEqual(grant_errors(dict(texts, **{rel: stripped + sep + body})), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
