"""Synthetic chain boundaries, separate from real desktop acceptance."""
import unittest
from test_curl_errors import CMD, event
from test_http_errors import response
from test_business_errors import event as business_event
import tas_notes_anomalies as a

DIAG = 'curl: (22) The requested URL returned error: 502'
REDIRECT = 'HTTP/1.1 302 Found\r\nLocation: /final\r\nContent-Length: 99\r\n\r\n'
INTERIM = 'HTTP/1.1 100 Continue\r\n\r\n'
CONNECT = 'HTTP/1.1 200 Connection established\r\n\r\n'


def command(options):
    return CMD.replace(' -i', ' -i '+options)


class HttpChains(unittest.TestCase):
    def check(self, raw, options='', rule='http-status-error'):
        hit=a.detect(event(raw,command(options)))
        self.assertEqual(hit['rule'] if hit else None,rule,(raw,options))
        if hit:self.assertEqual(hit['evidence'],raw)
        return hit

    def test_fail_headers_and_stderr_orders(self):
        for flag in ('-f','--fail','--fail-with-body'):
            for raw in (DIAG+'\n'+response(body=''), response(body='body\n')+DIAG+'\n'):
                self.check(raw,flag)
        self.check(DIAG,'--fail','curl-http-fail')
        self.check(DIAG,'--fail-with-body',None)
        self.check(response()+ '\n'+DIAG,'',None)
        self.check(response(500)+'\n'+DIAG,'--fail',None)
        self.check(response(200)+'\n'+DIAG,'--fail-with-body',None)

    def test_single_stream_and_interleaving_boundary(self):
        self.check(response(body='body\n')+DIAG,'--stderr - --fail-with-body')
        self.check(response(body='body\n')+DIAG,'--stderr other.log --fail-with-body',None)
        self.check('c'+response(body='')+'url: (22) The requested URL returned error: 502','--fail',None)
        self.check(response(body='curl: (22)\nThe requested URL returned error: 502'),'--fail-with-body',None)

    def test_network_diagnostic_with_supported_extended_options(self):
        self.check('curl: (6) Could not resolve host: test.invalid',
                   '--stderr - -L --fail-with-body', 'curl-dns')
        self.check('curl: (28) Connection timed out after 200 milliseconds',
                   '--stderr - --fail', 'curl-timeout')
        self.check(response(200,'curl: (6) Could not resolve host: test.invalid'),
                   '--stderr - -L --fail-with-body', None)

    def test_fail_discard_and_conflicting_commands(self):
        hit=a.detect(event(DIAG,CMD.replace('-i','-o NUL --fail-with-body')))
        self.assertEqual(hit['rule'],'curl-http-fail')
        for flags in ('--fail --fail-with-body','--fail --retry 2','--fail --next',
                      '-L --location','-L --max-redirs -1','--proxy http://u:p@host:80',
                      '--proxytunnel','--proxy socks5://host:80','-L --config x'):
            self.check(response(),flags,None)

    def test_redirects_only_final_status(self):
        for status in (301,302,303,307,308):
            redirect=REDIRECT.replace('302',str(status))
            self.check(redirect+response(),'-L')
            self.check(redirect+response(200),'-L',None)
            self.check(redirect+response(),' ',None)
        self.check(REDIRECT*3+response(),'-L --max-redirs 5')
        self.check(REDIRECT*33+response(),'-L',None)
        self.check(REDIRECT,'-L',None)
        self.check(REDIRECT.replace('Location: /final\r\n','')+response(),'-L',None)
        self.check(REDIRECT+'body\n'+response(),'-L',None)

    def test_interim_and_switch_boundaries(self):
        for code in (100,102,103):
            self.check(INTERIM.replace('100',str(code))+response())
            self.check(INTERIM.replace('100',str(code))+response(200),rule=None)
        self.check(INTERIM,rule=None)
        self.check(INTERIM.replace('100','101')+response(),rule=None)
        self.check(INTERIM.replace('\r\n\r\n','\r\nContent-Length: 0\r\n\r\n')+response(),rule=None)
        self.check(INTERIM+REDIRECT+INTERIM+response(),'-L')

    def test_explicit_proxy_tunnel(self):
        flags='--proxy http://127.0.0.1:8888 --proxytunnel'
        self.check(CONNECT+response(),flags)
        self.check(CONNECT+INTERIM+REDIRECT+response(),flags+' -L')
        self.check(CONNECT+response(200),flags,None)
        self.check(CONNECT,flags,None)
        self.check(CONNECT+response(),'',None)
        self.check(CONNECT+CONNECT+response(),flags,None)
        self.check(response(407),flags)

    def test_body_spoof_mixed_and_truncated(self):
        for raw in (response(200,REDIRECT+response()),response(200,INTERIM+response()),
                    response()+ '\n'+response(500), 'log\n'+response(),
                    DIAG+'\n'+response()+'\n'+DIAG):
            self.check(raw,'-L --fail-with-body',None)
        for field in ('tool_input','tool_response'):
            e=event(INTERIM+response());e['_coverage']={'truncated_fields':[field]}
            self.assertIsNone(a.detect(e))

    def test_extended_protocol_precedence(self):
        direct=business_event();direct['tool_input']['command']+=' --location'
        self.assertEqual(a.detect(direct)['rule'],'json-rpc-error')
        e=business_event();e['tool_input']['command']=e['tool_input']['command'].replace(' -i',' -i --fail-with-body')
        e['tool_response']=INTERIM+e['tool_response'].replace('200 OK','500 Error')
        e['tool_response']+='\n'+DIAG.replace('502','500')+'\n'
        self.assertEqual(a.detect(e)['rule'],'json-rpc-error')
        e['tool_input']['command']=e['tool_input']['command'].replace(' --fail-with-body',' --fail-with-body -L')
        e['tool_response']=REDIRECT+e['tool_response']
        self.assertEqual(a.detect(e)['rule'],'http-status-error')
