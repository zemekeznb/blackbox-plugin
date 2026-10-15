"""Reproduce host startup with no Python on PATH and no access to real settings."""
import json
from datetime import datetime
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/blackbox'
sys.path.insert(0, str(PLUGIN / 'scripts'))
import blackbox_runtime as runtime


class BindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='blackbox-runtime-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'host'
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.state / 'notes.sqlite3').touch()
        self.environment = dict(os.environ, CODEX_HOME=str(self.home),
                                USERPROFILE=str(self.root / 'profile'),
                                LOCALAPPDATA=str(self.root / 'appdata'))
        for name in ('BLACKBOX_STATE_DIR', 'TAS_NOTES_STATE_DIR'):
            self.environment.pop(name, None)
        environment = patch.dict(os.environ, self.environment, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def write_raw(self, value):
        target = runtime.binding_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8')
        return target

    def value(self):
        return {'schema_version': 1, 'python': sys.executable, 'state_directory': str(self.state)}

    def test_absent_binding_is_read_only(self):
        self.assertIsNone(runtime.read_binding())
        self.assertIsNone(runtime.configured_state())
        self.assertFalse(self.home.exists())

    def test_profile_fallback_and_explicit_home(self):
        self.assertEqual(runtime.binding_path(), self.home / 'blackbox/runtime.json')
        with patch.dict(os.environ, {'USERPROFILE': str(self.root / 'other-profile')}):
            os.environ.pop('CODEX_HOME')
            self.assertEqual(runtime.binding_path(), self.root / 'other-profile/.codex/blackbox/runtime.json')

    def test_write_read_and_update_interpreter_without_switching_state(self):
        fake_python = self.root / 'old-python.exe'
        fake_python.touch()
        runtime.write_binding(self.state, fake_python)
        fake_python.unlink()
        self.assertEqual(runtime.configured_state(), self.state)
        with self.assertRaisesRegex(runtime.RuntimeBindingError, '^runtime_unavailable$'):
            runtime.read_binding()
        runtime.write_binding(self.state, sys.executable)
        self.assertEqual(runtime.read_binding(), self.value())
        self.assertEqual(runtime.configured_state(), self.state)

    def test_different_state_is_rejected_without_changing_binding(self):
        runtime.write_binding(self.state, sys.executable)
        before = runtime.binding_path().read_bytes()
        other = self.root / 'other-state'
        other.mkdir()
        (other / 'notes.sqlite3').touch()
        with self.assertRaisesRegex(runtime.RuntimeBindingError, '^runtime_binding_conflict$'):
            runtime.write_binding(other, sys.executable)
        self.assertEqual(runtime.binding_path().read_bytes(), before)

    def test_missing_state_or_database_never_creates_a_new_store(self):
        runtime.write_binding(self.state, sys.executable)
        (self.state / 'notes.sqlite3').unlink()
        for present in (True, False):
            if not present:
                self.state.rmdir()
            with self.assertRaisesRegex(runtime.RuntimeBindingError, '^runtime_state_unavailable$'):
                runtime.read_binding()
            with self.assertRaisesRegex(runtime.RuntimeBindingError, '^runtime_state_unavailable$'):
                runtime.write_binding(self.state, sys.executable)
            self.assertEqual(self.state.exists(), present)
        self.assertFalse((self.root / 'appdata').exists())

    def test_invalid_schema_paths_and_duplicates_are_not_missing(self):
        bad = [None, [], {}, dict(self.value(), schema_version=True),
               dict(self.value(), schema_version=2), dict(self.value(), extra='value'),
               dict(self.value(), python='relative.exe'), dict(self.value(), state_directory='relative'),
               dict(self.value(), python=sys.executable + '\n'),
               dict(self.value(), state_directory=str(self.state) + '/../../other')]
        for value in bad:
            with self.subTest(value=value):
                self.write_raw(json.dumps(value))
                with self.assertRaises(runtime.RuntimeBindingError):
                    runtime.read_binding()
        for raw in ('{', 'x' * (runtime.MAX_BINDING_BYTES + 1),
                    json.dumps(self.value())[:-1] + ', "schema_version":1}'):
            self.write_raw(raw)
            with self.assertRaisesRegex(runtime.RuntimeBindingError, '^runtime_binding_invalid$'):
                runtime.read_binding()

    @unittest.skipUnless(os.name == 'nt', 'Windows local path restrictions')
    def test_device_network_and_alternate_stream_paths_are_rejected(self):
        for value in ('C:\\runtime.exe:stream', 'C:\\bad|runtime.exe', '\\\\server\\runtime.exe',
                      '\\\\?\\C:\\runtime.exe', 'C:runtime.exe'):
            self.write_raw(json.dumps(dict(self.value(), python=value)))
            with self.assertRaisesRegex(runtime.RuntimeBindingError, '^runtime_binding_invalid$'):
                runtime.read_binding(require_python=False)

    def test_unreadable_binding_preserves_safe_system_code(self):
        runtime.write_binding(self.state, sys.executable)
        error = PermissionError(13, 'PRIVATE-PATH-SENTINEL')
        with patch.object(Path, 'open', side_effect=error):
            with self.assertRaises(runtime.RuntimeBindingError) as caught:
                runtime.read_binding()
        self.assertEqual(caught.exception.public_code, 'runtime_binding_unreadable')
        self.assertEqual(caught.exception.errno, 13)
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_non_file_binding_and_bad_parent_do_not_fall_back(self):
        target = runtime.binding_path()
        target.mkdir(parents=True)
        with self.assertRaises(runtime.RuntimeBindingError):
            runtime.read_binding()
        target.rmdir()
        target.parent.rmdir()
        target.parent.write_text('not a directory')
        with self.assertRaises(runtime.RuntimeBindingError):
            runtime.read_binding()

    def test_dangling_binding_link_does_not_fall_back(self):
        target = runtime.binding_path()
        target.parent.mkdir(parents=True)
        try:
            target.symlink_to(self.root / 'missing-binding.json')
        except OSError as error:
            self.skipTest('Creating symlinks is unavailable: ' + str(error.errno))
        with self.assertRaises(runtime.RuntimeBindingError):
            runtime.read_binding()
        with self.assertRaises(runtime.RuntimeBindingError):
            runtime.write_binding(self.state, sys.executable)

    def test_atomic_replace_failure_keeps_previous_binding_and_no_temp_file(self):
        runtime.write_binding(self.state, sys.executable)
        target = runtime.binding_path()
        before = target.read_bytes()
        with patch.object(runtime.os, 'replace', side_effect=PermissionError(13, 'PRIVATE')):
            with self.assertRaisesRegex(runtime.RuntimeBindingError, '^runtime_binding_write_failed$'):
                runtime.write_binding(self.state, sys.executable)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(list(target.parent.glob('.runtime-*.json')), [])


@unittest.skipUnless(os.name == 'nt', 'Windows host launcher')
class WindowsLauncherTests(BindingTests):
    def setUp(self):
        super().setUp()
        self.plugin = self.root / '插件 %!& space'
        shutil.copytree(PLUGIN, self.plugin, ignore=shutil.ignore_patterns('__pycache__'))
        self.home = self.root / '主机 %!& space'
        old_state = self.state
        self.state = self.root / '物理状态 %!& space'
        old_state.rename(self.state)
        os.environ['CODEX_HOME'] = str(self.home)
        self.environment.update(CODEX_HOME=str(self.home),
                                PATH=str(Path(os.environ['SystemRoot']) / 'System32'))

    def launch(self, *, hook=False, data=b'', environment=None):
        if hook:
            args = ['cmd.exe', '/d', '/c', 'scripts\\launch.cmd']
        else:
            configured = json.loads((self.plugin / '.mcp.json').read_text())['mcpServers']['blackbox_reader']
            args = [configured['command'], *configured['args']]
        return subprocess.run(args, cwd=self.plugin, input=data, capture_output=True,
                              env=environment or self.environment, timeout=12)

    def test_actual_mcp_manifest_starts_without_path_python(self):
        runtime.write_binding(self.state, sys.executable)
        request = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n' + \
                  b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        before = (self.state / 'notes.sqlite3').read_bytes()
        result = self.launch(data=request)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'blackbox-reader')
        opener = next(tool for tool in replies[1]['result']['tools'] if tool['name'] == 'open_reader')
        self.assertEqual(opener['_meta']['openai/ui']['entrypoints'], [{'type': 'global'}, {'type': 'thread'}])
        self.assertEqual((self.state / 'notes.sqlite3').read_bytes(), before)
        self.assertFalse((self.root / 'appdata').exists())

    def test_missing_binding_mcp_does_not_create_any_state(self):
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'runtime_not_configured', result.stderr)
        self.assertEqual(result.stdout, b'')
        self.assertFalse(self.home.exists())
        self.assertFalse((self.root / 'appdata').exists())

    def test_broken_binding_never_uses_hook_legacy_fallback(self):
        self.write_raw('{"PRIVATE-PATH-SENTINEL":')
        marker = self.root / 'legacy-was-executed'
        (self.plugin / 'scripts/launch-legacy.cmd').write_text('@echo legacy>"' + str(marker) + '"\n')
        for hook in (False, True):
            result = self.launch(hook=hook)
            self.assertEqual(result.returncode, 0 if hook else 1)
            self.assertIn(b'runtime_binding_invalid', result.stderr)
            self.assertNotIn(b'PRIVATE', result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((self.state / 'last-error.json').exists())
            self.assertFalse((self.root / 'appdata').exists())

    def test_missing_runtime_state_and_database_fail_before_product_launch(self):
        value = self.value()
        checks = [('python', str(self.root / 'missing.exe'), b'runtime_unavailable'),
                  ('state_directory', str(self.root / 'missing-state'), b'runtime_state_unavailable')]
        for key, replacement, expected in checks:
            self.write_raw(json.dumps(dict(value, **{key: replacement})))
            result = self.launch()
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn(expected, result.stderr)
        self.write_raw(json.dumps(value))
        (self.state / 'notes.sqlite3').unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(b'runtime_state_unavailable', result.stderr)
        self.assertFalse((self.state / 'notes.sqlite3').exists())
        self.assertFalse((self.root / 'appdata').exists())

    def test_conflicting_environment_state_fails_without_switching(self):
        runtime.write_binding(self.state, sys.executable)
        other = self.root / 'another-state'
        for name in ('BLACKBOX_STATE_DIR', 'TAS_NOTES_STATE_DIR'):
            result = self.launch(environment=dict(self.environment, **{name: str(other)}))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn(b'runtime_state_conflict', result.stderr)
            self.assertFalse(other.exists())
            hook = self.launch(hook=True, environment=dict(self.environment, **{name: str(other)}))
            self.assertEqual(hook.returncode, 0)
            self.assertFalse((self.state / 'last-error.json').exists())

    def test_hook_failure_marker_uses_only_the_verified_bound_store(self):
        self.write_raw(json.dumps(dict(self.value(), python=str(self.root / 'missing-python.exe'))))
        marker = self.state / 'last-error.json'
        mcp = self.launch()
        self.assertEqual(mcp.returncode, 1)
        self.assertFalse(marker.exists())
        hook = self.launch(hook=True)
        self.assertEqual(hook.returncode, 0)
        self.assertIn(b'runtime_unavailable', hook.stderr)
        saved = json.loads(marker.read_text())
        self.assertEqual(saved['code'], 'LauncherRuntimeFailed')
        self.assertEqual(saved['scope'], 'unknown_project')
        self.assertIsNotNone(datetime.fromisoformat(saved['time']).tzinfo)
        self.assertEqual(len(saved['epoch']), 32)
        self.assertFalse((self.root / 'appdata').exists())
        self.assertEqual(list(self.state.glob('.launcher-error-*.tmp')), [])

    def test_hook_child_nonzero_is_fail_open_but_preserves_gap_marker(self):
        runtime.write_binding(self.state, sys.executable)
        (self.plugin / 'scripts/blackbox.py').write_text('raise SystemExit(23)\n')
        result = self.launch(hook=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'LauncherRuntimeFailed', result.stderr)
        saved = json.loads((self.state / 'last-error.json').read_text())
        self.assertEqual(saved['code'], 'LauncherRuntimeFailed')

    def test_bound_real_hook_preserves_project_opt_in(self):
        from blackbox_core import Store
        store = Store(self.state)
        runtime.write_binding(self.state, sys.executable)
        before = store.status()
        project = self.root / 'unselected-project'
        project.mkdir()
        event = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'synthetic-runtime-test',
                 'cwd': str(project), 'prompt': 'PRIVATE-NOT-AUTHORIZED'}
        result = self.launch(hook=True, data=json.dumps(event).encode('utf-8'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr, b'')
        self.assertEqual(store.status()['projects'], before['projects'])
        self.assertEqual(store.listing(), [])
        self.assertFalse((self.state / 'last-error.json').exists())
        self.assertNotIn(b'PRIVATE-NOT-AUTHORIZED', (self.state / 'notes.sqlite3').read_bytes())

    def test_duplicate_binding_key_cannot_write_a_hook_marker(self):
        raw = json.dumps(dict(self.value(), python=str(self.root / 'missing-python.exe')))
        self.write_raw(raw[:-1] + ', "schema_version": 1}')
        result = self.launch(hook=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'runtime_binding_invalid', result.stderr)
        self.assertFalse((self.state / 'last-error.json').exists())

    def test_legacy_hook_runtime_still_works_only_when_binding_absent(self):
        # Preserve the existing legacy launcher's supported path shape. The new
        # binding launcher, tested separately, also supports CMD metacharacters.
        ordinary_plugin = self.root / 'legacy-plugin'
        self.plugin.rename(ordinary_plugin)
        self.plugin = ordinary_plugin
        # Synthetic script records only that stdin passed through the old shim.
        (self.plugin / 'scripts/blackbox.py').write_text(
            'import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n', encoding='utf-8')
        legacy = self.root / 'legacy-state'
        legacy.mkdir()
        (legacy / 'runtime.cmd').write_text('@"' + sys.executable + '" %*\n', encoding='ascii')
        result = self.launch(hook=True, data='原样输入\n'.encode('utf-8'),
                             environment=dict(self.environment, BLACKBOX_STATE_DIR=str(legacy)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '原样输入\n'.encode('utf-8'), result.stderr)
        self.assertFalse(self.home.exists())

    def test_inherited_bytes_interactive_protocol_and_actual_child_exit_code(self):
        runtime.write_binding(self.state, sys.executable)
        (self.plugin / 'scripts/blackbox_mcp.py').write_text(
            'import sys\nfor unused in range(2):\n'
            '    sys.stdout.buffer.write(sys.stdin.buffer.readline())\n'
            '    sys.stdout.buffer.flush()\nraise SystemExit(23)\n', encoding='utf-8')
        process = subprocess.Popen(['cmd.exe', '/d', '/c', 'scripts\\mcp.cmd'], cwd=self.plugin,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=self.environment)
        first = queue.Queue()
        reader = threading.Thread(target=lambda: first.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            body = '协议中文 %!&\n'.encode('utf-8')
            process.stdin.write(body)
            process.stdin.flush()
            self.assertEqual(first.get(timeout=8), body)
            process.stdin.write(b'{"id":2}\n')
            process.stdin.flush()
            output, errors = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 23, errors)
            self.assertEqual(output, b'{"id":2}\n')
            self.assertEqual(errors, b'')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
