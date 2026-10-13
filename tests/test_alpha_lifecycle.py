"""Isolated Alpha lifecycle acceptance; synthetic CLI input is not host evidence."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'
sys.path.insert(0, str(SCRIPTS))
import tas_notes_core as core
import tas_notes_anomalies as anomalies
from tas_notes_web import make_server

TRACE = ('Traceback (most recent call last):\n'
         '  File "synthetic_lifecycle.py", line 1, in <module>\n'
         'RuntimeError: isolated lifecycle fixture\n')


class AlphaLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='blackbox-alpha-lifecycle-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        self.project = self.root / 'synthetic-project'
        self.project.mkdir()
        self.env = dict(os.environ, TAS_NOTES_STATE_DIR=str(self.state),
                        LOCALAPPDATA=str(self.root / 'appdata'), CODEX_HOME=str(self.root / 'codex'))

    def cli(self, *arguments, state=None, payload=None, expected=0):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'tas_notes.py'),
                                 '--state-dir', str(state or self.state), *arguments],
                                input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, encoding='utf-8',
                                env=self.env, cwd=self.project, timeout=25)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def event(self, identifier):
        return {'hook_event_name': 'PostToolUse', 'cwd': str(self.project),
                'session_id': 'lifecycle-synthetic-session', 'event_id': identifier,
                'tool_name': 'Bash', 'tool_input': {'command': 'python synthetic_lifecycle.py'},
                'tool_response': TRACE}

    def enable(self, *, state=None):
        result = self.cli('enable', str(self.project), '--accept-capture-scope', state=state)
        self.assertTrue(result['enabled'])
        # Synthetic lifecycle fixture: simulate metadata verification, not host capture.
        from tas_notes_admission import admit
        with patch('tas_notes_admission.lookup', return_value='allowed'):
            admit(core.Store(state or self.state), self.event('fixture-admission'))

    def export_anomaly(self, store, identifier, snapshot):
        # Exercise the actual local download route without launching any browser
        # or connecting to a user's running reader/server.
        server, token = make_server(store, 'isolated-lifecycle-token')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', f'/api/anomaly-export/{identifier}?through={snapshot}',
                               headers={'Authorization': 'Bearer ' + token})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn('text/markdown', response.getheader('Content-Type'))
            return response.read().decode('utf-8')
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_opt_in_disable_capacity_backup_restore_and_export_lifecycle(self):
        self.cli('configure-runtime')
        self.enable()
        self.cli('hook', payload=self.event('recorded-before-disable'))
        store = core.Store(self.state)
        page = anomalies.listing(store)
        self.assertEqual(page['total_count'], 1)
        item = page['items'][0]
        snapshot = page['snapshot_seq']
        detail = anomalies.detail(store, item['id'], through=snapshot)
        original_events = detail['events']
        original_export = self.export_anomaly(store, item['id'], snapshot)
        self.assertEqual(original_export, detail['markdown'])

        disabled = self.cli('disable', str(self.project))
        self.assertFalse(disabled['enabled'])
        disabled_database = store.database.read_bytes()
        self.cli('hook', payload=self.event('ignored-after-disable'))
        self.assertEqual(store.database.read_bytes(), disabled_database)
        self.assertEqual(anomalies.listing(store)['total_count'], 1)
        self.assertEqual(self.export_anomaly(store, item['id'], snapshot), original_export)

        # Explicitly opt back in only inside the fixture so a capacity refusal
        # is actually exercised; disabled projects must continue to be ignored.
        self.enable()
        before_capacity = store.database.read_bytes()
        with patch.object(core, 'MAX_DATABASE_BYTES', 1):
            with self.assertRaises(core.CaptureLimit) as rejected:
                store.record(self.event('rejected-at-capacity'))
            self.assertEqual(rejected.exception.code, 'DatabaseCapacityReached')
            self.assertEqual(store.status()['capture_health']['state'], 'capacity_reached')
            self.assertEqual(anomalies.listing(store)['total_count'], 1)
            self.assertEqual(self.export_anomaly(store, item['id'], snapshot), original_export)
        self.assertEqual(store.database.read_bytes(), before_capacity)

        saved = self.root / 'lifecycle-backup.sqlite3'
        original_database = store.database.read_bytes()
        backup = self.cli('backup', str(saved))
        self.assertTrue(backup['verified'])
        self.assertTrue(backup['contains_sensitive_local_data'])
        self.assertEqual(backup['counts']['events'], 1)
        self.assertEqual(backup['sha256'], hashlib.sha256(saved.read_bytes()).hexdigest())
        self.assertEqual(store.database.read_bytes(), original_database)

        target = self.root / 'restored-state'
        restored = self.cli('restore', str(saved), str(target))
        self.assertTrue(restored['verified'])
        self.assertTrue(restored['all_projects_disabled'])
        self.assertFalse(restored['runtime_configured'])
        self.assertEqual(restored['capture_continuity'], 'unknown_after_restore')
        self.assertFalse((target / 'runtime.cmd').exists())
        restored_store = core.Store(target, read_only=True)
        status = self.cli('status', state=target)
        self.assertFalse(status['projects'][0]['enabled'])
        self.assertEqual(status['host_integration'], 'not_checked')
        self.assertEqual(status['continuous_capture'], 'unknown')
        self.assertEqual(status['last_unscoped_capture_error']['code'], 'RestoreContinuityUnknown')
        self.assertEqual(anomalies.detail(restored_store, item['id'], through=snapshot)['events'], original_events)
        self.assertEqual(self.export_anomaly(restored_store, item['id'], snapshot), original_export)
        restored_database = restored_store.database.read_bytes()
        self.cli('hook', state=target, payload=self.event('ignored-after-restore'))
        self.assertEqual(restored_store.database.read_bytes(), restored_database)
        self.assertEqual(store.database.read_bytes(), original_database)
        self.assertTrue(self.cli('status')['projects'][0]['enabled'])
        self.assertFalse((self.root / 'codex').exists())

    def test_backup_and_restore_refuse_existing_targets_without_changes(self):
        self.enable()
        self.cli('hook', payload=self.event('original'))
        store = core.Store(self.state)
        original_database = store.database.read_bytes()
        saved = self.root / 'snapshot.sqlite3'
        self.cli('backup', str(saved))
        saved_bytes = saved.read_bytes()
        self.cli('backup', str(saved), expected=1)
        self.assertEqual(saved.read_bytes(), saved_bytes)
        self.cli('restore', str(saved), str(self.state), expected=1)
        self.assertEqual(store.database.read_bytes(), original_database)
        existing = self.root / 'existing-directory'
        existing.mkdir()
        sentinel = existing / 'preserve.txt'
        sentinel.write_text('preserve this unrelated file')
        self.cli('restore', str(saved), str(existing), expected=1)
        self.assertEqual(sentinel.read_text(), 'preserve this unrelated file')
        self.assertFalse((existing / 'notes.sqlite3').exists())

    def test_stopping_reader_does_not_disable_capture_or_delete_history(self):
        self.enable()
        self.cli('hook', payload=self.event('before-reader-stop'))
        started = self.cli('serve', '--background')
        try:
            self.assertEqual(started['state_dir'], str(self.state.resolve()))
            self.assertTrue(self.cli('stop')['stopped'])
            self.assertTrue(self.cli('status')['projects'][0]['enabled'])
            self.cli('hook', payload=self.event('after-reader-stop'))
            self.assertEqual(anomalies.listing(core.Store(self.state))['total_count'], 2)
        finally:
            self.cli('stop')


if __name__ == '__main__':
    unittest.main()
