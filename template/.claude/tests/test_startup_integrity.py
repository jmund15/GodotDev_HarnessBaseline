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

if __name__ == '__main__':
    unittest.main()
