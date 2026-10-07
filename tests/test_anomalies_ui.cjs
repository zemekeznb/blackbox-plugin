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
test('empty and partially indexed lists disclose limits',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:true,json:async()=>({items:[],index_pending:false})});
  await h.run('load()');assert.match(h.get('list-status').textContent,/不代表没有问题/);
  h.context.fetch=async()=>({ok:true,json:async()=>({items:[],index_pending:true})});
  await h.run('load()');assert.match(h.get('list-status').textContent,/尚未完成/);
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
