"""Audit assertions must not turn absent errors into a passing acceptance."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_mcp_coverage import assess, MARKERS, TOOL


def row(case, seq=1, tool=TOOL):
    return {'seq': seq, 'digest': 'synthetic', 'kind': 'PostToolUse', 'content': json.dumps({
        'tool_name': tool, 'tool_input': {'case': case},
        'tool_response': {'content': [{'type': 'text', 'text': MARKERS[case]}],
                          'isError': case in ('error', 'json_error')}})}


class McpCoverageHarnessTests(unittest.TestCase):
    def test_successes_alone_do_not_prove_error_delivery(self):
        report = assess([row('success'), row('log_success')])
        self.assertFalse(report['passed'])
        self.assertEqual(report['missing_cases'], ['error', 'json_error'])

    def test_complete_expected_results_pass_but_duplicates_do_not(self):
        rows = [row(c, i) for i, c in enumerate(MARKERS, 1)]
        self.assertTrue(assess(rows)['passed'])
        self.assertFalse(assess(rows + [row('error', 5)])['passed'])

    def test_wrong_flags_and_unrelated_tools_cannot_substitute(self):
        rows = [row(c, i) for i, c in enumerate(MARKERS, 1)]
        content = json.loads(rows[1]['content'])
        content['tool_response']['isError'] = 'true'
        rows[1]['content'] = json.dumps(content)
        self.assertFalse(assess(rows)['passed'])
        self.assertFalse(assess([row(c, tool='Bash') for c in MARKERS])['passed'])


if __name__ == '__main__':
    unittest.main()
