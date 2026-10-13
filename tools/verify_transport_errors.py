"""NOTES-057 local transport failures; no external network or trusted cert changes."""
import json,pathlib,shutil,socket,ssl,struct,subprocess,sys,tempfile,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from uuid import uuid4
from verify_curl_errors import main,anomalies,psquote
CASES={'tls':('curl-tls',35),'certificate':('curl-certificate',60),'reset':('curl-receive',56),
       'partial':('curl-partial',18),'redirect_limit':('curl-redirect-limit',47),'empty_reply':('curl-empty-reply',52),
       'success':(None,0),'redirect_success':(None,0),'body_spoof':(None,0),'read_log':(None,0),'plain_text':(None,0)}
EXPECTED={k:(rule,'curl: ('+str(code)+')' if rule else 'NOTES057' if k in ('read_log','plain_text') else '') for k,(rule,code) in CASES.items()}
FAKE='curl: (56) Recv failure: Connection was reset'

def serve(root):
 class Server(ThreadingHTTPServer):
  def shutdown_request(self,request):
   if request in getattr(self,'resets',set()):
    self.resets.remove(request);self.close_request(request)
   else:super().shutdown_request(request)
 class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
   if self.path=='/stop':
    self.send_response(204);self.end_headers();threading.Thread(target=self.server.shutdown,daemon=True).start();return
   case=self.path.split('/')[1]
   if case=='reset':
    self.connection.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('HH',1,0))
    self.server.resets.add(self.connection);self.close_connection=True;return
   if case=='empty_reply':self.close_connection=True;return
   if case=='redirect_limit' or case=='redirect_success' and '/final/' not in self.path:
    self.send_response(302);self.send_header('Location',self.path if case=='redirect_limit' else self.path+'/final/');data=b''
   else:
    self.send_response(200);data=(FAKE if case=='body_spoof' else 'NOTES057_OK').encode()
   self.send_header('Content-Length',str(len(data)+100 if case=='partial' else len(data)));self.end_headers()
   try:self.wfile.write(data)
   except OSError:pass
   self.close_connection=True
  def log_message(self,*args):pass
 server=Server(('127.0.0.1',0),Handler);server.resets=set()
 secure=ThreadingHTTPServer(('127.0.0.1',0),Handler)
 context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'cert.pem',root/'key.pem')
 secure.socket=context.wrap_socket(secure.socket,server_side=True)
 thread=threading.Thread(target=secure.serve_forever,daemon=True);thread.start()
 timer=threading.Timer(1800,server.shutdown);timer.daemon=True;timer.start()
 (root/'ready.json').write_text(json.dumps({'http':server.server_port,'https':secure.server_port}),encoding='utf-8')
 try:server.serve_forever()
 finally:timer.cancel();server.server_close();secure.shutdown();secure.server_close()

def prepare(project,store):
 root=pathlib.Path(tempfile.mkdtemp(prefix='blackbox-notes057-'));run=uuid4().hex
 subprocess.run(['C:/Program Files/Git/usr/bin/openssl.exe','req','-x509','-newkey','rsa:2048','-nodes',
   '-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem'),'-days','1','-subj','/CN=localhost',
   '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,capture_output=True,
   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 with (root/'server-output.txt').open('wb') as log:
  proc=subprocess.Popen([sys.executable,__file__,'serve','--run-root',str(root)],stdin=subprocess.DEVNULL,
   stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 for _ in range(100):
  if (root/'ready.json').exists():break
  if proc.poll() is not None:raise RuntimeError('service failed')
  time.sleep(.05)
 ports=json.loads((root/'ready.json').read_text());commands={};controls={}
 (root/'old.log').write_text(FAKE+'\nNOTES057',encoding='utf-8')
 for case,(rule,code) in CASES.items():
  scheme='https' if case in ('tls','certificate') else 'http'
  port=ports['https'] if case=='certificate' else ports['http']
  options=' -L --max-redirs 2' if case.startswith('redirect_') else ''
  cmd='& '+psquote(shutil.which('curl.exe'))+" -q -sS --stderr - --max-time 5 --noproxy '*' -o NUL"+options+' '+psquote(f'{scheme}://127.0.0.1:{port}/{case}/{run}')+'; exit $LASTEXITCODE'
  if case=='read_log':cmd='Get-Content -Raw -LiteralPath '+psquote(root/'old.log')
  if case=='body_spoof':cmd=cmd.replace(' -o NUL',' -i')
  if case=='plain_text':cmd="Write-Output 'NOTES057 example "+FAKE+"'"
  commands[case]=cmd
  result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  raw=(result.stdout+result.stderr).decode('utf-8',errors='replace');(root/(case+'-control.txt')).write_bytes(raw.encode())
  hit=anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
  controls[case]={'exit_code':result.returncode,'expected_rule':rule,'valid':result.returncode==code and (hit['rule'] if hit else None)==rule and EXPECTED[case][1] in raw}
 with store.connect() as db:before=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
 m={'project':project,'before_seq':before,'commands':commands,'controls':controls,'server_pid':proc.pid,'stop_url':f'http://127.0.0.1:{ports["http"]}/stop'}
 (root/'control.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
 print(json.dumps({'run_root':str(root),'controls':controls}));return all(x['valid'] for x in controls.values())

if __name__=='__main__':main(EXPECTED,prepare,serve)
