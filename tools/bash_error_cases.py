"""Controlled NOTES-049 operations. All exceptions come from real operations."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.request

HTTP_CODES = (400, 401, 403, 404, 408, 429, 500, 502, 503, 504)
EXPECTED = {
    'file_missing': 'FileNotFoundError', 'json_invalid': 'json.decoder.JSONDecodeError',
    'subprocess_timeout': 'subprocess.TimeoutExpired', 'file_locked': 'PermissionError',
    'dns_invalid': 'socket.gaierror', 'connect_timeout': 'TimeoutError',
    'read_timeout': 'TimeoutError', 'success': None, 'http200': None, 'http200_error_text': None,
    **{'http'+str(code): 'urllib.error.HTTPError' for code in HTTP_CODES},
}


def http_case(code, slow=False, error_text=False):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if slow:
                time.sleep(.8)
            try:
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b'error: synthetic application message' if error_text else b'NOTES049_HTTP_RESPONSE')
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:%d/' % server.server_port, timeout=.2 if slow else 3) as response:
            print(response.read().decode('ascii'))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def locked_file(root):
    # A share-mode rejection on a disposable file, not an ACL or policy change.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    path = root / 'locked.txt'
    handle = kernel.CreateFileW(str(path), 0x80000000 | 0x40000000, 0, None, 2, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        path.read_text(encoding='utf-8')
    finally:
        kernel.CloseHandle(handle)


def connect_timeout():
    # Fill the accept queue without accepting. A subsequent real connect must
    # time out; if this OS accepts all attempts the fixture fails explicitly.
    clients = []
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        try:
            for _ in range(16):
                client = socket.socket()
                clients.append(client)
                client.settimeout(.25)
                client.connect(server.getsockname())
        finally:
            for client in clients:
                client.close()
    raise RuntimeError('fixture could not produce an actual connect timeout')


def run(case, root):
    root = Path(root)
    if case == 'file_missing':
        (root / 'intentionally-absent.txt').read_text(encoding='utf-8')
    elif case == 'json_invalid':
        json.loads('{"NOTES049":')
    elif case == 'subprocess_timeout':
        subprocess.run([sys.executable, '-c', 'import time; time.sleep(2)'], timeout=.1,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    elif case == 'file_locked':
        locked_file(root)
    elif case == 'dns_invalid':
        socket.getaddrinfo('blackbox-notes049.invalid', 443, type=socket.SOCK_STREAM)
    elif case == 'connect_timeout':
        connect_timeout()
    elif case == 'read_timeout':
        http_case(200, slow=True)
    elif case == 'http200_error_text':
        http_case(200, error_text=True)
    elif case.startswith('http') and case[4:].isdigit():
        http_case(int(case[4:]))
    elif case == 'success':
        path = root / 'valid.json'
        path.write_text('{"value":1}', encoding='utf-8')
        assert json.loads(path.read_text(encoding='utf-8'))['value'] == 1
        print('NOTES049_SUCCESS')
    else:
        raise ValueError('unknown fixture')
