// State/race regression tests without touching real records or installing dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../plugins/blackbox/assets/review.js'), 'utf8');
function harness(filePreview=false) {
  const elements = new Map(), downloads = [], blobs = [];
  function node(tag='div') {
    return {tagName:tag.toUpperCase(), value:'', textContent:'', hidden:false, children:[], dataset:{}, attrs:{},
      append(...items){this.children.push(...items);}, replaceChildren(){this.children=[];},
      setAttribute(k,v){this.attrs[k]=v;}, addEventListener(){}, focus(){}, select(){}, scrollIntoView(){},
      click(){if(this.download) downloads.push(this.download);},
      querySelector(){return this.children[0];}};
  }
  const get = id => {if(!elements.has(id)) elements.set(id,node()); return elements.get(id);};
  const context = vm.createContext({document:{getElementById:get,createElement:node,createTextNode:x=>x},
    location:{hash:'#token=test',protocol:filePreview?'file:':'http:'}, URLSearchParams, AbortSignal, Blob,
    URL:{createObjectURL:blob=> {blobs.push(blob);return 'blob:test';},revokeObjectURL(){}},
    window:{addEventListener(){}}, confirm:()=>true, setTimeout(){},
    fetch:async()=>({ok:true,json:async()=>[]})});
  // Suppress only the two page-load requests; real functions below remain unchanged.
  vm.runInContext(filePreview ? source : source.slice(0,source.indexOf("if (location.protocol === 'file:')")),context);
  return {context,get,downloads,blobs,run:s=>vm.runInContext(s,context)};
}
function record() { return {id:'a',project:'test',display_title:'title',overview:'overview',events:[],source_seq:0}; }
test('late export response cannot preview or download a different selection', async()=>{
  const h=harness(); let resolve;
  h.context.fetch=()=>new Promise(r=>{resolve=r;});
  h.run("selected={id:'a'};selectedRevision={id:'rev-a',number:1}");
  const pending=h.run('exportSaved()');
  h.run("selected={id:'b'};selectedRevision={id:'rev-b',number:9}");
  resolve({ok:true,text:async()=> 'old revision content'}); await pending;
  h.run('downloadSavedPreview()');
  assert.deepEqual(h.downloads,[]);
  assert.equal(h.run('exportSnapshot'),null);
});
test('every historical revision remains readable and exportable without a write workflow', async()=>{
  const h=harness();let posts=0, exportUrl;
  const data=record();data.revisions=[{id:'one',number:1,title:'old title',body:'first saved text',source_seq:1},
    {id:'two',number:2,title:'new title',body:'second saved text',source_seq:2}];
  h.context.fetch=async(url,opts)=>{if(opts.method==='POST') posts++;exportUrl=url;return {ok:true,json:async()=>data,text:async()=> '# full revision\nfirst saved text'};};
  await h.run("openRecord('a')");
  assert.equal(h.get('revision').children.length,3);
  assert.equal(h.run('selectedRevision.id'),'two');
  h.run("selectRevision('one')");assert.equal(h.get('record-heading').textContent,'old title');
  assert.equal(h.get('export').disabled,false);
  await h.run('exportSaved()');assert.equal(exportUrl,'/api/export/one');
  assert.deepEqual(h.downloads,[]);
  h.run('downloadSavedPreview()');
  assert.deepEqual(h.downloads,['investigation-a-v1.md']);
  h.run("selectRevision('')");assert.equal(h.get('export').disabled,true);
  assert.equal(h.run('typeof save'),'undefined');assert.equal(posts,0);
  const html=fs.readFileSync(path.join(__dirname,'../plugins/blackbox/assets/review.html'),'utf8');
  assert.doesNotMatch(html,/<textarea|id="save"|id="summary-request"|id="editor"/);
  assert.doesNotMatch(source,/api\/revisions|api\/summar|beforeunload/);
});

test('preview is the full inert Markdown and download reuses exactly those bytes without a request',async()=>{
  const h=harness();let requests=0;
  const content='# 完整人工版本\r\n\r\n<script>bad()</script> <img src=https://example.org/pixel>\nsecret=fixture\n';
  h.run("selected={id:'a'};selectedRevision={id:'rev-a',number:2}");
  h.context.fetch=async()=>{requests++;return {ok:true,text:async()=>content};};
  await h.run('exportSaved()');
  assert.equal(h.get('export-preview').hidden,false);
  assert.equal(h.get('export-preview-text').textContent,content);
  assert.equal(h.get('export-preview-text').children.length,0);
  assert.equal(h.get('export-download').disabled,false);
  assert.deepEqual(h.downloads,[]);
  h.context.fetch=async()=>{throw new Error('download must not fetch');};
  h.run('downloadSavedPreview()');
  assert.equal(requests,1);
  assert.deepEqual(h.downloads,['investigation-a-v2.md']);
  assert.equal(await h.blobs[0].text(),content);
  assert.equal(h.run('Object.isFrozen(exportSnapshot)'),true);
  assert.doesNotMatch(source,/innerHTML/);
});

