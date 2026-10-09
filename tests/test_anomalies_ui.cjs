const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../plugins/tas-notes/assets/anomalies.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../plugins/tas-notes/assets/anomalies.html'),'utf8');
function harness(options={}) {
  if(typeof options==='boolean') options={preview:options};
  const elements=new Map(),downloads=[],frames=[],navigation=[],windowListeners=new Map();
  let document;
  function node(tag='div') {
    const listeners=new Map();
    return {tagName:tag.toUpperCase(),value:'',textContent:'',children:[],dataset:{},attrs:{},hidden:false,disabled:false,open:false,
      append(...n){this.children.push(...n)},replaceChildren(...n){this.children=[...n]},setAttribute(k,v){this.attrs[k]=v},
      addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn)},
      dispatch(type,event={}){return Promise.all((listeners.get(type)||[]).map(fn=>fn({preventDefault(){},...event})))},
      focus(options){document.activeElement=this;this.lastFocusOptions=options},
      click(){if(this.disabled)return Promise.resolve();if(this.download)downloads.push(this.download);return this.dispatch('click')}};
  }
  const get=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id)};
  document={getElementById:get,createElement:node,activeElement:null,title:''};
  // Use the real initial hidden/disabled state, rather than inventing DOM defaults.
  for(const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*?\bid="([^"]+)"[^>]*)>/gi)) {
    const element=node(match[1]);
    element.hidden=/\bhidden(?:\s|$)/.test(match[2]);element.disabled=/\bdisabled(?:\s|$)/.test(match[2]);
    elements.set(match[3],element);
  }
  get('time').value='all';
  if(!options.startup) {get('project').value='D:/project';get('session').value='i-1';}
  const location={hash:options.hash||'#token=test',protocol:options.preview?'file:':'http:'};
  const stack=[{hash:location.hash,state:null}];let index=0;
  const history={
    get state(){return stack[index].state},
    get length(){return stack.length},
    replaceState(state,_,url){stack[index]={state,hash:url??location.hash};location.hash=stack[index].hash},
    pushState(state,_,url){stack.splice(index+1);stack.push({state,hash:url??location.hash});index++;location.hash=stack[index].hash},
    back(){navigation.push(-1)},forward(){navigation.push(1)}
  };
  const window={scrollY:0,scrollTo(_,y){this.scrollY=y},
    addEventListener(type,fn){if(!windowListeners.has(type))windowListeners.set(type,[]);windowListeners.get(type).push(fn)}};
  const context=vm.createContext({document,location,history,window,URLSearchParams,AbortSignal,
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},setTimeout(){},requestAnimationFrame:fn=>frames.push(fn),
    fetch:options.fetch||(async()=>{throw new Error('unexpected initial network')})});
  vm.runInContext(options.preview||options.startup?source:source.slice(0,source.indexOf("if(location.protocol==='file:')")),context);
  async function flush({paint=true}={}) {
    while(navigation.length) {
      const target=index+navigation.shift();
      if(target<0||target>=stack.length)continue;
      index=target;location.hash=stack[index].hash;
      for(const listener of windowListeners.get('popstate')||[])listener({state:history.state});
    }
    await new Promise(resolve=>setImmediate(resolve));
    if(paint)while(frames.length)frames.shift()();
  }
  return {get,context,downloads,flush,run:s=>vm.runInContext(s,context)};
}
function ok(data) {return {ok:true,json:async()=>data};}
function row(id) {return {id,title:'工具返回 TypeError: '+id,captured:'2026-10-07T00:00:00Z',tool:'Bash',project:'D:/project',investigation:'i-1',session:'raw-1'};}
function detail(id) {return {id,title:'failure '+id,project:'D:/project',investigation:'i-1',session:'raw-1',tool:'Bash',captured:'2026-10-07T00:00:00Z',source_seq:8,rule_version:'v1',
  events:[{seq:1,investigation:'session',captured:'2026-10-07T00:00:00Z',digest:'abc',content:JSON.stringify({tool_input:{command:'<img src=x>'},tool_response:'<script>bad()</script>'})}]};}
