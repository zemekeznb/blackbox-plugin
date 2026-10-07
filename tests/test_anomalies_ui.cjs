const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../plugins/tas-notes/assets/anomalies.js'),'utf8');
function harness(preview=false) {
  const elements=new Map(),downloads=[];
  function node(tag='div') {return {tagName:tag.toUpperCase(),value:'',textContent:'',children:[],dataset:{},attrs:{},
    append(...n){this.children.push(...n)},replaceChildren(){this.children=[]},setAttribute(k,v){this.attrs[k]=v},
    addEventListener(){},click(){if(this.download)downloads.push(this.download)}};}
  const get=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id)};
  const context=vm.createContext({document:{getElementById:get,createElement:node},
    location:{hash:'#token=test',protocol:preview?'file:':'http:'}, URLSearchParams,AbortSignal,
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},setTimeout(){},
    fetch:async()=>{throw new Error('unexpected initial network')}});
  vm.runInContext(preview?source:source.slice(0,source.indexOf("if(location.protocol==='file:')")),context);
  return {get,context,downloads,run:s=>vm.runInContext(s,context)};
}
function detail(id) {return {id,title:'failure '+id,project:'project',tool:'Bash',captured:'2026-10-07T00:00:00Z',source_seq:8,rule_version:'v1',
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
  assert.match(h.get('status').textContent,/无需人工确认/);
});
test('empty scan page with a cursor remains traversable and pins snapshot across new arrivals',async()=>{
  const h=harness(), urls=[];
  const pages=[{items:[],snapshot_seq:501,next_before:2,scanned_count:500},
    {items:[{id:1,title:'old error',captured:'2026-10-07',tool:'Bash',project:'p'}],snapshot_seq:501,next_before:null,scanned_count:1,has_newer:true}];
  h.context.fetch=async url=>{urls.push(url);return {ok:true,json:async()=>pages.shift()}};
  await h.run('load()');
  assert.equal(h.get('more').hidden,false);assert.match(h.get('list-status').textContent,/尚未扫描/);
  h.get('project').value='unsaved filter';
  await h.run('load(true)');
  const next=new URL(urls[1],'http://localhost');
  assert.equal(next.searchParams.get('through'),'501'); assert.equal(next.searchParams.get('before'),'2');
  assert.equal(next.searchParams.get('project'),'');
  assert.equal(h.get('more').hidden,true);assert.match(h.get('list-status').textContent,/有新事件/);
  assert.equal(h.get('records').children.length,1);
  assert.match(h.get('list-status').textContent,/501 个事件/);
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
  assert.ok(notice);assert.match(notice.textContent,/已知采集缺口/);
  assert.match(notice.textContent,/停止跨缺口自动关联/);
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
test('failed network request leaves current readable record intact',async()=>{
  const h=harness();h.run("current={id:'1'}");
  await assert.rejects(h.run("openItem('2')"),/连接中断/);
  assert.equal(h.run('current.id'),'1');
});
