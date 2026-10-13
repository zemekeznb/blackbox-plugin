"""HTTP status observations, not synthetic desktop acceptance."""
import unittest
import tempfile
from pathlib import Path
from test_curl_errors import CMD, event
import tas_notes_anomalies as a
from test_business_errors import event as business_event, GQL, GQL_ERROR
from tas_notes_core import Store
from tas_notes_mcp import Reader


def response(code=502, body='<html>Unavailable</html>'):
    return f'HTTP/1.1 {code} Test\r\nContent-Type: text/html\r\n\r\n'+body


class HttpErrors(unittest.TestCase):
    def test_status_range_not_error_words(self):
        for code in (400,401,403,404,408,429,500,502,503,504,499,599):
            for body in ('', 'OK', '{"success":true}', '<script>alert(1)</script>', '错误'):
                raw=response(code,body);hit=a.detect(event(raw))
                self.assertEqual(hit['rule'],'http-status-error');self.assertIn(str(code),hit['title'])
                self.assertEqual(hit['evidence'],raw);self.assertFalse(hit['signal']['independently_verified'])
        for code in (100,200,204,301,302,399,600):self.assertIsNone(a.detect(event(response(code,'error failed HTTP 500'))))

    def test_command_and_capture_boundaries(self):
        raw=response()
        for cmd in ('Get-Content old.log', "Write-Output 'HTTP/1.1 502'", CMD.replace(' -q',''),
                    CMD.replace(' -i',''), CMD.replace(' -i',' -i -o NUL'), CMD.replace(' -i',' -i --retry 2'),
                    CMD.replace(' -i',' -i -w fake'), 'cmd /c '+CMD):
            self.assertIsNone(a.detect(event(raw,cmd)))
        for field in ('tool_response','tool_input'):
            e=event(raw);e['_coverage']={'truncated_fields':[field]};self.assertIsNone(a.detect(e))
        e=event(raw);e['tool_name']='mcp__test__http';self.assertIsNone(a.detect(e))

    def test_malformed_or_multiple_responses(self):
        for raw in ('HTTP/1.1 502 Test', 'HTTP/1.1 502 Test\nHeader: x',
                    'HTTP/1.1 502 Test\ninvalid header\n\nbody', 'log\n'+response(),
                    response()+ '\n'+response(500), response(100)+'\n'+response(500),
                    response(body='curl: (28) Operation timed out after 1 milliseconds with 0 bytes received'),
                    'HTTP/1.1 500\nContent-Length: 3\nContent-Length: 3\n\nabc',
                    'HTTP/1.1 500\nContent-Length: -1\n\nabc',
                    'HTTP/1.1 500\nContent-Length: 3\nTransfer-Encoding: chunked\n\nabc'):
            self.assertIsNone(a.detect(event(raw)),raw)

    def test_versions_headers_and_windows_newlines(self):
        for version in ('1.0','1.1','2','3'):
            raw=f'HTTP/{version} 500\r\nSet-Cookie: one=1\r\nSet-Cookie: two=2\r\n\r\nbody'
            self.assertEqual(a.detect(event(raw))['evidence'],raw)
            raw=raw.replace('\r\n','\r\r\n');self.assertEqual(a.detect(event(raw))['evidence'],raw)

    def test_protocol_specific_errors_keep_precedence(self):
        e=business_event();e['tool_response']=e['tool_response'].replace('200 OK','500 Error')
        self.assertEqual(a.detect(e)['rule'],'json-rpc-error')
        e=business_event(GQL,GQL_ERROR,'application/graphql-response+json');e['tool_response']=e['tool_response'].replace('200 OK','400 Error')
        self.assertEqual(a.detect(e)['rule'],'graphql-errors')
        e=business_event(GQL,GQL_ERROR);e['tool_response']=e['tool_response'].replace('200 OK','500 Error')
        self.assertEqual(a.detect(e)['rule'],'http-status-error')

    def test_fixed_export_and_read_only_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=root/'project';project.mkdir();store=Store(root/'state');store.set_project(str(project),True)
            store.record(dict(event(response()),cwd=str(project),session_id='synthetic',turn_id='unit',hook_event_name='PostToolUse',tool_use_id='1'))
            with store.connect() as db:saved=[tuple(r) for r in db.execute('SELECT * FROM events')]
            p=a.listing(store);self.assertEqual(len(p['items']),1);seq=p['items'][0]['id'];through=p['snapshot_seq']
            reader=Reader(root/'state')
            try:
                d=a.detail(store,seq,through);path=f'/api/anomaly-export/{seq}?through={through}&rule={a.RULE_VERSION}'
                self.assertEqual(reader.call('read_view',{'path':path})['_meta']['blackbox']['body'],d['markdown'])
                self.assertIn(response(),d['markdown'])
                self.assertEqual(reader.call('read_view',{'path':path.replace(a.RULE_VERSION,'observed-errors-8')})['_meta']['blackbox']['status'],400)
            finally:reader.names.close();reader.projects.close()
            with store.connect() as db:self.assertEqual(saved,[tuple(r) for r in db.execute('SELECT * FROM events')])
