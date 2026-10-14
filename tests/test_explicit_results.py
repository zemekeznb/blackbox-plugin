import unittest
import json
from test_curl_errors import event
from test_anomalies import TRACE
import blackbox_anomalies as a
from blackbox_curl_result import FORMAT, MARKER

URL='http://127.0.0.1:12345/test'
CMD="curl.exe -q -sS --stderr - -i --write-out '"+FORMAT+"' '"+URL+"'"
PREFIX='Microsoft.PowerShell.Utility\\Set-Alias -Name {alias} -Value Microsoft.PowerShell.Management\\Get-Content -Scope Local -Force -ErrorAction Stop; '

def response(code=18, message='transfer incomplete',prefix='HTTP/1.1 200 OK\nContent-Length: 500\n\npartial\n',**changes):
    value=dict(exitcode=code,errormsg=message,url=URL,response_code=200);value.update(changes)
    return prefix+MARKER+json.dumps(value)+'\n'

class ExplicitResults(unittest.TestCase):
    def test_bound_alias_read_and_real_missing(self):
        for alias in ('cat','gc','type'):
            cmd=PREFIX.format(alias=alias)+alias+" -LiteralPath 'C:/test/missing'"
            self.assertIsNone(a.detect(event(TRACE,cmd)))
            raw="Get-Content: Cannot find path 'C:\\test\\missing' because it does not exist.\n"
            self.assertEqual(a.detect(event(raw,cmd))['rule'],'powershell-diagnostic')
            self.assertIsNone(a.detect(event(raw,cmd.replace('C:/test/missing','C:/test/log'))))

    def test_no_unproven_alias_exemption(self):
        cmd=PREFIX.format(alias='cat')+"cat C:/test/log"
        for bad in (cmd.replace('-ErrorAction Stop',''),cmd.replace('Get-Content','other'),
                    cmd+'; python bad.py',cmd.replace('Set-Alias','Set-Item'),
                    cmd.replace('; cat','; gc')):
            self.assertEqual(a.detect(event(TRACE,bad))['rule'],'python-exception')
        e=event(TRACE,cmd);e['_coverage']={'truncated_fields':['tool_input']}
        self.assertEqual(a.detect(e)['rule'],'python-exception')

    def test_transfer_status_is_not_body_diagnostic(self):
        raw=response();hit=a.detect(event(raw,CMD));self.assertEqual(hit['rule'],'curl-transfer-result')
        self.assertEqual(hit['evidence'],raw)
        self.assertIsNone(a.detect(event(response(0,None,prefix=raw),CMD)))
        self.assertIsNone(a.detect(event(raw,'Get-Content C:/old.log')))

    def test_reject_missing_invalid_or_ambiguous_trailer(self):
        for raw in (response(url=URL+'/other'),response(True),response(18,''),response(0,'failed'),
                    response(response_code=True),response()+ 'junk',response().rstrip('\n'),
                    response().replace('"exitcode": 18','"exitcode":0,"exitcode":18'),
                    response().replace('BLACKBOX_CURL_RESULT','OTHER')):
            self.assertIsNone(a.detect(event(raw,CMD)),raw)
        for cmd in (CMD.replace('--stderr -',''),CMD+' --write-out "'+FORMAT+'"',
                    CMD.replace('%{json}','%{exitcode}'),CMD+' --retry 2',CMD+' | more'):
            self.assertIsNone(a.detect(event(response(),cmd)),cmd)
        for field in ('tool_input','tool_response'):
            e=event(response(),CMD);e['_coverage']={'truncated_fields':[field]};self.assertIsNone(a.detect(e))

    def test_zero_transfer_can_still_be_http_error(self):
        raw=response(0,None,prefix='HTTP/1.1 400 Bad Request\nContent-Type: text/plain\n\nbad',response_code=400)
        self.assertEqual(a.detect(event(raw,CMD))['rule'],'http-status-error')
        raw=response(0,None,prefix='HTTP/1.1 400 Bad Request\n\ncurl: (18) example',response_code=400)
        self.assertEqual(a.detect(event(raw,CMD))['signal']['contract_field'],'curl_write_out.response_code')

    def test_interleaved_body_does_not_require_reconstruction(self):
        self.assertEqual(a.detect(event(response(prefix='cHTTP/malformed\nurl: incomplete'),CMD))['rule'],'curl-transfer-result')
        # An old, unframed return remains unidentifiable.
        self.assertIsNone(a.detect(event('cHTTP/malformed\nurl: incomplete',CMD)))