test('slow selection cannot overwrite latest selection',async()=>{
  const h=harness();const pending=[];
  h.context.fetch=()=>new Promise(r=>pending.push(r));
  const first=h.run("openItem('1')"),second=h.run("openItem('2')");
  pending[1]({ok:true,json:async()=>detail('2')});await second;
  pending[0]({ok:true,json:async()=>detail('1')});await first;
  assert.equal(h.run('current.id'),'2');
  assert.equal(h.get('record-heading').textContent,'failure 2');
});
test('late failed selection, list and capture requests do not report stale errors',async()=>{
  for(const [first,second,data] of [["openItem('1')","openItem('2')",detail('2')],
    ['load()','load()',{items:[],snapshot_seq:3,next_before:null}],
    ['capture()','capture()',{projects:[],record_count:0}]]) {
    const h=harness(), pending=[];
    h.context.fetch=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
    const old=h.run(first), fresh=h.run(second);
    pending[1].resolve({ok:true,json:async()=>data});await fresh;
    pending[0].reject(new Error('old network failure'));await assert.doesNotReject(old);
  }
});
test('export pins ID and source cutoff while selection changes',async()=>{
  const h=harness();let resolve,url;
  h.context.fetch=p=>{url=p;return new Promise(r=>resolve=r)};
  h.run("current={id:'1',source_seq:8,rule_version:'v1'}");
  const promise=h.run('download()');h.run("current={id:'2',source_seq:9,rule_version:'v1'}");
  resolve({ok:true,blob:async()=>({})});await promise;
  assert.match(url,/anomaly-export\/1\?through=8&rule=v1/);
  assert.deepEqual(h.downloads,['anomaly-1-through-8.md']);
});
test('source content is inert and no review gate exists',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:true,json:async()=>detail('1')});
  await h.run("openItem('1')");
  assert.ok(h.get('reading').children.some(n=>n.textContent==='<script>bad()</script>'));
  const tags=[];function walk(n){tags.push(n.tagName);n.children.forEach(walk)}walk(h.get('reading'));
  assert.ok(!tags.includes('SCRIPT'));assert.ok(!tags.includes('IMG'));
  assert.equal(h.get('export').disabled,false);
  assert.equal(h.get('detail-status').textContent,'');
});
test('empty scan page with a cursor remains traversable and pins snapshot across new arrivals',async()=>{
  const h=harness(), urls=[];
  const pages=[{items:[],snapshot_seq:501,next_before:2,scanned_count:500},
    {items:[{id:1,title:'old error',captured:'2026-10-07',tool:'Bash',project:'p'}],snapshot_seq:501,next_before:null,scanned_count:1,has_newer:true}];
  h.context.fetch=async url=>{urls.push(url);return {ok:true,json:async()=>pages.shift()}};
  await h.run('load()');
  assert.equal(h.get('more').hidden,false);assert.match(h.get('list-status').textContent,/较早记录/);
  h.get('project').value='unsaved filter';
  await h.run('load(true)');
  const next=new URL(urls[1],'http://localhost');
  assert.equal(next.searchParams.get('through'),'501'); assert.equal(next.searchParams.get('before'),'2');
  assert.equal(next.searchParams.get('project_path'),'D:/project');
  assert.equal(h.get('more').hidden,true);assert.match(h.get('list-status').textContent,/有新事件/);
  assert.equal(h.get('records').children.length,1);
  assert.match(h.get('snapshot-info').textContent,/501 个事件/);
});
test('refresh resets the snapshot and stale page responses cannot mix into it',async()=>{
  const h=harness(), pending=[];
  h.context.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const first=h.run('load()'), second=h.run('load()');
  pending[1]({ok:true,json:async()=>({items:[],snapshot_seq:600,next_before:100,scanned_count:500})});await second;
  pending[0]({ok:true,json:async()=>({items:[{id:'stale'}],snapshot_seq:500,next_before:null,scanned_count:500})});await first;
  assert.equal(h.run('snapshotSeq'),600);assert.equal(h.run('nextBefore'),100);
  assert.equal(h.get('records').children.length,0);assert.equal(h.run('loading'),false);
});
test('opening details uses the list snapshot cutoff',async()=>{
  const h=harness();let url;
  h.context.fetch=async path=>{url=path;return {ok:true,json:async()=>detail('1')}};
  h.run('snapshotSeq=501');await h.run("openItem('1')");
  assert.equal(url,'/api/anomaly/1?through=501');
});
test('empty exhausted lists disclose uncertainty',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:true,json:async()=>({items:[],snapshot_seq:0,next_before:null})});
  await h.run('load()');assert.match(h.get('list-status').textContent,/不代表没有问题/);
  assert.equal(h.get('more').hidden,true);
});
test('retained long-output segments and structured returns remain visible and inert',async()=>{
  const h=harness(), data=detail('1');
  const output={preview:'<script>head</script>',tail:'TypeError: tail',truncated:true,original_length:90000};
  data.events[0].content=JSON.stringify({tool_input:{cmd:'run'},tool_response:output});
  h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openItem('1')");
  const text=h.get('reading').children.map(n=>n.textContent).join('\n');
  assert.match(text,/head/);assert.match(text,/TypeError: tail/);assert.match(text,/90000/);
  assert.match(text,/中间内容未保存/);assert.doesNotMatch(text,/未捕获到返回正文/);
  assert.match(h.run('outputText({isError:true,message:"failure"})'),/"isError": true/);
  const legacy=h.run('outputText({preview:"\\\"old encoded preview",truncated:true})');
  assert.match(legacy,/旧格式/);assert.doesNotMatch(legacy,/已保留输出末尾/);
});
test('known capture gap is visible without expanding saved JSON',async()=>{
  const h=harness(), data=detail('1');data.capture_gap=true;
  h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openItem('1')");
  const notice=h.get('reading').children.find(node=>node.className==='notice');
  assert.ok(notice);assert.match(notice.textContent,/这段会话有记录未能保存/);
  assert.match(notice.textContent,/不会跨过缺失部分连接/);
});
test('unknown rejection and legacy boundary notices do not assert current-session data loss',async()=>{
  const h=harness(),data=detail('1');data.capture_gap=true;data.capture_gap_scope='unknown_project';data.legacy_association_boundary=true;
  h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openItem('1')");
  const notices=h.get('reading').children.filter(node=>node.className==='notice').map(node=>node.textContent).join('\n');
  assert.match(notices,/无法确定所属项目或会话/);assert.match(notices,/旧记录缺少判断操作是否连续的信息/);
  assert.doesNotMatch(notices,/这段会话有记录未能保存/);
});
test('unknown input rejection remains visible after another scoped error',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:true,json:async()=>({projects:[],last_capture_error:{code:'NotesError'},last_unscoped_capture_error:{code:'InputTooLarge'}})});
  await h.run('capture()');assert.match(h.get('capture-error').textContent,/NotesError/);
  assert.match(h.get('capture-error').textContent,/InputTooLarge/);assert.match(h.get('capture-error').textContent,/512 KiB/);
  assert.match(h.get('capture-error').textContent,/不能确定所属项目或会话/);
});
test('restored and unreadable diagnostic boundaries do not assert a capture rejection',async()=>{
  for(const [reason,expected] of [['restored_store',/无法确认恢复前后的记录是否连续/],['diagnostic_unreadable',/采集诊断无法读取或内容不一致/]]) {
    const h=harness(),data=detail('1');data.capture_gap=true;data.capture_gap_scope='unknown_project';data.capture_gap_reason=reason;
    h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openItem('1')");
    const notice=h.get('reading').children.find(node=>node.className==='notice').textContent;
    assert.match(notice,expected);assert.doesNotMatch(notice,/有一次记录未能保存|这些记录来自恢复的备份/);
  }
});
test('status explains full capacity without implying old records unreadable',async()=>{
  const h=harness();
  h.context.fetch=async()=>({ok:true,json:async()=>({projects:[{enabled:true,path:'D:/project',last_capture_error:{code:'SessionCapacityReached',time:'2026-10-07T00:00:00Z'}}],record_count:3,runtime:{state:'configured'},
    application:{version:'v2',build:'abcdef',state_directory:'D:/private'},
    storage:{database_bytes:1024*1024,database_limit_bytes:1024*1024,capacity_reached:true},
    last_capture_error:{code:'session_limit',time:'2026-10-07T00:00:00Z'}})});
  await h.run('capture()');
  assert.match(h.get('application-status').textContent,/D:\/private/);
  assert.match(h.get('storage-status').textContent,/已有记录仍可读取/);
  assert.match(h.get('capture-error').textContent,/session_limit/);
  assert.ok(h.get('capture-projects').children.some(node=>/SessionCapacityReached/.test(node.textContent)));
  h.context.fetch=async()=>({ok:true,json:async()=>({projects:[],record_count:0})});
  await h.run('capture()');assert.match(h.get('application-status').textContent,/未提供/);
});
test('shutdown is explicit and authenticated without changing capture authorization',async()=>{
  const h=harness();let requested;
  h.context.fetch=async(path,options)=>{requested={path,options};return {ok:true}};
  await h.run('shutdown()');
  assert.equal(requested.path,'/api/shutdown');assert.equal(requested.options.method,'POST');
  assert.equal(requested.options.headers.Authorization,'Bearer test');
  assert.match(h.get('status').textContent,/采集开关没有改变/);
});
test('file preview disables service actions and retains safe entry explanation',()=>{
  const h=harness(true);assert.equal(h.get('file-preview').hidden,false);
  assert.equal(h.get('refresh').disabled,true);assert.equal(h.get('export').disabled,true);
});
test('failed opening clears the old export and retry keeps the requested snapshot',async()=>{
  const h=harness(),urls=[];
  h.context.fetch=async url=>{urls.push(url);return ok(detail('1'))};
  await h.run("openItem('1',8)");
  h.context.fetch=async url=>{urls.push(url);throw new Error('offline')};
  const failed=h.run("openItem('2',9)");
  assert.equal(h.run('current'),null);assert.equal(h.get('export').disabled,true);
  await assert.rejects(failed,/连接中断/);
  assert.equal(h.get('detail').hidden,true);assert.equal(h.get('retry').hidden,false);
  assert.match(h.get('detail-status').textContent,/连接中断/);
  const count=urls.length;await h.run('download()');assert.equal(urls.length,count);
  h.context.fetch=async url=>{urls.push(url);return ok({...detail('2'),source_seq:9})};
  await h.get('retry').click();await h.flush();
  assert.equal(urls.at(-1),'/api/anomaly/2?through=9');
  assert.equal(h.run('current.id'),'2');assert.equal(h.get('export').disabled,false);
  assert.equal(h.get('retry').hidden,true);assert.equal(h.context.history.length,2);
});

