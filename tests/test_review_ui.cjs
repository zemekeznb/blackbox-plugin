// State/race regression tests without touching real records or installing dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../plugins/tas-notes/assets/review.js'), 'utf8');
function harness() {
  const elements = new Map(), downloads = [];
  function node() {
    return {value:'', textContent:'', hidden:false, children:[], dataset:{}, attrs:{},
      append(...items){this.children.push(...items);}, replaceChildren(){this.children=[];},
      setAttribute(k,v){this.attrs[k]=v;}, addEventListener(){}, focus(){}, select(){}, scrollIntoView(){},
      click(){if(this.download) downloads.push(this.download);},
      querySelector(){return this.children[0];}};
  }
  const get = id => {if(!elements.has(id)) elements.set(id,node()); return elements.get(id);};
  const context = vm.createContext({document:{getElementById:get,createElement:node,createTextNode:x=>x},
    location:{hash:'#token=test'}, URLSearchParams, AbortSignal, URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
    window:{addEventListener(){}}, confirm:()=>true, setTimeout(){},
    fetch:async()=>({ok:true,json:async()=>[]})});
  // Suppress only the two page-load requests; real functions below remain unchanged.
  vm.runInContext(source.replace(/list\(\)\.catch\(e => status\(e.message\)\);\s*captureStatus\(\);\s*$/, ''),context);
  return {context,get,downloads,run:s=>vm.runInContext(s,context)};
}
function record(draft='draft') { return {id:'a',project:'test',display_title:'title',draft,overview:'overview',events:[],source_seq:0}; }
test('download retains original record and revision when selection changes mid-request', async()=>{
  const h=harness(); let resolve;
  h.context.fetch=()=>new Promise(r=>{resolve=r;});
  h.run("selected={id:'a',revision:{id:'rev-a',number:1}}");
  const pending=h.run('exportSaved()');
  h.run("selected={id:'b',revision:{id:'rev-b',number:9}}");
  resolve({ok:true,blob:async()=>({})}); await pending;
  assert.deepEqual(h.downloads,['investigation-a-v1.md']);
});
test('oversize draft leaves editor empty, never saves guidance as content', async()=>{
  const h=harness(); let posts=0;
  h.context.fetch=async(_url,opts)=>{if(opts.method==='POST') posts++; return {ok:true,json:async()=>record('x'.repeat(200001))};};
  await h.run("openRecord('a')");
  assert.equal(h.get('body').value,'');
  assert.match(h.get('editor-help').textContent,/超过/);
  await h.run('save()'); assert.equal(posts,0);
});
test('edits made during slow record opening are preserved', async()=>{
  const h=harness(); let resolve;
  h.context.fetch=()=>new Promise(r=>{resolve=r;});
  const pending=h.run("openRecord('a')");
  h.get('body').value='new unsaved work'; h.run('editSequence++; dirty=true;');
  resolve({ok:true,json:async()=>record()}); await pending;
  assert.equal(h.get('body').value,'new unsaved work');
  assert.equal(h.run('dirty'),true);
});
test('empty search and list refresh cannot overwrite editor status', async()=>{
  const h=harness(); h.get('query').value='missing'; h.get('status').textContent='save conflict';
  await h.run('list()');
  assert.match(h.get('list-status').textContent,/没有匹配/);
  assert.equal(h.get('status').textContent,'save conflict');
  assert.equal(h.run('dateLabel(null)'),'时间未知');
});
