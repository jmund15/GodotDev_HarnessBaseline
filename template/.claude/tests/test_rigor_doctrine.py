#!/usr/bin/env python3
"""Rigor doctrine proof: one owner states the bar and the stage table, every stage owner cites it, and the
retired tier and verdict words are gone. Each case id names one file, phrase or root, so a slice closes on its
own cases (`pytest -k`)."""
import re
import subprocess
import sys
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
REPO = CLAUDE.parent
sys.path.insert(0, str(CLAUDE / 'tools'))
BAR_OWNER = CLAUDE / 'skills/orchestration/reference/rigor.md'
LAYER_OWNER = CLAUDE / 'skills/_brainstorm_shared/rigor_stages.md'
# Every stage owner, in either layer's table, cites the bar's one home.
ANCHORS = {'rigor.md': 'rigor.md', 'rigor_stages.md': 'rigor.md'}
DOCTRINE = ('.claude/commands', '.claude/skills')

RETIRED = (
    ('no-low-priority-tier', 'There is no low-priority tier', DOCTRINE),
    ('no-quoted-low-priority-tier', 'There is no "low priority" tier', DOCTRINE),
    ('not-permission-to-discard', 'not permission to discard a valid improvement', DOCTRINE),
    ('whatever-its-severity', 'whatever its severity;', DOCTRINE),
    ('whatever-the-severity', 'whatever the severity', DOCTRINE),
    ('whatever-the-severity-guards', 'whatever the severity', ('.claude/guards',)),
    ('never-its-seat-count', 'never its seat count', DOCTRINE),
    ('no-limit-by-criticality', "Do not limit application by criticality; step 4's list and the `unverified` case "
                                'alone decide a hold', DOCTRINE),
    ('out-of-rounds', 'out of rounds', DOCTRINE),
    ('revise-plan', 'REVISE PLAN', DOCTRINE),
    ('request-changes', 'REQUEST CHANGES', DOCTRINE),
    ('minor-polish', 'MINOR POLISH', DOCTRINE),
    ('review-recommended', 'REVIEW RECOMMENDED', DOCTRINE),
    ('merge-blocker', 'MERGE-BLOCKER', DOCTRINE),
    ('blocker-warn-info', 'BLOCKER / WARN / INFO', DOCTRINE),
    ('bar-filters-reports', 'filters what you report', DOCTRINE + ('.claude/tools',)),
    ('seat-drops-below-bar', 'how many it dropped', DOCTRINE + ('.claude/tools',)),
    ('seat-drops-below-bar-contract', 'how many you dropped', DOCTRINE + ('.claude/tools',)),
)
RETIRED_WORDS = (('harden', 'HARDEN'), ('blocker', 'BLOCKERS?'))


