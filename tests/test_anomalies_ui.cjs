const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../plugins/blackbox/assets/anomalies.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../plugins/blackbox/assets/anomalies.html'),'utf8');
test('filter replacement waits for matching totals before committing cards and status',async()=>{
 const h=harness();h.run("selectedSession={id:'A'}");
 h.context.fetch=async url=>ok(url.includes('anomaly-types')?{types:[{name:'Error',count:4}]}:{items:[row(1)],snapshot_seq:10,next_before:2,total_count:4});
 await h.run('load()');const old=h.get('records').children[0];let finish;
 h.context.fetch=async url=>url.includes('anomaly-types')?new Promise(r=>finish=r):ok({items:[row(2)],snapshot_seq:11,next_before:null,total_count:1});
 h.get('time').value='today';const pending=h.run('load()');await h.flush();
 assert.equal(h.get('list-view').attrs['aria-busy'],'true');assert.equal(h.get('list-view').dataset.replacing,'true');assert.equal(h.get('list-loading').hidden,false);
 assert.equal(h.get('records').children[0],old);assert.equal(h.get('list-status').textContent,'4 条异常记录');
 h.run('saveSessionView()');assert.equal(h.run('sessionViews.size'),0);
 finish(ok({types:[{name:'Error',count:5}]}));await pending;
 assert.equal(h.get('records').children[0].dataset.recordId,'2');assert.equal(h.get('list-status').textContent,'1 条异常记录');assert.match(h.get('record-count').textContent,/1 \/ 5 条/);assert.equal(h.get('list-view').attrs['aria-busy'],'false');assert.equal(h.get('list-loading').hidden,true);
});
test('late same-session type totals cannot replace a newer filter result',async()=>{
 const h=harness();h.run("selectedSession={id:'A'}");let finish;
 h.context.fetch=async url=>url.includes('anomaly-types')?new Promise(r=>finish=r):ok({items:[row(1)],snapshot_seq:10,next_before:null,total_count:8});
 const first=h.run('load()');await h.flush();
 h.context.fetch=async url=>ok(url.includes('anomaly-types')?{types:[{name:'New',count:3}]}:{items:[row(2)],snapshot_seq:11,next_before:null,total_count:1});
 await h.run('load()');finish(ok({types:[{name:'Old',count:80}]}));await first;
 assert.equal(h.get('records').children[0].dataset.recordId,'2');assert.match(h.get('record-count').textContent,/1 \/ 3 条/);assert.equal(h.get('list-status').textContent,'1 条异常记录');
});
test('failed replacement does not present old cards under new filters and can retry',async()=>{
 const h=harness();h.context.fetch=async()=>ok({items:[row(1)],snapshot_seq:10,next_before:2,total_count:4});await h.run('load()');
 h.context.fetch=async()=>{throw Error('offline')};await assert.rejects(h.run('load()'));
 assert.equal(h.get('records').children.length,0);assert.equal(h.get('record-count').textContent,'');assert.equal(h.get('more').hidden,true);assert.match(h.get('list-status').textContent,/重试/);assert.equal(h.get('list-view').attrs['aria-busy'],'false');
 h.context.fetch=async()=>ok({items:[row(2)],snapshot_seq:11,next_before:null,total_count:1});await h.run('load()');assert.equal(h.get('records').children[0].dataset.recordId,'2');
});
function harness(options={}) {
  if(typeof options==='boolean') options={preview:options};
  const elements=new Map(),downloads=[],blobs=[],frames=[],navigation=[],windowListeners=new Map();
  let document;
  function node(tag='div') {
    const listeners=new Map();
    return {tagName:tag.toUpperCase(),value:'',textContent:'',children:[],parentNode:null,dataset:{},attrs:{},hidden:false,disabled:false,open:false,
      append(...nodes){for(const child of nodes){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(n=>n!==child);child.parentNode=this;this.children.push(child)}},
      replaceChildren(...nodes){for(const child of this.children)child.parentNode=null;this.children=[];this.append(...nodes)},setAttribute(k,v){this.attrs[k]=v},
      addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn)},
      dispatch(type,event={}){return Promise.all((listeners.get(type)||[]).map(fn=>fn({preventDefault(){},...event})))},
      focus(options){document.activeElement=this;this.lastFocusOptions=options},
      contains(target){return target===this || this.children.some(child=>child.contains(target))},
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
  const window={matchMedia:()=>({matches:!!options.wide,addEventListener(){}}),scrollY:0,scrollTo(_,y){this.scrollY=y},
    addEventListener(type,fn){if(!windowListeners.has(type))windowListeners.set(type,[]);windowListeners.get(type).push(fn)}};
  const context=vm.createContext({document,location,history,window,navigator:{clipboard:{writeText:async()=>{}}},URLSearchParams,AbortSignal,Blob,
    URL:{createObjectURL:blob=>{blobs.push(blob);return 'blob:test'},revokeObjectURL(){}},setTimeout(){},requestAnimationFrame:fn=>frames.push(fn),
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
  return {get,context,downloads,blobs,flush,windowEvent:(type,event)=>Promise.all((windowListeners.get(type)||[]).map(fn=>fn(event))),run:s=>vm.runInContext(s,context)};
}
function descendants(n) {return n.children.flatMap(c=>[c,...descendants(c)]);}
test('relative dates use local calendar days across year boundaries and preserve seconds',()=>{
 const h=harness();
 assert.equal(h.run("dateText(new Date(2026,0,1,0,0,1),new Date(2026,0,1,0,1))"),'今天 00:00:01');
 assert.equal(h.run("dateText(new Date(2025,11,31,23,59,59),new Date(2026,0,1,0,1))"),'昨天 23:59:59');
 assert.equal(h.run("dateText(new Date(2026,1,1,9,2,3),new Date(2026,9,12))"),'2月1日 09:02:03');
 assert.equal(h.run("dateText(new Date(2025,1,1,9,2,3),new Date(2026,9,12))"),'2025年2月1日 09:02:03');
 assert.equal(h.run("dateText('invalid')"),'时间未知');
});
test('list renders inert source excerpts, repeat metadata and refreshes cached time labels',async()=>{
 const h=harness();h.context.fetch=async()=>ok({items:[{...row(1),source_summary:'<img src=x>',source_summary_full:'D:/folder/<img src=x>',repeat_count:2}],snapshot_seq:8,next_before:null,total_count:1});
 await h.run('load()');const card=h.get('records').children[0];
 assert.ok(descendants(card).some(n=>n.className==='card-summary'&&n.textContent==='<img src=x>'&&n.title==='D:/folder/<img src=x>'));
 assert.ok(descendants(card).some(n=>n.className==='card-repeat'&&n.textContent==='再次调用 2 次'));
 assert.ok(!descendants(card).some(n=>n.tagName==='IMG'));
 const time=descendants(card).find(n=>n.className==='card-time');assert.equal(time.attrs.datetime,row(1).captured);assert.ok(time.title.includes('2026'));
 time.textContent='stale';await h.windowEvent('focus',{});assert.notEqual(time.textContent,'stale');
});
test('range picker waits for both dates, sorts reversed picks and auto-applies once',async()=>{
 const h=harness(),requests=[];
 h.context.fetch=async url=>{requests.push(url);return ok({items:[row(1)],snapshot_seq:8,next_before:null,total_count:1});};
 h.get('time').value='custom';h.get('start').value='2026-01-01';h.get('end').value='2026-01-31';
 h.run("openCalendar();chooseDate('2026-02-20')");
 assert.equal(h.get('calendar').hidden,false);assert.equal(h.get('start').value,'2026-01-01');assert.equal(requests.length,0);
 h.run("chooseDate('2026-02-10')");await h.flush();
 assert.equal(h.get('calendar').hidden,true);assert.equal(h.get('start').value,'2026-02-10');assert.equal(h.get('end').value,'2026-02-20');
 assert.equal(requests.length,1);assert.match(requests[0],/start=/);assert.match(requests[0],/end=/);assert.equal(h.get('clear').hidden,false);
 assert.equal(h.context.document.activeElement,h.get('range-open'));
});
test('cancelled range and repeated month navigation leave applied dates untouched',async()=>{
 const h=harness();h.get('time').value='custom';h.get('start').value='2026-12-01';h.get('end').value='2026-12-31';
 h.run("openCalendar();chooseDate('2026-12-12')");
 await h.get('month-next').click();await h.get('month-next').click();
 assert.equal(h.get('calendar-month').textContent,'2月');assert.equal(h.get('calendar-year').textContent,'2027年');
 const detached=h.context.document.createElement('span');
 await h.windowEvent('click',{target:detached,composedPath:()=>[detached,h.get('calendar')]});assert.equal(h.get('calendar').hidden,false);
 await h.get('calendar').dispatch('keydown',{key:'Escape'});
 assert.equal(h.get('start').value,'2026-12-01');assert.equal(h.get('end').value,'2026-12-31');assert.equal(h.get('calendar').hidden,true);
});
test('filtered count uses unfiltered session snapshot totals and clear is available with records',async()=>{
 const h=harness();h.context.fetch=async url=>url.startsWith('/api/anomaly-types')?ok({types:[{name:'TypeError',count:3},{name:'OtherError',count:9}]}):ok({items:[row(1)],snapshot_seq:8,next_before:null,total_count:1});
 await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
 h.get('anomaly-type').value='TypeError';await h.run('load()');await h.flush();
 assert.match(h.get('record-count').textContent,/1 \/ 12 条/);assert.equal(h.get('clear').hidden,false);
 await h.get('clear').click();await h.flush();assert.equal(h.get('time').value,'all');assert.equal(h.get('anomaly-type').value,'');assert.equal(h.get('clear').hidden,true);
 h.run('clearSession()');assert.equal(h.get('record-count').textContent,'');
});
test('unavailable unfiltered totals remain unknown, never use filtered total as denominator',async()=>{
 const h=harness();h.context.fetch=async url=>{if(url.startsWith('/api/anomaly-types'))throw Error('unavailable');return ok({items:[row(1)],snapshot_seq:8,next_before:null,total_count:1});};
 await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
 assert.match(h.get('record-count').textContent,/1 \/ — 条/);
});
test('privacy drawer supports tabs, Escape, background inert and return focus without writes',async()=>{
 const h=harness();await h.get('help-open').click();assert.equal(h.get('help').open,true);assert.equal(h.get('workspace').inert,true);
 await h.get('help-tab-1').click();assert.equal(h.get('help-panel-1').hidden,false);assert.equal(h.get('help-panel-0').hidden,true);
 await h.get('help-tab-1').dispatch('keydown',{key:'ArrowRight'});assert.equal(h.get('help-panel-2').hidden,false);
 await h.get('help').dispatch('keydown',{key:'Escape'});assert.equal(h.get('help').open,false);assert.equal(h.get('workspace').inert,false);assert.equal(h.context.document.activeElement,h.get('help-open'));
});
test('detail breadcrumb follows displayed record and repeated calls retain every original body',async()=>{
 const h=harness({wide:true}),data=detail(1);data.events.push({...data.events[0],seq:2,content:JSON.stringify({tool_input:{command:'second raw'},tool_response:'second return'})});
 h.context.fetch=async()=>ok(data);h.run("projects.set('D:/project',{name:'Project A'});sessionNames.set('i-1','Chat A')");
 await h.run('openItem(1,8,false)');
 h.run("selectedSession={id:'B'};sessionNames.set('B','Chat B');updateHeadings()");
 assert.equal(h.get('detail-context').textContent,'Project A / Chat A');assert.equal(h.get('session-description').textContent,'Chat B');
 const repeat=h.get('reading').children.find(n=>n.className==='repeated-calls');assert.ok(repeat);assert.equal(repeat.open,false);assert.match(repeat.children[0].textContent,/1 次/);
 const texts=descendants(repeat).map(n=>n.textContent);assert.ok(texts.some(t=>t.includes('second raw')));assert.ok(texts.includes('second return'));
 data.events.pop();await h.run('openItem(1,8,false)');assert.ok(!h.get('reading').children.some(n=>n.className==='repeated-calls'));
});
test('all three menus close outside, stay open inside and are mutually exclusive',async()=>{
 const h=harness();
 for(const id of ['time-picker','type-picker','scope-picker']) {
  const others=['time-picker','type-picker','scope-picker'].filter(other=>other!==id);
  for(const other of others)h.get(other).open=true;
  h.get(id).open=true;await h.get(id).dispatch('toggle');
  for(const other of others)assert.equal(h.get(other).open,false);
  assert.equal(h.get(id).open,true);
  const child=h.context.document.createElement('input');h.get(id).append(child);
  await h.windowEvent('click',{target:child});assert.equal(h.get(id).open,true);
  await h.windowEvent('click',{target:h.get('list-heading')});assert.equal(h.get(id).open,false);
  h.get(id).open=true;await h.get(id).dispatch('keydown',{key:'Escape'});assert.equal(h.get(id).open,false);
 }
});
function ok(data) {return {ok:true,json:async()=>data};}
function row(id) {return {id,title:'工具返回 TypeError: '+id,captured:'2026-10-07T00:00:00Z',tool:'Bash',project:'D:/project',investigation:'i-1',session:'raw-1'};}
function detail(id) {return {id,title:'failure '+id,project:'D:/project',investigation:'i-1',session:'raw-1',tool:'Bash',captured:'2026-10-07T00:00:00Z',source_seq:8,rule_version:'v1',
  events:[{seq:1,investigation:'session',captured:'2026-10-07T00:00:00Z',digest:'abc',content:JSON.stringify({tool_input:{command:'<img src=x>'},tool_response:'<script>bad()</script>'})}]};}
test('cards omit tool tags and type options stay scoped, searchable and inert',async()=>{
 const h=harness();h.context.fetch=async url=>url.startsWith('/api/anomaly-types')?ok({types:[{name:'RuntimeError',count:6},{name:'ValueError',count:1}]}):ok({items:[row(1)],snapshot_seq:8,next_before:null});
 await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
 assert.ok(!descendants(h.get('records')).some(n=>n.textContent==='Bash'));
 assert.equal(h.get('type-options').children.length,3);
 await h.get('type-options').children[1].click();await h.flush();
 assert.equal(h.get('anomaly-type').value,'RuntimeError');assert.equal(h.get('type-options').children.length,3);
 assert.match(h.run('filters()'),/anomaly_type=RuntimeError/);
 h.get('type-search').value='value';await h.get('type-search').dispatch('input');
 assert.equal(h.get('type-options').children.length,2);
});
test('late type response cannot put another session types into the selected chat',async()=>{
 const h=harness();let finish;
 h.context.fetch=async url=>url.startsWith('/api/anomaly-types')?(url.includes('investigation=A')?new Promise(r=>finish=r):ok({types:[{name:'BError',count:1}]})):ok({items:[],snapshot_seq:8,next_before:null});
 const first=h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();await h.run("selectSession('P',{id:'B'},{remember:false})");await h.flush();
 finish(ok({types:[{name:'AError',count:1}]}));await first;await h.flush();
 const option=h.get('type-options').children[1];
 assert.equal(option.children[0].textContent,'BError');assert.equal(option.children[1].textContent,'1');
 assert.equal(option.children[1].className,'type-count');assert.equal(option.attrs['aria-label'],'BError，1 条记录');
});
test('archive view separates sessions without removing their stored metadata',async()=>{
 const h=harness();h.context.fetch=async url=>ok(url.startsWith('/api/anomaly-contexts')?{projects:[{path:'P'}],sessions:[{id:'a'},{id:'b',archived:true}]}:url.startsWith('/api/session-names')?{names:{a:'A',b:'B'}}:{items:[],snapshot_seq:1,next_before:null});
 await h.run('contexts()');await h.flush();assert.equal(h.run('selectedSession.id'),'a');
 h.get('scope-picker').open=true;await h.get('scope-archived').click();await h.flush();
 assert.equal(h.get('scope-picker').open,false);assert.equal(h.get('scope-label').textContent,'已归档');
 assert.equal(h.get('scope-archived').attrs['aria-pressed'],'true');assert.equal(h.get('scope-active').attrs['aria-pressed'],'false');
 assert.equal(h.run('selectedSession.id'),'b');assert.equal(h.get('archive-session').textContent,'恢复会话');
 assert.equal(h.run("sessionGroups.get('P').sessions.length"),2);assert.equal(h.run("sessionGroups.get('P').body.children.length"),1);
 await h.get('scope-active').click();await h.flush();assert.equal(h.run('selectedSession.id'),'a');assert.equal(h.get('scope-label').textContent,'当前会话');
});
function cleanupPreview(scope) {return {scope:{...scope,through:8},token:'a'.repeat(64),can_apply:true,projects:['D:/project'],sessions:1,events:12,anomalies:3,other_events:9,exclusive_related:0,archived_sessions:0,versions:2,retained_versions:0,content_bytes:1024};}
test('cleanup requires preview then explicit confirmation; cancel never applies',async()=>{
 const h=harness(),calls=[];
 h.context.fetch=async(url,opts)=>{const body=JSON.parse(opts.body);calls.push(body);return ok(cleanupPreview(body.value));};
 await h.get('delete-session').click();assert.deepEqual(calls.map(c=>c.operation),['preview']);assert.equal(h.get('cleanup-dialog').hidden,false);
 await h.get('cleanup-cancel').click();await h.get('cleanup-apply').click();assert.equal(calls.length,1);
 await h.get('delete-session').click();h.run('refreshAfterCleanup=async()=>{}');
 h.context.fetch=async(url,opts)=>{calls.push(JSON.parse(opts.body));return ok({token:'a'.repeat(64),reclaimed:true,events:12,freed_capacity_bytes:1024,database_bytes:4096});};
 await h.get('cleanup-apply').click();assert.equal(calls.at(-1).operation,'apply');assert.equal(calls.at(-1).value.scope.investigation,'i-1');assert.equal(calls.at(-1).value.token,'a'.repeat(64));assert.match(h.get('cleanup-result').textContent,/空间回收完成/);
});
test('closing preview invalidates its delayed response',async()=>{
 const h=harness();let finish;h.context.fetch=async()=>new Promise(r=>finish=r);
 const preparing=h.get('delete-session').click();await h.get('cleanup-cancel').click();finish(ok(cleanupPreview(h.run("cleanupScope('session',{investigation:'i-1'})"))));await preparing;
 assert.equal(h.run('cleanupPlan'),null);assert.equal(h.get('cleanup-dialog').hidden,true);
});
test('failed confirmation cannot resubmit destructive action without new preview',async()=>{
 const h=harness();h.context.fetch=async(url,opts)=>ok(cleanupPreview(JSON.parse(opts.body).value));await h.get('delete-session').click();let attempts=0;
 h.context.fetch=async()=>{attempts++;return {ok:false,status:400};};await h.get('cleanup-apply').click();await h.get('cleanup-apply').click();
 assert.equal(attempts,1);assert.equal(h.get('cleanup-retry').hidden,false);assert.match(h.get('cleanup-result').textContent,/核对已提交结果/);
});
test('all project cleanup requires typed confirmation; dates invalidate preview',async()=>{
 const h=harness();h.context.fetch=async(url,opts)=>ok(cleanupPreview(JSON.parse(opts.body).value));
 await h.run("prepareCleanup(cleanupScope('delete_project',{projects:['D:/project']}))");assert.equal(h.get('cleanup-apply').disabled,true);
 h.get('cleanup-confirm-word').value='删除';await h.get('cleanup-confirm-word').dispatch('input');assert.equal(h.get('cleanup-apply').disabled,false);
 await h.get('cleanup-end').dispatch('change');assert.equal(h.get('cleanup-apply').disabled,true);
});
test('filtered cleanup uses committed filters and includes all old pages',async()=>{
 const h=harness();let body;h.run("activeFilters='anomaly_type=RuntimeError';snapshotSeq=100;matchingTotal=130;selectingAnomalies=true;syncSelection()");
 h.context.fetch=async(url,opts)=>{body=JSON.parse(opts.body);return ok(cleanupPreview(body.value));};await h.get('delete-filtered').click();assert.equal(body,undefined);await h.get('delete-selected').click();
 assert.equal(body.value.through,100);assert.equal(body.value.anomaly_type,'RuntimeError');assert.deepEqual(body.value.ids,[]);
});

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
test('export preview pins cutoff and discards stale response after selection changes',async()=>{
  const h=harness();let resolve,url;
  h.context.fetch=p=>{url=p;return new Promise(r=>resolve=r)};
  h.run("current={id:'1',source_seq:8,rule_version:'v1'}");
  const promise=h.run('previewExport()');h.run("current={id:'2',source_seq:9,rule_version:'v1'}");
  resolve({ok:true,text:async()=> 'stale export'});await promise;
  assert.match(url,/anomaly-export\/1\?through=8&rule=v1/);
  assert.deepEqual(h.downloads,[]);assert.equal(h.run('exportPreview'),null);
  assert.equal(h.get('export-download').disabled,true);
});
test('host download awaits confirmation, sends the cached preview once and shows feedback beside it',async()=>{
  const h=harness(),body='完整预览\n<script>inert</script>';let calls=0,resolve,sent;
  h.context.fetch=async()=>{calls++;return {ok:true,text:async()=>body};};
  h.context.window.blackboxDownload=(name,text)=>{sent={name,text};return new Promise(r=>resolve=r);};
  h.run("current={id:'1',source_seq:8,rule_version:'v1'}");await h.run('previewExport()');
  const downloading=h.run('download()');await h.run('download()');
  assert.deepEqual(sent,{name:'anomaly-1-through-8.md',text:body});assert.equal(calls,1);
  assert.equal(h.downloads.length,0);assert.equal(h.blobs.length,0);assert.equal(h.get('export-download').disabled,true);
  assert.match(h.get('export-preview-status').textContent,/正在请求/);resolve({});await downloading;
  assert.match(h.get('export-preview-status').textContent,/宿主已接受/);assert.equal(h.get('export-download').disabled,false);
});
test('host download errors remain visible with preview intact and retry available',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:true,text:async()=> 'saved preview'});
  h.context.window.blackboxDownload=async()=>{throw new Error('宿主未接受下载');};
  h.run("current={id:'1',source_seq:8,rule_version:'v1'}");await h.run('previewExport()');await h.run('download()');
  assert.equal(h.get('export-preview-status').textContent,'宿主未接受下载');assert.equal(h.get('export-preview-text').textContent,'saved preview');
  assert.equal(h.get('export-download').disabled,false);assert.equal(h.downloads.length,0);
});
test('late host download replies cannot overwrite another preview or its controls',async()=>{
  for(const fails of [false,true]) {
    const h=harness();let finish;
    h.context.fetch=async()=>({ok:true,text:async()=> 'saved preview'});
    h.context.window.blackboxDownload=()=>new Promise((resolve,reject)=>finish=()=>fails?reject(new Error('old error')):resolve({}));
    h.run("current={id:'1',source_seq:8,rule_version:'v1'}");await h.run('previewExport()');const downloading=h.run('download()');
    h.run("current={id:'2',source_seq:9,rule_version:'v1'}");await h.run('previewExport()');const status=h.get('export-preview-status').textContent;
    finish();await downloading;assert.equal(h.get('export-preview-status').textContent,status);assert.equal(h.get('export-download').disabled,false);
  }
});
test('local save is explicit, pinned and cannot overwrite a newer preview',async()=>{
  const h=harness();let finish,chosen,check;
  h.context.fetch=async()=>({ok:true,text:async()=> 'full preview',headers:{get:()=> 'a'.repeat(64)}});
  h.context.window.blackboxSaveExport=(snapshot,visible)=>{chosen=snapshot;check=visible;return new Promise(r=>finish=r);};
  h.run("current={id:'1',source_seq:8,rule_version:'v1'}");await h.run('previewExport()');
  assert.equal(h.get('export-save').hidden,false);assert.equal(h.get('export-save').disabled,false);assert.equal(h.get('export-download').hidden,true);
  const opening=h.run('saveExport()');assert.equal(chosen.id,'1');assert.equal(chosen.through,8);assert.equal(chosen.preview_sha256,'a'.repeat(64));assert.equal(check(),true);
  h.run("current={id:'2',source_seq:9,rule_version:'v1'}");await h.run('previewExport()');assert.equal(check(),false);
  const status=h.get('export-preview-status').textContent;finish();await opening;
  assert.equal(h.get('export-preview-status').textContent,status);assert.equal(h.get('export-save').disabled,false);
});

