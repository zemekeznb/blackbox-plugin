import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from probe_result_api import MAX_LINE, SENTINEL, probe, requests, summarize


class ResultApiProbeTests(unittest.TestCase):
    def test_requests_cannot_read_real_bodies_or_change_thread(self):
        calls = requests('selected-thread')
        self.assertEqual([method for method, _ in calls],
                         ['thread/read', 'thread/turns/list', 'thread/items/list'])
        self.assertIs(calls[0][1]['includeTurns'], False)
        self.assertEqual(calls[1][1]['itemsView'], 'notLoaded')
        self.assertEqual(calls[2][1]['threadId'], SENTINEL)
        self.assertEqual(calls[2][1]['turnId'], SENTINEL)

    def test_summary_never_exposes_names_preview_or_unexpected_body(self):
        reply = {'result': {'thread': {'id': 'selected', 'cwd': 'project', 'status': {'type': 'active'},
                                      'name': 'PRIVATE', 'preview': 'PRIVATE',
                                      'turns': [{'items': ['PRIVATE']}]}}}
        result = summarize(reply, 'thread/read', 'selected', 'project')
        self.assertTrue(result['identity_matches'])
        self.assertTrue(result['cwd_matches'])
        self.assertEqual(result['returned_turn_count'], 1)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_errors_are_classified_without_echo(self):
        result = summarize({'error': {'code': -32601, 'message': 'not supported PRIVATE'}}, '', '', '')
        self.assertEqual(result, {'rpc_error': 'unsupported', 'code': -32601})
        result = summarize({'error': {'message': 'PRIVATE'}}, '', '', '')
        self.assertEqual(result, {'rpc_error': 'other', 'code': None})

    def test_unexpected_real_turn_items_are_reported_without_text(self):
        result = summarize({'result': {'data': [{'items': [{'text': 'PRIVATE'}]}]}},
                           'thread/turns/list', '', '')
        self.assertFalse(result['item_bodies_absent'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def run_child(self, source, timeout=3):
        with tempfile.TemporaryDirectory() as temp:
            child = Path(temp) / 'fake_server.py'
            child.write_text(source, encoding='utf-8')
            return probe('selected', 'project', timeout=timeout, command=[sys.executable, str(child)])

    def test_real_pipe_exchange_uses_only_metadata_requests(self):
        result = self.run_child('''import sys,json
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='initialized': continue
 if r['method']=='initialize': value={}
 elif r['method']=='thread/read':
  assert r['params']['includeTurns'] is False
  value={'thread':{'id':'selected','cwd':'project','status':{'type':'notLoaded'},'turns':[]}}
 elif r['method']=='thread/turns/list':
  assert r['params']['itemsView']=='notLoaded'
  value={'data':[{'id':'synthetic','items':[]}]}
 else:
  assert r['method']=='thread/items/list'
  assert r['params']['threadId']=='00000000-0000-4000-8000-000000000048'
  print(json.dumps({'id':r['id'],'error':{'code':-32601,'message':'not supported'}}),flush=True)
  continue
 print(json.dumps({'id':r['id'],'result':value}),flush=True)
''')
        self.assertNotIn('probe_failed', result)
        self.assertEqual(len(result['results']), 3)
        self.assertEqual(result['results']['thread/items/list']['rpc_error'], 'unsupported')

    def test_timeout_output_limit_and_interactive_request_fail_closed(self):
        self.assertTrue(self.run_child('import time\ntime.sleep(20)\n', timeout=.2)['timed_out'])
        self.assertTrue(self.run_child(f'import sys\nsys.stdout.write("x"*{MAX_LINE+1})\nsys.stdout.flush()\n')['probe_failed'])
        self.assertTrue(self.run_child('print(\'{"id":4,"method":"approval/request"}\',flush=True)\n')['probe_failed'])

    def test_project_mismatch_stops_before_turn_metadata_request(self):
        result = self.run_child('''import sys,json
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='initialized': continue
 value={} if r['method']=='initialize' else {'thread':{'id':'selected','cwd':'other','turns':[]}}
 print(json.dumps({'id':r['id'],'result':value}),flush=True)
''')
        self.assertTrue(result['scope_not_confirmed'])
        self.assertEqual(list(result['results']), ['thread/read'])
        self.assertFalse(result['capture_path_validated'])


if __name__ == '__main__':
    unittest.main()
