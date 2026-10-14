"""Bounded curl response chains. Preserve observations, never infer causes."""
import re
from urllib.parse import urlsplit
from blackbox_curl_errors import network_command, curl_error
from blackbox_shell_errors import direct_tokens
from blackbox_business_errors import business_error
from blackbox_transport_errors import transport_error

STATUS = r'HTTP/(?:1\.[01]|2|3) ([1-5][0-9]{2})(?: ([^\r\n]*))?'
FAIL = r'curl: \(22\) The requested URL returned error: ([45][0-9]{2})'
VALUES = {'-X', '--request', '-H', '--header', '--data-raw', '--json',
          '--max-time', '--connect-timeout', '--noproxy', '-o', '--output'}


def command_context(content):
    tokens = direct_tokens(content)
    if len(tokens) < 3:
        return None
    clean, fail, follow, proxy, tunnel = tokens[:2], None, False, None, False
    index = 2
    while index < len(tokens):
        option = tokens[index]
        index += 1
        if option in ('-f', '--fail', '--fail-with-body'):
            if fail is not None:
                return None
            fail = 'body' if option == '--fail-with-body' else 'no-body'
        elif option in ('-L', '--location'):
            if follow:
                return None
            follow = True
        elif option in ('-p', '--proxytunnel'):
            if tunnel:
                return None
            tunnel = True
        elif option == '--stderr':
            if index == len(tokens) or tokens[index] != '-':
                return None
            index += 1
        elif option in ('--max-redirs', '-x', '--proxy'):
            if index == len(tokens):
                return None
            value = tokens[index]
            index += 1
            if option == '--max-redirs':
                if not re.fullmatch(r'[0-9]{1,2}', value) or int(value) > 20:
                    return None
            else:
                try:
                    parsed = urlsplit(value)
                    if (proxy or parsed.scheme != 'http' or not parsed.hostname
                            or parsed.username or parsed.password or parsed.query or parsed.fragment
                            or parsed.path not in ('', '/') or not parsed.port
                            or any(c in value for c in '{}[]')):
                        return None
                except ValueError:
                    return None
                proxy = value
        else:
            clean.append(option)
            if option in VALUES:
                if index == len(tokens):
                    return None
                clean.append(tokens[index])
                index += 1
    if tunnel and proxy is None:
        return None
    if not network_command(content, tokens=clean):
        return None
    urls = [x for x in clean if x.startswith(('http://', 'https://'))]
    return {'tokens': clean, 'fail': fail, 'follow': follow,
            'connect': bool(proxy and (tunnel or urls[-1].startswith('https://'))),
            'headers': any(x in clean for x in ('-i', '--include')),
            'discard': any(x in clean for x in ('-o', '--output'))}


def header_block(lines, start):
    if start >= len(lines):
        return None
    status = re.fullmatch(STATUS, lines[start])
    if not status:
        return None
    index, headers = start + 1, {}
    while index < len(lines) and lines[index]:
        if not re.fullmatch(r"[!#$%&'*+.^_\x60|~0-9A-Za-z-]+:[\t\x20-\x7e\x80-\uffff]*", lines[index]):
            return None
        key, value = lines[index].split(':', 1)
        headers.setdefault(key.lower(), []).append(value.strip())
        index += 1
    if index == len(lines):
        return None
    if ('content-length' in headers and 'transfer-encoding' in headers
            or any(len(headers.get(key, [])) > 1 for key in ('content-length', 'transfer-encoding', 'location'))):
        return None
    if 'content-length' in headers and not re.fullmatch(r'[0-9]+', headers['content-length'][0]):
        return None
    return int(status[1]), status[2] or '', headers, index + 1


def observation(body, status, diagnostic=False):
    rule = 'curl-http-fail' if diagnostic else 'http-status-error'
    return {'rule': rule, 'title': ('curl 报告 HTTP ' if diagnostic else 'HTTP 返回 ') + str(status) + ' 错误状态',
            'evidence': body, 'signal': {'kind': 'diagnostic_format' if diagnostic else 'http_reported_error',
            'source_field': 'tool_response', 'contract_field': 'curl_22' if diagnostic else 'http_status',
            'format': rule, 'segment': 'full', 'truncated': False, 'independently_verified': False}}


