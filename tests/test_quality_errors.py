"""Quality regressions; isolated stores never establish real Hook acceptance."""
import unittest
import test_anomalies as fixtures
from test_anomalies import TRACE, PS
from test_shell_errors import PS as PS_EXAMPLE, NODE, GIT
from test_curl_errors import CMD, event
import tas_notes_anomalies as a


class QualityDetection(unittest.TestCase):
    def test_curl_payload_does_not_fall_through_to_shell_rules(self):
        for raw in (TRACE, PS_EXAMPLE, NODE, GIT, PS, TRACE+PS_EXAMPLE):
            for ending in ('\n','\r\n','\r\r\n'):
                data=raw.replace('\n',ending)
                value='HTTP/1.1 200 OK'+ending+'Content-Length: 500'+ending*2+data
                self.assertIsNone(a.detect(event(value,CMD)))
                self.assertIsNone(a.detect(event(value,CMD.replace(' -i',' -i --unsupported'))))
                if raw != PS:
                    self.assertIsNone(a.detect(event(data,CMD.replace(' -i',' -o NUL'))))

    def test_curl_http_status_and_real_launch_failure_survive(self):
        hit=a.detect(event('HTTP/1.1 500 Error\r\n\r\n'+TRACE,CMD))
        self.assertEqual(hit['rule'],'http-status-error')
        self.assertEqual(a.detect(event(PS,CMD))['rule'],'powershell-process-start')
        self.assertEqual(a.detect(event('curl: (52) Empty reply from server',CMD))['rule'],'curl-empty-reply')

    def test_curl_truncated_payload_never_becomes_python_error(self):
        e=event({'truncated':True,'original_length':100000,'preview':'HTTP/1.1 200 OK\n\ncut','tail':'cut\n'+TRACE},CMD)
        e['_coverage']={'truncated_fields':['tool_response']}
        self.assertIsNone(a.detect(e))
        e['tool_input']['command']='python example.py'
        self.assertEqual(a.detect(e)['signal']['segment'],'tail')

    def test_complete_printed_diagnostics_remain_explicit_limit(self):
        for cmd,raw,rule in [('python example.py',TRACE,'python-exception'),
                             ('node example.cjs',NODE,'node-exception')]:
            hit=a.detect(event(raw+'OK\n',cmd))
            self.assertEqual(hit['rule'],rule)
            self.assertFalse(hit['signal']['independently_verified'])
        self.assertIsNone(a.detect(event('WARNING: example\nOK\n','python example.py')))


class QualityStorage(unittest.TestCase):
    # Reuse only the isolated fixture methods, not inherited test discovery.
    setUp=fixtures.AnomalyTests.setUp
    emit=fixtures.AnomalyTests.emit

    def test_one_observation_per_source_and_repeat_boundaries(self):
        first,_,_=self.emit(TRACE+TRACE,turn_id='quality')
        second,_,_=self.emit(TRACE,turn_id='quality')
        third,_,_=self.emit('WARNING\nOK\n',turn_id='quality')
        page=a.listing(self.store)
        self.assertEqual(page['total_count'],2)
        self.assertEqual([x['seq'] for x in a.detail(self.store,first)['events']],[first,second])
        self.assertEqual([x['seq'] for x in a.detail(self.store,second)['events']],[second,third])
        self.assertNotIn('修复成功',a.detail(self.store,second)['markdown'])
