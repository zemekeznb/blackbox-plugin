"""Alpha opt-in and receipt metadata must not imply real-host verification."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'
sys.path.insert(0, str(SCRIPTS))
import tas_notes_core as core
from tas_notes_diagnostics import marker_name


class AlphaCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='blackbox-alpha-capture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'state'

    def cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = core.main(['--state-dir', str(self.state), *arguments])
        return code, output.getvalue(), errors.getvalue()

    def store(self, *, enabled=True, configured=True):
        store = core.Store(self.state)
        store.set_project(str(self.project), enabled)
        if configured:
            (self.state / 'runtime.cmd').write_text('@"' + sys.executable + '" %*\n', encoding='ascii')
        return store

    def record(self, store, project=None):
        return store.record({'hook_event_name': 'PostToolUse', 'session_id': 'alpha-synthetic',
                             'cwd': str(project or self.project), 'tool_name': 'shell',
                             'tool_input': {'command': 'SENSITIVE_FIXTURE_ONLY'},
                             'tool_response': 'synthetic successful return', 'exit_code': 0})

    def marker(self, stamp, *, project=True, code='PermissionError'):
        name = marker_name(str(self.project)) if project else 'capture-unknown.json'
        core.write_diagnostic_marker(self.state, name, {
            'code': code, 'time': stamp, 'scope': 'project' if project else 'unknown_project'})

    def test_unacknowledged_enable_creates_nothing(self):
        code, output, errors = self.cli('enable', str(self.project))
        self.assertEqual(code, 2)
        self.assertEqual(output, '')
        self.assertIn('storage is not limited to errors', errors)
        self.assertIn('--accept-capture-scope', errors)
        self.assertIn(str(self.state), errors)
        self.assertIn('disable command', errors)
        self.assertFalse(self.state.exists())

    def test_unacknowledged_enable_does_not_change_existing_choices_or_data(self):
        self.store(enabled=False)
        before = (self.state / 'notes.sqlite3').read_bytes()
        code, _, _ = self.cli('enable', str(self.project))
        self.assertEqual(code, 2)
        self.assertEqual((self.state / 'notes.sqlite3').read_bytes(), before)

    def test_acknowledged_enable_emits_json_and_only_enables_exact_project(self):
        child, sibling = self.project / 'child', self.root / 'sibling'
        child.mkdir(); sibling.mkdir()
        code, output, errors = self.cli('enable', str(self.project), '--accept-capture-scope')
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output), {'project': str(self.project.resolve()), 'enabled': True})
        self.assertIn('Scope acknowledged', errors)
        self.assertIn('ordinary tool inputs', errors)
        store = core.Store(self.state)
        self.assertEqual(self.record(store)['status'], 'recorded')
        for other in (child, sibling):
            self.assertEqual(self.record(store, other)['reason'], 'project_not_enabled')
        status = store.status()
        self.assertEqual(len(status['projects']), 1)
        self.assertEqual(status['projects'][0]['event_count'], 1)
        self.assertEqual(status['host_integration'], 'not_checked')
        self.assertEqual(status['continuous_capture'], 'unknown')

    def test_disabled_and_missing_runtime_are_distinct(self):
        store = self.store(enabled=False, configured=False)
        status = store.status()
        self.assertEqual(status['capture_health']['state'], 'disabled')
        self.assertEqual(status['projects'][0]['capture_state'], 'disabled')
        store.set_project(str(self.project), True)
        status = store.status()
        self.assertEqual(status['capture_health']['state'], 'runtime_problem')
        self.assertEqual(status['projects'][0]['capture_state'], 'runtime_problem')
        self.assertIsNone(status['capture_health']['last_recorded_at'])

    def test_invalid_or_missing_runtime_target_is_not_executed(self):
        store = self.store()
        for body in ('@echo malicious-runtime-command\n', '@"' + str(self.root / 'missing.exe') + '" %*\n'):
            (self.state / 'runtime.cmd').write_text(body)
            with patch('subprocess.run', side_effect=AssertionError('runtime executed')):
                status = store.status()
            self.assertEqual(status['capture_health']['state'], 'runtime_problem')

    def test_awaiting_event_and_received_are_not_host_verification(self):
        store = self.store()
        initial = store.status()
        self.assertEqual(initial['capture_health']['state'], 'awaiting_event')
        self.assertEqual(initial['projects'][0]['capture_state'], 'awaiting_event')
        self.record(store)
        received = store.status()
        self.assertEqual(received['capture_health']['state'], 'events_received')
        self.assertEqual(received['projects'][0]['capture_state'], 'events_received')
        self.assertEqual(received['capture_health']['last_recorded_at'], received['projects'][0]['last_recorded_event']['captured'])
        self.assertEqual(received['capture_health']['scope'], 'local_metadata_only')
        self.assertEqual(received['host_integration'], 'not_checked')
        self.assertEqual(received['continuous_capture'], 'unknown')
        self.assertNotIn('SENSITIVE_FIXTURE_ONLY', json.dumps(received))

    def test_actual_capacity_and_diagnostic_capacity_are_distinct(self):
        store = self.store()
        with patch.object(core, 'MAX_DATABASE_BYTES', 1):
            status = store.status()
        self.assertEqual(status['capture_health']['state'], 'capacity_reached')
        self.assertEqual(status['projects'][0]['capture_state'], 'capacity_reached')
        self.marker(None, code='DatabaseCapacityReached')
        status = store.status()
        self.assertEqual(status['capture_health']['state'], 'review_diagnostics')
        self.assertFalse(status['storage']['capacity_reached'])

    def test_old_diagnostic_remains_history_new_or_unknown_needs_review(self):
        store = self.store()
        self.record(store)
        recorded_at = datetime.fromisoformat(store.status()['capture_health']['last_recorded_at'])
        for stamp in (None, 'invalid timestamp', (recorded_at + timedelta(seconds=1)).isoformat(), recorded_at.isoformat()):
            self.marker(stamp)
            status = store.status()
            self.assertEqual(status['capture_health']['state'], 'review_diagnostics')
            self.assertTrue(status['projects'][0]['capture_diagnostics_need_review'])
        self.marker((recorded_at - timedelta(seconds=1)).isoformat())
        status = store.status()
        self.assertEqual(status['capture_health']['state'], 'events_received')
        self.assertFalse(status['projects'][0]['capture_diagnostics_need_review'])
        self.assertIsNotNone(status['projects'][0]['last_capture_error'])

    def test_unscoped_diagnostic_is_visible_without_attributing_a_failed_project(self):
        store = self.store()
        self.marker(None, project=False)
        status = store.status()
        self.assertEqual(status['capture_health']['state'], 'review_diagnostics')
        self.assertTrue(status['capture_health']['unscoped_diagnostics_need_review'])
        self.assertIsNone(status['projects'][0]['last_capture_error'])
        store.set_project(str(self.project), False)
        status = store.status()
        self.assertEqual(status['projects'][0]['capture_state'], 'disabled')
        self.assertEqual(status['capture_health']['state'], 'review_diagnostics')
        self.assertEqual(status['capture_health']['enabled_project_count'], 0)

    def test_old_unscoped_diagnostic_does_not_claim_current_fault(self):
        store = self.store()
        self.record(store)
        stamp = datetime.fromisoformat(store.status()['capture_health']['last_recorded_at']) - timedelta(seconds=1)
        self.marker(stamp.isoformat(), project=False)
        status = store.status()
        self.assertEqual(status['capture_health']['state'], 'events_received')
        self.assertFalse(status['capture_health']['unscoped_diagnostics_need_review'])
        self.assertIsNotNone(status['last_unscoped_capture_error'])

    def test_status_never_reads_event_bodies(self):
        store = self.store()
        self.record(store)
        connect = store.connect

        @contextmanager
        def metadata_only():
            with connect() as connection:
                connection.set_authorizer(lambda action, table, column, *_:
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'events' and column in ('content', 'digest')
                    else sqlite3.SQLITE_OK)
                yield connection

        with patch.object(store, 'connect', metadata_only):
            status = store.status()
        self.assertEqual(status['capture_health']['state'], 'events_received')

    def test_disabled_project_history_does_not_establish_receipt_for_new_project(self):
        store = self.store()
        self.record(store)
        store.set_project(str(self.project), False)
        another = self.root / 'new-project'
        another.mkdir()
        store.set_project(str(another), True)
        status = store.status()
        self.assertEqual(status['capture_health']['state'], 'awaiting_event')
        self.assertIsNotNone(status['capture_health']['last_recorded_at'])


if __name__ == '__main__':
    unittest.main()
