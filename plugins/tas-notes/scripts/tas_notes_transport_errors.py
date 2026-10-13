"""Complete selected curl diagnostics only, never scan response body lines."""
import re

CERT_HELP = ('More details here: https://curl.se/docs/sslcerts.html\n\n'
             'curl failed to verify the legitimacy of the server and therefore could not\n'
             'establish a secure connection to it. To learn more about this situation and\n'
             'how to fix it, please visit the webpage mentioned above.')


def transport_error(body, context):
    text = body.replace('\r\r\n', '\n').replace('\r\n', '\n').rstrip('\n')
    https = any(t.startswith('https://') for t in context['tokens'])
    patterns = (
        (35, r'(?:schannel: (?:next )?InitializeSecurityContext failed: [^\n]+|'
             r'(?:OpenSSL|LibreSSL)/[^\n]+: error:[^\n]+)', 'curl-tls', 'curl 返回 TLS 握手失败', https),
        (60, r'(?:schannel: SEC_E_[A-Z_]+ \(0x[0-9A-Fa-f]+\) - [^\n]+|'
             r'SSL certificate problem: [^\n]+)', 'curl-certificate', 'curl 返回证书验证失败', https),
        (18, r'(?:end of response with \d+ bytes missing|transfer closed with outstanding read data remaining)',
             'curl-partial', 'curl 返回响应接收不完整', True),
        (56, r'Recv failure: (?:Connection was reset|Connection reset by peer)',
             'curl-receive', 'curl 返回连接重置（接收失败）', True),
        (47, r'Maximum \(\d+\) redirects followed',
             'curl-redirect-limit', 'curl 返回重定向次数超限', context['follow']),
        (52, r'Empty reply from server', 'curl-empty-reply', 'curl 返回服务器空回复错误', True),
    )
    for code, pattern, rule, title, allowed in patterns:
        suffix = r'(?:\n'+re.escape(CERT_HELP)+r')?' if code == 60 else ''
        if allowed and re.fullmatch(r'curl: \('+str(code)+r'\) '+pattern+suffix, text):
            return {'rule': rule, 'title': title, 'evidence': body,
                    'signal': {'kind': 'diagnostic_format', 'source_field': 'tool_response',
                               'format': rule, 'segment': 'full', 'truncated': False,
                               'independently_verified': False}}
    return None
