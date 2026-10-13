"""Independent protocol fixtures; not evidence of host Hook delivery."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

SERVER = Path(__file__).resolve().parents[1] / 'tools/error-probe/plugin/server.py'


class ErrorProbeTests(unittest.TestCase):
    def exchange(self, requests):
        process = subprocess.run([sys.executable, '-I', str(SERVER)],
            input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True,
            text=True, timeout=5, check=True)
        self.assertEqual(process.stderr, '')
        return [json.loads(line) for line in process.stdout.splitlines()]

    def request(self, case):
        return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'run_case', 'arguments': {'case': case}}}

    def test_success_and_error_results_remain_distinct(self):
        replies = self.exchange([self.request(c) for c in ('success', 'error', 'log_success', 'json_error', 'protocol_error')])
        self.assertEqual([r['result']['isError'] for r in replies[:4]], [False, True, False, True])
        self.assertIn('Traceback', replies[2]['result']['content'][0]['text'])
        self.assertIn('NOTES046_JSON_ERROR', replies[3]['result']['content'][0]['text'])
        self.assertEqual(replies[4]['error']['code'], -32602)
        self.assertNotIn('result', replies[4])

    def test_rejects_arbitrary_arguments_without_echoing(self):
        requests = []
        for args in ({'case': 'unknown'}, {'case': 'error', 'path': 'PRIVATE'}, {}, [], {'case': {}}):
            req = self.request('success'); req['params']['arguments'] = args; requests.append(req)
        for reply in self.exchange(requests):
            self.assertEqual(reply['error']['code'], -32602)
            self.assertNotIn('PRIVATE', json.dumps(reply))

    def test_discovery_and_size_limits(self):
        replies = self.exchange([{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                 {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}])
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[1]['result']['tools'][0]['name'], 'run_case')
        process = subprocess.run([sys.executable, '-I', str(SERVER)], input=b'x'*65537,
                                  capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout, b'')


if __name__ == '__main__':
    unittest.main()