test('local save shows the verified path only on success and blocks duplicate clicks',async()=>{
  const h=harness();let finish,calls=0;
  h.context.fetch=async()=>({ok:true,text:async()=> 'full preview'});
  h.context.window.blackboxSaveExport=()=>{calls++;return new Promise(r=>finish=r)};
  h.run("current={id:'1',source_seq:8,rule_version:'v1'}");await h.run('previewExport()');
  assert.equal(calls,0);
  const saving=h.run('saveExport()');await h.run('saveExport()');assert.equal(calls,1);assert.equal(h.get('export-save').disabled,true);
  finish({path:'C:\\Downloads\\anomaly-1-through-8.md',bytes:12});await saving;
  assert.match(h.get('export-preview-status').textContent,/已保存并校验.*12 字节.*C:\\Downloads\\anomaly-1-through-8.md/);
  assert.equal(h.get('export-save').disabled,false);assert.equal(h.blobs.length,0);
  h.context.window.blackboxSaveExport=async()=>{throw new Error('磁盘写入失败')};await h.run('saveExport()');
  assert.equal(h.get('export-preview-status').textContent,'磁盘写入失败');assert.equal(h.get('export-preview-text').textContent,'full preview');assert.equal(h.get('export-save').disabled,false);
});
test('browser export link shows a fresh full preview without automatically downloading',async()=>{
  const h=harness({hash:'#token=test&record=1&through=8&preview=1'});
  h.context.fetch=async url=>{
   if(url.startsWith('/api/anomaly/'))return ok(detail('1'));
   if(url.startsWith('/api/anomaly-export/'))return {ok:true,text:async()=> 'browser full preview'};
   if(url.startsWith('/api/anomaly-contexts'))return ok(url.includes('project_path=')?{sessions:[{id:'i-1',project:'D:/project'}]}:{projects:[{path:'D:/project'}]});
   if(url.includes('session-names'))return ok({names:{'i-1':'Saved chat'}});
   return ok(url.includes('anomaly-types')?{types:[],workspace_has_anomalies:true}:{items:[],snapshot_seq:8,next_before:null,total_count:0});
  };
  await h.run('start()');assert.equal(h.get('export-preview').hidden,false);assert.equal(h.get('export-preview-text').textContent,'browser full preview');assert.equal(h.downloads.length,0);
});
test('download contains exactly the complete inert preview and does not refetch',async()=>{
  const h=harness(),body='# Synthetic error\n\n<script>not executable</script>\nsource fields: PRIVATE-SYNTHETIC';let calls=0;
  h.run("current={id:'1',source_seq:8,rule_version:'v1'}");
  h.context.fetch=async()=>{calls++;return {ok:true,text:async()=>body}};
  await h.run('download()');assert.equal(calls,0);assert.equal(h.downloads.length,0);
  await h.run('previewExport()');
  assert.equal(h.get('export-preview-text').textContent,body);
  assert.equal(h.get('export-preview-text').children.length,0);
  assert.equal(h.downloads.length,0);
  await h.run('download()');assert.equal(calls,1);
  assert.equal(await h.blobs[0].text(),body);assert.deepEqual(h.downloads,['anomaly-1-through-8.md']);
  h.run('clearExportPreview()');await h.run('download()');assert.equal(h.downloads.length,1);
});
test('closing a pending export preview prevents its late result from reopening',async()=>{
  const h=harness();let resolve;h.run("current={id:'1',source_seq:8,rule_version:'v1'}");
  h.context.fetch=()=>new Promise(done=>resolve=done);
  const pending=h.run('previewExport()');h.run('clearExportPreview()');
  resolve({ok:true,text:async()=> 'closed'});await pending;
  assert.equal(h.get('export-preview').hidden,true);assert.equal(h.get('export-preview-text').textContent,'');
  assert.equal(h.get('export-download').disabled,true);assert.equal(h.get('export').disabled,false);
});
test('source content is inert and no review gate exists',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:true,json:async()=>detail('1')});
  await h.run("openItem('1')");
  assert.ok(descendants(h.get('reading')).some(n=>n.textContent==='<script>bad()</script>'));
  const tags=[];function walk(n){tags.push(n.tagName);n.children.forEach(walk)}walk(h.get('reading'));
  assert.ok(!tags.includes('SCRIPT'));assert.ok(!tags.includes('IMG'));
  assert.equal(h.get('export').disabled,false);
  assert.equal(h.get('detail-status').textContent,'');
});
test('empty scan page with a cursor remains traversable and pins snapshot across new arrivals',async()=>{
  const h=harness(), urls=[];
  const pages=[{items:[],snapshot_seq:501,next_before:2,scanned_count:500,total_count:1},
    {items:[{id:1,title:'old error',captured:'2026-10-07',tool:'Bash',project:'p'}],snapshot_seq:501,next_before:null,scanned_count:1,has_newer:true}];
  h.context.fetch=async url=>{urls.push(url);return {ok:true,json:async()=>pages.shift()}};
  await h.run('load()');
  assert.equal(h.get('more').hidden,false);assert.match(h.get('list-status').textContent,/1 条异常记录.*尚未扫描完/);
  assert.equal(h.get('list-empty').hidden,true);
  h.get('project').value='unsaved filter';
  await h.run('load(true)');
  const next=new URL(urls[1],'http://localhost');
  assert.equal(next.searchParams.get('through'),'501'); assert.equal(next.searchParams.get('before'),'2');
  assert.equal(next.searchParams.get('project_path'),'D:/project');
  assert.equal(h.get('more').hidden,true);assert.match(h.get('list-status').textContent,/有新事件/);
  assert.equal(h.get('records').children.length,1);
});
test('total count is independent of the 100-card page and survives load more',async()=>{
  const h=harness();
  const rows=Array.from({length:100},(_,i)=>({id:i+1,title:'error',captured:'2026-10-07',tool:'Bash',project:'p'}));
  const pages=[{items:rows,snapshot_seq:236,next_before:136,total_count:236},
    {items:rows,snapshot_seq:236,next_before:36,total_count:236}];
  h.context.fetch=async()=>({ok:true,json:async()=>pages.shift()});
  await h.run('load()');
  assert.equal(h.get('records').children.length,100);
  assert.equal(h.get('list-status').textContent,'236 条异常记录');
  assert.equal(h.get('more').hidden,false);
  await h.run('load(true)');
  assert.equal(h.get('records').children.length,200);
  assert.equal(h.get('list-status').textContent,'236 条异常记录');
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

test('MCP error details preserve every argument and inert result through copy and export',async()=>{
  const h=harness(),data=detail('mcp-1');
  data.tool='mcp__synthetic__read';data.title=data.tool+' 返回错误';
  const input={command:'read',path:'synthetic-missing',options:{mode:'strict'}};
  const output={isError:true,content:[{type:'text',text:'<script>synthetic failure</script>'}]};
  data.events[0].content=JSON.stringify({tool_name:data.tool,tool_input:input,tool_response:output});
  h.context.fetch=async()=>({ok:true,json:async()=>data});
  await h.run("openItem('mcp-1')");
  const nodes=descendants(h.get('reading'));
  assert.ok(nodes.some(n=>n.textContent==='工具输入'));
  const code=nodes.filter(n=>n.tagName==='PRE');
  assert.equal(code[0].textContent,JSON.stringify(input,null,2));
  assert.equal(code[1].textContent,JSON.stringify(output,null,2));
  assert.ok(!nodes.some(n=>n.tagName==='SCRIPT'));
  let copied,url;
  h.context.navigator.clipboard.writeText=async text=>{copied=text};
  await nodes.filter(n=>n.className==='code-copy')[0].click();
  assert.equal(copied,JSON.stringify(input,null,2));
  h.context.fetch=async path=>{url=path;return {ok:true,text:async()=> JSON.stringify({input,output})}};
  await h.run('previewExport()');await h.run('download()');
  assert.equal(url,'/api/anomaly-export/mcp-1?through=8&rule=v1');
  assert.deepEqual(h.downloads,['anomaly-mcp-1-through-8.md']);
});
test('empty exhausted lists use a single neutral empty state',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:true,json:async()=>({items:[],snapshot_seq:0,next_before:null})});
  await h.run('load()');assert.equal(h.get('list-status').textContent,'');
  assert.equal(h.get('more').hidden,true);
});
test('retained long-output segments and structured returns remain visible and inert',async()=>{
  const h=harness(), data=detail('1');
  const output={preview:'<script>head</script>',tail:'TypeError: tail',truncated:true,original_length:90000};
  data.events[0].content=JSON.stringify({tool_input:{cmd:'run'},tool_response:output});
  h.context.fetch=async()=>({ok:true,json:async()=>data});await h.run("openItem('1')");
  const text=descendants(h.get('reading')).map(n=>n.textContent).join('\n');
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
  assert.equal(h.get('application-status').textContent,'插件版本：v2');
  assert.doesNotMatch(html,/id="runtime-status"|id="storage-status"|id="capture-error"/);
  assert.equal(h.get('storage-value').textContent,'1.0 MiB / 1.0 MiB');
  assert.match(html,/已有记录仍可查看和导出/);
  h.context.fetch=async()=>({ok:true,json:async()=>({projects:[],record_count:0})});
  await h.run('capture()');assert.equal(h.get('application-status').textContent,'插件版本：未知');
});
test('shutdown is explicit and authenticated without changing capture authorization',async()=>{
  const h=harness();let requested;
  h.context.fetch=async(path,options)=>{requested={path,options};return {ok:true}};
  await h.run('shutdown()');
  assert.equal(requested.path,'/api/shutdown');assert.equal(requested.options.method,'POST');
  assert.equal(requested.options.headers.Authorization,'Bearer test');
  assert.match(h.get('status').textContent,/采集开关没有改变/);
});
test('header status is removed while capture diagnostics remain available',async()=>{
 const h=harness();assert.doesNotMatch(html,/id="capture-help"|id="capture-overview"/);
 h.context.fetch=async()=>ok({projects:[{path:'P',enabled:true}],capture_health:{state:'events_received'}});
 await h.run('capture()');assert.equal(h.get('capture-status').textContent,'1 个已启用 · 0 个已停用');
 h.context.fetch=async()=>{throw new Error('offline')};await assert.rejects(h.run('capture()'));
 assert.match(h.get('capture-status').textContent,/无法读取/);
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
  h.get('project').value='D:/project';h.get('anomaly-type').value='Bash';
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
  assert.equal(h.get('project').value,'draft filter');assert.equal(h.get('anomaly-type').value,'Bash');
  assert.equal(h.get('session').value,'session-A');assert.equal(h.get('time').value,'custom');
  assert.equal(h.get('start').value,'2026-10-01');assert.equal(h.get('end').value,'2026-10-07');
  assert.equal(h.run('activeFilters'),applied);assert.equal(h.run('snapshotSeq'),501);
  assert.equal(h.run('nextBefore'),100);
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
    assert.equal(h.run('activeFilters'),applied);
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
  assert.equal(h.get('session').value,'i-1');assert.match(h.get('detail-context').textContent,/A real title/);
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
  assert.match(html,/<details id="help"[\s\S]*?不仅限于报错的调用/);
  assert.match(html,/<section id="list-view"/);assert.match(html,/<section id="detail-view"[^>]*hidden/);
  assert.match(html,/id="record-heading" tabindex="-1"/);assert.match(html,/id="list-heading" tabindex="-1"/);
  const historyHtml=fs.readFileSync(path.join(__dirname,'../plugins/blackbox/assets/review.html'),'utf8');
  assert.doesNotMatch(historyHtml,/<body[^>]*class="[^"]*anomaly-app/);
  const css=fs.readFileSync(path.join(__dirname,'../plugins/blackbox/assets/review.css'),'utf8');
  const additions=css.slice(css.indexOf('/* NOTES-030:'));
  assert.ok(additions.length>0);assert.match(additions,/\.anomaly-app main\{/);
  for(const match of additions.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{/g)) {
    const selector=match[1].trim();if(selector.startsWith('@')||selector==='from'||selector==='to')continue;
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
  assert.equal(h.get('session-description').textContent,'Chat B');
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
    const text=descendants(h.get('reading')).map(n=>n.textContent).join('\n');
    assert.equal(text.includes('部分文字可能显示不完整'),false);assert.doesNotMatch(text,/没有找到可关联|原始文字可能已丢失|仅连接同一会话/);
    assert.doesNotMatch(h.get('meta').textContent,/saved-session/);assert.doesNotMatch(html,/id="source-details"|id="detail-snapshot"/);
  }
  assert.doesNotMatch(html,/<summary>技术详情<\/summary>/);
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
  assert.equal(h.get('session-description').textContent,'<img src=x>');
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
  pending[2].resolve(ok({items:[row(3)],snapshot_seq:12,next_before:null}));await h.flush();
  pending[3].resolve(ok({types:[],workspace_has_anomalies:false}));await third;
  assert.equal(h.get('session').value,'A');assert.equal(h.get('records').children[0].dataset.recordId,'3');assert.equal(h.run('snapshotSeq'),12);
});

