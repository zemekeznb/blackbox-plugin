const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('plugins/blackbox/assets/mcp-reader.js','utf8');
function harness(reply,initialize={},timers={}) {
  const sent=[]; let listener;
  const parent={postMessage(message){sent.push(message);if(!message.id)return;queueMicrotask(()=>{const result=message.method==='ui/initialize'?initialize:typeof reply==='function'?reply(message):reply;if(result?.noReply)return;listener({source:parent,data:{jsonrpc:'2.0',id:message.id,...(result?.rpcError?{error:result.rpcError}:{result})}});});}};
  const window={parent,addEventListener(name,fn){listener=fn}};
  vm.runInNewContext(source,{window,Response,Blob,URL,URLSearchParams,TextEncoder,setTimeout:timers.setTimeout||setTimeout,clearTimeout:timers.clearTimeout||clearTimeout,Map,Promise,Error,Number,JSON});
  return {window,sent,deliver:data=>listener({source:parent,data})};
}
test('reader shares handshake and decodes component-only JSON and Markdown',async()=>{
  const h=harness({_meta:{blackbox:{status:200,body:{items:['synthetic']}}},content:[]});
  const responses=await Promise.all([h.window.blackboxRequest('/api/anomalies'),h.window.blackboxRequest('/api/status')]);
  assert.deepEqual(await responses[0].json(),{items:['synthetic']});
  assert.equal(h.sent.filter(m=>m.method==='ui/initialize').length,1);
  assert.equal(h.sent.filter(m=>m.method==='tools/call').length,2);
  const preview='# 快照\n',digest=require('node:crypto').createHash('sha256').update(preview).digest('hex');
  const download=harness({_meta:{blackbox:{status:200,body:preview,preview_sha256:digest}}});
  const response=await download.window.blackboxRequest('/api/anomaly-export/1');
  assert.equal(await response.text(),preview);assert.equal(response.headers.get('X-Blackbox-Preview-SHA256'),digest);
});
test('session lifecycle forwards only the explicit confirmed action through the app tool',async()=>{
 const h=harness({_meta:{blackbox:{status:200,body:{completed:true}}}});
 const action={investigation:'synthetic',action:'delete',snapshot:'a'.repeat(64)};
 await h.window.blackboxRequest('/api/session-management','POST',action);
 const message=h.sent.at(-1);
 assert.equal(message.method,'tools/call');assert.equal(message.params.name,'manage_session');
 assert.deepEqual(JSON.parse(JSON.stringify(message.params.arguments)),action);
 assert.ok(!h.sent.some(m=>m.method==='ui/message'||m.method==='ui/update-model-context'));
});
test('missing private metadata fails closed, never falls back to model-visible data',async()=>{
  const h=harness({structuredContent:{status:200,body:'private'}});
  await assert.rejects(h.window.blackboxRequest('/api/status'),/宿主未返回/);
});
test('preference uses separate write tool and shutdown is rejected',async()=>{
  const h=harness({_meta:{blackbox:{status:200,body:{}}}});
  await h.window.blackboxRequest('/api/reader-selection','POST',{investigation:'saved'});
  assert.equal(h.sent.at(-1).params.name,'select_session');
  await assert.rejects(h.window.blackboxRequest('/api/shutdown','POST'),/不支持/);
  assert.equal(h.sent.filter(m=>m.method==='tools/call').length,1);
});