test('return preserves a paged list, filters, snapshot, cursor, scroll and focused card',async()=>{
  const h=harness(),urls=[];
  h.get('project').value='D:/project';h.get('tool').value='Bash';
  h.get('session').value='session-A';h.get('time').value='custom';
  h.get('start').value='2026-10-01';h.get('end').value='2026-10-07';
  const pages=[{items:[row(9)],snapshot_seq:501,next_before:300,scanned_count:201},
    {items:[row(8),row(7)],snapshot_seq:501,next_before:100,scanned_count:200}];
  h.context.fetch=async url=>{urls.push(url);return ok(pages.shift())};
  await h.run('load()');await h.run('load(true)');
  const cards=[...h.get('records').children],applied=h.run('activeFilters'),status=h.get('list-status').textContent;
  // Unsaved edits and the applied query are separate state and both survive navigation.
  h.get('project').value='draft filter';h.get('help').open=true;h.context.window.scrollY=735;
  h.context.fetch=async url=>{urls.push(url);return ok({...detail(8),source_seq:501})};
  await cards[1].click();
  assert.equal(h.get('list-view').hidden,true);assert.equal(h.get('detail-view').hidden,false);
  assert.equal(h.context.window.scrollY,0);assert.equal(h.get('help').open,false);
  assert.equal(h.context.document.activeElement,h.get('record-heading'));
  const requestCount=urls.length;
  await h.get('back').click();await h.flush();
  assert.equal(urls.length,requestCount,'return must reuse the already loaded list');
  assert.equal(h.get('list-view').hidden,false);assert.equal(h.get('detail-view').hidden,true);
  assert.equal(h.get('records').children.length,3);
  cards.forEach((card,i)=>assert.equal(h.get('records').children[i],card));
  assert.equal(h.get('project').value,'draft filter');assert.equal(h.get('tool').value,'Bash');
  assert.equal(h.get('session').value,'session-A');assert.equal(h.get('time').value,'custom');
  assert.equal(h.get('start').value,'2026-10-01');assert.equal(h.get('end').value,'2026-10-07');
  assert.equal(h.run('activeFilters'),applied);assert.equal(h.run('snapshotSeq'),501);
  assert.equal(h.run('nextBefore'),100);assert.equal(h.run('scannedCount'),401);
  assert.equal(h.get('list-status').textContent,status);assert.equal(h.get('help').open,true);
  assert.equal(h.context.window.scrollY,735);assert.equal(h.context.document.activeElement,cards[1]);
  assert.equal(cards[1].lastFocusOptions.preventScroll,true);
  assert.equal(h.context.location.hash,'#token=test');
});

