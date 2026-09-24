from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
import shutil
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/tas-notes/scripts'
sys.path.insert(0, str(SCRIPTS))
from tas_notes_core import Store, NotesError, Conflict, normalize, MAX_INPUT, MAX_FIELD
from tas_notes_web import make_server


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tas-notes-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / '项目 with spaces'
        self.project.mkdir()
        self.store = Store(self.root / 'state')
        self.store.set_project(str(self.project), True)

    def event(self, **fields):
        return dict(cwd=str(self.project), session_id='session-1', hook_event_name='PostToolUse',
                    tool_use_id='tool-1', tool_response='test passed', **fields)

    def capture(self):
        return self.store.record(self.event())['investigation']

    def test_opt_in_and_disable_are_exact_project_scope(self):
        child = self.project / 'child'
        child.mkdir()
        event = self.event()
        event['cwd'] = str(child)
        self.assertEqual(self.store.record(event)['status'], 'ignored')
        self.store.set_project(str(self.project), False)
        self.assertEqual(self.store.record(self.event())['status'], 'ignored')
        self.assertEqual(self.store.listing(), [])

    def test_state_cannot_be_inside_recorded_project(self):
        store = Store(self.project / 'state')
        with self.assertRaises(NotesError):
            store.set_project(str(self.project), True)

    def test_dedup_and_conflicting_host_identity(self):
        identifier = self.capture()
        self.assertEqual(self.store.record(self.event())['status'], 'duplicate')
        event = self.event()
        event['tool_response'] = 'now different'
        with self.assertRaises(Conflict):
            self.store.record(event)
        self.assertEqual(len(self.store.detail(identifier)['events']), 1)

    def test_no_id_repeats_are_kept(self):
        event = self.event()
        del event['tool_use_id']
        identifier = self.store.record(event)['investigation']
        self.store.record(event)
        self.assertEqual(len(self.store.detail(identifier)['events']), 2)

    def test_concurrent_duplicate_delivery(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.store.record(self.event())['status'], range(8)))
        self.assertEqual(outcomes.count('recorded'), 1)
        self.assertEqual(outcomes.count('duplicate'), 7)

    def test_human_revision_survives_later_correction_and_export_is_fixed(self):
        identifier = self.capture()
        first = self.store.detail(identifier)
        revision = self.store.save_revision(identifier, '人工标题', '人工正文', first['source_seq'], 0)
        event = self.event()
        event.update(hook_event_name='UserPromptSubmit', prompt='还是有问题', event_id='user-2')
        self.store.record(event)
        updated = Store(self.store.directory).detail(identifier)
        self.assertEqual(updated['revision']['body'], '人工正文')
        self.assertIn('还是有问题', updated['draft'])
        self.assertNotIn('还是有问题', self.store.export_revision(revision['id']))
        with self.assertRaises(Conflict):
            self.store.save_revision(identifier, '覆盖', '过时', first['source_seq'], 1)
        with self.assertRaises(Conflict):
            self.store.save_revision(identifier, '覆盖', '过时', updated['source_seq'], 0)
        self.store.save_revision(identifier, '新版', '补充用户反馈', updated['source_seq'], 1)
        self.assertIn('人工正文', self.store.export_revision(revision['id']))

    def test_redaction_truncation_and_no_transcript_read(self):
        event = self.event(tool_input={'api_key': 'secret-value', 'safe': 'Bearer abcdefghijkl',
                                       'long': 'x' * (MAX_FIELD + 10)},
                           transcript_path=str(self.root / 'never-open-me'))
        content = json.loads(normalize(event)[-1])
        self.assertNotIn('secret-value', json.dumps(content))
        self.assertNotIn('abcdefghijkl', json.dumps(content))
        self.assertTrue(content['tool_input']['truncated'])
        self.assertFalse(content['_coverage']['transcript_read'])
        self.assertNotIn('transcript_path', content)

    def test_invalid_input_shapes_do_not_write(self):
        for changes in ({'hook_event_name': []}, {'hook_event_name': 'Unknown'},
                        {'session_id': None}, {'thread_id': 'different'},
                        {'cwd': 'relative'}, {'event_id': {}}, {'tool_response': float('nan')}):
            event = self.event()
            event.update(changes)
            with self.subTest(changes=changes), self.assertRaises((NotesError, ValueError)):
                self.store.record(event)
        self.assertEqual(self.store.listing(), [])

    def test_oversize_and_deep_input(self):
        event = self.event()
        event['tool_response'] = 'x' * MAX_INPUT
        with self.assertRaises(NotesError):
            normalize(event)
        nested = 'data'
        for _ in range(20):
            nested = [nested]
        event['tool_response'] = nested
        with self.assertRaises(NotesError):
            normalize(event)

    def test_unknown_database_version_is_preserved(self):
        with self.store.connect() as db:
            db.execute('PRAGMA user_version=42')
        with self.assertRaises(NotesError):
            Store(self.store.directory)
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 42)

    def test_capacity_failure_rolls_back_new_session(self):
        with patch('tas_notes_core.MAX_SESSION_BYTES', 1):
            with self.assertRaises(NotesError):
                self.store.record(self.event())
        self.assertEqual(self.store.listing(), [])

    def cli(self, *args, data=None):
        return subprocess.run([sys.executable, str(SCRIPTS / 'tas_notes.py'), '--state-dir',
                               str(self.store.directory), *args], input=data, capture_output=True, timeout=10)

    def test_separate_process_persists_unicode_and_exports_without_overwrite(self):
        event = self.event(prompt='中文用户材料')
        result = self.cli('hook', data=json.dumps(event, ensure_ascii=False).encode('utf-8'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'')
        records = json.loads(self.cli('status').stdout)['records']
        identifier = records[0]['id']
        detail = self.store.detail(identifier)
        self.assertIn('中文用户材料', detail['draft'])
        revision = self.store.save_revision(identifier, '中文', '正文', detail['source_seq'], 0)
        destination = self.project / 'reviewed.md'
        self.assertEqual(self.cli('export', revision['id'], str(destination)).returncode, 0)
        self.assertEqual(self.cli('export', revision['id'], str(destination)).returncode, 1)
        self.assertIn('正文', destination.read_text(encoding='utf-8'))

    def test_bad_hook_fail_open_with_content_free_diagnostic(self):
        result = self.cli('hook', data=b'{secret-value-invalid')
        self.assertEqual(result.returncode, 0)
        marker = (self.store.directory / 'last-error.json').read_text(encoding='utf-8')
        self.assertNotIn('secret-value', marker + result.stderr.decode())
        self.assertEqual(json.loads(marker)['code'], 'JSONDecodeError')

    @unittest.skipUnless(os.name == 'nt', 'Windows launcher')
    def test_windows_launcher_copies_raw_unicode_stdin(self):
        self.assertEqual(self.cli('configure-runtime').returncode, 0)
        environment = dict(os.environ, TAS_NOTES_STATE_DIR=str(self.store.directory))
        copied = self.project / '插件 scripts'
        shutil.copytree(SCRIPTS, copied, ignore=shutil.ignore_patterns('__pycache__'))
        result = subprocess.run(['cmd.exe', '/d', '/c', str(copied / 'launch.cmd')],
                                input=json.dumps(self.event(prompt='启动器中文'), ensure_ascii=False).encode('utf-8'),
                                capture_output=True, env=environment, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'')
        records = self.store.listing()
        self.assertEqual(len(records), 1, result.stderr)
        self.assertIn('启动器中文', self.store.detail(records[0]['id'])['draft'])


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tas-notes-web-')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        project = root / 'project'
        project.mkdir()
        self.store = Store(root / 'state')
        self.store.set_project(str(project), True)
        self.identifier = self.store.record({'cwd': str(project), 'session_id': 'test',
                                             'hook_event_name': 'Stop', 'last_assistant_message': '<script>bad()</script>'})['investigation']
        self.server, self.token = make_server(self.store)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, method='GET', payload=None, auth=True, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, body=body, headers=headers)
        result = connection.getresponse()
        return result.status, result.read(), dict(result.getheaders())

    def test_auth_host_and_origin_guards(self):
        self.assertEqual(self.request('/api/records', auth=False)[0], 401)
        self.assertEqual(self.request('/api/records', Origin='https://example.com')[0], 403)
        self.assertEqual(self.request('/api/records', Host='evil.example')[0], 403)
        self.assertEqual(self.request('/api/records')[0], 200)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_sources_are_json_and_static_paths_are_allowlisted(self):
        code, raw, headers = self.request('/api/record/' + self.identifier)
        self.assertEqual(code, 200)
        self.assertIn('<script>bad()</script>', json.loads(raw)['draft'])
        self.assertIn('application/json', headers['Content-Type'])
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../scripts/tas_notes_core.py')[0], 404)

    def test_save_export_and_stale_conflict(self):
        detail = self.store.detail(self.identifier)
        body = {'id': self.identifier, 'title': '标题', 'body': '已审阅正文',
                'source_seq': detail['source_seq'], 'expected_revision': 0}
        code, raw, _ = self.request('/api/revisions', 'POST', body)
        self.assertEqual(code, 200)
        revision = json.loads(raw)
        self.assertEqual(self.request('/api/revisions', 'POST', body)[0], 409)
        code, raw, _ = self.request('/api/export/' + revision['id'])
        self.assertEqual(code, 200)
        self.assertIn('已审阅正文', raw.decode('utf-8'))

    def test_malformed_revision_types_are_rejected(self):
        body = {'id': [], 'title': 'title', 'body': 'body', 'source_seq': 1, 'expected_revision': 0}
        self.assertEqual(self.request('/api/revisions', 'POST', body)[0], 400)
        self.assertEqual(self.request('/api/revisions', 'POST', {})[0], 400)
        self.assertEqual(self.request('/api/revisions', 'POST', body, auth=False)[0], 401)

    def test_store_failure_is_not_exposed(self):
        with patch.object(self.store, 'listing', side_effect=sqlite3.OperationalError('secret-value')):
            code, raw, _ = self.request('/api/records')
        self.assertEqual(code, 503)
        self.assertNotIn(b'secret-value', raw)


if __name__ == '__main__':
    unittest.main()