test('switching saved sessions restores their cards, filters, cursor and scroll without another list fetch',async()=>{
  const h=harness(),urls=[];
  h.context.fetch=async url=>{urls.push(url);return ok({items:[row(url.includes('investigation=A')?1:2)],snapshot_seq:10,next_before:5,scanned_count:5})};
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  const card=h.get('records').children[0];h.get('anomaly-type').value='draft tool';h.context.window.scrollY=410;
  await h.run("selectSession('P',{id:'B'},{remember:false})");await h.flush();
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  assert.equal(urls.filter(url=>url.startsWith('/api/anomalies?')).length,2);assert.equal(h.get('records').children[0],card);assert.equal(h.run('nextBefore'),5);
  assert.equal(h.get('anomaly-type').value,'draft tool');assert.equal(h.context.window.scrollY,410);
});

test('delayed titles update the selected chat name without blocking records or replacing selection',async()=>{
  const h=harness();let resolveNames;
  h.context.fetch=async url=>{
    if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'P'}],sessions:[{id:'A',session:'raw-A'},{id:'B',session:'raw-B'}]});
    if(url.startsWith('/api/session-names'))return new Promise(resolve=>resolveNames=resolve);
    return ok({items:[row(1)],snapshot_seq:10,next_before:null});
  };
  await h.run('contexts()');assert.equal(h.get('records').children.length,1);assert.equal(h.get('session-description').textContent,'正在读取会话信息… · A');
  assert.equal(h.run("sessionGroups.get('P').buttons.get('A').button.hidden"),false);
  await h.run("selectSession('P',{id:'B'},{remember:false})");
  resolveNames(ok({names:{A:'Name A',B:'Name B'}}));await h.flush();
  assert.equal(h.get('session').value,'B');assert.equal(h.get('session-description').textContent,'Name B');
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
  h.get('time').value='today';h.get('anomaly-type').value='Bash';await h.run('load()');
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
  assert.equal(h.run("sessionViews.get('A').controls['anomaly-type']"),'Bash');
  h.context.history.forward();await h.flush();
  assert.equal(h.run('current.investigation'),'A');assert.equal(h.run('current.source_seq'),10);
  assert.equal(h.run('selectedSession.id'),'A');
  h.context.history.back();await h.flush();
  assert.equal(h.get('time').value,'today');assert.equal(h.get('anomaly-type').value,'Bash');
  assert.equal(h.get('records').children[0],card);assert.equal(h.run('snapshotSeq'),10);
  assert.equal(h.context.window.scrollY,655);
});

