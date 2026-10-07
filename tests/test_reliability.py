from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store, NotesError, Conflict, CaptureLimit, normalize, canonical, MAX_FIELD
from tas_notes_backup import backup, restore, verify
from tas_notes_diagnostics import last_error
import tas_notes_anomalies as anomalies
import tas_notes_core as core

TRACE = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nTypeError: bad type\n'


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='blackbox-reliability-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.store = Store(self.root / 'state')
        self.store.set_project(str(self.project), True)

    def event(self, **changes):
        return dict(cwd=str(self.project), session_id='s', hook_event_name='PostToolUse',
                    event_id='one', tool_name='Bash', tool_input={'command':'python x.py'},
                    tool_response=TRACE) | changes

    def test_adapter_retains_identity_and_status_without_inventing_contract(self):
        content = json.loads(normalize(self.event(turn_id='turn', call_id='call', exit_code=7, status='failed', private_unknown='not saved'))[-1])
        self.assertEqual(content['turn_id'], 'turn')
        self.assertEqual(content['exit_code'], 7)
        self.assertEqual(content['_coverage']['unknown_field_count'], 1)
        self.assertEqual(content['_coverage']['status_contract'], 'captured_without_interpretation')
        self.assertNotIn('not saved', canonical(content))

    def test_long_output_keeps_tail_and_detects_complete_real_trace_segment(self):
        content = json.loads(normalize(self.event(tool_response='normal output\n'*5000+TRACE))[-1])
        self.assertTrue(content['tool_response']['truncated'])
        self.assertIn('TypeError', content['tool_response']['tail'])
        self.assertIsNotNone(anomalies.detect(content))

    def test_legacy_redelivery_retains_original_even_for_truncated_content(self):
        for index, output in enumerate(('short result', 'normal output\n'*5000+TRACE)):
            payload = self.event(event_id=str(index),tool_response=output,turn_id='t')
            project,session,kind,key,body = normalize(payload, adapter_version=1)
            identifier = 'legacy'
            with self.store.connect() as db:
                db.execute('INSERT OR IGNORE INTO investigations VALUES (?,?,?,?)',(identifier,project,session,'2026-10-07'))
                db.execute('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES (?,?,?,?,?,?)',
                           (identifier,key,kind,body,hashlib.sha256(body.encode()).hexdigest(),'2026-10-07'))
            self.assertEqual(self.store.record(payload)['status'],'duplicate')
            with self.assertRaises(Conflict): self.store.record(dict(payload,tool_response='changed'))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],2)

    def test_legacy_added_body_is_conflict_not_duplicate(self):
        payload=self.event(); del payload['tool_response']
        project,session,kind,key,body=normalize(payload,adapter_version=1)
        with self.store.connect() as db:
            db.execute('INSERT INTO investigations VALUES (?,?,?,?)',('legacy',project,session,'2026-10-07'))
            db.execute('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES (?,?,?,?,?,?)',
                       ('legacy',key,kind,body,hashlib.sha256(body.encode()).hexdigest(),'2026-10-07'))
        with self.assertRaises(Conflict): self.store.record(self.event())

    def test_session_limit_reserves_errors_and_marks_gap_in_next_source(self):
        self.store.record(self.event(tool_response='normal'))
        with patch('tas_notes_core.MAX_SESSION_BYTES', 1):
            with self.assertRaises(CaptureLimit):
                self.store.record(self.event(event_id='two', hook_event_name='UserPromptSubmit', prompt='new task'))
            result=self.store.record(self.event(event_id='three'))
        event=self.store.detail(result['investigation'])['events'][-1]
        self.assertTrue(json.loads(event['content'])['_coverage']['capture_gap_before'])
        marker=last_error(self.store.directory,str(self.project),'s')
        self.assertEqual(marker['code'],'SessionCapacityReached')
        self.assertEqual(self.store.record(self.event(event_id='three'))['status'],'duplicate')

    def test_waiting_writer_rechecks_gap_after_concurrent_boundary_rejection(self):
        self.store.record(self.event())
        waiting, resume = threading.Event(), threading.Event()
        connect = self.store.connect
        outcomes, errors = [], []

        class Connection:
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            def execute(self, sql, *args):
                if sql == 'BEGIN IMMEDIATE' and threading.current_thread().name == 'waiting-retry':
                    waiting.set()
                    if not resume.wait(5):
                        raise AssertionError('test writer was not released')
                return self.db.execute(sql, *args)

        @contextmanager
        def interleaved_connect():
            with connect() as db:
                yield Connection(db)

        def retry():
            try:
                outcomes.append(self.store.record(self.event(event_id='retry')))
            except BaseException as error:
                errors.append(error)

        with patch.object(self.store, 'connect', interleaved_connect), patch.object(core, 'MAX_SESSION_BYTES', 1):
            worker = threading.Thread(target=retry, name='waiting-retry', daemon=True)
            worker.start()
            try:
                self.assertTrue(waiting.wait(5))
                with self.assertRaises(CaptureLimit):
                    self.store.record(self.event(event_id='stop', hook_event_name='Stop'))
            finally:
                resume.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outcomes[0]['status'], 'recorded')
        detail = anomalies.detail(self.store, 1)
        self.assertEqual([event['seq'] for event in detail['events']], [1])
        self.assertTrue(detail['capture_gap'])
        self.assertEqual(len(anomalies.listing(self.store)['items']), 2)

    def test_capacity_marker_is_published_once_before_writer_lock_releases(self):
        self.store.record(self.event())
        publish = core.diagnostic
        session_calls = []

        def checked_publish(directory, code, project=None, session=None):
            if session is not None:
                session_calls.append(code)
                with closing(sqlite3.connect(self.store.database, timeout=0)) as observer:
                    with self.assertRaises(sqlite3.OperationalError):
                        observer.execute('BEGIN IMMEDIATE')
            return publish(directory, code, project, session)

        with patch.object(core, 'MAX_SESSION_BYTES', 1), patch.object(core, 'diagnostic', checked_publish):
            with self.assertRaises(CaptureLimit):
                self.store.record(self.event(event_id='stop', hook_event_name='Stop'))
        self.assertEqual(session_calls, ['SessionCapacityReached'])
        self.assertEqual(last_error(self.store.directory, str(self.project), 's')['code'], 'SessionCapacityReached')

    def test_normalization_failure_still_publishes_one_external_diagnostic(self):
        with patch.object(core, 'diagnostic', wraps=core.diagnostic) as publish:
            with self.assertRaises(NotesError):
                self.store.record(self.event(hook_event_name='unsupported'))
        scoped = [call for call in publish.call_args_list if len(call.args) == 4 and call.args[3] is not None]
        self.assertEqual(len(scoped), 1)
        self.assertEqual(last_error(self.store.directory, str(self.project), 's')['code'], 'NotesError')
        self.assertEqual(self.store.status()['record_count'], 0)

    def test_full_database_is_readable_and_disabled_project_is_ignored(self):
        self.store.record(self.event())
        with patch('tas_notes_core.MAX_DATABASE_BYTES',1):
            with self.assertRaises(CaptureLimit): self.store.record(self.event(event_id='two'))
            self.assertTrue(self.store.status()['storage']['capacity_reached'])
            self.assertEqual(len(anomalies.listing(self.store)['items']),1)
            self.assertEqual(self.store.record(self.event())['status'],'duplicate')
            self.store.set_project(str(self.project),False)
            self.assertEqual(self.store.record(self.event(event_id='three'))['status'],'ignored')

    def test_usage_counter_tracks_legacy_sql_inserts_and_reopen(self):
        result=self.store.record(self.event())
        with self.store.connect() as db:
            db.execute("INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES (?, 'old', 'Stop', '{}', 'digest', 'date')",(result['investigation'],))
        with Store(self.store.directory).connect() as db:
            usage=db.execute('SELECT bytes,count FROM capture_usage').fetchone()
            actual=db.execute('SELECT SUM(length(CAST(content AS BLOB))),COUNT(*) FROM events').fetchone()
            self.assertEqual(tuple(usage),tuple(actual))

    def test_backup_restore_preserves_sources_all_versions_and_disables_recording(self):
        result=self.store.record(self.event())
        with self.store.connect() as db:
            for n in (1,2):
                db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)',(str(n),result['investigation'],n,1,'title','body'+str(n),'time'))
        original=self.store.detail(result['investigation'])
        destination=self.root/'archive.sqlite3'
        summary=backup(self.store,destination)
        self.assertTrue(summary['verified'])
        self.assertEqual(summary['counts']['revisions'],2)
        with self.assertRaises(NotesError): backup(self.store,destination)
        target=self.root/'restored'
        restored=restore(destination,target)
        self.assertTrue(restored['all_projects_disabled'])
        reopened=Store(target)
        self.assertEqual(reopened.detail(result['investigation']),original)
        self.assertFalse(reopened.status()['projects'][0]['enabled'])
        self.assertFalse((target/'runtime.cmd').exists())
        with self.assertRaises(NotesError): restore(destination,target)

    def test_backup_rejects_recorded_project_and_corrupt_restore_leaves_no_target(self):
        with self.assertRaises(NotesError): backup(self.store,self.project/'backup.sqlite3')
        corrupt=self.root/'corrupt.sqlite3';corrupt.write_bytes(b'not a database')
        with self.assertRaises((NotesError,sqlite3.Error)): restore(corrupt,self.root/'unused')
        self.assertFalse((self.root/'unused').exists())

    def test_legacy_readonly_open_does_not_migrate(self):
        self.store.record(self.event())
        with self.store.connect() as db:
            db.execute('DROP TRIGGER capture_usage_insert');db.execute('DROP TRIGGER capture_usage_delete')
            db.execute('DROP TABLE capture_usage')
        before=self.store.database.read_bytes()
        opened=Store(self.store.directory,read_only=True)
        self.assertEqual(len(anomalies.listing(opened)['items']),1)
        self.assertEqual(opened.status()['record_count'],1)
        self.assertEqual(self.store.database.read_bytes(),before)


if __name__ == '__main__': unittest.main()
