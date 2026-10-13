"""NOTES-053 real curl fixture service and bounded read-only Hook acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
from uuid import uuid4
from verify_shell_formats import audit, anomalies, Store, Reader, build_id
from verify_bash_coverage import psquote

EXPECTED = {'refused': ('curl-connect', 'curl: (7)'), 'dns': ('curl-dns', 'curl: (6)'),
            'connect_timeout': ('curl-timeout', 'curl: (28)'), 'read_timeout': ('curl-timeout', 'curl: (28)'),
            'success': (None, 'NOTES053_OK'), 'body_spoof': (None, 'curl: (7)'),
            'read_log': (None, 'curl: (7)'), 'plain_text': (None, 'curl: (7)')}
EXITS = {'refused':7, 'dns':6, 'connect_timeout':28, 'read_timeout':28}
FAKE = 'curl: (7) Failed to connect to example port 1 after 0 ms: Could not connect to server'


def serve(root):
    clients=[]
    refused=socket.socket();refused.bind(('127.0.0.1',0))  # reserved, no listener
    blocked=socket.socket();blocked.bind(('127.0.0.1',0));blocked.listen(1)
    saturated=False
    for _ in range(32):
        client=socket.socket();client.settimeout(.15);clients.append(client)
        try:client.connect(blocked.getsockname())
        except TimeoutError:saturated=True;break
    if not saturated:raise RuntimeError('could not saturate local accept queue')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/stop':
                self.send_response(204);self.end_headers()
                threading.Thread(target=self.server.shutdown,daemon=True).start();return
            with (root/'requests.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'path':self.path,'request_received':True})+'\n')
            data=(FAKE if '/body_spoof/' in self.path else 'NOTES053_OK').encode()
            self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers()
            if '/read_timeout/' in self.path:time.sleep(2)
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    timer=threading.Timer(1800,server.shutdown);timer.daemon=True;timer.start()
    (root/'ready.json').write_text(json.dumps({'http':server.server_port,'refused':refused.getsockname()[1],
        'blocked':blocked.getsockname()[1],'saturated':saturated}),encoding='utf-8')
    try:server.serve_forever()
    finally:
        timer.cancel();server.server_close();refused.close();blocked.close()
        for client in clients:client.close()


def prepare(project,store):
    root=Path(tempfile.mkdtemp(prefix='blackbox-notes053-'));run=uuid4().hex
    with (root/'server-output.txt').open('wb') as log:
        proc=subprocess.Popen([sys.executable,__file__,'serve','--run-root',str(root)],stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(100):
        if (root/'ready.json').exists():break
        if proc.poll() is not None:raise RuntimeError('fixture service failed')
        time.sleep(.1)
    ports=json.loads((root/'ready.json').read_text());curl=shutil.which('curl.exe');pwsh=shutil.which('pwsh')
    if not curl or not pwsh:raise RuntimeError('existing curl.exe and pwsh required')
    commands={};controls={};(root/'old.log').write_text(FAKE,encoding='utf-8')
    for case in EXPECTED:
        port=ports['refused'] if case=='refused' else ports['blocked'] if case=='connect_timeout' else ports['http']
        url=f'http://127.0.0.1:{port}/{case}/{run}'
        if case=='dns':url=f'http://blackbox-{run}.invalid/'
        command='& '+psquote(curl)+" -q -sS -4 --noproxy '*' --connect-timeout "+('3 --max-time 4' if case=='refused' else '0.3 --max-time '+('3' if case=='dns' else '0.6'))
        command+=(' -o NUL' if case in ('connect_timeout','read_timeout') else ' -i')+' '+psquote(url)+'; exit $LASTEXITCODE'
        if case=='read_log':command='Get-Content -Raw -LiteralPath '+psquote(root/'old.log')
        if case=='plain_text':command='Write-Output '+psquote('example '+FAKE)
        commands[case]=command
        result=subprocess.run([pwsh,'-NoProfile','-NonInteractive','-Command',command],capture_output=True,timeout=10,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raw=(result.stdout+result.stderr).decode('utf-8',errors='replace');(root/(case+'-control.txt')).write_bytes(raw.encode())
        hit=anomalies.detect({'tool_name':'Bash','tool_input':{'command':command},'tool_response':raw})
        controls[case]={'exit_code':result.returncode,'expected_rule':EXPECTED[case][0],
            'valid':result.returncode==EXITS.get(case,0) and (hit['rule'] if hit else None)==EXPECTED[case][0] and EXPECTED[case][1] in raw}
    with store.connect() as db:before=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    manifest={'project':project,'before_seq':before,'commands':commands,'controls':controls,'server_pid':proc.pid,
              'stop_url':f'http://127.0.0.1:{ports["http"]}/stop'}
    (root/'control.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'run_root':str(root),'controls':controls}));return all(c['valid'] for c in controls.values())


def main(expected=EXPECTED, prepare_fn=prepare, serve_fn=serve):
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['serve','prepare','check','stop'])
    p.add_argument('--run-root',type=Path);p.add_argument('--state-dir',type=Path);p.add_argument('--project',type=Path)
    args=p.parse_args()
    if args.phase=='serve':serve_fn(args.run_root);return
    project=str(args.project.resolve(strict=True));store=Store(args.state_dir,read_only=True)
    if not any(x['path']==project and x['enabled'] for x in store.status()['projects']):raise RuntimeError('project not enabled')
    if args.phase=='prepare':
        if not prepare_fn(project,store):raise SystemExit(1)
        return
    root=args.run_root;manifest=json.loads((root/'control.json').read_text())
    if manifest['project']!=project or set(manifest['commands'])!=set(expected):raise RuntimeError('unexpected manifest')
    if args.phase=='stop':
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(manifest['stop_url'],timeout=5) as r:
            print(json.dumps({'stopped':r.status==204}))
        return
    with store.connect() as db:
        through=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
        rows=db.execute('SELECT e.* FROM events e JOIN investigations i ON i.id=e.investigation WHERE i.project=? AND e.seq>? AND e.seq<=? ORDER BY e.seq LIMIT 1001',(project,manifest['before_seq'],through)).fetchall()
    if len(rows)>1000:raise RuntimeError('audit window too large')
    report=audit(rows,manifest['commands'],expected);report.update(before_seq=manifest['before_seq'],through=through,rule=anomalies.RULE_VERSION,build=build_id())
    listed=set();before=None;reader=Reader(args.state_dir)
    try:
        while True:
            page=anomalies.listing(store,project_path=project,through=through,before=before)
            listed.update(int(x['id']) for x in page['items']);before=page['next_before']
            if before is None or int(before)<=manifest['before_seq']:break
        for case,items in report['cases'].items():
            for item in items:
                item['listed_correctly']=(item['seq'] in listed)==bool(expected[case][0]);report['passed'] &= item['listed_correctly']
                if not expected[case][0] or not item['valid']:continue
                detail=anomalies.detail(store,item['seq'],through)
                out=reader.call('read_view',{'path':f'/api/anomaly-export/{item["seq"]}?through={through}&rule={anomalies.RULE_VERSION}'})['_meta']['blackbox']
                raw=detail['markdown'].encode();(root/(case+'-expected.md')).write_bytes(raw)
                item['export_sha256']=hashlib.sha256(raw).hexdigest();item['export_matches']=out['status']==200 and out['body']==detail['markdown']
                report['passed'] &= item['export_matches']
    finally:reader.names.close();reader.projects.close()
    report['passed'] &= all(c['valid'] for c in manifest['controls'].values())
    (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
