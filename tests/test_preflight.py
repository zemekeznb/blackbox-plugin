"""Release setup checks must not mutate history, opt in projects or configure Codex."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/blackbox'
sys.path.insert(0, str(PLUGIN / 'scripts'))
import blackbox_preflight as preflight
from blackbox_core import Store


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='blackbox-preflight-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / 'state'
        self.path_patch = patch.object(preflight, 'inspect_path_python', return_value={
            'executable': sys.executable, 'version': list(sys.version_info[:3])})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.platform_patch = patch.object(preflight.platform, 'system', return_value='Windows')
        self.platform_patch.start()
        self.addCleanup(self.platform_patch.stop)

    def test_check_is_read_only_and_does_not_claim_host_success(self):
        result = preflight.preflight(PLUGIN, self.state)
        self.assertTrue(result['ok'], result['errors'])
        self.assertFalse(self.state.exists())
        self.assertEqual(result['host_plugin_registration'], 'not_checked')
        self.assertEqual(result['host_capture'], 'not_checked')
        self.assertEqual(result['minimum_python'], '3.11')
        self.assertTrue(any('BLACKBOX_STATE_DIR' in item for item in result['warnings']))

    def test_required_package_files_and_manifest_fail_helpfully(self):
        package = self.root / 'incomplete'
        package.mkdir()
        version, errors = preflight.check_package(package)
        self.assertIsNone(version)
        self.assertTrue(any('scripts/blackbox_mcp.py' in item for item in errors))
        self.assertTrue(any('manifest' in item for item in errors))
        (package / '.codex-plugin').mkdir()
        (package / '.codex-plugin/plugin.json').write_text('{"name":"other","version":"garbage"}')
        self.assertTrue(any('technical key blackbox' in item for item in preflight.check_package(package)[1]))

    def test_python_and_platform_requirements_are_actionable(self):
        with patch.object(preflight.sys, 'version_info', (3, 10, 0)):
            result = preflight.preflight(PLUGIN, self.state)
        self.assertFalse(result['ok'])
        self.assertTrue(any('Python 3.11' in item for item in result['errors']))
        with patch.object(preflight.platform, 'system', return_value='Linux'):
            result = preflight.preflight(PLUGIN, self.state)
        self.assertFalse(result['ok'])
        self.assertTrue(any('Windows only' in item for item in result['errors']))

    def test_path_python_is_required_independently_of_selected_python(self):
        with patch.object(preflight, 'inspect_path_python', side_effect=ValueError('MCP requires python on PATH.')):
            result = preflight.preflight(PLUGIN, self.state)
        self.assertFalse(result['ok'])
        self.assertIn('MCP requires python on PATH.', result['errors'])
        with patch.object(preflight, 'inspect_path_python', return_value={
                'executable': str(self.root / 'another-python.exe'), 'version': [3, 12, 0]}):
            result = preflight.preflight(PLUGIN, self.state)
        self.assertTrue(result['ok'], result['errors'])
        self.assertTrue(any('differs' in item for item in result['warnings']))

    def test_path_probe_handles_aliases_timeouts_and_old_python(self):
        self.path_patch.stop()
        with patch.object(preflight.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'PATH'):
                preflight.inspect_path_python()
        cases = [subprocess.CompletedProcess([], 1, 'not json', ''),
                 subprocess.CompletedProcess([], 0, '{"version":[3,10,0],"executable":"old"}', '')]
        with patch.object(preflight.shutil, 'which', return_value='python'):
            for returned in cases:
                with patch.object(preflight.subprocess, 'run', return_value=returned):
                    with self.assertRaisesRegex(ValueError, 'Correct PATH'):
                        preflight.inspect_path_python()
            with patch.object(preflight.subprocess, 'run', side_effect=subprocess.TimeoutExpired('python', 10)):
                with self.assertRaisesRegex(ValueError, 'Correct PATH'):
                    preflight.inspect_path_python()

    def test_existing_database_checked_without_changing_bytes(self):
        Store(self.state)
        database = self.state / 'notes.sqlite3'
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        result = preflight.preflight(PLUGIN, self.state)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
        self.assertFalse((self.state / 'runtime.cmd').exists())

    def test_legacy_or_corrupt_database_is_not_migrated(self):
        self.state.mkdir()
        database = self.state / 'notes.sqlite3'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE legacy (value TEXT)')
        before = database.read_bytes()
        result = preflight.preflight(PLUGIN, self.state)
        self.assertFalse(result['ok'])
        self.assertTrue(any('will not migrate history' in item for item in result['errors']))
        self.assertEqual(database.read_bytes(), before)
        database.write_bytes(b'not a sqlite database')
        result = preflight.preflight(PLUGIN, self.state)
        self.assertFalse(result['ok'])
        self.assertTrue(any('cannot be checked' in item for item in result['errors']))
        self.assertEqual(database.read_bytes(), b'not a sqlite database')

    def test_state_must_be_outside_release_and_not_a_file(self):
        for directory in (PLUGIN / 'runtime-data', ROOT / 'runtime-data', ROOT):
            result = preflight.preflight(PLUGIN, directory)
            self.assertFalse(result['ok'])
            self.assertTrue(any('outside the complete release' in item for item in result['errors']))
        self.state.write_text('preserve')
        result = preflight.preflight(PLUGIN, self.state)
        self.assertFalse(result['ok'])
        self.assertEqual(self.state.read_text(), 'preserve')

    def test_state_cannot_be_in_another_git_worktree(self):
        for marker_kind in ('file', 'directory'):
            repository = self.root / marker_kind
            repository.mkdir()
            marker = repository / '.git'
            if marker_kind == 'file':
                marker.write_text('gitdir: unrelated-worktree-metadata')
            else:
                marker.mkdir()
            directory = repository / 'nested/state'
            result = preflight.preflight(PLUGIN, directory)
            self.assertFalse(result['ok'])
            self.assertTrue(any('Git working directories' in item for item in result['errors']))
            self.assertFalse((repository / 'nested').exists())

    def test_release_manifest_detects_tampering_and_version_mismatch(self):
        package = self.root / 'plugins/blackbox'
        package.mkdir(parents=True)
        files = {}
        for name in ['plugins/blackbox/' + item for item in preflight.REQUIRED_FILES] + ['install.ps1']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test fixture')
            files[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size}
        manifest = {'schema_version': 1, 'version': '0.4.0-alpha.1', 'files': files}
        manifest_path = self.root / 'release-manifest.json'
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(preflight.check_release_manifest(package, manifest['version']), ('release_manifest_verified', []))
        self.assertTrue(preflight.check_release_manifest(package, '0.3.0')[1])
        (package / 'scripts/blackbox.py').write_bytes(b'tampered')
        self.assertTrue(preflight.check_release_manifest(package, manifest['version'])[1])

    def test_release_manifest_rejects_paths_outside_extraction(self):
        package = self.root / 'plugins/blackbox'
        package.mkdir(parents=True)
        files = {('plugins/blackbox/' + name): {'sha256': '0' * 64, 'size': 0}
                 for name in preflight.REQUIRED_FILES}
        files['install.ps1'] = {'sha256': '0' * 64, 'size': 0}
        for name in ('../outside', 'C:/outside', '\\\\host\\file'):
            unsafe = {name: {'sha256': '0' * 64, 'size': 0}, **files}
            (self.root / 'release-manifest.json').write_text(json.dumps({
                'schema_version': 1, 'version': '0.4.0-alpha.1', 'files': unsafe}))
            self.assertTrue(preflight.check_release_manifest(package, '0.4.0-alpha.1')[1])


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell integration')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='blackbox-installer-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        self.env = dict(os.environ, LOCALAPPDATA=str(self.root / 'appdata'),
                        BLACKBOX_STATE_DIR=str(self.root / 'unused-state'),
                        CODEX_HOME=str(self.root / 'codex'))
        # Ensure the integration checks a known Python; production does not alter PATH.
        self.env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + self.env['PATH']

    def install(self, *options):
        return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(ROOT / 'install.ps1'), '-Python', sys.executable,
                               '-StateDir', str(self.state), '-RuntimeOnly', *options],
                              capture_output=True, text=True, env=self.env, timeout=30)

    def test_check_only_has_no_state_or_codex_side_effects(self):
        result = self.install('-CheckOnly', '-SkipShortcuts')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / 'codex').exists())
        self.assertFalse((self.root / 'unused-state').exists())

    def test_install_and_repeat_preserve_history_choices_and_service(self):
        first = self.install('-SkipShortcuts')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('pending verification', first.stdout)
        self.assertTrue((self.state / 'runtime.cmd').is_file())
        store = Store(self.state)
        with store.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM projects').fetchone()[0], 0)
        project = self.root / 'project'
        project.mkdir()
        store.set_project(str(project), True)
        store.record({'hook_event_name': 'PostToolUse', 'session_id': 'setup-regression',
                      'cwd': str(project), 'tool_name': 'shell', 'tool_response': 'synthetic failure', 'exit_code': 1})
        registry = self.state / 'review-instance.json'
        registry.write_text('{"sentinel":"do not stop or overwrite"}')
        before = (self.state / 'notes.sqlite3').read_bytes()
        second = self.install('-SkipShortcuts')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual((self.state / 'notes.sqlite3').read_bytes(), before)
        self.assertEqual(registry.read_text(), '{"sentinel":"do not stop or overwrite"}')
        self.assertFalse((self.root / 'codex').exists())
        self.assertFalse((self.root / 'appdata/reviewer').exists())

    def test_missing_python_has_actionable_output(self):
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(ROOT / 'install.ps1'), '-Python',
                                 str(self.root / 'missing.exe'), '-CheckOnly'],
                                capture_output=True, text=True, env=self.env, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Install Python 3.11+', result.stdout)
        self.assertNotIn('Traceback', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
