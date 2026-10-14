"""Contract regressions; synthetic Store inputs are not host acceptance evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import blackbox_anomalies as a
from blackbox_business_errors import strict_json
from blackbox_core import Store
from blackbox_mcp import Reader
from verify_shell_formats import audit
from verify_shell_installation import control_text

RPC = {'jsonrpc': '2.0', 'method': 'inventory.check', 'params': {'quantity': 4}, 'id': 'check-1'}
RPC_ERROR = {'jsonrpc': '2.0', 'id': 'check-1', 'error': {'code': 1001, 'message': 'Insufficient inventory'}}
GQL = {'query': '{ inventory { available } }'}
GQL_ERROR = {'errors': [{'message': 'Cannot query field', 'locations': [{'line': 1, 'column': 3}]}]}


def command(request):
    data = json.dumps(request, separators=(',', ':')).replace("'", "''")
    return 'curl.exe -q -sS -i --max-time 5 -H \'Content-Type: application/json\' --data-raw \'' + data + "' 'http://127.0.0.1:12345/api'"


def response(value, media='application/json'):
    return 'HTTP/1.1 200 OK\r\nContent-Type: ' + media + '\r\n\r\n' + json.dumps(value)


def event(request=RPC, result=RPC_ERROR, media='application/json'):
    return {'tool_name': 'Bash', 'tool_input': {'command': command(request)}, 'tool_response': response(result, media)}


class BusinessErrorTests(unittest.TestCase):
    def test_rpc_observed_error_preserves_entire_http_return(self):
        content = event()
        hit = a.detect(content)
        self.assertEqual(hit['rule'], 'json-rpc-error')
        self.assertEqual(hit['evidence'], content['tool_response'])
        self.assertEqual(hit['signal']['kind'], 'application_reported_error')
        self.assertFalse(hit['signal']['independently_verified'])
        content['tool_input']['command'] = content['tool_input']['command'].replace('curl.exe', "& 'C:\\Windows\\System32\\curl.exe'") + '; exit $LASTEXITCODE'
        self.assertIsNotNone(a.detect(content))

    def test_rpc_requires_exact_id_and_valid_error_envelope(self):
        for change in ({'id': 1}, {'id': None}, {'jsonrpc': '1.0'}, {'result': None},
                       {'error': {'code': True, 'message': 'bad'}},
                       {'error': {'code': '1001', 'message': 'bad'}},
                       {'error': {'code': 1001, 'message': ''}}, {'error': 'bad'}):
            self.assertIsNone(a.detect(event(result=dict(RPC_ERROR, **change))), change)
        for change in ({'id': None}, {'id': True}, {'id': ''}, {'method': None}, {'params': 'bad'}):
            self.assertIsNone(a.detect(event(request=dict(RPC, **change))))
        self.assertIsNone(a.detect(event(request={key: val for key, val in RPC.items() if key != 'id'})))
        self.assertIsNone(a.detect(event(result={'jsonrpc': '2.0', 'id': 'check-1', 'result': {'success': False, 'error': RPC_ERROR}})))
        self.assertIsNotNone(a.detect(event(request=dict(RPC, id=1), result=dict(RPC_ERROR, id=1))))
        self.assertIsNone(a.detect(event(request=dict(RPC, id=1), result=dict(RPC_ERROR, id=True))))

    def test_graphql_distinguishes_request_null_and_partial_data(self):
        for value, title in ((GQL_ERROR, '请求错误'),
                ({'data': None, 'errors': [{'message': 'Inventory unavailable', 'path': ['inventory']}]}, 'data 为 null'),
                ({'data': {'inventory': None, 'version': 'test'}, 'errors': [{'message': 'Inventory unavailable', 'path': ['inventory']}]}, '部分数据')):
            content = event(GQL, value, 'application/graphql-response+json; charset=utf-8')
            hit = a.detect(content)
            self.assertEqual(hit['rule'], 'graphql-errors')
            self.assertIn(title, hit['title'])
            self.assertEqual(hit['evidence'], content['tool_response'])

    def test_graphql_requires_protocol_and_valid_error_shapes(self):
        for value in ({'errors': []}, {'errors': [{'message': False}]},
                      {'errors': GQL_ERROR['errors'], 'data': []},
                      {'errors': GQL_ERROR['errors'], 'data': None},
                      {'errors': [{'message': 'bad', 'path': [True]}]},
                      {'errors': [{'message': 'bad', 'path': [-1]}]},
                      {'errors': [{'message': 'bad', 'locations': [{'line': 0, 'column': 1}]}]},
                      {'errors': [{'message': 'bad', 'extensions': 'bad'}]},
                      {'errors': GQL_ERROR['errors'], 'extensions': False},
                      {'errors': GQL_ERROR['errors'], 'custom': 1}):
            self.assertIsNone(a.detect(event(GQL, value, 'application/graphql-response+json')), value)
        self.assertIsNone(a.detect(event(GQL, GQL_ERROR)))
        for request in ({'query': ''}, {'query': 7}, dict(GQL, variables=[]), dict(GQL, operationName=7), dict(GQL, jsonrpc='2.0')):
            self.assertIsNone(a.detect(event(request, GQL_ERROR, 'application/graphql-response+json')))

    def test_success_and_custom_business_fields_do_not_match(self):
        for value in ({'success': False, 'error': 'bad'}, {'code': 500, 'message': 'bad'},
                      {'jsonrpc': '2.0', 'id': 'check-1', 'result': False},
                      {'jsonrpc': '2.0', 'id': 'check-1', 'result': None}):
            self.assertIsNone(a.detect(event(result=value)))
        for value in ({'data': None}, {'data': {'errors': ['bad'], 'success': False}}, {'data': {'inventory': 3}}):
            self.assertIsNone(a.detect(event(GQL, value, 'application/graphql-response+json')))

    def test_curl_context_rejects_logs_wrappers_options_and_ambiguous_calls(self):
        original = command(RPC)
        for value in ('Get-Content response.log', 'echo ' + original, original + ' | Out-String',
                      original + '; echo OK', original + '\nGet-Content response.log',
                      original.replace('-q ', ''), original.replace('-i ', ''),
                      original.replace('curl.exe', 'curl'), original + ' --location-trusted',
                      original + ' --config config.txt', original + ' --output output.json',
                      original + " 'http://127.0.0.1/other'", original.replace('--data-raw', '--data-binary'),
                      original.replace('/api', '/api[1-2]'), original.replace('-sS', '-X GET'),
                      original.replace('--data-raw', '-H \'Content-Type: text/plain\' --data-raw')):
            content = event()
            content['tool_input']['command'] = value
            self.assertIsNone(a.detect(content), value)
        content = event()
        content['tool_input']['command'] = original.replace("-H 'Content-Type: application/json' --data-raw", '--json')
        self.assertIsNotNone(a.detect(content))

    def test_http_requires_one_whole_json_response_and_final_status(self):
        original = event()['tool_response']
        for value in (original.replace('200 OK', '302 Found'), original.replace('application/json', 'text/plain'),
                      original + '\nextra log', 'progress\n' + original, original + original,
                      original.replace('Content-Type:', ' Content-Type:'),
                      original.replace('\r\n\r\n', '\r\nContent-Type: text/plain\r\n\r\n'),
                      json.dumps(RPC_ERROR), original[:-1], original.replace('"code": 1001', '"code": 1001, "code": 0')):
            content = event()
            content['tool_response'] = value
            self.assertIsNone(a.detect(content), value)
        for version in ('HTTP/2', 'HTTP/3'):
            content = event()
            content['tool_response'] = original.replace('HTTP/1.1', version)
            self.assertIsNotNone(a.detect(content))

    def test_strict_json_and_truncation_fail_closed(self):
        for text in ('{"a":NaN}', '{"a":1,"a":2}', '[' * 2000, 'not-json'):
            self.assertIsNone(strict_json(text))
        for field in ('tool_input', 'tool_response'):
            content = event()
            content['_coverage'] = {'truncated_fields': [field]}
            self.assertIsNone(a.detect(content))
        content = event()
        content['tool_response'] = {'truncated': True, 'original_length': 100000, 'preview': content['tool_response'], 'tail': 'cut\n'}
        self.assertIsNone(a.detect(content))
        content = event()
        content['tool_input']['command'] = command(dict(RPC, id='[REDACTED]'))
        self.assertIsNone(a.detect(content))
        content = event()
        content['tool_name'] = 'mcp__example__read_log'
        self.assertIsNone(a.detect(content))

    def test_control_reader_preserves_windows_newlines_before_detection(self):
        raw = event()['tool_response'].replace('\r\n', '\r\r\n')
        with tempfile.TemporaryDirectory(prefix='blackbox-control-test-') as tmp:
            path = Path(tmp) / 'control.txt'
            path.write_bytes(raw.encode('utf-8'))
            content = event()
            content['tool_response'] = control_text(path)
            self.assertEqual(content['tool_response'], raw)
            self.assertEqual(a.detect(content)['evidence'], raw)

    def test_readonly_list_detail_export_and_same_input_retry(self):
        with tempfile.TemporaryDirectory(prefix='blackbox-business-test-') as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            store = Store(root / 'state')
            store.set_project(str(project), True)
            for index, content in enumerate((event(), event(result={'jsonrpc': '2.0', 'id': 'check-1', 'result': True}))):
                store.record(dict(content, cwd=str(project), session_id='synthetic', turn_id='unit', hook_event_name='PostToolUse', tool_use_id=str(index)))
            with store.connect() as db:
                saved = [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY seq')]
            page = a.listing(store)
            self.assertEqual(len(page['items']), 1)
            item = page['items'][0]
            detail = a.detail(store, item['id'], page['snapshot_seq'])
            self.assertIn('相同输入再次调用 · 来源 #', detail['markdown'])
            self.assertIn(command(RPC), detail['markdown'])
            reader = Reader(root / 'state')
            try:
                path = f'/api/anomaly-export/{item["id"]}?through={page["snapshot_seq"]}&rule={a.RULE_VERSION}'
                result = reader.call('read_view', {'path': path})['_meta']['blackbox']
                self.assertEqual(result['body'], detail['markdown'])
                self.assertEqual(reader.call('read_view', {'path': path.replace(a.RULE_VERSION, 'observed-errors-5')})['_meta']['blackbox']['status'], 400)
            finally:
                reader.names.close()
                reader.projects.close()
            with store.connect() as db:
                self.assertEqual(saved, [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY seq')])

    def test_business_audit_rejects_missing_duplicate_and_wrong_sources(self):
        content = event()
        content['_coverage'] = {'capture_boundary_version': 1}
        row = {'kind': 'PostToolUse', 'seq': 1, 'digest': 'synthetic', 'content': json.dumps(content)}
        commands = {'inventory': command(RPC)}
        expected = {'inventory': ('json-rpc-error', 'Insufficient inventory')}
        self.assertTrue(audit([row], commands, expected)['passed'])
        self.assertFalse(audit([], commands, expected)['passed'])
        self.assertFalse(audit([row, row], commands, expected)['passed'])
        for changed in ({'tool_name': 'Read'}, {'tool_response': ''},
                        {'_coverage': {'capture_boundary_version': 1, 'capture_gap_before': True}},
                        {'_coverage': {'capture_boundary_version': 1, 'truncated_fields': ['tool_input']}}):
            other = dict(row, content=json.dumps(dict(content, **changed)))
            self.assertFalse(audit([other], commands, expected)['passed'])

    def test_graphql_generic_json_needs_explicit_protocol_acceptance(self):
        for accept in ('application/graphql-response+json',
                       'application/graphql-response+json, application/json;q=0.9',
                       'application/json, application/graphql-response+json;q=0.5'):
            content = event(GQL, GQL_ERROR)
            content['tool_input']['command'] += " -H 'Accept: " + accept + "'"
            self.assertEqual(a.detect(content)['rule'], 'graphql-errors')
        for accept in ('application/json', '*/*', 'application/graphql-response+json;q=0',
                       'application/graphql-response+json;q=0.000',
                       'application/graphql-response+json;q=2', 'application/graphql-response+json;q=no',
                       'application/graphql-response+json;q=1;q=0', 'application/graphql-response+jsonx'):
            content = event(GQL, GQL_ERROR)
            content['tool_input']['command'] += " -H 'Accept: " + accept + "'"
            self.assertIsNone(a.detect(content), accept)

    def test_graphql_non2xx_requires_specific_response_media(self):
        for status in (400, 422, 429, 500, 503):
            for media, expected in (('application/graphql-response+json', True), ('application/json', False), ('text/html', False)):
                content = event(GQL, GQL_ERROR, media)
                content['tool_input']['command'] += " -H 'Accept: application/graphql-response+json'"
                content['tool_response'] = content['tool_response'].replace('200 OK', str(status) + ' Error')
                self.assertEqual(a.detect(content)['rule'], 'graphql-errors' if expected else 'http-status-error')
        for status in (100, 302):
            content = event(GQL, GQL_ERROR, 'application/graphql-response+json')
            content['tool_response'] = content['tool_response'].replace('200 OK', str(status) + ' Other')
            self.assertIsNone(a.detect(content))

    def test_rpc_non2xx_requires_matched_error_not_http_status_alone(self):
        for status in (400, 422, 429, 500, 503):
            content = event()
            content['tool_response'] = content['tool_response'].replace('200 OK', str(status) + ' Error')
            self.assertEqual(a.detect(content)['rule'], 'json-rpc-error')
            content['tool_response'] = response({'error': 'Gateway error'}).replace('200 OK', str(status) + ' Error')
            self.assertEqual(a.detect(content)['rule'], 'http-status-error')

    def test_rpc_batch_matches_unordered_typed_ids_and_notifications(self):
        requests = [dict(RPC, id=1), dict(RPC, id='1'), {key: value for key, value in RPC.items() if key != 'id'}]
        results = [{'jsonrpc': '2.0', 'id': '1', 'result': False}, dict(RPC_ERROR, id=1)]
        content = event(requests, results)
        hit = a.detect(content)
        self.assertEqual(hit['rule'], 'json-rpc-batch-error')
        self.assertIn('1 项错误（共 2 项响应）', hit['title'])
        self.assertEqual(hit['evidence'], content['tool_response'])
        self.assertEqual(hit['signal']['contract_field'], 'http_body[].error')
        self.assertFalse(hit['signal']['independently_verified'])

    def test_rpc_batch_rejects_missing_extra_duplicate_or_invalid_responses(self):
        requests = [dict(RPC, id=1), dict(RPC, id=2)]
        results = [dict(RPC_ERROR, id=2), {'jsonrpc': '2.0', 'id': 1, 'result': True}]
        for value in (results[:1], results + [dict(RPC_ERROR, id=3)], [results[0], results[0]],
                      [results[0], dict(results[1], id=True)], [results[0], dict(results[1], jsonrpc='1.0')],
                      [results[0], dict(results[1], error=RPC_ERROR['error'])],
                      [results[0], {'jsonrpc': '2.0', 'id': 1}], [results[0], None],
                      {'jsonrpc': '2.0', 'id': None, 'error': RPC_ERROR['error']}, []):
            self.assertIsNone(a.detect(event(requests, value)), value)
        self.assertIsNone(a.detect(event(RPC, [RPC_ERROR])))

    def test_rpc_batch_bounds_and_invalid_requests(self):
        for requests in ([], [RPC] * 101, [RPC, RPC], [dict(RPC, id=None)], [dict(RPC, id=False)],
                         [dict(RPC, id=[])], [dict(RPC, params='bad')], [GQL], [None]):
            self.assertIsNone(a.detect(event(requests, [RPC_ERROR])), requests)
        self.assertIsNone(a.detect(event([{key: value for key, value in RPC.items() if key != 'id'}], [RPC_ERROR])))
        requests = [dict(RPC, id=index) for index in range(100)]
        results = [dict(RPC_ERROR, id=index) for index in range(100)]
        self.assertIn('100 项错误', a.detect(event(requests, results))['title'])
        self.assertIsNone(a.detect(event(requests + [dict(RPC, id=100)], results + [dict(RPC_ERROR, id=100)])))

    def test_batch_success_and_unknown_graphql_batches_or_streams_stay_excluded(self):
        self.assertIsNone(a.detect(event([RPC], [{'jsonrpc': '2.0', 'id': 'check-1', 'result': {'errors': ['ordinary']}}])))
        self.assertIsNone(a.detect(event([GQL], [GQL_ERROR], 'application/graphql-response+json')))
        content = event(GQL, GQL_ERROR, 'text/event-stream')
        content['tool_response'] += '\n\nevent: complete\n\n'
        self.assertIsNone(a.detect(content))
        content = event([RPC], [RPC_ERROR])
        content['_coverage'] = {'truncated_fields': ['tool_response']}
        self.assertIsNone(a.detect(content))

    def test_new_rules_keep_fixed_reader_snapshots_and_prior_sources(self):
        with tempfile.TemporaryDirectory(prefix='blackbox-expanded-test-') as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            store = Store(root / 'state')
            store.set_project(str(project), True)
            contents = [event([RPC], [RPC_ERROR]), event(GQL, GQL_ERROR, 'application/graphql-response+json')]
            contents[1]['tool_response'] = contents[1]['tool_response'].replace('200 OK', '422 Unprocessable Content')
            for index, content in enumerate(contents):
                store.record(dict(content, cwd=str(project), session_id='synthetic', turn_id='unit', hook_event_name='PostToolUse', tool_use_id=str(index)))
            with store.connect() as db:
                saved = [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY seq')]
            page = a.listing(store)
            self.assertEqual(len(page['items']), 2)
            reader = Reader(root / 'state')
            try:
                for item in page['items']:
                    detail = a.detail(store, item['id'], page['snapshot_seq'])
                    path = f'/api/anomaly-export/{item["id"]}?through={page["snapshot_seq"]}&rule={a.RULE_VERSION}'
                    output = reader.call('read_view', {'path': path})['_meta']['blackbox']
                    self.assertEqual(output['body'], detail['markdown'])
                    self.assertEqual(reader.call('read_view', {'path': path.replace(a.RULE_VERSION, 'observed-errors-6')})['_meta']['blackbox']['status'], 400)
            finally:
                reader.names.close()
                reader.projects.close()
            with store.connect() as db:
                self.assertEqual(saved, [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY seq')])


if __name__ == '__main__':
    unittest.main()