test('return invalidates pending detail success and failure before browser history completes',async()=>{
  for(const fail of [false,true]) {
    const h=harness();let resolve,reject;
    h.context.window.scrollY=340;
    h.context.fetch=()=>new Promise((yes,no)=>{resolve=yes;reject=no});
    const opening=h.run("openItem('1',501)");
    h.run('backToList()');
    assert.equal(h.get('list-view').hidden,false);assert.equal(h.get('detail-view').hidden,true);
    // Deliver before queued popstate, matching the race between a reply and browser Back.
    if(fail)reject(new Error('late failure'));else resolve(ok(detail('1')));
    await assert.doesNotReject(opening);await h.flush();
    assert.equal(h.run('view'),'list');assert.equal(h.run('current'),null);
    assert.equal(h.get('detail-view').hidden,true);assert.equal(h.get('export').disabled,true);
    assert.equal(h.get('retry').hidden,true);assert.equal(h.get('status').textContent,'');
    assert.doesNotMatch(h.get('detail-status').textContent,/连接中断/);
    assert.equal(h.context.window.scrollY,340);
  }
});

test('browser Back and Forward reuse the original detail cutoff after a newer list refresh',async()=>{
  const h=harness(),urls=[];
  h.context.fetch=async url=>{urls.push(url);return ok(url.startsWith('/api/anomalies?')
    ? {items:[row(1)],snapshot_seq:501,next_before:null,scanned_count:1}
    : {...detail(1),source_seq:501})};
  await h.run('load()');h.context.window.scrollY=440;await h.get('records').children[0].click();
  assert.match(h.context.location.hash,/record=1&through=501/);
  h.context.history.back();await h.flush();
  assert.equal(h.run('view'),'list');assert.equal(h.context.window.scrollY,440);
  h.context.fetch=async url=>{urls.push(url);return ok(url.startsWith('/api/anomalies?')
    ? {items:[row(2)],snapshot_seq:999,next_before:null,scanned_count:2}
    : {...detail(1),source_seq:501})};
  await h.run('load()');
  h.context.history.forward();await h.flush();
  assert.equal(urls.at(-1),'/api/anomaly/1?through=501');
  assert.equal(h.run('current.source_seq'),501);assert.equal(h.run('snapshotSeq'),999);
  assert.equal(h.get('list-view').hidden,true);
  h.context.history.back();await h.flush();
  assert.equal(h.run('view'),'list');assert.equal(h.get('records').children[0].dataset.recordId,'2');
});

test('fast detail switching replaces a single history step and never exports the previous record',async()=>{
  const h=harness(),pending=[];
  h.context.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const one=h.run("openItem('1',501)");
  const two=h.run("openItem('2',502)");
  assert.equal(h.context.history.length,2);assert.equal(h.run('current'),null);
  assert.equal(h.get('export').disabled,true);assert.match(h.context.location.hash,/record=2&through=502/);
  pending[1](ok({...detail(2),source_seq:502}));await two;
  pending[0](ok({...detail(1),source_seq:501}));await one;
  assert.equal(h.run('current.id'),2);assert.equal(h.run('current.source_seq'),502);
  h.context.history.back();await h.flush();
  assert.equal(h.run('view'),'list');assert.equal(h.context.location.hash,'#token=test');
});

test('pending pagination and refresh cannot replace the list while its detail is being read',async()=>{
  for(const append of [true,false])for(const fail of [false,true]) {
    const h=harness();
    h.get('project').value='original';
    h.context.fetch=async()=>ok({items:[row(1)],snapshot_seq:501,next_before:100,scanned_count:401});
    await h.run('load()');
    const card=h.get('records').children[0],applied=h.run('activeFilters');
    const pending=[];h.context.fetch=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
    if(!append)h.get('project').value='new filter';
    const old=h.run(append?'load(true)':'load()');
    const reading=h.run("openItem('1',501)");
    pending[1].resolve(ok({...detail(1),source_seq:501}));await reading;
    if(fail)pending[0].reject(new Error('late list failure'));
    else pending[0].resolve(ok({items:[row(2)],snapshot_seq:999,next_before:null,scanned_count:50}));
    await assert.doesNotReject(old);
    assert.equal(h.run('view'),'detail');assert.equal(h.run('current.id'),1);
    assert.equal(h.get('records').children.length,1);assert.equal(h.get('records').children[0],card);
    assert.equal(h.run('snapshotSeq'),501);assert.equal(h.run('nextBefore'),100);
    assert.equal(h.run('scannedCount'),401);assert.equal(h.run('activeFilters'),applied);
    assert.equal(h.run('loading'),false);assert.equal(h.get('more').disabled,false);
    assert.equal(h.get('list-view').attrs['aria-busy'],'false');assert.equal(h.get('status').textContent,'');
    h.run('backToList()');await h.flush();
    assert.equal(h.get('records').children[0],card);assert.equal(h.run('nextBefore'),100);
  }
});

