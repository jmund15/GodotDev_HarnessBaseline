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
        with patch.object(guard, '_legal_pins', return_value={'test'}), patch.object(guard, '_rails_map', return_value={}), patch.object(guard, '_rails_text', return_value={}), patch.object(guard, '_suffix_names', return_value={}), patch.object(guard, '_declared_efforts', return_value=['low']), patch.object(guard, 'coverage_map', return_value={}), patch.object(guard, 'skill_index', return_value=''), patch.object(guard.model_registry, 'row_for_model', return_value={'transport': 'fixture'}), patch.object(guard.model_registry, 'effort_values', return_value=['low']):
            args = guard.transport_injection(payload, 'fixture', {'models': [], 'transports': {'fixture': {}}})['args']
        self.assertEqual(args['__returns'], {})
        self.assertEqual(args['__toolProfiles']['verify'], {'agentType': 'lane-verify', 'tier': 'fanout'})
        self.assertFalse({'default', 'tierPins', 'fanoutEfforts'} & set(args['__transport']))

    def test_a_job_with_a_shape_and_no_agent_type_gets_the_engine_start(self):
        # dispatch.js swaps general-purpose to the shape's lean type when its brief is covered, but
        # rejects a missing agentType; a declared shape settles the start, so the call carries it.
        jobs = [{'label': 'shaped', 'promptPath': 'a.md', 'shape': 'survey'},
                {'label': 'typed', 'promptPath': 'b.md', 'shape': 'survey', 'agentType': 'Explore'},
                {'label': 'bare', 'promptPath': 'c.md'}]
        payload = {'tool_name': 'Workflow', 'tool_input': {'scriptPath': '.claude/workflows/dispatch.js', 'args': {'jobs': jobs}}}
        with patch.object(guard, '_legal_pins', return_value={'test'}), patch.object(guard, '_rails_map', return_value={}), patch.object(guard, '_rails_text', return_value={}), patch.object(guard, '_suffix_names', return_value={}), patch.object(guard, '_declared_efforts', return_value=['low']), patch.object(guard, 'coverage_map', return_value={}), patch.object(guard, 'skill_index', return_value=''), patch.object(guard.model_registry, 'row_for_model', return_value={'transport': 'fixture'}), patch.object(guard.model_registry, 'effort_values', return_value=['low']):
            args = guard.transport_injection(payload, 'fixture', {'models': [], 'transports': {'fixture': {}}})['args']
        got = {j['label']: j.get('agentType') for j in args['jobs']}
        self.assertEqual(got, {'shaped': 'general-purpose', 'typed': 'Explore', 'bare': None})

if __name__ == '__main__':
    unittest.main()
