"""NOTES-059 self-contained CONNECT emulator, never forwards traffic."""
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

CASES = {'timeout': ('curl-timeout', 28), 'reset': ('curl-receive', 56),
         'empty': ('curl-empty-reply', 52), 'interim': ('curl-timeout', 28),
         'success': (None, 0), 'spoof': (None, 0), 'partial': (None, 18)}
EXPECTED = {case: (rule, 'HTTP/1.1 200 Connection established') for case, (rule, _) in CASES.items()}


def serve(root):
    class Server(ThreadingHTTPServer):
        def shutdown_request(self, request):
            if request in self.resets:
                self.resets.remove(request); self.close_request(request)
            else:
                super().shutdown_request(request)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_CONNECT(self):
            # No DNS resolution or socket connection to the named destination.
            self.send_response_only(200, 'Connection established'); self.end_headers()
            self.close_connection = False

        def do_GET(self):
            self.close_connection = True
            if self.path == '/stop':
                self.send_response(204); self.end_headers()
                threading.Thread(target=self.server.shutdown, daemon=True).start(); return
            case = self.path.split('/')[1]
            if case not in CASES:
                self.send_error(404); return
            if case == 'interim':
                self.send_response_only(103, 'Early Hints'); self.end_headers(); self.wfile.flush()
            if case in ('timeout', 'interim'):
                time.sleep(1); return
            if case == 'reset':
                time.sleep(.1)
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('HH', 1, 0))
                self.server.resets.add(self.connection); return
            if case == 'empty': return
            data = b'NOTES059 body\n'
            if case == 'spoof':
                data = b'curl: (56) Recv failure: Connection was reset\nHTTP/1.1 200 Connection established\r\n\r\n'
            self.send_response(200); self.send_header('Content-Length', str(len(data)+(100 if case == 'partial' else 0)))
            self.send_header('Connection', 'close'); self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args): pass

    server = Server(('127.0.0.1', 0), Handler); server.resets = set()
    timer = threading.Timer(1800, server.shutdown); timer.daemon = True; timer.start()
    (root/'ready.json').write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    try: server.serve_forever()
    finally: timer.cancel(); server.server_close()


def prepare(project, store):
    root = Path(tempfile.mkdtemp(prefix='blackbox-notes059-')); run = uuid4().hex
    with (root/'server-output.txt').open('wb') as log:
        proc = subprocess.Popen([sys.executable, __file__, 'serve', '--run-root', str(root)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(100):
        if (root/'ready.json').exists(): break
        if proc.poll() is not None: raise RuntimeError('service failed')
        time.sleep(.05)
    port = json.loads((root/'ready.json').read_text())['port']; commands = {}; controls = {}
    for case, (rule, code) in CASES.items():
        limit = '0.5' if case in ('timeout', 'interim') else '4'
        cmd = ('& '+psquote(shutil.which('curl.exe'))+' -q -sS --stderr - -i --max-time '+limit
               +f' --proxy http://127.0.0.1:{port} --proxytunnel '
               +psquote(f'http://blackbox-notes059.invalid/{case}/{run}')+'; exit $LASTEXITCODE')
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
    # Exploratory commands: do not prescribe or synthesize the host's merge order.
    prefix = {str(n): commands['partial'].replace(' --stderr -', '').replace(run, run+f'-order-{n}') for n in range(1, 4)}
    m = {'project': project, 'before_seq': before, 'commands': commands, 'controls': controls,
         'prefix_commands': prefix, 'server_pid': proc.pid, 'stop_url': f'http://127.0.0.1:{port}/stop'}
    (root/'control.json').write_text(json.dumps(m, indent=2), encoding='utf-8')
    print(json.dumps({'run_root': str(root), 'controls': controls}))
    return all(x['valid'] for x in controls.values())


if __name__ == '__main__': main(EXPECTED, prepare, serve)
