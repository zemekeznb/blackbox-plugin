"""Selected curl stderr formats, with bounded literal command identity."""
import re
from urllib.parse import urlsplit
from blackbox_shell_errors import direct_tokens
from blackbox_business_errors import curl_request


def network_command(content, tokens=None):
    # Existing JSON POST contract includes -q and response headers.
    if curl_request(content, tokens=tokens) is not None:
        return True
    tokens = direct_tokens(content) if tokens is None else tokens
    if (len(tokens) < 3 or re.split(r'[/\\]', tokens[0])[-1].lower() != 'curl.exe'
            or tokens[1] not in ('-q', '--disable') or '[REDACTED]' in repr(tokens)):
        return False
    headers, discard, url, index = False, False, None, 2
    while index < len(tokens):
        option = tokens[index]
        index += 1
        if option in ('-s', '-S', '-sS', '--silent', '--show-error', '-4', '--ipv4'):
            continue
        if option in ('-i', '--include'):
            headers = True
            continue
        if option in ('-o', '--output', '--max-time', '--connect-timeout', '--noproxy'):
            if index == len(tokens):
                return False
            value = tokens[index]
            index += 1
            if option in ('-o', '--output'):
                if discard or value.upper() != 'NUL':
                    return False
                discard = True
            elif option == '--noproxy':
                if value != '*':
                    return False
            elif not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
                return False
        elif option.startswith(('http://', 'https://')) and url is None:
            try:
                parsed = urlsplit(option)
                if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                    return False
            except ValueError:
                return False
            if any(char in option for char in '{}[]'):
                return False
            url = option
        else:
            return False
    return url is not None and (headers or discard)


def curl_error(content, *, tokens=None):
    body = content.get('tool_response')
    if (content.get('tool_name') != 'Bash' or not isinstance(body, str)
            or content.get('_coverage', {}).get('truncated_fields')
            or not network_command(content, tokens=tokens)):
        return None
    # Entire response, not a matching line inside HTTP data or an old log.
    line = body.rstrip('\r\n')
    patterns = (
        (r'curl: \(6\) Could not resolve host: [^\s]+', 'curl-dns', 'curl 返回主机解析失败'),
        (r'curl: \(7\) Failed to connect to [^\r\n]+ (?:port \d+|after \d+ ms): [^\r\n]+',
         'curl-connect', 'curl 返回连接失败'),
        (r'curl: \(28\) (?:Connection timed out after \d+ milliseconds|'
         r'Operation timed out after \d+ milliseconds with \d+(?: out of \d+)? bytes received|'
         r'Failed to connect to [^\r\n]+ after \d+ ms: Timeout was reached)',
         'curl-timeout', 'curl 返回超时'),
    )
    for pattern, rule, title in patterns:
        if re.fullmatch(pattern, line):
            return {'rule': rule, 'title': title, 'evidence': body,
                    'signal': {'kind': 'diagnostic_format', 'source_field': 'tool_response',
                               'format': rule, 'segment': 'full', 'truncated': False,
                               'independently_verified': False}}
    return None