def git_grep(*args):
    run = subprocess.run(['git', '-C', str(REPO), 'grep', '-n', *args], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    if run.returncode not in (0, 1):
        raise RuntimeError('git grep failed: %s' % run.stderr.strip())
    return run.stdout.strip()


def layer_section():
    return LAYER_OWNER.read_text(encoding='utf-8') if LAYER_OWNER.is_file() else ''


def bar_text():
    return BAR_OWNER.read_text(encoding='utf-8') if BAR_OWNER.is_file() else ''


def stage_rows(text):
    rows, in_table = [], False
    for line in text.splitlines():
        if line.startswith('| Stage | Owner file |'):
            in_table = True
            continue
        if in_table and not line.startswith('|'):
            break
        if in_table and not line.startswith('|---'):
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if len(cells) >= 3:
                rows.append(cells)
    return rows


def citing_owner_files():
    cases = set()
    for anchor, text in (('rigor.md', bar_text()), ('rigor_stages.md', layer_section())):
        rows = stage_rows(text)
        if not rows:
            cases.add(('<no stage table in %s>' % anchor, anchor))
        for _stage, owner, effect, *_ in rows:
            if not effect.startswith('Nothing'):
                cases.update((path, anchor) for path in re.findall(r'`([\w./-]+\.md)`', owner))
    return sorted(cases)


def bar_phrases():
    try:
        import finding_bar
    except ImportError:
        return ['<finding_bar missing>']
    return list(finding_bar.BAR_PHRASES) or ['<BAR_PHRASES empty>']


@pytest.mark.parametrize('path,owner', citing_owner_files(), ids=['%s>%s' % c for c in citing_owner_files()])
def test_stage_owner_cites_its_rigor_owner(path, owner):
    assert not path.startswith('<'), path
    anchor = ANCHORS[owner]
    assert anchor in (CLAUDE / path).read_text(encoding='utf-8'), '%s does not cite %s' % (path, anchor)


@pytest.mark.parametrize('phrase', bar_phrases())
def test_bar_phrase_is_in_the_owner(phrase):
    assert not phrase.startswith('<'), phrase
    assert phrase in bar_text(), 'bar phrase missing from rigor.md: %r' % phrase


@pytest.mark.parametrize('case,phrase,roots', RETIRED, ids=[case for case, _, _ in RETIRED])
def test_retired_phrase_is_gone(case, phrase, roots):
    hits = git_grep('-F', phrase, '--', *roots)
    assert not hits, 'retired phrase %r still present:\n%s' % (phrase, hits)


@pytest.mark.parametrize('case,pattern', RETIRED_WORDS, ids=[case for case, _ in RETIRED_WORDS])
def test_retired_word_is_gone(case, pattern):
    hits = git_grep('-w', '-E', pattern, '--', *DOCTRINE)
    assert not hits, 'retired word %r still present:\n%s' % (pattern, hits)


def test_rigor_hole_is_renamed():
    hits = git_grep('-i', '-E', 'rigor[- ]holes?', '--', *DOCTRINE)
    assert not hits, 'rigor-hole still present:\n%s' % hits


def test_explore_relevance_rule_is_kept():
    text = (CLAUDE / 'commands/agents/explore_agents.md').read_text(encoding='utf-8')
    assert 'might be interesting' in text.lower(), 'explore_agents.md lost its relevance rule (excluded class)'


def test_pr_ready_has_no_tier_words():
    hits = git_grep('-w', '-E', 'WARN|INFO', '--', '.claude/commands/pr_ready.md')
    assert not hits, 'pr_ready.md still uses WARN/INFO tiers:\n%s' % hits


def test_proof_corpus_binds_every_doctrine_and_retired_root():
    sys.path.insert(0, str(CLAUDE / 'scripts'))
    import harness_tests as runner
    roots = sorted({folder for *_, folders in RETIRED for folder in folders} | set(DOCTRINE))
    with runner._policy_from(str(CLAUDE)):
        bound = runner.bindings(str(REPO), [root + '/unbound.md' for root in roots])
    missed = [t for t, proofs in bound.items() if 'test_rigor_doctrine.py' not in [Path(p).name for p in proofs]]
    assert not missed, 'adaptation.json proof_corpus no longer binds this proof under: %s' % missed


def main():
    cases = [(test_stage_owner_cites_its_rigor_owner, c, 'cites:%s>%s' % c) for c in citing_owner_files()]
    cases += [(test_bar_phrase_is_in_the_owner, (p,), 'bar:' + p) for p in bar_phrases()]
    cases += [(test_retired_phrase_is_gone, row, 'retired:' + row[0]) for row in RETIRED]
    cases += [(test_retired_word_is_gone, row, 'word:' + row[0]) for row in RETIRED_WORDS]
    cases += [(test, (), test.__name__) for test in
              (test_rigor_hole_is_renamed, test_explore_relevance_rule_is_kept, test_pr_ready_has_no_tier_words,
               test_proof_corpus_binds_every_doctrine_and_retired_root)]
    failed = 0
    for test, args, name in cases:
        try:
            test(*args)
        except AssertionError as error:
            failed += 1
            print('FAIL %s: %s' % (name, str(error).splitlines()[0]))
    print('%d of %d rigor doctrine cases pass' % (len(cases) - failed, len(cases)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
