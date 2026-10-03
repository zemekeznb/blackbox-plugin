// State/race regression tests without touching real records or installing dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../plugins/tas-notes/assets/review.js'), 'utf8');
function harness(filePreview=false) {
  const elements = new Map(), downloads = [];
  function node(tag='div') {
    return {tagName:tag.toUpperCase(), value:'', textContent:'', hidden:false, children:[], dataset:{}, attrs:{},
      append(...items){this.children.push(...items);}, replaceChildren(){this.children=[];},
      setAttribute(k,v){this.attrs[k]=v;}, addEventListener(){}, focus(){}, select(){}, scrollIntoView(){},
      click(){if(this.download) downloads.push(this.download);},
      querySelector(){return this.children[0];}};
  }
  const get = id => {if(!elements.has(id)) elements.set(id,node()); return elements.get(id);};
  const context = vm.createContext({document:{getElementById:get,createElement:node,createTextNode:x=>x},
    location:{hash:'#token=test',protocol:filePreview?'file:':'http:'}, URLSearchParams, AbortSignal, URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
    window:{addEventListener(){}}, confirm:()=>true, setTimeout(){},
    fetch:async()=>({ok:true,json:async()=>[]})});
  // Suppress only the two page-load requests; real functions below remain unchanged.
  vm.runInContext(filePreview ? source : source.slice(0,source.indexOf("if (location.protocol === 'file:')")),context);
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
