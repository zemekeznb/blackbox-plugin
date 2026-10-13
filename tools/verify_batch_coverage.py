"""NOTES-062 controlled files/SSE/partial transfer; real Hook audit stays read-only."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from verify_curl_errors import main, anomalies, psquote
from verify_quality_errors import TRACE, PS

ERROR={'errors':[{'message':'NOTES062 invalid field'}]}
CASES={
    'request_error':('graphql-sse-errors',0), 'null_error':('graphql-sse-errors',0),
    'partial_data':('graphql-sse-errors',0), 'siblings':('graphql-sse-errors',0),
    'multiline':('graphql-sse-errors',0), 'success':(None,0), 'unclosed':(None,0),
    'unknown_event':(None,0), 'duplicate':(None,0), 'multiplex':(None,0),
    'wrong_accept':(None,0), 'wrong_media':(None,0), 'partial_body':(None,18),
    'partial_spoof':(None,0), 'read_ps':(None,0), 'read_getcontent':(None,0),
    'read_trace':(None,0), 'child_log':(None,0),
    'missing':('powershell-diagnostic',1), 'child_missing':('powershell-diagnostic',1),
    'cat_log':('python-exception',0), 'gc_log':('python-exception',0), 'type_log':('python-exception',0)}
EXPECTED={case:(rule,'Cannot find path' if case in ('read_ps','read_getcontent','child_log','missing','child_missing')
                       else 'Traceback' if case in ('read_trace','cat_log','gc_log','type_log') else 'HTTP/1.1')
          for case,(rule,_) in CASES.items()}

def payload(case):
    value=ERROR
    if case=='null_error':value={'data':None,'errors':[{'message':'NOTES062 failed','path':['item']}]}
    if case=='partial_data':value={'data':{'item':None,'ok':True},'errors':[{'message':'NOTES062 failed','path':['item']}]}
    if case=='success':value={'data':{'ok':True}}
    if case=='multiplex':value={'id':'one','payload':ERROR}
    data='event: next\ndata: '+json.dumps(value)+'\n\n'
    if case=='siblings':data+='event: next\ndata: {"data":{"ok":true}}\n\n'
    if case=='multiline':data=': keepalive\n\n'+data.replace('data: {','data: {\ndata: ')
    if case=='unknown_event':data=data.replace('event: next','event: error')
    if case=='duplicate':data=data.replace('event: next','event: next\nevent: next')
    if case!='unclosed':data+='event: complete\ndata: \n\n'
    if case in ('partial_body','partial_spoof'):data='NOTES062 partial body\ncurl: (18) end of response with 100 bytes missing\n'
    return data.encode()

def serve(root):
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def do_GET(self):
            if self.path!='/stop':self.send_error(404);return
            self.send_response(204);self.end_headers()
            threading.Thread(target=self.server.shutdown,daemon=True).start()
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            case=self.path.strip('/').split('/')[0]
            if case not in CASES:self.send_error(404);return
            data=payload(case);self.close_connection=True
            self.send_response(200)
            media='application/json' if case=='wrong_media' else 'text/plain' if case in ('partial_body','partial_spoof') else 'text/event-stream'
            self.send_header('Content-Type',media)
            self.send_header('Content-Length',str(len(data)+(100 if case=='partial_body' else 0)))
            self.send_header('Connection','close');self.end_headers()
            try:self.wfile.write(data)
            except OSError:pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    timer=threading.Timer(1800,server.shutdown);timer.daemon=True;timer.start()
    (root/'ready.json').write_text(json.dumps({'port':server.server_port}),encoding='utf-8')
    try:server.serve_forever()
    finally:timer.cancel();server.server_close()

def prepare(project,store):
    root=Path(tempfile.mkdtemp(prefix='blackbox-notes062-'))
    with (root/'server-output.txt').open('wb') as log:
        proc=subprocess.Popen([sys.executable,__file__,'serve','--run-root',str(root)],stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(100):
        if (root/'ready.json').exists():break
        if proc.poll() is not None:raise RuntimeError('service failed')
        time.sleep(.05)
    port=json.loads((root/'ready.json').read_text())['port'];commands={};controls={}
    (root/'ps.log').write_text(PS,encoding='utf-8')
    (root/'getcontent.log').write_text("Get-Content: Cannot find path 'C:\\other\\missing' because it does not exist.\n",encoding='utf-8')
    (root/'trace.log').write_text(TRACE,encoding='utf-8')
    for case,(rule,code) in CASES.items():
        if case in ('read_ps','read_getcontent','read_trace','missing','child_log','child_missing'):
            name={'read_ps':'ps.log','read_getcontent':'getcontent.log','read_trace':'trace.log',
                  'child_log':'getcontent.log'}.get(case,case+'.log')
            cmd='Get-Content -Raw -LiteralPath '+psquote(root/name)
            if case.startswith('child_'):
                cmd='& '+psquote(shutil.which('pwsh'))+' -NoProfile -NonInteractive -Command '+psquote(cmd)+'; exit $LASTEXITCODE'
        elif case.endswith('_log'):
            cmd=case.split('_')[0]+' '+psquote(root/'trace.log')
        else:
            cmd=('& '+psquote(shutil.which('curl.exe'))+" -q -sS --stderr - -i --max-time 4 --noproxy '*'"
                 +" -H 'Content-Type: application/json' -H 'Accept: "+('application/json' if case=='wrong_accept' else 'text/event-stream')
                 +"' --data-raw "+psquote('{"query":"{ item }"}')+' '+psquote(f'http://127.0.0.1:{port}/{case}/{root.name}')+'; exit $LASTEXITCODE')
        commands[case]=cmd
        result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,
            timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raw=(result.stdout+result.stderr).decode('utf-8',errors='replace');(root/(case+'-control.txt')).write_bytes(raw.encode())
        hit=anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
        controls[case]={'exit_code':result.returncode,'expected_rule':rule,'observed_rule':hit['rule'] if hit else None,
                       'valid':result.returncode==code and (hit['rule'] if hit else None)==rule and EXPECTED[case][1] in raw}
    with store.connect() as db:before=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    manifest={'project':project,'before_seq':before,'commands':commands,'controls':controls,'server_pid':proc.pid,
              'stop_url':f'http://127.0.0.1:{port}/stop',
              'prefix_exploration':commands['partial_body'].replace(' --stderr -','')}
    (root/'control.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'run_root':str(root),'controls':controls}));return all(x['valid'] for x in controls.values())

if __name__=='__main__':main(EXPECTED,prepare,serve)
