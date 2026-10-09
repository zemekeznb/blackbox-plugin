'use strict';
const el = id => document.getElementById(id);
const auth = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let current = null, openGeneration = 0, listGeneration = 0, captureGeneration = 0;
let nextBefore = null, snapshotSeq = null, scannedCount = 0, activeFilters = '', loading = false;
let view = 'list', listScroll = 0, listFocus = null, listHelpOpen = false, detailRoute = null;
let contextGeneration = 0, selectionGeneration = 0;
const projects = new Map(), sessionGroups = new Map(), sessionNames = new Map(), sessionViews = new Map();
let selectedSession = null, preferenceQueue = Promise.resolve(), mobileReturnScroll = 0;
let pendingListReturn = null, listScrollPending = false;
const baseHash = '#'+new URLSearchParams({token:auth}).toString();
function say(message) { el('status').textContent = message; }
function dateText(value) { return value ? new Date(value).toLocaleString() : '时间未知'; }
function projectText(path) { return path.split(/[\\/]/).filter(Boolean).pop() || path; }
function sessionText(session) { return sessionNames.get(session?.id) || '未命名会话'; }
function updateHeadings() {
  if(selectedSession) {
    el('project-heading').textContent=projectText(selectedSession.project);
    el('project-heading').title=selectedSession.project;
    el('list-heading').textContent=sessionText(selectedSession);
    el('session-description').textContent='异常记录 · 首次记录 '+dateText(selectedSession.first_captured);
  }
  if(current) el('meta').textContent=`${projectText(current.project)} · ${sessionText({id:current.investigation})} · ${current.tool} · 记录于 ${dateText(current.captured)}`;
}
function rememberSession(id) {
  // Serialize writes so an older request can never overwrite a newer choice.
  preferenceQueue=preferenceQueue.catch(()=>{}).then(()=>request('/api/reader-selection','POST',{investigation:id})).catch(()=>{});
}
function saveSessionView() {
  if(!selectedSession || snapshotSeq===null || snapshotSeq===undefined) return;
  sessionViews.delete(selectedSession.id);
  sessionViews.set(selectedSession.id,{
    cards:Array.from(el('records').children),nextBefore,snapshotSeq,scannedCount,activeFilters,
    scroll:el('workspace').dataset.stage==='navigation'?mobileReturnScroll:view==='list'&&!listScrollPending?window.scrollY:listScroll,focus:listFocus,
    controls:Object.fromEntries(['time','tool','start','end'].map(id=>[id,el(id).value])),
    status:el('list-status').textContent,snapshot:el('snapshot-info').textContent,more:el('more').textContent
  });
  if(sessionViews.size>10) sessionViews.delete(sessionViews.keys().next().value);
}
function resetFilters() {
  for(const id of ['tool','start','end']) el(id).value='';
  el('time').value='all'; el('date-range').hidden=true;
}
function selectSession(project,session,{preserveView=false,remember=true}={}) {
  // Let an already requested Back finish before replacing its current history entry.
  if(pendingListReturn) return pendingListReturn.promise.then(()=>selectSession(project,session,{preserveView,remember}));
  const same=selectedSession?.id===session.id;
  const fromNavigation=el('workspace').dataset.stage==='navigation';
  if(!same) saveSessionView();
  selectedSession={...session,project}; el('project').value=project; el('session').value=session.id;
  el('filters').hidden=false;
  const selectedGroup=sessionGroups.get(project);if(selectedGroup)selectedGroup.details.open=true;
  ++selectionGeneration;
  if(remember) rememberSession(session.id);
  for(const group of sessionGroups.values()) for(const [id,nodes] of group.buttons) nodes.button.setAttribute('aria-current',id===session.id?'page':'false');
  updateHeadings(); el('nav-return').hidden=false; el('workspace').dataset.stage='reading';
  if(same && !preserveView) {
    if(view==='detail') backToList();
    else if(fromNavigation) requestAnimationFrame(()=>window.scrollTo(0,mobileReturnScroll));
    return Promise.resolve();
  }
  ++listGeneration; loading=false; el('list-view').setAttribute('aria-busy','false'); el('more').disabled=false;
  const saved=sessionViews.get(session.id);
  listScroll=saved?.scroll || 0; listFocus=saved?.focus || null;
  if(!preserveView) {showList();history.replaceState({view:'list'},'',baseHash);}
  resetFilters();
  if(saved) {
    for(const [id,value] of Object.entries(saved.controls)) el(id).value=value;
    el('date-range').hidden=el('time').value!=='custom';
    el('records').replaceChildren(...saved.cards);
    ({nextBefore,snapshotSeq,scannedCount,activeFilters}=saved);
    el('list-status').textContent=saved.status; el('snapshot-info').textContent=saved.snapshot;
    el('more').textContent=saved.more; el('more').hidden=nextBefore==null;
    el('list-empty').hidden=saved.cards.length>0;
    return Promise.resolve();
  }
  nextBefore=null; snapshotSeq=null; scannedCount=0; activeFilters='';
  el('records').replaceChildren();el('more').hidden=true;el('list-empty').hidden=true;el('snapshot-info').textContent='';
  el('list-status').textContent='正在读取这个会话的记录…';
  return load();
}
async function loadNames(group,generation) {
  let unavailable=false;
  for(let index=0;index<group.sessions.length;index+=20) {
    if(group.generation!==generation) return;
    const batch=group.sessions.slice(index,index+20),query=new URLSearchParams();
    for(const session of batch) query.append('investigation',session.id);
    try {
      const data=await (await request('/api/session-names?'+query)).json();
      if(group.generation!==generation) return;
      for(const session of batch) {
        const name=data.names?.[session.id];
        if(typeof name==='string' && name.trim()) sessionNames.set(session.id,name);
        else unavailable=true;
        const nodes=group.buttons.get(session.id);
        if(nodes) {nodes.label.textContent=sessionText(session);nodes.button.title=sessionText(session);}
      }
      updateHeadings();
    } catch(_) {unavailable=true;break;}
  }
  if(group.generation===generation) group.notice.textContent=unavailable?'部分会话名称暂不可用，记录仍可查看。':'';
}
async function populateProject(path,force=false) {
  const group=sessionGroups.get(path);
  if(!group) return [];
  if(group.pending && !force) return group.pending;
  if(group.loaded && !force) return group.sessions;
  const generation=++group.generation;
  group.notice.textContent='正在读取会话…';
  const pending=(async()=>{
    try {
      const data=await (await request('/api/anomaly-contexts?'+new URLSearchParams({project_path:path}))).json();
      if(group.generation!==generation || sessionGroups.get(path)!==group) return [];
      group.sessions=data.sessions;group.loaded=true;group.body.replaceChildren();group.buttons.clear();
      for(const session of group.sessions) {
        const button=document.createElement('button');button.className='session-link';button.dataset.sessionId=session.id;
        button.setAttribute('aria-current',selectedSession?.id===session.id?'page':'false');
        const label=block(button,'span',sessionText(session));label.className='session-name';
        block(button,'span',dateText(session.first_captured)).className='session-date';button.title=sessionText(session);
        button.addEventListener('click',()=>selectSession(path,session).catch(e=>say(e.message)));
        group.body.append(button);group.buttons.set(session.id,{button,label});
      }
      group.notice.textContent=group.sessions.length?'':'这个项目尚无已保存的会话。';
      loadNames(group,generation);
      return group.sessions;
    } catch(error) {
      if(group.generation===generation) group.notice.textContent='会话未能加载，请刷新项目重试。';
      throw error;
    } finally {if(group.generation===generation) group.pending=null;}
  })();
  group.pending=pending;return pending;
}
async function contexts(preferred=null) {
  const generation=++contextGeneration, selectionAtStart=selectionGeneration;
  el('context-status').textContent='';
  try {
    const data=await (await request('/api/anomaly-contexts')).json();
    if(generation!==contextGeneration) return;
    const expanded=new Set([...sessionGroups].filter(([,g])=>g.details.open).map(([path])=>path));
    for(const group of sessionGroups.values()) ++group.generation;
    projects.clear(); sessionGroups.clear();el('project-tree').replaceChildren();
    const preferredProject=selectedSession?.project || preferred?.project_path;
    const preferredId=selectedSession?.id || preferred?.investigation;
    const target=data.projects.find(p=>p.path===preferredProject)?.path || data.projects[0]?.path;
    for(const project of data.projects) {
      projects.set(project.path,project);
      const details=document.createElement('details');details.className='project-group';
      const summary=block(details,'summary',projectText(project.path));summary.title=project.path;
      const body=document.createElement('div');body.className='project-sessions';details.append(body);
      const notice=block(details,'p','');notice.className='muted';
      const group={details,body,notice,buttons:new Map(),sessions:[],loaded:false,generation:0,pending:null};
      sessionGroups.set(project.path,group);
      details.addEventListener('toggle',()=>{if(details.open)populateProject(project.path).catch(()=>{});});
      details.open=expanded.has(project.path)||project.path===target;el('project-tree').append(details);
    }
    if(!target) {
      el('context-status').textContent='尚无已保存的项目记录。';
      if(!selectedSession) {el('list-status').textContent='';el('filters').hidden=true;}
      return;
    }
    const sessions=await populateProject(target);
    if(generation!==contextGeneration || selectionAtStart!==selectionGeneration) return;
    const session=sessions.find(s=>s.id===preferredId)||sessions[0];
    if(session && selectedSession?.id!==session.id) await selectSession(target,session,{preserveView:view==='detail'});
    for(const [path,group] of sessionGroups) if(group.details.open && path!==target)populateProject(path).catch(()=>{});
  } catch(error) {if(generation===contextGeneration)el('context-status').textContent='项目未能加载，请刷新项目重试。'+error.message;}
}
function showNavigation() {
  mobileReturnScroll=window.scrollY;el('workspace').dataset.stage='navigation';
  requestAnimationFrame(()=>{el('nav-refresh').focus({preventScroll:true});window.scrollTo(0,0);});
}
async function start() {
  const direct=new URLSearchParams(location.hash.slice(1)).has('record');
  if(direct) await route().catch(()=>{});
  else history.replaceState({view:'list'},'',baseHash);
  let preferred=current?{project_path:current.project,investigation:current.investigation}:null;
  if(!preferred)try{preferred=await (await request('/api/reader-selection')).json();}catch(_){}
  await contexts(preferred);
}
function showList() {
  ++openGeneration;
  view='list'; current=null;
  el('detail-view').hidden=true; el('list-view').hidden=false; el('export').disabled=true;
  el('help').open=listHelpOpen;el('workspace').dataset.stage='reading';
  document.title='Blackbox Plugin · 执行异常'; say(''); listScrollPending=true;
  requestAnimationFrame(()=>{
    if(view!=='list') return;
    const card=Array.from(el('records').children).find(n=>n.dataset.recordId===listFocus);
    (card || el('list-heading')).focus({preventScroll:true});
    window.scrollTo(0,listScroll);
    listScrollPending=false;
  });
}
function backToList() {
  if(pendingListReturn) return;
  // Hide immediately; a late response must not reopen the detail while popstate is queued.
  showList();
  if(history.state?.returnToList) {
    let resolve;
    const promise=new Promise(done=>{resolve=done;});
    pendingListReturn={promise,resolve};
    history.back();
  }
  else history.replaceState({view:'list'},'',baseHash);
}
function route() {
  const params=new URLSearchParams(location.hash.slice(1));
  const id=params.get('record');
  if(id) return openItem(id,params.get('through'),false);
  showList();
  return Promise.resolve();
}
async function request(path, method='GET', body=undefined) {
  let response;
  try { response = await fetch(path, {method,headers:{Authorization:`Bearer ${auth}`,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(15000)}); }
  catch (_) { throw new Error('本地服务连接中断，请双击桌面或开始菜单的 Blackbox Plugin 重新打开。'); }
  if (!response.ok) throw new Error(response.status === 401 ? '本地凭据失效，请通过 Blackbox Plugin 入口重新打开。'
    : response.status === 503 ? '本地数据库暂时不可用，请稍后重试。'
    : '无法完成请求，请检查日期范围或刷新记录。');
  return response;
}
function filters() {
  const query = new URLSearchParams({project_path:el('project').value,investigation:el('project').value?el('session').value:'',tool:el('tool').value});
  const preset=el('time').value;
  if(preset==='today' || preset==='week') {
    const start=new Date(); start.setHours(0,0,0,0);
    const end=new Date(start); end.setDate(end.getDate()+1);
    if(preset==='week') start.setDate(start.getDate()-6);
    query.set('start',start.toISOString()); query.set('end',end.toISOString());
  } else if(preset==='custom') {
    for (const id of ['start','end']) {
      if (!el(id).value) continue;
      const date = new Date(el(id).value + 'T00:00:00');
      if (id === 'end') date.setDate(date.getDate()+1);
      query.set(id,date.toISOString());
    }
    if(query.has('start') && query.has('end') && query.get('start')>=query.get('end')) throw new Error('请选择有效的记录时间范围。');
  }
  return query.toString();
}
async function load(append=false) {
  if (append && (loading || nextBefore === null || nextBefore === undefined)) return;
  if(!el('project').value || !el('session').value) return;
  el('filters').hidden=false;
  const query = append ? activeFilters : filters();
  const generation = ++listGeneration;
  const cursor = append ? '&'+new URLSearchParams({before:nextBefore,through:snapshotSeq}) : '';
  loading=true; el('more').disabled=true; el('list-view').setAttribute('aria-busy','true');
  say('');
  try {
    const data = await (await request('/api/anomalies?' + query + cursor)).json();
    if (generation !== listGeneration) return;
    activeFilters = query;
    if (!append) {el('records').replaceChildren(); snapshotSeq=data.snapshot_seq; scannedCount=0;}
    scannedCount += data.scanned_count || 0;
    for (const row of data.items) {
      const button = document.createElement('button'); button.className='record'; button.dataset.recordId=String(row.id);
      button.setAttribute('aria-current',String(String(current?.id) === String(row.id)));
      const top=document.createElement('span'); top.className='card-top';
      block(top,'span','错误返回').className='error-label';
      block(top,'span','记录于 '+dateText(row.captured)).className='card-time';
      const title=document.createElement('strong'); title.textContent=row.title.replace(/^工具返回 /,'');
      const bottom=document.createElement('span'); bottom.className='card-bottom';
      block(bottom,'span',row.tool); block(bottom,'span','查看详情 →').className='card-open';
      const through=snapshotSeq;
      button.append(top,title,bottom); button.addEventListener('click',()=>openItem(row.id,through).catch(()=>{})); el('records').append(button);
    }
    nextBefore=data.next_before ?? null; el('more').hidden=nextBefore===null;
    el('more').textContent=data.items.length ? '加载更多' : '继续查找较早记录';
    const count=el('records').children.length;
    el('list-empty').hidden=count>0;
    el('list-status').textContent=(count ? `${count} 条异常记录` : '当前未找到匹配记录，不代表没有问题。')
      + (nextBefore!==null ? ' · 还有较早记录可查找' : '')
      + (data.has_newer ? ' · 有新事件，刷新可查看' : '');
    el('snapshot-info').textContent=`已检查 ${scannedCount} 个事件 · 快照 #${snapshotSeq ?? '未知'} · 刷新获取新记录`;
  } catch(error) {
    if(generation===listGeneration) throw error;
  } finally {
    if (generation===listGeneration) {loading=false; el('more').disabled=false; el('list-view').setAttribute('aria-busy','false');}
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
async function openItem(id, through=snapshotSeq, push=true) {
  if(push && pendingListReturn) return pendingListReturn.promise.then(()=>openItem(id,through,push));
  if(view==='list') {
    if(!listScrollPending) listScroll=window.scrollY;
    listFocus=String(id); listHelpOpen=el('help').open;
    // Keep the loaded list intact, including when paging/refreshing was in flight.
    ++listGeneration; loading=false; el('more').disabled=false; el('list-view').setAttribute('aria-busy','false');
  }
  if(push) {
    const hash=baseHash+'&'+new URLSearchParams({record:id,...(through!==null && through!==undefined ? {through} : {})});
    if(view==='list') {
      history.replaceState({view:'list'},'',baseHash);
      history.pushState({view:'detail',returnToList:true},'',hash);
    } else history.replaceState(history.state,'',hash);
  }
  detailRoute={id,through}; view='detail'; current=null; listScrollPending=false;
  say(''); el('help').open=false;
  el('list-view').hidden=true; el('detail-view').hidden=false;
  el('detail').hidden=true; el('export').disabled=true; el('retry').hidden=true;
  el('detail-status').textContent='正在读取详情…';
  el('source-details').open=false;
  el('back').focus({preventScroll:true}); window.scrollTo(0,0);
  const generation=++openGeneration;
  let data;
  try {data=await (await request('/api/anomaly/'+encodeURIComponent(id)+(through!==null && through!==undefined ? '?through='+encodeURIComponent(through) : ''))).json();}
  catch(error) {
    if(generation!==openGeneration) return;
    el('detail-status').textContent=error.message; el('retry').hidden=false;
    throw error;
  }
  if(generation!==openGeneration) return;
  current=data;
  // Browser Forward may revisit a detail from a different chat after a new selection.
  // Adopt that chat's saved list as the return destination without changing the detail cutoff.
  if(selectedSession && data.investigation && selectedSession.id!==data.investigation) {
    const session=sessionGroups.get(data.project)?.sessions.find(s=>s.id===data.investigation)
      || {id:data.investigation,session:data.session,first_captured:data.captured};
    selectSession(data.project,session,{preserveView:true}).catch(()=>{});
  }
  el('detail').hidden=false; el('export').disabled=false; el('detail-status').textContent='';
  el('record-heading').textContent=data.title.replace(/^工具返回 /,'');
  document.title=el('record-heading').textContent+' · Blackbox';
  updateHeadings();
  el('detail-snapshot').textContent=`记录 #${data.id} · 快照截至 #${data.source_seq} · 规则 ${data.rule_version} · 会话 ${data.session || '未知'}`;
  for (const button of el('records').children) button.setAttribute('aria-current',String(button.dataset.recordId===String(id)));
  const reading=el('reading'), sources=el('events'); reading.replaceChildren(); sources.replaceChildren();
  if(data.capture_gap) {
    const unknown=data.capture_gap_scope==='unknown_project';
    const notice=unknown && data.capture_gap_reason==='restored_store' ? '从备份恢复后，无法确认恢复前后的记录是否连续，因此没有跨恢复时间连接后续操作。这不代表记录一定丢失。' : unknown && data.capture_gap_reason==='diagnostic_unreadable' ? '采集诊断无法读取或内容不一致，暂不连接连续性不明的操作。这不代表当前会话一定丢失了记录。' : unknown ? '有一次记录未能保存，但无法确定所属项目或会话，因此没有跨过这段不确定的时间连接操作。' : '这段会话有记录未能保存，后续操作不会跨过缺失部分连接。缺失内容无法从这里恢复。';
    block(reading,'p',notice).className='notice';
  }
  if(data.legacy_association_boundary) block(reading,'p','旧记录缺少判断操作是否连续的信息，因此未附加后续调用。已有内容仍完整保留，这不代表当时一定丢失了记录。').className='notice';
  for (let i=0;i<data.events.length;i++) {
    const event=data.events[i], content=JSON.parse(event.content);
    block(reading,'h3',i ? `相同输入再次调用 ${i} · ${dateText(event.captured)}` : '执行操作');
    block(reading,'pre',operation(content));
    block(reading,'h3',i ? '返回结果' : '错误返回');
    const output=content.tool_response;
    const renderedOutput=outputText(output);
    block(reading,'pre',renderedOutput);
    const coverage=content._coverage?.truncated_fields || [];
    if(coverage.length) block(reading,'p','部分内容过长，仅保存了片段。具体字段见下方技术详情。').className='notice';
    if(renderedOutput.includes('\ufffd')) block(reading,'p','部分文字可能显示不完整。').className='notice';
    const details=document.createElement('details'); block(details,'summary',`来源 #${event.seq} · ${dateText(event.captured)}`);
    block(details,'pre',`SHA-256 ${event.digest}\n${JSON.stringify(content,null,2)}`); sources.append(details);
  }
  if(data.events.length>1) block(reading,'p','仅连接同一会话、同一回合内相同输入的调用，最多 20 次；遇到记录缺口或下一次匹配错误时停止。这些调用不表示问题已修复。');
  el('record-heading').focus({preventScroll:true});
}
async function download() {
  if(!current) return;
  const snapshot={id:current.id,through:current.source_seq,rule:current.rule_version};
  const response=await request('/api/anomaly-export/'+snapshot.id+'?'+new URLSearchParams({through:snapshot.through,rule:snapshot.rule}));
  const url=URL.createObjectURL(await response.blob());
  const link=document.createElement('a'); link.href=url; link.download=`anomaly-${snapshot.id}-through-${snapshot.through}.md`; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000); say(`已开始下载记录 #${snapshot.id}。`);
}
async function capture() {
  const generation=++captureGeneration;
  let data;
  try {data=await (await request('/api/status')).json();}
  catch(error) {if(generation===captureGeneration) throw error; return;}
  if(generation!==captureGeneration) return;
  const projects=data.projects || [];
  el('capture-status').textContent=`${projects.filter(p=>p.enabled).length} 个项目已启用`;
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
  const unknown=data.last_unscoped_capture_error;
  if(unknown) el('capture-error').textContent+=` 连续性诊断：${unknown.code} · ${dateText(unknown.time)}。${unknown.state==='unreadable' ? '诊断不可读或不一致，采用保守关联边界。' : unknown.code==='RestoreContinuityUnknown' ? '备份恢复后的采集连续性未知，停止跨恢复边界关联，不代表发生过采集错误。' : '不能确定所属项目或会话。'}${unknown.code==='InputTooLarge' ? 'Hook 输入超过 512 KiB 上限，整条未保存；后续关联按保守边界处理。' : ''}`;
}
async function shutdown() {
  el('shutdown').disabled=true;
  try {await request('/api/shutdown','POST'); say('本地阅读服务已请求停止。再次双击 Blackbox Plugin 入口即可打开；项目采集开关没有改变。');}
  catch(error) {el('shutdown').disabled=false; throw error;}
}
el('filters').addEventListener('submit',event=>{event.preventDefault();load().catch(e=>say(e.message));});
el('time').addEventListener('change',()=>{el('date-range').hidden=el('time').value!=='custom';if(el('time').value!=='custom')load().catch(e=>say(e.message));});
el('refresh').addEventListener('click',()=>{load().catch(e=>say(e.message));contexts();capture().catch(e=>{el('capture-status').textContent=e.message;});});
el('nav-refresh').addEventListener('click',()=>contexts());
el('browse-sessions').addEventListener('click',showNavigation);
el('nav-return').addEventListener('click',()=>{el('workspace').dataset.stage='reading';requestAnimationFrame(()=>window.scrollTo(0,mobileReturnScroll));});
el('clear').addEventListener('click',()=>{resetFilters();load().catch(e=>say(e.message));});
el('more').addEventListener('click',()=>load(true).catch(e=>say(e.message)));
el('export').addEventListener('click',()=>download().catch(e=>say(e.message)));
el('shutdown').addEventListener('click',()=>shutdown().catch(e=>say(e.message)));
el('back').addEventListener('click',backToList);
el('retry').addEventListener('click',()=>{if(detailRoute) openItem(detailRoute.id,detailRoute.through,false).catch(()=>{});});
history.scrollRestoration='manual';
window.addEventListener('popstate',()=>{
  const returning=pendingListReturn; pendingListReturn=null;
  route().catch(()=>{}).then(()=>{if(returning)returning.resolve();});
});
if(location.protocol==='file:') {
  el('file-preview').hidden=false; el('capture-status').textContent='文件预览模式，未连接记录服务。';
  for(const id of ['apply','refresh','clear','more','export','nav-refresh','browse-sessions','time','tool','start','end','shutdown']) el(id).disabled=true;
} else {
  start().catch(e=>say(e.message));capture().catch(e=>{el('capture-status').textContent=e.message;});
}
