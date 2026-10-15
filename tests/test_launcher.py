import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from urllib.parse import urlsplit, parse_qs

ENTRY = Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts/blackbox.py'
sys.path.insert(0,str(ENTRY.parent))
from blackbox_core import Store, NotesError
from blackbox_launcher import (probe_instance, ensure_reviewer, stop_reviewer, request_shutdown,
    resolve_state_directory, runtime_metadata, build_id, normalized_file_bytes, background)
from open_notes import parse_options

class LauncherTests(unittest.TestCase):
    def test_legacy_health_service_is_still_recognized(self):
        response=Mock(status=200)
        response.read.return_value=json.dumps({'service':'tas-notes-review','protocol':1,'pid':123,'build':'a'*64}).encode()
        connection=Mock()
        connection.getresponse.return_value=response
        with patch('blackbox_launcher.http.client.HTTPConnection',return_value=connection):
            self.assertEqual(probe_instance({'pid':123,'url':'http://127.0.0.1:12345/#token='+'a'*43})['service'],'tas-notes-review')

    def test_existing_database_resolves_file_overlay_before_directory(self):
        with tempfile.TemporaryDirectory(prefix='notes-overlay-test-') as root:
            logical, physical = Path(root).resolve() / 'logical', Path(root).resolve() / 'physical'
            logical.mkdir(); physical.mkdir()
            (logical / 'notes.sqlite3').write_bytes(b'placeholder')
            with patch('blackbox_launcher.physical_file_path', return_value=str(physical / 'notes.sqlite3')) as file_path, \
                    patch('blackbox_launcher.physical_directory_path', return_value=str(logical)) as directory_path:
                self.assertEqual(resolve_state_directory(logical), physical)
                file_path.assert_called_once_with(logical / 'notes.sqlite3')
                directory_path.assert_not_called()
            # A new explicit state still uses its own directory, never another DB.
            (logical / 'notes.sqlite3').unlink()
            with patch('blackbox_launcher.physical_file_path') as file_path:
                self.assertEqual(resolve_state_directory(logical), logical)
                file_path.assert_not_called()

    def test_reviewer_survives_launch_command_exit(self):
        with tempfile.TemporaryDirectory(prefix='notes-launch-test-') as root:
            launch = subprocess.run([sys.executable, str(ENTRY), '--state-dir', root,
                                     'serve', '--background'], capture_output=True, timeout=15)
            self.assertEqual(launch.returncode, 0, launch.stderr)
            value = json.loads(launch.stdout)
            url = urlsplit(value['url'])
            try:
                self.assertEqual(url.hostname, '127.0.0.1')
                token = parse_qs(url.fragment)['token'][0]
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
                try:
                    connection.request('GET', '/api/records', headers={'Authorization':'Bearer '+token})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), [])
                finally:
                    connection.close()
                self.assertEqual(list(Path(root).glob('.review-ready-*')), [])
            finally:
                self.assertTrue(request_shutdown(value)['stopped'])

    def test_invalid_registry_never_contacts_remote(self):
        with patch('blackbox_launcher.http.client.HTTPConnection') as connection:
            for value in [None,{}, {'pid':1,'url':'https://example.org/#token=secret'},
                          {'pid':1,'url':'http://localhost:80/#token='+'a'*43},
                          {'pid':1,'url':'http://127.0.0.1:99999/#token='+'a'*43}]:
                self.assertIsNone(probe_instance(value))
            connection.assert_not_called()

    def test_concurrent_open_reuses_and_stale_registry_recovers(self):
        with tempfile.TemporaryDirectory(prefix='notes-open-test-') as root:
            registry=Path(root)/'review-instance.json'
            registry.write_text('{broken')
            def launch(_):
                result=subprocess.run([sys.executable,str(ENTRY),'--state-dir',root,
                                       'open','--no-browser'],capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0,result.stderr)
                return json.loads(result.stdout)
            with ThreadPoolExecutor(max_workers=3) as pool:
                values=list(pool.map(launch,range(3)))
            first=values[0]
            try:
                self.assertEqual(len({v['url'] for v in values}),1)
                self.assertEqual(sum(not v['reused'] for v in values),1)
            finally:
                self.assertTrue(stop_reviewer(Store(root))['stopped'])
            # An invalid registry is not used as a kill instruction or browser URL.
            registry.write_text(json.dumps({'pid':os.getpid(),'url':'http://example.org/'}))
            second=launch(0)
            try:
                self.assertFalse(second['reused'])
                self.assertTrue(launch(0)['reused'])
            finally:
                self.assertTrue(stop_reviewer(Store(root))['stopped'])

    def test_explicit_state_overrides_environment_and_shortcut_uses_same_default(self):
        with tempfile.TemporaryDirectory(prefix='notes-state-test-') as root:
            base = Path(root)
            explicit, override = base / 'explicit', base / 'custom'
            explicit.mkdir(); override.mkdir()
            with patch.dict(os.environ, {'BLACKBOX_STATE_DIR': str(override), 'LOCALAPPDATA': str(base)}):
                self.assertEqual(resolve_state_directory(), resolve_state_directory(override))
                self.assertEqual(parse_options([]).state_dir, resolve_state_directory(override))
                self.assertEqual(parse_options(['--state-dir', str(explicit)]).state_dir,
                                 resolve_state_directory(explicit))
                self.assertFalse((base / 'Blackbox').exists())

    def test_build_metadata_and_newline_normalization(self):
        with tempfile.TemporaryDirectory(prefix='notes-build-test-') as root:
            base = Path(root)
            for name, newline in (('one', b'\n'), ('two', b'\r\n')):
                plugin = base / name
                (plugin / 'scripts').mkdir(parents=True)
                (plugin / '.codex-plugin').mkdir()
                (plugin / 'scripts/a.py').write_bytes(newline.join((b'first', b'second', b'')))
                (plugin / '.codex-plugin/plugin.json').write_text('{"version":"1.2.3"}')
            first, second = base / 'one', base / 'two'
            self.assertEqual(build_id(first), build_id(second))
            self.assertEqual(normalized_file_bytes(first / 'scripts/a.py'),
                             normalized_file_bytes(second / 'scripts/a.py'))
            (second / '.codex-plugin/plugin.json').write_text('{"version":"1.2.4"}')
            self.assertNotEqual(build_id(first), build_id(second))
            metadata = runtime_metadata(Store(base / 'state'))
            self.assertRegex(metadata['build'], r'^[a-f0-9]{64}$')
            self.assertNotEqual(metadata['version'], 'unknown')
            self.assertEqual(metadata['state_dir'], str(resolve_state_directory(base / 'state')))

    def test_verified_other_build_does_not_create_hidden_duplicate(self):
        with tempfile.TemporaryDirectory(prefix='notes-version-test-') as root:
            store = Store(root)
            saved = {'pid': 1234, 'url': 'http://127.0.0.1:8080/#token=' + 'a' * 43}
            (store.directory / 'review-instance.json').write_text(json.dumps(saved))
            metadata = dict(runtime_metadata(store), pid=1234, build='0' * 64)
            with patch('blackbox_launcher.probe_instance', return_value=metadata), \
                    patch('blackbox_launcher.background') as spawn:
                with self.assertRaises(NotesError) as raised:
                    ensure_reviewer(store)
                self.assertEqual(raised.exception.public_code, 'reviewer_build_changed')
                spawn.assert_not_called()
            self.assertEqual(json.loads((store.directory / 'review-instance.json').read_text()), saved)

    def test_transient_health_failure_retries_before_starting(self):
        with tempfile.TemporaryDirectory(prefix='notes-retry-test-') as root:
            store = Store(root)
            saved = {'pid': 1234, 'url': 'http://127.0.0.1:8080/#token=' + 'a' * 43}
            (store.directory / 'review-instance.json').write_text(json.dumps(saved))
            metadata = dict(runtime_metadata(store), pid=1234)
            with patch('blackbox_launcher.probe_instance', side_effect=[None, metadata]), \
                    patch('blackbox_launcher.background') as spawn:
                self.assertTrue(ensure_reviewer(store)['reused'])
                spawn.assert_not_called()

    def test_shutdown_refuses_unverified_and_unsupported_services(self):
        saved = {'pid': 1234, 'url': 'http://127.0.0.1:8080/#token=' + 'a' * 43}
        with patch('blackbox_launcher.probe_instance', return_value=None), \
                patch('blackbox_launcher.http.client.HTTPConnection') as connection:
            self.assertEqual(request_shutdown(saved)['reason'], 'not_running')
            connection.assert_not_called()
        with patch('blackbox_launcher.probe_instance', return_value={'build': '0' * 64}), \
                patch('blackbox_launcher.http.client.HTTPConnection') as connection:
            self.assertEqual(request_shutdown(saved)['reason'], 'shutdown_not_supported')
            connection.assert_not_called()

    def test_background_cli_registers_reuses_and_stops(self):
        with tempfile.TemporaryDirectory(prefix='notes-background-cli-') as root:
            def cli(*args):
                result = subprocess.run([sys.executable, str(ENTRY), '--state-dir', root, *args],
                                        capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            first = cli('serve', '--background')
            try:
                registry = Path(root) / 'review-instance.json'
                self.assertEqual(json.loads(registry.read_text())['url'], first['url'])
                self.assertFalse(first['reused'])
                for args in (('serve', '--background'), ('open', '--no-browser')):
                    reused = cli(*args)
                    self.assertTrue(reused['reused'])
                    self.assertEqual(reused['url'], first['url'])
                self.assertTrue(cli('stop')['stopped'])
                self.assertFalse(registry.exists())
                self.assertIsNone(probe_instance(first))
            finally:
                request_shutdown(first)

    def test_authentication_failure_and_state_mismatch_never_shutdown(self):
        saved = {'pid': 1234, 'url': 'http://127.0.0.1:8080/#token=' + 'a' * 43}
        metadata = {'service': 'blackbox-review', 'protocol': 1, 'pid': 1234,
                    'build': '0' * 64, 'state_dir': 'another-state', 'capabilities': ['shutdown']}
        with patch('blackbox_launcher.http.client.HTTPConnection') as constructor:
            connection = constructor.return_value
            response = connection.getresponse.return_value
            response.status = 401
            response.read.return_value = b'{}'
            self.assertFalse(request_shutdown(saved)['stopped'])
            self.assertEqual(connection.request.call_count, 1)
            self.assertEqual(connection.request.call_args.args[0], 'GET')
            connection.request.reset_mock()
            response.status = 200
            response.read.return_value = json.dumps(metadata).encode()
            self.assertFalse(request_shutdown(saved, 'this-state')['stopped'])
            self.assertEqual(connection.request.call_count, 1)
            self.assertEqual(connection.request.call_args.args[0], 'GET')

    def test_failed_registry_publication_stops_verified_owned_child(self):
        with tempfile.TemporaryDirectory(prefix='notes-start-cleanup-') as root:
            store = Store(root)
            captured = []
            def failed_publish(registry, value):
                captured.append(value)
                raise OSError('controlled publication failure')
            with patch('blackbox_launcher.write_registry', side_effect=failed_publish), \
                    patch('blackbox_launcher.request_shutdown', wraps=request_shutdown) as stop:
                with self.assertRaises(OSError):
                    ensure_reviewer(store)
                self.assertEqual(len(captured), 1)
                self.assertEqual(stop.call_count, 1)
                self.assertIsNone(probe_instance(captured[0]))
                self.assertFalse((store.directory / 'review-instance.json').exists())
                self.assertEqual(list(store.directory.glob('.review-ready-*')), [])

    def test_unhealthy_own_child_is_terminated_without_registry_pid_kill(self):
        with tempfile.TemporaryDirectory(prefix='notes-child-cleanup-') as root:
            store = Store(root)
            child = Mock(pid=54321)
            child.poll.return_value = None
            child.wait.side_effect = [subprocess.TimeoutExpired('owned-child', 1), 0]
            def launch(args, **options):
                Path(args[-1]).write_text(json.dumps({'pid': child.pid,
                    'url': 'http://127.0.0.1:8080/#token=' + 'a' * 43}))
                return child
            with patch('blackbox_launcher.subprocess.Popen', side_effect=launch), \
                    patch('blackbox_launcher.probe_instance', return_value=None):
                with self.assertRaises(NotesError):
                    background(store)
            child.terminate.assert_called_once()
            self.assertEqual(list(store.directory.glob('.review-ready-*')), [])

    def test_stop_authenticated_service_and_reopen(self):
        with tempfile.TemporaryDirectory(prefix='notes-stop-test-') as root:
            store = Store(root)
            first = ensure_reviewer(store)
            try:
                self.assertEqual(first['state_dir'], str(resolve_state_directory(store.directory)))
                self.assertTrue(stop_reviewer(store)['stopped'])
                self.assertFalse((store.directory / 'review-instance.json').exists())
                self.assertEqual(stop_reviewer(store)['reason'], 'not_running')
                second = ensure_reviewer(store)
                self.assertFalse(second['reused'])
                self.assertNotEqual(first['url'], second['url'])
            finally:
                stop_reviewer(store)

if __name__ == '__main__': unittest.main()