test('a direct detail route loads its own session return list without sending the token to the API',async()=>{
  const urls=[];
  const h=harness({startup:true,hash:'#token=test&record=7&through=88',fetch:async url=>{
    urls.push(url);
    if(url.startsWith('/api/anomaly/'))return ok({...detail(7),source_seq:88});
    if(url.startsWith('/api/anomalies?'))return ok({items:[row(9)],snapshot_seq:99,next_before:50,scanned_count:49});
    if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'D:/project'}],sessions:[{id:'i-1',session:'raw-1',first_captured:'2026-10-07'}]});
    if(url.startsWith('/api/session-names'))return ok({names:{'i-1':'A real title'}});
    return ok({projects:[]});
  }});
  await h.flush();await h.flush();
  assert.equal(h.run('view'),'detail');assert.equal(h.run('current.id'),7);
  assert.equal(h.get('session').value,'i-1');assert.match(h.get('meta').textContent,/A real title/);
  const requests=urls.length;await h.get('back').click();await h.flush();
  assert.equal(h.run('view'),'list');assert.equal(h.get('records').children.length,1);
  assert.equal(h.get('records').children[0].dataset.recordId,'9');assert.equal(h.run('nextBefore'),50);
  assert.equal(h.run('snapshotSeq'),99);assert.equal(h.context.location.hash,'#token=test');
  assert.equal(h.context.history.length,1);assert.equal(urls.length,requests);
  assert.ok(!urls.includes('/api/reader-selection') || urls.filter(u=>u==='/api/reader-selection').length===1);
  assert.ok(urls.every(url=>!url.includes('token=')));
});