test('switching revision clears preview and ignores a late text body',async()=>{
  const h=harness();let resolveBody;
  const data=record();data.revisions=[{id:'one',number:1,title:'one',body:'first'},
    {id:'two',number:2,title:'two',body:'second'}];
  h.context.data=data;h.run("selected=data;selectRevision('one')");
  h.context.fetch=async()=>({ok:true,text:()=>new Promise(resolve=>{resolveBody=resolve;})});
  const pending=h.run('exportSaved()');
  await new Promise(resolve=>setImmediate(resolve));
  h.run("selectRevision('two')");
  resolveBody('late first revision');await pending;
  assert.equal(h.get('export-preview').hidden,true);
  assert.equal(h.get('export-preview-text').textContent,'');
  assert.equal(h.get('export-download').disabled,true);
  h.run('downloadSavedPreview()');assert.deepEqual(h.downloads,[]);
});

test('opening another record immediately cancels preview and blocks old export during loading',async()=>{
  const h=harness();let resolveRecord;
  h.run("selected={id:'a'};selectedRevision={id:'rev-a',number:1}");
  h.context.fetch=async()=>({ok:true,text:async()=> 'first complete export'});
  await h.run('exportSaved()');
  h.context.fetch=()=>new Promise(resolve=>{resolveRecord=resolve;});
  const opening=h.run("openRecord('b')");
  assert.equal(h.get('export-preview').hidden,true);
  assert.equal(h.get('export').disabled,true);
  h.run('downloadSavedPreview()');
  await h.run('exportSaved()');
  assert.deepEqual(h.downloads,[]);
  resolveRecord({ok:true,json:async()=>({...record(),id:'b'})});await opening;
  assert.equal(h.run('selected.id'),'b');
  assert.equal(h.run('exportSnapshot'),null);
});

test('preview failure can retry and closing cancels late responses',async()=>{
  const h=harness();let resolve;
  h.run("selected={id:'a'};selectedRevision={id:'rev-a',number:1}");
  h.context.fetch=async()=>{throw new Error('fixture network failure');};
  await h.run('exportSaved()');
  assert.match(h.get('export-preview-status').textContent,/预览读取失败/);
  assert.equal(h.get('export').disabled,false);
  assert.equal(h.get('export-download').disabled,true);
  h.context.fetch=async()=>({ok:true,text:async()=> 'retry fixed content'});
  await h.run('exportSaved()');
  assert.equal(h.get('export-preview-text').textContent,'retry fixed content');
  h.context.fetch=()=>new Promise(r=>{resolve=r;});
  const pending=h.run('exportSaved()');
  h.run('cancelExportPreview()');
  resolve({ok:true,text:async()=> 'closed late response'});await pending;
  assert.equal(h.get('export-preview').hidden,true);
  assert.equal(h.run('exportSnapshot'),null);
});

