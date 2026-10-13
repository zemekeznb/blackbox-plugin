from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
import tas_notes_codex as c
from tas_notes_anomalies import saved_sessions
from tas_notes_core import NotesError, Store
from tas_notes_web import ReaderSelection, make_server


class WrittenPipe:
    def __init__(self):
        self.content = bytearray()
        self.closed = False

    def write(self, value):
        self.content.extend(value)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, replies):
        self.stdin = WrittenPipe()
        self.stdout = io.BytesIO(b''.join(json.dumps(value).encode() + b'\n' for value in replies))
        self.terminated = 0
        self.killed = 0
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = 0

    def kill(self):
        self.killed += 1
        self.returncode = -1

    def wait(self, timeout):
        return self.returncode


class NameProviderTests(unittest.TestCase):
    def invoke(self, process, identifiers):
        with patch.object(c, 'codex_executable', return_value='codex.exe'), \
                patch.object(c.subprocess, 'Popen', return_value=process) as popen:
            result = c.SessionNames().names(identifiers)
        return result, popen

    def test_only_exact_thread_names_are_used_from_metadata_only_requests(self):
        process = FakeProcess([
            {'id': 1, 'result': {}},
            {'method': 'notification', 'params': {'preview': 'must be ignored'}},
            {'id': 2, 'result': {'thread': {'id': 'thread-a', 'name': '  实际会话名称  ',
                                          'preview': 'not a title', 'turns': ['ignored']}}},
            {'id': 3, 'result': {'thread': {'id': 'wrong-thread', 'name': 'wrong title'}}},
            {'id': 4, 'result': {'thread': {'id': 'thread-c', 'preview': 'not a fallback'}}},
            {'id': 5, 'error': {'message': 'unsupported or missing'}},
            {'id': 6, 'result': {'thread': {'id': 'thread-e', 'name': 'available after missing'}}},
        ])
        result, popen = self.invoke(process, {f'i-{key}': f'thread-{key}' for key in 'abcde'})
        self.assertEqual(result, {'i-a': '实际会话名称', 'i-b': None, 'i-c': None,
                                  'i-d': None, 'i-e': 'available after missing'})
        sent = [json.loads(line) for line in process.stdin.content.splitlines()]
        self.assertEqual([item['method'] for item in sent], ['initialize', 'initialized'] + ['thread/read'] * 5)
        self.assertEqual([item['params'] for item in sent[2:]],
                         [{'threadId': f'thread-{key}', 'includeTurns': False} for key in 'abcde'])
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ['codex.exe', 'app-server', '--stdio'])
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(process.terminated, 1)
        self.assertTrue(process.stdin.closed)

    def test_unavailable_and_successful_names_are_cached_and_bounded(self):
        provider = c.SessionNames()
        with patch.object(c.time, 'monotonic', return_value=0), \
                patch.object(provider, '_lookup', return_value={'a': 'A'}) as lookup:
            self.assertEqual(provider.names({'i-a': 'a', 'i-b': 'b'}), {'i-a': 'A', 'i-b': None})
            self.assertEqual(provider.names({'another-a': 'a', 'i-b': 'b'}), {'another-a': 'A', 'i-b': None})
            lookup.assert_called_once_with(['a', 'b'])
        with patch.object(c.time, 'monotonic', return_value=61), \
                patch.object(provider, '_lookup', return_value={'a': 'renamed'}) as lookup:
            self.assertEqual(provider.names({'i-a': 'a'}), {'i-a': 'renamed'})
            lookup.assert_called_once_with(['a'])
        with patch.object(c, 'CACHE_ENTRIES', 2), patch.object(provider, '_lookup', return_value={}):
            provider.names({'i-c': 'c', 'i-d': 'd'})
            self.assertLessEqual(len(provider._cache), 2)
        with self.assertRaises(ValueError):
            provider.names({str(i): str(i) for i in range(21)})

    def test_unavailable_executable_does_not_spawn_and_missing_process_is_graceful(self):
        provider = c.SessionNames()
        with patch.object(c, 'codex_executable', return_value=None), patch.object(c.subprocess, 'Popen') as popen:
            self.assertEqual(provider.names({'i': 's'}), {'i': None})
            popen.assert_not_called()
        with patch.object(c, 'codex_executable', return_value='missing.exe'), \
                patch.object(c.subprocess, 'Popen', side_effect=FileNotFoundError) as popen:
            provider = c.SessionNames()
            self.assertEqual(provider.names({'i': 's'}), {'i': None})
            self.assertEqual(provider.names({'i': 's'}), {'i': None})
            self.assertEqual(popen.call_count, 1)

    def test_timeout_stops_owned_process_and_returns_null(self):
        class BlockingOutput:
            def __init__(self):
                self.event = threading.Event()

            def readline(self, _):
                self.event.wait(2)
                return b''

            def close(self):
                pass

        process = FakeProcess([])
        process.stdout = BlockingOutput()
        original_terminate = process.terminate

        def terminate():
            original_terminate()
            process.stdout.event.set()

        process.terminate = terminate
        started = time.monotonic()
        with patch.object(c, 'RPC_SECONDS', 0.02):
            result, _ = self.invoke(process, {'i': 's'})
        self.assertEqual(result, {'i': None})
        self.assertEqual(process.terminated, 1)
        self.assertLess(time.monotonic() - started, 1)

    def test_concurrent_request_does_not_start_second_lookup(self):
        provider = c.SessionNames()
        entered, release = threading.Event(), threading.Event()

        def lookup(_):
            entered.set()
            release.wait(2)
            return {'s': 'saved title'}

        with patch.object(provider, '_lookup', side_effect=lookup) as mocked, ThreadPoolExecutor(2) as pool:
            pending = pool.submit(provider.names, {'i': 's'})
            self.assertTrue(entered.wait(1))
            self.assertEqual(provider.names({'i': 's'}), {'i': None})
            release.set()
            self.assertEqual(pending.result(), {'i': 'saved title'})
            self.assertEqual(mocked.call_count, 1)

    def test_closing_reader_stops_current_child_and_disables_future_lookup(self):
        process = FakeProcess([])
        provider = c.SessionNames()
        provider._process = process
        provider.close()
        self.assertEqual(process.terminated, 1)
        with patch.object(c.subprocess, 'Popen') as popen:
            self.assertEqual(provider.names({'i': 's'}), {'i': None})
            popen.assert_not_called()

    def test_line_total_output_and_interactive_requests_are_bounded(self):
        variants = [
            b'x' * (c.MAX_LINE_BYTES + 1),
            json.dumps({'id': 99, 'method': 'approval/request'}).encode() + b'\n',
            b'[]\n', b'not json\n', b'[' * 1500 + b']' * 1500 + b'\n',
            b'{}\n' * (c.MAX_OUTPUT_BYTES // 3 + 1),
        ]
        for data in variants:
            process = FakeProcess([])
            process.stdout = io.BytesIO(data)
            result, _ = self.invoke(process, {'i': 's'})
            self.assertEqual(result, {'i': None})
            self.assertEqual(process.terminated, 1)
        for value in ('', 'x' * 513, 'title\nextra', {'name': 'not text'}, None):
            self.assertIsNone(c.display_name(value))


class ReadingMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.store = Store(self.root / 'state')
        self.store.set_project(str(self.project), True)
        self.identifiers = [self.store.record({'cwd': str(self.project), 'session_id': f'thread-{i}',
                            'hook_event_name': 'SessionStart'})['investigation'] for i in range(2)]

    def test_selection_is_id_only_atomic_and_survives_new_reader(self):
        selection = ReaderSelection(self.store)
        self.assertEqual(selection.get(), {'investigation': None, 'project_path': None})
        before = self.store.database.read_bytes()
        for identifier in self.identifiers:
            expected = {'investigation': identifier, 'project_path': str(self.project.resolve())}
            self.assertEqual(selection.set({'investigation': identifier}), expected)
            self.assertEqual(ReaderSelection(self.store).get(), expected)
            self.assertEqual(json.loads(selection.path.read_text()), {'investigation': identifier})
        self.assertEqual(self.store.database.read_bytes(), before)
        self.assertEqual(list(self.store.directory.glob('reader-selection-*.tmp')), [])

    def test_unknown_empty_or_deleted_selections_do_not_write_or_resolve(self):
        selection = ReaderSelection(self.store)
        for value in ({'investigation': 'unknown'}, {'investigation': ''}, {'investigation': None},
                      {'investigation': self.identifiers[0], 'name': 'do not save'}, []):
            with self.assertRaises(NotesError):
                selection.set(value)
            self.assertFalse(selection.path.exists())
        with self.store.connect() as db:
            db.execute('INSERT INTO investigations VALUES (?,?,?,?)',
                       ('empty-id', str(self.project.resolve()), 'empty-session', 'today'))
        with self.assertRaises(NotesError):
            saved_sessions(self.store, ['empty-id'])
        for data in ('not json', '[1]', '{"investigation":"unknown"}', 'x' * 1025):
            selection.path.write_text(data)
            self.assertEqual(selection.get(), {'investigation': None, 'project_path': None})

    def test_concurrent_selection_writes_leave_whole_valid_preference(self):
        selection = ReaderSelection(self.store)
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda identifier: selection.set({'investigation': identifier}),
                                    self.identifiers * 10))
        self.assertEqual(len(results), 20)
        self.assertIn(selection.get()['investigation'], self.identifiers)
        self.assertEqual(list(self.store.directory.glob('reader-selection-*.tmp')), [])

    def test_metadata_api_auth_validation_and_only_saved_threads_reach_provider(self):
        with patch.object(c.SessionNames, 'names', return_value={self.identifiers[0]: 'Real title'}) as names:
            server, token = make_server(self.store, 'test-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(path, method='GET', body=None, auth=True, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
                try:
                    supplied = {'Authorization': 'Bearer ' + token} if auth else {}
                    supplied.update(headers or {})
                    conn.request(method, path, body=body, headers=supplied)
                    response = conn.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    conn.close()

            try:
                query = '/api/session-names?' + urlencode({'investigation': self.identifiers[0]})
                self.assertEqual(request(query, auth=False)[0], 401)
                for path in ('/api/session-names', '/api/session-names?investigation=unknown',
                             '/api/session-names?session=thread-0',
                             '/api/session-names?' + '&'.join(['investigation=x'] * 21)):
                    self.assertEqual(request(path)[0], 400)
                names.assert_not_called()
                status, result = request(query)
                self.assertEqual((status, result), (200, {'names': {self.identifiers[0]: 'Real title'}, 'states': {self.identifiers[0]: 'named'}}))
                names.assert_called_once_with({self.identifiers[0]: 'thread-0'})
                self.assertEqual(request('/api/reader-selection', auth=False)[0], 401)
                self.assertIsNone(request('/api/reader-selection')[1]['investigation'])
                body = json.dumps({'investigation': self.identifiers[1]})
                headers = {'Content-Type': 'application/json'}
                self.assertEqual(request('/api/reader-selection', 'POST', body, False, headers)[0], 401)
                self.assertFalse((self.store.directory / 'reader-selection.json').exists())
                self.assertEqual(request('/api/reader-selection', 'POST', body, headers=headers)[0], 200)
                self.assertEqual(request('/api/reader-selection')[1], {
                    'investigation': self.identifiers[1], 'project_path': str(self.project.resolve())})
                self.assertEqual(request('/api/reader-selection', 'POST', 'x' * 1025, headers=headers)[0], 400)
                self.assertEqual(request('/api/reader-selection', 'POST', '[]', headers=headers)[0], 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()

class HostNavigationTests(unittest.TestCase):
    def test_distinguishes_missing_untitled_and_transient_without_returning_error_text(self):
        provider=c.SessionMetadata()
        self.assertEqual(provider._thread_value({'name':'Real'}),{'name':'Real','status':'named'})
        self.assertEqual(provider._thread_value({'name':None})['status'],'untitled')
        self.assertEqual(provider._thread_error({'error':{'code':-32600,'message':'thread not loaded: id'}})['status'],'unlinked')
        self.assertEqual(provider._thread_error({'error':{'code':-1,'message':'private error'}})['status'],'unavailable')

    def test_catalog_reads_only_bounded_project_metadata(self):
        process=FakeProcess([{'id':1,'result':{}},{'id':2,'result':{'data':[{'id':'p','name':'Codex label','roots':[{'path':'D:/one'},{'path':'D:/two'}],'private':'ignored'}]}}])
        found={};c.ProjectCatalog._exchange(process,[],found)
        self.assertEqual(found,{'catalog':[{'id':'p','name':'Codex label','roots':['D:/one','D:/two']}]})
        sent=[json.loads(x) for x in process.stdin.content.splitlines()]
        self.assertEqual([x['method'] for x in sent],['initialize','initialized','project/list'])

    def test_incomplete_catalog_never_guesses_root_ownership(self):
        process=FakeProcess([{'id':1,'result':{}}]+[{'id':i,'result':{'data':[],'nextCursor':'more'}} for i in range(2,7)])
        found={};c.ProjectCatalog._exchange(process,[],found)
        self.assertEqual(found,{})

    def test_multiple_saved_roots_merge_but_ambiguous_root_stays_unlinked(self):
        from unittest.mock import Mock
        catalog=Mock();catalog.projects.return_value=[{'id':'p','name':'Real project','roots':['D:/one','D:/two']}]
        data={'projects':[{'path':'d:\\one','enabled':True,'last_captured':'2'},{'path':'D:/two','enabled':False,'last_captured':'1'}],'sessions':[]}
        def contexts(_store,path=''):
            return {**data,'sessions':[{'id':path,'last_captured':'1'}] if path else []}
        with patch('tas_notes_anomalies.contexts',side_effect=contexts), patch('tas_notes_anomalies.anomaly_presence',return_value={'has_anomalies':False}):
            result=c.navigation_contexts(None,'D:/two',catalog)
            self.assertEqual(len(result['projects']),1)
            self.assertEqual(result['projects'][0]['name'],'Real project')
            self.assertEqual({x['project'] for x in result['sessions']},{'d:\\one','D:/two'})
            catalog.projects.return_value.append({'id':'q','name':'Other','roots':['D:/two']})
            result=c.navigation_contexts(None,'',catalog)
            self.assertEqual(len(result['projects']),2)
            self.assertIsNone(result['projects'][1]['name'])
