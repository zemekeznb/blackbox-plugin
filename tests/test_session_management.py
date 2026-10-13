import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store, NotesError
from tas_notes_sessions import inspect_session, manage_session
from tas_notes_anomalies import contexts, listing, anomaly_types, detail
from tas_notes_mcp import Reader, TOOLS
from tas_notes_backup import backup, restore
from tas_notes_web import make_server


class SessionManagementTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.store = Store(self.root / 'state'); self.store.set_project(str(self.project), True)
        self.number = 0
        self.first = self.emit('a')
        self.other = self.emit('b')

    def emit(self, session, kind='RuntimeError'):
        self.number += 1
        return self.store.record({'cwd': str(self.project), 'session_id': session,
            'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_use_id': str(self.number),
            'tool_input': {'command': 'synthetic'},
            'tool_response': 'Traceback (most recent call last):\n  File "test.py", line 1\n' + kind + ': synthetic\n'})['investigation']

    def action(self, identifier, action):
        return {'investigation': identifier, 'action': action,
                'snapshot': inspect_session(self.store, identifier)['snapshot']}

    def test_reading_legacy_store_does_not_create_archive_table_or_write(self):
        before = self.store.database.read_bytes()
        self.assertFalse(inspect_session(self.store, self.first)['archived'])
        self.assertTrue(all(not s['archived'] for s in contexts(self.store, str(self.project))['sessions']))
        self.assertEqual(before, self.store.database.read_bytes())

    def test_archive_restore_preserves_events_and_new_capture_stays_archived(self):
        original = self.store.detail(self.first)
        manage_session(self.store, self.action(self.first, 'archive'))
        self.assertEqual(original, self.store.detail(self.first))
        self.assertEqual(self.emit('a'), self.first)
        self.assertTrue(inspect_session(self.store, self.first)['archived'])
        manage_session(self.store, self.action(self.first, 'restore'))
        self.assertFalse(inspect_session(self.store, self.first)['archived'])
        self.assertEqual(inspect_session(self.store, self.first)['events'], 2)

    def test_delete_removes_only_selected_local_children_and_keeps_opt_in(self):
        other = self.store.detail(self.other)
        with self.store.connect() as db:
            db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)', ('r', self.first, 1, 1, 'title', 'body', 'now'))
            db.execute('INSERT INTO summaries VALUES (?,?,?,?,?,?)', ('s', self.first, 1, 'fingerprint', '{}', 'now'))
        manage_session(self.store, self.action(self.first, 'archive'))
        prepared = inspect_session(self.store, self.first)
        self.assertEqual((prepared['events'], prepared['revisions'], prepared['summaries']), (1, 1, 1))
        result = manage_session(self.store, self.action(self.first, 'delete'))
        self.assertTrue(result['completed']); self.assertEqual(self.store.detail(self.other), other)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT enabled FROM projects').fetchone()[0], 1)
            for table in ('events', 'revisions', 'summaries', 'capture_usage', 'reader_session_state'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE investigation=?', (self.first,)).fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertNotEqual(self.emit('a'), self.first)

    def test_new_event_invalidates_delete_confirmation_without_partial_changes(self):
        request = self.action(self.first, 'delete'); self.emit('a')
        before = self.store.database.read_bytes()
        with self.assertRaises(NotesError): manage_session(self.store, request)
        self.assertEqual(before, self.store.database.read_bytes())

    def test_invalid_actions_and_replay_never_delete_other_sessions(self):
        for value in ({}, {**self.action(self.first, 'delete'), 'snapshot': '0'*64},
                      {**self.action(self.first, 'delete'), 'action': 'delete-all'},
                      {**self.action(self.first, 'delete'), 'extra': True}):
            with self.assertRaises(NotesError): manage_session(self.store, value)
        request = self.action(self.first, 'delete'); manage_session(self.store, request)
        with self.assertRaises(NotesError): manage_session(self.store, request)
        self.assertEqual(inspect_session(self.store, self.other)['events'], 1)

    def test_failure_during_delete_rolls_back_every_child(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER prevent_delete BEFORE DELETE ON investigations BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        import sqlite3
        with self.assertRaises(sqlite3.Error): manage_session(self.store, self.action(self.first, 'delete'))
        self.assertEqual(inspect_session(self.store, self.first)['events'], 1)

    def test_archive_survives_backup_restore_and_backup_is_not_deleted(self):
        manage_session(self.store, self.action(self.first, 'archive'))
        destination = self.root / 'snapshot.sqlite3'; backup(self.store, destination)
        before = destination.read_bytes()
        manage_session(self.store, self.action(self.first, 'delete'))
        self.assertEqual(before, destination.read_bytes())
        restore(destination, self.root / 'restored')
        self.assertTrue(inspect_session(Store(self.root / 'restored'), self.first)['archived'])

    def test_types_cover_older_pages_stay_session_scoped_and_filter_totals(self):
        self.emit('a', 'ValueError'); self.emit('b', 'TypeError')
        for _ in range(105): self.emit('a')
        facets = anomaly_types(self.store, self.first)
        self.assertEqual(facets['types'], [{'name': 'RuntimeError', 'count': 106}, {'name': 'ValueError', 'count': 1}])
        filtered = listing(self.store, investigation=self.first, anomaly_type='ValueError')
        self.assertEqual(filtered['total_count'], 1)
        self.assertEqual(len(filtered['items']), 1)
        self.emit('a', 'KeyError')
        self.assertEqual(anomaly_types(self.store, self.first, facets['snapshot_seq']), facets)
        self.assertEqual(listing(self.store, investigation=self.first, anomaly_type='TypeError')['total_count'], 0)

    def test_short_export_retains_original_source_and_omits_global_rule_catalog(self):
        item = listing(self.store, investigation=self.first)['items'][0]
        exported = detail(self.store, item['id'])['markdown']
        self.assertIn('SHA-256', exported); self.assertIn('阅读提示', exported)
        self.assertNotIn('覆盖与保留说明', exported); self.assertNotIn('指定 GraphQL SSE', exported)

    def test_mcp_lifecycle_is_app_only_destructive_and_returns_private_result(self):
        tool = next(t for t in TOOLS if t['name'] == 'manage_session')
        self.assertTrue(tool['annotations']['destructiveHint']); self.assertFalse(tool['annotations']['readOnlyHint'])
        self.assertEqual(tool['_meta']['ui']['visibility'], ['app'])
        reader = Reader(self.store.directory)
        self.addCleanup(reader.names.close); self.addCleanup(reader.projects.close)
        response = reader.call('read_view', {'path': '/api/session-management?investigation=' + self.first})
        self.assertEqual(response['_meta']['blackbox']['status'], 200)
        result = reader.call('manage_session', self.action(self.first, 'archive'))
        self.assertEqual(result['content'], []); self.assertNotIn('structuredContent', result)
        self.assertTrue(result['_meta']['blackbox']['body']['completed'])
        types = reader.call('read_view', {'path': '/api/anomaly-types?investigation=' + self.first})
        self.assertEqual(types['_meta']['blackbox']['body']['types'][0]['name'], 'RuntimeError')

    def test_http_delete_requires_auth_and_same_origin_and_exact_confirmation(self):
        server, token = make_server(self.store, 'synthetic-token')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            def request(method, path, value=None, auth=True, origin=None):
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                headers = {'Content-Type': 'application/json'}
                if auth: headers['Authorization'] = 'Bearer ' + token
                if origin: headers['Origin'] = origin
                connection.request(method, path, json.dumps(value) if value is not None else None, headers)
                response = connection.getresponse(); status = response.status; body = response.read(); connection.close()
                return status, json.loads(body)
            action = self.action(self.first, 'delete')
            self.assertEqual(request('POST', '/api/session-management', action, auth=False)[0], 401)
            self.assertEqual(request('POST', '/api/session-management', action, origin='https://example.com')[0], 403)
            self.assertEqual(inspect_session(self.store, self.first)['events'], 1)
            self.assertEqual(request('GET', '/api/anomaly-types?investigation=' + self.first)[1]['types'][0]['name'], 'RuntimeError')
            self.assertEqual(request('POST', '/api/session-management', action)[0], 200)
            self.assertEqual(request('POST', '/api/session-management', action)[0], 400)
        finally:
            server.shutdown(); thread.join(); server.server_close()
