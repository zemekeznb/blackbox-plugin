import unittest
import json
from test_business_errors import command, GQL
from test_curl_errors import event
import blackbox_anomalies as a

def stream(result, ending='event: complete\ndata: \n\n'):
    return ('HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n'
            'event: next\ndata: '+json.dumps(result)+'\n\n'+ending)

CMD=command(GQL).replace(' -i ', " -i -H 'Accept: text/event-stream' ")
ERR={'errors':[{'message':'NOTES062 invalid field'}]}

class BatchCoverage(unittest.TestCase):
    def test_sse_request_and_execution_errors(self):
        for result in (ERR, {'data':None,'errors':[{'message':'failed','path':['item']}]},
                       {'data':{'item':None},'errors':[{'message':'failed','path':['item']}] }):
            for ending in ('\n','\r\n'):
                raw=stream(result).replace('\r\n','\n').replace('\n',ending)
                hit=a.detect(event(raw,CMD));self.assertEqual(hit['rule'],'graphql-sse-errors')
                self.assertEqual(hit['evidence'],raw)

    def test_stream_sequence_is_not_guessed(self):
        raw=stream(ERR)
        variants=[stream(ERR,''),raw+'event: complete\n\n',raw+'event: next\ndata: {}\n\n',
                  raw.replace('event: next','event: error'),raw.replace('event: next','event: next\nevent: next'),
                  raw.replace('event: next','id: 1\nevent: next'),raw.replace('text/event-stream','application/json'),
                  raw.replace('200 OK','201 Created'),raw.rstrip('\n'),
                  stream({'id':'1','payload':ERR}),stream({'hasNext':False,**ERR}),
                  stream({'errors':[]}),stream({'data':{},'errors':[{'message':'bad'}]}),
                  stream({'data':{}}),stream({'data':None}),stream({'code':123,'success':False})]
        for value in variants:self.assertIsNone(a.detect(event(value,CMD)),value)

    def test_sse_identity_truncation_and_duplicate_json(self):
        raw=stream(ERR)
        for cmd in (CMD.replace('text/event-stream','*/*'),'Get-Content C:/log.txt',CMD+' | more'):
            self.assertIsNone(a.detect(event(raw,cmd)))
        e=event(raw,CMD);e['_coverage']={'truncated_fields':['tool_response']};self.assertIsNone(a.detect(e))
        e=event(raw,CMD);e['_coverage']={'truncated_fields':['tool_input']};self.assertIsNone(a.detect(e))
        self.assertIsNone(a.detect(event(raw.replace('"errors":','"errors":[],"errors":'),CMD)))

    def test_multiline_comments_success_siblings_and_limit(self):
        raw=stream(ERR).replace('event: next',': keepalive\n\nevent: next').replace('data: {','data: {\ndata: ')
        self.assertIsNotNone(a.detect(event(raw,CMD)))
        raw=stream(ERR).replace('event: complete','event: next\ndata: {"data":{"ok":true}}\n\nevent: complete')
        self.assertIsNotNone(a.detect(event(raw,CMD)))
        block='event: next\ndata: {"data":{}}\n\n'
        self.assertIsNone(a.detect(event(raw.replace('event: complete',block*101+'event: complete'),CMD)))

    def test_bounded_child_read_native_short_diagnostic(self):
        raw="Get-Content: Cannot find path 'C:\\test\\missing' because it does not exist.\n"
        cmd='& pwsh.exe -NoProfile -NonInteractive -Command "Get-Content -LiteralPath \'C:/test/missing\'"'
        self.assertEqual(a.detect(event(raw,cmd))['rule'],'powershell-diagnostic')
        self.assertIsNone(a.detect(event(raw,cmd.replace('C:/test/missing','C:/test/old.log'))))
        self.assertIsNone(a.detect(event(raw,cmd.replace('-NoProfile ','').replace('C:/test/missing','C:/test/old.log'))))

    def test_logged_current_target_is_still_ambiguous(self):
        raw="Get-Content: Cannot find path 'C:\\test\\log.txt' because it does not exist.\n"
        self.assertIsNotNone(a.detect(event(raw,'Get-Content C:/test/log.txt')))
        # Candidate reads are ambiguous, not proved successful or resolved aliases.
        from test_anomalies import TRACE
        for alias in ('cat','gc','type'):
            self.assertIsNone(a.detect(event(TRACE,alias+' C:/test/log.txt')))

    def test_relative_reads_keep_existing_diagnostic_boundary(self):
        from test_shell_errors import PS
        self.assertEqual(a.detect(event(PS,'Get-Content relative.log'))['rule'],'powershell-diagnostic')

    def test_fixture_partial_data_is_not_transport_fixture(self):
        from verify_batch_coverage import payload
        self.assertIn(b'event: next',payload('partial_data'))
        self.assertIn(b'"errors"',payload('partial_data'))
        self.assertNotIn(b'curl:',payload('partial_data'))
