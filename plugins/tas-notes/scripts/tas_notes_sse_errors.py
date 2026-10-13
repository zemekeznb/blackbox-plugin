"""Bounded GraphQL SSE distinct-connection results; no generic SSE heuristics."""
import re
from tas_notes_business_errors import strict_json, graphql_error


def sse_error(request, accept, body):
    # An explicit single-operation POST and the response media type establish
    # this limited contract. Multiplex and incremental payloads are excluded.
    if (accept != 'text/event-stream' or not isinstance(request, dict)
            or not isinstance(request.get('query'), str) or not request['query'].strip()
            or not set(request) <= {'query', 'operationName', 'variables', 'extensions'}):
        return None
    if any(key in request and request[key] is not None and not isinstance(request[key], dict)
           for key in ('variables', 'extensions')):
        return None
    if request.get('operationName') is not None and not isinstance(request['operationName'], str):
        return None
    text = body.replace('\r\r\n', '\n').replace('\r\n', '\n')
    head, sep, stream = text.partition('\n\n')
    lines = head.split('\n')
    if not sep or not re.fullmatch(r'HTTP/(?:1\.[01]|2|3) 200(?: [^\r\n]*)?', lines[0]):
        return None
    headers = {}
    for line in lines[1:]:
        if not re.fullmatch(r'[A-Za-z0-9-]+:.*', line):
            return None
        key, value = line.split(':', 1); key = key.lower()
        if key in headers:
            return None
        headers[key] = value.strip().lower()
    if (headers.get('content-type') not in ('text/event-stream', 'text/event-stream; charset=utf-8')
            or 'content-length' in headers and 'transfer-encoding' in headers
            or not stream.endswith('\n\n') or '\r' in stream):
        return None
    completed, results, errors, partial = False, 0, 0, False
    blocks = stream.split('\n\n')
    if len(blocks) > 202:
        return None
    for block in blocks:
        if not block:
            continue
        fields, data = {}, []
        for line in block.split('\n'):
            if line.startswith(':'):
                continue
            key, colon, value = line.partition(':')
            if not colon or key not in ('event', 'data'):
                return None
            if value.startswith(' '): value = value[1:]
            if key == 'data': data.append(value)
            elif key in fields: return None
            else: fields[key] = value
        if not fields and not data:
            continue
        if completed:
            return None
        if fields.get('event') == 'complete' and (not data or data == ['']):
            completed = True
            continue
        if fields.get('event') != 'next' or not data:
            return None
        result = strict_json('\n'.join(data)); results += 1
        if not isinstance(result, dict) or not set(result) <= {'data', 'errors', 'extensions'}:
            return None
        if 'errors' in result:
            if not graphql_error(request, result, 'application/graphql-response+json'):
                return None
            errors += 1
            partial |= isinstance(result.get('data'), dict)
        elif ('data' not in result or not (result['data'] is None or isinstance(result['data'], dict))
              or 'extensions' in result and not isinstance(result['extensions'], dict)):
            return None
    if not completed or not errors or results > 100:
        return None
    return (f'GraphQL SSE 返回 {errors} 项错误结果' + ('（含部分数据）' if partial else ''),
            'graphql-sse-errors', 'http_body.next.data.errors')
