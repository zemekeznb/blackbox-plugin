"""NOTES-024 isolated stdio MCP probe: static UI only, no recording access."""
import json
import sys
from pathlib import Path

URI = 'ui://blackbox-probe/entry-v1.html'
MIME = 'text/html;profile=mcp-app'
LIMIT = 65536
TOOL = {
    'name': 'open_probe', 'title': 'Blackbox Entry Test',
    'description': 'Open a static local UI compatibility test; does not access records.',
    'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False},
    '_meta': {'ui': {'resourceUri': URI},
              'openai/ui': {'entrypoints': [{'type': 'global'}, {'type': 'thread'}]}},
}


def dispatch(method, params):
    if method == 'initialize':
        requested = params.get('protocolVersion')
        version = requested if requested in ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25') else '2025-11-25'
        return {'protocolVersion': version, 'capabilities': {'tools': {}, 'resources': {}},
                'serverInfo': {'name': 'blackbox-entry-probe', 'version': '0.0.1'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [TOOL]}
    if method == 'resources/list':
        return {'resources': [{'uri': URI, 'name': 'Blackbox entry test', 'mimeType': MIME}]}
    if method == 'resources/templates/list':
        return {'resourceTemplates': []}
    if method == 'resources/read':
        if params.get('uri') != URI:
            raise ValueError('Unknown resource')
        return {'contents': [{'uri': URI, 'mimeType': MIME,
                 'text': Path(__file__).with_name('probe.html').read_text(encoding='utf-8'),
                 '_meta': {'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}}}]}
    if method == 'tools/call':
        if params.get('name') != 'open_probe' or params.get('arguments', {}) != {}:
            raise ValueError('Only the static probe with empty arguments is supported')
        return {'content': [{'type': 'text', 'text': 'Blackbox static entry test. No records accessed.'}],
                'structuredContent': {'probe': 'NOTES-024', 'recordsAccessed': False}}
    raise LookupError('Unknown method')


def handle(message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
    if 'id' not in message:
        return None
    reply = {'jsonrpc': '2.0', 'id': message['id']}
    try:
        params = message.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('Invalid params')
        reply['result'] = dispatch(message['method'], params)
    except ValueError:
        reply['error'] = {'code': -32602, 'message': 'Invalid params'}
    except LookupError:
        reply['error'] = {'code': -32601, 'message': 'Method not found'}
    except OSError:
        reply['error'] = {'code': -32603, 'message': 'Static resource unavailable'}
    return reply


def main():
    while True:
        line = sys.stdin.buffer.readline(LIMIT + 1)
        if not line:
            break
        if len(line) > LIMIT:
            # Reject the connection rather than interpreting any remaining fragments.
            return 1
        try:
            reply = handle(json.loads(line))
        except (ValueError, UnicodeError):
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
