import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store, NotesError
from tas_notes_mcp import Reader, TOOLS, URI, resource_html, downloads_directory
from tas_notes_anomalies import listing, detail, RULE_VERSION


class McpReaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.store = Store(self.root / 'state')
        self.project = self.root / 'project'; self.project.mkdir()
        self.store.set_project(str(self.project), True)
        self.event = self.store.record({'cwd': str(self.project), 'session_id': 'synthetic-session',
            'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'example'},
            'tool_response': 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: PRIVATE-SENTINEL\n'})
        self.reader = Reader(self.store.directory); self.addCleanup(self.reader.names.close)

    def read(self, path):
        result = self.reader.call('read_view', {'path': path})
        self.assertEqual(result['content'], [])
        self.assertNotIn('structuredContent', result)
        return result['_meta']['blackbox']

    def test_snapshot_parity_and_no_model_content_or_database_writes(self):
        before = self.store.database.read_bytes()
        data = self.read('/api/anomalies')['body']
        self.assertEqual(data, listing(self.store))
        item = data['items'][0]
        result = self.read('/api/anomaly/' + item['id'])
        expected = detail(self.store, item['id'])
        self.assertEqual(result['body'], expected)
        exported = self.read('/api/anomaly-export/' + item['id'] + '?through=' + str(expected['source_seq']) + '&rule=' + expected['rule_version'])
        self.assertEqual(exported['body'], expected['markdown'])
        self.assertIn('PRIVATE-SENTINEL', exported['body'])
        self.assertEqual(before, self.store.database.read_bytes())

    def test_only_saved_selection_changes_independent_preference(self):
        before = self.store.database.read_bytes()
        result = self.reader.call('select_session', {'investigation': self.event['investigation']})
        self.assertEqual(result['_meta']['blackbox']['status'], 200)
        self.assertEqual(self.read('/api/reader-selection')['body']['investigation'], self.event['investigation'])
        self.assertEqual(before, self.store.database.read_bytes())
        self.assertEqual(self.reader.call('select_session', {'investigation': 'unknown'})['_meta']['blackbox']['status'], 400)

    def test_markdown_read_binds_preview_hash_to_its_exact_utf8_body_only_in_metadata(self):
        result = self.reader.call('read_view', {'path': '/api/anomaly-export/1?through=1&rule=' + RULE_VERSION})
        metadata = result['_meta']['blackbox']
        self.assertEqual(result['content'], [])
        self.assertNotIn('structuredContent', result)
        self.assertIsInstance(metadata['body'], str)
        self.assertEqual(metadata['preview_sha256'], hashlib.sha256(metadata['body'].encode('utf-8')).hexdigest())
        self.assertEqual(metadata['body'], detail(self.store, '1', 1)['markdown'])
        for path in ('/api/status', '/api/anomaly/1?through=1', '/api/anomaly-export/missing'):
            with self.subTest(path=path):
                self.assertNotIn('preview_sha256', self.reader.call('read_view', {'path': path})['_meta']['blackbox'])

    def test_rejects_non_reader_routes_and_ambiguous_queries(self):
        for path in ['/api/shutdown','/api/revisions','/api/records','/api/export/x','file:///tmp/x','https://example.com/api/status',
                     '//example.com/api/status','/api/status?unexpected=x','/api/status#x','/api/anomalies?through=1&through=2',
                     '/api/anomaly/a/b','/api/anomaly/%2e%2e','/api/session-names?investigation=unknown']:
            with self.subTest(path=path): self.assertEqual(self.read(path)['status'],400)
        result=self.reader.call('read_view', {'path':'/api/status','body':'PRIVATE-SENTINEL'})
        self.assertNotIn('PRIVATE-SENTINEL',json.dumps(result))
        self.assertEqual(result['_meta']['blackbox']['status'],400)

    def test_missing_store_does_not_create_store(self):
        reader=Reader(self.root/'missing'); self.addCleanup(reader.names.close)
        self.assertEqual(reader.call('read_view',{'path':'/api/status'})['_meta']['blackbox']['status'],503)
        self.assertFalse((self.root/'missing').exists())

    def test_metadata_resource_and_open_do_not_access_records(self):
        for tool in TOOLS: self.assertEqual(tool['_meta']['ui']['visibility'],['app'])
        with patch('tas_notes_mcp.Store', side_effect=AssertionError('must not access DB')):
            self.assertNotIn('_meta',self.reader.call('open_reader',{}))
            html=self.reader.dispatch('resources/read',{'uri':URI})['contents'][0]['text']
        self.assertIn('window.blackboxRequest',html)
        self.assertNotIn('src="./',html)
        self.assertNotIn('PRIVATE-SENTINEL',html)
        self.assertIn('id="shutdown" hidden disabled',html)
        self.assertNotIn('ui/update-model-context',html)
        self.assertNotIn('ui/message',html)
        from tas_notes_launcher import release_version
        self.assertIn("version:'" + release_version() + "'", html)
        self.assertNotIn('__BLACKBOX_VERSION__', html)

    def test_names_only_resolve_saved_session_ids(self):
        with patch.object(self.reader.names,'names',return_value={'x':'title'}) as call:
            self.assertEqual(self.read('/api/session-names?investigation='+self.event['investigation'])['status'],200)
            call.assert_called_once_with({self.event['investigation']:'synthetic-session'})

    def test_browser_export_schema_is_app_only_and_advertises_service_side_effects(self):
        descriptor = next(tool for tool in TOOLS if tool['name'] == 'open_browser_export')
        self.assertEqual(descriptor['_meta']['ui']['visibility'], ['app'])
        self.assertFalse(descriptor['annotations']['readOnlyHint'])
        self.assertFalse(descriptor['annotations']['destructiveHint'])
        schema = descriptor['inputSchema']
        self.assertEqual(set(schema['properties']), {'record_id', 'through', 'rule'})
        self.assertEqual(set(schema['required']), set(schema['properties']))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['record_id'], {'type': 'string', 'maxLength': 64})
        self.assertEqual(schema['properties']['through'], {'type': 'integer', 'minimum': 1})
        self.assertEqual(schema['properties']['rule'], {'type': 'string', 'maxLength': 128})

    def test_browser_export_pins_saved_snapshot_without_changing_data_or_selection(self):
        snapshot = detail(self.store, listing(self.store)['items'][0]['id'])
        self.store.record({'cwd': str(self.project), 'session_id': 'newer-session', 'hook_event_name': 'SessionStart'})
        self.reader.call('select_session', {'investigation': self.event['investigation']})
        preference = self.store.directory / 'reader-selection.json'
        before, selected = self.store.database.read_bytes(), preference.read_bytes()
        base = 'http://127.0.0.1:22266/#token=' + 'a' * 43
        args = {'record_id': snapshot['id'], 'through': snapshot['source_seq'], 'rule': snapshot['rule_version']}
        with patch('tas_notes_mcp.ensure_reviewer', return_value={'pid': 123, 'url': base}) as launch:
            result = self.reader.call('open_browser_export', args)
        launch.assert_called_once()
        readonly_store = launch.call_args.args[0]
        self.assertTrue(readonly_store.read_only)
        self.assertEqual(readonly_store.database, self.store.database)
        self.assertEqual(result['content'], [])
        self.assertNotIn('structuredContent', result)
        self.assertEqual(result['_meta']['blackbox']['status'], 200)
        body = result['_meta']['blackbox']['body']
        self.assertEqual(set(body), {'url'})
        self.assertEqual(body['url'], base + '&record=' + snapshot['id'] + '&through=' + str(snapshot['source_seq']) + '&preview=1')
        self.assertEqual(parse_qs(urlsplit(body['url']).fragment), {
            'token': ['a' * 43], 'record': [snapshot['id']], 'through': [str(snapshot['source_seq'])], 'preview': ['1']})
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(result))
        self.assertEqual(before, self.store.database.read_bytes())
        self.assertEqual(selected, preference.read_bytes())

    def test_browser_export_rejects_invalid_or_extra_arguments_before_starting_service(self):
        valid = {'record_id': '1', 'through': 1, 'rule': RULE_VERSION}
        invalid = []
        for key in valid:
            invalid.append({name: value for name, value in valid.items() if name != key})
        for key in ('url', 'path', 'state_dir', 'command', 'body'):
            invalid.append(dict(valid, **{key: 'PRIVATE-SENTINEL'}))
        for value in (None, True, 1, '', '0', '-1', '1/2', '../1', '1&x=1', '1' * 65, '9' * 64):
            invalid.append(dict(valid, record_id=value))
        for value in (None, True, False, '1', 1.0, 0, -1, 2**63):
            invalid.append(dict(valid, through=value))
        for value in (None, True, 1, '', 'x' * 129, 'old-rule'):
            invalid.append(dict(valid, rule=value))
        before = self.store.database.read_bytes()
        with patch('tas_notes_mcp.ensure_reviewer') as launch:
            for args in invalid:
                with self.subTest(args=args):
                    result = self.reader.call('open_browser_export', args)
                    self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
                        'status': 400, 'body': {'error': 'invalid_request'}}}})
            launch.assert_not_called()
        self.assertEqual(before, self.store.database.read_bytes())

    def test_browser_export_rejects_missing_non_anomaly_future_and_pre_record_snapshots(self):
        self.store.record({'cwd': str(self.project), 'session_id': 'synthetic-session', 'hook_event_name': 'SessionStart'})
        self.store.record({'cwd': str(self.project), 'session_id': 'synthetic-session',
            'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'second'},
            'tool_response': 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: SECOND\n'})
        before = self.store.database.read_bytes()
        with patch('tas_notes_mcp.ensure_reviewer') as launch:
            for identifier, through in [('999', 3), ('2', 3), ('1', 4), ('3', 2)]:
                with self.subTest(identifier=identifier, through=through):
                    result = self.reader.call('open_browser_export', {'record_id': identifier, 'through': through, 'rule': RULE_VERSION})
                    self.assertEqual(result['_meta']['blackbox'], {'status': 400, 'body': {'error': 'invalid_request'}})
            launch.assert_not_called()
        self.assertEqual(before, self.store.database.read_bytes())

    def test_browser_export_keeps_old_service_running_and_reports_build_mismatch_safely(self):
        error = NotesError('PRIVATE-SENTINEL'); error.public_code = 'reviewer_build_changed'
        with patch('tas_notes_mcp.ensure_reviewer', side_effect=error), patch('tas_notes_launcher.stop_reviewer') as stop:
            result = self.reader.call('open_browser_export', {'record_id': '1', 'through': 1, 'rule': RULE_VERSION})
        stop.assert_not_called()
        self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
            'status': 409, 'body': {'error': 'reviewer_build_changed'}}}})

    def test_browser_export_service_failures_and_untrusted_urls_are_not_returned(self):
        args = {'record_id': '1', 'through': 1, 'rule': RULE_VERSION}
        for error in (NotesError('PRIVATE-SENTINEL'), OSError('PRIVATE-SENTINEL')):
            with self.subTest(error=type(error).__name__), patch('tas_notes_mcp.ensure_reviewer', side_effect=error):
                result = self.reader.call('open_browser_export', args)
                self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
                    'status': 503, 'body': {'error': 'reviewer_unavailable'}}}})
        for value in [None, {'pid': 123, 'url': 'https://example.com/#token=' + 'a' * 43},
                      {'pid': 123, 'url': 'http://localhost:22266/#token=' + 'a' * 43},
                      {'pid': 123, 'url': 'http://127.0.0.1:0/#token=' + 'a' * 43},
                      {'pid': 123, 'url': 'http://127.0.0.1:22266/private#token=' + 'a' * 43},
                      {'pid': 123, 'url': 'http://127.0.0.1:22266/#token=' + 'a' * 42},
                      {'pid': True, 'url': 'http://127.0.0.1:22266/#token=' + 'a' * 43}]:
            with self.subTest(value=value), patch('tas_notes_mcp.ensure_reviewer', return_value=value):
                result = self.reader.call('open_browser_export', args)
                self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
                    'status': 503, 'body': {'error': 'reviewer_unavailable'}}}})

    def test_browser_export_missing_store_does_not_create_or_launch_anything(self):
        reader = Reader(self.root / 'missing'); self.addCleanup(reader.names.close)
        with patch('tas_notes_mcp.ensure_reviewer') as launch:
            result = reader.call('open_browser_export', {'record_id': '1', 'through': 1, 'rule': RULE_VERSION})
        launch.assert_not_called()
        self.assertEqual(result['_meta']['blackbox']['status'], 503)
        self.assertFalse((self.root / 'missing').exists())

    def export_args(self):
        snapshot = detail(self.store, '1', 1)
        body = snapshot['markdown'].encode('utf-8')
        return {'record_id': '1', 'through': 1, 'rule': RULE_VERSION,
                'preview_sha256': hashlib.sha256(body).hexdigest()}, body

    def test_save_export_schema_requires_exact_preview_and_app_only_write_annotation(self):
        descriptor = next(tool for tool in TOOLS if tool['name'] == 'save_export')
        self.assertEqual(descriptor['_meta']['ui']['visibility'], ['app'])
        self.assertFalse(descriptor['annotations']['readOnlyHint'])
        schema = descriptor['inputSchema']
        self.assertEqual(set(schema['properties']), {'record_id', 'through', 'rule', 'preview_sha256'})
        self.assertEqual(set(schema['required']), set(schema['properties']))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['preview_sha256'], {
            'type': 'string', 'minLength': 64, 'maxLength': 64, 'pattern': '^[a-f0-9]{64}$'})

    def test_save_export_writes_exact_verified_preview_without_database_or_selection_changes(self):
        args, body = self.export_args()
        self.reader.call('select_session', {'investigation': self.event['investigation']})
        before = self.store.database.read_bytes()
        preference = self.store.directory / 'reader-selection.json'
        selected = preference.read_bytes()
        destination = self.root / '重定向 下载'; destination.mkdir()
        with patch('tas_notes_mcp.downloads_directory', return_value=destination), patch('tas_notes_mcp.ensure_reviewer') as launch:
            result = self.reader.call('save_export', args)
        launch.assert_not_called()
        self.assertEqual(result['content'], [])
        self.assertNotIn('structuredContent', result)
        self.assertEqual(result['_meta']['blackbox']['status'], 200)
        saved = result['_meta']['blackbox']['body']
        self.assertEqual(saved, {'path': str(destination / 'anomaly-1-through-1.md'),
            'name': 'anomaly-1-through-1.md', 'sha256': args['preview_sha256'], 'bytes': len(body)})
        self.assertEqual(Path(saved['path']).read_bytes(), body)
        self.assertEqual(hashlib.sha256(Path(saved['path']).read_bytes()).hexdigest(), saved['sha256'])
        self.assertEqual(before, self.store.database.read_bytes())
        self.assertEqual(selected, preference.read_bytes())
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(result))

    def test_save_export_never_overwrites_existing_files_and_numbers_repeated_exports(self):
        args, body = self.export_args()
        destination = self.root / 'downloads'; destination.mkdir()
        existing = destination / 'anomaly-1-through-1.md'; existing.write_bytes(b'KEEP ORIGINAL')
        with patch('tas_notes_mcp.downloads_directory', return_value=destination):
            results = [self.reader.call('save_export', args) for _ in range(2)]
        self.assertEqual(existing.read_bytes(), b'KEEP ORIGINAL')
        self.assertEqual([r['_meta']['blackbox']['body']['name'] for r in results],
                         ['anomaly-1-through-1 (1).md', 'anomaly-1-through-1 (2).md'])
        for result in results:
            self.assertEqual(Path(result['_meta']['blackbox']['body']['path']).read_bytes(), body)

    def test_save_export_does_not_follow_an_existing_hard_link(self):
        args, body = self.export_args()
        destination = self.root / 'downloads'; destination.mkdir()
        original = self.root / 'keep.txt'; original.write_bytes(b'KEEP LINK TARGET')
        linked = destination / 'anomaly-1-through-1.md'; os.link(original, linked)
        with patch('tas_notes_mcp.downloads_directory', return_value=destination):
            result = self.reader.call('save_export', args)
        self.assertEqual(original.read_bytes(), b'KEEP LINK TARGET')
        self.assertEqual(linked.read_bytes(), b'KEEP LINK TARGET')
        self.assertEqual(result['_meta']['blackbox']['body']['name'], 'anomaly-1-through-1 (1).md')
        self.assertEqual(Path(result['_meta']['blackbox']['body']['path']).read_bytes(), body)

    def test_save_export_concurrent_calls_create_distinct_verified_files(self):
        args, body = self.export_args()
        destination = self.root / 'downloads'; destination.mkdir()
        with patch('tas_notes_mcp.downloads_directory', return_value=destination), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.reader.call('save_export', args), range(2)))
        self.assertTrue(all(result['_meta']['blackbox']['status'] == 200 for result in results))
        paths = {result['_meta']['blackbox']['body']['path'] for result in results}
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(Path(path).read_bytes() == body for path in paths))

    def test_save_export_hash_mismatch_never_resolves_directory_or_writes(self):
        args, _ = self.export_args(); args['preview_sha256'] = '0' * 64
        before = self.store.database.read_bytes()
        with patch('tas_notes_mcp.downloads_directory') as resolve, patch('tas_notes_mcp.save_export_file') as save:
            result = self.reader.call('save_export', args)
        resolve.assert_not_called(); save.assert_not_called()
        self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
            'status': 409, 'body': {'error': 'preview_mismatch'}}}})
        self.assertEqual(before, self.store.database.read_bytes())

    def test_save_export_rejects_invalid_arguments_and_snapshots_before_resolving_destination(self):
        valid, _ = self.export_args()
        invalid = [{name: value for name, value in valid.items() if name != key} for key in valid]
        for key in ('path', 'directory', 'state_dir', 'url', 'command', 'markdown'):
            invalid.append(dict(valid, **{key: 'PRIVATE-SENTINEL'}))
        for value in (None, True, 1, '', 'x' * 64, 'A' * 64, 'a' * 63, 'a' * 65):
            invalid.append(dict(valid, preview_sha256=value))
        for value in (None, True, 1, '', '../1', '1' * 65, '999'):
            invalid.append(dict(valid, record_id=value))
        for value in (None, True, '1', 1.0, 0, -1, 2, 2**63):
            invalid.append(dict(valid, through=value))
        for value in (None, True, '', 'old-rule', 'a' * 129):
            invalid.append(dict(valid, rule=value))
        before = self.store.database.read_bytes()
        with patch('tas_notes_mcp.downloads_directory') as resolve, patch('tas_notes_mcp.save_export_file') as save:
            for args in invalid:
                with self.subTest(args=args):
                    result = self.reader.call('save_export', args)
                    self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
                        'status': 400, 'body': {'error': 'invalid_request'}}}})
        resolve.assert_not_called(); save.assert_not_called()
        self.assertEqual(before, self.store.database.read_bytes())

    def test_save_export_reports_write_and_readback_failures_without_false_success(self):
        args, _ = self.export_args()
        destination = self.root / 'downloads'; destination.mkdir()
        for failure in (OSError('PRIVATE-SENTINEL'), lambda fd: os.write(fd, b'CORRUPT')):
            with self.subTest(failure=type(failure).__name__), \
                    patch('tas_notes_mcp.downloads_directory', return_value=destination), \
                    patch('tas_notes_mcp.os.fsync', side_effect=failure):
                result = self.reader.call('save_export', args)
                self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
                    'status': 503, 'body': {'error': 'export_save_failed'}}}})
        self.assertEqual(len(list(destination.iterdir())), 2)

    def test_save_export_exhausts_bounded_names_without_modifying_existing_files(self):
        args, _ = self.export_args()
        destination = self.root / 'downloads'; destination.mkdir()
        for suffix in ('', ' (1)', ' (2)'):
            (destination / ('anomaly-1-through-1' + suffix + '.md')).write_bytes(b'KEEP')
        with patch('tas_notes_mcp.downloads_directory', return_value=destination), patch('tas_notes_mcp.MAX_EXPORT_COPIES', 3):
            result = self.reader.call('save_export', args)
        self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
            'status': 503, 'body': {'error': 'export_name_exhausted'}}}})
        self.assertEqual(len(list(destination.iterdir())), 3)
        self.assertTrue(all(path.read_bytes() == b'KEEP' for path in destination.iterdir()))

    def mock_known_folder(self, value, status=0, drive_type=3):
        buffer = ctypes.create_unicode_buffer(value) if value is not None else None
        def get(identifier, flags, token, output):
            self.assertEqual(ctypes.string_at(identifier, 16), uuid.UUID('374DE290-123F-4565-9164-39C4925E467B').bytes_le)
            self.assertEqual(flags, 0); self.assertIsNone(token)
            if buffer is not None:
                ctypes.cast(output, ctypes.POINTER(ctypes.c_wchar_p))[0] = ctypes.cast(buffer, ctypes.c_wchar_p)
            return status
        shell = SimpleNamespace(SHGetKnownFolderPath=Mock(side_effect=get))
        ole = SimpleNamespace(CoTaskMemFree=Mock())
        kernel = SimpleNamespace(GetDriveTypeW=Mock(return_value=drive_type))
        def dll(name, **kwargs):
            self.assertTrue(kwargs['use_last_error'])
            return {'shell32': shell, 'ole32': ole, 'kernel32': kernel}[name]
        return dll, shell, ole

    def test_downloads_resolver_uses_redirected_windows_known_folder_and_frees_memory(self):
        redirected = self.root / '另一个盘的 下载'; redirected.mkdir()
        dll, shell, ole = self.mock_known_folder(str(redirected))
        with patch('tas_notes_mcp.sys.platform', 'win32'), patch('tas_notes_mcp.ctypes.WinDLL', side_effect=dll, create=True):
            result = downloads_directory()
        self.assertEqual(result, redirected.resolve())
        shell.SHGetKnownFolderPath.assert_called_once(); ole.CoTaskMemFree.assert_called_once()

    def test_downloads_resolver_has_no_profile_fallback_on_unsupported_or_failed_resolution(self):
        with patch('tas_notes_mcp.sys.platform', 'linux'), patch('tas_notes_mcp.ctypes.WinDLL', create=True) as dll:
            with self.assertRaises(NotesError) as raised:
                downloads_directory()
            self.assertEqual(raised.exception.public_code, 'downloads_unavailable'); dll.assert_not_called()
        for value, status in [(None, -1), (None, 0), ('', 0), ('relative-downloads', 0), (str(self.root / 'missing'), 0)]:
            with self.subTest(value=value, status=status):
                dll, _, ole = self.mock_known_folder(value, status)
                with patch('tas_notes_mcp.sys.platform', 'win32'), patch('tas_notes_mcp.ctypes.WinDLL', side_effect=dll, create=True):
                    with self.assertRaises(NotesError) as raised:
                        downloads_directory()
                    self.assertEqual(raised.exception.public_code, 'downloads_unavailable')
                self.assertEqual(ole.CoTaskMemFree.call_count, 0 if value is None else 1)
        args, _ = self.export_args()
        with patch('tas_notes_mcp.sys.platform', 'linux'):
            result = self.reader.call('save_export', args)
        self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
            'status': 503, 'body': {'error': 'downloads_unavailable'}}}})

    def test_downloads_resolver_rejects_unc_remote_and_unknown_drives_before_saving(self):
        args, _ = self.export_args()
        before = self.store.database.read_bytes()
        cases = [(r'\\server\share\downloads', 3), (r'\\?\UNC\server\share\downloads', 3)]
        cases += [(str(self.root), drive_type) for drive_type in (0, 1, 4, 5, 99)]
        for value, drive_type in cases:
            with self.subTest(value=value, drive_type=drive_type):
                dll, _, ole = self.mock_known_folder(value, drive_type=drive_type)
                with patch('tas_notes_mcp.sys.platform', 'win32'), \
                        patch('tas_notes_mcp.ctypes.WinDLL', side_effect=dll, create=True), \
                        patch('tas_notes_mcp.resolve_state_directory', return_value=self.store.directory), \
                        patch('tas_notes_mcp.save_export_file') as save:
                    result = self.reader.call('save_export', args)
                save.assert_not_called(); ole.CoTaskMemFree.assert_called_once()
                self.assertEqual(result, {'content': [], '_meta': {'blackbox': {
                    'status': 503, 'body': {'error': 'downloads_unavailable'}}}})
        self.assertEqual(before, self.store.database.read_bytes())

    def test_downloads_resolver_accepts_only_local_drive_types_and_rechecks_resolved_target(self):
        for drive_type in (2, 3, 6):
            with self.subTest(drive_type=drive_type):
                dll, _, _ = self.mock_known_folder(str(self.root), drive_type=drive_type)
                with patch('tas_notes_mcp.sys.platform', 'win32'), patch('tas_notes_mcp.ctypes.WinDLL', side_effect=dll, create=True):
                    self.assertEqual(downloads_directory(), self.root.resolve())
        dll, _, ole = self.mock_known_folder(str(self.root))
        with patch('tas_notes_mcp.sys.platform', 'win32'), \
                patch('tas_notes_mcp.ctypes.WinDLL', side_effect=dll, create=True), \
                patch('tas_notes_mcp.Path.resolve', return_value=Path(r'\\server\share\downloads')):
            with self.assertRaises(NotesError) as raised:
                downloads_directory()
            self.assertEqual(raised.exception.public_code, 'downloads_unavailable')
        ole.CoTaskMemFree.assert_called_once()

    def test_published_resource_references_survive_reader_update(self):
        with patch('tas_notes_mcp.Store', side_effect=AssertionError('must not access DB')):
            current = self.reader.dispatch('resources/read', {'uri': URI})['contents'][0]['text']
            for uri in ['ui://blackbox/reader-v1.html', 'ui://blackbox/reader-v2.html', 'ui://blackbox/reader-v3.html', 'ui://blackbox/reader-v21.html', URI]:
                with self.subTest(uri=uri):
                    result = self.reader.handle({'jsonrpc':'2.0','id':1,'method':'resources/read','params':{'uri':uri}})
                    resource = result['result']['contents'][0]
                    self.assertEqual(resource['uri'], uri)
                    self.assertEqual(resource['text'], current)
                    self.assertIn('code-copy', resource['text'])
                    self.assertNotIn('line-reveal', resource['text'])

    def test_resource_compatibility_is_an_exact_allowlist(self):
        for uri in [None, [], {}, 'ui://blackbox/reader-v99.html', URI+'?x=1',
                    'ui://other/reader-v3.html', 'ui://blackbox/../reader-v3.html']:
            with self.subTest(uri=uri):
                result = self.reader.handle({'jsonrpc':'2.0','id':1,'method':'resources/read','params':{'uri':uri}})
                self.assertEqual(result['error'], {'code':-32602,'message':'Invalid params'})

    def test_protocol_invalid_requests_are_bounded_generic(self):
        self.assertIsNone(self.reader.handle({'jsonrpc':'2.0','method':'notifications/initialized'}))
        for message in [[],{'jsonrpc':'2.0','id':1,'method':'resources/read','params':{'uri':'PRIVATE-SENTINEL'}},
                        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':[]}]:
            reply=self.reader.handle(message)
            self.assertIn('error',reply)
            self.assertNotIn('PRIVATE-SENTINEL',json.dumps(reply))

if __name__=='__main__': unittest.main()