test('standard host download sends the exact preview inline with no tool call or model context',async()=>{
  const body='# 中文\n<script>inert</script>\n',h=harness({},{hostCapabilities:{downloadFile:{}}});
  await h.window.blackboxDownload('anomaly-1-through-8.md',body);
  const message=h.sent.at(-1);
  assert.equal(message.method,'ui/download-file');
  assert.deepEqual(JSON.parse(JSON.stringify(message.params)),{contents:[{type:'resource',resource:{uri:'file:///anomaly-1-through-8.md',mimeType:'text/markdown',text:body}}]});
  assert.ok(!h.sent.some(m=>['tools/call','ui/message','ui/update-model-context'].includes(m.method)));
});
test('desktop host uses only the public contents format even without advertised capability',async()=>{
  const hostInfo={name:'chatgpt',client_type:'codex_desktop',version:'26.1007.2314.0'};
  const h=harness({},{hostInfo});await h.window.blackboxDownload('test.md','完整预览\n');
  assert.equal(h.sent.at(-1).params.contents[0].resource.text,'完整预览\n');assert.equal(h.sent.at(-1).params.blob,undefined);
  const standard=harness({},{hostInfo,hostCapabilities:{downloadFile:{}}});await standard.window.blackboxDownload('test.md','preview');
  assert.ok(standard.sent.at(-1).params.contents);assert.equal(standard.sent.at(-1).params.blob,undefined);
});
test('missing capability metadata does not block an explicit standard request, invalid names still fail',async()=>{
  for(const info of [{},{hostInfo:{name:'chatgpt'}},{hostInfo:{name:'another',client_type:'codex_desktop'}}]) {
    const h=harness({},info);await h.window.blackboxDownload('test.md','preview');
    assert.equal(h.sent.at(-1).method,'ui/download-file');assert.equal(h.sent.at(-1).params.contents[0].resource.text,'preview');
  }
  const h=harness({},{hostCapabilities:{downloadFile:{}}});
  for(const name of ['../test.md','test.exe','https://example.com/test.md'])await assert.rejects(h.window.blackboxDownload(name,'preview'),/无效/);
  assert.equal(h.sent.length,0);
});
test('download host rejection and isError do not become success or leak host error text',async()=>{
  for(const result of [{isError:true},{rpcError:{code:-32602,message:'PRIVATE-HOST-ERROR'}},null]) {
    const h=harness(result,{hostCapabilities:{downloadFile:{}}});
    await assert.rejects(h.window.blackboxDownload('test.md','preview'),e=>/宿主未/.test(e.message)&&!e.message.includes('PRIVATE'));
  }
});
test('download timeout clears pending state and an old reply cannot complete the retry',async()=>{
  const active=new Map();let timerId=0;
  const h=harness({noReply:true},{hostCapabilities:{downloadFile:{}}},{
    setTimeout(fn,ms){active.set(++timerId,{fn,ms});return timerId},clearTimeout(id){active.delete(id)}
  });
  const first=h.window.blackboxDownload('test.md','first');await new Promise(r=>setImmediate(r));
  const oldId=h.sent.at(-1).id;assert.equal(active.size,1);
  const [id,timer]=[...active][0];assert.equal(timer.ms,120000);active.delete(id);timer.fn();
  await assert.rejects(first,/尚未收到下载确认/);assert.equal(active.size,0);
  let finished=false;const retry=h.window.blackboxDownload('test.md','second').then(()=>finished=true);
  await new Promise(r=>setImmediate(r));const newId=h.sent.at(-1).id;assert.notEqual(oldId,newId);
  h.deliver({jsonrpc:'2.0',id:oldId,result:{}});await new Promise(r=>setImmediate(r));assert.equal(finished,false);assert.equal(active.size,1);
  h.deliver({jsonrpc:'2.0',id:newId,result:{}});await retry;assert.equal(finished,true);assert.equal(active.size,0);
});
test('browser fallback opens only a validated local fixed-preview link through the host',async()=>{
  const url='http://127.0.0.1:12345/#token='+ 'A'.repeat(43)+'&record=1&through=8&preview=1';
  const h=harness(m=>m.method==='tools/call'?{content:[],_meta:{blackbox:{status:200,body:{url}}}}:{});
  await h.window.blackboxOpenBrowser({id:'1',through:8,rule:'r1'});
  assert.equal(h.sent.at(-1).method,'ui/open-link');assert.equal(h.sent.at(-1).params.url,url);
  const args=h.sent.find(m=>m.method==='tools/call').params;
  assert.deepEqual(JSON.parse(JSON.stringify(args)),{name:'open_browser_export',arguments:{record_id:'1',through:8,rule:'r1'}});
  assert.ok(!h.sent.some(m=>['ui/message','ui/update-model-context'].includes(m.method)));
});
test('browser fallback rejects foreign URLs, wrong snapshots, stale results and service errors',async()=>{
  const valid='http://127.0.0.1:12345/#token='+ 'A'.repeat(43)+'&record=1&through=8&preview=1';
  for(const url of [valid.replace('127.0.0.1','example.com'),valid.replace('record=1','record=2'),valid+'&extra=x']) {
    const h=harness({_meta:{blackbox:{status:200,body:{url}}}});
    await assert.rejects(h.window.blackboxOpenBrowser({id:'1',through:8,rule:'r1'}),/无效/);
    assert.ok(!h.sent.some(m=>m.method==='ui/open-link'));
  }
  for(const status of [409,503]) {
    const h=harness({_meta:{blackbox:{status,body:{}}}});
    await assert.rejects(h.window.blackboxOpenBrowser({id:'1',through:8,rule:'r1'}),/服务版本|入口未就绪/);
  }
  let current=true;
  const h=harness(()=>{current=false;return {_meta:{blackbox:{status:200,body:{url:valid}}}}});
  await h.window.blackboxOpenBrowser({id:'1',through:8,rule:'r1'},()=>current);
  assert.ok(!h.sent.some(m=>m.method==='ui/open-link'));
});