test('session switching before Back scroll restoration paints saves the intended reading position',async()=>{
  const h=harness();
  h.context.fetch=async url=>ok(url.startsWith('/api/anomaly/')?{...detail(1),project:'P',investigation:'A',source_seq:10}
    :{items:[row(url.includes('investigation=B')?2:1)],snapshot_seq:10,next_before:null});
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  h.get('time').value='today';h.get('anomaly-type').value='Bash';await h.run('load()');
  h.context.window.scrollY=655;await h.run('openItem(1,10)');
  h.context.history.back();await h.flush({paint:false});
  assert.equal(h.run('view'),'list');assert.equal(h.context.window.scrollY,0,'the browser has not painted the restored scroll yet');
  await h.run("selectSession('P',{id:'B'},{remember:false})");
  assert.equal(h.run("sessionViews.get('A').scroll"),655);
  await h.flush();
  await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();
  assert.equal(h.context.window.scrollY,655);
  assert.equal(h.get('time').value,'today');assert.equal(h.get('anomaly-type').value,'Bash');
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
  h.get('time').value='today';h.get('anomaly-type').value='Bash';await h.run('load()');
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
  assert.equal(h.get('time').value,'today');assert.equal(h.get('anomaly-type').value,'Bash');
  cards.forEach((card,i)=>assert.equal(h.get('records').children[i],card));
  assert.equal(h.run('snapshotSeq'),10);assert.equal(h.context.window.scrollY,655);
  h.context.history.forward();await h.flush();
  assert.equal(h.run('current.id'),'2');assert.equal(urls.at(-1),'/api/anomaly/2?through=10');
});


test('wide workbench keeps timeline visible and preserves independent scroll during detail changes',async()=>{
  const h=harness({wide:true});
  h.context.fetch=async p=>ok(p.startsWith('/api/anomalies')?{items:[row('1'),row('2')],snapshot_seq:8,next_before:null}:detail(p.includes('/2')?'2':'1'));
  await h.run('load()');h.get('list-view').scrollTop=260;
  await h.run("openItem('1')");
  assert.equal(h.get('list-view').hidden,false);assert.equal(h.get('detail-view').hidden,false);
  assert.equal(h.get('detail-empty').hidden,true);assert.equal(h.get('list-view').scrollTop,260);
  h.get('list-view').scrollTop=340;await h.run("openItem('2')");
  assert.equal(h.get('list-view').scrollTop,340);assert.equal(h.get('detail-view').scrollTop,0);
  assert.equal(h.get('records').children[1].attrs['aria-current'],'true');
});
test('wide workbench clearing selection hides old detail and disables export without hiding panes',async()=>{
  const h=harness({wide:true});h.context.fetch=async()=>ok(detail('1'));
  await h.run("openItem('1')");h.run('showList()');await h.flush();
  assert.equal(h.get('list-view').hidden,false);assert.equal(h.get('detail-view').hidden,false);
  assert.equal(h.get('detail-empty').hidden,false);assert.equal(h.get('detail').hidden,true);
  assert.equal(h.get('export').disabled,true);assert.equal(h.run('current'),null);
});

test('code copy preserves exact text without line numbers or blank-line placeholders',async()=>{
 const h=harness();let copied;
 h.context.navigator.clipboard.writeText=async value=>{copied=value};
 h.run("codeBlock(el('reading'),'first\\n\\n<img>\\n')");
 const box=h.get('reading').children[0];await box.children[0].click();
 assert.equal(copied,'first\n\n<img>\n');assert.equal(box.children[0].attrs['aria-label'],'已复制');
 assert.equal(box.children.length,3);
 assert.equal(box.children[1].textContent,copied);
});
test('clipboard denial reports failure instead of success',async()=>{
 const h=harness();h.context.navigator.clipboard.writeText=async()=>{throw new Error('denied')};
 h.run("codeBlock(el('reading'),'private')");const button=h.get('reading').children[0].children[0];
 await button.click();assert.equal(button.attrs['aria-label'],'复制失败，请重试');assert.equal(button.disabled,false);
});

test('time menu selects presets, exposes custom range and resets its visible label',async()=>{
 const h=harness();h.context.fetch=async()=>ok({items:[],snapshot_seq:1,next_before:null});
 await h.get('time-week').click();await h.flush();
 assert.equal(h.get('time').value,'week');assert.equal(h.get('time-label').textContent,'近七天');
 assert.equal(h.get('time-week').attrs['aria-pressed'],'true');
 await h.get('time-custom').click();assert.equal(h.get('date-range').hidden,false);
 h.get('time-picker').open=true;await h.get('time-picker').dispatch('keydown',{key:'Escape'});
 assert.equal(h.get('time-picker').open,false);
 await h.get('clear').click();assert.equal(h.get('time-label').textContent,'全部时间');
});

test('host project label covers multiple roots while record filters keep the actual source root',async()=>{
 const h=harness();h.context.fetch=async url=>ok(url==='/api/anomaly-contexts'?{projects:[{path:'P',paths:['P','Q'],name:'Host project'}]}:
 url.startsWith('/api/anomaly-contexts?')?{sessions:[{id:'q',project:'Q'}]}:
 url.startsWith('/api/session-names')?{names:{q:'Host chat'},states:{q:'named'}}:{items:[],snapshot_seq:1,next_before:null});
 await h.run('contexts()');await h.flush();
 assert.equal(h.get('project-tree').children.length,1);
 assert.equal(h.get('session-description').hidden,false);
 assert.equal(h.get('project').value,'Q');assert.equal(h.run('selectedSession.project'),'Q');
});

test('unlinked sessions hide without hiding temporary metadata failures',async()=>{
 const h=harness();h.context.fetch=async url=>ok(url==='/api/anomaly-contexts'?{projects:[{path:'P',name:'Host'}]}:
 url.startsWith('/api/anomaly-contexts?')?{sessions:[{id:'a'},{id:'b'}]}:
 url.startsWith('/api/session-names')?{names:{a:null,b:null},states:{a:'unlinked',b:'unavailable'}}:{items:[],snapshot_seq:1,next_before:null});
 await h.run('contexts()');await h.flush();
 assert.equal(h.run("sessionText({id:'a'})"),'未关联记录 · a');
 assert.equal(h.run("sessionText({id:'b'})"),'会话信息暂不可用 · b');
 assert.equal(h.run("sessionGroups.get('P').buttons.get('a').button.hidden"),true);
 assert.equal(h.run("sessionGroups.get('P').buttons.get('b').button.hidden"),false);
 assert.equal(h.run('selectedSession.id'),'b');
 assert.equal(h.run("sessionGroups.get('P').buttons.get('a').button.parentNode"),null);
 assert.equal(h.run("sessionGroups.get('P').body.children[0]"),h.run("sessionGroups.get('P').buttons.get('b').button"));
});

test('late unlinked metadata clears the automatic reading selection without deleting records',async()=>{
 const h=harness();let resolveNames;
 h.context.fetch=async url=>url.startsWith('/api/session-names')?new Promise(r=>resolveNames=r):ok(url==='/api/anomaly-contexts'?{projects:[{path:'P'}]}:url.startsWith('/api/anomaly-contexts?')?{sessions:[{id:'old'}]}:{items:[],snapshot_seq:1,next_before:null});
 await h.run("contexts({project_path:'P',investigation:'old'})");resolveNames(ok({names:{old:null},states:{old:'unlinked'}}));await h.flush();
 assert.equal(h.run('selectedSession'),null);assert.equal(h.get('filters').hidden,true);
 assert.equal(h.run("sessionGroups.get('P').sessions.length"),1);assert.equal(h.run("sessionGroups.get('P').body.children.length"),0);
});
test('only confirmed unlinked records collapse after normal sessions across metadata batches',async()=>{
 const h=harness(),sessions=Array.from({length:25},(_,i)=>({id:'s'+i}));let calls=0;
 h.context.fetch=async url=>{
  if(url==='/api/anomaly-contexts')return ok({projects:[{path:'P'}]});
  if(url.startsWith('/api/anomaly-contexts?'))return ok({sessions});
  if(url.startsWith('/api/session-names')){
   calls++;const ids=new URL(url,'http://local').searchParams.getAll('investigation');
   return ok({names:Object.fromEntries(ids.map(id=>[id,Number(id.slice(1))<6?'Chat '+id:null])),
    states:Object.fromEntries(ids.map(id=>[id,Number(id.slice(1))<6?'named':'unlinked']))});
  }
  return ok({items:[],snapshot_seq:1,next_before:null});
 };
 await h.run('contexts()');await h.flush();
 const group=h.run("sessionGroups.get('P')");
 assert.equal(calls,2);assert.equal(group.body.children.length,5);assert.equal(group.more.hidden,false);
 assert.equal(h.run('selectedSession.id'),'s0');
 await group.more.click();assert.equal(group.body.children.length,6);assert.equal(group.more.textContent,'收起');
 assert.equal(group.buttons.get('s24').button.parentNode,null);
 await group.more.click();assert.equal(group.body.children.length,5);

});

test('remembered hidden sessions fall back but explicit historical detail links remain readable',async()=>{
 for(const direct of [false,true]){
  const h=harness({startup:true,hash:direct?'#token=test&record=7&through=88':'#token=test',fetch:async url=>{
   if(url.startsWith('/api/anomaly/'))return ok({...detail(7),project:'P',investigation:'old',source_seq:88});
   if(url==='/api/reader-selection')return ok({project_path:'P',investigation:'old'});
   if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'P'}],sessions:[{id:'new'},{id:'old'}]});
   if(url.startsWith('/api/session-names'))return ok({names:{new:'Normal chat',old:null},states:{new:'named',old:'unlinked'}});
   return ok({projects:[],items:[row(9)],snapshot_seq:99,next_before:null});
  }});
  await h.flush();await h.flush();
  assert.equal(h.run("sessionGroups.get('P').buttons.get('old').button.parentNode"),null);
  if(!direct)assert.equal(h.run('selectedSession.id'),'new');
  assert.equal(h.run("sessionGroups.get('P').details.open"),true);assert.equal(h.get('records').children.length,1);
  if(direct){assert.equal(h.run('view'),'detail');assert.equal(h.run('current.id'),7);assert.match(h.context.location.hash,/record=7&through=88/);}
 }
});

test('metadata failure marks every unrequested batch unavailable instead of leaving pending labels',async()=>{
 const h=harness(),sessions=Array.from({length:25},(_,i)=>({id:'s'+i}));let calls=0;
 h.context.fetch=async url=>{
  if(url==='/api/anomaly-contexts')return ok({projects:[{path:'P'}]});
  if(url.startsWith('/api/anomaly-contexts?'))return ok({sessions});
  if(url.startsWith('/api/session-names')){calls++;throw new Error('metadata unavailable');}
  return ok({items:[row(1)],snapshot_seq:1,next_before:null});
 };
 await h.run('contexts()');await h.flush();
 assert.equal(calls,1);assert.equal(h.get('records').children.length,1);
 const group=h.run("sessionGroups.get('P')");
 assert.equal(group.body.children.length,5);await group.more.click();assert.equal(group.body.children.length,25);
 for(const session of sessions){assert.equal(h.run(`sessionStates.get('${session.id}')`),'unavailable');assert.match(group.buttons.get(session.id).label.textContent,/会话信息暂不可用/);}
});

test('late failed metadata from a replaced project group cannot overwrite fresh names or grouping',async()=>{
 const h=harness();let rejectOld,calls=0;
 h.context.fetch=async url=>{
  if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'P'}],sessions:[{id:'s'}]});
  if(url.startsWith('/api/session-names')){
   if(++calls===1)return new Promise((_,reject)=>rejectOld=reject);
   return ok({names:{s:'Fresh title'},states:{s:'named'}});
  }
  return ok({items:[row(1)],snapshot_seq:1,next_before:null});
 };
 await h.run('contexts()');await h.run('contexts()');await h.flush();
 const group=h.run("sessionGroups.get('P')");rejectOld(new Error('old failure'));await h.flush();
 assert.equal(h.run("sessionStates.get('s')"),'named');assert.equal(h.get('session-description').textContent,'Fresh title');
 assert.equal(group.body.children.length,1);assert.equal(group.notice.textContent,'');assert.equal(h.get('records').children.length,1);
});

