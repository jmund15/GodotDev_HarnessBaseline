"""Startup fallback/readiness tests; engine, registry and shell calls are mocked."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('startup_integrity', os.environ.get('STARTUP_MODULE',
    ROOT / '.claude/hooks/session_context_loader.py'))
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
godot_spec = importlib.util.spec_from_file_location('startup_integrity_godot',
    Path(spec.origin).with_name('session_context_loader_godot.py'))
godot = importlib.util.module_from_spec(godot_spec)
godot_spec.loader.exec_module(godot)


class StartupIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_partial_import_with_error_is_not_reported_fixed(self):
        def partial(*args, **kwargs):
            imported = self.root / '.godot/imported'
            imported.mkdir(parents=True)
            (imported / 'one-resource').write_text('partial')
            return SimpleNamespace(returncode=1, stdout='', stderr='fixture failure')
        with mock.patch.object(godot, 'get_godot_bin', return_value='mock-engine'), \
             mock.patch.object(godot.subprocess, 'run', side_effect=partial):
            self.assertTrue(godot.setup_import_cache(self.root).startswith('FAILED'))

    def test_existing_cache_presence_is_not_current_import_validation(self):
        imported = self.root / '.godot/imported'
        imported.mkdir(parents=True)
        (imported / 'resource').write_text('present')
        with mock.patch.object(godot.subprocess, 'run') as run:
            result = godot.setup_import_cache(self.root)
        run.assert_not_called()
        self.assertTrue(result.startswith('PRESENT'))

    def test_unreadable_import_cache_is_unknown_not_hook_crash(self):
        with mock.patch.object(Path, 'exists', side_effect=OSError('fixture unreadable')):
            result = godot.setup_import_cache(self.root)
        self.assertTrue(result.startswith(('UNKNOWN', 'FAILED')))

    def test_registry_failure_never_selects_legacy_fallback_launchers(self):
        registry = SimpleNamespace(available_models=mock.Mock(side_effect=ValueError('fixture registry')))
        transport = SimpleNamespace(resolve=lambda: ('codex', 'fixture'))
        with mock.patch.dict(loader.sys.modules, {'model_registry': registry, '_session_transport': transport}), \
             mock.patch.object(loader, 'verify_sidecar') as verify:
            result = loader.sidecar_health(self.root)
        verify.assert_not_called()
        self.assertIn('UNKNOWN', result['sidecar_registry'])

    def test_live_registry_probes_only_available_off_seat_routes(self):
        registry = SimpleNamespace(available_models=lambda: [{'transport': 'codex', 'alias': 'codex-row'},
                                                             {'transport': 'other', 'alias': 'other-row'}],
            transports=lambda: ['codex', 'other', 'retired'],
            transport_meta=lambda name: {'launcher': name + '.sh'})
        transport = SimpleNamespace(resolve=lambda: ('codex', 'fixture'), session_plan=lambda: 'codex')
        with mock.patch.dict(loader.sys.modules, {'model_registry': registry, '_session_transport': transport}), \
             mock.patch.object(loader, 'verify_sidecar', return_value='OK') as verify:
            result = loader.sidecar_health(self.root)
        verify.assert_called_once_with(self.root, 'other.sh', 'other-row')
        self.assertEqual({'sidecar-other': 'OK'}, result)


if __name__ == '__main__':
    unittest.main()
