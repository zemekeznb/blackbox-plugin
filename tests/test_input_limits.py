"""Synthetic rejection and association tests; not real Codex Hook evidence."""
from contextlib import closing, contextmanager
import hashlib
import io
import json
from pathlib import Path
import sqlite3
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
from tas_notes_diagnostics import last_error, unscoped_error

TRACE = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nTypeError: synthetic\n'


class InputLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='blackbox-input-limit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.store = core.Store(self.root / 'state')
        self.store.set_project(str(self.project), True)
        self.serial = 0

    def payload(self, **changes):
        self.serial += 1
        return dict(cwd=str(self.project), session_id='s', hook_event_name='PostToolUse',
                    event_id=str(self.serial), tool_name='Bash',
                    tool_input={'command': 'python x.py'}, tool_response=TRACE) | changes

    def record(self, **changes):
        result = self.store.record(self.payload(**changes))
        return self.store.detail(result['investigation'])['events'][-1]

    def reject(self):
        with self.assertRaises(core.CaptureLimit):
            self.store.record_stream(io.BytesIO(b'x' * (core.MAX_INPUT + 50)))

    def test_wire_limit_is_exact_and_reads_only_one_byte_past_budget(self):
        raw = json.dumps(self.payload()).encode()
        self.store.record_stream(io.BytesIO(raw + b' ' * (core.MAX_INPUT - len(raw))))
        before = self.store.database.read_bytes()
        # Even a complete identity prefix is not trusted without the full JSON.
        secret = b'synthetic-not-for-diagnostics'
        stream = io.BytesIO(raw + b' ' * core.MAX_INPUT + secret)
        with self.assertRaises(core.CaptureLimit):
            self.store.record_stream(stream)
        self.assertEqual(stream.tell(), core.MAX_INPUT + 1)
        self.assertEqual(self.store.database.read_bytes(), before)
        self.assertIsNone(last_error(self.store.directory, str(self.project)))
        marker = unscoped_error(self.store.directory)
        self.assertEqual(marker['code'], 'InputTooLarge')
        self.assertEqual(marker['scope'], 'unknown_project')
        for path in self.store.directory.glob('*.json'):
            self.assertNotIn(secret, path.read_bytes())
            self.assertNotIn(str(self.project), path.read_text())

    def test_cli_rejects_fail_open_without_echoing_or_saving_oversized_content(self):
        secret = 'synthetic-secret-' * core.MAX_INPUT
        raw = json.dumps(self.payload(tool_response=secret)).encode()
        process = subprocess.run([sys.executable, str(SCRIPTS / 'tas_notes.py'), '--state-dir',
                                  str(self.store.directory), 'hook'], input=raw, capture_output=True,
                                 timeout=15, check=False)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout, b'')
        self.assertIn(b'InputTooLarge', process.stderr)
        self.assertNotIn(b'synthetic-secret', process.stderr)
        self.assertEqual(self.store.status()['record_count'], 0)
        self.assertEqual(unscoped_error(self.store.directory)['code'], 'InputTooLarge')

    def test_unknown_rejection_stops_each_session_once_and_clean_capture_recovers(self):
        first = {session: self.record(session_id=session)['seq'] for session in ('a', 'b')}
        self.reject()
        for session in ('a', 'b'):
            boundary = self.record(session_id=session)
            coverage = json.loads(boundary['content'])['_coverage']
            self.assertEqual(coverage['capture_gap_scope'], 'unknown_project')
            detail = anomalies.detail(self.store, first[session])
            self.assertEqual(len(detail['events']), 1)
            self.assertTrue(detail['capture_gap'])
            self.assertIn('不证明当前会话丢失', detail['markdown'])
            clean = self.record(session_id=session)
            after = self.record(session_id=session, tool_response='ordinary output')
            detail = anomalies.detail(self.store, clean['seq'])
            self.assertEqual([row['seq'] for row in detail['events']], [clean['seq'], after['seq']])
            self.assertFalse(detail['capture_gap'])

    def test_duplicate_after_rejection_does_not_consume_boundary_or_rewrite_source(self):
        payload = self.payload()
        identifier = self.store.record(payload)['investigation']
        original = self.store.detail(identifier)['events'][0]
        self.reject()
        self.assertEqual(self.store.record(payload)['status'], 'duplicate')
        self.assertEqual(self.store.detail(identifier)['events'][0], original)
        next_event = self.record()
        self.assertTrue(json.loads(next_event['content'])['_coverage']['capture_gap_before'])
        self.assertEqual(len(anomalies.detail(self.store, original['seq'])['events']), 1)

    def test_repeated_unknown_rejections_have_distinct_epochs_even_with_same_time(self):
        with patch.object(core, 'now', return_value='2026-10-08T00:00:00+00:00'):
            self.reject()
            first = unscoped_error(self.store.directory)['epoch']
            self.record()
            clean = self.record()
            self.reject()
            self.assertNotEqual(unscoped_error(self.store.directory)['epoch'], first)
            self.record()
        self.assertTrue(anomalies.detail(self.store, clean['seq'])['capture_gap'])

    def test_scoped_failure_does_not_create_or_overwrite_unknown_gap(self):
        clean = self.record(session_id='other')
        with self.assertRaises(core.CaptureLimit):
            self.store.record(self.payload(tool_response='x' * core.MAX_INPUT))
        self.assertIsNone(unscoped_error(self.store.directory))
        self.assertEqual(last_error(self.store.directory)['scope'], 'session')
        next_event = self.record(session_id='other', tool_response='ordinary output')
        self.assertEqual(len(anomalies.detail(self.store, clean['seq'])['events']), 2)
        self.reject()
        unknown = unscoped_error(self.store.directory)
        with self.assertRaises(core.NotesError):
            self.store.record(self.payload(hook_event_name='unsupported'))
        self.assertEqual(unscoped_error(self.store.directory), unknown)

    def test_rejected_ambiguous_or_missing_session_never_guesses_a_scoped_boundary(self):
        for changes in ({'session_id': 'other', 'thread_id': 's'}, {'session_id': None},
                        {'session_id': 'x' * 513}):
            with self.subTest(changes=changes):
                self.record()
                first = self.record()
                with self.assertRaises(core.NotesError):
                    self.store.record(self.payload(hook_event_name='UserPromptSubmit', **changes))
                self.record(tool_response='ordinary output')
                detail = anomalies.detail(self.store, first['seq'])
                self.assertEqual(len(detail['events']), 1)
                self.assertEqual(detail['capture_gap_scope'], 'unknown_project')

    def test_legacy_unscoped_marker_is_preserved_before_scoped_summary_changes(self):
        original = {'time': '2026-10-08T00:00:00+00:00', 'code': 'InputTooLarge'}
        (self.store.directory / 'last-error.json').write_text(json.dumps(original))
        with self.assertRaises(core.NotesError):
            self.store.record(self.payload(hook_event_name='unsupported'))
        self.assertEqual(last_error(self.store.directory)['code'], 'NotesError')
        self.assertEqual(unscoped_error(self.store.directory)['code'], 'InputTooLarge')
        event = self.record(session_id='another')
        self.assertEqual(json.loads(event['content'])['_coverage']['capture_gap_scope'], 'unknown_project')

    def test_unreadable_marker_remains_conservative_and_content_free(self):
        (self.store.directory / 'capture-unknown.json').write_bytes(b'synthetic-secret-' * 500)
        for _ in range(2):
            event = self.record()
            self.assertTrue(json.loads(event['content'])['_coverage']['capture_gap_before'])
        self.assertNotIn('synthetic-secret', json.dumps(self.store.status()))

    def test_partially_persisted_unknown_marker_survives_later_scoped_summary(self):
        self.reject()
        self.record()
        first = self.record()
        write = core.write_diagnostic_marker

        def fail_dedicated(directory, name, value):
            if name != 'capture-unknown.json':
                return write(directory, name, value)

        with patch.object(core, 'write_diagnostic_marker', fail_dedicated):
            self.reject()
        self.assertEqual(unscoped_error(self.store.directory)['state'], 'unreadable')
        with self.assertRaises(core.NotesError):
            self.store.record(self.payload(session_id='another', hook_event_name='unsupported'))
        self.assertEqual(last_error(self.store.directory)['code'], 'NotesError')
        self.assertEqual(unscoped_error(self.store.directory)['state'], 'unreadable')
        self.record(tool_response='ordinary output')
        detail = anomalies.detail(self.store, first['seq'])
        self.assertEqual(len(detail['events']), 1)
        self.assertEqual(detail['capture_gap_reason'], 'diagnostic_unreadable')
        self.assertIn('诊断不可读或不一致', detail['markdown'])

    def test_unknown_rejection_publishes_while_writer_lock_held(self):
        publish = core.diagnostic
        checked = []

        def check(directory, code, project=None, session=None):
            with closing(sqlite3.connect(self.store.database, timeout=0)) as observer:
                with self.assertRaises(sqlite3.OperationalError):
                    observer.execute('BEGIN IMMEDIATE')
            checked.append(code)
            return publish(directory, code, project, session)

        with patch.object(core, 'diagnostic', check):
            self.reject()
        self.assertEqual(checked, ['InputTooLarge'])

    def test_waiting_success_rechecks_unknown_gap_after_rejection(self):
        first = self.record()
        waiting, resume = threading.Event(), threading.Event()
        connect = self.store.connect
        errors = []

        class Connection:
            def __init__(self, db): self.db = db
            def __getattr__(self, name): return getattr(self.db, name)
            def execute(self, sql, *args):
                if sql == 'BEGIN IMMEDIATE' and threading.current_thread().name == 'waiting-capture':
                    waiting.set()
                    if not resume.wait(5): raise AssertionError('test writer was not released')
                return self.db.execute(sql, *args)

        @contextmanager
        def interleaved():
            with connect() as db: yield Connection(db)

        def capture():
            try: self.record(tool_response='ordinary output')
            except BaseException as error: errors.append(error)

        with patch.object(self.store, 'connect', interleaved):
            worker = threading.Thread(target=capture, name='waiting-capture', daemon=True)
            worker.start()
            try:
                self.assertTrue(waiting.wait(5))
                self.reject()
            finally:
                resume.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        detail = anomalies.detail(self.store, first['seq'])
        self.assertEqual(len(detail['events']), 1)
        self.assertEqual(detail['capture_gap_scope'], 'unknown_project')

    def test_pre_upgrade_sources_are_readonly_and_never_link_across_unverifiable_gaps(self):
        first, second = self.record(), self.record(tool_response='ordinary output')
        # Simulate already-stored v2 evidence with no global rejection contract.
        with self.store.connect() as db:
            for event in (first, second):
                content = json.loads(event['content'])
                content['_coverage'].pop('capture_boundary_version')
                body = core.canonical(content)
                db.execute('UPDATE events SET content=?,digest=? WHERE seq=?',
                           (body, hashlib.sha256(body.encode()).hexdigest(), event['seq']))
        before = self.store.database.read_bytes()
        snapshot = anomalies.detail(self.store, first['seq'])
        self.assertEqual(len(snapshot['events']), 1)
        self.assertTrue(snapshot['legacy_association_boundary'])
        self.assertIn('旧来源未保存完整采集边界', snapshot['markdown'])
        self.assertEqual(self.store.database.read_bytes(), before)
        # A later diagnostic must not mutate an existing rule-3 export snapshot.
        self.reject()
        self.assertEqual(anomalies.detail(self.store, first['seq'], snapshot['source_seq'])['markdown'], snapshot['markdown'])


if __name__ == '__main__':
    unittest.main()
