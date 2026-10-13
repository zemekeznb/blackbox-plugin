"""NOTES-048: bounded metadata-only app-server capability probe.

Never resumes/creates a thread, starts a model turn, or reads real item bodies.
The item-list request always uses an uncreated sentinel identity. Results go to
stdout as a fixed metadata summary; no raw response or exception text is logged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

SENTINEL = '00000000-0000-4000-8000-000000000048'
MAX_LINE = 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024


def schema_report(root):
    root = Path(root)
    def read(name):
        path = root / name
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('schema too large')
        return json.loads(path.read_text(encoding='utf-8'))
    request = read('ClientRequest.json')
    methods = {method for item in request['oneOf']
               for method in item.get('properties', {}).get('method', {}).get('enum', [])}
    notification = read('v2/ItemCompletedNotification.json')
    definitions = notification['definitions']
    mcp = next(item for item in definitions['ThreadItem']['oneOf']
               if item.get('properties', {}).get('type', {}).get('enum') == ['mcpToolCall'])
    params = read('v2/ThreadItemsListParams.json')['properties']
    return {
        'read_methods': sorted(methods & {'thread/read', 'thread/turns/list', 'thread/items/list'}),
        'passive_subscribe_named_method': 'thread/subscribe' in methods,
        'item_request_fields': sorted(params),
        'completed_identity_fields': sorted(set(notification['properties']) & {'threadId', 'turnId'}),
        'mcp_item_fields': sorted(mcp['properties']),
        'mcp_result_fields': sorted(definitions['McpToolCallResult']['properties']),
        'mcp_status_schema': definitions['McpToolCallStatus'],
    }


def requests(thread_id):
    return [
        ('thread/read', {'threadId': thread_id, 'includeTurns': False}),
        ('thread/turns/list', {'threadId': thread_id, 'itemsView': 'notLoaded', 'limit': 1}),
        # Never substitute a real ID here: this API can return reasoning and
        # messages as well as tools, and has no item-type filter in this version.
        ('thread/items/list', {'threadId': SENTINEL, 'turnId': SENTINEL, 'limit': 1}),
    ]


def summarize(reply, method, thread_id, project):
    error = reply.get('error')
    if isinstance(error, dict):
        message = str(error.get('message', '')).lower()
        if 'unsupported' in message or 'not supported' in message:
            kind = 'unsupported'
        elif 'not loaded' in message:
            kind = 'not_loaded'
        elif 'not found' in message or 'no rollout found' in message:
            kind = 'not_found'
        elif 'experimentalapi' in message:
            kind = 'experimental_capability'
        else:
            kind = 'other'
        code = error.get('code')
        return {'rpc_error': kind, 'code': code if type(code) is int else None}
    result = reply.get('result')
    if not isinstance(result, dict):
        return {'invalid_response': True}
    if method == 'thread/read':
        thread = result.get('thread')
        if not isinstance(thread, dict):
            return {'invalid_response': True}
        cwd = thread.get('cwd')
        status = thread.get('status')
        status = status.get('type') if isinstance(status, dict) else None
        return {'identity_matches': thread.get('id') == thread_id,
                'cwd_matches': isinstance(cwd, str) and os.path.normcase(cwd) == os.path.normcase(project),
                'status': status if status in ('notLoaded', 'idle', 'active', 'systemError') else 'unknown',
                'returned_turn_count': len(thread.get('turns', [])) if isinstance(thread.get('turns'), list) else None}
    data = result.get('data')
    if not isinstance(data, list):
        return {'invalid_response': True}
    if method == 'thread/turns/list':
        return {'returned_turn_count': len(data),
                'item_bodies_absent': all(isinstance(turn, dict) and turn.get('items') in (None, []) for turn in data)}
    return {'sentinel_item_count': len(data), 'real_items_requested': False}


def probe(thread_id, project, timeout=15, command=None):
    command = command or [shutil.which('codex') or 'codex', 'app-server', '--stdio']
    report = {'scope': 'fresh_app_server_metadata_only', 'model_turn_started': False,
              'capture_path_validated': False, 'results': {}}
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    def worker():
        consumed = 0
        def call(identifier, method, params):
            nonlocal consumed
            process.stdin.write((json.dumps({'id': identifier, 'method': method, 'params': params})+'\n').encode())
            process.stdin.flush()
            while True:
                line = process.stdout.readline(MAX_LINE + 1)
                consumed += len(line)
                if not line or len(line) > MAX_LINE or consumed > MAX_TOTAL:
                    raise ValueError('bounded response unavailable')
                reply = json.loads(line)
                if not isinstance(reply, dict) or ('id' in reply and 'method' in reply):
                    raise ValueError('unexpected interactive response')
                if reply.get('id') == identifier:
                    return reply
        try:
            initial = call(1, 'initialize', {'clientInfo': {'name': 'blackbox-result-api-probe', 'version': '1'},
                                            'capabilities': {'experimentalApi': True}})
            if 'error' in initial:
                report['initialize_failed'] = True
                return
            process.stdin.write(b'{"method":"initialized"}\n')
            process.stdin.flush()
            for identifier, (method, params) in enumerate(requests(thread_id), 2):
                summary = summarize(call(identifier, method, params), method, thread_id, project)
                report['results'][method] = summary
                if method == 'thread/read' and not (summary.get('identity_matches') is True
                        and summary.get('cwd_matches') is True and summary.get('returned_turn_count') == 0):
                    report['scope_not_confirmed'] = True
                    return
                if method == 'thread/turns/list' and summary.get('item_bodies_absent') is False:
                    report['unexpected_body'] = True
                    return
        except (OSError, ValueError, TypeError, RecursionError):
            report['probe_failed'] = True
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        thread.join(timeout)
        if thread.is_alive():
            report['timed_out'] = True
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        thread.join(1)
        if not thread.is_alive():
            process.stdin.close()
            process.stdout.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema-dir', type=Path, required=True)
    parser.add_argument('--live-metadata', action='store_true')
    parser.add_argument('--thread-id')
    parser.add_argument('--project')
    args = parser.parse_args()
    result = {'schema': schema_report(args.schema_dir)}
    if args.live_metadata:
        if not args.thread_id or not args.project:
            parser.error('live metadata requires an explicitly selected thread and project')
        result['live'] = probe(args.thread_id, args.project)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
