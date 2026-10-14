import unittest
from test_anomalies import TRACE, PS as START_ERROR
from test_shell_errors import PS as LOGGED_ERROR
from test_curl_errors import event
import blackbox_anomalies as a
from blackbox_shell_errors import content_read_path, literal_content_read


class AmbiguousReads(unittest.TestCase):
    def test_bounded_alias_syntax_is_uncertainty_not_identity(self):
        for alias in ('cat', 'gc', 'type', 'CAT'):
            for suffix in (" 'C:/log file.txt'", ' -Raw -LiteralPath C:/log.txt',
                           ' -Encoding utf8 -Path relative.log', ' relative.log -Tail 20'):
                cmd = alias + suffix
                self.assertFalse(literal_content_read(event('', cmd)))
                for body in (TRACE, LOGGED_ERROR, TRACE + LOGGED_ERROR, 'OK'):
                    self.assertIsNone(a.detect(event(body, cmd)))

    def test_matching_missing_target_is_retained(self):
        raw = "Get-Content: Cannot find path 'C:\\test\\missing.log' because it does not exist.\n"
        for alias in ('cat', 'gc', 'type'):
            cmd = alias + ' -LiteralPath C:/test/missing.log'
            hit = a.detect(event(raw, cmd))
            self.assertEqual(hit['rule'], 'powershell-diagnostic')
            self.assertEqual(hit['evidence'], raw)
            for body in (raw.replace('missing.log', 'other.log'), 'log\n' + raw, raw + 'OK\n'):
                self.assertIsNone(a.detect(event(body, cmd)))
        # Only component suffix agreement is observed, not a resolved cwd.
        self.assertEqual(a.detect(event(raw, 'cat missing.log'))['rule'], 'powershell-diagnostic')
        for cmd in ('cat other.log', 'cat ../missing.log', 'cat /missing.log', 'cat *.log', 'cat ing.log'):
            self.assertIsNone(a.detect(event(raw, cmd)))

    def test_compound_redefinition_and_unsupported_syntax_not_exempted(self):
        for cmd in ("Set-Alias cat python; cat fail.py", 'cat a; python fail.py',
                    'cat a | python -', 'cat $path', 'cat a b', 'cat -Unknown a',
                    'C:/bin/cat.exe a', 'cat -Path a -LiteralPath b', 'cat [REDACTED]'):
            self.assertIsNone(content_read_path(event('', cmd), ambiguous_alias=True))
            self.assertEqual(a.detect(event(TRACE, cmd))['rule'], 'python-exception')

    def test_truncation_never_establishes_identity_or_current_diagnostic(self):
        e = event(TRACE, 'cat C:/log.txt');e['_coverage'] = {'truncated_fields': ['tool_input']}
        self.assertEqual(a.detect(e)['rule'], 'python-exception')
        e = event({'truncated': True, 'original_length': 100000, 'preview': 'log\ncut',
                   'tail': 'cut\n' + TRACE}, 'cat C:/log.txt')
        e['_coverage'] = {'truncated_fields': ['tool_response']}
        self.assertIsNone(a.detect(e))

    def test_start_failure_and_hidden_redefinition_tradeoff(self):
        self.assertEqual(a.detect(event(START_ERROR, 'cat C:/log.txt'))['rule'], 'powershell-process-start')
        # Exactly the same saved input/output could be a hidden alias to Python.
        # Without its identity we exclude, never claim that the invocation succeeded.
        self.assertIsNone(a.detect(event(TRACE, 'cat C:/fail.py')))
        self.assertEqual(a.detect(event(TRACE, 'python C:/fail.py'))['rule'], 'python-exception')
