"""Narrow, explicit HTTP application error contracts. Never guess custom fields."""
import json
import re
from urllib.parse import urlsplit

from tas_notes_shell_errors import direct_tokens

MAX_BATCH = 100


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def invalid(value):
        raise ValueError('non-JSON constant')

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, TypeError, RecursionError):
        return None


def curl_request(content, tokens=None):
    """Literal Windows curl POST with inline JSON and headers included in stdout.

    An allowlist prevents file/config reads, multiple transfers, redirection and
    stdout rewriting from being mistaken for one response. This only inspects
    saved inputs; it never executes a command or opens a referenced file/URL.
    """
    tokens = direct_tokens(content) if tokens is None else tokens
    if (len(tokens) < 3 or re.split(r'[/\\]', tokens[0])[-1].lower() != 'curl.exe'
            or tokens[1] not in ('-q', '--disable') or '[REDACTED]' in repr(tokens)):
        return None
    included, data, url, method, headers = False, None, None, None, {}
    index = 2
    while index < len(tokens):
        option = tokens[index]
        index += 1
        if option in ('-s', '-S', '-sS', '--silent', '--show-error'):
            continue
        if option in ('-i', '--include'):
            included = True
            continue
        if option in ('-X', '--request', '-H', '--header', '--data-raw', '--json',
                      '--max-time', '--connect-timeout', '--noproxy'):
            if index == len(tokens):
                return None
            value = tokens[index]
            index += 1
            if option in ('-X', '--request'):
                if method is not None or value != 'POST':
                    return None
                method = value
            elif option in ('-H', '--header'):
                if ':' not in value:
                    return None
                name, value = value.split(':', 1)
                name = name.strip().lower()
                if name not in ('content-type', 'accept') or name in headers:
                    return None
                headers[name] = value.strip().lower()
            elif option in ('--data-raw', '--json'):
                if data is not None or (option == '--json' and value.startswith('@')):
                    return None
                data = value
                if option == '--json':
                    if headers:
                        return None
                    headers = {'content-type': 'application/json', 'accept': 'application/json'}
            elif option == '--noproxy':
                if value != '*':
                    return None
            elif not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
                return None
        elif option.startswith(('http://', 'https://')) and url is None:
            try:
                parsed = urlsplit(option)
                if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                    return None
            except ValueError:
                return None
            # curl globbing can silently create multiple requests.
            if any(char in option for char in '{}[]'):
                return None
            url = option
        else:
            return None
    if not included or url is None or data is None or headers.get('content-type') != 'application/json':
        return None
    request = strict_json(data)
    if not isinstance(request, (dict, list)):
        return None
    return request, headers.get('accept', '')


def http_json(body):
    # Only an intact final response, with no surrounding logs/progress.
    # Normalise line endings for parsing only. Evidence keeps the saved bytes.
    lines = body.replace('\r\r\n', '\n').replace('\r\n', '\n').split('\n')
    status = re.fullmatch(r'HTTP/(?:1\.[01]|2|3) ([245][0-9]{2})(?: [^\r\n]*)?', lines[0])
    if not status:
        return None
    headers = {}
    index = 1
    while index < len(lines) and lines[index]:
        line = lines[index]
        if not re.fullmatch(r'[A-Za-z0-9-]+:.*', line):
            return None
        key, value = line.split(':', 1)
        key = key.lower()
        if key in headers:
            return None
        headers[key] = value.strip().lower()
        index += 1
    if index == len(lines):
        return None
    media = headers.get('content-type', '').split(';', 1)[0].strip()
    if media not in ('application/json', 'application/graphql-response+json'):
        return None
    result = strict_json('\n'.join(lines[index + 1:]))
    return (int(status[1]), media, result) if isinstance(result, (dict, list)) else None


def rpc_request(request):
    return (isinstance(request, dict) and request.get('jsonrpc') == '2.0'
            and isinstance(request.get('method'), str) and bool(request['method'])
            and ('params' not in request or isinstance(request['params'], (list, dict))))


def rpc_id(value):
    # bool and int compare equal in Python; protocol IDs must keep their types.
    return (type(value).__name__, value) if type(value) in (str, int) and value != '' else None


def rpc_result(request, result):
    if (not rpc_request(request) or rpc_id(request.get('id')) is None
            or not isinstance(result, dict) or result.get('jsonrpc') != '2.0'
            or rpc_id(request['id']) != rpc_id(result.get('id'))
            or ('result' in result) == ('error' in result)):
        return None
    if 'result' in result:
        return False
    error = result['error']
    if (not isinstance(error, dict) or type(error.get('code')) is not int
            or not isinstance(error.get('message'), str) or not error['message'].strip()):
        return None
    return True


