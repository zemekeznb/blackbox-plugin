"""NOTES-052 controlled response fixtures, not a production GraphQL engine."""
import json

CASES = {
    'gql_json_error': ('graphql-errors', 'Unknown inventory field'),
    'gql_json_partial': ('graphql-errors', 'Inventory unavailable'),
    'gql_json_success': (None, 'available'),
    'gql_json_no_accept': (None, 'Unknown inventory field'),
    'gql_json_qzero': (None, 'Unknown inventory field'),
    'gql_http400': ('graphql-errors', 'HTTP/1.0 400'),
    'gql_http422': ('graphql-errors', 'HTTP/1.0 422'),
    'gql_http500': ('graphql-errors', 'HTTP/1.0 500'),
    'gql_json_gateway500': ('http-status-error', 'HTTP/1.0 500'),
    'gql_html502': ('http-status-error', 'HTTP/1.0 502'),
    'gql_batch': (None, 'Unknown inventory field'),
    'gql_stream': (None, 'event: complete'),
    'rpc_http400': ('json-rpc-error', 'HTTP/1.0 400'),
    'rpc_http500': ('json-rpc-error', 'HTTP/1.0 500'),
    'rpc_batch_mixed': ('json-rpc-batch-error', 'Insufficient inventory'),
    'rpc_batch_errors': ('json-rpc-batch-error', 'Insufficient inventory'),
    'rpc_batch_success': (None, 'available'),
    'rpc_batch_notification': ('json-rpc-batch-error', 'Insufficient inventory'),
    'rpc_batch_type_ids': ('json-rpc-batch-error', 'Insufficient inventory'),
    'rpc_batch_missing': (None, 'Insufficient inventory'),
    'rpc_batch_duplicate': (None, 'Insufficient inventory'),
    'rpc_batch_extra': (None, 'unknown-id'),
    'rpc_batch_invalid_response': (None, 'Malformed sibling'),
    'rpc_batch_duplicate_request': (None, 'Insufficient inventory'),
}


def headers(case):
    if not case.startswith('gql_') or case == 'gql_json_no_accept':
        return ''
    accept = 'application/graphql-response+json' + (';q=0' if case == 'gql_json_qzero' else ', application/json;q=0.9')
    return " -H 'Accept: " + accept + "'"


def request(case):
    if case.startswith('gql_'):
        payload = {'query': '{ inventory { available } health }'}
        return [payload, payload] if case == 'gql_batch' else payload
    payload = {'jsonrpc': '2.0', 'method': 'inventory.check', 'params': {'quantity': 10}, 'id': 'first'}
    if not case.startswith('rpc_batch_'):
        return payload
    second = dict(payload, id='second', params={'quantity': 1})
    if case == 'rpc_batch_success':
        payload = dict(payload, params={'quantity': 1})
    elif case == 'rpc_batch_errors':
        second = dict(second, params={'quantity': 10})
    elif case == 'rpc_batch_duplicate_request':
        second = dict(second, id='first')
    elif case == 'rpc_batch_type_ids':
        payload = dict(payload, id=1)
        second = dict(second, id='1')
    values = [payload, second]
    if case == 'rpc_batch_notification':
        values.append({key: value for key, value in payload.items() if key != 'id'})
    return values


def rpc_reply(value):
    quantity = value['params']['quantity']
    result = {'jsonrpc': '2.0', 'id': value['id']}
    if quantity > 3:
        result['error'] = {'code': 1001, 'message': 'Insufficient inventory', 'data': {'available': 3, 'requested': quantity}}
    else:
        result['result'] = {'available': 3, 'requested': quantity}
    return result


def reply(case, value):
    if case.startswith('gql_'):
        request_error = {'errors': [{'message': 'Unknown inventory field', 'locations': [{'line': 1, 'column': 3}]}]}
        execution_error = {'message': 'Inventory unavailable', 'path': ['inventory']}
        result = request_error
        status = 400 if case == 'gql_http400' else 422 if case == 'gql_http422' else 500 if case in ('gql_http500', 'gql_json_gateway500') else 200
        media = 'application/json' if case.startswith('gql_json_') or case == 'gql_batch' else 'application/graphql-response+json'
        if case == 'gql_json_partial':
            result = {'data': {'inventory': None, 'health': 'ready'}, 'errors': [execution_error]}
        elif case == 'gql_json_success':
            result = {'data': {'inventory': {'available': 3}, 'health': 'ready'}}
        elif case == 'gql_http500':
            result = {'errors': [{'message': 'Request could not be executed'}]}
        elif case == 'gql_html502':
            return 502, 'text/html', '<html><body>Upstream unavailable</body></html>'
        elif case == 'gql_batch':
            result = [request_error, {'data': {'health': 'ready'}}]
        elif case == 'gql_stream':
            return 200, 'text/event-stream', 'event: next\ndata: ' + json.dumps(request_error) + '\n\nevent: complete\n\n'
        return status, media, result
    if not isinstance(value, list):
        return (400 if case == 'rpc_http400' else 500), 'application/json', rpc_reply(value)
    result = [rpc_reply(item) for item in value if 'id' in item]
    if case == 'rpc_batch_missing':
        result = result[:1]
    elif case == 'rpc_batch_duplicate':
        result = [result[0], result[0]]
    elif case == 'rpc_batch_extra':
        result.append(dict(result[0], id='unknown-id'))
    elif case == 'rpc_batch_invalid_response':
        result[1] = {'jsonrpc': '2.0', 'id': 'second', 'error': {'code': True, 'message': 'Malformed sibling'}}
    # Standard JSON-RPC batch order is deliberately reversed.
    return 200, 'application/json', list(reversed(result))
