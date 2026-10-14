"""Explicit native curl write-out contract; never infer state from body text."""
from blackbox_shell_errors import direct_tokens
from blackbox_business_errors import strict_json

FORMAT = r'\nBLACKBOX_CURL_RESULT=%{json}\n'
MARKER = '\nBLACKBOX_CURL_RESULT='


def curl_result(content):
    from blackbox_http_errors import command_context, http_error, observation
    body = content.get('tool_response')
    if (content.get('tool_name') != 'Bash' or not isinstance(body, str)
            or content.get('_coverage', {}).get('truncated_fields')):
        return None
    tokens = direct_tokens(content)
    positions = [i for i, value in enumerate(tokens) if value in ('-w', '--write-out')]
    if len(positions) != 1:
        return None
    index = positions[0]
    if index < 2 or index + 1 >= len(tokens) or tokens[index + 1] != FORMAT:
        return None
    clean = tokens[:index] + tokens[index + 2:]
    # One stream is essential: host merging can place stderr after the trailer.
    if clean.count('--stderr') != 1 or clean.index('--stderr') + 1 >= len(clean) or clean[clean.index('--stderr')+1] != '-':
        return None
    command = ' '.join("'" + value.replace("'", "''") + "'" for value in clean)
    stripped = dict(content, tool_input={'command': '& ' + command})
    context = command_context(stripped)
    if context is None:
        return None
    text = body.replace('\r\r\n', '\n').replace('\r\n', '\n')
    prefix, marker, trailer = text.rpartition(MARKER)
    if not marker or not trailer.endswith('\n') or '\n' in trailer[:-1]:
        return None
    value = strict_json(trailer[:-1])
    if not isinstance(value, dict):
        return None
    code, message = value.get('exitcode'), value.get('errormsg')
    urls = [x for x in context['tokens'] if x.startswith(('http://', 'https://'))]
    if (type(code) is not int or not 0 <= code <= 99 or value.get('url') != urls[-1]
            or type(value.get('response_code')) is not int or not 0 <= value['response_code'] <= 599
            or code and (not isinstance(message, str) or not message.strip())
            or not code and message not in (None, '')):
        return None
    if code:
        return {'rule': 'curl-transfer-result', 'title': f'curl 报告传输失败（代码 {code}）',
                'evidence': body, 'signal': {'kind': 'tool_reported_error', 'source_field': 'tool_response',
                'contract_field': 'curl_write_out.exitcode', 'format': 'curl-transfer-result',
                'segment': 'full', 'truncated': False, 'independently_verified': False}}
    # Zero transfer status doesn't establish application success. Reuse HTTP
    # and protocol checks without letting a body-forged trailer override curl.
    stripped['tool_response'] = prefix
    hit = http_error(stripped)
    if hit:
        hit['evidence'] = body
    elif 400 <= value['response_code'] <= 599:
        hit = observation(body, value['response_code'])
        hit['signal']['contract_field'] = 'curl_write_out.response_code'
    return hit