test('newer preview wins even when older request completes last',async()=>{
  const h=harness(),pending=[];
  h.run("selected={id:'a'};selectedRevision={id:'rev-a',number:1}");
  h.context.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const older=h.run('exportSaved()'),newer=h.run('exportSaved()');
  pending[1]({ok:true,text:async()=> 'newer preview copy'});await newer;
  pending[0]({ok:true,text:async()=> 'older delayed copy'});await older;
  h.run('downloadSavedPreview()');
  assert.equal(h.get('export-preview-text').textContent,'newer preview copy');
  assert.equal(await h.blobs[0].text(),'newer preview copy');
});
test('all saved model summaries remain readable and distinct from original events', async()=>{
  const h=harness(), data=record();
  const content=JSON.stringify([{heading:'historical heading',items:[{text:'unverified explanation <img src=x>',sources:[{seq:1,quote:'original observation'}]}]}]);
  data.summaries=[{id:'s1',created:'2026-10-07',source_seq:1,content},{id:'s2',source_seq:2,content}];
  h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openRecord('a')");
  assert.equal(h.get('summaries').children.length,2);
  for(const entry of h.get('summaries').children) {
    assert.match(entry.children[1].textContent,/并非原始观察/);
    assert.match(entry.children[2].textContent,/original observation/);
    assert.equal(entry.children[2].tagName,'PRE');
  }
});
test('late opening and search responses cannot overwrite the newest selection', async()=>{
  const h=harness(), pending=[];
  h.context.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const first=h.run("openRecord('a')"),second=h.run("openRecord('b')");
  pending[1]({ok:true,json:async()=>({...record(),id:'b',display_title:'second'})});await second;
  pending[0]({ok:true,json:async()=>record()});await first;
  assert.equal(h.run('selected.id'),'b');assert.equal(h.get('record-heading').textContent,'second');
  const oldList=h.run('list()'),newList=h.run('list()');
  pending[3]({ok:true,json:async()=>[]});await newList;
  pending[2]({ok:true,json:async()=>[{id:'a',display_title:'stale'}]});await oldList;
  assert.equal(h.get('records').children.length,0);
});
test('late failed history requests do not replace a newer successful result', async()=>{
  for(const [first,second,data] of [["openRecord('a')","openRecord('b')",{...record(),id:'b'}],['list()','list()',[]]]) {
    const h=harness(), pending=[];
    h.context.fetch=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
    const old=h.run(first),fresh=h.run(second);
    pending[1].resolve({ok:true,json:async()=>data});await fresh;
    pending[0].reject(new Error('old failure'));await assert.doesNotReject(old);
  }
});
test('history exposes session capture gaps and project-specific diagnostic markers', async()=>{
  const h=harness(), data=record();
  data.events=[{seq:1,kind:'PostToolUse',content:JSON.stringify({_coverage:{capture_gap_before:true}})}];
  h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openRecord('a')");
  assert.match(h.get('status').textContent,/已知采集缺口/);
  h.context.fetch=async()=>({ok:true,json:async()=>({record_count:1,projects:[{path:'D:/project',enabled:true,last_capture_error:{code:'SessionCapacityReached'}}]})});
  await h.run('captureStatus()');
  assert.ok(h.get('capture-projects').children.some(node=>/SessionCapacityReached/.test(node.textContent)));
});
test('history distinguishes unknown rejection from confirmed session gaps',async()=>{
  const h=harness(),data=record();data.events=[{seq:1,kind:'PostToolUse',content:JSON.stringify({_coverage:{capture_gap_before:true,capture_gap_scope:'unknown_project'}})}];
  h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openRecord('a')");
  assert.match(h.get('status').textContent,/归属未知/);assert.match(h.get('status').textContent,/不能断言/);
  assert.doesNotMatch(h.get('status').textContent,/该会话存在已知采集缺口/);
});
test('empty search and list refresh preserve reader status', async()=>{
  const h=harness(); h.get('query').value='missing'; h.get('status').textContent='reading version 1';
  await h.run('list()');
  assert.match(h.get('list-status').textContent,/没有匹配/);
  assert.equal(h.get('status').textContent,'reading version 1');
  assert.equal(h.run('dateLabel(null)'),'时间未知');
});
test('file preview explains launcher and disables unavailable actions',()=>{
  const h=harness(true);
  assert.equal(h.get('file-preview').hidden,false);
  assert.equal(h.get('refresh').disabled,true);
  assert.match(h.get('capture-status').textContent,/文件预览/);
});
test('Markdown uses semantic nodes while HTML, images and URLs remain inert text',()=>{
  const h=harness(); h.run('selected={events:[{seq:1}]}');
  h.context.content='## 标题\n\n- **重点** 和 `code` [来源 #1]\n- 第二项\n\n1. 步骤\n\n> 用户反馈\n\n```js\n<script>bad()</script>\n```\n\n![image](https://example.org/pixel) <img src=x onerror=bad()> [link](javascript:bad())';
  h.run('renderReading(content)');
  const all=[]; function walk(n){if(typeof n==='object'){all.push(n);n.children.forEach(walk);}} walk(h.get('reading'));
  for(const tag of ['H3','UL','OL','LI','BLOCKQUOTE','STRONG','CODE','PRE','BUTTON']) assert.ok(all.some(n=>n.tagName===tag),tag);
  for(const tag of ['SCRIPT','IMG','A']) assert.ok(!all.some(n=>n.tagName===tag),tag);
  assert.ok(all.some(n=>n.tagName==='CODE' && n.textContent.includes('<script>')));
});
