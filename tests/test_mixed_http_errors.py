"""Decoded body bytes are not a reliable curl stderr boundary."""
import unittest
from test_curl_errors import CMD, event
from test_transport_errors import CASES, CERT_HELP
import tas_notes_anomalies as a

PARTIAL = 'curl: (18) end of response with 100 bytes missing'
TIMEOUT = 'curl: (28) Operation timed out after 300 milliseconds with 0 bytes received'
REDIRECT = 'HTTP/1.1 302 Found\r\nLocation: /next\r\nContent-Length: 10\r\n\r\n'
INTERIM = 'HTTP/1.1 103 Early Hints\r\nLink: </x>\r\n\r\n'
FINAL = 'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n'
COMMAND = CMD.replace('http://', 'https://').replace(' -i', ' -i --stderr - -L')


class MixedHttpErrors(unittest.TestCase):
    def check(self, raw, rule=None, command=COMMAND):
        hit = a.detect(event(raw, command))
        self.assertEqual(hit['rule'] if hit else None, rule, raw)
        if hit:
            self.assertEqual(hit['evidence'], raw)
            self.assertFalse(hit['signal']['independently_verified'])
        return hit

    def test_stderr_before_http_keeps_partial_body(self):
        for raw, rule in CASES:
            self.check(raw+'\n'+FINAL+'partial body', rule)
        self.check(CASES[1][0]+'\n'+CERT_HELP+'\n'+REDIRECT, 'curl-certificate')
        self.check(PARTIAL+'\n'+INTERIM+FINAL+'part', 'curl-partial')

    def test_interrupted_bodyless_chains(self):
        for chain in (INTERIM, REDIRECT, INTERIM+REDIRECT+INTERIM):
            self.check(chain+TIMEOUT+'\n', 'curl-timeout')
            self.check(chain+CASES[4][0]+'\n', 'curl-redirect-limit')
        self.check(REDIRECT*32+CASES[4][0], 'curl-redirect-limit')
        self.check(REDIRECT*33+CASES[4][0])
        self.check(REDIRECT+'curl: (7) Failed to connect to 127.0.0.1 port 1 after 0 ms: Could not connect to server', 'curl-connect')

    def test_no_entity_suffix_even_with_declared_length(self):
        for data in (PARTIAL, 'part'+PARTIAL, 'part\n'+PARTIAL, 'part\r\n'+PARTIAL):
            for length in ('0', '4', str(len(data)), str(len(data)+100)):
                self.check(FINAL.replace('100',length)+data)
        self.check(REDIRECT+FINAL+TIMEOUT)
        self.check(FINAL.replace('Content-Length: 100','Transfer-Encoding: chunked')+PARTIAL)
        self.check(FINAL.replace('Content-Length: 100\r\n','')+PARTIAL)

    def test_invalid_headers_chains_and_interleaving(self):
        for raw in ('c'+REDIRECT+'url: (47) Maximum (2) redirects followed',
                    REDIRECT+'discarded body\n'+TIMEOUT,
                    INTERIM.replace('103','101')+TIMEOUT,
                    INTERIM.replace('Link: </x>','Content-Length: 0')+TIMEOUT,
                    REDIRECT.replace('Location: /next\r\n','')+TIMEOUT,
                    REDIRECT.replace('Content-Length: 10','Content-Length: invalid')+TIMEOUT,
                    REDIRECT.replace('Content-Length: 10','Content-Length: 0\r\nContent-Length: 0')+TIMEOUT,
                    PARTIAL+'\nHTTP/1.1 200 OK\nmalformed\n\npart',
                    PARTIAL+'\n'+FINAL+TIMEOUT,
                    PARTIAL+'\n'+FINAL+FINAL,
                    'log\n'+PARTIAL+'\n'+FINAL):
            self.check(raw)

    def test_command_capture_and_proxy_boundaries(self):
        raw=REDIRECT+TIMEOUT
        for cmd in (COMMAND.replace(' -L',''), 'Get-Content old.log',
                    COMMAND.replace(' -i',' -o NUL'),COMMAND+' --retry 1'):
            self.check(raw,command=cmd)
        for field in ('tool_input','tool_response'):
            e=event(raw,COMMAND);e['_coverage']={'truncated_fields':[field]}
            self.assertIsNone(a.detect(e))
        tunnel='HTTP/1.1 200 Connection established\r\n\r\n'
        self.check(tunnel+TIMEOUT)
        proxy=COMMAND.replace(' -i',' -i --proxy http://127.0.0.1:8888 --proxytunnel')
        self.check(tunnel+TIMEOUT,'curl-timeout',proxy)
        self.check(tunnel+tunnel+TIMEOUT,command=proxy)
        self.check(REDIRECT+tunnel+TIMEOUT,command=proxy)

    def test_successful_body_can_imitate_whole_mixed_output(self):
        for fake in (PARTIAL+'\n'+FINAL, INTERIM+TIMEOUT, REDIRECT+TIMEOUT):
            self.check(FINAL+fake)
        self.check(FINAL+'normal text')
        self.check(INTERIM+FINAL+'normal text')