def rpc_error(request, result):
    if not isinstance(request, list):
        if rpc_result(request, result) is True:
            return 'JSON-RPC 2.0 返回应用错误', 'json-rpc-error', 'http_body.error'
        return None
    if (not 1 <= len(request) <= MAX_BATCH or not isinstance(result, list)
            or not 1 <= len(result) <= MAX_BATCH):
        return None
    expected = {}
    for item in request:
        if not rpc_request(item):
            return None
        if 'id' not in item:
            continue  # A notification has no response; never guess its outcome.
        key = rpc_id(item['id'])
        if key is None or key in expected:
            return None
        expected[key] = item
    if len(expected) != len(result):
        return None
    seen, errors = set(), 0
    for item in result:
        key = rpc_id(item.get('id')) if isinstance(item, dict) else None
        if key not in expected or key in seen:
            return None
        error = rpc_result(expected[key], item)
        if error is None:
            return None
        seen.add(key)
        errors += int(error)
    if not errors:
        return None
    return (f'JSON-RPC 2.0 批量返回 {errors} 项错误（共 {len(result)} 项响应）',
            'json-rpc-batch-error', 'http_body[].error')


def graphql_accept(accept):
    # Explicit protocol declaration only, not */*, an arbitrary endpoint name,
    # or an unrelated errors field. A q=0 declaration does not accept GraphQL.
    for entry in accept.split(','):
        parts = [part.strip() for part in entry.split(';')]
        if parts[0] != 'application/graphql-response+json':
            continue
        if len(parts) == 1:
            return True
        if len(parts) == 2 and re.fullmatch(r'q=(?:0(?:\.[0-9]{1,3})?|1(?:\.0{1,3})?)', parts[1]):
            if float(parts[1][2:]) > 0:
                return True
    return False


def graphql_error(request, result, media, status=200, accept=''):
    # Generic JSON on non-2xx may be a gateway error, even when GraphQL was
    # requested. Require the GraphQL-specific response media type in that case.
    identified = (media == 'application/graphql-response+json'
                  or media == 'application/json' and status < 300 and graphql_accept(accept))
    if (not identified or not isinstance(request, dict) or not isinstance(result, dict)
            or 'jsonrpc' in request
            or not isinstance(request.get('query'), str) or not request['query'].strip()
            or not set(request) <= {'query', 'operationName', 'variables', 'extensions'}
            or not set(result) <= {'data', 'errors', 'extensions'}):
        return None
    for key in ('variables', 'extensions'):
        if key in request and request[key] is not None and not isinstance(request[key], dict):
            return None
    if request.get('operationName') is not None and not isinstance(request['operationName'], str):
        return None
    if 'extensions' in result and not isinstance(result['extensions'], dict):
        return None
    errors = result.get('errors')
    if not isinstance(errors, list) or not errors:
        return None
    for error in errors:
        if (not isinstance(error, dict) or not isinstance(error.get('message'), str)
                or not error['message'].strip()):
            return None
        if 'data' in result and 'path' not in error:
            return None
        if 'path' in error and (not isinstance(error['path'], list) or not error['path']
                or any(not (isinstance(part, str) and part or type(part) is int and part >= 0)
                       for part in error['path'])):
            return None
        if 'locations' in error and (not isinstance(error['locations'], list) or not error['locations']
                or any(not isinstance(loc, dict) or type(loc.get('line')) is not int or loc['line'] < 1
                       or type(loc.get('column')) is not int or loc['column'] < 1 for loc in error['locations'])):
            return None
        if 'extensions' in error and not isinstance(error['extensions'], dict):
            return None
    if 'data' not in result:
        title = 'GraphQL 返回请求错误'
    elif result['data'] is None:
        title = 'GraphQL 返回执行错误（data 为 null）'
    elif isinstance(result['data'], dict):
        title = 'GraphQL 返回错误及部分数据'
    else:
        return None
    return title, 'graphql-errors', 'http_body.errors'


def business_error(content, *, tokens=None, response_body=None):
    if content.get('tool_name') != 'Bash':
        return None
    body = content.get('tool_response')
    if (not isinstance(body, str)
            or 'tool_response' in content.get('_coverage', {}).get('truncated_fields', [])):
        return None
    context = curl_request(content, tokens=tokens)
    if context is not None:
        from tas_notes_sse_errors import sse_error
        stream = sse_error(*context, body if response_body is None else response_body)
        if stream:
            title, rule, field = stream
            return {'title': title, 'rule': rule, 'evidence': body,
                    'signal': {'kind': 'application_reported_error', 'source_field': 'tool_response',
                               'contract_field': field, 'format': rule, 'segment': 'full',
                               'truncated': False, 'independently_verified': False}}
    response = http_json(body if response_body is None else response_body) if context is not None else None
    if response is None:
        return None
    request, accept = context
    status, media, result = response
    match = (rpc_error(request, result) if media == 'application/json' else None)
    match = match or graphql_error(request, result, media, status, accept)
    if not match:
        return None
    title, rule, field = match
    return {'title': title, 'rule': rule, 'evidence': body,
            'signal': {'kind': 'application_reported_error', 'source_field': 'tool_response',
                       'contract_field': field, 'format': rule, 'segment': 'full',
                       'truncated': False, 'independently_verified': False}}