def mixed_error(content, context):
    """A diagnostic before HTTP, or after bodyless intermediate headers only.

    No byte-count slicing of saved decoded text: a partial entity can itself
    contain a complete diagnostic, and host stream ordering is not guaranteed.
    """
    if not context['headers'] or context['discard']:
        return None
    body = content['tool_response']
    text = body.replace('\r\r\n', '\n').replace('\r\n', '\n')

    def diagnostic(value):
        hit = transport_error(value, context) or curl_error(
            dict(content, tool_response=value), tokens=context['tokens'])
        if hit:
            hit['evidence'] = body
            hit['signal']['contract_field'] = 'curl_diagnostic_outside_http_body'
        return hit

    # A saved stderr prefix cannot have come from the included HTTP entity.
    prefix, separator, tail = text.partition('\nHTTP/')
    leading = diagnostic(prefix) if separator else None
    lines = ('HTTP/' + tail if leading else text).split('\n')
    index, redirected, connected = 0, False, False
    for _ in range(32):
        block = header_block(lines, index)
        if block is None:
            return None
        status, reason, headers, index = block
        if status < 200:
            if status == 101 or 'content-length' in headers or 'transfer-encoding' in headers:
                return None
        elif (status == 200 and reason.lower() == 'connection established'
              and context['connect'] and not connected and not redirected):
            if 'content-length' in headers or 'transfer-encoding' in headers:
                return None
            connected = True
        elif status in (301, 302, 303, 307, 308) and context['follow']:
            if not headers.get('location', [''])[0]:
                return None
            redirected = True
        else:
            # Once an entity can start, never search it for a suffix diagnostic.
            if leading and not any(re.match(STATUS + r'$|curl: \([0-9]+\)', line)
                                   for line in lines[index:]):
                return leading
            return None
        rest = '\n'.join(lines[index:])
        if leading:
            if not rest:
                return leading
        else:
            trailing = diagnostic(rest)
            if trailing:
                return trailing
    return None


def http_error(content):
    body = content.get('tool_response')
    if (content.get('tool_name') != 'Bash' or not isinstance(body, str)
            or content.get('_coverage', {}).get('truncated_fields')):
        return None
    context = command_context(content)
    if context is None:
        return None
    transport = transport_error(body, context)
    if transport:
        return transport
    network = curl_error(content, tokens=context['tokens'])
    if network:
        return network
    mixed = mixed_error(content, context)
    if mixed:
        return mixed
    text = body.replace('\r\r\n', '\n').replace('\r\n', '\n')
    # Diagnostic-only is safe only when body output was suppressed/discarded.
    only = re.fullmatch(FAIL, text.rstrip('\n'))
    if only and context['fail'] and (context['fail'] == 'no-body' or context['discard']):
        return observation(body, int(only[1]), True)
    if not context['headers'] or context['discard']:
        return None
    lines = text.split('\n')
    diagnostic = None
    # Host may merge stderr before or after stdout. Never remove a middle line.
    if context['fail']:
        first = re.fullmatch(FAIL, lines[0])
        if first:
            diagnostic = int(first[1]);lines = lines[1:]
        else:
            end = len(lines)
            while end and lines[end - 1] == '':
                end -= 1
            last = re.fullmatch(FAIL, lines[end - 1]) if end else None
            if last:
                diagnostic = int(last[1]);lines = lines[:end - 1]
    index, redirected, connected = 0, False, False
    for _ in range(32):
        start = index
        block = header_block(lines, index)
        if block is None:
            return None
        status, reason, headers, index = block
        if status < 200:
            if status == 101 or 'content-length' in headers or 'transfer-encoding' in headers:
                return None
            continue
        if (status == 200 and reason.lower() == 'connection established'
                and context['connect'] and not connected and not redirected):
            if 'content-length' in headers or 'transfer-encoding' in headers:
                return None
            connected = True
            continue
        if status in (301, 302, 303, 307, 308) and context['follow']:
            if not headers.get('location', [''])[0]:
                return None
            redirected = True
            continue
        if any(re.match(STATUS + r'$|curl: \([0-9]+\)', line) for line in lines[index:]):
            return None
        if diagnostic is not None and (diagnostic != status or not 400 <= status <= 599):
            return None
        # Redirects can change POST semantics; don't reuse the original RPC contract.
        if not redirected and context['fail'] != 'no-body':
            specific = business_error(content, tokens=context['tokens'], response_body='\n'.join(lines[start:]))
            if specific:
                return specific
        if 400 <= status <= 599:
            return observation(body, status)
        return None
    return None
