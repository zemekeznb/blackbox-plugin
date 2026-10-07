from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store, NotesError, MAX_FIELD
import tas_notes_anomalies as a
from tas_notes_web import make_server

TRACE = 'Traceback (most recent call last):\n  File "example.py", line 1, in <module>\nFileNotFoundError: absent\n'
PS = "ResourceUnavailable: \nLine |\n   2 | command\n     | Program 'python.exe' failed to run: access denied\n"


class AnomalyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.store = Store(self.root / 'state')
        self.store.set_project(str(self.project), True)
        self.number = 0

    def emit(self, output=TRACE, command='python example.py', session='s', **changes):
        self.number += 1
        payload = dict(cwd=str(self.project), session_id=session, hook_event_name='PostToolUse',
                       tool_name='Bash', tool_input={'command': command}, tool_response=output,
                       tool_use_id=str(self.number))
        payload.update(changes)
        result = self.store.record(payload)
        record = self.store.detail(result['investigation'])
        return record['source_seq'], result['investigation'], payload

    def test_detects_return_formats_not_prompt_or_generic_errors(self):
        for body in (TRACE, PS):
            self.assertIsNotNone(a.detect({'tool_name': 'Bash', 'tool_response': body}))
        for body in ('', 'null', 'error', 'tests failed', 'exit code 1', 'text\n'+TRACE):
            self.assertIsNone(a.detect({'tool_name': 'Bash', 'tool_response': body}))
        self.assertIsNone(a.detect({'tool_name': 'Read', 'tool_response': TRACE}))
        self.emit('no results', tool_input={'command': TRACE})
        self.emit(TRACE, hook_event_name='UserPromptSubmit')
        self.emit('')
        self.assertEqual(a.listing(self.store)['items'], [])

    def test_failure_retry_and_frozen_snapshot(self):
        first, _, _ = self.emit()
        snapshot = a.detail(self.store, first)
        later, _, _ = self.emit('done')
        self.assertEqual([e['seq'] for e in a.detail(self.store, first)['events']], [first, later])
        self.assertEqual(a.detail(self.store, first, snapshot['source_seq'])['markdown'], snapshot['markdown'])
        self.assertEqual(len(a.listing(self.store)['items']), 1)
        self.assertNotIn('已修复', snapshot['markdown'])

    def test_real_retries_distinct_delivery_deduplicated(self):
        first, _, payload = self.emit()
        self.assertEqual(self.store.record(payload)['status'], 'duplicate')
        second, _, _ = self.emit()
        third, _, _ = self.emit('')
        self.assertEqual(len(a.listing(self.store)['items']), 2)
        self.assertEqual([e['seq'] for e in a.detail(self.store, first)['events']], [first, second])
        self.assertEqual([e['seq'] for e in a.detail(self.store, second)['events']], [second, third])
        self.assertIn('空返回不表示成功', a.detail(self.store, second)['markdown'])

    def test_interleaving_changed_inputs_sessions_and_interrupt(self):
        first, _, _ = self.emit(command='A')
        self.emit(command='B')
        self.emit('done', command='B')
        self.emit('done', command='A', session='other')
        self.emit('done', command='A modified')
        self.emit('', hook_event_name='Interrupt')
        self.emit('done', command='A')
        self.assertEqual(len(a.detail(self.store, first)['events']), 1)

    def test_truncation_and_redaction_do_not_create_associations(self):
        self.emit(TRACE + 'x'*MAX_FIELD)
        self.assertEqual(a.listing(self.store)['items'], [])
        first, _, _ = self.emit(tool_input={'command':'same','password':'one'})
        self.emit('done', tool_input={'command':'same','password':'two'})
        self.assertEqual(len(a.detail(self.store, first)['events']), 1)

    def test_consistent_backup_and_legacy_writes(self):
        first, record, _ = self.emit()
        rev = self.store.save_revision(record, 'kept', 'original human text', first, 0)
        before = self.store.detail(record)
        a.listing(self.store)
        backups = list(self.store.directory.glob('before-anomalies-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='anomalies'").fetchone())
        reopened = Store(self.store.directory)
        self.assertEqual(reopened.detail(record), before)
        self.assertIn('original human text', reopened.export_revision(rev['id']))
        self.emit('done')
        a.listing(reopened)
        self.assertEqual(len(list(self.store.directory.glob('before-anomalies-*.sqlite3'))), 1)

    def test_concurrent_migration_index_and_rebuild_preserve_sources(self):
        first, record, _ = self.emit()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: a.listing(self.store), range(4)))
        self.assertTrue(all(len(r['items']) == 1 for r in results))
        self.assertEqual(len(list(self.store.directory.glob('before-anomalies-*.sqlite3'))), 1)
        with self.store.connect() as db:
            db.execute("UPDATE anomaly_index_state SET rule_version='old'")
        self.assertEqual(len(a.listing(self.store)['items']), 1)
        self.assertEqual(self.store.detail(record)['source_seq'], first)

    def test_backup_failure_does_not_create_schema(self):
        self.emit()
        connect = sqlite3.connect
        def fail_backup(path, *args, **kwargs):
            if 'before-anomalies-' in str(path):
                raise OSError('disk full')
            return connect(path, *args, **kwargs)
        with patch.object(a.sqlite3, 'connect', side_effect=fail_backup):
            with self.assertRaises(OSError):
                a.ensure_schema(self.store)
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='anomalies'").fetchone())

    def test_filters_and_invalid_cursors(self):
        first, _, _ = self.emit()
        self.assertEqual(len(a.listing(self.store, tool='bash', project='project')['items']), 1)
        self.assertEqual(a.listing(self.store, tool='other')['items'], [])
        self.assertEqual(a.listing(self.store, start='2100-01-01T00:00:00+00:00')['items'], [])
        for args in ({'start':'bad'}, {'start':'2026-01-01'}, {'before':'-1'}, {'before':'9'*30},
                     {'start':'2026-01-02T00:00:00Z', 'end':'2026-01-01T00:00:00Z'}):
            with self.assertRaises(NotesError):
                a.listing(self.store, **args)
        with self.assertRaises(NotesError):
            a.detail(self.store, first, first+100)

    def test_incremental_index_and_pagination(self):
        # Use direct fixture insertion to exercise batch limits without renderer overhead.
        self.emit()
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM events LIMIT 1').fetchone()
            db.execute('BEGIN')
            for i in range(501):
                db.execute('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES(?,?,?,?,?,?)',
                           (row['investigation'], 'extra-'+str(i), row['kind'], row['content'], row['digest'],row['captured']))
            db.commit()
        first=a.listing(self.store)
        self.assertTrue(first['index_pending'])
        page=a.listing(self.store)
        self.assertFalse(page['index_pending'])
        second=a.listing(self.store,before=page['next_before'])
        self.assertFalse({r['id'] for r in page['items']} & {r['id'] for r in second['items']})

    def test_capacity_does_not_drop_source_or_advance_index(self):
        first, _, _=self.emit()
        a.listing(self.store)
        self.emit()
        with patch.object(a, 'MAX_DATABASE_BYTES', 1):
            with self.assertRaises(NotesError):
                a.listing(self.store)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT scanned_seq FROM anomaly_index_state').fetchone()[0], first)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)

    def test_markdown_output_cannot_escape_code_fences(self):
        payload='```\n<img src=x>\n````\n'
        fenced=a.fenced(payload)
        self.assertTrue(fenced.startswith('`````\n'))
        self.assertTrue(fenced.endswith('\n`````'))

    def test_api_auth_static_history_export_without_human_revision(self):
        first, _, _=self.emit()
        server, token=make_server(self.store, 'test-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        def request(path, auth=True):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            try:
                conn.request('GET',path,headers={'Authorization':'Bearer '+token} if auth else {})
                response=conn.getresponse(); return response.status,response.read()
            finally:
                conn.close()
        try:
            for path in ('/api/anomalies','/api/anomaly/'+str(first),'/api/anomaly-export/'+str(first)):
                self.assertEqual(request(path,False)[0],401)
                self.assertEqual(request(path)[0],200)
            self.assertIn('无需人工确认'.encode(),request('/')[1])
            self.assertIn('历史会话'.encode(),request('/history')[1])
            snapshot=json.loads(request('/api/anomaly/'+str(first))[1])
            self.emit('later output')
            exported=request('/api/anomaly-export/'+str(first)+'?through='+str(snapshot['source_seq']))[1].decode()
            self.assertEqual(exported,snapshot['markdown'])
            self.assertEqual(request('/api/anomaly-export/'+str(first)+'?rule=old')[0],400)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