test('regrouping reuses focused buttons and title recovery moves them back without duplicates',async()=>{
 const h=harness();let resolveNames,recovered=false;
 h.context.fetch=async url=>{
  if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[{path:'P'}],sessions:[{id:'main'},{id:'old'}]});
  if(url.startsWith('/api/session-names'))return recovered?ok({names:{main:'Main',old:'Recovered'},states:{main:'named',old:'named'}}):new Promise(resolve=>resolveNames=resolve);
  return ok({items:[row(1)],snapshot_seq:1,next_before:null});
 };
 await h.run('contexts()');await h.flush();const group=h.run("sessionGroups.get('P')"),button=group.buttons.get('old').button;
 button.focus();resolveNames(ok({names:{main:'Main',old:null},states:{main:'named',old:'unlinked'}}));await h.flush();
 assert.equal(button.parentNode,null);assert.equal(button.hidden,true);
 assert.equal(h.context.document.activeElement,group.body.children[0]);
 recovered=true;await h.run("loadNames(sessionGroups.get('P'),sessionGroups.get('P').generation)");
 assert.equal(button.hidden,false);
 assert.equal(group.body.children[1],button);assert.equal(button.parentNode,group.body);assert.equal(group.buttons.get('old').label.textContent,'Recovered');
 assert.equal(descendants(group.details).filter(n=>n===button).length,1);assert.equal(h.run('selectedSession.id'),'main');
});

test('refresh preserves expanded sessions without changing the selected normal session',async()=>{
 const h=harness();h.context.fetch=async url=>ok(url.startsWith('/api/anomaly-contexts')?{projects:[{path:'P'}],sessions:[{id:'main'},{id:'old'}]}:
  url.startsWith('/api/session-names')?{names:{main:'Main',old:null},states:{main:'named',old:'unlinked'}}:{items:[],snapshot_seq:1,next_before:null});
 await h.run('contexts()');await h.flush();h.run("sessionGroups.get('P').expanded=true");
 await h.run('contexts()');await h.flush();
 assert.equal(h.run('selectedSession.id'),'main');assert.equal(h.run("sessionGroups.get('P').expanded"),true);
});


test('custom calendar supports leap days, month boundaries, clear and Escape',async()=>{
 const h=harness();h.get('start').value='2024-02-29';await h.get('range-open').click();
 assert.equal(h.get('calendar').hidden,false);assert.equal(h.get('calendar-month').textContent,'2月');
 const leap=h.get('calendar-days').children.find(b=>b.dataset.date==='2024-02-29');
 assert.equal(leap.attrs['aria-pressed'],'true');
 await leap.dispatch('keydown',{key:'ArrowRight'});assert.equal(h.get('calendar-month').textContent,'3月');
 await h.get('calendar-days').children.find(b=>b.dataset.date==='2024-03-01').click();
 assert.equal(h.get('calendar').hidden,false);
 await h.get('calendar-days').children.find(b=>b.dataset.date==='2024-03-02').click();
 assert.equal(h.get('start').value,'2024-03-01');assert.equal(h.get('end').value,'2024-03-02');assert.equal(h.get('calendar').hidden,true);
 assert.equal(h.context.document.activeElement,h.get('range-open'));
 h.run("openCalendar()");await h.get('date-clear').click();assert.equal(h.get('start').value,'');
 h.run("openCalendar()");await h.get('calendar').dispatch('keydown',{key:'Escape'});assert.equal(h.get('calendar').hidden,true);
 h.get('start').value='2026-12-31';h.run("openCalendar()");await h.get('month-next').click();assert.equal(h.get('calendar-year').textContent,'2027年');
});

test('reading surface removes technical panels and native date controls',()=>{
 assert.doesNotMatch(html.slice(0,html.indexOf('<section id="cleanup-dialog"')),/id="snapshot-info"|id="source-details"|type="date"/);
 assert.doesNotMatch(source,/className='session-date'|查看详情 →/);
});


test('wide chat switching retains detail, scroll and export until another card is opened',async()=>{
 const h=harness({wide:true});let exportUrl;
 h.context.fetch=async url=>{
  if(url.startsWith('/api/anomaly-export/')){exportUrl=url;return {ok:true,text:async()=> 'synthetic preview'};}
  if(url.startsWith('/api/anomaly/'))return ok({...detail(url.includes('/2')?2:1),project:'P',investigation:url.includes('/2')?'B':'A',source_seq:10});
  return ok({items:[row(url.includes('investigation=B')?2:1)],snapshot_seq:10,next_before:null});
 };
 await h.run("selectSession('P',{id:'A'},{remember:false})");await h.flush();await h.run('openItem(1,10)');
 h.get('detail-view').scrollTop=180;
 await h.run("selectSession('P',{id:'B'},{remember:false})");await h.flush();
 assert.equal(h.run('current.id'),1);assert.equal(h.get('detail').hidden,false);assert.equal(h.get('detail-view').scrollTop,180);
 assert.equal(h.get('records').children[0].dataset.recordId,'2');assert.equal(h.get('export').disabled,false);
 await h.run('previewExport()');await h.run('download()');assert.match(exportUrl,/anomaly-export\/1\?through=10/);
 await h.run('openItem(2,10)');assert.equal(h.run('current.id'),2);
 await h.run("selectSession('P',{id:'A'},{remember:false})");assert.equal(h.get('records').children[0].attrs['aria-current'],'false');
});

test('late detail response after wide chat switch cannot revert the chosen timeline',async()=>{
 const h=harness({wide:true});let resolve;
 h.context.fetch=async url=>url.startsWith('/api/anomaly/')?new Promise(r=>resolve=r):ok({items:[],snapshot_seq:10,next_before:null});
 await h.run("selectSession('P',{id:'A'},{remember:false})");const opening=h.run('openItem(1,10)');
 await h.run("selectSession('P',{id:'B'},{remember:false})");
 resolve(ok({...detail(1),project:'P',investigation:'A'}));await opening;
 assert.equal(h.run('selectedSession.id'),'B');assert.equal(h.run('current.id'),1);assert.equal(h.get('detail').hidden,false);
});


test('range calendar replaces previous bounds, sorts reverse selection and allows same day',async()=>{
 const h=harness();h.get('start').value='2024-02-20';h.get('end').value='2024-02-25';await h.get('range-open').click();
 const day=k=>h.get('calendar-days').children.find(b=>b.dataset.date===k);
 await day('2024-02-26').click();
 assert.equal(h.get('start').value,'2024-02-20');assert.equal(h.get('end').value,'2024-02-25');
 await day('2024-02-26').dispatch('keydown',{key:'ArrowLeft'});
 assert.equal(h.context.document.activeElement.dataset.date,'2024-02-25');
 await day('2024-02-24').click();
 assert.equal(h.get('start').value,'2024-02-24');assert.equal(h.get('end').value,'2024-02-26');
 assert.equal(h.get('calendar').hidden,true);
 await h.get('range-open').click();await day('2024-02-25').click();await day('2024-02-25').click();
 assert.equal(h.get('start').value,'2024-02-25');assert.equal(h.get('end').value,'2024-02-25');
 await h.get('range-open').click();await h.get('date-today').click();
 assert.equal(h.get('start').value,h.run('localDateKey(new Date())'));
 assert.equal(h.get('start').value,h.get('end').value);assert.equal(h.get('calendar').hidden,true);
});

test('calendar year and month titles offer quick selection and year paging',async()=>{
 const h=harness();h.get('start').value='2026-10-09';h.run("openCalendar()");
 await h.get('calendar-year').click();assert.equal(h.get('calendar-days').children.length,12);assert.equal(h.get('calendar-week').hidden,true);
 await h.get('calendar-days').children.find(b=>b.textContent==='2020年').click();
 assert.equal(h.get('calendar-days').children.length,12);
 await h.get('calendar-days').children.find(b=>b.textContent==='2月').click();
 assert.equal(h.get('calendar-year').textContent,'2020年');assert.equal(h.get('calendar-month').textContent,'2月');
 assert.equal(h.get('calendar-week').hidden,false);
 await h.get('calendar-month').click();await h.get('month-next').click();assert.equal(h.get('calendar-year').textContent,'2021年');
});


test('long text collapses, expands and copies the full original while short text has no toggle',async()=>{
 const h=harness();let copied;h.context.navigator.clipboard.writeText=async value=>{copied=value};
 const text='long output\n'.repeat(100);h.context.longText=text;h.run("codeBlock(el('reading'),longText)");
 const box=h.get('reading').children[0],pre=box.children[1],toggle=box.children[2];pre.scrollHeight=2000;await h.flush();
 assert.equal(box.dataset.collapsed,'true');assert.equal(toggle.hidden,false);assert.equal(pre.textContent,text);
 await toggle.click();assert.equal(box.dataset.collapsed,'false');assert.equal(toggle.attrs['aria-label'],'收起');
 await toggle.click();assert.equal(box.dataset.collapsed,'true');await box.children[0].click();assert.equal(copied,text);
 h.run("codeBlock(el('reading'),'short')");const short=h.get('reading').children[1];short.children[1].scrollHeight=50;await h.flush();assert.equal(short.children[2].hidden,true);
});


test('icon controls have accessible names without visible labels or hover titles',async()=>{
 const h=harness();h.run("codeBlock(el('reading'),'sample')");const box=h.get('reading').children[0];
 box.children[1].scrollHeight=600;await h.flush();
 for(const button of [box.children[0],box.children[2]]){assert.equal(button.textContent,'');assert.equal(button.attrs.title,undefined);assert.ok(button.attrs['aria-label']);assert.equal(button.children[0].attrs['aria-hidden'],'true');}
 await box.children[0].click();assert.equal(box.children[0].dataset.state,'done');await box.dispatch('mouseleave');assert.equal(box.children[0].dataset.state,'copy');
});

test('project disclosure reverses in flight and closes only after animation finishes',async()=>{
 const h=harness(),d=h.context.document.createElement('details'),s=h.context.document.createElement('summary'),animations=[];
 d.open=true;d.getBoundingClientRect=()=>({height:d.open?180:30});s.getBoundingClientRect=()=>({height:30});d.querySelector=()=>null;
 d.animate=(frames,options)=>{const a={frames,options,cancel(){this.cancelled=true}};animations.push(a);return a};
 h.context.disclosure=d;h.context.summary=s;h.run('bindProjectDisclosure(disclosure,summary)');
 let prevented=0;await s.dispatch('click',{preventDefault(){prevented++}});
 assert.equal(d.open,true);assert.equal(d.dataset.closing,'true');assert.equal(animations[0].frames[1].height,'30px');
 await s.dispatch('click',{preventDefault(){prevented++}});
 assert.equal(animations[0].cancelled,true);assert.equal(animations[0].onfinish,null);animations[1].onfinish();assert.equal(d.open,true);
 await s.dispatch('click',{preventDefault(){prevented++}});animations[2].onfinish();assert.equal(d.open,false);assert.equal(d.dataset.closing,undefined);assert.equal(prevented,3);
});
test('reduced motion leaves native disclosure behavior intact',async()=>{
 const h=harness(),d=h.context.document.createElement('details'),s=h.context.document.createElement('summary');
 h.context.window.matchMedia=()=>({matches:true});d.animate=()=>{throw Error('motion should be disabled')};h.context.disclosure=d;h.context.summary=s;
 h.run('bindProjectDisclosure(disclosure,summary)');await s.dispatch('click',{preventDefault(){throw Error('native click should remain')}});
});
test('detail localizes known errors and preserves raw names and complete timestamp',async()=>{
 const h=harness();h.context.fetch=async()=>ok({...detail(1),title:'ModuleNotFoundError',captured:new Date().toISOString()});
 await h.run('openItem(1)');assert.equal(h.get('record-heading').textContent,'缺少模块');assert.equal(h.get('raw-heading').textContent,'ModuleNotFoundError');assert.equal(h.get('raw-heading').hidden,false);
 assert.match(h.get('meta').textContent,/记录于 今天/);assert.match(h.get('meta').title,/年.*月.*日/);
 h.get('meta').textContent='stale';await h.windowEvent('focus',{});assert.match(h.get('meta').textContent,/记录于 今天/);
 h.context.fetch=async()=>ok({...detail(2),title:'CustomApplicationError'});await h.run('openItem(2)');assert.equal(h.get('record-heading').textContent,'CustomApplicationError');assert.equal(h.get('raw-heading').hidden,true);
});

test('error highlight preserves exact text and copy payload and does not interpret markup',async()=>{
 const h=harness();const value='Traceback (most recent call last):\r\n  File "x.py", line 1\r\nFileNotFoundError: <img src=x>\n\'output/model.blend\'\n';
 h.context.sample=value;let copied;h.context.navigator.clipboard.writeText=async v=>{copied=v};
 h.run("codeBlock(el('reading'),sample,'FileNotFoundError')");const box=h.get('reading').children[0],pre=box.children[1];
 assert.equal(pre.children.map(n=>n.textContent).join(''),value);assert.equal(pre.children[1].className,'error-highlight');assert.ok(!descendants(pre).some(n=>n.tagName==='IMG'));
 await box.children[0].click();assert.equal(copied,value);
 h.run("codeBlock(el('reading'),sample)");assert.equal(h.get('reading').children[1].children[1].children.length,0);
 assert.equal(h.run("errorHighlightStart('message mentioning FileNotFoundError: text','FileNotFoundError')"),-1);
 assert.equal(h.run("errorHighlightStart('All good','FileNotFoundError')"),-1);
 assert.equal(h.run("errorHighlightStart('json.decoder.JSONDecodeError: bad','json.decoder.JSONDecodeError')"),0);
});

