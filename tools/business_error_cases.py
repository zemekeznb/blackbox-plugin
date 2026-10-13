"""Local NOTES-051 controlled protocol service; no production/external calls.

JSON-RPC inventory.check is a read-only quantity check. GraphQL routes return
declared response fixtures (not a full GraphQL execution engine). The server
binds loopback, runs at most 30 minutes and accepts only its generated run path.
"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading


CASES = {
    'rpc_inventory': ('json-rpc-error', 'Insufficient inventory'),
    'rpc_params': ('json-rpc-error', 'Invalid quantity'),
    'rpc_method': ('json-rpc-error', 'Method not found'),
    'rpc_success': (None, 'available'),
    'rpc_false': (None, 'false'),
    'rpc_null': (None, 'null'),
    'rpc_wrong_id': (None, 'different-id'),
    'rpc_bad_code': (None, 'Invalid error code fixture'),
    'rpc_both': (None, 'Contradictory fixture'),
    'custom_false': (None, 'Custom contract not registered'),
    'graphql_request': ('graphql-errors', 'Unknown inventory field'),
    'graphql_null': ('graphql-errors', 'Inventory unavailable'),
    'graphql_partial': ('graphql-errors', 'Inventory unavailable'),
    'graphql_success': (None, 'available'),
    'graphql_nested': (None, 'Ordinary data'),
    'graphql_empty': (None, '"errors": []'),
    'graphql_generic_media': (None, 'Unknown inventory field'),
    'graphql_missing_path': (None, 'Missing path fixture'),
    'read_saved_response': (None, 'Insufficient inventory'),
    'rpc_body_only': (None, 'Insufficient inventory'),
}


def request(case):
    if case.startswith('graphql'):
        return {'query': '{ inventory { available } health }', 'operationName': None}
    return {'jsonrpc': '2.0', 'id': case, 'method': 'missing' if case == 'rpc_method' else 'inventory.check',
            'params': {'quantity': -1 if case == 'rpc_params' else 1 if case == 'rpc_success' else 10}}


def reply(case, value):
    if case.startswith('graphql'):
        media = 'application/json' if case == 'graphql_generic_media' else 'application/graphql-response+json'
        error = {'message': 'Inventory unavailable', 'path': ['inventory'], 'extensions': {'code': 'INVENTORY_UNAVAILABLE'}}
        result = {
            'graphql_request': {'errors': [{'message': 'Unknown inventory field', 'locations': [{'line': 1, 'column': 3}]}]},
            'graphql_generic_media': {'errors': [{'message': 'Unknown inventory field'}]},
            'graphql_null': {'data': None, 'errors': [error]},
            'graphql_partial': {'data': {'inventory': None, 'health': 'ready'}, 'errors': [error]},
            'graphql_success': {'data': {'inventory': {'available': 3}, 'health': 'ready'}},
            'graphql_nested': {'data': {'success': False, 'errors': [{'message': 'Ordinary data'}]}},
            'graphql_empty': {'data': {'inventory': 3}, 'errors': []},
            'graphql_missing_path': {'data': None, 'errors': [{'message': 'Missing path fixture'}]},
        }[case]
        return media, result
    result = {'jsonrpc': '2.0', 'id': value.get('id')}
    quantity = value.get('params', {}).get('quantity')
    if value.get('method') != 'inventory.check':
        result['error'] = {'code': -32601, 'message': 'Method not found'}
    elif type(quantity) is not int or quantity < 0:
        result['error'] = {'code': -32602, 'message': 'Invalid quantity'}
    elif quantity > 3:
        result['error'] = {'code': 1001, 'message': 'Insufficient inventory', 'data': {'requested': quantity, 'available': 3}}
    else:
        result['result'] = {'available': 3, 'requested': quantity}
    if case in ('rpc_false', 'rpc_null'):
        result.pop('error', None)
        result['result'] = False if case == 'rpc_false' else None
    elif case == 'rpc_wrong_id':
        result['id'] = 'different-id'
    elif case == 'rpc_bad_code':
        result['error'] = {'code': True, 'message': 'Invalid error code fixture'}
    elif case == 'rpc_both':
        result.update(result=True, error={'code': 1001, 'message': 'Contradictory fixture'})
    elif case == 'custom_false':
        result = {'success': False, 'code': 1001, 'error': 'Custom contract not registered'}
    return 'application/json', result


def serve(root):
    setup = json.loads((root / 'server-config.json').read_text(encoding='utf-8'))
    suite = setup.get('suite', 'notes051')
    if suite == 'notes052':
        from business_protocol_cases import CASES as active_cases, reply as active_reply
    elif suite == 'notes051':
        active_cases = CASES
        def active_reply(case, value):
            media, result = reply(case, value)
            return 200, media, result
    else:
        raise ValueError('unknown fixture suite')
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            prefix = '/' + setup['run_id'] + '/'
            if not self.path.startswith(prefix):
                self.send_error(404)
                return
            case = self.path[len(prefix):]
            if case == 'stop':
                self.send_response(204)
                self.end_headers()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if case not in active_cases or case == 'read_saved_response':
                self.send_error(404)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 8192:
                self.send_error(400)
                return
            value = json.loads(self.rfile.read(length))
            status, media, result = active_reply(case, value)
            data = (result if isinstance(result, str) else json.dumps(result)).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            with (root / 'http-requests.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps({'case': case, 'status': status, 'request': value, 'response': result}) + '\n')

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    server.timeout = 5
    timer = threading.Timer(1800, server.shutdown)
    timer.daemon = True
    timer.start()
    (root / 'server-ready.json').write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    serve(parser.parse_args().run_root)
