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

    def test_real_host_coverage_gaps_stay_unknown(self):
        event = self.event(tool_input={'command': 'cmd.exe /d /c exit 7'})
        event['tool_response'] = ''
        identifier = self.store.record(event)['investigation']
        detail = self.store.detail(identifier)
        self.assertIn('未提取结构化退出状态', detail['draft'])
        self.assertIn('工具返回缺失或为空', detail['draft'])
        # Rendering warnings must not fabricate or rewrite the stored host result.
        self.assertEqual(json.loads(detail['events'][0]['content'])['tool_response'], '')
        event.update(tool_use_id='tool-2', tool_response='Synthetic: \ufffd content.')
        self.store.record(event)
        self.store.record(dict(cwd=str(self.project), session_id='session-1', hook_event_name='Interrupt'))
        draft = self.store.detail(identifier)['draft']
        self.assertIn('可能已发生编码丢失', draft)
        self.assertIn('本条没有可用的正文或原因', draft)

    def test_status_configuration_capture_history_and_disable_are_separate(self):
        initial = self.store.status()
        self.assertEqual(initial['runtime']['state'], 'missing')
        self.assertTrue(initial['projects'][0]['enabled'])
        self.assertIsNone(initial['projects'][0]['last_recorded_event'])
        self.assertEqual(self.cli('configure-runtime').returncode, 0)
        configured = self.store.status()
        self.assertEqual(configured['runtime']['state'], 'configured')
        self.assertEqual(configured['continuous_capture'], 'unknown')
        self.assertEqual(configured['host_integration'], 'not_checked')
        self.capture()
        self.cli('hook', data=b'{synthetic-bad-input')
        failed = self.store.status()
        self.assertEqual(failed['record_count'], 1)
        self.assertEqual(failed['last_capture_error']['code'], 'JSONDecodeError')
        before = failed['projects'][0]['last_recorded_event']
        self.store.set_project(str(self.project), False)
        self.store.record(self.event())
        disabled = self.store.status()
        self.assertFalse(disabled['projects'][0]['enabled'])
        self.assertEqual(disabled['projects'][0]['last_recorded_event'], before)
        self.assertEqual(disabled['last_capture_error'], failed['last_capture_error'])

    def test_diagnostics_are_bounded_sanitized_and_never_execute_configuration(self):
        marker = self.store.directory / 'last-error.json'
        marker.write_text('{"code":"secret-value","time":"secret-value","body":"secret-value"}')
        runtime = self.store.directory / 'runtime.cmd'
        runtime.write_text('@echo secret-value > unexpected.txt')
        self.store.record(self.event(prompt='private-prompt-value'))
        value = self.store.status()
        self.assertEqual(value['runtime']['state'], 'invalid')
        self.assertEqual(value['last_capture_error']['code'], 'UnknownCaptureError')
        self.assertNotIn('secret-value', json.dumps(value))
        self.assertNotIn('private-prompt-value', json.dumps(value))
        self.assertFalse((self.store.directory / 'unexpected.txt').exists())
        marker.write_text('x' * 5000)
        self.assertEqual(self.store.status()['last_capture_error']['state'], 'unreadable')
        runtime.write_text('@"' + str(self.root / 'missing-python.exe') + '" %*\n')
        self.assertEqual(self.store.status()['runtime']['state'], 'target_missing')

    def test_first_prompt_title_is_a_label_and_human_title_wins(self):
        identifier = self.store.record(dict(cwd=str(self.project), session_id='title-test',
            hook_event_name='UserPromptSubmit', prompt='请排查\n  Y 的重试失败 ' + 'x' * 100))['investigation']
        row = self.store.listing('Y 的重试')[0]
        self.assertLessEqual(len(row['display_title']), 80)
        self.assertEqual(row['title_source'], 'first_prompt')
        detail = self.store.detail(identifier)
        self.assertEqual(detail['display_title'], row['display_title'])
        revision = self.store.save_revision(identifier, '人工标题', '人工正文', detail['source_seq'], 0)
        frozen = self.store.export_revision(revision['id'])
        self.store.record(dict(cwd=str(self.project), session_id='title-test',
            hook_event_name='UserPromptSubmit', prompt='还是有问题'))
        self.assertEqual(self.store.listing()[0]['display_title'], '人工标题')
        self.assertEqual(self.store.listing()[0]['title_source'], 'human')
        self.assertEqual(self.store.export_revision(revision['id']), frozen)

    def test_test_only_shape_probe_forwards_unicode_without_logging_values(self):
        target = self.root / 'forwarded.bin'
        receiver = self.root / 'receiver.py'
        receiver.write_text('import sys\nfrom pathlib import Path\nPath(' + repr(str(target))
                            + ').write_bytes(sys.stdin.buffer.read())\n', encoding='utf-8')
        payload = dict(hook_event_name='PostToolUse', tool_name='synthetic-secret-value',
                       tool_input={'command': '中文'}, tool_response='', turn_id='synthetic-secret-value')
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        result = subprocess.run([sys.executable, str(ROOT / 'tools/hook_shape_probe.py'), str(receiver)],
            input=raw, capture_output=True, timeout=10,
            env=dict(os.environ, TAS_NOTES_STATE_DIR=str(self.store.directory)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), raw)
        metadata = (self.store.directory / 'hook-shapes.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('synthetic-secret-value', metadata)
        self.assertNotIn('中文', metadata)
        self.assertEqual(json.loads(metadata)['fields']['tool_response'], 'str')

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

    def test_status_requires_auth_and_excludes_record_bodies(self):
        self.assertEqual(self.request('/api/status', auth=False)[0], 401)
        self.assertEqual(self.request('/api/status', Origin='https://example.com')[0], 403)
        code, raw, _ = self.request('/api/status')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)['record_count'], 1)
        self.assertNotIn(b'bad()', raw)

    def test_rejected_post_bodies_do_not_reset_or_write_revisions(self):
        for _ in range(100):
            self.assertEqual(self.request('/api/revisions', 'POST', {'body': 'synthetic ' * 100}, auth=False)[0], 401)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 0)

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
