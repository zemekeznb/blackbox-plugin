"""NOTES-054 local HTTP status fixtures; reuse bounded desktop audit."""
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

CODES=(400,401,403,404,408,429,500,502,503,504)
EXPECTED={**{str(c):('http-status-error',f'HTTP/1.0 {c}') for c in CODES},
          '200':(None,'HTTP/1.0 200'),'204':(None,'HTTP/1.0 204'),'302':(None,'HTTP/1.0 302'),
          'body_spoof':(None,'HTTP/1.0 200'),'body_only':(None,'NOTES054'),
          'read_log':(None,'HTTP/1.1 502'),'plain_text':(None,'HTTP/1.1 502')}


def serve(root):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/stop':
                self.send_response(204);self.end_headers();threading.Thread(target=self.server.shutdown,daemon=True).start();return
            case=self.path.split('/')[1]
            if case not in EXPECTED:self.send_error(404);return
            code=int(case) if case.isdigit() else 502 if case=='body_only' else 200
            body=json.dumps({'message':'NOTES054 gateway error','code':code}) if code==500 else '<html>NOTES054 response</html>'
            if case=='body_spoof':body='HTTP/1.1 502 Bad Gateway\r\n\r\nerror'
            if code==204:body=''
            raw=body.encode();self.send_response(code)
            self.send_header('Content-Type','application/json' if code==500 else 'text/html')
            if code!=204:self.send_header('Content-Length',str(len(raw)))
            if code==302:self.send_header('Location','/200/redirect-target')
            self.end_headers();self.wfile.write(raw)
            with (root/'requests.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'path':self.path,'status':code})+'\n')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);timer=threading.Timer(1800,server.shutdown);timer.daemon=True;timer.start()
    (root/'ready.json').write_text(json.dumps({'port':server.server_port}),encoding='utf-8')
    try:server.serve_forever()
    finally:timer.cancel();server.server_close()


def prepare(project,store):
    root=Path(tempfile.mkdtemp(prefix='blackbox-notes054-'));run=uuid4().hex
    with (root/'server-output.txt').open('wb') as log:
        proc=subprocess.Popen([sys.executable,__file__,'serve','--run-root',str(root)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(100):
        if (root/'ready.json').exists():break
        if proc.poll() is not None:raise RuntimeError('service failed')
        time.sleep(.05)
    port=json.loads((root/'ready.json').read_text())['port'];curl=shutil.which('curl.exe');pwsh=shutil.which('pwsh')
    if not curl or not pwsh:raise RuntimeError('existing curl/pwsh required')
    old='HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/html\r\n\r\nNOTES054'
    (root/'old.log').write_bytes(old.encode());commands={};controls={}
    for case in EXPECTED:
        cmd='& '+psquote(curl)+" -q -sS --max-time 5 --noproxy '*'"+('' if case=='body_only' else ' -i')+' '+psquote(f'http://127.0.0.1:{port}/{case}/{run}')+'; exit $LASTEXITCODE'
        if case=='read_log':cmd='Get-Content -Raw -LiteralPath '+psquote(root/'old.log')
        if case=='plain_text':cmd="Write-Output 'example HTTP/1.1 502 Bad Gateway'"
        commands[case]=cmd
        result=subprocess.run([pwsh,'-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raw=(result.stdout+result.stderr).decode('utf-8',errors='replace');(root/(case+'-control.txt')).write_bytes(raw.encode())
        hit=anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
        controls[case]={'exit_code':result.returncode,'expected_rule':EXPECTED[case][0],'valid':result.returncode==0 and (hit['rule'] if hit else None)==EXPECTED[case][0] and EXPECTED[case][1] in raw}
    with store.connect() as db:before=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    m={'project':project,'before_seq':before,'commands':commands,'controls':controls,'server_pid':proc.pid,'stop_url':f'http://127.0.0.1:{port}/stop'}
    (root/'control.json').write_text(json.dumps(m,indent=2),encoding='utf-8');print(json.dumps({'run_root':str(root),'controls':controls}))
    return all(x['valid'] for x in controls.values())


if __name__=='__main__':main(EXPECTED,prepare,serve)
