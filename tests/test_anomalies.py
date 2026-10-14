from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'))
from blackbox_core import Store, NotesError, MAX_FIELD
import blackbox_anomalies as a
import blackbox_core as core
from blackbox_web import make_server

TRACE = 'Traceback (most recent call last):\n  File "example.py", line 1, in <module>\nFileNotFoundError: absent\n'
PS = "ResourceUnavailable: \nLine |\n   2 | command\n     | Program 'python.exe' failed to run: access denied\n"


class AnomalyTests(unittest.TestCase):
    def test_sparse_type_matches_fill_page_across_raw_scan_windows(self):
        self.emit(); self.add_copies(1500, {'tool_name':'Bash','tool_response':'ordinary success'})
        expected=[2,50,150,250,350,450,550,650,750,850,1100,1499]
        with self.store.connect() as db:
            content=json.loads(db.execute('SELECT content FROM events WHERE seq=1').fetchone()[0])
            content['tool_response']=TRACE.replace('FileNotFoundError','PermissionError')
            for seq in expected:db.execute('UPDATE events SET content=? WHERE seq=?',(json.dumps(content),seq))
        page=a.listing(self.store,anomaly_type='PermissionError')
        self.assertEqual(page['total_count'],12)
        self.assertEqual([int(item['id']) for item in page['items']],list(reversed(expected)))
        self.assertIsNone(page['next_before'])

    def test_sparse_matching_pages_fill_and_do_not_offer_empty_tail(self):
        self.emit();self.add_copies(1500)
        with self.store.connect() as db:
            original=db.execute('SELECT content FROM events LIMIT 1').fetchone()[0]
            db.execute('UPDATE events SET content=?',(json.dumps({'tool_name':'Bash','tool_response':'ordinary success'}),))
            expected=list(range(100,1400,10))
            for seq in expected:db.execute('UPDATE events SET content=? WHERE seq=?',(original,seq))
        first=a.listing(self.store);self.assertEqual(len(first['items']),100);self.assertEqual(first['total_count'],130)
        last=a.listing(self.store,before=first['next_before'],through=first['snapshot_seq'])
        self.assertEqual(len(last['items']),30);self.assertIsNone(last['next_before'])
        self.assertEqual([int(i['id']) for i in first['items']+last['items']],list(reversed(expected)))

    def test_total_scan_uses_full_batches_without_changing_display_pages(self):
        self.emit(); self.add_copies(1200)
        queries = []
        connect = self.store.connect
        @contextmanager
        def traced():
            with connect() as db:
                db.set_trace_callback(lambda sql: queries.append(sql) if 'SELECT e.seq,e.captured' in sql else None)
                yield db
        with patch.object(self.store, 'connect', traced):
            page = a.listing(self.store)
        self.assertEqual(page['total_count'], 1201)
        self.assertEqual(len(page['items']), 100)
        self.assertEqual(page['next_before'], '1102')
        self.assertLessEqual(len(queries), 4)

    def test_batched_total_matches_reference_pages_for_filters_and_old_cursor(self):
        self.emit(); self.add_copies(610)
        self.emit(output='ordinary output', session='other')
        for tool in ('', 'bash', 'missing'):
            for kind in ('', 'FileNotFoundError', 'RuntimeError'):
                for start in ('', '2099-01-01T00:00:00+00:00'):
                    kwargs=dict(tool=tool, anomaly_type=kind, start=start)
                    ref=a.listing(self.store, _include_total=False, **kwargs)
                    snapshot=ref['snapshot_seq']; total=len(ref['items'])
                    while ref['next_before'] is not None:
                        ref=a.listing(self.store, before=ref['next_before'], through=snapshot, _include_total=False, **kwargs)
                        total+=len(ref['items'])
                    for before in (None, 401):
                        page=a.listing(self.store, before=before, through=snapshot, **kwargs)
                        self.assertEqual(page['total_count'], total)
                        self.assertLessEqual(len(page['items']), 100)

    def test_repeat_reads_are_bounded_and_exclude_new_arrivals(self):
        first, inv, _ = self.emit(); self.add_copies(1100)
        rows=a.repeat_rows(self.store, inv, first, 1101)
        self.assertEqual(next(rows)['seq'], first)
        # Writing while the generator is suspended must not encounter a read lock.
        self.emit()
        rest=list(rows)
        self.assertEqual(len(rest), 1100)
        self.assertEqual(rest[-1]['seq'], 1101)

    def test_session_deleted_between_repeat_batches_has_unknown_count(self):
        first, inv, _ = self.emit()
        self.add_copies(600, {'tool_name':'Bash','tool_input':{'command':'different'},'tool_response':'done',
                              '_coverage':{'capture_boundary_version':1}})
        original=a.repeat_rows
        def deleting(*args):
            for index,row in enumerate(original(*args)):
                yield row
                if index==499:
                    with self.store.connect() as db:
                        db.execute('DELETE FROM events WHERE investigation=?',(inv,))
        items=[{'id':str(first),'investigation':inv}]
        with patch.object(a,'repeat_rows',deleting):
            a.enrich_list_items(self.store,items,601)
        self.assertIsNone(items[0]['repeat_count'])
        self.assertEqual(items[0]['source_summary'],'')

    def test_list_summary_is_source_backed_and_repeat_count_matches_detail_snapshot(self):
        trace = TRACE.replace('FileNotFoundError: absent', "FileNotFoundError: [Errno 2] No such file or directory: 'output/model.blend'")
        first, _, _ = self.emit(trace, turn_id='round-a')
        frozen = a.listing(self.store)
        self.assertEqual(frozen['items'][0]['source_summary'], 'model.blend')
        self.assertEqual(frozen['items'][0]['source_summary_full'], 'output/model.blend')
        self.assertEqual(frozen['items'][0]['repeat_count'], 0)
        self.emit('done', turn_id='round-a')
        current = a.listing(self.store)
        item = current['items'][0]
        self.assertEqual(item['repeat_count'], 1)
        self.assertEqual(item['repeat_count'], len(a.detail(self.store, first, current['snapshot_seq'])['events'])-1)
        self.assertEqual(a.listing(self.store, through=frozen['snapshot_seq'])['items'], frozen['items'])
        self.emit('done', turn_id='round-b')
        self.assertEqual(a.listing(self.store)['items'][0]['repeat_count'], 1)

    def test_card_path_leaf_preserves_full_location_and_special_inputs(self):
        cases = [
            (r'D:\Code\BlackboxPlugin\tests\test_cleanup.py', 'test_cleanup.py'),
            (r'\\server\share\中文 文件.py', '中文 文件.py'),
            ('output/model.blend', 'model.blend'),
            ('/tmp/results/', 'results/'),
            ('C:\\', 'C:\\'),
            ('/', '/'),
            ('<stdin>', '<stdin>'),
            ('<frozen importlib._bootstrap>', '<frozen importlib._bootstrap>'),
            ('https://example.com/file', 'https://example.com/file'),
        ]
        for path, short in cases:
            with self.subTest(path=path):
                self.assertEqual(a.path_summary(path, ' · line 91'), (short+' · line 91', path+' · line 91'))
        long_path = 'D:/' + 'directory/'*30 + 'last.py'
        self.assertEqual(a.source_summary_parts({'tool_input':{'file_path':long_path}}), ('last.py',long_path))
        self.assertTrue(a.path_summary('/'+'x'*5000)[1].endswith('…'))
        self.assertEqual(a.source_summary_parts({'tool_name':'Bash','tool_input':{'command':'Get-Content D:/dir/file.py'}}), ('执行：Get-Content D:/dir/file.py',)*2)

    def test_list_summary_frame_fallback_and_count_pages_do_not_enrich(self):
        self.emit()
        page = a.listing(self.store)
        self.assertEqual(page['items'][0]['source_summary'], 'example.py · line 1')
        with patch.object(a, 'enrich_list_items', side_effect=AssertionError('count page must not enrich')):
            a.listing(self.store, _include_total=False)
        self.assertEqual(a.source_summary_parts({'tool_name':'Bash','tool_input':{'command':'echo sample\nsecond line'}}), ('执行：echo sample',)*2)
        self.assertEqual(a.source_summary_parts({'tool_input':{'path':'<img src=x>'}}), ('<img src=x>',)*2)

    def test_list_repeat_count_preserves_legacy_and_gap_boundaries(self):
        first, _, _ = self.emit(turn_id='round-a')
        self.emit('done', turn_id='round-a')
        with self.store.connect() as db:
            row = db.execute('SELECT content FROM events WHERE seq=?', (first,)).fetchone()
            content = json.loads(row['content']);content.pop('_coverage', None)
            db.execute('UPDATE events SET content=? WHERE seq=?', (json.dumps(content), first))
        item = a.listing(self.store)['items'][0]
        self.assertEqual(item['repeat_count'], 0)
        self.assertEqual(len(a.detail(self.store, first)['events']), 1)

        other, _, _ = self.emit(session='gap', turn_id='gap-round')
        later, _, _ = self.emit('done', session='gap', turn_id='gap-round')
        with self.store.connect() as db:
            content = json.loads(db.execute('SELECT content FROM events WHERE seq=?', (later,)).fetchone()['content'])
            content['_coverage']['capture_gap_before'] = True
            db.execute('UPDATE events SET content=? WHERE seq=?', (json.dumps(content), later))
        item = next(r for r in a.listing(self.store)['items'] if r['id'] == str(other))
        self.assertEqual(item['repeat_count'], 0)
        self.assertEqual(len(a.detail(self.store, other)['events']), 1)

    def test_workspace_presence_includes_other_projects_and_respects_snapshot(self):
        self.emit(output='success', session='quiet')
        quiet = a.contexts(self.store, str(self.project.resolve()))['sessions'][0]['id']
        initial = a.anomaly_types(self.store, quiet)
        self.assertFalse(initial['workspace_has_anomalies'])
        other = self.root / 'other'; other.mkdir(); self.store.set_project(str(other), True)
        self.emit(cwd=str(other), session='other')
        self.assertTrue(a.anomaly_types(self.store, quiet)['workspace_has_anomalies'])
        self.emit(session='broken')
        self.assertTrue(a.anomaly_types(self.store, quiet)['workspace_has_anomalies'])
        self.assertEqual(a.anomaly_types(self.store, quiet)['types'], [])
        self.assertFalse(a.anomaly_types(self.store, quiet, initial['snapshot_seq'])['workspace_has_anomalies'])

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
        for body in (TRACE, PS, 'ordinary log\n'+TRACE):
            self.assertIsNotNone(a.detect({'tool_name': 'Bash', 'tool_response': body}))
        for body in ('', 'null', 'error', 'tests failed', 'exit code 1',
                     'Traceback (most recent call last):\nTypeError: missing frame',
                     '  File "x.py", line 1\nTypeError: missing header',
                     'Traceback (most recent call last):\n  File "x.py", line 1\nUnrelated log\nTypeError: disconnected'):
            self.assertIsNone(a.detect({'tool_name': 'Bash', 'tool_response': body}))
        self.assertIsNone(a.detect({'tool_name': 'Read', 'tool_response': TRACE}))
        self.emit('no results', tool_input={'command': TRACE})
        self.emit(TRACE, hook_event_name='UserPromptSubmit')
        self.emit('')
        self.assertEqual(a.listing(self.store)['items'], [])

    def test_python_exception_grammar_and_signal_are_explicit(self):
        for footer in ('TypeError: bad type', 'ModuleNotFoundError: missing',
                       'custom.failure.SpecialFailure: failed', 'KeyboardInterrupt'):
            trace = TRACE.rsplit('FileNotFoundError:', 1)[0] + footer + '\n'
            result = a.detect({'tool_name': 'Bash', 'tool_response': 'before\n'+trace+'after\n'})
            self.assertEqual(result['rule'], 'python-exception')
            self.assertEqual(result['evidence'], trace.rstrip())
            self.assertEqual(result['signal']['kind'], 'diagnostic_format')
            self.assertFalse(result['signal']['independently_verified'])
            self.assertEqual(result['signal']['segment'], 'full')

    def test_real_local_python_tracebacks_match_without_claiming_host_evidence(self):
        # Exercise Python's actual formatting. These subprocesses are not Codex
        # Hook integration evidence and do not establish another tool contract.
        samples = (("1 + 'x'", 'TypeError'),
                   ('import blackbox_missing_module_for_traceback_test', 'ModuleNotFoundError'),
                   ("print('ordinary log', flush=True); 1 + 'x'", 'TypeError'))
        for code, name in samples:
            with self.subTest(name=name, code=code):
                process = subprocess.run([sys.executable, '-I', '-c', code],
                                         capture_output=True, text=True, timeout=10, check=False)
                self.assertNotEqual(process.returncode, 0)
                hit = a.detect({'tool_name': 'Bash', 'tool_response': process.stdout + process.stderr})
                self.assertEqual(hit['title'], '工具返回 ' + name)
                self.assertIn(hit['evidence'], process.stderr)
                self.assertFalse(hit['signal']['independently_verified'])

    def test_truncated_segments_require_a_complete_trace_without_joining(self):
        def content(head, tail):
            return {'tool_name': 'Bash', 'tool_response': {'preview': head, 'tail': tail,
                    'truncated': True, 'original_length': 100_000},
                    '_coverage': {'truncated_fields': ['tool_response']}}
        tail_hit = a.detect(content('normal logs\ncut line', 'cut prefix\n'+TRACE))
        self.assertEqual(tail_hit['signal']['segment'], 'tail')
        self.assertTrue(tail_hit['signal']['truncated'])
        head_hit = a.detect(content(TRACE+'cut line', 'cut tail\nnormal logs'))
        self.assertEqual(head_hit['signal']['segment'], 'head')
        self.assertIsNone(a.detect(content(TRACE.split('FileNotFoundError')[0], 'cut\nFileNotFoundError: absent\n')))
        self.assertIsNone(a.detect(content(TRACE.rstrip(), 'cut\nnormal logs')))
        self.assertIsNone(a.detect({'tool_name': 'Bash', 'tool_response':
                                  {'preview': json.dumps(TRACE), 'truncated': True}}))
        self.assertIsNone(a.detect({'tool_name': 'Bash', 'tool_response': TRACE,
                                  '_coverage': {'truncated_fields': ['tool_response']}}))

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

    def test_new_rounds_and_missing_turn_identity_do_not_create_associations(self):
        for boundary in ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd'):
            with self.subTest(boundary=boundary):
                first, _, _ = self.emit(session=boundary)
                self.emit('', session=boundary, hook_event_name=boundary)
                self.emit('done', session=boundary)
                self.assertEqual(len(a.detail(self.store, first)['events']), 1)
        for first_turn, second_turn in (('one', 'two'), ('one', None), (None, 'two')):
            with self.subTest(turns=(first_turn, second_turn)):
                session = str((first_turn, second_turn))
                first, _, _ = self.emit(session=session, turn_id=first_turn)
                self.emit('done', session=session, turn_id=second_turn)
                self.assertEqual(len(a.detail(self.store, first)['events']), 1)
        first, _, _ = self.emit(session='same-turn', turn_id='one')
        later, _, _ = self.emit('done', session='same-turn', turn_id='one')
        self.assertEqual([e['seq'] for e in a.detail(self.store, first)['events']], [first, later])

    def test_truncated_input_and_redaction_do_not_create_associations(self):
        first, _, _ = self.emit(command='x'*MAX_FIELD)
        self.emit('done', command='x'*MAX_FIELD)
        self.assertEqual(len(a.detail(self.store, first)['events']), 1)
        first, _, _ = self.emit(tool_input={'command':'same','password':'one'})
        self.emit('done', tool_input={'command':'same','password':'two'})
        self.assertEqual(len(a.detail(self.store, first)['events']), 1)

    def test_output_truncation_retains_tail_errors_and_identical_input_links(self):
        first, _, _ = self.emit('ordinary log\n' + 'x'*MAX_FIELD + '\n' + TRACE)
        later, _, _ = self.emit('done')
        detail = a.detail(self.store, first)
        self.assertEqual(detail['signal']['segment'], 'tail')
        self.assertEqual([e['seq'] for e in detail['events']], [first, later])
        self.assertEqual(detail['association'], 'same-input-repeat')
        self.assertIn('相同输入再次调用', detail['markdown'])

    def test_escaped_output_preserves_full_text_when_raw_characters_fit_budget(self):
        output = '\n' * 140 + TRACE
        self.assertLess(len(output), 300)
        self.assertGreater(len(json.dumps(output)), 300)
        with patch.object(core, 'MAX_FIELD', 300):
            first, _, _ = self.emit(output)
        detail = a.detail(self.store, first)
        self.assertFalse(detail['signal']['truncated'])
        self.assertEqual(detail['signal']['format'], 'python-exception')
        self.assertEqual(json.loads(detail['events'][0]['content'])['tool_response'], output)

    def test_dropped_boundary_at_capacity_prevents_cross_gap_association(self):
        first, _, _ = self.emit()
        with patch.object(core, 'MAX_SESSION_BYTES', 1):
            with self.assertRaises(core.CaptureLimit):
                self.emit('', hook_event_name='Stop')
            later, _, _ = self.emit()
            third, _, _ = self.emit()
        for seq in (first, later):
            detail = a.detail(self.store, seq)
            self.assertEqual([event['seq'] for event in detail['events']], [seq])
            self.assertTrue(detail['capture_gap'])
            self.assertIn('采集缺口', detail['markdown'])
        self.assertEqual(len(a.listing(self.store)['items']), 3)

    def test_concurrent_readers_preserve_database_and_old_derived_tables(self):
        first, record, _ = self.emit()
        with self.store.connect() as db:
            db.execute('CREATE TABLE anomalies (event_seq INTEGER PRIMARY KEY, rule TEXT, title TEXT)')
            db.execute('CREATE TABLE anomaly_index_state (rule_version TEXT, scanned_seq INTEGER)')
            db.execute("INSERT INTO anomaly_index_state VALUES ('old-rule',999999)")
            db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)',
                       ('human', record, 1, first, 'kept', 'original human text', '2026-01-01T00:00:00Z'))
        before = self.store.database.read_bytes()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: a.listing(self.store), range(4)))
        self.assertTrue(all(len(r['items']) == 1 for r in results))
        self.assertEqual(self.store.database.read_bytes(), before)
        self.assertEqual(list(self.store.directory.glob('before-anomalies-*.sqlite3')), [])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT rule_version FROM anomaly_index_state').fetchone()[0], 'old-rule')
            self.assertEqual(db.execute('SELECT body FROM revisions WHERE id=?', ('human',)).fetchone()[0], 'original human text')

    def test_listing_and_detail_work_with_readonly_full_database(self):
        first, _, _ = self.emit()
        self.emit()
        @contextmanager
        def readonly_connection():
            db = sqlite3.connect(self.store.database.as_uri() + '?mode=ro', uri=True, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            try:
                yield db
            finally:
                db.close()
        before = self.store.database.read_bytes()
        with patch.object(core, 'MAX_DATABASE_BYTES', 1), patch.object(self.store, 'connect', readonly_connection):
            self.assertEqual(len(a.listing(self.store)['items']), 2)
            self.assertEqual(a.detail(self.store, first)['id'], str(first))
        self.assertEqual(self.store.database.read_bytes(), before)

    def test_filters_and_invalid_cursors(self):
        first, _, _ = self.emit()
        self.assertEqual(len(a.listing(self.store, tool='bash', project='project')['items']), 1)
        self.assertEqual(a.listing(self.store, tool='other')['items'], [])
        self.assertEqual(a.listing(self.store, start='2100-01-01T00:00:00+00:00')['items'], [])
        for args in ({'start':'bad'}, {'start':'2026-01-01'}, {'before':'-1'}, {'before':'9'*30},
                     {'through': first+1}, {'through': True}, {'through': -1}, {'through': 0.0},
                     {'start':'2026-01-02T00:00:00Z', 'end':'2026-01-01T00:00:00Z'},
                     {'project_path': None}, {'investigation': True},
                     {'project_path': 'x'*32768}, {'investigation': 'x'*201}):
            with self.assertRaises(NotesError):
                a.listing(self.store, **args)
        with self.assertRaises(NotesError):
            a.detail(self.store, first, first+100)

    def test_exact_project_and_session_filters_do_not_merge_matching_prefixes_or_ids(self):
        first, first_session, _ = self.emit(session='same-session')
        second, second_session, _ = self.emit(session='another-session')
        sibling = self.root / 'project-extra'
        sibling.mkdir()
        self.store.set_project(str(sibling), True)
        third, third_session, _ = self.emit(session='same-session', cwd=str(sibling))
        self.assertNotEqual(first_session, third_session)
        project = str(self.project.resolve())
        page = a.listing(self.store, project_path=project)
        self.assertEqual([item['id'] for item in page['items']], [str(second), str(first)])
        self.assertEqual({item['session'] for item in page['items']}, {'same-session', 'another-session'})
        self.assertEqual({item['investigation'] for item in page['items']}, {first_session, second_session})
        self.assertEqual(len(a.listing(self.store, project='project')['items']), 3)
        self.assertEqual([item['id'] for item in a.listing(self.store, investigation=first_session)['items']],
                         [str(first)])
        self.assertEqual(a.listing(self.store, project_path=project, investigation=third_session)['items'], [])
        frozen = a.listing(self.store, project_path=project, investigation=first_session)
        self.emit(session='same-session')
        self.assertEqual(a.listing(self.store, project_path=project, investigation=first_session,
                                  through=frozen['snapshot_seq'])['items'], frozen['items'])
        self.assertEqual(a.detail(self.store, third)['session'], 'same-session')
        self.assertEqual(a.detail(self.store, third)['investigation'], third_session)

    def test_contexts_are_readonly_metadata_and_keep_disabled_project_sessions(self):
        _, first_session, _ = self.emit(session='first-session')
        _, second_session, _ = self.emit(session='second-session', hook_event_name='UserPromptSubmit',
                                         prompt='private prompt must not become a session label')
        other = self.root / 'other-project'
        other.mkdir()
        self.store.set_project(str(other), True)
        self.emit(session='first-session', cwd=str(other))
        empty_project = self.root / 'empty-project'
        empty_project.mkdir()
        self.store.set_project(str(empty_project), False)
        self.store.set_project(str(self.project), False)
        with self.store.connect() as db:
            db.execute('UPDATE events SET captured=? WHERE investigation=?',
                       ('2026-01-02T00:00:00+00:00', first_session))
            # Broken/legacy event bodies cannot prevent context metadata reading.
            db.execute('UPDATE events SET content=? WHERE investigation=?', ('not JSON', second_session))
        before = self.store.database.read_bytes()
        @contextmanager
        def scalar_only_connection():
            db = sqlite3.connect(self.store.database.as_uri() + '?mode=ro', uri=True, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.set_authorizer(lambda action, table, column, *_:
                              sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ
                              and table == 'events' and column == 'content' else sqlite3.SQLITE_OK)
            try:
                yield db
            finally:
                db.close()
        with patch.object(self.store, 'connect', scalar_only_connection):
            result = a.contexts(self.store, str(self.project.resolve()))
            self.assertEqual(a.contexts(self.store)['sessions'], [])
            self.assertEqual(a.contexts(self.store, str(self.project) + '-missing')['sessions'], [])
        self.assertEqual(self.store.database.read_bytes(), before)
        self.assertEqual([(p['path'], p['enabled']) for p in result['projects']], [
            (str(other.resolve()), True), (str(self.project.resolve()), False), (str(empty_project.resolve()), False)])
        self.assertTrue(all(p['last_captured'] for p in result['projects'][:2]))
        self.assertIsNone(result['projects'][-1]['last_captured'])
        self.assertEqual([session['id'] for session in result['sessions']], [second_session, first_session])
        self.assertTrue(all(set(session) == {'id', 'session', 'first_captured', 'last_captured', 'archived'}
                            for session in result['sessions']))
        self.assertEqual(result['sessions'][1]['first_captured'], '2026-01-02T00:00:00+00:00')
        self.assertEqual(result['sessions'][1]['last_captured'], '2026-01-02T00:00:00+00:00')
        self.assertNotIn('private prompt', json.dumps(result))

    def test_contexts_do_not_silently_truncate_sessions(self):
        with self.store.connect() as db:
            db.execute('BEGIN')
            db.executemany('INSERT INTO investigations VALUES (?,?,?,?)',
                           ((f'id-{n:04}', str(self.project.resolve()), f'session-{n}',
                             '2026-01-01T00:00:00Z') for n in range(504)))
            db.executemany('''INSERT INTO events(investigation,event_key,kind,content,digest,captured)
                              VALUES(?,?,?,?,?,?)''',
                           ((f'id-{n:04}', f'event-{n}', 'SessionStart', '{}', 'test-digest',
                             '2026-01-01T00:00:00Z') for n in range(503)))
            db.commit()
        result = a.contexts(self.store, str(self.project.resolve()))
        self.assertEqual(len(result['sessions']), 503)
        self.assertTrue(all(session['first_captured'] and session['last_captured']
                            for session in result['sessions']))
        self.assertNotIn('id-0503', [session['id'] for session in result['sessions']])

    def test_export_session_context_and_encoding_notice_only_follow_readable_output(self):
        first, investigation, _ = self.emit(command='python example.py # \ufffd', session='session-\ufffd')
        result = a.detail(self.store, first)
        self.assertEqual(result['session'], 'session-\ufffd')
        self.assertEqual(result['investigation'], investigation)
        self.assertIn(a.fenced('session-\ufffd'), result['markdown'])
        self.assertIn('- 会话记录 ID：' + investigation, result['markdown'])
        self.assertIn('## 技术详情 · 已保存来源', result['markdown'])
        self.assertNotIn('未捕获到符合边界规则', result['markdown'])
        self.assertNotIn('部分文字可能显示不完整', result['markdown'])
        noise, _, _ = self.emit('\ufffd ordinary log\n' + TRACE, session='noise')
        self.assertNotIn('部分文字可能显示不完整', a.detail(self.store, noise)['markdown'])
        error, _, _ = self.emit(TRACE.replace('absent', '\ufffd'), session='error')
        self.assertIn('> 部分文字可能显示不完整。', a.detail(self.store, error)['markdown'])
        self.emit('repeated output \ufffd', command='python example.py # \ufffd', session='session-\ufffd')
        linked = a.detail(self.store, first)
        self.assertEqual(len(linked['events']), 2)
        self.assertIn('> 部分文字可能显示不完整。', linked['markdown'])
        self.assertNotIn('原始文字可能已丢失', linked['markdown'])

    def add_copies(self, count, content=None):
        # Use direct fixture insertion to exercise batch limits without renderer overhead.
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM events LIMIT 1').fetchone()
            db.execute('BEGIN')
            for i in range(count):
                db.execute('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES(?,?,?,?,?,?)',
                           (row['investigation'], 'extra-'+str(i), row['kind'],
                            json.dumps(content) if content else row['content'], row['digest'], row['captured']))
            db.commit()

    def test_501_events_paginate_newest_first_without_missing_or_duplicate_items(self):
        self.emit()
        self.add_copies(500)
        page = a.listing(self.store)
        self.assertEqual(page['items'][0]['id'], '501')
        seen = []
        while True:
            self.assertFalse(page['index_pending'])
            self.assertEqual(page['snapshot_seq'], 501)
            self.assertEqual(page['total_count'], 501)
            self.assertLessEqual(len(page['items']), 100)
            self.assertLessEqual(page['scanned_count'], 500)
            seen.extend(int(item['id']) for item in page['items'])
            if page['next_before'] is None:
                break
            page = a.listing(self.store, before=page['next_before'], through=page['snapshot_seq'])
        self.assertEqual(seen, list(range(501, 0, -1)))

    def test_filtered_empty_windows_are_scanned_until_first_matching_page(self):
        first, _, _ = self.emit()
        self.add_copies(1500, {'tool_name': 'Read', 'tool_response': TRACE})
        page = a.listing(self.store, tool='bash')
        self.assertEqual([item['id'] for item in page['items']], [str(first)])
        self.assertEqual(page['scanned_count'], 1501)
        self.assertEqual(page['total_count'], 1)
        self.assertIsNone(page['next_before'])

    def test_old_error_after_501_normal_calls_is_visible_on_first_request(self):
        first, _, _ = self.emit()
        self.add_copies(501, {'tool_name': 'Bash', 'tool_response': 'ordinary successful output'})
        before = self.store.database.read_bytes()
        page = a.listing(self.store)
        self.assertEqual([item['id'] for item in page['items']], [str(first)])
        self.assertEqual(page['scanned_count'], 502)
        self.assertEqual(page['total_count'], 1)
        self.assertIsNone(page['next_before'])
        self.assertEqual(self.store.database.read_bytes(), before)

    def test_internal_count_scan_stays_bounded_and_all_empty_query_terminates(self):
        self.emit()
        self.add_copies(1001, {'tool_name': 'Read', 'tool_response': TRACE})
        page = a.listing(self.store, tool='bash', _include_total=False)
        self.assertEqual(page['items'], [])
        self.assertEqual(page['scanned_count'], 500)
        self.assertEqual(page['next_before'], '503')
        self.assertNotIn('total_count', page)
        no_matches = a.listing(self.store, tool='missing-tool')
        self.assertEqual(no_matches['items'], [])
        self.assertEqual(no_matches['total_count'], 0)
        self.assertEqual(no_matches['scanned_count'], 1002)
        self.assertIsNone(no_matches['next_before'])

    def test_auto_scan_preserves_snapshot_when_new_event_arrives_during_detection(self):
        first, _, _ = self.emit()
        self.add_copies(501, {'tool_name': 'Bash', 'tool_response': 'ordinary successful output'})
        detect = a.detect
        added = []

        def detect_with_arrival(content):
            if not added:
                added.append(True)
                self.emit(session='arrived-during-scan')
            return detect(content)

        with patch.object(a, 'detect', detect_with_arrival):
            page = a.listing(self.store)
        self.assertEqual(page['snapshot_seq'], 502)
        self.assertEqual(page['total_count'], 1)
        self.assertEqual([item['id'] for item in page['items']], [str(first)])
        self.assertTrue(page['has_newer'])
        self.assertEqual(a.listing(self.store)['total_count'], 2)

    def test_empty_windows_between_result_pages_do_not_lose_or_repeat_matches(self):
        first, _, _ = self.emit()
        self.add_copies(501, {'tool_name': 'Bash', 'tool_response': 'ordinary successful output'})
        # Keep fixture keys unique while appending a full newest result page.
        with self.store.connect() as db:
            original = db.execute('SELECT * FROM events WHERE seq=?', (first,)).fetchone()
            db.execute('BEGIN')
            for number in range(100):
                db.execute('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES(?,?,?,?,?,?)',
                           (original['investigation'], 'newest-error-' + str(number), original['kind'],
                            original['content'], original['digest'], original['captured']))
            db.commit()
        page = a.listing(self.store)
        self.assertEqual(len(page['items']), 100)
        self.assertEqual(page['total_count'], 101)
        last = a.listing(self.store, before=page['next_before'], through=page['snapshot_seq'])
        self.assertEqual([item['id'] for item in last['items']], [str(first)])
        self.assertEqual(last['total_count'], 101)
        self.assertEqual(last['scanned_count'], 502)
        self.assertIsNone(last['next_before'])
        seen = [item['id'] for item in page['items'] + last['items']]
        self.assertEqual(len(seen), len(set(seen)))

    def test_list_snapshot_excludes_concurrent_arrivals_and_detail_accepts_global_bound(self):
        first, _, _ = self.emit()
        self.add_copies(100)
        page = a.listing(self.store)
        newer, _, _ = self.emit(session='another')
        next_page = a.listing(self.store, before=page['next_before'], through=page['snapshot_seq'])
        self.assertTrue(next_page['has_newer'])
        self.assertEqual(page['total_count'], 101)
        self.assertEqual(next_page['total_count'], 101)
        self.assertEqual([item['id'] for item in next_page['items']], [str(first)])
        current = a.listing(self.store)
        self.assertEqual(current['total_count'], 102)
        self.assertEqual(current['items'][0]['id'], str(newer))
        snapshot = a.detail(self.store, first, current['snapshot_seq'])
        self.assertEqual(snapshot['source_seq'], newer)
        self.assertEqual(a.detail(self.store, first, snapshot['source_seq'])['markdown'], snapshot['markdown'])

    def test_empty_snapshot_remains_empty_after_new_arrivals(self):
        empty = a.listing(self.store)
        self.assertEqual(empty['snapshot_seq'], 0)
        self.assertIsNone(empty['next_before'])
        self.emit()
        frozen = a.listing(self.store, through=empty['snapshot_seq'])
        self.assertEqual(frozen['items'], [])
        self.assertTrue(frozen['has_newer'])

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
            for path in ('/api/anomalies','/api/anomaly-contexts',
                         '/api/anomaly/'+str(first),'/api/anomaly-export/'+str(first)):
                self.assertEqual(request(path,False)[0],401)
                self.assertEqual(request(path)[0],200)
            home = request('/')[1].decode('utf-8')
            self.assertIn('id="list-view"', home)
            self.assertIn('id="detail-view"', home)
            self.assertIn('不仅限于报错的调用', home)
            self.assertNotIn('href="./history"', home)
            self.assertIn('历史会话'.encode(),request('/history')[1])
            page=json.loads(request('/api/anomalies')[1])
            snapshot=json.loads(request('/api/anomaly/'+str(first))[1])
            context=json.loads(request('/api/anomaly-contexts?' + urlencode({
                'project_path':str(self.project.resolve())}))[1])
            self.assertEqual(context['sessions'][0]['id'], snapshot['investigation'])
            scoped=json.loads(request('/api/anomalies?' + urlencode({
                'project_path':str(self.project.resolve()), 'investigation':snapshot['investigation']}))[1])
            self.assertEqual(scoped['items'], page['items'])
            self.emit(session='new session')
            frozen=json.loads(request('/api/anomalies?through='+str(page['snapshot_seq']))[1])
            self.assertEqual([item['id'] for item in frozen['items']], [str(first)])
            self.assertTrue(frozen['has_newer'])
            exported=request('/api/anomaly-export/'+str(first)+'?through='+str(snapshot['source_seq']))[1].decode()
            self.assertEqual(exported,snapshot['markdown'])
            self.assertEqual(request('/api/anomaly-export/'+str(first)+'?rule=old')[0],400)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