test('project enable requires consent and cancel never sends a mutation',async()=>{
 const h=harness();let calls=0;h.context.fetch=async()=>{calls++;return ok({})};
 h.run("confirmCapture({path:'D:/p',enabled:false})");assert.equal(h.get('capture-apply').disabled,true);
 await h.run('applyCapture()');assert.equal(calls,0);
 await h.get('capture-cancel').click();assert.equal(h.get('capture-confirm').hidden,true);assert.equal(calls,0);
});
test('project mutation is explicit, duplicate clicks suppressed and real status reloaded',async()=>{
 const h=harness();let finish,posts=[];
 h.context.fetch=async(url,options)=>{if(options.method==='POST'){posts.push(JSON.parse(options.body));return new Promise(r=>finish=r)}return ok({projects:[{path:'D:/p',enabled:true}],storage:{database_bytes:1048576,database_limit_bytes:268435456}})};
 h.run("confirmCapture({path:'D:/p',enabled:false})");h.get('capture-consent').checked=true;
 const pending=h.run('applyCapture()');await h.flush();await h.run('applyCapture()');assert.equal(posts.length,1);assert.equal(posts[0].accept_capture_scope,true);assert.equal(posts[0].expected_enabled,false);
 finish(ok({project:'D:/p',enabled:true}));await pending;assert.match(h.get('help-action-status').textContent,/已启用/);assert.equal(h.get('capture-confirm').hidden,true);assert.equal(h.get('storage-value').textContent,'1.0 MiB / 256.0 MiB');
});
test('failed project mutation is not shown as successful',async()=>{
 const h=harness();h.context.fetch=async(url,options)=>{if(options.method==='POST')throw Error('offline');return ok({projects:[{path:'D:/p',enabled:true}]})};
 h.run("confirmCapture({path:'D:/p',enabled:true})");await h.run('applyCapture()');assert.match(h.get('help-action-status').textContent,/未能确认/);assert.equal(h.get('capture-status').textContent,'1 个已启用 · 0 个已停用');
});
test('privacy links select pages and prompt copy reports success or failure',async()=>{
 const h=harness();await h.get('privacy-learn').click();assert.equal(h.get('help-panel-1').hidden,false);await h.get('privacy-projects').click();assert.equal(h.get('help-panel-0').hidden,false);
 h.get('capture-prompt').textContent='prompt';let copied;h.context.navigator.clipboard.writeText=async text=>copied=text;await h.get('capture-prompt-copy').click();assert.equal(copied,'prompt');assert.equal(h.get('capture-copy-status').textContent,'已复制');
 h.context.navigator.clipboard.writeText=async()=>{throw Error('denied')};await h.get('capture-prompt-copy').click();assert.match(h.get('capture-copy-status').textContent,/手动复制/);
});
test('project card renders untrusted name as text and missing storage stays unknown',async()=>{
 const h=harness();h.context.fetch=async()=>ok({projects:[{path:'D:/<img>',enabled:false}]});await h.run('capture()');assert.equal(h.get('capture-projects').children[0].children[0].textContent,'<img>');assert.equal(h.get('storage-value').textContent,'用量暂不可用');assert.equal(h.get('storage-meter').hidden,true);
});

test('capture modal cancels with Escape without closing drawer or changing project',async()=>{
 const h=harness();h.get('help').open=true;const trigger=h.context.document.createElement('button');trigger.focus();
 h.run("confirmCapture({path:'D:/p',enabled:false})");assert.equal(h.get('privacy-drawer').inert,true);assert.equal(h.get('capture-confirm').hidden,false);assert.equal(h.context.document.activeElement,h.get('capture-cancel'));
 await h.get('help').dispatch('keydown',{key:'Escape',preventDefault(){}});assert.equal(h.get('help').open,true);assert.equal(h.get('capture-confirm').hidden,true);assert.equal(h.get('privacy-drawer').inert,false);assert.equal(h.context.document.activeElement,trigger);
 h.run("confirmCapture({path:'D:/p',enabled:true});captureBusy=true");await h.get('capture-confirm').dispatch('click',{target:h.get('capture-confirm')});await h.get('help').dispatch('keydown',{key:'Escape',preventDefault(){}});h.run('closeHelp()');assert.equal(h.get('capture-confirm').hidden,false);assert.equal(h.get('help').open,true);
});
test('storage explanation toggles by click and remains open until toggled again',async()=>{
 const h=harness();await h.get('storage-help-trigger').click();assert.equal(h.get('storage-help-text').hidden,false);assert.equal(h.get('storage-help-trigger').attrs['aria-expanded'],'true');await h.get('storage-help-trigger').click();assert.equal(h.get('storage-help-text').hidden,true);
});

test('selected card identifiers are numeric and match backend deletion scope',async()=>{const h=harness();h.run("selectingAnomalies=true;toggleAnomaly('42')");assert.equal(h.run('selectedAnomalies.has(42)'),true);let body;h.context.fetch=async(url,opts)=>{body=JSON.parse(opts.body);return ok(cleanupPreview(body.value));};await h.get('delete-selected').click();assert.deepEqual(body.value.ids,[42]);});

test('selection UI hides destructive actions until explicitly selecting and clears on replacement',async()=>{
 const h=harness();assert.equal(h.get('selection-bar').hidden,true);assert.equal(h.get('selection-scope').hidden,true);
 h.context.fetch=async()=>ok({items:[row(1),row(2)],snapshot_seq:10,next_before:2,total_count:130});await h.run('load()');
 await h.get('select-anomalies').click();assert.equal(h.get('delete-selected').disabled,true);
 await h.get('delete-filtered').click();assert.equal(h.get('selection-count').textContent,'已选 130 条');
 assert.equal(h.get('records').children.every(c=>c.dataset.selected==='true'),true);
 await h.get('deselect-all').click();assert.equal(h.get('selection-count').textContent,'已选 0 条');
 await h.get('records').children[0].click();assert.equal(h.get('selection-count').textContent,'已选 1 条');
 h.get('time').value='today';await h.run('load()');assert.equal(h.get('selection-count').textContent,'已选 0 条');assert.equal(h.get('delete-selected').disabled,true);
 await h.get('select-anomalies').click();assert.equal(h.get('selection-bar').hidden,true);
});
test('all-match selection stays checked while loading more and session change clears it',async()=>{
 const h=harness();h.context.fetch=async()=>ok({items:[row(1)],snapshot_seq:10,next_before:1,total_count:130});await h.run('load()');
 await h.get('select-anomalies').click();await h.get('delete-filtered').click();
 h.context.fetch=async()=>ok({items:[row(2)],snapshot_seq:10,next_before:null,total_count:130});await h.run('load(true)');
 assert.equal(h.get('records').children.every(c=>c.dataset.selected==='true'),true);assert.equal(h.get('selection-count').textContent,'已选 130 条');
 h.run('clearSession()');assert.equal(h.get('selection-bar').hidden,true);assert.equal(h.run('allMatchingSelected'),false);
});
test('project clear and delete use distinct confirmation words and reset previous input',async()=>{
 const h=harness();h.context.fetch=async(url,opts)=>ok(cleanupPreview(JSON.parse(opts.body).value));
 for(const [action,word] of [['clear_project','清空'],['delete_project','删除']]){
  await h.run(`prepareCleanup(cleanupScope('${action}',{projects:['D:/project']}))`);
  assert.equal(h.get('cleanup-confirm-word').value,'');
  h.get('cleanup-confirm-word').value='清理';await h.get('cleanup-confirm-word').dispatch('input');assert.equal(h.get('cleanup-apply').disabled,true);
  h.get('cleanup-confirm-word').value=word;await h.get('cleanup-confirm-word').dispatch('input');assert.equal(h.get('cleanup-apply').disabled,false);
 }
});
test('cleanup scope blank click closes menu; calendar month navigation stays open',async()=>{
 const h=harness();h.run("cleanupProjects=[{path:'D:/project'}]");await h.run('openDateCleanup()');
 h.get('cleanup-scope-picker').open=true;await h.windowEvent('click',{target:h.get('cleanup-title')});assert.equal(h.get('cleanup-scope-picker').open,false);
 await h.get('cleanup-start-open').click();assert.equal(h.get('cleanup-calendar').hidden,false);
 const month=h.run('cleanupDateMonth');await h.get('cleanup-month-next').click();await h.get('cleanup-month-next').click();assert.equal(h.get('cleanup-calendar').hidden,false);assert.equal(h.run('cleanupDateMonth'),(month+2)%12);
 await h.get('cleanup-calendar-year').click();assert.equal(h.get('cleanup-calendar-days').children.length,12);
 await h.get('cleanup-calendar-days').children[5].click();assert.equal(h.run('cleanupDateMode'),'months');
 await h.get('cleanup-calendar-days').children[1].click();assert.equal(h.run('cleanupDateMode'),'days');
 await h.get('cleanup-calendar-days').children.find(n=>n.tagName==='BUTTON').click();assert.match(h.get('cleanup-start').value,/-02-01$/);assert.equal(h.get('cleanup-calendar').hidden,true);
 await h.get('cleanup-end-open').click();await h.get('cleanup-dialog').dispatch('keydown',{key:'Escape'});assert.equal(h.get('cleanup-calendar').hidden,true);assert.equal(h.get('cleanup-dialog').hidden,false);
 await h.get('cleanup-dialog').dispatch('keydown',{key:'Escape'});assert.equal(h.get('cleanup-dialog').hidden,true);
});
test('cleanup dates preserve local inclusive boundaries and reject reversed range before request',async()=>{
 const h=harness();await h.run('openDateCleanup()');let calls=[];h.context.fetch=async(url,opts)=>{const b=JSON.parse(opts.body);calls.push(b);return ok(cleanupPreview(b.value));};
 h.get('cleanup-start').value='2026-10-14';h.get('cleanup-end').value='2026-10-13';await h.get('cleanup-preview').click();assert.equal(calls.length,0);
 h.get('cleanup-start').value='';await h.get('cleanup-preview').click();assert.equal(calls.length,1);assert.equal(calls[0].value.start,'');assert.equal(calls[0].value.end,new Date('2026-10-14T00:00:00').toISOString());
 await h.get('cleanup-back').click();await h.get('cleanup-end-open').click();await h.get('cleanup-date-today').click();assert.equal(h.run('cleanupPlan'),null);assert.equal(h.get('cleanup-preview').hidden,false);assert.equal(h.get('cleanup-apply').disabled,true);
});

test('popup fits above a low trigger and clamps to the visible scroll region',()=>{
 const h=harness();h.context.window.innerHeight=600;
 const result=h.run(`(()=>{const panel={scrollHeight:110,style:{},getBoundingClientRect(){return {height:110}}};const picker={dataset:{},getBoundingClientRect(){return {top:450,bottom:480}},closest(){return {getBoundingClientRect(){return {top:100,bottom:510}}}}};fitPopup(picker,panel);return {placement:picker.dataset.placement,height:panel.style.maxHeight}})()`);
 assert.equal(result.placement,'above');assert.equal(result.height,'343px');
});
test('switching privacy tabs closes menus and resets only the drawer scroll',()=>{
 const h=harness();const content={scrollTop:170};h.context.document.querySelector=selector=>selector==='.help-content'?content:null;
 h.run("projectMenus.push({open:true});helpTab=0;selectHelpTab(1)");assert.equal(content.scrollTop,0);assert.equal(h.run('projectMenus[0].open'),false);
 content.scrollTop=24;h.run('selectHelpTab(1)');assert.equal(content.scrollTop,24);
});

test('project receipt uses saved events, remains independent of enabled and error state',async()=>{
 const h=harness();
 h.context.fetch=async()=>ok({projects:[
  {path:'D:/empty',enabled:true},
  {path:'D:/normal',enabled:true,last_recorded_event:{kind:'UserPromptSubmit',captured:'2026-10-13T14:30:22+08:00'}},
  {path:'D:/off',enabled:false,last_recorded_event:{captured:'2026-10-13T14:30:22+08:00'}}]});
 await h.run('capture()');
 const metas=h.get('capture-projects').children.map(card=>card.children[0].children[0]);
 assert.equal(metas[0].children[1].textContent,'等待首条记录');
 assert.match(metas[1].children[1].textContent,/最近收到：/);
 assert.equal(metas[1].children[1].dataset.received,'2026-10-13T14:30:22+08:00');
 assert.equal(metas[2].children.length,1);assert.equal(metas[2].children[0].textContent,'已停用');
 assert.equal(h.run("captureReceiptText({captured:new Date(2026,9,13,14,30,22)},new Date(2026,9,13,15))"),'最近收到：今天 14:30');
 assert.equal(h.run("captureReceiptText({captured:new Date(2026,9,12,14,30,22)},new Date(2026,9,13,15))"),'最近收到：昨天 14:30');
 assert.equal(h.run("captureReceiptText({captured:'invalid'})"),'最近收到：时间未知');
});

