"""Brand migration uses temporary data; never migrate the current installation."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'))
from blackbox_core import Store, NotesError
import blackbox_compat as compat


class BrandCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blackbox-brand-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.old = self.base / 'TAS Notes'
        self.new = self.base / 'Blackbox'
        env = dict(os.environ, LOCALAPPDATA=str(self.base))
        env.pop('BLACKBOX_STATE_DIR', None)
        env.pop('TAS_NOTES_STATE_DIR', None)
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_fresh_default_is_blackbox_without_creation(self):
        self.assertEqual(self.new, compat.default_state())
        self.assertFalse(self.new.exists())

    def test_legacy_environment_is_accepted_and_new_takes_precedence(self):
        with patch.dict(os.environ, {'TAS_NOTES_STATE_DIR': str(self.old)}):
            self.assertEqual(self.old, compat.default_state())
            with patch.dict(os.environ, {'BLACKBOX_STATE_DIR': str(self.new)}):
                self.assertEqual(self.new, compat.default_state())

    def test_conflicting_defaults_are_never_merged(self):
        self.old.mkdir(); self.new.mkdir()
        with self.assertRaisesRegex(NotesError, 'Both'):
            compat.default_state()

    def seed(self):
        store = Store(self.old)
        project = self.base / 'project'
        project.mkdir()
        store.set_project(str(project), True)
        store.record({'hook_event_name':'PostToolUse','cwd':str(project),'session_id':'fixture',
                      'tool_name':'shell','tool_response':'RuntimeError: fixture'})
        (self.old / 'reader-selection.json').write_bytes(b'{"fixture":"keep"}')
        return {p.name:p.read_bytes() for p in self.old.iterdir() if p.is_file()}

    def test_migration_preserves_database_configs_and_old_path_alias(self):
        before = self.seed()
        self.assertEqual(self.old, compat.default_state())
        self.assertEqual(self.new, compat.migrate_default(self.old))
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.new.iterdir() if p.is_file()})
        self.assertEqual(self.old.resolve(), self.new.resolve())
        self.assertEqual(self.new, compat.default_state())
        self.assertEqual(self.new, compat.migrate_default(self.new))

    def test_alias_failure_rolls_back_without_data_loss(self):
        before = self.seed()
        with patch.object(compat, 'make_alias', side_effect=OSError('fixture')):
            with self.assertRaises(NotesError):
                compat.migrate_default(self.old)
        self.assertFalse(self.new.exists())
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.old.iterdir() if p.is_file()})

    def test_running_reader_blocks_migration(self):
        self.seed()
        with patch('blackbox_launcher.read_registry', return_value={'fixture':True}), patch('blackbox_launcher.probe_instance', return_value={'pid':1}):
            with self.assertRaisesRegex(NotesError, 'Stop'):
                compat.migrate_default(self.old)
        self.assertTrue(self.old.exists())
        self.assertFalse(self.new.exists())

    def test_custom_legacy_environment_keeps_its_directory(self):
        self.seed()
        with patch.dict(os.environ, {'TAS_NOTES_STATE_DIR':str(self.old)}):
            self.assertEqual(self.old, compat.migrate_default(self.old))
        self.assertFalse(self.new.exists())
