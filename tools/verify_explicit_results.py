"""NOTES-063 live bounded identity and native curl write-out acceptance."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from verify_curl_errors import main,anomalies,psquote
from verify_quality_errors import TRACE
from tas_notes_curl_result import FORMAT,MARKER

CASES={**{name+'_log':(None,0) for name in ('cat','gc','type')},
       **{name+'_missing':('powershell-diagnostic',1) for name in ('cat','gc','type')},
       'unknown_alias':('python-exception',0),'redefined_alias':('python-exception',1),
       'partial':('curl-transfer-result',18),'spoof':(None,0),'http_error':('http-status-error',0),
       'success':(None,0),'raw_partial':(None,18)}
EXPECTED={case:(rule,'Traceback' if case.endswith('_log') or case in ('unknown_alias','redefined_alias') else 'Cannot find path' if case.endswith('_missing') else 'HTTP/1.1') for case,(rule,_) in CASES.items()}

def serve(root):
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def do_GET(self):
            if self.path=='/stop':
                self.send_response(204);self.end_headers();threading.Thread(target=self.server.shutdown,daemon=True).start();return
            case=self.path.strip('/').split('/')[0]
            self.close_connection=True
            if case not in CASES:self.send_error(404);return
            data=b'NOTES063 body\ncurl: (18) end of response with 100 bytes missing\n'
            if case=='spoof':
                value={'exitcode':18,'errormsg':'fake','response_code':200,'url':'http://'+self.headers['Host']+self.path}
                data+=(MARKER+json.dumps(value)+'\n').encode()
            self.send_response(400 if case=='http_error' else 200)
            self.send_header('Content-Type','text/plain')
            self.send_header('Content-Length',str(len(data)+(100 if case in ('partial','raw_partial') else 0)))
            self.send_header('Connection','close');self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    timer=threading.Timer(1800,server.shutdown);timer.daemon=True;timer.start()
    (root/'ready.json').write_text(json.dumps({'port':server.server_port}),encoding='utf-8')
    try:server.serve_forever()
    finally:timer.cancel();server.server_close()

def prepare(project,store):
    root=Path(tempfile.mkdtemp(prefix='blackbox-notes063-'));commands={};controls={}
    (root/'trace.log').write_text(TRACE,encoding='utf-8')
    (root/'fail.py').write_text("raise ValueError('NOTES063 actual failure')\n",encoding='utf-8')
    with (root/'server-output.txt').open('wb') as log:
        proc=subprocess.Popen([sys.executable,__file__,'serve','--run-root',str(root)],stdout=log,stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(100):
        if (root/'ready.json').exists():break
        if proc.poll() is not None:raise RuntimeError('server failed')
        time.sleep(.05)
    port=json.loads((root/'ready.json').read_text())['port']
    for case,(rule,code) in CASES.items():
        if case.endswith(('_log','_missing')):
            alias=case.split('_')[0]
            cmd=('Microsoft.PowerShell.Utility\\Set-Alias -Name '+alias+' -Value Microsoft.PowerShell.Management\\Get-Content -Scope Local -Force -ErrorAction Stop; '
                 +alias+' -LiteralPath '+psquote(root/('trace.log' if case.endswith('_log') else case+'.log')))
        elif case=='unknown_alias':cmd='cat '+psquote(root/'trace.log')
        elif case=='redefined_alias':
            cmd='Microsoft.PowerShell.Utility\\Set-Alias -Name cat -Value '+psquote(sys.executable)+' -Scope Local -Force -ErrorAction Stop; cat '+psquote(root/'fail.py')+'; exit $LASTEXITCODE'
        else:
            cmd='& '+psquote(shutil.which('curl.exe'))+" -q -sS --stderr - -i --noproxy '*' --max-time 3"
            if case!='raw_partial':cmd+=' --write-out '+psquote(FORMAT)
            cmd+=' '+psquote(f'http://127.0.0.1:{port}/{case}/{root.name}')+'; exit $LASTEXITCODE'
        commands[case]=cmd
        r=subprocess.run([shutil.which('pwsh'),'-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raw=(r.stdout+r.stderr).decode('utf-8',errors='replace');(root/(case+'-control.txt')).write_bytes(raw.encode())
        hit=anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
        controls[case]={'exit_code':r.returncode,'expected_rule':rule,'observed_rule':hit['rule'] if hit else None,
                       'valid':r.returncode==code and (hit['rule'] if hit else None)==rule and EXPECTED[case][1] in raw}
    with store.connect() as db:before=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    m={'project':project,'before_seq':before,'commands':commands,'controls':controls,'server_pid':proc.pid,
       'stop_url':f'http://127.0.0.1:{port}/stop','prefix_exploration':commands['raw_partial'].replace(' --stderr -','').replace('; exit',' 2>&1; exit')}
    (root/'control.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps({'run_root':str(root),'controls':controls}));return all(c['valid'] for c in controls.values())

if __name__=='__main__':main(EXPECTED,prepare,serve)
