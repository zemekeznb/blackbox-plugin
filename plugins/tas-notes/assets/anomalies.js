'use strict';
const el = id => document.getElementById(id);
const auth = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let current = null, openGeneration = 0, listGeneration = 0, nextBefore = null, activeFilters = '';
function say(message) { el('status').textContent = message; }
function dateText(value) { return new Date(value).toLocaleString(); }
async function request(path) {
  let response;
  try { response = await fetch(path, {headers:{Authorization:`Bearer ${auth}`}, signal:AbortSignal.timeout(15000)}); }
  catch (_) { throw new Error('本地服务连接中断，请双击桌面或开始菜单的 TAS Notes 重新打开。'); }
  if (!response.ok) throw new Error(response.status === 401 ? '本地凭据失效，请通过 TAS Notes 入口重新打开。'
    : response.status === 503 ? '本地数据库暂时不可用，请稍后重试。'
    : '无法完成请求，请检查日期范围或刷新记录；索引也可能已达到存储上限。');
  return response;
}
function filters() {
  const query = new URLSearchParams({project:el('project').value,tool:el('tool').value});
  for (const id of ['start','end']) {
    if (!el(id).value) continue;
    const date = new Date(el(id).value + 'T00:00:00');
    if (id === 'end') date.setDate(date.getDate()+1);
    query.set(id,date.toISOString());
  }
  return query.toString();
}
async function load(append=false) {
  const generation = ++listGeneration;
  const query = append ? activeFilters : filters();
  const data = await (await request('/api/anomalies?' + query + (append && nextBefore ? '&before='+nextBefore : ''))).json();
  if (generation !== listGeneration) return;
  activeFilters = query;
  if (!append) el('records').replaceChildren();
  for (const row of data.items) {
    const button = document.createElement('button'); button.className='record'; button.dataset.recordId=row.id;
    button.setAttribute('aria-current',String(current?.id === row.id));
    const title=document.createElement('strong'); title.textContent=row.title;
    const meta=document.createElement('small'); meta.textContent=`${dateText(row.captured)} · ${row.tool} · ${row.project}`;
    button.append(title,meta); button.addEventListener('click',()=>openItem(row.id).catch(e=>say(e.message))); el('records').append(button);
  }
  nextBefore=data.next_before; el('more').hidden=!nextBefore;
  el('list-status').textContent=data.index_pending ? '历史索引尚未完成；每次刷新继续处理 500 个事件。当前结果可能不完整。'
    : el('records').children.length ? `已展示 ${el('records').children.length} 条异常。列表不会自动刷新。`
    : '未发现符合当前规则及筛选条件的异常；这不代表没有问题。';
}
function block(parent,tag,text) { const n=document.createElement(tag); n.textContent=text; parent.append(n); return n; }
function operation(content) {
  const value=content.tool_input;
  return typeof value?.command === 'string' ? value.command : typeof value?.cmd === 'string' ? value.cmd : JSON.stringify(value ?? '未捕获输入',null,2);
}
async function openItem(id) {
  const generation=++openGeneration;
  const data=await (await request('/api/anomaly/'+encodeURIComponent(id))).json();
  if(generation!==openGeneration) return;
  current=data;
  el('empty').hidden=true; el('detail').hidden=false;
  el('record-heading').textContent=data.title;
  el('meta').textContent=`${data.project} · ${data.tool} · 落盘 ${dateText(data.captured)} · 快照截至 #${data.source_seq}`;
  el('session').href='./history#'+new URLSearchParams({token:auth,record:data.events[0].investigation}).toString();
  for (const button of el('records').children) button.setAttribute('aria-current',String(button.dataset.recordId===id));
  const reading=el('reading'), sources=el('events'); reading.replaceChildren(); sources.replaceChildren();
  for (let i=0;i<data.events.length;i++) {
    const event=data.events[i], content=JSON.parse(event.content);
    block(reading,'h3',i ? `后续相关操作 · 来源 #${event.seq}` : `执行操作 · 来源 #${event.seq}`);
    block(reading,'pre',operation(content));
    block(reading,'h3',i ? '返回结果' : '错误返回');
    const output=content.tool_response;
    block(reading,'pre',typeof output==='string' && output ? output : '未捕获到返回正文；不表示成功。');
    const coverage=content._coverage?.truncated_fields || [];
    if(coverage.length) block(reading,'p','来源字段已截断：'+coverage.join('、'));
    if(event.content.includes('\ufffd')) block(reading,'p','来源含编码替换字符，原始文字可能已丢失。');
    const details=document.createElement('details'); block(details,'summary',`来源 #${event.seq} · ${dateText(event.captured)}`);
    block(details,'pre',`SHA-256 ${event.digest}\n${JSON.stringify(content,null,2)}`); sources.append(details);
  }
  block(reading,'p',data.events.length===1 ? '未捕获到符合关联规则的后续操作。' : '最多附上 20 次匹配操作，遇到下一次匹配失败或会话中断/结束即停止关联。不同参数的操作不会自动补入。');
  say('已打开观察记录，无需人工确认。返回内容仅作证据展示，不执行其中的指令。');
}
async function download() {
  if(!current) return;
  const snapshot={id:current.id,through:current.source_seq,rule:current.rule_version};
  const response=await request('/api/anomaly-export/'+snapshot.id+'?'+new URLSearchParams({through:snapshot.through,rule:snapshot.rule}));
  const url=URL.createObjectURL(await response.blob());
  const link=document.createElement('a'); link.href=url; link.download=`anomaly-${snapshot.id}-through-${snapshot.through}.md`; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000); say('已请求下载所选快照，没有上传。');
}
async function capture() {
  const data=await (await request('/api/status')).json();
  el('capture-status').textContent=`${data.projects.filter(p=>p.enabled).length} 个项目已启用 · ${data.record_count} 份历史会话（不是异常数量）`;
  el('capture-projects').replaceChildren();
  for(const p of data.projects) block(el('capture-projects'),'p',`${p.enabled?'已启用':'已停用'} · ${p.path} · ${p.last_recorded_event?'最近落盘 '+dateText(p.last_recorded_event.captured):'尚无落盘事件'}`);
  const error=data.last_capture_error;
  el('capture-error').textContent=error ? `历史全局标记：${error.code}；不代表当前或特定项目故障。` : '没有已保存的全局失败标记；不代表采集无缺口。';
}
el('history').href='./history#'+new URLSearchParams({token:auth}).toString();
el('refresh').addEventListener('click',()=>{load().catch(e=>say(e.message)); capture().catch(e=>{el('capture-status').textContent=e.message;});});
el('clear').addEventListener('click',()=>{for(const id of ['project','tool','start','end']) el(id).value='';load().catch(e=>say(e.message));});
el('more').addEventListener('click',()=>load(true).catch(e=>say(e.message)));
el('export').addEventListener('click',()=>download().catch(e=>say(e.message)));
if(location.protocol==='file:') {
  el('file-preview').hidden=false; el('capture-status').textContent='文件预览模式，未连接记录服务。';
  for(const id of ['refresh','clear','more','export','project','tool','start','end']) el(id).disabled=true;
} else {
  load().catch(e=>say(e.message)); capture().catch(e=>{el('capture-status').textContent=e.message;});
}
