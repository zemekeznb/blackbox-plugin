import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store, NotesError
from tas_notes_mcp import Reader, TOOLS
from tas_notes_web import make_server


class ProjectManagementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.project = self.root / 'project'; self.project.mkdir()
        self.store = Store(self.root / 'state'); self.store.set_project(str(self.project), True)
        self.path = self.store.status()['projects'][0]['path']
        self.args = dict(path=self.path, enabled=False, expected_enabled=True, accept_capture_scope=False)
        self.store.record(dict(cwd=self.path, session_id='test', hook_event_name='UserPromptSubmit', prompt='retained'))

    def test_toggle_preserves_events_and_other_projects(self):
        other = self.root / 'other'; other.mkdir(); self.store.set_project(str(other), True)
        with self.store.connect() as db: before = [tuple(r) for r in db.execute('SELECT * FROM events')]
        self.assertFalse(self.store.manage_project(self.args)['enabled'])
        self.assertTrue(self.store.manage_project(dict(self.args, enabled=True, expected_enabled=False, accept_capture_scope=True))['enabled'])
        with self.store.connect() as db:
            self.assertEqual(before, [tuple(r) for r in db.execute('SELECT * FROM events')])
            self.assertTrue(all(r[0] for r in db.execute('SELECT enabled FROM projects')))

    def test_unknown_stale_and_malformed_requests_do_not_write(self):
        before = self.store.database.read_bytes()
        bad = [None, {}, dict(self.args, enabled='false'), dict(self.args, expected_enabled=False),
               dict(self.args, path=str(self.root / 'unknown')), dict(self.args, extra=True),
               dict(self.args, enabled=True, accept_capture_scope=False)]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(NotesError): self.store.manage_project(value)
        self.assertEqual(before, self.store.database.read_bytes())

    def test_reenable_requires_existing_path_and_scope_consent(self):
        self.store.manage_project(self.args)
        value = dict(self.args, enabled=True, expected_enabled=False)
        with self.assertRaises(NotesError): self.store.manage_project(value)
        self.project.rmdir()
        with self.assertRaises(NotesError): self.store.manage_project(dict(value, accept_capture_scope=True))
        self.assertFalse(self.store.status()['projects'][0]['enabled'])

    def test_mcp_app_only_route_and_private_response(self):
        reader = Reader(self.store.directory)
        self.addCleanup(reader.names.close); self.addCleanup(reader.projects.close)
        tool = next(t for t in TOOLS if t['name'] == 'manage_project')
        self.assertEqual(tool['_meta']['ui']['visibility'], ['app'])
        self.assertFalse(tool['annotations']['readOnlyHint'])
        result = reader.call('manage_project', self.args)
        self.assertEqual(result['content'], [])
        self.assertEqual(result['_meta']['blackbox']['status'], 200)
        self.assertEqual(reader.call('manage_project', self.args)['_meta']['blackbox']['status'], 400)

    def test_http_requires_auth_origin_and_valid_confirmation(self):
        server, token = make_server(self.store, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            def post(value, auth=True, origin=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                headers = {'Content-Type': 'application/json'}
                if auth: headers['Authorization'] = 'Bearer ' + token
                if origin: headers['Origin'] = origin
                connection.request('POST', '/api/project-management', json.dumps(value), headers)
                response = connection.getresponse(); status = response.status; response.read(); connection.close()
                return status
            self.assertEqual(post(self.args, auth=False), 401)
            self.assertEqual(post(self.args, origin='https://example.com'), 403)
            self.assertEqual(post(dict(self.args, enabled='false')), 400)
            self.assertEqual(post(self.args), 200)
            self.assertEqual(post(self.args), 400)
        finally:
            server.shutdown(); thread.join(); server.server_close()