test('onboarding distinguishes empty setup, waiting, existing records and filtered emptiness',()=>{
 const h=harness();
 h.run("contextsReady=true;contextsEmpty=true;onboardingStatus={projects:[]};syncOnboarding()");
 assert.equal(h.get('onboarding').hidden,false);assert.equal(h.get('onboarding-title').textContent,'从第一个项目开始');
 h.run("onboardingStatus={projects:[{path:'D:/demo',enabled:true,event_count:0,last_recorded_event:null}]};syncOnboarding()");
 assert.equal(h.get('onboarding-title').textContent,'已启用，等待首条记录');
 h.run("onboardingStatus.projects[0].enabled=false;syncOnboarding()");assert.equal(h.get('onboarding').hidden,true);
 h.run("onboardingStatus.projects[0].enabled=true;onboardingStatus.projects[0].event_count=3;syncOnboarding()");assert.equal(h.get('onboarding').hidden,true);
 h.run("selectedSession={id:'one',project:'D:/demo'};contextsEmpty=false;unfilteredTotal=0;workspaceHasAnomalies=false;snapshotSeq=3;syncOnboarding()");
 assert.equal(h.get('onboarding').hidden,false);assert.equal(h.get('onboarding-title').textContent,'记录已收到，暂无异常');
 h.get('time').value='week';h.run('syncOnboarding()');assert.equal(h.get('onboarding').hidden,true);
 h.get('time').value='all';h.run("view='detail';syncOnboarding()");assert.equal(h.get('onboarding').hidden,true);
 h.run("view='list';selectedSession=null;contextsEmpty=true;onboardingStatus={projects:[]};sessionScope='archived';syncOnboarding()");assert.equal(h.get('onboarding').hidden,true);
 h.run("sessionScope='active';contextsReady=false;syncOnboarding()");assert.equal(h.get('onboarding').hidden,true);
});
test('connect instructions preserve consent flow and close with focus restored',async()=>{
 const h=harness();h.get('capture-prompt').textContent='请先说明保存范围，确认后启用';
 await h.get('onboarding-connect').click();assert.equal(h.get('connect-project').hidden,false);assert.equal(h.get('workspace').inert,true);
 assert.equal(h.get('connect-prompt').textContent,h.get('capture-prompt').textContent);
 await h.get('connect-project').dispatch('keydown',{key:'Escape'});assert.equal(h.get('connect-project').hidden,true);assert.equal(h.get('workspace').inert,false);
 assert.equal(h.context.document.activeElement,h.get('onboarding-connect'));
});

test('enabled projects show runtime, capacity and diagnostic evidence without claiming readiness',async()=>{
 for(const state of ['runtime_problem','capacity_reached','review_diagnostics']){
  const h=harness();h.run('contextsReady=true;contextsEmpty=true');
  h.context.fetch=async()=>ok({projects:[{path:'D:/new',enabled:true,event_count:0,capture_state:state,last_recorded_event:null}]});
  await h.run('capture()');assert.equal(h.get('onboarding-title').textContent,'采集状态需要检查');
  assert.doesNotMatch(h.get('onboarding-project-state').textContent,/采集中|已准备好/);
  const meta=h.get('capture-projects').children[0].children[0].children[0];
  assert.equal(meta.children[0].textContent,'已启用');assert.equal(meta.children[2].className,'capture-project-issue');
  assert.equal(meta.children[2].textContent,'需检查');
  assert.equal(h.get('onboarding-project-state').textContent,'已启用 · 需检查');
  assert.ok(h.get('onboarding-description').textContent.length>10);
 }
});
test('checking after external setup updates the original first-use page',async()=>{
 const h=harness();h.run("contextsReady=true;contextsEmpty=true;onboardingStatus={projects:[]};syncOnboarding()");
 h.context.fetch=async url=>url.startsWith('/api/anomaly-contexts')?ok({projects:[]}):ok({projects:[{path:'D:/added',enabled:true,event_count:0,capture_state:'awaiting_event'}]});
 await h.get('onboarding-check').click();assert.equal(h.get('onboarding-title').textContent,'已启用，等待首条记录');
 assert.equal(h.get('onboarding-retry').hidden,true);assert.match(h.get('status').textContent,/已更新项目与采集状态/);
});
test('partial context or status failures never claim successful refresh and remain retryable',async()=>{
 for(const failed of ['contexts','status']){
  const h=harness();h.run('contextsReady=true;contextsEmpty=true;onboardingStatus={projects:[]}');
  h.context.fetch=async url=>{if((url.startsWith('/api/anomaly-contexts')?'contexts':'status')===failed)throw Error('offline');return ok(url.startsWith('/api/anomaly-contexts')?{projects:[]}:{projects:[]});};
  await h.get('onboarding-check').click();assert.match(h.get('status').textContent,/部分更新失败/);assert.doesNotMatch(h.get('status').textContent,/已更新/);
  assert.equal(h.get('onboarding-retry').hidden,false);assert.equal(h.get('onboarding-retry').disabled,false);
  h.context.fetch=async()=>ok({projects:[]});await h.get('onboarding-retry').click();assert.equal(h.get('onboarding-retry').hidden,true);assert.match(h.get('status').textContent,/已更新/);
 }
});

test('existing status pages follow A and B, including cached returns',async()=>{
 for(const bState of ['events_received','review_diagnostics','runtime_problem','capacity_reached','has_errors']){
  const h=harness();
  h.run(`contextsReady=true;contextsEmpty=false;onboardingStatus={projects:[{path:'A',enabled:true,capture_state:'events_received',event_count:1},{path:'B',enabled:true,capture_state:'${bState}',event_count:1}]}`);
  h.context.fetch=async url=>{
   const errors=bState==='has_errors'&&url.includes('investigation=b');
   return ok(url.includes('anomaly-types')?{types:errors?[{name:'TypeError',count:1}]:[],workspace_has_anomalies:bState==='has_errors'}:{items:errors?[row(1)]:[],snapshot_seq:3,next_before:null,total_count:errors?1:0});
  };
  const check=(title,name)=>{assert.equal(h.get('onboarding').hidden,false);assert.equal(h.get('onboarding-title').textContent,title);assert.equal(h.get('onboarding-project-name').textContent,name);};
  await h.run("selectSession('A',{id:'a'},{remember:false})");if(bState==='has_errors')assert.equal(h.get('onboarding').hidden,true);else check('记录已收到，暂无异常','A');
  await h.run("selectSession('B',{id:'b'},{remember:false})");
  if(bState==='has_errors'){assert.equal(h.get('onboarding').hidden,true);assert.equal(h.get('records').children.length,1);}
  else check(bState==='events_received'?'记录已收到，暂无异常':'采集状态需要检查','B');
  await h.run("selectSession('A',{id:'a'},{remember:false})");await h.flush();if(bState==='has_errors')assert.equal(h.get('onboarding').hidden,true);else check('记录已收到，暂无异常','A');
  h.run("onboardingStatus.capture_health={state:'review_diagnostics'};delete onboardingStatus.projects[0].capture_state;syncOnboarding()");if(bState==='has_errors')assert.equal(h.get('onboarding').hidden,true);else check('记录已收到，暂无异常','A');
 }
});

test('unknown anomaly totals never display the received-without-errors page',()=>{
 const h=harness();
 h.run("selectedSession={id:'a',project:'A'};onboardingStatus={projects:[{path:'A',enabled:true,event_count:1}]};snapshotSeq=3;unfilteredTotal=null;syncOnboarding()");
 assert.equal(h.get('onboarding').hidden,true);
 h.run('unfilteredTotal=0;workspaceHasAnomalies=false;loading=true;syncOnboarding()');assert.equal(h.get('onboarding').hidden,true);
 h.run('loading=false;syncOnboarding()');assert.equal(h.get('onboarding').hidden,false);
 h.get('anomaly-type').value='TypeError';h.run('syncOnboarding()');assert.equal(h.get('onboarding').hidden,true);
});

test('both refresh entries report type failures, keep list results and recover on retry',async()=>{
 for(const entry of ['onboarding-refresh','refresh']){
  const h=harness();let broken=true;
  h.run("selectedSession={id:'a',project:'A'};contextsReady=true;contextsEmpty=false");
  h.context.fetch=async url=>{
   if(url.startsWith('/api/anomaly-contexts'))return ok(url.includes('project_path=')?{sessions:[{id:'a',project:'A'}]}:{projects:[{path:'A',name:'A'}]});
   if(url.startsWith('/api/anomaly-types')){if(broken)throw Error('offline');return ok({types:[{name:'TypeError',count:1}]});}
   if(url.startsWith('/api/anomalies?'))return ok({items:[row(1)],snapshot_seq:3,next_before:null,total_count:1});
   return ok({projects:[{path:'A',enabled:true,event_count:1,capture_state:'events_received'}]});
  };
  await h.get(entry).click();
  assert.match(h.get('status').textContent,/部分更新失败：类型与总数/);assert.equal(h.get('onboarding-retry').hidden,false);
  assert.equal(h.get('records').children.length,1);assert.equal(h.get('refresh').disabled,false);
  broken=false;await h.get('onboarding-retry').click();
  assert.match(h.get('status').textContent,/已更新项目与采集状态/);assert.equal(h.get('onboarding-retry').hidden,true);
 }
});


test('wide project switching keeps the previous detail and shows the new session empty list',async()=>{
 const h=harness({wide:true});
 h.run("onboardingStatus={projects:[{path:'A',enabled:true,event_count:1},{path:'B',enabled:true,event_count:1}]}");
 h.context.fetch=async url=>url.startsWith('/api/anomaly/')?ok({...detail(1),project:'B',investigation:'b'}):ok(url.includes('anomaly-types')?{types:[],workspace_has_anomalies:true}:{items:[],snapshot_seq:3,next_before:null,total_count:0});
 await h.run("selectSession('B',{id:'b'},{remember:false})");await h.run('openItem(1,3)');
 await h.run("selectSession('A',{id:'a'},{remember:false})");
 assert.equal(h.run('view'),'detail');assert.equal(h.run('current.project'),'B');assert.equal(h.run('selectedSession.project'),'A');
 assert.equal(h.get('onboarding').hidden,true);assert.equal(h.get('list-empty').hidden,false);assert.equal(h.get('export').disabled,false);
 h.run('showList()');assert.equal(h.get('onboarding').hidden,true);
});

test('successful context refresh removes a selection absent from the returned navigation',async()=>{
 for(const projects of [[],[{path:'A',name:'A'}]]){
  const h=harness();
  h.run("selectedSession={id:'gone',project:'A'};snapshotSeq=3;unfilteredTotal=0");
  h.get('project').value='A';h.get('session').value='gone';
  h.context.fetch=async url=>ok(url.includes('project_path=')?{sessions:[]}:{projects});
  await h.run('contexts()');
  assert.equal(h.run('selectedSession'),null);assert.equal(h.get('session').value,'');assert.equal(h.run('snapshotSeq'),null);
 }
});

test('confirmed cleanup remains confirmed when its follow-up status refresh fails',async()=>{
 const h=harness();
 h.run("cleanupPlan={token:'receipt',scope:{action:'session'},can_apply:true}");
 h.context.fetch=async url=>{
  if(url==='/api/cleanup')return ok({token:'receipt',reclaimed:true,events:4,freed_capacity_bytes:1024,database_bytes:4096});
  if(url.startsWith('/api/anomaly-contexts'))return ok({projects:[]});
  throw Error('status offline');
 };
 await h.run('applyCleanup()');
 assert.match(h.get('cleanup-result').textContent,/已删除 4 条事件/);
 assert.match(h.get('cleanup-result').textContent,/刷新/);
 assert.doesNotMatch(h.get('cleanup-result').textContent,/未取得完成确认/);
 assert.equal(h.get('cleanup-retry').hidden,true);
});


test('failed context refresh preserves selection instead of treating failure as deletion',async()=>{
 const h=harness();h.run("selectedSession={id:'kept',project:'A'}");
 h.context.fetch=async()=>{throw Error('offline')};
 assert.equal(await h.run('contexts()'),false);assert.equal(h.run('selectedSession.id'),'kept');
});

test('late empty context response cannot clear a newer user selection',async()=>{
 const h=harness();let resolve;
 h.run("selectedSession={id:'old',project:'A'}");h.context.fetch=async()=>new Promise(done=>resolve=done);
 const pending=h.run('contexts()');
 h.run("selectedSession={id:'new',project:'B'};++selectionGeneration");resolve(ok({projects:[]}));await pending;
 assert.equal(h.run('selectedSession.id'),'new');
});


test('empty configured projects remain selectable and display their own waiting page',async()=>{
 const h=harness();
 h.run("onboardingStatus={projects:[{path:'A',enabled:true,event_count:0},{path:'B',enabled:true,event_count:0}]}");
 h.context.fetch=async url=>ok(url.includes('project_path=')?{sessions:[]}:{projects:[{path:'A',name:'A'},{path:'B',name:'B'}]});
 await h.run('contexts()');assert.equal(h.get('project-tree').children.length,2);
 for(const path of ['B','A']){
  await h.run(`sessionGroups.get('${path}').summary.click()`);
  assert.equal(h.run('selectedProject'),path);assert.equal(h.run('selectedSession'),null);
  assert.equal(h.get('onboarding').hidden,false);assert.equal(h.get('onboarding-title').textContent,'已启用，等待首条记录');assert.equal(h.get('onboarding-project-name').textContent,path);
 }
});

