import unittest
from test_curl_errors import CMD,event
import tas_notes_anomalies as a
from tas_notes_transport_errors import CERT_HELP

CASES=[('curl: (35) schannel: next InitializeSecurityContext failed: SEC_E_INVALID_TOKEN (0x80090308) - bad token','curl-tls'),
 ('curl: (60) schannel: SEC_E_UNTRUSTED_ROOT (0x80090325) - untrusted root','curl-certificate'),
 ('curl: (18) end of response with 100 bytes missing','curl-partial'),
 ('curl: (56) Recv failure: Connection was reset','curl-receive'),
 ('curl: (47) Maximum (2) redirects followed','curl-redirect-limit'),
 ('curl: (52) Empty reply from server','curl-empty-reply')]
COMMAND=CMD.replace('http://','https://').replace(' -i',' -o NUL --stderr - -L --max-redirs 2')

class TransportErrors(unittest.TestCase):
 def test_six_diagnostics_keep_raw_evidence(self):
  for raw,rule in CASES:
   for end in ('','\n','\r\n'):
    hit=a.detect(event(raw+end,COMMAND));self.assertEqual(hit['rule'],rule)
    self.assertEqual(hit['evidence'],raw+end);self.assertFalse(hit['signal']['independently_verified'])
 def test_certificate_help_is_exact_optional_suffix(self):
  raw=CASES[1][0]+'\n'+CERT_HELP
  self.assertEqual(a.detect(event(raw,COMMAND))['rule'],'curl-certificate')
  self.assertIsNone(a.detect(event(raw+'\nextra text',COMMAND)))
  self.assertIsNone(a.detect(event(raw.replace('More details here','custom message'),COMMAND)))
 def test_no_body_keyword_scan_or_empty_inference(self):
  for raw,_ in CASES:
   for value in ('HTTP/1.1 200 OK\r\n\r\n'+raw, 'log\n'+raw,raw+'\n'+raw,'cHTTP/1.1 200\nurl: '+raw):
    self.assertIsNone(a.detect(event(value,COMMAND.replace('-o NUL','-i'))))
  self.assertIsNone(a.detect(event('',COMMAND)))
 def test_identity_flags_and_capture_boundaries(self):
  for raw,_ in CASES:
   for command in ('Get-Content old.log','Write-Output example',COMMAND.replace(' -q',''),COMMAND+' --retry 2'):
    self.assertIsNone(a.detect(event(raw,command)))
   for field in ('tool_input','tool_response'):
    e=event(raw,COMMAND);e['_coverage']={'truncated_fields':[field]};self.assertIsNone(a.detect(e))
   e=event(raw,COMMAND);e['tool_name']='mcp__test__http';self.assertIsNone(a.detect(e))
  self.assertIsNone(a.detect(event(CASES[0][0],COMMAND.replace('https://','http://'))))
  self.assertIsNone(a.detect(event(CASES[4][0],COMMAND.replace(' -L',''))))
 def test_code_and_message_must_agree(self):
  for raw,_ in CASES:
   self.assertIsNone(a.detect(event(raw.replace('curl: (','curl: (9'),COMMAND)))
  self.assertIsNone(a.detect(event('curl: (56) arbitrary error',COMMAND)))
