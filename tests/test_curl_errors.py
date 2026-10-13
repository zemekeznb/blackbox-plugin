"""Synthetic parser cases; actual desktop evidence is a separate acceptance."""
import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
import tas_notes_anomalies as a
from tas_notes_core import Store
from tas_notes_mcp import Reader

CMD = "& 'C:/Windows/system32/curl.exe' -q -sS -i --max-time 2 --noproxy '*' http://127.0.0.1:9/; exit $LASTEXITCODE"
ERROR = 'curl: (7) Failed to connect to 127.0.0.1:9 after 0 ms: Could not connect to server\r\n'


def event(output=ERROR, command=CMD):
    return {'tool_name': 'Bash', 'tool_input': {'command': command}, 'tool_response': output}


class CurlErrors(unittest.TestCase):
    def test_known_formats_preserve_evidence(self):
        for output, rule in [(ERROR, 'curl-connect'), ('curl: (6) Could not resolve host: test.invalid', 'curl-dns'),
            ('curl: (28) Connection timed out after 200 milliseconds', 'curl-timeout'),
            ('curl: (28) Operation timed out after 300 milliseconds with 1 out of 100 bytes received', 'curl-timeout'),
            ('curl: (28) Operation timed out after 300 milliseconds with 0 bytes received', 'curl-timeout'),
            ('curl: (28) Failed to connect to 127.0.0.1 port 9 after 200 ms: Timeout was reached', 'curl-timeout')]:
            hit = a.detect(event(output));self.assertEqual(hit['rule'], rule)
            self.assertEqual(hit['evidence'], output)
            self.assertFalse(hit['signal']['independently_verified'])

    def test_identity_and_output_contract(self):
        for command in ['Get-Content old.log', "Write-Output '"+ERROR.strip()+"'", 'cmd /c '+CMD,
                        CMD.replace(' -q', ''), CMD.replace(' -i', ''), CMD.replace(' -i', ' -o data.txt'),
                        CMD.replace(' -i', ' -w fake -i'), CMD.replace(' -i', ' -K config -i'),
                        CMD.replace(' -i', ' --next -i'), CMD+' | Out-String', CMD.replace('curl.exe','curl')]:
            self.assertIsNone(a.detect(event(command=command)), command)
        self.assertIsNotNone(a.detect(event(command=CMD.replace(' -i', ' -o NUL'))))

    def test_body_spoof_and_unknown_diagnostics_rejected(self):
        for output in ['HTTP/1.1 200 OK\r\n\r\n'+ERROR, 'ordinary log\n'+ERROR,
                       ERROR+'extra', ERROR+ERROR, ERROR.replace('(7)', '(6)'),
                       'curl: (7) ordinary error', 'curl: (28) example', '', '{"error":"'+ERROR.strip()+'"}']:
            self.assertIsNone(a.detect(event(output)))

    def test_truncated_redacted_and_non_bash(self):
        for field in ('tool_input', 'tool_response'):
            body=event();body['_coverage']={'truncated_fields':[field]}
            self.assertIsNone(a.detect(body))
        body=event();body['tool_name']='mcp__test__curl';self.assertIsNone(a.detect(body))
        self.assertIsNone(a.detect(event(command=CMD.replace('127.0.0.1', '[REDACTED]'))))
        body=event();body['tool_response']={'truncated':True,'original_length':50000,'preview':ERROR,'tail':ERROR}
        self.assertIsNone(a.detect(body))

    def test_old_post_shape_still_recognized(self):
        command=CMD.replace(' http:', " -H 'Content-Type: application/json' --data-raw '{}' http:")
        self.assertEqual(a.detect(event(command=command))['rule'], 'curl-connect')

    def test_snapshot_exports_and_no_source_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=root/'project';project.mkdir();store=Store(root/'state')
            store.set_project(str(project),True)
            store.record(dict(event(),cwd=str(project),session_id='synthetic',turn_id='unit',hook_event_name='PostToolUse',tool_use_id='1'))
            with store.connect() as db:saved=[tuple(r) for r in db.execute('SELECT * FROM events')]
            page=a.listing(store);self.assertEqual(len(page['items']),1)
            reader=Reader(root/'state')
            try:
                seq=page['items'][0]['id'];through=page['snapshot_seq'];detail=a.detail(store,seq,through)
                path=f'/api/anomaly-export/{seq}?through={through}&rule={a.RULE_VERSION}'
                self.assertEqual(reader.call('read_view',{'path':path})['_meta']['blackbox']['body'],detail['markdown'])
                self.assertEqual(reader.call('read_view',{'path':path.replace(a.RULE_VERSION,'observed-errors-7')})['_meta']['blackbox']['status'],400)
            finally:reader.names.close();reader.projects.close()
            with store.connect() as db:self.assertEqual(saved,[tuple(r) for r in db.execute('SELECT * FROM events')])