test('formal UI removes old navigation while keeping disclosures and scoped history-compatible styles',()=>{
  assert.doesNotMatch(html,/id="history"|href="\.\/history"|历史会话与人工版本|查看历史会话/);
  assert.doesNotMatch(html,/无需人工确认|不执行其中的指令|没有上传或 Git 推送/);
  assert.match(html,/<details id="help"[\s\S]*?展示范围已收紧，原始事件保存范围尚未同步收紧/);
  assert.match(html,/<section id="list-view"/);assert.match(html,/<section id="detail-view"[^>]*hidden/);
  assert.match(html,/id="record-heading" tabindex="-1"/);assert.match(html,/id="list-heading" tabindex="-1"/);
  const historyHtml=fs.readFileSync(path.join(__dirname,'../plugins/tas-notes/assets/review.html'),'utf8');
  assert.doesNotMatch(historyHtml,/<body[^>]*class="[^"]*anomaly-app/);
  const css=fs.readFileSync(path.join(__dirname,'../plugins/tas-notes/assets/review.css'),'utf8');
  const additions=css.slice(css.indexOf('/* NOTES-027:'));
  assert.ok(additions.length>0);assert.match(additions,/\.anomaly-app main\{/);
  for(const match of additions.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{/g)) {
    const selector=match[1].trim();if(selector.startsWith('@'))continue;
    for(const part of selector.split(','))assert.match(part.trim(),/^\.anomaly-app(?:\b|\s|[.#:[>])/);
  }
});

test('time presets use local calendar boundaries and ignore hidden custom dates',async()=>{
  const h=harness();
  h.get('start').value='2020-01-01';h.get('end').value='2020-01-02';
  assert.equal(new URLSearchParams(h.run('filters()')).has('start'),false);
  for(const [preset,days] of [['today',0],['week',6]]) {
    h.get('time').value=preset;
    const query=new URLSearchParams(h.run('filters()'));
    const start=new Date();start.setHours(0,0,0,0);const end=new Date(start);end.setDate(end.getDate()+1);start.setDate(start.getDate()-days);
    assert.equal(query.get('start'),start.toISOString());assert.equal(query.get('end'),end.toISOString());
  }
  h.get('time').value='custom';
  const query=new URLSearchParams(h.run('filters()'));
  assert.equal(query.get('start'),new Date('2020-01-01T00:00:00').toISOString());
  assert.equal(query.get('end'),new Date('2020-01-03T00:00:00').toISOString());
  h.get('end').value='2019-12-31';assert.throws(()=>h.run('filters()'),/有效的记录时间范围/);
});

test('custom range is revealed only on demand, reset clears filters within selected session',async()=>{
  const h=harness();h.context.fetch=async url=>ok(url.startsWith('/api/anomaly-contexts')?{projects:[],sessions:[]}:{items:[],snapshot_seq:0,next_before:null});
  assert.equal(h.get('date-range').hidden,true);
  h.get('time').value='custom';await h.get('time').dispatch('change');assert.equal(h.get('date-range').hidden,false);
  h.get('project').value='p';h.get('session').value='s';h.get('start').value='2026-10-08';
  await h.get('clear').click();await h.flush();
  assert.equal(h.get('project').value,'p');assert.equal(h.get('session').value,'s');
  assert.equal(h.get('time').value,'all');assert.equal(h.get('date-range').hidden,true);assert.equal(h.get('start').value,'');
});

test('exact project and investigation filters stay pinned across pagination',async()=>{
  const h=harness(),urls=[];
  h.get('project').value='D:/project';h.get('session').value='i-1';h.get('time').value='today';
  h.context.fetch=async url=>{urls.push(url);return ok({items:[],snapshot_seq:500,next_before:12})};
  await h.run('load()');h.get('project').value='D:/project-copy';h.get('session').value='i-2';h.get('time').value='all';await h.run('load(true)');
  const first=new URL(urls[0],'http://local').searchParams,second=new URL(urls[1],'http://local').searchParams;
  assert.equal(first.get('project_path'),'D:/project');assert.equal(first.get('investigation'),'i-1');assert.equal(first.has('project'),false);
  for(const key of ['project_path','investigation','start','end'])assert.equal(second.get(key),first.get(key));
  assert.equal(second.get('through'),'500');
});

test('late project metadata cannot overwrite a newer navigation refresh',async()=>{
  const h=harness(),pending=[];
  h.context.fetch=url=>{
    if(url==='/api/anomaly-contexts')return new Promise(resolve=>pending.push(resolve));
    if(url.startsWith('/api/anomaly-contexts?'))return Promise.resolve(ok({sessions:[{id:'b2',session:'raw-B'}]}));
    if(url.startsWith('/api/session-names'))return Promise.resolve(ok({names:{b2:'Chat B'}}));
    if(url.startsWith('/api/anomalies?'))return Promise.resolve(ok({items:[],snapshot_seq:3,next_before:null}));
    return Promise.resolve(ok({}));
  };
  const old=h.run('contexts()'),fresh=h.run('contexts()');
  pending[1](ok({projects:[{path:'B'}]}));await fresh;
  pending[0](ok({projects:[{path:'A'}]}));await old;
  assert.equal(h.get('project').value,'B');assert.equal(h.get('session').value,'b2');
  assert.deepEqual(h.run('Array.from(projects.keys())').join(','),'B');
  assert.equal(h.get('list-heading').textContent,'Chat B');
});

test('failed project metadata can be retried without loading an unscoped list',async()=>{
  const h=harness(),urls=[];h.get('project').value='';h.get('session').value='';
  h.context.fetch=async url=>{urls.push(url);throw Error('offline')};
  await h.run('contexts()');await h.run('load()');assert.match(h.get('context-status').textContent,/刷新项目重试/);
  assert.ok(urls.every(u=>!u.startsWith('/api/anomalies?')));
  h.context.fetch=async()=>ok({projects:[],sessions:[]});await h.run('contexts()');
  assert.match(h.get('context-status').textContent,/尚无已保存的项目记录/);
});

test('encoding notice follows displayed output only and absent repeats stay silent',async()=>{
  for(const [content,warn] of [
    [{tool_input:{command:'echo \ufffd'},tool_response:'readable',meta:'\ufffd'},false],
    [{tool_response:'bad \ufffd'},true],
    [{tool_response:{truncated:true,preview:'head',tail:'tail \ufffd',original_length:90000}},true],
    [{tool_response:{truncated:true,preview:'head',tail:'tail',original_length:90000,hidden:'\ufffd'}},false]
  ]) {
    const h=harness(),data={...detail(1),session:'saved-session'};data.events[0].content=JSON.stringify(content);
    h.context.fetch=async()=>ok(data);await h.run('openItem(1)');
    const text=h.get('reading').children.map(n=>n.textContent).join('\n');
    assert.equal(text.includes('部分文字可能显示不完整'),warn);assert.doesNotMatch(text,/没有找到可关联|原始文字可能已丢失|仅连接同一会话/);
    assert.doesNotMatch(h.get('meta').textContent,/saved-session/);assert.match(h.get('detail-snapshot').textContent,/saved-session/);assert.equal(h.get('source-details').open,false);
  }
  assert.match(html,/<summary>技术详情<\/summary>/);
});

test('session names are inert and raw session IDs stay out of reading labels',async()=>{
  const h=harness();h.context.fetch=async url=>{
    if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'D:/project',enabled:false}],sessions:[{id:'i-1',session:'raw-secret-id'}]});
    if(url.startsWith('/api/session-names'))return ok({names:{'i-1':'<img src=x>'}});
    return ok({items:[row(1)],snapshot_seq:1,next_before:null});
  };
  await h.run('contexts()');await h.flush();
  const label=h.run("sessionGroups.get('D:/project').buttons.get('i-1').label");
  assert.equal(label.textContent,'<img src=x>');assert.equal(label.children.length,0);
  assert.equal(h.get('list-heading').textContent,'<img src=x>');
  assert.ok(h.get('records').children[0].children.every(n=>n.className!=='card-session'));
  assert.doesNotMatch(h.get('project-tree').children[0].children[0].textContent,/停用|raw-secret/);
});

test('invalid custom range cannot strand an in-flight page in busy state',async()=>{
  const h=harness();let resolve;
  h.context.fetch=()=>new Promise(r=>resolve=r);const pending=h.run('load()');
  h.get('time').value='custom';h.get('start').value='2026-10-08';h.get('end').value='2026-10-01';
  await assert.rejects(h.run('load()'),/有效的记录时间范围/);
  resolve(ok({items:[],snapshot_seq:500,next_before:12}));await pending;
  assert.equal(h.run('loading'),false);assert.equal(h.get('more').disabled,false);assert.equal(h.get('list-view').attrs['aria-busy'],'false');
  assert.equal(h.run('nextBefore'),12);
});

