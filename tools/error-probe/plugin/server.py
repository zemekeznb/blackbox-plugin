"""Controlled stdio MCP results; no filesystem, network, database or model calls."""
import json
import sys

LIMIT = 65536
CASES = ('success', 'error', 'log_success', 'json_error', 'protocol_error')
TRACE = 'Traceback (most recent call last):\n  File "synthetic.py", line 1\nTypeError: NOTES046_LOG_ONLY\n'
TOOL = {'name': 'run_case', 'description': 'Run an explicitly requested local synthetic error-coverage case. No records, network or files accessed.',
        'inputSchema': {'type': 'object', 'properties': {'case': {'type': 'string', 'enum': list(CASES)}},
                        'required': ['case'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}}


def dispatch(method, params):
    if method == 'initialize':
        version = params.get('protocolVersion')
        if version not in ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'):
            version = '2025-11-25'
        return {'protocolVersion': version, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'blackbox-error-probe', 'version': '0.0.1'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [TOOL]}
    if method == 'tools/call':
        args = params.get('arguments')
        if (params.get('name') != 'run_case' or not isinstance(args, dict)
                or set(args) != {'case'} or args['case'] not in CASES):
            raise ValueError()
        case = args['case']
        if case == 'protocol_error':
            raise ValueError()
        if case == 'json_error':
            try:
                json.loads('{NOTES046_INVALID_JSON}')
            except json.JSONDecodeError:
                text, failed = 'NOTES046_JSON_ERROR: synthetic JSON decoding failed', True
        elif case == 'error':
            text, failed = 'NOTES046_TOOL_ERROR: controlled MCP failure result', True
        elif case == 'log_success':
            text, failed = TRACE, False
        else:
            text, failed = 'NOTES046_SUCCESS: controlled normal result', False
        return {'content': [{'type': 'text', 'text': text}], 'isError': failed}
    raise LookupError()


def handle(message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
    if 'id' not in message:
        return None
    reply = {'jsonrpc': '2.0', 'id': message['id']}
    try:
        params = message.get('params', {})
        if not isinstance(params, dict):
            raise ValueError()
        reply['result'] = dispatch(message['method'], params)
    except ValueError:
        reply['error'] = {'code': -32602, 'message': 'NOTES046: invalid parameters'}
    except LookupError:
        reply['error'] = {'code': -32601, 'message': 'Method not found'}
    return reply


def main():
    while True:
        line = sys.stdin.buffer.readline(LIMIT + 1)
        if not line:
            return 0
        if len(line) > LIMIT:
            return 1
        try:
            reply = handle(json.loads(line))
        except (ValueError, UnicodeError, RecursionError):
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
