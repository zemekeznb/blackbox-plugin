"""Host setup persists one store across processes without touching real Codex data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ENTRY = Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts/blackbox.py'

# Run the actual CLI entry point. Replace only the host Hook inventory query:
# these tests check local setup, never an installed host or real Hook trust.
RUN_CLI = (
    'import pathlib,runpy,sys; '
    'entry=sys.argv.pop(1); '
    'sys.path.insert(0,str(pathlib.Path(entry).parent)); '
    'import blackbox_hooks; '
    'blackbox_hooks.inspect=lambda paths:{path:"hooks_unknown" for path in paths}; '
    'sys.argv[0]=entry; '
    'runpy.run_path(entry,run_name="__main__")'
)


class HostSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='blackbox-host-setup-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'host'
        self.state = self.root / 'selected-state'
        self.binding = self.home / 'blackbox/runtime.json'
        self.environment = dict(os.environ, CODEX_HOME=str(self.home),
                                USERPROFILE=str(self.root / 'profile'),
                                LOCALAPPDATA=str(self.root / 'appdata'))
        for name in ('BLACKBOX_STATE_DIR', 'TAS_NOTES_STATE_DIR'):
            self.environment.pop(name, None)
        if os.name == 'nt':
            self.environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')

    def cli(self, *arguments, environment=None, expected=0, runner=RUN_CLI):
        result = subprocess.run([sys.executable, '-I', '-B', '-c', runner, str(ENTRY), *arguments],
                                cwd=self.root, env=environment or self.environment,
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout) if expected == 0 else result

    def configure(self, *, bind=True, explicit=True, environment=None):
        arguments = ['--state-dir', str(self.state)] if explicit else []
        arguments += ['configure-runtime'] + (['--bind-host'] if bind else [])
        return self.cli(*arguments, environment=environment)

    def no_appdata(self):
        environment = self.environment.copy()
        environment.pop('LOCALAPPDATA', None)
        return environment

    def test_setup_binds_actual_runtime_and_later_process_recovers_same_store(self):
        result = self.configure()
        self.assertTrue(result['runtime_configured'])
        self.assertTrue(result['host_bound'])
        saved = json.loads(self.binding.read_text(encoding='utf-8'))
        self.assertTrue(os.path.samefile(saved['python'], sys.executable))
        self.assertTrue(os.path.samefile(saved['state_directory'], self.state))
        database = self.state / 'notes.sqlite3'
        before = database.read_bytes()
        binding_before = self.binding.read_bytes()
        binding_modified = self.binding.stat().st_mtime_ns
        status = self.cli('status', environment=self.no_appdata())
        self.assertTrue(os.path.samefile(status['application']['state_directory'], self.state))
        self.assertEqual(status['projects'], [])
        self.assertEqual(status['record_count'], 0)
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(binding_before, self.binding.read_bytes())
        self.assertEqual(binding_modified, self.binding.stat().st_mtime_ns)
        self.assertFalse((self.root / 'appdata').exists())

    def test_repeated_setup_keeps_database_and_project_choices(self):
        self.configure()
        project = self.root / 'opted-in-project'
        project.mkdir()
        self.cli('enable', str(project), '--accept-capture-scope')
        sibling = self.root / 'disabled-project'
        sibling.mkdir()
        self.cli('disable', str(sibling))
        before = (self.state / 'notes.sqlite3').read_bytes()
        self.configure(explicit=False, environment=self.no_appdata())
        self.assertEqual(before, (self.state / 'notes.sqlite3').read_bytes())
        status = self.cli('status', environment=self.no_appdata())
        self.assertEqual({item['path']: item['enabled'] for item in status['projects']},
                         {str(project): True, str(sibling): False})
        self.assertEqual(status['capture_health']['enabled_project_count'], 1)

    def test_removed_python_can_be_rebound_without_losing_original_store(self):
        self.configure()
        saved = json.loads(self.binding.read_text(encoding='utf-8'))
        saved['python'] = str(self.root / 'removed-python.exe')
        self.binding.write_text(json.dumps(saved), encoding='utf-8')
        before = (self.state / 'notes.sqlite3').read_bytes()
        # A fresh process using an explicitly selected working interpreter can
        # still find the persisted state and repair only its runtime binding.
        result = self.configure(explicit=False, environment=self.no_appdata())
        self.assertTrue(result['host_bound'])
        rebound = json.loads(self.binding.read_text(encoding='utf-8'))
        self.assertTrue(os.path.samefile(rebound['python'], sys.executable))
        self.assertEqual(rebound['state_directory'], saved['state_directory'])
        self.assertEqual(before, (self.state / 'notes.sqlite3').read_bytes())
        self.assertFalse((self.root / 'appdata').exists())

    def test_conflicting_setup_does_not_switch_binding_or_create_other_database(self):
        self.configure()
        before = self.binding.read_bytes()
        database_before = (self.state / 'notes.sqlite3').read_bytes()
        other = self.root / 'different-state'
        result = self.cli('--state-dir', str(other), 'configure-runtime', '--bind-host', expected=1)
        self.assertIn('runtime_binding_conflict', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(before, self.binding.read_bytes())
        self.assertEqual(database_before, (self.state / 'notes.sqlite3').read_bytes())
        self.assertFalse((other / 'notes.sqlite3').exists())
        self.assertFalse(other.exists())

    def test_status_of_legacy_state_does_not_create_host_binding(self):
        self.assertFalse(self.configure(bind=False)['host_bound'])
        self.assertFalse(self.binding.exists())
        before = (self.state / 'notes.sqlite3').read_bytes()
        status = self.cli('--state-dir', str(self.state), 'status')
        self.assertEqual(status['projects'], [])
        self.assertFalse(self.binding.exists())
        self.assertFalse(self.home.exists())
        self.assertEqual(before, (self.state / 'notes.sqlite3').read_bytes())

    def test_broken_binding_default_status_does_not_create_fallback_state(self):
        self.configure()
        original = (self.state / 'notes.sqlite3').read_bytes()
        self.binding.write_text('{"schema_version":', encoding='utf-8')
        result = self.cli('status', expected=1)
        self.assertIn('runtime_binding_invalid', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(original, (self.state / 'notes.sqlite3').read_bytes())
        self.assertFalse((self.root / 'appdata').exists())

    def test_missing_bound_database_cannot_be_recreated_by_explicit_path_or_environment(self):
        self.configure()
        before = self.binding.read_bytes()
        database = self.state / 'notes.sqlite3'
        database.unlink()
        for command in (['status'], ['open', '--no-browser'], ['stop'],
                        ['serve', '--background'], ['export', 'synthetic', str(self.root / 'out.md')],
                        ['backup', str(self.root / 'backup')]):
            with self.subTest(command=command):
                result = self.cli('--state-dir', str(self.state), *command, expected=1)
                self.assertIn('runtime_state_unavailable', result.stderr)
                self.assertFalse(database.exists())
        for name in ('BLACKBOX_STATE_DIR', 'TAS_NOTES_STATE_DIR'):
            result = self.cli('status', environment=dict(self.environment, **{name: str(self.state)}), expected=1)
            self.assertIn('runtime_state_unavailable', result.stderr)
            self.assertFalse(database.exists())
        self.assertEqual(before, self.binding.read_bytes())
        self.assertFalse((self.root / 'out.md').exists())
        self.assertFalse((self.root / 'backup').exists())

    def test_missing_bound_directory_is_not_recreated_by_explicit_status(self):
        self.configure()
        (self.state / 'notes.sqlite3').unlink()
        (self.state / 'runtime.cmd').unlink(missing_ok=True)
        self.state.rmdir()
        result = self.cli('--state-dir', str(self.state), 'status', expected=1)
        self.assertIn('runtime_state_unavailable', result.stderr)
        self.assertFalse(self.state.exists())

    def test_valid_binding_preserves_independent_explicit_store_semantics(self):
        self.configure()
        before = self.binding.read_bytes()
        original = (self.state / 'notes.sqlite3').read_bytes()
        other = self.root / 'independent-state'
        result = self.cli('--state-dir', str(other), 'status')
        self.assertTrue(os.path.samefile(result['application']['state_directory'], other))
        self.assertEqual(result['projects'], [])
        self.assertTrue((other / 'notes.sqlite3').is_file())
        self.assertEqual(before, self.binding.read_bytes())
        self.assertEqual(original, (self.state / 'notes.sqlite3').read_bytes())

    def test_database_removed_after_binding_validation_is_opened_read_only(self):
        self.configure()
        # Simulate deletion after a successful validation in the same process.
        # A read-only SQLite open must fail rather than recreate the bound DB.
        injection = '''
import blackbox_runtime
real_binding = blackbox_runtime.read_binding
def checked_binding(**kwargs):
    value = real_binding(**kwargs)
    pathlib.Path(value['state_directory']).joinpath('notes.sqlite3').unlink()
    return value
blackbox_runtime.read_binding = checked_binding
'''
        runner = RUN_CLI.replace('sys.argv[0]=entry; ', injection + 'sys.argv[0]=entry; ')
        result = self.cli('--state-dir', str(self.state), 'status', expected=1, runner=runner)
        self.assertIn('stage=open_store', result.stderr)
        self.assertFalse((self.state / 'notes.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
