"""NOTES-058 local mixed-stream fixtures; no writes to recorded events."""
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4
from verify_curl_errors import main, anomalies, psquote

CASES = {
    'redirect_limit': ('curl-redirect-limit', 47),
    'redirect_refused': ('curl-connect', 7),
    'redirect_empty': ('curl-empty-reply', 52),
    'redirect_reset': ('curl-receive', 56),
    'redirect_timeout': ('curl-timeout', 28),
    'interim_timeout': ('curl-timeout', 28),
    'interim_reset': ('curl-receive', 56),
    'success': (None, 0),
    'redirect_success': (None, 0),
    'interim_success': (None, 0),
    'body_spoof': (None, 0),
    'partial_body': (None, 18),
    'partial_line': (None, 18),
    'unfollowed': (None, 0),
    'read_log': (None, 0),
    'plain_text': (None, 0),
}
EXPECTED = {case: (rule, 'NOTES058' if case in ('read_log', 'plain_text') else 'HTTP/1.1')
            for case, (rule, _) in CASES.items()}
FAKE = 'curl: (18) end of response with 100 bytes missing'


def serve(root):
    refused = socket.socket(); refused.bind(('127.0.0.1', 0))

    class Server(ThreadingHTTPServer):
        def shutdown_request(self, request):
            if request in self.resets:
                self.resets.remove(request); self.close_request(request)
            else:
                super().shutdown_request(request)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            if self.path == '/stop':
                self.send_response(204); self.end_headers()
                threading.Thread(target=self.server.shutdown, daemon=True).start(); return
            case = self.path.split('/')[1]
            if case not in CASES:
                self.send_error(404); return
            self.close_connection = True
            final = '/final/' in self.path
            if case.startswith('interim_'):
                self.send_response_only(103, 'Early Hints'); self.end_headers(); self.wfile.flush()
            if ((case.startswith('redirect_') or case == 'unfollowed') and not final):
                target = (f'http://127.0.0.1:{refused.getsockname()[1]}/unused' if case == 'redirect_refused'
                          else self.path if case == 'redirect_limit' else self.path+'/final/')
                self.send_response(302); self.send_header('Location', target)
                # curl follows without exposing the discarded redirect entity.
                data = b'NOTES058 skipped redirect body\n'
            elif case.endswith('_timeout'):
                time.sleep(1); return
            elif case.endswith('_empty'):
                return
            elif case.endswith('_reset'):
                time.sleep(.1)
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('HH', 1, 0))
                self.server.resets.add(self.connection); return
            else:
                self.send_response(200)
                data = b'NOTES058 final body' + (b'\n' if case == 'partial_line' else b'')
                if case == 'body_spoof':
                    data = (FAKE+'\nHTTP/1.1 103 Early Hints\r\n\r\ncurl: (28) Connection timed out after 500 milliseconds\n').encode()
            self.send_header('Content-Length', str(len(data)+(100 if case.startswith('partial_') else 0)))
            self.send_header('Connection', 'close'); self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = Server(('127.0.0.1', 0), Handler); server.resets = set()
    timer = threading.Timer(1800, server.shutdown); timer.daemon = True; timer.start()
    (root/'ready.json').write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    try:
        server.serve_forever()
    finally:
        timer.cancel(); server.server_close(); refused.close()


def prepare(project, store):
    root = Path(tempfile.mkdtemp(prefix='blackbox-notes058-')); run = uuid4().hex
    with (root/'server-output.txt').open('wb') as log:
        proc = subprocess.Popen([sys.executable, __file__, 'serve', '--run-root', str(root)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(100):
        if (root/'ready.json').exists(): break
        if proc.poll() is not None: raise RuntimeError('fixture service failed')
        time.sleep(.05)
    port = json.loads((root/'ready.json').read_text())['port']
    commands, controls = {}, {}
    (root/'old.log').write_text('NOTES058\n'+FAKE, encoding='utf-8')
    for case, (rule, code) in CASES.items():
        flags = ' -L --max-redirs 2' if case.startswith('redirect_') else ''
        limit = '0.5' if case.endswith('_timeout') else '4'
        cmd = ('& '+psquote(shutil.which('curl.exe'))+" -q -sS --stderr - -i --noproxy '*' --max-time "+limit+flags
               +' '+psquote(f'http://127.0.0.1:{port}/{case}/{run}')+'; exit $LASTEXITCODE')
        if case == 'read_log': cmd = 'Get-Content -Raw -LiteralPath '+psquote(root/'old.log')
        if case == 'plain_text': cmd = "Write-Output 'NOTES058 example "+FAKE+"'"
        commands[case] = cmd
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-Command', cmd],
            capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = (result.stdout+result.stderr).decode('utf-8', errors='replace')
        (root/(case+'-control.txt')).write_bytes(raw.encode())
        hit = anomalies.detect({'tool_name': 'Bash', 'tool_input': {'command': cmd}, 'tool_response': raw})
        controls[case] = {'exit_code': result.returncode, 'expected_rule': rule,
                         'valid': result.returncode == code and (hit['rule'] if hit else None) == rule
                         and EXPECTED[case][1] in raw}
    with store.connect() as db: before = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    manifest = {'project': project, 'before_seq': before, 'commands': commands, 'controls': controls,
                'server_pid': proc.pid, 'stop_url': f'http://127.0.0.1:{port}/stop'}
    (root/'control.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'run_root': str(root), 'controls': controls}))
    return all(x['valid'] for x in controls.values())


if __name__ == '__main__':
    main(EXPECTED, prepare, serve)
