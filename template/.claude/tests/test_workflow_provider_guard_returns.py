import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
hooks = Path(__file__).resolve().parents[1] / 'hooks'
sys.path.insert(0, str(hooks))
spec = importlib.util.spec_from_file_location('guard', hooks / 'workflow_provider_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

class ReturnsInjection(unittest.TestCase):
    def test_brief_owns_required_return_block(self):
        with tempfile.TemporaryDirectory() as temp:
            brief = Path(temp) / 'brief.md'
            brief.write_text('Return a ```claims block, one row per claim.', encoding='utf-8')
            payload = {'tool_name': 'Workflow', 'tool_input': {'scriptPath': '.claude/workflows/dispatch_chains.js', 'args': {'chains': [{'name': 'lane', 'jobs': [{'label': 'job', 'promptPath': str(brief)}]}]}}}
            self.assertEqual(guard.required_returns(payload), {'job': 'claims'})
            brief.write_text('Return a ```findings block.', encoding='utf-8')
            self.assertEqual(guard.required_returns(payload), {'job': 'findings'})
            brief.write_text('Write code only.', encoding='utf-8')
            self.assertEqual(guard.required_returns(payload), {})
    def test_injection_carries_tier_only_profile_and_derived_returns(self):
        payload = {'tool_name': 'Workflow', 'tool_input': {'scriptPath': '.claude/workflows/dispatch.js', 'args': {'jobs': [], '__returns': {'forged': 'claims'}}}}
        with patch.object(guard, '_legal_pins', return_value={'test'}), patch.object(guard, '_rails_map', return_value={}), patch.object(guard, '_rails_text', return_value={}), patch.object(guard, '_suffix_names', return_value={}), patch.object(guard, '_declared_efforts', return_value=['low']), patch.object(guard.model_registry, 'row_for_model', return_value={'transport': 'fixture'}), patch.object(guard.model_registry, 'effort_values', return_value=['low']):
            args = guard.transport_injection(payload, 'fixture', {'models': [], 'transports': {'fixture': {}}})['args']
        self.assertEqual(args['__returns'], {})
        self.assertEqual(args['__toolProfiles']['verify'], {'agentType': 'lane-verify', 'tier': 'fanout'})
        self.assertFalse({'default', 'tierPins', 'fanoutEfforts'} & set(args['__transport']))

    def test_a_job_kind_sets_the_agent_type_through_its_profile_row(self):
        # The call carries no agentType on the job: the kind row injected beside it names the type.
        jobs = [{'label': 'looker', 'promptPath': 'a.md', 'kind': 'scout'},
                {'label': 'judge', 'promptPath': 'b.md', 'kind': 'reviewer'},
                {'label': 'writer', 'promptPath': 'c.md', 'kind': 'author'}]
        payload = {'tool_name': 'Workflow', 'tool_input': {'scriptPath': '.claude/workflows/dispatch.js', 'args': {'jobs': jobs}}}
        with patch.object(guard, '_legal_pins', return_value={'test'}), patch.object(guard, '_rails_map', return_value={}), patch.object(guard, '_rails_text', return_value={}), patch.object(guard, '_suffix_names', return_value={}), patch.object(guard, '_declared_efforts', return_value=['low']), patch.object(guard.model_registry, 'row_for_model', return_value={'transport': 'fixture'}), patch.object(guard.model_registry, 'effort_values', return_value=['low']):
            args = guard.transport_injection(payload, 'fixture', {'models': [], 'transports': {'fixture': {}}})['args']
        got = {label: row['agentType'] for label, row in args['__toolProfiles'].items() if label != 'verify'}
        self.assertEqual(got, {'looker': 'lane-survey', 'judge': 'lane-review', 'writer': 'lane-author'})
        self.assertTrue(all('agentType' not in j for j in args['jobs']))

    def test_a_job_with_no_kind_is_refused_before_injection(self):
        payload = {'tool_name': 'Workflow', 'tool_input': {'scriptPath': '.claude/workflows/dispatch.js', 'args': {'jobs': [{'label': 'bare', 'promptPath': 'c.md'}]}}}
        with self.assertRaisesRegex(ValueError, 'names no kind'):
            guard.transport_injection(payload, 'fixture', {'models': [], 'transports': {'fixture': {}}})

if __name__ == '__main__':
    unittest.main()
