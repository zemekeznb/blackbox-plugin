"""Static probe protocol tests; these do not prove host UI availability."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

PROBE = Path(__file__).resolve().parents[1] / 'tools/entry-probe/plugin/server.py'


class EntryProbeTests(unittest.TestCase):
    def exchange(self, messages):
        result = subprocess.run([sys.executable, '-I', str(PROBE)],
                                input=''.join(json.dumps(m) + '\n' for m in messages),
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stderr, '')
        return [json.loads(line) for line in result.stdout.splitlines()]

    def req(self, method, params=None):
        return {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}

    def test_initialize_and_notification(self):
        replies = self.exchange([self.req('initialize', {'protocolVersion': '2025-11-25'}),
                                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                 self.req('ping')])
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]['result']['protocolVersion'], '2025-11-25')

    def test_entrypoints_and_static_ui(self):
        tool = self.exchange([self.req('tools/list')])[0]['result']['tools'][0]
        self.assertEqual(tool['_meta']['openai/ui']['entrypoints'], [{'type': 'global'}, {'type': 'thread'}])
        uri = tool['_meta']['ui']['resourceUri']
        resource = self.exchange([self.req('resources/read', {'uri': uri})])[0]['result']['contents'][0]
        self.assertEqual(resource['mimeType'], 'text/html;profile=mcp-app')
        self.assertIn('ui/initialize', resource['text'])
        self.assertNotIn('fetch(', resource['text'])
        self.assertEqual(resource['_meta']['ui']['csp']['connectDomains'], [])

    def test_no_arbitrary_resource_or_tool_arguments(self):
        requests = [self.req('resources/read', {'uri': 'file:///private'}),
                    self.req('tools/call', {'name': 'open_probe', 'arguments': {'path': 'private'}}),
                    self.req('tools/call', {'name': 'read_records'})]
        for reply in self.exchange(requests):
            self.assertEqual(reply['error']['code'], -32602)

    def test_static_result(self):
        reply = self.exchange([self.req('tools/call', {'name': 'open_probe', 'arguments': {}})])[0]
        self.assertEqual(reply['result']['structuredContent'], {'probe': 'NOTES-024', 'recordsAccessed': False})

    def test_oversized_input_closes_without_echo(self):
        result = subprocess.run([sys.executable, '-I', str(PROBE)], input=b'x' * 65537,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    unittest.main()
