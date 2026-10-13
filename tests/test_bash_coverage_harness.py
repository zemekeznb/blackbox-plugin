import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_bash_coverage import assess, expected_output, psquote

TRACE = 'Traceback (most recent call last):\n  File "fixture.py", line 1\nFileNotFoundError: absent\n'


def row(case='file_missing', output=TRACE, **changes):
    body = {'tool_name': 'Bash', 'tool_input': {'command': case}, 'tool_response': output,
            '_coverage': {'capture_boundary_version': 1, 'truncated_fields': []}, 'turn_id': 'turn'}
    body.update(changes)
    return {'seq': 1, 'kind': 'PostToolUse', 'digest': 'synthetic', 'content': json.dumps(body)}


class BashCoverageHarnessTests(unittest.TestCase):
    def test_missing_or_duplicate_error_never_passes(self):
        commands = {'file_missing': 'file_missing'}
        for rows in ([], [row(), row()]):
            self.assertFalse(assess(rows, commands, list(commands))['passed'])
        self.assertTrue(assess([row()], commands, list(commands))['passed'])

    def test_wrong_tool_or_command_does_not_supply_evidence(self):
        for evidence in (row(tool_name='other'), row(tool_input={'command': 'other'})):
            self.assertFalse(assess([evidence], {'file_missing': 'file_missing'}, ['file_missing'])['passed'])

    def test_gap_truncation_and_missing_boundary_fail(self):
        for coverage in ({}, {'capture_boundary_version': 1, 'capture_gap_before': True},
                         {'capture_boundary_version': 1, 'truncated_fields': ['tool_response']}):
            self.assertFalse(assess([row(_coverage=coverage)], {'file_missing': 'file_missing'}, ['file_missing'])['passed'])

    def test_http_code_and_timeout_operation_are_checked(self):
        header = TRACE.rsplit('FileNotFoundError:', 1)[0]
        self.assertTrue(expected_output('http503', header + 'urllib.error.HTTPError: HTTP Error 503: Service Unavailable'))
        self.assertFalse(expected_output('http503', header + 'urllib.error.HTTPError: HTTP Error 429: Too Many Requests'))
        self.assertFalse(expected_output('connect_timeout', header + 'TimeoutError: timed out'))
        self.assertTrue(expected_output('connect_timeout', header + '    client.connect(server.getsockname())\nTimeoutError: timed out'))

    def test_success_requires_marker_and_no_traceback(self):
        self.assertTrue(expected_output('success', 'NOTES049_SUCCESS'))
        self.assertFalse(expected_output('success', ''))
        self.assertFalse(expected_output('success', 'NOTES049_SUCCESS\n' + TRACE))
        self.assertTrue(expected_output('http200_error_text', 'error: synthetic application message'))

    def test_powershell_literal_quoting_never_interpolates(self):
        self.assertEqual(psquote("a'b`$()"), "'a''b`$()'")


if __name__ == '__main__':
    unittest.main()
