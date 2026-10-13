"""NOTES-055 localhost curl chain fixtures and real Hook audit (no event injection)."""
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

CASES = {
 'fail_404': ('--fail', 404, 'http-status-error', 22),
 'fail_body_500': ('--fail-with-body', 500, 'http-status-error', 22),
 'fail_discard_503': ('--fail -o NUL', 503, 'curl-http-fail', 22),
 'redirect_301_error': ('-L', 502, 'http-status-error', 0),
 'redirect_302_error': ('-L', 502, 'http-status-error', 0),
 'redirect_303_error': ('-L', 502, 'http-status-error', 0),
 'redirect_307_error': ('-L', 502, 'http-status-error', 0),
 'redirect_308_error': ('-L', 502, 'http-status-error', 0),
 'redirect_fail': ('-L --fail', 404, 'http-status-error', 22),
 'redirect_fail_body': ('-L --fail-with-body', 500, 'http-status-error', 22),
 'interim_100_error': ('', 503, 'http-status-error', 0),
 'interim_103_error': ('', 503, 'http-status-error', 0),
 'interim_fail_body': ('--fail-with-body', 502, 'http-status-error', 22),
 'proxy_error': ('', 502, 'http-status-error', 0),
 'proxy_fail_body': ('--fail-with-body', 500, 'http-status-error', 22),
 'chain_error': ('-L', 504, 'http-status-error', 0),
 'fail_success': ('--fail', 200, None, 0),
 'fail_body_success': ('--fail-with-body', 200, None, 0),
 'redirect_success': ('-L', 200, None, 0),
 'redirect_unfollowed': ('', 302, None, 0),
 'interim_success': ('', 200, None, 0),
 'proxy_success': ('', 200, None, 0),
 'body_spoof': ('-L --fail-with-body', 200, None, 0),
 'read_log': ('', 200, None, 0),
 'plain_text': ('', 200, None, 0),
}
EXPECTED={case:(rule,'curl: (22)' if case=='fail_discard_503' else 'NOTES055' if case in ('read_log','plain_text') else 'HTTP/1.1')
          for case,(_,_,rule,_) in CASES.items()}


def serve(root):
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def do_CONNECT(self):
            # Self-contained tunnel emulator: never resolve/connect to the target.
            self.send_response_only(200,'Connection established');self.end_headers()
            self.close_connection=False
        def do_GET(self):
            if self.path=='/stop':
                self.send_response(204);self.end_headers()
                threading.Thread(target=self.server.shutdown,daemon=True).start();return
            parts=self.path.split('/');case=parts[1]
            if case not in CASES:self.send_error(404);return
            final='/final/' in self.path
            flags,code,rule,exit_code=CASES[case]
            if case.startswith('interim_') or case=='chain_error':
                self.send_response_only(103 if '103' in case else 100);self.end_headers()
            if (case.startswith('redirect_') or case=='chain_error') and not final:
                bits=case.split('_')
                redirect=int(bits[1]) if bits[1].isdigit() else 302
                self.send_response(redirect)
                self.send_header('Location',f'/{case}/final/{parts[-1]}')
                data=b'NOTES055 skipped redirect body\n'
            else:
                self.send_response(code)
                data=b'NOTES055 final body\n'
                if case=='body_spoof':data=b'HTTP/1.1 502 Bad Gateway\r\n\r\ncurl: (22) The requested URL returned error: 502\n'
            self.send_header('Content-Type','text/plain')
            self.send_header('Content-Length',str(len(data)));self.send_header('Connection','close');self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            self.close_connection=True
            with (root/'requests.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'path':self.path,'final':final,'case':case})+'\n')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    timer=threading.Timer(1800,server.shutdown);timer.daemon=True;timer.start()
    (root/'ready.json').write_text(json.dumps({'port':server.server_port}),encoding='utf-8')
    try:server.serve_forever()
    finally:timer.cancel();server.server_close()


def prepare(project,store):
    root=Path(tempfile.mkdtemp(prefix='blackbox-notes055-'));run=uuid4().hex
    with (root/'server-output.txt').open('wb') as log:
        proc=subprocess.Popen([sys.executable,__file__,'serve','--run-root',str(root)],stdin=subprocess.DEVNULL,
          stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(100):
        if (root/'ready.json').exists():break
        if proc.poll() is not None:raise RuntimeError('service failed')
        time.sleep(.05)
    port=json.loads((root/'ready.json').read_text())['port'];curl=shutil.which('curl.exe');pwsh=shutil.which('pwsh')
    if not curl or not pwsh:raise RuntimeError('existing curl/pwsh required')
    (root/'old.log').write_bytes(b'HTTP/1.1 502 Bad Gateway\r\n\r\nNOTES055')
    commands={};controls={}
    for case,(flags,code,rule,expected_exit) in CASES.items():
        proxy=case.startswith('proxy_')
        target='blackbox-notes055.invalid' if proxy else f'127.0.0.1:{port}'
        route=f' --proxy http://127.0.0.1:{port} --proxytunnel' if proxy else " --noproxy '*'"
        cmd='& '+psquote(curl)+' -q -sS --stderr - --max-time 5 -i '+flags+route+' '+psquote(f'http://{target}/{case}/{run}')+'; exit $LASTEXITCODE'
        if case=='read_log':cmd='Get-Content -Raw -LiteralPath '+psquote(root/'old.log')
        if case=='plain_text':cmd="Write-Output 'NOTES055 example HTTP/1.1 502 Bad Gateway'"
        commands[case]=cmd
        result=subprocess.run([pwsh,'-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,timeout=10,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raw=(result.stdout+result.stderr).decode('utf-8',errors='replace')
        (root/(case+'-control.txt')).write_bytes(raw.encode())
        hit=anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
        controls[case]={'exit_code':result.returncode,'expected_rule':rule,
          'valid':result.returncode==expected_exit and (hit['rule'] if hit else None)==rule and EXPECTED[case][1] in raw}
    with store.connect() as db:before=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    manifest={'project':project,'before_seq':before,'commands':commands,'controls':controls,
              'server_pid':proc.pid,'stop_url':f'http://127.0.0.1:{port}/stop'}
    (root/'control.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'run_root':str(root),'controls':controls}));return all(x['valid'] for x in controls.values())


if __name__=='__main__':main(EXPECTED,prepare,serve)