test('a sibling anomaly makes a zero-error session use its existing empty list',async()=>{
 const h=harness();let hasErrors=false;
 h.run("onboardingStatus={projects:[{path:'A',enabled:true,event_count:2}]}");
 h.context.fetch=async url=>ok(url.includes('anomaly-types')?{types:[],workspace_has_anomalies:hasErrors}:{items:[],snapshot_seq:3,next_before:null,total_count:0});
 await h.run("selectSession('A',{id:'quiet'},{remember:false})");assert.equal(h.get('onboarding').hidden,false);
 hasErrors=true;await h.run('load()');assert.equal(h.get('onboarding').hidden,true);assert.equal(h.get('list-empty').hidden,false);
 await h.run("selectSession('A',{id:'another-quiet'},{remember:false})");assert.equal(h.get('onboarding').hidden,true);
 await h.run("selectSession('A',{id:'quiet'},{remember:false})");await h.flush();assert.equal(h.get('onboarding').hidden,true);assert.equal(h.get('list-empty').hidden,false);
});


test('normal workspace selection of a project without received events preserves the open detail',async()=>{
 const h=harness({wide:true});
 h.run("workspaceHasAnomalies=true;view='detail';current={id:1,project:'A'};onboardingStatus={projects:[{path:'B',enabled:true,event_count:0}]};sessionGroups.set('B',{paths:['B'],sessions:[],loaded:true,buttons:new Map()})");
 await h.run("selectProject('B')");
 assert.equal(h.run('current.project'),'A');assert.equal(h.run('view'),'detail');assert.equal(h.get('onboarding').hidden,true);assert.equal(h.get('list-empty').hidden,false);
});


test('cold workspace with errors opens a waiting-record project as a list, not onboarding',async()=>{
 const h=harness();h.run("onboardingStatus={projects:[{path:'B',enabled:true,event_count:0}]}");
 h.context.fetch=async url=>ok(url.includes('project_path=')?{sessions:[]}:{projects:[{path:'B',name:'B'}],workspace_has_anomalies:true});
 await h.run('contexts()');assert.equal(h.get('onboarding').hidden,true);assert.equal(h.get('list-empty').hidden,false);assert.equal(h.get('list-heading').textContent,'异常记录');
});

test('older cached snapshot cannot send an established workspace back to onboarding',async()=>{
 const h=harness();h.run("workspaceHasAnomalies=true;onboardingStatus={projects:[{path:'B',enabled:true,event_count:1}]}");
 h.context.fetch=async url=>ok(url.includes('anomaly-types')?{types:[],workspace_has_anomalies:false}:{items:[],snapshot_seq:2,next_before:null,total_count:0});
 await h.run("selectSession('B',{id:'b'},{remember:false})");assert.equal(h.get('onboarding').hidden,true);assert.equal(h.get('list-empty').hidden,false);
});


function refreshFixture(h,{missingA=false,failA=false}={}){
 h.context.fetch=async url=>{
  if(url.includes('anomaly-contexts')){
   if(url.includes('project_path=A')){if(failA)throw Error('offline');return ok({sessions:missingA?[]:[{id:'a',project:'A'}]});}
   if(url.includes('project_path=B'))return ok({sessions:[]});
   return ok({projects:[{path:'A',name:'A'},{path:'B',name:'B'}],workspace_has_anomalies:true});
  }
  if(url.includes('session-names'))return ok({names:{a:'A chat'},states:{a:'named'}});
  return ok(url.includes('anomaly-types')?{types:[],workspace_has_anomalies:true}:{items:[],snapshot_seq:3,next_before:null,total_count:0});
 };
}

test('refresh of B without received events retains the valid independent A detail',async()=>{
 const h=harness({wide:true});refreshFixture(h);await h.run('contexts()');await h.flush();
 h.run("view='detail';current={id:1,project:'A',investigation:'a'}");
 await h.run("selectProject('B')");assert.equal(h.run('current.id'),1);
 await h.run('contexts()');assert.equal(h.run('current.id'),1);assert.equal(h.run('view'),'detail');assert.equal(h.run('selectedProject'),'B');
});

test('refresh closes a removed independent detail without losing the B project selection',async()=>{
 const h=harness({wide:true});refreshFixture(h);await h.run('contexts()');await h.flush();
 h.run("view='detail';current={id:1,project:'A',investigation:'a'}");await h.run("selectProject('B')");
 refreshFixture(h,{missingA:true});await h.run('contexts()');
 assert.equal(h.run('current'),null);assert.equal(h.run('view'),'list');assert.equal(h.run('selectedProject'),'B');assert.equal(h.get('export').disabled,true);
});

test('failed independent detail membership check preserves the detail and reports refresh failure',async()=>{
 const h=harness({wide:true});refreshFixture(h);await h.run('contexts()');await h.flush();
 h.run("view='detail';current={id:1,project:'A',investigation:'a'}");await h.run("selectProject('B')");
 refreshFixture(h,{failA:true});assert.equal(await h.run('contexts()'),false);assert.equal(h.run('current.id'),1);
});

test('late detail membership check cannot close a newer detail',async()=>{
 const h=harness({wide:true});let finish;
 h.run("view='detail';current={id:1,project:'A',investigation:'a'};selectedProject='B'");
 h.context.fetch=async url=>{
  if(url.includes('project_path=A'))return new Promise(r=>finish=r);
  if(url.includes('project_path=B'))return ok({sessions:[]});
  return ok({projects:[{path:'A'},{path:'B'}],workspace_has_anomalies:true});
 };
 const pending=h.run('contexts()');await h.flush();
 h.run("current={id:2,project:'B',investigation:'new'};++openGeneration");finish(ok({sessions:[]}));await pending;
 assert.equal(h.run('current.id'),2);
});

test('selecting a project without received events clears the old session highlight',async()=>{
 const h=harness();refreshFixture(h);await h.run('contexts()');await h.flush();
 assert.equal(h.run("sessionGroups.get('A').buttons.get('a').button.attrs['aria-current']"),'page');
 await h.run("selectProject('B')");assert.equal(h.run("sessionGroups.get('A').buttons.get('a').button.attrs['aria-current']"),'false');
});

test('all three status pages reuse the existing project navigation button and return path',async()=>{
 for(const mode of ['waiting','received','diagnostic']){
  const h=harness();
  h.run("contextsReady=true;selectedProject='A';workspaceHasAnomalies=false;onboardingStatus={projects:[{path:'A',enabled:true,event_count:0}]}");
  if(mode==='received')h.run("selectedSession={id:'a',project:'A'};unfilteredTotal=0;snapshotSeq=3;onboardingStatus.projects[0].event_count=1");
  if(mode==='diagnostic')h.run("onboardingStatus.projects[0].capture_state='review_diagnostics'");
  h.run('syncOnboarding()');assert.equal(h.get('browse-sessions').parentNode,h.get('onboarding-navigation'));
  await h.get('browse-sessions').click();assert.equal(h.get('workspace').dataset.stage,'navigation');assert.equal(h.get('nav-return').hidden,false);
  await h.get('nav-return').click();assert.equal(h.get('workspace').dataset.stage,'reading');
  h.run('workspaceHasAnomalies=true;syncOnboarding()');assert.equal(h.get('browse-sessions').parentNode,h.get('list-navigation'));
 }
 assert.match(html,/<div id="onboarding-navigation"><\/div>/);
 assert.match(html,/<div id="list-navigation"><button id="browse-sessions" class="mobile-navigation back-button">/);
});


test('refresh removes a detail whose entire project was deleted',async()=>{
 const h=harness({wide:true});h.run("current={id:1,project:'A',investigation:'a'};view='detail';selectedProject='B'");
 h.context.fetch=async url=>ok(url.includes('project_path=')?{sessions:[]}:{projects:[{path:'B'}],workspace_has_anomalies:true});
 await h.run('contexts()');assert.equal(h.run('current'),null);assert.equal(h.run('selectedProject'),'B');assert.equal(h.get('list-empty').hidden,false);
});

test('refresh retains an independently opened archived detail because its source still exists',async()=>{
 const h=harness({wide:true});h.run("current={id:1,project:'A',investigation:'a'};view='detail';selectedProject='B'");
 h.context.fetch=async url=>{
  if(url.includes('project_path=A'))return ok({sessions:[{id:'a',project:'A',archived:true}]});
  if(url.includes('project_path=B'))return ok({sessions:[]});
  if(url.includes('session-names'))return ok({names:{a:'A chat'},states:{a:'named'}});
  return ok({projects:[{path:'A'},{path:'B'}],workspace_has_anomalies:true});
 };
 await h.run('contexts()');assert.equal(h.run('current.id'),1);assert.equal(h.run('selectedProject'),'B');
});


test('global selection exclusions survive pagination and can be restored or reset',async()=>{
 const h=harness();h.context.fetch=async()=>ok({items:[row(1),row(2)],snapshot_seq:500,next_before:2,total_count:500});await h.run('load()');
 await h.get('select-anomalies').click();await h.get('delete-filtered').click();await h.get('records').children[0].click();
 assert.equal(h.get('selection-count').textContent,'已选 499 条');assert.equal(h.get('records').children[0].dataset.selected,'false');
 assert.deepEqual(JSON.parse(h.run('JSON.stringify(selectedCleanupScope())')).excluded_ids,[1]);
 h.context.fetch=async()=>ok({items:[row(3)],snapshot_seq:500,next_before:null,total_count:500});await h.run('load(true)');
 assert.equal(h.get('records').children[2].dataset.selected,'true');assert.equal(h.get('selection-count').textContent,'已选 499 条');
 await h.get('records').children[0].click();assert.equal(h.get('selection-count').textContent,'已选 500 条');
 await h.get('records').children[0].click();await h.get('delete-filtered').click();assert.equal(h.get('selection-count').textContent,'已选 500 条');
 h.run('resetSelection()');assert.equal(h.run('excludedAnomalies.size'),0);assert.equal(h.get('delete-selected').disabled,true);
});
test('date cleanup preview uses inclusive local dates and only relevant version warnings',async()=>{
 const h=harness();let versions=0,retained=0;
 h.context.fetch=async(url,opts)=>ok({...cleanupPreview(JSON.parse(opts.body).value),versions,retained_versions:retained});
 const command="prepareCleanup(cleanupScope('dates',{start:new Date(2026,9,1).toISOString(),end:new Date(2026,9,14).toISOString()}))";
 await h.run(command);let text=h.get('cleanup-summary').children.map(x=>x.textContent).join(' ');
 assert.match(text,/2026-10-01 起，截至 2026-10-13（含当天）/);assert.doesNotMatch(text,/历史版本|不含/);
 versions=2;await h.run(command);text=h.get('cleanup-summary').children.map(x=>x.textContent).join(' ');assert.match(text,/删除历史版本 2 个/);assert.doesNotMatch(text,/可能仍含/);
 retained=1;await h.run(command);text=h.get('cleanup-summary').children.map(x=>x.textContent).join(' ');assert.match(text,/可能仍含/);
});


test('completed cleanup closes modal and reports result in the underlying drawer',async()=>{
 const h=harness();h.get('help').open=true;
 h.run("showCleanup();cleanupPlan={token:'done',scope:{action:'session'},can_apply:true};refreshAfterCleanup=async()=>[]");
 h.context.fetch=async()=>ok({token:'done',reclaimed:true,events:3,freed_capacity_bytes:1024,database_bytes:4096});
 await h.run('applyCleanup()');assert.equal(h.get('cleanup-dialog').hidden,true);assert.equal(h.get('help').inert,false);
 assert.match(h.get('help-action-status').textContent,/已删除 3 条事件/);
});
test('cleanup failure or pending reclamation keeps the retry dialog open',async()=>{
 for(const pending of [true,false]){
  const h=harness();h.run("showCleanup();cleanupPlan={token:'done',scope:{action:'session'},can_apply:true};refreshAfterCleanup=async()=>[]");
  h.context.fetch=async()=>{if(!pending)throw Error('offline');return ok({token:'done',reclaimed:false,events:3});};
  await h.run('applyCleanup()');assert.equal(h.get('cleanup-dialog').hidden,false);assert.equal(h.get('cleanup-retry').hidden,false);
 }
});


test('untrusted Hook replaces waiting state and clears after trust refresh',async()=>{
 const h=harness();
 h.run("contextsReady=true;contextsEmpty=true;selectedProject='A';workspaceHasAnomalies=false;onboardingStatus={projects:[{path:'A',enabled:true,event_count:0,capture_state:'hooks_untrusted'}]};syncOnboarding()");
 assert.equal(h.get('onboarding-title').textContent,'采集状态需要检查');
 assert.match(h.get('onboarding-description').textContent,/尚未信任/);
 assert.match(h.get('onboarding-description').textContent,/hooks/);
 h.run("onboardingStatus.projects[0].capture_state='waiting_first_event';syncOnboarding()");
 assert.equal(h.get('onboarding-title').textContent,'已启用，等待首条记录');
});
