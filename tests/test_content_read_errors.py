"""Read identity only excludes foreign Python traceback shapes."""
import unittest
from test_curl_errors import event
from test_anomalies import TRACE, PS
from test_shell_errors import PS as READ_ERROR
import blackbox_anomalies as a
from blackbox_shell_errors import literal_content_read


class ContentReadErrors(unittest.TestCase):
    def test_explicit_read_forms_exclude_python_text(self):
        for cmd in ("Get-Content 'old log.txt'", "get-content -Raw -LiteralPath 'old log.txt'",
                    "& 'Microsoft.PowerShell.Management\\Get-Content' -Encoding utf8 -Path old.log",
                    'Get-Content old.log -Tail 20', 'Get-Content -TotalCount 10 old.log'):
            self.assertTrue(literal_content_read(event(TRACE,cmd)))
            for ending in ('\n','\r\n','\r\r\n'):
                self.assertIsNone(a.detect(event(TRACE.replace('\n',ending),cmd)))

    def test_read_errors_require_current_target(self):
        self.assertIsNone(a.detect(event(READ_ERROR,'Get-Content C:/logs/old.log')))
        self.assertIsNone(a.detect(event(TRACE+READ_ERROR,'Get-Content C:/logs/old.log')))
        self.assertEqual(a.detect(event(PS,'Get-Content missing.log'))['rule'],'powershell-process-start')
        raw=READ_ERROR.replace('Get-Item: C:\\test\\main.ps1:4','Get-Content:')
        self.assertEqual(a.detect(event(raw,'Get-Content C:/test/missing'))['rule'],'powershell-diagnostic')
        self.assertIsNone(a.detect(event(raw,'Get-Content C:/test/other')))

    def test_unknown_identity_and_options_do_not_gain_exemption(self):
        for cmd in ('Get-Content old.log | python -',
                    'Get-Content old.log; python run.py', 'Get-Content $path',
                    'C:/custom/Get-Content old.log','Get-Content old.log -Wait',
                    'Get-Content -Raw -Raw old.log','Get-Content -Path a -LiteralPath b',
                    'Get-Content -Tail nope old.log','Get-Content -Encoding unknown old.log',
                    'Get-Content -LiteralPath','Get-Content','Get-Content a b'):
            e=event(TRACE,cmd);self.assertFalse(literal_content_read(e),cmd)
            self.assertEqual(a.detect(e)['rule'],'python-exception')

    def test_capture_identity_and_truncated_output(self):
        for field in ('tool_input',):
            e=event(TRACE,'Get-Content old.log');e['_coverage']={'truncated_fields':[field]}
            self.assertFalse(literal_content_read(e))
        e=event(TRACE,'Get-Content [REDACTED]');self.assertFalse(literal_content_read(e))
        e=event({'truncated':True,'original_length':100000,'preview':'log\ncut','tail':'cut\n'+TRACE},'Get-Content old.log')
        e['_coverage']={'truncated_fields':['tool_response']};self.assertIsNone(a.detect(e))
        e['tool_input']['command']='python run.py';self.assertEqual(a.detect(e)['rule'],'python-exception')

    def test_ordinary_python_and_warning_not_reinterpreted(self):
        self.assertEqual(a.detect(event(TRACE,'python run.py'))['rule'],'python-exception')
        self.assertIsNone(a.detect(event('WARNING\nOK\n','Get-Content old.log')))

    def test_short_missing_path_requires_exact_requested_absolute_path(self):
        raw="Get-Content: Cannot find path 'C:\\test\\missing.log' because it does not exist.\r\n"
        cmd="Get-Content -Raw -LiteralPath 'C:/test/missing.log'"
        self.assertEqual(a.detect(event(raw,cmd))['rule'],'powershell-diagnostic')
        for output in (raw.replace('missing.log','other.log'),'log\n'+raw,raw+'OK\n',raw+raw):
            self.assertIsNone(a.detect(event(output,cmd)))
        self.assertIsNone(a.detect(event(raw,'Get-Content missing.log')))
        self.assertEqual(a.detect(event(raw,'cat C:/test/missing.log'))['rule'],'powershell-diagnostic')
