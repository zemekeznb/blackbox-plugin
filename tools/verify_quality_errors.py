"""NOTES-060 success examples, real exceptions and repeat-call fixtures."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4
from verify_curl_errors import main, anomalies, psquote

TRACE = 'Traceback (most recent call last):\n  File "example.py", line 1, in <module>\nValueError: NOTES060 example\n'
PS = "Get-Item: example.ps1:4\nLine |\n   4 | Get-Item missing\n     | ~~~~~~~~~~~~~~~~\n     | Cannot find path 'example' because it does not exist.\n"
GIT = 'fatal: not a git repository (or any of the parent directories): .git\n'
NODE = 'TypeError: NOTES060 example\n    at run (C:/example.cjs:1:2)\nNode.js v24.0.0\n'
PAYLOADS = {'http_trace': TRACE, 'http_ps': PS, 'http_git': GIT, 'http_node': NODE,
            'http_warning': 'WARNING: NOTES060\nOK\n', 'http500_trace': TRACE}
CASES = {**{case: ('http-status-error' if case == 'http500_trace' else None, 0) for case in PAYLOADS},
         'python_warning': (None, 0), 'node_warning': (None, 0),
         'python_error': ('python-exception', 1), 'python_chain': ('python-exception', 1),
         'print_trace': ('python-exception', 0), 'read_trace': (None, 0),
         'read_git': (None, 0), 'node_print_fake': ('node-exception', 0)}
EXPECTED = {case: (rule, 'HTTP/1.1' if case in PAYLOADS else 'NOTES060' if case != 'read_git' else 'fatal:')
            for case, (rule, _) in CASES.items()}


def serve(root):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            self.close_connection = True
            if self.path == '/stop':
                self.send_response(204); self.end_headers()
                threading.Thread(target=self.server.shutdown, daemon=True).start(); return
            case = self.path.split('/')[1]
            if case not in PAYLOADS: self.send_error(404); return
            data = PAYLOADS[case].encode()
            self.send_response(500 if case == 'http500_trace' else 200)
            self.send_header('Content-Length', str(len(data))); self.send_header('Connection', 'close'); self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    timer = threading.Timer(1800, server.shutdown); timer.daemon = True; timer.start()
    (root/'ready.json').write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    try: server.serve_forever()
    finally: timer.cancel(); server.server_close()


def prepare(project, store):
    root = Path(tempfile.mkdtemp(prefix='blackbox-notes060-')); run = uuid4().hex
    with (root/'server-output.txt').open('wb') as log:
        proc = subprocess.Popen([sys.executable, __file__, 'serve', '--run-root', str(root)], stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(100):
        if (root/'ready.json').exists(): break
        if proc.poll() is not None: raise RuntimeError('service failed')
        time.sleep(.05)
    port = json.loads((root/'ready.json').read_text())['port']; commands = {}; controls = {}
    scripts = {
        'python_warning.py': "import warnings\nwarnings.warn('NOTES060 warning')\nprint('NOTES060 OK')\n",
        'node_warning.cjs': "process.emitWarning('NOTES060 warning');console.log('NOTES060 OK');\n",
        'python_error.py': "raise ValueError('NOTES060 中文错误')\n",
        'python_chain.py': "try:\n raise ValueError('NOTES060 first')\nexcept ValueError as e:\n raise RuntimeError('NOTES060 second') from e\n",
        'print_trace.py': 'print('+repr(TRACE)+')\n',
        'node_print_fake.cjs': 'console.log('+json.dumps(NODE)+');\n',
        'repeat.py': "from pathlib import Path\np=Path(__file__).with_suffix('.count')\nn=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\nif n<3: raise ValueError('NOTES060 repeat')\nprint('NOTES060 OK')\n"}
    for name, source in scripts.items(): (root/name).write_text(source, encoding='utf-8')
    (root/'trace.log').write_text(TRACE, encoding='utf-8'); (root/'git.log').write_text(GIT, encoding='utf-8')
    for case, (rule, code) in CASES.items():
        if case in PAYLOADS:
            cmd = ('& '+psquote(shutil.which('curl.exe'))+" -q -sS --stderr - -i --noproxy '*' --max-time 4 "
                   +psquote(f'http://127.0.0.1:{port}/{case}/{run}')+'; exit $LASTEXITCODE')
        elif case.startswith('read_'):
            cmd = 'Get-Content -Raw -LiteralPath '+psquote(root/('trace.log' if case == 'read_trace' else 'git.log'))
        else:
            node = case.startswith('node_')
            cmd = '& '+psquote(shutil.which('node') if node else sys.executable)+' '+psquote(root/(case+('.cjs' if node else '.py')))+'; exit $LASTEXITCODE'
        commands[case] = cmd
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-Command', cmd],
            capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = (result.stdout+result.stderr).decode('utf-8', errors='replace')
        (root/(case+'-control.txt')).write_bytes(raw.encode())
        hit = anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
        controls[case] = {'exit_code':result.returncode,'expected_rule':rule,'observed_rule':hit['rule'] if hit else None,
                         'valid': result.returncode == code and (hit['rule'] if hit else None) == rule and EXPECTED[case][1] in raw}
    with store.connect() as db: before = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    m = {'project':project,'before_seq':before,'commands':commands,'controls':controls,'server_pid':proc.pid,
         'repeat_command':'& '+psquote(sys.executable)+' '+psquote(root/'repeat.py')+'; exit $LASTEXITCODE',
         'stop_url':f'http://127.0.0.1:{port}/stop'}
    (root/'control.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps({'run_root':str(root),'controls':controls})); return all(c['valid'] for c in controls.values())


if __name__ == '__main__': main(EXPECTED, prepare, serve)
