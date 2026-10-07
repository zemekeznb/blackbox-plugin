'use strict';
const el = id => document.getElementById(id);
const auth = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let current = null, openGeneration = 0, listGeneration = 0, captureGeneration = 0;
let nextBefore = null, snapshotSeq = null, scannedCount = 0, activeFilters = '', loading = false;
function say(message) { el('status').textContent = message; }
function dateText(value) { return value ? new Date(value).toLocaleString() : '时间未知'; }
async function request(path, method='GET') {
  let response;
  try { response = await fetch(path, {method,headers:{Authorization:`Bearer ${auth}`}, signal:AbortSignal.timeout(15000)}); }
  catch (_) { throw new Error('本地服务连接中断，请双击桌面或开始菜单的 Blackbox Plugin 重新打开。'); }
  if (!response.ok) throw new Error(response.status === 401 ? '本地凭据失效，请通过 Blackbox Plugin 入口重新打开。'
    : response.status === 503 ? '本地数据库暂时不可用，请稍后重试。'
    : '无法完成请求，请检查日期范围或刷新记录。');
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
  if (append && (loading || nextBefore === null || nextBefore === undefined)) return;
  const generation = ++listGeneration;
  const query = append ? activeFilters : filters();
  const cursor = append ? '&'+new URLSearchParams({before:nextBefore,through:snapshotSeq}) : '';
  loading=true; el('more').disabled=true;
  try {
    const data = await (await request('/api/anomalies?' + query + cursor)).json();
    if (generation !== listGeneration) return;
    activeFilters = query;
    if (!append) {el('records').replaceChildren(); snapshotSeq=data.snapshot_seq; scannedCount=0;}
    scannedCount += data.scanned_count || 0;
    for (const row of data.items) {
      const button = document.createElement('button'); button.className='record'; button.dataset.recordId=String(row.id);
      button.setAttribute('aria-current',String(String(current?.id) === String(row.id)));
      const title=document.createElement('strong'); title.textContent=row.title;
      const meta=document.createElement('small'); meta.textContent=`${dateText(row.captured)} · ${row.tool} · ${row.project}`;
      const through=snapshotSeq;
      button.append(title,meta); button.addEventListener('click',()=>openItem(row.id,through).catch(e=>say(e.message))); el('records').append(button);
    }
    nextBefore=data.next_before ?? null; el('more').hidden=nextBefore===null;
    el('more').textContent=data.items.length ? '加载更多 / 继续查找' : '继续查找较早事件';
    const count=el('records').children.length;
    el('list-status').textContent=(count ? `已展示 ${count} 条异常。` : '当前扫描范围未发现符合规则及筛选条件的异常；不代表没有问题。')
      + (nextBefore!==null ? '仍有较早事件尚未扫描，请继续查找。' : '已扫描至当前筛选范围末尾。')
      + (scannedCount ? ` 已扫描 ${scannedCount} 个事件。` : '')
      + ` 列表快照截至 #${snapshotSeq ?? '未知'}；刷新可查看最新材料。`
      + (data.has_newer ? ' 有新事件，刷新后才会纳入。' : '');
  } catch(error) {
    if(generation===listGeneration) throw error;
  } finally {
    if (generation===listGeneration) {loading=false; el('more').disabled=false;}
  }
}
function block(parent,tag,text) { const n=document.createElement(tag); n.textContent=text; parent.append(n); return n; }
function operation(content) {
  const value=content.tool_input;
  return typeof value?.command === 'string' ? value.command : typeof value?.cmd === 'string' ? value.cmd : JSON.stringify(value ?? '未捕获输入',null,2);
}
function outputText(output) {
  if(typeof output==='string') return output || '未捕获到返回正文；不表示成功。';
  if(output?.truncated) {
    if(typeof output.preview==='string' && typeof output.tail==='string' && Number.isInteger(output.original_length))
      return `已保留输出开头：\n${output.preview}\n\n[中间内容未保存；原长度 ${output.original_length} 字符]\n\n已保留输出末尾：\n${output.tail}`;
    return `已保存的截断预览（旧格式，可能包含 JSON 编码；不代表完整输出）：\n${output.preview || JSON.stringify(output,null,2)}`;
  }
  return output === undefined || output === null ? '未捕获到返回正文；不表示成功。' : JSON.stringify(output,null,2);
}
async function openItem(id, through=snapshotSeq) {
  const generation=++openGeneration;
  let data;
  try {data=await (await request('/api/anomaly/'+encodeURIComponent(id)+(through!==null && through!==undefined ? '?through='+encodeURIComponent(through) : ''))).json();}
  catch(error) {if(generation===openGeneration) throw error; return;}
  if(generation!==openGeneration) return;
  current=data;
  el('empty').hidden=true; el('detail').hidden=false;
  el('record-heading').textContent=data.title;
  el('meta').textContent=`${data.project} · ${data.tool} · 落盘 ${dateText(data.captured)} · 快照截至 #${data.source_seq}`;
  el('session').href='./history#'+new URLSearchParams({token:auth,record:data.events[0].investigation}).toString();
  for (const button of el('records').children) button.setAttribute('aria-current',String(button.dataset.recordId===String(id)));
  const reading=el('reading'), sources=el('events'); reading.replaceChildren(); sources.replaceChildren();
  if(data.capture_gap) block(reading,'p','当前会话存在已知采集缺口，已停止跨缺口自动关联。未保存的事件无法从此记录恢复。').className='notice';
  for (let i=0;i<data.events.length;i++) {
    const event=data.events[i], content=JSON.parse(event.content);
    block(reading,'h3',i ? `同参数后续调用 · 来源 #${event.seq}` : `执行操作 · 来源 #${event.seq}`);
    block(reading,'pre',operation(content));
    block(reading,'h3',i ? '返回结果' : '错误返回');
    const output=content.tool_response;
    block(reading,'pre',outputText(output));
    const coverage=content._coverage?.truncated_fields || [];
    if(coverage.length) block(reading,'p','来源字段已截断：'+coverage.join('、'));
    if(event.content.includes('\ufffd')) block(reading,'p','来源含编码替换字符，原始文字可能已丢失。');
    const details=document.createElement('details'); block(details,'summary',`来源 #${event.seq} · ${dateText(event.captured)}`);
    block(details,'pre',`SHA-256 ${event.digest}\n${JSON.stringify(content,null,2)}`); sources.append(details);
  }
  block(reading,'p',data.events.length===1 ? '未捕获到符合规则的同参数后续调用。' : '最多附上 20 次同参数调用；新回合、用户新输入、助手结束、中断、会话结束或下一次匹配失败会停止关联。这些调用不等于修复操作，不同参数的操作不会自动补入。');
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
  const generation=++captureGeneration;
  let data;
  try {data=await (await request('/api/status')).json();}
  catch(error) {if(generation===captureGeneration) throw error; return;}
  if(generation!==captureGeneration) return;
  const projects=data.projects || [];
  el('capture-status').textContent=`${projects.filter(p=>p.enabled).length} 个项目已启用 · ${data.record_count ?? 0} 份历史会话（不是异常数量）`;
  el('capture-projects').replaceChildren();
  for(const p of projects) {
    block(el('capture-projects'),'p',`${p.enabled?'已启用':'已停用'} · ${p.path} · ${p.last_recorded_event?'最近落盘 '+dateText(p.last_recorded_event.captured):'尚无落盘事件'}`);
    if(p.last_capture_error) block(el('capture-projects'),'p',`该项目的历史采集缺口：${p.last_capture_error.code} · ${dateText(p.last_capture_error.time)}。标记保留，不代表当前仍然故障。`).className='notice';
  }
  const runtime={missing:'Python 尚未配置',invalid:'Python 配置无效',target_missing:'配置中的 Python 已不存在',configured:'Python 路径已配置（未做执行检查）'};
  el('runtime-status').textContent=runtime[data.runtime?.state] || 'Python 配置状态未知';
  const app=data.application;
  el('application-status').textContent=app ? `版本 ${app.version || '未知'} · 运行构建 ${app.build || '未知'} · 数据目录 ${app.state_directory || '未知'}` : '当前服务未提供版本和数据目录信息。';
  const storage=data.storage, mb=value=>typeof value==='number' && Number.isFinite(value) ? (value/(1024*1024)).toFixed(1)+' MiB' : '未知';
  el('storage-status').textContent=storage ? `数据库 ${mb(storage.database_bytes)} / ${mb(storage.database_limit_bytes)}。${storage.capacity_reached ? '已达到容量保护上限，新事件可能无法保存；已有记录仍可读取。' : '当前未达到容量保护上限；这不证明所有事件已送达。'}` : '当前服务未提供容量信息。';
  const error=data.last_capture_error;
  el('capture-error').textContent=error ? `最近采集缺口标记：${error.code} · ${dateText(error.time)}。这是历史标记，不代表当前或特定项目仍然故障。` : '没有已保存的全局失败标记；不代表采集无缺口。';
}
async function shutdown() {
  el('shutdown').disabled=true;
  try {await request('/api/shutdown','POST'); say('本地阅读服务已请求停止。再次双击 Blackbox Plugin 入口即可打开；项目采集开关没有改变。');}
  catch(error) {el('shutdown').disabled=false; throw error;}
}
el('history').href='./history#'+new URLSearchParams({token:auth}).toString();
el('refresh').addEventListener('click',()=>{load().catch(e=>say(e.message)); capture().catch(e=>{el('capture-status').textContent=e.message;});});
el('clear').addEventListener('click',()=>{for(const id of ['project','tool','start','end']) el(id).value='';load().catch(e=>say(e.message));});
el('more').addEventListener('click',()=>load(true).catch(e=>say(e.message)));
el('export').addEventListener('click',()=>download().catch(e=>say(e.message)));
el('shutdown').addEventListener('click',()=>shutdown().catch(e=>say(e.message)));
if(location.protocol==='file:') {
  el('file-preview').hidden=false; el('capture-status').textContent='文件预览模式，未连接记录服务。';
  for(const id of ['refresh','clear','more','export','project','tool','start','end','shutdown']) el(id).disabled=true;
} else {
  load().catch(e=>say(e.message)); capture().catch(e=>{el('capture-status').textContent=e.message;});
}