test('startup restores the remembered session and falls back to the latest saved session',async()=>{
  for(const valid of [true,false]) {
    const urls=[],writes=[];
    const h=harness({startup:true,fetch:async(url,options)=>{
      urls.push(url);
      if(url==='/api/reader-selection' && options.method==='POST') {writes.push(JSON.parse(options.body));return ok({});}
      if(url==='/api/reader-selection')return ok({project_path:valid?'P':'removed',investigation:valid?'old':'removed'});
      if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'P'}],sessions:[{id:'new',session:'raw-new',first_captured:'2026-10-08'},{id:'old',session:'raw-old',first_captured:'2026-10-01'}]});
      if(url.startsWith('/api/session-names'))return ok({names:{new:'Latest chat',old:'Remembered chat'}});
      if(url.startsWith('/api/anomalies?'))return ok({items:[],snapshot_seq:3,next_before:null});
      return ok({projects:[]});
    }});
    await h.flush();await h.flush();
    const expected=valid?'old':'new';
    assert.equal(h.get('session').value,expected);assert.equal(h.get('project').value,'P');
    const lists=urls.filter(u=>u.startsWith('/api/anomalies?'));
    assert.equal(lists.length,1);assert.equal(new URL(lists[0],'http://local').searchParams.get('investigation'),expected);
    assert.deepEqual(writes,[{investigation:expected}]);
  }
});

test('rapid first-load A to B to A requests A again rather than caching an empty loading view',async()=>{
  const h=harness(),pending=[];
  h.context.fetch=url=>new Promise(resolve=>pending.push({url,resolve}));
  const first=h.run("selectSession('P',{id:'A'},{remember:false})");
  const second=h.run("selectSession('P',{id:'B'},{remember:false})");
  const third=h.run("selectSession('P',{id:'A'},{remember:false})");
  assert.equal(pending.length,3);
  pending[0].resolve(ok({items:[row(1)],snapshot_seq:10,next_before:null}));await first;
  pending[1].resolve(ok({items:[row(2)],snapshot_seq:11,next_before:null}));await second;
  pending[2].resolve(ok({items:[row(3)],snapshot_seq:12,next_before:null}));await third;
  assert.equal(h.get('session').value,'A');assert.equal(h.get('records').children[0].dataset.recordId,'3');assert.equal(h.run('snapshotSeq'),12);
});

test('switching saved sessions restores their cards, filters, cursor and scroll without another list fetch',async()=>{
  const h=harness(),urls=[];
  h.context.fetch=async url=>{urls.push(url);return ok({items:[row(url.includes('investigation=A')?1:2)],snapshot_seq:10,next_before:5,scanned_count:5})};
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  const card=h.get('records').children[0];h.get('tool').value='draft tool';h.context.window.scrollY=410;
  await h.run("selectSession('P',{id:'B'},{remember:false})");await h.flush();
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  assert.equal(urls.length,2);assert.equal(h.get('records').children[0],card);assert.equal(h.run('nextBefore'),5);
  assert.equal(h.get('tool').value,'draft tool');assert.equal(h.context.window.scrollY,410);
});

test('delayed titles update the selected chat name without blocking records or replacing selection',async()=>{
  const h=harness();let resolveNames;
  h.context.fetch=async url=>{
    if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'P'}],sessions:[{id:'A',session:'raw-A'},{id:'B',session:'raw-B'}]});
    if(url.startsWith('/api/session-names'))return new Promise(resolve=>resolveNames=resolve);
    return ok({items:[row(1)],snapshot_seq:10,next_before:null});
  };
  await h.run('contexts()');assert.equal(h.get('records').children.length,1);assert.equal(h.get('list-heading').textContent,'未命名会话');
  await h.run("selectSession('P',{id:'B'},{remember:false})");
  resolveNames(ok({names:{A:'Name A',B:'Name B'}}));await h.flush();
  assert.equal(h.get('session').value,'B');assert.equal(h.get('list-heading').textContent,'Name B');
  assert.equal(h.get('records').children.length,1);
});

test('reader preference writes are serialized and failures do not stop later choices',async()=>{
  const h=harness(),pending=[];
  h.context.fetch=(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}));
  h.run("rememberSession('A');rememberSession('B')");await h.flush();
  assert.equal(pending.length,1);assert.deepEqual(JSON.parse(pending[0].options.body),{investigation:'A'});
  pending[0].reject(Error('offline'));await h.flush();
  assert.equal(pending.length,2);assert.deepEqual(JSON.parse(pending[1].options.body),{investigation:'B'});
  pending[1].resolve(ok({}));await h.run('preferenceQueue');
  assert.equal(h.get('status').textContent,'');
});

test('mobile navigation returns to the same session scroll and desktop reselect does not jump',async()=>{
  const h=harness();h.context.fetch=async()=>ok({items:[row(1)],snapshot_seq:10,next_before:null});
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();h.context.window.scrollY=350;
  h.run('showNavigation()');await h.flush();assert.equal(h.get('workspace').dataset.stage,'navigation');
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  assert.equal(h.get('workspace').dataset.stage,'reading');assert.equal(h.context.window.scrollY,350);
  h.context.window.scrollY=450;await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  assert.equal(h.context.window.scrollY,450);
});

test('switching chats through mobile navigation preserves the previous reading scroll',async()=>{
  const h=harness();h.context.fetch=async()=>ok({items:[row(1)],snapshot_seq:10,next_before:null});
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();h.context.window.scrollY=800;
  h.run('showNavigation()');await h.flush();
  await h.run("selectSession('P',{id:'B'},{remember:false})");await h.flush();
  h.run('showNavigation()');await h.flush();
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  assert.equal(h.context.window.scrollY,800);
});