test('local save sends only the fixed snapshot and UTF-8 preview hash, validates the file receipt',async()=>{
  const body='# 中文\r\n<script>inert</script>\n',digest=require('node:crypto').createHash('sha256').update(body).digest('hex');
  const receipt={path:'C:\\Users\\test\\Downloads\\anomaly-1-through-8 (1).md',name:'anomaly-1-through-8 (1).md',sha256:digest,bytes:Buffer.byteLength(body)};
  const h=harness({content:[],_meta:{blackbox:{status:200,body:receipt}}});
  const saved=await h.window.blackboxSaveExport({id:'1',through:8,rule:'r1',body,preview_sha256:digest});
  assert.equal(saved,receipt);
  assert.deepEqual(JSON.parse(JSON.stringify(h.sent.at(-1).params)),{name:'save_export',arguments:{record_id:'1',through:8,rule:'r1',preview_sha256:digest}});
  assert.ok(!JSON.stringify(h.sent).includes('<script>'));
  assert.ok(!h.sent.some(m=>['ui/download-file','ui/open-link','ui/message','ui/update-model-context'].includes(m.method)));
});

test('save failure or an inconsistent receipt never becomes confirmed success',async()=>{
  const snapshot={id:'1',through:8,rule:'r1',body:'preview',preview_sha256:require('node:crypto').createHash('sha256').update('preview').digest('hex')};
  const receipt={path:'C:\\Downloads\\anomaly-1-through-8.md',name:'anomaly-1-through-8.md',sha256:require('node:crypto').createHash('sha256').update(snapshot.body).digest('hex'),bytes:7};
  for(const body of [{...receipt,sha256:'0'.repeat(64)},{...receipt,bytes:8},{...receipt,name:'wrong.md'},{...receipt,path:'bad\npath'}]) {
    const h=harness({_meta:{blackbox:{status:200,body}}});
    await assert.rejects(h.window.blackboxSaveExport(snapshot),/有效的保存凭据/);
  }
  for(const [status,error,pattern] of [[409,'preview_mismatch',/内容.*不一致/],[503,'downloads_unavailable',/无法找到/],[503,'export_save_failed',/保存未完成/],[503,'export_name_exhausted',/同名副本/]]) {
    const h=harness({_meta:{blackbox:{status,body:{error}}}});
    await assert.rejects(h.window.blackboxSaveExport(snapshot),pattern);
  }
  await assert.rejects(harness({}).window.blackboxSaveExport({...snapshot,id:'../1'}),/预览无效/);
  await assert.rejects(harness({}).window.blackboxSaveExport({...snapshot,preview_sha256:undefined}),/缺少预览校验/);
});

test('save discards stale previews before writing and ignores late results without retrying',async()=>{
  const snapshot={id:'1',through:8,rule:'r1',body:'preview',preview_sha256:require('node:crypto').createHash('sha256').update('preview').digest('hex')};
  const stale=harness({});await stale.window.blackboxSaveExport(snapshot,()=>false);
  assert.ok(!stale.sent.some(m=>m.method==='tools/call'));
  let current=true;
  const late=harness(()=>{current=false;return {_meta:{blackbox:{status:200,body:{}}}}});
  assert.equal(await late.window.blackboxSaveExport(snapshot,()=>current),undefined);
  assert.equal(late.sent.filter(m=>m.method==='tools/call').length,1);
});

test('save timeout reports uncertainty without automatically making another file',async()=>{
  const active=new Map();let timerId=0;
  const h=harness({noReply:true},{},{setTimeout(fn,ms){active.set(++timerId,{fn,ms});return timerId},clearTimeout(id){active.delete(id)}});
  const saving=h.window.blackboxSaveExport({id:'1',through:8,rule:'r1',body:'preview',preview_sha256:'a'.repeat(64)});
  await new Promise(r=>setImmediate(r));
  const [id,timer]=[...active][0];assert.equal(timer.ms,30000);active.delete(id);timer.fn();
  await assert.rejects(saving,/先检查下载文件夹.*避免重复保存/);
  assert.equal(h.sent.filter(m=>m.method==='tools/call').length,1);assert.equal(active.size,0);
});

test('project capture confirmation uses only the app-only management route',async()=>{
 const h=harness({_meta:{blackbox:{status:200,body:{enabled:false}}}});
 const action={path:'D:/project',enabled:false,expected_enabled:true,accept_capture_scope:false};
 await h.window.blackboxRequest('/api/project-management','POST',action);
 assert.equal(h.sent.at(-1).params.name,'manage_project');assert.deepEqual(JSON.parse(JSON.stringify(h.sent.at(-1).params.arguments)),action);
 assert.ok(!h.sent.some(m=>m.method==='ui/message'||m.method==='ui/update-model-context'));
});
test('cleanup forwards frozen confirmation privately through the app-only tool',async()=>{
 const h=harness({_meta:{blackbox:{status:200,body:{reclaimed:true}}}}),action={operation:'apply',value:{scope:{action:'dates',through:12},token:'a'.repeat(64)}};
 await h.window.blackboxRequest('/api/cleanup','POST',action);assert.equal(h.sent.at(-1).params.name,'cleanup');assert.deepEqual(JSON.parse(JSON.stringify(h.sent.at(-1).params.arguments)),action);assert.ok(!h.sent.some(m=>m.method==='ui/message'||m.method==='ui/update-model-context'));
});