test('browser Forward to a previous chat detail aligns navigation and its return list',async()=>{
  const h=harness();
  h.context.fetch=async url=>ok(url.startsWith('/api/anomaly/')?{...detail(1),project:'P',investigation:'A',source_seq:10}
    :{items:[row(url.includes('investigation=B')?2:1)],snapshot_seq:10,next_before:null});
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  h.context.window.scrollY=230;await h.run('openItem(1,10)');
  h.context.history.back();await h.flush();
  await h.run("selectSession('P',{id:'B'},{remember:false})");await h.flush();
  assert.equal(h.get('records').children[0].dataset.recordId,'2');
  h.context.history.forward();await h.flush();
  assert.equal(h.run('current.investigation'),'A');assert.equal(h.run('selectedSession.id'),'A');
  assert.equal(h.run('current.source_seq'),10);
  h.context.history.back();await h.flush();
  assert.equal(h.get('records').children[0].dataset.recordId,'1');assert.equal(h.get('session').value,'A');
  assert.equal(h.context.window.scrollY,230);
});

test('an immediate session switch waits for requested Back and preserves Forward to the old detail',async()=>{
  const h=harness();
  h.context.fetch=async url=>ok(url.startsWith('/api/anomaly/')?{...detail(1),project:'P',investigation:'A',source_seq:10}
    :{items:[row(url.includes('investigation=B')?2:1)],snapshot_seq:10,next_before:null});
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  h.get('time').value='today';h.get('tool').value='Bash';await h.run('load()');
  const card=h.get('records').children[0];h.context.window.scrollY=655;
  await h.run('openItem(1,10)');
  h.run('backToList()');
  const switching=h.run("selectSession('P',{id:'B'},{remember:false})");
  assert.equal(h.run('selectedSession.id'),'A','selection must wait for pending history traversal');
  assert.match(h.context.location.hash,/record=1&through=10/,'old detail history must not be replaced before Back');
  await h.flush();await switching;
  assert.equal(h.run('selectedSession.id'),'B');assert.equal(h.context.location.hash,'#token=test');
  assert.equal(h.run("sessionViews.get('A').scroll"),655);
  assert.equal(h.run("sessionViews.get('A').controls.time"),'today');
  assert.equal(h.run("sessionViews.get('A').controls.tool"),'Bash');
  h.context.history.forward();await h.flush();
  assert.equal(h.run('current.investigation'),'A');assert.equal(h.run('current.source_seq'),10);
  assert.equal(h.run('selectedSession.id'),'A');
  h.context.history.back();await h.flush();
  assert.equal(h.get('time').value,'today');assert.equal(h.get('tool').value,'Bash');
  assert.equal(h.get('records').children[0],card);assert.equal(h.run('snapshotSeq'),10);
  assert.equal(h.context.window.scrollY,655);
});

test('session switching before Back scroll restoration paints saves the intended reading position',async()=>{
  const h=harness();
  h.context.fetch=async url=>ok(url.startsWith('/api/anomaly/')?{...detail(1),project:'P',investigation:'A',source_seq:10}
    :{items:[row(url.includes('investigation=B')?2:1)],snapshot_seq:10,next_before:null});
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  h.get('time').value='today';h.get('tool').value='Bash';await h.run('load()');
  h.context.window.scrollY=655;await h.run('openItem(1,10)');
  h.context.history.back();await h.flush({paint:false});
  assert.equal(h.run('view'),'list');assert.equal(h.context.window.scrollY,0,'the browser has not painted the restored scroll yet');
  await h.run("selectSession('P',{id:'B'},{remember:false})");
  assert.equal(h.run("sessionViews.get('A').scroll"),655);
  await h.flush();
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  assert.equal(h.context.window.scrollY,655);
  assert.equal(h.get('time').value,'today');assert.equal(h.get('tool').value,'Bash');
  assert.equal(h.run('snapshotSeq'),10);assert.equal(h.get('records').children[0].dataset.recordId,'1');
});

test('opening another card immediately after Back waits before creating the new detail history entry',async()=>{
  const h=harness(),urls=[];
  h.context.fetch=async url=>{
    urls.push(url);
    if(url.startsWith('/api/anomaly/')) {
      const id=new URL(url,'http://localhost').pathname.split('/').at(-1);
      return ok({...detail(id),project:'P',investigation:'A',source_seq:10});
    }
    return ok({items:[row(1),row(2)],snapshot_seq:10,next_before:null});
  };
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  h.get('time').value='today';h.get('tool').value='Bash';await h.run('load()');
  const cards=[...h.get('records').children];h.context.window.scrollY=655;
  await h.run('openItem(1,10)');h.run('backToList()');
  const requestCount=urls.length,opening=h.run('openItem(2,10)');
  assert.equal(urls.length,requestCount,'new card waits for the requested Back');
  assert.match(h.context.location.hash,/record=1&through=10/);
  await h.flush();await opening;
  assert.equal(h.run('current.id'),'2');assert.equal(h.run('current.source_seq'),10);
  assert.equal(h.context.history.length,2,'new detail replaces the forward branch, not the pending Back destination');
  assert.match(h.context.location.hash,/record=2&through=10/);
  h.context.history.back();await h.flush();
  assert.equal(h.run('view'),'list');assert.equal(h.context.location.hash,'#token=test');
  assert.equal(h.get('time').value,'today');assert.equal(h.get('tool').value,'Bash');
  cards.forEach((card,i)=>assert.equal(h.get('records').children[i],card));
  assert.equal(h.run('snapshotSeq'),10);assert.equal(h.context.window.scrollY,655);
  h.context.history.forward();await h.flush();
  assert.equal(h.run('current.id'),'2');assert.equal(urls.at(-1),'/api/anomaly/2?through=10');
});
